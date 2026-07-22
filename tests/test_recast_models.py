"""RecastProject schema (recast-project/0.1) のモデルテスト（PR1）。"""
from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from svp_rpe.recast import RecastProject


def _base_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "recast-project/0.1",
        "project": {"id": "demo-project", "builds_root": "builds"},
        "work": {"score": "composition_score.yaml", "identity_manifest": "identity.yaml"},
        "variants": {"edm": {"arrangement": "arrangements/edm.yaml"}},
        "backends": {
            "suno": {
                "capability_profile": "suno",
                "invocation": "manual",
                "invocation_mode": "prompt_only",
            }
        },
        "policy": {"capability_mode": "advisory"},
        "observation": {"enabled": False, "anchors": []},
    }
    payload.update(overrides)
    return payload


# --- happy path ----------------------------------------------------------------


def test_minimal_valid_project_round_trips() -> None:
    project = RecastProject.model_validate(_base_payload())

    assert project.schema_version == "recast-project/0.1"
    assert project.project.id == "demo-project"
    assert project.work.score == "composition_score.yaml"
    assert project.variants["edm"].arrangement == "arrangements/edm.yaml"
    assert project.backends["suno"].capability_profile == "suno"
    # RecastPolicy デフォルト値
    assert project.policy.require_author_fields_resolved is True
    assert project.policy.require_verified_package is True


def test_backend_mode_overrides_optional_field() -> None:
    payload = _base_payload()
    payload["backends"]["suno"]["mode_overrides"] = "overrides/suno.yaml"

    project = RecastProject.model_validate(payload)

    assert project.backends["suno"].mode_overrides == "overrides/suno.yaml"


# --- unknown key rejection (top-level + nested) --------------------------------


def test_unknown_top_level_key_rejected() -> None:
    payload = _base_payload(unexpected_top_level="nope")

    with pytest.raises(ValidationError):
        RecastProject.model_validate(payload)


def test_unknown_nested_project_key_rejected() -> None:
    payload = _base_payload()
    payload["project"]["unexpected"] = "nope"

    with pytest.raises(ValidationError):
        RecastProject.model_validate(payload)


def test_unknown_nested_backend_key_rejected() -> None:
    payload = _base_payload()
    payload["backends"]["suno"]["unexpected"] = "nope"

    with pytest.raises(ValidationError):
        RecastProject.model_validate(payload)


# --- schema_version ---------------------------------------------------------


def test_unknown_schema_version_rejected() -> None:
    payload = _base_payload(**{"schema_version": "recast-project/9.9"})

    with pytest.raises(ValidationError):
        RecastProject.model_validate(payload)


# --- variants / backends non-empty ---------------------------------------------


def test_empty_variants_rejected() -> None:
    payload = _base_payload(variants={})

    with pytest.raises(ValidationError):
        RecastProject.model_validate(payload)


def test_empty_backends_rejected() -> None:
    payload = _base_payload(backends={})

    with pytest.raises(ValidationError):
        RecastProject.model_validate(payload)


def test_invalid_variant_key_slug_rejected() -> None:
    payload = _base_payload(variants={"Not A Slug": {"arrangement": "arrangements/edm.yaml"}})

    with pytest.raises(ValidationError):
        RecastProject.model_validate(payload)


def test_invalid_backend_key_slug_rejected() -> None:
    payload = _base_payload(
        backends={
            "Suno!": {
                "capability_profile": "suno",
                "invocation": "manual",
                "invocation_mode": "prompt_only",
            }
        }
    )

    with pytest.raises(ValidationError):
        RecastProject.model_validate(payload)


def test_invalid_project_id_slug_rejected() -> None:
    payload = _base_payload(project={"id": "Not A Slug", "builds_root": "builds"})

    with pytest.raises(ValidationError):
        RecastProject.model_validate(payload)


# --- observation.anchors duplicates --------------------------------------------


def test_duplicate_observation_anchors_rejected() -> None:
    payload = _base_payload(observation={"enabled": True, "anchors": ["lyrics", "lyrics"]})

    with pytest.raises(ValidationError):
        RecastProject.model_validate(payload)


def test_unique_observation_anchors_accepted() -> None:
    payload = _base_payload(observation={"enabled": True, "anchors": ["lyrics", "melody"]})

    project = RecastProject.model_validate(payload)

    assert project.observation.anchors == ["lyrics", "melody"]


# --- invocation / invocation_mode literals -------------------------------------


def test_invalid_invocation_kind_rejected() -> None:
    payload = _base_payload()
    payload["backends"]["suno"]["invocation"] = "cloud"

    with pytest.raises(ValidationError):
        RecastProject.model_validate(payload)


def test_invalid_invocation_mode_rejected() -> None:
    payload = _base_payload()
    payload["backends"]["suno"]["invocation_mode"] = "remix"

    with pytest.raises(ValidationError):
        RecastProject.model_validate(payload)


def test_invalid_capability_mode_rejected() -> None:
    payload = _base_payload(policy={"capability_mode": "loose"})

    with pytest.raises(ValidationError):
        RecastProject.model_validate(payload)
