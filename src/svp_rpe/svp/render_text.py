"""svp/render_text.py — SVPBundle → Markdown/TXT output."""
from __future__ import annotations

from svp_rpe.svp.models import SVPBundle


def render_text(bundle: SVPBundle) -> str:
    """Render SVPBundle as readable Markdown text."""
    lines = [
        "# SVP Report",
        "",
        "## Data Lineage",
        f"- Source: {bundle.data_lineage.source_audio}",
        f"- Method: {bundle.data_lineage.generation_method}",
        "",
        "## Analysis (RPE Summary)",
        f"- Core: {bundle.analysis_rpe.por_core}",
        f"- Surface: {', '.join(bundle.analysis_rpe.por_surface)}",
        f"- Gravity: {bundle.analysis_rpe.grv_primary}",
        f"- Duration: {bundle.analysis_rpe.duration_sec:.1f}s",
    ]
    if bundle.analysis_rpe.bpm:
        lines.append(f"- BPM: {bundle.analysis_rpe.bpm:.0f}")
    if bundle.analysis_rpe.key:
        lines.append(f"- Key: {bundle.analysis_rpe.key} {bundle.analysis_rpe.mode or ''}")

    lines.extend([
        "",
        "## Generation Prompt",
        "",
        bundle.svp_for_generation.prompt_text,
        "",
        "### Constraints",
    ])
    for c in bundle.svp_for_generation.constraints:
        lines.append(f"- {c}")

    lines.extend([
        "",
        "### Style Tags",
        ", ".join(f"#{t}" for t in bundle.svp_for_generation.style_tags),
        "",
        "## Evaluation Criteria",
        f"- PoR: {bundle.evaluation_criteria.por_check}",
        f"- GRV: {bundle.evaluation_criteria.grv_check}",
        f"- ΔE: {bundle.evaluation_criteria.delta_e_check}",
    ])
    for pc in bundle.evaluation_criteria.physical_checks:
        lines.append(f"- Physical: {pc}")

    lines.extend([
        "",
        "## Minimal SVP",
        f"- c: {bundle.minimal_svp.c}",
        f"- g: {bundle.minimal_svp.g}",
        f"- de: {bundle.minimal_svp.de}",
    ])

    return "\n".join(lines)
