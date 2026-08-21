"""af_compare.py — 測定値と Ground Truth の照合（設計書 §14 / §18）。

**AF0.json を読んでよいのはこの層だけ**（§14）。`af_measure` は設計値へ到達
できない構造にしてあり、ここで初めて「測った値」と「設計した値」が出会う。

許容値は `criteria/AF_P0_CRITERIA.json` の `body_gates` / `reexpression_gates`
に事前登録されている。**結果を見て許容値を変更してはならない**（§5 / 本書冒頭）。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence

import numpy as np

from af_measure import cosine_similarity
from af_spec import Criteria, FounderGenome, cents_between

VOWEL_STEMS = ("a", "i", "u", "e", "o")
R_STEMS = ("ra", "ri", "ru", "re", "ro")


def _err(measured: Optional[float], target: float) -> Optional[float]:
    if measured is None or not math.isfinite(measured):
        return None
    return float(abs(measured - target))


def _check_all(rows: Sequence[Dict[str, Any]], tol: float, label: str) -> Dict[str, Any]:
    """全 unit が許容内かを判定する（測定不能 = 不合格。fail-closed）。"""
    failed = [r for r in rows if r["error"] is None or r["error"] > tol]
    return {
        "metric": label, "tolerance": tol, "n": len(rows), "n_failed": len(failed),
        "worst": max((r["error"] for r in rows if r["error"] is not None), default=None),
        "failed_units": [r["stem"] for r in failed],
        "rows": rows,
        "verdict": "PASS" if (rows and not failed) else "FAIL",
    }


def _rows(meas: Mapping[str, Dict[str, Any]], stems: Sequence[str], key: str,
          target: float) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for stem in stems:
        m = meas.get(stem, {})
        value = m.get(key)
        out.append({"stem": stem, "measured": value, "target": target,
                    "error": _err(value, target)})
    return out


def _cent_rows(meas: Mapping[str, Dict[str, Any]], stems: Sequence[str], key: str,
               target_hz: float) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for stem in stems:
        value = meas.get(stem, {}).get(key)
        err = None
        if value is not None and math.isfinite(value) and value > 0:
            err = abs(cents_between(value, target_hz))
        out.append({"stem": stem, "measured": value, "target": target_hz, "error": err})
    return out


# ---------------------------------------------------------------------------
# Body
# ---------------------------------------------------------------------------
def compare_body(genome: FounderGenome, criteria: Criteria,
                 meas: Mapping[str, Dict[str, Any]]) -> Dict[str, Any]:
    """§18.1 の Body Gate をすべて評価する。"""
    g = criteria.body_gates
    stems = [u.stem for u in genome.units]
    out: Dict[str, Any] = {}

    # --- §15.1 Standard Identity（15 peak 中 13 以上）------------------------
    peaks: List[Dict[str, Any]] = []
    for stem in VOWEL_STEMS:
        unit = next(u for u in genome.units if u.stem == stem)
        truth = genome.vowels[unit.vowel]
        measured = meas.get(stem, {}).get("formants_hz") or [None, None, None]
        for i, (mv, tv) in enumerate(zip(measured, truth)):
            tol = max(float(g["vowel_formant_tolerance_hz"]),
                      float(g["vowel_formant_tolerance_rel"]) * tv)
            err = _err(mv, tv)
            peaks.append({"stem": stem, "formant": f"F{i + 1}", "measured": mv,
                          "target": tv, "tolerance": tol, "error": err,
                          "pass": bool(err is not None and err <= tol)})
    n_pass = sum(1 for p in peaks if p["pass"])
    out["identity"] = {
        "metric": "vowel_formants", "n_peaks": len(peaks), "n_pass": n_pass,
        "required_pass": int(g["vowel_formant_min_pass_peaks"]),
        "peaks": peaks,
        "verdict": "PASS" if (n_pass >= int(g["vowel_formant_min_pass_peaks"])
                              and len(peaks) == int(g["vowel_formant_total_peaks"]))
        else "FAIL",
    }

    # --- §15.2 HL-α ---------------------------------------------------------
    hl_target = genome.hl_even_multiplier / genome.hl_odd_multiplier
    out["hl_alpha"] = _check_all(_rows(meas, VOWEL_STEMS, "hl_even_odd_ratio", hl_target),
                                 float(g["hl_even_odd_ratio_tol"]), "hl_even_odd_ratio")
    out["hl_alpha"]["target"] = hl_target

    # --- §15.3 AR-α / AR-β --------------------------------------------------
    alpha_rows = _rows(meas, VOWEL_STEMS, "ar_alpha_center_hz", genome.ar_alpha_center_hz)
    detected = [s for s in VOWEL_STEMS if meas.get(s, {}).get("ar_alpha_detected")]
    alpha = _check_all(alpha_rows, float(g["ar_alpha_center_tol_hz"]), "ar_alpha_center_hz")
    alpha["detected_contexts"] = detected
    alpha["required_contexts"] = int(g["ar_alpha_required_contexts"])
    if len(detected) < int(g["ar_alpha_required_contexts"]):
        alpha["verdict"] = "FAIL"
    out["ar_alpha"] = alpha

    beta = _check_all(_rows(meas, VOWEL_STEMS, "ar_beta_center_hz", genome.ar_beta_center_hz),
                      float(g["ar_beta_center_tol_hz"]), "ar_beta_center_hz")
    ratios = [meas.get(s, {}).get("beta_alpha_ratio") for s in VOWEL_STEMS]
    finite = [r for r in ratios if r is not None and math.isfinite(r)]
    median_ratio = float(np.median(finite)) if finite else None
    ratio_err = _err(median_ratio, genome.beta_alpha_energy_ratio)
    beta["ratio"] = {
        "metric": "beta_alpha_ratio", "aggregate": "median_over_vowels",
        "per_vowel": dict(zip(VOWEL_STEMS, ratios)), "measured": median_ratio,
        "target": genome.beta_alpha_energy_ratio,
        "tolerance": float(g["beta_alpha_ratio_tol"]), "error": ratio_err,
        "verdict": "PASS" if (ratio_err is not None
                              and ratio_err <= float(g["beta_alpha_ratio_tol"])) else "FAIL",
    }
    if beta["ratio"]["verdict"] == "FAIL":
        beta["verdict"] = "FAIL"
    out["ar_beta"] = beta

    # --- §15.4 F0 -----------------------------------------------------------
    out["f0_core"] = _check_all(_cent_rows(meas, stems, "core_f0_hz", genome.core_f0_hz),
                                float(g["f0_core_tol_cents"]), "core_f0_cents")
    out["terminal_f0"] = _check_all(
        _rows(meas, stems, "terminal_f0_delta_cents", genome.terminal_f0_delta_cents),
        float(g["terminal_f0_delta_tol_cents"]), "terminal_f0_delta_cents")

    # --- §15.5 Duration -----------------------------------------------------
    out["duration_onset"] = _check_all(_rows(meas, R_STEMS, "onset_ms", genome.onset_ms),
                                       float(g["duration_onset_tol_ms"]), "onset_ms")
    out["duration_share"] = _check_all(_rows(meas, R_STEMS, "r_onset_share",
                                             genome.r_onset_share),
                                       float(g["r_share_tol"]), "r_onset_share")

    # --- §15.6 Energy -------------------------------------------------------
    out["energy_sustain"] = _check_all(_rows(meas, stems, "sustain_dbfs", genome.sustain_dbfs),
                                       float(g["energy_sustain_tol_db"]), "sustain_dbfs")
    out["energy_attack"] = _check_all(_rows(meas, VOWEL_STEMS, "attack_ms", genome.attack_ms),
                                      float(g["energy_attack_tol_ms"]), "attack_ms")

    # --- §15.7 Release ------------------------------------------------------
    out["release"] = _check_all(_rows(meas, stems, "main_release_ms", genome.main_taper_ms),
                                float(g["release_tol_ms"]), "main_release_ms")

    # --- §15.8 AG-α ---------------------------------------------------------
    out["afterglow"] = _check_all(
        _rows(meas, stems, "afterglow_extra_ms", genome.ar_alpha_afterglow_extra_ms),
        float(g["afterglow_extra_tol_ms"]), "afterglow_extra_ms")

    zero_rows = []
    for stem in stems:
        m = meas.get(stem, {})
        trailing = m.get("trailing_zero_ms")
        ok = bool(m.get("final_sample_is_zero")) and trailing is not None \
            and trailing >= genome.terminal_zero_hold_ms - 1e-9
        zero_rows.append({"stem": stem, "trailing_zero_ms": trailing,
                          "final_sample_is_zero": m.get("final_sample_is_zero"), "pass": ok})
    out["terminal_zero"] = {
        "metric": "terminal_digital_zero", "required_ms": genome.terminal_zero_hold_ms,
        "rows": zero_rows, "failed_units": [r["stem"] for r in zero_rows if not r["pass"]],
        "verdict": "PASS" if all(r["pass"] for r in zero_rows) and zero_rows else "FAIL",
    }
    return out


# ---------------------------------------------------------------------------
# VoiceGenesis re-expression
# ---------------------------------------------------------------------------
def compare_reexpression(genome: FounderGenome, criteria: Criteria,
                         body: Mapping[str, Dict[str, Any]],
                         re: Mapping[str, Dict[str, Any]]) -> Dict[str, Any]:
    """§18.2 の re-expression Gate。Body 差で見る行と真値で見る行を混ぜない。"""
    g = criteria.reexpression_gates
    stems = [u.stem for u in genome.units]
    out: Dict[str, Any] = {}

    # --- 母音スペクトル同一性（Body <-> re-expression の cosine）------------
    cos_rows = []
    for stem in VOWEL_STEMS:
        a = body.get(stem, {}).get("spectral_identity")
        b = re.get(stem, {}).get("spectral_identity")
        cos = cosine_similarity(a, b) if (a and b) else None
        cos_rows.append({"stem": stem, "cosine": cos})
    cos_values = [r["cosine"] for r in cos_rows if r["cosine"] is not None]
    each_min = float(g["spectral_identity_cosine_each_min"])
    med_min = float(g["spectral_identity_cosine_median_min"])
    median_cos = float(np.median(cos_values)) if cos_values else None
    out["spectral_identity"] = {
        "metric": "envelope_cosine", "rows": cos_rows, "median": median_cos,
        "each_min": each_min, "median_min": med_min,
        "failed_units": [r["stem"] for r in cos_rows
                         if r["cosine"] is None or r["cosine"] < each_min],
        "verdict": "PASS" if (len(cos_values) == len(VOWEL_STEMS)
                              and all(c >= each_min for c in cos_values)
                              and median_cos is not None and median_cos >= med_min) else "FAIL",
    }

    def _delta_rows(stems_: Sequence[str], key: str) -> List[Dict[str, Any]]:
        rows = []
        for stem in stems_:
            bv, rv = body.get(stem, {}).get(key), re.get(stem, {}).get(key)
            err = None
            if bv is not None and rv is not None and math.isfinite(bv) and math.isfinite(rv):
                err = abs(rv - bv)
            rows.append({"stem": stem, "body": bv, "reexpressed": rv, "error": err,
                         "measured": rv, "target": bv})
        return rows

    out["hl_alpha"] = _check_all(_delta_rows(VOWEL_STEMS, "hl_even_odd_ratio"),
                                 float(g["hl_even_odd_ratio_delta_max"]), "hl_delta")
    alpha = _check_all(_delta_rows(VOWEL_STEMS, "ar_alpha_center_hz"),
                       float(g["ar_alpha_center_delta_hz_max"]), "ar_alpha_center_delta_hz")
    detected = [s for s in VOWEL_STEMS if re.get(s, {}).get("ar_alpha_detected")]
    alpha["detected_contexts"] = detected
    alpha["required_contexts"] = int(g["ar_alpha_required_contexts"])
    if len(detected) < int(g["ar_alpha_required_contexts"]):
        alpha["verdict"] = "FAIL"
    # AR-α は主 Founder Identity。AR-β だけ通っても Founder Identity は PASS に
    # しない（§18.2 末尾）。
    out["ar_alpha"] = alpha

    beta = _check_all(_delta_rows(VOWEL_STEMS, "ar_beta_center_hz"),
                      float(g["ar_beta_center_delta_hz_max"]), "ar_beta_center_delta_hz")
    # β/α 比の集計は `criteria.metric_definitions.founder_resonance.ratio_aggregate`
    # （= median_over_vowels）に従う。Body 側の判定と同じ集計を再表現側でも使う
    # （per-vowel で見ると、母音ごとの卓立分解の悪条件がそのまま差に乗る）。
    ratio_rows = _delta_rows(VOWEL_STEMS, "beta_alpha_ratio")
    body_vals = [r["body"] for r in ratio_rows
                 if r["body"] is not None and math.isfinite(r["body"])]
    re_vals = [r["reexpressed"] for r in ratio_rows
               if r["reexpressed"] is not None and math.isfinite(r["reexpressed"])]
    body_med = float(np.median(body_vals)) if len(body_vals) == len(VOWEL_STEMS) else None
    re_med = float(np.median(re_vals)) if len(re_vals) == len(VOWEL_STEMS) else None
    ratio_err = None if (body_med is None or re_med is None) else abs(re_med - body_med)
    beta["ratio"] = {
        "metric": "beta_alpha_ratio_delta", "aggregate": "median_over_vowels",
        "rows": ratio_rows, "body_median": body_med, "reexpressed_median": re_med,
        "worst": ratio_err, "tolerance": float(g["beta_alpha_ratio_delta_max"]),
        "verdict": "PASS" if (ratio_err is not None
                              and ratio_err <= float(g["beta_alpha_ratio_delta_max"]))
        else "FAIL",
    }
    if beta["ratio"]["verdict"] == "FAIL":
        beta["verdict"] = "FAIL"
    out["ar_beta"] = beta

    out["f0_core"] = _check_all(_cent_rows(re, stems, "core_f0_hz", genome.core_f0_hz),
                                float(g["f0_core_tol_cents"]), "core_f0_cents")
    out["terminal_f0"] = _check_all(
        _rows(re, stems, "terminal_f0_delta_cents", genome.terminal_f0_delta_cents),
        float(g["terminal_f0_delta_tol_cents"]), "terminal_f0_delta_cents")
    out["duration_onset"] = _check_all(_rows(re, R_STEMS, "onset_ms", genome.onset_ms),
                                       float(g["duration_onset_tol_ms"]), "onset_ms")
    out["duration_share"] = _check_all(_rows(re, R_STEMS, "r_onset_share",
                                             genome.r_onset_share),
                                       float(g["r_share_tol"]), "r_onset_share")
    out["energy_sustain"] = _check_all(_rows(re, stems, "sustain_dbfs", genome.sustain_dbfs),
                                       float(g["energy_sustain_tol_db"]), "sustain_dbfs")
    out["release"] = _check_all(_rows(re, stems, "main_release_ms", genome.main_taper_ms),
                                float(g["release_tol_ms"]), "main_release_ms")
    out["afterglow"] = _check_all(_delta_rows(stems, "afterglow_extra_ms"),
                                  float(g["afterglow_extra_delta_ms_max"]),
                                  "afterglow_extra_delta_ms")
    return out
