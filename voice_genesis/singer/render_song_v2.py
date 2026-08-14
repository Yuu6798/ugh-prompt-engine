"""singer/render_song_v2.py — S6: /r/ 弾き音・/h/ 摩擦の強化（v2 派生）。

`render_song.py`（無改変・import 流用）の `build_note_subsegments` を
monkeypatch で一時的に差し替えて呼ぶことで、既存ファイルへの書き込みなしで
音響修正版のレンダを実現する（`render_sakura_v2` 呼び出し中のみ差し替え、
呼び出し後に必ず元へ復元する）。

修正内容（`results_s6/consonant_fix_report.md` に実測根拠）:
  - /r/（弾き音）: 旧実装は voicing=0.7・F3×0.85 のみで「渡り音」に近い
    ソフトな遷移だった。v2 は voicing を大きく下げ（閉鎖に近い振幅ディップ）
    F3 をさらに下げて /w/ との音響コントラストを作る
  - /h/（無声声門摩擦）: 旧実装は noise_gain=0.35 で聴感上ほぼ埋没していた。
    v2 は noise_gain を引き上げ、後続母音のフォルマントで整形する設計
    （既存どおり）は維持しつつ振幅のみ強化する
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
_PROTO1_DIR = _HERE.parent / "proto1"
for _p in (_PROTO1_DIR,):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import phoneme_jp as pj  # noqa: E402
import render_song as rs  # noqa: E402

_ORIGINAL_BUILD_NOTE_SUBSEGMENTS = rs.build_note_subsegments  # patch前の原関数を保持（委譲用・再帰防止）

# --- v2 パラメータ（`consonant_fix_report.md` §実測較正で決定・凍結） ---
R_FLAP_VOICING_V2 = 0.15   # 旧 0.7 → 新 0.15（閉鎖に近い深い振幅ディップ）
R_FLAP_F3_RATIO_V2 = 0.60  # 旧 0.85 → 新 0.60（F3 ディップ強化）
H_NOISE_GAIN_V2 = 0.70     # 旧 0.35 → 新 0.70（可聴レベルへ）


def build_note_subsegments_v2(
    mora: pj.Mora, start: int, end: int,
    formant_scale: float, formant_offsets: Tuple[float, float, float, float], bandwidth_scale: float,
) -> List["rs.SubSegment"]:
    """/r/・/h/ のみ強化版、それ以外は旧実装（無改変）に委譲する。"""
    if mora.is_moraic_nasal or mora.onset is None or mora.is_long_vowel_mark:
        return _ORIGINAL_BUILD_NOTE_SUBSEGMENTS(mora, start, end, formant_scale, formant_offsets, bandwidth_scale)

    onset = mora.onset
    if onset not in ("r", "h"):
        return _ORIGINAL_BUILD_NOTE_SUBSEGMENTS(mora, start, end, formant_scale, formant_offsets, bandwidth_scale)

    note_len = end - start
    vowel_f, vowel_b = rs._formant_target_for(mora.vowel, formant_scale, formant_offsets, bandwidth_scale)
    timing = rs._CONSONANT_TIMING_MS[onset]
    max_consonant_samples = int(note_len * rs._MAX_CONSONANT_FRACTION)

    def clamp_ms_to_samples(ms: float) -> int:
        return min(int(ms / 1000.0 * rs.SR_RENDER), max_consonant_samples)

    if onset == "r":
        flap_dur = clamp_ms_to_samples(timing["flap"])
        c_end = start + flap_dur
        flap_f = (vowel_f[0], vowel_f[1], vowel_f[2] * R_FLAP_F3_RATIO_V2, vowel_f[3])
        subs = [
            rs.SubSegment(start, c_end, flap_f, vowel_b, voicing=R_FLAP_VOICING_V2),
            rs.SubSegment(c_end, end, vowel_f, vowel_b, voicing=1.0),
        ]
        return [s for s in subs if s.end > s.start]

    # onset == "h"
    noise_dur = clamp_ms_to_samples(timing["noise"])
    c_end = start + noise_dur
    subs = [
        rs.SubSegment(start, c_end, vowel_f, vowel_b, voicing=0.0, noise_type="fricative_broad", noise_gain=H_NOISE_GAIN_V2),
        rs.SubSegment(c_end, end, vowel_f, vowel_b, voicing=1.0),
    ]
    return [s for s in subs if s.end > s.start]


def render_sakura_v2(genome, seed_extra: int = 0, notes=None, tempo_bpm: Optional[float] = None) -> "rs.RenderResult":
    """`render_song.render_sakura` を、/r//h/ のみ強化した
    `build_note_subsegments_v2` に一時差し替えて呼ぶ（呼び出し完了後は
    必ず元の関数に復元する。`render_song.py` ソースは無改変のまま）。
    """
    original = rs.build_note_subsegments
    rs.build_note_subsegments = build_note_subsegments_v2  # type: ignore[assignment]
    try:
        result = rs.render_sakura(genome, seed_extra=seed_extra, notes=notes, tempo_bpm=tempo_bpm)
    finally:
        rs.build_note_subsegments = original  # type: ignore[assignment]
    return result
