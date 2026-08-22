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

import hashlib
import json
import math
from pathlib import Path
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


#: §8 `body.pitch_dirs` の凍結値。**単一構成要素のディレクトリ名**に限る。
#:
#: 絶対パスや `..` を含む値を通すと、`write_body` の `root / genome.pitch_dir` が
#: staging の外を指し、G3 が落ちる前に固定名の WAV / oto.ini を外部へ書き出す
#: （しかもその書き込みは成果物ツリーの rollback では戻らない）。
FROZEN_PITCH_DIRS: Tuple[str, ...] = ("C4",)

#: §18 の許容値と §12 の取り込み要件の凍結値。**criteria ファイルはこの契約に
#: 一致しなければならない。**
#:
#: pin されたハッシュがあることだけを確認しても、`--criteria` に同じ schema の
#: 別文書を渡せば `vowel_formant_min_pass_peaks: 0`・巨大な許容値・負の cosine 下限
#: といった値で比較 Gate を素通りさせ、AF0 に帰属する偽の PASS を freeze まで
#: 通せてしまう。ハッシュは「どの文書だったか」を残すだけで「正しい文書か」は
#: 保証しない。
#:
#: `metric_definitions`（どう測るか）はここで凍結しない。測定方法の妥当性は
#: §17 の meter control が凍結値で弁別を実測して担保する（`af_gates.
#: REQUIRED_CONTROL_CONTRACT`）。凍結するのは「合否を決める閾値」だけ。
FROZEN_CRITERIA_IDENTITY: Dict[str, str] = {
    "schema": "voicegenesis-artificial-founder-criteria/1.1",
    "experiment_id": "AF-P0",
    "founder_id": "AF0",
}

FROZEN_BODY_GATES: Dict[str, Any] = {
    "vowel_formant_tolerance_hz": 40.0, "vowel_formant_tolerance_rel": 0.03,
    "vowel_formant_min_pass_peaks": 13, "vowel_formant_total_peaks": 15,
    "hl_even_odd_ratio_tol": 0.08, "ar_alpha_center_tol_hz": 50.0,
    "ar_alpha_required_contexts": 5, "ar_beta_center_tol_hz": 90.0,
    "beta_alpha_ratio_tol": 0.08, "f0_core_tol_cents": 5.0,
    "terminal_f0_delta_tol_cents": 15.0, "duration_onset_tol_ms": 2.0,
    "r_share_tol": 0.02, "energy_sustain_tol_db": 0.5, "energy_attack_tol_ms": 5.0,
    "release_tol_ms": 2.0, "afterglow_extra_tol_ms": 5.0, "terminal_zero_exact": True,
}

FROZEN_REEXPRESSION_GATES: Dict[str, Any] = {
    "spectral_identity_cosine_each_min": 0.85, "spectral_identity_cosine_median_min": 0.9,
    "hl_even_odd_ratio_delta_max": 0.15, "ar_alpha_center_delta_hz_max": 150.0,
    "ar_alpha_required_contexts": 4, "ar_beta_center_delta_hz_max": 220.0,
    "beta_alpha_ratio_delta_max": 0.15, "f0_core_tol_cents": 25.0,
    "terminal_f0_delta_tol_cents": 50.0, "duration_onset_tol_ms": 15.0,
    "r_share_tol": 0.08, "energy_sustain_tol_db": 2.0, "release_tol_ms": 30.0,
    "afterglow_extra_delta_ms_max": 20.0,
}

