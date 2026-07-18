"""tests/test_comparison.py — Comparison, valley, structure, semantic tests."""
from __future__ import annotations

import pytest

from svp_rpe.eval.anchor_matcher import grv_anchor_match
from svp_rpe.eval.comparison import compare_rpe_vs_svp, compute_physical_diff, generate_action_hints
from svp_rpe.eval.delta_e_alignment import delta_e_profile_alignment
from svp_rpe.eval.diff_models import (
    ParsedSVP,
    PhysicalDiff,
    SemanticDiff,
)
from svp_rpe.eval.semantic_similarity import por_lexical_similarity
from svp_rpe.io.audio_loader import load_audio
from svp_rpe.rpe.extractor import extract_physical, extract_rpe
from svp_rpe.rpe.models import PhysicalRPE, SectionMarker, SpectralProfile
from svp_rpe.rpe.structure_labels import assign_labels
from svp_rpe.rpe.valley import compute_valley_depth, valley_rms_percentile, valley_section_ar
from svp_rpe.svp.parser import load_svp, parse_svp_text, parse_svp_yaml


# ---------------------------------------------------------------------------
# SVP parser tests
# ---------------------------------------------------------------------------


class TestSVPParser:
    def test_parse_yaml_dict(self):
        data = {
            "analysis_rpe": {"por_core": "energetic", "por_surface": ["bright"]},
            "svp_for_generation": {"constraints": ["bpm>120"], "style_tags": ["edm"]},
            "minimal_svp": {"c": "test", "g": [], "de": "gradual"},
        }
        parsed = parse_svp_yaml(data)
        assert parsed.por_core == "energetic"
        assert parsed.por_surface == ["bright"]

    def test_parse_yaml_ignores_non_mapping_generation_hints(self):
        data = {
            "analysis_rpe": {"por_core": "test"},
            "svp_for_generation": {"generation_hints": None},
            "minimal_svp": {"c": "test", "de": "flat"},
        }
        parsed = parse_svp_yaml(data)
        assert parsed.instrumentation_notes == []

        data["svp_for_generation"]["generation_hints"] = "not a mapping"
        parsed = parse_svp_yaml(data)
        assert parsed.instrumentation_notes == []

    def test_parse_yaml_respects_explicit_empty_instrumentation_notes(self):
        data = {
            "analysis_rpe": {"por_core": "test"},
            "svp_for_generation": {
                "instrumentation_notes": [],
                "generation_hints": {"instrumentation_summary": "synth bass"},
            },
            "minimal_svp": {"c": "test", "de": "flat"},
        }
        parsed = parse_svp_yaml(data)
        assert parsed.instrumentation_notes == []

    def test_parse_yaml_includes_generation_hint_production_notes(self):
        data = {
            "analysis_rpe": {"por_core": "test"},
            "svp_for_generation": {
                "generation_hints": {
                    "instrumentation_summary": "synth bass",
                    "production_notes": ["compressed mix", "wide stereo"],
                },
            },
            "minimal_svp": {"c": "test", "de": "flat"},
        }
        parsed = parse_svp_yaml(data)
        assert parsed.instrumentation_notes == [
            "synth bass",
            "compressed mix",
            "wide stereo",
        ]

    def test_parse_yaml_ignores_non_mapping_source_artifact(self):
        data = {
            "analysis_rpe": {"por_core": "test"},
            "data_lineage": {"source_artifact": "not a mapping"},
            "minimal_svp": {"c": "test", "de": "flat"},
        }
        parsed = parse_svp_yaml(data)
        assert parsed.source_artifact is None

        data["data_lineage"]["source_artifact"] = ["not", "a", "mapping"]
        parsed = parse_svp_yaml(data)
        assert parsed.source_artifact is None

    def test_parse_yaml_defaults_invalid_domain_to_music(self):
        data = {
            "domain": None,
            "analysis_rpe": {"por_core": "test"},
            "minimal_svp": {"c": "test", "de": "flat"},
        }
        parsed = parse_svp_yaml(data)
        assert parsed.domain == "music"

        data["domain"] = ["not", "a", "domain"]
        parsed = parse_svp_yaml(data)
        assert parsed.domain == "music"

    def test_parse_text(self):
        text = """
        Core: energetic driving track
        Gravity: bass-heavy
        BPM: 152
        Key: F# major
        ΔE: gradual_build (0.7)
        """
        parsed = parse_svp_text(text)
        assert parsed.por_core == "energetic driving track"
        assert parsed.grv_primary == "bass-heavy"
        assert parsed.bpm == 152.0

    def test_load_svp_yaml(self, tmp_path):
        import yaml
        data = {
            "analysis_rpe": {"por_core": "test"},
            "minimal_svp": {"c": "test", "de": "flat"},
        }
        p = tmp_path / "test.yaml"
        p.write_text(yaml.dump(data))
        parsed = load_svp(str(p))
        assert parsed.por_core == "test"

    def test_load_svp_text(self, tmp_path):
        p = tmp_path / "test.txt"
        p.write_text("Core: my track\nBPM: 120\nKey: C major")
        parsed = load_svp(str(p))
        assert parsed.por_core == "my track"
        assert parsed.bpm == 120.0

    def test_load_svp_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_svp("/nonexistent.yaml")


