"""`svprpe verify --builds-root`: recursive builds-root tree audit tests (#190 follow-up).

Fixture construction reuses existing CLI-based builders rather than
hand-crafting builds-root trees byte-by-byte:
- `tests.test_builds_root_cli`'s `SCORE_PATH` / `_write_arrangement_spec` to
  publish an `arrange --builds-root` tree (`arrangement_bundle.json`-kind
  build).
- `tests.test_observe`'s `FIXTURE_DIR` / `_build_scratch_identity_dir` (a
  scratch copy of the `examples/arrangement/midnight_signal` identity
  fixture set under `tmp_path`, never mutating the real checked-in fixtures)
  to publish a `package --builds-root` tree (`compilation_report.json`-kind
  build).
- `tests.test_verify`'s `_snapshot_tree` for the read-only guarantee.
"""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from svp_rpe.cli import app
from tests.test_builds_root_cli import SCORE_PATH, _write_arrangement_spec
from tests.test_observe import (
    BASE_SCORE,
    EDM_IDENTITY_SPEC,
    FIXTURE_DIR,
    SUNO_PROFILE_PATH,
    _build_scratch_identity_dir,
)
from tests.test_verify import _snapshot_tree


def _arrange_builds_root_args(spec_path: Path, root: Path) -> list[str]:
    return ["arrange", str(SCORE_PATH), str(spec_path), "--builds-root", str(root)]


def _package_builds_root_args(identity_yaml: Path, root: Path) -> list[str]:
    return [
        "package",
        str(BASE_SCORE),
        str(identity_yaml),
        str(EDM_IDENTITY_SPEC),
        "--capability-profile",
        str(SUNO_PROFILE_PATH),
        "--builds-root",
        str(root),
    ]


def _build_arrange_tree(tmp_path: Path, *, root_dirname: str = "root", bpm: int = 140) -> Path:
    """Publish a single `arrange --builds-root` build under `tmp_path/root_dirname`."""
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path, bpm=bpm)
    root = tmp_path / root_dirname
    result = CliRunner().invoke(app, _arrange_builds_root_args(spec_path, root))
    assert result.exit_code == 0, result.output
    return root


def _build_package_manifest(tmp_path: Path) -> Path:
    chord_bytes = (FIXTURE_DIR / "identity" / "chord_progression.json").read_bytes()
    return _build_scratch_identity_dir(tmp_path, chord_artifact_bytes=chord_bytes)


def _verify_builds_root_args(root: Path) -> list[str]:
    return ["verify", "--builds-root", str(root)]


def _flat(text: str) -> str:
    """Collapse rich console line-wrapping (mirrors `tests.test_verify._flat`)."""
    return " ".join(text.split())


# --- clean tree: both descriptor kinds, deterministic order --------------------


def test_verify_builds_root_accepts_a_clean_mixed_tree(tmp_path: Path) -> None:
    root = tmp_path / "root"
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path)
    arrange_result = CliRunner().invoke(app, _arrange_builds_root_args(spec_path, root))
    assert arrange_result.exit_code == 0, arrange_result.output

    manifest_path = _build_package_manifest(tmp_path)
    package_result = CliRunner().invoke(app, _package_builds_root_args(manifest_path, root))
    assert package_result.exit_code == 0, package_result.output

    result = CliRunner().invoke(app, _verify_builds_root_args(root))

    assert result.exit_code == 0, result.output
    assert "FAIL" not in result.output
    flat = _flat(result.output)
    assert "builds 2, checked" in flat
    assert "failed 0" in flat


def test_verify_builds_root_visits_builds_in_deterministic_sorted_order(tmp_path: Path) -> None:
    root = tmp_path / "root"
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path, bpm=140)
    first = CliRunner().invoke(app, _arrange_builds_root_args(spec_path, root))
    assert first.exit_code == 0, first.output
    _write_arrangement_spec(spec_path, bpm=150)
    second = CliRunner().invoke(app, _arrange_builds_root_args(spec_path, root))
    assert second.exit_code == 0, second.output

    digest_names = sorted(p.name for p in (root / "builds").iterdir())
    assert len(digest_names) == 2

    result = CliRunner().invoke(app, _verify_builds_root_args(root))
    assert result.exit_code == 0, result.output

    first_index = result.output.index(f"build {digest_names[0]}")
    second_index = result.output.index(f"build {digest_names[1]}")
    assert first_index < second_index


# --- tamper: undeclared file -----------------------------------------------------


def test_reports_undeclared_file_in_digest_directory(tmp_path: Path) -> None:
    root = _build_arrange_tree(tmp_path)
    digest_dir = next((root / "builds").iterdir())
    (digest_dir / "sneaky_extra.txt").write_text("not declared", encoding="utf-8")

    result = CliRunner().invoke(app, _verify_builds_root_args(root))

    assert result.exit_code == 1
    flat = _flat(result.output)
    assert "undeclared" in flat
    assert "sneaky_extra.txt" in flat


