"""test_s1_dataprep_ph_dur_duration.py — ph_dur 合計と実 wav 長の整合検証。

`s1_poison_scan` 捜査（`results_s1/s1_record_2026-08-15.md`）で判明した「申告
ph_dur 合計が実音声長を大きく超過する」構造不良を検出/是正する2機能を検証する:

- `build_dataset.check_ph_dur_duration` / `build_dataset.py` の
  `--strict-duration` 2 段階仕様（既定 warn・fail-closed はオプトイン）
- `convert_pjs.normalize_ph_dur_to_wav_duration`（許容誤差超過 overshoot 行
  のみ末尾切り詰めで正規化。undershoot 行は fail-closed で違反収集し無変更
  のまま返す — review #264 R5。許容内の行は同一オブジェクトのまま完全
  無変更で返す）
- `convert_pjs.normalize_ph_dur_to_wav_duration`: ph_dur が非数/空/非有限
  （nan/inf）の行を `malformed_ph_dur_violations` へ収集して fail-closed
  拒否し、同じ行の name 安全性検査・missing/unreadable WAV 検査を短絡
  しない（review #264 R17 P2, convert_pjs.py:456。旧実装は ph_dur 側の
  異常が先に continue すると独立な後続検査が丸ごとスキップされ、
  `name="../../escape", ph_dur="nan"` のような行がどの violation にも
  記録されず通過し得た）
"""
from __future__ import annotations

import json
import os
import sys
import wave
from pathlib import Path

import pytest

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


# --- build_dataset.check_ph_dur_duration: パストラバーサル fail-closed
# (review #264 R4 P2) -------------------------------------------------------


def test_check_ph_dur_duration_does_not_open_path_traversal_name(tmp_path: Path) -> None:
    """`row["name"]` が `../../outside/secret` のような wav_dir 外を指す名前
    でも、`check_ph_dur_duration` は `validate_speaker` の安全 name 判定より
    前に WAV を open してはならない（open してしまうと外部ファイル内容が
    duration 検査経由で読み取られる）。安全でない name は黙ってスキップし、
    violation を報告しない（unsafe name の problems 昇格は `validate_speaker`
    側の責務）。"""
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    # wav_dir 側の申告 ph_dur と大きく乖離した長さの secret wav
    # （もし open されていれば violation として検出されてしまうはずの内容）。
    _write_wav(outside_dir / "secret.wav", 10.0)

    raw_dir = tmp_path / "pjs"
    wav_dir = raw_dir / "wavs"
    wav_dir.mkdir(parents=True)

    rows = [{"name": "../../outside/secret", "ph_seq": "SP a SP", "ph_dur": "1.0 1.0 1.0"}]
    assert bd.check_ph_dur_duration("pjs", wav_dir, rows) == []
    # validate_speaker 側は同じ名前を unsafe として検出し、fail-closed に乗せる。
    problems = bd.validate_speaker("pjs", raw_dir, rows)
    assert any("unsafe name" in p for p in problems)


def test_check_ph_dur_duration_does_not_open_symlink_escape(tmp_path: Path) -> None:
    """wav_dir 配下の symlink が wav_dir 外の実ファイルへ逃げている場合も、
    resolve 後のパスが wav_dir 配下に収まらない限り open しない。"""
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    _write_wav(outside_dir / "secret.wav", 10.0)

    raw_dir = tmp_path / "pjs"
    wav_dir = raw_dir / "wavs"
    wav_dir.mkdir(parents=True)
    (wav_dir / "evil.wav").symlink_to(outside_dir / "secret.wav")

    rows = [{"name": "evil", "ph_seq": "SP a SP", "ph_dur": "1.0 1.0 1.0"}]
    assert bd.check_ph_dur_duration("pjs", wav_dir, rows) == []
    problems = bd.validate_speaker("pjs", raw_dir, rows)
    assert any("unsafe name" in p for p in problems)


# --- build_dataset.check_note_dur_consistency (review #264 R3) -------------


def test_check_note_dur_consistency_flags_mismatch() -> None:
    rows = [
        {"name": "seg004", "ph_seq": "SP k a", "ph_dur": "0.5 0.5 0.5", "note_dur": "0.5 3.0"}
    ]
    violations = bd.check_note_dur_consistency("pjs", rows)
    assert len(violations) == 1
    assert "seg004" in violations[0]
    assert "1.5000s" in violations[0]  # ph_dur 合計
    assert "3.5000s" in violations[0]  # note_dur 合計


def test_check_note_dur_consistency_within_tolerance_is_silent() -> None:
    # ph_dur 合計 3.0 vs note_dur 合計 3.02: 差0.02s は絶対許容0.1sの範囲内
    rows = [
        {"name": "seg000", "ph_seq": "SP a SP", "ph_dur": "1.0 1.0 1.0", "note_dur": "1.0 1.02 1.0"}
    ]
    assert bd.check_note_dur_consistency("pjs", rows) == []


def test_check_note_dur_consistency_skips_rows_without_note_dur() -> None:
    rows = [{"name": "seg000", "ph_seq": "SP a SP", "ph_dur": "1.0 1.0 1.0"}]  # note_dur 列なし
    assert bd.check_note_dur_consistency("pjs", rows) == []


# --- review #264 R14 P2: 欠落 (missing key) と空値 (present but empty) の区別 -


def test_check_note_dur_consistency_still_skips_missing_note_dur_key() -> None:
    """列自体が無い（`row.get("note_dur")` が `None`）行は従来通り許容
    （optional 扱い）。R14 P2 の厳格化後も回帰しないことを固定する。
    """
    rows = [{"name": "seg000", "ph_seq": "SP a SP", "ph_dur": "1.0 1.0 1.0"}]  # note_dur 列なし
    assert bd.check_note_dur_consistency("pjs", rows) == []


def test_check_note_dur_consistency_rejects_present_but_empty_note_dur() -> None:
    """列は存在するが値が空文字列（CSV セルが空欄）の行は拒否する。

    旧実装の truthy ガード（`if not note_dur_raw`）は空文字列も「列が無い」
    場合と同一視してスキップしており、直後の `has empty note_dur` 分岐は
    到達不能だった。`is None` 判定に切り替えたことで、この行が実際に
    「note_dur が空」として拒否されることを固定する。
    """
    rows = [{"name": "seg000", "ph_seq": "SP a SP", "ph_dur": "1.0 1.0 1.0", "note_dur": ""}]
    violations = bd.check_note_dur_consistency("pjs", rows)
    assert len(violations) == 1
    assert "seg000" in violations[0]
    assert "has empty note_dur" in violations[0]


def test_check_note_dur_consistency_rejects_whitespace_only_note_dur() -> None:
    """空白のみの値（`split()` すると空リストになる）も同様に拒否する。"""
    rows = [{"name": "seg001", "ph_seq": "SP a SP", "ph_dur": "1.0 1.0 1.0", "note_dur": "   "}]
    violations = bd.check_note_dur_consistency("pjs", rows)
    assert len(violations) == 1
    assert "has empty note_dur" in violations[0]


# --- review #264 R8 P2 x2: note_dur 妥当性 + ノート単位比較 -----------------


def test_check_note_dur_consistency_rejects_non_numeric_note_dur() -> None:
    rows = [{"name": "seg000", "ph_seq": "SP a", "ph_dur": "1.0 1.0", "note_dur": "1.0 abc"}]
    violations = bd.check_note_dur_consistency("pjs", rows)
    assert len(violations) == 1
    assert "seg000" in violations[0]
    assert "non-numeric note_dur" in violations[0]


def test_check_note_dur_consistency_rejects_non_finite_note_dur() -> None:
    rows = [{"name": "seg000", "ph_seq": "SP a", "ph_dur": "1.0 1.0", "note_dur": "1.0 nan"}]
    violations = bd.check_note_dur_consistency("pjs", rows)
    assert len(violations) == 1
    assert "seg000" in violations[0]
    assert "non-finite note_dur" in violations[0]


def test_check_note_dur_consistency_rejects_non_positive_note_dur() -> None:
    rows = [{"name": "seg000", "ph_seq": "SP a", "ph_dur": "1.0 1.0", "note_dur": "1.0 0.0"}]
    violations = bd.check_note_dur_consistency("pjs", rows)
    assert len(violations) == 1
    assert "seg000" in violations[0]
    assert "non-positive note_dur" in violations[0]


def test_check_note_dur_consistency_detects_boundary_shift_with_matching_total() -> None:
    """合計は一致する（3.0 == 3.0）が、ph_num によるノート単位の対応付けで
    見ると境界が1秒ズレている破損（review #264 R8 P2 再現ケース）を検出する。
    """
    rows = [
        {
            "name": "seg005",
            "ph_seq": "SP a k",
            "ph_dur": "1 1 1",
            "ph_num": "1 2",
            "note_dur": "2 1",
        }
    ]
    violations = bd.check_note_dur_consistency("pjs", rows)
    assert len(violations) == 2
    assert all("seg005" in v and "note[" in v for v in violations)


# --- review #264 R9 P2: malformed ph_num は合計比較へフォールバックせず
# violation として拒否する (build_dataset.py:502) ---------------------------


