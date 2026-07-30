"""tests/test_m3_comparison_harness.py — `scripts/run_melody_comparison.py`（M3d）のテスト。

CI 安全（fake route_runner のみ・実音声/実 crepe 不要）: run→evaluate の二相
メカニズム、sequence hash pin（repeats 決定論）、tuning-only マージン計算、
holdout ロック、route_runner_injected による calibration verdict 発行拒否、
`--out` の protected-path を検証する。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_melody_comparison as harness  # noqa: E402
from svp_rpe.melody.observability import MelodyNote, MelodyObservation  # noqa: E402

M3_REGISTRY_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "m3_comparison_registry.yaml"
M1_REGISTRY_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "registry.yaml"


def _note(pitch_midi: float, start_sec: float, end_sec: float, confidence: float = 0.9) -> MelodyNote:
    return MelodyNote(
        start_sec=start_sec, end_sec=end_sec, pitch_midi=pitch_midi, confidence=confidence
    )


def _good_notes(shift: int = 0) -> List[MelodyNote]:
    """観測ゲートを通す 2 フレーズ・10 ノートの旋律（`test_melody_comparison.py` と同型）。"""
    phrase1 = [60, 62, 64, 65, 67]
    phrase2 = [69, 67, 65, 64, 62]
    notes: List[MelodyNote] = []
    t = 0.0
    for p in phrase1 + [None] + phrase2:  # type: ignore[list-item]
        if p is None:
            t += 1.0
            continue
        notes.append(_note(p + shift, t, t + 0.25))
        t += 0.3
    return notes


def _different_notes() -> List[MelodyNote]:
    """`_good_notes()` と折返し音程が一切重ならない旋律（negative 用）。"""
    phrase1 = [60, 65, 62, 56, 52]  # intervals +5,-3,-6,-4
    phrase2 = [57, 65, 60, 68, 62]  # intervals +8,-5,+8,-6 (boundary 57-52=5)
    notes: List[MelodyNote] = []
    t = 0.0
    for p in phrase1 + [None] + phrase2:  # type: ignore[list-item]
        if p is None:
            t += 1.0
            continue
        notes.append(_note(p, t, t + 0.25))
        t += 0.3
    return notes


def _notes_by_path(audio_paths: Dict[str, str]) -> Dict[str, List[MelodyNote]]:
    return {
        audio_paths["song_a"]: _good_notes(),
        audio_paths["song_a_transposed"]: _good_notes(shift=3),
        audio_paths["song_b"]: _different_notes(),
    }


def _write_audio_fixture_files(tmp_path: Path) -> Dict[str, str]:
    """`song_a`/`song_a_transposed`/`song_b` に対応する実ファイルを書き出す。

    レビュー対応 2026-07-30（第 4 ラウンド）: `file_sha256`（音声バイトの content
    pin）は実在ファイルの bytes を読むため、manifest の `audio_a`/`audio_b` は
    もう「fake route_runner に渡す識別子文字列」だけでは済まない——実パスで
    なければならない。3 素材それぞれ別内容の bytes を書き、str パスの辞書を返す。
    """
    paths = {
        "song_a": tmp_path / "song_a.audio",
        "song_a_transposed": tmp_path / "song_a_transposed.audio",
        "song_b": tmp_path / "song_b.audio",
    }
    for label, path in paths.items():
        path.write_bytes(f"fake-audio-bytes:{label}".encode("utf-8"))
    return {label: str(path) for label, path in paths.items()}


@pytest.fixture()
def audio_paths(tmp_path: Path) -> Dict[str, str]:
    """`_write_audio_fixture_files` を pytest fixture 化したもの（テスト間で使い回す）。"""
    return _write_audio_fixture_files(tmp_path)


# レビュー対応 2026-07-30（第 8 ラウンド・pin 値の整形検証）: `_validate_pin_formats`
# は `route_provenance_a/b` が mapping であることに加え、crepe 系経路
# （既定 route の crepe_direct を含む）では `extractor_weights_sha256` /
# `extractor_code_sha256` が非空文字列であることを要求する。fake route_runner の
# provenance も実測 crepe 観測（`extractors.observe_via_route_with_provenance`）
# と同型の 64 桁 hex 文字列を刻んでおく——fake であっても整形は本物と揃える。
_FAKE_EXTRACTOR_WEIGHTS_SHA256 = "a" * 64
_FAKE_EXTRACTOR_CODE_SHA256 = "b" * 64


def _fake_route_runner(notes_by_path: Dict[str, List[MelodyNote]]) -> "harness.RouteRunner":
    """`notes_by_path` を **bytes 内容** で引く runner を返す。

    レビュー対応 2026-07-30（第 5 ラウンド）: `run_comparison` は音声 bytes を
    一度だけ読み、その bytes を凍結コピー（一時パス）へ書いてから runner へ渡す
    ため、runner が受け取る `audio_path` はもう manifest の元パスと一致しない。
    元パスの bytes を起動時に一度だけ読んで内容→notes の対応表を作り、以後は
    `audio_path` の中身（凍結コピーでも元ファイルでも同じ bytes）で引く——TOCTOU
    解消後もテスト側の対応関係を保つ。未知の内容（意図的な改ざんテスト）は
    `_different_notes()` へフォールバックする（そのテストは notes の中身でなく
    `audio_sha256_*` の食い違いだけを見るため）。
    """
    notes_by_content = {
        Path(path).read_bytes(): notes for path, notes in notes_by_path.items()
    }

    def _runner(audio_path: str) -> "Tuple[MelodyObservation, Dict[str, Any]]":
        content = Path(audio_path).read_bytes()
        notes = notes_by_content.get(content, _different_notes())
        return (
            MelodyObservation(route="fake", source_model="test:fake", notes=tuple(notes)),
            {
                "fake_provenance": True,
                "extractor_weights_sha256": _FAKE_EXTRACTOR_WEIGHTS_SHA256,
                "extractor_code_sha256": _FAKE_EXTRACTOR_CODE_SHA256,
            },
        )

    return _runner


def _write_manifest(path: Path, pairs: List[Dict[str, Any]]) -> None:
    manifest = {"schema": "m3-comparison-pairs/0.1", "pairs": pairs}
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")


def _registry_mapping() -> Dict[str, Any]:
    return yaml.safe_load(M3_REGISTRY_PATH.read_text(encoding="utf-8"))


def _m1_registry_mapping() -> Dict[str, Any]:
    return yaml.safe_load(M1_REGISTRY_PATH.read_text(encoding="utf-8"))


def _write_m3_registry_variant(tmp_path: Path, *, margin_value: Any) -> Path:
    """`separation_margin.min_same_minus_cross_margin` を `margin_value` に差し替えた
    一時 M3 registry ファイルを書き出す（レビュー対応 2026-07-30 第 15 ラウンド）。
    """
    mapping = _registry_mapping()
    mapping["separation_margin"] = {"min_same_minus_cross_margin": margin_value}
    path = tmp_path / "m3_registry_variant.yaml"
    path.write_text(yaml.safe_dump(mapping, sort_keys=False), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# separation_margin の M0/M1 継承検証（レビュー対応 2026-07-30 第 15 ラウンド）
# --------------------------------------------------------------------------- #
def test_validate_separation_margin_accepts_value_synced_with_m1_registry():
    """既定の 2 fixture（同期済み: いずれも 0.15）は例外を投げない。"""
    config, _ = harness.load_m3_registry(M3_REGISTRY_PATH)
    m1_mapping = _m1_registry_mapping()
    harness._validate_separation_margin_inherited_from_m1(config, m1_mapping)  # 例外なし


@pytest.mark.parametrize("bad_margin", [-1, 2.0, True], ids=["negative", "above_one", "bool"])
def test_validate_separation_margin_rejects_invalid_value(tmp_path: Path, bad_margin: Any):
    """一時 M3 registry の separation_margin を -1/2.0/True に改変すると fail-closed
    （非 bool の有限数値かつ 0 < margin <= 1 でなければならない）。

    レビュー対応 2026-07-30（第 17 ラウンド）: この値不変条件は
    `M3ComparisonConfig.from_registry`（`representation.py`）がロード時に enforce
    するようになったため、`harness.load_m3_registry` の呼び出し自体がここで
    fail-closed する——`_validate_separation_margin_inherited_from_m1`（M0/M1
    継承の一致検証、別の独立した検証）まで到達する前に拒否される。
    """
    registry_path = _write_m3_registry_variant(tmp_path, margin_value=bad_margin)

    with pytest.raises(ValueError, match="separation_margin"):
        harness.load_m3_registry(registry_path)


def test_validate_separation_margin_rejects_mismatch_with_m1_registry(tmp_path: Path):
    """M3 registry の separation_margin(0.2) が M1 registry の値(0.15)と不一致なら
    fail-closed（M3 は M0/M1 の値を継承しなければならず新値を発明してはならない）。
    """
    registry_path = _write_m3_registry_variant(tmp_path, margin_value=0.2)
    config, _ = harness.load_m3_registry(registry_path)
    m1_mapping = _m1_registry_mapping()
    assert m1_mapping["separation_gate"]["min_same_minus_cross_margin"] != 0.2

    with pytest.raises(ValueError, match="不一致"):
        harness._validate_separation_margin_inherited_from_m1(config, m1_mapping)


def test_run_comparison_preflight_rejects_invalid_separation_margin(
    tmp_path: Path, audio_paths: Dict[str, str]
):
    """run phase の preflight でも同じ検証を独立に enforce する（設計の「run 側でも
    可能なら」を満たす配線確認）。"""
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    registry_path = _write_m3_registry_variant(tmp_path, margin_value=2.0)

    with pytest.raises(ValueError, match="separation_margin"):
        harness.run_comparison(
            manifest_path=manifest_path, route_runner=runner, registry_path=registry_path
        )


def test_run_comparison_preflight_rejects_separation_margin_mismatch_with_m1(
    tmp_path: Path, audio_paths: Dict[str, str]
):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    registry_path = _write_m3_registry_variant(tmp_path, margin_value=0.2)

    with pytest.raises(ValueError, match="不一致"):
        harness.run_comparison(
            manifest_path=manifest_path, route_runner=runner, registry_path=registry_path
        )


def _partially_frozen_registry_text() -> str:
    """`evidence_thresholds.status` を frozen にし contour 軸のみ整形済みで凍結する
    一方、`coverage.floor_status` は provisional のまま残す registry(holdout が
    開いてはならない「部分凍結」ケース: 条件 (b) は満たすが条件 (c) が満たされない)。

    レビュー対応 2026-07-30(第 18 ラウンド): axes 境界の値不変条件検証が
    `M3ComparisonConfig.from_registry`(`representation.py`)へ追加され、
    `status == "frozen"` の registry は axes が非空 mapping でなければロード
    自体が拒否されるようになった——旧来のように `status: frozen` のみを文字列
    置換で挿入し axes を一切持たない registry は、もはやロード可能な状態を
    表現できない(loader-level 不変条件違反)。1 軸(contour)のみ整形済みで
    凍結した最小の部分凍結状態を用いる。
    """
    mapping = _registry_mapping()
    mapping["evidence_thresholds"] = {
        "status": "frozen",
        "axes": {"contour": {"strong_min": 0.8, "none_max": 0.2}},
    }
    return yaml.safe_dump(mapping, sort_keys=False)


def _fully_frozen_registry_text() -> str:
    """holdout が開く条件を全て満たした registry: evidence_thresholds.status=frozen
    かつ axes(contour/interval/rhythm)が揃い、coverage.floor_status も frozen。
    """
    mapping = _registry_mapping()
    mapping["evidence_thresholds"] = {
        "status": "frozen",
        "axes": {
            "contour": {"strong_min": 0.8, "none_max": 0.2},
            "interval": {"strong_min": 0.8, "none_max": 0.2},
            "rhythm": {"strong_min": 0.7, "none_max": 0.3},
        },
    }
    mapping["coverage"] = dict(mapping["coverage"])
    mapping["coverage"]["floor_status"] = "frozen"
    return yaml.safe_dump(mapping, sort_keys=False)


def _default_pairs(audio_paths: Dict[str, str]) -> List[Dict[str, Any]]:
    """校正 manifest の最小構成（レビュー対応 2026-07-30 第 10 ラウンド:
    `_validate_manifest_composition`（設計 §6.1）を満たすよう構成: tuning に
    positive_transform + negative、狙い撃ち negative（negative_rhythm/
    negative_interval、split 不問のためどちらも tuning に置く）、holdout に
    positive + negative を各 1 件以上）。狙い撃ち negative / holdout negative は
    既存の song_a/song_b（`negative_cross` と同じ音声ペア）を再利用する——
    manifest 構成検査は kind/split/expected の組み合わせのみを見るため、
    音声内容の意味的な使い分けはテストの主眼ではない。
    """
    return [
        {
            "pair_id": "p_pos_tuning",
            "kind": "positive_transform",
            "split": "tuning",
            "audio_a": audio_paths["song_a"],
            "audio_b": audio_paths["song_a_transposed"],
            "expected": "same",
        },
        {
            "pair_id": "p_neg_tuning",
            "kind": "negative_cross",
            "split": "tuning",
            "audio_a": audio_paths["song_a"],
            "audio_b": audio_paths["song_b"],
            "expected": "different",
        },
        {
            "pair_id": "p_pos_holdout",
            "kind": "positive_transform",
            "split": "holdout",
            "audio_a": audio_paths["song_a"],
            "audio_b": audio_paths["song_a_transposed"],
            "expected": "same",
        },
        {
            "pair_id": "p_neg_rhythm_tuning",
            "kind": "negative_rhythm",
            "split": "tuning",
            "audio_a": audio_paths["song_a"],
            "audio_b": audio_paths["song_b"],
            "expected": "different",
        },
        {
            "pair_id": "p_neg_interval_tuning",
            "kind": "negative_interval",
            "split": "tuning",
            "audio_a": audio_paths["song_a"],
            "audio_b": audio_paths["song_b"],
            "expected": "different",
        },
        {
            "pair_id": "p_neg_holdout",
            "kind": "negative_cross",
            "split": "holdout",
            "audio_a": audio_paths["song_a"],
            "audio_b": audio_paths["song_b"],
            "expected": "different",
        },
    ]


# --------------------------------------------------------------------------- #
# run → evaluate の機構
# --------------------------------------------------------------------------- #
def test_run_then_evaluate_mechanism(tmp_path: Path, audio_paths: Dict[str, str]):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)

    assert report["schema_version"] == harness._EXPECTED_RUN_SCHEMA
    assert report["route_runner_injected"] is True
    assert set(report["pairs"]) == {
        "p_pos_tuning",
        "p_neg_tuning",
        "p_pos_holdout",
        "p_neg_rhythm_tuning",
        "p_neg_interval_tuning",
        "p_neg_holdout",
    }
    assert report["pairs"]["p_pos_tuning"]["comparison"]["axes"]["interval"] == pytest.approx(1.0)
    assert report["pairs"]["p_neg_tuning"]["comparison"]["evidence"] == "not_comparable"

    verdict = harness.evaluate_comparison([report])
    assert verdict["repeats_count"] == 1
    # n=1 は決定論 repeats 未検証 — true と書かない(正直な表現)。
    assert verdict["repeats_verified"] is False
    assert verdict["repeats_consistent"] is False
    # フェイク runner で作った report は calibration verdict を発行しない
    # (route_runner_injected の拒否が insufficient_repeats より優先される)。
    assert verdict["calibration_verdict_status"] == "rejected_route_runner_injected"


# --------------------------------------------------------------------------- #
# sequence hash pin（軌跡レベル決定論）
# --------------------------------------------------------------------------- #
def test_repeats_hash_pin_bit_identical(tmp_path: Path, audio_paths: Dict[str, str]):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report2 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)

    # 決定論の bit 一致を確認するだけの内部ヘルパー呼び出し(例外が出なければ pass)。
    harness._check_repeats_consistency([report1, report2])

    verdict = harness.evaluate_comparison([report1, report2])
    assert verdict["repeats_count"] == 2
    assert verdict["repeats_consistent"] is True


def test_repeats_hash_pin_rejects_tampered_report(tmp_path: Path, audio_paths: Dict[str, str]):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report2 = copy.deepcopy(report1)
    # 故意に sequence_sha256 を改変する(反復間の軌跡レベル決定論を壊す)。
    report2["pairs"]["p_pos_tuning"]["comparison"]["provenance"]["sequence_sha256_a"] = "tampered"

    with pytest.raises(ValueError, match="sequence_sha256/axes"):
        harness.evaluate_comparison([report1, report2])


def test_repeats_rejects_manifest_sha256_pin_mismatch(tmp_path: Path, audio_paths: Dict[str, str]):
    """repeats 間で `manifest_sha256`（report 全体の repeat 定義的 pin）が食い違えば
    たとえ pair 集合/axes が一致していても fail-closed で拒否する(レビュー対応 2026-07-30)。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report2 = copy.deepcopy(report1)
    report2["manifest_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="manifest_sha256"):
        harness._check_repeats_consistency([report1, report2])


def test_repeats_rejects_route_pin_mismatch(tmp_path: Path, audio_paths: Dict[str, str]):
    """repeats 間で report 全体の `route` pin が食い違えば拒否する。"""
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report2 = copy.deepcopy(report1)
    report2["route"] = "melodia_direct"

    with pytest.raises(ValueError, match="route"):
        harness._check_repeats_consistency([report1, report2])


def test_repeats_rejects_route_provenance_pin_mismatch(tmp_path: Path, audio_paths: Dict[str, str]):
    """pair 単位の `route_provenance_a/b`(route 由来の provenance pin)が repeats 間で
    食い違えば、sequence_sha256/axes が一致していても拒否する。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report2 = copy.deepcopy(report1)
    assert "route_provenance_a" in report2["pairs"]["p_pos_tuning"]
    report2["pairs"]["p_pos_tuning"]["route_provenance_a"] = {"fake_provenance": "tampered"}

    with pytest.raises(ValueError, match="route_provenance_a"):
        harness._check_repeats_consistency([report1, report2])


def test_repeats_rejects_audio_content_pin_mismatch_same_path(
    tmp_path: Path, audio_paths: Dict[str, str]
):
    """`audio_a`/`audio_b` のパス自体は repeats 間で同一でも、そのファイルの中身
    （bytes）が差し替わっていれば `audio_sha256_a`（音声バイトの content pin・
    レビュー対応 2026-07-30 第 4 ラウンド）が食い違い fail-closed で拒否する。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    assert "audio_sha256_a" in report1["pairs"]["p_pos_tuning"]

    # 同じパスのまま bytes だけ差し替える(サイズも変えてキャッシュ key の衝突を回避)。
    song_a_path = Path(audio_paths["song_a"])
    song_a_path.write_bytes(b"tampered-audio-bytes-of-a-very-different-length-than-original")

    report2 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)

    assert (
        report1["pairs"]["p_pos_tuning"]["audio_sha256_a"]
        != report2["pairs"]["p_pos_tuning"]["audio_sha256_a"]
    )
    with pytest.raises(ValueError, match="audio_sha256_a"):
        harness.evaluate_comparison([report1, report2])


