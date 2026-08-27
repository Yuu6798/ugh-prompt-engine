"""test_education_lesson_builder.py — RUN9-L0-HARNESS-3b: repo canonical
`education_lesson_builder.py` + `inputs/education_technique_lesson_manifest.json`
+ `run9_schema.load_pinned_education_lesson_manifest()` の最低テスト。

fixture は合成ミニデータ（tmp_path 上に構築した小さな lab/musicxml/wav 相当
の in-memory dict、または manifest 内容の in-memory コピー）のみを用いる。
**実 PJS 音源は一切使用しない**（training_bundle.json/validation_bundle.json
実体ファイルは rights 制約により repo 非収載——`HARNESS3B_EDUCATION_LESSON_
RECORD.md` 参照。実データに対する抽出・byte 再現性実測は session workdir
限定で別途実施済み）。全テストは高速（音声合成・実抽出を伴わない）。
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
import yaml

_THIS_DIR = Path(__file__).resolve().parent
_RUN_DIR = _THIS_DIR.parent
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

import run9_schema as m  # noqa: E402
import education_lesson_builder as elb  # noqa: E402

CONTRACT_PATH = _RUN_DIR / "RUN9_CONTRACT.yaml"
EDUCATION_ADJUDICATION_PATH = (
    _RUN_DIR / "USER_ADJUDICATION_20260827_PJS_LESSON_FREEZE.txt"
)


@pytest.fixture(scope="module")
def contract_raw() -> Dict[str, Any]:
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def contract() -> m.Run9RunContract:
    return m.load_run9_contract_from_yaml_path(CONTRACT_PATH)


def _education_manifest_data() -> Dict[str, Any]:
    return m._loads_strict_json(m.EDUCATION_MANIFEST_PATH.read_text(encoding="utf-8"))


def _canonical_json_bytes(data: Dict[str, Any]) -> bytes:
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _tampered_education_manifest_contract(
    contract: m.Run9RunContract, tmp_path: Path, *, mutate,
) -> Tuple[m.Run9RunContract, Path, Path]:
    """`education_technique_lesson_manifest.json` の内容を `mutate` で改変
    し、その実バイト sha256 で `education_technique_lesson_manifest_sha`
    pin を差し替えた合成 contract + manifest ファイル + contract ファイル
    を用意するテストヘルパー（`_tampered_speaker_map_contract()` と同型）。
    """
    data = copy.deepcopy(_education_manifest_data())
    mutate(data)
    manifest_bytes = _canonical_json_bytes(data)
    manifest_path = tmp_path / "education_technique_lesson_manifest.json"
    manifest_path.write_bytes(manifest_bytes)
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    tampered_raw = copy.deepcopy(contract.raw)
    tampered_raw["education_technique_lesson_manifest_sha"] = {
        "value": manifest_sha, "status": "PINNED",
    }
    tampered_contract_path = tmp_path / "RUN9_CONTRACT.yaml"
    tampered_contract_path.write_text(yaml.safe_dump(tampered_raw, allow_unicode=True), encoding="utf-8")
    return m.load_run9_contract(tampered_raw), manifest_path, tampered_contract_path


# ---------------------------------------------------------------------------
# repo 収載ファイルの存在 + byte-identical コピー照合
# ---------------------------------------------------------------------------


def test_harness3b_adjudication_source_file_exists() -> None:
    assert EDUCATION_ADJUDICATION_PATH.is_file()


def test_harness3b_spec_is_byte_identical_to_frozen_sha() -> None:
    assert m.compute_file_sha256(m.EDUCATION_LESSON_SPEC_PATH) == (
        "8f78ccdb275a9acca6b08ec75535d26863bb0464c6e23150829146339f2ff39c"
    )


def test_harness3b_freeze_record_and_superseded_present() -> None:
    assert m.EDUCATION_LESSON_FREEZE_RECORD_PATH.is_file()
    assert m.EDUCATION_LESSON_SUPERSEDED_FREEZE_RECORD_PATH.is_file()
    current = json.loads(m.EDUCATION_LESSON_FREEZE_RECORD_PATH.read_text(encoding="utf-8"))
    superseded = json.loads(
        m.EDUCATION_LESSON_SUPERSEDED_FREEZE_RECORD_PATH.read_text(encoding="utf-8")
    )
    assert current["metric_version"] == "h3b-extractor-spec/1.1"
    assert superseded["metric_version"] == "h3b-extractor-spec/1"
    assert current["supersedes"] == "h3b_freeze_record.superseded.1.json"


def test_harness3b_detail_record_present() -> None:
    assert m.EDUCATION_LESSON_DETAIL_RECORD_PATH.is_file()


def test_harness3b_builder_file_present() -> None:
    assert m.EDUCATION_LESSON_BUILDER_PATH.is_file()
    assert m.EDUCATION_LESSON_BUILDER_PATH.name == "education_lesson_builder.py"


# ---------------------------------------------------------------------------
# contract sha 照合 + validate_education_lesson_manifest() PASS
# ---------------------------------------------------------------------------


def test_harness3b_contract_sha_pinned_and_matches_manifest_bytes(
    contract: m.Run9RunContract,
) -> None:
    field = contract.pin_field("education_technique_lesson_manifest_sha")
    assert field["status"] == "PINNED"
    assert field["value"] == m.compute_file_sha256(m.EDUCATION_MANIFEST_PATH)


def test_harness3b_validate_education_lesson_manifest_passes_on_real_manifest() -> None:
    data = _education_manifest_data()
    m.validate_education_lesson_manifest(data)  # must not raise


# ---------------------------------------------------------------------------
# 三系統語彙対応表: schema 定数 <-> PERFORMANCE_RESIDUAL_VOCAB /
# EDUCATION_ALLOWED_CHANNELS <-> builder 側コピー <-> real manifest の一致
# ---------------------------------------------------------------------------


def test_harness3b_vocab_map_extracted_trait_is_performance_residual_vocab_member() -> None:
    for row in m.TECHNIQUE_LESSON_CHANNEL_VOCABULARY_MAP:
        assert row["extracted_trait"] in m.PERFORMANCE_RESIDUAL_VOCAB, row


def test_harness3b_vocab_map_education_allowed_channel_is_education_allowed_channels_member() -> None:
    for row in m.TECHNIQUE_LESSON_CHANNEL_VOCABULARY_MAP:
        assert row["education_allowed_channel"] in m.EDUCATION_ALLOWED_CHANNELS, row


def test_harness3b_vocab_map_has_five_rows() -> None:
    assert len(m.TECHNIQUE_LESSON_CHANNEL_VOCABULARY_MAP) == 5


def test_harness3b_builder_vocab_map_matches_schema_constant() -> None:
    assert elb.CHANNEL_VOCABULARY_MAP == list(m.TECHNIQUE_LESSON_CHANNEL_VOCABULARY_MAP)


def test_harness3b_real_manifest_vocab_map_matches_schema_constant() -> None:
    data = _education_manifest_data()
    assert data["channel_vocabulary_map"] == list(m.TECHNIQUE_LESSON_CHANNEL_VOCABULARY_MAP)


def test_harness3b_builder_bundle_format_matches_schema_constant() -> None:
    assert elb.BUNDLE_FORMAT == m.SCHEMA_TECHNIQUE_LESSON_BUNDLE


# ---------------------------------------------------------------------------
# load_pinned_education_lesson_manifest(): 正常系
# ---------------------------------------------------------------------------


def test_harness3b_load_pinned_education_lesson_manifest_happy_path(
    contract: m.Run9RunContract,
) -> None:
    data = m.load_pinned_education_lesson_manifest(contract)
    assert data["schema"] == m.SCHEMA_EDUCATION_TECHNIQUE_LESSON_MANIFEST
    assert data["alignment_accounting"]["total_songs"] == 85


def test_harness3b_load_pinned_education_lesson_manifest_missing_file_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    missing_path = tmp_path / "does_not_exist.json"
    with pytest.raises(m.Run9ValidationError, match="does not exist"):
        m.load_pinned_education_lesson_manifest(contract, manifest_path=missing_path)


def test_harness3b_load_pinned_education_lesson_manifest_byte_tampering_detected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    tampered_path = tmp_path / "education_technique_lesson_manifest.json"
    tampered_path.write_bytes(m.EDUCATION_MANIFEST_PATH.read_bytes() + b"\n")
    with pytest.raises(m.Run9ValidationError, match="実バイト sha256"):
        m.load_pinned_education_lesson_manifest(contract, manifest_path=tampered_path)


def test_harness3b_load_pinned_education_lesson_manifest_rejects_when_not_pinned(
    contract_raw: Dict[str, Any], tmp_path: Path,
) -> None:
    tampered = copy.deepcopy(contract_raw)
    tampered["education_technique_lesson_manifest_sha"] = {
        "value": None, "status": "PENDING", "reason": "test",
    }
    tampered_yaml_path = tmp_path / "RUN9_CONTRACT.yaml"
    tampered_yaml_path.write_text(yaml.safe_dump(tampered, allow_unicode=True), encoding="utf-8")
    tampered_contract = m.load_run9_contract(tampered)
    with pytest.raises(m.Run9ValidationError, match="not PINNED"):
        m.load_pinned_education_lesson_manifest(tampered_contract, contract_path=tampered_yaml_path)


def test_harness3b_load_pinned_education_lesson_manifest_detects_in_process_contract_tampering(
    contract: m.Run9RunContract,
) -> None:
    tampered_contract = copy.deepcopy(contract)
    tampered_contract.raw["education_technique_lesson_manifest_sha"] = {
        "value": "f" * 64, "status": "PINNED", "source": "forged",
    }
    with pytest.raises(m.Run9ValidationError, match="tampering evidence"):
        m.load_pinned_education_lesson_manifest(tampered_contract)


# ---------------------------------------------------------------------------
# load_pinned_education_lesson_manifest(): builder_provenance / adjudication
# cross-check tamper 拒否
# ---------------------------------------------------------------------------


def test_harness3b_adjudication_sha_forged_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    def _mutate(data: Dict[str, Any]) -> None:
        data["adjudication_basis"]["sha256"] = "0" * 64

    tampered_contract, manifest_path, contract_path = _tampered_education_manifest_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="adjudication_basis.sha256"):
        m.load_pinned_education_lesson_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


@pytest.mark.parametrize(
    "sha_key,label",
    [
        ("builder_sha256", "builder_provenance.builder_sha256"),
        ("spec_sha256", "builder_provenance.spec_sha256"),
        ("freeze_record_sha256", "builder_provenance.freeze_record_sha256"),
        ("superseded_freeze_record_sha256", "builder_provenance.superseded_freeze_record_sha256"),
        ("detail_record_sha256", "builder_provenance.detail_record_sha256"),
    ],
)
def test_harness3b_builder_provenance_sha_forged_rejected(
    contract: m.Run9RunContract, tmp_path: Path, sha_key: str, label: str,
) -> None:
    def _mutate(data: Dict[str, Any]) -> None:
        data["builder_provenance"][sha_key] = "0" * 64

    tampered_contract, manifest_path, contract_path = _tampered_education_manifest_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match=re_escape_dot(label)):
        m.load_pinned_education_lesson_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def re_escape_dot(text: str) -> str:
    """`pytest.raises(match=...)` は regex — `label` 内の `.` をリテラル
    一致させるためエスケープするだけの小ヘルパー（`re.escape` の全面適用は
    可読性を落とすため `.` のみ）。"""
    return text.replace(".", r"\.")


def test_harness3b_builder_provenance_repo_relative_path_absolute_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    def _mutate(data: Dict[str, Any]) -> None:
        data["builder_provenance"]["repo_relative_path"] = "/etc/passwd"

    tampered_contract, manifest_path, contract_path = _tampered_education_manifest_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="repo-containment guard"):
        m.load_pinned_education_lesson_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_harness3b_builder_provenance_repo_relative_path_traversal_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    def _mutate(data: Dict[str, Any]) -> None:
        data["builder_provenance"]["repo_relative_path"] = "../../../../etc/passwd"

    tampered_contract, manifest_path, contract_path = _tampered_education_manifest_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="repo-containment guard"):
        m.load_pinned_education_lesson_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_harness3b_channel_vocabulary_map_tampered_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    def _mutate(data: Dict[str, Any]) -> None:
        data["channel_vocabulary_map"][0]["education_allowed_channel"] = "phrasing"

    tampered_contract, manifest_path, contract_path = _tampered_education_manifest_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="channel_vocabulary_map"):
        m.load_pinned_education_lesson_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_harness3b_alignment_accounting_total_not_85_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    def _mutate(data: Dict[str, Any]) -> None:
        data["alignment_accounting"]["total_songs"] = 84

    tampered_contract, manifest_path, contract_path = _tampered_education_manifest_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="total_songs"):
        m.load_pinned_education_lesson_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_harness3b_alignment_accounting_sum_mismatch_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    def _mutate(data: Dict[str, Any]) -> None:
        data["alignment_accounting"]["aligned_count"] = 82

    tampered_contract, manifest_path, contract_path = _tampered_education_manifest_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="aligned_count"):
        m.load_pinned_education_lesson_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_harness3b_alignment_accounting_song_id_count_mismatch_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    def _mutate(data: Dict[str, Any]) -> None:
        data["alignment_accounting"]["count_mismatch_song_ids"] = ["pjs008"]

    tampered_contract, manifest_path, contract_path = _tampered_education_manifest_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="count_mismatch_song_ids"):
        m.load_pinned_education_lesson_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_harness3b_determinism_evidence_run_mismatch_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    def _mutate(data: Dict[str, Any]) -> None:
        data["determinism_evidence"]["training"]["run2_sha256"] = "1" * 64

    tampered_contract, manifest_path, contract_path = _tampered_education_manifest_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="determinism_evidence.training"):
        m.load_pinned_education_lesson_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


# ---------------------------------------------------------------------------
# per-founder 構造拒否（PoR §12 の一般規則の education manifest 側の確認）
# ---------------------------------------------------------------------------


def test_harness3b_validate_education_lesson_manifest_rejects_founder_id_key() -> None:
    data = copy.deepcopy(_education_manifest_data())
    data["founder_id"] = "R9F-01"
    with pytest.raises(m.Run9ValidationError, match="founder_id"):
        m.validate_education_lesson_manifest(data)


def test_harness3b_validate_education_lesson_manifest_rejects_founder_id_value() -> None:
    data = copy.deepcopy(_education_manifest_data())
    data["extraction_dependency_pins"]["note"] = "R9F-01"
    with pytest.raises(m.Run9ValidationError, match="founder"):
        m.validate_education_lesson_manifest(data)


# ---------------------------------------------------------------------------
# D3: run9 配下の他モジュール（builder 自身と tests を除く）が
# education_lesson_builder を import しないことを AST で検査する discipline
# テスト（将来の learner 分離の前哨）。
# ---------------------------------------------------------------------------


def _imports_education_lesson_builder(py_path: Path) -> bool:
    tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "education_lesson_builder":
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None and node.module.split(".")[0] == "education_lesson_builder":
                return True
    return False


def test_harness3b_d3_no_other_run9_module_imports_education_lesson_builder() -> None:
    offenders = []
    for py_path in sorted(_RUN_DIR.glob("*.py")):
        if py_path.name in ("education_lesson_builder.py",):
            continue
        if "__pycache__" in py_path.parts:
            continue
        if _imports_education_lesson_builder(py_path):
            offenders.append(str(py_path.relative_to(_RUN_DIR)))
    assert offenders == [], (
        f"education_lesson_builder is imported by non-builder run9 module(s): {offenders} "
        "— builder identity is a session-artifact-generating fixture, not a dependency of the "
        "identity/learning machinery (learner separation invariant)"
    )


# ---------------------------------------------------------------------------
# lesson_record バンドル内蔵検証（合成ミニバンドル、実 PJS データ不使用）
# ---------------------------------------------------------------------------


def _synthetic_song(song_id: str) -> Dict[str, Any]:
    """`education_lesson_builder.extract_song()` の出力形状を模した最小
    合成 song dict（1 phrase・1 mora のみ、`aligned` 済み）。実 WAV/lab/
    musicxml パースは一切通さない——`assemble_bundle()`/`build_lesson_
    record()` の直列化・validate_lesson_record() 経路のみを検証する。"""
    return {
        "song_id": song_id,
        "alignment_status": "aligned",
        "lab_mora_count": 1,
        "score_mora_count": 1,
        "wav_header": {"channels": 1, "sample_rate": 48000, "bits_per_sample": 24},
        "phrases": [{"phrase_index": 0, "offset_p_s": 0.0, "lab_mora_indices": [0]}],
        "channels": {
            "relative_F0": {
                "status": "extracted",
                "morae": [{"mora_index": 0, "frames": [{"t_s": 0.0, "voiced": True, "value_hz": 1.0}]}],
            },
            "duration_ratio": {"status": "extracted", "morae": [{"mora_index": 0, "value": 1.0}]},
            "energy_envelope": {
                "status": "extracted",
                "phrases": [
                    {"phrase_index": 0, "status": "extracted", "blocks": [{"k": 0, "t_s": 0.0, "value": 1.0}]},
                ],
            },
            "onset_offset": {
                "status": "extracted",
                "attack_timing": {"status": "extracted", "morae": [{"mora_index": 0, "value_s": 0.0}]},
                "phrase_end_timing": {
                    "status": "extracted",
                    "phrases": [{"phrase_index": 0, "value_s": 0.0}],
                },
            },
        },
    }


def test_harness3b_synthetic_mini_bundle_lesson_record_validates() -> None:
    songs = [_synthetic_song("pjs999")]
    bundle = elb.assemble_bundle("training", ["pjs999"], songs, "d" * 64)
    assert bundle["format"] == m.SCHEMA_TECHNIQUE_LESSON_BUNDLE
    assert bundle["split"] == "training"
    assert bundle["not_extracted_summary"]["aligned_count"] == 1
    assert bundle["not_extracted_summary"]["count_mismatch_count"] == 0
    m.validate_lesson_record(bundle["lesson_record"])  # must not raise


def test_harness3b_synthetic_mini_bundle_count_mismatch_not_extracted() -> None:
    song = _synthetic_song("pjs998")
    song["alignment_status"] = "count_mismatch"
    song["reason"] = "lab_mora_count=1 != score_mora_count=2"
    song["channels"] = {
        trait: {"status": "not_extracted", "reason": "alignment_status=count_mismatch"}
        for trait in elb.EXTRACTED_TRAITS
    }
    bundle = elb.assemble_bundle("validation", ["pjs998"], [song], "d" * 64)
    assert bundle["not_extracted_summary"]["count_mismatch_count"] == 1
    assert bundle["not_extracted_summary"]["count_mismatch_song_ids"] == ["pjs998"]
    # lesson_record itself is still schema-valid even when a song is
    # not_extracted (the per-song not_extracted status lives in `songs`, not
    # in `lesson_record`).
    m.validate_lesson_record(bundle["lesson_record"])  # must not raise


def test_harness3b_write_bundle_json_is_deterministic_serialization() -> None:
    songs = [_synthetic_song("pjs997")]
    bundle = elb.assemble_bundle("training", ["pjs997"], songs, "d" * 64)
    text1 = json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    # rebuild independently and confirm byte-identical serialization (spec §5).
    bundle2 = elb.assemble_bundle("training", ["pjs997"], [_synthetic_song("pjs997")], "d" * 64)
    text2 = json.dumps(bundle2, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    assert text1 == text2


def test_harness3b_write_bundle_json_is_atomic_happy_path(tmp_path: Path) -> None:
    """PR #329 第3巡レビュー指摘5（P2、採用対応）: `write_bundle_json()`
    は `Path.write_bytes()` の直書きではなく `_atomic_write_bytes()` +
    `os.replace()` を使う——正常系では従来どおり最終バイト列が書かれ、
    staging の残骸が残らないことを確認する。"""
    out_path = tmp_path / "assembled.json"
    bundle = elb.assemble_bundle("training", ["pjs997"], [_synthetic_song("pjs997")], "d" * 64)
    elb.write_bundle_json(bundle, out_path)
    assert out_path.read_bytes() == elb._serialize_bundle_json(bundle)  # noqa: SLF001
    leftovers = sorted(p.name for p in tmp_path.iterdir() if p != out_path)
    assert leftovers == []


def test_harness3b_write_bundle_json_failure_injection_leaves_old_artifact_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`write_bundle_json()` の書き込み途中失敗注入回帰テスト（PR #329
    第3巡レビュー指摘5, P2, 採用対応の必須テスト）: staging 段
    （`_atomic_write_bytes()`）を monkeypatch で失敗させると、旧世代
    artifact が無傷のまま残り、staging の残骸も残らないことを確認する
    ——`assemble` サブコマンドの単本出力が書き込み途中の失敗で破損した
    部分書き込みバイト列に上書きされないことの直接証跡。"""
    out_path = tmp_path / "assembled.json"
    out_path.write_bytes(b'{"gen":"old"}\n')

    def _boom(path: Path, data: bytes) -> Path:  # noqa: ARG001
        raise RuntimeError("synthetic assemble staging failure")

    monkeypatch.setattr(elb, "_atomic_write_bytes", _boom)

    bundle = elb.assemble_bundle("training", ["pjs997"], [_synthetic_song("pjs997")], "d" * 64)
    with pytest.raises(RuntimeError, match="synthetic assemble staging failure"):
        elb.write_bundle_json(bundle, out_path)

    assert out_path.read_bytes() == b'{"gen":"old"}\n'
    leftovers = sorted(p.name for p in tmp_path.iterdir() if p != out_path)
    assert leftovers == []


