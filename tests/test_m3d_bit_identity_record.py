"""tests/test_m3d_bit_identity_record.py — `docs/measurements/m3d_2026-08/
run_bit_identity.json` の恒久検証（N2・Codex レビュー #255 第 2 巡）。

committed 3 ファイル（`run1.json` / `run2.json` / `run_bit_identity.json`）だけを
読む fixture テスト（実 crepe/tensorflow・音声抽出は一切走らせない・高速）。

検証する契約:
1. `run_bit_identity.json["input_sha256"]` が `run1.json`/`run2.json` の現物
   バイトの sha256 と一致する（記録が committed 現物に束縛されていること）。
2. `compared_files` が committed 相対パスを指し、実在すること。
3. 記録済みの per-pair 等値性（`per_pair`）と `verdict.all_identical` を、
   `run1.json`/`run2.json` の `pairs` から独立に再計算し、記録の主張と一致する
   こと（比較ロジック: pair 単位 dict 等値・intersection of pair id keys、
   top-level run 識別子は pair dict に含まれないため除外操作は不要 — 記録済み
   `excluded_fields_note` の主張どおり）。
4. tuning（実行された比較チェーン: crepe 抽出→表現→整列→軸類似）が 66 件、
   holdout（`holdout_locked_until_frozen` ロックマーカー、比較未実施）が 32 件
   という会計（`docs/m3d_calibration_record.md` / README.md の是正後の記述と
   同じ数）を、`run1.json`/`run2.json` の生データから直接検証する——「98/98」
   ではなく「tuning 66/66 + holdout ロックマーカー 32/32」が正しい内訳である
   ことの回帰ガード。

比較結果 `all_identical` の値自体（= True）はこのテストでは不変更・不変前提
とする——変わった場合はテストが失敗し、実測記録の改変が検出される。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
MEAS_DIR = ROOT / "docs" / "measurements" / "m3d_2026-08"
RUN1_PATH = MEAS_DIR / "run1.json"
RUN2_PATH = MEAS_DIR / "run2.json"
BIT_IDENTITY_PATH = MEAS_DIR / "run_bit_identity.json"

_LOCK_STATUS = "holdout_locked_until_frozen"
_EXPECTED_TUNING_EXECUTED_COUNT = 66
_EXPECTED_HOLDOUT_LOCK_MARKER_COUNT = 32


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_json_bytes(path: Path) -> tuple[Dict[str, Any], bytes]:
    data = path.read_bytes()
    return json.loads(data), data


def test_run1_run2_files_exist_and_are_committed() -> None:
    assert RUN1_PATH.exists(), f"missing committed run1.json: {RUN1_PATH}"
    assert RUN2_PATH.exists(), f"missing committed run2.json: {RUN2_PATH}"
    assert BIT_IDENTITY_PATH.exists(), f"missing committed run_bit_identity.json: {BIT_IDENTITY_PATH}"


def test_compared_files_point_to_committed_relative_paths() -> None:
    record = json.loads(BIT_IDENTITY_PATH.read_bytes())
    compared_files = record["compared_files"]
    assert compared_files["run1"] == "docs/measurements/m3d_2026-08/run1.json"
    assert compared_files["run2"] == "docs/measurements/m3d_2026-08/run2.json"
    # committed パスとして実在すること（build/ 配下の非コミット生成物ではない）。
    assert (ROOT / compared_files["run1"]).resolve() == RUN1_PATH.resolve()
    assert (ROOT / compared_files["run2"]).resolve() == RUN2_PATH.resolve()


def test_input_sha256_binds_record_to_committed_report_bytes() -> None:
    record = json.loads(BIT_IDENTITY_PATH.read_bytes())
    run1_bytes = RUN1_PATH.read_bytes()
    run2_bytes = RUN2_PATH.read_bytes()

    assert record["input_sha256"]["run1"] == _sha256_bytes(run1_bytes)
    assert record["input_sha256"]["run2"] == _sha256_bytes(run2_bytes)


def test_per_pair_identity_and_verdict_recompute_to_the_same_claim() -> None:
    """`run1.json`/`run2.json` の `pairs` から独立に per-pair dict 等値性を
    再計算し、`run_bit_identity.json` の記録済み `per_pair`/`verdict` と一致
    することを確認する（record の主張を鵜呑みにしない）。"""
    record = json.loads(BIT_IDENTITY_PATH.read_bytes())
    run1 = json.loads(RUN1_PATH.read_bytes())
    run2 = json.loads(RUN2_PATH.read_bytes())

    pairs1: Dict[str, Any] = run1["pairs"]
    pairs2: Dict[str, Any] = run2["pairs"]

    keys1 = set(pairs1)
    keys2 = set(pairs2)
    only_in_run1 = sorted(keys1 - keys2)
    only_in_run2 = sorted(keys2 - keys1)

    recomputed_per_pair: Dict[str, Dict[str, bool]] = {}
    mismatched: list = []
    for key in sorted(keys1 & keys2):
        identical = pairs1[key] == pairs2[key]
        recomputed_per_pair[key] = {"identical": identical}
        if not identical:
            mismatched.append(key)

    recomputed_all_identical = (
        len(recomputed_per_pair) > 0
        and not mismatched
        and not only_in_run1
        and not only_in_run2
    )

    assert record["pair_key_set_mismatch"]["only_in_run1"] == only_in_run1
    assert record["pair_key_set_mismatch"]["only_in_run2"] == only_in_run2
    assert record["per_pair"] == recomputed_per_pair
    assert record["verdict"]["compared_pairs"] == len(recomputed_per_pair)
    assert record["verdict"]["mismatched_pairs"] == mismatched
    # all_identical の値自体は commit 済み実測（不変更）—— 変わったらここで
    # 検出されテストが失敗する（is 比較で偽装的な truthy 値も弾く）。
    assert record["verdict"]["all_identical"] is True
    assert recomputed_all_identical is True
    assert record["verdict"]["all_identical"] == recomputed_all_identical


def test_tuning_executed_vs_holdout_lock_marker_accounting_is_66_and_32() -> None:
    """N3 是正後の会計そのものの回帰ガード: 「98/98」ではなく「実行された比較
    チェーン(crepe 抽出→表現→整列→軸類似)は tuning 66/66、holdout 32 行は
    ロックマーカー(`holdout_locked_until_frozen`)の同一性であり抽出は実行され
    ていない」という主張を、run1.json の生データから直接検証する。"""
    run1 = json.loads(RUN1_PATH.read_bytes())
    pairs: Dict[str, Any] = run1["pairs"]

    lock_marker_pairs = {
        pair_id: entry
        for pair_id, entry in pairs.items()
        if isinstance(entry, dict) and entry.get("status") == _LOCK_STATUS
    }
    executed_pairs = {
        pair_id: entry for pair_id, entry in pairs.items() if pair_id not in lock_marker_pairs
    }

    assert len(pairs) == _EXPECTED_TUNING_EXECUTED_COUNT + _EXPECTED_HOLDOUT_LOCK_MARKER_COUNT
    assert len(executed_pairs) == _EXPECTED_TUNING_EXECUTED_COUNT
    assert len(lock_marker_pairs) == _EXPECTED_HOLDOUT_LOCK_MARKER_COUNT

    # ロックマーカーは {split, status} の 2 キーのみ — 抽出・比較チェーンの
    # フィールド（comparison/audio_sha256_*/route_provenance_* 等）を一切
    # 持たないことを確認する（=「抽出は実行されていない」の直接証拠）。
    for pair_id, entry in lock_marker_pairs.items():
        assert set(entry.keys()) == {"split", "status"}, (pair_id, entry)
        assert entry["split"] == "holdout"

    # 実行された比較チェーンの pair は "comparison" フィールド（crepe 抽出→
    # 表現→整列→軸類似の出力）を持つ — 抽出が実際に走ったことの直接証拠。
    for pair_id, entry in executed_pairs.items():
        assert "comparison" in entry, (pair_id, entry)
        assert entry["split"] == "tuning"
