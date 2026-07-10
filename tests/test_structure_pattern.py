"""K2-seg バッチ 2 — structure 欄 grip 計器 (`svp_rpe.control.structure_pattern`) のテスト。

1. 純ロジック unit test — `scratchpad/k2seg_batch2/selftest_measure.py` の 3 ケースを
   合成 numpy 配列（決定論・音声合成なし）で再現する。
2. fixture snapshot test — `examples/control/k2_suno_segments/structure_results_fixture.json`
   / `structure_expected_grip.json` の canonical 値（canonical 22050 計測:
   match_rate 0.666667 / 0.666667、novelty_d 0.448879、null_gate_fired=境界一致
   発火）をデータ内部の整合性として pin する（測定値そのものの再解釈はしない —
   判定は plan.yaml / order_sheet の事前登録規約が担う。初回 native 48kHz 計測値
   からの差し替え経緯は fixture measurement_note 参照）。

音声合成・実抽出は使わないため slow マーカーは不要。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from svp_rpe.control import grip_effect_size
from svp_rpe.control.structure_pattern import (
    KNIFE_EDGE_MARGIN,
    pattern_match_rate,
    rms_to_db,
    section_margins,
    sign_pattern,
    split_section_rms,
)

PRESCRIBED_PATTERN = ["low", "high", "low"]

FIXTURE_PATH = Path("examples/control/k2_suno_segments/structure_results_fixture.json")
EXPECTED_GRIP_PATH = Path("examples/control/k2_suno_segments/structure_expected_grip.json")


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _load_expected_grip() -> dict:
    return json.loads(EXPECTED_GRIP_PATH.read_text(encoding="utf-8"))


def _synth_track(amplitudes: list[float], n_per_section: int = 1000) -> np.ndarray:
    """区間ごとに定振幅の決定論配列を連結する（selftest のホワイトノイズ合成の代わり
    に、乱数を排した最小構成で同じ quiet/loud 対比を再現する）。
    """
    parts = [np.full(n_per_section, amp, dtype=np.float64) for amp in amplitudes]
    return np.concatenate(parts)


# ---------------------------------------------------------------------------
# 純ロジック unit test（selftest_measure.py の 3 ケース）
# ---------------------------------------------------------------------------


def test_case_a_quiet_loud_quiet_is_full_match() -> None:
    y = _synth_track([0.1, 0.8, 0.1])
    section_rms = split_section_rms(y, 3)
    observed = sign_pattern(section_rms)

    assert observed == ["low", "high", "low"]
    assert pattern_match_rate(observed, PRESCRIBED_PATTERN) == pytest.approx(1.0)


def test_case_b_constant_amplitude_is_degenerate_not_full_match() -> None:
    y = _synth_track([0.4, 0.4, 0.4])
    section_rms = split_section_rms(y, 3)
    observed = sign_pattern(section_rms)

    # 縮退ケース: 値そのものは規定しないが、1.0（完全一致）にはならないことのみ確認する。
    assert pattern_match_rate(observed, PRESCRIBED_PATTERN) != pytest.approx(1.0)


def test_case_c_loud_quiet_loud_is_zero_match() -> None:
    y = _synth_track([0.8, 0.1, 0.8])
    section_rms = split_section_rms(y, 3)
    observed = sign_pattern(section_rms)

    assert observed == ["high", "low", "high"]
    assert pattern_match_rate(observed, PRESCRIBED_PATTERN) == pytest.approx(0.0)


def test_sign_pattern_enforces_linear_domain_arithmetic_mean() -> None:
    """事前登録規約は線形 RMS の算術平均比較 — dB 域平均（=線形域の幾何平均）とは
    別統計であることを、両定義の符号が割れる合成ケースで enforce する。

    線形 RMS [0.1, 0.32, 0.9]: 算術平均 0.44 → [low, low, high]。
    dB 域 [-20.0, -9.897, -0.915]: 平均 -10.271 → [low, high, high]（中央区間が割れる）。
    """
    rms_linear = [0.1, 0.32, 0.9]

    observed = sign_pattern(rms_linear)
    assert observed == ["low", "low", "high"]

    # dB 域平均比較なら中央区間が high になる（別統計であることの実証）。
    rms_db = [rms_to_db(value) for value in rms_linear]
    db_mean = sum(rms_db) / len(rms_db)
    db_pattern = ["high" if value >= db_mean else "low" for value in rms_db]
    assert db_pattern == ["low", "high", "high"]
    assert db_pattern != observed


def test_split_section_rms_returns_linear_rms() -> None:
    """`split_section_rms` は線形 RMS（dB でない）を返す — 定振幅なら振幅そのもの。"""
    y = _synth_track([0.1, 0.8, 0.1])
    section_rms = split_section_rms(y, 3)

    assert section_rms == pytest.approx([0.1, 0.8, 0.1])
    assert rms_to_db(section_rms[0]) == pytest.approx(-20.0, abs=1e-3)


def test_pattern_match_rate_rejects_length_mismatch() -> None:
    with pytest.raises(ValueError):
        pattern_match_rate(["low", "high"], PRESCRIBED_PATTERN)


# ---------------------------------------------------------------------------
# section_margins / KNIFE_EDGE_MARGIN（K2-seg バッチ 3 事前登録・knife-edge 計器）
# ---------------------------------------------------------------------------


def test_section_margins_computes_relative_deviation_from_arithmetic_mean() -> None:
    """[0.1, 0.8, 0.1]: 算術平均 1/3 基準の (value - mean) / mean。"""
    margins = section_margins([0.1, 0.8, 0.1])

    assert margins == pytest.approx([-0.7, 1.4, -0.7], abs=1e-6)


def test_section_margins_degenerate_zero_average_returns_all_zero() -> None:
    """全区間 0（算術平均も 0）の縮退時は安全側で全要素 0.0（ゼロ除算回避）。"""
    margins = section_margins([0.0, 0.0, 0.0])

    assert margins == [0.0, 0.0, 0.0]


def test_section_margins_rejects_empty_sequence() -> None:
    with pytest.raises(ValueError):
        section_margins([])


def test_knife_edge_margin_boundary_is_exclusive() -> None:
    """|margin| がちょうど KNIFE_EDGE_MARGIN (0.005) は非フラグ、0.0049 はフラグ。"""
    assert KNIFE_EDGE_MARGIN == pytest.approx(0.005)

    at_boundary = 0.005
    just_inside = 0.0049

    assert not (abs(at_boundary) < KNIFE_EDGE_MARGIN)
    assert abs(just_inside) < KNIFE_EDGE_MARGIN

    # 負方向も同様に境界排他的。
    assert not (abs(-at_boundary) < KNIFE_EDGE_MARGIN)
    assert abs(-just_inside) < KNIFE_EDGE_MARGIN


# ---------------------------------------------------------------------------
# fixture snapshot test（K2-seg バッチ 2 実測 fixture のデータ内部整合性）
# ---------------------------------------------------------------------------


def test_fixture_prescribed_pattern_matches_order_sheet() -> None:
    fixture = _load_fixture()
    assert fixture["prescribed_pattern"] == PRESCRIBED_PATTERN


def test_per_song_match_rate_recomputes_from_sign_pattern() -> None:
    fixture = _load_fixture()
    for cell in ("low", "high"):
        for song in fixture["songs"][cell]:
            recomputed = pattern_match_rate(song["sign_pattern"], PRESCRIBED_PATTERN)
            assert recomputed == pytest.approx(song["match_rate"], abs=1e-6), song["path"]


def test_aggregate_match_rate_equals_per_song_average() -> None:
    fixture = _load_fixture()
    cell_to_key = {"low": "match_rate_low_cell", "high": "match_rate_high_cell"}
    for cell, key in cell_to_key.items():
        songs = fixture["songs"][cell]
        mean = sum(song["match_rate"] for song in songs) / len(songs)
        assert fixture["aggregate"][key] == pytest.approx(mean, abs=1e-6)

    # canonical 値（canonical 22050 計測・設計側事前計算との照合ゲート）。
    # 初回 native 48kHz 計測は low 0.75 だったが SR 是正で差し替え済み
    # （fixture measurement_note 参照）。
    assert fixture["aggregate"]["match_rate_low_cell"] == pytest.approx(0.666667, abs=1e-6)
    assert fixture["aggregate"]["match_rate_high_cell"] == pytest.approx(0.666667, abs=1e-6)


def test_novelty_d_recomputes_with_grip_effect_size() -> None:
    fixture = _load_fixture()
    low_counts = [song["novelty_boundary_count"] for song in fixture["songs"]["low"]]
    high_counts = [song["novelty_boundary_count"] for song in fixture["songs"]["high"]]

    d = grip_effect_size(low_counts, high_counts)

    assert d == pytest.approx(fixture["aggregate"]["novelty_d"], abs=1e-4)
    # canonical 値（canonical 22050 計測・設計側検算 0.75 / 1.6708 = 0.4489 と照合）。
    # 初回 native 48kHz 計測は 0.585540 だったが SR 是正で差し替え済み。
    assert d == pytest.approx(0.448879, abs=1e-4)


def test_null_gate_fired_derives_from_high_le_low_match_rate() -> None:
    """ヌル格下げ規則の機械適用（preregistered_rule_outcome=dead）と、生成順共線に
    よる primary verdict の confounded 格下げ（PR#166 P2 第 4 ラウンド採用）を pin。
    """
    fixture = _load_fixture()
    expected = _load_expected_grip()

    high = fixture["aggregate"]["match_rate_high_cell"]
    low = fixture["aggregate"]["match_rate_low_cell"]

    assert expected["null_gate_fired"] == (high <= low)
    assert expected["null_gate_fired"] is True
    # 事前登録規約の機械適用は dead（記録保全）だが、cross-cell 比較が生成順共線と
    # 分離不能のため primary verdict は confounded・非 canonical。
    assert expected["preregistered_rule_outcome"] == "dead"
    assert expected["primary_verdict"] == "confounded"
    assert expected["verdict_canonical"] is False
    assert expected["high_cell_only_reading"] == "loose"


def test_excluded_takes_are_recorded_with_reason() -> None:
    fixture = _load_fixture()
    excluded = fixture["excluded"]

    assert {item["path"] for item in excluded} == {"high_1_1.mp3", "low_2.mp3"}
    for item in excluded:
        assert item["excluded"] is True
        assert item["duration_sec"] < 30.0
        assert "preregistered" in item["reason"]


# ---------------------------------------------------------------------------
# CLI の事前登録カットオフ執行（scripts/measure_structure_pattern.py）
# ---------------------------------------------------------------------------


def _write_wav(path: Path, duration_sec: float, sr: int = 22050, amplitude: float = 0.3) -> Path:
    """小さい合成 wav（決定論）を書き出す。実バッチ音源は使わない。

    sr は selftest と同じ 22050（chroma_cqt が低 sr では Nyquist 制約で失敗するため）。
    """
    import soundfile as sf

    rng = np.random.default_rng(20260710)
    y = (rng.standard_normal(int(duration_sec * sr)) * amplitude).astype(np.float32)
    sf.write(path, y, sr)
    return path


def test_cli_default_min_duration_is_preregistered_30s() -> None:
    """batch-2 事前登録カットオフ（order_sheet §4: <30s 除外）が CLI 既定値。"""
    from scripts.measure_structure_pattern import DEFAULT_MIN_DURATION_SEC

    assert DEFAULT_MIN_DURATION_SEC == 30.0


def test_cli_min_duration_excludes_short_takes_before_aggregation(tmp_path: Path) -> None:
    from scripts.measure_structure_pattern import build_report

    short = _write_wav(tmp_path / "short.wav", 3.0)
    long_low = _write_wav(tmp_path / "long_low.wav", 10.0)
    long_high = _write_wav(tmp_path / "long_high.wav", 10.0)

    report = build_report(
        [short, long_low], [long_high], min_duration_sec=5.0, expected_per_cell=1
    )

    # 短尺テイクは集計から外れ、excluded: 節に明示記録される。
    assert [song["path"] for song in report["songs"]["low"]] == [str(long_low)]
    assert len(report["excluded"]) == 1
    entry = report["excluded"][0]
    assert entry["path"] == str(short)
    assert entry["cell"] == "low"
    assert entry["duration_sec"] == pytest.approx(3.0, abs=0.01)
    assert entry["reason"] == "duration < 5s cutoff (preregistered)"


def test_cli_min_duration_zero_disables_cutoff(tmp_path: Path) -> None:
    from scripts.measure_structure_pattern import build_report

    short_low = _write_wav(tmp_path / "short_low.wav", 3.0)
    short_high = _write_wav(tmp_path / "short_high.wav", 3.0)

    report = build_report([short_low], [short_high], min_duration_sec=0.0, expected_per_cell=1)

    assert [song["path"] for song in report["songs"]["low"]] == [str(short_low)]
    assert report["excluded"] == []


def test_cli_fails_fast_when_cell_has_no_accepted_takes(tmp_path: Path) -> None:
    from scripts.measure_structure_pattern import main

    short_low = _write_wav(tmp_path / "short_low.wav", 3.0)
    long_high = _write_wav(tmp_path / "long_high.wav", 10.0)

    exit_code = main(
        ["--low", str(short_low), "--high", str(long_high), "--min-duration", "5.0"]
    )

    assert exit_code == 2


def test_cli_default_cutoff_applies_without_flag(tmp_path: Path) -> None:
    """--min-duration 未指定でも既定 30s カットオフが CLI 経路で執行される
    （10s テイクのみ → 全滅 fail-fast = 非ゼロ exit）。"""
    from scripts.measure_structure_pattern import main

    short_low = _write_wav(tmp_path / "short_low.wav", 10.0)
    short_high = _write_wav(tmp_path / "short_high.wav", 10.0)

    exit_code = main(["--low", str(short_low), "--high", str(short_high)])

    assert exit_code == 2


def test_cli_default_expected_per_cell_is_preregistered_repetitions() -> None:
    """batch-2 事前登録 repetitions（structure_plan.yaml: repetitions: 4）が CLI 既定値。"""
    from scripts.measure_structure_pattern import DEFAULT_EXPECTED_PER_CELL

    assert DEFAULT_EXPECTED_PER_CELL == 4


def test_cli_fails_fast_on_expected_per_cell_mismatch(tmp_path: Path) -> None:
    """カットオフ後の受入数が expected と不一致（n=1 vs expected 2）なら fail-fast。"""
    from scripts.measure_structure_pattern import main

    low = _write_wav(tmp_path / "low.wav", 10.0)
    high = _write_wav(tmp_path / "high.wav", 10.0)

    exit_code = main(
        [
            "--low", str(low),
            "--high", str(high),
            "--min-duration", "0",
            "--expected-per-cell", "2",
        ]
    )

    assert exit_code == 2


def test_cli_expected_per_cell_zero_disables_gate(tmp_path: Path) -> None:
    """--expected-per-cell 0 で明示無効化 — n=1 でも集計まで通る。"""
    from scripts.measure_structure_pattern import build_report

    low = _write_wav(tmp_path / "low.wav", 10.0)
    high = _write_wav(tmp_path / "high.wav", 10.0)

    report = build_report([low], [high], min_duration_sec=0.0, expected_per_cell=0)

    assert len(report["songs"]["low"]) == 1
    assert len(report["songs"]["high"]) == 1
    assert report["aggregate"]["match_rate_low_cell"] is not None


def _write_constant_wav(path: Path, duration_sec: float, sr: int = 22050, amplitude: float = 0.3) -> Path:
    """定振幅（乱数なし）の合成 wav — 全区間 RMS が等しい縮退ケースを作るための helper。"""
    import soundfile as sf

    y = np.full(int(duration_sec * sr), amplitude, dtype=np.float32)
    sf.write(path, y, sr)
    return path


def test_measure_song_output_carries_section_margins_and_knife_edge(tmp_path: Path) -> None:
    """CLI 出力（`measure_song`）に `section_margins` / `knife_edge_sections` が乗り、
    既存フィールド（sign_pattern / match_rate 等）は不変であること。"""
    from scripts.measure_structure_pattern import measure_song

    wav = _write_wav(tmp_path / "song.wav", 10.0)
    measurement = measure_song(wav)

    # 新規フィールドが乗る。
    assert len(measurement.section_margins) == len(measurement.sign_pattern)
    assert all(isinstance(v, float) for v in measurement.section_margins)
    assert all(isinstance(i, int) for i in measurement.knife_edge_sections)
    assert all(0 <= i < len(measurement.section_margins) for i in measurement.knife_edge_sections)

    # 既存フィールドは match_rate 定義通りのまま（margin 追加で変わらない）。
    from svp_rpe.control.structure_pattern import pattern_match_rate as _match_rate

    assert measurement.match_rate == pytest.approx(
        _match_rate(measurement.sign_pattern, PRESCRIBED_PATTERN), abs=1e-6
    )


def test_measure_song_degenerate_constant_amplitude_margins_near_zero_and_all_knife_edge(
    tmp_path: Path,
) -> None:
    """全区間同振幅（縮退）: margins ≈ 0 かつ全区間が knife-edge フラグされる。"""
    from scripts.measure_structure_pattern import measure_song

    wav = _write_constant_wav(tmp_path / "constant.wav", 9.0)
    measurement = measure_song(wav)

    assert measurement.section_margins == pytest.approx([0.0, 0.0, 0.0], abs=1e-4)
    assert measurement.knife_edge_sections == [0, 1, 2]


def test_build_report_yaml_includes_margin_fields_without_altering_existing(
    tmp_path: Path,
) -> None:
    """render_yaml 経由の CLI 出力に両フィールドが乗り、match_rate/aggregate は不変。"""
    from scripts.measure_structure_pattern import build_report, render_yaml

    low = _write_wav(tmp_path / "low.wav", 10.0)
    high = _write_wav(tmp_path / "high.wav", 10.0)

    report = build_report([low], [high], min_duration_sec=0.0, expected_per_cell=1)
    rendered = render_yaml(report)
    reparsed = yaml.safe_load(rendered)

    for cell in ("low", "high"):
        for song in reparsed["songs"][cell]:
            assert "section_margins" in song
            assert "knife_edge_sections" in song
            assert len(song["section_margins"]) == len(song["sign_pattern"])

    # 既存フィールド（aggregate / sign_pattern / match_rate）は従来どおり存在し数値も揺れない。
    assert reparsed["aggregate"]["match_rate_low_cell"] == report["aggregate"]["match_rate_low_cell"]
    assert reparsed["aggregate"]["match_rate_high_cell"] == report["aggregate"]["match_rate_high_cell"]
    assert reparsed["excluded"] == []


def test_measure_song_resamples_to_canonical_rate(tmp_path: Path, monkeypatch) -> None:
    """計測は canonical RPE 経路（load_audio の 22050 リサンプル）で行われる —
    native SR（例 44100）の波形のまま novelty を測らないことを pin する。"""
    import scripts.measure_structure_pattern as msp

    wav = _write_wav(tmp_path / "native_44100.wav", 3.0, sr=44100)

    loaded_srs: list[int] = []
    real_load_audio = msp.load_audio

    def spy(path, **kwargs):
        audio = real_load_audio(path, **kwargs)
        loaded_srs.append(audio.sr)
        return audio

    monkeypatch.setattr(msp, "load_audio", spy)
    measurement = msp.measure_song(wav)

    # 計測波形は canonical 22050 にリサンプルされている（native 44100 ではない）。
    assert loaded_srs == [22050]
    assert measurement.duration_sec == pytest.approx(3.0, abs=0.01)
