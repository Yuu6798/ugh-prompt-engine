"""tests/test_authoring_contract.py — L0a authoring contract spec loader (D-L0a-1)."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from svp_rpe.authoring.contract import FieldSpec, ObjectSpec, load_authoring_contract


def test_load_default_contract_from_repo_config():
    spec = load_authoring_contract()
    assert spec.schema_version == "authoring-contract/1.0"
    assert set(spec.top_level.allowed_keys) == {
        "meta",
        "semantic",
        "physical",
        "structure",
        "rendering",
        "events",
    }


def test_load_contract_from_explicit_path(tmp_path: Path):
    default_bytes = Path("config/authoring_contract_l0.yaml").read_bytes()
    copy_path = tmp_path / "spec.yaml"
    copy_path.write_bytes(default_bytes)

    spec = load_authoring_contract(copy_path)
    assert spec.schema_version == "authoring-contract/1.0"


def test_default_contract_field_specs_match_validate_score_py_rules():
    """spot-check the逐語移植: physical/rendering/chord fields exactly mirror
    `examples/l0s_spike/scripts/validate_score.py`'s frozen constants."""

    spec = load_authoring_contract()

    assert spec.physical.fields["bpm"].type == "int"
    assert spec.physical.fields["bpm"].min == 1
    assert spec.physical.fields["key"].type == "str"
    assert spec.physical.fields["key"].format == r"^[A-Ga-g][#b]? (major|minor)$"
    assert spec.physical.fields["time_signature"].format == r"^[1-9][0-9]*/[1-9][0-9]*$"
    assert set(spec.physical.fields["brightness"].enum or []) == {"dark", "bright", "balanced"}

    assert spec.structure_section.fields["bars"].type == "int"
    assert spec.structure_section.fields["bars"].min == 1

    assert spec.rendering.fields["target_backend"].literal == "external"
    assert spec.rendering.fields["prompt_max_chars"].type == "int"
    assert spec.rendering.fields["prompt_max_chars"].enum is None
    assert spec.rendering.fields["priority"].type == "list_str"

    assert set(spec.chord.fields["root"].enum or []) == {
        "C",
        "C#",
        "D",
        "D#",
        "E",
        "F",
        "F#",
        "G",
        "G#",
        "A",
        "A#",
        "B",
    }
    assert set(spec.chord.fields["quality"].enum or []) == {"major", "minor"}

    # semantic.lyrics_presence deliberately absent (contract non-disclosure).
    assert "lyrics_presence" not in spec.semantic.allowed_keys
    # fixity/control_profile deliberately absent from every level.
    assert "fixity" not in spec.top_level.allowed_keys
    assert "control_profile" not in spec.top_level.allowed_keys


def test_load_nonexistent_explicit_path_raises(tmp_path: Path):
    with pytest.raises(OSError):
        load_authoring_contract(tmp_path / "does-not-exist.yaml")


