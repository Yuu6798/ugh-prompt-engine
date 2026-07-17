"""`ArrangementBundleDescriptor` reader-only model unit tests (PR #184 review
round 9). `compile_arrangement` never uses this model — it keeps building
`arrangement_bundle.json` as a plain dict, so publish-side bytes are
unaffected; this model exists purely so the builds-root blessing path
(`cli.py:_check_existing_builds_root_publication`) can fully schema-validate
an existing on-disk descriptor.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from svp_rpe.arrange.bundle import ArrangementBundleDescriptor, CONTENT_DIGEST_BASIS

FIXTURE_DIR = Path("examples/arrangement/midnight_signal/expected")


def _valid_bundle_dict() -> dict:
    return {
        "schema_version": "arrangement-bundle/0.2",
        "arrangement_id": "arr-test",
        "source_score": {"path": "score.yaml", "sha256": "a" * 64},
        "arrangement_spec": {"path": "spec.yaml", "sha256": "b" * 64},
        "changes": [
            {
                "path": "physical.bpm",
                "before": 120,
                "after": 128,
                "preservation_mode": "free",
            }
        ],
        "outputs": {
            "derived_score": {"path": "derived_score.yaml", "sha256": "c" * 64},
            "arrangement_diff": {"path": "arrangement_diff.json", "sha256": "d" * 64},
        },
        "content_digest": "e" * 64,
        "content_digest_basis": CONTENT_DIGEST_BASIS,
    }


def test_valid_bundle_dict_validates() -> None:
    descriptor = ArrangementBundleDescriptor.model_validate(_valid_bundle_dict())
    assert descriptor.schema_version == "arrangement-bundle/0.2"
    assert descriptor.arrangement_id == "arr-test"
    assert descriptor.outputs["derived_score"].path == "derived_score.yaml"
    assert descriptor.content_digest_basis == CONTENT_DIGEST_BASIS


@pytest.mark.parametrize("variant", ["edm", "jazz"])
def test_committed_example_bundle_validates_as_is(variant: str) -> None:
    """The committed `examples/arrangement/midnight_signal/expected/{variant}/
    arrangement_bundle.json` fixtures must validate unmodified — a fixture/model
    drift pin, in the same spirit as the config double-copy sync test."""
    data = json.loads(
        (FIXTURE_DIR / variant / "arrangement_bundle.json").read_text(encoding="utf-8")
    )
    descriptor = ArrangementBundleDescriptor.model_validate(data)
    assert descriptor.content_digest == data["content_digest"]


def test_unknown_top_level_key_is_rejected() -> None:
    data = _valid_bundle_dict()
    data["unexpected"] = "value"
    with pytest.raises(ValidationError):
        ArrangementBundleDescriptor.model_validate(data)


def test_wrong_schema_version_is_rejected() -> None:
    data = _valid_bundle_dict()
    data["schema_version"] = "arrangement-bundle/9.9"
    with pytest.raises(ValidationError):
        ArrangementBundleDescriptor.model_validate(data)


def test_wrong_content_digest_basis_is_rejected() -> None:
    data = _valid_bundle_dict()
    data["content_digest_basis"] = "some-other-basis/v1"
    with pytest.raises(ValidationError):
        ArrangementBundleDescriptor.model_validate(data)


def test_non_object_source_score_is_rejected() -> None:
    data = _valid_bundle_dict()
    data["source_score"] = "corrupt"
    with pytest.raises(ValidationError):
        ArrangementBundleDescriptor.model_validate(data)


def test_non_hex_sha256_is_rejected() -> None:
    data = _valid_bundle_dict()
    data["source_score"]["sha256"] = "not-a-valid-sha256"
    with pytest.raises(ValidationError):
        ArrangementBundleDescriptor.model_validate(data)
