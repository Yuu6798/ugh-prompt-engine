"""Phase D1: E_use evidence table の JSON loader/validator + template 生成
（設計正本 §10.2, IMPLEMENTATION_MAP §6.3）。

行の型は `gates.EUseEvidenceRow`（13 列）をそのまま再利用する
（`EUseEvidenceRow.__post_init__` が既に「`evidence_class=UNJUSTIFIED` は
`e_use_value` を持てない」を enforce している。設計正本 §10.2「UNJUSTIFIED に
数値 placeholder を作らない」）。本モジュールは (1) JSON <-> dataclass の
往復、(2) 13 列 shape + 語彙検証、(3) `USER_ACCEPTED_USE_BOUND` 行が Gate 1
`e_use_bound_accepted` を伴うことの検証、(4) 空 worksheet テンプレート生成、
(5) 自動 ceiling 導出、を追加する。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from voice_genesis.calibration.candidates.registry import ALL_CANDIDATES, Candidate
from voice_genesis.calibration.gates import EUseEvidenceRow, auto_ceiling_for_unjustified
from voice_genesis.calibration.vocab import ClaimCeiling, EvidenceClass

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: `GATE1_DECISION_RECORD.md` §4「全 USER_ACCEPTED_USE_BOUND 行に共通する
#: メタ情報: `source_id_or_url`: `"GATE1-DELEGATION-..."` ／
#: `source_hash_or_version`: 本ファイル（確定後の最終内容）の sha256」という
#: Gate 1 委任の運用規約を機械検証可能な形へ写した定数（round 20 採用 (1),
#: `[UNDERSPEC-CAL-D46]`）。`source_id_or_url` がこのプレフィックスで始まる
#: 行は、この repo-relative path を出典として引用しているとみなす。将来
#: 他の evidence_class/出典が別の repo-relative file を引用するようになれば
#: このプレフィックス集合を拡張する。
GATE1_DECISION_RECORD_RELATIVE_PATH = (
    "voice_genesis/calibration/approvals/records/GATE1_DECISION_RECORD.md"
)
GATE1_DELEGATION_SOURCE_ID_PREFIX = "GATE1-DELEGATION-"

#: `gates.EUseEvidenceRow` の列（設計正本 §10.2 が定める必須 13 列 +
#: `e_use_mode`（14 列目、`[UNDERSPEC-CAL-D11]`。absolute/relative の判別。
#: `gates.E_USE_MODE_VALUES` 参照））。
COLUMNS: tuple[str, ...] = (
    "construct_id",
    "unit",
    "domain",
    "intended_use",
    "maximum_claim",
    "e_use_value",
    "derivation_rule",
    "evidence_class",
    "source_id_or_url",
    "source_checked_at",
    "source_hash_or_version",
    "applicability_argument",
    "review_status",
    "e_use_mode",
)

_TEMPLATE_PLACEHOLDER = "UNFILLED"


def row_to_dict(row: EUseEvidenceRow) -> dict[str, Any]:
    d = asdict(row)
    d["evidence_class"] = row.evidence_class.value
    return d


def row_from_dict(d: Mapping[str, Any]) -> EUseEvidenceRow:
    """`d` から `EUseEvidenceRow` を構築する。13 列の欠落は `KeyError`、
    `evidence_class` が閉語彙外なら `ValueError`（`EUseEvidenceRow.__post_init__`
    経由の UNJUSTIFIED+数値の組み合わせ違反も同様）を fail-closed で送出する。
    """
    missing = [c for c in COLUMNS if c not in d]
    if missing:
        raise KeyError(f"e_use_table row missing column(s): {missing}")
    evidence_class_raw = d["evidence_class"]
    try:
        evidence_class = EvidenceClass(evidence_class_raw)
    except ValueError as exc:
        raise ValueError(f"e_use_table row: invalid evidence_class {evidence_class_raw!r}") from exc
    return EUseEvidenceRow(
        construct_id=str(d["construct_id"]),
        unit=str(d["unit"]),
        domain=str(d["domain"]),
        intended_use=str(d["intended_use"]),
        maximum_claim=str(d["maximum_claim"]),
        e_use_value=None if d["e_use_value"] is None else float(d["e_use_value"]),
        derivation_rule=str(d["derivation_rule"]),
        evidence_class=evidence_class,
        source_id_or_url=str(d["source_id_or_url"]),
        source_checked_at=str(d["source_checked_at"]),
        source_hash_or_version=str(d["source_hash_or_version"]),
        applicability_argument=str(d["applicability_argument"]),
        review_status=str(d["review_status"]),
        e_use_mode=str(d["e_use_mode"]),
    )


def load_e_use_table(path: Path) -> list[EUseEvidenceRow]:
    """JSON array of row-objects を読み、各行を `row_from_dict()` で構築する。
    1 行でも構築に失敗すれば、どの行が悪いかを示して fail-closed する。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path}: must contain a JSON array of row objects")
    rows: list[EUseEvidenceRow] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            raise ValueError(f"{path}[{i}]: row must be a JSON object")
        try:
            rows.append(row_from_dict(entry))
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError(f"{path}[{i}]: {exc}") from exc
    return rows


