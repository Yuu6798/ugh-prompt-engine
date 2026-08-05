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

import pytest
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
# Crash-family negatives (D-L0a-5, PR #246 Codex P2 review round 2): non-
# positive physical.bpm / structure[].bars / physical.time_signature values
# empirically confirmed to crash svp_rpe.perform.performer.perform() with an
# uncaught exception rather than fail gracefully (see
# docs/l0a_authoring_contract.md (b) for the measured exception per case).
# ---------------------------------------------------------------------------


def test_bpm_zero_is_rejected(tmp_path: Path):
    def mutate(data: dict[str, Any]) -> None:
        data["physical"]["bpm"] = 0

    errors = _fail_errors(tmp_path, mutate)
    matches = [e for e in errors if e["where"] == "physical.bpm" and e["kind"] == "range"]
    assert matches, errors


def test_bpm_negative_is_rejected(tmp_path: Path):
    def mutate(data: dict[str, Any]) -> None:
        data["physical"]["bpm"] = -60

    errors = _fail_errors(tmp_path, mutate)
    matches = [e for e in errors if e["where"] == "physical.bpm" and e["kind"] == "range"]
    assert matches, errors


def test_bars_zero_is_rejected(tmp_path: Path):
    def mutate(data: dict[str, Any]) -> None:
        data["structure"][0]["bars"] = 0

    errors = _fail_errors(tmp_path, mutate)
    matches = [e for e in errors if e["where"] == "structure[0].bars" and e["kind"] == "range"]
    assert matches, errors


def test_bars_negative_is_rejected(tmp_path: Path):
    def mutate(data: dict[str, Any]) -> None:
        data["structure"][0]["bars"] = -4

    errors = _fail_errors(tmp_path, mutate)
    matches = [e for e in errors if e["where"] == "structure[0].bars" and e["kind"] == "range"]
    assert matches, errors


def test_time_signature_zero_numerator_is_rejected(tmp_path: Path):
    def mutate(data: dict[str, Any]) -> None:
        data["physical"]["time_signature"] = "0/4"

    errors = _fail_errors(tmp_path, mutate)
    matches = [
        e for e in errors if e["where"] == "physical.time_signature" and e["kind"] == "format"
    ]
    assert matches, errors


def test_time_signature_zero_denominator_is_rejected(tmp_path: Path):
    """The denominator is never parsed anywhere in the deterministic
    pipeline (perform() only reads the numerator) — this is a graceful,
    non-crash case per the empirical sweep. It is still excluded by the
    narrowed format regex as a defensive sanity constraint, not a
    crash-family classification (boundary declaration, docs (b))."""

    def mutate(data: dict[str, Any]) -> None:
        data["physical"]["time_signature"] = "4/0"

    errors = _fail_errors(tmp_path, mutate)
    matches = [
        e for e in errors if e["where"] == "physical.time_signature" and e["kind"] == "format"
    ]
    assert matches, errors


def test_empty_structure_is_rejected(tmp_path: Path):
    """PR #246 Codex P2 review 4 巡目 B: `structure: []` previously passed
    both the public-scope check (per-element loop runs 0 times) and
    canonical `CompositionScore` (no min-length constraint there), then
    crashed `perform()` with `ValueError('perform() requires at least one
    structure section')` — confirmed via direct execution. Now gated as a
    container-size (`range`) violation at `structure` itself."""

    def mutate(data: dict[str, Any]) -> None:
        data["structure"] = []

    errors = _fail_errors(tmp_path, mutate)
    matches = [e for e in errors if e["where"] == "structure" and e["kind"] == "range"]
    assert matches, errors


