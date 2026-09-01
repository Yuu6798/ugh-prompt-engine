"""control class 定義 + control 共有契約データ（IMPLEMENTATION_MAP §2.7
「control 共有契約」/ 設計正本 §4.2, §10.1）。

- negative control 行は sweep truth を運ばない control class として全段階
  （selection の共通 fail filter / holdout gate 5）で評価可とする。leakage 検査
  の除外集合（`provenance.Ledger.check_leakage` の `control_row_ids`）は
  **negative control 行のみ**を対象とする。
- 対 positive control（§4.2 両側条件）= family anchor の **truth core 行**
  そのもの（`positive_control=True`）。これらは truth-bearing 行であり、
  holdout 側に属していれば holdout seal の対象そのものである（Codex レビュー
  2026-09-01 P1: 従来は positive control 行も leakage 除外集合へ含めており、
  home split が HOLDOUT の positive control が unseal 前に seal を破って観測
  可能になる欠陥があった）。positive control としての証拠は、leakage 除外を
  経由せず、評価対象 split 内に既にある truth 行から
  `positive_detection_instances()` で instance 数として数える
  （selection 段階 = selection split 内の truth 行、holdout gate 5 = unseal
  後の holdout split 内の truth 行。truth 行 × probe repeat 5 で
  `N_pos >= 10` を trivially 充足し、seal を跨がない）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from enum import Enum

from voice_genesis.calibration.fixtures.matrix import MatrixRow

#: §10.1「instance 数 = logical cell × probe repeat」の repeat 数（`tolerance.py`
#: 冒頭 docstring「per-cell n=5 (probe repeat)」と同じ campaign 定数）。
PROBE_REPEATS = 5


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
    truth core 行。2 件 × family = 14 件）。

    **leakage 除外集合には含めない**（Codex レビュー 2026-09-01 P1）。この
    row_id 集合そのものは truth core 行の識別に依然有用（`positive_controls_by_family`
    や監査用途）だが、`control_row_ids()`（leakage 除外集合）へは合流させない。
    positive control としての証拠は `positive_detection_instances()` を使う。
    """
    return frozenset(mr.row_id for mr in rows if mr.row.positive_control)


def control_row_ids(rows: Iterable[MatrixRow]) -> frozenset[str]:
    """leakage 検査 (`provenance.Ledger.check_leakage` の `control_row_ids`)
    の除外集合。**negative control 行のみ**（Codex レビュー 2026-09-01 P1:
    positive control 行は truth-bearing な truth core 行であり、home split が
    HOLDOUT なら holdout seal そのものの対象である。これを leakage 除外に含める
    と unseal 前に positive control の sweep truth を観測できてしまうため、
    positive 行は本集合から除外する。§2.7 改訂: 「sweep truth を運ばない
    control class」という除外根拠が成立するのは negative control のみ）。
    """
    return negative_control_row_ids(rows)


def positive_detection_instances(
    rows: Iterable[MatrixRow],
    assignment: Mapping[str, object],
    split: object,
    *,
    family: str | None = None,
) -> frozenset[tuple[str, int]]:
    """§2.7 改訂の positive 証拠の数え方: leakage 除外を経由せず、`split`
    （`splitter.RealizedSplitMap.assignment` が返す `vocab.Split` 値。
    selection 段階なら `Split.SELECTION`、holdout gate 5 なら unseal 後の
    `Split.HOLDOUT`）内にある positive control **truth 行**の
    `(row_id, probe_index)` instance 集合を返す。

    truth 行 1 件 × `PROBE_REPEATS`(=5) instance であり、family ごとに
    positive control truth 行が 2 件あるため、単一 split 内に home split を
    持つ 1 件のみでも `N_pos=5` だが、通常運用では両 anchor が異なる split に
    home を持つとは限らず、`family` を指定して両 anchor を合算すれば
    `N_pos>=10` を trivially 充足する（IMPLEMENTATION_MAP §2.7）。`family` を
    省略すると全 family 分をまとめて返す。
    """
    out: set[tuple[str, int]] = set()
    for mr in rows:
        if not mr.row.positive_control:
            continue
        if family is not None and mr.row.family != family:
            continue
        if assignment.get(mr.row_id) != split:
            continue
        out.update((mr.row_id, probe_index) for probe_index in range(PROBE_REPEATS))
    return frozenset(out)


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
