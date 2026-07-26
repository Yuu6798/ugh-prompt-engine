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
import importlib
import json
import os
import re
import sys
from datetime import datetime, timezone
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

# フェイク抽出器の model pin。真の sha256（64 桁 hex）でなければ evaluate が
# プレースホルダとして拒否する規律のため、決定論の digest を使う。
@pytest.fixture(autouse=True)
def _clean_evaluator_preload(monkeypatch: pytest.MonkeyPatch):
    """pytest プロセスの事前ロードを、テストに限り「素の CLI 起動」相当へ正規化する。

    pytest では他のテストファイルが svp_rpe モジュールを先に import しているため、
    本ハーネスの load 時点で `_PRELOADED_SEED_MODULES` が非空になる。evaluate は
    それを fail-closed で拒否する（正しい挙動）ので、機構テストが評価段まで到達
    できるようここで空へ正規化する——`_fake_run` が report 側の
    `preloaded_seed_modules` を正規化するのと同じテスト専用の操作。非空のとき
    evaluate が拒否すること自体は
    `test_evaluate_m2_bars_rejects_preloaded_evaluator_process` が固定する。

    併せて `_environment_execution_pins`（実行証拠の再計算）を、フェイク抽出器の
    pin と一致する値へ差し替える。CI 環境は CREPE/Demucs 未導入なので素のままだと
    「実行証拠を再計算できない」として全 evaluate テストが正しく拒否される——
    その拒否自体は `test_evaluate_m2_bars_refuses_without_execution_evidence` が
    固定し、ここでは機構テストが評価段へ到達できるよう slow-lane 機相当の環境を
    模す。
    """
    monkeypatch.setattr(harness, "_PRELOADED_SEED_MODULES", [])
    monkeypatch.setattr(harness, "_environment_execution_pins", _fake_environment_pins)
    # ハーネスはテストから import されるため直接パス実行フラグは False になる。
    # 素の CLI 起動相当へ正規化する（False の拒否自体は専用テストが固定する）。
    monkeypatch.setattr(harness, "_HARNESS_LOADED_AS_MAIN", True)
    # 測り直し検証（実抽出器の再実行）も同様に正規化する。素の CI では実抽出器が
    # 無く「再実行できない」拒否になる（正しい挙動・専用テストで固定）ため、機構
    # テストが評価段へ到達できるようここでは no-op にする。実検証の合否は
    # `_ORIG_REVERIFY` を戻す専用テスト群が固定する。
    monkeypatch.setattr(
        harness, "_reverify_category_measurement", lambda *args, **kwargs: None
    )
    # 事前登録の git 立証も正規化する。機構テストは tmp の bars（履歴に無い blob）を
    # 多用するため、素のままだと全 evaluate テストが立証不能拒否になる（正しい挙動・
    # 専用テスト群 `test_bars_registration_attestation_*` が実 gate を固定する）。
    monkeypatch.setattr(
        harness,
        "_require_attested_registration",
        lambda *args, **kwargs: {
            "first_commit": "0" * 40,
            "committed_utc": "2026-07-25T00:00:00+00:00",
            "source": "test_fixture_stub",
        },
    )


_ORIG_REVERIFY = harness._reverify_category_measurement
_ORIG_ATTEST = harness._require_attested_registration


def _fake_environment_pins(route) -> Dict[str, Any]:
    """フェイク抽出器の pin と一致する「評価環境の実行証拠」（slow-lane 機の模擬）。"""
    pins: Dict[str, Any] = {
        "extractor_code_sha256": FAKE_CODE_SHA256,
        "extractor_weights_sha256": FAKE_WEIGHTS_SHA256,
    }
    if route.requires_separation:
        pins["separation_code_sha256"] = FAKE_SEP_CODE_SHA256
        pins["separation_weights_sha256"] = FAKE_SEP_WEIGHTS_SHA256
    return pins


FAKE_WEIGHTS_SHA256 = "bf6875a563be64dafa0c8e16f4b6093f55e15ba38f5c7a8844eaa61141dc805e"
FAKE_CODE_SHA256 = "cffe5426ffd1a5c4a1530e74529ccd0b0cec63fcd07165c3c8564c5cedb770d9"
FAKE_SEP_WEIGHTS_SHA256 = hashlib.sha256(b"fake-separation-weights").hexdigest()
FAKE_SEP_CODE_SHA256 = hashlib.sha256(b"fake-separation-code").hexdigest()
FAKE_STEM_SHA256 = hashlib.sha256(b"fake-stem").hexdigest()


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
        times, freqs = reference_f0_from_monophonic_spec(
            specs["fixtures"][melody_id], sample_rate=int(specs["sample_rate"])
        )
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
        provenance: Dict[str, Any] = {
            "extractor_weights_sha256": FAKE_WEIGHTS_SHA256,
            "extractor_code_sha256": FAKE_CODE_SHA256,
        }
        if route.preprocessing:
            # 分離経路は分離器と stem も pin されていなければ evaluate が弾く。
            provenance["preprocessing"] = {
                "preprocessing": route.preprocessing,
                "separation_model": "fake-htdemucs",
                "separation_version": "0.0-fake",
                "separation_weights_sha256": FAKE_SEP_WEIGHTS_SHA256,
                "separation_code_sha256": FAKE_SEP_CODE_SHA256,
                "stem_sha256": FAKE_STEM_SHA256,
            }
        return observation, provenance

    return _runner


def _fake_run(**kwargs: Any) -> Dict[str, Any]:
    """フェイク抽出器で run し、**機構テスト用に** publish 可能な体裁へ整える。

    `run_accuracy(route_runner=...)` が刻む `route_runner_injected=True` は、
    evaluate 側で「フェイク抽出器の出力を実測記録として publish しない」関所に
    使われる。ここで False に落とすのは、その関所の**先にある**検査（バー適用・
    provenance 照合・決定論比較）を単体で確かめるためのテスト専用の操作であり、
    注入 report が素のままでは拒否されることは
    `test_evaluate_m2_bars_rejects_injected_runner_reports` が固定する。
    """
    report = harness.run_accuracy(**kwargs)
    report["route_runner_injected"] = False
    # pytest では閉包モジュールが先に import 済みなので、実測 run と同じ体裁に揃える
    # （事前ロード run が素のままでは拒否されることは別テストが固定する）。
    report["preloaded_seed_modules"] = []
    return report


def _as_report_artifact(report: Dict[str, Any]) -> Any:
    """dict の report を、raw/digest/parsed が整合した `ReportArtifact` へ包む。

    `evaluate_m2_bars` は pin と評価対象を束縛するため `ReportArtifact` しか受理しない。
    テストが report を編集して下流の関所を試す場合も、**編集後の内容を serialize した
    bytes** から組めば束縛は保たれ、狙った関所まで到達できる。
    """
    raw = json.dumps(report, sort_keys=True).encode("utf-8")
    return harness.ReportArtifact.from_bytes(raw, path=None)


def test_run_accuracy_with_fake_extractor_reports_measured_categories() -> None:
    report = _fake_run(route_runner=_make_fake_runner(shift_cents=10.0))

    assert report["mode"] == "synthetic_accuracy"
    assert set(report["categories"]) == {"S_direct", "S_fullstack"}
    for category, row in report["categories"].items():
        assert row["outcome"] == "measured", (category, row)
        assert row["metrics"]["raw_pitch_accuracy"] == pytest.approx(1.0)
        assert row["provenance_extractor_weights_sha256"] == FAKE_WEIGHTS_SHA256
        assert row["provenance_extractor_code_sha256"] == FAKE_CODE_SHA256


def test_run_accuracy_provenance_fields() -> None:
    report = _fake_run(route_runner=_make_fake_runner())

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
        assert row["provenance_extractor_weights_sha256"] == FAKE_WEIGHTS_SHA256


def test_run_accuracy_two_repeats_are_bit_identical_for_deterministic_fake() -> None:
    report1 = _fake_run(route_runner=_make_fake_runner(shift_cents=5.0))
    report2 = _fake_run(route_runner=_make_fake_runner(shift_cents=5.0))
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
    assert report["route_runner_injected"] is False
    for category, row in report["categories"].items():
        assert row["outcome"] == "unavailable", (category, row)
        assert "detail" in row


def test_run_accuracy_unknown_category_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown accuracy categories"):
        harness.run_accuracy(categories=("S_direct", "bogus_category"))


def test_run_accuracy_marks_injected_runner_in_report() -> None:
    report = harness.run_accuracy(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    assert report["route_runner_injected"] is True


def test_evaluate_m2_bars_rejects_injected_runner_reports() -> None:
    """フェイク抽出器の出力は、他の全検査を通っても publish 可能な実測にしない。"""
    reports = [
        harness.run_accuracy(
            categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
        )
        for _ in range(2)
    ]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="route_runner 注入"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_run_accuracy_records_preloaded_seed_modules() -> None:
    """「ハーネス読み込み時点で閉包モジュールが既にロード済みだったか」が report に載る。

    値そのものは import 順序に依存する（このテストファイルは harness を先に import
    するので通常は空）。ここで固定するのは、フィールドが必ず存在し、実際の判定に
    使える形（文字列のリスト）で記録されることまで。非空だった場合に evaluate が
    拒否することは `test_evaluate_m2_bars_rejects_preloaded_module_reports` が固定する。
    """
    report = harness.run_accuracy(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    preloaded = report["preloaded_seed_modules"]
    assert isinstance(preloaded, list)
    assert all(isinstance(name, str) for name in preloaded)
    # 監視対象は digest 閉包（推移的モジュール含む）+ ランタイムパッケージから導出。
    allowed = (
        set(harness._closure_module_names())
        | set(harness._SEED_MODULE_NAMES)
        | set(harness._runtime_package_names())
    )
    assert set(preloaded) <= allowed, sorted(set(preloaded) - allowed)


def test_evaluate_m2_bars_rejects_preloaded_module_reports() -> None:
    """閉包モジュールが事前ロード済みのプロセスで作られた run は publish 不可。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    reports[0]["preloaded_seed_modules"] = ["svp_rpe.melody.extractors"]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="事前ロード済み"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_evaluate_m2_bars_rejects_report_without_preloaded_field() -> None:
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    del reports[1]["preloaded_seed_modules"]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="preloaded_seed_modules"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_mir_eval_paths_are_protected_from_out(monkeypatch) -> None:
    """provenance のために hash する mir_eval のファイルも `--out` から守る。"""
    paths = harness._mir_eval_paths()
    assert paths, "mir_eval のファイルが解決できない（テストの前提が drift）"
    target = paths[0]
    before = target.read_bytes()
    monkeypatch.setattr(sys, "argv", ["run_melody_accuracy.py", "--out", str(target)])
    with pytest.raises(SystemExit, match="provenance"):
        harness.main()
    assert target.read_bytes() == before


def test_evaluate_m2_bars_rejects_report_without_injection_flag() -> None:
    """規律より前に作られた（または手組みの）report を黙って通さない。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    del reports[0]["route_runner_injected"]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="route_runner_injected"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


# ---------------------------------------------------------------------------
# evaluate phase
# ---------------------------------------------------------------------------


def test_evaluate_m2_bars_full_cycle_pass_and_diagnostic_only() -> None:
    report1 = _fake_run(route_runner=_make_fake_runner(shift_cents=10.0))
    report2 = _fake_run(route_runner=_make_fake_runner(shift_cents=10.0))
    bars, bars_sha256 = harness.load_bars(BARS_PATH)

    verdict = harness.evaluate_m2_bars(
            [_as_report_artifact(report1), _as_report_artifact(report2)],
            bars,
            bars_sha256=bars_sha256,
        )

    assert verdict["n_reports"] == 2
    assert len(set(verdict["run_ids"])) == 2
    s_direct = verdict["categories"]["S_direct"]
    assert s_direct["status"] == "pass", s_direct
    s_fullstack = verdict["categories"]["S_fullstack"]
    assert s_fullstack["status"] == "diagnostic_only", s_fullstack


def test_evaluate_m2_bars_fails_when_rpa_bar_not_met() -> None:
    # S_direct のバーは min_rpa=0.90。500 cent の大きなずれを与えて RPA を落とす。
    report1 = _fake_run(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=500.0)
    )
    report2 = _fake_run(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=500.0)
    )
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    verdict = harness.evaluate_m2_bars(
            [_as_report_artifact(report1), _as_report_artifact(report2)],
            bars,
            bars_sha256=bars_sha256,
        )
    assert verdict["categories"]["S_direct"]["status"] == "fail"
    assert verdict["categories"]["S_direct"]["failures"]


