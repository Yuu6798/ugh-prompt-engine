"""svprpe verify: PerformancePackage internal-consistency instrument (V1-V4).

Fixture construction reuses `tests.test_observe`'s existing helper factories
(`_cli_package_args` / `_build_scratch_identity_dir`) — a scratch copy of the
`examples/arrangement/midnight_signal` identity fixture set under `tmp_path`,
never mutating the real checked-in fixtures — so a package can be compiled via
the real `package` CLI command and then selectively tampered with per test.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Callable

from typer.testing import CliRunner

from svp_rpe.arrange.bundle import compute_content_digest
from svp_rpe.arrange.package import PERFORMANCE_PACKAGE_FILENAME
from svp_rpe.cli import app
from tests.test_observe import FIXTURE_DIR, _build_scratch_identity_dir, _cli_package_args


def _build_package(tmp_path: Path, *, pkg_dirname: str = "pkg") -> tuple[Path, Path]:
    """Compile a real, validly-linked package under `tmp_path/pkg_dirname`
    against a scratch copy of the midnight_signal identity fixture set.
    Returns `(pkg_dir, manifest_path)`."""
    chord_bytes = (FIXTURE_DIR / "identity" / "chord_progression.json").read_bytes()
    manifest_path = _build_scratch_identity_dir(tmp_path, chord_artifact_bytes=chord_bytes)
    pkg_dir = tmp_path / pkg_dirname
    result = CliRunner().invoke(app, _cli_package_args(pkg_dir, identity_yaml=manifest_path))
    assert result.exit_code == 0, result.output
    return pkg_dir, manifest_path


def _verify_args(package_arg: Path, manifest_path: Path) -> list[str]:
    return ["verify", str(package_arg), "--manifest", str(manifest_path)]


def _flat(text: str) -> str:
    """Collapse rich console line-wrapping (CliRunner captures output at a
    fixed terminal width, so a long check label can be split mid-phrase
    across a hard-wrapped line break) so `in`-substring assertions against a
    check's full label text are robust regardless of where rich wraps it."""
    return " ".join(text.split())


def test_verify_cli_accepts_a_valid_package(tmp_path: Path) -> None:
    pkg_dir, manifest_path = _build_package(tmp_path)

    result = CliRunner().invoke(
        app, _verify_args(pkg_dir / "performance_package.json", manifest_path)
    )

    assert result.exit_code == 0, result.output
    assert "FAIL" not in result.output
    assert "failed 0" in _flat(result.output)


def test_verify_cli_accepts_package_directory_argument(tmp_path: Path) -> None:
    """The package argument may be the containing directory instead of the
    `performance_package.json` path directly (mirrors the Design Memo's CLI
    argument shape)."""
    pkg_dir, manifest_path = _build_package(tmp_path)

    result = CliRunner().invoke(app, _verify_args(pkg_dir, manifest_path))

    assert result.exit_code == 0, result.output
    assert "failed 0" in _flat(result.output)


def test_verify_rejects_tampered_artifact_bytes(tmp_path: Path) -> None:
    """A tampered anchor artifact is caught by V4's own independent
    read+hash against the package's `artifact_sha256` — even though the same
    tamper also breaks V3's manifest-side hash verification (both read the
    same on-disk file), V4 must still run and report its own finding rather
    than being blinded by V3's failure."""
    pkg_dir, manifest_path = _build_package(tmp_path)
    lyrics_path = tmp_path / "identity" / "lyrics.txt"
    lyrics_path.write_bytes(b"tampered lyrics content")

    result = CliRunner().invoke(app, _verify_args(pkg_dir, manifest_path))

    assert result.exit_code == 1
    flat_output = _flat(result.output)
    assert "V4: channel_artifacts" in flat_output
    assert "artifact sha256 matches artifact_sha256" in flat_output