def test_empty_chord_progression_still_passes(tmp_path: Path):
    """Boundary declaration (PR #246 review round 4 B): `events.
    chord_progression: []` is confirmed non-crashing (perform() falls back
    to the key-derived default progression, the same path an omitted
    `events` block takes) — deliberately left ungated, unlike `structure: []`."""

    data = _base_score()
    data["events"] = {"chord_progression": []}
    score_path = _write_score(tmp_path, data)
    result = _invoke(str(score_path), "--contract", str(CONTRACT_PATH), "--format", "json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"status": "pass"}


def test_positive_bpm_and_bars_still_pass():
    """Regression guard: the min=1 gate must not reject the positive control's
    already-valid positive bpm/bars values."""

    result = _invoke(str(POSITIVE_CONTROL_PATH), "--contract", str(CONTRACT_PATH), "--format", "json")
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    "round_score_path",
    sorted(REPO_ROOT.glob("examples/l0s_spike/rounds/round*/score.yaml")),
    ids=lambda p: p.parent.name,
)
def test_historical_l0s_round_scores_still_pass(round_score_path: Path):
    """The 5 L0-s historical round scores (frozen evidence) all declare
    positive bpm/bars and a well-formed time_signature — the new min=1/
    narrowed-format gate must not regress them (PR #246 review round 2)."""

    result = _invoke(str(round_score_path), "--contract", str(CONTRACT_PATH), "--format", "json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"status": "pass"}


# ---------------------------------------------------------------------------
# Exit codes / operational errors
# ---------------------------------------------------------------------------


def test_missing_score_file_exits_2(tmp_path: Path):
    result = _invoke(str(tmp_path / "does-not-exist.yaml"))
    assert result.exit_code == 2, result.output


def test_missing_contract_file_exits_2(tmp_path: Path):
    result = _invoke(str(POSITIVE_CONTROL_PATH), "--contract", str(tmp_path / "no-spec.yaml"))
    assert result.exit_code == 2, result.output


def test_empty_yaml_contract_exits_2(tmp_path: Path):
    """`--contract` pointing at an empty YAML document (parses to `None`, not
    a mapping) must be an operational error, not an uncaught `ValueError`
    escaping `contract.py`'s `_load_yaml_mapping` (Codex P2 review, PR #246)."""

    empty_contract = tmp_path / "empty-contract.yaml"
    empty_contract.write_text("", encoding="utf-8")
    result = _invoke(str(POSITIVE_CONTROL_PATH), "--contract", str(empty_contract))
    assert result.exit_code == 2, result.output


def test_scalar_yaml_contract_exits_2(tmp_path: Path):
    scalar_contract = tmp_path / "scalar-contract.yaml"
    scalar_contract.write_text("just a string\n", encoding="utf-8")
    result = _invoke(str(POSITIVE_CONTROL_PATH), "--contract", str(scalar_contract))
    assert result.exit_code == 2, result.output


def test_list_yaml_contract_exits_2(tmp_path: Path):
    list_contract = tmp_path / "list-contract.yaml"
    list_contract.write_text("- 1\n- 2\n", encoding="utf-8")
    result = _invoke(str(POSITIVE_CONTROL_PATH), "--contract", str(list_contract))
    assert result.exit_code == 2, result.output


_MINIMAL_CONTRACT_SKELETON: dict[str, Any] = {
    "schema_version": "authoring-contract/1.0",
    "top_level": {"allowed_keys": ["meta"]},
    "semantic": {"allowed_keys": []},
    "grv": {"allowed_keys": []},
    "delta_e": {"allowed_keys": []},
    "physical": {"allowed_keys": []},
    "structure_section": {"allowed_keys": []},
    "rendering": {"allowed_keys": []},
    "events": {"allowed_keys": []},
    "chord": {"allowed_keys": []},
}


def _write_contract(tmp_path: Path, meta_title_field: dict[str, Any]) -> Path:
    contract = dict(_MINIMAL_CONTRACT_SKELETON)
    contract["meta"] = {"allowed_keys": ["title"], "fields": {"title": meta_title_field}}
    path = tmp_path / "contract.yaml"
    path.write_text(yaml.safe_dump(contract, sort_keys=False), encoding="utf-8")
    return path


def test_invalid_regex_format_contract_exits_2(tmp_path: Path):
    """PR #246 Codex P2 review 3 巡目: `format: '['`（不正な正規表現）を
    含む `--contract` spec は、後段 `re.fullmatch` の `re.error` ではなく
    spec ロード時の `ValidationError` → exit 2 として拒否される。"""

    contract_path = _write_contract(tmp_path, {"type": "str", "format": "["})
    result = _invoke(str(POSITIVE_CONTROL_PATH), "--contract", str(contract_path))
    assert result.exit_code == 2, result.output


def test_format_on_int_field_contract_exits_2(tmp_path: Path):
    """PR #246 Codex P2 review 3 巡目: `format` を `type: int` フィールドへ
    付与した spec は、実値へ `re.fullmatch` を呼んで `TypeError` になる前に
    spec ロード時の `ValidationError` → exit 2 として拒否される。"""

    contract_path = _write_contract(tmp_path, {"type": "int", "format": "^[0-9]+$"})
    result = _invoke(str(POSITIVE_CONTROL_PATH), "--contract", str(contract_path))
    assert result.exit_code == 2, result.output


def test_fields_key_not_in_allowed_keys_contract_exits_2(tmp_path: Path):
    """PR #246 Codex P2 review 6 巡目: a `fields` entry whose key is a typo
    of an `allowed_keys` entry (e.g. `bpn` for `bpm`) previously loaded
    silently — the constraint was declared but never applied, since
    `validate.py`'s `_object_errors` only iterates `spec.fields` without
    cross-checking `allowed_keys`. Now rejected at spec load time (exit 2),
    same posture as the type×constraint guards from round 3."""

    contract = dict(_MINIMAL_CONTRACT_SKELETON)
    contract["meta"] = {"allowed_keys": ["title"], "fields": {"titel": {"type": "str"}}}
    contract_path = tmp_path / "contract.yaml"
    contract_path.write_text(yaml.safe_dump(contract, sort_keys=False), encoding="utf-8")

    result = _invoke(str(POSITIVE_CONTROL_PATH), "--contract", str(contract_path))
    assert result.exit_code == 2, result.output


def test_invalid_score_yaml_parse_failure_still_exits_1_not_2(tmp_path: Path):
    """Asymmetry check (Codex P2 review, PR #246): an unparseable *score* is
    the artifact under test failing — `fail`/exit `1` — while an unparseable
    *contract* (above) is the instrument's own configuration being broken —
    an operational error, exit `2`. Confirms the score side keeps its
    existing exit-1 classification unchanged by this fix."""

    bad_path = tmp_path / "broken-score.yaml"
    bad_path.write_text("not: [valid yaml", encoding="utf-8")
    result = _invoke(str(bad_path), "--contract", str(CONTRACT_PATH), "--format", "json")
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "fail"


def test_output_collision_with_score_input_exits_2(tmp_path: Path):
    score_path = _write_score(tmp_path, _base_score())
    result = _invoke(str(score_path), "-o", str(score_path))
    assert result.exit_code == 2, result.output


def test_output_collision_with_contract_input_exits_2(tmp_path: Path):
    """`-o` resolving to the `--contract` spec path must be rejected too —
    the collision guard covers every input path this command reads, not
    only `score.yaml` (Codex P2 review, PR #246, same collision-family
    reasoning as PR #245)."""

    score_path = _write_score(tmp_path, _base_score())
    contract_copy = tmp_path / "contract.yaml"
    contract_copy.write_bytes(CONTRACT_PATH.read_bytes())
    before_bytes = contract_copy.read_bytes()

    result = _invoke(str(score_path), "--contract", str(contract_copy), "-o", str(contract_copy))
    assert result.exit_code == 2, result.output
    # nothing was written — the spec file this run read must be untouched.
    assert contract_copy.read_bytes() == before_bytes


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


def test_output_write_is_atomic_and_leaves_no_stray_tempfile(tmp_path: Path):
    """PR #246 Codex P2 review 8 巡目 C: `-o` now writes via
    `svp_rpe.utils.atomic_io.atomic_write_bytes` (tempfile + `os.replace`)
    instead of a direct `write_bytes` — confirms the output directory only
    ever contains the final report (no leaked `.tmp` staging file) and that
    the bytes match `dump_json_bytes`'s own byte-deterministic output
    exactly (the atomic writer must not alter content, only the write
    mechanism)."""

    from svp_rpe.authoring.contract import load_authoring_contract
    from svp_rpe.authoring.report import dump_json_bytes
    from svp_rpe.authoring.validate import validate_score

    score_path = _write_score(tmp_path, _base_score())
    out_path = tmp_path / "out.json"

    result = _invoke(str(score_path), "--contract", str(CONTRACT_PATH), "-o", str(out_path))
    assert result.exit_code == 0, result.output

    expected = dump_json_bytes(validate_score(_base_score(), load_authoring_contract(CONTRACT_PATH)))
    assert out_path.read_bytes() == expected

    remaining = {p.name for p in tmp_path.iterdir()}
    assert remaining == {score_path.name, out_path.name}, remaining


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
