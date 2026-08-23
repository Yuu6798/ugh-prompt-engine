"""test_committed_artifacts_immutable.py — 確定 artifact の機械保護（PR #306
レビュー第2巡 P2）。

`voice_genesis/foundry/debt/d4/d4_results_2026-08-22.json`（VG-DEBT-004）、
`voice_genesis/foundry/results_s3/run4_finite_report_2026-08-22.json`
（VG-DEBT-007）、`voice_genesis/foundry/results_s3/run4_provenance_closure_2026-08-23.json`
（VG-DEBT-008。PR #307 セルフレビュー指摘 2 で追加）は
「実測を生んだ版」の**バイト同一の凍結物**であり、以後の
コミットで（lint 由来の自動整形も含めて）1 バイトも変更してはならない
（`voice_genesis/evolution/probes/snapshots/README.md` と同じ規律）。

本テストは 2 種類の不変条件を機械強制する:

1. sha256 が **2 つの独立した基準の両方**と一致する（PR #307 Codex 第 2 巡
   P2 で穴が指摘されたため二重化した）:
   (a) `debt_ledger.yaml` の対応 debt の `evidence_delivered` の記帳値
       （台帳から動的に読む — 台帳が改訂されずファイルだけ変わる事故と、
       ファイルは無事で台帳だけ書き換わる事故を検出する）
   (b) 本モジュール内の `FROZEN_SHA256` 定数（台帳の**外**に固定した独立
       アンカー — artifact と台帳を同一コミットで協調して書き換える経路を
       検出する。(a) だけでは期待値が artifact と一緒に動くため素通りする）
   3 箇所（artifact / 台帳 / 本定数）の同時改変を要求することで、凍結物の
   事後変更が機械的に検出されないまま通ることを防ぐ。
2. 各ファイル固有の構造不変条件（VG-DEBT-004: 10 群 360 セル・全セル
   wav_sha256 + 3 軸 + 有限値・`d4_remeasure_spec_sha256` が実行当時の
   v0.1 spec sha のまま。VG-DEBT-007: `all_ok: true` など）。

依存は PyYAML + 標準ライブラリのみ（本体の必須依存）。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

_FOUNDRY = Path(__file__).resolve().parent.parent
LEDGER_PATH = _FOUNDRY / "debt" / "debt_ledger.yaml"
D4_RESULTS_PATH = _FOUNDRY / "debt" / "d4" / "d4_results_2026-08-22.json"
RUN4_FINITE_REPORT_PATH = _FOUNDRY / "results_s3" / "run4_finite_report_2026-08-22.json"
RUN4_PROVENANCE_CLOSURE_PATH = (
    _FOUNDRY / "results_s3" / "run4_provenance_closure_2026-08-23.json"
)

#: 2026-08-22 の D4 実測は v0.1 spec（本 sha256）に束縛して実行された。
#: spec 自体は PR #306 のレビュー対応で v0.2 → v0.3 へ改訂され続けているが、
#: **確定済みの結果ファイルはその時点で束縛した spec sha を変えない**
#: （事後に測定結果を新しい spec の主張へ合わせて書き換えない = order_discipline）。
D4_EXECUTION_SPEC_SHA256_V0_1 = "702f1a2231a1b53e71afe7ab6332d42a637546802d960bdd569f4d200de2eeca"

D4_AXES = {"excess_tail_voiced_ms", "release_after_score_boundary_ms", "tail_f0_persistence"}

#: 台帳の**外**に置いた独立アンカー（PR #307 Codex 第 2 巡 P2）。
#: 凍結物を事後に変更するには artifact・`debt_ledger.yaml`・本定数の
#: 3 箇所を同時に書き換える必要があり、協調書き換えがレビューで可視化される。
#: 凍結が発効するのは **artifact を導入した PR がマージされた時点**であり、
#: その PR 内でのレビュー対応（記録の誤りの訂正など）による更新はここに含めない
#: （本定数は「マージ済みの確定記録の事後改変」を検出するためのもの）。
#: マージ後に値を更新してよいのは「その artifact を生んだ実測をやり直した」ときだけで、
#: 表現の微修正や整形のために書き換えてはならない。
FROZEN_SHA256 = {
    "d4_results_2026-08-22.json": (
        "6b820a2a27744b9ed4f6e873231aa103b57dd622f993982a112063e5b4bacfa7"
    ),
    "run4_finite_report_2026-08-22.json": (
        "d0c1748a6e99903e0776740b726f27a75f6468763f2323ec6a7e0d0748c30876"
    ),
    "run4_provenance_closure_2026-08-23.json": (
        "40d32d33e4f81b99452cf183a831836285079dc784cf63c0681f44b97f4b0308"
    ),
}


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def ledger() -> Dict[str, Any]:
    with LEDGER_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _evidence_sha256(ledger: Dict[str, Any], debt_id: str, rel_path_startswith: str) -> str:
    """`ledger` の `debt_id` に記帳された `repayment.evidence_delivered` から、
    `rel_path_startswith` で始まるエントリの `sha256 <hex>` を抽出する
    （ハードコードせず台帳を正本として動的に読む）。`evidence_delivered` は
    `repayment` 配下に置くのが台帳の実際の記法（VG-DEBT-002/003/005/007 等）
    なのでそこを見る。"""
    debts = {d["debt_id"]: d for d in ledger["debts"]}
    assert debt_id in debts, f"{debt_id} が debt_ledger.yaml に無い"
    entries = (debts[debt_id].get("repayment") or {}).get("evidence_delivered") or []
    for entry in entries:
        if isinstance(entry, str) and entry.startswith(rel_path_startswith):
            m = re.search(r"sha256\s+([0-9a-f]{64})", entry)
            assert m, f"{debt_id}: {rel_path_startswith} の evidence_delivered に sha256 が無い記法: {entry[:120]!r}"
            return m.group(1)
    raise AssertionError(
        f"{debt_id}: {rel_path_startswith} で始まる evidence_delivered エントリが見つからない"
    )


# --- VG-DEBT-004: d4_results_2026-08-22.json ------------------------------


def test_d4_results_sha256_matches_ledger_evidence(ledger: Dict[str, Any]) -> None:
    assert D4_RESULTS_PATH.is_file(), f"{D4_RESULTS_PATH} が無い（確定 artifact が消えている）"
    expected = _evidence_sha256(
        ledger, "VG-DEBT-004", "voice_genesis/foundry/debt/d4/d4_results_2026-08-22.json"
    )
    got = _sha256_file(D4_RESULTS_PATH)
    assert got == expected, (
        f"d4_results_2026-08-22.json の実 sha256 {got} が debt_ledger.yaml "
        f"VG-DEBT-004 の記帳 {expected} と違う（確定 artifact が変更されたか、"
        "台帳の記帳が古い）"
    )


def test_d4_results_structural_invariants() -> None:
    """10 群 360 セル・全セル wav pin + 3 軸有限値・実行時 spec sha が
    v0.1（702f1a22…）のまま、を機械強制する。"""
    doc = json.loads(D4_RESULTS_PATH.read_text(encoding="utf-8"))

    assert doc["schema"] == "vg-d4-remeasure-results/0.1"
    assert doc["debt_ref"] == "VG-DEBT-004"
    assert doc["d4_remeasure_spec_sha256"] == D4_EXECUTION_SPEC_SHA256_V0_1, (
        "確定済み結果ファイルの spec sha256 束縛が変わっている（事後に測定結果を"
        "新しい spec の主張へ合わせて書き換えることは禁止 = order_discipline）"
    )
    assert doc["n_groups"] == 10
    assert doc["n_total_cells"] == 360
    assert doc["n_total_measured"] == 360
    assert doc["n_total_error"] == 0
    groups = doc["groups"]
    assert len(groups) == 10

    for group_id, group in groups.items():
        assert group["n_cells"] == 36, group_id
        assert group["n_measured"] == 36, group_id
        assert group["n_missing"] == 0, group_id
        assert group["n_error"] == 0, group_id
        cells = group["cells"]
        assert len(cells) == 36, group_id
        for cell_id, cell in cells.items():
            assert cell["outcome"] == "measured", f"{group_id}/{cell_id}"
            assert cell.get("wav_sha256"), f"{group_id}/{cell_id}: wav_sha256 が空"
            assert cell.get("samples_sha256"), f"{group_id}/{cell_id}: samples_sha256 が空"
            axes = cell.get("axes", {})
            assert set(axes) == D4_AXES, f"{group_id}/{cell_id}: axes keys {set(axes)}"
            for axis_name, value in axes.items():
                assert isinstance(value, (int, float)), f"{group_id}/{cell_id}/{axis_name}"
                assert math.isfinite(value), f"{group_id}/{cell_id}/{axis_name}: {value} が有限でない"


# --- VG-DEBT-007: run4_finite_report_2026-08-22.json -----------------------


def test_run4_finite_report_sha256_matches_ledger_evidence(ledger: Dict[str, Any]) -> None:
    assert RUN4_FINITE_REPORT_PATH.is_file(), f"{RUN4_FINITE_REPORT_PATH} が無い"
    expected = _evidence_sha256(
        ledger, "VG-DEBT-007",
        "voice_genesis/foundry/results_s3/run4_finite_report_2026-08-22.json",
    )
    got = _sha256_file(RUN4_FINITE_REPORT_PATH)
    assert got == expected, (
        f"run4_finite_report_2026-08-22.json の実 sha256 {got} が debt_ledger.yaml "
        f"VG-DEBT-007 の記帳 {expected} と違う"
    )


def test_run4_finite_report_structural_invariants() -> None:
    """4 checkpoint すべて all_finite・pin 一致・報告全体が all_ok、を機械強制する
    （軽い保護 — スキーマ全体の再検証は scripts/check_checkpoint_finite.py 側の
    責務であり、ここでは『この確定ファイルが主張どおりの中身を保っているか』
    だけを見る）。"""
    doc = json.loads(RUN4_FINITE_REPORT_PATH.read_text(encoding="utf-8"))

    assert doc["schema"] == "voicegenesis-checkpoint-finite-report/0.1"
    assert doc["all_ok"] is True
    assert doc["missing"] == []
    assert doc["extra"] == []
    assert doc["duplicates"] == []
    checkpoints = doc["checkpoints"]
    assert len(checkpoints) == 4
    for ck in checkpoints:
        assert ck["all_finite"] is True, ck.get("path")
        assert ck["status"] == "ok", ck.get("path")
        assert ck["pin_sha256_match"] is True, ck.get("path")
        assert ck["total_non_finite"] == 0, ck.get("path")


# --- VG-DEBT-008: run4_provenance_closure_2026-08-23.json -----------------


def test_run4_provenance_closure_sha256_matches_ledger_evidence(
    ledger: Dict[str, Any],
) -> None:
    """VG-DEBT-008 の証拠ファイルも台帳記帳 sha256 と一致させる
    （PR #307 セルフレビュー指摘 2: 台帳が pin を持つのに機械強制が無く、
    実際に本 PR 内で pin 後にファイルが変わった事故が起きていた）。"""
    assert RUN4_PROVENANCE_CLOSURE_PATH.is_file(), (
        f"{RUN4_PROVENANCE_CLOSURE_PATH} が無い（確定 artifact が消えている）"
    )
    expected = _evidence_sha256(
        ledger,
        "VG-DEBT-008",
        "voice_genesis/foundry/results_s3/run4_provenance_closure_2026-08-23.json",
    )
    got = _sha256_file(RUN4_PROVENANCE_CLOSURE_PATH)
    assert got == expected, (
        f"run4_provenance_closure_2026-08-23.json の実 sha256 {got} が "
        f"debt_ledger.yaml VG-DEBT-008 の記帳 {expected} と違う"
        "（証拠ファイルが変更されたか、台帳の記帳が古い）"
    )


def test_run4_provenance_closure_structural_invariants() -> None:
    """10 件・closure 語彙・acceptance/phase4 節の存在を機械強制する
    （closure の内訳自体は test_run4_provenance_closure.py が担当）。"""
    doc = json.loads(RUN4_PROVENANCE_CLOSURE_PATH.read_text(encoding="utf-8"))

    assert doc["schema"] == "voicegenesis-run4-provenance-closure/0.1"
    assert isinstance(doc.get("acceptance"), str) and doc["acceptance"].strip()
    items = doc["items"]
    assert len(items) == 10
    assert {i["closure"] for i in items} <= {
        "reproduced",
        "measured_only",
        "not_closable",
    }
    assert isinstance(doc.get("phase4_env_contract_retest"), dict)


# --- 台帳の外の独立アンカー（PR #307 Codex 第 2 巡 P2） -------------------


@pytest.mark.parametrize(
    "path",
    [D4_RESULTS_PATH, RUN4_FINITE_REPORT_PATH, RUN4_PROVENANCE_CLOSURE_PATH],
    ids=lambda p: p.name,
)
def test_frozen_artifact_matches_independent_anchor(path: Path) -> None:
    """artifact と台帳を同一コミットで協調して書き換えても、台帳の外に固定した
    本定数と食い違えば落ちる。台帳照合（`*_matches_ledger_evidence`）だけでは
    期待値が artifact と一緒に動くため、この経路が素通りしていた。"""
    assert path.is_file(), f"{path} が無い（凍結 artifact が消えている）"
    expected = FROZEN_SHA256[path.name]
    got = _sha256_file(path)
    assert got == expected, (
        f"{path.name} の実 sha256 {got} が独立アンカー FROZEN_SHA256 の "
        f"{expected} と違う。凍結物は実測をやり直したときにのみ更新してよく、"
        "表現の微修正や整形で書き換えてはならない"
    )


def test_independent_anchor_agrees_with_ledger(ledger: Dict[str, Any]) -> None:
    """2 基準が同じ値を指していること自体も検査する（片方だけ更新して
    もう片方を放置した中途半端な改訂を検出する）。"""
    pairs = [
        ("VG-DEBT-004", "voice_genesis/foundry/debt/d4/d4_results_2026-08-22.json"),
        (
            "VG-DEBT-007",
            "voice_genesis/foundry/results_s3/run4_finite_report_2026-08-22.json",
        ),
        (
            "VG-DEBT-008",
            "voice_genesis/foundry/results_s3/run4_provenance_closure_2026-08-23.json",
        ),
    ]
    for debt_id, rel_path in pairs:
        name = rel_path.rsplit("/", 1)[-1]
        assert _evidence_sha256(ledger, debt_id, rel_path) == FROZEN_SHA256[name], (
            f"{debt_id}: 台帳の記帳と FROZEN_SHA256 が食い違う（{name}）"
        )
