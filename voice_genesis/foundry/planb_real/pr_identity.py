"""planb_real/pr_identity.py — Ritsu 実歌唱からの Identity 供給（instruction §5）。

Identity 側が出すのは spectral envelope / aperiodicity / 音韻テクスチャ /
実収録の遷移だけ。PJS テクスチャは一切入れない。

planb（surrogate）で検証済みの `IdentityBank` をそのまま使う。実資産で
追加が要るのは「wav と音素境界の供給」だけ、という surrogate 走行の
引き渡し条件をここで実際に消費する。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np
import soundfile as sf

_PLANB = Path(__file__).resolve().parent.parent / "planb"
if str(_PLANB) not in sys.path:
    sys.path.insert(0, str(_PLANB))

import pb_extract as px  # noqa: E402
import pb_world as pbw  # noqa: E402
from pb_tracks import IdentityBank, Unit  # noqa: E402

import pr_lab  # noqa: E402

#: 母音コアの既定範囲（lab に音素境界があるので unit 内の相対で十分）
CORE_FRAC = (0.35, 0.85)

#: probe の前に含める音素数の既定。
#: 2 音素だと出力が 1 秒程度にしかならず**耳判定が成立しない**（実測: User 判定
#: 「サンプルが 1 秒しかないから両方とも判定できない」）。フレーズとして
#: 聞き取れる長さを既定にする。計測は probe unit のみを見るので、文脈を伸ばしても
#: 測る対象は変わらない。
DEFAULT_CONTEXT_PHONES = 22


#: 実 DB の 1 ファイルは最長 266 秒（44.1kHz）。全長を WORLD 解析すると 1 probe
#: あたり数十秒かかるため、probe の周辺だけを切り出して解析する。
SLICE_PAD_S = 0.6


def load_wav_mono(path: Path, *, start_s: float = 0.0,
                  end_s: Optional[float] = None) -> Tuple[np.ndarray, int, float]:
    """必要区間だけを読む。戻り値 = (波形, sr, 実際の切り出し開始時刻)。"""
    info = sf.info(str(path))
    sr = int(info.samplerate)
    total_s = float(info.frames) / sr
    a = max(0.0, float(start_s))
    b = total_s if end_s is None else min(total_s, float(end_s))
    if b <= a:
        raise ValueError(f"空の切り出し範囲: [{a}, {b}] in {path.name}")
    data, _sr = sf.read(str(path), start=int(a * sr), stop=int(b * sr), always_2d=False)
    x = np.asarray(data, dtype=np.float64)
    if x.ndim > 1:
        x = np.mean(x, axis=1)
    return x, sr, a


def units_from_phones(
    phones: Sequence[pr_lab.Phone], indices: Sequence[int], *,
    terminal_flags: Optional[Sequence[bool]] = None,
) -> Tuple[Unit, ...]:
    """lab の音素区間をそのまま Unit へ写す（子音は 1 unit、母音は 1 unit）。"""
    flags = list(terminal_flags) if terminal_flags is not None else [False] * len(indices)
    out: List[Unit] = []
    for k, i in enumerate(indices):
        p = phones[i]
        vowel = pr_lab.normalize_vowel(p.label)
        dur = p.duration_s
        out.append(Unit(
            label=p.label,
            onset=None if vowel is not None else p.label,
            vowel=vowel if vowel is not None else ("sil" if p.is_silence else "C"),
            start_s=p.start_s, end_s=p.end_s,
            core_start_s=p.start_s + CORE_FRAC[0] * dur,
            core_end_s=p.start_s + CORE_FRAC[1] * dur,
            is_terminal=bool(flags[k]),
        ))
    return tuple(out)


def probe_unit_indices(phones: Sequence[pr_lab.Phone], vowel_index: int,
                       *, context: int = DEFAULT_CONTEXT_PHONES) -> Tuple[List[int], List[bool], int]:
    """probe 母音とその直前文脈（子音・先行モーラ）+ 後続無音の index 列を返す。

    戻り値 = (index 列, terminal フラグ列, index 列内での probe 母音の位置)。

    **terminal フラグは lab 上の実際のフレーズ末判定で決める。**
    probe だからといって一律に立てると、`medial_ri` のような「語中を触らない
    対照」に terminal release skill が適用されてしまう（実測: 語中 probe の
    R4 で identity 距離が 3.0 -> 8.9 dB へ崩れ、終端エネルギーが +19.7dB
    増えた = 対照が対照でなくなっていた）。
    """
    lo = max(0, vowel_index - context)
    while lo < vowel_index and phones[lo].is_boundary:
        lo += 1
    idx = list(range(lo, vowel_index + 1))
    if vowel_index + 1 < len(phones) and phones[vowel_index + 1].is_boundary:
        idx.append(vowel_index + 1)
    finals = set(pr_lab.phrase_final_indices(list(phones)))
    flags = [i == vowel_index and i in finals for i in idx]
    return idx, flags, idx.index(vowel_index)


def measure_nasal_envelope(wav_path: Path, phones: Sequence[pr_lab.Phone],
                           *, near_s: float = 0.0) -> Tuple[Optional[np.ndarray], str]:
    """identity 自身の鼻音包絡を実測する（/N/ 優先、無ければ /m//n/）。

    probe の近くにある候補を選び、その区間だけを解析する（全長解析を避ける）。
    候補が無ければ None を返す — 合成代替で埋めない。
    """
    cands = [p for p in phones if p.label == "N" and p.duration_s >= 0.05]
    src = "measured:N"
    if not cands:
        cands = [p for p in phones if p.label in ("m", "n") and p.duration_s >= 0.03]
        src = "measured:m/n"
    if not cands:
        return None, "absent"
    best = min(cands, key=lambda p: abs(0.5 * (p.start_s + p.end_s) - near_s))
    wav, sr, off = load_wav_mono(Path(wav_path), start_s=best.start_s - 0.05,
                                 end_s=best.end_s + 0.05)
    a = pbw.analyze(wav, sr)
    fp = a.frame_period_ms / 1000.0
    lo = max(0, int((best.start_s - off) / fp))
    hi = min(a.sp.shape[0], max(lo + 2, int((best.end_s - off) / fp)))
    if hi - lo < 2:
        return None, "absent"
    return np.mean(a.sp[lo:hi], axis=0), f"{src}@{best.start_s:.2f}s"


def _shift_units(units: Sequence[Unit], offset_s: float) -> Tuple[Unit, ...]:
    import dataclasses
    return tuple(dataclasses.replace(
        u, start_s=u.start_s - offset_s, end_s=u.end_s - offset_s,
        core_start_s=u.core_start_s - offset_s, core_end_s=u.core_end_s - offset_s)
        for u in units)


def build_identity_for_probe(
    wav_path: Path, lab: pr_lab.LabFile, vowel_index: int, *, source_id: str,
    context: int = DEFAULT_CONTEXT_PHONES, pad_s: float = SLICE_PAD_S,
) -> Tuple[IdentityBank, int]:
    """Ritsu の 1 発話から probe 周辺の IdentityBank を作る（当該区間のみ解析）。

    戻り値 = (bank, bank.units 内での probe 母音の位置)。
    """
    import dataclasses
    idx, flags, probe_pos = probe_unit_indices(lab.phones, vowel_index, context=context)
    units = units_from_phones(lab.phones, idx, terminal_flags=flags)
    span_lo = min(u.start_s for u in units)
    span_hi = max(u.end_s for u in units)
    wav, sr, offset = load_wav_mono(Path(wav_path), start_s=span_lo - pad_s,
                                    end_s=span_hi + pad_s)
    analysis = pbw.analyze(wav, sr)
    bank = px.build_identity_bank(analysis, _shift_units(units, offset), source_id=source_id)
    nasal, nasal_src = measure_nasal_envelope(
        Path(wav_path), lab.phones, near_s=0.5 * (span_lo + span_hi))
    if nasal is not None:
        bank = dataclasses.replace(bank, nasal_envelope=nasal,
                                   nasal_envelope_source=nasal_src)
    return bank, probe_pos


def analyze_for_performance(
    wav_path: Path, *, start_s: float = 0.0, end_s: Optional[float] = None,
) -> Tuple[pbw.WorldAnalysis, np.ndarray, float]:
    """Performance 抽出に要る f0 とフレームパワー [dB]（+ 切り出し開始時刻）を返す。"""
    wav, sr, offset = load_wav_mono(Path(wav_path), start_s=start_s, end_s=end_s)
    a = pbw.analyze(wav, sr)
    power_db = 10.0 * np.log10(np.maximum(np.sum(a.sp, axis=1), 1e-12))
    return a, power_db, offset


def shift_phones(phones: Sequence[pr_lab.Phone], offset_s: float) -> List[pr_lab.Phone]:
    return [pr_lab.Phone(p.start_s - offset_s, p.end_s - offset_s, p.label) for p in phones]
