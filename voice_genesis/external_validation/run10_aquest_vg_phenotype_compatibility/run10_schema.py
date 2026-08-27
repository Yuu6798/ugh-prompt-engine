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

# §16: 生成互換が「成立した」と読める status（Phase B を実際に回した行に限る）。
GENERATIVE_SUCCESS_STATUS: Tuple[str, ...] = (
    "GENERATIVELY_COMPATIBLE",
    "GENERATIVELY_PARTIAL",
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
    "design_doc_sha256",
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
    # 題名だけでは、同じ題名で差し替えられた Drive 文書と区別できない。
    # 設計文書は本リポジトリへ置かない（§2.2）ので、実バイト sha256 を契約欄
    # として持つことが唯一の来歴束縛である（PR #330 Codex 第 9 巡 P1）。
    if doc["design_doc_sha256"] != DESIGN_DOC_SHA256:
        raise Run10ContractError(
            f"design_doc_sha256: 凍結値 {DESIGN_DOC_SHA256} でなければならない"
            f"（実際 {doc['design_doc_sha256']!r}）"
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


# staged_intervention の入れ子も閉世界にする。外側のキー集合だけ閉じて内側を
# `get()` で拾うと、`phase_a.activation: INTERVENTIONAL` や
# `phase_a.identity_copy: ALLOWED` のような機械可読欄を足した契約が通り、
# 非介入・Identity 除外の不変条件と矛盾したまま R10-G0 が PASS になり得る
# （PR #330 Codex 第 13 巡 P1）。
STAGED_INTERVENTION_SHAPE: Dict[str, Tuple[str, ...]] = {
    "phase_a": ("description", "changed_edge"),
    "phase_b": ("activation", "description", "changed_edge"),
}


def _validate_staged_intervention(obj: Any) -> None:
    """§23 staged_intervention。Phase A は無介入、Phase B は条件付き。"""
    mapping = _require_mapping("staged_intervention", obj)
    if set(mapping) != set(STAGED_INTERVENTION_SHAPE):
        raise Run10ContractError(
            f"staged_intervention: phase_a / phase_b のみ許容（実際 {sorted(mapping)}）"
        )
    for phase, allowed in STAGED_INTERVENTION_SHAPE.items():
        nested = _require_mapping(f"staged_intervention.{phase}", mapping[phase])
        if set(nested) != set(allowed):
            raise Run10ContractError(
                f"staged_intervention.{phase}: {sorted(allowed)} のみ許容"
                f"（実際 {sorted(nested)}）"
            )
    phase_a = mapping["phase_a"]
    if phase_a["changed_edge"] != "NONE_OBSERVATIONAL_AUDIT":
        raise Run10ContractError(
            "staged_intervention.phase_a.changed_edge は NONE_OBSERVATIONAL_AUDIT 固定"
        )
    phase_b = mapping["phase_b"]
    if phase_b["activation"] != "CONDITIONAL":
        raise Run10ContractError("staged_intervention.phase_b.activation は CONDITIONAL 固定")
    if phase_b["changed_edge"] != "SYNTHESIS_VALIDATION":
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


# §27 が列挙する results の欄（閉世界）。ここに無い欄は commit させない。
RESULTS_ALLOWED_FIELDS: Tuple[str, ...] = (
    "schema",
    "run_id",
    "experiment_id",
    "design_revision",
    "scope",
    "cohorts",
    "performance_analysis",
    "identity_copy",
    "protocol_verdict",
    "phase_b_entry",
    "scientific_outcome",
    "external_calibration",
    "decision_rules",
    "compatibility_matrix",
    "difference_map",
    "path_effects",
    "novel_trait_candidates",
    "generative_compatibility_matrix",
    "synthesis_validation",
    "hard_gates",
    "replay",
    "rights",
)


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

    # top-level のキー集合も閉じる。開いたままだと `publication_scope: PUBLIC` や
    # `public_dataset_release: true` のような対立する機械可読宣言を、必須の
    # private 宣言と同居させたまま正典結果にできる（PR #330 Codex 第 13 巡 P1）。
    unknown = set(mapping) - set(RESULTS_ALLOWED_FIELDS)
    if unknown:
        raise Run10ContractError(
            f"results: 未知の欄 {sorted(unknown)}（許容 {sorted(RESULTS_ALLOWED_FIELDS)}）"
        )

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

    _validate_results_rights(mapping.get("rights"))

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

# evidence 節の種類（第 20 巡でファミリーを終端するための分類）。
#
# `SHAPE` = キーが**設計の固定語彙**である節。未知キーを拒否する。
# `INDEX` = キーが trait id / case id などの**データ識別子**である節。
#           キー集合を閉じられない（閉じたら測定結果を記録できない）。
#           ただしその**行**は SHAPE であり、行側で閉じる。
#
# 「開いたキー集合」ファミリーは第 12/13/15/17/19/20 巡と 6 度再発した。
# 個別に塞ぐのをやめ、検証で降りる mapping を全数分類して
# `MAPPING_CLOSURE_INVENTORY` に登録し、
# `test_every_validated_mapping_is_registered` が未登録の追加を落とす。
_EVIDENCE_SECTION_KIND: Dict[str, str] = {
    "external_calibration": "SHAPE",
    "decision_rules": "SHAPE",
    "path_effects": "SHAPE",
    "replay": "SHAPE",
    "synthesis_validation": "SHAPE",
    "compatibility_matrix": "INDEX",
    "generative_compatibility_matrix": "INDEX",
    "novel_trait_candidates": "INDEX",
    "difference_map": "INDEX",
}

# 検証で降りる mapping の全数棚卸し。名前は `_require_mapping()` へ渡す名前
# （f-string の埋め込みは `<name>` に正規化）か、専用検証器が扱う節の名前。
MAPPING_CLOSURE_INVENTORY: Dict[str, str] = {
    # --- 契約 ---
    "<pin field>": "SHAPE",                       # value / status / reason / source
    "contract": "SHAPE",                          # ALLOWED_TOP_LEVEL_FIELDS
    "cost_cap": "SHAPE",                          # COST_CAP_FIELDS
    "claim_strength_target": "SHAPE",             # CLAIM_STRENGTH_KEYS
    "staged_intervention": "SHAPE",               # STAGED_INTERVENTION_SHAPE
    "staged_intervention.<phase>": "SHAPE",       # STAGED_INTERVENTION_SHAPE[phase]
    "supersedes[]": "SHAPE",                      # document / status
    # --- results ---
    "results": "SHAPE",                           # RESULTS_ALLOWED_FIELDS
    "results.rights": "SHAPE",                    # RESULTS_RIGHTS_REQUIRED|OPTIONAL
    "results.hard_gates": "SHAPE",                # ALL_GATE_IDS
    "results.synthesis_validation.controls": "SHAPE",   # PHASE_B_ONLY_COHORTS
    "compatibility_matrix.<trait_id>": "SHAPE",   # COMPATIBILITY_ENTRY_ALLOWED_FIELDS
    "generative_compatibility_matrix.<trait_id>": "SHAPE",  # GENERATIVE_ENTRY_ALLOWED_FIELDS
}

# evidence 欄のうち、**値まで**固定されるもの（閉世界）。欄の実在だけを見ると
# `replay: {same_process: FAIL, cross_process: FAIL}` を添えたまま比較地図の
# 成立を記録できる（PR #330 Codex 第 10 巡 P1）。§21 R10-G14 は Phase A PASS の
# 要件として same-process / cross-process 双方の再現を求めている。
_EVIDENCE_FIELD_REQUIRED_VALUES: Dict[Tuple[str, str], Tuple[str, ...]] = {
    ("replay", "same_process"): ("PASS",),
    ("replay", "cross_process"): ("PASS",),
}

# §20.4 / §21 R10-G15: Entry 裁定の結果と phase_b_entry は一対一で対応する。
# 実在だけを見ると `R10-G15: FABRICATED` や null が通り、裁定を表していない
# 値で SKIP を正典化できる（PR #330 Codex 第 10 巡 P1）。
_G15_VERDICT_FOR_ENTRY: Dict[str, str] = {
    "ENTER": "PASS",
    "SKIP": "SKIP",
    "BLOCKED": "BLOCKED",
    # Entry へ到達しなかった Run では、裁定 Gate も走っていない
    # （第 15 巡の未実行語彙と揃える）。
    "NOT_REACHED": "NOT_REACHED",
}

# `protocol_verdict: PASS` と両立する Entry 状態。§22.1 が明示する例外は
# 「Phase A PASS 後に PHASE_B_ENTRY = SKIP となっても PASS として完了可能」
# であって BLOCKED ではない。BLOCKED は裁定そのものが未解決であることを表すため、
# PASS の正典結果と両立しない（PR #330 Codex 第 11 巡 P1 — 第 10 巡で導入した
# ENTER/SKIP/BLOCKED 写像が新たに到達可能にした経路）。
_ENTRY_STATES_COMPATIBLE_WITH_PASS: Tuple[str, ...] = ("ENTER", "SKIP")

# §29 手順 35: Entry 裁定が「行われた」ことを意味する状態。これらは verdict に
# 依らず R10-G15 の記録を要求する。到達しなかった NOT_REACHED だけが例外。
_ENTRY_STATES_REQUIRING_ADJUDICATION: Tuple[str, ...] = ("ENTER", "SKIP", "BLOCKED")

# §15.1 / §20.1: 成功側 outcome で compatibility_matrix の各エントリが持つべき欄。
_COMPATIBILITY_ENTRY_REQUIRED: Tuple[str, ...] = ("status", "support", "calibration", "holdout")

# compatibility_matrix の 1 行が持てる欄（閉世界）。選んだキーだけ読んで残りを
# 素通しすると、`identity_copy: ALLOWED` のような対立宣言を行に足せる —
# top-level は identity_copy=PROHIBITED を宣言しているのに、trait 単位では
# 許可した正典結果になる（PR #330 Codex 第 19 巡 P1。第 13 巡の
# staged_intervention / results top-level と同型で、行レベルが残っていた）。
COMPATIBILITY_ENTRY_ALLOWED_FIELDS: Tuple[str, ...] = (
    "status",
    "support",
    "calibration",
    "holdout",
    "family_alias",
    "trait_value_equivalence",
    "phase_b_eligible",
    "notes",
)

# §21: Phase A PASS は R10-G0..G14 の全 PASS を要求する。
PHASE_A_GATE_IDS: Tuple[str, ...] = tuple(gate_id for gate_id, _ in PHASE_A_CORE_GATES)
PHASE_B_GATE_IDS: Tuple[str, ...] = tuple(gate_id for gate_id, _ in PHASE_B_GATES)

ALL_GATE_IDS: Tuple[str, ...] = (
    PHASE_A_GATE_IDS + (PHASE_B_ENTRY_GATE[0],) + PHASE_B_GATE_IDS
)

# Gate 台帳が取り得る値（閉世界）。`PASS` だけを拒否対象にすると、走っていない
# Phase で `FAIL` や `FABRICATED` を宣言できてしまう（PR #330 Codex 第 15 巡 P1）。
GATE_VERDICTS: Tuple[str, ...] = ("PASS", "FAIL", "BLOCKED", "SKIP", "NOT_REACHED")

# Phase B へ入らなかった Run における R10-G16..G22 の指定状態。
GATE_NOT_EXECUTED = "NOT_REACHED"


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
    if _EVIDENCE_SECTION_KIND.get(name) == "SHAPE":
        # キーが設計の固定語彙である節は閉じる。INDEX 節（trait id 等が
        # キー）は閉じられないため、行側 (`assert_*_entry`) で閉じている。
        unknown = set(section) - set(_EVIDENCE_SECTION_SHAPE.get(name, ()))
        if unknown:
            raise Run10ContractError(
                f"results.{name}: 未知の欄 {sorted(unknown)}"
                f"（許容 {sorted(_EVIDENCE_SECTION_SHAPE.get(name, ()))}）"
            )
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
    # 欄の実在だけでなく、値が結論を否定していないこと。
    for key in required:
        allowed = _EVIDENCE_FIELD_REQUIRED_VALUES.get((name, key))
        if allowed is not None and section.get(key) not in allowed:
            raise Run10ContractError(
                f"results.{name}.{key}: {why} には {list(allowed)} が必要"
                f"（実際 {section.get(key)!r}）— evidence が結論を否定してはならない"
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


# outcome と evidence / 状態量の**矛盾**を塞ぐ閉世界表。
#
# 「必要な節が在る」ことと「その中身が結論と整合する」ことは別であり、
# 後者が抜けていると evidence が結論を否定したまま正典結果を記録できる
# （PR #330 Codex 第 5 巡 P1 が 2 方向で露出させた）。同型を個別に潰さず、
# 下の 4 規則で outcome × 状態量のファミリーを全数掃討して終端する。
#
# 【境界宣言】ここで縛るのは outcome と、Run 全体の状態量
# （phase_b_entry / measurement_overfit_signal）の整合だけである。
# trait 単位の値と outcome の整合（例: 全行が NO_STABLE_MAPPING なのに
# COMPATIBILITY_MAP_ESTABLISHED を名乗る）は、§14.6 の判定則が数値で
# freeze されるまで検証しない — 何をもって「地図が成立した」とするかは
# measurement_decision_spec が定義するものであり、実装側が発明しない。


# 同時に成立し得ない outcome の組（閉世界）。§22.2 は outcome を list で持つが、
# 各要素を enum 照合するだけでは `COMPATIBILITY_MAP_ESTABLISHED` と
# `NO_STABLE_CROSS_SYSTEM_MAPPING` を並べた正典結果が通る
# （PR #330 Codex 第 6 巡 P1）。
#
# 【境界宣言】ここで拒否するのは**設計本文から一意に矛盾と読める対**だけである。
# 許容される組み合わせの完全な束（どの outcome が同時に成立してよいか）は
# DESIGN_RUN10 が定義していないため、実装側で発明しない。特に
# `GENERATIVE_COMPATIBILITY_ESTABLISHED` と `MEASUREMENT_ONLY_COMPATIBILITY` は
# trait ごとに別判定になり得る（§16.1 / §16.3 は trait 単位の分類）ため、
# Run 単位で相互排他とは判定しない。
_MUTUALLY_EXCLUSIVE_OUTCOMES: Tuple[Tuple[str, str], ...] = (
    # §22.2: 地図が成立した / 部分的にしか成立しない
    ("COMPATIBILITY_MAP_ESTABLISHED", "PARTIAL_COMPATIBILITY_MAP"),
    # §15.6 / §22.2: 地図が成立した / 安定した対応が無い
    ("COMPATIBILITY_MAP_ESTABLISHED", "NO_STABLE_CROSS_SYSTEM_MAPPING"),
    ("PARTIAL_COMPATIBILITY_MAP", "NO_STABLE_CROSS_SYSTEM_MAPPING"),
    # §22.2: 測定が不十分 / 何かが成立した
    ("MEASUREMENT_INSUFFICIENT", "COMPATIBILITY_MAP_ESTABLISHED"),
    ("MEASUREMENT_INSUFFICIENT", "PARTIAL_COMPATIBILITY_MAP"),
    ("MEASUREMENT_INSUFFICIENT", "SCHEMA_GAP_IDENTIFIED"),
    ("MEASUREMENT_INSUFFICIENT", "GENERATIVE_COMPATIBILITY_ESTABLISHED"),
    ("MEASUREMENT_INSUFFICIENT", "MEASUREMENT_ONLY_COMPATIBILITY"),
    # §15.6「十分な素材・校正・supportがあるにもかかわらず」= 不十分と両立しない
    ("MEASUREMENT_INSUFFICIENT", "NO_STABLE_CROSS_SYSTEM_MAPPING"),
    # §12.6 / §21 R10-G7: 外的妥当性が立たないのに成立を主張しない
    ("MEASUREMENT_OVERFIT_DETECTED", "COMPATIBILITY_MAP_ESTABLISHED"),
    ("MEASUREMENT_OVERFIT_DETECTED", "PARTIAL_COMPATIBILITY_MAP"),
    ("MEASUREMENT_OVERFIT_DETECTED", "GENERATIVE_COMPATIBILITY_ESTABLISHED"),
    # §16 / §22.2: Phase B へ入らなかった / Phase B 由来の結論
    ("PHASE_B_NOT_ENTERED", "GENERATIVE_COMPATIBILITY_ESTABLISHED"),
    ("PHASE_B_NOT_ENTERED", "MEASUREMENT_ONLY_COMPATIBILITY"),
)


def _validate_outcome_consistency(
    mapping: Mapping[str, Any],
    entry: str,
    outcomes: List[Any],
) -> None:
    """outcome と Run 状態量の整合を検証する（§16 / §21 R10-G7 / §22.2）。"""
    # 規則 0: outcome 同士が矛盾していないこと（§22.2）。
    if len(set(outcomes)) != len(outcomes):
        raise Run10ContractError(f"results.scientific_outcome: 重複がある {outcomes}")
    present = set(outcomes)
    for left, right in _MUTUALLY_EXCLUSIVE_OUTCOMES:
        if left in present and right in present:
            raise Run10ContractError(
                f"results.scientific_outcome: {left} と {right} は同時に成立しない（§22.2）"
            )

    # 規則 1: synthesis 由来の結論は Phase B が走った場合にしか出ない（§16）。
    derived = [o for o in outcomes if o in PHASE_B_DERIVED_OUTCOMES]
    if derived and entry != "ENTER":
        raise Run10ContractError(
            f"results: {derived} は phase_b_entry=ENTER が前提（§16）。"
            f" 実際の phase_b_entry={entry!r}"
        )

    # 規則 2: 逆方向 — Phase B へ入ったのに「入らなかった」と記録させない（§22.2）。
    if "PHASE_B_NOT_ENTERED" in outcomes and entry == "ENTER":
        raise Run10ContractError(
            "results: PHASE_B_NOT_ENTERED は phase_b_entry=ENTER と両立しない（§22.2）"
        )

    # 規則 2b: Gate 台帳の閉世界検査と、走っていない Phase の指定状態。
    _validate_hard_gates_shape(mapping, entry)

    calibration = mapping.get("external_calibration")
    signal = (
        calibration.get("measurement_overfit_signal")
        if isinstance(calibration, Mapping)
        else None
    )
    # 以降の規則はすべて `signal is True` で判定する。`1` のような非 bool の
    # truthy 値はどの規則にも掛からず、overfit 信号を立てたまま成立側 outcome と
    # R10-G7 PASS を記録できてしまう（PR #330 Codex 第 12 巡 P1）。
    _require_boolean_field(
        "results.external_calibration.measurement_overfit_signal", signal
    )

    # 規則 3: overfit を結論にするなら、その信号が実際に立っていること（§12.6）。
    if "MEASUREMENT_OVERFIT_DETECTED" in outcomes and signal is not True:
        raise Run10ContractError(
            "results: MEASUREMENT_OVERFIT_DETECTED には"
            " external_calibration.measurement_overfit_signal = true が必要"
            f"（実際 {signal!r}）— evidence が結論を否定してはならない"
        )

    # 規則 5: overfit 信号が立ったなら R10-G7 は PASS になり得ない
    # （§21 R10-G7 / §12.6: E0 校正に失敗した meter は外的妥当性を確立しない）。
    # これを縛らないと、提出された Gate 台帳が G0..G14 を PASS と主張するだけで
    # `protocol_verdict: PASS` の overfit 結果が成立してしまう
    # （PR #330 Codex 第 7 巡 P1）。
    if signal is True:
        ledger = mapping.get("hard_gates")
        if isinstance(ledger, Mapping) and ledger.get("R10-G7") == "PASS":
            raise Run10ContractError(
                "results: measurement_overfit_signal=true のまま R10-G7"
                " EXTERNAL_METER_CALIBRATION を PASS にはできない（§21 R10-G7 / §12.6）"
            )

    # 規則 4: 逆方向 — overfit 信号が立ったまま成立側の結論を名乗らせない。
    # §12.6 / §21 R10-G7: E0 校正に失敗した meter は CALIBRATED_EXTERNAL に
    # できず、§15.1 はそれを DIRECT_COMPATIBLE の必要条件にしている。
    if signal is True:
        established = [o for o in outcomes if o in _ESTABLISHED_OUTCOMES]
        if established:
            raise Run10ContractError(
                f"results: measurement_overfit_signal=true のまま {established} は名乗れない"
                "（§12.6 / §21 R10-G7 — 外的妥当性が確立していない）"
            )


# §2.2 の権利境界を results 側で閉世界に固定する。必須 2 欄だけを見ていると、
# `private_only: true` と同時に `public_audio_release: true` のような禁止された
# 公開モードを宣言した正典結果が通る（PR #330 Codex 第 12 巡 P1）。
RESULTS_RIGHTS_REQUIRED: Dict[str, bool] = {
    "private_only": True,
    "third_party_distribution": False,
}

# 記載してよい追加欄と、その固定値（すべて prohibited 側）。
RESULTS_RIGHTS_OPTIONAL: Dict[str, bool] = {
    "public_audio_release": False,
    "public_model_release": False,
    "public_synthesis_system_release": False,
    "external_listener_panel": False,
}


def _validate_results_rights(obj: Any) -> None:
    """§2.2 / §27: rights 節の閉世界形状。未知欄と矛盾する宣言を拒否する。"""
    rights = _require_mapping("results.rights", obj)
    allowed = set(RESULTS_RIGHTS_REQUIRED) | set(RESULTS_RIGHTS_OPTIONAL)
    unknown = set(rights) - allowed
    if unknown:
        raise Run10ContractError(
            f"results.rights: 未知の欄 {sorted(unknown)}（許容 {sorted(allowed)}）"
        )
    for key, expected in RESULTS_RIGHTS_REQUIRED.items():
        if rights.get(key) is not expected:
            raise Run10ContractError(
                f"results.rights.{key} は {str(expected).lower()} 固定"
                f"（実際 {rights.get(key)!r}）"
            )
    for key, expected in RESULTS_RIGHTS_OPTIONAL.items():
        if key in rights and rights[key] is not expected:
            raise Run10ContractError(
                f"results.rights.{key}: §2.2 は prohibited と定めている"
                f"（{str(expected).lower()} 固定。実際 {rights[key]!r}）"
            )


def _require_complete_gate_ledger(mapping: Mapping[str, Any], why: str) -> None:
    """Gate 台帳が R10-G0..G22 を 1 つも欠かさず載せていること。

    第 15 巡は「未実行の Gate は `NOT_REACHED` と書く」と定めたが、キー集合を
    上からしか閉じていなかったため、その Gate を**省略**すれば同じ結果になった
    （PR #330 Codex 第 17 巡 P1）。省略と NOT_REACHED は別物である —
    前者は「何も言っていない」であり、走らなかったことの記録ではない。
    """
    ledger = mapping.get("hard_gates")
    if not isinstance(ledger, Mapping) or not ledger:
        raise Run10ContractError(f"results.hard_gates: {why} には Gate 台帳が必要")
    missing = [gate_id for gate_id in ALL_GATE_IDS if gate_id not in ledger]
    if missing:
        raise Run10ContractError(
            f"results.hard_gates: {why} の台帳に Gate が欠けている: {missing}"
            f"（走らなかった Gate は省略でなく {GATE_NOT_EXECUTED} と記録する。§21）"
        )


def _validate_hard_gates_shape(mapping: Mapping[str, Any], entry: str) -> None:
    """Gate 台帳のキー集合・値語彙・未実行状態を閉世界で検査する。

    第 14 巡では「PASS を拒否する」だけだったため、走っていない Phase で
    `R10-G16: FAIL` や `FABRICATED` を宣言でき、未知の Gate ID も素通りした
    （PR #330 Codex 第 15 巡 P1）。台帳そのものを閉世界にする。
    """
    ledger = mapping.get("hard_gates")
    if ledger is None:
        return
    if not isinstance(ledger, Mapping):
        raise Run10ContractError(
            f"results.hard_gates は mapping でなければならない（実際 {type(ledger).__name__}）"
        )
    unknown = set(ledger) - set(ALL_GATE_IDS)
    if unknown:
        raise Run10ContractError(
            f"results.hard_gates: 未知の Gate ID {sorted(unknown)}（§21 は R10-G0..G22）"
        )
    bad = {gate_id: value for gate_id, value in ledger.items() if value not in GATE_VERDICTS}
    if bad:
        raise Run10ContractError(
            f"results.hard_gates: 未知の判定値 {bad}（許容 {list(GATE_VERDICTS)}）"
        )
    if entry != "ENTER":
        # §21: R10-G16..G22 は ENTER 時のみ必須。走らなかった Gate は
        # 合格も不合格も宣言できない — 指定の未実行状態だけが正当である。
        wrong = {
            gate_id: ledger[gate_id]
            for gate_id in PHASE_B_GATE_IDS
            if gate_id in ledger and ledger[gate_id] != GATE_NOT_EXECUTED
        }
        if wrong:
            raise Run10ContractError(
                f"results.hard_gates: phase_b_entry={entry} で {wrong} が宣言されている"
                f"（走っていない Gate は {GATE_NOT_EXECUTED} のみ。§21）"
            )


def _validate_entry_ledger_consistency(mapping: Mapping[str, Any], entry: str) -> None:
    """`phase_b_entry` と R10-G15 の対応を **verdict に依らず**強制する。

    第 10/11 巡の束縛は PASS 経路にしか掛かっていなかったため、
    `protocol_verdict: FAILED` + `phase_b_entry: ENTER` + `R10-G15: SKIP` や
    `BLOCKED` + entry `BLOCKED` + `R10-G15: PASS` が通った
    （PR #330 Codex 第 19 巡 P1）。失敗した Run の台帳でも、Entry 裁定と
    Gate の値が食い違った記録を正典にしてはならない。
    """
    gate_id = PHASE_B_ENTRY_GATE[0]
    ledger = mapping.get("hard_gates")
    recorded = ledger.get(gate_id) if isinstance(ledger, Mapping) else None
    has_gate = isinstance(ledger, Mapping) and gate_id in ledger

    # 裁定が行われたと言うなら、その結果が台帳に在る。ENTER だけを縛ると
    # `SKIP` + `hard_gates` 欠落が通り、「一度だけ行った裁定」を主張しながら
    # その Gate 記録を消せる（PR #330 Codex 第 22 巡 P1）。§29 手順 35 の
    # 裁定は ENTER / SKIP / BLOCKED のいずれでも「行われた」ことを意味し、
    # 記録が無くてよいのは Entry へ到達しなかった NOT_REACHED だけである。
    if entry in _ENTRY_STATES_REQUIRING_ADJUDICATION and not has_gate:
        raise Run10ContractError(
            f"results.hard_gates: phase_b_entry={entry} には {gate_id} の裁定結果が必要"
            "（§29 手順 35 — 裁定は一度だけ行われ、その結果は台帳に残る。"
            f"記録を省けるのは {GATE_NOT_EXECUTED} の Entry だけ）"
        )

    if not has_gate:
        return

    expected = _G15_VERDICT_FOR_ENTRY.get(entry)
    if expected is not None and recorded != expected:
        raise Run10ContractError(
            f"results.hard_gates.{gate_id}: phase_b_entry={entry} なら {expected!r}"
            f" でなければならない（実際 {recorded!r}）— §20.4 / §21 R10-G15"
        )


def _require_entry_adjudication(mapping: Mapping[str, Any], entry: str, why: str) -> None:
    """R10-G15 の裁定結果が実在し、`phase_b_entry` と一対一で対応すること。

    実在だけを要求すると `R10-G15: FABRICATED` や null が通り、実際の裁定を
    表していない値で SKIP を正典化できる（PR #330 Codex 第 10 巡 P1）。
    一方で値を一律 PASS に固定してもいけない — §22.1 は SKIP のまま
    Protocol PASS を認めており、SKIP 経路が記録できなくなる。
    そこで §20.4 の Entry 状態と Gate 台帳の値を一対一で束縛する。
    """
    gate_id = PHASE_B_ENTRY_GATE[0]
    ledger = mapping.get("hard_gates")
    if not isinstance(ledger, Mapping) or gate_id not in ledger:
        raise Run10ContractError(
            f"results.hard_gates: {why} には {gate_id} の裁定結果が必要"
            "（ENTER / SKIP のいずれでも裁定は一度行われる — §29 手順 35）"
        )
    if entry not in _ENTRY_STATES_COMPATIBLE_WITH_PASS:
        raise Run10ContractError(
            f"results: {why} で phase_b_entry={entry!r} は成立しない"
            f"（§29 手順 35 により Entry 裁定は必ず一度行われ、§22.1 が PASS と"
            f" 両立を認めるのは SKIP まで。BLOCKED は裁定未解決を表す。"
            f" 許容 {list(_ENTRY_STATES_COMPATIBLE_WITH_PASS)}）"
        )
    expected = _G15_VERDICT_FOR_ENTRY[entry]
    if ledger[gate_id] != expected:
        raise Run10ContractError(
            f"results.hard_gates.{gate_id}: phase_b_entry={entry} なら {expected!r}"
            f" でなければならない（実際 {ledger[gate_id]!r}）— §20.4 / §21 R10-G15"
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
    # outcome 同士・outcome と Run 状態量の矛盾は evidence の充足より先に見る
    # （矛盾した結論に対して「evidence が足りない」と報告するのは誤導になる）。
    _validate_outcome_consistency(mapping, entry, outcomes)
    # Entry 裁定と Gate 台帳の対応は verdict に依らず成り立つ（第 19 巡 P1）。
    _validate_entry_ledger_consistency(mapping, entry)

    if verdict == "PASS":
        # 正典 PASS の Gate 台帳は **R10-G0..G22 を欠かさず**載せる。未知キーだけを
        # 閉じても、走らなかった Gate を**書かない**ことで消せてしまい、第 15 巡が
        # 「未実行は NOT_REACHED と書く」と定めた記録規律が空文化する
        # （PR #330 Codex 第 17 巡 P1）。欠落は沈黙であり、沈黙は記録ではない。
        _require_complete_gate_ledger(mapping, "protocol_verdict=PASS")
        _validate_gate_ledger(mapping, PHASE_A_GATE_IDS, "protocol_verdict=PASS")
        # §29 手順 35「adjudicate PHASE_B_ENTRY exactly once」/ §23
        # phase_b_entry_result_sha: Entry の裁定は ENTER でも SKIP でも一度だけ
        # 行われる。裁定 Gate の実在を PASS の要件にしないと、裁定を経ていない
        # SKIP を正典化できる（PR #330 Codex 第 9 巡 P1）。
        #
        # ただし R10-G15 の値まで PASS を要求してはならない。§21 R10-G15 の
        # 条件が不成立なら PHASE_B_ENTRY=SKIP となり、§22.1 はそれでも
        # Protocol PASS を認めている。値の PASS を要求するのは ENTER のときだけ。
        _require_entry_adjudication(mapping, entry, "protocol_verdict=PASS")
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
            # `is None` だけを見ると `support: []` / `calibration: ""` /
            # `holdout: {}` が「在る」と数えられ、per-trait evidence が空の
            # まま比較地図の成立を主張できる（PR #330 Codex 第 23 巡 P1）。
            # 第 5 巡で導入した `_is_absent_evidence()`（0 や false は在るもの
            # として扱い、空コンテナだけを不在とする）をここにも適用する。
            lacking = [
                key
                for key in _COMPATIBILITY_ENTRY_REQUIRED
                if _is_absent_evidence(entry_doc, key)
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
        unknown = set(synthesis) - set(_EVIDENCE_SECTION_SHAPE["synthesis_validation"])
        if unknown:
            raise Run10ContractError(
                f"results.synthesis_validation: 未知の欄 {sorted(unknown)}"
                f"（許容 {sorted(_EVIDENCE_SECTION_SHAPE['synthesis_validation'])}）"
            )
        controls = _require_mapping("results.synthesis_validation.controls",
                                    synthesis.get("controls"))
        unknown_cohorts = set(controls) - set(PHASE_B_ONLY_COHORTS)
        if unknown_cohorts:
            raise Run10ContractError(
                f"results.synthesis_validation.controls: 未知の cohort"
                f" {sorted(unknown_cohorts)}（§7.5 の 3 対照のみ）"
            )
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


# 真偽値の意味論を持つ欄（閉世界）。`is True` / `is False` で判定する欄は
# 非 bool の truthy/falsy 値が全ガードを素通りするため、型を先に固定する。
# 第 12 巡（measurement_overfit_signal）と第 14 巡（phase_b_eligible）で
# 同型が 2 度露出したため、判定は本ヘルパへ一本化する。
def _require_boolean_field(name: str, value: Any) -> None:
    """`None`（未記載）か真偽値のみ許す。"""
    if value is not None and not isinstance(value, bool):
        raise Run10ContractError(
            f"{name} は真偽値でなければならない（実際 {type(value).__name__}: {value!r}）"
        )


def assert_compatibility_entry(trait_id: str, entry: Any) -> None:
    """§15 / §15.10: family alias が正規 status を上書きしないことを強制する。"""
    mapping = _require_mapping(f"compatibility_matrix.{trait_id}", entry)
    unknown = set(mapping) - set(COMPATIBILITY_ENTRY_ALLOWED_FIELDS)
    if unknown:
        raise Run10ContractError(
            f"compatibility_matrix.{trait_id}: 未知の欄 {sorted(unknown)}"
            f"（許容 {sorted(COMPATIBILITY_ENTRY_ALLOWED_FIELDS)}）— 行に機械可読な"
            "対立宣言を足せないよう閉世界にしている"
        )
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
    # `is True` だけを見ると `1` や `"yes"` のような非 bool の truthy 値が
    # 素通りする（PR #330 Codex 第 14 巡 P1 — 第 12 巡の overfit 信号と同型）。
    eligible = mapping.get("phase_b_eligible")
    _require_boolean_field(f"{trait_id}.phase_b_eligible", eligible)
    if status == "AQUEST_ONLY_CANDIDATE" and eligible is not False:
        raise Run10ContractError(
            f"{trait_id}: AQUEST_ONLY_CANDIDATE は phase_b_eligible=false でなければ"
            f"ならない（§15.5 / §17。実際 {eligible!r}）"
        )


# generative_compatibility_matrix の 1 行が持てる欄（閉世界）。
# compatibility 行と同型で、こちらだけ開いていた（PR #330 Codex 第 20 巡 P1）。
GENERATIVE_ENTRY_ALLOWED_FIELDS: Tuple[str, ...] = (
    "synthesis_status",
    "measurement_status",
    "phase_b_entry",
    "controls",
    "construction_meter",
    "confirmation_meter",
    "notes",
)


def assert_generative_entry(trait_id: str, entry: Any) -> None:
    """§16 / §20.3: GenerativeCompatibilityMatrix の 1 行を検証する。

    `GENERATIVE_STATUS` が宣言だけされて一度も適用されていなかったため、
    `synthesis_status: NOT_A_REAL_STATUS` のような無効分類のまま生成互換の
    成立を記録できた（PR #330 Codex 第 4 巡 P1）。
    """
    mapping = _require_mapping(f"generative_compatibility_matrix.{trait_id}", entry)
    unknown = set(mapping) - set(GENERATIVE_ENTRY_ALLOWED_FIELDS)
    if unknown:
        raise Run10ContractError(
            f"generative_compatibility_matrix.{trait_id}: 未知の欄 {sorted(unknown)}"
            f"（許容 {sorted(GENERATIVE_ENTRY_ALLOWED_FIELDS)}）"
        )
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
    # 行の Phase B 実行状態と生成成否の整合（PR #330 Codex 第 23 巡 P1）。
    # 行状態を enum とだけ照合していたため、`synthesis_status:
    # GENERATIVELY_COMPATIBLE` と `phase_b_entry: SKIP` を同時に載せられた —
    # 「この形質は Phase B を回していない」と「生成互換が成立した」を同じ行が
    # 主張する自己矛盾である。生成側の成立は Phase B を実際に回した行にしか
    # 起こり得ない。
    if status in GENERATIVE_SUCCESS_STATUS and entry_state is not None:
        if entry_state != "ENTER":
            raise Run10ContractError(
                f"{trait_id}: synthesis_status={status} は phase_b_entry=ENTER の行"
                f"でしか成立しない（実際 {entry_state!r}）— §16 / §20.3"
            )


# ---------------------------------------------------------------------------
# 補助
# ---------------------------------------------------------------------------


def verify_design_document(path: Path | str) -> bool:
    """手元に置いた設計文書の実バイトが凍結 sha256 と一致するか。

    設計文書はリポジトリへ commit しない（§2.2）ため、本関数は「Drive から
    private ストレージへ取得した文書が、契約の pin と同一である」ことを
    実行時に確かめるための入口である。文書が無い場合は `False`。
    """
    target = Path(path)
    if not target.is_file():
        return False
    return compute_file_sha256(target) == DESIGN_DOC_SHA256


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