# ---------------------------------------------------------------------------
# PR #329 第7巡レビュー指摘（採用2, P2）: `_cmd_extract_song()`/`_cmd_probe_
# header()` の出力書き込みを `Path.write_text()` の直書きから
# `write_bundle_bytes()`（`_atomic_write_bytes()` + `os.replace()`）経由へ
# 統一——`assemble` サブコマンド（上記 `test_harness3b_write_bundle_json_
# failure_injection_leaves_old_artifact_intact`）と同型の失敗注入回帰
# テストを両コマンドに対して追加する。`_atomic_write_bytes()` を monkeypatch
# で失敗させ、旧世代の中間物 JSON が無傷のまま残り、staging の残骸も
# 残らないことを確認する。`main()` は `RuntimeError` を捕捉しない
# （`ExtractorStopError`/`Run9ValidationError` のみ捕捉）ため、`elb.main()`
# 経由ではなく `_cmd_extract_song()`/`_cmd_probe_header()` を直接呼び出す。
# ---------------------------------------------------------------------------


def test_harness3b_extract_song_cli_write_failure_injection_leaves_old_intermediate_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_cmd_extract_song()` の出力書き込み失敗注入回帰テスト（PR #329
    第7巡レビュー指摘, 採用2, P2）: `extract_song()` 本体（decode）は
    monkeypatch で固定結果を返させ、`write_bundle_bytes()` 内部の
    `_atomic_write_bytes()` staging 段だけを失敗させる——旧世代の中間物
    JSON が無傷のまま残り、staging の残骸も残らないことを確認する。"""
    training_ids, _validation_ids, _sealed_ids = _practice_split_ids()
    song_id = training_ids[0]
    out_path = tmp_path / "intermediate.json"
    out_path.write_bytes(b'{"gen":"old"}\n')

    def _fake_extract_song(
        song_dir: Path, sid: str, *, frozen_split_pins: Any, consumed_inputs_pins: Any,
    ) -> Dict[str, Any]:  # noqa: ARG001
        return {"song_id": sid, "alignment_status": "ok"}

    monkeypatch.setattr(elb, "extract_song", _fake_extract_song)

    def _boom(path: Path, data: bytes) -> Path:  # noqa: ARG001
        raise RuntimeError("synthetic extract-song staging failure")

    monkeypatch.setattr(elb, "_atomic_write_bytes", _boom)

    args = argparse.Namespace(
        corpus_root=str(tmp_path), song_id=song_id,
        freeze_record=str(elb.DEFAULT_FREEZE_RECORD_PATH), spec_path=str(elb.DEFAULT_SPEC_PATH),
        out=str(out_path),
    )
    with pytest.raises(RuntimeError, match="synthetic extract-song staging failure"):
        elb._cmd_extract_song(args)  # noqa: SLF001

    assert out_path.read_bytes() == b'{"gen":"old"}\n'
    leftovers = sorted(p.name for p in tmp_path.iterdir() if p != out_path and p.name != song_id)
    assert leftovers == []


def test_harness3b_probe_header_cli_write_failure_injection_leaves_old_output_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_cmd_probe_header()` の出力書き込み失敗注入回帰テスト（PR #329
    第7巡レビュー指摘, 採用2, P2）: 対象 WAV は実ファイル（`read_wav_fmt_
    header()` を素通しし、書き込みステップまで正常に到達させる——欠落
    WAV だと `open()` の `FileNotFoundError` は `WavHeaderError` へラップ
    されず本テストの意図する書き込み経路まで到達しない）を用意し、
    `_atomic_write_bytes()` staging 段だけを失敗させる——旧世代の出力
    JSON が無傷のまま残り、staging の残骸も残らないことを確認する。"""
    training_ids, _validation_ids, _sealed_ids = _practice_split_ids()
    song_id = training_ids[0]
    song_dir = tmp_path / song_id
    song_dir.mkdir()
    wav_path = song_dir / f"{song_id}_song.wav"
    wav_path.write_bytes(b"pretend-wav-bytes")
    out_path = tmp_path / "header.json"
    out_path.write_bytes(b'{"gen":"old"}\n')

    def _boom(path: Path, data: bytes) -> Path:  # noqa: ARG001
        raise RuntimeError("synthetic probe-header staging failure")

    monkeypatch.setattr(elb, "_atomic_write_bytes", _boom)

    args = argparse.Namespace(corpus_root=str(tmp_path), song_ids=[song_id], out=str(out_path))
    with pytest.raises(RuntimeError, match="synthetic probe-header staging failure"):
        elb._cmd_probe_header(args)  # noqa: SLF001

    assert out_path.read_bytes() == b'{"gen":"old"}\n'
    assert wav_path.read_bytes() == b"pretend-wav-bytes"
    leftovers = sorted(p.name for p in tmp_path.iterdir() if p != out_path and p.name != song_id)
    assert leftovers == []


# ---------------------------------------------------------------------------
# freeze_selfcheck(): spec sha256 照合のみ（extractor 自己照合を行わない
# 意味論変更 — education_lesson_builder.py モジュール docstring 参照）
# ---------------------------------------------------------------------------


def test_harness3b_freeze_selfcheck_passes_on_real_spec_and_freeze_record() -> None:
    record = elb.freeze_selfcheck(
        m.EDUCATION_LESSON_FREEZE_RECORD_PATH, m.EDUCATION_LESSON_SPEC_PATH,
    )
    assert record["metric_version"] == "h3b-extractor-spec/1.1"


def test_harness3b_freeze_selfcheck_rejects_spec_sha_mismatch(tmp_path: Path) -> None:
    freeze_record_path = tmp_path / "freeze.json"
    freeze_record_path.write_text(
        json.dumps({"spec_sha256": "0" * 64}), encoding="utf-8",
    )
    spec_path = tmp_path / "spec.md"
    spec_path.write_text("not the real spec", encoding="utf-8")
    with pytest.raises(elb.FreezeCheckError, match="spec sha256 mismatch"):
        elb.freeze_selfcheck(freeze_record_path, spec_path)


def test_harness3b_freeze_selfcheck_rejects_missing_freeze_record(tmp_path: Path) -> None:
    with pytest.raises(elb.FreezeCheckError, match="not found"):
        elb.freeze_selfcheck(tmp_path / "missing.json", m.EDUCATION_LESSON_SPEC_PATH)


def test_harness3b_load_training_validation_ids_excludes_sealed_holdout() -> None:
    """`load_training_validation_ids()` は `FrozenSplitPins`（PR #329 第3巡
    レビュー指摘2, P1, 採用対応で新設された不透明型）を返す ——
    `__iter__` により `training_ids, validation_ids = ...` の unpack 慣用句
    は引き続き成立する。"""
    frozen_split_pins = elb.load_training_validation_ids(
        m.PRACTICE_MANIFEST_PATH,
    )
    assert isinstance(frozen_split_pins, elb.FrozenSplitPins)
    training_ids, validation_ids = frozen_split_pins
    assert len(training_ids) == 70
    assert len(validation_ids) == 15
    assert list(training_ids) == sorted(training_ids)
    assert list(validation_ids) == sorted(validation_ids)
    assert set(training_ids).isdisjoint(validation_ids)
    assert frozen_split_pins.frozen_allowed_ids == tuple(sorted(set(training_ids) | set(validation_ids)))
    # sealed_holdout row_ids never enter this function's return value.
    split_manifest = json.loads(m.PRACTICE_MANIFEST_PATH.read_text(encoding="utf-8"))
    sealed = set(split_manifest["row_ids"]["sealed_holdout"])
    assert sealed.isdisjoint(training_ids)
    assert sealed.isdisjoint(validation_ids)


def test_harness3b_frozen_split_pins_rejects_raw_types_in_extract_song(tmp_path: Path) -> None:
    """`extract_song()` は `FrozenSplitPins`/`ConsumedInputPins` 以外の生
    list/dict を受理しない（PR #329 第3巡レビュー指摘2, P1, 採用対応の
    isinstance ゲート）。"""
    with pytest.raises(elb.ExtractorStopError, match="must be a FrozenSplitPins instance"):
        elb.extract_song(
            tmp_path / "pjs001", "pjs001",
            frozen_split_pins=["pjs001"],  # type: ignore[arg-type]
            consumed_inputs_pins=elb.ConsumedInputPins(pins={}),
        )
    with pytest.raises(elb.ExtractorStopError, match="must be a ConsumedInputPins instance"):
        elb.extract_song(
            tmp_path / "pjs001", "pjs001",
            frozen_split_pins=elb.FrozenSplitPins(training_ids=("pjs001",), validation_ids=()),
            consumed_inputs_pins={},  # type: ignore[arg-type]
        )


def test_harness3b_load_training_validation_ids_uses_pinned_default_manifest() -> None:
    """`split_manifest_path` 省略時は `DEFAULT_SPLIT_MANIFEST_PATH`（=
    正典 `inputs/practice_audio_split_manifest.json`）を pin 検証込みで
    読む（PR #329 第1巡レビュー指摘1 対応後の既定動作）。"""
    training_ids, validation_ids = elb.load_training_validation_ids()
    assert len(training_ids) == 70
    assert len(validation_ids) == 15


