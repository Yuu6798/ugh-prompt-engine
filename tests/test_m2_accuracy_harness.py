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

# フェイク抽出器の model pin。真の sha256（64 桁 hex）でなければ evaluate が
# プレースホルダとして拒否する規律のため、決定論の digest を使う。
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
        harness.evaluate_m2_bars(reports, bars, bars_sha256=bars_sha256)


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
        | set(harness._RUNTIME_PACKAGE_NAMES)
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
        harness.evaluate_m2_bars(reports, bars, bars_sha256=bars_sha256)


def test_evaluate_m2_bars_rejects_report_without_preloaded_field() -> None:
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    del reports[1]["preloaded_seed_modules"]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="preloaded_seed_modules"):
        harness.evaluate_m2_bars(reports, bars, bars_sha256=bars_sha256)


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
        harness.evaluate_m2_bars(reports, bars, bars_sha256=bars_sha256)


# ---------------------------------------------------------------------------
# evaluate phase
# ---------------------------------------------------------------------------


def test_evaluate_m2_bars_full_cycle_pass_and_diagnostic_only() -> None:
    report1 = _fake_run(route_runner=_make_fake_runner(shift_cents=10.0))
    report2 = _fake_run(route_runner=_make_fake_runner(shift_cents=10.0))
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
    report1 = _fake_run(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=500.0)
    )
    report2 = _fake_run(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=500.0)
    )
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    verdict = harness.evaluate_m2_bars([report1, report2], bars, bars_sha256=bars_sha256)
    assert verdict["categories"]["S_direct"]["status"] == "fail"
    assert verdict["categories"]["S_direct"]["failures"]


def test_evaluate_m2_bars_insufficient_repeats_when_only_one_report() -> None:
    report = _fake_run(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    verdict = harness.evaluate_m2_bars([report], bars, bars_sha256=bars_sha256)
    assert verdict["categories"]["S_direct"]["status"] == "insufficient_repeats"


def test_evaluate_m2_bars_rejects_duplicate_run_id() -> None:
    report = _fake_run(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="run_id"):
        harness.evaluate_m2_bars([report, report], bars, bars_sha256=bars_sha256)


def test_evaluate_m2_bars_rejects_missing_recorded_utc() -> None:
    report = _fake_run(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    bad = dict(report)
    del bad["recorded_utc"]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="recorded_utc"):
        harness.evaluate_m2_bars([bad], bars, bars_sha256=bars_sha256)


def test_evaluate_m2_bars_rejects_mismatched_bars_sha256() -> None:
    report1 = _fake_run(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    report2 = dict(_fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)))
    report2["bars_sha256"] = "0" * 64
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="bars_sha256"):
        harness.evaluate_m2_bars([report1, report2], bars, bars_sha256=bars_sha256)


def test_evaluate_m2_bars_rejects_missing_generator_code_sha256() -> None:
    report = _fake_run(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    bad = dict(report)
    del bad["generator_code_sha256"]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="generator_code_sha256"):
        harness.evaluate_m2_bars([bad], bars, bars_sha256=bars_sha256)


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
        harness.evaluate_m2_bars([report1, report2], bars, bars_sha256=bars_sha256)


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
        harness.evaluate_m2_bars([report1, report2], bars, bars_sha256=bars_sha256)


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
        harness.evaluate_m2_bars([report1, report2], bars, bars_sha256=bars_sha256)


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
        harness.evaluate_m2_bars([report1, report2], bars, bars_sha256=bars_sha256)


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
        harness.evaluate_m2_bars([report1, report2], bars, bars_sha256=bars_sha256)


def test_evaluate_m2_bars_records_report_pins_when_supplied() -> None:
    report1 = _fake_run(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    report2 = _fake_run(
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
        harness.evaluate_m2_bars(reports, bars, bars_sha256=bars_sha256)


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
        harness.evaluate_m2_bars(reports, bars, bars_sha256=bars_sha256)


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
        harness.evaluate_m2_bars(reports, bars, bars_sha256=bars_sha256)


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
    metrics["raw_pitch_accuracy"] = 0.95
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    verdict = harness.evaluate_m2_bars(reports, bars, bars_sha256=bars_sha256)
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
    verdict = harness.evaluate_m2_bars(reports, bars, bars_sha256=bars_sha256)
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
        ("  S_direct:\n    min_rpa: .nan\n", "非有限"),
        ("  S_direct:\n    min_rpa: 1.5\n", "定義域"),
        ("  S_direct:\n    min_rpa: 0.90\n    bogus_key: 1\n", "未知の閾値キー"),
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
        harness.evaluate_m2_bars(reports, bars, bars_sha256=bars_sha256)


def test_preloaded_watch_set_covers_the_digest_closure() -> None:
    """監視集合が digest 閉包（推移的モジュール含む）とランタイムパッケージを覆う。"""
    closure = set(harness._closure_module_names())
    # 閉包は seed だけでなく推移的な first-party モジュールも含む。
    assert "svp_rpe.melody.accuracy" in closure
    assert any(name.startswith("svp_rpe.utils") for name in closure), sorted(closure)
    # 監視対象は閉包 + ランタイムパッケージから導出される。
    for name in ("mir_eval", "crepe", "demucs"):
        assert name in harness._RUNTIME_PACKAGE_NAMES


@pytest.mark.parametrize(
    ("mutate", "expect"),
    [
        (lambda m: m.update(median_cent_error=-1.0), "定義域"),
        (lambda m: m.update(voiced_chroma_correct_frame_count=-5), "が負"),
        (lambda m: m.update(voiced_chroma_correct_frame_count=1.5), "整数でない"),
        (lambda m: m.update(median_cent_error=None), "矛盾"),
        (lambda m: m.update(tolerance_cents=600.0), "凍結値"),
    ],
)
def test_evaluate_m2_bars_enforces_metrics_contract(mutate, expect) -> None:
    """誤差モデルの不変条件（中央値・母数・ネスト tolerance）を evaluate で再検査する。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    mutate(reports[0]["categories"]["S_direct"]["metrics"])
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match=expect):
        harness.evaluate_m2_bars(reports, bars, bars_sha256=bars_sha256)


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
        harness.evaluate_m2_bars(reports, bars, bars_sha256=bars_sha256)


def test_evaluate_m2_bars_requires_preprocessing_block_for_fullstack() -> None:
    reports = [
        _fake_run(categories=("S_fullstack",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    for report in reports:
        del report["categories"]["S_fullstack"]["provenance_preprocessing"]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="provenance_preprocessing"):
        harness.evaluate_m2_bars(reports, bars, bars_sha256=bars_sha256)


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
        harness.evaluate_m2_bars(reports, bars, bars_sha256=bars_sha256)


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
        harness.evaluate_m2_bars(reports, bars, bars_sha256=bars_sha256)


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
        harness.evaluate_m2_bars(reports, bars, bars_sha256=bars_sha256)


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
    with pytest.raises(ValueError, match="未知の category"):
        harness.evaluate_m2_bars(reports, bars, bars_sha256=bars_sha256)


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
