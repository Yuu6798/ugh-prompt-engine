"""PRACTICE Alignment再凍結のcontract/manifest境界テスト。"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import types
from pathlib import Path

import pytest

_RUN_DIR = Path(__file__).resolve().parent.parent
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

import run9_schema as m  # noqa: E402


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("path", "validator", "pin_name"),
    (
        (
            m.PRACTICE_ALIGNMENT_SPEC_PATH,
            m.validate_practice_alignment_spec_manifest,
            "practice_alignment_spec_sha",
        ),
        (
            m.PRACTICE_ACTOR_INPUT_MANIFEST_PATH,
            m.validate_practice_actor_input_manifest,
            "practice_actor_input_manifest_sha",
        ),
        (
            m.PRACTICE_AUDIT_ANNOTATION_MANIFEST_PATH,
            m.validate_practice_audit_annotation_manifest,
            "practice_audit_annotation_manifest_sha",
        ),
    ),
)
def test_practice_alignment_manifests_validate_and_match_contract(
    path: Path, validator, pin_name: str
) -> None:
    data = _load(path)
    validator(data)
    contract = m.load_run9_contract_from_yaml_path(m.RUN9_CONTRACT_YAML_PATH)
    assert contract.pin_field(pin_name)["status"] == "PINNED"
    assert contract.pin_field(pin_name)["value"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_practice_alignment_spec_is_closed_world() -> None:
    data = _load(m.PRACTICE_ALIGNMENT_SPEC_PATH)
    data["algorithm"]["seed"] = 0
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m.validate_practice_alignment_spec_manifest(data)


def test_practice_alignment_spec_pins_implementation_bytes() -> None:
    data = _load(m.PRACTICE_ALIGNMENT_SPEC_PATH)
    repo_root = _RUN_DIR.parent.parent.parent
    implementation = repo_root / data["algorithm"]["implementation"]
    assert data["algorithm"]["implementation_sha256"] == hashlib.sha256(
        implementation.read_bytes()
    ).hexdigest()


def test_alignment_spec_loader_executes_the_verified_source_buffer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale ambient import cannot become the pinned executable authority."""
    contract = m.load_run9_contract_from_yaml_path(m.RUN9_CONTRACT_YAML_PATH)
    data = _load(m.PRACTICE_ALIGNMENT_SPEC_PATH)
    repo_root = _RUN_DIR.parent.parent.parent
    implementation = (
        repo_root / data["algorithm"]["implementation"]
    ).resolve()
    expected_bytes = implementation.read_bytes()
    original_read_bytes = Path.read_bytes
    implementation_reads = 0

    def read_once_for_execution(path: Path) -> bytes:
        nonlocal implementation_reads
        if path.resolve() == implementation:
            implementation_reads += 1
            if implementation_reads > 1:
                return b"tampered after verified read"
        return original_read_bytes(path)

    stale = types.ModuleType("practice_alignment")
    stale.align_wav_to_projection = lambda *args, **kwargs: "stale"
    monkeypatch.setitem(sys.modules, "practice_alignment", stale)
    monkeypatch.setattr(Path, "read_bytes", read_once_for_execution)

    loaded = m.load_pinned_practice_alignment_spec(contract)

    assert implementation_reads == 1
    assert loaded.implementation_bytes == expected_bytes
    assert loaded.implementation_sha256 == data["algorithm"][
        "implementation_sha256"
    ]
    assert loaded.executable_module is not stale
    assert loaded.executable_module.sha256_bytes(b"verified") == hashlib.sha256(
        b"verified"
    ).hexdigest()


def test_actor_manifest_contains_only_wav_and_projection_references() -> None:
    data = _load(m.PRACTICE_ACTOR_INPUT_MANIFEST_PATH)
    assert len(data["entries"]) == 70
    assert all(set(entry) == {"song_id", "wav", "score_projection"} for entry in data["entries"])
    assert all(".lab" not in json.dumps(entry) for entry in data["entries"])
    assert data["input_boundary"]["lab_allowed"] is False
    assert data["input_boundary"]["teacher_boundary_allowed"] is False


