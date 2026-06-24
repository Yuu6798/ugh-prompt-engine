from __future__ import annotations

import hashlib
import json

import pytest

from svp_rpe.rpe.models import (
    DeltaEProfile,
    GrvAnchor,
    PhysicalRPE,
    RPEBundle,
    SectionMarker,
    SemanticRPE,
    SpectralProfile,
)
from svp_rpe.rpe.semantic_rules import generate_semantic
from svp_rpe.svp.generator import generate_svp
from svp_rpe.utils.config_loader import load_config


def _make_physical() -> PhysicalRPE:
    return PhysicalRPE(
        bpm=152.0,
        key="C",
        mode="major",
        duration_sec=90.0,
        sample_rate=44100,
        structure=[SectionMarker(label="Full", start_sec=0.0, end_sec=90.0)],
        rms_mean=0.3,
        peak_amplitude=0.9,
        crest_factor=3.0,
        active_rate=0.85,
        valley_depth=0.05,
        thickness=2.0,
        spectral_centroid=3200.0,
        spectral_profile=SpectralProfile(
            centroid=3200.0,
            low_ratio=0.1,
            mid_ratio=0.18,
            high_ratio=0.72,
            brightness=0.72,
        ),
        onset_density=4.5,
    )


def _make_bundle() -> RPEBundle:
    physical = _make_physical()
    semantic = generate_semantic(physical)
    return RPEBundle(
        physical=physical,
        semantic=semantic,
        audio_file="fixture.wav",
        audio_duration_sec=90.0,
        audio_sample_rate=44100,
        audio_channels=1,
        audio_format="wav",
    )