# --------------------------------------------------------------------------- #
# comparison 全体の等値比較（レビュー対応 2026-07-30 第 14 ラウンド）
# --------------------------------------------------------------------------- #
def test_repeats_rejects_evidence_only_tamper(tmp_path: Path, audio_paths: Dict[str, str]):
    """`comparison.evidence` だけを改変した repeat は、`sequence_sha256`/`axes` の
    個別フィールド比較（`ref_sig`/`cur_sig`）を素通りしても、`comparison` 全体の
    等値比較（レビュー対応 2026-07-30 第 14 ラウンド）で拒否される。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report2 = copy.deepcopy(report1)
    original_evidence = report2["pairs"]["p_pos_tuning"]["comparison"]["evidence"]
    tampered_evidence = "weak" if original_evidence != "weak" else "strong"
    report2["pairs"]["p_pos_tuning"]["comparison"]["evidence"] = tampered_evidence

    with pytest.raises(ValueError, match="comparison"):
        harness._check_repeats_consistency([report1, report2])


def test_repeats_rejects_coverage_only_tamper(tmp_path: Path, audio_paths: Dict[str, str]):
    """`comparison.coverage` だけを改変した repeat は、`sequence_sha256`/`axes` の
    個別フィールド比較を素通りしても、`comparison` 全体の等値比較で拒否される。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report2 = copy.deepcopy(report1)
    coverage = report2["pairs"]["p_pos_tuning"]["comparison"]["coverage"]
    assert "aligned_note_fraction_a" in coverage
    coverage["aligned_note_fraction_a"] = -1.0

    with pytest.raises(ValueError, match="comparison"):
        harness._check_repeats_consistency([report1, report2])


# --------------------------------------------------------------------------- #
# 生観測軌跡の content hash（レビュー対応 2026-07-30 第 11 ラウンド）
# --------------------------------------------------------------------------- #
def test_run_comparison_records_observation_sha256_for_each_side(
    tmp_path: Path, audio_paths: Dict[str, str]
):
    """`run_comparison` は各 pair の両側について `observation_sha256_a/b`
    （正規化前の生観測軌跡の content hash）を記録し、repeats 間で bit 一致する。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report2 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)

    row1 = report1["pairs"]["p_pos_tuning"]
    row2 = report2["pairs"]["p_pos_tuning"]
    assert harness._is_sha256_hex(row1["observation_sha256_a"])
    assert harness._is_sha256_hex(row1["observation_sha256_b"])
    # song_a と song_a_transposed は notes が異なる(移調)ため両側の hash も異なる。
    assert row1["observation_sha256_a"] != row1["observation_sha256_b"]
    # 同一 manifest × 同一 route_runner の repeats は bit 一致する。
    assert row1["observation_sha256_a"] == row2["observation_sha256_a"]
    assert row1["observation_sha256_b"] == row2["observation_sha256_b"]

    # holdout ロック pair には記録されない(音声未読・比較未実行)。
    holdout_row = report1["pairs"]["p_pos_holdout"]
    assert "observation_sha256_a" not in holdout_row
    assert "observation_sha256_b" not in holdout_row


def test_repeats_rejects_observation_sha256_pin_mismatch(
    tmp_path: Path, audio_paths: Dict[str, str]
):
    """pair 単位の `observation_sha256_a`（生観測軌跡の content hash）が repeats 間
    で食い違えば、sequence_sha256/axes や audio_sha256 が一致していても
    fail-closed で拒否する（レビュー対応 2026-07-30 第 11 ラウンド）。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report2 = copy.deepcopy(report1)
    report2["pairs"]["p_pos_tuning"]["observation_sha256_a"] = "0" * 64

    with pytest.raises(ValueError, match="observation_sha256_a"):
        harness._check_repeats_consistency([report1, report2])

    with pytest.raises(ValueError, match="observation_sha256_a"):
        harness.evaluate_comparison([report1, report2])


