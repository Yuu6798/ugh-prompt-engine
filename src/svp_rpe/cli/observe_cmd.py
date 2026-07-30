"""svprpe observe."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import typer
from rich.table import Table

from svp_rpe.cli._app import app, console

if TYPE_CHECKING:
    from svp_rpe.arrange.identity import IdentityManifest


def _observe_protected_input_paths(
    package_path: Path,
    manifest_path: Path,
    audio_path: Path,
    manifest: "IdentityManifest",
) -> list[Path]:
    """Every path `observe` reads as an input — package / manifest / audio /
    manifest source locator / every anchor artifact — resolved so `-o` can be
    checked against all of them before anything is measured or written
    (PR #187 review round 1: the same collision shape
    `_publish_artifacts_atomically` / `_reject_builds_root_input_collision`
    already guard against for `arrange` / `package`, applied here to
    `observe`'s single `-o` output file)."""
    from svp_rpe.arrange.identity import _resolve_confined

    base_dir = manifest_path.resolve().parent
    work_id = manifest.meta.work_id
    paths = [package_path.resolve(), manifest_path.resolve(), audio_path.resolve()]
    paths.append(
        _resolve_confined(manifest.source.locator, base_dir, work_id=work_id, target="source")
    )
    for anchor in manifest.anchors:
        paths.append(
            _resolve_confined(
                anchor.artifact, base_dir, work_id=work_id, target=f"anchor '{anchor.id}'"
            )
        )
    return paths


def _reject_observe_output_collision(output_path: str | Path, input_paths: list[Path]) -> None:
    """Reject `-o` if it aliases any input path — by resolved-path equality,
    and (PR #187 review round 3) by same-file identity (`os.path.samefile`,
    i.e. same `st_dev`/`st_ino`) when the output path already exists on disk.
    The resolved-path check alone misses a hard link: two distinct paths that
    are actually the same inode, neither of which is a symlink the other
    resolves through, so `Path.resolve()` leaves them unequal even though
    writing through one clobbers the other. `samefile` requires both paths to
    exist; if it can't be determined (e.g. an input vanished between
    resolution and this check, or some other OSError), that pair is skipped
    and only the plain path-equality result stands — a determination failure
    here must never crash the CLI, just fall back to the weaker check.
    """
    import os

    resolved_output = Path(output_path).resolve()
    output_exists = resolved_output.exists()
    for input_path in input_paths:
        if input_path == resolved_output:
            raise ValueError(f"input path collides with output artifact path: {resolved_output}")
        if output_exists:
            try:
                same_file = os.path.samefile(resolved_output, input_path)
            except OSError:
                continue
            if same_file:
                raise ValueError(
                    f"input path collides with output artifact path: {resolved_output} "
                    f"(same file as {input_path})"
                )


def _write_observation_report_atomically(output_path: Path, content: str) -> None:
    """Publish `output_path` via staging file + `os.replace` (PR #187 review
    round 6), the same minimal pattern `_update_builds_latest_pointer` uses
    for `latest.json`: write to a tempfile in the same directory, then
    atomically rename it onto the target. A partially-written report must
    never be observable as a complete one, whether the process is
    interrupted mid-write or an existing report is being overwritten (D-1's
    own instrument still needs its own output to be trustworthy). Any
    failure cleans up the staging file on a best-effort basis (its own
    unlink failure is swallowed) before re-raising.

    Thin wrapper — the implementation is consolidated in
    `svp_rpe.utils.atomic_io.atomic_write_text`.
    """
    from svp_rpe.utils.atomic_io import atomic_write_text

    atomic_write_text(output_path, content)


@app.command("observe")
def observe_cmd(
    package_json: str = typer.Argument(..., help="Path to performance_package.json"),
    audio: str = typer.Argument(..., help="Path to the generated audio (WAV/MP3)"),
    manifest: str = typer.Option(
        ..., "--manifest", help="Path to the IdentityManifest YAML used to compile the package"
    ),
    output: str = typer.Option(
        ..., "-o", "--output", help="Output observation report JSON path"
    ),
) -> None:
    """Record post-generation anchor observations against a generated artifact (AR4).

    Instrument, not verdict (Design Memo D-1): `adherence_status` is only ever
    set when it can be decided without a threshold — `not_observed` when no
    sensor is wired for an anchor's domain, `preserved` on an exact identity
    match, and `not_observed` (with `determination: "deferred"`) plus the raw
    measurements when the sensor ran but did not match exactly. Threshold-based
    `changed_within_policy` / `changed_outside_policy` classification is out of
    scope until a future Design Memo fixes it.

    Before measuring, verifies the provenance chain (D-3): the `--manifest`
    file's sha256 must equal `package.inputs.identity_manifest.sha256`, and
    every manifest anchor's artifact hash must match the file on disk (via the
    same loader `package` uses). A broken chain or malformed package exits 1
    without measuring anything — this instrument refuses to run against
    inputs it cannot trust. `-o` is also rejected (exit 1, nothing written) if
    it resolves to any input path (package / manifest / audio / manifest
    source locator / any anchor artifact) — the same input/output collision
    guard `arrange` / `package` apply to their own outputs.

    Scope boundary (PR #187 review round 16, P2 rejected by design): the
    cross-object checks here stop at *identifying* the observation target —
    manifest sha256, `work_id`, and the `anchor_statuses` id set, i.e. "does
    this package describe the same work and anchor set this manifest does".
    Internal consistency of `package`'s own fields (e.g. `channel_artifacts`
    entries' `artifact` / `artifact_sha256` matching the manifest anchors they
    reference) is `package`'s own schema-validation responsibility, not
    `observe`'s — the `svprpe verify` command is the right home for exhaustive
    package-internal consistency checking, so that job doesn't creep into this
    instrument's provenance-identification role.
    """
    import hashlib
    import os
    import tempfile

    import yaml
    from pydantic import ValidationError

    from svp_rpe.arrange.identity import (
        IdentityManifestError,
        parse_identity_manifest_with_artifacts,
    )
    from svp_rpe.arrange.observe import (
        build_observation_report,
        is_harmony_sensor_anchor,
        is_lyrics_sensor_anchor,
        is_melody_sensor_anchor,
        is_structure_sensor_anchor,
    )
    from svp_rpe.arrange.package import PerformancePackage

    package_path = Path(package_json)
    manifest_path = Path(manifest)
    audio_path = Path(audio)

    try:
        package_bytes = package_path.read_bytes()
    except OSError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    try:
        package = PerformancePackage.model_validate_json(package_bytes)
    except ValidationError as exc:
        typer.echo(f"Error: performance package failed schema validation: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if manifest_sha256 != package.inputs.identity_manifest.sha256:
        typer.echo(
            "Error: identity manifest sha256 does not match "
            f"package.inputs.identity_manifest.sha256: {manifest_sha256} != "
            f"{package.inputs.identity_manifest.sha256}",
            err=True,
        )
        raise typer.Exit(code=1)

    # Parse from the bytes already read and hashed above — `manifest_path` is
    # only used to resolve anchor/source locators and for error messages, the
    # manifest file itself is never read a second time (PR #187 review round 1).
    # `artifact_bytes_by_id` carries each anchor artifact's already-verified
    # bytes (same read the hash check performed) so the harmony sensor doesn't
    # reopen the file either (PR #187 review round 2). The exception set below
    # matches `package`'s own manifest-parsing catch (PR #187 review round 6):
    # a manifest can hash-match the package's recorded sha256 and still be
    # malformed YAML, non-mapping, or schema-invalid content.
    try:
        # Collect only the anchors a wired sensor actually reads content for
        # (PR #187 review round 11; widened 2026-07-19 for structure, and
        # 2026-07-20/WI0-a for lyrics/melody) — every anchor's bytes are
        # still read once and hash-verified regardless, but bytes for
        # anchors outside this predicate (e.g. a large `audio_excerpt`
        # reference) are discarded immediately rather than held in
        # `artifact_bytes_by_id` for the whole observe run. Update all four
        # predicates here (and in observe.py, shared with `_observe_anchor`'s
        # routing) if a future sensor needs another anchor's bytes.
        loaded_manifest, artifact_bytes_by_id = parse_identity_manifest_with_artifacts(
            manifest_bytes,
            manifest_path,
            collect=lambda anchor: (
                is_harmony_sensor_anchor(anchor)
                or is_structure_sensor_anchor(anchor)
                or is_lyrics_sensor_anchor(anchor)
                or is_melody_sensor_anchor(anchor)
            ),
        )
    except (IdentityManifestError, ValueError, ValidationError, yaml.YAMLError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    # PR #187 review round 10: the sha256 chain check above only proves the
    # manifest *bytes* the package recorded and the `--manifest` file agree —
    # it says nothing about whether they describe the *same work*. Two
    # different manifests could (accidentally or not) hash-match a stale
    # `package.inputs.identity_manifest.sha256` from an unrelated build only
    # if the bytes are literally identical, in which case work_id already
    # matches too — but defense in depth belongs at the provenance-chain
    # layer, not assumed from the hash check alone.
    if package.work_id != loaded_manifest.meta.work_id:
        typer.echo(
            "Error: package.work_id does not match manifest.meta.work_id: "
            f"{package.work_id!r} != {loaded_manifest.meta.work_id!r}",
            err=True,
        )
        raise typer.Exit(code=1)

    # PR #187 review round 15: sha256 and work_id matching still don't rule
    # out a package whose `anchor_statuses` set of anchor ids was tampered to
    # no longer match `loaded_manifest.anchors` (missing an anchor the
    # manifest declares, or carrying an extra one it doesn't) — a downstream
    # anchor_id join between this observation report and the package would
    # silently describe a different anchor set than the package's own
    # provenance fields claim. Order-independent set comparison; either
    # direction of mismatch is reported.
    package_anchor_ids = {status.anchor_id for status in package.anchor_statuses}
    manifest_anchor_ids = {anchor.id for anchor in loaded_manifest.anchors}
    if package_anchor_ids != manifest_anchor_ids:
        missing_from_package = sorted(manifest_anchor_ids - package_anchor_ids)
        extra_in_package = sorted(package_anchor_ids - manifest_anchor_ids)
        details = []
        if missing_from_package:
            details.append(f"missing from package.anchor_statuses: {missing_from_package}")
        if extra_in_package:
            details.append(f"extra in package.anchor_statuses: {extra_in_package}")
        typer.echo(
            "Error: package.anchor_statuses anchor_id set does not match "
            "manifest.anchors id set (" + "; ".join(details) + ")",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        protected_paths = _observe_protected_input_paths(
            package_path, manifest_path, audio_path, loaded_manifest
        )
        _reject_observe_output_collision(output, protected_paths)
    except (IdentityManifestError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        audio_bytes = audio_path.read_bytes()
    except OSError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    audio_sha256 = hashlib.sha256(audio_bytes).hexdigest()
    package_sha256 = hashlib.sha256(package_bytes).hexdigest()

    # PR #187 review round 13: only build the snapshot tempfile when audio
    # content will actually be read — the same predicate set
    # `build_observation_report`/`_observe_anchor` route on (round 12;
    # widened 2026-07-19 for structure, 2026-07-20/WI0-a for lyrics/melody),
    # so routing / extraction-gate / snapshot-gate all agree on a single
    # source of truth (no anchor matching any predicate means nothing ever
    # reads audio content, so the TOCTOU concern the snapshot exists for
    # (round 1) doesn't arise — there's no "bytes actually measured" to keep
    # in sync with the hashed bytes). Lyrics/melody read `audio_path`
    # directly (not through `extract_rpe_from_file`/`bundle`) but the same
    # TOCTOU concern applies to their file reads too, so they widen this
    # gate exactly like structure did for the shared-bundle path.
    needs_extraction = any(
        is_harmony_sensor_anchor(anchor)
        or is_structure_sensor_anchor(anchor)
        or is_lyrics_sensor_anchor(anchor)
        or is_melody_sensor_anchor(anchor)
        for anchor in loaded_manifest.anchors
    )
    snapshot_path: Optional[Path] = None
    try:
        if needs_extraction:
            # Extract from a snapshot of exactly the bytes just hashed, not
            # from `audio_path` directly — this makes "the sha256 recorded in
            # the report" and "the bytes actually measured" the same bytes by
            # construction, not merely by the assumption that nothing touches
            # `audio_path` in between (PR #187 review round 1).
            # `generated_artifact.path` in the report still reports the
            # user-supplied `audio` string, not this temp path.
            #
            # PR #187 review round 17: `mkstemp` / the snapshot write can
            # raise `OSError` (temp disk full, unwritable, etc.) — wrapped in
            # the same "Error: ..." + exit 1 handling every other failure
            # path in this command uses, instead of escaping as a raw
            # traceback. `snapshot_path` is set immediately after `mkstemp`
            # succeeds (before the write), so the outer `finally` still
            # cleans up a partially-written temp file on a write failure.
            try:
                snapshot_fd, snapshot_name = tempfile.mkstemp(
                    suffix=audio_path.suffix or ".wav"
                )
                snapshot_path = Path(snapshot_name)
                with os.fdopen(snapshot_fd, "wb") as snapshot_file:
                    snapshot_file.write(audio_bytes)
            except OSError as exc:
                typer.echo(f"Error: {exc}", err=True)
                raise typer.Exit(code=1) from exc
            measurement_audio_path = snapshot_path
        else:
            measurement_audio_path = audio_path

        try:
            report = build_observation_report(
                package=package,
                manifest=loaded_manifest,
                manifest_path=manifest_path,
                artifact_bytes=artifact_bytes_by_id,
                audio_path=measurement_audio_path,
                package_sha256=package_sha256,
                audio_sha256=audio_sha256,
                generated_artifact_path=audio,
            )
        except (OSError, ValueError, ValidationError, IdentityManifestError) as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    finally:
        if snapshot_path is not None:
            snapshot_path.unlink(missing_ok=True)

    content = json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)
    try:
        _write_observation_report_atomically(Path(output), content)
    except OSError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    table = Table(title=f"Observation: {package.work_id}")
    table.add_column("anchor_id")
    table.add_column("domain")
    table.add_column("sensor")
    table.add_column("available")
    table.add_column("adherence_status")
    table.add_column("determination")
    table.add_column("measurements")
    for anchor_observation in report.anchors:
        measurements_summary = ", ".join(
            f"{key}={value}" for key, value in anchor_observation.measurements.items()
        )
        table.add_row(
            anchor_observation.anchor_id,
            anchor_observation.domain,
            anchor_observation.sensor.name,
            "yes" if anchor_observation.sensor.available else "no",
            anchor_observation.adherence_status,
            anchor_observation.determination,
            measurements_summary,
        )
    console.print(table)
    console.print(f"[green]Observation report saved to {output}[/green]")
