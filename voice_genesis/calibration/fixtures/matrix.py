"""456 logical cell の明示列挙（設計正本 §5 / IMPLEMENTATION_MAP §2.7 FROZEN spec）。

行選択の裁量は残さない: 本モジュールは §2.7 の truth core 因子分解・正準
nuisance 系列・正準 boundary/negative 系列・per-family targeted interaction
列挙を機械的に転記して 456 行を組み立てる。数値未確定箇所は
`fixtures/axes.py` の `[UNDERSPEC-CAL-Bnn]` タグを参照。

各行は:

1. frozen dataclass `FixtureRow`（family / generator params / axis levels /
   context / control class tag / generator_implementation）を組み立て、
2. `canonical.row_id` で row_id を導出し（`domain` は含めない。domain は
   axis levels から導出される派生値であり、canonical row の一部にしない）、
3. `compute_domain()` で D2 (boundary axis 混入) + §3.3 F0 帯域整合検査を
   適用し `MatrixRow.domain` を確定する（split 生成より前に一度だけ）。
"""

from __future__ import annotations

import hashlib
import hmac as hmac_module
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from voice_genesis.calibration.canonical import row_id as compute_row_id
from voice_genesis.calibration.fixtures import axes
from voice_genesis.calibration.fixtures.axes import FixtureFamily
from voice_genesis.calibration.vocab import Domain

# ---------------------------------------------------------------------------
# FixtureRow
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FixtureRow:
    """1 logical cell の生成レシピ（canonical row_id の入力そのもの）。"""

    family: str
    block: str  # "TRUTH_CORE" | "CONFOUND" | "BOUNDARY" | "NEGATIVE_CONTROL"

    f0_hz: float
    sr_hz: int
    gain_dbfs: float
    duration_s: float
    noise_clean: bool
    noise_snr_db: float | None
    context: str

    control_class: str | None = None
    positive_control: bool = False
    interaction_tag: str | None = None
    boundary_tag: str | None = None
    nuisance_tag: str | None = None

    # FORMANT_GT
    pole_freqs_hz: tuple[float, ...] | None = None
    bandwidth_hz: float | None = None
    generator_impl: str | None = None

    # TILT_GT
    slope_db_per_oct: float | None = None

    # APERIODICITY_GT
    injected_noise_fraction: float | None = None
    bandwise_band: str | None = None

    # RESONANCE_GT
    center_hz: float | None = None
    resonance_bandwidth_hz: float | None = None
    prominence_db: float | None = None

    # TRANSITION_GT
    join_type: str | None = None
    severity: str | None = None
    duration_class: str | None = None
    join_time_s: float | None = None
    discontinuity_magnitude: float | None = None

    # IDENTITY_CAUSAL_SWEEP
    founder_id: str | None = None
    trait: str | None = None
    delta: int | None = None

    def to_canonical_dict(self) -> dict[str, Any]:
        """`canonical.row_id` へ渡す JSON 互換 dict（domain は含まない）。"""
        d: dict[str, Any] = {
            "family": self.family,
            "block": self.block,
            "f0_hz": self.f0_hz,
            "sr_hz": self.sr_hz,
            "gain_dbfs": self.gain_dbfs,
            "duration_s": self.duration_s,
            "noise_clean": self.noise_clean,
            "noise_snr_db": self.noise_snr_db,
            "context": self.context,
            "control_class": self.control_class,
            "positive_control": self.positive_control,
            "interaction_tag": self.interaction_tag,
            "boundary_tag": self.boundary_tag,
            "nuisance_tag": self.nuisance_tag,
            "bandwidth_hz": self.bandwidth_hz,
            "generator_impl": self.generator_impl,
            "slope_db_per_oct": self.slope_db_per_oct,
            "injected_noise_fraction": self.injected_noise_fraction,
            "bandwise_band": self.bandwise_band,
            "center_hz": self.center_hz,
            "resonance_bandwidth_hz": self.resonance_bandwidth_hz,
            "prominence_db": self.prominence_db,
            "join_type": self.join_type,
            "severity": self.severity,
            "duration_class": self.duration_class,
            "join_time_s": self.join_time_s,
            "discontinuity_magnitude": self.discontinuity_magnitude,
            "founder_id": self.founder_id,
            "trait": self.trait,
            "delta": self.delta,
        }
        d["pole_freqs_hz"] = list(self.pole_freqs_hz) if self.pole_freqs_hz is not None else None
        return d


@dataclass(frozen=True)
class MatrixRow:
    """`FixtureRow` + 導出 `row_id` / `domain`。"""

    row: FixtureRow
    row_id: str
    domain: Domain


# ---------------------------------------------------------------------------
# domain 導出（D2 + §3.3 F0 帯域整合検査）
# ---------------------------------------------------------------------------


def _has_boundary_axis(row: FixtureRow) -> bool:
    """D2: いずれかの軸が boundary level なら BOUNDARY。"""
    if row.f0_hz in axes.BOUNDARY_F0_HZ:
        return True
    if row.sr_hz in axes.BOUNDARY_SR_HZ or row.sr_hz == axes.NEGATIVE_INVALID_SR_HZ:
        return True
    if row.gain_dbfs in axes.BOUNDARY_GAIN_DBFS:
        return True
    if row.duration_s in axes.BOUNDARY_DURATION_S or row.duration_s == (
        axes.NEGATIVE_TOO_SHORT_DURATION_S
    ):
        return True
    if (not row.noise_clean) and row.noise_snr_db == axes.BOUNDARY_NOISE_SNR_DB:
        return True
    if row.control_class is not None:
        # negative control 行は本質的に domain 外/edge の探査であり BOUNDARY 扱い。
        return True
    return False


def f0_band_ok(f0_hz: float | None) -> bool:
    """§3.3 F0 帯域整合検査: `fmin <= 0.8*min(PRIMARY truth F0)` かつ
    `fmax >= 1.25*max(PRIMARY truth F0)` を、fixture 行自身の f0_hz が
    その安全帯域 `[fmin, fmax]` 内に収まっているかの検査として適用する
    （不成立 = 帯域整合が取れない = BOUNDARY 再タグ対象）。
    """
    if f0_hz is None:
        return True
    fmin = 0.8 * min(axes.PRIMARY_F0_HZ)
    fmax = 1.25 * max(axes.PRIMARY_F0_HZ)
    return fmin <= f0_hz <= fmax


def compute_domain(row: FixtureRow) -> Domain:
    """D2 (boundary axis 混入) → §3.3 F0 帯域整合検査、の順に適用する
    （どちらか一方でも成立すれば BOUNDARY）。split 生成・seal より前に一度だけ
    確定する（設計正本 §3.3）。"""
    if _has_boundary_axis(row):
        return Domain.BOUNDARY
    if not f0_band_ok(row.f0_hz):
        return Domain.BOUNDARY
    return Domain.PRIMARY


def _finalize(row: FixtureRow) -> MatrixRow:
    rid = compute_row_id(row.to_canonical_dict())
    return MatrixRow(row=row, row_id=rid, domain=compute_domain(row))


# ---------------------------------------------------------------------------
# 汎用ヘルパー: anchor dict + overrides -> FixtureRow kwargs
# ---------------------------------------------------------------------------


def _anchor_kwargs(family: FixtureFamily, family_truth: dict[str, Any]) -> dict[str, Any]:
    """anchor 水準（SR 48000 / gain -12dBFS / duration 1.00s / noise clean /
    context steady-isolated）+ family truth defaults を合成した基底 kwargs。"""
    base: dict[str, Any] = {
        "family": family.value,
        "f0_hz": axes.RESONANCE_EXCITATION_F0_HZ,  # 汎用デフォルト（family truth で上書き）
        "sr_hz": axes.ANCHOR_SR_HZ,
        "gain_dbfs": axes.ANCHOR_GAIN_DBFS,
        "duration_s": axes.ANCHOR_DURATION_S,
        "noise_clean": True,
        "noise_snr_db": None,
        "context": axes.ANCHOR_CONTEXT,
    }
    base.update(family_truth)
    return base