def test_evaluate_m2_bars_insufficient_repeats_when_only_one_report() -> None:
    report = _fake_run(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    verdict = harness.evaluate_m2_bars(
        [_as_report_artifact(report)], bars, bars_sha256=bars_sha256
    )
    assert verdict["categories"]["S_direct"]["status"] == "insufficient_repeats"


def test_evaluate_m2_bars_rejects_duplicate_run_id() -> None:
    report = _fake_run(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="run_id"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(report), _as_report_artifact(report)],
            bars,
            bars_sha256=bars_sha256,
        )


def test_evaluate_m2_bars_rejects_missing_recorded_utc() -> None:
    report = _fake_run(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    bad = dict(report)
    del bad["recorded_utc"]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="recorded_utc"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(bad)], bars, bars_sha256=bars_sha256
        )


def test_evaluate_m2_bars_rejects_mismatched_bars_sha256() -> None:
    report1 = _fake_run(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    report2 = dict(_fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)))
    report2["bars_sha256"] = "0" * 64
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="bars_sha256"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(report1), _as_report_artifact(report2)],
            bars,
            bars_sha256=bars_sha256,
        )


def test_evaluate_m2_bars_rejects_missing_generator_code_sha256() -> None:
    report = _fake_run(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    bad = dict(report)
    del bad["generator_code_sha256"]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="generator_code_sha256"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(bad)], bars, bars_sha256=bars_sha256
        )


def test_evaluate_m2_bars_rejects_generator_code_mismatch_between_repeats() -> None:
    report1 = _fake_run(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    report2 = dict(
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
    )
    report2["generator_code_sha256"] = "0" * 64
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="repeats 間で不一致"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(report1), _as_report_artifact(report2)],
            bars,
            bars_sha256=bars_sha256,
        )


def test_evaluate_m2_bars_rejects_stale_generator_code_sha256() -> None:
    """現 checkout と違う generator digest の report にバーを適用しない（stale 拒否）。"""
    report1 = dict(
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
    )
    report2 = dict(
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
    )
    stale = "1" * 64
    report1["generator_code_sha256"] = stale
    report2["generator_code_sha256"] = stale
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="現 checkout"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(report1), _as_report_artifact(report2)],
            bars,
            bars_sha256=bars_sha256,
        )


def test_evaluate_m2_bars_rejects_tolerance_override_against_frozen_bar() -> None:
    """凍結値と違う許容幅で測った row にバーを適用しない（バー緩和の抜け道を塞ぐ）。

    600 cent 許容なら 500 cent ずれた推定でも min_rpa=0.90 を満たしてしまうが、
    `bars_sha256` は override では変わらないため、report の tolerance そのものを
    突き合わせるのが唯一の関所になる。
    """
    kwargs = dict(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=500.0))
    report1 = _fake_run(tolerance_cents=600.0, **kwargs)
    report2 = _fake_run(tolerance_cents=600.0, **kwargs)
    bars, bars_sha256 = harness.load_bars(BARS_PATH)

    # 緩い許容幅では S_direct のバーを満たしてしまうことを先に示す（抜け道の実在）。
    assert report1["categories"]["S_direct"]["metrics"]["raw_pitch_accuracy"] >= 0.90

    with pytest.raises(ValueError, match="tolerance_cents"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(report1), _as_report_artifact(report2)],
            bars,
            bars_sha256=bars_sha256,
        )


def test_evaluate_m2_bars_rejects_heterogeneous_model_stack() -> None:
    """別の抽出器重み/コードで測った 2 本を repeats として数えない。"""
    report1 = _fake_run(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    report2 = _fake_run(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    # 真の sha256 だが別の値 = 「別の重みで測った」ケース（pin 形式は正しい）。
    report2["categories"]["S_direct"]["provenance_extractor_weights_sha256"] = hashlib.sha256(
        b"other-weights"
    ).hexdigest()
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="model stack"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(report1), _as_report_artifact(report2)],
            bars,
            bars_sha256=bars_sha256,
        )


def test_evaluate_m2_bars_rejects_measured_row_without_weight_pin() -> None:
    """measured なのに重み pin を欠く row は repeats として数えない。"""
    report1 = _fake_run(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    report2 = _fake_run(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    del report2["categories"]["S_direct"]["provenance_extractor_weights_sha256"]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="provenance_extractor_weights_sha256"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(report1), _as_report_artifact(report2)],
            bars,
            bars_sha256=bars_sha256,
        )


