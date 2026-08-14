"""singer/render_song.py — R0.9 統合レンダラ（S1-S4 の統合、決定論）。

パイプライン:
  1. score.py + performance.py で曲全体の F0 / 振幅エンベロープ・タイム
     ラインを構築（オーバーサンプル sr 上）。
  2. 各ノートを子音・母音のサブ区間に分割し（調音マナー別のタイミング）、
     曲全体の時変フォルマント目標タイムライン（コアーティキュレーション
     補間込み）と、有声/無声（voicing）エンベロープを構築する。
  3. glottal.py で声帯パルス励振（jitter/shimmer込み）を生成し、
     声門周期同期の呼気ノイズ（breathiness、Genome 契約の応答関数を再利用）
     を加算。voicing エンベロープで無声区間をミュートする。
  4. 子音の独立ノイズ成分（摩擦音・破裂音バースト）を加算。
  5. formant_tv.py の時変フィルタ（overlap-add、クリックなし）を全曲一括で
     適用。
  6. 振幅エンベロープ（フレーズ強弱・ブレス）を掛ける。
  7. オーバーサンプルからターゲット sr へアンチエイリアシングデシメーション
     （scipy.signal.resample_poly、決定論・多相 FIR）。

voice_A / voice_B の Genome は `proto1/genome.py` の VoiceGenome（P1 スキーマ）
をそのまま import・流用して定義する（vt_harness/proto1 は無改変・import
のみ）。R0.9 独自の genome -> レンダラパラメータ写像は本ファイル内
（`_glottal_params_from_genome` 等）に閉じる。
"""
from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.signal import resample_poly

import formant_tv as ftv
import glottal as g
import performance as perf
import phoneme_jp as pj
import score as sc

