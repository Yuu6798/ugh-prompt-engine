"""fixtures/controls.py テスト（IMPLEMENTATION_MAP §2.7「control 共有契約」。
Codex レビュー 2026-09-01 P1: positive control 行を leakage 除外集合から
除いたことの回帰防止 + 新設 `positive_detection_instances()` の実データ検証。
"""

from __future__ import annotations

import ast
from pathlib import Path

from voice_genesis.calibration import splitter
from voice_genesis.calibration.candidates.adapter import MeterOutput
from voice_genesis.calibration.fixtures import controls, matrix
from voice_genesis.calibration.vocab import Domain, MissingReason, Split

_DUMMY_SPLIT_SECRET = b"dummy-secret-for-controls-test-only"


def _real_matrix_rows() -> list:
    return matrix.build_matrix()


def _realize_dummy_split(rows) -> splitter.RealizedSplitMap:
    row_inputs = [
        splitter.RowInput(row_id=mr.row_id, family=mr.row.family, stratum={}) for mr in rows
    ]
    return splitter.realize_split(row_inputs, _DUMMY_SPLIT_SECRET, [])


# ---------------------------------------------------------------------------
# leakage 除外集合 (`control_row_ids`) は negative control 行のみ
# ---------------------------------------------------------------------------


def test_control_row_ids_excludes_positive_control_truth_rows() -> None:
    """[Codex レビュー 2026-09-01 P1] positive control は truth core 行であり、
    holdout 側の positive control を leakage 除外に含めると unseal 前に seal
    を破ってしまう。leakage 除外集合は negative control 行のみで構成される
    こと（= truth core 行を一切含まないこと）を確認する。
    """
    rows = _real_matrix_rows()
    exempt = controls.control_row_ids(rows)
    positive_ids = controls.positive_control_row_ids(rows)

    assert exempt == controls.negative_control_row_ids(rows)
    assert exempt.isdisjoint(positive_ids)

    truth_core_ids = {mr.row_id for mr in rows if mr.row.block == "TRUTH_CORE"}
    assert exempt.isdisjoint(truth_core_ids), (
        "leakage exemption set must not contain any TRUTH_CORE row "
        "(positive controls are TRUTH_CORE rows)"
    )


def test_control_row_ids_still_covers_all_negative_controls() -> None:
    rows = _real_matrix_rows()
    exempt = controls.control_row_ids(rows)
    for mr in rows:
        if mr.row.control_class is not None:
            assert mr.row_id in exempt


# ---------------------------------------------------------------------------
# positive_detection_instances: leakage 除外を経由しない positive 証拠の数え方
# (Codex レビュー 2026-09-01 第 8 巡 DESIGN RULING: 母集団 = 評価対象 split 内の
# 当該 family の全 truth-core 行。designated 2-anchor 方式は撤廃)
# ---------------------------------------------------------------------------


def test_positive_detection_instances_only_counts_home_split_and_truth_core_rows() -> None:
    rows = _real_matrix_rows()
    realized = _realize_dummy_split(rows)

    instances = controls.positive_detection_instances(
        rows, realized.assignment, Split.SELECTION, family="IDENTITY_CAUSAL_SWEEP"
    )
    row_ids_in_instances = {row_id for row_id, _probe_index in instances}
    for row_id in row_ids_in_instances:
        assert realized.assignment[row_id] == Split.SELECTION

    truth_core_ids_for_family = {
        mr.row_id
        for mr in rows
        if mr.row.block == "TRUTH_CORE" and mr.row.family == "IDENTITY_CAUSAL_SWEEP"
    }
    assert row_ids_in_instances.issubset(truth_core_ids_for_family)
    # designated anchor への限定は撤廃されたため、truth core 行のうち
    # positive_control=True でない行も母集団に含まれうる。
    assert row_ids_in_instances == {
        row_id
        for row_id in truth_core_ids_for_family
        if realized.assignment[row_id] == Split.SELECTION
    }

    probe_indices = {probe_index for _row_id, probe_index in instances}
    assert probe_indices == set(range(controls.PROBE_REPEATS))


