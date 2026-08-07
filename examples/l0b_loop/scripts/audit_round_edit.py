"""L0b 周回受付時の score.yaml 編集監査 (`.claude/memory/2026-08-07.md`
Handoff「②周回受付時の score 全文 diff 機械検査」が背景の運用改善候補)。

`audit_round_edit.py --prev <score.yaml> --curr <score.yaml>
[--intent <intent.yaml>]` は、前周と今周の `score.yaml` を比較し、次の 3 種の
**事実**（判定ではない）を JSON レポートとして stdout へ出力する:

(a) **変更フィールドの全列挙** — `score.yaml` を dict/list 再帰的にドット
    表記のパスへ平坦化し（list 要素は `path[index]` 記法、例:
    `structure[2].role`）、両平坦化結果を突き合わせて `modified`/`added`/
    `removed` を列挙する。**空の dict (`{}`) / 空の list (`[]`) は leaf として
    そのコンテナ自体を記録する**（中間ノードとして再帰を打ち切る）——
    `avoid: []` の追加・削除や `[]`↔`{}` の形状変化も列挙対象に入れるため。
(b) **認識語彙の出現一覧と変化** — `structure` 各セクションについて、
    `physical` + `role` を連結した文字列（`contract_v2.md` §2「ヒント照合の
    対象フィールド」と同一の照合対象）に対し、演奏者が実際に認識する 8 語
    （`RECOGNIZED_HINT_TOKENS` — `src/svp_rpe/perform/performer.py` の
    ソーステキストに literal として存在することを
    `tests/test_l0b_scripts.py` が拘束する）それぞれの出現回数を数え、前周
    からの変化があったセクション/語だけを列挙する。
(c) **intent の parse 成否**（`--intent` 指定時のみ）— `yaml.safe_load` を
    実行し、成功なら `intent_parseable: true`、例外なら `false` + 例外
    メッセージを記録する。

**この計器は判定しない**: tier 優先順位の解決（`contract_v2.md` §2「演奏
ヒントの解決規則」）・「単一変数か」の合否判定・intent とスコアの整合判定は
一切実装しない——事実の列挙のみを行い、tier 解決ロジックを再実装しない
（重複実装・ドリフトの回避）。判断はレポートを読んだ coordinator/Fable が
行う。

出力は 1 つの JSON オブジェクトを stdout へ pretty-print する（他計器と違い
決定論直列化の pin 対象ではないため `sort_keys`/`indent` は可読性目的）。
exit code: 正常終了（比較を実行できた）= 0、入出力エラー・`score.yaml` が
YAML mapping でない等の運用エラー = 1。`--intent` の YAML 構文エラー自体は
運用エラーではなく (c) の `intent_parseable: false` としてレポートへ記録
する（`--intent` ファイル自体が読めない場合は運用エラー = 1）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

SCHEMA_VERSION = "l0b-round-edit-audit/0.1"

# `contract_v2.md` §2「演奏の仕組み（著述ガイド）」が定義する、演奏者が
# 認識する手掛かり語彙の全体（8 語）。正本は `src/svp_rpe/perform/
# performer.py` のソーステキスト（`_hint_profile` 相当の分岐が参照する
# リテラル文字列群）——`test_recognized_hint_tokens_match_performer_source`
# がこのタプルの各要素を performer.py 内の literal として拘束し、乖離したら
# 赤くなる。
RECOGNIZED_HINT_TOKENS: tuple[str, ...] = (
    "silence",
    "no kick",
    "low density",
    "sub bass",
    "sparse",
    "full energy",
    "release",
    "rest",
)


class RoundEditAuditError(RuntimeError):
    """運用エラー（入出力・YAML 形状不正）。exit 1 に対応する。"""


def _load_score_mapping(path: Path) -> dict[str, Any]:
    """`path` を `yaml.safe_load` する（設計ノートの指定どおり——重複キー
    拒否等の追加検証は行わない、この計器は事実報告専用でスコアの正当性を
    検証する立場にない）。トップレベルが mapping でなければ
    `RoundEditAuditError`。"""

    with Path(path).open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise RoundEditAuditError(
            f"{path}: score.yaml must be a mapping (YAML dict) at the top level, "
            f"got {type(data).__name__}"
        )
    return data


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """dict/list を再帰的に平坦化し、dot-path（list は `path[index]`
    記法）-> leaf 値の dict を返す。非空の中間ノード（dict/list そのもの）
    はキーとして現れない——leaf のみが `field_changes` の対象になる。**空の
    dict (`{}`) / 空の list (`[]`) は再帰を打ち切り、そのコンテナ自体を
    `prefix` の leaf として記録する**（`avoid: []` の追加・削除や
    `[]`↔`{}` の形状変化を黙って消さないため。トップレベル全体が空の
    ケースは leaf キー `""` になるが、トップレベルは mapping 強制済み
    （`_load_score_mapping`）のため実質 `{}` のみが該当する）。"""

    flat: dict[str, Any] = {}
    if isinstance(value, dict):
        if not value:
            flat[prefix] = value
        else:
            for key, sub in value.items():
                sub_prefix = f"{prefix}.{key}" if prefix else str(key)
                flat.update(_flatten(sub, sub_prefix))
    elif isinstance(value, list):
        if not value:
            flat[prefix] = value
        else:
            for index, sub in enumerate(value):
                flat.update(_flatten(sub, f"{prefix}[{index}]"))
    else:
        flat[prefix] = value
    return flat


def _diff_fields(prev: dict[str, Any], curr: dict[str, Any]) -> list[dict[str, Any]]:
    """平坦化した prev/curr を突き合わせ、`modified`/`added`/`removed` を
    パス昇順で列挙する。"""

    prev_flat = _flatten(prev)
    curr_flat = _flatten(curr)
    all_paths = sorted(set(prev_flat) | set(curr_flat))

    changes: list[dict[str, Any]] = []
    for path in all_paths:
        in_prev = path in prev_flat
        in_curr = path in curr_flat
        if in_prev and in_curr:
            if prev_flat[path] != curr_flat[path]:
                changes.append(
                    {"path": path, "change": "modified", "prev": prev_flat[path], "curr": curr_flat[path]}
                )
        elif in_prev:
            changes.append({"path": path, "change": "removed", "prev": prev_flat[path], "curr": None})
        else:
            changes.append({"path": path, "change": "added", "prev": None, "curr": curr_flat[path]})
    return changes


def _section_hint_text(section: Any) -> str:
    """`physical` + `role` を演奏者の照合対象と**バイト同一**の正規化で
    連結した文字列を返す（`contract_v2.md` §2「ヒント照合の対象
    フィールド」）。実消費者 `src/svp_rpe/perform/performer.py`
    `_section_profile` は `f"{section.physical} {section.role}".lower()`
    （space join + lowercase）で照合する——これを鏡写しにしないと、
    大文字小文字違いの取りこぼし（例: "Full Energy"）や、無区切り連結による
    偽陽性/偽陰性（例: "physical: sub" + "role: bass" は無区切りだと
    "subbass" になり "sub bass" にヒットしない／"core"+"study" が無区切り
    "corestudy" 経由で "rest" に誤ヒットする）が生じ、この計器が実消費者と
    異なる事実を報告してしまう。値が str でなければ空文字扱い（型の妥当性
    検証はこの計器のスコープ外）。"""

    if not isinstance(section, dict):
        return ""
    physical = section.get("physical", "")
    role = section.get("role", "")
    physical_str = physical if isinstance(physical, str) else ""
    role_str = role if isinstance(role, str) else ""
    return f"{physical_str} {role_str}".lower()


def _vocab_hits(text: str) -> dict[str, int]:
    return {token: text.count(token) for token in RECOGNIZED_HINT_TOKENS}


def _section_vocab_report(score: dict[str, Any]) -> list[dict[str, Any]]:
    structure = score.get("structure")
    if not isinstance(structure, list):
        return []

    report: list[dict[str, Any]] = []
    for index, section in enumerate(structure):
        report.append(
            {
                "index": index,
                "section": section.get("section") if isinstance(section, dict) else None,
                "hits": _vocab_hits(_section_hint_text(section)),
            }
        )
    return report


def _vocab_diff(
    prev_sections: list[dict[str, Any]], curr_sections: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """index 単位で prev/curr の語彙出現回数を突き合わせ、変化した
    index/語だけを列挙する（`structure` の長さが変わった場合は
    `section_added`/`section_removed` を記録する）。"""

    prev_by_index = {entry["index"]: entry for entry in prev_sections}
    curr_by_index = {entry["index"]: entry for entry in curr_sections}
    all_indices = sorted(set(prev_by_index) | set(curr_by_index))

    diffs: list[dict[str, Any]] = []
    for index in all_indices:
        prev_entry = prev_by_index.get(index)
        curr_entry = curr_by_index.get(index)
        if prev_entry is None:
            diffs.append({"index": index, "change": "section_added", "curr_hits": curr_entry["hits"]})
            continue
        if curr_entry is None:
            diffs.append({"index": index, "change": "section_removed", "prev_hits": prev_entry["hits"]})
            continue

        prev_hits = prev_entry["hits"]
        curr_hits = curr_entry["hits"]
        changed_tokens = {
            token: {"prev": prev_hits[token], "curr": curr_hits[token]}
            for token in RECOGNIZED_HINT_TOKENS
            if prev_hits[token] != curr_hits[token]
        }
        if changed_tokens:
            diffs.append({"index": index, "change": "hits_changed", "tokens": changed_tokens})
    return diffs


def _intent_report(intent_path: Path) -> dict[str, Any]:
    """`intent_path` を読み `yaml.safe_load` の成否を記録する。ファイル自体
    が読めない（`OSError`/デコード失敗）場合はここでは吸収せず呼び出し元へ
    伝播させる（運用エラー = exit 1）。YAML 構文エラーのみを (c) の対象と
    して `intent_parseable: false` に丸める。"""

    raw_text = Path(intent_path).read_text(encoding="utf-8")
    try:
        yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        return {"intent_parseable": False, "error": str(exc)}
    return {"intent_parseable": True}


def audit_round_edit(
    prev_path: Path, curr_path: Path, intent_path: Optional[Path] = None
) -> dict[str, Any]:
    """モジュール docstring (a)/(b)/(c) を実行し、レポート dict を返す。"""

    prev_score = _load_score_mapping(prev_path)
    curr_score = _load_score_mapping(curr_path)

    prev_vocab = _section_vocab_report(prev_score)
    curr_vocab = _section_vocab_report(curr_score)

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "prev_path": str(prev_path),
        "curr_path": str(curr_path),
        "field_changes": _diff_fields(prev_score, curr_score),
        "vocabulary": {
            "recognized_tokens": list(RECOGNIZED_HINT_TOKENS),
            "prev_sections": prev_vocab,
            "curr_sections": curr_vocab,
            "changed": _vocab_diff(prev_vocab, curr_vocab),
        },
    }
    if intent_path is not None:
        report["intent"] = _intent_report(intent_path)
    return report


def _build_arg_parser() -> argparse.ArgumentParser:
    """引数は `--prev`/`--curr`（必須）+ `--intent`（任意）のみ（+ argparse
    既定の `-h`/`--help`）。自由記述の注入口を一切定義しない
    （`compose_payload.py` と同じ方針。`tests/test_l0b_scripts.py` がこの
    parser を検査してオプション集合を直接 assert する）。"""

    parser = argparse.ArgumentParser(description="L0b round-edit factual audit (no verdicts)")
    parser.add_argument("--prev", type=Path, required=True, help="Path to the previous round's score.yaml")
    parser.add_argument("--curr", type=Path, required=True, help="Path to the current round's score.yaml")
    parser.add_argument(
        "--intent", type=Path, default=None, help="Path to the current round's intent.yaml (optional)"
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    try:
        report = audit_round_edit(args.prev, args.curr, args.intent)
    except (OSError, UnicodeDecodeError, yaml.YAMLError, RoundEditAuditError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
