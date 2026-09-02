"""§5.1 の primary / boundary 軸水準 + §2.7 の anchor 水準・family 別 truth 値の
凍結定数（設計正本 §5.1, §5.2 / IMPLEMENTATION_MAP §2.7）。

本モジュールは値のみを持つ（生成ロジックは `matrix.py` / `generators/*.py`）。
数値は設計正本の記載を機械転記したもの。転記元にない具体形（TRANSITION の
severity 数値・IDENTITY founder bundle 等）は `[UNDERSPEC-CAL-Bnn]` で明示する。
"""

from __future__ import annotations

from enum import Enum

# ---------------------------------------------------------------------------
# 共通軸（§5.1）
# ---------------------------------------------------------------------------

# Primary F0: C3, G3, C4, G4 (Hz, 設計正本記載の精度をそのまま転記)
PRIMARY_F0_HZ: tuple[float, ...] = (130.813, 195.998, 261.626, 391.995)
PRIMARY_F0_LABELS: tuple[str, ...] = ("C3", "G3", "C4", "G4")

PRIMARY_SR_HZ: tuple[int, ...] = (24000, 44100, 48000)
PRIMARY_GAIN_DBFS: tuple[float, ...] = (-24.0, -12.0, -6.0)
PRIMARY_DURATION_S: tuple[float, ...] = (0.25, 0.50, 1.00, 2.00)
# "clean" または SNR dB (int)。noise_clean=True <=> "clean"。
PRIMARY_NOISE_SNR_DB: tuple[int, ...] = (40, 20, 10)
PRIMARY_CONTEXT: tuple[str, ...] = (
    "steady-isolated",
    "20ms-cosine-ramp",
    "100ms-voiced-prefix/suffix",
    "transition-adjacent",
)

# Boundary
BOUNDARY_F0_HZ: tuple[float, ...] = (97.999, 523.251)  # G2, C5
BOUNDARY_F0_LABELS: tuple[str, ...] = ("G2", "C5")
BOUNDARY_SR_HZ: tuple[int, ...] = (16000, 96000)
BOUNDARY_GAIN_DBFS: tuple[float, ...] = (-36.0, -1.0)
BOUNDARY_DURATION_S: tuple[float, ...] = (0.10, 4.00)
BOUNDARY_NOISE_SNR_DB: int = 0

# negative control 系列の "too-short" / "invalid-SR" は boundary probe より
# さらに外側の値を使う（boundary 探査点と negative 探査点を区別する）。
# [UNDERSPEC-CAL-B01] 設計正本は too-short/invalid-SR の具体数値までは規定しない。
NEGATIVE_TOO_SHORT_DURATION_S: float = 0.02
NEGATIVE_INVALID_SR_HZ: int = 8000

# nuisance / interaction 系列内で使う primary 側の extremum 値（§5.1 で言及される
# "short-duration" / "low-SNR" / "low-gain" の具体値）。
PRIMARY_DURATION_MIN_S: float = min(PRIMARY_DURATION_S)
PRIMARY_NOISE_SNR_MIN_DB: int = min(PRIMARY_NOISE_SNR_DB)
PRIMARY_GAIN_MIN_DBFS: float = min(PRIMARY_GAIN_DBFS)
PRIMARY_F0_LOW_HZ: float = min(PRIMARY_F0_HZ)  # C3
PRIMARY_F0_HIGH_HZ: float = max(PRIMARY_F0_HZ)  # G4

# [UNDERSPEC-CAL-B02] 設計正本 §5.1 の "high-F0×low-SR" 等 targeted interaction は
# 具体的な SR 極値を指定しない。primary anchor SR (48000) が既に primary domain の
# 最大値であるため、"low-SR/high-SR" を primary 極値のみで表現すると
# (低truth F0 anchor family で) anchor 行と完全一致する退化ケースが生じる
# (§2.7 の row_id 一意性要件に抵触)。boundary SR 極値 (16000/96000) を
# interaction 用の low-SR/high-SR として採用し、この退化を構造的に回避する。
INTERACTION_SR_LOW_HZ: int = min(BOUNDARY_SR_HZ)
INTERACTION_SR_HIGH_HZ: int = max(BOUNDARY_SR_HZ)

