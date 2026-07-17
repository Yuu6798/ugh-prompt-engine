"""`--builds-root` の不変 builds/<content_digest>/ 出力テスト (item 11)。

`svprpe arrange` / `svprpe package` 双方に共通の契約を検証する:
- `--output-dir` / `--builds-root` は相互排他かつどちらか必須（違反は exit 2）
- 初回公開は `<root>/builds/<content_digest>/` へ全成果物を書き、`<root>/latest.json`
  が指す digest を更新する
- 既に公開済みの digest ディレクトリは一切触れない（immutable no-op 再公開）
- staging はディレクトリ単位の `os.rename` で公開される（途中失敗で digest
  ディレクトリが生成されない）
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from svp_rpe.cli import app

SCORE_PATH = Path("examples/composition/midnight_signal/composition_score.yaml")
SUNO_PROFILE_PATH = Path("config/capability_profiles/suno.yaml")


# --- arrange fixtures --------------------------------------------------------


def _write_arrangement_spec(path: Path, *, bpm: int = 140) -> None:
    spec = {
        "meta": {"id": "arr-builds-root", "version": "1"},
        "target": {"physical": {"bpm": bpm, "brightness": "bright"}},
        "preservation": {
            "score_fields": {"physical.bpm": "elastic", "physical.brightness": "free"}
        },
    }
    path.write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _arrange_args(spec_path: Path, *, mode_flag: str, mode_value: str) -> list[str]:
    return [
        "arrange",
        str(SCORE_PATH),
        str(spec_path),
        f"--{mode_flag}",
        mode_value,
    ]


# --- package fixtures ---------------------------------------------------------


def _write_package_fixture(tmp_path: Path) -> tuple[Path, Path]:
    source_path = tmp_path / "source.wav"
    source_path.write_bytes(b"source audio")
    artifact_path = tmp_path / "lyrics.txt"
    artifact_path.write_bytes(b"hello midnight")

    manifest_path = tmp_path / "identity.yaml"
    manifest_data = {
        "schema_version": "identity-manifest/0.1",
        "meta": {"work_id": "builds-root-package", "version": "1"},
        "source": {
            "locator": source_path.name,
            "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            "rights_basis": "original",
        },
        "anchors": [
            {
                "id": "lyrics",
                "domain": "lyrics",
                "artifact": artifact_path.name,
                "artifact_type": "lyrics_text",
                "media_type": "text/plain",
                "sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
                "required": True,
            }
        ],
    }
    manifest_path.write_text(yaml.safe_dump(manifest_data, sort_keys=False), encoding="utf-8")

    spec_path = tmp_path / "arrangement.yaml"
    spec_path.write_text(
        yaml.safe_dump(
            {
                "meta": {"id": "package-builds-root", "version": "1"},
                "target": {},
                "preservation": {
                    "score_fields": {},
                    "identity_anchors": {"lyrics": {"mode": "hard", "allow": []}},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return manifest_path, spec_path


def _package_args(
    manifest_path: Path, spec_path: Path, *, mode_flag: str, mode_value: str
) -> list[str]:
    return [
        "package",
        str(SCORE_PATH),
        str(manifest_path),
        str(spec_path),
        "--capability-profile",
        str(SUNO_PROFILE_PATH),
        f"--{mode_flag}",
        mode_value,
    ]


# --- mutual exclusion (both commands) ----------------------------------------


def test_arrange_requires_exactly_one_of_output_dir_or_builds_root(tmp_path: Path) -> None:
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path)

    neither = CliRunner().invoke(app, ["arrange", str(SCORE_PATH), str(spec_path)])
    assert neither.exit_code == 2

    both = CliRunner().invoke(
        app,
        [
            "arrange",
            str(SCORE_PATH),
            str(spec_path),
            "--output-dir",
            str(tmp_path / "out"),
            "--builds-root",
            str(tmp_path / "builds-root"),
        ],
    )
    assert both.exit_code == 2


def test_package_requires_exactly_one_of_output_dir_or_builds_root(tmp_path: Path) -> None:
    manifest_path, spec_path = _write_package_fixture(tmp_path)

    neither = CliRunner().invoke(
        app,
        [
            "package",
            str(SCORE_PATH),
            str(manifest_path),
            str(spec_path),
            "--capability-profile",
            str(SUNO_PROFILE_PATH),
        ],
    )
    assert neither.exit_code == 2

    both = CliRunner().invoke(
        app,
        [
            "package",
            str(SCORE_PATH),
            str(manifest_path),
            str(spec_path),
            "--capability-profile",
            str(SUNO_PROFILE_PATH),
            "--output-dir",
            str(tmp_path / "out"),
            "--builds-root",
            str(tmp_path / "builds-root"),
        ],
    )
    assert both.exit_code == 2


# --- arrange --builds-root ----------------------------------------------------


def test_arrange_builds_root_first_publish_writes_digest_directory_and_latest(
    tmp_path: Path,
) -> None:
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path)
    root = tmp_path / "builds-root"

    result = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )
    assert result.exit_code == 0, result.output

    bundle_dirs = list((root / "builds").iterdir())
    assert len(bundle_dirs) == 1
    digest_dir = bundle_dirs[0]
    assert len(digest_dir.name) == 64  # sha256 hex

    produced = sorted(p.name for p in digest_dir.iterdir())
    assert produced == ["arrangement_bundle.json", "arrangement_diff.json", "derived_score.yaml"]

    bundle = json.loads((digest_dir / "arrangement_bundle.json").read_text(encoding="utf-8"))
    assert bundle["content_digest"] == digest_dir.name

    latest = json.loads((root / "latest.json").read_text(encoding="utf-8"))
    assert latest == {"schema_version": "builds-latest/0.1", "content_digest": digest_dir.name}


def test_arrange_builds_root_republish_same_digest_is_immutable_noop(tmp_path: Path) -> None:
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path)
    root = tmp_path / "builds-root"

    first = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )
    assert first.exit_code == 0, first.output
    digest_dir = next((root / "builds").iterdir())
    original_mtime = digest_dir.stat().st_mtime_ns
    original_files = {p.name: p.read_bytes() for p in digest_dir.iterdir()}

    second = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )

    assert second.exit_code == 0, second.output
    assert "already published" in second.stderr
    # directory left untouched: same files, same bytes, same mtime.
    assert digest_dir.stat().st_mtime_ns == original_mtime
    assert {p.name: p.read_bytes() for p in digest_dir.iterdir()} == original_files
    # still exactly one digest directory (no duplicate / mutation).
    assert len(list((root / "builds").iterdir())) == 1


def test_arrange_builds_root_different_inputs_get_separate_digest_dirs(tmp_path: Path) -> None:
    spec_path = tmp_path / "arrangement.yaml"
    root = tmp_path / "builds-root"

    _write_arrangement_spec(spec_path, bpm=140)
    first = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )
    assert first.exit_code == 0, first.output
    first_digest_dirs = {p.name for p in (root / "builds").iterdir()}

    _write_arrangement_spec(spec_path, bpm=150)
    second = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )
    assert second.exit_code == 0, second.output
    second_digest_dirs = {p.name for p in (root / "builds").iterdir()}

    assert len(second_digest_dirs) == 2
    assert first_digest_dirs < second_digest_dirs

    # both original digest artifacts remain, byte for byte.
    (first_digest,) = first_digest_dirs
    assert (root / "builds" / first_digest / "derived_score.yaml").exists()

    latest = json.loads((root / "latest.json").read_text(encoding="utf-8"))
    assert latest["content_digest"] in second_digest_dirs
    assert latest["content_digest"] not in first_digest_dirs


def test_arrange_builds_root_staging_failure_leaves_no_digest_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path)
    root = tmp_path / "builds-root"

    original_rename = os.rename

    def failing_rename(src: Any, dst: Any) -> Any:
        if "builds" in str(dst) and len(Path(dst).name) == 64:
            raise OSError("injected staging publish failure")
        return original_rename(src, dst)

    monkeypatch.setattr(os, "rename", failing_rename)

    result = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )

    assert result.exit_code == 1
    assert "Error:" in result.stderr
    builds_dir = root / "builds"
    # nothing published: either the builds/ dir was never created, or it
    # exists but holds no digest directory (staging cleaned up).
    assert not builds_dir.exists() or list(builds_dir.iterdir()) == []


# --- package --builds-root -----------------------------------------------------


def test_package_builds_root_first_publish_and_locator_resolves(tmp_path: Path) -> None:
    manifest_path, spec_path = _write_package_fixture(tmp_path)
    root = tmp_path / "builds-root"

    result = CliRunner().invoke(
        app, _package_args(manifest_path, spec_path, mode_flag="builds-root", mode_value=str(root))
    )
    assert result.exit_code == 0, result.output

    (digest_dir,) = (root / "builds").iterdir()
    assert len(digest_dir.name) == 64

    produced = sorted(p.name for p in digest_dir.iterdir())
    assert produced == ["compilation_report.json", "performance_package.json"]

    report = json.loads((digest_dir / "compilation_report.json").read_text(encoding="utf-8"))
    assert report["content_digest"] == digest_dir.name

    package = json.loads((digest_dir / "performance_package.json").read_text(encoding="utf-8"))
    reference = package["channel_artifacts"]["lyrics_text"][0]
    # artifact_base.locator must resolve from the *real* published digest
    # directory back to the manifest directory — this is the load-bearing
    # check for the same-depth-placeholder trick used to compute the locator
    # before content_digest is known.
    resolved = (digest_dir / reference["artifact_base"]["locator"] / reference["artifact"])
    assert resolved.resolve() == (tmp_path / "lyrics.txt").resolve()

    latest = json.loads((root / "latest.json").read_text(encoding="utf-8"))
    assert latest == {"schema_version": "builds-latest/0.1", "content_digest": digest_dir.name}


def test_package_builds_root_republish_same_digest_is_immutable_noop(tmp_path: Path) -> None:
    manifest_path, spec_path = _write_package_fixture(tmp_path)
    root = tmp_path / "builds-root"

    first = CliRunner().invoke(
        app, _package_args(manifest_path, spec_path, mode_flag="builds-root", mode_value=str(root))
    )
    assert first.exit_code == 0, first.output
    digest_dir = next((root / "builds").iterdir())
    original_files = {p.name: p.read_bytes() for p in digest_dir.iterdir()}

    second = CliRunner().invoke(
        app, _package_args(manifest_path, spec_path, mode_flag="builds-root", mode_value=str(root))
    )

    assert second.exit_code == 0, second.output
    assert "already published" in second.stderr
    assert {p.name: p.read_bytes() for p in digest_dir.iterdir()} == original_files
    assert len(list((root / "builds").iterdir())) == 1


# --- input <-> latest.json collision guard -------------------------------------


def test_arrange_builds_root_rejects_input_colliding_with_latest_json(tmp_path: Path) -> None:
    """An input that aliases `<root>/latest.json` must not be silently clobbered.

    `latest.json` is the one file the builds-root scheme ever overwrites; if
    the arrangement spec itself resolves to that path, publishing would
    overwrite the spec on the latest-pointer update.
    """
    root = tmp_path / "builds-root"
    root.mkdir()
    spec_path = root / "latest.json"
    _write_arrangement_spec(spec_path)

    result = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )

    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert "input path collides with output artifact path" in result.stderr
    assert str(root / "latest.json") in result.stderr
    # nothing published at all — the input's own bytes are untouched and no
    # digest directory or latest.json pointer was ever written.
    assert not (root / "builds").exists()
    assert spec_path.read_bytes() == (root / "latest.json").read_bytes()


def test_package_builds_root_rejects_input_colliding_with_latest_json(tmp_path: Path) -> None:
    """Same guard, exercised via `compiled.protected_input_paths` (score/manifest/
    spec/profile/manifest-artifacts) rather than a raw two-path list."""
    manifest_path, _ = _write_package_fixture(tmp_path)
    root = tmp_path / "builds-root"
    root.mkdir()
    spec_path = root / "latest.json"
    spec_path.write_text(
        yaml.safe_dump(
            {
                "meta": {"id": "package-builds-root", "version": "1"},
                "target": {},
                "preservation": {
                    "score_fields": {},
                    "identity_anchors": {"lyrics": {"mode": "hard", "allow": []}},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        _package_args(manifest_path, spec_path, mode_flag="builds-root", mode_value=str(root)),
    )

    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert "input path collides with output artifact path" in result.stderr
    assert str(root / "latest.json") in result.stderr
    assert not (root / "builds").exists()


# --- PR #184 review: non-directory digest target + read-only provenance check --


def _arrange_content_digest(tmp_path: Path, spec_path: Path) -> str:
    """Discover the content_digest a given (score, spec) pair resolves to, via
    a real publish into a throwaway builds-root."""
    probe_root = tmp_path / "digest-probe"
    probe = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(probe_root))
    )
    assert probe.exit_code == 0, probe.output
    return next((probe_root / "builds").iterdir()).name


def test_arrange_builds_root_rejects_non_directory_digest_target(tmp_path: Path) -> None:
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path)
    digest = _arrange_content_digest(tmp_path, spec_path)

    root = tmp_path / "builds-root"
    (root / "builds").mkdir(parents=True)
    occupying_file = root / "builds" / digest
    occupying_file.write_text("not a directory", encoding="utf-8")

    result = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )

    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert "not a directory" in result.stderr
    # neither the occupying file nor latest.json is touched.
    assert occupying_file.read_text(encoding="utf-8") == "not a directory"
    assert not (root / "latest.json").exists()


def test_arrange_builds_root_rejects_dangling_symlink_digest_target(tmp_path: Path) -> None:
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path)
    digest = _arrange_content_digest(tmp_path, spec_path)

    root = tmp_path / "builds-root"
    (root / "builds").mkdir(parents=True)
    dangling_target = root / "builds" / digest
    dangling_target.symlink_to(root / "builds" / "does-not-exist")

    result = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )

    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert "not a directory" in result.stderr
    assert dangling_target.is_symlink()
    assert not (root / "latest.json").exists()


def test_arrange_builds_root_rejects_existing_directory_missing_descriptor(
    tmp_path: Path,
) -> None:
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path)
    digest = _arrange_content_digest(tmp_path, spec_path)

    root = tmp_path / "builds-root"
    digest_dir = root / "builds" / digest
    digest_dir.mkdir(parents=True)
    # deliberately does not contain arrangement_bundle.json.

    result = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )

    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert "missing or unreadable" in result.stderr
    assert not (root / "latest.json").exists()


def test_arrange_builds_root_republish_with_differing_provenance_updates_latest_with_note(
    tmp_path: Path,
) -> None:
    """Same `content_digest` (derived_score/diff bytes unchanged) but a
    byte-different `arrangement_bundle.json` (a different `arrangement_spec.path`
    string for byte-identical spec content) must not be silently treated as an
    identical republish: the digest directory is still left untouched, but the
    stderr note says provenance differs, and `latest.json` is still updated."""
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path)
    root = tmp_path / "builds-root"

    first = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )
    assert first.exit_code == 0, first.output
    digest_dir = next((root / "builds").iterdir())
    original_files = {p.name: p.read_bytes() for p in digest_dir.iterdir()}

    spec_path_copy = tmp_path / "arrangement_copy.yaml"
    spec_path_copy.write_bytes(spec_path.read_bytes())

    second = CliRunner().invoke(
        app, _arrange_args(spec_path_copy, mode_flag="builds-root", mode_value=str(root))
    )

    assert second.exit_code == 0, second.output
    assert "provenance differs from this invocation" in second.stderr
    # existing digest directory is completely untouched.
    assert {p.name: p.read_bytes() for p in digest_dir.iterdir()} == original_files
    assert len(list((root / "builds").iterdir())) == 1
    # latest.json is still updated (same digest either way).
    latest = json.loads((root / "latest.json").read_text(encoding="utf-8"))
    assert latest["content_digest"] == digest_dir.name


def test_package_builds_root_rejects_non_directory_digest_target(tmp_path: Path) -> None:
    """Same non-directory rejection, exercised through the `package` CLI wiring."""
    manifest_path, spec_path = _write_package_fixture(tmp_path)

    probe_root = tmp_path / "digest-probe"
    probe = CliRunner().invoke(
        app,
        _package_args(
            manifest_path, spec_path, mode_flag="builds-root", mode_value=str(probe_root)
        ),
    )
    assert probe.exit_code == 0, probe.output
    digest = next((probe_root / "builds").iterdir()).name

    root = tmp_path / "builds-root"
    (root / "builds").mkdir(parents=True)
    occupying_file = root / "builds" / digest
    occupying_file.write_text("not a directory", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        _package_args(manifest_path, spec_path, mode_flag="builds-root", mode_value=str(root)),
    )

    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert "not a directory" in result.stderr
    assert occupying_file.read_text(encoding="utf-8") == "not a directory"
    assert not (root / "latest.json").exists()


# --- PR #184 review round 2: descriptor self-consistency (not mere byte diff) ---


def test_arrange_builds_root_rejects_tampered_content_digest_field(tmp_path: Path) -> None:
    """A descriptor whose own `content_digest` field doesn't match the digest
    directory it lives in must not be accepted as a valid prior publication,
    even though it parses as JSON and differs byte-for-byte from what this
    invocation would publish."""
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path)
    root = tmp_path / "builds-root"

    first = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )
    assert first.exit_code == 0, first.output
    digest_dir = next((root / "builds").iterdir())
    bundle_path = digest_dir / "arrangement_bundle.json"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["content_digest"] = "0" * 64  # deliberately wrong
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_before = (root / "latest.json").read_bytes()

    second = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )

    assert second.exit_code == 1
    assert "Error:" in second.stderr
    assert "content_digest" in second.stderr
    # not accepted as a no-op, and the corrupted descriptor + latest.json are
    # both left exactly as they were (no repair, no blessing).
    assert (root / "latest.json").read_bytes() == latest_before
    assert json.loads(bundle_path.read_text(encoding="utf-8"))["content_digest"] == "0" * 64


def test_arrange_builds_root_rejects_json_corrupted_descriptor(tmp_path: Path) -> None:
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path)
    root = tmp_path / "builds-root"

    first = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )
    assert first.exit_code == 0, first.output
    digest_dir = next((root / "builds").iterdir())
    bundle_path = digest_dir / "arrangement_bundle.json"
    bundle_path.write_text("not valid json {{{", encoding="utf-8")
    latest_before = (root / "latest.json").read_bytes()

    second = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )

    assert second.exit_code == 1
    assert "Error:" in second.stderr
    assert "unparsable" in second.stderr
    assert (root / "latest.json").read_bytes() == latest_before


def test_arrange_builds_root_rejects_self_inconsistent_output_hashes(tmp_path: Path) -> None:
    """A descriptor whose declared `content_digest` matches its own directory
    name, but whose declared output hashes recompute to a *different* digest,
    must be rejected — its own bookkeeping is internally inconsistent (the
    tamper this check exists for: step 2 alone would have let this through)."""
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path)
    root = tmp_path / "builds-root"

    first = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )
    assert first.exit_code == 0, first.output
    digest_dir = next((root / "builds").iterdir())
    bundle_path = digest_dir / "arrangement_bundle.json"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    # content_digest field is left correct; only a declared output hash is
    # tampered with, so the descriptor no longer recomputes to its own digest.
    bundle["outputs"]["derived_score"]["sha256"] = "1" * 64
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_before = (root / "latest.json").read_bytes()

    second = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )

    assert second.exit_code == 1
    assert "Error:" in second.stderr
    assert "recomputes to content_digest" in second.stderr
    assert (root / "latest.json").read_bytes() == latest_before


# --- PR #184 review round 3: symlink digest targets + latest.json preflight ----


def test_arrange_builds_root_rejects_symlink_to_directory_digest_target(
    tmp_path: Path,
) -> None:
    """A symlink digest target is rejected even when it points at a real,
    otherwise-valid-looking directory: the symlink itself could be repointed
    by whoever controls it, so this scheme only trusts a real directory."""
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path)
    digest = _arrange_content_digest(tmp_path, spec_path)

    root = tmp_path / "builds-root"
    (root / "builds").mkdir(parents=True)
    real_dir = tmp_path / "real-digest-dir"
    real_dir.mkdir()
    (real_dir / "arrangement_bundle.json").write_text("{}", encoding="utf-8")
    symlink_target = root / "builds" / digest
    symlink_target.symlink_to(real_dir)

    result = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )

    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert "not a directory" in result.stderr
    assert symlink_target.is_symlink()
    assert not (root / "latest.json").exists()


def test_arrange_builds_root_rejects_directory_latest_json_before_first_publish(
    tmp_path: Path,
) -> None:
    """`latest.json` as a directory is rejected by the preflight *before*
    `builds/` is even created — nothing under builds-root gets touched."""
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path)
    root = tmp_path / "builds-root"
    (root / "latest.json").mkdir(parents=True)

    result = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )

    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert "latest pointer target is not a plain file" in result.stderr
    assert not (root / "builds").exists()
    assert (root / "latest.json").is_dir()


def test_arrange_builds_root_rejects_directory_latest_json_with_existing_valid_digest_dir(
    tmp_path: Path,
) -> None:
    """Even with an existing, otherwise-valid digest directory on disk, a
    `latest.json` directory still fails the preflight and that digest
    directory is left completely untouched."""
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path)
    root = tmp_path / "builds-root"

    first = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )
    assert first.exit_code == 0, first.output
    digest_dir = next((root / "builds").iterdir())
    original_files = {p.name: p.read_bytes() for p in digest_dir.iterdir()}

    (root / "latest.json").unlink()
    (root / "latest.json").mkdir()

    second = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )

    assert second.exit_code == 1
    assert "Error:" in second.stderr
    assert "latest pointer target is not a plain file" in second.stderr
    assert {p.name: p.read_bytes() for p in digest_dir.iterdir()} == original_files
    assert (root / "latest.json").is_dir()


def test_arrange_builds_root_rejects_symlink_latest_json(tmp_path: Path) -> None:
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path)
    root = tmp_path / "builds-root"
    root.mkdir(parents=True)
    real_file = tmp_path / "real-latest.json"
    real_file.write_text("{}", encoding="utf-8")
    (root / "latest.json").symlink_to(real_file)

    result = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )

    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert "latest pointer target is not a plain file" in result.stderr
    assert not (root / "builds").exists()
    assert (root / "latest.json").is_symlink()


def test_arrange_builds_root_self_heals_latest_after_pointer_update_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the trailing `latest.json` update fails after a successful first
    publish, the freshly published digest directory is not rolled back; a
    same-input re-run finds it via the already-published no-op path and
    updates `latest.json` on its own (self-healing, no separate repair
    step)."""
    import svp_rpe.cli as cli_module

    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path)
    root = tmp_path / "builds-root"

    original_update = cli_module._update_builds_latest_pointer

    def failing_update(*args: Any, **kwargs: Any) -> None:
        raise OSError("injected latest.json update failure")

    monkeypatch.setattr(cli_module, "_update_builds_latest_pointer", failing_update)

    first = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )

    assert first.exit_code == 1
    assert "Error:" in first.stderr
    assert "remains valid" in first.stderr
    # the digest directory was published in full despite the pointer-update
    # failure — it is not rolled back.
    digest_dirs = list((root / "builds").iterdir())
    assert len(digest_dirs) == 1
    digest_dir = digest_dirs[0]
    produced = sorted(p.name for p in digest_dir.iterdir())
    assert produced == ["arrangement_bundle.json", "arrangement_diff.json", "derived_score.yaml"]
    assert not (root / "latest.json").exists()

    monkeypatch.setattr(cli_module, "_update_builds_latest_pointer", original_update)

    second = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )

    assert second.exit_code == 0, second.output
    assert "already published" in second.stderr
    latest = json.loads((root / "latest.json").read_text(encoding="utf-8"))
    assert latest["content_digest"] == digest_dir.name