def test_check_note_dur_consistency_rejects_non_numeric_ph_num_instead_of_total_fallback() -> None:
    """レビュー指摘の再現ケースそのもの: `ph_num="1 x"`（非数値）は、合計
    （ph_dur 1+1=2.0 == note_dur 1+1=2.0）が偶然一致していても、従来の
    「対応付け不能 -> 合計比較へフォールバック」経路では素通りしていた。
    ph_num フィールド自体は存在するため、フォールバックせず violation として
    拒否する。"""
    rows = [
        {
            "name": "seg006",
            "ph_seq": "SP a",
            "ph_dur": "1 1",
            "ph_num": "1 x",
            "note_dur": "1 1",
        }
    ]
    violations = bd.check_note_dur_consistency("pjs", rows)
    assert len(violations) == 1
    assert "seg006" in violations[0]
    assert "malformed ph_num" in violations[0]
    assert "non-numeric" in violations[0]


def test_check_note_dur_consistency_rejects_non_positive_ph_num_element() -> None:
    rows = [
        {
            "name": "seg007",
            "ph_seq": "SP a",
            "ph_dur": "1 1",
            "ph_num": "0 2",
            "note_dur": "1 1",
        }
    ]
    violations = bd.check_note_dur_consistency("pjs", rows)
    assert len(violations) == 1
    assert "seg007" in violations[0]
    assert "malformed ph_num" in violations[0]
    assert "non-positive" in violations[0]


def test_check_note_dur_consistency_rejects_ph_num_length_mismatch() -> None:
    rows = [
        {
            "name": "seg008",
            "ph_seq": "SP a k",
            "ph_dur": "1 1 1",
            "ph_num": "1 1 1",  # len(ph_num)=3 != len(note_dur)=2
            "note_dur": "1.5 1.5",
        }
    ]
    violations = bd.check_note_dur_consistency("pjs", rows)
    assert len(violations) == 1
    assert "seg008" in violations[0]
    assert "malformed ph_num" in violations[0]
    assert "len(ph_num)" in violations[0]


def test_check_note_dur_consistency_rejects_ph_num_sum_mismatch() -> None:
    rows = [
        {
            "name": "seg009",
            "ph_seq": "SP a",
            "ph_dur": "1 1",
            "ph_num": "3",  # sum(ph_num)=3 != len(ph_dur)=2
            "note_dur": "2",
        }
    ]
    violations = bd.check_note_dur_consistency("pjs", rows)
    assert len(violations) == 1
    assert "seg009" in violations[0]
    assert "malformed ph_num" in violations[0]
    assert "sum(ph_num)" in violations[0]


def test_check_note_dur_consistency_rejects_present_but_empty_ph_num() -> None:
    """review #264 R15 P2 (build_dataset.py:531): `ph_num` 列は存在するが
    値が空文字列（CSV セルが空欄）の行を拒否する。

    旧実装の truthy ガード（`if ph_num_raw:`）は空文字列も「列が無い」場合
    と同一視して合計比較へのフォールバックへ流しており、`ph_dur`/`note_dur`
    の合計がたまたま一致する（本ケースは 2 == 2）ため violation を検出
    できず、使い物にならない note-to-phoneme 対応付けをそのまま通過させて
    いた（`note_dur` R14 P2 と同型の欠陥）。`is None` 判定への変更後は
    「empty ph_num」として malformed 拒否されることを固定する。
    """
    rows = [
        {
            "name": "seg011",
            "ph_seq": "SP a",
            "ph_dur": "1 1",
            "ph_num": "",
            "note_dur": "1 1",
        }
    ]
    violations = bd.check_note_dur_consistency("pjs", rows)
    assert len(violations) == 1
    assert "seg011" in violations[0]
    assert "malformed ph_num" in violations[0]
    assert "empty ph_num" in violations[0]


def test_check_note_dur_consistency_rejects_whitespace_only_ph_num() -> None:
    """空白のみの値（`split()` すると空リストになる）も同様に拒否する。"""
    rows = [
        {
            "name": "seg012",
            "ph_seq": "SP a",
            "ph_dur": "1 1",
            "ph_num": "   ",
            "note_dur": "1 1",
        }
    ]
    violations = bd.check_note_dur_consistency("pjs", rows)
    assert len(violations) == 1
    assert "malformed ph_num" in violations[0]
    assert "empty ph_num" in violations[0]


def test_check_note_dur_consistency_falls_back_to_total_only_when_ph_num_absent() -> None:
    """`ph_num` 列自体が存在しない行（`row.get("ph_num") is None`）では、
    従来通り合計同士の比較へフォールバックする（malformed 拒否の対象外。
    R15 P2 で「値が空文字列」のケースと明確に区別されるようになった —
    そちらは上の `test_..._rejects_present_but_empty_ph_num` 参照）。"""
    rows = [
        {"name": "seg010", "ph_seq": "SP a", "ph_dur": "1 1", "note_dur": "1 1"}
    ]
    violations = bd.check_note_dur_consistency("pjs", rows)
    assert violations == []


# --- review #264 R11 P2: 合計比較はノート単位比較の代替ではなく追加
# (AND 条件、build_dataset.py:519) --------------------------------------------


def test_check_note_dur_consistency_detects_cumulative_drift_hidden_within_per_note_tolerance() -> None:
    """各ノートの ph_dur↔note_dur 差はノート単位の許容誤差（絶対0.1s）内に
    個別には収まるが、100 ノート全てが同方向へ 0.09s ずつズレているため
    合計では 9.0s の乖離になる破損行（review #264 R11 P2 再現ケース）。
    per-note 比較のみでは検出できず、常時実行される合計比較で初めて検出
    される。"""
    n_notes = 100
    ph_num = ["1"] * n_notes  # 1 音素/ノート
    ph_dur = ["1.0"] * n_notes  # 各音素 1.0s
    note_dur = ["1.09"] * n_notes  # 各ノート note_dur は 0.09s だけ過大（ノート単位では許容内）
    rows = [
        {
            "name": "seg100notes",
            "ph_seq": " ".join(["a"] * n_notes),
            "ph_dur": " ".join(ph_dur),
            "ph_num": " ".join(ph_num),
            "note_dur": " ".join(note_dur),
        }
    ]
    violations = bd.check_note_dur_consistency("pjs", rows)
    # per-note 比較自体は個別ノートの 0.09s 差が許容 0.1s 以内のため violation を出さない。
    per_note_violations = [v for v in violations if "note[" in v]
    assert per_note_violations == []
    # 合計比較（100 * 0.09s = 9.0s 乖離、許容 0.1s を大きく超過）が検出する。
    total_violations = [v for v in violations if "ph_dur total" in v]
    assert len(total_violations) == 1
    assert "seg100notes" in total_violations[0]
    assert "9.0000s" in total_violations[0] or "9.00" in total_violations[0]


def test_check_note_dur_consistency_malformed_ph_num_wired_into_validate_speaker(
    tmp_path: Path,
) -> None:
    """malformed ph_num の拒否が `validate_speaker`（fail-closed 集約）まで
    確実に配線されていることを end-to-end で確認する。"""
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "seg006.wav", 2.0)
    raw_dir = tmp_path
    rows = [
        {
            "name": "seg006",
            "ph_seq": "SP a",
            "ph_dur": "1.0 1.0",
            "ph_num": "1 x",
            "note_dur": "1.0 1.0",
        }
    ]
    problems = bd.validate_speaker("pjs", raw_dir, rows)
    assert any("seg006" in p and "malformed ph_num" in p for p in problems)


def test_check_note_dur_consistency_wired_into_validate_speaker_unconditionally(
    tmp_path: Path,
) -> None:
    """`check_ph_dur_duration`（wav vs ph_dur、既知5件の乖離を warn に留める
    2段階仕様）と異なり、ph_dur↔note_dur のクロスフィールド不変量は常に
    `validate_speaker` の problems（fail-closed）へ合流することを検証する
    （`--strict-duration` フラグに依存しない）。"""
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "seg004.wav", 3.0)
    raw_dir = tmp_path
    rows = [
        {"name": "seg004", "ph_seq": "SP k a", "ph_dur": "1.0 1.0 1.0", "note_dur": "1.0 1.0 5.0"}
    ]
    problems = bd.validate_speaker("pjs", raw_dir, rows)
    assert any("seg004" in p and "note_dur total" in p for p in problems)


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


def test_normalize_ph_dur_truncates_tail_of_overshooting_row(tmp_path: Path) -> None:
    """[P1 修正] (review #264 R1) 比例縮小ではなく末尾切り詰め方式。EOF
    （実 wav 長）以前の音素境界（この例では先頭 "SP" の 1.0s）は完全無変更
    のまま返され、EOF を跨ぐ音素だけが短縮される。

    [P1 修正] (review #264 R2) EOF より完全に後ろの音素は `ph_dur=0.0` の
    まま残さず、`ph_seq`/`ph_dur`/`ph_num`（対応するノートが空になった場合）
    から実際に削除する（0.0 のまま残すと下流 `build_dataset.validate_speaker`
    の `d <= 0` チェックで必ず reject されるため）。
    """
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "seg000.wav", 3.0)
    rows = [{"name": "seg000", "ph_seq": "SP a SP", "ph_dur": "1.0 4.0 1.0", "ph_num": "1 1 1"}]
    fixed_rows, fix_log, undershoot, unsafe, missing, malformed = cp.normalize_ph_dur_to_wav_duration(rows, wav_dir)
    assert undershoot == []
    assert unsafe == []
    assert missing == []
    assert len(fix_log) == 1
    assert "1 zero-length phoneme(s) removed" in fix_log[0]
    new_seq = fixed_rows[0]["ph_seq"].split()
    new_dur = [float(x) for x in fixed_rows[0]["ph_dur"].split()]
    assert sum(round(d, 6) for d in new_dur) == 3.0
    # 前方境界保存: 累積時刻が EOF(3.0s) に達するまでの先頭 "SP"（1.0s）は
    # 完全無変更
    assert new_dur[0] == 1.0
    # EOF を跨ぐ "a"（1.0s ~ 5.0s の予定）は EOF でちょうど収まるよう
    # 2.0s へ短縮される
    assert new_dur[1] == 2.0
    # EOF より完全に後ろの末尾 "SP" は要素ごと削除される（ゼロ長のまま残さない）
    assert new_seq == ["SP", "a"]
    assert len(new_dur) == 2
    # 削除された末尾 "SP" は自身の専用ノート（ph_num 3個目=1）だったため、
    # そのノート自体も ph_num から除去される
    assert fixed_rows[0]["ph_num"] == "1 1"


