"""planb_real/pr_diag.py — identity 経路の診断（instruction §14「Identity 崩壊」時の切り分け）。

G-ear 項目 2 で User が「原音にノイズは無いが R0 で増える」と判定した。
R0 は Performance を一切移植していない baseline なので、劣化の出所は
**Performance 移植ではなく identity 経路そのもの**にある。

その出所を段階的に切り分けるため、同じ区間について 4 本を並べる:

    ANCHOR      原音（WORLD を通していない）
    RT_world    WORLD 解析 → 無改変で再合成（往復のみ。F0 も原音のまま）
    RT_flatf0   往復 + **F0 をユニット中央値へ平坦化**（R0 が F0 に対して行う操作）
    R0          baseline 合成（unit 分割 + warp + 平坦 F0）

RT_world が綺麗なら往復は無罪で、平坦化か unit 処理が犯人。
RT_world が既に汚いなら解析パラメータ（f0 探索域・フレーム周期）を疑う。

併せて、耳が見つけた「雑音性」を測る診断値を出す（既存 6 軸に無い軸）:
非周期性 `ap` の帯域平均と、その終端窓での増分。**登録ゲートには使わない。**
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import soundfile as sf

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE.parent / "planb"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pb_world as pbw  # noqa: E402

import pr_census  # noqa: E402
import pr_identity  # noqa: E402
import pr_lab  # noqa: E402

LADDER_MANIFEST = _HERE / "results" / "ladder_manifest.json"
OUT_DIR = _HERE / "results" / "diag"
REPORT = _HERE / "results" / "identity_path_diagnosis.json"


def _write(path: Path, x: np.ndarray, sr: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    peak = float(np.max(np.abs(x))) or 1.0
    sf.write(path, (np.asarray(x) / peak * 0.9 * 32767.0).astype(np.int16), sr, subtype="PCM_16")


def aperiodicity_stats(wav: np.ndarray, sr: int, *, tail_frac: float = 0.25) -> Dict[str, float]:
    """雑音性の診断値。`ap` は WORLD の非周期性指標 [0,1]（1 に近いほど雑音的）。"""
    a = pbw.analyze(wav, sr)
    voiced = a.f0 > 0.0
    if not np.any(voiced):
        return {"mean_ap": float("nan"), "tail_ap": float("nan"), "tail_minus_body_ap": float("nan"),
                "voiced_frac": 0.0}
    ap = np.mean(a.ap, axis=1)
    n = ap.shape[0]
    t0 = int(n * (1.0 - tail_frac))
    body = ap[:t0][voiced[:t0]]
    tail = ap[t0:][voiced[t0:]]
    mean_ap = float(np.mean(ap[voiced]))
    tail_ap = float(np.mean(tail)) if tail.size else float("nan")
    body_ap = float(np.mean(body)) if body.size else float("nan")
    return {"mean_ap": mean_ap, "tail_ap": tail_ap,
            "tail_minus_body_ap": float(tail_ap - body_ap) if tail.size and body.size else float("nan"),
            "voiced_frac": float(np.mean(voiced))}


def _flatten_f0(f0: np.ndarray, n_units: int = 1) -> np.ndarray:
    """有声区間の F0 を全体中央値へ平坦化する（R0 の操作の単純版）。"""
    out = np.array(f0, dtype=np.float64)
    v = out > 0.0
    if np.any(v):
        out[v] = float(np.median(out[v]))
    return out


def run(pair_key: Optional[str] = None) -> Dict[str, Any]:
    data = json.loads(LADDER_MANIFEST.read_text(encoding="utf-8"))
    report: Dict[str, Any] = {"context_phones": data.get("context_phones"), "pairs": {}}
    for p in data["pairs"]:
        if pair_key and p["pair_key"] != pair_key:
            continue
        pk = p["pair_key"]
        tag = pk.replace("|", "__").replace("#", "-")
        out = OUT_DIR / tag
        lab_path = Path(p["ritsu_file"])
        wav_path = pr_census.wav_for_lab(lab_path)
        if wav_path is None:
            continue
        lab = pr_lab.read_lab(lab_path)
        idx = int(pk.split("|")[1].split("#")[1])
        ii, _fl, _pp = pr_identity.probe_unit_indices(
            lab.phones, idx, context=int(data.get("context_phones", 22)))
        lo = min(lab.phones[i].start_s for i in ii)
        hi = max(lab.phones[i].end_s for i in ii)
        x, sr, _off = pr_identity.load_wav_mono(wav_path, start_s=lo, end_s=hi)

        stats: Dict[str, Any] = {}
        _write(out / "ANCHOR.wav", x, sr)
        stats["ANCHOR"] = aperiodicity_stats(x, sr)

        a = pbw.analyze(x, sr)
        rt = pbw.synthesize(a.f0, a.sp, a.ap, sr, a.frame_period_ms)
        _write(out / "RT_world.wav", rt, sr)
        stats["RT_world"] = aperiodicity_stats(rt, sr)

        rt_flat = pbw.synthesize(_flatten_f0(a.f0), a.sp, a.ap, sr, a.frame_period_ms)
        _write(out / "RT_flatf0.wav", rt_flat, sr)
        stats["RT_flatf0"] = aperiodicity_stats(rt_flat, sr)

        for rung in ("R0", "R4"):
            src = Path(p["rungs"][rung]["wav_path"])
            if not src.exists():
                continue
            y, ysr = sf.read(str(src))
            _write(out / f"{rung}.wav", y, ysr)
            stats[rung] = aperiodicity_stats(np.asarray(y, dtype=np.float64), ysr)

        report["pairs"][pk] = {"dir": str(out), "aperiodicity": stats}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="identity 経路の劣化を段階的に切り分ける")
    ap.add_argument("--pair", default=None)
    args = ap.parse_args(argv)
    rep = run(args.pair)
    for pk, v in rep["pairs"].items():
        print(pk)
        print(f"   {'stage':10s} {'mean_ap':>8s} {'tail_ap':>8s} {'tail-body':>10s} {'voiced':>7s}")
        for stage, s in v["aperiodicity"].items():
            print(f"   {stage:10s} {s['mean_ap']:8.4f} {s['tail_ap']:8.4f} "
                  f"{s['tail_minus_body_ap']:10.4f} {s['voiced_frac']:7.3f}")
    print(f"\nreport -> {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