ANCHOR_SR_HZ: int = 48000
ANCHOR_GAIN_DBFS: float = -12.0
ANCHOR_DURATION_S: float = 1.00
ANCHOR_CONTEXT: str = "steady-isolated"

# ---------------------------------------------------------------------------
# 正準 nuisance 系列（§5.1: confound block の 11 行。記載順）
# ---------------------------------------------------------------------------

CANONICAL_NUISANCE_SEQUENCE: tuple[tuple[str, str, object], ...] = (
    ("gain", "gain_dbfs", -24.0),
    ("gain", "gain_dbfs", -6.0),
    ("duration", "duration_s", 0.25),
    ("duration", "duration_s", 0.50),
    ("duration", "duration_s", 2.00),
    ("noise", "noise_snr_db", 40),
    ("noise", "noise_snr_db", 20),
    ("noise", "noise_snr_db", 10),
    ("context", "context", "20ms-cosine-ramp"),
    ("context", "context", "100ms-voiced-prefix/suffix"),
    ("context", "context", "transition-adjacent"),
)
"""各要素 = (axis_family, field_name, value)。axis_family は §2.7 の除外規則
（APERIODICITY は "noise" 除外・TRANSITION は "context" 除外）に使う。"""

# 6 targeted interactions（§5.1 記載順）。値は §5.1/UNDERSPEC-CAL-B02 の解決に従う。
TARGETED_INTERACTIONS: tuple[tuple[str, str, dict[str, object]], ...] = (
    ("high-F0×low-SR", "f0_sr", {"f0_hz": PRIMARY_F0_HIGH_HZ, "sr_hz": INTERACTION_SR_LOW_HZ}),
    (
        "high-F0×short-duration",
        "f0_duration",
        {"f0_hz": PRIMARY_F0_HIGH_HZ, "duration_s": PRIMARY_DURATION_MIN_S},
    ),
    (
        "high-F0×low-SNR",
        "f0_noise",
        {
            "f0_hz": PRIMARY_F0_HIGH_HZ,
            "noise_snr_db": PRIMARY_NOISE_SNR_MIN_DB,
            "noise_clean": False,
        },
    ),
    ("low-F0×high-SR", "f0_sr", {"f0_hz": PRIMARY_F0_LOW_HZ, "sr_hz": INTERACTION_SR_HIGH_HZ}),
    (
        "low-gain×noise",
        "gain_noise",
        {
            "gain_dbfs": PRIMARY_GAIN_MIN_DBFS,
            "noise_snr_db": PRIMARY_NOISE_SNR_MIN_DB,
            "noise_clean": False,
        },
    ),
    (
        "transition×short-duration",
        "context_duration",
        {"context": "transition-adjacent", "duration_s": PRIMARY_DURATION_MIN_S},
    ),
)
"""各要素 = (name, axis_family_for_exclusion_check, field_overrides)。
axis_family は当該 interaction がどの除外対象軸に触れるかの判定用
（`context_duration` は TRANSITION の context 除外規則で弾かれる。noise 除外
規則で弾かれるのは `f0_noise` / `gain_noise`）。"""

# ---------------------------------------------------------------------------
# 正準 boundary/negative 系列（§2.7）
# ---------------------------------------------------------------------------

CANONICAL_BOUNDARY_SEQUENCE: tuple[tuple[str, str, object], ...] = (
    ("f0", "f0_hz", BOUNDARY_F0_HZ[0]),  # G2
    ("f0", "f0_hz", BOUNDARY_F0_HZ[1]),  # C5
    ("sr", "sr_hz", BOUNDARY_SR_HZ[0]),  # 16k
    ("sr", "sr_hz", BOUNDARY_SR_HZ[1]),  # 96k
    ("gain", "gain_dbfs", BOUNDARY_GAIN_DBFS[0]),  # -36
    ("gain", "gain_dbfs", BOUNDARY_GAIN_DBFS[1]),  # -1
    ("duration", "duration_s", BOUNDARY_DURATION_S[0]),  # 0.10
    ("duration", "duration_s", BOUNDARY_DURATION_S[1]),  # 4.00
    ("noise", "noise_snr_db", BOUNDARY_NOISE_SNR_DB),  # 0dB
)

