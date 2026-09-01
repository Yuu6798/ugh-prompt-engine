"""fixtures/controls.py テスト（IMPLEMENTATION_MAP §2.7「control 共有契約」。
Codex レビュー 2026-09-01 P1: positive control 行を leakage 除外集合から
除いたことの回帰防止 + 新設 `positive_detection_instances()` の実データ検証。
"""

from __future__ import annotations

from voice_genesis.calibration import splitter
from voice_genesis.calibration.fixtures import controls, matrix
from voice_genesis.calibration.vocab import Split

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
# ---------------------------------------------------------------------------


def test_positive_detection_instances_reaches_min_n_pos_on_real_matrix() -> None:
    """§10.1 の最小数 `N_pos>=10` を、leakage 除外を経由せず split 内の
    truth 行 × probe repeat 5 の instance 数だけで trivially 充足できること
    を実データで確認する（dummy split secret による決定論的 split の下、
    IDENTITY_CAUSAL_SWEEP の 2 件の positive control 行がどちらも SELECTION
    split を home split に持つ具体ケース）。
    """
    rows = _real_matrix_rows()
    realized = _realize_dummy_split(rows)

    instances = controls.positive_detection_instances(
        rows, realized.assignment, Split.SELECTION, family="IDENTITY_CAUSAL_SWEEP"
    )
    assert len(instances) >= 10
    # 2 positive control 行 × PROBE_REPEATS(5) = 10 ちょうど（両 anchor が同一
    # split に home split を持つ場合の下限そのもの）。
    assert len(instances) == 2 * controls.PROBE_REPEATS


def test_positive_detection_instances_only_counts_home_split_and_positive_rows() -> None:
    rows = _real_matrix_rows()
    realized = _realize_dummy_split(rows)

    instances = controls.positive_detection_instances(
        rows, realized.assignment, Split.SELECTION, family="IDENTITY_CAUSAL_SWEEP"
    )
    row_ids_in_instances = {row_id for row_id, _probe_index in instances}
    for row_id in row_ids_in_instances:
        assert realized.assignment[row_id] == Split.SELECTION

    positive_ids_for_family = {
        mr.row_id
        for mr in rows
        if mr.row.positive_control and mr.row.family == "IDENTITY_CAUSAL_SWEEP"
    }
    assert row_ids_in_instances.issubset(positive_ids_for_family)

    probe_indices = {probe_index for _row_id, probe_index in instances}
    assert probe_indices == set(range(controls.PROBE_REPEATS))


def test_positive_detection_instances_empty_split_yields_empty_set() -> None:
    """home split を誰も持たない split × family の組では空集合を返す
    （dummy split の実測: TILT_GT の positive control 2 件はどちらも
    SELECTION には割り当てられない）。
    """
    rows = _real_matrix_rows()
    realized = _realize_dummy_split(rows)
    instances = controls.positive_detection_instances(
        rows, realized.assignment, Split.SELECTION, family="TILT_GT"
    )
    assert instances == frozenset()
