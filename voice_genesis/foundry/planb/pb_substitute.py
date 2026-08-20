"""planb/pb_substitute.py — 実資産不在時の代替 Identity / Performance ドナー。

リツ voicebank と PJS コーパスは**本実行環境に存在しない**（ライセンス上
リポジトリへ同梱できず、User 側の資産）。そこで分離機構そのものの検証には、
同一リポジトリ内で完結する手続き的歌唱合成器（`singer/` の R0.9 = 決定論・
学習フリー）で作った 2 声を代替ドナーとして使う。

    Identity 代替  : voice_A（基準テンポ・素の歌唱）
    Performance 代替: voice_B（別テンポ + rubato + 深い vibrato = 別の歌い方）

代替で**検証できること**: 分離機構の実装可能性・テクスチャ非混入・決定論・
成分独立性・TRF 軸の弁別力。
代替で**検証できないこと**: リツの声としての聴感（プラン受入 1）と、
実 DiffSinger の り→ん破綻に対する改善量（同 2）。これらは実資産律速。

さらに `inject_terminal_nasal_drift` は、終端で母音包絡が identity 自身の
鼻音包絡へ流れる**合成故障**を identity 側へ焼き込む。これは実モデルの
破綻原因の主張ではなく、TRF 軸に弁別力があるかを測るための陽性対照である
（陰性対照 = 故障なしの同一ラダー）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np

_HERE = Path(__file__).resolve().parent
_VG = _HERE.parent.parent
for _p in (_VG / "singer", _VG / "harness", _VG / "proto1"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import dataclasses  # noqa: E402

import render_song as rs  # noqa: E402  (singer、read-only import)
import render_song_v5 as rs5  # noqa: E402
import score as sc  # noqa: E402

import pb_world as w  # noqa: E402
from pb_tracks import IdentityBank, Unit  # noqa: E402

_EPS = 1e-12

IDENTITY_TEMPO_BPM = 72.0
PERFORMANCE_TEMPO_BPM = 66.0

# rubato: ノート内 index -> 尺の倍率（決定論の固定表。人間の timing 揺れの代替）
_RUBATO = (1.06, 0.94, 1.10, 1.02, 0.92, 1.12, 0.96, 1.04, 0.98, 1.14,
           1.05, 0.93, 1.08, 0.97, 1.03, 0.95, 1.09, 1.01, 0.99, 1.18)


def _rubato_notes(notes: Sequence[sc.ScoreNote]) -> List[sc.ScoreNote]:
    out = []
    for i, n in enumerate(notes):
        f = _RUBATO[i % len(_RUBATO)]
        out.append(dataclasses.replace(n, duration_beats=n.duration_beats * f))
    return out


def _units_from_segments(segments, sr: int, *, core_frac=(0.35, 0.85)) -> Tuple[Unit, ...]:
    """singer の TimelineSegment 列 -> Unit 列（フレーズ間ブレスを無音 unit 化）。"""
    units: List[Unit] = []
    prev_end = None
    for seg in segments:
        s = seg.start_sample / sr
        e = seg.end_sample / sr
        if prev_end is not None and seg.start_sample > prev_end:
            gs, ge = prev_end / sr, s
            units.append(Unit(
                label="sil", onset=None, vowel="sil", start_s=gs, end_s=ge,
                core_start_s=gs + 0.3 * (ge - gs), core_end_s=gs + 0.7 * (ge - gs),
                is_terminal=False,
            ))
        mora = seg.note.mora
        dur = e - s
        units.append(Unit(
            label=mora.kana, onset=mora.onset, vowel=mora.vowel,
            start_s=s, end_s=e,
            core_start_s=s + core_frac[0] * dur, core_end_s=s + core_frac[1] * dur,
            is_terminal=bool(seg.is_phrase_last),
        ))
        prev_end = seg.end_sample
    return tuple(units)


def render_source(
    *, genome_name: str, tempo_bpm: float, rubato: bool,
) -> Tuple[np.ndarray, int, Tuple[Unit, ...]]:
    """代替ドナーを 1 本レンダする（決定論）。"""
    genome = {"voice_A": rs.voice_a, "voice_B": rs.voice_b, "voice_C": rs.voice_c}[genome_name]()
    notes = sc.build_sakura_score()
    if rubato:
        notes = _rubato_notes(notes)
    result = rs5.render_sakura_v5(genome, notes=notes, tempo_bpm=tempo_bpm)
    units = _units_from_segments(result.segments, result.sr)
    return np.asarray(result.wav, dtype=np.float64), int(result.sr), units


def analyze_source(wav: np.ndarray, sr: int) -> w.WorldAnalysis:
    return w.analyze(wav, sr)


# ---------------------------------------------------------------------------
# 合成故障（陽性対照）: 終端で母音が identity 自身の鼻音包絡へ流れる
# ---------------------------------------------------------------------------
def inject_terminal_nasal_drift(
    bank: IdentityBank, *, window_frac: float = 0.35, depth: float = 0.85,
    hold_energy: bool = True,
) -> IdentityBank:
    """終端 unit の末尾に鼻音方向のドリフト + 利得の据え置きを焼き込む。

    「り→ん」= 母音 /i/ が終端で鼻音 /N/ 側へ流れ、かつ減衰せず鳴り続ける
    （s6 記録の「かぁんん」「連続発声」）という観測の**代替モデル**。
    鼻音テクスチャは identity 自身の実測包絡（`bank.nasal_envelope`）から
    取るので、Performance ドナーのテクスチャは 1 バイトも混ざらない。
    """
    if bank.nasal_envelope is None:
        raise ValueError(
            "identity 側に鼻音包絡の実測源が無い（/N/ も /m//n/ onset も不在）。"
            "合成代替は行わない方針のため故障注入は不可"
        )
    nasal_log = np.log(np.maximum(bank.nasal_envelope, _EPS))
    new_sp: List[np.ndarray] = []
    for i, unit in enumerate(bank.units):
        sp = np.array(bank.sp[i], dtype=np.float64)
        if unit.is_terminal:
            n = sp.shape[0]
            r0 = n - max(2, int(round(n * window_frac)))
            m = n - r0
            ramp = (np.linspace(0.0, 1.0, m) ** 1.5) * float(depth)
            log_sp = np.log(np.maximum(sp[r0:], _EPS))
            blended = log_sp * (1.0 - ramp[:, None]) + nasal_log[None, :] * ramp[:, None]
            sp[r0:] = np.exp(blended)
            if hold_energy:
                ref = float(np.sum(np.exp(log_sp[0])))
                cur = np.sum(sp[r0:], axis=1)
                sp[r0:] = sp[r0:] * (ref / np.maximum(cur, _EPS))[:, None]
        new_sp.append(sp)
    return dataclasses.replace(bank, sp=tuple(new_sp), source_id=bank.source_id + "+nasal_drift")


def probe_unit_indices(units: Sequence[Unit], kana: str, *, terminal_only: bool = True) -> List[int]:
    """プラン §3 の probe（終端 /ri/・/su/ 等）に対応する unit index を返す。"""
    out = []
    for i, u in enumerate(units):
        if u.label != kana:
            continue
        if terminal_only and not u.is_terminal:
            continue
        out.append(i)
    return out


def probe_coverage(units: Sequence[Unit]) -> dict:
    """プラン §3 の probe セットに対する被覆を正直に申告する。"""
    wanted = {
        "terminal_ri": ("り", True),
        "terminal_su": ("す", True),
        "terminal_i": ("い", True),
        "terminal_N": ("ん", True),
        "medial_ri": ("り", False),
    }
    cov = {}
    for name, (kana, term) in wanted.items():
        idx = probe_unit_indices(units, kana, terminal_only=term)
        if not term:
            idx = [i for i in idx if not units[i].is_terminal]
        cov[name] = idx
    return cov


def find_optional(units: Sequence[Unit], kana: str) -> Optional[int]:
    for i, u in enumerate(units):
        if u.label == kana:
            return i
    return None
