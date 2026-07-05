"""K0/K1 grip harness tests."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from scripts.measure_grip import (
    _exact_match_score,
    _key_match_score,
    analyze_fixture,
    load_fixture,
    main,
)
from svp_rpe.control import (
    GRIP_SATURATED,
    classify_grip,
    classify_match_grip,
    grip_effect_size,
    match_rate,
)

FIXTURE_PATH = Path("examples/control/k0/musicgen_rpe_fixture.json")
EXPECTED_PATH = Path("examples/control/k0/expected_grip.json")
K1_FIXTURE_PATH = Path("examples/control/k1/synth_performer_rpe_fixture.json")
K1_EXPECTED_PATH = Path("examples/control/k1/expected_grip.json")
K2_FIXTURE_PATH = Path("examples/control/k2/suno_rpe_fixture.json")
K2_EXPECTED_PATH = Path("examples/control/k2/expected_grip.json")


def test_grip_effect_size_uses_pooled_sd() -> None:
    assert grip_effect_size([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]) == pytest.approx(3.0)
    assert grip_effect_size([1.0, 2.0, 3.0, 4.0, 5.0], [2.0, 3.0, 4.0, 5.0, 6.0]) == pytest.approx(
        0.632455532
    )


def test_grip_effect_size_zero_variance_rules_are_finite() -> None:
    assert grip_effect_size([1.0, 1.0], [1.0, 1.0]) == 0.0
    assert grip_effect_size([1.0, 1.0], [2.0, 2.0]) == GRIP_SATURATED
    assert grip_effect_size([2.0, 2.0], [1.0, 1.0]) == -GRIP_SATURATED

    for value in (
        grip_effect_size([1.0, 1.0], [1.0, 1.0]),
        grip_effect_size([1.0, 1.0], [2.0, 2.0]),
        grip_effect_size([2.0, 2.0], [1.0, 1.0]),
    ):
        assert math.isfinite(value)


def test_classify_grip_thresholds_and_sign() -> None:
    assert classify_grip(0.8, expected_sign=1) == "tight"
    assert classify_grip(0.2, expected_sign=1) == "loose"
    assert classify_grip(0.199999, expected_sign=1) == "dead"
    assert classify_grip(-0.3, expected_sign=1) == "dead"
    assert classify_grip(-GRIP_SATURATED, expected_sign=-1) == "tight"
    assert classify_grip(GRIP_SATURATED, expected_sign=-1) == "dead"


def test_classify_grip_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError):
        classify_grip(1.0, expected_sign=0)
    with pytest.raises(ValueError):
        classify_grip(float("nan"), expected_sign=1)


def test_k0_fixture_snapshot() -> None:
    report = analyze_fixture(load_fixture(FIXTURE_PATH))
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))

    assert report == expected
    by_knob = {result["knob"]: result for result in report["results"]}
    assert by_knob["bpm"]["classification"] == "tight"
    assert by_knob["bpm"]["grip"] > 0.8
    assert by_knob["brightness"]["classification"] == "dead"


def test_measure_grip_cli_json_output(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["--fixture", str(FIXTURE_PATH), "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))


def test_measure_grip_cli_knob_filter(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["--fixture", str(FIXTURE_PATH), "--json", "--knob", "bpm"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert [result["knob"] for result in payload["results"]] == ["bpm"]
    assert payload["summary"] == {"tight": 1, "loose": 0, "dead": 0}


def test_match_rate_bounds_and_validation() -> None:
    assert match_rate([1.0, 0.0]) == pytest.approx(0.5)
    assert match_rate([1.0, 1.0, 1.0]) == 1.0
    with pytest.raises(ValueError):
        match_rate([1.2])
    with pytest.raises(ValueError):
        match_rate([])


def test_classify_match_grip_thresholds() -> None:
    assert classify_match_grip(0.7) == "tight"
    assert classify_match_grip(0.699999) == "loose"
    assert classify_match_grip(0.3) == "loose"
    assert classify_match_grip(0.299999) == "dead"
    with pytest.raises(ValueError):
        classify_match_grip(1.5)


def test_key_match_score_known_relations() -> None:
    assert _key_match_score("C major", "C major") == 1.0
    # mir_eval weighted score: relative minor = 0.3、無関係キーは 0.0
    assert _key_match_score("C major", "A minor") == pytest.approx(0.3)
    assert _key_match_score("C major", "F# minor") == 0.0


def test_exact_match_score_is_literal_and_normalized() -> None:
    """非 key categorical センサー用の汎用一致スコア: casefold + 空白正規化の完全一致のみ。"""
    assert _exact_match_score("4/4", "4/4") == 1.0
    assert _exact_match_score("4/4", "3/4") == 0.0
    assert _exact_match_score("3/4", " 3/4 ") == 1.0
    assert _exact_match_score("C Major", "c  major") == 1.0
    # key ファジーマッチなら部分点 0.3 が付く近縁調も、汎用スコアでは文字列不一致 = 0.0
    assert _key_match_score("C major", "A minor") == pytest.approx(0.3)
    assert _exact_match_score("C major", "A minor") == 0.0


def test_categorical_non_key_sensor_uses_exact_match_not_key_fuzzy() -> None:
    """sensor != "key" の categorical ノブは mir_eval key 経路を通らず完全一致率で採点。

    K2-seg time_signature ノブの経路: "4/4"/"3/4" を `_key_match_score` に流すと
    音楽 key として解釈されて意味を持たない。observed に「key として読めば部分点が
    付く」文字列（A minor vs C major = 0.3）を混ぜ、exact match の 0.0 として
    数えられることまで確認する。
    """
    fixture = {
        "fixture_id": "categorical_dispatch_probe",
        "repetitions": 2,
        "knobs": [
            {
                "name": "time_signature",
                "sensor": "time_signature",
                "kind": "categorical",
                "low_level": "4/4",
                "high_level": "3/4",
                "expected_sign": 0,
            }
        ],
        "samples": [
            {
                "knob": "time_signature",
                "level": "4/4",
                "features": {"time_signature": "4/4"},
            },
            {
                "knob": "time_signature",
                "level": "4/4",
                "features": {"time_signature": "3/4"},
            },
            {
                "knob": "time_signature",
                "level": "3/4",
                "features": {"time_signature": "3/4"},
            },
            {
                "knob": "time_signature",
                "level": "3/4",
                "features": {"time_signature": "4/4"},
            },
        ],
    }

    report = analyze_fixture(fixture)

    assert len(report["results"]) == 1
    result = report["results"][0]
    assert result["kind"] == "categorical"
    assert result["low_values"] == [1.0, 0.0]
    assert result["high_values"] == [1.0, 0.0]
    assert result["low_mean"] == 0.5
    assert result["high_mean"] == 0.5
    assert result["grip"] == 0.5
    assert result["classification"] == "loose"

    # key として読めば部分点 0.3 の近縁調（C major vs A minor）が、非 key センサー
    # では 0.0 になる = mir_eval key ファジーが混入していないことの直接証明
    fuzzy_probe = {
        "fixture_id": "categorical_dispatch_probe_2",
        "repetitions": 1,
        "knobs": [
            {
                "name": "mode_label",
                "sensor": "mode_label",
                "kind": "categorical",
                "low_level": "C major",
                "high_level": "A minor",
                "expected_sign": 0,
            }
        ],
        "samples": [
            {"knob": "mode_label", "level": "C major", "features": {"mode_label": "A minor"}},
            {"knob": "mode_label", "level": "A minor", "features": {"mode_label": "A minor"}},
        ],
    }
    fuzzy_report = analyze_fixture(fuzzy_probe)
    fuzzy_result = fuzzy_report["results"][0]
    assert fuzzy_result["low_values"] == [0.0]  # key ファジーなら 0.3 になるところ
    assert fuzzy_result["high_values"] == [1.0]
    assert fuzzy_result["grip"] == 0.5


def test_k1_fixture_snapshot_spans_tight_and_dead() -> None:
    """K1 代表マップ: 決定論的演奏者に対する 5 ツマミ + 補助センサーの grip 固定。"""
    report = analyze_fixture(load_fixture(K1_FIXTURE_PATH))
    expected = json.loads(K1_EXPECTED_PATH.read_text(encoding="utf-8"))

    assert report == expected
    by_knob = {result["knob"]: result for result in report["results"]}
    assert by_knob["bpm"]["classification"] == "tight"
    assert by_knob["key"]["classification"] == "tight"
    assert by_knob["key"]["kind"] == "categorical"
    # 正規センサー（spectral_centroid）では tight。legacy の帯域比センサーは
    # HF の乏しい素材で盲目になり dead に見える — 「ツマミ死」と「センサー盲」の判別例
    assert by_knob["brightness"]["classification"] == "tight"
    assert by_knob["brightness"]["sensor"] == "spectral_centroid"
    assert by_knob["brightness_band_ratio"]["classification"] == "dead"
    # 演奏者が読まないフィールド = 繋がっていないツマミは dead と検出される
    assert by_knob["active_rate_target"]["classification"] == "dead"
    assert by_knob["valley_depth_target"]["classification"] == "dead"


def test_k2_suno_fixture_snapshot_bpm_and_brightness_transfer() -> None:
    """K2 転移検証: K1 で tight だった bpm/brightness が本物 Suno でも tight に転移。

    fixture は Suno 生成 16 曲（bpm/brightness × 2 水準 × 4 反復）の抽出特徴量。
    bpm は素朴な製品センサー（既定 prior 120）でも tight（d≈1.61、真テンポでは
    さらに大きいが prior アトラクタが分離を圧縮 — docs §5.2）。brightness は
    spectral_centroid で borderline tight（d≈0.86、Suno は「明」は守るが「暗」は
    絶対 dark 帯まで落ちない非対称）。
    """
    report = analyze_fixture(load_fixture(K2_FIXTURE_PATH))
    expected = json.loads(K2_EXPECTED_PATH.read_text(encoding="utf-8"))

    assert report == expected
    by_knob = {result["knob"]: result for result in report["results"]}
    assert by_knob["bpm"]["classification"] == "tight"
    assert by_knob["bpm"]["grip"] > 0.8
    assert by_knob["brightness"]["sensor"] == "spectral_centroid"
    assert by_knob["brightness"]["classification"] == "tight"


K2_MUSICGEN_FIXTURE_PATH = Path("examples/control/k2_musicgen/fixture.json")
K2_MUSICGEN_EXPECTED_PATH = Path("examples/control/k2_musicgen/expected_grip.json")


def test_k2_musicgen_fixture_snapshot_brightness_tight_bpm_loose() -> None:
    """K2 第二機種（MusicGen PR B, 2026-07-03 実測）: fixture→grip の決定論スナップショット。

    fixture は facebook/musicgen-small ローカル生成 32 本（bpm 90/170・brightness
    dark/bright × R=8）の抽出特徴量。brightness は Suno（0.86）より強い tight
    （d≈2.25、絶対 dark 帯 ≤1200Hz へも 3/8 到達＝Suno 0/4 と対照的）。bpm は素朴
    センサーで loose（d≈0.21）だが、高 prior 再推定（start_bpm=180）で high 側
    7/8 が 172.27 に回復＝R2 の抽出器 halving が第二生成器でも再現（knob_dead では
    ない — docs/musicgen_backend.md PR B 実測）。
    """
    report = analyze_fixture(load_fixture(K2_MUSICGEN_FIXTURE_PATH))
    expected = json.loads(K2_MUSICGEN_EXPECTED_PATH.read_text(encoding="utf-8"))

    assert report == expected
    by_knob = {result["knob"]: result for result in report["results"]}
    assert by_knob["bpm"]["classification"] == "loose"
    assert by_knob["brightness"]["classification"] == "tight"
    assert by_knob["brightness"]["grip"] > 2.0
