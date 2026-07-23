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
    `None` を許容する（型としては許容するが、値としては信用しない —
    `recast status`/`recast ingest` は `None` を「不明だから判定スキップ」
    ではなく fail-closed に stale（pin なし）として扱う。Codex P2 review
    round 4, PR3 #208 指摘 9: pin 欠落は旧形式/手動コピーされた state が
    最もありそうなケースであり、そのまま素通りさせるのは危険）。

    `plan_sha256`: この run を記録した時点で publish した `recast_plan.json`
    自身の bytes sha256（Codex P2 fourth round #207: state は plan の中身を
    一切参照しておらず、publish 後に成果物が削除・破損・別 (variant,backend)
    の plan で上書きされても state だけが古い `verified` を信用し続けていた）。
    `inputs_digest` と同じ optional 後方互換規約（同じく `None` は stale 扱い）。

    `orders_digest`: manual invocation の `awaiting_generation` 記録時に
    publish した注文書 6 ファイル（`recast/backends/manual.py:
    MANUAL_ORDER_FILENAMES`）の content digest
    （`recast/backends/manual.py:compute_orders_digest`）。`recast ingest` が
    collect 前に disk 上の現在の 6 ファイルからこれを再計算し突合する（Codex
    P2 review round 13, PR3 #208 指摘28: `ManualInvoker.prepare()` は呼ぶ度に
    同じ 6 ファイルを無条件に再公開するため、ingest が単純に `prepare()` を
    再度呼ぶと run 後に人手で編集された注文書が黙って rebuild 結果で
    上書きされ、「編集済み注文で生成された音声」を正規の注文書由来として
    受理してしまう）。`inputs_digest`/`plan_sha256` と同じ optional 後方互換
    規約（本フィールド追加前の state・local backend の run は `None` — local
    backend は注文書を publish しないため意図的に無関係で常に `None`）。
    manual invocation の run で `None` の場合は同じく stale（pin なし）扱い。
    """

    state: RecastState
    note: Optional[str] = None
    updated_at: str
    history: List[RecastStateHistoryEntry] = Field(default_factory=list)
    inputs_digest: Optional[str] = None
    plan_sha256: Optional[str] = None
    orders_digest: Optional[str] = None


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


def _write_recast_state_atomically(path: Path, content: bytes) -> None:
    """tempfile + `os.replace` による atomic publish（`cli/observe_cmd.py` の
    `_write_observation_report_atomically` と同型）。書き込み途中の失敗が
    部分的な `recast_state.json` を残さないようにする。bytes を受け取り
    binary モードで書く（Codex P2 ninth round #207: `recast_cmd.py` の
    `recast_plan.json` 書き込みと同じ理由で text モードを避ける —
    `recast_state.json` 自体は現状バイト単位の hash 突合対象ではないが、
    single-source of truth 原則で recast モジュール内の atomic 書き込みを
    統一する）。"""
    output_dir = path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=output_dir, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
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
    plan_sha256: Optional[str] = None,
    orders_digest: Optional[str] = None,
    protected_inputs: List[Path],
) -> RecastStateFile:
    """`(variant, backend)` 実行の到達状態を記録し、更新後の `RecastStateFile` を返す。

    再実行冪等: 直前の記録済み状態が `state` + `note` + `inputs_digest` +
    `plan_sha256` + `orders_digest` の組で完全一致する場合は history に
    重複追加せず、ファイルも書き換えない（呼び出し側から見て純粋な no-op —
    `updated_at` も更新しない）。
    `inputs_digest` / `plan_sha256` / `orders_digest` も同一性判定に含める理由: これらが変わっても
    偶然 state/note が同じになるケースで古いまま no-op してしまうと
    `recast status` の stale 検出が機能しなくなるため。それ以外は history へ
    1 件追記し、atomic write で publish する。

    `protected_inputs` は必須（デフォルト値なし — Codex P2 review round 5,
    PR3 #208 指摘 10: `recast/plan.py:_publish_recast_plan` と同じ理由で、
    project 側の宣言次第で入力パスが `<project_dir>/recast_state.json` と
    一致し得る。書き込み直前に `recast/run_paths.py:collect_protected_input_paths`
    の戻り値との alias 検査を行い、衝突時は何も書かずに `ValueError` を送出する
    （fail-closed — 他の publish サイトと同じ契約）。冪等 no-op で返る経路
    （既存記録と完全一致）は実際には書き込まないため検査しない。"""
    state_file = load_recast_state(project_dir)
    key = _run_key(variant, backend)
    existing = state_file.runs.get(key)
    if (
        existing is not None
        and existing.state == state
        and existing.note == note
        and existing.inputs_digest == inputs_digest
        and existing.plan_sha256 == plan_sha256
        and existing.orders_digest == orders_digest
    ):
        return state_file

    state_path = _state_path(project_dir)
    if protected_inputs:
        resolved_inputs = {candidate.resolve() for candidate in protected_inputs}
        if state_path.resolve() in resolved_inputs:
            raise ValueError(f"output path collides with a protected input path: {state_path}")

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
        plan_sha256=plan_sha256,
        orders_digest=orders_digest,
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
    _write_recast_state_atomically(_state_path(project_dir), content.encode("utf-8"))
    return new_state_file
