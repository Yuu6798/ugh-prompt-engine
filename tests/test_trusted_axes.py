"""tests/test_trusted_axes.py — L0a trusted-axis table derivation (D-L0a-3).

Drift guard: `derive_trusted_axes()` (a pure function over source instruments)
must exactly equal the frozen `config/authoring_trusted_axes_l0.yaml` — the
same "re-derive == frozen file" shape as M3's registry freeze-match tests.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from svp_rpe.authoring.trusted_axes import derive_trusted_axes
from svp_rpe.roundtrip.diagnose import load_grip_map

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_derive_matches_frozen_repo_config():
    frozen = yaml.safe_load(
        (REPO_ROOT / "config" / "authoring_trusted_axes_l0.yaml").read_text(encoding="utf-8")
    )
    assert derive_trusted_axes() == frozen


def test_derive_matches_frozen_packaged_config():
    frozen = yaml.safe_load(
        (REPO_ROOT / "src" / "svp_rpe" / "config" / "authoring_trusted_axes_l0.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert derive_trusted_axes() == frozen


def test_schema_version():
    result = derive_trusted_axes()
    assert result["schema_version"] == "authoring-trusted-axes/1.0"


def test_every_axis_has_a_source_instrument():
    result = derive_trusted_axes()
    for axis_name, axis in result["axes"].items():
        assert "source_instrument" in axis, f"{axis_name} missing source_instrument"
        assert axis["source_instrument"]


def test_dead_knobs_are_excluded():
    """K1 grip map classifies active_rate_target/valley_depth_target as
    `dead` — the performer's knob doesn't move the sensor, so the axis must
    not appear in the trusted table (source instrument can't back it)."""

    grip_map = load_grip_map()
    assert grip_map["active_rate_target"].classification == "dead"
    assert grip_map["valley_depth_target"].classification == "dead"

    axes = derive_trusted_axes()["axes"]
    assert "active_rate_target" not in axes
    assert "valley_depth_target" not in axes


def test_constant_sensor_blind_fields_are_excluded():
    axes = derive_trusted_axes()["axes"]
    assert "stereo_width" not in axes
    assert "time_signature" not in axes


def test_expected_physical_and_structure_axes_present():
    axes = derive_trusted_axes()["axes"]
    assert set(axes) == {"bpm", "key", "brightness", "structure"}


def test_brightness_has_dark_only_band_restriction():
    axes = derive_trusted_axes()["axes"]
    assert axes["brightness"]["band_restriction"] == {"trusted_values": ["dark"]}


def test_bpm_and_key_have_no_band_restriction():
    axes = derive_trusted_axes()["axes"]
    assert "band_restriction" not in axes["bpm"]
    assert "band_restriction" not in axes["key"]


def test_bpm_has_runtime_gate_documenting_conditional_trust():
    """§5's "count only if the pre-registered task's BPM falls within the
    trusted band, else out_of_band" rule must be readable from the table
    itself, not only from docstrings/docs — otherwise an auditor reading
    only the frozen YAML could misread bpm as unconditionally trusted, an
    asymmetry with brightness's in-table `band_restriction` (Fable review)."""

    axes = derive_trusted_axes()["axes"]
    gate = axes["bpm"]["runtime_gate"]
    assert isinstance(gate, str) and gate
    assert "out_of_band" in gate
    assert "llm_adapter_planning.md" in gate


def test_only_bpm_has_a_runtime_gate():
    axes = derive_trusted_axes()["axes"]
    assert "runtime_gate" not in axes["key"]
    assert "runtime_gate" not in axes["brightness"]
    assert "runtime_gate" not in axes["structure"]


def test_structure_source_instrument_references_ar4_observe():
    axes = derive_trusted_axes()["axes"]
    assert "observe" in axes["structure"]["source_instrument"]


def test_derive_is_pure_and_repeatable():
    assert derive_trusted_axes() == derive_trusted_axes()
