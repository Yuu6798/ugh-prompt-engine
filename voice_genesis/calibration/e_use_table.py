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

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from voice_genesis.calibration.candidates.registry import Candidate
from voice_genesis.calibration.gates import EUseEvidenceRow, auto_ceiling_for_unjustified
from voice_genesis.calibration.vocab import ClaimCeiling, EvidenceClass

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
    fail-closed 済み）。ここでは残る 2 つの横断制約を検査する:

    - `evidence_class == UNJUSTIFIED` の行は `e_use_value is None`
      （`EUseEvidenceRow.__post_init__` が既に構築時点で enforce しているため、
      理論上ここに到達する違反は存在しないが、dataclass を経由せず外部から
      構築された行を防御的に再検査する）。
    - `evidence_class == USER_ACCEPTED_USE_BOUND` の行は Gate 1 承認の
      `e_use_bound_accepted=True` を伴わなければならない（設計正本 §10.2:
      「USER_ACCEPTED_USE_BOUND はユーザー判断1へ統合」）。
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
    return violations


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


__all__ = [
    "COLUMNS",
    "row_to_dict",
    "row_from_dict",
    "load_e_use_table",
    "save_e_use_table",
    "validate_e_use_table",
    "unique_construct_unit_domain",
    "generate_template",
    "auto_ceiling",
]
