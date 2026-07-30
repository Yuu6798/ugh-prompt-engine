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


def _notes_by_path() -> Dict[str, List[MelodyNote]]:
    return {
        "song_a": _good_notes(),
        "song_a_transposed": _good_notes(shift=3),
        "song_b": _different_notes(),
    }


def _fake_route_runner(notes_by_path: Dict[str, List[MelodyNote]]) -> "harness.RouteRunner":
    def _runner(audio_path: str) -> "Tuple[MelodyObservation, Dict[str, Any]]":
        notes = notes_by_path[audio_path]
        return (
            MelodyObservation(route="fake", source_model="test:fake", notes=tuple(notes)),
            {"fake_provenance": True},
        )

    return _runner


def _write_manifest(path: Path, pairs: List[Dict[str, Any]]) -> None:
    manifest = {"schema": "m3-comparison-pairs/0.1", "pairs": pairs}
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")


def _registry_mapping() -> Dict[str, Any]:
    return yaml.safe_load(M3_REGISTRY_PATH.read_text(encoding="utf-8"))


def _partially_frozen_registry_text() -> str:
    """`evidence_thresholds.status` のみ frozen にした registry(axes なし・
    coverage.floor_status は provisional のまま)。holdout が開いてはならない
    「部分凍結」ケース。
    """
    text = M3_REGISTRY_PATH.read_text(encoding="utf-8").replace(
        "status: uncalibrated", "status: frozen", 1
    )
    assert text != M3_REGISTRY_PATH.read_text(encoding="utf-8")  # replace が効いたこと確認
    return text


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


def _default_pairs() -> List[Dict[str, Any]]:
    return [
        {
            "pair_id": "p_pos_tuning",
            "kind": "positive_transform",
            "split": "tuning",
            "audio_a": "song_a",
            "audio_b": "song_a_transposed",
            "expected": "same",
        },
        {
            "pair_id": "p_neg_tuning",
            "kind": "negative_cross",
            "split": "tuning",
            "audio_a": "song_a",
            "audio_b": "song_b",
            "expected": "different",
        },
        {
            "pair_id": "p_pos_holdout",
            "kind": "positive_transform",
            "split": "holdout",
            "audio_a": "song_a",
            "audio_b": "song_a_transposed",
            "expected": "same",
        },
    ]


# --------------------------------------------------------------------------- #
# run → evaluate の機構
# --------------------------------------------------------------------------- #
def test_run_then_evaluate_mechanism(tmp_path: Path):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())
    runner = _fake_route_runner(_notes_by_path())

    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)

    assert report["schema_version"] == harness._EXPECTED_RUN_SCHEMA
    assert report["route_runner_injected"] is True
    assert set(report["pairs"]) == {"p_pos_tuning", "p_neg_tuning", "p_pos_holdout"}
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
def test_repeats_hash_pin_bit_identical(tmp_path: Path):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())
    runner = _fake_route_runner(_notes_by_path())

    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report2 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)

    # 決定論の bit 一致を確認するだけの内部ヘルパー呼び出し(例外が出なければ pass)。
    harness._check_repeats_consistency([report1, report2])

    verdict = harness.evaluate_comparison([report1, report2])
    assert verdict["repeats_count"] == 2
    assert verdict["repeats_consistent"] is True


def test_repeats_hash_pin_rejects_tampered_report(tmp_path: Path):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())
    runner = _fake_route_runner(_notes_by_path())

    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report2 = copy.deepcopy(report1)
    # 故意に sequence_sha256 を改変する(反復間の軌跡レベル決定論を壊す)。
    report2["pairs"]["p_pos_tuning"]["comparison"]["provenance"]["sequence_sha256_a"] = "tampered"

    with pytest.raises(ValueError, match="sequence_sha256/axes"):
        harness.evaluate_comparison([report1, report2])


