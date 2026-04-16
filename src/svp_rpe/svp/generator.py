"""svp/generator.py — RPEBundle → SVPBundle deterministic conversion.

Transforms a complete RPE analysis into a structured prompt (SVP).
Deterministic: same RPEBundle → same SVPBundle.
"""
from __future__ import annotations

from svp_rpe.rpe.models import RPEBundle
from svp_rpe.svp.models import (
    AnalysisRPE,
    DataLineage,
    EvaluationCriteria,
    MinimalSVP,
    SVPBundle,
    SVPForGeneration,
)


def _build_data_lineage(bundle: RPEBundle) -> DataLineage:
    return DataLineage(source_audio=bundle.audio_file)


def _build_analysis_rpe(bundle: RPEBundle) -> AnalysisRPE:
    phys = bundle.physical
    sem = bundle.semantic

    section_labels = [s.label for s in phys.structure]
    structure_summary = f"{len(section_labels)} sections: {', '.join(section_labels)}"

    return AnalysisRPE(
        por_core=sem.por_core,
        por_surface=sem.por_surface,
        grv_primary=sem.grv_anchor.primary,
        bpm=phys.bpm,
        key=phys.key,
        mode=phys.mode,
        duration_sec=phys.duration_sec,
        structure_summary=structure_summary,
    )


def _build_svp_for_generation(bundle: RPEBundle) -> SVPForGeneration:
    sem = bundle.semantic
    phys = bundle.physical

    # Build structured prompt text
    lines = [
        f"Core character: {sem.por_core}",
        f"Gravity anchor: {sem.grv_anchor.primary}",
        f"Energy profile: {sem.delta_e_profile.transition_type} "
        f"(intensity {sem.delta_e_profile.intensity:.2f})",
    ]
    if phys.bpm:
        lines.append(f"Tempo: ~{phys.bpm:.0f} BPM")
    if phys.key:
        lines.append(f"Key: {phys.key} {phys.mode or ''}")

    prompt_text = "\n".join(lines)

    # Constraints from physical characteristics
    constraints = []
    if phys.bpm:
        lo = max(0, phys.bpm - 10)
        hi = phys.bpm + 10
        constraints.append(f"BPM range: {lo:.0f}-{hi:.0f}")
    constraints.append(f"Active rate target: {phys.active_rate:.0%}")
    constraints.append(f"Crest factor target: ~{phys.crest_factor:.1f}")
    if phys.stereo_profile:
        constraints.append(f"Stereo width: {phys.stereo_profile.width:.0%}")

    # Style tags from semantic surface
    style_tags = list(sem.por_surface[:5])
    style_tags.extend(sem.cultural_context[:2])

    tempo_range = f"{phys.bpm - 10:.0f}-{phys.bpm + 10:.0f}" if phys.bpm else None
    key_suggestion = f"{phys.key} {phys.mode}" if phys.key else None

    return SVPForGeneration(
        prompt_text=prompt_text,
        constraints=constraints,
        style_tags=style_tags,
        tempo_range=tempo_range,
        key_suggestion=key_suggestion,
    )


def _build_evaluation_criteria(bundle: RPEBundle) -> EvaluationCriteria:
    sem = bundle.semantic
    phys = bundle.physical

    physical_checks = [
        f"RMS within ±20% of {phys.rms_mean:.3f}",
        f"Active rate within ±10% of {phys.active_rate:.0%}",
    ]
    if phys.bpm:
        physical_checks.append(f"BPM within ±10 of {phys.bpm:.0f}")
    if phys.key:
        physical_checks.append(f"Key matches {phys.key} {phys.mode or ''}")

    return EvaluationCriteria(
        por_check=f"Maintains core character: {sem.por_core}",
        grv_check=f"Preserves gravity anchor: {sem.grv_anchor.primary}",
        delta_e_check=f"Energy follows {sem.delta_e_profile.transition_type} pattern",
        physical_checks=physical_checks,
    )


def _build_minimal_svp(bundle: RPEBundle) -> MinimalSVP:
    sem = bundle.semantic
    phys = bundle.physical

    constraints = []
    if phys.bpm:
        constraints.append(f"bpm~{phys.bpm:.0f}")
    if phys.key:
        constraints.append(f"key={phys.key}")
    constraints.append(f"energy={sem.delta_e_profile.transition_type}")

    return MinimalSVP(
        c=sem.por_core,
        g=constraints,
        de=f"{sem.delta_e_profile.transition_type} ({sem.delta_e_profile.intensity:.2f})",
    )


def generate_svp(bundle: RPEBundle) -> SVPBundle:
    """Convert RPEBundle to SVPBundle.

    Deterministic: same RPEBundle → same SVPBundle.
    """
    return SVPBundle(
        data_lineage=_build_data_lineage(bundle),
        analysis_rpe=_build_analysis_rpe(bundle),
        svp_for_generation=_build_svp_for_generation(bundle),
        evaluation_criteria=_build_evaluation_criteria(bundle),
        minimal_svp=_build_minimal_svp(bundle),
    )