def test_repeats_rejects_observation_sha256_missing_in_one_repeat(
    tmp_path: Path, audio_paths: Dict[str, str]
):
    """`observation_sha256_b` が一方の repeat にだけ欠落していれば、値が
    同一かどうかを問わず有無の不一致として fail-closed で拒否する。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report2 = copy.deepcopy(report1)
    del report2["pairs"]["p_pos_tuning"]["observation_sha256_b"]

    with pytest.raises(ValueError, match="observation_sha256_b"):
        harness._check_repeats_consistency([report1, report2])


def test_observation_content_sha256_distinguishes_1e_minus_5_difference():
    """`_observation_content_sha256` は丸めなし（`float(x).hex()`）で直列化する
    ため、1e-5 差の軌跡でも別 hash になる（レビュー対応 2026-07-30 第 12 ラウンド）。

    旧実装（`.4f` への丸め）では `100.00000` と `100.00001` は共に `"100.0000"`
    へ収束し、同一 hash になっていた——4 桁未満の差異が repeats 間でぶれていても
    pin 一致として見逃す穴があった。
    """
    obs_a = MelodyObservation(
        route="fake",
        source_model="test:fake",
        frame_times=(0.0, 0.1),
        frame_hz=(220.0, 100.00000),
        frame_confidence=(0.9, 0.9),
    )
    obs_b = MelodyObservation(
        route="fake",
        source_model="test:fake",
        frame_times=(0.0, 0.1),
        frame_hz=(220.0, 100.00001),
        frame_confidence=(0.9, 0.9),
    )

    # 前提: 差は確かに 1e-5 であり、旧 `.4f` 丸めでは区別できない値であること。
    assert obs_b.frame_hz[1] - obs_a.frame_hz[1] == pytest.approx(1e-5)
    assert format(obs_a.frame_hz[1], ".4f") == format(obs_b.frame_hz[1], ".4f")

    assert harness._observation_content_sha256(obs_a) != harness._observation_content_sha256(obs_b)


# --------------------------------------------------------------------------- #
# マージン計算の手計算一致
# --------------------------------------------------------------------------- #
def _synthetic_pairs_for_margin() -> Dict[str, Dict[str, Any]]:
    def _row(
        evidence: str,
        contour: Any,
        interval: Any,
        rhythm: Any,
        split: str,
        expected: str,
        kind: str = "negative_cross",
    ) -> Dict[str, Any]:
        return {
            "split": split,
            "expected": expected,
            "kind": kind,
            "comparison": {
                "evidence": evidence,
                "axes": {"contour": contour, "interval": interval, "rhythm": rhythm},
                "coverage": {
                    "aligned_note_fraction_a": 0.9,
                    "aligned_note_fraction_b": 0.85,
                    "phrase_coverage_a": 1.0,
                    "phrase_coverage_b": 1.0,
                },
            },
        }

    return {
        "pos1": _row("none", 0.9, 0.8, 0.7, "tuning", "same", kind="positive_transform"),
        "pos2": _row("none", 0.95, 0.85, 0.75, "tuning", "same", kind="positive_transform"),
        # negative_cross（軸を限定しない狙い撃ちでない negative）— 従来どおり全軸に寄与。
        "neg1": _row("none", 0.2, 0.1, 0.3, "tuning", "different", kind="negative_cross"),
        "neg2": _row("none", 0.3, 0.2, 0.25, "tuning", "different", kind="negative_cross"),
        # holdout split は除外されるべき(margin 計算に混ぜない)。
        "pos_holdout": _row(
            "none", 0.99, 0.99, 0.99, "holdout", "same", kind="positive_transform"
        ),
    }


def test_margin_table_hand_calc():
    pairs = _synthetic_pairs_for_margin()
    result = harness._margin_table(pairs, split="tuning", min_margin=0.15)

    # positive_min / negative_max は tuning split の positive/negative のみで手計算。
    assert result["axes"]["contour"]["positive_min"] == pytest.approx(0.9)
    assert result["axes"]["contour"]["negative_max"] == pytest.approx(0.3)
    assert result["axes"]["contour"]["margin"] == pytest.approx(0.6)
    assert result["axes"]["contour"]["calibrated_candidate"] is True

    assert result["axes"]["interval"]["positive_min"] == pytest.approx(0.8)
    assert result["axes"]["interval"]["negative_max"] == pytest.approx(0.2)
    assert result["axes"]["interval"]["margin"] == pytest.approx(0.6)
    assert result["axes"]["interval"]["calibrated_candidate"] is True

    assert result["axes"]["rhythm"]["positive_min"] == pytest.approx(0.7)
    assert result["axes"]["rhythm"]["negative_max"] == pytest.approx(0.3)
    assert result["axes"]["rhythm"]["margin"] == pytest.approx(0.4)
    assert result["axes"]["rhythm"]["calibrated_candidate"] is True

    assert set(result["calibrated_axes"]) == {"contour", "interval", "rhythm"}


def test_margin_table_below_threshold_is_not_calibrated():
    pairs = {
        "pos1": {
            "split": "tuning",
            "expected": "same",
            "kind": "positive_transform",
            "comparison": {"evidence": "none", "axes": {"contour": 0.5, "interval": 0.5, "rhythm": 0.5}},
        },
        "neg1": {
            "split": "tuning",
            "expected": "different",
            "kind": "negative_cross",
            "comparison": {"evidence": "none", "axes": {"contour": 0.45, "interval": 0.2, "rhythm": 0.5}},
        },
    }
    result = harness._margin_table(pairs, split="tuning", min_margin=0.15)
    # contour: margin = 0.05 < 0.15 → not calibrated。interval: margin=0.3 >= 0.15 → calibrated。
    # rhythm: margin = 0.0 < 0.15 → not calibrated。
    assert result["axes"]["contour"]["calibrated_candidate"] is False
    assert result["axes"]["interval"]["calibrated_candidate"] is True
    assert result["axes"]["rhythm"]["calibrated_candidate"] is False
    assert result["calibrated_axes"] == ["interval"]


# --------------------------------------------------------------------------- #
# 狙い撃ち negative の軸スコープ化（レビュー対応 2026-07-30 第 19 ラウンド）
# --------------------------------------------------------------------------- #
def test_margin_table_negative_kind_axis_scope_hand_calc():
    """`negative_rhythm`/`negative_interval` は `_NEGATIVE_KIND_AXIS_SCOPE` の
    スコープ軸のみへ寄与し、保存軸（狙い撃ちで意図的に保存されている軸）の高い
    類似値は当該軸の negative バケットに混入しない。

    - `negative_rhythm`（同音程列・別リズム）: contour/interval は保存 ≈ 1.0 —
      rhythm のみが弁別対象。ここでの `interval=1.0`/`contour=1.0` は interval/
      contour の negative_max に**入ってはならない**（入れば margin が壊れ、
      当該軸の校正が構造的に不可能になる——本テストの受け入れ条件）。
    - `negative_interval`（同リズム・別音程列）: rhythm は保存 ≈ 1.0 — interval/
      contour が弁別対象。`rhythm=1.0` は rhythm の negative_max に入らない。
    """
    pairs: Dict[str, Dict[str, Any]] = {
        "pos1": {
            "split": "tuning",
            "expected": "same",
            "kind": "positive_transform",
            "comparison": {
                "evidence": "none",
                "axes": {"contour": 0.9, "interval": 0.85, "rhythm": 0.8},
            },
        },
        "neg_rhythm": {
            "split": "tuning",
            "expected": "different",
            "kind": "negative_rhythm",
            "comparison": {
                "evidence": "none",
                # interval/contour は保存 ≈ 1.0(=高)・rhythm のみ低い(=弁別対象)。
                "axes": {"contour": 1.0, "interval": 1.0, "rhythm": 0.3},
            },
        },
        "neg_interval": {
            "split": "tuning",
            "expected": "different",
            "kind": "negative_interval",
            "comparison": {
                "evidence": "none",
                # rhythm は保存 ≈ 1.0(=高)・interval/contour のみ低い(=弁別対象)。
                "axes": {"contour": 0.25, "interval": 0.2, "rhythm": 1.0},
            },
        },
    }

    result = harness._margin_table(pairs, split="tuning", min_margin=0.15)

    # contour: negative は neg_interval の 0.25 のみ(neg_rhythm の 1.0 は非混入)。
    assert result["axes"]["contour"]["positive_min"] == pytest.approx(0.9)
    assert result["axes"]["contour"]["negative_max"] == pytest.approx(0.25)
    assert result["axes"]["contour"]["margin"] == pytest.approx(0.65)
    assert result["axes"]["contour"]["calibrated_candidate"] is True

    # interval: negative は neg_interval の 0.2 のみ(neg_rhythm の 1.0 は非混入
    # ——ここが本レビュー対応の核心。旧実装なら negative_max=1.0 になり margin
    # が負値になっていた)。
    assert result["axes"]["interval"]["positive_min"] == pytest.approx(0.85)
    assert result["axes"]["interval"]["negative_max"] == pytest.approx(0.2)
    assert result["axes"]["interval"]["margin"] == pytest.approx(0.65)
    assert result["axes"]["interval"]["calibrated_candidate"] is True

    # rhythm: negative は neg_rhythm の 0.3 のみ(neg_interval の 1.0 は非混入)。
    assert result["axes"]["rhythm"]["positive_min"] == pytest.approx(0.8)
    assert result["axes"]["rhythm"]["negative_max"] == pytest.approx(0.3)
    assert result["axes"]["rhythm"]["margin"] == pytest.approx(0.5)
    assert result["axes"]["rhythm"]["calibrated_candidate"] is True

    assert set(result["calibrated_axes"]) == {"contour", "interval", "rhythm"}


def test_margin_table_negative_empty_axis_reports_negative_empty_reason():
    """狙い撃ち negative のみの構成で、あるスコープ外の軸の negative バケットが
    空になった場合、`reason: "negative_empty"` を記録し calibrated 候補にしない
    （`insufficient_positive_or_negative_samples` とは区別する）。
    """
    pairs: Dict[str, Dict[str, Any]] = {
        "pos1": {
            "split": "tuning",
            "expected": "same",
            "kind": "positive_transform",
            "comparison": {
                "evidence": "none",
                "axes": {"contour": 0.9, "interval": 0.85, "rhythm": 0.8},
            },
        },
        # negative_rhythm のみ(negative_cross/negative_interval 無し) — interval/
        # contour の negative バケットはどの pair からも供給されず空になる。
        "neg_rhythm": {
            "split": "tuning",
            "expected": "different",
            "kind": "negative_rhythm",
            "comparison": {
                "evidence": "none",
                "axes": {"contour": 1.0, "interval": 1.0, "rhythm": 0.3},
            },
        },
    }

    result = harness._margin_table(pairs, split="tuning", min_margin=0.15)

    assert result["axes"]["rhythm"]["calibrated_candidate"] is True

    for axis in ("contour", "interval"):
        assert result["axes"][axis]["calibrated_candidate"] is False
        assert result["axes"][axis]["reason"] == "negative_empty"
        assert result["axes"][axis]["positive_min"] is None
        assert result["axes"][axis]["negative_max"] is None

    assert result["calibrated_axes"] == ["rhythm"]


def test_holdout_validation_table_negative_kind_axis_scope_hand_calc():
    """`_holdout_validation_table` も `_margin_table` と同じ
    `_NEGATIVE_KIND_AXIS_SCOPE` を適用し、狙い撃ち negative の保存軸の高い
    類似値が当該軸の negative バケットへ混入しないことを手計算で確認する。
    """
    frozen_axes = {
        "contour": {"strong_min": 0.8, "none_max": 0.2},
        "interval": {"strong_min": 0.8, "none_max": 0.2},
        "rhythm": {"strong_min": 0.7, "none_max": 0.3},
    }
    pairs: Dict[str, Dict[str, Any]] = {
        "h_pos": {
            "split": "holdout",
            "expected": "same",
            "kind": "positive_transform",
            "comparison": {
                "evidence": "none",
                "axes": {"contour": 0.95, "interval": 0.9, "rhythm": 0.85},
            },
        },
        "h_neg_rhythm": {
            "split": "holdout",
            "expected": "different",
            "kind": "negative_rhythm",
            "comparison": {
                "evidence": "none",
                # interval/contour は保存 ≈ 1.0 — もし interval の negative バケットへ
                # 混入すれば negative_max=1.0 > none_max=0.2 で holdout_failed になる。
                "axes": {"contour": 1.0, "interval": 1.0, "rhythm": 0.25},
            },
        },
        "h_neg_interval": {
            "split": "holdout",
            "expected": "different",
            "kind": "negative_interval",
            "comparison": {
                "evidence": "none",
                # rhythm は保存 ≈ 1.0 — もし rhythm の negative バケットへ混入すれば
                # negative_max=1.0 > none_max=0.3 で holdout_failed になる。
                "axes": {"contour": 0.2, "interval": 0.15, "rhythm": 1.0},
            },
        },
    }

    result = harness._holdout_validation_table(pairs, frozen_axes=frozen_axes)

    assert result["axes"]["contour"]["holdout_positive_min"] == pytest.approx(0.95)
    assert result["axes"]["contour"]["holdout_negative_max"] == pytest.approx(0.2)
    assert result["axes"]["contour"]["verdict"] == "confirmed"

    assert result["axes"]["interval"]["holdout_positive_min"] == pytest.approx(0.9)
    assert result["axes"]["interval"]["holdout_negative_max"] == pytest.approx(0.15)
    assert result["axes"]["interval"]["verdict"] == "confirmed"

    assert result["axes"]["rhythm"]["holdout_positive_min"] == pytest.approx(0.85)
    assert result["axes"]["rhythm"]["holdout_negative_max"] == pytest.approx(0.25)
    assert result["axes"]["rhythm"]["verdict"] == "confirmed"


def test_holdout_validation_table_negative_empty_axis_reports_negative_empty_reason():
    """狙い撃ち negative のみ(negative_rhythm のみ)の holdout 構成で、スコープ外の
    軸の negative バケットが空になれば `reason: "negative_empty"` を伴う
    `holdout_failed` として記録する(判定不能を confirmed 側に倒さない)。
    """
    frozen_axes = {
        "contour": {"strong_min": 0.8, "none_max": 0.2},
        "interval": {"strong_min": 0.8, "none_max": 0.2},
        "rhythm": {"strong_min": 0.7, "none_max": 0.3},
    }
    pairs: Dict[str, Dict[str, Any]] = {
        "h_pos": {
            "split": "holdout",
            "expected": "same",
            "kind": "positive_transform",
            "comparison": {
                "evidence": "none",
                "axes": {"contour": 0.95, "interval": 0.9, "rhythm": 0.85},
            },
        },
        "h_neg_rhythm": {
            "split": "holdout",
            "expected": "different",
            "kind": "negative_rhythm",
            "comparison": {
                "evidence": "none",
                "axes": {"contour": 1.0, "interval": 1.0, "rhythm": 0.25},
            },
        },
    }

    result = harness._holdout_validation_table(pairs, frozen_axes=frozen_axes)

    assert result["axes"]["rhythm"]["verdict"] == "confirmed"
    for axis in ("contour", "interval"):
        assert result["axes"][axis]["verdict"] == "holdout_failed"
        assert result["axes"][axis]["reason"] == "negative_empty"
        assert result["axes"][axis]["holdout_positive_min"] is None
        assert result["axes"][axis]["holdout_negative_max"] is None


# --------------------------------------------------------------------------- #
# not_comparable の正直会計（レビュー対応 2026-07-30 第 5 ラウンド）
# --------------------------------------------------------------------------- #
def test_margin_table_records_not_comparable_positive_and_negative_counts():
    """`not_comparable` で除外した pair は `skipped_pairs` だけでなく、positive/negative
    の内訳もカウントする。カウントは `skipped_pairs` の件数と一致しなければならない。
    """
    pairs = {
        "pos_ok": {
            "split": "tuning",
            "expected": "same",
            "comparison": {"evidence": "none", "axes": {"contour": 0.9, "interval": 0.85, "rhythm": 0.8}},
        },
        "pos_nc": {
            "split": "tuning",
            "expected": "same",
            "comparison": {
                "evidence": "not_comparable",
                "axes": {"contour": None, "interval": None, "rhythm": None},
            },
        },
        "neg_ok": {
            "split": "tuning",
            "expected": "different",
            "kind": "negative_cross",
            "comparison": {"evidence": "none", "axes": {"contour": 0.1, "interval": 0.05, "rhythm": 0.2}},
        },
        "neg_nc1": {
            "split": "tuning",
            "expected": "different",
            "comparison": {
                "evidence": "not_comparable",
                "axes": {"contour": None, "interval": None, "rhythm": None},
            },
        },
        "neg_nc2": {
            "split": "tuning",
            "expected": "different",
            "comparison": {
                "evidence": "not_comparable",
                "axes": {"contour": None, "interval": None, "rhythm": None},
            },
        },
    }
    result = harness._margin_table(pairs, split="tuning", min_margin=0.15)

    assert result["not_comparable_positive_count"] == 1
    assert result["not_comparable_negative_count"] == 2
    assert set(result["skipped_pairs"]) == {
        "pos_nc:not_comparable",
        "neg_nc1:not_comparable",
        "neg_nc2:not_comparable",
    }
    # not_comparable を除いても pos_ok/neg_ok の実値だけで各軸マージンは算出される
    # (not_comparable の除外自体は従来どおり計算に影響しない)。
    assert result["calibrated_axes"] == ["contour", "interval", "rhythm"]


# --------------------------------------------------------------------------- #
# freeze proposal 前の coverage 完全性検証（レビュー対応 2026-07-30 第 15 ラウンド）
# --------------------------------------------------------------------------- #
def test_validate_tuning_coverage_completeness_accepts_full_distribution():
    """`_synthetic_pairs_for_margin` の coverage は全て整形済み — 違反なし。"""
    pairs = _synthetic_pairs_for_margin()
    assert harness._validate_tuning_coverage_completeness(pairs) == []


def test_validate_tuning_coverage_completeness_flags_missing_field():
    pairs = _synthetic_pairs_for_margin()
    del pairs["pos1"]["comparison"]["coverage"]["phrase_coverage_b"]
    assert harness._validate_tuning_coverage_completeness(pairs) == ["pos1"]


def test_validate_tuning_coverage_completeness_flags_out_of_range_value():
    pairs = _synthetic_pairs_for_margin()
    pairs["neg1"]["comparison"]["coverage"]["aligned_note_fraction_a"] = 1.5
    assert harness._validate_tuning_coverage_completeness(pairs) == ["neg1"]


def test_validate_tuning_coverage_completeness_flags_bool_value():
    pairs = _synthetic_pairs_for_margin()
    pairs["pos2"]["comparison"]["coverage"]["phrase_coverage_a"] = True
    assert harness._validate_tuning_coverage_completeness(pairs) == ["pos2"]


def test_validate_tuning_coverage_completeness_ignores_not_comparable_and_holdout():
    """not_comparable pair・holdout split pair の壊れた coverage は対象外。"""
    pairs = _synthetic_pairs_for_margin()
    pairs["nc1"] = {
        "split": "tuning",
        "expected": "same",
        "comparison": {"evidence": "not_comparable", "axes": {}, "coverage": {}},
    }
    pairs["pos_holdout"]["comparison"]["coverage"] = {}
    assert harness._validate_tuning_coverage_completeness(pairs) == []


# --------------------------------------------------------------------------- #
# holdout 行の coverage 完全性 + floor 充足（レビュー対応 2026-07-30 第 18 ラウンド）
# --------------------------------------------------------------------------- #
def _synthetic_holdout_pairs(*, floor: float = 0.5) -> Dict[str, Dict[str, Any]]:
    """`_synthetic_pairs_for_margin` と同型・holdout split のみの合成 pairs。"""

    def _row(coverage: Dict[str, Any], *, split: str = "holdout") -> Dict[str, Any]:
        return {
            "split": split,
            "expected": "same",
            "comparison": {
                "evidence": "strong",
                "axes": {"contour": 0.9, "interval": 0.85, "rhythm": 0.8},
                "coverage": coverage,
            },
        }

    return {
        "h_ok": _row(
            {
                "aligned_note_fraction_a": 0.9,
                "aligned_note_fraction_b": 0.85,
                "phrase_coverage_a": 1.0,
                "phrase_coverage_b": 1.0,
            }
        ),
    }


def test_validate_holdout_coverage_and_floor_accepts_well_formed_above_floor():
    pairs = _synthetic_holdout_pairs()
    coverage_invalid, below_floor = harness._validate_holdout_coverage_and_floor(pairs, floor=0.5)
    assert coverage_invalid == []
    assert below_floor == []


def test_validate_holdout_coverage_and_floor_flags_missing_field():
    pairs = _synthetic_holdout_pairs()
    del pairs["h_ok"]["comparison"]["coverage"]["phrase_coverage_b"]
    coverage_invalid, below_floor = harness._validate_holdout_coverage_and_floor(pairs, floor=0.5)
    assert coverage_invalid == ["h_ok"]
    assert below_floor == []


def test_validate_holdout_coverage_and_floor_flags_non_mapping_coverage():
    pairs = _synthetic_holdout_pairs()
    pairs["h_ok"]["comparison"]["coverage"] = None
    coverage_invalid, below_floor = harness._validate_holdout_coverage_and_floor(pairs, floor=0.5)
    assert coverage_invalid == ["h_ok"]
    assert below_floor == []


def test_validate_holdout_coverage_and_floor_flags_out_of_range_value():
    pairs = _synthetic_holdout_pairs()
    pairs["h_ok"]["comparison"]["coverage"]["aligned_note_fraction_a"] = 1.5
    coverage_invalid, below_floor = harness._validate_holdout_coverage_and_floor(pairs, floor=0.5)
    assert coverage_invalid == ["h_ok"]
    assert below_floor == []


def test_validate_holdout_coverage_and_floor_flags_bool_value():
    pairs = _synthetic_holdout_pairs()
    pairs["h_ok"]["comparison"]["coverage"]["phrase_coverage_a"] = True
    coverage_invalid, below_floor = harness._validate_holdout_coverage_and_floor(pairs, floor=0.5)
    assert coverage_invalid == ["h_ok"]
    assert below_floor == []


def test_validate_holdout_coverage_and_floor_flags_below_floor():
    """`min(aligned_note_fraction_a, b) < floor` なら `below_floor` に積む
    （coverage 自体は整形済みのため `coverage_invalid` には積まない）。
    """
    pairs = _synthetic_holdout_pairs()
    pairs["h_ok"]["comparison"]["coverage"]["aligned_note_fraction_b"] = 0.4
    coverage_invalid, below_floor = harness._validate_holdout_coverage_and_floor(pairs, floor=0.5)
    assert coverage_invalid == []
    assert below_floor == ["h_ok"]


def test_validate_holdout_coverage_and_floor_accepts_exactly_at_floor():
    """`min(...) == floor`（境界値）は floor 未満ではないため below_floor に積まない。"""
    pairs = _synthetic_holdout_pairs()
    pairs["h_ok"]["comparison"]["coverage"]["aligned_note_fraction_b"] = 0.5
    coverage_invalid, below_floor = harness._validate_holdout_coverage_and_floor(pairs, floor=0.5)
    assert coverage_invalid == []
    assert below_floor == []


def test_validate_holdout_coverage_and_floor_ignores_tuning_and_missing_comparison():
    """tuning split の pair・`comparison` を持たない(holdout ロック)行は対象外。"""
    pairs = _synthetic_holdout_pairs()
    pairs["h_ok"]["comparison"]["coverage"]["aligned_note_fraction_b"] = 0.1  # 本来なら below_floor
    pairs["h_ok"]["split"] = "tuning"  # tuning へ変えると対象外になる
    pairs["h_locked"] = {"split": "holdout", "status": "holdout_locked_until_frozen"}
    coverage_invalid, below_floor = harness._validate_holdout_coverage_and_floor(pairs, floor=0.5)
    assert coverage_invalid == []
    assert below_floor == []


def test_evaluate_holdout_validation_not_confirmed_when_coverage_invalid(
    tmp_path: Path, audio_paths: Dict[str, str]
):
    """holdout 行の `comparison.coverage` が壊れていれば(欠落フィールド)、軸別
    verdict が全て confirmed でも `holdout_validation_status` は
    `calibration_not_confirmed_on_holdout` に倒れ、`holdout_validation_reasons` に
    `holdout_pair_coverage_invalid(<pair_id>)` が記録される。
    """
    reports, frozen_registry = _reports_for_holdout_validation(
        tmp_path,
        audio_paths,
        positive_axes_override={"contour": 0.95, "interval": 1.0, "rhythm": 0.9},
        negative_axes_override={"contour": 0.1, "interval": 0.05, "rhythm": 0.2},
    )
    for report in reports:
        del report["pairs"]["p_pos_holdout"]["comparison"]["coverage"]["phrase_coverage_b"]

    verdict = harness.evaluate_comparison(reports, registry_path=frozen_registry)

    assert verdict["holdout_validation"] == {
        "contour": "confirmed",
        "interval": "confirmed",
        "rhythm": "confirmed",
    }
    assert verdict["holdout_validation_status"] == "calibration_not_confirmed_on_holdout"
    assert "holdout_pair_coverage_invalid(p_pos_holdout)" in verdict["holdout_validation_reasons"]


def test_evaluate_holdout_validation_not_confirmed_when_below_coverage_floor(
    tmp_path: Path, audio_paths: Dict[str, str]
):
    """holdout 行の被覆が凍結 `coverage.floor` 未満なら、軸別 verdict が全て
    confirmed でも `holdout_validation_status` は
    `calibration_not_confirmed_on_holdout` に倒れ、`holdout_validation_reasons` に
    `holdout_pair_below_floor(<pair_id>)` が記録される（floor 未満なのに
    favorable axes を持つ行は、run 時の coverage floor gate が本来
    not_comparable へ倒すはずの内部不整合に対する防御）。
    """
    reports, frozen_registry = _reports_for_holdout_validation(
        tmp_path,
        audio_paths,
        positive_axes_override={"contour": 0.95, "interval": 1.0, "rhythm": 0.9},
        negative_axes_override={"contour": 0.1, "interval": 0.05, "rhythm": 0.2},
    )
    config, _ = harness.load_m3_registry(frozen_registry)
    floor = config.coverage.floor
    for report in reports:
        coverage = report["pairs"]["p_pos_holdout"]["comparison"]["coverage"]
        coverage["aligned_note_fraction_a"] = max(0.0, floor - 0.05)

    verdict = harness.evaluate_comparison(reports, registry_path=frozen_registry)

    assert verdict["holdout_validation"] == {
        "contour": "confirmed",
        "interval": "confirmed",
        "rhythm": "confirmed",
    }
    assert verdict["holdout_validation_status"] == "calibration_not_confirmed_on_holdout"
    assert "holdout_pair_below_floor(p_pos_holdout)" in verdict["holdout_validation_reasons"]


def test_evaluate_holdout_validation_confirmed_stays_confirmed_with_valid_coverage(
    tmp_path: Path, audio_paths: Dict[str, str]
):
    """holdout 行の coverage が整形済み・floor 以上であれば、新検証を追加しても
    既存の `confirmed` 判定（回帰）は崩れない。
    """
    reports, frozen_registry = _reports_for_holdout_validation(
        tmp_path,
        audio_paths,
        positive_axes_override={"contour": 0.95, "interval": 1.0, "rhythm": 0.9},
        negative_axes_override={"contour": 0.1, "interval": 0.05, "rhythm": 0.2},
    )

    verdict = harness.evaluate_comparison(reports, registry_path=frozen_registry)

    assert verdict["holdout_validation_status"] == "confirmed"
    assert "holdout_validation_reasons" not in verdict


def test_evaluate_rejects_freeze_proposal_when_tuning_positive_not_comparable(
    tmp_path: Path, audio_paths: Dict[str, str]
):
    """tuning split の positive pair が 1 件でも not_comparable なら freeze proposal
    を発行しない(`rejected_positive_not_comparable` を理由として記録)。negative の
    not_comparable は除外 + カウント記録のみで発行を妨げないことも併せて確認する
    (レビュー対応 2026-07-30 第 5 ラウンド・正直会計)。
    """
    pairs = _default_pairs(audio_paths) + [
        {
            "pair_id": "p_pos_tuning2",
            "kind": "positive_transform",
            "split": "tuning",
            "audio_a": audio_paths["song_a"],
            "audio_b": audio_paths["song_a_transposed"],
            "expected": "same",
        },
    ]
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, pairs)
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    reports = []
    for _ in range(2):
        report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
        report["route_runner_injected"] = False
        # p_neg_tuning は実データでは observation/coverage の都合で既に
        # not_comparable になる(本レビュー対象外の既存挙動)——ここでは margin
        # 計算に real な負例信号を与えるため、テスト用に軸値を上書きする(テスト
        # 対象は「positive の not_comparable が freeze_proposal を止めること」で
        # あって negative 側の生成過程ではない)。
        # p_neg_rhythm_tuning/p_neg_interval_tuning も song_a/song_b の同一ペア
        # （レビュー対応 2026-07-30 第 10 ラウンド・manifest 構成要件で追加された
        # 狙い撃ち negative）で、同じ理由でデフォルトでは not_comparable になる
        # ——not_comparable_negative_count を 0 に保つため p_neg_tuning と同様に
        # 上書きする（この 3 pair の生成過程はテスト対象ではない）。
        for neg_pair_id in ("p_neg_tuning", "p_neg_rhythm_tuning", "p_neg_interval_tuning"):
            report["pairs"][neg_pair_id]["comparison"]["evidence"] = "none"
            report["pairs"][neg_pair_id]["comparison"]["axes"] = {
                "contour": 0.1,
                "interval": 0.05,
                "rhythm": 0.2,
            }
        # p_pos_tuning を not_comparable へ上書き(p_pos_tuning2 は実データのまま
        # 高い一致率を保つ——tuning positive pair が他にも存在する状況で、1 件の
        # not_comparable だけで freeze proposal 全体が止まることを確認する)。
        report["pairs"]["p_pos_tuning"]["comparison"]["evidence"] = "not_comparable"
        report["pairs"]["p_pos_tuning"]["comparison"]["axes"] = {
            "contour": None,
            "interval": None,
            "rhythm": None,
        }
        reports.append(report)

    verdict = harness.evaluate_comparison(reports)

    assert verdict["repeats_verified"] is True
    assert verdict["not_comparable_positive_count"] == 1
    assert verdict["not_comparable_negative_count"] == 0
    # p_pos_tuning2(実データ)と上書き済み p_neg_tuning だけを見れば各軸マージンは
    # 十分確保されている(=freeze_proposal が非空になり得る状況)ことを確認した
    # うえで、tuning positive の not_comparable がそれを上書きすることを見る。
    assert verdict["calibrated_axes"]
    assert verdict["freeze_proposal"] == {}
    assert verdict["freeze_proposal_rejected_reason"] == "rejected_positive_not_comparable"


def test_evaluate_rejects_freeze_proposal_when_tuning_coverage_field_missing(
    tmp_path: Path, audio_paths: Dict[str, str]
):
    """比較可能な tuning pair の `comparison.coverage` にフィールド欠落があれば、
    freeze proposal と coverage floor 候補の双方を発行しない（レビュー対応
    2026-07-30 第 15 ラウンド）。margin_table 自体は emit される。
    """
    reports = _two_reports_for_calibration_verdict(tmp_path, audio_paths)
    for report in reports:
        del report["pairs"]["p_pos_tuning"]["comparison"]["coverage"]["phrase_coverage_b"]

    verdict = harness.evaluate_comparison(reports)

    assert "margin_table" in verdict
    assert verdict["freeze_proposal"] == {}
    assert verdict["freeze_proposal_rejected_reason"] == "coverage_incomplete(p_pos_tuning)"
    assert "coverage_floor_candidate" not in verdict


@pytest.mark.parametrize(
    "bad_value",
    [2.0, -1.0, True],
    ids=["out_of_range_high", "out_of_range_low", "bool_value"],
)
def test_evaluate_rejects_freeze_proposal_when_tuning_coverage_value_invalid(
    tmp_path: Path, audio_paths: Dict[str, str], bad_value: Any
):
    """`comparison.coverage` の値が域外(2.0/-1.0)・bool(True)のいずれかなら、
    freeze proposal と coverage floor 候補の双方を発行しない。
    """
    reports = _two_reports_for_calibration_verdict(tmp_path, audio_paths)
    for report in reports:
        report["pairs"]["p_pos_tuning"]["comparison"]["coverage"][
            "aligned_note_fraction_a"
        ] = bad_value

    verdict = harness.evaluate_comparison(reports)

    assert "margin_table" in verdict
    assert verdict["freeze_proposal"] == {}
    assert verdict["freeze_proposal_rejected_reason"] == "coverage_incomplete(p_pos_tuning)"
    assert "coverage_floor_candidate" not in verdict


# --------------------------------------------------------------------------- #
# holdout ロック
# --------------------------------------------------------------------------- #
def test_holdout_locked_until_frozen(tmp_path: Path, audio_paths: Dict[str, str]):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)

    # route_runner_injected だと calibration verdict 自体を発行しないため、holdout
    # ロック機構だけを単体で検証するには内部の margin/holdout ヘルパーを直接使う。
    holdout_ids = harness._holdout_pair_ids(report["pairs"])
    # `_default_pairs` は holdout split に positive (p_pos_holdout) と negative
    # (p_neg_holdout) を各 1 件以上持つ(manifest 構成要件・レビュー対応
    # 2026-07-30 第 10 ラウンド)。`_holdout_pair_ids` はソート済みで返す。
    assert holdout_ids == ["p_neg_holdout", "p_pos_holdout"]

    mapping = yaml.safe_load(M3_REGISTRY_PATH.read_text(encoding="utf-8"))
    assert mapping["evidence_thresholds"]["status"] == "uncalibrated"

    # uncalibrated の間は margin 計算からも holdout pair を除外する(tuning のみ)。
    margin = harness._margin_table(report["pairs"], split="tuning", min_margin=0.15)
    assert "p_pos_holdout" not in str(margin)  # tuning フィルタで holdout 行が混入していない
    assert "p_neg_holdout" not in str(margin)


def test_evaluate_comparison_records_holdout_lock_when_not_route_runner_injected(tmp_path: Path, audio_paths: Dict[str, str]):
    """calibration verdict が発行されるケース(route_runner 非注入相当・repeats n=2)での
    holdout ロック記録。

    `evaluate_comparison` は report 自身の `route_runner_injected` フラグだけを見て
    拒否判定するため、フラグを手動で False に落とした report（値の出所を偽装しない
    範囲でのメカニズムテスト）を 2 本（repeats n>=2 要件を満たす）用意し、holdout
    ロックの記録を確認する。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    reports_for_verdict = []
    for _ in range(2):
        report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
        report["route_runner_injected"] = False
        reports_for_verdict.append(report)

    verdict = harness.evaluate_comparison(reports_for_verdict)
    assert verdict["repeats_verified"] is True
    assert verdict["repeats_consistent"] is True
    assert verdict["holdout_locked_until_frozen"] is True
    assert verdict["holdout_pair_ids_skipped"] == ["p_neg_holdout", "p_pos_holdout"]
    assert "calibration_verdict_status" not in verdict
    assert "margin_table" in verdict
    assert "coverage_floor_candidate" in verdict
    # holdout ロック中（uncalibrated）は holdout 検証節を一切 emit しない
    # （既存動作は不変・レビュー対応 2026-07-30 第 10 ラウンド）。
    assert "holdout_table" not in verdict
    assert "holdout_validation" not in verdict
    assert "holdout_validation_status" not in verdict