def test_repeats_rejects_manifest_sha256_pin_mismatch(tmp_path: Path):
    """repeats 間で `manifest_sha256`（report 全体の repeat 定義的 pin）が食い違えば
    たとえ pair 集合/axes が一致していても fail-closed で拒否する(レビュー対応 2026-07-30)。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())
    runner = _fake_route_runner(_notes_by_path())

    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report2 = copy.deepcopy(report1)
    report2["manifest_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="manifest_sha256"):
        harness._check_repeats_consistency([report1, report2])


def test_repeats_rejects_route_pin_mismatch(tmp_path: Path):
    """repeats 間で report 全体の `route` pin が食い違えば拒否する。"""
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())
    runner = _fake_route_runner(_notes_by_path())

    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report2 = copy.deepcopy(report1)
    report2["route"] = "melodia_direct"

    with pytest.raises(ValueError, match="route"):
        harness._check_repeats_consistency([report1, report2])


def test_repeats_rejects_route_provenance_pin_mismatch(tmp_path: Path):
    """pair 単位の `route_provenance_a/b`(route 由来の provenance pin)が repeats 間で
    食い違えば、sequence_sha256/axes が一致していても拒否する。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())
    runner = _fake_route_runner(_notes_by_path())

    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report2 = copy.deepcopy(report1)
    assert "route_provenance_a" in report2["pairs"]["p_pos_tuning"]
    report2["pairs"]["p_pos_tuning"]["route_provenance_a"] = {"fake_provenance": "tampered"}

    with pytest.raises(ValueError, match="route_provenance_a"):
        harness._check_repeats_consistency([report1, report2])


# --------------------------------------------------------------------------- #
# マージン計算の手計算一致
# --------------------------------------------------------------------------- #
def _synthetic_pairs_for_margin() -> Dict[str, Dict[str, Any]]:
    def _row(evidence: str, contour: Any, interval: Any, rhythm: Any, split: str, expected: str) -> Dict[str, Any]:
        return {
            "split": split,
            "expected": expected,
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
        "pos1": _row("none", 0.9, 0.8, 0.7, "tuning", "same"),
        "pos2": _row("none", 0.95, 0.85, 0.75, "tuning", "same"),
        "neg1": _row("none", 0.2, 0.1, 0.3, "tuning", "different"),
        "neg2": _row("none", 0.3, 0.2, 0.25, "tuning", "different"),
        # holdout split は除外されるべき(margin 計算に混ぜない)。
        "pos_holdout": _row("none", 0.99, 0.99, 0.99, "holdout", "same"),
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
            "comparison": {"evidence": "none", "axes": {"contour": 0.5, "interval": 0.5, "rhythm": 0.5}},
        },
        "neg1": {
            "split": "tuning",
            "expected": "different",
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
# holdout ロック
# --------------------------------------------------------------------------- #
def test_holdout_locked_until_frozen(tmp_path: Path):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())
    runner = _fake_route_runner(_notes_by_path())
    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)

    # route_runner_injected だと calibration verdict 自体を発行しないため、holdout
    # ロック機構だけを単体で検証するには内部の margin/holdout ヘルパーを直接使う。
    holdout_ids = harness._holdout_pair_ids(report["pairs"])
    assert holdout_ids == ["p_pos_holdout"]

    mapping = yaml.safe_load(M3_REGISTRY_PATH.read_text(encoding="utf-8"))
    assert mapping["evidence_thresholds"]["status"] == "uncalibrated"

    # uncalibrated の間は margin 計算からも holdout pair を除外する(tuning のみ)。
    margin = harness._margin_table(report["pairs"], split="tuning", min_margin=0.15)
    assert "p_pos_holdout" not in str(margin)  # tuning フィルタで holdout 行が混入していない


def test_evaluate_comparison_records_holdout_lock_when_not_route_runner_injected(tmp_path: Path):
    """calibration verdict が発行されるケース(route_runner 非注入相当・repeats n=2)での
    holdout ロック記録。

    `evaluate_comparison` は report 自身の `route_runner_injected` フラグだけを見て
    拒否判定するため、フラグを手動で False に落とした report（値の出所を偽装しない
    範囲でのメカニズムテスト）を 2 本（repeats n>=2 要件を満たす）用意し、holdout
    ロックの記録を確認する。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())
    runner = _fake_route_runner(_notes_by_path())
    reports_for_verdict = []
    for _ in range(2):
        report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
        report["route_runner_injected"] = False
        reports_for_verdict.append(report)

    verdict = harness.evaluate_comparison(reports_for_verdict)
    assert verdict["repeats_verified"] is True
    assert verdict["repeats_consistent"] is True
    assert verdict["holdout_locked_until_frozen"] is True
    assert verdict["holdout_pair_ids_skipped"] == ["p_pos_holdout"]
    assert "calibration_verdict_status" not in verdict
    assert "margin_table" in verdict
    assert "coverage_floor_candidate" in verdict


def test_evaluate_rejects_single_report_for_calibration_verdict(tmp_path: Path):
    """repeats n>=2 の事前登録要求（設計 §6.2）: n=1 は校正証拠として発行しない。"""
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())
    runner = _fake_route_runner(_notes_by_path())
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
def test_route_runner_injected_rejects_calibration_verdict(tmp_path: Path):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())
    runner = _fake_route_runner(_notes_by_path())
    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)

    assert report["route_runner_injected"] is True
    verdict = harness.evaluate_comparison([report])
    assert verdict["calibration_verdict_status"] == "rejected_route_runner_injected"
    assert "margin_table" not in verdict
    assert "freeze_proposal" not in verdict


def test_evaluate_rejects_missing_route_runner_injected_field(tmp_path: Path):
    """`route_runner_injected` キー自体が欠落した report は理由つきで拒否する
    (レビュー対応 2026-07-30 第 3 ラウンド: 欠落を `bool(None)` で False 扱いに
    フォールバックさせない)。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())
    runner = _fake_route_runner(_notes_by_path())
    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    del report["route_runner_injected"]

    with pytest.raises(ValueError, match="route_runner_injected"):
        harness.evaluate_comparison([report])


