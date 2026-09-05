"""C4 holdout stage: gate 判定 + 終端 status cascade
（IMPLEMENTATION_MAP_v1.md §6.4, 設計正本 §10, §11）。

2 層構成:

- **building blocks**（`build_instance_margins`/`build_directional_pairs`/
  `evaluate_absolute_meter`/`evaluate_directional_meter`）は
  `observables.py`/`gates.py`/`status.py` の純関数をそのまま呼ぶ薄いラッパー
  であり、pre-computed な観測値（真値・repeat 生値・E_use・U_GT/U_num）さえ
  与えれば real audio を経由せず単体テストできる（Task Brief「holdout stage
  producing terminal statuses (use synthetic observables where real meters
  are too slow)」）。
- **orchestration**（`render_and_measure_holdout`/`run_holdout_stage`）は
  `render_stage`/`measure_stage` を呼んで実 audio 上で上記 building blocks を
  組み立てる、実キャンペーン実行時の経路。

`HOLDOUT_EXECUTED_VALID` は **全 meter の終端 status + 理由コードをまとめた
単一 ledger event**（`per_meter` mapping）として記帳する（設計正本 §1: 手続
Gate は meter status とは別軸の 1 つの手続完了イベント）。

**meter 被覆検証**（finding #10, 第 10 巡採用）: `run_holdout_stage` は記帳
前に、渡された `results` が `vocab.MeterId` の全 7 値をちょうど 1 回ずつ
含むことを検証する（`per_meter` は `meter_id` キーの dict comprehension で
組み立てるため、検証なしでは重複が黙って上書きされ欠落を検出できない）。
欠落・重複・未知の meter_id はいずれも `HoldoutCoverageError` で
fail-closed する。
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace as dataclass_replace
from pathlib import Path
from typing import Any

from voice_genesis.calibration.campaign import measure_stage, workunits
from voice_genesis.calibration.campaign.render_stage import run_render_stage
from voice_genesis.calibration.campaign.selection_stage import truth_value_for_row
from voice_genesis.calibration.campaign.state import FrozenCampaign
from voice_genesis.calibration.campaign.time_budget import SliceStatus, TimeBudget
from voice_genesis.calibration.candidates.registry import Candidate, candidate_by_id
from voice_genesis.calibration.cost_caps import CapCounters, CostCaps
from voice_genesis.calibration.e_use_table import row_from_dict
from voice_genesis.calibration.e_use_table import find_row as find_e_use_row
from voice_genesis.calibration.fixtures import controls as fixture_controls
from voice_genesis.calibration.fixtures.matrix import FixtureRow, MatrixRow
from voice_genesis.calibration.gates import (
    AbsoluteGateResult,
    DirectionalGateResult,
    DirectionalPair,
    EUseEvidenceRow,
    InstanceMargin,
    InvariancePair,
    absolute_gates,
    directional_gates,
)
from voice_genesis.calibration.observables import (
    detection_rates,
    error_terms,
    nuisance_ds,
    two_stage_median,
    u_proc,
    u_rep,
)
from voice_genesis.calibration.provenance import LedgerEntry
from voice_genesis.calibration.status import terminal_status
from voice_genesis.calibration.vocab import (
    ClaimCeiling,
    Domain,
    MeterId,
    MissingReason,
    Split,
    TerminalStatus,
)


# ---------------------------------------------------------------------------
# frozen fixture_spec からの invariance 軸宣言 [UNDERSPEC-CAL-D18]
# ---------------------------------------------------------------------------

#: `[UNDERSPEC-CAL-D18]`（UNDERSPEC-CAL-D76 により訂正）: 設計正本/
#: `c0_freeze._fixture_specs()` は per-family の「変動しうる軸名」を
#: `frozen_design.fixture_spec.<FAMILY>.confound_axes` として宣言する。
#: 本モジュールはこの `confound_axes` 列を gate4' invariance axis 宣言
#: としてのみ使う（§10.1「truth 自体が変わる軸は invariance 対象に混ぜない」
#: の nuisance 軸 = confound_axes そのものであり、この用途は正しい）。
#: **旧 D18 は同じ列を DIRECTIONAL gate の sweep_id 宣言としても再利用する
#: と記していたが、これは誤りだった**（`sweep_truth_investigation.md`：
#: nuisance 軸で group 化すると truth が anchor 固定になり全 pair
#: `delta_truth == 0`、§10.4 の resolvable pair が構造的に 0 件になる）。
#: DIRECTIONAL sweep 宣言は `frozen_design.fixture_spec.<FAMILY>.
#: declared_sweeps`（`fixtures.matrix.declared_sweeps_by_family()`、def A:
#: truth-core block の nuisance-constant series）という別 key に分離した
#: （`campaign.cli._run_c4` が消費する）。本関数は confound_axes 専用のまま
#: 変更しない。
def declared_axes_for_family(manifest: Mapping[str, object], family: str) -> tuple[str, ...]:
    frozen_design = manifest.get("frozen_design")
    if not isinstance(frozen_design, Mapping):
        return ()
    fixture_spec = frozen_design.get("fixture_spec")
    if not isinstance(fixture_spec, Mapping):
        return ()
    family_spec = fixture_spec.get(family)
    if not isinstance(family_spec, Mapping):
        return ()
    axes = family_spec.get("confound_axes")
    if not isinstance(axes, list):
        return ()
    return tuple(str(a) for a in axes)


# ---------------------------------------------------------------------------
# v1.1 §V3.2 (D17 close): real gate input assembly from campaign measurements
# ---------------------------------------------------------------------------


def declared_u_gt_u_num_for_family(
    manifest: Mapping[str, object], family: str
) -> tuple[float, float] | None:
    """v1.1 §V3.2/§V3.3: ABSOLUTE/DIRECTIONAL 実 gate の `U_GT[i]`/`U_num[i]`
    （設計正本 §10.2「generator truth の保守上限」「PCM 量子化・浮動小数・
    宣言分解能から機械導出」）を campaign 実測から組み立てる唯一の入口。

    **v1.1 §V3.3 実装後（追記, 本 WP）**: `c0_freeze._fixture_specs()` が
    non-ABSENT な 5 family（F0_CONTROL/FORMANT_GT/TILT_GT/APERIODICITY_GT/
    TRANSITION_GT）について `frozen_design.fixture_spec.<FAMILY>.u_gt_bound`/
    `.u_num_bound` を plain number として populate するようになった（値と
    導出式は `.u_gt_bound_formula`/`.u_num_bound_formula`/`*_unit` の
    sibling キーに併記——本関数はこれらを読まず、既存の scalar 契約のみを
    読む）。RESONANCE_GT/IDENTITY_CAUSAL_SWEEP は gate 入力を持たないため
    `"ABSENT:<reason>"` 文字列を凍結する——非 numeric なので本関数は以下と
    同じ理由で黙って `None` を返す。

    本関数はこの `frozen_design.fixture_spec.<FAMILY>` に `u_gt_bound`/
    `u_num_bound`（いずれも non-negative finite な number）という後方互換
    キーを読む——欠落している既存 manifest（閉 campaign 含む。§V3.3 実装
    以前に freeze された campaign 等）を壊さず、値が無い/型不正/負/非有限の
    いずれかなら `None` を返すのみに留める。

    呼び出し側（`build_absolute_gate_inputs`/`build_directional_gate_
    inputs`）はこれを§11「C0 入力側 critical missing → NOT_EVALUABLE/
    INPUT_MISSING」として扱う。テスト fixture（`tests/_campaign_fixture.py`）
    は `c0_freeze.py` を経由せず manifest を直接組み立てるため、このキーを
    注入して gate 全通過シナリオを検証できる。"""
    frozen_design = manifest.get("frozen_design")
    if not isinstance(frozen_design, Mapping):
        return None
    fixture_spec = frozen_design.get("fixture_spec")
    if not isinstance(fixture_spec, Mapping):
        return None
    family_spec = fixture_spec.get(family)
    if not isinstance(family_spec, Mapping):
        return None
    raw_u_gt = family_spec.get("u_gt_bound")
    raw_u_num = family_spec.get("u_num_bound")
    if isinstance(raw_u_gt, bool) or not isinstance(raw_u_gt, (int, float)):
        return None
    if isinstance(raw_u_num, bool) or not isinstance(raw_u_num, (int, float)):
        return None
    u_gt_value = float(raw_u_gt)
    u_num_value = float(raw_u_num)
    if not (math.isfinite(u_gt_value) and u_gt_value >= 0.0):
        return None
    if not (math.isfinite(u_num_value) and u_num_value >= 0.0):
        return None
    return u_gt_value, u_num_value


#: v1.1 R18（Codex レビュー第 18 巡 P1 採用、2026-09-05）: `units_commensurate_
#: for_family()` が使う、候補宣言 unit（`candidates.registry.Candidate.unit`）
#: と C0 凍結 fixture truth unit（`frozen_design.fixture_spec.<FAMILY>.
#: u_gt_bound_unit`）を突き合わせるための表記ゆれ正規化。両者は独立に書かれた
#: 自由記述文字列であり、同じ物理量でも綴りが異なる組がある（例:
#: `M2A-HARMONIC-RESIDUAL-*`/`M2A-D4C-*` 候補の `"fraction"` と
#: `TRUTH_UNIT[APERIODICITY_GT] == "dimensionless_fraction"`）。閉じた
#: 対応表のみを機械的に吸収し、表に無い綴りは正規化しない（結果的に
#: unknown 同士の不一致 = 保守側 False に落ちる）。
_UNIT_SYNONYMS: dict[str, str] = {
    "fraction": "dimensionless_fraction",
}


def _normalize_unit_token(raw: str) -> str:
    """大小文字・前後空白・`/`（`dB/oct` 形式）・`-` の表記ゆれのみを
    正規化した上で `_UNIT_SYNONYMS` の既知同義語表を引く。"""
    token = raw.strip().lower().replace("/", "_per_").replace("-", "_")
    return _UNIT_SYNONYMS.get(token, token)


def units_commensurate_for_family(
    manifest: Mapping[str, object], family: str, candidate_unit: str
) -> bool:
    """v1.1 §V3.2/§10.4 条件 (c) の単位可換性フラグを、凍結 candidate 定義の
    `unit`（`candidates.registry.Candidate.unit`）と C0 凍結 fixture truth
    unit（`frozen_design.fixture_spec.<FAMILY>.u_gt_bound_unit` —
    `c0_freeze._u_gt_bound_for_family()` が truth の物理単位として populate
    する sibling キー。`declared_u_gt_u_num_for_family()` は数値契約のみを
    読み、この unit キーには触れない設計のため、本関数を独立に持つ）から
    機械導出する（R18 対応、Codex レビュー第 18 巡 P1 採用、2026-09-05——
    旧実装は `evaluate_directional_meter_from_campaign()` の
    `units_commensurate` を `_run_c4` が一度も明示的に上書きせず、本番では
    §10.4 条件 (c) が常に無効化されていた）。

    双方の unit 文字列を `_normalize_unit_token()` で正規化した上で厳密一致
    するときのみ `True`。`u_gt_bound_unit` が欠落/空/非 string（ABSENT
    family の `"n/a"` を含む）な場合や、正規化しても一致しない未知の組は
    保守側で `False`（§10.4 条件 (c) を課さない——(a)/(b) の二連言のみで
    resolvability を判定する、既定の安全側）。"""
    frozen_design = manifest.get("frozen_design")
    if not isinstance(frozen_design, Mapping):
        return False
    fixture_spec = frozen_design.get("fixture_spec")
    if not isinstance(fixture_spec, Mapping):
        return False
    family_spec = fixture_spec.get(family)
    if not isinstance(family_spec, Mapping):
        return False
    truth_unit = family_spec.get("u_gt_bound_unit")
    if not isinstance(truth_unit, str) or not truth_unit:
        return False
    return _normalize_unit_token(truth_unit) == _normalize_unit_token(candidate_unit)


def instance_id_str(row_id: str, probe_index: int) -> str:
    """`(row_id, probe_index)` の canonical 文字列 instance id
    （`InstanceMargin.instance_id`/`evaluate_absolute_meter`
    の `expected_primary_instance_ids` が要求する `str` 型への唯一の変換
    入口——campaign 実測ではこの形式以前に確立された正本が無かったため
    本 WP で新規に定める）。"""
    return f"{row_id}#{probe_index}"


def absolute_e_use_value(row: EUseEvidenceRow, truth: float) -> float | None:
    """`gates.EUseEvidenceRow.e_use_mode` に従って instance 単位の絶対
    `E_use[i]` を展開する（`gates.py` の `EUseEvidenceRow` docstring:
    relative 行は `e_use_value * declared_truth` の展開を呼び出し側の責務と
    する）。`row.e_use_value is None`（`UNJUSTIFIED`）、または展開結果が
    有限正でなければ `None`（§10.2 gate3 前提と同じ `> 0` 基準）。符号付き
    construct（例: TILT の負の slope）でも E_use は正の許容誤差量である
    ため `abs(truth)` を使う。"""
    if row.e_use_value is None:
        return None
    value = row.e_use_value * abs(truth) if row.e_use_mode == "relative" else row.e_use_value
    if not (math.isfinite(value) and value > 0.0):
        return None
    return float(value)


class GateInputError(RuntimeError):
    """v1.1 §V3.2: ABSOLUTE/DIRECTIONAL 実 gate の入力組み立て不能
    （E_use 行欠落・U_GT/U_num 未凍結・U_rep/U_proc 計算不能等）。
    呼び出し側は §11「C0 入力側 critical missing → NOT_EVALUABLE/
    INPUT_MISSING」へ写像する。"""


def _detected_output(output: measure_stage.adapter.MeterOutput) -> bool:
    """`campaign.selection_stage._detected()` と同一定義（missing_reason/
    ineligible のいずれでも説明されない finite 出力を「検出した」とみなす）
    を本モジュールから独立に持つ（private helper を跨 module 参照しない
    既存方針）。"""
    return output.missing_reason is None and not output.ineligible


def _per_instance_output_repeats(
    records: Sequence[measure_stage.MeasurementRecord],
    candidate: Candidate,
    row_id: str,
    probe_index: int,
) -> dict[str, list[float]]:
    """`(row_id, probe_index)` の `candidate` own record を `process_id ->
    [output value, ...]` へグルーピングする（`observables.two_stage_median`
    の入力形そのもの）。非有限/欠落値は素通しせず除外する。"""
    per_process: dict[str, list[float]] = {}
    for r in records:
        if r.candidate_id != candidate.candidate_id:
            continue
        if r.row_id != row_id or r.probe_index != probe_index:
            continue
        value = measure_stage.primary_output_value(candidate, r.output)
        if value is None or not math.isfinite(value):
            continue
        per_process.setdefault(r.process_id, []).append(value)
    return per_process


def _sweep_context_fields(row: FixtureRow) -> dict[str, object]:
    """v1.1 §V2.2/§V2.4 の claim-shrinkage 列挙が使う、sweep の held-fixed
    文脈の可読スナップショット（best-effort。C0 は `claim_relevant_fields`
    を family ごとに凍結する規約を予告しているが本 WP 時点では未実装のため、
    汎用の代表的 field のみを機械的に拾う——列挙義務そのもの（v1.1 §V2.4）
    を先に満たし、より精密な `claim_relevant_fields` 消費は将来の改訂に
    委ねる）。"""
    candidates: dict[str, object] = {
        "generator_impl": row.generator_impl,
        "founder_id": row.founder_id,
        "trait": row.trait,
        "join_type": row.join_type,
        "duration_class": row.duration_class,
        "bandwise_band": row.bandwise_band,
        "sr_hz": row.sr_hz,
    }
    return {k: v for k, v in candidates.items() if v is not None}


@dataclass(frozen=True)
class ControlDetection:
    fdr0: float
    fnr1: float
    n_neg: int
    n_pos: int
    min_count_met: bool
    negative_control_failures: int
    positive_control_failures: int


def control_detection_for_family(
    *,
    matrix_rows: Sequence[MatrixRow],
    assignment: Mapping[str, object],
    family: str,
    candidate: Candidate,
    records: Sequence[measure_stage.MeasurementRecord],
) -> ControlDetection:
    """gate5（§10.1 detection、§10.3 gate5）の `FDR0`/`FNR1` を、negative/
    positive control instance の実測から組み立てる。

    - negative control: `fixtures.controls.negative_control_instances()`
      （split に依らず全件——module docstring「sweep truth を運ばない
      control class は全段階で評価可」）。
    - positive control: `fixtures.controls.positive_detection_instances(...,
      Split.HOLDOUT, family=family)`（memo「gate 5 の FDR0/FNR1 = holdout に
      home する control instance」の holdout 版）。

    判定 predicate は positive/negative で非対称（v1.1 §V3.6、Codex レビュー
    第 12 巡 P1 採用、2026-09-05 — 旧実装は両側とも `_all_detected()`（ALL
    repeat が detected の場合のみ「発火」）を共有しており、negative control
    では次の 2 経路で「missing/invalid の分子算入」（v1.0 §10.1）に違反して
    いた: (a) instance の own record が 1 件も無い（`render_and_measure_
    holdout()` が測定を落とした等）場合を `False`＝非発火＝成功と誤って
    写像していた、(b) 複数 repeat のうち一部が実際に発火（`_detected_output`
    True）していても、他の repeat が missing/invalid（`_detected_output`
    False）であれば `all()` が `False` を返し、実在する偽検出を非発火＝
    成功へ隠蔽していた:

    - **positive control**（`_positive_detected()`）: 全 repeat が detected
      の場合のみ「発火（成功）」——1 repeat でも missing/invalid なら
      instance 全体を不発火（失敗）とする（ABSOLUTE gate1 の
      `usable_primary_instances` 全件一致規約と対称）。record が 1 件も
      無い instance は不発火（失敗）。
    - **negative control**（`_negative_fired()`）: **いずれか 1 repeat でも**
      detected（`_detected_output` True。真の偽検出）なら「発火（失敗）」
      （`candidates.adapter.negative_control_false_fire()` と同じ any-fire
      規約）。record が 1 件も無い instance も「発火（失敗）」——missing
      repeat を「非検出＝成功」に丸め込まない。全 repeat が
      missing_reason 付きで一貫して静穏（`_detected_output` False）な
      instance のみが「不発火（成功）」——これは round 30 ADOPT
      (`[UNDERSPEC-CAL-D67]`) が正当と認めた「一貫した非検出」の定義と
      同じ判定基準を再利用する（missing_reason は「候補が正しく無音を
      報告した」ことと「候補が測定に失敗した」ことの両方を同じ値で表現する
      ため、両者は型システム上区別できない——本関数はいずれの場合も
      any-fire が無ければ成功として扱う、v1.0 §8 の既存 any-fire 規約通り）。
    """
    neg_instances = fixture_controls.negative_control_instances(matrix_rows, family=family)
    pos_instances = fixture_controls.positive_detection_instances(
        matrix_rows, assignment, Split.HOLDOUT, family=family
    )
    own_records = [r for r in records if r.candidate_id == candidate.candidate_id]
    by_instance: dict[tuple[str, int], list[measure_stage.MeasurementRecord]] = {}
    for r in own_records:
        by_instance.setdefault((r.row_id, r.probe_index), []).append(r)

    def _positive_detected(instance: tuple[str, int]) -> bool:
        group = by_instance.get(instance)
        if not group:
            return False
        return all(_detected_output(r.output) for r in group)

    def _negative_fired(instance: tuple[str, int]) -> bool:
        group = by_instance.get(instance)
        if not group:
            return True  # missing entirely -> count as failure (v1.1 §V3.6)
        return any(_detected_output(r.output) for r in group)

    neg_outcomes = {
        instance_id_str(row_id, probe_index): _negative_fired((row_id, probe_index))
        for row_id, probe_index in neg_instances
    }
    pos_outcomes = {
        instance_id_str(row_id, probe_index): _positive_detected((row_id, probe_index))
        for row_id, probe_index in pos_instances
    }
    result = detection_rates(neg_outcomes, pos_outcomes, control_gate="APPLICABLE")
    return ControlDetection(
        fdr0=result.fdr0,
        fnr1=result.fnr1,
        n_neg=result.n_neg,
        n_pos=result.n_pos,
        min_count_met=result.min_count_met,
        negative_control_failures=sum(1 for v in neg_outcomes.values() if v),
        positive_control_failures=sum(1 for v in pos_outcomes.values() if not v),
    )


def build_invariance_pairs_for_family(
    *,
    matrix_rows: Sequence[MatrixRow],
    assignment: Mapping[str, object],
    family: str,
    candidate: Candidate,
    records: Sequence[measure_stage.MeasurementRecord],
    declared_axes: Collection[str],
    e_use_row: EUseEvidenceRow,
) -> dict[str, tuple[InvariancePair, ...]]:
    """gate4' の invariance pair を、CONFOUND 行の `nuisance_tag`
    （`fixtures.matrix._build_confound_block`: 1本目 anchor からの変動は
    `f"{axis}={value!r}"`、2本目 anchor からの変動は `f"A2:{axis}={value!r}"`）
    から機械的に組み立てる。各 varied 行は、**同じ truth**
    （`selection_stage.truth_value_for_row` — nuisance 行は truth を anchor
    に固定したまま 1 軸だけ変動させる、という matrix.py の構築規約そのもの）
    を持つ family の designated anchor（`fixtures.controls.
    positive_controls_by_family()`。2 件中 1 本目/2 本目を `nuisance_tag` の
    `A2:` 接頭辞で判別）とペアにする——`observables.nuisance_ds` の
    「anchor error / varied error」そのもの。

    v1.1 §V3.5 追補（Codex レビュー第 15 巡 P1 採用、2026-09-05）: **anchor
    行は split 非依存の共有 control として扱う**（negative control と同型
    — anchor は TRUTH_CORE の positive control 行であり、home split が
    CALIBRATION/SELECTION なら C1 で、HOLDOUT なら C4 で、いずれも既に
    render・測定済み: `render_and_measure_holdout()` が
    `fixtures.controls.positive_controls_by_family()` の全 probe instance
    を per-family 測定対象へ union している）。旧実装は varied（CONFOUND）
    行・anchor 行の**両方**が `Split.HOLDOUT` に home することを要求して
    おり、HMAC split が変異行を HOLDOUT・対応する anchor を CALIBRATION/
    SELECTION へ割り当てた場合（構造的に起こりうる——anchor の split 割当は
    varied 行の割当と独立）、anchor 側の own record が存在せず pair が
    黙って全滅していた（gate4' 自体は `<5 pairs` で正直に FAIL するため
    「バグとして気づかれにくい静かな失敗」だった）。本追補は **varied 行
    のみ** `Split.HOLDOUT` を要求し、anchor 行は home split を問わず
    （measurement さえ存在すれば）ペアの対象とする——seal/leakage 境界は
    動かさない（HOLDOUT 行を unseal 前に露出させるわけではなく、C1 で
    render 済みの anchor 行を読むだけ）。usable な出力を持つ `probe_index`
    のみを 1 `InvariancePair` とする（それ以外は黙ってスキップする——
    gate4' 自体が `<5 pairs` を FAIL として検出するため、母集団を人為的に
    水増ししない）。
    """
    positive_by_family = fixture_controls.positive_controls_by_family(matrix_rows)
    anchor_row_ids = positive_by_family.get(family, ())
    if not anchor_row_ids or not declared_axes:
        return {axis: () for axis in declared_axes}

    row_by_id = {mr.row_id: mr.row for mr in matrix_rows}
    anchors_by_truth: dict[tuple[str, float], str] = {}
    for i, anchor_row_id in enumerate(anchor_row_ids):
        row = row_by_id.get(anchor_row_id)
        if row is None:
            continue
        truth = truth_value_for_row(row)
        if truth is None:
            continue
        prefix = "A2:" if i == 1 else ""
        anchors_by_truth[(prefix, truth)] = anchor_row_id

    pairs_by_axis: dict[str, list[InvariancePair]] = {axis: [] for axis in declared_axes}
    for mr in matrix_rows:
        row = mr.row
        if row.family != family or row.block != "CONFOUND" or row.nuisance_tag is None:
            continue
        tag = row.nuisance_tag
        prefix = "A2:" if tag.startswith("A2:") else ""
        bare_tag = tag[len("A2:"):] if prefix else tag
        if "=" not in bare_tag:
            continue
        axis = bare_tag.split("=", 1)[0]
        if axis not in declared_axes:
            continue
        truth = truth_value_for_row(row)
        if truth is None:
            continue
        anchor_row_id = anchors_by_truth.get((prefix, truth))
        if anchor_row_id is None:
            continue
        # v1.1 §V3.5 追補: only the varied (CONFOUND) row must home to
        # HOLDOUT — the anchor is a split-independent shared control (see
        # docstring above), so its own home split is irrelevant here.
        if assignment.get(mr.row_id) != Split.HOLDOUT:
            continue
        e_use_value = absolute_e_use_value(e_use_row, truth)
        if e_use_value is None:
            continue
        for probe_index in range(fixture_controls.PROBE_REPEATS):
            varied_repeats = _per_instance_output_repeats(records, candidate, mr.row_id, probe_index)
            anchor_repeats = _per_instance_output_repeats(records, candidate, anchor_row_id, probe_index)
            if not varied_repeats or not anchor_repeats:
                continue
            varied_e = error_terms(two_stage_median(varied_repeats), truth, 1e-9).e
            anchor_e = error_terms(two_stage_median(anchor_repeats), truth, 1e-9).e
            pairs_by_axis[axis].append(
                InvariancePair(
                    pair_id=f"{axis}:{mr.row_id}:{probe_index}",
                    axis=axis,
                    ds=nuisance_ds(anchor_e, varied_e),
                    e_use_i0=e_use_value,
                    e_use_ia=e_use_value,
                )
            )
    return {axis: tuple(pairs) for axis, pairs in pairs_by_axis.items()}


@dataclass(frozen=True)
class AbsoluteGateInputBundle:
    margins: tuple[InstanceMargin, ...]
    invariance_pairs_by_axis: Mapping[str, tuple[InvariancePair, ...]]
    declared_invariance_axes: tuple[str, ...]
    u_rep: float
    u_proc: float
    fdr0: float
    fnr1: float
    min_count_met: bool


def build_absolute_gate_inputs(
    *,
    manifest: Mapping[str, object],
    family: str,
    candidate: Candidate,
    row_by_id: Mapping[str, FixtureRow],
    matrix_rows: Sequence[MatrixRow],
    assignment: Mapping[str, object],
    records: Sequence[measure_stage.MeasurementRecord],
    expected_primary_instances: Collection[tuple[str, int]],
    e_use_rows: Sequence[EUseEvidenceRow],
) -> AbsoluteGateInputBundle:
    """v1.1 §V3.2: §10.3 ABSOLUTE holdout gate の実入力を campaign 実測から
    組み立てる。入力の出所:

    - `E_use[i]`: `e_use_rows`（`load_e_use_rows()`）から `candidate.
      construct/unit/domain` に一致する 1 行（`e_use_table.find_row()`）。
      relative 行は `absolute_e_use_value()` で instance 単位に展開する。
    - `U_GT[i]`/`U_num[i]`: `declared_u_gt_u_num_for_family()`
      （family 単位の C0-frozen 保守上限。全 instance で共通値として使う
      ——現行 C0 freeze はこれより細かい粒度を凍結していない）。
    - `U_rep`/`U_proc`: `observables.u_rep`/`u_proc`（本関数が組み立てた
      within/fresh process の実測 repeat 値から）。
    - invariance 軸 pair: `build_invariance_pairs_for_family()`。
    - gate5 の `FDR0`/`FNR1`: `control_detection_for_family()`。

    必須入力（E_use 行・U_GT/U_num・U_rep・U_proc）のいずれかが欠落/計算
    不能なら `GateInputError`（呼び出し側が §11 の `NOT_EVALUABLE/
    INPUT_MISSING` へ写像する）。"""
    e_use_row = find_e_use_row(
        e_use_rows, construct_id=candidate.construct, unit=candidate.unit, domain=candidate.domain
    )
    if e_use_row is None:
        raise GateInputError(
            "no E_use evidence row for "
            f"(construct={candidate.construct!r}, unit={candidate.unit!r}, "
            f"domain={candidate.domain!r})"
        )
    u_gt_u_num = declared_u_gt_u_num_for_family(manifest, family)
    if u_gt_u_num is None:
        raise GateInputError(f"no frozen U_GT/U_num bound for family {family!r}")
    u_gt_value, u_num_value = u_gt_u_num

    own_records = [r for r in records if r.candidate_id == candidate.candidate_id]

    margins: list[InstanceMargin] = []
    per_process_ranges: dict[tuple[str, str], list[float]] = {}
    per_instance_process_medians: dict[str, list[float]] = {}
    for row_id, probe_index in sorted(expected_primary_instances):
        instance_id = instance_id_str(row_id, probe_index)
        row = row_by_id.get(row_id)
        if row is None:
            raise GateInputError(f"expected PRIMARY instance {instance_id!r} has no matrix row")
        truth = truth_value_for_row(row)
        if truth is None:
            raise GateInputError(
                f"row {row_id!r} (family {family!r}) has no declared truth value"
            )
        per_process = _per_instance_output_repeats(own_records, candidate, row_id, probe_index)
        if not per_process:
            raise GateInputError(f"no usable measurement for expected PRIMARY instance {instance_id!r}")
        m = two_stage_median(per_process)
        et = error_terms(m, truth, 1e-9)
        e_use_value = absolute_e_use_value(e_use_row, truth)
        if e_use_value is None:
            raise GateInputError(
                f"E_use for construct {candidate.construct!r} is not a usable positive "
                f"value (evidence_class={e_use_row.evidence_class.value})"
            )
        margins.append(
            InstanceMargin(
                instance_id=instance_id,
                domain=Domain.PRIMARY,
                eligible=True,
                ae=et.ae,
                e=et.e,
                u_gt=u_gt_value,
                u_num=u_num_value,
                e_use=e_use_value,
            )
        )
        for process_id, values in per_process.items():
            per_process_ranges[(instance_id, process_id)] = list(values)
        per_instance_process_medians[instance_id] = [
            statistics.median(values) for values in per_process.values()
        ]

    u_rep_value = u_rep(per_process_ranges)
    if u_rep_value is None:
        raise GateInputError(
            "U_rep is not computable from the measured repeats (no >=2-repeat process cell)"
        )
    try:
        u_proc_value = u_proc(per_instance_process_medians)
    except ValueError as exc:
        raise GateInputError(f"U_proc is not computable: {exc}") from exc

    declared_axes = declared_axes_for_family(manifest, family)
    invariance_pairs_by_axis = build_invariance_pairs_for_family(
        matrix_rows=matrix_rows,
        assignment=assignment,
        family=family,
        candidate=candidate,
        records=own_records,
        declared_axes=declared_axes,
        e_use_row=e_use_row,
    )

    detection = control_detection_for_family(
        matrix_rows=matrix_rows,
        assignment=assignment,
        family=family,
        candidate=candidate,
        records=own_records,
    )

    return AbsoluteGateInputBundle(
        margins=tuple(margins),
        invariance_pairs_by_axis=invariance_pairs_by_axis,
        declared_invariance_axes=declared_axes,
        u_rep=u_rep_value,
        u_proc=u_proc_value,
        fdr0=detection.fdr0,
        fnr1=detection.fnr1,
        min_count_met=detection.min_count_met,
    )


def evaluate_absolute_meter_from_campaign(
    *,
    meter_id: str,
    family: str,
    candidate: Candidate,
    manifest: Mapping[str, object],
    row_by_id: Mapping[str, FixtureRow],
    matrix_rows: Sequence[MatrixRow],
    assignment: Mapping[str, object],
    records: Sequence[measure_stage.MeasurementRecord],
    expected_primary_instances: Collection[tuple[str, int]],
    e_use_rows: Sequence[EUseEvidenceRow],
) -> MeterHoldoutResult:
    """v1.1 §V3.2 (D17 close): `evaluate_absolute_meter()` の入力を campaign
    実測から組み立て、実 gate を評価する。入力組み立て不能は正直に
    `NOT_EVALUABLE/INPUT_MISSING` として終端する（§11: C0 入力側 critical
    missing。gate が正直に fail するのとは異なる終端——ここでは gate 自体を
    評価する前に入力が組み立てられない）。"""
    try:
        bundle = build_absolute_gate_inputs(
            manifest=manifest,
            family=family,
            candidate=candidate,
            row_by_id=row_by_id,
            matrix_rows=matrix_rows,
            assignment=assignment,
            records=records,
            expected_primary_instances=expected_primary_instances,
            e_use_rows=e_use_rows,
        )
    except GateInputError as exc:
        return MeterHoldoutResult(
            meter_id=meter_id,
            terminal_status=TerminalStatus.NOT_EVALUABLE.value,
            reason_code=MissingReason.INPUT_MISSING.value,
            ceiling=ClaimCeiling.NONE.value,
            selected_candidate_id=candidate.candidate_id,
            gate_detail={
                "reason": f"[v1.1 §V3.2] ABSOLUTE gate input assembly failed: {exc}",
            },
        )
    return evaluate_absolute_meter(
        meter_id,
        ClaimCeiling.ABSOLUTE,
        selected_candidate_id=candidate.candidate_id,
        per_instance_margins=bundle.margins,
        u_rep=bundle.u_rep,
        u_proc=bundle.u_proc,
        invariance_pairs_by_axis=bundle.invariance_pairs_by_axis,
        declared_invariance_axes=bundle.declared_invariance_axes,
        expected_primary_instance_ids=[
            instance_id_str(row_id, probe_index) for row_id, probe_index in expected_primary_instances
        ],
        fdr0=bundle.fdr0,
        fnr1=bundle.fnr1,
        min_count_met=bundle.min_count_met,
    )


def directional_claim_shrinkage_detail(
    *,
    expected_sweep_member_row_ids: Mapping[str, Sequence[str]],
    row_by_id: Mapping[str, FixtureRow],
) -> dict[str, object]:
    """v1.1 §V2.2 末尾 + §V2.4（claim 縮小の列挙義務）: 全 family の
    DIRECTIONAL 終端 status の claim text に必須で列挙する「評価済み sweep
    の held-fixed 文脈」+ prohibited interpretations。claim-relevant field
    の全値被覆が成立する family（FORMANT/IDENTITY の周辺被覆）でも列挙義務は
    同様に課される（§V2.2 末尾: 「被覆成立は claim の広さではなく選抜の質の
    保証にすぎない」）。"""
    evaluated_sweep_contexts: list[dict[str, object]] = []
    for sweep_id in sorted(expected_sweep_member_row_ids):
        member_row_ids = tuple(sorted(expected_sweep_member_row_ids[sweep_id]))
        sample_row = next((row_by_id[rid] for rid in member_row_ids if rid in row_by_id), None)
        held_fixed = _sweep_context_fields(sample_row) if sample_row is not None else {}
        evaluated_sweep_contexts.append(
            {
                "sweep_id": sweep_id,
                "held_fixed_context": held_fixed,
                "member_row_ids": member_row_ids,
            }
        )
    return {
        "evaluated_sweep_contexts": evaluated_sweep_contexts,
        "prohibited_interpretations": (
            "directional extrapolation to sweep contexts not listed in "
            "evaluated_sweep_contexts is prohibited (v1.1 SS V2.2/V2.4)",
        ),
    }


@dataclass(frozen=True)
class DirectionalGateInputBundle:
    pairs: tuple[DirectionalPair, ...]
    expected_sweep_ids: tuple[str, ...]
    expected_adjacent_pair_ids: Mapping[str, tuple[str, ...]]
    u_rep: float
    u_proc: float
    negative_control_failures: int
    positive_control_failures: int


def build_directional_gate_inputs(
    *,
    family: str,
    candidate: Candidate,
    row_by_id: Mapping[str, FixtureRow],
    matrix_rows: Sequence[MatrixRow],
    assignment: Mapping[str, object],
    records: Sequence[measure_stage.MeasurementRecord],
    usable_primary_instances: Collection[tuple[str, int]],
    expected_sweep_member_row_ids: Mapping[str, Sequence[str]],
    manifest: Mapping[str, object],
) -> DirectionalGateInputBundle:
    """v1.1 §V3.2/§V2.3: §10.4 DIRECTIONAL holdout gate の実入力。sweep 単位
    = V2.3 の holdout 常駐 declared sweep（`expected_sweep_member_row_ids`
    ——呼び出し側 `cli._run_c4` が manifest `holdout_sweeps[family]`
    （優先）または `declared_sweeps_by_family()` から渡す、既存の
    `expected_sweep_ids` と同じ入口）。sweep 内の distinct truth level
    （`selection_stage.truth_value_for_row`）ごとに集約した出力から、truth
    昇順で全 pair を構成する（隣接 pair = ソート順で連続する 2 level。
    gates.directional_gates() 自身が resolvable 性・符号・sweep 最小数を
    判定する——本関数は候補ペアの機械的組み立てのみを担う）。`U_GT`/`U_num`
    は ABSOLUTE と同じ family 単位の frozen bound
    （`declared_u_gt_u_num_for_family()`）を再利用する。

    **instance 単位の two-stage median（R18 対応、Codex レビュー第 18 巡
    P1 採用、2026-09-05）**: `MeasurementRecord.process_id` は
    `within-process`/`fresh-process-N` として全 probe instance で同名を共有
    するため、旧実装は `two_stage_median()` へ渡す `per_process` バケットを
    `row_id` 単位（`f"{row_id}:{process_id}"`）でしか分けておらず、同じ
    row の 5 probe instance（`fixture_controls.PROBE_REPEATS`）の repeat が
    同一 process バケットへ黙って併合されていた——`observables.
    two_stage_median` が要求する `m[i] = median_p(median_r(x))`（§10.1）の
    `i` は 1 probe instance（`(row_id, probe_index)`）を指すが、この併合は
    5 instance ぶんの生値を「1 instance の複数 repeat」として二段 median に
    通す非結合的な操作であり、instance ごとに正しく二段 median を取った後で
    集約するのとは異なる符号・値になり得る（合成ケースで実証: `tests/
    test_holdout_stage.py::test_build_directional_gate_inputs_...`）。
    本関数は ABSOLUTE 側 `build_absolute_gate_inputs()` と同じ粒度——
    `(row_id, probe_index)` の 1 instance ごとに `two_stage_median()` で
    `m[i]` を算出してから、truth level の代表値は複数 instance の `m[i]`
    の `statistics.median()`（`gate3`/`gate2'` 等、設計正本全体で instance
    集約に一貫して使われている中央値ベースの代表値と同じ選択）で結合する。"""
    u_gt_u_num = declared_u_gt_u_num_for_family(manifest, family)
    if u_gt_u_num is None:
        raise GateInputError(f"no frozen U_GT/U_num bound for family {family!r}")
    u_gt_value, u_num_value = u_gt_u_num

    own_records = [r for r in records if r.candidate_id == candidate.candidate_id]
    usable_set = set(usable_primary_instances)

    pairs: list[DirectionalPair] = []
    expected_adjacent_pair_ids: dict[str, tuple[str, ...]] = {}
    per_process_ranges: dict[tuple[str, str], list[float]] = {}
    per_instance_process_medians: dict[str, list[float]] = {}

    for sweep_id in sorted(expected_sweep_member_row_ids):
        member_row_ids = expected_sweep_member_row_ids[sweep_id]
        by_truth: dict[float, list[str]] = {}
        for row_id in member_row_ids:
            row = row_by_id.get(row_id)
            if row is None:
                continue
            truth = truth_value_for_row(row)
            if truth is None:
                continue
            by_truth.setdefault(truth, []).append(row_id)

        level_outputs: dict[float, float] = {}
        for truth, row_ids in by_truth.items():
            instance_m_values: list[float] = []
            for row_id in row_ids:
                for probe_index in range(fixture_controls.PROBE_REPEATS):
                    if (row_id, probe_index) not in usable_set:
                        continue
                    repeats = _per_instance_output_repeats(own_records, candidate, row_id, probe_index)
                    if not repeats:
                        continue
                    instance_id = instance_id_str(row_id, probe_index)
                    for process_id, values in repeats.items():
                        per_process_ranges.setdefault((instance_id, process_id), []).extend(values)
                    per_instance_process_medians[instance_id] = [
                        statistics.median(values) for values in repeats.values()
                    ]
                    # R18: two_stage_median は必ず 1 instance 自身の
                    # per_process repeats にのみ適用する（他 instance と
                    # 併合しない）——m[i] の定義そのもの。
                    instance_m_values.append(two_stage_median(repeats))
            if instance_m_values:
                level_outputs[truth] = statistics.median(instance_m_values)

        expected_adjacent_pair_ids.setdefault(sweep_id, ())
        levels = sorted(level_outputs)
        if len(levels) < 2:
            continue
        adjacent_ids: list[str] = []
        for i in range(len(levels)):
            for j in range(i + 1, len(levels)):
                truth_i, truth_j = levels[i], levels[j]
                delta_truth = truth_j - truth_i
                delta_output = level_outputs[truth_j] - level_outputs[truth_i]
                pair_id = f"{sweep_id}#{i}#{j}"
                is_adjacent = j == i + 1
                pairs.append(
                    DirectionalPair(
                        pair_id=pair_id,
                        delta_truth=delta_truth,
                        delta_output=delta_output,
                        u_gt_i=u_gt_value,
                        u_num_i=u_num_value,
                        u_gt_j=u_gt_value,
                        u_num_j=u_num_value,
                        correct_sign=(delta_truth > 0) == (delta_output > 0),
                        is_adjacent=is_adjacent,
                        sweep_id=sweep_id,
                    )
                )
                if is_adjacent:
                    adjacent_ids.append(pair_id)
        expected_adjacent_pair_ids[sweep_id] = tuple(adjacent_ids)

    if not pairs:
        raise GateInputError("no directional pair could be assembled from usable holdout instances")

    u_rep_value = u_rep(per_process_ranges)
    if u_rep_value is None:
        raise GateInputError("U_rep is not computable from the measured repeats")
    try:
        u_proc_value = u_proc(per_instance_process_medians)
    except ValueError as exc:
        raise GateInputError(f"U_proc is not computable: {exc}") from exc

    detection = control_detection_for_family(
        matrix_rows=matrix_rows,
        assignment=assignment,
        family=family,
        candidate=candidate,
        records=records,
    )

    return DirectionalGateInputBundle(
        pairs=tuple(pairs),
        expected_sweep_ids=tuple(sorted(expected_sweep_member_row_ids)),
        expected_adjacent_pair_ids=expected_adjacent_pair_ids,
        u_rep=u_rep_value,
        u_proc=u_proc_value,
        negative_control_failures=detection.negative_control_failures,
        positive_control_failures=detection.positive_control_failures,
    )


def evaluate_directional_meter_from_campaign(
    *,
    meter_id: str,
    family: str,
    candidate: Candidate,
    manifest: Mapping[str, object],
    row_by_id: Mapping[str, FixtureRow],
    matrix_rows: Sequence[MatrixRow],
    assignment: Mapping[str, object],
    records: Sequence[measure_stage.MeasurementRecord],
    usable_primary_instances: Collection[tuple[str, int]],
    expected_sweep_member_row_ids: Mapping[str, Sequence[str]],
    units_commensurate: bool | None = None,
) -> MeterHoldoutResult:
    """v1.1 §V3.2 (D17 close): `evaluate_directional_meter()` の入力を
    campaign 実測から組み立て、実 gate を評価する。終端 status がいずれで
    あっても（`CALIBRATED_DIRECTIONAL`/`DIAGNOSTIC_ONLY`/`NOT_EVALUABLE`）、
    v1.1 §V2.4 の claim-shrinkage 列挙義務を `gate_detail["claim_text"]`/
    `gate_detail["prohibited_interpretations"]` として必ず添付する（何も
    評価できなかった場合に claim を主張しないよう、`NOT_EVALUABLE` のときは
    列挙を空のまま——ただし INPUT_MISSING 以外は常に添付する。INPUT_MISSING
    は gate 自体に到達していないため対象外）。

    **`units_commensurate`（R18 対応、Codex レビュー第 18 巡 P1 採用、
    2026-09-05）**: 省略（`None`）時は `units_commensurate_for_family()`
    で `candidate.unit` と凍結 fixture truth unit から機械導出する——旧実装は
    既定値 `False` を `_run_c4` が一度も上書きせず、本番で §10.4 条件 (c)
    が常に無効だった。明示的に `True`/`False` を渡した呼び出し側（テスト等）
    はその値をそのまま使う（後方互換）。"""
    resolved_units_commensurate = (
        units_commensurate
        if units_commensurate is not None
        else units_commensurate_for_family(manifest, family, candidate.unit)
    )
    try:
        bundle = build_directional_gate_inputs(
            family=family,
            candidate=candidate,
            row_by_id=row_by_id,
            matrix_rows=matrix_rows,
            assignment=assignment,
            records=records,
            usable_primary_instances=usable_primary_instances,
            expected_sweep_member_row_ids=expected_sweep_member_row_ids,
            manifest=manifest,
        )
    except GateInputError as exc:
        return MeterHoldoutResult(
            meter_id=meter_id,
            terminal_status=TerminalStatus.NOT_EVALUABLE.value,
            reason_code=MissingReason.INPUT_MISSING.value,
            ceiling=ClaimCeiling.NONE.value,
            selected_candidate_id=candidate.candidate_id,
            gate_detail={
                "reason": f"[v1.1 §V3.2] DIRECTIONAL gate input assembly failed: {exc}",
            },
        )
    result = evaluate_directional_meter(
        meter_id,
        ClaimCeiling.DIRECTIONAL,
        selected_candidate_id=candidate.candidate_id,
        pairs=bundle.pairs,
        u_rep=bundle.u_rep,
        u_proc=bundle.u_proc,
        expected_sweep_ids=bundle.expected_sweep_ids,
        expected_adjacent_pair_ids=bundle.expected_adjacent_pair_ids,
        negative_control_failures=bundle.negative_control_failures,
        positive_control_failures=bundle.positive_control_failures,
        units_commensurate=resolved_units_commensurate,
    )
    claim_detail = directional_claim_shrinkage_detail(
        expected_sweep_member_row_ids=expected_sweep_member_row_ids,
        row_by_id=row_by_id,
    )
    return dataclass_replace(
        result,
        gate_detail={
            **dict(result.gate_detail),
            "claim_text": {"evaluated_sweep_contexts": claim_detail["evaluated_sweep_contexts"]},
            "prohibited_interpretations": claim_detail["prohibited_interpretations"],
        },
    )


def evaluate_m6_identity(
    *,
    manifest: Mapping[str, object],
    matrix_rows: Sequence[MatrixRow],
) -> MeterHoldoutResult:
    """v1.1 §V3.2 M6: `vocab.CLAIM_CRITICAL_SET` の全 member が
    `CALIBRATED_ABSOLUTE` のときのみ呼び出し側（`cli._run_c4`）から呼ばれる
    ——precondition の判定自体は呼び出し側の責務のまま変えない（既存分岐は
    実評価呼び出しへ差し替えるのみ）。

    **既知の infra gap（本 WP のスコープ境界。正直に記録する）**: 設計正本
    §12 の `m6_identity.m6_distance()` は IDENTITY_CAUSAL_SWEEP fixture 上で
    CLAIM_CRITICAL_SET の 3 meter（M3_FORMANTS/M2_SPECTRAL_TILT/
    M2_APERIODICITY）を founder pair（A/B）に対して実測した component
    vector と、null pair 母集団（`T_null` 用）を要求する。しかし
    `campaign.cli._FAMILY_TO_METER` は IDENTITY_CAUSAL_SWEEP を全く含まず
    （実地調査で確認済み: M6 は独立 fixture family を持たない独立 meter で
    あり、この family の候補は C4 の per-family loop で一度も測定されない）、
    この cross-family 測定（3 meter の選択済み candidate を IDENTITY 音声に
    対して追加測定する経路。加えて「A/B のどの行を比較するか」「null pair を
    どう構成するか」という実験設計そのものが v1.0/v1.1 で操作的に規定されて
    いない）は `render_stage`/`measure_stage`/`workunits`（いずれも本 WP の
    許可ファイル範囲外）への新規測定ユニット追加を要する——単なる「配線」を
    超える、別の Design Memo を要する設計課題である。したがって本関数は
    precondition が真であっても常に `NOT_EVALUABLE/INPUT_MISSING`
    （§11「C0 入力側 critical missing」）を返す——これは D17 placeholder の
    ような無言の固定応答ではなく、precondition を実評価した上での正直な
    境界宣言であり、次の Design Memo（IDENTITY pair 測定経路の設計）への
    引き継ぎ材料として `gate_detail` に記録する。"""
    holdout_sweeps = manifest.get("holdout_sweeps")
    identity_sweeps = (
        holdout_sweeps.get("IDENTITY_CAUSAL_SWEEP")
        if isinstance(holdout_sweeps, Mapping)
        else None
    )
    detail: dict[str, object] = {
        "reason": (
            "[v1.1 SS V3.2 boundary] M6 identity-preservation gate precondition "
            "satisfied (all CLAIM_CRITICAL_SET members reached CALIBRATED_ABSOLUTE), "
            "but the cross-family measurement path that would produce "
            "component_a/component_b (the 3 CLAIM_CRITICAL_SET meters measured "
            "against IDENTITY_CAUSAL_SWEEP founder-pair audio, plus a null-pair "
            "population for T_null) does not exist in the current measurement "
            "pipeline (campaign.cli._FAMILY_TO_METER never maps "
            "IDENTITY_CAUSAL_SWEEP to a meter) and its experimental design "
            "(which rows form the A/B comparison, how null pairs are built) is "
            "unspecified in DESIGN_VG_METER_CAL_DEBT v1.0/v1.1. Building it is "
            "out of this work package's file scope (render_stage/measure_stage/"
            "workunits) and requires a follow-up Design Memo, not silent "
            "wiring. Recorded honestly rather than papered over."
        ),
    }
    if isinstance(identity_sweeps, Mapping) and identity_sweeps:
        row_by_id = {mr.row_id: mr.row for mr in matrix_rows}
        pinned_cells = []
        for sweep_id, member in sorted(identity_sweeps.items()):
            member_row_ids = tuple(sorted(member))
            sample_row = next(
                (row_by_id[rid] for rid in member_row_ids if rid in row_by_id), None
            )
            pinned_cells.append(
                {
                    "sweep_id": sweep_id,
                    "held_fixed_context": (
                        _sweep_context_fields(sample_row) if sample_row is not None else {}
                    ),
                    "member_row_ids": member_row_ids,
                }
            )
        detail["pinned_identity_cells"] = pinned_cells
        detail["prohibited_interpretations"] = (
            "no identity-preservation claim is made for any (founder, trait) cell "
            "-- pinned_identity_cells lists what holdout sweep pinning made "
            "structurally available, not what M6 evaluated (v1.1 SS V2.4)",
        )
    return MeterHoldoutResult(
        meter_id=MeterId.M6_IDENTITY.value,
        terminal_status=TerminalStatus.NOT_EVALUABLE.value,
        reason_code=MissingReason.INPUT_MISSING.value,
        ceiling=ClaimCeiling.NONE.value,
        selected_candidate_id=None,
        gate_detail=detail,
    )


# ---------------------------------------------------------------------------
# E_use table: relative/absolute row split
#
# `gates.EUseEvidenceRow.e_use_mode`（`[UNDERSPEC-CAL-D11]`、`gates.py`/
# `e_use_table.py` — 他 agent が同一セッションで並行実装した凍結列）が
# absolute/relative の権威ある判別列。本モジュールはそれをそのまま消費する
# だけで、独自の mode 推測ロジックは持たない。
# ---------------------------------------------------------------------------


class StaleEUseTableError(RuntimeError):
    """round 20 採用 (2): `load_e_use_rows()` が読んだ `e_use_table.json` の
    バイト列が、凍結 manifest の `frozen_inputs.e_use_table_sha256` pin と
    一致しない、または pin/ファイル自体が欠落している場合の fail-closed
    error（凍結後の改竄・欠落・pin 未設定のいずれも同じ経路で検出する）。"""


def _read_and_verify_e_use_table_bytes(campaign: FrozenCampaign) -> tuple[Path, bytes]:
    """`e_use_table.json` を **1 回だけ** `read_bytes()` し、そのバッファの
    sha256 を凍結 manifest の `frozen_inputs.e_use_table_sha256` pin と照合
    する。検証に使ったのと同一バッファを呼び出し側へ返す（TOCTOU 排除:
    `c0_freeze._check_e_use_table()`/`measure_stage._verify_and_load_rendered_pcm()`
    と同じ「1 read → 検証 → 同じバッファをパース」規約）。不一致・欠落は
    いずれも `StaleEUseTableError`。"""
    frozen_inputs = campaign.manifest.get("frozen_inputs")
    expected_sha256 = (
        frozen_inputs.get("e_use_table_sha256") if isinstance(frozen_inputs, Mapping) else None
    )
    if not isinstance(expected_sha256, str) or not expected_sha256:
        raise StaleEUseTableError(
            "load_e_use_rows: frozen manifest is missing a non-blank "
            "frozen_inputs.e_use_table_sha256 pin"
        )
    path = campaign.campaign_dir / "e_use_table.json"
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise StaleEUseTableError(f"load_e_use_rows: cannot read {path}: {exc}") from exc
    actual_sha256 = hashlib.sha256(data).hexdigest()
    if actual_sha256 != expected_sha256:
        raise StaleEUseTableError(
            f"load_e_use_rows: {path} sha256 ({actual_sha256!r}) does not match frozen "
            f"manifest frozen_inputs.e_use_table_sha256 pin ({expected_sha256!r}) — "
            "the frozen E_use evidence table appears to have been mutated after freeze"
        )
    return path, data


def _parse_e_use_table_bytes(path: Path, data: bytes) -> list[EUseEvidenceRow]:
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StaleEUseTableError(f"load_e_use_rows: {path}: {exc}") from exc
    if not isinstance(raw, list):
        raise StaleEUseTableError(
            f"load_e_use_rows: {path}: must contain a JSON array of row objects"
        )
    rows: list[EUseEvidenceRow] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            raise StaleEUseTableError(f"load_e_use_rows: {path}[{i}]: row must be a JSON object")
        try:
            rows.append(row_from_dict(entry))
        except (KeyError, ValueError, TypeError) as exc:
            raise StaleEUseTableError(f"load_e_use_rows: {path}[{i}]: {exc}") from exc
    return rows


def load_e_use_rows(
    campaign: FrozenCampaign, *, invocation_id: str | None = None
) -> tuple[EUseEvidenceRow, ...]:
    """凍結 campaign dir 直下の `e_use_table.json`（`e_use_table.load_e_use_table`
    が読む 14 列 JSON 配列。armed `c0_freeze.armed_freeze()` がここへ配置する）
    を読む。

    round 20 採用 (2): 従来は欠落時に空タプルへ fail-soft していたが
    （「REQUIRED_BLOCKING の判定は C0 freeze 側の責務」という理由付け）、
    これは凍結後にファイルが差し替えられた/削除された場合でも holdout gate
    が「E_use evidence 無し」を静かに受理してしまう欠陥だった（レビュー
    round 20 finding #2）。本関数は now: (1) `e_use_table.json` を 1 回だけ
    `read_bytes()` し、(2) その sha256 を凍結 manifest の
    `frozen_inputs.e_use_table_sha256` pin と照合し、(3) 検証に使った同一
    バッファをパースする。ファイル欠落・pin 欠落・sha256 不一致はいずれも
    `StaleEUseTableError` を送出しつつ ledger `stop_event`
    （`reason: "E_USE_TABLE_STALE_OR_MUTATED"`）を記帳した上で fail-closed
    する（`measure_stage.run_measurement_for_instance` の
    `StaleMeasurementError`/`StaleRenderError` と同じ「catch → stop_event
    記帳 → re-raise」規約。空タプルを返す経路はもう存在しない）。"""
    try:
        path, data = _read_and_verify_e_use_table_bytes(campaign)
        rows = _parse_e_use_table_bytes(path, data)
    except StaleEUseTableError as exc:
        campaign.ledger.append(
            {
                "kind": "stop_event",
                "reason": "E_USE_TABLE_STALE_OR_MUTATED",
                "detail": str(exc),
                "invocation_id": invocation_id,
            }
        )
        raise
    return tuple(rows)


def split_e_use_rows_by_mode(
    rows: Sequence[EUseEvidenceRow],
) -> tuple[tuple[EUseEvidenceRow, ...], tuple[EUseEvidenceRow, ...]]:
    """`(absolute_rows, relative_rows)`。`EUseEvidenceRow.e_use_mode`
    （`gates.E_USE_MODE_VALUES`）で分割する。"absolute" 行の `e_use_value` は
    `InstanceMargin.e_use`/`threshold_margin()` へ絶対量としてそのまま渡せる。
    "relative" 行は construct 単位の 1 スカラー相対誤差であり、instance 単位の
    絶対 E_use 展開（`e_use_value * declared_truth`）は呼び出し側
    （`build_instance_margins` 呼び出し前の前処理）の責務のまま残る
    （`gates.EUseEvidenceRow` docstring 参照）。"""
    absolute_rows = tuple(r for r in rows if r.e_use_mode == "absolute")
    relative_rows = tuple(r for r in rows if r.e_use_mode == "relative")
    return absolute_rows, relative_rows


# ---------------------------------------------------------------------------
# building blocks: raw repeat outputs -> InstanceMargin / DirectionalPair
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawInstanceObservation:
    """1 instance の観測入力（すでに集計済み）。`per_process_repeats` は
    `{process_id: [raw_value, ...]}`（`observables.two_stage_median` の入力
    形そのもの）。"""

    instance_id: str
    domain: Domain
    truth: float
    per_process_repeats: Mapping[str, Sequence[float]]
    u_gt: float
    u_num: float
    e_use: float
    zero_guard: float = 1e-9
    eligible: bool = True


def build_instance_margins(
    observations: Sequence[RawInstanceObservation],
) -> list[InstanceMargin]:
    """`observables.two_stage_median`/`error_terms` を通して `InstanceMargin`
    を組み立てる（`gates.absolute_gates` の直接入力）。"""
    margins: list[InstanceMargin] = []
    for obs in observations:
        m = two_stage_median(obs.per_process_repeats)
        et = error_terms(m, obs.truth, obs.zero_guard)
        margins.append(
            InstanceMargin(
                instance_id=obs.instance_id,
                domain=obs.domain,
                eligible=obs.eligible,
                ae=et.ae,
                e=et.e,
                u_gt=obs.u_gt,
                u_num=obs.u_num,
                e_use=obs.e_use,
            )
        )
    return margins


@dataclass(frozen=True)
class RawDirectionalObservation:
    pair_id: str
    sweep_id: str
    delta_truth: float
    delta_output: float
    u_gt_i: float
    u_num_i: float
    u_gt_j: float
    u_num_j: float
    correct_sign: bool
    is_adjacent: bool


def build_directional_pairs(
    observations: Sequence[RawDirectionalObservation],
) -> list[DirectionalPair]:
    return [
        DirectionalPair(
            pair_id=o.pair_id,
            delta_truth=o.delta_truth,
            delta_output=o.delta_output,
            u_gt_i=o.u_gt_i,
            u_num_i=o.u_num_i,
            u_gt_j=o.u_gt_j,
            u_num_j=o.u_num_j,
            correct_sign=o.correct_sign,
            is_adjacent=o.is_adjacent,
            sweep_id=o.sweep_id,
        )
        for o in observations
    ]


# ---------------------------------------------------------------------------
# per-meter evaluation -> terminal status
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MeterHoldoutResult:
    meter_id: str
    terminal_status: str
    reason_code: str | None
    ceiling: str
    selected_candidate_id: str | None
    gate_detail: Mapping[str, object]


def evaluate_absolute_meter(
    meter_id: str,
    ceiling: ClaimCeiling,
    *,
    selected_candidate_id: str | None,
    per_instance_margins: Sequence[InstanceMargin],
    u_rep: float,
    u_proc: float,
    invariance_pairs_by_axis: Mapping[str, Sequence[InvariancePair]],
    declared_invariance_axes: Collection[str],
    expected_primary_instance_ids: Collection[str],
    fdr0: float,
    fnr1: float,
    min_count_met: bool,
    procedure_breach: bool = False,
    evaluable: bool = True,
) -> MeterHoldoutResult:
    """§10.3 ABSOLUTE holdout gate 一式 → §11 first-match cascade。"""
    if not evaluable or not per_instance_margins:
        status = terminal_status(
            procedure_breach=procedure_breach,
            evaluable=False,
            ceiling=ceiling,
            absolute_gates_passed=False,
            directional_gates_passed=False,
        )
        reason = (
            MissingReason.PROCEDURE.value
            if procedure_breach
            else MissingReason.OUTPUT_NOT_EVALUABLE.value
        )
        return MeterHoldoutResult(
            meter_id=meter_id,
            terminal_status=status.value,
            reason_code=reason,
            ceiling=ceiling.value,
            selected_candidate_id=selected_candidate_id,
            gate_detail={"reason": "no evaluable PRIMARY instance"},
        )

    gate: AbsoluteGateResult = absolute_gates(
        per_instance_margins,
        u_rep=u_rep,
        u_proc=u_proc,
        invariance_pairs_by_axis=invariance_pairs_by_axis,
        declared_invariance_axes=declared_invariance_axes,
        expected_primary_instance_ids=expected_primary_instance_ids,
        fdr0=fdr0,
        fnr1=fnr1,
        min_count_met=min_count_met,
    )
    status = terminal_status(
        procedure_breach=procedure_breach,
        evaluable=True,
        ceiling=ceiling,
        absolute_gates_passed=gate.passed,
        directional_gates_passed=False,
    )
    #: R13 対応（Codex 第 13 巡 P1 採用、2026-09-05）: 設計正本 §11 は
    #: `OUTPUT_MISSING` を「score 計算可能だが PRIMARY 一部 output missing で
    #: gate 不通過」専用に予約する。旧実装は cascade 5 (else -> DIAGNOSTIC_
    #: ONLY) の全経路（accuracy/invariance/control gate の正直な fail を
    #: 含む）を一律 `OUTPUT_MISSING` としており、完全・有限な観測で単に
    #: gate2'/gate_max'/gate3/gate4'/gate5 が閾値を満たさなかっただけの
    #: ケースまで「output が missing」と偽って記録していた。`gate1_all_
    #: eligible` こそが「PRIMARY output の完全性」を表す唯一のフラグ
    #: （§10.3 gate1: 「全 PRIMARY instance が eligible」）——これが False の
    #: ときのみ真の部分欠落として `OUTPUT_MISSING` を立て、gate1 が通った上で
    #: 他の gate が正直に fail した場合は理由コード無し（`None`）とし、
    #: `gate_detail.failure_reasons` に落ちた gate の内訳を残す。
    reason = None
    if status == TerminalStatus.DIAGNOSTIC_ONLY and not gate.gate1_all_eligible:
        reason = MissingReason.OUTPUT_MISSING.value
    return MeterHoldoutResult(
        meter_id=meter_id,
        terminal_status=status.value,
        reason_code=reason,
        ceiling=ceiling.value,
        selected_candidate_id=selected_candidate_id,
        gate_detail={
            "passed": gate.passed,
            "failure_reasons": list(gate.failure_reasons),
        },
    )


def evaluate_directional_meter(
    meter_id: str,
    ceiling: ClaimCeiling,
    *,
    selected_candidate_id: str | None,
    pairs: Sequence[DirectionalPair],
    u_rep: float,
    u_proc: float,
    expected_sweep_ids: Collection[str],
    expected_adjacent_pair_ids: Mapping[str, Collection[str]] | None,
    negative_control_failures: int,
    positive_control_failures: int,
    units_commensurate: bool,
    procedure_breach: bool = False,
    evaluable: bool = True,
) -> MeterHoldoutResult:
    """§10.4 DIRECTIONAL holdout gate 一式 → §11 first-match cascade。"""
    if not evaluable or not pairs:
        status = terminal_status(
            procedure_breach=procedure_breach,
            evaluable=False,
            ceiling=ceiling,
            absolute_gates_passed=False,
            directional_gates_passed=False,
        )
        reason = (
            MissingReason.PROCEDURE.value
            if procedure_breach
            else MissingReason.OUTPUT_NOT_EVALUABLE.value
        )
        return MeterHoldoutResult(
            meter_id=meter_id,
            terminal_status=status.value,
            reason_code=reason,
            ceiling=ceiling.value,
            selected_candidate_id=selected_candidate_id,
            gate_detail={"reason": "no evaluable directional pair"},
        )

    gate: DirectionalGateResult = directional_gates(
        pairs,
        u_rep=u_rep,
        u_proc=u_proc,
        expected_sweep_ids=expected_sweep_ids,
        expected_adjacent_pair_ids=expected_adjacent_pair_ids,
        negative_control_failures=negative_control_failures,
        positive_control_failures=positive_control_failures,
        units_commensurate=units_commensurate,
    )
    status = terminal_status(
        procedure_breach=procedure_breach,
        evaluable=True,
        ceiling=ceiling,
        absolute_gates_passed=False,
        directional_gates_passed=gate.passed,
    )
    #: R13 対応（Codex 第 13 巡 P1 採用、2026-09-05）: `evaluate_absolute_
    #: meter()` と同じ欠陥（cascade 5 の全経路を一律 `OUTPUT_MISSING` として
    #: いた）を DIRECTIONAL 側でも修正する。DIRECTIONAL に ABSOLUTE の
    #: `gate1_all_eligible` に相当する単一フラグは無いため、「宣言済み
    #: sweep のうち 1 件でも観測 pair が 0 件（`observed_sweep_ids` に
    #: 含まれない）」を真の PRIMARY output 欠落として扱う——完全に測定
    #: された全 sweep が resolvability/reversal/control 判定で正直に
    #: fail した場合は理由コード無し（`None`）とする。
    observed_sweep_ids = {p.sweep_id for p in pairs}
    missing_sweep_coverage = bool(set(expected_sweep_ids) - observed_sweep_ids)
    reason = (
        MissingReason.OUTPUT_MISSING.value
        if status == TerminalStatus.DIAGNOSTIC_ONLY and missing_sweep_coverage
        else None
    )
    return MeterHoldoutResult(
        meter_id=meter_id,
        terminal_status=status.value,
        reason_code=reason,
        ceiling=ceiling.value,
        selected_candidate_id=selected_candidate_id,
        gate_detail={
            "passed": gate.passed,
            "failure_reasons": list(gate.failure_reasons),
            "resolvable_count": gate.resolvable_count,
        },
    )


def diagnostic_only_close(
    meter_id: str, *, selected_candidate_id: str | None = None, reason: str = "§16-1"
) -> MeterHoldoutResult:
    """§16-1: M4 は全候補 DIAGNOSTIC_ONLY 上限で closes（selection を回さない）。"""
    return MeterHoldoutResult(
        meter_id=meter_id,
        terminal_status=TerminalStatus.DIAGNOSTIC_ONLY.value,
        reason_code=None,
        ceiling=ClaimCeiling.DIAGNOSTIC_ONLY.value,
        selected_candidate_id=selected_candidate_id,
        gate_detail={"reason": reason},
    )


def selection_failed_closed_meter(meter_id: str) -> MeterHoldoutResult:
    """§9: 全候補 fail → `SELECTION_FAILED_CLOSED`、meter は NOT_EVALUABLE。"""
    return MeterHoldoutResult(
        meter_id=meter_id,
        terminal_status=TerminalStatus.NOT_EVALUABLE.value,
        reason_code=MissingReason.OUTPUT_NOT_EVALUABLE.value,
        ceiling=ClaimCeiling.NONE.value,
        selected_candidate_id=None,
        gate_detail={"reason": "SELECTION_FAILED_CLOSED"},
    )


# ---------------------------------------------------------------------------
# orchestration: real render + measure on the holdout split
# ---------------------------------------------------------------------------


def render_and_measure_holdout(
    campaign: FrozenCampaign,
    matrix_rows: Sequence[MatrixRow],
    *,
    candidates_by_family: Mapping[str, Sequence[Candidate]],
    max_workers: int = 1,
    f0_by_instance: Mapping[tuple[str, int], float] | None = None,
    f0_unusable_instances: frozenset[tuple[str, int]] = frozenset(),
    f0_missing_reason: str = "F0_UNUSABLE",
    f0_prepass: Callable[[], tuple[Any, ...]] | None = None,
    cap_counters: CapCounters | None = None,
    cost_caps: CostCaps | None = None,
    discard_partial_groups: bool = False,
    time_budget: TimeBudget | None = None,
    invocation_id: str | None = None,
) -> (
    dict[str, list[measure_stage.MeasurementRecord]]
    | tuple[dict[str, list[measure_stage.MeasurementRecord]], SliceStatus]
):
    """C4: holdout 非 control 行を render（determinism 検査つき。§7 leakage
    検査は `render_stage.run_render_stage` が行う）→ family ごとに指定
    candidate（選択済み候補 + B0）で測定する。戻り値は
    `{family: [MeasurementRecord, ...]}`。`f0_by_instance`（finding #2）は
    素通しで `measure_stage.run_measure_stage` へ渡す — 呼び出し元
    (`cli._run_c4`) が C3b と同じ規約（選択済み F0 candidate の instance 単位
    実測、fixture truth は使わない）で構築する。`cap_counters`/`cost_caps`
    （finding #1）は render/measure 双方へ素通しする。round 27 ADOPT (1)
    (`[UNDERSPEC-CAL-D61]`): `f0_unusable_instances` も同様に素通しし、
    F0-dependent candidate を該当 instance 上で一切呼ばせない（詳細は
    `measure_stage.run_measure_stage()`/`cli._build_f0_by_instance()`）。
    round 29 ADOPT (`[UNDERSPEC-CAL-D65]`): `f0_missing_reason` is forwarded
    unchanged to `measure_stage.run_measure_stage()`'s `missing_reason` — the
    caller passes `"F0_SELECTION_FAILED"` when `f0_unusable_instances` is
    every C4 instance because C3a itself has no F0 winner.

    `[UNDERSPEC-CAL-D86]` `f0_prepass` (optional, default `None`): a zero-arg
    callable that, when given, is invoked **after** the render sub-phase
    below and **before** the per-family measure sub-phase, and its return
    value overrides the static `f0_by_instance`/`f0_unusable_instances`/
    `f0_missing_reason` arguments above. This exists because the selected F0
    candidate's own C4 measurement (what builds `f0_by_instance`) reads the
    *rendered* PCM of each C4 HOLDOUT instance — audio that does not exist
    until the render sub-phase immediately below has produced it (production
    `c1` renders only CALIBRATION/SELECTION rows; HOLDOUT rows are rendered
    here, by `c4`). `cli._run_c4` passes a closure over its own
    `_build_f0_by_instance()` call here instead of calling it beforehand, so
    that call — and its `measure_stage.run_measurement_for_instance()` PCM
    reads — cannot run before this function's own render sub-phase does
    (previously it always could, on every campaign's very first `c4`
    invocation, deterministically: `FileNotFoundError`). Every other caller
    (direct tests, and `cli._run_c4`'s own F0_SELECTION_FAILED branch, which
    never touches PCM at all) keeps passing the static three arguments and
    omits `f0_prepass`, unaffected by this parameter's default. The callable
    itself must switch on the same `time_budget is None` rule the static
    arguments' producer (`cli._build_f0_by_instance`) already uses: return
    `(f0_by_instance, f0_unusable_instances, f0_missing_reason)` when this
    call's own `time_budget` is `None`, or additionally append its own
    `SliceStatus` as a fourth element when it is not.

    R1 の `discard_partial_groups`（design memo `design_runner_robustness.md`,
    `[UNDERSPEC-CAL-D79]`）は素通しで `measure_stage.run_measure_stage` へ
    渡す（`stage="c4"`）。

    R2: `time_budget` が渡されれば、render サブフェーズ・（あれば）
    `f0_prepass` サブフェーズ・family ごとの measure サブフェーズすべてが
    **同一の** `time_budget` を共有する — 予算切れ以降に呼ばれるサブフェーズ
    は自分の instance を 1 件も dispatch せず自分の総数をそのまま
    `instances_remaining` として返すので、`SliceStatus.aggregate()` で単純
    合算するだけで stage 全体の完走可否・進捗が正しく合成される（render を
    打ち切った場合、`time_budget` は既に expired 済みのため、後続の
    `f0_prepass`/family measure はいずれも自分の最初の pending instance で
    即座に打ち切り、1 件も新規 dispatch しない — PCM 未 render の instance に
    触れない。leakage 検査は最初の `run_render_stage` 呼び出しの中で完走済み
    の前提で毎回安全に呼べる）。この場合、戻り値は `(results, SliceStatus)`
    の 2-tuple になる。`time_budget` が `None`（既定）のときは従来どおり
    `results` 単体を返す（呼び出し元の挙動・シグネチャは不変）。"""
    if time_budget is not None:
        _outcomes, render_slice_status = run_render_stage(
            campaign,
            matrix_rows,
            stage="c4",
            cap_counters=cap_counters,
            cost_caps=cost_caps,
            time_budget=time_budget,
            invocation_id=invocation_id,
        )
        slice_statuses = [render_slice_status]
    else:
        run_render_stage(
            campaign,
            matrix_rows,
            stage="c4",
            cap_counters=cap_counters,
            cost_caps=cost_caps,
            invocation_id=invocation_id,
        )
        slice_statuses = []

    # `[UNDERSPEC-CAL-D86]`: `f0_prepass`, when given, runs strictly here —
    # after the render sub-phase above (its own measurement needs this
    # invocation's C4 HOLDOUT rows actually rendered) and strictly before
    # the per-family measure sub-phase below (which consumes its output).
    # See the docstring above for the full rationale and the callable's
    # contract. Callers that don't need this (a static `f0_by_instance`, or
    # none at all) simply never pass it — the parameters they did pass stay
    # untouched.
    if f0_prepass is not None:
        if time_budget is not None:
            f0_by_instance, f0_unusable_instances, f0_missing_reason, f0_slice_status = (
                f0_prepass()
            )
            slice_statuses.append(f0_slice_status)
        else:
            f0_by_instance, f0_unusable_instances, f0_missing_reason = f0_prepass()

    assignment = campaign.realized_split.assignment
    sr_by_row = {mr.row_id: mr.row.sr_hz for mr in matrix_rows}
    results: dict[str, list[measure_stage.MeasurementRecord]] = {}
    family_order = sorted(candidates_by_family.items())
    # v1.1 §V3.2 (D17 close): `control_detection_for_family()`'s gate5
    # FDR0/FNR1 needs the selected candidate's own measurement on negative
    # control instances (`fixtures.controls.negative_control_instances()`)
    # — `workunits.c4_holdout_instances()` deliberately excludes them
    # (`control_row_ids`, §7 leakage-artifact reuse contract: their PCM is
    # already rendered by c1, split-independent). Positive control evidence
    # needs no extra measurement here: `positive_detection_instances(...,
    # Split.HOLDOUT, family)` is a subset of the non-BOUNDARY HOLDOUT
    # population `c4_holdout_instances()` already covers (positive controls
    # are ordinary TRUTH_CORE rows). Union, not replace, so every existing
    # consumer of `instances_by_family`/`results` sees a strict superset.
    #
    # v1.1 §V3.5 追補（Codex レビュー第 15 巡 P1 採用、2026-09-05）: gate4'
    # の designated anchor（`fixtures.controls.positive_controls_by_family()`
    # の family あたり 2 行）も、negative control と同じ「split 非依存の
    # 共有 control」として union する。理由: HMAC split で変異 CONFOUND 行が
    # HOLDOUT に home し、対応する anchor 行が CALIBRATION/SELECTION に home
    # した場合、`build_invariance_pairs_for_family()` は anchor 側の own
    # record が無いため pair を 1 件も作れず（`_per_instance_output_repeats`
    # が空を返す）gate4' が黙って全滅していた。anchor 行は TRUTH_CORE 行
    # として C1 で（home split が CALIBRATION/SELECTION の場合）既に render
    # 済みであり、HOLDOUT に home する場合は既存の `c4_holdout_instances()`
    # 母集団に既に含まれる——いずれの場合も本 union は seal/leakage 境界を
    # 動かさない（HOLDOUT 行の unseal 前露出は生じない。C1 で render 済みの
    # 行を C4 で測定するだけ）。
    anchor_row_ids_by_family = fixture_controls.positive_controls_by_family(matrix_rows)
    instances_by_family = {
        family: tuple(
            sorted(
                frozenset(workunits.c4_holdout_instances(matrix_rows, assignment, family=family))
                | fixture_controls.negative_control_instances(matrix_rows, family=family)
                | {
                    (row_id, probe_index)
                    for row_id in anchor_row_ids_by_family.get(family, ())
                    for probe_index in range(fixture_controls.PROBE_REPEATS)
                }
            )
        )
        for family, _candidates in family_order
    }

    if time_budget is None:
        for family, candidates in family_order:
            results[family] = measure_stage.run_measure_stage(
                campaign,
                instances_by_family[family],
                candidates,
                sr_by_row=sr_by_row,
                f0_by_instance=f0_by_instance,
                f0_unusable_instances=f0_unusable_instances,
                max_workers=max_workers,
                cap_counters=cap_counters,
                cost_caps=cost_caps,
                missing_reason=f0_missing_reason,
                discard_partial_groups=discard_partial_groups,
                stage="c4",
                invocation_id=invocation_id,
            )
        return results

    # Codex PR #345 round 9 finding ③ (adopted, category ③,
    # `[UNDERSPEC-CAL-D79]`, mirrors `cli._run_c3b()`'s identical fix for
    # C3b's per-family loop): a family already fully measured before this
    # invocation must not have `run_measure_stage()` called on it at all
    # here — that call's own trivial `completed_all=True` unconditionally
    # pays `_rebuild_skipped_records()`'s reconstruction cost on EVERY
    # resumed invocation for as long as some OTHER family (or the render
    # sub-phase above) stays pending, which can by itself exhaust the
    # slice's `time_budget` before a genuinely pending family is ever
    # dispatched (see `measure_stage.family_has_pending_work()`'s
    # docstring). Dispatch is attempted only for families this O(1)
    # pre-check finds genuinely pending; every family's full record set
    # (including families skipped here because they were already complete)
    # is reconstructed only in the completing pass below.
    precheck_index = measure_stage.MeterCallIndex.build(campaign.ledger.entries)
    # Codex PR #345 round 10 finding ② (adopted, category ②,
    # `[UNDERSPEC-CAL-D79]`, mirrors `cli._run_c3b()`'s identical fix): a
    # duplicate-key cell makes this precheck itself raise
    # `StaleMeasurementError` — before any of `run_measure_stage()`'s own
    # logging wrappers ever run — so without this `try`/`except` the ledger
    # would lack the `STALE_MEASUREMENT_STATE` stop_event explaining the
    # failed invocation. Explicit loop (not a list/generator comprehension)
    # so the raise is caught per family, same shared helper
    # `run_measure_stage()` itself uses.
    pending_families = []
    for family, candidates in family_order:
        try:
            has_pending = measure_stage.family_has_pending_work(
                instances_by_family[family], candidates, precheck_index, f0_unusable_instances
            )
        except measure_stage.StaleMeasurementError as exc:
            measure_stage._append_stale_measurement_stop_event(
                campaign, exc, invocation_id=invocation_id
            )
            raise
        if has_pending:
            pending_families.append((family, candidates))
    for family, candidates in pending_families:
        family_records, family_slice_status = measure_stage.run_measure_stage(
            campaign,
            instances_by_family[family],
            candidates,
            sr_by_row=sr_by_row,
            f0_by_instance=f0_by_instance,
            f0_unusable_instances=f0_unusable_instances,
            max_workers=max_workers,
            cap_counters=cap_counters,
            cost_caps=cost_caps,
            missing_reason=f0_missing_reason,
            discard_partial_groups=discard_partial_groups,
            stage="c4",
            time_budget=time_budget,
            invocation_id=invocation_id,
        )
        # round 9b fix (this call's own records are already computed above
        # at no extra cost — capture them rather than discarding, so a
        # PARTIAL_SLICE return still reflects real per-family progress for
        # every family this invocation actually dispatched. `cli._run_c4`
        # still discards `results` wholesale on `not completed_all` (its own
        # `overall_slice_status.completed_all` check runs first), so this
        # costs that caller nothing — but `render_and_measure_holdout()` is
        # a public function other direct callers (e.g.
        # `test_c4_render_phase_valid_marker_skips_rehash_on_measure_only_
        # slices`) rely on for real partial data on every slice, not only
        # the completing one. Families skipped above because they had zero
        # pending work before this invocation are deliberately NOT
        # reconstructed here (that is the cost this fix exists to skip) and
        # so stay absent from `results` on a PARTIAL_SLICE.
        results[family] = family_records
        slice_statuses.append(family_slice_status)

    overall_slice_status = SliceStatus.aggregate(slice_statuses)
    if not overall_slice_status.completed_all:
        return results, overall_slice_status

    # Completing invocation: every family's cells are now complete (already
    # complete before this call, or just completed by the dispatch loop
    # above) — reconstruct full records for EVERY family exactly once here.
    # Each call below is unsliced (no `time_budget`) and therefore
    # guaranteed cheap: every cell is already `is_complete()`, so it takes
    # the pure O(1) skip path with no new dispatch.
    for family, candidates in family_order:
        results[family] = measure_stage.run_measure_stage(
            campaign,
            instances_by_family[family],
            candidates,
            sr_by_row=sr_by_row,
            f0_by_instance=f0_by_instance,
            f0_unusable_instances=f0_unusable_instances,
            max_workers=max_workers,
            cap_counters=cap_counters,
            cost_caps=cost_caps,
            missing_reason=f0_missing_reason,
            discard_partial_groups=discard_partial_groups,
            stage="c4",
            invocation_id=invocation_id,
        )
    return results, overall_slice_status


def resolve_candidates(candidate_ids: Sequence[str]) -> tuple[Candidate, ...]:
    return tuple(candidate_by_id(cid) for cid in candidate_ids)


# ---------------------------------------------------------------------------
# HOLDOUT_EXECUTED_VALID event
# ---------------------------------------------------------------------------


class HoldoutCoverageError(RuntimeError):
    """finding #10: `run_holdout_stage` に渡された `results` が
    `vocab.MeterId` の全 7 値をちょうど 1 回ずつ含まない場合の fail-closed
    error（欠落・重複・未知の meter_id のいずれか）。"""


def _validate_meter_coverage(results: Sequence[MeterHoldoutResult]) -> None:
    seen = [r.meter_id for r in results]
    counts = Counter(seen)
    duplicates = sorted(m for m, n in counts.items() if n > 1)
    expected = {m.value for m in MeterId}
    missing = sorted(expected - set(seen))
    unexpected = sorted(set(seen) - expected)
    if duplicates or missing or unexpected:
        raise HoldoutCoverageError(
            "run_holdout_stage: results must cover exactly the "
            f"{len(expected)} vocab.MeterId values with no duplicates "
            f"(duplicates={duplicates!r}, missing={missing!r}, unexpected={unexpected!r})"
        )


def run_holdout_stage(
    campaign: FrozenCampaign,
    results: Sequence[MeterHoldoutResult],
    *,
    invocation_id: str | None = None,
) -> LedgerEntry:
    """全 meter の評価結果を単一 `holdout_executed_valid` event として記帳
    する（設計正本 §1: 手続 Gate は meter status とは別軸だが、本 event 自体
    は全 meter の終端 status + reason code を payload に持つ）。

    finding #10: 記帳前に `_validate_meter_coverage` で `results` の被覆を
    検証する（`per_meter` は `meter_id` キーの dict comprehension のため、
    検証なしでは重複が黙って上書きされ欠落を検出できない — fail-closed で
    `HoldoutCoverageError` を送出する）。
    """
    _validate_meter_coverage(results)
    per_meter = {
        r.meter_id: {
            "terminal_status": r.terminal_status,
            "reason_code": r.reason_code,
            "ceiling": r.ceiling,
            "selected_candidate_id": r.selected_candidate_id,
            "gate_detail": dict(r.gate_detail),
        }
        for r in results
    }
    return campaign.ledger.append(
        {
            "kind": "holdout_executed_valid",
            "per_meter": per_meter,
            "invocation_id": invocation_id,
        }
    )


__all__ = [
    "declared_axes_for_family",
    "declared_u_gt_u_num_for_family",
    "units_commensurate_for_family",
    "instance_id_str",
    "absolute_e_use_value",
    "GateInputError",
    "ControlDetection",
    "control_detection_for_family",
    "build_invariance_pairs_for_family",
    "AbsoluteGateInputBundle",
    "build_absolute_gate_inputs",
    "evaluate_absolute_meter_from_campaign",
    "directional_claim_shrinkage_detail",
    "DirectionalGateInputBundle",
    "build_directional_gate_inputs",
    "evaluate_directional_meter_from_campaign",
    "evaluate_m6_identity",
    "StaleEUseTableError",
    "load_e_use_rows",
    "split_e_use_rows_by_mode",
    "RawInstanceObservation",
    "build_instance_margins",
    "RawDirectionalObservation",
    "build_directional_pairs",
    "MeterHoldoutResult",
    "evaluate_absolute_meter",
    "evaluate_directional_meter",
    "diagnostic_only_close",
    "selection_failed_closed_meter",
    "render_and_measure_holdout",
    "resolve_candidates",
    "HoldoutCoverageError",
    "run_holdout_stage",
]
