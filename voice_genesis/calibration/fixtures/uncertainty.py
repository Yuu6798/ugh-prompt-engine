"""v1.1 §V3.3 の `U_GT`/`U_num` 境界を producer/validator が共有する canonical 導出
（Codex 第 22 巡 finding (2) 対応、R22-2、2026-09-05）。

## 背景（finding）

`c0_validate._check_u_gt_u_num_bounds()` は従来、`frozen_design.fixture_spec.
<FAMILY>.u_gt_bound`/`.u_num_bound` が「有限非負の number」であり、対応する
`*_formula` が「非空文字列」であることしか検査していなかった。この形状検査
だけでは、独立生成された v1.1 manifest が両 bound を `0.0` に、formula を
無関係な文字列にしても素通りしてしまう——過小な bound を消費した C4 が偽の
`CALIBRATED_DIRECTIONAL` を出す経路につながる。

## 解法: 「同一 canonical 関数を producer/validator が import する」+「manifest
自己完結」

`c0_freeze._fixture_specs()`（producer）は本モジュールの `gather_u_bound_
inputs()` で凍結時点の `fixtures.axes` 定数から導出入力一式を収集し、
`frozen_design.fixture_spec.<FAMILY>.u_bound_inputs` として manifest core へ
記録したうえで、`derive_u_gt_bound()`/`derive_u_num_bound()` にその入力を渡して
`u_gt_bound`/`u_num_bound`/両 `*_formula` を得る。

`c0_validate._check_u_gt_u_num_bounds()`（validator）は v1.1 manifest の非
ABSENT family について、manifest に記録済みの `u_bound_inputs` だけを使って
同じ `derive_u_gt_bound()`/`derive_u_num_bound()` を**再実行**し、宣言済みの
value/formula と一致するかを検査する。

**本モジュールの `derive_u_gt_bound()`/`derive_u_num_bound()` は `fixtures.axes`
を一切参照しない純関数**（`gather_u_bound_inputs()` だけが `axes` を読む）。
これにより、後日 `fixtures/axes.py` の定数がリポジトリ側で変更されても、
過去に凍結済みの manifest の検証結果はその変更で揺れ動かない——manifest は
それ自身が記録した入力だけから再現できる（「manifest 自己完結」の原則。
validator が repo の現在値を暗黙に使う設計は不可）。

`c0_freeze` は `c0_validate` を import するため逆方向 import ができず、両者が
依存できる中立モジュールとして `fixtures` サブパッケージへ置く（R21 の
`fixtures.axes.TRUTH_UNIT_BY_FAMILY` 昇格と同じ「神経系だけを共有モジュールに
置く」規約）。
"""

from __future__ import annotations

import math
import sys
from collections.abc import Mapping

from .. import tolerance
from . import axes
from .axes import FixtureFamily

#: [UNDERSPEC-CAL-V01] 設計正本 v1.1 §V3.3 は「16-bit PCM 量子化（-96 dBFS 相当
#: の加法雑音、宣言 gain に対して相対化）の当該 construct 単位への伝播量」を
#: family ごとの閉形式で求めるよう指示するが、振幅領域の量子化雑音から
#: 各 construct（Hz / dB_per_oct / 無次元比率など単位も物理的性質も異なる）
#: への厳密な伝達関数は正本非規定であり、推定器ごとの実測較正（本 WP の
#: スコープ外）を要する。本実装は「振幅領域の相対雑音比を、construct の真値
#: スケールへ unity-gain（伝達係数 1）で転写する」という単純化を全 family
#: 共通で採用する（実際の伝達係数はほとんどの推定器で 1 未満と見込まれるため、
#: この単純化は過大側＝保守側に倒れる。§10.2「過大は許容・過小は禁止」に整合）。
#: 相対雑音比は「その family が取りうる最も静かな宣言 gain（boundary gain の
#: 最小値）」で相対化する（gain が低いほど PCM 量子化雑音の相対寄与が大きく
#: なるため、これが worst-case = 最も保守的）。
PCM_NOISE_FLOOR_DBFS: float = -96.0
WORST_CASE_GAIN_DBFS: float = min(axes.BOUNDARY_GAIN_DBFS)
FLOAT64_EPS: float = sys.float_info.epsilon