# ---------------------------------------------------------------------------
# PR #329 第1巡 Codex bot レビュー対応: sealed-holdout 境界の builder 側
# 機械強制（指摘1, P1, 採用）。
#
# 3系統のテスト:
#  (A) sealed ID が row_ids.training へ混入した split manifest を拒否
#  (B) sealed_holdout の song_id を直接 `extract-song` へ渡しても拒否
#      （decode/抽出前に停止する——実コーパスなしで検証可能）
#  (C) 改ざんされた（pin と実バイト sha256 が一致しない）split manifest
#      を拒否
# ---------------------------------------------------------------------------


def _practice_manifest_data() -> Dict[str, Any]:
    return json.loads(m.PRACTICE_MANIFEST_PATH.read_text(encoding="utf-8"))


def _tampered_practice_manifest_contract(
    tmp_path: Path, *, mutate,
) -> Tuple[Path, Path]:
    """`practice_audio_split_manifest.json` の内容を `mutate` で改変し、
    その実バイト sha256 で `practice_audio_split_manifest_sha` pin を
    差し替えた合成 manifest ファイル + contract ファイルを用意する
    （`_tampered_education_manifest_contract()` と同型のテストヘルパー。
    こちらは manifest 側の内容検証——`validate_practice_split_manifest()`
    ——を経由させたいテストのためのもので、`load_pinned_practice_split_
    manifest()` の「in-process contract tampering」自体の検出は
    `run9_schema` 側の責務であり、本ファイルでは重複テストしない）。
    戻り値は (tampered manifest path, tampered contract path)。"""
    data = copy.deepcopy(_practice_manifest_data())
    mutate(data)
    manifest_bytes = _canonical_json_bytes(data)
    manifest_path = tmp_path / "practice_audio_split_manifest.json"
    manifest_path.write_bytes(manifest_bytes)
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    contract_raw_data = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    tampered_raw = copy.deepcopy(contract_raw_data)
    tampered_raw["practice_audio_split_manifest_sha"] = {
        "value": manifest_sha, "status": "PINNED",
    }
    tampered_contract_path = tmp_path / "RUN9_CONTRACT.yaml"
    tampered_contract_path.write_text(yaml.safe_dump(tampered_raw, allow_unicode=True), encoding="utf-8")
    return manifest_path, tampered_contract_path


def test_harness3b_split_manifest_sealed_id_injected_into_training_rejected(
    tmp_path: Path,
) -> None:
    """(A) sealed_holdout の row_id が row_ids.training へ混入した split
    manifest は、たとえ pin 値をその改変後バイトへ差し替えても
    `validate_practice_split_manifest()`（3集合非交差検証）が fail-closed
    で拒否する——`--split-manifest` に任意ファイルを渡す迂回では
    sealed-holdout 境界を突破できないことの確認。"""

    def _mutate(data: Dict[str, Any]) -> None:
        sealed_id = sorted(data["row_ids"]["sealed_holdout"])[0]
        data["row_ids"]["training"] = sorted(data["row_ids"]["training"] + [sealed_id])

    manifest_path, contract_path = _tampered_practice_manifest_contract(tmp_path, mutate=_mutate)
    with pytest.raises(m.Run9ValidationError, match="sealed_holdout overlaps training"):
        elb.load_training_validation_ids(manifest_path, contract_path=contract_path)


def test_harness3b_split_manifest_sealed_id_injected_into_validation_rejected(
    tmp_path: Path,
) -> None:
    """(A) の validation 側対称ケース。"""

    def _mutate(data: Dict[str, Any]) -> None:
        sealed_id = sorted(data["row_ids"]["sealed_holdout"])[0]
        data["row_ids"]["validation"] = sorted(data["row_ids"]["validation"] + [sealed_id])

    manifest_path, contract_path = _tampered_practice_manifest_contract(tmp_path, mutate=_mutate)
    with pytest.raises(m.Run9ValidationError, match="sealed_holdout overlaps validation"):
        elb.load_training_validation_ids(manifest_path, contract_path=contract_path)


def test_harness3b_split_manifest_count_mismatch_rejected(tmp_path: Path) -> None:
    """(A) 派生ケース: 3集合が非交差のままでも、training=70/validation=15/
    sealed_holdout=15 の固定件数（裁定 §1）と食い違う split manifest は
    `load_training_validation_ids()` 自身が拒否する。"""

    def _mutate(data: Dict[str, Any]) -> None:
        moved = data["row_ids"]["training"].pop()
        data["row_ids"]["validation"] = sorted(data["row_ids"]["validation"] + [moved])

    manifest_path, contract_path = _tampered_practice_manifest_contract(tmp_path, mutate=_mutate)
    with pytest.raises(elb.ExtractorStopError, match="row_ids counts must be exactly"):
        elb.load_training_validation_ids(manifest_path, contract_path=contract_path)


def test_harness3b_extract_song_cli_rejects_sealed_holdout_song_id(tmp_path: Path) -> None:
    """(B) `extract-song --song-id <sealed_holdout id>` は、対応する
    WAV/lab/musicxml が一切存在しなくても（=実コーパス不要で検証可能）、
    decode/抽出前に `ExtractorStopError` で fail-closed 拒否される
    （main() の終了コード2 経路）。"""
    split_manifest = _practice_manifest_data()
    sealed_id = sorted(split_manifest["row_ids"]["sealed_holdout"])[0]
    out_path = tmp_path / "out.json"
    rc = elb.main([
        "extract-song",
        "--corpus-root", str(tmp_path),
        "--song-id", sealed_id,
        "--out", str(out_path),
    ])
    assert rc == 2
    assert not out_path.exists()


def test_harness3b_extract_song_cli_rejects_arbitrary_song_id(tmp_path: Path) -> None:
    """(B) 派生ケース: split manifest のいずれの集合にも属さない任意の
    song_id も同じ経路で拒否される。"""
    out_path = tmp_path / "out.json"
    rc = elb.main([
        "extract-song",
        "--corpus-root", str(tmp_path),
        "--song-id", "pjs999_not_a_real_song",
        "--out", str(out_path),
    ])
    assert rc == 2
    assert not out_path.exists()


def test_harness3b_probe_header_cli_rejects_sealed_holdout_song_id_before_opening_wav(
    tmp_path: Path,
) -> None:
    """PR #329 第3巡レビュー指摘6（P2、採用対応、凍結 split ゲート）:
    `probe-header --song-ids` に sealed_holdout の song_id が1件でも含まれ
    ていれば、対応する WAV が一切存在しなくても（＝WAV を open する前に）
    decode/処理前に拒否される（裁定 §2「sealed は完全性 hash と ID 確認
    以外の処理禁止」の遵守——header probe も「処理」に含まれる）。凍結集合
    内の他の song_id も、sealed ID が1件混入していれば道連れで全件拒否
    される（部分的に open してから拒否、という中途半端な状態を作らない）。
    """
    split_manifest = _practice_manifest_data()
    sealed_id = sorted(split_manifest["row_ids"]["sealed_holdout"])[0]
    training_ids, _validation_ids, _sealed_ids = _practice_split_ids()
    out_path = tmp_path / "out.json"
    rc = elb.main([
        "probe-header",
        "--corpus-root", str(tmp_path),  # empty — no WAV files present at all
        "--song-ids", training_ids[0], sealed_id,
        "--out", str(out_path),
    ])
    assert rc == 2
    assert not out_path.exists()