def test_normalize_ph_dur_removes_note_seq_note_dur_for_emptied_note(tmp_path: Path) -> None:
    """`note_seq`/`note_dur`（ノート単位フィールド）が存在する場合も、音素が
    1個も残らなかったノートは同じインデックスで除去されることを検証する
    （review #264 R2）。"""
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "seg000.wav", 3.0)
    rows = [
        {
            "name": "seg000",
            "ph_seq": "SP a SP",
            "ph_dur": "1.0 4.0 1.0",
            "ph_num": "1 1 1",
            "note_seq": "rest 60 rest",
            "note_dur": "1.0 4.0 1.0",
        }
    ]
    fixed_rows, fix_log, undershoot, unsafe, missing, malformed = cp.normalize_ph_dur_to_wav_duration(rows, wav_dir)
    assert undershoot == []
    assert unsafe == []
    assert missing == []
    assert len(fix_log) == 1
    assert fixed_rows[0]["ph_num"] == "1 1"
    assert fixed_rows[0]["note_seq"] == "rest 60"
    # [P2 修正] (review #264 R3) note1（"a"）は 4.0s -> 2.0s へ truncate される
    # ため、note_dur も同じ 2.0s へ再計算される（旧実装は "4.0" のまま残り、
    # ph_dur 合計との不変量が崩れていた）。
    assert fixed_rows[0]["note_dur"] == "1.0 2.0"
    new_dur_total = sum(float(x) for x in fixed_rows[0]["ph_dur"].split())
    note_dur_total = sum(float(x) for x in fixed_rows[0]["note_dur"].split())
    assert new_dur_total == note_dur_total  # クロスフィールド不変量


def test_normalize_ph_dur_partial_note_survives_with_reduced_count(tmp_path: Path) -> None:
    """1個のノートに複数音素が対応しており、そのうち一部だけが EOF より後ろへ
    落ちる場合、ノート自体は残しつつ `ph_num` のそのノートの数だけ減ること
    を検証する（`ph_num` の対応ノートが空にならない限りノートは削除されない）。

    [P2 修正] (review #264 R3) 生存ノート（note1）の `note_dur` も、その
    ノートに属する生存音素の新 ph_dur 合計へ追従して短縮されることを検証
    する（旧実装は note_dur を旧値 3.0 のまま残し、ph_dur 合計 1.0（wav 全体
    では 1.5s）と note_dur 合計 3.5s が食い違ったまま下流へ渡っていた —
    レビュー指摘の「ph_dur/wav 1.5s vs note_dur 3.5s」の再現ケース）。
    """
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "seg004.wav", 1.5)
    # ノート0="SP"（1音素）、ノート1="k"+"a"+"i"（3音素）。累積:
    # SP(0.5)->0.5, k(0.5)->1.0, a(1.5)->EOF(1.5)超過なので EOF 跨ぎとして
    # 1.5-1.0=0.5 に短縮（crossed）。crossed 以降の "i" は完全に EOF より
    # 後ろへ落ちるため 0.0 化->削除対象になる。
    rows = [
        {
            "name": "seg004",
            "ph_seq": "SP k a i",
            "ph_dur": "0.5 0.5 1.5 1.0",
            "ph_num": "1 3",
            "note_seq": "rest 60",
            "note_dur": "0.5 3.0",
        }
    ]
    fixed_rows, fix_log, undershoot, unsafe, missing, malformed = cp.normalize_ph_dur_to_wav_duration(rows, wav_dir)
    assert undershoot == []
    assert unsafe == []
    assert missing == []
    assert len(fix_log) == 1
    new_seq = fixed_rows[0]["ph_seq"].split()
    new_dur = [float(x) for x in fixed_rows[0]["ph_dur"].split()]
    # "i" のみ完全に EOF より後ろに落ちるため削除される
    assert new_seq == ["SP", "k", "a"]
    assert len(new_dur) == 3
    assert sum(round(d, 6) for d in new_dur) == 1.5
    # ノート1（"k","a","i" のうち "i" のみ喪失）は残存するが音素数が 3->2 に減る
    assert fixed_rows[0]["ph_num"] == "1 2"
    # ノート自体は消えていないので note_seq（音高/歌詞情報）は無変更のまま
    assert fixed_rows[0]["note_seq"] == rows[0]["note_seq"]
    # note_dur は旧値 "0.5 3.0" のままではなく、生存音素の新 ph_dur 合計へ
    # 追従する: note0=SP(0.5, 無変更) / note1=k(0.5)+a(0.5, truncate後)=1.0
    # （旧実装バグ: "0.5 3.0" のまま残り ph_dur 合計との不変量が崩れていた）。
    assert fixed_rows[0]["note_dur"] == "0.5 1.0"
    note_dur_total = sum(float(x) for x in fixed_rows[0]["note_dur"].split())
    assert note_dur_total == sum(round(d, 6) for d in new_dur)  # クロスフィールド不変量


def test_normalize_ph_dur_forward_boundaries_preserved_multi_phoneme(tmp_path: Path) -> None:
    """EOF 以前に複数音素がある場合、それら全ての境界（開始時刻・長さ）が
    一切変更されないことを確認する（比例縮小との違いの核心）。"""
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "seg002.wav", 2.0)
    # 累積: 0.3 / 0.7 / 1.2(=0.3+0.4+... 実際は 0.3,0.4,0.5 -> 1.2) / 続いて
    # 大きな最終音素が EOF を大きく超過する
    rows = [{"name": "seg002", "ph_seq": "a i u SP", "ph_dur": "0.3 0.4 0.5 5.0"}]
    fixed_rows, fix_log, undershoot, unsafe, missing, malformed = cp.normalize_ph_dur_to_wav_duration(rows, wav_dir)
    assert undershoot == []
    assert unsafe == []
    assert missing == []
    assert len(fix_log) == 1
    new_dur = [float(x) for x in fixed_rows[0]["ph_dur"].split()]
    # EOF(2.0s) 以前の 3 音素は完全無変更
    assert new_dur[0] == 0.3
    assert new_dur[1] == 0.4
    assert new_dur[2] == 0.5
    # EOF を跨ぐ最終音素は残り (2.0 - 1.2 = 0.8s) へ短縮
    assert abs(new_dur[3] - 0.8) < 1e-9
    assert sum(round(d, 6) for d in new_dur) == 2.0


def test_normalize_ph_dur_undershoot_is_rejected_fail_closed(tmp_path: Path) -> None:
    """[P2 修正] (review #264 R5) 申告合計が実 wav 長を下回る場合
    （undershoot）は、もはや最後の音素へ不足分を加算して合成しない。EOF
    クランプ診断は overshoot のみを説明し undershoot は原因未特定・
    現行素材でも未観測のため、行は無変更のまま返し違反として収集する
    （公開前に fail-closed で拒否するのは呼び出し元 `main()` の責務）。"""
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "seg003.wav", 4.0)
    row = {"name": "seg003", "ph_seq": "a i SP", "ph_dur": "0.5 0.5 0.5"}  # 合計1.5s vs 実4.0s
    rows = [row]
    fixed_rows, fix_log, undershoot, unsafe, missing, malformed = cp.normalize_ph_dur_to_wav_duration(rows, wav_dir)
    assert fix_log == []  # overshoot 用ログには積まれない
    assert len(undershoot) == 1
    assert "seg003" in undershoot[0]
    assert "1.5000s" in undershoot[0]
    assert "4.0000s" in undershoot[0]
    assert "undershoot" in undershoot[0]
    assert unsafe == []
    assert missing == []
    # 行自体は無変更（捏造した境界を書き込まない）のまま返る
    assert fixed_rows[0] is row
    assert fixed_rows[0]["ph_dur"] == "0.5 0.5 0.5"


def test_normalize_ph_dur_undershoot_within_tolerance_is_untouched(tmp_path: Path) -> None:
    """許容誤差（絶対0.1s）内の undershoot は従来通り無変更のまま許容する
    （fail-closed の対象は許容超過の undershoot のみ）。"""
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "seg005.wav", 3.05)
    row = {"name": "seg005", "ph_seq": "SP a SP", "ph_dur": "1.0 1.0 1.0"}  # 合計3.0s vs 実3.05s
    rows = [row]
    fixed_rows, fix_log, undershoot, unsafe, missing, malformed = cp.normalize_ph_dur_to_wav_duration(rows, wav_dir)
    assert fix_log == []
    assert undershoot == []
    assert unsafe == []
    assert missing == []
    assert fixed_rows[0] is row


def test_normalize_ph_dur_leaves_within_tolerance_row_byte_identical(tmp_path: Path) -> None:
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "seg001.wav", 3.0)
    row = {"name": "seg001", "ph_seq": "SP a SP", "ph_dur": "1.0 1.02 1.0"}
    rows = [row]
    fixed_rows, fix_log, undershoot, unsafe, missing, malformed = cp.normalize_ph_dur_to_wav_duration(rows, wav_dir)
    assert fix_log == []
    assert undershoot == []
    assert unsafe == []
    assert missing == []
    assert fixed_rows[0] is row  # 無変更行は同一オブジェクトのまま返る


