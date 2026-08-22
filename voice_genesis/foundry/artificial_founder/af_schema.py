"""af_schema.py — AF0 Founder Genome v0.2 の fail-closed スキーマ検査（設計書 §8 / §19 G1）。

**このモジュールは I/O を持たない。** 受け取るのは既に読み込まれた `dict` だけで、
ファイルパスも音響処理も持たない。理由は G1（SPEC_VALID）を「値の検査」だけで
再現できるようにするため（`af_spec` が読み込み、`af_gates` が判定を持ち上げる）。

fail-closed の原則:

- 未知キーは**全階層で拒否**する（typo による黙殺を作らない）。
- 数値は `math.isfinite` を通す（NaN / Inf を拒否）。`float("nan")` は比較を
  すべて False にするため、範囲検査だけでは素通りする。
- 範囲・順序・整合（`r_onset_ms + i_vowel_ms == articulation_ms` 等）を検査する。
- 共鳴中心は Nyquist 未満、帯域幅は正。

エラーは 1 件目で打ち切らず**全件収集**して返す（設計書 §19 の「fail-closed 検査」は
「最初の 1 件だけ報告して残りを隠す」ことを意味しない）。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence, Tuple

SPEC_SCHEMA = "voicegenesis-artificial-founder/0.2"

#: §8 の JSON をそのまま写した「許可キー」。ここに無いキーは拒否する。
_ALLOWED: Dict[str, Any] = {
    "schema": None,
    "founder_id": None,
    "phenotype_codename": None,
    "origin": {
        "kind": None,
        "human_audio_used": None,
        "speaker_specific_parameters_used": None,
        "pretrained_voice_model_used": None,
        "external_voicebank_used": None,
        "generic_phonetic_topology_used": None,
    },
    "generator": {
        "sample_rate_hz": None,
        "channels": None,
        "pcm_format": None,
        "canonical_pitch_name": None,
        "canonical_pitch_hz": None,
        "harmonic_count": None,
        "harmonic_rolloff_power": None,
        "global_seed": None,
        # 実装追補（DESIGN_AF_P0.md §A-1）: §2 の "stochastic breath/noise:
        # minimal and deterministic" を実現するための 2 フィールド。phenotype
        # 数値ではなく生成器パラメータ。
        "harmonic_phase_scheme": None,
        "breath_noise_db_rel_sustain": None,
    },
    "founder_source_traits": {
        "HL-alpha": {
            "kind": None,
            "odd_multiplier": None,
            "even_multiplier": None,
            "scope": None,
        },
    },
    "identity_signature": {
        "vowels": "*",  # "*" = 任意キー（母音ラベル）を許可する葉
        "vowel_formant_bandwidths_hz": None,
        "founder_resonances": {
            "AR-alpha": {"center_hz": None, "bandwidth_hz": None, "gain_db": None},
            "AR-beta": {
                "center_hz": None,
                "bandwidth_hz": None,
                "beta_alpha_energy_ratio": None,
            },
        },
    },
    "performance_genes": {
        "f0": {"core_f0_hz": None, "vibrato_depth_cents": None, "jitter_cents": None},
        "duration": {
            "target_sequence": None,
            "articulation_ms": None,
            "r_onset_ms": None,
            "i_vowel_ms": None,
            "r_onset_share": None,
        },
        "energy": {"attack_ms": None, "sustain_dbfs": None, "sustain_ripple_db": None},
        "release": {"main_taper_ms": None, "curve": None},
    },
    "founder_expression_traits": {
        "AG-alpha": {
            "terminal_window_ms": None,
            "terminal_f0_delta_cents": None,
            "ar_alpha_afterglow_extra_ms": None,
            "terminal_zero_hold_ms": None,
        },
    },
    "body": {
        "format": None,
        "pitch_dirs": None,
        "lead_zero_ms": None,
        "tail_zero_ms": None,
    },
    "inventory": {"aliases": None},
}

_ALLOWED_VOWELS: Tuple[str, ...] = ("a", "i", "u", "e", "o")

#: §8 `inventory.aliases` の凍結値。**順序込みで完全一致**を要求する。
#:
#: 部分集合を通すと、G1 を抜けた縮小 genome が `validate_body` の期待数
#: （`len(genome.units)` 由来）まで一緒に縮め、25 unit の Body 契約を検査せずに
#: G3 まで PASS してしまう。そのまま G0–G3 の公開前提が揃うと、**不完全な
#: voicebank が完全な旧 bundle を置き換える**。さらに小さい部分集合では
#: `af_compare.compare_body` が `next(...)` で落ちる。
FROZEN_ALIASES: Tuple[str, ...] = (
    "あ", "い", "う", "え", "お",
    "か", "き", "く", "け", "こ",
    "さ", "し", "す", "せ", "そ",
    "な", "に", "ぬ", "ね", "の",
    "ら", "り", "る", "れ", "ろ",
)
_RELEASE_CURVES: Tuple[str, ...] = ("half_cosine",)
_PHASE_SCHEMES: Tuple[str, ...] = ("schroeder", "zero")


class SpecError(ValueError):
    """G1 SPEC_VALID の不合格。`errors` に全件の理由を持つ。"""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors: List[str] = list(errors)
        super().__init__("; ".join(self.errors))


# ---------------------------------------------------------------------------
# 低レベルヘルパ
# ---------------------------------------------------------------------------
def _walk_unknown(obj: Any, allowed: Any, path: str, errors: List[str]) -> None:
    """許可テーブルと突き合わせて未知キー / 欠落キーを収集する。"""
    if allowed == "*":
        return
    if allowed is None:
        return
    if not isinstance(obj, dict):
        errors.append(f"{path}: expected object, got {type(obj).__name__}")
        return
    for key in obj:
        if key not in allowed:
            errors.append(f"{path}.{key}: unknown key")
    for key, sub in allowed.items():
        if key not in obj:
            errors.append(f"{path}.{key}: missing key")
            continue
        _walk_unknown(obj[key], sub, f"{path}.{key}", errors)


def _finite_scan(obj: Any, path: str, errors: List[str]) -> None:
    """全階層の数値を走査し NaN / Inf を拒否する。

    `bool` は `int` のサブクラスなので数値扱いしない（`origin.*` のフラグ）。
    """
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        if not math.isfinite(float(obj)):
            errors.append(f"{path}: non-finite number ({obj!r})")
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            _finite_scan(v, f"{path}.{k}", errors)
        return
    if isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _finite_scan(v, f"{path}[{i}]", errors)


def _num(obj: Dict[str, Any], key: str, path: str, errors: List[str]) -> float:
    v = obj.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        errors.append(f"{path}.{key}: expected number, got {type(v).__name__}")
        return math.nan
    return float(v)


def _require(cond: bool, msg: str, errors: List[str]) -> None:
    if not cond:
        errors.append(msg)


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------
def validate_founder_spec(spec: Dict[str, Any]) -> List[str]:
    """AF0 Founder Genome を検査し、**エラー全件**のリストを返す（空 = 合格）。"""
    errors: List[str] = []
    if not isinstance(spec, dict):
        return [f"root: expected object, got {type(spec).__name__}"]

    _walk_unknown(spec, _ALLOWED, "root", errors)
    _finite_scan(spec, "root", errors)
    if errors:
        # 構造が壊れている状態で値検査へ進むと KeyError で全件収集できない。
        return errors

    if spec["schema"] != SPEC_SCHEMA:
        errors.append(f"root.schema: expected {SPEC_SCHEMA!r}, got {spec['schema']!r}")
    if not isinstance(spec["founder_id"], str) or not spec["founder_id"]:
        errors.append("root.founder_id: expected non-empty string")

    # --- origin (§19 G0 の宣言部。tripwire 側の検証は af_gates) ---------------
    origin = spec["origin"]
    for k in ("human_audio_used", "speaker_specific_parameters_used",
              "pretrained_voice_model_used", "external_voicebank_used"):
        if origin[k] is not False:
            errors.append(f"root.origin.{k}: must be false (Source-Free)")
    if origin["kind"] != "procedural_source_filter":
        errors.append("root.origin.kind: must be 'procedural_source_filter'")
    if origin["generic_phonetic_topology_used"] is not True:
        errors.append("root.origin.generic_phonetic_topology_used: must be true")

    # --- generator -----------------------------------------------------------
    gen = spec["generator"]
    sr = _num(gen, "sample_rate_hz", "root.generator", errors)
    _require(sr == 44100.0, "root.generator.sample_rate_hz: must be 44100", errors)
    _require(gen["channels"] == 1, "root.generator.channels: must be 1", errors)
    _require(gen["pcm_format"] == "s16le", "root.generator.pcm_format: must be 's16le'", errors)
    pitch_hz = _num(gen, "canonical_pitch_hz", "root.generator", errors)
    _require(pitch_hz > 0.0, "root.generator.canonical_pitch_hz: must be > 0", errors)
    hcount = gen["harmonic_count"]
    _require(isinstance(hcount, int) and not isinstance(hcount, bool) and hcount >= 1,
             "root.generator.harmonic_count: must be a positive integer", errors)
    rolloff = _num(gen, "harmonic_rolloff_power", "root.generator", errors)
    _require(rolloff > 0.0, "root.generator.harmonic_rolloff_power: must be > 0", errors)
    seed = gen["global_seed"]
    _require(isinstance(seed, int) and not isinstance(seed, bool) and seed >= 0,
             "root.generator.global_seed: must be a non-negative integer", errors)
    _require(gen["harmonic_phase_scheme"] in _PHASE_SCHEMES,
             f"root.generator.harmonic_phase_scheme: must be one of {_PHASE_SCHEMES}", errors)
    noise_db = _num(gen, "breath_noise_db_rel_sustain", "root.generator", errors)
    _require(noise_db < 0.0,
             "root.generator.breath_noise_db_rel_sustain: must be < 0 dB (minimal breath)", errors)

    nyquist = sr / 2.0 if math.isfinite(sr) else math.inf
    if math.isfinite(sr) and math.isfinite(pitch_hz) and isinstance(hcount, int):
        top = pitch_hz * hcount
        _require(top < nyquist,
                 f"root.generator: harmonic_count*canonical_pitch_hz={top:.1f} Hz "
                 f"exceeds Nyquist {nyquist:.1f} Hz", errors)

    # --- HL-alpha ------------------------------------------------------------
    hl = spec["founder_source_traits"]["HL-alpha"]
    _require(hl["kind"] == "odd_even_harmonic_lattice",
             "root.founder_source_traits.HL-alpha.kind: must be 'odd_even_harmonic_lattice'", errors)
    _require(hl["scope"] == "all_voiced_source",
             "root.founder_source_traits.HL-alpha.scope: must be 'all_voiced_source'", errors)
    odd = _num(hl, "odd_multiplier", "root.founder_source_traits.HL-alpha", errors)
    even = _num(hl, "even_multiplier", "root.founder_source_traits.HL-alpha", errors)
    _require(odd > 0.0, "root.founder_source_traits.HL-alpha.odd_multiplier: must be > 0", errors)
    _require(even > 0.0, "root.founder_source_traits.HL-alpha.even_multiplier: must be > 0", errors)

    # --- identity signature --------------------------------------------------
    idsig = spec["identity_signature"]
    vowels = idsig["vowels"]
    if not isinstance(vowels, dict) or not vowels:
        errors.append("root.identity_signature.vowels: expected non-empty object")
    else:
        for name in sorted(vowels):
            if name not in _ALLOWED_VOWELS:
                errors.append(f"root.identity_signature.vowels.{name}: unknown vowel label")
                continue
            fs = vowels[name]
            p = f"root.identity_signature.vowels.{name}"
            if not isinstance(fs, list) or len(fs) != 3:
                errors.append(f"{p}: expected [F1, F2, F3]")
                continue
            if any(isinstance(f, bool) or not isinstance(f, (int, float)) for f in fs):
                errors.append(f"{p}: formants must be numbers")
                continue
            f1, f2, f3 = (float(f) for f in fs)
            _require(f1 > 0.0, f"{p}: F1 must be > 0", errors)
            _require(f1 < f2 < f3, f"{p}: formants must be strictly ascending (F1<F2<F3)", errors)
            _require(f3 < nyquist, f"{p}: F3 must be below Nyquist {nyquist:.1f} Hz", errors)
        for name in _ALLOWED_VOWELS:
            if name not in vowels:
                errors.append(f"root.identity_signature.vowels.{name}: missing vowel")

    bws = idsig["vowel_formant_bandwidths_hz"]
    if not isinstance(bws, list) or len(bws) != 3:
        errors.append("root.identity_signature.vowel_formant_bandwidths_hz: expected 3 values")
    else:
        for i, b in enumerate(bws):
            if isinstance(b, bool) or not isinstance(b, (int, float)) or float(b) <= 0.0:
                errors.append(
                    f"root.identity_signature.vowel_formant_bandwidths_hz[{i}]: must be > 0")

    res = idsig["founder_resonances"]
    ar_a, ar_b = res["AR-alpha"], res["AR-beta"]
    a_c = _num(ar_a, "center_hz", "root...AR-alpha", errors)
    a_bw = _num(ar_a, "bandwidth_hz", "root...AR-alpha", errors)
    _num(ar_a, "gain_db", "root...AR-alpha", errors)
    b_c = _num(ar_b, "center_hz", "root...AR-beta", errors)
    b_bw = _num(ar_b, "bandwidth_hz", "root...AR-beta", errors)
    ratio = _num(ar_b, "beta_alpha_energy_ratio", "root...AR-beta", errors)
    _require(0.0 < a_c < nyquist,
             f"root.identity_signature.founder_resonances.AR-alpha.center_hz: "
             f"must satisfy 0 < center < Nyquist ({nyquist:.1f} Hz)", errors)
    _require(0.0 < b_c < nyquist,
             f"root.identity_signature.founder_resonances.AR-beta.center_hz: "
             f"must satisfy 0 < center < Nyquist ({nyquist:.1f} Hz)", errors)
    _require(a_bw > 0.0,
             "root.identity_signature.founder_resonances.AR-alpha.bandwidth_hz: must be > 0", errors)
    _require(b_bw > 0.0,
             "root.identity_signature.founder_resonances.AR-beta.bandwidth_hz: must be > 0", errors)
    _require(0.0 < ratio <= 1.0,
             "root.identity_signature.founder_resonances.AR-beta.beta_alpha_energy_ratio: "
             "must satisfy 0 < ratio <= 1", errors)

    # --- performance genes ---------------------------------------------------
    pg = spec["performance_genes"]
    f0 = pg["f0"]
    core = _num(f0, "core_f0_hz", "root.performance_genes.f0", errors)
    _require(core > 0.0, "root.performance_genes.f0.core_f0_hz: must be > 0", errors)
    _require(_num(f0, "vibrato_depth_cents", "root.performance_genes.f0", errors) == 0.0,
             "root.performance_genes.f0.vibrato_depth_cents: AF0 requires 0 (§2)", errors)
    _require(_num(f0, "jitter_cents", "root.performance_genes.f0", errors) == 0.0,
             "root.performance_genes.f0.jitter_cents: AF0 requires 0 (§2)", errors)
    if math.isfinite(core) and math.isfinite(pitch_hz):
        _require(abs(core - pitch_hz) < 1e-9,
                 "root.performance_genes.f0.core_f0_hz: must equal generator.canonical_pitch_hz",
                 errors)

    dur = pg["duration"]
    seq = dur["target_sequence"]
    _require(isinstance(seq, list) and all(isinstance(s, str) and s for s in seq) and len(seq) == 2,
             "root.performance_genes.duration.target_sequence: expected 2 phoneme labels", errors)
    art = _num(dur, "articulation_ms", "root.performance_genes.duration", errors)
    r_on = _num(dur, "r_onset_ms", "root.performance_genes.duration", errors)
    i_vow = _num(dur, "i_vowel_ms", "root.performance_genes.duration", errors)
    share = _num(dur, "r_onset_share", "root.performance_genes.duration", errors)
    _require(art > 0.0, "root.performance_genes.duration.articulation_ms: must be > 0", errors)
    _require(r_on > 0.0, "root.performance_genes.duration.r_onset_ms: must be > 0", errors)
    _require(i_vow > 0.0, "root.performance_genes.duration.i_vowel_ms: must be > 0", errors)
    _require(0.0 < share < 1.0,
             "root.performance_genes.duration.r_onset_share: must satisfy 0 < share < 1", errors)
    if all(math.isfinite(v) for v in (art, r_on, i_vow, share)):
        _require(abs((r_on + i_vow) - art) < 1e-9,
                 "root.performance_genes.duration: r_onset_ms + i_vowel_ms must equal "
                 "articulation_ms", errors)
        _require(abs(r_on / art - share) < 1e-9,
                 "root.performance_genes.duration: r_onset_ms / articulation_ms must equal "
                 "r_onset_share", errors)

    en = pg["energy"]
    attack = _num(en, "attack_ms", "root.performance_genes.energy", errors)
    sustain = _num(en, "sustain_dbfs", "root.performance_genes.energy", errors)
    ripple = _num(en, "sustain_ripple_db", "root.performance_genes.energy", errors)
    _require(attack > 0.0, "root.performance_genes.energy.attack_ms: must be > 0", errors)
    _require(sustain < 0.0, "root.performance_genes.energy.sustain_dbfs: must be < 0 dBFS", errors)
    _require(ripple == 0.0,
             "root.performance_genes.energy.sustain_ripple_db: AF0 requires 0 (§2)", errors)

    rel = pg["release"]
    taper = _num(rel, "main_taper_ms", "root.performance_genes.release", errors)
    _require(taper > 0.0, "root.performance_genes.release.main_taper_ms: must be > 0", errors)
    _require(rel["curve"] in _RELEASE_CURVES,
             f"root.performance_genes.release.curve: must be one of {_RELEASE_CURVES}", errors)

    # --- AG-alpha ------------------------------------------------------------
    ag = spec["founder_expression_traits"]["AG-alpha"]
    tw = _num(ag, "terminal_window_ms", "root...AG-alpha", errors)
    _num(ag, "terminal_f0_delta_cents", "root...AG-alpha", errors)
    extra = _num(ag, "ar_alpha_afterglow_extra_ms", "root...AG-alpha", errors)
    hold = _num(ag, "terminal_zero_hold_ms", "root...AG-alpha", errors)
    _require(tw > 0.0,
             "root.founder_expression_traits.AG-alpha.terminal_window_ms: must be > 0", errors)
    _require(extra >= 0.0,
             "root.founder_expression_traits.AG-alpha.ar_alpha_afterglow_extra_ms: "
             "must be >= 0", errors)
    _require(hold > 0.0,
             "root.founder_expression_traits.AG-alpha.terminal_zero_hold_ms: must be > 0", errors)
    if math.isfinite(tw) and math.isfinite(art):
        _require(tw <= art,
                 "root.founder_expression_traits.AG-alpha.terminal_window_ms: must not exceed "
                 "performance_genes.duration.articulation_ms", errors)

    # --- body / inventory ----------------------------------------------------
    body = spec["body"]
    _require(body["format"] == "utau-classic-cv", "root.body.format: must be 'utau-classic-cv'",
             errors)
    pdirs = body["pitch_dirs"]
    _require(isinstance(pdirs, list) and len(pdirs) == 1
             and isinstance(pdirs[0], str) and pdirs[0],
             "root.body.pitch_dirs: P0 requires exactly 1 pitch dir (§19 G3)", errors)
    lead = _num(body, "lead_zero_ms", "root.body", errors)
    tail = _num(body, "tail_zero_ms", "root.body", errors)
    _require(lead > 0.0, "root.body.lead_zero_ms: must be > 0", errors)
    _require(tail > 0.0, "root.body.tail_zero_ms: must be > 0", errors)
    if math.isfinite(tail) and math.isfinite(hold):
        _require(abs(tail - hold) < 1e-9,
                 "root.body.tail_zero_ms must equal "
                 "founder_expression_traits.AG-alpha.terminal_zero_hold_ms", errors)

    aliases = spec["inventory"]["aliases"]
    if not isinstance(aliases, list) or not aliases:
        errors.append("root.inventory.aliases: expected non-empty list")
    elif any(not isinstance(a, str) or not a for a in aliases):
        errors.append("root.inventory.aliases: all aliases must be non-empty strings")
    elif len(set(aliases)) != len(aliases):
        errors.append("root.inventory.aliases: duplicate alias")
    elif tuple(aliases) != FROZEN_ALIASES:
        missing = [a for a in FROZEN_ALIASES if a not in set(aliases)]
        extra = [a for a in aliases if a not in set(FROZEN_ALIASES)]
        errors.append(
            "root.inventory.aliases: must equal the frozen AF-P0 inventory in order "
            f"(missing={missing}, unexpected={extra}, n={len(aliases)}/{len(FROZEN_ALIASES)})")

    return errors


def require_valid_founder_spec(spec: Dict[str, Any]) -> None:
    """`validate_founder_spec` が非空なら `SpecError` を送出する（fail-closed 入口）。"""
    errs = validate_founder_spec(spec)
    if errs:
        raise SpecError(errs)
