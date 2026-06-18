"""Unit tests for the R1 corpus screening comparison logic (pure, no extraction)."""
from __future__ import annotations

from pathlib import Path

import yaml

import scripts.screen_corpus as sc


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "examples" / "sample_input"


def test_resolve_audio_anchors_relative_to_base_dir() -> None:
    base = Path("/corpus/manifests")
    # 相対パスは base_dir 基準で解決（cwd 非依存）
    assert sc.resolve_audio("take.wav", base) == base / "take.wav"
    assert sc.resolve_audio("audio/take.wav", base) == base / "audio/take.wav"
    # 絶対パスはそのまま
    assert sc.resolve_audio("/abs/take.wav", base) == Path("/abs/take.wav")
    # base_dir 無指定なら生のパス
    assert sc.resolve_audio("take.wav", None) == Path("take.wav")


def test_parse_key_combined_and_split() -> None:
    assert sc.parse_key("D minor") == ("D", "minor")
    assert sc.parse_key("E major") == ("E", "major")
    assert sc.parse_key("F#", "minor") == ("F#", "minor")
    assert sc.parse_key(None) == (None, None)


def test_pitch_class_handles_sharps_and_flats() -> None:
    assert sc.pitch_class("C") == 0
    assert sc.pitch_class("F#") == 6
    assert sc.pitch_class("Gb") == 6
    assert sc.pitch_class("Bb") == 10
    assert sc.pitch_class("H") is None


def test_bpm_relation_classes() -> None:
    # 紫電: 168 -> 172.27 = preserved (within ±4%)
    assert sc.bpm_relation(168.0, 172.27)["status"] == "preserved"
    # J-rock: 175 -> 136 = off (non-octave misdetection)
    off = sc.bpm_relation(175.0, 136.0)
    assert off["status"] == "off"
    assert off["error_pct"] == 22.3
    # clean octave errors
    assert sc.bpm_relation(170.0, 85.0)["status"] == "octave_half"
    assert sc.bpm_relation(85.0, 170.0)["status"] == "octave_double"
    assert sc.bpm_relation(168.0, None)["status"] == "no_detection"


def test_prior_recovery_classes() -> None:
    # 既定 prior で既に保存 → 回復の余地なし
    assert sc.prior_recovery("preserved", "preserved") == "n/a"
    assert sc.prior_recovery("preserved", "off") == "n/a"
    # 既定は崩壊だが高 prior で保存 → 抽出器 halving（真テンポは音源に在る）
    assert sc.prior_recovery("off", "preserved") == "recovered"
    assert sc.prior_recovery("octave_half", "preserved") == "recovered"
    # 高 prior でも非保存 → prior 偏向では説明できない（生成器不忠実 or 別要因）
    assert sc.prior_recovery("off", "off") == "not_recovered"
    assert sc.prior_recovery("octave_half", "octave_double") == "not_recovered"


def test_classify_prior_recovery_uses_raw_default_not_corrected() -> None:
    """Codex P2 (PR #85): recovery は補正前の *生* 既定 prior 推定で判定する。

    真 172 が既定 prior で 117.45 へ崩壊（subharmonic）し高 prior で 172.3 を回復する
    R2-2c/2d シナリオ: 生の default(117.45)=off・high(172.3)=preserved → "recovered"。
    もし default 側に R2-2c 補正後の 172.3 を渡すと preserved→"n/a" になり、まさに
    surface すべき halving が母数から漏れる。raw default を渡せば正しく拾えることを pin。
    """
    assert sc.classify_prior_recovery(172.0, 117.45, 172.3) == "recovered"
    # 補正後の値（172.3）を default 側に渡す回帰バグ: "n/a" になってしまう
    assert sc.classify_prior_recovery(172.0, 172.3, 172.3) == "n/a"
    # 遅い曲は既定 prior で既に正解 → 回復の余地なし
    assert sc.classify_prior_recovery(96.0, 95.7, 198.8) == "n/a"
    # 高 prior でも回復しない（octave double のまま）
    assert sc.classify_prior_recovery(172.0, 117.45, 86.0) == "not_recovered"


def test_classify_doubling_recovery_uses_low_prior_with_same_recovery_rule() -> None:
    # 既定 prior が倍速側を選ぶが低 prior で stated を回復 → 抽出器 doubling
    assert sc.classify_doubling_recovery(60.0, 117.45, 60.09) == "recovered"
    # 既定 prior で既に保存 → 低 prior が崩れても回復余地なし
    assert sc.classify_doubling_recovery(120.0, 123.05, 60.09) == "n/a"
    # 低 prior でも stated を保存しない → not_recovered
    assert sc.classify_doubling_recovery(60.0, 117.45, 118.0) == "not_recovered"
    # halving ケースを doubling と取り違えない
    assert sc.classify_doubling_recovery(172.0, 89.1, 86.0) == "not_recovered"