# --- PR #184 review round 4: descriptor schema_version validation --------------


def test_arrange_builds_root_rejects_unknown_schema_version_in_descriptor(
    tmp_path: Path,
) -> None:
    """A descriptor whose `schema_version` doesn't match the current bundle
    schema must be rejected outright (AGENTS.md §8 Persistent Artifact Safety
    Gate: unknown schema_version is rejected, not silently treated as an
    ordinary provenance difference in the current schema), even though its
    `content_digest` field and declared output hashes are still correct."""
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path)
    root = tmp_path / "builds-root"

    first = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )
    assert first.exit_code == 0, first.output
    digest_dir = next((root / "builds").iterdir())
    bundle_path = digest_dir / "arrangement_bundle.json"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["schema_version"] = "arrangement-bundle/9.9"
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_before = (root / "latest.json").read_bytes()

    second = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )

    assert second.exit_code == 1
    assert "Error:" in second.stderr
    assert "schema_version" in second.stderr
    assert "arrangement-bundle/9.9" in second.stderr
    assert (root / "latest.json").read_bytes() == latest_before
    assert (
        json.loads(bundle_path.read_text(encoding="utf-8"))["schema_version"]
        == "arrangement-bundle/9.9"
    )


def test_arrange_builds_root_rejects_descriptor_missing_schema_version(
    tmp_path: Path,
) -> None:
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path)
    root = tmp_path / "builds-root"

    first = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )
    assert first.exit_code == 0, first.output
    digest_dir = next((root / "builds").iterdir())
    bundle_path = digest_dir / "arrangement_bundle.json"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    del bundle["schema_version"]
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_before = (root / "latest.json").read_bytes()

    second = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )

    assert second.exit_code == 1
    assert "Error:" in second.stderr
    assert "schema_version" in second.stderr
    assert (root / "latest.json").read_bytes() == latest_before


