"""IdentityManifest schema + hash 付き loader のテスト (AR2-1)。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from svp_rpe.arrange import (
    IdentityAnchor,
    IdentityManifest,
    IdentityManifestError,
    is_harmony_sensor_anchor,
    load_identity_manifest,
    parse_identity_manifest_with_artifacts,
)
from svp_rpe.arrange.identity import IdentityMeta, IdentitySource

VALID_SHA256 = "0" * 64

ANCHOR_ARTIFACT_META = {
    "lyrics.txt": {"artifact_type": "lyrics_text", "media_type": "text/plain"},
    "melody.mid": {"artifact_type": "midi_clip", "media_type": "audio/midi"},
    "harmony.txt": {
        "artifact_type": "chord_sequence_json",
        "media_type": "application/json",
    },
    "structure.yaml": {"artifact_type": "section_map", "media_type": "application/x-yaml"},
    "motif.mid": {"artifact_type": "midi_clip", "media_type": "audio/midi"},
}


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
        "schema_version": "identity-manifest/0.1",
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
                **ANCHOR_ARTIFACT_META["lyrics.txt"],
                "sha256": _sha256(contents["lyrics.txt"]),
                "required": True,
            },
            {
                "id": "anchor-melody",
                "domain": "melody",
                "artifact": "melody.mid",
                **ANCHOR_ARTIFACT_META["melody.mid"],
                "sha256": _sha256(contents["melody.mid"]),
                "required": True,
            },
            {
                "id": "anchor-harmony",
                "domain": "harmony",
                "artifact": "harmony.txt",
                **ANCHOR_ARTIFACT_META["harmony.txt"],
                "sha256": _sha256(contents["harmony.txt"]),
                "required": False,
            },
            {
                "id": "anchor-structure",
                "domain": "structure",
                "artifact": "structure.yaml",
                **ANCHOR_ARTIFACT_META["structure.yaml"],
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
    lyrics_anchor = next(a for a in manifest.anchors if a.id == "anchor-lyrics")
    assert lyrics_anchor.artifact_type == "lyrics_text"
    assert lyrics_anchor.media_type == "text/plain"
    assert lyrics_anchor.format_version is None


def test_parse_identity_manifest_with_artifacts_collect_none_retains_nothing(
    tmp_path: Path,
) -> None:
    """PR #187 review round 11: `collect=None` (what `parse_identity_manifest`
    / `load_identity_manifest` pass) reads and hash-verifies every anchor's
    bytes — the artifact content is still checked — but retains none of them
    in the returned dict. The manifest-only path must not carry
    O(total anchor bytes) in memory just because a caller somewhere else
    wants a subset of it."""
    contents = _write_artifacts(tmp_path)
    manifest_dict = _manifest_dict(contents)
    manifest_path = tmp_path / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    manifest, artifact_bytes = parse_identity_manifest_with_artifacts(
        manifest_path.read_bytes(), manifest_path, collect=None
    )

    assert len(manifest.anchors) == 4
    assert artifact_bytes == {}


def test_parse_identity_manifest_with_artifacts_collect_predicate_selects_anchors(
    tmp_path: Path,
) -> None:
    """`collect` retains only the anchors it selects. Using the same
    predicate `svprpe observe` passes (`is_harmony_sensor_anchor`): only the
    harmony/chord_sequence_json anchor's bytes come back, not the
    lyrics/melody/structure anchors' (midi_clip / lyrics_text / section_map)
    even though all four are read and hash-verified."""
    contents = _write_artifacts(tmp_path)
    manifest_dict = _manifest_dict(contents)
    manifest_path = tmp_path / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    manifest, artifact_bytes = parse_identity_manifest_with_artifacts(
        manifest_path.read_bytes(), manifest_path, collect=is_harmony_sensor_anchor
    )

    assert len(manifest.anchors) == 4
    assert set(artifact_bytes) == {"anchor-harmony"}
    assert artifact_bytes["anchor-harmony"] == contents["harmony.txt"]


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


# --- AR2-3: section_ref resolution against section-map/0.2 structure anchors ----


def _write_section_map_v2(
    tmp_path: Path, entries: list[tuple[str, str]], *, filename: str
) -> bytes:
    payload = {
        "schema_version": "section-map/0.2",
        "sections": [{"id": section_id, "label": label} for section_id, label in entries],
    }
    data = json.dumps(payload).encode("utf-8")
    (tmp_path / filename).write_bytes(data)
    return data


def _section_ref_manifest_dict(
    *,
    source_contents: bytes,
    lyrics_contents: bytes,
    lyrics_section_ref: str | None,
    structure_anchors: list[dict[str, Any]],
    work_id: str = "work-1",
) -> dict[str, Any]:
    """Minimal manifest dict dedicated to `section_ref` resolution tests — kept
    separate from `_manifest_dict` (whose `anchor-structure` always declares
    `section_ref: "verse-1"` with no `format_version`) so adding a
    section-map/0.2 structure anchor to a test manifest never accidentally
    makes that unrelated, pre-existing `section_ref` value subject to the new
    resolution the anchor's presence now triggers."""
    return {
        "schema_version": "identity-manifest/0.1",
        "meta": {"work_id": work_id, "version": "1"},
        "source": {
            "locator": "source.wav",
            "sha256": _sha256(source_contents),
            "rights_basis": "original",
        },
        "anchors": [
            {
                "id": "anchor-lyrics",
                "domain": "lyrics",
                "artifact": "lyrics.txt",
                "artifact_type": "lyrics_text",
                "media_type": "text/plain",
                "sha256": _sha256(lyrics_contents),
                "section_ref": lyrics_section_ref,
                "required": True,
            },
            *structure_anchors,
        ],
    }


