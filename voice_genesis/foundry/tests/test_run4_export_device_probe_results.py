"""test_run4_export_device_probe_results.py — VG-DEBT-008 (a-2) export-device
probe の実測記録ペア（`run4_export_device_probe_2026-08-23.json` /
`run4_export_device_probe_report_2026-08-23.md`）の形状・整合性テスト。

`test_committed_artifacts_immutable.py` の凍結テスト（sha256 二重アンカー）
とは役割を分ける: 本ファイルのテストは **freeze-bound ではない**——値を
ハードコードせずファイル内容から再導出するので、凍結値がマージ前の
3 者協調更新（artifact・台帳・FROZEN_SHA256 定数。マージ前の記録に限る——
マージ後はバイト無改変で、再分類は裁定記録の参照方式 =
DEBT_ADJUDICATION_v1.1.md §8）で正当に改訂された後も、再導出した
内容が自己整合していれば pass し続ける。

依存は標準ライブラリ（json / hashlib / pathlib / re）+ PyYAML + pytest のみ
（本体の必須依存）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

_FOUNDRY = Path(__file__).resolve().parent.parent
RESULTS_S3 = _FOUNDRY / "results_s3"
LEDGER_PATH = _FOUNDRY / "debt" / "debt_ledger.yaml"

PROBE_JSON_PATH = RESULTS_S3 / "run4_export_device_probe_2026-08-23.json"
PROBE_REPORT_PATH = RESULTS_S3 / "run4_export_device_probe_report_2026-08-23.md"
PROVENANCE_CLOSURE_PATH = RESULTS_S3 / "run4_provenance_closure_2026-08-23.json"
ANCHOR_PROVENANCE_PATH = RESULTS_S3 / "run4_anchor_provenance.json"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

REQUIRED_TOP_LEVEL_KEYS = {
    "probe_script",
    "execution_provenance",
    "environment_run3",
    "expected_values",
    "arms",
    "comparisons",
    "findings_measured",
    "unresolved_enumerated_not_swept",
    "a2_functional_corroboration",
    "non_claims",
}

# PR #307 終端宣言（追補 2）の順位付け語彙禁止。新規 2 ファイルのみ走査する
# （"主因" は他所の履歴的文脈で正当に使われているため対象外 — ここでは
# 走査すらしない）。
FORBIDDEN_RANKING_VOCAB = ["最有力", "第一候補", "可能性が高い"]

# probe json 内の {song}_{speaker} キーと run4_anchor_provenance.json の
# ファイル名の対応（run4_gate_{song}_{speaker}.wav）。
_VOICE_TO_ANCHOR_FILENAME = {
    "ritsu_sakura": "run4_gate_sakura_ritsu.wav",
    "ritsu_umi": "run4_gate_umi_ritsu.wav",
    "pjs_sakura": "run4_gate_sakura_pjs.wav",
    "pjs_umi": "run4_gate_umi_pjs.wav",
    "user_sakura": "run4_gate_sakura_user.wav",
    "user_umi": "run4_gate_umi_user.wav",
}


@pytest.fixture(scope="module")
def probe_doc() -> Dict[str, Any]:
    assert PROBE_JSON_PATH.is_file(), f"{PROBE_JSON_PATH} が無い"
    return json.loads(PROBE_JSON_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ledger() -> Dict[str, Any]:
    with LEDGER_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _walk_strings(obj: Any):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_strings(v)
    elif isinstance(obj, str):
        yield obj


def _collect_sha256_valued_fields(doc: Dict[str, Any]) -> Dict[str, str]:
    """`arms` と `expected_values` を歩き、キー名が `*sha256*` のフィールドを
    dotted-path をキーにして集める。`onnx_sha256` のような直接の文字列値と、
    `wav_sha256` のような「音声名 -> sha256」ネスト dict の両方をカバーする。
    """
    found: Dict[str, str] = {}

    def walk(obj: Any, path: str) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                sub_path = f"{path}.{k}" if path else k
                if "sha256" in k.lower():
                    if isinstance(v, str):
                        found[sub_path] = v
                    elif isinstance(v, dict):
                        for nested_k, nested_v in v.items():
                            if isinstance(nested_v, str):
                                found[f"{sub_path}.{nested_k}"] = nested_v
                else:
                    walk(v, sub_path)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}]")

    walk(doc.get("arms", {}), "arms")
    walk(doc.get("expected_values", {}), "expected_values")
    return found


# --- (1) 形状: JSON parse + 必須トップレベルキー -----------------------


def test_probe_json_parses_and_has_required_top_level_keys(probe_doc: Dict[str, Any]) -> None:
    missing = REQUIRED_TOP_LEVEL_KEYS - set(probe_doc)
    assert not missing, f"必須トップレベルキー欠落: {missing}"


# --- (2) sha256 フィールドの well-formed 性 -----------------------------


def test_all_sha256_valued_fields_are_well_formed_hex64(probe_doc: Dict[str, Any]) -> None:
    fields = _collect_sha256_valued_fields(probe_doc)
    assert len(fields) >= 10, f"sha256 系フィールドが少なすぎる（{len(fields)} 件のみ検出）"
    for path, value in fields.items():
        assert _SHA256_RE.fullmatch(value), f"{path}: 64 桁小文字 16 進でない sha256: {value!r}"


# --- (3) comparisons の boolean を arms/expected_values から再計算 -------


def test_comparisons_recomputed_from_arms_match_stored(probe_doc: Dict[str, Any]) -> None:
    arms = probe_doc["arms"]
    expected = probe_doc["expected_values"]
    comparisons = probe_doc["comparisons"]

    g1_onnx = arms["g1_gpu_export_full"]["onnx_sha256"]
    g2_onnx = arms["g2_gpu_export_determinism"]["onnx_sha256"]
    c1_onnx = arms["c1_cpu_export_same_pod"]["onnx_sha256"]
    c1b_onnx = arms["c1b_cpu_export_alt_path_root"]["onnx_sha256"]
    session_cpu_onnx = expected["session_cpu_onnx_sha256"]

    recomputed = {
        "g1_onnx_eq_session_cpu": g1_onnx == session_cpu_onnx,
        "g2_onnx_eq_g1_onnx": g2_onnx == g1_onnx,
        "c1_onnx_eq_session_cpu": c1_onnx == session_cpu_onnx,
        "c1_onnx_eq_g1_onnx": c1_onnx == g1_onnx,
        "c1b_eq_c1": c1b_onnx == c1_onnx,
    }
    for key, expected_bool in recomputed.items():
        assert comparisons[key] == expected_bool, (
            f"comparisons.{key} の記帳値 {comparisons[key]!r} が arms/expected_values "
            f"から再計算した {expected_bool!r} と食い違う"
        )


# --- (4) session CPU export ONNX sha256 のクロスファイル整合 -------------


def test_session_cpu_onnx_sha256_matches_provenance_closure(probe_doc: Dict[str, Any]) -> None:
    """probe json の `expected_values.session_cpu_onnx_sha256` が
    `run4_provenance_closure_2026-08-23.json`（正本）に実在すること。
    どのフィールド名でその値を持つかは closure JSON 側の改訂で動き得るため
    値の実在をテキスト走査で確認する（構造パスをハードコードしない）。"""
    assert PROVENANCE_CLOSURE_PATH.is_file(), f"{PROVENANCE_CLOSURE_PATH} が無い"
    session_cpu_onnx = probe_doc["expected_values"]["session_cpu_onnx_sha256"]
    closure_text = PROVENANCE_CLOSURE_PATH.read_text(encoding="utf-8")
    assert session_cpu_onnx in closure_text, (
        f"expected_values.session_cpu_onnx_sha256 ({session_cpu_onnx}) が "
        f"{PROVENANCE_CLOSURE_PATH.name} に見つからない（クロスファイル pin の食い違い）"
    )


# --- (5) 記録済み run4 anchor WAV sha256 のクロスファイル整合 -----------


def test_recorded_run4_wav_sha256_matches_anchor_provenance(probe_doc: Dict[str, Any]) -> None:
    assert ANCHOR_PROVENANCE_PATH.is_file(), f"{ANCHOR_PROVENANCE_PATH} が無い"
    anchor_doc = json.loads(ANCHOR_PROVENANCE_PATH.read_text(encoding="utf-8"))
    anchor_wavs = anchor_doc["anchor_wavs"]

    recorded = probe_doc["expected_values"]["recorded_run4_wav_sha256"]
    assert set(recorded) == set(_VOICE_TO_ANCHOR_FILENAME), (
        "expected_values.recorded_run4_wav_sha256 のキー集合が想定 6 音声と食い違う"
    )
    for voice, filename in _VOICE_TO_ANCHOR_FILENAME.items():
        assert filename in anchor_wavs, (
            f"{ANCHOR_PROVENANCE_PATH.name} に {filename} の anchor_wavs エントリが無い"
        )
        anchor_sha = anchor_wavs[filename]["sha256"]
        assert recorded[voice] == anchor_sha, (
            f"expected_values.recorded_run4_wav_sha256[{voice}] ({recorded[voice]}) が "
            f"{ANCHOR_PROVENANCE_PATH.name} の anchor_wavs[{filename}].sha256 ({anchor_sha}) "
            "と食い違う"
        )


# --- (6) 台帳クロスチェック ----------------------------------------------


def test_ledger_vg_debt_008_status_is_accepted_residual(ledger: Dict[str, Any]) -> None:
    debts = {d["debt_id"]: d for d in ledger["debts"]}
    assert "VG-DEBT-008" in debts, "VG-DEBT-008 が debt_ledger.yaml に無い"
    assert debts["VG-DEBT-008"]["status"] == "accepted_residual"


def test_ledger_vg_debt_008_evidence_delivered_names_probe_json(ledger: Dict[str, Any]) -> None:
    debts = {d["debt_id"]: d for d in ledger["debts"]}
    entries = (debts["VG-DEBT-008"].get("repayment") or {}).get("evidence_delivered") or []
    assert any(
        isinstance(e, str)
        and e.startswith(
            "voice_genesis/foundry/results_s3/run4_export_device_probe_2026-08-23.json"
        )
        for e in entries
    ), "VG-DEBT-008 の evidence_delivered に run4_export_device_probe_2026-08-23.json が無い"


def test_ledger_reentry_condition_a1_still_bars_wav_only_promotion(
    ledger: Dict[str, Any],
) -> None:
    """(a-2) の機能的裏付け記帳が (a-1) の昇格禁止文言を蝕んでいないことの
    ガード（この追記で規約文が薄まる回帰の検出）。"""
    debts = {d["debt_id"]: d for d in ledger["debts"]}
    reentry = debts["VG-DEBT-008"].get("reentry_condition") or []
    a1_entries = [
        e for e in reentry if isinstance(e, str) and e.strip().startswith("(a-1)")
    ]
    assert a1_entries, "VG-DEBT-008.reentry_condition に (a-1) エントリが無い"
    assert any("anchor WAV 6 本の一致では昇格しない" in e for e in a1_entries), (
        "(a-1) の『anchor WAV 6 本の一致では昇格しない』文言が消えている"
    )


# --- (7) report md ---------------------------------------------------------


def test_report_md_exists_and_mentions_key_findings() -> None:
    assert PROBE_REPORT_PATH.is_file(), f"{PROBE_REPORT_PATH} が無い"
    text = PROBE_REPORT_PATH.read_text(encoding="utf-8")
    assert "6/6" in text
    assert "measured_only" in text


def test_no_forbidden_ranking_vocabulary_in_new_files(probe_doc: Dict[str, Any]) -> None:
    """PR #307 終端宣言（追補 2）の順位付け語彙禁止を、本 PR で新規追加された
    2 ファイル（probe JSON・report md）のみに対して機械強制する
    （"主因" は他所の履歴的文脈で正当に使われているためスキャン対象外＝
    本テストでは走査すらしない）。"""
    report_text = PROBE_REPORT_PATH.read_text(encoding="utf-8")
    json_text = PROBE_JSON_PATH.read_text(encoding="utf-8")
    for term in FORBIDDEN_RANKING_VOCAB:
        assert term not in report_text, f"禁止語彙 {term!r} が report md に出現"
        assert term not in json_text, f"禁止語彙 {term!r} が probe JSON に出現"
