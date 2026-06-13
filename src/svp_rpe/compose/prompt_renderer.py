"""Deterministic prompt rendering for Composition Score documents."""
from __future__ import annotations

from dataclasses import dataclass

from svp_rpe.compose.models import CompositionScore, GeneratedPrompt


@dataclass(frozen=True)
class _PromptSegment:
    token: str
    text: str
    order: int


class ExternalPromptAdapter:
    """Render a CompositionScore for external text-to-music generators."""

    backend = "external"

    def render(self, score: CompositionScore, max_chars: int | None = None) -> GeneratedPrompt:
        limit = max(0, score.rendering.prompt_max_chars if max_chars is None else max_chars)
        segments = _segments_for(score)
        kept = list(segments)
        dropped_elements: list[str] = []

        while _join_segments(kept) and len(_join_segments(kept)) > limit:
            segment = _lowest_priority_segment(kept, score.rendering.priority)
            kept.remove(segment)
            dropped_elements.append(segment.token)

        return GeneratedPrompt(
            backend="external",
            text=_join_segments(kept),
            tags=_tags_for(score),
            negative_tags=list(score.semantic.avoid),
            dropped_elements=dropped_elements,
        )


def _segments_for(score: CompositionScore) -> list[_PromptSegment]:
    segments: list[_PromptSegment] = []

    def add(token: str, text: str) -> None:
        if text:
            segments.append(_PromptSegment(token=token, text=text, order=len(segments)))

    grv_values = [value for value in [score.semantic.grv.primary, score.semantic.grv.secondary] if value]
    if grv_values:
        add("semantic.grv", f"{' / '.join(grv_values)} track.")

    add("semantic.core", f"{score.physical.brightness.capitalize()}, {score.semantic.core} atmosphere.")
    add("physical.bpm", _bpm_text(score.physical.bpm))
    add("physical.key", f"{score.physical.key}.")

    for section in score.structure:
        add(
            "structure",
            f"{section.section}: {section.physical}; role={section.role}.",
        )

    add(
        "physical.optional",
        (
            f"Brightness {score.physical.brightness}; {score.physical.stereo_width} stereo; "
            f"active rate {score.physical.active_rate_target}; "
            f"valley depth {score.physical.valley_depth_target}."
        ),
    )

    if score.semantic.avoid:
        add("semantic.avoid", f"Avoid: {'; '.join(score.semantic.avoid)}.")

    return _ordered_segments(segments, score.rendering.priority)


def _ordered_segments(
    segments: list[_PromptSegment],
    priority: list[str],
) -> list[_PromptSegment]:
    priority_index = {token: index for index, token in enumerate(priority)}
    unlisted_index = len(priority)
    return sorted(
        segments,
        key=lambda segment: (priority_index.get(segment.token, unlisted_index), segment.order),
    )


def _lowest_priority_segment(
    segments: list[_PromptSegment],
    priority: list[str],
) -> _PromptSegment:
    priority_index = {token: index for index, token in enumerate(priority)}
    unlisted_index = len(priority)
    return max(
        segments,
        key=lambda segment: (priority_index.get(segment.token, unlisted_index), segment.order),
    )


def _join_segments(segments: list[_PromptSegment]) -> str:
    return " ".join(segment.text for segment in segments)


def _bpm_text(bpm: int | str) -> str:
    if isinstance(bpm, int) and not isinstance(bpm, bool):
        return f"{bpm} BPM."
    return f"{bpm}."


def _tags_for(score: CompositionScore) -> list[str]:
    return [
        score.semantic.grv.primary,
        score.semantic.grv.secondary,
        score.physical.brightness,
        f"{score.physical.stereo_width}_stereo",
    ]
