"""test_convert_d3.py — S3 Phase B3 `s1_dataprep/convert_d3.py` の検証。

`DESIGN_S3_backfill.md` §2.3 B3 の受け入れ条件（正常変換 / ペア欠落 fail /
duration 乖離 fail / 決定論 2 回一致）を高速・合成フィクスチャで検証する。
実レンダ（`adapter/render.py` 実行）には依存しない。timing CSV の列は
`render.py` `TIMING_CSV_FIELDS`（row_index/row_kind/note_index/phrase_index/
is_phrase_first/is_phrase_last/mora_kana/onset/vowel/midi/note_dur_frames/
note_dur_sec/ph_seq/ph_dur_frames/ph_dur_sec/ph_num）を模した最小フィクス
チャで組み立てる。
"""
from __future__ import annotations

import csv
import hashlib
import sys
import wave
from pathlib import Path
from typing import Dict, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "s1_dataprep"))
import convert_d3  # noqa: E402

# render.py TIMING_CSV_FIELDS と同一の列集合（本テストのフィクスチャも同じ
# ヘッダで書く。実フィールドのうち本変換器が読まない列は空文字で埋める）。
_TIMING_FIELDS = [
    "row_index", "row_kind", "note_index", "phrase_index", "is_phrase_first", "is_phrase_last",
    "mora_kana", "onset", "vowel", "midi", "note_dur_frames", "note_dur_sec",
    "ph_seq", "ph_dur_frames", "ph_dur_sec", "ph_num",
]

_FFMPEG_MISSING = convert_d3.FFMPEG_PATH is None
_ffmpeg_required = pytest.mark.skipif(
    _FFMPEG_MISSING, reason="ffmpeg が見つからない環境ではスキップ（resample_to_44k1 依存）"
)


def _note_row(row_index: int, *, midi: float, ph_seq: str, ph_dur_sec: str, ph_num: int,
              note_dur_sec: float) -> Dict[str, str]:
    return {
        "row_index": str(row_index), "row_kind": "note", "note_index": str(row_index),
        "phrase_index": "0", "is_phrase_first": "True", "is_phrase_last": "True",
        "mora_kana": "", "onset": "", "vowel": "",
        "midi": str(midi), "note_dur_frames": "", "note_dur_sec": str(note_dur_sec),
        "ph_seq": ph_seq, "ph_dur_frames": "", "ph_dur_sec": ph_dur_sec, "ph_num": str(ph_num),
    }


def _rest_row(row_index: int, *, ph_dur_sec: float) -> Dict[str, str]:
    return {
        "row_index": str(row_index), "row_kind": "rest", "note_index": "",
        "phrase_index": "", "is_phrase_first": "", "is_phrase_last": "",
        "mora_kana": "", "onset": "", "vowel": "",
        "midi": "", "note_dur_frames": "", "note_dur_sec": str(ph_dur_sec),
        "ph_seq": "SP", "ph_dur_frames": "", "ph_dur_sec": str(ph_dur_sec), "ph_num": "1",
    }


