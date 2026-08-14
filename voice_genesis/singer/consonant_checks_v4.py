"""singer/consonant_checks_v4.py — S8: 開放速度 + マーマー閉鎖度の機械検査。

S6/S7 の振幅ディップ検査（`consonant_checks_v2/v3`、無改変・import流用）
に、S8診断で判明した「振幅ディップだけでは/w/知覚と乖離する」問題への
対処として、鼻音境界のフォルマント遷移速度（開放速度）とマーマー区間の
母音フォルマント帯エネルギー（閉鎖度）を追加する。

**計測方式（[UNDERSPEC-S8-1] 参照）**: 生成波形のスペクトル重心を追跡する
初期実装は、`apply_time_varying_formant_filter` 自体のSTFTブロック
（20ms窓/10ms hop）による平滑化と干渉し、旧実装(v3)と新実装(v4)を
安定に弁別できなかった（実測: v3のn releaseで0.899という「速い」判定
になってしまい、diagnosis節で直接確認した約50msの遅い遷移という
事実と矛盾した）。そのため本モジュールは、レンダラが実際に使う
**フォルマント目標タイムライン**（`formant_tv.interpolate_formant_timeline`
の出力、決定論的な制御信号そのもの）を一時的な spy で捕捉し、これを
直接測定する方式に変更した。これは「レンダラが何を命令されたか」を
検査者が知ってよいという S4 gate6-v2 の score-informed QC 原理
（本サイクルにも援用）と整合する。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

_HERE = Path(__file__).resolve().parent
import render_song as rs  # noqa: E402
import formant_tv as ftv  # noqa: E402

# --- 凍結閾値（実測較正、下記コメント参照） ---
RELEASE_FRACTION_AT_10MS_MIN = 0.65
# 根拠: 境界(murmur終端)から+10ms時点でF1が最終値(+60ms時点)にどれだけ
# 到達しているか（0=未変化,1=到達済み）。急峻な開放(目標10ms)なら+10ms
# でほぼ完了(高い値)、緩やかな遷移(旧実装60ms)なら道半ば(低い値)のはず。
# 実測較正: 旧実装(v3) n=0.334, m=0.334（60ms遷移のうち+10msはまだ道半ば
# 前）。新実装(v4) n=1.000, m=1.000（+10ms時点で完全に完了、10ms遷移幅と
# 整合）。0.65 は両者を明確に弁別する閾値として採用。

MURMUR_VOWEL_F1_TOLERANCE_HZ = 30.0
# 根拠: マーマー区間の中央60%（境界の遷移窓を避けた区間）でF1が鼻音
# F1(250Hz)から30Hz以上ズレていれば「母音フォルマントが残存」と判定する。
# 実測較正: 旧実装(v3)・新実装(v4)ともマーマー中央60%ではF1=250.0Hzで
# 安定していた（S7で既に振幅ディップ修正時にマーマー区間自体の
# フォルマントターゲットは維持されていたため）。本チェックは主に
# 遷移窓がマーマー中央部まで浸食するケース（60ms超の遷移や短いnasal_dur）
# を将来検出するための安全網として維持する。


def _capture_timeline(render_fn, *args, **kwargs) -> Tuple["rs.RenderResult", np.ndarray, int]:
    """render_fn(*args,**kwargs) 実行中に実際使われるフォルマントタイムライン
    生成関数を spy して捕捉する。`render_song_v4.render_sakura_v4` は
    `ftv.interpolate_formant_timeline` を自前実装
    （`render_song_v4.interpolate_formant_timeline_v4`）へ一時差し替える
    ため、`ftv` 側だけでなく `render_song_v4` 側の関数も同時に spy する
    （render_fn が v3/plain のときは前者、v4 のときは後者が実際に呼ばれる）。
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

    orig_v4 = None
    try:
        import render_song_v4 as rs4
        orig_v4 = rs4.interpolate_formant_timeline_v4
        rs4.interpolate_formant_timeline_v4 = _make_spy(orig_v4)
    except ImportError:
        rs4 = None

    try:
        result = render_fn(*args, **kwargs)
    finally:
        ftv.interpolate_formant_timeline = orig_ftv
        if rs4 is not None and orig_v4 is not None:
            rs4.interpolate_formant_timeline_v4 = orig_v4
    return result, captured["formants_t"], captured["sr"]


