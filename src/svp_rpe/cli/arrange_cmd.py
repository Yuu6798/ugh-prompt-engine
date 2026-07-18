"""svprpe arrange."""
from __future__ import annotations

import json
from typing import Any, Optional

import typer

from svp_rpe.cli._app import app, console


def _arrange_bundle_digest_inputs(descriptor: dict[str, Any]) -> dict[str, str]:
    """`{artifact_name: sha256}` from a parsed `arrangement_bundle.json`.

    Mirrors `compile_arrangement`'s own `compute_content_digest` call: the
    digest is computed over `outputs`' `{path, sha256}` entries (keyed by
    filename), never over `source_score`/`arrangement_spec` (D-1: bundle
    provenance is descriptive, not digest-bearing).
    """
    outputs = descriptor["outputs"]
    return {entry["path"]: entry["sha256"] for entry in outputs.values()}


def _arrange_bundle_validate_descriptor(descriptor: dict[str, Any]) -> None:
    """Full schema validation of a parsed `arrangement_bundle.json` (round 9).

    `ArrangementBundleDescriptor` (`arrange/bundle.py`) is a **reader-only**
    model — `compile_arrangement` never uses it and keeps building the bundle
    as a plain dict, so publish-side bytes are unaffected. It exists purely
    so this blessing-path validation can check the whole descriptor's shape
    (nested `{path, sha256}` entries, `changes` records, unknown top-level
    keys via `extra="forbid"`) instead of just the individual fields
    `_check_existing_builds_root_publication` otherwise inspects one at a
    time.
    """
    from pydantic import ValidationError

    from svp_rpe.arrange.bundle import ArrangementBundleDescriptor

    try:
        ArrangementBundleDescriptor.model_validate(descriptor)
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
_ARRANGE_BUNDLE_ALLOWED_PROVENANCE_FIELDS = frozenset({"source_score", "arrangement_spec"})


@app.command()
def arrange(
    score_yaml: str = typer.Argument(..., help="Path to base Composition Score YAML"),
    arrangement_yaml: str = typer.Argument(..., help="Path to ArrangementSpec YAML"),
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
) -> None:
    """Resolve an ArrangementSpec against a base Composition Score into provenance artifacts."""
    import yaml
    from pydantic import ValidationError

    from svp_rpe.arrange import (
        ArrangementError,
        BUNDLE_FILENAME,
        DERIVED_SCORE_FILENAME,
        DIFF_FILENAME,
        compile_arrangement,
    )
    from svp_rpe.arrange.bundle import BUNDLE_SCHEMA_VERSION
    from svp_rpe.cli import builds_root as builds_root_module

    if (output_dir is None) == (builds_root is None):
        raise typer.BadParameter(
            "exactly one of --output-dir or --builds-root is required",
            param_hint="--output-dir / --builds-root",
        )

    try:
        compiled = compile_arrangement(score_yaml, arrangement_yaml)
    except (
        # OSError は FileNotFoundError に加え IsADirectoryError / PermissionError 等の
        # 入力読み取り失敗を包含する（P2 第 4 ラウンド: 生 traceback でなく exit 1）。
        OSError,
        yaml.YAMLError,
        ValueError,
        ValidationError,
        ArrangementError,
    ) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    contents = {
        DERIVED_SCORE_FILENAME: compiled.derived_score_yaml,
        BUNDLE_FILENAME: json.dumps(compiled.bundle, ensure_ascii=False, indent=2),
        DIFF_FILENAME: json.dumps(compiled.diff, ensure_ascii=False, indent=2),
    }

    if builds_root is not None:
        content_digest = compiled.bundle["content_digest"]
        try:
            builds_root_module._reject_builds_root_input_collision(
                [score_yaml, arrangement_yaml], builds_root
            )
            published_dir, already_existed, provenance_differs = (
                builds_root_module._publish_artifacts_to_builds_root(
                    contents,
                    builds_root,
                    content_digest,
                    descriptive_filename=BUNDLE_FILENAME,
                    expected_schema_version=BUNDLE_SCHEMA_VERSION,
                    extract_digest_inputs=_arrange_bundle_digest_inputs,
                    allowed_provenance_fields=_ARRANGE_BUNDLE_ALLOWED_PROVENANCE_FIELDS,
                    validate_descriptor=_arrange_bundle_validate_descriptor,
                )
            )
        except (OSError, ValueError) as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        builds_root_module._print_builds_root_publish_note(
            content_digest, already_existed=already_existed, provenance_differs=provenance_differs
        )
        console.print(
            f"[green]Derived score saved to {published_dir / DERIVED_SCORE_FILENAME}[/green]"
        )
        console.print(
            f"[green]Arrangement bundle saved to {published_dir / BUNDLE_FILENAME}[/green]"
        )
        console.print(
            f"[green]Arrangement diff saved to {published_dir / DIFF_FILENAME}[/green]"
        )
        return

    assert output_dir is not None  # mutual-exclusion check above guarantees this
    try:
        out_dir = builds_root_module._publish_artifacts_atomically(
            contents, output_dir, [score_yaml, arrangement_yaml]
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    console.print(f"[green]Derived score saved to {out_dir / DERIVED_SCORE_FILENAME}[/green]")
    console.print(f"[green]Arrangement bundle saved to {out_dir / BUNDLE_FILENAME}[/green]")
    console.print(f"[green]Arrangement diff saved to {out_dir / DIFF_FILENAME}[/green]")
