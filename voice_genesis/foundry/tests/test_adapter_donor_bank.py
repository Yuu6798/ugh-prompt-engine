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


# --- normalize_unit_energy（追補 F1.3-B item1） ---


def _bank_with_levels(levels: list, n_bins: int = 4, unit_len: int = 20) -> db.DonorBank:
    """各 unit が指定レベル（sp の一様スカラー）を持つ合成 bank を組み立てる。"""
    n_total = unit_len * len(levels)
    sp = np.zeros((n_total, n_bins), dtype=np.float64)
    ap = np.zeros((n_total, n_bins), dtype=np.float64)
    units = []
    for i, level in enumerate(levels):
        s, e = i * unit_len, (i + 1) * unit_len
        # 単位内部にも軽い変動を持たせる（正規化がスケールのみを揃え形状は保つことの確認用）。
        base = level + 0.01 * np.arange(unit_len)[:, None]
        sp[s:e] = base
        ap[s:e] = 0.2 + 0.01 * i  # レベルと無関係な ap（正規化で不変であることの確認用）
        units.append(
            db.DonorUnit(
                index=i, start_frame=s, end_frame=e, median_f0=220.0, duration_s=unit_len * 5.0 / 1000.0,
                head_log_bands=db._log_band_vector(sp[s], db.SR),
                tail_log_bands=db._log_band_vector(sp[e - 1], db.SR),
                overlap_frames=3 + i, preutterance_frames=2 + i,
            )
        )
    return db.DonorBank(
        sr=db.SR, frame_period_ms=5.0, f0=np.full(n_total, 220.0), sp=sp, ap=ap, units=units,
        wav_sha256="dummy", source="synthetic_test", notes_csv_path=None, stats={},
    )


# --- P1 修正 (review #262): キャッシュキーの内容ハッシュ（notes CSV 編集検知） ---


def _write_mini_wav(path: Path, sr: int = 44100, dur: float = 1.0) -> None:
    import soundfile as sf

    t = np.arange(int(dur * sr)) / sr
    x = 0.3 * np.sin(2 * np.pi * 220.0 * t)
    sf.write(str(path), x, sr, subtype="PCM_16")


def test_cache_key_changes_when_notes_csv_content_edited(tmp_path: Path) -> None:
    """notes CSV のバイト内容を 1 バイトでも変えればキャッシュキーが変わる
    （同一 --cache-dir 再利用時に notes CSV を in-place 編集しても古い
    pickle/npz を返さないこと。旧実装はパス文字列のみをキー材料にしていた
    ため、この edit はキーへ反映されなかった）。"""
    notes_path = tmp_path / "notes.csv"
    notes_path.write_text("0.0,300.0,0.5\n0.5,320.0,0.3\n")
    wav_sha = "a" * 64

    key_before = db._cache_key(wav_sha, db.sha256_of(notes_path), 5.0)

    notes_path.write_text("0.0,300.0,0.5\n0.5,320.0,0.35\n")  # 1 値だけ編集
    key_after = db._cache_key(wav_sha, db.sha256_of(notes_path), 5.0)

    assert key_before != key_after


def test_build_donor_bank_cache_stale_after_notes_csv_edit(tmp_path: Path) -> None:
    """build_donor_bank を通した end-to-end 確認: notes CSV 編集後は
    cache_hit=False で再計算される（編集前の古い npz を誤って再利用しない）。"""
    wav_path = tmp_path / "mini_donor.wav"
    _write_mini_wav(wav_path)
    notes_path = tmp_path / "notes.csv"
    notes_path.write_text("0.0,300.0,0.5\n0.5,320.0,0.3\n")
    cache_dir = tmp_path / "cache"

    bank1 = db.build_donor_bank(wav_path, notes_csv_path=notes_path, cache_dir=cache_dir)
    assert bank1.stats.get("cache_hit") is False

    notes_path.write_text("0.0,300.0,0.5\n0.5,320.0,0.35\n")  # duration を編集
    bank2 = db.build_donor_bank(wav_path, notes_csv_path=notes_path, cache_dir=cache_dir)
    assert bank2.stats.get("cache_hit") is False  # 古いキャッシュを再利用しない
    cached_files = list(cache_dir.glob("donor_bank_*.npz"))
    assert len(cached_files) == 2  # 編集前後で別々のキャッシュファイル


def test_normalize_unit_energy_equalizes_unit_mean_power() -> None:
    bank = _bank_with_levels([1.0, 4.0, 9.0])
    normalized, stats = db.normalize_unit_energy(bank)
    levels = [db._unit_mean_power(normalized.sp, u) for u in normalized.units]
    # 正規化後は全 unit の平均パワーがほぼ揃う（中央値ターゲットへ収束）。
    assert levels[0] == pytest.approx(levels[1], rel=1e-9)
    assert levels[1] == pytest.approx(levels[2], rel=1e-9)
    assert stats["post_variance"] < stats["pre_variance"]
    assert stats["pre_variance"] > 0.0
    assert stats["post_variance"] == pytest.approx(0.0, abs=1e-6)


def test_normalize_unit_energy_leaves_ap_unchanged() -> None:
    bank = _bank_with_levels([1.0, 4.0, 9.0])
    normalized, _stats = db.normalize_unit_energy(bank)
    assert np.array_equal(normalized.ap, bank.ap)


def test_normalize_unit_energy_preserves_overlap_preutterance_fields() -> None:
    bank = _bank_with_levels([1.0, 4.0, 9.0])
    normalized, _stats = db.normalize_unit_energy(bank)
    assert [u.overlap_frames for u in normalized.units] == [u.overlap_frames for u in bank.units]
    assert [u.preutterance_frames for u in normalized.units] == [u.preutterance_frames for u in bank.units]


