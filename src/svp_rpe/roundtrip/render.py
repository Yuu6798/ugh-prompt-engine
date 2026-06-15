"""Roundtrip report renderers."""
from __future__ import annotations

from typing import Any

from svp_rpe.roundtrip.models import RoundtripReport


def render_roundtrip_text(report: RoundtripReport) -> str:
    """Render a compact markdown table for CLI output."""

    lines = [
        f"# Roundtrip Preservation - {report.source_id}",
        "",
        "| field | source | transcribed | diagnosis | grip | sensor | note |",
        "|---|---|---|---|---:|---|---|",
    ]
    for item in report.fields:
        lines.append(
            "| "
            f"{item.field} | "
            f"{_format_value(item.source_value)} | "
            f"{_format_value(item.transcribed_value)} | "
            f"{item.diagnosis} | "
            f"{_format_value(item.grip)} | "
            f"{item.sensor} | "
            f"{item.note or ''} |"
        )
    return "\n".join(lines) + "\n"


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)