def save_e_use_table(path: Path, rows: Sequence[EUseEvidenceRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([row_to_dict(r) for r in rows], indent=2, ensure_ascii=False, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def validate_e_use_table(
    rows: Sequence[EUseEvidenceRow], *, gate1_e_use_bound_accepted: bool
) -> list[str]:
    """(1) 13 列 shape は `EUseEvidenceRow` 型そのものが保証する（load 時点で
    fail-closed 済み）。ここでは残る横断制約を検査する:

    - `evidence_class == UNJUSTIFIED` の行は `e_use_value is None`
      （`EUseEvidenceRow.__post_init__` が既に構築時点で enforce しているため、
      理論上ここに到達する違反は存在しないが、dataclass を経由せず外部から
      構築された行を防御的に再検査する）。
    - `evidence_class == USER_ACCEPTED_USE_BOUND` の行は Gate 1 承認の
      `e_use_bound_accepted=True` を伴わなければならない（設計正本 §10.2:
      「USER_ACCEPTED_USE_BOUND はユーザー判断1へ統合」）。
    - （第 9 巡採用）表の `(construct_id, unit, domain)` キー集合は
      `unique_construct_unit_domain(registry.ALL_CANDIDATES)`（現在の候補
      registry から機械導出される期待キー集合）と **厳密に一致**しなければ
      ならない。欠落キー（登録候補にはあるが表に無い）・余剰キー（表には
      あるがどの登録候補にも対応しない）・重複キー（同一キーが複数行に
      現れる）はそれぞれ個別の違反として列挙する。fail-closed: 候補の
      追加/削除に E_use table の更新が追随していない状態を静かに見逃さない
      （行単位の shape/evidence 検査だけでは検出できない）。
    """
    violations: list[str] = []
    for i, row in enumerate(rows):
        if row.evidence_class == EvidenceClass.UNJUSTIFIED and row.e_use_value is not None:
            violations.append(
                f"row[{i}] ({row.construct_id}): evidence_class=UNJUSTIFIED must have "
                "e_use_value=null"
            )
        if row.evidence_class == EvidenceClass.USER_ACCEPTED_USE_BOUND and not gate1_e_use_bound_accepted:
            violations.append(
                f"row[{i}] ({row.construct_id}): evidence_class=USER_ACCEPTED_USE_BOUND "
                "requires Gate 1 approval e_use_bound_accepted=true"
            )

    expected_key_set = set(unique_construct_unit_domain(ALL_CANDIDATES))
    table_keys = [(row.construct_id, row.unit, row.domain) for row in rows]
    table_key_set = set(table_keys)

    for key in sorted(expected_key_set - table_key_set):
        violations.append(
            f"missing row for (construct_id, unit, domain)={key!r} "
            "(declared by the candidates registry but absent from the E_use table)"
        )
    for key in sorted(table_key_set - expected_key_set):
        violations.append(
            f"unexpected row for (construct_id, unit, domain)={key!r} "
            "(not declared by any candidate in the registry)"
        )

    key_counts: dict[tuple[str, str, str], int] = {}
    for key in table_keys:
        key_counts[key] = key_counts.get(key, 0) + 1
    for key in sorted(k for k, count in key_counts.items() if count > 1):
        violations.append(
            f"duplicate row(s) for (construct_id, unit, domain)={key!r}: "
            f"appears {key_counts[key]} times in the table (must appear exactly once)"
        )

    return violations


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_source_digests(
    rows: Sequence[EUseEvidenceRow],
    *,
    source_path: Path | None = None,
    repo_root: Path | None = None,
) -> list[str]:
    """行が引用する repo-relative な出典ファイルの実際の sha256 と、行の
    `source_hash_or_version` 列が一致することを検証する（round 20 採用
    (1)(b), `[UNDERSPEC-CAL-D46]`）。

    現状 `GATE1_DELEGATION_SOURCE_ID_PREFIX` で始まる `source_id_or_url` を
    持つ行（＝ `GATE1_DECISION_RECORD.md` §4 の Gate 1 委任規約に従う
    `USER_ACCEPTED_USE_BOUND` 行）のみが対象。それ以外の行（`UNJUSTIFIED`
    の `UNFILLED` placeholder 等、repo-relative file を出典に持たない行）は
    無視する（fail-open ではなく「対象外」——`validate_e_use_table` 側の
    `UNJUSTIFIED`/`USER_ACCEPTED_USE_BOUND` 横断制約とは独立した別チェック）。

    出典ファイルの読込に一度でも失敗すれば（対象行が1件以上ある場合のみ）
    その旨を 1 件の違反として返す。不一致行はそれぞれ個別に
    `E_USE_SOURCE_DIGEST_MISMATCH` プレフィックス付きで、row 番号と
    `(construct_id, unit, domain)` を名指しして列挙する（fail-closed:
    `GATE1_DECISION_RECORD.md` が確定後に編集され digest が動いた場合に
    黙って古い digest を通過させない）。`source_path` を明示すれば
    （テスト/`restamp` CLI 用）その path をそのまま出典として使う——未指定
    時のみ `repo_root / GATE1_DECISION_RECORD_RELATIVE_PATH` を既定解決する。
    """
    root = repo_root if repo_root is not None else _REPO_ROOT
    resolved_source_path = (
        source_path if source_path is not None else root / GATE1_DECISION_RECORD_RELATIVE_PATH
    )
    matching = [
        (i, row)
        for i, row in enumerate(rows)
        if row.source_id_or_url.startswith(GATE1_DELEGATION_SOURCE_ID_PREFIX)
    ]
    if not matching:
        return []
    try:
        actual_sha256 = _sha256_file(resolved_source_path)
    except OSError as exc:
        row_ids = [row.construct_id for _, row in matching]
        return [
            f"E_USE_SOURCE_DIGEST_MISMATCH: cannot read cited source "
            f"{resolved_source_path} for row(s) {row_ids!r}: {exc}"
        ]
    violations: list[str] = []
    for i, row in matching:
        if row.source_hash_or_version != actual_sha256:
            violations.append(
                f"E_USE_SOURCE_DIGEST_MISMATCH: row[{i}] "
                f"({row.construct_id}/{row.unit}/{row.domain}): source_hash_or_version "
                f"({row.source_hash_or_version!r}) does not match sha256 of "
                f"{resolved_source_path} ({actual_sha256!r}) — the cited source appears "
                "to have changed since this row's digest was stamped"
            )
    return violations


def restamp_source_digests(
    path: Path, *, source_path: Path | None = None, repo_root: Path | None = None
) -> tuple[int, str]:
    """`path` の E_use table を読み、`GATE1_DELEGATION_SOURCE_ID_PREFIX` 行の
    `source_hash_or_version` を `GATE1_DECISION_RECORD.md` の現在の sha256 で
    書き換えて `path` へ書き戻す（round 20 採用 (1)(a) の機械的再刻印。
    `approvals.refresh_document_hashes()` と同じ「他フィールドは一切変更
    しない」規約）。**`GATE1_DECISION_RECORD.md` 自体は変更しない** ——
    運用手順は「決定記録を確定 → 本関数で再刻印 → commit → dry-run」の順
    （`approvals/README.md` freeze runbook）。

    戻り値は `(書き換えた行数, 新 sha256)`。対象行が 0 件なら
    `(0, 新 sha256)` を返す（table 自体は変更しない）。`source_path` を
    明示すればその path をそのまま出典として使う（CLI 用）——未指定時のみ
    `repo_root / GATE1_DECISION_RECORD_RELATIVE_PATH` を既定解決する。"""
    root = repo_root if repo_root is not None else _REPO_ROOT
    resolved_source_path = (
        source_path if source_path is not None else root / GATE1_DECISION_RECORD_RELATIVE_PATH
    )
    rows = load_e_use_table(path)
    new_sha256 = _sha256_file(resolved_source_path)
    changed = 0
    restamped_rows: list[EUseEvidenceRow] = []
    for row in rows:
        if row.source_id_or_url.startswith(GATE1_DELEGATION_SOURCE_ID_PREFIX):
            if row.source_hash_or_version != new_sha256:
                changed += 1
            row = replace_source_hash(row, new_sha256)
        restamped_rows.append(row)
    save_e_use_table(path, restamped_rows)
    return changed, new_sha256


def replace_source_hash(row: EUseEvidenceRow, new_sha256: str) -> EUseEvidenceRow:
    """`row` の `source_hash_or_version` のみを差し替えた新しい
    `EUseEvidenceRow` を返す（frozen dataclass のため `dataclasses.replace`
    を使う薄いラッパー — `restamp_source_digests()` 専用）。"""
    return replace(row, source_hash_or_version=new_sha256)


def unique_construct_unit_domain(candidates: Iterable[Candidate]) -> list[tuple[str, str, str]]:
    """`(construct, unit, domain)` の一意なタプルを、`registry.ALL_CANDIDATES`
    の宣言順で最初に現れた順に返す（PR レビュー第 2 巡: domain 文字列が
    候補ごとに異なれば別行として扱う — 同一 algorithm_family 内でも
    パラメタが domain 記述へ反映されている候補は複数行になる。例:
    M3-BURG は `max_formant_hz` により domain 文字列が 2 種に分かれる）。
    """
    seen: dict[tuple[str, str, str], None] = {}
    for c in candidates:
        key = (c.construct, c.unit, c.domain)
        if key not in seen:
            seen[key] = None
    return list(seen.keys())


def generate_template(path: Path, candidates: Iterable[Candidate]) -> list[EUseEvidenceRow]:
    """`candidates` の一意な `(construct, unit, domain)` タプルごとに 1 行、
    全て `evidence_class=UNJUSTIFIED` かつ `e_use_value=None` の空 worksheet を
    生成して `path` へ書き、生成した行を返す。数値 placeholder は一切書かない
    （設計正本 §10.2「UNJUSTIFIED に数値 placeholder を作らない」）。
    """
    rows = [
        EUseEvidenceRow(
            construct_id=construct,
            unit=unit,
            domain=domain,
            intended_use=_TEMPLATE_PLACEHOLDER,
            maximum_claim=_TEMPLATE_PLACEHOLDER,
            e_use_value=None,
            derivation_rule=_TEMPLATE_PLACEHOLDER,
            evidence_class=EvidenceClass.UNJUSTIFIED,
            source_id_or_url=_TEMPLATE_PLACEHOLDER,
            source_checked_at=_TEMPLATE_PLACEHOLDER,
            source_hash_or_version=_TEMPLATE_PLACEHOLDER,
            applicability_argument=_TEMPLATE_PLACEHOLDER,
            review_status=_TEMPLATE_PLACEHOLDER,
        )
        for construct, unit, domain in unique_construct_unit_domain(candidates)
    ]
    save_e_use_table(path, rows)
    return rows


def auto_ceiling(row: EUseEvidenceRow, has_apriori_truth_order: bool) -> ClaimCeiling | None:
    """設計正本 §10.2 の自動 ceiling: `row.evidence_class != UNJUSTIFIED` の
    行には適用対象がない（`None` を返す — 呼び出し側は通常経路の ceiling 判定
    を使う）。`UNJUSTIFIED` 行は `gates.auto_ceiling_for_unjustified()` を
    委譲する（独立 truth order が事前に立つ → DIRECTIONAL、立たない →
    DIAGNOSTIC_ONLY。NOT_EVALUABLE へは落とさない）。
    """
    if row.evidence_class != EvidenceClass.UNJUSTIFIED:
        return None
    return auto_ceiling_for_unjustified(has_apriori_truth_order)


# ---------------------------------------------------------------------------
# CLI — `python -m voice_genesis.calibration.e_use_table restamp ...`
# ---------------------------------------------------------------------------

#: `c0_freeze.DEFAULT_E_USE_TABLE_RELATIVE_PATH` と同値を独立に保持する
#: （`c0_freeze.py` は本モジュールを import するため、逆 import は循環に
#: なる — `approvals.py`/`c0_freeze.py` が互いの定数を持ち合わない既存方針
#: と同じ）。
DEFAULT_E_USE_TABLE_RELATIVE_PATH = "voice_genesis/calibration/config/e_use_table_v1.json"

#: 本モジュール自身が置かれているディレクトリ（`voice_genesis/calibration/`）。
#: CLI の `--source` に相対 path が渡された場合、リポジトリ直下ではなく
#: この package root を基準に解決する（round 20 ADOPT(1) の実行例
#: `--source approvals/records/GATE1_DECISION_RECORD.md` が
#: `voice_genesis/calibration/` 相対で書かれているため）。
_PACKAGE_ROOT = Path(__file__).resolve().parent


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m voice_genesis.calibration.e_use_table",
        description="E_use evidence table (config/e_use_table_v1.json) utilities.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    restamp = sub.add_parser(
        "restamp",
        help=(
            "Re-stamp source_hash_or_version on every row whose source_id_or_url "
            f"starts with {GATE1_DELEGATION_SOURCE_ID_PREFIX!r} to GATE1_DECISION_"
            "RECORD.md's current sha256. All other columns/rows untouched. This "
            "command does NOT edit GATE1_DECISION_RECORD.md itself. Freeze runbook "
            "order: finalize GATE1_DECISION_RECORD.md -> restamp -> commit -> dry-run "
            "(see approvals/README.md)."
        ),
    )
    restamp.add_argument(
        "--table-path",
        type=Path,
        default=None,
        help=(
            "E_use table JSON path to restamp (default: "
            f"{DEFAULT_E_USE_TABLE_RELATIVE_PATH!r} repo-relative)"
        ),
    )
    restamp.add_argument(
        "--source",
        type=Path,
        default=None,
        help=(
            "GATE1_DECISION_RECORD.md path to hash (default: "
            f"{GATE1_DECISION_RECORD_RELATIVE_PATH!r} repo-relative). A relative "
            "value here is resolved against this module's own directory "
            "(voice_genesis/calibration/), not the repo root or CWD."
        ),
    )
    restamp.add_argument("--repo-root", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    if args.command != "restamp":
        return 1  # pragma: no cover - argparse `required=True` prevents reaching this
    root = args.repo_root if args.repo_root is not None else _REPO_ROOT
    table_path = (
        args.table_path
        if args.table_path is not None
        else root / DEFAULT_E_USE_TABLE_RELATIVE_PATH
    )
    if args.source is None:
        source_path = root / GATE1_DECISION_RECORD_RELATIVE_PATH
    else:
        source_path = args.source if args.source.is_absolute() else _PACKAGE_ROOT / args.source
    changed, new_sha256 = restamp_source_digests(table_path, source_path=source_path)
    print(f"table: {table_path}")
    print(f"source: {source_path}")
    print(f"new sha256: {new_sha256}")
    print(f"rows changed: {changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "COLUMNS",
    "GATE1_DECISION_RECORD_RELATIVE_PATH",
    "GATE1_DELEGATION_SOURCE_ID_PREFIX",
    "DEFAULT_E_USE_TABLE_RELATIVE_PATH",
    "row_to_dict",
    "row_from_dict",
    "load_e_use_table",
    "save_e_use_table",
    "validate_e_use_table",
    "validate_source_digests",
    "restamp_source_digests",
    "replace_source_hash",
    "unique_construct_unit_domain",
    "generate_template",
    "auto_ceiling",
]
