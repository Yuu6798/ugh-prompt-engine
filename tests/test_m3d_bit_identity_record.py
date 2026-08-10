"""tests/test_m3d_bit_identity_record.py — `docs/measurements/m3d_2026-08/
run_bit_identity.json` の恒久検証（N2・Codex レビュー #255 第 2 巡）。

committed 4 ファイル（`run1.json` / `run2.json` / `run_bit_identity.json` /
`screening_v2.json`）だけを読む fixture テスト（実 crepe/tensorflow・音声抽出は
一切走らせない・高速）。

検証する契約:
1. `run_bit_identity.json["input_sha256"]` が `run1.json`/`run2.json` の現物
   バイトの sha256 と一致する（記録が committed 現物に束縛されていること）。
2. `compared_files` が committed 相対パスを指し、実在すること。
3. 記録済みの per-pair 等値性（`per_pair`）と `verdict.all_identical` を、
   `run1.json`/`run2.json` の `pairs` から独立に再計算し、記録の主張と一致する
   こと（比較ロジック: pair 単位 dict 等値・intersection of pair id keys、
   top-level run 識別子は pair dict に含まれないため除外操作は不要 — 記録済み
   `excluded_fields_note` の主張どおり）。
4. tuning（`comparison` フィールドを持つ行）が 66 件、holdout
   （`holdout_locked_until_frozen` ロックマーカー、比較未実施）が 32 件という
   会計（`docs/m3d_calibration_record.md` / README.md の是正後の記述と同じ数）
   を、`run1.json`/`run2.json` の生データから直接検証する——「98/98」ではなく
   「tuning 66/66 + holdout ロックマーカー 32/32」が正しい内訳であることの
   回帰ガード。
4b.（第 11 巡 BB1・正直会計の精密化）tuning 66 行を「M3 比較器チェーン
   （crepe 抽出→表現→NW 整列→軸類似）のどこまで到達したか」で 3 分類し、
   その実数を `run1.json` の `comparison.reasons`/`comparison.axes` から
   独立に再計算する——観測ゲート `observation_gate_insufficient_*` 止まり
   （axes 全 null）60 件・被覆/整列ゲート `insufficient_overlap` 止まり
   （axes 全 null）2 件・全チェーン到達（axes に数値がある）4 件
   （60+2+4=66）。tuning 66/66 という bit 一致の会計そのものは正しいが、
   「66 行すべてが全チェーンを走った」という読みは誤りであることの回帰
   ガード（`docs/m3d_calibration_record.md` §3 是正と同じ内訳）。
5.（第 6 巡 W2 対応・部分採用 (a)）committed `screening_v2.json` の単一
   extractor provenance（`extractor_code_sha256`/`extractor_weights_sha256`）
   が、committed `run1.json`/`run2.json` の全 measured 行の
   `route_provenance_a`/`route_provenance_b` の対応フィールドと**完全一致**
   することを検証する——prereg_v2 §2「screening と比較は同一抽出器 pin」の、
   実施済み実験についての恒久機械証明。

比較結果 `all_identical` の値自体（= True）はこのテストでは不変更・不変前提
とする——変わった場合はテストが失敗し、実測記録の改変が検出される。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_m3d_pairs as bm  # noqa: E402

MEAS_DIR = ROOT / "docs" / "measurements" / "m3d_2026-08"
RUN1_PATH = MEAS_DIR / "run1.json"
RUN2_PATH = MEAS_DIR / "run2.json"
BIT_IDENTITY_PATH = MEAS_DIR / "run_bit_identity.json"
SCREENING_V2_PATH = MEAS_DIR / "screening_v2.json"

_LOCK_STATUS = "holdout_locked_until_frozen"
_EXPECTED_TUNING_EXECUTED_COUNT = 66
_EXPECTED_HOLDOUT_LOCK_MARKER_COUNT = 32

# 第 11 巡 BB1: tuning 66 行の「M3 比較器チェーンのどこまで到達したか」内訳
# （観測ゲート止まり / 被覆・整列ゲート止まり / 全チェーン到達）。
_EXPECTED_OBSERVATION_GATE_STOP_COUNT = 60
_EXPECTED_OVERLAP_GATE_STOP_COUNT = 2
_EXPECTED_FULL_CHAIN_REACHED_COUNT = 4


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
    """N3 是正後の会計そのものの回帰ガード: 「98/98」ではなく「tuning 66/66 は
    最低限 crepe 抽出→M1 観測ゲート判定まで実行された(comparison フィールドを
    持つ)、holdout 32 行はロックマーカー(`holdout_locked_until_frozen`)の同一性
    であり抽出は実行されていない」という主張を、run1.json の生データから直接
    検証する。tuning 66 行のうち比較チェーンを最後まで完走した行数の内訳は
    `test_tuning_stage_reached_breakdown_is_60_2_4`(BB1)が別途検証する。"""
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

    # 実行された比較チェーンの pair は "comparison" フィールド（少なくとも
    # crepe 抽出→M1 観測ゲート判定の出力）を持つ — 抽出が実際に走ったことの
    # 直接証拠。ただしこれは「観測ゲートまで実行された」ことの証拠であって
    # 「表現→整列→軸類似まで完走した」ことの証拠ではない（その内訳は次の
    # テストが検証する）。
    for pair_id, entry in executed_pairs.items():
        assert "comparison" in entry, (pair_id, entry)
        assert entry["split"] == "tuning"


def test_tuning_stage_reached_breakdown_is_60_2_4() -> None:
    """第 11 巡 BB1: tuning 66 行を「M3 比較器チェーン(crepe 抽出→表現→NW
    整列→軸類似)のどこまで到達したか」で 3 分類し、その実数を run1.json の
    `comparison.reasons`/`comparison.axes` から独立に再計算する
    ——`comparison` フィールドを持つことだけを根拠に「66 行すべてが全チェーン
    を走った」と主張するのは誤りであることの回帰ガード。

    分類規則（`comparison.axes` の 3 軸すべてが null かどうかで判定——軸類似
    まで到達していれば必ずいずれかの軸に数値が入る）:
      - 観測ゲート止まり: axes 全 null かつ reasons が
        `observation_gate_insufficient_*` のみ
      - 被覆/整列ゲート止まり: axes 全 null かつ reasons に
        `insufficient_overlap(...)` を含む
      - 全チェーン到達: axes のいずれかが non-null
    """
    run1 = json.loads(RUN1_PATH.read_bytes())
    pairs: Dict[str, Any] = run1["pairs"]
    executed_pairs = {
        pid: entry for pid, entry in pairs.items() if entry.get("status") != _LOCK_STATUS
    }
    assert len(executed_pairs) == _EXPECTED_TUNING_EXECUTED_COUNT

    observation_gate_stop: list = []
    overlap_gate_stop: list = []
    full_chain_reached: list = []
    unclassified: list = []

    for pair_id, entry in executed_pairs.items():
        comparison = entry["comparison"]
        reasons = comparison["reasons"]
        axes = comparison["axes"]
        axes_has_value = any(v is not None for v in axes.values())

        if axes_has_value:
            full_chain_reached.append(pair_id)
        elif any(r.startswith("insufficient_overlap(") for r in reasons):
            overlap_gate_stop.append(pair_id)
        elif reasons and all(r.startswith("observation_gate_insufficient_") for r in reasons):
            observation_gate_stop.append(pair_id)
        else:
            unclassified.append((pair_id, reasons, axes))

    assert unclassified == [], unclassified
    assert len(observation_gate_stop) == _EXPECTED_OBSERVATION_GATE_STOP_COUNT
    assert len(overlap_gate_stop) == _EXPECTED_OVERLAP_GATE_STOP_COUNT
    assert len(full_chain_reached) == _EXPECTED_FULL_CHAIN_REACHED_COUNT
    assert (
        len(observation_gate_stop) + len(overlap_gate_stop) + len(full_chain_reached)
        == _EXPECTED_TUNING_EXECUTED_COUNT
    )

    # 観測ゲート/被覆ゲート止まりの行は axes が厳密に全 null（部分実行の
    # 直接証拠）。
    for pair_id in observation_gate_stop + overlap_gate_stop:
        axes = pairs[pair_id]["comparison"]["axes"]
        assert set(axes.values()) == {None}, (pair_id, axes)

    # 全チェーン到達の行は evidence='none' + reasons=['evidence_thresholds_
    # uncalibrated']（観測ゲート・比較器双方が完走し、閾値未校正で verdict
    # 判定のみ保留されている状態）であることを確認する。
    for pair_id in full_chain_reached:
        comparison = pairs[pair_id]["comparison"]
        assert comparison["evidence"] == "none", (pair_id, comparison)
        assert comparison["reasons"] == ["evidence_thresholds_uncalibrated"], (
            pair_id,
            comparison,
        )


# --------------------------------------------------------------------------- #
# 第 6 巡 W2 対応（部分採用 (a)）: committed screening_v2.json の単一
# extractor provenance が committed run1.json/run2.json の全 measured 行の
# route_provenance_a/b と完全一致することの恒久機械証明（prereg_v2 §2）。
# --------------------------------------------------------------------------- #
def test_screening_v2_json_exists_and_is_committed() -> None:
    assert SCREENING_V2_PATH.exists(), f"missing committed screening_v2.json: {SCREENING_V2_PATH}"


def _screening_v2_single_extractor_provenance() -> Dict[str, str]:
    """committed `screening_v2.json` から `bm._verify_screening_route_binding`
    （record 内で全エントリの `route_provenance` が相互に完全一致することを
    独立に再検証した上で、その単一値から抽出器 pin を返す production コード
    そのもの）を通して単一の extractor provenance を取得する。record の
    集計値やコメントを一切信用せず、`clips` の生データから独立に導出させる
    ——`build_m3d_pairs.py` 側の検証ロジックと同じ保証をテスト側でも再利用
    する。"""
    doc, _digest = bm._load_screening_record(SCREENING_V2_PATH)
    return bm._verify_screening_route_binding(doc, source=str(SCREENING_V2_PATH))


def test_screening_v2_json_route_provenance_is_internally_consistent() -> None:
    """committed record 内で `route_provenance` が単一値に相互一致すること
    （`_verify_screening_route_binding` が raise しないこと）自体を明示的に
    確認する回帰ガード（以降のテストが前提とする不変条件）。"""
    provenance = _screening_v2_single_extractor_provenance()
    assert set(provenance) == {"extractor_code_sha256", "extractor_weights_sha256"}
    for key, value in provenance.items():
        assert isinstance(value, str) and len(value) == 64, (key, value)


def test_screening_v2_extractor_provenance_matches_run1_run2_measured_route_provenance() -> None:
    """committed `screening_v2.json` の単一 extractor provenance
    （`extractor_code_sha256`/`extractor_weights_sha256`）が、committed
    `run1.json`/`run2.json` の全 measured 行（`comparison` フィールドを持つ
    tuning 行。holdout ロックマーカー行は `route_provenance_a/b` 自体を
    持たないため対象外）の `route_provenance_a`/`route_provenance_b` の
    対応フィールドと**完全一致**することを機械検証する——prereg_v2 §2
    「screening と比較実行は同一抽出器 pin を使う」という要求の、既に
    実施済みの実験についての恒久証明（コミット後に screening/run のどちらか
    が別環境の抽出器で再生成されても、本テストが不一致で検出する）。

    実測結果（本テスト作成時点。逐語）:
      extractor_code_sha256    = bc9987ac6245b47f67d4ff7fd71be723e790b2feae23f3adb6ada6fa35882fcd
      extractor_weights_sha256 = fb369944d4feb5964cae189dceba1e554d6471f0be712aad61fc087afaef4a55
    """
    expected = _screening_v2_single_extractor_provenance()

    checked = 0
    mismatched: list = []
    for run_path in (RUN1_PATH, RUN2_PATH):
        run_doc = json.loads(run_path.read_bytes())
        pairs: Dict[str, Any] = run_doc["pairs"]
        for pair_id, entry in pairs.items():
            if not isinstance(entry, dict):
                continue
            for side in ("a", "b"):
                provenance = entry.get(f"route_provenance_{side}")
                if not isinstance(provenance, dict):
                    # holdout ロックマーカー行（{split, status} のみ）は
                    # route_provenance を持たない——対象外（会計は N3 是正
                    # 済みの `test_tuning_executed_vs_holdout_lock_marker_
                    # accounting_is_66_and_32` が別途検証済み）。
                    continue
                checked += 1
                actual_code = provenance.get("extractor_code_sha256")
                actual_weights = provenance.get("extractor_weights_sha256")
                if (
                    actual_code != expected["extractor_code_sha256"]
                    or actual_weights != expected["extractor_weights_sha256"]
                ):
                    mismatched.append(
                        (run_path.name, pair_id, side, actual_code, actual_weights)
                    )

    # 66 measured tuning pairs × 2 runs × 2 sides(a/b) = 264。measured 行が
    # 0 件のまま「何も比較していないのに green」になる偽陰性を防ぐ下限確認。
    assert checked == 264, checked
    assert mismatched == []
