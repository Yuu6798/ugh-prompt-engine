"""Markdown report rendering for semantic CI runs."""
from __future__ import annotations

import json
from typing import Any

from svp_rpe.semantic_ci.models import SemanticCIRun


def _escape_cell(value: str) -> str:
    return value.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").replace("|", "\\|")


def _format_value(value: Any) -> str:
    if value is None:
        return "(none)"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return _escape_cell(value)
    return _escape_cell(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _format_list(values: list[str]) -> str:
    if not values:
        return "(none)"
    return ", ".join(_escape_cell(value) for value in values)


def _short_hash(value: str) -> str:
    return value[:12]


def _append_signal_diff(lines: list[str], run: SemanticCIRun) -> None:
    diff = run.semantic_diff
    lines.extend(
        [
            "## Signal Diff",
            "",
            "| Category | Signals |",
            "|---|---|",
            f"| missing | {_format_list(diff.missing)} |",
            f"| preserved | {_format_list(diff.preserved)} |",
            f"| over_changed | {_format_list(diff.over_changed)} |",
            "",
        ]
    )


def _append_metric_diff(lines: list[str], run: SemanticCIRun) -> None:
    lines.extend(
        [
            "## Metric Diff",
            "",
            "| Metric | Expected | Observed | Tolerance | Diff | Passed |",
            "|---|---:|---:|---:|---:|:---:|",
        ]
    )
    for metric in run.semantic_diff.metric_diffs:
        lines.append(
            "| "
            f"{_escape_cell(metric.name)} | "
            f"{_format_value(metric.expected)} | "
            f"{_format_value(metric.observed)} | "
            f"{_format_value(metric.tolerance)} | "
            f"{_format_value(metric.diff)} | "
            f"{'yes' if metric.passed else 'no'} |"
        )
    if not run.semantic_diff.metric_diffs:
        lines.append("| (none) | (none) | (none) | (none) | (none) | yes |")
    lines.append("")


def _append_repair_plan(lines: list[str], run: SemanticCIRun) -> None:
    repair = run.repair_svp
    lines.extend(
        [
            "## Repair Plan",
            "",
            f"- Change budget: {repair.change_budget}",
            f"- Preserve: {_format_list(repair.preserve)}",
            f"- Restore: {_format_list(repair.restore)}",
            f"- Reduce: {_format_list(repair.reduce)}",
            f"- Lock: {_format_list(repair.lock)}",
            f"- Deferred restore: {_format_list(repair.deferred_restore)}",
            f"- Deferred reduce: {_format_list(repair.deferred_reduce)}",
            "",
            "| Order | Op | Signal | Applied |",
            "|---:|---|---|:---:|",
        ]
    )
    for index, action in enumerate(repair.repair_order, start=1):
        lines.append(
            f"| {index} | {action.op} | {_escape_cell(action.signal)} | "
            f"{'yes' if action.applied else 'no'} |"
        )
    if not repair.repair_order:
        lines.append("| 0 | (none) | (none) | yes |")
    lines.append("")


def _append_hash_trail(lines: list[str], run: SemanticCIRun) -> None:
    log = run.roundtrip_log
    lines.extend(
        [
            "## Hash Trail",
            "",
            "| Object | SHA-256 prefix |",
            "|---|---|",
            f"| target_svp | {_short_hash(log.target_svp_hash)} |",
            f"| expected_rpe | {_short_hash(log.expected_rpe_hash)} |",
            f"| observed_rpe | {_short_hash(log.observed_rpe_hash)} |",
            f"| semantic_diff | {_short_hash(log.semantic_diff_hash)} |",
            f"| repair_svp | {_short_hash(log.repair_svp_hash)} |",
            f"| final | {_short_hash(log.final_hash)} |",
            "",
        ]
    )


def render_markdown(run: SemanticCIRun) -> str:
    """Render a deterministic Markdown report for a semantic CI run."""

    diff = run.semantic_diff
    lines = [
        "# Semantic CI Report",
        "",
        "## Verdict",
        "",
        f"- Verdict: {diff.verdict}",
        f"- Loss: {diff.loss:.4f}",
        f"- Target SVP: {_escape_cell(run.target_svp.id)}",
        f"- Observed RPE: {_escape_cell(run.observed_rpe.id)}",
        "",
    ]
    _append_signal_diff(lines, run)
    _append_metric_diff(lines, run)
    _append_repair_plan(lines, run)
    _append_hash_trail(lines, run)
    return "\n".join(lines).rstrip() + "\n"
