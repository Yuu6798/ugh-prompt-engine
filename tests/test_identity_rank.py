"""tests/test_identity_rank.py — WI2 identity-rank instrument.

Hand-calculation cross-check gate: the structure / bpm / brightness anchors
in ``wi2_preregistration_design.md`` section C are independently reproduced
here against synthetic ``RPEBundle`` / ``CompositionScore`` inputs (no audio
synthesis, no real extraction — same fast-loop style
``tests/test_roundtrip_repetition.py`` and ``tests/test_score_adherence.py``
use for their own pure-logic tests). Per the task brief: if any of these
numbers disagree with the design memo, that is a stop-and-report condition,
not something to quietly "fix" here.
"""
from __future__ import annotations

import builtins
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from svp_rpe.cli import app
from svp_rpe.compose.models import CompositionScore
from svp_rpe.rpe.models import ChordEvent, PhysicalRPE, RPEBundle, SectionMarker, SpectralProfile
from svp_rpe.rpe.semantic_rules import generate_semantic
from svp_rpe.roundtrip.identity_rank import (
    ReferenceEntry,
    ReferenceList,
    _ResolvedReference,
    _brightness_axis,
    _bpm_axis,
    _harmony_axis,
    _key_axis,
    _structure_axis,
    identity_rank_from_paths,
    normalize_observed_section_label,
    normalize_reference_section_label,
    position_match_rate,
    render_identity_rank_text,
    run_identity_rank,
)

runner = CliRunner()


