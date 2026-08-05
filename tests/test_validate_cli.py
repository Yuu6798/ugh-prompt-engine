"""tests/test_validate_cli.py — `svprpe validate` CLI E2E (D-L0a-2, negative battery D-L0a-5).

Negative battery coverage (one test per L0-s 9-round hardened rule, each
firing exactly the intended `{where, kind}`):

| rule                                   | where                                | kind          |
|-----------------------------------------|---------------------------------------|---------------|
| fixity mixed in                        | fixity                               | public_scope  |
| semantic.lyrics_presence               | semantic.lyrics_presence             | public_scope  |
| bpm "96" (digit string)                | physical.bpm                         | type          |
| brightness "murky"                     | physical.brightness                  | enum          |
| structure extra key                    | structure[0].<extra>                 | public_scope  |
| rendering.target_backend "suno"        | rendering.target_backend             | literal       |
| physical.key "D dorian"                | physical.key                         | format        |
| physical.time_signature "waltz"        | physical.time_signature              | format        |
| chord root "Bb"                        | events.chord_progression[0].root     | enum          |

All fixture scores are fake/synthetic YAML text — no audio processing.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import yaml
from typer.testing import CliRunner

from svp_rpe.cli import app

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / "config" / "authoring_contract_l0.yaml"
POSITIVE_CONTROL_PATH = REPO_ROOT / "examples" / "l0s_spike" / "positive_control" / "score.yaml"

runner = CliRunner()


def _base_score() -> dict[str, Any]:
    return copy.deepcopy(yaml.safe_load(POSITIVE_CONTROL_PATH.read_text(encoding="utf-8")))


def _write_score(tmp_path: Path, data: dict[str, Any], *, name: str = "score.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _invoke(*args: str) -> Any:
    return runner.invoke(app, ["validate", *args])


# ---------------------------------------------------------------------------
# Positive path
# ---------------------------------------------------------------------------


def test_positive_control_passes_with_contract():
    result = _invoke(str(POSITIVE_CONTROL_PATH), "--contract", str(CONTRACT_PATH))
    assert result.exit_code == 0, result.output
    assert "status=pass" in result.output


def test_positive_control_passes_canonical_only(tmp_path: Path):
    """Omitting --contract skips the public-scope check (canonical-only)."""
    result = _invoke(str(POSITIVE_CONTROL_PATH))
    assert result.exit_code == 0, result.output


def test_json_format_pass():
    result = _invoke(str(POSITIVE_CONTROL_PATH), "--contract", str(CONTRACT_PATH), "--format", "json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload == {"status": "pass"}


# ---------------------------------------------------------------------------
# Negative battery (D-L0a-5): one L0-s hardened rule each
# ---------------------------------------------------------------------------


def _fail_errors(tmp_path: Path, mutate) -> list[dict[str, str]]:
    data = _base_score()
    mutate(data)
    score_path = _write_score(tmp_path, data)
    result = _invoke(str(score_path), "--contract", str(CONTRACT_PATH), "--format", "json")
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "fail"
    return payload["errors"]


def test_fixity_mixed_in_is_rejected(tmp_path: Path):
    def mutate(data: dict[str, Any]) -> None:
        data["fixity"] = {
            "bpm": "locked",
            "key": "locked",
            "time_signature": "locked",
            "active_rate_target": "locked",
            "valley_depth_target": "locked",
            "brightness": "locked",
            "stereo_width": "locked",
        }

    errors = _fail_errors(tmp_path, mutate)
    matches = [e for e in errors if e["where"] == "fixity" and e["kind"] == "public_scope"]
    assert matches, errors


def test_lyrics_presence_is_rejected(tmp_path: Path):
    def mutate(data: dict[str, Any]) -> None:
        data["semantic"]["lyrics_presence"] = "present"

    errors = _fail_errors(tmp_path, mutate)
    matches = [
        e for e in errors if e["where"] == "semantic.lyrics_presence" and e["kind"] == "public_scope"
    ]
    assert matches, errors


def test_bpm_digit_string_is_rejected(tmp_path: Path):
    def mutate(data: dict[str, Any]) -> None:
        data["physical"]["bpm"] = "96"

    errors = _fail_errors(tmp_path, mutate)
    matches = [e for e in errors if e["where"] == "physical.bpm" and e["kind"] == "type"]
    assert matches, errors


def test_brightness_murky_is_rejected(tmp_path: Path):
    def mutate(data: dict[str, Any]) -> None:
        data["physical"]["brightness"] = "murky"

    errors = _fail_errors(tmp_path, mutate)
    matches = [e for e in errors if e["where"] == "physical.brightness" and e["kind"] == "enum"]
    assert matches, errors


def test_structure_extra_key_is_rejected(tmp_path: Path):
    def mutate(data: dict[str, Any]) -> None:
        data["structure"][0]["extra_field"] = "not allowed"

    errors = _fail_errors(tmp_path, mutate)
    matches = [
        e for e in errors if e["where"] == "structure[0].extra_field" and e["kind"] == "public_scope"
    ]
    assert matches, errors


def test_target_backend_suno_is_rejected(tmp_path: Path):
    def mutate(data: dict[str, Any]) -> None:
        data["rendering"]["target_backend"] = "suno"

    errors = _fail_errors(tmp_path, mutate)
    matches = [
        e for e in errors if e["where"] == "rendering.target_backend" and e["kind"] == "literal"
    ]
    assert matches, errors


def test_target_backend_musicgen_is_rejected(tmp_path: Path):
    def mutate(data: dict[str, Any]) -> None:
        data["rendering"]["target_backend"] = "musicgen"

    errors = _fail_errors(tmp_path, mutate)
    matches = [
        e for e in errors if e["where"] == "rendering.target_backend" and e["kind"] == "literal"
    ]
    assert matches, errors


def test_key_format_dorian_is_rejected(tmp_path: Path):
    def mutate(data: dict[str, Any]) -> None:
        data["physical"]["key"] = "D dorian"

    errors = _fail_errors(tmp_path, mutate)
    matches = [e for e in errors if e["where"] == "physical.key" and e["kind"] == "format"]
    assert matches, errors


def test_time_signature_format_waltz_is_rejected(tmp_path: Path):
    def mutate(data: dict[str, Any]) -> None:
        data["physical"]["time_signature"] = "waltz"

    errors = _fail_errors(tmp_path, mutate)
    matches = [
        e for e in errors if e["where"] == "physical.time_signature" and e["kind"] == "format"
    ]
    assert matches, errors


def test_chord_root_flat_bb_is_rejected(tmp_path: Path):
    def mutate(data: dict[str, Any]) -> None:
        data["events"] = {"chord_progression": [{"root": "Bb", "quality": "major"}]}

    errors = _fail_errors(tmp_path, mutate)
    matches = [
        e
        for e in errors
        if e["where"] == "events.chord_progression[0].root" and e["kind"] == "enum"
    ]
    assert matches, errors


# ---------------------------------------------------------------------------
# Exit codes / operational errors
# ---------------------------------------------------------------------------


def test_missing_score_file_exits_2(tmp_path: Path):
    result = _invoke(str(tmp_path / "does-not-exist.yaml"))
    assert result.exit_code == 2, result.output


def test_missing_contract_file_exits_2(tmp_path: Path):
    result = _invoke(str(POSITIVE_CONTROL_PATH), "--contract", str(tmp_path / "no-spec.yaml"))
    assert result.exit_code == 2, result.output


def test_output_collision_with_score_input_exits_2(tmp_path: Path):
    score_path = _write_score(tmp_path, _base_score())
    result = _invoke(str(score_path), "-o", str(score_path))
    assert result.exit_code == 2, result.output


def test_yaml_parse_failure_is_fail_not_operational_error(tmp_path: Path):
    bad_path = tmp_path / "broken.yaml"
    bad_path.write_text("not: [valid yaml", encoding="utf-8")
    result = _invoke(str(bad_path), "--format", "json")
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "fail"
    assert payload["errors"][0]["where"] == "<file>"
    assert payload["errors"][0]["kind"] == "canonical"


def test_non_mapping_score_is_fail(tmp_path: Path):
    bad_path = tmp_path / "list.yaml"
    bad_path.write_text("- 1\n- 2\n", encoding="utf-8")
    result = _invoke(str(bad_path), "--format", "json")
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "fail"


def test_invalid_format_option_rejected():
    result = _invoke(str(POSITIVE_CONTROL_PATH), "--format", "yaml")
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Byte determinism / -o output
# ---------------------------------------------------------------------------


def test_output_file_is_written_and_byte_deterministic_across_runs(tmp_path: Path):
    score_path = _write_score(tmp_path, _base_score())
    out1 = tmp_path / "out1.json"
    out2 = tmp_path / "out2.json"

    result1 = _invoke(str(score_path), "--contract", str(CONTRACT_PATH), "-o", str(out1))
    result2 = _invoke(str(score_path), "--contract", str(CONTRACT_PATH), "-o", str(out2))
    assert result1.exit_code == 0, result1.output
    assert result2.exit_code == 0, result2.output
    assert out1.read_bytes() == out2.read_bytes()
    assert out1.read_bytes().endswith(b"\n")


def test_output_json_matches_stdout_json(tmp_path: Path):
    score_path = _write_score(tmp_path, _base_score())
    out_path = tmp_path / "out.json"
    result = _invoke(
        str(score_path), "--contract", str(CONTRACT_PATH), "--format", "json", "-o", str(out_path)
    )
    assert result.exit_code == 0, result.output
    assert result.output.encode("utf-8") == out_path.read_bytes()


def test_output_json_is_sorted_and_well_formed(tmp_path: Path):
    def mutate(data: dict[str, Any]) -> None:
        data["physical"]["brightness"] = "murky"
        data["physical"]["bpm"] = "96"

    data = _base_score()
    mutate(data)
    score_path = _write_score(tmp_path, data)
    out_path = tmp_path / "out.json"
    result = _invoke(str(score_path), "--contract", str(CONTRACT_PATH), "-o", str(out_path))
    assert result.exit_code == 1, result.output
    payload = json.loads(out_path.read_bytes().decode("utf-8"))
    assert payload["status"] == "fail"
    kinds = {(e["where"], e["kind"]) for e in payload["errors"]}
    assert ("physical.bpm", "type") in kinds
    assert ("physical.brightness", "enum") in kinds
    # deterministic sort: errors sorted by (where, message)
    wheres = [e["where"] for e in payload["errors"]]
    assert wheres == sorted(wheres)


def test_text_format_lists_errors_in_table(tmp_path: Path):
    def mutate(data: dict[str, Any]) -> None:
        data["physical"]["brightness"] = "murky"

    data = _base_score()
    mutate(data)
    score_path = _write_score(tmp_path, data)
    result = _invoke(str(score_path), "--contract", str(CONTRACT_PATH))
    assert result.exit_code == 1, result.output
    assert "physical.brightness" in result.output
    assert "enum" in result.output


def test_no_output_file_written_without_o(tmp_path: Path):
    score_path = _write_score(tmp_path, _base_score())
    before = set(tmp_path.iterdir())
    result = _invoke(str(score_path), "--contract", str(CONTRACT_PATH))
    assert result.exit_code == 0, result.output
    after = set(tmp_path.iterdir())
    assert before == after
