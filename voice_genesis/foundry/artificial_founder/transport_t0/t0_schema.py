"""t0_schema.py — AF-T0 の凍結契約と fail-closed 検査（設計書 §4 / §8 / §13 / §27）。

**I/O を持たない。** 値の検査と凍結定数だけを置く。AF-P0 の `af_schema` と同じ
方針で、「結果を見てから許容値・候補・fixture を都合よく変える」余地を定数と
検査で塞ぐ。

T0 固有の要点:

* §4 Frozen Inputs — AF0 spec SHA と base commit を定数で持つ。不一致は
  `AF0_INPUT_DRIFT` で BLOCKED。
* §13 Final Tolerances — AF-P0 original tolerance の **再利用**。T0 側に別の
  許容値を持たせない（緩和の余地を型で消す）。criteria が P0 と食い違ったら
  拒否する。
* §12 Calibration / Holdout — fixture 値を凍結し、**AF0 target を較正に使わない**
  ことを検査可能にする。
* §10 / §11 Candidate — 候補 ID と探索順を凍結。結果を見てからの追加を拒否。
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, List, Mapping, Sequence, Tuple

T0_SCHEMA = "voicegenesis-af-t0/1.0"
T0_CRITERIA_SCHEMA = "voicegenesis-af-t0-criteria/1.0"
T0_FIXTURES_SCHEMA = "voicegenesis-af-t0-fixtures/1.0"
SIDECAR_SCHEMA = "voicegenesis-trait-transport/0.1"

EXPERIMENT_ID = "AF-T0"
FOUNDER_ID = "AF0"

#: §4 / §39。設計基準コミットと AF0 spec の正準ダイジェスト。
FROZEN_BASE_COMMIT = "adcf67bc3e70006afb86edad730e1da261209e33"
FROZEN_AF0_SPEC_SHA256 = (
    "c477fd5a9ec2ac3dd97f2c7ea076568acce19b53c28eed5c10175cc807b5e8d4")

#: AF-T0 の canonical revision。**実験 ID は増やさず revision を上げる**運用。
#: 失敗した試行は非正典の run 履歴として残し、experiment_id は `AF-T0` のまま。
#: 履歴は DESIGN_AF_T0.md Appendix B（Revision History）が正本。
T0_REVISION = 2

#: §4。pin 対象の AF-P0 成果物と **凍結 SHA256**。
#: 「存在するか」だけでは pin にならない（内容が変わっても素通りする）ので、
#: base commit `adcf67b` 時点の実バイト列のハッシュと突き合わせる。
FROZEN_P0_ARTIFACTS: Tuple[str, ...] = (
    "p0_results.json",
    "comparison.json",
    "measurements/body.json",
    "measurements/reexpressed.json",
    "founder_manifest.json",
    "code_closure.json",
)
#: §4 / T0-G0。**AF-P0 criteria のバイト列**も pin する。全測定・全比較が
#: この文書を読むので、metric_definitions や sentinel 許容値を書き換えれば
#: canonical verdict が変わる。git 由来の drift 検査は git が無い環境で `None`
#: を許すため、それだけでは pin にならない。
FROZEN_P0_CRITERIA_SHA256 = (
    "3d6ea665fb426ad0edf19433327de4b9c9df3a576f7ebe92f3881bcab827858a")

#: §4 / T0-G0。**実 voicebank の identity digest**（`p0_results.pins` に
#: 記録されている値）。JSON 成果物だけを照合しても、WAV が差し替わっていれば
#: 汚染された Body を canonical AF0 として受け入れてしまう。
FROZEN_AF0_BODY_IDENTITY_DIGEST = (
    "5a5702cb453768265c390fc2eeabd3a07dad6194c0a6a426eedc0df239a7d6ec")

FROZEN_P0_ARTIFACT_SHA256: Dict[str, str] = {
    "p0_results.json":
        "c23407411d263bad9fedf4d52ca1364e59066e28e78cc83091d14578aa2b96dd",
    "comparison.json":
        "7b2d3c488169aa602ea894f1b76ca10e05f9492cd508ceb455c510f598ea345e",
    "measurements/body.json":
        "b8343b22d1ad65dcb09aba6be9ad17643a2899d0e6af33faf503251c779db1ce",
    "measurements/reexpressed.json":
        "afba48964497a4f4ae740793c00427e879b269ebcfad8b32fb10b9626ef398a1",
    "founder_manifest.json":
        "0feb4731880a7ec5ecf09226f03d865f2be07eb4d7d17d2f23836bdbdb5680ec",
    "code_closure.json":
        "eb3575fdc3ba95a09711005a86db01c4b39f5c61d4a4d0a7f3516b358f2e917a",
}

#: §0。T0 が前提とする AF-P0 の historical verdict と失敗 3 形質。
FROZEN_P0_VERDICT = "NOT_ESTABLISHED"
FROZEN_P0_FAILED_GATES: Tuple[str, ...] = ("G10", "G11", "G13")
FROZEN_P0_FAILED_FAMILIES: Tuple[str, ...] = (
    "duration_onset", "energy_sustain", "afterglow")

#: §5。transport path の段。S2 は diagnostic 専用（波形 verdict を作らない）。
STAGE_IDS: Tuple[str, ...] = (
    "S0_BODY_44K", "S1_RESAMPLED_24K", "S2_WORLD_ANALYSIS",
    "S3_WORLD_SYNTH_RAW", "S4_POST_NORM_FLOAT", "S5_PCM16_ROUNDTRIP")
#: blind meter を当てる段（§5「S0/S1/S3/S4/S5 には同じ blind meter」）。
WAVEFORM_STAGE_IDS: Tuple[str, ...] = (
    "S0_BODY_44K", "S1_RESAMPLED_24K", "S3_WORLD_SYNTH_RAW",
    "S4_POST_NORM_FLOAT", "S5_PCM16_ROUNDTRIP")
#: §5 末尾。ここから trait verdict を作ってはならない段。
DIAGNOSTIC_ONLY_STAGE_IDS: Tuple[str, ...] = ("S2_WORLD_ANALYSIS",)

#: §6。first_divergence_stage の語彙。
DIVERGENCE_VOCABULARY: Tuple[str, ...] = (
    "RESAMPLING", "WORLD_ANALYSIS", "WORLD_SYNTHESIS",
    "POST_SYNTH_NORMALIZATION", "PCM_PUBLICATION", "METER_ONLY",
    "MULTI_STAGE", "UNRESOLVED")

#: §6。localization 専用 epsilon。**final PASS 閾値ではない**。
FROZEN_LOCALIZATION_EPSILON: Dict[str, float] = {
    "duration": 5.0,      # ms
    "energy": 0.75,       # dB
    "afterglow": 7.5,     # ms
}

#: §7。transport の 2 様式。
TRANSPORT_MODES: Tuple[str, ...] = ("native", "sidecar")

#: §10 / §11。候補 ID と **探索順**（結果後の追加は禁止）。
FROZEN_CANDIDATES: Dict[str, Tuple[str, ...]] = {
    "duration": ("D0", "D1", "D2"),
    "energy": ("E0", "E1", "E2"),
    "afterglow": ("A0", "A1", "A2"),
}
#: §10。後続候補へ進める **前提条件**。「理由を問わず FAIL したら次」は
#: preregistered search space の実質拡張になるので、設計が書いた条件だけを許す。
#: 値は「直前候補のどの失敗なら次へ進めるか」。
FROZEN_CANDIDATE_PRECONDITION: Dict[str, str] = {
    # D2 は D1 FAIL 時のみ（§10 D2「D1 FAIL 時のみ」）
    "D1": "any",          # D0 が通らなければ D1 へ
    "D2": "any",          # §10 は D1 FAIL とだけ書いている
    # E2 は E1 が **holdout FAIL** の場合のみ（§10 E2）
    "E1": "any",
    "E2": "holdout",
    # A2 は A1 が **sentinel regression** で落ちた場合のみ（§10 A2）
    "A1": "any",
    "A2": "sentinel",
}

#: 各候補の transport mode。native baseline 以外は sidecar。
FROZEN_CANDIDATE_MODES: Dict[str, str] = {
    "D0": "native", "D1": "sidecar", "D2": "sidecar",
    "E0": "native", "E1": "sidecar", "E2": "sidecar",
    "A0": "native", "A1": "sidecar", "A2": "sidecar",
}

#: §13。AF-P0 original tolerance をそのまま再利用する（T0 で緩和禁止）。
#: キーは `AF_P0_CRITERIA.json.reexpression_gates` のキー名と一致させ、
#: `validate_criteria` が P0 側の値と照合する。
FROZEN_FINAL_TOLERANCES: Dict[str, float] = {
    "duration_onset_tol_ms": 15.0,
    "r_share_tol": 0.08,
    "energy_sustain_tol_db": 2.0,
    "afterglow_extra_delta_ms_max": 20.0,
}

#: §14。sentinel（既存 PASS 形質）。P0 original re-expression criteria を再適用する。
FROZEN_SENTINEL_FAMILIES: Tuple[str, ...] = (
    "spectral_identity", "hl_alpha", "ar_alpha", "ar_beta",
    "f0_core", "terminal_f0", "release")

#: §21。Gate 集合。欠落・重複・未知は判定不能（BLOCKED）。
ALL_GATE_IDS: Tuple[str, ...] = tuple(f"T0-G{i}" for i in range(11))

#: §33 exit code。
EXIT_CODES: Dict[str, int] = {
    "PASS": 0, "NOT_ESTABLISHED": 1, "BLOCKED": 3, "FAILED": 4}

#: §12。fixture の凍結値。**AF0 target を candidate fitting に使わない**ことを
#: 型で表す: `calibration` と `holdout` に AF0 の値が現れたら検査で落とす。
FROZEN_CALIBRATION: Dict[str, Any] = {
    "duration": [
        {"id": "dur_lo", "r_onset_share": 0.10, "r_onset_ms": 20.0, "i_vowel_ms": 180.0},
        {"id": "dur_hi", "r_onset_share": 0.40, "r_onset_ms": 80.0, "i_vowel_ms": 120.0},
    ],
    "energy": [
        {"id": "en_lo", "sustain_dbfs": -18.0},
        {"id": "en_hi", "sustain_dbfs": -6.0},
    ],
    "afterglow": [
        {"id": "ag_lo", "ar_alpha_afterglow_extra_ms": 0.0},
        {"id": "ag_hi", "ar_alpha_afterglow_extra_ms": 80.0},
    ],
}
FROZEN_HOLDOUT: Dict[str, Any] = {
    "duration": [
        {"id": "dur_holdout", "r_onset_share": 0.30, "r_onset_ms": 60.0,
         "i_vowel_ms": 140.0},
    ],
    "energy": [
        {"id": "en_holdout", "sustain_dbfs": -9.0},
    ],
    "afterglow": [
        {"id": "ag_holdout", "ar_alpha_afterglow_extra_ms": 55.0},
    ],
}
#: §12。AF0 final confirmation の値（**fitting に使ってはならない**）。
FROZEN_AF0_CONFIRMATION: Dict[str, float] = {
    "r_onset_share": 0.20,
    "sustain_dbfs": -12.0,
    "ar_alpha_afterglow_extra_ms": 35.0,
}

#: §17。重点 failure unit。
FROZEN_FOCUS_UNITS: Dict[str, Tuple[str, ...]] = {
    "duration": ("ra", "ri", "ru", "re", "ro"),
    "energy": ("i", "ki", "shi", "ni", "ri"),
    "afterglow": ("u", "ku", "ko", "su", "so", "ru"),
}

#: §29。Source-Free で **追加許可** される読み取り（P0 境界の拡張分）。
T0_EXTRA_READ_KINDS: Tuple[str, ...] = (
    "af_p0_generated_body", "af_p0_result_json", "t0_fixture", "t0_sidecar")


class T0SpecError(ValueError):
    """T0 の凍結契約違反。`errors` に全件の理由を持つ。"""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors: List[str] = list(errors)
        super().__init__("; ".join(self.errors))


def canonical_digest(payload: Any) -> str:
    """JSON 部分木の正準ダイジェスト（キー順・空白非依存）。"""
    canon = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


FROZEN_CALIBRATION_SHA256 = canonical_digest(FROZEN_CALIBRATION)
FROZEN_HOLDOUT_SHA256 = canonical_digest(FROZEN_HOLDOUT)


def _num(obj: Mapping[str, Any], key: str, path: str, errors: List[str]) -> float:
    v = obj.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        errors.append(f"{path}.{key}: expected number, got {type(v).__name__}")
        return math.nan
    if not math.isfinite(float(v)):
        errors.append(f"{path}.{key}: must be finite, got {v!r}")
        return math.nan
    return float(v)


def validate_criteria(criteria: Any, p0_reexpression_gates: Mapping[str, Any]) -> List[str]:
    """T0 criteria を検査する。**許容値は P0 と一致していなければならない**。

    §13「AF-P0 original tolerance をそのまま再利用。T0 で緩和禁止」を、
    「T0 側に独自の数値を持たせない」形で担保する。T0 criteria が P0 の
    `reexpression_gates` と 1 つでも食い違えば不合格。
    """
    errors: List[str] = []
    if not isinstance(criteria, dict):
        return [f"criteria: expected object, got {type(criteria).__name__}"]

    if criteria.get("schema") != T0_CRITERIA_SCHEMA:
        errors.append(f"criteria.schema: expected {T0_CRITERIA_SCHEMA!r}, "
                      f"got {criteria.get('schema')!r}")
    if criteria.get("experiment_id") != EXPERIMENT_ID:
        errors.append(f"criteria.experiment_id: expected {EXPERIMENT_ID!r}, "
                      f"got {criteria.get('experiment_id')!r}")
    if criteria.get("founder_id") != FOUNDER_ID:
        errors.append(f"criteria.founder_id: expected {FOUNDER_ID!r}, "
                      f"got {criteria.get('founder_id')!r}")

    tol = criteria.get("final_tolerances")
    if not isinstance(tol, dict):
        errors.append("criteria.final_tolerances: expected object")
    else:
        for key, want in FROZEN_FINAL_TOLERANCES.items():
            got = tol.get(key)
            if got is None:
                errors.append(f"criteria.final_tolerances.{key}: missing")
            elif isinstance(got, bool) or not isinstance(got, (int, float)):
                # `float()` へ渡す前に型を見る。非数値で例外を投げると
                # `T0Stop` を経由せず traceback + exit 1 になり、CLI が
                # NOT_ESTABLISHED に予約している終了コードと衝突する。
                errors.append(f"criteria.final_tolerances.{key}: expected number, "
                              f"got {type(got).__name__}")
            elif float(got) != want:
                errors.append(
                    f"criteria.final_tolerances.{key}: AF-P0 original tolerance を"
                    f"そのまま使うこと（expected {want}, got {got}）— §13 で緩和禁止")
            elif key in p0_reexpression_gates and float(
                    p0_reexpression_gates[key]) != want:
                errors.append(
                    f"criteria.final_tolerances.{key}: AF-P0 criteria 側が "
                    f"{p0_reexpression_gates[key]} で凍結値 {want} と一致しない "
                    "（P0 criteria drift）")
        for key in sorted(set(tol) - set(FROZEN_FINAL_TOLERANCES)):
            errors.append(f"criteria.final_tolerances.{key}: unknown key")

    eps = criteria.get("localization_epsilon")
    if not isinstance(eps, dict):
        errors.append("criteria.localization_epsilon: expected object")
    else:
        for key, want in FROZEN_LOCALIZATION_EPSILON.items():
            got = eps.get(key)
            if got is None or isinstance(got, bool) or not isinstance(got, (int, float)):
                errors.append(f"criteria.localization_epsilon.{key}: expected number, "
                              f"got {type(got).__name__}")
            elif float(got) != want:
                errors.append(f"criteria.localization_epsilon.{key}: "
                              f"expected {want}, got {got!r}")
        for key in sorted(set(eps) - set(FROZEN_LOCALIZATION_EPSILON)):
            errors.append(f"criteria.localization_epsilon.{key}: unknown key")

    cand = criteria.get("candidate_order")
    if not isinstance(cand, dict):
        errors.append("criteria.candidate_order: expected object")
    else:
        for trait, want_seq in FROZEN_CANDIDATES.items():
            got = cand.get(trait)
            if got is None:
                errors.append(f"criteria.candidate_order.{trait}: missing")
            elif not isinstance(got, (list, tuple)):
                errors.append(f"criteria.candidate_order.{trait}: expected list, "
                              f"got {type(got).__name__}")
            elif tuple(got) != want_seq:
                errors.append(
                    f"criteria.candidate_order.{trait}: 候補と探索順は凍結されている"
                    f"（expected {list(want_seq)}, got {got}）— §10/§11 で事後追加禁止")
        for key in sorted(set(cand) - set(FROZEN_CANDIDATES)):
            errors.append(f"criteria.candidate_order.{key}: unknown trait")

    sent = criteria.get("sentinel_families")
    if sent is None:
        errors.append("criteria.sentinel_families: missing")
    elif not isinstance(sent, (list, tuple)):
        errors.append(f"criteria.sentinel_families: expected list, "
                      f"got {type(sent).__name__}")
    elif tuple(sent) != FROZEN_SENTINEL_FAMILIES:
        errors.append(f"criteria.sentinel_families: expected "
                      f"{list(FROZEN_SENTINEL_FAMILIES)}, got {sent}")
    return errors


def _validate_fixture_rows(rows: Any, trait: str, path: str,
                           frozen: Sequence[Mapping[str, Any]],
                           errors: List[str]) -> None:
    if not isinstance(rows, list):
        errors.append(f"{path}.{trait}: expected list")
        return
    if len(rows) != len(frozen):
        errors.append(f"{path}.{trait}: expected {len(frozen)} row(s), got {len(rows)}")
        return
    for got, want in zip(rows, frozen):
        if not isinstance(got, dict):
            errors.append(f"{path}.{trait}: row must be an object")
            continue
        if got.get("id") != want["id"]:
            errors.append(f"{path}.{trait}: expected id {want['id']!r}, "
                          f"got {got.get('id')!r}")
        for key, wv in want.items():
            if key == "id":
                continue
            gv = _num(got, key, f"{path}.{trait}[{want['id']}]", errors)
            if math.isfinite(gv) and gv != float(wv):
                errors.append(f"{path}.{trait}[{want['id']}].{key}: "
                              f"expected {wv}, got {gv}")
        for key in sorted(set(got) - set(want)):
            errors.append(f"{path}.{trait}[{want['id']}].{key}: unknown key")


def validate_fixtures(calibration: Any, holdout: Any) -> List[str]:
    """§12 の fixture を凍結値と照合する。

    さらに **AF0 confirmation 値が calibration / holdout に現れないこと** を
    検査する。現れていたら「AF0 target を補正量決定に使わない」という T0 の
    基本設計が崩れる（§12 冒頭）。
    """
    errors: List[str] = []
    for label, doc, frozen in (("calibration", calibration, FROZEN_CALIBRATION),
                               ("holdout", holdout, FROZEN_HOLDOUT)):
        if not isinstance(doc, dict):
            errors.append(f"{label}: expected object, got {type(doc).__name__}")
            continue
        if doc.get("schema") != T0_FIXTURES_SCHEMA:
            errors.append(f"{label}.schema: expected {T0_FIXTURES_SCHEMA!r}, "
                          f"got {doc.get('schema')!r}")
        if doc.get("role") != label:
            errors.append(f"{label}.role: expected {label!r}, got {doc.get('role')!r}")
        rows = doc.get("fixtures")
        if not isinstance(rows, dict):
            errors.append(f"{label}.fixtures: expected object")
            continue
        for trait, frozen_rows in frozen.items():
            _validate_fixture_rows(rows.get(trait), trait, f"{label}.fixtures",
                                   frozen_rows, errors)
        for key in sorted(set(rows) - set(frozen)):
            errors.append(f"{label}.fixtures.{key}: unknown trait")

    errors += _reject_af0_leakage(calibration, holdout)
    return errors


def _reject_af0_leakage(calibration: Any, holdout: Any) -> List[str]:
    """AF0 confirmation 値が fixture に混じっていないか（§12）。

    calibration に AF0 の値が入ると補正量が AF0 に過適合し、holdout に入ると
    「新鮮な保留」でなくなる。どちらも T0 の主張を無効にするので構造で止める。
    """
    errors: List[str] = []
    key_by_trait = {"duration": "r_onset_share", "energy": "sustain_dbfs",
                    "afterglow": "ar_alpha_afterglow_extra_ms"}
    for label, doc in (("calibration", calibration), ("holdout", holdout)):
        rows = doc.get("fixtures") if isinstance(doc, dict) else None
        if not isinstance(rows, dict):
            continue
        for trait, key in key_by_trait.items():
            af0 = FROZEN_AF0_CONFIRMATION[key]
            for row in rows.get(trait) or []:
                if not isinstance(row, dict):
                    continue
                v = row.get(key)
                if isinstance(v, (int, float)) and not isinstance(v, bool) \
                        and float(v) == af0:
                    errors.append(
                        f"{label}.fixtures.{trait}[{row.get('id')!r}].{key}: "
                        f"AF0 confirmation 値 {af0} を fixture に使ってはならない"
                        "（§12: AF0 を candidate fitting に使わない）")
    return errors


def validate_sidecar(sidecar: Any) -> List[str]:
    """§8 / §27。Trait Sidecar の必須項目と Body 束縛を検査する。"""
    errors: List[str] = []
    if not isinstance(sidecar, dict):
        return [f"sidecar: expected object, got {type(sidecar).__name__}"]
    if sidecar.get("schema") != SIDECAR_SCHEMA:
        errors.append(f"sidecar.schema: expected {SIDECAR_SCHEMA!r}, "
                      f"got {sidecar.get('schema')!r}")
    # §27 Sidecar Integrity の必須項目。
    for key in ("unit_id", "source_body_sha256", "operator_version",
                "duration", "energy", "founder_expression"):
        if key not in sidecar:
            errors.append(f"sidecar.{key}: missing (§27 で必須)")
    uid = sidecar.get("unit_id")
    if uid is not None and (not isinstance(uid, str) or uid.count("/") != 2):
        errors.append(f"sidecar.unit_id: expected 'founder/pitch/stem', got {uid!r}")
    sha = sidecar.get("source_body_sha256")
    if sha is not None and (not isinstance(sha, str) or len(sha) != 64):
        errors.append("sidecar.source_body_sha256: expected a sha256 hex digest")

    dur = sidecar.get("duration")
    if isinstance(dur, dict):
        if dur.get("source") != "founder_body_truth":
            errors.append("sidecar.duration.source: §9 で 'founder_body_truth' 固定")
        for key in ("onset_ms", "vowel_ms", "total_articulation_ms"):
            _num(dur, key, "sidecar.duration", errors)
    elif dur is not None:
        errors.append("sidecar.duration: expected object")

    en = sidecar.get("energy")
    if isinstance(en, dict):
        if en.get("source") != "body_measurement":
            errors.append("sidecar.energy.source: §9 で 'body_measurement' 固定"
                          "（AF0.json の値を post-hoc 強制しない）")
        # sustain は必須。attack は **母音 unit でしか測れない**（CV unit は子音
        # onset があるため attack が定義されない）ので nullable にする。ここで
        # 数値を要求すると、測れない量を AF0.json から埋める誘惑が生まれ、
        # §9「AF0.json の値を直接 post-hoc 強制しない」に反する。
        _num(en, "sustain_dbfs", "sidecar.energy", errors)
        if "attack_ms" not in en:
            errors.append("sidecar.energy.attack_ms: missing (null 可・キーは必須)")
        elif en["attack_ms"] is not None:
            _num(en, "attack_ms", "sidecar.energy", errors)
    elif en is not None:
        errors.append("sidecar.energy: expected object")

    fe = sidecar.get("founder_expression")
    if isinstance(fe, dict):
        if fe.get("id") != "AG-alpha":
            errors.append(f"sidecar.founder_expression.id: expected 'AG-alpha', "
                          f"got {fe.get('id')!r}")
        for key in ("terminal_f0_delta_cents", "ar_alpha_afterglow_extra_ms"):
            _num(fe, key, "sidecar.founder_expression", errors)
        # Body 実現値は測定由来なので nullable（キーは必須）。
        if "body_realized_afterglow_ms" not in fe:
            errors.append("sidecar.founder_expression.body_realized_afterglow_ms: "
                          "missing (null 可・キーは必須)")
        if fe.get("source") != "genome_parameter+body_realization":
            errors.append("sidecar.founder_expression.source: §9 で "
                          "'genome_parameter+body_realization' 固定")
    elif fe is not None:
        errors.append("sidecar.founder_expression: expected object")
    return errors


def require_valid(errors: Sequence[str]) -> None:
    """非空なら `T0SpecError` を送出する（fail-closed 入口）。"""
    if errors:
        raise T0SpecError(errors)