def test_verify_rejects_missing_artifact(tmp_path: Path) -> None:
    pkg_dir, manifest_path = _build_package(tmp_path)
    lyrics_path = tmp_path / "identity" / "lyrics.txt"
    lyrics_path.unlink()

    result = CliRunner().invoke(app, _verify_args(pkg_dir, manifest_path))

    assert result.exit_code == 1
    assert "V4: channel_artifacts" in _flat(result.output)


def test_verify_rejects_symlinked_artifact(tmp_path: Path) -> None:
    pkg_dir, manifest_path = _build_package(tmp_path)
    lyrics_path = tmp_path / "identity" / "lyrics.txt"
    real_target = tmp_path / "identity" / "lyrics_real.txt"
    real_target.write_bytes(lyrics_path.read_bytes())
    lyrics_path.unlink()
    lyrics_path.symlink_to(real_target)

    result = CliRunner().invoke(app, _verify_args(pkg_dir, manifest_path))

    assert result.exit_code == 1
    assert "is a symlink" in _flat(result.output)


def test_verify_rejects_missing_compilation_report(tmp_path: Path) -> None:
    pkg_dir, manifest_path = _build_package(tmp_path)
    (pkg_dir / "compilation_report.json").unlink()

    result = CliRunner().invoke(app, _verify_args(pkg_dir, manifest_path))

    assert result.exit_code == 1
    flat_output = _flat(result.output)
    assert "V2: compilation report" in flat_output
    assert "compilation_report.json is a regular, non-symlink file" in flat_output


def _patch_report(pkg_dir: Path, **fields: object) -> None:
    report_path = pkg_dir / "compilation_report.json"
    data = json.loads(report_path.read_text(encoding="utf-8"))
    data.update(fields)
    report_path.write_text(json.dumps(data), encoding="utf-8")


def _patch_package(pkg_dir: Path, mutate: Callable[[dict], None]) -> None:
    """Hand-edit `performance_package.json` in place via `mutate(data)`."""
    package_path = pkg_dir / "performance_package.json"
    data = json.loads(package_path.read_text(encoding="utf-8"))
    mutate(data)
    package_path.write_text(json.dumps(data), encoding="utf-8")


def _recompute_report_hashes(pkg_dir: Path) -> None:
    """Recompute `compilation_report.json`'s `package_sha256` / `content_digest`
    to match the current (possibly hand-tampered) `performance_package.json`
    bytes. Mirrors the Codex review round-1 P2 attack shape (PR #190): the
    package and its sibling report are edited *together* so V1/V2 stay green
    and any channel-reference tamper is left for V4 alone to catch."""
    package_bytes = (pkg_dir / "performance_package.json").read_bytes()
    package_sha256 = hashlib.sha256(package_bytes).hexdigest()
    content_digest = compute_content_digest({PERFORMANCE_PACKAGE_FILENAME: package_sha256})
    _patch_report(pkg_dir, package_sha256=package_sha256, content_digest=content_digest)


def test_verify_rejects_tampered_package_sha256_in_report(tmp_path: Path) -> None:
    pkg_dir, manifest_path = _build_package(tmp_path)
    _patch_report(pkg_dir, package_sha256="0" * 64)

    result = CliRunner().invoke(app, _verify_args(pkg_dir, manifest_path))

    assert result.exit_code == 1
    assert "report.package_sha256 matches computed package sha256" in _flat(result.output)


def test_verify_rejects_tampered_content_digest_in_report(tmp_path: Path) -> None:
    pkg_dir, manifest_path = _build_package(tmp_path)
    _patch_report(pkg_dir, content_digest="1" * 64)

    result = CliRunner().invoke(app, _verify_args(pkg_dir, manifest_path))

    assert result.exit_code == 1
    assert (
        "report.content_digest matches independently recomputed content_digest"
        in _flat(result.output)
    )


