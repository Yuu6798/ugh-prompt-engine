"""Fixity helpers for CompositionScore physical fields."""
from __future__ import annotations

from typing import Any

from svp_rpe.compose.models import CompositionScore, FixityState, PhysicalLayer

TODO_SENTINEL_PREFIX = "TODO(transcribe):"
PHYSICAL_FIXITY_FIELDS = tuple(PhysicalLayer.model_fields)


def field_fixity(score: CompositionScore) -> dict[str, FixityState]:
    """Return explicit or TODO-derived fixity for physical CompositionScore fields."""

    if score.fixity is not None:
        return dict(score.fixity)
    return {
        field: _derived_fixity(getattr(score.physical, field))
        for field in PHYSICAL_FIXITY_FIELDS
    }


def _derived_fixity(value: Any) -> FixityState:
    return "unlocked" if _is_todo(value) else "locked"


def _is_todo(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(TODO_SENTINEL_PREFIX)
