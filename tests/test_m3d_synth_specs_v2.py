"""tests/test_m3d_synth_specs_v2.py — `tests/fixtures/melody_bench/m3d_synth_specs_v2.yaml`
（M3d v2・合成素材）の構造検証。

実音声・crepe 非依存（CI 安全・`pytest -m "not slow"` に含む）: `docs/measurements/
m3d_2026-08/preregistration_v2.md` §4 が要求する「ゲート通過可能な構造」（2 フレーズ
以上・フレーズ間ギャップ >=1.2s・総 note 数 >=10）を spec 直読みの機械アサートで
確認し（(A)）、negative_rhythm/negative_interval 対の「診断が構造的に成立しうる」
ことを `build_sequences` 直通しで確認する（(B)。v1 `tests/test_melody_comparison.py`
の `test_m3d_negative_rhythm_pair{1,2}_is_structurally_diagnosable` と同型）。

v1 の `tests/fixtures/melody_bench/m3d_synth_specs.yaml` / `tests/test_melody_comparison.py`
はいずれも不変更（本ファイルは v2 専用の新規テストファイル）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_melody_bench as bmb  # noqa: E402

from svp_rpe.melody.observability import MelodyNote  # noqa: E402
from svp_rpe.melody.representation import build_sequences, load_m3_registry  # noqa: E402

BENCH_DIR = ROOT / "tests" / "fixtures" / "melody_bench"
SPECS_V2_PATH = BENCH_DIR / "m3d_synth_specs_v2.yaml"
M3_REGISTRY_PATH = BENCH_DIR / "m3_comparison_registry.yaml"

# prereg_v2 §4 の構造下限。
_MIN_PHRASE_COUNT = 2
_MIN_PHRASE_GAP_SEC = 1.2
_MIN_NOTE_COUNT = 10

_ALL_FIXTURE_IDS = (
    "m3d_synth_tuning_pos",
    "m3d_synth_holdout_pos",
    "m3d_synth_rhythm_neg1_a",
    "m3d_synth_rhythm_neg1_b",
    "m3d_synth_rhythm_neg2_a",
    "m3d_synth_rhythm_neg2_b",
    "m3d_synth_interval_neg1_a",
    "m3d_synth_interval_neg1_b",
    "m3d_synth_interval_neg2_a",
    "m3d_synth_interval_neg2_b",
)


def _default_config():
    config, _ = load_m3_registry(M3_REGISTRY_PATH)
    return config


def _load_v2_specs() -> Dict[str, Any]:
    return bmb.load_specs(SPECS_V2_PATH)


# --------------------------------------------------------------------------- #
# (A) ゲート通過可能構造の機械アサート（spec 直読み・crepe 非依存）
# --------------------------------------------------------------------------- #
def test_v2_synth_specs_declares_all_expected_fixture_ids():
    specs = _load_v2_specs()
    assert set(specs["fixtures"]) == set(_ALL_FIXTURE_IDS)


def _total_note_count(spec: Dict[str, Any]) -> int:
    phrases: List[List[float]] = spec["phrases"]
    return sum(len(phrase) for phrase in phrases)


def test_v2_synth_specs_all_fixtures_meet_structural_gate_bounds():
    """prereg_v2 §4: 全 10 fixture が 2 フレーズ以上・phrase_gap_sec>=1.2・
    総 note 数>=10 を満たすことを spec 直読みで確認する（音声合成・crepe 抽出を
    経由しない）。"""
    specs = _load_v2_specs()
    for fixture_id in _ALL_FIXTURE_IDS:
        spec = specs["fixtures"][fixture_id]
        assert spec["kind"] == "monophonic", fixture_id
        phrases = spec["phrases"]
        assert len(phrases) >= _MIN_PHRASE_COUNT, f"{fixture_id}: phrase_count"
        assert spec["phrase_gap_sec"] >= _MIN_PHRASE_GAP_SEC, f"{fixture_id}: phrase_gap_sec"
        assert _total_note_count(spec) >= _MIN_NOTE_COUNT, f"{fixture_id}: total note count"


def test_v2_synth_specs_negative_rhythm_pairs_share_pitch_sequence():
    """negative_rhythm 対は音程列（phrases の MIDI 値）が a/b で完全一致する
    （「同音程・別リズム」の前提。note_durs_sec の有無だけが違う）。"""
    specs = _load_v2_specs()
    for pair in (
        ("m3d_synth_rhythm_neg1_a", "m3d_synth_rhythm_neg1_b"),
        ("m3d_synth_rhythm_neg2_a", "m3d_synth_rhythm_neg2_b"),
    ):
        a_id, b_id = pair
        spec_a = specs["fixtures"][a_id]
        spec_b = specs["fixtures"][b_id]
        assert spec_a["phrases"] == spec_b["phrases"], pair
        assert "note_durs_sec" not in spec_a, a_id
        assert "note_durs_sec" in spec_b, b_id


def test_v2_synth_specs_negative_interval_pairs_share_timing_but_not_pitch():
    """negative_interval 対は timing（note_dur_sec/note_gap_sec/phrase_gap_sec）が
    a/b で完全一致し、音程列（phrases）は異なる（「同リズム・別音程列」の前提）。"""
    specs = _load_v2_specs()
    for pair in (
        ("m3d_synth_interval_neg1_a", "m3d_synth_interval_neg1_b"),
        ("m3d_synth_interval_neg2_a", "m3d_synth_interval_neg2_b"),
    ):
        a_id, b_id = pair
        spec_a = specs["fixtures"][a_id]
        spec_b = specs["fixtures"][b_id]
        for key in ("note_dur_sec", "note_gap_sec", "phrase_gap_sec"):
            assert spec_a[key] == spec_b[key], f"{pair}: {key}"
        assert spec_a["phrases"] != spec_b["phrases"], pair
        # フレーズ構造（フレーズ数・各フレーズのノート数）は同一でなければならない
        # （IOI 完全一致の前提 — ノート数が違えば IOI 列の長さ自体が変わる）。
        assert [len(p) for p in spec_a["phrases"]] == [len(p) for p in spec_b["phrases"]], pair


# --------------------------------------------------------------------------- #
# (B) 診断可能性（build_sequences 直通し・v1 test_m3d_negative_rhythm_pair* と同型）
# --------------------------------------------------------------------------- #
def _notes_for_phrase(
    midis: List[float], durs: List[float], *, gap: float, start_sec: float = 0.0
) -> List[MelodyNote]:
    notes: List[MelodyNote] = []
    t = start_sec
    for midi, dur in zip(midis, durs):
        notes.append(MelodyNote(start_sec=t, end_sec=t + dur, pitch_midi=float(midi), confidence=0.9))
        t += dur + gap
    return notes


def _assert_same_pitch_and_interval_structure(seqs_a, seqs_b) -> None:
    assert seqs_a.pitch_semitones == seqs_b.pitch_semitones
    assert seqs_a.intervals_raw == seqs_b.intervals_raw
    assert seqs_a.intervals_folded == seqs_b.intervals_folded
    assert seqs_a.contour == seqs_b.contour


def _assert_rhythm_structurally_separable(seqs_a, seqs_b) -> None:
    assert seqs_a.duration_log2_ratios != seqs_b.duration_log2_ratios
    assert seqs_a.ioi_log2_ratios != seqs_b.ioi_log2_ratios
    assert all(v == 0.0 for v in seqs_a.duration_log2_ratios if v is not None)
    assert all(v == 0.0 for v in seqs_a.ioi_log2_ratios if v is not None)
    assert any(v is not None and v != 0.0 for v in seqs_b.duration_log2_ratios)
    assert any(v is not None and v != 0.0 for v in seqs_b.ioi_log2_ratios)


def test_v2_synth_specs_negative_rhythm_pair1_is_structurally_diagnosable():
    """`m3d_synth_rhythm_neg1_a`/`_b`（v2）: 音程列共通 [60,62,64,65,67]/[67,65,64,62,60]
    （2 フレーズ）、a 側 note_dur_sec=0.3 一様、b 側 note_durs_sec 長短交互。"""
    specs = _load_v2_specs()
    config = _default_config()
    spec_a = specs["fixtures"]["m3d_synth_rhythm_neg1_a"]
    spec_b = specs["fixtures"]["m3d_synth_rhythm_neg1_b"]
    gap = spec_a["note_gap_sec"]

    for phrase_a, phrase_b, durs_b in zip(
        spec_a["phrases"], spec_b["phrases"], spec_b["note_durs_sec"]
    ):
        assert phrase_a == phrase_b
        notes_a = _notes_for_phrase(phrase_a, [spec_a["note_dur_sec"]] * len(phrase_a), gap=gap)
        notes_b = _notes_for_phrase(phrase_b, durs_b, gap=gap)
        seqs_a = build_sequences(notes_a, config)
        seqs_b = build_sequences(notes_b, config)
        _assert_same_pitch_and_interval_structure(seqs_a, seqs_b)
        _assert_rhythm_structurally_separable(seqs_a, seqs_b)


def test_v2_synth_specs_negative_rhythm_pair2_is_structurally_diagnosable():
    """`m3d_synth_rhythm_neg2_a`/`_b`（v2）: pair1 とは別の音程列・note_dur_sec。"""
    specs = _load_v2_specs()
    config = _default_config()
    spec_a = specs["fixtures"]["m3d_synth_rhythm_neg2_a"]
    spec_b = specs["fixtures"]["m3d_synth_rhythm_neg2_b"]
    gap = spec_a["note_gap_sec"]

    for phrase_a, phrase_b, durs_b in zip(
        spec_a["phrases"], spec_b["phrases"], spec_b["note_durs_sec"]
    ):
        assert phrase_a == phrase_b
        notes_a = _notes_for_phrase(phrase_a, [spec_a["note_dur_sec"]] * len(phrase_a), gap=gap)
        notes_b = _notes_for_phrase(phrase_b, durs_b, gap=gap)
        seqs_a = build_sequences(notes_a, config)
        seqs_b = build_sequences(notes_b, config)
        _assert_same_pitch_and_interval_structure(seqs_a, seqs_b)
        _assert_rhythm_structurally_separable(seqs_a, seqs_b)


def _assert_interval_structurally_separable(seqs_a, seqs_b) -> None:
    """「別音程列」——interval/contour 軸が両側で実質的に相違することを確認する
    （negative_rhythm 側の `_assert_rhythm_structurally_separable` と対称）。"""
    assert seqs_a.pitch_semitones != seqs_b.pitch_semitones
    assert seqs_a.intervals_raw != seqs_b.intervals_raw or seqs_a.contour != seqs_b.contour


def _assert_same_rhythm_structure(seqs_a, seqs_b) -> None:
    """「同リズム」——duration/IOI の log2 比列（rhythm 軸表現）が両側で完全一致する
    ことを確認する（negative_interval 対の前提）。"""
    assert seqs_a.duration_log2_ratios == seqs_b.duration_log2_ratios
    assert seqs_a.ioi_log2_ratios == seqs_b.ioi_log2_ratios


def test_v2_synth_specs_negative_interval_pair1_is_structurally_diagnosable():
    specs = _load_v2_specs()
    config = _default_config()
    spec_a = specs["fixtures"]["m3d_synth_interval_neg1_a"]
    spec_b = specs["fixtures"]["m3d_synth_interval_neg1_b"]

    for phrase_a, phrase_b in zip(spec_a["phrases"], spec_b["phrases"]):
        notes_a = _notes_for_phrase(
            phrase_a, [spec_a["note_dur_sec"]] * len(phrase_a), gap=spec_a["note_gap_sec"]
        )
        notes_b = _notes_for_phrase(
            phrase_b, [spec_b["note_dur_sec"]] * len(phrase_b), gap=spec_b["note_gap_sec"]
        )
        seqs_a = build_sequences(notes_a, config)
        seqs_b = build_sequences(notes_b, config)
        _assert_same_rhythm_structure(seqs_a, seqs_b)
        _assert_interval_structurally_separable(seqs_a, seqs_b)


def test_v2_synth_specs_negative_interval_pair2_is_structurally_diagnosable():
    specs = _load_v2_specs()
    config = _default_config()
    spec_a = specs["fixtures"]["m3d_synth_interval_neg2_a"]
    spec_b = specs["fixtures"]["m3d_synth_interval_neg2_b"]

    for phrase_a, phrase_b in zip(spec_a["phrases"], spec_b["phrases"]):
        notes_a = _notes_for_phrase(
            phrase_a, [spec_a["note_dur_sec"]] * len(phrase_a), gap=spec_a["note_gap_sec"]
        )
        notes_b = _notes_for_phrase(
            phrase_b, [spec_b["note_dur_sec"]] * len(phrase_b), gap=spec_b["note_gap_sec"]
        )
        seqs_a = build_sequences(notes_a, config)
        seqs_b = build_sequences(notes_b, config)
        _assert_same_rhythm_structure(seqs_a, seqs_b)
        _assert_interval_structurally_separable(seqs_a, seqs_b)
