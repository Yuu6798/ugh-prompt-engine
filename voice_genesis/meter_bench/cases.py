"""meter 別 BenchCase 集合（リセット設計 v0 §1）。

case は「正解が分かっている 1 本の合成音 + その音に対する 1 個の判定」であり、
生成は Tier K の凍結 generator（`calibration/fixtures/generators/*`）へ
**パラメータを渡すだけ**（生成式は本パッケージでは触らない = §7 範囲外）。

判定式は 3 種のみ:

- `abs_error`: `|observed - truth|` を `criteria.yaml` の `tol` と比べる。
  比較単位は `scale`（`absolute` / `cents` / `relative`）が決める。
- `monotone`: 同一 `group` の case 群で truth と observed の Kendall tau を
  取り、`polarity` を掛けて `tau_min` と比べる。
- `no_fire`: 負例（NOISE_ONLY / SILENCE）で検出器が発火しないこと。

P0–P2 の実装範囲は M2_SPECTRAL_TILT のみ。他 meter の case は P3/P4 で足す
（骨格・criteria.yaml は先に置いてある）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from voice_genesis.calibration import vocab
from voice_genesis.calibration.fixtures.axes import FixtureFamily
from voice_genesis.calibration.fixtures.matrix import FixtureRow

ABS_ERROR = "abs_error"
MONOTONE = "monotone"
NO_FIRE = "no_fire"
CHECKS: tuple[str, ...] = (ABS_ERROR, MONOTONE, NO_FIRE)
"""判定式の閉語彙（§1「判定式は 3 種のみ。増やさない」）。"""

SCALE_ABSOLUTE = "absolute"
SCALE_CENTS = "cents"
SCALE_RELATIVE = "relative"
SCALES: tuple[str, ...] = (SCALE_ABSOLUTE, SCALE_CENTS, SCALE_RELATIVE)
"""`abs_error` の比較単位（criteria の `unit` と対応する）。"""

NEGATIVE_CONTROLS: tuple[str, ...] = ("NOISE_ONLY", "SILENCE")
"""全 meter 共通の負例（§1 criteria 表「全 meter 負例 no_fire」）。"""

#: 1 case = 1 秒音 1 本（§1）。sr / gain / context は anchor 水準に固定する。
CASE_SR_HZ = 48000
CASE_GAIN_DBFS = -12.0
CASE_DURATION_S = 1.0
CASE_CONTEXT = "steady-isolated"


@dataclass(frozen=True)
class BenchCase:
    """1 case = (生成パラメータ, 真値, 判定式)。"""

    meter: vocab.MeterId
    family: FixtureFamily
    case_id: str
    gen_params: Mapping[str, object]
    """`FixtureRow` フィールドの上書き（生成器へ渡す値そのもの）。"""
    truth: float | None
    check: str
    field_name: str | None = None
    """`MeterOutput.values` から observed を読むキー（`no_fire` では未使用）。"""
    scale: str = SCALE_ABSOLUTE
    measure_params: Mapping[str, object] = field(default_factory=dict)
    """候補の `parameters` に重ねて `measure()` へ渡す値（例: 計器に渡す f0）。"""
    group: str | None = None
    """`monotone` の評価単位。"""
    polarity: int = 1
    """`monotone` の truth 方向と construct 方向の関係（+1 同方向 / -1 逆方向）。"""

    def __post_init__(self) -> None:
        if self.check not in CHECKS:
            raise ValueError(f"BenchCase.check must be one of {CHECKS}; got {self.check!r}")
        if self.scale not in SCALES:
            raise ValueError(f"BenchCase.scale must be one of {SCALES}; got {self.scale!r}")
        if self.polarity not in (1, -1):
            raise ValueError(f"BenchCase.polarity must be +1 or -1; got {self.polarity!r}")
        if self.check != NO_FIRE and (self.truth is None or self.field_name is None):
            raise ValueError(f"{self.case_id}: {self.check} needs both truth and field_name")
        if self.check == MONOTONE and self.group is None:
            raise ValueError(f"{self.case_id}: monotone needs a group")

    def row(self) -> FixtureRow:
        """`gen_params` を anchor 水準に重ねた生成レシピ。"""
        base: dict[str, object] = {
            "family": self.family.value,
            "block": "TRUTH_CORE",
            "f0_hz": 0.0,
            "sr_hz": CASE_SR_HZ,
            "gain_dbfs": CASE_GAIN_DBFS,
            "duration_s": CASE_DURATION_S,
            "noise_clean": True,
            "noise_snr_db": None,
            "context": CASE_CONTEXT,
        }
        base.update(self.gen_params)
        return FixtureRow(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# M2_SPECTRAL_TILT（P1）: 84 正例 + 2 負例
# ---------------------------------------------------------------------------

TILT_SLOPE_DB_PER_OCT: tuple[float, ...] = (-3.0, -6.0, -9.0, -12.0)
TILT_F0_HZ: tuple[float, ...] = (110.0, 220.0, 440.0)
TILT_F0_ERROR_PCT: tuple[float, ...] = (0.0, 1.0, -1.0, 2.0, -2.0, 3.0, -3.0)
"""計器へ渡す f0 を真値から何 % ずらすか。現行 TILT の構造欠陥
（`tilt_harmonic.harmonic_amplitudes_db` が `k*f0` の bin を固定で読み、ピーク
探索をしない）はこの誤差を k 倍に増幅する。誤差 0 の列だけを見ていると欠陥が
見えないため、この軸を case 集合の一級の軸に置く。"""

TILT_FIELD = "tilt_db_per_oct"


def _tilt_cases() -> tuple[BenchCase, ...]:
    out: list[BenchCase] = []
    for slope in TILT_SLOPE_DB_PER_OCT:
        for f0_hz in TILT_F0_HZ:
            for err_pct in TILT_F0_ERROR_PCT:
                out.append(
                    BenchCase(
                        meter=vocab.MeterId.M2_SPECTRAL_TILT,
                        family=FixtureFamily.TILT_GT,
                        case_id=f"tilt-slope{slope:+.0f}-f0{f0_hz:.0f}-err{err_pct:+.0f}pct",
                        gen_params=MappingProxyType(
                            {"f0_hz": f0_hz, "slope_db_per_oct": slope}
                        ),
                        truth=slope,
                        check=ABS_ERROR,
                        field_name=TILT_FIELD,
                        measure_params=MappingProxyType(
                            {"f0_hz": f0_hz * (1.0 + err_pct / 100.0)}
                        ),
                    )
                )
    for control in NEGATIVE_CONTROLS:
        out.append(
            BenchCase(
                meter=vocab.MeterId.M2_SPECTRAL_TILT,
                family=FixtureFamily.TILT_GT,
                case_id=f"tilt-negative-{control.lower()}",
                gen_params=MappingProxyType(
                    {
                        "f0_hz": TILT_F0_HZ[1],
                        "slope_db_per_oct": TILT_SLOPE_DB_PER_OCT[1],
                        "control_class": control,
                        "block": "NEGATIVE_CONTROL",
                    }
                ),
                truth=None,
                check=NO_FIRE,
                measure_params=MappingProxyType({"f0_hz": TILT_F0_HZ[1]}),
            )
        )
    return tuple(out)


CASES_BY_METER: Mapping[vocab.MeterId, tuple[BenchCase, ...]] = MappingProxyType(
    {vocab.MeterId.M2_SPECTRAL_TILT: _tilt_cases()}
)
"""実装済み meter → case 集合。未実装 meter はキー自体が無い（P3/P4 で足す）。"""


def cases_for(meter: vocab.MeterId) -> tuple[BenchCase, ...]:
    try:
        return CASES_BY_METER[meter]
    except KeyError:
        implemented = ", ".join(sorted(m.value for m in CASES_BY_METER))
        raise KeyError(
            f"meter_bench: {meter.value} の case は未実装（実装済み: {implemented}）"
        ) from None