def test_harness3b_probe_header_direct_call_gate_raises_before_read_wav_fmt_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_cmd_probe_header()` のゲートが `read_wav_fmt_header()`（WAV を
    open する箇所）より前に効くことを、同関数を monkeypatch して「呼ばれ
    たら失敗させる」ことで直接確認する。"""

    def _boom(path: Path) -> Dict[str, Any]:  # pragma: no cover - must not run
        raise AssertionError("read_wav_fmt_header() must not be called for a rejected song_id batch")

    monkeypatch.setattr(elb, "read_wav_fmt_header", _boom)
    split_manifest = _practice_manifest_data()
    sealed_id = sorted(split_manifest["row_ids"]["sealed_holdout"])[0]
    out_path = tmp_path / "out.json"
    args = argparse.Namespace(
        corpus_root=str(tmp_path), song_ids=[sealed_id], out=str(out_path),
    )
    with pytest.raises(elb.ExtractorStopError, match="not a member of the pinned"):
        elb._cmd_probe_header(args)  # noqa: SLF001


def test_harness3b_extract_song_direct_call_gate_raises_before_extract_song() -> None:
    """`_cmd_extract_song()` のゲートが `extract_song()`（decode/抽出本体）
    より前に効くことを、`elb.extract_song` を monkeypatch して「呼ばれたら
    失敗させる」ことで確認する（sealed_holdout 拒否時に一切 decode へ
    到達しないことの直接証跡）。"""

    def _boom(song_dir: Path, song_id: str) -> Dict[str, Any]:  # pragma: no cover - must not run
        raise AssertionError("extract_song() must not be called for a rejected song_id")

    original = elb.extract_song
    elb.extract_song = _boom  # type: ignore[assignment]
    try:
        split_manifest = _practice_manifest_data()
        sealed_id = sorted(split_manifest["row_ids"]["sealed_holdout"])[0]
        args = argparse.Namespace(
            corpus_root="/nonexistent",
            song_id=sealed_id,
            freeze_record=str(m.EDUCATION_LESSON_FREEZE_RECORD_PATH),
            spec_path=str(m.EDUCATION_LESSON_SPEC_PATH),
            out="/nonexistent/out.json",
        )
        with pytest.raises(elb.ExtractorStopError, match="not a member of the pinned"):
            elb._cmd_extract_song(args)  # noqa: SLF001
    finally:
        elb.extract_song = original  # type: ignore[assignment]


def test_harness3b_split_manifest_byte_tampering_rejected(tmp_path: Path) -> None:
    """(C) `--split-manifest` へ渡したファイルの実バイトが `RUN9_
    CONTRACT.yaml` の `practice_audio_split_manifest_sha` pin 値と一致
    しなければ、内容が構造的に正しくても fail-closed で拒否される。"""
    tampered_path = tmp_path / "practice_audio_split_manifest.json"
    tampered_path.write_bytes(m.PRACTICE_MANIFEST_PATH.read_bytes() + b"\n")
    with pytest.raises(m.Run9ValidationError, match="実バイト sha256"):
        elb.load_training_validation_ids(tampered_path)


def test_harness3b_split_manifest_missing_file_rejected(tmp_path: Path) -> None:
    """(C) 派生ケース: `--split-manifest` が存在しないパスを指す場合も
    fail-closed で拒否される（direct json.load() 迂回の再現形の1つ）。"""
    with pytest.raises(m.Run9ValidationError, match="does not exist"):
        elb.load_training_validation_ids(tmp_path / "does_not_exist.json")


# ---------------------------------------------------------------------------
# PR #329 第1巡 Codex bot レビュー対応: バンドル2本の atomic ペア公開
# （指摘2, P1, 採用）。
# ---------------------------------------------------------------------------


def test_harness3b_publish_bundle_pair_happy_path(tmp_path: Path) -> None:
    training_path = tmp_path / "training_bundle.json"
    validation_path = tmp_path / "validation_bundle.json"
    training_bytes = b'{"gen":"new-training"}\n'
    validation_bytes = b'{"gen":"new-validation"}\n'
    elb.publish_bundle_pair(training_path, training_bytes, validation_path, validation_bytes)
    assert training_path.read_bytes() == training_bytes
    assert validation_path.read_bytes() == validation_bytes
    # no staging leftovers
    leftovers = sorted(p.name for p in tmp_path.iterdir() if p not in (training_path, validation_path))
    assert leftovers == []


def test_harness3b_publish_bundle_pair_failure_injection_leaves_old_generation_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """失敗注入回帰テスト（PR #329 第1巡レビュー指摘2 必須テスト）:
    validation 側の staging 書き込みを monkeypatch で失敗させると、
    training 側も含めどちらの最終名も置換されず、旧世代（存在すれば）が
    そのまま残ることを確認する——「新世代 training だけが現れる」混合
    世代ペアが観測されないことの直接証跡。"""
    training_path = tmp_path / "training_bundle.json"
    validation_path = tmp_path / "validation_bundle.json"
    training_path.write_bytes(b'{"gen":"old-training"}\n')
    validation_path.write_bytes(b'{"gen":"old-validation"}\n')

    real_atomic_write_bytes = elb._atomic_write_bytes  # noqa: SLF001

    def _boom(path: Path, data: bytes) -> Path:
        if path.name == "validation_bundle.json":
            raise RuntimeError("synthetic validation staging failure")
        return real_atomic_write_bytes(path, data)

    monkeypatch.setattr(elb, "_atomic_write_bytes", _boom)

    with pytest.raises(RuntimeError, match="synthetic validation staging failure"):
        elb.publish_bundle_pair(
            training_path, b'{"gen":"new-training"}\n',
            validation_path, b'{"gen":"new-validation"}\n',
        )

    # old generation survives untouched on BOTH sides — no new-gen training
    # appears while validation is stale/missing.
    assert training_path.read_bytes() == b'{"gen":"old-training"}\n'
    assert validation_path.read_bytes() == b'{"gen":"old-validation"}\n'
    # no staging leftovers (training staging was cleaned up after the
    # validation staging failure).
    leftovers = sorted(p.name for p in tmp_path.iterdir() if p not in (training_path, validation_path))
    assert leftovers == []


def test_harness3b_publish_bundle_pair_failure_injection_no_prior_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同上、旧世代が一切存在しない（初回 build）場合: 失敗後、最終出力
    ディレクトリに training_bundle.json/validation_bundle.json のいずれも
    現れないことを確認する（「新世代 training だけが現れる」を明示的に
    否定する）。"""
    training_path = tmp_path / "training_bundle.json"
    validation_path = tmp_path / "validation_bundle.json"

    real_atomic_write_bytes = elb._atomic_write_bytes  # noqa: SLF001

    def _boom(path: Path, data: bytes) -> Path:
        if path.name == "validation_bundle.json":
            raise RuntimeError("synthetic validation staging failure")
        return real_atomic_write_bytes(path, data)

    monkeypatch.setattr(elb, "_atomic_write_bytes", _boom)

    with pytest.raises(RuntimeError, match="synthetic validation staging failure"):
        elb.publish_bundle_pair(
            training_path, b'{"gen":"new-training"}\n',
            validation_path, b'{"gen":"new-validation"}\n',
        )

    assert not training_path.exists()
    assert not validation_path.exists()
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# PR #329 第2巡 Codex bot レビュー対応: assemble コマンドの凍結 split 強制
# （P1、採用）。中間物を1つも読む前に、要求 ID 集合が選択 split（training
# 70件/validation 15件）と厳密集合一致することを検証する。
# ---------------------------------------------------------------------------


def _practice_split_ids() -> Tuple[List[str], List[str], List[str]]:
    data = _practice_manifest_data()
    return (
        sorted(data["row_ids"]["training"]),
        sorted(data["row_ids"]["validation"]),
        sorted(data["row_ids"]["sealed_holdout"]),
    )


def test_harness3b_assemble_cli_rejects_sealed_id_mixed_into_training_request(
    tmp_path: Path,
) -> None:
    """sealed_holdout の1件が training 要求リストへ紛れ込んだ場合（凍結
    training の1件と差し替え）、中間物ディレクトリが空でも decode/読み込み
    前に拒否される。"""
    training_ids, _validation_ids, sealed_ids = _practice_split_ids()
    requested = training_ids[:-1] + [sealed_ids[0]]
    song_ids_path = tmp_path / "song_ids.json"
    song_ids_path.write_text(json.dumps(requested), encoding="utf-8")
    out_path = tmp_path / "out.json"
    rc = elb.main([
        "assemble",
        "--split", "training",
        "--song-ids-json", str(song_ids_path),
        "--intermediates-dir", str(tmp_path),  # empty — no <id>.json present
        "--out", str(out_path),
    ])
    assert rc == 2
    assert not out_path.exists()


def test_harness3b_assemble_cli_rejects_incomplete_training_request(tmp_path: Path) -> None:
    """凍結 training 集合から1件欠落したリストは拒否される（過不足なしの
    厳密集合一致——部分集合では READY 化しない）。"""
    training_ids, _validation_ids, _sealed_ids = _practice_split_ids()
    requested = training_ids[:-1]
    song_ids_path = tmp_path / "song_ids.json"
    song_ids_path.write_text(json.dumps(requested), encoding="utf-8")
    out_path = tmp_path / "out.json"
    rc = elb.main([
        "assemble",
        "--split", "training",
        "--song-ids-json", str(song_ids_path),
        "--intermediates-dir", str(tmp_path),
        "--out", str(out_path),
    ])
    assert rc == 2
    assert not out_path.exists()


def test_harness3b_assemble_cli_rejects_unknown_extra_id(tmp_path: Path) -> None:
    """凍結 training 集合に加えて凍結集合外の未知 ID を1件追加したリストは
    拒否される（過剰指定の拒否）。"""
    training_ids, _validation_ids, _sealed_ids = _practice_split_ids()
    requested = training_ids + ["pjs999_not_a_real_song"]
    song_ids_path = tmp_path / "song_ids.json"
    song_ids_path.write_text(json.dumps(requested), encoding="utf-8")
    out_path = tmp_path / "out.json"
    rc = elb.main([
        "assemble",
        "--split", "training",
        "--song-ids-json", str(song_ids_path),
        "--intermediates-dir", str(tmp_path),
        "--out", str(out_path),
    ])
    assert rc == 2
    assert not out_path.exists()


def test_harness3b_assemble_cli_rejects_validation_ids_under_training_split(
    tmp_path: Path,
) -> None:
    """training/validation の取り違え（--split training に validation の
    凍結 ID 集合を渡す）も拒否される。"""
    _training_ids, validation_ids, _sealed_ids = _practice_split_ids()
    song_ids_path = tmp_path / "song_ids.json"
    song_ids_path.write_text(json.dumps(validation_ids), encoding="utf-8")
    out_path = tmp_path / "out.json"
    rc = elb.main([
        "assemble",
        "--split", "training",
        "--song-ids-json", str(song_ids_path),
        "--intermediates-dir", str(tmp_path),
        "--out", str(out_path),
    ])
    assert rc == 2
    assert not out_path.exists()


def test_harness3b_assemble_cli_rejects_duplicate_song_id_in_frozen_training_request(
    tmp_path: Path,
) -> None:
    """PR #329 第3巡レビュー指摘1（P1、採用対応）: 凍結 training 集合の
    全 ID + 重複1件（凍結集合内の既存 ID をもう1回列挙）を渡すと、
    `_require_exact_frozen_split_membership()`（内部で `set()` 化するため
    重複を検出できない）より前に `_require_no_duplicate_song_ids()` が
    拒否する——中間物ディレクトリが空でも decode/読み込み前に拒否される
    ことを確認する。"""
    training_ids, _validation_ids, _sealed_ids = _practice_split_ids()
    requested = training_ids + [training_ids[0]]  # exact frozen set + 1 duplicate
    song_ids_path = tmp_path / "song_ids.json"
    song_ids_path.write_text(json.dumps(requested), encoding="utf-8")
    out_path = tmp_path / "out.json"
    rc = elb.main([
        "assemble",
        "--split", "training",
        "--song-ids-json", str(song_ids_path),
        "--intermediates-dir", str(tmp_path),  # empty — no <id>.json present
        "--out", str(out_path),
    ])
    assert rc == 2
    assert not out_path.exists()


def test_harness3b_require_no_duplicate_song_ids_rejects_duplicates() -> None:
    with pytest.raises(elb.ExtractorStopError, match=r"duplicate song_id\(s\).*\['pjs001'\]"):
        elb._require_no_duplicate_song_ids(  # noqa: SLF001
            ["pjs001", "pjs002", "pjs001"], context="assemble",
        )


def test_harness3b_require_no_duplicate_song_ids_happy_path_does_not_raise() -> None:
    elb._require_no_duplicate_song_ids(["pjs001", "pjs002"], context="assemble")  # noqa: SLF001


def test_harness3b_require_exact_frozen_split_membership_error_reports_both_sides() -> None:
    """`_require_exact_frozen_split_membership()` のエラーメッセージが
    missing/unexpected の両方を報告することを直接確認する。"""
    with pytest.raises(elb.ExtractorStopError, match=r"missing=\['pjs002'\].*unexpected=\['pjs003'\]"):
        elb._require_exact_frozen_split_membership(  # noqa: SLF001
            ["pjs001", "pjs003"], ["pjs001", "pjs002"], split="training", context="assemble",
        )


def test_harness3b_require_exact_frozen_split_membership_happy_path_does_not_raise() -> None:
    elb._require_exact_frozen_split_membership(  # noqa: SLF001
        ["pjs002", "pjs001"], ["pjs001", "pjs002"], split="training", context="assemble",
    )


# ---------------------------------------------------------------------------
# PR #329 第2巡 Codex bot レビュー対応: extract_song() 本体へのゲート内蔵
# （P1、採用）。CLI だけでなく関数本体が直接呼び出されても、決定前に
# 拒否することを合成データで確認する。
# ---------------------------------------------------------------------------


def test_harness3b_extract_song_direct_call_rejects_id_outside_frozen_split(
    tmp_path: Path,
) -> None:
    """凍結集合外の song_id を直接 `extract_song()` へ渡すと、対応する
    corpus ディレクトリが一切存在しなくても（＝ファイルアクセス前に）
    拒否される。"""
    with pytest.raises(elb.ExtractorStopError, match="not a member of the pinned"):
        elb.extract_song(
            tmp_path / "pjs999_not_a_real_song", "pjs999_not_a_real_song",
            frozen_split_pins=elb.FrozenSplitPins(training_ids=("pjs001", "pjs002"), validation_ids=()),
            consumed_inputs_pins=elb.ConsumedInputPins(pins={}),
        )


def test_harness3b_extract_song_direct_call_rejects_missing_consumed_input_pin_entry(
    tmp_path: Path,
) -> None:
    """`song_id` が凍結集合内でも、`consumed_inputs_pins` に対応エントリが
    無ければ decode 前に拒否される（pin ファイルの穴に対する二重防御）。"""
    song_dir = tmp_path / "pjs001"
    song_dir.mkdir()
    (song_dir / "pjs001_song.wav").write_bytes(b"x")
    (song_dir / "pjs001.lab").write_bytes(b"x")
    (song_dir / "pjs001.musicxml").write_bytes(b"x")
    with pytest.raises(elb.ExtractorStopError, match="no pinned consumed-input"):
        elb.extract_song(
            song_dir, "pjs001",
            frozen_split_pins=elb.FrozenSplitPins(training_ids=("pjs001",), validation_ids=()),
            consumed_inputs_pins=elb.ConsumedInputPins(pins={}),
        )


def test_harness3b_extract_song_direct_call_rejects_consumed_input_byte_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """消費3入力（lab/musicxml/wav）のいずれかの実バイトが pin と一致し
    なければ decode 前に拒否される——`check_wav_header_or_stop_bytes()`
    （decode 本体側の最初のステップ、PR #329 第3巡レビュー指摘3, P2,
    採用対応で read-once bytes 版へ移行）を monkeypatch して、呼ばれたら
    失敗させることで「pin 照合より先に decode へ到達しない」ことを直接
    確認する。"""
    song_dir = tmp_path / "pjs001"
    song_dir.mkdir()
    (song_dir / "pjs001_song.wav").write_bytes(b"tampered-wav-bytes")
    (song_dir / "pjs001.lab").write_bytes(b"tampered-lab-bytes")
    (song_dir / "pjs001.musicxml").write_bytes(b"tampered-musicxml-bytes")
    consumed_inputs_pins = elb.ConsumedInputPins(pins={
        "pjs001": {"lab_sha256": "0" * 64, "musicxml_sha256": "0" * 64, "wav_sha256": "0" * 64},
    })

    def _boom(*_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover - must not run
        raise AssertionError(
            "check_wav_header_or_stop_bytes() must not run before the consumed-input pin check passes"
        )

    monkeypatch.setattr(elb, "check_wav_header_or_stop_bytes", _boom)
    with pytest.raises(elb.ExtractorStopError, match="実バイト sha256"):
        elb.extract_song(
            song_dir, "pjs001",
            frozen_split_pins=elb.FrozenSplitPins(training_ids=("pjs001",), validation_ids=()),
            consumed_inputs_pins=consumed_inputs_pins,
        )


def test_harness3b_extract_song_direct_call_musicxml_only_tamper_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """musicxml だけが改ざんされ lab/wav は pin と一致するケース ——
    `corpus_identity_hash()` が被覆しない具体的な穴を本 pin が閉じている
    ことの直接証跡。"""
    song_dir = tmp_path / "pjs001"
    song_dir.mkdir()
    wav_bytes = b"real-wav-bytes"
    lab_bytes = b"real-lab-bytes"
    xml_bytes_tampered = b"tampered-musicxml-bytes"
    (song_dir / "pjs001_song.wav").write_bytes(wav_bytes)
    (song_dir / "pjs001.lab").write_bytes(lab_bytes)
    (song_dir / "pjs001.musicxml").write_bytes(xml_bytes_tampered)
    consumed_inputs_pins = elb.ConsumedInputPins(pins={
        "pjs001": {
            "lab_sha256": hashlib.sha256(lab_bytes).hexdigest(),
            "musicxml_sha256": hashlib.sha256(b"real-musicxml-bytes").hexdigest(),
            "wav_sha256": hashlib.sha256(wav_bytes).hexdigest(),
        },
    })

    def _boom(*_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover - must not run
        raise AssertionError(
            "check_wav_header_or_stop_bytes() must not run before the consumed-input pin check passes"
        )

    monkeypatch.setattr(elb, "check_wav_header_or_stop_bytes", _boom)
    with pytest.raises(elb.ExtractorStopError, match="musicxml_sha256"):
        elb.extract_song(
            song_dir, "pjs001",
            frozen_split_pins=elb.FrozenSplitPins(training_ids=("pjs001",), validation_ids=()),
            consumed_inputs_pins=consumed_inputs_pins,
        )


def test_harness3b_extract_song_reads_each_consumed_input_file_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR #329 第3巡レビュー指摘3（P2、採用対応、TOCTOU 閉鎖）: `extract_
    song()` は wav/lab/musicxml をそれぞれ `Path.read_bytes()` で1回だけ
    読む——`Path.read_bytes` をラップして呼び出し回数をファイル単位で
    数え、いずれも高々1回であることを直接確認する（count_mismatch 経路
    ——decode まで到達しない最短経路——で確認: wav decode を経由しない分、
    「1回」の主張がより厳密になる）。"""
    song_dir = tmp_path / "pjs001"
    song_dir.mkdir()
    wav_bytes = b"minimal-wav-bytes"
    lab_bytes = b"0 100 a\n"
    xml_bytes = b"<score-partwise/>"
    (song_dir / "pjs001_song.wav").write_bytes(wav_bytes)
    (song_dir / "pjs001.lab").write_bytes(lab_bytes)
    (song_dir / "pjs001.musicxml").write_bytes(xml_bytes)
    consumed_inputs_pins = elb.ConsumedInputPins(pins={
        "pjs001": {
            "lab_sha256": hashlib.sha256(lab_bytes).hexdigest(),
            "musicxml_sha256": hashlib.sha256(xml_bytes).hexdigest(),
            "wav_sha256": hashlib.sha256(wav_bytes).hexdigest(),
        },
    })

    call_counts: Dict[str, int] = {}
    real_read_bytes = Path.read_bytes

    def _counting_read_bytes(self: Path) -> bytes:
        call_counts[self.name] = call_counts.get(self.name, 0) + 1
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _counting_read_bytes)

    # wav header is deliberately invalid (not RIFF/WAVE), so extract_song()
    # stops at check_wav_header_or_stop_bytes() — before any further wav
    # decode — while still exercising exactly the read-once path for all
    # three consumed inputs (sha match happens first, then header check).
    with pytest.raises(elb.WavHeaderError, match="not a RIFF/WAVE file"):
        elb.extract_song(
            song_dir, "pjs001",
            frozen_split_pins=elb.FrozenSplitPins(training_ids=("pjs001",), validation_ids=()),
            consumed_inputs_pins=consumed_inputs_pins,
        )
    assert call_counts.get("pjs001_song.wav") == 1
    assert call_counts.get("pjs001.lab") == 1
    assert call_counts.get("pjs001.musicxml") == 1


# ---------------------------------------------------------------------------
# PR #329 第2巡 Codex bot レビュー対応: publish_bundle_pair() の2連
# os.replace() 自体の失敗注入（P1、採用）。第1巡修正は staging の失敗の
# みを扱っており、rename（os.replace）自体の失敗は旧世代を復元しなかった
# ——本節は rename 1本目/2本目それぞれの失敗を注入し、いずれも最終状態が
# 「旧世代ペア無傷 + 残骸なし」であることを確認する。
# ---------------------------------------------------------------------------


def test_harness3b_publish_bundle_pair_first_rename_failure_restores_old_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    training_path = tmp_path / "training_bundle.json"
    validation_path = tmp_path / "validation_bundle.json"
    training_path.write_bytes(b'{"gen":"old-training"}\n')
    validation_path.write_bytes(b'{"gen":"old-validation"}\n')

    real_os_replace = os.replace
    # 「1回だけ失敗し、以降は成功する」一時的失敗をシミュレートする——
    # rollback 自身も training_path 宛の os.replace() を使う（backup から
    # の復元）ため、常時失敗にするとロールバック自体も失敗してしまい
    # 「publish 側の rename 失敗」と「ロールバックの検証」を分離できない。
    calls = {"count": 0}

    def _boom(src: Any, dst: Any) -> Any:
        if Path(dst) == training_path and calls["count"] == 0:
            calls["count"] += 1
            raise RuntimeError("synthetic training rename failure")
        return real_os_replace(src, dst)

    monkeypatch.setattr(elb.os, "replace", _boom)

    with pytest.raises(RuntimeError, match="synthetic training rename failure"):
        elb.publish_bundle_pair(
            training_path, b'{"gen":"new-training"}\n',
            validation_path, b'{"gen":"new-validation"}\n',
        )

    assert training_path.read_bytes() == b'{"gen":"old-training"}\n'
    assert validation_path.read_bytes() == b'{"gen":"old-validation"}\n'
    leftovers = sorted(p.name for p in tmp_path.iterdir() if p not in (training_path, validation_path))
    assert leftovers == []


def test_harness3b_publish_bundle_pair_second_rename_failure_restores_old_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1本目（training）の rename が成功した後に2本目（validation）が
    失敗するケース——training だけ新世代・validation は旧世代/欠落という
    混合ペアが観測されないことの直接証跡。"""
    training_path = tmp_path / "training_bundle.json"
    validation_path = tmp_path / "validation_bundle.json"
    training_path.write_bytes(b'{"gen":"old-training"}\n')
    validation_path.write_bytes(b'{"gen":"old-validation"}\n')

    real_os_replace = os.replace
    # 「1回だけ失敗し、以降は成功する」一時的失敗をシミュレートする——
    # rollback 自身も validation_path 宛の os.replace() を使う（backup
    # からの復元）ため、常時失敗にするとロールバック自体も失敗してしまい
    # 「publish 側の rename 失敗」と「ロールバックの検証」を分離できない。
    calls = {"count": 0}

    def _boom(src: Any, dst: Any) -> Any:
        if Path(dst) == validation_path and calls["count"] == 0:
            calls["count"] += 1
            raise RuntimeError("synthetic validation rename failure")
        return real_os_replace(src, dst)

    monkeypatch.setattr(elb.os, "replace", _boom)

    with pytest.raises(RuntimeError, match="synthetic validation rename failure"):
        elb.publish_bundle_pair(
            training_path, b'{"gen":"new-training"}\n',
            validation_path, b'{"gen":"new-validation"}\n',
        )

    assert training_path.read_bytes() == b'{"gen":"old-training"}\n'
    assert validation_path.read_bytes() == b'{"gen":"old-validation"}\n'
    leftovers = sorted(p.name for p in tmp_path.iterdir() if p not in (training_path, validation_path))
    assert leftovers == []


def test_harness3b_publish_bundle_pair_first_rename_failure_no_prior_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    training_path = tmp_path / "training_bundle.json"
    validation_path = tmp_path / "validation_bundle.json"

    real_os_replace = os.replace

    def _boom(src: Any, dst: Any) -> Any:
        if Path(dst) == training_path:
            raise RuntimeError("synthetic training rename failure")
        return real_os_replace(src, dst)

    monkeypatch.setattr(elb.os, "replace", _boom)

    with pytest.raises(RuntimeError, match="synthetic training rename failure"):
        elb.publish_bundle_pair(
            training_path, b'{"gen":"new-training"}\n',
            validation_path, b'{"gen":"new-validation"}\n',
        )

    assert not training_path.exists()
    assert not validation_path.exists()
    assert list(tmp_path.iterdir()) == []


def test_harness3b_publish_bundle_pair_second_rename_failure_no_prior_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    training_path = tmp_path / "training_bundle.json"
    validation_path = tmp_path / "validation_bundle.json"

    real_os_replace = os.replace

    def _boom(src: Any, dst: Any) -> Any:
        if Path(dst) == validation_path:
            raise RuntimeError("synthetic validation rename failure")
        return real_os_replace(src, dst)

    monkeypatch.setattr(elb.os, "replace", _boom)

    with pytest.raises(RuntimeError, match="synthetic validation rename failure"):
        elb.publish_bundle_pair(
            training_path, b'{"gen":"new-training"}\n',
            validation_path, b'{"gen":"new-validation"}\n',
        )

    assert not training_path.exists()
    assert not validation_path.exists()
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# PR #329 第5巡 Codex bot レビュー対応: `_backup_existing()` の失敗（=
# source が元の場所から一切動いていない）と「旧世代なし」の混同（P1、
# 採用）。第3巡の `..._second_backup_failure_restores_first_and_leaves_
# no_debris` はディレクトリ異常系を使っており、`_rollback_to_backup()`
# が誤って呼ぶ `path.unlink()` が `IsADirectoryError`（OSError のサブ
# クラス）で失敗して握りつぶされるため、たまたまバグが顕在化しなかった
# ——本節は通常ファイルを対象に `_backup_existing()` 内部の
# `os.replace(path, backup_path)` そのものを monkeypatch で失敗させ、
# `unlink()` が実際に成功してしまう経路（= バグが顕在化する経路）で
# 「旧公開ペアが両方無傷で残る」ことを確認する。
# ---------------------------------------------------------------------------


