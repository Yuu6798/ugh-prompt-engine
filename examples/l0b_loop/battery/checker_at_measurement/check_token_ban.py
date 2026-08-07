"""L0b-R R4 語句制約の機械判定 (`battery/task_br_d2.md` §「R4 語句制約の機械判定」/
`battery/ledger_l0br.yaml` `constraint_checker` 節が正本).

`check_token_ban.py --score <score.yaml> [-o <result.json>]` は、提出
`score.yaml` の生テキスト全文（UTF-8 デコード後・小文字化）に対して禁止語
`BANNED_TOKENS`（台帳 `constraint_checker.banned_tokens` と同値: `silence` /
`no kick` / `low density` / `sub bass`）の部分文字列出現回数を数え、1 つでも
出現すれば `fail`、いずれも 0 回なら `pass` として判定する。

自由記述の注入口（`--note`/`--comment`/`--extra` 類）を一切定義しない
（`compose_payload.py` と同じ AGENTS.md §8「情報遮断実験のペイロード組成は
機械化する」則の中心的な安全特性を踏襲——引数は `--score`/`-o`（+ argparse
既定の `-h`/`--help`）のみ。`tests/test_l0b_scripts.py` がこの parser を
検査してオプション集合を直接 assert する）。

判定は score ファイルの生バイトを **1 回だけ** 読み（single-read）、その
バイト列から `score_sha256`（判定結果へ pin する実測値）と UTF-8 デコードの
両方を導出する。デコードが失敗した場合はバイナリ/非 UTF-8 提出物とみなし、
判定を行わず運用エラー（exit 2）として fail-closed に拒否する
（`compose_payload.py` の各 part 処理と同じ方針: 判定不能な入力を「pass」側
へ倒さない）。

判定結果（著者へは送らない——`task_br_d2.md`「判定結果は著者へ送らない」節。
本スクリプトの出力先は coordinator 手元の台帳記録のみ）は決定論直列化した
JSON として `-o` へ publish する（`sort_keys=True`・`indent=2`・
`ensure_ascii=False`・末尾改行。`hits` は 4 語全部のカウントを 0 も含めて
明記する——「該当なしはキー省略」ではなく「該当なしは値 0」で正直に会計する）。
`-o` の既存ファイルは上書き禁止（`compose_payload.py` と同じ
`atomic_write_bytes` 経由の fail-fast publish）。

exit code: `pass` = 0 / `fail` = 1 / 運用エラー（`-o` 上書き拒否・非 UTF-8
デコード失敗等）= 2。stdout には判定結果の 1 行サマリのみ、エラーは stderr。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Optional

from svp_rpe.utils.atomic_io import atomic_write_bytes

SCHEMA_VERSION = "l0br-token-ban/0.1"

# 台帳 `constraint_checker.banned_tokens`（`battery/ledger_l0br.yaml`）と同値。
# ここが正本——台帳側は本定数からの転記。
BANNED_TOKENS: tuple[str, ...] = ("silence", "no kick", "low density", "sub bass")


class TokenBanCheckError(RuntimeError):
    """運用エラー（非 UTF-8 デコード失敗・`-o` 上書き禁止）。exit 2 に対応する。"""


def _count_hits(lowered_text: str) -> dict[str, int]:
    """`BANNED_TOKENS` それぞれの部分文字列出現回数を数える。0 件も明記する。"""

    return {token: lowered_text.count(token) for token in BANNED_TOKENS}


def check_token_ban(score_path: Path) -> dict[str, object]:
    """`score_path` の生バイトを 1 回だけ読み、判定結果 dict を返す。

    非 UTF-8 は `TokenBanCheckError` で拒否する（fail-closed——判定不能な
    入力を「pass」側へ倒さない）。
    """

    score_path = Path(score_path)
    data = score_path.read_bytes()
    score_sha256 = hashlib.sha256(data).hexdigest()

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TokenBanCheckError(
            f"{score_path} is not valid UTF-8 text — refusing to judge (fail-closed): {exc}"
        ) from exc

    hits = _count_hits(text.lower())
    status = "pass" if all(count == 0 for count in hits.values()) else "fail"

    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "score_sha256": score_sha256,
        "banned_tokens": list(BANNED_TOKENS),
        "hits": hits,
    }


def _publish_result(result: dict[str, object], out_path: Path) -> None:
    out_path = Path(out_path)
    if out_path.exists():
        raise TokenBanCheckError(f"refusing to overwrite existing output file: {out_path}")
    payload = (
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    atomic_write_bytes(out_path, payload)


def _build_arg_parser() -> argparse.ArgumentParser:
    """引数は `--score`（必須）/ `-o`（任意）の 2 つのみ（+ argparse 既定の
    `-h`/`--help`）。自由記述の注入口を一切定義しない（`compose_payload.py`
    §「自由記述の注入口を一切定義しない」則と同じ——
    `tests/test_l0b_scripts.py` がこの parser を検査してオプション集合を
    直接 assert する）。"""

    parser = argparse.ArgumentParser(description="L0b-R R4 token-ban constraint checker")
    parser.add_argument(
        "--score", type=Path, required=True, help="Path to the submitted score.yaml"
    )
    parser.add_argument(
        "-o", dest="out", type=Path, default=None, help="Path to write the judgement JSON to"
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    try:
        result = check_token_ban(args.score)
        if args.out is not None:
            _publish_result(result, args.out)
    except (TokenBanCheckError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    hits_summary = " ".join(f"{token!r}={count}" for token, count in result["hits"].items())
    print(
        f"check_token_ban: status={result['status']} score_sha256={result['score_sha256']} "
        f"hits=({hits_summary})"
    )
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