#: family の generator truth が取りうる最大絶対値（§V3.3「truth の最大絶対値で
#: 保守化」）。RESONANCE_GT / IDENTITY_CAUSAL_SWEEP は U_GT/U_num とも ABSENT
#: のため含まない。
TRUTH_SCALE_MAX_BY_FAMILY: dict[str, float] = {
    FixtureFamily.F0_CONTROL.value: max(axes.PRIMARY_F0_HZ + axes.BOUNDARY_F0_HZ),
    FixtureFamily.FORMANT_GT.value: max(
        pole for pole_set in axes.FORMANT_POLE_SETS_HZ for pole in pole_set
    ),
    FixtureFamily.TILT_GT.value: max(abs(s) for s in axes.TILT_SLOPES_DB_PER_OCT),
    FixtureFamily.APERIODICITY_GT.value: max(axes.APERIODICITY_FRACTIONS),
    FixtureFamily.TRANSITION_GT.value: max(axes.TRANSITION_SEVERITY_MAGNITUDE.values()),
}

#: U_GT/U_num が構造的に gate 入力にならない family（§V3.3）。
#: `campaign/holdout_stage.declared_u_gt_u_num_for_family()` はこの文字列を
#: 非 numeric として黙って `None` 扱いする（`isinstance(str, (int, float))`
#: は False）ため、値の型を変える必要なく正しく `NOT_EVALUABLE/INPUT_MISSING`
#: へ倒れる。
U_ABSENT_REASON_BY_FAMILY: dict[str, str] = {
    FixtureFamily.RESONANCE_GT.value: "ABSENT:diagnostic_only",
    FixtureFamily.IDENTITY_CAUSAL_SWEEP.value: "ABSENT:no_physical_ground_truth",
}


def gather_u_bound_inputs(family: FixtureFamily) -> dict[str, object] | str:
    """freeze 時点の `fixtures.axes` 定数から、`derive_u_gt_bound()`/
    `derive_u_num_bound()` が必要とする導出入力一式を収集する。

    **producer (`c0_freeze._fixture_specs()`) だけが呼ぶ**——本関数の戻り値は
    manifest の `frozen_design.fixture_spec.<FAMILY>.u_bound_inputs` として
    記録される。validator (`c0_validate._check_u_gt_u_num_bounds()`) は
    本関数を呼び直してはならず、manifest に記録済みのこの値を読むだけに
    留める（自己完結の原則 — モジュール docstring 参照）。
    """
    absent_reason = U_ABSENT_REASON_BY_FAMILY.get(family.value)
    if absent_reason is not None:
        return absent_reason
    inputs: dict[str, object] = {
        "truth_scale_max": TRUTH_SCALE_MAX_BY_FAMILY[family.value],
        "pcm_noise_floor_dbfs": PCM_NOISE_FLOOR_DBFS,
        "worst_case_gain_dbfs": WORST_CASE_GAIN_DBFS,
        "float64_eps": FLOAT64_EPS,
    }
    if family in (FixtureFamily.TRANSITION_GT, FixtureFamily.APERIODICITY_GT):
        inputs["sr_min_hz"] = min(axes.PRIMARY_SR_HZ + axes.BOUNDARY_SR_HZ)
    if family is FixtureFamily.APERIODICITY_GT:
        inputs["duration_min_s"] = min(axes.PRIMARY_DURATION_S + axes.BOUNDARY_DURATION_S)
        inputs["aperiodicity_fraction_max"] = max(axes.APERIODICITY_FRACTIONS)
    return inputs


