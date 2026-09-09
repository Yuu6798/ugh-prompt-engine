"""Repository-side fail-closed guard for VoiceGenesis campaign authorization references.

This module does *not* authenticate a user or turn repository/Drive metadata into
an authority source. It only enforces that any changed production campaign is
either explicitly quarantined or carries a direct-user-approval *reference* that
is scoped to the campaign. The executor must still verify that reference against
the task/conversation authority source before running gated operations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

AUTH_REF_FILENAME = "direct_authorization_ref.json"
QUARANTINE_FILENAME = "authorization_quarantine.json"
AUTH_REF_SCHEMA = "vgcal-direct-authorization-ref/1"
QUARANTINE_SCHEMA = "vgcal-quarantine/1"
DIRECT_USER_APPROVAL = "DIRECT_USER_APPROVAL"
REQUIRED_PRE_FREEZE_OPERATIONS = frozenset({"C0_FREEZE", "CAMPAIGN_EXECUTION"})
SEAL_ACCEPTANCE_OPERATION = "GATE3_SEAL_ACCEPTANCE"
POST_SEAL_LEDGER_KINDS = frozenset(
    {
        "gate3_accepted",
        "holdout_unseal",
        "holdout_render_valid",
        "holdout_executed_valid",
        "split_secret_revealed",
    }
)
POST_SEAL_STAGES = frozenset({"c4", "c4-holdout"})
FORBIDDEN_REFERENCE_PREFIXES = (
    "drive:",
    "drive://",
    "repo:",
    "repo://",
    "git:",
    "git://",
    "file:",
    "file://",
    "signed_by:",
    "relayed_by:",
)
FORBIDDEN_REFERENCE_FRAGMENTS = (
    "delegated",
    "delegation",
    "general delegation",
    "generalized delegation",
    "委任",
    "signed by",
    "signed-by",
    "signed_by",
    "relayed by",
    "relayed-by",
    "relayed_by",
)
FORBIDDEN_REFERENCE_HOSTS = frozenset(
    {
        "api.github.com",
        "docs.google.com",
        "drive.google.com",
        "github.com",
        "raw.githubusercontent.com",
    }
)


@dataclass(frozen=True)
class GuardResult:
    campaign_id: str
    ok: bool
    mode: str
    reasons: tuple[str, ...]


def _load_json(path: Path) -> Mapping[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError(f"{path}: expected a JSON object")
    return raw


def _nonblank_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_base_evidence_preserved(
    campaign_id: str,
    campaign_dir: Path,
    base_campaign_dir: Path,
    *,
    reject_new_files: bool = False,
) -> GuardResult:
    reasons: list[str] = []
    base_files = sorted(
        path.relative_to(base_campaign_dir)
        for path in base_campaign_dir.rglob("*")
        if path.is_file()
    )
    for relative_path in base_files:
        base_path = base_campaign_dir / relative_path
        current_path = campaign_dir / relative_path
        if not current_path.is_file() or current_path.is_symlink():
            reasons.append(f"base evidence file removed: {relative_path.as_posix()}")
            continue
        if _file_sha256(current_path) != _file_sha256(base_path):
            reasons.append(f"base evidence file modified: {relative_path.as_posix()}")
    if reject_new_files:
        base_file_set = set(base_files)
        current_files = sorted(
            path.relative_to(campaign_dir)
            for path in campaign_dir.rglob("*")
            if path.is_file() or path.is_symlink()
        )
        for relative_path in current_files:
            if relative_path not in base_file_set:
                reasons.append(
                    "new file added to quarantined campaign: "
                    + relative_path.as_posix()
                )
    return GuardResult(campaign_id, not reasons, "QUARANTINE", tuple(reasons))


def _validate_quarantine(campaign_id: str, path: Path) -> GuardResult:
    reasons: list[str] = []
    try:
        data = _load_json(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return GuardResult(campaign_id, False, "QUARANTINE", (f"invalid quarantine marker: {exc}",))

    if data.get("schema") != QUARANTINE_SCHEMA:
        reasons.append(f"schema must be {QUARANTINE_SCHEMA!r}")
    if data.get("campaign_id") != campaign_id:
        reasons.append("quarantine campaign_id must match directory name")
    if data.get("status") != "QUARANTINED":
        reasons.append("quarantine status must be 'QUARANTINED'")
    for key in ("claimable", "debt_discharge_eligible", "run11_eligible"):
        if data.get(key) is not False:
            reasons.append(f"{key} must be false for a quarantined campaign")
    reason_codes = data.get("reason_codes")
    if not isinstance(reason_codes, list) or "AUTHORIZATION_BOUNDARY_NOT_VERIFIED" not in reason_codes:
        reasons.append("reason_codes must include AUTHORIZATION_BOUNDARY_NOT_VERIFIED")
    return GuardResult(campaign_id, not reasons, "QUARANTINE", tuple(reasons))


def _required_operations(campaign_dir: Path) -> tuple[frozenset[str], tuple[str, ...]]:
    """Derive operation scope from campaign evidence without trusting the auth ref.

    Gate 3 is intentionally post-freeze, so it cannot be inferred from the C0
    manifest.  Any ledger evidence that the sealed holdout was accepted,
    unsealed, rendered, measured, or revealed therefore requires an independent
    Gate 3 reference.
    """

    required = set(REQUIRED_PRE_FREEZE_OPERATIONS)
    ledger_path = campaign_dir / "ledger.jsonl"
    if not ledger_path.exists():
        return frozenset(required), ()
    if not ledger_path.is_file() or ledger_path.is_symlink():
        return frozenset(required), ("ledger.jsonl must be a regular file",)

    line_number = 0
    try:
        with ledger_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                entry = json.loads(line)
                if not isinstance(entry, Mapping):
                    raise ValueError("expected a JSON object")
                payload = entry.get("payload")
                if not isinstance(payload, Mapping):
                    raise ValueError("payload must be a JSON object")
                if (
                    payload.get("kind") in POST_SEAL_LEDGER_KINDS
                    or payload.get("stage") in POST_SEAL_STAGES
                    or payload.get("split") == "HOLDOUT"
                ):
                    required.add(SEAL_ACCEPTANCE_OPERATION)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return frozenset(required), (
            f"cannot derive authorization scope from ledger.jsonl: line {line_number}: {exc}",
        )

    return frozenset(required), ()


def _validate_direct_reference(
    campaign_id: str,
    path: Path,
    campaign_dir: Path,
    base_campaign_dir: Path | None,
) -> GuardResult:
    reasons: list[str] = []
    try:
        data = _load_json(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return GuardResult(campaign_id, False, "DIRECT_REFERENCE", (f"invalid authorization reference: {exc}",))

    if data.get("schema") != AUTH_REF_SCHEMA:
        reasons.append(f"schema must be {AUTH_REF_SCHEMA!r}")
    if data.get("campaign_id") != campaign_id:
        reasons.append("authorization campaign_id must match directory name")
    if data.get("authority_type") != DIRECT_USER_APPROVAL:
        reasons.append(f"authority_type must be {DIRECT_USER_APPROVAL!r}")

    ref = data.get("direct_approval_ref")
    if not _nonblank_string(ref):
        reasons.append("direct_approval_ref must be a non-blank string")
    else:
        normalized = unicodedata.normalize("NFKC", ref).casefold()
        normalized = "".join(
            "" if unicodedata.category(char) == "Cf" else char
            for char in normalized
        )
        normalized = " ".join(normalized.split())
        hostname = urlparse(normalized).hostname
        if hostname is None and "://" not in normalized:
            hostname = urlparse("//" + normalized).hostname
        if normalized.startswith(FORBIDDEN_REFERENCE_PREFIXES):
            reasons.append(
                "direct_approval_ref must not use repository/Drive/signature/relay metadata as authority"
            )
        if hostname is not None and any(
            hostname == forbidden or hostname.endswith(f".{forbidden}")
            for forbidden in FORBIDDEN_REFERENCE_HOSTS
        ):
            reasons.append(
                "direct_approval_ref must not use repository/Drive URLs as authority"
            )
        if any(fragment in normalized for fragment in FORBIDDEN_REFERENCE_FRAGMENTS):
            reasons.append(
                "direct_approval_ref must identify a direct scoped user approval, not generalized delegation"
            )

    required_operations, ledger_reasons = _required_operations(campaign_dir)
    reasons.extend(ledger_reasons)
    if base_campaign_dir is not None:
        base_required_operations, base_ledger_reasons = _required_operations(
            base_campaign_dir
        )
        required_operations = required_operations.union(base_required_operations)
        reasons.extend(
            "trusted-base " + reason for reason in base_ledger_reasons
        )
    operations = data.get("approved_operations")
    if not isinstance(operations, list) or any(not _nonblank_string(x) for x in operations):
        reasons.append("approved_operations must be a list of non-blank strings")
    else:
        missing = required_operations.difference(operations)
        if missing:
            reasons.append(
                "approved_operations missing required operations: " + ", ".join(sorted(missing))
            )

    if data.get("reference_is_not_authentication") is not True:
        reasons.append("reference_is_not_authentication must be true")

    return GuardResult(campaign_id, not reasons, "DIRECT_REFERENCE", tuple(reasons))


def validate_campaign(
    campaign_dir: Path,
    *,
    base_campaign_dir: Path | None = None,
) -> GuardResult:
    campaign_id = campaign_dir.name
    if campaign_dir.is_symlink():
        return GuardResult(
            campaign_id,
            False,
            "UNSAFE_CAMPAIGN_PATH",
            ("campaign directory must not be a symlink",),
        )
    manifest = campaign_dir / "c0_manifest.json"
    base_manifest = (
        base_campaign_dir / "c0_manifest.json"
        if base_campaign_dir is not None
        else None
    )
    if manifest.is_symlink():
        return GuardResult(
            campaign_id,
            False,
            "UNSAFE_CAMPAIGN_PATH",
            ("c0_manifest.json must not be a symlink",),
        )
    if not manifest.is_file():
        if base_manifest is not None and base_manifest.is_file():
            return GuardResult(
                campaign_id,
                False,
                "C0_MANIFEST_REMOVED",
                ("an existing frozen campaign c0_manifest.json must be preserved",),
            )
        return GuardResult(campaign_id, True, "NO_C0_MANIFEST", ())
    if base_manifest is not None and base_manifest.is_file():
        if base_manifest.is_symlink():
            return GuardResult(
                campaign_id,
                False,
                "UNSAFE_BASE_CAMPAIGN",
                ("trusted-base c0_manifest.json must not be a symlink",),
            )
        if _file_sha256(manifest) != _file_sha256(base_manifest):
            return GuardResult(
                campaign_id,
                False,
                "C0_MANIFEST_MODIFIED",
                ("an existing frozen campaign c0_manifest.json must remain byte-identical",),
            )

    quarantine = campaign_dir / QUARANTINE_FILENAME
    if quarantine.is_symlink():
        return GuardResult(
            campaign_id,
            False,
            "QUARANTINE",
            (f"{QUARANTINE_FILENAME} must not be a symlink",),
        )
    base_quarantine = (
        base_campaign_dir / QUARANTINE_FILENAME
        if base_campaign_dir is not None
        else None
    )
    if (
        base_quarantine is not None
        and base_quarantine.is_file()
        and not quarantine.is_file()
    ):
        return GuardResult(
            campaign_id,
            False,
            "QUARANTINE",
            ("an existing quarantine marker must be preserved",),
        )
    if quarantine.is_file():
        quarantine_result = _validate_quarantine(campaign_id, quarantine)
        if not quarantine_result.ok or base_campaign_dir is None:
            return quarantine_result
        evidence_result = _validate_base_evidence_preserved(
            campaign_id,
            campaign_dir,
            base_campaign_dir,
            reject_new_files=bool(base_quarantine and base_quarantine.is_file()),
        )
        return GuardResult(
            campaign_id,
            evidence_result.ok,
            "QUARANTINE",
            evidence_result.reasons,
        )

    auth_ref = campaign_dir / AUTH_REF_FILENAME
    if auth_ref.is_symlink():
        return GuardResult(
            campaign_id,
            False,
            "DIRECT_REFERENCE",
            (f"{AUTH_REF_FILENAME} must not be a symlink",),
        )
    if not auth_ref.is_file():
        return GuardResult(
            campaign_id,
            False,
            "MISSING_DIRECT_REFERENCE",
            (
                f"{AUTH_REF_FILENAME} is required unless the campaign has a valid {QUARANTINE_FILENAME}",
            ),
        )
    return _validate_direct_reference(
        campaign_id,
        auth_ref,
        campaign_dir,
        base_campaign_dir,
    )


def _campaign_dir(repo_root: Path, campaign_id: str) -> Path:
    if not _nonblank_string(campaign_id) or Path(campaign_id).name != campaign_id:
        raise ValueError("campaign_id must be one non-blank path component")

    campaigns_root = repo_root
    for component in ("voice_genesis", "calibration", "campaigns"):
        campaigns_root = campaigns_root / component
        if campaigns_root.is_symlink():
            raise ValueError(f"campaign path component must not be a symlink: {component}")

    campaign_dir = campaigns_root / campaign_id
    if campaign_dir.is_symlink():
        raise ValueError("campaign directory must not be a symlink")
    if campaign_dir.resolve(strict=False).parent != campaigns_root.resolve(strict=False):
        raise ValueError("campaign directory must remain inside campaigns root")
    return campaign_dir


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed VoiceGenesis campaign authorization-reference guard"
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--base-repo-root",
        type=Path,
        help="Base-revision checkout used to reject removal of frozen campaign manifests",
    )
    parser.add_argument("--campaign-id", action="append", required=True)
    args = parser.parse_args(argv)

    failed = False
    for campaign_id in args.campaign_id:
        base_campaign_dir = (
            _campaign_dir(args.base_repo_root, campaign_id)
            if args.base_repo_root is not None
            else None
        )
        result = validate_campaign(
            _campaign_dir(args.repo_root, campaign_id),
            base_campaign_dir=base_campaign_dir,
        )
        print(
            json.dumps(
                {
                    "campaign_id": result.campaign_id,
                    "ok": result.ok,
                    "mode": result.mode,
                    "reasons": list(result.reasons),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        failed = failed or not result.ok
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
