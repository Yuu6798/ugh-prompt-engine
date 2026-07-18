"""utils/clamp.py — shared float clamping helper.

`_clamp` had 8 independent copies across rpe/eval modules, split across two
call signatures that were behaviorally identical: a `(v, lo=0.0, hi=1.0)`
form and a `(v)`-only form hardcoding the same [0, 1] bounds. This mirrors
the `keys.py` consolidation (#142) — collapse the duplicated implementation
into a single source of truth, parameterized so both call sites keep working
unchanged.
"""
from __future__ import annotations


def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp `value` into the inclusive [lo, hi] range."""
    return max(lo, min(hi, value))
