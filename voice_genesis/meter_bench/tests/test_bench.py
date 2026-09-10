"""Meter Bench の構造テスト（リセット設計 v0 §6 受け入れ条件）。

**計器の PASS/FAIL はテストの成否に含めない**（それは `results/*.json` が
記録する事実であって、テストが守るのは台の側の不変条件だけ）。ここが守るのは
統治予算 4 点: import 境界 / 800 行上限 / 判定式 3 種 / claimable=False、
および決定論（2 回実行で timestamp 以外一致）。
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from voice_genesis.calibration import vocab
from voice_genesis.calibration.candidates.adapter import MeterOutput
from voice_genesis.calibration.fixtures.controls import DetectionPredicate
from voice_genesis.meter_bench import CLAIMABLE, SCHEMA, cases, run as run_mod

PACKAGE_DIR = Path(run_mod.__file__).resolve().parent
SOURCES = sorted(PACKAGE_DIR.rglob("*.py"))

FORBIDDEN_MODULES = (
    "voice_genesis.calibration.campaign",
    "voice_genesis.calibration.provenance",
    "voice_genesis.calibration.approvals",
    "voice_genesis.calibration.c0_freeze",
    "voice_genesis.calibration.c0_validate",
    "voice_genesis.calibration.splitter",
    "voice_genesis.calibration.gates",
    "voice_genesis.calibration.selection",
    "voice_genesis.calibration.e_use_table",
    "voice_genesis.calibration.cost_caps",
    "voice_genesis.calibration.authorization_guard",
)
"""Tier F（§3）。Bench はここを一切 import しない — 承認・封印・台帳・Gate を
持ち込んだ瞬間に Bench は第 2 の campaign 基盤に育つ。"""

LINE_BUDGET = 800


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_import_boundary_rejects_tier_f() -> None:
    offenders: list[str] = []
    for path in SOURCES:
        for name in _imported_names(path):
            if any(name == f or name.startswith(f + ".") for f in FORBIDDEN_MODULES):
                offenders.append(f"{path.relative_to(PACKAGE_DIR)}: {name}")
    assert not offenders, f"meter_bench から Tier F への import: {offenders}"


def test_import_boundary_holds_transitively() -> None:
    """`import voice_genesis.meter_bench.run` の閉包にも Tier F が現れない
    （fresh process で sys.modules を見る — 同一セッションの他テストの
    import に汚染されないため）。"""
    code = (
        "import importlib, sys, json;"
        "importlib.import_module('voice_genesis.meter_bench.run');"
        f"print(json.dumps([m for m in sys.modules if m.startswith({FORBIDDEN_MODULES!r})]))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=120
    )
    assert json.loads(proc.stdout.strip()) == []


def test_line_budget() -> None:
    """§4-3: tests・criteria.yaml 込み 800 行上限。"""
    files = sorted(PACKAGE_DIR.rglob("*.py")) + sorted(PACKAGE_DIR.rglob("*.yaml"))
    total = sum(len(p.read_text(encoding="utf-8").splitlines()) for p in files)
    assert total <= LINE_BUDGET, f"meter_bench が {total} 行で {LINE_BUDGET} 行上限を超過"


def test_claimable_is_a_constant_false() -> None:
    """§4-5: Bench の PASS は校正証拠ではない。"""
    assert CLAIMABLE is False


# ---------------------------------------------------------------------------
# case / criteria の形
# ---------------------------------------------------------------------------


def test_criteria_declares_only_the_three_frozen_checks() -> None:
    criteria = run_mod.load_criteria()
    assert set(criteria) <= {m.value for m in vocab.MeterId}
    for meter_name, spec in criteria.items():
        assert spec["check"] in cases.CHECKS, meter_name
        if spec["check"] == cases.ABS_ERROR:
            assert spec["unit"] in cases.SCALES, meter_name
            assert float(spec["tol"]) > 0.0, meter_name
        if spec["check"] == cases.MONOTONE:
            assert 0.0 < float(spec["tau_min"]) <= 1.0, meter_name


def test_every_implemented_meter_has_criteria_and_unique_case_ids() -> None:
    criteria = run_mod.load_criteria()
    for meter, meter_cases in cases.CASES_BY_METER.items():
        assert meter.value in criteria, meter
        ids = [c.case_id for c in meter_cases]
        assert len(ids) == len(set(ids)), meter
        assert all(c.meter is meter for c in meter_cases)


def test_every_implemented_meter_carries_both_negative_controls() -> None:
    """§1 criteria 表「全 meter 負例 no_fire NOISE_ONLY / SILENCE」。"""
    for meter, meter_cases in cases.CASES_BY_METER.items():
        controls = {
            c.gen_params.get("control_class")
            for c in meter_cases
            if c.check == cases.NO_FIRE
        }
        assert controls == set(cases.NEGATIVE_CONTROLS), meter


def test_tilt_case_set_is_the_declared_84_positives_plus_2_negatives() -> None:
    tilt = cases.cases_for(vocab.MeterId.M2_SPECTRAL_TILT)
    positives = [c for c in tilt if c.check == cases.ABS_ERROR]
    assert len(positives) == 84 and len(tilt) == 86
    assert {c.truth for c in positives} == set(cases.TILT_SLOPE_DB_PER_OCT)


def test_case_rejects_checks_outside_the_frozen_three() -> None:
    with pytest.raises(ValueError, match="check must be one of"):
        cases.BenchCase(
            meter=vocab.MeterId.M2_SPECTRAL_TILT,
            family=cases.FixtureFamily.TILT_GT,
            case_id="bogus",
            gen_params={"f0_hz": 220.0},
            truth=1.0,
            check="r_squared",
            field_name="x",
        )


# ---------------------------------------------------------------------------
# 判定式（音を作らない純関数）
# ---------------------------------------------------------------------------


def test_scaled_error_units() -> None:
    assert run_mod.scaled_error(-5.0, -6.0, cases.SCALE_ABSOLUTE) == pytest.approx(1.0)
    assert run_mod.scaled_error(440.0, 220.0, cases.SCALE_CENTS) == pytest.approx(1200.0)
    assert run_mod.scaled_error(105.0, 100.0, cases.SCALE_RELATIVE) == pytest.approx(0.05)


def test_abs_error_reports_the_missing_reason_rather_than_a_number() -> None:
    case = cases.cases_for(vocab.MeterId.M2_SPECTRAL_TILT)[0]
    missing = MeterOutput(missing_reason=vocab.MissingReason.OUTPUT_MISSING)
    ok, observed, error, reason = run_mod.check_abs_error(case, missing, 1.0)
    assert (ok, observed, error) == (False, None, None)
    assert reason == "MISSING:OUTPUT_MISSING"
    ok, _, _, reason = run_mod.check_abs_error(
        case, MeterOutput(values={"tilt_db_per_oct": -3.5}), 1.0
    )
    assert ok and reason == ""


def test_no_fire_uses_the_frozen_detection_semantics() -> None:
    predicate = DetectionPredicate(field="hnr_acf_db", min_value=-5.0)
    fires = MeterOutput(values={"tilt_db_per_oct": 0.0, "hnr_acf_db": 0.5})
    quiet = MeterOutput(values={"tilt_db_per_oct": 0.0})
    assert run_mod.check_no_fire(quiet, predicate) == (True, "")
    assert run_mod.check_no_fire(fires, predicate) == (False, "FIRED")
    assert run_mod.check_no_fire(quiet, None) == (False, "FIRED")


def test_monotone_applies_polarity_and_needs_three_points() -> None:
    truths = [0.0, 0.2, 0.4, 0.6]
    assert run_mod.check_monotone(truths, [1.0, 2.0, 3.0, 4.0], 1, 0.9)[0]
    assert not run_mod.check_monotone(truths, [4.0, 3.0, 2.0, 1.0], 1, 0.9)[0]
    assert run_mod.check_monotone(truths, [4.0, 3.0, 2.0, 1.0], -1, 0.9)[0]
    assert not run_mod.check_monotone([0.0, 1.0], [0.0, 1.0], 1, 0.9)[0]


# ---------------------------------------------------------------------------
# 実行（音を作る）
# ---------------------------------------------------------------------------


def test_render_is_byte_identical_across_calls() -> None:
    for case in cases.cases_for(vocab.MeterId.M2_SPECTRAL_TILT)[:3]:
        first, sr = run_mod.render(case)
        second, sr2 = run_mod.render(case)
        assert (sr, first.tobytes()) == (sr2, second.tobytes()), case.case_id


def test_committed_result_is_present_and_not_claimable() -> None:
    path = run_mod.RESULTS_DIR / f"{vocab.MeterId.M2_SPECTRAL_TILT.value}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == SCHEMA
    assert payload["claimable"] is False
    assert payload["verdict"] in (run_mod.PASS, run_mod.FAIL)


@pytest.mark.slow
def test_run_is_deterministic_apart_from_the_timestamp(tmp_path: Path) -> None:
    """§6: 2 回実行で timestamp 以外一致。"""
    first = run_mod.run(vocab.MeterId.M2_SPECTRAL_TILT).to_dict()
    second = run_mod.run(vocab.MeterId.M2_SPECTRAL_TILT).to_dict()
    first.pop("generated_at"), second.pop("generated_at")
    assert first == second
    written = run_mod.run(vocab.MeterId.M2_SPECTRAL_TILT).write(tmp_path)
    assert json.loads(written.read_text(encoding="utf-8"))["meter"] == "M2_SPECTRAL_TILT"


def test_cases_for_names_the_implemented_meters_when_asked_for_another() -> None:
    with pytest.raises(KeyError, match="M4_RESONANCE"):
        cases.cases_for(vocab.MeterId.M4_RESONANCE)
