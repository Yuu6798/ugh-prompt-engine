"""`campaign/` テスト共通ヘルパー（`test_campaign_*.py` からのみ import
される。それ自体は `test_*.py` 命名ではないため pytest には収集されない）。

Task Brief: 「build a tiny synthetic frozen campaign fixture in tests using
c0_freeze internals is heavy — instead write a test helper that fabricates a
minimal campaign dir」。`c0_freeze.armed_freeze()` を一切呼ばず、
`splitter.realize_split()` のみを使って `c0_manifest.json` /
`realized_split`（インライン）/ `ledger.jsonl`（freeze event）/ secret 2 ファイル
を直接組み立てる。**`tmp_path` 配下のみを書く。`~/.vg_cal` やリポジトリの
`voice_genesis/calibration/campaigns/` には一切触れない。**
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from voice_genesis.calibration import approvals
from voice_genesis.calibration.c0_freeze import split_frozen_event_payload
from voice_genesis.calibration.canonical import canonical_json
from voice_genesis.calibration.canonical import manifest_sha as _manifest_sha
from voice_genesis.calibration.fixtures.matrix import MatrixRow, build_matrix
from voice_genesis.calibration.provenance import Ledger
from voice_genesis.calibration.splitter import RowInput, realize_split

STRATUM_FACTOR_NAMES: tuple[str, ...] = ("truth_level", "boundary_class")

SPLIT_SECRET = b"S" * 32
RENDER_ROOT_SECRET = b"R" * 32
CAMPAIGN_ID = "RUN10-CAL-TESTFIXTURE"

#: Gate 1 承認ファイルの `authorization_nonce` 既定値。`build_tiny_campaign()`
#: と `write_gate1_approval()` の両方の既定値として使う（finding #5: 両者が
#: デフォルトのまま呼ばれれば自動的に束縛が一致するように）。
DEFAULT_NONCE = "test-nonce-campaign-000000"

#: Gate 1 承認ファイルの `cost_caps` 既定値（`write_gate1_approval()` の既定と
#: 同値。`build_tiny_campaign()` は同じ値を `frozen_design.cost_caps`
#: （finding #1）へも埋め込む — 両者は実運用で常に同一値のため）。
#: `budget_accounting_mode`（round 13 finding #3）は本キャンペーンが
#: ローカル計算資源のみで動く前提と揃え `"local_zero_cost"` を既定にする
#: （`GATE1_DECISION_RECORD.md` §5.x のルーリングと同じ根拠）。
DEFAULT_GATE1_COST_CAPS: Mapping[str, object] = {
    "compute": 36000.0,
    "storage": 1_000_000_000,
    "budget": 1.0,
    "budget_accounting_mode": "local_zero_cost",
}


def _all_constructs() -> tuple[str, ...]:
    """全候補の `construct` 全種（`ALL_CANDIDATES` の遅延 import — 循環 import
    回避のため関数内 import）。`build_tiny_campaign()` の
    `max_claim_scope`（finding #11）既定値: 「全 construct が scope 内」
    という最も緩い既定にし、scope 制限そのものを検証したいテストだけが
    明示的に狭い `max_claim_scope` を渡す。"""
    from voice_genesis.calibration.candidates.registry import ALL_CANDIDATES

    return tuple(sorted({c.construct for c in ALL_CANDIDATES}))

#: `build_tiny_campaign()` が manifest `candidates` 節（finding #7:
#: `cli._canonical_path_violations` が照合する 5 カテゴリ。
#: `meter_implementation_paths_sha256` は `[UNDERSPEC-CAL-D49]` で追加した
#: harness meter 実装カテゴリ）へ埋め込む、実在する小さな代表 path 集合
#: （カテゴリごとに 1 件ずつ）。`c0_freeze._path_hash_maps()` の全量走査は
#: 行わず、`cli._canonical_path_violations` が 5 カテゴリすべてを実際に
#: 検査することだけをこの小集合で確認できれば十分なため、実ファイルの
#: sha256 を毎回 `_canonical_candidates_section()` 呼び出し時点で計算する
#: （固定値をハードコードしない — 対象ファイルが後で編集されても自動的に
#: 追随し陳腐化しない）。
_CANONICAL_PATH_SAMPLES: Mapping[str, str] = {
    "meter_paths_sha256": "voice_genesis/calibration/candidates/registry.py",
    "meter_implementation_paths_sha256": "voice_genesis/harness/measure.py",
    "generator_paths_sha256": "voice_genesis/calibration/fixtures/generators/f0_control.py",
    "schema_paths_sha256": "voice_genesis/calibration/vocab.py",
    "test_paths_sha256": "voice_genesis/calibration/tests/_campaign_fixture.py",
}


def _canonical_candidates_section(repo_root: Path | None = None) -> dict[str, dict[str, str]]:
    """`c0_freeze._path_hash_maps()` と同じ 5 キー形状
    (`{category: {rel_path: sha256}}`) を、`_CANONICAL_PATH_SAMPLES` の
    実ファイルから独立に計算して返す（finding #7 のテスト用最小 fixture。
    `c0_freeze.py` には依存しない — 他 agent 並行編集の対象外にする既存方針
    と同じ）。"""
    root = repo_root if repo_root is not None else _REPO_ROOT
    return {
        category: {rel_path: hashlib.sha256((root / rel_path).read_bytes()).hexdigest()}
        for category, rel_path in _CANONICAL_PATH_SAMPLES.items()
    }

_REPO_ROOT = Path(__file__).resolve().parents[3]
#: v1.1 統治文書切替（§V6, 2026-09-04）: pin 対象文書は `approvals` モジュール
#: の定数を経由して解決する（ハードコードすると `DESIGN_DOC_RELATIVE_PATH` の
#: 切替に追随できず、`approvals.load_approval()` の design_doc_sha256 照合が
#: 常に不一致になる）。
DESIGN_DOC_PATH = _REPO_ROOT / approvals.DESIGN_DOC_RELATIVE_PATH
MEMO_DOC_PATH = _REPO_ROOT / approvals.MEMO_RELATIVE_PATH


def design_doc_sha256() -> str:
    return hashlib.sha256(DESIGN_DOC_PATH.read_bytes()).hexdigest()


def memo_sha256() -> str:
    return hashlib.sha256(MEMO_DOC_PATH.read_bytes()).hexdigest()


def small_matrix_subset(n: int = 6, *, family: str | None = None) -> list[MatrixRow]:
    """`build_matrix()` から TRUTH_CORE 行を最大 `n` 件抜き出す。`family`
    未指定なら family をまたいで 1 家系 1 行ずつ（split 挙動の検証には不向き
    — 単一 family の n 行が欲しい場合は `family=` を渡す）。"""
    all_rows = build_matrix()
    subset: list[MatrixRow] = []
    seen: set[str] = set()
    for mr in all_rows:
        if mr.row.block != "TRUTH_CORE":
            continue
        if family is not None:
            if mr.row.family != family:
                continue
        else:
            if mr.row.family in seen:
                continue
            seen.add(mr.row.family)
        subset.append(mr)
        if len(subset) >= n:
            break
    return subset


def build_tiny_campaign(
    tmp_path: Path,
    *,
    subset: list[MatrixRow] | None = None,
    campaign_id: str = CAMPAIGN_ID,
    split_secret: bytes = SPLIT_SECRET,
    render_root_secret: bytes = RENDER_ROOT_SECRET,
    write_secrets: bool = True,
    gate1_nonce: str = DEFAULT_NONCE,
    gate1_cost_caps: Mapping[str, object] | None = None,
    canonical_candidates_section: Mapping[str, Mapping[str, str]] | None = None,
    max_claim_scope: Sequence[str] | None = None,
    dependencies: Mapping[str, str] | None = None,
    frozen_inputs: Mapping[str, object] | None = None,
    freeze_event_time_utc: str | None = "2026-09-02T00:00:00+00:00",
) -> tuple[Path, Path]:
    """`(campaign_dir, secret_dir_root)` を組み立てて返す。

    `freeze_event_time_utc`（#345 指摘②, `UNDERSPEC-CAL-D85`: `campaign.unseal`
    の Gate 3 freeze-後発行検証テスト専用）は合成 `c0_freeze` event の
    `event_time_utc` フィールドへそのまま埋め込む。既定は
    `write_gate3_approval()` の既定 `approved_at_utc`（後述、freeze より後）
    より前の値。`None` を渡すと `event_time_utc` キー自体を省略する
    （unparsable/legacy ledger を模した fixture 用）。

    `gate1_nonce`/`gate1_cost_caps`（既定は `write_gate1_approval()` の既定と
    一致するよう選んである）は manifest の `authorization_nonce`/
    `approvals.gate1_sha256`（finding #5: Gate 1 承認の凍結 manifest への
    束縛）と `frozen_design.cost_caps`（finding #1: frozen cost caps の
    enforcement）へ埋め込まれる。デフォルトのまま呼べば、同じくデフォルトの
    `write_gate1_approval(approval_dir)` が書く承認ファイルと自動的に
    束縛が一致する。`canonical_candidates_section`（既定は
    `_canonical_candidates_section()`）は manifest `candidates` 節
    （finding #7: canonical path 照合）— 改竄検知テストは実ファイルを一切
    変更せず、この引数へ細工した mapping を渡して検証する。`max_claim_scope`
    （既定は `_all_constructs()` — 全 construct が scope 内、finding #11:
    scope 制限そのものを検証したいテストだけ明示的に狭い値を渡す）は
    manifest `frozen_design.max_claim_scope` へ埋め込まれる。`dependencies`
    （round 17 finding #2, `[UNDERSPEC-CAL-D38]`: `cli._environment_drift_violations()`
    のテスト専用。既定 `None` は manifest に `dependencies` キー自体を
    持たせない — 既存の CLI 単体テストは drift 照合の対象外のまま）は
    manifest `dependencies` へそのまま埋め込まれる。`frozen_inputs`
    （round 20 採用 (2), `[UNDERSPEC-CAL-D47]`: `holdout_stage.load_e_use_rows()`
    の sha256 pin 検証のテスト専用。既定 `None` は manifest に
    `frozen_inputs` キー自体を持たせない）は manifest `frozen_inputs` へ
    そのまま埋め込まれる。"""
    subset = subset if subset is not None else small_matrix_subset()
    row_inputs = [
        RowInput(
            row_id=mr.row_id,
            family=mr.row.family,
            stratum={"truth_level": mr.row.block, "boundary_class": mr.domain.value},
            truth_level=mr.row.block,
            generator_impl=mr.row.generator_impl,
            boundary_class=mr.domain.value,
        )
        for mr in subset
    ]
    realized = realize_split(row_inputs, split_secret, STRATUM_FACTOR_NAMES)

    manifest: dict[str, object] = {
        "campaign_meta": {"campaign_date_utc": "2026-09-02"},
        "campaign_id": campaign_id,
        "authorization_nonce": gate1_nonce,
        "commitments": {
            "split_secret_sha256": hashlib.sha256(split_secret).hexdigest(),
            "render_root_secret_sha256": hashlib.sha256(render_root_secret).hexdigest(),
        },
        "approvals": {
            "gate1_sha256": gate1_content_sha256(nonce=gate1_nonce, cost_caps=gate1_cost_caps),
        },
        "realized_split": {
            "stratum_factor_names": list(realized.stratum_factor_names),
            "assignment": {rid: s.value for rid, s in sorted(realized.assignment.items())},
            "swaps": [
                {
                    "row_id": s.row_id,
                    "from_split": s.from_split.value,
                    "to_split": s.to_split.value,
                    "reason": s.reason,
                    "hmac_key": s.hmac_key,
                    "detail": s.detail,
                }
                for s in realized.swaps
            ],
            "realized_sha": realized.realized_sha,
        },
        "realized_split_sha": realized.realized_sha,
        "frozen_design": {
            "fixture_spec": {},
            "cost_caps": dict(gate1_cost_caps)
            if gate1_cost_caps is not None
            else dict(DEFAULT_GATE1_COST_CAPS),
            "max_claim_scope": list(max_claim_scope)
            if max_claim_scope is not None
            else list(_all_constructs()),
        },
        "candidates": {
            category: dict(paths)
            for category, paths in (
                canonical_candidates_section
                if canonical_candidates_section is not None
                else _canonical_candidates_section()
            ).items()
        },
    }
    if dependencies is not None:
        manifest["dependencies"] = dict(dependencies)
    if frozen_inputs is not None:
        manifest["frozen_inputs"] = dict(frozen_inputs)

    campaigns_dir = tmp_path / "campaigns"
    secret_root = tmp_path / "secrets"
    campaign_dir = campaigns_dir / campaign_id
    campaign_dir.mkdir(parents=True)
    (campaign_dir / "c0_manifest.json").write_text(canonical_json(manifest), encoding="utf-8")
    fixture_ledger = Ledger(campaign_dir / "ledger.jsonl")
    freeze_event: dict[str, object] = {
        "kind": "c0_freeze",
        "campaign_id": campaign_id,
        "manifest_sha": _manifest_sha(manifest),
        "realized_split_sha": realized.realized_sha,
    }
    if freeze_event_time_utc is not None:
        freeze_event["event_time_utc"] = freeze_event_time_utc
    fixture_ledger.append(freeze_event)
    # round 14 finding #1: call the production `split_frozen` emitter
    # (`c0_freeze.split_frozen_event_payload`) instead of hand-fabricating an
    # equivalent dict here, so fixture and production payload shape cannot
    # drift apart again.
    fixture_ledger.append(
        split_frozen_event_payload(
            campaign_id=campaign_id,
            realized_split_sha=realized.realized_sha,
            split_secret_sha256=hashlib.sha256(split_secret).hexdigest(),
            event_time_utc="2026-09-02T00:00:00+00:00",
        )
    )
    if write_secrets:
        secret_dir = secret_root / campaign_id
        secret_dir.mkdir(parents=True)
        (secret_dir / "split_secret.bin").write_bytes(split_secret)
        (secret_dir / "render_root_secret.bin").write_bytes(render_root_secret)

    return campaign_dir, secret_root


# ---------------------------------------------------------------------------
# Gate approval file fabrication (tmp_path only)
# ---------------------------------------------------------------------------


def _gate1_payload(
    *, nonce: str = DEFAULT_NONCE, cost_caps: Mapping[str, object] | None = None
) -> dict[str, object]:
    return {
        "gate": "GATE1_CAMPAIGN_EXECUTION",
        "approver": "tester",
        "approved_at_utc": "2026-09-02T00:00:00Z",
        "design_doc_sha256": design_doc_sha256(),
        "memo_sha256": memo_sha256(),
        "authorization_nonce": nonce,
        "cost_caps": dict(cost_caps) if cost_caps is not None else dict(DEFAULT_GATE1_COST_CAPS),
        "e_use_bound_accepted": True,
        "max_claim_scope": ["formant_frequency"],
    }


def gate1_content_sha256(
    *, nonce: str = DEFAULT_NONCE, cost_caps: Mapping[str, object] | None = None
) -> str:
    """`write_gate1_approval()` が実際にディスクへ書き出すのと **同一
    payload・同一 serialization**（`json.dumps(payload)`, kwargs 無し）から
    content sha256 を計算する。`build_tiny_campaign()` が manifest 内
    `approvals.gate1_sha256`（finding #5）を埋め込む際に、実際の承認
    ファイルの bytes と食い違わせないために使う — 呼び出し側 2 つが別々に
    文字列を組み立てて事故ることを防ぐ、単一の正本。"""
    payload = _gate1_payload(nonce=nonce, cost_caps=cost_caps)
    return hashlib.sha256(json.dumps(payload).encode("utf-8")).hexdigest()


def write_gate1_approval(
    approval_dir: Path,
    *,
    nonce: str = DEFAULT_NONCE,
    cost_caps: Mapping[str, object] | None = None,
) -> None:
    payload = _gate1_payload(nonce=nonce, cost_caps=cost_caps)
    approval_dir.mkdir(parents=True, exist_ok=True)
    (approval_dir / "gate1_campaign_execution.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def write_gate3_approval(
    approval_dir: Path, *, accepted: bool = True, approved_at_utc: str = "2026-09-02T01:00:00Z"
) -> None:
    """`approved_at_utc`（#345 指摘②, `UNDERSPEC-CAL-D85`）既定値は
    `build_tiny_campaign()` の既定 `freeze_event_time_utc`
    （`2026-09-02T00:00:00+00:00`）より厳密に後——`campaign.unseal.unseal_campaign()`
    の freeze-後発行検証を素通りするデフォルト束縛を維持する。"""
    payload = {
        "gate": "GATE3_SEAL_ACCEPTANCE",
        "approver": "tester",
        "approved_at_utc": approved_at_utc,
        "design_doc_sha256": design_doc_sha256(),
        "memo_sha256": memo_sha256(),
        "seal_protection_level_accepted": accepted,
    }
    approval_dir.mkdir(parents=True, exist_ok=True)
    (approval_dir / "gate3_seal_acceptance.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
