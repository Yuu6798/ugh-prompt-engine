"""第3ドナー積み立てキット — 取り込みスクリプト（骨格）。

`README.md` §6 の実装。受領ディレクトリ（チャット添付 or AI-Drive
`/user_donor/incoming/` からダウンロードしたもの）にある音声
（wav/m4a/mp3）を 24kHz mono wav へ正規化し、`user_donor_ledger.json`
へカード ID・sha256・受領日・長さ・ラウドネス概況を追記する。

**スコープ外（TODO）**: 音素/ノート境界のアラインメントは本 stub では
行わない。T0 カード（`cards.md`: UC-001/UC-002）は既存の凍結スコア
（`voice_genesis/singer/score.py`/`score_umi.py`）に一致するため、
後続タスクでアラインメント半自動化を行う想定。本スクリプトはその前段
（受領・正規化・台帳記録）のみを担う。

ffmpeg は外部バイナリ依存のため、実行時に `shutil.which("ffmpeg")` で
存在確認する（import ガード。ffmpeg 不在でもモジュール読み込み自体は
失敗しない — `python -c "import intake"` や pytest 収集を壊さない）。

カード ID はファイル名の先頭トークン（`_` または空白区切りの最初の要素）
を `cards.md` の ID 形式（`UC-\\d{3}`）として抽出する。3 桁の直後は
stem 終端か区切り文字（`_`/空白/`.`/`-`）のみ許可し、`UC-0010` や
`UC-001oops` のような非区切りの続きはマッチさせない（誤って別カードの
プレフィックスに誤帰属しないための境界チェック）。マッチしない場合は
`card_id: null` のまま記録し、後で手動補完できるようにする（fail-closed
にせず記録は残す — 積み立て運用の「録ったものは失わない」を優先）。

同一 stem（拡張子違いの再送・再録）は正規化後ファイル名が衝突するため、
事前に `assign_normalized_filenames` で全入力の派生ファイル名を予約し、
2 件目以降はテイク連番（`UC-001.take2.norm24k.wav` 等）で一意化する
（積み立て運用では同カードの再録が正常系のため fail-closed にはしない）。

正規化 wav の書き出しと台帳更新は staging ディレクトリ上でバッチ全体を
構築してから一括で `--out-dir` / `--ledger` へ公開する。途中で
変換・測定が失敗した場合は staging を破棄するだけで済み、公開済み
ディレクトリと台帳は失敗前の状態のまま変化しない（部分バッチを残さない）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import soundfile as sf

FFMPEG_PATH: Optional[str] = shutil.which("ffmpeg")

SUPPORTED_EXTENSIONS = (".wav", ".m4a", ".mp3")

TARGET_SAMPLE_RATE = 24000

# 3 桁の直後は stem 終端 ($) か区切り文字のみ許可（非有界マッチで
# `UC-0010` を `UC-001` に誤帰属させない。R11 P2 対応）。
CARD_ID_PATTERN = re.compile(r"^(UC-\d{3})(?=$|[_\s.\-])", re.IGNORECASE)

LEDGER_SCHEMA = "user-donor-ledger/0.1"


class FfmpegNotFoundError(RuntimeError):
    """ffmpeg バイナリが見つからない場合に送出する。"""


@dataclass(frozen=True)
class LedgerEntry:
    """`user_donor_ledger.json` の `entries` 1 件分。"""

    card_id: Optional[str]
    source_filename: str
    normalized_path: str
    sha256: str
    received_at: str
    duration_sec: float
    sample_rate: int
    rms_dbfs: Optional[float]
    peak_dbfs: Optional[float]
    alignment_status: str  # 常に "not_started"（TODO: アラインメント実装後に更新）


def discover_inputs(incoming_dir: Path) -> List[Path]:
    """受領ディレクトリ直下の対応拡張子ファイルを名前順で列挙する。"""
    return sorted(
        p
        for p in incoming_dir.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def extract_card_id(filename: str) -> Optional[str]:
    """ファイル名先頭の `UC-NNN` トークンをカード ID として抽出する。"""
    stem = Path(filename).stem
    match = CARD_ID_PATTERN.match(stem)
    if match is None:
        return None
    return match.group(1).upper()


def assign_normalized_filenames(inputs: List[Path], out_dir: Path) -> Dict[Path, str]:
    """入力ごとに一意な正規化後ファイル名（`out_dir` 直下、拡張子込み）を予約する。

    `UC-001.wav` と `UC-001.m4a` のように拡張子違いで stem が一致する入力は、
    そのままだと同じ `{stem}.norm24k.wav` に解決されて衝突する（2 回目の
    ffmpeg -y が 1 回目を上書きし、台帳側は 2 エントリのまま hash が
    不整合になる）。ここで全入力を事前に検査し、2 件目以降はテイク連番
    （`{stem}.take2.norm24k.wav`, `take3`, ...）で一意化する。

    既存の `out_dir` 内ファイル名とも衝突しないようにする（別バッチの
    既存収録を上書きしない）。`inputs` の順序（`discover_inputs` の
    名前順）がそのままテイク番号の割り当て順になるため決定論的。
    """
    taken: set[str] = set()
    if out_dir.exists():
        taken.update(p.name for p in out_dir.iterdir() if p.is_file())

    assigned: Dict[Path, str] = {}
    for src in inputs:
        stem = src.stem
        candidate = f"{stem}.norm24k.wav"
        take_no = 2
        while candidate in taken:
            candidate = f"{stem}.take{take_no}.norm24k.wav"
            take_no += 1
        taken.add(candidate)
        assigned[src] = candidate
    return assigned


def normalize_to_wav(src: Path, dst: Path) -> None:
    """`src` を 24kHz mono PCM16 wav として `dst` へ書き出す（ffmpeg 経由）。

    TODO: 現状は ffmpeg CLI をそのまま subprocess 起動する薄いラッパー。
    ラウドネス正規化（EBU R128 等）は未実装 — 本スクリプトは「読める形式へ
    揃える」だけを担い、ラウドネス概況の記録は測るだけで補正はしない。
    """
    if FFMPEG_PATH is None:
        raise FfmpegNotFoundError(
            "ffmpeg が見つかりません（`shutil.which('ffmpeg')` が None）。"
            "ffmpeg をインストールしてから再実行してください。"
        )
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG_PATH,
        "-y",
        "-i",
        str(src),
        "-ac",
        "1",
        "-ar",
        str(TARGET_SAMPLE_RATE),
        "-sample_fmt",
        "s16",
        str(dst),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def measure_loudness(wav_path: Path) -> tuple[float, Optional[float], Optional[float]]:
    """正規化後 wav の長さ秒・RMS dBFS 概況・peak dBFS 概況を返す。

    厳密なラウドネス規格（LUFS 等）ではなく「概況」（README §6 の記述通り）
    のための簡易指標。無音ファイル（全サンプル 0）は dBFS を -inf にせず
    `None` として記録する（下流の JSON シリアライズで NaN/inf を避ける）。
    """
    data, sample_rate = sf.read(str(wav_path), dtype="float64", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    duration_sec = len(data) / float(sample_rate) if sample_rate else 0.0

    rms = float(np.sqrt(np.mean(np.square(data)))) if len(data) else 0.0
    peak = float(np.max(np.abs(data))) if len(data) else 0.0

    rms_dbfs = 20.0 * np.log10(rms) if rms > 0.0 else None
    peak_dbfs = 20.0 * np.log10(peak) if peak > 0.0 else None

    return duration_sec, rms_dbfs, peak_dbfs


def load_ledger(ledger_path: Path) -> dict:
    if not ledger_path.exists():
        return {"schema": LEDGER_SCHEMA, "entries": []}
    with ledger_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_ledger(ledger_path: Path, ledger: dict) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = ledger_path.with_suffix(ledger_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp_path.replace(ledger_path)


def process_one(src: Path, staging_dir: Path, filename: str, publish_dir: Path) -> LedgerEntry:
    """`src` を `staging_dir/filename` へ正規化し、公開後の想定パスで台帳エントリを作る。

    実ファイルは呼び出し時点では `staging_dir` にしか存在しない
    （`run` が全件成功後に `publish_dir` へ一括移動する）。`normalized_path`
    には公開後の最終パス（`publish_dir/filename`）を記録するため、台帳の
    内容は公開完了後の状態と最初から一致する。
    """
    card_id = extract_card_id(src.name)
    staged_path = staging_dir / filename
    normalize_to_wav(src, staged_path)

    duration_sec, rms_dbfs, peak_dbfs = measure_loudness(staged_path)

    return LedgerEntry(
        card_id=card_id,
        source_filename=src.name,
        normalized_path=str(publish_dir / filename),
        sha256=sha256_of(staged_path),
        received_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        duration_sec=round(duration_sec, 3),
        sample_rate=TARGET_SAMPLE_RATE,
        rms_dbfs=round(rms_dbfs, 2) if rms_dbfs is not None else None,
        peak_dbfs=round(peak_dbfs, 2) if peak_dbfs is not None else None,
        alignment_status="not_started",
    )


def run(incoming_dir: Path, out_dir: Path, ledger_path: Path) -> List[LedgerEntry]:
    """受領ディレクトリを取り込み、`out_dir` と `ledger_path` を一括更新する。

    変換・測定は `out_dir` の兄弟に作る一時 staging ディレクトリで行い、
    全入力の処理が成功した場合のみ staging から `out_dir` へファイルを
    移動し、続けて台帳を保存する（バッチ全体が 1 フェーズで公開される）。
    途中で例外（`KeyboardInterrupt`/`SystemExit` を含む）が発生した場合は
    `finally` で staging を丸ごと破棄するだけで、`out_dir`・`ledger_path`
    は呼び出し前の状態のまま変化しない（部分バッチを残さない。R10 P2 対応）。
    """
    inputs = discover_inputs(incoming_dir)
    if not inputs:
        return []

    filenames = assign_normalized_filenames(inputs, out_dir)

    staging_root = out_dir.parent
    staging_root.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(tempfile.mkdtemp(prefix=".intake-staging-", dir=str(staging_root)))
    try:
        entries = [
            process_one(src, staging_dir, filenames[src], out_dir) for src in inputs
        ]

        ledger = load_ledger(ledger_path)
        ledger.setdefault("entries", []).extend(asdict(e) for e in entries)

        out_dir.mkdir(parents=True, exist_ok=True)
        for src in inputs:
            staged_path = staging_dir / filenames[src]
            final_path = out_dir / filenames[src]
            shutil.move(str(staged_path), str(final_path))

        save_ledger(ledger_path, ledger)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    return entries


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--incoming-dir", type=Path, required=True, help="受領ファイルを置いたディレクトリ"
    )
    parser.add_argument(
        "--out-dir", type=Path, required=True, help="正規化後 24kHz mono wav の出力先"
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=Path(__file__).resolve().parent / "user_donor_ledger.json",
        help="user_donor_ledger.json の出力先（既存なら追記）",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if not args.incoming_dir.is_dir():
        print(f"error: --incoming-dir が存在しません: {args.incoming_dir}", file=sys.stderr)
        return 1

    try:
        entries = run(args.incoming_dir, args.out_dir, args.ledger)
    except FfmpegNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for entry in entries:
        print(f"{entry.source_filename} -> card_id={entry.card_id} sha256={entry.sha256[:12]}...")
    print(f"{len(entries)} 件を {args.ledger} へ追記しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
