"""run8/s7_io.py — run 8 の入出力ガード（同型穴をここ 1 箇所へ集約する）。

塞いでいる穴は 2 系統で、どちらも **PR #300 のレビューで同型の再発が指摘された**
ため、モジュールごとに書かず**単一実装 + 全書き込み口からの呼び出し**にする。

1. `reject_output_collision` — 出力先が入力（事前登録 JSON / `transcriptions.csv` /
   MIDI 対応表 / 測定 spec）と一致していたら**書く前に**停止する。
   `scripts/measure_bands.py::_reject_output_collision` と同じ resolved 比較。
2. `read_json_with_pin` / `read_bytes_with_pin` — 入力を**一度だけ**読み、
   その同じバイト列から parse と sha256 の両方を作る（読み直すと、
   parse と hash の間で差し替わったときに「古いバイトで計算した結果」を
   「新しいバイトの sha」で pin してしまう）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple


class OutputCollisionError(RuntimeError):
    """出力先が保護対象の入力と衝突した（fail-closed）。"""


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def reject_output_collision(
    out_paths: Iterable[Path], input_paths: Iterable[Path]
) -> None:
    """出力先が入力のいずれかと一致したら停止する（symlink 解決後の完全一致）。"""
    resolved_inputs = {Path(p).resolve() for p in input_paths if Path(p).exists()}
    for out in out_paths:
        if Path(out).resolve() in resolved_inputs:
            raise OutputCollisionError(
                f"出力先 ({out}) が入力と衝突しています（fail-closed で拒否）"
            )


def read_bytes_with_pin(path: Path) -> Tuple[bytes, str, int]:
    """(生バイト, sha256, バイト数) を**一度の読み込み**から返す。"""
    raw = Path(path).read_bytes()
    return raw, sha256_bytes(raw), len(raw)


def read_json_with_pin(path: Path) -> Tuple[Dict[str, Any], str, int]:
    """JSON を**一度だけ読んだバイト列**から parse し、その sha256 とともに返す。"""
    raw, sha, n = read_bytes_with_pin(path)
    return json.loads(raw.decode("utf-8")), sha, n