NEGATIVE_CONTROL_SEQUENCE: tuple[str, ...] = (
    "SILENCE",
    "NOISE_ONLY",
    "PURE_SINE",
    "OUT_OF_BAND_POLE",
    "TOO_SHORT",
    "INVALID_SR",
)
"""§2.7 negative control 系列（記載順）。適用可否は family により異なる
(`controls.negative_control_applicable`)。"""

# ---------------------------------------------------------------------------
# family 別 truth 値（§5.2）
# ---------------------------------------------------------------------------

FORMANT_POLE_SETS_HZ: tuple[tuple[float, float, float], ...] = (
    (300.0, 2200.0, 3000.0),
    (500.0, 1900.0, 2600.0),
    (800.0, 1200.0, 2500.0),
    (500.0, 900.0, 2400.0),
    (350.0, 800.0, 2200.0),
)
FORMANT_BANDWIDTH_ANCHOR_HZ: tuple[float, ...] = (80.0, 100.0, 120.0)
FORMANT_IMPLEMENTATIONS: tuple[str, ...] = ("cascade", "additive")
# [UNDERSPEC-CAL-B03] confound/boundary/negative の FORMANT 行 (implementation は
# truth core でのみ因子として直積される) をどちらの実装で描画するかは正本非規定。
# 単純化のため cascade に固定する。
FORMANT_NON_TRUTH_IMPLEMENTATION: str = "cascade"

TILT_SLOPES_DB_PER_OCT: tuple[float, ...] = (-24.0, -18.0, -12.0, -6.0, 0.0)

APERIODICITY_FRACTIONS: tuple[float, ...] = (0.0, 0.01, 0.03, 0.10, 0.30, 0.60)
APERIODICITY_BANDS: tuple[str, ...] = ("broadband", "0-3kHz", "3-6kHz", "6kHz-Nyquist")
APERIODICITY_ANCHOR_F0_HZ: float = PRIMARY_F0_LOW_HZ  # C3
APERIODICITY_ANCHOR_SR_HZ: int = ANCHOR_SR_HZ

RESONANCE_CENTER_HZ: tuple[float, ...] = (500.0, 1000.0, 2000.0, 3500.0)
RESONANCE_BANDWIDTH_HZ: tuple[float, ...] = (50.0, 150.0, 300.0)
RESONANCE_PROMINENCE_DB: tuple[float, ...] = (6.0, 12.0)
# [UNDERSPEC-CAL-B04] RESONANCE_GT truth core は F0 を因子に持たない
# (§2.7: center x bandwidth x prominence のみ) が、生成には broadband 励起の
# ほかに文脈用 (context assembly) の "excitation pitch" が要る。primary domain の
# 中央値 C4 を固定 anchor として採用する。
RESONANCE_EXCITATION_F0_HZ: float = 261.626  # C4

TRANSITION_JOIN_TYPES: tuple[str, ...] = (
    "amplitude-step",
    "phase-jump",
    "spectral-envelope-switch",
    "crossfade",
)
# [UNDERSPEC-CAL-B05] 設計正本 §5.2 は severity を「3 severities（C0 表で固定）」と
# C0 側での確定を明記して据え置く。ここでは正規化した discontinuity magnitude
# (無次元, join_type ごとに解釈される) として単純な等間隔 3 水準を採用する。
TRANSITION_SEVERITIES: tuple[str, ...] = ("low", "medium", "high")
TRANSITION_SEVERITY_MAGNITUDE: dict[str, float] = {"low": 0.15, "medium": 0.35, "high": 0.65}
# [UNDERSPEC-CAL-B06] duration class (join 遷移窓長) も同様に C0 未確定。
# short=5ms / long=50ms を採用する（primary "duration" 軸のクリップ全長とは別概念）。
TRANSITION_DURATION_CLASSES: tuple[str, ...] = ("short", "long")
TRANSITION_DURATION_CLASS_S: dict[str, float] = {"short": 0.005, "long": 0.050}
TRANSITION_EXCITATION_F0_HZ: float = 261.626  # C4, [UNDERSPEC-CAL-B04] と同根拠