# ---------------------------------------------------------------------------
# Semantic similarity tests
# ---------------------------------------------------------------------------


class TestSemanticSimilarity:
    def test_identical_text(self):
        score = por_lexical_similarity("energetic driving track", "energetic driving track")
        assert score == 1.0

    def test_no_overlap(self):
        score = por_lexical_similarity("dark melancholic", "bright energetic")
        # synonym map may create overlap
        assert 0.0 <= score <= 1.0

    def test_empty_text(self):
        assert por_lexical_similarity("", "something") == 0.0
        assert por_lexical_similarity("something", "") == 0.0


class TestAnchorMatcher:
    def test_perfect_match(self):
        score = grv_anchor_match(
            primary_a="bass-heavy", primary_b="bass-heavy",
            bpm_a=120.0, bpm_b=120.0,
            key_a="C", key_b="C",
        )
        assert score == 1.0

    def test_partial_match(self):
        score = grv_anchor_match(
            primary_a="bass-heavy", primary_b="bright",
            bpm_a=120.0, bpm_b=150.0,
            key_a="C", key_b="D",
        )
        assert 0.0 <= score < 0.5

    def test_bpm_close_enough(self):
        score = grv_anchor_match(
            primary_a="bass", primary_b="bass",
            bpm_a=120.0, bpm_b=125.0,
            key_a=None, key_b=None,
        )
        assert score >= 0.8


class TestDeltaEAlignment:
    def test_same_type(self):
        assert delta_e_profile_alignment("gradual_build", "gradual_build") >= 0.9

    def test_compatible_types(self):
        score = delta_e_profile_alignment("gradual_build", "crescendo")
        assert score >= 0.5

    def test_incompatible_types(self):
        score = delta_e_profile_alignment("flat", "dramatic_contrast")
        assert score < 0.5


# ---------------------------------------------------------------------------
# Valley strategy tests
# ---------------------------------------------------------------------------


class TestValleyStrategy:
    def test_rms_percentile(self, sine_wave_mono):
        audio = load_audio(sine_wave_mono)
        val, diag = valley_rms_percentile(audio.y_mono, audio.sr)
        assert val >= 0.0
        assert diag["rms_p90"] >= diag["rms_p10"]

    def test_section_ar(self, sine_wave_mono):
        from svp_rpe.rpe.models import SectionMarker
        audio = load_audio(sine_wave_mono)
        sections = [SectionMarker(label="s1", start_sec=0.0, end_sec=1.5),
                    SectionMarker(label="s2", start_sec=1.5, end_sec=3.0)]
        val, diag = valley_section_ar(audio.y_mono, audio.sr, sections)
        assert val >= 0.0
        assert 0.0 <= diag["ar_main"] <= 1.0

    def test_hybrid_method(self, sine_wave_mono):
        from svp_rpe.rpe.models import SectionMarker
        audio = load_audio(sine_wave_mono)
        sections = [SectionMarker(label="s1", start_sec=0.0, end_sec=3.0)]
        val, diagnostics = compute_valley_depth(audio.y_mono, audio.sr, sections, method="hybrid")
        assert val >= 0.0
        assert diagnostics.method == "hybrid"
        assert diagnostics.confidence > 0

    def test_invalid_method_raises(self, sine_wave_mono):
        audio = load_audio(sine_wave_mono)
        with pytest.raises(ValueError, match="unknown valley method"):
            compute_valley_depth(audio.y_mono, audio.sr, [], method="bogus")

    def test_all_three_methods(self, sine_wave_mono):
        from svp_rpe.rpe.models import SectionMarker
        audio = load_audio(sine_wave_mono)
        sections = [SectionMarker(label="s1", start_sec=0.0, end_sec=3.0)]
        for method in ("rms_percentile", "section_ar", "hybrid"):
            val, diag = compute_valley_depth(
                audio.y_mono, audio.sr, sections, method=method
            )
            assert val >= 0.0
            assert diag.method == method


