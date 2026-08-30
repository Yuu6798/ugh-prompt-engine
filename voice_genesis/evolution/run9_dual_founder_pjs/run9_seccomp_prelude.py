#!/usr/bin/env python3
"""RUN9 seccomp BPF network-isolation prelude (x86_64 only).

RunPod CPU コンテナは iptables に `CAP_NET_ADMIN` が無く、`unshare -n` /
`-rn` もコンテナランタイムに拒否される環境がある。本モジュールは 3 番目・
4 番目の代替として、socket(2) のドメイン軸を **アローリスト** で閉じる
seccomp BPF フィルタを自プロセスにインストールする。

フィルタは `AF_UNIX`（自プロセス内 IPC・多数のシステムライブラリが暗黙に
依存）と `AF_NETLINK`（カーネル内ローカル通信のみで外部送信経路を持たない。
glibc の `getifaddrs`/libuuid 等が依存し得る）の 2 ドメインだけを許可し、
それ以外の**あらゆる**ドメイン（`AF_INET`/`AF_INET6`/`AF_PACKET` はもちろん、
`AF_SMC`(43, TCP へのフォールバックを持ち実質外部送信可能) や将来追加され得る
未知ドメインも含む）を一律 `EPERM` で拒否する。ブロックリスト（列挙した
既知の危険ドメインだけを拒否する方式）はドメイン軸を終端できない
（列挙し損ねた新規ドメインが素通りする）ため、本フィルタは意図的に
アローリスト方向を採用し、ドメイン軸を構造的に終端する。

`--probe` はこのプロセス自身にフィルタをインストールし自己検査するだけの
プリフライト用モード。`--exec CMD [ARGS...]` はフィルタをインストール・
自己検査した後にのみ `os.execv` で対象コマンドに置き換わる。自己検査に
失敗した経路がフィルタ未装着のまま対象コマンドへフォールスルーすることは
ない（検査失敗は必ず非 0 終了で打ち切る）。
"""
from __future__ import annotations

import ctypes
import os
import socket
import struct
import sys

PR_SET_NO_NEW_PRIVS = 38
PR_SET_SECCOMP = 22
SECCOMP_MODE_FILTER = 2
AUDIT_ARCH_X86_64 = 0xC000003E
SYS_SOCKET = 41
EPERM = 1
AF_UNIX = 1
AF_NETLINK = 16
BPF_LD_W_ABS = 0x20
BPF_JEQ_K = 0x15
BPF_RET_K = 0x06
SECCOMP_RET_ALLOW = 0x7FFF0000
SECCOMP_RET_ERRNO = 0x00050000
SECCOMP_RET_KILL = 0x00000000


def _ins(code: int, jt: int, jf: int, k: int) -> bytes:
    """1 個の BPF ソック命令を `struct sock_filter` バイト列にエンコードする。"""
    return struct.pack("HBBI", code, jt, jf, k)


# struct seccomp_data のオフセット: nr=0, arch=4, args[0]の下位32bit=16
#
# 命令表（0-indexed。アローリスト: AF_UNIX/AF_NETLINK 以外の全ドメインで
# socket(2) を EPERM にする。ジャンプオフセットは「次の命令からの相対」）:
#
#   idx  意味
#   ---  ----------------------------------------------------------------
#    0   A = arch
#    1   A == AUDIT_ARCH_X86_64 ? jt=1(→idx3へスキップ) : jf=0(→idx2へ落下)
#    2   ret KILL                        (想定外アーキテクチャ)
#    3   A = syscall nr
#    4   A == SYS_socket(41) ? jt=1(→idx6へスキップ) : jf=0(→idx5へ落下)
#    5   ret ALLOW                       (socket(2) 以外は全許可)
#    6   A = args[0] (domain)
#    7   A == AF_UNIX(1)    ? jt=2(→idx10へ) : jf=0(→idx8へ落下)
#    8   A == AF_NETLINK(16) ? jt=1(→idx10へ) : jf=0(→idx9へ落下)
#    9   ret ERRNO(EPERM)                (AF_UNIX/AF_NETLINK 以外は全拒否)
#   10   ret ALLOW                       (AF_UNIX または AF_NETLINK)
_FILTER = b"".join(
    [
        _ins(BPF_LD_W_ABS, 0, 0, 4),  # idx0: A = arch
        _ins(BPF_JEQ_K, 1, 0, AUDIT_ARCH_X86_64),  # idx1: x86_64 -> continue
        _ins(BPF_RET_K, 0, 0, SECCOMP_RET_KILL),  # idx2: unexpected arch -> kill
        _ins(BPF_LD_W_ABS, 0, 0, 0),  # idx3: A = syscall nr
        _ins(BPF_JEQ_K, 1, 0, SYS_SOCKET),  # idx4: socket(2) -> inspect domain
        _ins(BPF_RET_K, 0, 0, SECCOMP_RET_ALLOW),  # idx5: everything else -> allow
        _ins(BPF_LD_W_ABS, 0, 0, 16),  # idx6: A = args[0] = domain
        _ins(BPF_JEQ_K, 2, 0, AF_UNIX),  # idx7: AF_UNIX -> allow
        _ins(BPF_JEQ_K, 1, 0, AF_NETLINK),  # idx8: AF_NETLINK -> allow
        _ins(BPF_RET_K, 0, 0, SECCOMP_RET_ERRNO | EPERM),  # idx9: everything else -> errno
        _ins(BPF_RET_K, 0, 0, SECCOMP_RET_ALLOW),  # idx10: AF_UNIX/AF_NETLINK -> allow
    ]
)