# [UNDERSPEC-CAL-B07] IDENTITY_CAUSAL_SWEEP の 4 founder（distinct F0/formant-set/
# tilt parameter bundle）の具体的な値は正本非規定。primary F0 4 水準と
# FORMANT_GT pole set の一部を再利用し、内部一貫性のある 4 bundle を凍結する。
IDENTITY_FOUNDERS: dict[str, dict[str, object]] = {
    "F1": {
        "f0_hz": 130.813,  # C3
        "pole_freqs_hz": (500.0, 1900.0, 2600.0),
        "bandwidth_hz": 100.0,
        "tilt_db_per_oct": -12.0,
    },
    "F2": {
        "f0_hz": 195.998,  # G3
        "pole_freqs_hz": (300.0, 2200.0, 3000.0),
        "bandwidth_hz": 100.0,
        "tilt_db_per_oct": -18.0,
    },
    "F3": {
        "f0_hz": 261.626,  # C4
        "pole_freqs_hz": (800.0, 1200.0, 2500.0),
        "bandwidth_hz": 100.0,
        "tilt_db_per_oct": -6.0,
    },
    "F4": {
        "f0_hz": 391.995,  # G4
        "pole_freqs_hz": (350.0, 800.0, 2200.0),
        "bandwidth_hz": 100.0,
        "tilt_db_per_oct": -24.0,
    },
}
IDENTITY_FOUNDER_IDS: tuple[str, ...] = ("F1", "F2", "F3", "F4")

# [UNDERSPEC-CAL-B08] 3 claim-critical traits の generator-unit -> 物理量換算則。
IDENTITY_TRAITS: tuple[str, ...] = ("F0", "FORMANT_SHIFT", "TILT_SLOPE")
IDENTITY_TRAIT_UNIT_CENTS: float = 5.0  # "F0" trait: 1 generator unit = 5 cents
IDENTITY_TRAIT_UNIT_FORMANT_SCALE: float = 0.02  # "FORMANT_SHIFT": 1 unit = 2% pole scale
IDENTITY_TRAIT_UNIT_TILT_DB: float = 1.0  # "TILT_SLOPE": 1 unit = 1 dB/oct
IDENTITY_DELTAS: tuple[int, ...] = (-2, -1, 0, 1, 2)

# ---------------------------------------------------------------------------
# family anchor (confound の基点。§2.7)
# ---------------------------------------------------------------------------

# [UNDERSPEC-CAL-B09] F0_CONTROL は §2.7 anchor 一覧で単一 anchor (C4@48k) のみ
# 明記されるが、confound 件数検算 "F0 24 = 11+6+7" は FORMANT_GT/IDENTITY と
# 同型の 2-anchor 構造（A1 の 11 行 + interactions 6 行 + A2 の nuisance 系列
# 先頭 7 行）を要求する。F0 のみを anchor から変えた第 2 anchor
# (G4@48k) を採用し、他の 5 family (single-anchor 構造で件数が閉じる
# TILT/APERIODICITY/RESONANCE/TRANSION) と整合させる。
F0_CONTROL_ANCHOR_A1: dict[str, object] = {"f0_hz": 261.626, "sr_hz": ANCHOR_SR_HZ}  # C4@48k
F0_CONTROL_ANCHOR_A2: dict[str, object] = {"f0_hz": 391.995, "sr_hz": ANCHOR_SR_HZ}  # G4@48k

FORMANT_ANCHOR_A1: dict[str, object] = {
    "pole_freqs_hz": (500.0, 1900.0, 2600.0),
    "bandwidth_hz": 100.0,
    "f0_hz": 130.813,  # C3
    "generator_impl": FORMANT_NON_TRUTH_IMPLEMENTATION,
}
FORMANT_ANCHOR_A2: dict[str, object] = {
    "pole_freqs_hz": (500.0, 900.0, 2400.0),
    "bandwidth_hz": 100.0,
    "f0_hz": 391.995,  # G4
    "generator_impl": FORMANT_NON_TRUTH_IMPLEMENTATION,
}