def test_evaluate_rejects_single_report_for_calibration_verdict(tmp_path: Path, audio_paths: Dict[str, str]):
    """repeats n>=2 の事前登録要求（設計 §6.2）: n=1 は校正証拠として発行しない。"""
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report["route_runner_injected"] = False  # 「実測」相当だが repeats が 1 本しかない。

    verdict = harness.evaluate_comparison([report])

    assert verdict["repeats_count"] == 1
    assert verdict["repeats_verified"] is False
    assert verdict["repeats_consistent"] is False
    assert verdict["calibration_verdict_status"] == "rejected_insufficient_repeats"
    assert verdict["reason"] == "insufficient_repeats(n=1, required>=2)"
    assert "margin_table" not in verdict
    assert "freeze_proposal" not in verdict
    assert "coverage_floor_candidate" not in verdict


# --------------------------------------------------------------------------- #
# route_runner_injected による calibration verdict 発行拒否
# --------------------------------------------------------------------------- #
def test_route_runner_injected_rejects_calibration_verdict(tmp_path: Path, audio_paths: Dict[str, str]):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)

    assert report["route_runner_injected"] is True
    verdict = harness.evaluate_comparison([report])
    assert verdict["calibration_verdict_status"] == "rejected_route_runner_injected"
    assert "margin_table" not in verdict
    assert "freeze_proposal" not in verdict


def test_evaluate_rejects_missing_route_runner_injected_field(tmp_path: Path, audio_paths: Dict[str, str]):
    """`route_runner_injected` キー自体が欠落した report は理由つきで拒否する
    (レビュー対応 2026-07-30 第 3 ラウンド: 欠落を `bool(None)` で False 扱いに
    フォールバックさせない)。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    del report["route_runner_injected"]

    with pytest.raises(ValueError, match="route_runner_injected"):
        harness.evaluate_comparison([report])


def test_evaluate_rejects_non_bool_route_runner_injected_field(tmp_path: Path, audio_paths: Dict[str, str]):
    """`route_runner_injected` が bool でない(文字列/整数)場合は拒否する。"""
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    report_str = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report_str["route_runner_injected"] = "true"
    with pytest.raises(ValueError, match="route_runner_injected"):
        harness.evaluate_comparison([report_str])

    report_int = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report_int["route_runner_injected"] = 1
    with pytest.raises(ValueError, match="route_runner_injected"):
        harness.evaluate_comparison([report_int])


# --------------------------------------------------------------------------- #
# --out の protected-path
# --------------------------------------------------------------------------- #
def test_out_cannot_overwrite_pairs_manifest(tmp_path: Path, monkeypatch, capsys, audio_paths: Dict[str, str]):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))

    argv = [
        "run_melody_comparison.py",
        "--pairs",
        str(manifest_path),
        "--out",
        str(manifest_path),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit, match="fail-closed"):
        harness.main()


def test_out_cannot_overwrite_registry(tmp_path: Path, monkeypatch, audio_paths: Dict[str, str]):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))

    argv = [
        "run_melody_comparison.py",
        "--pairs",
        str(manifest_path),
        "--out",
        str(M3_REGISTRY_PATH),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit, match="fail-closed"):
        harness.main()


def test_out_cannot_overwrite_evaluate_input(tmp_path: Path, monkeypatch, audio_paths: Dict[str, str]):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    argv = [
        "run_melody_comparison.py",
        "--evaluate",
        str(report_path),
        "--out",
        str(report_path),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit, match="fail-closed"):
        harness.main()


# --------------------------------------------------------------------------- #
# manifest 検証（fail-closed）
# --------------------------------------------------------------------------- #
def test_manifest_rejects_unknown_schema(tmp_path: Path, audio_paths: Dict[str, str]):
    manifest_path = tmp_path / "pairs.yaml"
    manifest_path.write_text(
        yaml.safe_dump({"schema": "bogus/9.9", "pairs": _default_pairs(audio_paths)}), encoding="utf-8"
    )
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    with pytest.raises(ValueError, match="unsupported pairs manifest schema"):
        harness.run_comparison(manifest_path=manifest_path, route_runner=runner)


def test_manifest_rejects_duplicate_pair_id(tmp_path: Path, audio_paths: Dict[str, str]):
    pairs = _default_pairs(audio_paths)
    pairs.append(dict(pairs[0]))
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, pairs)
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    with pytest.raises(ValueError, match="duplicate pair_id"):
        harness.run_comparison(manifest_path=manifest_path, route_runner=runner)


def test_manifest_rejects_invalid_kind():
    manifest = {
        "schema": "m3-comparison-pairs/0.1",
        "pairs": [
            {
                "pair_id": "p1",
                "kind": "bogus_kind",
                "split": "tuning",
                "audio_a": "a",
                "audio_b": "b",
                "expected": "same",
            }
        ],
    }
    with pytest.raises(ValueError, match="invalid kind"):
        harness._validate_manifest(manifest)


def test_manifest_rejects_positive_transform_with_expected_different():
    """`kind: positive_transform` は `expected: same` でなければ矛盾(fail-closed)。"""
    manifest = {
        "schema": "m3-comparison-pairs/0.1",
        "pairs": [
            {
                "pair_id": "p_bad",
                "kind": "positive_transform",
                "split": "tuning",
                "audio_a": "a",
                "audio_b": "b",
                "expected": "different",
            }
        ],
    }
    with pytest.raises(ValueError, match=r"p_bad.*kind.*expected"):
        harness._validate_manifest(manifest)


def test_manifest_rejects_negative_kind_with_expected_same():
    """`kind: negative_*` は `expected: different` でなければ矛盾(fail-closed)。"""
    manifest = {
        "schema": "m3-comparison-pairs/0.1",
        "pairs": [
            {
                "pair_id": "p_bad",
                "kind": "negative_cross",
                "split": "tuning",
                "audio_a": "a",
                "audio_b": "b",
                "expected": "same",
            }
        ],
    }
    with pytest.raises(ValueError, match=r"p_bad.*kind.*expected"):
        harness._validate_manifest(manifest)


# --------------------------------------------------------------------------- #
# manifest 素材構成の事前登録検査（設計 §6.1・レビュー対応 2026-07-30 第 10 ラウンド）
# --------------------------------------------------------------------------- #
def _composition_compliant_manifest_pairs() -> List[Dict[str, Any]]:
    """`_validate_manifest_composition` の全要件を満たす最小 pair 集合（audio
    path はダミー文字列 — `_validate_manifest` はファイルシステムを読まない）。
    """
    return [
        {
            "pair_id": "t_pos",
            "kind": "positive_transform",
            "split": "tuning",
            "audio_a": "a",
            "audio_b": "b",
            "expected": "same",
        },
        {
            "pair_id": "t_neg",
            "kind": "negative_cross",
            "split": "tuning",
            "audio_a": "a",
            "audio_b": "b",
            "expected": "different",
        },
        {
            "pair_id": "t_neg_rhythm",
            "kind": "negative_rhythm",
            "split": "tuning",
            "audio_a": "a",
            "audio_b": "b",
            "expected": "different",
        },
        {
            "pair_id": "t_neg_interval",
            "kind": "negative_interval",
            "split": "tuning",
            "audio_a": "a",
            "audio_b": "b",
            "expected": "different",
        },
        {
            "pair_id": "h_pos",
            "kind": "positive_transform",
            "split": "holdout",
            "audio_a": "a",
            "audio_b": "b",
            "expected": "same",
        },
        {
            "pair_id": "h_neg",
            "kind": "negative_cross",
            "split": "holdout",
            "audio_a": "a",
            "audio_b": "b",
            "expected": "different",
        },
    ]


def test_manifest_composition_accepts_fully_compliant_pairs():
    """設計 §6.1 の全要件（tuning positive/negative・狙い撃ち negative 各軸・
    holdout positive/negative）を満たす manifest は composition 検査を通過する
    （以降の欠落系テストの baseline が有効であることの確認を兼ねる）。
    """
    manifest = {
        "schema": "m3-comparison-pairs/0.1",
        "pairs": _composition_compliant_manifest_pairs(),
    }
    validated = harness._validate_manifest(manifest)
    assert len(validated) == 6


def test_manifest_composition_rejects_missing_targeted_negative():
    """狙い撃ち negative（`negative_rhythm`）が manifest に 1 件も無ければ、
    欠けている要件を理由に列挙して fail-closed 拒否する。"""
    pairs = [
        p for p in _composition_compliant_manifest_pairs() if p["kind"] != "negative_rhythm"
    ]
    manifest = {"schema": "m3-comparison-pairs/0.1", "pairs": pairs}
    with pytest.raises(ValueError, match="negative_rhythm"):
        harness._validate_manifest(manifest)


def test_manifest_composition_rejects_missing_negative_interval():
    """狙い撃ち negative（`negative_interval`）が manifest に 1 件も無ければ、
    欠けている要件を理由に列挙して fail-closed 拒否する。"""
    pairs = [
        p for p in _composition_compliant_manifest_pairs() if p["kind"] != "negative_interval"
    ]
    manifest = {"schema": "m3-comparison-pairs/0.1", "pairs": pairs}
    with pytest.raises(ValueError, match="negative_interval"):
        harness._validate_manifest(manifest)


def test_manifest_composition_rejects_empty_holdout_split():
    """holdout split が manifest に 1 件も無ければ（positive/negative とも 0 件）、
    欠けている要件を理由に列挙して fail-closed 拒否する。"""
    pairs = [p for p in _composition_compliant_manifest_pairs() if p["split"] != "holdout"]
    manifest = {"schema": "m3-comparison-pairs/0.1", "pairs": pairs}
    with pytest.raises(ValueError, match="holdout"):
        harness._validate_manifest(manifest)


def test_manifest_composition_rejects_holdout_missing_negative_only():
    """holdout split に positive はあるが negative が 0 件の場合も fail-closed 拒否する
    （positive のみの holdout は「未校正証拠を漏らさない」検証にならない）。
    """
    pairs = [
        p
        for p in _composition_compliant_manifest_pairs()
        if not (p["split"] == "holdout" and p["kind"].startswith("negative_"))
    ]
    manifest = {"schema": "m3-comparison-pairs/0.1", "pairs": pairs}
    with pytest.raises(ValueError, match="holdout split に negative"):
        harness._validate_manifest(manifest)


def test_manifest_composition_rejects_tuning_missing_positive_transform():
    """tuning split に positive_transform が 0 件の場合も fail-closed 拒否する。"""
    pairs = [
        p
        for p in _composition_compliant_manifest_pairs()
        if not (p["split"] == "tuning" and p["kind"] == "positive_transform")
    ]
    manifest = {"schema": "m3-comparison-pairs/0.1", "pairs": pairs}
    with pytest.raises(ValueError, match="tuning split に positive_transform"):
        harness._validate_manifest(manifest)


# --------------------------------------------------------------------------- #
# 狙い撃ち negative の tuning split 必須化（レビュー対応 2026-07-30 第 11 ラウンド）
# --------------------------------------------------------------------------- #
def test_manifest_composition_rejects_targeted_negative_rhythm_only_in_holdout():
    """`negative_rhythm`（狙い撃ち negative）が manifest 中に存在しても、tuning
    split ではなく holdout split にしか無ければ fail-closed 拒否する——狙い撃ち
    negative は各軸の弁別を tuning で単独検証する趣旨のため、holdout のみへの
    配置では要件を満たさない（従来は split 不問だったため通っていたケース）。
    """
    pairs = [p for p in _composition_compliant_manifest_pairs() if p["kind"] != "negative_rhythm"]
    pairs.append(
        {
            "pair_id": "h_neg_rhythm_only",
            "kind": "negative_rhythm",
            "split": "holdout",
            "audio_a": "a",
            "audio_b": "b",
            "expected": "different",
        }
    )
    manifest = {"schema": "m3-comparison-pairs/0.1", "pairs": pairs}
    with pytest.raises(ValueError, match="tuning split に negative_rhythm"):
        harness._validate_manifest(manifest)


def test_manifest_composition_rejects_targeted_negative_interval_only_in_holdout():
    """`negative_interval`（狙い撃ち negative）が holdout split にしか無ければ
    fail-closed 拒否する（`negative_rhythm` と対称のケース）。
    """
    pairs = [
        p for p in _composition_compliant_manifest_pairs() if p["kind"] != "negative_interval"
    ]
    pairs.append(
        {
            "pair_id": "h_neg_interval_only",
            "kind": "negative_interval",
            "split": "holdout",
            "audio_a": "a",
            "audio_b": "b",
            "expected": "different",
        }
    )
    manifest = {"schema": "m3-comparison-pairs/0.1", "pairs": pairs}
    with pytest.raises(ValueError, match="tuning split に negative_interval"):
        harness._validate_manifest(manifest)


def test_manifest_composition_accepts_targeted_negative_in_both_tuning_and_holdout():
    """狙い撃ち negative が tuning split の必須本数を満たした上で、holdout に
    **追加で**同じ kind を置くのは妨げない（tuning 必須 + holdout 追加は許可）。
    """
    pairs = _composition_compliant_manifest_pairs() + [
        {
            "pair_id": "h_neg_rhythm_extra",
            "kind": "negative_rhythm",
            "split": "holdout",
            "audio_a": "a",
            "audio_b": "b",
            "expected": "different",
        },
    ]
    manifest = {"schema": "m3-comparison-pairs/0.1", "pairs": pairs}
    validated = harness._validate_manifest(manifest)
    assert len(validated) == 7


# --------------------------------------------------------------------------- #
# --out の protected-path（manifest が指す音声入力）
# --------------------------------------------------------------------------- #
def _manifest_composition_compliant_pairs(audio_a: Path, audio_b: Path) -> List[Dict[str, Any]]:
    """`_validate_manifest_composition`（設計 §6.1・レビュー対応 2026-07-30 第 10
    ラウンド）を満たす最小 pair 集合を、単一の音声ペア（`audio_a`/`audio_b`）だけを
    使い回して構成する。manifest 構成検査は kind/split/expected の組み合わせしか
    見ないため、`--out` protected-path テストのような audio path 自体が主眼の
    テストでは、音声内容の意味的な使い分けをする必要はない。
    """
    return [
        {
            "pair_id": "p1",
            "kind": "positive_transform",
            "split": "tuning",
            "audio_a": str(audio_a),
            "audio_b": str(audio_b),
            "expected": "same",
        },
        {
            "pair_id": "p2_neg_tuning",
            "kind": "negative_cross",
            "split": "tuning",
            "audio_a": str(audio_a),
            "audio_b": str(audio_b),
            "expected": "different",
        },
        {
            "pair_id": "p3_neg_rhythm",
            "kind": "negative_rhythm",
            "split": "tuning",
            "audio_a": str(audio_a),
            "audio_b": str(audio_b),
            "expected": "different",
        },
        {
            "pair_id": "p4_neg_interval",
            "kind": "negative_interval",
            "split": "tuning",
            "audio_a": str(audio_a),
            "audio_b": str(audio_b),
            "expected": "different",
        },
        {
            "pair_id": "p5_pos_holdout",
            "kind": "positive_transform",
            "split": "holdout",
            "audio_a": str(audio_a),
            "audio_b": str(audio_b),
            "expected": "same",
        },
        {
            "pair_id": "p6_neg_holdout",
            "kind": "negative_cross",
            "split": "holdout",
            "audio_a": str(audio_a),
            "audio_b": str(audio_b),
            "expected": "different",
        },
    ]


def test_out_cannot_overwrite_manifest_audio_input(tmp_path: Path, monkeypatch):
    audio_a = tmp_path / "a.wav"
    audio_a.write_bytes(b"fake-audio-a")
    audio_b = tmp_path / "b.wav"
    audio_b.write_bytes(b"fake-audio-b")
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _manifest_composition_compliant_pairs(audio_a, audio_b))

    argv = [
        "run_melody_comparison.py",
        "--pairs",
        str(manifest_path),
        "--out",
        str(audio_a),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit, match="fail-closed"):
        harness.main()


def test_out_cannot_overwrite_manifest_audio_b_input(tmp_path: Path, monkeypatch):
    audio_a = tmp_path / "a.wav"
    audio_a.write_bytes(b"fake-audio-a")
    audio_b = tmp_path / "b.wav"
    audio_b.write_bytes(b"fake-audio-b")
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _manifest_composition_compliant_pairs(audio_a, audio_b))

    argv = [
        "run_melody_comparison.py",
        "--pairs",
        str(manifest_path),
        "--out",
        str(audio_b),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit, match="fail-closed"):
        harness.main()


# --------------------------------------------------------------------------- #
# holdout を run 時点で開かない
# --------------------------------------------------------------------------- #
def test_holdout_pair_not_opened_at_run_time_when_uncalibrated(tmp_path: Path, audio_paths: Dict[str, str]):
    """既定 fixture registry(status=uncalibrated)では holdout pair の音声を run
    phase で一切読まず、report にも axes/hash を書かない。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    base_runner = _fake_route_runner(_notes_by_path(audio_paths))
    calls: List[bytes] = []

    def _tracking_runner(audio_path: str):
        # レビュー対応 2026-07-30（第 5 ラウンド）: runner に渡るのは音声 bytes の
        # 凍結コピー（一時パス）であって元パスではないため、渡された bytes 自体で
        # 呼び出し順序を検証する（一時パス文字列は毎回変わるので比較対象にしない）。
        calls.append(Path(audio_path).read_bytes())
        return base_runner(audio_path)

    mapping = yaml.safe_load(M3_REGISTRY_PATH.read_text(encoding="utf-8"))
    assert mapping["evidence_thresholds"]["status"] == "uncalibrated"

    report = harness.run_comparison(manifest_path=manifest_path, route_runner=_tracking_runner)

    # holdout pair (p_pos_holdout / p_neg_holdout) の音声は一度も runner に
    # 渡されていない——tuning pair (p_pos_tuning/p_neg_tuning/
    # p_neg_rhythm_tuning/p_neg_interval_tuning) の音声のみが manifest 記載順に
    # 読まれる。
    assert calls == [
        Path(audio_paths["song_a"]).read_bytes(),
        Path(audio_paths["song_a_transposed"]).read_bytes(),
        Path(audio_paths["song_a"]).read_bytes(),
        Path(audio_paths["song_b"]).read_bytes(),
        Path(audio_paths["song_a"]).read_bytes(),
        Path(audio_paths["song_b"]).read_bytes(),
        Path(audio_paths["song_a"]).read_bytes(),
        Path(audio_paths["song_b"]).read_bytes(),
    ]

    for holdout_pair_id in ("p_pos_holdout", "p_neg_holdout"):
        holdout_row = report["pairs"][holdout_pair_id]
        assert holdout_row == {"split": "holdout", "status": "holdout_locked_until_frozen"}
        assert "comparison" not in holdout_row
        assert "audio_a" not in holdout_row
        assert "audio_b" not in holdout_row

    # tuning pair は通常通り比較済み。
    assert "comparison" in report["pairs"]["p_pos_tuning"]