def test_evaluate_m2_bars_derives_report_pins_from_evaluated_bytes() -> None:
    """pin は呼び出し側の申告ではなく、実際に評価した bytes から導出される。"""
    report1 = _fake_run(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    report2 = _fake_run(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    artifacts = [_as_report_artifact(report1), _as_report_artifact(report2)]
    verdict = harness.evaluate_m2_bars(artifacts, bars, bars_sha256=bars_sha256)
    assert verdict["report_pins"] == [a.pin() for a in artifacts]
    assert [pin["sha256"] for pin in verdict["report_pins"]] == [a.sha256 for a in artifacts]
    assert verdict["tolerance_cents"] == 50.0


def test_evaluate_m2_bars_rejects_plain_dict_reports() -> None:
    """parsed 内容と digest が切り離された report（素の dict）は受理しない。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    # **意図的に** 素の dict を渡す（artifact に包まない）。
    with pytest.raises(ValueError, match="ReportArtifact"):
        harness.evaluate_m2_bars(reports, bars, bars_sha256=bars_sha256)


def test_evaluate_m2_bars_rejects_report_mutated_after_load() -> None:
    """元ファイルの hash を pin しながら別内容を判定する経路を塞ぐ。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    artifacts = [_as_report_artifact(r) for r in reports]
    # load 後に parsed mapping だけを書き換える（raw bytes と digest は元のまま）。
    artifacts[0].data["categories"]["S_direct"]["metrics"]["raw_pitch_accuracy"] = 0.99
    with pytest.raises(ValueError, match="load 後に変異"):
        harness.evaluate_m2_bars(artifacts, bars, bars_sha256=bars_sha256)


def test_load_report_binds_bytes_digest_and_parsed_data(tmp_path: Path) -> None:
    report = _fake_run(categories=("S_direct",), route_runner=_make_fake_runner())
    path = tmp_path / "r1.json"
    raw = json.dumps(report, indent=2, sort_keys=True).encode("utf-8")
    path.write_bytes(raw)
    artifact = harness.load_report(path)
    assert artifact.sha256 == hashlib.sha256(raw).hexdigest()
    assert artifact.verify() == report
    assert artifact.pin()["path_name"] == "r1.json"


def test_generator_digest_covers_first_party_extraction_path() -> None:
    """digest の閉包が抽出経路の first-party コードを含む（ハーネス 2 本だけでない）。"""
    paths = {p.name for p in harness._generator_code_paths()}
    for expected in ("run_melody_accuracy.py", "accuracy.py", "extractors.py", "routing.py"):
        assert expected in paths, (expected, sorted(paths))
    # third-party（crepe / numpy 等）は閉包に混ぜない（環境差で digest が揺れるため）。
    for path in harness._generator_code_paths():
        assert any(
            harness._is_relative_to(path, root) for root in harness._FIRST_PARTY_ROOTS
        ), path


def test_generator_digest_changes_when_extraction_module_changes(tmp_path, monkeypatch) -> None:
    """`melody/extractors.py` が変われば digest が動く（旧 row の stale 検出が効く）。"""
    before = harness._generator_code_sha256()
    extractors_path = ROOT / "src" / "svp_rpe" / "melody" / "extractors.py"
    original = extractors_path.read_bytes()
    try:
        extractors_path.write_bytes(original + b"\n# provenance drift probe\n")
        after = harness._generator_code_sha256()
    finally:
        extractors_path.write_bytes(original)
    assert before != after
    assert harness._generator_code_sha256() == before


def test_evaluate_m2_bars_rejects_placeholder_model_pin() -> None:
    """`"TBD"` 等のプレースホルダは、両 repeats で同一でも model pin と見なさない。"""
    reports = [
        _fake_run(
            categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
        )
        for _ in range(2)
    ]
    for report in reports:
        report["categories"]["S_direct"]["provenance_extractor_weights_sha256"] = "TBD"
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="真の sha256"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_atomic_write_text_publishes_exact_utf8_bytes(tmp_path) -> None:
    """publish される bytes が、ハーネスが選んだ encode 結果と完全一致する。"""
    target = tmp_path / "out.json"
    payload = '{"a": 1}\n{"b": "\\u00e9"}\n'
    harness._atomic_write_text(target, payload)
    assert target.read_bytes() == payload.encode("utf-8")


def test_run_report_pins_the_mir_eval_scorer() -> None:
    """指標を計算した mir_eval の version / code hash が report に載る。"""
    report = _fake_run(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    assert report["mir_eval_version"]
    assert harness._is_sha256(report["mir_eval_code_sha256"])


def test_evaluate_m2_bars_rejects_divergent_mir_eval_pins() -> None:
    """別リリースの mir_eval で測った 2 本を同一 stack の repeats と数えない。"""
    reports = [
        _fake_run(
            categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
        )
        for _ in range(2)
    ]
    reports[1]["mir_eval_version"] = "0.999"
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="mir_eval pin"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_evaluate_m2_bars_rejects_missing_mir_eval_pin() -> None:
    reports = [
        _fake_run(
            categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
        )
        for _ in range(2)
    ]
    for report in reports:
        del report["mir_eval_version"]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="mir_eval_version"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_evaluate_m2_bars_fails_when_deterministic_repeats_diverge() -> None:
    """同一 stack なのに metrics が食い違う repeats は、個々がバー内でも pass にしない。"""
    reports = [
        _fake_run(
            categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
        )
        for _ in range(2)
    ]
    metrics = reports[1]["categories"]["S_direct"]["metrics"]
    # 0.95 はバー（min_rpa=0.90）を満たすが、repeat[0] の 1.0 とは一致しない。
    # `octave_gap` は導出フィールド（RCA - RPA）なので、row 単体としては整合させる
    # ——ここで検証したいのは「row 内の矛盾」ではなく「repeats 間の不一致」の検出。
    metrics["raw_pitch_accuracy"] = 0.95
    metrics["octave_gap"] = metrics["raw_chroma_accuracy"] - metrics["raw_pitch_accuracy"]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    verdict = harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )
    s_direct = verdict["categories"]["S_direct"]
    assert s_direct["repeats_bit_identical"] is False
    assert s_direct["status"] == "fail", s_direct
    assert any("bit 一致" in f for f in s_direct["failures"]), s_direct["failures"]


def test_evaluate_m2_bars_records_bit_identical_repeats_on_pass() -> None:
    reports = [
        _fake_run(
            categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
        )
        for _ in range(2)
    ]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    verdict = harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )
    assert verdict["categories"]["S_direct"]["repeats_bit_identical"] is True
    assert verdict["categories"]["S_direct"]["status"] == "pass"
    assert verdict["mir_eval_version"] == reports[0]["mir_eval_version"]


def test_module_file_resolution_imports_nothing(monkeypatch) -> None:
    """seed パス解決が親パッケージを実行しない（find_spec を使っていたら失敗する）。

    `importlib.util.find_spec("svp_rpe.melody.accuracy")` は `svp_rpe.melody.__init__`
    を実行し、それが `observability` / `routing` を import する。hash より前に import が
    起きると「旧モジュールが実行され digest は新しいディスクを見る」窓が開くため、
    パス解決は純粋なファイルシステム写像でなければならない。
    """
    watched = [
        "svp_rpe",
        "svp_rpe.melody",
        "svp_rpe.melody.accuracy",
        "svp_rpe.melody.observability",
        "svp_rpe.melody.routing",
    ]
    for name in watched:
        monkeypatch.delitem(sys.modules, name, raising=False)

    resolved = harness._first_party_module_file("svp_rpe.melody.accuracy")
    assert resolved is not None
    assert resolved.name == "accuracy.py"
    assert not any(name in sys.modules for name in watched), sorted(
        name for name in watched if name in sys.modules
    )


def test_generator_code_paths_imports_nothing(monkeypatch) -> None:
    """閉包計算そのものも import を起こさない（load-time pin の前提）。"""
    watched = [
        "svp_rpe",
        "svp_rpe.melody",
        "svp_rpe.melody.accuracy",
        "svp_rpe.melody.extractors",
        "svp_rpe.melody.observability",
        "svp_rpe.melody.routing",
    ]
    for name in watched:
        monkeypatch.delitem(sys.modules, name, raising=False)

    paths = harness._generator_code_paths()
    assert {p.name for p in paths} >= {"run_melody_accuracy.py", "accuracy.py", "routing.py"}
    assert not any(name in sys.modules for name in watched), sorted(
        name for name in watched if name in sys.modules
    )


def test_scorer_pins_rehash_bypasses_cache() -> None:
    """post-run 検証は再 hash する（size/mtime 据え置きの差し替えを見逃さない）。"""
    cached = harness._scorer_pins()
    fresh = harness._scorer_pins(use_cache=False)
    assert cached == fresh
    assert harness._is_sha256(fresh["mir_eval_code_sha256"])


def test_scorer_pins_do_not_import_mir_eval(monkeypatch) -> None:
    """スコアラー pin は import を起こさずに取れる（load-time 束縛の前提）。"""
    monkeypatch.delitem(sys.modules, "mir_eval", raising=False)
    pins = harness._scorer_pins()
    assert pins["mir_eval_version"]
    assert harness._is_sha256(pins["mir_eval_code_sha256"])
    assert "mir_eval" not in sys.modules


def test_run_accuracy_detects_scorer_change_during_execution(monkeypatch) -> None:
    """実行中に mir_eval が差し替わったら fail-closed（旧スコアラーで測った run）。"""
    monkeypatch.setattr(
        harness, "_LOADED_SCORER_PINS", {"mir_eval_version": "0.0", "mir_eval_code_sha256": "0" * 64}
    )
    with pytest.raises(RuntimeError, match="mir_eval が実行中に差し替わった"):
        harness.run_accuracy(
            categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
        )


def test_run_accuracy_detects_source_change_during_execution(monkeypatch) -> None:
    """実行中にディスクのソースが差し替わったら fail-closed（旧コードを走らせた run）。"""
    monkeypatch.setattr(harness, "_LOADED_GENERATOR_CODE_SHA256", "0" * 64)
    with pytest.raises(RuntimeError, match="実行中に変化"):
        _fake_run(
            categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
        )


@pytest.mark.parametrize(
    ("mutation", "expect"),
    [
        # max_vfa は残す（落とすと必須キー検査が先に発火し、狙いの検査に届かない）。
        ("  S_direct:\n    min_rpa: .nan\n    max_vfa: 0.15\n", "非有限"),
        ("  S_direct:\n    min_rpa: 1.5\n    max_vfa: 0.15\n", "定義域"),
        ("  S_direct:\n    min_rpa: 0.90\n    max_vfa: 0.15\n    bogus_key: 1\n", "未知の閾値キー"),
    ],
)
def test_load_bars_rejects_malformed_thresholds(mutation, expect, tmp_path) -> None:
    """バー自身が未定義値なら読み込み時点で弾く（NaN のバーは比較が常に False）。"""
    original = BARS_PATH.read_text()
    broken = original.replace("  S_direct:                       # 抽出器の健全性バー（落ちたら経路自体を疑う）\n    min_rpa: 0.90\n    max_vfa: 0.15\n", mutation)
    assert broken != original, "fixture の該当ブロックが見つからない（テストの前提が drift）"
    path = tmp_path / "broken_bars.yaml"
    path.write_text(broken)
    with pytest.raises(ValueError, match=expect):
        harness.load_bars(path)


def test_evaluate_m2_bars_rejects_unknown_outcome() -> None:
    """`"failed"` 等の未知 outcome を measured 扱いにしない。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    reports[0]["categories"]["S_direct"]["outcome"] = "failed"
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="未知の outcome"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_preloaded_watch_set_covers_the_digest_closure() -> None:
    """監視集合が digest 閉包（推移的モジュール含む）とランタイムパッケージを覆う。"""
    closure = set(harness._closure_module_names())
    # 閉包は seed だけでなく推移的な first-party モジュールも含む。
    assert "svp_rpe.melody.accuracy" in closure
    assert any(name.startswith("svp_rpe.utils") for name in closure), sorted(closure)
    # 監視対象は閉包 + ランタイムパッケージから導出される。ランタイム側は登録表
    # （provenance）由来なので、crepe 実行スタックの backend も分離器も落ちない。
    runtime = set(harness._runtime_package_names())
    for name in ("mir_eval", "crepe", "tensorflow", "keras", "hmmlearn", "librosa", "resampy"):
        assert name in runtime, sorted(runtime)
    for name in ("demucs", "torch"):
        assert name in runtime, sorted(runtime)


def test_runtime_package_names_are_derived_from_the_provenance_registry() -> None:
    """監視集合は手書きではなく登録表から導出される（登録表を絞れば集合も縮む）。"""
    from svp_rpe.melody import provenance

    runtime = set(harness._runtime_package_names())
    expected = {"mir_eval"}
    for category_spec in harness._CATEGORY_SPECS.values():
        route = harness._select_named_route(
            category_spec["input_kind"], category_spec["route_name"]
        )
        expected.update(provenance.extractor_code_packages_for(route.extractor))
        if route.requires_separation:
            expected.update(provenance.SEPARATION_CODE_PACKAGES)
    assert runtime == expected


def test_runtime_package_names_fail_closed_on_unregistered_extractor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """登録表に載っていない抽出器の route は、黙って監視漏れにせず落とす。"""
    from svp_rpe.melody import provenance

    monkeypatch.setattr(provenance, "extractor_code_packages_for", lambda _extractor: ())
    with pytest.raises(RuntimeError, match="未登録"):
        harness._runtime_package_names()


def test_preloaded_seed_modules_read_the_frozen_snapshot_not_live_sys_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """事前ロード判定はモジュール先頭で凍結したスナップショットに対して行う。

    監視集合の導出には `svp_rpe.melody.provenance`（= digest 閉包の一員）の import が
    必要なので、現在の `sys.modules` を見る実装は自分の import を「事前ロード」として
    数えてしまい、素の CLI run まで publish 不可になる。凍結スナップショットを空に
    差し替えれば、live な sys.modules を読む実装だけが非空を返す。
    """
    assert "svp_rpe.melody.provenance" in set(harness._closure_module_names())
    assert "svp_rpe.melody.provenance" in sys.modules  # 監視集合の導出で必ず読まれる

    monkeypatch.setattr(harness, "_SYS_MODULES_AT_LOAD", frozenset())
    assert harness._preloaded_seed_modules() == []

    monkeypatch.setattr(
        harness,
        "_SYS_MODULES_AT_LOAD",
        frozenset({"svp_rpe.melody.provenance", "zzz_not_a_watched_module"}),
    )
    assert harness._preloaded_seed_modules() == ["svp_rpe.melody.provenance"]


def test_generator_closure_includes_ancestor_package_initializers() -> None:
    """import が必ず実行する祖先 `__init__.py` も generator digest の対象。

    AST 走査は明示 import された名前しか辿らないため、`svp_rpe/__init__.py` 等の
    変更が digest に写らず「別の first-party bytes を実行したのに同一 generator
    provenance」を主張できた（Codex P2 第 32 巡）。
    """
    paths = set(harness._generator_code_paths())
    src = harness.SRC.resolve()
    assert (src / "svp_rpe" / "__init__.py") in paths
    assert (src / "svp_rpe" / "melody" / "__init__.py") in paths
    assert (src / "svp_rpe" / "rpe" / "__init__.py") in paths
    assert (src / "svp_rpe" / "rpe" / "learned" / "__init__.py") in paths
    assert (src / "svp_rpe" / "io" / "__init__.py") in paths


def test_preloaded_parent_packages_are_watched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """トップレベル `svp_rpe` だけの事前 import も preload ゲートに掛かる。

    sys.path の並べ替えは import 済み親パッケージの `__path__` を書き換えないため、
    別 checkout の `svp_rpe` が先にキャッシュされていると子モジュールは外部
    checkout から実行される。子モジュール名だけの監視ではこの親キャッシュが
    素通りする（Codex P1 第 26 巡）。
    """
    monkeypatch.setattr(harness, "_SYS_MODULES_AT_LOAD", frozenset({"svp_rpe"}))
    assert harness._preloaded_seed_modules() == ["svp_rpe"]
    monkeypatch.setattr(
        harness, "_SYS_MODULES_AT_LOAD", frozenset({"svp_rpe.melody"})
    )
    assert harness._preloaded_seed_modules() == ["svp_rpe.melody"]
    monkeypatch.setattr(
        harness, "_SYS_MODULES_AT_LOAD", frozenset({"svp_rpe.rpe.learned"})
    )
    assert harness._preloaded_seed_modules() == ["svp_rpe.rpe.learned"]


@pytest.mark.parametrize(
    ("mutate", "expect"),
    [
        (lambda m: m.update(median_cent_error=-1.0), "定義域"),
        (lambda m: m.update(voiced_chroma_correct_frame_count=-5), "が負"),
        (lambda m: m.update(voiced_chroma_correct_frame_count=1.5), "整数でない"),
        (lambda m: m.update(median_cent_error=None), "矛盾"),
        (lambda m: m.update(tolerance_cents=600.0), "凍結値"),
        # 導出フィールド octave_gap を独立に書き換えた row（RPA 0.91 / RCA 0.10 /
        # gap 0.0 のような不可能な誤差モデル）は関係式で弾く。
        (
            lambda m: m.update(raw_pitch_accuracy=0.91, raw_chroma_accuracy=0.95, octave_gap=0.0),
            "一致しない",
        ),
        # 関係式に整合させても RCA < RPA（gap<0）は mir_eval が返さない組。
        (
            lambda m: m.update(raw_pitch_accuracy=0.91, raw_chroma_accuracy=0.10, octave_gap=-0.81),
            "下回る",
        ),
    ],
)
def test_evaluate_m2_bars_enforces_metrics_contract(mutate, expect) -> None:
    """誤差モデルの不変条件（中央値・母数・ネスト tolerance・導出関係）を再検査する。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    mutate(reports[0]["categories"]["S_direct"]["metrics"])
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match=expect):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


