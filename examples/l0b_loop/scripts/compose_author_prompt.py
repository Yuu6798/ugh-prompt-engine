"""L0b 著者 spawn 配送プロンプトの機械組成 (`AGENTS.md` §8「情報遮断被験者への
spawn 配送は逐語性を機械照合する」2026-08-07 制定の前段——配送前の組成そのもの
を機械化する)。

`compose_author_prompt.py --wrapper <author_wrapper.md> --payload <payload.md>
--out <author_prompt.txt>` は、pin 済みラッパファイル（例:
`battery/author_wrapper.md`）の全文と、`compose_payload.py` が組成した
`payload.md` の全文とを、この順で機械連結したものだけを出力する。

連結規則: `wrapper` のバイト列 + 改行 1 バイト（`\\n`）+ `payload` のバイト列。
ラッパ本文の末尾行（例: `battery/author_wrapper.md` の `PAYLOAD:` 行）の
直後に空行 1 つを挟んでペイロード全文を続ける、既存の実 author_prompt 組成
実績（#249/#250 実測。`docs/l0a_v2_remeasure_record.md` §5）と同一の形式で、
それ以外のバイトは一切追加しない——`compose_payload.py` と同じ「自由記述の
注入口を一切定義しない」原則を踏襲し、`--note`/`--comment` 等の自由文
オプションは存在しない（引数は `--wrapper`/`--payload`/`--out`/
`--expect-wrapper-sha256`/`--expect-payload-sha256` の 5 つのみ + argparse
既定の `-h`/`--help`。後者 2 つは自由文ではなく宣言的な入力 pin 検証）。

出典: #249/#250 実測でコーディネーターが著者呼び出しプロンプトを手動転写した
際に 1 文字破損が 2 件発生した（`docs/l0a_v2_remeasure_record.md` §5:
「が」→「April」・「同」→「異」）。組成そのものを機械化すれば、転写経路に
残る唯一の手作業（人間またはモデルによるコピー）を配送直前の 1 回に限定でき、
`verify_prompt_delivery.py` による配送直後の逐語照合と組み合わせて事故の
発生源を配送経路のみへ絞り込める。

出力は `svp_rpe.utils.atomic_io.atomic_write_bytes` で `--out` へ publish
する。`--out` の既存ファイルは上書き禁止（`compose_payload.py`/
`check_token_ban.py` と同じ方針。既存判定は `os.path.lexists` を使い、
リンク先が存在しない dangling symlink も「既存」として拒否する——symlink
エントリの静黙置換を防ぐ）。stdout には `wrapper`/`payload` それぞれの
入力バイト列の sha256 を常時 1 行ずつ印字したうえで、publish 先パスと
バイト数・出力バイト列の sha256 を 1 行で印字する（機械可読な計 3 行）。

任意で `--expect-wrapper-sha256 <hex>` / `--expect-payload-sha256 <hex>`
を指定できる（宣言的検証のみ・自由文オプションではない）。指定時は
読み込んだ `--wrapper`/`--payload` の実 sha256 と照合し、不一致なら
`--out` へ一切 publish せず、stderr に expected/actual 両 hex を併記した
エラーを出して exit 1 で終える。台帳（`ledger_l0br_v2.yaml` 等）が
wrapper/payload の来歴をそれぞれ別々の sha256 として pin しているため、
組成時にこれらの pin を `--expect-*-sha256` として宣言すれば、stale な
ファイルや取り違えたファイルからの組成を publish 前に機械的に遮断できる。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path
from typing import Optional

from svp_rpe.utils.atomic_io import atomic_write_bytes


class AuthorPromptComposeError(RuntimeError):
    """運用エラー（`--out` 上書き禁止・入出力エラー）。exit 1 に対応する。"""


def _concat_bytes(wrapper_bytes: bytes, payload_bytes: bytes) -> bytes:
    """モジュール docstring の連結規則そのもの（wrapper バイト列 + 改行 1
    バイト + payload バイト列）。`compose_author_prompt` と `main` の両方が
    このヘルパーを唯一の連結ロジックとして共有する（規則の二重実装を避ける）。"""

    return wrapper_bytes + b"\n" + payload_bytes


def compose_author_prompt(wrapper_path: Path, payload_path: Path) -> bytes:
    """`wrapper_path` の全バイト + 改行 1 バイト + `payload_path` の全バイトを
    返す（モジュール docstring の連結規則が正本。他の変換は一切行わない）。"""

    wrapper_bytes = Path(wrapper_path).read_bytes()
    payload_bytes = Path(payload_path).read_bytes()
    return _concat_bytes(wrapper_bytes, payload_bytes)


def _publish(out_bytes: bytes, out_path: Path) -> None:
    out_path = Path(out_path)
    # `os.path.lexists` は symlink 自体の存在を判定し、リンク先へは辿らない
    # ——dangling symlink も「既存」として拒否する（compose_payload.py と
    # 同じ理由: `Path.exists()` だと dangling symlink を見逃し、
    # `atomic_write_bytes` 内部の `os.replace` がリンクエントリ自体を静黙に
    # 実ファイルへ置換してしまう）。
    if os.path.lexists(out_path):
        raise AuthorPromptComposeError(f"refusing to overwrite existing output file: {out_path}")
    atomic_write_bytes(out_path, out_bytes)


def _build_arg_parser() -> argparse.ArgumentParser:
    """引数は `--wrapper`/`--payload`/`--out`/`--expect-wrapper-sha256`/
    `--expect-payload-sha256` の 5 つのみ（+ argparse 既定の `-h`/`--help`）。
    後者 2 つは宣言的な入力 pin 検証であり自由文の注入口ではない
    （`--note`/`--comment`/`--extra` 類は依然として一切定義しない——
    `compose_payload.py` と同じ、この計器の中心的な安全特性）。
    `tests/test_l0b_scripts.py` がこの parser を検査してオプション集合を
    直接 assert する。"""

    parser = argparse.ArgumentParser(description="L0b author-prompt composer (spawn delivery text)")
    parser.add_argument(
        "--wrapper", type=Path, required=True, help="Path to the pinned author wrapper file"
    )
    parser.add_argument(
        "--payload", type=Path, required=True, help="Path to the composed payload.md"
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="Path to write the composed author prompt to"
    )
    parser.add_argument(
        "--expect-wrapper-sha256",
        dest="expect_wrapper_sha256",
        type=str,
        default=None,
        help="Optional pinned sha256 of --wrapper; mismatch aborts before publish (exit 1)",
    )
    parser.add_argument(
        "--expect-payload-sha256",
        dest="expect_payload_sha256",
        type=str,
        default=None,
        help="Optional pinned sha256 of --payload; mismatch aborts before publish (exit 1)",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    try:
        wrapper_bytes = Path(args.wrapper).read_bytes()
        payload_bytes = Path(args.payload).read_bytes()
    except OSError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    wrapper_sha256 = hashlib.sha256(wrapper_bytes).hexdigest()
    payload_sha256 = hashlib.sha256(payload_bytes).hexdigest()

    if args.expect_wrapper_sha256 is not None and args.expect_wrapper_sha256 != wrapper_sha256:
        print(
            "Error: --expect-wrapper-sha256 mismatch: "
            f"expected={args.expect_wrapper_sha256} actual={wrapper_sha256}",
            file=sys.stderr,
        )
        return 1
    if args.expect_payload_sha256 is not None and args.expect_payload_sha256 != payload_sha256:
        print(
            "Error: --expect-payload-sha256 mismatch: "
            f"expected={args.expect_payload_sha256} actual={payload_sha256}",
            file=sys.stderr,
        )
        return 1

    out_bytes = _concat_bytes(wrapper_bytes, payload_bytes)
    try:
        _publish(out_bytes, args.out)
    except (AuthorPromptComposeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    out_sha256 = hashlib.sha256(out_bytes).hexdigest()
    print(f"wrapper sha256={wrapper_sha256}")
    print(f"payload sha256={payload_sha256}")
    print(f"wrote {args.out} ({len(out_bytes)} bytes) sha256={out_sha256}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
