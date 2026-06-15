"""Roundtrip corpus batch runner."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from svp_rpe.roundtrip.compare import values_match
from svp_rpe.roundtrip.manifest import (
    SendForm,
    TakeLayer,
    RoundtripManifest,
    RoundtripTake,
    classify_take,
    resolve_audio_path,
)
from svp_rpe.rpe.extractor import extract_rpe_from_file
from svp_rpe.transcribe import draft_score


class CorpusFieldComparison(BaseModel):
    """One numeric-knob field comparison in a corpus take."""

    model_config = ConfigDict(extra="forbid")

    field: str
    intent: Any
    measured: Any
    send_form: SendForm
    match: bool


class CorpusTakeResult(BaseModel):
    """Descriptive corpus result for one take."""

    model_config = ConfigDict(extra="forbid")

    id: str
    layer: TakeLayer
    regenerated: bool
    comparisons: list[CorpusFieldComparison] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CorpusBatchReport(BaseModel):
    """Roundtrip corpus batch report.

    This is a measurement table, not a gate. It intentionally carries field
    matches only as descriptive comparisons and has no verdict/pass/fail/loss.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    takes: list[CorpusTakeResult] = Field(default_factory=list)


def run_corpus_batch(
    manifest: RoundtripManifest,
    *,
    repo_root: str | Path | None = None,
) -> CorpusBatchReport:
    """Run or replay every take in a roundtrip corpus manifest."""

    return CorpusBatchReport(
        takes=[
            _run_take(take, repo_root=repo_root)
            for take in sorted(manifest.takes, key=lambda item: item.id)
        ]
    )


def render_corpus_batch_text(report: CorpusBatchReport) -> str:
    """Render a compact markdown table for CLI output."""

    lines = [
        "# Roundtrip Corpus",
        "",
        "| take | layer | regenerated | field | intent | measured | send_form | match |",
        "|---|---|---:|---|---|---|---|---:|",
    ]
    for take in report.takes:
        if not take.comparisons:
            lines.append(
                f"| {take.id} | {take.layer} | {take.regenerated} |  |  |  |  |  |"
            )
            continue
        for item in take.comparisons:
            lines.append(
                "| "
                f"{take.id} | "
                f"{take.layer} | "
                f"{take.regenerated} | "
                f"{item.field} | "
                f"{_format_value(item.intent)} | "
                f"{_format_value(item.measured)} | "
                f"{item.send_form} | "
                f"{item.match} |"
            )
    return "\n".join(lines) + "\n"


def _run_take(
    take: RoundtripTake,
    *,
    repo_root: str | Path | None,
) -> CorpusTakeResult:
    layer = classify_take(take, repo_root=repo_root)
    measured = (
        _regenerate_measured(take, repo_root=repo_root)
        if layer == "calibratable"
        else dict(take.measured or {})
    )
    return CorpusTakeResult(
        id=take.id,
        layer=layer,
        regenerated=layer == "calibratable",
        comparisons=_compare_numeric_knobs(take, measured),
        notes=list(take.notes),
    )


def _regenerate_measured(
    take: RoundtripTake,
    *,
    repo_root: str | Path | None,
) -> dict[str, Any]:
    audio_path = resolve_audio_path(take, repo_root=repo_root)
    if audio_path is None:
        return {}
    bundle = extract_rpe_from_file(str(audio_path))
    score = draft_score(bundle)
    physical = score.physical
    return {
        "bpm": physical.bpm,
        "key": physical.key,
        "time_signature": physical.time_signature,
        "active_rate_target": physical.active_rate_target,
        "valley_depth_target": physical.valley_depth_target,
        "brightness": physical.brightness,
        "stereo_width": physical.stereo_width,
    }


def _compare_numeric_knobs(
    take: RoundtripTake,
    measured: dict[str, Any],
) -> list[CorpusFieldComparison]:
    comparisons: list[CorpusFieldComparison] = []
    for field, intent in sorted(take.intent.items()):
        if intent.send_form != "numeric_knob":
            continue
        measured_value = measured.get(field)
        comparisons.append(
            CorpusFieldComparison(
                field=field,
                intent=intent.value,
                measured=measured_value,
                send_form=intent.send_form,
                match=values_match(field, intent.value, measured_value),
            )
        )
    return comparisons


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)