def _apply_field_overrides(kwargs: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    out = dict(kwargs)
    out.update(overrides)
    return out


def _apply_nuisance_item(kwargs: dict[str, Any], item: tuple[str, str, Any]) -> dict[str, Any]:
    _axis_family, field_name, value = item
    out = dict(kwargs)
    out[field_name] = value
    if field_name == "noise_snr_db":
        out["noise_clean"] = False
    return out


# ---------------------------------------------------------------------------
# confound block の汎用構築 (§2.7 決定的レシピ)
# ---------------------------------------------------------------------------


def _filtered_nuisance_sequence(
    exclude_axis_family: str | None,
) -> tuple[tuple[str, str, Any], ...]:
    if exclude_axis_family is None:
        return axes.CANONICAL_NUISANCE_SEQUENCE
    return tuple(
        item for item in axes.CANONICAL_NUISANCE_SEQUENCE if item[0] != exclude_axis_family
    )


def _filtered_interactions(
    k: int, exclude_axis_family: str | None
) -> tuple[tuple[str, str, dict[str, Any]], ...]:
    if exclude_axis_family is None:
        pool = axes.TARGETED_INTERACTIONS
    else:
        pool = tuple(
            it for it in axes.TARGETED_INTERACTIONS if exclude_axis_family not in it[1]
        )
    return pool[:k]


def _build_confound_block(
    *,
    family: FixtureFamily,
    anchor1_truth: dict[str, Any],
    target_n: int,
    interaction_k: int,
    exclude_axis_family: str | None,
    anchor2_truth: dict[str, Any] | None,
) -> list[FixtureRow]:
    seq = _filtered_nuisance_sequence(exclude_axis_family)
    interactions = _filtered_interactions(interaction_k, exclude_axis_family)

    rows_kwargs: list[dict[str, Any]] = []
    anchor1 = _anchor_kwargs(family, anchor1_truth)
    for item in seq:
        rows_kwargs.append(
            {
                **_apply_nuisance_item(anchor1, item),
                "block": "CONFOUND",
                "nuisance_tag": f"{item[1]}={item[2]!r}",
            }
        )
    for name, _axis_family, overrides in interactions:
        rows_kwargs.append(
            {
                **_apply_field_overrides(anchor1, overrides),
                "block": "CONFOUND",
                "interaction_tag": name,
            }
        )
    if anchor2_truth is not None:
        anchor2 = _anchor_kwargs(family, anchor2_truth)
        for item in seq:
            rows_kwargs.append(
                {
                    **_apply_nuisance_item(anchor2, item),
                    "block": "CONFOUND",
                    "nuisance_tag": f"A2:{item[1]}={item[2]!r}",
                }
            )

    rows_kwargs = rows_kwargs[:target_n]
    return [FixtureRow(**kw) for kw in rows_kwargs]


# ---------------------------------------------------------------------------
# boundary/negative block の汎用構築 (§2.7 決定的レシピ)
# ---------------------------------------------------------------------------


def _boundary_negative_targets(n: int) -> tuple[int, int]:
    if n == 6:
        return 4, 2
    return n - 3, 3


def _filtered_boundary_sequence(
    exclude_axis_family: str | None,
) -> tuple[tuple[str, str, Any], ...]:
    if exclude_axis_family is None:
        return axes.CANONICAL_BOUNDARY_SEQUENCE
    return tuple(
        item for item in axes.CANONICAL_BOUNDARY_SEQUENCE if item[0] != exclude_axis_family
    )


def _negative_applicable(family: FixtureFamily, control_class: str) -> bool:
    if control_class == "PURE_SINE":
        return family != FixtureFamily.F0_CONTROL
    if control_class == "OUT_OF_BAND_POLE":
        return family in (FixtureFamily.FORMANT_GT, FixtureFamily.RESONANCE_GT)
    return True


def _negative_row_kwargs(
    family: FixtureFamily, anchor_truth: dict[str, Any], control_class: str
) -> dict[str, Any]:
    kw = _anchor_kwargs(family, anchor_truth)
    kw["block"] = "NEGATIVE_CONTROL"
    kw["control_class"] = control_class
    if control_class == "SILENCE":
        pass
    elif control_class == "NOISE_ONLY":
        pass
    elif control_class == "PURE_SINE":
        # 純正弦は family truth (poles/slope/fraction/...) を無効化して比較する
        # ための negative control であり、生成器側で control_class を見て truth
        # 構造を無視する。row 上は family truth をそのまま残し証跡とする。
        pass
    elif control_class == "OUT_OF_BAND_POLE":
        out_of_band = 0.49 * (kw["sr_hz"])
        if family == FixtureFamily.FORMANT_GT:
            kw["pole_freqs_hz"] = (out_of_band, out_of_band, out_of_band)
        elif family == FixtureFamily.RESONANCE_GT:
            kw["center_hz"] = out_of_band
    elif control_class == "TOO_SHORT":
        kw["duration_s"] = axes.NEGATIVE_TOO_SHORT_DURATION_S
    elif control_class == "INVALID_SR":
        kw["sr_hz"] = axes.NEGATIVE_INVALID_SR_HZ
    else:
        raise ValueError(f"unknown control_class {control_class!r}")
    return kw


def _build_boundary_negative_block(
    *,
    family: FixtureFamily,
    anchor_truth: dict[str, Any],
    n: int,
    exclude_axis_family: str | None,
) -> list[FixtureRow]:
    boundary_n, negative_n = _boundary_negative_targets(n)
    seq = _filtered_boundary_sequence(exclude_axis_family)[:boundary_n]

    rows: list[FixtureRow] = []
    anchor = _anchor_kwargs(family, anchor_truth)
    for axis_family, field_name, value in seq:
        kw = dict(anchor)
        kw[field_name] = value
        if field_name == "noise_snr_db":
            kw["noise_clean"] = False
        kw["block"] = "BOUNDARY"
        kw["boundary_tag"] = f"{field_name}={value!r}"
        rows.append(FixtureRow(**kw))

    applicable = [c for c in axes.NEGATIVE_CONTROL_SEQUENCE if _negative_applicable(family, c)]
    for control_class in applicable[:negative_n]:
        rows.append(FixtureRow(**_negative_row_kwargs(family, anchor_truth, control_class)))

    return rows


# ---------------------------------------------------------------------------
# truth core: family 別 (§2.7 因子分解)
# ---------------------------------------------------------------------------


def _f0_control_truth_core() -> list[FixtureRow]:
    rows = []
    for f0 in axes.PRIMARY_F0_HZ:
        for sr in axes.PRIMARY_SR_HZ:
            kw = _anchor_kwargs(
                FixtureFamily.F0_CONTROL, {"f0_hz": f0, "sr_hz": sr}
            )
            kw["block"] = "TRUTH_CORE"
            rows.append(FixtureRow(**kw))
    return rows


def _formant_truth_core() -> list[FixtureRow]:
    rows = []
    for poles in axes.FORMANT_POLE_SETS_HZ:
        for bw in axes.FORMANT_BANDWIDTH_ANCHOR_HZ:
            for impl in axes.FORMANT_IMPLEMENTATIONS:
                for f0 in (axes.PRIMARY_F0_LOW_HZ, axes.PRIMARY_F0_HIGH_HZ):
                    kw = _anchor_kwargs(
                        FixtureFamily.FORMANT_GT,
                        {
                            "pole_freqs_hz": poles,
                            "bandwidth_hz": bw,
                            "generator_impl": impl,
                            "f0_hz": f0,
                        },
                    )
                    kw["block"] = "TRUTH_CORE"
                    rows.append(FixtureRow(**kw))
    return rows


def _tilt_truth_core() -> list[FixtureRow]:
    rows = []
    for slope in axes.TILT_SLOPES_DB_PER_OCT:
        for sr in axes.PRIMARY_SR_HZ:
            for f0 in (axes.PRIMARY_F0_LOW_HZ, axes.PRIMARY_F0_HIGH_HZ):
                kw = _anchor_kwargs(
                    FixtureFamily.TILT_GT,
                    {"slope_db_per_oct": slope, "sr_hz": sr, "f0_hz": f0},
                )
                kw["block"] = "TRUTH_CORE"
                rows.append(FixtureRow(**kw))
    return rows


def _aperiodicity_truth_core() -> list[FixtureRow]:
    rows = []
    for fraction in axes.APERIODICITY_FRACTIONS:
        for f0 in (axes.PRIMARY_F0_LOW_HZ, axes.PRIMARY_F0_HIGH_HZ):
            for sr in axes.PRIMARY_SR_HZ:
                kw = _anchor_kwargs(
                    FixtureFamily.APERIODICITY_GT,
                    {
                        "injected_noise_fraction": fraction,
                        "f0_hz": f0,
                        "sr_hz": sr,
                        "bandwise_band": None,
                    },
                )
                kw["block"] = "TRUTH_CORE"
                rows.append(FixtureRow(**kw))
    for band in axes.APERIODICITY_BANDS:
        for fraction in axes.APERIODICITY_FRACTIONS:
            kw = _anchor_kwargs(
                FixtureFamily.APERIODICITY_GT,
                {
                    "injected_noise_fraction": fraction,
                    "f0_hz": axes.APERIODICITY_ANCHOR_F0_HZ,
                    "sr_hz": axes.APERIODICITY_ANCHOR_SR_HZ,
                    "bandwise_band": band,
                },
            )
            kw["block"] = "TRUTH_CORE"
            rows.append(FixtureRow(**kw))
    return rows


def _resonance_truth_core() -> list[FixtureRow]:
    rows = []
    for center in axes.RESONANCE_CENTER_HZ:
        for bw in axes.RESONANCE_BANDWIDTH_HZ:
            for prom in axes.RESONANCE_PROMINENCE_DB:
                kw = _anchor_kwargs(
                    FixtureFamily.RESONANCE_GT,
                    {
                        "center_hz": center,
                        "resonance_bandwidth_hz": bw,
                        "prominence_db": prom,
                        "f0_hz": axes.RESONANCE_EXCITATION_F0_HZ,
                    },
                )
                kw["block"] = "TRUTH_CORE"
                rows.append(FixtureRow(**kw))
    return rows


def _transition_truth_core() -> list[FixtureRow]:
    rows = []
    for join_type in axes.TRANSITION_JOIN_TYPES:
        for severity in axes.TRANSITION_SEVERITIES:
            for dclass in axes.TRANSITION_DURATION_CLASSES:
                dur_s = axes.ANCHOR_DURATION_S
                kw = _anchor_kwargs(
                    FixtureFamily.TRANSITION_GT,
                    {
                        "join_type": join_type,
                        "severity": severity,
                        "duration_class": dclass,
                        "f0_hz": axes.TRANSITION_EXCITATION_F0_HZ,
                        "join_time_s": dur_s / 2.0,
                        "discontinuity_magnitude": axes.TRANSITION_SEVERITY_MAGNITUDE[severity],
                    },
                )
                kw["block"] = "TRUTH_CORE"
                rows.append(FixtureRow(**kw))
    return rows


def _identity_truth_core() -> list[FixtureRow]:
    rows = []
    for founder_id in axes.IDENTITY_FOUNDER_IDS:
        founder = axes.IDENTITY_FOUNDERS[founder_id]
        for trait in axes.IDENTITY_TRAITS:
            for delta in axes.IDENTITY_DELTAS:
                kw = _anchor_kwargs(
                    FixtureFamily.IDENTITY_CAUSAL_SWEEP,
                    {
                        "founder_id": founder_id,
                        "trait": trait,
                        "delta": delta,
                        "f0_hz": founder["f0_hz"],
                    },
                )
                kw["block"] = "TRUTH_CORE"
                rows.append(FixtureRow(**kw))
    return rows


# ---------------------------------------------------------------------------
# per-family assembly
# ---------------------------------------------------------------------------


def _f0_control_rows() -> list[FixtureRow]:
    truth = _f0_control_truth_core()
    confound = _build_confound_block(
        family=FixtureFamily.F0_CONTROL,
        anchor1_truth=axes.F0_CONTROL_ANCHOR_A1,
        target_n=24,
        interaction_k=6,
        exclude_axis_family=None,
        anchor2_truth=axes.F0_CONTROL_ANCHOR_A2,
    )
    boundary_neg = _build_boundary_negative_block(
        family=FixtureFamily.F0_CONTROL,
        anchor_truth=axes.F0_CONTROL_ANCHOR_A1,
        n=12,
        exclude_axis_family=None,
    )
    truth = _mark_positive_controls(
        truth, [axes.F0_CONTROL_ANCHOR_A1, axes.F0_CONTROL_ANCHOR_A2]
    )
    return truth + confound + boundary_neg


def _formant_rows() -> list[FixtureRow]:
    truth = _formant_truth_core()
    confound = _build_confound_block(
        family=FixtureFamily.FORMANT_GT,
        anchor1_truth=axes.FORMANT_ANCHOR_A1,
        target_n=24,
        interaction_k=6,
        exclude_axis_family=None,
        anchor2_truth=axes.FORMANT_ANCHOR_A2,
    )
    boundary_neg = _build_boundary_negative_block(
        family=FixtureFamily.FORMANT_GT,
        anchor_truth=axes.FORMANT_ANCHOR_A1,
        n=12,
        exclude_axis_family=None,
    )
    truth = _mark_positive_controls(
        truth, [axes.FORMANT_ANCHOR_A1, axes.FORMANT_ANCHOR_A2]
    )
    return truth + confound + boundary_neg


def _tilt_rows() -> list[FixtureRow]:
    truth = _tilt_truth_core()
    confound = _build_confound_block(
        family=FixtureFamily.TILT_GT,
        anchor1_truth=axes.TILT_ANCHOR,
        target_n=12,
        interaction_k=1,
        exclude_axis_family=None,
        anchor2_truth=None,
    )
    boundary_neg = _build_boundary_negative_block(
        family=FixtureFamily.TILT_GT,
        anchor_truth=axes.TILT_ANCHOR,
        n=6,
        exclude_axis_family=None,
    )
    truth = _mark_positive_controls(truth, [axes.TILT_ANCHOR, axes.TILT_POSITIVE_A2])
    return truth + confound + boundary_neg


def _aperiodicity_rows() -> list[FixtureRow]:
    truth = _aperiodicity_truth_core()
    confound = _build_confound_block(
        family=FixtureFamily.APERIODICITY_GT,
        anchor1_truth=axes.APERIODICITY_ANCHOR,
        target_n=6,
        interaction_k=0,
        exclude_axis_family="noise",
        anchor2_truth=None,
    )
    boundary_neg = _build_boundary_negative_block(
        family=FixtureFamily.APERIODICITY_GT,
        anchor_truth=axes.APERIODICITY_ANCHOR,
        n=6,
        exclude_axis_family=None,
    )
    truth = _mark_positive_controls(
        truth, [axes.APERIODICITY_ANCHOR, axes.APERIODICITY_POSITIVE_A2]
    )
    return truth + confound + boundary_neg


def _resonance_rows() -> list[FixtureRow]:
    truth = _resonance_truth_core()
    confound = _build_confound_block(
        family=FixtureFamily.RESONANCE_GT,
        anchor1_truth=axes.RESONANCE_ANCHOR,
        target_n=12,
        interaction_k=1,
        exclude_axis_family=None,
        anchor2_truth=None,
    )
    boundary_neg = _build_boundary_negative_block(
        family=FixtureFamily.RESONANCE_GT,
        anchor_truth=axes.RESONANCE_ANCHOR,
        n=12,
        exclude_axis_family=None,
    )
    truth = _mark_positive_controls(
        truth, [axes.RESONANCE_ANCHOR, axes.RESONANCE_POSITIVE_A2]
    )
    return truth + confound + boundary_neg


def _transition_rows() -> list[FixtureRow]:
    truth = _transition_truth_core()
    confound = _build_confound_block(
        family=FixtureFamily.TRANSITION_GT,
        anchor1_truth={
            **axes.TRANSITION_ANCHOR,
            "f0_hz": axes.TRANSITION_EXCITATION_F0_HZ,
            "join_time_s": axes.ANCHOR_DURATION_S / 2.0,
            "discontinuity_magnitude": axes.TRANSITION_SEVERITY_MAGNITUDE[
                axes.TRANSITION_ANCHOR["severity"]
            ],
        },
        target_n=12,
        interaction_k=4,
        exclude_axis_family="context",
        anchor2_truth=None,
    )
    boundary_neg = _build_boundary_negative_block(
        family=FixtureFamily.TRANSITION_GT,
        anchor_truth={
            **axes.TRANSITION_ANCHOR,
            "f0_hz": axes.TRANSITION_EXCITATION_F0_HZ,
            "join_time_s": axes.ANCHOR_DURATION_S / 2.0,
            "discontinuity_magnitude": axes.TRANSITION_SEVERITY_MAGNITUDE[
                axes.TRANSITION_ANCHOR["severity"]
            ],
        },
        n=12,
        exclude_axis_family=None,
    )
    # confound/boundary 行の duration_s は nuisance/boundary 系列で上書きされう
    # るため join_time_s (=duration_s/2) を再導出する。
    confound = [_retime_transition(r) for r in confound]
    boundary_neg = [_retime_transition(r) for r in boundary_neg]
    truth = _mark_positive_controls(
        truth,
        [
            {**axes.TRANSITION_ANCHOR, "f0_hz": axes.TRANSITION_EXCITATION_F0_HZ},
            {**axes.TRANSITION_POSITIVE_A2, "f0_hz": axes.TRANSITION_EXCITATION_F0_HZ},
        ],
        match_fields=("join_type", "severity", "duration_class"),
    )
    return truth + confound + boundary_neg


def _retime_transition(row: FixtureRow) -> FixtureRow:
    if row.control_class is not None or row.join_type is None:
        return row
    return replace(row, join_time_s=row.duration_s / 2.0)


def _identity_rows() -> list[FixtureRow]:
    truth = _identity_truth_core()
    confound = _build_confound_block(
        family=FixtureFamily.IDENTITY_CAUSAL_SWEEP,
        anchor1_truth={**axes.IDENTITY_ANCHOR_A1, "delta": 0, "f0_hz": _identity_anchor_f0(axes.IDENTITY_ANCHOR_A1)},
        target_n=24,
        interaction_k=6,
        exclude_axis_family=None,
        anchor2_truth={**axes.IDENTITY_ANCHOR_A2, "delta": 0, "f0_hz": _identity_anchor_f0(axes.IDENTITY_ANCHOR_A2)},
    )
    boundary_neg = _build_boundary_negative_block(
        family=FixtureFamily.IDENTITY_CAUSAL_SWEEP,
        anchor_truth={
            **axes.IDENTITY_ANCHOR_A1,
            "delta": 0,
            "f0_hz": _identity_anchor_f0(axes.IDENTITY_ANCHOR_A1),
        },
        n=12,
        exclude_axis_family=None,
    )
    truth = _mark_positive_controls(
        truth,
        [axes.IDENTITY_ANCHOR_A1, axes.IDENTITY_ANCHOR_A2],
        match_fields=("founder_id", "trait", "delta"),
    )
    return truth + confound + boundary_neg


def _identity_anchor_f0(anchor: dict[str, Any]) -> float:
    founder_id = anchor["founder_id"]
    return float(axes.IDENTITY_FOUNDERS[founder_id]["f0_hz"])


def _mark_positive_controls(
    rows: list[FixtureRow],
    anchors: list[dict[str, Any]],
    match_fields: tuple[str, ...] = (),
) -> list[FixtureRow]:
    """`rows`（truth core）のうち `anchors` に一致する 2 行を positive_control=True
    へ差し替える（§2.7 control 共有契約: family anchor の truth core 行 2 件）。"""
    if not match_fields:
        match_fields = tuple(k for k in anchors[0])
    out: list[FixtureRow] = []
    remaining = list(anchors)
    for row in rows:
        matched_idx = None
        for i, anc in enumerate(remaining):
            if all(getattr(row, k) == anc.get(k) for k in match_fields if k in anc):
                matched_idx = i
                break
        if matched_idx is not None:
            out.append(replace(row, positive_control=True))
            remaining.pop(matched_idx)
        else:
            out.append(row)
    if remaining:
        raise RuntimeError(
            f"matrix: positive control anchor(s) not found in truth core: {remaining}"
        )
    return out


_FAMILY_BUILDERS = {
    FixtureFamily.F0_CONTROL: _f0_control_rows,
    FixtureFamily.FORMANT_GT: _formant_rows,
    FixtureFamily.TILT_GT: _tilt_rows,
    FixtureFamily.APERIODICITY_GT: _aperiodicity_rows,
    FixtureFamily.RESONANCE_GT: _resonance_rows,
    FixtureFamily.TRANSITION_GT: _transition_rows,
    FixtureFamily.IDENTITY_CAUSAL_SWEEP: _identity_rows,
}


def build_matrix() -> list[MatrixRow]:
    """456 logical cell 全ての明示列挙を、`axes.FAMILY_ORDER` の順・各 family 内は
    truth core -> confound -> boundary/negative の順で返す（列挙順は決定的）。
    """
    out: list[MatrixRow] = []
    for family in axes.FAMILY_ORDER:
        rows = _FAMILY_BUILDERS[family]()
        out.extend(_finalize(r) for r in rows)
    return out


# ---------------------------------------------------------------------------
# 検証
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MatrixValidationReport:
    total: int
    per_family_total: dict[str, int]
    per_family_block_counts: dict[str, dict[str, int]]
    duplicate_row_ids: tuple[str, ...]
    ok: bool


def validate_matrix(rows: list[MatrixRow]) -> MatrixValidationReport:
    """§5.2 の per-family 内訳・total 456・row_id 一意性を検証する。
    不一致・重複があれば `ok=False` を返す（例外は投げない。呼び出し側 test が
    厳密一致を assert する）。row_id 重複は "件数検査を素通りして計画セルを暗黙に
    欠落させる" ため（IMPLEMENTATION_MAP §2.5）、別途 `raise` する専用関数
    `assert_no_duplicate_row_ids` も提供する。
    """
    per_family_total: dict[str, int] = {}
    per_family_block_counts: dict[str, dict[str, int]] = {}
    seen: dict[str, int] = {}
    for mr in rows:
        fam = mr.row.family
        per_family_total[fam] = per_family_total.get(fam, 0) + 1
        blocks = per_family_block_counts.setdefault(fam, {})
        blocks[mr.row.block] = blocks.get(mr.row.block, 0) + 1
        seen[mr.row_id] = seen.get(mr.row_id, 0) + 1

    duplicates = tuple(sorted(rid for rid, n in seen.items() if n > 1))

    ok = len(rows) == axes.TOTAL_LOGICAL_CELLS and not duplicates
    for family, (truth_n, confound_n, boundary_n, total_n) in axes.FAMILY_COUNTS.items():
        fam_key = family.value
        if per_family_total.get(fam_key) != total_n:
            ok = False
            continue
        blocks = per_family_block_counts.get(fam_key, {})
        truth_actual = blocks.get("TRUTH_CORE", 0)
        confound_actual = blocks.get("CONFOUND", 0)
        boundary_actual = blocks.get("BOUNDARY", 0) + blocks.get("NEGATIVE_CONTROL", 0)
        if (truth_actual, confound_actual, boundary_actual) != (truth_n, confound_n, boundary_n):
            ok = False

    return MatrixValidationReport(
        total=len(rows),
        per_family_total=per_family_total,
        per_family_block_counts=per_family_block_counts,
        duplicate_row_ids=duplicates,
        ok=ok,
    )


def assert_no_duplicate_row_ids(rows: list[MatrixRow]) -> None:
    seen: set[str] = set()
    for mr in rows:
        if mr.row_id in seen:
            raise ValueError(f"matrix: duplicate row_id detected: {mr.row_id}")
        seen.add(mr.row_id)


# ---------------------------------------------------------------------------
# 宣言済み sweep（UNDERSPEC-CAL-D76 def A。D75/`[UNDERSPEC-CAL-D75]` の
# nuisance-axis 定義を SUPERSEDE する。§10.4 DIRECTIONAL gate 前提）
#
# 調査結論（`sweep_truth_investigation.md`）: 設計正本 §10.4 の
# `Delta_truth(i,j) > R_ij` は truth が変動する pair を前提にしており
# （`Delta_truth == 0` は原理的に resolvable になり得ない）、§4.2 L169
# 「content/F0/duration/SNR 固定の one-factor causal sweep」、§10.1
# L325-326/L334「truth 自体が変わる軸は invariance 対象に混ぜない」+
# 「invariance 軸ごとに >= 5 pairs」（nuisance 軸 = 別語・別最小数）から、
# sweep は「nuisance/covariate 設定を固定し truth 水準だけを動かす行集合」
# （= truth-core block の因子分解そのもの）であって nuisance 軸そのもの
# ではない。D75 の `declared_sweeps_by_family()`（nuisance_tag の軸名を
# sweep_id とする定義）は 7 family 中 7 family で「全 sweep >= 3」を
# 構造的に満たせず（group 内 truth が anchor 固定のため全 pair
# `delta_truth == 0`）、462 セル化はこの誤った定義への対症療法だった
# （本 D76 で revert 済み）。
# ---------------------------------------------------------------------------

#: family の主要 truth スカラー field（`campaign.selection_stage.
#: _TRUTH_FIELD_BY_FAMILY`/`c0_freeze._KNOWN_TRUTH_FIELD` と同じ対応だが、
#: 他 module（campaign 層）には依存せず本モジュールで独立に宣言する
#: （`selection_stage.py` 冒頭コメント「他 agent が並行編集中の... には
#: 依存せず本モジュールで独立に宣言する」と同じ規約）。値は「sweep 内で
#: 固定される行フィールド」の key から除外する対象そのもの——TRANSITION_GT
#: のみ `severity`/`discontinuity_magnitude` の 2 field が同一 truth
#: construct を指す（severity から discontinuity_magnitude が 1:1 導出
#: される、`axes.TRANSITION_SEVERITY_MAGNITUDE`）ため両方を除外する。
_SWEEP_TRUTH_FIELDS_BY_FAMILY: dict[str, tuple[str, ...]] = {
    FixtureFamily.F0_CONTROL.value: ("f0_hz",),
    FixtureFamily.FORMANT_GT.value: ("pole_freqs_hz",),
    FixtureFamily.TILT_GT.value: ("slope_db_per_oct",),
    FixtureFamily.APERIODICITY_GT.value: ("injected_noise_fraction",),
    FixtureFamily.RESONANCE_GT.value: ("center_hz",),
    FixtureFamily.TRANSITION_GT.value: ("severity", "discontinuity_magnitude"),
    FixtureFamily.IDENTITY_CAUSAL_SWEEP.value: ("delta",),
}

#: sweep の group key から常に除外するメタデータ field。`block` は
#: `declared_sweeps_by_family()` の呼び出し前に TRUTH_CORE へ既に絞り込んで
#: いるため常に同一値だが明示的に除外する。`positive_control` は
#: family anchor（TRUTH_CORE 行の一部）にのみ True が立つタグであり、
#: 同一 sweep 内の行を anchor/non-anchor で誤って分断しないよう
#: 明示的に除外する（UNDERSPEC-CAL-D76: 「do NOT include positive_control/
#: block in the key」）。`family` は呼び出し側で既に family ごとに
#: 分割済みのため冗長だが、明示的に除外して意図を残す。
_SWEEP_KEY_EXCLUDED_FIELDS: frozenset[str] = frozenset({"family", "block", "positive_control"})


def _sweep_fixed_fields(row: FixtureRow) -> dict[str, Any]:
    """`row` の「sweep 内で固定される（=held-fixed）」canonical field。
    truth field 群 + メタデータ除外 field を除いた `to_canonical_dict()` の
    残り——同じ値を持つ TRUTH_CORE/PRIMARY 行が 1 declared sweep を成す。"""
    d = row.to_canonical_dict()
    excluded = _SWEEP_KEY_EXCLUDED_FIELDS | set(_SWEEP_TRUTH_FIELDS_BY_FAMILY.get(row.family, ()))
    return {k: v for k, v in d.items() if k not in excluded}


def truth_identity_for_row(row: FixtureRow) -> tuple[Any, ...]:
    """`row` の family の truth construct を一意に識別するタプル
    （`_SWEEP_TRUTH_FIELDS_BY_FAMILY` の値をそのまま読む——FORMANT_GT は
    `pole_freqs_hz` tuple 全体、TRANSITION_GT は `severity`+
    `discontinuity_magnitude` の組）。同一 declared sweep 内で相異なる
    truth level を数える唯一の入力（C0 の sweep 宣言検証・C4 の holdout
    構造下界チェックの両方が使う）。"""
    fields = _SWEEP_TRUTH_FIELDS_BY_FAMILY.get(row.family, ())
    return tuple(getattr(row, f) for f in fields)


def _varying_fixed_field_names(rows: Sequence[FixtureRow]) -> tuple[str, ...]:
    """`rows`（同一 family の TRUTH_CORE/PRIMARY 行）の held-fixed field の
    うち、family 内で実際に複数値を取る field 名だけを sweep_id の可読な
    構成要素として返す（定数 field を含めても分割結果は変わらないが、
    id の可読性のため差分だけを載せる）。"""
    seen: dict[str, set[Any]] = {}
    for row in rows:
        for k, v in _sweep_fixed_fields(row).items():
            seen.setdefault(k, set()).add(v)
    return tuple(sorted(k for k, values in seen.items() if len(values) > 1))


def _sweep_groups(rows: Sequence[MatrixRow]) -> dict[str, dict[str, list[MatrixRow]]]:
    """`declared_sweeps_by_family()` と v1.1 pin 関数群が共有する内部
    グルーピング（family -> {sweep_id: [member MatrixRow, ...]}）。sweep_id
    の導出規則は `declared_sweeps_by_family()` の docstring と同一。member
    を `MatrixRow`（row_id だけでなく held-fixed field の実値も）のまま保持
    する点だけが `declared_sweeps_by_family()` の公開出力（row_id のみの
    tuple）と異なる——pin 選抜が member の `generator_impl`/`founder_id`/
    `trait`/claim-relevant field の値を読む必要があるため。
    """
    by_family: dict[str, list[MatrixRow]] = {family.value: [] for family in axes.FAMILY_ORDER}
    for mr in rows:
        if mr.row.block != "TRUTH_CORE" or mr.domain is not Domain.PRIMARY:
            continue
        by_family.setdefault(mr.row.family, []).append(mr)

    result: dict[str, dict[str, list[MatrixRow]]] = {}
    for family, family_matrix_rows in by_family.items():
        id_fields = _varying_fixed_field_names([mr.row for mr in family_matrix_rows])
        groups: dict[tuple[tuple[str, Any], ...], list[MatrixRow]] = {}
        for mr in family_matrix_rows:
            fixed = _sweep_fixed_fields(mr.row)
            key = tuple(sorted(fixed.items(), key=lambda kv: kv[0]))
            groups.setdefault(key, []).append(mr)
        family_sweeps: dict[str, list[MatrixRow]] = {}
        for key, members in groups.items():
            fixed = dict(key)
            sweep_id = (
                "|".join(f"{field}={fixed[field]!r}" for field in id_fields)
                if id_fields
                else "anchor"
            )
            family_sweeps[sweep_id] = members
        result[family] = family_sweeps
    return result


def declared_sweeps_by_family(rows: Sequence[MatrixRow]) -> dict[str, dict[str, tuple[str, ...]]]:
    """UNDERSPEC-CAL-D76 def A（sweep_truth_investigation.md）: family の
    declared sweep 集合は、PRIMARY domain の TRUTH_CORE 行のうち
    nuisance/covariate 設定（held-fixed field）が同一で truth 水準
    （`truth_identity_for_row()`）だけが異なる行の集合。sweep_id は
    held-fixed field のうち family 内で実際に変動する field を
    `field=value` 形式で連結した文字列として決定的に導出する
    （`positive_control`/`block` は除く）。

    戻り値: `family -> {sweep_id: (member row_id, ...)}`（`axes.FAMILY_ORDER`
    の全 family を key に持つ。宣言 sweep が無い family は空 dict）。
    C0 freeze（`c0_freeze._fixture_specs()`）がこの出力をそのまま
    `frozen_design.fixture_spec.<FAMILY>.declared_sweeps` として凍結し
    （`manifest_core_sha` に含まれる）、`campaign.cli._run_c4` の
    DIRECTIONAL 最小数チェックも同じ入口を使う（`campaign.holdout_stage.
    declared_axes_for_family()` は gate4' invariance 軸専用に残り、この
    sweep 宣言とは独立——旧 D18/D75 の「confound_axes を sweep_id として
    再利用する」写像は誤りとして本関数へ一本化した）。
    """
    groups = _sweep_groups(rows)
    return {
        family: {
            sweep_id: tuple(sorted(mr.row_id for mr in members))
            for sweep_id, members in family_sweeps.items()
        }
        for family, family_sweeps in groups.items()
    }


def declared_sweep_ids_by_family(rows: Sequence[MatrixRow]) -> dict[str, tuple[str, ...]]:
    """`declared_sweeps_by_family()` の sweep_id のみ（member row_id を
    落とした軽量版）。C4 の `expected_sweep_ids` 引数など、member を
    必要としない消費側向け。"""
    return {
        family: tuple(sorted(sweeps))
        for family, sweeps in declared_sweeps_by_family(rows).items()
    }


# ---------------------------------------------------------------------------
# v1.1 §V2.2 — holdout sweep pinning（2 段割当の段 1）
#
# `DESIGN_VG_METER_CAL_DEBT_v1.1.md` §V2.2 正本: family ごとに declared
# sweep（上記 `declared_sweeps_by_family()`）から `k_hold` 個を HOLDOUT
# 専属 sweep として pin し、その member 全行を HOLDOUT へ割当てる。選抜は
# secret 依存の HMAC-SHA256 のみを秘匿源とする決定論アルゴリズム。
# ---------------------------------------------------------------------------


def _pin_hmac_hex(secret: bytes, message: str) -> str:
    """段 1 pin 選抜専用の HMAC ヘルパー。`splitter._hmac_hex` と同一実装だが、
    `splitter.py` へは依存しない（本モジュールは fixture/matrix domain 知識
    のみを持つ独立モジュールという既存規約——`_SWEEP_TRUTH_FIELDS_BY_FAMILY`
    docstring の「他 module には依存せず本モジュールで独立に宣言する」と
    同じ理由）。"""
    return hmac_module.new(secret, message.encode("utf-8"), hashlib.sha256).hexdigest()


#: sweep stratum key 用の held-fixed field（§V2.2 3rd bullet）。(a) coverage
#: 軸該当（`generator_impl`、FORMANT_GT のみ）と (b) claim 構成次元
#: （IDENTITY_CAUSAL_SWEEP の `founder_id`/`trait`）の 2 例のみが 456 セル
#: canonical matrix に存在する。対応する field が無い family は単一
#: stratum（空 tuple）。
_SWEEP_STRATUM_KEY_FIELDS_BY_FAMILY: dict[str, tuple[str, ...]] = {
    FixtureFamily.F0_CONTROL.value: (),
    FixtureFamily.FORMANT_GT.value: ("generator_impl",),
    FixtureFamily.TILT_GT.value: (),
    FixtureFamily.APERIODICITY_GT.value: (),
    FixtureFamily.RESONANCE_GT.value: (),
    FixtureFamily.TRANSITION_GT.value: (),
    FixtureFamily.IDENTITY_CAUSAL_SWEEP.value: ("founder_id", "trait"),
}

#: claim-relevant field（§V2.2 5th bullet: 「construct の適用範囲を分割する」
#: held-fixed field）の機械導出許可語彙——本 tuple に列挙された field 名の
#: うち、family 内で実際に held-fixed field として変動するもの
#: （`_varying_fixed_field_names()`）だけが claim-relevant field になる
#: （`claim_relevant_fields_by_family()`）。この allow-list 自体は設計文書が
#: 明示する 5 field（TRANSITION_GT の join_type/duration_class、
#: APERIODICITY_GT の bandwise_band、IDENTITY_CAUSAL_SWEEP の
#: founder_id/trait）を凍結したもの——nuisance のみが変動する field
#: （f0_hz/sr_hz/gain_dbfs/duration_s/noise_snr_db/context/bandwidth_hz/
#: generator_impl 等）は意図的に含めない。
_CLAIM_DIVIDING_FIELD_NAMES: frozenset[str] = frozenset(
    {"join_type", "duration_class", "bandwise_band", "founder_id", "trait"}
)


def claim_relevant_fields_by_family(rows: Sequence[MatrixRow]) -> dict[str, tuple[str, ...]]:
    """family ごとの claim-relevant field を matrix 実体から機械導出する
    （§V2.2 5th bullet）。`_varying_fixed_field_names()`（family 内で実際に
    複数値を取る held-fixed field）と `_CLAIM_DIVIDING_FIELD_NAMES` の
    積集合——nuisance のみが変動する family は空 tuple（該当なし）。

    456 セル canonical matrix での帰結: TRANSITION_GT ->
    `("duration_class", "join_type")`、APERIODICITY_GT ->
    `("bandwise_band",)`、IDENTITY_CAUSAL_SWEEP ->
    `("founder_id", "trait")`、他 4 family -> `()`。
    """
    groups = _sweep_groups(rows)
    result: dict[str, tuple[str, ...]] = {}
    for family in axes.FAMILY_ORDER:
        fam = family.value
        family_sweeps = groups.get(fam, {})
        member_rows = [mr.row for members in family_sweeps.values() for mr in members]
        varying = set(_varying_fixed_field_names(member_rows))
        result[fam] = tuple(sorted(varying & _CLAIM_DIVIDING_FIELD_NAMES))
    return result


class HoldoutPinInfeasible(RuntimeError):
    """§V2.2 の被覆要件が cap `floor((N_hold-1)/r)` 内で充足不能な family
    構成（C0 fail-closed。456 セル canonical matrix では発生しない —
    `holdout_pin_params_by_family()` の実測値表を参照）。

    縮退規則（2026-09-04 追補）採用後は `cap < 1` の family はこの例外では
    なく pin 免除（`HoldoutPinParams.pin_exempt`）へ倒れるため、本例外が
    実際に発生するのは `cap >= 1` かつ `max_field_cardinality > cap`
    （＝holdout に pin 用の余地はあるが被覆要件を満たすには足りない）場合
    のみに縮小した。"""

    def __init__(self, family: str, *, max_field_cardinality: int, cap: int) -> None:
        self.family = family
        self.max_field_cardinality = max_field_cardinality
        self.cap = cap
        super().__init__(
            f"matrix: holdout sweep pin coverage requirement for family {family!r} "
            f"needs max_field_cardinality={max_field_cardinality} pinned sweep(s) but "
            f"cap floor((N_hold-1)/r)={cap} is smaller — infeasible, refusing to pin "
            "(fail-closed, §V2.2)"
        )


class HoldoutPinDegradationExhausted(RuntimeError):
    """§V2.2 縮退規則（2026-09-04 追補）: 段 2（`splitter.realize_split()` の
    coverage repair）が pin 選抜の結果として修復不能になり、k_hold を 1 ずつ
    下げて再試行しても、`family` の縮退下限（`degradation_floor` —
    claim 被覆 family（`max_field_cardinality > 1`: FORMANT_GT/
    IDENTITY_CAUSAL_SWEEP）では `max_field_cardinality`、それ以外は 0）を
    割り込む縮退が必要になった場合の fail-closed 構造化例外。被覆保証を
    静かに弱めない（R2/R4 巡で採用した被覆保証の維持）。呼び出し側
    （`c0_freeze._pin_and_realize_holdout()`）はこれを捕捉して
    `FreezeOutcome.VALIDATION_BLOCKED` へ変換し、未捕捉のまま
    `armed_freeze()` の外へ漏らさない。"""

    def __init__(self, family: str, *, floor: int, attempted_k: int) -> None:
        self.family = family
        self.floor = floor
        self.attempted_k = attempted_k
        super().__init__(
            f"matrix: holdout sweep pin degradation for family {family!r} would need "
            f"k_hold={attempted_k}, which is below the coverage-guarantee degradation "
            f"floor {floor} — refusing to degrade further (fail-closed, §V2.2 縮退規則)"
        )


@dataclass(frozen=True)
class HoldoutPinParams:
    """family ごとの §V2.2 k_hold 導出に使う構造値（secret 非依存。matrix
    実体のみから決まるため、C0 validation は split_secret なしにこの値を
    再導出して feasibility を検査できる）。"""

    family: str
    sweep_count: int  # S
    member_rows_per_sweep: int  # r（family 内で一様。456 セルでは常に一様）
    n_hold: int  # N_hold = family total の 25%（§5.2。456 セルでは常に整数）
    max_field_cardinality: int  # stratum-key field の値数の最大（無ければ 1）
    cap: int  # floor((N_hold - 1) / r)
    feasible: bool  # pin_exempt or (max_field_cardinality <= cap)
    k_hold: int  # min(max(floor(0.25*S+0.5), 1, max_field_cardinality), cap)。
    # pin_exempt な family は 0。
    #: §V2.2 縮退規則（2026-09-04 追補）: `cap < 1`（holdout が sweep 1 本 +
    #: 非 sweep 行 1 行すら収容できない）family は pin 免除（k_hold=0・
    #: `HoldoutPinInfeasible` を送出しない）。456 セル canonical matrix では
    #: 全 family とも False（`test_k_hold_matches_v2_2_frozen_table` 参照）。
    pin_exempt: bool = False
    #: §V2.2 縮退規則 2nd bullet: 段 2 修復不能時に k_hold を 1 ずつ下げる
    #: 縮退リトライの下限。claim 被覆 family（`max_field_cardinality > 1`:
    #: FORMANT_GT/IDENTITY_CAUSAL_SWEEP）は `max_field_cardinality`
    #: （被覆保証を静かに弱めない）、それ以外の family は 0（= pin 免除まで
    #: 縮退可能）。
    degradation_floor: int = 0


def holdout_pin_params_by_family(rows: Sequence[MatrixRow]) -> dict[str, HoldoutPinParams]:
    """§V2.2 の k_hold 完全形を family ごとに算出する（secret 非依存）。
    宣言 sweep を持たない family は結果から除外する（456 セルでは発生しない
    が、汎用性のため防御的に扱う）。"""
    groups = _sweep_groups(rows)
    result: dict[str, HoldoutPinParams] = {}
    for family in axes.FAMILY_ORDER:
        fam = family.value
        family_sweeps = groups.get(fam, {})
        if not family_sweeps:
            continue
        sweep_count = len(family_sweeps)
        member_lists = list(family_sweeps.values())
        member_rows_per_sweep = len(member_lists[0])
        stratum_fields = _SWEEP_STRATUM_KEY_FIELDS_BY_FAMILY.get(fam, ())
        if stratum_fields:
            max_field_cardinality = max(
                len({getattr(mr.row, f) for members in member_lists for mr in members})
                for f in stratum_fields
            )
        else:
            max_field_cardinality = 1
        total = axes.FAMILY_COUNTS[family][3]
        n_hold = total // 4
        cap = (n_hold - 1) // member_rows_per_sweep
        degradation_floor = max_field_cardinality if max_field_cardinality > 1 else 0
        # §V2.2 縮退規則（2026-09-04 追補）: cap<1（holdout が sweep 1 本 +
        # 非 sweep 行 1 行すら収容できない）は、被覆要件の大小に関わらず
        # 無条件で pin 免除する — `HoldoutPinInfeasible` はこの下で
        # `cap>=1 だが max_field_cardinality>cap` の場合にのみ発生する。
        pin_exempt = cap < 1
        if pin_exempt:
            feasible = True
            k_hold = 0
        else:
            feasible = max_field_cardinality <= cap
            ideal = math.floor(0.25 * sweep_count + 0.5)
            k_hold = min(max(ideal, 1, max_field_cardinality), cap)
        result[fam] = HoldoutPinParams(
            family=fam,
            sweep_count=sweep_count,
            member_rows_per_sweep=member_rows_per_sweep,
            n_hold=n_hold,
            max_field_cardinality=max_field_cardinality,
            cap=cap,
            feasible=feasible,
            k_hold=k_hold,
            pin_exempt=pin_exempt,
            degradation_floor=degradation_floor,
        )
    return result


def _pin_single_field_stratum(
    family_sweeps: Mapping[str, Sequence[MatrixRow]],
    secret: bytes,
    field_name: str,
    k_hold: int,
) -> tuple[str, ...]:
    """単一 field family（456 セルでは FORMANT_GT の `generator_impl`）の
    選抜（§V2.2 4th bullet 前段）: 値ごとに 1 件を先取りしてから残余を
    largest-remainder で配分する（全値 >= 1 を保証、同点は値の字句順）+
    stratum 内 HMAC-SHA256(secret, sweep_id) 昇順。"""
    value_of: dict[str, Any] = {
        sid: getattr(members[0].row, field_name) for sid, members in family_sweeps.items()
    }
    by_value: dict[Any, list[str]] = {}
    for sid, v in value_of.items():
        by_value.setdefault(v, []).append(sid)
    values = sorted(by_value, key=lambda v: str(v))
    n_values = len(values)
    if k_hold < n_values:
        raise HoldoutPinInfeasible(
            "<single-field-stratum>", max_field_cardinality=n_values, cap=k_hold
        )
    counts = {v: len(by_value[v]) for v in values}
    total_sweeps = sum(counts.values())
    extra_budget = k_hold - n_values
    quotas = {v: extra_budget * counts[v] / total_sweeps for v in values}
    floors = {v: math.floor(quotas[v]) for v in values}
    remainder = extra_budget - sum(floors.values())
    ranked_by_fraction = sorted(values, key=lambda v: (-(quotas[v] - floors[v]), str(v)))
    bumped = set(ranked_by_fraction[:remainder])
    per_value_target = {
        v: 1 + floors[v] + (1 if v in bumped else 0) for v in values
    }
    selected: list[str] = []
    for v in values:
        ordered = sorted(by_value[v], key=lambda sid: _pin_hmac_hex(secret, sid))
        selected.extend(ordered[: per_value_target[v]])
    return tuple(selected)


def _pin_identity_founder_trait(
    family_sweeps: Mapping[str, Sequence[MatrixRow]], secret: bytes, k_hold: int
) -> tuple[str, ...]:
    """IDENTITY_CAUSAL_SWEEP 型（stratum key = founder_id x trait, 各 cell
    sweep 1 個）の選抜（§V2.2 4th bullet 後段）: founder を
    HMAC-SHA256(secret, founder_id) 昇順、trait を HMAC-SHA256(secret,
    trait) 昇順に並べ、i 番目 (i=0..k_hold-1) の founder に trait
    `i mod len(traits)` の sweep を割当てる。"""
    sweep_by_cell: dict[tuple[str, str], str] = {}
    for sid, members in family_sweeps.items():
        row = members[0].row
        sweep_by_cell[(row.founder_id, row.trait)] = sid
    founders_sorted = sorted(axes.IDENTITY_FOUNDER_IDS, key=lambda f: _pin_hmac_hex(secret, f))
    traits_sorted = sorted(axes.IDENTITY_TRAITS, key=lambda t: _pin_hmac_hex(secret, t))
    if k_hold > len(founders_sorted):
        raise HoldoutPinInfeasible(
            FixtureFamily.IDENTITY_CAUSAL_SWEEP.value,
            max_field_cardinality=len(founders_sorted),
            cap=k_hold,
        )
    selected: list[str] = []
    for i in range(k_hold):
        founder = founders_sorted[i]
        trait = traits_sorted[i % len(traits_sorted)]
        selected.append(sweep_by_cell[(founder, trait)])
    return tuple(selected)


def _pin_claim_round_robin(
    family_sweeps: Mapping[str, Sequence[MatrixRow]],
    secret: bytes,
    claim_fields: tuple[str, ...],
    k_hold: int,
) -> tuple[str, ...]:
    """claim-relevant field を持つ単一 stratum family（456 セルでは
    TRANSITION_GT / APERIODICITY_GT）の選抜（§V2.2 5th bullet）: claim-
    relevant field の値組を HMAC-SHA256(secret, canonical 表現) 昇順に巡回し
    （ラウンドロビン）、各値組グループ内は HMAC-SHA256(secret, sweep_id)
    昇順で消費する。"""

    def value_tuple(sid: str) -> tuple[Any, ...]:
        row = family_sweeps[sid][0].row
        return tuple(getattr(row, f) for f in claim_fields)

    def canon(value_tup: tuple[Any, ...]) -> str:
        return "|".join(f"{field}={value!r}" for field, value in zip(claim_fields, value_tup))

    by_value: dict[tuple[Any, ...], list[str]] = {}
    for sid in family_sweeps:
        by_value.setdefault(value_tuple(sid), []).append(sid)

    ordered_values = sorted(by_value, key=lambda vt: _pin_hmac_hex(secret, canon(vt)))
    queues = [
        sorted(by_value[vt], key=lambda sid: _pin_hmac_hex(secret, sid))
        for vt in ordered_values
    ]
    n_groups = len(queues)
    total_available = sum(len(q) for q in queues)
    target = min(k_hold, total_available)
    cursor = [0] * n_groups
    selected: list[str] = []
    idx = 0
    while len(selected) < target:
        group = idx % n_groups
        if cursor[group] < len(queues[group]):
            selected.append(queues[group][cursor[group]])
            cursor[group] += 1
        idx += 1
    return tuple(selected)


def _pin_plain_topk(
    family_sweeps: Mapping[str, Sequence[MatrixRow]], secret: bytes, k_hold: int
) -> tuple[str, ...]:
    """stratum key も claim-relevant field も持たない family（456 セルでは
    F0_CONTROL / TILT_GT / RESONANCE_GT）の選抜（§V2.2 4th bullet: 「claim-
    relevant field が無い family は sweep_id HMAC 昇順の先頭 k」）。"""
    ordered = sorted(family_sweeps, key=lambda sid: _pin_hmac_hex(secret, sid))
    return tuple(ordered[:k_hold])


def pin_holdout_sweeps_by_family(
    rows: Sequence[MatrixRow],
    split_secret: bytes,
    *,
    k_hold_overrides: Mapping[str, int] | None = None,
) -> dict[str, dict[str, tuple[str, ...]]]:
    """§V2.2 段 1: family ごとに declared sweep から `k_hold` 個を HOLDOUT
    専属 sweep として pin する。戻り値は `declared_sweeps_by_family()` と
    同型（`family -> {sweep_id: (member row_id, ...)}`）だが、各 family は
    pin された sweep のみを含む部分集合を持つ（宣言 sweep が無い family、
    pin 免除 family、`k_hold_overrides` で 0 まで縮退した family は空
    dict）。

    被覆要件が cap `floor((N_hold-1)/r)` 内で充足不能な family 構成は
    `HoldoutPinInfeasible` で fail-closed する（456 セル canonical matrix
    では発生しない）。`cap < 1` の family は本関数に到達する前に
    `holdout_pin_params_by_family()` が pin 免除（`k_hold=0`）と判定して
    いるため、この例外を送出しない。

    `k_hold_overrides`（§V2.2 縮退規則、2026-09-04 追補）: 段 2
    （`splitter.realize_split()`）の coverage repair が pin 選抜の結果として
    修復不能になったときの決定論的再選抜リトライ用。family ごとに nominal
    `k_hold` を上書きする（`c0_freeze._pin_and_realize_holdout()` が使う）。
    上書き値が当該 family の `degradation_floor` を下回る場合は
    `HoldoutPinDegradationExhausted` で fail-closed する（claim 被覆
    family の被覆保証を静かに弱めない）。
    """
    groups = _sweep_groups(rows)
    params = holdout_pin_params_by_family(rows)
    claim_fields_by_family = claim_relevant_fields_by_family(rows)
    result: dict[str, dict[str, tuple[str, ...]]] = {}
    for family in axes.FAMILY_ORDER:
        fam = family.value
        family_sweeps = groups.get(fam, {})
        if not family_sweeps:
            result[fam] = {}
            continue
        p = params[fam]
        k_hold = p.k_hold
        if k_hold_overrides is not None and fam in k_hold_overrides:
            k_hold = k_hold_overrides[fam]
            if k_hold < p.degradation_floor:
                raise HoldoutPinDegradationExhausted(
                    fam, floor=p.degradation_floor, attempted_k=k_hold
                )
        if p.pin_exempt or k_hold <= 0:
            result[fam] = {}
            continue
        if not p.feasible:
            raise HoldoutPinInfeasible(
                fam, max_field_cardinality=p.max_field_cardinality, cap=p.cap
            )
        stratum_fields = _SWEEP_STRATUM_KEY_FIELDS_BY_FAMILY.get(fam, ())
        if stratum_fields == ("generator_impl",):
            pinned_ids = _pin_single_field_stratum(
                family_sweeps, split_secret, "generator_impl", k_hold
            )
        elif stratum_fields == ("founder_id", "trait"):
            pinned_ids = _pin_identity_founder_trait(family_sweeps, split_secret, k_hold)
        else:
            claim_fields = claim_fields_by_family.get(fam, ())
            if claim_fields:
                pinned_ids = _pin_claim_round_robin(
                    family_sweeps, split_secret, claim_fields, k_hold
                )
            else:
                pinned_ids = _pin_plain_topk(family_sweeps, split_secret, k_hold)
        result[fam] = {
            sid: tuple(sorted(mr.row_id for mr in family_sweeps[sid])) for sid in pinned_ids
        }
    return result
