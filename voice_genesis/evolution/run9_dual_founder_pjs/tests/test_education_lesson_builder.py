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

import ast
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

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
    training_ids, validation_ids = elb.load_training_validation_ids(
        m.PRACTICE_MANIFEST_PATH,
    )
    assert len(training_ids) == 70
    assert len(validation_ids) == 15
    assert training_ids == sorted(training_ids)
    assert validation_ids == sorted(validation_ids)
    assert set(training_ids).isdisjoint(validation_ids)
    # sealed_holdout row_ids never enter this function's return value.
    split_manifest = json.loads(m.PRACTICE_MANIFEST_PATH.read_text(encoding="utf-8"))
    sealed = set(split_manifest["row_ids"]["sealed_holdout"])
    assert sealed.isdisjoint(training_ids)
    assert sealed.isdisjoint(validation_ids)
