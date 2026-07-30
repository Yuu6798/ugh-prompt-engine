"""melody/representation.py — M3a: 観測ノート列 → 正規化系列（旋律表現）。

`observability.py` が返す `MelodyNote` 列を、移調・変速に不変な正規化系列
（音程列・輪郭列・IOI 比列・音長比列）へ変換する。比較（対応付け・類似度算出）は
`alignment.py`（M3b）/ `comparison.py`（M3c）の仕事であり、本モジュールは表現の
正規化と `m3_comparison_registry.yaml`（M3 比較器の事前登録定数）のロードのみを
担う。

`observability.py` / `accuracy.py` / 既存レジストリ（`registry.yaml` /
`m2_accuracy_bars.yaml`）の凍結値は一切変更しない（`docs/DESIGN_M3_melody_comparator.md`
§8）。
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import MISSING
from dataclasses import fields as dc_fields
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

from svp_rpe.melody.observability import MelodyNote

__all__ = [
    "RepresentationConfig",
    "AlignmentConfig",
    "CoverageConfig",
    "EvidenceThresholdsConfig",
    "SeparationMarginConfig",
    "M3ComparisonConfig",
    "load_m3_registry",
    "MelodySequences",
    "build_sequences",
    "split_phrases",
    "sequence_sha256",
]

_EXPECTED_SCHEMA = "m3-comparison/0.1"


# --------------------------------------------------------------------------- #
# レジストリ（M3 比較器の事前登録定数）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RepresentationConfig:
    """`m3_comparison_registry.yaml` の ``representation`` 節。"""

    pitch_quantization_semitones: int
    contour_small_max_semitones: int
    ioi_ratio_log2_step: float
    duration_ratio_log2_step: float
    chroma_fold_semitones: int
    octave_artifact_divergence: float


@dataclass(frozen=True)
class AlignmentConfig:
    """`m3_comparison_registry.yaml` の ``alignment`` 節。"""

    match_score: float
    mismatch_score: float
    gap_open: float
    gap_extend: float
    traceback_preference: Tuple[str, ...]
    phrase_gap_sec: float
    phrase_gap_score: float


@dataclass(frozen=True)
class CoverageConfig:
    """`m3_comparison_registry.yaml` の ``coverage`` 節。"""

    floor: float
    floor_status: str


@dataclass(frozen=True)
class EvidenceThresholdsConfig:
    """`m3_comparison_registry.yaml` の ``evidence_thresholds`` 節。

    ``status`` が ``"uncalibrated"`` の間は ``axes`` を持たない（M3d で軸別に
    凍結した時点で ``axes: {contour: {...}, interval: {...}, rhythm: {...}}`` が
    追加される）。
    """

    status: str
    axes: Optional[Dict[str, Dict[str, float]]] = None


@dataclass(frozen=True)
class SeparationMarginConfig:
    """`m3_comparison_registry.yaml` の ``separation_margin`` 節。"""

    min_same_minus_cross_margin: float


@dataclass(frozen=True)
class M3ComparisonConfig:
    """`m3_comparison_registry.yaml`（m3-comparison/0.1）を検証済みで保持する。

    `ObservabilityThresholds.from_registry` と同型: 未知キー / schema 不一致 /
    必須キー欠落はすべて fail-fast（`ValueError`）。事前登録の逸脱を握りつぶさない。
    """

    schema: str
    registered_utc: str
    representation: RepresentationConfig
    alignment: AlignmentConfig
    coverage: CoverageConfig
    evidence_thresholds: EvidenceThresholdsConfig
    separation_margin: SeparationMarginConfig

    @classmethod
    def from_registry(cls, mapping: Dict[str, Any]) -> "M3ComparisonConfig":
        if not isinstance(mapping, dict):
            raise ValueError(
                f"m3_comparison_registry must be a mapping (got {type(mapping).__name__})"
            )
        schema = mapping.get("schema")
        if schema != _EXPECTED_SCHEMA:
            raise ValueError(
                f"unsupported m3-comparison registry schema {schema!r}; "
                f"expected {_EXPECTED_SCHEMA} (fail-closed)"
            )
        known_top = {
            "schema",
            "registered_utc",
            "representation",
            "alignment",
            "coverage",
            "evidence_thresholds",
            "separation_margin",
        }
        unknown_top = set(mapping) - known_top
        if unknown_top:
            raise ValueError(
                f"unknown m3_comparison_registry top-level keys (pre-registration "
                f"violation): {sorted(unknown_top)}"
            )
        missing_top = known_top - {"schema"} - set(mapping)
        if missing_top:
            raise ValueError(
                f"m3_comparison_registry missing required top-level keys: {sorted(missing_top)}"
            )
        registered_utc = mapping["registered_utc"]
        if not isinstance(registered_utc, str) or not registered_utc:
            raise ValueError(
                f"m3_comparison_registry registered_utc {registered_utc!r} must be a "
                "non-empty string"
            )

        alignment_mapping = dict(mapping["alignment"])
        if "traceback_preference" in alignment_mapping:
            alignment_mapping["traceback_preference"] = tuple(
                alignment_mapping["traceback_preference"]
            )

        return cls(
            schema=schema,
            registered_utc=registered_utc,
            representation=_build_section(
                RepresentationConfig, mapping["representation"], what="representation"
            ),
            alignment=_build_section(AlignmentConfig, alignment_mapping, what="alignment"),
            coverage=_build_section(CoverageConfig, mapping["coverage"], what="coverage"),
            evidence_thresholds=_build_section(
                EvidenceThresholdsConfig,
                mapping["evidence_thresholds"],
                what="evidence_thresholds",
            ),
            separation_margin=_build_section(
                SeparationMarginConfig,
                mapping["separation_margin"],
                what="separation_margin",
            ),
        )


def _known_field_names(dc: type) -> set:
    return {f.name for f in dc_fields(dc)}


def _required_field_names(dc: type) -> set:
    return {
        f.name
        for f in dc_fields(dc)
        if f.default is MISSING and f.default_factory is MISSING  # type: ignore[misc]
    }


def _build_section(dc: type, mapping: Any, *, what: str) -> Any:
    """`mapping` から frozen dataclass `dc` を構築する（未知/欠落キー fail-fast）。"""
    if not isinstance(mapping, dict):
        raise ValueError(f"m3_comparison_registry.{what} must be a mapping")
    known = _known_field_names(dc)
    unknown = set(mapping) - known
    if unknown:
        raise ValueError(
            f"unknown m3_comparison_registry.{what} keys (pre-registration violation): "
            f"{sorted(unknown)}"
        )
    missing = _required_field_names(dc) - set(mapping)
    if missing:
        raise ValueError(
            f"m3_comparison_registry.{what} missing required keys: {sorted(missing)}"
        )
    return dc(**mapping)


class _NoDupSafeLoader(yaml.SafeLoader):
    """重複 mapping キーを拒否する SafeLoader（`scripts/run_melody_observability.py` と同型）。"""


def _no_dup_construct_mapping(loader: "yaml.SafeLoader", node: Any, deep: bool = False) -> Dict[Any, Any]:
    mapping: Dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(
                f"duplicate YAML mapping key {key!r} in m3_comparison_registry.yaml; "
                "last-wins で pre-registration block を隠す穴を弾く (fail-closed)"
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_NoDupSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_dup_construct_mapping
)


def load_m3_registry(path: "str | Path") -> Tuple[M3ComparisonConfig, str]:
    """`m3_comparison_registry.yaml` を読み ``(config, sha256_hex)`` を返す。

    `scripts/run_melody_observability.py` の `_load_registry` と同型: 生バイトを
    一度だけ読み、その bytes から sha256 と YAML parse の双方を導出する
    （single read で registry_sha256 と実際に消費した内容の整合を保証する）。
    """
    data = Path(path).read_bytes()
    try:
        mapping = yaml.load(data, Loader=_NoDupSafeLoader)  # noqa: S506 (dup-key 拒否付き SafeLoader)
    except yaml.YAMLError as exc:
        raise ValueError(f"m3_comparison_registry.yaml: YAML parse error: {exc}") from exc
    config = M3ComparisonConfig.from_registry(mapping)
    return config, hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# 正規化系列
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MelodySequences:
    """ノート列から導出した正規化系列（移調・変速不変な表現）。

    ``intervals_raw`` / ``intervals_folded`` / ``contour`` / ``ioi_log2_ratios`` /
    ``duration_log2_ratios`` はいずれも長さ ``len(pitch_semitones) - 1``
    （隣接ノート対の数）。ノートが 0 または 1 個なら全て空タプル。
    """

    pitch_semitones: Tuple[int, ...]
    intervals_raw: Tuple[int, ...]
    intervals_folded: Tuple[int, ...]
    contour: Tuple[str, ...]
    ioi_log2_ratios: Tuple[Optional[float], ...]
    duration_log2_ratios: Tuple[Optional[float], ...]


def _quantize(value: float, step: float) -> float:
    """`value` を `step` 刻みへ丸める（事前登録の丸め流儀。負値/0 も対称に扱う）。"""
    return round(value / step) * step


def _contour_bin(interval: int, small_max_semitones: int) -> str:
    """半音差 1 個を輪郭ビンへ写像する（下降対称）。"""
    if interval == 0:
        return "flat"
    magnitude = abs(interval)
    if interval > 0:
        return "up_small" if magnitude <= small_max_semitones else "up_large"
    return "down_small" if magnitude <= small_max_semitones else "down_large"


def _fold_interval(interval: int, fold_semitones: int) -> int:
    """半音差をオクターブ折返し（chroma 差）へ写像する: ``((d + half) mod m) - half``。"""
    half = fold_semitones // 2
    return ((interval + half) % fold_semitones) - half


def _round_half_up_semitone(pitch_midi: float) -> int:
    """半音丸め: `floor(pitch_midi + 0.5)`（一側タイブレーク・移調同変）。

    レビュー対応 2026-07-30（第 11 ラウンド）: Python 組み込み `round()` は
    偶数丸め（banker's rounding）——タイブレークの向きが値の偶奇で変わるため、
    整数移調をかけても量子化結果が同量シフトしない（例: `round(60.5)=60` だが
    `round(61.5)=62` で、+1 移調のはずが +2 になる）。`floor(x + 0.5)` は常に
    「.5 は繰り上げ」で片側に倒すため、任意の整数 `n` について
    `floor((x+n) + 0.5) == floor(x + 0.5) + n` が恒等的に成り立ち、移調同変性
    （intervals_raw が移調で不変であるという `MelodySequences` の契約）を
    タイブレーク境界でも保つ。
    """
    return int(math.floor(pitch_midi + 0.5))


def build_sequences(
    notes: Sequence[MelodyNote], config: M3ComparisonConfig
) -> MelodySequences:
    """ノート列 → 正規化系列。半音丸めは `_round_half_up_semitone`（`floor(x+0.5)`）。

    IOI（隣接ノート onset 間隔）と音長比は event-driven（テンポ不変）で計算し、
    IOI<=0 / 音長<=0 の対は防御的に ``None`` を返す。
    """
    pitch_semitones = tuple(_round_half_up_semitone(note.pitch_midi) for note in notes)
    note_count = len(pitch_semitones)

    intervals_raw = tuple(
        pitch_semitones[i + 1] - pitch_semitones[i] for i in range(note_count - 1)
    )
    fold_semitones = config.representation.chroma_fold_semitones
    intervals_folded = tuple(_fold_interval(d, fold_semitones) for d in intervals_raw)
    small_max = config.representation.contour_small_max_semitones
    contour = tuple(_contour_bin(d, small_max) for d in intervals_raw)

    iois = tuple(
        notes[i + 1].start_sec - notes[i].start_sec for i in range(note_count - 1)
    )
    ioi_step = config.representation.ioi_ratio_log2_step
    ioi_ratios: List[Optional[float]] = []
    for i in range(len(iois)):
        if i == 0:
            ioi_ratios.append(None)
            continue
        prev_ioi, cur_ioi = iois[i - 1], iois[i]
        if prev_ioi <= 0.0 or cur_ioi <= 0.0:
            ioi_ratios.append(None)
        else:
            ioi_ratios.append(_quantize(math.log2(cur_ioi / prev_ioi), ioi_step))

    durations = tuple(note.end_sec - note.start_sec for note in notes)
    dur_step = config.representation.duration_ratio_log2_step
    duration_ratios: List[Optional[float]] = []
    for i in range(note_count - 1):
        dur_i, dur_next = durations[i], durations[i + 1]
        if dur_i <= 0.0 or dur_next <= 0.0:
            duration_ratios.append(None)
        else:
            duration_ratios.append(_quantize(math.log2(dur_next / dur_i), dur_step))

    return MelodySequences(
        pitch_semitones=pitch_semitones,
        intervals_raw=intervals_raw,
        intervals_folded=intervals_folded,
        contour=contour,
        ioi_log2_ratios=tuple(ioi_ratios),
        duration_log2_ratios=tuple(duration_ratios),
    )


def split_phrases(
    notes: Sequence[MelodyNote], phrase_gap_sec: float
) -> List[Tuple[MelodyNote, ...]]:
    """ノート間ギャップが ``phrase_gap_sec`` を超えたらフレーズを分割する。

    `observability._phrase_count` と同じ並び替え・ギャップ条件を用いるため、
    ``len(split_phrases(notes, gap)) == _phrase_count(notes, gap)`` が常に成立する
    （呼び出し側で同期テストする）。
    """
    if not notes:
        return []
    ordered = sorted(notes, key=lambda n: n.start_sec)
    phrases: List[List[MelodyNote]] = [[ordered[0]]]
    for prev, current in zip(ordered, ordered[1:]):
        if current.start_sec - prev.end_sec > phrase_gap_sec:
            phrases.append([current])
        else:
            phrases[-1].append(current)
    return [tuple(phrase) for phrase in phrases]


def _normalize_float_for_hash(value: Optional[float]) -> Optional[str]:
    """float を固定表記（小数点 4 桁）へ正規化する（repr の環境依存を避ける）。"""
    if value is None:
        return None
    return format(value, ".4f")


def sequence_sha256(seqs: MelodySequences) -> str:
    """`MelodySequences` の内容 hash（決定論・キー順は `sort_keys=True` で固定）。"""
    payload = {
        "pitch": list(seqs.pitch_semitones),
        "intervals_raw": list(seqs.intervals_raw),
        "intervals_folded": list(seqs.intervals_folded),
        "contour": list(seqs.contour),
        "ioi": [_normalize_float_for_hash(v) for v in seqs.ioi_log2_ratios],
        "dur": [_normalize_float_for_hash(v) for v in seqs.duration_log2_ratios],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
