"""svp/generator.py - RPEBundle to SVPBundle deterministic conversion.

Transforms a complete RPE analysis into a structured prompt (SVP).
Deterministic: same RPEBundle to same SVPBundle.
"""
from __future__ import annotations

from svp_rpe.rpe.models import RPEBundle
from svp_rpe.svp.domain_profile import DomainProfile, load_domain_profile
from svp_rpe.svp.models import (
    AnalysisRPE,
    DataLineage,
    EvaluationCriteria,
    MinimalSVP,
    SVPBundle,
    SVPForGeneration,
    SourceArtifact,
)


def _build_context(bundle: RPEBundle) -> dict:
    phys = bundle.physical
    sem = bundle.semantic
    context = {
        "source_path": bundle.audio_file,
        "por_core": sem.por_core,
        "por_surface": sem.por_surface,
        "grv_primary": sem.grv_anchor.primary,
        "duration_sec": phys.duration_sec,
        "sections": phys.structure,
        "bpm": phys.bpm,
        "key": phys.key,
        "mode": phys.mode,
        "rms_mean": phys.rms_mean,
        "active_rate": phys.active_rate,
        "valley_depth": phys.valley_depth,
        "thickness": phys.thickness,
        "spectral_centroid": phys.spectral_centroid,
        "low_ratio": phys.spectral_profile.low_ratio,
        "mid_ratio": phys.spectral_profile.mid_ratio,
        "high_ratio": phys.spectral_profile.high_ratio,
        "brightness": phys.spectral_profile.brightness,
        "delta_e_transition": sem.delta_e_profile.transition_type,
        "delta_e_intensity": sem.delta_e_profile.intensity,
        "delta_e_intensity_2dp": f"{sem.delta_e_profile.intensity:.2f}",
        "cultural_context": sem.cultural_context,
        "instrumentation_summary": sem.instrumentation_summary,
        "production_notes": sem.production_notes,
    }
    if phys.stereo_profile:
        context["stereo_width"] = phys.stereo_profile.width
        context["stereo_width_pct"] = f"{phys.stereo_profile.width:.0%}"
    if phys.bpm:
        context["tempo_range"] = f"{max(0, phys.bpm - 10):.0f}-{phys.bpm + 10:.0f}"
        context["bpm_rounded"] = f"{phys.bpm:.0f}"
    if phys.key:
        context["key_suggestion"] = f"{phys.key} {phys.mode or ''}".strip()
    context["active_rate_pct"] = f"{phys.active_rate:.0%}"
    context["crest_factor_1dp"] = f"{phys.crest_factor:.1f}"
    return context


def _build_data_lineage(bundle: RPEBundle, profile: DomainProfile) -> DataLineage:
    return DataLineage(
        source_artifact=SourceArtifact(
            type=profile.source_artifact_type,
            path=bundle.audio_file,
            metadata={
                "duration_sec": bundle.audio_duration_sec,
                "sample_rate": bundle.audio_sample_rate,
                "channels": bundle.audio_channels,
                "format": bundle.audio_format,
            },
        )
    )


def _build_analysis_rpe(bundle: RPEBundle, profile: DomainProfile, context: dict) -> AnalysisRPE:
    phys = bundle.physical
    sem = bundle.semantic

    return AnalysisRPE(
        por_core=sem.por_core,
        por_surface=context.get("por_surface", sem.por_surface),
        grv_primary=context.get("grv_primary", sem.grv_anchor.primary),
        bpm=phys.bpm,
        key=phys.key,
        mode=phys.mode,
        duration_sec=phys.duration_sec,
        structure_summary=profile.format_structure_summary(context),
        domain_features={
            "rms_mean": phys.rms_mean,
            "active_rate": phys.active_rate,
            "valley_depth": phys.valley_depth,
            "thickness": phys.thickness,
            "spectral_centroid": phys.spectral_centroid,
        },
    )


def _build_svp_for_generation(
    bundle: RPEBundle,
    profile: DomainProfile,
    context: dict,
) -> SVPForGeneration:
    sem = bundle.semantic
    por_surface = context.get("por_surface", sem.por_surface)

    return SVPForGeneration(
        prompt_text=profile.render_prompt(context),
        constraints=profile.render_constraints(context),
        style_tags=profile.build_style_tags(context, por_surface),
        tempo_range=context.get("tempo_range"),
        key_suggestion=context.get("key_suggestion"),
        generation_hints={
            "instrumentation_summary": sem.instrumentation_summary,
            "production_notes": sem.production_notes,
        },
    )


def _build_evaluation_criteria(profile: DomainProfile, context: dict) -> EvaluationCriteria:
    return EvaluationCriteria(
        por_check=profile.render_por_check(context),
        grv_check=profile.render_grv_check(context),
        delta_e_check=profile.render_delta_e_check(context),
        physical_checks=profile.render_physical_checks(context),
        metric_checks={
            "domain": profile.domain,
            "metrics": profile.diff_metric_names,
        },
    )


def _build_minimal_svp(bundle: RPEBundle, profile: DomainProfile, context: dict) -> MinimalSVP:
    return MinimalSVP(
        c=bundle.semantic.por_core,
        g=profile.render_minimal_constraints(context),
        de=profile.format_delta_e(context),
    )


def generate_svp(bundle: RPEBundle, *, domain: str = "music") -> SVPBundle:
    """Convert RPEBundle to SVPBundle.

    Deterministic: same RPEBundle to same SVPBundle.
    """
    profile = load_domain_profile(domain)
    context = _build_context(bundle)
    profile_por_surface = profile.select_por_surface(context)
    if profile_por_surface != profile.default_por_surface:
        context["por_surface"] = profile_por_surface
    profile_grv = profile.select_grv_primary(context)
    if profile_grv != profile.default_grv_primary or not context.get("grv_primary"):
        context["grv_primary"] = profile_grv
    return SVPBundle(
        domain=profile.domain,
        data_lineage=_build_data_lineage(bundle, profile),
        analysis_rpe=_build_analysis_rpe(bundle, profile, context),
        svp_for_generation=_build_svp_for_generation(bundle, profile, context),
        evaluation_criteria=_build_evaluation_criteria(profile, context),
        minimal_svp=_build_minimal_svp(bundle, profile, context),
    )
