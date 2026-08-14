"""singer/consonant_checks_v2.py — S6: /r/ 振幅ディップ・/h/ 摩擦エネルギーの機械チェック。

`gate_checks.py`（無改変・import 流用: `_high_energy_ratio` 等）を再利用し、
新規に /r/・/h/ 専用のチェックを追加する。閾値は本サイクルの実測較正で
決定・凍結した値（`results_s6/consonant_fix_report.md` §較正 参照）。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Tuple

import numpy as np

import gate_checks as gc  # noqa: E402  (無改変・import 流用のみ)

# --- 凍結閾値（本サイクルの実測較正で決定） ---
R_DIP_MIN_DB = 5.0
# 根拠: 旧実装(v1) /r/ 振幅ディップ実測 1.4〜3.1dB（さくら4インスタンス、
# 1件は-1.16dBで実質ディップなし）、/w/ 実測 -9.33dB（ディップ無し=渡り音
# の音響的性質）。新実装(v2) /r/ 実測 6.9〜12.7dB。5.0dB を閾値とすると
# v1(旧)・/w/ は確実に不合格、v2(新)は全インスタンス確実に合格する
# （実測データとの整合を確認済み）。

H_LEVEL_GAP_MIN_DB = -6.0
# 根拠: 「うみ」の2インスタンスで測定した h区間RMS(dB) - 後続母音RMS(dB)
# の平均値。旧実装(v1)平均 -9.20dB（母音よりかなり小さく聴感上埋没）、
# 新実装(v2)平均 -3.70dB。-6.0dB を閾値とすると v1(旧)平均は不合格、
# v2(新)平均は合格する。gate3同型の high_energy_ratio（相対スペクトル比）
# だけでは v1 でも既に比較的高い値（~10倍）を示し「弱すぎて聞こえない」
# という耳所見を判別できなかったため（両実装とも gate3 の
# CONSONANT_ENERGY_RATIO_MIN=1.3 を大きく超える）、絶対レベル差という
# 補助指標を追加した（詳細は underspec_log_s6.md）。


def _group_by_note(result) -> Dict[int, List]:
    by_note: Dict[int, List] = defaultdict(list)
    for s in result.subsegments_out:
        by_note[s.note_index].append(s)
    return by_note


def _rms_db(x: np.ndarray) -> float:
    r = float(np.sqrt(np.mean(x ** 2))) + 1e-12
    return 20.0 * np.log10(r)


def r_dip_instances(result) -> List[float]:
    """/r/ 各インスタンスの振幅ディップ（後続母音RMS(dB) - 閉鎖RMS(dB)）。"""
    out = []
    for subs in _group_by_note(result).values():
        subs = sorted(subs, key=lambda s: s.start)
        if subs[0].onset != "r" or len(subs) < 2:
            continue
        closure = result.wav[subs[0].start:subs[0].end]
        vowel = result.wav[subs[-1].start:subs[-1].end]
        if len(closure) < 10 or len(vowel) < 10:
            continue
        out.append(_rms_db(vowel) - _rms_db(closure))
    return out


def w_dip_instances(result) -> List[float]:
    out = []
    for subs in _group_by_note(result).values():
        subs = sorted(subs, key=lambda s: s.start)
        if subs[0].onset != "w" or len(subs) < 2:
            continue
        closure = result.wav[subs[0].start:subs[0].end]
        vowel = result.wav[subs[-1].start:subs[-1].end]
        if len(closure) < 10 or len(vowel) < 10:
            continue
        out.append(_rms_db(vowel) - _rms_db(closure))
    return out


def r_dip_gate(result) -> Dict:
    dips = r_dip_instances(result)
    ok = bool(dips) and all(d >= R_DIP_MIN_DB for d in dips)
    return {"instances_db": [round(d, 3) for d in dips], "threshold_db": R_DIP_MIN_DB, "pass": ok}


def h_energy_instances(result) -> List[Tuple[float, float, float]]:
    """/h/ 各インスタンスの (energy_ratio_of_ratios[gate3同型], level_gap_db)。"""
    out = []
    for subs in _group_by_note(result).values():
        subs = sorted(subs, key=lambda s: s.start)
        if subs[0].onset != "h" or len(subs) < 2:
            continue
        cons = result.wav[subs[0].start:subs[0].end]
        vowel_full = result.wav[subs[-1].start:subs[-1].end]
        vowel_match = result.wav[subs[-1].start:min(subs[-1].end, subs[-1].start + (subs[0].end - subs[0].start))]
        cr = gc._high_energy_ratio(cons, result.sr)
        vr = gc._high_energy_ratio(vowel_match, result.sr)
        ratio = cr / vr if (vr and np.isfinite(vr) and vr > 0) else float("nan")
        gap = _rms_db(cons) - _rms_db(vowel_full)
        out.append((ratio, gap, None))
    return [(r, g) for r, g, _ in out]


def h_gate(result) -> Dict:
    vals = h_energy_instances(result)
    if not vals:
        return {"instances": [], "mean_level_gap_db": float("nan"), "pass": False}
    ratios = [v[0] for v in vals]
    gaps = [v[1] for v in vals]
    mean_gap = float(np.mean(gaps))
    ok = bool(mean_gap >= H_LEVEL_GAP_MIN_DB)
    return {
        "instances_ratio": [round(r, 3) for r in ratios],
        "instances_level_gap_db": [round(g, 3) for g in gaps],
        "mean_level_gap_db": round(mean_gap, 3),
        "threshold_level_gap_db": H_LEVEL_GAP_MIN_DB,
        "gate3_style_ratio_threshold": gc.CONSONANT_ENERGY_RATIO_MIN,
        "pass": ok,
    }
