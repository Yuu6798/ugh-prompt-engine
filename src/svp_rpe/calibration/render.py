"""Text renderers for genre calibration reports."""
from __future__ import annotations

from typing import Any

from svp_rpe.calibration.analyze import GenreCalibrationReport


def render_genre_report_text(report: GenreCalibrationReport) -> str:
    """Render a compact markdown report for CLI output."""

    lines = [
        "# Genre Calibration",
        "",
        f"- min samples per genre: {report.min_samples_per_genre}",
        f"- excluded samples: {[item.id for item in report.excluded_samples]}",
        "",
        "## Genres",
        "",
        "| genre | n | status | measured features |",
        "|---|---:|---|---:|",
    ]
    for genre, stats in report.genres.items():
        measured_features = sum(1 for item in stats.features.values() if item.count > 0)
        lines.append(
            f"| {genre} | {stats.sample_count} | {stats.status} | {measured_features} |"
        )

    lines.extend(
        [
            "",
            "## Threshold Candidates",
            "",
        ]
    )
    if not report.threshold_candidates:
        lines.append("No threshold candidates. Insufficient or overlapping data.")
        return "\n".join(lines) + "\n"

    lines.extend(
        [
            "| pair | feature | threshold | direction | d |",
            "|---|---|---:|---|---:|",
        ]
    )
    for item in report.threshold_candidates:
        lines.append(
            "| "
            f"{item.genre_a} / {item.genre_b} | "
            f"{item.feature} | "
            f"{_format_value(item.threshold)} | "
            f"{item.direction} | "
            f"{_format_value(item.d)} |"
        )
    return "\n".join(lines) + "\n"


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)
