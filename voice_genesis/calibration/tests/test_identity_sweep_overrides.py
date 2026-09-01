from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from voice_genesis.calibration.fixtures import axes
from voice_genesis.calibration.fixtures.generators import identity_sweep
from voice_genesis.calibration.fixtures.matrix import build_matrix


def _identity_rows_with_f0_override():
    rows = []
    for matrix_row in build_matrix():
        row = matrix_row.row
        if row.family != "IDENTITY_CAUSAL_SWEEP" or row.control_class is not None:
            continue
        founder_f0 = float(axes.IDENTITY_FOUNDERS[row.founder_id]["f0_hz"])
        if row.f0_hz != founder_f0:
            rows.append(row)
    return rows


def test_identity_effective_f0_tracks_canonical_row_overrides() -> None:
    """Confound/boundary F0 overrides must survive founder parameter expansion.

    The canonical matrix contains high-F0 interactions and G2/C5 boundary probes
    whose ``row.f0_hz`` intentionally differs from the founder bundle.  The
    synthesized baseline must use that row-level value; an F0-trait delta is then
    applied on top of it.
    """
    rows = _identity_rows_with_f0_override()
    assert rows, "expected frozen identity rows with row-level F0 overrides"

    for row in rows:
        effective_f0, _poles, _bandwidth, _tilt = identity_sweep._effective_params(row)
        expected = float(row.f0_hz)
        if row.trait == "F0":
            delta = row.delta or 0
            expected *= 2.0 ** (delta * 5.0 / 1200.0)
        assert effective_f0 == pytest.approx(expected, rel=1e-12, abs=1e-12)


def test_identity_render_changes_when_boundary_f0_override_is_removed() -> None:
    """Regression at the PCM boundary: the declared C5 probe must not render as
    the founder's original F0.

    Before the fix, ``identity_sweep._effective_params`` discarded ``row.f0_hz``;
    consequently replacing C5 with the founder F0 produced byte-identical PCM.
    """
    row = next(
        matrix_row.row
        for matrix_row in build_matrix()
        if matrix_row.row.family == "IDENTITY_CAUSAL_SWEEP"
        and matrix_row.row.block == "BOUNDARY"
        and matrix_row.row.control_class is None
        and matrix_row.row.f0_hz == axes.BOUNDARY_F0_HZ[1]
    )
    founder_f0 = float(axes.IDENTITY_FOUNDERS[row.founder_id]["f0_hz"])
    founder_baseline_row = replace(row, f0_hz=founder_f0)

    pcm_declared = identity_sweep.render(row, np.random.default_rng(12345))
    pcm_founder = identity_sweep.render(founder_baseline_row, np.random.default_rng(12345))

    assert not np.array_equal(pcm_declared, pcm_founder)
