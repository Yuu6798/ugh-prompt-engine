"""run9_schema.py — RUN9 run-local 正本モジュール（Phase 0 スキャフォールド）。

`DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md`（本ディレクトリ同梱、
以下 DESIGN_RUN9）の §8/§9/§23 を実装する。VG-E0 の凍結三角形
（`voice_genesis/evolution/models.py` の `ANCHOR_NAMES = ("ritsu", "pjs", "user")`）
は DESIGN_RUN9 §8 の指示により一切変更しない。RUN9 は新しい run-local domain
`run9-af0-ritsu-user/1.0`（anchor_order: af0, ritsu, user）を本モジュールが
独立に定義する。VG-E0 の `simplex.py`/`models.py` はモジュールレベルで import
しない（domain が異なるため意味論だけを踏襲した独立実装 — DESIGN_RUN9 §8
「既存 schema・既存台帳を in-place 変更しない」）。

sibling import 流儀（`voice_genesis/evolution/` 全体の家風）を踏襲するため
`_THIS_DIR` を `sys.path` へ挿入する。ただし本モジュール自体は他の run9
sibling モジュールを import しない（現時点では単一ファイル）。

fail-closed 方針（models.py と同型）: 未知キー拒否、欠落キーのデフォルト
補完なし、公開 API に coords/weights の事後注入経路を作らない
（DESIGN_RUN9 §27 item 22 / §9.4「試聴後に0.55/0.45等へ調整してはならない」）。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, FrozenSet, Mapping, Tuple

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

# ---------------------------------------------------------------------------
# 共通定数
# ---------------------------------------------------------------------------

SCHEMA_IDENTITY_DOMAIN = "voicegenesis-identity-domain/1.0"
SCHEMA_RUN_CONTRACT = "voicegenesis-run-contract/1.0"

RUN9_DOMAIN_ID = "run9-af0-ritsu-user/1.0"
RUN9_ANCHOR_ORDER: Tuple[str, str, str] = ("af0", "ritsu", "user")
RUN9_EXCLUDED_TEACHER_IDENTITIES: Tuple[str, ...] = ("pjs",)
RUN9_COORDINATE_PRECISION = 6
RUN9_NORMALIZATION = "largest-component-residual"

RUN_ID = "RUN9"
EXPERIMENT_ID = "VG-R9-DUAL-FOUNDER-PJS"

# DESIGN_RUN9 §9.2/§9.3: 事前固定重み。genome 発行時の唯一の重みソース
# （公開 API から任意 weights を注入する経路は作らない — §27 item 22）。
R9F01_WEIGHTS: Tuple[float, float, float] = (0.6, 0.3, 0.1)
R9F02_WEIGHTS: Tuple[float, float, float] = (0.1, 0.3, 0.6)
SHARED_PERFORMANCE_SEED = 909001
LEARNING_SEED = 909002
MAX_HUMAN_AUDIT_PAIRS = 12

OPERATOR_ID = "TRI_CROSSOVER/1.0"

_FOUNDER_ID_RE = re.compile(r"^R9F-0[12]$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_PLACEHOLDER_RE = re.compile(r"^<[A-Z_]+>$")

_GENOME_ID_LEN = 16
_GENOME_ID_RE = re.compile(rf"^[0-9a-f]{{{_GENOME_ID_LEN}}}$")


class Run9ValidationError(ValueError):
    """Run9IdentityDomain / Run9Coords / Run9FounderGenome / RUN9 Contract の
    構築・デシリアライズ時の型・構造不正。"""


def normalize_signed_zero(x: float) -> float:
    """丸め後の値が負のゼロ（-0.0）であれば正準表現 +0.0 へ正規化する
    （`voice_genesis/evolution/models.py` の同名関数と同一の丸め規約 —
    coords/weights の6桁丸め結果は -0.0 になり得るため、genome_id ハッシュ・
    格納の全経路で最終防衛として正規化する）。"""
    return 0.0 if x == 0.0 else x


# ---------------------------------------------------------------------------
# Run9Coords + normalize（VG-E0 simplex.normalize() と同一意味論の独立実装 —
# 6桁丸め・残差は最大成分へ吸収・タイは anchor_order 順で決定論的に優先）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Run9Coords:
    af0: float
    ritsu: float
    user: float

    def as_dict(self) -> Dict[str, float]:
        return {"af0": self.af0, "ritsu": self.ritsu, "user": self.user}


_MICRO_PER_UNIT = 1_000_000


def _require_finite_triple(af0: float, ritsu: float, user: float) -> None:
    for name, v in (("af0", af0), ("ritsu", ritsu), ("user", user)):
        if not math.isfinite(v):
            raise Run9ValidationError(f"non-finite coordinate rejected: {name}={v!r}")


def normalize_run9_coords(af0: float, ritsu: float, user: float) -> Run9Coords:
    """(af0, ritsu, user) を Δ²（af0/ritsu/user、成分非負・合計1）上へ射影し、
    小数6桁へ丸め、合計が厳密に1.000000になるよう最大成分（クランプ前の生値
    基準）へ残差を吸収する。`voice_genesis/evolution/simplex.py`
    `normalize()` と同一の丸め規約（マイクロ単位整数演算・タイは
    `RUN9_ANCHOR_ORDER` 順で優先）を run-local に独立実装する — domain が
    異なるため import 共有はしない（本モジュール docstring 参照）。

    NaN/inf は即例外。負の生値は0へクランプする。
    """
    _require_finite_triple(af0, ritsu, user)
    raw = {"af0": af0, "ritsu": ritsu, "user": user}
    clamped = {k: max(0.0, v) for k, v in raw.items()}
    micros = {k: int(round(v * _MICRO_PER_UNIT)) for k, v in clamped.items()}
    total = sum(micros.values())
    residual = _MICRO_PER_UNIT - total

    # タイブレークは RUN9_ANCHOR_ORDER 順（af0, ritsu, user）で最初に見つかった
    # 最大値を優先する — Python の max(..., key=...) は同値タイで最初に
    # 出現した要素を返すため、raw を anchor_order 順の list として渡す。
    max_key = max(RUN9_ANCHOR_ORDER, key=lambda k: raw[k])
    micros[max_key] += residual
    if micros[max_key] < 0:
        raise Run9ValidationError(
            f"residual absorption drove the dominant component negative (raw={raw!r}); "
            "refusing to emit an invalid simplex point"
        )
    return Run9Coords(**{
        k: normalize_signed_zero(micros[k] / _MICRO_PER_UNIT) for k in ("af0", "ritsu", "user")
    })


def _validate_run9_coords_value(coords: Run9Coords) -> None:
    """coords が Δ²（af0/ritsu/user、成分非負・合計1）上の正規形（小数6桁
    丸め済み・符号付きゼロ非含有）であることを検証する。"""
    total = 0.0
    for name in RUN9_ANCHOR_ORDER:
        v = getattr(coords, name)
        if not isinstance(v, float) or isinstance(v, bool):
            raise Run9ValidationError(f"coords.{name} must be a float, got {v!r}")
        if not math.isfinite(v):
            raise Run9ValidationError(f"coords.{name} must be finite (NaN/inf rejected): {v!r}")
        if v < 0.0:
            raise Run9ValidationError(f"coords.{name} must be >= 0 (barycentric constraint): {v!r}")
        if round(v, RUN9_COORDINATE_PRECISION) != v:
            raise Run9ValidationError(
                f"coords.{name} must already be rounded to {RUN9_COORDINATE_PRECISION} decimal "
                f"places, got {v!r}"
            )
        if v == 0.0 and math.copysign(1.0, v) < 0.0:
            raise Run9ValidationError(
                f"coords.{name} must be canonical positive zero, not negative zero (-0.0), got {v!r}"
            )
        total += v
    if abs(total - 1.0) > 1e-9:
        raise Run9ValidationError(f"coords must sum to 1.000000 (barycentric constraint), got {total!r}")


# ---------------------------------------------------------------------------
# Run9IdentityDomain
# ---------------------------------------------------------------------------

_DOMAIN_TOP_LEVEL_KEYS: FrozenSet[str] = frozenset({
    "schema", "domain_id", "anchor_order", "anchor_hashes",
    "excluded_teacher_identities", "coordinate_precision", "normalization",
    "metric_space_sha", "pin_source_candidates",
})


@dataclass(frozen=True)
class Run9IdentityDomain:
    """DESIGN_RUN9 §8 の `voicegenesis-identity-domain/1.0` run-local domain。

    `anchor_order` は `RUN9_ANCHOR_ORDER = (af0, ritsu, user)` に固定される
    （順序不変条件）。`anchor_hashes` の3キー全てに64hex sha256 が揃って
    初めて `is_pinned()` が True になる — プレースホルダ（`<PIN_BEFORE_RUN>`
    等）は未 pin 扱い。`pin_source_candidates` は任意の補助情報
    （§ domains/identity_domain_run9_v1.json 参照）で検証対象外。
    """

    schema: str
    domain_id: str
    anchor_order: Tuple[str, str, str]
    anchor_hashes: Dict[str, str]
    excluded_teacher_identities: Tuple[str, ...]
    coordinate_precision: int
    normalization: str
    metric_space_sha: str
    pin_source_candidates: Dict[str, Any]

    def is_pinned(self) -> bool:
        """3 anchor 全てが 64hex sha256（プレースホルダでない）で埋まって
        いるときのみ True。"""
        for name in RUN9_ANCHOR_ORDER:
            value = self.anchor_hashes.get(name)
            if not isinstance(value, str) or not _SHA256_HEX_RE.match(value):
                return False
        return True

    def content_digest(self) -> str:
        """domain の内容ダイジェスト（正規形 JSON の sha256）。
        `build_founder()` の genome_id 計算入力に含める — anchor 未 pin の
        domain から生成した genome は毎回異なるダイジェストを持つため、
        pin 前の genome_id は「正式発行」として意味を持たない
        （DESIGN_RUN9 §22 実行順 step 3→4 の機械強制）。
        """
        canonical = _canonical_json({
            "schema": self.schema,
            "domain_id": self.domain_id,
            "anchor_order": list(self.anchor_order),
            "anchor_hashes": dict(sorted(self.anchor_hashes.items())),
            "excluded_teacher_identities": list(self.excluded_teacher_identities),
            "coordinate_precision": self.coordinate_precision,
            "normalization": self.normalization,
            "metric_space_sha": self.metric_space_sha,
        })
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _reject_pjs_key(*, context: str, keys: Any) -> None:
    """DESIGN_RUN9 §27 item 10「PJS coordinate is structurally impossible」:
    anchor_order / anchor_hashes / coords いずれかのキー集合に "pjs" が
    現れたら構造的に拒否する。PJS は Curriculum provider であり Identity
    anchor ではない（DESIGN_RUN9 §0/§7.4）。"""
    if isinstance(keys, (list, tuple, set, frozenset)):
        key_set = set(keys)
    elif isinstance(keys, Mapping):
        key_set = set(keys.keys())
    else:
        raise TypeError(f"unsupported keys container for pjs rejection: {type(keys).__name__}")
    if "pjs" in key_set:
        raise Run9ValidationError(
            f"{context} may not contain a 'pjs' key — PJS is an external curriculum provider, "
            "never an Identity anchor for RUN9 (DESIGN_RUN9 §0/§7.4/§27 item 10: "
            "'PJS coordinate is structurally impossible')"
        )


def build_run9_identity_domain(
    *,
    anchor_hashes: Mapping[str, str],
    metric_space_sha: str,
    pin_source_candidates: Mapping[str, Any] | None = None,
) -> Run9IdentityDomain:
    """Run9IdentityDomain を構築する唯一の経路。`anchor_order` は
    `RUN9_ANCHOR_ORDER` に固定され、呼び出し元から変更できない
    （並べ替え不可 — DESIGN_RUN9 §27 item 8）。"""
    _reject_pjs_key(context="anchor_hashes", keys=anchor_hashes)
    unknown = set(anchor_hashes.keys()) - set(RUN9_ANCHOR_ORDER)
    if unknown:
        raise Run9ValidationError(f"anchor_hashes has unknown key(s): {sorted(unknown)}")
    missing = set(RUN9_ANCHOR_ORDER) - set(anchor_hashes.keys())
    if missing:
        raise Run9ValidationError(f"anchor_hashes missing required key(s): {sorted(missing)}")
    validated_hashes: Dict[str, str] = {}
    for name in RUN9_ANCHOR_ORDER:
        v = anchor_hashes[name]
        if not isinstance(v, str) or not v:
            raise Run9ValidationError(f"anchor_hashes.{name} must be a non-empty string, got {v!r}")
        validated_hashes[name] = v

    if not isinstance(metric_space_sha, str) or not metric_space_sha:
        raise Run9ValidationError(f"metric_space_sha must be a non-empty string, got {metric_space_sha!r}")

    return Run9IdentityDomain(
        schema=SCHEMA_IDENTITY_DOMAIN,
        domain_id=RUN9_DOMAIN_ID,
        anchor_order=RUN9_ANCHOR_ORDER,
        anchor_hashes=validated_hashes,
        excluded_teacher_identities=RUN9_EXCLUDED_TEACHER_IDENTITIES,
        coordinate_precision=RUN9_COORDINATE_PRECISION,
        normalization=RUN9_NORMALIZATION,
        metric_space_sha=metric_space_sha,
        pin_source_candidates=dict(pin_source_candidates) if pin_source_candidates else {},
    )


def run9_identity_domain_from_dict(data: Any) -> Run9IdentityDomain:
    """JSON dict から Run9IdentityDomain を再構築する。fail-closed（未知
    キー拒否・欠落キーのデフォルト補完なし）。"""
    if not isinstance(data, dict):
        raise Run9ValidationError(f"identity domain document must be an object, got {type(data).__name__}")
    unknown = set(data.keys()) - _DOMAIN_TOP_LEVEL_KEYS
    if unknown:
        raise Run9ValidationError(f"identity domain document has unknown key(s): {sorted(unknown)}")
    required = _DOMAIN_TOP_LEVEL_KEYS - {"pin_source_candidates"}
    missing = required - set(data.keys())
    if missing:
        raise Run9ValidationError(f"identity domain document missing required key(s): {sorted(missing)}")

    schema = data["schema"]
    if schema != SCHEMA_IDENTITY_DOMAIN:
        raise Run9ValidationError(f"schema must be {SCHEMA_IDENTITY_DOMAIN!r}, got {schema!r}")

    domain_id = data["domain_id"]
    if domain_id != RUN9_DOMAIN_ID:
        raise Run9ValidationError(f"domain_id must be {RUN9_DOMAIN_ID!r}, got {domain_id!r}")

    anchor_order_raw = data["anchor_order"]
    if not isinstance(anchor_order_raw, list):
        raise Run9ValidationError(f"anchor_order must be a list, got {type(anchor_order_raw).__name__}")
    _reject_pjs_key(context="anchor_order", keys=anchor_order_raw)
    if tuple(anchor_order_raw) != RUN9_ANCHOR_ORDER:
        raise Run9ValidationError(
            f"anchor_order must be exactly {list(RUN9_ANCHOR_ORDER)} (fixed, no reordering allowed), "
            f"got {anchor_order_raw!r}"
        )

    anchor_hashes_raw = data["anchor_hashes"]
    if not isinstance(anchor_hashes_raw, dict):
        raise Run9ValidationError(f"anchor_hashes must be an object, got {type(anchor_hashes_raw).__name__}")
    _reject_pjs_key(context="anchor_hashes", keys=anchor_hashes_raw)
    unknown_anchor = set(anchor_hashes_raw.keys()) - set(RUN9_ANCHOR_ORDER)
    if unknown_anchor:
        raise Run9ValidationError(f"anchor_hashes has unknown key(s): {sorted(unknown_anchor)}")
    missing_anchor = set(RUN9_ANCHOR_ORDER) - set(anchor_hashes_raw.keys())
    if missing_anchor:
        raise Run9ValidationError(f"anchor_hashes missing required key(s): {sorted(missing_anchor)}")
    anchor_hashes: Dict[str, str] = {}
    for name in RUN9_ANCHOR_ORDER:
        v = anchor_hashes_raw[name]
        if not isinstance(v, str) or not v:
            raise Run9ValidationError(f"anchor_hashes.{name} must be a non-empty string, got {v!r}")
        anchor_hashes[name] = v

    excluded_raw = data["excluded_teacher_identities"]
    if list(excluded_raw) != list(RUN9_EXCLUDED_TEACHER_IDENTITIES):
        raise Run9ValidationError(
            f"excluded_teacher_identities must be exactly {list(RUN9_EXCLUDED_TEACHER_IDENTITIES)}, "
            f"got {excluded_raw!r}"
        )

    precision = data["coordinate_precision"]
    if precision != RUN9_COORDINATE_PRECISION:
        raise Run9ValidationError(
            f"coordinate_precision must be {RUN9_COORDINATE_PRECISION!r}, got {precision!r}"
        )

    normalization = data["normalization"]
    if normalization != RUN9_NORMALIZATION:
        raise Run9ValidationError(f"normalization must be {RUN9_NORMALIZATION!r}, got {normalization!r}")

    metric_space_sha = data["metric_space_sha"]
    if not isinstance(metric_space_sha, str) or not metric_space_sha:
        raise Run9ValidationError(f"metric_space_sha must be a non-empty string, got {metric_space_sha!r}")

    pin_source_candidates_raw = data.get("pin_source_candidates", {})
    if not isinstance(pin_source_candidates_raw, dict):
        raise Run9ValidationError(
            f"pin_source_candidates must be an object, got {type(pin_source_candidates_raw).__name__}"
        )

    return Run9IdentityDomain(
        schema=schema, domain_id=domain_id, anchor_order=RUN9_ANCHOR_ORDER,
        anchor_hashes=anchor_hashes, excluded_teacher_identities=RUN9_EXCLUDED_TEACHER_IDENTITIES,
        coordinate_precision=precision, normalization=normalization, metric_space_sha=metric_space_sha,
        pin_source_candidates=dict(pin_source_candidates_raw),
    )


def run9_identity_domain_from_json(text: str) -> Run9IdentityDomain:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise Run9ValidationError(f"invalid JSON: {exc}") from exc
    return run9_identity_domain_from_dict(data)


def load_run9_identity_domain(path: Path) -> Run9IdentityDomain:
    return run9_identity_domain_from_json(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# TRI_CROSSOVER + Run9FounderGenome（DESIGN_RUN9 §9）
# ---------------------------------------------------------------------------

_GENOME_TOP_LEVEL_KEYS: FrozenSet[str] = frozenset({
    "voice_id", "ecosystem_role", "ecosystem_generation", "genetic_generation",
    "identity_domain", "coords", "profile_label", "performance_seed", "parents",
    "skill_state", "operator_id", "genome_id",
})


@dataclass(frozen=True)
class Run9FounderGenome:
    """DESIGN_RUN9 §9.2/§9.3 の Founder genome。`genome_id` は
    `_compute_founder_genome_id()` の再計算値以外を持てない（構築時にのみ
    導出され、フィールドとして外部から指定できない）。"""

    voice_id: str
    ecosystem_role: str
    ecosystem_generation: int
    genetic_generation: int
    identity_domain: str
    coords: Run9Coords
    profile_label: str
    performance_seed: int
    parents: Tuple[str, str, str]
    skill_state: str
    operator_id: str
    genome_id: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "voice_id": self.voice_id,
            "ecosystem_role": self.ecosystem_role,
            "ecosystem_generation": self.ecosystem_generation,
            "genetic_generation": self.genetic_generation,
            "identity_domain": self.identity_domain,
            "coords": self.coords.as_dict(),
            "profile_label": self.profile_label,
            "performance_seed": self.performance_seed,
            "parents": list(self.parents),
            "skill_state": self.skill_state,
            "operator_id": self.operator_id,
            "genome_id": self.genome_id,
        }


# founder_id -> (weights, profile_label) の閉じたテーブル。DESIGN_RUN9 §9.2/9.3
# の凍結重みそのもの。本テーブル自身は非公開（先頭アンダースコア）— 公開
# 経路は `build_founder(domain, founder_id)` のみで、weights を外部から
# 注入する公開 API は存在しない（§27 item 22「no post-listening coordinate
# adjustment API」）。
_FOUNDER_TABLE: Dict[str, Tuple[Tuple[float, float, float], str]] = {
    "R9F-01": (R9F01_WEIGHTS, "AF0_DOMINANT"),
    "R9F-02": (R9F02_WEIGHTS, "USER_DOMINANT"),
}


def _canonicalize_for_hash(obj: Any) -> Any:
    """genome_id 計算用の正規化: float は小数6桁固定表記の文字列へ変換する
    （`voice_genesis/evolution/models.py` `_canonicalize_for_hash` と同一の
    規約 — 0.500000 と 0.5 が異なるバイト列になるのを防ぐ）。"""
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise Run9ValidationError(f"non-finite value rejected in genome_id payload: {obj!r}")
        return format(normalize_signed_zero(round(obj, RUN9_COORDINATE_PRECISION)), ".6f")
    if isinstance(obj, str):
        return obj
    if obj is None:
        return None
    if isinstance(obj, (list, tuple)):
        return [_canonicalize_for_hash(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): _canonicalize_for_hash(v) for k, v in obj.items()}
    raise Run9ValidationError(f"unsupported type in genome_id payload: {type(obj).__name__}")


def _compute_founder_genome_id(
    *,
    voice_id: str,
    identity_domain: Run9IdentityDomain,
    coords: Run9Coords,
    profile_label: str,
    performance_seed: int,
    parents: Tuple[str, str, str],
    skill_state: str,
    operator_id: str,
) -> str:
    """genome_id = sha256(正規形JSON)[:16]。ハッシュ入力に domain の内容
    ダイジェスト（`Run9IdentityDomain.content_digest()`）を含めることで、
    anchor 未 pin の domain からは実行のたびに異なる genome_id しか
    出せない構造にする（DESIGN_RUN9 §22 step 3→4 の機械強制。実際には
    `build_founder()` が pin 前の domain を先に拒否するため、この性質は
    二重の防御として機能する）。
    """
    payload = {
        "voice_id": voice_id,
        "ecosystem_role": "FOUNDER_CANDIDATE",
        "ecosystem_generation": 0,
        "genetic_generation": 1,
        "identity_domain": identity_domain.domain_id,
        "identity_domain_content_sha256": identity_domain.content_digest(),
        "coords": coords.as_dict(),
        "profile_label": profile_label,
        "performance_seed": performance_seed,
        "parents": list(parents),
        "skill_state": skill_state,
        "operator_id": operator_id,
    }
    canonical = _canonical_json(_canonicalize_for_hash(payload))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:_GENOME_ID_LEN]


def _tri_crossover(
    *,
    domain: Run9IdentityDomain,
    weights: Tuple[float, float, float],
    voice_id: str,
    profile_label: str,
    performance_seed: int,
) -> Run9FounderGenome:
    """TRI_CROSSOVER/1.0 純関数（DESIGN_RUN9 §9.1）。run9 domain では anchor
    が基底ベクトルのため child coords = normalize(weights) そのもの。
    random_search なし・乱数不使用（完全決定論）。本関数は先頭アンダー
    スコアで非公開 — 外部から任意 weights を注入できる公開経路は
    `build_founder(domain, founder_id)` のみ（§27 item 22）。
    """
    if not domain.is_pinned():
        raise Run9ValidationError(
            "TRI_CROSSOVER requires a pinned Run9IdentityDomain (all 3 anchor_hashes must be real "
            "64hex sha256, not placeholders) — DESIGN_RUN9 §22 execution order requires the domain "
            "(step 3) to be frozen before founder generation (step 4)"
        )
    w_af0, w_ritsu, w_user = weights
    coords = normalize_run9_coords(w_af0, w_ritsu, w_user)
    _validate_run9_coords_value(coords)

    genome_id = _compute_founder_genome_id(
        voice_id=voice_id, identity_domain=domain, coords=coords, profile_label=profile_label,
        performance_seed=performance_seed, parents=("AF0", "RITSU", "USER_DONOR"),
        skill_state="DEFAULT_NEUTRAL", operator_id=OPERATOR_ID,
    )
    return Run9FounderGenome(
        voice_id=voice_id, ecosystem_role="FOUNDER_CANDIDATE", ecosystem_generation=0,
        genetic_generation=1, identity_domain=domain.domain_id, coords=coords,
        profile_label=profile_label, performance_seed=performance_seed,
        parents=("AF0", "RITSU", "USER_DONOR"), skill_state="DEFAULT_NEUTRAL",
        operator_id=OPERATOR_ID, genome_id=genome_id,
    )


def build_founder(domain: Run9IdentityDomain, founder_id: str) -> Run9FounderGenome:
    """RUN9 Founder genome を構築する唯一の公開経路。`founder_id` は
    `{"R9F-01", "R9F-02"}` のいずれかのみを受け付け、凍結重みテーブル
    `_FOUNDER_TABLE` から重みを引く。任意の weights を外部から注入する
    公開 API は存在しない（DESIGN_RUN9 §27 item 22 / §9.4）。
    """
    if founder_id not in _FOUNDER_TABLE:
        raise Run9ValidationError(
            f"founder_id must be one of {sorted(_FOUNDER_TABLE)}, got {founder_id!r}"
        )
    weights, profile_label = _FOUNDER_TABLE[founder_id]
    return _tri_crossover(
        domain=domain, weights=weights, voice_id=founder_id, profile_label=profile_label,
        performance_seed=SHARED_PERFORMANCE_SEED,
    )


def founder_genome_from_dict(data: Any) -> Run9FounderGenome:
    """JSON dict から Run9FounderGenome を再構築する。fail-closed（未知
    キー拒否）+ genome_id 再計算値との一致は呼び出し元の責務（本関数は
    構造検証のみを行う — genome_id 再計算には元の Run9IdentityDomain が
    必要なため、比較は `build_founder()` の再実行結果と `to_dict()` を
    突き合わせるテスト側の責務とする）。"""
    if not isinstance(data, dict):
        raise Run9ValidationError(f"genome document must be an object, got {type(data).__name__}")
    unknown = set(data.keys()) - _GENOME_TOP_LEVEL_KEYS
    if unknown:
        raise Run9ValidationError(f"genome document has unknown key(s): {sorted(unknown)}")
    missing = _GENOME_TOP_LEVEL_KEYS - set(data.keys())
    if missing:
        raise Run9ValidationError(f"genome document missing required key(s): {sorted(missing)}")

    voice_id = data["voice_id"]
    if not _FOUNDER_ID_RE.match(voice_id):
        raise Run9ValidationError(f"voice_id must match {_FOUNDER_ID_RE.pattern}, got {voice_id!r}")

    if data["ecosystem_role"] != "FOUNDER_CANDIDATE":
        raise Run9ValidationError(
            f"ecosystem_role must be 'FOUNDER_CANDIDATE', got {data['ecosystem_role']!r}"
        )
    if data["ecosystem_generation"] != 0:
        raise Run9ValidationError(f"ecosystem_generation must be 0, got {data['ecosystem_generation']!r}")
    if data["genetic_generation"] != 1:
        raise Run9ValidationError(f"genetic_generation must be 1, got {data['genetic_generation']!r}")
    if data["identity_domain"] != RUN9_DOMAIN_ID:
        raise Run9ValidationError(f"identity_domain must be {RUN9_DOMAIN_ID!r}, got {data['identity_domain']!r}")

    coords_raw = data["coords"]
    _reject_pjs_key(context="coords", keys=coords_raw if isinstance(coords_raw, dict) else {})
    if not isinstance(coords_raw, dict) or set(coords_raw.keys()) != set(RUN9_ANCHOR_ORDER):
        raise Run9ValidationError(f"coords must have exactly keys {list(RUN9_ANCHOR_ORDER)}, got {coords_raw!r}")
    coords = Run9Coords(**{k: float(coords_raw[k]) for k in RUN9_ANCHOR_ORDER})
    _validate_run9_coords_value(coords)

    if data["profile_label"] not in ("AF0_DOMINANT", "USER_DOMINANT"):
        raise Run9ValidationError(f"profile_label invalid: {data['profile_label']!r}")
    if data["performance_seed"] != SHARED_PERFORMANCE_SEED:
        raise Run9ValidationError(
            f"performance_seed must be {SHARED_PERFORMANCE_SEED!r}, got {data['performance_seed']!r}"
        )
    parents_raw = data["parents"]
    if list(parents_raw) != ["AF0", "RITSU", "USER_DONOR"]:
        raise Run9ValidationError(f"parents must be exactly ['AF0','RITSU','USER_DONOR'], got {parents_raw!r}")
    if data["skill_state"] != "DEFAULT_NEUTRAL":
        raise Run9ValidationError(f"skill_state must be 'DEFAULT_NEUTRAL', got {data['skill_state']!r}")
    if data["operator_id"] != OPERATOR_ID:
        raise Run9ValidationError(f"operator_id must be {OPERATOR_ID!r}, got {data['operator_id']!r}")

    genome_id = data["genome_id"]
    if not isinstance(genome_id, str) or not _GENOME_ID_RE.match(genome_id):
        raise Run9ValidationError(
            f"genome_id must be exactly {_GENOME_ID_LEN} lowercase hex characters, got {genome_id!r}"
        )

    return Run9FounderGenome(
        voice_id=voice_id, ecosystem_role="FOUNDER_CANDIDATE", ecosystem_generation=0,
        genetic_generation=1, identity_domain=RUN9_DOMAIN_ID, coords=coords,
        profile_label=data["profile_label"], performance_seed=data["performance_seed"],
        parents=("AF0", "RITSU", "USER_DONOR"), skill_state="DEFAULT_NEUTRAL",
        operator_id=OPERATOR_ID, genome_id=genome_id,
    )


# ---------------------------------------------------------------------------
# Run Contract（DESIGN_RUN9 §23 `voicegenesis-run-contract/1.0`）
# ---------------------------------------------------------------------------

_PIN_STATUSES: Tuple[str, str, str] = ("PINNED", "PENDING", "BLOCKED")
_PIN_FIELD_ALLOWED_KEYS: FrozenSet[str] = frozenset({"value", "status", "reason", "source"})
_PIN_FIELD_REQUIRED_KEYS: FrozenSet[str] = frozenset({"value", "status"})

# §23 の yaml に列挙された全 pin 欄（design_doc_sha256 は §23 に無いが
# タスク指示により本 contract 実装で追加する欄 — 編入した設計書ファイルの
# 実 sha256 を PINNED で記録する）。
CONTRACT_PIN_FIELDS: Tuple[str, ...] = (
    "design_doc_sha256",
    "attempt_id",
    "repository_commit_sha",
    "dataset_manifest_sha",
    "dataset_row_order_sha",
    "config_sha",
    "dependency_pins_sha",
    "execution_profile_sha",
    "seed_policy_sha",
    "expected_speaker_map_sha",
    "backbone_checkpoint_sha",
    "lesson_sha",
    "learning_recipe_sha",
    "probe_manifest_sha",
    "measurement_spec_sha",
    "hypothesis_algebra_sha",
    "human_evaluation_protocol_sha",
    "artifact_manifest_sha",
    "cost_record_sha",
    "failure_abort_criteria_sha",
)

# founder_genome_shas は {R9F-01: pin_field, R9F-02: pin_field} という
# 入れ子構造のため別枠で扱う（CONTRACT_PIN_FIELDS には含めない）。
CONTRACT_FOUNDER_IDS: Tuple[str, str] = ("R9F-01", "R9F-02")

# post-run pin（実行後にのみ実測できる証拠欄）。gate_state() の READY 判定
# から除外する（DESIGN_RUN9 §27 item 49「incomplete Hard Gate set -> BLOCKED」
# の pre-run 側判定 — artifact/cost は run record closure 側の要件）。
CONTRACT_POST_RUN_PIN_FIELDS: FrozenSet[str] = frozenset({"artifact_manifest_sha", "cost_record_sha"})

_CONTRACT_TOP_LEVEL_KEYS: FrozenSet[str] = frozenset(
    {
        "schema", "run_id", "experiment_id", "design_revision", "design_doc",
        "single_intervention", "baseline_run", "parent_designs",
        "founder_genome_shas", "claim_strength_target",
    }
    | set(CONTRACT_PIN_FIELDS)
)

_RESERVED_CONTRACT_SUBSTRINGS: Tuple[str, ...] = ("total_score", "totalscore")


def _reject_total_score_vocabulary(*, context: str, names: Any) -> None:
    """DESIGN_RUN9 §27 item 40「no TotalScore field in evaluation/result
    schema」: contract / genome のどのフィールド名にも total_score 系の
    語彙を許さない（大文字小文字非依存）。"""
    for name in names:
        lowered = str(name).replace("_", "").lower()
        for forbidden in _RESERVED_CONTRACT_SUBSTRINGS:
            if forbidden.replace("_", "") in lowered:
                raise Run9ValidationError(
                    f"{context} field name {name!r} contains reserved total-score vocabulary "
                    f"({forbidden!r}) — DESIGN_RUN9 §27 item 40 permanently forbids a single "
                    "aggregate score field"
                )


def _validate_pin_field(name: str, field: Any) -> Dict[str, Any]:
    if not isinstance(field, dict):
        raise Run9ValidationError(f"{name} must be an object, got {type(field).__name__}")
    unknown = set(field.keys()) - _PIN_FIELD_ALLOWED_KEYS
    if unknown:
        raise Run9ValidationError(f"{name} has unknown key(s): {sorted(unknown)}")
    missing = _PIN_FIELD_REQUIRED_KEYS - set(field.keys())
    if missing:
        raise Run9ValidationError(f"{name} missing required key(s): {sorted(missing)}")
    status = field["status"]
    if status not in _PIN_STATUSES:
        raise Run9ValidationError(f"{name}.status must be one of {_PIN_STATUSES}, got {status!r}")
    if "reason" in field and not isinstance(field["reason"], str):
        raise Run9ValidationError(f"{name}.reason must be a string, got {field['reason']!r}")
    if "source" in field and field["source"] is not None and not isinstance(field["source"], str):
        raise Run9ValidationError(f"{name}.source must be a string or null, got {field['source']!r}")
    return dict(field)


@dataclass(frozen=True)
class Run9RunContract:
    """DESIGN_RUN9 §23 の `voicegenesis-run-contract/1.0`。value は検証済み
    生 dict をそのまま保持する（RUN9_CONTRACT.yaml 全体の忠実な表現。
    フィールド意味の解釈は `gate_state()` 等の別関数が担う）。"""

    raw: Dict[str, Any]

    def pin_field(self, name: str) -> Dict[str, Any]:
        return self.raw[name]

    def founder_genome_sha(self, founder_id: str) -> Dict[str, Any]:
        return self.raw["founder_genome_shas"][founder_id]


def _is_field_pinned(field: Mapping[str, Any]) -> bool:
    return field.get("status") == "PINNED"


def load_run9_contract(data: Mapping[str, Any]) -> Run9RunContract:
    """RUN9_CONTRACT.yaml をパース済み dict として受け取り検証する。
    fail-closed（未知キー拒否）+ run_id は厳密に "RUN9"（"RUN9A"等は拒否 —
    DESIGN_RUN9 §27 item 54）+ total_score 語彙拒否（item 40）。
    """
    if not isinstance(data, dict):
        raise Run9ValidationError(f"contract document must be an object, got {type(data).__name__}")
    unknown = set(data.keys()) - _CONTRACT_TOP_LEVEL_KEYS
    if unknown:
        raise Run9ValidationError(f"contract document has unknown key(s): {sorted(unknown)}")
    missing = _CONTRACT_TOP_LEVEL_KEYS - set(data.keys())
    if missing:
        raise Run9ValidationError(f"contract document missing required key(s): {sorted(missing)}")

    _reject_total_score_vocabulary(context="contract", names=data.keys())

    schema = data["schema"]
    if schema != SCHEMA_RUN_CONTRACT:
        raise Run9ValidationError(f"schema must be {SCHEMA_RUN_CONTRACT!r}, got {schema!r}")

    run_id = data["run_id"]
    if run_id != RUN_ID:
        raise Run9ValidationError(
            f"run_id must be exactly {RUN_ID!r} — branch numbers (e.g. 'RUN9A'/'RUN9B'/'RUN9C') are "
            f"forbidden (DESIGN_RUN9 §27 item 54 / header note: design changes are tracked via "
            f"design_revision, execution history via attempt_id), got {run_id!r}"
        )

    experiment_id = data["experiment_id"]
    if experiment_id != EXPERIMENT_ID:
        raise Run9ValidationError(f"experiment_id must be {EXPERIMENT_ID!r}, got {experiment_id!r}")

    if not isinstance(data["design_revision"], str) or not data["design_revision"]:
        raise Run9ValidationError(f"design_revision must be a non-empty string, got {data['design_revision']!r}")

    if not isinstance(data["design_doc"], str) or not data["design_doc"]:
        raise Run9ValidationError(f"design_doc must be a non-empty string, got {data['design_doc']!r}")

    single_intervention = data["single_intervention"]
    if not isinstance(single_intervention, dict):
        raise Run9ValidationError("single_intervention must be an object")
    allowed_si_keys = {"description", "changed_edge"}
    unknown_si = set(single_intervention.keys()) - allowed_si_keys
    if unknown_si:
        raise Run9ValidationError(f"single_intervention has unknown key(s): {sorted(unknown_si)}")
    missing_si = allowed_si_keys - set(single_intervention.keys())
    if missing_si:
        raise Run9ValidationError(f"single_intervention missing key(s): {sorted(missing_si)}")

    if data["baseline_run"] is not None:
        raise Run9ValidationError(f"baseline_run must be null (RUN9 has no baseline_run), got {data['baseline_run']!r}")

    parent_designs = data["parent_designs"]
    if not isinstance(parent_designs, list) or not parent_designs:
        raise Run9ValidationError("parent_designs must be a non-empty list")

    for name in CONTRACT_PIN_FIELDS:
        _validate_pin_field(name, data[name])

    founder_shas = data["founder_genome_shas"]
    if not isinstance(founder_shas, dict) or set(founder_shas.keys()) != set(CONTRACT_FOUNDER_IDS):
        raise Run9ValidationError(
            f"founder_genome_shas must have exactly keys {list(CONTRACT_FOUNDER_IDS)}, got {founder_shas!r}"
        )
    for founder_id in CONTRACT_FOUNDER_IDS:
        _validate_pin_field(f"founder_genome_shas.{founder_id}", founder_shas[founder_id])

    claim_strength = data["claim_strength_target"]
    if claim_strength != "C2":
        raise Run9ValidationError(f"claim_strength_target must be 'C2', got {claim_strength!r}")

    return Run9RunContract(raw=dict(data))


def load_run9_contract_from_yaml_text(text: str) -> Run9RunContract:
    import yaml  # PyYAML は本体必須依存（pyproject.toml [project].dependencies）

    data = yaml.safe_load(text)
    return load_run9_contract(data)


def load_run9_contract_from_yaml_path(path: Path) -> Run9RunContract:
    return load_run9_contract_from_yaml_text(Path(path).read_text(encoding="utf-8"))


def gate_state(contract: Run9RunContract) -> str:
    """DESIGN_RUN9 §27 item 49「incomplete Hard Gate set -> BLOCKED」の
    pre-run 機械判定: pre-run 必須欄（`CONTRACT_PIN_FIELDS` から
    `CONTRACT_POST_RUN_PIN_FIELDS` を除いた全欄 + 両 founder の
    founder_genome_shas）が全て PINNED のときのみ "READY"。1つでも
    PENDING/BLOCKED なら "BLOCKED"。post-run 専用欄
    （artifact_manifest_sha / cost_record_sha）は判定対象外
    （実行後にのみ実測できる証拠欄のため — RUN_CONTRACT_SCHEMA_v1.json の
    x-gate-class post_run 分類と同じ考え方）。
    """
    pre_run_fields = [n for n in CONTRACT_PIN_FIELDS if n not in CONTRACT_POST_RUN_PIN_FIELDS]
    for name in pre_run_fields:
        if not _is_field_pinned(contract.pin_field(name)):
            return "BLOCKED"
    for founder_id in CONTRACT_FOUNDER_IDS:
        if not _is_field_pinned(contract.founder_genome_sha(founder_id)):
            return "BLOCKED"
    return "READY"