def test_reports_undeclared_subdirectory_in_digest_directory(tmp_path: Path) -> None:
    root = _build_arrange_tree(tmp_path)
    digest_dir = next((root / "builds").iterdir())
    sneaky_dir = digest_dir / "sneaky_subdir"
    sneaky_dir.mkdir()
    (sneaky_dir / "nested.txt").write_text("hidden", encoding="utf-8")

    result = CliRunner().invoke(app, _verify_builds_root_args(root))

    assert result.exit_code == 1
    flat = _flat(result.output)
    assert "sneaky_subdir" in flat
    assert "nested.txt" in flat


# --- tamper: declared artifact byte tamper --------------------------------------


def test_reports_declared_artifact_hash_mismatch(tmp_path: Path) -> None:
    root = _build_arrange_tree(tmp_path)
    digest_dir = next((root / "builds").iterdir())
    diff_path = digest_dir / "arrangement_diff.json"
    diff_path.write_bytes(diff_path.read_bytes() + b"\n")

    result = CliRunner().invoke(app, _verify_builds_root_args(root))

    assert result.exit_code == 1
    flat = _flat(result.output)
    assert "arrangement_diff.json" in flat
    assert "sha256 matches declared hash" in flat


# --- tamper: declared artifact replaced with a symlink --------------------------


def test_reports_symlinked_declared_artifact(tmp_path: Path) -> None:
    root = _build_arrange_tree(tmp_path)
    digest_dir = next((root / "builds").iterdir())
    diff_path = digest_dir / "arrangement_diff.json"
    external_copy = digest_dir.parent / "external_diff_copy.json"
    external_copy.write_bytes(diff_path.read_bytes())
    diff_path.unlink()
    diff_path.symlink_to(external_copy)

    result = CliRunner().invoke(app, _verify_builds_root_args(root))

    assert result.exit_code == 1
    flat = _flat(result.output)
    assert "arrangement_diff.json" in flat
    assert "is a regular, non-symlink file" in flat


# --- tamper: descriptive file deletion / duplication -----------------------------


def test_reports_missing_descriptive_file(tmp_path: Path) -> None:
    root = _build_arrange_tree(tmp_path)
    digest_dir = next((root / "builds").iterdir())
    (digest_dir / "arrangement_bundle.json").unlink()

    result = CliRunner().invoke(app, _verify_builds_root_args(root))

    assert result.exit_code == 1
    flat = _flat(result.output)
    assert "exactly one descriptive file present" in flat


def test_reports_duplicated_descriptive_file(tmp_path: Path) -> None:
    root = _build_arrange_tree(tmp_path)
    digest_dir = next((root / "builds").iterdir())
    (digest_dir / "compilation_report.json").write_text("{}", encoding="utf-8")

    result = CliRunner().invoke(app, _verify_builds_root_args(root))

    assert result.exit_code == 1
    flat = _flat(result.output)
    assert "exactly one descriptive file present" in flat


def test_reports_non_utf8_descriptive_file(tmp_path: Path) -> None:
    """A descriptive file with non-UTF-8 bytes is a FAIL check, not a crash

    (Codex P2, PR #204): `json.loads(bytes)` raises `UnicodeDecodeError` before
    it ever gets a chance at `json.JSONDecodeError`, so the except clause must
    catch both.
    """
    root = _build_arrange_tree(tmp_path)
    digest_dir = next((root / "builds").iterdir())
    (digest_dir / "arrangement_bundle.json").write_bytes(b"\xff\xfe{\"broken")

    result = CliRunner().invoke(app, _verify_builds_root_args(root))

    assert result.exit_code == 1
    flat = _flat(result.output)
    assert "arrangement_bundle.json parses as JSON" in flat


# --- tamper: content_digest vs. digest directory name mismatch ------------------


def test_reports_content_digest_mismatch_when_digest_directory_renamed(tmp_path: Path) -> None:
    root = _build_arrange_tree(tmp_path)
    digest_dir = next((root / "builds").iterdir())
    original_name = digest_dir.name
    new_first_char = "f" if original_name[0] != "f" else "0"
    new_name = new_first_char + original_name[1:]
    renamed_dir = digest_dir.parent / new_name
    digest_dir.rename(renamed_dir)

    result = CliRunner().invoke(app, _verify_builds_root_args(root))

    assert result.exit_code == 1
    flat = _flat(result.output)
    assert f"build {new_name}" in flat
    assert "digest directory name matches independently recomputed content_digest" in flat


# --- tamper: latest.json --------------------------------------------------------


def test_reports_unparsable_latest_json(tmp_path: Path) -> None:
    root = _build_arrange_tree(tmp_path)
    (root / "latest.json").write_text("not valid json {{{", encoding="utf-8")

    result = CliRunner().invoke(app, _verify_builds_root_args(root))

    assert result.exit_code == 1
    flat = _flat(result.output)
    assert "latest.json parses as JSON" in flat


