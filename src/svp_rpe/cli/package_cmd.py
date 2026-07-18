"""svprpe package."""
from __future__ import annotations

from typing import Any, Optional

import typer

from svp_rpe.cli._app import app, console


def _package_report_digest_inputs(descriptor: dict[str, Any]) -> dict[str, str]:
    """`{artifact_name: sha256}` from a parsed `compilation_report.json`.

    Mirrors `build_performance_package`'s own `compute_content_digest` call:
    the digest is computed over `{performance_package.json: package_sha256}`
    only, never over `invocation_provenance` (D-1).
    """
    from svp_rpe.arrange.package import PERFORMANCE_PACKAGE_FILENAME

    return {PERFORMANCE_PACKAGE_FILENAME: descriptor["package_sha256"]}


def _package_report_validate_descriptor(descriptor: dict[str, Any]) -> None:
    """Full schema validation of a parsed `compilation_report.json` (round 9).

    Reuses `CompilationReport` itself (`arrange/package.py`) — the same model
    `build_performance_package` constructs on the publish side — so there is
    no separate reader-only model to keep in sync for `package`.
    """
    from pydantic import ValidationError

    from svp_rpe.arrange.package import CompilationReport

    try:
        CompilationReport.model_validate(descriptor)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


# PR #184 review round 7, Thread J: top-level descriptor fields allowed to
# differ between two publications that share a `content_digest` without
# that being treated as anything worse than "provenance drift". Every other
# top-level field — `schema_version`/`content_digest`/`content_digest_basis`
# are already pinned exactly by earlier checks; `arrangement_id` / `changes`
# (arrange) and `work_id` / `generator` / `generator_variant` / `package_sha256`
# (package) are not part of `content_digest` at all, so without this whitelist a tampered
# `changes` list (for example) could otherwise be waved through as mere
# provenance. Paired with `_arrange_bundle_digest_inputs` /
# `_package_report_digest_inputs` the same way those pair with
# `descriptive_filename` — one arrange-shaped constant, one package-shaped
# constant.
#
# Round 8 partial adoption: `warnings` was removed from the package
# whitelist (kept in round 7). Every warning `build_performance_package`
# ever emits is a pure function of facts already baked into
# `performance_package.json` bytes — channel support / anchor delivery
# status — so an identical `package_sha256` (already pinned by
# `content_digest`, since content_digest = sha256(package_sha256) for
# package) implies identical warnings; device-profile advisories are a
# separate stderr-only channel that never lands in `warnings` (#128), so
# they can't explain a difference here either. A `warnings` difference at
# matching `package_sha256` therefore isn't legitimate invocation drift —
# it's a tamper signal, so it stays outside the whitelist.
#
# `inputs` is deliberately *kept* in the whitelist (the other half of the
# same round-8 review comment was rejected): `inputs` records input-file
# byte hashes, which is invocation provenance in the same sense
# `invocation_provenance.compiler.git_commit` already is — first-publish-wins
# already accepts that the *first* successful invocation's environment is
# what gets recorded permanently. Requiring `inputs` to match exactly would
# reject legitimate re-runs where a parse-equivalent but differently
# formatted input (whitespace, key order) produces the same package but a
# different input-file hash — a false positive, not a caught tamper.
_PACKAGE_REPORT_ALLOWED_PROVENANCE_FIELDS = frozenset({"inputs", "invocation_provenance", "mode"})


