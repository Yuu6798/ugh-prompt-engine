"""Composition Score conversion into semantic CI target specifications."""
from __future__ import annotations

import re

from svp_rpe.compose.models import CompositionScore, StructureSection
from svp_rpe.semantic_ci import TargetSVP


def composition_to_target_svp(score: CompositionScore) -> TargetSVP:
    """Convert an author-facing CompositionScore into a semantic CI TargetSVP."""
    physical = score.physical
    return TargetSVP(
        id=_slugify(score.meta.title),
        core=score.semantic.core,
        grv=_grv_values(score),
        delta_e_profile=score.semantic.delta_e.overall,
        avoid=score.semantic.avoid,
        metric_targets={
            "bpm": physical.bpm,
            "key": physical.key,
            "time_signature": physical.time_signature,
            "active_rate_target": physical.active_rate_target,
            "valley_depth_target": physical.valley_depth_target,
            "brightness": physical.brightness,
            "stereo_width": physical.stereo_width,
        },
        notes=[_section_note(section) for section in score.structure],
    )


def _grv_values(score: CompositionScore) -> list[str]:
    values = [score.semantic.grv.primary, score.semantic.grv.secondary]
    return [value for value in values if value]


def _section_note(section: StructureSection) -> str:
    return (
        f"{section.section}({section.bars}bars): "
        f"role={section.role} | physical={section.physical}"
    )


def _slugify(value: str) -> str:
    slug = value.strip().lower()
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "composition-score"