def test_verify_rejects_manifest_sha256_mismatch(tmp_path: Path) -> None:
    """A `--manifest` file whose bytes no longer hash-match
    `package.inputs.identity_manifest.sha256` (e.g. it was regenerated after
    packaging) is caught by V3's direct sha256 comparison, independent of
    whether the manifest still parses and its own anchors still hash-verify
    (they do here — only a trailing comment byte changes)."""
    pkg_dir, manifest_path = _build_package(tmp_path)
    tampered_manifest = tmp_path / "identity_manifest_tampered.yaml"
    tampered_manifest.write_bytes(manifest_path.read_bytes() + b"\n# trailing comment\n")

    result = CliRunner().invoke(app, _verify_args(pkg_dir, tampered_manifest))

    assert result.exit_code == 1
    flat_output = _flat(result.output)
    assert (
        "identity manifest sha256 matches package.inputs.identity_manifest.sha256"
        in flat_output
    )
    # the manifest itself is still schema-valid and its own anchors still
    # hash-verify against the (untouched) artifact files, so V4 still runs
    # to completion against every channel_artifacts entry.
    assert "V4: channel_artifacts" in flat_output


def test_verify_rejects_work_id_mismatch_in_report(tmp_path: Path) -> None:
    pkg_dir, manifest_path = _build_package(tmp_path)
    _patch_report(pkg_dir, work_id="a-different-work-id")

    result = CliRunner().invoke(app, _verify_args(pkg_dir, manifest_path))

    assert result.exit_code == 1
    assert "report.work_id matches package.work_id" in _flat(result.output)


def test_verify_collects_all_failures_across_groups_in_a_single_run(tmp_path: Path) -> None:
    """Design Memo test 10: two independent tamper points (V2's
    `package_sha256` and V4's artifact bytes) are both reported by the same
    invocation — a single failure never hides the rest."""
    pkg_dir, manifest_path = _build_package(tmp_path)
    _patch_report(pkg_dir, package_sha256="0" * 64)
    melody_path = tmp_path / "identity" / "melody_notes.json"
    melody_path.write_bytes(b"[]")

    result = CliRunner().invoke(app, _verify_args(pkg_dir, manifest_path))

    assert result.exit_code == 1
    flat_output = _flat(result.output)
    assert "report.package_sha256 matches computed package sha256" in flat_output
    assert "artifact sha256 matches artifact_sha256" in flat_output
    fail_count = result.output.count("FAIL")
    assert fail_count >= 2