class _SockFprog(ctypes.Structure):
    _fields_ = (("len", ctypes.c_ushort), ("filter", ctypes.c_void_p))


class SeccompInstallError(RuntimeError):
    """フィルタの装着または自己検査に失敗した。"""


def install_seccomp_filter() -> None:
    """このプロセスに socket(2) ドメインアローリスト（AF_UNIX/AF_NETLINK のみ許可）フィルタを装着する。"""
    if platform_machine() != "x86_64":
        raise SeccompInstallError(f"seccomp filter is x86_64-only, got {platform_machine()!r}")
    libc = ctypes.CDLL(None, use_errno=True)
    no_new_privs_rc = libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
    if no_new_privs_rc != 0:
        errno_value = ctypes.get_errno()
        raise SeccompInstallError(f"PR_SET_NO_NEW_PRIVS failed: errno={errno_value}")
    # ctypes.create_string_buffer への参照は prctl 呼び出し完了まで生存させる
    # 必要がある（GC で解放されると SockFprog.filter がダングリングポインタ
    # になる）。
    filter_buffer = ctypes.create_string_buffer(_FILTER, len(_FILTER))
    prog = _SockFprog(
        len(_FILTER) // 8,
        ctypes.cast(filter_buffer, ctypes.c_void_p),
    )
    seccomp_rc = libc.prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.byref(prog), 0, 0)
    if seccomp_rc != 0:
        errno_value = ctypes.get_errno()
        raise SeccompInstallError(f"PR_SET_SECCOMP failed: errno={errno_value}")


def platform_machine() -> str:
    return os.uname().machine


# このフィルタはドメイン（arg0）単位でアローリスト判定するため、
# AF_UNIX/AF_NETLINK 以外のドメインは type/protocol を問わず一律 EPERM
# のはず。自己検査は「装着した既知セマンティクスのフィルタ」を検査する
# ものなので EPERM 厳密判定（run9_success_admission.py 側の errno 非依存な
# 運用側検証とは異なる立場）で、かつドメイン軸を実際に広くさらう。
_SELF_CHECK_EXEMPT_DOMAINS = (AF_UNIX, AF_NETLINK)
_SELF_CHECK_DOMAIN_SWEEP_RANGE = range(0, 64)
# Plain ints with labels: socket.socket() accepts an int `type`, and DCCP /
# SOCK_PACKET have no `socket` module constants to reference.
_SELF_CHECK_TYPES: tuple[tuple[int, str], ...] = (
    (1, "SOCK_STREAM"),
    (2, "SOCK_DGRAM"),
    (3, "SOCK_RAW"),
    (4, "SOCK_RDM"),
    (5, "SOCK_SEQPACKET"),
    (6, "SOCK_DCCP"),
    (10, "SOCK_PACKET"),
)
_SELF_CHECK_INET_FAMILIES: tuple[tuple[int, str], ...] = (
    (socket.AF_INET, "AF_INET"),
    (socket.AF_INET6, "AF_INET6"),
)
_SELF_CHECK_INET_PROTOCOL_SAMPLE: tuple[tuple[int, str], ...] = (
    (0, "proto=0"),
    (1, "IPPROTO_ICMP"),
    (6, "IPPROTO_TCP"),
    (17, "IPPROTO_UDP"),
    (58, "IPPROTO_ICMPV6"),
    (255, "IPPROTO_RAW"),
)
_SELF_CHECK_PACKET_TYPES: tuple[tuple[int, str], ...] = (
    (3, "SOCK_RAW"),
    (2, "SOCK_DGRAM"),
)
_SELF_CHECK_PACKET_ETHERTYPE_SAMPLE: tuple[tuple[int, str], ...] = (
    (0x0000, "ethertype=0"),
    (0x0003, "ETH_P_ALL"),
    (0x0800, "ETH_P_IP"),
    (0x86DD, "ETH_P_IPV6"),
)


