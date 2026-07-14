"""`svprpe arrange` CLI と provenance bundle のテスト (AR1-2)。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from svp_rpe.arrange import ArrangementSpec, resolve_arrangement
from svp_rpe.cli import app
from svp_rpe.compose import load_composition_score

SAMPLE_PATH = Path("examples/composition/midnight_signal/composition_score.yaml")


def _spec_dict(
    target: dict[str, Any],
    score_fields: dict[str, str],
    *,
    arrangement_id: str = "arr-test",
    version: str = "1",
) -> dict[str, Any]:
    return {
        "meta": {"id": arrangement_id, "version": version},
        "target": target,
        "preservation": {"score_fields": score_fields},
    }


def _write_spec(path: Path, spec_dict: dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(spec_dict, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _elastic_bpm_spec_dict() -> dict[str, Any]:
    return _spec_dict(
        target={"physical": {"bpm": 140, "brightness": "bright"}},
        score_fields={"physical.bpm": "elastic", "physical.brightness": "free"},
    )


# --- happy path -----------------------------------------------------------------


def test_arrange_cli_succeeds_and_writes_exactly_three_files(tmp_path: Path) -> None:
    spec_path = tmp_path / "arrangement.yaml"
    _write_spec(spec_path, _elastic_bpm_spec_dict())
    output_dir = tmp_path / "out"

    result = CliRunner().invoke(
        app,
        ["arrange", str(SAMPLE_PATH), str(spec_path), "--output-dir", str(output_dir)],
    )

    assert result.exit_code == 0, result.output
    produced = sorted(p.name for p in output_dir.iterdir())
    assert produced == ["arrangement_bundle.json", "arrangement_diff.json", "derived_score.yaml"]
    assert "Derived score saved" in result.stdout
    assert "Arrangement bundle saved" in result.stdout
    assert "Arrangement diff saved" in result.stdout


def test_derived_score_yaml_reloads_and_matches_resolution(tmp_path: Path) -> None:
    spec_path = tmp_path / "arrangement.yaml"
    spec_dict = _elastic_bpm_spec_dict()
    _write_spec(spec_path, spec_dict)
    output_dir = tmp_path / "out"

    result = CliRunner().invoke(
        app,
        ["arrange", str(SAMPLE_PATH), str(spec_path), "--output-dir", str(output_dir)],
    )
    assert result.exit_code == 0, result.output

    reloaded = load_composition_score(output_dir / "derived_score.yaml")

    source = load_composition_score(SAMPLE_PATH)
    spec = ArrangementSpec.model_validate(spec_dict)
    resolution = resolve_arrangement(source, spec)

    assert reloaded.model_dump(mode="json") == resolution.derived_score.model_dump(mode="json")


def test_bundle_sha256_and_paths_match_independent_recomputation(tmp_path: Path) -> None:
    spec_path = tmp_path / "arrangement.yaml"
    _write_spec(spec_path, _elastic_bpm_spec_dict())
    output_dir = tmp_path / "out"

    result = CliRunner().invoke(
        app,
        ["arrange", str(SAMPLE_PATH), str(spec_path), "--output-dir", str(output_dir)],
    )
    assert result.exit_code == 0, result.output

    bundle = json.loads((output_dir / "arrangement_bundle.json").read_text(encoding="utf-8"))

    assert bundle["schema_version"] == "arrangement-bundle/0.1"
    assert bundle["arrangement_id"] == "arr-test"
    assert bundle["source_score"]["path"] == str(SAMPLE_PATH)
    assert bundle["source_score"]["sha256"] == hashlib.sha256(
        SAMPLE_PATH.read_bytes()
    ).hexdigest()
    assert bundle["arrangement_spec"]["path"] == str(spec_path)
    assert bundle["arrangement_spec"]["sha256"] == hashlib.sha256(
        spec_path.read_bytes()
    ).hexdigest()
    assert bundle["outputs"] == {
        "derived_score": "derived_score.yaml",
        "arrangement_diff": "arrangement_diff.json",
    }
    # output_dir が bundle 内に埋め込まれていないこと（bare filename のみ）。
    assert str(output_dir) not in json.dumps(bundle)


def test_diff_and_bundle_changes_are_identical(tmp_path: Path) -> None:
    spec_path = tmp_path / "arrangement.yaml"
    _write_spec(spec_path, _elastic_bpm_spec_dict())
    output_dir = tmp_path / "out"

    result = CliRunner().invoke(
        app,
        ["arrange", str(SAMPLE_PATH), str(spec_path), "--output-dir", str(output_dir)],
    )
    assert result.exit_code == 0, result.output

    bundle = json.loads((output_dir / "arrangement_bundle.json").read_text(encoding="utf-8"))
    diff = json.loads((output_dir / "arrangement_diff.json").read_text(encoding="utf-8"))

    assert diff["schema_version"] == "arrangement-diff/0.1"
    assert diff["arrangement_id"] == "arr-test"
    assert diff["changes"] == bundle["changes"]
    paths = {change["path"] for change in diff["changes"]}
    assert paths == {"physical.bpm", "physical.brightness"}


def test_compose_accepts_derived_score(tmp_path: Path) -> None:
    spec_path = tmp_path / "arrangement.yaml"
    _write_spec(spec_path, _elastic_bpm_spec_dict())
    output_dir = tmp_path / "out"

    arrange_result = CliRunner().invoke(
        app,
        ["arrange", str(SAMPLE_PATH), str(spec_path), "--output-dir", str(output_dir)],
    )
    assert arrange_result.exit_code == 0, arrange_result.output

    compose_result = CliRunner().invoke(
        app, ["compose", str(output_dir / "derived_score.yaml")]
    )
    assert compose_result.exit_code == 0, compose_result.output


# --- determinism / non-mutation --------------------------------------------------


def test_two_runs_into_different_output_dirs_are_byte_identical(tmp_path: Path) -> None:
    spec_path = tmp_path / "arrangement.yaml"
    _write_spec(spec_path, _elastic_bpm_spec_dict())
    output_dir_1 = tmp_path / "out1"
    output_dir_2 = tmp_path / "out2"

    for output_dir in (output_dir_1, output_dir_2):
        result = CliRunner().invoke(
            app,
            ["arrange", str(SAMPLE_PATH), str(spec_path), "--output-dir", str(output_dir)],
        )
        assert result.exit_code == 0, result.output

    for filename in ("derived_score.yaml", "arrangement_bundle.json", "arrangement_diff.json"):
        first = (output_dir_1 / filename).read_bytes()
        second = (output_dir_2 / filename).read_bytes()
        assert first == second, filename


def test_input_files_are_not_modified(tmp_path: Path) -> None:
    spec_path = tmp_path / "arrangement.yaml"
    _write_spec(spec_path, _elastic_bpm_spec_dict())
    output_dir = tmp_path / "out"

    score_bytes_before = SAMPLE_PATH.read_bytes()
    spec_bytes_before = spec_path.read_bytes()

    result = CliRunner().invoke(
        app,
        ["arrange", str(SAMPLE_PATH), str(spec_path), "--output-dir", str(output_dir)],
    )
    assert result.exit_code == 0, result.output

    assert SAMPLE_PATH.read_bytes() == score_bytes_before
    assert spec_path.read_bytes() == spec_bytes_before


# --- error paths: exit 1, no partial artifacts -----------------------------------


def _assert_no_partial_artifacts(output_dir: Path) -> None:
    assert not output_dir.exists() or list(output_dir.iterdir()) == []


def test_hard_conflict_exits_1_with_field_path_and_no_partial_artifacts(
    tmp_path: Path,
) -> None:
    spec_path = tmp_path / "arrangement.yaml"
    _write_spec(
        spec_path,
        _spec_dict(
            target={"physical": {"bpm": 140}},
            score_fields={"physical.bpm": "hard"},
        ),
    )
    output_dir = tmp_path / "out"

    result = CliRunner().invoke(
        app,
        ["arrange", str(SAMPLE_PATH), str(spec_path), "--output-dir", str(output_dir)],
    )

    assert result.exit_code == 1
    assert "physical.bpm" in result.stderr
    _assert_no_partial_artifacts(output_dir)


def test_unknown_preservation_path_exits_1_with_no_partial_artifacts(tmp_path: Path) -> None:
    spec_path = tmp_path / "arrangement.yaml"
    _write_spec(
        spec_path,
        _spec_dict(target={}, score_fields={"physical.nonexistent": "hard"}),
    )
    output_dir = tmp_path / "out"

    result = CliRunner().invoke(
        app,
        ["arrange", str(SAMPLE_PATH), str(spec_path), "--output-dir", str(output_dir)],
    )

    assert result.exit_code == 1
    assert "physical.nonexistent" in result.stderr
    _assert_no_partial_artifacts(output_dir)


def test_unknown_top_level_key_in_spec_exits_1(tmp_path: Path) -> None:
    spec_path = tmp_path / "arrangement.yaml"
    spec_dict = _spec_dict(target={}, score_fields={})
    spec_dict["unexpected"] = "x"
    _write_spec(spec_path, spec_dict)
    output_dir = tmp_path / "out"

    result = CliRunner().invoke(
        app,
        ["arrange", str(SAMPLE_PATH), str(spec_path), "--output-dir", str(output_dir)],
    )

    assert result.exit_code == 1
    _assert_no_partial_artifacts(output_dir)


def test_non_mapping_spec_yaml_exits_1(tmp_path: Path) -> None:
    spec_path = tmp_path / "arrangement.yaml"
    spec_path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    output_dir = tmp_path / "out"

    result = CliRunner().invoke(
        app,
        ["arrange", str(SAMPLE_PATH), str(spec_path), "--output-dir", str(output_dir)],
    )

    assert result.exit_code == 1
    _assert_no_partial_artifacts(output_dir)


def test_invalid_yaml_spec_exits_1(tmp_path: Path) -> None:
    spec_path = tmp_path / "arrangement.yaml"
    spec_path.write_text("meta: [unterminated\n", encoding="utf-8")
    output_dir = tmp_path / "out"

    result = CliRunner().invoke(
        app,
        ["arrange", str(SAMPLE_PATH), str(spec_path), "--output-dir", str(output_dir)],
    )

    assert result.exit_code == 1
    _assert_no_partial_artifacts(output_dir)


def test_nonexistent_score_input_exits_1(tmp_path: Path) -> None:
    spec_path = tmp_path / "arrangement.yaml"
    _write_spec(spec_path, _spec_dict(target={}, score_fields={}))
    output_dir = tmp_path / "out"

    result = CliRunner().invoke(
        app,
        ["arrange", "nonexistent_score.yaml", str(spec_path), "--output-dir", str(output_dir)],
    )

    assert result.exit_code == 1
    _assert_no_partial_artifacts(output_dir)


def test_nonexistent_spec_input_exits_1(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"

    result = CliRunner().invoke(
        app,
        [
            "arrange",
            str(SAMPLE_PATH),
            str(tmp_path / "nonexistent_spec.yaml"),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 1
    _assert_no_partial_artifacts(output_dir)


def test_existing_directory_at_target_path_exits_1_and_preserves_other_files(
    tmp_path: Path,
) -> None:
    """出力先のいずれかが既存ディレクトリなら何も書かず exit 1（P2-2 事前チェック）。"""
    spec_path = tmp_path / "arrangement.yaml"
    _write_spec(spec_path, _elastic_bpm_spec_dict())
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "arrangement_bundle.json").mkdir()
    existing_derived = output_dir / "derived_score.yaml"
    existing_derived.write_text("pre-existing content\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["arrange", str(SAMPLE_PATH), str(spec_path), "--output-dir", str(output_dir)],
    )

    assert result.exit_code == 1
    assert "Error:" in result.stderr
    # 既存 derived_score.yaml は上書き・生成されない（部分成果物なしの pin）。
    assert existing_derived.read_text(encoding="utf-8") == "pre-existing content\n"
    assert not (output_dir / "arrangement_diff.json").exists()


def test_compile_arrangement_reads_each_input_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """hash とパースが同一バイト列を共有する（P2-1: 各入力の read_bytes は 1 回だけ）。"""
    from svp_rpe.arrange import compile_arrangement

    spec_path = tmp_path / "arrangement.yaml"
    _write_spec(spec_path, _elastic_bpm_spec_dict())

    read_counts: dict[str, int] = {}
    original_read_bytes = Path.read_bytes

    def counting_read_bytes(self: Path) -> bytes:
        key = str(self)
        read_counts[key] = read_counts.get(key, 0) + 1
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)

    compiled = compile_arrangement(str(SAMPLE_PATH), str(spec_path))

    assert read_counts[str(SAMPLE_PATH)] == 1
    assert read_counts[str(spec_path)] == 1
    # sha256 は同一バイト列由来（独立再計算と一致）。
    assert compiled.bundle["source_score"]["sha256"] == hashlib.sha256(
        SAMPLE_PATH.read_bytes()
    ).hexdigest()


def test_invalid_final_score_exits_1(tmp_path: Path) -> None:
    spec_path = tmp_path / "arrangement.yaml"
    _write_spec(
        spec_path,
        _spec_dict(
            target={"physical": {"bpm": "adagio"}},
            score_fields={"physical.bpm": "elastic"},
        ),
    )
    output_dir = tmp_path / "out"

    result = CliRunner().invoke(
        app,
        ["arrange", str(SAMPLE_PATH), str(spec_path), "--output-dir", str(output_dir)],
    )

    assert result.exit_code == 1
    _assert_no_partial_artifacts(output_dir)