_HERE = Path(__file__).resolve().parent
_VT_HARNESS_DIR = _HERE.parent / "harness"
_PROTO1_DIR = _HERE.parent / "proto1"
for _p in (_VT_HARNESS_DIR, _PROTO1_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import genome as pg  # noqa: E402  (proto1、変更禁止・import 流用のみ)
import voice_r0 as _vr0  # noqa: E402  (vt_harness、変更禁止・import 流用のみ)

SR_OUT = 22050
OVERSAMPLE_FACTOR = 4
SR_RENDER = SR_OUT * OVERSAMPLE_FACTOR


# ---------------------------------------------------------------------------
# voice_A / voice_B（proto1.genome.VoiceGenome、2 声を対照的に定義）
# ---------------------------------------------------------------------------


def voice_a() -> pg.VoiceGenome:
    return pg.build_genome(name="voice_A")


def voice_b() -> pg.VoiceGenome:
    return pg.build_genome(
        name="voice_B",
        source=pg.SourceSection(tilt=-15.0, source_mode="breathy"),
        resonance=pg.ResonanceSection(formant_scale=1.12, formant_offsets=(0.0, 0.0, 0.0, 0.0), bandwidth_scale=1.0),
        noise=pg.NoiseSection(breathiness_base=0.12, register_gains=(0.0, 0.12, 0.28, 0.5, 0.7)),
        microprosody=pg.MicroprosodySection(vibrato_rate_hz=6.0, vibrato_depth_cents=55.0, jitter_amount=0.005, jitter_seed=7),
    )


def voice_c() -> pg.VoiceGenome:
    """S2 T2-2: 「大きく暗い声道」対照ペア。memo 指定値（formant_scale≈0.87,
    tilt -13, breathiness 低, vibrato 5.0Hz/30c）を出発点に、T1 反復測定
    （`identity_metrics.measure_separation`）と S5 gate1/gate6（`gate_checks.py`、
    凍結）の両方を同時に満たす点を実測で探索した（詳細な探索過程・各実測値は
    `results_s2/identity_report.md` §T2 参照）。

    調整の経緯（根拠付き、全て実測ログあり）:
    - formant_scale 単独の調整は極めて脆弱と判明: voice_A 基準からの偏差が
      ±0.01 を超えると gate6 breathiness grip（凍結ゲート、閾値 3.0）が
      急落する（実測: fs=0.99 で grip=3.003 通過、fs=0.98 で grip=1.875 で
      不通過）。よって formant_scale は 1.0（voice_A 既定）に据え置いた
      —— memo の意図した「formant_scale による声道サイズ差」は本エンジンの
      現行 gate6 較正下では tract identity 軸として機能しない（T2-1 の
      「写像の希釈」ではなく、gate6 側の許容域が voice_A 近傍に限定される
      という別の構造的知見。詳細は identity_report.md §T2-1）
    - 代わりに **tilt**（gate6 実測で -17 まで安全）と **bandwidth_scale**
      （凍結表に無いフィールド。0.80 まで安全、0.75 で gate6 不通過）を
      主な tract 対比軸として採用。tilt=-17・bandwidth_scale=0.80 は
      物理事前分布内（tilt 境界 (-18,-3)、bandwidth_scale に凍結境界なし）
    - breathiness_base/register_gains は E2（log-mel embedding）の
      between>within 分離に強く効くと判明（実測: C 側を完全無音化＝
      breathiness_base=0.0・register_gains 全 0 にすると E2 両フレーズの
      margin が正転。中間値では E2 phrase1 のみ僅差不成立が残存）。
      voice_B の教訓（極端値の重ね掛けによる計測クロストーク）を踏まえ
      つつ、C は「息成分ゼロの硬い声」という物理的に妥当な対照点として
      採用（gate1/gate6 とも余裕を持って通過、実測値は report 参照）
    """
    return pg.build_genome(
        name="voice_C",
        source=pg.SourceSection(tilt=-17.0, source_mode="modal"),
        resonance=pg.ResonanceSection(formant_scale=1.0, formant_offsets=(0.0, 0.0, 0.0, 0.0), bandwidth_scale=0.80),
        noise=pg.NoiseSection(breathiness_base=0.0, register_gains=(0.0, 0.0, 0.0, 0.0, 0.0)),
        microprosody=pg.MicroprosodySection(vibrato_rate_hz=5.0, vibrato_depth_cents=30.0, jitter_amount=0.006, jitter_seed=11),
    )


def voice_d() -> pg.VoiceGenome:
    """S2 T2-2: 「小さく明るい声道」対照ペア（voice_C と対をなす）。voice_c()
    と同じ実測駆動の理由で formant_scale は 1.0 に据え置き、tilt/
    bandwidth_scale/breathiness_base を対比軸として使う（探索過程は
    `results_s2/identity_report.md` §T2 参照）。

    - tilt: memo 値 -6 は gate6 breathiness grip が不通過（実測 grip=2.91<3.0）。
      voice_A 基準の tilt 安全域を実測で走査し、-10 が gate1/gate6 とも
      安全（かつ bandwidth_scale の安全域が -7 より広い）ことを確認して採用
    - bandwidth_scale: tilt=-10 において 1.30 まで安全（1.35 で gate6
      breathiness grip 不通過）。voice_C の 0.80 と対称的な「広い/明るい」
      対比を狙って 1.30 を採用
    - breathiness_base: E2 分離の主駆動因子（voice_c() 参照）。D 側を
      0.40 まで上げると gate1（F0 追従）に鋭い崖がある（実測: 0.40 で
      max_cents_err=22.9 通過、0.42 で 1218.2 に急悪化 — 背景ノイズが
      過大になり F0 推定器が破綻する構造的限界）。0.40 を安全上限として採用
    """
    return pg.build_genome(
        name="voice_D",
        source=pg.SourceSection(tilt=-10.0, source_mode="modal"),
        resonance=pg.ResonanceSection(formant_scale=1.0, formant_offsets=(0.0, 0.0, 0.0, 0.0), bandwidth_scale=1.30),
        noise=pg.NoiseSection(breathiness_base=0.40, register_gains=(0.0, 0.10, 0.20, 0.30, 0.40)),
        microprosody=pg.MicroprosodySection(vibrato_rate_hz=6.3, vibrato_depth_cents=45.0, jitter_amount=0.006, jitter_seed=13),
    )


SHIMMER_AMOUNT: Dict[str, float] = {"voice_A": 0.02, "voice_B": 0.035, "voice_C": 0.02, "voice_D": 0.025}  # [0,0.1] 事前分布（memo §S1）

NOISE_BREATHINESS_GAIN = 1.3
# [UNDERSPEC-S1-6] breathiness 応答関数の値（voice_r0.breathiness、無改変で
# 再利用・0.95 クランプ込み = Genome 契約は維持）を「息ノイズ / 声帯調波
# 信号の RMS 比」へ変換する係数は memo が規定していない。ピッチ同期の
# 声帯パルス励振（R0.1 の加算正弦波スタックより単発の周期性が強い）は
# 同じ breathiness 値でも periodicity への影響が小さいことを gate 6
# クイックチェックの実測で確認したため、応答関数値をそのまま RMS 比に
# 使う（gain=1.0）のではなく `NOISE_BREATHINESS_GAIN` 倍してから RMS 比
# として適用する（Genome の応答関数自体は変更していない。あくまで新しい
# 音響エンジンでの「同じ breathiness 値をどれだけの物理ノイズ量として
# 実現するか」という写像の調整）。値は gate 6 が通る最小限として実測調整。


def _noise_ratio_from_breathiness(b: float) -> float:
    """breathiness 応答関数値 -> 息ノイズ/調波信号 RMS 比（§UNDERSPEC-S1-6 続き）。

    単純な線形ゲイン+ハードクランプ（`min(b*gain, cap)`）を試したところ、
    gain を上げるほど高音域 probe（register_gain が大きく breathiness が
    早期にクランプへ到達する音域）で sweep 全体が天井に張り付き、grip
    クイックチェックの E(intended) がかえって悪化する（sweep 端点間の
    差が縮む）ことを実測で確認した。ハードクランプの代わりに漸近的に
    0.97 へ近づく飽和関数（`1-exp(-b*gain)`）を使うことで、b が大きい
    領域でも微小ながら単調な感度を残し、全 probe で sweep 端点間の差を
    最大化する gain を探索した。
    """
    return 0.97 * (1.0 - np.exp(-b * NOISE_BREATHINESS_GAIN))


HARMONIC_ATTENUATION_PER_BREATHINESS = 0.78  # b=0.95(cap) で調波振幅を最大 74% 減衰。gate6 実測で調整


def deterministic_seed(*parts) -> int:
    """gate 4（決定論）用の安定シード生成。

    Python 組み込み `hash()` は文字列/タプルに対しプロセス起動毎にランダム化
    される（PYTHONHASHSEED、既定で有効）ため、`hash()` を直接シードに使うと
    「同一プロセス内では決定論だが、プロセスを再起動すると異なる波形になる」
    という**サイレントな非決定性**を生む。gate 4 はプロセスをまたいだ再現性
    を要求するため、`hashlib.sha256` による安定ハッシュに置き換える。
    """
    s = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(s.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], byteorder="big") % (2**31)


