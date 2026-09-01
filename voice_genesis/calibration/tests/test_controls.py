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