def test_reports_non_utf8_latest_json(tmp_path: Path) -> None:
    """A `latest.json` with non-UTF-8 bytes is a FAIL check, not a crash

    (Codex P2, PR #204): `json.loads(bytes)` raises `UnicodeDecodeError` before
    it ever gets a chance at `json.JSONDecodeError`, so the except clause must
    catch both.
    """
    root = _build_arrange_tree(tmp_path)
    (root / "latest.json").write_bytes(b"\xff\xfe{\"broken")

    result = CliRunner().invoke(app, _verify_builds_root_args(root))

    assert result.exit_code == 1
    flat = _flat(result.output)
    assert "latest.json parses as JSON" in flat


def test_reports_dangling_latest_json_digest(tmp_path: Path) -> None:
    root = _build_arrange_tree(tmp_path)
    latest_path = root / "latest.json"
    latest_data = json.loads(latest_path.read_text(encoding="utf-8"))
    latest_data["content_digest"] = "a" * 64
    latest_path.write_text(json.dumps(latest_data), encoding="utf-8")

    result = CliRunner().invoke(app, _verify_builds_root_args(root))

    assert result.exit_code == 1
    flat = _flat(result.output)
    assert "corresponding builds/ digest directory" in flat


def test_reports_symlinked_latest_json(tmp_path: Path) -> None:
    root = _build_arrange_tree(tmp_path)
    latest_path = root / "latest.json"
    real_copy = root / "real_latest.json"
    real_copy.write_bytes(latest_path.read_bytes())
    latest_path.unlink()
    latest_path.symlink_to(real_copy)

    result = CliRunner().invoke(app, _verify_builds_root_args(root))

    assert result.exit_code == 1
    flat = _flat(result.output)
    assert "latest.json is present and a regular, non-symlink file" in flat


# --- tamper: builds/ direct entries ----------------------------------------------


def test_reports_non_hex_digest_directory_name(tmp_path: Path) -> None:
    root = _build_arrange_tree(tmp_path)
    bogus_dir = root / "builds" / "not-a-valid-digest-name"
    bogus_dir.mkdir()

    result = CliRunner().invoke(app, _verify_builds_root_args(root))

    assert result.exit_code == 1
    flat = _flat(result.output)
    assert "is a valid 64-hex content_digest name" in flat


def test_reports_placeholder_digest_directory(tmp_path: Path) -> None:
    root = _build_arrange_tree(tmp_path)
    placeholder_dir = root / "builds" / ("0" * 64)
    placeholder_dir.mkdir()

    result = CliRunner().invoke(app, _verify_builds_root_args(root))

    assert result.exit_code == 1
    flat = _flat(result.output)
    assert "reserved locator-placeholder digest" in flat


# --- tamper: root-level unexpected entry -----------------------------------------


def test_reports_unexpected_root_level_entry(tmp_path: Path) -> None:
    root = _build_arrange_tree(tmp_path)
    (root / "stray.txt").write_text("stray", encoding="utf-8")

    result = CliRunner().invoke(app, _verify_builds_root_args(root))

    assert result.exit_code == 1
    flat = _flat(result.output)
    assert "no unexpected root entry" in flat
    assert "stray.txt" in flat


# --- mutual exclusion ------------------------------------------------------------


def test_verify_cli_rejects_builds_root_together_with_package_argument(tmp_path: Path) -> None:
    root = _build_arrange_tree(tmp_path)

    result = CliRunner().invoke(app, ["verify", "some/package.json", "--builds-root", str(root)])

    assert result.exit_code == 2


def test_verify_cli_rejects_builds_root_together_with_manifest(tmp_path: Path) -> None:
    root = _build_arrange_tree(tmp_path)
    manifest_path = tmp_path / "unused_manifest.yaml"
    manifest_path.write_text("unused", encoding="utf-8")

    result = CliRunner().invoke(
        app, ["verify", "--builds-root", str(root), "--manifest", str(manifest_path)]
    )

    assert result.exit_code == 2


def test_verify_cli_rejects_neither_package_nor_builds_root(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["verify"])

    assert result.exit_code == 2


def test_verify_cli_rejects_package_argument_without_manifest(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["verify", "some/package.json"])

    assert result.exit_code == 2


def test_verify_cli_rejects_manifest_without_package_argument(tmp_path: Path) -> None:
    manifest_path = tmp_path / "unused_manifest.yaml"
    manifest_path.write_text("unused", encoding="utf-8")

    result = CliRunner().invoke(app, ["verify", "--manifest", str(manifest_path)])

    assert result.exit_code == 2


# --- read-only guarantee ----------------------------------------------------------


def test_verify_builds_root_is_read_only(tmp_path: Path) -> None:
    """Byte-identical before/after, even against a tampered (failing) tree —
    an audit run must never repair or otherwise mutate anything it inspects."""
    root = _build_arrange_tree(tmp_path)
    digest_dir = next((root / "builds").iterdir())
    (digest_dir / "sneaky_extra.txt").write_text("not declared", encoding="utf-8")
    before = _snapshot_tree(tmp_path)

    result = CliRunner().invoke(app, _verify_builds_root_args(root))
    assert result.exit_code == 1

    after = _snapshot_tree(tmp_path)
    assert before == after
