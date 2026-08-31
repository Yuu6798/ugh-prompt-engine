from __future__ import annotations

import json
import os
import shlex
import signal
import time
import pathlib
import subprocess
import sys
from typing import Any

import pytest


RUN_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(RUN_DIR) not in sys.path:
    sys.path.insert(0, str(RUN_DIR))

import run9_success_pod_runner as runner  # noqa: E402


COMMIT = "a" * 40


def test_payload_is_cpu_pinned_non_interruptible_and_secret_free() -> None:
    payload = runner.build_launch_payload(COMMIT)
    assert payload["computeType"] == "CPU"
    assert payload["cpuFlavorIds"] == ["cpu5c"]
    assert payload["interruptible"] is False
    assert payload["locked"] is False
    assert payload["imageName"].startswith("runpod/base:1.1.0-ubuntu2404@sha256:")
    assert payload["ports"] == ["8000/http"]
    assert payload["env"] == {"RUN9_PIN_COMMIT": COMMIT}
    assert "RUNPOD_API_KEY" not in json.dumps(payload, sort_keys=True)


def test_payload_bootstrap_fetches_exact_commit_and_stops_on_bootstrap_failure() -> None:
    command = runner.build_launch_payload(COMMIT)["dockerStartCmd"][0]
    assert 'readonly BOOTSTRAP_NETWORK_TIMEOUT_SECONDS=1800' in command
    assert 'timeout --foreground --signal=TERM --kill-after=30s' in command
    assert 'bootstrap_network apt-get update' in command
    assert 'bootstrap_network apt-get install -y --no-install-recommends' in command
    assert 'bootstrap_network git -C /root/run9-bootstrap fetch --depth 1 origin "$RUN9_PIN_COMMIT"' in command
    assert 'actual="$(git -C /root/run9-bootstrap rev-parse HEAD)"' in command
    assert '[ "$actual" = "$RUN9_PIN_COMMIT" ]' in command
    assert 'runpodctl stop pod "$RUNPOD_POD_ID"' in command
    assert "for attempt in 1 2 3 4 5" in command
    assert 'sleep 30' in command
    assert "SELF-STOP FAILED after 5 attempts" in command
    assert "run9_success_pod_entry.sh" in command


def test_checked_in_policy_passes_runner_preflight_shape_check() -> None:
    runner._validate_policy(RUN_DIR / "inputs" / "success_only_admission_policy.json")


def test_entry_watchdog_reuses_confirmed_retrying_self_stop() -> None:
    text = (RUN_DIR / "run9_success_pod_entry.sh").read_text(encoding="utf-8")
    watchdog = text[text.index('(\n  readonly WATCHDOG_SELF_PID='): text.index('WATCHDOG_PID=$!')]
    assert watchdog.index("terminate_main_for_deadline") < watchdog.index("force_restore_network_for_stop")
    assert watchdog.index("force_restore_network_for_stop") < watchdog.index("self_stop || true")
    assert "self_stop || true" in watchdog
    assert 'runpodctl stop pod "$RUNPOD_POD_ID" || true' not in watchdog
    assert "for attempt in 1 2 3 4 5" in text


def test_native_lock_keeps_os_base_outside_exact_install_boundary() -> None:
    names = {
        row.rpartition("=")[0].split(":", 1)[0]
        for row in (RUN_DIR / "inputs" / "measurement_native_install_lock.txt").read_text(encoding="utf-8").splitlines()
        if row.strip()
    }
    assert "libc6" not in names
    assert "libc6-dev" not in names
    assert "linux-libc-dev" not in names
    assert "dpkg" not in names
    assert "perl-base" not in names
    assert {"gcc", "g++", "make", "binutils", "libffi-dev", "libsndfile1"} <= names


def test_entry_native_preflight_reads_existing_verified_bootstrap_checkout() -> None:
    text = (RUN_DIR / "run9_success_pod_entry.sh").read_text(encoding="utf-8")
    bootstrap_dir = text.index("readonly BOOTSTRAP_RUN_DIR=")
    preflight_start = text.index('stage "preflight-system"')
    source_checkout = text.index('stage "source-checkout"')
    assert bootstrap_dir < preflight_start < source_checkout
    preflight = text[preflight_start:text.index('stage "python-3.11.15"')]
    assert 'git -C "$BOOTSTRAP_RUN_DIR" rev-parse HEAD' in preflight
    assert '[ "$BOOTSTRAP_HEAD" = "$RUN9_PIN_COMMIT" ]' in preflight
    assert '$BOOTSTRAP_RUN_DIR/inputs/measurement_native_install_lock.txt' in preflight
    assert 'measurement_native_manifest.txt' not in preflight
    assert '$RUN_DIR/inputs/measurement_native_' not in preflight


