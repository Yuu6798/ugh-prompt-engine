"""`recast/state.py` の永続化テスト（PR2）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from svp_rpe.recast.state import (
    RECAST_STATE_FILENAME,
    RecastStateFile,
    load_recast_state,
    record_state,
)


# --- load_recast_state -----------------------------------------------------


def test_load_recast_state_missing_file_returns_empty(tmp_path: Path) -> None:
    state_file = load_recast_state(tmp_path)

    assert state_file.schema_version == "recast-state/0.1"
    assert state_file.runs == {}


def test_load_recast_state_unknown_schema_version_raises(tmp_path: Path) -> None:
    (tmp_path / RECAST_STATE_FILENAME).write_text(
        json.dumps({"schema_version": "recast-state/9.9", "runs": {}}), encoding="utf-8"
    )

    with pytest.raises(ValidationError):
        load_recast_state(tmp_path)


def test_load_recast_state_unknown_top_level_key_raises(tmp_path: Path) -> None:
    (tmp_path / RECAST_STATE_FILENAME).write_text(
        json.dumps({"schema_version": "recast-state/0.1", "runs": {}, "extra": 1}),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_recast_state(tmp_path)


def test_load_recast_state_non_mapping_raises(tmp_path: Path) -> None:
    (tmp_path / RECAST_STATE_FILENAME).write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    with pytest.raises(ValueError):
        load_recast_state(tmp_path)


# --- record_state ------------------------------------------------------------


def test_record_state_creates_run_with_single_history_entry(tmp_path: Path) -> None:
    result = record_state(tmp_path, "edm", "suno", "authored", "first author pass")

    run = result.runs["edm@suno"]
    assert run.state == "authored"
    assert run.note == "first author pass"
    assert len(run.history) == 1
    assert run.history[0].state == "authored"
    assert run.history[0].note == "first author pass"

    persisted = load_recast_state(tmp_path)
    assert persisted.runs["edm@suno"].state == "authored"


def test_record_state_appends_history_on_transition(tmp_path: Path) -> None:
    record_state(tmp_path, "edm", "suno", "draft")
    record_state(tmp_path, "edm", "suno", "authored")
    result = record_state(tmp_path, "edm", "suno", "compiled")

    run = result.runs["edm@suno"]
    assert run.state == "compiled"
    assert [entry.state for entry in run.history] == ["draft", "authored", "compiled"]


def test_record_state_is_idempotent_for_identical_state_and_note(tmp_path: Path) -> None:
    first = record_state(tmp_path, "edm", "suno", "verified", "note-a")
    second = record_state(tmp_path, "edm", "suno", "verified", "note-a")

    run = second.runs["edm@suno"]
    assert len(run.history) == 1
    assert first.runs["edm@suno"].history == run.history


def test_record_state_with_different_note_is_not_idempotent(tmp_path: Path) -> None:
    record_state(tmp_path, "edm", "suno", "blocked_authoring", "reason A")
    result = record_state(tmp_path, "edm", "suno", "blocked_authoring", "reason B")

    run = result.runs["edm@suno"]
    assert len(run.history) == 2
    assert [entry.note for entry in run.history] == ["reason A", "reason B"]


def test_record_state_tracks_independent_variant_backend_keys(tmp_path: Path) -> None:
    record_state(tmp_path, "edm", "suno", "authored")
    result = record_state(tmp_path, "jazz", "musicgen", "draft")

    assert set(result.runs) == {"edm@suno", "jazz@musicgen"}
    assert result.runs["edm@suno"].state == "authored"
    assert result.runs["jazz@musicgen"].state == "draft"


def test_record_state_timestamps_are_not_used_for_identity(tmp_path: Path) -> None:
    """`updated_at` / `history[].at` は実時刻であり、同一性判定には使わない —
    同一 state+note の再呼び出しが history を伸ばさないことでこれを検証する
    （時刻が違っても history は不変のまま）。"""
    import time

    record_state(tmp_path, "edm", "suno", "verified", "same")
    time.sleep(0.01)
    result = record_state(tmp_path, "edm", "suno", "verified", "same")

    assert len(result.runs["edm@suno"].history) == 1


# --- atomic write ------------------------------------------------------------


def test_record_state_atomic_write_leaves_no_partial_file_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from svp_rpe.recast import state as state_module

    original_replace = os.replace

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("simulated failure during publish")

    monkeypatch.setattr(state_module.os, "replace", _boom)

    with pytest.raises(OSError):
        record_state(tmp_path, "edm", "suno", "authored")

    # No target file was ever published...
    assert not (tmp_path / RECAST_STATE_FILENAME).exists()
    # ...and the staging tempfile was cleaned up (no `.tmp` litter left behind).
    leftovers = list(tmp_path.glob(f"{RECAST_STATE_FILENAME}.*.tmp"))
    assert leftovers == []

    monkeypatch.setattr(state_module.os, "replace", original_replace)


def test_record_state_publishes_valid_json(tmp_path: Path) -> None:
    record_state(tmp_path, "edm", "suno", "authored", "note")

    raw = (tmp_path / RECAST_STATE_FILENAME).read_text(encoding="utf-8")
    assert raw.endswith("\n")
    data = json.loads(raw)
    RecastStateFile.model_validate(data)  # round-trips through the schema
