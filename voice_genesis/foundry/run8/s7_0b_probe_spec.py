"""run8/s7_0b_probe_spec.py — Run 8-0b probe set の**事前登録**生成器。

`DESIGN_S7_run8.md` §4（36 セル）/ §4-3（360 セルへの展開）/ §5（観測ベクトル）を
機械可読な 1 文書へ落とし、**1 セルもレンダする前に** commit / pin する
（§12-0-C2 と同じ順序規律。B-1 の事前登録 3 点と同じ扱い）。

本文書が決めること:

- 36 セルの因子水準（probe / 音高 / 尺 / 位置 / 期待終端 / SP の有無）
- 診断 33 セルの譜面は `singer/score_diag_pf.py` が決定論的に作る（sha を pin）
- P-ANCHOR 3 セルは実曲フル譜面レンダからの**切り出し**（§4-2）
- 世代 × 話者 = 360 セルの割付と、各世代の checkpoint sha256
- 測定は**凍結済み** `trf_measurement_spec.json`（1.0 / frozen）の選定候補で行う
- ringing 補正（§5-0）と status 語彙

本文書が決めないこと（**ここで決めてはいけない**）:

- 耳ラベル（§6）と Gate 1 の閾値（§7-0b）。どちらも本 probe の実測より後に来る
- 軸の向き `orient`（§7-0 (2)）。校正データが要るので Gate 側の仕事
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_HERE = Path(__file__).resolve().parent
_SINGER = _HERE.parents[1] / "singer"
for _p in (str(_HERE), str(_SINGER)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import s7_io  # noqa: E402
import s7_spec as sp  # noqa: E402

RESULTS_DIR = _HERE.parent / "results_s7"
SPEC_PATH = RESULTS_DIR / "s7_0b_probe_spec.json"
TRF_SPEC_PATH = RESULTS_DIR / "trf_measurement_spec.json"
SCORE_DIAG_PATH = _SINGER / "score_diag_pf.py"
SCORE_PATH = _SINGER / "score.py"
SCORE_UMI_PATH = _SINGER / "score_umi.py"
PHONEME_JP_PATH = _SINGER / "phoneme_jp.py"

PROBE_SPEC_SCHEMA = "s7-0b-probe-spec/0.1"

#: 世代 -> (checkpoint sha256, 話者, 記録の出所)。§4-3。
GENERATIONS: Dict[str, Dict[str, Any]] = {
    "run5": {
        "checkpoint_sha256": "d3c51399cb1c3914981d4a11da8391a4e344130c84b263f0ef9774f60c3f8da5",
        "checkpoint_bytes": 556013282,
        "speakers": ["ritsu", "pjs", "user"],
        "sha_source": "results_s4/s4_record_2026-08-19.md（phase B 40K = run 5 最終）",
    },
    "run6": {
        "checkpoint_sha256": "6a28d744642df6535000857767c32efee2e69668b390c2e7fa6486908723306a",
        "checkpoint_bytes": None,
        "speakers": ["ritsu", "pjs", "user"],
        "sha_source": "results_s5/s5_record_2026-08-20.md（phase_b/checkpoints/40000 = 判定の正本）",
    },
    "run7": {
        "checkpoint_sha256": "518df090a8154e61f28b529f731418f4f97d47c3b56d1326d354e6be4629fa93",
        "checkpoint_bytes": 556022498,
        "speakers": ["ritsu", "pjs", "user", "amitaro"],
        "sha_source": "results_s6/s6_record_2026-08-20.md",
    },
}

#: P-ANCHOR 3 セル（§4-2）。実曲フル譜面をレンダして該当区間を切り出す。
ANCHORS: List[Dict[str, Any]] = [
    {
        "cell_id": "P-ANCHOR|sakura|kagiri",
        "probe": "P-ANCHOR",
        "song": "sakura",
        "phrase_index": 5,
        "phrase_kana": "かぎり",
        "target_mora_index_in_phrase": 2,
        "target_kana": "り",
        "position": "final",
        "expected_terminal": "ri",
        "has_following_sp": True,
        "pitch_midi": 57.0,
        "pitch_key": "low",
        "duration_beats": 4.0,
        "duration_key": "b4",
        "role": "positive_control",
        "why": (
            "User の耳判定で破綻が確定している唯一の実体（§4-2）。同条件の "
            "P-RI-FINAL|low|b4 との差が H0 そのもの。"
        ),
    },
    {
        "cell_id": "P-ANCHOR|sakura|miwatasu",
        "probe": "P-ANCHOR",
        "song": "sakura",
        "phrase_index": 4,
        "phrase_kana": "みわたす",
        "target_mora_index_in_phrase": 3,
        "target_kana": "す",
        "position": "medial",
        "expected_terminal": "su",
        "has_following_sp": False,
        "pitch_midi": 62.0,
        "pitch_key": "mid",
        "duration_beats": 2.0,
        "duration_key": "b2",
        "role": "real_song_context",
        "why": (
            "フル譜面の中では発話末ではないので**後続 SP を持たない**（§4-0）。"
            "position=medial として記帳し、終端解放の期待を課さない。"
        ),
    },
    {
        "cell_id": "P-ANCHOR|umi|final",
        "probe": "P-ANCHOR",
        "song": "umi",
        "phrase_index": 2,
        "phrase_kana": "おおきいな",
        "target_mora_index_in_phrase": 4,
        "target_kana": "な",
        "position": "final",
        "expected_terminal": "a",
        "has_following_sp": True,
        "pitch_midi": 62.0,
        "pitch_key": "mid",
        "duration_beats": 3.0,
        "duration_key": "b3",
        "role": "real_song_context",
        "underspecified_in_design": True,
        "resolution": (
            "§4-1 は 3 番目のアンカーを「うみ」とだけ書き、標的モーラを特定していない。"
            "P-ANCHOR の役割は**実曲文脈の終端事象**であり、フル譜面で後続 SP を持つのは"
            "発話末だけ（§4-0）なので、うみ譜面の発話末「な」を標的に採る。"
        ),
        "alternative_reading": (
            "語「うみ」の /mi/（うみは の 2 モーラ目）。後続に「は」が続くので "
            "position=medial になる。User が後者を意図していたなら本セルのみ差し替えで足りる。"
        ),
    },
]

#: 測定不能・補正不能を黙って落とさないための status 語彙（§5-0 / §4-4）。
STATUS_VOCABULARY = {
    "ok": "全軸を無声対照との差分で報告できた",
    "ringing_uncorrected": "無声対照の母集団が空で、生値のまま報告した（§5-0-3）",
    "ringing_uncorrected_group": (
        "同一話者 × 同一世代の群に補正不能セルがあるため、群ごと Gate から除外した（§5-0）"
    ),
    "dictionary_uncovered": "音素辞書に未収載のモーラがあり、代用せず除外した（§4-4）",
    "render_failed": "レンダ自体が失敗した（fail-closed。結果を捏造しない）",
    "degenerate_axis": "その (話者 × 世代) で MAD == 0 の軸（§7-0 (0)。Gate 側で使う）",
}


def _cells() -> List[Dict[str, Any]]:
    import score_diag_pf as diag

    cells = [dict(c) for c in diag.enumerate_cells()]
    for anchor in ANCHORS:
        c = dict(anchor)
        c["source"] = "full_song_render_excerpt"
        c["tempo_bpm"] = 72.0 if c["song"] == "sakura" else 88.0
        cells.append(c)
    return cells


def build_spec() -> Dict[str, Any]:
    import score_diag_pf as diag

    trf, trf_sha, _ = s7_io.read_json_with_pin(TRF_SPEC_PATH)
    if trf.get("freeze_status") != "frozen":
        raise ValueError(
            f"TRF measurement spec が frozen でない（{trf.get('freeze_status')!r}）。"
            "凍結前に probe を事前登録しない（測る道具が決まっていない）"
        )
    cells = _cells()
    matrix = []
    for gen, info in GENERATIONS.items():
        for speaker in info["speakers"]:
            matrix.append({"generation": gen, "speaker": speaker, "cells": len(cells)})
    total = sum(m["cells"] for m in matrix)

    return {
        "schema": PROBE_SPEC_SCHEMA,
        "frozen_at": "2026-08-21",
        "authority": "DESIGN_S7_run8.md §4 / §4-3 / §5 と PR-2。事前登録（レンダ前に pin）",
        "order_discipline": (
            "本文書を commit / pin してから 1 セル目をレンダする（§12-0-C2 と同じ規律）。"
            "レンダ後に条件を足さない。追加は別 amendment。"
        ),
        "measurement": {
            "trf_measurement_spec": {
                "path": str(TRF_SPEC_PATH.relative_to(_HERE.parents[2])),
                "sha256": trf_sha,
                "spec_version": trf["spec_version"],
                "freeze_status": trf["freeze_status"],
            },
            "primary_axes": {
                axis: {
                    "selected_candidate": trf["axes"][axis]["selected_candidate"],
                    "epsilon": trf["axes"][axis]["epsilon"],
                    "unit": trf["axes"][axis]["unit"],
                }
                for axis in sp.PRIMARY_AXES
            },
            "raw_waveform_rule": (
                "一次指標は**正規化前の生波形**で測る（§5-2）。probe は "
                "`synth_song` のピーク正規化を通さず `run_pipeline` の出力を直接測る。"
            ),
            "boundaries_rule": (
                "命令区間（`final_phone_dur`）から算出する。波形から推定しない。"
            ),
            "ringing_correction": {
                "rule": "§5-0。無声対照との差分で報告する",
                "unvoiced_reference_test": "終端窓の voiced_frames == 0 を機械検査で満たしたレンダのみ",
                "leave_one_out": True,
                "su_is_not_assumed_unvoiced": True,
                "empty_reference": "status = ringing_uncorrected で生値のまま報告する",
                "group_rule": (
                    "比較群（同一話者 × 同一世代）で 1 つでも補正不能なら群ごと Gate から除外し "
                    "status = ringing_uncorrected_group を立てる"
                ),
            },
            "status_vocabulary": STATUS_VOCABULARY,
        },
        "render_contract": {
            "one_target_event_per_render": True,
            "why": (
                "レンダラにフレーズの概念が無く SP は発話末にしか付かない（§4-0）。"
                "連結すると最後の 1 セル以外が黙って無効になる。"
            ),
            "anchors_rerendered_in_same_batch": True,
            "anchor_why": "既存 wav の流用ではドリフト由来か文脈由来かを分離できない（§4-2）",
            "normalization": "none（生波形のまま測る）",
            "harness": "voice_genesis/foundry/s1_gate/gate_synth.py::run_pipeline",
            "song_ids": {
                "diagnostic": "diag:<cell_id>（`load_song_module` の診断分岐。sha pin つき）",
                "anchor": "sakura / umi（フル譜面。既存経路と同一）",
            },
        },
        "score_modules": {
            "score_diag_pf.py": s7_io.read_bytes_with_pin(SCORE_DIAG_PATH)[1],
            "score.py": s7_io.read_bytes_with_pin(SCORE_PATH)[1],
            "score_umi.py": s7_io.read_bytes_with_pin(SCORE_UMI_PATH)[1],
            "phoneme_jp.py": s7_io.read_bytes_with_pin(PHONEME_JP_PATH)[1],
        },
        "factor_levels": {
            "pitch_bank": diag.PITCH_BANK,
            "duration_bank": diag.DURATION_BANK,
            "tempo_bpm_diagnostic": diag.TEMPO_BPM,
            "reduced_probes_pitch": diag.REDUCED_PITCH,
            "medial_filler_kana": diag.MEDIAL_FILLER_KANA,
            "medial_filler_beats": diag.MEDIAL_FILLER_BEATS,
            "underspecified_resolutions": [
                {
                    "what": "P-RI-MEDIAL / P-SU-FINAL の 3 条件の取り方",
                    "design_says": "§4-1 は条件数 3 だけを規定し、水準を特定していない",
                    "resolution": (
                        "音高を中央 (D4) に固定し尺 3 水準を動かす。位置・音素の分離が目的なので "
                        "同音高・同尺の P-RI-FINAL と 1:1 で対にできることを優先した"
                    ),
                },
                {
                    "what": "語中条件の充填モーラ",
                    "resolution": "「か」1 拍・同音高（標的終端に有声で連続しない無声子音始まり）",
                },
                {
                    "what": "P-ANCHOR 3 番目「うみ」の標的モーラ",
                    "resolution": "うみ譜面の発話末「な」（後続 SP を持つのは発話末だけ = §4-0）",
                    "alternative_reading": "語「うみ」の /mi/（medial になる）。差し替えは 1 セルで足りる",
                },
            ],
        },
        "cells": cells,
        "n_cells": len(cells),
        "expansion": {
            "generations": GENERATIONS,
            "matrix": matrix,
            "n_total_cells": total,
            "design_total": 360,
            "checkpoint_rule": (
                "ONNX 実体が無い世代は 40K checkpoint から再 export し、記録済み sha256 と"
                "照合する。不一致は fail-closed（§4-3）"
            ),
        },
        "not_decided_here": [
            "耳ラベル（§6）とその 2 値化",
            "Gate 1 の閾値と合否（§7-0b）",
            "軸の向き orient（§7-0 (2)）— 校正データが要る",
            "z 化（§7-0 (0)）— Gate 側で (話者 × 世代) 内に対して行う",
        ],
        "prohibitions": [
            "レンダ後にセルや水準を足さない（追加は別 amendment）",
            "測定仕様（凍結済み 1.0）の候補・窓・hop をここで変えない",
            "計測不能を黙って落とさない（status を立てて記帳する）",
            "P-ANCHOR に既存 wav を流用しない",
        ],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=SPEC_PATH)
    ap.add_argument("--check", action="store_true", help="既存 spec と一致するかだけ見る")
    args = ap.parse_args(list(argv) if argv is not None else None)

    doc = build_spec()
    s7_io.assert_json_finite(doc)
    text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        current = args.out.read_text(encoding="utf-8")
        if current != text:
            print("MISMATCH: 生成物が pin 済み spec と違う")
            return 1
        print("OK: spec は生成物と一致")
        return 0
    s7_io.reject_output_collision([args.out], [TRF_SPEC_PATH, SCORE_DIAG_PATH])
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {args.out} ({doc['n_cells']} cells x {len(doc['expansion']['matrix'])} groups "
          f"= {doc['expansion']['n_total_cells']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
