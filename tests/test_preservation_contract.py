"""PreservationContract schema + build_preservation_contract のテスト (AR2-2)。"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from svp_rpe.arrange import (
    AnchorPreservation,
    ArrangementMeta,
    ArrangementSpec,
    ArrangementTarget,
    ContractAnchor,
    DOMAIN_ALLOWED_TRANSFORMS,
    IdentityAnchor,
    IdentityManifest,
    PreservationContract,
    PreservationContractError,
    PreservationSpec,
    build_preservation_contract,
    load_arrangement_spec,
    resolve_arrangement,
)
from svp_rpe.arrange.identity import IdentityMeta, IdentitySource
from svp_rpe.cli import app
from svp_rpe.compose import load_composition_score

VALID_SHA256 = "0" * 64
OTHER_SHA256 = "1" * 64
MANIFEST_SHA256 = "a" * 64
SPEC_SHA256 = "b" * 64


def _identity_anchor(
    anchor_id: str,
    domain: str,
    *,
    required: bool = True,
    artifact: str | None = None,
    sha256: str = VALID_SHA256,
) -> IdentityAnchor:
    return IdentityAnchor(
        id=anchor_id,
        domain=domain,
        artifact=artifact or f"{anchor_id}.txt",
        sha256=sha256,
        required=required,
    )


def _manifest(anchors: list[IdentityAnchor], *, work_id: str = "work-1") -> IdentityManifest:
    return IdentityManifest(
        meta=IdentityMeta(work_id=work_id, version="1"),
        source=IdentitySource(locator="source.wav", sha256=VALID_SHA256, rights_basis="original"),
        anchors=anchors,
    )


def _spec(identity_anchors: dict[str, AnchorPreservation] | None) -> ArrangementSpec:
    return ArrangementSpec(
        meta=ArrangementMeta(id="spec-1", version="0.1"),
        target=ArrangementTarget(),
        preservation=PreservationSpec(score_fields={}, identity_anchors=identity_anchors),
    )


def _planning_manifest() -> IdentityManifest:
    return _manifest(
        [
            _identity_anchor("anchor-lyrics", "lyrics", required=True),
            _identity_anchor("anchor-melody", "melody", required=True),
            _identity_anchor("anchor-harmony", "harmony", required=False),
            _identity_anchor("anchor-structure", "structure", required=False),
        ]
    )


def _planning_policies() -> dict[str, AnchorPreservation]:
    return {
        "anchor-lyrics": AnchorPreservation(mode="hard"),
        "anchor-melody": AnchorPreservation(
            mode="elastic",
            allow=["octave_displacement", "ornamentation", "timing_warp"],
        ),
        "anchor-harmony": AnchorPreservation(
            mode="elastic",
            allow=["chord_extensions", "functional_substitution"],
        ),
        "anchor-structure": AnchorPreservation(
            mode="elastic",
            allow=["intro_extension", "instrumental_break"],
        ),
    }


# --- schema: mode / allow consistency ----------------------------------------


def test_hard_with_nonempty_allow_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        AnchorPreservation(mode="hard", allow=["octave_displacement"])


def test_free_with_nonempty_allow_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        AnchorPreservation(mode="free", allow=["timing_warp"])


def test_elastic_with_empty_allow_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        AnchorPreservation(mode="elastic", allow=[])


def test_elastic_with_default_allow_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        AnchorPreservation(mode="elastic")


def test_unknown_transformation_string_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        AnchorPreservation(mode="elastic", allow=["time_travel"])


def test_unknown_key_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        AnchorPreservation.model_validate({"mode": "hard", "unexpected": "value"})


def test_tolerance_profile_is_optional_and_preserved() -> None:
    without = AnchorPreservation(mode="hard")
    assert without.tolerance_profile is None

    with_profile = AnchorPreservation(
        mode="elastic", allow=["timing_warp"], tolerance_profile="loose-v1"
    )
    assert with_profile.tolerance_profile == "loose-v1"


def test_hard_and_free_with_empty_allow_are_valid() -> None:
    hard = AnchorPreservation(mode="hard")
    free = AnchorPreservation(mode="free")
    assert hard.allow == []
    assert free.allow == []


# --- build: happy path --------------------------------------------------------


def test_build_preservation_contract_matches_planning_example() -> None:
    manifest = _planning_manifest()
    spec = _spec(_planning_policies())

    contract = build_preservation_contract(
        manifest, spec, manifest_sha256=MANIFEST_SHA256, spec_sha256=SPEC_SHA256
    )

    assert isinstance(contract, PreservationContract)
    assert contract.schema_version == "preservation-contract/0.1"
    assert contract.work_id == "work-1"
    assert contract.inputs.identity_manifest.sha256 == MANIFEST_SHA256
    assert contract.inputs.arrangement_spec.sha256 == SPEC_SHA256

    anchor_ids = [anchor.anchor_id for anchor in contract.anchors]
    assert anchor_ids == ["anchor-lyrics", "anchor-melody", "anchor-harmony", "anchor-structure"]

    lyrics = contract.anchors[0]
    assert isinstance(lyrics, ContractAnchor)
    assert lyrics.mode == "hard"
    assert lyrics.allow == []
    assert lyrics.artifact == "anchor-lyrics.txt"
    assert lyrics.artifact_sha256 == VALID_SHA256

    melody = contract.anchors[1]
    assert melody.mode == "elastic"
    assert set(melody.allow) == {"octave_displacement", "ornamentation", "timing_warp"}


def test_build_preservation_contract_omits_anchors_without_policy() -> None:
    manifest = _manifest(
        [
            _identity_anchor("anchor-lyrics", "lyrics", required=True),
            _identity_anchor("anchor-melody", "melody", required=False),
        ]
    )
    spec = _spec({"anchor-lyrics": AnchorPreservation(mode="hard")})

    contract = build_preservation_contract(
        manifest, spec, manifest_sha256=MANIFEST_SHA256, spec_sha256=SPEC_SHA256
    )

    anchor_ids = [anchor.anchor_id for anchor in contract.anchors]
    assert anchor_ids == ["anchor-lyrics"]


def test_build_preservation_contract_with_no_identity_anchors_policy_is_empty() -> None:
    manifest = _manifest([_identity_anchor("anchor-melody", "melody", required=False)])
    spec = _spec(None)

    contract = build_preservation_contract(
        manifest, spec, manifest_sha256=MANIFEST_SHA256, spec_sha256=SPEC_SHA256
    )

    assert contract.anchors == []


# --- build: cross-validation errors ------------------------------------------


def test_unknown_anchor_id_in_policy_raises_error() -> None:
    manifest = _manifest([_identity_anchor("anchor-lyrics", "lyrics", required=False)])
    spec = _spec({"anchor-ghost": AnchorPreservation(mode="hard")})

    with pytest.raises(PreservationContractError) as exc_info:
        build_preservation_contract(
            manifest, spec, manifest_sha256=MANIFEST_SHA256, spec_sha256=SPEC_SHA256
        )

    message = str(exc_info.value)
    assert "work-1" in message
    assert "anchor-ghost" in message


def test_required_anchor_without_policy_raises_error() -> None:
    manifest = _manifest(
        [
            _identity_anchor("anchor-lyrics", "lyrics", required=True),
            _identity_anchor("anchor-melody", "melody", required=True),
        ]
    )
    spec = _spec({"anchor-lyrics": AnchorPreservation(mode="hard")})

    with pytest.raises(PreservationContractError) as exc_info:
        build_preservation_contract(
            manifest, spec, manifest_sha256=MANIFEST_SHA256, spec_sha256=SPEC_SHA256
        )

    message = str(exc_info.value)
    assert "work-1" in message
    assert "anchor-melody" in message


def test_non_required_anchor_may_omit_policy() -> None:
    manifest = _manifest([_identity_anchor("anchor-harmony", "harmony", required=False)])
    spec = _spec(None)

    contract = build_preservation_contract(
        manifest, spec, manifest_sha256=MANIFEST_SHA256, spec_sha256=SPEC_SHA256
    )

    assert contract.anchors == []


def test_elastic_allow_outside_domain_vocabulary_raises_error() -> None:
    manifest = _manifest([_identity_anchor("anchor-melody", "melody", required=True)])
    spec = _spec(
        {"anchor-melody": AnchorPreservation(mode="elastic", allow=["chord_extensions"])}
    )

    with pytest.raises(PreservationContractError) as exc_info:
        build_preservation_contract(
            manifest, spec, manifest_sha256=MANIFEST_SHA256, spec_sha256=SPEC_SHA256
        )

    message = str(exc_info.value)
    assert "work-1" in message
    assert "anchor-melody" in message
    assert "melody" in message


@pytest.mark.parametrize("domain", ["lyrics", "rhythm", "motif"])
def test_vocabulary_empty_domains_reject_elastic(domain: str) -> None:
    assert DOMAIN_ALLOWED_TRANSFORMS[domain] == frozenset()

    manifest = _manifest([_identity_anchor("anchor-x", domain, required=True)])
    spec = _spec({"anchor-x": AnchorPreservation(mode="elastic", allow=["ornamentation"])})

    with pytest.raises(PreservationContractError) as exc_info:
        build_preservation_contract(
            manifest, spec, manifest_sha256=MANIFEST_SHA256, spec_sha256=SPEC_SHA256
        )

    assert "anchor-x" in str(exc_info.value)


@pytest.mark.parametrize("mode", ["hard", "free"])
def test_vocabulary_empty_domains_accept_hard_and_free(mode: str) -> None:
    manifest = _manifest([_identity_anchor("anchor-x", "lyrics", required=True)])
    spec = _spec({"anchor-x": AnchorPreservation(mode=mode)})

    contract = build_preservation_contract(
        manifest, spec, manifest_sha256=MANIFEST_SHA256, spec_sha256=SPEC_SHA256
    )

    assert contract.anchors[0].mode == mode
    assert contract.anchors[0].allow == []


# --- determinism ---------------------------------------------------------------


def test_build_preservation_contract_is_deterministic() -> None:
    manifest = _planning_manifest()
    spec = _spec(_planning_policies())

    first = build_preservation_contract(
        manifest, spec, manifest_sha256=MANIFEST_SHA256, spec_sha256=SPEC_SHA256
    )
    second = build_preservation_contract(
        manifest, spec, manifest_sha256=MANIFEST_SHA256, spec_sha256=SPEC_SHA256
    )

    assert first.model_dump(mode="json") == second.model_dump(mode="json")


# --- schema: contract read-back invariants (safety net) -------------------------


def _contract_dict(anchor: dict) -> dict:
    return {
        "schema_version": "preservation-contract/0.1",
        "work_id": "work-1",
        "inputs": {
            "identity_manifest": {"sha256": MANIFEST_SHA256},
            "arrangement_spec": {"sha256": SPEC_SHA256},
        },
        "anchors": [anchor],
    }


def _contract_anchor_dict(**overrides: object) -> dict:
    anchor = {
        "anchor_id": "anchor-x",
        "domain": "melody",
        "mode": "hard",
        "allow": [],
        "artifact": "anchor-x.txt",
        "artifact_sha256": VALID_SHA256,
    }
    anchor.update(overrides)
    return anchor


def test_readback_hard_with_nonempty_allow_raises_validation_error() -> None:
    data = _contract_dict(_contract_anchor_dict(mode="hard", allow=["timing_warp"]))

    with pytest.raises(ValidationError):
        PreservationContract.model_validate(data)


def test_readback_elastic_with_empty_allow_raises_validation_error() -> None:
    data = _contract_dict(_contract_anchor_dict(mode="elastic", allow=[]))

    with pytest.raises(ValidationError):
        PreservationContract.model_validate(data)


def test_readback_domain_vocabulary_violation_raises_validation_error() -> None:
    data = _contract_dict(
        _contract_anchor_dict(domain="melody", mode="elastic", allow=["chord_extensions"])
    )

    with pytest.raises(ValidationError):
        PreservationContract.model_validate(data)


def test_built_contract_roundtrips_through_model_validate() -> None:
    manifest = _planning_manifest()
    spec = _spec(_planning_policies())

    contract = build_preservation_contract(
        manifest, spec, manifest_sha256=MANIFEST_SHA256, spec_sha256=SPEC_SHA256
    )

    dumped = contract.model_dump(mode="json")
    readback = PreservationContract.model_validate(dumped)

    assert readback.model_dump(mode="json") == dumped


# --- schema: sha256 pattern validation ----------------------------------------


def test_malformed_manifest_sha256_raises_validation_error() -> None:
    manifest = _manifest([_identity_anchor("anchor-lyrics", "lyrics", required=False)])
    spec = _spec(None)

    with pytest.raises(ValidationError):
        build_preservation_contract(
            manifest, spec, manifest_sha256="not-a-hash", spec_sha256=SPEC_SHA256
        )


def test_malformed_spec_sha256_raises_validation_error() -> None:
    manifest = _manifest([_identity_anchor("anchor-lyrics", "lyrics", required=False)])
    spec = _spec(None)

    with pytest.raises(ValidationError):
        build_preservation_contract(
            manifest, spec, manifest_sha256=MANIFEST_SHA256, spec_sha256="short"
        )


# --- backward-compat regression: existing fixtures unaffected ------------------


FIXTURE_DIR = Path("examples/arrangement/midnight_signal")
BASE_SCORE = FIXTURE_DIR / "composition_score.yaml"


@pytest.mark.parametrize("variant", ["edm", "jazz"])
def test_existing_arrangement_spec_fixtures_load_without_identity_anchors(variant: str) -> None:
    spec = load_arrangement_spec(FIXTURE_DIR / f"{variant}.arrangement.yaml")
    assert spec.preservation.identity_anchors is None


@pytest.mark.parametrize("variant", ["edm", "jazz"])
def test_resolve_arrangement_ignores_absent_identity_anchors(variant: str) -> None:
    source = load_composition_score(BASE_SCORE)
    spec = load_arrangement_spec(FIXTURE_DIR / f"{variant}.arrangement.yaml")

    resolution = resolve_arrangement(source, spec)

    expected_dir = FIXTURE_DIR / "expected" / variant
    expected_derived = load_composition_score(expected_dir / "derived_score.yaml")
    assert resolution.derived_score.model_dump(mode="json") == expected_derived.model_dump(
        mode="json"
    )


def test_arrange_cli_regression_unaffected_by_identity_anchors_field(tmp_path: Path) -> None:
    """AR2-2 の additive field 追加が `svprpe arrange` CLI の既存出力に影響しない
    ことを固定する（干渉なしの pin）。"""
    output_dir = tmp_path / "edm"
    result = CliRunner().invoke(
        app,
        [
            "arrange",
            str(BASE_SCORE),
            str(FIXTURE_DIR / "edm.arrangement.yaml"),
            "--output-dir",
            str(output_dir),
        ],
    )
    assert result.exit_code == 0, result.output

    expected_dir = FIXTURE_DIR / "expected" / "edm"
    for filename in ("derived_score.yaml", "arrangement_bundle.json", "arrangement_diff.json"):
        assert (output_dir / filename).read_bytes() == (expected_dir / filename).read_bytes()