def derive_u_gt_bound(family: FixtureFamily, inputs: Mapping[str, object] | str) -> tuple[object, str]:
    """`(value, formula)` を返す。`inputs` は `gather_u_bound_inputs()` の
    戻り値そのもの（producer が呼ぶ時点）か、manifest から読み戻した同形の
    dict/文字列（validator が再導出する時点）のいずれでもよい。

    本関数自体は `fixtures.axes` を一切参照しない純関数（自己完結の原則）。
    `inputs` に必要なキーが欠けている場合は `KeyError` を送出する（呼び出し側
    ——`c0_validate._check_u_gt_u_num_bounds()`——がこれを fail-closed の
    violation に変換する）。
    """
    if isinstance(inputs, str):
        absent_reason = inputs
        return (
            absent_reason,
            f"{family.value} has no ABSOLUTE/DIRECTIONAL gate input (v1.1 §V3.3): "
            f"{absent_reason}",
        )
    if family in (FixtureFamily.F0_CONTROL, FixtureFamily.FORMANT_GT, FixtureFamily.TILT_GT):
        return (
            0.0,
            "U_GT = 0 (truth realized by exact float64 analytic synthesis at "
            "generation time; residual absorbed by the U_num float_eps term)",
        )
    if family is FixtureFamily.TRANSITION_GT:
        sr_min = inputs["sr_min_hz"]
        join_time_bound = 0.5 / sr_min
        formula = (
            "discontinuity_magnitude (the frozen scalar; matches the unit of the only "
            "wired M5_TRANSITION primary_output field, dimensionless_magnitude): "
            "U_GT = 0 (analytic instantaneous amplitude-step assignment at generation "
            "time; residual absorbed by the U_num float_eps term). Informational only, "
            "not folded into this scalar (different unit; no currently wired candidate "
            "uses it as primary_output — see [UNDERSPEC-CAL-V02] in the WP report): "
            f"join_time_s U_GT = 0.5/sr_hz, worst case at sr_hz={sr_min!r} => "
            f"{join_time_bound!r} s."
        )
        return 0.0, formula
    if family is FixtureFamily.APERIODICITY_GT:
        fraction_max = inputs["aperiodicity_fraction_max"]
        duration_min = inputs["duration_min_s"]
        sr_min = inputs["sr_min_hz"]
        n_min = duration_min * sr_min
        value = fraction_max * 3.0 * math.sqrt(2.0 / n_min)
        formula = (
            "U_GT = fraction * 3 * sqrt(2/N), N = duration_s * sr_hz (finite-length "
            "noise realization; 3-sigma conservative upper bound on the chi-square "
            f"fluctuation around the declared fraction); family bound uses "
            f"fraction={fraction_max!r} (max truth-core fraction) and N={n_min!r} "
            f"(duration_s={duration_min!r} x sr_hz={sr_min!r}, both family minima, "
            f"conservative) => {value!r}"
        )
        return value, formula
    raise AssertionError(f"unhandled family for U_GT: {family!r}")  # pragma: no cover


def derive_u_num_bound(family: FixtureFamily, inputs: Mapping[str, object] | str) -> tuple[object, str]:
    """v1.1 §V3.3 の family 別 `U_num`（PCM 量子化・浮動小数・宣言分解能から
    機械導出）。`tolerance.derive_floor()` をそのまま使う。`meter_declared_
    resolution` は C0 時点では候補未選抜のため 0 固定とする——
    `declared_u_gt_u_num_for_family(manifest, family)` は候補（parameter
    JSON）を一切受け取らないシグネチャのため、選抜後の候補宣言分解能を
    上乗せする経路は現行消費側では構造的に組み込めない（[UNDERSPEC-CAL-V02]、
    WP 報告に明記）。`derive_u_gt_bound()` と同じく `inputs` に必要なキーが
    欠けている場合は `KeyError` を送出する。
    """
    if isinstance(inputs, str):
        absent_reason = inputs
        return (
            absent_reason,
            f"{family.value} has no ABSOLUTE/DIRECTIONAL gate input (v1.1 §V3.3): "
            f"{absent_reason}",
        )
    truth_max = inputs["truth_scale_max"]
    pcm_noise_floor_dbfs = inputs["pcm_noise_floor_dbfs"]
    worst_case_gain_dbfs = inputs["worst_case_gain_dbfs"]
    float64_eps = inputs["float64_eps"]
    pcm_relative_noise_fraction = 10.0 ** ((pcm_noise_floor_dbfs - worst_case_gain_dbfs) / 20.0)
    pcm_quantization_step = 2.0 * pcm_relative_noise_fraction * truth_max
    float_eps_bound = float64_eps * truth_max
    value, floor_formula = tolerance.derive_floor(
        pcm_quantization_step=pcm_quantization_step,
        float_eps_bound=float_eps_bound,
        meter_declared_resolution=None,
    )
    formula = (
        f"U_num = tolerance.derive_floor(pcm_quantization_step=2*"
        f"{pcm_relative_noise_fraction!r}*|truth|_max, float_eps_bound=float64_eps*"
        "|truth|_max, meter_declared_resolution=0). pcm term = 16-bit/-96dBFS "
        f"quantization noise floor relativized to worst declared gain "
        f"({worst_case_gain_dbfs!r} dBFS boundary; unity-gain transfer to construct "
        f"units, conservative per [UNDERSPEC-CAL-V01]) x |truth|_max={truth_max!r} "
        f"({family.value}). float term = float64 eps ({float64_eps!r}) x "
        f"|truth|_max={truth_max!r}. meter_declared_resolution=0 at C0 (candidate not "
        "yet selected; post-selection declared resolution is not incorporated by the "
        "current declared_u_gt_u_num_for_family(manifest, family) consumer signature "
        f"— out of scope for this WP, see [UNDERSPEC-CAL-V02]). {floor_formula}"
    )
    return value, formula
