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
    意味する。v1.2 では全候補が未宣言だった。**v1.3 §X1 preregistration**:
    M2_SPECTRAL_TILT の harmonic 12 候補（OLS 6 + THEILSEN 6）のみが
    `DetectionPredicate(field="hnr_acf_db", min_value=-5.0)` を宣言する
    （`_M2T_HARMONIC_DETECTION_PREDICATE`）。宣言により
    `candidate_space_sha()` の payload が変わる（v1.2 の「未宣言候補では
    sha 不変」は、未宣言候補にのみ成り立つ主張として引き続き有効）。"""
    abstention_reasons: frozenset[vocab.MissingReason] = frozenset()
    """RUN10-CAL-v1.4 §前提 2 経路 (B)/§前提 4 preregistration
    （`DESIGN_VG_METER_CAL_DEBT_v1.4.md`）: 候補が negative control 行で
    「正しく棄権した」と事前登録する `vocab.MissingReason` の閉集合（既定
    空）。`fixtures.controls.abstained(output, candidate)` が
    `output.missing_reason in candidate.abstention_reasons and not
    output.ineligible` として消費する——`SANCTIONED_ABSTENTIONS`（§前提 3、
    経路 (A): F0 prepass skip で record 自体が皆無になるケース）とは別の
    閉語彙で、こちらは経路 (B): record は存在し `MeterOutput.missing_reason`
    が立つケースを対象とする。v1.4 で宣言するのは
    `M2A-B0-AUTOCORR-PERIODICITY: {OUTPUT_MISSING}`（P2 census
    `scratchpad/v14/p23/p23_report.txt` §5.2 PASS: 正例 18/18 で
    `OUTPUT_MISSING` 0 件）と、**F0_CONTROL 全 5 候補**（`F0-B0-CURRENT`
    + `F0-PYIN-*` 4 件、いずれも `{OUTPUT_MISSING}`。PR #354 round 3 追補の
    F0 census `scratchpad/v14/p2f0/p2f0_report.txt`: 正例 12/12
    measured&detected・`OUTPUT_MISSING` 0 件、SILENCE 3/3 が
    `OUTPUT_MISSING`）の計 6 候補。宣言は `candidate_space_sha()` の payload へ
    含まれる（空集合の候補では従来どおりキー自体を出力しない）。"""
    truth_polarity: int | None = None
    """RUN10-CAL-v1.4 §前提 5 preregistration
    （`DESIGN_VG_METER_CAL_DEBT_v1.4.md`）: DIRECTIONAL 候補の
    construct 変化方向と truth（injected_noise_fraction 等）の変化方向の
    関係（`+1` = 同方向, `-1` = 逆方向）。`None` は「宣言なし」であり
    DIRECTIONAL 不適格（`selection_stage.build_candidate_criteria` が
    `claim_scope_report` で `DIAGNOSTIC_ONLY` へ cap し、理由
    `NO_POLARITY` を記録する）。`observables.apply_polarity()` が
    `polarity * delta_output` として tau/reversal/`DirectionalPair.
    correct_sign` の入力に一様に適用する（`gates.py` 自体は無変更）。
    構築時に `{+1, -1, None}` のいずれかであることを検証する
    （`Candidate.__post_init__`）。v1.4 で宣言するのは APERIODICITY_GT の
    `harmonic_to_noise_ratio` 系（`-1`。HNR は noise fraction と逆相関）と
    `injected_noise_fraction` 系（`+1`）、`world_d4c_aperiodicity` 系
    （`+1`。P3 census は pyworld 不在で実測未検証——construct の物理から
    宣言するのみ）のみ。他 family は v1.4 では宣言しない。"""

    def __post_init__(self) -> None:
        if self.truth_polarity not in (1, -1, None):
            raise ValueError(
                f"Candidate.truth_polarity must be +1, -1, or None; got {self.truth_polarity!r} "
                f"(candidate_id={self.candidate_id!r})"
            )

    def params_dict(self) -> dict[str, object]:
        return dict(self.parameters)


def _params(**kwargs: object) -> tuple[tuple[str, object], ...]:
    return tuple(sorted(kwargs.items()))


# ---------------------------------------------------------------------------
# F0_CONTROL（5 候補: B0 + 4 pyin。claim-critical 外・上流 control）
# ---------------------------------------------------------------------------

_F0_MISSING_RULE = "全フレーム無声/推定失敗 → OUTPUT_MISSING（縮退代入なし）。"

#: RUN10-CAL-v1.4 §前提 4 preregistration（PR #354 round 3 追補、2026-09-09）:
#: F0_CONTROL 全候補が `OUTPUT_MISSING` を「正しい棄権」として宣言する。
#: **物理的根拠**: 無声対照（SILENCE / NOISE_ONLY / TOO_SHORT）に基本周波数は
#: 存在しない——F0 推定器が有声フレームを 1 本も見つけずに `OUTPUT_MISSING` を
#: 返すのは、誤検出でも実装欠陥でもなく構成概念どおりの正しい非検出である
#: （`_F0_MISSING_RULE`「全フレーム無声/推定失敗 → OUTPUT_MISSING」の負例側の
#: 現れ方そのもの）。
#: **census 根拠**（`scratchpad/v14/p2f0/p2f0_report.txt`、`--repeats 3
#: --max-cells 30 --dump-values`、schema diagnose/0.4 の `census` block）:
#: 5 候補すべてで正例 12/12 が measured&detected（`OUTPUT_MISSING` 0 件、
#: confound 54/54 も同様に 0 件）、SILENCE は 5 候補すべてで 3/3 が
#: `OUTPUT_MISSING`。すなわち「正例では同一理由の棄権が 1 件も無い」という
#: v1.4 の宣言条件を 5 候補全件が満たす。
#: **宣言が救わないもの**: `negative_control_false_fire`（実発火）は本宣言と
#: 無関係のまま——F0-B0-CURRENT の TOO_SHORT 3/3 発火、pyin 3 候補の
#: NOISE_ONLY 1〜2/3 発火は引き続き失敗として算入される。
_F0_ABSTENTION_REASONS = frozenset({vocab.MissingReason.OUTPUT_MISSING})

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
            # v1.4 §前提 4 preregistration（PR #354 round 3 追補）:
            # 無声対照に F0 は存在しない → unvoiced な `OUTPUT_MISSING` は
            # 正しい棄権（`_F0_ABSTENTION_REASONS` の census 根拠を参照）。
            abstention_reasons=_F0_ABSTENTION_REASONS,
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
                # v1.4 §前提 4 preregistration（PR #354 round 3 追補）:
                # 無声対照に F0 は存在しない → `librosa.pyin` が有声フレーム
                # 0 本で返す `OUTPUT_MISSING`（`candidates/impl/f0_pyin.py`
                # L40-41 → L49-50）は正しい棄権。
                abstention_reasons=_F0_ABSTENTION_REASONS,
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

#: RUN10-CAL v1.3 §X1 preregistration: TILT harmonic 12 候補（OLS 6 + THEILSEN 6）
#: の fire 判定を primary output `tilt_db_per_oct` の present/finite ではなく
#: harmonicity 補助値 `hnr_acf_db` の閾値で行う。閾値 -5.0 dB は段階 2 実測
#: （WP-A、`scratchpad/v13/wpa_report.md` §3 H-T1）の
#: 正例 [-1.915, +0.747] dB / NOISE_ONLY [-9.878, -9.758] dB の中間（余裕
#: 7.8 dB）に置いた。**既知の限界**: TILT_GT の負例集合は SILENCE / NOISE_ONLY
#: のみ（`fixtures.matrix._tilt_rows()` の `negative_n=2`）であり、本 predicate は
#: PURE_SINE（完全 harmonic、FORMANT_GT 行の実測 `hnr_acf_db` = 0.762）に対して
#: 非発火を保証しない。これは「測定器を通すための対照変更」ではなく、既に凍結
#: された対照集合に対する測定器の棄権条件の宣言である（v1.2 §W0 ルール 7）。
#: B0-HYBRID（ceiling=NONE）は宣言しない。
M2T_HNR_DETECTION_FIELD = "hnr_acf_db"
M2T_HNR_DETECTION_MIN_DB = -5.0
_M2T_HARMONIC_DETECTION_PREDICATE = DetectionPredicate(
    field=M2T_HNR_DETECTION_FIELD, min_value=M2T_HNR_DETECTION_MIN_DB
)


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
                detection_predicate=_M2T_HARMONIC_DETECTION_PREDICATE,
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
                detection_predicate=_M2T_HARMONIC_DETECTION_PREDICATE,
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
            # v1.4 §前提 4 preregistration (P2 census PASS §5.2): `hnr_db_approx`
            # が非有限（周期成分なし）を示す OUTPUT_MISSING は正しい棄権。
            abstention_reasons=frozenset({vocab.MissingReason.OUTPUT_MISSING}),
            # v1.4 §前提 5 preregistration (P3 census consistent, tau=-0.9487):
            # HNR は injected_noise_fraction と逆相関。
            truth_polarity=-1,
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
                # v1.4 §前提 5 preregistration (P3 census consistent,
                # tau=-0.9487): HNR は injected_noise_fraction と逆相関。
                # v1.4 は `abstention_reasons` を本 family には宣言しない
                # （P2 census: NOISE_ONLY 負例で実発火が観測され、棄権では
                # 救われない——`p23_report.txt` §5.2 参考情報）。
                truth_polarity=-1,
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
                # v1.4 §前提 5 preregistration (P3 census consistent,
                # tau=+0.9487): residual_fraction は injected_noise_fraction
                # と同方向。
                truth_polarity=1,
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
                # v1.4 §前提 5 preregistration: WORLD の aperiodicity は
                # construct の物理（noise 増で増加）から `+1` を宣言する。
                # 実測未検証（pyworld 不在）——P3 census
                # (`scratchpad/v14/p23/p23_report.txt` §5.4) は本環境に
                # pyworld が無いため全 cell ineligible となり、tau は
                # 算出不能（NA。confirm も反証もできない）。反証されては
                # いないため v1.4 は宣言を維持するが、実測裏付けは pyworld
                # 導入後の再 census に残る（v1.4 doc §Y4「答えていない問い」）。
                truth_polarity=1,
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
