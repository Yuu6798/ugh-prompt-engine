"""run10_schema.py — RUN10 run-local 正本モジュール（Phase 0 スキャフォールド）。

正本設計は Google Drive 上の凍結文書
`VoiceGenesis_RUN10_AQUEST_VG_Phenotype_Compatibility_Audit_v0.4.md`
（以下 DESIGN_RUN10。実バイト sha256 = `DESIGN_DOC_SHA256`）であり、
本モジュールはその §15/§16/§21/§22/§23/§27 を機械可読な形で実装する。

設計文書そのものは本リポジトリへ commit しない。DESIGN_RUN10 §2.2 が
「分析表、集計値、設計文書の外部公開可否は今回の回答だけでは確定しない
ため、本Runでは公開しない」と規定しており、本リポジトリは public
（`Yuu6798/ugh-prompt-engine`）だからである。本モジュールは設計文書を
**実バイト sha256 で参照 pin** するに留め、文書本文・AQUEST 由来資産・
測定値・集計表を一切含まない（`private_boundary.py` が機械強制する）。

fail-closed 方針（run9_schema.py と同型）: 未知キー拒否、欠落キーの
デフォルト補完なし、pin 欄への事後注入経路を作らない。

設計文書内 erratum（本モジュールが正典として採用した側を明記する）:

1. `design_revision`
   DESIGN_RUN10 のヘッダは v0.4、§37「v0.4 Revision Record」も v0.4 を
   宣言するが、§23 Run Contract 雛形と §27 results schema は
   `design_revision: 0.3` のままである。v0.4 で AF01 v1.0 の凍結登録
   （§7.3 / §36 Pre-Run Reference Presence Note / §37）という実体的改訂
   が入っている以上、文書版と contract 版が食い違う方が危険であるため、
   本モジュールは **"0.4" を正典**とする（`DESIGN_REVISION`）。
2. 章番号 `# 37` の重複
   「37. 最終原則」と「37. v0.4 Revision Record」が同番号で併存する。
   本モジュールは章番号ではなく見出し文字列で参照する。

いずれも実体（凍結ハッシュ・Gate 集合・enum）には影響しない。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml  # PyYAML は本体必須依存（pyproject.toml [project].dependencies）

# ---------------------------------------------------------------------------
# 不変識別子（DESIGN_RUN10 §1.1 / §23 / §27）
# ---------------------------------------------------------------------------

SCHEMA_RUN_CONTRACT = "voicegenesis-run-contract/1.1"
SCHEMA_RESULTS = "voicegenesis-run10-aquest-vg-phenotype-compatibility/1.1"

RUN_ID = "RUN10"
EXPERIMENT_ID = "VG-R10-AQUEST-VG-PHENOTYPE-COMPATIBILITY"
DESIGN_REVISION = "0.4"

# DESIGN_RUN10 実バイト sha256（Drive file id 12NSnw-bjpF2dssbcMk59ykZODZDviy2D /
# 95,709 bytes）。文書本文は非公開のため、参照可能なのはこの pin だけである。
DESIGN_DOC_TITLE = "VoiceGenesis_RUN10_AQUEST_VG_Phenotype_Compatibility_Audit_v0.4.md"
DESIGN_DOC_SHA256 = "cc05f0254aa1ee4a7302edac847a3d07d2fd385f115865185bcfe1343a350957"

# DESIGN_RUN10 §1.2: 旧 RUN10 案は実行前に supersede される。
SUPERSEDED_DESIGN_DOCUMENT = "VoiceGenesis_RUN10_Known_Performance_Trainability_Test_v0.1"
SUPERSEDED_STATUS = "SUPERSEDED_BEFORE_EXECUTION"

# DESIGN_RUN10 §7.3 / §23 / §36: AF01 v1.0 凍結識別子。RUN10 内で変更禁止
# （§7.3「V0は本Run中に変更、再生成、音質改善、自然さ調整を行わない」）。
AF01_FROZEN_HASHES: Dict[str, str] = {
    "af01_payload_ledger_sha256":
        "d447aa1bb9811116d57ecce9a94749707752dcb6f415772d784e22573af9f41d",
    "af01_spec_sha256":
        "888e39161c25adf3c2728ca444b283955f6d32dd58ee185f281830759583dc1d",
    "af01_generator_sha256":
        "46a7034029d5dd65ae4df570ee2702631ff57dfbe172d43a8cf0c4d74aa3ee93",
    "af01_manifest_sha256":
        "19538ccb81c001d22bb8bda26e99edbeca433b9b98595bf8814877956130ed89",
    "af01_canonical_c4_sha256":
        "6710c5ec56109337d586921aa552ffc53f37609c0b7380f31249a1b94b71232c",
}

# DESIGN_RUN10 §7.3 bundle 内容の構造量（AF01_FROZEN_REGISTRATION_v1.0.md /
# FREEZE_REGISTRATION.json と一致することを af01_freeze_verifier が検査する）。
AF01_UNIT_FILE_COUNT = 75
AF01_PITCHES: Tuple[str, str, str] = ("C3", "C4", "G4")
AF01_ALIAS_COUNT = 25
AF01_E0_CALIBRATION_CASES = 9
AF01_AGGREGATE_PROBE_COUNT = 6

# ---------------------------------------------------------------------------
# Cohort（DESIGN_RUN10 §7 / §27）
# ---------------------------------------------------------------------------

COHORTS: Dict[str, str] = {
    "A0": "AQUEST_RAW_VOICEBANK",
    "A1": "AQUEST_NEUTRAL_UTAU_RENDER",
    "V0": "VOICEGENESIS_CANONICAL_BODY",
    "V1": "VOICEGENESIS_REEXPRESSION",
    "E0": "INDEPENDENT_EXTERNAL_GROUND_TRUTH_SYNTH",
    "G_null": "VOICEGENESIS_SYNTHESIS_NULL_CONTROL",
    "G_target": "VOICEGENESIS_SYNTHESIS_TARGET_CONTROL",
    "G_inverse": "VOICEGENESIS_SYNTHESIS_INVERSE_CONTROL",
}

# G 系は Phase B（PHASE_B_ENTRY = ENTER）でのみ生成される（§7.5 / §8.1）。
PHASE_B_ONLY_COHORTS: Tuple[str, str, str] = ("G_null", "G_target", "G_inverse")

# ---------------------------------------------------------------------------
# 分類 enum（DESIGN_RUN10 §14.7 / §15 / §16 / §22）
# ---------------------------------------------------------------------------

# §15 冒頭「正規enumは本節だけに置く」。family alias（HL_alpha 等）は status を
# 上書きできない（§15.10）。
COMPATIBILITY_STATUS: Tuple[str, ...] = (
    "DIRECT_COMPATIBLE",
    "ANALOGOUS_COMPATIBLE",
    "PARTIAL_COMPATIBLE",
    "VG_ONLY_OBSERVED",
    "AQUEST_ONLY_CANDIDATE",
    "NO_STABLE_MAPPING",
    "MEASUREMENT_CONFOUNDED",
    "NOT_EVALUABLE",
    "UNCALIBRATED",
)

GENERATIVE_STATUS: Tuple[str, ...] = (
    "GENERATIVELY_COMPATIBLE",
    "GENERATIVELY_PARTIAL",
    "MEASUREMENT_ONLY_COMPATIBLE",
    "SYNTHESIS_CONFOUNDED",
    "TEACHING_TO_METRIC_RISK",
    "NOT_SYNTHESIS_ELIGIBLE",
    "NOT_EVALUABLE",
)

# §14.7: DIRECT_COMPATIBLE は measurement-definition compatibility であり
# trait 値の等価を意味しない。値の等価を主張するには事前登録 TOST 等が要る。
TRAIT_VALUE_EQUIVALENCE: Tuple[str, ...] = (
    "EQUIVALENT",
    "NOT_EQUIVALENT",
    "UNDETERMINED",
    "NOT_EVALUATED",
)

PROTOCOL_VERDICT: Tuple[str, str, str] = ("PASS", "BLOCKED", "FAILED")

PHASE_B_ENTRY_STATES: Tuple[str, ...] = ("ENTER", "SKIP", "BLOCKED", "NOT_REACHED")

SCIENTIFIC_OUTCOMES: Tuple[str, ...] = (
    "COMPATIBILITY_MAP_ESTABLISHED",
    "PARTIAL_COMPATIBILITY_MAP",
    "SCHEMA_GAP_IDENTIFIED",
    "MEASUREMENT_OVERFIT_DETECTED",
    "GENERATIVE_COMPATIBILITY_ESTABLISHED",
    "MEASUREMENT_ONLY_COMPATIBILITY",
    "PHASE_B_NOT_ENTERED",
    "NO_STABLE_CROSS_SYSTEM_MAPPING",
    "MEASUREMENT_INSUFFICIENT",
)

# §5.3: claim_strength_target は scalar へ圧縮しない（ベクトルのまま保存）。
# 値も §5.3 の凍結語彙そのもので固定する — 任意の非空文字列を許すと
# `performance_claim: C2`（§6 で対象外と定めた軸）や `C999` のような
# 無意味な値で R10-G0 が開く（PR #330 Codex 第 4 巡 P1）。
CLAIM_STRENGTH_TARGET: Dict[str, str] = {
    "measurement_compatibility_claim": "C2",
    "external_schema_validity_claim": "C1-C2",
    "trait_identity_equivalence_claim": "C0",
    "performance_claim": "C0",
    "transfer_or_reconstruction_claim": "C0",
    "generative_trait_compatibility_claim": "C1-C2",
}

CLAIM_STRENGTH_KEYS: Tuple[str, ...] = tuple(CLAIM_STRENGTH_TARGET)

# §14.5 / §27「`total_score`フィールドは禁止」。同義の圧縮スカラも塞ぐ。
FORBIDDEN_RESULT_FIELDS: Tuple[str, ...] = (
    "total_score",
    "totalscore",
    "compatibility_score",
    "similarity_score",
    "overall_score",
)

# ---------------------------------------------------------------------------
# Hard Gate（DESIGN_RUN10 §21）
# ---------------------------------------------------------------------------

PHASE_A_CORE_GATES: Tuple[Tuple[str, str], ...] = (
    ("R10-G0", "RUN_CONTRACT_COMPLETE"),
    ("R10-G1", "RIGHTS_AND_PRIVATE_BOUNDARY"),
    ("R10-G2", "PRE_RUN_INVENTORY_COMPLETE"),
    ("R10-G3", "INPUT_FREEZE"),
    ("R10-G4", "PERFORMANCE_EXCLUSION"),
    ("R10-G5", "COHORT_MATCHING_AND_AXIS_VALIDITY"),
    ("R10-G6", "INTERNAL_METER_CALIBRATION"),
    ("R10-G7", "EXTERNAL_METER_CALIBRATION"),
    ("R10-G8", "DECISION_RULE_FREEZE"),
    ("R10-G9", "RAW_RENDER_SEPARATION"),
    ("R10-G10", "PROCEDURAL_BLINDING"),
    ("R10-G11", "SUPPORT_AND_CONTEXT_REPLICATION"),
    ("R10-G12", "SEALED_CONFIRMATION"),
    ("R10-G13", "CONFOUND_AUDIT"),
    ("R10-G14", "PHASE_A_REPLAY_AND_PRIVATE_PUBLICATION"),
)

PHASE_B_ENTRY_GATE: Tuple[str, str] = ("R10-G15", "PHASE_B_ENTRY")

PHASE_B_GATES: Tuple[Tuple[str, str], ...] = (
    ("R10-G16", "PHASE_A_FREEZE_BEFORE_SYNTHESIS"),
    ("R10-G17", "SYNTHESIS_SPEC_AND_CONTROLS_FROZEN"),
    ("R10-G18", "NO_SCHEMA_EXTENSION"),
    ("R10-G19", "NO_IDENTITY_OR_PERFORMANCE_COPY"),
    ("R10-G20", "GENERATIVE_NEGATIVE_CONTROL_VALID"),
    ("R10-G21", "CONFIRMATION_METER_INDEPENDENCE"),
    ("R10-G22", "SYNTHESIS_VALIDATION_REPLAY_AND_OUTPUT_POLICY"),
)

# ---------------------------------------------------------------------------
# Run Contract 欄定義（DESIGN_RUN10 §23）
# ---------------------------------------------------------------------------

PIN_STATUSES: Tuple[str, ...] = ("PINNED", "PENDING", "BLOCKED", "NOT_APPLICABLE")

# PINNED 値の形式契約（閉世界。AGENTS.md「回収・検収系の成功条件は閉世界契約で
# 書く」）。`_validate_pin_field()` が「非 null なら何でも PINNED」を許すと、
# `pinned::resampler_sha` のようなプレースホルダで R10-G0 を開けてしまい、
# 実在の成果物・依存・commit へ束縛されないまま本測定へ進める偽成功経路になる
# （PR #330 Codex 第 1 巡 P1）。欄ごとに正の形式を要求する。
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_ATTEMPT_ID_RE = re.compile(r"^RUN10-ATTEMPT-[0-9]{2,}$")

# sha 系でない Core 欄の形式（sha 系は名前規約 `_sha` / `_sha256` で判定する）。
NON_SHA_PIN_FORMATS: Dict[str, str] = {
    "attempt_id": "attempt_id",
    "repository_commit_sha": "git_object",
    "minimum_generatable_traits": "positive_int",
}


def _pin_value_format(name: str) -> str:
    """pin 欄名から要求する値形式を決める。"""
    leaf = name.rsplit(".", 1)[-1]
    if leaf in NON_SHA_PIN_FORMATS:
        return NON_SHA_PIN_FORMATS[leaf]
    if name.startswith("cost_cap."):
        return "positive_number"
    if leaf.endswith("_sha") or leaf.endswith("_sha256"):
        return "sha256"
    raise Run10ContractError(f"{name}: 値形式が未定義（閉世界契約に欄を登録すること）")


def _validate_pin_value(name: str, value: Any) -> None:
    """PINNED 値が欄の形式契約を満たすか（fail-closed）。"""
    kind = _pin_value_format(name)
    if kind == "sha256":
        if not isinstance(value, str) or not _SHA256_RE.match(value):
            raise Run10ContractError(
                f"{name}: sha256 は小文字 16 進 64 桁でなければならない（実際 {value!r}）"
            )
    elif kind == "git_object":
        if not isinstance(value, str) or not _GIT_OBJECT_RE.match(value):
            raise Run10ContractError(
                f"{name}: git object 形式（小文字 16 進 40 桁または 64 桁）が必要"
                f"（実際 {value!r}）"
            )
    elif kind == "attempt_id":
        if not isinstance(value, str) or not _ATTEMPT_ID_RE.match(value):
            raise Run10ContractError(
                f"{name}: RUN10-ATTEMPT-NN 形式が必要（実際 {value!r}）"
            )
    elif kind == "positive_int":
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise Run10ContractError(f"{name}: 正の整数が必要（実際 {value!r}）")
    elif kind == "positive_number":
        # YAML の `.inf` / `.nan` は float である。inf は `<= 0` を通り抜け、
        # NaN はあらゆる順序比較が False になるため、素朴な正値判定を素通りして
        # 「有限の上限なしに R10-G0 が開く」（PR #330 Codex 第 2 巡 P2）。
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise Run10ContractError(f"{name}: 有限かつ正の数が必要（実際 {value!r}）")
    else:  # pragma: no cover - _pin_value_format が網羅する
        raise Run10ContractError(f"{name}: 未知の値形式 {kind!r}")

# §23 の `<PIN>` 注記を、pin が確定しなければならない時点でグループ化する。
# Calibration 開始前に Core、Calibration 終了後・AQUEST target 本測定開始前に
# decision 欄を freeze する（§23 冒頭）。
CORE_PIN_FIELDS: Tuple[str, ...] = (
    "attempt_id",
    "repository_commit_sha",
    "dependency_pins_sha",
    "aquest_correspondence_record_sha",
    "rights_manifest_sha",
    "private_storage_policy_sha",
    "pre_run_inventory_sha",
    "aquest_voicebank_manifest_sha",
    "aquest_raw_file_order_sha",
    "aquest_recorded_pitch_inventory_sha",
    "oto_ini_sha",
    "utau_execution_profile_sha",
    "neutral_carrier_manifest_sha",
    "neutral_ust_sha",
    "resampler_sha",
    "wavtool_sha",
    "vg_evolution_theory_ref_sha",
    "af_p0_design_ref_sha",
    "af0_canonical_artifact_sha",
    "vg_reference_manifest_sha",
    "vg_body_artifact_sha",
    "vg_reexpression_artifact_sha",
    "measurement_registry_sha",
    "internal_calibration_fixture_manifest_sha",
    "e0_external_calibration_source_sha",
    "e0_parameter_manifest_sha",
    "construction_meter_registry_sha",
    "confirmation_meter_registry_sha",
    "procedural_blinding_spec_sha",
    "blind_id_map_sha",
    "split_manifest_sha",
    "dataset_row_order_sha",
    "confound_audit_spec_sha",
    "statistical_analysis_plan_sha",
    "novel_candidate_protocol_sha",
    "failure_abort_criteria_sha",
    "execution_profile_sha",
    "e0_af01_sf1_truth_sha",
)

# §23: AF-P0 / AF0 は optional historical reference。欠損は RUN10 全体を
# BLOCK せず、AF 固有 family だけを NOT_EVALUABLE へ落とす（§7.7 / §21 R10-G2）。
BLOCKABLE_PIN_FIELDS: Tuple[str, ...] = (
    "af_p0_design_ref_sha",
    "af0_canonical_artifact_sha",
)

AFTER_CALIBRATION_PIN_FIELDS: Tuple[str, ...] = (
    "external_calibration_results_sha",
)

BEFORE_TARGET_MEASUREMENT_PIN_FIELDS: Tuple[str, ...] = (
    "measurement_decision_spec_sha",
    "af01_v1_manifest_sha",
    "phase_b_entry_spec_sha",
    "minimum_generatable_traits",
)

AFTER_PHASE_A_PIN_FIELDS: Tuple[str, ...] = (
    "phase_a_freeze_manifest_sha",
    "phase_b_entry_result_sha",
)

PHASE_B_PIN_FIELDS: Tuple[str, ...] = (
    "synthesis_trait_spec_sha",
    "synthesis_control_manifest_sha",
    "synthesis_engine_sha",
    "synthesis_renderer_sha",
    "synthesis_vocoder_sha",
    "synthesis_seed_policy_sha",
    "synthesis_acceptance_spec_sha",
)

AFTER_RUN_PIN_FIELDS: Tuple[str, ...] = (
    "artifact_manifest_sha",
    "cost_record_sha",
)

ALL_PIN_FIELDS: Tuple[str, ...] = (
    CORE_PIN_FIELDS
    + AFTER_CALIBRATION_PIN_FIELDS
    + BEFORE_TARGET_MEASUREMENT_PIN_FIELDS
    + AFTER_PHASE_A_PIN_FIELDS
    + PHASE_B_PIN_FIELDS
    + AFTER_RUN_PIN_FIELDS
)

# pin 欄以外のトップレベル構造欄。
STRUCTURAL_FIELDS: Tuple[str, ...] = (
    "schema",
    "run_id",
    "experiment_id",
    "design_revision",
    "design_doc",
    "staged_intervention",
    "supersedes",
    "cost_cap",
    "claim_strength_target",
    "publication_scope",
    "performance_analysis",
    "identity_copy",
) + tuple(AF01_FROZEN_HASHES)

ALLOWED_TOP_LEVEL_FIELDS: Tuple[str, ...] = STRUCTURAL_FIELDS + ALL_PIN_FIELDS

COST_CAP_FIELDS: Tuple[str, ...] = (
    "cpu_hours_max",
    "gpu_hours_max",
    "storage_bytes_max",
    "render_count_max",
)

# §31 cost cap の 4 欄も R10-G0 RUN_CONTRACT_COMPLETE の対象である。
# §23 は cost_cap を Run Contract の必須内容として並べ、§32 Stop Rule 20 は
# cap 超過を停止条件にしている。cap が PENDING のまま G0 が PASS すると、
# 上限なしで課金作業を開始できてしまう（PR #330 Codex 第 1 巡 P2）。
COST_CAP_PIN_FIELDS: Tuple[str, ...] = tuple(f"cost_cap.{name}" for name in COST_CAP_FIELDS)

CONTRACT_STAGES: Tuple[str, ...] = (
    "CORE",
    "AFTER_CALIBRATION",
    "BEFORE_TARGET_MEASUREMENT",
    "AFTER_PHASE_A",
    "PHASE_B",
    "AFTER_RUN",
)

_STAGE_FIELDS: Dict[str, Tuple[str, ...]] = {
    "CORE": CORE_PIN_FIELDS + COST_CAP_PIN_FIELDS,
    "AFTER_CALIBRATION": AFTER_CALIBRATION_PIN_FIELDS,
    "BEFORE_TARGET_MEASUREMENT": BEFORE_TARGET_MEASUREMENT_PIN_FIELDS,
    "AFTER_PHASE_A": AFTER_PHASE_A_PIN_FIELDS,
    "PHASE_B": PHASE_B_PIN_FIELDS,
    "AFTER_RUN": AFTER_RUN_PIN_FIELDS,
}


class Run10ContractError(ValueError):
    """RUN10 contract の構造・値の不正。fail-closed で送出する。"""


@dataclass(frozen=True)
class PinField:
    """§23 の pin 欄 1 件。`{value, status, reason?, source?}` 形。"""

    name: str
    value: Any
    status: str
    reason: Optional[str] = None
    source: Optional[str] = None

    @property
    def pinned(self) -> bool:
        return self.status == "PINNED"


@dataclass(frozen=True)
class Run10Contract:
    """検証済み RUN10 Run Contract。"""

    raw: Mapping[str, Any]
    pins: Mapping[str, PinField]

    def pin(self, name: str) -> PinField:
        if name not in self.pins:
            raise Run10ContractError(f"未定義の pin 欄: {name}")
        return self.pins[name]

    def missing(self, stage: str) -> List[str]:
        """`stage` で PINNED になっていない欄名を返す。

        BLOCKABLE_PIN_FIELDS は status == "BLOCKED" を許容する
        （§7.7 / §21 R10-G2: AF-P0 / AF0 欠損は RUN10 を BLOCK しない）。
        """
        if stage not in _STAGE_FIELDS:
            raise Run10ContractError(f"未知の stage: {stage}")
        out: List[str] = []
        for name in _STAGE_FIELDS[stage]:
            field = self.pins[name]
            if field.pinned:
                continue
            if name in BLOCKABLE_PIN_FIELDS and field.status == "BLOCKED":
                continue
            out.append(name)
        return out

    def stage_state(self, stage: str) -> str:
        """`PASS` か `BLOCKED`。§21 R10-G0 は Core の完全 pin を要求する。"""
        return "PASS" if not self.missing(stage) else "BLOCKED"

    def gate_r10_g0(self) -> str:
        """R10-G0 RUN_CONTRACT_COMPLETE（Core 欄がすべて pin 済みか）。"""
        return self.stage_state("CORE")


def _require_mapping(name: str, obj: Any) -> Mapping[str, Any]:
    if not isinstance(obj, Mapping):
        raise Run10ContractError(f"{name} は mapping でなければならない: {type(obj).__name__}")
    return obj


def _validate_pin_field(name: str, obj: Any) -> PinField:
    mapping = _require_mapping(name, obj)
    unknown = set(mapping) - {"value", "status", "reason", "source"}
    if unknown:
        raise Run10ContractError(f"{name}: 未知のキー {sorted(unknown)}")
    if "status" not in mapping:
        raise Run10ContractError(f"{name}: status が無い")
    if "value" not in mapping:
        raise Run10ContractError(f"{name}: value が無い（欠落をデフォルト補完しない）")
    status = mapping["status"]
    if status not in PIN_STATUSES:
        raise Run10ContractError(f"{name}: 未知の status {status!r}（許容 {list(PIN_STATUSES)}）")
    value = mapping["value"]
    if status == "PINNED" and value is None:
        raise Run10ContractError(f"{name}: status=PINNED なのに value が null")
    if status == "PINNED":
        _validate_pin_value(name, value)
    if status != "PINNED" and value is not None:
        raise Run10ContractError(
            f"{name}: status={status} なのに value が非 null（未確定値を先取りしない）"
        )
    if status in ("PENDING", "BLOCKED", "NOT_APPLICABLE") and not mapping.get("reason"):
        raise Run10ContractError(f"{name}: status={status} には reason が必須")
    if status == "BLOCKED" and name not in BLOCKABLE_PIN_FIELDS:
        raise Run10ContractError(
            f"{name}: この欄に BLOCKED は許されない（BLOCKED 可 = {list(BLOCKABLE_PIN_FIELDS)}）"
        )
    return PinField(
        name=name,
        value=value,
        status=status,
        reason=mapping.get("reason"),
        source=mapping.get("source"),
    )


def _validate_structure(doc: Mapping[str, Any]) -> None:
    """pin 欄以外の固定値・構造欄を検証する（§1.1 / §5.3 / §23 / §31）。"""
    unknown = set(doc) - set(ALLOWED_TOP_LEVEL_FIELDS)
    if unknown:
        raise Run10ContractError(f"contract に未知の欄: {sorted(unknown)}")
    missing = set(STRUCTURAL_FIELDS) - set(doc)
    if missing:
        raise Run10ContractError(f"contract に構造欄が無い: {sorted(missing)}")

    fixed = {
        "schema": SCHEMA_RUN_CONTRACT,
        "run_id": RUN_ID,
        "experiment_id": EXPERIMENT_ID,
        "design_revision": DESIGN_REVISION,
        "publication_scope": "PRIVATE_ONLY",
        "performance_analysis": "OUT_OF_SCOPE",
        "identity_copy": "PROHIBITED",
    }
    for key, expected in fixed.items():
        if doc[key] != expected:
            raise Run10ContractError(f"{key}: {expected!r} でなければならない（実際 {doc[key]!r}）")

    if doc["design_doc"] != DESIGN_DOC_TITLE:
        raise Run10ContractError(
            f"design_doc: {DESIGN_DOC_TITLE!r} でなければならない（実際 {doc['design_doc']!r}）"
        )

    for key, expected_hash in AF01_FROZEN_HASHES.items():
        if doc[key] != expected_hash:
            raise Run10ContractError(
                f"{key}: AF01 v1.0 凍結値 {expected_hash} と一致しない（実際 {doc[key]!r}）"
                " — §7.3 により RUN10 内で AF01 を差し替えてはならない"
            )

    _validate_staged_intervention(doc["staged_intervention"])
    _validate_supersedes(doc["supersedes"])
    _validate_claim_strength(doc["claim_strength_target"])


def _validate_staged_intervention(obj: Any) -> None:
    """§23 staged_intervention。Phase A は無介入、Phase B は条件付き。"""
    mapping = _require_mapping("staged_intervention", obj)
    if set(mapping) != {"phase_a", "phase_b"}:
        raise Run10ContractError(
            f"staged_intervention: phase_a / phase_b のみ許容（実際 {sorted(mapping)}）"
        )
    phase_a = _require_mapping("staged_intervention.phase_a", mapping["phase_a"])
    if phase_a.get("changed_edge") != "NONE_OBSERVATIONAL_AUDIT":
        raise Run10ContractError(
            "staged_intervention.phase_a.changed_edge は NONE_OBSERVATIONAL_AUDIT 固定"
        )
    phase_b = _require_mapping("staged_intervention.phase_b", mapping["phase_b"])
    if phase_b.get("activation") != "CONDITIONAL":
        raise Run10ContractError("staged_intervention.phase_b.activation は CONDITIONAL 固定")
    if phase_b.get("changed_edge") != "SYNTHESIS_VALIDATION":
        raise Run10ContractError(
            "staged_intervention.phase_b.changed_edge は SYNTHESIS_VALIDATION 固定"
        )


def _validate_supersedes(obj: Any) -> None:
    """§1.2: 旧 RUN10 案が実行前 supersede として記録されていること。"""
    if not isinstance(obj, list) or not obj:
        raise Run10ContractError("supersedes: 非空の list でなければならない")
    found = False
    for entry in obj:
        item = _require_mapping("supersedes[]", entry)
        if set(item) != {"document", "status"}:
            raise Run10ContractError(
                f"supersedes[]: document / status のみ許容（実際 {sorted(item)}）"
            )
        if item["document"] == SUPERSEDED_DESIGN_DOCUMENT:
            if item["status"] != SUPERSEDED_STATUS:
                raise Run10ContractError(
                    f"supersedes: 旧 RUN10 案の status は {SUPERSEDED_STATUS} でなければならない"
                )
            found = True
    if not found:
        raise Run10ContractError(
            f"supersedes: 旧設計 {SUPERSEDED_DESIGN_DOCUMENT!r} の supersede 記録が無い"
        )


def _validate_cost_cap(obj: Any) -> Dict[str, PinField]:
    """§31: cost cap 4 欄が存在すること（結果確認後の引き上げは同一 attempt 内禁止）。"""
    mapping = _require_mapping("cost_cap", obj)
    if set(mapping) != set(COST_CAP_FIELDS):
        raise Run10ContractError(
            f"cost_cap: {sorted(COST_CAP_FIELDS)} のみ許容（実際 {sorted(mapping)}）"
        )
    return {
        f"cost_cap.{key}": _validate_pin_field(f"cost_cap.{key}", mapping[key])
        for key in COST_CAP_FIELDS
    }


def _validate_claim_strength(obj: Any) -> None:
    """§5.3: 6 軸ベクトルのまま保存し scalar へ圧縮しない。"""
    mapping = _require_mapping("claim_strength_target", obj)
    if set(mapping) != set(CLAIM_STRENGTH_KEYS):
        raise Run10ContractError(
            f"claim_strength_target: {sorted(CLAIM_STRENGTH_KEYS)} のみ許容"
            f"（実際 {sorted(mapping)}）"
        )
    for key, expected in CLAIM_STRENGTH_TARGET.items():
        if mapping[key] != expected:
            raise Run10ContractError(
                f"claim_strength_target.{key}: §5.3 の凍結値 {expected!r} でなければならない"
                f"（実際 {mapping[key]!r}）"
            )


def load_run10_contract(path: Path | str) -> Run10Contract:
    """`RUN10_CONTRACT.yaml` を fail-closed で読み込む。"""
    text = Path(path).read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    return parse_run10_contract(doc)


def parse_run10_contract(doc: Any) -> Run10Contract:
    """すでに読み込んだ mapping を検証して `Run10Contract` にする。"""
    mapping = _require_mapping("contract", doc)
    _validate_structure(mapping)
    pins: Dict[str, PinField] = {}
    for name in ALL_PIN_FIELDS:
        if name not in mapping:
            raise Run10ContractError(f"contract に pin 欄が無い: {name}")
        pins[name] = _validate_pin_field(name, mapping[name])
    # §31 cost cap の 4 欄も pin 欄であり、R10-G0 の対象に含める。
    pins.update(_validate_cost_cap(mapping["cost_cap"]))
    return Run10Contract(raw=dict(mapping), pins=pins)


# ---------------------------------------------------------------------------
# results 文書（DESIGN_RUN10 §27）
# ---------------------------------------------------------------------------


def assert_no_forbidden_score_field(doc: Any, path: str = "$") -> None:
    """§14.5 / §27: TotalScore 系の圧縮スカラを再帰的に拒否する。"""
    if isinstance(doc, Mapping):
        for key, value in doc.items():
            if isinstance(key, str) and key.lower() in FORBIDDEN_RESULT_FIELDS:
                raise Run10ContractError(
                    f"{path}.{key}: 単一スコア欄は禁止（§14.5 — 形質ごとの状態ベクトルを保持する）"
                )
            assert_no_forbidden_score_field(value, f"{path}.{key}")
    elif isinstance(doc, list):
        for index, value in enumerate(doc):
            assert_no_forbidden_score_field(value, f"{path}[{index}]")


def validate_results_document(doc: Any) -> None:
    """`run10_results.json` の最小 schema（§27）を fail-closed で検証する。"""
    mapping = _require_mapping("results", doc)
    assert_no_forbidden_score_field(mapping)

    if mapping.get("schema") != SCHEMA_RESULTS:
        raise Run10ContractError(f"results.schema は {SCHEMA_RESULTS} 固定")
    if mapping.get("run_id") != RUN_ID:
        raise Run10ContractError(f"results.run_id は {RUN_ID} 固定")
    if mapping.get("experiment_id") != EXPERIMENT_ID:
        raise Run10ContractError(f"results.experiment_id は {EXPERIMENT_ID} 固定")
    if mapping.get("design_revision") != DESIGN_REVISION:
        raise Run10ContractError(f"results.design_revision は {DESIGN_REVISION} 固定")
    if mapping.get("scope") != "PRIVATE_ONLY":
        raise Run10ContractError("results.scope は PRIVATE_ONLY 固定（§2.2 / §26）")
    if mapping.get("performance_analysis") != "OUT_OF_SCOPE":
        raise Run10ContractError("results.performance_analysis は OUT_OF_SCOPE 固定（§6 / §H7）")
    if mapping.get("identity_copy") != "PROHIBITED":
        raise Run10ContractError("results.identity_copy は PROHIBITED 固定（§5.2 / §6）")

    if mapping.get("cohorts") != COHORTS:
        raise Run10ContractError("results.cohorts が §27 の cohort 定義と一致しない")

    verdict = mapping.get("protocol_verdict")
    if verdict not in PROTOCOL_VERDICT:
        raise Run10ContractError(f"results.protocol_verdict: 未知の値 {verdict!r}")

    entry = mapping.get("phase_b_entry")
    if entry not in PHASE_B_ENTRY_STATES:
        raise Run10ContractError(f"results.phase_b_entry: 未知の値 {entry!r}")

    outcomes = mapping.get("scientific_outcome")
    if not isinstance(outcomes, list) or not outcomes:
        raise Run10ContractError("results.scientific_outcome: 非空の list が必要")
    for item in outcomes:
        if item not in SCIENTIFIC_OUTCOMES:
            raise Run10ContractError(f"results.scientific_outcome: 未知の値 {item!r}")

    rights = _require_mapping("results.rights", mapping.get("rights"))
    if rights.get("private_only") is not True:
        raise Run10ContractError("results.rights.private_only は true 固定")
    if rights.get("third_party_distribution") is not False:
        raise Run10ContractError("results.rights.third_party_distribution は false 固定")

    _validate_results_evidence(mapping, verdict, entry, outcomes)


# 「比較地図・生成互換が成立した」と主張する側の outcome（閉世界）。
# protocol_verdict が BLOCKED / FAILED のときに名乗れない集合であり、
# compatibility_matrix の per-entry evidence を要求する集合でもある。
#
# `MEASUREMENT_OVERFIT_DETECTED` はここに**含めない**。§12.6 / §22.2 により
# これは「成立の主張」ではなく有効な否定的診断であり、protocol_verdict が
# BLOCKED でも成立し得る。ただし evidence を伴う結論であることに変わりはない
# ため、下の evidence 表には登録する（PR #330 Codex 第 4 巡 P1）。
_ESTABLISHED_OUTCOMES: Tuple[str, ...] = (
    "COMPATIBILITY_MAP_ESTABLISHED",
    "PARTIAL_COMPATIBILITY_MAP",
    "SCHEMA_GAP_IDENTIFIED",
    "GENERATIVE_COMPATIBILITY_ESTABLISHED",
    "MEASUREMENT_ONLY_COMPATIBILITY",
    "NO_STABLE_CROSS_SYSTEM_MAPPING",
)

# outcome ごとに要求する evidence 節（閉世界契約。AGENTS.md「回収・検収系の
# 成功条件は閉世界契約で書く」）。実行時データから要求集合を導出しない。
_EVIDENCE_FOR_OUTCOME: Dict[str, Tuple[str, ...]] = {
    # §12.6 / §22.2 Outcome C: overfit の検出自体が evidence を伴う結論であり、
    # データ無しで名乗れる中立な未完状態ではない。
    "MEASUREMENT_OVERFIT_DETECTED": (
        "external_calibration",
        "replay",
    ),
    "COMPATIBILITY_MAP_ESTABLISHED": (
        "external_calibration",
        "decision_rules",
        "compatibility_matrix",
        "difference_map",
        "path_effects",
        "replay",
    ),
    "PARTIAL_COMPATIBILITY_MAP": (
        "external_calibration",
        "decision_rules",
        "compatibility_matrix",
        "replay",
    ),
    "SCHEMA_GAP_IDENTIFIED": (
        "external_calibration",
        "decision_rules",
        "compatibility_matrix",
        "novel_trait_candidates",
        "replay",
    ),
    "GENERATIVE_COMPATIBILITY_ESTABLISHED": (
        "external_calibration",
        "decision_rules",
        "compatibility_matrix",
        "generative_compatibility_matrix",
        "synthesis_validation",
        "replay",
    ),
    "MEASUREMENT_ONLY_COMPATIBILITY": (
        "external_calibration",
        "decision_rules",
        "compatibility_matrix",
        "generative_compatibility_matrix",
        "synthesis_validation",
        "replay",
    ),
    "NO_STABLE_CROSS_SYSTEM_MAPPING": (
        "external_calibration",
        "decision_rules",
        "compatibility_matrix",
        "replay",
    ),
}

# §16: Phase B が走らなければ出ない outcome。
PHASE_B_DERIVED_OUTCOMES: Tuple[str, ...] = (
    "GENERATIVE_COMPATIBILITY_ESTABLISHED",
    "MEASUREMENT_ONLY_COMPATIBILITY",
)

# evidence 節の**形状契約**（閉世界）。節が非空 mapping であるだけでは
# `{"placeholder": true}` が通り、実体のない成功を記録できる
# （PR #330 Codex 第 1〜3 巡が同一領域で 3 度露出させた）。各節に設計が
# 명示する固定欄を要求してファミリーを終端させる。
#
# 【境界宣言】本表は DESIGN_RUN10 が節ごとに明示している欄までを要求する。
# 各欄の**内側**の値域・単位・数値妥当性は、§11 measurement family と
# §14.6 measurement_decision_spec が実装されるまで検証しない（存在しない
# 測定契約を先取りして発明しないため）。measurement 層の実装 PR で
# `_EVIDENCE_SECTION_SHAPE` を深める。
_EVIDENCE_SECTION_SHAPE: Dict[str, Tuple[str, ...]] = {
    # §7.6 / §12.3 / §12.5 / §12.6: E0 独立校正の回収結果と overfit 信号。
    "external_calibration": (
        "e0_parameter_recovery",
        "meter_calibration_status",
        "measurement_overfit_signal",
    ),
    # §14.6 measurement_decision_spec.yaml が本測定前に freeze する欄。
    "decision_rules": (
        "minimum_support",
        "calibration",
        "equivalence",
        "context_persistence",
    ),
    # §18.1 / §18.2 / §18.3。
    "path_effects": ("delta_a_path", "delta_v_path", "d_output"),
    # §14.2 / §28-84 / §28-85 / §28-86。
    "replay": ("same_process", "cross_process", "feature_json_sha"),
    # §20.3: 測定互換と生成互換を別列で保存する。
    "generative_compatibility_matrix": (),
    # §17 の候補登録簿。
    "novel_trait_candidates": (),
    # §20.2。
    "difference_map": (),
    "compatibility_matrix": (),
    "synthesis_validation": ("controls", "construction_meter", "confirmation_meter"),
}

# §15.1 / §20.1: 成功側 outcome で compatibility_matrix の各エントリが持つべき欄。
_COMPATIBILITY_ENTRY_REQUIRED: Tuple[str, ...] = ("status", "support", "calibration", "holdout")

# §21: Phase A PASS は R10-G0..G14 の全 PASS を要求する。
PHASE_A_GATE_IDS: Tuple[str, ...] = tuple(gate_id for gate_id, _ in PHASE_A_CORE_GATES)
PHASE_B_GATE_IDS: Tuple[str, ...] = tuple(gate_id for gate_id, _ in PHASE_B_GATES)


def _is_absent_evidence(section: Mapping[str, Any], key: str) -> bool:
    """evidence の欄が実質的に不在か（false / 0 を不在と混同しない）。"""
    if key not in section:
        return True
    value = section[key]
    if value is None:
        return True
    return isinstance(value, (Mapping, list, str)) and len(value) == 0


def _require_nonempty_section(mapping: Mapping[str, Any], name: str, why: str) -> None:
    """evidence 節が**非空の mapping** であることを要求する。

    「空でなければよい」では `compatibility_matrix: placeholder` のような
    スカラが通り、しかも mapping でないためエントリ検証も飛ぶ
    （PR #330 Codex 第 2 巡 P1 — 第 1 巡で入れた検査の型が緩かった）。
    """
    section = mapping.get(name)
    if section is None:
        raise Run10ContractError(f"results.{name}: {why} には本節が必要（欠落）")
    if not isinstance(section, Mapping):
        raise Run10ContractError(
            f"results.{name}: {why} の evidence は mapping でなければならない"
            f"（実際 {type(section).__name__}）"
        )
    if not section:
        raise Run10ContractError(f"results.{name}: {why} には本節が必要（空）")
    required = _EVIDENCE_SECTION_SHAPE.get(name, ())
    # 真偽値で判定しない: `measurement_overfit_signal: false` は正当な値であり、
    # 欠落と混同してはならない。要求するのは「キーが在り、null でなく、
    # 空の mapping / list / str でないこと」。
    missing = [key for key in required if _is_absent_evidence(section, key)]
    if missing:
        raise Run10ContractError(
            f"results.{name}: {why} に必要な欄が無い/空: {missing}"
            f"（設計が明示する固定欄。placeholder mapping では成立しない）"
        )


def _validate_gate_ledger(mapping: Mapping[str, Any], gate_ids: Tuple[str, ...], why: str) -> None:
    """`hard_gates` が要求 Gate 集合を閉世界で被覆し、全て PASS であること。"""
    ledger = mapping.get("hard_gates")
    if not isinstance(ledger, Mapping) or not ledger:
        raise Run10ContractError(f"results.hard_gates: {why} には Gate 台帳が必要")
    missing = [gate_id for gate_id in gate_ids if gate_id not in ledger]
    if missing:
        raise Run10ContractError(f"results.hard_gates: {why} に必要な Gate が無い: {missing}")
    not_passed = [gate_id for gate_id in gate_ids if ledger.get(gate_id) != "PASS"]
    if not_passed:
        raise Run10ContractError(
            f"results.hard_gates: {why} なのに PASS でない Gate がある: {not_passed}"
        )


def _validate_results_evidence(
    mapping: Mapping[str, Any],
    verdict: str,
    entry: str,
    outcomes: List[Any],
) -> None:
    """成功側の判定に、対応する evidence が実在することを要求する（§21 / §22 / §27）。

    構造だけ整えた空文書で `protocol_verdict: PASS` +
    `COMPATIBILITY_MAP_ESTABLISHED` を記録できると、比較地図が成立していない
    のに成立したことにできる偽成功経路になる（PR #330 Codex 第 1 巡 P1）。
    """
    if verdict == "PASS":
        _validate_gate_ledger(mapping, PHASE_A_GATE_IDS, "protocol_verdict=PASS")
        if entry == "ENTER":
            # R10-G15 PHASE_B_ENTRY は Phase B を authorize する Gate そのもの
            # であり、要求集合から落とせない（PR #330 Codex 第 2 巡 P1）。
            _validate_gate_ledger(
                mapping,
                PHASE_A_GATE_IDS + (PHASE_B_ENTRY_GATE[0],) + PHASE_B_GATE_IDS,
                "phase_b_entry=ENTER の PASS",
            )
    else:
        # BLOCKED / FAILED で成立側 outcome を名乗らせない（§22.1 / §22.2）。
        established = [o for o in outcomes if o in _ESTABLISHED_OUTCOMES]
        if established:
            raise Run10ContractError(
                f"results: protocol_verdict={verdict} で成功側 outcome {established} は名乗れない"
            )

    for outcome in outcomes:
        for section in _EVIDENCE_FOR_OUTCOME.get(outcome, ()):
            _require_nonempty_section(mapping, section, outcome)

    # §16: synthesis 由来の結論はすべて Phase B が走った場合にしか出ない。
    # GENERATIVE_COMPATIBILITY_ESTABLISHED だけを縛ると、
    # MEASUREMENT_ONLY_COMPATIBILITY（§16.3）を SKIP / NOT_REACHED のまま
    # 名乗れてしまう（PR #330 Codex 第 3 巡 P1）。
    named = [o for o in outcomes if o in PHASE_B_DERIVED_OUTCOMES]
    if named and entry != "ENTER":
        raise Run10ContractError(
            f"results: {named} は phase_b_entry=ENTER が前提（§16）。"
            f" 実際の phase_b_entry={entry!r}"
        )

    matrix = mapping.get("compatibility_matrix")
    established = any(outcome in _ESTABLISHED_OUTCOMES for outcome in outcomes)
    if isinstance(matrix, Mapping):
        for trait_id, entry_doc in matrix.items():
            assert_compatibility_entry(str(trait_id), entry_doc)
            if not established:
                continue
            if not isinstance(entry_doc, Mapping):  # pragma: no cover - 上で送出済み
                continue
            # §15.1 / §20.1: status だけの行で比較地図成立を主張させない。
            lacking = [
                key for key in _COMPATIBILITY_ENTRY_REQUIRED if entry_doc.get(key) is None
            ]
            if lacking:
                raise Run10ContractError(
                    f"compatibility_matrix.{trait_id}: 成功側 outcome には"
                    f" {_COMPATIBILITY_ENTRY_REQUIRED} が必要（欠落 {lacking}）"
                )

    generative = mapping.get("generative_compatibility_matrix")
    if isinstance(generative, Mapping):
        for trait_id, entry_doc in generative.items():
            assert_generative_entry(str(trait_id), entry_doc)

    synthesis = mapping.get("synthesis_validation")
    if isinstance(synthesis, Mapping) and synthesis:
        controls = _require_mapping("results.synthesis_validation.controls",
                                    synthesis.get("controls"))
        for cohort in PHASE_B_ONLY_COHORTS:
            if not controls.get(cohort):
                raise Run10ContractError(
                    f"results.synthesis_validation.controls.{cohort}: "
                    "G_null / G_target / G_inverse の 3 対照が揃わない裁定は不可（§7.5 / §21 R10-G20）"
                )
        # §21 R10-G21: construction meter 単独で generative success を裁定しない。
        if not synthesis.get("confirmation_meter"):
            raise Run10ContractError(
                "results.synthesis_validation.confirmation_meter: "
                "生成に未使用の confirmation meter が無い裁定は不可（§21 R10-G21 / §32-26）"
            )


def assert_compatibility_entry(trait_id: str, entry: Any) -> None:
    """§15 / §15.10: family alias が正規 status を上書きしないことを強制する。"""
    mapping = _require_mapping(f"compatibility_matrix.{trait_id}", entry)
    status = mapping.get("status")
    if status not in COMPATIBILITY_STATUS:
        raise Run10ContractError(f"{trait_id}.status: 未知の値 {status!r}（§15 が唯一の正規 enum）")
    alias = mapping.get("family_alias")
    if alias is not None and alias in COMPATIBILITY_STATUS:
        raise Run10ContractError(
            f"{trait_id}.family_alias: 正規 status 語彙 {alias!r} を alias に使えない（§15.10）"
        )
    equivalence = mapping.get("trait_value_equivalence")
    if equivalence is not None and equivalence not in TRAIT_VALUE_EQUIVALENCE:
        raise Run10ContractError(f"{trait_id}.trait_value_equivalence: 未知の値 {equivalence!r}")
    # §15.5 / §21 R10-G18: AQUEST_ONLY_CANDIDATE は Phase B へ送らない。
    if status == "AQUEST_ONLY_CANDIDATE" and mapping.get("phase_b_eligible") is True:
        raise Run10ContractError(
            f"{trait_id}: AQUEST_ONLY_CANDIDATE は phase_b_eligible にできない（§15.5 / §17）"
        )


def assert_generative_entry(trait_id: str, entry: Any) -> None:
    """§16 / §20.3: GenerativeCompatibilityMatrix の 1 行を検証する。

    `GENERATIVE_STATUS` が宣言だけされて一度も適用されていなかったため、
    `synthesis_status: NOT_A_REAL_STATUS` のような無効分類のまま生成互換の
    成立を記録できた（PR #330 Codex 第 4 巡 P1）。
    """
    mapping = _require_mapping(f"generative_compatibility_matrix.{trait_id}", entry)
    status = mapping.get("synthesis_status")
    if status not in GENERATIVE_STATUS:
        raise Run10ContractError(
            f"{trait_id}.synthesis_status: 未知の値 {status!r}（§16 が唯一の正規 enum）"
        )
    measurement_status = mapping.get("measurement_status")
    if measurement_status is not None and measurement_status not in COMPATIBILITY_STATUS:
        raise Run10ContractError(
            f"{trait_id}.measurement_status: 未知の値 {measurement_status!r}（§15）"
        )
    entry_state = mapping.get("phase_b_entry")
    if entry_state is not None and entry_state not in PHASE_B_ENTRY_STATES + ("NOT_ELIGIBLE",):
        raise Run10ContractError(f"{trait_id}.phase_b_entry: 未知の値 {entry_state!r}")
    # §15.5 / §21 R10-G18: AQUEST_ONLY_CANDIDATE は Phase B の対象外。
    if measurement_status == "AQUEST_ONLY_CANDIDATE" and status != "NOT_SYNTHESIS_ELIGIBLE":
        raise Run10ContractError(
            f"{trait_id}: AQUEST_ONLY_CANDIDATE は NOT_SYNTHESIS_ELIGIBLE 以外になれない"
        )


# ---------------------------------------------------------------------------
# 補助
# ---------------------------------------------------------------------------


def compute_file_sha256(path: Path | str) -> str:
    """実バイト sha256（run9_schema.compute_file_sha256 と同一規約）。"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(doc: Any) -> bytes:
    """成果物 JSON の正規化バイト（sort_keys + 改行終端）。"""
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
