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
from pathlib import Path

from voice_genesis.calibration.canonical import canonical_json
from voice_genesis.calibration.canonical import manifest_sha as _manifest_sha
from voice_genesis.calibration.fixtures.matrix import MatrixRow, build_matrix
from voice_genesis.calibration.provenance import Ledger
from voice_genesis.calibration.splitter import RowInput, realize_split

STRATUM_FACTOR_NAMES: tuple[str, ...] = ("truth_level", "boundary_class")

SPLIT_SECRET = b"S" * 32
RENDER_ROOT_SECRET = b"R" * 32
CAMPAIGN_ID = "RUN10-CAL-TESTFIXTURE"

_REPO_ROOT = Path(__file__).resolve().parents[3]
DESIGN_DOC_PATH = _REPO_ROOT / "voice_genesis/calibration/DESIGN_VG_METER_CAL_DEBT_v1.0.md"
MEMO_DOC_PATH = _REPO_ROOT / "voice_genesis/calibration/IMPLEMENTATION_MAP_v1.md"


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
) -> tuple[Path, Path]:
    """`(campaign_dir, secret_dir_root)` を組み立てて返す。"""
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
        "commitments": {
            "split_secret_sha256": hashlib.sha256(split_secret).hexdigest(),
            "render_root_secret_sha256": hashlib.sha256(render_root_secret).hexdigest(),
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
        "frozen_design": {"fixture_spec": {}},
    }

    campaigns_dir = tmp_path / "campaigns"
    secret_root = tmp_path / "secrets"
    campaign_dir = campaigns_dir / campaign_id
    campaign_dir.mkdir(parents=True)
    (campaign_dir / "c0_manifest.json").write_text(canonical_json(manifest), encoding="utf-8")
    Ledger(campaign_dir / "ledger.jsonl").append(
        {
            "kind": "c0_freeze",
            "campaign_id": campaign_id,
            "manifest_sha": _manifest_sha(manifest),
        }
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

DEFAULT_NONCE = "test-nonce-campaign-000000"


def write_gate1_approval(approval_dir: Path, *, nonce: str = DEFAULT_NONCE) -> None:
    payload = {
        "gate": "GATE1_CAMPAIGN_EXECUTION",
        "approver": "tester",
        "approved_at_utc": "2026-09-02T00:00:00Z",
        "design_doc_sha256": design_doc_sha256(),
        "memo_sha256": memo_sha256(),
        "authorization_nonce": nonce,
        "cost_caps": {"compute": 36000.0, "storage": 1_000_000_000, "budget": 1.0},
        "e_use_bound_accepted": True,
        "max_claim_scope": ["formant_frequency"],
    }
    approval_dir.mkdir(parents=True, exist_ok=True)
    (approval_dir / "gate1_campaign_execution.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def write_gate3_approval(approval_dir: Path, *, accepted: bool = True) -> None:
    payload = {
        "gate": "GATE3_SEAL_ACCEPTANCE",
        "approver": "tester",
        "approved_at_utc": "2026-09-02T00:00:00Z",
        "design_doc_sha256": design_doc_sha256(),
        "memo_sha256": memo_sha256(),
        "seal_protection_level_accepted": accepted,
    }
    approval_dir.mkdir(parents=True, exist_ok=True)
    (approval_dir / "gate3_seal_acceptance.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