def test_harness3b_publish_bundle_pair_first_backup_failure_leaves_old_generation_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """失敗注入回帰テスト（PR #329 第5巡レビュー指摘1 必須テスト）:
    `_backup_existing(training_path)` 内部の `os.replace(path,
    backup_path)` 自体が失敗する（= training_path はまだ元の場所から
    一切動いていない）経路で、`_rollback_to_backup()` が `training_
    backup` の未代入（= 旧実装では `None` のまま）を「旧世代なし」と
    誤解釈して、無傷で残っている旧 training_path を誤って unlink
    **しない**ことを確認する。旧実装ではこの経路で training/validation
    両方の旧公開ファイルが削除されていた——validation は一度も
    `_backup_existing()` すら呼ばれていないにもかかわらず、である
    （training 側の代入未完了により `validation_backup` も初期値
    `None` のまま同じ誤解釈でロールバックされるため）。"""
    training_path = tmp_path / "training_bundle.json"
    validation_path = tmp_path / "validation_bundle.json"
    training_path.write_bytes(b'{"gen":"old-training"}\n')
    validation_path.write_bytes(b'{"gen":"old-validation"}\n')

    real_os_replace = os.replace

    def _boom(src: Any, dst: Any) -> Any:
        if Path(src) == training_path and str(dst).endswith(".prevgen.tmp"):
            raise RuntimeError("synthetic training backup failure")
        return real_os_replace(src, dst)

    monkeypatch.setattr(elb.os, "replace", _boom)

    with pytest.raises(RuntimeError, match="synthetic training backup failure"):
        elb.publish_bundle_pair(
            training_path, b'{"gen":"new-training"}\n',
            validation_path, b'{"gen":"new-validation"}\n',
        )

    # both old-generation files survive untouched: training was never
    # moved (its own backup rename is what failed) and validation was
    # never even reached.
    assert training_path.read_bytes() == b'{"gen":"old-training"}\n'
    assert validation_path.read_bytes() == b'{"gen":"old-validation"}\n'
    leftovers = sorted(p.name for p in tmp_path.iterdir() if p not in (training_path, validation_path))
    assert leftovers == []


def test_harness3b_publish_bundle_pair_second_backup_failure_leaves_old_generation_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """失敗注入回帰テスト（PR #329 第5巡レビュー指摘1 必須テスト）:
    1本目（training）の backup 退避は成功したうえで、2本目
    （validation）の `_backup_existing()` 内部の `os.replace(path,
    backup_path)` 自体が失敗する（= validation_path はまだ元の場所
    から一切動いていない）経路。旧実装では `validation_backup` の
    未代入（= `None` のまま）を「旧世代なし」と誤解釈し、無傷で残って
    いる旧 validation_path を実際に `unlink()` で削除していた
    （通常ファイルを対象にしているため `unlink()` が成功してしまい、
    第3巡のディレクトリ異常系テストとは異なり実際にバグが顕在化する
    経路——第3巡テストは `IsADirectoryError` が握りつぶされて偶然
    バグを検出できていなかった）。"""
    training_path = tmp_path / "training_bundle.json"
    validation_path = tmp_path / "validation_bundle.json"
    training_path.write_bytes(b'{"gen":"old-training"}\n')
    validation_path.write_bytes(b'{"gen":"old-validation"}\n')

    real_os_replace = os.replace

    def _boom(src: Any, dst: Any) -> Any:
        if Path(src) == validation_path and str(dst).endswith(".prevgen.tmp"):
            raise RuntimeError("synthetic validation backup failure")
        return real_os_replace(src, dst)

    monkeypatch.setattr(elb.os, "replace", _boom)

    with pytest.raises(RuntimeError, match="synthetic validation backup failure"):
        elb.publish_bundle_pair(
            training_path, b'{"gen":"new-training"}\n',
            validation_path, b'{"gen":"new-validation"}\n',
        )

    # training is restored from its backup; validation was never moved
    # and must be left untouched — not unlinked.
    assert training_path.read_bytes() == b'{"gen":"old-training"}\n'
    assert validation_path.read_bytes() == b'{"gen":"old-validation"}\n'
    leftovers = sorted(p.name for p in tmp_path.iterdir() if p not in (training_path, validation_path))
    assert leftovers == []


