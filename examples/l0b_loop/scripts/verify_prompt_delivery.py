"""L0b 著者 spawn 配送の逐語性機械照合 (`AGENTS.md` §8「情報遮断被験者への
spawn 配送は逐語性を機械照合する」2026-08-07 制定の実装本体)。

`verify_prompt_delivery.py --expected <author_prompt.txt> --delivered <file>`
は、`compose_author_prompt.py` が組成した `--expected` の全文と、実際に配送
された本文（`--delivered`）とを strip 後（先頭/末尾の空白除去後）バイト同一で
比較する。一致すれば exit 0、不一致なら exit 1 + 最初の差分位置と前後 60 文字
の文脈を stderr に印字する。

`--delivered-jsonl <task.jsonl>` を指定した場合は、`--delivered` の代わりに
JSONL の配送記録から最初の `role == "user"` メッセージ本文を抽出して比較する
（`--delivered`/`--delivered-jsonl` は排他必須の 1 つ）。抽出手順:

1. 各行を `json.loads` する（1 行が不正 JSON なら抽出不能）。
2. `message.role == "user"` の最初のエントリを探す（`message` キーが
   トップレベルに無い行形式——エントリ自体が `{"role": ..., "content": ...}`
   ——も防御的に扱う）。
3. 見つかった最初の user エントリの `content` が `str` ならそのまま採用、
   `list` なら**全ブロックが well-formed な text ブロック**（`dict` かつ
   `type == "text"` かつ `text` が `str`）であることを要求して連結する。
   1 つでもそれ以外のブロック（画像等の非 text ブロック、不正な形の要素）
   が混じっていれば抽出不能として扱う——期待テキスト+画像が配送された
   ケースを黙って exit 0 で認証しない fail-closed。

見つからない・`content` の形が上記のいずれでもない・非 text ブロックが
混入している・JSON として parse できない、のいずれも「抽出不能」として
exit 2（未知構造を黙って exit 0 にしない fail-closed。**判定不能を照合成立
の側へ倒さない**——`check_token_ban.py` の非 UTF-8 拒否と同じ思想）。
ファイル入出力エラー（`--expected`/`--delivered` が読めない等）も同様に
exit 2 として扱う——0/1 は「比較を実行できた」上での一致/不一致にのみ使う。

出典: #249/#250 実測（`docs/l0a_v2_remeasure_record.md` §5）— spawn 直後に
配送記録（task JSONL）と `author_prompt.txt` を機械照合する手順が、手動転写
による 1 文字破損 2 件を実害ゼロで止血した。本スクリプトはその手順の再実装
ではなく標準化——以後の spawn では毎回この計器を通す。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

_CONTEXT_CHARS = 60


class DeliveryMismatchError(RuntimeError):
    """`--expected`/delivered が strip 後バイト不一致。exit 1 に対応する。"""


class DeliveryVerifyOperationError(RuntimeError):
    """入出力エラー・JSONL からの抽出不能。exit 2 に対応する
    （照合そのものが成立しなかった、という意味で 1 とは区別する）。"""


class _NonTextContentBlockError(Exception):
    """`content` リスト中に non-text ブロック（画像等）または well-formed
    でない text ブロックが 1 つでも混入していることを示す内部シグナル。
    `_extract_user_text` が送出し、`extract_first_user_message` が捕捉して
    行番号付きの `DeliveryVerifyOperationError`（fail-closed メッセージ）に
    変換する。モジュール外へは伝播させない。"""


def _read_stripped_text(path: Path) -> str:
    """`path` を UTF-8 として読み、strip 後の文字列を返す。読み取り・
    デコード失敗はそのまま呼び出し元へ伝播する（`OSError`/
    `UnicodeDecodeError`。main() が exit 2 の運用エラーとして拾う）。"""

    return Path(path).read_bytes().decode("utf-8").strip()


def _extract_user_text(entry: Any) -> Optional[str]:
    """1 件の JSONL エントリから role=='user' な message の本文を抽出する。
    このエントリが user メッセージでない、または content の形が未知なら
    `None`（呼び出し元が「次の行を見る」か「抽出不能」かを判断する）。
    content が list で non-text ブロック（dict でない・`type != "text"`・
    `text` が str でない、のいずれか）を 1 つでも含む場合は `None` を返さず
    `_NonTextContentBlockError` を送出する（fail-closed。画像等を黙って
    無視して部分連結を exit 0 で認証しない——全ブロックが well-formed な
    text ブロックであることを要求する）。"""

    message = entry.get("message") if isinstance(entry, dict) else None
    if not isinstance(message, dict):
        # トップレベルに `message` が無い行形式（防御的）: エントリ自体が
        # `{"role": ..., "content": ...}` かもしれない。
        message = entry if isinstance(entry, dict) else None
    if not isinstance(message, dict):
        return None
    if message.get("role") != "user":
        return None

    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_blocks: list[str] = []
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ):
                text_blocks.append(block["text"])
            else:
                raise _NonTextContentBlockError()
        if text_blocks:
            return "".join(text_blocks)
        return None  # list content だが text ブロックが 1 つもない = 未知構造
    return None  # content が str でも list でもない = 未知構造


def extract_first_user_message(jsonl_path: Path) -> str:
    """`jsonl_path` から最初の role=='user' メッセージ本文を返す。
    抽出不能（不正 JSON 行・user エントリなし・content 形状不明）は
    `DeliveryVerifyOperationError` を送出する（exit 2 に対応。黙って
    空文字/exit 0 へは倒さない）。"""

    jsonl_path = Path(jsonl_path)
    try:
        raw_text = jsonl_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise DeliveryVerifyOperationError(f"cannot read {jsonl_path}: {exc}") from exc

    for line_number, raw_line in enumerate(raw_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DeliveryVerifyOperationError(
                f"{jsonl_path}:{line_number}: invalid JSON — cannot extract (fail-closed): {exc}"
            ) from exc

        message = entry.get("message") if isinstance(entry, dict) else None
        if not isinstance(message, dict):
            message = entry if isinstance(entry, dict) else None
        if isinstance(message, dict) and message.get("role") == "user":
            try:
                text = _extract_user_text(entry)
            except _NonTextContentBlockError:
                raise DeliveryVerifyOperationError(
                    f"{jsonl_path}:{line_number}: first role='user' entry's content "
                    "list contains a non-text content block — refusing to certify "
                    "delivery (fail-closed)"
                ) from None
            if text is None:
                raise DeliveryVerifyOperationError(
                    f"{jsonl_path}:{line_number}: first role='user' entry has an "
                    "unrecognized content shape (not str, not a list of type='text' "
                    "blocks) — refusing to extract (fail-closed)"
                )
            return text

    raise DeliveryVerifyOperationError(
        f"{jsonl_path}: no role='user' message found — cannot extract (fail-closed)"
    )


def _diff_context(expected: str, delivered: str) -> str:
    """最初の差分位置と、その前後 `_CONTEXT_CHARS` 文字の文脈を人間可読な
    複数行文字列で返す（stderr へそのまま出す想定）。"""

    min_len = min(len(expected), len(delivered))
    diff_index = 0
    while diff_index < min_len and expected[diff_index] == delivered[diff_index]:
        diff_index += 1

    context_start = max(0, diff_index - _CONTEXT_CHARS)
    expected_context = expected[context_start : diff_index + _CONTEXT_CHARS]
    delivered_context = delivered[context_start : diff_index + _CONTEXT_CHARS]
    return (
        f"mismatch at char offset {diff_index} "
        f"(expected len={len(expected)}, delivered len={len(delivered)})\n"
        f"  expected  context: {expected_context!r}\n"
        f"  delivered context: {delivered_context!r}"
    )


def compare_delivery(expected_text: str, delivered_text: str) -> None:
    """完全一致でなければ `DeliveryMismatchError`（差分位置+文脈込み）を
    送出する。"""

    if expected_text == delivered_text:
        return
    raise DeliveryMismatchError(_diff_context(expected_text, delivered_text))


def _build_arg_parser() -> argparse.ArgumentParser:
    """引数は `--expected`（必須）+ `--delivered`/`--delivered-jsonl`
    （排他必須の一方）のみ（+ argparse 既定の `-h`/`--help`）。自由記述の
    注入口を一切定義しない（`compose_payload.py` と同じ方針。
    `tests/test_l0b_scripts.py` がこの parser を検査してオプション集合を
    直接 assert する）。"""

    parser = argparse.ArgumentParser(description="L0b spawn delivery verbatim verifier")
    parser.add_argument(
        "--expected", type=Path, required=True, help="Path to the composed author_prompt.txt"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--delivered", type=Path, default=None, help="Path to the delivered prompt text file"
    )
    group.add_argument(
        "--delivered-jsonl",
        dest="delivered_jsonl",
        type=Path,
        default=None,
        help="Path to a task JSONL to extract the first user message from",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    try:
        expected_text = _read_stripped_text(args.expected)
        if args.delivered is not None:
            delivered_text = _read_stripped_text(args.delivered)
        else:
            delivered_text = extract_first_user_message(args.delivered_jsonl).strip()
    except (OSError, UnicodeDecodeError, DeliveryVerifyOperationError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    try:
        compare_delivery(expected_text, delivered_text)
    except DeliveryMismatchError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print("verify_prompt_delivery: match (strip-normalized byte-identical)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