def _fake_import_without_mir_eval(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same technique ``tests/test_keys.py`` uses to simulate a normal
    (non-``dev``-extra) install where ``mir_eval`` is absent: patch
    ``builtins.__import__`` to raise ``ModuleNotFoundError`` only for
    ``mir_eval.key``, leaving every other import untouched."""

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "mir_eval.key":
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def _assert_no_outcome_keys(value: Any) -> None:
    if isinstance(value, dict):
        for forbidden in ("verdict", "passed", "pass", "failed", "fail", "ok", "threshold"):
            assert forbidden not in value
        for item in value.values():
            _assert_no_outcome_keys(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_outcome_keys(item)


def _bundle(
    *,
    structure_labels: list[str],
    chords: list[tuple[str, str]] | None = None,
    bpm: float | None = 120.0,
    key: str | None = "C",
    mode: str | None = "minor",
    spectral_centroid: float = 2200.0,
    bpm_octave_ambiguous: bool = False,
) -> RPEBundle:
    """Synthetic RPEBundle carrying only the fields identity_rank's axes read.

    ``key`` / ``mode`` are kept as two separate fields on purpose — matching
    real ``RPEBundle`` fixtures (e.g. ``wi2_A_take1.rpe.json``: ``key: "C"``,
    ``mode: "minor"``) rather than a single combined ``"C minor"`` string.
    """

    markers = [
        SectionMarker(label=label, start_sec=float(index), end_sec=float(index + 1))
        for index, label in enumerate(structure_labels)
    ]
    chord_events = [
        ChordEvent(
            chord=f"{root} {quality}",
            root=root,
            quality=quality,
            start_sec=float(index),
            end_sec=float(index + 1),
            confidence=0.9,
        )
        for index, (root, quality) in enumerate(chords or [])
    ]
    physical = PhysicalRPE(
        bpm=bpm,
        bpm_confidence=0.9 if bpm is not None else None,
        bpm_octave_ambiguous=bpm_octave_ambiguous,
        key=key,
        mode=mode,
        key_confidence=0.9 if key is not None else None,
        duration_sec=8.0,
        sample_rate=44100,
        time_signature="4/4",
        time_signature_confidence=0.9,
        structure=markers,
        rms_mean=0.2,
        peak_amplitude=0.8,
        crest_factor=4.0,
        active_rate=0.73,
        valley_depth=0.18,
        thickness=1.0,
        spectral_centroid=spectral_centroid,
        spectral_profile=SpectralProfile(
            centroid=spectral_centroid, low_ratio=0.4, mid_ratio=0.5, high_ratio=0.1, brightness=0.1,
        ),
        stereo_profile=None,
        onset_density=2.0,
        chord_events=chord_events,
    )
    return RPEBundle(
        physical=physical,
        semantic=generate_semantic(physical),
        audio_file="fixture.wav",
        audio_duration_sec=8.0,
        audio_sample_rate=44100,
        audio_channels=1,
        audio_format="wav",
    )


def _score(
    *,
    bpm: int | str = 128,
    key: str = "C minor",
    brightness: str = "dark",
    structure_sections: list[str],
) -> CompositionScore:
    return CompositionScore.model_validate(
        {
            "meta": {"title": "fixture", "version": "0.1"},
            "semantic": {
                "core": "core",
                "grv": {"primary": "p", "secondary": "s"},
                "delta_e": {"overall": "o"},
                "avoid": [],
            },
            "physical": {
                "bpm": bpm,
                "key": key,
                "time_signature": "4/4",
                "active_rate_target": "0.10-0.90",
                "valley_depth_target": "0.10-0.90",
                "brightness": brightness,
                "stereo_width": "wide",
            },
            "structure": [
                {"section": section, "bars": 1, "role": "r", "physical": "p"}
                for section in structure_sections
            ],
            "rendering": {
                "target_backend": "external",
                "prompt_max_chars": 650,
                "priority": ["semantic.core"],
            },
        }
    )


def _resolved(ref_id: str, score: CompositionScore, progression=None) -> _ResolvedReference:
    return _ResolvedReference(ref_id, score, progression)


# ---------------------------------------------------------------------------
# Hand-calculation cross-check gate (design memo section C anchors)
# ---------------------------------------------------------------------------


def test_structure_anchor_vs_canonical() -> None:
    """observed [intro, chorus, outro] vs canonical [intro, verse, chorus,
    bridge]: match at pos0 only -> rate 1/4 = 0.25 -> distance 0.75."""

    bundle = _bundle(structure_labels=["intro", "chorus", "outro"])
    ref = _resolved("canonical", _score(structure_sections=["intro", "verse", "chorus", "bridge"]))

    axis = _structure_axis(bundle, [ref])

    assert axis.ranking[0].ref_id == "canonical"
    assert axis.ranking[0].distance == 0.75


def test_structure_anchor_vs_structure_destroyed() -> None:
    """observed [intro, chorus, outro] vs C [intro, chorus, verse, bridge]:
    match pos0, pos1 -> rate 2/4 = 0.5 -> distance 0.5."""

    bundle = _bundle(structure_labels=["intro", "chorus", "outro"])
    ref = _resolved(
        "structure_destroyed", _score(structure_sections=["intro", "chorus", "verse", "bridge"])
    )

    axis = _structure_axis(bundle, [ref])

    assert axis.ranking[0].distance == 0.5


def test_structure_anchor_vs_other_work() -> None:
    """observed [intro, chorus, outro] vs D [intro, body, outro]: match
    pos0, pos2 -> rate 2/3 ~= 0.6667 -> distance ~= 0.3333 (other work is
    the *closest* on this axis for this observed pattern — an intentional,
    pre-registered result per the design memo, not a bug)."""

    bundle = _bundle(structure_labels=["intro", "chorus", "outro"])
    ref = _resolved("other_work", _score(structure_sections=["intro", "body", "outro"]))

    axis = _structure_axis(bundle, [ref])

    assert axis.ranking[0].distance == 0.3333


def test_bpm_anchor() -> None:
    """observed 120.0 vs reference 128 -> 8/128 = 0.0625."""

    bundle = _bundle(structure_labels=["intro"], bpm=120.0)
    ref = _resolved("canonical", _score(bpm=128, structure_sections=["intro"]))

    axis = _bpm_axis(bundle, [ref])

    assert axis.ranking[0].distance == 0.0625


def test_brightness_anchor() -> None:
    """declared bright (>=2500) vs observed centroid 2200 ->
    (2500-2200)/2500 = 0.12."""

    bundle = _bundle(structure_labels=["intro"], spectral_centroid=2200.0)
    ref = _resolved("canonical", _score(brightness="bright", structure_sections=["intro"]))

    axis = _brightness_axis(bundle, [ref])

    assert axis.ranking[0].distance == 0.12


def test_brightness_dark_in_band_is_zero() -> None:
    bundle = _bundle(structure_labels=["intro"], spectral_centroid=900.0)
    ref = _resolved("canonical", _score(brightness="dark", structure_sections=["intro"]))

    axis = _brightness_axis(bundle, [ref])

    assert axis.ranking[0].distance == 0.0


def test_brightness_neutral_in_band_is_zero() -> None:
    bundle = _bundle(structure_labels=["intro"], spectral_centroid=1800.0)
    ref = _resolved("canonical", _score(brightness="neutral", structure_sections=["intro"]))

    axis = _brightness_axis(bundle, [ref])

    assert axis.ranking[0].distance == 0.0


# ---------------------------------------------------------------------------
# Key axis: bundle key/mode are separate fields (regression for #key-axis-bug)
#
# Real RPEBundle fixtures (e.g. wi2_A_take1.rpe.json) store the tonic and
# mode as two separate physical fields (``key: "C"``, ``mode: "minor"``),
# while a reference CompositionScore's physical.key is a single mir_eval-
# style "<tonic> <mode>" string (e.g. "C minor"). ``_key_axis`` must join the
# bundle's two fields before scoring, or every comparison silently fails
# mir_eval's key-label validation and falls back to a 0.0 score (distance
# 1.0) for every reference, even an exact match. The previous synthetic
# ``_bundle`` helper masked this: it hardcoded ``mode="minor"`` and let
# callers pass an already-combined string (e.g. ``key="C major"``) as
# ``key``, which happened to already be in the exact single-string form
# mir_eval expects — never exercising the real two-field bundle shape.
# ---------------------------------------------------------------------------


def test_key_axis_exact_match_real_bundle_format_is_zero_distance() -> None:
    """Minimal excerpt of wi2_A_take1.rpe.json's shape: bundle key="C" +
    mode="minor" (separate fields) vs a canonical reference of "C minor"
    (single mir_eval-style string, as composition_score.yaml declares) must
    score as a perfect match (distance 0.0), not distance 1.0.

    ``_key_axis`` requires mir_eval (see the not_observed-degrade test
    below), so this numeric assertion needs a real mir_eval to be installed
    — skip rather than fail on a non-dev install."""

    pytest.importorskip("mir_eval")
    bundle = _bundle(structure_labels=["intro"], key="C", mode="minor")
    ref = _resolved("canonical", _score(key="C minor", structure_sections=["intro"]))

    axis = _key_axis(bundle, [ref])

    assert axis.ranking[0].distance == 0.0


def test_key_axis_mismatched_key_is_nonzero_distance() -> None:
    pytest.importorskip("mir_eval")
    bundle = _bundle(structure_labels=["intro"], key="C", mode="minor")
    ref = _resolved("other_work", _score(key="F# major", structure_sections=["intro"]))

    axis = _key_axis(bundle, [ref])

    assert axis.ranking[0].distance > 0.0


# ---------------------------------------------------------------------------
# Key axis: mir_eval unavailable degrades to not_observed (Codex P2 #201:
# require_mir_eval=True instead of the undeclared exact-string-match
# fallback, which would otherwise silently change key-axis distances between
# dev-extra and normal installs).
# ---------------------------------------------------------------------------


def test_key_axis_not_observed_when_mir_eval_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_import_without_mir_eval(monkeypatch)

    bundle = _bundle(structure_labels=["intro"], key="C", mode="minor")
    ref = _resolved("canonical", _score(key="C minor", structure_sections=["intro"]))

    axis = _key_axis(bundle, [ref])

    assert axis.ranking == []
    assert axis.not_observed[0].ref_id == "canonical"
    assert axis.not_observed[0].reason == (
        "mir_eval unavailable — dev extra required for weighted key scoring"
    )


def test_key_axis_not_observed_when_mir_eval_unavailable_all_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every reference degrades to not_observed, not just the first one
    scored — mir_eval's absence is a single condition applying uniformly."""

    _fake_import_without_mir_eval(monkeypatch)

    bundle = _bundle(structure_labels=["intro"], key="C", mode="minor")
    refs = [
        _resolved("canonical", _score(key="C minor", structure_sections=["intro"])),
        _resolved("other_work", _score(key="F# major", structure_sections=["intro"])),
    ]

    axis = _key_axis(bundle, refs)

    assert axis.ranking == []
    assert {item.ref_id for item in axis.not_observed} == {"canonical", "other_work"}


# ---------------------------------------------------------------------------
# Normalization helpers (independent public reimplementation of observe.py)
# ---------------------------------------------------------------------------


def test_reference_label_normalization_lowercases_only() -> None:
    assert normalize_reference_section_label("Chorus1") == "chorus1"
    assert normalize_reference_section_label("VERSE2") == "verse2"


def test_observed_label_normalization_strips_verse_digits_only() -> None:
    assert normalize_observed_section_label("Verse2") == "verse"
    assert normalize_observed_section_label("Chorus2") == "chorus2"  # not stripped


def test_position_match_rate_empty_sequences_is_zero() -> None:
    assert position_match_rate([], []) == 0.0


# ---------------------------------------------------------------------------
# Harmony axis: not_observed when a reference declares no progression
# ---------------------------------------------------------------------------


def test_harmony_axis_not_observed_without_progression() -> None:
    bundle = _bundle(structure_labels=["intro"], chords=[("C", "minor")])
    ref = _resolved("structure_destroyed", _score(structure_sections=["intro"]), progression=None)

    axis = _harmony_axis(bundle, [ref])

    assert axis.ranking == []
    assert axis.not_observed[0].ref_id == "structure_destroyed"


def test_harmony_axis_reuses_repeated_chord_sequence_match_rate() -> None:
    bundle = _bundle(structure_labels=["intro"], chords=[("C", "minor"), ("G", "major")])
    ref = _resolved(
        "canonical",
        _score(structure_sections=["intro"]),
        progression=[("C", "minor"), ("G", "major")],
    )

    axis = _harmony_axis(bundle, [ref])

    assert axis.ranking[0].distance == 0.0


# ---------------------------------------------------------------------------
# Key / bpm axis: not_observed when the bundle's own sensor reading is absent
# ---------------------------------------------------------------------------


def test_key_axis_not_observed_when_bundle_key_missing() -> None:
    bundle = _bundle(structure_labels=["intro"], key=None)
    ref = _resolved("canonical", _score(structure_sections=["intro"]))

    axis = _key_axis(bundle, [ref])

    assert axis.ranking == []
    assert axis.not_observed[0].reason.startswith("bundle.physical.key is None")


def test_key_axis_not_observed_when_bundle_mode_missing() -> None:
    """A bare tonic without a mode (e.g. mode not detected in extraction)
    cannot form a valid mir_eval key label — not_observed, not a fabricated
    distance."""

    bundle = _bundle(structure_labels=["intro"], key="C", mode=None)
    ref = _resolved("canonical", _score(structure_sections=["intro"]))

    axis = _key_axis(bundle, [ref])

    assert axis.ranking == []
    assert axis.not_observed[0].reason.startswith("bundle.physical.mode is None")


def test_bpm_axis_not_observed_when_bundle_bpm_missing() -> None:
    bundle = _bundle(structure_labels=["intro"], bpm=None)
    ref = _resolved("canonical", _score(structure_sections=["intro"]))

    axis = _bpm_axis(bundle, [ref])

    assert axis.ranking == []
    assert axis.not_observed[0].reason.startswith("bundle.physical.bpm is None")


def test_brightness_axis_not_observed_for_unrecognized_declared_class() -> None:
    bundle = _bundle(structure_labels=["intro"], spectral_centroid=2000.0)
    ref = _resolved("canonical", _score(brightness="TODO(transcribe): unknown", structure_sections=["intro"]))

    axis = _brightness_axis(bundle, [ref])

    assert axis.ranking == []
    assert "not one of dark/neutral/bright" in axis.not_observed[0].reason


# ---------------------------------------------------------------------------
# Tie-break stability (repetition.py's RepetitionRankingEntry precedent)
# ---------------------------------------------------------------------------


def test_tie_break_orders_by_ascending_ref_id() -> None:
    bundle = _bundle(structure_labels=["intro", "verse"])
    refs = [
        _resolved("zzz_ref", _score(structure_sections=["intro", "verse"])),
        _resolved("aaa_ref", _score(structure_sections=["intro", "verse"])),
    ]

    axis = _structure_axis(bundle, refs)

    assert [entry.ref_id for entry in axis.ranking] == ["aaa_ref", "zzz_ref"]
    assert [entry.rank for entry in axis.ranking] == [1, 2]
    assert axis.ranking[0].distance == axis.ranking[1].distance == 0.0
    # Both share the same distance: the ref_id tie-break gave "aaa_ref"
    # rank1, but that is a machine tie-break pick, not a genuine
    # discrimination — tied_with must say so on both entries.
    assert axis.ranking[0].tied_with == ["zzz_ref"]
    assert axis.ranking[1].tied_with == ["aaa_ref"]


def test_tied_with_is_empty_for_a_genuine_unique_win() -> None:
    """A reference with a strictly smaller distance than every other
    reference is an undisputed win: tied_with must be empty, not just
    unmentioned, for every entry in a ranking with no ties at all."""

    bundle = _bundle(structure_labels=["intro", "verse"])
    refs = [
        _resolved("closer_ref", _score(structure_sections=["intro", "verse"])),
        _resolved("farther_ref", _score(structure_sections=["verse", "intro"])),
    ]

    axis = _structure_axis(bundle, refs)

    assert [entry.ref_id for entry in axis.ranking] == ["closer_ref", "farther_ref"]
    assert axis.ranking[0].tied_with == []
    assert axis.ranking[1].tied_with == []


def test_tied_with_groups_three_way_tie_excluding_self() -> None:
    """A three-way tie must list the *other two* ref_ids on each entry, in
    ascending ref_id order, never including the entry's own ref_id."""

    bundle = _bundle(structure_labels=["intro", "verse"])
    refs = [
        _resolved("bbb_ref", _score(structure_sections=["outro", "outro"])),
        _resolved("aaa_ref", _score(structure_sections=["outro", "outro"])),
        _resolved("ccc_ref", _score(structure_sections=["outro", "outro"])),
    ]

    axis = _structure_axis(bundle, refs)

    assert [entry.ref_id for entry in axis.ranking] == ["aaa_ref", "bbb_ref", "ccc_ref"]
    assert axis.ranking[0].tied_with == ["bbb_ref", "ccc_ref"]
    assert axis.ranking[1].tied_with == ["aaa_ref", "ccc_ref"]
    assert axis.ranking[2].tied_with == ["aaa_ref", "bbb_ref"]


# ---------------------------------------------------------------------------
# End-to-end: run_identity_rank / determinism / CLI / real fixture
# ---------------------------------------------------------------------------


REFERENCES_PATH = Path(
    "examples/arrangement/midnight_signal/observed/wi2_discrimination/wi2_references.yaml"
)


def test_run_identity_rank_against_real_fixture_references() -> None:
    """Loads the real WI2 references YAML (committed fixture) against a
    synthetic bundle mimicking WI1's most-frequent observed structure
    pattern, cross-checking the same anchors end-to-end through
    ``run_identity_rank``/``identity_rank_from_paths`` rather than the
    per-axis helpers directly."""

    bundle = _bundle(
        structure_labels=["intro", "chorus", "outro"],
        bpm=120.0,
        key="C",
        mode="major",
        spectral_centroid=2200.0,
    )
    from svp_rpe.roundtrip.identity_rank import load_reference_list

    references = load_reference_list(REFERENCES_PATH)
    report = run_identity_rank(bundle, references, references_dir=REFERENCES_PATH.parent)

    structure_axis = next(axis for axis in report.axes if axis.axis == "structure")
    by_ref = {entry.ref_id: entry.distance for entry in structure_axis.ranking}
    assert by_ref["canonical"] == 0.75
    assert by_ref["structure_destroyed"] == 0.5
    assert by_ref["other_work"] == 0.3333

    harmony_axis = next(axis for axis in report.axes if axis.axis == "harmony")
    not_observed_ids = {item.ref_id for item in harmony_axis.not_observed}
    assert not_observed_ids == {"structure_destroyed", "other_work"}

    excluded = {item.domain for item in report.excluded_domains}
    assert excluded == {"melody", "lyrics", "clap"}

    _assert_no_outcome_keys(report.model_dump(mode="json"))


def test_run_identity_rank_is_deterministic(tmp_path: Path) -> None:
    bundle = _bundle(structure_labels=["intro", "chorus", "outro"])
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(bundle.model_dump()), encoding="utf-8")

    first = identity_rank_from_paths(bundle_path, REFERENCES_PATH)
    second = identity_rank_from_paths(bundle_path, REFERENCES_PATH)

    assert first == second
    first_json = json.dumps(first.model_dump(mode="json"), sort_keys=False)
    second_json = json.dumps(second.model_dump(mode="json"), sort_keys=False)
    assert first_json == second_json


