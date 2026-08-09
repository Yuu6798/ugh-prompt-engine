"""tests/test_m3d_notcomparable_diagnosis_record.py — `docs/measurements/
m3d_2026-08/notcomparable_diagnosis.json` の恒久検証（Codex レビュー #255
第 9 巡指摘 1 件対応）。

committed ファイルのみを読む fixture テスト（外部 vocadito 素材・実 crepe/
tensorflow 抽出は一切走らせない・高速）。`scripts/diagnose_m3d_notcomparable.py`
が committed 入力から決定論的に導出する記録であることを、以下の 3 点で
機械検証する:

(a) ``diagnosis["inputs"]["run1_sha256"]`` が committed ``run1.json`` の実
    バイトの sha256 と一致する（記録が committed 現物に束縛されていること）。
(b) ``diagnosis["3_annotation_phrase_structure_table"]["rows"]`` の
    gate_crosscheck 行（clip 別の insufficient/measured 判定）を、
    ``run1.json``/``m3d_pairs_manifest.yaml`` の生データから独立に再計算し、
    記録済みの主張と一致することを確認する（`scripts/
    diagnose_m3d_notcomparable.py` の production ロジックをそのまま再利用
    ——record の集計値を鵜呑みにしない）。
(c) ``diagnosis["inputs"]["vocadito_annotation_sha256"]`` の全 clip が
    ``m2c_external_fixtures.yaml`` の ``expected_annotation_sha256`` と
    完全一致する（外部素材 pin の束縛）。

**境界の明記（M2c 流儀・`docs/m2e_provisioning_runbook.md` 参照）**: 本テストは
外部 vocadito F0 CSV 本体を一切読まない・再解析しない。committed
``inputs.vocadito_annotation_sha256`` の値と committed
``m2c_external_fixtures.yaml`` の pin 値という、**2 つの committed ファイル
同士**の一致だけを検証する。「pin された sha256 の実体が本当に正しい F0 CSV
から算出されたものか」の検証は外部素材のプロビジョニング（`--vocadito-dir`
指定・`scripts/diagnose_m3d_notcomparable.py` 実行）を要し、クリーン
チェックアウトの CI では意図的に対象外——`3_annotation_phrase_structure_table`
の ``total_duration_sec``/``voiced_ratio``/``annotation_phrase_count`` 等
（アノテーション本体の中身に依存する集計）の再計算・照合も同じ理由で対象外
とする。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import diagnose_m3d_notcomparable as dm  # noqa: E402

MEAS_DIR = ROOT / "docs" / "measurements" / "m3d_2026-08"
RUN1_PATH = MEAS_DIR / "run1.json"
DIAGNOSIS_PATH = MEAS_DIR / "notcomparable_diagnosis.json"
MANIFEST_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "m3d_pairs_manifest.yaml"
M2C_FIXTURES_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "m2c_external_fixtures.yaml"

_LOCK_STATUS = "holdout_locked_until_frozen"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_diagnosis() -> Dict[str, Any]:
    return json.loads(DIAGNOSIS_PATH.read_bytes())


def test_diagnosis_and_all_committed_inputs_exist() -> None:
    assert DIAGNOSIS_PATH.exists(), f"missing committed diagnosis: {DIAGNOSIS_PATH}"
    assert RUN1_PATH.exists(), f"missing committed run1.json: {RUN1_PATH}"
    assert MANIFEST_PATH.exists(), f"missing committed manifest: {MANIFEST_PATH}"
    assert M2C_FIXTURES_PATH.exists(), f"missing committed fixtures: {M2C_FIXTURES_PATH}"


# --------------------------------------------------------------------------- #
# (a) inputs.run1_sha256 が committed run1.json の実バイトと一致する
# --------------------------------------------------------------------------- #
def test_inputs_run1_sha256_binds_record_to_committed_run1_bytes() -> None:
    diagnosis = _load_diagnosis()
    run1_bytes = RUN1_PATH.read_bytes()
    assert diagnosis["inputs"]["run1_path"] == "docs/measurements/m3d_2026-08/run1.json"
    assert diagnosis["inputs"]["run1_sha256"] == _sha256_bytes(run1_bytes)


def test_inputs_manifest_and_registry_sha256_bind_to_committed_bytes() -> None:
    diagnosis = _load_diagnosis()
    manifest_bytes = MANIFEST_PATH.read_bytes()
    registry_bytes = (
        ROOT / "tests" / "fixtures" / "melody_bench" / "registry.yaml"
    ).read_bytes()
    fixtures_bytes = M2C_FIXTURES_PATH.read_bytes()

    inputs = diagnosis["inputs"]
    assert inputs["manifest_sha256"] == _sha256_bytes(manifest_bytes)
    assert inputs["gate_registry_sha256"] == _sha256_bytes(registry_bytes)
    assert inputs["m2c_external_fixtures_sha256"] == _sha256_bytes(fixtures_bytes)

    # registry.yaml sha256 は run1.json 自身の per-pair provenance
    # (`comparison.provenance.m1_registry_sha256`) とも一致するはず——記録が
    # 実際に run1.json 生成に使われた registry と束縛されていることの二重確認。
    run1 = json.loads(RUN1_PATH.read_bytes())
    tuning_pairs = {
        pid: p for pid, p in run1["pairs"].items() if p.get("status") != _LOCK_STATUS
    }
    assert tuning_pairs, "no executed (non-holdout-lock) pairs found in run1.json"
    for pid, entry in tuning_pairs.items():
        assert (
            entry["comparison"]["provenance"]["m1_registry_sha256"]
            == inputs["gate_registry_sha256"]
        ), pid


# --------------------------------------------------------------------------- #
# (c) inputs.vocadito_annotation_sha256 が m2c_external_fixtures.yaml の
# expected_annotation_sha256 と一致する（外部 CSV 本体は読まない）
# --------------------------------------------------------------------------- #
def test_inputs_annotation_sha256_matches_m2c_external_fixtures_pin() -> None:
    diagnosis = _load_diagnosis()
    fixtures = yaml.safe_load(M2C_FIXTURES_PATH.read_bytes())["fixtures"]

    annotation_sha256 = diagnosis["inputs"]["vocadito_annotation_sha256"]
    assert annotation_sha256, "expected at least one used clip"

    checked = 0
    for clip_id, digest in annotation_sha256.items():
        assert clip_id in fixtures, f"{clip_id} missing from m2c_external_fixtures.yaml"
        assert digest == fixtures[clip_id]["expected_annotation_sha256"], clip_id
        checked += 1
    assert checked == len(annotation_sha256)

    # 記録された clip 数は 3_annotation_phrase_structure_table の
    # clip_count_real_total と一致するはず（同じ 18 clip 母集団）。
    table = diagnosis["3_annotation_phrase_structure_table"]
    assert len(annotation_sha256) == table["clip_count_real_total"]


# --------------------------------------------------------------------------- #
# (b) gate_crosscheck 行が run1.json/manifest から独立に再計算した結果と一致
# --------------------------------------------------------------------------- #
def _recompute_gate_crosscheck_rows() -> Dict[str, Any]:
    """`scripts/diagnose_m3d_notcomparable.py` の production ロジック
    （`_material_of` / `_basename_clip_id` / `_parse_reason` 等）をそのまま
    再利用し、committed `run1.json`/`m3d_pairs_manifest.yaml` から
    clip 別 gate_crosscheck を独立に再計算する。外部 CSV には触れない
    ——ここで得られるのは `gate_crosscheck`/`measured_evidence_pair_present`/
    `split` のみで、アノテーション本体に依存する `voiced_ratio` 等は含まない。
    """
    run1 = json.loads(RUN1_PATH.read_bytes())
    manifest_pairs: List[Dict[str, Any]] = yaml.safe_load(MANIFEST_PATH.read_bytes())["pairs"]
    run1_pairs = run1["pairs"]

    clip_split: Dict[str, str] = {}
    for mp in manifest_pairs:
        if dm._material_of(mp["pair_id"]) != "real":
            continue
        for audio_key in ("audio_a", "audio_b"):
            clip_id = dm._basename_clip_id(mp[audio_key])
            if clip_id is None:
                continue
            existing = clip_split.get(clip_id)
            assert existing is None or existing == mp["split"], clip_id
            clip_split[clip_id] = mp["split"]

    clip_pairs: Dict[str, List[Any]] = {c: [] for c in clip_split}
    for mp in manifest_pairs:
        if dm._material_of(mp["pair_id"]) != "real":
            continue
        for side, audio_key in (("a", "audio_a"), ("b", "audio_b")):
            clip_id = dm._basename_clip_id(mp[audio_key])
            if clip_id in clip_pairs:
                clip_pairs[clip_id].append((mp["pair_id"], side))

    result: Dict[str, Any] = {}
    for clip_id, split in clip_split.items():
        if split == "holdout":
            result[clip_id] = {
                "split": split,
                "gate_crosscheck": "not applicable: holdout comparison is locked ",
            }
            continue
        crosscheck = []
        measured_present = False
        for pair_id, side in clip_pairs[clip_id]:
            entry = run1_pairs[pair_id]
            comparison = entry["comparison"]
            evidence = comparison["evidence"]
            if evidence != "not_comparable":
                measured_present = True
            reasons = comparison["reasons"]
            gate_insufficient_this_side = any(
                r.startswith(f"observation_gate_insufficient_{side}:") for r in reasons
            )
            mp = next(m for m in manifest_pairs if m["pair_id"] == pair_id)
            crosscheck.append(
                {
                    "pair_id": pair_id,
                    "side": side,
                    "kind": mp["kind"],
                    "evidence": evidence,
                    "gate_insufficient_this_side": gate_insufficient_this_side,
                    "reasons": list(reasons),
                }
            )
        result[clip_id] = {
            "split": split,
            "gate_crosscheck": crosscheck,
            "measured_evidence_pair_present": measured_present,
        }
    return result


def test_gate_crosscheck_rows_match_independent_recomputation_from_run1_and_manifest() -> None:
    diagnosis = _load_diagnosis()
    table = diagnosis["3_annotation_phrase_structure_table"]
    recomputed = _recompute_gate_crosscheck_rows()

    rows_by_clip = {row["clip_id"]: row for row in table["rows"]}
    assert set(rows_by_clip) == set(recomputed)
    assert len(rows_by_clip) > 0

    for clip_id, expected in recomputed.items():
        actual = rows_by_clip[clip_id]
        assert actual["split"] == expected["split"], clip_id
        assert actual["gate_crosscheck"] == expected["gate_crosscheck"], clip_id
        if expected["split"] != "holdout":
            assert (
                actual["measured_evidence_pair_present"]
                == expected["measured_evidence_pair_present"]
            ), clip_id
        else:
            assert "measured_evidence_pair_present" not in actual, clip_id


def test_gate_crosscheck_reasons_are_verbatim_subset_of_run1_comparison_reasons() -> None:
    """gate_crosscheck の各行の `reasons` が、対応する run1.json pair の
    `comparison.reasons` と verbatim 一致すること（record 側の改変・要約が
    無いことの直接証拠）。"""
    diagnosis = _load_diagnosis()
    run1 = json.loads(RUN1_PATH.read_bytes())
    run1_pairs = run1["pairs"]

    table = diagnosis["3_annotation_phrase_structure_table"]
    checked = 0
    for row in table["rows"]:
        crosscheck = row["gate_crosscheck"]
        if isinstance(crosscheck, str):
            continue
        for entry in crosscheck:
            pair_id = entry["pair_id"]
            assert entry["reasons"] == run1_pairs[pair_id]["comparison"]["reasons"], pair_id
            assert entry["evidence"] == run1_pairs[pair_id]["comparison"]["evidence"], pair_id
            checked += 1
    assert checked > 0