def _semantic_hash(physical: PhysicalRPE) -> str:
    semantic = generate_semantic(physical)
    payload = json.dumps(semantic.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_same_physical_rpe_produces_same_semantic_hash() -> None:
    physical = _make_physical()

    assert _semantic_hash(physical) == _semantic_hash(physical)


def test_emitted_labels_include_evidence_and_source_rule() -> None:
    semantic = generate_semantic(_make_physical())

    assert semantic.schema_version == "2.0"
    assert semantic.por_surface
    for label in semantic.por_surface:
        assert label.evidence
        assert label.source_rule


def test_low_confidence_hypothesis_does_not_enter_primary_style_tags() -> None:
    svp = generate_svp(_make_bundle())

    style_tags = svp.svp_for_generation.style_tags
    candidate_labels = [
        item["label"]
        for item in svp.svp_for_generation.generation_hints["candidate_context"]
    ]
    assert "energetic" not in style_tags
    assert "driving" not in style_tags
    assert "energetic" in candidate_labels
    assert "driving" in candidate_labels


def test_low_confidence_labels_do_not_enter_por_core() -> None:
    semantic = generate_semantic(_make_physical())

    assert "energetic" not in semantic.por_core
    assert "driving" not in semantic.por_core


def test_semantic_hypothesis_rules_have_multiple_conditions() -> None:
    config = load_config("semantic_rules")

    for rule in config["semantic_hypothesis"]:
        assert len(rule["condition"]) >= 2


def test_legacy_semantic_schema_fails_fast() -> None:
    with pytest.raises(ValueError, match="schema_version 1.0"):
        SemanticRPE.model_validate(
            {
                "schema_version": "1.0",
                "por_core": "legacy",
                "por_surface": ["legacy"],
                "grv_anchor": GrvAnchor(primary="legacy").model_dump(),
                "delta_e_profile": DeltaEProfile(
                    transition_type="flat",
                    intensity=0.2,
                    description="legacy",
                ).model_dump(),
                "cultural_context": ["general"],
                "instrumentation_summary": "legacy",
                "production_notes": ["legacy"],
                "confidence_notes": ["legacy"],
            }
        )


def _make_physical_genre(
    *,
    harmonic_ratio,
    spectral_centroid,
    low_ratio,
    mid_ratio,
    high_ratio,
    bpm,
    active_rate,
    valley_depth,
) -> PhysicalRPE:
    return PhysicalRPE(
        bpm=bpm,
        key="C",
        mode="major",
        duration_sec=90.0,
        sample_rate=44100,
        structure=[SectionMarker(label="Full", start_sec=0.0, end_sec=90.0)],
        rms_mean=0.3,
        peak_amplitude=0.9,
        crest_factor=3.0,
        active_rate=active_rate,
        valley_depth=valley_depth,
        thickness=2.0,
        spectral_centroid=spectral_centroid,
        spectral_profile=SpectralProfile(
            centroid=spectral_centroid,
            low_ratio=low_ratio,
            mid_ratio=mid_ratio,
            high_ratio=high_ratio,
            brightness=high_ratio,
        ),
        onset_density=4.5,
        harmonic_ratio=harmonic_ratio,
    )


def test_high_valley_material_gets_cinematic_context() -> None:
    # 旧ハードコード valley_depth>0.3 -> cinematic/orchestral を config 化後も温存。
    semantic = generate_semantic(
        _make_physical_genre(
            harmonic_ratio=None,
            spectral_centroid=1588.0,
            low_ratio=0.20,
            mid_ratio=0.30,
            high_ratio=0.20,
            bpm=100.0,
            active_rate=0.6,
            valley_depth=0.4,
        )
    )
    assert "cinematic/orchestral" in semantic.cultural_context


def test_bass_music_genre_inference() -> None:
    # low_ratio>0.4 -> bass-music を config 化後も温存（旧ハードコード等価）。
    semantic = generate_semantic(
        _make_physical_genre(
            harmonic_ratio=0.71,
            spectral_centroid=3735.0,
            low_ratio=0.42,
            mid_ratio=0.25,
            high_ratio=0.33,
            bpm=129.0,
            active_rate=0.98,
            valley_depth=0.07,
        )
    )
    assert semantic.cultural_context == ["bass-music"]
    assert semantic.instrumentation_summary == "Bass-heavy production with prominent low-end"


def test_genre_inference_backward_compatible_without_harmonic_ratio() -> None:
    # harmonic_ratio 欠落（旧 RPE）でもレガシー経路を温存（electronic/dance）。
    semantic = generate_semantic(
        _make_physical_genre(
            harmonic_ratio=None,
            spectral_centroid=3200.0,
            low_ratio=0.10,
            mid_ratio=0.18,
            high_ratio=0.72,
            bpm=152.0,
            active_rate=0.85,
            valley_depth=0.05,
        )
    )
    assert semantic.cultural_context == ["electronic/dance"]
    assert semantic.instrumentation_summary == "Bright production with emphasis on highs"


def test_cultural_context_falls_back_to_default() -> None:
    # どのルールにも当たらない素材は default(general) に落ちる。
    semantic = generate_semantic(
        _make_physical_genre(
            harmonic_ratio=0.5,
            spectral_centroid=1800.0,
            low_ratio=0.20,
            mid_ratio=0.30,
            high_ratio=0.20,
            bpm=110.0,
            active_rate=0.5,
            valley_depth=0.12,
        )
    )
    assert semantic.cultural_context == ["general"]


def test_genre_sections_backfilled_from_packaged_when_local_partial(monkeypatch) -> None:
    # stale/partial な local config が新セクションを欠いても、packaged 補完で
    # genre 推定が silent 退行しないこと（Codex P2 #99）。
    import svp_rpe.rpe.semantic_rules as sr

    full = load_config("semantic_rules")
    partial = {k: v for k, v in full.items() if k not in ("cultural_context", "instrumentation")}
    monkeypatch.setattr(sr, "load_config", lambda name: partial)

    semantic = sr.generate_semantic(_make_physical())  # bpm152/active_rate0.85
    assert semantic.cultural_context == ["electronic/dance"]
    assert semantic.instrumentation_summary != "Unknown instrumentation"


def test_genre_rules_preserve_strict_boundaries() -> None:
    # 旧ハードコードは厳密 > / <。knob 値ちょうど（bpm==140, active_rate==0.8, bpm==90,
    # low_ratio==0.4）では発火しないことを確認（_gt/_lt で境界等価を担保, Codex P2 #99）。
    boundary = generate_semantic(
        _make_physical_genre(
            harmonic_ratio=None,
            spectral_centroid=2000.0,
            low_ratio=0.40,   # ちょうど 0.4 -> bass-music(>0.4) は発火しない
            mid_ratio=0.30,
            high_ratio=0.20,
            bpm=140.0,        # ちょうど 140 -> electronic/dance(>140) は発火しない
            active_rate=0.80,  # ちょうど 0.8
            valley_depth=0.30,  # ちょうど 0.3 -> cinematic(>0.3) は発火しない
        )
    )
    assert boundary.cultural_context == ["general"]

    just_below_ambient = generate_semantic(
        _make_physical_genre(
            harmonic_ratio=None,
            spectral_centroid=2000.0,
            low_ratio=0.20,
            mid_ratio=0.30,
            high_ratio=0.20,
            bpm=90.0,         # ちょうど 90 -> ambient/downtempo(<90) は発火しない
            active_rate=0.5,
            valley_depth=0.10,
        )
    )
    assert just_below_ambient.cultural_context == ["general"]
