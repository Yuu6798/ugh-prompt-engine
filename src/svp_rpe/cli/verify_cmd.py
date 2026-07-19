"""svprpe verify."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

import typer

from svp_rpe.cli._app import app, console

if TYPE_CHECKING:
    from svp_rpe.arrange.verify import VerifyReport


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


@app.command("verify")
def verify_cmd(
    package_json: str = typer.Argument(
        ..., help="Path to performance_package.json, or the directory containing it"
    ),
    manifest: str = typer.Option(
        ..., "--manifest", help="Path to the IdentityManifest YAML used to compile the package"
    ),
) -> None:
    """Exhaustively verify a PerformancePackage's own internal consistency (V1-V4).

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
      shape, so no confinement applies there — and its `artifact` resolves
      confined under that base directory
      (`arrange.pathsafe.resolve_confined`), its bytes hash to the declared
      `artifact_sha256`, and its `anchor_id` is one the manifest actually
      declares — and, once that anchor_id is found, its `artifact` /
      `artifact_sha256` / `artifact_type` / `media_type` / `format_version`
      are each compared field-by-field against the same-id manifest anchor
      (not just the anchor id set), so a reference silently retargeted to a
      different file under `artifact_base` is caught even when its
      package-local hash still matches that file's bytes.

    Every group's checks are collected in full before anything is printed — a
    single failure never hides the rest (exit 1 if *any* check across all
    groups fails). The only exception is a structural load failure: the
    package file itself failing to parse/validate (V1), or the manifest
    failing to parse/validate against `IdentityManifest`'s schema (V3;
    artifact hash mismatches are ordinary collected failures, not structural)
    — either aborts every check that depends on the missing object (V1 failing
    skips V2-V4 entirely; V3 failing skips only V4, since V2 needs neither the
    manifest nor V3's outcome).

    Out of scope (follow-up, not this command's job): a recursive audit of an
    entire `--builds-root` tree (see `docs/cli.md`'s `arrange`/`package` notes
    on the no-op-publish blessing check, which is deliberately not that either
    — a distinct, larger surface than a single package); cross-checking
    against an `ObservationReport` sidecar (AR2-3 depends on structure-anchor
    policy that hasn't landed); rewriting or repairing anything found broken
    (this command writes nothing, ever); and any musical/perceptual verdict —
    `verify` only ever reports structural pass/fail, never adherence quality.
    """
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
