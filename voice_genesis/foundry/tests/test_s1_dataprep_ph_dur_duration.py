"""test_s1_dataprep_ph_dur_duration.py — ph_dur 合計と実 wav 長の整合検証。

`s1_poison_scan` 捜査（`results_s1/s1_record_2026-08-15.md`）で判明した「申告
ph_dur 合計が実音声長を大きく超過する」構造不良を検出/是正する2機能を検証する:

- `build_dataset.check_ph_dur_duration` / `build_dataset.py` の
  `--strict-duration` 2 段階仕様（既定 warn・fail-closed はオプトイン）
- `convert_pjs.normalize_ph_dur_to_wav_duration`（許容誤差超過 overshoot 行
  のみ末尾切り詰めで正規化。undershoot 行は fail-closed で違反収集し無変更
  のまま返す — review #264 R5。許容内の行は同一オブジェクトのまま完全
  無変更で返す）
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


def test_check_note_dur_consistency_falls_back_to_total_only_when_ph_num_absent() -> None:
    """`ph_num` フィールド自体が存在しない行（列が無い/空）では、従来通り
    合計同士の比較へフォールバックする（malformed 拒否の対象外）。"""
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
    fixed_rows, fix_log, undershoot, unsafe, missing = cp.normalize_ph_dur_to_wav_duration(rows, wav_dir)
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
    fixed_rows, fix_log, undershoot, unsafe, missing = cp.normalize_ph_dur_to_wav_duration(rows, wav_dir)
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
    fixed_rows, fix_log, undershoot, unsafe, missing = cp.normalize_ph_dur_to_wav_duration(rows, wav_dir)
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
    fixed_rows, fix_log, undershoot, unsafe, missing = cp.normalize_ph_dur_to_wav_duration(rows, wav_dir)
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
    fixed_rows, fix_log, undershoot, unsafe, missing = cp.normalize_ph_dur_to_wav_duration(rows, wav_dir)
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
    fixed_rows, fix_log, undershoot, unsafe, missing = cp.normalize_ph_dur_to_wav_duration(rows, wav_dir)
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
    fixed_rows, fix_log, undershoot, unsafe, missing = cp.normalize_ph_dur_to_wav_duration(rows, wav_dir)
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
    fixed_rows, fix_log, undershoot, unsafe, missing = cp.normalize_ph_dur_to_wav_duration(rows, wav_dir)
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
    fixed_rows, fix_log, undershoot, unsafe, missing = cp.normalize_ph_dur_to_wav_duration(rows, wav_dir)
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
    fixed_rows, fix_log, undershoot, unsafe, missing = cp.normalize_ph_dur_to_wav_duration([row], wav_dir)
    assert len(missing) == 1  # main() はこれを検知して return 1 する
    assert fixed_rows[0] is row


def test_normalize_ph_dur_mixed_batch_only_touches_violating_rows(tmp_path: Path) -> None:
    wav_dir = tmp_path / "wavs"
    _write_wav(wav_dir / "good.wav", 3.0)
    _write_wav(wav_dir / "bad.wav", 3.0)
    good = {"name": "good", "ph_seq": "SP a SP", "ph_dur": "1.0 1.0 1.0"}
    bad = {"name": "bad", "ph_seq": "SP a SP", "ph_dur": "1.0 4.0 1.0"}
    fixed_rows, fix_log, undershoot, unsafe, missing = cp.normalize_ph_dur_to_wav_duration([good, bad], wav_dir)
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

    fixed_rows, fix_log, undershoot, unsafe, missing = cp.normalize_ph_dur_to_wav_duration(rows, wav_dir)
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
    fixed_rows, fix_log, undershoot, unsafe, missing = cp.normalize_ph_dur_to_wav_duration(rows, wav_dir)
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
    fixed_rows, fix_log, undershoot, unsafe, missing = cp.normalize_ph_dur_to_wav_duration(rows, wav_dir)
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
    fixed_rows, fix_log, undershoot, unsafe, missing = cp.normalize_ph_dur_to_wav_duration(
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
    fixed_rows, fix_log, undershoot, unsafe, missing = cp.normalize_ph_dur_to_wav_duration([row], wav_dir)
    assert len(unsafe) == 1  # main() はこれを検知して return 1 する
    assert missing == []
    assert fixed_rows[0] is row


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