# --- PR #184 review round 5: blessing requires content-artifact verification ---
# --- (Thread F) + locator-placeholder subtree rejection (Thread G, package) ----


def test_arrange_builds_root_rejects_missing_content_artifact_on_fast_path(
    tmp_path: Path,
) -> None:
    """A byte-identical descriptor is not enough to bless a directory that is
    missing one of its declared content artifacts: the fast (byte-match)
    path still verifies every declared output exists with its declared
    hash before `latest.json` may be updated."""
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path)
    root = tmp_path / "builds-root"

    first = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )
    assert first.exit_code == 0, first.output
    digest_dir = next((root / "builds").iterdir())
    (digest_dir / "derived_score.yaml").unlink()
    latest_before = (root / "latest.json").read_bytes()

    second = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )

    assert second.exit_code == 1
    assert "Error:" in second.stderr
    assert "derived_score.yaml" in second.stderr
    assert "missing or unreadable content artifact" in second.stderr
    assert (root / "latest.json").read_bytes() == latest_before


def test_arrange_builds_root_rejects_tampered_content_artifact_hash(tmp_path: Path) -> None:
    """A byte-identical descriptor whose declared output hash no longer
    matches the actual on-disk artifact bytes must also be rejected — the
    descriptor's own bytes being untouched doesn't prove the directory it
    describes is still intact."""
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path)
    root = tmp_path / "builds-root"

    first = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )
    assert first.exit_code == 0, first.output
    digest_dir = next((root / "builds").iterdir())
    diff_path = digest_dir / "arrangement_diff.json"
    diff_path.write_bytes(diff_path.read_bytes() + b"\n")  # tamper, descriptor left untouched
    latest_before = (root / "latest.json").read_bytes()

    second = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )

    assert second.exit_code == 1
    assert "Error:" in second.stderr
    assert "arrangement_diff.json" in second.stderr
    assert "sha256 mismatch" in second.stderr
    assert (root / "latest.json").read_bytes() == latest_before