# --- convert_pjs.normalize_ph_dur_to_wav_duration: 欠落/破損/ゼロ長 wav の
# fail-closed 拒否 (review #264 R9 P2) ---------------------------------------


def test_normalize_ph_dur_missing_wav_is_collected_not_silently_skipped(tmp_path: Path) -> None:
    """`db_converter.py` が成功終了していても CSV 行の wav が存在しない場合、
    従来は「検証不能 = skip」として行を無変更のまま黙って通していた。この
    ケースは `missing_wav_violations` へ収集され、行自体は無変更のまま
    返る（呼び出し元 `main()` が公開前に fail-closed で拒否する）。"""
    wav_dir = tmp_path / "wavs"
    wav_dir.mkdir()
    row = {"name": "nope", "ph_seq": "SP a SP", "ph_dur": "1.0 1.0 1.0"}
    rows = [row]
    fixed_rows, fix_log, undershoot, unsafe, missing, malformed = cp.normalize_ph_dur_to_wav_duration(rows, wav_dir)
    assert fix_log == []
    assert undershoot == []
    assert unsafe == []
    assert len(missing) == 1
    assert "nope" in missing[0]
    assert fixed_rows[0] is row


def test_normalize_ph_dur_zero_length_wav_is_collected_not_silently_skipped(tmp_path: Path) -> None:
    """wav ファイルは存在するがゼロ長（`_wav_duration_seconds` が 0 を返す）
    場合も、欠落と同様に `missing_wav_violations` へ収集する。"""
    wav_dir = tmp_path / "wavs"
    wav_dir.mkdir()
    (wav_dir / "empty.wav").write_bytes(b"")
    row = {"name": "empty", "ph_seq": "SP a SP", "ph_dur": "1.0 1.0 1.0"}
    rows = [row]
    fixed_rows, fix_log, undershoot, unsafe, missing, malformed = cp.normalize_ph_dur_to_wav_duration(rows, wav_dir)
    assert fix_log == []
    assert undershoot == []
    assert unsafe == []
    assert len(missing) == 1
    assert "empty" in missing[0]
    assert fixed_rows[0] is row


def test_normalize_ph_dur_missing_wav_fails_closed_via_main(tmp_path: Path) -> None:
    """`main()` は `missing_wav_violations` が非空なら swap 前に fail-closed
    で拒否し、`--staging-dir` を新世代へ差し替えない（公開しない）ことを
    end-to-end で確認する回帰ガードの土台として、
    `normalize_ph_dur_to_wav_duration` を直接呼んだ結果を `main()` の判定
    条件と同じ形（非空なら reject）で再確認する。"""
    wav_dir = tmp_path / "wavs"
    wav_dir.mkdir()
    row = {"name": "gone", "ph_seq": "SP a SP", "ph_dur": "1.0 1.0 1.0"}
    fixed_rows, fix_log, undershoot, unsafe, missing, malformed = cp.normalize_ph_dur_to_wav_duration([row], wav_dir)
    assert len(missing) == 1  # main() はこれを検知して return 1 する
    assert fixed_rows[0] is row


def test_normalize_ph_dur_mixed_batch_only_touches_violating_rows(tmp_path: Path) -> None:
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "good.wav", 3.0)
    _write_wav(wav_dir / "bad.wav", 3.0)
    good = {"name": "good", "ph_seq": "SP a SP", "ph_dur": "1.0 1.0 1.0"}
    bad = {"name": "bad", "ph_seq": "SP a SP", "ph_dur": "1.0 4.0 1.0"}
    fixed_rows, fix_log, undershoot, unsafe, missing, malformed = cp.normalize_ph_dur_to_wav_duration([good, bad], wav_dir)
    assert undershoot == []
    assert unsafe == []
    assert missing == []
    assert len(fix_log) == 1
    assert "bad" in fix_log[0]
    assert fixed_rows[0] is good
    assert fixed_rows[1] is not bad
    assert sum(round(float(x), 6) for x in fixed_rows[1]["ph_dur"].split()) == 3.0


# --- end-to-end: normalize -> transcriptions.csv -> build_dataset.validate_speaker
#
# [P1 修正] (review #264 R2): R1 のテストは `normalize_ph_dur_to_wav_duration`
# の戻り値のみを検査し、下流 `build_dataset.validate_speaker` まで実際に
# 通していなかった。旧方式（EOF より完全に後ろの音素を ph_dur=0.0 のまま
# ph_seq 等に残す）は `validate_speaker` の `any(d <= 0 for d in ph_dur)`
# チェックに無条件で reject され、修復セグメントは `convert_pjs.py` 単体では
# 成功しても `build_dataset.py` を通す時点で必ず fail する統合バグだった。
# 以下のテストは「毒サンプル捜査」で実測判明した5件（`pjs004_seg003` /
# `pjs016_seg000` / `pjs029_seg002` / `pjs030_seg001` / `pjs031_seg001`、
# `results_s1/s1_record_2026-08-15.md` 参照）と同じ構造不良（末尾に1個以上
# EOF より完全に後ろの音素を持つ行）を代表する行を正規化 ->
# `transcriptions.csv` へ実際に書き出し・読み戻し -> `validate_speaker` まで
# 通して、0 件の問題（fail-closed に引っかからない）で公開できることを
# end-to-end で確認する（実 PJS 音源・`nnsvs-db-converter` 実 clone は本
# 環境では利用不能な machine-dependent 素材のため、構造を再現した合成 wav
# で代替する。実素材での再測定は別途 machine-dependent 検証として扱う）。


def test_normalize_then_build_dataset_validate_speaker_passes_end_to_end(
    tmp_path: Path,
) -> None:
    import csv as _csv

    raw_dir = tmp_path / "pjs_raw"
    wav_dir = raw_dir / "wavs"
    # pjs004/pjs029/pjs030 相当（`s1_record_2026-08-15.md` 実測記録どおり
    # 「末尾1音素（全件SP）がゼロ化されるのみ」）: 申告合計が実 wav 長を
    # 大きく超過し、末尾 SP が完全に EOF より後ろへ落ちる代表例。
    _write_wav(wav_dir / "seg000.wav", 3.0)
    # pjs016_seg000 相当（末尾から2番目の母音が短縮され、末尾 SP がゼロ化）:
    # EOF 跨ぎ音素自体は短縮されて生存し（削除されない）、それより後ろの
    # 末尾 SP のみが削除対象になる代表例。
    _write_wav(wav_dir / "seg001.wav", 2.0)
    rows = [
        {
            "name": "seg000",
            "ph_seq": "SP a SP",
            "ph_dur": "1.0 4.0 1.0",
            "ph_num": "1 1 1",
            "note_seq": "rest 60 rest",
            "note_dur": "1.0 4.0 1.0",
        },
        {
            "name": "seg001",
            "ph_seq": "SP k a i SP",
            "ph_dur": "0.5 0.5 0.5 3.0 1.0",
            "ph_num": "1 1 2 1",
            "note_seq": "rest 60 60 rest",
            "note_dur": "0.5 0.5 3.5 1.0",
        },
    ]

    fixed_rows, fix_log, undershoot, unsafe, missing, malformed = cp.normalize_ph_dur_to_wav_duration(rows, wav_dir)
    assert undershoot == []
    assert unsafe == []
    assert missing == []
    assert len(fix_log) == 2  # 両行とも許容誤差を超過し正規化対象になる

    csv_path = raw_dir / "transcriptions.csv"
    fieldnames = ["name", "ph_seq", "ph_dur", "ph_num", "note_seq", "note_dur"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = _csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(fixed_rows)

    reloaded = bd.read_transcriptions(csv_path)
    # 正規化により ph_dur=0.0 の音素が1個も残っていないこと（validate_speaker
    # の d<=0 チェックに引っかかる要素が存在しないこと）を先に確認する。
    for row in reloaded:
        durs = [float(x) for x in row["ph_dur"].split()]
        assert all(d > 0.0 for d in durs), (row["name"], durs)
        assert len(row["ph_seq"].split()) == len(durs)

    problems = bd.validate_speaker("pjs", raw_dir, reloaded)
    assert problems == [], problems

    duration_warnings = bd.check_ph_dur_duration("pjs", wav_dir, reloaded)
    assert duration_warnings == [], duration_warnings


# --- convert_pjs.normalize_ph_dur_to_wav_duration: 安全でない name の
# fail-closed 拒否 (review #264 R7) ------------------------------------------
#
# `check_ph_dur_duration`（build_dataset.py 側、review #264 R4 P2 で修正済み）
# は不安全 name を黙ってスキップするだけで良い（`validate_speaker` 側が別途
# problems へ昇格させるため）。一方 `normalize_ph_dur_to_wav_duration` には
# そのような別経路の検出ロジックが存在しないため、ここでは違反を
# `unsafe_name_violations` として自ら収集し、呼び出し元 `main()` が公開前に
# fail-closed で拒否する（`check_ph_dur_duration` とは異なり「黙ってスキップ」
# では終わらない）。


def test_normalize_ph_dur_does_not_open_path_traversal_name(tmp_path: Path) -> None:
    """`row["name"]` が `../../outside/secret` のような wav_dir 外を指す名前
    でも、`normalize_ph_dur_to_wav_duration` は `_wav_duration_seconds` で
    WAV を open してはならない。安全でない name は `unsafe_name_violations`
    へ収集し、行は無変更のまま返す。"""
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    # もし open されていれば ph_dur との乖離から fix_log/undershoot 側に
    # 副作用が出てしまうはずの secret wav（10.0s）。
    _write_wav(outside_dir / "secret.wav", 10.0)

    wav_dir = tmp_path / "pjs" / "wavs"
    wav_dir.mkdir(parents=True)

    row = {"name": "../../outside/secret", "ph_seq": "SP a SP", "ph_dur": "1.0 1.0 1.0"}
    rows = [row]
    fixed_rows, fix_log, undershoot, unsafe, missing, malformed = cp.normalize_ph_dur_to_wav_duration(rows, wav_dir)
    assert fix_log == []
    assert undershoot == []
    assert len(unsafe) == 1
    assert missing == []
    assert "../../outside/secret" in unsafe[0]
    assert fixed_rows[0] is row  # 無変更のまま返る（open されていない証拠でもある）


def test_normalize_ph_dur_does_not_open_symlink_escape(tmp_path: Path) -> None:
    """wav_dir 配下の symlink が wav_dir 外の実ファイルへ逃げている場合も、
    resolve 後のパスが wav_dir 配下に収まらない限り open しない。"""
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    _write_wav(outside_dir / "secret.wav", 10.0)

    wav_dir = tmp_path / "pjs" / "wavs"
    wav_dir.mkdir(parents=True)
    (wav_dir / "evil.wav").symlink_to(outside_dir / "secret.wav")

    row = {"name": "evil", "ph_seq": "SP a SP", "ph_dur": "1.0 1.0 1.0"}
    rows = [row]
    fixed_rows, fix_log, undershoot, unsafe, missing, malformed = cp.normalize_ph_dur_to_wav_duration(rows, wav_dir)
    assert fix_log == []
    assert undershoot == []
    assert len(unsafe) == 1
    assert missing == []
    assert "evil" in unsafe[0]
    assert fixed_rows[0] is row


def test_normalize_ph_dur_unsafe_name_mixed_with_safe_rows(tmp_path: Path) -> None:
    """安全な行と不安全な行が混在する場合、安全な行は通常どおり処理され
    （overshoot 正規化も含め）、不安全な行だけが `unsafe_name_violations`
    へ収集されることを検証する（全件収集の設計を確認する）。"""
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "good.wav", 3.0)
    good = {"name": "good", "ph_seq": "SP a SP", "ph_dur": "1.0 4.0 1.0", "ph_num": "1 1 1"}
    evil = {"name": "../escape", "ph_seq": "SP a SP", "ph_dur": "1.0 1.0 1.0"}
    fixed_rows, fix_log, undershoot, unsafe, missing, malformed = cp.normalize_ph_dur_to_wav_duration(
        [good, evil], wav_dir
    )
    assert undershoot == []
    assert len(unsafe) == 1
    assert missing == []
    assert "../escape" in unsafe[0]
    assert len(fix_log) == 1
    assert "good" in fix_log[0]
    assert fixed_rows[0] is not good  # overshoot 正規化により新しい行になる
    assert fixed_rows[1] is evil  # 不安全な行は無変更のまま