def test_positive_detection_instances_reaches_min_n_pos_for_every_family_both_splits() -> None:
    """[Codex レビュー 2026-09-01 P1 / 第 8 巡] regression: 旧 designated-2-anchor
    方式では、2 行の home split が同じ split に偏ると、もう一方の split では
    `N_pos=0` となり selection と holdout の両方で `N_pos>=10` を同時に
    満たせなかった。truth-core 行全体を母集団に拡張した新方式では、実データ
    (実 matrix + dummy split secret) の下で全 family・selection/holdout 両方
    ともに `N_pos>=10` を満たすことを確認する。
    """
    rows = _real_matrix_rows()
    realized = _realize_dummy_split(rows)
    families = sorted({mr.row.family for mr in rows})

    for family in families:
        for split in (Split.SELECTION, Split.HOLDOUT):
            instances = controls.positive_detection_instances(
                rows, realized.assignment, split, family=family
            )
            assert len(instances) >= 10, (family, split)


def test_positive_detection_instances_reaches_min_n_pos_for_every_family_both_splits_real_matrix_default_secret() -> None:  # noqa: E501
    """`_realize_dummy_split` のダミー secret に加え、`splitter.realize_split`
    のデフォルト stratum 因子（空リスト）でも同様に成立することを確認する
    （dummy secret 依存の偶然一致ではないことの追加確認）。
    """
    rows = _real_matrix_rows()
    row_inputs = [
        splitter.RowInput(row_id=mr.row_id, family=mr.row.family, stratum={}) for mr in rows
    ]
    realized = splitter.realize_split(row_inputs, b"another-dummy-secret-for-controls-test", [])
    families = sorted({mr.row.family for mr in rows})

    for family in families:
        for split in (Split.SELECTION, Split.HOLDOUT):
            instances = controls.positive_detection_instances(
                rows, realized.assignment, split, family=family
            )
            assert len(instances) >= 10, (family, split)


# ---------------------------------------------------------------------------
# round 30 self-review ADOPT (1) (`[UNDERSPEC-CAL-D68]`): `non_boundary_
# selection_instances` widens `positive_detection_instances`'s TRUTH_CORE
# -only population to TRUTH_CORE + CONFOUND (all `domain == Domain.PRIMARY`
# rows), so `campaign.selection_stage.candidate_fail_filter_report()`'s
# `coverage_incomplete` filter can catch a candidate that consistently
# returns `OUTPUT_MISSING` on every hard CONFOUND row of a family (a
# population D64's TRUTH_CORE-only check did not cover).
# ---------------------------------------------------------------------------


def test_non_boundary_selection_instances_excludes_boundary_and_negative_control_rows() -> None:
    rows = _real_matrix_rows()
    realized = _realize_dummy_split(rows)
    row_by_id = {mr.row_id: mr for mr in rows}
    families = sorted({mr.row.family for mr in rows})

    for family in families:
        instances = controls.non_boundary_selection_instances(
            rows, realized.assignment, Split.SELECTION, family=family
        )
        row_ids_in_instances = {row_id for row_id, _probe_index in instances}
        for row_id in row_ids_in_instances:
            mr = row_by_id[row_id]
            assert realized.assignment[row_id] == Split.SELECTION
            assert mr.row.family == family
            assert mr.domain is Domain.PRIMARY
            assert mr.row.block in ("TRUTH_CORE", "CONFOUND")
            assert mr.row.control_class is None