TILT_ANCHOR: dict[str, object] = {"slope_db_per_oct": -12.0, "f0_hz": 130.813, "sr_hz": ANCHOR_SR_HZ}
# [UNDERSPEC-CAL-B10] positive control には "2 anchor truth rows" が要る
# (IMPLEMENTATION_MAP §2.7 の control 共有契約)。single-anchor family には
# 明示第2 anchor が無いため、truth core grid 上で A1 と最も対照的な点
# (primary sweep 軸の反対端) を第 2 positive-control anchor として選ぶ。
TILT_POSITIVE_A2: dict[str, object] = {"slope_db_per_oct": 0.0, "f0_hz": 130.813, "sr_hz": ANCHOR_SR_HZ}

APERIODICITY_ANCHOR: dict[str, object] = {
    "injected_noise_fraction": 0.10,
    "f0_hz": APERIODICITY_ANCHOR_F0_HZ,
    "sr_hz": APERIODICITY_ANCHOR_SR_HZ,
}
APERIODICITY_POSITIVE_A2: dict[str, object] = {
    "injected_noise_fraction": 0.60,
    "f0_hz": APERIODICITY_ANCHOR_F0_HZ,
    "sr_hz": APERIODICITY_ANCHOR_SR_HZ,
}

RESONANCE_ANCHOR: dict[str, object] = {
    "center_hz": 1000.0,
    "resonance_bandwidth_hz": 150.0,
    "prominence_db": 12.0,
}
RESONANCE_POSITIVE_A2: dict[str, object] = {
    "center_hz": 3500.0,
    "resonance_bandwidth_hz": 150.0,
    "prominence_db": 12.0,
}

TRANSITION_ANCHOR: dict[str, object] = {
    "join_type": "amplitude-step",
    "severity": "medium",
    "duration_class": "long",
}
TRANSITION_POSITIVE_A2: dict[str, object] = {
    "join_type": "crossfade",
    "severity": "medium",
    "duration_class": "long",
}

IDENTITY_ANCHOR_A1: dict[str, object] = {"founder_id": "F1", "trait": "F0", "delta": 0}
IDENTITY_ANCHOR_A2: dict[str, object] = {"founder_id": "F3", "trait": "FORMANT_SHIFT", "delta": 0}


class FixtureFamily(str, Enum):
    """§4.2 の 7 fixture family（fixture 生成側の語彙。`vocab.MeterId` とは別軸）。"""

    F0_CONTROL = "F0_CONTROL"
    FORMANT_GT = "FORMANT_GT"
    TILT_GT = "TILT_GT"
    APERIODICITY_GT = "APERIODICITY_GT"
    RESONANCE_GT = "RESONANCE_GT"
    TRANSITION_GT = "TRANSITION_GT"
    IDENTITY_CAUSAL_SWEEP = "IDENTITY_CAUSAL_SWEEP"


FAMILY_ORDER: tuple[FixtureFamily, ...] = (
    FixtureFamily.F0_CONTROL,
    FixtureFamily.FORMANT_GT,
    FixtureFamily.TILT_GT,
    FixtureFamily.APERIODICITY_GT,
    FixtureFamily.RESONANCE_GT,
    FixtureFamily.TRANSITION_GT,
    FixtureFamily.IDENTITY_CAUSAL_SWEEP,
)

# §5.2 の per-family 内訳 (truth, confound, boundary_negative, total)
FAMILY_COUNTS: dict[FixtureFamily, tuple[int, int, int, int]] = {
    FixtureFamily.F0_CONTROL: (12, 24, 12, 48),
    FixtureFamily.FORMANT_GT: (60, 24, 12, 96),
    FixtureFamily.TILT_GT: (30, 12, 6, 48),
    FixtureFamily.APERIODICITY_GT: (60, 6, 6, 72),
    FixtureFamily.RESONANCE_GT: (24, 12, 12, 48),
    FixtureFamily.TRANSITION_GT: (24, 12, 12, 48),
    FixtureFamily.IDENTITY_CAUSAL_SWEEP: (60, 24, 12, 96),
}

TOTAL_LOGICAL_CELLS: int = 456
