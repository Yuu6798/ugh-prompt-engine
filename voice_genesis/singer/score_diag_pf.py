"""singer/score_diag_pf.py — Run 8-0b 診断 probe セット（`DESIGN_S7_run8.md` §4-1）。

`score.py` / `score_umi.py` は**無改変**で `ScoreNote` 型のみ import 流用する
（`score_umi.py` / `score_d3_sustain.py` と同じ流儀）。

本モジュールが決定論的に展開するのは **§4-1 の 36 セルのうち診断 33 セル**で、
残る 3 セル（P-ANCHOR）は**実曲のフル譜面レンダから切り出す**ため
（§4-2）ここでは譜面を作らず、切り出し対象の指定だけを持つ。

設計の要点（**なぜ 1 セル 1 レンダか**・§4-0）:

- レンダラにフレーズの概念が無く、SP が付くのは**発話末だけ**である。複数セルを
  1 レンダへ連結すると最後の 1 つしか「→SP」を持たず、残りが黙って無効になる。
  したがって **1 target event = 1 render**。本モジュールの各セルは 1 譜面である

音高帯（§4-1）: 低 A3(57) / 中 D4(62) / 高 F4(65) = `score.py` 都節音階の
deg1 / deg3 / deg5。**新規音律を導入しない**。
尺: 1 / 2 / 4 拍、テンポ 72 BPM（`score.py` と同一。P-ANCHOR「かぎり」と揃える）。
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import phoneme_jp as pj
from score import ScoreNote  # 無改変・import 流用（型を共有するだけ）

TEMPO_BPM = 72.0

# 音高帯（§4-1）。`score.py` の _SCALE_ROOT_MIDI=57 + 都節音階 deg1/3/5。
PITCH_BANK: Dict[str, float] = {"low": 57.0, "mid": 62.0, "high": 65.0}
DURATION_BANK: Dict[str, float] = {"b1": 1.0, "b2": 2.0, "b4": 4.0}

#: 語中条件・無声語尾条件を 3 セルへ絞るときの固定水準（§4-1 は条件数のみ規定）。
#: 音高を中央に固定して尺 3 水準を動かす。位置（final / medial）の分離が目的なので、
#: **同じ音高・同じ尺の P-RI-FINAL と 1:1 で対になる**ことを優先する。
REDUCED_PITCH = "mid"

#: 語中条件で標的モーラの後ろに置く充填モーラ（無声子音 + 母音）。
#: 「次の音素が来る」ことだけが要件なので、標的の終端に有声で連続しない
#: `/k/` 始まりを選び、1 拍・同音高で固定する。
MEDIAL_FILLER_KANA = "か"
MEDIAL_FILLER_BEATS = 1.0


def beats_to_seconds(beats: float, tempo_bpm: float = TEMPO_BPM) -> float:
    return float(beats) * 60.0 / float(tempo_bpm)


def _mora(kana: str) -> pj.Mora:
    morae = pj.kana_to_morae(kana)
    if len(morae) != 1:
        raise ValueError(f"{kana!r} は 1 モーラではない: {morae}")
    return morae[0]


#: probe -> (標的モーラのかな, 位置, 期待終端ラベル)
PROBE_TARGETS: Dict[str, Tuple[str, str, str]] = {
    "P-RI-FINAL": ("り", "final", "ri"),
    "P-RI-MEDIAL": ("り", "medial", "ri"),
    "P-I-FINAL": ("い", "final", "i"),
    "P-N-FINAL": ("ん", "final", "N"),
    "P-SU-FINAL": ("す", "final", "su"),
}

#: 3×3 に展開する probe（§4-1 の改訂: 対照 2 系も 3×3 にした）。
FULL_GRID_PROBES = ("P-RI-FINAL", "P-I-FINAL", "P-N-FINAL")
#: 尺 3 水準のみ（音高は `REDUCED_PITCH` 固定）。
REDUCED_PROBES = ("P-RI-MEDIAL", "P-SU-FINAL")


def cell_id(probe: str, pitch_key: str, dur_key: str) -> str:
    return f"{probe}|{pitch_key}|{dur_key}"


def enumerate_cells() -> List[Dict[str, object]]:
    """診断 33 セルを事前登録順（決定論）で返す。P-ANCHOR は含まない。"""
    cells: List[Dict[str, object]] = []
    for probe in FULL_GRID_PROBES:
        for pitch_key in ("low", "mid", "high"):
            for dur_key in ("b1", "b2", "b4"):
                cells.append(_cell(probe, pitch_key, dur_key))
    for probe in REDUCED_PROBES:
        for dur_key in ("b1", "b2", "b4"):
            cells.append(_cell(probe, REDUCED_PITCH, dur_key))
    return cells


def _cell(probe: str, pitch_key: str, dur_key: str) -> Dict[str, object]:
    kana, position, expected_terminal = PROBE_TARGETS[probe]
    return {
        "cell_id": cell_id(probe, pitch_key, dur_key),
        "probe": probe,
        "kana": kana,
        "position": position,
        "expected_terminal": expected_terminal,
        "pitch_key": pitch_key,
        "pitch_midi": PITCH_BANK[pitch_key],
        "duration_key": dur_key,
        "duration_beats": DURATION_BANK[dur_key],
        "tempo_bpm": TEMPO_BPM,
        "has_following_sp": position == "final",
        "source": "diagnostic_score",
    }


def build_cell_score(cell: Dict[str, object]) -> List[ScoreNote]:
    """1 セル = 1 譜面（`ScoreNote` 列）。語中条件だけ充填モーラを 1 つ足す。"""
    midi = float(cell["pitch_midi"])
    beats = float(cell["duration_beats"])
    notes = [
        ScoreNote(
            midi=midi,
            duration_beats=beats,
            mora=_mora(str(cell["kana"])),
            phrase_index=0,
            is_phrase_final=(cell["position"] == "final"),
        )
    ]
    if cell["position"] == "medial":
        notes.append(
            ScoreNote(
                midi=midi,
                duration_beats=MEDIAL_FILLER_BEATS,
                mora=_mora(MEDIAL_FILLER_KANA),
                phrase_index=0,
                is_phrase_final=True,
            )
        )
    return notes


def target_note_index(cell: Dict[str, object]) -> int:
    """標的ノートの位置（語中条件でも標的は先頭ノート）。"""
    return 0


def build_score_for_cell_id(cid: str) -> List[ScoreNote]:
    for cell in enumerate_cells():
        if cell["cell_id"] == cid:
            return build_cell_score(cell)
    raise KeyError(f"unknown diagnostic cell: {cid}")
