"""svprpe verify."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Optional

import typer

from svp_rpe.cli._app import app, console

if TYPE_CHECKING:
    from svp_rpe.arrange.verify import VerifyReport
    from svp_rpe.cli.builds_audit import AuditCheck, BuildsRootAuditReport


def _render_verify_report(
    report: "VerifyReport", *, package_path: Path, group_titles: dict[str, str]
) -> None:
    console.print(f"[bold]Verify: {package_path}[/bold]")
    current_group: Optional[str] = None
    for check in report.checks:
        if check.group != current_group:
            current_group = check.group
            console.print(f"[bold]{group_titles.get(current_group, current_group)}[/bold]")
        status = "[green]ok[/green]  " if check.ok else "[red]FAIL[/red]"
        line = f"  {status} {check.label}"
        if not check.ok and check.detail:
            line += f" — {check.detail}"
        console.print(line)

    if report.aborted:
        console.print(
            "[yellow]note: a structural load failure stopped further checks "
            "before every group could run[/yellow]"
        )

    checked = len(report.checks)
    failed = len(report.failures)
    summary_color = "red" if failed else "green"
    console.print(f"[{summary_color}]checked {checked}, failed {failed}[/{summary_color}]")


def _print_audit_checks(checks: Iterable["AuditCheck"]) -> None:
    current_group: Optional[str] = None
    for check in checks:
        if check.group != current_group:
            current_group = check.group
            console.print(f"[bold]{current_group}[/bold]")
        status = "[green]ok[/green]  " if check.ok else "[red]FAIL[/red]"
        line = f"  {status} {check.label}"
        if not check.ok and check.detail:
            line += f" — {check.detail}"
        console.print(line)


def _render_builds_root_audit_report(
    report: "BuildsRootAuditReport", *, builds_root: Path
) -> None:
    console.print(f"[bold]Verify (builds-root): {builds_root}[/bold]")
    console.print("[bold]root[/bold]")
    _print_audit_checks(report.root_checks)

    if report.aborted:
        console.print(
            "[yellow]note: builds_root itself failed a structural check; no build "
            "directories were audited[/yellow]"
        )

    for build in report.builds:
        console.print(f"[bold]build {build.digest_dir_name}[/bold]")
        _print_audit_checks(build.checks)

    checked = len(report.root_checks) + sum(len(build.checks) for build in report.builds)
    failed = len(report.failures)
    summary_color = "red" if failed else "green"
    console.print(
        f"[{summary_color}]builds {len(report.builds)}, checked {checked}, "
        f"failed {failed}[/{summary_color}]"
    )


@app.command("verify")
def verify_cmd(
    package_json: Optional[str] = typer.Argument(
        None,
        help=(
            "Path to performance_package.json, or the directory containing it "
            "(mutually exclusive with --builds-root)"
        ),
    ),
    manifest: Optional[str] = typer.Option(
        None,
        "--manifest",
        help=(
            "Path to the IdentityManifest YAML used to compile the package "
            "(required unless --builds-root; mutually exclusive with --builds-root)"
        ),
    ),
    builds_root: Optional[str] = typer.Option(
        None,
        "--builds-root",
        help=(
            "Recursively audit an entire <root>/ tree published by `arrange`/"
            "`package --builds-root` (structural audit only, no --manifest; "
            "mutually exclusive with the package argument and --manifest)"
        ),
    ),
) -> None:
    """Exhaustively verify a PerformancePackage's own internal consistency (V1-V4).

    Two mutually exclusive modes, selected by whether `--builds-root` is given
    (violating the split — `--builds-root` together with `package_json` and/or
    `--manifest`, or neither `package_json`/`--manifest` nor `--builds-root` —
    exits `2`, mirroring `arrange`/`package`'s own `--output-dir`/`--builds-root`
    exclusivity convention):

    - **Single-package mode** (`package_json` + `--manifest`, both required):
      everything documented below (V1-V4). Unchanged by this option's addition.
    - **`--builds-root` mode**: a *different, larger* surface — recursively
      audits an entire `<root>/` tree published by `arrange`/`package
      --builds-root` (see `cli/builds_audit.py`'s `audit_builds_root`), not any
      single package. This mode needs no `--manifest` and never touches V3/V4's
      `IdentityManifest` chain or `channel_artifacts` cross-references — those
      remain single-package-mode-only, since a builds-root tree alone carries no
      manifest to check against. It checks `<root>/latest.json`'s shape and
      pointer validity, every `<root>/builds/<content_digest>/` directory's
      descriptive file (`arrangement_bundle.json` or `compilation_report.json`)
      schema-validity and self-consistent `content_digest`, every artifact that
      file declares (existence, non-symlink, hash), and — the part neither this
      command's single-package V4 nor `cli/builds_root.py`'s no-op-publish
      blessing check ever do — every entry each digest directory contains that
      its descriptive file does *not* declare. Structural audit only: like
      single-package mode, it repairs nothing and renders no musical/perceptual
      verdict.

    Read-only, full internal-consistency instrument — the receiving end PR #187
    review round 16 named when it drew `observe`'s scope boundary at
    *identifying* the observation target (manifest sha256 / work_id /
    anchor_statuses id set — see `cli/observe_cmd.py`'s docstring). `verify`
    picks up exactly there and checks every remaining fact a `package` + its
    sibling `compilation_report.json` + the `--manifest` chain declare about
    themselves:

    - V1 — the package file itself: not a symlink/non-regular file, reads, and
      validates against `PerformancePackage`'s schema (which already enforces
      `channel_artifacts` cross-references via `_validate_delivery_references`
      — not re-checked here).
    - V2 — the co-located `compilation_report.json`: present, valid, and its
      `work_id` / `generator` / `generator_variant` / `inputs` /
      `package_sha256` / `content_digest` all agree with the package (the
      digest is independently recomputed via
      `arrange.bundle.compute_content_digest`, not merely read back).
    - V3 — the `--manifest` chain: sha256-pins to
      `package.inputs.identity_manifest.sha256`, parses and hash-verifies
      every source/anchor artifact it declares (via the same
      `parse_identity_manifest_with_artifacts` `observe` uses), and its
      `work_id` / anchor id set agree with the package.
    - V4 — every `channel_artifacts` entry: `artifact_base.locator` resolves
      (relative to the package directory) to an existing directory —
      legitimately *outside* the package directory, `".."` being the normal
      shape, so no confinement applies there — that must itself resolve to
      the same directory as the supplied `--manifest`'s own parent directory
      (Codex review round 2, PR #190: an existing directory alone is not
      enough — a hash-matching artifact copy planted anywhere else, with the
      package/report hashes recomputed to match, would otherwise sail
      through every other V4 check). Its `artifact` also resolves confined
      under that base directory (`arrange.pathsafe.resolve_confined`), its
      bytes hash to the declared `artifact_sha256`, and its `anchor_id` is
      one the manifest actually declares — and, once that anchor_id is
      found, its `artifact` / `artifact_sha256` / `artifact_type` /
      `media_type` / `format_version` are each compared field-by-field
      against the same-id manifest anchor (not just the anchor id set), so a
      reference silently retargeted to a different file under `artifact_base`
      is caught even when its package-local hash still matches that file's
      bytes.

    Every group's checks are collected in full before anything is printed — a
    single failure never hides the rest (exit 1 if *any* check across all
    groups fails). The only exception is a structural load failure: the
    package file itself failing to parse/validate (V1), or the manifest
    failing to parse/validate against `IdentityManifest`'s schema (V3;
    artifact hash mismatches are ordinary collected failures, not structural)
    — either aborts every check that depends on the missing object (V1 failing
    skips V2-V4 entirely; V3 failing skips only V4, since V2 needs neither the
    manifest nor V3's outcome).

    Out of scope in both modes (follow-up, not this command's job):
    cross-checking against an `ObservationReport` sidecar (AR2-3 depends on
    structure-anchor policy that hasn't landed); rewriting or repairing
    anything found broken (this command writes nothing, ever, in either mode);
    and any musical/perceptual verdict — `verify` only ever reports structural
    pass/fail, never adherence quality. A recursive `--builds-root` tree audit
    used to be out of scope too (see `docs/cli.md`'s `arrange`/`package` notes
    on the no-op-publish blessing check, which is deliberately *not* that — a
    distinct, larger surface than a single digest directory) — that is exactly
    what `--builds-root` mode above now covers.
    """
    if builds_root is not None:
        if package_json is not None or manifest is not None:
            raise typer.BadParameter(
                "--builds-root is mutually exclusive with the package argument and --manifest",
                param_hint="package_json / --manifest / --builds-root",
            )
        from svp_rpe.cli.builds_audit import audit_builds_root

        builds_root_path = Path(builds_root)
        audit_report = audit_builds_root(builds_root_path)
        _render_builds_root_audit_report(audit_report, builds_root=builds_root_path)

        if not audit_report.ok:
            raise typer.Exit(code=1)
        return

    if package_json is None or manifest is None:
        raise typer.BadParameter(
            "package_json and --manifest are both required unless --builds-root is given",
            param_hint="package_json / --manifest / --builds-root",
        )

    from svp_rpe.arrange.package import PERFORMANCE_PACKAGE_FILENAME
    from svp_rpe.arrange.verify import GROUP_TITLES, verify_package

    package_input_path = Path(package_json)
    package_path = (
        package_input_path / PERFORMANCE_PACKAGE_FILENAME
        if package_input_path.is_dir()
        else package_input_path
    )
    manifest_path = Path(manifest)

    report = verify_package(package_path, manifest_path)
    _render_verify_report(report, package_path=package_path, group_titles=GROUP_TITLES)

    if not report.ok:
        raise typer.Exit(code=1)