def test_package_builds_root_rejects_manifest_artifact_inside_locator_placeholder(
    tmp_path: Path,
) -> None:
    """An identity-manifest artifact that happens to live inside the reserved
    `<root>/builds/<64 zeros>/` locator-placeholder subtree must be rejected:
    the locator computed against the placeholder would not describe where
    the artifact actually ends up relative to the real (differently-named)
    digest directory once published."""
    root = tmp_path / "builds-root"
    placeholder_dir = root / "builds" / ("0" * 64)
    placeholder_dir.mkdir(parents=True)

    source_path = placeholder_dir / "source.wav"
    source_path.write_bytes(b"source audio")
    artifact_path = placeholder_dir / "lyrics.txt"
    artifact_path.write_bytes(b"hello midnight")

    manifest_path = tmp_path / "identity.yaml"
    manifest_data = {
        "schema_version": "identity-manifest/0.1",
        "meta": {"work_id": "placeholder-subtree", "version": "1"},
        "source": {
            "locator": os.path.relpath(source_path, tmp_path),
            "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            "rights_basis": "original",
        },
        "anchors": [
            {
                "id": "lyrics",
                "domain": "lyrics",
                "artifact": os.path.relpath(artifact_path, tmp_path),
                "artifact_type": "lyrics_text",
                "media_type": "text/plain",
                "sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
                "required": True,
            }
        ],
    }
    manifest_path.write_text(yaml.safe_dump(manifest_data, sort_keys=False), encoding="utf-8")

    spec_path = tmp_path / "arrangement.yaml"
    spec_path.write_text(
        yaml.safe_dump(
            {
                "meta": {"id": "package-builds-root", "version": "1"},
                "target": {},
                "preservation": {
                    "score_fields": {},
                    "identity_anchors": {"lyrics": {"mode": "hard", "allow": []}},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        _package_args(manifest_path, spec_path, mode_flag="builds-root", mode_value=str(root)),
    )

    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert "input path collides with output artifact path" in result.stderr
    assert "locator-placeholder subtree" in result.stderr
    assert not (root / "latest.json").exists()
    # a real (differently-named) digest directory is not similarly reserved:
    # confirm nothing was published under the placeholder either.
    assert sorted(p.name for p in (root / "builds").iterdir()) == ["0" * 64]


# --- PR #184 review round 6: `<builds_root>/builds` symlink rejection ----------


def test_arrange_builds_root_rejects_symlinked_builds_directory(tmp_path: Path) -> None:
    """A symlinked `builds/` is rejected outright, even when it points at a
    real, otherwise-writable directory: every digest directory this scheme
    publishes lives under `builds/`, so a symlinked `builds/` would place
    publications outside `builds_root`, repointable by whoever controls the
    symlink — nothing is written, and the real directory the symlink points
    at stays empty."""
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path)
    root = tmp_path / "builds-root"
    root.mkdir(parents=True)
    real_builds_dir = tmp_path / "real-builds"
    real_builds_dir.mkdir()
    (root / "builds").symlink_to(real_builds_dir)

    result = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )

    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert "builds directory is a symlink" in result.stderr
    assert (root / "builds").is_symlink()
    assert not (root / "latest.json").exists()
    # nothing was published through the symlink into the real directory.
    assert list(real_builds_dir.iterdir()) == []


def test_arrange_builds_root_allows_symlinked_builds_root_itself(tmp_path: Path) -> None:
    """`builds_root` itself (the user-supplied `--builds-root` argument) is
    not subject to the same rejection: it is the caller's own path (like
    `--output-dir`), not part of the scheme's reserved internal layout, so a
    symlinked `builds_root` publishes normally."""
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path)
    real_root = tmp_path / "real-builds-root"
    real_root.mkdir()
    symlinked_root = tmp_path / "builds-root-link"
    symlinked_root.symlink_to(real_root)

    result = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(symlinked_root))
    )

    assert result.exit_code == 0, result.output
    digest_dirs = list((real_root / "builds").iterdir())
    assert len(digest_dirs) == 1
    digest_dir = digest_dirs[0]
    produced = sorted(p.name for p in digest_dir.iterdir())
    assert produced == ["arrangement_bundle.json", "arrangement_diff.json", "derived_score.yaml"]
    latest = json.loads((real_root / "latest.json").read_text(encoding="utf-8"))
    assert latest["content_digest"] == digest_dir.name


