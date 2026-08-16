"""test_s1_dataprep_ph_dur_duration.py — ph_dur 合計と実 wav 長の整合検証。

`s1_poison_scan` 捜査（`results_s1/s1_record_2026-08-15.md`）で判明した「申告
ph_dur 合計が実音声長を大きく超過する」構造不良を検出/是正する2機能を検証する:

- `build_dataset.check_ph_dur_duration` / `build_dataset.py` の
  `--strict-duration` 2 段階仕様（既定 warn・fail-closed はオプトイン）
- `convert_pjs.normalize_ph_dur_to_wav_duration`（許容誤差超過行のみ比例
  縮小/拡大で正規化。許容内の行は同一オブジェクトのまま完全無変更で返す）
"""
from __future__ import annotations

import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "s1_dataprep"))

import build_dataset as bd  # noqa: E402
import convert_pjs as cp  # noqa: E402


def _write_wav(path: Path, seconds: float, sr: int = 44100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    n_frames = int(round(seconds * sr))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x00" * n_frames)


# --- wav_duration_seconds / _wav_duration_seconds --------------------------


def test_wav_duration_seconds_matches_written_length(tmp_path: Path) -> None:
    p = tmp_path / "a.wav"
    _write_wav(p, 2.5)
    assert bd.wav_duration_seconds(p) == 2.5
    assert cp._wav_duration_seconds(p) == 2.5


def test_wav_duration_seconds_missing_file_returns_none(tmp_path: Path) -> None:
    assert bd.wav_duration_seconds(tmp_path / "missing.wav") is None
    assert cp._wav_duration_seconds(tmp_path / "missing.wav") is None


# --- build_dataset.check_ph_dur_duration ------------------------------------


def test_check_ph_dur_duration_flags_gross_overshoot(tmp_path: Path) -> None:
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "seg000.wav", 3.0)
    rows = [{"name": "seg000", "ph_seq": "SP a SP", "ph_dur": "1.0 4.0 1.0"}]  # 合計6.0s vs 実3.0s
    violations = bd.check_ph_dur_duration("pjs", wav_dir, rows)
    assert len(violations) == 1
    assert "seg000" in violations[0]
    assert "6.0000s" in violations[0]
    assert "3.0000s" in violations[0]


def test_check_ph_dur_duration_within_tolerance_is_silent(tmp_path: Path) -> None:
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "seg001.wav", 3.0)
    # 合計3.02s vs 実3.0s: 差0.02s は絶対許容0.1sの範囲内 -> 違反なし
    rows = [{"name": "seg001", "ph_seq": "SP a SP", "ph_dur": "1.0 1.02 1.0"}]
    assert bd.check_ph_dur_duration("pjs", wav_dir, rows) == []


def test_check_ph_dur_duration_skips_missing_wav(tmp_path: Path) -> None:
    wav_dir = tmp_path / "wavs"
    wav_dir.mkdir()
    rows = [{"name": "nope", "ph_seq": "SP", "ph_dur": "5.0"}]
    # wav が存在しない場合は他チェック（存在確認）に譲り、ここでは検出しない
    assert bd.check_ph_dur_duration("pjs", wav_dir, rows) == []


# --- build_dataset.py main() 2段階仕様（warn既定 / --strict-duration fail） --