def _nasal_boundaries(result, onset: str) -> List[Tuple[int, int, int]]:
    """(note.start_sample_out, murmur_end_sample_out, note.end_sample_out) のリスト。"""
    out = []
    for seg in result.segments:
        if seg.note.mora.onset == onset:
            note_len = seg.end_sample - seg.start_sample
            max_cons = int(note_len * rs._MAX_CONSONANT_FRACTION)
            nasal_dur = min(int(rs._CONSONANT_TIMING_MS[onset]["nasal"] / 1000.0 * rs.SR_OUT), max_cons)
            murmur_end = seg.start_sample + nasal_dur
            out.append((seg.start_sample, murmur_end, seg.end_sample))
    return out


def release_fraction_at_10ms(formants_t: np.ndarray, sr_r: int, murmur_end_out: int, out_to_render: int) -> float:
    boundary_r = murmur_end_out * out_to_render
    off10 = int(sr_r * 0.010)
    off60 = int(sr_r * 0.060)
    f1_pre = float(formants_t[max(0, boundary_r - 1), 0])
    f1_10 = float(formants_t[min(len(formants_t) - 1, boundary_r + off10), 0])
    f1_60 = float(formants_t[min(len(formants_t) - 1, boundary_r + off60), 0])
    denom = f1_60 - f1_pre
    if abs(denom) < 1e-6:
        return float("nan")
    return float((f1_10 - f1_pre) / denom)


def murmur_f1_deviation(formants_t: np.ndarray, sr_r: int, start_out: int, murmur_end_out: int, out_to_render: int) -> float:
    s_r = start_out * out_to_render
    e_r = murmur_end_out * out_to_render
    n = e_r - s_r
    if n <= 0:
        return float("nan")
    core = formants_t[s_r + int(n * 0.2):s_r + int(n * 0.8), 0]
    if len(core) == 0:
        return float("nan")
    return float(np.max(np.abs(core - ftv.NASAL_FORMANTS_HZ[0])))


NASAL_ONSETS = ("n", "m")


def nasal_release_report(render_fn, genome, **kw) -> Dict:
    result, formants_t, sr_r = _capture_timeline(render_fn, genome, **kw)
    out_to_render = sr_r // result.sr
    out = {}
    for onset in NASAL_ONSETS:
        fracs, devs = [], []
        for start_out, murmur_end_out, end_out in _nasal_boundaries(result, onset):
            fracs.append(release_fraction_at_10ms(formants_t, sr_r, murmur_end_out, out_to_render))
            devs.append(murmur_f1_deviation(formants_t, sr_r, start_out, murmur_end_out, out_to_render))
        fracs = [f for f in fracs if np.isfinite(f)]
        devs = [d for d in devs if np.isfinite(d)]
        out[onset] = {
            "release_frac_at_10ms": [round(f, 3) for f in fracs],
            "release_frac_threshold_min": RELEASE_FRACTION_AT_10MS_MIN,
            "release_pass": bool(fracs) and all(f >= RELEASE_FRACTION_AT_10MS_MIN for f in fracs),
            "murmur_f1_max_deviation_hz": [round(d, 2) for d in devs],
            "murmur_f1_tolerance_hz": MURMUR_VOWEL_F1_TOLERANCE_HZ,
            "murmur_closure_pass": bool(devs) and all(d <= MURMUR_VOWEL_F1_TOLERANCE_HZ for d in devs),
        }
    return out, result