def _write_section_ref_scenario(
    tmp_path: Path,
    *,
    lyrics_section_ref: str | None,
    section_maps: list[list[tuple[str, str]]],
) -> Path:
    """Write a minimal manifest + artifacts with one lyrics anchor (declaring
    `lyrics_section_ref`) and one section-map/0.2 structure anchor per entry
    in `section_maps` (each a list of `(id, label)` pairs)."""
    source_contents = b"source-audio-bytes"
    lyrics_contents = b"lyrics transcript content"
    (tmp_path / "source.wav").write_bytes(source_contents)
    (tmp_path / "lyrics.txt").write_bytes(lyrics_contents)

    structure_anchors: list[dict[str, Any]] = []
    for index, entries in enumerate(section_maps):
        filename = f"section_map_v2_{index}.json"
        artifact_bytes = _write_section_map_v2(tmp_path, entries, filename=filename)
        structure_anchors.append(
            {
                "id": f"anchor-structure-v2-{index}",
                "domain": "structure",
                "artifact": filename,
                "artifact_type": "section_map",
                "media_type": "application/json",
                "format_version": "section-map/0.2",
                "sha256": _sha256(artifact_bytes),
                "required": False,
            }
        )

    manifest_dict = _section_ref_manifest_dict(
        source_contents=source_contents,
        lyrics_contents=lyrics_contents,
        lyrics_section_ref=lyrics_section_ref,
        structure_anchors=structure_anchors,
    )
    manifest_path = tmp_path / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)
    return manifest_path


def test_section_ref_resolves_against_single_0_2_structure_anchor(tmp_path: Path) -> None:
    manifest_path = _write_section_ref_scenario(
        tmp_path,
        lyrics_section_ref="sec-verse",
        section_maps=[[("sec-intro", "intro"), ("sec-verse", "verse")]],
    )

    manifest = load_identity_manifest(manifest_path)

    lyrics_anchor = next(a for a in manifest.anchors if a.id == "anchor-lyrics")
    assert lyrics_anchor.section_ref == "sec-verse"


def test_section_ref_dangling_reference_raises_when_0_2_anchor_present(
    tmp_path: Path,
) -> None:
    manifest_path = _write_section_ref_scenario(
        tmp_path,
        lyrics_section_ref="sec-does-not-exist",
        section_maps=[[("sec-intro", "intro"), ("sec-verse", "verse")]],
    )

    with pytest.raises(IdentityManifestError) as exc_info:
        load_identity_manifest(manifest_path)

    message = str(exc_info.value)
    assert "work-1" in message
    assert "anchor-lyrics" in message
    assert "sec-does-not-exist" in message


def test_section_ref_stays_opaque_without_any_0_2_structure_anchor(tmp_path: Path) -> None:
    """No section-map/0.2 structure anchor at all: an unresolvable-looking
    `section_ref` is left untouched (opaque), matching pre-AR2-3 behaviour."""
    manifest_path = _write_section_ref_scenario(
        tmp_path,
        lyrics_section_ref="this-id-does-not-exist-anywhere",
        section_maps=[],
    )

    manifest = load_identity_manifest(manifest_path)

    lyrics_anchor = next(a for a in manifest.anchors if a.id == "anchor-lyrics")
    assert lyrics_anchor.section_ref == "this-id-does-not-exist-anywhere"