def test_holdout_pair_compared_when_registry_frozen(tmp_path: Path, audio_paths: Dict[str, str]):
    """holdout unlock の全条件(status=frozen + axes 揃い + coverage.floor_status=frozen)
    を満たした registry なら holdout pair も通常通り比較される。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    frozen_registry = tmp_path / "frozen_registry.yaml"
    frozen_registry.write_text(_fully_frozen_registry_text(), encoding="utf-8")
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    report = harness.run_comparison(
        manifest_path=manifest_path, route_runner=runner, registry_path=frozen_registry
    )

    holdout_row = report["pairs"]["p_pos_holdout"]
    assert holdout_row["split"] == "holdout"
    assert "comparison" in holdout_row
    assert holdout_row["comparison"]["axes"]["interval"] == pytest.approx(1.0)


def test_holdout_pair_not_opened_when_partially_frozen(tmp_path: Path, audio_paths: Dict[str, str]):
    """`evidence_thresholds.status == "frozen"` + 整形済み axes（1 軸）だけでは
    holdout は開かない — `coverage.floor_status` が provisional のまま
    （`_holdout_unlocked` の cross-field 不変条件 (c)）の「部分凍結」状態は
    ロック継続が正しい（条件 (b) は満たすが (c) が満たされない）。

    レビュー対応 2026-07-30（第 18 ラウンド）: axes 皆無（`axes is None`）の
    「status のみ frozen」という組み合わせは、ローダ側の axes 境界検証
    （`M3ComparisonConfig.from_registry`）が導入されたことでロード自体が
    拒否されるようになった——本テストは axes 皆無ではなく floor_status
    provisional のみを単離するケースへ更新した。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    partial_registry = tmp_path / "partial_registry.yaml"
    partial_registry.write_text(_partially_frozen_registry_text(), encoding="utf-8")
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    config, _ = harness.load_m3_registry(partial_registry)
    assert config.evidence_thresholds.status == "frozen"
    assert set(config.evidence_thresholds.axes) == {"contour"}
    assert config.coverage.floor_status != "frozen"
    assert harness._holdout_unlocked(config) is False

    report = harness.run_comparison(
        manifest_path=manifest_path, route_runner=runner, registry_path=partial_registry
    )

    holdout_row = report["pairs"]["p_pos_holdout"]
    assert holdout_row == {"split": "holdout", "status": "holdout_locked_until_frozen"}
    assert "comparison" not in holdout_row


def test_holdout_unlocked_requires_all_three_conditions():
    """`_holdout_unlocked` は status=frozen + 整形済み axes だけでは真にならない
    (coverage.floor_status も frozen である必要がある)。

    レビュー対応 2026-07-30（第 18 ラウンド）: axes 皆無（`{"status": "frozen"}`
    のみ）はローダ側の axes 境界検証（`M3ComparisonConfig.from_registry`）が
    導入されたことでロード自体が拒否されるようになったため、1 軸のみ整形済みの
    axes を与えた上で coverage.floor_status を provisional のまま残す（条件 (c)
    のみを単離）。
    """
    from svp_rpe.melody.representation import M3ComparisonConfig

    base_mapping = _registry_mapping()

    partial_mapping = dict(base_mapping)
    partial_mapping["evidence_thresholds"] = {
        "status": "frozen",
        "axes": {"contour": {"strong_min": 0.8, "none_max": 0.2}},
    }
    partial_config = M3ComparisonConfig.from_registry(partial_mapping)
    assert partial_config.coverage.floor_status != "frozen"
    assert harness._holdout_unlocked(partial_config) is False

    full_mapping = dict(base_mapping)
    full_mapping["evidence_thresholds"] = {
        "status": "frozen",
        "axes": {
            "contour": {"strong_min": 0.8, "none_max": 0.2},
            "interval": {"strong_min": 0.8, "none_max": 0.2},
            "rhythm": {"strong_min": 0.7, "none_max": 0.3},
        },
    }
    full_mapping["coverage"] = dict(base_mapping["coverage"])
    full_mapping["coverage"]["floor_status"] = "frozen"
    full_config = M3ComparisonConfig.from_registry(full_mapping)
    assert harness._holdout_unlocked(full_config) is True


# レビュー対応 2026-07-30（第 18 ラウンド）: axes 境界の値不変条件検証が
# `M3ComparisonConfig.from_registry`（`representation.py`）へ追加されたため、
# 以下 4 テストが従来のように壊れた axes（空 mapping・大小関係逆転・域外値・
# 非数値）を `from_registry` 経由で作ろうとすると、そこで即座に `ValueError`
# が飛ぶようになった——`_validate_frozen_axes`/`_holdout_unlocked`（ローダを
# 経由しない config にも安全側に倒す defense-in-depth）を単体で確認する目的は
# もう `from_registry` では達成できない。round 17 の `test_holdout_unlocked_
# rejects_invalid_coverage_floor` と同型の回避策（`from_registry` を経由しない
# 直接 dataclass 構築）を、繰り返しが多いため共通ヘルパーへ切り出す。
def _config_with_raw_evidence_thresholds(evidence_thresholds: Any) -> Any:
    """`from_registry` を経由せず、`evidence_thresholds` だけ差し替えた
    `M3ComparisonConfig` を直接構築する。`coverage.floor_status` は "frozen" に
    固定する（axes 検証のみを単離するため——floor_status 自体の検証は別の
    テスト群（`test_holdout_unlocked_rejects_invalid_coverage_floor` 等）が
    担当する）。
    """
    from svp_rpe.melody.representation import CoverageConfig, M3ComparisonConfig

    base_config, _ = harness.load_m3_registry(M3_REGISTRY_PATH)
    return M3ComparisonConfig(
        schema=base_config.schema,
        registered_utc=base_config.registered_utc,
        representation=base_config.representation,
        alignment=base_config.alignment,
        coverage=CoverageConfig(floor=base_config.coverage.floor, floor_status="frozen"),
        evidence_thresholds=evidence_thresholds,
        separation_margin=base_config.separation_margin,
    )


def test_holdout_unlocked_rejects_empty_axes_mapping():
    """axes の 3 キー自体は揃っているが値が空 mapping(`{}`)——holdout は開かない。"""
    from svp_rpe.melody.representation import EvidenceThresholdsConfig

    config = _config_with_raw_evidence_thresholds(
        EvidenceThresholdsConfig(
            status="frozen",
            axes={"contour": {}, "interval": {}, "rhythm": {}},
        )
    )

    assert harness._validate_frozen_axes(config.evidence_thresholds.axes) is False
    assert harness._holdout_unlocked(config) is False


def test_holdout_unlocked_rejects_strong_min_below_none_max():
    """`strong_min < none_max`(大小関係逆転)は holdout を開かない。"""
    from svp_rpe.melody.representation import EvidenceThresholdsConfig

    config = _config_with_raw_evidence_thresholds(
        EvidenceThresholdsConfig(
            status="frozen",
            axes={
                "contour": {"strong_min": 0.1, "none_max": 0.8},
                "interval": {"strong_min": 0.8, "none_max": 0.2},
                "rhythm": {"strong_min": 0.7, "none_max": 0.3},
            },
        )
    )

    assert harness._validate_frozen_axes(config.evidence_thresholds.axes) is False
    assert harness._holdout_unlocked(config) is False


def test_holdout_unlocked_rejects_out_of_range_axis_values():
    """`strong_min`/`none_max` が 0.0〜1.0 域外なら holdout を開かない。"""
    from svp_rpe.melody.representation import EvidenceThresholdsConfig

    config = _config_with_raw_evidence_thresholds(
        EvidenceThresholdsConfig(
            status="frozen",
            axes={
                "contour": {"strong_min": 1.5, "none_max": 0.2},
                "interval": {"strong_min": 0.8, "none_max": 0.2},
                "rhythm": {"strong_min": 0.7, "none_max": 0.3},
            },
        )
    )

    assert harness._validate_frozen_axes(config.evidence_thresholds.axes) is False
    assert harness._holdout_unlocked(config) is False


def test_holdout_unlocked_rejects_non_numeric_axis_values():
    """`strong_min`/`none_max` が非数値(bool 含む)なら holdout を開かない。"""
    from svp_rpe.melody.representation import EvidenceThresholdsConfig

    config = _config_with_raw_evidence_thresholds(
        EvidenceThresholdsConfig(
            status="frozen",
            axes={
                "contour": {"strong_min": True, "none_max": 0.2},
                "interval": {"strong_min": 0.8, "none_max": 0.2},
                "rhythm": {"strong_min": 0.7, "none_max": 0.3},
            },
        )
    )

    assert harness._validate_frozen_axes(config.evidence_thresholds.axes) is False
    assert harness._holdout_unlocked(config) is False

    config_str = _config_with_raw_evidence_thresholds(
        EvidenceThresholdsConfig(
            status="frozen",
            axes={
                "contour": {"strong_min": 0.8, "none_max": "0.2"},
                "interval": {"strong_min": 0.8, "none_max": 0.2},
                "rhythm": {"strong_min": 0.7, "none_max": 0.3},
            },
        )
    )

    assert harness._validate_frozen_axes(config_str.evidence_thresholds.axes) is False
    assert harness._holdout_unlocked(config_str) is False


# --------------------------------------------------------------------------- #
# coverage.floor 値の検証（レビュー対応 2026-07-30 第 14 ラウンド）
# --------------------------------------------------------------------------- #
def test_validate_coverage_floor_accepts_in_range_values():
    assert harness._validate_coverage_floor(0.5) is True
    assert harness._validate_coverage_floor(0.0) is True
    assert harness._validate_coverage_floor(1.0) is True


@pytest.mark.parametrize(
    "bad_floor",
    [float("nan"), -1.0, True],
    ids=["nan", "negative", "bool"],
)
def test_holdout_unlocked_rejects_invalid_coverage_floor(bad_floor: Any):
    """`coverage.floor` の値そのものが NaN/負値/bool なら、`floor_status` が
    frozen かつ axes も揃っていても holdout を開かない（レビュー対応 2026-07-30
    第 14 ラウンド: 従来は `floor_status` という文字列しか見ておらず、値自体の
    破損を検出できなかった）。

    レビュー対応 2026-07-30（第 17 ラウンド）: `coverage.floor` の値不変条件は
    `M3ComparisonConfig.from_registry`（`representation.py`）がロード時に fail-fast
    enforce するようになった——`from_registry` 経由ではもう壊れた floor 値の
    config を作れないため、ここでは（`from_registry` を経由しない）直接
    dataclass 構築で壊れた config を組み、`_validate_coverage_floor` /
    `_holdout_unlocked`（from_registry を経由しない config にも安全側に倒す
    defense-in-depth）が引き続き機能することを確認する。
    """
    from svp_rpe.melody.representation import (
        CoverageConfig,
        EvidenceThresholdsConfig,
        M3ComparisonConfig,
    )

    base_config, _ = harness.load_m3_registry(M3_REGISTRY_PATH)
    config = M3ComparisonConfig(
        schema=base_config.schema,
        registered_utc=base_config.registered_utc,
        representation=base_config.representation,
        alignment=base_config.alignment,
        coverage=CoverageConfig(floor=bad_floor, floor_status="frozen"),
        evidence_thresholds=EvidenceThresholdsConfig(
            status="frozen",
            axes={
                "contour": {"strong_min": 0.8, "none_max": 0.2},
                "interval": {"strong_min": 0.8, "none_max": 0.2},
                "rhythm": {"strong_min": 0.7, "none_max": 0.3},
            },
        ),
        separation_margin=base_config.separation_margin,
    )

    assert harness._validate_coverage_floor(config.coverage.floor) is False
    assert harness._holdout_unlocked(config) is False


# --------------------------------------------------------------------------- #
# 凍結 axes の部分成立（レビュー対応 2026-07-30 第 4 ラウンド: 3 軸全て必須の緩和）
# --------------------------------------------------------------------------- #
def test_validate_frozen_axes_rejects_empty_axes_mapping():
    """`axes` 自体が空 mapping（`{}`）なら fail-closed で拒否する（軸が 1 つも無い）。"""
    assert harness._validate_frozen_axes({}) is False


def test_validate_frozen_axes_rejects_unknown_axis_name():
    """軸名が {contour, interval, rhythm} の部分集合でなければ fail-closed で拒否する
    (タイプミス・registry の書式崩れを見逃さない)。
    """
    axes = {
        "contour": {"strong_min": 0.8, "none_max": 0.2},
        "timbre": {"strong_min": 0.8, "none_max": 0.2},  # 未知軸名
    }
    assert harness._validate_frozen_axes(axes) is False


def test_validate_frozen_axes_accepts_partial_two_axes():
    """3 軸全てではなく 2 軸（contour/interval）のみでも、整形済みなら有効と判定する。"""
    axes = {
        "contour": {"strong_min": 0.8, "none_max": 0.2},
        "interval": {"strong_min": 0.8, "none_max": 0.2},
    }
    assert harness._validate_frozen_axes(axes) is True