@pytest.mark.parametrize(
    "missing_key",
    ["separation_weights_sha256", "separation_code_sha256", "stem_sha256"],
)
def test_evaluate_m2_bars_requires_separation_digests_for_fullstack(missing_key) -> None:
    """分離経路は分離器と stem も pin されていなければ証拠にしない。"""
    reports = [
        _fake_run(categories=("S_fullstack",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    for report in reports:
        del report["categories"]["S_fullstack"]["provenance_preprocessing"][missing_key]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match=missing_key):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_evaluate_m2_bars_requires_preprocessing_block_for_fullstack() -> None:
    reports = [
        _fake_run(categories=("S_fullstack",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    for report in reports:
        del report["categories"]["S_fullstack"]["provenance_preprocessing"]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="provenance_preprocessing"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_evaluate_m2_bars_rejects_nan_metrics_instead_of_passing() -> None:
    """NaN は全比較が False になり pass を偽造するため、閾値判定前に拒否する。"""
    reports = [
        _fake_run(
            categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
        )
        for _ in range(2)
    ]
    reports[0]["categories"]["S_direct"]["metrics"]["raw_pitch_accuracy"] = float("nan")
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="非有限"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_json_loader_rejects_nan_literal() -> None:
    """`json.loads` 既定で通る NaN リテラルを artifact 段階で弾く。"""
    with pytest.raises(ValueError, match="非有限リテラル"):
        harness._json_loads_no_dup_keys('{"raw_pitch_accuracy": NaN}', what="test")


def test_evaluate_m2_bars_rejects_metric_outside_domain() -> None:
    reports = [
        _fake_run(
            categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
        )
        for _ in range(2)
    ]
    reports[1]["categories"]["S_direct"]["metrics"]["voicing_false_alarm"] = 1.5
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="定義域"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


@pytest.mark.parametrize(
    ("field", "bogus"),
    [
        ("route", "some_other_route"),
        ("input_kind", "full_mix"),
        ("waveform_sha256", "f" * 64),
    ],
)
def test_evaluate_m2_bars_rejects_row_identity_mismatch(field: str, bogus: str) -> None:
    """ラベルだけが S_direct の row（別 fixture/経路・編集済み）にバーを適用しない。"""
    reports = [
        _fake_run(
            categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
        )
        for _ in range(2)
    ]
    reports[0]["categories"]["S_direct"][field] = bogus
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match=field):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_evaluate_m2_bars_rejects_unregistered_category_label() -> None:
    reports = [
        _fake_run(
            categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
        )
        for _ in range(2)
    ]
    for report in reports:
        report["categories"]["S_bogus"] = dict(report["categories"]["S_direct"])
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="未知の category|未登録の category"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_cli_rejects_out_path_colliding_with_protected_inputs(tmp_path, monkeypatch) -> None:
    """`--out` が report / bars / specs を指したら書く前に停止する。"""
    report_path = tmp_path / "run1.json"
    report = _fake_run(
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


@pytest.mark.parametrize(
    "source_rel",
    ["scripts/run_melody_accuracy.py", "src/svp_rpe/melody/accuracy.py"],
)
def test_cli_rejects_out_path_overwriting_hashed_sources(source_rel, tmp_path, monkeypatch) -> None:
    """provenance のために hash するソースを `--out` で潰させない（run/evaluate 両モード）。"""
    source_path = ROOT / source_rel
    before = source_path.read_bytes()

    monkeypatch.setattr(sys, "argv", ["run_melody_accuracy.py", "--out", str(source_path)])
    with pytest.raises(SystemExit, match="provenance"):
        harness.main()
    assert source_path.read_bytes() == before

    report_path = tmp_path / "run1.json"
    report_path.write_text(
        json.dumps(
            _fake_run(
                categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
            )
        )
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_melody_accuracy.py", "--evaluate", str(report_path), "--out", str(source_path)],
    )
    with pytest.raises(SystemExit, match="provenance"):
        harness.main()
    assert source_path.read_bytes() == before


def test_evaluate_m2_bars_records_generator_code_sha256_in_verdict() -> None:
    report1 = _fake_run(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    report2 = _fake_run(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    verdict = harness.evaluate_m2_bars(
            [_as_report_artifact(report1), _as_report_artifact(report2)],
            bars,
            bars_sha256=bars_sha256,
        )
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


# ---------------------------------------------------------------------------
# 推定 voicing の符号化（Codex P1）: CREPE は無声フレームでも最尤 F0 を正値で返す。
# frame_hz をそのまま採点すると VFA が事実上 1.0 に張り付く。
# ---------------------------------------------------------------------------


def _make_crepe_style_runner(*, voiced_confidence: float, unvoiced_confidence: float):
    """CREPE の契約を模したフェイク抽出器。

    `extract_crepe_f0` と同じく **全フレームで正の最尤 F0** を返し、有声の証拠は
    `frame_confidence` に分離する（正解が無音の区間でも直前のノートの F0 を返し続ける
    ——CREPE の実挙動を保守的に模した最悪ケース）。
    """
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
        times, freqs = reference_f0_from_monophonic_spec(
            specs["fixtures"][melody_id], sample_rate=int(specs["sample_rate"])
        )
        # 無声フレームも「直前の有声 F0」で埋める（0 を返さない = CREPE の契約）。
        filled: list = []
        last = 440.0
        confidences: list = []
        for hz in freqs:
            if hz > 0.0:
                last = hz
                filled.append(hz)
                confidences.append(voiced_confidence)
            else:
                filled.append(last)
                confidences.append(unvoiced_confidence)
        observation = MelodyObservation(
            route=route.name,
            source_model="fake:crepe-style",
            frame_times=times,
            frame_hz=tuple(filled),
            frame_confidence=tuple(confidences),
            total_duration_sec=times[-1] if times else 0.0,
        )
        provenance: Dict[str, Any] = {
            "extractor_weights_sha256": FAKE_WEIGHTS_SHA256,
            "extractor_code_sha256": FAKE_CODE_SHA256,
        }
        if route.preprocessing:
            provenance["preprocessing"] = {
                "preprocessing": route.preprocessing,
                "separation_model": "fake-htdemucs",
                "separation_version": "0.0-fake",
                "separation_weights_sha256": FAKE_SEP_WEIGHTS_SHA256,
                "separation_code_sha256": FAKE_SEP_CODE_SHA256,
                "stem_sha256": FAKE_STEM_SHA256,
            }
        return observation, provenance

    return _runner


def test_est_freqs_with_voicing_encodes_confidence_as_mir_eval_sign() -> None:
    """confidence < floor のフレームは負値（mir_eval の「無声だが推定値はこれ」）。"""
    observation = MelodyObservation(
        route="crepe_direct",
        source_model="fake",
        frame_times=(0.0, 0.01, 0.02, 0.03),
        frame_hz=(440.0, 440.0, 0.0, 220.0),
        frame_confidence=(0.9, 0.1, 0.9, 0.30),
    )
    signed = harness._est_freqs_with_voicing(observation, confidence_floor=0.30)
    # 閾値以上は正、未満は負、hz==0 は 0.0（推定値そのものが無い）、境界は有声側。
    assert signed == (440.0, -440.0, 0.0, 220.0)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.1, 1.5])
def test_est_freqs_with_voicing_rejects_invalid_confidence(bad: float) -> None:
    """非有限 / [0,1] 外の confidence を voicing 判定へ黙って変換しない。

    `NaN >= floor` は False なので、不正な confidence は「無声と予測」（負周波数）に
    化け、VFA を人工的に下げて凍結バーを通しうる（Codex P2 第 30 巡）。
    """
    observation = MelodyObservation(
        route="crepe_direct",
        source_model="fake",
        frame_times=(0.0, 0.01),
        frame_hz=(440.0, 440.0),
        frame_confidence=(0.9, bad),
    )
    with pytest.raises(ValueError, match=r"frame_confidence\[1\]"):
        harness._est_freqs_with_voicing(observation, confidence_floor=0.30)


def test_crepe_style_run_does_not_pin_vfa_at_one() -> None:
    """CREPE 型の出力（無声でも正の F0）で VFA が飽和しないこと。

    これが Codex P1 の核心: 変換前は正解が無音の全フレームが false alarm に数えられ、
    凍結 `max_vfa: 0.15` を精度の良い run でも落としていた。
    """
    bars, _ = harness.load_bars(BARS_PATH)
    floor = float(bars["m2_accuracy_bars"]["est_voiced_confidence_floor"])
    report = _fake_run(
        categories=("S_direct",),
        route_runner=_make_crepe_style_runner(
            voiced_confidence=0.95, unvoiced_confidence=floor / 2.0
        ),
    )
    row = report["categories"]["S_direct"]
    metrics = row["metrics"]

    # 有声フレームは全て正解 → RPA は 1.0（符号化は RPA/RCA を変えない）。
    assert metrics["raw_pitch_accuracy"] == pytest.approx(1.0)
    # 無声フレームは負値化され false alarm にならない。
    assert metrics["voicing_false_alarm"] == pytest.approx(0.0, abs=1e-9)
    assert metrics["voicing_recall"] == pytest.approx(1.0)
    assert metrics["voicing_false_alarm"] <= bars["m2_accuracy_bars"]["S_direct"]["max_vfa"]
    # 推定側の有声フレーム数が正解の有声フレーム数と一致する（監査可能な記録）。
    assert row["est_voiced_frame_count"] == row["ref_voiced_frame_count"]
    assert row["est_frame_count"] == row["ref_frame_count"]


def test_crepe_style_run_would_saturate_vfa_without_the_encoding() -> None:
    """符号化を外した場合（= 修正前の挙動）に VFA が飽和することを対照で示す。"""
    from svp_rpe.melody.accuracy import evaluate_melody_accuracy

    specs, _ = harness.load_specs(SPECS_PATH)
    times, ref_freqs = reference_f0_from_monophonic_spec(
        specs["fixtures"]["m2_s_direct_melody"], sample_rate=int(specs["sample_rate"])
    )
    filled: list = []
    last = 440.0
    for hz in ref_freqs:
        if hz > 0.0:
            last = hz
        filled.append(last)
    unencoded = evaluate_melody_accuracy(times, ref_freqs, times, tuple(filled))
    assert unencoded.voicing_false_alarm == pytest.approx(1.0)


def test_load_bars_requires_registered_est_voiced_confidence_floor(tmp_path: Path) -> None:
    raw = BARS_PATH.read_text(encoding="utf-8")
    # 閾値の**宣言行だけ**を落とす（amendments の `added:` 記録は残す——そちらを消すと
    # 登録日検査が先に落ちて、この関所を確かめられない）。
    stripped = raw.replace("  est_voiced_confidence_floor: 0.30\n", "")
    assert stripped != raw
    assert "added: [est_voiced_confidence_floor]" in stripped
    path = tmp_path / "bars_without_floor.yaml"
    path.write_text(stripped, encoding="utf-8")
    with pytest.raises(ValueError, match="est_voiced_confidence_floor が無い"):
        harness.load_bars(path)


@pytest.mark.parametrize("bad", ["1.5", "-0.1", ".nan", "'0.3'"])
def test_load_bars_rejects_out_of_domain_est_voiced_confidence_floor(
    tmp_path: Path, bad: str
) -> None:
    raw = BARS_PATH.read_text(encoding="utf-8")
    patched = raw.replace("est_voiced_confidence_floor: 0.30", f"est_voiced_confidence_floor: {bad}")
    assert patched != raw
    path = tmp_path / "bars_bad_floor.yaml"
    path.write_text(patched, encoding="utf-8")
    with pytest.raises(ValueError, match="est_voiced_confidence_floor"):
        harness.load_bars(path)


@pytest.mark.parametrize(
    ("mutate", "expect"),
    [
        (lambda r: r.pop("est_voiced_confidence_floor"), "を欠く"),
        (lambda r: r.update(est_voiced_confidence_floor=0.05), "凍結値"),
    ],
)
def test_evaluate_m2_bars_requires_frozen_est_voicing_floor(mutate, expect) -> None:
    """緩い有声判定閾値で測った row に凍結 max_vfa を適用させない。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    mutate(reports[0])
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match=expect):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_verdict_records_the_frozen_est_voicing_floor() -> None:
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    verdict = harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )
    assert verdict["est_voiced_confidence_floor"] == pytest.approx(
        float(bars["m2_accuracy_bars"]["est_voiced_confidence_floor"])
    )


# ---------------------------------------------------------------------------
# 誤差モデルの母数の上界（Codex P2）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mutate", "expect"),
    [
        (
            lambda row: row["metrics"].update(voiced_chroma_correct_frame_count=1_000_000_000),
            "を超える",
        ),
        (lambda row: row.update(ref_frame_count="470"), "整数でない"),
        (lambda row: row.update(ref_voiced_frame_count=-1), "不一致"),
        # 母数と上界を**揃えて**膨らませても、上界は凍結 spec から再計算されるので通らない。
        (
            lambda row: (
                row["metrics"].update(voiced_chroma_correct_frame_count=1_000_000_000),
                row.update(ref_frame_count=1_000_000_000, ref_voiced_frame_count=1_000_000_000),
            ),
            "不一致",
        ),
    ],
)
def test_evaluate_m2_bars_bounds_median_sample_count_by_reference(mutate, expect) -> None:
    """`voiced_chroma_correct_frame_count` は凍結 spec 由来の正解フレーム数を超えられない。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    for report in reports:  # repeats 間の bit 一致は保ったまま母数だけ不正にする
        mutate(report["categories"]["S_direct"])
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match=expect):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_evaluate_m2_bars_rejects_more_voiced_than_total_reference_frames() -> None:
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    for report in reports:
        row = report["categories"]["S_direct"]
        row["ref_frame_count"] = row["ref_voiced_frame_count"] - 1
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="ref_frame_count"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_reference_counts_are_recomputed_from_the_frozen_specs() -> None:
    """上界は row の自己申告ではなく凍結 spec から組み直した値である。"""
    specs, _ = harness.load_specs(SPECS_PATH)
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    expected = harness._registered_reference_counts("S_direct", bars, specs)
    verdict = harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )
    recorded = verdict["categories"]["S_direct"]["reference_frame_counts"]
    assert (recorded["ref_frame_count"], recorded["ref_voiced_frame_count"]) == expected
    assert recorded["source"] == "recomputed_from_frozen_specs"
    # run 側が row に刻んだ値とも一致する（run と evaluate が同じ関数を使う）。
    row = reports[0]["categories"]["S_direct"]
    assert (row["ref_frame_count"], row["ref_voiced_frame_count"]) == expected


def test_evaluate_m2_bars_rejects_reports_declaring_another_specs_generation() -> None:
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    reports[0]["specs_sha256"] = hashlib.sha256(b"another-specs").hexdigest()
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="specs_sha256"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


# ---------------------------------------------------------------------------
# 空バーで受け入れゲートを無効化させない（Codex P2）
# ---------------------------------------------------------------------------


def test_load_bars_rejects_empty_gate_for_non_diagnostic_category(tmp_path: Path) -> None:
    """`--bars` が S_direct の閾値を落としただけで判定が消える経路を塞ぐ。"""
    raw = BARS_PATH.read_text(encoding="utf-8")
    patched = raw.replace(
        "  S_direct:                       # 抽出器の健全性バー（落ちたら経路自体を疑う）\n"
        "    min_rpa: 0.90\n"
        "    max_vfa: 0.15\n",
        "  S_direct: {}\n",
    )
    assert patched != raw
    path = tmp_path / "bars_empty_s_direct.yaml"
    path.write_text(patched, encoding="utf-8")
    with pytest.raises(ValueError, match="空/欠落"):
        harness.load_bars(path)


def test_load_bars_rejects_partial_gate_missing_max_vfa(tmp_path: Path) -> None:
    """min_rpa だけの部分バーで凍結済み max_vfa ゲートが黙って消える経路を塞ぐ。

    設計 §4 の S_direct 登録は min_rpa と max_vfa の対。片方だけ残した bars は
    「バーを触らずにゲートの一部を無効化する」のと同じ（Codex P2 第 22 巡）。
    """
    raw = BARS_PATH.read_text(encoding="utf-8")
    patched = raw.replace("    max_vfa: 0.15\n", "")
    assert patched != raw
    path = tmp_path / "bars_partial_s_direct.yaml"
    path.write_text(patched, encoding="utf-8")
    with pytest.raises(ValueError, match=r"max_vfa"):
        harness.load_bars(path)


def test_load_bars_rejects_thresholds_on_diagnostic_only_category(tmp_path: Path) -> None:
    raw = BARS_PATH.read_text(encoding="utf-8")
    patched = raw.replace(
        "  S_fullstack: {}", "  S_fullstack:\n    min_rpa: 0.10"
    )
    assert patched != raw
    path = tmp_path / "bars_gated_fullstack.yaml"
    path.write_text(patched, encoding="utf-8")
    with pytest.raises(ValueError, match="診断専用"):
        harness.load_bars(path)


def _artifact_from_yaml_text(text: str) -> Tuple[Any, str]:
    """`load_bars` の検証を通さずに **整合した** BarsArtifact を組む（テスト専用）。

    raw / digest / parsed data は互いに整合させる（束縛検査は通る）ので、
    `load_bars` の loader 検査より後ろにある関所を単体で試せる。
    """
    raw = text.encode("utf-8")
    data = harness._yaml_load_no_dup_keys(raw, what="m2_accuracy_bars.yaml")
    sha256 = hashlib.sha256(raw).hexdigest()
    return harness.BarsArtifact(data, sha256, raw), sha256


def test_evaluate_m2_bars_refuses_diagnostic_only_for_gated_category() -> None:
    """`load_bars` を経由しない経路でも、空バーの S_direct を diagnostic_only にしない。"""
    raw = BARS_PATH.read_text(encoding="utf-8")
    patched = raw.replace(
        "  S_direct:                       # 抽出器の健全性バー（落ちたら経路自体を疑う）\n"
        "    min_rpa: 0.90\n"
        "    max_vfa: 0.15\n",
        "  S_direct: {}\n",
    )
    assert patched != raw
    bars, bars_sha256 = _artifact_from_yaml_text(patched)
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    for report in reports:  # 手組み bars に合わせて pin を揃える（関門を先に通す）
        report["bars_sha256"] = bars_sha256
    with pytest.raises(ValueError, match="診断専用ではない"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


# ---------------------------------------------------------------------------
# バー digest と parsed data の束縛（Codex P2）
# ---------------------------------------------------------------------------


def test_evaluate_m2_bars_rejects_bars_mutated_after_load() -> None:
    """閾値を下げたまま元の凍結 digest を名乗る verdict を publish させない。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    bars["m2_accuracy_bars"]["S_direct"]["min_rpa"] = 0.01  # 凍結バーの実質的な緩和
    with pytest.raises(ValueError, match="load 後に変異"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_evaluate_m2_bars_rejects_plain_dict_bars() -> None:
    """parsed 閾値と digest が切り離された入力（素の dict）は受理しない。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="BarsArtifact でなければならない"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports],
            dict(bars.data),
            bars_sha256=bars_sha256,
        )


def test_evaluate_m2_bars_rejects_mismatched_bars_sha256_argument() -> None:
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    bars, _bars_sha256 = harness.load_bars(BARS_PATH)
    other = hashlib.sha256(b"another-bars").hexdigest()
    for report in reports:
        report["bars_sha256"] = other
    with pytest.raises(ValueError, match="アーティファクトの digest"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=other
        )


def test_bars_artifact_detects_digest_tampering() -> None:
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    tampered = harness.BarsArtifact(
        bars.data, hashlib.sha256(b"wrong").hexdigest(), BARS_PATH.read_bytes()
    )
    with pytest.raises(ValueError, match="raw bytes の hash"):
        tampered.verify()
    # 正しいアーティファクトは verify を通り、parsed data をそのまま返す。
    assert bars.verify(bars_sha256) is bars.data


# ---------------------------------------------------------------------------
# repeats_min の下限（Codex P2）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["1", "0"])
def test_load_bars_rejects_repeats_min_below_two(tmp_path: Path, bad: str) -> None:
    """`repeats_min: 1` は単一 report での pass を許すため loader で弾く。"""
    raw = BARS_PATH.read_text(encoding="utf-8")
    patched = raw.replace("repeats_min: 2", f"repeats_min: {bad}")
    assert patched != raw
    path = tmp_path / "bars_repeats_one.yaml"
    path.write_text(patched, encoding="utf-8")
    with pytest.raises(ValueError, match="repeats_min"):
        harness.load_bars(path)


def test_registered_bars_require_two_repeats() -> None:
    bars, _ = harness.load_bars(BARS_PATH)
    assert int(bars["m2_accuracy_bars"]["repeats_min"]) >= 2


# ---------------------------------------------------------------------------
# CLI: evaluate モードへ --specs を転送する（Codex P2）
# ---------------------------------------------------------------------------


def test_cli_evaluate_forwards_specs_path(tmp_path: Path, monkeypatch) -> None:
    """`--specs` が evaluate へ渡らないと、カスタム spec の report が pin 不一致で落ちる。"""
    custom_specs = tmp_path / "custom_specs.yaml"
    custom_specs.write_text(SPECS_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({"stub": True}), encoding="utf-8")
    out_path = tmp_path / "verdict.json"

    captured: Dict[str, Any] = {}

    def _fake_evaluate(reports, bars, **kwargs):
        captured["specs_path"] = kwargs.get("specs_path")
        return {"categories": {}}

    monkeypatch.setattr(harness, "evaluate_m2_bars", _fake_evaluate)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_melody_accuracy.py",
            "--evaluate",
            str(report_path),
            "--out",
            str(out_path),
            "--specs",
            str(custom_specs),
        ],
    )
    assert harness.main() == 0
    assert captured["specs_path"] == custom_specs


