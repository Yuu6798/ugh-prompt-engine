"""`svprpe verify --builds-root`: read-only recursive audit of an entire
`<builds_root>/` tree (#190 follow-up).

Distinct from `arrange/verify.py`'s `VerifyReport` (a single
`PerformancePackage` + its `compilation_report.json` + a `--manifest`
identity chain, V1-V4). This module audits the *structural* self-consistency
of an entire builds-root publication tree instead: `latest.json`'s shape,
every `<root>/builds/<content_digest>/` directory's descriptive file
(`arrangement_bundle.json` or `compilation_report.json`), its self-consistent
`content_digest`, every artifact it declares — and, the part neither
`arrange/verify.py`'s single-package V4 nor `cli/builds_root.py`'s blessing
check ever do, every entry a digest directory contains that the descriptive
file does *not* declare. `_check_existing_builds_root_publication`
(`cli/builds_root.py`) deliberately stops short of this (its own docstring:
"never rehashes undeclared files or recurses into anything beyond what the
descriptor itself lists") — this module is the follow-up it points at.

This module knows nothing about `IdentityManifest` chains or
`channel_artifacts` cross-references (`arrange/verify.py`'s V3/V4) — a
builds-root tree alone carries no `--manifest`, and auditing that chain
remains the single-package `verify` mode's job. Read-only: only
`os.scandir`/`os.walk`/`read_bytes` are ever used here, nothing is written.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from svp_rpe.arrange.bundle import BUNDLE_FILENAME, BUNDLE_SCHEMA_VERSION, compute_content_digest
from svp_rpe.arrange.package import COMPILATION_REPORT_FILENAME, COMPILATION_REPORT_SCHEMA_VERSION
from svp_rpe.arrange.pathsafe import PathConfinementError, resolve_confined
from svp_rpe.arrange.verify import _eq_check, _regular_file_error
from svp_rpe.cli.arrange_cmd import (
    _arrange_bundle_digest_inputs,
    _arrange_bundle_validate_descriptor,
)
from svp_rpe.cli.builds_root import (
    BUILDS_LATEST_SCHEMA_VERSION,
    _BUILDS_LOCATOR_PLACEHOLDER_DIGEST,
)
from svp_rpe.cli.package_cmd import (
    _package_report_digest_inputs,
    _package_report_validate_descriptor,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class AuditCheck:
    """1 件の監査結果。`detail` は fail 時のみ意味を持つ（pass 時は None）。"""

    group: str
    label: str
    ok: bool
    detail: Optional[str] = None


@dataclass(frozen=True)
class BuildAuditResult:
    """1 つの `<builds_root>/builds/<content_digest>/` ディレクトリの監査結果。"""

    digest_dir_name: str
    checks: tuple[AuditCheck, ...]

    @property
    def failures(self) -> tuple[AuditCheck, ...]:
        return tuple(check for check in self.checks if not check.ok)

    @property
    def ok(self) -> bool:
        return not self.failures


@dataclass(frozen=True)
class BuildsRootAuditReport:
    """`audit_builds_root` の戻り値。ルート監査結果 + 各 build の監査結果。"""

    root_checks: tuple[AuditCheck, ...]
    builds: tuple[BuildAuditResult, ...]
    aborted: bool

    @property
    def failures(self) -> tuple[AuditCheck, ...]:
        collected = [check for check in self.root_checks if not check.ok]
        for build in self.builds:
            collected.extend(build.failures)
        return tuple(collected)

    @property
    def ok(self) -> bool:
        return not self.failures


@dataclass(frozen=True)
class _DescriptorKind:
    """descriptive file 名から特定される種別（arrange の bundle / package の report）。

    `digest_inputs` / `validate` は `cli/arrange_cmd.py` と `cli/package_cmd.py`
    が builds-root blessing 経路向けに既に持つ private ヘルパーをそのまま再利用する
    （arrange 側 = `_arrange_bundle_digest_inputs` / `_arrange_bundle_validate_descriptor`、
    package 側 = `_package_report_digest_inputs` / `_package_report_validate_descriptor`）。
    """

    label: str
    schema_version: str
    digest_inputs: Callable[[dict[str, Any]], dict[str, str]]
    validate: Callable[[dict[str, Any]], None]


_DESCRIPTOR_KINDS: dict[str, _DescriptorKind] = {
    BUNDLE_FILENAME: _DescriptorKind(
        label="arrangement bundle",
        schema_version=BUNDLE_SCHEMA_VERSION,
        digest_inputs=_arrange_bundle_digest_inputs,
        validate=_arrange_bundle_validate_descriptor,
    ),
    COMPILATION_REPORT_FILENAME: _DescriptorKind(
        label="compilation report",
        schema_version=COMPILATION_REPORT_SCHEMA_VERSION,
        digest_inputs=_package_report_digest_inputs,
        validate=_package_report_validate_descriptor,
    ),
}


def _audit_root(builds_root: Path) -> tuple[list[AuditCheck], bool, list[str]]:
    """ルート構造を監査する。戻り値は `(checks, aborted, valid_digest_dir_names)`。

    `aborted=True` になるのは `builds_root` 自身が実在ディレクトリでない場合のみ
    （structural gate）。それ以外のルート不整合（`latest.json` の欠落・`builds/`
    の欠落等）は通常の FAIL として収集し、後続の per-build 監査を続行する（この
    場合 `valid_digest_dir_names` は自然と空になる）。
    """
    checks: list[AuditCheck] = []

    if builds_root.is_symlink():
        checks.append(
            AuditCheck(
                "root",
                "builds_root is a real, non-symlink directory",
                False,
                f"{builds_root} is a symlink",
            )
        )
        return checks, True, []
    if not builds_root.is_dir():
        checks.append(
            AuditCheck(
                "root",
                "builds_root is a real, non-symlink directory",
                False,
                f"{builds_root} does not exist or is not a directory",
            )
        )
        return checks, True, []
    checks.append(AuditCheck("root", "builds_root is a real, non-symlink directory", True))

    builds_dir = builds_root / "builds"
    latest_path = builds_root / "latest.json"

    # --- latest.json ------------------------------------------------------
    # A successful `arrange`/`package --builds-root` publish always writes
    # `latest.json` (`_publish_artifacts_to_builds_root` -> trailing
    # `_update_builds_latest_pointer`); the one state that leaves it absent
    # after a real digest directory was already published is the documented
    # self-heal failure mode (`_publish_artifacts_to_builds_root`'s own
    # docstring) — itself a broken, to-be-retried state, so absence here is
    # treated as FAIL rather than a silently-legitimate "not published yet".
    if latest_path.is_symlink():
        checks.append(
            AuditCheck(
                "latest",
                "latest.json is present and a regular, non-symlink file",
                False,
                f"{latest_path} is a symlink",
            )
        )
    elif not latest_path.exists():
        checks.append(
            AuditCheck(
                "latest",
                "latest.json is present and a regular, non-symlink file",
                False,
                f"{latest_path} is missing (a builds-root publish always writes latest.json)",
            )
        )
    elif not latest_path.is_file():
        checks.append(
            AuditCheck(
                "latest",
                "latest.json is present and a regular, non-symlink file",
                False,
                f"{latest_path} is not a regular file",
            )
        )
    else:
        checks.append(
            AuditCheck("latest", "latest.json is present and a regular, non-symlink file", True)
        )
        try:
            latest_data = json.loads(latest_path.read_bytes())
        except (OSError, json.JSONDecodeError) as exc:
            checks.append(AuditCheck("latest", "latest.json parses as JSON", False, str(exc)))
        else:
            if not isinstance(latest_data, dict):
                checks.append(
                    AuditCheck(
                        "latest", "latest.json parses as JSON", False, "must be a JSON object"
                    )
                )
            else:
                checks.append(AuditCheck("latest", "latest.json parses as JSON", True))
                checks.append(
                    _eq_check(
                        "latest",
                        "latest.json schema_version is builds-latest/0.1",
                        latest_data.get("schema_version"),
                        BUILDS_LATEST_SCHEMA_VERSION,
                    )
                )
                digest = latest_data.get("content_digest")
                if isinstance(digest, str) and _SHA256_RE.match(digest):
                    checks.append(
                        AuditCheck(
                            "latest", "latest.json content_digest is a 64-hex sha256 string", True
                        )
                    )
                    pointed_dir = builds_dir / digest
                    if pointed_dir.is_dir() and not pointed_dir.is_symlink():
                        checks.append(
                            AuditCheck(
                                "latest",
                                "latest.json content_digest has a corresponding "
                                "builds/ digest directory",
                                True,
                            )
                        )
                    else:
                        checks.append(
                            AuditCheck(
                                "latest",
                                "latest.json content_digest has a corresponding "
                                "builds/ digest directory",
                                False,
                                f"{pointed_dir} does not exist as a real, non-symlink directory",
                            )
                        )
                else:
                    checks.append(
                        AuditCheck(
                            "latest",
                            "latest.json content_digest is a 64-hex sha256 string",
                            False,
                            f"got {digest!r}",
                        )
                    )

    # --- unexpected root-level entries -------------------------------------
    try:
        root_entries = sorted(os.scandir(builds_root), key=lambda entry: entry.name)
    except OSError as exc:
        checks.append(AuditCheck("root-entries", "builds_root is listable", False, str(exc)))
        root_entries = []
    for entry in root_entries:
        if entry.name in ("latest.json", "builds"):
            continue
        kind = "symlink" if entry.is_symlink() else ("directory" if entry.is_dir() else "file")
        checks.append(
            AuditCheck(
                "root-entries",
                f"no unexpected root entry {entry.name!r}",
                False,
                f"unexpected {kind} directly under builds_root",
            )
        )

    # --- builds/ directory itself + its direct entries ----------------------
    valid_digest_dir_names: list[str] = []
    if builds_dir.is_symlink():
        checks.append(
            AuditCheck(
                "builds-dir",
                "builds/ is a real, non-symlink directory",
                False,
                f"{builds_dir} is a symlink",
            )
        )
        return checks, False, valid_digest_dir_names
    if not builds_dir.exists():
        checks.append(
            AuditCheck(
                "builds-dir",
                "builds/ is a real, non-symlink directory",
                False,
                f"{builds_dir} is missing",
            )
        )
        return checks, False, valid_digest_dir_names
    if not builds_dir.is_dir():
        checks.append(
            AuditCheck(
                "builds-dir",
                "builds/ is a real, non-symlink directory",
                False,
                f"{builds_dir} is not a directory",
            )
        )
        return checks, False, valid_digest_dir_names
    checks.append(AuditCheck("builds-dir", "builds/ is a real, non-symlink directory", True))

    try:
        build_entries = sorted(os.scandir(builds_dir), key=lambda entry: entry.name)
    except OSError as exc:
        checks.append(AuditCheck("digest-names", "builds/ is listable", False, str(exc)))
        build_entries = []

    for entry in build_entries:
        name = entry.name
        if entry.is_symlink():
            checks.append(
                AuditCheck(
                    "digest-names",
                    f"builds/{name} is not a symlink",
                    False,
                    f"{builds_dir / name} is a symlink",
                )
            )
            continue
        if not entry.is_dir():
            checks.append(
                AuditCheck(
                    "digest-names",
                    f"builds/{name} is a directory",
                    False,
                    f"{builds_dir / name} is not a directory",
                )
            )
            continue
        if not _SHA256_RE.match(name):
            checks.append(
                AuditCheck(
                    "digest-names",
                    f"builds/{name} is a valid 64-hex content_digest name",
                    False,
                    f"{name!r} is not a 64-hex sha256 string",
                )
            )
            continue
        if name == _BUILDS_LOCATOR_PLACEHOLDER_DIGEST:
            checks.append(
                AuditCheck(
                    "digest-names",
                    f"builds/{name} is not the reserved locator-placeholder digest",
                    False,
                    f"{builds_dir / name} occupies the reserved all-zero placeholder "
                    "digest — never a real completed build",
                )
            )
            continue
        valid_digest_dir_names.append(name)

    return checks, False, valid_digest_dir_names


def _undeclared_entry_checks(digest_dir: Path, allowed_names: set[str]) -> list[AuditCheck]:
    """`digest_dir` を再帰走査し、`allowed_names`（descriptive file + 宣言 artifact
    群、いずれもフラットなファイル名）に含まれない一切のエントリ（ファイル・
    サブディレクトリ・symlink）を個別に FAIL 列挙する。

    `os.walk(..., followlinks=False)`（デフォルト）はシンボリックリンクの
    ディレクトリへは決して降りないため、symlink 経由の循環走査は起こらない。
    正規の build は宣言 artifact がすべてフラットなファイル名である以上サブ
    ディレクトリを一切持たないので、見つかったサブディレクトリ自体も常に
    「宣言外」として列挙対象になる（その配下も併せて列挙される）。
    """
    checks: list[AuditCheck] = []
    found: list[tuple[str, str]] = []
    try:
        for dirpath, dirnames, filenames in os.walk(digest_dir, followlinks=False):
            current_dir = Path(dirpath)
            for dirname in dirnames:
                full = current_dir / dirname
                rel = full.relative_to(digest_dir).as_posix()
                if rel in allowed_names:
                    continue
                kind = "symlink" if full.is_symlink() else "directory"
                found.append((rel, kind))
            for filename in filenames:
                full = current_dir / filename
                rel = full.relative_to(digest_dir).as_posix()
                if rel in allowed_names:
                    continue
                kind = "symlink" if full.is_symlink() else "file"
                found.append((rel, kind))
    except OSError as exc:
        checks.append(AuditCheck("undeclared", "digest directory is listable", False, str(exc)))
        return checks

    if not found:
        checks.append(
            AuditCheck("undeclared", "digest directory contains no undeclared entries", True)
        )
        return checks

    for rel, kind in sorted(found):
        checks.append(
            AuditCheck(
                "undeclared",
                f"no undeclared entry at {rel!r}",
                False,
                f"unexpected {kind} not declared by the descriptive file",
            )
        )
    return checks


def _audit_digest_dir(digest_dir: Path) -> BuildAuditResult:
    """1 つの `<builds_root>/builds/<content_digest>/` ディレクトリを監査する。

    descriptive file の特定に失敗した場合（0 個・2 個存在）は、それ 1 件の FAIL
    だけを積んで即座に打ち切る — 以降の内容整合チェックは descriptive file が
    一意に定まらない限り成立しないため。
    """
    name = digest_dir.name
    checks: list[AuditCheck] = []

    present_kinds = [
        filename
        for filename in _DESCRIPTOR_KINDS
        if (digest_dir / filename).exists() or (digest_dir / filename).is_symlink()
    ]
    if len(present_kinds) != 1:
        detail = (
            f"found {sorted(present_kinds)!r}"
            if present_kinds
            else "found neither arrangement_bundle.json nor compilation_report.json"
        )
        checks.append(
            AuditCheck(
                "descriptor",
                "exactly one descriptive file present "
                "(arrangement_bundle.json xor compilation_report.json)",
                False,
                detail,
            )
        )
        return BuildAuditResult(digest_dir_name=name, checks=tuple(checks))
    checks.append(
        AuditCheck(
            "descriptor",
            "exactly one descriptive file present "
            "(arrangement_bundle.json xor compilation_report.json)",
            True,
        )
    )

    descriptive_filename = present_kinds[0]
    kind = _DESCRIPTOR_KINDS[descriptive_filename]
    descriptive_path = digest_dir / descriptive_filename

    reg_err = _regular_file_error(
        descriptive_path, label=f"digest directory {digest_dir} descriptive file"
    )
    if reg_err is not None:
        checks.append(
            AuditCheck(
                "descriptor",
                f"{descriptive_filename} is a regular, non-symlink file",
                False,
                reg_err,
            )
        )
        return BuildAuditResult(digest_dir_name=name, checks=tuple(checks))
    checks.append(
        AuditCheck("descriptor", f"{descriptive_filename} is a regular, non-symlink file", True)
    )

    try:
        raw_bytes = descriptive_path.read_bytes()
    except OSError as exc:
        checks.append(
            AuditCheck("descriptor", f"{descriptive_filename} is readable", False, str(exc))
        )
        return BuildAuditResult(digest_dir_name=name, checks=tuple(checks))

    try:
        parsed = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        checks.append(
            AuditCheck(
                "descriptor", f"{descriptive_filename} parses as JSON", False, str(exc)
            )
        )
        return BuildAuditResult(digest_dir_name=name, checks=tuple(checks))
    if not isinstance(parsed, dict):
        checks.append(
            AuditCheck(
                "descriptor",
                f"{descriptive_filename} parses as JSON",
                False,
                f"{descriptive_filename} must be a JSON object",
            )
        )
        return BuildAuditResult(digest_dir_name=name, checks=tuple(checks))
    checks.append(AuditCheck("descriptor", f"{descriptive_filename} parses as JSON", True))

    try:
        kind.validate(parsed)
    except ValueError as exc:
        checks.append(
            AuditCheck(
                "descriptor",
                f"{descriptive_filename} validates against {kind.label} schema",
                False,
                str(exc),
            )
        )
        return BuildAuditResult(digest_dir_name=name, checks=tuple(checks))
    checks.append(
        AuditCheck(
            "descriptor", f"{descriptive_filename} validates against {kind.label} schema", True
        )
    )

    try:
        digest_inputs = kind.digest_inputs(parsed)
    except (KeyError, TypeError, AttributeError) as exc:
        checks.append(
            AuditCheck(
                "content-digest",
                "content_digest inputs are extractable from the descriptive file",
                False,
                str(exc),
            )
        )
        return BuildAuditResult(digest_dir_name=name, checks=tuple(checks))
    checks.append(
        AuditCheck(
            "content-digest",
            "content_digest inputs are extractable from the descriptive file",
            True,
        )
    )

    recomputed_digest = compute_content_digest(digest_inputs)
    checks.append(
        _eq_check(
            "content-digest",
            "descriptive file's content_digest field matches independently "
            "recomputed content_digest",
            parsed.get("content_digest"),
            recomputed_digest,
        )
    )
    checks.append(
        _eq_check(
            "content-digest",
            "digest directory name matches independently recomputed content_digest",
            name,
            recomputed_digest,
        )
    )

    for artifact_name in sorted(digest_inputs):
        expected_sha256 = digest_inputs[artifact_name]
        prefix = f"artifact {artifact_name!r}"

        literal_path = digest_dir / artifact_name
        reg_err = _regular_file_error(literal_path, label=f"{prefix} ({literal_path})")
        if reg_err is not None:
            checks.append(
                AuditCheck("artifacts", f"{prefix}: is a regular, non-symlink file", False, reg_err)
            )
            continue
        checks.append(AuditCheck("artifacts", f"{prefix}: is a regular, non-symlink file", True))

        try:
            resolved_path = resolve_confined(artifact_name, digest_dir)
        except PathConfinementError as exc:
            checks.append(
                AuditCheck(
                    "artifacts", f"{prefix}: resolves inside the digest directory", False, str(exc)
                )
            )
            continue
        checks.append(
            AuditCheck("artifacts", f"{prefix}: resolves inside the digest directory", True)
        )

        if not resolved_path.exists():
            checks.append(
                AuditCheck(
                    "artifacts",
                    f"{prefix}: sha256 matches declared hash",
                    False,
                    f"{resolved_path} does not exist",
                )
            )
            continue
        try:
            actual_bytes = resolved_path.read_bytes()
        except OSError as exc:
            checks.append(
                AuditCheck(
                    "artifacts", f"{prefix}: sha256 matches declared hash", False, str(exc)
                )
            )
            continue
        actual_sha256 = hashlib.sha256(actual_bytes).hexdigest()
        checks.append(
            _eq_check(
                "artifacts", f"{prefix}: sha256 matches declared hash", actual_sha256, expected_sha256
            )
        )

    allowed_names = {descriptive_filename, *digest_inputs.keys()}
    checks.extend(_undeclared_entry_checks(digest_dir, allowed_names))

    return BuildAuditResult(digest_dir_name=name, checks=tuple(checks))


def audit_builds_root(builds_root: Path) -> BuildsRootAuditReport:
    """`<builds_root>/` ツリー全体を再帰的に読み取り専用監査する。

    `builds_root` 自身が実在の非 symlink ディレクトリでない場合のみ
    `aborted=True` で即座に打ち切る（それ以外のルート不整合は通常の FAIL として
    収集し、per-build 監査は有効な digest ディレクトリ集合に対して続行する）。
    """
    root_checks, aborted, valid_digest_dir_names = _audit_root(builds_root)
    if aborted:
        return BuildsRootAuditReport(root_checks=tuple(root_checks), builds=(), aborted=True)

    builds_dir = builds_root / "builds"
    build_results = tuple(
        _audit_digest_dir(builds_dir / name) for name in sorted(valid_digest_dir_names)
    )
    return BuildsRootAuditReport(
        root_checks=tuple(root_checks), builds=build_results, aborted=False
    )