def test_evaluate_rejects_non_bool_route_runner_injected_field(tmp_path: Path):
    """`route_runner_injected` が bool でない(文字列/整数)場合は拒否する。"""
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())
    runner = _fake_route_runner(_notes_by_path())

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
def test_out_cannot_overwrite_pairs_manifest(tmp_path: Path, monkeypatch, capsys):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())

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


def test_out_cannot_overwrite_registry(tmp_path: Path, monkeypatch):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())

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


def test_out_cannot_overwrite_evaluate_input(tmp_path: Path, monkeypatch):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())
    runner = _fake_route_runner(_notes_by_path())
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
def test_manifest_rejects_unknown_schema(tmp_path: Path):
    manifest_path = tmp_path / "pairs.yaml"
    manifest_path.write_text(
        yaml.safe_dump({"schema": "bogus/9.9", "pairs": _default_pairs()}), encoding="utf-8"
    )
    runner = _fake_route_runner(_notes_by_path())
    with pytest.raises(ValueError, match="unsupported pairs manifest schema"):
        harness.run_comparison(manifest_path=manifest_path, route_runner=runner)


def test_manifest_rejects_duplicate_pair_id(tmp_path: Path):
    pairs = _default_pairs()
    pairs.append(dict(pairs[0]))
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, pairs)
    runner = _fake_route_runner(_notes_by_path())
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
# --out の protected-path（manifest が指す音声入力）
# --------------------------------------------------------------------------- #
def test_out_cannot_overwrite_manifest_audio_input(tmp_path: Path, monkeypatch):
    audio_a = tmp_path / "a.wav"
    audio_a.write_bytes(b"fake-audio-a")
    audio_b = tmp_path / "b.wav"
    audio_b.write_bytes(b"fake-audio-b")
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(
        manifest_path,
        [
            {
                "pair_id": "p1",
                "kind": "positive_transform",
                "split": "tuning",
                "audio_a": str(audio_a),
                "audio_b": str(audio_b),
                "expected": "same",
            }
        ],
    )

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
    _write_manifest(
        manifest_path,
        [
            {
                "pair_id": "p1",
                "kind": "positive_transform",
                "split": "tuning",
                "audio_a": str(audio_a),
                "audio_b": str(audio_b),
                "expected": "same",
            }
        ],
    )

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
def test_holdout_pair_not_opened_at_run_time_when_uncalibrated(tmp_path: Path):
    """既定 fixture registry(status=uncalibrated)では holdout pair の音声を run
    phase で一切読まず、report にも axes/hash を書かない。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())
    base_runner = _fake_route_runner(_notes_by_path())
    calls: List[str] = []

    def _tracking_runner(audio_path: str):
        calls.append(audio_path)
        return base_runner(audio_path)

    mapping = yaml.safe_load(M3_REGISTRY_PATH.read_text(encoding="utf-8"))
    assert mapping["evidence_thresholds"]["status"] == "uncalibrated"

    report = harness.run_comparison(manifest_path=manifest_path, route_runner=_tracking_runner)

    # holdout pair (p_pos_holdout) の音声は一度も runner に渡されていない。
    assert calls == ["song_a", "song_a_transposed", "song_a", "song_b"]

    holdout_row = report["pairs"]["p_pos_holdout"]
    assert holdout_row == {"split": "holdout", "status": "holdout_locked_until_frozen"}
    assert "comparison" not in holdout_row
    assert "audio_a" not in holdout_row
    assert "audio_b" not in holdout_row

    # tuning pair は通常通り比較済み。
    assert "comparison" in report["pairs"]["p_pos_tuning"]


def test_holdout_pair_compared_when_registry_frozen(tmp_path: Path):
    """holdout unlock の全条件(status=frozen + axes 揃い + coverage.floor_status=frozen)
    を満たした registry なら holdout pair も通常通り比較される。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())
    frozen_registry = tmp_path / "frozen_registry.yaml"
    frozen_registry.write_text(_fully_frozen_registry_text(), encoding="utf-8")
    runner = _fake_route_runner(_notes_by_path())

    report = harness.run_comparison(
        manifest_path=manifest_path, route_runner=runner, registry_path=frozen_registry
    )

    holdout_row = report["pairs"]["p_pos_holdout"]
    assert holdout_row["split"] == "holdout"
    assert "comparison" in holdout_row
    assert holdout_row["comparison"]["axes"]["interval"] == pytest.approx(1.0)


