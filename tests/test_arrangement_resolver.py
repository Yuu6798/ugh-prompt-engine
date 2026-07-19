"""ArrangementSpec / resolve_arrangement の決定論的 Resolver テスト (AR1-1)。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from svp_rpe.arrange import (
    ArrangementConflictError,
    ArrangementPolicyError,
    ArrangementSpec,
    resolve_arrangement,
)
from svp_rpe.compose import CompositionScore, load_composition_score

SAMPLE_PATH = Path("examples/composition/midnight_signal/composition_score.yaml")


def _source() -> CompositionScore:
    return load_composition_score(SAMPLE_PATH)


def _spec(
    target: dict[str, Any],
    score_fields: dict[str, str],
    *,
    arrangement_id: str = "arr-test",
    version: str = "1",
) -> ArrangementSpec:
    return ArrangementSpec.model_validate(
        {
            "meta": {"id": arrangement_id, "version": version},
            "target": target,
            "preservation": {"score_fields": score_fields},
        }
    )


# --- empty target -------------------------------------------------------------


def test_empty_target_produces_no_changes_and_identical_derived_score() -> None:
    source = _source()
    spec = _spec(target={}, score_fields={})

    resolution = resolve_arrangement(source, spec)

    assert resolution.changes == []
    assert resolution.derived_score.model_dump(mode="json") == source.model_dump(mode="json")


# --- scalar single field override --------------------------------------------


def test_scalar_field_override_elastic() -> None:
    source = _source()
    spec = _spec(
        target={"physical": {"bpm": 140}},
        score_fields={"physical.bpm": "elastic"},
    )

    resolution = resolve_arrangement(source, spec)

    assert resolution.derived_score.physical.bpm == 140
    assert len(resolution.changes) == 1
    change = resolution.changes[0]
    assert change.path == "physical.bpm"
    assert change.before == 128
    assert change.after == 140
    assert change.preservation_mode == "elastic"


# --- nested semantic.grv.primary override ------------------------------------


def test_nested_semantic_grv_primary_override() -> None:
    source = _source()
    spec = _spec(
        target={"semantic": {"grv": {"primary": "trap"}}},
        score_fields={"semantic.grv.primary": "free"},
    )

    resolution = resolve_arrangement(source, spec)

    assert resolution.derived_score.semantic.grv.primary == "trap"
    assert resolution.derived_score.semantic.grv.secondary == "ambient"
    assert len(resolution.changes) == 1
    assert resolution.changes[0].path == "semantic.grv.primary"
    assert resolution.changes[0].before == "deep_house"
    assert resolution.changes[0].after == "trap"


# --- multiple field override + stable ordering -------------------------------


def test_multiple_field_override_is_sorted_by_canonical_path_order() -> None:
    source = _source()
    spec = _spec(
        target={
            "physical": {"key": "D minor", "bpm": 140},
            "semantic": {"core": "new core"},
        },
        score_fields={
            "physical.key": "elastic",
            "physical.bpm": "elastic",
            "semantic.core": "free",
        },
    )

    resolution = resolve_arrangement(source, spec)

    paths = [change.path for change in resolution.changes]
    # canonical order: semantic.core < physical.bpm < physical.key
    assert paths == ["semantic.core", "physical.bpm", "physical.key"]


# --- list atomic override -----------------------------------------------------


def test_list_atomic_override_replaces_whole_value() -> None:
    source = _source()
    new_avoid = ["over-produced pop", "screaming vocals"]
    spec = _spec(
        target={"semantic": {"avoid": new_avoid}},
        score_fields={"semantic.avoid": "elastic"},
    )

    resolution = resolve_arrangement(source, spec)

    assert resolution.derived_score.semantic.avoid == new_avoid
    assert len(resolution.changes) == 1
    change = resolution.changes[0]
    assert change.path == "semantic.avoid"
    assert change.before == source.semantic.avoid
    assert change.after == new_avoid


def test_rendering_priority_atomic_override() -> None:
    source = _source()
    new_priority = ["physical.bpm", "semantic.core"]
    spec = _spec(
        target={"rendering": {"priority": new_priority}},
        score_fields={"rendering.priority": "free"},
    )

    resolution = resolve_arrangement(source, spec)

    assert resolution.derived_score.rendering.priority == new_priority
    assert resolution.changes[0].path == "rendering.priority"


# --- events=None -> chord_progression added -----------------------------------


def test_events_none_source_gains_chord_progression() -> None:
    source = _source()
    assert source.events is None

    spec = _spec(
        target={
            "events": {
                "chord_progression": [
                    {"root": "C", "quality": "minor"},
                    {"root": "G", "quality": "major"},
                ]
            }
        },
        score_fields={"events.chord_progression": "free"},
    )

    resolution = resolve_arrangement(source, spec)

    assert resolution.derived_score.events is not None
    assert [c.root for c in resolution.derived_score.events.chord_progression] == ["C", "G"]
    change = resolution.changes[0]
    assert change.path == "events.chord_progression"
    assert change.before is None
    assert change.after == [
        {"root": "C", "quality": "minor"},
        {"root": "G", "quality": "major"},
    ]


# --- hard preservation ---------------------------------------------------------


def test_hard_same_value_is_allowed_with_no_change_record() -> None:
    source = _source()
    spec = _spec(
        target={"physical": {"bpm": 128}},
        score_fields={"physical.bpm": "hard"},
    )

    resolution = resolve_arrangement(source, spec)

    assert resolution.changes == []
    assert resolution.derived_score.physical.bpm == 128


def test_hard_changed_value_raises_conflict_error() -> None:
    source = _source()
    spec = _spec(
        target={"physical": {"bpm": 140}},
        score_fields={"physical.bpm": "hard"},
    )

    with pytest.raises(ArrangementConflictError) as excinfo:
        resolve_arrangement(source, spec)

    assert "physical.bpm" in str(excinfo.value)
    assert "arr-test" in str(excinfo.value)


def test_elastic_same_value_does_not_produce_change_record() -> None:
    source = _source()
    spec = _spec(
        target={"physical": {"bpm": 128}},
        score_fields={"physical.bpm": "elastic"},
    )

    resolution = resolve_arrangement(source, spec)

    assert resolution.changes == []


def test_free_change_record() -> None:
    source = _source()
    spec = _spec(
        target={"physical": {"brightness": "bright"}},
        score_fields={"physical.brightness": "free"},
    )

    resolution = resolve_arrangement(source, spec)

    assert resolution.changes[0].preservation_mode == "free"
    assert resolution.changes[0].before == "dark"
    assert resolution.changes[0].after == "bright"


# --- canonical normalization before comparison (P2-2) --------------------------


def test_hard_numeric_string_bpm_normalizes_and_does_not_conflict() -> None:
    """bpm の numeric string "128" は canonical 正規化で int 128 になるため、
    hard でも conflict にならず change record も出ない。"""
    source = _source()
    spec = _spec(
        target={"physical": {"bpm": "128"}},
        score_fields={"physical.bpm": "hard"},
    )

    resolution = resolve_arrangement(source, spec)

    assert resolution.changes == []
    assert resolution.derived_score.physical.bpm == 128


def test_elastic_numeric_string_bpm_records_canonical_after_value() -> None:
    source = _source()
    spec = _spec(
        target={"physical": {"bpm": "140"}},
        score_fields={"physical.bpm": "elastic"},
    )

    resolution = resolve_arrangement(source, spec)

    assert resolution.derived_score.physical.bpm == 140
    assert len(resolution.changes) == 1
    change = resolution.changes[0]
    assert change.before == 128
    assert change.after == 140
    assert isinstance(change.after, int)


# --- policy errors -------------------------------------------------------------


def test_missing_preservation_policy_raises_policy_error() -> None:
    source = _source()
    spec = _spec(target={"physical": {"bpm": 140}}, score_fields={})

    with pytest.raises(ArrangementPolicyError) as excinfo:
        resolve_arrangement(source, spec)

    assert "physical.bpm" in str(excinfo.value)


def test_preservation_only_paths_without_target_override_are_allowed() -> None:
    """target 未指定で preservation map にのみ path がある場合は許可される
    （将来 contract 用に保持方針を先に書ける）。changes は空、derived == source。"""
    source = _source()
    spec = _spec(
        target={},
        score_fields={"physical.bpm": "hard", "semantic.core": "elastic"},
    )

    resolution = resolve_arrangement(source, spec)

    assert resolution.changes == []
    assert resolution.derived_score.model_dump(mode="json") == source.model_dump(mode="json")


def test_missing_policy_is_reported_before_final_score_validation() -> None:
    """policy 欠落 + 最終 Score も invalid になる override の場合、
    ValidationError より先に ArrangementPolicyError が出る（チェック順の pin）。"""
    source = _source()
    spec = _spec(target={"physical": {"bpm": "adagio"}}, score_fields={})

    with pytest.raises(ArrangementPolicyError) as excinfo:
        resolve_arrangement(source, spec)

    assert "physical.bpm" in str(excinfo.value)


def test_unknown_preservation_path_is_rejected() -> None:
    source = _source()
    spec = _spec(target={}, score_fields={"physical.nonexistent": "hard"})

    with pytest.raises(ArrangementPolicyError) as excinfo:
        resolve_arrangement(source, spec)

    assert "physical.nonexistent" in str(excinfo.value)


@pytest.mark.parametrize("parent_path", ["semantic.grv", "physical", "semantic", "rendering"])
def test_parent_path_only_is_rejected(parent_path: str) -> None:
    source = _source()
    spec = _spec(target={}, score_fields={parent_path: "hard"})

    with pytest.raises(ArrangementPolicyError) as excinfo:
        resolve_arrangement(source, spec)

    assert parent_path in str(excinfo.value)


# --- schema-level rejection (extra keys) --------------------------------------


def test_extra_key_in_target_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _spec(target={"unknown_top_level": "x"}, score_fields={})


def test_extra_key_in_nested_semantic_override_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _spec(target={"semantic": {"unexpected": "x"}}, score_fields={})


def test_extra_key_in_preservation_root_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ArrangementSpec.model_validate(
            {
                "meta": {"id": "arr-test", "version": "1"},
                "target": {},
                "preservation": {"score_fields": {}},
                "unexpected": "x",
            }
        )


# --- invalid final Score --------------------------------------------------------


def test_invalid_final_score_raises_validation_error() -> None:
    source = _source()
    spec = _spec(
        target={"physical": {"bpm": "adagio"}},
        score_fields={"physical.bpm": "elastic"},
    )

    with pytest.raises(ValidationError):
        resolve_arrangement(source, spec)


# --- immutability / non-sharing -------------------------------------------------


def test_source_dump_is_not_mutated_by_resolve() -> None:
    source = _source()
    baseline = source.model_dump(mode="json")

    spec = _spec(
        target={
            "physical": {"bpm": 140, "brightness": "bright"},
            "semantic": {"avoid": ["new avoid entry"]},
            "structure": [
                {"section": "outro", "bars": 4, "role": "fade", "physical": "silence"},
            ],
        },
        score_fields={
            "physical.bpm": "elastic",
            "physical.brightness": "free",
            "semantic.avoid": "elastic",
            "structure": "free",
        },
    )

    resolve_arrangement(source, spec)

    assert source.model_dump(mode="json") == baseline


def test_nested_list_and_dict_objects_are_not_shared_with_source() -> None:
    source = _source()
    spec = _spec(target={}, score_fields={})

    resolution = resolve_arrangement(source, spec)

    assert resolution.derived_score.structure is not source.structure
    assert resolution.derived_score.semantic.avoid is not source.semantic.avoid

    resolution.derived_score.semantic.avoid.append("mutated only on derived")
    assert "mutated only on derived" not in source.semantic.avoid


def test_source_fixity_and_control_profile_are_preserved_unchanged() -> None:
    source = _source()
    assert source.control_profile is not None

    spec = _spec(
        target={"semantic": {"core": "different mood"}},
        score_fields={"semantic.core": "elastic"},
    )

    resolution = resolve_arrangement(source, spec)

    assert resolution.derived_score.control_profile == source.control_profile
    assert resolution.derived_score.fixity == source.fixity


# --- determinism -----------------------------------------------------------------


def test_repeated_resolution_is_deterministic() -> None:
    source = _source()
    spec = _spec(
        target={
            "physical": {"bpm": 140, "key": "D minor"},
            "semantic": {"grv": {"primary": "trap"}},
        },
        score_fields={
            "physical.bpm": "elastic",
            "physical.key": "elastic",
            "semantic.grv.primary": "free",
        },
    )

    first = resolve_arrangement(source, spec)
    second = resolve_arrangement(source, spec)

    assert first.derived_score.model_dump(mode="json") == second.derived_score.model_dump(
        mode="json"
    )
    assert [c.model_dump(mode="json") for c in first.changes] == [
        c.model_dump(mode="json") for c in second.changes
    ]


# --- model-level regression against the midnight_signal fixture -----------------


def test_midnight_signal_regression_resolve() -> None:
    source = _source()
    spec = _spec(
        target={
            "physical": {"bpm": 132, "brightness": "bright"},
            "rendering": {"priority": ["physical.bpm", "semantic.core"]},
        },
        score_fields={
            "physical.bpm": "elastic",
            "physical.brightness": "free",
            "rendering.priority": "elastic",
        },
    )

    resolution = resolve_arrangement(source, spec)

    assert isinstance(resolution.derived_score, CompositionScore)
    assert resolution.derived_score.physical.bpm == 132
    assert resolution.derived_score.physical.brightness == "bright"
    assert resolution.derived_score.rendering.priority == ["physical.bpm", "semantic.core"]
    # untouched fields remain identical to source
    assert resolution.derived_score.physical.key == source.physical.key
    assert resolution.derived_score.structure == source.structure
    assert resolution.derived_score.meta == source.meta
    paths = {c.path for c in resolution.changes}
    assert paths == {"physical.bpm", "physical.brightness", "rendering.priority"}


# --- AR2-3: preservation.identity_anchors vocabulary parses through the spec ----


def test_identity_anchors_ar2_3_vocabulary_round_trips_through_spec_parsing() -> None:
    """`ArrangementSpec.preservation.identity_anchors` accepts the AR2-3
    structure vocabulary (section_insertion/section_omission/
    section_repetition), and `resolve_arrangement` — which only ever reads
    `score_fields` for scalar/list overrides — is indifferent to its
    presence: `identity_anchors` cross-validation against a manifest's actual
    anchors/domains is `build_preservation_contract`'s job
    (`tests/test_preservation_contract.py`), not the resolver's."""
    source = _source()
    spec = ArrangementSpec.model_validate(
        {
            "meta": {"id": "arr-test", "version": "1"},
            "target": {},
            "preservation": {
                "score_fields": {},
                "identity_anchors": {
                    "anchor-structure": {
                        "mode": "elastic",
                        "allow": [
                            "section_insertion",
                            "section_omission",
                            "section_repetition",
                        ],
                    }
                },
            },
        }
    )

    resolution = resolve_arrangement(source, spec)

    assert resolution.changes == []
    assert resolution.derived_score.model_dump(mode="json") == source.model_dump(mode="json")
    assert spec.preservation.identity_anchors is not None
    assert spec.preservation.identity_anchors["anchor-structure"].allow == [
        "section_insertion",
        "section_omission",
        "section_repetition",
    ]