# ---------------------------------------------------------------------------
# genome -> R0.9 パラメータ写像
# ---------------------------------------------------------------------------


def _register_weights_and_breathiness(genome: pg.VoiceGenome, midi: float, intensity: float = 0.75) -> float:
    """breathiness の Genome 契約（応答関数・0.95クランプ）を voice_r0.py の
    既存実装（register_mix / breathiness、無改変で再利用）で評価する。
    """
    register_params = _vr0.RegisterParams(
        chest_to_mix_midi=genome.register.boundaries_midi[0],
        mix_to_head_midi=genome.register.boundaries_midi[1],
        head_to_falsetto_midi=genome.register.boundaries_midi[2],
        falsetto_to_whistle_midi=genome.register.boundaries_midi[3],
        transition_width=genome.register.transition_width,
    )
    noise_params = _vr0.NoiseParams(
        base_breathiness=genome.noise.breathiness_base,
        register_gain=dict(zip(pg.REGISTER_ORDER, genome.noise.register_gains)),
    )
    weights = _vr0.register_mix(midi, register_params)
    return _vr0.breathiness(midi, intensity, weights, noise_params)


@dataclass
class SubSegment:
    start: int
    end: int
    formants: Tuple[float, float, float, float]
    bandwidths: Tuple[float, float, float, float]
    voicing: float  # 0..1
    noise_type: Optional[str] = None  # None / 'fricative_high' / 'fricative_broad' / 'burst_k' / 'burst_t' / 'burst_g'
    noise_gain: float = 0.0