def test_holdout_pair_not_opened_when_partially_frozen(tmp_path: Path):
    """`evidence_thresholds.status == "frozen"` だけでは holdout は開かない —
    axes が未凍結（`_holdout_unlocked` の cross-field 不変条件 (b)）または
    `coverage.floor_status` が provisional のまま（同条件 (c)）の「部分凍結」状態は
    ロック継続が正しい。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())
    partial_registry = tmp_path / "partial_registry.yaml"
    partial_registry.write_text(_partially_frozen_registry_text(), encoding="utf-8")
    runner = _fake_route_runner(_notes_by_path())

    config, _ = harness.load_m3_registry(partial_registry)
    assert config.evidence_thresholds.status == "frozen"
    assert config.evidence_thresholds.axes is None
    assert config.coverage.floor_status != "frozen"
    assert harness._holdout_unlocked(config) is False

    report = harness.run_comparison(
        manifest_path=manifest_path, route_runner=runner, registry_path=partial_registry
    )

    holdout_row = report["pairs"]["p_pos_holdout"]
    assert holdout_row == {"split": "holdout", "status": "holdout_locked_until_frozen"}
    assert "comparison" not in holdout_row


def test_holdout_unlocked_requires_all_three_conditions():
    """`_holdout_unlocked` は status=frozen だけでは真にならない(axes/floor_status も必須)。"""
    from svp_rpe.melody.representation import M3ComparisonConfig

    base_mapping = _registry_mapping()

    partial_mapping = dict(base_mapping)
    partial_mapping["evidence_thresholds"] = {"status": "frozen"}
    partial_config = M3ComparisonConfig.from_registry(partial_mapping)
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


def test_holdout_unlocked_rejects_empty_axes_mapping():
    """axes の 3 キー自体は揃っているが値が空 mapping(`{}`)——holdout は開かない。"""
    from svp_rpe.melody.representation import M3ComparisonConfig

    base_mapping = _registry_mapping()
    mapping = dict(base_mapping)
    mapping["evidence_thresholds"] = {
        "status": "frozen",
        "axes": {"contour": {}, "interval": {}, "rhythm": {}},
    }
    mapping["coverage"] = dict(base_mapping["coverage"])
    mapping["coverage"]["floor_status"] = "frozen"
    config = M3ComparisonConfig.from_registry(mapping)

    assert harness._validate_frozen_axes(config.evidence_thresholds.axes) is False
    assert harness._holdout_unlocked(config) is False


def test_holdout_unlocked_rejects_strong_min_below_none_max():
    """`strong_min < none_max`(大小関係逆転)は holdout を開かない。"""
    from svp_rpe.melody.representation import M3ComparisonConfig

    base_mapping = _registry_mapping()
    mapping = dict(base_mapping)
    mapping["evidence_thresholds"] = {
        "status": "frozen",
        "axes": {
            "contour": {"strong_min": 0.1, "none_max": 0.8},
            "interval": {"strong_min": 0.8, "none_max": 0.2},
            "rhythm": {"strong_min": 0.7, "none_max": 0.3},
        },
    }
    mapping["coverage"] = dict(base_mapping["coverage"])
    mapping["coverage"]["floor_status"] = "frozen"
    config = M3ComparisonConfig.from_registry(mapping)

    assert harness._validate_frozen_axes(config.evidence_thresholds.axes) is False
    assert harness._holdout_unlocked(config) is False


def test_holdout_unlocked_rejects_out_of_range_axis_values():
    """`strong_min`/`none_max` が 0.0〜1.0 域外なら holdout を開かない。"""
    from svp_rpe.melody.representation import M3ComparisonConfig

    base_mapping = _registry_mapping()
    mapping = dict(base_mapping)
    mapping["evidence_thresholds"] = {
        "status": "frozen",
        "axes": {
            "contour": {"strong_min": 1.5, "none_max": 0.2},
            "interval": {"strong_min": 0.8, "none_max": 0.2},
            "rhythm": {"strong_min": 0.7, "none_max": 0.3},
        },
    }
    mapping["coverage"] = dict(base_mapping["coverage"])
    mapping["coverage"]["floor_status"] = "frozen"
    config = M3ComparisonConfig.from_registry(mapping)

    assert harness._validate_frozen_axes(config.evidence_thresholds.axes) is False
    assert harness._holdout_unlocked(config) is False


def test_holdout_unlocked_rejects_non_numeric_axis_values():
    """`strong_min`/`none_max` が非数値(bool 含む)なら holdout を開かない。"""
    from svp_rpe.melody.representation import M3ComparisonConfig

    base_mapping = _registry_mapping()
    mapping = dict(base_mapping)
    mapping["evidence_thresholds"] = {
        "status": "frozen",
        "axes": {
            "contour": {"strong_min": True, "none_max": 0.2},
            "interval": {"strong_min": 0.8, "none_max": 0.2},
            "rhythm": {"strong_min": 0.7, "none_max": 0.3},
        },
    }
    mapping["coverage"] = dict(base_mapping["coverage"])
    mapping["coverage"]["floor_status"] = "frozen"
    config = M3ComparisonConfig.from_registry(mapping)

    assert harness._validate_frozen_axes(config.evidence_thresholds.axes) is False
    assert harness._holdout_unlocked(config) is False

    mapping_str = dict(base_mapping)
    mapping_str["evidence_thresholds"] = {
        "status": "frozen",
        "axes": {
            "contour": {"strong_min": 0.8, "none_max": "0.2"},
            "interval": {"strong_min": 0.8, "none_max": 0.2},
            "rhythm": {"strong_min": 0.7, "none_max": 0.3},
        },
    }
    mapping_str["coverage"] = dict(base_mapping["coverage"])
    mapping_str["coverage"]["floor_status"] = "frozen"
    config_str = M3ComparisonConfig.from_registry(mapping_str)

    assert harness._validate_frozen_axes(config_str.evidence_thresholds.axes) is False
    assert harness._holdout_unlocked(config_str) is False


def test_check_repeats_consistency_rejects_mixed_holdout_lock_state(tmp_path: Path):
    """同一 manifest でも registry の凍結状態が変われば holdout ロック状態が変わる
    — repeats 間でロック状態が食い違ったら fail-closed で拒否する。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())
    runner = _fake_route_runner(_notes_by_path())

    report_locked = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)

    frozen_registry = tmp_path / "frozen_registry.yaml"
    frozen_registry.write_text(_fully_frozen_registry_text(), encoding="utf-8")
    report_unlocked = harness.run_comparison(
        manifest_path=manifest_path, route_runner=runner, registry_path=frozen_registry
    )

    with pytest.raises(ValueError, match="holdout ロック状態"):
        harness._check_repeats_consistency([report_locked, report_unlocked])