def test_actor_manifest_rejects_lab_reference() -> None:
    data = copy.deepcopy(_load(m.PRACTICE_ACTOR_INPUT_MANIFEST_PATH))
    data["entries"][0]["wav"]["relative_path"] = "pjs063/pjs063.lab"
    with pytest.raises(m.Run9ValidationError, match="relative .wav path"):
        m.validate_practice_actor_input_manifest(data)


def test_audit_manifest_requires_post_freeze_attestation() -> None:
    contract = m.load_run9_contract_from_yaml_path(m.RUN9_CONTRACT_YAML_PATH)
    with pytest.raises(m.Run9ValidationError, match="freeze attestation"):
        m.load_pinned_practice_audit_annotation_manifest(contract)
    data = m.load_pinned_practice_audit_annotation_manifest(
        contract, r_practice_frozen=True
    )
    assert data["access_policy"]["mode"] == "POST_FREEZE_AUDIT_ONLY"


def test_learning_binding_search_excludes_lab_bearing_manifests() -> None:
    data = _load(m.LEARNING_DATA_BINDING_MANIFEST_PATH)
    m.validate_learning_data_binding_manifest(data)
    practice = data["branch_usage"]["practice"]
    assert practice["uses"] == [
        "practice_alignment_spec_sha",
        "practice_actor_input_manifest_sha",
    ]
    assert "pjs_consumed_inputs_manifest_sha" in practice["excludes_during_search"]
    assert "practice_audit_annotation_manifest_sha" in practice["excludes_during_search"]
    assert practice["lab_allowed"] is False


def test_actor_loader_returns_the_exact_verified_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A consumer never has to reopen mutable actor paths after hash preflight."""
    wav_bytes = b"RIFF-verification-snapshot"
    projection = {
        "mora_order": [0],
        "mora_count": 1,
        "nominal_duration_ratio": [1.0],
        "phrase_grouping": [0],
        "lyrics_phoneme_sequence": [
            {"lyric": "あ", "phoneme_sequence": []},
        ],
        "nominal_pitch": [60],
    }
    projection_bytes = (
        json.dumps(
            projection,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    wav_path = tmp_path / "pjs001" / "pjs001_song.wav"
    projection_path = tmp_path / "harness_work" / "pjs001.json"
    wav_path.parent.mkdir(parents=True)
    projection_path.parent.mkdir(parents=True)
    wav_path.write_bytes(wav_bytes)
    projection_path.write_bytes(projection_bytes)
    manifest = {
        "entries": [
            {
                "song_id": "pjs001",
                "wav": {
                    "relative_path": "pjs001/pjs001_song.wav",
                    "sha256": hashlib.sha256(wav_bytes).hexdigest(),
                },
                "score_projection": {
                    "relative_path": "harness_work/pjs001.json",
                    "sha256": hashlib.sha256(projection_bytes).hexdigest(),
                },
            }
        ]
    }
    monkeypatch.setattr(
        m,
        "_load_pinned_practice_actor_input_manifest_metadata",
        lambda *args, **kwargs: manifest,
    )

    loaded = m.load_pinned_practice_actor_input_manifest(
        object(), artifact_root=tmp_path
    )
    verified = loaded.entries[0]

    # A concurrent regeneration after preflight cannot change the bytes consumed
    # from the returned snapshot.
    wav_path.write_bytes(b"changed after verification")
    projection_path.write_text("{}\n", encoding="utf-8")
    assert verified.wav_bytes == wav_bytes
    assert verified.score_projection_bytes == projection_bytes
    assert verified.score_projection == projection
    assert hashlib.sha256(verified.wav_bytes).hexdigest() == manifest["entries"][0][
        "wav"
    ]["sha256"]
    assert hashlib.sha256(verified.score_projection_bytes).hexdigest() == manifest[
        "entries"
    ][0]["score_projection"]["sha256"]
