"""singer/consonant_checks_v3.py — S7: /w/ 弁別検査の一般化。

S6 の `consonant_checks_v2.r_dip_gate`（/r/ 専用）を一般化し、閉鎖性子音
（r/n/m/k/t/g、+可能なら撥音N）が振幅ディップで /w/ 区間と機械弁別できる
ことを検査する。鼻音（n/m/N）はさらに族固有指標（高域減衰比）を追加する。
`gate_checks.py`・`consonant_checks_v2.py` は無改変で import 流用。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

import numpy as np


# --- 凍結閾値（本サイクルの実測較正で決定。S6 の X=5.0dB を継承・拡張） ---
CLOSURE_DIP_MIN_DB = 5.0  # S6 r_dip_gate と同一閾値（/w/ 実測 -9.33dB との弁別に十分な余裕）
NASAL_HF_RATIO_MAX = 0.60
# 根拠: 鼻音閉鎖区間の高域(>=3000Hz)エネルギー比は、対応する母音区間の
# 比より明確に低いことが期待される（鼻腔経由で高域が強く減衰するため）。
# 実測（voice_C, さくら, /n//m/ 2インスタンス, v3）: closure_hf_ratio /
# vowel_hf_ratio = 0.069, 0.179（両方 0.60 を大きく下回る）。旧実装(v1)は
# 0.254, 0.381（境界寄り、明確な弁別ができていなかった）。0.60 を閾値と
# して採用。


def _group_by_note(result) -> Dict[int, List]:
    by_note: Dict[int, List] = defaultdict(list)
    for s in result.subsegments_out:
        by_note[s.note_index].append(s)
    return by_note


def _rms_db(x: np.ndarray) -> float:
    r = float(np.sqrt(np.mean(x ** 2))) + 1e-12
    return 20.0 * np.log10(r)


def _hf_ratio(x: np.ndarray, sr: int, cutoff: float = 3000.0) -> float:
    spec = np.abs(np.fft.rfft(x)) ** 2
    freqs = np.fft.rfftfreq(len(x), d=1.0 / sr)
    total = spec.sum() + 1e-30
    return float(spec[freqs >= cutoff].sum() / total)


def closure_dip_instances(result, onset: str) -> List[float]:
    out = []
    for subs in _group_by_note(result).values():
        subs = sorted(subs, key=lambda s: s.start)
        if subs[0].onset != onset or len(subs) < 2:
            continue
        closure = result.wav[subs[0].start:subs[0].end]
        vowel = result.wav[subs[-1].start:subs[-1].end]
        if len(closure) < 10 or len(vowel) < 10:
            continue
        out.append(_rms_db(vowel) - _rms_db(closure))
    return out


def nasal_hf_ratio_instances(result, onset: str) -> List[float]:
    out = []
    for subs in _group_by_note(result).values():
        subs = sorted(subs, key=lambda s: s.start)
        if subs[0].onset != onset or len(subs) < 2:
            continue
        closure = result.wav[subs[0].start:subs[0].end]
        vowel = result.wav[subs[-1].start:subs[-1].end]
        if len(closure) < 32 or len(vowel) < 32:
            continue
        cr = _hf_ratio(closure, result.sr)
        vr = _hf_ratio(vowel, result.sr)
        out.append(cr / vr if vr > 0 else float("nan"))
    return out


CLOSURE_ONSETS = ("r", "n", "m", "k", "t", "g")
NASAL_ONSETS = ("n", "m")


def w_discrimination_report(result) -> Dict:
    """S3仕様W2: closure系(r/n/m/k/t/g) 各onsetの dip 実測 + /w/ との弁別判定。
    鼻音(n/m)はさらに hf_ratio を報告。撥音Nはsubsegments_outのonsetが
    Noneのため本関数のグルーピングでは検出対象外（[UNDERSPEC-S7]参照）。
    """
    w_dips = closure_dip_instances(result, "w")
    out = {"w_dip_instances": [round(d, 3) for d in w_dips], "onsets": {}}
    for onset in CLOSURE_ONSETS:
        dips = closure_dip_instances(result, onset)
        entry = {
            "dip_instances_db": [round(d, 3) for d in dips],
            "threshold_db": CLOSURE_DIP_MIN_DB,
            "pass": bool(dips) and all(d >= CLOSURE_DIP_MIN_DB for d in dips),
        }
        if onset in NASAL_ONSETS:
            ratios = nasal_hf_ratio_instances(result, onset)
            entry["hf_ratio_instances"] = [round(r, 3) for r in ratios]
            entry["hf_ratio_threshold_max"] = NASAL_HF_RATIO_MAX
            entry["hf_ratio_pass"] = bool(ratios) and all(r <= NASAL_HF_RATIO_MAX for r in ratios)
        out["onsets"][onset] = entry
    return out
