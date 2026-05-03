"""rpe/dynamics_summary.py — Track-level dynamics aggregates from novelty curve.

Reuses the existing multi-feature novelty curve (RMS / onset / spectral flux /
chroma change) and reduces it to a small, descriptive summary suitable for
cross-song comparison.
"""
from __future__ import annotations

import numpy as np

from svp_rpe.rpe.models import DynamicsSummary


def compute_dynamics_summary(novelty: np.ndarray) -> DynamicsSummary:
    """Aggregate a novelty curve into a track-level descriptor.

    Args:
        novelty: 1D novelty curve (typically from compute_novelty_curve).

    Returns:
        DynamicsSummary with peak / mean / std / event_count / temporal_balance.
        Empty or all-zero novelty returns a zero-filled summary with
        temporal_balance = 1.0 (the neutral / balanced value).
    """
    n = np.asarray(novelty, dtype=float)
    if n.size == 0:
        return DynamicsSummary(
            peak_novelty=0.0,
            mean_novelty=0.0,
            std_novelty=0.0,
            event_count=0,
            temporal_balance=1.0,
        )

    peak = float(np.max(n))
    mean = float(np.mean(n))
    std = float(np.std(n))

    threshold = mean + 0.5 * std
    if n.size >= 3 and threshold > 0.0:
        # Local maxima above threshold.
        prev = n[:-2]
        cur = n[1:-1]
        nxt = n[2:]
        peak_mask = (cur > prev) & (cur > nxt) & (cur > threshold)
        event_count = int(np.sum(peak_mask))
    else:
        event_count = 0

    half = n.size // 2
    if half > 0 and mean > 0.0:
        first_half_mean = float(np.mean(n[:half]))
        temporal_balance = first_half_mean / mean
    else:
        temporal_balance = 1.0

    return DynamicsSummary(
        peak_novelty=round(peak, 4),
        mean_novelty=round(mean, 4),
        std_novelty=round(std, 4),
        event_count=event_count,
        temporal_balance=round(temporal_balance, 4),
    )
