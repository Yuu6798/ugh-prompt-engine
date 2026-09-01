"""control class 定義 + control 共有契約データ（IMPLEMENTATION_MAP §2.7
「control 共有契約」/ 設計正本 §4.2, §10.1）。

- negative control 行は sweep truth を運ばない control class として全段階
  （selection の共通 fail filter / holdout gate 5）で評価可とする。
- 対 positive control（§4.2 両側条件）= family anchor の truth core 行 2 件。
- `provenance.Ledger.check_leakage` の `control_row_ids` にそのまま渡せる
  row_id 集合を本モジュールが導出する。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import Enum

from voice_genesis.calibration.fixtures.matrix import MatrixRow


class ControlClass(str, Enum):
    """negative control の閉語彙（§2.7 negative control 系列）。"""

    SILENCE = "SILENCE"
    NOISE_ONLY = "NOISE_ONLY"
    PURE_SINE = "PURE_SINE"
    OUT_OF_BAND_POLE = "OUT_OF_BAND_POLE"
    TOO_SHORT = "TOO_SHORT"
    INVALID_SR = "INVALID_SR"


def negative_control_row_ids(rows: Iterable[MatrixRow]) -> frozenset[str]:
    """`row.control_class is not None` の全行の row_id（family ごとの negative
    control 系列。§2.7 の family 別適用可否は matrix.py の構築時に既に反映済み
    のため、ここでは "control_class が設定されている行" を機械的に集める）。"""
    return frozenset(mr.row_id for mr in rows if mr.row.control_class is not None)


def positive_control_row_ids(rows: Iterable[MatrixRow]) -> frozenset[str]:
    """`row.positive_control` が True の全行の row_id（family anchor の
    truth core 行。2 件 × family = 14 件）。"""
    return frozenset(mr.row_id for mr in rows if mr.row.positive_control)


def control_row_ids(rows: Iterable[MatrixRow]) -> frozenset[str]:
    """negative + positive control の合併集合。§2.7 の契約:
    「sweep truth を運ばない control class」として selection/holdout の共通
    fail filter・gate 5 の評価対象にはなるが、leakage 検査
    (`provenance.Ledger.check_leakage`) の除外集合として扱う。
    """
    rows = list(rows)
    return negative_control_row_ids(rows) | positive_control_row_ids(rows)


def negative_controls_by_class(
    rows: Iterable[MatrixRow],
) -> dict[str, tuple[str, ...]]:
    """control_class 別に row_id をグルーピングする（provenance 記録・監査用）。"""
    out: dict[str, list[str]] = {}
    for mr in rows:
        cc = mr.row.control_class
        if cc is None:
            continue
        out.setdefault(cc, []).append(mr.row_id)
    return {k: tuple(v) for k, v in out.items()}


def positive_controls_by_family(rows: Iterable[MatrixRow]) -> dict[str, tuple[str, ...]]:
    """family 別 positive control row_id（各 family 厳密 2 件であることは
    `tests/test_matrix.py` が enforce する）。"""
    out: dict[str, list[str]] = {}
    for mr in rows:
        if mr.row.positive_control:
            out.setdefault(mr.row.family, []).append(mr.row_id)
    return {k: tuple(v) for k, v in out.items()}


def control_gate_declaration(rows: Sequence[MatrixRow]) -> dict[str, str]:
    """provenance §13 `control_gate` 宣言列（family -> "APPLICABLE" |
    "NOT_APPLICABLE"）。§10.1: binary detection gate を持つ construct は
    `control_gate` を事前宣言する。本キャンペーンは全 family が negative/positive
    control ペアを持つため、全 family `APPLICABLE` を返す。
    """
    return {family: "APPLICABLE" for family in sorted({mr.row.family for mr in rows})}