# ---------------------------------------------------------------------------
# Structure labels tests
# ---------------------------------------------------------------------------


class TestStructureLabels:
    def test_single_section(self):
        assert assign_labels([0.5], 1) == ["Full"]

    def test_two_sections(self):
        labels = assign_labels([0.3, 0.5], 2)
        assert labels[0] == "Intro"
        assert labels[-1] == "Outro"

    def test_multi_section_has_chorus(self):
        rms = [0.1, 0.3, 0.8, 0.5, 0.9, 0.2]
        labels = assign_labels(rms, 6)
        assert labels[0] == "Intro"
        assert labels[-1] == "Outro"
        assert "Chorus" in labels


# ---------------------------------------------------------------------------
# Comparison engine tests
# ---------------------------------------------------------------------------


class TestComparison:
    def test_compare_self(self, sine_wave_mono):
        audio = load_audio(sine_wave_mono)
        rpe = extract_rpe(audio)
        parsed = ParsedSVP(
            por_core=rpe.semantic.por_core,
            grv_primary=rpe.semantic.grv_anchor.primary,
            bpm=rpe.physical.bpm,
            key=rpe.physical.key,
            delta_e_profile=rpe.semantic.delta_e_profile.transition_type,
        )
        result = compare_rpe_vs_svp(rpe, parsed)
        assert result.mode == "self"
        assert 0.0 <= result.overall_score <= 1.0
        assert len(result.action_hints) >= 1

    def test_compare_returns_action_hints(self):
        sem = SemanticDiff(
            por_lexical_similarity=0.1, grv_anchor_match=0.2,
            delta_e_profile_alignment=0.3, instrumentation_context_alignment=0.1,
            overall=0.15,
        )
        phys = PhysicalDiff(
            valley_diff=-0.1, active_rate_diff=0.1,
            bpm_diff=15.0, key_match=False, overall=0.3,
        )
        hints = generate_action_hints(sem, phys)
        assert any("valley" in h.lower() or "Bridge" in h for h in hints)
        assert any("bpm" in h.lower() or "key" in h.lower() for h in hints)

    def test_action_hints_falls_back_when_diffs_are_small(self):
        sem = SemanticDiff(
            por_lexical_similarity=0.9,
            grv_anchor_match=0.9,
            delta_e_profile_alignment=0.9,
            instrumentation_context_alignment=0.9,
            overall=0.9,
        )
        phys = PhysicalDiff(key_match=True, overall=1.0)

        hints = generate_action_hints(sem, phys)

        assert hints == ["大きな差分なし — 現状維持または微調整"]


# ---------------------------------------------------------------------------
# compute_physical_diff: v2 scoring gated on both-sides valley_depth_method
# (Codex PR #188 P2 #4 — the extractor now populates v2 fields
# unconditionally, so gating solely on field presence made the legacy
# comparison path unreachable for fresh extractions).
# ---------------------------------------------------------------------------


def _physical(
    *,
    valley_depth_method: str,
    valley_depth: float = 0.18,
    valley_depth_legacy: float | None = None,
    valley_db: float | None = 12.0,
    fullness: float | None = 0.8,
    crest_factor_robust: float | None = 5.0,
    active_rate_v2: float | None = 0.9,
    active_rate: float = 0.7,
) -> PhysicalRPE:
    return PhysicalRPE(
        duration_sec=10.0,
        sample_rate=44100,
        structure=[SectionMarker(label="full", start_sec=0.0, end_sec=10.0)],
        rms_mean=0.2,
        peak_amplitude=0.8,
        crest_factor=4.0,
        active_rate=active_rate,
        valley_depth=valley_depth,
        valley_depth_legacy=valley_depth_legacy,
        valley_depth_method=valley_depth_method,
        valley_db=valley_db,
        fullness=fullness,
        crest_factor_robust=crest_factor_robust,
        active_rate_v2=active_rate_v2,
        thickness=1.0,
        spectral_centroid=2000.0,
        spectral_profile=SpectralProfile(
            centroid=2000.0, low_ratio=0.3, mid_ratio=0.5, high_ratio=0.2, brightness=0.2,
        ),
        onset_density=2.0,
    )


