"""IdentityManifest schema + hash 付き loader のテスト (AR2-1)。"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from svp_rpe.arrange import (
    IdentityAnchor,
    IdentityManifest,
    IdentityManifestError,
    load_identity_manifest,
)
from svp_rpe.arrange.identity import IdentityMeta, IdentitySource

VALID_SHA256 = "0" * 64


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_artifacts(tmp_path: Path) -> dict[str, bytes]:
    contents = {
        "source.wav": b"source-audio-bytes",
        "lyrics.txt": b"lyrics transcript content",
        "melody.mid": b"melody-midi-bytes",
        "harmony.txt": b"harmony chord list",
        "structure.yaml": b"structure: intro-verse-chorus",
    }
    for name, data in contents.items():
        (tmp_path / name).write_bytes(data)
    return contents


def _manifest_dict(contents: dict[str, bytes], *, work_id: str = "work-1") -> dict[str, Any]:
    return {
        "meta": {"work_id": work_id, "version": "1"},
        "source": {
            "locator": "source.wav",
            "sha256": _sha256(contents["source.wav"]),
            "rights_basis": "original",
        },
        "anchors": [
            {
                "id": "anchor-lyrics",
                "domain": "lyrics",
                "artifact": "lyrics.txt",
                "sha256": _sha256(contents["lyrics.txt"]),
                "required": True,
            },
            {
                "id": "anchor-melody",
                "domain": "melody",
                "artifact": "melody.mid",
                "sha256": _sha256(contents["melody.mid"]),
                "required": True,
            },
            {
                "id": "anchor-harmony",
                "domain": "harmony",
                "artifact": "harmony.txt",
                "sha256": _sha256(contents["harmony.txt"]),
                "required": False,
            },
            {
                "id": "anchor-structure",
                "domain": "structure",
                "artifact": "structure.yaml",
                "sha256": _sha256(contents["structure.yaml"]),
                "section_ref": "verse-1",
                "required": False,
            },
        ],
    }


def _write_manifest(path: Path, data: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


# --- happy path -------------------------------------------------------------


def test_load_identity_manifest_succeeds_with_all_anchor_domains(tmp_path: Path) -> None:
    contents = _write_artifacts(tmp_path)
    manifest_dict = _manifest_dict(contents)
    manifest_path = tmp_path / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    manifest = load_identity_manifest(manifest_path)

    assert manifest.meta.work_id == "work-1"
    assert manifest.source.locator == "source.wav"
    assert manifest.source.rights_basis == "original"
    assert len(manifest.anchors) == 4
    domains = {anchor.domain for anchor in manifest.anchors}
    assert domains == {"lyrics", "melody", "harmony", "structure"}


def test_note_and_section_ref_are_optional_and_preserved(tmp_path: Path) -> None:
    contents = _write_artifacts(tmp_path)
    manifest_dict = _manifest_dict(contents)
    manifest_dict["source"]["note"] = "recorded 2026"
    manifest_path = tmp_path / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    manifest = load_identity_manifest(manifest_path)

    assert manifest.source.note == "recorded 2026"
    structure_anchor = next(a for a in manifest.anchors if a.id == "anchor-structure")
    assert structure_anchor.section_ref == "verse-1"
    lyrics_anchor = next(a for a in manifest.anchors if a.id == "anchor-lyrics")
    assert lyrics_anchor.section_ref is None


# --- hash mismatch ------------------------------------------------------------


def test_source_sha256_mismatch_raises_identity_manifest_error(tmp_path: Path) -> None:
    contents = _write_artifacts(tmp_path)
    manifest_dict = _manifest_dict(contents)
    manifest_dict["source"]["sha256"] = VALID_SHA256
    manifest_path = tmp_path / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    with pytest.raises(IdentityManifestError) as exc_info:
        load_identity_manifest(manifest_path)

    message = str(exc_info.value)
    assert "work-1" in message
    assert "source" in message


def test_anchor_sha256_mismatch_raises_identity_manifest_error_with_anchor_id(
    tmp_path: Path,
) -> None:
    contents = _write_artifacts(tmp_path)
    manifest_dict = _manifest_dict(contents)
    manifest_dict["anchors"][0]["sha256"] = VALID_SHA256
    manifest_path = tmp_path / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    with pytest.raises(IdentityManifestError) as exc_info:
        load_identity_manifest(manifest_path)

    message = str(exc_info.value)
    assert "work-1" in message
    assert "anchor-lyrics" in message


# --- missing / directory artifact --------------------------------------------


def test_missing_artifact_path_raises_identity_manifest_error(tmp_path: Path) -> None:
    contents = _write_artifacts(tmp_path)
    manifest_dict = _manifest_dict(contents)
    manifest_dict["anchors"][0]["artifact"] = "does-not-exist.txt"
    manifest_path = tmp_path / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    with pytest.raises(IdentityManifestError) as exc_info:
        load_identity_manifest(manifest_path)

    assert "anchor-lyrics" in str(exc_info.value)


def test_directory_artifact_path_raises_identity_manifest_error(tmp_path: Path) -> None:
    contents = _write_artifacts(tmp_path)
    manifest_dict = _manifest_dict(contents)
    (tmp_path / "a_directory").mkdir()
    manifest_dict["anchors"][0]["artifact"] = "a_directory"
    manifest_path = tmp_path / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    with pytest.raises(IdentityManifestError) as exc_info:
        load_identity_manifest(manifest_path)

    assert "anchor-lyrics" in str(exc_info.value)


# --- path confinement (manifest 可搬性契約) -------------------------------------


def test_absolute_locator_path_is_rejected(tmp_path: Path) -> None:
    contents = _write_artifacts(tmp_path)
    manifest_dict = _manifest_dict(contents)
    absolute_locator = str(tmp_path / "source.wav")
    manifest_dict["source"]["locator"] = absolute_locator
    manifest_path = tmp_path / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    with pytest.raises(IdentityManifestError) as exc_info:
        load_identity_manifest(manifest_path)

    message = str(exc_info.value)
    assert "work-1" in message
    assert absolute_locator in message


def test_parent_escaping_artifact_is_rejected_even_with_correct_hash(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "manifest_dir"
    manifest_dir.mkdir()
    contents = _write_artifacts(manifest_dir)
    outside_bytes = b"outside artifact bytes"
    (tmp_path / "outside.txt").write_bytes(outside_bytes)

    manifest_dict = _manifest_dict(contents)
    manifest_dict["anchors"][0]["artifact"] = "../outside.txt"
    manifest_dict["anchors"][0]["sha256"] = _sha256(outside_bytes)  # hash は正しい
    manifest_path = manifest_dir / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    with pytest.raises(IdentityManifestError) as exc_info:
        load_identity_manifest(manifest_path)

    message = str(exc_info.value)
    assert "anchor-lyrics" in message
    assert "../outside.txt" in message


def test_symlink_escaping_manifest_directory_is_rejected(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "manifest_dir"
    manifest_dir.mkdir()
    contents = _write_artifacts(manifest_dir)
    outside_bytes = b"outside artifact bytes"
    outside_file = tmp_path / "outside.txt"
    outside_file.write_bytes(outside_bytes)
    (manifest_dir / "sneaky_link.txt").symlink_to(outside_file)

    manifest_dict = _manifest_dict(contents)
    manifest_dict["anchors"][0]["artifact"] = "sneaky_link.txt"
    manifest_dict["anchors"][0]["sha256"] = _sha256(outside_bytes)  # hash は正しい
    manifest_path = manifest_dir / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    with pytest.raises(IdentityManifestError) as exc_info:
        load_identity_manifest(manifest_path)

    assert "anchor-lyrics" in str(exc_info.value)


def test_nested_relative_artifact_path_loads(tmp_path: Path) -> None:
    contents = _write_artifacts(tmp_path)
    subdir = tmp_path / "identity"
    subdir.mkdir()
    nested_bytes = b"nested lyrics content"
    (subdir / "lyrics.txt").write_bytes(nested_bytes)

    manifest_dict = _manifest_dict(contents)
    manifest_dict["anchors"][0]["artifact"] = "identity/lyrics.txt"
    manifest_dict["anchors"][0]["sha256"] = _sha256(nested_bytes)
    manifest_path = tmp_path / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    manifest = load_identity_manifest(manifest_path)

    lyrics_anchor = next(a for a in manifest.anchors if a.id == "anchor-lyrics")
    assert lyrics_anchor.artifact == "identity/lyrics.txt"


# --- schema validation ---------------------------------------------------------


def test_duplicate_anchor_id_raises_validation_error(tmp_path: Path) -> None:
    contents = _write_artifacts(tmp_path)
    manifest_dict = _manifest_dict(contents)
    manifest_dict["anchors"][1]["id"] = manifest_dict["anchors"][0]["id"]
    manifest_path = tmp_path / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    with pytest.raises(ValidationError):
        load_identity_manifest(manifest_path)


def test_unknown_anchor_domain_raises_validation_error(tmp_path: Path) -> None:
    contents = _write_artifacts(tmp_path)
    manifest_dict = _manifest_dict(contents)
    manifest_dict["anchors"][0]["domain"] = "timbre"
    manifest_path = tmp_path / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    with pytest.raises(ValidationError):
        load_identity_manifest(manifest_path)


def test_unknown_rights_basis_raises_validation_error(tmp_path: Path) -> None:
    contents = _write_artifacts(tmp_path)
    manifest_dict = _manifest_dict(contents)
    manifest_dict["source"]["rights_basis"] = "guessed"
    manifest_path = tmp_path / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    with pytest.raises(ValidationError):
        load_identity_manifest(manifest_path)


@pytest.mark.parametrize(
    "bad_sha256",
    [
        "0" * 63,  # too short
        "0" * 65,  # too long
        "A" * 64,  # uppercase not allowed
        "g" * 64,  # non-hex char
    ],
)
def test_malformed_sha256_raises_validation_error(tmp_path: Path, bad_sha256: str) -> None:
    contents = _write_artifacts(tmp_path)
    manifest_dict = _manifest_dict(contents)
    manifest_dict["source"]["sha256"] = bad_sha256
    manifest_path = tmp_path / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    with pytest.raises(ValidationError):
        load_identity_manifest(manifest_path)


def test_unknown_top_level_key_raises_validation_error(tmp_path: Path) -> None:
    contents = _write_artifacts(tmp_path)
    manifest_dict = _manifest_dict(contents)
    manifest_dict["unexpected"] = "value"
    manifest_path = tmp_path / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    with pytest.raises(ValidationError):
        load_identity_manifest(manifest_path)


def test_unknown_anchor_key_raises_validation_error(tmp_path: Path) -> None:
    contents = _write_artifacts(tmp_path)
    manifest_dict = _manifest_dict(contents)
    manifest_dict["anchors"][0]["unexpected"] = "value"
    manifest_path = tmp_path / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    with pytest.raises(ValidationError):
        load_identity_manifest(manifest_path)


def test_missing_manifest_path_raises_identity_manifest_error(tmp_path: Path) -> None:
    manifest_path = tmp_path / "does-not-exist.yaml"

    with pytest.raises(IdentityManifestError) as exc_info:
        load_identity_manifest(manifest_path)

    assert str(manifest_path) in str(exc_info.value)


def test_directory_manifest_path_raises_identity_manifest_error(tmp_path: Path) -> None:
    manifest_path = tmp_path / "a_directory"
    manifest_path.mkdir()

    with pytest.raises(IdentityManifestError) as exc_info:
        load_identity_manifest(manifest_path)

    assert str(manifest_path) in str(exc_info.value)


def test_non_mapping_yaml_raises_value_error(tmp_path: Path) -> None:
    manifest_path = tmp_path / "identity.yaml"
    manifest_path.write_text(yaml.safe_dump([1, 2, 3]), encoding="utf-8")

    with pytest.raises(ValueError):
        load_identity_manifest(manifest_path)


# --- determinism / relative path resolution ------------------------------------


def test_repeated_load_is_deterministic(tmp_path: Path) -> None:
    contents = _write_artifacts(tmp_path)
    manifest_dict = _manifest_dict(contents)
    manifest_path = tmp_path / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    first = load_identity_manifest(manifest_path)
    second = load_identity_manifest(manifest_path)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_relative_paths_resolve_against_manifest_parent_not_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contents = _write_artifacts(tmp_path)
    manifest_dict = _manifest_dict(contents)
    manifest_path = tmp_path / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    manifest = load_identity_manifest(manifest_path)

    assert manifest.meta.work_id == "work-1"


# --- compose independence pin ---------------------------------------------------


def test_identity_module_does_not_import_compose() -> None:
    source = Path("src/svp_rpe/arrange/identity.py").read_text(encoding="utf-8")
    import_lines = [
        line
        for line in source.splitlines()
        if line.startswith("import ") or line.startswith("from ")
    ]
    assert not any("svp_rpe.compose" in line for line in import_lines)


# --- construction without loader (schema-level) --------------------------------


def test_identity_manifest_direct_construction_roundtrips() -> None:
    manifest = IdentityManifest(
        meta=IdentityMeta(work_id="w1", version="1"),
        source=IdentitySource(
            locator="source.wav", sha256=VALID_SHA256, rights_basis="unknown"
        ),
        anchors=[
            IdentityAnchor(
                id="a1",
                domain="motif",
                artifact="motif.mid",
                sha256=VALID_SHA256,
                required=True,
            )
        ],
    )

    dumped = manifest.model_dump(mode="json")
    assert dumped["source"]["rights_basis"] == "unknown"
    assert dumped["anchors"][0]["domain"] == "motif"