def test_non_boundary_selection_instances_is_superset_of_positive_detection_instances() -> None:
    """TRUTH_CORE ⊆ non-BOUNDARY, so widening the population must never drop
    an instance `positive_detection_instances()` already counted."""
    rows = _real_matrix_rows()
    realized = _realize_dummy_split(rows)
    families = sorted({mr.row.family for mr in rows})

    for family in families:
        for split in (Split.SELECTION, Split.HOLDOUT):
            truth_core_only = controls.positive_detection_instances(
                rows, realized.assignment, split, family=family
            )
            non_boundary = controls.non_boundary_selection_instances(
                rows, realized.assignment, split, family=family
            )
            assert truth_core_only <= non_boundary, (family, split)


def test_non_boundary_selection_instances_includes_confound_rows_somewhere() -> None:
    """Regression guard for the self-review round finding this closes:
    confirm the widened population actually reaches CONFOUND-block rows (not
    just a no-op that happens to equal the TRUTH_CORE-only set for every
    family/split under the dummy split secret)."""
    rows = _real_matrix_rows()
    realized = _realize_dummy_split(rows)
    families = sorted({mr.row.family for mr in rows})
    row_by_id = {mr.row_id: mr for mr in rows}

    confound_row_ids_seen: set[str] = set()
    for family in families:
        instances = controls.non_boundary_selection_instances(
            rows, realized.assignment, Split.SELECTION, family=family
        )
        for row_id, _probe_index in instances:
            if row_by_id[row_id].row.block == "CONFOUND":
                confound_row_ids_seen.add(row_id)
    assert confound_row_ids_seen, (
        "expected at least one CONFOUND row assigned to SELECTION split across all "
        "families under the dummy split secret"
    )


# ---------------------------------------------------------------------------
# RUN10-CAL-v1.2 WP1: `detected()` — the single fire-判定 that
# `campaign.selection_stage`/`campaign.holdout_stage` both now call (the two
# modules previously diverged: `selection_stage._detected()` did not require
# `values` to be non-empty, `holdout_stage._detected_output()` — the R20-2
# strict version — did). Default (`predicate=None`) reproduces the R20-2
# strict version exactly; a `DetectionPredicate` switches to a field-specific
# threshold test for candidates that declare one via `registry.Candidate.
# detection_predicate`.
# ---------------------------------------------------------------------------


def test_detected_default_missing_reason_is_false() -> None:
    output = MeterOutput(values={"f0_hz": 220.0}, missing_reason=MissingReason.OUTPUT_MISSING)
    assert controls.detected(output) is False


def test_detected_default_ineligible_is_false() -> None:
    output = MeterOutput(values={"f0_hz": 220.0}, ineligible=True, ineligible_reason="no dep")
    assert controls.detected(output) is False


def test_detected_default_empty_values_is_false() -> None:
    """round 20 finding #2's regression target: `missing_reason=None,
    ineligible=False, values={}` (a candidate that ran cleanly and found
    nothing) must not be misread as a detection."""
    output = MeterOutput(values={})
    assert controls.detected(output) is False


def test_detected_default_nonfinite_value_is_false() -> None:
    output = MeterOutput(values={"f0_hz": float("nan")})
    assert controls.detected(output) is False


def test_detected_default_finite_nonempty_values_is_true() -> None:
    output = MeterOutput(values={"f0_hz": 220.0})
    assert controls.detected(output) is True


def test_detected_with_predicate_field_present_above_threshold_is_true() -> None:
    predicate = controls.DetectionPredicate(field="energy_db", min_value=6.0)
    output = MeterOutput(values={"energy_db": 12.0})
    assert controls.detected(output, predicate) is True


def test_detected_with_predicate_field_present_below_threshold_is_false() -> None:
    predicate = controls.DetectionPredicate(field="energy_db", min_value=6.0)
    output = MeterOutput(values={"energy_db": 3.0})
    assert controls.detected(output, predicate) is False


def test_detected_with_predicate_field_missing_is_false() -> None:
    predicate = controls.DetectionPredicate(field="energy_db", min_value=6.0)
    output = MeterOutput(values={"other_field": 100.0})
    assert controls.detected(output, predicate) is False


