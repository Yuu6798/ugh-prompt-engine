"""正規化 JSON serialization — 独自正規形 `vgcal-canon/1`（設計正本 §7, §3.3）。

設計正本 §7 は「RFC 8785 相当」と表現するが、本実装は **RFC 8785 との byte 互換を
主張しない**（例: 指数表記が `1e-07`（多くの RFC 8785 実装が採用する固定幅指数）と
Python の `repr(1e-07)` が返す `1e-07`... のような場合でも実装間で表記差が生じうる
一般論として、`1e-7` 形式 vs `1e-07` 形式など指数部の桁埋め規約は実装依存であり、本
実装は Python の `repr()` が返す最短往復表現をそのまま採用する）。そのため正規形を
バージョン付きの独自名 `vgcal-canon/1` として管理し、`row_id` / `manifest_sha` /
provenance ledger の照合はすべて **本実装** を正本として行う（他言語・他ライブラリの
RFC 8785 実装が生成した文字列とは byte 一致しない可能性がある。Codex レビュー
2026-09-01 採用）。

仕様 (`vgcal-canon/1`):
- dict キーは codepoint 順にソート
- 区切りは `(",", ":")` の最小形
- `ensure_ascii=False`（unicode をエスケープしない）
- NaN / Infinity は `ValueError`
- float は Python `repr()` による最短往復表現。ただし `-0.0` は `0.0` に正規化する
  （符号なし ID 生成で `-0.0 != 0.0` が非決定的な揺れを生まないようにするため）
- dict / list / str / int / float / bool / None 以外の型は拒否
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

CANONICAL_FORMAT = "vgcal-canon/1"


def _validate_canonical(obj: Any, *, path: str = "$") -> None:
    """JSON 互換型のみを再帰許可する。"""
    if obj is None:
        return
    if isinstance(obj, bool):
        return
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            raise ValueError(f"canonical_json: NaN/Infinity is not permitted at {path}")
        return
    if isinstance(obj, int):
        return
    if isinstance(obj, str):
        return
    if isinstance(obj, list):
        for i, item in enumerate(obj):
            _validate_canonical(item, path=f"{path}[{i}]")
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise ValueError(
                    f"canonical_json: dict key must be str at {path}, got {type(k)!r}"
                )
            _validate_canonical(v, path=f"{path}.{k}")
        return
    raise ValueError(f"canonical_json: unsupported type {type(obj)!r} at {path}")


def _normalize_negative_zero(obj: Any) -> Any:
    """`-0.0` を `0.0` へ正規化する（他は再帰的にコピー、dict/list 以外は素通し）。"""
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        return 0.0 if obj == 0.0 else obj
    if isinstance(obj, list):
        return [_normalize_negative_zero(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _normalize_negative_zero(v) for k, v in obj.items()}
    return obj


def canonical_json(obj: Any) -> str:
    """`vgcal-canon/1` 正規形へ直列化する。"""
    _validate_canonical(obj)
    normalized = _normalize_negative_zero(obj)
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def row_id(row: Mapping[str, Any]) -> str:
    """`sha256(canonical_json(row))` の hex digest。"""
    return hashlib.sha256(canonical_json(row).encode("utf-8")).hexdigest()


def manifest_sha(manifest: Mapping[str, Any]) -> str:
    """manifest 自己 hash（設計正本 §3.3: 正規化 serialization 上の manifest_sha）。"""
    return hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()
