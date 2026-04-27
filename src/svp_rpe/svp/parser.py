"""svp/parser.py — Parse external SVP files (YAML or text) into ParsedSVP."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

from svp_rpe.eval.diff_models import ParsedSVP


def parse_svp_yaml(data: dict) -> ParsedSVP:
    """Parse a dict (from YAML) into ParsedSVP."""
    analysis = _as_mapping(data.get("analysis_rpe"))
    gen = _as_mapping(data.get("svp_for_generation"))
    minimal = _as_mapping(data.get("minimal_svp"))
    lineage = _as_mapping(data.get("data_lineage"))
    raw_source_artifact = lineage.get("source_artifact")
    source_artifact = (
        dict(raw_source_artifact)
        if isinstance(raw_source_artifact, Mapping)
        else None
    )
    if source_artifact is None and lineage.get("source_audio"):
        source_artifact = {
            "type": "audio",
            "path": lineage["source_audio"],
            "metadata": {"legacy_field": "source_audio"},
        }

    return ParsedSVP(
        domain=_as_domain(data.get("domain")),
        source_artifact=source_artifact,
        por_core=analysis.get("por_core", minimal.get("c", "")),
        por_surface=analysis.get("por_surface", []),
        grv_primary=analysis.get("grv_primary", ""),
        grv_anchors=[],
        delta_e_profile=minimal.get("de", ""),
        bpm=analysis.get("bpm"),
        key=analysis.get("key"),
        mode=analysis.get("mode"),
        duration_sec=analysis.get("duration_sec"),
        constraints=gen.get("constraints", []),
        style_tags=gen.get("style_tags", []),
        instrumentation_notes=_extract_instrumentation_notes(gen),
        raw_text=str(data),
    )


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_domain(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else "music"


def _extract_instrumentation_notes(gen: Mapping[str, Any]) -> list[str]:
    if "instrumentation_notes" in gen:
        return _as_notes(gen["instrumentation_notes"])

    hints = _as_mapping(gen.get("generation_hints"))
    notes = (
        _as_notes(hints["instrumentation_notes"])
        if "instrumentation_notes" in hints
        else _as_notes(hints.get("instrumentation_summary"))
    )
    notes.extend(_as_notes(hints.get("production_notes")))
    return notes


def _as_notes(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _extract_field(text: str, pattern: str) -> str:
    """Extract a field value from markdown-style text."""
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _extract_float(text: str, pattern: str) -> Optional[float]:
    """Extract a float from text."""
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def parse_svp_text(text: str) -> ParsedSVP:
    """Parse a text/markdown SVP into ParsedSVP."""
    por_core = _extract_field(text, r"(?:Core|por_core)[:\s]*(.+)")
    por_surface_raw = _extract_field(text, r"(?:Surface|por_surface)[:\s]*(.+)")
    por_surface = [s.strip() for s in por_surface_raw.split(",") if s.strip()]

    grv_primary = _extract_field(text, r"(?:Gravity|grv_primary|grv)[:\s]*(.+)")
    delta_e = _extract_field(text, r"(?:ΔE|delta_e|Energy)[:\s]*(.+)")

    bpm = _extract_float(text, r"(?:BPM|Tempo)[:\s]*~?(\d+\.?\d*)")
    key_raw = _extract_field(text, r"Key[:\s]*(.+)")
    key = key_raw.split()[0] if key_raw else None
    mode = key_raw.split()[1] if key_raw and len(key_raw.split()) > 1 else None

    duration = _extract_float(text, r"Duration[:\s]*(\d+\.?\d*)")

    # Extract constraints from bullet points
    constraints = re.findall(r"[-•]\s*(.+)", text)
    style_tags = re.findall(r"#(\w+)", text)

    return ParsedSVP(
        domain="music",
        source_artifact=None,
        por_core=por_core,
        por_surface=por_surface,
        grv_primary=grv_primary,
        grv_anchors=[],
        delta_e_profile=delta_e,
        bpm=bpm,
        key=key,
        mode=mode,
        duration_sec=duration,
        constraints=constraints[:10],
        style_tags=style_tags[:10],
        instrumentation_notes=[],
        raw_text=text,
    )


def load_svp(path: str) -> ParsedSVP:
    """Load and parse an SVP file (YAML or text)."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"SVP file not found: {p}")

    content = p.read_text(encoding="utf-8")

    if p.suffix in (".yaml", ".yml"):
        data = yaml.safe_load(content)
        if isinstance(data, dict):
            return parse_svp_yaml(data)

    return parse_svp_text(content)
