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
  `positive_detection_instances()` で instance 数として数える。
- **positive 検出証拠 = 評価対象 split 内の当該 family の全 truth-core 行
  （×5 repeats）**（Codex レビュー 2026-09-01 第 8 巡: DESIGN RULING、
  designated-2-anchor 方式を置換）。旧方式は `positive_control=True` の
  designated 2 行のみを instance 母集団としており、この 2 行の home split
  は HMAC 由来で片方にしか属さないことが構造的にあり得るため、selection と
  holdout の両方で `N_pos>=10` を同時に満たせない場合があった（2 行が同一
  split に home split を持てば一方が `N_pos=10`・他方は `N_pos=0`）。family
  の truth core 行数は最小でも 12 件（F0_CONTROL）あり、50/25/25 split の下で
  selection/holdout 各側に最低でも数件が入るため、truth core 行全体を母集団に
  拡張すれば両 split で `N_pos>=10` を安定して充足できる。`positive_control`
  フラグ自体（designated anchor の row metadata）は監査用途にそのまま残すが、
  instance 数の計算からは外す。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from enum import Enum

from voice_genesis.calibration.fixtures.matrix import MatrixRow
from voice_genesis.calibration.vocab import Domain

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
    """§2.7 第 8 巡改訂の positive 証拠の数え方（DESIGN RULING, FROZEN）:
    leakage 除外を経由せず、`split`（`splitter.RealizedSplitMap.assignment`
    が返す `vocab.Split` 値。selection 段階なら `Split.SELECTION`、
    holdout gate 5 なら unseal 後の `Split.HOLDOUT`）内にある **当該 family の
    全 truth-core 行**（`block == "TRUTH_CORE"`。designated 2-anchor
    (`positive_control=True`) への限定は撤廃）の `(row_id, probe_index)`
    instance 集合を返す。

    旧方式（designated 2-anchor のみ）は、2 行の home split が HMAC 由来で
    片方の split にしか属さない場合があり、selection と holdout の両方で
    `N_pos>=10` を同時に満たせない構造的欠陥があった（Codex レビュー
    2026-09-01 P1）。family の truth core 行数は最小でも 12 件あり、50/25/25
    split の下で各 split に複数件が入るため、truth core 行全体を母集団に
    拡張すれば `truth 行数 × PROBE_REPEATS(=5)` で selection/holdout 両方で
    `N_pos>=10` を安定して充足する。`family` を省略すると全 family 分を
    まとめて返す。
    """
    out: set[tuple[str, int]] = set()
    for mr in rows:
        if mr.row.block != "TRUTH_CORE":
            continue
        if family is not None and mr.row.family != family:
            continue
        if assignment.get(mr.row_id) != split:
            continue
        out.update((mr.row_id, probe_index) for probe_index in range(PROBE_REPEATS))
    return frozenset(out)


def non_boundary_selection_instances(
    rows: Iterable[MatrixRow],
    assignment: Mapping[str, object],
    split: object,
    *,
    family: str | None = None,
) -> frozenset[tuple[str, int]]:
    """round 30 self-review ADOPT (1) (`[UNDERSPEC-CAL-D68]`): `positive_
    detection_instances()` の "評価対象 split 内の当該 family の全 truth-core
    行" という population を、`block == "TRUTH_CORE"` 限定から **`domain ==
    Domain.PRIMARY`**（`fixtures.matrix.compute_domain()` が D2 boundary-axis
    混入検査 + §3.3 F0 帯域整合検査のいずれにも該当しない行に付与する tag）
    へ拡張したもの——`row.block` の語彙で言えば `TRUTH_CORE` ∪ `CONFOUND`
    （非 BOUNDARY の全行）を返す。`compute_domain()` は `block == "BOUNDARY"`
    の行だけでなく `row.control_class is not None`（negative control 行）に
    も `Domain.BOUNDARY` を付与するため、この関数は特別扱いなしに negative
    control 行を自動的に除外する（negative control の coverage 判定は既存の
    `negative_control_row_ids`/`negative_controls_incomplete` filter が別途
    担う——ここへ合流させない）。

    採用理由（selection stage 側 self-review round 30 MAJOR finding #1）:
    `positive_detection_instances()` ベースの `coverage_incomplete`（D64）は
    TRUTH_CORE 行のみを母集団としており、hard CONFOUND 行（`block ==
    "CONFOUND"`、114 行）で一貫して `OUTPUT_MISSING` を返す candidate は
    coverage 判定の対象外のまま、縮小された instance 母集団の上で
    MAE/bias/q95 が計算され、`missing_failure_rate` による比較（lexicographic
    順位の末尾側）より **先に** primary_normalized_mae で正直に測定した
    candidate に勝ち得た——D64 が閉じたはずの「縮小母集団で勝つ」fail-open
    を、D64 が対象にしなかった CONFOUND 母集団の上で再現する経路だった。
    BOUNDARY-domain 行は §1 D2「domain 外は自動外挿せず NOT_EVALUABLE」の
    設計判断どおり本 filter の対象外のままとする（missing はそこでは
    design-sanctioned であり、`missing_failure_rate` にのみ反映されればよい）。
    """
    out: set[tuple[str, int]] = set()
    for mr in rows:
        if mr.domain != Domain.PRIMARY:
            continue
        if family is not None and mr.row.family != family:
            continue
        if assignment.get(mr.row_id) != split:
            continue
        out.update((mr.row_id, probe_index) for probe_index in range(PROBE_REPEATS))
    return frozenset(out)


def negative_control_instances(
    rows: Iterable[MatrixRow],
    *,
    family: str | None = None,
) -> frozenset[tuple[str, int]]:
    """round 17 finding #1（採用）: §2.7 control 共有契約「negative control 行は
    ... 全段階（selection の共通 fail filter / holdout gate 5）で評価可とする」の
    「全段階」には C3a/C3b の測定対象 instance 集合そのものが含まれる——これらの行は
    C1 で「全 control」としてすでに render 済みである（`workunits.
    enumerate_c1_render_units`）。`assignment`/`split` に依らず（HMAC home split が
    CALIBRATION/HOLDOUT の行も含め）、`family`（省略時は全 family）の負の control 行
    全件の `(row_id, probe_index)` instance 集合を返す。`positive_detection_instances()`
    が正 control（truth-core 行）に対して行う「評価対象 split 内の全 truth-core 行」
    という拡張の、負 control 側の対応物（ただし split 制約自体を持たない点が異なる —
    負 control は sweep truth を運ばないため、どの split に home していようと C3 の
    fail filter 母集団としては常に含める）。"""
    out: set[tuple[str, int]] = set()
    for mr in rows:
        if mr.row.control_class is None:
            continue
        if family is not None and mr.row.family != family:
            continue
        out.update((mr.row_id, p) for p in range(PROBE_REPEATS))
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