def test_normalize_ph_dur_unsafe_name_fails_closed_via_main(tmp_path: Path, capsys) -> None:
    """`main()` は `unsafe_name_violations` が非空なら swap 前に fail-closed
    で拒否し、`--staging-dir` を新世代へ差し替えない（公開しない）ことを
    end-to-end で確認する回帰ガードの土台として、`normalize_ph_dur_to_wav_duration`
    を直接呼んだ結果を `main()` の判定条件と同じ形（非空なら reject）で
    再確認する。"""
    wav_dir = tmp_path / "wavs"
    wav_dir.mkdir()
    row = {"name": "/etc/passwd", "ph_seq": "SP a SP", "ph_dur": "1.0 1.0 1.0"}
    fixed_rows, fix_log, undershoot, unsafe, missing, malformed = cp.normalize_ph_dur_to_wav_duration([row], wav_dir)
    assert len(unsafe) == 1  # main() はこれを検知して return 1 する
    assert missing == []
    assert fixed_rows[0] is row


# --- convert_pjs.normalize_ph_dur_to_wav_duration: 非有限 ph_dur の拒否 +
# 行単位で独立な検査の非短絡化 (review #264 R17 P2, convert_pjs.py:456) -------
#
# 旧実装は ph_dur が非数/空/非有限（nan/inf）の行を最初の検査で即 continue
# しており、同じ行に対する name 安全性検査・missing/unreadable WAV 検査を
# 丸ごと短絡していた。これらは ph_dur の妥当性とは独立な観点（row["name"]
# と実 WAV ファイルにしか依存しない）であるにもかかわらず、ph_dur 側の
# 異常だけが先に continue すると、どの violation リストにも記録されない
# 行が生まれ得た（fail-closed をすり抜ける）。


def test_normalize_ph_dur_rejects_nan_as_malformed(tmp_path: Path) -> None:
    """`ph_dur` に `nan` が含まれる行は `malformed_ph_dur_violations` へ
    収集され、行は無変更のまま返る。"""
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "seg000.wav", 3.0)
    row = {"name": "seg000", "ph_seq": "SP a SP", "ph_dur": "1.0 nan 1.0"}
    fixed_rows, fix_log, undershoot, unsafe, missing, malformed = cp.normalize_ph_dur_to_wav_duration(
        [row], wav_dir
    )
    assert fix_log == []
    assert undershoot == []
    assert unsafe == []
    assert missing == []
    assert len(malformed) == 1
    assert "seg000" in malformed[0]
    assert fixed_rows[0] is row


def test_normalize_ph_dur_rejects_inf_as_malformed(tmp_path: Path) -> None:
    """`ph_dur` に `inf` が含まれる行も同様に malformed として拒否する。"""
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "seg001.wav", 3.0)
    row = {"name": "seg001", "ph_seq": "SP a SP", "ph_dur": "1.0 inf 1.0"}
    fixed_rows, fix_log, undershoot, unsafe, missing, malformed = cp.normalize_ph_dur_to_wav_duration(
        [row], wav_dir
    )
    assert len(malformed) == 1
    assert "seg001" in malformed[0]
    assert fixed_rows[0] is row


def test_normalize_ph_dur_malformed_and_unsafe_name_both_recorded(tmp_path: Path) -> None:
    """レビュー再現例: `name="../../escape"` かつ `ph_dur="nan"` の行は、
    旧実装だと ph_dur の非有限判定で即 continue し、unsafe_name_violations
    には一切現れず、5個の violation リストすべてが空のまま通過していた。
    修正後は ph_dur 妥当性検査と name 安全性検査が短絡せず両方実行され、
    `malformed_ph_dur_violations` と `unsafe_name_violations` の両方に
    同時に記録される。"""
    wav_dir = tmp_path / "pjs" / "wavs"
    wav_dir.mkdir(parents=True)
    row = {"name": "../../escape", "ph_seq": "SP a SP", "ph_dur": "nan"}
    fixed_rows, fix_log, undershoot, unsafe, missing, malformed = cp.normalize_ph_dur_to_wav_duration(
        [row], wav_dir
    )
    assert fix_log == []
    assert undershoot == []
    assert len(unsafe) == 1
    assert "../../escape" in unsafe[0]
    assert missing == []
    assert len(malformed) == 1
    assert "../../escape" in malformed[0]
    assert fixed_rows[0] is row  # unsafe なので open されておらず無変更


def test_normalize_ph_dur_malformed_and_missing_wav_both_recorded(tmp_path: Path) -> None:
    """`ph_dur` が malformed かつ WAV が欠落している行は、name 自体は安全
    なので missing_wav_violations と malformed_ph_dur_violations の両方に
    記録される（name 安全性検査は ph_dur 妥当性に関わらず必ず走ることの
    確認）。"""
    wav_dir = tmp_path / "wavs"
    wav_dir.mkdir(parents=True)
    row = {"name": "no_such_wav", "ph_seq": "SP a SP", "ph_dur": "nan"}
    fixed_rows, fix_log, undershoot, unsafe, missing, malformed = cp.normalize_ph_dur_to_wav_duration(
        [row], wav_dir
    )
    assert unsafe == []
    assert len(missing) == 1
    assert "no_such_wav" in missing[0]
    assert len(malformed) == 1
    assert "no_such_wav" in malformed[0]
    assert fixed_rows[0] is row


def test_normalize_ph_dur_malformed_mixed_with_safe_rows(tmp_path: Path) -> None:
    """malformed な行と正常な行が混在する場合、正常な行は通常どおり処理され
    （overshoot 正規化も含め）、malformed な行だけが
    `malformed_ph_dur_violations` へ収集されることを確認する。"""
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "good.wav", 3.0)
    _write_wav(wav_dir / "bad.wav", 3.0)
    good = {"name": "good", "ph_seq": "SP a SP", "ph_dur": "1.0 4.0 1.0", "ph_num": "1 1 1"}
    bad = {"name": "bad", "ph_seq": "SP a SP", "ph_dur": "1.0 nan 1.0"}
    fixed_rows, fix_log, undershoot, unsafe, missing, malformed = cp.normalize_ph_dur_to_wav_duration(
        [good, bad], wav_dir
    )
    assert undershoot == []
    assert unsafe == []
    assert missing == []
    assert len(malformed) == 1
    assert "bad" in malformed[0]
    assert len(fix_log) == 1
    assert "good" in fix_log[0]
    assert fixed_rows[0] is not good  # overshoot 正規化により新しい行になる
    assert fixed_rows[1] is bad  # malformed な行は無変更のまま


