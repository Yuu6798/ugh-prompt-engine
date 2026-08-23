"""test_adapter_donor_bank.py — 単位切り出し境界（notes CSV 主経路 + energy valley
フォールバック）の検証。合成ミニドナーで高速・実 vocadito 非依存。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapter"))

import numpy as np
import pytest

from _optional_runtime_stubs import optional_runtime_available, stub_pyworld_if_missing

with stub_pyworld_if_missing():
    import donor_bank as db

requires_pyworld = pytest.mark.skipif(
    not optional_runtime_available("pyworld"), reason="pyworld is not installed"
)


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


@requires_pyworld
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


@requires_pyworld
def test_build_donor_bank_reads_wav_and_notes_csv_exactly_once(tmp_path: Path, monkeypatch) -> None:
    """P2 修正 (review #262 R3): wav / notes CSV とも、ハッシュと decode/parse の
    両方に同一 read 結果を使う（split-read を直接 enforce。measure_bands.py の
    `test_analyze_wav_reads_file_bytes_exactly_once` と同じ流儀）。
    """
    import soundfile as sf

    sr = 44100
    dur = 1.0
    t = np.arange(int(dur * sr)) / sr
    x = 0.3 * np.sin(2 * np.pi * 220.0 * t)
    wav_path = tmp_path / "mini_donor.wav"
    sf.write(str(wav_path), x, sr, subtype="PCM_16")
    notes_path = tmp_path / "notes.csv"
    notes_path.write_text("0.0,220.0,1.0\n")

    counts: dict = {}
    orig_read_bytes = Path.read_bytes

    def _counting_read_bytes(self):
        key = str(self.resolve())
        counts[key] = counts.get(key, 0) + 1
        return orig_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _counting_read_bytes)
    db.build_donor_bank(wav_path, notes_csv_path=notes_path, cache_dir=None)

    assert counts.get(str(wav_path.resolve())) == 1
    assert counts.get(str(notes_path.resolve())) == 1


def test_build_donor_bank_missing_wav_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        db.build_donor_bank(tmp_path / "nope.wav")


@requires_pyworld
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


@requires_pyworld
def test_build_donor_bank_cache_hit_preserves_stats(tmp_path: Path) -> None:
    """review #262 R9 (`r3789495247`): npz キャッシュヒット時に `bank.stats` の
    `n_units_kept`/`n_dropped_short`/`n_clipped_to_donor_range` 等が初回ビルドと
    一致することを直接検証する（旧実装は `cache_hit=True` のみの dict へ
    差し替えられ、他フィールドが丸ごと消えていた）。"""
    import soundfile as sf

    sr = 44100
    t = np.arange(int(1.0 * sr)) / sr
    x = 0.3 * np.sin(2 * np.pi * 220.0 * t)
    wav_path = tmp_path / "mini_donor.wav"
    sf.write(str(wav_path), x, sr, subtype="PCM_16")
    cache_dir = tmp_path / "cache"

    bank1 = db.build_donor_bank(wav_path, cache_dir=cache_dir)
    bank2 = db.build_donor_bank(wav_path, cache_dir=cache_dir)

    stats1 = dict(bank1.stats)
    stats2 = dict(bank2.stats)
    stats1.pop("cache_hit")
    stats2.pop("cache_hit")
    assert stats1 == stats2  # cache_hit 以外は完全一致
    assert stats2  # 空 dict へ縮退していないことも確認
    assert bank1.stats.get("cache_hit") is False
    assert bank2.stats.get("cache_hit") is True


# --- P2 修正 (review #262 R5・`r3789400805`): donor cache の atomic 公開 ---
# （`atomic_pickle_dump`/`atomic_savez` は donor_bank.py に共通実装され、
#   donor_bank_utau.py（v1/v2）・donor_bank_lab.py・本ファイルの npz cache
#   すべてが利用する。§ render.py `_atomic_write_wav` と同じテスト流儀）。


def test_atomic_savez_writes_readable_content_no_tmp_residue(tmp_path: Path) -> None:
    path = tmp_path / "bank.npz"
    db.atomic_savez(path, a=np.arange(5), b=np.array(["x"]))
    assert path.exists()
    z = np.load(path, allow_pickle=False)
    assert list(z["a"]) == [0, 1, 2, 3, 4]
    assert list(z["b"]) == ["x"]
    assert list(tmp_path.glob("bank.npz.*.tmp")) == []


def test_atomic_savez_no_partial_file_on_failure(tmp_path: Path, monkeypatch) -> None:
    """AGENTS.md Persistent Artifact Safety Gate 項目7「公開途中失敗の注入
    テスト」: `np.savez` が失敗しても最終 path に部分成果物を残さない。"""
    path = tmp_path / "bank.npz"

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated savez failure")

    monkeypatch.setattr(db.np, "savez", _boom)
    with pytest.raises(RuntimeError):
        db.atomic_savez(path, a=np.arange(3))
    assert not path.exists()
    assert list(tmp_path.glob("bank.npz.*.tmp")) == []


def test_atomic_savez_does_not_clobber_existing_cache_on_failure(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "bank.npz"
    db.atomic_savez(path, a=np.arange(3))
    before_bytes = path.read_bytes()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated savez failure")

    monkeypatch.setattr(db.np, "savez", _boom)
    with pytest.raises(RuntimeError):
        db.atomic_savez(path, a=np.arange(9))
    assert path.read_bytes() == before_bytes  # 旧キャッシュが無傷のまま残る
    z = np.load(path, allow_pickle=False)
    assert list(z["a"]) == [0, 1, 2]  # 破損 npz ではなく正常に読み戻せる


def test_atomic_pickle_dump_writes_readable_content_no_tmp_residue(tmp_path: Path) -> None:
    path = tmp_path / "bank.pkl"
    db.atomic_pickle_dump({"x": 1, "y": [1, 2, 3]}, path)
    assert path.exists()
    import pickle

    with open(path, "rb") as f:
        assert pickle.load(f) == {"x": 1, "y": [1, 2, 3]}
    assert list(tmp_path.glob("bank.pkl.*.tmp")) == []


def test_atomic_pickle_dump_no_partial_file_on_failure(tmp_path: Path, monkeypatch) -> None:
    """公開途中失敗の注入テスト: `pickle.dump` が失敗しても最終 path に
    破損 pickle を残さない（review #262 R5 `r3789400805` の実害シナリオ
    ——旧実装は直書きだったため、中断時に破損ファイルが残り
    `cache_path.exists()` が真のまま以後の全リクエストが読み込み失敗し
    続けた）。
    """
    path = tmp_path / "bank.pkl"

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated pickle.dump failure")

    monkeypatch.setattr(db.pickle, "dump", _boom)
    with pytest.raises(RuntimeError):
        db.atomic_pickle_dump({"x": 1}, path)
    assert not path.exists()  # cache_path.exists() が真になり続ける実害が再発しない
    assert list(tmp_path.glob("bank.pkl.*.tmp")) == []


def test_atomic_pickle_dump_does_not_clobber_existing_cache_on_failure(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "bank.pkl"
    db.atomic_pickle_dump({"x": 1}, path)
    before_bytes = path.read_bytes()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated pickle.dump failure")

    monkeypatch.setattr(db.pickle, "dump", _boom)
    with pytest.raises(RuntimeError):
        db.atomic_pickle_dump({"x": 2}, path)
    assert path.read_bytes() == before_bytes  # 旧キャッシュが無傷のまま残る


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


@requires_pyworld
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


# ---------------------------------------------------------------------------
# P2 修正 (review #262 R2): aggregate_content_hash はパスと hash を結合する
# ---------------------------------------------------------------------------


def test_aggregate_content_hash_deterministic_order_independent() -> None:
    pairs_a = [("b/file2", "hash2"), ("a/file1", "hash1")]
    pairs_b = [("a/file1", "hash1"), ("b/file2", "hash2")]
    assert db.aggregate_content_hash(pairs_a) == db.aggregate_content_hash(pairs_b)


def test_aggregate_content_hash_detects_content_swap_between_same_named_paths() -> None:
    """2 ファイルが中身（sha256）を入れ替えても、パスと結合せずに hash 集合
    だけを sorted 連結すると集約ダイジェストが不変になってしまう（旧実装の
    provenance 破損。review #262 R2 P2 指摘）。パス結合後は検知できる。"""
    before = [("a.wav", "hash_A"), ("b.wav", "hash_B")]
    after_swapped = [("a.wav", "hash_B"), ("b.wav", "hash_A")]
    assert db.aggregate_content_hash(before) != db.aggregate_content_hash(after_swapped)


def test_aggregate_content_hash_changes_when_path_changes_but_hash_set_same() -> None:
    """hash の集合が同じでも、どちらのパスがどの hash かが変われば別ダイジェスト
    になる（上のスワップテストと相補的な検証）。"""
    a = [("x.wav", "h1"), ("y.wav", "h2")]
    b = [("x.wav", "h2"), ("y.wav", "h1")]
    assert db.aggregate_content_hash(a) != db.aggregate_content_hash(b)


def test_aggregate_content_hash_empty() -> None:
    # 空でもエラーにならず安定した値を返す（決定論・空集合の hash）。
    assert db.aggregate_content_hash([]) == db.aggregate_content_hash([])


# ---------------------------------------------------------------------------
# P2 修正 (review #262 R13): reject_paths_outside_root — symlink 脱出の共通拒否
# ---------------------------------------------------------------------------


def test_reject_paths_outside_root_accepts_paths_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    p = root / "sub" / "file.wav"
    p.write_bytes(b"x")
    db.reject_paths_outside_root([p], root, source_label="test")  # no raise


def test_reject_paths_outside_root_missing_path_inside_root_does_not_raise(tmp_path: Path) -> None:
    """未作成（分析前に probe する _song.wav 等）でも lexical に root 配下なら通す
    （strict=False resolve）。"""
    root = tmp_path / "root"
    root.mkdir()
    missing = root / "sub" / "not_yet_written.wav"
    db.reject_paths_outside_root([missing], root, source_label="test")  # no raise


def test_reject_paths_outside_root_rejects_symlinked_dir_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file.lab").write_text("x")
    (root / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="root 外"):
        db.reject_paths_outside_root(
            [root / "linked" / "file.lab"], root, source_label="test"
        )


def test_reject_paths_outside_root_lists_violation_in_message(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)
    bad = root / "linked" / "escaped.wav"

    with pytest.raises(ValueError) as excinfo:
        db.reject_paths_outside_root([bad], root, source_label="test-source")
    msg = str(excinfo.value)
    assert "test-source" in msg
    assert "escaped.wav" in msg