def test_entry_measurement_environment_uses_committed_full_lock() -> None:
    text = (RUN_DIR / "run9_success_pod_entry.sh").read_text(encoding="utf-8")
    measurement = text[text.index('stage "measurement-environment"'): text.index('stage "render-network-isolation"')]
    assert 'measurement_environment_lock.txt' in measurement
    assert '--no-deps --no-build-isolation' in measurement
    assert 'pip", "freeze", "--all"' in measurement
    lock = RUN_DIR / "inputs" / "measurement_environment_lock.txt"
    rows = [line for line in lock.read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) > 7
    assert all(row.count("==") == 1 for row in rows)


def test_entry_uses_committed_exact_native_closure() -> None:
    text = (RUN_DIR / "run9_success_pod_entry.sh").read_text(encoding="utf-8")
    preflight = text[text.index('stage "preflight-system"'): text.index('stage "python-3.11.15"')]
    assert "measurement_native_install_lock.txt" in preflight
    assert "measurement_native_manifest.txt" not in preflight
    assert 'dpkg-query", "-W"' in preflight
    assert 'ca-certificates curl git iptables unzip xz-utils' in preflight
    lock = RUN_DIR / "inputs" / "measurement_native_install_lock.txt"
    rows = [line for line in lock.read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) > 20
    assert any(row.startswith("libsndfile1:") for row in rows)
    assert not any(row.startswith("libc6:") for row in rows)
    assert any(row.startswith("gcc") for row in rows)
    assert all("=" in row for row in rows)


def test_entry_keeps_watchdog_through_hold_and_caps_wall_clock() -> None:
    text = (RUN_DIR / "run9_success_pod_entry.sh").read_text(encoding="utf-8")
    block = text[text.index("on_exit() {"): text.index("trap on_exit EXIT")]
    assert 'STOP_BUDGET_SECONDS=180' in text
    assert 'max_hold=$(( remaining - STOP_BUDGET_SECONDS ))' in block
    assert block.index('sleep "$hold"') < block.index('if self_stop; then')
    assert block.index('if self_stop; then') < block.index('kill "$WATCHDOG_PID"')
    assert block.index('wait "$WATCHDOG_PID"') > block.index('if self_stop; then')


def test_watchdog_terminates_workload_before_opening_network_for_stop() -> None:
    text = (RUN_DIR / "run9_success_pod_entry.sh").read_text(encoding="utf-8")
    watchdog = text[text.index('(\n  readonly WATCHDOG_SELF_PID='): text.index('WATCHDOG_PID=$!')]
    assert 'readonly WATCHDOG_SELF_PID="$BASHPID"' in watchdog
    assert 'terminate_main_for_deadline "$WATCHDOG_SELF_PID"' in watchdog
    assert watchdog.index("terminate_main_for_deadline") < watchdog.index("force_restore_network_for_stop")
    assert watchdog.index("force_restore_network_for_stop") < watchdog.index("self_stop || true")


