"""hashing.py — proto1 内共通の sha256 ヘルパ（自前実装）。

波形は float32 生バイト列で hash、構造化データは正規形 JSON で hash する
という一般的な設計方針を採用するが、コードは独立に本ファイルで実装する
（proto1 はリポジトリ非依存の scratch 実装であり、他モジュールへの依存を
持たない）。

- `sha256_of_waveform`: probes.py の manifest（波形 hash）で使う。
- `sha256_of_canonical_json`: genome.py / registry.py / reference_set.py の
  content hash（genome_id・reference_set_hash）で使う。
- `sha256_of_file`: proto1_demo.py の WAV 成果物 digest（PR#261 レビュー
  R11）で使う。`sha256_of_waveform` はメモリ上の float32 生サンプルを
  hash するのに対し、こちらはディスク上のファイルバイト列（soundfile が
  書いた WAV ヘッダ込み）を hash する — 別の決定論主張（「レンダリング結果
  の波形が決定論」ではなく「ファイルへのシリアライズ自体が決定論」）を
  検証する。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Union

import numpy as np


def sha256_of_waveform(samples: np.ndarray) -> str:
    """波形を float32 の生サンプル bytes として hash する。"""
    return hashlib.sha256(np.ascontiguousarray(samples, dtype=np.float32).tobytes()).hexdigest()


def sha256_of_file(path: Union[str, Path]) -> str:
    """ファイルバイト列全体を hash する（PR#261 レビュー R11）。"""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha256_of_canonical_json(obj: Any) -> str:
    """dict/list 等を正規形 JSON（キーソート・区切り固定・ASCII 固定）にしてから hash する。

    決定論性: 同一の論理内容は常に同一バイト列にシリアライズされる（キー順序・
    float 表現・空白の揺れを排除する）よう `sort_keys=True, separators=(",", ":")`
    を使う。
    """
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
