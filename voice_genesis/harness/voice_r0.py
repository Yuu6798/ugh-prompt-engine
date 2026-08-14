"""
VT-1: R0 Diagnostic Synth -- 仕様準拠再実装（自己完結性テスト）

設計書「UGH Voice Genesis Engine v0.2」§4.3 の R0 記述 *のみ* を仕様として、
そこから独立に再実装したもの。既存の examples/r0_diagnostic/voice_r0.py は
参照していない（本テストの目的は「設計書の文章だけから同じ設計を
再構築できるか」の検証であるため）。

§4.3 R0 要求項目（この実装が満たすべき機能）:
  - 加算合成倍音源（spectral tilt + 高次減衰、整形ノイズ成分 = breathiness）
  - 5 声区 register mixer（MIDI 閾値のシグモイド混合）
  - フォルマント包絡フィルタ（母音 /a/ 相当、3-4 フォルマント）
  - 高音域 F1 追従（F0 > F1 のとき F1 が F0 を追う「ソプラノ式フォルマント
    チューニング」）
  - vibrato / jitter（seed 固定の決定論）

§3.1 応答関数（本実装が明示的可読関数として実装するもの）:
  breathiness   = f(pitch, intensity, register)
  formant_shift = g(phoneme, pitch, intensity)
  harmonic_tilt = h(pitch, intensity, source_mode)
  register_mix  = r(pitch, intensity, transition_state)

設計書の文章だけでは決定できなかった値・式・構造は、すべて
results/underspec_log.md に §番号込みで記録する（本モジュール内のコメントに
[UNDERSPEC-n] マーカーを付け、ログの行番号と対応させる）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple

import numpy as np

SR = 22050
NOTE_DUR_SEC = 1.5

REGISTERS = ("chest", "mix", "head", "falsetto", "whistle")


# ---------------------------------------------------------------------------
# VoiceGenome (§3.1 / §3.3 の趣旨に沿う dataclass)
# ---------------------------------------------------------------------------


@dataclass
class SourceParams:
    """励振（加算合成倍音源）パラメータ。harmonic_tilt = h(pitch, intensity, source_mode) を担う。"""

    reference_midi: float = 60.0  # 応答関数の基準ピッチ (C4)
    reference_intensity: float = 0.7  # 応答関数の基準 intensity（[UNDERSPEC-1] 参照）
    base_tilt_db_per_oct: float = -10.0  # 基準ピッチ/intensity での spectral tilt
    tilt_pitch_sensitivity_db_per_oct_per_oct: float = -1.5  # ピッチが 1 oct 上がるごとの tilt 変化
    tilt_intensity_sensitivity_db_per_oct: float = 6.0  # intensity が +1 のときの tilt 変化（大きいほど明るい）
    source_mode: str = "modal"  # [UNDERSPEC-2]
    source_mode_tilt_gain: Dict[str, float] = field(
        default_factory=lambda: {"modal": 1.0, "breathy": 0.6, "pressed": 1.3}
    )
    n_harmonics: int = 60
    extra_decay_start_harmonic: int = 8  # [UNDERSPEC-3] 「高次減衰」の折れ点
    extra_decay_db_per_harmonic: float = 0.22  # [UNDERSPEC-3]


@dataclass
class ResonanceParams:
    """フォルマント包絡（母音 /a/ 相当）。formant_shift = g(phoneme, pitch, intensity) を担う。"""

    phoneme: str = "a"  # R0 は /a/ のみ対応（設計書 §4.3 記述どおり）
    formant_scale: float = 1.0  # 声道長プロキシ（一様スケール）
    base_formants_hz: Tuple[float, float, float, float] = (800.0, 1150.0, 2900.0, 3900.0)  # [UNDERSPEC-4]
    bandwidths_hz: Tuple[float, float, float, float] = (80.0, 90.0, 120.0, 130.0)  # [UNDERSPEC-4]
    # 高音域 F1 追従（ソプラノ式フォルマントチューニング）[UNDERSPEC-5]
    f1_tracking_onset_ratio: float = 1.05  # F0 が F1_base の何倍を超えたら追従を開始するか
    f1_tracking_width_ratio: float = 0.15  # シグモイド遷移幅（F1_base に対する比）
    f1_tracking_target_ratio: float = 0.95  # 追従先 = F0 * この比


@dataclass
class NoiseParams:
    """整形ノイズ成分 = breathiness。breathiness = f(pitch, intensity, register) を担う。"""

    base_breathiness: float = 0.05  # 基準 intensity・chest 声区での noise/harmonic 振幅比
    intensity_sensitivity: float = -0.10  # [UNDERSPEC-1] intensity が +1 のときの変化（大きいほど息漏れ減）
    # [UNDERSPEC-6] register 別ゲイン。register_mix は凸結合（重み和=1）なので
    # breathiness の register 項はこの辞書の値域を超えない。値域は [0, ~0.9]
    # に収まるよう選定した（校正メモ: 当初 whistle=2.2 相当の値を置いたところ
    # breathiness > 1（ノイズ RMS が倍音 RMS を上回る）となり、高音域で
    # 周期性が事実上消滅し自前 F0 推定器がオクターブ誤りを起こす縮退が実測
    # された。§4.3 は数値を与えていないため、この失敗を踏まえ「breathiness
    # は register 項単独では 1.0 未満に収まるべき」という制約を自分で置いて
    # 再校正した）。
    register_gain: Dict[str, float] = field(
        default_factory=lambda: {
            "chest": 0.0,
            "mix": 0.08,
            "head": 0.18,
            "falsetto": 0.32,
            "whistle": 0.50,
        }
    )


@dataclass
class RegisterParams:
    """5 声区 register mixer（MIDI 閾値のシグモイド混合）。register_mix = r(pitch, intensity, transition_state) を担う。"""

    chest_to_mix_midi: float = 52.0  # [UNDERSPEC-7]
    mix_to_head_midi: float = 62.0  # [UNDERSPEC-7]
    head_to_falsetto_midi: float = 74.0  # [UNDERSPEC-7]
    falsetto_to_whistle_midi: float = 88.0  # [UNDERSPEC-7]
    transition_width: float = 3.0  # 半音単位、全境界共通 [UNDERSPEC-7]


@dataclass
class MicroprosodyParams:
    """vibrato / jitter（seed 固定の決定論）。"""

    vibrato_rate_hz: float = 5.5  # [UNDERSPEC-8]
    vibrato_depth_cents: float = 45.0  # [UNDERSPEC-8]
    jitter_pct_of_period: float = 0.6  # [UNDERSPEC-8]
    jitter_seed: int = 0


@dataclass
class RangeParams:
    midi_min: float = 36.0  # C2
    midi_max: float = 96.0  # C7


@dataclass
class VoiceGenome:
    name: str
    source: SourceParams = field(default_factory=SourceParams)
    resonance: ResonanceParams = field(default_factory=ResonanceParams)
    noise: NoiseParams = field(default_factory=NoiseParams)
    register: RegisterParams = field(default_factory=RegisterParams)
    microprosody: MicroprosodyParams = field(default_factory=MicroprosodyParams)
    range: RangeParams = field(default_factory=RangeParams)


# ---------------------------------------------------------------------------
# 応答関数（§3.1）
# ---------------------------------------------------------------------------


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def register_mix(midi: float, reg: RegisterParams) -> Dict[str, float]:
    """register_mix = r(pitch, intensity, transition_state)

    [UNDERSPEC-9] intensity と transition_state の効果は §4.3/§3.1 に式が
    存在しないため、R0 では pitch のみに依存する memoryless な関数として実装
    する（transition_state は「直前フレームの重み」等の履歴依存を想定しうるが、
    本 VT は 1 音・定常母音ロングトーンのみを対象とするため履歴は導入しない）。
    """
    w = reg.transition_width
    s1 = _sigmoid((midi - reg.chest_to_mix_midi) / w)
    s2 = _sigmoid((midi - reg.mix_to_head_midi) / w)
    s3 = _sigmoid((midi - reg.head_to_falsetto_midi) / w)
    s4 = _sigmoid((midi - reg.falsetto_to_whistle_midi) / w)

    w_chest = 1 - s1
    w_mix = s1 * (1 - s2)
    w_head = s2 * (1 - s3)
    w_falsetto = s3 * (1 - s4)
    w_whistle = s4

    return {
        "chest": float(w_chest),
        "mix": float(w_mix),
        "head": float(w_head),
        "falsetto": float(w_falsetto),
        "whistle": float(w_whistle),
    }


def breathiness(midi: float, intensity: float, weights: Dict[str, float], noise: NoiseParams) -> float:
    """breathiness = f(pitch, intensity, register)

    pitch の効果は register 経由（register_mix が pitch の関数）で入る。
    """
    reg_term = sum(weights[r] * noise.register_gain[r] for r in REGISTERS)
    val = noise.base_breathiness + noise.intensity_sensitivity * (intensity - 0.7) + reg_term
    # 安全クランプ: breathiness はノイズ/倍音の振幅比。1.0 以上ではノイズが
    # 倍音を凌駕し周期性が事実上消滅する（実測で自前 F0 推定器のオクターブ
    # 誤りを誘発することを確認済み、cf. register_gain のコメント）。
    return float(max(0.0, min(0.95, val)))


def harmonic_tilt_db_per_oct(midi: float, intensity: float, src: SourceParams) -> float:
    """harmonic_tilt = h(pitch, intensity, source_mode)"""
    oct_from_ref = (midi - src.reference_midi) / 12.0
    mode_gain = src.source_mode_tilt_gain.get(src.source_mode, 1.0)
    tilt = (
        src.base_tilt_db_per_oct
        + src.tilt_pitch_sensitivity_db_per_oct_per_oct * oct_from_ref * mode_gain
        + src.tilt_intensity_sensitivity_db_per_oct * (intensity - src.reference_intensity) * mode_gain
    )
    return float(tilt)


def formant_shift(f0: float, res: ResonanceParams) -> Tuple[float, float, float, float]:
    """formant_shift = g(phoneme, pitch, intensity)

    [UNDERSPEC-10] intensity の直接的な効果は §4.3 に記述がないため未実装
    （R0 では formant_shift は phoneme 固定 (/a/) + pitch のみに依存する）。
    高音域 F1 追従（ソプラノ式フォルマントチューニング）のみを実装する。
    """
    f1_base, f2_base, f3_base, f4_base = (f * res.formant_scale for f in res.base_formants_hz)

    onset = f1_base * res.f1_tracking_onset_ratio
    width = f1_base * res.f1_tracking_width_ratio
    blend = float(_sigmoid((f0 - onset) / max(width, 1e-6)))
    f1_target = f0 * res.f1_tracking_target_ratio
    f1_eff = f1_base + blend * (f1_target - f1_base)
    f1_eff = max(f1_eff, f1_base)  # 追従は上方向のみ

    return (f1_eff, f2_base, f3_base, f4_base)


def formant_filter_gain(freq_hz: np.ndarray, formants: Tuple[float, float, float, float], res: ResonanceParams) -> np.ndarray:
    """並列共振（2 極近似）によるフォルマント包絡の振幅利得。

    [UNDERSPEC-11] フィルタの具体形（極数・並列/直列・重み）は §4.3 に記述が
    ないため、標準的な並列 2 極共振近似（Lorentzian 型）を採用する:
        M_i(f) = 1 / sqrt(1 + ((f^2 - F_i^2) / (f * B_i))^2)
    総利得 = 各フォルマントの線形振幅の和（parallel formant filter bank）。
    """
    total = np.zeros_like(freq_hz, dtype=np.float64)
    bws = res.bandwidths_hz
    for fi, bi in zip(formants, bws):
        denom = np.maximum(freq_hz * bi, 1e-6)
        ratio = (freq_hz**2 - fi**2) / denom
        mag = 1.0 / np.sqrt(1.0 + ratio**2)
        total += mag
    return total


# ---------------------------------------------------------------------------
# レンダラ
# ---------------------------------------------------------------------------


def render_note(
    genome: VoiceGenome,
    midi: float,
    intensity: float = 0.7,
    duration_sec: float = NOTE_DUR_SEC,
    sr: int = SR,
) -> np.ndarray:
    """1 音・母音ロングトーンを合成する（§4.3 R0 の要求機能をすべて経由）。"""
    n = int(round(duration_sec * sr))
    t = np.arange(n) / sr

    f0_base = 440.0 * 2.0 ** ((midi - 69.0) / 12.0)

    mp = genome.microprosody
    vibrato_cents = mp.vibrato_depth_cents * np.sin(2 * np.pi * mp.vibrato_rate_hz * t)

    rng = np.random.default_rng(mp.jitter_seed + int(round(midi * 100)))
    period_sec = 1.0 / max(f0_base, 1e-6)
    n_periods_est = max(int(duration_sec / period_sec), 8)
    jitter_walk_coarse = np.cumsum(rng.normal(0.0, 1.0, size=n_periods_est))
    jitter_walk_coarse = jitter_walk_coarse - np.linspace(0, jitter_walk_coarse[-1], n_periods_est)  # 直流ドリフト除去
    coarse_t = np.linspace(0, duration_sec, n_periods_est)
    jitter_cents = np.interp(t, coarse_t, jitter_walk_coarse) * mp.jitter_pct_of_period * 100.0 / 50.0

    f0_t = f0_base * (2.0 ** ((vibrato_cents + jitter_cents) / 1200.0))

    weights = register_mix(midi, genome.register)
    breath = breathiness(midi, intensity, weights, genome.noise)
    tilt_db_per_oct = harmonic_tilt_db_per_oct(midi, intensity, genome.source)
    formants = formant_shift(f0_base, genome.resonance)

    phase = 2 * np.pi * np.cumsum(f0_t) / sr

    harmonic_signal = np.zeros(n, dtype=np.float64)
    nyq = sr / 2.0
    src = genome.source
    for k in range(1, src.n_harmonics + 1):
        f_k = k * f0_base
        if f_k >= nyq * 0.95:
            break
        db = tilt_db_per_oct * np.log2(k) - src.extra_decay_db_per_harmonic * max(
            0, k - src.extra_decay_start_harmonic
        )
        amp_k = 10.0 ** (db / 20.0)
        filt_gain = formant_filter_gain(np.array([f_k]), formants, genome.resonance)[0]
        harmonic_signal += amp_k * filt_gain * np.sin(k * phase)

    # 整形ノイズ（breathiness）: 白色雑音を同じフォルマントフィルタで整形
    freqs_full = np.fft.rfftfreq(n, d=1.0 / sr)
    noise_white = rng.normal(0.0, 1.0, size=n)
    noise_spec = np.fft.rfft(noise_white)
    noise_filt_gain = formant_filter_gain(freqs_full, formants, genome.resonance)
    noise_shaped = np.fft.irfft(noise_spec * noise_filt_gain, n=n)

    harm_rms = float(np.sqrt(np.mean(harmonic_signal**2)) + 1e-12)
    noise_rms = float(np.sqrt(np.mean(noise_shaped**2)) + 1e-12)
    noise_shaped = noise_shaped * (harm_rms * breath / noise_rms)

    signal = harmonic_signal + noise_shaped

    # attack/release フェード（クリックノイズ回避。§4.3 に明記なし）[UNDERSPEC-12]
    fade_n = int(0.03 * sr)
    fade_n = min(fade_n, n // 4) if n >= 4 else 0
    if fade_n > 0:
        env = np.ones(n)
        env[:fade_n] = np.linspace(0, 1, fade_n)
        env[-fade_n:] = np.linspace(1, 0, fade_n)
        signal = signal * env

    # 出力レベル正規化（目標 RMS）[UNDERSPEC-12]
    target_rms = 0.1
    cur_rms = float(np.sqrt(np.mean(signal**2)) + 1e-12)
    if cur_rms > 1e-9:
        signal = signal * (target_rms / cur_rms)

    return signal.astype(np.float64)


def voice_a() -> VoiceGenome:
    return VoiceGenome(name="voice_A")


def voice_b() -> VoiceGenome:
    """voice_A と対照的なパラメータ（因果検証用の第 2 Genome）。"""
    return VoiceGenome(
        name="voice_B",
        source=SourceParams(
            base_tilt_db_per_oct=-16.0,
            tilt_pitch_sensitivity_db_per_oct_per_oct=-0.8,
            tilt_intensity_sensitivity_db_per_oct=9.0,
            source_mode="breathy",
        ),
        resonance=ResonanceParams(
            formant_scale=1.18,  # 声道が短い＝周波数が高い方向（例えば小柄な声質）
        ),
        noise=NoiseParams(
            base_breathiness=0.14,
            register_gain={"chest": 0.0, "mix": 0.15, "head": 0.32, "falsetto": 0.55, "whistle": 0.75},
        ),
        register=RegisterParams(
            chest_to_mix_midi=50.0,
            mix_to_head_midi=60.0,
            head_to_falsetto_midi=71.0,
            falsetto_to_whistle_midi=85.0,
            transition_width=2.0,
        ),
        microprosody=MicroprosodyParams(
            vibrato_rate_hz=6.2,
            vibrato_depth_cents=65.0,
            jitter_pct_of_period=1.0,
            jitter_seed=7,
        ),
    )
