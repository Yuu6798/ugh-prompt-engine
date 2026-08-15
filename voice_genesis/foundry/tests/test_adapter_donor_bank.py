"""test_adapter_donor_bank.py — 単位切り出し境界（notes CSV 主経路 + energy valley
フォールバック）の検証。合成ミニドナーで高速・実 vocadito 非依存。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapter"))

import numpy as np
import pytest

import donor_bank as db


def test_load_notes_csv_sorted(tmp_path: Path) -> None:
    p = tmp_path / "notes.csv"
    p.write_text("1.0,300.0,0.5\n0.2,250.0,0.3\n2.0,320.0,0.4\n")
    rows = db.load_notes_csv(p)
    assert [r[0] for r in rows] == [0.2, 1.0, 2.0]


def test_units_from_notes_boundaries_and_drop_short() -> None:
    frame_period_ms = 5.0
    # 3 notes: 通常長 / 通常長 / 短すぎ (3 フレーム未満 = 15ms 未満)
    notes = [
        (0.0, 300.0, 0.5),   # 0 -> 100 frames
        (0.5, 320.0, 0.3),   # 100 -> 160 frames
        (0.8, 310.0, 0.005),  # 160 -> 161 frames (1フレーム、MIN_UNIT_FRAMES_ABS=3未満で drop)
    ]
    n_donor_frames = 200
    boundaries, stats = db.units_from_notes(notes, n_donor_frames, frame_period_ms)
    assert boundaries == [(0, 100), (100, 160)]
    assert stats["n_notes_total"] == 3
    assert stats["n_units_kept"] == 2
    assert stats["n_dropped_short"] == 1


def test_units_from_notes_clips_to_donor_range() -> None:
    notes = [(0.0, 300.0, 10.0)]  # 遥かに donor 長を超える duration
    boundaries, stats = db.units_from_notes(notes, n_donor_frames=50, frame_period_ms=5.0)
    assert boundaries == [(0, 50)]
    assert stats["n_clipped_to_donor_range"] == 1


def test_units_from_energy_valleys_short_run_kept_as_is() -> None:
    # 短い有声区間（100ms = 20 frames、250ms 未満）はそのまま 1 unit で残る
    n = 40
    f0 = np.zeros(n)
    f0[5:25] = 220.0  # 20 frames 有声
    sp = np.ones((n, 8))
    boundaries, stats = db.units_from_energy_valleys(f0, sp, frame_period_ms=5.0)
    assert boundaries == [(5, 25)]
    assert stats["n_short_runs_under_250ms"] == 1


def test_units_from_energy_valleys_splits_long_run() -> None:
    # 3.0s = 600 frames の有声区間 -> MAX_UNIT_SEC(1.2s=240frames) を超えるため分割される
    frame_period_ms = 5.0
    n = 700
    f0 = np.zeros(n)
    f0[50:650] = 220.0  # 600 frames 有声 (3.0s)
    sp = np.ones((n, 8)) * 10.0
    # エネルギー谷を人工的に用意（谷=低エネルギー地点）: 中央付近を谷にする
    sp[350] = 0.001
    boundaries, stats = db.units_from_energy_valleys(f0, sp, frame_period_ms)
    assert len(boundaries) >= 2
    max_frames = int(round(db.MAX_UNIT_SEC / (frame_period_ms / 1000.0)))
    for s, e in boundaries:
        assert (e - s) <= max_frames
    # 境界はすべて元の run 内に収まり、連続して元の区間を覆う
    assert boundaries[0][0] == 50
    assert boundaries[-1][1] == 650
    for i in range(1, len(boundaries)):
        assert boundaries[i][0] == boundaries[i - 1][1]
    assert stats["n_split_ops"] >= 1


def test_units_from_energy_valleys_multiple_disjoint_runs() -> None:
    n = 100
    f0 = np.zeros(n)
    f0[10:30] = 200.0
    f0[50:80] = 210.0
    sp = np.ones((n, 8))
    boundaries, stats = db.units_from_energy_valleys(f0, sp, frame_period_ms=5.0)
    assert boundaries == [(10, 30), (50, 80)]
    assert stats["n_voiced_runs"] == 2


def test_build_units_median_f0_and_log_bands() -> None:
    n = 20
    n_bins = 16
    f0 = np.zeros(n)
    f0[2:10] = 220.0
    f0[3] = 440.0  # 外れ値: median は影響を受けにくい
    sp = np.abs(np.sin(np.linspace(0, 10, n * n_bins))).reshape(n, n_bins) + 0.1
    ap = np.full((n, n_bins), 0.2)
    donor = dict(f0=f0, sp=sp, ap=ap)
    units = db._build_units(donor, [(2, 10)], sr=24000, frame_period_ms=5.0)
    assert len(units) == 1
    u = units[0]
    assert u.start_frame == 2 and u.end_frame == 10
    assert u.duration_s == pytest.approx(8 * 5.0 / 1000.0)
    assert u.median_f0 == pytest.approx(float(np.median(f0[2:10])))
    assert u.head_log_bands.shape == (db.N_LOG_BANDS,)
    assert u.tail_log_bands.shape == (db.N_LOG_BANDS,)
    assert np.all(np.isfinite(u.head_log_bands))


def test_build_donor_bank_end_to_end_with_synthetic_wav(tmp_path: Path) -> None:
    """load_donor_24k (sr=44100 前提) から build_donor_bank までの結合動作確認。

    実 vocadito は使わず、44.1kHz の合成トーンを donor wav として与える。
    """
    import soundfile as sf

    sr = 44100
    dur = 2.0
    t = np.arange(int(dur * sr)) / sr
    f0_hz = 220.0 + 20.0 * np.sin(2 * np.pi * 0.5 * t)
    x = 0.3 * np.sin(2 * np.pi * np.cumsum(f0_hz) / sr)
    wav_path = tmp_path / "mini_donor.wav"
    sf.write(str(wav_path), x, sr, subtype="PCM_16")

    bank = db.build_donor_bank(wav_path, notes_csv_path=None, cache_dir=None)
    assert bank.source == "energy_valley_fallback"
    assert bank.sr == db.SR
    assert len(bank.units) >= 1
    assert bank.wav_sha256 == db.sha256_of(wav_path)

    # 決定論: 同一入力で再構築しても unit 境界が一致する
    bank2 = db.build_donor_bank(wav_path, notes_csv_path=None, cache_dir=None)
    assert [(u.start_frame, u.end_frame) for u in bank.units] == [
        (u.start_frame, u.end_frame) for u in bank2.units
    ]


def test_build_donor_bank_missing_wav_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        db.build_donor_bank(tmp_path / "nope.wav")


def test_build_donor_bank_cache_roundtrip(tmp_path: Path) -> None:
    import soundfile as sf

    sr = 44100
    t = np.arange(int(1.0 * sr)) / sr
    x = 0.3 * np.sin(2 * np.pi * 220.0 * t)
    wav_path = tmp_path / "mini_donor.wav"
    sf.write(str(wav_path), x, sr, subtype="PCM_16")
    cache_dir = tmp_path / "cache"

    bank1 = db.build_donor_bank(wav_path, cache_dir=cache_dir)
    assert bank1.stats.get("cache_hit") is False
    cached_files = list(cache_dir.glob("donor_bank_*.npz"))
    assert len(cached_files) == 1

    bank2 = db.build_donor_bank(wav_path, cache_dir=cache_dir)
    assert bank2.stats.get("cache_hit") is True
    assert np.allclose(bank1.f0, bank2.f0)
    assert np.allclose(bank1.sp, bank2.sp)
    assert [(u.start_frame, u.end_frame) for u in bank1.units] == [
        (u.start_frame, u.end_frame) for u in bank2.units
    ]