# --------------------------------------------------------------------------- #
# registry pin（sha256）の整合検証
# --------------------------------------------------------------------------- #
def test_evaluate_rejects_missing_m3_registry_sha(tmp_path: Path):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())
    runner = _fake_route_runner(_notes_by_path())
    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report2 = copy.deepcopy(report1)
    del report2["m3_registry_sha256"]

    with pytest.raises(ValueError, match="m3_registry_sha256 が記録されていない"):
        harness.evaluate_comparison([report1, report2])


def test_evaluate_rejects_m3_registry_sha_mismatch_across_reports(tmp_path: Path):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())
    runner = _fake_route_runner(_notes_by_path())
    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report2 = copy.deepcopy(report1)
    report2["m3_registry_sha256"] = "0" * 64

    with pytest.raises(ValueError, match=r"reports\[1\] の m3_registry_sha256"):
        harness.evaluate_comparison([report1, report2])


def test_evaluate_rejects_m3_registry_sha_mismatch_with_current_registry(tmp_path: Path):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())
    runner = _fake_route_runner(_notes_by_path())
    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report1["m3_registry_sha256"] = "0" * 64
    report2 = copy.deepcopy(report1)  # report 間は一致させ、現在ロード registry との不一致のみ踏む。

    with pytest.raises(ValueError, match="現在ロードした registry"):
        harness.evaluate_comparison([report1, report2])