#: §A-7 で事前登録した `metric_definitions`（どう測るか）の凍結ダイジェスト。
#:
#: 第 6 巡では「測定方法の妥当性は §17 の meter control が担保する」として
#: この部分木を凍結しなかったが、**それは誤りだった**。control は 8 family の
#: metric しか実測しておらず、`spectral_identity`（G6 の再表現 cosine が使う
#: band / bands 数 / 平均除去）には対応する control が無い。したがって custom
#: criteria が `spectral_identity` を書き換えれば、G5 は PASS のまま G6 の判定
#: だけを緩められた。§15.3「結果を見て peak finder を変更しない」・§A-8 とも
#: 矛盾する。
#:
#: 値そのものではなくダイジェストで縛るのは、(a) 事前登録値の正本は
#: `criteria/AF_P0_CRITERIA.json`（リポジトリ内・provenance で hash pin 済み）
#: であり二重管理を避けたい、(b) 計器を変えるときは **この定数も意図的に
#: 更新する** という 2 箇所編集を強制したい（§15.3 の規律をコードで表す）、
#: の 2 点による。同梱 criteria から再計算して一致することはテストが固定する。
FROZEN_METRIC_DEFINITIONS_SHA256 = "da96ef853dbf63cb2173168eb54db4037c95e3a450ac58698409118592d0c2f9"

FROZEN_INGESTION: Dict[str, Any] = {
    "required_onsets": ["k", "s", "n", "r"],
    "required_vowels": ["a", "i", "u", "e", "o"],
    "expected_wav_count": 25, "expected_oto_entries": 25,
    "join_smoke_sequence": ["か", "り", "あ"],
}

#: §2 で凍結された AF0 の同一性。`p0_run` の公開先は `voicebank/AF0` に **固定**
#: されており、比較層は「spec に書かれた値」をそのまま目標値に使う。したがって
#: ここを縛らないと、別 founder_id・別表現型の `--spec` が G1–G3 を通り、
#: 自分自身を目標にして全 Body 比較へ PASS し、canonical な AF0 公開場所を
#: 置き換えたうえで「Artificial Founder AF0 ESTABLISHED」を凍結できてしまう。
FROZEN_FOUNDER_IDENTITY: Dict[str, str] = {
    "schema": SPEC_SCHEMA,
    "founder_id": "AF0",
    "phenotype_codename": "Dual Resonance / Afterglow",
}

#: 凍結 AF0 spec（`founder_specs/AF0.json`）の正準ダイジェスト。
#: §16 前文「結果を見て AF0 特徴・候補・許容値・Gate を都合よく変更しては
#: ならない」を **数値 1 個単位で** 効かせる唯一の形。criteria の
#: `metric_definitions`（§A-8）と同じ扱いで、表現型を動かすには
#: この定数の意図的な更新を伴わなければならない。
FROZEN_SPEC_SHA256 = "c477fd5a9ec2ac3dd97f2c7ea076568acce19b53c28eed5c10175cc807b5e8d4"


def is_safe_name(name: Any) -> bool:
    """パス区切り・`..`・絶対パスを含まない単一構成要素名か。"""
    if not isinstance(name, str) or not name or name in (".", ".."):
        return False
    if "/" in name or "\\" in name or "\x00" in name:
        return False
    return Path(name).name == name and not Path(name).is_absolute()


def _canonical_digest(payload: Any) -> str:
    """JSON 部分木の正準ダイジェスト（キー順・空白非依存）。"""
    canon = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def metric_definitions_digest(metric_definitions: Any) -> str:
    """`metric_definitions` 部分木の正準ダイジェスト（キー順非依存）。"""
    return _canonical_digest(metric_definitions)


def founder_spec_digest(spec: Any) -> str:
    """Founder spec 文書全体の正準ダイジェスト（整形差では変わらない）。"""
    return _canonical_digest(spec)


