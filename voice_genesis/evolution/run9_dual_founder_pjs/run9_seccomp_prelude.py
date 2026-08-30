#!/usr/bin/env python3
"""RUN9 seccomp BPF network-isolation prelude (x86_64 only).

RunPod CPU コンテナは iptables に `CAP_NET_ADMIN` が無く、`unshare -n` /
`-rn` もコンテナランタイムに拒否される環境がある。本モジュールは 3 番目・
4 番目の代替として、`AF_INET` / `AF_INET6` / `AF_PACKET` ソケットの生成を
カーネルレベルで拒否する seccomp BPF フィルタを自プロセスにインストールする。

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
_FILTER = b"".join(
    [
        _ins(BPF_LD_W_ABS, 0, 0, 4),  # A = arch
        _ins(BPF_JEQ_K, 1, 0, AUDIT_ARCH_X86_64),  # x86_64 -> continue
        _ins(BPF_RET_K, 0, 0, SECCOMP_RET_KILL),  # unexpected arch -> kill
        _ins(BPF_LD_W_ABS, 0, 0, 0),  # A = syscall nr
        _ins(BPF_JEQ_K, 1, 0, SYS_SOCKET),  # socket(2) -> inspect domain
        _ins(BPF_RET_K, 0, 0, SECCOMP_RET_ALLOW),  # everything else -> allow
        _ins(BPF_LD_W_ABS, 0, 0, 16),  # A = args[0] = domain
        _ins(BPF_JEQ_K, 2, 0, 2),  # AF_INET -> errno
        _ins(BPF_JEQ_K, 1, 0, 10),  # AF_INET6 -> errno
        _ins(BPF_JEQ_K, 0, 1, 17),  # AF_PACKET -> errno, else allow
        _ins(BPF_RET_K, 0, 0, SECCOMP_RET_ERRNO | EPERM),
        _ins(BPF_RET_K, 0, 0, SECCOMP_RET_ALLOW),
    ]
)


class _SockFprog(ctypes.Structure):
    _fields_ = (("len", ctypes.c_ushort), ("filter", ctypes.c_void_p))


class SeccompInstallError(RuntimeError):
    """フィルタの装着または自己検査に失敗した。"""


def install_seccomp_filter() -> None:
    """このプロセスに `AF_INET`/`AF_INET6`/`AF_PACKET` ソケット生成拒否フィルタを装着する。"""
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


_SELF_CHECK_FAMILIES: tuple[tuple[int, str], ...] = (
    (socket.AF_INET, "AF_INET"),
    (socket.AF_INET6, "AF_INET6"),
    (socket.AF_PACKET, "AF_PACKET"),
)
# Plain ints with labels: socket.socket() accepts an int `type`, and DCCP /
# SOCK_PACKET have no `socket` module constants to reference.
_SELF_CHECK_KINDS: tuple[tuple[int, str], ...] = (
    (1, "SOCK_STREAM"),
    (2, "SOCK_DGRAM"),
    (3, "SOCK_RAW"),
    (4, "SOCK_RDM"),
    (5, "SOCK_SEQPACKET"),
    (6, "SOCK_DCCP"),
    (10, "SOCK_PACKET"),
)


def self_check_filter_installed() -> None:
    """フィルタが実際に効いていることを検証する（偽陽性防止に AF_UNIX 成功も確認）。

    このフィルタはドメイン（arg0）単位で拒否するため、同一ドメインのソケット
    種別は STREAM/DGRAM/RAW/... を問わずすべて拒否されるはず。一部の種別
    だけを検査すると、それ以外の種別（SOCK_RAW・AF_PACKET 等）を許すホスト
    ポリシー下で偽陽性の自己検査通過を招く。AF_INET/AF_INET6/AF_PACKET と
    socket(2) が受理する 7 種別の全マトリクスを検査する。

    自プロセスに装着した既知セマンティクスのフィルタ（ドメイン単位で
    ハンドラ前段に割り込み、型バリデーション前に EPERM を返す）を検査する
    自己検査であるため、他ファミリ検証（`run9_success_admission.py` の
    errno 非依存な運用側検証）とは異なり EPERM 厳密判定を維持する。
    """
    for family, label in _SELF_CHECK_FAMILIES:
        for kind, kind_label in _SELF_CHECK_KINDS:
            _expect_af_blocked(family, kind, f"{label}/{kind_label}")
    try:
        unix_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except OSError as exc:
        raise SeccompInstallError(f"AF_UNIX socket creation unexpectedly failed: {exc}") from exc
    unix_socket.close()


def _expect_af_blocked(family: int, kind: int, label: str) -> None:
    try:
        blocked_socket = socket.socket(family, kind)
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