def test_evaluate_rejects_missing_m1_registry_sha_when_other_report_has_it(tmp_path: Path):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())
    runner = _fake_route_runner(_notes_by_path())
    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report2 = copy.deepcopy(report1)
    del report2["m1_registry_sha256"]

    with pytest.raises(ValueError, match="m1_registry_sha256 が記録されて"):
        harness.evaluate_comparison([report1, report2])


def test_evaluate_rejects_m1_registry_sha_missing_from_all_reports(tmp_path: Path):
    """レビュー対応 2026-07-30: 全 report が m1_registry_sha256 を欠いていても
    `any()` バイパスで検査自体をスキップしていた穴を閉じる — 全欠落でも fail-closed。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())
    runner = _fake_route_runner(_notes_by_path())
    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    del report1["m1_registry_sha256"]
    report2 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    del report2["m1_registry_sha256"]

    with pytest.raises(ValueError, match="m1_registry_sha256 が記録されて"):
        harness.evaluate_comparison([report1, report2])


def test_evaluate_rejects_m1_registry_sha_mismatch_with_current_registry(tmp_path: Path):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())
    runner = _fake_route_runner(_notes_by_path())
    report1 = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    report1["m1_registry_sha256"] = "0" * 64
    report2 = copy.deepcopy(report1)

    with pytest.raises(ValueError, match="m1 registry"):
        harness.evaluate_comparison([report1, report2])


# --------------------------------------------------------------------------- #
# 校正 run の経路制限（crepe 系限定 / melodia 常時禁止）
# --------------------------------------------------------------------------- #
def test_real_run_rejects_non_crepe_route(tmp_path: Path):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())

    with pytest.raises(ValueError, match="crepe 系経路"):
        harness.run_comparison(manifest_path=manifest_path, route_name="pyin_direct")


def test_melodia_route_rejected_even_when_route_runner_injected(tmp_path: Path):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())
    runner = _fake_route_runner(_notes_by_path())

    with pytest.raises(ValueError, match="melodia"):
        harness.run_comparison(
            manifest_path=manifest_path, route_name="melodia_direct", route_runner=runner
        )


def test_melodia_route_rejected_for_real_run(tmp_path: Path):
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())

    with pytest.raises(ValueError, match="melodia"):
        harness.run_comparison(manifest_path=manifest_path, route_name="melodia_direct")


def test_injected_run_allows_non_crepe_route(tmp_path: Path):
    """route_runner 注入時(テスト)は crepe 限定を課さない — pyin_direct のような
    既存デフォルトが引き続き動くことを確認する。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())
    runner = _fake_route_runner(_notes_by_path())

    report = harness.run_comparison(
        manifest_path=manifest_path, route_name="pyin_direct", route_runner=runner
    )
    assert report["route"] == "pyin_direct"


def test_run_comparison_default_route_is_crepe_direct(tmp_path: Path):
    """callable API `run_comparison` の `route_name` 既定値は `crepe_direct`
    (CLI `--route` 既定値との整合・レビュー対応 2026-07-30 第 3 ラウンド)。
    """
    import inspect

    assert inspect.signature(harness.run_comparison).parameters["route_name"].default == (
        "crepe_direct"
    )

    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())
    runner = _fake_route_runner(_notes_by_path())

    report = harness.run_comparison(manifest_path=manifest_path, route_runner=runner)
    assert report["route"] == "crepe_direct"


def test_cli_default_route_is_crepe_direct(tmp_path: Path, monkeypatch):
    """CLI `--route` 未指定時の既定値は crepe_direct(正規の校正 run が既定で通る
    整合性回復・レビュー対応 2026-07-30)。`run_comparison` を差し替えて実際に main()
    へ渡る `route_name` を捕捉する(実抽出器を呼ばせない)。
    """
    manifest_path = tmp_path / "pairs.yaml"
    _write_manifest(manifest_path, _default_pairs())
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
