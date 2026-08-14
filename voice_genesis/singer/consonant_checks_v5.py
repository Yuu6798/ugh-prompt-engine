"""singer/consonant_checks_v5.py — S9: /n//m/ F2 locus 検査。

S8(v4) の開放速度検査（`consonant_checks_v4.py`、無改変・import流用）に、
開放直後の F2 出発点が調音位置別のlocus帯内にあることを検査する
機構を追加する。計測方式は v4 と同じ「commandedフォルマントタイムライン
直接計測」（score-informed QC原理、`consonant_checks_v4.py`docstring
[UNDERSPEC-S8-1]参照）を踏襲する。

memo item2: 「マーマー→locus の切替」は v4 の開放速度検査（維持）の対象、
「locus→母音の20-25ms遷移」は本検査（locus帯判定）の対象であり
**開放速度検査の対象外**であることをここに明記する（開放後の"最初の
安定点"がlocus帯にあることだけを見る。その後locusから母音へ遷移する
速度自体は別途 v5 が 20-25ms に設計・固定しているため個別の速度検査は
設けない — 設計値として `LOCUS_TO_VOWEL_TRANSITION_MS` に凍結済み）。
"""
from __future__ import annotations

from typing import Dict, Tuple

import numpy as np

import consonant_checks_v4 as cc4  # noqa: E402  (S8、無改変・import流用)
import render_song as rs  # noqa: E402
import formant_tv as ftv  # noqa: E402

# --- 凍結閾値（実測較正） ---
LOCUS_BAND_HZ = {"n": (1500.0, 1900.0), "m": (800.0, 1200.0)}
# 根拠: memo指定のlocus帯をそのまま採用（/n/歯茎音のF2locus≈1700Hz中心の
# 帯、/m/両唇音のF2locus≈1000Hz中心の帯）。


def _f2_at_release_plus(formants_t: np.ndarray, sr_r: int, murmur_end_out: int, out_to_render: int, offset_ms: float = 6.0) -> float:
    """開放境界(murmur終端)から offset_ms 後のF2値。locus保持区間
    （設計上10ms）の中で、遷移の影響を避けた早い時点を見る。
    """
    boundary_r = murmur_end_out * out_to_render
    off = int(sr_r * offset_ms / 1000.0)
    idx = min(len(formants_t) - 1, boundary_r + off)
    return float(formants_t[idx, 1])


def _capture_timeline_v5(render_fn, *args, **kwargs):
    """`consonant_checks_v4._capture_timeline` と同じ spy 方式だが、
    `render_song_v5.interpolate_formant_timeline_v5` も対象に含める
    （v4版はv5モジュールの存在を知らないため、v5専用にここで拡張する。
    `consonant_checks_v4.py` は無改変のまま）。
    """
    captured = {}

    def _make_spy(orig_fn):
        def spy(segments, n_samples, sr, transition_ms=60.0):
            result = orig_fn(segments, n_samples, sr, transition_ms=transition_ms)
            captured["formants_t"] = result[0]
            captured["sr"] = sr
            return result
        return spy

    orig_ftv = ftv.interpolate_formant_timeline
    ftv.interpolate_formant_timeline = _make_spy(orig_ftv)

    import render_song_v4 as rs4
    import render_song_v5 as rs5
    orig_v4 = rs4.interpolate_formant_timeline_v4
    rs4.interpolate_formant_timeline_v4 = _make_spy(orig_v4)
    orig_v5 = rs5.interpolate_formant_timeline_v5
    rs5.interpolate_formant_timeline_v5 = _make_spy(orig_v5)

    try:
        result = render_fn(*args, **kwargs)
    finally:
        ftv.interpolate_formant_timeline = orig_ftv
        rs4.interpolate_formant_timeline_v4 = orig_v4
        rs5.interpolate_formant_timeline_v5 = orig_v5
    return result, captured["formants_t"], captured["sr"]


def place_locus_report(render_fn, genome, **kw) -> Tuple[Dict, "rs.RenderResult"]:
    result, formants_t, sr_r = _capture_timeline_v5(render_fn, genome, **kw)
    out_to_render = sr_r // result.sr
    out = {}
    for onset in ("n", "m"):
        vals = []
        for start_out, murmur_end_out, end_out in cc4._nasal_boundaries(result, onset):
            vals.append(_f2_at_release_plus(formants_t, sr_r, murmur_end_out, out_to_render))
        lo, hi = LOCUS_BAND_HZ[onset]
        out[onset] = {
            "f2_at_release_plus6ms_hz": [round(v, 1) for v in vals],
            "locus_band_hz": (lo, hi),
            "pass": bool(vals) and all(lo <= v <= hi for v in vals),
        }
    return out, result
