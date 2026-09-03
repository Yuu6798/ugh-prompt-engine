"""`python -m voice_genesis.calibration.campaign <subcommand> --campaign-dir ...
--secret-dir ... [--armed] [--workers N]`（IMPLEMENTATION_MAP_v1.md §6.4）。

サブコマンド: `plan`（既定 dry-run。work unit 件数 vs 設計値/caps 照合表を
出力するのみ、副作用なし）/ `c1-fixtures` / `c2-baseline` /
`c3a-f0-selection` / `c3b-selection` / `unseal` / `c4-holdout` / `close`。

**武装プロトコル**（`plan` を除く全サブコマンド）: `--armed` フラグ AND
環境変数 `VG_CAL_CAMPAIGN_AUTHORIZED=1` AND 有効な Gate 1 承認ファイル
（`approvals.check_armed(Gate.GATE1_CAMPAIGN_EXECUTION, ...)`）が揃わなければ
`AUTHORIZATION_REQUIRED` を返し副作用ゼロで終了する。`--armed` を渡さない
場合は当該 stage の work-unit 計画のみを表示して正常終了する（他の 2 要素は
検査しない — 「まだ実行するつもりがない」ことを表明する経路であり拒否理由の
提示は不要なため）。

三要素武装が揃った後、キャンペーンを読み込んでからさらに 4 段の fail-closed
検査を通す（第 8/9/17 巡採用）:

- **canonical path 照合**（`_canonical_path_violations`。finding #7,
  第 9 巡採用）: 凍結 manifest の `candidates.{meter,generator,schema,
  test}_paths_sha256` に列挙された全 path について、**現在のファイル bytes**
  の sha256 を独立に再計算し manifest 記載値と照合する。1 件でも不一致・
  欠落があれば `BLOCKED_CANONICAL_MUTATION_REQUIRED`（設計正本 §3.3）
  ledger `stop_event` を記帳し、副作用を増やさず fail-closed 終了する。
  `hashlib`/`Path.read_bytes()` のみで完結し、`matrix`/`generator`/
  `registry`/`impl` モジュールを import・使用しない（確認対象のコードを
  import して確認する自己言及を避ける）。ただし本モジュール自身は
  `candidates.registry`/`fixtures.matrix` を **モジュール先頭で** import
  している（`_run_c3a` 等が使うため）— Python の import はプロセス内で
  1 度だけ実行され、この import 自体は `main()` 呼び出しより前（`cli.py`
  自身のロード時）に既に完了しているため、「それらの import がこの照合の
  後に来る」ことは本モジュールの現在の構造では実現できない。本照合が
  実際に保証するのは「照合が通らない限り、それらのモジュールが提供する
  **実行時の測定・生成ロジックを呼び出さない**」こと（`build_matrix()`
  呼び出し・stage dispatch は本照合の後に置く）である
  （`[UNDERSPEC-CAL-D23]`）。**運用契約**（round 17 finding #4 見送り・境界
  宣言、`[UNDERSPEC-CAL-D40]`）: 本照合が意味を持つのは「stage 呼び出し毎に
  新規 `python -m voice_genesis.calibration.campaign` プロセスを起動し、
  各プロセスがディスクから再 import する」運用を前提とした場合のみ
  （プロセス起動時にこの照合が走ってから import 済みモジュールを使う）。
  同一プロセス内で長時間 `main()` を繰り返し呼ぶ・モジュールを再利用する
  形の呼び出し方は本契約の対象外。
- **environment drift 照合**（`_environment_drift_violations`。round 17
  finding #2 採用）: 凍結 manifest の `dependencies.{python,numpy,scipy,
  librosa,soundfile,pyworld}_version` を、現在の実行環境から
  `importlib.metadata.version()`/`platform.python_version()` で再取得した
  値と照合する。1 件でも不一致があれば `BLOCKED_ENVIRONMENT_DRIFT`
  （`vocab.BlockedCode` の閉語彙とは別軸。定義は `ENVIRONMENT_DRIFT_CODE`
  参照）ledger `stop_event` を記帳し fail-closed 終了する。`plan`
  （unarmed）はこの照合結果を `environment_drift` キーで報告のみ行い
  block しない。
- **Gate 1 承認の凍結 manifest への束縛**（`_gate1_frozen_binding_violation`）:
  現在ロードした Gate 1 承認ファイルの content sha256 / `authorization_nonce`
  が、このキャンペーンを凍結した時点で manifest に刻まれた
  `approvals.gate1_sha256`/`authorization_nonce` と一致することを要求する。
  不一致（＝凍結後に承認ファイルが差し替えられた）は
  `AUTHORIZATION_REQUIRED`（理由 `gate1_not_frozen_approval`）で拒否する。
- **手続 phase 順序の強制**（`_phase_order_violation`）: 各 subcommand は
  `state.CampaignPhase` 上の直前提条件 phase が到達済みであることを要求し、
  かつ（render 系の resume 対応 subcommand を除き）自身が生成する phase に
  既に到達済みなら再実行を拒否する（`PHASE_ORDER_VIOLATION`）。cap 強制
  （`campaign.caps` 経由の `CostCaps`/`CapCounters`。finding #1）もこの後、
  各 stage dispatch の直前に読み込む。

secret/approval dir の既定解決（`VG_CAL_SECRET_DIR`/`VG_CAL_APPROVAL_DIR`）は
`c0_freeze.py`/`approvals.py` と同じ規約だが、他 agent が並行編集中の
`c0_freeze.py` には依存せず本モジュールで独立に再定義する
（`approvals.default_approval_dir` は import してよい — 本パッケージが
所有しないファイルではない）。
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import os
import platform
import resource
import sys
from importlib import metadata as importlib_metadata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from voice_genesis.calibration.approvals import ArmingDecision, Gate, check_armed, default_approval_dir
from voice_genesis.calibration.campaign import (
    baseline_stage,
    close as close_stage,
    holdout_stage,
    measure_stage,
    render_stage,
    selection_stage,
    unseal as unseal_stage,
    workunits,
)
from voice_genesis.calibration.campaign.caps import (
    CountersCorruptError,
    cost_caps_from_manifest,
    reconcile_cap_counters,
    save_cap_counters,
)
from voice_genesis.calibration.campaign.state import (
    CampaignPhase,
    CampaignStateError,
    FrozenCampaign,
    load_frozen_campaign,
)
from voice_genesis.calibration.campaign.time_budget import SliceStatus, TimeBudget
from voice_genesis.calibration.candidates.registry import candidate_by_id, candidates_for_meter
from voice_genesis.calibration.cost_caps import (
    BudgetAccountingUndeclaredError,
    CapCounters,
    CostCaps,
    StopDecision,
)
from voice_genesis.calibration.cost_caps import check as cost_caps_check
from voice_genesis.calibration.fixtures.axes import FixtureFamily
from voice_genesis.calibration.fixtures.controls import (
    negative_control_row_ids,
    non_boundary_selection_instances,
    positive_detection_instances,
)
from voice_genesis.calibration.fixtures.matrix import (
    build_matrix,
    declared_sweeps_by_family,
    truth_identity_for_row,
)
from voice_genesis.calibration.gates import (
    MIN_RESOLVABLE_PAIRS_PER_SWEEP,
    resolvable_pairs_possible,
)
from voice_genesis.calibration.observables import two_stage_median
from voice_genesis.calibration.vocab import (
    CLAIM_CRITICAL_SET,
    BlockedCode,
    ClaimCeiling,
    MeterId,
    MissingReason,
    Split,
    TerminalStatus,
)

#: `cli.py` から 3 階層上が repo root（`voice_genesis/calibration/campaign/cli.py`）。
#: finding #7 の canonical path 照合が `manifest["candidates"].*_paths_sha256`
#: の相対 path を解決するのに使う。`c0_freeze._REPO_ROOT` と同じ意味だが、
#: 他 agent が並行編集中の `c0_freeze.py` には依存せず本モジュールで独立に
#: 再定義する（モジュール docstring の既存方針と同じ）。
_REPO_ROOT = Path(__file__).resolve().parents[3]

#: `c0_freeze.SECRET_DIR_ENV_VAR`/`DEFAULT_SECRET_DIR` と同一規約の独立定義
#: （モジュール docstring 参照）。
SECRET_DIR_ENV_VAR = "VG_CAL_SECRET_DIR"
DEFAULT_SECRET_DIR = Path.home() / ".vg_cal" / "secrets"

CAMPAIGN_ARMED_ENV_VAR = "VG_CAL_CAMPAIGN_AUTHORIZED"


def _process_cpu_seconds() -> float:
    """round 15 finding #5 (`[UNDERSPEC-CAL-D31]`): this process's own
    cumulative user+sys CPU seconds (`resource.getrusage(RUSAGE_SELF)`).
    Independent copy of `measure_stage._process_cpu_seconds()` (module
    docstring convention: each module keeps its own copy of small shared
    helpers rather than depend on another module's private name) — used to
    snapshot/charge the CLI dispatch path's own *parent-side* CPU for a
    whole stage invocation, which is not captured by either the
    fresh-process workers' self-reported `cpu_seconds` or
    `measure_stage`'s narrower within-process-call window (matrix build,
    ledger/JSON I/O, hashing, subprocess orchestration overhead)."""
    ru_self = resource.getrusage(resource.RUSAGE_SELF)
    return ru_self.ru_utime + ru_self.ru_stime

SUBCOMMANDS: tuple[str, ...] = (
    "plan",
    "c1-fixtures",
    "c2-baseline",
    "c3a-f0-selection",
    "c3b-selection",
    "unseal",
    "c4-holdout",
    "close",
)

MUTATING_SUBCOMMANDS: frozenset[str] = frozenset(SUBCOMMANDS) - {"plan"}


def default_secret_dir(env: Mapping[str, str] | None = None) -> Path:
    source = env if env is not None else os.environ
    override = source.get(SECRET_DIR_ENV_VAR)
    return Path(override) if override else DEFAULT_SECRET_DIR


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m voice_genesis.calibration.campaign")
    parser.add_argument("subcommand", choices=SUBCOMMANDS)
    parser.add_argument("--campaign-dir", required=True, type=Path)
    parser.add_argument("--secret-dir", type=Path, default=None)
    parser.add_argument("--approval-dir", type=Path, default=None)
    parser.add_argument("--armed", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--reveal-split-secret",
        action="store_true",
        help="close サブコマンド専用（[UNDERSPEC-CAL-D09]）: CAMPAIGN_CLOSED 後に "
        "split_secret の commit-reveal event を追加で記帳する。",
    )
    parser.add_argument(
        "--time-budget-seconds",
        type=float,
        default=None,
        help="R2（design memo `design_runner_robustness.md`,"
        " [UNDERSPEC-CAL-D79]）: c1-fixtures/c2-baseline/c3a-f0-selection/"
        "c3b-selection/c4-holdout 専用。dispatch 開始からこの秒数（float、"
        "> 0）が経過したら新規 instance を dispatch せず stage を "
        "PARTIAL_SLICE として終了する（既に dispatch 済みの instance は "
        "完走する）。既定 None は無制限（従来どおり）。",
    )
    parser.add_argument(
        "--discard-partial-groups",
        action="store_true",
        help="R1（design memo `design_runner_robustness.md`,"
        " [UNDERSPEC-CAL-D79]）: c2-baseline/c3a-f0-selection/"
        "c3b-selection/c4-holdout 専用。resume 判定で末尾の meter_call "
        "group が部分的にしか記帳されていない場合（中断直後）、"
        "StaleMeasurementError で fail-closed する代わりに "
        "meter_call_group_discarded event を記帳しフルグループを "
        "再測定する。同一キーへの重複記帳（duplicate）はこのフラグの "
        "有無に関わらず常に fail-closed のまま。既定 False は従来どおり。",
    )
    return parser


def _print(obj: object) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


def _load_campaign_or_none(campaign_dir: Path, secret_dir: Path) -> FrozenCampaign | None:
    try:
        return load_frozen_campaign(campaign_dir, secret_dir)
    except CampaignStateError:
        return None


def build_plan_report(
    campaign_dir: Path, secret_dir: Path, *, stage: str | None = None
) -> dict[str, Any]:
    """全体（設計値）計画 + （読み込めれば）realized split 上の実件数。
    `stage` 指定時はその stage の work unit 件数のみを追加で報告する。"""
    design = workunits.plan_counts()
    report: dict[str, Any] = {
        "design_totals": {
            "instances_total": design.instances_total,
            "renders_total": design.renders_total,
            "meter_calls_per_implementation": design.meter_calls_per_implementation,
            "selection_order_of_magnitude": design.selection_order_of_magnitude,
        }
    }
    campaign = _load_campaign_or_none(campaign_dir, secret_dir)
    if campaign is None:
        report["campaign_state"] = "UNAVAILABLE"
        return report

    matrix_rows = build_matrix()
    assignment = campaign.realized_split.assignment
    realized = workunits.realized_plan(matrix_rows, assignment)
    report["campaign_state"] = "OK"
    report["campaign_id"] = campaign.campaign_id
    report["phases_passed"] = sorted(p.value for p in campaign.phases_passed())
    report["realized"] = {
        "c1_render_instances": realized.c1_render_instances,
        "c4_render_instances": realized.c4_render_instances,
        "c2_baseline_instances": realized.c2_baseline_instances,
        "c3a_instances": realized.c3a_instances,
        "c3b_instances_by_family": dict(realized.c3b_instances_by_family),
    }
    if stage is not None:
        report["stage"] = stage
    # round 17 finding #2 (採用): `plan`（unarmed）は drift を報告のみ行い
    # block しない — armed dispatch のみが `main()` 内の
    # `_environment_drift_violations()` チェックで fail-closed に拒否する。
    environment_violations = _environment_drift_violations(campaign)
    report["environment_drift"] = list(environment_violations)
    return report


# ---------------------------------------------------------------------------
# environment drift 照合（round 17 finding #2 採用）
# ---------------------------------------------------------------------------

#: `manifest["dependencies"]`（`c0_freeze._dependencies_section()` が書き込む
#: キー）のうち、実行時に `importlib.metadata.version()` で再取得・比較できる
#: フィールド（key -> package name）。`pyworld_wheel_hash` はこの環境からは
#: 安価に再取得できない wheel バイト列ハッシュ（`c0_freeze.
#: _pyworld_dependency_fields()` docstring 参照）のため比較対象から除外する。
_DEPENDENCY_PACKAGE_BY_MANIFEST_KEY: dict[str, str] = {
    "numpy_version": "numpy",
    "scipy_version": "scipy",
    "librosa_version": "librosa",
    "soundfile_version": "soundfile",
    "pyworld_version": "pyworld",
}

#: `BLOCKED_ENVIRONMENT_DRIFT` は `vocab.BlockedCode`（設計正本 §3.3 の閉
#: 語彙。C0 で列挙済み・事後追加禁止、`tests/test_vocab.py::
#: test_blocked_code_closed_vocab` が `len(BlockedCode) == 6` で enforce）
#: には **含めない** — `AUTHORIZATION_REQUIRED`/`PHASE_ORDER_VIOLATION`/
#: `BUDGET_ACCOUNTING_UNDECLARED`/`COUNTERS_CORRUPT` と同様、閉語彙とは別軸の
#: pre-dispatch 拒否コードとして扱う（IMPLEMENTATION_MAP_v1.md §6.1 の
#: `AUTHORIZATION_REQUIRED` 注記と同型）。
ENVIRONMENT_DRIFT_CODE = "BLOCKED_ENVIRONMENT_DRIFT"


def _current_dependency_value(package_name: str) -> str:
    try:
        return importlib_metadata.version(package_name)
    except importlib_metadata.PackageNotFoundError:
        # `c0_freeze._pyworld_dependency_fields()` と同じ ABSENT 記法。
        return "ABSENT:not_installed"


def _environment_drift_violations(campaign: FrozenCampaign) -> tuple[str, ...]:
    """round 17 finding #2（採用）: dispatch 前に、凍結 manifest
    `manifest["dependencies"]`（Python 本体 + numpy/scipy/librosa/soundfile/
    pyworld の各バージョン）を現在の実行環境から再取得した値と照合する。
    不一致項目 1 件につき `"<key>: manifest=<frozen> runtime=<current>"`
    形式の 1 行を返す（全て一致すれば空 tuple）。`manifest["dependencies"]`
    が無い（想定外の manifest 形状）場合は照合不能として空 tuple を返す —
    本検査は dependencies セクションの記録形式そのものの欠落までは扱わない
    （それは `c0_validate` の REQUIRED_MANIFEST_KEYS が別途担当する）。
    比較対象パッケージの選定・ABSENT 記法・`vocab.BlockedCode` の閉語彙に
    含めない設計判断は `[UNDERSPEC-CAL-D38]`。"""
    dependencies = campaign.manifest.get("dependencies")
    if not isinstance(dependencies, Mapping):
        return ()
    violations: list[str] = []
    frozen_python = dependencies.get("python_version")
    if isinstance(frozen_python, str):
        current_python = platform.python_version()
        if current_python != frozen_python:
            violations.append(f"python_version: manifest={frozen_python} runtime={current_python}")
    for key, package_name in _DEPENDENCY_PACKAGE_BY_MANIFEST_KEY.items():
        frozen_value = dependencies.get(key)
        if not isinstance(frozen_value, str):
            continue
        current_value = _current_dependency_value(package_name)
        if current_value != frozen_value:
            violations.append(f"{key}: manifest={frozen_value} runtime={current_value}")
    return tuple(violations)


# ---------------------------------------------------------------------------
# canonical path 照合（finding #7, 第 9 巡採用）
# ---------------------------------------------------------------------------

#: 凍結 manifest `candidates` 節の 5 カテゴリキー（`c0_freeze._path_hash_maps`
#: が生成する形状。値は `{相対 path: sha256 hex}` の mapping）。
#: `meter_implementation_paths_sha256`（`[UNDERSPEC-CAL-D49]`、Codex round 21
#: レビュー finding, ADOPT）は `candidates/impl/b0_wrappers.py` が無改変
#: import で実行する `voice_genesis/harness/` 配下の meter 実装を指す。従来
#: このカテゴリが本 tuple に無かったため、C0 freeze 後に harness meter 実装を
#: 改変してもここでの canonical-path 照合を素通りしていた。
_CANONICAL_PATH_CATEGORIES: tuple[str, ...] = (
    "meter_paths_sha256",
    "meter_implementation_paths_sha256",
    "generator_paths_sha256",
    "schema_paths_sha256",
    "test_paths_sha256",
)


def _canonical_path_violations(campaign: FrozenCampaign, repo_root: Path) -> tuple[str, ...]:
    """finding #7: 凍結 manifest の `candidates.<category>`（5 カテゴリ、
    `[UNDERSPEC-CAL-D49]` で harness meter 実装用の
    `meter_implementation_paths_sha256` を追加）に列挙された全 path について、
    `repo_root` 上の **現在のファイル bytes** の sha256 を独立に再計算し、
    manifest 記載値と照合する。1 件でも不一致・欠落があれば、違反 path 1 件
    につき 1 行（`"<category>:<path>: <detail>"` 形式）の tuple を返す。
    全て一致すれば空 tuple。

    `matrix`/`generator`/`registry`/`impl` の import には一切依存しない
    （`hashlib`/`Path.read_bytes()` のみ）— 確認対象のコードを import して
    確認する自己言及を避ける（モジュール docstring の `[UNDERSPEC-CAL-D23]`
    参照）。
    """
    candidates_section = campaign.manifest.get("candidates")
    if not isinstance(candidates_section, Mapping):
        return ("manifest is missing a candidates section",)

    violations: list[str] = []
    for category in _CANONICAL_PATH_CATEGORIES:
        paths = candidates_section.get(category)
        if not isinstance(paths, Mapping):
            violations.append(f"{category}: section missing from manifest")
            continue
        for rel_path, expected_sha in sorted(paths.items()):
            if not isinstance(rel_path, str) or not isinstance(expected_sha, str):
                violations.append(f"{category}: malformed entry {rel_path!r}")
                continue
            file_path = repo_root / rel_path
            if not file_path.is_file():
                violations.append(f"{category}:{rel_path}: file missing on disk")
                continue
            actual_sha = hashlib.sha256(file_path.read_bytes()).hexdigest()
            if actual_sha != expected_sha:
                violations.append(
                    f"{category}:{rel_path}: sha256 mismatch "
                    f"(manifest={expected_sha!r}, actual={actual_sha!r})"
                )
    return tuple(violations)


# ---------------------------------------------------------------------------
# stage dispatch
# ---------------------------------------------------------------------------


def _selected_candidates_by_family(campaign: FrozenCampaign) -> dict[str, str | None]:
    """ledger の最新 `selection_frozen` payload から `selected_by_family` を
    読む（`selection_stage.run_c3b_selection` が記帳したもの）。"""
    result: dict[str, str | None] = {}
    for entry in campaign.ledger.entries:
        payload = entry.payload
        if isinstance(payload, Mapping) and payload.get("kind") == "selection_frozen":
            selected = payload.get("selected_by_family")
            if isinstance(selected, Mapping):
                result = {str(k): (str(v) if v else None) for k, v in selected.items()}
    return result


#: 1 row あたりの probe repeat 数（`fixtures.controls.PROBE_REPEATS` と同値。
#: 本モジュールは truth_by_instance 構築で `range(...)` を直書きしていた
#: 既存の慣例を named constant へ揃えた）。
_PROBE_REPEATS = 5


def _latest_f0_selection(campaign: FrozenCampaign) -> tuple[bool, str | None]:
    """`f0_selection_frozen` event の有無と、あれば最新 payload の
    `selected_candidate_id` を返す（finding #2: C3b/C4 はこの event を
    必須の前提とする — fixture の truth F0 は一切使わない）。1 件も無ければ
    `(False, None)`。event はあるが selection 自体が `SELECTION_FAILED_CLOSED`
    等で候補未選出なら `(True, None)`。"""
    found = False
    selected: str | None = None
    for entry in campaign.ledger.entries:
        payload = entry.payload
        if isinstance(payload, Mapping) and payload.get("kind") == "f0_selection_frozen":
            found = True
            sid = payload.get("selected_candidate_id")
            selected = str(sid) if isinstance(sid, str) else None
    return found, selected


def _reusable_f0_values_by_process(
    campaign: FrozenCampaign,
    candidate_id: str,
    row_id: str,
    probe_index: int,
    *,
    meter_call_index: measure_stage.MeterCallIndex | None = None,
) -> dict[str, list[float]] | None:
    """finding #2「(row_id, probe_index) が既に測定済みならその出力を再利用し、
    二重測定しない」: ledger 上に当該 (candidate_id, row_id, probe_index) の
    within `WITHIN_PROCESS_REPEATS` 回 + fresh `FRESH_PROCESS_REPEATS` 回が
    過不足なく記帳済みなら `f0_hz` 値を process 単位でまとめて返す
    （`observables.two_stage_median` の入力形）。1 件でも欠けていれば
    `None`（呼び出し側は `run_measurement_for_instance` 経由で改めて実測
    する — その resume 判定が同じ ledger 状態から重複記帳なしに解決する）。

    round 14 finding #3: coverage 判定は
    `measure_stage._completed_meter_call_records()`（`(repeat_kind,
    repeat_index)` の**厳密な一意キー集合**を要求する唯一の正本 —
    `run_measurement_for_instance` の resume 判定・`StaleMeasurementError`
    の判定基準そのもの）に一本化した。旧実装は独立の subset 比較
    （`expected.issubset(seen)`）と無条件 append で ledger を自前走査して
    おり、同一 `(repeat_kind, repeat_index)` キーへの重複 `meter_call`
    event を検出できないまま — `_completed_meter_call_records()` の
    duplicate-key 拒否を素通りして — 余分な f0_hz 値をそのまま
    `two_stage_median()` へ平均入力してしまっていた。これにより duplicate
    と partial coverage の両方が、この関数を通じて素通り/平均されることなく
    `StaleMeasurementError`（呼び出し元 CLI 経由で未捕捉のまま fail-closed
    伝播 — 他の `measure_stage`/`render_stage` の Stale*Error と同じ契約）
    として扱われる。

    R3（design memo `design_runner_robustness.md`, `[UNDERSPEC-CAL-D79]`）:
    `meter_call_index` が渡されれば `campaign.ledger.entries` を再取得せず
    それ経由で判定する（`_build_f0_by_instance` が stage 呼び出しごとに
    1 回だけ構築した index を渡す）。`None`（既定）なら従来どおり
    `campaign.ledger.entries` を 1 回スキャンする。"""
    records = (
        meter_call_index.completed_records(row_id, probe_index, candidate_id)
        if meter_call_index is not None
        else measure_stage._completed_meter_call_records(
            campaign.ledger.entries, row_id, probe_index, candidate_id
        )
    )
    if records is None:
        return None
    by_process: dict[str, list[float]] = {}
    for record in records:
        f0 = record.output.values.get("f0_hz")
        if not isinstance(f0, (int, float)) or isinstance(f0, bool):
            # Matches the pre-fix semantics: any repeat without a valid
            # f0_hz means this instance is not cleanly reusable as-is —
            # fall through to `run_measurement_for_instance`, whose own
            # resume check re-derives the same complete record set (no
            # duplicate append) and whose caller rebuilds `by_process`
            # itself (see `_build_f0_by_instance` below).
            return None
        by_process.setdefault(record.process_id, []).append(float(f0))
    return by_process


def _build_f0_by_instance(
    campaign: FrozenCampaign,
    instances: Sequence[tuple[str, int]],
    f0_candidate_id: str,
    sr_by_row: Mapping[str, int],
    *,
    max_workers: int,
    cap_counters: CapCounters | None,
    cost_caps: CostCaps | None,
    stage: str,
    discard_partial_groups: bool = False,
    meter_call_index: measure_stage.MeterCallIndex | None = None,
    time_budget: TimeBudget | None = None,
) -> (
    tuple[dict[tuple[str, int], float], frozenset[tuple[str, int]]]
    | tuple[dict[tuple[str, int], float], frozenset[tuple[str, int]], SliceStatus]
):
    """finding #2: 選択済み F0 candidate を `instances` の各 instance 上で
    測定し（ledger に within3+fresh3 が既に揃っていれば再測定しない）、
    `observables.two_stage_median` で instance ごとに 1 スカラーへ集約する。
    F0_CONTROL 以外の family の instance（TILT_GT 等の実音源）に対して、
    その audio 自体から F0 を検出する — fixture の truth F0 は使わない。

    round 27 ADOPT (1) (`[UNDERSPEC-CAL-D61]`) "Reject unusable F0 values
    before downstream injection": round 26 (`[UNDERSPEC-CAL-D58]`) made a
    non-finite `f0_hz` repeat durably round-trip through the ledger instead
    of aborting the work unit inside `canonical_json()` — so a NaN/Inf
    repeat (or a two-stage-median aggregate that comes out non-finite even
    when every repeat is individually valid) now silently reaches this
    function's `result` dict, from where `_params_with_f0()` injects it into
    every F0-dependent candidate's `params["f0_hz"]`.
    `formant_cepstral.cepstral_envelope_db()` treats a non-finite/
    non-positive `f0_hz` as "use the default lifter cutoff" rather than
    reporting missing (unlike `aperiodicity.py`/`tilt_harmonic.py`'s own
    F0-dependent candidates, which already self-report OUTPUT_MISSING/
    INPUT_MISSING on invalid F0) — so an unusable F0 could freeze a finite,
    plausible-looking formant output as if a valid selected F0 had been
    used, and be indistinguishable downstream from a genuine measurement.

    Guard (the meter implementation is frozen — the guard lives here, in
    the runner): every individual repeat AND the two-stage-median aggregate
    must be `math.isfinite` and strictly positive. If not, the instance is
    excluded from the returned `result` (its F0 is never injected into any
    candidate's params for this stage) and is instead added to the second
    return value, `f0_unusable_instances`. Callers pass that set through to
    `measure_stage.run_measure_stage()`/`holdout_stage.
    render_and_measure_holdout()`, which skip calling any
    `measure_stage.F0_DEPENDENT_ALGORITHM_FAMILIES` candidate on these
    instances entirely — the candidate's `measure()` is never invoked, so
    `formant_cepstral.py`'s own default-cutoff substitution is never
    reached (this is why merely not injecting the key would not have been
    enough: the call itself must not happen). A non-empty rejection set is
    recorded as an `f0_injection_rejected` ledger event (`reason:
    "F0_UNUSABLE"`, tagged with `stage`) for provenance.

    round 28 ADOPT (1) (`[UNDERSPEC-CAL-D63]`) "Mark empty F0 measurements
    unusable": the guard above only fires once `by_process` is non-empty.
    When the selected F0 meter returns `OUTPUT_MISSING` (`f0_hz is None`)
    for every one of an instance's 6 repeats — either freshly measured here
    or reconstructed from the ledger by
    `_reusable_f0_values_by_process()` (which itself returns `None`,
    routing back through this branch, whenever any single repeat's `f0_hz`
    is missing/non-numeric) — `by_process` comes back `{}` and the prior
    code took a bare `continue`: the instance was silently dropped from
    both `result` *and* `unusable`. `_params_with_f0()` then received no
    injected F0 for that instance and fell through to
    `params.get("f0_hz")`'s caller-supplied default of `None`, which
    `formant_cepstral.cepstral_envelope_db()` — like the non-finite/
    non-positive case above — treats as "use the default lifter cutoff
    (64)" rather than reporting missing, producing the exact same
    plausible-but-unfounded finite output this guard exists to prevent.
    Ruling: an empty (or, after the repeat-level check below, partially
    empty) F0 observation set that cannot yield a finite, strictly positive
    two-stage-median aggregate is `F0_UNUSABLE` exactly like non-finite/
    non-positive repeats — there is no third outcome. The instance is now
    added to `unusable` (not silently dropped) whenever `by_process` comes
    back empty, so it reaches the same `f0_injection_rejected` ledger event
    and the same `F0_DEPENDENT_ALGORITHM_FAMILIES` skip in
    `measure_stage.run_measure_stage()`/`holdout_stage.
    render_and_measure_holdout()` as every other unusable case.

    R1 の `discard_partial_groups`/R3 の `meter_call_index`（design memo
    `design_runner_robustness.md`, `[UNDERSPEC-CAL-D79]`）は素通しで
    `_reusable_f0_values_by_process`/`measure_stage.run_measurement_for_
    instance` へ渡す。

    R2: `time_budget` が渡されれば instance 境界（1 instance = この F0
    candidate の 1 回の測定）で予算超過を検査し、超過していれば以降の
    instance を dispatch しない（既に dispatch 済みの instance は完走する）。
    この場合、戻り値は `(result, unusable, SliceStatus)` の 3-tuple になる。
    `time_budget` が `None`（既定）のときは従来どおり `(result, unusable)`
    の 2-tuple を返す（呼び出し元の挙動・シグネチャは不変）。"""
    f0_candidate = candidate_by_id(f0_candidate_id)
    result: dict[tuple[str, int], float] = {}
    unusable: set[tuple[str, int]] = set()
    sorted_instances = sorted(set(instances))
    instances_completed_this_run = 0
    completed_all = True
    for row_id, probe_index in sorted_instances:
        # R2 instance boundary: checked before dispatching a NEW instance —
        # an instance already in flight always runs to completion.
        if time_budget is not None and time_budget.expired():
            completed_all = False
            break
        instances_completed_this_run += 1
        by_process = _reusable_f0_values_by_process(
            campaign, f0_candidate_id, row_id, probe_index, meter_call_index=meter_call_index
        )
        # round 28 ADOPT (1) (`[UNDERSPEC-CAL-D63]`): tracks whether any
        # repeat came back OUTPUT_MISSING (f0_hz is None) during the manual
        # `by_process` reconstruction below — the *sole* place a partially-
        # missing repeat set can arise (`_reusable_f0_values_by_process()`
        # is all-or-nothing: it returns `None`, routing here, the moment any
        # one repeat is non-numeric). A repeat dropped by the `continue`
        # below vanishes from every `all_repeats_usable`/`two_stage_median`
        # view that only sees `by_process`'s surviving values — so it must
        # be tracked independently, not inferred from `by_process` alone.
        missing_repeat = False
        if by_process is None:
            records = measure_stage.run_measurement_for_instance(
                campaign,
                f0_candidate,
                row_id=row_id,
                probe_index=probe_index,
                sr_hz=sr_by_row[row_id],
                cap_counters=cap_counters,
                cost_caps=cost_caps,
                max_workers=max_workers,
                discard_partial_groups=discard_partial_groups,
                stage=stage,
                meter_call_index=meter_call_index,
            )
            by_process = {}
            for r in records:
                f0 = r.output.values.get("f0_hz")
                if f0 is None:
                    missing_repeat = True
                    continue
                by_process.setdefault(r.process_id, []).append(float(f0))
        if not by_process or missing_repeat:
            # round 28 ADOPT (1) (`[UNDERSPEC-CAL-D63]`): an empty (every
            # repeat OUTPUT_MISSING) or partially-empty (some repeats
            # OUTPUT_MISSING, dropped above rather than surviving into
            # `by_process`) F0 observation set is F0_UNUSABLE exactly like
            # non-finite/non-positive repeats — never a silent drop (see
            # docstring above).
            unusable.add((row_id, probe_index))
            continue
        all_repeats_usable = all(
            math.isfinite(v) and v > 0.0
            for repeats in by_process.values()
            for v in repeats
        )
        if not all_repeats_usable:
            unusable.add((row_id, probe_index))
            continue
        aggregate = two_stage_median(by_process)
        if not math.isfinite(aggregate) or aggregate <= 0.0:
            unusable.add((row_id, probe_index))
            continue
        result[(row_id, probe_index)] = aggregate
    if unusable:
        campaign.ledger.append(
            {
                "kind": "f0_injection_rejected",
                "stage": stage,
                "reason": "F0_UNUSABLE",
                "instances": [[rid, pidx] for rid, pidx in sorted(unusable)],
            }
        )
    if time_budget is None:
        return result, frozenset(unusable)
    slice_status = SliceStatus(
        time_budget_seconds=time_budget.seconds,
        elapsed_seconds=time_budget.elapsed(),
        instances_completed_this_run=instances_completed_this_run,
        instances_remaining=len(sorted_instances) - instances_completed_this_run,
        completed_all=completed_all,
    )
    return result, frozenset(unusable), slice_status


def _positive_instances_for_selection(
    rows: Sequence[Any], assignment: Mapping[str, Any], family: str
) -> frozenset[tuple[str, int]]:
    """round 13 finding #1: positive evidence = every TRUTH_CORE row of the
    evaluated SELECTION split for `family` (`fixtures.controls.
    positive_detection_instances()`, DESIGN RULING per `fixtures/controls.py`
    module docstring), not just the 2 designated anchors
    (`positive_control_row_ids()`). The 2-anchor row_id set under-covers:
    each anchor's home split is HMAC-derived and may not include SELECTION at
    all, in which case `candidate_fail_filter_report()` silently treated the
    positive-control filter as inapplicable instead of ineligible
    (`[UNDERSPEC-CAL-D25]`).

    round 28 ADOPT (2) (`[UNDERSPEC-CAL-D64]`): returns the full
    `(row_id, probe_index)` instance set (not just row_id). Callers derive
    the row-id-only view for the row-level `positive_control_non_fire`/
    `positive_rows_absent` filters with `frozenset(row_id for row_id, _ in
    instances)`.

    round 30 self-review ADOPT (1) (`[UNDERSPEC-CAL-D68]`): this TRUTH_CORE
    -only instance set no longer feeds `coverage_incomplete` directly — that
    filter's expected-instance population was widened to TRUTH_CORE +
    CONFOUND (see `_expected_coverage_instances_for_selection()` below).
    This function's row-id-only derivative still exclusively backs
    `positive_control_row_ids`/`positive_control_non_fire`/
    `positive_rows_absent`, which must stay TRUTH_CORE-scoped (a CONFOUND
    row is not a positive-detection anchor)."""
    return positive_detection_instances(rows, assignment, Split.SELECTION, family=family)


def _expected_coverage_instances_for_selection(
    rows: Sequence[Any], assignment: Mapping[str, Any], family: str
) -> frozenset[tuple[str, int]]:
    """round 30 self-review ADOPT (1) (`[UNDERSPEC-CAL-D68]`): the expected
    non-BOUNDARY (TRUTH_CORE + CONFOUND) instance population for `family`'s
    evaluated SELECTION split (`fixtures.controls.
    non_boundary_selection_instances()`), passed as `candidate_fail_filter_
    report()`'s `expected_coverage_instances` — deliberately a *separate*
    function from `_positive_instances_for_selection()` above (whose
    TRUTH_CORE-only set must stay narrow for `positive_control_row_ids`).
    See `non_boundary_selection_instances()`'s docstring for the fail-open
    scenario this closes (a candidate consistently missing on hard CONFOUND
    rows was invisible to D64's TRUTH_CORE-only `coverage_incomplete`)."""
    return non_boundary_selection_instances(rows, assignment, Split.SELECTION, family=family)


def _criteria_with_fail_filters(
    candidate: Any,
    records: Sequence[Any],
    truth_by_instance: Mapping[tuple[str, int], float],
    *,
    negative_control_ids: frozenset[str],
    positive_control_ids: frozenset[str],
    expected_coverage_instances: frozenset[tuple[str, int]] = frozenset(),
    max_claim_scope: frozenset[str],
) -> tuple[Any, dict[str, bool], dict[str, object]]:
    """finding #8: `build_candidate_criteria()`（有限値の有無のみ）に加えて
    `candidates.adapter` 共通 5 fail filter を適用し、いずれか 1 つでも
    発火していれば `eligible=False` へ落とす。finding #11: さらに
    `max_claim_scope` 外の construct なら ceiling を capping する
    （`select_across_ceilings` へ渡す前に反映 — capping 済み ceiling で
    ABSOLUTE pool から除外される）。round 28 ADOPT (2) (`[UNDERSPEC-CAL-D64]`)
    →round 30 self-review ADOPT (1) (`[UNDERSPEC-CAL-D68]`): `expected_
    coverage_instances`（`_expected_coverage_instances_for_selection()` —
    TRUTH_CORE + CONFOUND、D64 時点の TRUTH_CORE 限定から拡張済み）を
    `candidate_fail_filter_report()` の同名引数へそのまま渡し、
    `coverage_incomplete` filter を通す。`(criteria, fail_filter_report,
    claim_scope_report)` を返す — 呼び出し元はこれらを
    `run_c3a_f0_selection`/`run_c3b_selection` の対応する `*_reports*` へ
    積み上げて SELECTION_FROZEN payload に記録する。"""
    base = selection_stage.build_candidate_criteria(candidate, records, truth_by_instance)
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        negative_control_row_ids=negative_control_ids,
        positive_control_row_ids=positive_control_ids,
        expected_coverage_instances=expected_coverage_instances,
    )
    eligible = base.eligible and selection_stage.eligible_after_fail_filters(report)
    capped, scope_report = selection_stage.claim_scope_report(candidate, max_claim_scope)
    criteria = dataclasses.replace(base, eligible=eligible, ceiling=capped)
    return criteria, report, scope_report


def _checkpoint_parent_cpu_before_transition(
    campaign: FrozenCampaign,
    cap_counters: CapCounters,
    cost_caps: CostCaps,
    parent_cpu_checkpoint: list[float],
) -> StopDecision | None:
    """round 16 finding #2 ordering ruling (`[UNDERSPEC-CAL-D34]`): charge
    the parent-process CPU accumulated since the last checkpoint (dispatch
    start, or an earlier pre-transition checkpoint within this same
    dispatch — `parent_cpu_checkpoint[0]`) to the compute cap and check it,
    called immediately before a stage wrapper's own call into the library
    function that appends that stage's phase-transition ledger event
    (`f0_selection_frozen`/`selection_frozen`/`holdout_executed_valid`/
    `campaign_closed`), so a breach blocks the transition — the caller must
    return a `COST_CAP_EXCEEDED` result *without* making that call when this
    returns non-`None`.

    Scope: only wired into `_run_c3a`/`_run_c3b`/`_run_c4`/`_run_close`.
    `c1-fixtures`/`c2-baseline` deliberately are not: those two delegate
    their entire stage body (every render/measure per-unit charge *and* the
    phase-transition event itself) to `render_stage`/`baseline_stage` in a
    single call with no intervening `cli.py`-side work, so a checkpoint
    immediately before that call would charge/check `cap_counters`
    unchanged from what `_refuse_if_caps_already_breached` (the dispatch-
    entry guard) already checked moments earlier — a same-value no-op. Those
    two stages still get the `finally`-block recheck in `main()` (the base
    round 16 finding #2 fix), same as every other stage.

    Mutates `parent_cpu_checkpoint[0]` to the new checkpoint so `main()`'s
    `finally` block charges only the residual CPU spent after this point,
    rather than double-charging the portion already charged here."""
    now = _process_cpu_seconds()
    delta = now - parent_cpu_checkpoint[0]
    if delta < 0.0:  # pragma: no cover - defensive only
        delta = 0.0
    parent_cpu_checkpoint[0] = now
    cap_counters.add(compute=delta)
    return _refuse_if_caps_already_breached(campaign, cost_caps, cap_counters)


def _partial_slice_report(stage: str, slice_status: SliceStatus) -> dict[str, Any]:
    """R2（design memo `design_runner_robustness.md`, `[UNDERSPEC-CAL-D79]`）:
    the `PARTIAL_SLICE` CLI report shape shared by every `--time-budget-
    seconds` stage runner — no phase transition happened (the caller must
    not have appended it before calling this), so `main()`'s `finally`
    block skips the `stage_summary` ledger event for this dispatch too
    (parent CPU is still charged to `cap_counters`/`counters.json` — see
    `main()`)."""
    return {
        "result": "PARTIAL_SLICE",
        "stage": stage,
        "slice": slice_status.as_report_dict(),
    }


def _run_c1(
    campaign: FrozenCampaign,
    matrix_rows: Sequence[Any],
    *,
    cap_counters: CapCounters | None = None,
    cost_caps: CostCaps | None = None,
    time_budget_seconds: float | None = None,
) -> dict[str, Any]:
    if time_budget_seconds is not None:
        outcomes, slice_status = render_stage.run_render_stage(
            campaign,
            matrix_rows,
            stage="c1",
            cap_counters=cap_counters,
            cost_caps=cost_caps,
            time_budget=TimeBudget.start_now(time_budget_seconds),
        )
        if not slice_status.completed_all:
            return _partial_slice_report("c1-fixtures", slice_status)
    else:
        outcomes = render_stage.run_render_stage(
            campaign, matrix_rows, stage="c1", cap_counters=cap_counters, cost_caps=cost_caps
        )
    return {
        "result": "OK",
        "instances": len({(o.row_id, o.probe_index) for o in outcomes}),
        "rendered": sum(1 for o in outcomes if o.status == "rendered"),
        "skipped_resume": sum(1 for o in outcomes if o.status == "skipped_resume"),
    }


def _run_c2(
    campaign: FrozenCampaign,
    matrix_rows: Sequence[Any],
    workers: int,
    *,
    cap_counters: CapCounters | None = None,
    cost_caps: CostCaps | None = None,
    discard_partial_groups: bool = False,
    time_budget_seconds: float | None = None,
) -> dict[str, Any]:
    result = baseline_stage.run_baseline_stage(
        campaign,
        matrix_rows,
        max_workers=workers,
        cap_counters=cap_counters,
        cost_caps=cost_caps,
        discard_partial_groups=discard_partial_groups,
        time_budget=TimeBudget.start_now(time_budget_seconds) if time_budget_seconds else None,
    )
    if "slice_status" in result:
        return _partial_slice_report("c2-baseline", result["slice_status"])
    return {"result": "OK", "baseline_audit_sha": result["baseline_audit_sha"]}


def _run_c3a(
    campaign: FrozenCampaign,
    matrix_rows: Sequence[Any],
    workers: int,
    *,
    cap_counters: CapCounters | None = None,
    cost_caps: CostCaps | None = None,
    parent_cpu_checkpoint: list[float] | None = None,
    discard_partial_groups: bool = False,
    time_budget_seconds: float | None = None,
) -> dict[str, Any]:
    # finding #11: claim scope must be frozen before any selection runs.
    try:
        max_claim_scope = selection_stage.max_claim_scope_from_manifest(campaign.manifest)
    except selection_stage.ClaimScopeError as exc:
        return {"result": "ERROR", "detail": str(exc)}

    assignment = campaign.realized_split.assignment
    instances = workunits.c3a_f0_selection_instances(matrix_rows, assignment)
    sr_by_row = {mr.row_id: mr.row.sr_hz for mr in matrix_rows}
    truth_by_instance = {
        (mr.row_id, p): selection_stage.truth_value_for_row(mr.row)
        for mr in matrix_rows
        if mr.row.family == FixtureFamily.F0_CONTROL.value
        for p in range(_PROBE_REPEATS)
    }
    candidates = candidates_for_meter(MeterId.F0_CONTROL)

    if time_budget_seconds is not None:
        records, slice_status = measure_stage.run_measure_stage(
            campaign,
            instances,
            candidates,
            sr_by_row=sr_by_row,
            max_workers=workers,
            cap_counters=cap_counters,
            cost_caps=cost_caps,
            discard_partial_groups=discard_partial_groups,
            stage="c3a",
            time_budget=TimeBudget.start_now(time_budget_seconds),
        )
        if not slice_status.completed_all:
            return _partial_slice_report("c3a-f0-selection", slice_status)
    else:
        records = measure_stage.run_measure_stage(
            campaign,
            instances,
            candidates,
            sr_by_row=sr_by_row,
            max_workers=workers,
            cap_counters=cap_counters,
            cost_caps=cost_caps,
            discard_partial_groups=discard_partial_groups,
            stage="c3a",
        )
    # round 17 finding #1: scope the declared negative-control population to
    # F0_CONTROL's own rows, matching c3b's per-family scoping below — the
    # unscoped full-matrix set previously "declared" every family's negative
    # controls to the F0_CONTROL candidate, which the new
    # `negative_controls_incomplete` completeness check would then always
    # fail (F0_CONTROL's C3a instance set never includes other families'
    # control rows).
    f0_rows = [mr for mr in matrix_rows if mr.row.family == FixtureFamily.F0_CONTROL.value]
    neg_ids = negative_control_row_ids(f0_rows)
    pos_instances = _positive_instances_for_selection(
        matrix_rows, assignment, FixtureFamily.F0_CONTROL.value
    )
    pos_ids = frozenset(row_id for row_id, _ in pos_instances)
    # round 30 self-review ADOPT (1) (`[UNDERSPEC-CAL-D68]`): apply the same
    # widened (TRUTH_CORE + CONFOUND) expected-instance semantics to C3a's
    # F0_CONTROL family selection, not just C3b/C4's non-F0 families.
    coverage_instances = _expected_coverage_instances_for_selection(
        matrix_rows, assignment, FixtureFamily.F0_CONTROL.value
    )
    known_truth_by_instance = {k: v for k, v in truth_by_instance.items() if v is not None}
    criteria: list[Any] = []
    fail_filter_reports: dict[str, dict[str, bool]] = {}
    claim_scope_reports: dict[str, dict[str, object]] = {}
    for c in candidates:
        candidate_criteria, report, scope_report = _criteria_with_fail_filters(
            c,
            records,
            known_truth_by_instance,
            negative_control_ids=neg_ids,
            positive_control_ids=pos_ids,
            expected_coverage_instances=coverage_instances,
            max_claim_scope=max_claim_scope,
        )
        criteria.append(candidate_criteria)
        fail_filter_reports[c.candidate_id] = report
        claim_scope_reports[c.candidate_id] = scope_report

    # round 16 finding #2 ordering ruling: recheck the compute cap
    # (including this stage's own parent-side CPU so far) immediately
    # before `f0_selection_frozen` is appended below, so a breach blocks
    # the phase transition instead of committing it and only reporting
    # failure afterwards.
    if parent_cpu_checkpoint is not None and cap_counters is not None and cost_caps is not None:
        breach = _checkpoint_parent_cpu_before_transition(
            campaign, cap_counters, cost_caps, parent_cpu_checkpoint
        )
        if breach is not None:
            return {"result": "COST_CAP_EXCEEDED", "detail": breach.detail}

    result = selection_stage.run_c3a_f0_selection(
        campaign,
        criteria,
        fail_filter_reports=fail_filter_reports,
        claim_scope_reports=claim_scope_reports,
    )
    return {
        "result": "OK",
        "selected_candidate_id": result.outcome.selected_candidate_id,
        "outcome": result.outcome.outcome,
    }


def _run_c3b(
    campaign: FrozenCampaign,
    matrix_rows: Sequence[Any],
    workers: int,
    *,
    cap_counters: CapCounters | None = None,
    cost_caps: CostCaps | None = None,
    parent_cpu_checkpoint: list[float] | None = None,
    discard_partial_groups: bool = False,
    time_budget_seconds: float | None = None,
) -> dict[str, Any]:
    time_budget = TimeBudget.start_now(time_budget_seconds) if time_budget_seconds is not None else None
    slice_statuses: list[SliceStatus] = []
    # finding #11: claim scope must be frozen before any selection runs.
    try:
        max_claim_scope = selection_stage.max_claim_scope_from_manifest(campaign.manifest)
    except selection_stage.ClaimScopeError as exc:
        return {"result": "ERROR", "detail": str(exc)}

    baseline_audit_entry_sha = None
    for entry in campaign.ledger.entries:
        payload = entry.payload
        if isinstance(payload, Mapping) and payload.get("kind") == "baseline_audit":
            baseline_audit_entry_sha = entry.entry_sha
    if baseline_audit_entry_sha is None:
        return {"result": "ERROR", "detail": "no baseline_audit event found; run c2-baseline first"}

    # finding #2: C3b requires C3a's frozen F0 selection — F0-dependent
    # candidates (harmonic-tilt/harmonic-residual/D4C) must receive the
    # *selected* F0 candidate's own per-instance output, never fixture truth.
    f0_found, f0_selected_id = _latest_f0_selection(campaign)
    if not f0_found:
        return {
            "result": "ERROR",
            "detail": "no f0_selection_frozen event found; run c3a-f0-selection first",
        }

    assignment = campaign.realized_split.assignment
    sr_by_row = {mr.row_id: mr.row.sr_hz for mr in matrix_rows}

    instances_by_family: dict[str, tuple[tuple[str, int], ...]] = {}
    for family in FixtureFamily:
        if family is FixtureFamily.F0_CONTROL:
            continue
        if not _candidates_for_family(family):
            continue
        instances_by_family[family.value] = workunits.c3b_family_selection_instances(
            matrix_rows, assignment, family.value
        )

    all_instances = sorted({inst for insts in instances_by_family.values() for inst in insts})
    f0_by_instance: dict[tuple[str, int], float] = {}
    f0_unusable_instances: frozenset[tuple[str, int]] = frozenset()
    f0_missing_reason = "F0_UNUSABLE"
    if f0_selected_id is not None:
        if time_budget is not None:
            f0_by_instance, f0_unusable_instances, f0_slice_status = _build_f0_by_instance(
                campaign,
                all_instances,
                f0_selected_id,
                sr_by_row,
                max_workers=workers,
                cap_counters=cap_counters,
                cost_caps=cost_caps,
                stage="c3b",
                discard_partial_groups=discard_partial_groups,
                time_budget=time_budget,
            )
            slice_statuses.append(f0_slice_status)
        else:
            f0_by_instance, f0_unusable_instances = _build_f0_by_instance(
                campaign,
                all_instances,
                f0_selected_id,
                sr_by_row,
                max_workers=workers,
                cap_counters=cap_counters,
                cost_caps=cost_caps,
                stage="c3b",
                discard_partial_groups=discard_partial_groups,
            )
    else:
        # round 29 ADOPT (`[UNDERSPEC-CAL-D65]`): C3a itself recorded
        # SELECTION_FAILED_CLOSED (f0_found is True, f0_selected_id is
        # None) — there is no F0 winner whose per-instance output could be
        # measured or injected, so every instance is F0_UNUSABLE for every
        # F0-dependent candidate (reason F0_SELECTION_FAILED, distinct from
        # the D61/D63/D64 per-instance rejection reason F0_UNUSABLE). This
        # is the "new path where an unusable F0 reaches a candidate" the
        # `[UNDERSPEC-CAL-D61]`/`[UNDERSPEC-CAL-D63]`/`[UNDERSPEC-CAL-D64]`
        # family terminal declaration names as its reopen exception.
        f0_unusable_instances = frozenset(all_instances)
        f0_missing_reason = "F0_SELECTION_FAILED"
        campaign.ledger.append(
            {
                "kind": "f0_dependent_selection_blocked",
                "stage": "c3b",
                "reason": "F0_SELECTION_FAILED",
                "instance_count": len(all_instances),
            }
        )

    criteria_by_family: dict[str, list] = {}
    fail_filter_reports_by_family: dict[str, dict[str, dict[str, bool]]] = {}
    claim_scope_reports_by_family: dict[str, dict[str, dict[str, object]]] = {}
    for family in FixtureFamily:
        if family is FixtureFamily.F0_CONTROL:
            continue
        meter_candidates = _candidates_for_family(family)
        if not meter_candidates:
            continue
        instances = instances_by_family[family.value]
        truth_by_instance = {
            (mr.row_id, p): selection_stage.truth_value_for_row(mr.row)
            for mr in matrix_rows
            if mr.row.family == family.value
            for p in range(_PROBE_REPEATS)
        }
        truth_by_instance = {k: v for k, v in truth_by_instance.items() if v is not None}
        if time_budget is not None:
            records, family_slice_status = measure_stage.run_measure_stage(
                campaign,
                instances,
                meter_candidates,
                sr_by_row=sr_by_row,
                f0_by_instance=f0_by_instance,
                f0_unusable_instances=f0_unusable_instances,
                max_workers=workers,
                cap_counters=cap_counters,
                cost_caps=cost_caps,
                missing_reason=f0_missing_reason,
                discard_partial_groups=discard_partial_groups,
                stage="c3b",
                time_budget=time_budget,
            )
            slice_statuses.append(family_slice_status)
        else:
            records = measure_stage.run_measure_stage(
                campaign,
                instances,
                meter_candidates,
                sr_by_row=sr_by_row,
                f0_by_instance=f0_by_instance,
                f0_unusable_instances=f0_unusable_instances,
                max_workers=workers,
                cap_counters=cap_counters,
                cost_caps=cost_caps,
                missing_reason=f0_missing_reason,
                discard_partial_groups=discard_partial_groups,
                stage="c3b",
            )
        family_rows = [mr for mr in matrix_rows if mr.row.family == family.value]
        neg_ids = negative_control_row_ids(family_rows)
        pos_instances = _positive_instances_for_selection(family_rows, assignment, family.value)
        pos_ids = frozenset(row_id for row_id, _ in pos_instances)
        # round 30 self-review ADOPT (1) (`[UNDERSPEC-CAL-D68]`): widened
        # (TRUTH_CORE + CONFOUND) expected-instance population for
        # `coverage_incomplete`, separate from `pos_instances` above (which
        # must stay TRUTH_CORE-only for `positive_control_row_ids`).
        coverage_instances = _expected_coverage_instances_for_selection(
            family_rows, assignment, family.value
        )
        family_criteria: list[Any] = []
        family_fail_filter_reports: dict[str, dict[str, bool]] = {}
        family_claim_scope_reports: dict[str, dict[str, object]] = {}
        for c in meter_candidates:
            candidate_criteria, report, scope_report = _criteria_with_fail_filters(
                c,
                records,
                truth_by_instance,
                negative_control_ids=neg_ids,
                positive_control_ids=pos_ids,
                expected_coverage_instances=coverage_instances,
                max_claim_scope=max_claim_scope,
            )
            family_criteria.append(candidate_criteria)
            family_fail_filter_reports[c.candidate_id] = report
            family_claim_scope_reports[c.candidate_id] = scope_report
        criteria_by_family[family.value] = family_criteria
        fail_filter_reports_by_family[family.value] = family_fail_filter_reports
        claim_scope_reports_by_family[family.value] = family_claim_scope_reports

    # R2: if any sub-phase (F0 measurement, or any family's measure loop —
    # all sharing the same `time_budget`) did not complete, the whole stage
    # is a PARTIAL_SLICE — no phase transition below.
    if slice_statuses:
        overall_slice_status = SliceStatus.aggregate(slice_statuses)
        if not overall_slice_status.completed_all:
            return _partial_slice_report("c3b-selection", overall_slice_status)

    # round 16 finding #2 ordering ruling: see `_run_c3a`'s identical
    # comment — recheck the compute cap immediately before
    # `selection_frozen` is appended below.
    if parent_cpu_checkpoint is not None and cap_counters is not None and cost_caps is not None:
        breach = _checkpoint_parent_cpu_before_transition(
            campaign, cap_counters, cost_caps, parent_cpu_checkpoint
        )
        if breach is not None:
            return {"result": "COST_CAP_EXCEEDED", "detail": breach.detail}

    result = selection_stage.run_c3b_selection(
        campaign,
        criteria_by_family,
        baseline_audit_entry_sha=baseline_audit_entry_sha,
        fail_filter_reports_by_family=fail_filter_reports_by_family,
        claim_scope_reports_by_family=claim_scope_reports_by_family,
    )
    return {
        "result": "OK",
        "selected_by_family": {
            family: outcome.selected_candidate_id
            for family, outcome in result.outcomes_by_family.items()
        },
    }


_FAMILY_TO_METER: Mapping[FixtureFamily, MeterId] = {
    FixtureFamily.FORMANT_GT: MeterId.M3_FORMANTS,
    FixtureFamily.TILT_GT: MeterId.M2_SPECTRAL_TILT,
    FixtureFamily.APERIODICITY_GT: MeterId.M2_APERIODICITY,
    FixtureFamily.RESONANCE_GT: MeterId.M4_RESONANCE,
    FixtureFamily.TRANSITION_GT: MeterId.M5_TRANSITION,
}


def _candidates_for_family(family: FixtureFamily) -> tuple[Any, ...]:
    meter = _FAMILY_TO_METER.get(family)
    if meter is None:
        return ()
    return candidates_for_meter(meter)


def _run_unseal(campaign: FrozenCampaign, approval_dir: Path) -> dict[str, Any]:
    try:
        result = unseal_stage.unseal_campaign(campaign, approval_dir=approval_dir)
    except unseal_stage.UnsealError as exc:
        return {"result": "UNSEAL_REFUSED", "detail": str(exc)}
    return {
        "result": "OK",
        "holdout_unseal_entry_sha": result.holdout_unseal_entry_sha,
    }


def _run_c4(
    campaign: FrozenCampaign,
    matrix_rows: Sequence[Any],
    workers: int,
    *,
    cap_counters: CapCounters | None = None,
    cost_caps: CostCaps | None = None,
    parent_cpu_checkpoint: list[float] | None = None,
    discard_partial_groups: bool = False,
    time_budget_seconds: float | None = None,
) -> dict[str, Any]:
    time_budget = TimeBudget.start_now(time_budget_seconds) if time_budget_seconds is not None else None
    slice_statuses: list[SliceStatus] = []
    # finding #11: claim scope must be frozen before holdout runs too (the
    # capping fact is recorded per candidate below; see the note at the
    # per-family loop for why the CLI's DIAGNOSTIC_ONLY placeholder ceiling
    # itself is not swapped for the capped value — that would misreport an
    # ABSOLUTE claim no real gate ever evaluated, which [UNDERSPEC-CAL-D17]
    # already forbids).
    try:
        max_claim_scope = selection_stage.max_claim_scope_from_manifest(campaign.manifest)
    except selection_stage.ClaimScopeError as exc:
        return {"result": "ERROR", "detail": str(exc)}

    # finding #2: C4 also feeds F0-dependent candidates the selected F0
    # candidate's own per-instance output (never fixture truth).
    f0_found, f0_selected_id = _latest_f0_selection(campaign)
    if not f0_found:
        return {
            "result": "ERROR",
            "detail": "no f0_selection_frozen event found; run c3a-f0-selection first",
        }

    selected = _selected_candidates_by_family(campaign)
    candidates_by_family: dict[str, tuple[Any, ...]] = {}
    for family in FixtureFamily:
        if family is FixtureFamily.F0_CONTROL:
            continue
        pool = _candidates_for_family(family)
        b0 = tuple(c for c in pool if "-B0-" in c.candidate_id)
        selected_id = selected.get(family.value)
        selected_candidate = tuple(c for c in pool if c.candidate_id == selected_id)
        combined = tuple({c.candidate_id: c for c in (*b0, *selected_candidate)}.values())
        if combined:
            candidates_by_family[family.value] = combined

    assignment = campaign.realized_split.assignment
    sr_by_row = {mr.row_id: mr.row.sr_hz for mr in matrix_rows}
    # UNDERSPEC-CAL-D76 ruling (2)（D75 の nuisance-axis 写像を SUPERSEDE）:
    # sweep_id 分割の唯一の入力は `fixtures.matrix.declared_sweeps_by_
    # family()`（def A: truth-core block の nuisance-constant series）——
    # frozen matrix から決定的に導出する。`gates.resolvable_pairs_
    # possible()` へ渡す `expected_sweep_ids` と、C4 側で数える「usable
    # instance の distinct truth level 数」（ruling (3)）の両方がこの宣言を
    # 単一の入口として共有する（fabricated `"default"` sweep は経由しない）。
    declared_sweeps_by_family_map = declared_sweeps_by_family(matrix_rows)
    row_id_to_sweep_id: dict[str, dict[str, str]] = {}
    for family_value, family_sweeps in declared_sweeps_by_family_map.items():
        for sweep_id, member_row_ids in family_sweeps.items():
            for row_id in member_row_ids:
                row_id_to_sweep_id.setdefault(family_value, {})[row_id] = sweep_id
    row_by_id = {mr.row_id: mr.row for mr in matrix_rows}
    all_instances = sorted(
        {
            inst
            for family in candidates_by_family
            for inst in workunits.c4_holdout_instances(matrix_rows, assignment, family=family)
        }
    )
    f0_by_instance: dict[tuple[str, int], float] = {}
    f0_unusable_instances: frozenset[tuple[str, int]] = frozenset()
    f0_missing_reason = "F0_UNUSABLE"
    if f0_selected_id is not None:
        if time_budget is not None:
            f0_by_instance, f0_unusable_instances, f0_slice_status = _build_f0_by_instance(
                campaign,
                all_instances,
                f0_selected_id,
                sr_by_row,
                max_workers=workers,
                cap_counters=cap_counters,
                cost_caps=cost_caps,
                stage="c4",
                discard_partial_groups=discard_partial_groups,
                time_budget=time_budget,
            )
            slice_statuses.append(f0_slice_status)
        else:
            f0_by_instance, f0_unusable_instances = _build_f0_by_instance(
                campaign,
                all_instances,
                f0_selected_id,
                sr_by_row,
                max_workers=workers,
                cap_counters=cap_counters,
                cost_caps=cost_caps,
                stage="c4",
                discard_partial_groups=discard_partial_groups,
            )
    else:
        # round 29 ADOPT (`[UNDERSPEC-CAL-D65]`): mirrors `_run_c3b`'s else
        # branch — C3a recorded SELECTION_FAILED_CLOSED, so every C4
        # instance is F0_UNUSABLE (reason F0_SELECTION_FAILED) for every
        # F0-dependent candidate here too (defense in depth: a stale
        # `selection_frozen` event from before this fix could otherwise
        # still name a dependent candidate as `selected`).
        f0_unusable_instances = frozenset(all_instances)
        f0_missing_reason = "F0_SELECTION_FAILED"
        campaign.ledger.append(
            {
                "kind": "f0_dependent_selection_blocked",
                "stage": "c4",
                "reason": "F0_SELECTION_FAILED",
                "instance_count": len(all_instances),
            }
        )

    if time_budget is not None:
        records_by_family, render_measure_slice_status = holdout_stage.render_and_measure_holdout(
            campaign,
            matrix_rows,
            candidates_by_family=candidates_by_family,
            max_workers=workers,
            f0_by_instance=f0_by_instance,
            f0_unusable_instances=f0_unusable_instances,
            f0_missing_reason=f0_missing_reason,
            cap_counters=cap_counters,
            cost_caps=cost_caps,
            discard_partial_groups=discard_partial_groups,
            time_budget=time_budget,
        )
        slice_statuses.append(render_measure_slice_status)
    else:
        records_by_family = holdout_stage.render_and_measure_holdout(
            campaign,
            matrix_rows,
            candidates_by_family=candidates_by_family,
            max_workers=workers,
            f0_by_instance=f0_by_instance,
            f0_unusable_instances=f0_unusable_instances,
            f0_missing_reason=f0_missing_reason,
            cap_counters=cap_counters,
            cost_caps=cost_caps,
            discard_partial_groups=discard_partial_groups,
        )

    # finding #11: candidate_id -> Candidate lookup, for annotating gate_detail
    # with claim_scope_report() below (covers every pool this stage touches).
    candidate_by_candidate_id: dict[str, Any] = {
        c.candidate_id: c for pool in candidates_by_family.values() for c in pool
    }

    # finding #10: `results` must cover exactly the 7 `vocab.MeterId` values
    # (holdout_stage.run_holdout_stage now enforces this itself, fail-closed).
    results: list[holdout_stage.MeterHoldoutResult] = []
    for family, meter in _FAMILY_TO_METER.items():
        if meter is MeterId.M4_RESONANCE:
            # M4 always closes DIAGNOSTIC_ONLY regardless of selection
            # (§16-1, handled below via diagnostic_only_close) — it must
            # NOT also go through the generic per-family branch below (that
            # was finding #10's "M4 の二重追加" bug: this family used to
            # produce a *second*, generic MeterHoldoutResult here in
            # addition to the diagnostic_only_close() appended after the
            # loop, silently colliding in run_holdout_stage's old
            # unvalidated per_meter dict).
            continue
        if family.value not in records_by_family:
            results.append(holdout_stage.selection_failed_closed_meter(meter.value))
            continue
        selected_id = selected.get(family.value)
        if selected_id is None:
            results.append(holdout_stage.selection_failed_closed_meter(meter.value))
            continue
        # round 30 ADOPT (`[UNDERSPEC-CAL-D66]`, Codex round 30 PR #343
        # finding #1 「Require records from the selected holdout candidate」
        # 採用): the `family.value not in records_by_family` check above only
        # asks whether *any* candidate in the family produced a record — B0
        # always runs and always supplies at least one, so that check falls
        # through even when the *selected* F0-dependent candidate itself was
        # skipped on every C4 holdout instance (`[UNDERSPEC-CAL-D61]`/
        # `[UNDERSPEC-CAL-D65]`: `render_and_measure_holdout()` never calls
        # it, so its cells are simply absent from `records_by_family`, not
        # present-with-missing_reason). coverage は `selected_id` 自身の
        # record 集合で判定する（B0 の record 存在は無関係）——期待 instance
        # 集合と突き合わせる（集合の中身自体は round 2 #344 P2 で PRIMARY 限定
        # へ改めた。下記コメント参照）。
        #
        # round 30 self-review ADOPT 採用（finding #2/#3/#4）: `[UNDERSPEC-
        # CAL-D66]` の record-presence-only 規約は §11 の 2 帰結を取り違えて
        # いた。design 引用（`DESIGN_VG_METER_CAL_DEBT_v1.0.md` §11）:
        # 「critical output 全欠損 or 最小数割れで score/gate 計算不能 →
        # NOT_EVALUABLE/OUTPUT_NOT_EVALUABLE」対「score 計算可能だが PRIMARY
        # 一部 output missing で gate 不通過 → DIAGNOSTIC_ONLY/OUTPUT_
        # MISSING」——1 件の instance を欠くだけでは score 計算不能にはならず
        # `NOT_EVALUABLE` の対象ではない（finding #2）。**全欠損**（usable な
        # record が 1 件も無い）のみ `NOT_EVALUABLE`/`OUTPUT_NOT_EVALUABLE`
        # とし、**部分被覆**（1 件以上は usable だが期待集合の一部を欠く）は
        # `DIAGNOSTIC_ONLY`/`OUTPUT_MISSING` へ倒す（gate 1 不通過の事実は
        # `gate_detail` に記録する）。完全被覆時は無変更。
        #
        # finding #3: 判定を **値の有無** にする——record が存在するだけでは
        # 「usable」と数えない。`measure_stage.primary_output_value()`（missing_
        # reason/ineligible を除外）+ `math.isfinite()` で有限値が読み取れる
        # record のみ usable instance に数える。`selected_candidate_obj` が
        # プールに見つからない（`selected_id` が stale/無効）場合は
        # `algorithm_family` が引けないため record-presence へフォールバック
        # する——この場合 `own_selected_records` 自体が空であることがほとんど
        # で、いずれにせよ全欠損 NOT_EVALUABLE 枝に落ちる。
        #
        # finding #4: `claim_scope_report()` を分岐の**前**で計算し、
        # NOT_EVALUABLE/DIAGNOSTIC_ONLY いずれの `gate_detail` にも
        # `claim_scope` を含める（旧実装は早期 `continue` でこの監査事実を
        # 落としていた）。
        own_selected_records = [
            r for r in records_by_family[family.value] if r.candidate_id == selected_id
        ]
        # round 2 #344 ADOPT (`[UNDERSPEC-CAL-D72]`, amends D69): the coverage
        # set used to belong to `workunits.c4_holdout_instances()`, which is
        # HOLDOUT-split minus *negative-control* rows only — it still
        # includes non-control BOUNDARY-domain rows (boundary-axis probes,
        # `fixtures.matrix.compute_domain()`). A correct, expected miss on
        # one of those BOUNDARY instances (e.g. a candidate legitimately
        # returning `OUTPUT_MISSING` at an edge-of-range F0/SR/gain/
        # duration/noise axis) therefore fell into `missing_expected_
        # instances` and falsely produced a `DIAGNOSTIC_ONLY`/`OUTPUT_
        # MISSING` (or, with zero PRIMARY coverage too, `NOT_EVALUABLE`)
        # gate-1 coverage failure for BOUNDARY rows, which DESIGN_VG_METER_
        # CAL_DEBT_v1.0.md §10.3 (~L351-361) does not ask this gate to
        # police: "gate 1: 全 PRIMARY instance が eligible（critical missing/
        # undefined なし）" — the eligibility precondition ahead of gate 2'.
        # BOUNDARY rows are handled by their own separate gate/diagnostic
        # treatment, not folded into this PRIMARY completeness check.
        # Ruling: build the coverage
        # set from PRIMARY-domain rows only, reusing `fixtures.controls.
        # non_boundary_selection_instances()` (already used for the C3
        # selection-side D68/D71 coverage population, `domain ==
        # Domain.PRIMARY` = TRUTH_CORE ∪ CONFOUND) against `Split.HOLDOUT`
        # instead of `Split.SELECTION`. `workunits.c4_holdout_instances()`
        # itself is unchanged and still used above (`all_instances`) to
        # decide what actually gets rendered/measured in C4 — BOUNDARY rows
        # are still measured, just not counted toward this completeness
        # gate.
        expected_holdout_instances = non_boundary_selection_instances(
            matrix_rows, assignment, Split.HOLDOUT, family=family.value
        )
        selected_candidate_obj = candidate_by_candidate_id.get(selected_id)
        claim_scope_detail: dict[str, object] = {}
        # round 4 #344 ADOPT (`[UNDERSPEC-CAL-D74]`): the *effective* ceiling
        # (post `capped_ceiling()`/`max_claim_scope` capping) decides below
        # whether the DIRECTIONAL-only minimum-count precondition applies —
        # previously discarded as `_capped`, now kept.
        effective_ceiling: ClaimCeiling | None = None
        if selected_candidate_obj is not None:
            effective_ceiling, claim_scope_detail = selection_stage.claim_scope_report(
                selected_candidate_obj, max_claim_scope
            )
            # UNDERSPEC-CAL-D76 ruling (4): an instance counts as usable
            # only if ALL of its within/fresh repeats are present, finite,
            # and mutually consistent (D67) — see
            # `measure_stage.usable_primary_instances()`. The prior set
            # comprehension marked an instance usable if ANY single repeat
            # (out of 6) happened to be finite, an existential collapse that
            # let 5 missing/divergent repeats hide behind 1 lucky one.
            usable_holdout_instances = measure_stage.usable_primary_instances(
                selected_candidate_obj, own_selected_records
            )
        else:
            usable_holdout_instances = {(r.row_id, r.probe_index) for r in own_selected_records}
        missing_expected_instances = expected_holdout_instances - usable_holdout_instances
        # round 2 #344 ADOPT (`[UNDERSPEC-CAL-D72]`): "zero usable output"
        # must mean zero usable *PRIMARY* output — `usable_holdout_instances`
        # itself is not domain-scoped (it is built from every record the
        # selected candidate produced, PRIMARY and BOUNDARY alike, since
        # `all_instances`/`render_and_measure_holdout()` above still render
        # BOUNDARY too). Without this, a candidate with zero usable PRIMARY
        # records but at least one usable BOUNDARY record would skip this
        # branch (`usable_holdout_instances` non-empty) and wrongly fall
        # through to the partial-coverage `DIAGNOSTIC_ONLY`/`OUTPUT_MISSING`
        # branch below instead of `NOT_EVALUABLE`. The plain `not usable_
        # holdout_instances` disjunct is kept for its original meaning (zero
        # usable output on *any* row — e.g. the candidate was never even
        # called, `[UNDERSPEC-CAL-D61]`/`[UNDERSPEC-CAL-D65]`) so an empty
        # PRIMARY population (`expected_holdout_instances`) does not itself
        # force NOT_EVALUABLE when some other (BOUNDARY) output exists.
        usable_primary_instances = usable_holdout_instances & expected_holdout_instances
        if not usable_holdout_instances or (
            expected_holdout_instances and not usable_primary_instances
        ):
            results.append(
                holdout_stage.MeterHoldoutResult(
                    meter_id=meter.value,
                    terminal_status=TerminalStatus.NOT_EVALUABLE.value,
                    reason_code=MissingReason.OUTPUT_NOT_EVALUABLE.value,
                    ceiling=ClaimCeiling.NONE.value,
                    selected_candidate_id=selected_id,
                    gate_detail={
                        "reason": (
                            "[UNDERSPEC-CAL-D69] selected holdout candidate has no "
                            "measurement record with a usable (finite, non-missing) "
                            "primary value for any expected C4 instance"
                        ),
                        "expected_instance_count": len(expected_holdout_instances),
                        "seen_instance_count": 0,
                        "claim_scope": claim_scope_detail,
                    },
                )
            )
            continue
        # round 4 #344 ADOPT (`[UNDERSPEC-CAL-D74]`, amends `[UNDERSPEC-
        # CAL-D73]`): D73's frozen minimum-count / resolvable-pair
        # precondition below is a §10.4 DIRECTIONAL-gate concept
        # ("resolvable pair は各 sweep で >= 3", design §10.4 ~L375,
        # directly under §10.3) — D73 wrongly applied it to every meter
        # regardless of the selected candidate's effective ceiling.
        # Design quote, §10.3 ABSOLUTE holdout gate (~L351-353): "gate 1:
        # 全 PRIMARY instance が eligible（critical missing/undefined
        # なし）" — §10.3 states no minimum sample count beyond that
        # eligibility precondition; MAE/BIAS/q95 (§10.1) are computable
        # from any nonzero-usable-instance population. Applying D73's
        # DIRECTIONAL-only minimum to an ABSOLUTE-ceiling candidate
        # therefore produced a false `NOT_EVALUABLE` for a candidate
        # whose score genuinely is computable (Codex #344 round 4,
        # category ③ false terminal status). Ruling: the structural
        # minimum-count check applies **only** when this meter's
        # *effective* claim ceiling — `effective_ceiling` above, the same
        # `capped_ceiling()`/`max_claim_scope` arbitration
        # `claim_scope_report()` already performs — is `DIRECTIONAL`; for
        # every other effective ceiling (ABSOLUTE included), nonzero-but-
        # partial coverage is unconditionally `DIAGNOSTIC_ONLY`/
        # `OUTPUT_MISSING` (§11 "score 計算可能だが PRIMARY 一部 output
        # missing で gate 不通過"), matching D69/D72's original rule.
        # When the check does apply, `gates.resolvable_pairs_possible()`
        # is still the sole threshold authority (`gates.
        # MIN_RESOLVABLE_PAIRS_PER_SWEEP`). round 6-7 #344 ADOPT
        # (`[UNDERSPEC-CAL-D76]` ruling (2)/(3), SUPERSEDES D75 ruling
        # (1)): D75 partitioned usable PRIMARY instances by nuisance
        # axis (`nuisance_axis_family()`) and counted raw usable-
        # instance rows per axis — but nuisance axis is not sweep (see
        # `sweep_truth_investigation.md`: a nuisance-axis group holds
        # truth fixed at the family anchor, so every pair inside it has
        # `delta_truth == 0` and can never be resolvable under §10.4).
        # The real declared sweep partition is `fixtures.matrix.
        # declared_sweeps_by_family()` (def A: truth-core block,
        # nuisance-constant series — `row_id_to_sweep_id` above), and
        # the count that matters is the number of DISTINCT TRUTH LEVELS
        # among usable instances in that sweep (row-level `row_id`,
        # never `probe_index` repeats) — two usable rows at the SAME
        # truth level still form an unresolvable `delta_truth == 0`
        # pair, so counting raw instances (as D75 did) over-counts.
        #
        # round 8 #344 ADOPT (`[UNDERSPEC-CAL-D77]` ruling (2), 分類③ false
        # terminal): the block below used to run only inside `if
        # expected_holdout_instances and missing_expected_instances:` — i.e.
        # only on *partial* coverage. A DIRECTIONAL-ceiling candidate with
        # FULL holdout coverage on a partition where some declared sweep
        # still has < `MIN_RESOLVABLE_PAIRS_PER_SWEEP` distinct truth levels
        # among usable instances therefore skipped this check entirely and
        # fell through to the generic `DIAGNOSTIC_ONLY` placeholder further
        # below — recording a status that implies "score/gate assembly
        # pending" for a candidate whose score is, in fact, structurally
        # uncomputable (same §10.4 ~L375 fact as D76 ruling (2)/(3), just
        # reached via a different — complete, not partial — coverage path).
        # Ruling: perform this capacity check on the usable PRIMARY holdout
        # instances **regardless of coverage completeness**, and **before**
        # the partial-vs-complete coverage branch below (still gated on
        # `effective_ceiling is ClaimCeiling.DIRECTIONAL`, so ABSOLUTE-
        # ceiling candidates are unaffected — §10.3 does not depend on sweep
        # structure). Capacity unmet → `NOT_EVALUABLE` even when coverage is
        # complete; capacity met (or ceiling is not DIRECTIONAL) → fall
        # through to the existing partial-coverage `DIAGNOSTIC_ONLY`/
        # `OUTPUT_MISSING` branch, or, when coverage is also complete, to
        # the pre-existing generic `DIAGNOSTIC_ONLY` placeholder unchanged.
        n_usable_primary = len(usable_primary_instances)
        if expected_holdout_instances and effective_ceiling is ClaimCeiling.DIRECTIONAL:
            usable_row_ids_by_sweep: dict[str, set[str]] = {}
            for row_id, _probe_index in usable_primary_instances:
                sweep_id = row_id_to_sweep_id.get(family.value, {}).get(row_id)
                if sweep_id is None:
                    continue  # not a declared-sweep member (e.g. a targeted-interaction row)
                usable_row_ids_by_sweep.setdefault(sweep_id, set()).add(row_id)
            usable_truth_level_counts_by_sweep: dict[str, int] = {
                sweep_id: len({truth_identity_for_row(row_by_id[rid]) for rid in row_ids})
                for sweep_id, row_ids in usable_row_ids_by_sweep.items()
            }
            expected_sweep_ids = set(declared_sweeps_by_family_map.get(family.value, {}))
            directional_minimum_not_met = not resolvable_pairs_possible(
                usable_truth_level_counts_by_sweep, expected_sweep_ids
            )
            if directional_minimum_not_met:
                sweeps_below_minimum = sorted(
                    s
                    for s in expected_sweep_ids
                    if math.comb(usable_truth_level_counts_by_sweep.get(s, 0), 2)
                    < MIN_RESOLVABLE_PAIRS_PER_SWEEP
                )
                results.append(
                    holdout_stage.MeterHoldoutResult(
                        meter_id=meter.value,
                        terminal_status=TerminalStatus.NOT_EVALUABLE.value,
                        reason_code=MissingReason.OUTPUT_NOT_EVALUABLE.value,
                        ceiling=ClaimCeiling.NONE.value,
                        selected_candidate_id=selected_id,
                        gate_detail={
                            "reason": (
                                "[UNDERSPEC-CAL-D77] DIRECTIONAL_SWEEP_UNRESOLVABLE_ON_"
                                "HOLDOUT: declared sweep(s) "
                                f"{sweeps_below_minimum} have too few distinct truth "
                                "levels among usable holdout instances: each needs "
                                f"C(levels,2) >= gates.MIN_RESOLVABLE_PAIRS_PER_SWEEP="
                                f"{MIN_RESOLVABLE_PAIRS_PER_SWEEP} (design §10.4 ~L375); "
                                "score/gate is uncomputable regardless of measured "
                                "values, even when holdout coverage for this meter is "
                                "otherwise complete (D77 ruling (2): this capacity "
                                "check now runs unconditionally for DIRECTIONAL-"
                                "ceiling candidates, not only when "
                                "missing_expected_instances is non-empty). This is "
                                "the expected, honest outcome for DIRECTIONAL-ceiling "
                                "candidates under the canonical 456-cell matrix + "
                                "(block, domain) holdout split (README ledger D76/D77 "
                                "— a design fact, not a bug to work around; sweep-"
                                "aware stratification is a candidate for a future "
                                "design v1.1 revision)."
                            ),
                            "gate_detail_reason_code": "DIRECTIONAL_SWEEP_UNRESOLVABLE_ON_HOLDOUT",
                            "expected_instance_count": len(expected_holdout_instances),
                            "seen_instance_count": n_usable_primary,
                            "min_resolvable_pairs_per_sweep": MIN_RESOLVABLE_PAIRS_PER_SWEEP,
                            "expected_sweep_ids": sorted(expected_sweep_ids),
                            "usable_truth_level_counts_by_sweep": dict(
                                sorted(usable_truth_level_counts_by_sweep.items())
                            ),
                            "sweeps_below_minimum": sweeps_below_minimum,
                            "effective_ceiling": (
                                effective_ceiling.value if effective_ceiling is not None else None
                            ),
                            "claim_scope": claim_scope_detail,
                            "coverage_complete": not bool(missing_expected_instances),
                        },
                    )
                )
                continue
        if expected_holdout_instances and missing_expected_instances:
            results.append(
                holdout_stage.MeterHoldoutResult(
                    meter_id=meter.value,
                    terminal_status=TerminalStatus.DIAGNOSTIC_ONLY.value,
                    reason_code=MissingReason.OUTPUT_MISSING.value,
                    ceiling=ClaimCeiling.DIAGNOSTIC_ONLY.value,
                    selected_candidate_id=selected_id,
                    gate_detail={
                        "reason": (
                            "[UNDERSPEC-CAL-D69] gate 1 not passed: selected holdout "
                            "candidate has a usable primary value for some but not all "
                            "expected C4 instances"
                        ),
                        "expected_instance_count": len(expected_holdout_instances),
                        "seen_instance_count": n_usable_primary,
                        "claim_scope": claim_scope_detail,
                    },
                )
            )
            continue
        # finding #11: record the claim-scope capping fact for this meter's
        # selected candidate (§b/§c). The placeholder `ceiling` below stays
        # DIAGNOSTIC_ONLY regardless — swapping it for the capped ceiling
        # would misreport an ABSOLUTE claim no real gate ever evaluated
        # ([UNDERSPEC-CAL-D17]); real gate assembly (out of CLI scope) is
        # where `evaluate_absolute_meter`/`evaluate_directional_meter`
        # would receive the capped ceiling directly as their `ceiling` arg.
        results.append(
            holdout_stage.MeterHoldoutResult(
                meter_id=meter.value,
                terminal_status="DIAGNOSTIC_ONLY",
                reason_code=None,
                ceiling=ClaimCeiling.DIAGNOSTIC_ONLY.value,
                selected_candidate_id=selected_id,
                gate_detail={
                    "note": (
                        "[UNDERSPEC-CAL-D17] full E_use-bound absolute/directional gate "
                        "assembly from CLI is out of D2 infra scope; holdout_stage "
                        "evaluate_absolute_meter/evaluate_directional_meter building "
                        "blocks are exercised directly in tests with real gate wiring."
                    ),
                    "claim_scope": claim_scope_detail,
                },
            )
        )

    # M4_RESONANCE (§16-1: always DIAGNOSTIC_ONLY, selection not gate-tested).
    results.append(
        holdout_stage.diagnostic_only_close(
            MeterId.M4_RESONANCE.value,
            selected_candidate_id=selected.get(FixtureFamily.RESONANCE_GT.value),
        )
    )

    # F0_CONTROL (finding #10): the upstream control's own terminal status,
    # derived from the C3a f0_selection_frozen outcome. F0_CONTROL feeds
    # other meters' params["f0_hz"] per instance (finding #2) rather than
    # being independently gate-evaluated in C4.
    if f0_selected_id is None:
        results.append(holdout_stage.selection_failed_closed_meter(MeterId.F0_CONTROL.value))
    else:
        results.append(
            holdout_stage.diagnostic_only_close(
                MeterId.F0_CONTROL.value,
                selected_candidate_id=f0_selected_id,
                reason=(
                    "F0_CONTROL feeds other meters' params['f0_hz'] per-instance "
                    "(finding #2); not independently gate-evaluated in C4."
                ),
            )
        )

    # M6_IDENTITY (finding #10): evaluated only when every claim-critical
    # meter (vocab.CLAIM_CRITICAL_SET) reached ABSOLUTE ceiling; otherwise
    # NOT_EVALUABLE. Under the current D2 CLI scope the per-family branch
    # above always assigns DIAGNOSTIC_ONLY (never ABSOLUTE — see the D17
    # note), so this correctly resolves to NOT_EVALUABLE today; the check
    # itself is real (not hardcoded) so it lights up once real gate
    # assembly lands upstream.
    critical_ceilings = {r.meter_id: r.ceiling for r in results}
    all_critical_absolute = all(
        critical_ceilings.get(m.value) == ClaimCeiling.ABSOLUTE.value for m in CLAIM_CRITICAL_SET
    )
    if all_critical_absolute:
        results.append(
            holdout_stage.MeterHoldoutResult(
                meter_id=MeterId.M6_IDENTITY.value,
                terminal_status=TerminalStatus.DIAGNOSTIC_ONLY.value,
                reason_code=None,
                ceiling=ClaimCeiling.DIAGNOSTIC_ONLY.value,
                selected_candidate_id=None,
                gate_detail={
                    "note": (
                        "[UNDERSPEC-CAL-D17] full M6 identity-preservation gate "
                        "assembly is out of D2 infra scope; all claim-critical "
                        "meters reached ABSOLUTE ceiling (necessary precondition "
                        "satisfied)."
                    )
                },
            )
        )
    else:
        results.append(
            holdout_stage.MeterHoldoutResult(
                meter_id=MeterId.M6_IDENTITY.value,
                terminal_status=TerminalStatus.NOT_EVALUABLE.value,
                reason_code=MissingReason.OUTPUT_NOT_EVALUABLE.value,
                ceiling=ClaimCeiling.NONE.value,
                selected_candidate_id=None,
                gate_detail={
                    "reason": "not all claim-critical meters reached ABSOLUTE ceiling",
                },
            )
        )

    # R2: if any sub-phase (F0 measurement, C4 render, or any family's
    # measure loop — all sharing the same `time_budget`) did not complete,
    # the whole stage is a PARTIAL_SLICE — no phase transition below. The
    # gate/status computation above ran on `records_by_family` regardless
    # (it is pure and side-effect free — no ledger writes between the
    # `render_and_measure_holdout()` call above and here), but its result
    # (`results`) is simply discarded in favor of the PARTIAL_SLICE report.
    if slice_statuses:
        overall_slice_status = SliceStatus.aggregate(slice_statuses)
        if not overall_slice_status.completed_all:
            return _partial_slice_report("c4-holdout", overall_slice_status)

    # round 16 finding #2 ordering ruling: see `_run_c3a`'s identical
    # comment — recheck the compute cap immediately before
    # `holdout_executed_valid` is appended below.
    if parent_cpu_checkpoint is not None and cap_counters is not None and cost_caps is not None:
        breach = _checkpoint_parent_cpu_before_transition(
            campaign, cap_counters, cost_caps, parent_cpu_checkpoint
        )
        if breach is not None:
            return {"result": "COST_CAP_EXCEEDED", "detail": breach.detail}

    entry = holdout_stage.run_holdout_stage(campaign, results)
    return {"result": "OK", "holdout_executed_valid_entry_sha": entry.entry_sha}


def _run_close(
    campaign: FrozenCampaign,
    *,
    reveal: bool,
    cap_counters: CapCounters | None = None,
    cost_caps: CostCaps | None = None,
    parent_cpu_checkpoint: list[float] | None = None,
) -> dict[str, Any]:
    holdout_payload = None
    for entry in campaign.ledger.entries:
        payload = entry.payload
        if isinstance(payload, Mapping) and payload.get("kind") == "holdout_executed_valid":
            holdout_payload = payload
    if holdout_payload is None:
        return {"result": "ERROR", "detail": "no holdout_executed_valid event found"}

    # round 16 finding #2 ordering ruling: see `_run_c3a`'s identical
    # comment — recheck the compute cap immediately before
    # `campaign_closed` is appended below, so a breach here blocks the
    # close transition itself (distinct from the *post*-close residual
    # breach the `finally` block in `main()` can still separately detect —
    # see that block's `post_close_breach` handling).
    if parent_cpu_checkpoint is not None and cap_counters is not None and cost_caps is not None:
        breach = _checkpoint_parent_cpu_before_transition(
            campaign, cap_counters, cost_caps, parent_cpu_checkpoint
        )
        if breach is not None:
            return {"result": "COST_CAP_EXCEEDED", "detail": breach.detail}

    try:
        result = close_stage.close_campaign(campaign, holdout_payload)
    except close_stage.CampaignNotClosableError as exc:
        return {"result": "NOT_CLOSABLE", "detail": str(exc)}
    out: dict[str, Any] = {
        "result": "OK",
        "campaign_closed_entry_sha": result.campaign_closed_entry_sha,
        "debt_discharged": result.debt_discharged,
    }
    if reveal:
        reveal_entry = close_stage.reveal_split_secret(campaign)
        out["split_secret_revealed_entry_sha"] = reveal_entry.entry_sha
    return out


_STAGE_DISPATCH_NEEDS_MATRIX = {"c1-fixtures", "c2-baseline", "c3a-f0-selection", "c3b-selection", "c4-holdout"}


# ---------------------------------------------------------------------------
# Gate 1 承認の凍結 manifest への束縛（finding #5, 第 8 巡採用）
# ---------------------------------------------------------------------------


def _gate1_frozen_binding_violation(arming: ArmingDecision, campaign: FrozenCampaign) -> str | None:
    """`check_armed(GATE1)` が `armed=True` を返した後にさらに要求する束縛:
    現在ロードした Gate 1 承認ファイルの content sha256 / `authorization_nonce`
    が、このキャンペーンを凍結した時点で manifest に刻まれた
    `approvals.gate1_sha256`/`authorization_nonce`（`c0_freeze.armed_freeze()`
    が freeze 時点の承認ファイルから埋め込んだもの）と一致することを要求する。
    凍結後に承認ファイルが差し替えられていれば（同じ 3 要素武装が揃っていても）
    拒否する。一致すれば `None`、不一致なら `missing_factors` へ載せる 1 行を
    返す。"""
    approvals_section = campaign.manifest.get("approvals")
    frozen_gate1_sha = (
        approvals_section.get("gate1_sha256") if isinstance(approvals_section, Mapping) else None
    )
    if arming.approval_content_sha256 != frozen_gate1_sha:
        return "gate1_not_frozen_approval"
    frozen_nonce = campaign.manifest.get("authorization_nonce")
    approval_nonce = arming.approval.authorization_nonce if arming.approval is not None else None
    if approval_nonce != frozen_nonce:
        return "gate1_not_frozen_approval"
    return None


# ---------------------------------------------------------------------------
# 手続 phase 順序の強制（finding #6, 第 8 巡採用）
# ---------------------------------------------------------------------------

#: subcommand -> 実行前に到達済みであることを要求する直前提条件 phase。
_SUBCOMMAND_PREREQUISITE_PHASE: Mapping[str, CampaignPhase] = {
    "c1-fixtures": CampaignPhase.PREPARATION_VALID,
    "c2-baseline": CampaignPhase.FIXTURE_VALID,
    "c3a-f0-selection": CampaignPhase.BASELINE_AUDITED,
    "c3b-selection": CampaignPhase.F0_SELECTION_FROZEN,
    "unseal": CampaignPhase.SELECTION_FROZEN,
    "c4-holdout": CampaignPhase.UNSEALED,
    "close": CampaignPhase.HOLDOUT_EXECUTED_VALID,
}

#: subcommand -> 成功時に新規到達する phase。
_SUBCOMMAND_PRODUCES_PHASE: Mapping[str, CampaignPhase] = {
    "c1-fixtures": CampaignPhase.FIXTURE_VALID,
    "c2-baseline": CampaignPhase.BASELINE_AUDITED,
    "c3a-f0-selection": CampaignPhase.F0_SELECTION_FROZEN,
    "c3b-selection": CampaignPhase.SELECTION_FROZEN,
    "unseal": CampaignPhase.UNSEALED,
    "c4-holdout": CampaignPhase.HOLDOUT_EXECUTED_VALID,
    "close": CampaignPhase.CAMPAIGN_CLOSED,
}

#: これらの subcommand は render_stage の resume（sha 一致する既存 instance を
#: skip し pcm/ledger を re-append しない — `render_stage.render_instance` の
#: resume 判定）により、自身の `_SUBCOMMAND_PRODUCES_PHASE` 到達後の再実行が
#: 安全（`tests/test_campaign_render.py::test_c1_render_determinism_and_resume`
#: が module 単位で実証済み）。それ以外の subcommand は produces phase 到達後の
#: 再実行を一律拒否する。round 19 finding #3（採用, `[UNDERSPEC-CAL-D45]`）:
#: これら resumable subcommand が produces phase に既到達の状態で再実行された
#: 場合、`_phase_order_violation` は違反としない（従来どおり）が、`main()` は
#: `_stage_already_complete()` で検知して **dispatch そのものを呼ばない真の
#: no-op**（render/measure なし・phase 遷移 event なし・`stage_summary` なし）
#: として扱う——旧実装は render/measure を再度呼び（resume 機構により実質
#: no-op だが）、`fixture_valid`/`holdout_executed_valid` の phase 遷移 event と
#: `stage_summary` event を無条件に re-append していた。
_RESUMABLE_SUBCOMMANDS: frozenset[str] = frozenset({"c1-fixtures", "c4-holdout"})


def _phase_order_violation(subcommand: str, campaign: FrozenCampaign) -> str | None:
    """`subcommand` を今このキャンペーンに対して実行してよいかを、
    `state.CampaignPhase` の到達済み集合だけから判定する（finding #6）。
    違反理由（`missing_factors` へ載せる 1 行）を返す。問題なければ `None`。

    round 19 finding #3（採用, `[UNDERSPEC-CAL-D45]`）: `CampaignPhase.
    CAMPAIGN_CLOSED` に到達済みなら、`subcommand` が `_RESUMABLE_SUBCOMMANDS`
    に属するかに関わらず（`close` 自身を含む）常に violation とする——
    「CAMPAIGN_CLOSED 後は plan（unarmed）を除く全 stage 呼び出しを拒否する」
    ルーリングを、resumable/non-resumable の場合分けより先に評価する。
    それ以外は従来どおり: 非 resumable subcommand は produces phase 到達後の
    再実行を一律拒否する（`_RESUMABLE_SUBCOMMANDS` は対象外——ただし
    CAMPAIGN_CLOSED に達していない produces-phase-only の再実行は、ここでは
    許可しつつ `main()` 側の `_stage_already_complete()` が真の no-op として
    処理する。`[UNDERSPEC-CAL-D22]` の「完了済み段の再実行は pin 済み結果と
    同一なら no-op」という厳密な byte-identical 判定は依然として本 fix の
    範囲外——本実装は phase 到達済みという粗い basis のみで no-op 判定する）。"""
    prerequisite = _SUBCOMMAND_PREREQUISITE_PHASE.get(subcommand)
    produces = _SUBCOMMAND_PRODUCES_PHASE.get(subcommand)
    if prerequisite is None or produces is None:
        return None
    passed = campaign.phases_passed()
    if CampaignPhase.CAMPAIGN_CLOSED in passed:
        return f"phase_order:{subcommand}_after_campaign_closed"
    if prerequisite not in passed:
        return f"phase_order:{subcommand}_requires_{prerequisite.value}"
    if subcommand not in _RESUMABLE_SUBCOMMANDS and produces in passed:
        return f"phase_order:{subcommand}_already_{produces.value}"
    return None


def _stage_already_complete(subcommand: str, campaign: FrozenCampaign) -> bool:
    """round 19 finding #3（採用, `[UNDERSPEC-CAL-D45]`）: `subcommand` が
    `_RESUMABLE_SUBCOMMANDS`（`c1-fixtures`/`c4-holdout`）に属し、かつその
    `_SUBCOMMAND_PRODUCES_PHASE` に既に到達済みなら真の no-op として扱う。
    CAMPAIGN_CLOSED 到達後の呼び出しは `_phase_order_violation` が先に
    PHASE_ORDER_VIOLATION として拒否するため、ここへは到達しない（CLOSED が
    到達済みなら PRODUCES も必ず到達済みだが、その場合は常に violation が
    先に返る——`main()` は phase_violation チェックの後にこの関数を呼ぶ）。"""
    if subcommand not in _RESUMABLE_SUBCOMMANDS:
        return False
    produces = _SUBCOMMAND_PRODUCES_PHASE.get(subcommand)
    if produces is None:
        return False
    return produces in campaign.phases_passed()


def _refuse_if_caps_already_breached(
    campaign: FrozenCampaign,
    cost_caps: CostCaps | None,
    cap_counters: CapCounters,
    *,
    extra_payload: Mapping[str, object] | None = None,
) -> StopDecision | None:
    """round 13 finding #2: `counters.json` is reloaded on every subcommand
    invocation, but the frozen-cap check only ran *inside* the previous
    stage's per-unit loop (`render_stage`/`measure_stage`). A retry after a
    breach reloaded already-over-limit counters and let dispatch proceed
    anyway, charging one more work unit per retry. Run the same
    `cost_caps.check()` immediately after loading counters, before any stage
    dispatch — if already breached, refuse to dispatch (`[UNDERSPEC-CAL-D26]`).

    Idempotent: `stop_event` recording is append-only and this guard can run
    on every invocation while the campaign sits in a breached state, so it
    must not append a duplicate `stop_event` when the *most recent*
    `stop_event` in the ledger already records this exact breach (same
    reason/counters/caps) — it still refuses dispatch either way. Looks at
    the most recent `stop_event` specifically rather than only the literal
    last entry: round 15 finding #5 (`[UNDERSPEC-CAL-D31]`) appends a
    trailing `stage_summary` event after every dispatch (including a
    mid-stage breach exit), which would otherwise sit *after* the
    breach's own `stop_event` and defeat this dedup on the very next
    invocation.

    round 16 finding #2 ordering ruling (`[UNDERSPEC-CAL-D34]`):
    `extra_payload`, when given, is merged into the appended `stop_event`
    payload (but not into the dedup comparison above, which stays scoped to
    reason/counters/caps — a duplicate breach carries the same extra
    marker anyway). `main()`'s `finally`-block residual recheck uses this
    to mark a breach detected *after* `close` has already appended
    `campaign_closed` as `{"post_close_breach": True}`, distinguishing it
    from a breach this same guard would otherwise record identically at
    other call sites (the pre-dispatch guard, and each stage's own
    pre-transition checkpoint via `_checkpoint_parent_cpu_before_transition`).
    """
    if cost_caps is None:
        return None
    decision = cost_caps_check(cap_counters, cost_caps)
    if decision is None:
        return None
    last_payload = None
    for entry in reversed(campaign.ledger.entries):
        if isinstance(entry.payload, Mapping) and entry.payload.get("kind") == "stop_event":
            last_payload = entry.payload
            break
    already_recorded = (
        isinstance(last_payload, Mapping)
        and last_payload.get("reason") == decision.event_payload.get("reason")
        and last_payload.get("counters") == decision.event_payload.get("counters")
        and last_payload.get("caps") == decision.event_payload.get("caps")
    )
    if not already_recorded:
        payload_to_append = dict(decision.event_payload)
        if extra_payload:
            payload_to_append.update(extra_payload)
        campaign.ledger.append(payload_to_append)
    return decision


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    secret_dir = args.secret_dir or default_secret_dir()
    approval_dir = args.approval_dir or default_approval_dir()

    # R2 (design memo `design_runner_robustness.md`, `[UNDERSPEC-CAL-D79]`):
    # `--time-budget-seconds` must be a positive float — reject before any
    # side effect (mirrors `TimeBudget.start_now()`'s own guard, checked
    # here too so a bad value is reported without touching the campaign at
    # all rather than surfacing mid-dispatch).
    if args.time_budget_seconds is not None and not (args.time_budget_seconds > 0.0):
        _print(
            {
                "result": "ERROR",
                "detail": f"--time-budget-seconds must be > 0, got {args.time_budget_seconds!r}",
            }
        )
        return 1

    if args.subcommand == "plan":
        _print(build_plan_report(args.campaign_dir, secret_dir))
        return 0

    if not args.armed:
        _print(
            {
                "result": "PLAN_ONLY",
                "note": "pass --armed to execute; showing plan for this stage only",
                **build_plan_report(args.campaign_dir, secret_dir, stage=args.subcommand),
            }
        )
        return 0

    arming = check_armed(
        Gate.GATE1_CAMPAIGN_EXECUTION, args.armed, os.environ, approval_dir
    )
    if not arming.armed:
        _print(
            {
                "result": "AUTHORIZATION_REQUIRED",
                "missing_factors": list(arming.missing_factors),
            }
        )
        return 1

    try:
        campaign = load_frozen_campaign(args.campaign_dir, secret_dir)
    except CampaignStateError as exc:
        _print({"result": "CAMPAIGN_STATE_ERROR", "detail": str(exc)})
        return 1

    # finding #7: verify the pinned meter/generator/schema/test source bytes
    # match the frozen manifest *before* touching anything that would use
    # them (build_matrix()/stage dispatch below).
    canonical_violations = _canonical_path_violations(campaign, _REPO_ROOT)
    if canonical_violations:
        campaign.ledger.append(
            {
                "kind": "stop_event",
                "reason": BlockedCode.BLOCKED_CANONICAL_MUTATION_REQUIRED.value,
                "paths": list(canonical_violations),
            }
        )
        _print(
            {
                "result": "BLOCKED_CANONICAL_MUTATION_REQUIRED",
                "paths": list(canonical_violations),
            }
        )
        return 1

    # round 17 finding #2 (採用): the frozen Python/dependency versions
    # recorded at freeze time (`manifest["dependencies"]`) must still match
    # the runtime environment before any stage dispatch — a silent version
    # drift (e.g. a different librosa/numpy build) could change meter output
    # without leaving any other trace. Placed right after the canonical path
    # check: both are pre-dispatch integrity checks over `campaign.manifest`.
    environment_violations = _environment_drift_violations(campaign)
    if environment_violations:
        campaign.ledger.append(
            {
                "kind": "stop_event",
                "reason": ENVIRONMENT_DRIFT_CODE,
                "differences": list(environment_violations),
            }
        )
        _print(
            {
                "result": ENVIRONMENT_DRIFT_CODE,
                "differences": list(environment_violations),
            }
        )
        return 1

    # finding #5: the 3-factor arming above proves *a* valid, currently-armed
    # Gate 1 approval exists — it does not prove it is the *same* approval
    # this campaign was frozen against. Bind it explicitly.
    binding_violation = _gate1_frozen_binding_violation(arming, campaign)
    if binding_violation is not None:
        _print({"result": "AUTHORIZATION_REQUIRED", "missing_factors": [binding_violation]})
        return 1

    # finding #6: refuse subcommands out of procedural order (skip-ahead, or
    # re-running a non-resumable stage that already pinned its result).
    phase_violation = _phase_order_violation(args.subcommand, campaign)
    if phase_violation is not None:
        _print({"result": "PHASE_ORDER_VIOLATION", "detail": phase_violation})
        return 1

    # finding #1: frozen cost caps, loaded from the manifest Gate 1 embedded
    # at freeze time, and cumulative counters persisted across subcommands.
    # round 13 finding #3: a *declared* cost_caps section with a missing/
    # unknown budget_accounting_mode fails closed with a distinct code
    # rather than silently falling back to "no caps" (which would let the
    # dead `budget` dimension stay dead).
    #
    # round 20 採用 (3) (`[UNDERSPEC-CAL-D48]`): this loading + reconciliation
    # now runs *before* the round 19 finding #3 no-op early-return below,
    # not only on the normal dispatch path — a campaign already sitting in a
    # persisted cap breach must still refuse a `c1-fixtures`/`c4-holdout`
    # retry once its produces-phase is already recorded, rather than
    # returning a plain `NOOP_ALREADY_COMPLETE` that silently re-legitimizes
    # the breached state on every retry.
    try:
        cost_caps_obj = cost_caps_from_manifest(campaign.manifest)
    except BudgetAccountingUndeclaredError as exc:
        campaign.ledger.append(
            {
                "kind": "stop_event",
                "reason": BudgetAccountingUndeclaredError.CODE,
                "detail": str(exc),
            }
        )
        _print({"result": BudgetAccountingUndeclaredError.CODE, "detail": str(exc)})
        return 1
    # round 15 finding #3 (`[UNDERSPEC-CAL-D31]`): `counters.json` is a
    # derived cache — the ledger is authoritative. Reconcile the persisted
    # cache against ledger-derived totals (per-dimension max; a structurally
    # corrupt persisted cache fails closed with a distinct code, round 15
    # finding #1) *before* the round 13 finding #2 pre-dispatch breach
    # check, so that check sees the reconciled (never-undercounted) values.
    try:
        cap_counters, reconstructed = reconcile_cap_counters(
            campaign.campaign_dir, campaign.ledger.entries, cost_caps_obj
        )
    except CountersCorruptError as exc:
        campaign.ledger.append(
            {
                "kind": "stop_event",
                "reason": CountersCorruptError.CODE,
                "detail": str(exc),
            }
        )
        _print({"result": CountersCorruptError.CODE, "detail": str(exc)})
        return 1

    # round 19 finding #3 (採用, `[UNDERSPEC-CAL-D45]`) + round 20 採用 (3)
    # (`[UNDERSPEC-CAL-D48]`): a resumable subcommand (`c1-fixtures`/
    # `c4-holdout`) whose produces-phase is already recorded is a true
    # no-op *only if the frozen caps are not already breached* — check
    # that first (`_refuse_if_caps_already_breached` is idempotent: a
    # breach already recorded by an earlier invocation's `stop_event` is
    # not appended twice, but dispatch is refused either way). Neither
    # branch here appends `counters_reconstructed` or persists
    # `counters.json` (unlike the normal dispatch path below) — a true
    # no-op leaves no other trace: no renders/measurements, no transition
    # event, no stage_summary, and (round 20) no counters cache write.
    if _stage_already_complete(args.subcommand, campaign):
        breach = _refuse_if_caps_already_breached(campaign, cost_caps_obj, cap_counters)
        if breach is not None:
            _print({"result": "COST_CAP_EXCEEDED", "detail": breach.detail})
            return 1
        _print({"result": "NOOP_ALREADY_COMPLETE", "stage": args.subcommand})
        return 0

    if reconstructed:
        campaign.ledger.append(
            {"kind": "counters_reconstructed", "counters": cap_counters.as_dict()}
        )
    # Persist the reconciled counters before dispatch (finding #3).
    save_cap_counters(campaign.campaign_dir, cap_counters)

    # round 13 finding #2: refuse dispatch immediately if the reconciled
    # counters already breach the frozen caps — do not let a retry silently
    # proceed and charge one more work unit.
    breach = _refuse_if_caps_already_breached(campaign, cost_caps_obj, cap_counters)
    if breach is not None:
        _print({"result": "COST_CAP_EXCEEDED", "detail": breach.detail})
        return 1

    matrix_rows = build_matrix() if args.subcommand in _STAGE_DISPATCH_NEEDS_MATRIX else None

    # round 15 finding #5 (`[UNDERSPEC-CAL-D31]`): charge this CLI process's
    # own parent-side CPU for the whole stage dispatch to the compute cap —
    # on the normal exit path *and* on any stop/breach/exception exit
    # (`finally`), so a mid-stage `CostCapExceededError`/`RenderNondeterministicError`/
    # `StaleMeasurementError` (all currently uncaught here — they propagate
    # out of `main()`) still gets its parent CPU charged and persisted
    # before propagating. Recorded on a dedicated `stage_summary` ledger
    # event (independent of whatever `stop_event`/`render`/`meter_call`
    # events the stage itself appended) so it is always present exactly
    # once per dispatch, regardless of exit path.
    #
    # round 16 finding #2 (`[UNDERSPEC-CAL-D34]`): `parent_cpu_checkpoint`
    # is a mutable single-element box, not a plain float — `_run_c3a`/
    # `_run_c3b`/`_run_c4`/`_run_close` advance it (via
    # `_checkpoint_parent_cpu_before_transition`) when they perform their
    # own pre-transition recheck, so the residual charge below only covers
    # CPU spent *after* that last mid-stage checkpoint (or the whole
    # dispatch, for `c1-fixtures`/`c2-baseline`/`unseal`, which never
    # checkpoint mid-stage — see `_checkpoint_parent_cpu_before_transition`'s
    # docstring for why).
    out: dict[str, Any] | None = None
    parent_cpu_t0 = _process_cpu_seconds()
    parent_cpu_checkpoint = [parent_cpu_t0]
    try:
        if args.subcommand == "c1-fixtures":
            out = _run_c1(
                campaign,
                matrix_rows,
                cap_counters=cap_counters,
                cost_caps=cost_caps_obj,
                time_budget_seconds=args.time_budget_seconds,
            )
        elif args.subcommand == "c2-baseline":
            out = _run_c2(
                campaign,
                matrix_rows,
                args.workers,
                cap_counters=cap_counters,
                cost_caps=cost_caps_obj,
                discard_partial_groups=args.discard_partial_groups,
                time_budget_seconds=args.time_budget_seconds,
            )
        elif args.subcommand == "c3a-f0-selection":
            out = _run_c3a(
                campaign,
                matrix_rows,
                args.workers,
                cap_counters=cap_counters,
                cost_caps=cost_caps_obj,
                parent_cpu_checkpoint=parent_cpu_checkpoint,
                discard_partial_groups=args.discard_partial_groups,
                time_budget_seconds=args.time_budget_seconds,
            )
        elif args.subcommand == "c3b-selection":
            out = _run_c3b(
                campaign,
                matrix_rows,
                args.workers,
                cap_counters=cap_counters,
                cost_caps=cost_caps_obj,
                parent_cpu_checkpoint=parent_cpu_checkpoint,
                discard_partial_groups=args.discard_partial_groups,
                time_budget_seconds=args.time_budget_seconds,
            )
        elif args.subcommand == "unseal":
            out = _run_unseal(campaign, approval_dir)
        elif args.subcommand == "c4-holdout":
            out = _run_c4(
                campaign,
                matrix_rows,
                args.workers,
                cap_counters=cap_counters,
                cost_caps=cost_caps_obj,
                parent_cpu_checkpoint=parent_cpu_checkpoint,
                discard_partial_groups=args.discard_partial_groups,
                time_budget_seconds=args.time_budget_seconds,
            )
        elif args.subcommand == "close":
            out = _run_close(
                campaign,
                reveal=args.reveal_split_secret,
                cap_counters=cap_counters,
                cost_caps=cost_caps_obj,
                parent_cpu_checkpoint=parent_cpu_checkpoint,
            )
        else:  # pragma: no cover - argparse choices already constrains this
            out = {"result": "ERROR", "detail": f"unknown subcommand {args.subcommand!r}"}
    finally:
        # round 17 finding #3 (採用, `[UNDERSPEC-CAL-D39]`): `cap_counters`
        # (persisted `counters.json`) must be charged only the *residual*
        # CPU since the last mid-stage checkpoint — the checkpoint delta(s)
        # were already added to `cap_counters` in-memory by
        # `_checkpoint_parent_cpu_before_transition()` — but the
        # `stage_summary` *ledger* event must record the FULL dispatch
        # parent-CPU delta (pre-transition checkpoint delta(s) + this
        # residual), because `_checkpoint_parent_cpu_before_transition()`
        # itself appends no ledger event of its own for the CPU it charges.
        # Recording only the residual here (the pre-round-17 behaviour) made
        # `cap_counters_from_ledger()` permanently under-count relative to
        # the persisted cache for any stage that checkpoints mid-dispatch
        # (`c3a`/`c3b`/`c4`/`close`) — the checkpoint delta was charged to
        # `counters.json` but never appeared anywhere in the ledger.
        now_cpu = _process_cpu_seconds()
        residual_cpu_seconds = now_cpu - parent_cpu_checkpoint[0]
        if residual_cpu_seconds < 0.0:  # pragma: no cover - defensive only
            residual_cpu_seconds = 0.0
        # R2 (design memo `design_runner_robustness.md`, `[UNDERSPEC-CAL-D79]`):
        # a `PARTIAL_SLICE` exit charges this dispatch's parent CPU to
        # `cap_counters`/`counters.json` exactly like a normal transition
        # (below, unconditionally) — caps stay honest across slices — but
        # does NOT append the `stage_summary` ledger event, matching "NO
        # phase transition, NO stage_summary event" (no phase transition
        # happened, and the *next* invocation's own `stage_summary` will
        # cover the CPU spent finishing the stage).
        out_is_partial_slice = isinstance(out, dict) and out.get("result") == "PARTIAL_SLICE"
        cap_counters.add(compute=residual_cpu_seconds)
        save_cap_counters(campaign.campaign_dir, cap_counters)
        full_dispatch_parent_cpu_seconds = now_cpu - parent_cpu_t0
        if full_dispatch_parent_cpu_seconds < 0.0:  # pragma: no cover - defensive only
            full_dispatch_parent_cpu_seconds = 0.0
        if not out_is_partial_slice:
            campaign.ledger.append(
                {
                    "kind": "stage_summary",
                    "stage": args.subcommand,
                    "parent_cpu_seconds": full_dispatch_parent_cpu_seconds,
                }
            )
        # round 16 finding #2 (`[UNDERSPEC-CAL-D34]`) — the base fix:
        # recheck the cap with this residual charge folded in, and refuse
        # to report success if it alone breaches. For `close` specifically,
        # a breach detected only here means `campaign_closed` was already
        # appended above (the pre-transition checkpoint in `_run_close`
        # passed) — the close stays recorded (append-only ledger; nothing
        # here retracts it), but this dispatch is still reported as a
        # failure, and the stop event carries `post_close_breach: True` so
        # the ledger itself records that distinction (memo requirement).
        out_is_ok = isinstance(out, dict) and out.get("result") == "OK"
        extra_payload = (
            {"post_close_breach": True} if (args.subcommand == "close" and out_is_ok) else None
        )
        post_breach = _refuse_if_caps_already_breached(
            campaign, cost_caps_obj, cap_counters, extra_payload=extra_payload
        )
        if post_breach is not None and isinstance(out, dict):
            out = {"result": "COST_CAP_EXCEEDED", "detail": post_breach.detail}

    _print(out)
    # R2: `PARTIAL_SLICE` is a clean, expected stop (not a failure) — the
    # design memo requires the stage to "exit 0" so a slice loop (re-running
    # the same command until the stage completes) can rely on exit code
    # alone.
    ok_results = ("OK", "PARTIAL_SLICE")
    return 0 if isinstance(out, dict) and out.get("result") in ok_results else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