# --- PR #184 review round 7: symlinked blessing inputs (I) + whitelist (J) ----
# --- + symlinked locator placeholder (K, package) -----------------------------


def test_arrange_builds_root_rejects_symlinked_content_artifact(tmp_path: Path) -> None:
    """A declared content artifact replaced with a symlink to a byte-identical
    file elsewhere must still be rejected — a symlink is rejected regardless
    of what it resolves to, since resolving it at all means trusting
    whoever controls that link."""
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path)
    root = tmp_path / "builds-root"

    first = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )
    assert first.exit_code == 0, first.output
    digest_dir = next((root / "builds").iterdir())
    diff_path = digest_dir / "arrangement_diff.json"
    external_copy = tmp_path / "external_diff_copy.json"
    external_copy.write_bytes(diff_path.read_bytes())
    diff_path.unlink()
    diff_path.symlink_to(external_copy)
    latest_before = (root / "latest.json").read_bytes()

    second = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )

    assert second.exit_code == 1
    assert "Error:" in second.stderr
    assert "arrangement_diff.json" in second.stderr
    assert "is a symlink" in second.stderr
    assert (root / "latest.json").read_bytes() == latest_before
    assert diff_path.is_symlink()


def test_arrange_builds_root_rejects_symlinked_descriptor(tmp_path: Path) -> None:
    """The descriptor itself (`arrangement_bundle.json`) being a symlink —
    even to a byte-identical copy — is rejected the same way."""
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path)
    root = tmp_path / "builds-root"

    first = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )
    assert first.exit_code == 0, first.output
    digest_dir = next((root / "builds").iterdir())
    bundle_path = digest_dir / "arrangement_bundle.json"
    external_copy = tmp_path / "external_bundle_copy.json"
    external_copy.write_bytes(bundle_path.read_bytes())
    bundle_path.unlink()
    bundle_path.symlink_to(external_copy)
    latest_before = (root / "latest.json").read_bytes()

    second = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )

    assert second.exit_code == 1
    assert "Error:" in second.stderr
    assert "arrangement_bundle.json" in second.stderr
    assert "is a symlink" in second.stderr
    assert (root / "latest.json").read_bytes() == latest_before
    assert bundle_path.is_symlink()


