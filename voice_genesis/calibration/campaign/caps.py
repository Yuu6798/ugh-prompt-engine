"""frozen cost cap の復元 + `CapCounters` の campaign dir 永続化
（`cli.py` finding #1 対応: 「stages call the measurement pipeline without
CapCounters/CostCaps」）。

`cli.py` の全武装 stage はここを経由して:

- `cost_caps_from_manifest()` — 凍結 manifest の `frozen_design.cost_caps`
  （`c0_freeze.build_manifest()` が Gate 1 承認の `cost_caps` payload から
  埋め込んだ canonical 3 キー `compute`/`storage`/`budget`）から
  `cost_caps.CostCaps` を復元する。Gate 1 未承認時の manifest は
  `"ABSENT:GATE1_NOT_APPROVED"`（文字列 sentinel）を持つため、その場合や
  節自体が無い場合は `None` を返す（cap 値の要否判断そのものは行わない —
  値が無ければ強制もしない、という D1 `cost_caps.py` と同じ授権境界）。
- `load_cap_counters()`/`save_cap_counters()` — `<campaign_dir>/counters.json`
  へ `cost_caps.CapCounters` を atomic write（同一ディレクトリ内 tempfile →
  `os.replace`。`approvals.py` の hash refresh と同一パターン）で永続化し、
  次回のサブコマンド起動時に読み戻す。render/measure 各 unit の直後に
  都度上書きするため、stage 途中で cap 超過して fail-closed 終了しても、
  その unit までの消費が失われない。壊れた/型不正な `counters.json` は
  fail-closed でエラーにする（黙って 0 へリセットしない — cap バイパスを
  防ぐ）。

`[UNDERSPEC-CAL-D21]` 設計正本/IMPLEMENTATION_MAP は cap counters の永続化
形式（ファイル単体か ledger 由来の再導出か）を規定しない。本モジュールは
「`counters.json` を都度 atomic 上書きする単一 mutable ファイル」を正本
として採用した（ledger `meter_call` event からの事後再導出は不採用 — event
payload は消費した compute 秒数を保持せず、後から正確に再導出できないため。
`storage` 次元だけ ledger から再計算可能でも `compute` 次元が再導出不能な
以上、二重の正本を持つより単一ファイルの方が単純で監査しやすい）。
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

from voice_genesis.calibration.cost_caps import CapCounters, CostCaps, cost_caps_from_mapping

COUNTERS_FILENAME = "counters.json"


class CapStateError(RuntimeError):
    """`counters.json` の読み込みが壊れている場合の fail-closed error。"""


class CostCapExceededError(RuntimeError):
    """`cost_caps.check()` が超過を検出した際の fail-closed error
    （render_stage/measure_stage 共通 — 単一の型を両モジュールが import して
    使う。個別に定義すると `except` 側が両方を捕捉し損ねる恐れがあるため）。"""


def counters_path(campaign_dir: Path) -> Path:
    return Path(campaign_dir) / COUNTERS_FILENAME


def cost_caps_from_manifest(manifest: Mapping[str, object]) -> CostCaps | None:
    """`manifest["frozen_design"]["cost_caps"]` から `CostCaps` を復元する。
    節が無い・mapping でない・値が不正（例: Gate 1 未承認時の
    `"ABSENT:GATE1_NOT_APPROVED"` 文字列）ならいずれも `None`。"""
    frozen_design = manifest.get("frozen_design")
    if not isinstance(frozen_design, Mapping):
        return None
    raw = frozen_design.get("cost_caps")
    if not isinstance(raw, Mapping):
        return None
    try:
        return cost_caps_from_mapping(dict(raw))
    except (KeyError, TypeError, ValueError):
        return None


def load_cap_counters(campaign_dir: Path) -> CapCounters:
    """`<campaign_dir>/counters.json` を読み戻す。未作成なら 0 の
    `CapCounters()`（新規 campaign の初回起動）。壊れた JSON・型不正は
    `CapStateError`（fail-closed — 黙って 0 に戻すと cap を実質バイパス
    できてしまうため、読み込み不能は明示的な拒否とする）。"""
    path = counters_path(campaign_dir)
    if not path.is_file():
        return CapCounters()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CapStateError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise CapStateError(f"{path} must contain a JSON object")
    try:
        return CapCounters(
            compute_used=float(data["compute_used"]),
            storage_used=int(data["storage_used"]),
            budget_used=float(data["budget_used"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CapStateError(f"malformed counters at {path}: {exc}") from exc


def save_cap_counters(campaign_dir: Path, counters: CapCounters) -> None:
    """`counters.json` へ atomic write（同一ディレクトリ内 tempfile →
    `os.replace`。`approvals.py` の hash refresh と同一パターン）で永続化
    する。"""
    path = counters_path(campaign_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(counters.as_dict(), f, sort_keys=True)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


__all__ = [
    "COUNTERS_FILENAME",
    "CapStateError",
    "CostCapExceededError",
    "counters_path",
    "cost_caps_from_manifest",
    "load_cap_counters",
    "save_cap_counters",
]
