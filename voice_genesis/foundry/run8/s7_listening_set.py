"""run8/s7_listening_set.py — §6-1 のブラインド耳校正セット（40 unique + 8 duplicate）。

`DESIGN_S7_run8.md` §6-1 の割付を**そのまま**機械可読化する。割付を発明しない。
`s7_listening_set.json` を書き、sha256 を pin してから聴取を始める
（生成 → pin → 聴取 の順序を守れば、機械結果を見てから対象を選ぶ経路が閉じる）。

各セルには 1.2 の Gate 対象群判定（§5-0）を**注記として**付ける。除外群のセルを
黙って落とすことはしない（落とすかどうかは裁定事項であり、本モジュールは事実を
並べるだけ）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import s7_io  # noqa: E402

RESULTS_DIR = _HERE.parent / "results_s7"
REMEASURE_1_2 = RESULTS_DIR / "s7_0b_remeasure_1_2.json"
SCHEMA = "s7-listening-set/0.1"

PITCH_ALIAS = {"p57": "low", "p62": "mid", "p65": "high"}


def _cell(gen: str, spk: str, probe: str, beats: str, pitch: str) -> Dict[str, Any]:
    return {
        "cell_id": f"{gen}/{spk}/{probe}/{beats}/{pitch}",
        "generation": gen, "speaker": spk, "probe": probe,
        "beats": beats, "pitch": pitch,
        "probe_cell_id": f"{probe}|{PITCH_ALIAS[pitch]}|{beats}",
    }


def _anchor(gen: str, spk: str, region: str) -> Dict[str, Any]:
    return {
        "cell_id": f"{gen}/{spk}/P-ANCHOR/{region}",
        "generation": gen, "speaker": spk, "probe": "P-ANCHOR", "region": region,
        "probe_cell_id": {"sakura-kagiri": "P-ANCHOR|sakura|kagiri"}[region],
    }


def build_blocks() -> Dict[str, List[Dict[str, Any]]]:
    """§6-1 の A / B / C / D をそのまま展開する。"""
    a1 = [_cell(g, "user", p, b, pt)
          for g in ("run5", "run7")
          for (p, b, pt) in (("P-RI-FINAL", "b4", "p57"), ("P-RI-FINAL", "b2", "p57"),
                             ("P-RI-FINAL", "b4", "p62"), ("P-I-FINAL", "b4", "p57"),
                             ("P-N-FINAL", "b4", "p57"))]
    a2 = [_cell(g, "ritsu", p, b, "p57")
          for g in ("run5", "run6")
          for (p, b) in (("P-RI-FINAL", "b4"), ("P-RI-FINAL", "b2"), ("P-N-FINAL", "b4"))]
    a3 = [_cell(g, "pjs", p, "b4", "p57")
          for g in ("run5", "run6")
          for p in ("P-RI-FINAL", "P-I-FINAL", "P-RI-MEDIAL")]
    a4 = [_cell("run6", "user", p, "b4", "p57") for p in ("P-RI-FINAL", "P-N-FINAL")]
    b = [_cell("run7", spk, p, "b4", "p62")
         for spk in ("ritsu", "pjs", "amitaro")
         for p in ("P-RI-FINAL", "P-RI-MEDIAL", "P-N-FINAL", "P-SU-FINAL")]
    c = [_cell("run7", "ritsu", "P-RI-FINAL", "b1", "p57"),
         _cell("run7", "ritsu", "P-RI-FINAL", "b1", "p65")]
    d = [_anchor("run7", "ritsu", "sakura-kagiri"), _anchor("run7", "pjs", "sakura-kagiri")]
    return {"A": a1 + a2 + a3 + a4, "B": b, "C": c, "D": d}


def build_set() -> Dict[str, Any]:
    blocks = build_blocks()
    unique: List[Dict[str, Any]] = []
    for name, cells in blocks.items():
        for c in cells:
            c["block"] = name
            unique.append(c)
    ids = [c["cell_id"] for c in unique]
    if len(set(ids)) != len(ids):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"重複がある: {dup}")
    if len(unique) != 40:
        raise ValueError(f"unique {len(unique)} != 40")

    # 重複提示 8 件: A/B/C/D の各ブロックから cell_id 昇順で先頭 2 件ずつ
    duplicates = []
    for name in ("A", "B", "C", "D"):
        for c in sorted((x for x in unique if x["block"] == name), key=lambda x: x["cell_id"])[:2]:
            duplicates.append({"cell_id": c["cell_id"], "block": name,
                               "note": "重複提示（被験者には重複と知らせない）"})
    if len(duplicates) != 8:
        raise ValueError(f"duplicates {len(duplicates)} != 8")

    # 1.2 の Gate 対象群（§5-0）を注記として付ける
    rem, rem_sha, _ = s7_io.read_json_with_pin(REMEASURE_1_2)
    eligible = {f"{g['generation']}/{g['speaker']}"
                for g in rem["groups"] if g["ringing_reference_obtainable"]}
    for c in unique:
        key = f"{c['generation']}/{c['speaker']}"
        c["group"] = key
        c["gate_eligible_group_1_2"] = key in eligible

    n_excl = sum(1 for c in unique if not c["gate_eligible_group_1_2"])
    return {
        "schema": SCHEMA,
        "frozen_at": "2026-08-22",
        "authority": "DESIGN_S7_run8.md §6-1（割付はそのまま。発明していない）",
        "protocol": {
            "blind": "モデル名・話者・世代を伏せる。伏せないのは expected_terminal と position",
            "order": "ランダム順で提示する",
            "axes": {"nasalization": "0=なし … 3=明確に「ん」",
                     "continuous_voicing": "0=正常解放 … 3=語尾が止まらない",
                     "intelligibility": "0=明瞭 … 3=語として不成立"},
            "duplicates": "20% を重複提示し自己一致率を記録する。被験者に重複と知らせない",
            "order_discipline": "本文書を pin してから聴取を始める。聴取後に集合を変えない",
        },
        "n_unique": len(unique), "n_duplicate": len(duplicates),
        "blocks": {k: len(v) for k, v in blocks.items()},
        "feasibility_check_from_design": {
            "calibration_scored_ge_12": sum(1 for c in unique if c["generation"] in ("run5", "run6")),
            "holdout_scored_ge_12": sum(1 for c in unique if c["generation"] == "run7"),
            "gate3_user_pairs": sum(1 for c in unique
                                    if c["speaker"] == "user" and c["generation"] == "run5"),
            "positive_control_present": any(c["cell_id"] == "run7/ritsu/P-ANCHOR/sakura-kagiri"
                                            for c in unique),
        },
        "gate_eligibility_1_2": {
            "source": {"path": REMEASURE_1_2.name, "sha256": rem_sha},
            "eligible_groups": sorted(eligible),
            "n_cells_in_excluded_groups": n_excl,
            "note": ("§5-0 の群規則で Gate から外れる群のセルにも耳ラベルは付ける。"
                     "落とすかどうかは裁定事項であり、本文書は事実を並べるだけ"),
        },
        "cells": unique,
        "duplicates": duplicates,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=RESULTS_DIR / "s7_listening_set.json")
    args = ap.parse_args(list(argv) if argv is not None else None)
    doc = build_set()
    s7_io.assert_json_finite(doc)
    s7_io.reject_output_collision([args.out], [REMEASURE_1_2])
    args.out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}: unique {doc['n_unique']} + dup {doc['n_duplicate']}")
    print(f"  blocks: {doc['blocks']}")
    print(f"  充足検算: {json.dumps(doc['feasibility_check_from_design'], ensure_ascii=False)}")
    print(f"  Gate 対象群(1.2): {doc['gate_eligibility_1_2']['eligible_groups']}")
    print(f"  除外群に属するセル: {doc['gate_eligibility_1_2']['n_cells_in_excluded_groups']}/40")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