def test_arrange_builds_root_rejects_tampered_arrangement_id_outside_digest(
    tmp_path: Path,
) -> None:
    """`arrangement_id` is not part of `content_digest` at all, so a
    descriptor with a tampered `arrangement_id` but an otherwise-correct
    `content_digest`/`outputs` must still be rejected by the whitelist check
    — not silently waved through as mere provenance drift."""
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path)
    root = tmp_path / "builds-root"

    first = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )
    assert first.exit_code == 0, first.output
    digest_dir = next((root / "builds").iterdir())
    bundle_path = digest_dir / "arrangement_bundle.json"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["arrangement_id"] = "tampered-id"
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_before = (root / "latest.json").read_bytes()

    second = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )

    assert second.exit_code == 1
    assert "Error:" in second.stderr
    assert "non-provenance field" in second.stderr
    assert "arrangement_id" in second.stderr
    assert (root / "latest.json").read_bytes() == latest_before


def test_arrange_builds_root_rejects_tampered_content_digest_basis(tmp_path: Path) -> None:
    spec_path = tmp_path / "arrangement.yaml"
    _write_arrangement_spec(spec_path)
    root = tmp_path / "builds-root"

    first = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )
    assert first.exit_code == 0, first.output
    digest_dir = next((root / "builds").iterdir())
    bundle_path = digest_dir / "arrangement_bundle.json"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["content_digest_basis"] = "tampered-basis/v1"
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_before = (root / "latest.json").read_bytes()

    second = CliRunner().invoke(
        app, _arrange_args(spec_path, mode_flag="builds-root", mode_value=str(root))
    )

    assert second.exit_code == 1
    assert "Error:" in second.stderr
    assert "non-provenance field" in second.stderr
    assert "content_digest_basis" in second.stderr
    assert (root / "latest.json").read_bytes() == latest_before


