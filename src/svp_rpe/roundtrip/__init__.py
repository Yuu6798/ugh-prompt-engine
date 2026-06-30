"""Roundtrip preservation diagnostics."""
from __future__ import annotations

from .adherence import (
    ScoreAdherenceReport,
    TightFieldAdherence,
    render_score_adherence_text,
    run_score_adherence,
    score_adherence,
)
from .corpus_batch import (
    CorpusBatchReport,
    CorpusFieldComparison,
    CorpusTakeResult,
    render_corpus_batch_text,
    run_corpus_batch,
)
from .diagnose import GripRecord, diagnose_roundtrip, load_grip_map
from .harness import run_roundtrip
from .manifest import (
    FieldIntent,
    RoundtripManifest,
    RoundtripTake,
    classify_take,
    load_manifest,
)
from .models import RoundtripField, RoundtripReport
from .render import render_roundtrip_text

__all__ = [
    "CorpusBatchReport",
    "CorpusFieldComparison",
    "CorpusTakeResult",
    "FieldIntent",
    "GripRecord",
    "RoundtripManifest",
    "RoundtripTake",
    "RoundtripField",
    "RoundtripReport",
    "ScoreAdherenceReport",
    "TightFieldAdherence",
    "classify_take",
    "diagnose_roundtrip",
    "load_grip_map",
    "load_manifest",
    "render_corpus_batch_text",
    "render_roundtrip_text",
    "render_score_adherence_text",
    "run_corpus_batch",
    "run_roundtrip",
    "run_score_adherence",
    "score_adherence",
]