def test_verify_rejects_reference_retargeted_to_a_different_file(tmp_path: Path) -> None:
    """Codex review round 1 (PR #190, P2): V4 used to reduce the manifest to
    an id set only, and never compared `reference.artifact` /
    `artifact_sha256` / etc. against the same-id manifest anchor. That let an
    attacker retarget the `lyrics` channel reference to a *different* real
    file under `artifact_base` (here: the `melody` anchor's own artifact) and
    recompute both the declared `artifact_sha256` and the sibling report's
    `package_sha256` / `content_digest` to match it byte-for-byte. Every
    *old* V4 check for this reference (locator resolves, confined, bytes hash
    to the declared sha, anchor_id known to the manifest) and every V1-V3
    check all still pass under that tamper -- only a field-by-field
    reference-vs-manifest-anchor comparison catches it."""
    from svp_rpe.arrange.verify import verify_package

    pkg_dir, manifest_path = _build_package(tmp_path)
    melody_bytes = (tmp_path / "identity" / "melody_notes.json").read_bytes()
    melody_sha256 = hashlib.sha256(melody_bytes).hexdigest()

    def _retarget(data: dict) -> None:
        references = data["channel_artifacts"]["lyrics_text"]
        (reference,) = [r for r in references if r["anchor_id"] == "lyrics"]
        assert reference["artifact"] != "identity/melody_notes.json"
        reference["artifact"] = "identity/melody_notes.json"
        reference["artifact_sha256"] = melody_sha256

    _patch_package(pkg_dir, _retarget)
    _recompute_report_hashes(pkg_dir)

    report = verify_package(pkg_dir / "performance_package.json", manifest_path)

    assert not report.ok
    checks_by_label = {check.label: check for check in report.checks}
    prefix = "channel 'lyrics_text' anchor 'lyrics'"
    # every *old* V1-V3 + V4-physical check still passes -- the tamper is
    # internally self-consistent by construction.
    assert not report.aborted
    assert {check.group for check in report.checks} == {"V1", "V2", "V3", "V4"}
    for old_label in (
        f"{prefix}: artifact_base.locator resolves to an existing directory",
        f"{prefix}: artifact is a regular, non-symlink file",
        f"{prefix}: artifact resolves inside the artifact_base directory",
        f"{prefix}: artifact sha256 matches artifact_sha256",
        f"{prefix}: anchor_id is present in the manifest anchor set",
    ):
        assert checks_by_label[old_label].ok, old_label
    # the new field-by-field comparisons catch the retarget:
    assert not checks_by_label[
        f"{prefix}: reference.artifact matches manifest anchor artifact locator"
    ].ok
    assert not checks_by_label[
        f"{prefix}: reference.artifact_sha256 matches manifest anchor sha256"
    ].ok
    # artifact_type/media_type/format_version were left untouched, so those
    # still pass -- confirming the comparison is field-level, not all-or-nothing.
    for untouched_label in (
        f"{prefix}: reference.artifact_type matches manifest anchor artifact_type",
        f"{prefix}: reference.media_type matches manifest anchor media_type",
        f"{prefix}: reference.format_version matches manifest anchor format_version",
    ):
        assert checks_by_label[untouched_label].ok, untouched_label


def test_verify_rejects_reference_sha256_mismatched_against_manifest_anchor(
    tmp_path: Path,
) -> None:
    """Field-level granularity companion to the retargeting test above: even
    when `reference.artifact` still matches the same-id manifest anchor's
    artifact locator untouched, a declared `artifact_sha256` that diverges
    from the manifest anchor's own `sha256` is caught by its own dedicated
    check (distinct from the pre-existing artifact-bytes-hash check, which
    also fires here since the declared value no longer matches the real
    file's bytes either)."""
    from svp_rpe.arrange.verify import verify_package

    pkg_dir, manifest_path = _build_package(tmp_path)
    bogus_sha256 = "0" * 64

    def _corrupt_sha(data: dict) -> None:
        references = data["channel_artifacts"]["lyrics_text"]
        (reference,) = [r for r in references if r["anchor_id"] == "lyrics"]
        assert reference["artifact_sha256"] != bogus_sha256
        reference["artifact_sha256"] = bogus_sha256

    _patch_package(pkg_dir, _corrupt_sha)
    _recompute_report_hashes(pkg_dir)

    report = verify_package(pkg_dir / "performance_package.json", manifest_path)

    assert not report.ok
    checks_by_label = {check.label: check for check in report.checks}
    prefix = "channel 'lyrics_text' anchor 'lyrics'"
    # the artifact locator itself is untouched, so it still resolves and
    # still matches the manifest anchor's artifact field:
    assert checks_by_label[
        f"{prefix}: reference.artifact matches manifest anchor artifact locator"
    ].ok
    # but the declared sha256 no longer matches either the real file's bytes
    # (pre-existing check) or the manifest anchor's own sha256 (new check):
    assert not checks_by_label[f"{prefix}: artifact sha256 matches artifact_sha256"].ok
    assert not checks_by_label[
        f"{prefix}: reference.artifact_sha256 matches manifest anchor sha256"
    ].ok


