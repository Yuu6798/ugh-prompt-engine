"""InputCapabilityProfile schema + evidence-checking loader のテスト (AR3-1)。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from svp_rpe.arrange import (
    INPUT_CHANNELS,
    InputCapabilityError,
    InputCapabilityProfile,
    InputChannelCapability,
    InputChannels,
    load_input_capability_profile,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _profile_dict(**channel_overrides: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema_version": "input-capability/0.1",
        "generator": "acme",
        "profile_version": "2026-07",
        "input_channels": dict(channel_overrides),
    }
    return data


def _write_profile(path: Path, data: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


# --- happy path -------------------------------------------------------------


def test_load_profile_succeeds_with_all_channels_declared(tmp_path: Path) -> None:
    profile_dict = _profile_dict(
        style_prompt={"support": "supported"},
        lyrics_text={"support": "unsupported", "note": "no lyrics channel"},
        section_tags={"support": "experimental"},
        reference_audio={"support": "unknown"},
        symbolic_melody={"support": "unsupported"},
        midi={"support": "unsupported"},
    )
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path, profile_dict)

    profile = load_input_capability_profile(profile_path)

    assert profile.generator == "acme"
    assert profile.schema_version == "input-capability/0.1"
    assert profile.input_channels.style_prompt.support == "supported"
    assert profile.input_channels.lyrics_text.note == "no lyrics channel"


def test_evidence_and_note_are_optional(tmp_path: Path) -> None:
    profile_dict = _profile_dict(style_prompt={"support": "supported"})
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path, profile_dict)

    profile = load_input_capability_profile(profile_path)

    assert profile.input_channels.style_prompt.evidence is None
    assert profile.input_channels.style_prompt.note is None


# --- omitted channel -> unknown fallback ------------------------------------


def test_omitted_channel_falls_back_to_unknown_not_supported(tmp_path: Path) -> None:
    profile_dict = _profile_dict(style_prompt={"support": "supported"})
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path, profile_dict)

    profile = load_input_capability_profile(profile_path)

    for channel_name in INPUT_CHANNELS:
        if channel_name == "style_prompt":
            continue
        capability = getattr(profile.input_channels, channel_name)
        assert capability.support == "unknown"
        assert capability.evidence is None
        assert capability.note is None


def test_all_channels_omitted_defaults_to_all_unknown(tmp_path: Path) -> None:
    profile_dict = _profile_dict()
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path, profile_dict)

    profile = load_input_capability_profile(profile_path)

    for channel_name in INPUT_CHANNELS:
        assert getattr(profile.input_channels, channel_name).support == "unknown"


# --- evidence verification ---------------------------------------------------


def test_evidence_verification_succeeds_when_evidence_file_exists(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "docs"
    evidence_dir.mkdir()
    (evidence_dir / "proof.md").write_text("proof", encoding="utf-8")

    profile_dict = _profile_dict(style_prompt={"support": "supported", "evidence": "docs/proof.md"})
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path, profile_dict)

    profile = load_input_capability_profile(profile_path, evidence_base=tmp_path)

    assert profile.input_channels.style_prompt.evidence == "docs/proof.md"


def test_evidence_verification_fails_when_evidence_file_missing(tmp_path: Path) -> None:
    profile_dict = _profile_dict(
        style_prompt={"support": "supported", "evidence": "docs/does-not-exist.md"}
    )
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path, profile_dict)

    with pytest.raises(InputCapabilityError) as exc_info:
        load_input_capability_profile(profile_path, evidence_base=tmp_path)

    message = str(exc_info.value)
    assert "acme" in message
    assert "style_prompt" in message
    assert "does-not-exist.md" in message


def test_evidence_none_skips_verification(tmp_path: Path) -> None:
    profile_dict = _profile_dict(style_prompt={"support": "unknown"})
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path, profile_dict)

    # evidence_base points somewhere that does not even exist; must not matter
    # since no channel declares evidence.
    profile = load_input_capability_profile(
        profile_path, evidence_base=tmp_path / "nonexistent-base"
    )

    assert profile.input_channels.style_prompt.evidence is None


def test_default_base_skips_existence_check_for_missing_evidence(tmp_path: Path) -> None:
    """packaged 環境シミュレーション: evidence の実ファイルが無くてもロード成功する。

    wheel インストール環境では profile YAML は package resource として存在するが
    evidence の指す docs/*.md は同梱されない。`evidence_base=None`（既定）は
    実在検証を行わず、evidence を provenance 文字列として保持するのみ。
    """
    profile_dict = _profile_dict(
        style_prompt={"support": "supported", "evidence": "docs/not-shipped-in-wheel.md"}
    )
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path, profile_dict)

    profile = load_input_capability_profile(profile_path)

    assert profile.input_channels.style_prompt.evidence == "docs/not-shipped-in-wheel.md"


def test_default_base_still_rejects_absolute_evidence(tmp_path: Path) -> None:
    """形式検証（絶対パス拒否）は base 非依存のため `evidence_base=None` でも維持。"""
    outside_file = tmp_path / "outside.md"
    outside_file.write_text("exists but absolute", encoding="utf-8")

    profile_dict = _profile_dict(
        style_prompt={"support": "supported", "evidence": str(outside_file)}
    )
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path, profile_dict)

    with pytest.raises(InputCapabilityError) as exc_info:
        load_input_capability_profile(profile_path)

    message = str(exc_info.value)
    assert "acme" in message
    assert "style_prompt" in message
    assert str(outside_file) in message


# --- evidence confinement (可搬性契約: evidence は evidence_base 配下に閉じる) ----


def test_absolute_evidence_path_is_rejected(tmp_path: Path) -> None:
    outside_file = tmp_path / "outside.md"
    outside_file.write_text("exists but absolute", encoding="utf-8")

    profile_dict = _profile_dict(
        style_prompt={"support": "supported", "evidence": str(outside_file)}
    )
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path, profile_dict)

    with pytest.raises(InputCapabilityError) as exc_info:
        load_input_capability_profile(profile_path, evidence_base=tmp_path)

    message = str(exc_info.value)
    assert "acme" in message
    assert "style_prompt" in message
    assert str(outside_file) in message


def test_parent_escaping_evidence_is_rejected_even_if_file_exists(tmp_path: Path) -> None:
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    (tmp_path / "outside.md").write_text("real file outside the base", encoding="utf-8")

    profile_dict = _profile_dict(
        style_prompt={"support": "supported", "evidence": "../outside.md"}
    )
    profile_path = base_dir / "profile.yaml"
    _write_profile(profile_path, profile_dict)

    with pytest.raises(InputCapabilityError) as exc_info:
        load_input_capability_profile(profile_path, evidence_base=base_dir)

    message = str(exc_info.value)
    assert "acme" in message
    assert "style_prompt" in message
    assert "../outside.md" in message


def test_symlink_escaping_evidence_is_rejected(tmp_path: Path) -> None:
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    outside_file = tmp_path / "outside.md"
    outside_file.write_text("real file outside the base", encoding="utf-8")
    (base_dir / "sneaky_link.md").symlink_to(outside_file)

    profile_dict = _profile_dict(
        style_prompt={"support": "supported", "evidence": "sneaky_link.md"}
    )
    profile_path = base_dir / "profile.yaml"
    _write_profile(profile_path, profile_dict)

    with pytest.raises(InputCapabilityError) as exc_info:
        load_input_capability_profile(profile_path, evidence_base=base_dir)

    message = str(exc_info.value)
    assert "acme" in message
    assert "style_prompt" in message


def test_nested_relative_evidence_path_still_loads(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "docs" / "nested"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "proof.md").write_text("proof", encoding="utf-8")

    profile_dict = _profile_dict(
        style_prompt={"support": "supported", "evidence": "docs/nested/proof.md"}
    )
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path, profile_dict)

    profile = load_input_capability_profile(profile_path, evidence_base=tmp_path)

    assert profile.input_channels.style_prompt.evidence == "docs/nested/proof.md"


def test_default_base_rejects_lexically_traversing_evidence(tmp_path: Path) -> None:
    """`evidence_base=None` でも字面の遡上（`../`）は形式検証で拒否される。"""
    (tmp_path / "outside.md").write_text("real file outside the base", encoding="utf-8")

    profile_dict = _profile_dict(
        style_prompt={"support": "supported", "evidence": "../outside.md"}
    )
    profile_path = tmp_path / "base"
    profile_path.mkdir()
    profile_path = profile_path / "profile.yaml"
    _write_profile(profile_path, profile_dict)

    with pytest.raises(InputCapabilityError) as exc_info:
        load_input_capability_profile(profile_path)

    message = str(exc_info.value)
    assert "acme" in message
    assert "style_prompt" in message
    assert "../outside.md" in message


def test_default_base_allows_internally_cancelling_dotdot(tmp_path: Path) -> None:
    """`docs/../docs/x.md` の内部相殺は遡上でないため形式検証を通過し、
    `evidence_base=None` では実在検証もスキップされてロード成功する。"""
    profile_dict = _profile_dict(
        style_prompt={"support": "supported", "evidence": "docs/../docs/x.md"}
    )
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path, profile_dict)

    profile = load_input_capability_profile(profile_path)

    assert profile.input_channels.style_prompt.evidence == "docs/../docs/x.md"


# --- schema validation --------------------------------------------------------


def test_unknown_support_value_raises_validation_error(tmp_path: Path) -> None:
    profile_dict = _profile_dict(style_prompt={"support": "always"})
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path, profile_dict)

    with pytest.raises(ValidationError):
        load_input_capability_profile(profile_path)


def test_unknown_channel_key_raises_validation_error(tmp_path: Path) -> None:
    profile_dict = _profile_dict(
        style_prompt={"support": "supported"}, video_prompt={"support": "unknown"}
    )
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path, profile_dict)

    with pytest.raises(ValidationError):
        load_input_capability_profile(profile_path)


def test_unknown_top_level_key_raises_validation_error(tmp_path: Path) -> None:
    profile_dict = _profile_dict(style_prompt={"support": "supported"})
    profile_dict["unexpected"] = "value"
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path, profile_dict)

    with pytest.raises(ValidationError):
        load_input_capability_profile(profile_path)


def test_unknown_capability_key_raises_validation_error(tmp_path: Path) -> None:
    profile_dict = _profile_dict(style_prompt={"support": "supported", "unexpected": "value"})
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path, profile_dict)

    with pytest.raises(ValidationError):
        load_input_capability_profile(profile_path)


# --- loader error paths --------------------------------------------------------


def test_missing_profile_path_raises_input_capability_error(tmp_path: Path) -> None:
    profile_path = tmp_path / "does-not-exist.yaml"

    with pytest.raises(InputCapabilityError) as exc_info:
        load_input_capability_profile(profile_path)

    assert str(profile_path) in str(exc_info.value)


def test_directory_profile_path_raises_input_capability_error(tmp_path: Path) -> None:
    profile_path = tmp_path / "a_directory"
    profile_path.mkdir()

    with pytest.raises(InputCapabilityError) as exc_info:
        load_input_capability_profile(profile_path)

    assert str(profile_path) in str(exc_info.value)


def test_non_mapping_yaml_raises_value_error(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile.yaml"
    profile_path.write_text(yaml.safe_dump([1, 2, 3]), encoding="utf-8")

    with pytest.raises(ValueError):
        load_input_capability_profile(profile_path)


# --- committed profiles: real load + evidence check ---------------------------


@pytest.mark.parametrize("generator", ["suno", "musicgen"])
def test_committed_profile_loads_and_evidence_resolves(generator: str) -> None:
    profile_path = REPO_ROOT / "config" / "capability_profiles" / f"{generator}.yaml"

    profile = load_input_capability_profile(profile_path, evidence_base=REPO_ROOT)

    assert profile.generator == generator
    assert profile.schema_version == "input-capability/0.1"


# --- config double-copy sync pin (tests/test_config.py 契約は変更せず、ここで独立に pin) --


def test_capability_profiles_repo_and_packaged_copies_are_byte_identical() -> None:
    repo_dir = REPO_ROOT / "config" / "capability_profiles"
    packaged_dir = REPO_ROOT / "src" / "svp_rpe" / "config" / "capability_profiles"

    repo_files = {p.name for p in repo_dir.glob("*.yaml")}
    packaged_files = {p.name for p in packaged_dir.glob("*.yaml")}

    assert repo_files == packaged_files
    assert repo_files, "expected at least one capability profile YAML to compare"

    for name in sorted(repo_files):
        assert (repo_dir / name).read_text(encoding="utf-8") == (
            packaged_dir / name
        ).read_text(encoding="utf-8"), f"capability profile drift: {name}"


def test_capability_profiles_are_listed_in_package_data() -> None:
    """wheel 配布で capability_profiles YAML が同梱されることを pin する。

    editable install ではパッケージ path が src/ を直接指すため package-data の
    欠落に気付けない（Codex P2-2）。エントリの存在自体を pin して回帰を防ぐ。
    """
    import tomllib

    with (REPO_ROOT / "pyproject.toml").open("rb") as f:
        pyproject = tomllib.load(f)

    package_data = pyproject["tool"]["setuptools"]["package-data"]
    assert package_data["svp_rpe.config.capability_profiles"] == ["*.yaml"]


# --- determinism --------------------------------------------------------------


def test_repeated_load_is_deterministic(tmp_path: Path) -> None:
    profile_dict = _profile_dict(style_prompt={"support": "supported"})
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path, profile_dict)

    first = load_input_capability_profile(profile_path)
    second = load_input_capability_profile(profile_path)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")


# --- construction without loader (schema-level) --------------------------------


def test_input_capability_profile_direct_construction_roundtrips() -> None:
    profile = InputCapabilityProfile(
        schema_version="input-capability/0.1",
        generator="acme",
        profile_version="2026-07",
        input_channels=InputChannels(
            style_prompt=InputChannelCapability(support="supported"),
        ),
    )

    dumped = profile.model_dump(mode="json")
    assert dumped["input_channels"]["style_prompt"]["support"] == "supported"
    assert dumped["input_channels"]["midi"]["support"] == "unknown"
