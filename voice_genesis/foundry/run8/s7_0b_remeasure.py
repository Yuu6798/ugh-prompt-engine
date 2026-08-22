"""run8/s7_0b_remeasure.py — 既存 360 セルを 1.1 検出器で**再測定する診断**。

User 裁定 2026-08-21:「新しい voicing detector によって §5-0 の『機械検査で無声』が
実レンダ上で再び取得可能になるかを**先に検証する**。取得可能になれば §5-0 の意味論は
変更不要。それでも取得不能なら、その時点で初めて ringing reference semantics の
amendment を別裁定する」。

**これは Gate 評価ではない。** 裁定は「現 360 セルは 1.1 設計の failure discovery に
使用されたので untouched confirmation set とは呼ばない」と定めている。したがって本
モジュールは:

- `verdict` を出さない（合否語彙を持たない）
- 出すのは「1.1 検出器で測ると、無声基準セルが群あたり何セル取れるか」「主観測 4 軸の
  群内 MAD が縮退から抜けるか」だけである

レンダはやり直さない（既存 wav を測り直すだけ。命令区間は群 JSON の `input_meta` から
そのまま使う = 波形から推定しない）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import s7_b1_calibration as b1  # noqa: E402
import s7_b1_v11 as v11  # noqa: E402
import s7_io  # noqa: E402
import s7_spec as sp  # noqa: E402

RESULTS_DIR = _HERE.parent / "results_s7"
SPEC_1_1_PATH = RESULTS_DIR / "trf_measurement_spec_1_1.json"
DIAG_SCHEMA = "s7-0b-remeasure-diagnostic/0.1"


def _mad(vals: Sequence[float]) -> float:
    arr = np.asarray(list(vals), dtype=np.float64)
    if arr.size == 0:
        return 0.0
    return float(np.median(np.abs(arr - np.median(arr))))


def load_winners(spec_path: Path = SPEC_1_1_PATH):
    spec, sha, _ = s7_io.read_json_with_pin(spec_path)
    if spec.get("freeze_status") != "frozen":
        raise RuntimeError(f"{spec_path.name} は frozen でない")
    prereg = v11.load_prereg_11()
    by_id = {c.candidate_id: c for c in v11.enumerate_candidates_11(prereg)}
    winners = {}
    for axis in sp.PRIMARY_AXES:
        cid = str(spec["axes"][axis]["selected_candidate"])
        winners[axis] = by_id[cid]
    return spec, sha, winners


def remeasure_group(path: Path, winners) -> Dict[str, Any]:
    doc, sha, _ = s7_io.read_json_with_pin(path)
    out_dir = Path(str(doc["out_dir"]))
    cells: List[Dict[str, Any]] = []
    for c in doc["cells"]:
        if c["outcome"] != "rendered":
            cells.append({"cell_id": c["cell_id"], "outcome": c["outcome"]})
            continue
        meta = c["input_meta"]
        # 測る前に**記録済み pin を照合する**（PR #303 レビュー指摘）。out_dir は
        # 機械ローカルなので、再生成・編集・別バッチ差し替えが起こりうる。照合
        # しないと「元の 360 セル由来」と名乗ったまま別の音を測った成果物が出る。
        y, sr = s7_io.read_wav_with_pins(
            s7_io.child_path(out_dir, c["wav"]), c["wav_sha256"], c["samples_sha256"]
        )
        stim = b1.Stimulus(
            stim_id=str(c["cell_id"]), family="production_cell",
            samples=np.asarray(y, dtype=np.float64), sr=int(sr),
            note_onset_s=float(meta["note_onset_s"]),
            commanded_note_end_s=float(meta["commanded_note_end_s"]),
            score_boundary_s=float(meta["score_boundary_s"]),
            tail_window_ms=float(meta["tail_window_ms"]),
        )
        primary = {}
        for axis, cand in winners.items():
            primary[axis] = float(v11.measure_candidate_11(cand, stim)[axis])
        vcand = winners["excess_tail_voiced_ms"]
        voiced, _f0, times = v11.voiced_mask_and_f0(vcand, stim)
        w = stim.tail_window_ms / 1000.0
        mask = (times > stim.score_boundary_s) & (times <= stim.score_boundary_s + w) & voiced
        cells.append(
            {
                "cell_id": c["cell_id"], "outcome": "rendered",
                "probe": c.get("probe"), "pitch_key": c.get("pitch_key"),
                "duration_key": c.get("duration_key"), "position": c.get("position"),
                "primary_1_1": primary,
                "tail_voiced_frames_1_1": int(np.count_nonzero(mask)),
                "primary_1_0": c.get("primary"),
                "tail_voiced_frames_1_0": c.get("tail_voiced_frames"),
            }
        )
    rendered = [c for c in cells if c["outcome"] == "rendered"]
    axis_stats = {}
    for axis in sp.PRIMARY_AXES:
        vals = [c["primary_1_1"][axis] for c in rendered]
        axis_stats[axis] = {
            "mad_1_1": _mad(vals),
            "n_distinct_1_1": len({round(v, 9) for v in vals}),
            "degenerate_1_1": _mad(vals) == 0.0,
            "median_1_1": float(np.median(vals)) if vals else None,
        }
    zero_cells = [c["cell_id"] for c in rendered if c["tail_voiced_frames_1_1"] == 0]
    return {
        "generation": doc["generation"], "speaker": doc["speaker"],
        "group_file_sha256": sha,
        "n_rendered": len(rendered),
        "unvoiced_reference_cells_1_1": zero_cells,
        "n_unvoiced_reference_1_1": len(zero_cells),
        "n_unvoiced_reference_1_0": sum(
            1 for c in rendered if c.get("tail_voiced_frames_1_0") == 0
        ),
        "ringing_reference_obtainable": len(zero_cells) >= 2,
        "ringing_note": (
            "群内で leave-one-out が全セルに対して成立するには**2 セル以上**要る"
            "（1 セルだとそのセル自身が基準を失う = §5-0 の非対称）"
        ),
        "axis_stats": axis_stats,
        "cells": cells,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--group-dir", required=True)
    ap.add_argument("--out", type=Path, default=RESULTS_DIR / "s7_0b_remeasure_1_1.json")
    args = ap.parse_args(list(argv) if argv is not None else None)

    spec, spec_sha, winners = load_winners()
    groups = []
    for path in sorted(Path(args.group_dir).glob("*.json")):
        if path.name.startswith("s7_") or path.name.startswith("trf_"):
            continue
        groups.append(remeasure_group(path, winners))
        g = groups[-1]
        print(
            f"| {g['generation']}/{g['speaker']}: unvoiced-ref 1.0={g['n_unvoiced_reference_1_0']} "
            f"-> 1.1={g['n_unvoiced_reference_1_1']} obtainable={g['ringing_reference_obtainable']}"
        )
    doc = {
        "schema": DIAG_SCHEMA,
        "authority": "User 裁定 2026-08-21（RINGING: 新検出器で無声基準が再取得可能か先に検証）",
        "not_a_gate_evaluation": (
            "本文書は Gate 評価ではない。現 360 セルは 1.1 設計の failure discovery に"
            "使われたため untouched confirmation set ではない（裁定 CONFIRMATION）。"
            "Gate 1 の再評価は新規事前登録の confirmation set で行う"
        ),
        "measurement_spec_1_1": {"sha256": spec_sha, "spec_version": spec["spec_version"]},
        "winners": {a: c.candidate_id for a, c in winners.items()},
        "groups": groups,
        "summary": {
            "n_groups": len(groups),
            "groups_with_obtainable_ringing_reference": sum(
                1 for g in groups if g["ringing_reference_obtainable"]
            ),
            "degenerate_axis_groups_1_1": {
                axis: sum(1 for g in groups if g["axis_stats"][axis]["degenerate_1_1"])
                for axis in sp.PRIMARY_AXES
            },
        },
    }
    s7_io.assert_json_finite(doc)
    args.out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    print(f"  ringing 再取得可能な群: {doc['summary']['groups_with_obtainable_ringing_reference']}/{len(groups)}")
    print(f"  1.1 で縮退した群数: {doc['summary']['degenerate_axis_groups_1_1']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