def test_verify_rejects_artifact_base_pointing_outside_the_manifest_directory(
    tmp_path: Path,
) -> None:
    """Codex review round 2 (PR #190, P2): V4 step 1 used to accept *any*
    existing directory as `artifact_base.locator`, never checking it against
    the identity manifest directory `--manifest` actually points at. That let
    an attacker plant a byte-identical copy of the anchor artifact in an
    unrelated decoy directory, retarget `artifact_base.locator` to it, and
    recompute the sibling report's `package_sha256` / `content_digest` to
    match -- every *other* V4 check (locator resolves to an existing
    directory, artifact is a regular file, resolves confined under that base,
    bytes hash to the declared sha256, anchor_id known to the manifest, and
    every field-by-field reference-vs-manifest-anchor comparison) still
    passes under that tamper, since the decoy copy and its declared metadata
    are byte-for-byte identical to the real artifact -- only a check that the
    resolved base directory is the *same directory* as the supplied
    `--manifest`'s own parent catches it."""
    from svp_rpe.arrange.verify import verify_package

    pkg_dir, manifest_path = _build_package(tmp_path)
    lyrics_bytes = (tmp_path / "identity" / "lyrics.txt").read_bytes()
    decoy_dir = tmp_path / "decoy"
    (decoy_dir / "identity").mkdir(parents=True)
    (decoy_dir / "identity" / "lyrics.txt").write_bytes(lyrics_bytes)
    decoy_locator = os.path.relpath(str(decoy_dir), str(pkg_dir))

    def _retarget_base(data: dict) -> None:
        references = data["channel_artifacts"]["lyrics_text"]
        (reference,) = [r for r in references if r["anchor_id"] == "lyrics"]
        assert reference["artifact_base"]["locator"] != decoy_locator
        reference["artifact_base"]["locator"] = decoy_locator

    _patch_package(pkg_dir, _retarget_base)
    _recompute_report_hashes(pkg_dir)

    report = verify_package(pkg_dir / "performance_package.json", manifest_path)

    assert not report.ok
    assert not report.aborted
    checks_by_label = {check.label: check for check in report.checks}
    prefix = "channel 'lyrics_text' anchor 'lyrics'"
    # every *old* V4 check still passes -- the decoy is a byte-identical,
    # correctly-shaped copy, so nothing about it looks tampered in isolation.
    for old_label in (
        f"{prefix}: artifact_base.locator resolves to an existing directory",
        f"{prefix}: artifact is a regular, non-symlink file",
        f"{prefix}: artifact resolves inside the artifact_base directory",
        f"{prefix}: artifact sha256 matches artifact_sha256",
        f"{prefix}: anchor_id is present in the manifest anchor set",
        f"{prefix}: reference.artifact matches manifest anchor artifact locator",
        f"{prefix}: reference.artifact_sha256 matches manifest anchor sha256",
        f"{prefix}: reference.artifact_type matches manifest anchor artifact_type",
        f"{prefix}: reference.media_type matches manifest anchor media_type",
        f"{prefix}: reference.format_version matches manifest anchor format_version",
    ):
        assert checks_by_label[old_label].ok, old_label
    # only the new same-directory identity check catches the retarget:
    new_check = checks_by_label[
        f"{prefix}: artifact_base.locator resolves to the supplied identity manifest directory"
    ]
    assert not new_check.ok
    assert str(decoy_dir.resolve()) in (new_check.detail or "")
    assert str(tmp_path.resolve()) in (new_check.detail or "")


def test_verify_accepts_artifact_base_locator_matching_the_manifest_directory(
    tmp_path: Path,
) -> None:
    """Happy-path companion/regression guard for the new check above: the
    legitimately compiled package's `artifact_base.locator` (computed by
    `compile_performance_package` itself) must still resolve to the same
    directory as `--manifest`'s own parent, so the new check passes without
    any tampering involved."""
    from svp_rpe.arrange.verify import verify_package

    pkg_dir, manifest_path = _build_package(tmp_path)

    report = verify_package(pkg_dir / "performance_package.json", manifest_path)

    assert report.ok
    checks_by_label = {check.label: check for check in report.checks}
    prefix = "channel 'lyrics_text' anchor 'lyrics'"
    assert checks_by_label[
        f"{prefix}: artifact_base.locator resolves to the supplied identity manifest directory"
    ].ok