def test_detected_with_predicate_missing_reason_is_false() -> None:
    """the `missing_reason`/`ineligible` gate applies uniformly regardless of
    whether a predicate is supplied."""
    predicate = controls.DetectionPredicate(field="energy_db", min_value=6.0)
    output = MeterOutput(
        values={"energy_db": 12.0}, missing_reason=MissingReason.OUTPUT_MISSING
    )
    assert controls.detected(output, predicate) is False


def test_detected_with_predicate_nonfinite_value_is_false() -> None:
    predicate = controls.DetectionPredicate(field="energy_db", min_value=6.0)
    output = MeterOutput(values={"energy_db": float("inf")})
    assert controls.detected(output, predicate) is False


# ---------------------------------------------------------------------------
# RUN10-CAL-v1.2 WP1: `SANCTIONED_ABSTENTIONS` closed vocabulary.
# ---------------------------------------------------------------------------


def test_sanctioned_abstentions_is_the_single_preregistered_pair() -> None:
    """closed vocabulary — additions are preregistration-only for the next
    revision (module docstring). Locks the current membership so an
    unreviewed addition shows up as a test diff."""
    assert controls.SANCTIONED_ABSTENTIONS == frozenset(
        {(controls.ControlClass.SILENCE, "F0_UNUSABLE")}
    )


# ---------------------------------------------------------------------------
# #349 第 3 巡 ③ P2 (selection_stage.py:640 `noise_only_false_detection_rate`):
# `detected()` の omitted `predicate=` を第 2 巡が取りこぼした穴。同型の穴が
# 他 call site に残っていないかを AST で全数固定し、このファミリーを終端する。
# ---------------------------------------------------------------------------


def _detected_calls_without_predicate_kwarg(source: str) -> list[int]:
    """`source` 中で `detected(...)` を呼び出しているが `predicate=` キーワード
    引数を渡していない行番号（`x.detected()` の属性呼び出しも対象）。
    `def detected(...)` 自体は `ast.Call` に現れないため自動的に除外される。"""
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else None
        )
        if name != "detected":
            continue
        has_predicate_kwarg = any(kw.arg == "predicate" for kw in node.keywords)
        # `detected(output, predicate)` のような位置引数渡しも許容する
        # （`fixtures.controls.detected` の第 2 位置引数は `predicate`）。
        has_predicate_positional = len(node.args) >= 2
        if not (has_predicate_kwarg or has_predicate_positional):
            lines.append(node.lineno)
    return lines


def test_no_detected_call_sites_omit_predicate_outside_tests() -> None:
    """`voice_genesis/calibration` 配下の production コードで `detected(`
    を呼ぶ全 call site が `predicate=`（または第 2 位置引数）で
    `Candidate.detection_predicate` を伝播していることを固定する
    （`fixtures/controls.py` 自身の `def detected(...)`/docstring 中の言及は
    `ast.Call` に現れないため対象外。テストは意図的に既定分岐
    `predicate=None` を検証するため対象外）。第 2 巡が selection_stage.py の
    `noise_only_false_detection_rate` 1 箇所を取りこぼした反省から、この
    全数検査をもって「detected の predicate 伝播ファミリー」を終端する。"""
    calibration_root = Path(__file__).resolve().parents[1]
    repo_root = calibration_root.parents[1]
    offenders: list[str] = []
    for path in sorted(calibration_root.rglob("*.py")):
        rel = path.relative_to(repo_root).as_posix()
        if "/tests/" in f"/{rel}":
            continue
        if path.name == "controls.py" and path.parent.name == "fixtures":
            # `detected()` 自身の定義モジュール — 内部の再帰呼び出しは無い。
            continue
        for lineno in _detected_calls_without_predicate_kwarg(path.read_text(encoding="utf-8")):
            offenders.append(f"{rel}:{lineno}")
    assert not offenders, (
        "detected() call site(s) without predicate= found outside tests — "
        "propagate Candidate.detection_predicate so per-candidate fire "
        f"thresholds are honored everywhere: {offenders}"
    )
