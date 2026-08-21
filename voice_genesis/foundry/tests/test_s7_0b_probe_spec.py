"""test_s7_0b_probe_spec.py — Run 8-0b probe set の事前登録の番人。

守るのは §4 の**割付と順序規律**であって測定値ではない:

- 36 セル（診断 33 + アンカー 3）と 360 セル展開が設計と一致する
- 3 対照差分（§5-1）が **9 つの (音高 × 尺) すべてで定義できる**
- H0 の対（P-ANCHOR かぎり ↔ P-RI-FINAL|low|b4）が同条件である
- spec が**凍結済み**の測定仕様（1.0 / frozen）を pin している
- spec が譜面モジュールの sha を pin しており、実体と一致する
- spec が再生成でバイト一致する（後から手で書き換えていない）

実行: `python -m pytest voice_genesis/foundry/tests/test_s7_0b_probe_spec.py -q`
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

_FOUNDRY = Path(__file__).resolve().parent.parent
_RUN8 = _FOUNDRY / "run8"
_SINGER = _FOUNDRY.parent / "singer"
for _p in (str(_RUN8), str(_SINGER)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import s7_0b_probe_spec as ps  # noqa: E402
import score_diag_pf as diag  # noqa: E402
import s7_spec as sp  # noqa: E402

SPEC_PATH = _FOUNDRY / "results_s7" / "s7_0b_probe_spec.json"


@pytest.fixture(scope="module")
def spec():
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def test_schema_and_cell_count(spec):
    assert spec["schema"] == ps.PROBE_SPEC_SCHEMA
    assert spec["n_cells"] == 36
    ids = [c["cell_id"] for c in spec["cells"]]
    assert len(set(ids)) == 36


def test_probe_allocation_matches_the_design_table(spec):
    counts = {}
    for c in spec["cells"]:
        counts[c["probe"]] = counts.get(c["probe"], 0) + 1
    assert counts == {
        "P-RI-FINAL": 9,
        "P-RI-MEDIAL": 3,
        "P-I-FINAL": 9,
        "P-N-FINAL": 9,
        "P-SU-FINAL": 3,
        "P-ANCHOR": 3,
    }


def test_expansion_is_360(spec):
    exp = spec["expansion"]
    assert exp["n_total_cells"] == exp["design_total"] == 360
    speakers = {g: set(v["speakers"]) for g, v in exp["generations"].items()}
    assert speakers == {
        "run5": {"ritsu", "pjs", "user"},
        "run6": {"ritsu", "pjs", "user"},
        "run7": {"ritsu", "pjs", "user", "amitaro"},
    }


def test_three_way_contrast_is_defined_for_every_pitch_and_duration(spec):
    """§5-1 の中核手法が 9 セル**すべて**で成立する（3×3 拡張の理由そのもの）。"""
    by_probe = {}
    for c in spec["cells"]:
        if c["probe"] in ("P-RI-FINAL", "P-I-FINAL", "P-N-FINAL"):
            by_probe.setdefault(c["probe"], set()).add((c["pitch_key"], c["duration_key"]))
    assert len(by_probe) == 3
    grids = list(by_probe.values())
    assert all(len(g) == 9 for g in grids)
    assert grids[0] == grids[1] == grids[2]


def test_h0_pair_is_matched_in_pitch_and_duration(spec):
    """H0 = P-ANCHOR「かぎり」と同条件の P-RI-FINAL の差（§4-2）。"""
    cells = {c["cell_id"]: c for c in spec["cells"]}
    anchor = cells["P-ANCHOR|sakura|kagiri"]
    diagnostic = cells["P-RI-FINAL|low|b4"]
    assert anchor["role"] == "positive_control"
    for key in ("pitch_midi", "duration_beats", "expected_terminal", "position"):
        assert anchor[key] == diagnostic[key], key
    assert anchor["has_following_sp"] is True


def test_medial_cells_declare_no_following_sp(spec):
    """§4-0: SP が付くのは発話末だけ。語中セルに終端解放を期待しない。"""
    for c in spec["cells"]:
        if c["position"] == "medial":
            assert c["has_following_sp"] is False, c["cell_id"]
        if c["position"] == "final" and c["probe"] != "P-ANCHOR":
            assert c["has_following_sp"] is True, c["cell_id"]


def test_spec_pins_a_frozen_measurement_spec(spec):
    m = spec["measurement"]["trf_measurement_spec"]
    assert m["freeze_status"] == "frozen"
    assert m["spec_version"] == "1.0"
    path = _FOUNDRY.parents[1] / m["path"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == m["sha256"]
    assert set(spec["measurement"]["primary_axes"]) == set(sp.PRIMARY_AXES)


def test_spec_pins_score_modules_and_they_match_disk(spec):
    for name, sha in spec["score_modules"].items():
        path = _SINGER / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sha, name


def test_spec_regenerates_byte_identically():
    """pin 済み spec が生成器の出力と一致する（後から手で書き換えていない）。"""
    doc = ps.build_spec()
    text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    assert text == SPEC_PATH.read_text(encoding="utf-8")


def test_diagnostic_scores_are_one_target_event_each():
    """1 セル = 1 譜面。語尾セルは 1 ノート、語中セルは標的 + 充填 1 ノート。"""
    for cell in diag.enumerate_cells():
        notes = diag.build_cell_score(cell)
        if cell["position"] == "final":
            assert len(notes) == 1, cell["cell_id"]
        else:
            assert len(notes) == 2, cell["cell_id"]
            assert notes[0].duration_beats == cell["duration_beats"]
        assert diag.target_note_index(cell) == 0


def test_status_vocabulary_covers_the_unmeasurable_paths(spec):
    vocab = spec["measurement"]["status_vocabulary"]
    for key in (
        "ok",
        "ringing_uncorrected",
        "ringing_uncorrected_group",
        "dictionary_uncovered",
        "render_failed",
    ):
        assert key in vocab


def test_spec_does_not_decide_gate_or_ear_labels(spec):
    text = json.dumps(spec, ensure_ascii=False)
    assert "threshold" not in text.lower() or "not_decided_here" in text
    assert spec["not_decided_here"]
    for token in ("nasalization", "continuous_voicing", "intelligibility"):
        assert token not in text, f"耳ラベルの語 {token} が probe 事前登録に入っている"