def test_render_identity_rank_text_has_axis_tables() -> None:
    bundle = _bundle(structure_labels=["intro", "chorus", "outro"])
    from svp_rpe.roundtrip.identity_rank import load_reference_list

    references = load_reference_list(REFERENCES_PATH)
    report = run_identity_rank(bundle, references, references_dir=REFERENCES_PATH.parent)

    text = render_identity_rank_text(report)

    assert "## structure" in text
    assert "## harmony" in text
    assert "## key" in text
    assert "## bpm" in text
    assert "## brightness" in text
    assert "excluded domains" in text
    assert "melody" in text


def test_identity_rank_cli_outputs_json(tmp_path: Path) -> None:
    bundle = _bundle(structure_labels=["intro", "chorus", "outro"])
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(bundle.model_dump()), encoding="utf-8")
    output_path = tmp_path / "rank.json"

    result = runner.invoke(
        app,
        [
            "identity-rank",
            str(bundle_path),
            "--references",
            str(REFERENCES_PATH),
            "--format",
            "json",
            "-o",
            str(output_path),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "identity-rank-report/0.1"
    assert set(payload["reference_ids"]) == {
        "canonical",
        "structure_destroyed",
        "harmony_decoy",
        "other_work",
    }
    _assert_no_outcome_keys(payload)


def test_reference_list_rejects_duplicate_ref_ids() -> None:
    with pytest.raises(Exception):
        ReferenceList.model_validate(
            {
                "schema_version": "wi2-references/0.1",
                "references": [
                    {"ref_id": "a", "score_path": "x.yaml"},
                    {"ref_id": "a", "score_path": "y.yaml"},
                ],
            }
        )


def test_reference_entry_extra_keys_are_rejected() -> None:
    with pytest.raises(Exception):
        ReferenceEntry.model_validate({"ref_id": "a", "score_path": "x.yaml", "unknown": 1})