def _make_speaker_dir(base: Path, name: str, rows_csv: str, wav_specs: dict) -> Path:
    import csv

    d = base / name
    (d / "wavs").mkdir(parents=True)
    for wav_name, seconds in wav_specs.items():
        _write_wav(d / "wavs" / f"{wav_name}.wav", seconds)
    with open(d / "transcriptions.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "ph_seq", "ph_dur"])
        for line in rows_csv.strip("\n").split("\n"):
            writer.writerow(line.split("|"))
    return d


def test_build_dataset_main_warns_but_publishes_by_default(tmp_path: Path, capsys) -> None:
    ritsu_dir = _make_speaker_dir(
        tmp_path, "ritsu", "r000|SP a SP|1.0 1.0 1.0", {"r000": 3.0}
    )
    # pjs: 1件は正常、1件は ph_dur 過大（合計6.0 vs 実3.0）
    pjs_dir = _make_speaker_dir(
        tmp_path,
        "pjs",
        "p000|SP a SP|1.0 1.0 1.0\np001|SP a SP|1.0 4.0 1.0",
        {"p000": 3.0, "p001": 3.0},
    )
    rc = bd.main(
        [
            "--ritsu-raw-dir", str(ritsu_dir),
            "--pjs-raw-dir", str(pjs_dir),
            "--out-dict", str(tmp_path / "dict.txt"),
            "--out-config", str(tmp_path / "config.yaml"),
            "--binary-data-dir", str(tmp_path / "binary"),
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0  # 既定は warn のみで公開は継続する
    assert "warning: pjs: p001" in captured.err
    assert (tmp_path / "dict.txt").exists()  # 公開された


def test_build_dataset_main_strict_duration_fails_closed(tmp_path: Path, capsys) -> None:
    ritsu_dir = _make_speaker_dir(
        tmp_path, "ritsu", "r000|SP a SP|1.0 1.0 1.0", {"r000": 3.0}
    )
    pjs_dir = _make_speaker_dir(
        tmp_path,
        "pjs",
        "p000|SP a SP|1.0 1.0 1.0\np001|SP a SP|1.0 4.0 1.0",
        {"p000": 3.0, "p001": 3.0},
    )
    rc = bd.main(
        [
            "--ritsu-raw-dir", str(ritsu_dir),
            "--pjs-raw-dir", str(pjs_dir),
            "--out-dict", str(tmp_path / "dict.txt"),
            "--out-config", str(tmp_path / "config.yaml"),
            "--binary-data-dir", str(tmp_path / "binary"),
            "--strict-duration",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 1
    assert "problem: pjs: p001" in captured.err
    assert not (tmp_path / "dict.txt").exists()  # fail-closed: 公開されない


# --- convert_pjs.normalize_ph_dur_to_wav_duration ---------------------------


def test_normalize_ph_dur_rescales_overshooting_row(tmp_path: Path) -> None:
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "seg000.wav", 3.0)
    rows = [{"name": "seg000", "ph_seq": "SP a SP", "ph_dur": "1.0 4.0 1.0", "ph_num": "1 1 1"}]
    fixed_rows, fix_log = cp.normalize_ph_dur_to_wav_duration(rows, wav_dir)
    assert len(fix_log) == 1
    new_dur = [float(x) for x in fixed_rows[0]["ph_dur"].split()]
    assert sum(round(d, 6) for d in new_dur) == 3.0
    # 比率は元のまま保たれる (1:4:1)
    assert new_dur[0] == new_dur[2]
    assert abs(new_dur[1] / new_dur[0] - 4.0) < 1e-9
    # ph_seq/ph_num など他フィールドは無変更
    assert fixed_rows[0]["ph_seq"] == rows[0]["ph_seq"]
    assert fixed_rows[0]["ph_num"] == rows[0]["ph_num"]


def test_normalize_ph_dur_leaves_within_tolerance_row_byte_identical(tmp_path: Path) -> None:
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "seg001.wav", 3.0)
    row = {"name": "seg001", "ph_seq": "SP a SP", "ph_dur": "1.0 1.02 1.0"}
    rows = [row]
    fixed_rows, fix_log = cp.normalize_ph_dur_to_wav_duration(rows, wav_dir)
    assert fix_log == []
    assert fixed_rows[0] is row  # 無変更行は同一オブジェクトのまま返る


def test_normalize_ph_dur_mixed_batch_only_touches_violating_rows(tmp_path: Path) -> None:
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "good.wav", 3.0)
    _write_wav(wav_dir / "bad.wav", 3.0)
    good = {"name": "good", "ph_seq": "SP a SP", "ph_dur": "1.0 1.0 1.0"}
    bad = {"name": "bad", "ph_seq": "SP a SP", "ph_dur": "1.0 4.0 1.0"}
    fixed_rows, fix_log = cp.normalize_ph_dur_to_wav_duration([good, bad], wav_dir)
    assert len(fix_log) == 1
    assert "bad" in fix_log[0]
    assert fixed_rows[0] is good
    assert fixed_rows[1] is not bad
    assert sum(round(float(x), 6) for x in fixed_rows[1]["ph_dur"].split()) == 3.0
