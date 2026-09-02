"""e_use_table.py のテスト（設計正本 §10.2）。"""

from __future__ import annotations

import hashlib
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


def _unjustified_row_for(construct_id: str, unit: str, domain: str) -> EUseEvidenceRow:
    """`_unjustified_row()` の unit/domain も指定できる版（第 9 巡採用のキー
    集合完全一致チェック向け: 特定の `(construct_id, unit, domain)` を持つ
    baseline 行を組み立てるのに使う）。"""
    return EUseEvidenceRow(
        construct_id=construct_id,
        unit=unit,
        domain=domain,
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


def _full_unjustified_rows() -> list[EUseEvidenceRow]:
    """`registry.ALL_CANDIDATES` が宣言する全 `(construct_id, unit, domain)`
    キーを 1 行ずつ UNJUSTIFIED でカバーする baseline テーブル（第 9 巡採用の
    キー集合完全一致チェックを満たす — 単一行の table はもはや『valid』では
    ない）。"""
    return [
        _unjustified_row_for(construct, unit, domain)
        for construct, unit, domain in e_use_table.unique_construct_unit_domain(
            candidate_registry.ALL_CANDIDATES
        )
    ]


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
    """第 9 巡採用のキー集合完全一致チェックを満たすため、対象行以外は
    `_full_unjustified_rows()` の baseline で埋め、対象キーの行だけ
    `USER_ACCEPTED_USE_BOUND` に差し替える（この行単体の完全な `[]`
    アサーションを保つには registry の全キーを揃える必要がある）。"""
    rows = _full_unjustified_rows()
    target_idx = next(
        i for i, r in enumerate(rows) if r.construct_id == "harmonic_to_noise_ratio" and r.unit == "db"
    )
    accepted_row = EUseEvidenceRow(
        construct_id=rows[target_idx].construct_id,
        unit=rows[target_idx].unit,
        domain=rows[target_idx].domain,
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
    rows[target_idx] = accepted_row

    violations = e_use_table.validate_e_use_table(rows, gate1_e_use_bound_accepted=False)
    assert any("USER_ACCEPTED_USE_BOUND" in v for v in violations)

    violations_ok = e_use_table.validate_e_use_table(rows, gate1_e_use_bound_accepted=True)
    assert violations_ok == []


def test_validate_e_use_table_accepts_clean_unjustified_row() -> None:
    """単一行だけでは（第 9 巡採用のキー集合完全一致チェックにより）もはや
    『valid』にならない — `_full_unjustified_rows()` の完全な baseline で
    アサーションする。単一行版の意図（clean UNJUSTIFIED 行に行単位の違反が
    無いこと）は次のテストで別途カバーする。"""
    rows = _full_unjustified_rows()
    assert e_use_table.validate_e_use_table(rows, gate1_e_use_bound_accepted=False) == []


def test_validate_e_use_table_clean_unjustified_row_has_no_row_level_violation() -> None:
    """単一の clean UNJUSTIFIED 行には行単位の違反が出ない、という元の意図を
    キー集合完全一致チェックと分離して確認する（完全一致由来の
    missing/unexpected 違反が出ることは許容し、それら以外の violation が
    無いことだけを見る）。"""
    row = _unjustified_row()
    violations = e_use_table.validate_e_use_table([row], gate1_e_use_bound_accepted=False)
    assert not any("must have e_use_value=null" in v for v in violations)
    assert not any("USER_ACCEPTED_USE_BOUND" in v for v in violations)


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


# ---------------------------------------------------------------------------
# `e_use_mode` column (`[UNDERSPEC-CAL-D11]`, Part B) — 14 columns now
# ---------------------------------------------------------------------------


def test_columns_include_e_use_mode() -> None:
    assert e_use_table.COLUMNS[-1] == "e_use_mode"
    assert len(e_use_table.COLUMNS) == 14


def test_generate_template_rows_default_to_absolute_mode(tmp_path: Path) -> None:
    path = tmp_path / "t.json"
    rows = e_use_table.generate_template(path, candidate_registry.ALL_CANDIDATES)
    assert all(r.e_use_mode == "absolute" for r in rows)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert all(entry["e_use_mode"] == "absolute" for entry in raw)


def test_row_round_trip_via_dict_preserves_relative_mode() -> None:
    row = EUseEvidenceRow(
        construct_id="formant_frequency",
        unit="hz",
        domain="d",
        intended_use="u",
        maximum_claim="ABSOLUTE",
        e_use_value=0.05,
        derivation_rule="relative: 0.05 x declared truth",
        evidence_class=EvidenceClass.USER_ACCEPTED_USE_BOUND,
        source_id_or_url="s",
        source_checked_at="t",
        source_hash_or_version="v",
        applicability_argument="a",
        review_status="ACCEPTED",
        e_use_mode="relative",
    )
    d = e_use_table.row_to_dict(row)
    assert d["e_use_mode"] == "relative"
    rebuilt = e_use_table.row_from_dict(d)
    assert rebuilt == row


def test_row_from_dict_missing_e_use_mode_raises() -> None:
    d = e_use_table.row_to_dict(_unjustified_row())
    del d["e_use_mode"]
    with pytest.raises(KeyError):
        e_use_table.row_from_dict(d)


def test_load_save_round_trip_preserves_e_use_mode(tmp_path: Path) -> None:
    path = tmp_path / "table.json"
    relative_row = EUseEvidenceRow(
        construct_id="fundamental_frequency",
        unit="hz",
        domain="d",
        intended_use="u",
        maximum_claim="ABSOLUTE",
        e_use_value=0.01161,
        derivation_rule="20 cents",
        evidence_class=EvidenceClass.USER_ACCEPTED_USE_BOUND,
        source_id_or_url="s",
        source_checked_at="t",
        source_hash_or_version="v",
        applicability_argument="a",
        review_status="ACCEPTED",
        e_use_mode="relative",
    )
    rows = [_unjustified_row("a"), relative_row]
    e_use_table.save_e_use_table(path, rows)
    loaded = e_use_table.load_e_use_table(path)
    assert loaded == rows
    assert loaded[1].e_use_mode == "relative"


# ---------------------------------------------------------------------------
# 第 9 巡採用 #1 — validate_e_use_table: (construct_id, unit, domain) キー
# 集合が unique_construct_unit_domain(registry.ALL_CANDIDATES) と厳密一致する
# ことを要求する（欠落/余剰/重複をそれぞれ個別の違反として列挙）
# ---------------------------------------------------------------------------


def test_repo_e_use_table_v1_key_set_matches_registry_exactly() -> None:
    """`config/e_use_table_v1.json`（実運用テーブル）は現状のまま registry と
    厳密一致し、キー集合完全一致チェックによる新規違反を一切出さないことを
    確認する（第 9 巡指示: 通らなければ表を修正せず理由を報告する対象。実測は
    通っている）。"""
    repo_root = Path(__file__).resolve().parents[3]
    table_path = repo_root / "voice_genesis" / "calibration" / "config" / "e_use_table_v1.json"
    rows = e_use_table.load_e_use_table(table_path)
    violations = e_use_table.validate_e_use_table(rows, gate1_e_use_bound_accepted=True)
    key_set_violations = [
        v for v in violations if v.startswith(("missing row", "unexpected row", "duplicate row"))
    ]
    assert key_set_violations == [], key_set_violations


def test_validate_e_use_table_flags_one_missing_row() -> None:
    rows = _full_unjustified_rows()
    dropped = rows.pop()  # remove exactly one row -> exactly one missing-key violation
    violations = e_use_table.validate_e_use_table(rows, gate1_e_use_bound_accepted=False)
    missing = [v for v in violations if v.startswith("missing row")]
    assert len(missing) == 1
    assert dropped.construct_id in missing[0]
    assert not any(v.startswith("unexpected row") for v in violations)
    assert not any(v.startswith("duplicate row") for v in violations)


def test_validate_e_use_table_flags_an_extra_row() -> None:
    rows = _full_unjustified_rows()
    # A (construct_id, unit, domain) triple no candidate in the registry declares.
    rows.append(_unjustified_row_for("nonexistent_construct", "nonexistent_unit", "nonexistent_domain"))
    violations = e_use_table.validate_e_use_table(rows, gate1_e_use_bound_accepted=False)
    extra = [v for v in violations if v.startswith("unexpected row")]
    assert len(extra) == 1
    assert "nonexistent_construct" in extra[0]
    assert not any(v.startswith("missing row") for v in violations)
    assert not any(v.startswith("duplicate row") for v in violations)


def test_validate_e_use_table_flags_a_duplicate_row() -> None:
    rows = _full_unjustified_rows()
    rows.append(_unjustified_row_for(rows[0].construct_id, rows[0].unit, rows[0].domain))
    violations = e_use_table.validate_e_use_table(rows, gate1_e_use_bound_accepted=False)
    duplicate = [v for v in violations if v.startswith("duplicate row")]
    assert len(duplicate) == 1
    assert rows[0].construct_id in duplicate[0]
    assert "appears 2 times" in duplicate[0]
    assert not any(v.startswith("missing row") for v in violations)
    assert not any(v.startswith("unexpected row") for v in violations)


def test_validate_e_use_table_key_set_violations_are_independent_of_each_other() -> None:
    """欠落/余剰/重複が同時に発生しても、それぞれ個別の violation として
    列挙される（互いに隠蔽しない）ことを確認する。"""
    rows = _full_unjustified_rows()
    rows.pop()  # missing
    rows.append(_unjustified_row_for("x", "y", "z"))  # unexpected
    rows.append(_unjustified_row_for(rows[0].construct_id, rows[0].unit, rows[0].domain))  # duplicate
    violations = e_use_table.validate_e_use_table(rows, gate1_e_use_bound_accepted=False)
    assert any(v.startswith("missing row") for v in violations)
    assert any(v.startswith("unexpected row") for v in violations)
    assert any(v.startswith("duplicate row") for v in violations)


# ---------------------------------------------------------------------------
# round 20 採用 (1) (`[UNDERSPEC-CAL-D46]`): USER_ACCEPTED_USE_BOUND rows'
# `source_hash_or_version` must actually match the cited source file's
# sha256 — mechanical restamp CLI + fail-closed freeze-time validation.
# ---------------------------------------------------------------------------


def _delegation_row(
    *, construct_id: str = "fundamental_frequency", source_hash_or_version: str
) -> EUseEvidenceRow:
    return EUseEvidenceRow(
        construct_id=construct_id,
        unit="hz",
        domain="d",
        intended_use="u",
        maximum_claim="ABSOLUTE",
        e_use_value=0.01,
        derivation_rule="r",
        evidence_class=EvidenceClass.USER_ACCEPTED_USE_BOUND,
        source_id_or_url=f"{e_use_table.GATE1_DELEGATION_SOURCE_ID_PREFIX}2026-09-02 (test)",
        source_checked_at="t",
        source_hash_or_version=source_hash_or_version,
        applicability_argument="a",
        review_status="APPROVED_BY_DELEGATION",
    )


def test_validate_source_digests_ignores_rows_without_gate1_delegation_prefix() -> None:
    """`UNJUSTIFIED`/その他の行（`GATE1_DELEGATION_SOURCE_ID_PREFIX` を持た
    ない）は対象外——出典ファイルを一切読まず、常に無違反。"""
    rows = [_unjustified_row(), _unjustified_row_for("x", "hz", "d")]
    assert e_use_table.validate_source_digests(rows, repo_root=Path("/nonexistent")) == []


def test_validate_source_digests_flags_mismatch(tmp_path: Path) -> None:
    source_dir = tmp_path / "voice_genesis" / "calibration" / "approvals" / "records"
    source_dir.mkdir(parents=True)
    source_path = source_dir / "GATE1_DECISION_RECORD.md"
    source_path.write_text("decision record content v1\n", encoding="utf-8")

    rows = [_delegation_row(source_hash_or_version="0" * 64)]
    violations = e_use_table.validate_source_digests(rows, repo_root=tmp_path)
    assert len(violations) == 1
    assert violations[0].startswith("E_USE_SOURCE_DIGEST_MISMATCH")
    assert "row[0]" in violations[0]
    assert "fundamental_frequency" in violations[0]


def test_validate_source_digests_passes_when_hash_matches(tmp_path: Path) -> None:
    source_dir = tmp_path / "voice_genesis" / "calibration" / "approvals" / "records"
    source_dir.mkdir(parents=True)
    source_path = source_dir / "GATE1_DECISION_RECORD.md"
    source_path.write_text("decision record content v1\n", encoding="utf-8")
    actual_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()

    rows = [_delegation_row(source_hash_or_version=actual_sha256)]
    assert e_use_table.validate_source_digests(rows, repo_root=tmp_path) == []


def test_validate_source_digests_missing_source_file_is_one_violation_naming_all_rows(
    tmp_path: Path,
) -> None:
    rows = [
        _delegation_row(construct_id="fundamental_frequency", source_hash_or_version="a" * 64),
        _delegation_row(construct_id="formant_frequency", source_hash_or_version="b" * 64),
    ]
    violations = e_use_table.validate_source_digests(rows, repo_root=tmp_path)
    assert len(violations) == 1
    assert violations[0].startswith("E_USE_SOURCE_DIGEST_MISMATCH")
    assert "fundamental_frequency" in violations[0]
    assert "formant_frequency" in violations[0]


def test_restamp_source_digests_rewrites_stale_rows_and_leaves_others_untouched(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "voice_genesis" / "calibration" / "approvals" / "records"
    source_dir.mkdir(parents=True)
    source_path = source_dir / "GATE1_DECISION_RECORD.md"
    source_path.write_text("decision record content v1\n", encoding="utf-8")
    actual_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()

    table_path = tmp_path / "e_use_table.json"
    stale_row = _delegation_row(construct_id="fundamental_frequency", source_hash_or_version="0" * 64)
    unaffected_row = _unjustified_row_for("formant_centroid", "hz", "d")
    e_use_table.save_e_use_table(table_path, [stale_row, unaffected_row])

    changed, new_sha256 = e_use_table.restamp_source_digests(table_path, repo_root=tmp_path)
    assert changed == 1
    assert new_sha256 == actual_sha256

    restamped_rows = e_use_table.load_e_use_table(table_path)
    by_construct = {r.construct_id: r for r in restamped_rows}
    assert by_construct["fundamental_frequency"].source_hash_or_version == actual_sha256
    # every other column on the restamped row is untouched.
    assert by_construct["fundamental_frequency"].source_id_or_url == stale_row.source_id_or_url
    assert by_construct["fundamental_frequency"].e_use_value == stale_row.e_use_value
    # the non-delegation row is byte-for-byte untouched.
    assert by_construct["formant_centroid"] == unaffected_row

    # source file itself was never written to (restamp must not edit the
    # decision record — only the table cites it).
    assert source_path.read_text(encoding="utf-8") == "decision record content v1\n"


def test_restamp_then_validate_source_digests_closes_the_loop(tmp_path: Path) -> None:
    """round 20 ADOPT(1) acceptance test: mismatch -> BLOCKED; restamp ->
    validate passes."""
    source_dir = tmp_path / "voice_genesis" / "calibration" / "approvals" / "records"
    source_dir.mkdir(parents=True)
    source_path = source_dir / "GATE1_DECISION_RECORD.md"
    source_path.write_text("decision record content v1\n", encoding="utf-8")

    table_path = tmp_path / "e_use_table.json"
    stale_row = _delegation_row(source_hash_or_version="dead" * 16)
    e_use_table.save_e_use_table(table_path, [stale_row])

    rows_before = e_use_table.load_e_use_table(table_path)
    violations_before = e_use_table.validate_source_digests(rows_before, repo_root=tmp_path)
    assert any(v.startswith("E_USE_SOURCE_DIGEST_MISMATCH") for v in violations_before)

    e_use_table.restamp_source_digests(table_path, repo_root=tmp_path)

    rows_after = e_use_table.load_e_use_table(table_path)
    violations_after = e_use_table.validate_source_digests(rows_after, repo_root=tmp_path)
    assert violations_after == []


def test_repo_e_use_table_v1_source_digests_match_gate1_decision_record_at_head() -> None:
    """`config/e_use_table_v1.json`（実運用テーブル）の全 USER_ACCEPTED_USE_BOUND
    行が、現在の `GATE1_DECISION_RECORD.md` の sha256 と一致することを確認する
    （round 20 ADOPT(1)(a) の再刻印が実際に適用済みであることの regression
    guard）。"""
    repo_root = Path(__file__).resolve().parents[3]
    table_path = repo_root / e_use_table.DEFAULT_E_USE_TABLE_RELATIVE_PATH
    rows = e_use_table.load_e_use_table(table_path)
    assert e_use_table.validate_source_digests(rows, repo_root=repo_root) == []
    matching = [
        r
        for r in rows
        if r.source_id_or_url.startswith(e_use_table.GATE1_DELEGATION_SOURCE_ID_PREFIX)
    ]
    assert len(matching) == 9  # all USER_ACCEPTED_USE_BOUND rows currently in the table
