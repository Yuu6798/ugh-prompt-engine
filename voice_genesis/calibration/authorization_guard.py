"""Repository-side fail-closed guard for VoiceGenesis campaign authorization references.

This module does *not* authenticate a user or turn repository/Drive metadata into
an authority source. It only enforces that any changed production campaign is
either explicitly quarantined or carries a direct-user-approval *reference* that
is scoped to the campaign. The executor must still verify that reference against
the task/conversation authority source before running gated operations.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

AUTH_REF_FILENAME = "direct_authorization_ref.json"
QUARANTINE_FILENAME = "authorization_quarantine.json"
AUTH_REF_SCHEMA = "vgcal-direct-authorization-ref/1"
QUARANTINE_SCHEMA = "vgcal-quarantine/1"
DIRECT_USER_APPROVAL = "DIRECT_USER_APPROVAL"
REQUIRED_PRE_FREEZE_OPERATIONS = frozenset({"C0_FREEZE", "CAMPAIGN_EXECUTION"})
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


def _validate_direct_reference(campaign_id: str, path: Path) -> GuardResult:
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
        normalized = ref.strip().lower()
        if normalized.startswith(FORBIDDEN_REFERENCE_PREFIXES):
            reasons.append(
                "direct_approval_ref must not use repository/Drive/signature/relay metadata as authority"
            )
        if any(fragment in normalized for fragment in FORBIDDEN_REFERENCE_FRAGMENTS):
            reasons.append(
                "direct_approval_ref must identify a direct scoped user approval, not generalized delegation"
            )

    operations = data.get("approved_operations")
    if not isinstance(operations, list) or any(not _nonblank_string(x) for x in operations):
        reasons.append("approved_operations must be a list of non-blank strings")
    else:
        missing = REQUIRED_PRE_FREEZE_OPERATIONS.difference(operations)
        if missing:
            reasons.append(
                "approved_operations missing required operations: " + ", ".join(sorted(missing))
            )

    if data.get("reference_is_not_authentication") is not True:
        reasons.append("reference_is_not_authentication must be true")

    return GuardResult(campaign_id, not reasons, "DIRECT_REFERENCE", tuple(reasons))


def validate_campaign(campaign_dir: Path) -> GuardResult:
    campaign_id = campaign_dir.name
    manifest = campaign_dir / "c0_manifest.json"
    if not manifest.is_file():
        return GuardResult(campaign_id, True, "NO_C0_MANIFEST", ())

    quarantine = campaign_dir / QUARANTINE_FILENAME
    if quarantine.is_file():
        return _validate_quarantine(campaign_id, quarantine)

    auth_ref = campaign_dir / AUTH_REF_FILENAME
    if not auth_ref.is_file():
        return GuardResult(
            campaign_id,
            False,
            "MISSING_DIRECT_REFERENCE",
            (
                f"{AUTH_REF_FILENAME} is required unless the campaign has a valid {QUARANTINE_FILENAME}",
            ),
        )
    return _validate_direct_reference(campaign_id, auth_ref)


def _campaign_dir(repo_root: Path, campaign_id: str) -> Path:
    return repo_root / "voice_genesis" / "calibration" / "campaigns" / campaign_id


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed VoiceGenesis campaign authorization-reference guard"
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--campaign-id", action="append", required=True)
    args = parser.parse_args(argv)

    failed = False
    for campaign_id in args.campaign_id:
        result = validate_campaign(_campaign_dir(args.repo_root, campaign_id))
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