def test_normalize_ph_dur_malformed_fails_closed_via_main(tmp_path: Path) -> None:
    """`main()` は `malformed_ph_dur_violations` が非空なら swap 前に
    fail-closed で拒否する判定条件を、`normalize_ph_dur_to_wav_duration`
    を直接呼んだ結果で確認する。"""
    wav_dir = tmp_path / "wavs"
    wav_dir.mkdir()
    row = {"name": "seg002", "ph_seq": "SP a SP", "ph_dur": "not-a-number"}
    fixed_rows, fix_log, undershoot, unsafe, missing, malformed = cp.normalize_ph_dur_to_wav_duration(
        [row], wav_dir
    )
    assert len(malformed) == 1  # main() はこれを検知して return 1 する
    assert fixed_rows[0] is row


# --- convert_pjs.normalize_ph_dur_to_wav_duration: 非正 ph_dur の拒否
# (review #264 R18 P2, convert_pjs.py:480) -----------------------------------
#
# `ph_dur="-1 2"` のような負値混じりの行は、合計が実 wav 長と偶然一致する
# （相殺）と旧実装の合計比較だけでは検出できず、そのまま公開されていた。
# `build_dataset.validate_speaker` の `any(d <= 0 for d in ph_dur)` は無条件
# reject するため、`convert_pjs.py` 単体では成功しても下流で必ず fail する
# 統合バグだった（R2/R17 と同型）。負値は無条件で malformed、ゼロは
# `_drop_dead_phonemes` の overshoot 修復経路が正規に処理する値のため
# malformed 扱いしないが、修復を経ずに（または修復が効かずに）ゼロが公開行
# へ残るケースは別途 fail-closed で拒否する。


def test_normalize_ph_dur_rejects_negative_duration_that_cancels_total(tmp_path: Path) -> None:
    """レビュー再現例そのもの: `ph_dur="-1 2"` は合計 1.0s が 1 秒 WAV と
    一致するため、合計比較だけでは検出できない。負値は合計比較の前に
    無条件で malformed 判定される。"""
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "seg000.wav", 1.0)
    row = {"name": "seg000", "ph_seq": "a i", "ph_dur": "-1 2"}
    fixed_rows, fix_log, undershoot, unsafe, missing, malformed = cp.normalize_ph_dur_to_wav_duration(
        [row], wav_dir
    )
    assert fix_log == []
    assert undershoot == []
    assert unsafe == []
    assert missing == []
    assert len(malformed) == 1
    assert "seg000" in malformed[0]
    assert "negative" in malformed[0]
    assert fixed_rows[0] is row  # malformed のため無変更のまま


def test_normalize_ph_dur_rejects_negative_duration_regardless_of_total(tmp_path: Path) -> None:
    """相殺せずとも、負値が1個でも含まれていれば無条件で malformed になる
    ことを確認する（total が wav_dur から大きく外れていても同じ扱い）。"""
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "seg001.wav", 5.0)
    row = {"name": "seg001", "ph_seq": "a i u", "ph_dur": "1.0 -0.5 1.0"}
    fixed_rows, fix_log, undershoot, unsafe, missing, malformed = cp.normalize_ph_dur_to_wav_duration(
        [row], wav_dir
    )
    assert fix_log == []
    assert undershoot == []
    assert len(malformed) == 1
    assert "seg001" in malformed[0]
    assert fixed_rows[0] is row


def test_normalize_ph_dur_zero_via_overshoot_repair_path_is_not_malformed(tmp_path: Path) -> None:
    """ゼロが `_drop_dead_phonemes` の overshoot 修復経路（正規の除去先）に
    入る場合は malformed 扱いされず、従来通り除去されて公開されることの
    回帰ガード（既存 `test_normalize_ph_dur_truncates_tail_of_overshooting_row`
    と同じ構造を、本 fix が壊していないことを明示的に固定する）。"""
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "seg002.wav", 3.0)
    row = {"name": "seg002", "ph_seq": "SP a SP", "ph_dur": "1.0 4.0 1.0", "ph_num": "1 1 1"}
    fixed_rows, fix_log, undershoot, unsafe, missing, malformed = cp.normalize_ph_dur_to_wav_duration(
        [row], wav_dir
    )
    assert malformed == []  # ゼロは修復経路が正規に処理するため malformed にならない
    assert undershoot == []
    assert unsafe == []
    assert missing == []
    assert len(fix_log) == 1
    new_dur = [float(x) for x in fixed_rows[0]["ph_dur"].split()]
    assert all(d > 0.0 for d in new_dur)  # ゼロは実際に除去されている


def test_normalize_ph_dur_rejects_zero_within_tolerance_never_repaired(tmp_path: Path) -> None:
    """申告合計が許容誤差内で実 wav 長と一致するため overshoot 修復が
    トリガーされない行に、ゼロ長音素が既に含まれているケース（「修復経路
    に入らないゼロ」）。総和が変わらないため合計比較だけでは検出できず、
    修復も走らないため無修復のまま公開されてしまう。fail-closed で拒否
    することを確認する。"""
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "seg003.wav", 2.0)
    row = {"name": "seg003", "ph_seq": "a i u", "ph_dur": "1.0 0.0 1.0"}  # 合計2.0s、許容内一致
    fixed_rows, fix_log, undershoot, unsafe, missing, malformed = cp.normalize_ph_dur_to_wav_duration(
        [row], wav_dir
    )
    assert fix_log == []
    assert undershoot == []
    assert unsafe == []
    assert missing == []
    assert len(malformed) == 1
    assert "seg003" in malformed[0]
    assert "within tolerance" in malformed[0]
    assert fixed_rows[0] is row  # 行自体は無変更のまま（公開されない）


def test_normalize_ph_dur_rejects_residual_zero_after_length_mismatch_fallback(
    tmp_path: Path,
) -> None:
    """`_drop_dead_phonemes` の ph_seq/ph_dur 長不一致フォールバック（安全側
    で削除せず ph_dur のみ更新）を通ると、overshoot 修復が呼ばれてもゼロ長
    音素が除去されずに published 出力へ残り得る。この残存ゼロを公開直前に
    検出して fail-closed で拒否することを確認する。"""
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "seg004.wav", 1.0)
    # ph_seq (2要素) と ph_dur (3要素) が対応しないため `_drop_dead_phonemes`
    # は安全側フォールバックへ入り、truncate で 0.0 化された末尾要素を
    # 削除せずそのまま返す。
    row = {"name": "seg004", "ph_seq": "a i", "ph_dur": "0.5 0.5 2.0"}  # 合計3.0s vs 実1.0s
    fixed_rows, fix_log, undershoot, unsafe, missing, malformed = cp.normalize_ph_dur_to_wav_duration(
        [row], wav_dir
    )
    assert fix_log == []
    assert undershoot == []
    assert unsafe == []
    assert missing == []
    assert len(malformed) == 1
    assert "seg004" in malformed[0]
    assert "after overshoot repair" in malformed[0]


# --- convert_pjs._is_safe_wav_name / build_dataset._is_safe_wav_name:
# 挙動同値性 (review #264 R7) -------------------------------------------------
#
# 両ファイルとも record スクリプト群の既存慣例（共有モジュール新設ではなく
# ロジックをコピペする）に倣っているため、判定ロジックの実装が独立に
# ドリフトしないことをこのテストで担保する。


def test_is_safe_wav_name_matches_build_dataset_behavior(tmp_path: Path) -> None:
    wav_dir = tmp_path / "wavs"
    wav_dir.mkdir()
    (wav_dir / "safe.wav").write_bytes(b"")
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (wav_dir / "evil.wav").symlink_to(outside_dir / "does_not_matter.wav")
    resolved_wav_dir = wav_dir.resolve()

    candidates = [
        "safe",
        "seg000",
        "../escape",
        "../../outside/secret",
        "/etc/passwd",
        "a/b",
        "",
        ".",
        "..",
        "evil",  # wav_dir 配下だが wav_dir 外へ逃げる symlink
    ]
    for name in candidates:
        assert cp._is_safe_wav_name(name, wav_dir, resolved_wav_dir) == bd._is_safe_wav_name(
            name, wav_dir, resolved_wav_dir
        ), name


# --- review #264 R15 P1: write_lang_def の決定論 tmp パス排除 + rollback
# 保護領域の拡張 (convert_pjs.py:673-674, :920) ------------------------------


def test_write_lang_def_does_not_touch_colliding_deterministic_tmp_name(tmp_path: Path) -> None:
    """`write_lang_def` は旧実装の決定論的 tmp パス（`.{name}.tmp-{pid}`）を
    もはや使わない（`build_dataset._atomic_write_text` の `tempfile.mkstemp`
    排他生成へ共通化）。このパスに無関係な既存ファイルが偶然存在していても、
    書き込みに巻き込まれず内容がそのまま残ることを固定する（intake.py
    `_atomic_write` R14 P1 と同型の欠陥の再発防止）。
    """
    path = tmp_path / "lang.json"
    stray_tmp = tmp_path / f".{path.name}.tmp-{os.getpid()}"
    stray_content = b"unrelated pre-existing file that must survive untouched"
    stray_tmp.write_bytes(stray_content)

    cp.write_lang_def(path)

    assert stray_tmp.read_bytes() == stray_content
    assert json.loads(path.read_text(encoding="utf-8")) == cp.DEFAULT_LANG_DEF
    # mkstemp が生成した実際の tmp ファイルも成功時に replace 済みで残らない。
    leftover = [
        p for p in tmp_path.iterdir()
        if p not in (path, stray_tmp)
    ]
    assert leftover == []