def test_key_relation_classes() -> None:
    assert sc.key_relation("D", "minor", "D", "minor") == "preserved"
    # J-rock: E major stated, D major detected = off (whole step, not a clean relation)
    assert sc.key_relation("E", "major", "D", "major") == "off"
    # parallel: same root different mode
    assert sc.key_relation("D", "major", "D", "minor") == "parallel"
    # relative: C major <-> A minor
    assert sc.key_relation("C", "major", "A", "minor") == "relative"
    assert sc.key_relation("A", "minor", "C", "major") == "relative"
    assert sc.key_relation(None, None, "C", "major") == "unknown"


def test_aggregate_base_rates_and_unflagged() -> None:
    rows = [
        {
            "id": "ok",
            "detected": {"bpm_octave_ambiguous": False},
            "bpm_relation": {"status": "preserved", "ratio": 1.0},
            "bpm_prior_recovery": "n/a",
            "bpm_doubling_prior_recovery": "n/a",
            "key_relation": "preserved",
        },
        {
            "id": "bad",
            "detected": {"bpm_octave_ambiguous": False},
            "bpm_relation": {"status": "off", "ratio": 0.78},
            "bpm_prior_recovery": "recovered",
            "bpm_doubling_prior_recovery": "recovered",
            "key_relation": "off",
        },
    ]
    summary = sc.aggregate(rows)
    assert summary["n_songs"] == 2
    assert summary["bpm_preservation_rate"] == 0.5
    assert summary["key_preservation_rate"] == 0.5
    # 非保存 bpm 誤差で octave_ambiguous=False のものが surfaced（"off" も含む総称）
    assert summary["bpm_errors_unflagged"] == ["bad"]
    # 高 prior で回復した崩壊（抽出器 halving）が母数として分離される
    assert summary["bpm_halving_prior_recoverable"] == ["bad"]
    # 低 prior で回復した崩壊（抽出器 doubling）も追加キーで分離される
    assert summary["bpm_doubling_prior_recoverable"] == ["bad"]


def test_build_report_marks_only_slow_pad_as_doubling_prior_recovered(tmp_path: Path) -> None:
    source_rows = yaml.safe_load((SAMPLE_DIR / "ground_truth.yaml").read_text(encoding="utf-8"))
    songs = [
        {
            "id": row["id"],
            "audio": str(SAMPLE_DIR / row["filename"]),
            "bpm": row["bpm"],
            "key": row["key"],
            "mode": row["mode"],
            "time_signature": row["time_signature"],
        }
        for row in source_rows
    ]
    manifest = tmp_path / "screen_ground_truth.yaml"
    manifest.write_text(yaml.safe_dump({"songs": songs}, sort_keys=False), encoding="utf-8")

    report = sc.build_report(manifest)
    rows = {row["id"]: row for row in report["rows"]}

    assert (
        rows["synth_01_slow_pad_c_major"]["bpm_doubling_prior_recovery"] == "recovered"
    )
    for sample_id in [
        "synth_02_minor_pulse_a_minor",
        "synth_03_mid_groove_g_major",
        "synth_04_waltz_fsharp_minor",
        "synth_05_fast_bright_d_major",
    ]:
        assert rows[sample_id]["bpm_doubling_prior_recovery"] == "n/a"
    assert rows["synth_01_slow_pad_c_major"]["detected"]["bpm_low_prior"] is not None
    assert report["summary"]["bpm_doubling_prior_recoverable"] == [
        "synth_01_slow_pad_c_major"
    ]

    markdown = sc.render_markdown(report)
    assert "lp bpm" in markdown
    assert "dbl-recov" in markdown


def test_audio_hash_ok(tmp_path: Path) -> None:
    from svp_rpe.perform import sha256_bytes

    f = tmp_path / "take.wav"
    f.write_bytes(b"abc123")
    digest = sha256_bytes(b"abc123")
    assert sc.audio_hash_ok(f, None) is True            # pin 無し = 常に許容
    assert sc.audio_hash_ok(f, digest) is True          # 一致
    assert sc.audio_hash_ok(f, "deadbeef") is False     # 不一致
    assert sc.audio_hash_ok(tmp_path / "missing.wav", digest) is False  # 欠損


def test_screen_song_marks_hash_mismatch_untrusted_without_extraction(tmp_path: Path) -> None:
    # 不一致 hash の行は抽出せず untrusted（早期 return が extract を回避）。
    f = tmp_path / "take.wav"
    f.write_bytes(b"not the pinned bytes")
    song = {"id": "stale", "audio": str(f), "bpm": 120, "key": "C major",
            "audio_sha256": "0" * 64}
    row = sc.screen_song(song)
    assert row["provenance"] == "sha256_mismatch"
    assert row["detected"] is None
    assert row["bpm_relation"]["status"] == "untrusted"
    # untrusted 行は保存率の母数から除外される
    summary = sc.aggregate([row])
    assert summary["untrusted"] == ["stale"]
    assert summary["bpm_preservation_rate"] == 0.0
    assert summary["key_preservation_rate"] == 0.0
