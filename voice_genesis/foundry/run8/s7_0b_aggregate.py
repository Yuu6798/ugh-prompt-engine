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


class GroupFileError(ValueError):
    """群 JSON が事前登録と結び付いていない（fail-closed）。"""


def validate_group_document(
    doc: Dict[str, Any],
    path: Path,
    generation: str,
    speaker: str,
    cell_ids: Sequence[str],
    spec_sha256: str,
) -> None:
    """**セルを消費する前に**群 JSON の不変条件を検査する（PR #303 レビュー第 2 巡 P1）。

    この関数が無いと、群 JSON は**ファイル名だけ**で (世代, 話者) が決まる。改名・
    取り違え・古い probe spec での実行が起きると、集計側は中身を見ずに
    「ファイル名から導いた群」としてセルを**貼り替えて**しまい、Gate 会計が別の
    モデル / 話者の観測を根拠にできてしまう。

    検査する不変条件:

    1. schema（0.1 = 由来記録なしの履歴形式 / 0.2 = `export_binding` 必須）
    2. 埋め込まれた `generation` / `speaker` == ファイル名から導いた群
    3. `probe_spec_sha256` == **いま読んでいる** probe spec の sha256
       （従来は「群どうしで一致するか」しか見ておらず、10 群すべてが同じ古い spec で
       回っていれば素通りした）
    4. `cell_id` の重複が無い（`by_id` は後勝ちで黙って畳むため）
    5. 事前登録に**無いセルが混ざっていない**（混ざると MAD / 縮退判定へ黙って参加する）。
       事前登録にあるセルの**欠落は許す** — 部分実行は `cell_absent_from_group_file`
       として記帳する正当な状態だから
    """
    schema = str(doc.get("schema"))
    if schema not in sp.PROBE_GROUP_SCHEMAS:
        raise GroupFileError(
            f"{path.name}: schema {doc.get('schema')!r} は "
            f"{list(sp.PROBE_GROUP_SCHEMAS)!r} のいずれでもない"
        )
    if schema == sp.PROBE_GROUP_SCHEMA and not doc.get("export_binding"):
        # 0.2 を名乗るなら ONNX の由来を機械照合した記録がある（0.1 は持たない）
        raise GroupFileError(f"{path.name}: {schema} なのに export_binding が無い")
    if str(doc.get("generation")) != generation or str(doc.get("speaker")) != speaker:
        raise GroupFileError(
            f"{path.name}: 中身の群 "
            f"({doc.get('generation')!r}, {doc.get('speaker')!r}) が "
            f"ファイル名の群 ({generation!r}, {speaker!r}) と違う"
        )
    if str(doc.get("probe_spec_sha256")) != spec_sha256:
        raise GroupFileError(
            f"{path.name}: 実行時 probe spec {doc.get('probe_spec_sha256')} が "
            f"現在の {spec_sha256} と違う"
        )
    got = [str(c["cell_id"]) for c in doc["cells"]]
    dupes = sorted({cid for cid in got if got.count(cid) > 1})
    if dupes:
        raise GroupFileError(f"{path.name}: cell_id が重複している: {dupes}")
    extra = sorted(set(got) - set(cell_ids))
    if extra:
        raise GroupFileError(f"{path.name}: 事前登録に無いセルが混ざっている: {extra}")


def aggregate(spec: Dict[str, Any], group_dir: Path, spec_sha256: str) -> Dict[str, Any]:
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
            validate_group_document(doc, path, gen, speaker, cell_ids, spec_sha256)
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
        # 群が 1 つも走っていなくても、集計が名乗る spec は**いま読んだバイト列**の
        # sha である（生の read_bytes で読み直すと parse と hash が別バイト由来になる）。
        "probe_spec_sha256": spec_sha256,
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

    spec, spec_sha, _ = s7_io.read_json_with_pin(SPEC_PATH)
    doc = aggregate(spec, Path(args.group_dir), spec_sha)
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
