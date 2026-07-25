"""tests/test_m2_accuracy_harness.py — M2a `scripts/run_melody_accuracy.py` の単体テスト。

対象: `docs/DESIGN_M2_extraction_accuracy.md`（M2a 行、設計 §7 受け入れ条件）。

CI 安全性: 実抽出器（crepe / demucs）を一切必要としない。run/evaluate の二相
メカニズムはフェイク抽出器（決定論の f0 を返す `route_runner`）で検証し、
「実抽出器が未導入なら unavailable として fail-closed に落ちる」経路のみ
既定 runner（`observe_via_route_with_provenance`）を使った軽量スモークで確認する
（設計 §7 M2a 行: 「crepe が CI 不可なら…ハーネス単体テスト」）。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_melody_accuracy as harness  # noqa: E402
from svp_rpe.melody.observability import MelodyObservation  # noqa: E402
from svp_rpe.melody.accuracy import reference_f0_from_monophonic_spec  # noqa: E402

BARS_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "m2_accuracy_bars.yaml"
SPECS_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "m2_accuracy_specs.yaml"


# ---------------------------------------------------------------------------
# フェイク抽出器: spec 由来の正解を「+shift_cents だけずれた」決定論 f0 として返す。
# 実際の音声・抽出器コードには一切触れない。
# ---------------------------------------------------------------------------


def _make_fake_runner(shift_cents: float = 0.0):
    specs, _ = harness.load_specs(SPECS_PATH)

    def _runner(audio_path: str, route) -> Tuple[MelodyObservation, Dict[str, Any]]:
        category_spec = next(
            cs for cs in harness._CATEGORY_SPECS.values() if cs["route_name"] == route.name
        )
        melody_id = (
            category_spec["fixture_id"]
            if category_spec["kind"] == "direct"
            else specs["composites"][category_spec["composite_id"]]["melody"]
        )
        times, freqs = reference_f0_from_monophonic_spec(specs["fixtures"][melody_id])
        shifted = tuple(
            (0.0 if hz == 0.0 else hz * (2.0 ** (shift_cents / 1200.0))) for hz in freqs
        )
        observation = MelodyObservation(
            route=route.name,
            source_model="fake:deterministic",
            frame_times=times,
            frame_hz=shifted,
            frame_confidence=tuple(1.0 if hz > 0.0 else 0.0 for hz in shifted),
            total_duration_sec=times[-1] if times else 0.0,
        )
        provenance = {
            "extractor_weights_sha256": "fake-weights-sha256",
            "extractor_code_sha256": "fake-code-sha256",
        }
        return observation, provenance

    return _runner


def test_run_accuracy_with_fake_extractor_reports_measured_categories() -> None:
    report = harness.run_accuracy(route_runner=_make_fake_runner(shift_cents=10.0))

    assert report["mode"] == "synthetic_accuracy"
    assert set(report["categories"]) == {"S_direct", "S_fullstack"}
    for category, row in report["categories"].items():
        assert row["outcome"] == "measured", (category, row)
        assert row["metrics"]["raw_pitch_accuracy"] == pytest.approx(1.0)
        assert row["provenance_extractor_weights_sha256"] == "fake-weights-sha256"
        assert row["provenance_extractor_code_sha256"] == "fake-code-sha256"


def test_run_accuracy_provenance_fields() -> None:
    report = harness.run_accuracy(route_runner=_make_fake_runner())

    # recorded_utc: dated record 契約（UTC・ISO8601・未来でない）。
    parsed = harness._parse_recorded_utc(report["recorded_utc"], where="report")
    assert parsed is not None

    # run_id: 非空文字列で run 毎に発行される。
    assert isinstance(report["run_id"], str) and report["run_id"]

    # repo 相対パス（チェックアウト非依存の論理パス）。絶対パスは焼き込まない。
    assert report["specs_path_relative"] == "tests/fixtures/melody_bench/m2_accuracy_specs.yaml"
    assert report["bars_path_relative"] == "tests/fixtures/melody_bench/m2_accuracy_bars.yaml"
    assert not Path(report["specs_path_relative"]).is_absolute()
    assert not Path(report["bars_path_relative"]).is_absolute()

    # registry 相当 hash pin (bars/specs)。
    _, expected_bars_sha256 = harness.load_bars(BARS_PATH)
    _, expected_specs_sha256 = harness.load_specs(SPECS_PATH)
    assert report["bars_sha256"] == expected_bars_sha256
    assert report["specs_sha256"] == expected_specs_sha256

    # weights/code hash（フェイク抽出器が返した provenance がそのまま転記される）。
    for row in report["categories"].values():
        assert row["provenance_extractor_weights_sha256"] == "fake-weights-sha256"


def test_run_accuracy_two_repeats_are_bit_identical_for_deterministic_fake() -> None:
    report1 = harness.run_accuracy(route_runner=_make_fake_runner(shift_cents=5.0))
    report2 = harness.run_accuracy(route_runner=_make_fake_runner(shift_cents=5.0))
    for category in report1["categories"]:
        assert report1["categories"][category]["metrics"] == report2["categories"][category]["metrics"]
    # run_id は不透明で毎回発行されるため異なる（デザイン通り。#217 と同型）。
    assert report1["run_id"] != report2["run_id"]


def test_run_accuracy_real_extractor_falls_back_to_unavailable_when_uninstalled() -> None:
    """既定 runner（実抽出器）は crepe/demucs 未導入環境で fail-closed に unavailable を返す。

    実推論は一切行わない（CI 安全）。M2b の slow-lane でのみ real crepe を通す。
    """
    try:
        import crepe  # noqa: F401

        pytest.skip("crepe is installed in this environment; unavailable-path smoke test N/A")
    except ImportError:
        pass

    report = harness.run_accuracy()
    for category, row in report["categories"].items():
        assert row["outcome"] == "unavailable", (category, row)
        assert "detail" in row


def test_run_accuracy_unknown_category_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown accuracy categories"):
        harness.run_accuracy(categories=("S_direct", "bogus_category"))


# ---------------------------------------------------------------------------
# evaluate phase
# ---------------------------------------------------------------------------


def test_evaluate_m2_bars_full_cycle_pass_and_diagnostic_only() -> None:
    report1 = harness.run_accuracy(route_runner=_make_fake_runner(shift_cents=10.0))
    report2 = harness.run_accuracy(route_runner=_make_fake_runner(shift_cents=10.0))
    bars, bars_sha256 = harness.load_bars(BARS_PATH)

    verdict = harness.evaluate_m2_bars([report1, report2], bars, bars_sha256=bars_sha256)

    assert verdict["n_reports"] == 2
    assert len(set(verdict["run_ids"])) == 2
    s_direct = verdict["categories"]["S_direct"]
    assert s_direct["status"] == "pass", s_direct
    s_fullstack = verdict["categories"]["S_fullstack"]
    assert s_fullstack["status"] == "diagnostic_only", s_fullstack


def test_evaluate_m2_bars_fails_when_rpa_bar_not_met() -> None:
    # S_direct のバーは min_rpa=0.90。500 cent の大きなずれを与えて RPA を落とす。
    report1 = harness.run_accuracy(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=500.0)
    )
    report2 = harness.run_accuracy(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=500.0)
    )
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    verdict = harness.evaluate_m2_bars([report1, report2], bars, bars_sha256=bars_sha256)
    assert verdict["categories"]["S_direct"]["status"] == "fail"
    assert verdict["categories"]["S_direct"]["failures"]


def test_evaluate_m2_bars_insufficient_repeats_when_only_one_report() -> None:
    report = harness.run_accuracy(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    verdict = harness.evaluate_m2_bars([report], bars, bars_sha256=bars_sha256)
    assert verdict["categories"]["S_direct"]["status"] == "insufficient_repeats"


def test_evaluate_m2_bars_rejects_duplicate_run_id() -> None:
    report = harness.run_accuracy(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="run_id"):
        harness.evaluate_m2_bars([report, report], bars, bars_sha256=bars_sha256)


def test_evaluate_m2_bars_rejects_missing_recorded_utc() -> None:
    report = harness.run_accuracy(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    bad = dict(report)
    del bad["recorded_utc"]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="recorded_utc"):
        harness.evaluate_m2_bars([bad], bars, bars_sha256=bars_sha256)


def test_evaluate_m2_bars_rejects_mismatched_bars_sha256() -> None:
    report1 = harness.run_accuracy(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    report2 = dict(harness.run_accuracy(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)))
    report2["bars_sha256"] = "0" * 64
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="bars_sha256"):
        harness.evaluate_m2_bars([report1, report2], bars, bars_sha256=bars_sha256)


def test_evaluate_m2_bars_rejects_missing_generator_code_sha256() -> None:
    report = harness.run_accuracy(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    bad = dict(report)
    del bad["generator_code_sha256"]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="generator_code_sha256"):
        harness.evaluate_m2_bars([bad], bars, bars_sha256=bars_sha256)


def test_evaluate_m2_bars_rejects_generator_code_mismatch_between_repeats() -> None:
    report1 = harness.run_accuracy(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    report2 = dict(
        harness.run_accuracy(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
    )
    report2["generator_code_sha256"] = "0" * 64
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="repeats 間で不一致"):
        harness.evaluate_m2_bars([report1, report2], bars, bars_sha256=bars_sha256)


def test_evaluate_m2_bars_rejects_stale_generator_code_sha256() -> None:
    """現 checkout と違う generator digest の report にバーを適用しない（stale 拒否）。"""
    report1 = dict(
        harness.run_accuracy(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
    )
    report2 = dict(
        harness.run_accuracy(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
    )
    stale = "1" * 64
    report1["generator_code_sha256"] = stale
    report2["generator_code_sha256"] = stale
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="現 checkout"):
        harness.evaluate_m2_bars([report1, report2], bars, bars_sha256=bars_sha256)


def test_evaluate_m2_bars_rejects_tolerance_override_against_frozen_bar() -> None:
    """凍結値と違う許容幅で測った row にバーを適用しない（バー緩和の抜け道を塞ぐ）。

    600 cent 許容なら 500 cent ずれた推定でも min_rpa=0.90 を満たしてしまうが、
    `bars_sha256` は override では変わらないため、report の tolerance そのものを
    突き合わせるのが唯一の関所になる。
    """
    kwargs = dict(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=500.0))
    report1 = harness.run_accuracy(tolerance_cents=600.0, **kwargs)
    report2 = harness.run_accuracy(tolerance_cents=600.0, **kwargs)
    bars, bars_sha256 = harness.load_bars(BARS_PATH)

    # 緩い許容幅では S_direct のバーを満たしてしまうことを先に示す（抜け道の実在）。
    assert report1["categories"]["S_direct"]["metrics"]["raw_pitch_accuracy"] >= 0.90

    with pytest.raises(ValueError, match="tolerance_cents"):
        harness.evaluate_m2_bars([report1, report2], bars, bars_sha256=bars_sha256)


def test_evaluate_m2_bars_rejects_heterogeneous_model_stack() -> None:
    """別の抽出器重み/コードで測った 2 本を repeats として数えない。"""
    report1 = harness.run_accuracy(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    report2 = harness.run_accuracy(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    report2["categories"]["S_direct"]["provenance_extractor_weights_sha256"] = "other-weights"
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="model stack"):
        harness.evaluate_m2_bars([report1, report2], bars, bars_sha256=bars_sha256)


def test_evaluate_m2_bars_rejects_measured_row_without_weight_pin() -> None:
    """measured なのに重み pin を欠く row は repeats として数えない。"""
    report1 = harness.run_accuracy(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    report2 = harness.run_accuracy(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    del report2["categories"]["S_direct"]["provenance_extractor_weights_sha256"]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="provenance_extractor_weights_sha256"):
        harness.evaluate_m2_bars([report1, report2], bars, bars_sha256=bars_sha256)


def test_evaluate_m2_bars_records_report_pins_when_supplied() -> None:
    report1 = harness.run_accuracy(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    report2 = harness.run_accuracy(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    pins = [{"sha256": "a" * 64, "path_name": "r1.json"}, {"sha256": "b" * 64, "path_name": "r2.json"}]
    verdict = harness.evaluate_m2_bars(
        [report1, report2], bars, bars_sha256=bars_sha256, report_pins=pins
    )
    assert verdict["report_pins"] == pins
    assert verdict["tolerance_cents"] == 50.0

    with pytest.raises(ValueError, match="report_pins 件数"):
        harness.evaluate_m2_bars(
            [report1, report2], bars, bars_sha256=bars_sha256, report_pins=pins[:1]
        )


def test_cli_rejects_out_path_colliding_with_protected_inputs(tmp_path, monkeypatch) -> None:
    """`--out` が report / bars / specs を指したら書く前に停止する。"""
    report_path = tmp_path / "run1.json"
    report = harness.run_accuracy(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    report_path.write_text(json.dumps(report))

    # evaluate モード: --out が入力 report を指す。
    monkeypatch.setattr(
        sys, "argv",
        ["run_melody_accuracy.py", "--evaluate", str(report_path), "--out", str(report_path)],
    )
    with pytest.raises(SystemExit, match="評価入力"):
        harness.main()
    # 入力が破壊されていないこと。
    assert json.loads(report_path.read_text())["run_id"] == report["run_id"]

    # run モード: --out が凍結 bars を指す。
    monkeypatch.setattr(
        sys, "argv", ["run_melody_accuracy.py", "--out", str(BARS_PATH)]
    )
    before = BARS_PATH.read_bytes()
    with pytest.raises(SystemExit, match="凍結入力"):
        harness.main()
    assert BARS_PATH.read_bytes() == before


def test_evaluate_m2_bars_records_generator_code_sha256_in_verdict() -> None:
    report1 = harness.run_accuracy(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    report2 = harness.run_accuracy(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    verdict = harness.evaluate_m2_bars([report1, report2], bars, bars_sha256=bars_sha256)
    assert verdict["generator_code_sha256"] == report1["generator_code_sha256"]


# ---------------------------------------------------------------------------
# バー・spec ファイルの凍結内容テスト
# ---------------------------------------------------------------------------


def test_m2_accuracy_bars_are_frozen_single_values() -> None:
    bars, _ = harness.load_bars(BARS_PATH)
    assert bars["schema_version"] == "m2-accuracy-bars/0.1"
    block = bars["m2_accuracy_bars"]

    assert block["tolerance_cents"] == 50
    assert block["S_direct"] == {"min_rpa": 0.90, "max_vfa": 0.15}
    assert block["V_direct"] == {"min_rpa": 0.80, "max_octave_gap": 0.05}
    assert block["V_fullstack"] == {
        "min_rpa": 0.65,
        "max_octave_gap": 0.10,
        "max_vfa": 0.25,
    }
    assert block["S_fullstack"] == {}
    assert block["repeats_min"] == 2
    assert "緩めない" in block["one_way_rule"]


def test_m2_accuracy_bars_pins_match_committed_files() -> None:
    """bars.yaml の provenance pin が実ファイルと一致するか（drift 検出）。"""
    bars, _ = harness.load_bars(BARS_PATH)
    specs_bytes = SPECS_PATH.read_bytes()
    assert bars["provenance"]["specs_sha256"] == hashlib.sha256(specs_bytes).hexdigest()


def test_registry_yaml_is_untouched_by_m2a() -> None:
    """M2a は registry.yaml を編集しない（M1-real verdict の hash pin を壊さないため）。

    tests/test_m1real_committed_record.py が既にこの pin を CI で守っているが、
    ここでも M2a のスコープ判断（別ファイル分離）を明示的に固定する。
    """
    registry_path = ROOT / "tests" / "fixtures" / "melody_bench" / "registry.yaml"
    verdict_path = ROOT / "docs" / "measurements" / "m1real_2026-07" / "m1real_verdict.json"
    verdict = json.loads(verdict_path.read_text())
    actual = hashlib.sha256(registry_path.read_bytes()).hexdigest()
    assert actual == verdict["registry_sha256"], (
        "registry.yaml changed since the M1-real verdict was recorded; M2a must not "
        "edit registry.yaml (use m2_accuracy_bars.yaml instead)"
    )


def test_m2_accuracy_specs_uses_only_existing_builder_kinds() -> None:
    """M2a は build_melody_bench.py を変更しない前提なので、既存 kind のみを使う。"""
    specs, _ = harness.load_specs(SPECS_PATH)
    for fixture_id, spec in specs["fixtures"].items():
        assert spec["kind"] in ("monophonic", "chord_pad"), (fixture_id, spec["kind"])