def test_harness3b_publish_bundle_pair_second_backup_failure_restores_first_and_leaves_no_debris(
    tmp_path: Path,
) -> None:
    """PR #329 第3巡レビュー指摘4（P1、採用対応）: `_backup_existing()`
    の2回の呼び出し自体が2連 `os.replace()` と同一の `BaseException`
    ロールバック/cleanup トランザクションに含まれることを、`os.replace()`
    そのものではなく「2本目（validation）の退避（backup）」が失敗する
    経路で確認する——`validation_path` が通常ファイルでなくディレクトリ
    であるケース（`os.replace(directory, existing_file)` は構造的に失敗
    する）。1本目（training）は既に backup 名へ退避成功済みの状態で
    2本目の退避が失敗するため、旧実装（退避2回が `try` の外側）ではこの
    経路がロールバックを一切経由せず「training の最終名が一時的に欠落」
    したまま例外が伝播していた。

    期待する最終状態:
      - `training_path` は公開前と同一バイトのまま存在する（backup から
        の復元、または一度も動かされていない、いずれかの観測結果として
        「無傷」）。
      - `validation_path` は公開前と同じくディレクトリのまま存在する
        （触れられない）。
      - どちらの staging ファイルも `_backup_existing()` が作った空
        プレースホルダも残らない（「残骸なし」）。
    """
    training_path = tmp_path / "training_bundle.json"
    validation_path = tmp_path / "validation_bundle.json"
    training_path.write_bytes(b'{"gen":"old-training"}\n')
    validation_path.mkdir()  # structural anomaly: not a regular file

    with pytest.raises(NotADirectoryError):
        elb.publish_bundle_pair(
            training_path, b'{"gen":"new-training"}\n',
            validation_path, b'{"gen":"new-validation"}\n',
        )

    # training is untouched (rolled back to its pre-publish bytes).
    assert training_path.is_file()
    assert training_path.read_bytes() == b'{"gen":"old-training"}\n'
    # validation is untouched (still the pre-existing directory).
    assert validation_path.is_dir()
    # no debris: exactly training_path (file) + validation_path (dir) remain.
    leftovers = sorted(p.name for p in tmp_path.iterdir() if p not in (training_path, validation_path))
    assert leftovers == []


# ---------------------------------------------------------------------------
# PR #329 第2巡 Codex bot レビュー対応: run_build() の pinned education
# manifest 照合（P1、採用）。実コーパスなしで `_require_bundle_bytes_
# match_pinned_manifest()` を単体検証する（`_tampered_education_manifest_
# contract()` で改変済み合成 manifest + contract を注入）。
# ---------------------------------------------------------------------------