def test_section_ref_resolves_across_merged_ids_from_multiple_0_2_structure_anchors(
    tmp_path: Path,
) -> None:
    manifest_path = _write_section_ref_scenario(
        tmp_path,
        lyrics_section_ref="sec-chorus",
        section_maps=[
            [("sec-intro", "intro"), ("sec-verse", "verse")],
            [("sec-chorus", "chorus"), ("sec-outro", "outro")],
        ],
    )

    manifest = load_identity_manifest(manifest_path)

    lyrics_anchor = next(a for a in manifest.anchors if a.id == "anchor-lyrics")
    assert lyrics_anchor.section_ref == "sec-chorus"


def test_section_ref_duplicate_id_across_multiple_0_2_structure_anchors_raises(
    tmp_path: Path,
) -> None:
    manifest_path = _write_section_ref_scenario(
        tmp_path,
        lyrics_section_ref=None,
        section_maps=[
            [("sec-intro", "intro"), ("sec-verse", "verse")],
            [("sec-verse", "verse-again"), ("sec-outro", "outro")],
        ],
    )

    with pytest.raises(IdentityManifestError) as exc_info:
        load_identity_manifest(manifest_path)

    message = str(exc_info.value)
    assert "work-1" in message
    assert "sec-verse" in message


def test_section_ref_none_is_never_validated_even_with_0_2_anchor_present(
    tmp_path: Path,
) -> None:
    """`section_ref: null` (no reference declared) is always fine, regardless
    of whether the manifest has a section-map/0.2 structure anchor — only
    *non-None* `section_ref` values are resolved/validated."""
    manifest_path = _write_section_ref_scenario(
        tmp_path,
        lyrics_section_ref=None,
        section_maps=[[("sec-intro", "intro")]],
    )

    manifest = load_identity_manifest(manifest_path)

    lyrics_anchor = next(a for a in manifest.anchors if a.id == "anchor-lyrics")
    assert lyrics_anchor.section_ref is None


def test_section_ref_own_structure_anchor_is_also_resolved(tmp_path: Path) -> None:
    """A structure anchor's own `section_ref` (e.g. one 0.2 section map
    referencing an id declared by another) is resolved too — resolution
    applies to every anchor's `section_ref`, not just non-structure anchors."""
    source_contents = b"source-audio-bytes"
    lyrics_contents = b"lyrics transcript content"
    (tmp_path / "source.wav").write_bytes(source_contents)
    (tmp_path / "lyrics.txt").write_bytes(lyrics_contents)
    artifact_bytes = _write_section_map_v2(
        tmp_path, [("sec-intro", "intro"), ("sec-verse", "verse")], filename="section_map_v2.json"
    )

    manifest_dict = {
        "schema_version": "identity-manifest/0.1",
        "meta": {"work_id": "work-1", "version": "1"},
        "source": {
            "locator": "source.wav",
            "sha256": _sha256(source_contents),
            "rights_basis": "original",
        },
        "anchors": [
            {
                "id": "anchor-lyrics",
                "domain": "lyrics",
                "artifact": "lyrics.txt",
                "artifact_type": "lyrics_text",
                "media_type": "text/plain",
                "sha256": _sha256(lyrics_contents),
                "required": True,
            },
            {
                "id": "anchor-structure-v2",
                "domain": "structure",
                "artifact": "section_map_v2.json",
                "artifact_type": "section_map",
                "media_type": "application/json",
                "format_version": "section-map/0.2",
                "sha256": _sha256(artifact_bytes),
                "section_ref": "sec-intro",
                "required": False,
            },
        ],
    }
    manifest_path = tmp_path / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    manifest = load_identity_manifest(manifest_path)

    structure_anchor = next(a for a in manifest.anchors if a.id == "anchor-structure-v2")
    assert structure_anchor.section_ref == "sec-intro"