def self_check_filter_installed() -> None:
    """フィルタが実際に効いていることを検証する（偽陽性防止に AF_UNIX 成功も確認）。

    アローリストなので、ドメイン軸そのものを広く検査できる（列挙漏れの
    ドメインが素通りしていないことを直接確認する）:

      * ドメイン 0..63（AF_UNIX/AF_NETLINK を除く）x SOCK_STREAM, proto=0
        -- 未列挙の危険ドメイン（AF_SMC・AF_XDP 等）が抜けていないことを
        ドメイン軸全体で確認する。
      * AF_INET/AF_INET6 x 7 種別 x プロトコルサンプル
        {0, ICMP, TCP, UDP, ICMPv6, RAW} -- 明示プロトコル指定の raw
        ソケット経路も EPERM になることを確認する。
      * AF_PACKET x {SOCK_RAW, SOCK_DGRAM} x ethertype サンプル
        {0, ETH_P_ALL, ETH_P_IP, ETH_P_IPV6}。
      * AF_UNIX/SOCK_STREAM は成功しなければならない（本チェックが
        `socket.socket` 自体の不調で見かけ上通過していないことの保証）。

    自プロセスに装着した既知セマンティクスのフィルタ（ドメイン単位で
    ハンドラ前段に割り込み、型・プロトコルのバリデーション前に EPERM を
    返す）を検査する自己検査であるため、他ファミリ検証
    （`run9_success_admission.py` の errno 非依存な運用側検証）とは異なり
    EPERM 厳密判定を維持する。
    """
    for domain in _SELF_CHECK_DOMAIN_SWEEP_RANGE:
        if domain in _SELF_CHECK_EXEMPT_DOMAINS:
            continue
        _expect_af_blocked(domain, 1, 0, f"domain={domain}/SOCK_STREAM/proto=0")
    for family, family_label in _SELF_CHECK_INET_FAMILIES:
        for kind, kind_label in _SELF_CHECK_TYPES:
            for proto, proto_label in _SELF_CHECK_INET_PROTOCOL_SAMPLE:
                _expect_af_blocked(
                    family, kind, proto, f"{family_label}/{kind_label}/{proto_label}"
                )
    for kind, kind_label in _SELF_CHECK_PACKET_TYPES:
        for proto, proto_label in _SELF_CHECK_PACKET_ETHERTYPE_SAMPLE:
            _expect_af_blocked(
                socket.AF_PACKET, kind, proto, f"AF_PACKET/{kind_label}/{proto_label}"
            )
    try:
        unix_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except OSError as exc:
        raise SeccompInstallError(f"AF_UNIX socket creation unexpectedly failed: {exc}") from exc
    unix_socket.close()


def _expect_af_blocked(family: int, kind: int, proto: int, label: str) -> None:
    try:
        blocked_socket = socket.socket(family, kind, proto)
    except OSError as exc:
        if exc.errno != EPERM:
            raise SeccompInstallError(
                f"{label} socket creation failed with unexpected errno={exc.errno}"
            ) from exc
        return
    blocked_socket.close()
    raise SeccompInstallError(f"{label} socket creation unexpectedly succeeded")


def _install_and_self_check() -> None:
    install_seccomp_filter()
    self_check_filter_installed()


def _run_probe() -> int:
    try:
        _install_and_self_check()
    except SeccompInstallError as exc:
        print(f"run9-seccomp-prelude: probe failed: {exc}", file=sys.stderr)
        return 1
    return 0


def _run_exec(command: str, args: list[str]) -> int:
    if not os.path.isabs(command):
        print(f"run9-seccomp-prelude: --exec command must be absolute: {command}", file=sys.stderr)
        return 1
    try:
        _install_and_self_check()
    except SeccompInstallError as exc:
        print(f"run9-seccomp-prelude: exec preflight failed: {exc}", file=sys.stderr)
        return 1
    # 自己検査に通ったこの一点でのみ execv する。ここより前で失敗した経路は
    # 必ず return で打ち切られ、フィルタ未装着のまま対象コマンドへ
    # フォールスルーすることはない。
    os.execv(command, [command, *args])
    # os.execv は成功時に戻らない。ここに到達するのは execv 自体が失敗した
    # ときだけ。
    print("run9-seccomp-prelude: os.execv did not replace the process", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--probe"]:
        return _run_probe()
    if len(argv) >= 2 and argv[0] == "--exec":
        return _run_exec(argv[1], argv[2:])
    print(
        "usage: run9_seccomp_prelude.py --probe | --exec CMD [ARGS...]",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