def _stage_all_pjs_dummy(pjs_root: Path) -> None:
    """`stage_song_wavs` は wav 内容を検証せず存在確認のみ行うため、
    PJS_EXPECTED_IDS 全100件分のダミー `.lab`/`_song.wav` を用意すれば
    `main()` の missing_pairs/escaped_pairs fail-closed ガードを通過できる。
    """
    for name in cp.PJS_EXPECTED_IDS:
        d = pjs_root / name
        d.mkdir(parents=True)
        (d / f"{name}.lab").write_text("dummy\n", encoding="utf-8")
        (d / f"{name}_song.wav").write_bytes(b"")


def test_write_lang_def_external_rollback_when_interrupted_after_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """review #264 R15 P1 (convert_pjs.py:920): `write_lang_def` は従来
    `main()` の `try` ブロックの外側で呼ばれており、`_restore_lang_def_backup`
    を呼ぶ `except BaseException` の保護範囲外だった。`write_lang_def` の
    実行が `os.replace` まで完了した直後（関数から戻る前）に
    `KeyboardInterrupt`/`SystemExit` を含む `BaseException` が飛ぶと、
    `--staging-dir` 外を指す外部 `--lang-def` だけが新内容へ更新された
    まま巻き戻されずに残っていた。`write_lang_def` 自体を `try` 内へ
    移したことで、この隙間でも `_restore_lang_def_backup` が発火し、
    実行前のバイト列へ復元されることを固定する（既存ファイルだった場合）。
    """
    pjs_root = tmp_path / "pjs_root"
    _stage_all_pjs_dummy(pjs_root)
    converter_dir = tmp_path / "converter"
    converter_dir.mkdir()
    staging_dir = tmp_path / "staging"
    lang_def_path = tmp_path / "external_lang.json"
    old_content = b'{"pre_existing": true}\n'
    lang_def_path.write_bytes(old_content)

    real_atomic_write_text = cp._atomic_write_text

    def _write_then_interrupt(path: Path, content: str, **kwargs: object) -> None:
        # os.replace までは実行させ（= 外部永続物は新内容へ更新済み）、
        # write_lang_def が呼び出し元へ戻る前に割り込みが飛んだ状況を再現する。
        real_atomic_write_text(path, content, **kwargs)
        raise KeyboardInterrupt()

    monkeypatch.setattr(cp, "_atomic_write_text", _write_then_interrupt)

    with pytest.raises(KeyboardInterrupt):
        cp.main([
            "--pjs-root", str(pjs_root),
            "--converter-dir", str(converter_dir),
            "--staging-dir", str(staging_dir),
            "--lang-def", str(lang_def_path),
        ])

    assert lang_def_path.read_bytes() == old_content


def test_write_lang_def_external_new_file_removed_when_interrupted_after_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """上のテストの「実行前は外部 lang-def が存在しなかった」変種。
    `write_lang_def` が新規作成した直後に割り込まれても、`created_new=True`
    分岐（review #263 R11）により作成物が削除され「実行前は存在しなかった」
    状態へ戻ることを固定する。
    """
    pjs_root = tmp_path / "pjs_root"
    _stage_all_pjs_dummy(pjs_root)
    converter_dir = tmp_path / "converter"
    converter_dir.mkdir()
    staging_dir = tmp_path / "staging"
    lang_def_path = tmp_path / "external_lang.json"
    assert not lang_def_path.exists()

    real_atomic_write_text = cp._atomic_write_text

    def _write_then_interrupt(path: Path, content: str, **kwargs: object) -> None:
        real_atomic_write_text(path, content, **kwargs)
        raise KeyboardInterrupt()

    monkeypatch.setattr(cp, "_atomic_write_text", _write_then_interrupt)

    with pytest.raises(KeyboardInterrupt):
        cp.main([
            "--pjs-root", str(pjs_root),
            "--converter-dir", str(converter_dir),
            "--staging-dir", str(staging_dir),
            "--lang-def", str(lang_def_path),
        ])

    assert not lang_def_path.exists()


# --- convert_pjs._swap_into_place: 無関係な既存 .old ディレクトリの保護
# (review #264 R25 P1, convert_pjs.py:1216 スレッド + 本文直書き P1) ---------
#
# `_swap_into_place` は `<staging_dir>.old` が既存の場合、それが本ツールが
# 過去の swap で作った退避物である保証なしに無条件で `shutil.rmtree` して
# いた。決定論的なパスへ本ツールと無関係なディレクトリが偶然存在していた
# 場合、検証なしに丸ごと削除してしまう破壊経路（`gate_synth.py` の
# `_swap_step_dir_into_place` と同型。同時修正）。


def test_swap_into_place_rejects_unrelated_old_dir(tmp_path: Path) -> None:
    """本ツールが作った退避物であることを示すマーカーを持たない
    `<staging_dir>.old` は fail-closed 拒否され、無変更のまま残る（削除
    されない）。"""
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    (staging_dir / "existing.txt").write_text("old generation", encoding="utf-8")

    old_dir = tmp_path / "staging.old"
    old_dir.mkdir()
    (old_dir / "unrelated.txt").write_text("not created by this tool", encoding="utf-8")

    build_dir = tmp_path / "staging.build-123"
    build_dir.mkdir()
    (build_dir / "new.txt").write_text("new generation", encoding="utf-8")

    with pytest.raises(SystemExit):
        cp._swap_into_place(build_dir, staging_dir)

    # 無関係な old_dir は削除されず中身も無変更のまま。
    assert old_dir.exists()
    assert (old_dir / "unrelated.txt").read_text(encoding="utf-8") == "not created by this tool"
    # staging_dir も swap されず旧世代のまま公開は起きていない。
    assert (staging_dir / "existing.txt").exists()
    assert not (staging_dir / "new.txt").exists()


def test_swap_into_place_rotates_own_backup_across_three_runs(tmp_path: Path) -> None:
    """本ツール自身が過去の swap で作った `.old`（マーカー付き）は、通常
    どおりローテートされて削除される（正常系の回帰ガード）。

    マーカーは `build_dir` -> `staging_dir` -> `.old` と rename でしか
    運ばれないため、`.old` にマーカーが乗るのは「`staging_dir` 自体が
    本ツールの swap 経由で作られていた」場合のみ。初回 swap
    （`staging_dir` 未存在）の直後にできる `staging_dir` にはこの時点で
    まだマーカーが付いているが、`.old` はまだ存在しない。2 回目 swap で
    初めて `.old`（マーカー付き）が生まれ、3 回目 swap でそのローテート
    を検証できる。
    """
    staging_dir = tmp_path / "staging"  # 初回 swap 前は未存在（クリーンな初回実行）
    old_dir = tmp_path / "staging.old"

    build_dir1 = tmp_path / "staging.build-1"
    build_dir1.mkdir()
    (build_dir1 / "gen1.txt").write_text("gen1", encoding="utf-8")
    cp._swap_into_place(build_dir1, staging_dir)
    assert (staging_dir / "gen1.txt").exists()
    assert not old_dir.exists()  # 初回は退避対象が無い

    build_dir2 = tmp_path / "staging.build-2"
    build_dir2.mkdir()
    (build_dir2 / "gen2.txt").write_text("gen2", encoding="utf-8")
    cp._swap_into_place(build_dir2, staging_dir)
    assert (staging_dir / "gen2.txt").exists()
    assert old_dir.exists()
    assert (old_dir / "gen1.txt").exists()  # 2回目で初めて .old (マーカー付き) が生まれる

    build_dir3 = tmp_path / "staging.build-3"
    build_dir3.mkdir()
    (build_dir3 / "gen3.txt").write_text("gen3", encoding="utf-8")
    cp._swap_into_place(build_dir3, staging_dir)  # 3回目: 前回の .old を安全にローテート

    assert (staging_dir / "gen3.txt").exists()
    assert old_dir.exists()
    assert (old_dir / "gen2.txt").exists()
    assert not (old_dir / "gen1.txt").exists()


# --- convert_pjs.main(): swap 完了判定のファイルシステム導出
# (review #264 R25 P2, convert_pjs.py:1216) ----------------------------------
#
# 従来は `_swap_into_place()` 呼び出し**直後**の `swapped = True` という
# 後続代入変数で「swap が完了したか」を判定していた。`_swap_into_place()`
# 自体が正常 return した直後・この代入文実行前に `KeyboardInterrupt`/
# `SystemExit` が飛ぶと、実際には公開 rename が完了しているのに `swapped`
# は `False` のままハンドラへ落ち、公開済みの新世代コーパスに対して外部
# `--lang-def` だけ旧内容へ巻き戻してしまう（新旧混成生成）。修正後は
# `build_dir.exists()` という実ファイルシステム状態から completion を
# 導出する。


