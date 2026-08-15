"""adapter/consonants.py — VG-F1.1-B: 子音オンセット移植（WORLD パラメータ領域）。

`DESIGN_F1_adapter_v0.md` 追補 F1.1「F1.1-B 子音オンセット移植」に対応する。
singer/ S6-S9 の耳検証済みレシピ（`render_song.py` / `render_song_v2.py` /
`render_song_v5.py`）を、フォルマント極フィルタ領域から WORLD の
(sp, ap) 包絡フレーム領域へ移植する。

実装方針（WORLD 領域への翻訳・[実装決定]）: singer/ 側は声道フォルマント
フィルタの「目標フォルマント (F1..F4) + voicing 混合比」で子音を表現するが、
WORLD の sp は既にフレームごとの周波数包絡そのものである。そのため:
  - 「voicing を下げる」は sp の振幅スケールとして移植する（sp を直接
    ダウンスケールすると WORLD 合成後の実効音量が下がる。ap は変更しない
    ＝有声のまま音量だけ落ちる、という近似）。
  - 「ap→1（破裂/摩擦の noise_gain）」は文字通り ap を 1 に寄せる
    （WORLD は ap=1 のフレームをほぼ完全な非周期励振として合成するため、
    別途ノイズ波形を加算しなくても sp の帯域整形だけで band-noise が
    実現できる）。
  - 「フォルマント目標 (F1,F2,...)」は sp の該当周波数へガウス型の
    bump/notch ゲインとして移植する（対数周波数領域、`_band_noise` の
    帯域マスクと同じ関数形）。

出典（一次ソース・移植する数値は全てここから取る）:
  - singer/render_song.py `_CONSONANT_TIMING_MS` / `_MAX_CONSONANT_FRACTION`
    / `_NOISE_BAND_SPEC` / `_band_noise`（帯域マスクの関数形）
  - singer/render_song_v2.py `R_FLAP_VOICING_V2` / `R_FLAP_F3_RATIO_V2` /
    `H_NOISE_GAIN_V2`
  - singer/render_song_v5.py（S9 実測）`LOCUS_HOLD_MS` /
    `MURMUR_TO_LOCUS_TRANSITION_MS` / `LOCUS_TO_VOWEL_TRANSITION_MS` /
    `LOCUS_F1_HZ` / `PLACE_LOCUS_F2_HZ`
  - singer/render_song_v3.py `NASAL_DIP_VOICING_V3` / `NASAL_NOTCH_*` /
    `NASAL_HF_*`
  - singer/formant_tv.py `NASAL_FORMANTS_HZ`（murmur F1 ≈ 250Hz の出典）

実装対象は sakura / umi の歌詞に現れる onset のみ（score.py / score_umi.py の
mora を列挙して確定・[実装決定・record 記録]）:
  - sakura: s, k, r, y, n, h, m, w, t, g （+ 母音のみ onset=None）
  - umi:    m, h, r, n, k                （+ 母音のみ onset=None）
  - 和集合 = {s, k, t, g, r, n, m, h, y, w}（撥音「ん」は両曲とも未出現・
    出現した場合の扱いは units.mora_to_vowel_target の docstring 参照）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np

import vowel_class as vc  # noqa: F401  (adapter sibling import・グライドの i/u プロトタイプ再利用)

FRAME_PERIOD_MS_DEFAULT = 5.0

BURST_ONSETS: Tuple[str, ...] = ("s", "k", "t", "g")
NASAL_ONSETS: Tuple[str, ...] = ("m", "n")
R_ONSET = "r"
H_ONSET = "h"
GLIDE_ONSETS: Tuple[str, ...] = ("y", "w")

# 出典: singer/render_song.py `_CONSONANT_TIMING_MS`（closure+burst または noise の合計）
# / `_MAX_CONSONANT_FRACTION`。s:noise=70 / k:closure40+burst15=55 / t:38+12=50 / g:25+10=35。
BURST_DUR_MS: Dict[str, float] = {"s": 70.0, "k": 55.0, "t": 50.0, "g": 35.0}
MAX_CONSONANT_FRACTION = 0.45

# 出典: singer/render_song.py `_NOISE_BAND_SPEC`（帯域中心Hz, 対数幅oct）。
# s は fricative_high、k/t/g は burst_k/burst_t/burst_g にそれぞれ対応。
BURST_BAND_HZ: Dict[str, Tuple[float, float]] = {
    "s": (6000.0, 0.6), "k": (2500.0, 0.5), "t": (5000.0, 0.5), "g": (1800.0, 0.5),
}

# 出典: singer/render_song.py `_CONSONANT_TIMING_MS["r"]["flap"]`=28ms。
# singer/render_song_v2.py R_FLAP_VOICING_V2=0.15 / R_FLAP_F3_RATIO_V2=0.60。
R_FLAP_DUR_MS = 28.0
R_FLAP_AMP_SCALE = 0.15
# [実装決定] F3 を 0.60 倍へ下げる元レシピを、WORLD sp 領域では「F3 近傍帯域の
# 減衰（1-0.60=0.40）」として近似する。WORLD の sp は極を明示的に持たないため
# F3 の中心周波数は固定 nominal 値（一般的な女声 F3 帯）を使う（出典なし・
# §Open Questions 記録）。
R_F3_ATTEN = 1.0 - 0.60
R_F3_NOMINAL_HZ = 2700.0
R_F3_WIDTH_OCT = 0.5
R_TRANSITION_MS = 8.0  # murmur->locus と同じ短遷移スタイルを転用（[実装決定]）

# 出典: singer/render_song.py `_CONSONANT_TIMING_MS["h"]["noise"]`=55ms。
H_DUR_MS = 55.0
# [実装決定・出典なし] 追補 F1.1-B は /h/ を「後続母音の包絡で整形した気息
# ノイズ」と指定する（render_song.py の h は fricative_broad 帯域ノイズだが、
# 追補は明示的にこれと異なるレシピを指定しているため、ここでは sp の周波数
# 形状は変更せず（＝後続母音自身の包絡をそのまま使う）、ap のみ引き上げて
# 気息感を出す。H_AP_LIFT / H_AMP_SCALE の値に一次ソースはない。
H_AP_LIFT = 0.85
H_AMP_SCALE = 0.7
H_TRANSITION_MS = 8.0

# 出典: singer/render_song.py `_CONSONANT_TIMING_MS["m"/"n"]["nasal"]`=100ms。
# singer/formant_tv.py `NASAL_FORMANTS_HZ[0]`=250Hz（murmur F1）。
# singer/render_song_v3.py `NASAL_NOTCH_CENTER_HZ`=1150 / `NASAL_NOTCH_WIDTH_OCT`=0.6
# / `NASAL_NOTCH_DEPTH`=0.80 / `NASAL_HF_CUTOFF_HZ`=3000 / `NASAL_HF_ATTEN_DB`=-18
# / `NASAL_DIP_VOICING_V3`=0.15。
NASAL_MURMUR_DUR_MS = 100.0
NASAL_F1_HZ = 250.0
NASAL_F1_BUMP_WIDTH_OCT = 0.25
NASAL_F1_BUMP_GAIN_DB = 6.0  # [実装決定・出典なし] F1 低域強調量
NASAL_NOTCH_CENTER_HZ = 1150.0
NASAL_NOTCH_WIDTH_OCT = 0.6
NASAL_NOTCH_DEPTH = 0.80
NASAL_HF_CUTOFF_HZ = 3000.0
NASAL_HF_ATTEN_DB = -18.0
NASAL_DIP_AMP_SCALE = 0.15

# 出典: singer/render_song_v5.py（S9 実測、docstring §較正 記載）。
LOCUS_HOLD_MS = 16.0
LOCUS_F1_HZ = 300.0
PLACE_LOCUS_F2_HZ: Dict[str, float] = {"n": 1700.0, "m": 1000.0}
LOCUS_BUMP_WIDTH_OCT = 0.25
LOCUS_BUMP_GAIN_DB = 6.0  # [実装決定・出典なし] F1/F2 locus 強調量（NASAL_F1_BUMP_GAIN_DB と揃える）
MURMUR_TO_LOCUS_TRANSITION_MS = 8.0
LOCUS_TO_VOWEL_TRANSITION_MS = 22.0

# 出典: singer/render_song.py `_CONSONANT_TIMING_MS["y"/"w"]["glide"]`=70ms、
# `_GLIDE_TARGETS = {"w": "u", "y": "i"}`。
GLIDE_DUR_MS = 70.0
GLIDE_SOURCE_VOWEL: Dict[str, str] = {"y": "i", "w": "u"}
GLIDE_BUMP_WIDTH_OCT = 0.35
GLIDE_BUMP_GAIN_DB = 6.0  # [実装決定・出典なし]


@dataclass(frozen=True)
class ConsonantEvent:
    note_index: int
    onset: str
    consonant_class: str  # "burst" | "flap" | "nasal" | "breath" | "glide"
    n_frames_processed: int


def _bin_freqs(n_bins: int, sr: int) -> np.ndarray:
    return np.linspace(0.0, sr / 2.0, n_bins)


def _band_mask_gain(freqs: np.ndarray, center_hz: float, width_oct: float) -> np.ndarray:
    """render_song.py `_band_noise` と同じガウス型対数帯域マスク（ピーク=1）。

    白色ノイズを整形する代わりに、既存 sp 包絡へ直接乗算するゲインとして
    転用する（sp を center_hz 近傍へ集中させる = band-noise の帯域設計）。
    """
    freqs_safe = np.maximum(freqs, 1.0)
    log_ratio = np.log2(freqs_safe / center_hz)
    return np.exp(-0.5 * (log_ratio / width_oct) ** 2)


def _notch_gain(freqs: np.ndarray, center_hz: float, width_oct: float, depth: float) -> np.ndarray:
    """render_song_v3.py の nasal notch と同形（1 - depth*ガウシアン）。"""
    freqs_safe = np.maximum(freqs, 1.0)
    log_ratio = np.log2(freqs_safe / center_hz)
    return 1.0 - depth * np.exp(-0.5 * (log_ratio / width_oct) ** 2)


def _hf_shelf_gain(freqs: np.ndarray, cutoff_hz: float, atten_db: float, ramp_oct: float = 0.5) -> np.ndarray:
    """render_song_v3.py の HF シェルフと同形。sp はパワー領域のため dB->gain は /10
    （voice_spec.spectral_tilt と同じ規約。waveform 領域の /20 とは異なる）。
    """
    lin = 10.0 ** (atten_db / 10.0)
    gain = np.ones_like(freqs)
    ramp_lo = cutoff_hz / (2.0 ** ramp_oct)
    above = freqs >= cutoff_hz
    gain[above] = lin
    ramp_mask = (freqs >= ramp_lo) & (freqs < cutoff_hz)
    if np.any(ramp_mask):
        t = (freqs[ramp_mask] - ramp_lo) / (cutoff_hz - ramp_lo)
        gain[ramp_mask] = 1.0 + (lin - 1.0) * (0.5 - 0.5 * np.cos(np.pi * t))
    return gain


def _bump_gain(freqs: np.ndarray, center_hz: float, width_oct: float, gain_db: float) -> np.ndarray:
    freqs_safe = np.maximum(freqs, 1.0)
    log_ratio = np.log2(freqs_safe / center_hz)
    db = gain_db * np.exp(-0.5 * (log_ratio / width_oct) ** 2)
    return 10.0 ** (db / 10.0)


def _blend_window(n_proc: int, fade_frames: int) -> np.ndarray:
    """[0,n_proc) の重み: 先頭は 1（処理適用）、末尾 fade_frames で 1->0 のフェード
    （window の終端で元の（未処理）フレームへ連続的に戻す）。"""
    w = np.ones(n_proc, dtype=np.float64)
    fade_frames = max(0, min(fade_frames, n_proc))
    if fade_frames > 0:
        w[n_proc - fade_frames:] = np.linspace(1.0, 0.0, fade_frames)
    return w


def _ms_to_frames(ms: float, frame_period_ms: float, n_total: int) -> int:
    return max(1, min(n_total, int(round(ms / frame_period_ms))))


def _apply_burst(
    sp: np.ndarray, ap: np.ndarray, onset: str, freqs: np.ndarray, frame_period_ms: float
) -> Tuple[np.ndarray, np.ndarray, ConsonantEvent]:
    n_total = sp.shape[0]
    n_proc = _ms_to_frames(BURST_DUR_MS[onset], frame_period_ms, n_total)
    center_hz, width_oct = BURST_BAND_HZ[onset]
    mask = _band_mask_gain(freqs, center_hz, width_oct)
    fade_frames = _ms_to_frames(8.0, frame_period_ms, n_proc)
    w = _blend_window(n_proc, fade_frames)

    sp_out = sp.copy()
    ap_out = ap.copy()
    band_shaped = sp[:n_proc] * mask[None, :]
    sp_out[:n_proc] = sp[:n_proc] * (1.0 - w[:, None]) + band_shaped * w[:, None]
    ap_target = np.ones_like(ap[:n_proc])
    ap_out[:n_proc] = ap[:n_proc] * (1.0 - w[:, None]) + ap_target * w[:, None]
    ap_out = np.clip(ap_out, 0.0, 1.0)
    return sp_out, ap_out, ConsonantEvent(-1, onset, "burst", n_proc)


def _apply_flap(
    sp: np.ndarray, ap: np.ndarray, freqs: np.ndarray, frame_period_ms: float
) -> Tuple[np.ndarray, np.ndarray, ConsonantEvent]:
    n_total = sp.shape[0]
    n_proc = _ms_to_frames(R_FLAP_DUR_MS, frame_period_ms, n_total)
    fade_frames = _ms_to_frames(R_TRANSITION_MS, frame_period_ms, n_proc)
    w = _blend_window(n_proc, fade_frames)
    f3_notch = _notch_gain(freqs, R_F3_NOMINAL_HZ, R_F3_WIDTH_OCT, R_F3_ATTEN)

    sp_out = sp.copy()
    processed = sp[:n_proc] * f3_notch[None, :] * R_FLAP_AMP_SCALE
    sp_out[:n_proc] = sp[:n_proc] * (1.0 - w[:, None]) + processed * w[:, None]
    ap_out = ap.copy()  # /r/ は有声のため ap は変更しない（[実装決定]）
    return sp_out, ap_out, ConsonantEvent(-1, R_ONSET, "flap", n_proc)


def _apply_breath(
    sp: np.ndarray, ap: np.ndarray, frame_period_ms: float
) -> Tuple[np.ndarray, np.ndarray, ConsonantEvent]:
    n_total = sp.shape[0]
    n_proc = _ms_to_frames(H_DUR_MS, frame_period_ms, n_total)
    fade_frames = _ms_to_frames(H_TRANSITION_MS, frame_period_ms, n_proc)
    w = _blend_window(n_proc, fade_frames)

    sp_out = sp.copy()
    amp_target = sp[:n_proc] * H_AMP_SCALE
    sp_out[:n_proc] = sp[:n_proc] * (1.0 - w[:, None]) + amp_target * w[:, None]

    ap_out = ap.copy()
    ap_target = ap[:n_proc] + (1.0 - ap[:n_proc]) * H_AP_LIFT
    ap_out[:n_proc] = ap[:n_proc] * (1.0 - w[:, None]) + ap_target * w[:, None]
    ap_out = np.clip(ap_out, 0.0, 1.0)
    return sp_out, ap_out, ConsonantEvent(-1, H_ONSET, "breath", n_proc)


def _apply_nasal(
    sp: np.ndarray, ap: np.ndarray, onset: str, freqs: np.ndarray, frame_period_ms: float
) -> Tuple[np.ndarray, np.ndarray, ConsonantEvent]:
    n_total = sp.shape[0]
    max_frames = max(1, int(n_total * MAX_CONSONANT_FRACTION))
    murmur_frames = min(max_frames, _ms_to_frames(NASAL_MURMUR_DUR_MS, frame_period_ms, n_total))
    remaining_for_locus = max(0, min(max_frames - murmur_frames, n_total - murmur_frames - 1))
    locus_frames = min(remaining_for_locus, _ms_to_frames(LOCUS_HOLD_MS, frame_period_ms, n_total))
    locus_frames = max(0, locus_frames)

    murmur_gain = (
        _notch_gain(freqs, NASAL_NOTCH_CENTER_HZ, NASAL_NOTCH_WIDTH_OCT, NASAL_NOTCH_DEPTH)
        * _hf_shelf_gain(freqs, NASAL_HF_CUTOFF_HZ, NASAL_HF_ATTEN_DB)
        * _bump_gain(freqs, NASAL_F1_HZ, NASAL_F1_BUMP_WIDTH_OCT, NASAL_F1_BUMP_GAIN_DB)
    )
    locus_f2 = PLACE_LOCUS_F2_HZ[onset]
    locus_gain = _bump_gain(freqs, LOCUS_F1_HZ, LOCUS_BUMP_WIDTH_OCT, LOCUS_BUMP_GAIN_DB) * _bump_gain(
        freqs, locus_f2, LOCUS_BUMP_WIDTH_OCT, LOCUS_BUMP_GAIN_DB
    )

    sp_out = sp.copy()
    ap_out = ap.copy()

    if murmur_frames > 0:
        fade_frames = _ms_to_frames(MURMUR_TO_LOCUS_TRANSITION_MS, frame_period_ms, murmur_frames)
        w = _blend_window(murmur_frames, fade_frames)
        processed = sp[:murmur_frames] * murmur_gain[None, :] * NASAL_DIP_AMP_SCALE
        sp_out[:murmur_frames] = sp[:murmur_frames] * (1.0 - w[:, None]) + processed * w[:, None]

    if locus_frames > 0:
        lo, hi = murmur_frames, murmur_frames + locus_frames
        fade_frames = _ms_to_frames(LOCUS_TO_VOWEL_TRANSITION_MS, frame_period_ms, locus_frames)
        w = _blend_window(locus_frames, fade_frames)
        processed = sp[lo:hi] * locus_gain[None, :]
        sp_out[lo:hi] = sp[lo:hi] * (1.0 - w[:, None]) + processed * w[:, None]

    n_proc = murmur_frames + locus_frames
    return sp_out, ap_out, ConsonantEvent(-1, onset, "nasal", n_proc)


def _apply_glide(
    sp: np.ndarray, ap: np.ndarray, onset: str, freqs: np.ndarray, frame_period_ms: float
) -> Tuple[np.ndarray, np.ndarray, ConsonantEvent]:
    n_total = sp.shape[0]
    n_proc = _ms_to_frames(GLIDE_DUR_MS, frame_period_ms, n_total)
    src_vowel = GLIDE_SOURCE_VOWEL[onset]
    f1, f2 = vc.VOWEL_PROTOTYPES[src_vowel]
    glide_gain = _bump_gain(freqs, f1, GLIDE_BUMP_WIDTH_OCT, GLIDE_BUMP_GAIN_DB) * _bump_gain(
        freqs, f2, GLIDE_BUMP_WIDTH_OCT, GLIDE_BUMP_GAIN_DB
    )
    # グライドは window 先頭で最大、末尾でゼロ（=目標母音そのものへ収束）。
    w = np.linspace(1.0, 0.0, n_proc) if n_proc > 0 else np.zeros(0)

    sp_out = sp.copy()
    processed = sp[:n_proc] * glide_gain[None, :]
    sp_out[:n_proc] = sp[:n_proc] * (1.0 - w[:, None]) + processed * w[:, None]
    ap_out = ap.copy()
    return sp_out, ap_out, ConsonantEvent(-1, onset, "glide", n_proc)


def apply_consonant_onset(
    sp: np.ndarray, ap: np.ndarray, onset: Optional[str], sr: int,
    frame_period_ms: float = FRAME_PERIOD_MS_DEFAULT,
) -> Tuple[np.ndarray, np.ndarray, Optional[ConsonantEvent]]:
    """1 note 分の resolve 済み (sp, ap) フレーム列にノート頭の子音加工を適用する。

    `onset` が対応する子音種でなければ無改変で返す（母音のみ onset=None・
    未実装子音種も同様）。決定論（RNG 不使用）。
    """
    if onset is None or sp.shape[0] == 0:
        return sp, ap, None
    freqs = _bin_freqs(sp.shape[1], sr)

    if onset in BURST_ONSETS:
        return _apply_burst(sp, ap, onset, freqs, frame_period_ms)
    if onset == R_ONSET:
        return _apply_flap(sp, ap, freqs, frame_period_ms)
    if onset in NASAL_ONSETS:
        return _apply_nasal(sp, ap, onset, freqs, frame_period_ms)
    if onset == H_ONSET:
        return _apply_breath(sp, ap, frame_period_ms)
    if onset in GLIDE_ONSETS:
        return _apply_glide(sp, ap, onset, freqs, frame_period_ms)
    return sp, ap, None