def test_harness3b_require_bundle_bytes_match_pinned_manifest_happy_path(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    fake_training_bytes = b'{"synthetic":"training"}\n'
    fake_validation_bytes = b'{"synthetic":"validation"}\n'
    training_sha = hashlib.sha256(fake_training_bytes).hexdigest()
    validation_sha = hashlib.sha256(fake_validation_bytes).hexdigest()

    def _mutate(data: Dict[str, Any]) -> None:
        data["training_technique_lesson_sha256"] = training_sha
        data["validation_technique_lesson_sha256"] = validation_sha
        for run_key in ("run1_sha256", "run2_sha256", "run3_sha256"):
            data["determinism_evidence"]["training"][run_key] = training_sha
            data["determinism_evidence"]["validation"][run_key] = validation_sha

    _tampered_contract, manifest_path, contract_path = _tampered_education_manifest_contract(
        contract, tmp_path, mutate=_mutate,
    )
    elb._require_bundle_bytes_match_pinned_manifest(  # noqa: SLF001
        training_sha, validation_sha, contract_path=contract_path, manifest_path=manifest_path,
    )  # must not raise


def test_harness3b_require_bundle_bytes_match_pinned_manifest_rejects_mismatch(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    pinned_training_sha = "a" * 64
    pinned_validation_sha = "b" * 64

    def _mutate(data: Dict[str, Any]) -> None:
        data["training_technique_lesson_sha256"] = pinned_training_sha
        data["validation_technique_lesson_sha256"] = pinned_validation_sha
        for run_key in ("run1_sha256", "run2_sha256", "run3_sha256"):
            data["determinism_evidence"]["training"][run_key] = pinned_training_sha
            data["determinism_evidence"]["validation"][run_key] = pinned_validation_sha

    _tampered_contract, manifest_path, contract_path = _tampered_education_manifest_contract(
        contract, tmp_path, mutate=_mutate,
    )
    actual_training_sha = hashlib.sha256(b"drifted-training-bytes").hexdigest()
    actual_validation_sha = pinned_validation_sha  # only training drifts
    with pytest.raises(elb.ExtractorStopError, match="do not match the pinned education lesson"):
        elb._require_bundle_bytes_match_pinned_manifest(  # noqa: SLF001
            actual_training_sha, actual_validation_sha,
            contract_path=contract_path, manifest_path=manifest_path,
        )


def test_harness3b_run_build_allow_unpinned_skips_pinned_manifest_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """`allow_unpinned=True` のとき `_require_bundle_bytes_match_pinned_
    manifest()` が一切呼ばれないことを直接確認する（monkeypatch で
    「呼ばれたら失敗」にする）——真の意味でのバイパス経路であることの
    証跡。実コーパスを使わず `extract_song()`/`freeze_selfcheck()` を
    monkeypatch で置き換え、`publish_bundle_pair()` の実装だけ確認する。
    """

    def _boom(*_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover - must not run
        raise AssertionError("_require_bundle_bytes_match_pinned_manifest() must not run when allow_unpinned=True")

    monkeypatch.setattr(elb, "_require_bundle_bytes_match_pinned_manifest", _boom)
    monkeypatch.setattr(elb, "freeze_selfcheck", lambda *a, **k: {"metric_version": "stub"})

    training_ids = ["pjs001"]
    validation_ids = ["pjs002"]

    def _fake_load_training_validation_ids(*_a: Any, **_k: Any) -> Tuple[List[str], List[str]]:
        return training_ids, validation_ids

    monkeypatch.setattr(elb, "load_training_validation_ids", _fake_load_training_validation_ids)
    monkeypatch.setattr(elb, "load_consumed_inputs_pins", lambda *a, **k: {})

    def _fake_extract_song(song_dir: Path, song_id: str, **_kwargs: Any) -> Dict[str, Any]:
        return _synthetic_song(song_id)

    monkeypatch.setattr(elb, "extract_song", _fake_extract_song)
    monkeypatch.setattr(elb, "sha256_of_file", lambda p: "d" * 64)

    out_dir = tmp_path / "out"
    result = elb.run_build(
        corpus_root=tmp_path / "corpus",
        out_dir=out_dir,
        allow_unpinned=True,
    )
    assert result["pinned_manifest_check"] == "SKIPPED_UNPINNED"
    assert (out_dir / "training_bundle.json").exists()
    assert (out_dir / "validation_bundle.json").exists()


# ---------------------------------------------------------------------------
# PR #329 第2巡 Codex bot レビュー対応: musicxml を含む消費3入力の per-file
# sha256 pin（`pjs_consumed_inputs_sha256.json`）の schema/loader（P1、
# 採用）。
# ---------------------------------------------------------------------------


def _consumed_inputs_manifest_data() -> Dict[str, Any]:
    return json.loads(m.PJS_CONSUMED_INPUTS_MANIFEST_PATH.read_text(encoding="utf-8"))


def test_harness3b_consumed_inputs_manifest_covers_exactly_85_songs_and_excludes_sealed() -> None:
    data = _consumed_inputs_manifest_data()
    assert data["schema"] == m.SCHEMA_PJS_CONSUMED_INPUTS_MANIFEST
    assert data["sealed_holdout_excluded"] is True
    assert data["song_count"] == 85
    assert len(data["songs"]) == 85
    training_ids, validation_ids, sealed_ids = _practice_split_ids()
    assert set(data["songs"]) == set(training_ids) | set(validation_ids)
    assert set(data["songs"]).isdisjoint(sealed_ids)


def test_harness3b_validate_pjs_consumed_inputs_manifest_passes_on_real_manifest() -> None:
    data = _consumed_inputs_manifest_data()
    m.validate_pjs_consumed_inputs_manifest(data)  # must not raise


def test_harness3b_load_pinned_consumed_inputs_manifest_happy_path(
    contract: m.Run9RunContract,
) -> None:
    data = m.load_pinned_consumed_inputs_manifest(contract)
    assert len(data["songs"]) == 85
    assert set(data["songs"]["pjs001"].keys()) == {"lab_sha256", "musicxml_sha256", "wav_sha256"}


def test_harness3b_load_pinned_consumed_inputs_manifest_rejects_byte_tampering(
    tmp_path: Path,
) -> None:
    tampered_path = tmp_path / "pjs_consumed_inputs_sha256.json"
    tampered_path.write_bytes(m.PJS_CONSUMED_INPUTS_MANIFEST_PATH.read_bytes() + b"\n")
    with pytest.raises(m.Run9ValidationError, match="実バイト sha256"):
        elb.load_consumed_inputs_pins(tampered_path)


def _tampered_consumed_inputs_manifest_contract(
    contract: m.Run9RunContract, tmp_path: Path, *, mutate,
) -> Tuple[Path, Path]:
    data = copy.deepcopy(_consumed_inputs_manifest_data())
    mutate(data)
    manifest_bytes = _canonical_json_bytes(data)
    manifest_path = tmp_path / "pjs_consumed_inputs_sha256.json"
    manifest_path.write_bytes(manifest_bytes)
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    tampered_raw = copy.deepcopy(contract.raw)
    tampered_raw["pjs_consumed_inputs_manifest_sha"] = {"value": manifest_sha, "status": "PINNED"}
    tampered_contract_path = tmp_path / "RUN9_CONTRACT.yaml"
    tampered_contract_path.write_text(yaml.safe_dump(tampered_raw, allow_unicode=True), encoding="utf-8")
    return manifest_path, tampered_contract_path


def test_harness3b_consumed_inputs_manifest_rejects_single_song_sha_tampering(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """pin ファイル側の1曲1ファイルの sha を改ざんしても構造検証は通る
    （値整形式は正しいまま）——実際の突合は builder 側
    `_require_consumed_input_bytes_match()` の責務であることの確認
    （構造検証と実バイト照合は別レイヤ）。"""

    def _mutate(data: Dict[str, Any]) -> None:
        any_song = next(iter(data["songs"]))
        data["songs"][any_song]["musicxml_sha256"] = "f" * 64

    manifest_path, contract_path = _tampered_consumed_inputs_manifest_contract(
        contract, tmp_path, mutate=_mutate,
    )
    tampered_contract = m.load_run9_contract_from_yaml_path(contract_path)
    data = m.load_pinned_consumed_inputs_manifest(
        tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
    )  # must not raise — structurally valid (still 64hex)
    assert len(data["songs"]) == 85


def test_harness3b_consumed_inputs_manifest_rejects_wrong_song_count(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    def _mutate(data: Dict[str, Any]) -> None:
        any_song = next(iter(data["songs"]))
        del data["songs"][any_song]
        data["song_count"] = 84

    manifest_path, contract_path = _tampered_consumed_inputs_manifest_contract(
        contract, tmp_path, mutate=_mutate,
    )
    tampered_contract = m.load_run9_contract_from_yaml_path(contract_path)
    with pytest.raises(m.Run9ValidationError, match="song_count must be exactly 85"):
        m.load_pinned_consumed_inputs_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_harness3b_consumed_inputs_manifest_rejects_sealed_holdout_excluded_false(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    def _mutate(data: Dict[str, Any]) -> None:
        data["sealed_holdout_excluded"] = False

    manifest_path, contract_path = _tampered_consumed_inputs_manifest_contract(
        contract, tmp_path, mutate=_mutate,
    )
    tampered_contract = m.load_run9_contract_from_yaml_path(contract_path)
    with pytest.raises(m.Run9ValidationError, match="sealed_holdout_excluded must be exactly True"):
        m.load_pinned_consumed_inputs_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_harness3b_education_manifest_corpus_provenance_cross_check_rejects_tampered_pin(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """education manifest の `corpus_provenance.consumed_inputs_manifest_
    sha256` が実ファイルと一致しなければ `load_pinned_education_lesson_
    manifest()` が拒否することを確認する（PR #329 第2巡レビュー指摘2-4
    採用対応の cross-check、新設）。"""

    def _mutate(data: Dict[str, Any]) -> None:
        data["corpus_provenance"]["consumed_inputs_manifest_sha256"] = "0" * 64

    _tampered_contract, manifest_path, contract_path = _tampered_education_manifest_contract(
        contract, tmp_path, mutate=_mutate,
    )
    tampered_contract = m.load_run9_contract_from_yaml_path(contract_path)
    with pytest.raises(m.Run9ValidationError, match="consumed_inputs_manifest_sha256"):
        m.load_pinned_education_lesson_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


# ---------------------------------------------------------------------------
# PR #329 第4巡 Codex bot レビュー対応（採用2件, P1）:
#   (1) extract-song/assemble/probe-header の `--out` corpus-alias 拒否
#   (2) assemble 経路の pinned education manifest 照合
# ---------------------------------------------------------------------------


def test_harness3b_extract_song_cli_rejects_out_aliasing_corpus_wav(tmp_path: Path) -> None:
    """`--out` が対象曲の WAV を直接指す場合、decode/書き込み前に拒否され、
    corpus 入力の実バイトは無傷のまま残る。"""
    song_id = "pjs001"
    song_dir = tmp_path / song_id
    song_dir.mkdir()
    wav_path = song_dir / f"{song_id}_song.wav"
    wav_path.write_bytes(b"pretend-wav-bytes")
    rc = elb.main([
        "extract-song",
        "--corpus-root", str(tmp_path),
        "--song-id", song_id,
        "--out", str(wav_path),
    ])
    assert rc == 2
    assert wav_path.read_bytes() == b"pretend-wav-bytes"


def test_harness3b_extract_song_cli_rejects_out_aliasing_corpus_wav_via_symlink(
    tmp_path: Path,
) -> None:
    """`--out` が対象曲の WAV への symlink を指す場合も、同一実体として
    拒否される（直接指定と同型の防御）。"""
    song_id = "pjs001"
    song_dir = tmp_path / song_id
    song_dir.mkdir()
    wav_path = song_dir / f"{song_id}_song.wav"
    wav_path.write_bytes(b"pretend-wav-bytes")
    alias_out = tmp_path / "alias_out.json"
    alias_out.symlink_to(wav_path)
    rc = elb.main([
        "extract-song",
        "--corpus-root", str(tmp_path),
        "--song-id", song_id,
        "--out", str(alias_out),
    ])
    assert rc == 2
    assert wav_path.read_bytes() == b"pretend-wav-bytes"


def test_harness3b_extract_song_cli_rejects_out_aliasing_corpus_wav_via_hardlink(
    tmp_path: Path,
) -> None:
    """PR #329 第7巡レビュー指摘（採用1, P1）: `--out` が対象曲の WAV への
    **既存ハードリンク**（symlink ではなく、同一 inode を指す独立した
    ディレクトリエントリ）を指す場合も、decode/書き込み前に拒否され、
    corpus 入力の実バイトは無傷のまま残る。ハードリンクは symlink と違い
    `Path.resolve()` で追跡されるパス参照ではない（複数ディレクトリ
    エントリが同一 inode を対等に指す構造そのもの）ため、旧実装の
    lexical/resolved パス比較だけでは検出できなかった alias 種別
    ——`os.path.samefile()` による inode 単位の照合（`_hardlink_alias_
    conflict()`）で検出する。"""
    song_id = "pjs001"
    song_dir = tmp_path / song_id
    song_dir.mkdir()
    wav_path = song_dir / f"{song_id}_song.wav"
    wav_path.write_bytes(b"pretend-wav-bytes")
    hardlink_out = tmp_path / "hardlink_out.json"
    os.link(wav_path, hardlink_out)
    rc = elb.main([
        "extract-song",
        "--corpus-root", str(tmp_path),
        "--song-id", song_id,
        "--out", str(hardlink_out),
    ])
    assert rc == 2
    assert wav_path.read_bytes() == b"pretend-wav-bytes"
    assert hardlink_out.read_bytes() == b"pretend-wav-bytes"


def test_harness3b_probe_header_cli_rejects_out_aliasing_corpus_wav_via_hardlink(
    tmp_path: Path,
) -> None:
    """PR #329 第7巡レビュー指摘（採用1, P1）: `probe-header` 版の同型
    ケース——`--out` が対象曲の WAV への既存ハードリンクを指す場合も、
    いずれの WAV も open する前に拒否される。"""
    training_ids, _validation_ids, _sealed_ids = _practice_split_ids()
    song_id = training_ids[0]
    song_dir = tmp_path / song_id
    song_dir.mkdir()
    wav_path = song_dir / f"{song_id}_song.wav"
    wav_path.write_bytes(b"pretend-wav-bytes")
    hardlink_out = tmp_path / "hardlink_out.json"
    os.link(wav_path, hardlink_out)
    rc = elb.main([
        "probe-header",
        "--corpus-root", str(tmp_path),
        "--song-ids", song_id,
        "--out", str(hardlink_out),
    ])
    assert rc == 2
    assert wav_path.read_bytes() == b"pretend-wav-bytes"
    assert hardlink_out.read_bytes() == b"pretend-wav-bytes"


def test_harness3b_resolve_output_alias_conflict_hardlink_stat_failure_policy(
    tmp_path: Path,
) -> None:
    """`_hardlink_alias_conflict()` の stat 失敗規約を直接単体で確認する
    （PR #329 第7巡レビュー指摘, 採用1, P1）:
    (1) `out_path` が存在しなければ hardlink 照合はスキップされ `None`
        （＝これから新規作成されるファイルは何の inode も指さない）。
    (2) `protected` の一部が存在しなくても、他の existing protected との
        照合は継続される（1件の不在で全件スキップしない）。
    (3) 実際にハードリンクされている場合は同一 inode として検出される。
    """
    protected_a = tmp_path / "protected_a.bin"
    protected_a.write_bytes(b"a")
    missing_protected = tmp_path / "does_not_exist.bin"
    out_new = tmp_path / "new_out.json"  # not created — brand-new output path

    # (1) out_path 未存在 → hardlink 照合スキップ、lexical/resolved も
    #     不一致なので全体として None。
    assert elb._resolve_output_alias_conflict(  # noqa: SLF001
        out_new, [protected_a, missing_protected],
    ) is None

    # (2)+(3) out が protected_a への既存ハードリンクなら、missing_
    #     protected が先に照合されて存在しなくても protected_a との一致で
    #     検出される（走査順に依存しないことの確認として両順序を試す）。
    hardlinked_out = tmp_path / "hardlinked_out.json"
    os.link(protected_a, hardlinked_out)
    assert elb._resolve_output_alias_conflict(  # noqa: SLF001
        hardlinked_out, [missing_protected, protected_a],
    ) == protected_a
    assert elb._resolve_output_alias_conflict(  # noqa: SLF001
        hardlinked_out, [protected_a, missing_protected],
    ) == protected_a

    # missing_protected 単体との照合では衝突なし（存在しないファイルへの
    # ハードリンクはあり得ない）。
    assert elb._resolve_output_alias_conflict(  # noqa: SLF001
        hardlinked_out, [missing_protected],
    ) is None


def test_harness3b_extract_song_cli_rejects_out_aliasing_split_manifest(tmp_path: Path) -> None:
    """`--out` が消費3入力に限らず、repo 正典 split manifest
    （`elb.DEFAULT_SPLIT_MANIFEST_PATH`）と同一実体を指す場合も拒否される
    （保護対象集合が corpus 入力だけでなく split manifest/contract/
    consumed-inputs pin/freeze record/spec にも及ぶことの直接証跡）。
    PR #329 第6巡レビュー指摘（P1、採用対応）により `extract-song` は
    `--split-manifest` を CLI から受け付けなくなった——CLI は常に repo
    正典 split manifest を読むため、保護対象も常にその実ファイルである
    ことを直接指定して確認する（`inputs/education_technique_lesson_
    manifest.json`/裁定 txt/freeze record を直接指す既存テストと同型）。
    """
    rc = elb.main([
        "extract-song",
        "--corpus-root", str(tmp_path),
        "--song-id", "pjs001",
        "--out", str(elb.DEFAULT_SPLIT_MANIFEST_PATH),
    ])
    assert rc == 2
    assert elb.DEFAULT_SPLIT_MANIFEST_PATH.read_bytes() == m.PRACTICE_MANIFEST_PATH.read_bytes()


def test_harness3b_probe_header_cli_rejects_out_aliasing_corpus_wav(tmp_path: Path) -> None:
    """`--out` が `--song-ids` 対象曲の WAV を直接指す場合、いずれの WAV も
    open する前に拒否される。"""
    training_ids, _validation_ids, _sealed_ids = _practice_split_ids()
    song_id = training_ids[0]
    song_dir = tmp_path / song_id
    song_dir.mkdir()
    wav_path = song_dir / f"{song_id}_song.wav"
    wav_path.write_bytes(b"pretend-wav-bytes")
    rc = elb.main([
        "probe-header",
        "--corpus-root", str(tmp_path),
        "--song-ids", song_id,
        "--out", str(wav_path),
    ])
    assert rc == 2
    assert wav_path.read_bytes() == b"pretend-wav-bytes"


def test_harness3b_assemble_cli_rejects_out_aliasing_intermediate_json(tmp_path: Path) -> None:
    """`--out` が選択 split の中間物 JSON（1件）と同一実体を指す場合、
    中間物を1つも読む前に拒否される（assemble 出力の同型ケース）。"""
    training_ids, _validation_ids, _sealed_ids = _practice_split_ids()
    song_ids_path = tmp_path / "song_ids.json"
    song_ids_path.write_text(json.dumps(training_ids), encoding="utf-8")
    intermediate_path = tmp_path / f"{training_ids[0]}.json"
    intermediate_path.write_text(
        json.dumps({"song_id": training_ids[0], "sentinel": "do-not-overwrite"}), encoding="utf-8",
    )
    rc = elb.main([
        "assemble",
        "--split", "training",
        "--song-ids-json", str(song_ids_path),
        "--intermediates-dir", str(tmp_path),
        "--out", str(intermediate_path),
    ])
    assert rc == 2
    assert json.loads(intermediate_path.read_text(encoding="utf-8"))["sentinel"] == "do-not-overwrite"


# ---------------------------------------------------------------------------
# PR #329 第5巡 Codex bot レビュー対応（採用1件, P1）:
#   assemble/extract-song/probe-header の保護パス集合に、pinned education
#   lesson manifest 検証が実際に読む cross-check closure（manifest 本体 +
#   builder/裁定 txt/freeze record 2本/detail record/consumed-inputs pin）
#   を追加する。旧実装ではこの closure が保護集合から丸ごと欠落しており、
#   `--out inputs/education_technique_lesson_manifest.json` 等を指定すると
#   検証成功後に `write_bundle_bytes()` がその pin 済みファイルをバンドル
#   JSON で上書きし得た。
#
# 以下のテストは `--out` に実リポジトリの pin 済みファイルを直接指定する
# ——alias preflight は `_cmd_assemble` 内で split membership 検証の直後、
# 中間物 JSON を1つも読む前に実行されるため、preflight が正しく拒否する
# 限り本テストが実リポジトリファイルへ書き込むことは無い。preflight が
# もし拒否し損なっても、`--intermediates-dir` には実在しない中間物 JSON
# しか無いため、後続の読み取りが `FileNotFoundError` で先に失敗し、
# `write_bundle_bytes()` へは到達しない（実ファイル破壊のリスクなしに
# 「保護されているか」を検証できる構成）。
# ---------------------------------------------------------------------------


def test_harness3b_assemble_cli_rejects_out_aliasing_pinned_education_manifest(
    tmp_path: Path,
) -> None:
    """`--out` が pin 済み education lesson manifest 本体
    （`inputs/education_technique_lesson_manifest.json`）を直接指す場合、
    中間物を1つも読む前に拒否され、manifest の実バイトは無傷のまま残る。"""
    training_ids, _validation_ids, _sealed_ids = _practice_split_ids()
    song_ids_path = tmp_path / "song_ids.json"
    song_ids_path.write_text(json.dumps(training_ids), encoding="utf-8")
    before = elb.DEFAULT_EDUCATION_MANIFEST_PATH.read_bytes()
    rc = elb.main([
        "assemble",
        "--split", "training",
        "--song-ids-json", str(song_ids_path),
        "--intermediates-dir", str(tmp_path / "does_not_exist"),
        "--out", str(elb.DEFAULT_EDUCATION_MANIFEST_PATH),
    ])
    assert rc == 2
    assert elb.DEFAULT_EDUCATION_MANIFEST_PATH.read_bytes() == before


def test_harness3b_assemble_cli_rejects_out_aliasing_adjudication_basis(tmp_path: Path) -> None:
    """`--out` が裁定文書
    （`USER_ADJUDICATION_20260827_PJS_LESSON_FREEZE.txt`、pinned education
    lesson manifest の cross-check (a) 対象）を直接指す場合も同様に拒否
    される。"""
    training_ids, _validation_ids, _sealed_ids = _practice_split_ids()
    song_ids_path = tmp_path / "song_ids.json"
    song_ids_path.write_text(json.dumps(training_ids), encoding="utf-8")
    before = elb.DEFAULT_ADJUDICATION_BASIS_PATH.read_bytes()
    rc = elb.main([
        "assemble",
        "--split", "training",
        "--song-ids-json", str(song_ids_path),
        "--intermediates-dir", str(tmp_path / "does_not_exist"),
        "--out", str(elb.DEFAULT_ADJUDICATION_BASIS_PATH),
    ])
    assert rc == 2
    assert elb.DEFAULT_ADJUDICATION_BASIS_PATH.read_bytes() == before


def test_harness3b_assemble_cli_rejects_out_aliasing_freeze_record(tmp_path: Path) -> None:
    """`--out` が freeze record（`inputs/h3b_freeze_record.json`、pinned
    education lesson manifest の cross-check (d) 対象）を直接指す場合も
    同様に拒否される。"""
    training_ids, _validation_ids, _sealed_ids = _practice_split_ids()
    song_ids_path = tmp_path / "song_ids.json"
    song_ids_path.write_text(json.dumps(training_ids), encoding="utf-8")
    before = elb.DEFAULT_FREEZE_RECORD_PATH.read_bytes()
    rc = elb.main([
        "assemble",
        "--split", "training",
        "--song-ids-json", str(song_ids_path),
        "--intermediates-dir", str(tmp_path / "does_not_exist"),
        "--out", str(elb.DEFAULT_FREEZE_RECORD_PATH),
    ])
    assert rc == 2
    assert elb.DEFAULT_FREEZE_RECORD_PATH.read_bytes() == before


def test_harness3b_extract_song_protected_paths_include_pinned_education_manifest_closure(
) -> None:
    """`_extract_song_protected_paths()` が pinned education lesson
    manifest cross-check closure 全体（manifest 本体・裁定 txt・builder
    本体・spec・freeze record 現行/superseded・detail record・
    consumed-inputs pin）を含むことを直接確認する（第5巡レビュー指摘2、
    採用対応。extract-song 自身はこの closure を読まないが、保護集合への
    一貫した包含を保証する構造であることの直接証跡）。PR #329 第6巡
    レビュー指摘（P1、採用対応）以降 `split_manifest`/`contract_path`/
    `consumed_inputs_manifest` は CLI 属性として存在しない（保護対象は
    常に repo 正典パスへ固定済み）ため、`args` には含めない。"""
    args = argparse.Namespace(
        corpus_root="/tmp/does-not-matter", song_id="pjs001",
        freeze_record=None, spec_path=None,
    )
    protected = elb._extract_song_protected_paths(args)  # noqa: SLF001
    assert elb.DEFAULT_EDUCATION_MANIFEST_PATH in protected
    assert elb.DEFAULT_ADJUDICATION_BASIS_PATH in protected
    assert elb._THIS_MODULE_PATH in protected  # noqa: SLF001
    assert elb.DEFAULT_SUPERSEDED_FREEZE_RECORD_PATH in protected
    assert elb.DEFAULT_DETAIL_RECORD_PATH in protected


def test_harness3b_probe_header_protected_paths_include_pinned_education_manifest_closure(
) -> None:
    """同上、`_probe_header_protected_paths()` 版（第5巡レビュー指摘2、
    採用対応）。PR #329 第6巡レビュー指摘（P1、採用対応）以降
    `split_manifest`/`contract_path` は CLI 属性として存在しない
    （保護対象は常に repo 正典パスへ固定済み）ため、`args` には含めない。
    """
    args = argparse.Namespace(corpus_root="/tmp/does-not-matter", song_ids=["pjs001"])
    protected = elb._probe_header_protected_paths(args)  # noqa: SLF001
    assert elb.DEFAULT_EDUCATION_MANIFEST_PATH in protected
    assert elb.DEFAULT_ADJUDICATION_BASIS_PATH in protected
    assert elb._THIS_MODULE_PATH in protected  # noqa: SLF001
    assert elb.DEFAULT_SUPERSEDED_FREEZE_RECORD_PATH in protected
    assert elb.DEFAULT_DETAIL_RECORD_PATH in protected


def test_harness3b_require_single_split_bundle_bytes_match_pinned_manifest_happy_path(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    fake_training_bytes = b'{"synthetic":"training"}\n'
    training_sha = hashlib.sha256(fake_training_bytes).hexdigest()

    def _mutate(data: Dict[str, Any]) -> None:
        data["training_technique_lesson_sha256"] = training_sha
        for run_key in ("run1_sha256", "run2_sha256", "run3_sha256"):
            data["determinism_evidence"]["training"][run_key] = training_sha

    _tampered_contract, manifest_path, contract_path = _tampered_education_manifest_contract(
        contract, tmp_path, mutate=_mutate,
    )
    elb._require_single_split_bundle_bytes_match_pinned_manifest(  # noqa: SLF001
        "training", training_sha, contract_path=contract_path, manifest_path=manifest_path,
    )  # must not raise


def test_harness3b_require_single_split_bundle_bytes_match_pinned_manifest_rejects_mismatch(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    pinned_validation_sha = "b" * 64

    def _mutate(data: Dict[str, Any]) -> None:
        data["validation_technique_lesson_sha256"] = pinned_validation_sha
        for run_key in ("run1_sha256", "run2_sha256", "run3_sha256"):
            data["determinism_evidence"]["validation"][run_key] = pinned_validation_sha

    _tampered_contract, manifest_path, contract_path = _tampered_education_manifest_contract(
        contract, tmp_path, mutate=_mutate,
    )
    actual_validation_sha = hashlib.sha256(b"drifted-validation-bytes").hexdigest()
    with pytest.raises(elb.ExtractorStopError, match="do not match the pinned education lesson"):
        elb._require_single_split_bundle_bytes_match_pinned_manifest(  # noqa: SLF001
            "validation", actual_validation_sha, contract_path=contract_path, manifest_path=manifest_path,
        )


def test_harness3b_assemble_cli_rejects_bundle_bytes_not_matching_pinned_manifest_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """assemble は pinned education manifest と照合せず canonical 形式で
    成功出力し得た（第4巡指摘）——合成中間物（実 PJS バンドルとは一致
    しない）を渡した `assemble` が既定（`--allow-unpinned` 省略）では
    publish されずに拒否されることを直接確認する。"""
    training_ids = ["pjs001", "pjs002"]

    def _fake_load_training_validation_ids(*_a: Any, **_k: Any) -> elb.FrozenSplitPins:
        return elb.FrozenSplitPins(training_ids=tuple(training_ids), validation_ids=())

    monkeypatch.setattr(elb, "load_training_validation_ids", _fake_load_training_validation_ids)

    for sid in training_ids:
        (tmp_path / f"{sid}.json").write_text(json.dumps(_synthetic_song(sid)), encoding="utf-8")
    song_ids_path = tmp_path / "song_ids.json"
    song_ids_path.write_text(json.dumps(training_ids), encoding="utf-8")
    out_path = tmp_path / "out.json"

    rc = elb.main([
        "assemble",
        "--split", "training",
        "--song-ids-json", str(song_ids_path),
        "--intermediates-dir", str(tmp_path),
        "--out", str(out_path),
    ])
    assert rc == 2
    assert not out_path.exists()


def test_harness3b_assemble_cli_allow_unpinned_skips_pinned_manifest_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`assemble --allow-unpinned` は `_require_single_split_bundle_bytes_
    match_pinned_manifest()` を一切呼ばずに publish する——`run_build()`
    の `--allow-unpinned` と同型のエスケープハッチであることを直接確認
    する。"""

    def _boom(*_a: Any, **_k: Any) -> Any:  # pragma: no cover - must not run
        raise AssertionError(
            "_require_single_split_bundle_bytes_match_pinned_manifest() must not run when "
            "--allow-unpinned is set"
        )

    monkeypatch.setattr(elb, "_require_single_split_bundle_bytes_match_pinned_manifest", _boom)

    training_ids = ["pjs001", "pjs002"]

    def _fake_load_training_validation_ids(*_a: Any, **_k: Any) -> elb.FrozenSplitPins:
        return elb.FrozenSplitPins(training_ids=tuple(training_ids), validation_ids=())

    monkeypatch.setattr(elb, "load_training_validation_ids", _fake_load_training_validation_ids)

    for sid in training_ids:
        (tmp_path / f"{sid}.json").write_text(json.dumps(_synthetic_song(sid)), encoding="utf-8")
    song_ids_path = tmp_path / "song_ids.json"
    song_ids_path.write_text(json.dumps(training_ids), encoding="utf-8")
    out_path = tmp_path / "out.json"

    rc = elb.main([
        "assemble",
        "--split", "training",
        "--song-ids-json", str(song_ids_path),
        "--intermediates-dir", str(tmp_path),
        "--out", str(out_path),
        "--allow-unpinned",
    ])
    assert rc == 0
    assert out_path.exists()


# ---------------------------------------------------------------------------
# PR #329 第6巡 Codex bot レビュー対応（採用1件, P1）: 公開 CLI の
# split/contract/pin 参照を repo 正典へ固定する——`probe-header`/
# `extract-song`/`assemble`/`build` の全サブコマンドから `--contract-path`/
# `--split-manifest`/`--consumed-inputs-manifest` を撤去し、CLI 経路は常に
# repo 正典パスで検証させる（呼び出し元が contract のコピーを改変して渡す
# ことで凍結境界を再定義できた穴を閉じる）。以下2グループのテスト:
#   (a) CLI がこれらのオプションを一切受け付けないこと（argparse エラー）
#   (b) CLI 経路が実際に repo 正典パスで検証していることの直接証跡
#       （下位 loader を monkeypatch で観測——呼び出し引数が常に `None`
#       = 正典パスであることを確認する）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "removed_flag"),
    [
        (
            ["probe-header", "--corpus-root", "/tmp", "--song-ids", "pjs001", "--out", "/tmp/out.json"],
            "--contract-path",
        ),
        (
            ["probe-header", "--corpus-root", "/tmp", "--song-ids", "pjs001", "--out", "/tmp/out.json"],
            "--split-manifest",
        ),
        (
            ["extract-song", "--corpus-root", "/tmp", "--song-id", "pjs001", "--out", "/tmp/out.json"],
            "--contract-path",
        ),
        (
            ["extract-song", "--corpus-root", "/tmp", "--song-id", "pjs001", "--out", "/tmp/out.json"],
            "--split-manifest",
        ),
        (
            ["extract-song", "--corpus-root", "/tmp", "--song-id", "pjs001", "--out", "/tmp/out.json"],
            "--consumed-inputs-manifest",
        ),
        (
            [
                "assemble", "--split", "training", "--song-ids-json", "/tmp/s.json",
                "--intermediates-dir", "/tmp", "--out", "/tmp/out.json",
            ],
            "--contract-path",
        ),
        (
            [
                "assemble", "--split", "training", "--song-ids-json", "/tmp/s.json",
                "--intermediates-dir", "/tmp", "--out", "/tmp/out.json",
            ],
            "--split-manifest",
        ),
        (["build", "--corpus-root", "/tmp", "--out-dir", "/tmp/out"], "--contract-path"),
        (["build", "--corpus-root", "/tmp", "--out-dir", "/tmp/out"], "--split-manifest"),
        (["build", "--corpus-root", "/tmp", "--out-dir", "/tmp/out"], "--consumed-inputs-manifest"),
    ],
)
def test_harness3b_cli_rejects_removed_contract_split_pin_path_options(
    argv: List[str], removed_flag: str, capsys: pytest.CaptureFixture[str],
) -> None:
    """(a) `--contract-path`/`--split-manifest`/`--consumed-inputs-manifest`
    は `probe-header`/`extract-song`/`assemble`/`build` のいずれの
    サブコマンドからも撤去されている——渡すと argparse が「unrecognized
    arguments」で拒否し、終了コード2で `SystemExit` する（`main()` の
    `try/except`（`ExtractorStopError`/`Run9ValidationError` のみを捕捉）
    より前段の argparse 自身のエラー経路であり、コード記述なしの CLI
    操作で contract/split-manifest/consumed-inputs pin の参照先を差し
    替える迂回が構造的に成立しないことの直接証跡）。"""
    full_argv = argv + [removed_flag, "/tmp/does-not-matter"]
    with pytest.raises(SystemExit) as excinfo:
        elb.main(full_argv)
    assert excinfo.value.code == 2
    stderr = capsys.readouterr().err
    assert "unrecognized arguments" in stderr
    assert removed_flag in stderr


def _spy_and_short_circuit(
    monkeypatch: pytest.MonkeyPatch, target: Any, name: str, calls: List[Tuple[Tuple[Any, ...], Dict[str, Any]]],
) -> None:
    """`target.name` を、呼び出し引数を `calls` へ記録したうえで
    `ExtractorStopError` を送出して短絡させるスパイへ差し替える——本体
    ロジック（実 corpus 読み取り・decode）へ一切到達させずに「CLI が
    どの引数でこの loader を呼んだか」だけを観測するためのヘルパー。"""

    def _spy(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        raise elb.ExtractorStopError(f"{name}() spy short-circuit for CLI wiring test")

    monkeypatch.setattr(target, name, _spy)


def test_harness3b_probe_header_cli_calls_load_training_validation_ids_with_canonical_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(b) `probe-header` CLI 経路は `load_training_validation_ids()` を
    常に `(None, contract_path=None)`（= repo 正典パスへ解決される既定値）
    で呼ぶ——`--split-manifest`/`--contract-path` で差し替える手段が
    存在しないことの直接証跡。"""
    calls: List[Tuple[Tuple[Any, ...], Dict[str, Any]]] = []
    _spy_and_short_circuit(monkeypatch, elb, "load_training_validation_ids", calls)
    rc = elb.main([
        "probe-header",
        "--corpus-root", str(tmp_path),
        "--song-ids", "pjs001",
        "--out", str(tmp_path / "out.json"),
    ])
    assert rc == 2
    assert calls == [((None,), {"contract_path": None})]


def test_harness3b_extract_song_cli_calls_load_training_validation_ids_with_canonical_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(b) 同上、`extract-song` 版。"""
    calls: List[Tuple[Tuple[Any, ...], Dict[str, Any]]] = []
    _spy_and_short_circuit(monkeypatch, elb, "load_training_validation_ids", calls)
    rc = elb.main([
        "extract-song",
        "--corpus-root", str(tmp_path),
        "--song-id", "pjs001",
        "--out", str(tmp_path / "out.json"),
    ])
    assert rc == 2
    assert calls == [((None,), {"contract_path": None})]


def test_harness3b_assemble_cli_calls_load_training_validation_ids_with_canonical_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(b) 同上、`assemble` 版。"""
    calls: List[Tuple[Tuple[Any, ...], Dict[str, Any]]] = []
    _spy_and_short_circuit(monkeypatch, elb, "load_training_validation_ids", calls)
    song_ids_path = tmp_path / "song_ids.json"
    song_ids_path.write_text(json.dumps(["pjs001"]), encoding="utf-8")
    rc = elb.main([
        "assemble",
        "--split", "training",
        "--song-ids-json", str(song_ids_path),
        "--intermediates-dir", str(tmp_path),
        "--out", str(tmp_path / "out.json"),
    ])
    assert rc == 2
    assert calls == [((None,), {"contract_path": None})]


def test_harness3b_build_cli_calls_load_training_validation_ids_with_canonical_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(b) 同上、`build` 版。"""
    calls: List[Tuple[Tuple[Any, ...], Dict[str, Any]]] = []
    _spy_and_short_circuit(monkeypatch, elb, "load_training_validation_ids", calls)
    out_dir = tmp_path / "out"
    rc = elb.main([
        "build",
        "--corpus-root", str(tmp_path / "corpus"),
        "--out-dir", str(out_dir),
    ])
    assert rc == 2
    assert calls == [((None,), {"contract_path": None})]


def test_harness3b_extract_song_cli_calls_load_consumed_inputs_pins_with_canonical_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(b) `extract-song` CLI 経路は `load_consumed_inputs_pins()` も
    常に `(None, contract_path=None)` で呼ぶ——`--consumed-inputs-manifest`
    で差し替える手段が存在しないことの直接証跡。`load_training_
    validation_ids()` は monkeypatch せず実行させる（正典 split で
    `--song-id` を検証させ、`load_consumed_inputs_pins()` まで到達させる
    ため）。"""
    calls: List[Tuple[Tuple[Any, ...], Dict[str, Any]]] = []
    _spy_and_short_circuit(monkeypatch, elb, "load_consumed_inputs_pins", calls)
    training_ids, _validation_ids, _sealed_ids = _practice_split_ids()
    rc = elb.main([
        "extract-song",
        "--corpus-root", str(tmp_path),
        "--song-id", training_ids[0],
        "--out", str(tmp_path / "out.json"),
    ])
    assert rc == 2
    assert calls == [((None,), {"contract_path": None})]


def test_harness3b_build_cli_calls_load_consumed_inputs_pins_with_canonical_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(b) 同上、`build` 版（`load_training_validation_ids()` は実行させ、
    `load_consumed_inputs_pins()` まで到達させる）。"""
    calls: List[Tuple[Tuple[Any, ...], Dict[str, Any]]] = []
    _spy_and_short_circuit(monkeypatch, elb, "load_consumed_inputs_pins", calls)
    out_dir = tmp_path / "out"
    rc = elb.main([
        "build",
        "--corpus-root", str(tmp_path / "corpus"),
        "--out-dir", str(out_dir),
    ])
    assert rc == 2
    assert calls == [((None,), {"contract_path": None})]
