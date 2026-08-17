"""models.py — VG-E0: schema 4 種（voice-genome/0.1, evaluation-record/0.1,
hack-record/0.1 + Archive 用の内部モデル）。

`DESIGN_VG_E0.md` §1/§5/§7 の凍結仕様。`voice_genesis/foundry/adapter/voice_spec.py`
（未知フィールド拒否・fail-closed 検証）と `voice_genesis/proto1/genome.py`
（宣言値の再計算一致検証 = 改ざん検出）の家風を踏襲する:

- 全ての `*_from_dict()` は未知トップレベルキーを拒否し、必須キー欠落を
  デフォルト補完せず明示的に拒否する。
- `genome_id` は宣言値をそのまま信頼せず、`compute_genome_id()` による
  再計算値と一致することを load 時に必ず検証する（改ざん・破損検出）。
- lineage の「座標との整合性（NOVELTY 例外を除く）」は `genome_from_dict()`
  が `simplex.assign_lineage()`（系統帰属の機械決定の一次ソース、§3.1）の
  再計算値と照合して強制する（Codex 指摘3, 2026-08-17 採用: 従来は
  build_genome() 経由の書込み側だけがこれを保証し、ローダーは宣言値を
  そのまま信頼していた）。simplex.py が Coords 型のため models.py を
  モジュールレベルで import する片方向依存を保ったまま、models.py 側は
  genome_from_dict() 内のデファード import で import 時循環を回避する
  （モジュールレベルでは依然として simplex.py に依存しない）。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, FrozenSet, Mapping, Optional, Sequence, Tuple

# sibling import（simplex.py 等が踏襲する流儀。§ 下記 genome_from_dict() の
# デファード `import simplex` のために、models.py 自身の読み込み時点で
# 確実に _THIS_DIR を sys.path へ通しておく — 呼び出し元が既に通している
# ケースが大半だが、models.py を直接 `import models` する経路（テスト等）
# でも成立させるため自前で保証する）。
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

# ---------------------------------------------------------------------------
# 共通定数
# ---------------------------------------------------------------------------

SCHEMA_GENOME = "voice-genome/0.1"
SCHEMA_EVALUATION = "evaluation-record/0.1"
SCHEMA_HACK = "hack-record/0.1"

ANCHOR_NAMES: Tuple[str, str, str] = ("ritsu", "pjs", "user")

VALID_LINEAGES: Tuple[str, ...] = ("L-R", "L-P", "L-U", "L-C", "NOVELTY")
VALID_OPERATORS: Tuple[str, ...] = (
    "founder", "drift", "vertex_pull", "reseed", "edge_walk", "novelty_jump",
)

# DESIGN_VG_E0.md §4: オペレータ毎の親個体数の不変条件（proto1/registry.py
# `EXPECTED_PARENT_COUNT` と同型のファミリー規律）。
EXPECTED_PARENT_COUNT: Dict[str, int] = {
    "founder": 0,
    "drift": 1,
    "vertex_pull": 2,
    "reseed": 1,
    "edge_walk": 1,
    "novelty_jump": 1,
}

# DESIGN_VG_E0.md §4 の表: step/pull の凍結上限。models.py が single source of
# truth を持ち、operators.py（構築時）・本モジュールの from_dict（読込時）の
# 双方がここを参照する（proto1/registry.py R28「書読対称性」と同じ動機）。
DRIFT_STEP_MAX = 0.08
VERTEX_PULL_PULL_MAX = 0.2
EDGE_WALK_STEP_MAX = 0.1

# novelty_jump の最小跳躍距離（凍結値。DESIGN_VG_E0.md §4）。Δ² 上の一様
# サンプル単独では親の近傍に着地しうる（実測: centroid 親 + rng_seed=2031 で
# L1=0.00512 の「novelty」が生成された — Codex 指摘 6, 採用 2026-08-17）ため、
# operators.novelty_jump() は同一 rng ストリームからの決定論的棄却サンプリング
# でこの下限を保証する。
NOVELTY_JUMP_MIN_L1 = 0.4

_GENOME_ID_LEN = 16
_GENOME_ID_RE = re.compile(rf"^[0-9a-f]{{{_GENOME_ID_LEN}}}$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

EVALUATOR_KINDS: Tuple[str, str, str] = ("training", "hidden", "human")
VERDICTS: Tuple[str, str, str] = ("pass", "fail", "hold")
HACK_DISPOSITIONS: Tuple[str, str] = ("retained", "superseded")


class GenomeValidationError(ValueError):
    """VoiceGenome / EvaluationRecord / HackRecord の構築・デシリアライズ時の
    型・構造不正、または genome_id の再計算不一致（改ざん・破損）。"""


# ---------------------------------------------------------------------------
# Coords（重心座標。値そのものは §2 の simplex.normalize() が生成する —
# 本モジュールは型と「既に正規形か」の検証のみを持つ）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Coords:
    ritsu: float
    pjs: float
    user: float

    def as_dict(self) -> Dict[str, float]:
        return {"ritsu": self.ritsu, "pjs": self.pjs, "user": self.user}


def _validate_coords_value(coords: Coords) -> None:
    """coords が Δ²（3頂点 ritsu/pjs/user、成分非負・合計1）上の正規形
    （小数6桁丸め済み）であることを検証する（DESIGN_VG_E0.md §2）。
    `simplex.normalize()` の出力契約そのものを検証するが、循環 import を
    避けるため normalize() を呼ばずここで直接検証する。
    """
    total = 0.0
    for name in ANCHOR_NAMES:
        v = getattr(coords, name)
        if not isinstance(v, float) or isinstance(v, bool):
            raise GenomeValidationError(f"coords.{name} must be a float, got {v!r}")
        if not math.isfinite(v):
            raise GenomeValidationError(f"coords.{name} must be finite (NaN/inf rejected): {v!r}")
        if v < 0.0:
            raise GenomeValidationError(f"coords.{name} must be >= 0 (barycentric constraint): {v!r}")
        if round(v, 6) != v:
            raise GenomeValidationError(
                f"coords.{name} must already be rounded to 6 decimal places (normalize() output "
                f"contract), got {v!r}"
            )
        total += v
    if abs(total - 1.0) > 1e-9:
        raise GenomeValidationError(f"coords must sum to 1.000000 (barycentric constraint), got {total!r}")


def _coords_from_dict(data: Any) -> Coords:
    if not isinstance(data, dict):
        raise GenomeValidationError(f"coords must be an object, got {type(data).__name__}")
    unknown = set(data.keys()) - set(ANCHOR_NAMES)
    if unknown:
        raise GenomeValidationError(f"coords has unknown key(s): {sorted(unknown)}")
    missing = set(ANCHOR_NAMES) - set(data.keys())
    if missing:
        raise GenomeValidationError(f"coords missing required key(s): {sorted(missing)}")
    values: Dict[str, float] = {}
    for name in ANCHOR_NAMES:
        raw = data[name]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise GenomeValidationError(f"coords.{name} must be a number, got {raw!r}")
        values[name] = float(raw)
    coords = Coords(**values)
    _validate_coords_value(coords)
    return coords


# ---------------------------------------------------------------------------
# genome_id: 正規形 JSON（キー昇順・小数6桁固定表記）の sha256 先頭16 hex
# （DESIGN_VG_E0.md §1）。coords/seed/lineage/generation/parents/operator/
# operator_params の6フィールドのみが対象（genome_id 自身は自己言及を避ける
# ため除外。schema/anchors_provenance/notes も対象外 — §1「genome_id は
# ...の正規形JSONから導出」に明記された6フィールドのみ）。
# ---------------------------------------------------------------------------


def _canonicalize_for_hash(obj: Any) -> Any:
    """genome_id 計算用の正規化: float は小数6桁固定表記の文字列へ変換する
    （§2「丸め規約」の「小数6桁固定表記」を genome_id 計算の場でも徹底する —
    Python の json モジュールは float を可変桁の repr で出力するため、これを
    経由すると 0.500000 と 0.5 が異なるバイト列になり得る。文字列化することで
    表記を固定する）。NaN/inf は即例外。bool は int のサブクラスのため int
    より先に判定する。
    """
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise GenomeValidationError(f"non-finite value rejected in genome_id payload: {obj!r}")
        return format(round(obj, 6), ".6f")
    if isinstance(obj, str):
        return obj
    if obj is None:
        return None
    if isinstance(obj, (list, tuple)):
        return [_canonicalize_for_hash(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): _canonicalize_for_hash(v) for k, v in obj.items()}
    raise GenomeValidationError(f"unsupported type in genome_id payload: {type(obj).__name__}")


def _canonical_json_for_hash(obj: Any) -> str:
    canonical = _canonicalize_for_hash(obj)
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def compute_genome_id(
    *,
    coords: Coords,
    seed: int,
    lineage: str,
    generation: int,
    parents: Sequence[str],
    operator: str,
    operator_params: Mapping[str, Any],
) -> str:
    payload = {
        "coords": coords.as_dict(),
        "seed": seed,
        "lineage": lineage,
        "generation": generation,
        "parents": list(parents),
        "operator": operator,
        "operator_params": dict(operator_params),
    }
    canonical = _canonical_json_for_hash(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:_GENOME_ID_LEN]


def _validate_genome_id_format(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise GenomeValidationError(f"{field} must be a string, got {value!r}")
    if not _GENOME_ID_RE.match(value):
        raise GenomeValidationError(
            f"{field} must be exactly {_GENOME_ID_LEN} lowercase hex characters, got {value!r}"
        )
    return value


# ---------------------------------------------------------------------------
# operator_params: オペレータ毎の閉じた語彙 + 数値上限（DESIGN_VG_E0.md §4）
# ---------------------------------------------------------------------------


def _require_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise GenomeValidationError(f"{field} must be an int, got {value!r}")
    return value


def _require_finite_float(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GenomeValidationError(f"{field} must be a number, got {value!r}")
    out = float(value)
    if not math.isfinite(out):
        raise GenomeValidationError(f"{field} must be finite (NaN/inf rejected): {value!r}")
    return out


def _require_bounded_float(value: Any, field: str, *, lo: float, hi: float, normalize: bool) -> float:
    """`normalize=True`（構築経路・build_genome 経由）は座標と同じ小数6桁
    固定丸めへ正規化してから返す（Codex 指摘B: 丸めていない生値が
    operator_params にそのまま格納・serialize されると、genome_id ハッシュ
    計算は既に6桁丸め後の値で正規化される一方（`_canonicalize_for_hash`）、
    格納ペイロード自体は生値のままだったため `weight=0.5` と
    `weight=0.5000001` が同一 genome_id・異なるペイロードになっていた）。
    丸めは上限値検査より先に行う（丸め後値で境界判定する — 丸めで初めて
    境界に一致する値を一貫して受理するため、丸め前チェックとの矛盾を防ぐ）。

    `normalize=False`（デシリアライズ経路・genome_from_dict 経由）は
    coords の `_validate_coords_value` と対称に、既に6桁丸め済みでない
    float を fail-closed で拒否する（正規化されていないペイロードが
    そのまま台帳へ紛れ込むのを防ぐ）。
    """
    out = _require_finite_float(value, field)
    if normalize:
        out = round(out, 6)
    elif round(out, 6) != out:
        raise GenomeValidationError(
            f"{field} must already be rounded to 6 decimal places (operator_params normalization "
            f"contract — DESIGN_VG_E0.md §4), got {value!r}"
        )
    if not (lo <= out <= hi):
        raise GenomeValidationError(f"{field} must be within [{lo}, {hi}], got {out!r}")
    return out


def _require_anchor_name(value: Any, field: str) -> str:
    if not isinstance(value, str) or value not in ANCHOR_NAMES:
        raise GenomeValidationError(f"{field} must be one of {ANCHOR_NAMES}, got {value!r}")
    return value


def _validate_operator_params(operator: str, params: Mapping[str, Any], *, normalize: bool) -> Dict[str, Any]:
    """operator ごとの閉じた operator_params 語彙・型・数値上限を検証し、
    正規形（tuple は list へ）の dict を返す。build_genome()（書込経路）と
    genome_from_dict()（読込経路）の両方から呼ばれる単一実装
    （proto1/registry.py R28「書読対称性」と同じ設計判断）。

    `normalize` は float（weight/pull/step 等）の6桁丸め挙動を書込/読込
    経路で非対称にする（Codex 指摘B）: 書込側（`normalize=True`）は生値を
    座標と同じ規約へ丸めて格納し、読込側（`normalize=False`）は既に
    丸め済みでない値を fail-closed で拒否する。int/str/bool の各フィールド
    （rng_seed/new_seed/vertex/edge）は丸め対象外で不変。
    """
    if not isinstance(params, Mapping):
        raise GenomeValidationError(f"operator_params must be an object, got {type(params).__name__}")

    if operator == "founder":
        if dict(params) != {}:
            raise GenomeValidationError(f"operator=founder requires empty operator_params, got {dict(params)!r}")
        return {}

    if operator == "drift":
        allowed = {"rng_seed", "step"}
        unknown = set(params.keys()) - allowed
        if unknown:
            raise GenomeValidationError(f"operator=drift has unknown operator_params key(s): {sorted(unknown)}")
        missing = allowed - set(params.keys())
        if missing:
            raise GenomeValidationError(f"operator=drift missing operator_params key(s): {sorted(missing)}")
        return {
            "rng_seed": _require_int(params["rng_seed"], "operator_params.rng_seed"),
            "step": _require_bounded_float(
                params["step"], "operator_params.step", lo=0.0, hi=DRIFT_STEP_MAX, normalize=normalize
            ),
        }

    if operator == "vertex_pull":
        allowed = {"weight", "vertex", "pull"}
        unknown = set(params.keys()) - allowed
        if unknown:
            raise GenomeValidationError(f"operator=vertex_pull has unknown operator_params key(s): {sorted(unknown)}")
        missing = allowed - set(params.keys())
        if missing:
            raise GenomeValidationError(f"operator=vertex_pull missing operator_params key(s): {sorted(missing)}")
        return {
            "weight": _require_bounded_float(
                params["weight"], "operator_params.weight", lo=0.0, hi=1.0, normalize=normalize
            ),
            "vertex": _require_anchor_name(params["vertex"], "operator_params.vertex"),
            "pull": _require_bounded_float(
                params["pull"], "operator_params.pull", lo=0.0, hi=VERTEX_PULL_PULL_MAX, normalize=normalize
            ),
        }

    if operator == "reseed":
        allowed = {"new_seed"}
        unknown = set(params.keys()) - allowed
        if unknown:
            raise GenomeValidationError(f"operator=reseed has unknown operator_params key(s): {sorted(unknown)}")
        missing = allowed - set(params.keys())
        if missing:
            raise GenomeValidationError(f"operator=reseed missing operator_params key(s): {sorted(missing)}")
        return {"new_seed": _require_int(params["new_seed"], "operator_params.new_seed")}

    if operator == "edge_walk":
        allowed = {"rng_seed", "edge", "step"}
        unknown = set(params.keys()) - allowed
        if unknown:
            raise GenomeValidationError(f"operator=edge_walk has unknown operator_params key(s): {sorted(unknown)}")
        missing = allowed - set(params.keys())
        if missing:
            raise GenomeValidationError(f"operator=edge_walk missing operator_params key(s): {sorted(missing)}")
        edge_raw = params["edge"]
        if not isinstance(edge_raw, (list, tuple)) or len(edge_raw) != 2:
            raise GenomeValidationError(
                f"operator_params.edge must be a 2-element list, got {edge_raw!r}"
            )
        a = _require_anchor_name(edge_raw[0], "operator_params.edge[0]")
        b = _require_anchor_name(edge_raw[1], "operator_params.edge[1]")
        if a == b:
            raise GenomeValidationError(f"operator_params.edge must name two distinct anchors, got {edge_raw!r}")
        return {
            "rng_seed": _require_int(params["rng_seed"], "operator_params.rng_seed"),
            "edge": [a, b],
            "step": _require_bounded_float(
                params["step"], "operator_params.step", lo=0.0, hi=EDGE_WALK_STEP_MAX, normalize=normalize
            ),
        }

    if operator == "novelty_jump":
        allowed = {"rng_seed"}
        unknown = set(params.keys()) - allowed
        if unknown:
            raise GenomeValidationError(f"operator=novelty_jump has unknown operator_params key(s): {sorted(unknown)}")
        missing = allowed - set(params.keys())
        if missing:
            raise GenomeValidationError(f"operator=novelty_jump missing operator_params key(s): {sorted(missing)}")
        return {"rng_seed": _require_int(params["rng_seed"], "operator_params.rng_seed")}

    raise GenomeValidationError(f"unknown operator: {operator!r}")  # pragma: no cover - defensive


# ---------------------------------------------------------------------------
# anchors_provenance（DESIGN_VG_E0.md §1/§6: run4 前は null、以後必須化）
# ---------------------------------------------------------------------------


def _validate_anchors_provenance(data: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, Mapping):
        raise GenomeValidationError(f"anchors_provenance must be an object or null, got {type(data).__name__}")
    allowed = {"checkpoint_sha256", "embed_sha256"}
    unknown = set(data.keys()) - allowed
    if unknown:
        raise GenomeValidationError(f"anchors_provenance has unknown key(s): {sorted(unknown)}")
    missing = allowed - set(data.keys())
    if missing:
        raise GenomeValidationError(f"anchors_provenance missing required key(s): {sorted(missing)}")
    # sha256 は「正確に64文字の小文字16進」を構文要求する（Codex 指摘4:
    # 従来は非空文字列であれば何でも通っていたため、hex でない文字列や桁数
    # 違いの値が fail-closed の外側にすり抜けていた）。
    checkpoint_sha256 = data["checkpoint_sha256"]
    if not isinstance(checkpoint_sha256, str) or not _SHA256_HEX_RE.match(checkpoint_sha256):
        raise GenomeValidationError(
            "anchors_provenance.checkpoint_sha256 must be exactly 64 lowercase hex characters, "
            f"got {checkpoint_sha256!r}"
        )
    embed_sha256 = data["embed_sha256"]
    if not isinstance(embed_sha256, Mapping):
        raise GenomeValidationError(f"anchors_provenance.embed_sha256 must be an object, got {type(embed_sha256).__name__}")
    unknown_anchor = set(embed_sha256.keys()) - set(ANCHOR_NAMES)
    if unknown_anchor:
        raise GenomeValidationError(f"anchors_provenance.embed_sha256 has unknown key(s): {sorted(unknown_anchor)}")
    missing_anchor = set(ANCHOR_NAMES) - set(embed_sha256.keys())
    if missing_anchor:
        raise GenomeValidationError(f"anchors_provenance.embed_sha256 missing key(s): {sorted(missing_anchor)}")
    out_embed: Dict[str, str] = {}
    for name in ANCHOR_NAMES:
        v = embed_sha256[name]
        if not isinstance(v, str) or not _SHA256_HEX_RE.match(v):
            raise GenomeValidationError(
                f"anchors_provenance.embed_sha256.{name} must be exactly 64 lowercase hex characters, "
                f"got {v!r}"
            )
        out_embed[name] = v
    return {"checkpoint_sha256": checkpoint_sha256, "embed_sha256": out_embed}


# ---------------------------------------------------------------------------
# VoiceGenome
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VoiceGenome:
    schema: str
    genome_id: str
    coords: Coords
    seed: int
    lineage: str
    generation: int
    parents: Tuple[str, ...]
    operator: str
    operator_params: Dict[str, Any]
    anchors_provenance: Optional[Dict[str, Any]]
    notes: str


def build_genome(
    *,
    coords: Coords,
    seed: int,
    lineage: str,
    generation: int,
    parents: Sequence[str],
    operator: str,
    operator_params: Mapping[str, Any],
    anchors_provenance: Optional[Mapping[str, Any]] = None,
    notes: str = "",
) -> VoiceGenome:
    """VoiceGenome を構築する唯一の経路。genome_id はここで
    `compute_genome_id()` から導出する（呼び出し元が genome_id を直接指定する
    経路は存在しない — 内容アドレスの捏造を構造的に防ぐ）。
    """
    if operator not in VALID_OPERATORS:
        raise GenomeValidationError(f"operator must be one of {VALID_OPERATORS}, got {operator!r}")
    if lineage not in VALID_LINEAGES:
        raise GenomeValidationError(f"lineage must be one of {VALID_LINEAGES}, got {lineage!r}")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise GenomeValidationError(f"seed must be an int, got {seed!r}")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise GenomeValidationError(f"generation must be a non-negative int, got {generation!r}")

    parents_tuple = tuple(parents)
    expected_n = EXPECTED_PARENT_COUNT[operator]
    if len(parents_tuple) != expected_n:
        raise GenomeValidationError(
            f"operator={operator!r} requires exactly {expected_n} parent(s), got {len(parents_tuple)}"
        )
    for p in parents_tuple:
        _validate_genome_id_format(p, "parents[]")

    _validate_coords_value(coords)
    # normalize=True: build_genome() は operator_params の float を6桁丸めへ
    # 正規化してから格納する（Codex 指摘B）。
    validated_params = _validate_operator_params(operator, operator_params, normalize=True)
    validated_anchors = (
        _validate_anchors_provenance(anchors_provenance) if anchors_provenance is not None else None
    )
    if not isinstance(notes, str):
        raise GenomeValidationError(f"notes must be a string, got {notes!r}")

    genome_id = compute_genome_id(
        coords=coords, seed=seed, lineage=lineage, generation=generation,
        parents=parents_tuple, operator=operator, operator_params=validated_params,
    )
    return VoiceGenome(
        schema=SCHEMA_GENOME, genome_id=genome_id, coords=coords, seed=seed, lineage=lineage,
        generation=generation, parents=parents_tuple, operator=operator,
        operator_params=validated_params, anchors_provenance=validated_anchors, notes=notes,
    )


_GENOME_TOP_LEVEL_KEYS: FrozenSet[str] = frozenset({
    "schema", "genome_id", "coords", "seed", "lineage", "generation", "parents",
    "operator", "operator_params", "anchors_provenance", "notes",
})


def genome_to_dict(genome: VoiceGenome) -> Dict[str, Any]:
    return {
        "schema": genome.schema,
        "genome_id": genome.genome_id,
        "coords": genome.coords.as_dict(),
        "seed": genome.seed,
        "lineage": genome.lineage,
        "generation": genome.generation,
        "parents": list(genome.parents),
        "operator": genome.operator,
        "operator_params": dict(genome.operator_params),
        "anchors_provenance": (
            dict(genome.anchors_provenance) if genome.anchors_provenance is not None else None
        ),
        "notes": genome.notes,
    }


def genome_to_json(genome: VoiceGenome, *, indent: Optional[int] = 2) -> str:
    return json.dumps(genome_to_dict(genome), indent=indent, sort_keys=True, ensure_ascii=False)


def genome_from_dict(data: Any) -> VoiceGenome:
    """JSON dict から VoiceGenome を再構築する。fail-closed（未知キー拒否・
    欠落キーのデフォルト補完なし）+ genome_id 再計算一致検証（改ざん検出）。
    """
    if not isinstance(data, dict):
        raise GenomeValidationError(f"genome document must be an object, got {type(data).__name__}")
    unknown = set(data.keys()) - _GENOME_TOP_LEVEL_KEYS
    if unknown:
        raise GenomeValidationError(f"genome document has unknown key(s): {sorted(unknown)}")
    missing = _GENOME_TOP_LEVEL_KEYS - set(data.keys())
    if missing:
        raise GenomeValidationError(f"genome document missing required key(s): {sorted(missing)}")

    schema = data["schema"]
    if schema != SCHEMA_GENOME:
        raise GenomeValidationError(f"schema must be {SCHEMA_GENOME!r}, got {schema!r}")

    coords = _coords_from_dict(data["coords"])

    seed = data["seed"]
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise GenomeValidationError(f"seed must be an int, got {seed!r}")

    lineage = data["lineage"]
    if lineage not in VALID_LINEAGES:
        raise GenomeValidationError(f"lineage must be one of {VALID_LINEAGES}, got {lineage!r}")
    if lineage != "NOVELTY":
        # lineage は座標のみからの機械決定（NOVELTY 隔離を除く。
        # DESIGN_VG_E0.md §3.1「機械決定・手書き上書き禁止」）。build_genome()
        # 経由の書込み側（operators.py）は常に simplex.assign_lineage() で
        # 正しい値を計算するが、genome_from_dict() は従来これを検証せず
        # 宣言値をそのまま信頼していた（Codex 指摘3）。デファード import は
        # models.py↔simplex.py の import 時循環を避けるため（simplex.py は
        # models.Coords 型のため models をモジュールレベルで import する。
        # ここでの遅延 import は呼び出し時点に両モジュールが完全にロード
        # 済みであることを利用して循環を切る）。
        import simplex  # noqa: E402

        expected_lineage = simplex.assign_lineage(coords)
        if lineage != expected_lineage:
            raise GenomeValidationError(
                f"lineage {lineage!r} does not match coords-derived lineage {expected_lineage!r} "
                "(lineage is machine-derived from coords except for NOVELTY-origin genomes — "
                "DESIGN_VG_E0.md §3.1; hand-edited lineage is rejected)"
            )

    generation = data["generation"]
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise GenomeValidationError(f"generation must be a non-negative int, got {generation!r}")

    parents_raw = data["parents"]
    if not isinstance(parents_raw, list):
        raise GenomeValidationError(f"parents must be a list, got {type(parents_raw).__name__}")
    parents = tuple(_validate_genome_id_format(p, "parents[]") for p in parents_raw)

    operator = data["operator"]
    if operator not in VALID_OPERATORS:
        raise GenomeValidationError(f"operator must be one of {VALID_OPERATORS}, got {operator!r}")
    expected_n = EXPECTED_PARENT_COUNT[operator]
    if len(parents) != expected_n:
        raise GenomeValidationError(
            f"operator={operator!r} requires exactly {expected_n} parent(s), got {len(parents)}"
        )

    # Codex 指摘C: lineage=NOVELTY は operators.py 上、novelty_jump の全出力と
    # 系統間（親 lineage 不一致）vertex_pull の出力に限定される（§3.1/§4
    # 「系統内/系統間判定はオペレータではなく台帳が行う」— 本実装ではこの
    # 判定点をオペレータ自身が担う設計のため、founder/drift/reseed/edge_walk
    # は座標由来 lineage しか生成し得ない）。ローダーは従来これを検証せず、
    # NOVELTY が任意 operator と組み合わせて宣言可能だった。
    #
    # vertex_pull については「両親の lineage が実際に異なっていたか」は
    # このドキュメント単体（genome 1件の宣言値）からは検証不能 — 台帳上の
    # 親個体の実体参照が必要なため、ここでは operator が vertex_pull で
    # あることのみを許容条件とする。両親 lineage の実差分検証は台帳
    # （ledger.py）側の責務として VG-E1 送り（今回はスコープ外）。
    if lineage == "NOVELTY" and operator not in ("novelty_jump", "vertex_pull"):
        raise GenomeValidationError(
            f"lineage='NOVELTY' requires operator in ('novelty_jump', 'vertex_pull'), got operator={operator!r} "
            "(NOVELTY isolation is limited to novelty_jump's own output and cross-lineage vertex_pull "
            "crossings — DESIGN_VG_E0.md §3.1/§4; founder/drift/reseed/edge_walk cannot declare NOVELTY)"
        )
    # 逆方向: novelty_jump の出力は常に NOVELTY 隔離される（operators.py
    # `novelty_jump()` 参照）。operator=novelty_jump で座標由来 lineage を
    # 宣言したドキュメントは改ざん・破損として拒否する。
    if operator == "novelty_jump" and lineage != "NOVELTY":
        raise GenomeValidationError(
            f"operator='novelty_jump' requires lineage='NOVELTY', got lineage={lineage!r} "
            "(novelty_jump output is always NOVELTY-isolated by construction — DESIGN_VG_E0.md §4)"
        )

    operator_params_raw = data["operator_params"]
    # normalize=False: genome_from_dict() は既に6桁丸め済みでない float を
    # fail-closed で拒否する（Codex 指摘B。build_genome() 側の normalize=True
    # と対称）。
    operator_params = _validate_operator_params(operator, operator_params_raw, normalize=False)

    anchors_raw = data["anchors_provenance"]
    if anchors_raw is not None and not isinstance(anchors_raw, dict):
        raise GenomeValidationError(f"anchors_provenance must be an object or null, got {type(anchors_raw).__name__}")
    anchors = _validate_anchors_provenance(anchors_raw) if anchors_raw is not None else None

    notes = data["notes"]
    if not isinstance(notes, str):
        raise GenomeValidationError(f"notes must be a string, got {notes!r}")

    declared_id = _validate_genome_id_format(data["genome_id"], "genome_id")
    recomputed_id = compute_genome_id(
        coords=coords, seed=seed, lineage=lineage, generation=generation,
        parents=parents, operator=operator, operator_params=operator_params,
    )
    if declared_id != recomputed_id:
        raise GenomeValidationError(
            f"genome_id mismatch: declared={declared_id!r} recomputed={recomputed_id!r} "
            "(content-addressed id does not match the 6 hashed fields — tampering or corruption)"
        )

    return VoiceGenome(
        schema=schema, genome_id=declared_id, coords=coords, seed=seed, lineage=lineage,
        generation=generation, parents=parents, operator=operator, operator_params=operator_params,
        anchors_provenance=anchors, notes=notes,
    )


def genome_from_json(text: str) -> VoiceGenome:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GenomeValidationError(f"invalid JSON: {exc}") from exc
    return genome_from_dict(data)


# ---------------------------------------------------------------------------
# EvaluationRecord（DESIGN_VG_E0.md §5。値は VG-E1、フィールドのみ本書で凍結）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Evaluator:
    kind: str
    version: str


@dataclass(frozen=True)
class EvaluationRecord:
    schema: str
    genome_id: str
    probe_set: str
    evaluator: Evaluator
    axes: Dict[str, float]
    blind_batch: Optional[str]
    verdict: Optional[str]


_EVAL_TOP_LEVEL_KEYS: FrozenSet[str] = frozenset({
    "schema", "genome_id", "probe_set", "evaluator", "axes", "blind_batch", "verdict",
})
_EVALUATOR_KEYS: FrozenSet[str] = frozenset({"kind", "version"})


def build_evaluation_record(
    *,
    genome_id: str,
    probe_set: str,
    evaluator: Evaluator,
    axes: Mapping[str, float],
    blind_batch: Optional[str] = None,
    verdict: Optional[str] = None,
) -> EvaluationRecord:
    _validate_genome_id_format(genome_id, "genome_id")
    if not isinstance(probe_set, str) or not probe_set:
        raise GenomeValidationError(f"probe_set must be a non-empty string, got {probe_set!r}")
    if evaluator.kind not in EVALUATOR_KINDS:
        raise GenomeValidationError(f"evaluator.kind must be one of {EVALUATOR_KINDS}, got {evaluator.kind!r}")
    if not isinstance(evaluator.version, str) or not evaluator.version:
        raise GenomeValidationError(f"evaluator.version must be a non-empty string, got {evaluator.version!r}")
    validated_axes: Dict[str, float] = {}
    for name, value in axes.items():
        if not isinstance(name, str) or not name:
            raise GenomeValidationError(f"axes key must be a non-empty string, got {name!r}")
        validated_axes[name] = _require_finite_float(value, f"axes.{name}")
    if blind_batch is not None and not isinstance(blind_batch, str):
        raise GenomeValidationError(f"blind_batch must be a string or null, got {blind_batch!r}")
    # Codex 指摘A: blind_batch は human 評価者専用のフィールド（training/hidden
    # は機械評価者のため blind_batch という概念自体が成立しない）。kind が
    # human 以外なら null 必須、human なら与える場合は非空文字列を要求する。
    if evaluator.kind != "human":
        if blind_batch is not None:
            raise GenomeValidationError(
                f"blind_batch must be null when evaluator.kind={evaluator.kind!r} "
                "(blind_batch is reserved for evaluator.kind='human')"
            )
    elif blind_batch is not None and blind_batch == "":
        raise GenomeValidationError(
            "blind_batch must be a non-empty string when evaluator.kind='human' (empty string rejected), "
            f"got {blind_batch!r}"
        )
    if verdict is not None and verdict not in VERDICTS:
        raise GenomeValidationError(f"verdict must be one of {VERDICTS} or null, got {verdict!r}")
    return EvaluationRecord(
        schema=SCHEMA_EVALUATION, genome_id=genome_id, probe_set=probe_set, evaluator=evaluator,
        axes=validated_axes, blind_batch=blind_batch, verdict=verdict,
    )


def evaluation_record_to_dict(record: EvaluationRecord) -> Dict[str, Any]:
    return {
        "schema": record.schema,
        "genome_id": record.genome_id,
        "probe_set": record.probe_set,
        "evaluator": {"kind": record.evaluator.kind, "version": record.evaluator.version},
        "axes": dict(record.axes),
        "blind_batch": record.blind_batch,
        "verdict": record.verdict,
    }


def evaluation_record_from_dict(data: Any) -> EvaluationRecord:
    if not isinstance(data, dict):
        raise GenomeValidationError(f"evaluation record must be an object, got {type(data).__name__}")
    unknown = set(data.keys()) - _EVAL_TOP_LEVEL_KEYS
    if unknown:
        raise GenomeValidationError(f"evaluation record has unknown key(s): {sorted(unknown)}")
    missing = _EVAL_TOP_LEVEL_KEYS - set(data.keys())
    if missing:
        raise GenomeValidationError(f"evaluation record missing required key(s): {sorted(missing)}")

    schema = data["schema"]
    if schema != SCHEMA_EVALUATION:
        raise GenomeValidationError(f"schema must be {SCHEMA_EVALUATION!r}, got {schema!r}")

    genome_id = _validate_genome_id_format(data["genome_id"], "genome_id")

    probe_set = data["probe_set"]
    if not isinstance(probe_set, str) or not probe_set:
        raise GenomeValidationError(f"probe_set must be a non-empty string, got {probe_set!r}")

    evaluator_raw = data["evaluator"]
    if not isinstance(evaluator_raw, dict):
        raise GenomeValidationError(f"evaluator must be an object, got {type(evaluator_raw).__name__}")
    unknown_ev = set(evaluator_raw.keys()) - _EVALUATOR_KEYS
    if unknown_ev:
        raise GenomeValidationError(f"evaluator has unknown key(s): {sorted(unknown_ev)}")
    missing_ev = _EVALUATOR_KEYS - set(evaluator_raw.keys())
    if missing_ev:
        raise GenomeValidationError(f"evaluator missing required key(s): {sorted(missing_ev)}")
    kind = evaluator_raw["kind"]
    if kind not in EVALUATOR_KINDS:
        raise GenomeValidationError(f"evaluator.kind must be one of {EVALUATOR_KINDS}, got {kind!r}")
    version = evaluator_raw["version"]
    if not isinstance(version, str) or not version:
        raise GenomeValidationError(f"evaluator.version must be a non-empty string, got {version!r}")
    evaluator = Evaluator(kind=kind, version=version)

    axes_raw = data["axes"]
    if not isinstance(axes_raw, dict):
        raise GenomeValidationError(f"axes must be an object, got {type(axes_raw).__name__}")
    axes: Dict[str, float] = {
        name: _require_finite_float(value, f"axes.{name}") for name, value in axes_raw.items()
    }

    blind_batch = data["blind_batch"]
    if blind_batch is not None and not isinstance(blind_batch, str):
        raise GenomeValidationError(f"blind_batch must be a string or null, got {blind_batch!r}")
    # Codex 指摘A（build_evaluation_record と対称。デシリアライズ側でも同じ
    # fail-closed 拘束を強制しないと、宣言値をそのまま信頼する経路が抜け道
    # になる）。
    if kind != "human":
        if blind_batch is not None:
            raise GenomeValidationError(
                f"blind_batch must be null when evaluator.kind={kind!r} "
                "(blind_batch is reserved for evaluator.kind='human')"
            )
    elif blind_batch is not None and blind_batch == "":
        raise GenomeValidationError(
            "blind_batch must be a non-empty string when evaluator.kind='human' (empty string rejected), "
            f"got {blind_batch!r}"
        )

    verdict = data["verdict"]
    if verdict is not None and verdict not in VERDICTS:
        raise GenomeValidationError(f"verdict must be one of {VERDICTS} or null, got {verdict!r}")

    return EvaluationRecord(
        schema=schema, genome_id=genome_id, probe_set=probe_set, evaluator=evaluator,
        axes=axes, blind_batch=blind_batch, verdict=verdict,
    )


# ---------------------------------------------------------------------------
# HackRecord（DESIGN_VG_E0.md §5）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HackRecord:
    schema: str
    genome_id: str
    symptom: str
    evaluator_version: str
    discovered_by: str
    disposition: str


_HACK_TOP_LEVEL_KEYS: FrozenSet[str] = frozenset({
    "schema", "genome_id", "symptom", "evaluator_version", "discovered_by", "disposition",
})


def build_hack_record(
    *,
    genome_id: str,
    symptom: str,
    evaluator_version: str,
    discovered_by: str,
    disposition: str = "retained",
) -> HackRecord:
    _validate_genome_id_format(genome_id, "genome_id")
    if not isinstance(symptom, str) or not symptom:
        raise GenomeValidationError(f"symptom must be a non-empty string, got {symptom!r}")
    if not isinstance(evaluator_version, str) or not evaluator_version:
        raise GenomeValidationError(f"evaluator_version must be a non-empty string, got {evaluator_version!r}")
    if not isinstance(discovered_by, str) or not discovered_by:
        raise GenomeValidationError(f"discovered_by must be a non-empty string, got {discovered_by!r}")
    if disposition not in HACK_DISPOSITIONS:
        raise GenomeValidationError(f"disposition must be one of {HACK_DISPOSITIONS}, got {disposition!r}")
    return HackRecord(
        schema=SCHEMA_HACK, genome_id=genome_id, symptom=symptom, evaluator_version=evaluator_version,
        discovered_by=discovered_by, disposition=disposition,
    )


def hack_record_to_dict(record: HackRecord) -> Dict[str, Any]:
    return {
        "schema": record.schema,
        "genome_id": record.genome_id,
        "symptom": record.symptom,
        "evaluator_version": record.evaluator_version,
        "discovered_by": record.discovered_by,
        "disposition": record.disposition,
    }


def hack_record_from_dict(data: Any) -> HackRecord:
    if not isinstance(data, dict):
        raise GenomeValidationError(f"hack record must be an object, got {type(data).__name__}")
    unknown = set(data.keys()) - _HACK_TOP_LEVEL_KEYS
    if unknown:
        raise GenomeValidationError(f"hack record has unknown key(s): {sorted(unknown)}")
    missing = _HACK_TOP_LEVEL_KEYS - set(data.keys())
    if missing:
        raise GenomeValidationError(f"hack record missing required key(s): {sorted(missing)}")

    schema = data["schema"]
    if schema != SCHEMA_HACK:
        raise GenomeValidationError(f"schema must be {SCHEMA_HACK!r}, got {schema!r}")

    genome_id = _validate_genome_id_format(data["genome_id"], "genome_id")

    symptom = data["symptom"]
    if not isinstance(symptom, str) or not symptom:
        raise GenomeValidationError(f"symptom must be a non-empty string, got {symptom!r}")

    evaluator_version = data["evaluator_version"]
    if not isinstance(evaluator_version, str) or not evaluator_version:
        raise GenomeValidationError(f"evaluator_version must be a non-empty string, got {evaluator_version!r}")

    discovered_by = data["discovered_by"]
    if not isinstance(discovered_by, str) or not discovered_by:
        raise GenomeValidationError(f"discovered_by must be a non-empty string, got {discovered_by!r}")

    disposition = data["disposition"]
    if disposition not in HACK_DISPOSITIONS:
        raise GenomeValidationError(f"disposition must be one of {HACK_DISPOSITIONS}, got {disposition!r}")

    return HackRecord(
        schema=schema, genome_id=genome_id, symptom=symptom, evaluator_version=evaluator_version,
        discovered_by=discovered_by, disposition=disposition,
    )


# ---------------------------------------------------------------------------
# Archive 用の内部モデル（DESIGN_VG_E0.md §3.2。公開 schema ではなく
# archive.py 専用の内部表現 — 台帳へ直接永続化する対象ではないため
# schema 文字列は持たない）。
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArchiveEntry:
    """1 スロット（elite または保護スロット）の占有者。"""

    genome_id: str
    lineage: str
    quality: float


@dataclass(frozen=True)
class EvictionEvent:
    """既存スロット占有者が追い出された記録（append-only。§3.2「追い出しは
    記録付き（絶滅も研究資産）」）。空きスロットへの初回充填は「追い出し」
    ではないため記録しない — `evicted_genome_id` が必ず非 None なのはこの
    ためである。
    """

    cell: Tuple[int, int, int]
    slot: str  # "elite" | "protected"
    evicted_genome_id: str
    evicted_quality: float
    incoming_genome_id: str
    incoming_quality: float
    reason: str
