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


# --- M4 (DD-10b): MelodyObservationConfig.reference_band ------------------------


def _melody_config_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "reference": "score",
        "comparison_registry": "m3_comparison_registry.yaml",
        "m1_registry": "registry.yaml",
    }
    payload.update(overrides)
    return payload


def test_melody_observation_config_reference_band_defaults_to_none() -> None:
    from svp_rpe.recast.models import MelodyObservationConfig

    config = MelodyObservationConfig.model_validate(_melody_config_payload())
    assert config.reference_band is None


def test_melody_observation_config_score_reference_forbids_reference_band() -> None:
    """DD-10b: `reference == 'score'` のとき `reference_band` の宣言は
    fail-closed で拒否する（記号旋律に帯域の概念は無い）。"""
    from svp_rpe.recast.models import MelodyObservationConfig

    with pytest.raises(ValidationError, match="forbids reference_band"):
        MelodyObservationConfig.model_validate(
            _melody_config_payload(reference="score", reference_band="clear_lead")
        )


def test_melody_observation_config_audio_reference_accepts_reference_band() -> None:
    from svp_rpe.recast.models import MelodyObservationConfig

    config = MelodyObservationConfig.model_validate(
        _melody_config_payload(
            reference="audio", reference_audio="ref.wav", reference_band="clear_lead"
        )
    )
    assert config.reference_band == "clear_lead"


def test_melody_observation_config_audio_reference_allows_omitted_reference_band() -> None:
    """`reference == 'audio'` は `reference_band` を宣言してもしなくてもよい
    （既定 `None` は現行どおり G2 fail-closed のまま — DD-10b は additive）。"""
    from svp_rpe.recast.models import MelodyObservationConfig

    config = MelodyObservationConfig.model_validate(
        _melody_config_payload(reference="audio", reference_audio="ref.wav")
    )
    assert config.reference_band is None


def test_melody_observation_config_rejects_unknown_reference_band_value() -> None:
    from svp_rpe.recast.models import MelodyObservationConfig

    with pytest.raises(ValidationError):
        MelodyObservationConfig.model_validate(
            _melody_config_payload(
                reference="audio", reference_audio="ref.wav", reference_band="stereo_mix"
            )
        )


# --- R1-1 (Codex round1 P1): route の校正済み集合バインド --------------------


def test_calibration_bound_routes_is_crepe_direct_only() -> None:
    """M3d の evidence 閾値は crepe_direct ペアで校正予定（DESIGN_M3 §該当行）
    ——活性化を許す route 集合はこの 1 本のみ（single source）。"""
    from svp_rpe.recast.models import CALIBRATION_BOUND_ROUTES

    assert CALIBRATION_BOUND_ROUTES == frozenset({"crepe_direct"})


def test_melody_observation_config_route_defaults_to_crepe_direct() -> None:
    from svp_rpe.recast.models import MelodyObservationConfig

    config = MelodyObservationConfig.model_validate(_melody_config_payload())
    assert config.route == "crepe_direct"


def test_melody_observation_config_accepts_crepe_direct_route_explicitly() -> None:
    from svp_rpe.recast.models import MelodyObservationConfig

    config = MelodyObservationConfig.model_validate(
        _melody_config_payload(route="crepe_direct")
    )
    assert config.route == "crepe_direct"


def test_melody_observation_config_rejects_pyin_direct_route() -> None:
    """R1-1: pyin_direct は clear_lead 帯だが校正済み集合外——凍結閾値を
    未校正の別 route 出力分布へ適用すると誤って preserved / 違反を発しうる
    ため fail-closed で拒否する（旧仕様からの破壊的変更・意図的）。"""
    from svp_rpe.recast.models import MelodyObservationConfig

    with pytest.raises(ValidationError, match="calibration-bound"):
        MelodyObservationConfig.model_validate(_melody_config_payload(route="pyin_direct"))


def test_melody_observation_config_rejects_melodia_direct_route() -> None:
    from svp_rpe.recast.models import MelodyObservationConfig

    with pytest.raises(ValidationError, match="calibration-bound"):
        MelodyObservationConfig.model_validate(_melody_config_payload(route="melodia_direct"))


def test_melody_observation_config_rejects_non_clear_lead_route() -> None:
    """clear_lead 帯にすら無い route 名は従来どおり拒否する（回帰確認）。"""
    from svp_rpe.recast.models import MelodyObservationConfig

    with pytest.raises(ValidationError):
        MelodyObservationConfig.model_validate(
            _melody_config_payload(route="demucs_vocals_then_crepe")
        )
