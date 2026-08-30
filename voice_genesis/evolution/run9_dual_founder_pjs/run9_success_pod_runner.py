#!/usr/bin/env python3
"""Prepare or explicitly launch the RUN9 success-only admission Pod.

``prepare`` is read-only and is the default operational boundary.  ``launch``
performs the single billable POST only when the caller supplies the exact
confirmation token.  Both commands require a clean commit that is published at
the same branch name on ``origin`` so the Pod can fetch exactly those bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any


API_BASE = "https://rest.runpod.io/v1"
CREATE_POD_URL = f"{API_BASE}/pods"
REPOSITORY_URL = "https://github.com/Yuu6798/ugh-prompt-engine.git"
RUN_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = RUN_DIR.parents[2]
ENTRY_RELATIVE = (
    "voice_genesis/evolution/run9_dual_founder_pjs/run9_success_pod_entry.sh"
)
POLICY_RELATIVE = (
    "voice_genesis/evolution/run9_dual_founder_pjs/inputs/"
    "success_only_admission_policy.json"
)
ADMISSION_RELATIVE = (
    "voice_genesis/evolution/run9_dual_founder_pjs/run9_success_admission.py"
)
IMAGE = (
    "runpod/base:1.1.0-ubuntu2404@"
    "sha256:6a7ffc191ddee44bcede7b8508a76f67df690df7d58043edd6695ef63bffef23"
)
CONFIRMATION = "RUN9_SUCCESS_ONLY"
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class PreflightError(RuntimeError):
    """The local or remote source boundary is not launchable."""


class AmbiguousLaunchError(RuntimeError):
    """The RunPod create request may have produced a billable Pod without a handle."""


def _git(*args: str, cwd: pathlib.Path = REPO_ROOT) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _bootstrap_command() -> str:
    return r'''set -euo pipefail
bootstrap_self_stop() {
  if [ -z "${RUNPOD_POD_ID:-}" ]; then
    echo "run9 bootstrap: RUNPOD_POD_ID unset; self-stop skipped" >&2
    return 0
  fi
  local attempt
  for attempt in 1 2 3 4 5; do
    if runpodctl stop pod "$RUNPOD_POD_ID"; then
      return 0
    fi
    echo "run9 bootstrap: self-stop attempt=$attempt failed" >&2
    [ "$attempt" -lt 5 ] && sleep 30
  done
  echo "run9 bootstrap: SELF-STOP FAILED after 5 attempts; pod may still be billing: $RUNPOD_POD_ID" >&2
  return 1
}
bootstrap_exit() {
  ec=$?
  if [ "$ec" -ne 0 ]; then
    bootstrap_self_stop || true
  fi
  exit "$ec"
}
trap bootstrap_exit EXIT
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl git python3
rm -rf /root/run9-bootstrap
git init /root/run9-bootstrap
git -C /root/run9-bootstrap remote add origin https://github.com/Yuu6798/ugh-prompt-engine.git
git -C /root/run9-bootstrap fetch --depth 1 origin "$RUN9_PIN_COMMIT"
git -C /root/run9-bootstrap checkout --detach FETCH_HEAD
actual="$(git -C /root/run9-bootstrap rev-parse HEAD)"
[ "$actual" = "$RUN9_PIN_COMMIT" ]
entry=/root/run9-bootstrap/voice_genesis/evolution/run9_dual_founder_pjs/run9_success_pod_entry.sh
chmod +x "$entry"
set +e
bash "$entry" 2>&1 | tee /workspace/run9_console.log
entry_ec=${PIPESTATUS[0]}
set -e
trap - EXIT
exit "$entry_ec"'''


def build_launch_payload(source_commit: str) -> dict[str, Any]:
    """Return the complete, secret-free Pod creation payload."""
    if not _COMMIT_RE.fullmatch(source_commit):
        raise ValueError("source_commit must be 40 lowercase hexadecimal characters")
    return {
        "cloudType": "SECURE",
        "computeType": "CPU",
        "containerDiskInGb": 80,
        "cpuFlavorIds": ["cpu5c"],
        "cpuFlavorPriority": "availability",
        "dockerEntrypoint": ["/bin/bash", "-lc"],
        "dockerStartCmd": [_bootstrap_command()],
        "env": {"RUN9_PIN_COMMIT": source_commit},
        "imageName": IMAGE,
        "interruptible": False,
        "locked": False,
        "name": "run9-success-only-admission",
        "ports": ["8000/http"],
        "supportPublicIp": True,
        "vcpuCount": 16,
        "volumeInGb": 10,
        "volumeMountPath": "/workspace",
    }


def _required_paths(repo_root: pathlib.Path) -> tuple[pathlib.Path, ...]:
    return tuple(
        repo_root / relative
        for relative in (ENTRY_RELATIVE, POLICY_RELATIVE, ADMISSION_RELATIVE)
    )


def _validate_policy(path: pathlib.Path) -> None:
    with path.open(encoding="utf-8") as stream:
        policy = json.load(stream)
    admission = policy.get("admission")
    if not isinstance(admission, Mapping) or admission.get("decision") != "PASS_ONLY":
        raise PreflightError("success-only policy is not PASS_ONLY")
    if admission.get("failure_registry_effect") != "NONE":
        raise PreflightError("failure policy does not preserve an unchanged registry")
    evaluator = policy.get("evaluator_boundary")
    if not isinstance(evaluator, Mapping):
        raise PreflightError("evaluator boundary is missing")
    forbidden = evaluator.get("forbidden_inputs")
    if not isinstance(forbidden, list) or "candidate_artifact_sha256" not in forbidden:
        raise PreflightError("candidate identity is not forbidden at the evaluator boundary")


def verify_prelaunch(repo_root: pathlib.Path = REPO_ROOT) -> dict[str, Any]:
    """Verify the exact local/remote source boundary without creating a Pod."""
    repo_root = repo_root.resolve()
    head = _git("rev-parse", "HEAD", cwd=repo_root)
    if not _COMMIT_RE.fullmatch(head):
        raise PreflightError(f"invalid HEAD: {head!r}")
    dirty = _git("status", "--porcelain=v1", "--untracked-files=all", cwd=repo_root)
    if dirty:
        raise PreflightError("working tree is not clean; commit the reviewed bytes first")
    branch = _git("symbolic-ref", "--quiet", "--short", "HEAD", cwd=repo_root)
    if not branch:
        raise PreflightError("detached HEAD cannot establish a published branch boundary")
    missing = [str(path) for path in _required_paths(repo_root) if not path.is_file()]
    if missing:
        raise PreflightError(f"required source files are missing: {missing}")
    subprocess.run(
        ["bash", "-n", str(repo_root / ENTRY_RELATIVE)],
        cwd=repo_root,
        check=True,
    )
    _validate_policy(repo_root / POLICY_RELATIVE)
    remote = _git("ls-remote", "--heads", "origin", f"refs/heads/{branch}", cwd=repo_root)
    remote_head = remote.split()[0] if remote else ""
    if remote_head != head:
        raise PreflightError(
            f"origin/{branch} does not publish HEAD exactly "
            f"(local={head}, remote={remote_head or 'missing'})"
        )
    payload = build_launch_payload(head)
    return {
        "schema": "run9-success-pod-prelaunch/1.0",
        "ready": True,
        "pod_created": False,
        "source_branch": branch,
        "source_commit": head,
        "image": IMAGE,
        "payload_sha256": hashlib.sha256(_canonical_json_bytes(payload)).hexdigest(),
        "credential_present": bool(os.environ.get("RUNPOD_API_KEY")),
        "next_action": "launch requires the exact confirmation token",
        "payload": payload,
    }


def _request_json(
    method: str,
    url: str,
    *,
    api_key: str,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    body = None if payload is None else _canonical_json_bytes(payload)
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if method == "POST" and 500 <= exc.code <= 599:
            raise AmbiguousLaunchError(
                f"RunPod {method} server error {exc.code} after request initiation: {detail}"
            ) from exc
        raise RuntimeError(f"RunPod API {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AmbiguousLaunchError(
            f"RunPod {method} transport failed after request initiation: {type(exc).__name__}"
        ) from exc
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AmbiguousLaunchError(
            f"RunPod {method} response was received but could not be decoded as JSON"
        ) from exc
    if not isinstance(result, dict):
        raise AmbiguousLaunchError(f"RunPod {method} returned a non-object JSON response")
    return result


def _print_json(value: object) -> None:
    json.dump(value, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def _extract_pod_id(response: Mapping[str, Any]) -> str | None:
    """Return a non-empty RunPod identifier from known REST response keys."""
    value = response.get("id") or response.get("podId")
    return value if isinstance(value, str) and value else None


def _prepare(_: argparse.Namespace) -> int:
    _print_json(verify_prelaunch())
    return 0


def _launch(args: argparse.Namespace) -> int:
    if args.confirm_launch != CONFIRMATION:
        raise PreflightError(
            f"refusing billable Pod creation; pass --confirm-launch {CONFIRMATION}"
        )
    api_key = os.environ.get("RUNPOD_API_KEY", "")
    if not api_key:
        raise PreflightError("RUNPOD_API_KEY is not configured")
    report = verify_prelaunch()
    try:
        response = _request_json(
            "POST",
            CREATE_POD_URL,
            api_key=api_key,
            payload=report["payload"],
        )
    except AmbiguousLaunchError as exc:
        _print_json(
            {
                "schema": "run9-success-pod-launch/1.0",
                "source_commit": report["source_commit"],
                "payload_sha256": report["payload_sha256"],
                "launch_status": "AMBIGUOUS_POST",
                "pod_id": None,
                "pod": None,
            }
        )
        raise RuntimeError(
            "RunPod POST /pods outcome is ambiguous after request initiation; a billable Pod "
            "may exist without a recorded stop handle — reconcile in the RunPod console immediately"
        ) from exc
    pod_id = _extract_pod_id(response)
    launch_record = {
        "schema": "run9-success-pod-launch/1.0",
        "source_commit": report["source_commit"],
        "payload_sha256": report["payload_sha256"],
        "launch_status": "CONFIRMED_RESPONSE" if pod_id is not None else "AMBIGUOUS_NO_POD_ID",
        "pod_id": pod_id,
        "pod": response,
    }
    _print_json(launch_record)
    if pod_id is None:
        raise RuntimeError(
            "RunPod POST /pods succeeded but returned no id/podId; a billable Pod may "
            "exist without a recorded stop handle — reconcile in the RunPod console immediately"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="verify and print; never create a Pod")
    prepare.set_defaults(handler=_prepare)
    launch = subparsers.add_parser("launch", help="perform one confirmed Pod creation POST")
    launch.add_argument("--confirm-launch", default="")
    launch.set_defaults(handler=_launch)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (PreflightError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"run9 pod runner: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
