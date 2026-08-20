"""planb/pb_sweep.py — TRF 軸の用量反応（dose-response）較正。

合成故障の深さ `depth` を 0 -> 1 で掃引し、TRF 各軸が単調に応答するかを測る。
「軸が壊れの量を読めているか」を、改善の判定より前に確かめるための計器較正で
あり、run 8 側で TRF 軸を事前登録する際の材料になる。

出力: `sweep.json`（depth × probe × 軸）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

import numpy as np

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import pb_compose as pc  # noqa: E402
import pb_ladder as pl  # noqa: E402
import pb_metrics as pm  # noqa: E402
import pb_substitute as ps  # noqa: E402
from pb_tracks import PerformanceToggles  # noqa: E402

DEPTHS = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)


def run(depths=DEPTHS, out: Optional[Path] = None) -> dict:
    cfg = pl.RunConfig(fault=False, write_wav=False)
    clean_bank, perf, _donor, (_wp, _sr, ap, units_p) = pl.build_assets(cfg)
    donor_cores = pm.donor_core_envelopes(ap, units_p)
    probes = sorted(set(
        pl._probe_indices(clean_bank.units, [cfg.primary_probe_kana])
        + pl._probe_indices(clean_bank.units, cfg.holdout_probe_kana)
    ))
    rows: List[dict] = []
    for d in depths:
        bank = clean_bank if d <= 0.0 else ps.inject_terminal_nasal_drift(
            clean_bank, window_frac=cfg.fault_window_frac, depth=float(d))
        r0 = pc.compose(bank, perf, PerformanceToggles())
        r4 = pc.compose(bank, perf, PerformanceToggles(True, True, True, True))
        m0 = pm.measure_rung("R0", r0, bank, perf, probes, donor_cores)
        m4 = pm.measure_rung("R4", r4, bank, perf, probes, donor_cores)
        rows.append({
            "depth": float(d),
            "R0": {t.label + f"@{t.unit_index}": t.as_dict() for t in m0.trf},
            "R4": {t.label + f"@{t.unit_index}": t.as_dict() for t in m4.trf},
        })
    result = {"depths": list(map(float, depths)), "rows": rows,
              "monotonicity": _monotonicity(rows)}
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _monotonicity(rows) -> dict:
    """各 probe × 軸で R0 の値が depth に対して単調増加かを判定する。"""
    out = {}
    if not rows:
        return out
    for probe in rows[0]["R0"]:
        for axis in ("nasal_gain_db", "nasal_gain_shape_db", "drift_db", "drift_shape_db"):
            vals = [r["R0"][probe][axis] for r in rows]
            diffs = np.diff(vals)
            out[f"{probe}|{axis}"] = {
                "values": [round(v, 4) for v in vals],
                "monotone_increasing": bool(np.all(diffs >= -1e-9)),
                "spearman_like": round(float(np.mean(np.sign(diffs))), 4),
                "range_db": round(float(vals[-1] - vals[0]), 4),
            }
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="TRF 軸の用量反応較正")
    ap.add_argument("--out", default=str(_HERE / "results_pb0" / "sweep" / "sweep.json"))
    args = ap.parse_args(argv)
    res = run(out=Path(args.out))
    for k, v in res["monotonicity"].items():
        print(f"{k:34s} mono={str(v['monotone_increasing']):5s} "
              f"range={v['range_db']:+7.3f}dB  {v['values']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
