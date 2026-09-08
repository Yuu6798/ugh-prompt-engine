"""v1.2 WP2 §D — rehearsal 経路の E2E（C0 freeze -> close を実 CLI プロセスで完走）。

本番 456 セルの campaign は 14 時間規模で、C0->close の全経路（C4 実 gate を
含む）を実際に通せる機会が事実上無かった。本テストは縮小行列
（`fixtures.matrix.build_rehearsal_matrix()`）と `--rehearsal` フラグで同じ
経路を丸ごと通し、疎通の破断を早期に捕まえる。

**授権境界**（IMPLEMENTATION_MAP §0）: すべての書込は `tmp_path` 配下の
campaigns/secrets/approvals に限る。リポジトリの
`voice_genesis/calibration/campaigns/` と `~/.vg_cal/` には一切触れない
——`--rehearsal` 自身がそれらの配下を指す path を `BLOCKED_REHEARSAL_PATH`
で拒否する（`c0_freeze.rehearsal_path_violations()`）。

**既定で skip する理由（実測、2026-09-06）**: 第 1 回の実測は縮小行列
（456 -> 58 行）だけで **約 3 時間**だった。律速は行数ではなく
「候補数 (99) x instance x fresh-process 起動」の積である。Fable 判定
（v1.2 WP2b）: rehearsal は claim を生まないので候補プールの縮小も許容する
——`candidates.registry.rehearsal_candidate_pool()`（family ごとに B0 +
`claim_ceiling != NONE` の先頭 1 件 = 99 -> 12 候補）。

再実測（同 checkout・同一機、2026-09-06。stage ごとに実 CLI を駆動）:

| 段 | 実測秒 |
|---|---|
| c0 dry-run / armed freeze | 0.4 / 0.6 |
| c1-fixtures | 466.7 |
| c2-baseline | 398.5 |
| c3a-f0-selection | 125.8 |
| c3b-selection | 957.1（`--time-budget-seconds 450` の 3 slice 合計） |
| unseal | 2.4 |
| c4-holdout | 447.1 |
| close | 2.0 |
| **合計** | **2400.6 秒 = 40.0 分**（3 時間 -> 1/4.5。meter_call 6540） |

目標の 30 分には届かない（残る律速は c1 の render 290 と c2/c4 の instance 数で、
どちらも候補プールとは独立）。それでも既定の `pytest` / CI（`slow` を skip
しない規約）を 40 分拘束するのは実害なので、環境変数
`VG_CAL_REHEARSAL_E2E=1` を明示したときだけ実行する（手動 gate）。

1 回の呼び出しに 10 分の上限があるハーネスからは、本テストの代わりに
`campaign` CLI を段ごとに直接駆動できる: 長い段は
`--time-budget-seconds <n>` を付けると `PARTIAL_SLICE` を返して途中終了し、
同じコマンドの再実行で続きから進む（上表の c3b はこの方法で計測した）。

**subprocess で回す理由**: 本 WP が配線したのは CLI の引数解析から
`set_rehearsal_mode()`・manifest への `rehearsal` 記録・path ガード・
stage dispatch までの一続きであり、in-process 呼び出しではその入口
（`main()` の argv 解析と大域フラグ設定）を跨げない。代償として checkout の
dirty 判定（`c0_validate._inspect_checkout_identity()`）を monkeypatch で
迂回できないため、作業ツリーが dirty なときは skip する（CI の clean checkout
では常に実行される）。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from voice_genesis.calibration import approvals, c0_freeze, c0_validate, vocab

_REPO_ROOT = c0_freeze._REPO_ROOT

_DESIGN_SHA = hashlib.sha256(
    (_REPO_ROOT / approvals.DESIGN_DOC_RELATIVE_PATH).read_bytes()
).hexdigest()
_MEMO_SHA = hashlib.sha256((_REPO_ROOT / approvals.MEMO_RELATIVE_PATH).read_bytes()).hexdigest()

_NONCE = "rehearsal-e2e-nonce-000000"

#: rehearsal の 1 stage あたりの上限（秒）。無限待ちを避けるためだけの安全弁で、
#: 性能主張ではない（所要時間そのものは報告文に実測値として残す）。
_STAGE_TIMEOUT_SECONDS = 3600

#: 明示 opt-in の環境変数（モジュール docstring「既定で skip する理由」参照）。
REHEARSAL_E2E_ENV_VAR = "VG_CAL_REHEARSAL_E2E"


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_gate1(approval_dir: Path, *, approved_at_utc: str) -> None:
    payload = {
        "gate": "GATE1_CAMPAIGN_EXECUTION",
        "approver": "rehearsal-e2e",
        "approved_at_utc": approved_at_utc,
        "design_doc_sha256": _DESIGN_SHA,
        "memo_sha256": _MEMO_SHA,
        "authorization_nonce": _NONCE,
        "cost_caps": {
            "compute": 360000.0,
            "storage": 10_000_000_000,
            "budget": 1.0,
            "budget_accounting_mode": "local_zero_cost",
        },
        "e_use_bound_accepted": True,
        # §B(vii): rehearsal は claim を生まないので construct-id ではない
        # sentinel を使う（本番 freeze はこの承認では武装できない）。
        "max_claim_scope": [approvals.REHEARSAL_CLAIM_SCOPE_SENTINEL],
    }
    (approval_dir / approvals.APPROVAL_FILENAMES[approvals.Gate.GATE1_CAMPAIGN_EXECUTION]).write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _write_gate2(approval_dir: Path, manifest_core_sha: str, *, approved_at_utc: str) -> None:
    payload = {
        "gate": "GATE2_C0_FREEZE",
        "approver": "rehearsal-e2e",
        "approved_at_utc": approved_at_utc,
        "design_doc_sha256": _DESIGN_SHA,
        "memo_sha256": _MEMO_SHA,
        "authorization_nonce": _NONCE,
        "manifest_core_sha": manifest_core_sha,
    }
    (approval_dir / approvals.APPROVAL_FILENAMES[approvals.Gate.GATE2_C0_FREEZE]).write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _write_gate3(approval_dir: Path, *, approved_at_utc: str) -> None:
    payload = {
        "gate": "GATE3_SEAL_ACCEPTANCE",
        "approver": "rehearsal-e2e",
        "approved_at_utc": approved_at_utc,
        "design_doc_sha256": _DESIGN_SHA,
        "memo_sha256": _MEMO_SHA,
        "seal_protection_level_accepted": True,
    }
    (approval_dir / approvals.APPROVAL_FILENAMES[approvals.Gate.GATE3_SEAL_ACCEPTANCE]).write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _run(argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", *argv],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=_STAGE_TIMEOUT_SECONDS,
    )


def _ledger_payloads(campaign_dir: Path) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for line in (campaign_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        entry = json.loads(line)
        payload = entry.get("payload")
        if isinstance(payload, dict):
            out.append(payload)
    return out


def _kind_counts(payloads: list[dict[str, object]]) -> dict[str, int]:
    """ledger の `kind` 列とその件数（報告へ転記するための観測値）。"""
    counts: dict[str, int] = {}
    for payload in payloads:
        kind = payload.get("kind")
        if isinstance(kind, str):
            counts[kind] = counts.get(kind, 0) + 1
    return dict(sorted(counts.items()))


@pytest.mark.slow
def test_rehearsal_campaign_runs_c0_through_close(tmp_path: Path) -> None:
    if os.environ.get(REHEARSAL_E2E_ENV_VAR) != "1":
        pytest.skip(
            f"set {REHEARSAL_E2E_ENV_VAR}=1 to run the rehearsal E2E "
            "(measured multi-hour runtime; see this module's docstring)"
        )
    head_sha, dirty, error = c0_validate._inspect_checkout_identity()
    if error is not None or dirty is None:
        pytest.skip(f"cannot inspect checkout identity: {error}")
    if dirty:
        pytest.skip(
            "checkout is dirty: this E2E drives real CLI subprocesses, so "
            "`repo.dirty_tree=false` cannot be monkeypatched clean (commit first)"
        )
    assert head_sha is not None

    approval_dir = tmp_path / "approvals"
    secret_dir = tmp_path / "secrets"
    campaigns_dir = tmp_path / "campaigns"
    approval_dir.mkdir()

    now = datetime.now(timezone.utc)
    _write_gate1(approval_dir, approved_at_utc=_utc(now - timedelta(minutes=5)))

    base_env = dict(os.environ)
    base_env["VG_CAL_APPROVAL_DIR"] = str(approval_dir)
    base_env["VG_CAL_SECRET_DIR"] = str(secret_dir)

    freeze_argv = [
        "voice_genesis.calibration.c0_freeze",
        "--rehearsal",
        "--campaigns-dir",
        str(campaigns_dir),
        "--secret-dir",
        str(secret_dir),
        "--approval-dir",
        str(approval_dir),
    ]

    started = time.monotonic()
    #: 段ごとの実測所要（秒）。所要時間そのものは主張ではなく観測値なので、
    #: 段が終わるたびに flush して出力する——途中で中断されても、どこまでで
    #: 何秒かかったかが必ず記録に残る（v1.2 WP2b の再実測要件）。
    stage_seconds: dict[str, float] = {}

    def _timed(label: str, fn):
        mark = time.monotonic()
        try:
            return fn()
        finally:
            stage_seconds[label] = time.monotonic() - mark
            print(
                f"rehearsal E2E stage={label} seconds={stage_seconds[label]:.1f}",
                flush=True,
            )

    dry = _timed("c0-dry-run", lambda: _run(freeze_argv, base_env))
    core_sha = next(
        line.split(": ", 1)[1].strip()
        for line in dry.stdout.splitlines()
        if line.startswith("manifest_core_sha: ")
    )
    assert len(core_sha) == 64, dry.stdout

    _write_gate2(approval_dir, core_sha, approved_at_utc=_utc(datetime.now(timezone.utc)))

    freeze_env = dict(base_env)
    freeze_env["VG_CAL_C0_FREEZE_AUTHORIZED"] = "1"
    armed = _timed("c0-armed-freeze", lambda: _run([*freeze_argv, "--armed"], freeze_env))
    assert armed.returncode == 0, armed.stdout + armed.stderr
    assert "outcome: PUBLISHED" in armed.stdout, armed.stdout
    campaign_id = next(
        line.split(": ", 1)[1].strip()
        for line in armed.stdout.splitlines()
        if line.startswith("campaign_id: ")
    )
    assert campaign_id.startswith(c0_freeze.REHEARSAL_CAMPAIGN_ID_PREFIX + "RUN10-CAL-")
    campaign_dir = campaigns_dir / campaign_id
    assert campaign_dir.is_dir()

    manifest = json.loads((campaign_dir / "c0_manifest.json").read_text(encoding="utf-8"))
    assert manifest["frozen_design"]["rehearsal"] is True

    campaign_env = dict(base_env)
    campaign_env["VG_CAL_CAMPAIGN_AUTHORIZED"] = "1"

    def run_stage(subcommand: str) -> dict[str, object]:
        proc = _timed(
            subcommand,
            lambda: _run(
                [
                    "voice_genesis.calibration.campaign",
                    subcommand,
                    "--campaign-dir",
                    str(campaign_dir),
                    "--secret-dir",
                    str(secret_dir),
                    "--approval-dir",
                    str(approval_dir),
                    "--rehearsal",
                    "--armed",
                ],
                campaign_env,
            ),
        )
        assert proc.returncode == 0, f"{subcommand}: {proc.stdout}\n{proc.stderr}"
        out = json.loads(proc.stdout)
        assert out.get("result") == "OK", (subcommand, out)
        return out

    for stage in ("c1-fixtures", "c2-baseline", "c3a-f0-selection", "c3b-selection"):
        run_stage(stage)

    # Gate 3 は freeze **後** に発行される（`campaign.unseal` は
    # `freeze_time < gate3_time <= now + 60s` を要求する）。
    _write_gate3(approval_dir, approved_at_utc=_utc(datetime.now(timezone.utc)))

    run_stage("unseal")
    run_stage("c4-holdout")
    run_stage("close")

    elapsed_seconds = time.monotonic() - started

    payloads = _ledger_payloads(campaign_dir)
    kinds = [p.get("kind") for p in payloads]
    for required in (
        "c0_freeze",
        "split_frozen",
        "fixture_valid",
        "baseline_audited",
        "f0_selection_frozen",
        "selection_frozen",
        "gate3_accepted",
        "holdout_unseal",
        "holdout_executed_valid",
        "campaign_closed",
    ):
        assert kinds.count(required) >= 1, (required, sorted(set(kinds)))

    # BLOCKED_* は 1 件も出ない（stop_event の reason / stage 出力の双方）。
    blocked_codes = {code.value for code in vocab.BlockedCode}
    blocking = [
        p
        for p in payloads
        if isinstance(p.get("reason"), str) and p["reason"] in blocked_codes
    ]
    assert blocking == [], blocking

    holdout = next(p for p in payloads if p.get("kind") == "holdout_executed_valid")
    per_meter = holdout["per_meter"]
    assert isinstance(per_meter, dict)
    assert set(per_meter) == {meter.value for meter in vocab.MeterId}
    for meter_id, entry in per_meter.items():
        status = entry.get("terminal_status")
        assert status in {s.value for s in vocab.TerminalStatus}, (meter_id, status)

    closed = next(p for p in payloads if p.get("kind") == "campaign_closed")
    # §B(v): rehearsal は claim を生まない——`debt_discharged` は常に false。
    assert closed["derived"]["debt_discharged"] is False
    assert closed["derived"]["rehearsal"] is True

    # 所要時間は主張ではなく観測値。失敗時に必ず目に入るよう出力する。
    print(f"rehearsal E2E elapsed_seconds={elapsed_seconds:.1f}")
    print(
        "rehearsal E2E stage_seconds="
        + json.dumps({k: round(v, 1) for k, v in stage_seconds.items()})
    )
    print("rehearsal E2E ledger kinds=" + json.dumps(_kind_counts(payloads)))
    print(
        "rehearsal E2E per_meter terminal_status="
        + json.dumps(
            {
                meter_id: entry.get("terminal_status")
                for meter_id, entry in sorted(per_meter.items())
            }
        )
    )