def test_deadline_process_tree_preserves_outer_watchdog(tmp_path: pathlib.Path) -> None:
    text = (RUN_DIR / "run9_success_pod_entry.sh").read_text(encoding="utf-8")
    start = text.index("terminate_main_for_deadline() {")
    end = text.index("\n\nself_stop()", start)
    helper = text[start:end]

    marker = tmp_path / "watchdog-survived.txt"
    workload_pid_file = tmp_path / "workload.pid"
    grandchild_pid_file = tmp_path / "grandchild.pid"
    q_marker = shlex.quote(str(marker))
    q_workload = shlex.quote(str(workload_pid_file))
    q_grandchild = shlex.quote(str(grandchild_pid_file))
    script = f"""
set -eu
{helper}
MAIN_SHELL_PID=$$
(
  readonly WATCHDOG_SELF_PID="$BASHPID"
  while [ ! -s {q_workload} ]; do sleep 0.01; done
  terminate_main_for_deadline "$WATCHDOG_SELF_PID"
  printf 'watchdog-survived\n' > {q_marker}
) &
(
  sleep 30 &
  printf '%s\n' "$!" > {q_grandchild}
  printf '%s\n' "$BASHPID" > {q_workload}
  wait
) &
wait
"""
    proc = subprocess.Popen(["bash", "-c", script])
    try:
        proc.wait(timeout=5)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not marker.exists():
            time.sleep(0.02)
        assert proc.returncode == -signal.SIGKILL
        assert marker.read_text(encoding="utf-8") == "watchdog-survived\n"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)
        for pid_file in (workload_pid_file, grandchild_pid_file):
            if not pid_file.exists():
                continue
            try:
                os.kill(int(pid_file.read_text(encoding="utf-8").strip()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass

def test_entry_enables_watchdog_before_the_first_download() -> None:
    text = (RUN_DIR / "run9_success_pod_entry.sh").read_text(encoding="utf-8")
    assert text.index("trap on_exit EXIT") < text.index("PYTHON_TGZ_URL=")
    assert text.index("WATCHDOG_PID=$!") < text.index("PYTHON_TGZ_URL=")


def test_entry_preflight_falls_back_to_seccomp_probe() -> None:
    text = (RUN_DIR / "run9_success_pod_entry.sh").read_text(encoding="utf-8")
    preflight = text[
        text.index('stage "preflight-system"') : text.index('stage "python-3.11.15"')
    ]
    assert (
        'elif python3 "$BOOTSTRAP_RUN_DIR/run9_seccomp_prelude.py" --probe 2>/dev/null; then'
        in preflight
    )
    assert 'NETWORK_ISOLATION_MODE="seccomp"' in preflight
    assert preflight.index("unshare -rn true") < preflight.index(
        'python3 "$BOOTSTRAP_RUN_DIR/run9_seccomp_prelude.py" --probe'
    )
    assert (
        "no usable network isolation mechanism (iptables needs NET_ADMIN; "
        "unshare -n/-rn and seccomp filter denied)" in preflight
    )


def test_entry_render_isolation_case_covers_seccomp() -> None:
    text = (RUN_DIR / "run9_success_pod_entry.sh").read_text(encoding="utf-8")
    isolation_case = text[
        text.index('case "$NETWORK_ISOLATION_MODE" in') : text.index("esac")
    ]
    assert "seccomp)" in isolation_case
    assert (
        'ISOLATION_PREFIX=("$VENV_RENDER/bin/python" "$RUN_DIR/run9_seccomp_prelude.py" "--exec")'
        in isolation_case
    )
    assert isolation_case.index("seccomp)") < isolation_case.index("*)")


def test_entry_admission_invocation_passes_network_isolation_mode() -> None:
    text = (RUN_DIR / "run9_success_pod_entry.sh").read_text(encoding="utf-8")
    admission_call = text[
        text.index('stage "success-only-admission"') : text.index("ADMISSION_EC=$?")
    ]
    assert '--network-isolation-mode "$NETWORK_ISOLATION_MODE"' in admission_call


def test_entry_isolates_measurement_network_before_admission() -> None:
    text = (RUN_DIR / "run9_success_pod_entry.sh").read_text(encoding="utf-8")
    isolation = text[text.index('stage "render-network-isolation"') :]
    assert isolation.index("iptables -P OUTPUT DROP") < isolation.index(
        '"$VENV_RENDER/bin/python" "$RUN_DIR/run9_success_admission.py"'
    )
    assert isolation.index("iptables -A OUTPUT -o lo -j ACCEPT") < isolation.index(
        "iptables -P OUTPUT DROP"
    )
    assert isolation.index("iptables -A OUTPUT -j REJECT") < isolation.index(
        "iptables -P OUTPUT DROP"
    )
    assert isolation.index("iptables -P OUTPUT DROP") < isolation.index(
        'FIREWALL_ACTIVE="true"'
    )
    assert "ORT_TELEMETRY_DISABLED=1" in text
    assert "restore_network" in text


def test_entry_never_appends_unmanifested_files_to_success_bundle() -> None:
    text = (RUN_DIR / "run9_success_pod_entry.sh").read_text(encoding="utf-8")
    success_tail = text[text.index('if [ "$ADMISSION_EC" -eq 0 ]'):]
    assert 'cp "$WORK/measurement_environment.freeze" "$RESULT_DIR' not in success_tail


def test_entry_rejection_has_no_registration_directory() -> None:
    text = (RUN_DIR / "run9_success_pod_entry.sh").read_text(encoding="utf-8")
    assert '[ ! -e "$RESULT_DIR" ] || die "rejected artifact created a registration directory"' in text
    assert "artifact-rejected-no-registration" in text
    assert "candidate_sha" not in text.lower()


def test_prepare_never_calls_runpod_api(monkeypatch: pytest.MonkeyPatch) -> None:
    report: dict[str, Any] = {
        "ready": True,
        "pod_created": False,
        "payload": runner.build_launch_payload(COMMIT),
    }
    monkeypatch.setattr(runner, "verify_prelaunch", lambda: report)

    def forbidden(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("prepare must not call the RunPod API")

    monkeypatch.setattr(runner, "_request_json", forbidden)
    assert runner.main(["prepare"]) == 0


def test_launch_requires_exact_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "not-printed")
    monkeypatch.setattr(
        runner,
        "verify_prelaunch",
        lambda: {
            "source_commit": COMMIT,
            "payload_sha256": "b" * 64,
            "payload": runner.build_launch_payload(COMMIT),
        },
    )

    def forbidden(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("unconfirmed launch must not call the RunPod API")

    monkeypatch.setattr(runner, "_request_json", forbidden)
    assert runner.main(["launch"]) == 2
    assert runner.main(["launch", "--confirm-launch", "wrong"]) == 2


def test_confirmed_launch_performs_exactly_one_post(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []
    payload = runner.build_launch_payload(COMMIT)
    monkeypatch.setenv("RUNPOD_API_KEY", "not-printed")
    monkeypatch.setattr(
        runner,
        "verify_prelaunch",
        lambda: {
            "source_commit": COMMIT,
            "payload_sha256": "b" * 64,
            "payload": payload,
        },
    )

    def record(
        method: str,
        url: str,
        *,
        api_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        assert api_key == "not-printed"
        calls.append((method, url, payload))
        return {"id": "pod-id"}

    monkeypatch.setattr(runner, "_request_json", record)
    assert runner.main(["launch", "--confirm-launch", runner.CONFIRMATION]) == 0
    assert calls == [("POST", runner.CREATE_POD_URL, payload)]


def test_launch_rejects_success_response_without_pod_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = runner.build_launch_payload(COMMIT)
    monkeypatch.setenv("RUNPOD_API_KEY", "not-printed")
    monkeypatch.setattr(
        runner,
        "verify_prelaunch",
        lambda: {
            "source_commit": COMMIT,
            "payload_sha256": "b" * 64,
            "payload": payload,
        },
    )
    monkeypatch.setattr(runner, "_request_json", lambda *args, **kwargs: {"status": "created"})
    assert runner.main(["launch", "--confirm-launch", runner.CONFIRMATION]) == 2
    captured = capsys.readouterr()
    assert '"pod_id": null' in captured.out
    assert "billable Pod may exist" in captured.err


def test_launch_reconciles_ambiguous_post_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = runner.build_launch_payload(COMMIT)
    monkeypatch.setenv("RUNPOD_API_KEY", "not-printed")
    monkeypatch.setattr(
        runner,
        "verify_prelaunch",
        lambda: {
            "source_commit": COMMIT,
            "payload_sha256": "b" * 64,
            "payload": payload,
        },
    )
    monkeypatch.setattr(
        runner,
        "_request_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(runner.AmbiguousLaunchError("timeout")),
    )
    assert runner.main(["launch", "--confirm-launch", runner.CONFIRMATION]) == 2
    captured = capsys.readouterr()
    assert '"launch_status": "AMBIGUOUS_POST"' in captured.out
    assert '"pod_id": null' in captured.out
    assert "billable Pod may exist" in captured.err
    assert "reconcile in the RunPod console immediately" in captured.err


def test_request_json_classifies_http_5xx_as_ambiguous_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ErrorBody:
        def read(self) -> bytes:
            return b"temporary server failure"

    def server_error(*args: Any, **kwargs: Any) -> Any:
        raise runner.urllib.error.HTTPError(
            runner.CREATE_POD_URL,
            503,
            "Service Unavailable",
            hdrs=None,
            fp=ErrorBody(),
        )

    monkeypatch.setattr(runner.urllib.request, "urlopen", server_error)
    with pytest.raises(runner.AmbiguousLaunchError, match="server error 503"):
        runner._request_json(
            "POST", runner.CREATE_POD_URL, api_key="not-printed", payload={"x": 1}
        )


def test_request_json_classifies_5xx_body_read_timeout_as_ambiguous_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ErrorBody:
        def read(self) -> bytes:
            raise TimeoutError("error body stalled")

        def close(self) -> None:
            return None

    def server_error(*args: Any, **kwargs: Any) -> Any:
        raise runner.urllib.error.HTTPError(
            runner.CREATE_POD_URL,
            503,
            "Service Unavailable",
            hdrs=None,
            fp=ErrorBody(),
        )

    monkeypatch.setattr(runner.urllib.request, "urlopen", server_error)
    with pytest.raises(runner.AmbiguousLaunchError, match="server error 503"):
        runner._request_json(
            "POST", runner.CREATE_POD_URL, api_key="not-printed", payload={"x": 1}
        )


def test_request_json_keeps_http_4xx_as_definite_api_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ErrorBody:
        def read(self) -> bytes:
            return b"bad request"

    def client_error(*args: Any, **kwargs: Any) -> Any:
        raise runner.urllib.error.HTTPError(
            runner.CREATE_POD_URL,
            400,
            "Bad Request",
            hdrs=None,
            fp=ErrorBody(),
        )

    monkeypatch.setattr(runner.urllib.request, "urlopen", client_error)
    with pytest.raises(RuntimeError, match="RunPod API 400") as exc_info:
        runner._request_json(
            "POST", runner.CREATE_POD_URL, api_key="not-printed", payload={"x": 1}
        )
    assert type(exc_info.value) is RuntimeError


def test_request_json_classifies_timeout_as_ambiguous_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(*args: Any, **kwargs: Any) -> Any:
        raise TimeoutError("response lost")

    monkeypatch.setattr(runner.urllib.request, "urlopen", timeout)
    with pytest.raises(runner.AmbiguousLaunchError, match="transport failed"):
        runner._request_json(
            "POST", runner.CREATE_POD_URL, api_key="not-printed", payload={"x": 1}
        )


def test_request_json_classifies_truncated_2xx_body_as_ambiguous_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def read(self) -> bytes:
            raise runner.http.client.IncompleteRead(b'{"id": "partial', 30)

    monkeypatch.setattr(runner.urllib.request, "urlopen", lambda *args, **kwargs: Response())
    with pytest.raises(runner.AmbiguousLaunchError, match="HTTP response failed.*IncompleteRead"):
        runner._request_json(
            "POST", runner.CREATE_POD_URL, api_key="not-printed", payload={"x": 1}
        )


def test_request_json_classifies_malformed_json_as_ambiguous_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def read(self) -> bytes:
            return b"{not-json"

    monkeypatch.setattr(runner.urllib.request, "urlopen", lambda *args, **kwargs: Response())
    with pytest.raises(runner.AmbiguousLaunchError, match="could not be decoded as JSON"):
        runner._request_json(
            "POST", runner.CREATE_POD_URL, api_key="not-printed", payload={"x": 1}
        )


def test_preflight_rejects_dirty_tree_before_remote_check(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for relative in (runner.ENTRY_RELATIVE, runner.POLICY_RELATIVE, runner.ADMISSION_RELATIVE):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative == runner.POLICY_RELATIVE:
            path.write_text(
                json.dumps(
                    {
                        "admission": {
                            "decision": "PASS_ONLY",
                            "failure_registry_effect": "NONE",
                        },
                        "evaluator_boundary": {
                            "forbidden_inputs": ["candidate_artifact_sha256"]
                        },
                    }
                ),
                encoding="utf-8",
            )
        else:
            path.write_text("#!/bin/bash\n", encoding="utf-8")

    values = iter([COMMIT, " M changed.py"])
    monkeypatch.setattr(runner, "_git", lambda *args, **kwargs: next(values))
    with pytest.raises(runner.PreflightError, match="working tree is not clean"):
        runner.verify_prelaunch(tmp_path)


def test_entry_shell_syntax() -> None:
    subprocess.run(["bash", "-n", str(RUN_DIR / "run9_success_pod_entry.sh")], check=True)