_GLIDE_TARGETS = {"w": "u", "y": "i"}

# 各子音マナーの時間配分（ms）。ノート長に対し上限比率でクランプする。
_CONSONANT_TIMING_MS = {
    "s": {"noise": 70.0},
    "h": {"noise": 55.0},
    "k": {"closure": 40.0, "burst": 15.0},
    "t": {"closure": 38.0, "burst": 12.0},
    "g": {"closure": 25.0, "burst": 10.0},
    "r": {"flap": 28.0},
    "m": {"nasal": 100.0},
    "n": {"nasal": 100.0},
    "w": {"glide": 70.0},
    "y": {"glide": 70.0},
}
_MAX_CONSONANT_FRACTION = 0.45  # ノート長に対する子音部分の上限比率


def _formant_target_for(
    vowel_or_target: str, formant_scale: float, formant_offsets, bandwidth_scale: float
) -> Tuple[Tuple[float, float, float, float], Tuple[float, float, float, float]]:
    if vowel_or_target == "N":
        return ftv.nasal_targets(formant_scale, formant_offsets, bandwidth_scale)
    return ftv.scaled_vowel_targets(vowel_or_target, formant_scale, formant_offsets, bandwidth_scale)


def build_note_subsegments(
    mora: pj.Mora,
    start: int,
    end: int,
    formant_scale: float,
    formant_offsets: Tuple[float, float, float, float],
    bandwidth_scale: float,
) -> List[SubSegment]:
    """1 ノート分をマナー別の子音+母音サブ区間へ分割する。"""
    note_len = end - start
    vowel_f, vowel_b = _formant_target_for(mora.vowel, formant_scale, formant_offsets, bandwidth_scale)

    if mora.is_moraic_nasal:
        f, b = ftv.nasal_targets(formant_scale, formant_offsets, bandwidth_scale)
        return [SubSegment(start, end, f, b, voicing=1.0)]

    if mora.onset is None or mora.is_long_vowel_mark:
        return [SubSegment(start, end, vowel_f, vowel_b, voicing=1.0)]

    onset = mora.onset
    timing = _CONSONANT_TIMING_MS.get(onset)
    if timing is None:
        return [SubSegment(start, end, vowel_f, vowel_b, voicing=1.0)]

    max_consonant_samples = int(note_len * _MAX_CONSONANT_FRACTION)

    def clamp_ms_to_samples(ms: float, sr_local: int) -> int:
        return min(int(ms / 1000.0 * sr_local), max_consonant_samples)

    sr_local = SR_RENDER
    subs: List[SubSegment] = []

    if onset in ("s", "h"):
        noise_dur = clamp_ms_to_samples(timing["noise"], sr_local)
        c_end = start + noise_dur
        noise_type = "fricative_high" if onset == "s" else "fricative_broad"
        subs.append(SubSegment(start, c_end, vowel_f, vowel_b, voicing=0.0, noise_type=noise_type, noise_gain=0.35))
        subs.append(SubSegment(c_end, end, vowel_f, vowel_b, voicing=1.0))

    elif onset in ("k", "t", "g"):
        closure_dur = clamp_ms_to_samples(timing["closure"], sr_local)
        burst_dur = clamp_ms_to_samples(timing["burst"], sr_local)
        c1 = start + closure_dur
        c2 = c1 + burst_dur
        c2 = min(c2, end)
        voiced_closure = 0.3 if onset == "g" else 0.0
        subs.append(SubSegment(start, c1, vowel_f, vowel_b, voicing=voiced_closure))
        noise_type = {"k": "burst_k", "t": "burst_t", "g": "burst_g"}[onset]
        gain = 0.22 if onset == "g" else 0.32
        subs.append(SubSegment(c1, c2, vowel_f, vowel_b, voicing=voiced_closure, noise_type=noise_type, noise_gain=gain))
        subs.append(SubSegment(c2, end, vowel_f, vowel_b, voicing=1.0))

    elif onset == "r":
        flap_dur = clamp_ms_to_samples(timing["flap"], sr_local)
        c_end = start + flap_dur
        # F3 を一時的に下げた「弾き」ターゲット
        flap_f = (vowel_f[0], vowel_f[1], vowel_f[2] * 0.85, vowel_f[3])
        subs.append(SubSegment(start, c_end, flap_f, vowel_b, voicing=0.7))
        subs.append(SubSegment(c_end, end, vowel_f, vowel_b, voicing=1.0))

    elif onset in ("m", "n"):
        nasal_dur = clamp_ms_to_samples(timing["nasal"], sr_local)
        c_end = start + nasal_dur
        nasal_f, nasal_b = ftv.nasal_targets(formant_scale, formant_offsets, bandwidth_scale)
        subs.append(SubSegment(start, c_end, nasal_f, nasal_b, voicing=1.0))
        subs.append(SubSegment(c_end, end, vowel_f, vowel_b, voicing=1.0))

    elif onset in ("w", "y"):
        glide_dur = clamp_ms_to_samples(timing["glide"], sr_local)
        c_end = start + glide_dur
        glide_vowel = _GLIDE_TARGETS[onset]
        glide_f, glide_b = ftv.scaled_vowel_targets(glide_vowel, formant_scale, formant_offsets, bandwidth_scale)
        subs.append(SubSegment(start, c_end, glide_f, glide_b, voicing=1.0))
        subs.append(SubSegment(c_end, end, vowel_f, vowel_b, voicing=1.0))

    else:
        subs.append(SubSegment(start, end, vowel_f, vowel_b, voicing=1.0))

    return [s for s in subs if s.end > s.start]


