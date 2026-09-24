"""YAML loader for `RecastProject` documents.

`load_recast_project` は project ファイルを 1 回だけ読み（sha256 も同一 read
から計算し TOCTOU を排除する）、schema 検証（`RecastProject.model_validate`）と
参照解決（既存 sidecar への相対パスの封じ込め + 存在チェック）のみを行う。
score/identity_manifest/arrangement の**中身**の parse・検証は行わない
（`compose.load_composition_score` 等を呼ばない）— それらは PR2 の plan 段の
責務であり、本 loader は「参照が安全に解決できるか」だけを扱う。

封じ込めは `arrange/pathsafe.py` の `validate_relative_locator` /
`resolve_confined` を再利用する（identity/capabilities loader と同じ可搬性契約:
絶対パス拒否 + `../` / symlink 経由の脱出拒否）。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Dict, Optional

import yaml

from svp_rpe.arrange.pathsafe import (
    PathConfinementError,
    resolve_confined,
    validate_relative_locator,
)
from svp_rpe.recast.models import (
    ModeOverridesConfig,
    RecastError,
    RecastProject,
    RecastReferenceError,
)

# capability_profile / mode_overrides の名前参照規約: スラッシュ・拡張子を含まない
# 裸の識別子。マッチしない値（`foo.yaml` / `sub/foo` 等）は相対パス参照として扱う。
_NAMED_CONFIG_PATTERN = re.compile(r"^[a-z0-9_-]+$")


@dataclass(frozen=True)
class LoadedRecastProject:
    """`load_recast_project` の戻り値: 検証済み project + 解決済み参照パス一式。"""

    project: RecastProject
    path: Path
    project_dir: Path
    sha256: str
    score_path: Path
    identity_manifest_path: Path
    arrangement_paths: Dict[str, Path]
    capability_profile_paths: Dict[str, Path]
    mode_override_paths: Dict[str, Path]
    builds_root: Path


def load_recast_project(path: Path | str) -> LoadedRecastProject:
    """RecastProject YAML をロードし、既存 sidecar への参照をすべて解決する。"""
    project_path = Path(path)
    try:
        raw_bytes = project_path.read_bytes()
    except OSError as exc:
        raise RecastError(f"recast project unreadable at {project_path}: {exc}") from exc

    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    data = yaml.safe_load(raw_bytes)
    if not isinstance(data, dict):
        raise RecastError(f"recast project must be a mapping: {project_path}")

    project = RecastProject.model_validate(data)
    project_dir = project_path.resolve().parent

    score_path = _resolve_sidecar_reference(
        project.work.score, project_dir, project_path=project_path, target="work.score"
    )
    identity_manifest_path = _resolve_sidecar_reference(
        project.work.identity_manifest,
        project_dir,
        project_path=project_path,
        target="work.identity_manifest",
    )

    arrangement_paths: Dict[str, Path] = {}
    for variant_name, variant in project.variants.items():
        arrangement_paths[variant_name] = _resolve_sidecar_reference(
            variant.arrangement,
            project_dir,
            project_path=project_path,
            target=f"variants.{variant_name}.arrangement",
        )

    capability_profile_paths: Dict[str, Path] = {}
    mode_override_paths: Dict[str, Path] = {}
    for backend_name, backend in project.backends.items():
        capability_profile_paths[backend_name] = _resolve_capability_profile(
            backend.capability_profile,
            project_dir,
            project_path=project_path,
            target=f"backends.{backend_name}.capability_profile",
        )
        if backend.mode_overrides is not None:
            mode_override_paths[backend_name] = _resolve_mode_overrides(
                backend.mode_overrides,
                project_dir,
                project_path=project_path,
                target=f"backends.{backend_name}.mode_overrides",
            )

    builds_root = _resolve_builds_root(
        project.project.builds_root, project_dir, project_path=project_path
    )

    return LoadedRecastProject(
        project=project,
        path=project_path,
        project_dir=project_dir,
        sha256=sha256,
        score_path=score_path,
        identity_manifest_path=identity_manifest_path,
        arrangement_paths=arrangement_paths,
        capability_profile_paths=capability_profile_paths,
        mode_override_paths=mode_override_paths,
        builds_root=builds_root,
    )


def _resolve_sidecar_reference(
    value: str, project_dir: Path, *, project_path: Path, target: str
) -> Path:
    """相対 locator を project_dir 配下に閉じた実パスへ解決し、ファイルの実在を検証する。"""
    resolved = _confine(value, project_dir, project_path=project_path, target=target)
    if not resolved.is_file():
        raise RecastReferenceError(
            f"recast project {project_path}: {target} reference {value!r} "
            f"does not exist at {resolved}"
        )
    return resolved


def _confine(value: str, base_dir: Path, *, project_path: Path, target: str) -> Path:
    try:
        validate_relative_locator(value)
        return resolve_confined(value, base_dir)
    except PathConfinementError as exc:
        if exc.reason == "absolute":
            raise RecastReferenceError(
                f"recast project {project_path}: {target} reference {value!r} "
                f"must be a relative path (absolute paths are not allowed)"
            ) from exc
        if exc.reason == "traversal":
            raise RecastReferenceError(
                f"recast project {project_path}: {target} reference {value!r} "
                f"must not traverse above the project directory"
            ) from exc
        raise RecastReferenceError(
            f"recast project {project_path}: {target} reference {value!r} "
            f"escapes the project directory {exc.base}: resolved to {exc.resolved}"
        ) from exc


def _resolve_capability_profile(
    value: str, project_dir: Path, *, project_path: Path, target: str
) -> Path:
    """capability_profile 参照を解決する。

    `^[a-z0-9_-]+$` にマッチする裸の識別子は名前参照として扱い、
    `config/capability_profiles/<name>.yaml` をリポジトリ checkout →
    パッケージ同梱の順で探す（`utils/config_loader.load_config` と同じ
    local→packaged フォールバックパターン）。マッチしなければ project_dir
    相対のパス参照として通常の封じ込め+存在チェックへフォールバックする。
    """
    if _NAMED_CONFIG_PATTERN.match(value):
        resolved = _find_named_config(value, subdir="capability_profiles")
        if resolved is None:
            raise RecastReferenceError(
                f"recast project {project_path}: {target} names capability profile "
                f"{value!r}, which was not found under config/capability_profiles/ "
                f"(repo checkout or packaged svp_rpe.config.capability_profiles)"
            )
        return resolved
    return _resolve_sidecar_reference(value, project_dir, project_path=project_path, target=target)


def _resolve_mode_overrides(
    value: str, project_dir: Path, *, project_path: Path, target: str
) -> Path:
    """mode_overrides 参照を解決する。

    `_resolve_capability_profile` と同じ名前参照規約: `^[a-z0-9_-]+$` にマッチする
    裸の識別子は `config/mode_overrides/<name>.yaml` の名前参照として扱い（repo
    checkout → パッケージ同梱の順）、マッチしなければ project_dir 相対のパス参照
    として通常の封じ込め+存在チェックへフォールバックする。
    """
    if _NAMED_CONFIG_PATTERN.match(value):
        resolved = _find_named_config(value, subdir="mode_overrides")
        if resolved is None:
            raise RecastReferenceError(
                f"recast project {project_path}: {target} names mode overrides "
                f"{value!r}, which was not found under config/mode_overrides/ "
                f"(repo checkout or packaged svp_rpe.config.mode_overrides)"
            )
        return resolved
    return _resolve_sidecar_reference(value, project_dir, project_path=project_path, target=target)


def _find_named_config(name: str, *, subdir: str) -> Optional[Path]:
    for candidate in _local_config_paths(name, subdir=subdir):
        if candidate.is_file():
            return candidate
    return _packaged_config_path(name, subdir=subdir)


def _local_config_paths(name: str, *, subdir: str) -> list[Path]:
    # svp_rpe/recast/loader.py -> parents[3] == repo root (utils/config_loader.py と同じ深さ)。
    repo_root = Path(__file__).resolve().parents[3]
    return [
        repo_root / "config" / subdir / f"{name}.yaml",
        Path.cwd() / "config" / subdir / f"{name}.yaml",
    ]


def _packaged_config_path(name: str, *, subdir: str) -> Optional[Path]:
    try:
        resource = files(f"svp_rpe.config.{subdir}").joinpath(f"{name}.yaml")
    except ModuleNotFoundError:
        return None
    if not resource.is_file():
        return None
    return Path(str(resource))


def load_mode_overrides(path: Path | str) -> ModeOverridesConfig:
    """mode-overrides/0.1 YAML を単一 read でロード・検証する。"""
    mode_overrides_path = Path(path)
    try:
        raw_bytes = mode_overrides_path.read_bytes()
    except OSError as exc:
        raise RecastError(f"mode overrides unreadable at {mode_overrides_path}: {exc}") from exc
    data = yaml.safe_load(raw_bytes)
    if not isinstance(data, dict):
        raise RecastError(f"mode overrides must be a mapping: {mode_overrides_path}")
    return ModeOverridesConfig.model_validate(data)


def _resolve_builds_root(value: str, project_dir: Path, *, project_path: Path) -> Path:
    """builds_root を解決する。実在は要求しない（後で作られる）が、既存の
    ファイル（ディレクトリ以外）を指している場合は fail-fast で拒否する。"""
    resolved = _confine(value, project_dir, project_path=project_path, target="project.builds_root")
    if resolved.exists() and not resolved.is_dir():
        raise RecastReferenceError(
            f"recast project {project_path}: project.builds_root {value!r} "
            f"points at an existing non-directory file at {resolved}"
        )
    return resolved