class TestComputePhysicalDiffValleyMethodGate:
    def test_both_sides_v2_scores_v2_diffs_and_fires_crest_hint(self) -> None:
        ref = _physical(
            valley_depth_method="v2", valley_depth=0.30, valley_db=12.0,
            crest_factor_robust=6.0,
        )
        cand = _physical(
            valley_depth_method="v2", valley_depth=0.35, valley_db=4.0,
            crest_factor_robust=4.0,
        )

        diff = compute_physical_diff(ref, cand)

        assert diff.valley_db_diff == -8.0
        assert diff.fullness_diff == 0.0
        assert diff.crest_diff == -2.0
        assert diff.active_rate_v2_diff == 0.0
        assert diff.details.get("crest_factor_robust_cand") == "4.0"

        hints = generate_action_hints(
            SemanticDiff(
                por_lexical_similarity=0.9, grv_anchor_match=0.9,
                delta_e_profile_alignment=0.9, instrumentation_context_alignment=0.9,
                overall=0.9,
            ),
            diff,
        )
        assert any("張り不足" in h for h in hints)

    def test_both_sides_legacy_hybrid_v2_diffs_stay_none_and_legacy_hint_fires(self) -> None:
        # Both sides carry v2 fields (as the extractor now always populates
        # them) but explicitly chose a legacy method — the gate must still
        # keep all four v2 diffs None so scoring/hints fall through to the
        # legacy path, reproducing pre-v2 compare behavior for
        # `--valley-method legacy_hybrid`.
        ref = _physical(valley_depth_method="legacy_hybrid", valley_depth=0.30)
        cand = _physical(valley_depth_method="legacy_hybrid", valley_depth=0.10)

        diff = compute_physical_diff(ref, cand)

        assert diff.valley_db_diff is None
        assert diff.fullness_diff is None
        assert diff.crest_diff is None
        assert diff.active_rate_v2_diff is None
        assert diff.details == {}
        assert diff.valley_diff == pytest.approx(-0.2)

        hints = generate_action_hints(
            SemanticDiff(
                por_lexical_similarity=0.9, grv_anchor_match=0.9,
                delta_e_profile_alignment=0.9, instrumentation_context_alignment=0.9,
                overall=0.9,
            ),
            diff,
        )
        assert any("Bridge/Verse" in h for h in hints)

    def test_mixed_methods_fall_back_to_legacy_diffs(self) -> None:
        ref = _physical(valley_depth_method="v2", valley_depth=0.30)
        cand = _physical(valley_depth_method="hybrid", valley_depth=0.10)

        diff = compute_physical_diff(ref, cand)

        assert diff.valley_db_diff is None
        assert diff.fullness_diff is None
        assert diff.crest_diff is None
        assert diff.active_rate_v2_diff is None
        assert diff.valley_diff == pytest.approx(-0.2)

    def test_valley_diff_uses_legacy_scale_not_raw_valley_depth_on_mixed_methods(self) -> None:
        # Codex PR #188 P2 #6: valley_diff must be computed from
        # legacy_valley_depth on BOTH sides, not phys.valley_depth directly —
        # otherwise a mixed ref(v2)/cand(legacy method) pair diffs a v2 norm
        # against a legacy linear value (two different scales), which is
        # exactly the bug this test pins.
        ref = _physical(
            valley_depth_method="v2", valley_depth=0.30, valley_depth_legacy=0.05,
        )
        cand = _physical(
            valley_depth_method="hybrid", valley_depth=0.10, valley_depth_legacy=None,
        )

        diff = compute_physical_diff(ref, cand)

        # legacy_valley_depth(cand)=0.10 (falls back to valley_depth, since
        # cand carries no explicit legacy field and its selected method IS
        # the legacy scale) minus legacy_valley_depth(ref)=0.05 (explicit
        # legacy field) = 0.05 — NOT cand.valley_depth - ref.valley_depth
        # = 0.10 - 0.30 = -0.20, which would be the old cross-scale bug.
        assert diff.valley_diff == pytest.approx(0.05)

        hints = generate_action_hints(
            SemanticDiff(
                por_lexical_similarity=0.9, grv_anchor_match=0.9,
                delta_e_profile_alignment=0.9, instrumentation_context_alignment=0.9,
                overall=0.9,
            ),
            diff,
        )
        # With the correct (positive, small) legacy-scale diff, the
        # low-valley "Bridge/Verse" hint must NOT fire (it requires
        # valley_diff < -0.05, which only the old buggy -0.20 would trigger).
        assert not any("Bridge/Verse" in h for h in hints)


# ---------------------------------------------------------------------------
# Extractor v2 integration tests
# ---------------------------------------------------------------------------


