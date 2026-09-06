"""RUN10-CAL candidate measurement space — 99 候補の宣言的定義
（設計正本 §8 + IMPLEMENTATION_MAP_v1.md §2.6 が凍結したグリッド）。

本モジュールはデータ定義のみを持つ（実測・selection・freeze は一切行わない
= 設計正本 §0 授権境界）。`ALL_CANDIDATES` が唯一の正本リストであり、
count 検算・パラメタグリッドの完全一致・independence tier / claim ceiling
の一貫性は `tests/test_registry.py` が enforce する。

## candidate_id 命名規則

`<METER-PREFIX>-<ALGORITHM-FAMILY>[-<PARAM-TOKEN>...]`。B0 候補は設計正本
§8 が literal に挙げる名称をそのまま使う
（例: `F0-B0-CURRENT`, `M2T-B0-CURRENT-HYBRID`）。パラメタ化された候補は
パラメタ grid の各軸をトークン化して連結する（例:
`F0-PYIN-FRAME2048-HOP256`）。全 99 candidate_id の一意性は
`tests/test_registry.py::test_candidate_id_uniqueness` が enforce する。

## complexity_rank の割り当て規則

[UNDERSPEC-CAL-C05] 設計正本は complexity_rank を「selection の
lexicographic 比較に使う整数」として要求する（§9 のタイブレーク軸の 1 つ）
のみで、具体的な数値化方法（FLOPs 等の実測指標）までは規定しない。
最も単純で全 meter family 内で一意な全順序を与える規則として、
**本モジュール内の宣言順（B0 → §8 記載順の algorithm family → 各 family
内は itertools.product のグリッド軸宣言順）の 0-based 連番**を採用する。
この値は実際の計算コスト（FLOPs 等）を表すものではなく、単に
「family 内で一意な全順序」という selection.py の要求仕様を満たすための
決定論的な tie-break キーである。

## independence tier / claim ceiling の割り当て根拠

各候補の `independence_tier` は設計正本 §4.1/§4.2/§8 の記述から機械的に
選び、`claim_ceiling` は選んだ tier に対して
`vocab.INDEPENDENCE_TIER_CLAIM_CEILING[tier]` が許す**上限**の範囲内で
設計正本 §8 が明示する値（明示がなければ tier の上限そのもの）を設定する。
tier→ceiling の整合性は「ceiling が tier の許す上限を超えない」という
`<=` 関係として `tests/test_registry.py` が検証する（tier の上限そのものを
使う候補は自動的に等号を満たす）。

[UNDERSPEC-CAL-C06] `M2T-B0-CURRENT-HYBRID`（「そのままでは INVALID」と
明記）は、vocab の 4-tier 閉語彙に「unit/construct 不一致による無効」を
指す専用の tier が存在しないため、最も意味が近い `INVALID_CIRCULAR`
（ceiling=`NONE` = 校正証拠として無効）へ割り当てた。真の循環性
（GT が同一 estimator 由来）ではない点に注意（記録のみ、C0 freeze 承認時の
レビュー対象）。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

from .. import vocab
from ..fixtures import matrix as fixture_matrix
from ..fixtures.controls import DetectionPredicate

# ---------------------------------------------------------------------------
# Candidate 値オブジェクト
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """1 候補の宣言的定義（設計正本 §8 共通フィールド）。"""

    candidate_id: str
    meter: vocab.MeterId
    construct: str
    unit: str
    algorithm_family: str
    parameters: tuple[tuple[str, object], ...]
    domain: str
    """候補の宣言済み適用可能域（fixture 行の `vocab.Domain` PRIMARY/BOUNDARY
    とは別軸。設計正本 §8 の「宣言済み F0×ceiling×window×sample-rate domain」
    のような自由記述テキスト）。"""
    missing_rule: str
    independence_tier: vocab.IndependenceTier
    claim_ceiling: vocab.ClaimCeiling
    complexity_rank: int
    implementation_ref: str
    """`module:function` 形式（`voice_genesis.calibration.candidates.impl.<module>:<function>`
    の完全修飾のうち `candidates.impl.` を省略した短縮形）。"""
    detection_predicate: DetectionPredicate | None = None
    """RUN10-CAL-v1.2 WP1: `fixtures.controls.detected()` へ渡す非既定 fire
    判定（任意）。既定 `None` は `detected()` の既定分岐（missing_reason/
    ineligible のいずれでも説明されず values が非空かつ全値有限）を使うことを
    意味し、既存の全候補は本 revision では宣言しない（挙動不変）。"""

    def params_dict(self) -> dict[str, object]:
        return dict(self.parameters)


def _params(**kwargs: object) -> tuple[tuple[str, object], ...]:
    return tuple(sorted(kwargs.items()))


# ---------------------------------------------------------------------------
# F0_CONTROL（5 候補: B0 + 4 pyin。claim-critical 外・上流 control）
# ---------------------------------------------------------------------------

_F0_MISSING_RULE = "全フレーム無声/推定失敗 → OUTPUT_MISSING（縮退代入なし）。"

F0_PYIN_FRAME = (2048, 4096)
F0_PYIN_HOP = (256, 512)


def _build_f0_control() -> list[Candidate]:
    out: list[Candidate] = []
    rank = 0
    out.append(
        Candidate(
            candidate_id="F0-B0-CURRENT",
            meter=vocab.MeterId.F0_CONTROL,
            construct="fundamental_frequency",
            unit="hz",
            algorithm_family="B0_CURRENT_NACF_YIN",
            parameters=_params(),
            domain="宣言済み primary F0 帯 (C3-G4 anchor) + boundary probe",
            missing_rule=_F0_MISSING_RULE,
            independence_tier=vocab.IndependenceTier.INDEPENDENT_ANALYTIC,
            claim_ceiling=vocab.ClaimCeiling.ABSOLUTE,
            complexity_rank=rank,
            implementation_ref="candidates.impl.b0_wrappers:measure_f0_b0",
        )
    )
    rank += 1
    for frame, hop in itertools.product(F0_PYIN_FRAME, F0_PYIN_HOP):
        out.append(
            Candidate(
                candidate_id=f"F0-PYIN-FRAME{frame}-HOP{hop}",
                meter=vocab.MeterId.F0_CONTROL,
                construct="fundamental_frequency",
                unit="hz",
                algorithm_family="PYIN",
                parameters=_params(frame_length=frame, hop_length=hop, fmin=80.0, fmax=600.0),
                domain="宣言済み primary F0 帯 (C3-G4 anchor) + boundary probe。fmin=80/fmax=600 固定。",
                missing_rule=_F0_MISSING_RULE,
                independence_tier=vocab.IndependenceTier.INDEPENDENT_ANALYTIC,
                claim_ceiling=vocab.ClaimCeiling.ABSOLUTE,
                complexity_rank=rank,
                implementation_ref="candidates.impl.f0_pyin:measure",
            )
        )
        rank += 1
    return out


# ---------------------------------------------------------------------------
# M3 formants（43 候補: B0-centroid(1) + cepstral-poles(18) + burg-lpc(24)）
# ---------------------------------------------------------------------------

M3_CEPSTRAL_LIFTER_RATIO = (0.5, 0.7, 0.9)
M3_CEPSTRAL_MIN_LIFTER_SAMPLES = (4, 8)
M3_CEPSTRAL_BAND_HI = (3500, 4000, 4500)

M3_BURG_ORDER = (12, 16, 20)
M3_BURG_WINDOW_MS = (25, 40)
M3_BURG_PREEMPH_HZ = (0, 50)
M3_BURG_MAX_FORMANT_HZ = (4000, 5000)


def _build_m3_formants() -> list[Candidate]:
    out: list[Candidate] = []
    rank = 0
    out.append(
        Candidate(
            candidate_id="M3-B0-CURRENT-CENTROID",
            meter=vocab.MeterId.M3_FORMANTS,
            construct="formant_centroid",
            unit="hz",
            algorithm_family="B0_CURRENT_CEPSTRAL_CENTROID",
            parameters=_params(),
            domain="DIAGNOSTIC_ONLY: centroid は F1/F2/F3 個別 Hz error の代用にならない。",
            missing_rule="帯域内ピーク 0 個 → OUTPUT_MISSING。",
            independence_tier=vocab.IndependenceTier.SHARED_MODEL_DIAGNOSTIC,
            claim_ceiling=vocab.ClaimCeiling.DIAGNOSTIC_ONLY,
            complexity_rank=rank,
            implementation_ref="candidates.impl.b0_wrappers:measure_m3_b0_centroid",
        )
    )
    rank += 1
    for lifter_ratio, min_lifter, band_hi in itertools.product(
        M3_CEPSTRAL_LIFTER_RATIO, M3_CEPSTRAL_MIN_LIFTER_SAMPLES, M3_CEPSTRAL_BAND_HI
    ):
        out.append(
            Candidate(
                candidate_id=(
                    f"M3-CEPSTRAL-LIFT{lifter_ratio}-MINLIFT{min_lifter}-BANDHI{band_hi}"
                ),
                meter=vocab.MeterId.M3_FORMANTS,
                construct="formant_frequency",
                unit="hz",
                algorithm_family="CEPSTRAL_POLES",
                parameters=_params(
                    lifter_ratio=lifter_ratio,
                    min_lifter_samples=min_lifter,
                    band_hi=band_hi,
                    band_lo=300.0,
                ),
                domain="baseline と同族（ケプストラム liftering 系）。band_lo=300Hz 固定。",
                missing_rule="帯域内ピーク 0 個 → OUTPUT_MISSING（[UNDERSPEC-CAL-C02]）。",
                independence_tier=vocab.IndependenceTier.CROSS_IMPLEMENTATION,
                claim_ceiling=vocab.ClaimCeiling.ABSOLUTE,
                complexity_rank=rank,
                implementation_ref="candidates.impl.formant_cepstral:measure",
            )
        )
        rank += 1
    for order, window_ms, preemph_hz, max_formant_hz in itertools.product(
        M3_BURG_ORDER, M3_BURG_WINDOW_MS, M3_BURG_PREEMPH_HZ, M3_BURG_MAX_FORMANT_HZ
    ):
        out.append(
            Candidate(
                candidate_id=(
                    f"M3-BURG-ORDER{order}-WIN{window_ms}MS-PREEMPH{preemph_hz}HZ"
                    f"-MAXF{max_formant_hz}"
                ),
                meter=vocab.MeterId.M3_FORMANTS,
                construct="formant_frequency",
                unit="hz",
                algorithm_family="BURG_LPC",
                parameters=_params(
                    order=order,
                    window_ms=window_ms,
                    preemph_hz=preemph_hz,
                    max_formant_hz=max_formant_hz,
                ),
                domain=(
                    f"唯一の独立 family。fs'=2*{max_formant_hz}Hz へ決定的 resample 必須。"
                ),
                missing_rule="安定極が 0 個 → OUTPUT_MISSING。",
                independence_tier=vocab.IndependenceTier.INDEPENDENT_ANALYTIC,
                claim_ceiling=vocab.ClaimCeiling.ABSOLUTE,
                complexity_rank=rank,
                implementation_ref="candidates.impl.formant_burg:measure",
            )
        )
        rank += 1
    return out


# ---------------------------------------------------------------------------
# M2 spectral tilt（13 候補: B0-hybrid(1) + OLS(6) + TheilSen(6)）
# ---------------------------------------------------------------------------

M2T_K = (4, 6, 8)
M2T_WINDOW = ("hann", "blackman_harris")


def _build_m2_tilt() -> list[Candidate]:
    out: list[Candidate] = []
    rank = 0
    out.append(
        Candidate(
            candidate_id="M2T-B0-CURRENT-HYBRID",
            meter=vocab.MeterId.M2_SPECTRAL_TILT,
            construct="source_spectral_tilt",
            unit="mixed(db_per_oct|db)",
            algorithm_family="B0_CURRENT_HYBRID",
            parameters=_params(),
            domain="unit 混在のためそのままでは INVALID（設計正本 §8）。",
            missing_rule="regression も h1h2 も不能 → OUTPUT_MISSING。",
            independence_tier=vocab.IndependenceTier.INVALID_CIRCULAR,
            claim_ceiling=vocab.ClaimCeiling.NONE,
            complexity_rank=rank,
            implementation_ref="candidates.impl.b0_wrappers:measure_m2t_b0_hybrid",
        )
    )
    rank += 1
    for k, window in itertools.product(M2T_K, M2T_WINDOW):
        out.append(
            Candidate(
                candidate_id=f"M2T-HARMONIC-OLS-K{k}-WIN{window.upper()}",
                meter=vocab.MeterId.M2_SPECTRAL_TILT,
                construct="source_spectral_tilt",
                unit="db_per_oct",
                algorithm_family="HARMONIC_OLS",
                parameters=_params(k=k, window=window),
                domain="20*log10(A_k) vs log2(k) 線形回帰。H1-H2 フォールバックなし。",
                missing_rule=f"K={k} 本未満の倍音取得 → 縮退せず OUTPUT_MISSING（設計正本 §8）。",
                independence_tier=vocab.IndependenceTier.INDEPENDENT_ANALYTIC,
                claim_ceiling=vocab.ClaimCeiling.ABSOLUTE,
                complexity_rank=rank,
                implementation_ref="candidates.impl.tilt_harmonic:measure_ols",
            )
        )
        rank += 1
    for k, window in itertools.product(M2T_K, M2T_WINDOW):
        out.append(
            Candidate(
                candidate_id=f"M2T-HARMONIC-THEILSEN-K{k}-WIN{window.upper()}",
                meter=vocab.MeterId.M2_SPECTRAL_TILT,
                construct="source_spectral_tilt",
                unit="db_per_oct",
                algorithm_family="HARMONIC_THEILSEN",
                parameters=_params(k=k, window=window),
                domain="Theil-Sen（中央値ベース）勾配。H1-H2 フォールバックなし。",
                missing_rule=f"K={k} 本未満の倍音取得 → 縮退せず OUTPUT_MISSING（設計正本 §8）。",
                independence_tier=vocab.IndependenceTier.INDEPENDENT_ANALYTIC,
                claim_ceiling=vocab.ClaimCeiling.ABSOLUTE,
                complexity_rank=rank,
                implementation_ref="candidates.impl.tilt_harmonic:measure_theilsen",
            )
        )
        rank += 1
    return out


# ---------------------------------------------------------------------------
# M2 aperiodicity（24 候補: B0(1) + HNR-ACF(8) + harmonic-residual(12) + D4C(3)）
# ---------------------------------------------------------------------------

M2A_HNR_FRAME_MS = (25, 40)
M2A_HNR_HOP_MS = (10, 20)
M2A_HNR_WINDOW = ("hann", "blackman_harris")

M2A_RESIDUAL_K = (8, 10, 12)
M2A_RESIDUAL_WINDOW = ("hann", "blackman_harris")
M2A_RESIDUAL_BAND = ("broadband", "0-6khz")
"""[UNDERSPEC-CAL-C09] 設計正本 §2.6 の「residual band {0–Nyquist, 0–6 kHz}」の
うち「0–Nyquist」は `candidates.impl.aperiodicity.harmonic_residual_fraction`
の `broadband`（帯域制限なし = 0〜Nyquist 全域）トークンへ写像する
（D4C 側の `band` グリッドで既に使っている命名と揃え、実装内のトークン
語彙を 1 つに統一した）。"""

M2A_D4C_BAND = ("broadband", "0-3khz", "3-6khz")


def _build_m2_aperiodicity() -> list[Candidate]:
    out: list[Candidate] = []
    rank = 0
    out.append(
        Candidate(
            candidate_id="M2A-B0-AUTOCORR-PERIODICITY",
            meter=vocab.MeterId.M2_APERIODICITY,
            construct="harmonic_to_noise_ratio",
            unit="db",
            algorithm_family="B0_CURRENT_HNR_APPROX",
            parameters=_params(),
            domain="harmonic/noise 帯域エネルギー比（FFT ベース）。",
            missing_rule="f0 無効 or 帯域エネルギー欠損 → OUTPUT_MISSING。",
            independence_tier=vocab.IndependenceTier.CROSS_IMPLEMENTATION,
            claim_ceiling=vocab.ClaimCeiling.DIRECTIONAL,
            complexity_rank=rank,
            implementation_ref="candidates.impl.b0_wrappers:measure_m2a_b0_periodicity",
        )
    )
    rank += 1
    for frame_ms, hop_ms, window in itertools.product(
        M2A_HNR_FRAME_MS, M2A_HNR_HOP_MS, M2A_HNR_WINDOW
    ):
        out.append(
            Candidate(
                candidate_id=(
                    f"M2A-HNR-ACF-FRAME{frame_ms}MS-HOP{hop_ms}MS-WIN{window.upper()}"
                ),
                meter=vocab.MeterId.M2_APERIODICITY,
                construct="harmonic_to_noise_ratio",
                unit="db",
                algorithm_family="HNR_ACF",
                parameters=_params(frame_ms=frame_ms, hop_ms=hop_ms, window=window),
                domain="正規化自己相関ピーク → HNR。独立実装は directional/monotonicity 上限。",
                missing_rule="有効フレーム 0 → OUTPUT_MISSING。",
                independence_tier=vocab.IndependenceTier.CROSS_IMPLEMENTATION,
                claim_ceiling=vocab.ClaimCeiling.DIRECTIONAL,
                complexity_rank=rank,
                implementation_ref="candidates.impl.aperiodicity:measure_hnr_acf",
            )
        )
        rank += 1
    for k, window, band in itertools.product(
        M2A_RESIDUAL_K, M2A_RESIDUAL_WINDOW, M2A_RESIDUAL_BAND
    ):
        out.append(
            Candidate(
                candidate_id=(
                    f"M2A-HARMONIC-RESIDUAL-K{k}-WIN{window.upper()}"
                    f"-BAND{band.upper().replace('-', '_')}"
                ),
                meter=vocab.MeterId.M2_APERIODICITY,
                construct="injected_noise_fraction",
                unit="fraction",
                algorithm_family="HARMONIC_RESIDUAL",
                parameters=_params(k=k, window=window, residual_band=band),
                domain="comb-remove 後の残差/全パワー比。独立 generator 上のみ ABSOLUTE 候補。",
                missing_rule="f0 無効 or 対象帯域パワー 0 → OUTPUT_MISSING。",
                independence_tier=vocab.IndependenceTier.INDEPENDENT_ANALYTIC,
                claim_ceiling=vocab.ClaimCeiling.ABSOLUTE,
                complexity_rank=rank,
                implementation_ref="candidates.impl.aperiodicity:measure_harmonic_residual",
            )
        )
        rank += 1
    for band in M2A_D4C_BAND:
        out.append(
            Candidate(
                candidate_id=f"M2A-D4C-BAND-{band.upper().replace('-', '_')}",
                meter=vocab.MeterId.M2_APERIODICITY,
                construct="world_d4c_aperiodicity",
                unit="fraction",
                algorithm_family="D4C_WORLD",
                parameters=_params(band=band),
                domain=(
                    "WORLD 合成 fixture 上は SHARED_MODEL_DIAGNOSTIC。"
                    "F0 入力は選択済み F0_CONTROL 固定（params['f0_hz']）。"
                ),
                missing_rule=(
                    "pyworld 不在 → ineligible (INELIGIBLE_DEPENDENCY_ABSENT。"
                    "設計正本 §3.3 pyworld 特則: 当該候補のみ ineligible)。"
                    "f0 無効 → INPUT_MISSING。"
                ),
                independence_tier=vocab.IndependenceTier.SHARED_MODEL_DIAGNOSTIC,
                claim_ceiling=vocab.ClaimCeiling.DIAGNOSTIC_ONLY,
                complexity_rank=rank,
                implementation_ref="candidates.impl.aperiodicity:measure_d4c",
            )
        )
        rank += 1
    return out


# ---------------------------------------------------------------------------
# M4 resonance（5 候補。全候補 DIAGNOSTIC_ONLY 上限で閉じる = 設計正本 §16）
# ---------------------------------------------------------------------------

M4_PROMINENCE_DB = (6, 12)
M4_SMOOTHING_BANDWIDTH_HZ = (150, 300)


def _build_m4_resonance() -> list[Candidate]:
    out: list[Candidate] = []
    rank = 0
    out.append(
        Candidate(
            candidate_id="M4-B0-CURRENT-CENTROID",
            meter=vocab.MeterId.M4_RESONANCE,
            construct="resonance_centroid",
            unit="hz",
            algorithm_family="B0_CURRENT_CEPSTRAL_CENTROID",
            parameters=_params(),
            domain="全 M4 候補は RUN10 で DIAGNOSTIC_ONLY 上限に閉じる（設計正本 §16）。",
            missing_rule="帯域内ピーク 0 個 → OUTPUT_MISSING。",
            independence_tier=vocab.IndependenceTier.SHARED_MODEL_DIAGNOSTIC,
            claim_ceiling=vocab.ClaimCeiling.DIAGNOSTIC_ONLY,
            complexity_rank=rank,
            implementation_ref="candidates.impl.b0_wrappers:measure_m4_b0_centroid",
        )
    )
    rank += 1
    for prominence_db, smoothing_hz in itertools.product(
        M4_PROMINENCE_DB, M4_SMOOTHING_BANDWIDTH_HZ
    ):
        out.append(
            Candidate(
                candidate_id=f"M4-LOCAL-PROMINENCE-THR{prominence_db}DB-SMOOTH{smoothing_hz}HZ",
                meter=vocab.MeterId.M4_RESONANCE,
                construct="resonance_center_frequency",
                unit="hz",
                algorithm_family="LOCAL_PROMINENCE",
                parameters=_params(
                    prominence_db=prominence_db, smoothing_bandwidth_hz=smoothing_hz
                ),
                domain="全 M4 候補は RUN10 で DIAGNOSTIC_ONLY 上限に閉じる（設計正本 §16。M3 との construct 独立性は未証明）。",
                missing_rule="prominence 条件を満たすピーク 0 個 → OUTPUT_MISSING。",
                independence_tier=vocab.IndependenceTier.SHARED_MODEL_DIAGNOSTIC,
                claim_ceiling=vocab.ClaimCeiling.DIAGNOSTIC_ONLY,
                complexity_rank=rank,
                implementation_ref="candidates.impl.resonance_prominence:measure",
            )
        )
        rank += 1
    return out


# ---------------------------------------------------------------------------
# M5 transition/join（7 候補: wave-discontinuity(3) + spectral-flux(4)）
# ---------------------------------------------------------------------------

M5_WAVE_WINDOW_MS = (2, 5, 10)
M5_FLUX_FRAME = (512, 1024)
M5_FLUX_NORM = ("L1", "L2")


def _build_m5_transition() -> list[Candidate]:
    out: list[Candidate] = []
    rank = 0
    for window_ms in M5_WAVE_WINDOW_MS:
        out.append(
            Candidate(
                candidate_id=f"M5-WAVE-DISCONTINUITY-WIN{window_ms}MS",
                meter=vocab.MeterId.M5_TRANSITION,
                construct="join_discontinuity_magnitude",
                unit="rms_amplitude_delta",
                algorithm_family="WAVE_DISCONTINUITY",
                parameters=_params(window_ms=window_ms),
                domain="短窓 RMS の frame-to-frame jump。",
                missing_rule="有効フレーム対 < 2 → OUTPUT_MISSING。",
                independence_tier=vocab.IndependenceTier.CROSS_IMPLEMENTATION,
                claim_ceiling=vocab.ClaimCeiling.DIRECTIONAL,
                complexity_rank=rank,
                implementation_ref="candidates.impl.transition:measure_wave_discontinuity",
            )
        )
        rank += 1
    for frame_len, norm in itertools.product(M5_FLUX_FRAME, M5_FLUX_NORM):
        out.append(
            Candidate(
                candidate_id=f"M5-SPECTRAL-FLUX-FRAME{frame_len}-NORM{norm}",
                meter=vocab.MeterId.M5_TRANSITION,
                construct="join_discontinuity_magnitude",
                unit=f"spectral_flux_{norm.lower()}",
                algorithm_family="SPECTRAL_FLUX",
                parameters=_params(frame_len=frame_len, norm=norm),
                domain="frame-to-frame 振幅スペクトル差分のノルム。",
                missing_rule="有効フレーム対 < 2 → OUTPUT_MISSING。",
                independence_tier=vocab.IndependenceTier.CROSS_IMPLEMENTATION,
                claim_ceiling=vocab.ClaimCeiling.DIRECTIONAL,
                complexity_rank=rank,
                implementation_ref="candidates.impl.transition:measure_spectral_flux",
            )
        )
        rank += 1
    return out


# ---------------------------------------------------------------------------
# M6 identity（2 候補: weighted_L1 / weighted_L2）
# ---------------------------------------------------------------------------


def _build_m6_identity() -> list[Candidate]:
    return [
        Candidate(
            candidate_id="M6-WEIGHTED-L1",
            meter=vocab.MeterId.M6_IDENTITY,
            construct="identity_component_distance",
            unit="normalized_l1",
            algorithm_family="WEIGHTED_L1",
            parameters=_params(norm="L1"),
            domain="CLAIM_CRITICAL_SET 全 member が CALIBRATED_ABSOLUTE のときのみ計算（m6_identity.py）。",
            missing_rule="critical set 空集合 or 部分構成 → NOT_EVALUABLE（distance 出力禁止）。",
            independence_tier=vocab.IndependenceTier.CROSS_IMPLEMENTATION,
            claim_ceiling=vocab.ClaimCeiling.DIRECTIONAL,
            complexity_rank=0,
            implementation_ref="voice_genesis.calibration.m6_identity:m6_distance",
        ),
        Candidate(
            candidate_id="M6-WEIGHTED-L2",
            meter=vocab.MeterId.M6_IDENTITY,
            construct="identity_component_distance",
            unit="normalized_l2",
            algorithm_family="WEIGHTED_L2",
            parameters=_params(norm="L2"),
            domain="CLAIM_CRITICAL_SET 全 member が CALIBRATED_ABSOLUTE のときのみ計算（m6_identity.py）。",
            missing_rule="critical set 空集合 or 部分構成 → NOT_EVALUABLE（distance 出力禁止）。",
            independence_tier=vocab.IndependenceTier.CROSS_IMPLEMENTATION,
            claim_ceiling=vocab.ClaimCeiling.DIRECTIONAL,
            complexity_rank=1,
            implementation_ref="voice_genesis.calibration.m6_identity:m6_distance",
        ),
    ]


# ---------------------------------------------------------------------------
# 集約
# ---------------------------------------------------------------------------

ALL_CANDIDATES: tuple[Candidate, ...] = tuple(
    _build_f0_control()
    + _build_m3_formants()
    + _build_m2_tilt()
    + _build_m2_aperiodicity()
    + _build_m4_resonance()
    + _build_m5_transition()
    + _build_m6_identity()
)


def candidates_for_meter(meter: vocab.MeterId) -> tuple[Candidate, ...]:
    return tuple(c for c in ALL_CANDIDATES if c.meter == meter)


def candidate_by_id(candidate_id: str) -> Candidate:
    for c in ALL_CANDIDATES:
        if c.candidate_id == candidate_id:
            return c
    raise KeyError(f"unknown candidate_id: {candidate_id!r}")


# ---------------------------------------------------------------------------
# v1.2 WP2b — rehearsal 候補プール（縮小プールと 1 箇所切替）
#
# Fable 判定 2026-09-06: rehearsal E2E の実測 3 時間は「疎通試験」として不合格。
# 律速は `候補数 (99) x instance x fresh-process 起動` の積であり、行列の縮小
# （`fixtures.matrix.build_rehearsal_matrix()`、456 -> 58 行）だけでは届かない。
# rehearsal は claim を生まないため候補プールの縮小は許容される——ただし
# **本番 manifest が縮小プールで凍結されることは許されない**
# （`c0_validate._check_candidate_space_pool()` が両方向に固定する）。
#
# 切替は行列と同じく 1 箇所（`active_candidates()`）で、分岐フラグも共有する
# （`fixtures.matrix._REHEARSAL_MODE` / `set_rehearsal_mode()`）——行列と候補が
# 別々のフラグで動くと「縮小行列 x 全候補」のような未定義の組が作れてしまう。
# ---------------------------------------------------------------------------


def rehearsal_candidate_pool() -> tuple[Candidate, ...]:
    """`ALL_CANDIDATES` の決定論的部分集合（registry の宣言順を保存する）。

    meter family ごとに次の 2 規則で **最大 2 件**を拾う（手選びの余地は無い）:

    (i)  `candidate_id` に `"-B0-"` を含む baseline 候補の先頭 1 件
         （B0 は設計正本 §8 が「必ず含める」と定める比較基準であり、
         c2 baseline audit / c4 holdout の双方が明示的に要求する）。
    (ii) `claim_ceiling != NONE` かつ (i) で選ばれていない先頭 1 件
         （selection が「B0 と B0 以外」を比較できる最小構成。ceiling=NONE の
         候補は校正証拠として無効であり、疎通の対象にする価値が無い）。

    B0 を持たない family（`M5_TRANSITION` / `M6_IDENTITY`）は (ii) の 1 件のみ。
    `F0_CONTROL` も他 family と同じ規則で扱う（特例を置かない）。
    """
    entries_by_meter: dict[vocab.MeterId, list[tuple[int, Candidate]]] = {}
    for index, candidate in enumerate(ALL_CANDIDATES):
        entries_by_meter.setdefault(candidate.meter, []).append((index, candidate))

    keep: set[int] = set()
    for entries in entries_by_meter.values():
        baseline_index: int | None = None
        for index, candidate in entries:
            if "-B0-" in candidate.candidate_id:
                baseline_index = index
                keep.add(index)
                break
        for index, candidate in entries:
            if index == baseline_index:
                continue
            if candidate.claim_ceiling is not vocab.ClaimCeiling.NONE:
                keep.add(index)
                break
    return tuple(ALL_CANDIDATES[index] for index in sorted(keep))


def active_candidates() -> tuple[Candidate, ...]:
    """候補空間の唯一の実行時入口。既定は `ALL_CANDIDATES`（本番 99 候補）、
    `fixtures.matrix.set_rehearsal_mode(True)` の下でのみ
    `rehearsal_candidate_pool()`。

    production 側の候補列挙 call site は **すべて** 本関数（または
    `active_candidates_for_meter()`）を経由する
    （`tests/test_matrix.py::test_candidate_enumeration_call_sites_are_frozen`
    が全数を固定する）。"""
    if fixture_matrix.rehearsal_mode():
        return rehearsal_candidate_pool()
    return ALL_CANDIDATES


def active_candidates_for_meter(meter: vocab.MeterId) -> tuple[Candidate, ...]:
    """`candidates_for_meter()` の rehearsal 対応版（`active_candidates()` 由来）。"""
    return tuple(c for c in active_candidates() if c.meter == meter)
