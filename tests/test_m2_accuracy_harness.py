"""tests/test_m2_accuracy_harness.py — M2a `scripts/run_melody_accuracy.py` の単体テスト。

対象: `docs/DESIGN_M2_extraction_accuracy.md`（M2a 行、設計 §8 受け入れ条件）。

CI 安全性: 実抽出器（crepe / demucs）を一切必要としない。run/evaluate の二相
メカニズムはフェイク抽出器（決定論の f0 を返す `route_runner`）で検証し、
「実抽出器が未導入なら unavailable として fail-closed に落ちる」経路のみ
既定 runner（`observe_via_route_with_provenance`）を使った軽量スモークで確認する
（設計 §8 M2a 行: 「crepe が CI 不可なら…ハーネス単体テスト」）。
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
import sysconfig
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_melody_accuracy as harness  # noqa: E402
from svp_rpe.melody.observability import MelodyObservation  # noqa: E402
from svp_rpe.melody.accuracy import reference_f0_from_monophonic_spec  # noqa: E402

# scipy/mir_eval.melody を collection 時点で 1 回だけ「予め」import しておく
# （セルフレビュー第二弾 H15 の副作用対応）: `threadpool_info()`（`_numeric_
# runtime_config` が記録）はプロセス全体で現在ロードされている BLAS 実体を都度
# 走査するため、scipy が**このテストプロセス内で初めて**遅延 import される
# タイミングに居合わせた `_fake_run()` 呼び出し同士では、記録される
# `threadpool_info` のエントリ数が「scipy 未ロード→ロード後」で食い違い、
# `_require_homogeneous_numeric_runtime_config` の repeats 間同質性検査が偽陽性で
# 落ちる（`_fake_run()` は同一プロセス内で複数回 `run_accuracy()` を呼ぶテスト
# 専用の簡略化のため、実運用の別プロセス per-repeat では起きない）。ここで
# モジュール collection 時点（＝各チャンクの新規プロセスの最初期）に 1 回だけ
# 実 import を済ませ、以降の全テストが安定した状態から `_fake_run()` を呼べる
# ようにする。
import mir_eval.melody  # noqa: F401,E402

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
    # `_PRE_BOUND_SCORER_NATIVE_MAPPINGS` も同じ理由で正規化する（Codex P1 7 巡目）:
    # pytest プロセスは svp_rpe 経由で numpy/scipy が本ハーネスの束縛より先に import
    # 済みなので、素のままだと `_PRE_BOUND_NATIVE_MAPPING_LOG` 由来の凍結タプルが
    # 非空になり、evaluate の pre-bind ゲートが機構テストの前に発火してしまう。非空の
    # とき拒否すること自体は
    # `test_evaluate_m2_bars_rejects_pre_bound_scorer_native_mappings` が固定する。
    monkeypatch.setattr(harness, "_PRE_BOUND_SCORER_NATIVE_MAPPINGS", ())
    # 直接 `_reject_pre_bound_native_mappings` を呼ぶ単体テストが共有の可変ログへ
    # 追記し続けないよう、テストごとに空へ差し替える（テスト分離。凍結タプル
    # `_PRE_BOUND_SCORER_NATIVE_MAPPINGS` 自体は上ですでに独立して正規化済み）。
    monkeypatch.setattr(harness, "_PRE_BOUND_NATIVE_MAPPING_LOG", [])
    # `_NON_STANDARD_IMPORT_HOOKS` も同じ理由で正規化する（セルフレビュー H3）: pytest
    # 自身が `sys.meta_path` へ `_pytest.assertion.rewrite.AssertionRewritingHook` を
    # 挿すため、素のままだと evaluate の非標準 import hook ゲートが機構テストの前に
    # 発火してしまう。非空のとき拒否すること自体は
    # `test_evaluate_m2_bars_rejects_non_standard_import_hooks_evaluator_process` が
    # 固定する。
    monkeypatch.setattr(harness, "_NON_STANDARD_IMPORT_HOOKS", ())
    # `_SCORER_LOAD_TIME_HASH_MISMATCHES` も同じ理由でテストごとに空へ正規化する
    # （Codex 10 巡目 P1-B）: audit hook はプロセス生涯にわたって除去できず
    # インストールされ続けるため、この可変リストは他のテスト（や本ファイル自身の
    # import）が偶然記録した内容を引きずりうる——テスト分離のため、他の pre-bind
    # 系可変ログと同じ規律で空へ揃える。非空のとき evaluate が拒否すること自体は
    # `test_evaluate_m2_bars_rejects_scorer_load_time_hash_mismatches` が固定する。
    monkeypatch.setattr(harness, "_SCORER_LOAD_TIME_HASH_MISMATCHES", [])
    # H16（セルフレビュー第二弾）の compile 観測 coverage 自己ゲートも同じ理由で
    # 正規化する: pytest では mir_eval/scipy/numpy が本ハーネスの audit hook 設置
    # より前に他のテストファイル経由で import 済みなのが常態——それらの compile は
    # 本ハーネスの観測窓の外で起きているため、期待集合と観測集合が本質的に食い違う
    # （正当な状態）。`_scorer_compile_expected_paths` を空へ差し替えて機構テストが
    # 評価段へ到達できるようにする——非空のとき evaluate が拒否すること自体は
    # `test_evaluate_m2_bars_rejects_uncovered_scorer_compile_observation_evaluator_process`
    # が固定する。
    monkeypatch.setattr(harness, "_scorer_compile_expected_paths", lambda: [])
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
# autouse fixture が `[]` へ正規化する前の実体（H16 の機構そのものをテストする際に
# 明示的に復元して使う）。
_ORIG_SCORER_COMPILE_EXPECTED_PATHS = harness._scorer_compile_expected_paths
# autouse fixture が True へ正規化する前の実値（本テストファイルは import 形 = False）。
_ORIG_LOADED_AS_MAIN = harness._HARNESS_LOADED_AS_MAIN


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
    # 同様に scorer ネイティブの pre-bind も正規化する（Codex P1 7 巡目。非空のとき
    # evaluate が拒否すること自体は専用テストが固定する）。
    report["pre_bound_scorer_native_mappings"] = []
    # 同様に非標準 import hook も正規化する（セルフレビュー H3。非空のとき evaluate が
    # 拒否すること自体は専用テストが固定する）。
    report["non_standard_import_hooks"] = []
    # 同様に scorer .py の swap-and-restore 痕跡も正規化する（Codex 10 巡目 P1-B。
    # 非空のとき evaluate が拒否すること自体は専用テストが固定する）。
    report["scorer_load_time_hash_mismatches"] = []
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


def test_run_accuracy_records_pre_bound_scorer_native_mappings() -> None:
    """「束縛前に scorer ネイティブが既にロード済みだったか」が report に載る（Codex P1 7 巡目）。

    `_clean_evaluator_preload` がテストごとに `_PRE_BOUND_SCORER_NATIVE_MAPPINGS` を
    空へ正規化するため、ここでは通常空になる。非空だった場合に evaluate が拒否する
    ことは `test_evaluate_m2_bars_rejects_pre_bound_scorer_native_mappings_reports` /
    `test_evaluate_m2_bars_rejects_pre_bound_scorer_native_mappings_evaluator_process`
    が固定する。
    """
    report = harness.run_accuracy(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    pre_bound = report["pre_bound_scorer_native_mappings"]
    assert isinstance(pre_bound, list)
    assert all(isinstance(path, str) for path in pre_bound)


def test_evaluate_m2_bars_rejects_pre_bound_scorer_native_mappings_reports() -> None:
    """scorer ネイティブが束縛前に既にロード済みだった run は publish 不可（Codex P1 7 巡目）。

    mmap 済み実体は disk hash では検出できない（TOCTOU: mmap → 差し替え → hash）ため、
    このフィールドが非空の report は「pin が実行 bytes を代表する」保証を持たない。
    """
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    reports[0]["pre_bound_scorer_native_mappings"] = [
        "/usr/local/lib/python3.11/dist-packages/numpy.libs/libopenblas-fake.so.0"
    ]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="束縛前"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_evaluate_m2_bars_rejects_report_without_pre_bound_scorer_native_mappings_field() -> None:
    """規律より前に作られた（または手組みの）report を黙って通さない（Codex P1 7 巡目）。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    del reports[1]["pre_bound_scorer_native_mappings"]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="pre_bound_scorer_native_mappings"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_run_accuracy_records_scorer_load_time_hash_mismatches() -> None:
    """「scorer .py の swap-and-restore 痕跡」が report に載る（Codex 10 巡目 P1-B）。

    `_clean_evaluator_preload` がテストごとに `_SCORER_LOAD_TIME_HASH_MISMATCHES` を
    空へ正規化するため、ここでは通常空になる。非空だった場合に evaluate が拒否する
    ことは `test_evaluate_m2_bars_rejects_scorer_load_time_hash_mismatches_reports` /
    `test_evaluate_m2_bars_rejects_scorer_load_time_hash_mismatches_evaluator_process`
    が固定する。
    """
    report = harness.run_accuracy(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    mismatches = report["scorer_load_time_hash_mismatches"]
    assert isinstance(mismatches, list)
    assert mismatches == []


def test_evaluate_m2_bars_rejects_scorer_load_time_hash_mismatches_reports() -> None:
    """scorer .py の swap-and-restore 痕跡がある run は publish 不可（Codex 10 巡目 P1-B）。

    compile 時点の disk bytes が束縛時点の期待と食い違う report は「pin が実行 bytes
    を代表する」保証を持たない。
    """
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    reports[0]["scorer_load_time_hash_mismatches"] = [
        "/usr/local/lib/python3.11/dist-packages/numpy/core/fake.py: mismatch"
    ]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="swap-and-restore"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_evaluate_m2_bars_rejects_report_without_scorer_load_time_hash_mismatches_field() -> None:
    """規律より前に作られた（または手組みの）report を黙って通さない（Codex 10 巡目 P1-B）。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    del reports[1]["scorer_load_time_hash_mismatches"]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="scorer_load_time_hash_mismatches"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_evaluate_m2_bars_rejects_scorer_load_time_hash_mismatches_evaluator_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """評価器プロセス自身の swap-and-restore 痕跡も publish を拒否する（Codex 10 巡目 P1-B）。

    `_PRE_BOUND_SCORER_NATIVE_MAPPINGS`/`_NON_STANDARD_IMPORT_HOOKS` と異なり、この
    一覧は「load 時 1 回だけ確定」の凍結タプルではなく**ライブ**の可変リストを直接
    参照する自己ゲート——束縛完了後に実際の compile イベントが起きて初めて増える
    値なので、束縛時点で凍結すると恒常的に空になり無意味になる。
    """
    monkeypatch.setattr(
        harness, "_SCORER_LOAD_TIME_HASH_MISMATCHES", ["/fake/scorer.py: mismatch"]
    )
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(RuntimeError, match="swap-and-restore"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_scorer_compile_expected_paths_reflects_currently_imported_modules() -> None:
    """H16: 期待集合は「今 import 済みの scorer .py」を反映する（native 除く）。

    autouse fixture が機構テスト用に `_scorer_compile_expected_paths` を `[]` へ
    正規化するため、ここでは束縛時に捕まえておいた本物の関数を明示的に使う。
    `mir_eval.melody` はモジュール collection 時点で既に import 済み（H15 の
    warm-up）。
    """
    expected = _ORIG_SCORER_COMPILE_EXPECTED_PATHS()
    assert any(p.endswith("melody.py") for p in expected)
    assert all(p.endswith(".py") for p in expected)


def test_require_scorer_compile_observation_covers_imported_modules_passes_when_covered() -> None:
    """H16: 観測集合が期待集合を覆っていれば通る（健全系）。"""
    report = {
        "scorer_compile_expected_paths": ["/a/b.py", "/a/c.py"],
        "scorer_compile_observed_paths": ["/a/b.py", "/a/c.py", "/a/extra.py"],
    }
    harness._require_scorer_compile_observation_covers_imported_modules(
        report, context="test"
    )  # 例外が出ないことが期待値


def test_require_scorer_compile_observation_covers_imported_modules_fails_closed_when_missing() -> None:
    """H16: 期待集合の一部が観測集合に無ければ fail-closed（H13 症状の直接検出）。"""
    report = {
        "scorer_compile_expected_paths": ["/a/b.py", "/a/c.py"],
        "scorer_compile_observed_paths": ["/a/b.py"],
    }
    with pytest.raises(ValueError, match=r"/a/c\.py"):
        harness._require_scorer_compile_observation_covers_imported_modules(
            report, context="test"
        )


def test_require_scorer_compile_observation_covers_imported_modules_fails_closed_on_missing_fields() -> None:
    """H16: 期待/観測フィールドいずれかの欠落も fail-closed（規律より前の report 等）。"""
    with pytest.raises(ValueError, match="scorer_compile_expected_paths"):
        harness._require_scorer_compile_observation_covers_imported_modules(
            {"scorer_compile_observed_paths": []}, context="test"
        )
    with pytest.raises(ValueError, match="scorer_compile_expected_paths"):
        harness._require_scorer_compile_observation_covers_imported_modules(
            {"scorer_compile_expected_paths": []}, context="test"
        )


def test_require_scorer_compile_observation_covers_imported_modules_respects_exception_cls() -> None:
    """H16: `exception_cls` で呼び出し元の既存の例外種別に揃えられる。"""
    with pytest.raises(RuntimeError, match="scorer_compile_expected_paths"):
        harness._require_scorer_compile_observation_covers_imported_modules(
            {"scorer_compile_observed_paths": []},
            context="test",
            exception_cls=RuntimeError,
        )


def test_run_accuracy_records_scorer_compile_coverage_fields() -> None:
    """H16: report が compile 観測/期待の両フィールドを型・構造として持つ（回帰）。

    「期待集合が観測集合に完全に覆われる」こと自体は、pytest の共有プロセスでは
    numpy 等が本ハーネスの audit hook 設置より前に**他のテストファイル経由で**
    import 済みであることが多く（正当な "preloaded" 状態——実運用の単発 CLI 起動
    では発生しない、`_PRELOADED_SEED_MODULES` が別途捕捉する対象）、
    `run_accuracy()` 単体では保証されない——保証されるのは
    `_PRELOADED_SEED_MODULES == []` 等が別ゲートで確認された文脈（evaluate 自己
    ゲート・fresh-process 検証）でのみ。ここでは構造のみ固定し、実際の被覆検証は
    `test_require_scorer_compile_observation_covers_imported_modules_*`
    （合成 dict）と `test_evaluate_m2_bars_rejects_uncovered_scorer_compile_
    observation_evaluator_process`（自己ゲート統合）が担う。
    """
    report = harness.run_accuracy(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    assert isinstance(report["scorer_compile_observed_paths"], list)
    assert isinstance(report["scorer_compile_expected_paths"], list)
    assert all(isinstance(p, str) for p in report["scorer_compile_observed_paths"])
    assert all(isinstance(p, str) for p in report["scorer_compile_expected_paths"])


def test_evaluate_m2_bars_rejects_reports_with_uncovered_scorer_compile_observation() -> None:
    """H16: 提出 report の compile 観測が期待集合を覆っていなければ publish 不可。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    reports[0]["scorer_compile_expected_paths"] = ["/definitely/not/observed.py"]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="compile を観測しなかった"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_evaluate_m2_bars_rejects_uncovered_scorer_compile_observation_evaluator_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H16: 評価器プロセス自身の compile 観測 coverage も自己ゲートで検査する。

    `_clean_evaluator_preload` が `_scorer_compile_expected_paths` を `[]` へ正規化
    するため、通常の機構テストはこの自己ゲートを素通りする——ここでは明示的に
    「期待するが観測されていないファイルがある」状態を作って fail-closed を固定する。
    """
    monkeypatch.setattr(
        harness, "_scorer_compile_expected_paths", lambda: ["/definitely/not/observed.py"]
    )
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(RuntimeError, match="compile を観測しなかった"):
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


def test_mir_eval_paths_limits_single_module_distribution_scan() -> None:
    """単一モジュール配布（`threadpoolctl`）は本体ファイルだけに限定する（Codex 9 巡目 P2）。

    修正前は `spec.origin.parent`（= site-packages 全体）を無条件に rglob しており、
    `threadpoolctl` を `_SCORER_RUNTIME_PACKAGES` に追加したセルフレビュー H1 以降
    site-packages 全体（他の無関係な distribution も含む数千ファイル）を巻き込んで
    いた——`#217` の `soundfile.py` 単一モジュール事故と同型の再発。修正後の値
    （本体ファイルだけ・site-packages 全体を含まない）をここで固定する。
    """
    paths = harness._mir_eval_paths()
    threadpoolctl_paths = [p for p in paths if "threadpoolctl" in p.name]
    assert threadpoolctl_paths == [
        Path(__import__("threadpoolctl").__file__).resolve()
    ], threadpoolctl_paths
    # site-packages 全体を巻き込んでいない回帰確認: 明らかに無関係な distribution
    # （threadpoolctl とも decorator/mir_eval/numpy/scipy/charset_normalizer とも
    # 無関係）のファイルが混入していないこと。
    unrelated_hits = [
        p
        for p in paths
        if p not in threadpoolctl_paths
        and not any(
            marker in str(p)
            for marker in (
                "threadpoolctl",
                "decorator",
                "mir_eval",
                "numpy",
                "scipy",
                "charset_normalizer",
            )
        )
    ]
    assert unrelated_hits == [], (
        f"site-packages 全体を巻き込んでいる疑い（無関係ファイル {len(unrelated_hits)} 件）: "
        f"{unrelated_hits[:5]}"
    )
    assert len(paths) < 5000, (
        f"_mir_eval_paths() が {len(paths)} 件を返した; site-packages 全体走査の"
        "再発が疑われる（回帰）"
    )


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
    with pytest.raises(ValueError, match="スコアラー閉包 pin"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_evaluate_m2_bars_rejects_divergent_scipy_pins() -> None:
    """mir_eval が同一でも scipy の pin が repeats 間で不一致なら拒否する（Codex P1）。

    `mir_eval.melody` は `scipy.interpolate` を直接実行するため、patch/別バージョンの
    scipy は mir_eval 自体の pin を動かさずに RPA/RCA を変えうる——スコアラー閉包の
    一部として scipy も揃える必要がある。
    """
    reports = [
        _fake_run(
            categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
        )
        for _ in range(2)
    ]
    reports[1]["scipy_version"] = "0.999"
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="スコアラー閉包 pin"):
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


def test_evaluate_m2_bars_rejects_missing_scipy_pin() -> None:
    """scorer 閉包 pin の欠落は package 名を問わず一律 fail-closed（Codex P1）。"""
    reports = [
        _fake_run(
            categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
        )
        for _ in range(2)
    ]
    for report in reports:
        del report["scipy_version"]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="scipy_version"):
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


def test_verdict_carries_the_full_scorer_pin_closure() -> None:
    """verdict は mir_eval だけでなく scipy/numpy の pin も 9 キー全部を運ぶ（Codex P1）。

    旧実装は `mir_eval_version`/`mir_eval_code_sha256` の 2 キーしか転記せず、verdict
    単体からは scipy/numpy が何で測られたか読み取れなかった。`{name}_dist_native_sha256`
    （wheel 同梱ネイティブ実体・Codex P1 2 巡目）も同じく全パッケージ分転記される。
    """
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
    for name in harness._SCORER_RUNTIME_PACKAGES:
        assert verdict[f"{name}_version"] == reports[0][f"{name}_version"]
        assert harness._is_sha256(verdict[f"{name}_code_sha256"])
        assert verdict[f"{name}_code_sha256"] == reports[0][f"{name}_code_sha256"]
        assert harness._is_sha256(verdict[f"{name}_dist_native_sha256"])
        assert (
            verdict[f"{name}_dist_native_sha256"]
            == reports[0][f"{name}_dist_native_sha256"]
        )


def test_scorer_pins_records_absent_optional_closure_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """任意閉包メンバー（threadpoolctl/charset_normalizer）未導入は明示的 absent 記録（Codex 11 巡目 P1-B）。

    両者とも numpy/scipy/mir_eval のどの pyproject にも宣言依存として現れない
    try/except ImportError 経由の任意 import（実測確認済み）——クリーン環境では
    未導入が正当であり、version 非空必須の旧実装はこの環境を fail-closed で
    割ってしまっていた。
    """
    import importlib.metadata

    real_version = importlib.metadata.version

    def fake_version(name: str) -> str:
        if name == "charset_normalizer":
            raise importlib.metadata.PackageNotFoundError(name)
        return real_version(name)

    monkeypatch.setattr(importlib.metadata, "version", fake_version)
    pins = harness._scorer_pins(use_cache=False)
    assert pins["charset_normalizer_version"] is None
    assert pins["charset_normalizer_code_sha256"] is None
    assert pins["charset_normalizer_dist_native_sha256"] is None
    assert pins["charset_normalizer_closure_state"] == "absent"
    # 必須メンバーはこの patch の影響を受けない。
    assert pins["mir_eval_version"]
    assert pins["numpy_version"]


def test_scorer_pins_records_present_optional_closure_member() -> None:
    """任意閉包メンバーが導入済みなら必須メンバーと同じ完全 pin を要求する（弱めない）。"""
    pins = harness._scorer_pins(use_cache=False)
    for name in harness._SCORER_RUNTIME_PACKAGES_OPTIONAL:
        state = pins[f"{name}_closure_state"]
        assert state in ("present", "absent")
        if state == "present":
            assert isinstance(pins[f"{name}_version"], str) and pins[f"{name}_version"]
            assert harness._is_sha256(pins[f"{name}_code_sha256"])
            assert harness._is_sha256(pins[f"{name}_dist_native_sha256"])


def test_validated_scorer_pin_tuple_accepts_absent_optional_member() -> None:
    """absent（version/code/dist_native が揃って None）な任意メンバーはマーカーで通す。"""
    mapping = dict(harness._scorer_pins(use_cache=False))
    mapping["charset_normalizer_version"] = None
    mapping["charset_normalizer_code_sha256"] = None
    mapping["charset_normalizer_dist_native_sha256"] = None
    mapping["charset_normalizer_closure_state"] = "absent"
    tup = harness._validated_scorer_pin_tuple(mapping, context="test")
    index = harness._SCORER_RUNTIME_PACKAGES.index("charset_normalizer")
    assert tup[index] == harness._SCORER_ABSENT_OPTIONAL_PIN_MARKER


def test_validated_scorer_pin_tuple_rejects_missing_closure_state_field() -> None:
    """任意メンバーの `{name}_closure_state` 欠落は fail-closed（規律より前の report 等）。"""
    mapping = dict(harness._scorer_pins(use_cache=False))
    del mapping["charset_normalizer_closure_state"]
    with pytest.raises(ValueError, match="charset_normalizer_closure_state"):
        harness._validated_scorer_pin_tuple(mapping, context="test")


def test_validated_scorer_pin_tuple_rejects_invalid_closure_state_value() -> None:
    """`{name}_closure_state` が `present`/`absent` 以外なら fail-closed。"""
    mapping = dict(harness._scorer_pins(use_cache=False))
    mapping["charset_normalizer_closure_state"] = "maybe"
    with pytest.raises(ValueError, match="charset_normalizer_closure_state"):
        harness._validated_scorer_pin_tuple(mapping, context="test")


def test_validated_scorer_pin_tuple_rejects_absent_state_with_partial_pin() -> None:
    """absent と自称しつつ version 等が非 None（矛盾）なら fail-closed。"""
    mapping = dict(harness._scorer_pins(use_cache=False))
    mapping["charset_normalizer_closure_state"] = "absent"
    # version はそのまま残す（= absent 自称と矛盾する部分 pin）。
    with pytest.raises(ValueError, match="未導入と部分 pin が矛盾"):
        harness._validated_scorer_pin_tuple(mapping, context="test")


def test_require_homogeneous_scorer_rejects_absent_presence_mismatch_between_repeats() -> None:
    """任意メンバーの有無自体が repeats 間で食い違えば fail-closed（Codex 11 巡目 P1-B）。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    reports[1]["charset_normalizer_version"] = None
    reports[1]["charset_normalizer_code_sha256"] = None
    reports[1]["charset_normalizer_dist_native_sha256"] = None
    reports[1]["charset_normalizer_closure_state"] = "absent"
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="スコアラー閉包 pin が repeats 間で不一致"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_verdict_transcribes_absent_optional_closure_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """評価環境で任意メンバーが absent なら、verdict も None + closure_state で正直記録する。

    比較用の内部マーカー（`_SCORER_ABSENT_OPTIONAL_PIN_MARKER`）がそのまま verdict に
    漏れないことを固定する。reports は現行の実環境（charset_normalizer 導入済み）で
    素直に作った上で、`_require_homogeneous_scorer` が参照する「評価環境の再計算
    スコアラー pin」だけを absent 相当へ差し替える——`importlib.metadata.version`
    そのものを差し替えると、無関係な `_require_unchanged_since_load()`（束縛時
    スコアラーとの不変性チェック）まで「実行中に差し替わった」と正しく検出して
    しまい、本テストの主眼（verdict transcription）と無関係な経路で落ちるため。
    """
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    for report in reports:
        report["charset_normalizer_version"] = None
        report["charset_normalizer_code_sha256"] = None
        report["charset_normalizer_dist_native_sha256"] = None
        report["charset_normalizer_closure_state"] = "absent"

    real_scorer_pins = harness._scorer_pins

    def fake_scorer_pins(**kwargs: Any) -> Dict[str, Any]:
        pins = dict(real_scorer_pins(**kwargs))
        pins["charset_normalizer_version"] = None
        pins["charset_normalizer_code_sha256"] = None
        pins["charset_normalizer_dist_native_sha256"] = None
        pins["charset_normalizer_closure_state"] = "absent"
        return pins

    monkeypatch.setattr(harness, "_scorer_pins", fake_scorer_pins)
    # 評価器自身の束縛時不変性チェックは本テストの主眼と無関係（上記 docstring 参照）
    # なので no-op にする。
    monkeypatch.setattr(harness, "_require_unchanged_since_load", lambda: None)

    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    verdict = harness.evaluate_m2_bars(
        [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
    )
    assert verdict["charset_normalizer_version"] is None
    assert verdict["charset_normalizer_code_sha256"] is None
    assert verdict["charset_normalizer_dist_native_sha256"] is None
    assert verdict["charset_normalizer_closure_state"] == "absent"
    assert "__absent__" not in repr(verdict)


def test_scorer_runtime_packages_required_optional_partition_is_disjoint_and_covers_all() -> None:
    """required/optional の分割が全域を過不足なく覆う（Codex 11 巡目 P1-B）。"""
    required = set(harness._SCORER_RUNTIME_PACKAGES_REQUIRED)
    optional = set(harness._SCORER_RUNTIME_PACKAGES_OPTIONAL)
    assert not (required & optional)
    assert required | optional == set(harness._SCORER_RUNTIME_PACKAGES)
    assert "decorator" in required  # mir_eval の宣言依存（実測確認済み）
    assert "threadpoolctl" in optional
    assert "charset_normalizer" in optional


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


def test_scorer_pins_cover_scipy_and_numpy_execution_closure() -> None:
    """`mir_eval.melody` が直接実行する scipy/numpy も version + code pin を持つ（#217）。

    `mir_eval/melody.py` が `scipy.interpolate` を、`mir_eval/melody.py` /
    `mir_eval/util.py` が numpy を直接 import して実行するため、patch された
    scipy/numpy は RPA/RCA/median cent error を変えるのに、mir_eval 単体の pin は
    動かない。閉包を `_SCORER_RUNTIME_PACKAGES` へ拡張したことを固定する。
    """
    pins = harness._scorer_pins()
    for name in ("mir_eval", "scipy", "numpy"):
        assert name in harness._SCORER_RUNTIME_PACKAGES
        assert pins[f"{name}_version"], f"{name}_version が空 (pins={pins!r})"
        assert harness._is_sha256(pins[f"{name}_code_sha256"]), (
            f"{name}_code_sha256 が真の sha256 でない (pins={pins!r})"
        )


def test_scorer_runtime_packages_cover_observed_mir_eval_import_closure() -> None:
    """H1 完全性テスト: 実測した `mir_eval.melody` の import 閉包が宣言済み集合に収まる。

    holes.md H1 の手作業列挙（grep ベース）は `decorator`/`threadpoolctl` を発見した
    が、実行可能な形で検証していなかった。本テストは fresh subprocess で
    `import mir_eval.melody` 前後の `sys.modules` 差分を取り、新たに現れた
    third-party トップレベル配布が `_SCORER_RUNTIME_PACKAGES` の package_root /
    RECORD 由来ネイティブ companion（例: charset_normalizer の mypyc `.so` が
    site-packages 直下の兄弟ファイルにある）/ stdlib のいずれかで説明できることを
    assert する——将来 mir_eval/scipy が新しい third-party 依存を牽引するように
    なったら、`_SCORER_RUNTIME_PACKAGES` を直さない限りこのテストが割れる構造にする。

    Cython ランタイムが sys.modules へ注入する `spec` 無しの疑似モジュール
    （`cython_runtime` / `_cython_<version>`）は、別ファイルとして差し替え可能な
    独立の実体を持たないため対象外にする。
    """
    script = f"""
import sys, sysconfig, importlib.util, importlib.metadata, re, json
from pathlib import Path

before = set(sys.modules)
import mir_eval.melody
after = set(sys.modules)
new = after - before
tops = sorted(set(n.split(".")[0] for n in new))

declared = {harness._SCORER_RUNTIME_PACKAGES!r}

stdlib_names = set(sys.stdlib_module_names)
stdlib_paths = [sysconfig.get_paths().get(k) for k in ("stdlib", "platstdlib")]
stdlib_paths = [Path(p).resolve() for p in stdlib_paths if p]

declared_roots = {{}}
declared_native_companions = set()
for name in declared:
    spec = importlib.util.find_spec(name)
    if spec and spec.origin:
        origin = Path(spec.origin).resolve()
        declared_roots[name] = origin.parent if origin.name == "__init__.py" else origin
    try:
        dist = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        continue
    for record in (dist.files or ()):
        try:
            located = Path(str(dist.locate_file(record))).resolve()
        except Exception:
            continue
        declared_native_companions.add(located)

pseudo_allow = re.compile(r"^(cython_runtime|_cython_[0-9_]+)$")

undeclared = []
for name in tops:
    if name in declared or name in stdlib_names or pseudo_allow.match(name):
        continue
    try:
        spec = importlib.util.find_spec(name)
    except Exception:
        spec = None
    origin = getattr(spec, "origin", None) if spec else None
    if origin is None:
        undeclared.append([name, "NO_ORIGIN"])
        continue
    origin_p = Path(origin).resolve()
    if any(str(origin_p).startswith(str(p)) for p in stdlib_paths):
        continue
    if origin_p in declared_native_companions:
        continue
    covered = False
    for droot in declared_roots.values():
        try:
            origin_p.relative_to(droot)
            covered = True
            break
        except ValueError:
            pass
    if covered:
        continue
    undeclared.append([name, str(origin_p)])

print(json.dumps(undeclared))
"""
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    undeclared = json.loads(proc.stdout.strip().splitlines()[-1])
    assert undeclared == [], (
        f"mir_eval.melody の import 閉包に未宣言の third-party が現れた: {undeclared}; "
        "_SCORER_RUNTIME_PACKAGES を更新すること"
    )


def test_scorer_pins_cover_wheel_bundled_native_libraries() -> None:
    """numpy/scipy の wheel 同梱ネイティブ実体（`{name}.libs/`）も pin される（Codex P1 2 巡目）。

    OpenBLAS 等は `numpy/__init__.py` を含むディレクトリの**兄弟**（`numpy.libs/`）に
    置かれるため、`{name}_code_sha256`（本体ディレクトリ配下のみ rglob）はこれを
    覆わない。`{name}_dist_native_sha256` が全パッケージに存在し真の sha256 であること、
    mir_eval（純 Python）は空入力 sha256 と一致すること、numpy はこの環境の wheel
    install で `numpy.libs/` が実在するはずなので空入力 sha256 と**異なる**ことを固定する。
    """
    import hashlib

    empty_input_sha256 = hashlib.sha256(b"").hexdigest()
    pins = harness._scorer_pins()
    for name in harness._SCORER_RUNTIME_PACKAGES:
        key = f"{name}_dist_native_sha256"
        assert key in pins, f"{key} が pins に無い (pins={pins!r})"
        assert harness._is_sha256(pins[key]), f"{key} が真の sha256 でない (pins={pins!r})"

    # (c) mir_eval は純 Python 配布 = 同梱ネイティブ集合が空 = 空入力 sha256 と一致する。
    assert pins["mir_eval_dist_native_sha256"] == empty_input_sha256

    # (b) numpy はこの環境（wheel install）では numpy.libs/ が実在するはず。実在しない
    # 環境（source ビルド等）ではこの前提が崩れるため skip する。
    import importlib.metadata

    try:
        numpy_dist = importlib.metadata.distribution("numpy")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("numpy が未導入")
    numpy_libs_present = any(
        str(record).split("/")[0].endswith(".libs") for record in (numpy_dist.files or ())
    )
    if not numpy_libs_present:
        pytest.skip("この環境の numpy install に .libs 同梱ネイティブが無い")
    assert pins["numpy_dist_native_sha256"] != empty_input_sha256


def test_scorer_dist_native_sha256_fails_closed_when_record_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RECORD (`distribution().files`) が引けない導入済みパッケージは fail-closed（Codex P1 2 巡目）。

    「覆えない閉包を覆ったと主張しない」(#217) 原則: 未導入（`PackageNotFoundError`）は
    「同梱ネイティブが実行されることもない」= 空入力 sha256 で正しいが、**導入済みなのに
    RECORD が読めない**場合に skip すると、実行されうる同梱ネイティブを覆わない pin を
    「揃っている」と誤認する。
    """
    import importlib.metadata

    class _RecordlessDistribution:
        files = None

    real_distribution = importlib.metadata.distribution

    def _fake_distribution(name: str) -> Any:
        if name == "numpy":
            return _RecordlessDistribution()
        return real_distribution(name)

    monkeypatch.setattr(importlib.metadata, "distribution", _fake_distribution)
    with pytest.raises(RuntimeError, match="RECORD"):
        harness._scorer_dist_native_sha256("numpy")


def test_scorer_pins_are_unchanged_by_ownership_verification() -> None:
    """所有権検証（Codex P1 4 巡目）を追加しても、正常環境の 3 パッケージ pins は不変（回帰）。

    `_scorer_dist_native_sha256` に distribution/find_spec 所有権チェックを追加した
    ことで、shadow install の無い通常環境まで誤って fail-closed にしないことを固定する。
    """
    pins = harness._scorer_pins()
    for name in harness._SCORER_RUNTIME_PACKAGES:
        assert pins[f"{name}_version"], name
        assert harness._is_sha256(pins[f"{name}_code_sha256"]), name
        assert harness._is_sha256(pins[f"{name}_dist_native_sha256"]), name


def test_scorer_dist_native_sha256_fails_closed_on_shadow_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """distribution メタデータと find_spec が別インストールを指す shadow 環境は拒否する
    （Codex P1 4 巡目）。

    RECORD（メタデータ側）が指す `{top}/__init__.py` の実パスと、`find_spec`（実行側）の
    origin が別ファイルだと、version pin（メタデータ由来）と code/native pin（find_spec
    由来）が別インストールを指したまま一致比較されうる——揃っているという保証を失う。
    """
    import importlib.metadata

    class _ShadowRecord(str):
        pass

    class _ShadowDistribution:
        files = [_ShadowRecord("numpy/__init__.py")]

        def locate_file(self, record: Any) -> str:
            return "/nonexistent/shadow-site-packages/numpy/__init__.py"

    real_distribution = importlib.metadata.distribution

    def _fake_distribution(name: str) -> Any:
        if name == "numpy":
            return _ShadowDistribution()
        return real_distribution(name)

    monkeypatch.setattr(importlib.metadata, "distribution", _fake_distribution)
    with pytest.raises(RuntimeError, match="shadow|重複インストール"):
        harness._scorer_dist_native_sha256("numpy")


def test_scorer_dist_native_sha256_fails_closed_when_record_lacks_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RECORD に `{top}/__init__.py` が無い（editable install 等）場合も所有権を立証できず
    fail-closed する（Codex P1 4 巡目）。
    """
    import importlib.metadata

    class _NoInitRecord(str):
        pass

    class _NoInitDistribution:
        files = [_NoInitRecord("numpy/version.py")]

        def locate_file(self, record: Any) -> str:
            return f"/nonexistent/{record}"

    real_distribution = importlib.metadata.distribution

    def _fake_distribution(name: str) -> Any:
        if name == "numpy":
            return _NoInitDistribution()
        return real_distribution(name)

    monkeypatch.setattr(importlib.metadata, "distribution", _fake_distribution)
    with pytest.raises(RuntimeError, match="__init__.py"):
        harness._scorer_dist_native_sha256("numpy")


def test_scorer_dist_native_sha256_fails_closed_for_non_wheel_numeric_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """numpy/scipy の RECORD にネイティブ実体が 1 つも無ければ fail-closed（Codex P1 5 巡目）。

    conda/distro パッケージやソースビルドは wheel と異なり `numpy.libs/` のような
    同梱ネイティブを持たず、外部 BLAS/LAPACK に動的リンクする——RECORD はその外部
    ライブラリを把握しないため natives が空になる。これを「同梱ネイティブなし」と
    寛容に空入力 digest で通すと、実行された数値バックエンドの閉包を一切覆わない
    install が「揃っている」と誤認されるため、`_SCORER_NATIVE_BACKEND_REQUIRED`
    （numpy/scipy）はこの空集合を fail-closed で拒否する。所有権検証（4 巡目）は
    通す（`__init__.py` は実際の find_spec origin を指す）ことで、この新しい
    チェックだけを単体で踏ませる。
    """
    import importlib.metadata
    import importlib.util

    real_distribution = importlib.metadata.distribution
    real_spec = importlib.util.find_spec("numpy")
    assert real_spec is not None and real_spec.origin
    real_init = Path(real_spec.origin).resolve()

    class _NoNativeRecord(str):
        pass

    class _NoNativeDistribution:
        files = [_NoNativeRecord("numpy/__init__.py"), _NoNativeRecord("numpy/version.py")]

        def locate_file(self, record: Any) -> str:
            if str(record) == "numpy/__init__.py":
                return str(real_init)
            return f"/nonexistent/{record}"

    def _fake_distribution(name: str) -> Any:
        if name == "numpy":
            return _NoNativeDistribution()
        return real_distribution(name)

    monkeypatch.setattr(importlib.metadata, "distribution", _fake_distribution)
    with pytest.raises(RuntimeError, match="非 wheel"):
        harness._scorer_dist_native_sha256("numpy")


def test_scorer_dist_native_sha256_still_allows_empty_natives_for_mir_eval() -> None:
    """mir_eval（純 Python・`_SCORER_NATIVE_BACKEND_REQUIRED` の対象外）は natives が
    空でも従来どおり空入力 sha256 で正当（Codex P1 5 巡目: 数値バックエンド必須化は
    numpy/scipy 限定で、mir_eval 自身は数値実行を持たない）。
    """
    import hashlib

    assert "mir_eval" not in harness._SCORER_NATIVE_BACKEND_REQUIRED
    empty_input_sha256 = hashlib.sha256(b"").hexdigest()
    assert harness._scorer_dist_native_sha256("mir_eval") == empty_input_sha256


@pytest.mark.parametrize("name", ["numpy", "scipy"])
def test_scorer_dist_native_sha256_fails_closed_when_required_package_missing(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """数値バックエンド必須パッケージが未導入なら fail-closed（セルフレビュー H12）。

    従来は distribution 不在（`PackageNotFoundError`）を「未導入 = 同梱ネイティブが
    実行されることもない」として空入力 sha256 を返していたが、これは
    `_SCORER_NATIVE_BACKEND_REQUIRED`（natives 必ず立証する）の意図をこの分岐だけ
    免れていた——`run_accuracy` が native ゲートを丸ごと skip したまま完走できる
    非対称を塞ぐ。
    """
    import importlib.metadata

    real_distribution = importlib.metadata.distribution

    def _fake_distribution(pkg: str) -> Any:
        if pkg == name:
            raise importlib.metadata.PackageNotFoundError(pkg)
        return real_distribution(pkg)

    monkeypatch.setattr(importlib.metadata, "distribution", _fake_distribution)
    with pytest.raises(RuntimeError, match="数値バックエンド必須パッケージ"):
        harness._scorer_dist_native_sha256(name)


def test_scorer_dist_native_sha256_still_allows_missing_mir_eval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mir_eval（数値バックエンド必須の対象外）は未導入でも空入力 sha256 のまま（回帰）。"""
    import hashlib
    import importlib.metadata

    real_distribution = importlib.metadata.distribution

    def _fake_distribution(pkg: str) -> Any:
        if pkg == "mir_eval":
            raise importlib.metadata.PackageNotFoundError(pkg)
        return real_distribution(pkg)

    monkeypatch.setattr(importlib.metadata, "distribution", _fake_distribution)
    assert harness._scorer_dist_native_sha256("mir_eval") == hashlib.sha256(b"").hexdigest()


def test_scorer_dist_native_sha256_still_allows_missing_threadpoolctl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """threadpoolctl（単一モジュール配布・数値バックエンド必須の対象外）でも同様に回帰する。"""
    import hashlib
    import importlib.metadata

    real_distribution = importlib.metadata.distribution

    def _fake_distribution(pkg: str) -> Any:
        if pkg == "threadpoolctl":
            raise importlib.metadata.PackageNotFoundError(pkg)
        return real_distribution(pkg)

    monkeypatch.setattr(importlib.metadata, "distribution", _fake_distribution)
    assert harness._scorer_dist_native_sha256("threadpoolctl") == hashlib.sha256(b"").hexdigest()


def test_reject_sourceless_scorer_code_passes_for_real_environment() -> None:
    """H2 の sourceless `.pyc` 検査が実環境の全 scorer パッケージで通ること（回帰）。

    `_scorer_dist_native_sha256` から自動的に呼ばれるため、単独のパスの存在は
    `test_scorer_pins_cover_wheel_bundled_native_libraries` 等が間接的に固定するが、
    ここでは検査の実行自体を明示的に固定する。
    """
    for name in harness._SCORER_RUNTIME_PACKAGES:
        harness._scorer_dist_native_sha256(name)  # 例外が出ないことが期待値


def test_reject_sourceless_scorer_code_fails_closed_on_sourceless_top_level(
    tmp_path: Path,
) -> None:
    """`find_spec` の origin 自体が `.pyc`（トップレベルの `.py` が削除された）なら fail-closed。"""
    with pytest.raises(RuntimeError, match="sourceless"):
        harness._reject_sourceless_scorer_code(
            "fakepkg",
            origin=tmp_path / "fakepkg" / "__init__.pyc",
            package_root=tmp_path / "fakepkg",
            is_package=True,
        )


def test_reject_sourceless_scorer_code_ignores_single_module_without_rglob(
    tmp_path: Path,
) -> None:
    """単一モジュール配布はトップレベル origin だけ検査し、rglob はしない（サブモジュール無し）。"""
    origin = tmp_path / "threadpoolctl.py"
    origin.write_text("# not empty", encoding="utf-8")
    harness._reject_sourceless_scorer_code(
        "threadpoolctl", origin=origin, package_root=origin, is_package=False
    )  # 例外が出ないことが期待値


def test_reject_sourceless_scorer_code_fails_closed_on_orphan_pycache(
    tmp_path: Path,
) -> None:
    """`__pycache__` 内に対応する `.py` の無い `.pyc`（stale cache/捏造）があれば fail-closed。

    holes.md H2 の実測手口: `mir_eval/melody.py` を削除して `.pyc` だけを残す状況を
    単体化する。
    """
    package_root = tmp_path / "fakepkg"
    package_root.mkdir()
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    pycache = package_root / "__pycache__"
    pycache.mkdir()
    (pycache / "melody.cpython-311.pyc").write_bytes(b"poisoned-bytecode")
    # 対応する fakepkg/melody.py は存在しない（削除済みを模す）。
    with pytest.raises(RuntimeError, match="sourceless"):
        harness._reject_sourceless_scorer_code(
            "fakepkg",
            origin=package_root / "__init__.py",
            package_root=package_root,
            is_package=True,
        )


def test_reject_sourceless_scorer_code_fails_closed_on_orphan_bare_pyc(
    tmp_path: Path,
) -> None:
    """`__pycache__` の外に直置きされた orphan `.pyc`（`SourcelessFileLoader` 経路）も fail-closed。"""
    package_root = tmp_path / "fakepkg"
    package_root.mkdir()
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    (package_root / "inner.pyc").write_bytes(b"poisoned-bytecode")
    with pytest.raises(RuntimeError, match="sourceless"):
        harness._reject_sourceless_scorer_code(
            "fakepkg",
            origin=package_root / "__init__.py",
            package_root=package_root,
            is_package=True,
        )


def test_reject_sourceless_scorer_code_allows_pyc_with_sibling_source(
    tmp_path: Path,
) -> None:
    """通常の（対応する `.py` がある）bytecode cache は無害——orphan だけを狙う。"""
    package_root = tmp_path / "fakepkg"
    package_root.mkdir()
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    (package_root / "inner.py").write_text("", encoding="utf-8")
    pycache = package_root / "__pycache__"
    pycache.mkdir()
    (pycache / "inner.cpython-311.pyc").write_bytes(b"normal-bytecode-cache")
    harness._reject_sourceless_scorer_code(
        "fakepkg", origin=package_root / "__init__.py", package_root=package_root, is_package=True
    )  # 例外が出ないことが期待値


def test_is_os_baseline_library_covers_glibc_family_and_excludes_others() -> None:
    """OS 基盤 whitelist（Codex P1 6 巡目 P1-A）は glibc 族 + libgcc_s/libstdc++/libz のみ。"""
    for soname in (
        "libc.so.6",
        "libm.so.6",
        "libpthread.so.0",
        "libdl.so.2",
        "librt.so.1",
        "libgcc_s.so.1",
        "libstdc++.so.6",
        "libz.so.1",
        "ld-linux-x86-64.so.2",
    ):
        assert harness._is_os_baseline_library(soname), soname
    for soname in ("libopenblas.so.0", "libgfortran-abc123.so.5", "libfoo.so.1", "libmkl_core.so"):
        assert not harness._is_os_baseline_library(soname), soname


def test_verify_scorer_dt_needed_closure_passes_for_real_wheel_numpy_and_scipy() -> None:
    """DT_NEEDED 閉包検証が実環境の wheel numpy/scipy で通ること（回帰・Codex P1 6 巡目 P1-A）。

    `_scorer_dist_native_sha256` 経由で `_verify_scorer_dt_needed_closure` /
    `_reject_pre_bound_native_mappings` の両方が実行される。実装時の実測で numpy/scipy
    の同梱ネイティブ（`.libs/`）が `libz.so.1` へ動的リンクすることを確認したため
    whitelist に `libz` を含めている——この回帰が崩れたら whitelist の想定が崩れた合図。
    """
    for name in ("numpy", "scipy"):
        digest = harness._scorer_dist_native_sha256(name)
        assert harness._is_sha256(digest)


def test_verify_scorer_dt_needed_closure_fails_closed_on_unparseable_root(
    tmp_path: Path,
) -> None:
    """seed の native 拡張モジュールが ELF としてパース不能なら fail-closed。"""
    package_root = tmp_path / "fakepkg_unparseable"
    package_root.mkdir()
    (package_root / "_ext.cpython-311-x86_64-linux-gnu.so").write_bytes(
        b"definitely-not-elf-bytes"
    )
    with pytest.raises(RuntimeError, match="DT_NEEDED を読めない"):
        harness._verify_scorer_dt_needed_closure(
            "numpy", package_root=package_root, natives=set()
        )


def test_verify_scorer_dt_needed_closure_verifies_os_baseline_soname_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """OS 基盤 soname も実解決し、ldconfig cache 登録の exact path 一致を要求する。

    （Codex 10 巡目 P1-A）旧実装は「名前が基盤らしい」だけで無条件に `continue`
    （resolve すら呼ばない）していた——`LD_LIBRARY_PATH`/`DT_RPATH` が攻撃者の
    ディレクトリの同名ファイルを指しても検出できなかった。ここでは
    `_resolve_soname_without_loading` が実際に**呼ばれ**、解決先が
    `_is_ldconfig_registered_path`（Codex 14 巡目 P1-A: ディレクトリメンバシップ
    から exact cache path 一致へ変更）を満たすことを要求することを固定する。
    """
    import svp_rpe.melody.provenance as provenance

    package_root = tmp_path / "fakepkg_baseline"
    package_root.mkdir()
    ext = package_root / "_ext.cpython-311-x86_64-linux-gnu.so"
    ext.write_bytes(b"not-a-real-elf")

    def _fake_elf_dynamic_info(path: Any) -> Any:
        if Path(path).name == ext.name:
            return (["libc.so.6", "libstdc++.so.6", "libz.so.1"], (), ())
        return None

    resolved_calls: "list[str]" = []

    def _fake_resolve(soname: str, *, rpath_dirs: Any = (), runpath_dirs: Any = ()) -> Any:
        resolved_calls.append(soname)
        return Path("/usr/lib/x86_64-linux-gnu") / soname

    monkeypatch.setattr(provenance, "_elf_dynamic_info", _fake_elf_dynamic_info)
    monkeypatch.setattr(provenance, "_resolve_soname_without_loading", _fake_resolve)
    monkeypatch.setattr(
        provenance,
        "_is_ldconfig_registered_path",
        lambda soname, resolved: resolved == Path("/usr/lib/x86_64-linux-gnu") / soname,
    )
    harness._verify_scorer_dt_needed_closure(
        "numpy", package_root=package_root, natives=set()
    )
    assert sorted(resolved_calls) == ["libc.so.6", "libstdc++.so.6", "libz.so.1"]


def test_verify_scorer_dt_needed_closure_fails_closed_when_baseline_soname_unresolvable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """基盤 soname が解決できなければ fail-closed（Codex 10 巡目 P1-A）。"""
    import svp_rpe.melody.provenance as provenance

    package_root = tmp_path / "fakepkg_baseline_unresolvable"
    package_root.mkdir()
    ext = package_root / "_ext.cpython-311-x86_64-linux-gnu.so"
    ext.write_bytes(b"not-a-real-elf")

    def _fake_elf_dynamic_info(path: Any) -> Any:
        if Path(path).name == ext.name:
            return (["libc.so.6"], (), ())
        return None

    monkeypatch.setattr(provenance, "_elf_dynamic_info", _fake_elf_dynamic_info)
    monkeypatch.setattr(provenance, "_resolve_soname_without_loading", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="基盤 soname .* を解決できない"):
        harness._verify_scorer_dt_needed_closure(
            "numpy", package_root=package_root, natives=set()
        )


def test_verify_scorer_dt_needed_closure_fails_closed_when_baseline_soname_resolves_outside_ldconfig_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """基盤 soname の解決先が ldconfig cache の exact path と一致しなければ fail-closed。

    `LD_LIBRARY_PATH`/`DT_RPATH` が攻撃者の用意した別ディレクトリの同名ファイル
    （`libc.so.6` 等）を指す実測相当のシナリオ（Codex 10 巡目 P1-A・14 巡目 P1-A で
    ディレクトリメンバシップから exact cache path 一致へ強化）。
    """
    import svp_rpe.melody.provenance as provenance

    package_root = tmp_path / "fakepkg_baseline_hijacked"
    package_root.mkdir()
    ext = package_root / "_ext.cpython-311-x86_64-linux-gnu.so"
    ext.write_bytes(b"not-a-real-elf")

    def _fake_elf_dynamic_info(path: Any) -> Any:
        if Path(path).name == ext.name:
            return (["libc.so.6"], (), ())
        return None

    hijacked_dir = tmp_path / "evil_ld_library_path"
    hijacked_dir.mkdir()

    def _fake_resolve(soname: str, *, rpath_dirs: Any = (), runpath_dirs: Any = ()) -> Any:
        return hijacked_dir / soname

    monkeypatch.setattr(provenance, "_elf_dynamic_info", _fake_elf_dynamic_info)
    monkeypatch.setattr(provenance, "_resolve_soname_without_loading", _fake_resolve)
    monkeypatch.setattr(provenance, "_is_ldconfig_registered_path", lambda soname, resolved: False)
    with pytest.raises(RuntimeError, match="ldconfig cache 登録の exact path と"):
        harness._verify_scorer_dt_needed_closure(
            "numpy", package_root=package_root, natives=set()
        )


def test_is_ldconfig_registered_path_requires_exact_cache_path_not_directory_membership(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`_is_ldconfig_registered_path` はディレクトリメンバシップでなく exact path 一致（Codex 14 巡目 P1-A）。

    `/usr/local/lib` のようにベンダー/アプリライブラリが 1 つだけ ldconfig 登録された
    「混在ディレクトリ」を模擬する: そのディレクトリに置かれた cache **未登録**の
    custom `libm.so.6`（`LD_LIBRARY_PATH` 経由で解決されたと想定）は baseline と
    認めてはならない——旧ディレクトリメンバシップ実装ならここを誤って許可した。
    一方、cache に正確に登録されたライブラリは従来どおり baseline。
    """
    import svp_rpe.melody.provenance as provenance

    mixed_dir = tmp_path / "usr_local_lib"
    mixed_dir.mkdir()
    registered_vendor_lib = mixed_dir / "libvendorthing.so.1"
    registered_vendor_lib.write_bytes(b"vendor")
    custom_unregistered_libm = mixed_dir / "libm.so.6"
    custom_unregistered_libm.write_bytes(b"custom-unregistered")

    fake_ldconfig_output = f"libvendorthing.so.1 (libc6,x86-64) => {registered_vendor_lib}\n"
    monkeypatch.setattr(provenance, "_ldconfig_cache_listing", lambda: fake_ldconfig_output)

    # cache に登録された soname はそのまま baseline。
    assert provenance._is_ldconfig_registered_path(
        "libvendorthing.so.1", registered_vendor_lib.resolve()
    )
    # 同じディレクトリに置かれていても、cache 未登録の soname/path は baseline でない
    # ——ディレクトリメンバシップ判定ならここが誤って True になっていた。
    assert not provenance._is_ldconfig_registered_path(
        "libm.so.6", custom_unregistered_libm.resolve()
    )


def test_verify_scorer_dt_needed_closure_fails_closed_when_soname_unresolvable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """DT_NEEDED の soname が解決できなければ fail-closed（覆えない閉包を主張しない）。"""
    import svp_rpe.melody.provenance as provenance

    package_root = tmp_path / "fakepkg_unresolvable"
    package_root.mkdir()
    ext = package_root / "_ext.cpython-311-x86_64-linux-gnu.so"
    ext.write_bytes(b"not-a-real-elf")

    def _fake_elf_dynamic_info(path: Any) -> Any:
        if Path(path).name == ext.name:
            return (["libmissing.so.9"], (), ())
        return None

    monkeypatch.setattr(provenance, "_elf_dynamic_info", _fake_elf_dynamic_info)
    monkeypatch.setattr(provenance, "_resolve_soname_without_loading", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="解決できない"):
        harness._verify_scorer_dt_needed_closure(
            "numpy", package_root=package_root, natives=set()
        )


def test_verify_scorer_dt_needed_closure_fails_closed_on_external_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """DT_NEEDED が distribution 外部（BLAS/LAPACK/MKL 等）へ解決されたら fail-closed。

    「natives が非空」の cardinality 検査だけでは見逃す部分ベンダリング
    ——同梱ネイティブが一部あるのに、実際に実行される実装が外部の system-wide
    ライブラリに動的リンクしている——を模す（Codex P1 6 巡目 P1-A の主眼）。
    """
    import svp_rpe.melody.provenance as provenance

    package_root = tmp_path / "fakepkg_external"
    package_root.mkdir()
    ext = package_root / "_ext.cpython-311-x86_64-linux-gnu.so"
    ext.write_bytes(b"not-a-real-elf")

    def _fake_elf_dynamic_info(path: Any) -> Any:
        if Path(path).name == ext.name:
            return (["libfakeexternalblas.so.1"], (), ())
        return None

    def _fake_resolve(soname: str, *, rpath_dirs: Any = (), runpath_dirs: Any = ()) -> Any:
        if soname == "libfakeexternalblas.so.1":
            return Path("/usr/lib/x86_64-linux-gnu/libfakeexternalblas.so.1")
        return None

    monkeypatch.setattr(provenance, "_elf_dynamic_info", _fake_elf_dynamic_info)
    monkeypatch.setattr(provenance, "_resolve_soname_without_loading", _fake_resolve)
    with pytest.raises(RuntimeError, match="外部数値バックエンド"):
        harness._verify_scorer_dt_needed_closure(
            "numpy", package_root=package_root, natives=set()
        )


def test_verify_scorer_dt_needed_closure_revisits_shared_native_under_new_rpath_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """同一 owned native が異なる RPATH context で 2 extension root から到達可能なら
    両方の context を検証する（Codex 13 巡目 P1-B）。

    `_ext_a` と `_ext_b` は共に同じ同梱 native（`libcommon.so.1` の解決先＝
    `shared_native`、package_root 配下なので owned）へリンクする。`shared_native`
    自身は own RPATH を持たず（auditwheel `.libs` 慣例）、次の依存
    `libbackend.so.1` の解決は**継承 RPATH**だけに依存する。`_ext_a` 経由の継承
    context では `libbackend.so.1` は package_root 配下（owned）に解決され、
    `_ext_b` 経由の継承 context では distribution 外部（unowned）に解決される
    ——つまり「同じ path でも context が違えば解決結果が変わる」を模す。

    path のみで dedup していた旧実装なら、`_ext_a` の walk で `shared_native` が
    visited 済みになり、`_ext_b` からの再到達が skip されて外部解決を見逃す
    （fail-closed に倒れない）。(path, context) dedup ならどちらの context も
    walk され、`_ext_b` 側で fail-closed になる。
    """
    import svp_rpe.melody.provenance as provenance

    package_root = tmp_path / "fakepkg_context"
    package_root.mkdir()
    ext_a = package_root / "_ext_a.cpython-311-x86_64-linux-gnu.so"
    ext_b = package_root / "_ext_b.cpython-311-x86_64-linux-gnu.so"
    ext_a.write_bytes(b"not-a-real-elf-a")
    ext_b.write_bytes(b"not-a-real-elf-b")

    shared_native = package_root / "fakepkg.libs" / "libcommon.so.1"  # owned (under package_root)
    owned_backend = package_root / "fakepkg.libs" / "libbackend.so.1"  # owned
    external_backend = tmp_path / "outside_distribution" / "libbackend.so.1"  # unowned

    # RPATH マーカー（実ディレクトリである必要はない・`_elf_dynamic_info` を丸ごと
    # 差し替えるので `_expand_loader_paths` は経由しない）。
    owned_rpath_marker = package_root / "fakepkg.libs"
    external_rpath_marker = tmp_path / "evil_inherited_rpath"

    def _fake_elf_dynamic_info(path: Any) -> Any:
        p = Path(path)
        if p == ext_a:
            # own RPATH = owned_rpath_marker（このルート経由で shared_native に
            # 到達する呼び出しの継承 context になる）。
            return (["libcommon.so.1"], (str(owned_rpath_marker),), ())
        if p == ext_b:
            # own RPATH = external_rpath_marker（別 context）。
            return (["libcommon.so.1"], (str(external_rpath_marker),), ())
        if p == shared_native:
            # 自身の RPATH を持たない（auditwheel `.libs` 慣例）——継承 context のみで
            # 次の依存を解決する。
            return (["libbackend.so.1"], (), ())
        if p == owned_backend:
            # owned context 側の末端（これ以上の依存は無い）。
            return ([], (), ())
        return None

    resolve_calls: "list[tuple[str, Any]]" = []

    def _fake_resolve(soname: str, *, rpath_dirs: Any = (), runpath_dirs: Any = ()) -> Any:
        resolve_calls.append((soname, tuple(rpath_dirs)))
        if soname == "libcommon.so.1":
            return shared_native
        if soname == "libbackend.so.1":
            if owned_rpath_marker in rpath_dirs:
                return owned_backend
            if external_rpath_marker in rpath_dirs:
                return external_backend
        return None

    monkeypatch.setattr(provenance, "_elf_dynamic_info", _fake_elf_dynamic_info)
    monkeypatch.setattr(provenance, "_resolve_soname_without_loading", _fake_resolve)

    with pytest.raises(RuntimeError, match="外部数値バックエンド"):
        harness._verify_scorer_dt_needed_closure(
            "numpy", package_root=package_root, natives=set()
        )

    # shared_native への libbackend.so.1 解決が両方の context で試みられたこと
    # （= (path, context) dedup で再訪が起きたこと）を固定する。path のみの dedup
    # だったら external context 側の呼び出しは発生せず、この assert が落ちる。
    backend_contexts = [rpath for soname, rpath in resolve_calls if soname == "libbackend.so.1"]
    assert (owned_rpath_marker,) in backend_contexts
    assert (external_rpath_marker,) in backend_contexts


def test_reject_ld_preload_before_scorer_bind_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`LD_PRELOAD` が非空ならスコアラー pin の束縛前に fail-closed（Codex P1 6 巡目 P1-B）。"""
    monkeypatch.setenv("LD_PRELOAD", "/tmp/whatever-fake-preload.so")
    with pytest.raises(RuntimeError, match="LD_PRELOAD"):
        harness._reject_ld_preload_before_scorer_bind()
    with pytest.raises(RuntimeError, match="LD_PRELOAD"):
        harness._scorer_pins()


def test_reject_ld_preload_absent_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """`LD_PRELOAD` が未設定なら束縛前チェックは何もしない（回帰）。"""
    monkeypatch.delenv("LD_PRELOAD", raising=False)
    harness._reject_ld_preload_before_scorer_bind()  # 例外が出ないことが期待値


@pytest.mark.parametrize("env_var", ["LD_PRELOAD", "LD_AUDIT", "LD_DYNAMIC_WEAK"])
def test_reject_ld_preload_sibling_env_vars_fail_closed(
    env_var: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`LD_AUDIT`/`LD_DYNAMIC_WEAK` も `LD_PRELOAD` と同格の割り込み経路（セルフレビュー H4）。"""
    for name in harness._LD_PRELOAD_SIBLING_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(env_var, "/tmp/whatever-fake-interposer.so")
    with pytest.raises(RuntimeError, match=env_var):
        harness._reject_ld_preload_before_scorer_bind()


def test_reject_ld_so_preload_file_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/etc/ld.so.preload` が存在し非空なら fail-closed（セルフレビュー H4）。"""
    import pathlib

    for name in harness._LD_PRELOAD_SIBLING_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    real_read_bytes = pathlib.Path.read_bytes

    def _fake_read_bytes(self: Path, *args: Any, **kwargs: Any) -> bytes:
        if str(self) == "/etc/ld.so.preload":
            return b"/tmp/evil.so\n"
        return real_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_bytes", _fake_read_bytes)
    with pytest.raises(RuntimeError, match="ld.so.preload"):
        harness._reject_ld_preload_before_scorer_bind()


def test_reject_ld_so_preload_absent_file_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/etc/ld.so.preload` が存在しない（大半の環境）なら束縛前チェックは何もしない。"""
    for name in harness._LD_PRELOAD_SIBLING_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    assert not Path("/etc/ld.so.preload").exists(), (
        "この実行環境には /etc/ld.so.preload が実在する（テスト前提の drift）"
    )
    harness._reject_ld_preload_before_scorer_bind()  # 例外が出ないことが期待値


def test_parse_proc_self_maps_executable_mappings_fails_closed_when_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/proc/self/maps` を読めない環境（非 Linux 等）は fail-closed（Codex P1 6 巡目 P1-B）。"""
    import pathlib

    real_read_text = pathlib.Path.read_text

    def _fake_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if str(self) == "/proc/self/maps":
            raise OSError("simulated: no such platform file")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", _fake_read_text)
    with pytest.raises(RuntimeError, match="/proc/self/maps"):
        harness._parse_proc_self_maps_executable_mappings()


def test_parse_proc_self_maps_executable_mappings_parses_synthetic_maps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """偽 `/proc/self/maps` テキストから実行可能マッピングだけを、`(deleted)` を剥がして拾う。

    （セルフレビュー H5/H6）権限フィールドを先に見るため、拡張子の有無や `(deleted)`
    注記は無関係にまず候補へ入る——分類は呼び出し側（`_reject_pre_bound_native_
    mappings`）の責務。非実行可能（`r--p` のみ）マッピングと `[heap]` は対象外。
    """
    import pathlib

    real_read_text = pathlib.Path.read_text
    fake_maps = "\n".join(
        [
            "7f0000000000-7f0000010000 r--p 00000000 00:00 0                          [heap]",
            "7f0000010000-7f0000020000 r-xp 00000000 08:01 123   "
            "/usr/lib/x86_64-linux-gnu/libc.so.6",
            "7f0000020000-7f0000030000 r-xp 00000000 08:01 456   "
            "/some/where/libopenblas-fake.so.0",
            "7f0000030000-7f0000040000 r--p 00000000 08:01 789   "
            "/usr/lib/x86_64-linux-gnu/libnotexec.so.1",
            "7f0000040000-7f0000050000 r-xp 00000000 08:01 999   "
            "/usr/.../libc.so.6 (deleted)",
        ]
    )

    def _fake_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if str(self) == "/proc/self/maps":
            return fake_maps
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", _fake_read_text)
    mappings = harness._parse_proc_self_maps_executable_mappings()
    by_path = dict(mappings)
    assert by_path["/usr/lib/x86_64-linux-gnu/libc.so.6"] is False
    assert by_path["/some/where/libopenblas-fake.so.0"] is False
    assert "/usr/lib/x86_64-linux-gnu/libnotexec.so.1" not in by_path  # 非実行可能
    assert "[heap]" not in by_path  # 非実行可能（r--p）なので対象外
    assert by_path["/usr/.../libc.so.6"] is True  # (deleted) を剥がした上で deleted=True


def test_parse_proc_self_maps_executable_mappings_synthesizes_anonymous_tag_for_pathless_exec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """パスフィールド自体が無い実行可能行（5 フィールド）も `[anonymous]` として拾う。

    （Codex 9 巡目 P1）旧実装は `len(fields) < 6` の 5 フィールド行を権限検査より前に
    `continue` で落としており、匿名 exec 領域が default-deny（H11）に一度も到達
    しない穴だった。実測（fresh CLI の `/proc/self/maps`）でパス無し行に `x` を含む
    ものは確認できなかったため、許容リスト化はせず `"[anonymous]"`
    （`_ANONYMOUS_EXECUTABLE_MAPPING_ALLOWLIST` に無い名前）として default-deny に
    合流させる。
    """
    import pathlib

    real_read_text = pathlib.Path.read_text
    fake_maps = "\n".join(
        [
            # パスフィールドが無い純粋な匿名 exec 領域（device 00:00, inode 0）。
            "7f0000000000-7f0000010000 r-xp 00000000 00:00 0",
            # 同型だが非実行可能（比較対照）。
            "7f0000010000-7f0000020000 rw-p 00000000 00:00 0",
        ]
    )

    def _fake_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if str(self) == "/proc/self/maps":
            return fake_maps
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", _fake_read_text)
    mappings = harness._parse_proc_self_maps_executable_mappings()
    assert mappings == [("[anonymous]", False)]


def test_reject_pre_bound_native_mappings_flags_pathless_anonymous_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`"[anonymous]"`（パス無し実行マッピングの合成タグ）は許容リスト外として fail-closed。

    （Codex 9 巡目 P1）`_parse_proc_self_maps_executable_mappings` が正しく
    `"[anonymous]"` を返しても、分類側がこれを許容してしまえば意味が無い——
    `_ANONYMOUS_EXECUTABLE_MAPPING_ALLOWLIST`（`[vdso]`/`[vsyscall]` のみ）に含まれ
    ないことを固定する。
    """
    monkeypatch.setattr(
        harness,
        "_parse_proc_self_maps_executable_mappings",
        lambda: [("[anonymous]", False)],
    )
    with pytest.raises(RuntimeError, match="匿名実行マッピング"):
        harness._reject_pre_bound_native_mappings(
            "numpy", package_root=tmp_path / "numpy_root_nonexistent", natives=set()
        )


def test_reject_pre_bound_native_mappings_flags_external_blas_family(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """所有パス外の BLAS 系マッピング（bytes も pin 対象と不一致）は fail-closed。"""
    fake_external = tmp_path / "libopenblas-shadow.so.0"
    fake_external.write_bytes(b"malicious-blas-bytes")
    monkeypatch.setattr(
        harness,
        "_parse_proc_self_maps_executable_mappings",
        lambda: [(str(fake_external.resolve()), False)],
    )
    with pytest.raises(RuntimeError, match="BLAS"):
        harness._reject_pre_bound_native_mappings(
            "numpy", package_root=tmp_path / "numpy_root_nonexistent", natives=set()
        )


def test_reject_pre_bound_native_mappings_flags_libscipy_openblas_naming(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`libscipy_openblas*` 命名（セルフレビュー H5 の実測反証）も BLAS 系として検出する。

    6 巡目時点の正規表現は "lib" 直後が "scipy_" のため実環境の主流命名
    （`numpy.libs/libscipy_openblas64_-…so` 等）にマッチしなかった——是正後の
    regex がこの命名も拾うことを固定する。
    """
    fake_external = tmp_path / "libscipy_openblas64_-deadbeef.so"
    fake_external.write_bytes(b"malicious-blas-bytes")
    monkeypatch.setattr(
        harness,
        "_parse_proc_self_maps_executable_mappings",
        lambda: [(str(fake_external.resolve()), False)],
    )
    with pytest.raises(RuntimeError, match="BLAS"):
        harness._reject_pre_bound_native_mappings(
            "numpy", package_root=tmp_path / "numpy_root_nonexistent", natives=set()
        )


def test_reject_pre_bound_native_mappings_flags_fake_libc_outside_system_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """basename が `libc.so.6` でも、正規システムディレクトリ外なら許容しない（Codex 10 巡目 P1-A）。

    旧実装は basename の命名規約一致だけで OS 基盤として無条件許容していた——
    `/tmp/evil/libc.so.6` のように所有権の無い場所に置かれた同名ファイルも
    素通りしていた（本テストは是正前の旧名 `test_reject_pre_bound_native_mappings_
    ignores_os_baseline_mapping` が固定していた振る舞いそのものを反転する）。
    ここでは default-deny の記録対象（即 raise ではない）に落ちることを固定する。
    """
    unrelated_libc = tmp_path / "somewhere" / "libc.so.6"
    unrelated_libc.parent.mkdir()
    unrelated_libc.write_bytes(b"not-really-libc-but-named-like-it")
    monkeypatch.setattr(
        harness,
        "_parse_proc_self_maps_executable_mappings",
        lambda: [(str(unrelated_libc.resolve()), False)],
    )
    recorded = harness._reject_pre_bound_native_mappings(
        "numpy", package_root=tmp_path / "numpy_root_nonexistent", natives=set()
    )  # 例外は出ないが、default-deny の 2 段構え記録には入る
    assert recorded == [str(unrelated_libc.resolve())]


def test_reject_pre_bound_native_mappings_allows_real_os_baseline_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """正規システムディレクトリ配下の OS 基盤マッピングは許容し記録もしない（回帰）。

    `test_reject_pre_bound_native_mappings_allows_deleted_os_baseline` の
    `deleted=False` 版——実パスの検証（P1-A）が正しく通ることを固定する。
    """
    real_libc = Path("/usr/lib/x86_64-linux-gnu/libc.so.6")
    if not real_libc.is_file():
        pytest.skip("この環境に /usr/lib/x86_64-linux-gnu/libc.so.6 が無い")
    monkeypatch.setattr(
        harness,
        "_parse_proc_self_maps_executable_mappings",
        lambda: [(str(real_libc), False)],
    )
    recorded = harness._reject_pre_bound_native_mappings(
        "numpy", package_root=tmp_path / "numpy_root_nonexistent", natives=set()
    )  # 例外が出ないことが期待値
    assert recorded == []


def test_reject_pre_bound_native_mappings_allows_scorer_owned_mapping(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """package_root 配下からのマッピングは所有物として許容する——ただし記録はする。

    （Codex P1 7 巡目・2 段構え）「良性だから見ない」ではなく「良性だが記録する」。
    raise しないが、束縛前に既にロード済みだった事実は戻り値（と `_PRE_BOUND_
    NATIVE_MAPPING_LOG`）に残る——実測経路（`evaluate_m2_bars`）だけがこれを
    fail-closed の材料にする。
    """
    package_root = tmp_path / "numpy"
    package_root.mkdir()
    owned_ext = package_root / "_core.cpython-311-x86_64-linux-gnu.so"
    owned_ext.write_bytes(b"ext")
    monkeypatch.setattr(
        harness,
        "_parse_proc_self_maps_executable_mappings",
        lambda: [(str(owned_ext.resolve()), False)],
    )
    recorded = harness._reject_pre_bound_native_mappings(
        "numpy", package_root=package_root, natives=set()
    )  # 例外が出ないことが期待値
    assert recorded == [str(owned_ext.resolve())]
    assert harness._PRE_BOUND_NATIVE_MAPPING_LOG == [str(owned_ext.resolve())]


def test_reject_pre_bound_native_mappings_allows_byte_identical_duplicate_vendoring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """basename・所在が pin 対象と食い違っても bytes が一致すれば良性（Codex P1 6 巡目 P1-B）。

    実装時の実測で判明した良性の衝突: numpy と scipy が同一 gfortran ビルドを
    それぞれ `.libs/` へ同梱すると、auditwheel の content-hash 命名規約により
    **同じ basename・同じ bytes** のファイルが 2 か所に存在する。ELF ローダは
    `DT_SONAME` 単位でロード済み実体を再利用するため、どちらの物理コピーが実際に
    マップされてもおかしくない——bytes が一致する限り fail-closed にしない。
    """
    package_root = tmp_path / "scipy"
    package_root.mkdir()
    pinned = tmp_path / "scipy.libs" / "libgfortran-abc123.so.5.0.0"
    pinned.parent.mkdir()
    pinned.write_bytes(b"identical-gfortran-bytes")
    mapped_elsewhere = tmp_path / "numpy.libs" / "libgfortran-abc123.so.5.0.0"
    mapped_elsewhere.parent.mkdir()
    mapped_elsewhere.write_bytes(b"identical-gfortran-bytes")  # 同一 bytes・別ディレクトリ
    monkeypatch.setattr(
        harness,
        "_parse_proc_self_maps_executable_mappings",
        lambda: [(str(mapped_elsewhere.resolve()), False)],
    )
    recorded = harness._reject_pre_bound_native_mappings(
        "scipy", package_root=package_root, natives={pinned.resolve()}
    )  # 例外が出ないことが期待値
    # bytes 一致でも「起きた」事実として記録する（Codex P1 7 巡目・2 段構え）。
    assert recorded == [str(mapped_elsewhere.resolve())]


def test_reject_pre_bound_native_mappings_flags_content_mismatch_for_pin_target_basename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同名 pin 対象でも bytes が違えばすり替えの疑いとして fail-closed。"""
    package_root = tmp_path / "scipy"
    package_root.mkdir()
    pinned = tmp_path / "scipy.libs" / "libgfortran-abc123.so.5.0.0"
    pinned.parent.mkdir()
    pinned.write_bytes(b"legit-bytes")
    shadow = tmp_path / "elsewhere" / "libgfortran-abc123.so.5.0.0"
    shadow.parent.mkdir()
    shadow.write_bytes(b"tampered-bytes")
    monkeypatch.setattr(
        harness,
        "_parse_proc_self_maps_executable_mappings",
        lambda: [(str(shadow.resolve()), False)],
    )
    with pytest.raises(RuntimeError, match="別所在"):
        harness._reject_pre_bound_native_mappings(
            "scipy", package_root=package_root, natives={pinned.resolve()}
        )


def test_reject_pre_bound_native_mappings_flags_unexplained_default_deny(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H5 default-deny: 所有・OS基盤・first-party 連鎖のどれでもないマッピングは記録対象。

    BLAS 命名にも pin 対象 basename にも一致しない、無関係を装った任意名ライブラリ
    （`RTLD_GLOBAL` dlopen によるシンボル割り込みの疑い）も、6 巡目までは無条件で
    見逃していた——default-deny 反転後は記録（実測経路で fail-closed）対象になる。
    """
    fake_arbitrary = tmp_path / "libtotally_unrelated_name.so.1"
    fake_arbitrary.write_bytes(b"whatever")
    monkeypatch.setattr(
        harness,
        "_parse_proc_self_maps_executable_mappings",
        lambda: [(str(fake_arbitrary.resolve()), False)],
    )
    recorded = harness._reject_pre_bound_native_mappings(
        "numpy", package_root=tmp_path / "numpy_root_nonexistent", natives=set()
    )  # 例外は出ない（記録して実測経路に委ねる）
    assert recorded == [str(fake_arbitrary.resolve())]


def test_reject_pre_bound_native_mappings_allows_sibling_scorer_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """numpy⇔scipy 相互の `.libs/` 実体は互いの pre-bind 検査で誤検出しない（実測で判明）。

    numpy/scipy が両方 import 済みの環境（demucs/crepe 経由等）で `scipy` の検査を
    行うと、`numpy.libs/` 由来のファイルが既にマップされていることがある——これを
    「所有パス外の BLAS 系ライブラリ」と誤認しないことを固定する。
    """
    scipy_root = tmp_path / "scipy"
    scipy_root.mkdir()
    numpy_root = tmp_path / "numpy"
    numpy_root.mkdir()
    numpy_libs = tmp_path / "numpy.libs"
    numpy_libs.mkdir()
    sibling_native = numpy_libs / "libscipy_openblas64_-cafef00d.so"
    sibling_native.write_bytes(b"numpys-own-openblas")
    monkeypatch.setattr(
        harness,
        "_parse_proc_self_maps_executable_mappings",
        lambda: [(str(sibling_native.resolve()), False)],
    )
    monkeypatch.setattr(
        harness,
        "_sibling_scorer_backend_roots",
        lambda exclude: [numpy_root, numpy_libs],
    )
    recorded = harness._reject_pre_bound_native_mappings(
        "scipy", package_root=scipy_root, natives=set()
    )  # 例外が出ないことが期待値
    assert recorded == []  # 兄弟 scorer パッケージの所有物なので記録もしない


def test_ldconfig_cache_paths_by_soname_covers_real_multiarch_libc() -> None:
    """`_ldconfig_cache_paths_by_soname()` は実測（ldconfig）で実環境の libc を拾う。

    アーキテクチャ名（`x86_64-linux-gnu` 等）をハードコードせず、この計算機の
    `ldconfig -p` キャッシュが実際に指す soname → exact path を動的に確定することを
    固定する（Codex 10 巡目 P1-A の後継。14 巡目 P1-A でディレクトリメンバシップから
    exact path 一致へ切り替えたため、対象を `_system_library_directories()` から
    `_ldconfig_cache_paths_by_soname()` へ置き換える）。
    """
    import svp_rpe.melody.provenance as provenance

    by_soname = provenance._ldconfig_cache_paths_by_soname()
    assert by_soname, "ldconfig cache から soname が 1 つも見つからない"
    real_libc = Path("/usr/lib/x86_64-linux-gnu/libc.so.6")
    if real_libc.is_file():
        assert real_libc.resolve() in by_soname.get("libc.so.6", frozenset())
        assert provenance._is_ldconfig_registered_path("libc.so.6", real_libc.resolve())


def test_ldconfig_invoked_from_trusted_absolute_path_ignores_path_hijack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """PATH 上の偽 ldconfig ではなく、信頼できる絶対パスの本物が実行される（Codex 13 巡目 P1-A）。

    `_ldconfig_cache_listing`（`svp_rpe.melody.provenance` の共有ヘルパー。
    `_resolve_soname_without_loading` と `_ldconfig_cache_paths_by_soname`/
    `_is_ldconfig_registered_path`（Codex 14 巡目 P1-A）の両方がこれ経由で
    ldconfig を呼ぶ）は非修飾コマンドを一切使わないため、PATH に別ディレクトリを
    吐く偽 `ldconfig` を先頭に置いても無視されることを固定する。
    """
    import svp_rpe.melody.provenance as provenance

    fake_bin_dir = tmp_path / "evil_bin"
    fake_bin_dir.mkdir()
    fake_ldconfig = fake_bin_dir / "ldconfig"
    fake_ldconfig.write_text("#!/bin/sh\necho 'FAKE_HIJACKED_LDCONFIG_OUTPUT'\n")
    fake_ldconfig.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    invoked_commands: "list[list[str]]" = []
    real_subprocess_run = subprocess.run

    def _spy_run(command: Any, *args: Any, **kwargs: Any) -> Any:
        invoked_commands.append(list(command))
        return real_subprocess_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _spy_run)

    output = provenance._ldconfig_cache_listing()

    assert invoked_commands, "ldconfig の subprocess 呼び出しが発生していない"
    executed = invoked_commands[0][0]
    assert Path(executed).is_absolute(), f"非絶対パスで ldconfig を実行した: {executed}"
    assert executed != str(fake_ldconfig)
    assert executed in provenance._TRUSTED_LDCONFIG_CANDIDATES
    assert "FAKE_HIJACKED_LDCONFIG_OUTPUT" not in output


def test_ldconfig_subprocess_env_is_locked_to_trusted_path_and_strips_ld_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ldconfig サブプロセスの `PATH` を最小集合へ固定し、`LD_*` を除去する（P1-A）。"""
    import svp_rpe.melody.provenance as provenance

    monkeypatch.setenv("LD_PRELOAD", "/tmp/evil.so")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/evil_lib")
    monkeypatch.setenv("LD_AUDIT", "/tmp/evil_audit.so")

    captured_env: "dict[str, Any]" = {}

    class _Completed:
        stdout = ""

    def _spy_run(command: Any, *args: Any, **kwargs: Any) -> Any:
        captured_env.update(kwargs.get("env") or {})
        return _Completed()

    monkeypatch.setattr(subprocess, "run", _spy_run)

    provenance._ldconfig_cache_listing()

    assert captured_env.get("PATH") == provenance._TRUSTED_SUBPROCESS_PATH
    for var in ("LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT"):
        assert var not in captured_env, f"{var} がサブプロセス env に漏れている"


def test_hardened_subprocess_env_strips_gconv_and_locale_vectors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`GCONV_PATH`/`GLIBC_TUNABLES`/`LOCPATH` 等も硬化 env から除去する（Codex 13 巡目 H20）。

    14 巡目 P1-B で `_hardened_subprocess_env()` は blocklist（個別列挙して除去）から
    allowlist（`_HARDENED_SUBPROCESS_ENV_ALLOWLIST` に明示したものだけ通す）へ反転
    した——これらの変数はいずれも allowlist に**含まれない**ため、iconv/gconv
    モジュールロードや CPU dispatch を歪める既知ベクトルは列挙に依らず一律で塞がれる。
    ldconfig（P1-A）・git（H19）の両方の信頼実行が共有する。
    """
    import svp_rpe.melody.provenance as provenance

    leak_vars = ("GCONV_PATH", "GLIBC_TUNABLES", "LOCPATH", "NLSPATH", "GETCONF_DIR")
    for var in leak_vars:
        monkeypatch.setenv(var, "/tmp/evil")

    env = provenance._hardened_subprocess_env()

    assert env.get("PATH") == provenance._TRUSTED_SUBPROCESS_PATH
    for var in leak_vars:
        assert var not in env, f"{var} が硬化 env に漏れている"
    # Codex 14 巡目 P1-B: allowlist 方式へ反転したため、宣言（allowlist）に
    # 含まれていない = 通らない、という向きで固定する。
    for var in leak_vars:
        assert var not in provenance._HARDENED_SUBPROCESS_ENV_ALLOWLIST


def test_trusted_ldconfig_fails_closed_when_no_absolute_candidate_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """絶対パス候補が 1 つも実在しない環境なら PATH フォールバックせず fail-closed（P1-A）。"""
    import svp_rpe.melody.provenance as provenance

    monkeypatch.setattr(
        provenance,
        "_TRUSTED_LDCONFIG_CANDIDATES",
        ("/nonexistent/sbin/ldconfig", "/nonexistent/usr/sbin/ldconfig"),
    )
    with pytest.raises(RuntimeError, match="trusted ldconfig binary not found"):
        provenance._trusted_ldconfig_executable()
    with pytest.raises(RuntimeError, match="trusted ldconfig binary not found"):
        provenance._ldconfig_cache_listing()
    # `_resolve_soname_without_loading` 経由でも同じ fail-closed が伝播すること
    # （rpath/runpath/LD_LIBRARY_PATH のどれも当たらず ldconfig に落ちる経路）。
    monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)
    with pytest.raises(RuntimeError, match="trusted ldconfig binary not found"):
        provenance._resolve_soname_without_loading("libtotallymadeup.so.99")


def test_ldconfig_cache_paths_by_soname_fails_closed_when_no_trusted_ldconfig(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_ldconfig_cache_paths_by_soname()` も同じ fail-closed を継承する（P1-A の後継）。

    共有ヘルパー `_ldconfig_cache_listing` 経由なので、consumer 側で個別に
    ldconfig 存在チェックを実装し直す必要がないことを固定する。
    """
    import svp_rpe.melody.provenance as provenance

    monkeypatch.setattr(
        provenance, "_TRUSTED_LDCONFIG_CANDIDATES", ("/nonexistent/ldconfig",)
    )
    with pytest.raises(RuntimeError, match="trusted ldconfig binary not found"):
        provenance._ldconfig_cache_paths_by_soname()
    with pytest.raises(RuntimeError, match="trusted ldconfig binary not found"):
        provenance._is_ldconfig_registered_path("libc.so.6", Path("/usr/lib/libc.so.6"))


def test_reject_pre_bound_native_mappings_allows_first_party_bind_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """本ハーネス自身が束縛前に import する first-party 依存（pydantic_core 等）は許容する。"""
    package_root = tmp_path / "numpy"
    package_root.mkdir()
    pydantic_root = tmp_path / "pydantic_core"
    pydantic_root.mkdir()
    native = pydantic_root / "_pydantic_core.cpython-311-x86_64-linux-gnu.so"
    native.write_bytes(b"pydantic-native")
    monkeypatch.setattr(
        harness,
        "_parse_proc_self_maps_executable_mappings",
        lambda: [(str(native.resolve()), False)],
    )
    monkeypatch.setattr(
        harness, "_first_party_bind_chain_native_roots", lambda: [pydantic_root]
    )
    recorded = harness._reject_pre_bound_native_mappings(
        "numpy", package_root=package_root, natives=set()
    )  # 例外が出ないことが期待値
    assert recorded == []


def test_reject_pre_bound_native_mappings_allows_stdlib_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stdlib C 拡張（`lib-dynload/*.so` 等）は default-deny の許容クラス。"""
    package_root = tmp_path / "numpy"
    package_root.mkdir()
    stdlib_dir = tmp_path / "stdlib_prefix"
    lib_dynload = stdlib_dir / "lib-dynload"
    lib_dynload.mkdir(parents=True)
    ext = lib_dynload / "_bz2.cpython-311-x86_64-linux-gnu.so"
    ext.write_bytes(b"stdlib-ext")
    monkeypatch.setattr(
        harness,
        "_parse_proc_self_maps_executable_mappings",
        lambda: [(str(ext.resolve()), False)],
    )
    monkeypatch.setattr(harness, "_stdlib_prefixes", lambda: [stdlib_dir.resolve()])
    recorded = harness._reject_pre_bound_native_mappings(
        "numpy", package_root=package_root, natives=set()
    )  # 例外が出ないことが期待値
    assert recorded == []


def test_reject_pre_bound_native_mappings_flags_deleted_non_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`(deleted)` かつ OS 基盤でない実行マッピングは即 fail-closed（セルフレビュー H6）。

    削除済み実体は hash 突合が原理的に不能——bytes 一致による救済の余地が無い。
    """
    monkeypatch.setattr(
        harness,
        "_parse_proc_self_maps_executable_mappings",
        lambda: [("/usr/lib/x86_64-linux-gnu/libscipy_openblas_evil.so", True)],
    )
    with pytest.raises(RuntimeError, match="削除済み"):
        harness._reject_pre_bound_native_mappings(
            "numpy", package_root=tmp_path / "numpy_root_nonexistent", natives=set()
        )


def test_reject_pre_bound_native_mappings_allows_deleted_os_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`(deleted)` でも OS ツールチェーン（`apt upgrade` 置換等）は良性の通常運転。"""
    monkeypatch.setattr(
        harness,
        "_parse_proc_self_maps_executable_mappings",
        lambda: [("/usr/lib/x86_64-linux-gnu/libc.so.6", True)],
    )
    recorded = harness._reject_pre_bound_native_mappings(
        "numpy", package_root=tmp_path / "numpy_root_nonexistent", natives=set()
    )  # 例外が出ないことが期待値
    assert recorded == []


def test_reject_pre_bound_native_mappings_flags_memfd_fileless_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`memfd:` 由来の fileless 実行マッピングは即 fail-closed（セルフレビュー H6）。"""
    monkeypatch.setattr(
        harness,
        "_parse_proc_self_maps_executable_mappings",
        lambda: [("/memfd:pwn", True)],
    )
    with pytest.raises(RuntimeError, match="memfd"):
        harness._reject_pre_bound_native_mappings(
            "numpy", package_root=tmp_path / "numpy_root_nonexistent", natives=set()
        )


def test_reject_pre_bound_native_mappings_flags_extensionless_executable_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """拡張子なしの実行マッピング（本プロセスの解釈系実行ファイル以外）は fail-closed。"""
    monkeypatch.setattr(
        harness,
        "_parse_proc_self_maps_executable_mappings",
        lambda: [("/tmp/blob-without-extension", False)],
    )
    with pytest.raises(RuntimeError, match="拡張子なし"):
        harness._reject_pre_bound_native_mappings(
            "numpy", package_root=tmp_path / "numpy_root_nonexistent", natives=set()
        )


def test_reject_pre_bound_native_mappings_allows_interpreter_executable_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """本プロセスの解釈系実行ファイル自身（拡張子なし）は許容する。

    `/proc/self/maps` はカーネルが解決した実ファイルパスを報告する（`sys.executable`
    がシンボリックリンク経由の起動コマンドでも、maps 上は解決済みの実体パスになる）
    ため、テストでも `.resolve()` 後の文字列を使う。
    """
    import sys as _sys

    resolved_executable = str(Path(_sys.executable).resolve())
    monkeypatch.setattr(
        harness,
        "_parse_proc_self_maps_executable_mappings",
        lambda: [(resolved_executable, False)],
    )
    recorded = harness._reject_pre_bound_native_mappings(
        "numpy", package_root=tmp_path / "numpy_root_nonexistent", natives=set()
    )  # 例外が出ないことが期待値
    assert recorded == []


def test_reject_pre_bound_native_mappings_allows_interpreter_shared_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--enable-shared` ビルドの CPython 自身の共有ライブラリ実体は許容する。

    実行ファイル自身（`sys.executable`）とは別の実体（`libpython3.X.so.1.0` 等）が
    stdlib prefix 外（例 hostedtoolcache の `lib/` 直下）に置かれる CI 実測
    （PR #225 9 巡目: `test_reverification_refuses_when_stack_cannot_rerun` が
    Python 3.12 の測り直し子プロセスで `libpython3.12.so.1.0` を default-deny 対象
    として記録し失敗）を固定する。本環境が static ビルドで `_interpreter_shared_library_paths()`
    が空の場合は、この許容クラスを経由せず素通しで通ることを確認する意味が薄れるため、
    実体を monkeypatch で注入して常に検証可能にする。
    """
    fake_lib = tmp_path / "libpython3.99.so.1.0"
    fake_lib.write_bytes(b"\x7fELF fake shared library")
    monkeypatch.setattr(harness, "_interpreter_shared_library_paths", lambda: [fake_lib.resolve()])
    monkeypatch.setattr(
        harness,
        "_parse_proc_self_maps_executable_mappings",
        lambda: [(str(fake_lib.resolve()), False)],
    )
    recorded = harness._reject_pre_bound_native_mappings(
        "numpy", package_root=tmp_path / "numpy_root_nonexistent", natives=set()
    )  # 例外が出ないことが期待値
    assert recorded == []


def test_interpreter_shared_library_paths_resolves_via_sysconfig(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`sysconfig` の `LIBDIR`/`LDLIBRARY`/`INSTSONAME` からハードコードなしで解決する。"""
    fake_lib = tmp_path / "libpython9.9.so.1.0"
    fake_lib.write_bytes(b"fake")

    original_get_config_var = sysconfig.get_config_var

    def _fake_get_config_var(name: str) -> Any:
        if name == "LIBDIR":
            return str(tmp_path)
        if name == "LDLIBRARY":
            return fake_lib.name
        if name == "INSTSONAME":
            return None
        return original_get_config_var(name)

    monkeypatch.setattr(sysconfig, "get_config_var", _fake_get_config_var)
    paths = harness._interpreter_shared_library_paths()
    assert paths == [fake_lib.resolve()]

    # static ビルド（LIBDIR はあるが実体が無い）では空を返す。
    def _fake_get_config_var_missing(name: str) -> Any:
        if name == "LIBDIR":
            return str(tmp_path)
        if name in ("LDLIBRARY", "INSTSONAME"):
            return "libpython_does_not_exist.so"
        return original_get_config_var(name)

    monkeypatch.setattr(sysconfig, "get_config_var", _fake_get_config_var_missing)
    assert harness._interpreter_shared_library_paths() == []


def test_reject_pre_bound_native_mappings_allows_kernel_anonymous_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`[vdso]`/`[vsyscall]` はカーネル注入の無害な匿名実行マッピング（セルフレビュー H11）。"""
    monkeypatch.setattr(
        harness,
        "_parse_proc_self_maps_executable_mappings",
        lambda: [("[vdso]", False), ("[vsyscall]", False)],
    )
    recorded = harness._reject_pre_bound_native_mappings(
        "numpy", package_root=tmp_path / "numpy_root_nonexistent", natives=set()
    )  # 例外が出ないことが期待値
    assert recorded == []


def test_reject_pre_bound_native_mappings_flags_non_allowlisted_anonymous_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """許容外の匿名実行マッピングは JIT/手動 mmap ロードの疑いとして fail-closed（H11）。"""
    monkeypatch.setattr(
        harness,
        "_parse_proc_self_maps_executable_mappings",
        lambda: [("[some_unexpected_anon_region]", False)],
    )
    with pytest.raises(RuntimeError, match="匿名実行マッピング"):
        harness._reject_pre_bound_native_mappings(
            "numpy", package_root=tmp_path / "numpy_root_nonexistent", natives=set()
        )


def test_reject_pre_bound_native_mappings_records_anonymous_mapping_when_post_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`treat_anonymous_as_recorded=True` は run 完了後再検証限定の緩和（既定は変えない）。

    `_require_unchanged_since_load()` は実抽出（CREPE/TensorFlow 等）が既に走った後の
    自己整合性再検証で `_scorer_pins(treat_anonymous_as_recorded=True)` を呼ぶ——この
    経路でのみ非許容匿名マッピングは即 raise せず記録に倒す（CI 実測: PR #225 9 巡目、
    `test_cli_run_categories_flag_limits_run` が実 CREPE/TensorFlow 環境で
    `'[anonymous]'` を検出して post-run 再検証がクラッシュしていた）。既定
    （`treat_anonymous_as_recorded` 省略時 = False）は前のテストと同じ厳格挙動のまま
    であることも併せて確認する。
    """
    monkeypatch.setattr(
        harness,
        "_parse_proc_self_maps_executable_mappings",
        lambda: [("[some_jit_region_from_unrelated_library]", False)],
    )
    recorded = harness._reject_pre_bound_native_mappings(
        "numpy",
        package_root=tmp_path / "numpy_root_nonexistent",
        natives=set(),
        treat_anonymous_as_recorded=True,
    )
    assert recorded == ["[some_jit_region_from_unrelated_library]"]
    # 既定（省略）は変わらず即 fail-closed。
    with pytest.raises(RuntimeError, match="匿名実行マッピング"):
        harness._reject_pre_bound_native_mappings(
            "numpy", package_root=tmp_path / "numpy_root_nonexistent", natives=set()
        )


def test_require_unchanged_since_load_survives_post_execution_anonymous_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """post-run 再検証は非許容匿名マッピングだけでクラッシュしない（report pin は不変）。

    CI 実測（PR #225 9 巡目・`test_cli_run_categories_flag_limits_run` が実 CREPE/
    TensorFlow 環境で `run_accuracy()` 末尾の `_require_unchanged_since_load()` から
    `'[anonymous]'` 検出で fail-closed していた）の直接再現: 実抽出後に生成された
    無関係な JIT 匿名領域だけを `/proc/self/maps` の代わりに返しても、
    `_require_unchanged_since_load()` は `treat_anonymous_as_recorded=True` 経由で
    これを記録に倒すだけで raise しない——native hash 自体（disk 上の実体）は
    monkeypatch 前と変わらないため、load 時に凍結した pin との一致比較も崩れない。
    """
    monkeypatch.setattr(
        harness,
        "_parse_proc_self_maps_executable_mappings",
        lambda: [("[some_jit_region_from_unrelated_library]", False)],
    )
    harness._require_unchanged_since_load()  # 例外が出ないことが期待値


def test_scorer_runtime_packages_are_always_in_runtime_package_names() -> None:
    """スコアラー閉包は route の抽出器選択に関係なく `_runtime_package_names()` に入る。

    scipy は `melody/provenance._EXTRACTOR_CODE_PACKAGES` の `pyin` route にしか
    登録が無い。本ハーネスの既定カテゴリは `crepe_direct` /
    `demucs_vocals_then_crepe` route を使うため、抽出器登録表からの導出だけに頼ると
    scipy が監視集合から漏れる——mir_eval が直接実行するにもかかわらず。
    """
    runtime = set(harness._runtime_package_names())
    for name in harness._SCORER_RUNTIME_PACKAGES:
        assert name in runtime, sorted(runtime)


def test_run_accuracy_detects_scorer_change_during_execution(monkeypatch) -> None:
    """実行中に mir_eval が差し替わったら fail-closed（旧スコアラーで測った run）。

    Codex 12 巡目 P1（H14 対象範囲の一般化）以降、この改ざんは post-run の
    `_require_unchanged_since_load`（旧: 唯一の検出経路・メッセージ「mir_eval が
    実行中に差し替わった」）より**先に**、mid-run checkpoint
    （`_require_scorer_native_unchanged_since_bind`。各カテゴリ処理直後に走り、
    対象を `_SCORER_RUNTIME_PACKAGES` 全体——mir_eval を含む——へ一般化した）が
    検出するようになった。検出そのものは失われておらず、むしろ検出タイミングが
    早まっている（run 完走を待たず最初のカテゴリ処理直後に fail-closed）——
    期待するメッセージを mid-run checkpoint 側の実文言に更新する。
    """
    monkeypatch.setattr(
        harness, "_LOADED_SCORER_PINS", {"mir_eval_version": "0.0", "mir_eval_code_sha256": "0" * 64}
    )
    with pytest.raises(
        RuntimeError, match=r"'mir_eval'.*コード実体（code pin）.*bind→import 窓で"
    ):
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


def test_require_scorer_native_unchanged_since_bind_passes_in_real_environment() -> None:
    """H14: import 済み numpy/scipy の native 実体が束縛時点と一致する（回帰）。"""
    harness._require_scorer_native_unchanged_since_bind()  # 例外が出ないことが期待値


def test_require_scorer_native_unchanged_since_bind_skips_unimported_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H14: まだ import されていないパッケージは静かにスキップする。"""
    monkeypatch.delitem(sys.modules, "scipy", raising=False)
    called_with: List[str] = []

    def _fake_dist_native_sha256(name: str, **kwargs: Any) -> str:
        called_with.append(name)
        return harness._LOADED_SCORER_PINS[f"{name}_dist_native_sha256"]

    monkeypatch.setattr(harness, "_scorer_dist_native_sha256", _fake_dist_native_sha256)
    harness._require_scorer_native_unchanged_since_bind()  # 例外が出ないことが期待値
    assert "scipy" not in called_with  # sys.modules に無いので検査自体が呼ばれない
    assert "numpy" in called_with  # numpy は import 済みなので検査が走る


def test_require_scorer_native_unchanged_since_bind_fails_closed_on_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H14: import 完了後の再 hash が束縛時点の pin と食い違えば fail-closed。

    holes2.md H14 が指摘した「bind→import の窓」の直接固定: `_scorer_dist_native_
    sha256` の再計算結果が束縛時点の pin と異なる状況（native が差し替えられた
    ふり）を模す。
    """
    monkeypatch.setattr(
        harness, "_scorer_dist_native_sha256", lambda name, **k: "f" * 64
    )
    with pytest.raises(RuntimeError, match="import 完了後の再"):
        harness._require_scorer_native_unchanged_since_bind()


def test_require_scorer_native_unchanged_since_bind_skips_when_bind_pin_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H14: 束縛時点で pin できなかったパッケージは（他ゲートに委ね）静かにスキップする。"""
    patched_pins = dict(harness._LOADED_SCORER_PINS)
    for name in harness._SCORER_NATIVE_BACKEND_REQUIRED:
        patched_pins[f"{name}_dist_native_sha256"] = None
    monkeypatch.setattr(harness, "_LOADED_SCORER_PINS", patched_pins)
    monkeypatch.setattr(
        harness,
        "_scorer_dist_native_sha256",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("呼ばれてはならない")),
    )
    harness._require_scorer_native_unchanged_since_bind()  # 例外が出ないことが期待値


def test_require_scorer_native_unchanged_since_bind_fails_closed_on_code_pin_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex 12 巡目 P1: package-root native カーネル本体（code pin）が bind→import
    窓で差し替えられた場合も fail-closed する。

    12 巡目レビュー指摘: 旧実装はこの checkpoint で `{name}_dist_native_sha256`
    （wheel 同梱 `.libs/`）しか再検証しておらず、docstring が名指しする実カーネル
    本体（`numpy/_core/_multiarray_umath.cpython-*.so` 等・package root 配下）は
    `{name}_code_sha256` が pin する対象だったが再検証されていなかった。ここでは
    `package_code_sha256` の再計算結果だけを束縛時点の pin と食い違わせ（dist_native
    側は変えない）、この checkpoint が検出できることを固定する。numpy（native 必須
    パッケージ）だけを差し替え対象にすることで、native カーネル向けの文言分岐
    （`_SCORER_NATIVE_BACKEND_REQUIRED` 判定）を経由することを保証する。
    """
    original = harness.package_code_sha256

    def _fake(name: str, **kwargs: Any) -> Any:
        if name == "numpy":
            return "f" * 64
        return original(name, **kwargs)

    monkeypatch.setattr(harness, "package_code_sha256", _fake)
    with pytest.raises(RuntimeError, match=r"'numpy'.*package-root native カーネル（code pin）"):
        harness._require_scorer_native_unchanged_since_bind()


def test_require_scorer_native_unchanged_since_bind_code_check_covers_non_native_packages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex 12 巡目 P1: code pin 再検証は `_SCORER_NATIVE_BACKEND_REQUIRED`
    （numpy/scipy）に限らず、mir_eval のように「同梱ネイティブを持たない」
    パッケージにも及ぶ（required/optional を区別しない対象範囲の一般化）。
    """
    assert "mir_eval" not in harness._SCORER_NATIVE_BACKEND_REQUIRED
    assert "mir_eval" in sys.modules
    original = harness.package_code_sha256

    def _fake(name: str, **kwargs: Any) -> Any:
        if name == "mir_eval":
            return "f" * 64
        return original(name, **kwargs)

    monkeypatch.setattr(harness, "package_code_sha256", _fake)
    with pytest.raises(RuntimeError, match=r"'mir_eval'.*code pin"):
        harness._require_scorer_native_unchanged_since_bind()


def test_require_scorer_native_unchanged_since_bind_skips_code_check_when_pin_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex 12 巡目 P1: 束縛時点で code pin できなかったパッケージは code 再検証を
    静かにスキップする（`{name}_dist_native_sha256` の既存スキップ規約と対称）。
    """
    patched_pins = dict(harness._LOADED_SCORER_PINS)
    for name in harness._SCORER_RUNTIME_PACKAGES:
        patched_pins[f"{name}_code_sha256"] = None
    monkeypatch.setattr(harness, "_LOADED_SCORER_PINS", patched_pins)
    monkeypatch.setattr(
        harness,
        "package_code_sha256",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("呼ばれてはならない")),
    )
    harness._require_scorer_native_unchanged_since_bind()  # 例外が出ないことが期待値


def test_scorer_dist_native_sha256_verify_pre_bind_gates_false_skips_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """セルフレビュー第三弾 H17: `verify_pre_bind_gates=False` は pre-bind gate
    （`_reject_pre_bound_native_mappings` の maps スキャン + `_verify_scorer_
    dt_needed_closure` の DT_NEEDED 再検証）を丸ごと skip し、純粋なディスク
    hash 比較のみ行う。"""
    monkeypatch.setattr(
        harness,
        "_reject_pre_bound_native_mappings",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("呼ばれてはならない")),
    )
    monkeypatch.setattr(
        harness,
        "_verify_scorer_dt_needed_closure",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("呼ばれてはならない")),
    )
    digest = harness._scorer_dist_native_sha256("numpy", use_cache=False, verify_pre_bind_gates=False)
    assert harness._is_sha256(digest)


def test_scorer_dist_native_sha256_verify_pre_bind_gates_true_still_runs_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """既定（`verify_pre_bind_gates=True`）は従来どおり pre-bind gate を実行する（回帰）。"""
    called: List[str] = []
    original = harness._reject_pre_bound_native_mappings

    def _spy(*args: Any, **kwargs: Any) -> Any:
        called.append("called")
        return original(*args, **kwargs)

    monkeypatch.setattr(harness, "_reject_pre_bound_native_mappings", _spy)
    harness._scorer_dist_native_sha256("numpy")  # 既定 True
    assert called


def test_require_scorer_native_unchanged_since_bind_does_not_invoke_pre_bind_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """セルフレビュー第三弾 H17: mid-run checkpoint は pre-bind gate
    （`_reject_pre_bound_native_mappings`）を一切呼ばない（`verify_pre_bind_
    gates=False` で渡している）ことを直接固定する。"""
    monkeypatch.setattr(
        harness,
        "_reject_pre_bound_native_mappings",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("呼ばれてはならない")),
    )
    harness._require_scorer_native_unchanged_since_bind()  # 例外が出ないことが期待値


def test_require_scorer_native_unchanged_since_bind_survives_unrelated_memfd_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H17 の直接再現: mid-run checkpoint は pre-bind gate（maps スキャン）を
    再実行しないため、numpy/scipy と無関係な `memfd:` マッピング（実測機で
    numba/TensorFlow/torch 等が実抽出中に張る JIT 領域を模す）が
    `/proc/self/maps` に現れていても mid-run では fail-closed しない。

    旧実装（`_scorer_dist_native_sha256` 経由で pre-bind gate を mid-run でも
    再実行）ではこの `memfd:` エントリだけで即 `RuntimeError` になっていた
    （`test_reject_pre_bound_native_mappings_flags_memfd_fileless_mapping` が
    `_reject_pre_bound_native_mappings` 単体でその即時 raise を固定している）。
    """
    monkeypatch.setattr(
        harness,
        "_parse_proc_self_maps_executable_mappings",
        lambda: [("/memfd:jit-from-unrelated-backend", True)],
    )
    harness._require_scorer_native_unchanged_since_bind()  # 例外が出ないことが期待値


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
    """監視集合は手書きではなく登録表から導出される（登録表を絞れば集合も縮む）。

    スコアラー閉包（`_SCORER_RUNTIME_PACKAGES` = mir_eval + scipy + numpy）は route の
    抽出器選択に関係なく常にシードへ入る——scipy は `pyin` route の登録表にしか
    載っておらず、選ばれる route が非 pyin（crepe 系）だと抽出器由来の集合だけでは
    scipy が欠落するため。
    """
    from svp_rpe.melody import provenance

    runtime = set(harness._runtime_package_names())
    expected = set(harness._SCORER_RUNTIME_PACKAGES)
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


def test_evaluate_m2_bars_rejects_pre_bound_scorer_native_mappings_evaluator_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """評価器プロセス自身が scorer ネイティブの束縛前ロードを検出したら publish しない。

    （Codex P1 7 巡目・`_PRELOADED_SEED_MODULES` と同型の 2 段構え）正規の fresh CLI
    では `_scorer_pins()` の束縛が numpy/scipy の import より先に走るため
    `_PRE_BOUND_SCORER_NATIVE_MAPPINGS` は必ず空になる。実測経路（この評価器自身）の
    シミュレーションとして、凍結タプルを直接 monkeypatch で非空にし、fail-closed を
    確認する——mmap 済み実体は disk hash では検出できない (TOCTOU) ため。
    """
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    monkeypatch.setattr(
        harness,
        "_PRE_BOUND_SCORER_NATIVE_MAPPINGS",
        ("/usr/local/lib/python3.11/dist-packages/numpy.libs/libopenblas-fake.so.0",),
    )
    with pytest.raises(RuntimeError, match="束縛前"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_evaluate_m2_bars_rejects_non_standard_import_hooks_evaluator_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """評価器プロセス自身が非標準 import hook を検出したら publish しない（セルフレビュー H3）。

    `_clean_evaluator_preload` が `_NON_STANDARD_IMPORT_HOOKS` をテストごとに空へ
    正規化するため、ここでは直接 monkeypatch で非空にし実測経路の拒否を確認する。
    """
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    monkeypatch.setattr(
        harness, "_NON_STANDARD_IMPORT_HOOKS", ("meta_path:evil.EvilFinder",)
    )
    with pytest.raises(RuntimeError, match="非標準の import hook"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_evaluate_m2_bars_rejects_evaluator_process_running_with_optimize_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """評価器プロセス自身が `-O`/`-OO` 実行なら publish しない（セルフレビュー H9）。"""
    import types

    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    monkeypatch.setattr(sys, "flags", types.SimpleNamespace(optimize=1))
    with pytest.raises(RuntimeError, match="-O/-OO"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_non_standard_import_hooks_detects_injected_meta_path_finder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_non_standard_import_hooks` が標準 3 finder 以外を検出する（セルフレビュー H3 単体）。"""

    class _EvilFinder:
        pass

    monkeypatch.setattr(sys, "meta_path", [*sys.meta_path, _EvilFinder()])
    findings = harness._non_standard_import_hooks()
    assert any("EvilFinder" in f for f in findings), findings


def test_non_standard_import_hooks_allows_distutils_hack_finder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """実測で確認済みの `_distutils_hack.DistutilsMetaFinder` は許容する（H3 の教訓）。"""

    class _FakeDistutilsMetaFinder:
        pass

    _FakeDistutilsMetaFinder.__module__ = "_distutils_hack"
    _FakeDistutilsMetaFinder.__qualname__ = "DistutilsMetaFinder"
    monkeypatch.setattr(sys, "meta_path", [*sys.meta_path, _FakeDistutilsMetaFinder()])
    findings = harness._non_standard_import_hooks()
    assert not any("DistutilsMetaFinder" in f for f in findings), findings


def test_non_standard_import_hooks_clean_in_real_environment() -> None:
    """実環境（本プロセス）の meta_path/path_hooks/path_importer_cache は非標準ゼロ（回帰）。

    pytest 自身が挿す assertion-rewrite finder は autouse fixture が正規化しない限り
    非空になる——本テストは fixture 正規化「後」の値（モジュール属性）ではなく
    `_non_standard_import_hooks()` を直接呼ぶため、pytest 由来の非標準 1 件
    （`_pytest.assertion.rewrite.AssertionRewritingHook`）を許容した上で検証する。
    """
    findings = harness._non_standard_import_hooks()
    assert all("AssertionRewritingHook" in f for f in findings), findings


def test_qualname_of_distinguishes_classes_functions_and_instances() -> None:
    """`_qualname_of` がクラス・関数・インスタンスのどれでも正しい qualname を返す（回帰）。

    実装中に実測で踏んだ不具合: 関数を先に `type()` へ通すと `builtins.function` に
    潰れ、`FileFinder.path_hook` の closure が標準 hook と認識されなくなっていた。
    """

    def _sample_function() -> None:
        pass

    class _SampleClass:
        pass

    # クラスオブジェクト自体・関数・インスタンスのいずれも自身の qualname を返す
    # （`type()` に通してから読むと関数が `builtins.function` に潰れる不具合の回帰）。
    expected_class_qualname = f"{_SampleClass.__module__}.{_SampleClass.__qualname__}"
    expected_function_qualname = f"{_sample_function.__module__}.{_sample_function.__qualname__}"
    assert harness._qualname_of(_SampleClass) == expected_class_qualname
    assert harness._qualname_of(_sample_function) == expected_function_qualname
    assert harness._qualname_of(_SampleClass()) == expected_class_qualname
    assert harness._qualname_of(_sample_function) != "builtins.function"


def test_run_accuracy_records_numeric_runtime_config_and_execution_paths() -> None:
    """H7/H9: report に実行時数値構成・実行パス・-O フラグが記録される。"""
    report = harness.run_accuracy(
        categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0)
    )
    config = report["numeric_runtime_config"]
    # セルフレビュー第二弾 H15: numpy_simd_dispatch/threadpool_info を追加。
    assert set(config) == {
        "env",
        "cpu_count",
        "sched_affinity_count",
        "numpy_simd_dispatch",
        "threadpool_info",
    }
    assert isinstance(config["env"], dict)
    assert report["sys_flags_optimize"] == 0
    paths = report["execution_paths"]
    assert set(paths) == {"sys_path", "PYTHONPATH", "LD_LIBRARY_PATH", "sys_executable"}
    assert isinstance(paths["sys_path"], list)


def test_numeric_runtime_config_records_numpy_simd_dispatch_and_threadpool_info() -> None:
    """H15: SIMD dispatch 集合・threadpoolctl 実行時構成が記録される（record-completeness）。

    同一 env でも CPU が違えば別の SIMD カーネルが dispatch され、結果の数値が
    揺れうる——観測済みの `median_cent_error` 双安定の原因帰属を可能にする記録
    拡張（gate ではない）。
    """
    config = harness._numeric_runtime_config()
    simd = config["numpy_simd_dispatch"]
    assert simd is None or {"baseline", "found", "not_found"} <= set(simd)
    threadpool = config["threadpool_info"]
    assert threadpool is None or isinstance(threadpool, list)


def test_require_homogeneous_numeric_runtime_config_rejects_simd_dispatch_mismatch() -> None:
    """H15: numpy_simd_dispatch が repeats 間で食い違えば fail-closed（記録拡張は同質性検査対象）。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    reports[1]["numeric_runtime_config"] = dict(reports[1]["numeric_runtime_config"])
    reports[1]["numeric_runtime_config"]["numpy_simd_dispatch"] = {
        "baseline": ["X86_V2"],
        "found": [],
        "not_found": ["AVX512_ICL"],
    }
    with pytest.raises(ValueError, match="numeric_runtime_config が repeats 間で"):
        harness._require_homogeneous_numeric_runtime_config(reports)


def test_require_homogeneous_numeric_runtime_config_passes_when_identical() -> None:
    """repeats 間で numeric_runtime_config が一致していれば通る（H7）。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    result = harness._require_homogeneous_numeric_runtime_config(reports)
    assert result == reports[0]["numeric_runtime_config"]


def test_require_homogeneous_numeric_runtime_config_rejects_mismatch() -> None:
    """repeats 間で numeric_runtime_config が食い違えば fail-closed（H7）。

    実測済みの事故（`median_cent_error` のバッチ間往復 1.352838 ↔ 1.353400）は
    スレッド数等の実行時構成差が原因と推定される——この検査はその条件付き bit 一致を
    「決定論の証拠」として誤って verdict に載せないための同質性ゲート。
    """
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    reports[1]["numeric_runtime_config"] = dict(
        reports[1]["numeric_runtime_config"], cpu_count=1
    )
    with pytest.raises(ValueError, match="numeric_runtime_config"):
        harness._require_homogeneous_numeric_runtime_config(reports)


def test_require_homogeneous_numeric_runtime_config_rejects_missing_field() -> None:
    """`numeric_runtime_config` を欠く report は publish 可能な実測にしない（H7）。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    del reports[1]["numeric_runtime_config"]
    with pytest.raises(ValueError, match="numeric_runtime_config"):
        harness._require_homogeneous_numeric_runtime_config(reports)


def test_evaluate_m2_bars_rejects_repeats_with_mismatched_numeric_runtime_config() -> None:
    """evaluate 全体でも numeric_runtime_config の repeats 間不一致を fail-closed にする（H7）。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    reports[1]["numeric_runtime_config"] = dict(
        reports[1]["numeric_runtime_config"], sched_affinity_count=1
    )
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="numeric_runtime_config"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_evaluate_m2_bars_rejects_reports_with_optimize_flag() -> None:
    """report が `sys_flags_optimize != 0` を名乗るなら publish 可能な実測にしない（H9）。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    reports[0]["sys_flags_optimize"] = 2
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="-O/-OO"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_evaluate_m2_bars_rejects_reports_missing_optimize_flag_field() -> None:
    """`sys_flags_optimize` を欠く report は publish 可能な実測にしない（H9）。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    del reports[0]["sys_flags_optimize"]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="sys_flags_optimize"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_evaluate_m2_bars_rejects_reports_with_non_standard_import_hooks() -> None:
    """report が非標準 import hook を名乗るなら publish 可能な実測にしない（H3）。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    reports[0]["non_standard_import_hooks"] = ["meta_path:evil.EvilFinder"]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="非標準の import hook"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_evaluate_m2_bars_rejects_reports_missing_non_standard_import_hooks_field() -> None:
    """`non_standard_import_hooks` を欠く report は publish 可能な実測にしない（H3）。"""
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    del reports[0]["non_standard_import_hooks"]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="non_standard_import_hooks"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_require_scorer_modules_match_pinned_origin_passes_in_real_environment() -> None:
    """H8: 実環境で import 済み scorer module の origin が束縛時と一致する（回帰）。"""
    import numpy  # noqa: F401
    import scipy  # noqa: F401

    harness._require_scorer_modules_match_pinned_origin()  # 例外が出ないことが期待値


def test_require_scorer_modules_match_pinned_origin_fails_closed_on_origin_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """swap-and-restore の疑い（import 済み module の origin が束縛時と別）は fail-closed（H8）。"""
    monkeypatch.setitem(
        harness._SCORER_PINNED_ORIGINS, "numpy", "/totally/different/path/numpy/__init__.py"
    )
    with pytest.raises(RuntimeError, match="束縛時に解決した"):
        harness._require_scorer_modules_match_pinned_origin()


def test_require_scorer_modules_match_pinned_origin_fails_closed_on_non_source_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """import 済み module の loader が `SourceFileLoader` でなければ fail-closed（H8・H2 の実行時版）。"""
    import types as _types

    class _FakeLoader:
        pass

    fake_module = _types.SimpleNamespace(
        __spec__=_types.SimpleNamespace(
            origin=harness._SCORER_PINNED_ORIGINS["numpy"], loader=_FakeLoader()
        )
    )
    monkeypatch.setitem(sys.modules, "numpy", fake_module)
    with pytest.raises(RuntimeError, match="loader"):
        harness._require_scorer_modules_match_pinned_origin()


def test_require_scorer_kernel_submodules_match_pinned_origin_passes_in_real_environment() -> None:
    """H13: `mir_eval.melody`/`mir_eval.util`（指標カーネル）の origin/loader も検査対象（回帰）。

    `mir_eval.melody` はモジュール collection 時点で既に import 済み（H15 の
    warm-up）。`mir_eval.util` はここで初めて import する。
    """
    import mir_eval.util  # noqa: F401

    harness._require_scorer_modules_match_pinned_origin()  # 例外が出ないことが期待値


def test_require_scorer_kernel_submodules_match_pinned_origin_fails_closed_on_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H13: `mir_eval.melody` の origin 不一致（トップレベル `mir_eval` は無傷）も fail-closed。

    holes2.md H13 が指摘した「H8 の origin 検査はトップレベル名のみで、指標を実際に
    計算するサブモジュールは対象外」の直接再現——トップレベル `mir_eval` の origin は
    無傷のまま、`mir_eval.melody`（RPA/RCA を実際に計算するファイル）だけが
    swap-and-restore された状況を模す。
    """
    import types as _types

    fake_module = _types.SimpleNamespace(
        __spec__=_types.SimpleNamespace(
            origin="/totally/different/path/mir_eval/melody.py",
            loader=None,
        )
    )
    monkeypatch.setitem(sys.modules, "mir_eval.melody", fake_module)
    with pytest.raises(RuntimeError, match="束縛時に解決した"):
        harness._require_scorer_modules_match_pinned_origin()


def test_scorer_kernel_submodule_pinned_origins_resolves_without_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H13: サブモジュール origin の事前解決は import を起こさない（束縛規律の維持）。"""
    for name in ("mir_eval.melody", "mir_eval.util"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    origins = harness._scorer_kernel_submodule_pinned_origins()
    assert "mir_eval.melody" not in sys.modules
    assert "mir_eval.util" not in sys.modules
    assert origins["mir_eval.melody"].endswith("melody.py")
    assert origins["mir_eval.util"].endswith("util.py")


def test_audit_scorer_source_load_time_hash_normalizes_symlinked_filename(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """H13: symlink 経由の未解決 `filename` でも resolved キーで一致検出する。

    実測反証（holes2.md H13）: symlink 化 site-packages では compile イベントの
    `filename` が未解決パスで渡り、`.resolve()` 済みキーの期待値表と文字列不一致
    になって機構全体が無言 no-op になっていた。ここでは実体ファイル + symlink を
    実際に用意し、symlink 経由の未解決パスを `filename` として hook に渡しても
    正しく検出されることを固定する。
    """
    mismatches: List[str] = []
    observed: "set[str]" = set()
    monkeypatch.setattr(harness, "_SCORER_LOAD_TIME_HASH_MISMATCHES", mismatches)
    monkeypatch.setattr(harness, "_SCORER_COMPILE_OBSERVED_PATHS", observed)
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    real_path = real_dir / "fake_scorer.py"
    original_content = "x = 1\n"
    real_path.write_text(original_content, encoding="utf-8")
    expected = hashlib.sha256(original_content.encode("utf-8")).hexdigest()
    # 期待値表のキーは resolve 済み（`_mir_eval_paths()` の実装と同じ規約）。
    monkeypatch.setitem(
        harness._SCORER_LOAD_TIME_EXPECTED_HASHES, str(real_path.resolve()), expected
    )

    link_dir = tmp_path / "link"
    link_dir.symlink_to(real_dir)
    unresolved_filename = str(link_dir / "fake_scorer.py")
    assert unresolved_filename != str(real_path.resolve())  # 前提: 文字列としては不一致

    malicious_content = "x = 2  # malicious\n"
    harness._audit_scorer_source_load_time_hash(
        "compile", (malicious_content, unresolved_filename)
    )
    assert len(mismatches) == 1, "symlink 経由の filename でも検出されるはず"
    assert str(real_path.resolve()) in mismatches[0]
    # H16 の観測集合にも resolved 形式で記録される。
    assert str(real_path.resolve()) in observed


def test_scorer_load_time_expected_hashes_covers_mir_eval_paths() -> None:
    """P1-B の期待値表は `_mir_eval_paths()` の全 `.py` ファイルを覆う（Codex 10 巡目）。

    `_SCORER_LOAD_TIME_EXPECTED_HASHES` はモジュール load 時に 1 回だけ確定済み
    （束縛シーケンスの一部）。ここでは束縛済みの値そのものを検証する——再計算すると
    プロセス起動後にファイルが変わった環境差を拾ってしまうため、`_mir_eval_paths()`
    が**今**返す集合とキー集合が一致するとは限らない点に注意し、`.py` の部分集合
    関係とハッシュ形式だけを固定する。
    """
    py_paths = [p for p in harness._mir_eval_paths() if p.suffix == ".py"]
    assert py_paths, "スコアラー閉包の .py ファイルが解決できない（テストの前提が drift）"
    # 束縛時点でこれらの全ファイルがハッシュ済みであること（該当ファイルが束縛後に
    # 消えていない限り）。
    missing = [str(p) for p in py_paths if str(p) not in harness._SCORER_LOAD_TIME_EXPECTED_HASHES]
    assert not missing, f"期待値表に無い .py ファイルがある: {missing[:5]}"
    for digest in harness._SCORER_LOAD_TIME_EXPECTED_HASHES.values():
        assert re.fullmatch(r"[0-9a-f]{64}", digest)


def test_audit_scorer_source_load_time_hash_ignores_non_compile_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`"compile"` 以外のイベントは早期 return で無視する（Codex 10 巡目 P1-B・点 5）。"""
    mismatches: List[str] = []
    monkeypatch.setattr(harness, "_SCORER_LOAD_TIME_HASH_MISMATCHES", mismatches)
    harness._audit_scorer_source_load_time_hash("open", ("/etc/passwd", "r", None))
    harness._audit_scorer_source_load_time_hash("import", ("os", None, [], [], []))
    assert mismatches == []


def test_audit_scorer_source_load_time_hash_ignores_unrelated_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """期待値表に無いファイルの compile イベントは対象外（scorer 閉包外・大多数のケース）。"""
    mismatches: List[str] = []
    monkeypatch.setattr(harness, "_SCORER_LOAD_TIME_HASH_MISMATCHES", mismatches)
    unrelated = tmp_path / "unrelated.py"
    unrelated.write_text("y = 1\n", encoding="utf-8")
    harness._audit_scorer_source_load_time_hash("compile", ("y = 1\n", str(unrelated)))
    assert mismatches == []


def test_audit_scorer_source_load_time_hash_allows_matching_compile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """compile された source bytes が束縛時点の期待と一致すれば記録しない（健全系）。"""
    mismatches: List[str] = []
    monkeypatch.setattr(harness, "_SCORER_LOAD_TIME_HASH_MISMATCHES", mismatches)
    fake_path = tmp_path / "fake_scorer.py"
    content = "x = 1\n"
    fake_path.write_text(content, encoding="utf-8")
    expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
    monkeypatch.setitem(harness._SCORER_LOAD_TIME_EXPECTED_HASHES, str(fake_path), expected)
    harness._audit_scorer_source_load_time_hash("compile", (content, str(fake_path)))
    assert mismatches == []


def test_audit_scorer_source_load_time_hash_hashes_bytes_source_directly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`source` が `bytes`（実測での実際の型）でもそのまま hash し、記録に反映する。"""
    mismatches: List[str] = []
    monkeypatch.setattr(harness, "_SCORER_LOAD_TIME_HASH_MISMATCHES", mismatches)
    fake_path = tmp_path / "fake_scorer_bytes.py"
    content_bytes = b"x = 1\n"
    fake_path.write_bytes(content_bytes)
    expected = hashlib.sha256(content_bytes).hexdigest()
    monkeypatch.setitem(harness._SCORER_LOAD_TIME_EXPECTED_HASHES, str(fake_path), expected)
    harness._audit_scorer_source_load_time_hash("compile", (content_bytes, str(fake_path)))
    assert mismatches == []

    malicious_bytes = b"x = 2  # malicious\n"
    harness._audit_scorer_source_load_time_hash("compile", (malicious_bytes, str(fake_path)))
    assert len(mismatches) == 1
    assert str(fake_path) in mismatches[0]


def test_audit_scorer_source_load_time_hash_detects_swap_and_restore(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """compile された source bytes が束縛時点の期待と食い違えば記録する（Codex 10 巡目 P1-B）。

    直接の再現: `_require_scorer_modules_match_pinned_origin`（H8）の docstring が
    指摘する「差し替え → import（compile） → 元へ復元」は origin パス比較を
    素通りするが、`args[0]`（compile された実際の source）を直接 hash する本機構は
    素通りしない——ここでは audit hook のコールバックを直接呼んで機構そのものを
    固定する（実際の `sys.addaudithook` 経由の統合動作は round 10 の実測スモーク
    テストで既に確認済み）。
    """
    mismatches: List[str] = []
    monkeypatch.setattr(harness, "_SCORER_LOAD_TIME_HASH_MISMATCHES", mismatches)
    fake_path = tmp_path / "fake_scorer.py"
    original_content = "x = 1\n"
    fake_path.write_text(original_content, encoding="utf-8")
    expected = hashlib.sha256(original_content.encode("utf-8")).hexdigest()
    monkeypatch.setitem(harness._SCORER_LOAD_TIME_EXPECTED_HASHES, str(fake_path), expected)

    # 攻撃者が compile 直前に別 bytes を書き込んだ状態を模す。
    malicious_content = "x = 2  # malicious\n"
    fake_path.write_text(malicious_content, encoding="utf-8")
    harness._audit_scorer_source_load_time_hash("compile", (malicious_content, str(fake_path)))
    assert len(mismatches) == 1
    assert str(fake_path) in mismatches[0]

    # 攻撃者が元へ復元しても、記録は既に済んでいるため消えない（2 段構え）。
    fake_path.write_text(original_content, encoding="utf-8")
    assert len(mismatches) == 1


def test_audit_scorer_source_load_time_hash_detects_restore_before_hook_fires(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """hook 到達**前**に復元済みでも `args[0]` 方式なら検出する（Codex 11 巡目 P1-A）。

    10 巡目時点の実装（audit イベント発火時にディスクを**読み直して** hash する）は
    「差し替え → ローダが読取（compile へ渡す bytes 確定）→ ***攻撃者が本 hook の
    実行前にディスクを元へ復元*** → 本 hook がディスクを再読」という順序では、
    hook の再読が復元**後**に走るため常に無害な原本を見てしまい、swap-and-restore
    を**検出できなかった**（この順序を再現すれば、旧実装は必ず見逃す）。

    ここでは正にその順序を作る: `_audit_scorer_source_load_time_hash` を呼ぶ
    **時点で、ディスク上は既に original_content へ復元済み**にしておく——それでも
    `args[0]`（`source`。ローダが実際に compile へ渡した malicious bytes）を
    直接 hash する現行実装は、ディスクの状態に一切依存せず不一致を検出できる
    ことを固定する。
    """
    mismatches: List[str] = []
    monkeypatch.setattr(harness, "_SCORER_LOAD_TIME_HASH_MISMATCHES", mismatches)
    fake_path = tmp_path / "fake_scorer.py"
    original_content = "x = 1\n"
    fake_path.write_text(original_content, encoding="utf-8")
    expected = hashlib.sha256(original_content.encode("utf-8")).hexdigest()
    monkeypatch.setitem(harness._SCORER_LOAD_TIME_EXPECTED_HASHES, str(fake_path), expected)

    # ディスクは既に（攻撃者によって）original へ復元済み——旧実装（ディスク再読）
    # ならここで hook を呼んでも「一致」を見て何も記録できないはずの状態。
    assert fake_path.read_text(encoding="utf-8") == original_content

    # それでも、ローダが実際に compile へ渡した malicious な source（args[0]）を
    # 直接渡して hook を呼ぶ——これが「hook 到達前に復元済み」の完全な再現。
    malicious_content = "x = 2  # malicious, but disk is already restored\n"
    harness._audit_scorer_source_load_time_hash("compile", (malicious_content, str(fake_path)))

    # ディスクの状態（既に無害な原本）に関わらず、args[0] 方式は検出する。
    assert len(mismatches) == 1
    assert str(fake_path) in mismatches[0]
    # ディスクは無害な原本のままであることの再確認（旧実装ならここで「一致」と
    # 誤判定していたはずの状態）。
    assert fake_path.read_text(encoding="utf-8") == original_content


def test_audit_scorer_source_load_time_hash_ignores_unknown_source_type(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """未知の source 型（bytes/str 以外）は静かに諦める（audit hook 内で raise しない）。"""
    mismatches: List[str] = []
    monkeypatch.setattr(harness, "_SCORER_LOAD_TIME_HASH_MISMATCHES", mismatches)
    missing_path = tmp_path / "gone.py"
    monkeypatch.setitem(
        harness._SCORER_LOAD_TIME_EXPECTED_HASHES, str(missing_path), "0" * 64
    )
    # AST 等、bytes/str のどちらでもない source（実測では未観測の防御的分岐）。
    harness._audit_scorer_source_load_time_hash("compile", (object(), str(missing_path)))
    assert mismatches == []  # 例外も出ない・記録も増えない


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
    # 引用されている節が実在することまで確認
    # （§2 指標 / §4 バー / §6 scorer pin 脅威モデル / §8 PR 分割 / §9 禁止事項）。
    for marker in (
        "## 2. 指標",
        "## 4. 事前登録バー",
        "## 6. Scorer pin の脅威モデルと境界",
        "## 8. PR 分割",
        "## 9. やってはいけないこと",
    ):
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
        # ハーネスは decode 前に WAV を 0o400（read-only）へ落とす（第 39 巡）。root
        # ではパーミッションビットが素通りして書けてしまうが、非 root（CI runner）
        # では素直に PermissionError になる。ここで検証したいのは read-only ビット
        # そのものではなく差し替えを **hash 照合で検出できるか** なので、差し替え役
        # （ファイル所有者）として明示 chmod してから書く——所有者による chmod は
        # 想定する脅威モデルの範囲内（同権限者はプロセスメモリも書ける = 既定の境界外）。
        os.chmod(audio_path, 0o644)
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
    with pytest.raises(ValueError, match="スコアラー閉包 pin"):
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


def test_git_invoked_from_trusted_absolute_path_ignores_path_hijack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """PATH 上の偽 git ではなく、信頼できる絶対パスの本物が実行される（Codex 13 巡目 H19）。

    ldconfig（P1-A）と同じ PATH 注入クラス。`_bars_registration_attestation` の内部
    `_git()` ヘルパーは非修飾コマンドを使わないため、PATH に偽 `git` を先頭に置いても
    無視されることを固定する。
    """
    fake_bin_dir = tmp_path / "evil_bin"
    fake_bin_dir.mkdir()
    fake_git = fake_bin_dir / "git"
    fake_git.write_text("#!/bin/sh\necho 'FAKE_HIJACKED_GIT_OUTPUT'\nexit 1\n")
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    invoked_commands: "list[list[str]]" = []
    real_subprocess_run = subprocess.run

    def _spy_run(command: Any, *args: Any, **kwargs: Any) -> Any:
        invoked_commands.append(list(command))
        return real_subprocess_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _spy_run)

    # 本物の git（絶対パス）が使われ、実リポジトリの立証が成功することを固定する
    # （偽 git は `exit 1` するので、フェイクが使われていれば必ず RuntimeError になる）。
    attestation, _committed = harness._bars_registration_attestation(
        BARS_PATH, BARS_PATH.read_bytes()
    )
    assert re.fullmatch(r"[0-9a-f]{40}", attestation["first_commit"])
    assert invoked_commands, "git の subprocess 呼び出しが発生していない"
    for command in invoked_commands:
        executed = command[0]
        assert Path(executed).is_absolute(), f"非絶対パスで git を実行した: {executed}"
        assert executed != str(fake_git)


def test_hardened_subprocess_env_strips_git_repository_overrides_via_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`GIT_DIR`/`GIT_WORK_TREE` 等の git repository override が硬化 env から消える（Codex 14 巡目 P1-B）。

    13 巡目 H20 までの blocklist 方式は `LD_*`/`GCONV_PATH` 等を個別列挙して除去
    するだけで、`GIT_DIR`/`GIT_WORK_TREE`/`GIT_OBJECT_DIRECTORY`/`GIT_INDEX_FILE`/
    `GIT_ALTERNATE_OBJECT_DIRECTORIES`/`GIT_CONFIG` 等の git repository override は
    素通りしていた——trusted `git -C ROOT` を呼んでも、これらが立っていれば git は
    **foreign リポジトリ**を見る。allowlist 反転（既定ですべて落として明示的に信頼
    する最小集合だけ通す）により、列挙していない任意の env（ここでは
    `SOME_UNRELATED_VAR` も）が一律で漏れないことを固定する。
    """
    import svp_rpe.melody.provenance as provenance

    git_override_vars = (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_INDEX_FILE",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CONFIG",
    )
    for var in git_override_vars:
        monkeypatch.setenv(var, "/tmp/evil_repo")
    monkeypatch.setenv("SOME_UNRELATED_VAR", "should-not-leak")

    env = provenance._hardened_subprocess_env()

    assert env == {"PATH": provenance._TRUSTED_SUBPROCESS_PATH}
    for var in git_override_vars:
        assert var not in env, f"{var} が硬化 env に漏れている"
    assert "SOME_UNRELATED_VAR" not in env


def test_bars_registration_attestation_ignores_git_dir_override_and_sees_root_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`GIT_DIR`/`GIT_WORK_TREE` を export しても trusted git は ROOT リポジトリを見る（Codex 14 巡目 P1-B）。

    allowlist 反転前の blocklist は `GIT_DIR` 等を除去しなかったため、これらを export
    した状態で trusted `git -C ROOT` を呼ぶと **foreign リポジトリ**（ここでは bars の
    blob を含まない無関係な空リポジトリ）を見てしまい得た——事前登録の立証が ROOT の
    履歴ではなく foreign リポジトリに対して行われる false attestation の入口になる。
    ここでは GIT_DIR/GIT_WORK_TREE を無関係な空リポジトリへ向けても attestation が
    ROOT の実履歴に対して成功することを固定する——foreign リポジトリを見ていたら、
    そこに bars の blob が存在しないため必ず RuntimeError になる。
    """
    foreign_repo = tmp_path / "foreign_repo"
    foreign_repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=foreign_repo, check=True)
    subprocess.run(
        ["git", "-C", str(foreign_repo), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(["git", "-C", str(foreign_repo), "config", "user.name", "test"], check=True)
    (foreign_repo / "unrelated.txt").write_text("nothing to do with bars\n")
    subprocess.run(["git", "-C", str(foreign_repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(foreign_repo), "commit", "-q", "-m", "unrelated"], check=True)

    monkeypatch.setenv("GIT_DIR", str(foreign_repo / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(foreign_repo))

    attestation, _committed = harness._bars_registration_attestation(
        BARS_PATH, BARS_PATH.read_bytes()
    )
    assert re.fullmatch(r"[0-9a-f]{40}", attestation["first_commit"])


def test_bars_registration_attestation_fails_closed_when_no_trusted_git(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """絶対パス候補が 1 つも実在しない環境なら PATH フォールバックせず fail-closed（H19）。"""
    import svp_rpe.melody.provenance as provenance

    monkeypatch.setattr(
        provenance, "_TRUSTED_GIT_CANDIDATES", ("/nonexistent/usr/bin/git",)
    )
    with pytest.raises(RuntimeError, match="trusted git binary not found"):
        harness._bars_registration_attestation(BARS_PATH, BARS_PATH.read_bytes())


def test_require_out_outside_git_metadata_degrades_gracefully_without_trusted_git(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`_require_out_outside_git_metadata` は信頼できる git が無くても既定保護を維持する。

    本関数は attestation（fail-closed 対象）と違い防御的な追加保護であり、`ROOT/.git`
    の既定保護はこの git 解決に依存しない——信頼できる git が無い環境でも黙って
    degrade し、少なくとも `ROOT/.git` 配下は拒否し続けることを固定する（H19）。
    """
    import svp_rpe.melody.provenance as provenance

    monkeypatch.setattr(
        provenance, "_TRUSTED_GIT_CANDIDATES", ("/nonexistent/usr/bin/git",)
    )
    with pytest.raises(SystemExit, match="git メタデータ"):
        harness._require_out_outside_git_metadata(harness.ROOT / ".git" / "HEAD")
    # git メタデータ外の通常パスは従来どおり許可される（誤って全拒否に倒れない）。
    harness._require_out_outside_git_metadata(tmp_path / "ordinary_report.json")


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
        **scorer,
        "harness_loaded_as_main": True,
        "pre_bound_scorer_native_mappings": [],
        "non_standard_import_hooks": [],
        "scorer_load_time_hash_mismatches": [],
        "scorer_compile_expected_paths": [],
        "scorer_compile_observed_paths": [],
        "sys_flags_optimize": 0,
        "specs_sha256": frozen_specs,
    }
    harness._require_fresh_process_report_provenance(
        dict(base), "S_direct", expected_specs_sha256=frozen_specs
    )  # 健全なら通る
    for mutation, pattern in [
        ({"route_runner_injected": True}, "注入ランナー"),
        ({"preloaded_seed_modules": ["svp_rpe"]}, "事前ロード"),
        ({"generator_code_sha256": "0" * 64}, "generator_code_sha256"),
        ({"mir_eval_code_sha256": "1" * 64}, "スコアラー閉包 pin"),
        # (d) numpy pin の差でも fresh-process 照合が fail-closed すること（Codex P1）:
        # mir_eval 単体の pin を旧実装のまま揃えても numpy が別 stack なら拒否する。
        ({"numpy_code_sha256": "3" * 64}, "スコアラー閉包 pin"),
        ({"scipy_version": "0.0-stale"}, "スコアラー閉包 pin"),
        ({"schema_version": "m2-accuracy-report/9.9"}, "schema_version"),
        ({"harness_loaded_as_main": False}, "直接パス"),
        # (e) scorer ネイティブが束縛前に既にロード済みだった子プロセスも拒否する
        # （Codex P1 7 巡目）: mmap 済み実体は disk hash で検出できない。
        ({"pre_bound_scorer_native_mappings": ["/fake/libopenblas-shadow.so.0"]}, "束縛前"),
        # (f) 非標準 import hook が束縛前に存在した子プロセスも拒否する（セルフレビュー H3）。
        ({"non_standard_import_hooks": ["meta_path:evil.EvilFinder"]}, "非標準の import hook"),
        # (h) scorer .py の swap-and-restore 痕跡を記録した子プロセスも拒否する
        # （Codex 10 巡目 P1-B）。
        (
            {"scorer_load_time_hash_mismatches": ["/fake/numpy/core.py: mismatch"]},
            "swap-and-restore",
        ),
        # (i) compile 観測が期待集合を覆っていない子プロセスも拒否する
        # （セルフレビュー第二弾 H16）。
        (
            {"scorer_compile_expected_paths": ["/definitely/not/observed.py"]},
            "compile を観測しなかった",
        ),
        # (g) -O/-OO 実行の子プロセスも拒否する（セルフレビュー H9）。
        ({"sys_flags_optimize": 1}, r"-O/-OO"),
        ({"specs_sha256": hashlib.sha256(b"other").hexdigest()}, "凍結 specs"),
    ]:
        with pytest.raises(RuntimeError, match=pattern):
            harness._require_fresh_process_report_provenance(
                {**base, **mutation}, "S_direct", expected_specs_sha256=frozen_specs
            )


def test_evaluate_refuses_import_style_evaluator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """評価器自身が直接ソース実行でなければ publish を拒否する（Codex P2 第 37 巡）。

    preload ゲートは依存モジュールしか覆わず、`python -m ... --evaluate` は評価器
    モジュール自身を stale .pyc から実行しうる。
    """
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    monkeypatch.setattr(harness, "_HARNESS_LOADED_AS_MAIN", False)
    with pytest.raises(RuntimeError, match="評価器が直接パスの script 実行でない"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


def test_loaded_as_main_detection_is_structural(tmp_path: Path) -> None:
    """`__name__` だけでなく `__spec__ is None` を要求する（Codex P2 第 35 巡）。

    `python -m` も `__name__ == "__main__"` になるが import 機構（= .pyc キャッシュ）
    を通る。直接ファイル実行では `__main__.__spec__` が None、`-m` では ModuleSpec が
    設定される、という CPython の区別を実サブプロセスで確認する。
    """
    assert _ORIG_LOADED_AS_MAIN is False  # 本テストファイルは import 形なので False
    probe = tmp_path / "m2probe.py"
    probe.write_text(
        'print(__name__ == "__main__" and globals().get("__spec__") is None)\n',
        encoding="utf-8",
    )
    direct = subprocess.run(
        [sys.executable, str(probe)], capture_output=True, text=True
    )
    assert direct.stdout.strip() == "True"
    via_module = subprocess.run(
        [sys.executable, "-m", "m2probe"],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )
    assert via_module.stdout.strip() == "False"


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


def test_input_wav_is_read_only_during_extraction() -> None:
    """抽出器がデコードする時点で WAV は 0400（in-place 上書きの遮断・Codex P2 第 39 巡）。"""
    modes: List[int] = []
    inner = _make_fake_runner(shift_cents=10.0)

    def _capture_mode(audio_path: str, route: Any) -> Any:
        modes.append(os.stat(audio_path).st_mode & 0o777)
        return inner(audio_path, route)

    harness.run_accuracy(categories=("S_direct",), route_runner=_capture_mode)
    assert modes == [0o400]


def test_wav_serialization_is_deterministic() -> None:
    """直列化 WAV は同一 (y, sr) → 同一 bytes（libsndfile の PEAK timestamp を排除）。

    `sf.write` は float WAV の PEAK チャンクに壁時計を書くため bytes が秒単位で
    揺れ、`input_wav_sha256` を測り直しへ束縛できなかった（Codex P2 第 38 巡）。
    自前の最小 RIFF 直列化が決定論で、libsndfile で bit 一致に読み戻せることを固定。
    """
    import numpy as np
    import soundfile as sf

    y = (np.sin(np.linspace(0.0, 20.0, 2000)) * 0.3).astype(np.float32)
    first = harness._serialize_wav_float32(y, 22050)
    second = harness._serialize_wav_float32(y, 22050)
    assert first == second
    wav_path = Path(tempfile.mkdtemp(prefix="m2-wavdet-")) / "probe.wav"
    wav_path.write_bytes(first)
    readback, sr = sf.read(str(wav_path), dtype="float32")
    assert sr == 22050
    assert np.asarray(readback, dtype=np.float32).tobytes() == y.tobytes()


def test_reverification_rejects_forged_input_wav_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """metrics が再現しても、直列化 WAV pin を偽った row は publish しない。

    waveform_sha256 だけの照合では、編集・stale な input_wav_sha256 を持つ report が
    「抽出器が消費した bytes」を偽って名乗るまま通った（Codex P2 第 38 巡）。
    """
    inner = _make_fake_runner(shift_cents=10.0)
    reports = [_fake_run(categories=("S_direct",), route_runner=inner) for _ in range(2)]
    reports[0]["categories"]["S_direct"]["input_wav_sha256"] = "a" * 64
    monkeypatch.setattr(
        harness, "_reverify_category_measurement", _reverify_via(inner)
    )
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    with pytest.raises(ValueError, match="input_wav_sha256"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports], bars, bars_sha256=bars_sha256
        )


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
    """実抽出器で測り直せない環境（素の CI）からは publish しない。

    `test_run_accuracy_real_extractor_falls_back_to_unavailable_when_uninstalled`
    と同じ理由で skip する: crepe が（手動導入や slow-lane 作業の副産物として）
    実際にこの環境へ入っていると、`_ORIG_REVERIFY` は実抽出器で測り直しに成功して
    しまい、この unavailable-path smoke test の前提が成立しない。
    """
    try:
        import crepe  # noqa: F401

        pytest.skip("crepe is installed in this environment; unavailable-path smoke test N/A")
    except ImportError:
        pass
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


def test_reverification_surfaces_cannot_rerun_when_prebound_mappings_are_clean(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CI 相当条件の再現: pre-bound 記録が正しく空でも、実抽出器が無い環境では
    「再実行できない」regex で fail-closed すること（skip ガードに隠れない形）。

    `test_reverification_refuses_when_stack_cannot_rerun` はローカルに crepe が
    導入済みだと skip され、この経路の回帰を検出できない——実際に CI
    （Python 3.12、PR #225 9 巡目 commit 2ff56cf）はこの skip 無しの環境で走り、
    `libpython3.12.so.1.0`（`--enable-shared` ビルドの CPython 自身の共有ライブラリ）が
    `pre_bound_scorer_native_mappings` の default-deny 記録に混入し、本来期待される
    「再実行できない」エラーより**先に**そのゲートで落ちた（
    `_interpreter_shared_library_paths()` 追加で是正済み）。ここでは実際の子プロセス
    起動を待たず、`subprocess.run` を差し替えて「pre-bound 記録が空の、しかし
    outcome=unavailable の測り直し report」を直接返すことで、環境の実際のビルド形態
    （`--enable-shared` か否か・crepe 有無）に関わらず、常にこの経路の regex を
    固定する。
    """
    reports = [
        _fake_run(categories=("S_direct",), route_runner=_make_fake_runner(shift_cents=10.0))
        for _ in range(2)
    ]
    monkeypatch.setattr(harness, "_reverify_category_measurement", _ORIG_REVERIFY)
    bars, bars_sha256 = harness.load_bars(BARS_PATH)
    scorer = harness._scorer_pins(use_cache=False)

    real_subprocess_run = subprocess.run

    def _fake_subprocess_run(command: Any, *args: Any, **kwargs: Any) -> Any:
        # `harness.subprocess.run` はこの子プロセス spawn 呼び出し**だけ**を偽装する
        # 対象——`_is_ldconfig_registered_path()`/`_ldconfig_cache_paths_by_soname()`
        # （Codex 14 巡目 P1-A、10 巡目 P1-A の後継）が同じ `subprocess.run` 参照経由で
        # `ldconfig -p` も呼ぶため、harness 自身の子プロセス spawn（`sys.executable`
        # 起動）以外は実関数へ委譲する。
        if not (isinstance(command, list) and command and command[0] == sys.executable):
            return real_subprocess_run(command, *args, **kwargs)
        out_index = command.index("--out")
        report_path = Path(command[out_index + 1])
        specs_index = command.index("--specs")
        specs_sha256 = hashlib.sha256(Path(command[specs_index + 1]).read_bytes()).hexdigest()
        category_index = command.index("--categories")
        category = command[category_index + 1]
        canned = {
            "schema_version": harness._EXPECTED_REPORT_SCHEMA,
            "route_runner_injected": False,
            "preloaded_seed_modules": [],
            "generator_code_sha256": harness._LOADED_GENERATOR_CODE_SHA256,
            **scorer,
            "harness_loaded_as_main": True,
            "pre_bound_scorer_native_mappings": [],
            "non_standard_import_hooks": [],
            "scorer_load_time_hash_mismatches": [],
            "scorer_compile_expected_paths": [],
            "scorer_compile_observed_paths": [],
            "sys_flags_optimize": 0,
            "specs_sha256": specs_sha256,
            "categories": {
                category: {"outcome": "unavailable", "detail": "crepe not installed (fake CI)"}
            },
        }
        report_path.write_text(json.dumps(canned), encoding="utf-8")

        class _Result:
            returncode = 0
            stderr = ""
            stdout = ""

        return _Result()

    monkeypatch.setattr(harness.subprocess, "run", _fake_subprocess_run)
    with pytest.raises(RuntimeError, match="再実行できない"):
        harness.evaluate_m2_bars(
            [_as_report_artifact(r) for r in reports],
            bars,
            bars_sha256=bars_sha256,
            # verification_runner を渡さない = 実抽出器の測り直し経路（fake subprocess 経由）。
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


def test_runtime_input_paths_cover_runtime_distribution_metadata() -> None:
    """配布メタデータの保護は mir_eval 特例でなく全 runtime パッケージ分（Codex P2 第 36 巡）。

    run は separation_version（demucs）等のために importlib.metadata を読む。
    導入済みの runtime パッケージ（例: numpy）の dist-info が保護集合に入ることを固定。
    """
    import importlib.metadata

    assert "numpy" in set(harness._runtime_package_names())
    dist = importlib.metadata.distribution("numpy")
    metadata_files = [
        Path(str(dist.locate_file(record))).resolve()
        for record in (dist.files or ())
        if str(record).endswith("METADATA")
    ]
    assert metadata_files, "numpy の dist-info METADATA が見つからない（前提の drift）"
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
