"""builds-root publication helpers shared by `arrange` and `package` (no commands here)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import typer


def _publish_artifacts_atomically(
    contents: dict[str, str],
    output_dir: str | Path,
    input_paths: list[str | Path],
) -> Path:
    """Publish a complete artifact set with rollback on an interrupted rename."""
    import os
    import tempfile

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    resolved_inputs = {Path(path).resolve() for path in input_paths}
    for filename in contents:
        target = out_dir / filename
        if target.resolve() in resolved_inputs:
            raise ValueError(f"input path collides with output artifact path: {target}")
        if target.is_dir():
            raise ValueError(f"output path is an existing directory: {target}")

    with tempfile.TemporaryDirectory(dir=out_dir) as staging:
        staging_dir = Path(staging)
        for filename, content in contents.items():
            (staging_dir / filename).write_bytes(content.encode("utf-8"))

        snapshots: dict[str, Path] = {}
        published: list[str] = []
        try:
            for filename in contents:
                target = out_dir / filename
                if target.exists():
                    previous = staging_dir / f"{filename}.prev"
                    os.replace(target, previous)
                    snapshots[filename] = previous
            for filename in contents:
                os.replace(staging_dir / filename, out_dir / filename)
                published.append(filename)
        except OSError:
            for filename in published:
                try:
                    os.unlink(out_dir / filename)
                except OSError:
                    pass
            for filename, previous in snapshots.items():
                try:
                    os.replace(previous, out_dir / filename)
                except OSError:
                    pass
            raise
    return out_dir


BUILDS_LATEST_SCHEMA_VERSION = "builds-latest/0.1"
# `os.path.relpath` depends only on path *depth*, never on the literal name
# of the final component, so any 64-hex-char stand-in produces the exact
# same relative locator as the real `content_digest` would (every real
# digest directory sits at the same depth under `<builds_root>/builds/`).
# This lets `package` compute `artifact_base.locator` — itself part of the
# package content that determines `content_digest` — *before* the digest is
# known, without the two ever needing to agree on a real value.
_BUILDS_LOCATOR_PLACEHOLDER_DIGEST = "0" * 64


def _builds_placeholder_package_dir(builds_root: str | Path) -> Path:
    """Same-depth stand-in for `<builds_root>/builds/<content_digest>/`."""
    return Path(builds_root) / "builds" / _BUILDS_LOCATOR_PLACEHOLDER_DIGEST


def _update_builds_latest_pointer(latest_path: Path, content_digest: str, *, root: Path) -> None:
    """Atomically (over)write `<root>/latest.json` to point at `content_digest`.

    `latest.json` is the one file this scheme ever overwrites — everything
    under `<root>/builds/<digest>/` is immutable once published (Design
    Memo §4).
    """
    import os
    import tempfile

    payload = json.dumps(
        {"schema_version": BUILDS_LATEST_SCHEMA_VERSION, "content_digest": content_digest},
        ensure_ascii=False,
        indent=2,
    )
    root.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=root, prefix="latest.json.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_name, latest_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _reject_builds_root_input_collision(
    input_paths: list[str | Path],
    builds_root: str | Path,
    *,
    reject_placeholder_subtree: bool = False,
) -> None:
    """Guard against an input path aliasing `<builds_root>/latest.json`, or
    (package only) sitting inside the reserved locator-placeholder subtree.

    `latest.json` is the **only** file the builds-root scheme ever
    overwrites; if an input (score / spec / manifest / capability profile /
    etc.) resolves to that same path, publishing would silently clobber the
    input on the `latest.json` update — the same shape of hole
    `_publish_artifacts_atomically` already guards against for `--output-dir`
    (#176 P2 R3). No equivalent check is needed against a real
    `<builds_root>/builds/<content_digest>/` directory: unpublished, it
    doesn't exist yet; once published, it is never written to again — so it
    is structurally safe either way.

    `reject_placeholder_subtree=True` (package only; PR #184 review round 5,
    Thread G) additionally rejects any input that resolves inside
    `<builds_root>/builds/<64 zeros>/` — the reserved, same-depth stand-in
    `_builds_placeholder_package_dir` uses to compute `artifact_base.locator`
    *before* the real `content_digest` is known (see that function's
    docstring). If an input — most concretely, an identity manifest anchor
    artifact — actually lives inside that reserved subtree, the locator
    computed against the placeholder does not describe where the artifact
    ends up relative to the real digest directory once published, silently
    breaking `artifact_base`. A *real* digest directory (any other 64-hex
    name) is not reserved and is not rejected: an input placed there resolves
    correctly regardless, because `os.path.relpath` only depends on depth and
    every digest directory sits at the same depth under `builds/` (the same
    property that makes the placeholder trick valid in the first place).
    """
    latest_path = Path(builds_root) / "latest.json"
    resolved_latest = latest_path.resolve()
    placeholder_dir = (
        _builds_placeholder_package_dir(builds_root).resolve()
        if reject_placeholder_subtree
        else None
    )
    for input_path in input_paths:
        resolved_input = Path(input_path).resolve()
        if resolved_input == resolved_latest:
            raise ValueError(
                f"input path collides with output artifact path: {latest_path}"
            )
        if placeholder_dir is not None and resolved_input.is_relative_to(placeholder_dir):
            raise ValueError(
                f"input path collides with output artifact path: {resolved_input} "
                f"lies inside the builds-root locator-placeholder subtree "
                f"{placeholder_dir} reserved for artifact_base.locator computation"
            )


def _reject_if_not_regular_file(path: Path, *, label: str) -> None:
    """Reject a symlink, or any existing non-regular-file entity, before it
    is read (PR #184 review round 7, Thread I).

    A blessed, immutable publication must not depend on a mutable entity
    outside `builds_root` — the same principle as the round 3/6 digest- and
    `builds/`-directory symlink rejections, applied one level down to every
    file `_check_existing_builds_root_publication` reads (the descriptor
    itself and each declared content artifact). A symlink is rejected
    regardless of what it resolves to (even a symlink to an otherwise
    byte-identical file) — resolving it at all would mean trusting whoever
    controls that link.
    """
    if path.is_symlink():
        raise ValueError(f"builds-root {label} is a symlink: {path}")
    if path.exists() and not path.is_file():
        raise ValueError(f"builds-root {label} is not a regular file: {path}")


def _check_existing_builds_root_publication(
    target_dir: Path,
    descriptive_filename: str,
    pending_content: str,
    *,
    content_digest: str,
    expected_schema_version: str,
    extract_digest_inputs: Callable[[dict[str, Any]], dict[str, str]],
    allowed_provenance_fields: frozenset[str],
    validate_descriptor: Callable[[dict[str, Any]], None],
) -> bool:
    """Read-and-verify check for the immutable no-op publish path, run before
    `latest.json` is ever moved onto an existing digest directory.

    Reads the existing digest directory's one *descriptive* file
    (`arrangement_bundle.json` for `arrange`, `compilation_report.json` for
    `package` — the file that describes the whole publication, as opposed to
    plain content outputs) exactly once — after `_reject_if_not_regular_file`
    confirms it is neither a symlink nor some other non-regular entity. If
    its bytes match what this invocation would have published for that same
    filename, no parsing, no digest recomputation, no schema check is needed
    for the *descriptor itself* — an identical-bytes descriptor was
    necessarily produced by *this* schema version by construction. (PR #184
    review round 5, Thread F: that fast path alone is not enough to bless the
    directory — see below.)

    If the descriptor's bytes differ, this is *not* immediately treated as
    mere provenance/metadata drift (round 2, Thread B) — a directory merely
    occupying this digest path (corrupted, hand-edited, or from an unrelated
    build) must not be blessed by a `latest.json` update just because a byte
    difference was, in isolation, plausible-looking provenance drift. Before
    accepting it as provenance drift, several things are verified, each
    raising `ValueError` (and leaving `latest.json` untouched) on failure:

    1. the descriptive file parses as JSON;
    2. its `schema_version` field equals `expected_schema_version` (AGENTS.md
       §8 Persistent Artifact Safety Gate: unknown `schema_version` values
       are rejected, not read as if they were the current schema — a
       descriptor written by an older/newer schema must not be silently
       treated as an ordinary provenance difference in the *current* one).
       This explicit check runs *before* step 3 so an unknown-schema
       descriptor gets this specific, targeted error rather than whatever
       incidental validation error the current schema's model happens to
       raise against it;
    3. (round 9) the whole descriptor validates against `validate_descriptor`
       — `ArrangementBundleDescriptor.model_validate` for `arrange`,
       `CompilationReport.model_validate` for `package` (both translate
       `pydantic.ValidationError` to `ValueError` internally). This is a full
       schema validation, not just the top-level fields step 6 below
       compares: every nested shape (e.g. `outputs`' `{path, sha256}` entries,
       `changes`' leaf-change records) is checked, `extra="forbid"` rejects
       unknown keys, and it subsumes step 2's schema_version check in the
       sense that an unexpected schema_version would fail model validation
       too — step 2 is kept anyway for the more specific error message;
    4. its own `content_digest` field equals the `content_digest` this
       invocation computed (the digest is the directory's name, so this
       catches a descriptor that doesn't even claim to describe *this*
       digest);
    5. recomputing `content_digest` from the descriptor's own declared
       output hashes (via `extract_digest_inputs`, which knows the
       arrange-vs-package shape of "artifact_name -> sha256") reproduces the
       same digest — this catches a descriptor whose declared hashes were
       tampered with (or corrupted) to match step 4's digest field without
       actually being self-consistent;
    6. (round 7, Thread J, checked after every content artifact below is
       also confirmed present and hash-matching) every top-level field of
       the existing descriptor that is *not* in `allowed_provenance_fields`
       is byte-for-byte equal (as parsed JSON) to the same field in the
       pending descriptor — e.g. for `arrange`'s bundle, only
       `source_score`/`arrangement_spec` may differ; `arrangement_id` and
       `changes` are *not* part of `content_digest`, so without this check a
       tampered `changes` list could otherwise be waved through as "mere
       provenance drift" merely because `content_digest` still matched.

    Either way — byte-identical fast path or a byte-difference that survives
    checks 1-5 — one more thing is verified **before** `latest.json` may be
    moved onto this directory: every content artifact the descriptor
    declares (`extract_digest_inputs`' `{artifact_name: sha256}`, e.g.
    `derived_score.yaml` + `arrangement_diff.json` for `arrange`) is neither
    a symlink nor a non-regular file (`_reject_if_not_regular_file` again),
    actually exists in `target_dir`, and hashes to its declared value. A
    byte-identical (or otherwise self-consistent) *descriptor* copied into a
    directory that is missing, has tampered, or has symlinked-out content
    files would otherwise slip through undetected — the descriptor alone
    doesn't prove the directory it sits in is complete. On the fast path,
    the declared output map is read from `pending_content` (already known to
    be byte-identical to the on-disk descriptor, so parsing either is
    equivalent) rather than re-reading the descriptor from disk a second
    time — and, being byte-identical to a descriptor this invocation itself
    just built via its own model, it needs no separate schema validation.

    Blessing a digest directory with a `latest.json` update therefore always
    means: descriptor self-consistency (schema + digest + provenance-only
    diff, checks 1-6), every declared output artifact present with a
    matching hash, and nothing read along the way was a symlink. This is
    still not a full audit — it never rehashes *undeclared* files or
    recurses into anything beyond what the descriptor itself lists, and
    nothing in the directory is ever rewritten or repaired; an independent,
    fully recursive audit is left to a future `verify`-style command.
    """
    descriptive_path = target_dir / descriptive_filename
    _reject_if_not_regular_file(
        descriptive_path,
        label=f"digest directory {target_dir} descriptive file {descriptive_filename!r}",
    )
    try:
        existing_bytes = descriptive_path.read_bytes()
    except OSError as exc:
        raise ValueError(
            f"builds-root digest directory {target_dir} is missing or unreadable "
            f"descriptive file {descriptive_filename!r}: {exc}"
        ) from exc

    pending_bytes = pending_content.encode("utf-8")
    provenance_differs = existing_bytes != pending_bytes

    if not provenance_differs:
        # Fast path: the descriptor on disk is byte-identical to the one
        # this invocation would publish, so parsing the pending copy (which
        # this process already holds in memory) is equivalent to re-reading
        # and re-parsing the one on disk.
        digest_inputs = extract_digest_inputs(json.loads(pending_bytes))
    else:
        try:
            existing_descriptor = json.loads(existing_bytes)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"builds-root digest directory {target_dir} has an unparsable "
                f"descriptive file {descriptive_filename!r}: {exc}"
            ) from exc
        if not isinstance(existing_descriptor, dict):
            raise ValueError(
                f"builds-root digest directory {target_dir} descriptive file "
                f"{descriptive_filename!r} must be a JSON object"
            )

        recorded_schema_version = existing_descriptor.get("schema_version")
        if recorded_schema_version != expected_schema_version:
            raise ValueError(
                f"builds-root digest directory {target_dir} descriptive file "
                f"{descriptive_filename!r} declares schema_version "
                f"{recorded_schema_version!r}, expected {expected_schema_version!r}"
            )

        # `validate_descriptor` (arrange: `ArrangementBundleDescriptor.model_validate`,
        # package: `CompilationReport.model_validate`) is expected to translate
        # `pydantic.ValidationError` into `ValueError` itself — see
        # `_arrange_bundle_validate_descriptor` / `_package_report_validate_descriptor`
        # — so it can simply be called here and let `ValueError` propagate like
        # every other check in this function.
        validate_descriptor(existing_descriptor)

        recorded_digest = existing_descriptor.get("content_digest")
        if recorded_digest != content_digest:
            raise ValueError(
                f"builds-root digest directory {target_dir} descriptive file "
                f"{descriptive_filename!r} declares content_digest "
                f"{recorded_digest!r}, expected {content_digest!r}"
            )

        try:
            digest_inputs = extract_digest_inputs(existing_descriptor)
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError(
                f"builds-root digest directory {target_dir} descriptive file "
                f"{descriptive_filename!r} is missing field(s) needed to recompute "
                f"content_digest: {exc}"
            ) from exc

        from svp_rpe.arrange.bundle import compute_content_digest

        recomputed_digest = compute_content_digest(digest_inputs)
        if recomputed_digest != content_digest:
            raise ValueError(
                f"builds-root digest directory {target_dir} descriptive file "
                f"{descriptive_filename!r} recomputes to content_digest "
                f"{recomputed_digest!r} from its own declared output hashes, "
                f"expected {content_digest!r} — its declared hashes are not "
                "self-consistent"
            )

    for artifact_name, expected_sha256 in digest_inputs.items():
        artifact_path = target_dir / artifact_name
        _reject_if_not_regular_file(
            artifact_path,
            label=(
                f"digest directory {target_dir} content artifact {artifact_name!r} "
                f"(declared by descriptive file {descriptive_filename!r})"
            ),
        )
        try:
            artifact_bytes = artifact_path.read_bytes()
        except OSError as exc:
            raise ValueError(
                f"builds-root digest directory {target_dir} is missing or "
                f"unreadable content artifact {artifact_name!r} declared by "
                f"descriptive file {descriptive_filename!r}: {exc}"
            ) from exc
        actual_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
        if actual_sha256 != expected_sha256:
            raise ValueError(
                f"builds-root digest directory {target_dir} content artifact "
                f"{artifact_name!r} sha256 mismatch: expected {expected_sha256!r}, "
                f"got {actual_sha256!r} (declared by descriptive file "
                f"{descriptive_filename!r})"
            )

    if provenance_differs:
        pending_descriptor = json.loads(pending_bytes)
        for key in sorted(set(existing_descriptor) | set(pending_descriptor)):
            if key in allowed_provenance_fields:
                continue
            if existing_descriptor.get(key) != pending_descriptor.get(key):
                raise ValueError(
                    f"builds-root digest directory {target_dir} descriptive file "
                    f"{descriptive_filename!r} differs from this invocation in "
                    f"non-provenance field {key!r} (existing="
                    f"{existing_descriptor.get(key)!r}, pending="
                    f"{pending_descriptor.get(key)!r}); only "
                    f"{sorted(allowed_provenance_fields)!r} may differ between "
                    "publications sharing a content_digest"
                )

    return provenance_differs


def _reject_invalid_latest_pointer_target(latest_path: Path) -> None:
    """Preflight: `<root>/latest.json` must be either absent or a plain,
    non-symlink file — checked *before* anything under `builds/` is touched.

    `latest.json` is the one path this scheme ever writes to via
    `os.replace`; a directory, a symlink (dangling or not), or any other
    non-regular entity sitting at that path must not be silently accepted
    (`os.replace` onto a directory raises anyway, but only *after* a fresh
    digest directory may already have been published — this preflight fails
    first, before `builds/` is created or written to at all, so a bad
    `latest.json` never leaves a stray digest directory behind it).
    """
    if latest_path.is_symlink() or (latest_path.exists() and not latest_path.is_file()):
        raise ValueError(
            f"builds-root latest pointer target is not a plain file: {latest_path}"
        )


def _publish_artifacts_to_builds_root(
    contents: dict[str, str],
    builds_root: str | Path,
    content_digest: str,
    *,
    descriptive_filename: str,
    expected_schema_version: str,
    extract_digest_inputs: Callable[[dict[str, Any]], dict[str, str]],
    allowed_provenance_fields: frozenset[str],
    validate_descriptor: Callable[[dict[str, Any]], None],
) -> tuple[Path, bool, bool]:
    """Publish a complete artifact set under `<builds_root>/builds/<content_digest>/`.

    Immutability contract (Design Memo §4, revised by PR #184 review): if the
    digest directory already exists, its artifacts are never rewritten,
    appended to, or fully re-verified — but a single read of its one
    *descriptive* file (`descriptive_filename`) is compared against what this
    invocation would have published, so a directory that merely happens to
    exist at this digest path isn't blindly trusted (see
    `_check_existing_builds_root_publication`, which also recomputes
    `content_digest` from the descriptor's own declared hashes via
    `extract_digest_inputs` before accepting a byte difference as mere
    provenance drift). `latest.json` is updated only when that check passes.
    Returns `(published_dir, already_existed, provenance_differs)`.

    Before anything is written, `_reject_invalid_latest_pointer_target`
    rejects an invalid `latest.json` target (see that function) so a
    misplaced `latest.json` can never leave a stray digest directory behind.

    `<builds_root>/builds` itself is rejected the same way if it is a
    symlink (any target, including a real directory): every digest directory
    this scheme publishes lives under `builds/`, so a symlinked `builds/`
    would place publications at a location outside `builds_root` that could
    be repointed out from under the immutability contract by whoever
    controls the symlink — the same hole a symlinked digest target (below)
    would open one level down. `builds_root` itself (`root`) is deliberately
    *not* checked this way: it is a user-supplied path the caller fully
    controls (like `--output-dir`), not part of the scheme's own reserved
    layout, so a symlinked `builds_root` is accepted.

    An existing digest path that is not a real, non-symlink directory (a
    regular file, a dangling symlink, *or a symlink to a directory*) is
    likewise rejected outright rather than silently treated as "already
    published": a symlink digest target — even one pointing at a real
    directory — could be repointed out from under the immutability contract
    by whoever controls the symlink, and (like the non-directory cases)
    still moving `latest.json` onto it would advertise a target this scheme
    doesn't actually control as valid.

    Self-healing on a `latest.json` update failure: if writing the artifact
    set itself succeeds but the trailing `_update_builds_latest_pointer` call
    fails (e.g. a permission or disk error on the rename), the freshly
    published digest directory is **not** rolled back or deleted — it is a
    complete, valid publication, and removing it would violate the
    immutability contract for no benefit (the failure is in the mutable
    `latest.json` pointer, not the immutable content). The run still fails
    (the caller sees the wrapped error), but a subsequent identical
    invocation takes the already-published no-op path and retries the
    `latest.json` update on its own — no separate repair step exists or is
    needed.

    First publish: the full artifact set is written into a
    `tempfile.mkdtemp` staging directory under `<builds_root>/builds/`, then
    the whole staging directory is moved onto the target with a single
    `os.rename` (an atomic directory-level publish, unlike the
    snapshot+per-file `os.replace` dance `_publish_artifacts_atomically`
    needs for the mutable `--output-dir` case — a fresh digest directory has
    no previous content to roll back to). A `rename` failure that turns out
    to be a concurrent publish (the target now exists *as a real directory*)
    is treated as the immutable no-op case; any other failure propagates
    after best-effort staging cleanup.
    """
    import os
    import shutil
    import tempfile

    root = Path(builds_root)
    builds_dir = root / "builds"
    target_dir = builds_dir / content_digest
    latest_path = root / "latest.json"

    if builds_dir.is_symlink():
        raise ValueError(f"builds-root builds directory is a symlink: {builds_dir}")

    _reject_invalid_latest_pointer_target(latest_path)

    def _reject_if_not_directory() -> None:
        if target_dir.is_symlink() or (target_dir.exists() and not target_dir.is_dir()):
            raise ValueError(
                f"builds-root digest target is not a directory: {target_dir}"
            )

    _reject_if_not_directory()
    already_existed = target_dir.is_dir()
    if not already_existed:
        builds_dir.mkdir(parents=True, exist_ok=True)
        staging = tempfile.mkdtemp(dir=builds_dir)
        try:
            staging_path = Path(staging)
            for filename, content in contents.items():
                (staging_path / filename).write_bytes(content.encode("utf-8"))
            try:
                os.rename(staging, target_dir)
            except OSError:
                _reject_if_not_directory()
                if target_dir.is_dir():
                    # Lost a concurrent-publish race: the directory some other
                    # process just published wins (immutability contract).
                    already_existed = True
                else:
                    raise
        finally:
            if Path(staging).exists():
                shutil.rmtree(staging, ignore_errors=True)

    provenance_differs = False
    if already_existed:
        provenance_differs = _check_existing_builds_root_publication(
            target_dir,
            descriptive_filename,
            contents[descriptive_filename],
            content_digest=content_digest,
            expected_schema_version=expected_schema_version,
            extract_digest_inputs=extract_digest_inputs,
            allowed_provenance_fields=allowed_provenance_fields,
            validate_descriptor=validate_descriptor,
        )

    try:
        _update_builds_latest_pointer(latest_path, content_digest, root=root)
    except Exception as exc:
        raise ValueError(
            f"failed to update {latest_path} to point at content_digest "
            f"{content_digest!r}: {exc}. The digest directory {target_dir} was "
            "already published successfully and remains valid (not rolled back); "
            "re-running this command with the same inputs will retry the "
            "latest.json update via the already-published no-op path."
        ) from exc
    return target_dir, already_existed, provenance_differs


def _print_builds_root_publish_note(
    content_digest: str, *, already_existed: bool, provenance_differs: bool
) -> None:
    if not already_existed:
        return
    if provenance_differs:
        typer.echo(
            f"note: builds/{content_digest} already published; existing publication "
            "retained; its provenance differs from this invocation (latest.json updated)",
            err=True,
        )
    else:
        typer.echo(
            f"note: builds/{content_digest} already published; left untouched "
            "(latest.json updated)",
            err=True,
        )
