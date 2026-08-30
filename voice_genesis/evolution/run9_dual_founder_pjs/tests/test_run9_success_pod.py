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
    assert "run9_success_pod_entry.sh" in command


def test_checked_in_policy_passes_runner_preflight_shape_check() -> None:
    runner._validate_policy(RUN_DIR / "inputs" / "success_only_admission_policy.json")


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
