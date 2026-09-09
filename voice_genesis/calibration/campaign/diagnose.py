"""C-1 探索ステージ（RUN10-CAL-v1.2 WP4、Design Memo「開発指針 1: 探索ステージを
本番の前に必ず置く」の実装）。

armed campaign（`c0_freeze`/`campaign.cli` の凍結・封印・ledger 記帳経路）へ
候補を入れる前に、freeze も封印も ledger も無い安い診断で「正例
（positive control = family の TRUTH_CORE 行）に発火し、負例
（negative control 行: SILENCE/NOISE_ONLY/... 等）に発火しない」ことを
先に確認する。目安 30 セル・30 分以内。**本モジュールの出力に claim 能力は
無い**（`claimable` は常に `False`）——armed campaign の C0 freeze / C3 selection
/ C4 holdout を経ていない測定は、どれだけ結果が綺麗でも校正証拠にならない。

## 実経路との差分（意図的な簡略化。§ 報告参照）

本モジュールは `campaign.render_stage`/`campaign.measure_stage.
run_measure_stage()`（ledger 記帳・cap 課金・within/fresh 二重プロセス
determinism 検査・`FrozenCampaign` 前提）を呼ばない。代わりに:

1. **render**: `fixtures.generators.render_row()` + `streams.derive_generator()`
   を直接呼ぶ（`campaign.render_stage`/`_render_worker.py` が使うのと同じ
   純粋関数だが、本経路は subprocess 二重生成 byte-identity 検査を行わない
   ——診断は「発火する/しない」だけを見る cheap gate であり、generator
   determinism の証明は C1 render stage の責務のまま）。secret は固定定数
   `_DIAGNOSE_SECRET`（実 campaign の per-campaign secret とは無関係、
   `~/.vg_cal/` には一切触れない）。
2. **measure**: `campaign.measure_stage.run_within_process_calls()`
   （within-process 直接呼び出し。実 campaign の fresh-process 二重検査・
   ledger 記帳・cost cap 課金は行わない）を `repeats=1` で呼ぶ
   （CLI `--repeats` は probe_index の本数であり、`run_within_process_calls`
   内部の repeat 数とは別軸）。
3. **F0 injection**（RUN10-CAL-v1.2 WP4b 改訂）: 実経路
   `campaign.cli._build_f0_by_instance()` は C3a で選定された F0 candidate
   の within/fresh 全 process 出力を集約（二段階中央値）し、finite かつ
   厳密正の instance だけを F0 依存候補へ注入する（`campaign.measure_stage.
   F0_DEPENDENT_ALGORITHM_FAMILIES`）。本モジュールは selection を経由
   しないため、どの F0 candidate が実 campaign の C3a で選定されるかを
   事前には知り得ない——`scratchpad/campaign/c3b_failclosed_analysis.md`
   §3.2 の実測どおり、選定される F0 candidate 次第で negative control 行の
   F0 使用可否（＝F0 依存候補が実際に呼ばれるか）が変わり、診断結果
   （特に negative fire）が変わり得る。そのため既定では **registry の
   F0_CONTROL 候補全件（`f0_registry_candidates()`、candidate_id 昇順）を
   1 件ずつ prepass に使って診断を掃引し**、候補ごとに結果を出す
   （出力 JSON は `results[].f0_candidate` で分ける。`f0_prepass` フィールド
   が `"swept"`）。`--f0-candidate <id>` で 1 件に固定できる
   （`f0_prepass: "single"`）。F0 依存候補が 1 件も無い診断対象
   （family=F0_CONTROL 自身を含む）では F0 prepass 自体を行わない
   （`f0_prepass: "not_applicable"`、`results` は 1 要素で
   `f0_candidate: null`）。F0 prepass は当該 F0 candidate を同じ signal に
   対して 1 回 within-process 実行した単一値をそのまま使う（実経路の
   二段階中央値集約ではなく単発値——診断は cheap gate であり集約統計量の
   再現は C3a selection stage の責務のまま）。F0 が使用不能（欠測 or
   非有限 or 非正）な instance では、実経路の「候補を一切呼ばない」skip
   挙動を模して候補呼び出し自体を省略し、`missing_reason="F0_UNUSABLE"`
   を合成する（`fixtures.controls.SANCTIONED_ABSTENTIONS` の判定——v1.4
   §前提 3 の 2 組 `{(SILENCE, "F0_UNUSABLE"),
   (NOISE_ONLY, "F0_UNUSABLE")}`——を意味のあるものにするため。実経路では
   これは `MeterOutput.missing_reason` ではなく `measurement_missing`
   ledger event の `reason` フィールドだが、ledger を持たない本モジュール
   では `CellOutcome.missing_reason` という別軸のラベルとして同じ役割を
   代替する）。
4. **selection/holdout/split/approval/ledger**: 一切経由しない。
   `campaigns/` にも `~/.vg_cal/` にも書き込まない（`--out` は任意パスへの
   診断結果 JSON 出力のみ）。

## 判定の意味論（RUN10-CAL-v1.2 WP4b 改訂）

`c3b_failclosed_analysis.md` §5.1 が指摘した「同じ design-sanctioned な
欠測を 2 つの filter が正反対に扱っている」実装バグ疑いと同型の穴が本
モジュールの初版（WP4）にもあった: negative control 行が F0_UNUSABLE で
丸ごとスキップされた（=候補が一度も呼ばれず record が皆無になった）とき、
`detected()` は欠落を一様に「非発火（False）」へ写像するため、
`(TOO_SHORT, "F0_UNUSABLE")` のような **非 sanctioned** な行欠測が
「negative fire rate 0.0 = clean」という偽の PASS を作れてしまっていた
（`fixtures.controls.SANCTIONED_ABSTENTIONS` は閉語彙であり、そこに無い
組は sanctioned にならない）。**v1.4 での更新**（PR #354 round 4 P2 是正）:
`SANCTIONED_ABSTENTIONS` は v1.4 §前提 3 で 2 組
`{(SILENCE, "F0_UNUSABLE"), (NOISE_ONLY, "F0_UNUSABLE")}` へ拡張された
ため、旧文が非 sanctioned の例に挙げていた `(NOISE_ONLY, "F0_UNUSABLE")`
は現在は **sanctioned** である（無声対照に F0 は存在しない）。本改訂が
閉じた穴の構造そのものは変わらない——閉語彙に無い組は依然として偽 PASS を
作らせない。

本改訂は `campaign.selection_stage.candidate_fail_filter_report()` の
`negative_controls_incomplete` filter と同じ意味論をとる: ある negative
control_class（本モジュールは control_class ごとに行を 1 本しか選ばない
——`select_diagnostic_cells()`——ため「行」と「control_class」は 1:1）の
全 probe が F0_UNUSABLE でスキップされ、かつその
`(ControlClass, "F0_UNUSABLE")` の組が `SANCTIONED_ABSTENTIONS` に無い
場合、その候補の verdict は当該 control_class の負例に対する判定材料が
存在しないという理由で `NOT_EVALUABLE`（`verdict_reason=
"negative_controls_incomplete"`）にする——PASS 側の判定材料としては
数えない。`PASS` は「全正例で detected、全負例で record があり
かつ非 detected（sanctioned abstention は非 detected 扱い）、
ceiling != NONE」の場合のみ。内訳は `negative_controls_incomplete_by_class`
（control_class -> 当該 class が非 sanctioned な行欠測だったか）に記録する。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import kendalltau

from voice_genesis.calibration.campaign import measure_stage
from voice_genesis.calibration.campaign.selection_stage import truth_value_for_row
from voice_genesis.calibration.candidates import registry
from voice_genesis.calibration.candidates.adapter import MeterOutput
from voice_genesis.calibration.candidates.registry import Candidate
from voice_genesis.calibration.fixtures import matrix
from voice_genesis.calibration.fixtures.axes import FixtureFamily
from voice_genesis.calibration.fixtures.controls import (
    SANCTIONED_ABSTENTIONS,
    ControlClass,
    abstained,
    detected,
)
from voice_genesis.calibration.fixtures.generators import common as gen_common
from voice_genesis.calibration.fixtures.generators import render_row
from voice_genesis.calibration.fixtures.matrix import FixtureRow, MatrixRow
from voice_genesis.calibration.streams import derive_generator
from voice_genesis.calibration.vocab import ClaimCeiling, MeterId

# ---------------------------------------------------------------------------
# family -> meter（他 module — 並行編集中の `campaign/cli.py` を含む — には
# 依存せず本モジュールで独立に宣言する。`matrix.py` 冒頭の既存規約
# 「他 agent が並行編集中の module には依存せず独立に宣言する」と同じ理由）。
# `cli._FAMILY_TO_METER` と値は同一だが import 元は別（IDENTITY_CAUSAL_SWEEP
# は M6 が cross-meter distance のため対応する fixture-diagnosable meter を
# 持たず、`cli._FAMILY_TO_METER` にも本表にも現れない——
# `campaign/holdout_stage.py` docstring 「`cli._FAMILY_TO_METER` は
# IDENTITY_CAUSAL_SWEEP を全く含まず」と同じ事実を独立に転記）。
# ---------------------------------------------------------------------------

FAMILY_TO_METER: Mapping[str, MeterId] = {
    FixtureFamily.F0_CONTROL.value: MeterId.F0_CONTROL,
    FixtureFamily.FORMANT_GT.value: MeterId.M3_FORMANTS,
    FixtureFamily.TILT_GT.value: MeterId.M2_SPECTRAL_TILT,
    FixtureFamily.APERIODICITY_GT.value: MeterId.M2_APERIODICITY,
    FixtureFamily.RESONANCE_GT.value: MeterId.M4_RESONANCE,
    FixtureFamily.TRANSITION_GT.value: MeterId.M5_TRANSITION,
}


def f0_registry_candidates() -> tuple[Candidate, ...]:
    """`--f0-candidate` 省略時に既定で掃引する F0 prepass 候補集合
    （`MeterId.F0_CONTROL` の registry 全候補、candidate_id 昇順で決定論）。"""
    return tuple(
        sorted(registry.candidates_for_meter(MeterId.F0_CONTROL), key=lambda c: c.candidate_id)
    )


#: negative control 行が実 candidate 呼び出しを一切スキップした場合に合成する
#: missing_reason ラベル（実経路の `measurement_missing` ledger event の
#: `reason="F0_UNUSABLE"` に対応。`vocab.MissingReason` の閉語彙には属さない
#: ——`campaign/measure_stage.py` docstring 参照）。
F0_UNUSABLE_REASON = "F0_UNUSABLE"

#: 診断 render 用の固定 seed（campaign secret ではない。`~/.vg_cal/` に触れず、
#: 同一入力で常に同一波形を再現するための公開定数）。
DIAGNOSE_SEED = 0
_DIAGNOSE_SECRET = hashlib.sha256(
    f"voice_genesis.calibration.campaign.diagnose/v1/seed={DIAGNOSE_SEED}".encode("utf-8")
).digest()
_DIAGNOSE_CAMPAIGN_ID = "diagnose"
_DIAGNOSE_SPLIT = "DIAGNOSE"

#: v0.2 (RUN10-CAL-v1.2 WP4b): F0 候補掃引対応で top-level 形状が変わった
#: （旧 `candidates`/`elapsed_seconds` は `results[]` の各要素へ移動し、
#: `f0_prepass`/`results[].f0_candidate` が新設）。
#: v0.3 (RUN10-CAL-v1.3 段階 2): `--dump-values` 指定時のみ
#: `results[].candidates[].cell_values` を追加（セルごとの生 `values` 全
#: フィールド + `missing_reason`/`ineligible`/`detected`）。既定（フラグ
#: 無し）の出力形状は v0.2 と同一——分離実測のための追加情報であり、
#: verdict や fire rate の意味論は一切変えない。
#: v0.4 (RUN10-CAL-v1.4 §前提 6): `--dump-values` 指定時のみ
#: `results[].candidates[].census`（control class x outcome 件数表。P2
#: 棄権 census の内製化）と `results[].candidates[].kendall_tau_sign`
#: （DIRECTIONAL 候補のみ、正例セルの (truth, primary_output) 対の
#: Kendall tau-b 符号。P3 極性 census の内製化）を追加する。既定（フラグ
#: 無し）の出力形状は v0.2/v0.3 と同一——追加情報のみで verdict や fire
#: rate の意味論は一切変えない。
#: v0.5 (PR #354 round 5 finding #2, 2026-09-09): `evaluate_candidate()` は
#: 従来 `detected()` 経由で undeclared missing_reason/ineligible な負例
#: record を無条件に非発火へ写像し、PASS まで抜けてしまう経路を持っていた
#: （`fixtures.controls.abstained()` による宣言/未宣言の弁別が verdict に
#: 反映されていなかった）。v0.5 で `verdict="FAIL_NEGATIVE"` かつ
#: `verdict_reason="UNDECLARED_NEGATIVE_MISS"` の組が新たに現れ得る
#: （closed verdict vocabulary は不変、`verdict_reason` が FAIL_NEGATIVE でも
#: 非 `None` になり得る点のみ形状が変わる）。宣言済み棄権（`abstained()`
#: が `True` を返す record）は従来どおり非発火 → PASS-eligible のまま。
SCHEMA = "diagnose/0.5"

_ROLE_POSITIVE = "positive"
_ROLE_NEGATIVE = "negative"
_ROLE_CONFOUND = "confound"


# ---------------------------------------------------------------------------
# セル選抜（決定論、手選び禁止）
# ---------------------------------------------------------------------------


def select_diagnostic_cells(family_value: str, max_cells: int) -> list[tuple[MatrixRow, str]]:
    """`fixtures.matrix.build_matrix()` の当該 family 行から、決定論的な
    4 規則 (a)-(c) で診断セルを選ぶ（手選びなし。列挙順は `build_matrix()`
    の順序をそのまま保存する）。

    (a) positive: TRUTH_CORE を truth level（`matrix.truth_identity_for_row()`）
        別に先頭 1 行。母集団は `max_cells // 2` を上限とする。
    (b) negative: `control_class is not None` の行を control_class ごとに
        先頭 1 行（`max_cells - len(positive)` を上限とする——通常 family
        あたり 2-3 control class しか無いため実際にはこの上限に達しない）。
    (c) 余りがあれば `single_axis_nuisance_tag_axis(row) is not None` の
        CONFOUND 行を先頭から詰める。

    戻り値は `(MatrixRow, selection_rule)` のリスト（`selection_rule` は
    `"positive"`/`"negative"`/`"confound"`）。合計は常に `<= max_cells`。
    """
    full = matrix.build_matrix()
    family_rows = [mr for mr in full if mr.row.family == family_value]

    positive_budget = max_cells // 2
    positive: list[tuple[MatrixRow, str]] = []
    seen_truth_levels: set[tuple[Any, ...]] = set()
    for mr in family_rows:
        if len(positive) >= positive_budget:
            break
        if mr.row.block != "TRUTH_CORE":
            continue
        level = matrix.truth_identity_for_row(mr.row)
        if level in seen_truth_levels:
            continue
        seen_truth_levels.add(level)
        positive.append((mr, _ROLE_POSITIVE))

    chosen_ids = {mr.row_id for mr, _ in positive}
    negative_budget = max(0, max_cells - len(positive))
    negative: list[tuple[MatrixRow, str]] = []
    seen_control_classes: set[str] = set()
    for mr in family_rows:
        if len(negative) >= negative_budget:
            break
        cc = mr.row.control_class
        if cc is None or cc in seen_control_classes:
            continue
        seen_control_classes.add(cc)
        negative.append((mr, _ROLE_NEGATIVE))
        chosen_ids.add(mr.row_id)

    confound_budget = max(0, max_cells - len(positive) - len(negative))
    confound: list[tuple[MatrixRow, str]] = []
    if confound_budget > 0:
        for mr in family_rows:
            if len(confound) >= confound_budget:
                break
            if mr.row_id in chosen_ids:
                continue
            if mr.row.block != "CONFOUND":
                continue
            if matrix.single_axis_nuisance_tag_axis(mr.row) is None:
                continue
            confound.append((mr, _ROLE_CONFOUND))
            chosen_ids.add(mr.row_id)

    return positive + negative + confound


# ---------------------------------------------------------------------------
# render + F0 prepass + measure（最下層 API 直呼び。ledger/campaign を経由しない）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CellOutcome:
    """1 (row, probe_index) × 1 candidate の診断結果。`missing_reason` は
    `output.missing_reason.value` か、F0-unusable skip 由来の合成ラベル
    (`F0_UNUSABLE_REASON`) のいずれか（両方とも無ければ `None`）。

    `row_id`/`probe_index` は `--dump-values`（schema v0.3）でセルを識別
    するためだけの注記であり、判定（fire rate・verdict）には一切使わない
    ——既定 `None` で、合成 `CellOutcome` を使う純関数テストは従来どおり
    省略できる。"""

    role: str
    control_class: str | None
    output: MeterOutput
    missing_reason: str | None
    row_id: str | None = None
    probe_index: int | None = None
    #: RUN10-CAL-v1.4 §前提 6 (`DESIGN_VG_METER_CAL_DEBT_v1.4.md`): 正例
    #: セルの truth スカラー（`selection_stage.truth_value_for_row()`）。
    #: `evaluate_candidate()` の DIRECTIONAL `kendall_tau_sign` 算出専用
    #: （fire rate/verdict の判定には使わない）。既定 `None` で、合成
    #: `CellOutcome` を使う既存の純関数テストは従来どおり省略できる。
    truth: float | None = None


def render_diagnose_signal(row: FixtureRow, row_id: str, probe_index: int) -> tuple[np.ndarray, int]:
    """`row` を 1 probe 分 render し `[-1, 1]` 正規化 float64 signal を返す
    （`fixtures.generators.render_row` + `streams.derive_generator` の直呼び。
    secret は固定公開定数 `_DIAGNOSE_SECRET` ——campaign secret ではない）。"""
    rng = derive_generator(
        _DIAGNOSE_SECRET,
        campaign_id=_DIAGNOSE_CAMPAIGN_ID,
        family=row.family,
        split=_DIAGNOSE_SPLIT,
        row_id=row_id,
        probe_index=probe_index,
        purpose="generator",
    )
    pcm = render_row(row, rng)
    return measure_stage.pcm_bytes_to_signal(gen_common.pcm16_bytes(pcm), row.sr_hz)


def resolve_f0_prepass(
    f0_candidate: Candidate, signal: np.ndarray, sr: int, row_id: str, probe_index: int
) -> float | None:
    """`f0_candidate`（呼び出し側が選んだ registry の F0_CONTROL 候補
    ——`--f0-candidate` 指定値、または `f0_registry_candidates()` 掃引中の
    1 件）を `signal` 上で 1 回 within-process 実行し、finite かつ厳密正の
    f0 推定値を返す（`campaign.cli._build_f0_by_instance()` の
    finite/strictly-positive guard を単一候補・単一 call に単純化したもの。
    実経路の within/fresh 二段階中央値集約はしない）。使用不能なら `None`。"""
    records = measure_stage.run_within_process_calls(
        f0_candidate, signal, sr, f0_hz=None, row_id=row_id, probe_index=probe_index, repeats=1
    )
    output = records[0].output
    if output.ineligible or output.missing_reason is not None:
        return None
    value = output.values.get("f0_hz")
    if value is None or not math.isfinite(value) or value <= 0.0:
        return None
    return float(value)


def needs_f0_injection(candidate: Candidate) -> bool:
    return candidate.algorithm_family in measure_stage.F0_DEPENDENT_ALGORITHM_FAMILIES


def measure_cell(
    candidate: Candidate,
    role: str,
    control_class: str | None,
    signal: np.ndarray,
    sr: int,
    f0_hz: float | None,
    row_id: str,
    probe_index: int,
    truth: float | None = None,
) -> CellOutcome:
    """1 (row, probe_index) × 1 candidate の測定。F0 依存候補で `f0_hz` が
    使用不能なら、実経路の skip 挙動（候補を一切呼ばない）を模して呼び出し
    自体を省略し `F0_UNUSABLE_REASON` を合成する。`truth`（既定 `None`）は
    `CellOutcome.truth` へそのまま渡す（v1.4 §前提 6 の DIRECTIONAL
    `kendall_tau_sign` 専用、判定には使わない）。"""
    if needs_f0_injection(candidate) and f0_hz is None:
        return CellOutcome(
            role, control_class, MeterOutput(), F0_UNUSABLE_REASON, row_id, probe_index,
            truth=truth,
        )
    records = measure_stage.run_within_process_calls(
        candidate,
        signal,
        sr,
        f0_hz=f0_hz if needs_f0_injection(candidate) else None,
        row_id=row_id,
        probe_index=probe_index,
        repeats=1,
    )
    output = records[0].output
    reason = output.missing_reason.value if output.missing_reason is not None else None
    return CellOutcome(role, control_class, output, reason, row_id, probe_index, truth=truth)


# ---------------------------------------------------------------------------
# 判定（純関数。合成 MeterOutput でテスト可能——render/measure を経由しない）
# ---------------------------------------------------------------------------


def _rate(flags: Sequence[bool]) -> float | None:
    if not flags:
        return None
    return sum(1 for f in flags if f) / len(flags)


def _round_or_none(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


#: PR #354 round 5 finding #2: distinct `verdict_reason` for a negative cell
#: whose present output carries an undeclared `missing_reason`/`ineligible`
#: (`fixtures.controls.abstained()` is `False`) — the shared distinction with
#: `campaign.selection_stage.negative_control_undeclared_missing`. Kept apart
#: from the plain `FAIL_NEGATIVE` (an actual `detected()` fire) so the report
#: names which of the two negative-side failure shapes occurred.
UNDECLARED_NEGATIVE_MISS = "UNDECLARED_NEGATIVE_MISS"


def _verdict(
    ceiling: ClaimCeiling,
    positive_rate: float | None,
    negative_rate: float | None,
    all_ineligible: bool,
    incomplete_classes: frozenset[str],
    has_undeclared_negative_miss: bool = False,
) -> tuple[str, str | None]:
    """`(verdict, verdict_reason)`。`verdict_reason` は `NOT_EVALUABLE` の
    3 分岐（判定材料が無い / 全 ineligible / 非 sanctioned な負例欠測）を
    区別する（WP4b 改訂: 3 番目の `negative_controls_incomplete` が新設）。
    PASS/FAIL_POSITIVE/NO_CEILING では常に `None`。

    PR #354 round 5 finding #2（2026-09-09）: `FAIL_NEGATIVE` は 2 分岐を
    持つようになった——`negative_rate > 0.0`（`detected()` が実際に発火した
    record が 1 件以上）の従来経路は `verdict_reason=None` のまま、新設の
    `has_undeclared_negative_miss`（`abstained()` で説明されない
    missing_reason/ineligible な負例 record が 1 件以上）は
    `verdict_reason="UNDECLARED_NEGATIVE_MISS"` を持つ。`detected()` は
    missing_reason/ineligible な record を無条件に非発火へ写像するため
    `negative_rate` 自体はこの経路を検出できない——`has_undeclared_
    negative_miss` は `evaluate_candidate()` が `abstained()` で別途算出する
    独立した入力。`positive_rate < 1.0`（FAIL_POSITIVE）は本分岐より先に
    判定する（`campaign.selection_stage` の positive-control 非発火判定と
    同様、positive 側の失敗を優先して報告する）。"""
    if positive_rate is None or negative_rate is None:
        return "NOT_EVALUABLE", "no_positive_or_negative_rows"
    if all_ineligible:
        return "NOT_EVALUABLE", "all_ineligible"
    if incomplete_classes:
        return "NOT_EVALUABLE", "negative_controls_incomplete"
    if positive_rate < 1.0:
        return "FAIL_POSITIVE", None
    if has_undeclared_negative_miss:
        return "FAIL_NEGATIVE", UNDECLARED_NEGATIVE_MISS
    if negative_rate > 0.0:
        return "FAIL_NEGATIVE", None
    if ceiling == ClaimCeiling.NONE:
        return "NO_CEILING", None
    return "PASS", None


def _cell_value_entry(candidate: Candidate, outcome: CellOutcome) -> dict[str, Any]:
    """`--dump-values`（schema v0.3）用の 1 セル分の生記録。`values` は
    `MeterOutput.values` を丸めずそのまま写す（分離閾値の実測が用途のため。
    `diagnostics` は主張に使えない補助情報なので含めない）。"""
    return {
        "row_id": outcome.row_id,
        "probe_index": outcome.probe_index,
        "role": outcome.role,
        "control_class": outcome.control_class,
        "values": {k: float(v) for k, v in sorted(outcome.output.values.items())},
        "missing_reason": outcome.missing_reason,
        "ineligible": bool(outcome.output.ineligible),
        "ineligible_reason": outcome.output.ineligible_reason,
        "detected": detected(outcome.output, predicate=candidate.detection_predicate),
    }


def _census_block(
    candidate: Candidate, outcomes: Sequence[CellOutcome]
) -> dict[str, dict[str, Any]]:
    """RUN10-CAL-v1.4 §前提 6 (`DESIGN_VG_METER_CAL_DEBT_v1.4.md`):
    control class（負例）/ `"positive"` / `"confound"` ごとの outcome 件数表
    （`--dump-values` schema v0.4 専用）。P2 棄権 census
    (`scratchpad/v14/p23/p23_report.txt` §2 の外部集計スクリプト
    `analyze_p23.py` が出していた表と同じ語彙）を schema へ内製化したもの:

    - `f0_unusable_prepass_skip`: 前提 2 経路 (A)（F0 prepass skip、record
      皆無。合成 `F0_UNUSABLE_REASON`）の件数。
    - `missing_reason`: 前提 2 経路 (B)（`MeterOutput.missing_reason`、
      record あり）の理由別件数。`f0_unusable_prepass_skip` とは排他
      （前者は record が無いため `missing_reason` に現れない）。
    - `ineligible`: `MeterOutput.ineligible` の件数（`missing_reason` とは
      排他 — `MeterOutput.__post_init__` 相当の規約どおり同時に立たない）。
    - `measured_detected`/`measured_not_detected`: 上記いずれにも該当しない
      record の `fixtures.controls.detected()` 結果。
    """
    groups: dict[str, list[CellOutcome]] = {}
    for outcome in outcomes:
        key = outcome.control_class if outcome.role == _ROLE_NEGATIVE else outcome.role
        groups.setdefault(key, []).append(outcome)

    census: dict[str, dict[str, Any]] = {}
    for key, group_outcomes in sorted(groups.items()):
        prepass_skip = 0
        ineligible = 0
        measured_detected = 0
        measured_not_detected = 0
        missing_reason_counts: Counter[str] = Counter()
        for o in group_outcomes:
            if o.missing_reason == F0_UNUSABLE_REASON:
                prepass_skip += 1
                continue
            if o.output.ineligible:
                ineligible += 1
                continue
            if o.missing_reason is not None:
                missing_reason_counts[o.missing_reason] += 1
                continue
            if detected(o.output, predicate=candidate.detection_predicate):
                measured_detected += 1
            else:
                measured_not_detected += 1
        census[key] = {
            "n_cells": len(group_outcomes),
            "measured_detected": measured_detected,
            "measured_not_detected": measured_not_detected,
            "f0_unusable_prepass_skip": prepass_skip,
            "missing_reason": dict(sorted(missing_reason_counts.items())),
            "ineligible": ineligible,
        }
    return census


def _kendall_tau_sign(candidate: Candidate, outcomes: Sequence[CellOutcome]) -> int | None:
    """RUN10-CAL-v1.4 §前提 6: DIRECTIONAL 候補について、正例セルの
    `(truth, primary_output)` 対の Kendall tau-b の符号（`+1`/`-1`/`0`）を
    返す（P3 極性 census, `scratchpad/v14/p23/p23_report.txt` §5.4 の
    `analyze_p23.py` 集計と同じ算出をここに内製化する）。DIRECTIONAL 以外の
    候補、または算出条件（distinct truth/value がそれぞれ >= 2 件）を
    満たさない場合は `None`。**記録専用**（`registry.Candidate.
    truth_polarity` の宣言判定・PASS 判定のいずれにも使わない — `--dump-
    values` の診断出力のみ）。"""
    if candidate.claim_ceiling is not ClaimCeiling.DIRECTIONAL:
        return None
    truths: list[float] = []
    values: list[float] = []
    for o in outcomes:
        if o.role != _ROLE_POSITIVE or o.truth is None:
            continue
        value = measure_stage.primary_output_value(candidate, o.output)
        if value is None or not math.isfinite(value):
            continue
        truths.append(o.truth)
        values.append(value)
    if len(truths) < 2 or len(set(truths)) < 2 or len(set(values)) < 2:
        return None
    tau, _p_value = kendalltau(truths, values)
    if tau is None or not math.isfinite(float(tau)):
        return None
    tau = float(tau)
    if tau > 0:
        return 1
    if tau < 0:
        return -1
    return 0


def evaluate_candidate(
    candidate: Candidate, outcomes: Sequence[CellOutcome], *, dump_values: bool = False
) -> dict[str, Any]:
    """`outcomes`（`measure_cell()` の結果列、または合成 `CellOutcome`）から
    1 候補分の診断レポートを組み立てる純関数。CONFOUND cell は
    `missing_by_reason` にのみ寄与し、positive/negative fire rate・
    verdict の分母には含めない。

    WP4b 改訂: 本モジュールは control_class ごとに行を 1 本しか選ばない
    （`select_diagnostic_cells()`）ため「行」と「control_class」は 1:1。
    ある negative control_class の全 outcome が F0_UNUSABLE スキップ合成
    （`F0_UNUSABLE_REASON`）で、かつ `(ControlClass, "F0_UNUSABLE")` の組が
    `SANCTIONED_ABSTENTIONS` に無ければ、その class は「判定材料が消えた」
    non-sanctioned な行欠測として `incomplete_classes` へ入れ、verdict を
    `NOT_EVALUABLE(negative_controls_incomplete)` にする
    （`campaign.selection_stage.candidate_fail_filter_report()` の
    `negative_controls_incomplete` filter と同じ意味論）。sanctioned な行
    （v1.4 §前提 3 の 2 組 `{(SILENCE, "F0_UNUSABLE"),
    (NOISE_ONLY, "F0_UNUSABLE")}`）はここでの `sanctioned_
    abstentions` に数え、not-fired（False）として fire rate に算入する
    （従来どおり）。

    PR #354 round 5 finding #2（2026-09-09）: 上記の path (A)（行丸ごと
    skip）とは別に、path (B)（record は存在するが `missing_reason`/
    `ineligible` を持つ）の negative record を `fixtures.controls.
    abstained()` で宣言済み/未宣言に弁別する。`detected()` は両者を
    区別なく非発火へ写像するため、従来は未宣言の欠落・ineligible も
    `negative_rate == 0.0` に埋もれて `PASS` まで抜け得た
    （`campaign.selection_stage.negative_control_undeclared_missing` が
    selection 側で閉じていた穴の diagnose 側対応）。1 件でも未宣言なら
    `verdict="FAIL_NEGATIVE"`, `verdict_reason="UNDECLARED_NEGATIVE_MISS"`
    （`UNDECLARED_NEGATIVE_MISS` 定数）。宣言済み棄権（`abstained()` が
    `True`）は従来どおり non-fire → PASS-eligible のまま。"""
    positive_flags: list[bool] = []
    negative_outcomes_by_class: dict[str, list[CellOutcome]] = {}
    missing_by_reason: Counter[str] = Counter()
    verdict_relevant_total = 0
    verdict_relevant_ineligible = 0

    for outcome in outcomes:
        if outcome.missing_reason is not None:
            missing_by_reason[outcome.missing_reason] += 1
        if outcome.role == _ROLE_CONFOUND:
            continue
        verdict_relevant_total += 1
        if outcome.output.ineligible:
            verdict_relevant_ineligible += 1
        if outcome.role == _ROLE_POSITIVE:
            positive_flags.append(detected(outcome.output, predicate=candidate.detection_predicate))
        else:
            cc = outcome.control_class
            if cc is None:
                raise ValueError("evaluate_candidate: negative-role outcome missing control_class")
            negative_outcomes_by_class.setdefault(cc, []).append(outcome)

    negative_fire_by_control_class: dict[str, float | None] = {}
    negative_controls_incomplete_by_class: dict[str, bool] = {}
    incomplete_classes: set[str] = set()
    sanctioned_abstentions = 0
    negative_flags_all: list[bool] = []
    has_undeclared_negative_miss = False

    for cc, class_outcomes in sorted(negative_outcomes_by_class.items()):
        flags = [
            detected(o.output, predicate=candidate.detection_predicate) for o in class_outcomes
        ]
        negative_flags_all.extend(flags)
        negative_fire_by_control_class[cc] = _round_or_none(_rate(flags))

        row_all_skipped = all(o.missing_reason == F0_UNUSABLE_REASON for o in class_outcomes)
        sanctioned = row_all_skipped and (
            ControlClass(cc),
            F0_UNUSABLE_REASON,
        ) in SANCTIONED_ABSTENTIONS
        negative_controls_incomplete_by_class[cc] = row_all_skipped and not sanctioned
        if row_all_skipped:
            if sanctioned:
                sanctioned_abstentions += 1
            else:
                incomplete_classes.add(cc)
        # PR #354 round 5 finding #2: `detected()` maps every missing_reason/
        # ineligible record to non-fire unconditionally (declared or not), so
        # `flags`/`negative_flags_all` alone cannot see an undeclared miss —
        # apply the shared `fixtures.controls.abstained()` distinction here,
        # per-record, mirroring `campaign.selection_stage.negative_control_
        # undeclared_missing`. `row_all_skipped` outcomes are the synthetic
        # F0-unusable-prepass-skip `CellOutcome` (`MeterOutput()` default:
        # `missing_reason=None`, `ineligible=False`) so they never satisfy the
        # `or` guard below and this loop cannot double-count path (A).
        for o in class_outcomes:
            if (o.output.missing_reason is not None or o.output.ineligible) and not abstained(
                o.output, candidate
            ):
                has_undeclared_negative_miss = True

    positive_rate = _rate(positive_flags)
    negative_rate = _rate(negative_flags_all)
    all_ineligible = verdict_relevant_total > 0 and verdict_relevant_ineligible == verdict_relevant_total
    verdict, verdict_reason = _verdict(
        candidate.claim_ceiling,
        positive_rate,
        negative_rate,
        all_ineligible,
        frozenset(incomplete_classes),
        has_undeclared_negative_miss,
    )

    report: dict[str, Any] = {
        "candidate_id": candidate.candidate_id,
        "ceiling": candidate.claim_ceiling.value,
        "positive_fire_rate": _round_or_none(positive_rate),
        "negative_fire_rate": _round_or_none(negative_rate),
        "negative_fire_by_control_class": negative_fire_by_control_class,
        "negative_controls_incomplete_by_class": dict(
            sorted(negative_controls_incomplete_by_class.items())
        ),
        "sanctioned_abstentions": sanctioned_abstentions,
        "missing_by_reason": dict(sorted(missing_by_reason.items())),
        "verdict": verdict,
        "verdict_reason": verdict_reason,
    }
    if dump_values:
        report["cell_values"] = [_cell_value_entry(candidate, o) for o in outcomes]
        # RUN10-CAL-v1.4 §前提 6: census block（control class x outcome 件数
        # 表）+ DIRECTIONAL 候補の kendall_tau_sign（schema v0.4）。
        report["census"] = _census_block(candidate, outcomes)
        report["kendall_tau_sign"] = _kendall_tau_sign(candidate, outcomes)
    return report


# ---------------------------------------------------------------------------
# 全体オーケストレーション
# ---------------------------------------------------------------------------


def _serialize_cells(cells: Sequence[tuple[MatrixRow, str]]) -> list[dict[str, Any]]:
    return [
        {
            "row_id": mr.row_id,
            "block": mr.row.block,
            "control_class": mr.row.control_class,
            "selection_rule": role,
        }
        for mr, role in cells
    ]


def run_diagnosis_for_f0_candidate(
    family_value: str,
    candidates: Sequence[Candidate],
    cells: Sequence[tuple[MatrixRow, str]],
    repeats: int,
    f0_candidate: Candidate | None,
    dump_values: bool = False,
) -> dict[str, Any]:
    """`cells`（`select_diagnostic_cells()` の戻り値、呼び出し側で 1 回だけ
    選抜し F0 候補間で共有する——セル選抜は F0 に依存しないため）を、単一の
    F0 prepass 候補 `f0_candidate` の下で render + measure + 判定する。
    `candidates` のいずれも `needs_f0_injection()` でなければ `f0_candidate`
    は使われない（`None` で構わない）。"""
    started = time.monotonic()

    signal_cache: dict[tuple[str, int], tuple[np.ndarray, int]] = {}
    f0_cache: dict[tuple[str, int], float | None] = {}

    def _signal_for(mr: MatrixRow, probe_index: int) -> tuple[np.ndarray, int]:
        key = (mr.row_id, probe_index)
        cached = signal_cache.get(key)
        if cached is None:
            cached = render_diagnose_signal(mr.row, mr.row_id, probe_index)
            signal_cache[key] = cached
        return cached

    def _f0_for(mr: MatrixRow, probe_index: int) -> float | None:
        if f0_candidate is None:
            raise ValueError(
                "run_diagnosis_for_f0_candidate: f0_candidate is required when a "
                "candidate needs F0 injection"
            )
        key = (mr.row_id, probe_index)
        if key not in f0_cache:
            signal, sr = _signal_for(mr, probe_index)
            f0_cache[key] = resolve_f0_prepass(f0_candidate, signal, sr, mr.row_id, probe_index)
        return f0_cache[key]

    outcomes_by_candidate: dict[str, list[CellOutcome]] = {c.candidate_id: [] for c in candidates}
    for mr, role in cells:
        control_class = mr.row.control_class if role == _ROLE_NEGATIVE else None
        # RUN10-CAL-v1.4 §前提 6: truth スカラー（positive セルのみ意味を
        # 持つ。負例/confound 行は `truth_value_for_row()` が `None` を返す
        # ため、そのまま `CellOutcome.truth=None` になる）。
        truth = truth_value_for_row(mr.row)
        for probe_index in range(repeats):
            signal, sr = _signal_for(mr, probe_index)
            for candidate in candidates:
                f0_hz = _f0_for(mr, probe_index) if needs_f0_injection(candidate) else None
                outcome = measure_cell(
                    candidate, role, control_class, signal, sr, f0_hz, mr.row_id, probe_index,
                    truth=truth,
                )
                outcomes_by_candidate[candidate.candidate_id].append(outcome)

    candidate_reports = [
        evaluate_candidate(
            candidate, outcomes_by_candidate[candidate.candidate_id], dump_values=dump_values
        )
        for candidate in candidates
    ]
    elapsed = time.monotonic() - started

    return {
        "f0_candidate": f0_candidate.candidate_id if f0_candidate is not None else None,
        "candidates": candidate_reports,
        "elapsed_seconds": round(elapsed, 3),
    }


def run_diagnosis(
    family_value: str,
    candidates: Sequence[Candidate],
    max_cells: int,
    repeats: int,
    f0_candidate_id: str | None = None,
    dump_values: bool = False,
) -> dict[str, Any]:
    """全体オーケストレーション（CLI のエントリポイント）。`candidates` の
    いずれかが `needs_f0_injection()` なら、F0 prepass 候補を掃引する:
    `f0_candidate_id` 指定時はその 1 件のみ（`f0_prepass: "single"`）、
    省略時は `f0_registry_candidates()` の全件（`f0_prepass: "swept"`、
    RUN10-CAL-v1.2 WP4b — 実 campaign の C3a が選ぶ F0 候補次第で negative
    control 行の F0 使用可否が変わるため、既定は 1 候補に決め打ちしない）。
    F0 依存候補が 1 件も無ければ prepass 自体を省略する
    （`f0_prepass: "not_applicable"`、`results` は `f0_candidate: null` の
    1 要素）。セル選抜は F0 候補に依存しないため 1 回だけ行い、全 F0 候補
    ですべて共有する（出力 `cells` は掃引の外側に 1 つだけ持つ）。"""
    cells = select_diagnostic_cells(family_value, max_cells)
    needs_any_injection = any(needs_f0_injection(c) for c in candidates)

    f0_candidates: tuple[Candidate | None, ...]
    if not needs_any_injection:
        f0_prepass = "not_applicable"
        f0_candidates = (None,)
    elif f0_candidate_id is not None:
        f0_prepass = "single"
        f0_candidates = (registry.candidate_by_id(f0_candidate_id),)
    else:
        f0_prepass = "swept"
        f0_candidates = f0_registry_candidates()

    results = [
        run_diagnosis_for_f0_candidate(
            family_value, candidates, cells, repeats, f0_candidate, dump_values=dump_values
        )
        for f0_candidate in f0_candidates
    ]

    return {
        "schema": SCHEMA,
        "family": family_value,
        "cells": _serialize_cells(cells),
        "f0_prepass": f0_prepass,
        "results": results,
        "claimable": False,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def candidates_for_family(family_value: str) -> tuple[Candidate, ...]:
    """`--candidate` 省略時の既定候補集合（当該 family の registry 全候補）。
    `FAMILY_TO_METER` に無い family（IDENTITY_CAUSAL_SWEEP）は空集合。"""
    meter = FAMILY_TO_METER.get(family_value)
    if meter is None:
        return ()
    return registry.candidates_for_meter(meter)


def _print(obj: object) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m voice_genesis.calibration.campaign.diagnose",
        description=(
            "armed campaign 投入前の C-1 探索ステージ: freeze/封印/ledger なしで"
            "正例発火・負例不発火を安く確認する（claim 不可）。"
        ),
    )
    parser.add_argument(
        "--family", required=True, choices=[f.value for f in FixtureFamily], help="診断対象 fixture family"
    )
    parser.add_argument(
        "--candidate",
        action="append",
        default=None,
        help="診断対象 candidate_id（複数指定可。省略時は当該 family の registry 全候補）",
    )
    parser.add_argument("--max-cells", type=int, default=30, help="診断セル数の上限（既定 30）")
    parser.add_argument("--repeats", type=int, default=1, help="セルあたりの probe 数（既定 1）")
    parser.add_argument(
        "--f0-candidate",
        default=None,
        help=(
            "F0 prepass に固定する registry F0_CONTROL candidate_id（省略時は "
            "f0_registry_candidates() の全件を掃引し候補ごとに結果を出す。F0 依存"
            "候補が対象に無ければ無視される）"
        ),
    )
    parser.add_argument(
        "--dump-values",
        action="store_true",
        help=(
            "各候補レポートに cell_values（セルごとの生 values 全フィールド + "
            "missing_reason/ineligible/detected）を含める（schema v0.3。既定は"
            "従来どおり含めない——判定の意味論は変わらない）"
        ),
    )
    parser.add_argument("--out", type=Path, default=None, help="結果 JSON の出力先（省略時 stdout）")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if args.max_cells < 1:
        _print({"result": "ERROR", "detail": f"--max-cells must be >= 1, got {args.max_cells!r}"})
        return 1
    if args.repeats < 1:
        _print({"result": "ERROR", "detail": f"--repeats must be >= 1, got {args.repeats!r}"})
        return 1

    if args.candidate:
        try:
            candidates: tuple[Candidate, ...] = tuple(
                registry.candidate_by_id(cid) for cid in args.candidate
            )
        except KeyError as exc:
            _print({"result": "ERROR", "detail": str(exc)})
            return 1
        meter = FAMILY_TO_METER.get(args.family)
        if meter is not None:
            mismatched = [c.candidate_id for c in candidates if c.meter != meter]
            if mismatched:
                _print(
                    {
                        "result": "ERROR",
                        "detail": (
                            f"--candidate {mismatched!r} do not belong to family "
                            f"{args.family!r} (meter {meter.value!r})"
                        ),
                    }
                )
                return 1
    else:
        candidates = candidates_for_family(args.family)

    if args.f0_candidate is not None:
        try:
            f0_candidate = registry.candidate_by_id(args.f0_candidate)
        except KeyError as exc:
            _print({"result": "ERROR", "detail": str(exc)})
            return 1
        if f0_candidate.meter != MeterId.F0_CONTROL:
            _print(
                {
                    "result": "ERROR",
                    "detail": (
                        f"--f0-candidate {args.f0_candidate!r} is not an "
                        f"{MeterId.F0_CONTROL.value!r} candidate"
                    ),
                }
            )
            return 1

    report = run_diagnosis(
        args.family,
        candidates,
        args.max_cells,
        args.repeats,
        f0_candidate_id=args.f0_candidate,
        dump_values=args.dump_values,
    )

    if args.out is not None:
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        _print({"result": "OK", "out": str(args.out)})
    else:
        _print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