def test_normalize_unit_energy_recomputes_head_tail_log_bands() -> None:
    bank = _bank_with_levels([1.0, 9.0])
    normalized, _stats = db.normalize_unit_energy(bank)
    for u in normalized.units:
        expected_head = db._log_band_vector(normalized.sp[u.start_frame], db.SR)
        expected_tail = db._log_band_vector(normalized.sp[u.end_frame - 1], db.SR)
        assert np.allclose(u.head_log_bands, expected_head)
        assert np.allclose(u.tail_log_bands, expected_tail)


def test_normalize_unit_energy_deterministic_repeat() -> None:
    bank = _bank_with_levels([2.0, 5.0, 3.0, 8.0])
    n1, s1 = db.normalize_unit_energy(bank)
    n2, s2 = db.normalize_unit_energy(bank)
    assert np.array_equal(n1.sp, n2.sp)
    assert s1 == s2


def test_normalize_unit_energy_empty_units_is_noop() -> None:
    bank = db.DonorBank(
        sr=db.SR, frame_period_ms=5.0, f0=np.zeros(0), sp=np.zeros((0, 4)), ap=np.zeros((0, 4)),
        units=[], wav_sha256="dummy", source="synthetic_test", notes_csv_path=None, stats={},
    )
    normalized, stats = db.normalize_unit_energy(bank)
    assert normalized is bank
    assert stats["n_units"] == 0


# --- 追補 F1.4-A: VCV unit（vowel_core_start_frame あり）の正規化基準域 ---


def _vcv_bank_with_transition_and_core(
    transition_level: float, core_level: float, n_bins: int = 4, transition_len: int = 8, core_len: int = 40,
) -> db.DonorBank:
    """1 unit の中に「調音遷移（transition_level）+ 母音定常部（core_level）」を
    持つ合成 VCV bank を組み立てる（正規化が母音定常部だけを基準にすることの検証用）。
    """
    n_total = transition_len + core_len
    sp = np.concatenate(
        [np.full((transition_len, n_bins), transition_level), np.full((core_len, n_bins), core_level)], axis=0
    )
    ap = np.full((n_total, n_bins), 0.15)
    unit = db.DonorUnit(
        index=0, start_frame=0, end_frame=n_total, median_f0=220.0, duration_s=n_total * 5.0 / 1000.0,
        head_log_bands=db._log_band_vector(sp[0], db.SR), tail_log_bands=db._log_band_vector(sp[-1], db.SR),
        overlap_frames=2, preutterance_frames=6, vowel_core_start_frame=transition_len,
    )
    return db.DonorBank(
        sr=db.SR, frame_period_ms=5.0, f0=np.full(n_total, 220.0), sp=sp, ap=ap, units=[unit],
        wav_sha256="dummy", source="synthetic_test_vcv", notes_csv_path=None, stats={},
    )


def test_unit_mean_power_uses_vowel_core_only_when_present() -> None:
    bank = _vcv_bank_with_transition_and_core(transition_level=100.0, core_level=1.0)
    unit = bank.units[0]
    # 調音遷移の極端な値（100.0）に引きずられず、母音定常部（1.0）だけが平均パワーに反映される。
    level = db._unit_mean_power(bank.sp, unit)
    expected = 1.0 * bank.sp.shape[1]  # n_bins 個の 1.0 の総和
    assert level == pytest.approx(expected)


def test_normalize_unit_energy_does_not_distort_by_transition_level() -> None:
    """2 unit が同じ母音定常部レベルだが異なる調音遷移レベルを持つ場合、
    正規化後は両方ともほぼ同じゲインになる（調音遷移の極端値に引きずられない）。"""
    n_bins = 4
    core_len = 30
    trans_len = 8
    sp = np.concatenate(
        [
            np.full((trans_len, n_bins), 50.0), np.full((core_len, n_bins), 2.0),
            np.full((trans_len, n_bins), 0.01), np.full((core_len, n_bins), 2.0),
        ],
        axis=0,
    )
    ap = np.full((sp.shape[0], n_bins), 0.1)
    u0_end = trans_len + core_len
    units = [
        db.DonorUnit(
            index=0, start_frame=0, end_frame=u0_end, median_f0=220.0, duration_s=u0_end * 5.0 / 1000.0,
            head_log_bands=db._log_band_vector(sp[0], db.SR), tail_log_bands=db._log_band_vector(sp[u0_end - 1], db.SR),
            vowel_core_start_frame=trans_len,
        ),
        db.DonorUnit(
            index=1, start_frame=u0_end, end_frame=sp.shape[0], median_f0=220.0,
            duration_s=(sp.shape[0] - u0_end) * 5.0 / 1000.0,
            head_log_bands=db._log_band_vector(sp[u0_end], db.SR), tail_log_bands=db._log_band_vector(sp[-1], db.SR),
            vowel_core_start_frame=trans_len,
        ),
    ]
    bank = db.DonorBank(
        sr=db.SR, frame_period_ms=5.0, f0=np.full(sp.shape[0], 220.0), sp=sp, ap=ap, units=units,
        wav_sha256="dummy", source="synthetic_test_vcv", notes_csv_path=None, stats={},
    )
    _normalized, stats = db.normalize_unit_energy(bank)
    assert stats["gain_min"] == pytest.approx(stats["gain_max"], rel=1e-9)


def test_unit_mean_power_backward_compatible_when_vcore_none() -> None:
    """`vowel_core_start_frame=None`（旧 unit）は unit 全体を使う旧挙動のまま。"""
    bank = _bank_with_levels([1.0, 4.0])
    for u in bank.units:
        assert u.vowel_core_start_frame is None
    level0 = db._unit_mean_power(bank.sp, bank.units[0])
    assert level0 == pytest.approx(np.mean(np.sum(bank.sp[0:20], axis=1)))