def _band_noise(n: int, sr: int, center_hz: float, width_oct: float, seed: int) -> np.ndarray:
    """対数周波数のガウス型帯域整形ノイズ（決定論、seed 固定）。"""
    rng = np.random.default_rng(seed)
    white = rng.normal(0.0, 1.0, size=n)
    spec = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n, d=1.0 / sr)
    freqs_safe = np.maximum(freqs, 1.0)
    log_ratio = np.log2(freqs_safe / center_hz)
    gain = np.exp(-0.5 * (log_ratio / width_oct) ** 2)
    shaped = np.fft.irfft(spec * gain, n=n)
    peak = np.max(np.abs(shaped)) + 1e-12
    return shaped / peak


_NOISE_BAND_SPEC = {
    "fricative_high": (6000.0, 0.6),
    "fricative_broad": (2500.0, 1.6),
    "burst_k": (2500.0, 0.5),
    "burst_t": (5000.0, 0.5),
    "burst_g": (1800.0, 0.5),
}


# ---------------------------------------------------------------------------
# 全曲レンダリング
# ---------------------------------------------------------------------------


@dataclass
class SubSegmentOut:
    start: int  # SR_OUT ドメイン
    end: int
    onset: Optional[str]
    noise_type: Optional[str]
    note_index: int


@dataclass
class RenderResult:
    wav: np.ndarray
    sr: int
    segments: List[perf.TimelineSegment]
    voicing_envelope_render: np.ndarray
    genome_name: str
    subsegments_out: List[SubSegmentOut]


