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
オプションは存在しない（引数は `--wrapper`/`--payload`/`--out` の 3 つのみ
+ argparse 既定の `-h`/`--help`）。

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
エントリの静黙置換を防ぐ）。stdout には publish 先パスとバイト数、出力
バイト列の sha256 を 1 行で印字する。
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


def compose_author_prompt(wrapper_path: Path, payload_path: Path) -> bytes:
    """`wrapper_path` の全バイト + 改行 1 バイト + `payload_path` の全バイトを
    返す（モジュール docstring の連結規則が正本。他の変換は一切行わない）。"""

    wrapper_bytes = Path(wrapper_path).read_bytes()
    payload_bytes = Path(payload_path).read_bytes()
    return wrapper_bytes + b"\n" + payload_bytes


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
    """引数は `--wrapper`/`--payload`/`--out` の 3 つのみ（+ argparse 既定の
    `-h`/`--help`）。自由記述の注入口（`--note`/`--comment`/`--extra` 類）を
    一切定義しない——`compose_payload.py` と同じ、この計器の中心的な安全
    特性。`tests/test_l0b_scripts.py` がこの parser を検査してオプション
    集合を直接 assert する。"""

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
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    try:
        out_bytes = compose_author_prompt(args.wrapper, args.payload)
        _publish(out_bytes, args.out)
    except (AuthorPromptComposeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    sha256 = hashlib.sha256(out_bytes).hexdigest()
    print(f"wrote {args.out} ({len(out_bytes)} bytes) sha256={sha256}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