def test_spec_rejects_unknown_top_level_key(tmp_path: Path):
    path = tmp_path / "spec.yaml"
    path.write_text(
        """
schema_version: "authoring-contract/1.0"
top_level: {allowed_keys: [meta]}
meta: {allowed_keys: [title]}
semantic: {allowed_keys: []}
grv: {allowed_keys: []}
delta_e: {allowed_keys: []}
physical: {allowed_keys: []}
structure_section: {allowed_keys: []}
rendering: {allowed_keys: []}
events: {allowed_keys: []}
chord: {allowed_keys: []}
unknown_extra_section: {}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_authoring_contract(path)


def test_spec_rejects_unknown_field_spec_key(tmp_path: Path):
    path = tmp_path / "spec.yaml"
    path.write_text(
        """
schema_version: "authoring-contract/1.0"
top_level: {allowed_keys: [meta]}
meta:
  allowed_keys: [title]
  fields:
    title: {type: str, unknown_key: 1}
semantic: {allowed_keys: []}
grv: {allowed_keys: []}
delta_e: {allowed_keys: []}
physical: {allowed_keys: []}
structure_section: {allowed_keys: []}
rendering: {allowed_keys: []}
events: {allowed_keys: []}
chord: {allowed_keys: []}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_authoring_contract(path)


def test_spec_rejects_wrong_schema_version(tmp_path: Path):
    path = tmp_path / "spec.yaml"
    path.write_text(
        """
schema_version: "authoring-contract/2.0"
top_level: {allowed_keys: [meta]}
meta: {allowed_keys: [title]}
semantic: {allowed_keys: []}
grv: {allowed_keys: []}
delta_e: {allowed_keys: []}
physical: {allowed_keys: []}
structure_section: {allowed_keys: []}
rendering: {allowed_keys: []}
events: {allowed_keys: []}
chord: {allowed_keys: []}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_authoring_contract(path)


def test_object_spec_defaults_to_no_fields():
    spec = ObjectSpec(allowed_keys=["a", "b"])
    assert spec.fields == {}


def test_object_spec_defaults_to_no_min_items():
    spec = ObjectSpec(allowed_keys=["a"])
    assert spec.min_items is None


def test_object_spec_accepts_min_items():
    spec = ObjectSpec(allowed_keys=["a"], min_items=1)
    assert spec.min_items == 1


def test_default_contract_structure_section_requires_at_least_one_item():
    """PR #246 Codex P2 review 4 巡目 B: `structure: []` is a crash-family
    member (confirmed via direct `perform()` execution) — the frozen spec
    must declare `min_items: 1` on `structure_section`."""

    spec = load_authoring_contract()
    assert spec.structure_section.min_items == 1


def test_object_spec_rejects_fields_key_not_in_allowed_keys():
    """PR #246 Codex P2 review 6 巡目: a `fields` entry outside
    `allowed_keys` (typo, e.g. `bpn` for `bpm`) previously loaded silently
    and its type/enum/literal/format/min constraints were never applied by
    `validate.py`'s `_object_errors` (which only iterates `spec.fields`,
    never cross-checking against `allowed_keys`) — now rejected at spec
    load time, same fail-closed posture as the `FieldSpec` type×constraint
    guards (round 3)."""

    with pytest.raises(ValidationError):
        ObjectSpec(allowed_keys=["bpm"], fields={"bpn": FieldSpec(type="int")})


def test_object_spec_accepts_sparse_fields_subset_of_allowed_keys():
    """Sparse `fields` (fewer entries than `allowed_keys`) stays legitimate
    — only fields declaring an unknown key are rejected, not fields that
    simply omit constraints for some allowed keys."""

    spec = ObjectSpec(allowed_keys=["bpm", "key"], fields={"bpm": FieldSpec(type="int")})
    assert set(spec.fields) == {"bpm"}


def test_object_spec_accepts_empty_fields():
    spec = ObjectSpec(allowed_keys=["a", "b"])
    assert spec.fields == {}


def test_default_contract_all_object_specs_have_fields_subset_of_allowed_keys():
    """Whole-spec sanity sweep: every ObjectSpec in the frozen default
    contract already satisfies fields ⊆ allowed_keys (this is enforced by
    the loader itself, but spelled out explicitly as a regression guard)."""

    spec = load_authoring_contract()
    for name in (
        "meta",
        "semantic",
        "grv",
        "delta_e",
        "physical",
        "structure_section",
        "rendering",
        "events",
        "chord",
    ):
        object_spec = getattr(spec, name)
        assert set(object_spec.fields) <= set(object_spec.allowed_keys), name


def test_field_spec_rejects_unknown_type():
    with pytest.raises(ValidationError):
        FieldSpec(type="float")  # type: ignore[arg-type]


def test_field_spec_accepts_min_on_int_type():
    spec = FieldSpec(type="int", min=1)
    assert spec.min == 1


def test_field_spec_rejects_min_on_non_int_type():
    """PR #246 Codex P2 review 2 巡目: `min` は `type: int` にのみ意味を持つ
    （非正整数の crash-family ゲート専用）——他の型と組み合わせた spec は
    タイプミスとして拒否する。"""

    with pytest.raises(ValidationError):
        FieldSpec(type="str", min=1)


def test_field_spec_rejects_invalid_regex_format():
    """PR #246 Codex P2 review 3 巡目: `format` は spec ロード時に
    `re.compile` で検証する——不正な正規表現（例 `'['`）は spec ロード時に
    `ValidationError` として拒否され、後段 `validate.py` の
    `re.fullmatch` が `re.error` で非構造化クラッシュするのを防ぐ。"""

    with pytest.raises(ValidationError):
        FieldSpec(type="str", format="[")


def test_field_spec_accepts_valid_regex_format():
    spec = FieldSpec(type="str", format="^[0-9]+$")
    assert spec.format == "^[0-9]+$"


def test_field_spec_rejects_format_on_non_str_type():
    """PR #246 Codex P2 review 3 巡目: `format` を `type: int` へ付与すると、
    実値（int）へ `re.fullmatch(pattern, value)` が呼ばれ `TypeError` で
    非構造化クラッシュしうる——`min` と同型のガードを `type: str` 限定へ
    拡張する。"""

    with pytest.raises(ValidationError):
        FieldSpec(type="int", format="^[0-9]+$")

    with pytest.raises(ValidationError):
        FieldSpec(type="list_str", format="^[0-9]+$")


def test_field_spec_rejects_enum_on_non_str_type():
    with pytest.raises(ValidationError):
        FieldSpec(type="int", enum=["a", "b"])


def test_field_spec_rejects_literal_on_non_str_type():
    with pytest.raises(ValidationError):
        FieldSpec(type="list_str", literal="x")


def test_field_spec_accepts_enum_literal_format_on_str_type():
    assert FieldSpec(type="str", enum=["a", "b"]).enum == ["a", "b"]
    assert FieldSpec(type="str", literal="external").literal == "external"
    assert FieldSpec(type="str", format="^[0-9]+$").format == "^[0-9]+$"


def test_packaged_contract_config_matches_repo_config(monkeypatch: pytest.MonkeyPatch):
    """`config/` <-> `src/svp_rpe/config/` sync (enforced generally by
    tests/test_config.py's dynamic glob comparison; this is a targeted
    smoke-check that the new file specifically round-trips through
    `load_config`'s packaged-resource fallback)."""

    from svp_rpe.utils import config_loader as config_loader_module

    monkeypatch.setattr(config_loader_module, "_local_config_paths", lambda name: [])
    spec = load_authoring_contract()
    assert spec.schema_version == "authoring-contract/1.0"