def test_section_map_v2_anchor_with_invalid_artifact_content_raises_identity_manifest_error(
    tmp_path: Path,
) -> None:
    """A declared section-map/0.2 structure anchor whose artifact content
    fails `SectionMapArtifactV2` validation (here: a duplicate section id)
    surfaces as `IdentityManifestError` — this module's existing error
    contract — not a raw `ValueError`/`ValidationError` leaking from the
    shared `arrange.section_map` parser."""
    source_contents = b"source-audio-bytes"
    lyrics_contents = b"lyrics transcript content"
    (tmp_path / "source.wav").write_bytes(source_contents)
    (tmp_path / "lyrics.txt").write_bytes(lyrics_contents)
    bad_payload = {
        "schema_version": "section-map/0.2",
        "sections": [
            {"id": "sec-1", "label": "intro"},
            {"id": "sec-1", "label": "verse"},
        ],
    }
    artifact_bytes = json.dumps(bad_payload).encode("utf-8")
    (tmp_path / "section_map_v2.json").write_bytes(artifact_bytes)

    manifest_dict = _section_ref_manifest_dict(
        source_contents=source_contents,
        lyrics_contents=lyrics_contents,
        lyrics_section_ref=None,
        structure_anchors=[
            {
                "id": "anchor-structure-v2",
                "domain": "structure",
                "artifact": "section_map_v2.json",
                "artifact_type": "section_map",
                "media_type": "application/json",
                "format_version": "section-map/0.2",
                "sha256": _sha256(artifact_bytes),
                "required": False,
            }
        ],
    )
    manifest_path = tmp_path / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    with pytest.raises(IdentityManifestError) as exc_info:
        load_identity_manifest(manifest_path)

    message = str(exc_info.value)
    assert "anchor-structure-v2" in message
    assert "sec-1" in message


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


def test_missing_identity_manifest_schema_version_raises_validation_error(
    tmp_path: Path,
) -> None:
    contents = _write_artifacts(tmp_path)
    manifest_dict = _manifest_dict(contents)
    manifest_dict.pop("schema_version")
    manifest_path = tmp_path / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    with pytest.raises(ValidationError) as exc_info:
        load_identity_manifest(manifest_path)

    assert "schema_version" in str(exc_info.value)


def test_unknown_identity_manifest_schema_version_raises_validation_error(
    tmp_path: Path,
) -> None:
    contents = _write_artifacts(tmp_path)
    manifest_dict = _manifest_dict(contents)
    manifest_dict["schema_version"] = "identity-manifest/0.2"
    manifest_path = tmp_path / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    with pytest.raises(ValidationError) as exc_info:
        load_identity_manifest(manifest_path)

    assert "identity-manifest/0.2" in str(exc_info.value)


def test_unknown_anchor_domain_raises_validation_error(tmp_path: Path) -> None:
    contents = _write_artifacts(tmp_path)
    manifest_dict = _manifest_dict(contents)
    manifest_dict["anchors"][0]["domain"] = "timbre"
    manifest_path = tmp_path / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    with pytest.raises(ValidationError):
        load_identity_manifest(manifest_path)


def test_unknown_anchor_artifact_type_raises_validation_error(tmp_path: Path) -> None:
    contents = _write_artifacts(tmp_path)
    manifest_dict = _manifest_dict(contents)
    manifest_dict["anchors"][0]["artifact_type"] = "pdf_score"
    manifest_path = tmp_path / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    with pytest.raises(ValidationError) as exc_info:
        load_identity_manifest(manifest_path)

    assert "pdf_score" in str(exc_info.value)


@pytest.mark.parametrize("missing_key", ["artifact_type", "media_type"])
def test_anchor_artifact_metadata_is_required(
    tmp_path: Path, missing_key: str
) -> None:
    contents = _write_artifacts(tmp_path)
    manifest_dict = _manifest_dict(contents)
    manifest_dict["anchors"][0].pop(missing_key)
    manifest_path = tmp_path / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    with pytest.raises(ValidationError) as exc_info:
        load_identity_manifest(manifest_path)

    assert missing_key in str(exc_info.value)


def test_empty_anchor_media_type_raises_validation_error(tmp_path: Path) -> None:
    contents = _write_artifacts(tmp_path)
    manifest_dict = _manifest_dict(contents)
    manifest_dict["anchors"][0]["media_type"] = ""
    manifest_path = tmp_path / "identity.yaml"
    _write_manifest(manifest_path, manifest_dict)

    with pytest.raises(ValidationError) as exc_info:
        load_identity_manifest(manifest_path)

    assert "media_type" in str(exc_info.value)


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
        schema_version="identity-manifest/0.1",
        meta=IdentityMeta(work_id="w1", version="1"),
        source=IdentitySource(
            locator="source.wav", sha256=VALID_SHA256, rights_basis="unknown"
        ),
        anchors=[
            IdentityAnchor(
                id="a1",
                domain="motif",
                artifact="motif.mid",
                **ANCHOR_ARTIFACT_META["motif.mid"],
                sha256=VALID_SHA256,
                required=True,
            )
        ],
    )

    dumped = manifest.model_dump(mode="json")
    assert dumped["source"]["rights_basis"] == "unknown"
    assert dumped["anchors"][0]["domain"] == "motif"
