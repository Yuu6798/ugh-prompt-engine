"""e_use_table.py のテスト（設計正本 §10.2）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voice_genesis.calibration import e_use_table
from voice_genesis.calibration.candidates import registry as candidate_registry
from voice_genesis.calibration.gates import EUseEvidenceRow
from voice_genesis.calibration.vocab import ClaimCeiling, EvidenceClass


def _unjustified_row(construct_id: str = "formant_frequency") -> EUseEvidenceRow:
    return EUseEvidenceRow(
        construct_id=construct_id,
        unit="hz",
        domain="d",
        intended_use="UNFILLED",
        maximum_claim="UNFILLED",
        e_use_value=None,
        derivation_rule="UNFILLED",
        evidence_class=EvidenceClass.UNJUSTIFIED,
        source_id_or_url="UNFILLED",
        source_checked_at="UNFILLED",
        source_hash_or_version="UNFILLED",
        applicability_argument="UNFILLED",
        review_status="UNFILLED",
    )


def test_generate_template_has_no_numeric_e_use_placeholder(tmp_path: Path) -> None:
    path = tmp_path / "e_use_table.json"
    rows = e_use_table.generate_template(path, candidate_registry.ALL_CANDIDATES)
    assert rows  # non-empty
    for row in rows:
        assert row.evidence_class == EvidenceClass.UNJUSTIFIED
        assert row.e_use_value is None

    raw = json.loads(path.read_text(encoding="utf-8"))
    for entry in raw:
        assert entry["e_use_value"] is None
        assert entry["evidence_class"] == "UNJUSTIFIED"


def test_generate_template_one_row_per_unique_construct_unit_domain(tmp_path: Path) -> None:
    """PR レビュー第 2 巡: domain 文字列が候補ごとに異なれば別行（同一
    algorithm_family 内でもパラメタが domain 記述へ反映されている候補は複数行に
    なる。例: M3-BURG は `max_formant_hz` により domain が 2 種に分かれる）。"""
    path = tmp_path / "t.json"
    rows = e_use_table.generate_template(path, candidate_registry.ALL_CANDIDATES)
    expected = e_use_table.unique_construct_unit_domain(candidate_registry.ALL_CANDIDATES)
    assert len(rows) == len(expected)
    actual_keys = [(r.construct_id, r.unit, r.domain) for r in rows]
    assert actual_keys == expected
    # M3-BURG's domain text differs by max_formant_hz -> at least 2 distinct
    # formant_frequency rows should exist among the BURG-derived entries.
    burg_domains = {
        c.domain for c in candidate_registry.ALL_CANDIDATES if c.algorithm_family == "BURG_LPC"
    }
    assert len(burg_domains) >= 2


def test_row_round_trip_via_dict() -> None:
    row = _unjustified_row()
    d = e_use_table.row_to_dict(row)
    rebuilt = e_use_table.row_from_dict(d)
    assert rebuilt == row


def test_load_save_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "table.json"
    rows = [_unjustified_row("a"), _unjustified_row("b")]
    e_use_table.save_e_use_table(path, rows)
    loaded = e_use_table.load_e_use_table(path)
    assert loaded == rows


def test_row_from_dict_missing_column_raises() -> None:
    d = e_use_table.row_to_dict(_unjustified_row())
    del d["review_status"]
    with pytest.raises(KeyError):
        e_use_table.row_from_dict(d)


def test_unjustified_row_with_numeric_value_rejected_at_construction() -> None:
    with pytest.raises(ValueError):
        EUseEvidenceRow(
            construct_id="x",
            unit="hz",
            domain="d",
            intended_use="u",
            maximum_claim="m",
            e_use_value=1.0,
            derivation_rule="r",
            evidence_class=EvidenceClass.UNJUSTIFIED,
            source_id_or_url="s",
            source_checked_at="t",
            source_hash_or_version="v",
            applicability_argument="a",
            review_status="r",
        )


def test_validate_e_use_table_flags_user_accepted_without_gate1() -> None:
    row = EUseEvidenceRow(
        construct_id="harmonic_to_noise_ratio",
        unit="db",
        domain="d",
        intended_use="u",
        maximum_claim="DIRECTIONAL",
        e_use_value=2.0,
        derivation_rule="r",
        evidence_class=EvidenceClass.USER_ACCEPTED_USE_BOUND,
        source_id_or_url="s",
        source_checked_at="t",
        source_hash_or_version="v",
        applicability_argument="a",
        review_status="ACCEPTED",
    )
    violations = e_use_table.validate_e_use_table([row], gate1_e_use_bound_accepted=False)
    assert any("USER_ACCEPTED_USE_BOUND" in v for v in violations)

    violations_ok = e_use_table.validate_e_use_table([row], gate1_e_use_bound_accepted=True)
    assert violations_ok == []


def test_validate_e_use_table_accepts_clean_unjustified_row() -> None:
    row = _unjustified_row()
    assert e_use_table.validate_e_use_table([row], gate1_e_use_bound_accepted=False) == []


def test_auto_ceiling_non_unjustified_row_returns_none() -> None:
    row = EUseEvidenceRow(
        construct_id="x",
        unit="hz",
        domain="d",
        intended_use="u",
        maximum_claim="ABSOLUTE",
        e_use_value=1.0,
        derivation_rule="r",
        evidence_class=EvidenceClass.NORMATIVE_SPEC,
        source_id_or_url="s",
        source_checked_at="t",
        source_hash_or_version="v",
        applicability_argument="a",
        review_status="r",
    )
    assert e_use_table.auto_ceiling(row, has_apriori_truth_order=True) is None
    assert e_use_table.auto_ceiling(row, has_apriori_truth_order=False) is None


def test_auto_ceiling_unjustified_row_branches() -> None:
    row = _unjustified_row()
    assert e_use_table.auto_ceiling(row, has_apriori_truth_order=True) == ClaimCeiling.DIRECTIONAL
    assert (
        e_use_table.auto_ceiling(row, has_apriori_truth_order=False)
        == ClaimCeiling.DIAGNOSTIC_ONLY
    )
