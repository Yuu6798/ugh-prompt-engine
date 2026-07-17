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