class TestExtractorV2:
    def test_extract_physical_returns_tuple(self, sine_wave_mono):
        audio = load_audio(sine_wave_mono)
        phys, valley_diag, section_features = extract_physical(audio)
        assert phys.duration_sec > 0
        assert valley_diag is not None
        # Default method switched hybrid -> v2 (Metrics v2 rollout).
        assert valley_diag.method == "v2"
        assert len(section_features) >= 1

    def test_section_labels_not_generic(self, sine_wave_mono):
        audio = load_audio(sine_wave_mono)
        phys, _, _ = extract_physical(audio)
        labels = [s.label for s in phys.structure]
        # At least one should NOT be section_XX format
        has_named = any(
            lbl in ("Intro", "Outro", "Chorus", "Verse", "Bridge", "Full")
            or not lbl.startswith("section_")
            for lbl in labels
        )
        assert has_named

    def test_valley_method_switchable(self, sine_wave_mono):
        audio = load_audio(sine_wave_mono)
        for method in ("rms_percentile", "section_ar", "hybrid"):
            phys, diag, _ = extract_physical(audio, valley_method=method)
            assert phys.valley_depth_method == method
            assert diag.method == method


# ---------------------------------------------------------------------------
# Batch runner tests
# ---------------------------------------------------------------------------


class TestBatchRunner:
    def test_batch_empty_dir(self, tmp_path):
        from svp_rpe.batch.runner import run_batch
        empty = tmp_path / "empty_dir"
        empty.mkdir()
        result = run_batch(str(empty))
        # No audio files → error key or total_files == 0
        if "error" in result:
            assert "no audio" in result["error"]
        else:
            assert result["total_files"] == 0

    def test_batch_with_audio(self, tmp_path):
        from svp_rpe.batch.runner import run_batch
        import soundfile as sf
        import numpy as np
        # Create a dedicated batch dir with one audio file
        batch_dir = tmp_path / "batch_input"
        batch_dir.mkdir()
        sr = 22050
        y = 0.5 * np.sin(2 * np.pi * 440 * np.linspace(0, 3, int(sr * 3))).astype(np.float32)
        sf.write(str(batch_dir / "test.wav"), y, sr)

        out_dir = tmp_path / "batch_out"
        result = run_batch(str(batch_dir), output_dir=str(out_dir))
        assert result["total_files"] == 1
        assert result["successful"] == 1
        assert (out_dir / "ranking.json").is_file()
        assert (out_dir / "summary.csv").is_file()

    def test_batch_forwards_stem_separation_options(self, monkeypatch, tmp_path):
        from types import SimpleNamespace
        import soundfile as sf
        import numpy as np
        import svp_rpe.batch.runner as batch_runner

        batch_dir = tmp_path / "batch_input"
        batch_dir.mkdir()
        sr = 22050
        y = np.zeros(sr, dtype=np.float32)
        sf.write(str(batch_dir / "test.wav"), y, sr)

        calls: list[dict] = []

        def fake_extract(path: str, **kwargs):
            calls.append({"path": path, **kwargs})
            return SimpleNamespace(physical=object())

        monkeypatch.setattr(batch_runner, "extract_rpe_from_file", fake_extract)
        monkeypatch.setattr(batch_runner, "generate_svp", lambda rpe: object())
        monkeypatch.setattr(
            batch_runner,
            "score_rpe",
            lambda physical, baseline: SimpleNamespace(overall=0.8, baseline_profile=baseline),
        )
        monkeypatch.setattr(batch_runner, "score_ugher", lambda rpe, svp: SimpleNamespace(overall=0.7))
        monkeypatch.setattr(
            batch_runner,
            "score_integrated",
            lambda ugher, rpe: SimpleNamespace(integrated_score=0.75),
        )

        result = batch_runner.run_batch(
            str(batch_dir),
            include_stems=True,
            separation_model="fake-model",
            separation_device="cuda",
        )

        assert result["successful"] == 1
        assert calls == [
            {
                "path": str(batch_dir / "test.wav"),
                "include_stems": True,
                "separation_model": "fake-model",
                "separation_device": "cuda",
            }
        ]

    def test_batch_discovery_matching(self):
        from pathlib import Path
        from svp_rpe.batch.discovery import match_audio_to_svp
        audios = [Path("track_a.wav"), Path("track_b.wav")]
        svps = [Path("track_a_design.yaml"), Path("other.yaml")]
        matches = match_audio_to_svp(audios, svps)
        assert len(matches) == 2
        assert len(matches[0][1]) == 1  # track_a matches track_a_design
        assert matches[0][1][0].name == "track_a_design.yaml"