def test_package_builds_root_rejects_tampered_work_id(tmp_path: Path) -> None:
    """`work_id` is not part of `content_digest` (only `package_sha256` is),
    so a tampered `work_id` must be caught by the whitelist check even
    though the digest and package hash both still match."""
    manifest_path, spec_path = _write_package_fixture(tmp_path)
    root = tmp_path / "builds-root"

    first = CliRunner().invoke(
        app,
        _package_args(manifest_path, spec_path, mode_flag="builds-root", mode_value=str(root)),
    )
    assert first.exit_code == 0, first.output
    digest_dir = next((root / "builds").iterdir())
    report_path = digest_dir / "compilation_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["work_id"] = "tampered-work-id"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_before = (root / "latest.json").read_bytes()

    second = CliRunner().invoke(
        app,
        _package_args(manifest_path, spec_path, mode_flag="builds-root", mode_value=str(root)),
    )

    assert second.exit_code == 1
    assert "Error:" in second.stderr
    assert "non-provenance field" in second.stderr
    assert "work_id" in second.stderr
    assert (root / "latest.json").read_bytes() == latest_before


def test_package_builds_root_allows_invocation_provenance_only_difference(
    tmp_path: Path,
) -> None:
    """`invocation_provenance` alone differing is exactly the intended
    provenance-drift case (rounds 4/5's compiler-version story) and must
    still succeed as a no-op with the "differs" advisory."""
    manifest_path, spec_path = _write_package_fixture(tmp_path)
    root = tmp_path / "builds-root"

    first = CliRunner().invoke(
        app,
        _package_args(manifest_path, spec_path, mode_flag="builds-root", mode_value=str(root)),
    )
    assert first.exit_code == 0, first.output
    digest_dir = next((root / "builds").iterdir())
    report_path = digest_dir / "compilation_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["invocation_provenance"]["compiler"]["package_version"] = "9.9.9"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    second = CliRunner().invoke(
        app,
        _package_args(manifest_path, spec_path, mode_flag="builds-root", mode_value=str(root)),
    )

    assert second.exit_code == 0, second.output
    assert "provenance differs from this invocation" in second.stderr
    latest = json.loads((root / "latest.json").read_text(encoding="utf-8"))
    assert latest["content_digest"] == digest_dir.name


def test_package_builds_root_rejects_symlinked_locator_placeholder(tmp_path: Path) -> None:
    """A `<root>/builds/<64 zeros>/` locator placeholder that is itself a
    symlink (even to a real, writable directory) is rejected before
    compilation even starts — resolving it would compute the locator
    relative to wherever the symlink actually points, not the reserved
    same-depth placeholder path."""
    manifest_path, spec_path = _write_package_fixture(tmp_path)
    root = tmp_path / "builds-root"
    (root / "builds").mkdir(parents=True)
    real_dir = tmp_path / "real-placeholder-target"
    real_dir.mkdir()
    (root / "builds" / ("0" * 64)).symlink_to(real_dir)

    result = CliRunner().invoke(
        app,
        _package_args(manifest_path, spec_path, mode_flag="builds-root", mode_value=str(root)),
    )

    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert "locator placeholder is a symlink" in result.stderr
    assert not (root / "latest.json").exists()
    assert list(real_dir.iterdir()) == []
