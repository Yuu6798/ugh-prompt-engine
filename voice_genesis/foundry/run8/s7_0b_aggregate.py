"""run8/s7_0b_aggregate.py — 8-0b の 10 群を 1 文書へ束ね、帰結を全数記帳する。

AC（§11）が要求するのは「360 セル全てがレンダされたこと」ではなく
**「360 セル全ての帰結が記帳されたこと」**なので、群ファイルが無い場合も
`outcome = "not_run"` で 1 行残す（黙って縮小しない）。

本モジュールは**裁定しない**。z 化・向き決め・Gate 判定（§7-0）は耳ラベルを
要するので Gate 側の仕事であり、ここでは次だけを機械的に出す:

- 360 セルの `outcome` 全数（rendered / dropped / not_run）
- 群ごとの ringing 状態（§5-0）と、Gate に入れられる群の数
- 群 × 軸の MAD（§7-0 (0) の `degenerate_axis` 判定に必要な素材）
- **H0**（P-ANCHOR かぎり ↔ 同条件 P-RI-FINAL）の軸別一致 / 不一致（§4-2）
- 主観測 4 軸の飽和度（同一群で値が何通りしか無いか）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import s7_io  # noqa: E402
import s7_spec as sp  # noqa: E402

RESULTS_DIR = _HERE.parent / "results_s7"
SPEC_PATH = RESULTS_DIR / "s7_0b_probe_spec.json"
AGG_SCHEMA = "s7-0b-probe-results/0.1"

H0_PAIR = ("P-ANCHOR|sakura|kagiri", "P-RI-FINAL|low|b4")


def _mad(values: Sequence[float]) -> float:
    arr = np.asarray([v for v in values if v is not None], dtype=np.float64)
    if arr.size == 0:
        return 0.0
    return float(np.median(np.abs(arr - np.median(arr))))


def aggregate(spec: Dict[str, Any], group_dir: Path) -> Dict[str, Any]:
    cell_ids = [str(c["cell_id"]) for c in spec["cells"]]
    groups: List[Dict[str, Any]] = []
    all_cells: List[Dict[str, Any]] = []
    epsilon: Optional[Dict[str, float]] = None
    spec_shas = set()

    for gen, info in spec["expansion"]["generations"].items():
        for speaker in info["speakers"]:
            path = group_dir / f"{gen}_{speaker}.json"
            if not path.exists():
                for cid in cell_ids:
                    all_cells.append(
                        {
                            "cell_id": cid, "generation": gen, "speaker": speaker,
                            "outcome": "not_run", "status": "group_not_executed",
                        }
                    )
                groups.append(
                    {"generation": gen, "speaker": speaker, "outcome": "not_run",
                     "in_gate": False, "reason": "group_not_executed"}
                )
                continue
            doc, sha, _ = s7_io.read_json_with_pin(path)
            spec_shas.add(str(doc["probe_spec_sha256"]))
            epsilon = {k: float(v) for k, v in doc["measurement_spec"]["epsilon"].items()}
            by_id = {str(c["cell_id"]): c for c in doc["cells"]}
            missing = [cid for cid in cell_ids if cid not in by_id]
            for cid in missing:
                all_cells.append(
                    {"cell_id": cid, "generation": gen, "speaker": speaker,
                     "outcome": "not_run", "status": "cell_absent_from_group_file"}
                )
            rendered = [c for c in doc["cells"] if c["outcome"] == "rendered"]
            axis_stats = {}
            for axis in sp.PRIMARY_AXES:
                vals = [float(c["primary"][axis]) for c in rendered if c.get("primary")]
                uniq = sorted({round(v, 9) for v in vals})
                axis_stats[axis] = {
                    "n": len(vals),
                    "median": float(np.median(vals)) if vals else None,
                    "mad": _mad(vals),
                    "n_distinct_values": len(uniq),
                    "degenerate_axis": _mad(vals) == 0.0,
                }
            h0 = _h0_for_group(by_id, epsilon)
            groups.append(
                {
                    "generation": gen, "speaker": speaker, "outcome": "run",
                    "group_file_sha256": sha,
                    "n_rendered": int(doc["n_rendered"]), "n_dropped": int(doc["n_dropped"]),
                    "ringing_status": doc["ringing"]["group_status"],
                    "in_gate": bool(doc["ringing"]["in_gate"]),
                    "unvoiced_reference_pool": doc["ringing"]["unvoiced_reference_pool"],
                    "axis_stats": axis_stats,
                    "h0": h0,
                }
            )
            for c in doc["cells"]:
                all_cells.append(
                    {
                        "cell_id": c["cell_id"], "generation": gen, "speaker": speaker,
                        "outcome": c["outcome"], "status": c.get("status"),
                        "wav_sha256": c.get("wav_sha256"),
                        "samples_sha256": c.get("samples_sha256"),
                        "primary": c.get("primary"),
                        "primary_corrected": c.get("primary_corrected"),
                        "tail_voiced_frames": c.get("tail_voiced_frames"),
                        "duration": c.get("duration"),
                        "acoustic": c.get("acoustic"),
                        "waveform": c.get("waveform"),
                        "commanded_note_frames": (c.get("input_meta") or {}).get(
                            "commanded_note_frames"
                        ),
                    }
                )

    if len(spec_shas) > 1:
        raise ValueError(f"群ごとに違う probe spec で回している: {sorted(spec_shas)}")

    outcomes: Dict[str, int] = {}
    for c in all_cells:
        outcomes[c["outcome"]] = outcomes.get(c["outcome"], 0) + 1

    return {
        "schema": AGG_SCHEMA,
        "authority": "DESIGN_S7_run8.md §4 / §5 / §11 AC（帰結の全数記帳）",
        "probe_spec_sha256": (
            spec_shas.pop() if spec_shas
            else hashlib.sha256(SPEC_PATH.read_bytes()).hexdigest()
        ),
        "n_cells_expected": len(cell_ids) * len(groups),
        "n_cells_recorded": len(all_cells),
        "outcomes": outcomes,
        "epsilon": epsilon,
        "groups": groups,
        "gate_eligible_groups": [
            f"{g['generation']}/{g['speaker']}" for g in groups if g.get("in_gate")
        ],
        "h0_summary": _h0_summary(groups),
        "not_decided_here": [
            "z 化 / 向き / Gate 1 の合否（§7-0。耳ラベルが要る）",
            "H0–H5 の裁定（§2-3。本文書は素材のみ）",
        ],
        "cells": all_cells,
    }


def _h0_for_group(
    by_id: Dict[str, Any], epsilon: Optional[Dict[str, float]]
) -> Dict[str, Any]:
    anchor, diagnostic = by_id.get(H0_PAIR[0]), by_id.get(H0_PAIR[1])
    if not anchor or not diagnostic or not epsilon:
        return {"status": "undetermined", "reason": "pair_missing"}
    if anchor["outcome"] != "rendered" or diagnostic["outcome"] != "rendered":
        return {"status": "undetermined", "reason": "pair_not_rendered"}
    axes = {}
    for axis, eps in epsilon.items():
        va = float(anchor["primary"][axis])
        vd = float(diagnostic["primary"][axis])
        axes[axis] = {
            "anchor": va, "diagnostic": vd, "abs_delta": abs(va - vd),
            "epsilon": eps, "agrees_within_epsilon": abs(va - vd) <= eps,
        }
    n_agree = sum(1 for a in axes.values() if a["agrees_within_epsilon"])
    return {
        "status": "agree" if n_agree == len(axes) else "partial_disagreement",
        "n_axes_agreeing": n_agree,
        "n_axes": len(axes),
        "axes": axes,
    }


def _h0_summary(groups: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    per_axis: Dict[str, Dict[str, int]] = {}
    statuses: Dict[str, int] = {}
    for g in groups:
        h0 = g.get("h0") or {}
        statuses[h0.get("status", "undetermined")] = (
            statuses.get(h0.get("status", "undetermined"), 0) + 1
        )
        for axis, a in (h0.get("axes") or {}).items():
            slot = per_axis.setdefault(axis, {"agree": 0, "disagree": 0})
            slot["agree" if a["agrees_within_epsilon"] else "disagree"] += 1
    return {"group_status_counts": statuses, "per_axis": per_axis,
            "pair": {"anchor": H0_PAIR[0], "diagnostic": H0_PAIR[1]}}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--group-dir", required=True)
    ap.add_argument("--out", type=Path, default=RESULTS_DIR / "s7_0b_results.json")
    args = ap.parse_args(list(argv) if argv is not None else None)

    spec, _, _ = s7_io.read_json_with_pin(SPEC_PATH)
    doc = aggregate(spec, Path(args.group_dir))
    s7_io.assert_json_finite(doc)
    s7_io.reject_output_collision([args.out], [SPEC_PATH])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}: {doc['n_cells_recorded']} cells {doc['outcomes']}")
    print(f"  gate-eligible groups: {len(doc['gate_eligible_groups'])}/{len(doc['groups'])}")
    print(f"  H0: {doc['h0_summary']['group_status_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
