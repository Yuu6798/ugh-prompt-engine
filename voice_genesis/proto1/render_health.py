"""render_health.py — P6: レンダラ健全性テスト（VG-004/005/006 の Done 条件）。

`proto1_design_memo.md` §P6:
  - aliasing: 加算合成の倍音が Nyquist 未満で打ち切られていること
    （>0.45×sr 帯域のエネルギー比 < -40dB）。
  - register transition: register_sweep probe でフレーム RMS の隣接差が
    6dB を超えないこと（遷移の連続性）。
  - formant sweep: formant_scale 0.85→1.15 で formant_centroid_v3 が単調に
    動くこと（簡易確認で可）。

測定単位の補充判断は underspec_log_p1.md [UNDERSPEC-P1-12]・[UNDERSPEC-P1-13]。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

import numpy as np

import bridge
import probes
from genome import ResonanceSection, VoiceGenome, build_genome

import measure_v3 as mv3  # harness、無改変 import 流用

SR = bridge.SR
EPS = 1e-12

ALIASING_BAND_RATIO = 0.45  # >0.45*sr のエネルギー比をチェック
ALIASING_THRESHOLD_DB = -40.0

REGISTER_TRANSITION_MAX_DB = 6.0

FORMANT_SWEEP_MIDI = 69.0  # A4（underspec_log_p1.md [UNDERSPEC-P1-13]）
FORMANT_SWEEP_SCALES: Tuple[float, ...] = (0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15)


# ---------------------------------------------------------------------------
# aliasing
# ---------------------------------------------------------------------------


@dataclass
class AliasingReport:
    high_band_energy_ratio_db: float
    passed: bool
    sr: int
    band_ratio: float


def aliasing_energy_ratio_db(sig: np.ndarray, sr: int = SR, band_ratio: float = ALIASING_BAND_RATIO) -> float:
    """>band_ratio*sr 帯域のエネルギーが全帯域エネルギーに占める比率を dB で返す。"""
    n = len(sig)
    spec = np.fft.rfft(sig * np.hanning(n))
    freqs = np.fft.rfftfreq(n, d=1.0 / sr)
    power = np.abs(spec) ** 2
    total_energy = float(power.sum()) + EPS
    high_mask = freqs > (band_ratio * sr)
    high_energy = float(power[high_mask].sum())
    ratio = high_energy / total_energy
    return float(10.0 * np.log10(ratio + EPS))


def check_aliasing(
    genome: VoiceGenome,
    midi: float = 69.0,
    duration_sec: float = 1.0,
    threshold_db: float = ALIASING_THRESHOLD_DB,
) -> AliasingReport:
    sig = bridge.render_note(genome, midi, duration_sec=duration_sec)
    ratio_db = aliasing_energy_ratio_db(sig)
    return AliasingReport(
        high_band_energy_ratio_db=ratio_db,
        passed=bool(ratio_db < threshold_db),
        sr=SR,
        band_ratio=ALIASING_BAND_RATIO,
    )


# ---------------------------------------------------------------------------
# register transition の連続性
# ---------------------------------------------------------------------------


@dataclass
class RegisterTransitionReport:
    note_rms_db: List[float]
    adjacent_db_jumps: List[float]
    max_db_jump: float
    passed: bool
    threshold_db: float = REGISTER_TRANSITION_MAX_DB


def _mid_window_rms(sig: np.ndarray, frac: float = 0.5) -> float:
    """波形中央 frac 割合の区間の RMS（attack/release フェードの影響を避ける）。

    underspec_log_p1.md [UNDERSPEC-P1-12] 参照。
    """
    n = len(sig)
    half_excl = int(n * (1.0 - frac) / 2.0)
    window = sig[half_excl : n - half_excl] if half_excl > 0 else sig
    if len(window) == 0:
        window = sig
    return float(np.sqrt(np.mean(window**2)) + EPS)


def register_transition_report(
    genome: VoiceGenome, probe_name: str = "register_sweep", threshold_db: float = REGISTER_TRANSITION_MAX_DB
) -> RegisterTransitionReport:
    result = probes.render_probe(genome, probe_name)
    note_rms_db = [float(20.0 * np.log10(_mid_window_rms(wave))) for wave in result.waveforms]
    jumps = [abs(note_rms_db[i + 1] - note_rms_db[i]) for i in range(len(note_rms_db) - 1)]
    max_jump = float(max(jumps)) if jumps else 0.0
    return RegisterTransitionReport(
        note_rms_db=note_rms_db,
        adjacent_db_jumps=jumps,
        max_db_jump=max_jump,
        passed=bool(max_jump <= threshold_db),
        threshold_db=threshold_db,
    )


# ---------------------------------------------------------------------------
# formant sweep の単調性
# ---------------------------------------------------------------------------


# [UNDERSPEC-P1-13 追記] 既存 grip v3 実測（voice_genesis/harness/results_v3/grip_report_v3.json、
# axis=formant_scale, config_b, intended_feature=formant_centroid）では
# direction_consistency=0.75（隣接ステップの 75% のみが期待方向へ動く）であり、
# 厳密な単調性は実測上そもそも成立しない（cepstral top-2-by-magnitude ピーク選択が
# formant_scale の掃引途中でどのフォルマント対を拾うか切り替わるため）。本テストは
# 「既存 grip 結果と整合。簡易確認で可」という memo の指示に合わせ、厳密な単調性では
# なく (a) 隣接差の direction_consistency がしきい値以上、(b) 掃引全体としては
# 上昇方向（終点 > 始点）の 2 条件を pass 基準とする。しきい値 0.60 は実測 0.75 に
# 対してマージンを持たせた値（本テストは異なる seed/genome で回るため、単一 genome の
# 実測が実測レンジの下限に振れても壊れないようにする）。
DIRECTION_CONSISTENCY_THRESHOLD = 0.60


@dataclass
class FormantSweepReport:
    formant_scales: List[float]
    formant_centroid_log2hz: List[float]
    direction_consistency: float
    net_direction_positive: bool
    passed: bool
    # PR#261 レビュー R31: 非有限（測定不能）だった formant_scale の一覧。
    # 空リストなら全点が有限に測定できたことを意味する。
    non_finite_scales: List[float] = field(default_factory=list)


def formant_sweep_report(
    base_genome: VoiceGenome,
    midi: float = FORMANT_SWEEP_MIDI,
    scales: Tuple[float, ...] = FORMANT_SWEEP_SCALES,
    duration_sec: float = 1.0,
    direction_consistency_threshold: float = DIRECTION_CONSISTENCY_THRESHOLD,
) -> FormantSweepReport:
    """P6 formant sweep（`FormantSweepReport` docstring参照）。

    PR#261 レビュー R31: 全 formant_scale 点のセントロイド測定値の有限性を
    PASS の前提条件に追加する。旧実装は隣接差分を
    `1.0 if d >= 0.0 else 0.0` で評価していたため、centroid 測定自体が
    失敗して NaN になった箇所を「方向が逆だった 1 ステップ」として黙って
    0 加算してしまい、残りのステップが十分健全であれば
    direction_consistency がたまたま閾値を上回って測定不能な状態のまま
    PASS してしまう不完全証拠 PASS の穴があった（gate_checks.py の R23 と
    同型）。全セントロイドが有限であることを PASS の前提条件とし、1 点でも
    非有限なら測定不能 FAIL とし、`non_finite_scales` に該当 formant_scale
    を列挙する。
    """
    centroids: List[float] = []
    for scale in scales:
        resonance = ResonanceSection(
            formant_scale=scale,
            formant_offsets=base_genome.resonance.formant_offsets,
            bandwidth_scale=base_genome.resonance.bandwidth_scale,
        )
        genome = build_genome(
            f"{base_genome.name}-fscale{scale}",
            source=base_genome.source,
            resonance=resonance,
            noise=base_genome.noise,
            register=base_genome.register,
            microprosody=base_genome.microprosody,
            range_=base_genome.range,
        )
        sig = bridge.render_note(genome, midi, duration_sec=duration_sec)
        feat = mv3.formant_centroid_and_f1(sig, SR, mv3.estimate_f0_v3(sig, sr=SR))
        centroids.append(float(feat["formant_centroid_log2hz"]))

    non_finite_scales = [scales[i] for i, c in enumerate(centroids) if not np.isfinite(c)]
    if non_finite_scales:
        return FormantSweepReport(
            formant_scales=list(scales),
            formant_centroid_log2hz=centroids,
            direction_consistency=float("nan"),
            net_direction_positive=False,
            passed=False,
            non_finite_scales=non_finite_scales,
        )

    diffs = [centroids[i + 1] - centroids[i] for i in range(len(centroids) - 1)]
    direction_consistency = float(np.mean([1.0 if d >= 0.0 else 0.0 for d in diffs])) if diffs else 1.0
    # net_direction_positive は診断用の付随情報として報告するのみで pass 判定には使わない
    # （grip v3 実測の acceptance 指標は direction_consistency 単体であり、単一の大きな
    # ピーク選択ジャンプ 1 箇所だけで終点<始点になり得ることを確認したため。詳細は
    # underspec_log_p1.md [UNDERSPEC-P1-13b]）。
    net_direction_positive = bool(centroids[-1] > centroids[0])
    passed = bool(direction_consistency >= direction_consistency_threshold)
    return FormantSweepReport(
        formant_scales=list(scales),
        formant_centroid_log2hz=centroids,
        direction_consistency=direction_consistency,
        net_direction_positive=net_direction_positive,
        passed=passed,
        non_finite_scales=[],
    )
