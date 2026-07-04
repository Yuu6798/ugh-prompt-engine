"""tests/test_calibrate_semantic_axes.py — calibrate_semantic_axes.py smoke test.

Runs the calibration harness's `main()` against the REAL committed fixtures
in `examples/learned/clap/` (small JSON, in-repo; no audio, no fresh audio
inference — embedding-space only). `laion_clap` itself is faked via a
deterministic hash-seeded text embedder (see `_hash_vector`) so no real
learned-model dependency is needed and no `slow` marker is required.
"""
from __future__ import annotations

import hashlib
import math
from typing import Any, Optional

import pytest
import yaml

import scripts.calibrate_semantic_axes as calibrate

_EMBED_DIM = 512

# The real fixtures under examples/learned/clap/ are pinned to this
# checkpoint sha256 / amodel (see model.checkpoint_sha256 / model.amodel in
# {lyrics_vocal_contrast,musicgen_k2_contrast}_fixture.json). Tests that
# exercise the happy path must satisfy this pin, so `_checkpoint_sha256` is
# monkeypatched to return it for the fake checkpoint path used below.
_PINNED_SHA256 = "fae3e9c087f2909c28a09dc31c8dfcdacbc42ba44c70e972b58c1bd1caf6dedd"
_PINNED_AMODEL = "HTSAT-base"


def _fake_checkpoint_sha256(checkpoint: Optional[str]) -> Optional[str]:
    return _PINNED_SHA256 if checkpoint else None


def _hash_vector(text: str, dim: int = _EMBED_DIM) -> list[float]:
    """Deterministic, distinct-per-text unit vector derived from a SHA-256 hash.

    Same text -> same vector, every run, every process (no RNG) — so
    `probe_determinism_same_run` reads `True` and the reproduction check is
    reproducible. Distinct texts hash to (generically) distinct vectors so
    per-axis `contrast_fit` values differ meaningfully.
    """
    values: list[float] = []
    counter = 0
    while len(values) < dim:
        digest = hashlib.sha256(f"{counter}:{text}".encode("utf-8")).digest()
        for i in range(0, len(digest) - 1, 2):
            if len(values) >= dim:
                break
            raw = int.from_bytes(digest[i : i + 2], "big")
            values.append(raw / 65535.0 - 0.5)
        counter += 1
    norm = math.sqrt(sum(v * v for v in values))
    if norm < 1e-12:
        return values
    return [v / norm for v in values]


def _fake_embed_texts(texts: list[str], model: Optional[Any] = None) -> list[list[float]]:
    return [_hash_vector(text) for text in texts]


def _fake_load_clap_model(checkpoint: Optional[str] = None, amodel: Optional[str] = None) -> Any:
    return {"checkpoint": checkpoint, "amodel": amodel}


def _install_fake_clap_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calibrate, "load_clap_model", _fake_load_clap_model)
    monkeypatch.setattr(calibrate, "embed_texts", _fake_embed_texts)
    monkeypatch.setattr(calibrate, "_checkpoint_sha256", _fake_checkpoint_sha256)


AXIS_NAMES = {"vocal_presence", "brightness", "energy", "acousticness", "warmth"}


def test_main_writes_structurally_complete_calibration(monkeypatch, tmp_path):
    _install_fake_clap_calls(monkeypatch)
    output_path = tmp_path / "calibration.yaml"

    exit_code = calibrate.main(
        [
            "--checkpoint",
            "music_audioset_epoch_15_esc_90.14.pt",
            "--amodel",
            "HTSAT-base",
            "--output",
            str(output_path),
            "--run-date",
            "2026-07-04",
        ]
    )

    assert exit_code == 0
    payload = yaml.safe_load(output_path.read_text(encoding="utf-8"))

    # Top-level keys.
    for key in (
        "schema_version",
        "kind",
        "run_date",
        "model",
        "axes_config_version",
        "probe_determinism_same_run",
        "readings",
        "reproduction_check",
        "stats",
    ):
        assert key in payload, key

    assert payload["schema_version"] == "1.0"
    assert payload["kind"] == "semantic_axes_calibration"
    assert payload["run_date"] == "2026-07-04"
    assert payload["probe_determinism_same_run"] is True

    model = payload["model"]
    assert model["name"] == "laion_clap"
    assert model["checkpoint"] == "music_audioset_epoch_15_esc_90.14.pt"
    assert model["amodel"] == "HTSAT-base"

    # readings: 6 + 32 sample_ids x 5 axes.
    readings = payload["readings"]
    assert set(readings.keys()) == {"lyrics_vocal_contrast", "musicgen_k2_contrast"}
    assert len(readings["lyrics_vocal_contrast"]) == 6
    assert len(readings["musicgen_k2_contrast"]) == 32
    for fixture_readings in readings.values():
        for row in fixture_readings.values():
            assert set(row.keys()) == AXIS_NAMES

    # reproduction_check: both fixtures, correct sample counts.
    reproduction_check = payload["reproduction_check"]
    assert reproduction_check["lyrics_vocal_contrast"]["n_samples"] == 6
    assert reproduction_check["musicgen_k2_contrast"]["n_samples"] == 32
    for info in reproduction_check.values():
        assert isinstance(info["n_exact_6dp"], int)
        assert isinstance(info["max_abs_deviation"], float)

    # stats: structurally complete.
    stats = payload["stats"]
    lyrics_stats = stats["lyrics_effect_vs_regen_noise"]
    lyrics_rows = sum(len(per_genre) for per_genre in lyrics_stats.values())
    assert lyrics_rows == 10  # 5 axes x 2 genres
    assert set(lyrics_stats.keys()) == AXIS_NAMES
    for per_genre in lyrics_stats.values():
        assert set(per_genre.keys()) == {"edm", "rock"}
        for row in per_genre.values():
            assert set(row.keys()) == {"effect", "regen_noise", "ratio", "exceeds_noise"}

    musicgen_stats = stats["musicgen_knob_cells"]
    musicgen_rows = sum(len(per_knob) for per_knob in musicgen_stats.values())
    assert musicgen_rows == 10  # 5 axes x 2 knobs
    assert set(musicgen_stats.keys()) == AXIS_NAMES
    for per_knob in musicgen_stats.values():
        assert set(per_knob.keys()) == {"bpm", "brightness"}
        for row in per_knob.values():
            assert set(row.keys()) == {
                "low_mean",
                "low_stdev",
                "high_mean",
                "high_stdev",
                "cohens_d",
            }

    pearson_r = stats["warmth_brightness_pearson_r"]
    assert -1.0 <= pearson_r <= 1.0

    census = stats["acousticness_sign_census"]
    assert census["total"] == 38
    assert 0 <= census["negative"] <= 38


