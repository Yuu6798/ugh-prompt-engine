"""C4 Composition E2E デモの回帰テスト。

3 階層でカバーする:
1. 演奏者の決定論（同一 Score + style → 同一波形バイト列）
2. 縮小 Score でのフルループ smoke（perform → extract → audit）
3. コミット済み成果物に対する針移動の検証（faithful_take が target に近い）
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from conftest import assert_no_outcome_keys

from scripts.compose_e2e_demo import (
    FAITHFUL_TAKE,
    FIRST_TAKE,
    SCORE_PATH,
    parse_key,
    perform,
    scaled_score,
)
from scripts.generate_synth_samples import wav_bytes
from svp_rpe.compose import load_composition_score

E2E_DIR = (
    Path(__file__).resolve().parents[1] / "examples" / "composition" / "midnight_signal" / "e2e"
)


def _committed_audit(style_name: str) -> dict[str, Any]:
    path = E2E_DIR / f"{style_name}_audit.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _needle(report: dict[str, Any], name: str) -> dict[str, Any]:
    for layer in ("physical", "semantic"):
        for needle in report[layer]:
            if needle["name"] == name:
                return needle
    raise AssertionError(f"needle not found: {name}")


def test_parse_key_handles_accidentals_and_case() -> None:
    assert parse_key("C minor") == (0, "minor")
    assert parse_key("f# Minor") == (6, "minor")
    assert parse_key("Eb major") == (3, "major")
    with pytest.raises(ValueError):
        parse_key("H mixolydian")


def test_perform_is_deterministic() -> None:
    score = load_composition_score(str(SCORE_PATH))
    mini = scaled_score(score, bars_scale=0.125)

    first = wav_bytes(perform(mini, FAITHFUL_TAKE))
    second = wav_bytes(perform(mini, FAITHFUL_TAKE))
    assert first == second

    deviant = wav_bytes(perform(mini, FIRST_TAKE))
    assert deviant != first


def test_e2e_loop_smoke_on_scaled_score(tmp_path: Path) -> None:
    """縮小 Score で perform → extract → audit のフルループが完走する。"""
    from svp_rpe.rpe.extractor import extract_rpe_from_file
    from svp_rpe.semantic_ci.audit import build_audit_report

    score = load_composition_score(str(SCORE_PATH))
    mini = scaled_score(score, bars_scale=0.25)
    wav_path = tmp_path / "faithful_mini.wav"
    wav_path.write_bytes(wav_bytes(perform(mini, FAITHFUL_TAKE)))

    bundle = extract_rpe_from_file(str(wav_path))
    report = build_audit_report(mini, bundle, observed_id="faithful_mini")
    payload = report.model_dump(mode="json")

    assert_no_outcome_keys(payload)
    assert payload["score_id"] == "midnight-signal"
    bpm = _needle(payload, "bpm")
    assert bpm["observed"] is not None
    key = _needle(payload, "key")
    assert key["target"] == "C minor"


def test_committed_artifacts_show_needles_moving_toward_target() -> None:
    """faithful_take の針が first_take より target に近いことを成果物で固定する。"""
    first = _committed_audit("first_take")
    faithful = _committed_audit("faithful_take")
    assert_no_outcome_keys(first)
    assert_no_outcome_keys(faithful)

    for knob in ("bpm", "key", "active_rate", "valley_depth", "delta_e"):
        dev_first = _needle(first, knob)["deviation"]
        dev_faithful = _needle(faithful, knob)["deviation"]
        assert abs(dev_faithful) < abs(dev_first), (
            f"{knob}: faithful deviation {dev_faithful} not closer than {dev_first}"
        )

    assert _needle(faithful, "key")["observed"] == "C minor"
    assert _needle(faithful, "delta_e")["observed"] == "gradual_build"


def test_committed_summary_matches_audit_artifacts() -> None:
    """needle_comparison.md が audit JSON と同じ針の値を表示している。"""
    summary = (E2E_DIR / "needle_comparison.md").read_text(encoding="utf-8")
    faithful = _committed_audit("faithful_take")
    bpm = _needle(faithful, "bpm")
    assert f"{bpm['observed']:.4g}" in summary
    # 単語境界で判定する（"bypass" や "surpass" を誤検出しない）
    assert re.search(r"\b(verdict|pass|passed|fail|failed)\b", summary, re.IGNORECASE) is None