def _write_timing_csv(path: Path, rows: List[Dict[str, str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_TIMING_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_wav(path: Path, duration_sec: float, *, rate: int = 24000) -> None:
    """`rate` Hz mono PCM_16 の無音 wav を `duration_sec` 秒ぶん書く
    （内容は変換器の対象外 — duration 整合検査は長さのみを見る）。"""
    n_frames = round(duration_sec * rate)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(b"\x00\x00" * n_frames)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# フィクスチャ: 1 wav = 1 note(1音素) + 1 rest + 1 note(2音素) = 2.5s
# ---------------------------------------------------------------------------


def _make_cell_a(render_dir: Path) -> None:
    rows = [
        _note_row(0, midi=60.0, ph_seq="a", ph_dur_sec="1.000000", ph_num=1, note_dur_sec=1.0),
        _rest_row(1, ph_dur_sec=0.5),
        _note_row(
            2, midi=64.0, ph_seq="k a", ph_dur_sec="0.300000 0.700000", ph_num=2, note_dur_sec=1.0
        ),
    ]
    _write_timing_csv(render_dir / "cellA.csv", rows)
    _write_wav(render_dir / "cellA.wav", 2.5)


def _make_cell_b(render_dir: Path) -> None:
    rows = [
        _note_row(0, midi=57.0, ph_seq="i", ph_dur_sec="0.800000", ph_num=1, note_dur_sec=0.8),
    ]
    _write_timing_csv(render_dir / "cellB.csv", rows)
    _write_wav(render_dir / "cellB.wav", 0.8)


# ---------------------------------------------------------------------------
# 1. 正常変換
# ---------------------------------------------------------------------------


def test_build_transcription_row_note_and_rest_formatting(tmp_path: Path) -> None:
    """note 行 -> midi 丸め文字列 / rest 行 -> "rest" トークン / ph_dur・note_dur
    は `str(round(d, 12))` 慣行（`convert_pjs.py` と同一）で連結されること。"""
    _make_cell_a(tmp_path)
    row = convert_d3.build_transcription_row(
        "cellA", tmp_path / "cellA.csv", tmp_path / "cellA.wav",
        convert_d3.DEFAULT_DURATION_TOLERANCE_SEC,
    )
    assert row["name"] == "cellA"
    assert row["ph_seq"] == "a SP k a"
    assert row["ph_dur"] == "1.0 0.5 0.3 0.7"
    assert row["ph_num"] == "1 1 2"
    assert row["note_seq"] == "60 rest 64"
    assert row["note_dur"] == "1.0 0.5 1.0"


@_ffmpeg_required
def test_convert_end_to_end_publishes_dataset(tmp_path: Path) -> None:
    render_dir = tmp_path / "render"
    render_dir.mkdir()
    _make_cell_a(render_dir)
    _make_cell_b(render_dir)
    out_dir = tmp_path / "out"

    summary = convert_d3.convert(render_dir, out_dir)

    assert summary["n_segments"] == 2
    assert summary["n_note_rows"] == 3  # cellA: 2 note rows + cellB: 1 note row
    assert summary["n_rest_rows"] == 1

    csv_path = out_dir / "transcriptions.csv"
    assert csv_path.exists()
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    names = {r["name"] for r in rows}
    assert names == {"cellA", "cellB"}

    for stem in ("cellA", "cellB"):
        wav_path = out_dir / "wavs" / f"{stem}.wav"
        assert wav_path.exists()
        with wave.open(str(wav_path), "rb") as w:
            assert w.getframerate() == 44100
            assert w.getsampwidth() == 2


@_ffmpeg_required
def test_convert_output_passes_build_dataset_gates(tmp_path: Path) -> None:
    """`build_dataset.py` の 3 ゲート（validate_speaker / check_ph_dur_duration
    / check_note_dur_consistency）を D3 出力データセットに対して直接呼び出す
    （build_dataset.py 自身は改変しない。read-only import）。"""
    import build_dataset

    render_dir = tmp_path / "render"
    render_dir.mkdir()
    _make_cell_a(render_dir)
    _make_cell_b(render_dir)
    out_dir = tmp_path / "out"
    convert_d3.convert(render_dir, out_dir)

    rows = build_dataset.read_transcriptions(out_dir / "transcriptions.csv")
    problems = build_dataset.validate_speaker("d3", out_dir, rows)
    problems += build_dataset.check_ph_dur_duration("d3", out_dir / "wavs", rows)
    problems += build_dataset.check_note_dur_consistency("d3", rows)
    assert problems == []


# ---------------------------------------------------------------------------
# 2.6 P1 修正 (review #265 R7): wav の単一 read 束縛（duration 検査 と
# resample が同じスナップショットのみを読む・TOCTOU 対策）
# ---------------------------------------------------------------------------


@_ffmpeg_required
def test_resample_receives_snapshot_path_not_original_render_dir_path(
    tmp_path: Path, monkeypatch
) -> None:
    """`resample_to_44k1` へ渡される `src` は `render_dir` 直下の原本ではなく
    `.snapshot-<pid>` 配下のスナップショットである（`_snapshot_wav_pairs`
    の配線確認）。"""
    render_dir = tmp_path / "render"
    render_dir.mkdir()
    _make_cell_a(render_dir)
    out_dir = tmp_path / "out"

    captured_srcs = []
    real_resample = convert_d3.resample_to_44k1

    def _capture(src, dst, ffmpeg_bin=None):
        captured_srcs.append(Path(src))
        return real_resample(src, dst, ffmpeg_bin=ffmpeg_bin)

    monkeypatch.setattr(convert_d3, "resample_to_44k1", _capture)

    convert_d3.convert(render_dir, out_dir)

    assert len(captured_srcs) == 1
    assert captured_srcs[0].parent != render_dir
    assert ".snapshot-" in captured_srcs[0].parent.name


@_ffmpeg_required
def test_convert_publishes_snapshot_bytes_even_if_render_dir_wav_mutated_after_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    """P1 修正 (review #265 R7) の核心シナリオ: duration 検査の実行タイミング
    （snapshot 取得後）で `render_dir` 原本の wav が別内容へ差し替わっても、
    実際に公開される wav バイト列は snapshot 取得時点のまま変化しない
    （旧実装は duration 検査と resample が別々に `render_dir` 原本を open
    していたため、この間の差し替えが公開バイト列に反映されうる TOCTOU
    だった）。"""
    render_dir = tmp_path / "render"
    render_dir.mkdir()
    _make_cell_a(render_dir)
    out_dir = tmp_path / "out"

    real_wav_duration_seconds = convert_d3._wav_duration_seconds
    mutated = {"done": False}

    def _mutate_render_dir_then_measure(path):
        # この時点で `path` は既にスナップショットのはず（render_dir 原本
        # ではない）。副作用として render_dir 原本を別内容（長さの異なる
        # wav — 無音同士でも resample 後のバイト長が変わるため確実に検出
        # できる）へ差し替える。
        if not mutated["done"]:
            _write_wav(render_dir / "cellA.wav", 5.0)
            mutated["done"] = True
        return real_wav_duration_seconds(path)

    monkeypatch.setattr(convert_d3, "_wav_duration_seconds", _mutate_render_dir_then_measure)

    convert_d3.convert(render_dir, out_dir)
    assert mutated["done"]  # mutation フックが実際に発火したことの確認

    published_bytes = (out_dir / "wavs" / "cellA.wav").read_bytes()

    # 差し替え後の render_dir 原本を素朴に resample した場合とは一致しない
    # （= 公開されたのが snapshot 由来のバイト列であることの証拠）。
    direct_from_mutated = tmp_path / "direct_from_mutated.wav"
    convert_d3.resample_to_44k1(render_dir / "cellA.wav", direct_from_mutated)
    assert published_bytes != direct_from_mutated.read_bytes()


# ---------------------------------------------------------------------------
# 2. ペア不整合 fail-closed
# ---------------------------------------------------------------------------


def test_discover_pairs_missing_csv_fails_closed(tmp_path: Path) -> None:
    _write_wav(tmp_path / "orphanWav.wav", 1.0)
    with pytest.raises(convert_d3.PairDiscoveryError, match="orphanWav"):
        convert_d3.discover_pairs(tmp_path)


def test_discover_pairs_missing_wav_fails_closed(tmp_path: Path) -> None:
    _write_timing_csv(
        tmp_path / "orphanCsv.csv",
        [_note_row(0, midi=60.0, ph_seq="a", ph_dur_sec="1.0", ph_num=1, note_dur_sec=1.0)],
    )
    with pytest.raises(convert_d3.PairDiscoveryError, match="orphanCsv"):
        convert_d3.discover_pairs(tmp_path)


def test_convert_with_missing_pair_publishes_nothing(tmp_path: Path) -> None:
    render_dir = tmp_path / "render"
    render_dir.mkdir()
    _make_cell_a(render_dir)
    _write_wav(render_dir / "orphan.wav", 1.0)  # csv 無し
    out_dir = tmp_path / "out"

    with pytest.raises(convert_d3.PairDiscoveryError):
        convert_d3.convert(render_dir, out_dir)
    assert not out_dir.exists()


# ---------------------------------------------------------------------------
# 2.5 P1 修正 (review #265): 衝突検査を convert() 自身が行う（CLI 経由でなくても
# fail-closed）+ discover_pairs() 0 件（空データセット）を fail-closed
# ---------------------------------------------------------------------------


def test_convert_rejects_out_dir_colliding_with_render_dir_without_cli(tmp_path: Path) -> None:
    """CLI `main()` を経由しない直接呼び出しでも `--out-dir` が `render_dir`
    と衝突していれば `convert()` 自身が fail-closed で拒否する（review #265
    P1: 旧実装は CLI のみが preflight していたため、非 CLI 経路はこの検査を
    素通りしていた）。"""
    render_dir = tmp_path / "render"
    render_dir.mkdir()
    _make_cell_a(render_dir)

    with pytest.raises(convert_d3.OutputCollisionError):
        convert_d3.convert(render_dir, render_dir)


def test_convert_rejects_out_dir_inside_render_dir_without_cli(tmp_path: Path) -> None:
    render_dir = tmp_path / "render"
    render_dir.mkdir()
    _make_cell_a(render_dir)
    out_dir = render_dir / "nested_out"

    with pytest.raises(convert_d3.OutputCollisionError):
        convert_d3.convert(render_dir, out_dir)


# ---------------------------------------------------------------------------
# 2.6 P1 修正 (review #265 R3): _reject_output_collision の protected_files は
# 完全一致だけでなく双方向の包含判定を行う（out_path が保護ファイルの祖先
# ディレクトリの場合も fail-closed）
# ---------------------------------------------------------------------------


def test_reject_output_collision_protected_file_exact_match_raises(tmp_path: Path) -> None:
    protected_file = tmp_path / "ledger.json"
    protected_file.write_text("{}", encoding="utf-8")

    with pytest.raises(convert_d3.OutputCollisionError):
        convert_d3._reject_output_collision(
            [protected_file], protected_roots=[], protected_files=[protected_file]
        )


def test_reject_output_collision_out_path_is_ancestor_of_protected_file_raises(tmp_path: Path) -> None:
    """`out_path` が保護ファイルの祖先ディレクトリの場合、完全一致でなくても
    fail-closed で拒否する（R3 P1 修正: `_swap_into_place` が `out_dir` を
    `.old` へ rename する際に保護ファイルごと退避・次回実行時の `rmtree` で
    消失し得るため。旧実装（完全一致のみ）はこのケースを見逃していた）。"""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    protected_file = out_dir / "ledger.json"  # out_dir の**内側**にある保護ファイル
    protected_file.write_text("{}", encoding="utf-8")

    with pytest.raises(convert_d3.OutputCollisionError):
        convert_d3._reject_output_collision(
            [out_dir], protected_roots=[], protected_files=[protected_file]
        )


def test_reject_output_collision_out_path_is_sibling_of_protected_file_does_not_raise(
    tmp_path: Path,
) -> None:
    """`out_path` が保護ファイルと同じ親ディレクトリの**兄弟**パスであるだけ
    なら誤検知しない（一般的な運用: 台帳/辞書と出力先を同じ scratch
    ディレクトリ直下に置く）。"""
    protected_file = tmp_path / "ledger.json"
    protected_file.write_text("{}", encoding="utf-8")
    out_dir = tmp_path / "out_sibling"

    convert_d3._reject_output_collision(
        [out_dir], protected_roots=[], protected_files=[protected_file]
    )  # no raise


def test_convert_empty_render_dir_fails_closed_without_publishing(tmp_path: Path) -> None:
    """`discover_pairs()` が 0 件（wav/csv が 1 本も無い render_dir）の場合、
    staging 構築・swap の前に fail-closed で拒否する（空データセットで既存
    `out_dir` を黙って置き換える false-success の防止）。"""
    render_dir = tmp_path / "render"
    render_dir.mkdir()  # 空のまま
    out_dir = tmp_path / "out"

    with pytest.raises(convert_d3.EmptyDatasetError):
        convert_d3.convert(render_dir, out_dir)
    assert not out_dir.exists()


@_ffmpeg_required
def test_convert_empty_render_dir_does_not_clobber_existing_out_dir(tmp_path: Path) -> None:
    """既存の `out_dir`（前回の正常な変換結果）がある状態で、空の render_dir
    から再度 `convert()` を呼んでも既存の出力は破壊されない（false-success
    防止の実害シナリオ）。"""
    render_dir = tmp_path / "render"
    render_dir.mkdir()
    _make_cell_a(render_dir)
    out_dir = tmp_path / "out"
    convert_d3.convert(render_dir, out_dir)
    existing_csv_bytes = (out_dir / "transcriptions.csv").read_bytes()

    empty_render_dir = tmp_path / "render_empty"
    empty_render_dir.mkdir()
    with pytest.raises(convert_d3.EmptyDatasetError):
        convert_d3.convert(empty_render_dir, out_dir)

    assert (out_dir / "transcriptions.csv").read_bytes() == existing_csv_bytes


# ---------------------------------------------------------------------------
# 3. duration 乖離 fail-closed
# ---------------------------------------------------------------------------


def test_duration_mismatch_beyond_tolerance_fails_closed(tmp_path: Path) -> None:
    _make_cell_a(tmp_path)  # ph_dur 合計 2.5s
    # wav 実長を 2.5s -> 2.7s へずらす（乖離 200ms > 既定許容 50ms）。
    _write_wav(tmp_path / "cellA.wav", 2.7)

    with pytest.raises(convert_d3.DurationMismatchError, match=r"diff 0\.2"):
        convert_d3.build_transcription_row(
            "cellA", tmp_path / "cellA.csv", tmp_path / "cellA.wav",
            convert_d3.DEFAULT_DURATION_TOLERANCE_SEC,
        )


def test_duration_mismatch_within_tolerance_passes(tmp_path: Path) -> None:
    _make_cell_a(tmp_path)  # ph_dur 合計 2.5s
    # 20ms 級の乖離（実測 kana フィクスチャと同オーダー）は既定許容内。
    _write_wav(tmp_path / "cellA.wav", 2.52)

    row = convert_d3.build_transcription_row(
        "cellA", tmp_path / "cellA.csv", tmp_path / "cellA.wav",
        convert_d3.DEFAULT_DURATION_TOLERANCE_SEC,
    )
    assert row["name"] == "cellA"


# ---------------------------------------------------------------------------
# 3.5 P2 修正 (review #265 R5): duration_tolerance_sec の nan/inf/負値は
# fail-closed（乖離検査 `diff > duration_tolerance_sec` の常時 False 素通り
# を防止）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf"), -0.01])
def test_convert_rejects_non_finite_or_negative_duration_tolerance(
    tmp_path: Path, bad_value: float
) -> None:
    """`nan`/`+inf` は `diff > duration_tolerance_sec` を常時 `False` にして
    乖離検査を無条件で素通りさせる（NaN との比較は Python では常に
    `False`）。`-inf`/負値も無意味な入力のため、まとめて公開 API 入口で
    fail-closed 拒否する。"""
    render_dir = tmp_path / "render"
    render_dir.mkdir()
    _make_cell_a(render_dir)
    out_dir = tmp_path / "out"

    with pytest.raises(convert_d3.InvalidDurationToleranceError):
        convert_d3.convert(render_dir, out_dir, duration_tolerance_sec=bad_value)
    assert not out_dir.exists()


def test_convert_rejects_nan_duration_tolerance_even_with_grossly_mismatched_wav(
    tmp_path: Path,
) -> None:
    """実害の再現: `duration_tolerance_sec=nan` を許すと、実際には秒単位で
    ずれた wav でも `diff > nan` が常に `False` のため乖離検査を通過して
    しまう（修正前の挙動）。修正後は乖離を評価するより前に
    `InvalidDurationToleranceError` で拒否され、決してこの wav が公開
    されない。"""
    render_dir = tmp_path / "render"
    render_dir.mkdir()
    _make_cell_a(render_dir)
    _write_wav(render_dir / "cellA.wav", 999.0)  # 極端な乖離（996.5s）
    out_dir = tmp_path / "out"

    with pytest.raises(convert_d3.InvalidDurationToleranceError):
        convert_d3.convert(render_dir, out_dir, duration_tolerance_sec=float("nan"))
    assert not out_dir.exists()


@_ffmpeg_required
def test_convert_zero_duration_tolerance_is_accepted_as_finite_nonnegative(tmp_path: Path) -> None:
    """境界値 `0.0` は有限かつ非負のため許可される（`< 0.0` であって
    `<= 0.0` ではないことの確認）。`cellA` フィクスチャは wav 実長と
    `ph_dur` 合計が厳密に一致するため、`tolerance=0.0` でも
    `InvalidDurationToleranceError`/`DurationMismatchError` いずれも出さず
    正常に変換が完走する。"""
    render_dir = tmp_path / "render"
    render_dir.mkdir()
    _make_cell_a(render_dir)
    out_dir = tmp_path / "out"

    summary = convert_d3.convert(render_dir, out_dir, duration_tolerance_sec=0.0)
    assert summary["n_segments"] == 1


@_ffmpeg_required
def test_convert_end_to_end_duration_mismatch_publishes_nothing(tmp_path: Path) -> None:
    render_dir = tmp_path / "render"
    render_dir.mkdir()
    _make_cell_a(render_dir)
    _write_wav(render_dir / "cellA.wav", 2.7)  # 上書き: 200ms 乖離
    out_dir = tmp_path / "out"

    with pytest.raises(convert_d3.MalformedTimingCsvError):
        convert_d3.convert(render_dir, out_dir)
    assert not out_dir.exists()


# ---------------------------------------------------------------------------
# 4. 決定論: 同一入力 -> 同一出力バイト列
# ---------------------------------------------------------------------------


@_ffmpeg_required
def test_convert_is_deterministic_across_two_runs(tmp_path: Path) -> None:
    render_dir = tmp_path / "render"
    render_dir.mkdir()
    _make_cell_a(render_dir)
    _make_cell_b(render_dir)

    out_dir_1 = tmp_path / "out1"
    out_dir_2 = tmp_path / "out2"
    convert_d3.convert(render_dir, out_dir_1)
    convert_d3.convert(render_dir, out_dir_2)

    csv_1 = (out_dir_1 / "transcriptions.csv").read_bytes()
    csv_2 = (out_dir_2 / "transcriptions.csv").read_bytes()
    assert csv_1 == csv_2

    for stem in ("cellA", "cellB"):
        h1 = _sha256(out_dir_1 / "wavs" / f"{stem}.wav")
        h2 = _sha256(out_dir_2 / "wavs" / f"{stem}.wav")
        assert h1 == h2


# ---------------------------------------------------------------------------
# 5. malformed timing csv
# ---------------------------------------------------------------------------


def test_malformed_ph_num_mismatch_fails_closed(tmp_path: Path) -> None:
    rows = [
        _note_row(0, midi=60.0, ph_seq="k a", ph_dur_sec="0.3 0.7", ph_num=1, note_dur_sec=1.0),
    ]
    _write_timing_csv(tmp_path / "bad.csv", rows)
    _write_wav(tmp_path / "bad.wav", 1.0)
    with pytest.raises(convert_d3.MalformedTimingCsvError, match="length mismatch"):
        convert_d3.build_transcription_row(
            "bad", tmp_path / "bad.csv", tmp_path / "bad.wav",
            convert_d3.DEFAULT_DURATION_TOLERANCE_SEC,
        )


def test_missing_required_column_fails_closed(tmp_path: Path) -> None:
    csv_path = tmp_path / "bad.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["row_index", "row_kind"])
        writer.writeheader()
        writer.writerow({"row_index": "0", "row_kind": "note"})
    _write_wav(tmp_path / "bad.wav", 1.0)
    with pytest.raises(convert_d3.MalformedTimingCsvError, match="missing required column"):
        convert_d3.build_transcription_row(
            "bad", csv_path, tmp_path / "bad.wav", convert_d3.DEFAULT_DURATION_TOLERANCE_SEC,
        )


def test_non_contiguous_row_index_fails_closed(tmp_path: Path) -> None:
    rows = [
        _note_row(0, midi=60.0, ph_seq="a", ph_dur_sec="1.0", ph_num=1, note_dur_sec=1.0),
        _note_row(2, midi=62.0, ph_seq="i", ph_dur_sec="1.0", ph_num=1, note_dur_sec=1.0),
    ]
    _write_timing_csv(tmp_path / "bad.csv", rows)
    _write_wav(tmp_path / "bad.wav", 2.0)
    with pytest.raises(convert_d3.MalformedTimingCsvError, match="contiguous"):
        convert_d3.build_transcription_row(
            "bad", tmp_path / "bad.csv", tmp_path / "bad.wav",
            convert_d3.DEFAULT_DURATION_TOLERANCE_SEC,
        )