def test_run_date_omitted_when_not_given(monkeypatch, tmp_path):
    _install_fake_clap_calls(monkeypatch)
    output_path = tmp_path / "calibration.yaml"

    exit_code = calibrate.main(
        [
            "--checkpoint",
            "ckpt.pt",
            "--amodel",
            "HTSAT-base",
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    payload = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    assert "run_date" not in payload


def test_running_twice_produces_byte_identical_files(monkeypatch, tmp_path):
    _install_fake_clap_calls(monkeypatch)
    output_path_1 = tmp_path / "calibration_1.yaml"
    output_path_2 = tmp_path / "calibration_2.yaml"

    args = lambda output: [  # noqa: E731
        "--checkpoint",
        "music_audioset_epoch_15_esc_90.14.pt",
        "--amodel",
        "HTSAT-base",
        "--output",
        str(output),
        "--run-date",
        "2026-07-04",
    ]

    assert calibrate.main(args(output_path_1)) == 0
    assert calibrate.main(args(output_path_2)) == 0

    assert output_path_1.read_bytes() == output_path_2.read_bytes()


def test_main_reports_install_hint_when_laion_clap_unavailable(monkeypatch, tmp_path, capsys):
    def _raise_unavailable(checkpoint=None, amodel=None):
        raise calibrate.LearnedModelUnavailable("laion_clap is not installed")

    monkeypatch.setattr(calibrate, "load_clap_model", _raise_unavailable)
    monkeypatch.setattr(calibrate, "_checkpoint_sha256", _fake_checkpoint_sha256)
    output_path = tmp_path / "calibration.yaml"

    exit_code = calibrate.main(
        [
            "--checkpoint",
            "music_audioset_epoch_15_esc_90.14.pt",
            "--amodel",
            _PINNED_AMODEL,
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 1
    assert "laion_clap" in capsys.readouterr().err
    assert not output_path.exists()


def test_build_calibration_axes_config_version_matches_real_config(monkeypatch, tmp_path):
    _install_fake_clap_calls(monkeypatch)

    payload = calibrate.build_calibration(
        checkpoint="music_audioset_epoch_15_esc_90.14.pt", amodel=_PINNED_AMODEL
    )

    assert payload["axes_config_version"] == "1.0"
    assert payload["model"]["checkpoint"] == "music_audioset_epoch_15_esc_90.14.pt"


def test_build_calibration_rejects_mismatched_checkpoint_sha(monkeypatch):
    monkeypatch.setattr(calibrate, "load_clap_model", _fake_load_clap_model)
    monkeypatch.setattr(calibrate, "embed_texts", _fake_embed_texts)
    monkeypatch.setattr(calibrate, "_checkpoint_sha256", lambda checkpoint: "deadbeef" * 8)

    with pytest.raises(ValueError, match="embedding-space mismatch"):
        calibrate.build_calibration(checkpoint="other_checkpoint.pt", amodel=_PINNED_AMODEL)


def test_main_rejects_mismatched_checkpoint_sha(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(calibrate, "load_clap_model", _fake_load_clap_model)
    monkeypatch.setattr(calibrate, "embed_texts", _fake_embed_texts)
    monkeypatch.setattr(calibrate, "_checkpoint_sha256", lambda checkpoint: "deadbeef" * 8)
    output_path = tmp_path / "calibration.yaml"

    exit_code = calibrate.main(
        [
            "--checkpoint",
            "other_checkpoint.pt",
            "--amodel",
            _PINNED_AMODEL,
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 1
    stderr = capsys.readouterr().err
    assert "embedding-space mismatch" in stderr
    assert _PINNED_SHA256 in stderr
    assert not output_path.exists()


def test_main_rejects_omitted_checkpoint(monkeypatch, tmp_path, capsys):
    _install_fake_clap_calls(monkeypatch)
    output_path = tmp_path / "calibration.yaml"

    exit_code = calibrate.main(
        [
            "--amodel",
            _PINNED_AMODEL,
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 1
    stderr = capsys.readouterr().err
    assert "embedding-space mismatch" in stderr
    assert _PINNED_SHA256 in stderr
    assert not output_path.exists()


def test_main_rejects_mismatched_amodel(monkeypatch, tmp_path, capsys):
    _install_fake_clap_calls(monkeypatch)
    output_path = tmp_path / "calibration.yaml"

    exit_code = calibrate.main(
        [
            "--checkpoint",
            "music_audioset_epoch_15_esc_90.14.pt",
            "--amodel",
            "HTSAT-tiny",
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 1
    stderr = capsys.readouterr().err
    assert "embedding-space mismatch" in stderr
    assert _PINNED_AMODEL in stderr
    assert not output_path.exists()
