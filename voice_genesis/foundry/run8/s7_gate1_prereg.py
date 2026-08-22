"""run8/s7_gate1_prereg.py — Gate 1 人間検証の事前登録（耳ラベル取得**前**に pin）。

User 裁定 2026-08-22（(α) 採用）で凍結を指示された項目を機械可読にする:

1. 候補は `thr=0.2 / win=100` のみ。再調整禁止
2. Gate 対象は §5-0 に従う 7 群のみ
3. voicing 3 軸の集約規則と**不一致時の扱い**を先に凍結
4. `terminal_mel_persistence` は参考値として記録するが合否に使わない
5. 耳判定はブラインド
6. 耳判定後に閾値・窓・対象群を変更しない

あわせて **pre-flight（充足可能性の機械検算）** を同梱する。§7-0 (4b) と §7-0b は
耳ラベルが付く前に決まる部分（分割ごとのセル数・陽性対照の所在）を持つので、
**聴取の労力を払う前に**評価可能性を確定させる。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import s7_io  # noqa: E402

RESULTS_DIR = _HERE.parent / "results_s7"
SPEC_1_2 = Path("/home/user/s7work/out/b1_cal_1_2/trf_measurement_spec_1_2_candidate.json")
SPEC_1_2_REPO = RESULTS_DIR / "trf_measurement_spec_1_2.json"
REMEASURE_1_2 = RESULTS_DIR / "s7_0b_remeasure_1_2.json"
LISTENING_SET = RESULTS_DIR / "s7_listening_set.json"
SCHEMA = "s7-gate1-human-verification-prereg/0.1"

VOICING_AXES = ("excess_tail_voiced_ms", "release_after_score_boundary_ms", "tail_f0_persistence")
POSITIVE_CONTROL = "run7/ritsu/P-ANCHOR/sakura-kagiri"


def build(spec_path: Path) -> Dict[str, Any]:
    spec, spec_sha, _ = s7_io.read_json_with_pin(spec_path)
    rem, rem_sha, _ = s7_io.read_json_with_pin(REMEASURE_1_2)
    ls, ls_sha, _ = s7_io.read_json_with_pin(LISTENING_SET)

    stat = {f"{g['generation']}/{g['speaker']}": g for g in rem["groups"]}
    eligible = {k for k, g in stat.items() if g["ringing_reference_obtainable"]}

    # --- pre-flight: 軸ごとに「使える群」と各分割のセル数を数える ---
    preflight = {}
    for axis in VOICING_AXES:
        usable = {k for k in eligible if not stat[k]["axis_stats"][axis]["degenerate_1_2"]}
        cal = [c for c in ls["cells"] if c["generation"] in ("run5", "run6") and c["group"] in usable]
        ho = [c for c in ls["cells"] if c["generation"] == "run7" and c["group"] in usable]
        pairs = [c for c in ls["cells"]
                 if c["speaker"] == "user" and c["generation"] == "run5" and c["group"] in usable]
        pc = any(c["cell_id"] == POSITIVE_CONTROL and c["group"] in usable for c in ls["cells"])
        blockers = []
        if len(cal) < 12:
            blockers.append(f"calibration_scored={len(cal)} < 12（§7-0 (4b)）")
        if len(ho) < 12:
            blockers.append(f"holdout_scored={len(ho)} < 12（§7-0 (4b)）")
        if len(pairs) < 5:
            blockers.append(f"gate3_user_pairs={len(pairs)} < 5（§7-0 (7)）")
        if not pc:
            blockers.append("positive_control_absent（§7-0b 条件 1 が評価不能）")
        preflight[axis] = {
            "usable_groups": sorted(usable), "n_usable_groups": len(usable),
            "calibration_scored": len(cal), "holdout_scored": len(ho),
            "gate3_user_pairs": len(pairs), "positive_control_available": pc,
            "blockers": blockers, "evaluable": not blockers,
        }

    any_evaluable = any(v["evaluable"] for v in preflight.values())
    return {
        "schema": SCHEMA,
        "frozen_at": "2026-08-22",
        "authority": "User 裁定 2026-08-22（(α) 採用。1.2 は BLOCKED のまま凍結）",
        "order_discipline": "本文書を pin してから耳ラベルを取る。取得後に閾値・窓・対象群を変更しない",

        "instrument": {
            "spec": {"path": spec_path.name, "sha256": spec_sha,
                     "spec_version": spec["spec_version"], "freeze_status": spec["freeze_status"],
                     "note": "**BLOCKED のまま凍結する。PASS へ書き換えない**"},
            "voicing_axes_used": {
                axis: {"selected_candidate": spec["axes"][axis]["selected_candidate"],
                       "epsilon": spec["axes"][axis]["epsilon"]}
                for axis in VOICING_AXES},
            "retuning": "禁止。閾値 thr=0.2 / 窓 win=100 以外へ動かさない",
            "terminal_mel_persistence": {
                "status": "UNAVAILABLE（この観測設計では構成妥当性を失っている）",
                "reason": "終端/核のパワー比 = 相対エネルギー量であり、床 0.319 > 本番中央値 0.207 の逆転を相続する",
                "use": "参考値として記録する。**合否には使わない**"},
        },

        "gate_scope": {
            "rule": "§5-0 の群規則に従い ringing 補正が成立する群のみ",
            "eligible_groups": sorted(eligible),
            "excluded_groups": sorted(set(stat) - eligible),
            "excluded_reason": "無声基準セルが 2 件未満で leave-one-out が成立しない",
            "degenerate_note": "§7-0 (0): MAD = 0 の軸はその (話者 × 世代) で degenerate_axis とし当該群を外す",
        },

        "aggregation_rule": {
            "why_frozen_now": "User 裁定「voicing 3 軸の集約規則と不一致時の扱いを先に凍結」",
            "step_1": "3 軸すべてについて Gate 1–4 を独立に計算し、全て記録する",
            "step_2": "primary = §7-0 (3) の規則（校正分割での margin argmax・辞書順タイブレーク）",
            "step_3": "合否判定は primary 軸で行う（設計どおり）",
            "disagreement": {
                "all_agree": "3 軸の Gate 判定が一致 → その判定を採る（status = consistent）",
                "primary_pass_others_fail": (
                    "primary が pass でも他 2 軸のいずれかが同じ Gate で fail →"
                    " status = axis_disagreement。**Gate 1 を PASS にしない**（UNDETERMINED）"),
                "primary_fail": "primary が fail → その帰結に従う（PASS にしない）",
                "why": "裁定「耳ラベルと voicing 3 軸が整合 → 最終判定 / 整合しない → UNDETERMINED」の直訳",
            },
        },

        "listening_protocol": {
            "set": {"path": LISTENING_SET.name, "sha256": ls_sha,
                    "n_unique": ls["n_unique"], "n_duplicate": ls["n_duplicate"]},
            "blind": "モデル名・話者・世代を伏せる。伏せないのは expected_terminal と position",
            "order": "ランダム順。重複であることを被験者に知らせない",
            "post_label_changes": "禁止（閾値・窓・対象群・集合のいずれも変更しない）",
        },

        "preflight": {
            "purpose": "耳ラベルが付く前に決まる充足条件を、聴取の労力を払う前に検算する",
            "requirements_checked": ["§7-0 (4b) 校正 scored >= 12", "§7-0 (4b) hold-out scored >= 12",
                                     "§7-0 (7) user run5↔run7 対応 >= 5", "§7-0b 条件 1 陽性対照の所在"],
            "per_axis": preflight,
            "any_axis_evaluable": any_evaluable,
            "verdict_if_not_evaluable": (
                "裁定の停止条件「判定材料不足 → Gate 1 = UNDETERMINED → Run 8 終了」に該当する。"
                "**耳ラベルを取っても Gate 1 は最終判定できない**"),
            "prohibited_workaround": (
                "§7-0 (4b):「不足が起きても耳ラベルの対象セルを事後に足して埋めない」。"
                "足すなら memo 改訂として層化設計をやり直す（別裁定）"),
        },

        "stop_condition": {
            "authority": "User 裁定 2026-08-22",
            "consistent": "耳ラベルと voicing 3 軸が整合 → Gate 1 を最終判定して Run 8 終了",
            "inconsistent_or_insufficient": "整合しない / 判定材料不足 → Gate 1 = UNDETERMINED → Run 8 終了",
            "no_1_3": "1.3 は作らない。HNR / vowel drift 等は Run 8 の継続版ではなく別の観測研究として扱う",
        },
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--spec", type=Path, default=SPEC_1_2 if SPEC_1_2.exists() else SPEC_1_2_REPO)
    ap.add_argument("--out", type=Path, default=RESULTS_DIR / "s7_gate1_human_verification_prereg.json")
    args = ap.parse_args(list(argv) if argv is not None else None)
    doc = build(args.spec)
    s7_io.assert_json_finite(doc)
    s7_io.reject_output_collision([args.out], [LISTENING_SET, REMEASURE_1_2])
    args.out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    for axis, v in doc["preflight"]["per_axis"].items():
        print(f"  {axis:34s} evaluable={v['evaluable']}  cal={v['calibration_scored']} "
              f"ho={v['holdout_scored']} pairs={v['gate3_user_pairs']} pc={v['positive_control_available']}")
        for b in v["blockers"]:
            print(f"      blocker: {b}")
    print(f"  any_axis_evaluable = {doc['preflight']['any_axis_evaluable']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