def test_s_fullstack_remains_diagnostic_only() -> None:
    """診断専用カテゴリは従来どおり判定せず記録のみ（設計 §8）。"""
    reports = [
        _fake_run(categories=("S_fullstack",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    verdict = harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )
    assert verdict["categories"]["S_fullstack"]["status"] == "diagnostic_only"


# ---------------------------------------------------------------------------
# run report の schema discriminator（Codex P2）
# ---------------------------------------------------------------------------


def test_run_report_declares_its_schema_version() -> None:
    report = _fake_run(categories=("S_direct",), route_runner=_make_fake_runner())
    assert report["schema_version"] == harness._EXPECTED_REPORT_SCHEMA


@pytest.mark.parametrize(
    ("mutate", "expect"),
    [
        (lambda r: r.pop("schema_version"), "schema_version を欠く"),
        (lambda r: r.update(schema_version="m2-accuracy-report/9.9"), "未知"),
    ],
)
def test_evaluate_m2_bars_rejects_unknown_report_schema(mutate, expect) -> None:
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    mutate(reports[0])
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match=expect):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_measured_rows_report_counts_within_the_reference_bound() -> None:
    """実際の run が出す母数が上界内に収まる（検査が偽陽性を出さないことの確認）。"""
    report = _fake_run(route_runner=_make_fake_runner(shift_cents=10.0))
    for category, row in report["categories"].items():
        count = row["metrics"]["voiced_chroma_correct_frame_count"]
        assert 0 < count <= row["ref_voiced_frame_count"] <= row["ref_frame_count"], (
            category,
            row["metrics"],
        )


# ---------------------------------------------------------------------------
# verdict publish 前の load-time pin 検証（Codex P1）
# ---------------------------------------------------------------------------


def test_evaluate_m2_bars_refuses_to_publish_when_first_party_source_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """メモリ上の旧 evaluator が走りつつ新しいディスク bytes を名乗る窓を塞ぐ。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    monkeypatch.setattr(
        harness, "_LOADED_GENERATOR_CODE_SHA256", hashlib.sha256(b"stale").hexdigest()
    )
    with pytest.raises(RuntimeError, match="first-party ソースが実行中に変化した"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_evaluate_m2_bars_refuses_to_publish_when_scorer_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    monkeypatch.setattr(
        harness,
        "_LOADED_SCORER_PINS",
        {"mir_eval_version": "0.0-stale", "mir_eval_code_sha256": None},
    )
    with pytest.raises(RuntimeError, match="mir_eval が実行中に差し替わった"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


# ---------------------------------------------------------------------------
# 合成仕様のスキーマ discriminator（Codex P2）
# ---------------------------------------------------------------------------


def test_registered_specs_declare_their_schema_version() -> None:
    specs, _ = harness.load_specs(SPECS_PATH)
    assert specs["schema_version"] == harness._EXPECTED_SPECS_SCHEMA


@pytest.mark.parametrize(
    "mutate",
    [
        lambda text: text.replace('schema_version: "m2-accuracy-specs/0.1"\n', ""),
        lambda text: text.replace("m2-accuracy-specs/0.1", "m2-accuracy-specs/9.9"),
    ],
)
def test_load_specs_rejects_missing_or_unknown_schema_version(tmp_path: Path, mutate) -> None:
    raw = SPECS_PATH.read_text(encoding="utf-8")
    patched = mutate(raw)
    assert patched != raw
    path = tmp_path / "specs_bad_schema.yaml"
    path.write_text(patched, encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        harness.load_specs(path)


# ---------------------------------------------------------------------------
# 母数と RCA の整合（Codex P2）: 母数は RCA の分子そのもの。
# ---------------------------------------------------------------------------


def test_evaluate_m2_bars_ties_chroma_correct_count_to_rca() -> None:
    """`RCA=1.0` かつ `count=1` のような矛盾した誤差モデルを受理しない。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    for report in reports:  # bit 一致は保ったまま母数だけ矛盾させる
        metrics = report["categories"]["S_direct"]["metrics"]
        assert metrics["raw_chroma_accuracy"] == pytest.approx(1.0)
        metrics["voiced_chroma_correct_frame_count"] = 1
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="復元される分子"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_measured_rows_satisfy_the_rca_numerator_identity() -> None:
    """実 run の値は恒等式 `count == RCA × 有声フレーム数` を満たす（偽陽性なし）。"""
    specs, _ = harness.load_specs(SPECS_PATH)
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    report = _fake_run(route_runner=_make_fake_runner(shift_cents=10.0))
    for category, row in report["categories"].items():
        _frames, voiced = harness._registered_reference_counts(category, bars.data, specs)
        implied = float(row["metrics"]["raw_chroma_accuracy"]) * voiced
        # 恒等式は厳密（fp 誤差のみ）。1 フレーム許容は第 18 巡で撤回した。
        assert row["metrics"]["voiced_chroma_correct_frame_count"] == pytest.approx(
            implied, abs=0.5
        ), (category, row["metrics"], voiced)


# ---------------------------------------------------------------------------
# verdict の schema discriminator（Codex P2）
# ---------------------------------------------------------------------------


def test_verdict_declares_its_schema_version() -> None:
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    verdict = harness.evaluate_m2_bars(
        [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
    )
    assert verdict["schema_version"] == harness._EXPECTED_VERDICT_SCHEMA


# ---------------------------------------------------------------------------
# バー artifact の登録日（Codex P2）: 「実測前に凍結した」主張の土台。
# ---------------------------------------------------------------------------


def test_registered_bars_carry_a_valid_registration_date() -> None:
    bars, _ = harness.load_bars(BARS_PATH)
    assert harness._parse_registered_utc(
        bars["registered_utc"], where="test"
    ) <= datetime.now(timezone.utc)


@pytest.mark.parametrize(
    ("replacement", "expect"),
    [
        ('registered_utc: "2026-07-25"\n', "registered_utc が無い"),          # 欠落
        ('registered_utc: "not-a-date"\n', "でもない"),                        # 不正形式
        ('registered_utc: "2099-01-01"\n', "未来"),                            # 未来
        ('registered_utc: "2026-07-25T00:00:00+09:00"\n', "UTC でない"),       # 非 UTC
    ],
)
def test_load_bars_rejects_undated_or_invalid_registration(
    tmp_path: Path, replacement: str, expect: str
) -> None:
    raw = BARS_PATH.read_text(encoding="utf-8")
    if expect == "registered_utc が無い":
        patched = raw.replace('registered_utc: "2026-07-25"\n', "", 1)
    else:
        patched = raw.replace('registered_utc: "2026-07-25"\n', replacement, 1)
    assert patched != raw
    path = tmp_path / "bars_bad_registration.yaml"
    path.write_text(patched, encoding="utf-8")
    with pytest.raises(ValueError, match=expect):
        harness.load_bars(path)


@pytest.mark.parametrize(
    ("mutate", "expect"),
    [
        # amendment が元の凍結より前 = 履歴の捏造
        (lambda raw: raw.replace('- registered_utc: "2026-07-26"', '- registered_utc: "2026-07-01"'),
         "より前"),
        # amendment の日付が未来
        (lambda raw: raw.replace('- registered_utc: "2026-07-26"', '- registered_utc: "2099-01-01"'),
         "未来"),
        # 何を追加したか辿れない amendment
        (lambda raw: raw.replace("    added: [est_voiced_confidence_floor]\n", "    added: []\n"),
         "added"),
    ],
)
def test_load_bars_validates_amendment_dates(tmp_path: Path, mutate, expect: str) -> None:
    raw = BARS_PATH.read_text(encoding="utf-8")
    patched = mutate(raw)
    assert patched != raw
    path = tmp_path / "bars_bad_amendment.yaml"
    path.write_text(patched, encoding="utf-8")
    with pytest.raises(ValueError, match=expect):
        harness.load_bars(path)


# ---------------------------------------------------------------------------
# 第 15 巡: evaluate プロセスの preload ゲート / 型強制の拒否 / 登録日前の report
# ---------------------------------------------------------------------------


def test_evaluate_m2_bars_rejects_preloaded_evaluator_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """監視モジュールが先に import 済みのプロセスから verdict を publish しない。

    ディスク不変の検査（`_require_unchanged_since_load`）では捕まらないケース:
    旧モジュールを import → checkout 更新 → ハーネス import では、load 時 digest も
    実行後 digest も**新しい**ディスクを見るのに、評価はキャッシュ済みの旧コードで
    走る。run 側は `preloaded_seed_modules` の拒否で守られているが、evaluate 側の
    プロセス自身にも同じゲートが要る（Codex P1）。
    """
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    monkeypatch.setattr(
        harness, "_PRELOADED_SEED_MODULES", ["svp_rpe.melody.accuracy"]
    )
    with pytest.raises(RuntimeError, match="先に import 済み"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


@pytest.mark.parametrize(
    ("mutate", "expect"),
    [
        # 文字列 "50" は float("50") で黙って正規化されていた（Codex P2）。
        (lambda r: r.update(tolerance_cents="50"), "数値でない"),
        (lambda r: r.update(est_voiced_confidence_floor="0.30"), "数値でない"),
        (lambda r: r["categories"]["S_direct"]["metrics"].update(tolerance_cents="50.0"),
         "数値でない"),
        (lambda r: r.update(tolerance_cents=True), "数値でない"),  # bool は int の subclass
    ],
)
def test_evaluate_m2_bars_rejects_coerced_numeric_fields(mutate, expect) -> None:
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    mutate(reports[0])
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match=expect):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_evaluate_m2_bars_rejects_reports_predating_bar_registration() -> None:
    """バーの最新登録時点より前に測ったと申告する report を証拠にしない。

    例: `est_voiced_confidence_floor` は 2026-07-26 に追加登録された。2026-07-25 の
    recorded_utc を名乗る report がその閾値の下で pass するのは、実測後に選んだ
    閾値を「事前登録済み」として提示するのと同じ（Codex P2）。
    """
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    for report in reports:
        # 開始・完了とも最新 amendment (07-26) より前（順序検査ではなく登録時点検査に到達させる）。
        report["started_utc"] = "2026-07-25T11:00:00+00:00"
        report["recorded_utc"] = "2026-07-25T12:00:00+00:00"
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="測定の開始時点|最新登録時点"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_fresh_reports_postdate_the_bar_registration() -> None:
    """実 run の recorded_utc は登録時点以降なので、この関所は偽陽性を出さない。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    verdict = harness.evaluate_m2_bars(
        [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
    )
    assert verdict["categories"]["S_direct"]["status"] == "pass"


# ---------------------------------------------------------------------------
# 第 16 巡: 測定開始時点の検証（Codex P2）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mutate", "expect"),
    [
        # 登録（07-26）前に測り始め、登録後に完了した run — 完了時刻だけでは通っていた。
        (lambda r: r.update(started_utc="2026-07-25T23:00:00+00:00"), "測定の開始時点"),
        # 開始が完了より後 = 成立しない測定記録。
        (lambda r: r.update(started_utc="2099-01-01T00:00:00+00:00"), "未来"),
        (lambda r: r.pop("started_utc"), "started_utc が無い"),
        (lambda r: r.update(started_utc="not-a-timestamp"), "ISO 8601"),
    ],
)
def test_evaluate_m2_bars_validates_measurement_start(mutate, expect) -> None:
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    mutate(reports[0])
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match=expect):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_evaluate_m2_bars_rejects_start_after_completion() -> None:
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    # 開始を完了の 1 秒後へ（未来チェックに当たらない過去の時刻同士で順序だけ壊す）。
    recorded = reports[0]["recorded_utc"]
    from datetime import datetime, timedelta
    late = (datetime.fromisoformat(recorded) + timedelta(seconds=1)).isoformat()
    reports[0]["started_utc"] = late
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    # recorded が「今」に近い場合、+1 秒は未来検査（同じく fail-closed）に先に当たる。
    # どちらの関門でも拒否されることが本質なので両方を受理する。
    with pytest.raises(ValueError, match="より後|未来"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_design_doc_referenced_by_the_m2_layer_is_committed() -> None:
    """コード・fixture が引く設計書パスが実在する（参照だけの幽霊 doc を残さない）。"""
    doc = ROOT / "docs" / "DESIGN_M2_extraction_accuracy.md"
    assert doc.is_file()
    text = doc.read_text(encoding="utf-8")
    # 引用されている節が実在することまで確認（§2 指標 / §4 バー / §7 PR 分割 / §8 禁止事項）。
    for marker in ("## 2. 指標", "## 4. 事前登録バー", "## 7. PR 分割", "## 8. やってはいけないこと"):
        assert marker in text, marker


# ---------------------------------------------------------------------------
# 第 17 巡: 空カテゴリ選択の拒否（Codex P2）
# ---------------------------------------------------------------------------


def test_run_accuracy_rejects_empty_category_selection() -> None:
    """CREPE を呼ばない「測定ゼロ report」を publishable にしない。"""
    with pytest.raises(ValueError, match="categories が空"):
        harness.run_accuracy(categories=(), route_runner=_make_fake_runner())


def test_evaluate_m2_bars_rejects_reports_without_any_category_rows() -> None:
    """手組みで `categories: {}` を名乗る report にも evaluate 側で独立に落とす。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    for report in reports:
        report["categories"] = {}
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="測定ゼロ"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


# ---------------------------------------------------------------------------
# 第 18 巡: RCA 分子の厳密一致 / WAV 直列化と pin の束縛（Codex P2×2）
# ---------------------------------------------------------------------------


def test_evaluate_m2_bars_rejects_off_by_one_rca_numerator() -> None:
    """`RCA=1.0` のまま count を total−1 にした矛盾 row（旧・1 フレーム許容の穴）を拒否。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    for report in reports:  # bit 一致は保ったまま両 repeats を同じ矛盾値へ
        row = report["categories"]["S_direct"]
        assert row["metrics"]["raw_chroma_accuracy"] == pytest.approx(1.0)
        row["metrics"]["voiced_chroma_correct_frame_count"] = (
            row["ref_voiced_frame_count"] - 1
        )
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="一致しない"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_run_rows_pin_the_serialized_wav_bytes() -> None:
    """row が in-memory 波形 pin に加えて、抽出器が消費した WAV bytes の pin を持つ。"""
    report = _fake_run(categories=("S_direct",), route_runner=_make_fake_runner())
    row = report["categories"]["S_direct"]
    assert harness._is_sha256(row["input_wav_sha256"])
    assert row["input_wav_sha256"] != row["waveform_sha256"]  # bytes 形式が違う（WAV コンテナ vs 生サンプル）


def test_run_accuracy_rejects_wav_swapped_during_extraction() -> None:
    """抽出中に入力 WAV を差し替えると publish 前に落ちる（pre/post digest 束縛）。"""

    def _swapping_runner(audio_path: str, route):
        Path(audio_path).write_bytes(b"not-a-wav-anymore")
        return _make_fake_runner(shift_cents=10.0)(audio_path, route)

    with pytest.raises(RuntimeError, match="差し替えられた"):
        harness.run_accuracy(categories=("S_direct",), route_runner=_swapping_runner)


# ---------------------------------------------------------------------------
# 第 19 巡: 未登録カテゴリの前段拒否 / WAV inode 束縛 / est-unvoiced と恒等式
# ---------------------------------------------------------------------------


def test_evaluate_m2_bars_rejects_unknown_category_before_shortcuts() -> None:
    """未知カテゴリ（unavailable row のみ）を insufficient_repeats として記録させない。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    for report in reports:
        report["categories"]["X"] = {"outcome": "unavailable", "detail": "forbidden"}
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="未登録の category"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_run_accuracy_rejects_wav_renamed_during_extraction() -> None:
    """rename による差し替え（inode 変更）も post 検査で落ちる。

    保持 fd の re-hash は inode の中身しか見ないため、rename には path↔inode の
    照合が要る。temp dir は抽出中 0o500 だが、テストは意図的に chmod で緩めて
    rename を通す = 「明示 chmod まで行う攻撃者」でも検出はされることの確認。
    """

    def _renaming_runner(audio_path: str, route):
        result = _make_fake_runner(shift_cents=10.0)(audio_path, route)
        path = Path(audio_path)
        os.chmod(path.parent, 0o700)  # 0o500 の rename 阻止を意図的に解除
        replacement = path.parent / "replacement.wav"
        replacement.write_bytes(path.read_bytes())
        os.replace(replacement, path)  # 同内容だが別 inode
        return result

    with pytest.raises(RuntimeError, match="別 inode"):
        harness.run_accuracy(categories=("S_direct",), route_runner=_renaming_runner)


def test_low_confidence_frames_do_not_break_the_rca_identity() -> None:
    """est-unvoiced（負値エンコード）でも count == RCA × voiced の恒等式は成立する。

    mir_eval の RPA/RCA は MIREX 規約どおり推定 voicing を無視する（実ソースで
    `raw_chroma_accuracy` は est_voicing を計算に使わない）ため、低信頼フレームを
    負値化しても分子の母集団は変わらない。第 19 巡で「est_voicing を mask に含める
    べき」という指摘があったが、それを採ると mir_eval と母集団がずれて恒等式が
    壊れる——本テストはその反証を固定する。
    """
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    floor = float(bars["m2_accuracy_bars"]["est_voiced_confidence_floor"])
    # 有声フレームの半分を低信頼（floor 未満）にする CREPE 型 runner。
    specs, _ = harness.load_specs(SPECS_PATH)

    def _half_confident_runner(audio_path: str, route):
        observation, provenance = _make_fake_runner(shift_cents=10.0)(audio_path, route)
        confidences = list(observation.frame_confidence)
        flip = True
        for i, c in enumerate(confidences):
            if c > 0.0:
                confidences[i] = floor / 2.0 if flip else 0.95
                flip = not flip
        return (
            MelodyObservation(
                route=observation.route,
                source_model=observation.source_model,
                frame_times=observation.frame_times,
                frame_hz=observation.frame_hz,
                frame_confidence=tuple(confidences),
                total_duration_sec=observation.total_duration_sec,
            ),
            provenance,
        )

    report = _fake_run(categories=("S_direct",), route_runner=_half_confident_runner)
    row = report["categories"]["S_direct"]
    metrics = row["metrics"]
    # RPA/RCA は voicing 非依存なので満点のまま、VR だけが半減する。
    assert metrics["raw_chroma_accuracy"] == pytest.approx(1.0)
    assert metrics["voicing_recall"] == pytest.approx(0.5, abs=0.01)
    implied = metrics["raw_chroma_accuracy"] * row["ref_voiced_frame_count"]
    assert metrics["voiced_chroma_correct_frame_count"] == pytest.approx(implied, abs=0.5)
    # evaluate の恒等式関門も通る（= 実 run が偽陽性で落ちない）。
    report2 = _fake_run(categories=("S_direct",), route_runner=_half_confident_runner)
    verdict = harness.evaluate_m2_bars(
        [_as_report_artifact(report), _as_report_artifact(report2)],
        bars,
        bars_sha256=bars_sha256,
    )
    assert verdict["categories"]["S_direct"]["status"] == "pass"


# ---------------------------------------------------------------------------
# 第 20 巡: publish 可否の根拠を評価環境の実行証拠へ（Codex P1）
# ---------------------------------------------------------------------------


def test_evaluate_m2_bars_refuses_without_execution_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """抽出器スタックを再計算できない環境（pin が None）からは publish しない。

    CI 環境（CREPE/Demucs 未導入）の素の挙動そのもの。実測を行った slow-lane 機で
    評価する運用を fail-closed に強制する。
    """
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)

    def _no_evidence(route):
        return {"extractor_code_sha256": None, "extractor_weights_sha256": None}

    monkeypatch.setattr(harness, "_environment_execution_pins", _no_evidence)
    with pytest.raises(RuntimeError, match="実行証拠"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_forged_injected_flag_cannot_pass_the_execution_evidence_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """指摘のシナリオそのもの: 注入 run のフラグを False へ書き換えて serialize しても、
    row の pin が評価環境の実スタック pin と一致しない限り publish できない。

    `route_runner_injected` は report bytes の一部なので書き換え可能（指摘のとおり）。
    最終根拠は環境照合であり、偽造 report はフェイク pin（FAKE_*）を持つ一方、
    評価環境の実行証拠は別の値（ここでは実スタック相当の別 sha256）になるため落ちる。
    """
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]  # _fake_run が injected=False へ正規化 = 指摘の「フラグ書き換え」を既に実施した状態

    real_code = hashlib.sha256(b"real-installed-crepe-code").hexdigest()
    real_weights = hashlib.sha256(b"real-installed-crepe-weights").hexdigest()

    def _real_environment(route):
        return {
            "extractor_code_sha256": real_code,
            "extractor_weights_sha256": real_weights,
        }

    monkeypatch.setattr(harness, "_environment_execution_pins", _real_environment)
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="実行証拠.*一致しない"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_execution_evidence_gate_checks_separation_pins_for_fullstack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports = [
        _fake_run(categories=("S_fullstack",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]

    def _mismatched_separation(route):
        pins = _fake_environment_pins(route)
        if route.requires_separation:
            pins["separation_weights_sha256"] = hashlib.sha256(b"other-demucs").hexdigest()
        return pins

    monkeypatch.setattr(harness, "_environment_execution_pins", _mismatched_separation)
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="separation_weights_sha256"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


# ---------------------------------------------------------------------------
# 第 21 巡: 分数分子の拒否 / 測り直し検証 / runtime 入力の --out 保護
# ---------------------------------------------------------------------------


def test_evaluate_m2_bars_rejects_fractional_rca_numerator() -> None:
    """RCA=(k−0.5)/N の境界書き換え（diff がちょうど 0.5）を受理しない。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    for report in reports:
        row = report["categories"]["S_direct"]
        n = row["ref_voiced_frame_count"]
        k = row["metrics"]["voiced_chroma_correct_frame_count"]
        forged = (k - 0.5) / n
        row["metrics"]["raw_chroma_accuracy"] = forged
        row["metrics"]["raw_pitch_accuracy"] = forged  # 他の関係式（gap=RCA−RPA）を整合
        row["metrics"]["octave_gap"] = 0.0
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="一致しない"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def _reverify_via(runner: Any) -> Any:
    """`_ORIG_REVERIFY` を検証用フェイクランナー付きで呼ぶラッパを返す（テスト専用）。

    公開 API（`evaluate_m2_bars`）に注入口は無い（Codex P1 第 22 巡）ので、機構
    テストは monkeypatch（プロセスメモリへの同権限書き込み = 境界外）で「真の抽出
    出力がこうだった環境」を模す。
    """

    def _patched(category: str, rows: Any, **kwargs: Any) -> None:
        _ORIG_REVERIFY(category, rows, verification_runner=runner, **kwargs)

    return _patched


def test_reverification_rejects_metrics_that_do_not_reproduce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """導入済み機で pin を写し取った捏造 metrics も、測り直しと不一致なら publish 不可。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    # 検証側の「真の抽出出力」を報告側（shift 10・median 10 cent）と異なる
    # 値（shift 20・median 20 cent）にする = 捏造が再現しない状況。
    monkeypatch.setattr(
        harness,
        "_reverify_category_measurement",
        _reverify_via(_make_fake_runner(shift_cents=20.0)),
    )
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="測り直しと bit 一致しない"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_reverification_passes_when_metrics_reproduce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    monkeypatch.setattr(
        harness,
        "_reverify_category_measurement",
        _reverify_via(_make_fake_runner(shift_cents=10.0)),
    )
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    verdict = harness.evaluate_m2_bars(
        [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
    )
    assert verdict["categories"]["S_direct"]["status"] == "pass"


def test_reverification_runs_repeats_min_independent_measurements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """repeats 契約の根拠は evaluator 側の独立実行 — 要求本数だけ実際に測り直す。

    run_id は self-reported なので、1 report のコピー + run_id 差し替えで「n>=2 の
    独立実測」を名乗れる（Codex P2 第 23 巡）。決定論契約下でコピーと本物の 2 run は
    観測不能に等価なため、評価器が repeats_min 回の独立実行を自分で行う。
    """
    calls = {"n": 0}
    inner = _make_fake_runner(shift_cents=10.0)

    def _counting(audio_path: str, route: Any) -> Any:
        calls["n"] += 1
        return inner(audio_path, route)

    reports = [
        _fake_run(categories=("S_direct",), route_runner=inner) for _ in range(2)
    ]
    # コピー + run_id 差し替えの水増し repeats を模す（重複検査は通る）。
    forged = json.loads(json.dumps(reports[0]))
    forged["run_id"] = reports[0]["run_id"] + "-copy"
    reports[1] = forged
    monkeypatch.setattr(
        harness, "_reverify_category_measurement", _reverify_via(_counting)
    )
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    verdict = harness.evaluate_m2_bars(
        [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
    )
    assert verdict["categories"]["S_direct"]["status"] == "pass"
    # S_direct 1 カテゴリ × repeats_min(=2) 回の独立実行。
    assert calls["n"] == verdict["repeats_min"] == 2


def test_reverification_rejects_nondeterministic_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """評価器自身の測り直し同士が bit 一致しなければ、決定論の証拠として publish しない。"""
    calls = {"n": 0}

    def _flaky(audio_path: str, route: Any) -> Any:
        calls["n"] += 1
        shift = 10.0 if calls["n"] % 2 == 1 else 20.0
        return _make_fake_runner(shift_cents=shift)(audio_path, route)

    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    monkeypatch.setattr(
        harness, "_reverify_category_measurement", _reverify_via(_flaky)
    )
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(RuntimeError, match="相互に bit 一致しない"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_scorer_pins_must_match_evaluator_environment() -> None:
    """report 同士で相互一致する捏造 mir_eval pin を verdict に転記させない。

    相互比較だけでは、両 report が同じ捏造 pin を名乗れば通り、verdict は「一度も
    走っていないスコアラー実装」を主張できる（Codex P2 第 23 巡）。評価環境から
    再計算した実スコアラー pin との一致を要求する。
    """
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    fabricated = hashlib.sha256(b"scorer-that-never-ran").hexdigest()
    for report in reports:
        report["mir_eval_version"] = "9.99.9"
        report["mir_eval_code_sha256"] = fabricated
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="実スコアラー pin"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_bars_registration_attestation_finds_committed_blob() -> None:
    """commit 済み凍結バーは git 履歴で立証でき、登録時点（commit 日時）が得られる。"""
    attestation, committed = harness._bars_registration_attestation(
        BARS_PATH, BARS_PATH.read_bytes()
    )
    assert re.fullmatch(r"[0-9a-f]{40}", attestation["first_commit"])
    assert committed.tzinfo is not None
    assert attestation["committed_utc"] == committed.isoformat()
    # 正直会計: 立証は「blob が HEAD 祖先に存在する」ことまで。committer 日時は
    # 作成者設定値（GIT_COMMITTER_DATE）なので、順序を証明として名乗らない
    # （Codex P2 第 30 巡）。
    assert attestation["content_evidence"] == "blob_in_head_ancestry"
    assert attestation["ordering_evidence"] == "committer_date"
    assert attestation["ordering_is_proof"] is False


def test_bars_registration_attestation_rejects_uncommitted_bytes(tmp_path: Path) -> None:
    """履歴に無い blob（事後選択したバー）は自己申告 registered_utc では立証できない。

    実測を見てから閾値を作り日付を backdate した bars は「未来でない」検査を通るが、
    git 履歴（不可変）に現れた時点は偽れない（Codex P2 第 28 巡）。
    """
    tampered = BARS_PATH.read_bytes() + b"# post-selected\n"
    path = tmp_path / "m2_accuracy_bars.yaml"
    path.write_bytes(tampered)
    # リポジトリ外パス → 立証不能
    with pytest.raises(RuntimeError, match="リポジトリ外"):
        harness._bars_registration_attestation(path, tampered)
    # リポジトリ内パスでも、履歴に無い bytes は立証不能
    with pytest.raises(RuntimeError, match="どの commit にも"):
        harness._bars_registration_attestation(BARS_PATH, tampered)


def test_attested_registration_rejects_measurements_before_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """測定開始が bars の履歴登録時点より前なら publish しない（backdate 封じ）。"""
    committed = datetime(2026, 7, 26, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        harness,
        "_bars_registration_attestation",
        lambda *a, **k: ({"first_commit": "f" * 40, "committed_utc": committed.isoformat()},
                         committed),
    )
    early = datetime(2026, 7, 26, 11, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="履歴に現れた登録時点"):
        _ORIG_ATTEST(BARS_PATH, BARS_PATH.read_bytes(), [(0, early)])
    # 同一秒も拒否: _utc_now() と git %cI は秒精度で、同一秒内では「commit より前に
    # 開始した」ケースと順序を区別できない（Codex P2 第 29 巡）。
    with pytest.raises(ValueError, match="同一秒"):
        _ORIG_ATTEST(BARS_PATH, BARS_PATH.read_bytes(), [(0, committed)])
    late = datetime(2026, 7, 26, 13, 0, 0, tzinfo=timezone.utc)
    attestation = _ORIG_ATTEST(BARS_PATH, BARS_PATH.read_bytes(), [(0, late)])
    assert attestation["first_commit"] == "f" * 40


def test_harness_forces_fresh_bytecode_for_subsequent_imports() -> None:
    """ハーネス import 以降のモジュールはソースから再コンパイルされる。

    既定のタイムスタンプ検証 .pyc は、同サイズ・同 mtime 差し替えで stale bytecode を
    実行しつつ pin は新ソースを hash する乖離を許す（Codex P2 第 33 巡）。存在しない
    一意な pycache_prefix = キャッシュ不在で必ずソースからコンパイルさせる。
    """
    assert sys.dont_write_bytecode is True
    assert sys.pycache_prefix is not None
    assert "m2-pyc-" in sys.pycache_prefix
    assert not Path(sys.pycache_prefix).exists()


def test_fresh_process_verification_gets_fresh_bytecode_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """測り直し子プロセスにも fresh な bytecode キャッシュ空間を渡す。"""
    captured: Dict[str, Any] = {}

    def _fake_run(command: Any, capture_output: bool = True, text: bool = True, env: Any = None):
        captured["env"] = env

        class _Result:
            returncode = 1
            stderr = "boom"
            stdout = ""

        return _Result()

    monkeypatch.setattr(harness.subprocess, "run", _fake_run)
    with pytest.raises(RuntimeError, match="測り直しプロセスが失敗"):
        harness._run_verification_in_fresh_process(
            "S_direct",
            0,
            tmp_dir=tmp_path,
            specs_path=harness.SPECS_PATH,
            bars_path=harness.BARS_PATH,
            expected_specs_sha256=hashlib.sha256(b"unused").hexdigest(),
        )
    assert captured["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert captured["env"]["PYTHONPYCACHEPREFIX"].startswith(str(tmp_path))


def test_fresh_process_report_provenance_gates() -> None:
    """測り直し子プロセスの report は「素の CLI・現行コード・現行スコアラー」を要求。

    metrics だけ取り出して report を捨てると、測り直し中に差し替わったスタックが
    同じ metrics を出した場合に「以前のスタックを名乗る verdict」が通る
    （Codex P2 第 27 巡）。
    """
    scorer = harness._scorer_pins(use_cache=False)
    frozen_specs = hashlib.sha256(b"frozen-specs").hexdigest()
    base = {
        "schema_version": harness._EXPECTED_REPORT_SCHEMA,
        "route_runner_injected": False,
        "preloaded_seed_modules": [],
        "generator_code_sha256": harness._LOADED_GENERATOR_CODE_SHA256,
        "mir_eval_version": scorer["mir_eval_version"],
        "mir_eval_code_sha256": scorer["mir_eval_code_sha256"],
        "harness_loaded_as_main": True,
        "specs_sha256": frozen_specs,
    }
    harness._require_fresh_process_report_provenance(
        dict(base), "S_direct", expected_specs_sha256=frozen_specs
    )  # 健全なら通る
    for mutation, pattern in [
        ({"route_runner_injected": True}, "注入ランナー"),
        ({"preloaded_seed_modules": ["svp_rpe"]}, "事前ロード"),
        ({"generator_code_sha256": "0" * 64}, "generator_code_sha256"),
        ({"mir_eval_code_sha256": "1" * 64}, "スコアラー pin"),
        ({"schema_version": "m2-accuracy-report/9.9"}, "schema_version"),
        ({"harness_loaded_as_main": False}, "直接パス"),
        ({"specs_sha256": hashlib.sha256(b"other").hexdigest()}, "凍結 specs"),
    ]:
        with pytest.raises(RuntimeError, match=pattern):
            harness._require_fresh_process_report_provenance(
                {**base, **mutation}, "S_direct", expected_specs_sha256=frozen_specs
            )


def test_evaluate_rejects_import_style_harness_runs() -> None:
    """import 経由で実行されたハーネスの report は publish 不可（stale .pyc の余地）。

    直接パスの script 実行だけが .pyc を経由せずソースから実行される
    （Codex P2 第 34 巡）。
    """
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    reports[0]["harness_loaded_as_main"] = False
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="import 経由"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )
    for report in reports:
        report.pop("harness_loaded_as_main", None)
    with pytest.raises(ValueError, match="harness_loaded_as_main"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_reverification_uses_frozen_specs_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """測り直しは `--specs` の実パスでなく、評価器が読んだ bytes の凍結複製を測る。

    実パスを渡すと「評価器が読んで hash した後の差し替え」を子が測る TOCTOU が残る
    （Codex P2 第 34 巡）。
    """
    captured: Dict[str, Any] = {}

    def _capture_run_accuracy(**kwargs: Any) -> Dict[str, Any]:
        captured["specs_path"] = Path(kwargs["specs_path"])
        captured["bytes"] = Path(kwargs["specs_path"]).read_bytes()
        raise RuntimeError("stop-after-capture")

    monkeypatch.setattr(harness, "run_accuracy", _capture_run_accuracy)
    bars, _bars_sha256 = harness.load_bars(BARS_PATH)
    specs_raw = harness.SPECS_PATH.read_bytes()
    with pytest.raises(RuntimeError, match="stop-after-capture"):
        _ORIG_REVERIFY(
            "S_direct",
            [],
            bars=bars,
            specs_raw=specs_raw,
            repeats=2,
            verification_runner=_make_fake_runner(shift_cents=10.0),
        )
    assert captured["specs_path"].resolve() != harness.SPECS_PATH.resolve()
    assert captured["bytes"] == specs_raw


def test_reverification_rejects_stack_drift_during_reverification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """測り直しの実行スタックが提出 row と異なれば、metrics が一致しても拒否する。"""
    inner = _make_fake_runner(shift_cents=10.0)

    def _drifted(audio_path: str, route: Any) -> Any:
        observation, provenance = inner(audio_path, route)
        provenance = dict(provenance)
        provenance["extractor_weights_sha256"] = "a" * 64  # 別重みで測った検証 run を模す
        return observation, provenance

    reports = [
        _fake_run(categories=("S_direct",), route_runner=inner) for _ in range(2)
    ]
    monkeypatch.setattr(
        harness, "_reverify_category_measurement", _reverify_via(_drifted)
    )
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="別 model stack"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_evaluate_m2_bars_has_no_verification_runner_seam() -> None:
    """検証用ランナーの注入口が公開 API に無いこと自体を回帰テストで固定する。

    kwarg が存在すると、捏造 report と同じフェイクランナーで測り直し検証ごと
    再現させ、CREPE を走らせずに pass を publish できる（Codex P1 第 22 巡）。
    """
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(TypeError, match="verification_runner"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports],
            bars,
            bars_sha256=bars_sha256,
            verification_runner=_make_fake_runner(shift_cents=10.0),
        )


def test_reverification_refuses_when_stack_cannot_rerun(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """実抽出器で測り直せない環境（素の CI）からは publish しない。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    monkeypatch.setattr(harness, "_reverify_category_measurement", _ORIG_REVERIFY)
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(RuntimeError, match="再実行できない"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports],
            bars,
            bars_sha256=bars_sha256,
            # verification_runner を渡さない = 実抽出器（CI では unavailable）。
        )


def test_runtime_input_paths_cover_provisioned_weights(monkeypatch: pytest.MonkeyPatch) -> None:
    """重みが解決できる環境では、そのパスが --out 保護集合に入る。"""
    fake_weight = Path("/nonexistent/crepe/model-full.h5")

    import svp_rpe.rpe.learned.crepe_adapter as crepe_adapter

    monkeypatch.setattr(crepe_adapter, "crepe_weight_files", lambda *a, **k: [fake_weight])
    paths = harness._runtime_input_paths()
    assert fake_weight.resolve() in paths


def test_runtime_input_paths_cover_native_extensions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """保護集合は `.py` に限らず、pin が hash するネイティブ拡張も覆う。

    `provenance.package_code_state` は `.so`/`.pyd`/`.dylib` と版番号付き共有
    ライブラリ（`lib*.so.1` 等）も hash 対象にするのに、`--out` 保護が `*.py`
    止まりだと「pin 済みの実行コードを report で潰す」穴が残る（Codex P2 第 22 巡）。
    """
    pkg = tmp_path / "m2fakepkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "_ext.so").write_bytes(b"native")
    (pkg / "libm2fake.so.1").write_bytes(b"versioned native")
    (pkg / "notes.txt").write_text("not code", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    monkeypatch.setattr(harness, "_runtime_package_names", lambda: {"m2fakepkg"})
    paths = harness._runtime_input_paths()
    assert (pkg / "__init__.py").resolve() in paths
    assert (pkg / "_ext.so").resolve() in paths
    assert (pkg / "libm2fake.so.1").resolve() in paths
    assert (pkg / "notes.txt").resolve() not in paths


def test_checkout_roots_are_forced_ahead_of_foreign_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """別 checkout が sys.path で先行していても、本 checkout の root を先頭へ移す。

    存在チェックだけの前置は「別 checkout の src が先・本 checkout の src が後」の
    環境で実行と hash の乖離を許す（Codex P1 第 25 巡）。
    """
    src = str(harness.SRC)
    scripts = str(harness.ROOT / "scripts")
    foreign = "/nonexistent/other-checkout/src"
    monkeypatch.setattr(sys, "path", [foreign, "/somewhere/else", src, scripts])
    harness._force_checkout_roots_first()
    assert sys.path[0] == scripts
    assert sys.path[1] == src
    assert sys.path.index(src) < sys.path.index(foreign)
    assert sys.path.count(src) == 1
    assert sys.path.count(scripts) == 1


def test_runtime_input_paths_cover_decoder_executables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`separation_code_fingerprint` が読む FFmpeg 実行ファイル + libav* closure も保護する。

    パッケージツリー + 重みだけでは、pin が実際に読む集合（デコーダ実行ファイルと
    その共有ライブラリ）より狭い（Codex P2 第 24 巡）。
    """
    import svp_rpe.melody.provenance as provenance

    fake_ffmpeg = tmp_path / "ffmpeg"
    fake_ffmpeg.write_bytes(b"fake executable")
    fake_lib = tmp_path / "libavformat.so.60"
    fake_lib.write_bytes(b"fake library")
    monkeypatch.setattr(
        provenance, "_separation_audio_executables", lambda: (("ffmpeg",), True)
    )
    monkeypatch.setattr(provenance, "_ffmpeg_library_closure", lambda exe: [fake_lib])
    monkeypatch.setattr(
        "shutil.which", lambda tool: str(fake_ffmpeg) if tool == "ffmpeg" else None
    )
    paths = harness._runtime_input_paths()
    assert fake_ffmpeg.resolve() in paths
    assert fake_lib.resolve() in paths


def test_runtime_input_paths_cover_scorer_distribution_metadata() -> None:
    """`_scorer_pins` の version pin が読む mir_eval 配布メタデータも保護する。"""
    import importlib.metadata

    dist = importlib.metadata.distribution("mir_eval")
    metadata_files = [
        Path(str(dist.locate_file(record))).resolve()
        for record in (dist.files or ())
        if str(record).endswith("METADATA")
    ]
    assert metadata_files, "mir_eval の dist-info METADATA が見つからない（前提の drift）"
    paths = harness._runtime_input_paths()
    assert metadata_files[0] in paths


def test_cli_run_categories_flag_limits_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--categories` が run をカテゴリ部分集合へ絞る（測り直しプロセスの前提）。"""
    out = tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_melody_accuracy.py", "--out", str(out), "--categories", "S_direct"],
    )
    assert harness.main() == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert set(report["categories"]) == {"S_direct"}


def test_cli_rejects_out_inside_git_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--out .git/HEAD` 等は attestation の入力 = checkout の制御ファイルを潰す。

    保護集合はファイル単位で git ディレクトリを含まない（Codex P2 第 31 巡）。
    run / evaluate 両モードで git メタデータ内への出力を丸ごと拒否する。
    """
    dummy_report = tmp_path / "r.json"
    dummy_report.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_melody_accuracy.py",
            "--out",
            str(harness.ROOT / ".git" / "HEAD"),
            "--evaluate",
            str(dummy_report),
        ],
    )
    with pytest.raises(SystemExit, match="git メタデータ"):
        harness.main()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_melody_accuracy.py",
            "--out",
            str(harness.ROOT / ".git" / "refs" / "heads" / "fake"),
        ],
    )
    with pytest.raises(SystemExit, match="git メタデータ"):
        harness.main()


def test_cli_evaluate_rejects_categories_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """evaluate モードで `--categories` は不正（report 側の row を評価するため）。"""
    report = tmp_path / "r.json"
    report.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_melody_accuracy.py",
            "--out",
            str(tmp_path / "v.json"),
            "--evaluate",
            str(report),
            "--categories",
            "S_direct",
        ],
    )
    with pytest.raises(SystemExit, match="run phase 専用"):
        harness.main()