# --------------------------------------------------------------------------- #
# 凍結 axes のマージン検証（レビュー対応 2026-07-30 第 16 ラウンド）
# --------------------------------------------------------------------------- #
def test_holdout_unlocked_rejects_axis_margin_below_min_separation_margin():
    """凍結 axes の `strong_min - none_max` が 0（`separation_margin` の 0.15 未満）
    なら、大小関係（`strong_min >= none_max`）自体は満たしていても holdout は
    開かない（`strong_min: 0.50`/`none_max: 0.50` の凍結 registry）。
    """
    from svp_rpe.melody.representation import M3ComparisonConfig

    base_mapping = _registry_mapping()
    assert base_mapping["separation_margin"]["min_same_minus_cross_margin"] == pytest.approx(0.15)
    mapping = dict(base_mapping)
    mapping["evidence_thresholds"] = {
        "status": "frozen",
        "axes": {
            "contour": {"strong_min": 0.50, "none_max": 0.50},
            "interval": {"strong_min": 0.50, "none_max": 0.50},
            "rhythm": {"strong_min": 0.50, "none_max": 0.50},
        },
    }
    mapping["coverage"] = dict(base_mapping["coverage"])
    mapping["coverage"]["floor_status"] = "frozen"
    config = M3ComparisonConfig.from_registry(mapping)

    assert (
        harness._validate_frozen_axes(
            config.evidence_thresholds.axes,
            min_separation_margin=config.separation_margin.min_same_minus_cross_margin,
        )
        is False
    )
    assert harness._holdout_unlocked(config) is False


def test_holdout_unlocked_opens_when_axis_margin_exactly_meets_min_separation_margin():
    """`strong_min - none_max` がちょうど `separation_margin`（0.15）に一致すれば
    （`>=` 判定の境界値）、他条件が揃っていれば holdout が開く。
    """
    from svp_rpe.melody.representation import M3ComparisonConfig

    base_mapping = _registry_mapping()
    assert base_mapping["separation_margin"]["min_same_minus_cross_margin"] == pytest.approx(0.15)
    mapping = dict(base_mapping)
    mapping["evidence_thresholds"] = {
        "status": "frozen",
        "axes": {
            "contour": {"strong_min": 0.65, "none_max": 0.50},
            "interval": {"strong_min": 0.65, "none_max": 0.50},
            "rhythm": {"strong_min": 0.65, "none_max": 0.50},
        },
    }
    mapping["coverage"] = dict(base_mapping["coverage"])
    mapping["coverage"]["floor_status"] = "frozen"
    config = M3ComparisonConfig.from_registry(mapping)

    assert (
        harness._validate_frozen_axes(
            config.evidence_thresholds.axes,
            min_separation_margin=config.separation_margin.min_same_minus_cross_margin,
        )
        is True
    )
    assert harness._holdout_unlocked(config) is True


def _partially_two_axes_frozen_registry_text() -> str:
    """axes が contour/interval の 2 軸のみ凍結（rhythm は未凍結のまま欠落)。
    coverage.floor_status も frozen —— 3 軸全てが揃わなくても holdout が開く
    ケース(レビュー対応 2026-07-30 第 4 ラウンド)。
    """
    mapping = _registry_mapping()
    mapping["evidence_thresholds"] = {
        "status": "frozen",
        "axes": {
            "contour": {"strong_min": 0.8, "none_max": 0.2},
            "interval": {"strong_min": 0.8, "none_max": 0.2},
        },
    }
    mapping["coverage"] = dict(mapping["coverage"])
    mapping["coverage"]["floor_status"] = "frozen"
    return yaml.safe_dump(mapping, sort_keys=False)


def test_holdout_opens_with_only_two_axes_frozen(tmp_path: Path, audio_paths: Dict[str, str]):
    """レビュー対応 2026-07-30 第 4 ラウンド: 3 軸全てが揃わなくても、1 軸以上の
    整形済み凍結軸 + coverage.floor_status=frozen + evidence_thresholds.status=frozen
    が揃えば holdout が開く。M3c 側の `_derive_evidence`（src/ 不変）は未凍結の
    rhythm 軸を `axis_evidence` から除外するだけで fail はしない既存挙動のまま
    であることも併せて確認する。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    two_axes_registry = tmp_path / "two_axes_registry.yaml"
    two_axes_registry.write_text(_partially_two_axes_frozen_registry_text(), encoding="utf-8")
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    config, _ = harness.load_m3_registry(two_axes_registry)
    assert set(config.evidence_thresholds.axes) == {"contour", "interval"}
    assert harness._holdout_unlocked(config) is True

    report = harness.run_comparison(
        manifest_path=manifest_path, route_runner=runner, registry_path=two_axes_registry
    )

    holdout_row = report["pairs"]["p_pos_holdout"]
    assert holdout_row["split"] == "holdout"
    assert "comparison" in holdout_row
    axis_evidence = holdout_row["comparison"]["axis_evidence"]
    assert set(axis_evidence) == {"contour", "interval"}
    assert "rhythm" not in axis_evidence


# --------------------------------------------------------------------------- #
# 部分凍結 holdout の未凍結軸 redaction（レビュー対応 2026-07-30 第 6 ラウンド）
# --------------------------------------------------------------------------- #
def _partially_one_axis_frozen_registry_text() -> str:
    """axes が contour のみ凍結（interval/rhythm は未凍結）。coverage.floor_status
    も frozen —— holdout は開くが interval/rhythm は redaction 対象になるケース。
    """
    mapping = _registry_mapping()
    mapping["evidence_thresholds"] = {
        "status": "frozen",
        "axes": {
            "contour": {"strong_min": 0.8, "none_max": 0.2},
        },
    }
    mapping["coverage"] = dict(mapping["coverage"])
    mapping["coverage"]["floor_status"] = "frozen"
    return yaml.safe_dump(mapping, sort_keys=False)


def test_holdout_partial_freeze_redacts_unfrozen_axes(tmp_path: Path, audio_paths: Dict[str, str]):
    """`evidence_thresholds.axes` が contour のみ凍結された holdout run では、
    未凍結の interval/rhythm は `comparison.axes` / `axis_evidence` に serialize
    されず、`unfrozen_axes_redacted` にその軸名が明示記録される。tuning pair は
    redaction 対象外（全軸のまま）であることも併せて確認する。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    one_axis_registry = tmp_path / "one_axis_registry.yaml"
    one_axis_registry.write_text(_partially_one_axis_frozen_registry_text(), encoding="utf-8")
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    config, _ = harness.load_m3_registry(one_axis_registry)
    assert set(config.evidence_thresholds.axes) == {"contour"}
    assert harness._holdout_unlocked(config) is True

    report = harness.run_comparison(
        manifest_path=manifest_path, route_runner=runner, registry_path=one_axis_registry
    )

    holdout_row = report["pairs"]["p_pos_holdout"]
    assert holdout_row["split"] == "holdout"
    assert "comparison" in holdout_row

    axes = holdout_row["comparison"]["axes"]
    assert set(axes) == {"contour"}
    assert "interval" not in axes
    assert "rhythm" not in axes

    axis_evidence = holdout_row["comparison"]["axis_evidence"]
    assert "interval" not in axis_evidence
    assert "rhythm" not in axis_evidence

    assert holdout_row["unfrozen_axes_redacted"] == ["interval", "rhythm"]

    # tuning pair は redaction 対象外 — 全軸がそのまま残り、
    # `unfrozen_axes_redacted` キー自体が付与されない。
    tuning_row = report["pairs"]["p_pos_tuning"]
    assert set(tuning_row["comparison"]["axes"]) == {"contour", "interval", "rhythm"}
    assert "unfrozen_axes_redacted" not in tuning_row


def test_holdout_full_freeze_records_empty_redacted_list(tmp_path: Path, audio_paths: Dict[str, str]):
    """3 軸全て凍結済みの holdout run では、redaction 対象がないため
    `unfrozen_axes_redacted` は空リストとして記録される（正直会計 — 「redaction
    は起きなかった」ことも明示する）。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    frozen_registry = tmp_path / "frozen_registry.yaml"
    frozen_registry.write_text(_fully_frozen_registry_text(), encoding="utf-8")
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    report = harness.run_comparison(
        manifest_path=manifest_path, route_runner=runner, registry_path=frozen_registry
    )

    holdout_row = report["pairs"]["p_pos_holdout"]
    assert holdout_row["unfrozen_axes_redacted"] == []
    assert set(holdout_row["comparison"]["axes"]) == {"contour", "interval", "rhythm"}


# --------------------------------------------------------------------------- #
# 凍結後の holdout 検証表（レビュー対応 2026-07-30 第 10 ラウンド）
# --------------------------------------------------------------------------- #
# holdout テスト用の被覆値（floor=0.5 を上回る整形済み値。レビュー対応 2026-07-30
# 第 18 ラウンド）: `p_neg_holdout`（song_a×song_b）は実測では被覆下限ゲート
# （`comparison.py` の `min_fraction < floor`）で `not_comparable` になり、
# 実測 `coverage` は全て 0.0（`_validate_holdout_coverage_and_floor` から見れば
# floor 未満）——`evidence`/`axes` だけを "none"/明示値へ上書きして
# not_comparable でない行を装う以上、`coverage` も一貫した（floor 以上の）
# 整形済み値へ上書きしないと、新設の holdout coverage/floor 検証が「favorable
# なのに floor 未満」という(この場合は上書きに起因する見かけ上の)内部不整合を
# 正しく検出してしまう。
_HOLDOUT_TEST_COVERAGE: Dict[str, float] = {
    "aligned_note_fraction_a": 0.9,
    "aligned_note_fraction_b": 0.9,
    "phrase_coverage_a": 1.0,
    "phrase_coverage_b": 1.0,
}


def _reports_for_holdout_validation(
    tmp_path: Path,
    audio_paths: Dict[str, str],
    *,
    positive_axes_override: Dict[str, Any],
    negative_axes_override: Dict[str, Any],
) -> "Tuple[List[Dict[str, Any]], Path]":
    """全 3 軸凍結済み registry で repeats n=2 の report を作り、holdout pair
    （`p_pos_holdout`/`p_neg_holdout`）の axes を明示値へ上書きする。

    `song_a`/`song_b` の fake note ペアは compare_melodies 上で
    `not_comparable`（被覆下限ゲート不通過）になる組み合わせのため、holdout
    負例の実測値を得られない——`test_evaluate_rejects_freeze_proposal_when_
    tuning_positive_not_comparable` と同じ流儀で、テスト対象（凍結値との比較
    判定そのもの）に必要な軸値だけを直接上書きする。`coverage` も
    `_HOLDOUT_TEST_COVERAGE`（floor=0.5 を上回る整形済み値）へ揃えて上書きする
    ——`evidence`/`axes` の上書きと矛盾しない一貫した被覆値にするため（レビュー
    対応 2026-07-30 第 18 ラウンド）。両 report で同一の上書き値を与えることで
    repeats 決定論（bit 一致）は保つ。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    frozen_registry = tmp_path / "frozen_registry.yaml"
    frozen_registry.write_text(_fully_frozen_registry_text(), encoding="utf-8")
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    reports = []
    for _ in range(2):
        report = harness.run_comparison(
            manifest_path=manifest_path, route_runner=runner, registry_path=frozen_registry
        )
        report["route_runner_injected"] = False
        report["pairs"]["p_pos_holdout"]["comparison"]["evidence"] = "strong"
        report["pairs"]["p_pos_holdout"]["comparison"]["axes"] = dict(positive_axes_override)
        report["pairs"]["p_neg_holdout"]["comparison"]["evidence"] = "none"
        report["pairs"]["p_neg_holdout"]["comparison"]["axes"] = dict(negative_axes_override)
        report["pairs"]["p_neg_holdout"]["comparison"]["coverage"] = dict(_HOLDOUT_TEST_COVERAGE)
        reports.append(report)
    return reports, frozen_registry


def test_evaluate_holdout_validation_confirmed_when_within_frozen_thresholds(
    tmp_path: Path, audio_paths: Dict[str, str]
):
    """凍結済み全軸（contour/interval/rhythm）で holdout positive 最小 >=
    strong_min かつ holdout negative 最大 <= none_max を満たせば、軸ごとに
    `confirmed` が記録され、`holdout_validation_status` も `confirmed` になる。
    """
    reports, frozen_registry = _reports_for_holdout_validation(
        tmp_path,
        audio_paths,
        positive_axes_override={"contour": 0.95, "interval": 1.0, "rhythm": 0.9},
        negative_axes_override={"contour": 0.1, "interval": 0.05, "rhythm": 0.2},
    )

    verdict = harness.evaluate_comparison(reports, registry_path=frozen_registry)

    assert verdict["holdout_locked_until_frozen"] is False
    assert verdict["holdout_validation"] == {
        "contour": "confirmed",
        "interval": "confirmed",
        "rhythm": "confirmed",
    }
    assert verdict["holdout_validation_status"] == "confirmed"
    for axis in ("contour", "interval", "rhythm"):
        entry = verdict["holdout_table"][axis]
        assert entry["verdict"] == "confirmed"
        assert entry["holdout_positive_min"] is not None
        assert entry["holdout_negative_max"] is not None


def test_evaluate_holdout_validation_marks_axis_failed_when_positive_below_strong_min(
    tmp_path: Path, audio_paths: Dict[str, str]
):
    """1 軸（contour）だけ holdout positive 最小が凍結 `strong_min` 未達なら、
    その軸は `holdout_failed`（違反した側の値を `violations` に記録）となり、
    他の軸が confirmed でも verdict 全体は `calibration_not_confirmed_on_holdout`
    を主張する。
    """
    reports, frozen_registry = _reports_for_holdout_validation(
        tmp_path,
        audio_paths,
        # contour の凍結 strong_min=0.8 に対し 0.5 で未達（interval/rhythm は
        # 凍結値を満たす）。
        positive_axes_override={"contour": 0.5, "interval": 1.0, "rhythm": 0.9},
        negative_axes_override={"contour": 0.1, "interval": 0.05, "rhythm": 0.2},
    )

    verdict = harness.evaluate_comparison(reports, registry_path=frozen_registry)

    assert verdict["holdout_validation"]["contour"] == "holdout_failed"
    assert verdict["holdout_validation"]["interval"] == "confirmed"
    assert verdict["holdout_validation"]["rhythm"] == "confirmed"
    assert verdict["holdout_validation_status"] == "calibration_not_confirmed_on_holdout"

    contour_entry = verdict["holdout_table"]["contour"]
    assert contour_entry["holdout_positive_min"] == pytest.approx(0.5)
    assert contour_entry["frozen_strong_min"] == pytest.approx(0.8)
    assert any("frozen_strong_min" in violation for violation in contour_entry["violations"])


def test_evaluate_holdout_validation_not_confirmed_when_holdout_pair_not_comparable(
    tmp_path: Path, audio_paths: Dict[str, str]
):
    """holdout split に 1 件でも `not_comparable` pair があれば、他の軸別 verdict
    が全て `confirmed` でも `holdout_validation_status` は
    `calibration_not_confirmed_on_holdout` に倒れ、`holdout_validation_reasons`
    に `holdout_pair_not_measurable(<pair_id>)` が記録される（レビュー対応
    2026-07-30 第 11 ラウンド・holdout 全対測定の要求）。
    """
    manifest_path = tmp_path / "pairs.yaml"
    pairs = _default_pairs(audio_paths) + [
        {
            "pair_id": "p_extra_holdout_nc",
            "kind": "negative_cross",
            "split": "holdout",
            "audio_a": audio_paths["song_a"],
            "audio_b": audio_paths["song_b"],
            "expected": "different",
        },
    ]
    _write_manifest(manifest_path, pairs)
    frozen_registry = tmp_path / "frozen_registry.yaml"
    frozen_registry.write_text(_fully_frozen_registry_text(), encoding="utf-8")
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    reports = []
    for _ in range(2):
        report = harness.run_comparison(
            manifest_path=manifest_path, route_runner=runner, registry_path=frozen_registry
        )
        report["route_runner_injected"] = False
        report["pairs"]["p_pos_holdout"]["comparison"]["evidence"] = "strong"
        report["pairs"]["p_pos_holdout"]["comparison"]["axes"] = {
            "contour": 0.95,
            "interval": 1.0,
            "rhythm": 0.9,
        }
        report["pairs"]["p_neg_holdout"]["comparison"]["evidence"] = "none"
        report["pairs"]["p_neg_holdout"]["comparison"]["axes"] = {
            "contour": 0.1,
            "interval": 0.05,
            "rhythm": 0.2,
        }
        # レビュー対応 2026-07-30（第 18 ラウンド）: p_neg_holdout の実測 coverage
        # は被覆下限ゲートで 0.0（floor=0.5 未満）——evidence/axes を
        # not_comparable でない値へ上書きする以上、coverage も一貫した floor
        # 以上の値へ揃える（`_reports_for_holdout_validation` と同じ理由）。
        report["pairs"]["p_neg_holdout"]["comparison"]["coverage"] = dict(
            _HOLDOUT_TEST_COVERAGE
        )
        reports.append(report)

    # p_extra_holdout_nc（song_a×song_b）は観測ゲート不通過で not_comparable に
    # なる既存挙動（`test_run_then_evaluate_mechanism` の p_neg_tuning と同型）
    # ——本テストはこの not_comparable pair の存在が holdout_validation_status を
    # 倒すことが対象であって、not_comparable になる過程自体は対象外。
    assert reports[0]["pairs"]["p_extra_holdout_nc"]["comparison"]["evidence"] == "not_comparable"

    verdict = harness.evaluate_comparison(reports, registry_path=frozen_registry)

    # 軸別 verdict は全て confirmed（p_pos_holdout/p_neg_holdout の上書き値のみで
    # 判定される——not_comparable pair は axis 集計から素通りに除外されるだけ）。
    assert verdict["holdout_validation"] == {
        "contour": "confirmed",
        "interval": "confirmed",
        "rhythm": "confirmed",
    }
    # しかし not_comparable pair が 1 件存在する事実が、軸別 verdict に関わらず
    # 全体 status を「未確認」に倒す。
    assert verdict["holdout_validation_status"] == "calibration_not_confirmed_on_holdout"
    assert verdict["holdout_validation_reasons"] == [
        "holdout_pair_not_measurable(p_extra_holdout_nc)"
    ]
    assert "p_extra_holdout_nc:not_comparable" in verdict["holdout_skipped_pairs"]