def _snapshot_tree(*roots: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for root in roots:
        if not root.exists():
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for filename in filenames:
                path = Path(dirpath) / filename
                snapshot[str(path)] = path.read_bytes()
    return snapshot


def test_verify_is_read_only(tmp_path: Path) -> None:
    """`verify` never writes: every file under the package directory and the
    identity fixture directory is byte-identical before and after a run
    (checked on the happy path, where every group actually executes)."""
    pkg_dir, manifest_path = _build_package(tmp_path)
    before = _snapshot_tree(tmp_path)

    result = CliRunner().invoke(app, _verify_args(pkg_dir, manifest_path))
    assert result.exit_code == 0, result.output

    after = _snapshot_tree(tmp_path)
    assert before == after


def test_verify_aborts_after_v1_structural_failure_without_running_other_groups(
    tmp_path: Path,
) -> None:
    pkg_dir, manifest_path = _build_package(tmp_path)
    (pkg_dir / "performance_package.json").write_bytes(b"not valid json")

    result = CliRunner().invoke(app, _verify_args(pkg_dir, manifest_path))

    assert result.exit_code == 1
    flat_output = _flat(result.output)
    assert "V1: package load" in flat_output
    assert "V2: compilation report" not in flat_output
    assert "V3: identity manifest chain" not in flat_output
    assert "V4: channel_artifacts" not in flat_output
    assert "structural load failure" in flat_output


def test_verify_aborts_v4_only_after_v3_structural_failure(tmp_path: Path) -> None:
    """A manifest that fails to parse at all (invalid YAML) is a structural
    V3 failure: V4 (which needs the manifest's anchor id set) is skipped, but
    V1/V2 — independent of the manifest — still ran and are reported."""
    pkg_dir, manifest_path = _build_package(tmp_path)
    malformed_manifest = tmp_path / "malformed_manifest.yaml"
    malformed_manifest.write_bytes(b"schema_version: [unclosed\n")

    result = CliRunner().invoke(app, _verify_args(pkg_dir, malformed_manifest))

    assert result.exit_code == 1
    flat_output = _flat(result.output)
    assert "V1: package load" in flat_output
    assert "V2: compilation report" in flat_output
    assert "V3: identity manifest chain" in flat_output
    assert "V4: channel_artifacts" not in flat_output
    assert "structural load failure" in flat_output


def test_verify_package_function_reports_ok_and_unaborted_on_a_valid_package(
    tmp_path: Path,
) -> None:
    """Core-API-level pin (not just CLI text matching): `verify_package`
    returns `ok=True`, `aborted=False`, and a non-empty, fully-passing check
    list on a legitimately compiled package."""
    from svp_rpe.arrange.verify import verify_package

    pkg_dir, manifest_path = _build_package(tmp_path)

    report = verify_package(pkg_dir / "performance_package.json", manifest_path)

    assert report.ok
    assert not report.aborted
    assert report.failures == ()
    assert len(report.checks) > 0
    assert {check.group for check in report.checks} == {"V1", "V2", "V3", "V4"}


def test_verify_package_function_hash_mismatch_detail_includes_expected_and_actual(
    tmp_path: Path,
) -> None:
    from svp_rpe.arrange.verify import verify_package

    pkg_dir, manifest_path = _build_package(tmp_path)
    _patch_report(pkg_dir, package_sha256="f" * 64)

    report = verify_package(pkg_dir / "performance_package.json", manifest_path)

    assert not report.ok
    failure = next(
        check
        for check in report.failures
        if check.label == "report.package_sha256 matches computed package sha256"
    )
    assert failure.detail is not None
    assert "f" * 64 in failure.detail
    package_bytes = (pkg_dir / "performance_package.json").read_bytes()
    assert hashlib.sha256(package_bytes).hexdigest() in failure.detail