@app.command("package")
def package_command(
    score_yaml: str = typer.Argument(..., help="Path to base Composition Score YAML"),
    identity_yaml: str = typer.Argument(..., help="Path to IdentityManifest YAML"),
    arrangement_yaml: str = typer.Argument(..., help="Path to ArrangementSpec YAML"),
    capability_profile: str = typer.Option(
        ...,
        "--capability-profile",
        help="Path to InputCapabilityProfile YAML",
    ),
    output_dir: Optional[str] = typer.Option(
        None, "--output-dir", help="Output directory (mutually exclusive with --builds-root)"
    ),
    builds_root: Optional[str] = typer.Option(
        None,
        "--builds-root",
        help=(
            "Publish under <root>/builds/<content_digest>/ (immutable once "
            "published; <root>/latest.json points at the most recent digest). "
            "Mutually exclusive with --output-dir."
        ),
    ),
    strict_capabilities: bool = typer.Option(
        False,
        "--strict-capabilities",
        help="Fail when a hard anchor is unsupported or unknown",
    ),
) -> None:
    """Compile a generator handoff package and capability report."""
    import yaml
    from pydantic import ValidationError

    from svp_rpe.arrange import (
        ArrangementError,
        COMPILATION_REPORT_FILENAME,
        PERFORMANCE_PACKAGE_FILENAME,
        compile_performance_package,
    )
    from svp_rpe.arrange.package import COMPILATION_REPORT_SCHEMA_VERSION
    from svp_rpe.cli import builds_root as builds_root_module

    if (output_dir is None) == (builds_root is None):
        raise typer.BadParameter(
            "exactly one of --output-dir or --builds-root is required",
            param_hint="--output-dir / --builds-root",
        )

    # `artifact_base.locator` (embedded in the package, part of what
    # determines content_digest) is relative to the eventual package
    # directory. In builds mode that directory's name is the content_digest
    # itself, which is not known yet — a same-depth placeholder yields an
    # identical relative path (see `_builds_placeholder_package_dir`).
    if builds_root is not None:
        placeholder_dir = builds_root_module._builds_placeholder_package_dir(builds_root)
        # PR #184 review round 7, Thread K: a *symlinked* placeholder would
        # make `package_dir.resolve()` follow the symlink to wherever it
        # actually points, so the locator gets computed relative to that
        # (arbitrary, mutable) target instead of the reserved same-depth
        # placeholder path — the locator would then be wrong once the real
        # digest directory is published. A real directory sitting at the
        # placeholder path is fine (same depth, so the locator is still
        # correct); only a symlink is rejected here.
        if placeholder_dir.is_symlink():
            typer.echo(
                f"Error: builds-root locator placeholder is a symlink: {placeholder_dir}",
                err=True,
            )
            raise typer.Exit(code=1)
        locator_dir = str(placeholder_dir)
    else:
        locator_dir = output_dir

    try:
        compiled = compile_performance_package(
            score_yaml,
            identity_yaml,
            arrangement_yaml,
            capability_profile,
            locator_dir,
            strict=strict_capabilities,
        )
    except (
        OSError,
        yaml.YAMLError,
        ValueError,
        ValidationError,
        ArrangementError,
    ) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    contents = {
        PERFORMANCE_PACKAGE_FILENAME: compiled.package_json,
        COMPILATION_REPORT_FILENAME: compiled.report_json,
    }

    if builds_root is not None:
        content_digest = compiled.report.content_digest
        try:
            builds_root_module._reject_builds_root_input_collision(
                list(compiled.protected_input_paths),
                builds_root,
                reject_placeholder_subtree=True,
            )
            published_dir, already_existed, provenance_differs = (
                builds_root_module._publish_artifacts_to_builds_root(
                    contents,
                    builds_root,
                    content_digest,
                    descriptive_filename=COMPILATION_REPORT_FILENAME,
                    expected_schema_version=COMPILATION_REPORT_SCHEMA_VERSION,
                    extract_digest_inputs=_package_report_digest_inputs,
                    allowed_provenance_fields=_PACKAGE_REPORT_ALLOWED_PROVENANCE_FIELDS,
                    validate_descriptor=_package_report_validate_descriptor,
                )
            )
        except (OSError, ValueError) as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        builds_root_module._print_builds_root_publish_note(
            content_digest, already_existed=already_existed, provenance_differs=provenance_differs
        )
        console.print(
            "[green]Performance package saved to "
            f"{published_dir / PERFORMANCE_PACKAGE_FILENAME}[/green]"
        )
        console.print(
            "[green]Compilation report saved to "
            f"{published_dir / COMPILATION_REPORT_FILENAME}[/green]"
        )
        return

    assert output_dir is not None  # mutual-exclusion check above guarantees this
    try:
        out_dir = builds_root_module._publish_artifacts_atomically(
            contents,
            output_dir,
            list(compiled.protected_input_paths),
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    console.print(
        f"[green]Performance package saved to "
        f"{out_dir / PERFORMANCE_PACKAGE_FILENAME}[/green]"
    )
    console.print(
        f"[green]Compilation report saved to "
        f"{out_dir / COMPILATION_REPORT_FILENAME}[/green]"
    )