def test_evaluate_holdout_validation_not_confirmed_when_comparison_missing_and_not_locked(
    tmp_path: Path, audio_paths: Dict[str, str]
):
    """holdout unlock 済み（registry 完全凍結）の下で、holdout pair 行から
    `comparison` だけを削除し `status` は `holdout_locked_until_frozen` に
    書き換えない（=ロック状態ではないのに comparison が欠落した不整合行）と、
    軸別 verdict に関わらず `holdout_validation_status` は
    `calibration_not_confirmed_on_holdout` に倒れ、`holdout_validation_reasons`
    に `holdout_pair_missing_comparison(<pair_id>)` が記録される
    （レビュー対応 2026-07-30 第 12 ラウンド・holdout 行 comparison 必須化）。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    frozen_registry = tmp_path / "frozen_registry.yaml"
    frozen_registry.write_text(_fully_frozen_registry_text(), encoding="utf-8")
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    reports = []
    for _ in range(2):
        report = harness.run_comparison(
            manifest_path=manifest_path, route_runner=runner, registry_path=frozen_registry
        )
        report["route_runner_injected"] = False
        # p_neg_holdout の comparison だけを削除する——「split」等の他フィールド
        # は残るため、locked pair の既存構造（"status" キーのみ）とは異なる。
        del report["pairs"]["p_neg_holdout"]["comparison"]
        reports.append(report)

    verdict = harness.evaluate_comparison(reports, registry_path=frozen_registry)

    assert verdict["holdout_locked_until_frozen"] is False
    assert verdict["holdout_validation_status"] == "calibration_not_confirmed_on_holdout"
    assert "holdout_pair_missing_comparison(p_neg_holdout)" in verdict["holdout_validation_reasons"]


def test_evaluate_holdout_validation_rejects_locked_status_disguise_when_unlocked(
    tmp_path: Path, audio_paths: Dict[str, str]
):
    """holdout unlock 済み（registry 完全凍結）の評価で、holdout pair 行を
    `comparison` を持たず `status: holdout_locked_until_frozen` だけを申告する
    構造（本来 holdout ロック中にのみ現れる形）に書き換えた「locked 偽装」行は、
    unlocked 評価では申告 status に関わらず `comparison` 欠落として fail 対象に
    なる（レビュー対応 2026-07-30 第 13 ラウンド: `_holdout_missing_comparison_
    pair_ids` の locked 免除は unlocked 評価では適用しない——unlocked 評価に
    `holdout_locked_until_frozen` を申告する行が現れること自体、run phase の
    意図どおりに動いていれば起こり得ないため、額面どおり信用せず一律 missing
    扱いにする）。第 12 ラウンドの実装ではこの偽装行が免除され見逃されていた。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    frozen_registry = tmp_path / "frozen_registry.yaml"
    frozen_registry.write_text(_fully_frozen_registry_text(), encoding="utf-8")
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    reports = []
    for _ in range(2):
        report = harness.run_comparison(
            manifest_path=manifest_path, route_runner=runner, registry_path=frozen_registry
        )
        report["route_runner_injected"] = False
        # p_neg_holdout の行を「holdout ロック中にのみ現れる」構造（"split" と
        # "status" のみ）へ書き換える——manifest 束縛検証は row に存在する
        # フィールドのみ照合するため、"split" を残せば pass する。
        report["pairs"]["p_neg_holdout"] = {
            "split": "holdout",
            "status": "holdout_locked_until_frozen",
        }
        reports.append(report)

    verdict = harness.evaluate_comparison(reports, registry_path=frozen_registry)

    assert verdict["holdout_locked_until_frozen"] is False
    assert verdict["holdout_validation_status"] == "calibration_not_confirmed_on_holdout"
    assert "holdout_pair_missing_comparison(p_neg_holdout)" in verdict["holdout_validation_reasons"]


def test_evaluate_holdout_validation_absent_while_holdout_locked(
    tmp_path: Path, audio_paths: Dict[str, str]
):
    """holdout がロックされたまま（uncalibrated registry）の既定 fixture では、
    holdout 行に `comparison` が存在しないため holdout 検証節を一切 emit しない
    （既存動作は不変）。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    reports = []
    for _ in range(2):
        report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
        report["route_runner_injected"] = False
        reports.append(report)

    verdict = harness.evaluate_comparison(reports)

    assert verdict["holdout_locked_until_frozen"] is True
    assert "holdout_table" not in verdict
    assert "holdout_validation" not in verdict
    assert "holdout_validation_status" not in verdict


# --------------------------------------------------------------------------- #
# 部分凍結 holdout の octave 派生診断 redaction（レビュー対応 2026-07-30 第 7 ラウンド）
# --------------------------------------------------------------------------- #
def test_holdout_partial_freeze_redacts_octave_diagnostic(tmp_path: Path):
    """`interval` 軸が未凍結の holdout run では、interval 軸の folded/raw 類似の
    乖離から機械導出される派生診断（`octave_artifact_suspected` フィールドと
    reasons 内の `octave_artifact_suspected(folded=…, raw=…)` 文字列）も
    serialize されない。contour のみ凍結した registry
    （`_partially_one_axis_frozen_registry_text`）を使い、
    `test_melody_comparison.py::test_octave_error_injection_flags_artifact` と
    同型のオクターブ誤り注入（1 ノートを +12 半音）で実際に
    `octave_artifact_suspected=True` が発生する状況を作った上で、redaction 後の
    report に痕跡が一切残らないことを確認する。
    """
    audio_c = tmp_path / "song_c.audio"
    audio_c.write_bytes(b"fake-audio-bytes:song_c")
    audio_c_octave = tmp_path / "song_c_octave.audio"
    audio_c_octave.write_bytes(b"fake-audio-bytes:song_c_octave")

    notes_c = _good_notes()
    notes_c_octave = list(notes_c)
    injected = notes_c_octave[2]
    notes_c_octave[2] = _note(injected.pitch_midi + 12, injected.start_sec, injected.end_sec)

    notes_by_content = {
        audio_c.read_bytes(): notes_c,
        audio_c_octave.read_bytes(): notes_c_octave,
    }

    def _runner(audio_path: str) -> "Tuple[MelodyObservation, Dict[str, Any]]":
        content = Path(audio_path).read_bytes()
        notes = notes_by_content[content]
        return (
            MelodyObservation(route="fake", source_model="test:fake", notes=tuple(notes)),
            {"fake_provenance": True},
        )

    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(
        manifest_path,
        [
            {
                "pair_id": "p_octave_holdout",
                "kind": "positive_transform",
                "split": "holdout",
                "audio_a": str(audio_c),
                "audio_b": str(audio_c_octave),
                "expected": "same",
            },
            # 以下は manifest 構成要件（設計 §6.1・レビュー対応 2026-07-30 第 10
            # ラウンド）を満たすための埋め合わせ pair — 同じ audio_c/audio_c_octave
            # を使い回す（このテストの主眼は holdout redaction であって、これらの
            # 埋め合わせ pair の比較結果は検証対象ではない）。
            {
                "pair_id": "p_fill_pos_tuning",
                "kind": "positive_transform",
                "split": "tuning",
                "audio_a": str(audio_c),
                "audio_b": str(audio_c_octave),
                "expected": "same",
            },
            {
                "pair_id": "p_fill_neg_tuning",
                "kind": "negative_cross",
                "split": "tuning",
                "audio_a": str(audio_c),
                "audio_b": str(audio_c_octave),
                "expected": "different",
            },
            {
                "pair_id": "p_fill_neg_rhythm",
                "kind": "negative_rhythm",
                "split": "tuning",
                "audio_a": str(audio_c),
                "audio_b": str(audio_c_octave),
                "expected": "different",
            },
            {
                "pair_id": "p_fill_neg_interval",
                "kind": "negative_interval",
                "split": "tuning",
                "audio_a": str(audio_c),
                "audio_b": str(audio_c_octave),
                "expected": "different",
            },
            {
                "pair_id": "p_fill_neg_holdout",
                "kind": "negative_cross",
                "split": "holdout",
                "audio_a": str(audio_c),
                "audio_b": str(audio_c_octave),
                "expected": "different",
            },
        ],
    )
    one_axis_registry = tmp_path / "one_axis_registry.yaml"
    one_axis_registry.write_text(_partially_one_axis_frozen_registry_text(), encoding="utf-8")

    report = harness.run_comparison(
        manifest_path=manifest_path, route_runner=_runner, registry_path=one_axis_registry
    )

    holdout_row = report["pairs"]["p_octave_holdout"]
    assert holdout_row["split"] == "holdout"
    comparison = holdout_row["comparison"]

    assert "octave_artifact_suspected" not in comparison
    assert not any(
        reason.startswith("octave_artifact_suspected(") for reason in comparison["reasons"]
    )
    assert holdout_row["unfrozen_axes_redacted"] == ["interval", "rhythm"]
    assert holdout_row["redacted_diagnostics"] == ["octave_artifact_suspected"]


# --------------------------------------------------------------------------- #
# pair 単位 pin の全欠落バイパス拒否（レビュー対応 2026-07-30 第 6 ラウンド）
# --------------------------------------------------------------------------- #
def test_evaluate_rejects_reports_with_all_pair_pins_missing(tmp_path: Path, audio_paths: Dict[str, str]):
    """全 repeat が同じ pair 単位 pin（`audio_sha256_a/b` / `route_provenance_a/b`）
    を揃って欠落させた場合、`_check_repeats_consistency` の整合性チェック
    （有無が repeats 間で揃っているか）だけでは「揃っている」ため通過してしまう
    ——publishable report 全体への必須化でこのバイパスを閉じる。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    reports = []
    for _ in range(2):
        report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
        report["route_runner_injected"] = False  # 「実測」相当（publishable）にする。
        for pair in report["pairs"].values():
            if "comparison" not in pair:
                continue
            for key in (
                "audio_sha256_a",
                "audio_sha256_b",
                "route_provenance_a",
                "route_provenance_b",
            ):
                pair.pop(key, None)
        reports.append(report)

    with pytest.raises(ValueError, match="pair 単位 pin"):
        harness.evaluate_comparison(reports)


def test_evaluate_rejects_reports_with_partial_pair_pin_missing(tmp_path: Path, audio_paths: Dict[str, str]):
    """全欠落だけでなく部分欠落（`audio_sha256_a` のみ 1 pair から削除）も
    publishable report については fail-closed で拒否する。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report["route_runner_injected"] = False
    del report["pairs"]["p_pos_tuning"]["audio_sha256_a"]

    with pytest.raises(ValueError, match="pair 単位 pin"):
        harness.evaluate_comparison([report])


def test_evaluate_allows_injected_reports_with_missing_pair_pins(tmp_path: Path, audio_paths: Dict[str, str]):
    """`route_runner_injected` な report（テスト用フェイク抽出器）は pair 単位 pin
    の必須化から除外される（現行の扱いを維持）。calibration verdict はどのみち
    `rejected_route_runner_injected` で拒否されるが、それ以前の pin 検証で
    落ちないことを確認する。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    del report["pairs"]["p_pos_tuning"]["audio_sha256_a"]
    assert report["route_runner_injected"] is True

    verdict = harness.evaluate_comparison([report])
    assert verdict["calibration_verdict_status"] == "rejected_route_runner_injected"


def test_check_repeats_consistency_rejects_mixed_holdout_lock_state(tmp_path: Path, audio_paths: Dict[str, str]):
    """同一 manifest でも registry の凍結状態が変われば holdout ロック状態が変わる
    — repeats 間でロック状態が食い違ったら fail-closed で拒否する。

    レビュー対応 2026-07-30（第 14 ラウンド）: `evidence_thresholds`/registry sha256
    が report 間で異なると tuning pair の `comparison`（`evidence`/`axis_evidence`/
    `provenance` 等）も併せて変わり、新設した comparison 全体の等値比較がそちらを
    先に検出してしまう——本テストの主眼（holdout 行の比較有無の食い違い）を単離
    するため、tuning pair の行は report_locked 側へ揃えてから比較する。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    report_locked = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)

    frozen_registry = tmp_path / "frozen_registry.yaml"
    frozen_registry.write_text(_fully_frozen_registry_text(), encoding="utf-8")
    report_unlocked = harness.run_comparison(
        manifest_path=manifest_path, route_runner=runner, registry_path=frozen_registry
    )

    # tuning pair の行は本テストの主眼でない差異(registry 変更に伴う evidence
    # 分類・provenance の registry sha256 の変化)を含むため、比較対象から単離する。
    for pair_id, pair in report_locked["pairs"].items():
        if pair["split"] == "tuning":
            report_unlocked["pairs"][pair_id] = copy.deepcopy(pair)

    with pytest.raises(ValueError, match="holdout ロック状態"):
        harness._check_repeats_consistency([report_locked, report_unlocked])


# --------------------------------------------------------------------------- #
# registry pin（sha256）の整合検証
# --------------------------------------------------------------------------- #
def test_evaluate_rejects_missing_m3_registry_sha(tmp_path: Path, audio_paths: Dict[str, str]):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report2 = copy.deepcopy(report1)
    del report2["m3_registry_sha256"]

    with pytest.raises(ValueError, match="m3_registry_sha256 が記録されていない"):
        harness.evaluate_comparison([report1, report2])


def test_evaluate_rejects_m3_registry_sha_mismatch_across_reports(tmp_path: Path, audio_paths: Dict[str, str]):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report2 = copy.deepcopy(report1)
    report2["m3_registry_sha256"] = "0" * 64

    with pytest.raises(ValueError, match=r"reports\[1\] の m3_registry_sha256"):
        harness.evaluate_comparison([report1, report2])


def test_evaluate_rejects_m3_registry_sha_mismatch_with_current_registry(tmp_path: Path, audio_paths: Dict[str, str]):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report1["m3_registry_sha256"] = "0" * 64
    report2 = copy.deepcopy(report1)  # report 間は一致させ、現在ロード registry との不一致のみ踏む。

    with pytest.raises(ValueError, match="現在ロードした registry"):
        harness.evaluate_comparison([report1, report2])


def test_evaluate_rejects_missing_m1_registry_sha_when_other_report_has_it(tmp_path: Path, audio_paths: Dict[str, str]):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report2 = copy.deepcopy(report1)
    del report2["m1_registry_sha256"]

    with pytest.raises(ValueError, match="m1_registry_sha256 が記録されて"):
        harness.evaluate_comparison([report1, report2])


def test_evaluate_rejects_m1_registry_sha_missing_from_all_reports(tmp_path: Path, audio_paths: Dict[str, str]):
    """レビュー対応 2026-07-30: 全 report が m1_registry_sha256 を欠いていても
    `any()` バイパスで検査自体をスキップしていた穴を閉じる — 全欠落でも fail-closed。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    del report1["m1_registry_sha256"]
    report2 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    del report2["m1_registry_sha256"]

    with pytest.raises(ValueError, match="m1_registry_sha256 が記録されて"):
        harness.evaluate_comparison([report1, report2])


def test_evaluate_rejects_m1_registry_sha_mismatch_with_current_registry(tmp_path: Path, audio_paths: Dict[str, str]):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report1["m1_registry_sha256"] = "0" * 64
    report2 = copy.deepcopy(report1)

    with pytest.raises(ValueError, match="m1 registry"):
        harness.evaluate_comparison([report1, report2])


# --------------------------------------------------------------------------- #
# 第一者 M3 実装コードの content pin（レビュー対応 2026-07-30 第 12 ラウンド）
# --------------------------------------------------------------------------- #
def test_run_comparison_records_m3_code_sha256(tmp_path: Path, audio_paths: Dict[str, str]):
    """`run_comparison` は `m3_code_sha256`（第一者 M3 実装コードの content pin）を
    記録し、`_m3_code_sha256()` の現在値と一致する。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)

    assert harness._is_sha256_hex(report["m3_code_sha256"])
    assert report["m3_code_sha256"] == harness._m3_code_sha256()


def test_m3_code_sha256_bound_at_import_time(tmp_path: Path, audio_paths: Dict[str, str]):
    """レビュー対応 2026-07-30（第 13 ラウンド・import 時 bind）: `run_comparison`
    が report へ記録する `m3_code_sha256` と `evaluate_comparison` が検証に使う
    現在値は、いずれも import 完了直後に一度だけ bind したモジュール定数
    `_M3_CODE_SHA256_AT_IMPORT` であり、`_m3_code_sha256()` の遅延（呼び出し
    ごとの）再計算値と一致する。
    """
    assert harness._is_sha256_hex(harness._M3_CODE_SHA256_AT_IMPORT)
    assert harness._M3_CODE_SHA256_AT_IMPORT == harness._m3_code_sha256()

    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report2 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)

    assert report1["m3_code_sha256"] == harness._M3_CODE_SHA256_AT_IMPORT
    assert report2["m3_code_sha256"] == harness._M3_CODE_SHA256_AT_IMPORT

    verdict = harness.evaluate_comparison([report1, report2])
    assert verdict["repeats_verified"] is True


def test_evaluate_rejects_missing_m3_code_sha256(tmp_path: Path, audio_paths: Dict[str, str]):
    """`m3_code_sha256` が report から欠落していれば理由つきで拒否する。"""
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report2 = copy.deepcopy(report1)
    del report2["m3_code_sha256"]

    with pytest.raises(ValueError, match="m3_code_sha256 が記録されていない"):
        harness.evaluate_comparison([report1, report2])


def test_evaluate_rejects_m3_code_sha256_mismatch_with_current_code(
    tmp_path: Path, audio_paths: Dict[str, str]
):
    """report の `m3_code_sha256` が（report 間では一致していても）evaluate 実行中
    の環境で再計算した値と食い違えば拒否する（report の値を書き換えて注入する
    ケース——M3 実装コードが run 後に変更された疑いを検出する）。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report1["m3_code_sha256"] = "0" * 64
    report2 = copy.deepcopy(report1)  # report 間は一致させ、現在計算値との不一致のみ踏む。

    with pytest.raises(ValueError, match="現在実行中の evaluate 環境"):
        harness.evaluate_comparison([report1, report2])