def render_sakura(
    genome: pg.VoiceGenome, seed_extra: int = 0,
    notes: "Optional[List[sc.ScoreNote]]" = None, tempo_bpm: "Optional[float]" = None,
) -> RenderResult:
    """[S5 追記] `notes` を渡すと「さくらさくら」以外の任意 score
    （`score.ScoreNote` のリスト。cross-song 実証用、例: `score_umi.py`）を
    同じ R0.9 パイプラインでレンダできる（既定 None なら従来通り
    `sc.build_sakura_score()`/`sc.TEMPO_BPM` を使うため、既存呼び出しの
    挙動は完全に不変）。関数名は歴史的経緯で `render_sakura` のままだが、
    本質は「score→歌唱」の汎用レンダラである。
    """
    if notes is None:
        notes = sc.build_sakura_score()
    if tempo_bpm is None:
        tempo_bpm = sc.TEMPO_BPM
    segments, total_samples_out = perf.build_timeline(notes, SR_OUT, tempo_bpm=tempo_bpm)
    total_samples_render = total_samples_out * OVERSAMPLE_FACTOR

    # --- F0 / 振幅（out-rate で作り、線形補間で render-rate へアップサンプル） ---
    f0_out = perf.build_f0_contour(
        segments, total_samples_out, SR_OUT,
        vibrato_rate_hz=genome.microprosody.vibrato_rate_hz,
        vibrato_depth_cents=genome.microprosody.vibrato_depth_cents,
    )
    amp_out = perf.build_amplitude_envelope(segments, total_samples_out, SR_OUT)

    t_out = np.arange(total_samples_out) / SR_OUT
    t_render = np.arange(total_samples_render) / SR_RENDER
    f0_render = np.interp(t_render, t_out, f0_out)
    amp_render = np.interp(t_render, t_out, amp_out)

    # --- サブ区間（子音/母音、フォルマント目標 + voicing + ノイズ種別） ---
    formant_scale = genome.resonance.formant_scale
    formant_offsets = genome.resonance.formant_offsets
    bandwidth_scale = genome.resonance.bandwidth_scale

    all_subs: List[SubSegment] = []
    subs_out: List[SubSegmentOut] = []
    for note_idx, seg in enumerate(segments):
        s_render = seg.start_sample * OVERSAMPLE_FACTOR
        e_render = seg.end_sample * OVERSAMPLE_FACTOR
        subs = build_note_subsegments(seg.note.mora, s_render, e_render, formant_scale, formant_offsets, bandwidth_scale)
        all_subs.extend(subs)
        for s in subs:
            subs_out.append(
                SubSegmentOut(
                    start=s.start // OVERSAMPLE_FACTOR, end=s.end // OVERSAMPLE_FACTOR,
                    onset=seg.note.mora.onset, noise_type=s.noise_type, note_index=note_idx,
                )
            )

    formant_segments = [ftv.FormantSegment(s.start, s.end, s.formants, s.bandwidths) for s in all_subs]
    formants_t, bandwidths_t = ftv.interpolate_formant_timeline(formant_segments, total_samples_render, SR_RENDER, transition_ms=60.0)

    voicing_render = np.zeros(total_samples_render, dtype=np.float64)
    for s in all_subs:
        voicing_render[s.start:s.end] = s.voicing
    # voicing envelope も短い時定数でスムージング（クリック防止）
    smooth_n = max(int(SR_RENDER * 0.008), 1)
    kernel = np.ones(smooth_n) / smooth_n
    voicing_render = np.convolve(voicing_render, kernel, mode="same")

    # --- 声帯パルス励振 + 声門同期呼気ノイズ ---
    tilt = genome.source.tilt
    oq = g.open_quotient_from_tilt(tilt)
    seed_base = deterministic_seed(genome.name, seed_extra)
    glot = g.generate_glottal_excitation(
        f0_render, SR_RENDER, oq=oq,
        jitter_amount=genome.microprosody.jitter_amount,  # [UNDERSPEC-S1-1] 倍率なし。詳細は underspec_log_s1.md
        shimmer_amount=SHIMMER_AMOUNT.get(genome.name, 0.02),
        seed=seed_base,
    )

    voiced_signal = glot.excitation * voicing_render

    # breathiness: ノート中央 MIDI を代表値として応答関数を評価し、声門周期
    # 同期のノイズエンベロープで振幅変調する。
    #
    # [UNDERSPEC-S1-5] 当初、曲全体で 1 回だけ noise_rms を harm_rms に
    # マッチさせてから breath_gain の全曲平均を掛ける実装だったが、これは
    # 「曲全体平均の声質」でノイズレベルを決めてしまうため、局所的に励振が
    # 弱い/breathiness が高いノートでは意図した比率を大きく超えるノイズが
    # 乗ってしまう（machine gate 実測: voice_B の低音長音ノートで noise が
    # 支配的になり measure_v3 のオクターブ誤りを誘発した）。
    # **ノートごとに** harmonic RMS を測り、そのノート自身の breathiness 値
    # に比例するようノイズを個別スケーリングする方式に修正した。
    rng_noise = np.random.default_rng(seed_base + 100)
    white = rng_noise.normal(0.0, 1.0, size=total_samples_render)
    breathiness_noise = np.zeros(total_samples_render, dtype=np.float64)
    for seg in segments:
        s_render, e_render = seg.start_sample * OVERSAMPLE_FACTOR, seg.end_sample * OVERSAMPLE_FACTOR
        b = _register_weights_and_breathiness(genome, seg.note.midi)
        note_voiced = voiced_signal[s_render:e_render]
        note_env = glot.noise_envelope[s_render:e_render]
        note_white = white[s_render:e_render]
        note_voicing = voicing_render[s_render:e_render]
        raw_noise = note_white * note_env * note_voicing
        # ノイズ目標レベルは「減衰前」の調波 RMS を基準にする（減衰後を基準に
        # すると分子・分母が同じ係数で動いて相殺し、減衰の効果が SNR に
        # 全く反映されない実装ミスを実測で発見した。§UNDERSPEC-S1-6 参照）。
        harm_rms_pre = np.sqrt(np.mean(note_voiced**2)) + 1e-9
        raw_noise_rms = np.sqrt(np.mean(raw_noise**2)) + 1e-9
        b_eff = _noise_ratio_from_breathiness(b)
        breathiness_noise[s_render:e_render] = raw_noise * (harm_rms_pre * b_eff / raw_noise_rms)
        # 声門閉鎖の効率低下（breathy な発声ほど調波成分自体も弱まる、という
        # 生理的に妥当な副作用）を軽度に加える。ノイズ目標は上で減衰前の
        # 調波 RMS を基準にしているため、この減衰は正味で SNR を下げる
        # 方向に効く。
        harm_atten = 1.0 - HARMONIC_ATTENUATION_PER_BREATHINESS * b
        voiced_signal[s_render:e_render] *= harm_atten

    source_signal = voiced_signal + breathiness_noise

    # --- 子音の独立ノイズ成分 ---
    for i, s in enumerate(all_subs):
        if s.noise_type is None:
            continue
        n = s.end - s.start
        if n <= 0:
            continue
        center, width = _NOISE_BAND_SPEC[s.noise_type]
        burst = _band_noise(n, SR_RENDER, center, width, seed=seed_base + 200 + i)
        env = np.ones(n)
        fade = min(n // 4, int(SR_RENDER * 0.005))
        if fade > 0:
            env[:fade] = np.linspace(0, 1, fade)
            env[-fade:] = np.linspace(1, 0, fade)
        source_signal[s.start:s.end] += burst * env * s.noise_gain

    # --- 時変フォルマントフィルタ ---
    filtered = ftv.apply_time_varying_formant_filter(source_signal, formants_t, bandwidths_t, SR_RENDER)

    # --- 振幅エンベロープ ---
    filtered = filtered * amp_render

    # --- レベル正規化（クリップ防止） ---
    peak = np.max(np.abs(filtered)) + 1e-9
    target_peak = 0.6
    filtered = filtered * (target_peak / peak)

    # --- アンチエイリアシングデシメーション（決定論・多相FIR） ---
    wav_out = resample_poly(filtered, up=1, down=OVERSAMPLE_FACTOR)

    return RenderResult(
        wav=wav_out.astype(np.float64), sr=SR_OUT, segments=segments,
        voicing_envelope_render=voicing_render, genome_name=genome.name, subsegments_out=subs_out,
    )


def render_sustained_vowel(
    genome: pg.VoiceGenome, midi: float, vowel: str = "a", duration_sec: float = 1.5, seed_extra: int = 0
) -> np.ndarray:
    """S5 gate #6（grip 非退行クイックチェック）用: 単一母音ロングトーンを
    R0.9 パイプライン（glottal + 時変フォルマントフィルタ）で生成する。
    performance 層のオンセット遅延・ポルタメント・フレーズ強弱は含めない
    （定常測定に特化した最小レンダラ）が、**Genome 契約であるビブラート
    （microprosody.vibrato_rate_hz / vibrato_depth_cents）はノート先頭から
    フルにかける**（vt_harness 系の従来の sustained-tone grip 計測と同じ
    前提。performance 層のオンセット遅延は「いつビブラートを掛けるかという
    演奏判断」であり Genome 自体のビブラート特性とは別レイヤー、§6.2）。
    """
    n_render = int(duration_sec * SR_RENDER)
    f0_hz = 440.0 * 2.0 ** ((midi - 69.0) / 12.0)
    t_render = np.arange(n_render) / SR_RENDER
    vibrato_cents = genome.microprosody.vibrato_depth_cents * np.sin(2 * np.pi * genome.microprosody.vibrato_rate_hz * t_render)
    f0_render = f0_hz * (2.0 ** (vibrato_cents / 1200.0))

    oq = g.open_quotient_from_tilt(genome.source.tilt)
    seed_base = deterministic_seed(genome.name, "sustained", vowel, midi, seed_extra)
    glot = g.generate_glottal_excitation(
        f0_render, SR_RENDER, oq=oq,
        jitter_amount=genome.microprosody.jitter_amount,
        shimmer_amount=SHIMMER_AMOUNT.get(genome.name, 0.02),
        seed=seed_base,
    )

    breathiness_val = _register_weights_and_breathiness(genome, midi)

    rng_noise = np.random.default_rng(seed_base + 100)
    white = rng_noise.normal(0.0, 1.0, size=n_render)
    breathiness_noise = white * glot.noise_envelope * breathiness_val

    harm_rms_pre = np.sqrt(np.mean(glot.excitation**2)) + 1e-9
    noise_rms = np.sqrt(np.mean(breathiness_noise**2)) + 1e-9
    breathiness_val_eff = _noise_ratio_from_breathiness(breathiness_val)
    breathiness_noise = breathiness_noise * (harm_rms_pre * breathiness_val_eff / noise_rms)

    harm_atten = 1.0 - HARMONIC_ATTENUATION_PER_BREATHINESS * breathiness_val
    excitation = glot.excitation * harm_atten

    source_signal = excitation + breathiness_noise

    formants, bandwidths = ftv.scaled_vowel_targets(
        vowel, genome.resonance.formant_scale, genome.resonance.formant_offsets, genome.resonance.bandwidth_scale
    )
    formants_t = np.tile(np.array(formants), (n_render, 1))
    bandwidths_t = np.tile(np.array(bandwidths), (n_render, 1))
    filtered = ftv.apply_time_varying_formant_filter(source_signal, formants_t, bandwidths_t, SR_RENDER)

    fade_n = int(SR_RENDER * 0.03)
    env = np.ones(n_render)
    env[:fade_n] = np.linspace(0, 1, fade_n)
    env[-fade_n:] = np.linspace(1, 0, fade_n)
    filtered = filtered * env

    # RMS 正規化（peak 正規化だと breathiness による crest factor の変化が
    # 出力 RMS に機械的に混入し、grip クイックチェックの rms 側特徴が
    # breathiness と本質的でない相関を持ってしまうため、目標 RMS へ正規化
    # する。念のため軽いソフトリミッタで安全域を確保する）。
    target_rms = 0.12
    cur_rms = float(np.sqrt(np.mean(filtered**2))) + 1e-9
    filtered = filtered * (target_rms / cur_rms)
    filtered = np.tanh(filtered / 0.9) * 0.9

    wav_out = resample_poly(filtered, up=1, down=OVERSAMPLE_FACTOR)
    return wav_out.astype(np.float64)
