"""462 logical cell の明示列挙（設計正本 §5 / IMPLEMENTATION_MAP §2.7 FROZEN spec、
UNDERSPEC-CAL-D75 ruling (2) により 456→462 へ改訂）。

行選択の裁量は残さない: 本モジュールは §2.7 の truth core 因子分解・正準
nuisance 系列・正準 boundary/negative 系列・per-family targeted interaction
列挙を機械的に転記して 462 行を組み立てる。数値未確定箇所は
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

from collections.abc import Sequence
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
        # UNDERSPEC-CAL-D75 ruling (2): 12(nuisance, gain 軸 3 本目追加後)
        # + 1(interaction) = 13。旧 target_n=12 は nuisance 系列 11 行+
        # interaction 1 行の旧サイズにちょうど一致していたため、gain 軸の
        # 3 本目追加分だけ引き上げ、interaction 行が切り捨てられないように
        # する（据え置けば targeted interaction 行が丸ごと消える）。
        target_n=13,
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
        # UNDERSPEC-CAL-D75 ruling (2): 旧 target_n=6 は filtered nuisance
        # 系列（noise 軸除外後、gain 2+duration 3+context 3 = 8 行、gain 軸
        # 3 本目追加後は 9 行）を context 軸 1 行まで切り捨てていた（declared
        # sweep "context" が PRIMARY domain で 1 行しか持てず §10.4 の
        # resolvable-pair 最低数 (>=3) を構造的に満たせなかった根因）。
        # target_n を filtered 系列の全長 9 へ引き上げ、切り捨てなしで
        # gain/duration/context 各 3 行を確保する。
        target_n=9,
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
        # UNDERSPEC-CAL-D75 ruling (2): TILT_GT と同じ根拠で 12->13。
        target_n=13,
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
        # UNDERSPEC-CAL-D75 ruling (2): filtered nuisance 系列（context 軸
        # 除外後、gain 2+duration 3+noise 3=8 行、gain 軸 3 本目追加後は 9
        # 行）+ interaction 4 行 = 13。旧 target_n=12 は 8+4=12 の旧サイズに
        # 一致していたため、gain 軸の 3 本目追加分だけ引き上げる。
        target_n=13,
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
    """462 logical cell 全ての明示列挙を、`axes.FAMILY_ORDER` の順・各 family 内は
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
    """§5.2 の per-family 内訳・total 462・row_id 一意性を検証する。
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
# 宣言済み sweep（UNDERSPEC-CAL-D75 ruling (1)、§10.4 DIRECTIONAL gate 前提）
# ---------------------------------------------------------------------------

#: `axes.CANONICAL_NUISANCE_SEQUENCE` の `(axis_family, field_name, value)`
#: から `field_name -> axis_family` を逆引きする（`nuisance_tag` 文字列
#: `f"{field_name}={value!r}"` / `f"A2:{field_name}={value!r}"` から
#: axis_family を復元するための唯一の入力。1 つの field_name が複数
#: axis_family へまたがることはない前提 — 逆引きの一意性は
#: `test_matrix.py` 側で検査する）。
_FIELD_NAME_TO_AXIS_FAMILY: dict[str, str] = {
    field_name: axis_family
    for axis_family, field_name, _value in axes.CANONICAL_NUISANCE_SEQUENCE
}


def nuisance_axis_family(row: FixtureRow) -> str | None:
    """`row.nuisance_tag` が属す axis_family（`"gain"`/`"duration"`/
    `"noise"`/`"context"`）を返す。truth-core/targeted-interaction/boundary/
    negative-control 行（`nuisance_tag is None`）は sweep（§10.4 の
    resolvable-pair 最低数判定の単位。`gates.DirectionalPair.sweep_id`）の
    対象外として `None` を返す — f0_hz/sr_hz は truth-core grid であり
    sweep ではない、という UNDERSPEC-CAL-D75 ruling (1) の frozen matrix 側
    の実装点はここに閉じる。"""
    tag = row.nuisance_tag
    if tag is None:
        return None
    field_part = tag.split("=", 1)[0]
    if field_part.startswith("A2:"):
        field_part = field_part[len("A2:") :]
    return _FIELD_NAME_TO_AXIS_FAMILY.get(field_part)


def sweep_primary_row_counts(rows: Sequence[MatrixRow]) -> dict[str, dict[str, int]]:
    """family -> {sweep(axis_family): PRIMARY domain 内の行数}
    （UNDERSPEC-CAL-D75 ruling (1)/(2) の共通入力。`declared_sweeps_by_
    family()` の宣言集合と、`c0_validate` の sweep 容量検査
    (`>= gates.MIN_RESOLVABLE_PAIRS_PER_SWEEP`) の両方がこの関数の出力を
    使う)。"""
    counts: dict[str, dict[str, int]] = {family.value: {} for family in axes.FAMILY_ORDER}
    for mr in rows:
        if mr.domain is not Domain.PRIMARY:
            continue
        axis_family = nuisance_axis_family(mr.row)
        if axis_family is None:
            continue
        family_counts = counts.setdefault(mr.row.family, {})
        family_counts[axis_family] = family_counts.get(axis_family, 0) + 1
    return counts


def declared_sweeps_by_family(rows: Sequence[MatrixRow]) -> dict[str, tuple[str, ...]]:
    """UNDERSPEC-CAL-D75 ruling (1): family の宣言済み sweep 集合は、凍結
    matrix が PRIMARY domain 内で `nuisance_tag` を介して実際に変動させる
    confound 軸そのもの — truth-core grid の f0_hz/sr_hz は sweep ではなく、
    family が除外する軸（§2.7 exclude_axis_family。例: APERIODICITY_GT の
    noise・TRANSITION_GT の context）は当該 family の PRIMARY 行に
    `nuisance_tag` として一度も現れないため、この導出だけで自然に集合から
    漏れる（別途の除外リストを持つ必要がない）。

    `build_matrix()` の出力から決定的に導出する唯一の入口——生成レシピ
    （`_build_confound_block` 等）が変われば、この関数の出力も機械的に
    追従する。以下の 3 箇所が本関数の出力を最終的な権威として共有する:
    `c0_freeze._fixture_specs()`（`frozen_design.fixture_spec.<FAMILY>.
    confound_axes` として凍結し `manifest_core_sha` へ含める）、
    `campaign.holdout_stage.declared_axes_for_family()`（凍結 manifest から
    その値を読み戻す）、`campaign.cli._run_c4`（`gates.
    resolvable_pairs_possible()` へ渡す `expected_sweep_ids`）。旧実装
    （`c0_freeze._CONFOUND_AXES` の flat 6-tuple 固定値・`cli._run_c4` の
    fabricated `"default"` sweep）はいずれも本関数へ一本化して除去した。
    """
    counts = sweep_primary_row_counts(rows)
    return {family: tuple(sorted(sweeps)) for family, sweeps in counts.items()}
