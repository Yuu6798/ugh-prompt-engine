"""genome.py — P1 (VG-001): VoiceGenome v0.2 スキーマ。

`proto1_design_memo.md` §P1 の仕様に基づく frozen dataclass 群 + JSON
round-trip + 物理事前分布（凍結表）+ `out_of_physio_range` フラグ判定。

セクション構成（メモの記述順）:
  source（tilt, source_mode）/ resonance（formant_scale, formant_offsets[4],
  bandwidth_scale）/ noise（breathiness_base, register_gains[5]）/
  register（boundaries_midi[4], transition_width）/
  microprosody（vibrato_rate_hz, vibrato_depth_cents, jitter_amount,
  jitter_seed）/ range（lowest_midi, highest_midi）/
  physio_range（out_of_physio_range, violated_bounds）/
  audit（reference_set_hash, linkability_report_id, residual_gate_passed）。

`out_of_physio_range` は「生成拒否」ではなく **フラグ** である（§1.5 楽器
原理: 意図的な範囲外設計を禁止しない）。範囲外フィールドの一覧は
`PHYSIO_PRIOR_RANGES`（凍結表に無いフィールドは対象外。
`underspec_log_p1.md` [UNDERSPEC-P1-1] 参照）。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = "voice-genome/0.2"

# 5 声区の順序（noise.register_gains / voice_r0.REGISTERS と一致させる）。
REGISTER_ORDER: Tuple[str, str, str, str, str] = ("chest", "mix", "head", "falsetto", "whistle")


class GenomeValidationError(ValueError):
    """VoiceGenome の JSON デシリアライズ / 構築時の型・構造不正。"""


# ---------------------------------------------------------------------------
# セクション dataclass（すべて frozen = 値オブジェクトは不変）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceSection:
    tilt: float = -10.0  # dB/oct（基準ピッチ/intensity での spectral tilt）
    source_mode: str = "modal"


@dataclass(frozen=True)
class ResonanceSection:
    formant_scale: float = 1.0
    formant_offsets: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    bandwidth_scale: float = 1.0


@dataclass(frozen=True)
class NoiseSection:
    breathiness_base: float = 0.05
    # 順序は REGISTER_ORDER = (chest, mix, head, falsetto, whistle)
    register_gains: Tuple[float, float, float, float, float] = (0.0, 0.08, 0.18, 0.32, 0.50)


@dataclass(frozen=True)
class RegisterSection:
    # 昇順 4 境界: chest→mix, mix→head, head→falsetto, falsetto→whistle
    boundaries_midi: Tuple[float, float, float, float] = (52.0, 62.0, 74.0, 88.0)
    transition_width: float = 3.0


@dataclass(frozen=True)
class MicroprosodySection:
    vibrato_rate_hz: float = 5.5
    vibrato_depth_cents: float = 45.0
    jitter_amount: float = 0.006
    jitter_seed: int = 0


@dataclass(frozen=True)
class RangeSection:
    lowest_midi: float = 36.0  # C2
    highest_midi: float = 96.0  # C7


@dataclass(frozen=True)
class PhysioRangeSection:
    out_of_physio_range: bool = False
    violated_bounds: Tuple[str, ...] = ()


@dataclass(frozen=True)
class AuditSection:
    reference_set_hash: Optional[str] = None
    linkability_report_id: Optional[str] = None
    # R3（残差ゲート）は本試作品では未実装。None = not_applicable。
    residual_gate_passed: Optional[bool] = None


@dataclass(frozen=True)
class VoiceGenome:
    name: str
    source: SourceSection = field(default_factory=SourceSection)
    resonance: ResonanceSection = field(default_factory=ResonanceSection)
    noise: NoiseSection = field(default_factory=NoiseSection)
    register: RegisterSection = field(default_factory=RegisterSection)
    microprosody: MicroprosodySection = field(default_factory=MicroprosodySection)
    range: RangeSection = field(default_factory=RangeSection)
    physio_range: PhysioRangeSection = field(default_factory=PhysioRangeSection)
    audit: AuditSection = field(default_factory=AuditSection)
    schema_version: str = SCHEMA_VERSION


# ---------------------------------------------------------------------------
# 物理事前分布（凍結表。§3.2 の具体化）
# ---------------------------------------------------------------------------

# key はセクション名.フィールド名。値は [lo, hi]（両端含む）。
# 凍結表に無いフィールド（resonance.formant_offsets, resonance.bandwidth_scale）
# は意図的に対象外（underspec_log_p1.md [UNDERSPEC-P1-1]）。
PHYSIO_PRIOR_RANGES: Dict[str, Tuple[float, float]] = {
    "resonance.formant_scale": (0.80, 1.25),
    "noise.breathiness_base": (0.0, 0.6),
    "source.tilt": (-18.0, -3.0),
    "microprosody.vibrato_rate_hz": (4.0, 7.5),
    "microprosody.vibrato_depth_cents": (0.0, 150.0),
    "microprosody.jitter_amount": (0.0, 0.02),
    "register.transition_width": (1.0, 6.0),
}

REGISTER_BOUNDARY_RANGE: Tuple[float, float] = (40.0, 96.0)


def compute_physio_range(
    source: SourceSection,
    resonance: ResonanceSection,
    noise: NoiseSection,
    register: RegisterSection,
    microprosody: MicroprosodySection,
) -> PhysioRangeSection:
    """凍結表に照らして out_of_physio_range / violated_bounds を判定する。"""
    violated: List[str] = []

    def _check(key: str, value: float) -> None:
        lo, hi = PHYSIO_PRIOR_RANGES[key]
        if not (lo <= value <= hi):
            violated.append(key)

    _check("resonance.formant_scale", resonance.formant_scale)
    _check("noise.breathiness_base", noise.breathiness_base)
    _check("source.tilt", source.tilt)
    _check("microprosody.vibrato_rate_hz", microprosody.vibrato_rate_hz)
    _check("microprosody.vibrato_depth_cents", microprosody.vibrato_depth_cents)
    _check("microprosody.jitter_amount", microprosody.jitter_amount)
    _check("register.transition_width", register.transition_width)

    lo, hi = REGISTER_BOUNDARY_RANGE
    for i, b in enumerate(register.boundaries_midi):
        if not (lo <= b <= hi):
            violated.append(f"register.boundaries_midi[{i}]")

    boundaries = register.boundaries_midi
    if any(boundaries[i] >= boundaries[i + 1] for i in range(len(boundaries) - 1)):
        violated.append("register.boundaries_midi.ascending")

    return PhysioRangeSection(out_of_physio_range=bool(violated), violated_bounds=tuple(violated))


# ---------------------------------------------------------------------------
# ファクトリ（physio_range を自動計算して構築する）
# ---------------------------------------------------------------------------


def build_genome(
    name: str,
    source: Optional[SourceSection] = None,
    resonance: Optional[ResonanceSection] = None,
    noise: Optional[NoiseSection] = None,
    register: Optional[RegisterSection] = None,
    microprosody: Optional[MicroprosodySection] = None,
    range_: Optional[RangeSection] = None,
    audit: Optional[AuditSection] = None,
) -> VoiceGenome:
    """physio_range を凍結表から自動計算して VoiceGenome を組み立てる。

    sampler.py / mutate 等、パラメータから新しい Genome を作るすべての経路は
    本関数（か `revalidate_physio_range`）を経由し、`out_of_physio_range` の
    捏造・失念を防ぐ。
    """
    source = source or SourceSection()
    resonance = resonance or ResonanceSection()
    noise = noise or NoiseSection()
    register = register or RegisterSection()
    microprosody = microprosody or MicroprosodySection()
    range_ = range_ or RangeSection()
    audit = audit or AuditSection()

    physio = compute_physio_range(source, resonance, noise, register, microprosody)

    return VoiceGenome(
        name=name,
        source=source,
        resonance=resonance,
        noise=noise,
        register=register,
        microprosody=microprosody,
        range=range_,
        physio_range=physio,
        audit=audit,
    )


def with_audit(genome: VoiceGenome, audit: AuditSection) -> VoiceGenome:
    """既存 Genome の audit セクションだけを差し替えた新インスタンスを返す（frozen 対応）。

    F1 (final_assembly_memo.md) の linkability 監査結果
    （reference_set_hash / linkability_report_id）を Genome の audit フィールドへ
    書き戻す際に使う。physio_range は audit と無関係なので再計算しない。
    """
    return VoiceGenome(
        name=genome.name,
        source=genome.source,
        resonance=genome.resonance,
        noise=genome.noise,
        register=genome.register,
        microprosody=genome.microprosody,
        range=genome.range,
        physio_range=genome.physio_range,
        audit=audit,
        schema_version=genome.schema_version,
    )


def revalidate_physio_range(genome: VoiceGenome) -> VoiceGenome:
    """既存 Genome の physio_range を現在の凍結表で再計算した新インスタンスを返す。"""
    physio = compute_physio_range(
        genome.source, genome.resonance, genome.noise, genome.register, genome.microprosody
    )
    return VoiceGenome(
        name=genome.name,
        source=genome.source,
        resonance=genome.resonance,
        noise=genome.noise,
        register=genome.register,
        microprosody=genome.microprosody,
        range=genome.range,
        physio_range=physio,
        audit=genome.audit,
        schema_version=genome.schema_version,
    )


# ---------------------------------------------------------------------------
# JSON round-trip
# ---------------------------------------------------------------------------


def to_dict(genome: VoiceGenome) -> Dict[str, Any]:
    return asdict(genome)


def to_json(genome: VoiceGenome, *, indent: Optional[int] = 2) -> str:
    return json.dumps(to_dict(genome), indent=indent, sort_keys=True, ensure_ascii=True)


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise GenomeValidationError(msg)


def _req(d: Dict[str, Any], key: str, field_name: str) -> Any:
    """d[key] を返す。キー自体が欠落している場合は拒否する（PR#261 レビュー
    R16: `.get(key, デフォルト値)` によるキー欠落時のデフォルト補完は、
    「切り詰められた/古い/手編集された payload」を「全フィールドが揃った
    正しい payload」として fail-open してしまう。デフォルト補完が許される
    のは `build_genome()` 等の明示的コンストラクタ経路のみであり、JSON
    からの逆直列化 (`from_dict`) では全セクション・全リーフフィールドの
    存在を必須とする）。
    """
    _require(key in d, f"{field_name} キーが存在しない（欠落）")
    return d[key]


def _as_float(value: Any, field_name: str) -> float:
    _require(isinstance(value, (int, float)) and not isinstance(value, bool), f"{field_name} は数値でなければならない: {value!r}")
    return float(value)


def _as_int(value: Any, field_name: str) -> int:
    _require(isinstance(value, int) and not isinstance(value, bool), f"{field_name} は整数でなければならない: {value!r}")
    return int(value)


def _as_str(value: Any, field_name: str) -> str:
    _require(isinstance(value, str), f"{field_name} は文字列でなければならない: {value!r}")
    return value


def _as_bool(value: Any, field_name: str) -> bool:
    _require(isinstance(value, bool), f"{field_name} は真偽値でなければならない: {value!r}")
    return value


def _as_float_tuple(value: Any, field_name: str, length: int) -> Tuple[float, ...]:
    _require(isinstance(value, (list, tuple)), f"{field_name} はリストでなければならない: {value!r}")
    _require(len(value) == length, f"{field_name} は長さ {length} でなければならない（実際: {len(value)}）")
    return tuple(_as_float(v, f"{field_name}[{i}]") for i, v in enumerate(value))


def from_dict(data: Dict[str, Any]) -> VoiceGenome:
    """辞書から VoiceGenome を再構築する。型・構造不正は GenomeValidationError で拒否する。

    schema_version は宣言値が `SCHEMA_VERSION` と一致しなければ拒否する（将来の
    非互換スキーマを現行レイアウトで誤解釈して fail-open することを防ぐ。
    PR#261 レビュー C6）。physio_range は宣言値をそのまま信頼せず、実パラメータ
    から `compute_physio_range()` で再計算した値と一致しなければ拒否する
    （改ざん・手編集された安全性フラグの通過を防ぐ。PR#261 レビュー C5）。
    schema_version キー自体が欠落している場合も、現行版へのデフォルト補完は
    行わず明示的に拒否する（PR#261 レビュー R12: `.get(..., SCHEMA_VERSION)`
    のフォールバックは「キー欠落」と「現行版を宣言」を区別できず、前者を
    後者として fail-open してしまっていた）。同じ理由で、全セクション
    （source/resonance/noise/register/microprosody/range/physio_range/audit）
    と各リーフフィールドについても、キー欠落時のデフォルト補完は行わず
    明示的に拒否する（PR#261 レビュー R16。デフォルト補完が許されるのは
    `build_genome()` 等の明示的コンストラクタ経路のみ）。
    """
    _require(isinstance(data, dict), "genome データは dict でなければならない")

    _require("schema_version" in data, "schema_version キーが存在しない（欠落）")
    schema_version = _as_str(data["schema_version"], "schema_version")
    _require(
        schema_version == SCHEMA_VERSION,
        f"schema_version は {SCHEMA_VERSION!r} でなければならない（実際: {schema_version!r}）",
    )

    name = _as_str(_req(data, "name", "name"), "name")

    s = _req(data, "source", "source")
    _require(isinstance(s, dict), "source は dict でなければならない")
    source = SourceSection(
        tilt=_as_float(_req(s, "tilt", "source.tilt"), "source.tilt"),
        source_mode=_as_str(_req(s, "source_mode", "source.source_mode"), "source.source_mode"),
    )

    r = _req(data, "resonance", "resonance")
    _require(isinstance(r, dict), "resonance は dict でなければならない")
    resonance = ResonanceSection(
        formant_scale=_as_float(_req(r, "formant_scale", "resonance.formant_scale"), "resonance.formant_scale"),
        formant_offsets=_as_float_tuple(
            _req(r, "formant_offsets", "resonance.formant_offsets"), "resonance.formant_offsets", 4
        ),
        bandwidth_scale=_as_float(_req(r, "bandwidth_scale", "resonance.bandwidth_scale"), "resonance.bandwidth_scale"),
    )

    n = _req(data, "noise", "noise")
    _require(isinstance(n, dict), "noise は dict でなければならない")
    noise = NoiseSection(
        breathiness_base=_as_float(_req(n, "breathiness_base", "noise.breathiness_base"), "noise.breathiness_base"),
        register_gains=_as_float_tuple(
            _req(n, "register_gains", "noise.register_gains"), "noise.register_gains", 5
        ),
    )

    reg = _req(data, "register", "register")
    _require(isinstance(reg, dict), "register は dict でなければならない")
    register = RegisterSection(
        boundaries_midi=_as_float_tuple(
            _req(reg, "boundaries_midi", "register.boundaries_midi"), "register.boundaries_midi", 4
        ),
        transition_width=_as_float(
            _req(reg, "transition_width", "register.transition_width"), "register.transition_width"
        ),
    )

    mp = _req(data, "microprosody", "microprosody")
    _require(isinstance(mp, dict), "microprosody は dict でなければならない")
    microprosody = MicroprosodySection(
        vibrato_rate_hz=_as_float(
            _req(mp, "vibrato_rate_hz", "microprosody.vibrato_rate_hz"), "microprosody.vibrato_rate_hz"
        ),
        vibrato_depth_cents=_as_float(
            _req(mp, "vibrato_depth_cents", "microprosody.vibrato_depth_cents"), "microprosody.vibrato_depth_cents"
        ),
        jitter_amount=_as_float(
            _req(mp, "jitter_amount", "microprosody.jitter_amount"), "microprosody.jitter_amount"
        ),
        jitter_seed=_as_int(_req(mp, "jitter_seed", "microprosody.jitter_seed"), "microprosody.jitter_seed"),
    )

    rg = _req(data, "range", "range")
    _require(isinstance(rg, dict), "range は dict でなければならない")
    range_ = RangeSection(
        lowest_midi=_as_float(_req(rg, "lowest_midi", "range.lowest_midi"), "range.lowest_midi"),
        highest_midi=_as_float(_req(rg, "highest_midi", "range.highest_midi"), "range.highest_midi"),
    )

    pr = _req(data, "physio_range", "physio_range")
    _require(isinstance(pr, dict), "physio_range は dict でなければならない")
    vb = _req(pr, "violated_bounds", "physio_range.violated_bounds")
    _require(isinstance(vb, (list, tuple)), "physio_range.violated_bounds はリストでなければならない")
    physio_range = PhysioRangeSection(
        out_of_physio_range=_as_bool(
            _req(pr, "out_of_physio_range", "physio_range.out_of_physio_range"), "physio_range.out_of_physio_range"
        ),
        violated_bounds=tuple(_as_str(v, "physio_range.violated_bounds[]") for v in vb),
    )
    recomputed_physio_range = compute_physio_range(source, resonance, noise, register, microprosody)
    _require(
        physio_range == recomputed_physio_range,
        "physio_range が実パラメータからの再計算値と不一致（宣言値: "
        f"out_of_physio_range={physio_range.out_of_physio_range}, "
        f"violated_bounds={physio_range.violated_bounds!r} / 再計算値: "
        f"out_of_physio_range={recomputed_physio_range.out_of_physio_range}, "
        f"violated_bounds={recomputed_physio_range.violated_bounds!r}）",
    )

    au = _req(data, "audit", "audit")
    _require(isinstance(au, dict), "audit は dict でなければならない")
    ref_hash = _req(au, "reference_set_hash", "audit.reference_set_hash")
    _require(ref_hash is None or isinstance(ref_hash, str), "audit.reference_set_hash は str か null")
    link_id = _req(au, "linkability_report_id", "audit.linkability_report_id")
    _require(link_id is None or isinstance(link_id, str), "audit.linkability_report_id は str か null")
    residual = _req(au, "residual_gate_passed", "audit.residual_gate_passed")
    _require(residual is None or isinstance(residual, bool), "audit.residual_gate_passed は bool か null")
    audit = AuditSection(reference_set_hash=ref_hash, linkability_report_id=link_id, residual_gate_passed=residual)

    return VoiceGenome(
        name=name,
        source=source,
        resonance=resonance,
        noise=noise,
        register=register,
        microprosody=microprosody,
        range=range_,
        physio_range=physio_range,
        audit=audit,
        schema_version=schema_version,
    )


def from_json(text: str) -> VoiceGenome:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise GenomeValidationError(f"不正な JSON: {exc}") from exc
    return from_dict(data)