def _fake_run_converter_success(pjs_expected_ids, write_wav_fn):
    """`run_converter`（実 `db_converter.py` subprocess 呼び出し）を軽量に
    フェイクする。実変換器を使わず、`build_dir/diffsinger_db/` 配下へ
    validate_speaker 等の下流検査を全通貨する最小構成（曲ごとに1セグメント
    ・1音素、wav 長と ph_dur が厳密一致）を直接生成する。"""
    import subprocess as _subprocess

    def _fake(converter_dir: Path, build_dir: Path, lang_def_path: Path, python_bin: str):
        out_dir = build_dir / "diffsinger_db"
        wav_dir = out_dir / "wavs"
        wav_dir.mkdir(parents=True)
        rows = []
        for song_id in pjs_expected_ids:
            name = f"{song_id}_seg000"
            write_wav_fn(wav_dir / f"{name}.wav", 0.01, sr=8000)
            rows.append({"name": name, "ph_seq": "a", "ph_dur": "0.01"})
        csv_path = out_dir / "transcriptions.csv"
        import csv as _csv

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = _csv.DictWriter(f, fieldnames=["name", "ph_seq", "ph_dur"])
            writer.writeheader()
            writer.writerows(rows)
        return _subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    return _fake


def test_swap_completion_from_filesystem_keeps_new_lang_def_when_swap_already_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """review #264 R25 P2 の再現の裏返し（修正後の期待挙動）: swap が
    実際には完了した直後に割り込まれても、公開済みの新世代コーパスに対して
    外部 lang-def を旧内容へ巻き戻してはならない（新世代で統一）。"""
    pjs_root = tmp_path / "pjs_root"
    _stage_all_pjs_dummy(pjs_root)
    converter_dir = tmp_path / "converter"
    converter_dir.mkdir()
    staging_dir = tmp_path / "staging"
    lang_def_path = tmp_path / "external_lang.json"
    old_content = b'{"pre_existing": true}\n'
    lang_def_path.write_bytes(old_content)

    monkeypatch.setattr(cp, "run_converter", _fake_run_converter_success(cp.PJS_EXPECTED_IDS, _write_wav))

    real_swap_into_place = cp._swap_into_place

    def _swap_then_interrupt(build_dir: Path, staging_dir_arg: Path) -> None:
        # 実 swap（rename の原子性そのもの）は完走させ、`_swap_into_place`
        # が呼び出し元へ正常 return した直後・`swapped = True` 相当の
        # タイミングで割り込みが飛んだ状況を再現する。
        real_swap_into_place(build_dir, staging_dir_arg)
        raise KeyboardInterrupt()

    monkeypatch.setattr(cp, "_swap_into_place", _swap_then_interrupt)

    with pytest.raises(KeyboardInterrupt):
        cp.main([
            "--pjs-root", str(pjs_root),
            "--converter-dir", str(converter_dir),
            "--staging-dir", str(staging_dir),
            "--lang-def", str(lang_def_path),
        ])

    # 公開（swap）自体は完了しているため、コーパスは新世代のまま公開される。
    assert (staging_dir / "diffsinger_db" / "transcriptions.csv").exists()
    # lang-def は巻き戻されず新内容のまま（新世代で統一。混成生成にならない）。
    assert lang_def_path.read_bytes() != old_content
    assert json.loads(lang_def_path.read_text(encoding="utf-8")) == cp.DEFAULT_LANG_DEF


def test_swap_completion_from_filesystem_restores_old_lang_def_when_swap_did_not_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回帰ガード: swap が実際には完了していない中断では、従来どおり外部
    lang-def を実行前の内容へ巻き戻す（旧世代で統一。修正がこの既存経路を
    壊していないことを固定する）。"""
    pjs_root = tmp_path / "pjs_root"
    _stage_all_pjs_dummy(pjs_root)
    converter_dir = tmp_path / "converter"
    converter_dir.mkdir()
    staging_dir = tmp_path / "staging"
    lang_def_path = tmp_path / "external_lang.json"
    old_content = b'{"pre_existing": true}\n'
    lang_def_path.write_bytes(old_content)

    monkeypatch.setattr(cp, "run_converter", _fake_run_converter_success(cp.PJS_EXPECTED_IDS, _write_wav))

    def _swap_never_runs(build_dir: Path, staging_dir_arg: Path) -> None:
        # swap 自体を一切実行せずに中断（= build_dir はそのまま存在し続ける）。
        raise KeyboardInterrupt()

    monkeypatch.setattr(cp, "_swap_into_place", _swap_never_runs)

    with pytest.raises(KeyboardInterrupt):
        cp.main([
            "--pjs-root", str(pjs_root),
            "--converter-dir", str(converter_dir),
            "--staging-dir", str(staging_dir),
            "--lang-def", str(lang_def_path),
        ])

    # swap が起きていないため staging_dir は旧世代（未公開）のまま。
    assert not (staging_dir / "diffsinger_db").exists()
    # lang-def は実行前の内容へ巻き戻される。
    assert lang_def_path.read_bytes() == old_content


# --- build_dataset.validate_speaker: 空 ph_seq/ph_dur の同時空受理
# (review #264 R25 P2 本文直書き, build_dataset.py:658-660) ------------------
#
# `ph_seq=""` かつ `ph_dur=""` はいずれも空リストへ parse され、長さ一致
# 検査（0 == 0）を素通りする。必須の整列フィールドが両方空という構造不良を
# 明示的に検出する。


def test_validate_speaker_rejects_both_ph_seq_and_ph_dur_empty(tmp_path: Path) -> None:
    """再現ケースそのもの: ph_seq/ph_dur が両方空文字列の行は、長さ一致
    （0==0）・非正/非有限（空リストに対して no-op）のいずれも素通りする
    ため、旧実装では一切の violation を出さず公開されてしまっていた。"""
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "seg000.wav", 1.0)
    rows = [{"name": "seg000", "ph_seq": "", "ph_dur": ""}]
    problems = bd.validate_speaker("pjs", tmp_path, rows)
    assert any("seg000" in p and "empty ph_seq/ph_dur" in p for p in problems)


def test_validate_speaker_rejects_ph_seq_empty_ph_dur_nonempty(tmp_path: Path) -> None:
    """片方だけ空のケース（長さ不一致にもなるため、既存の length-mismatch
    ではなく新設の empty 検査で先に検出されることを確認する）。"""
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "seg001.wav", 1.0)
    rows = [{"name": "seg001", "ph_seq": "", "ph_dur": "1.0"}]
    problems = bd.validate_speaker("pjs", tmp_path, rows)
    assert any("seg001" in p and "empty ph_seq/ph_dur" in p for p in problems)


def test_validate_speaker_accepts_normal_nonempty_ph_seq_ph_dur(tmp_path: Path) -> None:
    """正常系の回帰ガード: 通常どおり非空の ph_seq/ph_dur は empty 検査に
    引っかからない。"""
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "seg002.wav", 1.0)
    rows = [{"name": "seg002", "ph_seq": "a", "ph_dur": "1.0"}]
    problems = bd.validate_speaker("pjs", tmp_path, rows)
    assert not any("empty ph_seq/ph_dur" in p for p in problems)


# --- build_dataset.validate_speaker: 読めない/非正 duration WAV の
# 無条件違反化 (review #264 R25 P2, build_dataset.py:655 スレッド) -----------
#
# 従来の `missing` は `.exists()` のみを見るため、WAV が存在するが読めない
# （非 PCM/破損）・duration<=0 の場合を検出できず、`check_ph_dur_duration`
# 側も readback 失敗を「比較スキップの合図」として黙って continue するのみ
# だった（`--strict-duration` 未指定時は既定で警告扱いに留まる 2 段階仕様
# 自体は妥当だが、readback 失敗そのものは無条件で violation にする）。


def test_validate_speaker_rejects_corrupt_wav_unconditionally(tmp_path: Path) -> None:
    """破損 WAV（wave モジュールで open できない内容）は `--strict-duration`
    の有無に関わらず常に violation として検出される。"""
    wav_dir = tmp_path / "wavs"
    wav_dir.mkdir()
    (wav_dir / "seg000.wav").write_bytes(b"not a real wav file, corrupt header")
    rows = [{"name": "seg000", "ph_seq": "a", "ph_dur": "1.0"}]
    problems = bd.validate_speaker("pjs", tmp_path, rows)
    assert any("seg000" in p and "unreadable or non-positive duration" in p for p in problems)


def test_validate_speaker_rejects_header_only_zero_duration_wav_unconditionally(
    tmp_path: Path,
) -> None:
    """ヘッダのみで実データフレームが0のWAV（duration=0）も同様に
    unconditional violation として検出される。"""
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "seg001.wav", 0.0)
    rows = [{"name": "seg001", "ph_seq": "a", "ph_dur": "1.0"}]
    problems = bd.validate_speaker("pjs", tmp_path, rows)
    assert any("seg001" in p and "unreadable or non-positive duration" in p for p in problems)


def test_validate_speaker_accepts_healthy_wav_readback(tmp_path: Path) -> None:
    """正常系の回帰ガード: 正常に読める正 duration WAV は readback 検査に
    引っかからない。"""
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "seg002.wav", 1.0)
    rows = [{"name": "seg002", "ph_seq": "a", "ph_dur": "1.0"}]
    problems = bd.validate_speaker("pjs", tmp_path, rows)
    assert not any("unreadable or non-positive duration" in p for p in problems)


def test_validate_speaker_does_not_double_report_missing_wav_as_unreadable(
    tmp_path: Path,
) -> None:
    """`missing`（.exists()==False）と `unreadable`（readback 失敗）は排他:
    存在しない wav は missing 側でのみ報告され、unreadable 側では二重
    報告されない。"""
    wav_dir = tmp_path / "wavs"
    wav_dir.mkdir()
    rows = [{"name": "nope", "ph_seq": "a", "ph_dur": "1.0"}]
    problems = bd.validate_speaker("pjs", tmp_path, rows)
    assert any("wav missing" in p for p in problems)
    assert not any("unreadable or non-positive duration" in p for p in problems)
