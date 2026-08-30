from __future__ import annotations

import json
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
    assert 'fetch --depth 1 origin "$RUN9_PIN_COMMIT"' in command
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
    watchdog = text[text.index('(\n  sleep "$WALL_CLOCK_SECONDS"'): text.index('WATCHDOG_PID=$!')]
    assert watchdog.index("force_restore_network_for_stop") < watchdog.index("self_stop || true")
    assert "self_stop || true" in watchdog
    assert 'runpodctl stop pod "$RUNPOD_POD_ID" || true' not in watchdog
    assert "for attempt in 1 2 3 4 5" in text


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
    assert '$BOOTSTRAP_RUN_DIR/inputs/measurement_native_manifest.txt' in preflight
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
    assert "measurement_native_manifest.txt" in preflight
    assert "--allow-downgrades" in preflight
    assert "dpkg-query -W" in preflight
    assert "cmp -s" in preflight
    install_rows = [
        line for line in (RUN_DIR / "inputs" / "measurement_native_install_lock.txt").read_text(encoding="utf-8").splitlines() if line
    ]
    manifest_rows = [
        line for line in (RUN_DIR / "inputs" / "measurement_native_manifest.txt").read_text(encoding="utf-8").splitlines() if line
    ]
    assert install_rows
    assert len(manifest_rows) > len(install_rows)
    assert all("=" in row for row in install_rows + manifest_rows)
    assert set(install_rows).issubset(set(manifest_rows))


def test_entry_enables_watchdog_before_the_first_download() -> None:
    text = (RUN_DIR / "run9_success_pod_entry.sh").read_text(encoding="utf-8")
    assert text.index("trap on_exit EXIT") < text.index("PYTHON_TGZ_URL=")
    assert text.index("WATCHDOG_PID=$!") < text.index("PYTHON_TGZ_URL=")


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
