"""S3 Phase B3: D3 レンダ成果物 -> DiffSinger acoustic 学習形式変換器。

`DESIGN_S3_backfill.md` §2.3 B3 の実装。入力は D3 レンダ成果物のディレクトリ
（`adapter/render.py` `--out`/`--timing-out` が書き出す wav 24kHz PCM_16 +
「同名」timing CSV — 拡張子違いのみで stem が一致するペア: `<stem>.wav` +
`<stem>.csv`）。timing CSV の列は `render.py` `TIMING_CSV_FIELDS`（row_index/
row_kind/note_index/phrase_index/is_phrase_first/is_phrase_last/mora_kana/
onset/vowel/midi/note_dur_frames/note_dur_sec/ph_seq/ph_dur_frames/
ph_dur_sec/ph_num）を一次ソースとする。出力は `convert_ritsu.py`/
`convert_pjs.py` と同一流儀の `transcriptions.csv`（列:
name,ph_seq,ph_dur,ph_num,note_seq,note_dur）+ `wavs/`（44.1kHz PCM_16 へ
ffmpeg リサンプル）。

**タイミング計算の二重実装はしない**（`DESIGN_S3_backfill.md` §2.3 B1 の
設計方針）: 本ファイルは `render.py` が計算済みの timing CSV の値
（`ph_dur_sec`/`note_dur_sec`/`midi`/`ph_num`）をそのまま読むだけで、音素
境界や音符長を独自に再計算しない。

## 変換規則

timing CSV の 1 行 = 1 note（歌唱ノート、1 音素以上）または 1 rest（フレーズ
間ブレス、`ph_seq="SP"` の 1 音素）。1 wav（= 1 レンダ = 1 曲/1 フレーズ譜）が
出力 `transcriptions.csv` の 1 行に対応する（`convert_pjs.py` の
`pjsNNN_song.wav` 1 本 = 1 行と同型）。行を `row_index` 昇順に正規化してから
（物理的な書き込み順に依存しない）次のとおり連結する:

- **note 行**: `ph_seq`/`ph_dur_sec` をそのまま音素粒度で `ph_seq`/`ph_dur`
  へ追記し、`ph_num` へ音素数（この note に属する音素数）を追記する。
  `note_seq` へ `midi`（例 `57.0`）を四捨五入した整数の文字列（例 `"57"`）を、
  `note_dur` へ `note_dur_sec` を追記する。
- **rest 行**: `ph_seq`/`ph_dur` へ `"SP"` + その `ph_dur_sec` を追記し、
  `ph_num` へ `1` を追記する。`note_seq` へ `"rest"`（openvpi DiffSinger の
  休符トークン規約。`tests/test_s1_dataprep_ph_dur_duration.py` の
  `note_seq="rest 60 rest"` 等の既存慣行と同一）を、`note_dur` へ
  `note_dur_sec`（= rest の `ph_dur_sec` と同値）を追記する。

`ph_dur`/`note_dur` の小数桁は `convert_pjs.py` `_drop_dead_phonemes` と同一の
慣行（`str(round(d, 12))`）に合わせる（note_seq/note_dur/ph_num を持つ既存
transcriptions.csv 生成箇所はこちらのみのため）。

## duration 整合検査（自前）

`ph_dur` 合計と実 wav 長（変換前・24kHz 側で計測。秒単位のため samplerate に
非依存）の乖離が `--duration-tolerance-sec`（既定 0.05s = 50ms 級、
`DESIGN_S3_backfill.md` §2.3 B3 の閾値）を超えたら fail-closed で拒否する
（`build_dataset.check_ph_dur_duration` の相対5%/絶対0.1s 閾値とは独立の、
本変換器自身の受け入れ検査）。

## リサンプル

`ffmpeg`（house 慣行 = `recording_kit/intake.py` `normalize_to_wav` と同一の
呼び出し形: `-y -i <src> -ac 1 -ar 44100 -sample_fmt s16 <dst>`）で 24kHz ->
44.1kHz PCM_16 へ決定論的に変換する（同一入力 wav に対し 2 回実行した出力の
sha256 が一致することを実測確認済み）。

## 家風の踏襲

`convert_ritsu.py`/`convert_pjs.py` と同型の設計:

- 単一 read 束縛（timing CSV は 1 回だけ読む）
- 全収集してから公開前に fail-closed（ペア不整合・duration 乖離・timing CSV
  の欠損列/型崩れのいずれも、全違反を集めてから例外を送出する）
- `<out-dir>.staging-<pid>` に完全構築してから `<out-dir>` へ原子的に swap
  （`_swap_into_place`: 2 段 rename + 失敗時ロールバック、`convert_ritsu.py`
  と同一実装）
- `--out-dir` が `--render-dir` と衝突・内包関係にある場合は staging 構築前に
  fail-closed で拒否する（`.old`/`.staging-<pid>` 派生パスも対象、
  `convert_ritsu.py` `_reject_output_collision` と同一実装）
- rename/move の成否を Python 変数で記帳しない（swap 後の完了判定は
  呼び出し元がファイルシステム状態を見て行う想定。本ファイル自身も
  `_swap_into_place` 内の `old_dir.exists()` 観測パターンをそのまま踏襲）
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# house 慣行 (`recording_kit/intake.py` FFMPEG_PATH) に合わせ、import 時に
# 一度だけ解決する。無ければ実際に resample が必要になった時点で
# FfmpegNotFoundError を送出する（import 自体は失敗させない）。
FFMPEG_PATH: Optional[str] = shutil.which("ffmpeg")

TARGET_SAMPLE_RATE = 44100

# `render.py` TIMING_CSV_FIELDS のうち本変換器が実際に読む列。
# （row_index は正規化ソート鍵、row_kind は note/rest 分岐、他は §変換規則参照）
REQUIRED_TIMING_FIELDS: Tuple[str, ...] = (
    "row_index", "row_kind", "midi", "note_dur_sec", "ph_seq", "ph_dur_sec", "ph_num",
)

# `DESIGN_S3_backfill.md` §2.3 B3: "duration 不整合...乖離 > 50ms 級は
# fail-closed"。kana フィクスチャ実測（フレーズ間ブレスの端数丸め）で
# 20ms 級の乖離が正常に発生することを確認済みのため、50ms を既定とする。
DEFAULT_DURATION_TOLERANCE_SEC = 0.05


class FfmpegNotFoundError(RuntimeError):
    """ffmpeg バイナリが見つからない場合に送出する（`shutil.which` が None）。"""


class PairDiscoveryError(ValueError):
    """`--render-dir` 内の wav/timing-csv ペアが不整合（片方欠落）の場合に
    送出する（全違反を収集してから公開前に fail-closed）。"""


class MalformedTimingCsvError(ValueError):
    """timing CSV の列欠損・型崩れ・row_index 連番崩れを検出した場合に
    送出する（全違反を収集してから公開前に fail-closed）。"""


class DurationMismatchError(ValueError):
    """`ph_dur` 合計と実 wav 長の乖離が許容誤差を超えた場合に送出する
    （全違反を収集してから公開前に fail-closed）。"""


class OutputCollisionError(ValueError):
    """`--out-dir` が `--render-dir` と衝突・内包関係にある場合に送出する
    （`convert_ritsu.py` `OutputCollisionError` と同型判定。`_swap_into_place`
    の rename/`.old` 退避・rmtree の前に検出する）。"""


class EmptyDatasetError(ValueError):
    """P1 修正 (review #265): `discover_pairs()` が 0 件のとき、staging 構築・
    `_swap_into_place` の前に送出する（fail-closed。空データセットで既存
    `--out-dir` を黙って置き換える false-success を防止する）。"""


# ---------------------------------------------------------------------------
# 1. ペア発見（wav <-> 同名 timing CSV）
# ---------------------------------------------------------------------------


def discover_pairs(render_dir: Path) -> List[Tuple[str, Path, Path]]:
    """`render_dir` 直下（非再帰）の `*.wav`/`*.csv` を stem で突き合わせ、
    `(stem, wav_path, csv_path)` のリストを stem 昇順で返す。

    片方のみ存在する stem（wav 欠落 or csv 欠落）は全件収集したうえで
    `PairDiscoveryError` を送出する（1 件目で即エラーにせず、複数欠落を
    1 回の報告で把握できるようにする既存 house 慣行）。
    """
    wav_stems: Dict[str, Path] = {p.stem: p for p in sorted(render_dir.glob("*.wav"))}
    csv_stems: Dict[str, Path] = {p.stem: p for p in sorted(render_dir.glob("*.csv"))}

    missing_csv = sorted(set(wav_stems) - set(csv_stems))
    missing_wav = sorted(set(csv_stems) - set(wav_stems))
    if missing_csv or missing_wav:
        problems: List[str] = []
        if missing_csv:
            problems.append(
                f"{len(missing_csv)} wav(s) without matching timing csv: {missing_csv}"
            )
        if missing_wav:
            problems.append(
                f"{len(missing_wav)} timing csv(s) without matching wav: {missing_wav}"
            )
        raise PairDiscoveryError(
            f"render-dir {render_dir} has mismatched wav/timing-csv pair(s) "
            "(fail-closed, nothing published): " + "; ".join(problems)
        )

    return [(stem, wav_stems[stem], csv_stems[stem]) for stem in sorted(wav_stems)]


# ---------------------------------------------------------------------------
# 2. timing CSV -> transcriptions.csv 1 行への変換
# ---------------------------------------------------------------------------


def _read_timing_rows(stem: str, csv_path: Path) -> List[Dict[str, str]]:
    """`csv_path` を 1 回だけ読み、列欠損があれば fail-closed で拒否する。
    行は `row_index`（整数）昇順へ正規化して返す（物理書き込み順への非依存
    ・row_index の連番崩れ検出込み）。"""
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = set(reader.fieldnames or [])
        missing_fields = [c for c in REQUIRED_TIMING_FIELDS if c not in fieldnames]
        if missing_fields:
            raise MalformedTimingCsvError(
                f"{stem}: timing csv {csv_path} missing required column(s) "
                f"{missing_fields} (fail-closed, nothing published)"
            )
        rows = list(reader)

    if not rows:
        raise MalformedTimingCsvError(
            f"{stem}: timing csv {csv_path} has no data rows (fail-closed, nothing published)"
        )

    indexed: List[Tuple[int, Dict[str, str]]] = []
    bad_index: List[str] = []
    for row in rows:
        try:
            idx = int(row["row_index"])
        except (KeyError, ValueError):
            bad_index.append(repr(row.get("row_index")))
            continue
        indexed.append((idx, row))
    if bad_index:
        raise MalformedTimingCsvError(
            f"{stem}: timing csv {csv_path} has non-integer row_index value(s): "
            f"{bad_index} (fail-closed, nothing published)"
        )

    indexed.sort(key=lambda pair: pair[0])
    expected = list(range(len(indexed)))
    actual = [idx for idx, _ in indexed]
    if actual != expected:
        raise MalformedTimingCsvError(
            f"{stem}: timing csv {csv_path} row_index is not a contiguous 0..{len(indexed)-1} "
            f"sequence (got {actual}) — truncated/duplicated rows are not safe to publish "
            "(fail-closed, nothing published)"
        )
    return [row for _, row in indexed]


def _fmt_dur(value: float) -> str:
    """`convert_pjs.py` `_drop_dead_phonemes` と同一の慣行（round(d, 12) の
    既定 repr）で秒値を文字列化する。"""
    return str(round(value, 12))


def build_transcription_row(
    stem: str, csv_path: Path, wav_path: Path, duration_tolerance_sec: float
) -> Dict[str, str]:
    """1 ペア（timing csv + wav）から `transcriptions.csv` の 1 行を組み立てる。

    ph_dur 合計と実 wav 長（変換前 24kHz 側、秒単位のため samplerate 非依存）
    の乖離検査もここで行う（`duration_tolerance_sec` 超過は
    `DurationMismatchError`、全件を集めてから呼び出し側が最終判定するのではなく
    ここで即座に送出する — 1 ペア = 1 独立検査単位のため他ペアの読み込みを
    ブロックしない設計。`convert()` 側が複数ペアの違反を集約する）。
    """
    rows = _read_timing_rows(stem, csv_path)

    ph_seq_all: List[str] = []
    ph_dur_all: List[float] = []
    ph_num_all: List[int] = []
    note_seq_all: List[str] = []
    note_dur_all: List[float] = []

    malformed: List[str] = []

    for row in rows:
        kind = row["row_kind"]
        row_index = row["row_index"]
        try:
            ph_num = int(row["ph_num"])
        except ValueError:
            malformed.append(f"row {row_index}: non-integer ph_num {row['ph_num']!r}")
            continue

        ph_seq_tokens = row["ph_seq"].split()
        try:
            ph_dur_tokens = [float(x) for x in row["ph_dur_sec"].split()]
        except ValueError:
            malformed.append(f"row {row_index}: non-numeric ph_dur_sec {row['ph_dur_sec']!r}")
            continue

        if not (len(ph_seq_tokens) == len(ph_dur_tokens) == ph_num) or ph_num <= 0:
            malformed.append(
                f"row {row_index}: ph_seq/ph_dur_sec/ph_num length mismatch "
                f"(ph_seq={ph_seq_tokens!r}, ph_dur_sec={ph_dur_tokens!r}, ph_num={ph_num!r})"
            )
            continue
        if not all(math.isfinite(d) and d > 0.0 for d in ph_dur_tokens):
            malformed.append(f"row {row_index}: non-finite/non-positive ph_dur_sec {ph_dur_tokens!r}")
            continue

        try:
            note_dur = float(row["note_dur_sec"])
        except ValueError:
            malformed.append(f"row {row_index}: non-numeric note_dur_sec {row['note_dur_sec']!r}")
            continue
        if not math.isfinite(note_dur) or note_dur <= 0.0:
            malformed.append(f"row {row_index}: non-finite/non-positive note_dur_sec {note_dur!r}")
            continue

        if kind == "rest":
            if ph_seq_tokens != ["SP"]:
                malformed.append(f"row {row_index}: rest row has unexpected ph_seq {ph_seq_tokens!r}")
                continue
            note_token = "rest"
        elif kind == "note":
            try:
                midi_val = float(row["midi"])
            except ValueError:
                malformed.append(f"row {row_index}: non-numeric midi {row['midi']!r}")
                continue
            midi_int = round(midi_val)
            if not math.isfinite(midi_val) or abs(midi_val - midi_int) > 1e-6:
                malformed.append(f"row {row_index}: midi {midi_val!r} is not integral")
                continue
            note_token = str(midi_int)
        else:
            malformed.append(f"row {row_index}: unknown row_kind {kind!r}")
            continue

        ph_seq_all.extend(ph_seq_tokens)
        ph_dur_all.extend(ph_dur_tokens)
        ph_num_all.append(ph_num)
        note_seq_all.append(note_token)
        note_dur_all.append(note_dur)

    if malformed:
        raise MalformedTimingCsvError(
            f"{stem}: timing csv {csv_path} has {len(malformed)} malformed row(s) "
            "(fail-closed, nothing published): " + "; ".join(malformed)
        )

    wav_dur = _wav_duration_seconds(wav_path)
    ph_total = math.fsum(ph_dur_all)
    diff = abs(ph_total - wav_dur)
    if diff > duration_tolerance_sec:
        raise DurationMismatchError(
            f"{stem}: ph_dur total {ph_total:.6f}s vs wav duration {wav_dur:.6f}s "
            f"(diff {diff:.6f}s > tolerance {duration_tolerance_sec:.6f}s) — "
            f"{wav_path} (fail-closed, nothing published)"
        )

    return {
        "name": stem,
        "ph_seq": " ".join(ph_seq_all),
        "ph_dur": " ".join(_fmt_dur(d) for d in ph_dur_all),
        "ph_num": " ".join(str(n) for n in ph_num_all),
        "note_seq": " ".join(note_seq_all),
        "note_dur": " ".join(_fmt_dur(d) for d in note_dur_all),
    }


def _wav_duration_seconds(path: Path) -> float:
    """`path`（PCM wav）の実長を秒で返す。非 PCM/破損/フレーム数 0 は
    `MalformedTimingCsvError` として fail-closed する（duration 整合検査の
    前提が壊れているため）。"""
    try:
        with wave.open(str(path), "rb") as w:
            rate = w.getframerate()
            n_frames = w.getnframes()
    except (wave.Error, OSError, EOFError) as exc:
        raise MalformedTimingCsvError(
            f"{path}: cannot read wav duration ({exc}) (fail-closed, nothing published)"
        ) from None
    if rate <= 0 or n_frames <= 0:
        raise MalformedTimingCsvError(
            f"{path}: wav has non-positive framerate/frame count "
            f"(rate={rate}, frames={n_frames}) (fail-closed, nothing published)"
        )
    return n_frames / rate


# ---------------------------------------------------------------------------
# 3. ffmpeg リサンプル (24kHz -> 44.1kHz PCM_16)
# ---------------------------------------------------------------------------


def resample_to_44k1(src: Path, dst: Path, ffmpeg_bin: Optional[str] = None) -> None:
    """`src` を 44.1kHz mono PCM_16 wav として `dst` へ書き出す（ffmpeg 経由）。

    `recording_kit/intake.py` `normalize_to_wav` と同一のコマンド形（`-y -i
    <src> -ac 1 -ar 44100 -sample_fmt s16 <dst>`）。同一入力に対し 2 回実行した
    出力の sha256 が一致することを実測確認済み（決定論変換）。
    """
    resolved_bin = ffmpeg_bin if ffmpeg_bin is not None else FFMPEG_PATH
    if resolved_bin is None:
        raise FfmpegNotFoundError(
            "ffmpeg が見つかりません（`shutil.which('ffmpeg')` が None）。"
            "ffmpeg をインストールしてから再実行してください。"
        )
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        resolved_bin, "-y", "-i", str(src),
        "-ac", "1", "-ar", str(TARGET_SAMPLE_RATE), "-sample_fmt", "s16",
        str(dst),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


# ---------------------------------------------------------------------------
# 4. 原子的公開 (`convert_ritsu.py` `_swap_into_place`/`convert` と同型)
# ---------------------------------------------------------------------------


def _swap_into_place(staging_dir: Path, out_dir: Path) -> None:
    """`convert_ritsu.py` `_swap_into_place` と同一実装（2 段 rename +
    失敗時ロールバック。完了判定は `old_dir.exists()` というファイルシステム
    状態の観測で行い、Python 変数での記帳はしない）。"""
    old_dir = out_dir.parent / f"{out_dir.name}.old"
    if old_dir.exists():
        shutil.rmtree(old_dir)
    try:
        if out_dir.exists():
            out_dir.rename(old_dir)
        staging_dir.rename(out_dir)
    except BaseException:
        if old_dir.exists() and not out_dir.exists():
            old_dir.rename(out_dir)
        raise


def _reject_output_collision(
    out_paths: Sequence[Path],
    protected_roots: Sequence[Path],
    protected_files: Sequence[Optional[Path]] = (),
) -> None:
    """`convert_ritsu.py` `_reject_output_collision` と同一ロジック（双方向
    包含判定 + 相互衝突判定）に加え、`protected_files`（P1 修正・review #265、
    R3 で双方向化）で単一ファイル入力を保護する（`adapter/render.py`
    `_reject_output_collision` の `protected_files`/`protected_roots` 分離と
    同型だが、本関数は `out_paths` が `_swap_into_place` で**ディレクトリごと
    rename/rmtree される**ことを踏まえ `protected_roots` と同じ双方向包含判定
    を `protected_files` にも適用する）。`protected_roots` はディレクトリ全体
    （配下への包含・逆包含も検査）を保護するのに対し、`protected_files` は
    単一ファイル（例: `--ledger`/`--dsdict` の JSON/YAML ファイル自身）を
    保護する:

    - 完全一致（`out_path == protected_file`）
    - 保護ファイルが `out_path` 配下（`.old`/`.staging-<pid>` 派生含む）に
      ある場合（review #265 R3 P1 指摘: `out_dir` が保護ファイルの**祖先
      ディレクトリ**だと、`_swap_into_place` が `out_dir` を `.old` へ
      rename する際に保護ファイルごと退避・次回実行時の `rmtree` で消失
      し得た。旧実装は完全一致のみ検査しており、この包含ケースを見逃して
      いた）

    のいずれかで fail-closed する。`out_path` が保護ファイルの兄弟（同じ
    親ディレクトリの別パス）であるだけなら誤検知しない（台帳/辞書ファイルと
    出力先が同じ scratch ディレクトリ配下にある一般的な運用を通す）。
    `None`/未存在の要素はスキップする。
    """
    resolved_outs = [(p, p.resolve()) for p in out_paths]

    for i, (p_i, r_i) in enumerate(resolved_outs):
        for p_j, r_j in resolved_outs[i + 1:]:
            if r_i == r_j:
                raise OutputCollisionError(
                    f"output paths collide with each other: {p_i} == {p_j}（fail-closed で拒否）"
                )

    for f in protected_files:
        if f is None:
            continue
        f_path = Path(f)
        if not f_path.exists():
            continue
        f_resolved = f_path.resolve()
        for p, r in resolved_outs:
            if r == f_resolved:
                raise OutputCollisionError(
                    f"output path {p} collides with protected input file {f}（fail-closed で拒否）"
                )
            try:
                f_resolved.relative_to(r)
            except ValueError:
                continue
            raise OutputCollisionError(
                f"protected input file {f} is inside output path {p}"
                f"（fail-closed で拒否。出力側の公開処理（rename/rmtree）が"
                f"保護ファイルを巻き込む）"
            )

    for root in protected_roots:
        if not root.exists():
            continue
        root_resolved = root.resolve()
        for p, r in resolved_outs:
            if r == root_resolved:
                raise OutputCollisionError(
                    f"output path {p} collides with protected input root {root}（fail-closed で拒否）"
                )
            try:
                r.relative_to(root_resolved)
            except ValueError:
                pass
            else:
                raise OutputCollisionError(
                    f"output path {p} is inside protected input root {root}（fail-closed で拒否）"
                )
            try:
                root_resolved.relative_to(r)
            except ValueError:
                continue
            raise OutputCollisionError(
                f"protected input root {root} is inside output path {p}"
                f"（fail-closed で拒否。出力側の公開処理が保護 root を巻き込む）"
            )


def convert(
    render_dir: Path,
    out_dir: Path,
    duration_tolerance_sec: float = DEFAULT_DURATION_TOLERANCE_SEC,
    ffmpeg_bin: Optional[str] = None,
) -> dict:
    """`render_dir` の D3 wav+timing-csv ペア群を `out_dir` へ変換する
    （`transcriptions.csv` + `wavs/`）。`convert_ritsu.py` `convert()` と同型:
    `<out_dir>.staging-<pid>` に完全構築してから成功時のみ原子的 swap する。
    失敗時は staging を削除し既存 `out_dir` はそのまま残す。統計 dict を返す。

    P1 修正 (review #265): 衝突検査 (`_reject_output_collision`) はこの公開
    関数自身が行う（旧実装は CLI `main()` のみが preflight として呼んでおり、
    `convert()` を非 CLI 経路（他スクリプトからの直接呼び出し）から呼ぶと
    `--out-dir` が `render_dir` と衝突していても無検査で通過し得た）。
    加えて `discover_pairs()` が 0 件（空データセット）の場合も staging
    構築前に fail-closed で拒否する（`EmptyDatasetError`）。
    """
    render_dir = Path(render_dir)
    out_dir = Path(out_dir)
    old_dir = out_dir.parent / f"{out_dir.name}.old"
    staging_dir = out_dir.parent / f"{out_dir.name}.staging-{os.getpid()}"
    _reject_output_collision([out_dir, old_dir, staging_dir], protected_roots=[render_dir])

    pairs = discover_pairs(render_dir)  # fail-closed（ペア不整合）はここで完結
    if not pairs:
        raise EmptyDatasetError(
            f"render-dir {render_dir} contains no wav/timing-csv pairs to convert "
            "(fail-closed, nothing published; any existing out_dir is left untouched)"
        )

    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)
    try:
        summary = _convert_into(pairs, staging_dir, duration_tolerance_sec, ffmpeg_bin)
        _swap_into_place(staging_dir, out_dir)
    except BaseException:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    return summary


def _convert_into(
    pairs: List[Tuple[str, Path, Path]],
    out_dir: Path,
    duration_tolerance_sec: float,
    ffmpeg_bin: Optional[str],
) -> dict:
    out_wavs = out_dir / "wavs"
    out_wavs.mkdir(parents=True, exist_ok=True)

    # 全ペアの duration/型崩れ検査を先に全件走らせ、1 ペアでも違反があれば
    # 実際の ffmpeg 変換/書き込みを一切始める前に集約して fail-closed する
    # （`convert_ritsu.py` の missing_wavs/malformed_oto_lines と同じ「全収集
    # してから公開前に止める」設計）。
    rows: List[Dict[str, str]] = []
    violations: List[str] = []
    for stem, wav_path, csv_path in pairs:
        try:
            row = build_transcription_row(stem, csv_path, wav_path, duration_tolerance_sec)
        except (MalformedTimingCsvError, DurationMismatchError) as exc:
            violations.append(str(exc))
            continue
        rows.append(row)

    if violations:
        raise MalformedTimingCsvError(
            f"{len(violations)} pair(s) failed validation (fail-closed, nothing published): "
            + " | ".join(violations)
        )

    with open(out_dir / "transcriptions.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["name", "ph_seq", "ph_dur", "ph_num", "note_seq", "note_dur"]
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    n_note_rows = 0
    n_rest_rows = 0
    total_ph_dur_s = 0.0
    for row in rows:
        for token in row["note_seq"].split():
            if token == "rest":
                n_rest_rows += 1
            else:
                n_note_rows += 1
        total_ph_dur_s += math.fsum(float(x) for x in row["ph_dur"].split())

    for stem, wav_path, _csv_path in pairs:
        resample_to_44k1(wav_path, out_wavs / f"{stem}.wav", ffmpeg_bin=ffmpeg_bin)

    return dict(
        n_segments=len(rows),
        n_note_rows=n_note_rows,
        n_rest_rows=n_rest_rows,
        total_ph_dur_s=round(total_ph_dur_s, 3),
        effective_minutes=round(total_ph_dur_s / 60.0, 3),
    )


# ---------------------------------------------------------------------------
# 5. CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--render-dir", type=Path, required=True,
        help="D3 レンダ成果物のディレクトリ (wav 24kHz PCM_16 + 同名 timing CSV)",
    )
    parser.add_argument(
        "--out-dir", type=Path, required=True,
        help="出力先 (transcriptions.csv / wavs/ を書く)",
    )
    parser.add_argument(
        "--duration-tolerance-sec", type=float, default=DEFAULT_DURATION_TOLERANCE_SEC,
        help=f"ph_dur 合計と実 wav 長の許容乖離 秒 (既定: {DEFAULT_DURATION_TOLERANCE_SEC})",
    )
    parser.add_argument(
        "--ffmpeg-bin", default=None,
        help="ffmpeg バイナリのパス (既定: PATH 上の 'ffmpeg' を shutil.which で解決)",
    )
    args = parser.parse_args(argv)

    # P1 修正 (review #265): 衝突検査・空データセット検査は `convert()` 自身が
    # 行う（公開関数へ移設済み。CLI 側の preflight 二重実装はしない）。
    try:
        summary = convert(
            args.render_dir, args.out_dir, args.duration_tolerance_sec, args.ffmpeg_bin
        )
    except (
        OutputCollisionError,
        EmptyDatasetError,
        PairDiscoveryError,
        MalformedTimingCsvError,
        DurationMismatchError,
        FfmpegNotFoundError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"n_segments={summary['n_segments']}")
    print(f"n_note_rows={summary['n_note_rows']}")
    print(f"n_rest_rows={summary['n_rest_rows']}")
    print(f"total_ph_dur_s={summary['total_ph_dur_s']:.2f} ({summary['effective_minutes']:.2f} min)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
