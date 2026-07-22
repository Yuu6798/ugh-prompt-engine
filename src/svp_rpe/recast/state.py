"""RecastState: `<project_dir>/recast_state.json` への状態機械の永続化。

各 (variant, backend) 実行の到達状態を追跡する。正常系:
``draft → authored → compiled → verified → awaiting_generation → generated →
observed → reported``。失敗系（いずれも正常系から分岐する終端）:
``blocked_authoring`` / ``blocked_capability`` / ``blocked_verification`` /
``generation_failed`` / ``observation_incomplete``。状態遷移そのもの
（どの状態からどの状態へ進めるか）は `recast/plan.py` 等の呼び出し側が決める
— 本モジュールは「記録された状態を読み書きする」永続化層のみを担う（状態機械の
遷移規則を検証・強制しない）。

`updated_at` / `history[].at` は UTC ISO 8601 の実時刻を記録するが、
同一性判定・テスト比較には使わない（`record_state` の冪等判定は
state + note の組のみで行う）。
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

RecastState = Literal[
    # 正常系
    "draft",
    "authored",
    "compiled",
    "verified",
    "awaiting_generation",
    "generated",
    "observed",
    "reported",
    # 失敗系
    "blocked_authoring",
    "blocked_capability",
    "blocked_verification",
    "generation_failed",
    "observation_incomplete",
]

RECAST_STATE_SCHEMA_VERSION = "recast-state/0.1"
RECAST_STATE_FILENAME = "recast_state.json"


class RecastStateModel(BaseModel):
    """recast state 側スキーマの共通基底。未知 key を拒否する（fail-closed）。"""

    model_config = ConfigDict(extra="forbid")


class RecastStateHistoryEntry(RecastStateModel):
    """1 回の `record_state` 呼び出しが積む history 1 件。"""

    state: RecastState
    note: Optional[str] = None
    at: str


class RecastRunState(RecastStateModel):
    """1 (variant, backend) 実行の現在状態 + 履歴全体。

    `inputs_digest`: この run を記録した時点の入力一式（project/score/
    identity_manifest/arrangement spec/capability profile/mode_overrides）の
    合成 digest（`recast.plan.compute_recast_inputs_digest`）。optional —
    本フィールド追加前に書かれた既存 `recast_state.json` との後方互換のため
    `None` を許容する（`recast status` は `None` を「不明」として stale
    判定をスキップし、false positive を出さない）。
    """

    state: RecastState
    note: Optional[str] = None
    updated_at: str
    history: List[RecastStateHistoryEntry] = Field(default_factory=list)
    inputs_digest: Optional[str] = None


class RecastStateFile(RecastStateModel):
    """`recast_state.json` 全体。未知 schema_version は Literal 不一致で
    pydantic の `ValidationError` により fail-closed で拒否される
    （他 recast loader と同じ「ラップしない」規約）。"""

    schema_version: Literal["recast-state/0.1"] = RECAST_STATE_SCHEMA_VERSION
    runs: Dict[str, RecastRunState] = Field(default_factory=dict)


def _run_key(variant: str, backend: str) -> str:
    return f"{variant}@{backend}"


def _state_path(project_dir: Path | str) -> Path:
    return Path(project_dir) / RECAST_STATE_FILENAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_recast_state(project_dir: Path | str) -> RecastStateFile:
    """`<project_dir>/recast_state.json` をロードする。

    ファイルが存在しない場合は空の `RecastStateFile`（`runs={}`）を返す —
    まだ一度も `record_state` が呼ばれていないプロジェクトの既定状態。
    非 mapping の JSON / 未知 schema_version / 未知 key はここで
    `ValueError` / pydantic `ValidationError` を送出する（ラップしない）。
    """
    path = _state_path(project_dir)
    if not path.is_file():
        return RecastStateFile()
    raw_bytes = path.read_bytes()
    data = json.loads(raw_bytes)
    if not isinstance(data, dict):
        raise ValueError(f"recast state must be a mapping: {path}")
    return RecastStateFile.model_validate(data)


def _write_recast_state_atomically(path: Path, content: str) -> None:
    """tempfile + `os.replace` による atomic publish（`cli/observe_cmd.py` の
    `_write_observation_report_atomically` と同型）。書き込み途中の失敗が
    部分的な `recast_state.json` を残さないようにする。"""
    output_dir = path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=output_dir, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def record_state(
    project_dir: Path | str,
    variant: str,
    backend: str,
    state: RecastState,
    note: Optional[str] = None,
    *,
    inputs_digest: Optional[str] = None,
) -> RecastStateFile:
    """`(variant, backend)` 実行の到達状態を記録し、更新後の `RecastStateFile` を返す。

    再実行冪等: 直前の記録済み状態が `state` + `note` + `inputs_digest` の組で
    完全一致する場合は history に重複追加せず、ファイルも書き換えない（呼び出し側
    から見て純粋な no-op — `updated_at` も更新しない）。`inputs_digest` も
    同一性判定に含める理由: 入力が変わっても偶然 state/note が同じになるケースで
    digest だけ古いまま no-op してしまうと `recast status` の stale 検出が機能
    しなくなるため。それ以外は history へ 1 件追記し、atomic write で publish する。
    """
    state_file = load_recast_state(project_dir)
    key = _run_key(variant, backend)
    existing = state_file.runs.get(key)
    if (
        existing is not None
        and existing.state == state
        and existing.note == note
        and existing.inputs_digest == inputs_digest
    ):
        return state_file

    now = _now_iso()
    new_history = list(existing.history) if existing is not None else []
    new_history.append(RecastStateHistoryEntry(state=state, note=note, at=now))
    new_runs = dict(state_file.runs)
    new_runs[key] = RecastRunState(
        state=state,
        note=note,
        updated_at=now,
        history=new_history,
        inputs_digest=inputs_digest,
    )
    new_state_file = RecastStateFile(schema_version=RECAST_STATE_SCHEMA_VERSION, runs=new_runs)

    content = (
        json.dumps(
            new_state_file.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    _write_recast_state_atomically(_state_path(project_dir), content)
    return new_state_file