# --------------------------------------------------------------------------- #
# run_id の重複拒否（同一 run の二重指定検出）
# --------------------------------------------------------------------------- #
def test_evaluate_rejects_duplicate_run_id_same_report_specified_twice(
    tmp_path: Path, audio_paths: Dict[str, str]
):
    """同一 report を誤って 2 度 `--evaluate` に渡す(同一 run の二重指定)は、他の
    全構造検査(registry pin / repeats consistency)を通過してもなお `run_id` の
    相互 distinct 性検査で拒否する(レビュー対応 2026-07-30 第 4 ラウンド)。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)

    with pytest.raises(ValueError, match="run_id"):
        harness.evaluate_comparison([report, report])


def test_evaluate_rejects_missing_run_id_field(tmp_path: Path, audio_paths: Dict[str, str]):
    """`run_id` キー自体が欠落した report は理由つきで拒否する。"""
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    del report["run_id"]

    with pytest.raises(ValueError, match="run_id"):
        harness.evaluate_comparison([report])


# --------------------------------------------------------------------------- #
# 校正 run の経路制限（crepe 系限定 / melodia 常時禁止）
# --------------------------------------------------------------------------- #
def test_real_run_rejects_non_crepe_route(tmp_path: Path, audio_paths: Dict[str, str]):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))

    with pytest.raises(ValueError, match="crepe 系経路"):
        harness.run_comparison(manifest_path=manifest_path, route_name="pyin_direct")


def test_melodia_route_rejected_even_when_route_runner_injected(tmp_path: Path, audio_paths: Dict[str, str]):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    with pytest.raises(ValueError, match="melodia"):
        harness.run_comparison(
            manifest_path=manifest_path, route_name="melodia_direct", route_runner=runner
        )


def test_melodia_route_rejected_for_real_run(tmp_path: Path, audio_paths: Dict[str, str]):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))

    with pytest.raises(ValueError, match="melodia"):
        harness.run_comparison(manifest_path=manifest_path, route_name="melodia_direct")


def test_injected_run_allows_non_crepe_route(tmp_path: Path, audio_paths: Dict[str, str]):
    """route_runner 注入時(テスト)は crepe 限定を課さない — pyin_direct のような
    既存デフォルトが引き続き動くことを確認する。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    report = harness.run_comparison(
        manifest_path=manifest_path, route_name="pyin_direct", route_runner=runner
    )
    assert report["route"] == "pyin_direct"


def test_run_comparison_default_route_is_crepe_direct(tmp_path: Path, audio_paths: Dict[str, str]):
    """callable API `run_comparison` の `route_name` 既定値は `crepe_direct`
    (CLI `--route` 既定値との整合・レビュー対応 2026-07-30 第 3 ラウンド)。
    """
    import inspect

    assert inspect.signature(harness.run_comparison).parameters["route_name"].default == (
        "crepe_direct"
    )

    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    assert report["route"] == "crepe_direct"


def test_cli_default_route_is_crepe_direct(
    tmp_path: Path, monkeypatch, audio_paths: Dict[str, str]
):
    """CLI `--route` 未指定時の既定値は crepe_direct(正規の校正 run が既定で通る
    整合性回復・レビュー対応 2026-07-30)。`run_comparison` を差し替えて実際に main()
    へ渡る `route_name` を捕捉する(実抽出器を呼ばせない)。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    out_path = tmp_path / "report.json"
    captured: Dict[str, Any] = {}

    def _fake_run_comparison(*, manifest_path, route_name, route_runner=None, **kwargs):  # noqa: ANN001
        captured["route_name"] = route_name
        return {"schema_version": harness._EXPECTED_RUN_SCHEMA, "pairs": {}}

    monkeypatch.setattr(harness, "run_comparison", _fake_run_comparison)
    argv = [
        "run_melody_comparison.py",
        "--pairs",
        str(manifest_path),
        "--out",
        str(out_path),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    harness.main()

    assert captured["route_name"] == "crepe_direct"


# --------------------------------------------------------------------------- #
# evaluate phase での経路制限（レビュー対応 2026-07-30 第 9 ラウンド・run 側と対称）
# --------------------------------------------------------------------------- #
def test_evaluate_rejects_publishable_report_with_non_crepe_route(
    tmp_path: Path, audio_paths: Dict[str, str]
):
    """publishable（route_runner_injected: false）な report は evaluate phase でも
    crepe 系経路限定——route_runner 注入時のみ許される pyin_direct を publishable
    へ偽装しても、report を直接書き換えて run phase のガードをすり抜ける経路を
    evaluate 側が独立に塞ぐ。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report = harness.run_comparison(
        manifest_path=manifest_path, route_name="pyin_direct", route_runner=runner
    )
    assert report["route"] == "pyin_direct"
    report["route_runner_injected"] = False  # 「実測」相当（publishable）へ偽装。

    with pytest.raises(ValueError, match="crepe 系経路"):
        harness.evaluate_comparison([report])


def test_evaluate_rejects_melodia_route_even_when_route_runner_injected(
    tmp_path: Path, audio_paths: Dict[str, str]
):
    """melodia 系経路は route_runner_injected（テスト用フェイク抽出器）であっても
    evaluate phase で拒否する——run phase の `_validate_route_for_run` は melodia
    系経路の report をそもそも生成させないが、report を直接書き換えて `route` を
    melodia 系へ差し替える改ざんは run 側のガードをすり抜けるため、evaluate 側で
    独立に enforce する（run 側の既存規則と対称）。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    assert report["route_runner_injected"] is True
    report["route"] = "melodia_direct"

    with pytest.raises(ValueError, match="melodia"):
        harness.evaluate_comparison([report])


# --------------------------------------------------------------------------- #
# pair ラベルの manifest 束縛（レビュー対応 2026-07-30 第 7 ラウンド）
# --------------------------------------------------------------------------- #
def test_evaluate_rejects_tampered_split_holdout_to_tuning(tmp_path: Path, audio_paths: Dict[str, str]):
    """report 側で holdout pair の `split` を `tuning` へ書き換える改ざん
    （未校正の holdout 証拠を tuning のマージン計算へ混入させる攻撃）は、
    manifest 記録値との不一致として fail-closed で拒否する。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    frozen_registry = tmp_path / "frozen_registry.yaml"
    frozen_registry.write_text(_fully_frozen_registry_text(), encoding="utf-8")
    runner = _fake_route_runner(_notes_by_path(audio_paths))

    report = harness.run_comparison(
        manifest_path=manifest_path, route_runner=runner, registry_path=frozen_registry
    )
    assert report["pairs"]["p_pos_holdout"]["split"] == "holdout"
    report["pairs"]["p_pos_holdout"]["split"] = "tuning"

    with pytest.raises(ValueError, match="split"):
        harness.evaluate_comparison([report])


def test_evaluate_rejects_tampered_expected_field(tmp_path: Path, audio_paths: Dict[str, str]):
    """report 側で negative pair の `expected` を `different`→`same` へ書き換える
    改ざんは、manifest 記録値との不一致として fail-closed で拒否する。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)

    assert report["pairs"]["p_neg_tuning"]["expected"] == "different"
    report["pairs"]["p_neg_tuning"]["expected"] = "same"

    with pytest.raises(ValueError, match="expected"):
        harness.evaluate_comparison([report])


def test_evaluate_rejects_manifest_content_swap_after_run(tmp_path: Path, audio_paths: Dict[str, str]):
    """run 後に manifest ファイルの中身を書き換える（pair 構造は壊さずバイト列だけ
    変える）と、report 記録の `manifest_sha256` と evaluate 時点の実バイト sha256
    が不一致になり fail-closed で拒否する。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)

    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8") + "\n# tampered after run\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="manifest_sha256"):
        harness.evaluate_comparison([report])


# --------------------------------------------------------------------------- #
# manifest 完全集合の要求（レビュー対応 2026-07-30 第 8 ラウンド）
# --------------------------------------------------------------------------- #
def test_evaluate_rejects_report_missing_manifest_pair(tmp_path: Path, audio_paths: Dict[str, str]):
    """report から manifest の pair を 1 件削除する（部分集合化）と、欠落 pair_id
    を列挙した理由付きで fail-closed 拒否する。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)

    del report["pairs"]["p_neg_tuning"]

    with pytest.raises(ValueError) as excinfo:
        harness.evaluate_comparison([report])
    assert "欠落 pair_id" in str(excinfo.value)
    assert "p_neg_tuning" in str(excinfo.value)


def test_evaluate_rejects_report_with_excess_pair_not_in_manifest(
    tmp_path: Path, audio_paths: Dict[str, str]
):
    """manifest に存在しない pair_id を report へ追加する（過剰集合化）と
    fail-closed 拒否する（欠落チェックとは独立に、既存の per-pair ループが
    検出する）。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)

    report["pairs"]["p_extra_not_in_manifest"] = copy.deepcopy(report["pairs"]["p_neg_tuning"])

    with pytest.raises(ValueError) as excinfo:
        harness.evaluate_comparison([report])
    assert "p_extra_not_in_manifest" in str(excinfo.value)
    assert "に存在しない" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# pin 値の整形検証（レビュー対応 2026-07-30 第 8 ラウンド）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bad_value",
    [None, "", "z" * 64, "a" * 63, "A" * 64],
    ids=["null", "empty", "non_hex_char", "too_short", "uppercase"],
)
def test_evaluate_rejects_malformed_audio_sha256(
    tmp_path: Path, audio_paths: Dict[str, str], bad_value: Any
):
    """`audio_sha256_a` が null・空文字列・非 hex 文字・桁数不足・大文字混じりの
    いずれかなら、64 桁小文字 hex の整形検証で fail-closed 拒否する。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)

    report["pairs"]["p_pos_tuning"]["audio_sha256_a"] = bad_value

    with pytest.raises(ValueError, match="audio_sha256_a"):
        harness.evaluate_comparison([report])


def test_evaluate_rejects_malformed_observation_sha256(tmp_path: Path, audio_paths: Dict[str, str]):
    """`observation_sha256_a`（生観測軌跡の content hash、第 11 ラウンドで追加された
    pair 単位 pin）が非 hex 文字列（`"x"`）なら、`audio_sha256_a/b` と同じ 64 桁
    小文字 hex の整形検証で fail-closed 拒否する（レビュー対応 2026-07-30 第 16
    ラウンド: 従来はキー存在・repeats 間一致しか見ておらず、単一 report 内の
    観測軌跡 pin の書式そのものは無検査だった）。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)

    assert harness._is_sha256_hex(report["pairs"]["p_pos_tuning"]["observation_sha256_a"])
    report["pairs"]["p_pos_tuning"]["observation_sha256_a"] = "x"

    with pytest.raises(ValueError, match="observation_sha256_a"):
        harness.evaluate_comparison([report])


def test_evaluate_rejects_non_mapping_route_provenance(tmp_path: Path, audio_paths: Dict[str, str]):
    """`route_provenance_a` が mapping でない（例: 文字列）場合は fail-closed 拒否する。"""
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)

    report["pairs"]["p_pos_tuning"]["route_provenance_a"] = "not-a-mapping"

    with pytest.raises(ValueError, match="mapping"):
        harness.evaluate_comparison([report])


def test_evaluate_rejects_empty_crepe_required_pin_in_route_provenance(
    tmp_path: Path, audio_paths: Dict[str, str]
):
    """crepe 系経路（既定 route_name=crepe_direct）の `route_provenance_a` から
    必須 pin（`extractor_weights_sha256`）を空文字列にすると、非空文字列の
    整形検証で fail-closed 拒否する。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)

    assert report["route"] == "crepe_direct"
    report["pairs"]["p_pos_tuning"]["route_provenance_a"]["extractor_weights_sha256"] = ""

    with pytest.raises(ValueError, match="extractor_weights_sha256"):
        harness.evaluate_comparison([report])


def test_evaluate_rejects_null_crepe_required_pin_in_route_provenance(
    tmp_path: Path, audio_paths: Dict[str, str]
):
    """crepe 系経路の `route_provenance_b` の必須 pin（`extractor_code_sha256`）が
    null なら fail-closed 拒否する。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)

    report["pairs"]["p_pos_tuning"]["route_provenance_b"]["extractor_code_sha256"] = None

    with pytest.raises(ValueError, match="extractor_code_sha256"):
        harness.evaluate_comparison([report])


def test_evaluate_rejects_non_hex_crepe_required_pin_in_route_provenance(
    tmp_path: Path, audio_paths: Dict[str, str]
):
    """crepe 系経路の必須 pin（`extractor_weights_sha256`）が非空文字列であっても
    64 桁小文字 hex（sha256 digest の書式）でなければ fail-closed 拒否する
    （レビュー対応 2026-07-30 第 9 ラウンド: 従来は非空文字列であることしか見て
    おらず `"x"` のような非 hex 値でも素通りしていた）。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)

    assert report["route"] == "crepe_direct"
    report["pairs"]["p_pos_tuning"]["route_provenance_a"]["extractor_weights_sha256"] = "x"

    with pytest.raises(ValueError, match="extractor_weights_sha256"):
        harness.evaluate_comparison([report])


def test_evaluate_allows_non_crepe_route_without_crepe_required_pins(
    tmp_path: Path, audio_paths: Dict[str, str]
):
    """crepe 系でない経路（例: pyin_direct・route_runner 注入時のみ許容）は
    crepe 固有の必須 pin 要求を課されない — `route_provenance` が mapping で
    あることだけが検証される。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    report = harness.run_comparison(
        manifest_path=manifest_path, route_name="pyin_direct", route_runner=runner
    )
    # pyin には重み/コード pin が無い運用を模してテスト側で明示的に外す。
    del report["pairs"]["p_pos_tuning"]["route_provenance_a"]["extractor_weights_sha256"]
    del report["pairs"]["p_pos_tuning"]["route_provenance_a"]["extractor_code_sha256"]

    verdict = harness.evaluate_comparison([report])
    assert verdict["calibration_verdict_status"] == "rejected_route_runner_injected"


# --------------------------------------------------------------------------- #
# axes 値の検証（レビュー対応 2026-07-30 第 14 ラウンド）
# --------------------------------------------------------------------------- #
def _two_reports_for_calibration_verdict(
    tmp_path: Path, audio_paths: Dict[str, str]
) -> List[Dict[str, Any]]:
    """`route_runner_injected` を False に落とした 2 本の report を返す。

    calibration verdict の発行に必要な前提（route_runner 非注入相当 ×
    repeats n>=2）を満たし、margin/holdout 集計まで到達させるための共通ヘルパー
    （`test_evaluate_comparison_records_holdout_lock_when_not_route_runner_injected`
    の inline パターンを共通化）。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs(audio_paths))
    runner = _fake_route_runner(_notes_by_path(audio_paths))
    reports: List[Dict[str, Any]] = []
    for _ in range(2):
        report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
        report["route_runner_injected"] = False
        reports.append(report)
    return reports


@pytest.mark.parametrize(
    "bad_value",
    [2.0, -1.0, True, "0.5"],
    ids=["out_of_range_high", "out_of_range_low", "bool_value", "string_value"],
)
def test_evaluate_rejects_invalid_axes_value(
    tmp_path: Path, audio_paths: Dict[str, str], bad_value: Any
):
    """`comparison.axes` の値が域外(2.0/-1.0)・bool(True)・文字列("0.5")のいずれか
    なら、margin/holdout 集計より前に `_validate_pair_axes_values` が fail-closed
    で拒否する。両 report に同一の壊れた値を注入する(repeats 間の整合性チェック
    自体は通し、axes 値検証で拒否されることを見る)。
    """
    reports = _two_reports_for_calibration_verdict(tmp_path, audio_paths)
    for report in reports:
        report["pairs"]["p_pos_tuning"]["comparison"]["axes"]["contour"] = bad_value

    with pytest.raises(ValueError, match="contour"):
        harness.evaluate_comparison(reports)


def test_evaluate_rejects_unknown_axis_name(tmp_path: Path, audio_paths: Dict[str, str]):
    """`comparison.axes` に既知軸名（contour/interval/rhythm）以外のキーが
    あれば fail-closed で拒否する。
    """
    reports = _two_reports_for_calibration_verdict(tmp_path, audio_paths)
    for report in reports:
        report["pairs"]["p_pos_tuning"]["comparison"]["axes"]["bogus_axis"] = 0.5

    with pytest.raises(ValueError, match="bogus_axis"):
        harness.evaluate_comparison(reports)


def test_evaluate_accepts_none_axes_value(tmp_path: Path, audio_paths: Dict[str, str]):
    """axes 値の `None`（計測不能の既存の意味論）は axes 値検証を通過する。"""
    reports = _two_reports_for_calibration_verdict(tmp_path, audio_paths)
    for report in reports:
        report["pairs"]["p_pos_tuning"]["comparison"]["axes"]["contour"] = None

    verdict = harness.evaluate_comparison(reports)
    assert "calibration_verdict_status" not in verdict
    assert "margin_table" in verdict


# --------------------------------------------------------------------------- #
# evidence の列挙値検証 + axes との整合検証（レビュー対応 2026-07-30 第 16 ラウンド）
# --------------------------------------------------------------------------- #
def test_evaluate_rejects_unknown_evidence_value(tmp_path: Path, audio_paths: Dict[str, str]):
    """`comparison.evidence` が既知の列挙値（strong/weak/none/not_comparable）以外
    （例: `"maybe"`）なら fail-closed で拒否する。
    """
    reports = _two_reports_for_calibration_verdict(tmp_path, audio_paths)
    for report in reports:
        report["pairs"]["p_pos_tuning"]["comparison"]["evidence"] = "maybe"

    with pytest.raises(ValueError, match="evidence"):
        harness.evaluate_comparison(reports)


def test_evaluate_rejects_not_comparable_relabeled_to_none_with_axes_all_none(
    tmp_path: Path, audio_paths: Dict[str, str]
):
    """`p_neg_tuning`（観測ゲート不通過で自然に `not_comparable` になる既存挙動の
    pair）の `evidence` だけを `not_comparable` → `none` へ改変し、`axes`
    （3 軸とも `None` — `_not_comparable` の既存契約どおり）はそのまま残すと、
    `evidence != not_comparable` なのに axes が全 `None` という不整合として
    fail-closed で拒否する——`not_comparable`（測れなかった）を `none`（測った
    上で証拠なし）に偽装する改ざんの検出。
    """
    reports = _two_reports_for_calibration_verdict(tmp_path, audio_paths)
    for report in reports:
        comparison = report["pairs"]["p_neg_tuning"]["comparison"]
        assert comparison["evidence"] == "not_comparable"
        assert all(value is None for value in comparison["axes"].values())
        comparison["evidence"] = "none"

    with pytest.raises(ValueError, match="全て None"):
        harness.evaluate_comparison(reports)
