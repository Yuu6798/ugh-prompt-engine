"""PRACTICE Alignment再凍結のcontract/manifest境界テスト。"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
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