def validate_criteria(criteria: Any) -> List[str]:
    """criteria が AF-P0 の凍結契約と一致するか検査し、**全件**返す（空 = 合格）。"""
    errors: List[str] = []
    if not isinstance(criteria, dict):
        return [f"criteria: expected object, got {type(criteria).__name__}"]
    for key, want in FROZEN_CRITERIA_IDENTITY.items():
        if criteria.get(key) != want:
            errors.append(f"criteria.{key}: expected {want!r}, got {criteria.get(key)!r}")
    for section, frozen in (("body_gates", FROZEN_BODY_GATES),
                            ("reexpression_gates", FROZEN_REEXPRESSION_GATES),
                            ("ingestion", FROZEN_INGESTION)):
        got = criteria.get(section)
        if not isinstance(got, dict):
            errors.append(f"criteria.{section}: expected object")
            continue
        for key in sorted(set(frozen) | set(got)):
            if key not in frozen:
                errors.append(f"criteria.{section}.{key}: unknown key")
            elif key not in got:
                errors.append(f"criteria.{section}.{key}: missing key")
            elif got[key] != frozen[key]:
                errors.append(
                    f"criteria.{section}.{key}: expected {frozen[key]!r}, got {got[key]!r}")
    md = criteria.get("metric_definitions")
    if md is None:
        errors.append("criteria.metric_definitions: missing section")
    else:
        got = metric_definitions_digest(md)
        if got != FROZEN_METRIC_DEFINITIONS_SHA256:
            errors.append(
                "criteria.metric_definitions: does not match the frozen AF-P0 measurement "
                f"contract (expected sha256 {FROZEN_METRIC_DEFINITIONS_SHA256}, got {got}); "
                "changing the meter requires deliberately updating "
                "af_schema.FROZEN_METRIC_DEFINITIONS_SHA256 as well (§15.3 / §A-8)")
    return errors


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

    # --- 凍結 AF0 同一性 ------------------------------------------------------
    # 公開先が `voicebank/AF0` 固定である以上、「どの Founder の spec か」は
    # 任意入力にしてはならない（別個体を AF0 として公開できてしまう）。
    for key, want in FROZEN_FOUNDER_IDENTITY.items():
        got = spec.get(key)
        if got != want:
            errors.append(
                f"root.{key}: AF-P0 は凍結 AF0 の spec のみ受け付ける "
                f"(expected {want!r}, got {got!r})")

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
    if isinstance(pdirs, list):
        unsafe = [d for d in pdirs if not is_safe_name(d)]
        _require(not unsafe,
                 f"root.body.pitch_dirs: must be single-component directory names "
                 f"(no separators, no '..', no absolute paths); got {unsafe}", errors)
        _require(tuple(pdirs) == FROZEN_PITCH_DIRS,
                 f"root.body.pitch_dirs: must equal the frozen AF-P0 value "
                 f"{list(FROZEN_PITCH_DIRS)}, got {pdirs}", errors)
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


def validate_pinned_founder_spec(spec: Dict[str, Any]) -> List[str]:
    """**run の pinned `--spec`** を検査する（構造 + 凍結表現型）。

    `validate_founder_spec` との違いは凍結ダイジェストの有無。範囲検査は
    「壊れていないこと」しか見ず、比較層は spec の値をそのまま目標値に使うため、
    範囲内で AR-alpha を 3400 -> 2500 Hz に動かした spec は自分自身に一致して
    Body 比較を全 PASS し、`voicebank/AF0`（公開先は固定）を置き換えて
    「AF0 ESTABLISHED」を凍結できてしまう。§16 前文を効かせるには文書全体を縛る。

    ダイジェストを `validate_founder_spec` 側に置いてはならない: §17 の計器較正は
    **spec を意図的に patch した** fixture を `genome_from_dict` に通すので、
    そこで凍結を要求すると較正そのものが不可能になる。凍結は「この run の被験体
    そのものか」を問う入口の検査であり、派生 fixture の検査ではない。
    """
    errors = validate_founder_spec(spec)
    if errors:
        # 構造が壊れているならダイジェスト不一致は自明。原因だけを返す。
        return errors
    got = founder_spec_digest(spec)
    if got != FROZEN_SPEC_SHA256:
        errors.append(
            "root: does not match the frozen AF-P0 founder spec "
            f"(expected sha256 {FROZEN_SPEC_SHA256}, got {got}); "
            "AF0 の表現型を動かすには af_schema.FROZEN_SPEC_SHA256 の意図的な "
            "更新を伴わなければならない（§16 前文 / §2）")
    return errors


def require_valid_founder_spec(spec: Dict[str, Any]) -> None:
    """`validate_founder_spec` が非空なら `SpecError` を送出する（fail-closed 入口）。"""
    errs = validate_founder_spec(spec)
    if errs:
        raise SpecError(errs)
