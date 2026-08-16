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
を `cards.md` の ID 形式（`UC-\\d{3}`）として抽出する。マッチしない場合は
`card_id: null` のまま記録し、後で手動補完できるようにする（fail-closed
にせず記録は残す — 積み立て運用の「録ったものは失わない」を優先）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import numpy as np
import soundfile as sf

FFMPEG_PATH: Optional[str] = shutil.which("ffmpeg")

SUPPORTED_EXTENSIONS = (".wav", ".m4a", ".mp3")

TARGET_SAMPLE_RATE = 24000

CARD_ID_PATTERN = re.compile(r"^(UC-\d{3})", re.IGNORECASE)

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


def process_one(src: Path, out_dir: Path) -> LedgerEntry:
    card_id = extract_card_id(src.name)
    normalized_path = out_dir / f"{src.stem}.norm24k.wav"
    normalize_to_wav(src, normalized_path)

    duration_sec, rms_dbfs, peak_dbfs = measure_loudness(normalized_path)

    return LedgerEntry(
        card_id=card_id,
        source_filename=src.name,
        normalized_path=str(normalized_path),
        sha256=sha256_of(normalized_path),
        received_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        duration_sec=round(duration_sec, 3),
        sample_rate=TARGET_SAMPLE_RATE,
        rms_dbfs=round(rms_dbfs, 2) if rms_dbfs is not None else None,
        peak_dbfs=round(peak_dbfs, 2) if peak_dbfs is not None else None,
        alignment_status="not_started",
    )


def run(incoming_dir: Path, out_dir: Path, ledger_path: Path) -> List[LedgerEntry]:
    inputs = discover_inputs(incoming_dir)
    entries = [process_one(src, out_dir) for src in inputs]

    ledger = load_ledger(ledger_path)
    ledger.setdefault("entries", []).extend(asdict(e) for e in entries)
    save_ledger(ledger_path, ledger)

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
