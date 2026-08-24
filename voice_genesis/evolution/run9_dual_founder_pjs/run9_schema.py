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

import copy
import hashlib
import json
import math
import re
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Mapping, Tuple

import yaml  # PyYAML は本体必須依存（pyproject.toml [project].dependencies）

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

# 現行 design_revision（凍結値。User 裁定 2026-08-24 =
# DESIGN_RUN9_REVISION_0.2.md）。旧 revision "0.1" を宣言する contract は
# 意図どおり拒否される — 修正が必要なら design_revision を上げ、旧
# attempt を append-only 履歴として残す規約（DESIGN_RUN9 ヘッダ注記）。
DESIGN_REVISION = "0.2"

# DESIGN_RUN9 §23: 単一介入エッジは凍結値。他のエッジへの差し替えは新しい
# design_revision を持つ別 attempt として扱う（§20 禁止事項「結果を見た後の
# 座標・Lesson・閾値追加」と同種の凍結規律）。
CHANGED_EDGE = "LEARN_PERFORMANCE"

# DESIGN_RUN9 §6 の parent_designs 正典（凍結値。順序も含めて完全一致を
# 要求する — Codex bot レビュー PR #315 第7巡指摘2採用）。§6 は5件を宣言
# するが §23 の Run Contract 雛形は3件しか列挙していない設計書内部の
# erratum（第6巡指摘1で判明・contract 側で是正済み）があるため、完全側の
# §6 を正典として run-local に固定する（設計書自体は byte-pin 済みのため
# 一切編集しない）。
PARENT_DESIGNS: Tuple[str, ...] = (
    "voice_genesis/evolution/DESIGN_VG_E0.md",
    "voice_genesis/evolution/DESIGN_VG_L0.md",
    "VoiceGenesis Evolution Theory v0.3",
    "VoiceGenesis Singing Baseline v0.1",
    "VoiceGenesis Supplement A / Selection Pressure Routing",
)

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
# git commit object ID は SHA-1（40桁小文字hex）— repository_commit_sha は
# 他の *_sha 欄（sha256）と同じ64hex規則を課すと、正直な git sha を PINNED
# にしても構造的に READY へ到達できなくなる不備だった（第1巡修正時の
# 見落とし。Codex bot レビュー PR #315 第3巡指摘1採用）。
_SHA1_HEX_RE = re.compile(r"^[0-9a-f]{40}$")
# attempt_id の正の文法（Codex bot レビュー PR #315 第4巡指摘採用）: 先頭は
# 英数字、以降は英数字/`.`/`_`/`-` のみ。プレースホルダ変種
# （`" <PIN_BEFORE_RUN> "` のような前後空白、`<PIN_1>` のような数字入り等）を
# 個別にブラックリスト追撃するのではなく、`<`/`>`/空白を構造的に許容しない
# 正の文法で終端する。
_ATTEMPT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

_GENOME_ID_LEN = 16
_GENOME_ID_RE = re.compile(rf"^[0-9a-f]{{{_GENOME_ID_LEN}}}$")


class Run9ValidationError(ValueError):
    """Run9IdentityDomain / Run9Coords / Run9FounderGenome / RUN9 Contract の
    構築・デシリアライズ時の型・構造不正。"""


class _StrictYAMLLoader(yaml.SafeLoader):
    """`yaml.SafeLoader` を継承し、mapping ノードの重複キーを fail-closed
    拒否する（VG-E0 `models.py` `loads_strict()` と同型の fail-closed
    規約を YAML 読込にも適用する — Codex bot レビュー PR #315 第8巡指摘1
    採用）。`construct_mapping` は文書内の全ての mapping ノードへ
    （トップレベル・ネストした pin 欄 dict を含め）再帰的に呼ばれるため、
    この一箇所のオーバーライドだけで任意の深さの重複キーを検出できる。
    重複キーが無い場合の挙動は `yaml.safe_load()` と完全に同一。
    """

    def construct_mapping(self, node: Any, deep: bool = False) -> Dict[Any, Any]:
        seen: set = set()
        for key_node, _value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise Run9ValidationError(f"duplicate key in YAML mapping: {key!r}")
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


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


# 型強制/等価比較サイトのファミリー終端宣言（Codex bot レビュー PR #315
# 第5巡（coordinate_precision/coords）→ 第6巡（run_id/experiment_id/
# claim_strength_target/ecosystem_generation/genetic_generation/
# performance_seed/parents/excluded_teacher_identities/anchor_order/
# voice_id/profile_label/skill_state/operator_id/ecosystem_role/
# identity_domain/schema/domain_id/normalization/parent_designs）で全数
# 掃討: JSON 由来の生値は `_is_strict_int()`（bool 除外 int）/
# `isinstance(x, list)`（dict のキー列挙で `list(...)` 比較をすり抜ける
# 経路を拒否）/ `isinstance(x, str)` のいずれかの厳密型検査を通ってから
# のみ等価比較・型変換に進む。本ファミリーはこの巡で終端する。


def _is_strict_int(value: Any) -> bool:
    """bool を明示的に除外した厳密 int 判定（Codex bot レビュー PR #315
    第5巡指摘1採用）: Python は `True == 1` / `6.0 == 6` が真になるため、
    `value == RUN9_COORDINATE_PRECISION` のような等価比較だけでは
    `coordinate_precision: 6.0`（float）や `coordinate_precision: true`
    のような非正準値も通過してしまう。通過を許すと、同一のはずの pinned
    domain から `content_digest()` の JSON 直列化時に異なるバイト列
    （ひいては異なる genome_id）が出る決定論欠陥になる。
    """
    return isinstance(value, int) and not isinstance(value, bool)


def _require_valid_coord_scalar(value: Any, field: str) -> float:
    """coords 生値の型強制排除（Codex bot レビュー PR #315 第5巡指摘2
    採用）: bool でない int または有限 float のみを許可し、int は明示的に
    float へ変換する（JSON の `0`/`1` 等を許容するため）。文字列
    （例 `"0.6"`）や bool の黙った型正規化は、改ざん検出を掲げる
    `founder_genome_from_dict()` が非正準・改変された genome document を
    正典として通してしまう契約矛盾になるため拒否する。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Run9ValidationError(f"{field} must be a number (bool/str rejected), got {value!r}")
    out = float(value)
    if not math.isfinite(out):
        raise Run9ValidationError(f"{field} must be finite (NaN/inf rejected): {value!r}")
    return out


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

    `anchor_hashes` / `pin_source_candidates` は `__post_init__` で
    `types.MappingProxyType` に凍結される（読み取り専用 `Mapping`）—
    `dataclass(frozen=True)` はトップレベル属性の再代入だけを禁止し、
    属性が指すネスト dict 自体は素の mutable dict のままだったため、
    構築後に `domain.anchor_hashes["af0"] = ...` のような in-place 書き換え
    で anchor set を差し替えても型レベルでは防げていなかった（Codex bot
    レビュー PR #315 第3巡指摘3採用）。
    """

    schema: str
    domain_id: str
    anchor_order: Tuple[str, str, str]
    anchor_hashes: Mapping[str, str]
    excluded_teacher_identities: Tuple[str, ...]
    coordinate_precision: int
    normalization: str
    metric_space_sha: str
    pin_source_candidates: Mapping[str, Any]

    def __post_init__(self) -> None:
        # frozen dataclass では `self.x = ...` が使えないため
        # `object.__setattr__` で直接代入する（dataclass 自身の凍結機構と
        # 同じ回避手段）。
        object.__setattr__(self, "anchor_hashes", types.MappingProxyType(dict(self.anchor_hashes)))
        object.__setattr__(
            self, "pin_source_candidates", types.MappingProxyType(dict(self.pin_source_candidates))
        )

    def is_pinned(self) -> bool:
        """3 anchor 全てに加え `metric_space_sha` も 64hex sha256
        （プレースホルダでない）で埋まっているときのみ True。
        `metric_space_sha` を含めるのは `content_digest()` の入力に含まれる
        ため — これを未 pin のまま genome を発行し、後から pin し直すと
        `content_digest()` ひいては genome_id が変わり、既発行の成果物が
        無効化される（将来汚染。Codex bot レビュー PR #315 指摘1採用）。"""
        for name in RUN9_ANCHOR_ORDER:
            value = self.anchor_hashes.get(name)
            if not isinstance(value, str) or not _SHA256_HEX_RE.match(value):
                return False
        if not isinstance(self.metric_space_sha, str) or not _SHA256_HEX_RE.match(self.metric_space_sha):
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
    if not isinstance(schema, str) or schema != SCHEMA_IDENTITY_DOMAIN:
        raise Run9ValidationError(f"schema must be {SCHEMA_IDENTITY_DOMAIN!r}, got {schema!r}")

    domain_id = data["domain_id"]
    if not isinstance(domain_id, str) or domain_id != RUN9_DOMAIN_ID:
        raise Run9ValidationError(f"domain_id must be {RUN9_DOMAIN_ID!r}, got {domain_id!r}")

    anchor_order_raw = data["anchor_order"]
    if not isinstance(anchor_order_raw, list):
        raise Run9ValidationError(f"anchor_order must be a list, got {type(anchor_order_raw).__name__}")
    if not all(isinstance(item, str) for item in anchor_order_raw):
        raise Run9ValidationError(f"anchor_order elements must all be strings, got {anchor_order_raw!r}")
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
    # isinstance(list) + 全要素 str を先行させる（Codex bot レビュー PR #315
    # 第6巡指摘2採用）: 旧実装の `list(excluded_raw) != list(...)` は、
    # `excluded_raw` が `{"pjs": 1}` のような dict でも `list(dict)` が
    # キー列挙で `["pjs"]` を返し `list(RUN9_EXCLUDED_TEACHER_IDENTITIES)`
    # （`["pjs"]`）と一致してしまう穴だった。
    if not isinstance(excluded_raw, list) or not all(isinstance(item, str) for item in excluded_raw):
        raise Run9ValidationError(
            f"excluded_teacher_identities must be a list of strings, got {excluded_raw!r}"
        )
    if list(excluded_raw) != list(RUN9_EXCLUDED_TEACHER_IDENTITIES):
        raise Run9ValidationError(
            f"excluded_teacher_identities must be exactly {list(RUN9_EXCLUDED_TEACHER_IDENTITIES)}, "
            f"got {excluded_raw!r}"
        )

    precision = data["coordinate_precision"]
    if not _is_strict_int(precision) or precision != RUN9_COORDINATE_PRECISION:
        raise Run9ValidationError(
            f"coordinate_precision must be the exact int {RUN9_COORDINATE_PRECISION!r} — bool and "
            "float variants are rejected (Python's == would otherwise accept 6.0/True as equal to "
            f"6, which breaks content_digest() determinism), got {precision!r} "
            f"({type(precision).__name__})"
        )

    normalization = data["normalization"]
    if not isinstance(normalization, str) or normalization != RUN9_NORMALIZATION:
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


def _reject_duplicate_json_keys(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    """`json.loads(..., object_pairs_hook=...)` 用フック。VG-E0
    `voice_genesis/evolution/models.py` の `loads_strict()`（重複キー拒否の
    既存先例）と同型の fail-closed 規約を run-local に実装する（Codex bot
    レビュー PR #315 第8巡指摘1採用）: 標準の `json.loads` は同一 JSON
    オブジェクト内に同じキーが複数回出現しても黙って後勝ちで採用するため、
    手編集した domain document で `anchor_hashes` 内に `af0` を2回書く
    ような改ざんが検証をすり抜け得た。`object_pairs_hook` は文書内の全ての
    `{...}` ノードへボトムアップで（最も深い入れ子から順に）呼ばれるため、
    本フックをトップレベルの構築に使うだけで、任意の深さの入れ子オブジェ
    クトの重複キーも自動的に検出できる。
    """
    seen: set = set()
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise Run9ValidationError(f"duplicate key in JSON object: {key!r}")
        seen.add(key)
        result[key] = value
    return result


def _loads_strict_json(text: str) -> Any:
    """`json.loads()` 相当だが、全階層の JSON オブジェクトで重複キーを
    fail-closed 拒否する（models.py `loads_strict()` と同型の規約）。"""
    return json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)


def run9_identity_domain_from_json(text: str) -> Run9IdentityDomain:
    try:
        data = _loads_strict_json(text)
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


def _validate_domain_invariants(domain: Run9IdentityDomain) -> None:
    """`Run9IdentityDomain` の不変条件を全数検証する（Codex bot レビュー
    PR #315 第2巡指摘3採用）: `run9_identity_domain_from_dict()` を経由
    せず `Run9IdentityDomain(...)` を直接インスタンス化した偽 domain
    （dataclass はコンストラクタレベルの検証を持たない）が `is_pinned()`
    だけを満たして `build_founder()` へ渡された場合に、domain_id 偽装等を
    ここで検出する。違反は Run9ValidationError。
    """
    if domain.schema != SCHEMA_IDENTITY_DOMAIN:
        raise Run9ValidationError(f"domain.schema must be {SCHEMA_IDENTITY_DOMAIN!r}, got {domain.schema!r}")
    if domain.domain_id != RUN9_DOMAIN_ID:
        raise Run9ValidationError(f"domain.domain_id must be {RUN9_DOMAIN_ID!r}, got {domain.domain_id!r}")
    if domain.anchor_order != RUN9_ANCHOR_ORDER:
        raise Run9ValidationError(
            f"domain.anchor_order must be exactly {RUN9_ANCHOR_ORDER!r}, got {domain.anchor_order!r}"
        )
    if domain.excluded_teacher_identities != RUN9_EXCLUDED_TEACHER_IDENTITIES:
        raise Run9ValidationError(
            f"domain.excluded_teacher_identities must be exactly "
            f"{RUN9_EXCLUDED_TEACHER_IDENTITIES!r}, got {domain.excluded_teacher_identities!r}"
        )
    if not _is_strict_int(domain.coordinate_precision) or domain.coordinate_precision != RUN9_COORDINATE_PRECISION:
        raise Run9ValidationError(
            f"domain.coordinate_precision must be the exact int {RUN9_COORDINATE_PRECISION!r} — bool "
            "and float variants are rejected (Codex bot review PR #315 第5巡指摘1), got "
            f"{domain.coordinate_precision!r} ({type(domain.coordinate_precision).__name__})"
        )
    if domain.normalization != RUN9_NORMALIZATION:
        raise Run9ValidationError(
            f"domain.normalization must be {RUN9_NORMALIZATION!r}, got {domain.normalization!r}"
        )
    if set(domain.anchor_hashes.keys()) != set(RUN9_ANCHOR_ORDER):
        raise Run9ValidationError(
            f"domain.anchor_hashes must have exactly keys {set(RUN9_ANCHOR_ORDER)}, "
            f"got {set(domain.anchor_hashes.keys())!r} (this also rejects a smuggled 'pjs' key)"
        )


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
    _validate_domain_invariants(domain)
    if not domain.is_pinned():
        raise Run9ValidationError(
            "TRI_CROSSOVER requires a pinned Run9IdentityDomain (all 3 anchor_hashes and "
            "metric_space_sha must be real 64hex sha256, not placeholders) — DESIGN_RUN9 §22 "
            "execution order requires the domain (step 3) to be frozen before founder generation "
            "(step 4)"
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


def founder_genome_from_dict(data: Any, *, domain: Run9IdentityDomain) -> Run9FounderGenome:
    """JSON dict から Run9FounderGenome を再構築する。fail-closed（未知
    キー拒否）+ 構造検証の後、`build_founder(domain, voice_id)` で正典を
    再構築し `to_dict()`（genome_id 含む）が完全一致することを要求する
    （改ざん検出。Codex bot レビュー PR #315 指摘3採用: 従来は voice_id /
    coords / genome_id 相互の整合を検証しておらず、「R9F-01 ラベル +
    R9F-02 座標 + 任意の16hex genome_id」のような偽装 genome document が
    構造検証だけを通過し得た）。"""
    if not isinstance(data, dict):
        raise Run9ValidationError(f"genome document must be an object, got {type(data).__name__}")
    unknown = set(data.keys()) - _GENOME_TOP_LEVEL_KEYS
    if unknown:
        raise Run9ValidationError(f"genome document has unknown key(s): {sorted(unknown)}")
    missing = _GENOME_TOP_LEVEL_KEYS - set(data.keys())
    if missing:
        raise Run9ValidationError(f"genome document missing required key(s): {sorted(missing)}")

    voice_id = data["voice_id"]
    if not isinstance(voice_id, str):
        raise Run9ValidationError(f"voice_id must be a string, got {voice_id!r}")
    if not _FOUNDER_ID_RE.match(voice_id):
        raise Run9ValidationError(f"voice_id must match {_FOUNDER_ID_RE.pattern}, got {voice_id!r}")

    ecosystem_role = data["ecosystem_role"]
    if not isinstance(ecosystem_role, str) or ecosystem_role != "FOUNDER_CANDIDATE":
        raise Run9ValidationError(f"ecosystem_role must be 'FOUNDER_CANDIDATE', got {ecosystem_role!r}")
    # ecosystem_generation/genetic_generation/performance_seed: 厳密int等値
    # （Codex bot レビュー PR #315 第6巡指摘2採用）。`!= 0`/`!= 1` のような
    # 素の等価比較は bool（`False == 0`/`True == 1`）や float
    # （`0.0 == 0`/`909001.0 == 909001`）を黙って通してしまう — 通過を
    # 許すと `_canonicalize_for_hash()` の genome_id 直列化で非正準値が
    # 混入しうる。`_is_strict_int()` で bool/float を先に排除する。
    ecosystem_generation = data["ecosystem_generation"]
    if not _is_strict_int(ecosystem_generation) or ecosystem_generation != 0:
        raise Run9ValidationError(f"ecosystem_generation must be the exact int 0, got {ecosystem_generation!r}")
    genetic_generation = data["genetic_generation"]
    if not _is_strict_int(genetic_generation) or genetic_generation != 1:
        raise Run9ValidationError(f"genetic_generation must be the exact int 1, got {genetic_generation!r}")
    identity_domain = data["identity_domain"]
    if not isinstance(identity_domain, str) or identity_domain != RUN9_DOMAIN_ID:
        raise Run9ValidationError(f"identity_domain must be {RUN9_DOMAIN_ID!r}, got {identity_domain!r}")

    coords_raw = data["coords"]
    _reject_pjs_key(context="coords", keys=coords_raw if isinstance(coords_raw, dict) else {})
    if not isinstance(coords_raw, dict) or set(coords_raw.keys()) != set(RUN9_ANCHOR_ORDER):
        raise Run9ValidationError(f"coords must have exactly keys {list(RUN9_ANCHOR_ORDER)}, got {coords_raw!r}")
    # Codex bot レビュー PR #315 第5巡指摘2採用: 生値を `float(...)` へ黙って
    # 型強制するのではなく `_require_valid_coord_scalar()` で「bool でない
    # int/有限float」であることを検証してから変換する。改ざん検出を掲げる
    # 本関数が文字列（例 "0.6"）等の非正準値まで黙って正規化して受理すると、
    # 非正準・改変された genome document が builder 照合を通過して正典
    # として返る契約矛盾になる。
    coords = Run9Coords(**{
        k: _require_valid_coord_scalar(coords_raw[k], f"coords.{k}") for k in RUN9_ANCHOR_ORDER
    })
    _validate_run9_coords_value(coords)

    profile_label = data["profile_label"]
    if not isinstance(profile_label, str) or profile_label not in ("AF0_DOMINANT", "USER_DOMINANT"):
        raise Run9ValidationError(f"profile_label invalid: {profile_label!r}")
    performance_seed = data["performance_seed"]
    if not _is_strict_int(performance_seed) or performance_seed != SHARED_PERFORMANCE_SEED:
        raise Run9ValidationError(
            f"performance_seed must be the exact int {SHARED_PERFORMANCE_SEED!r}, got {performance_seed!r}"
        )
    parents_raw = data["parents"]
    # isinstance(list) を先行させる（Codex bot レビュー PR #315 第6巡指摘2
    # 採用）: `list(parents_raw) != [...]` は `parents_raw` が
    # `{"AF0": 1, "RITSU": 1, "USER_DONOR": 1}` のような dict でも
    # `list(dict)` がキー列挙で `["AF0","RITSU","USER_DONOR"]` を返し
    # 一致してしまう（`excluded_teacher_identities` の同型欠陥と同じ穴）。
    if not isinstance(parents_raw, list) or parents_raw != ["AF0", "RITSU", "USER_DONOR"]:
        raise Run9ValidationError(f"parents must be exactly ['AF0','RITSU','USER_DONOR'], got {parents_raw!r}")
    skill_state = data["skill_state"]
    if not isinstance(skill_state, str) or skill_state != "DEFAULT_NEUTRAL":
        raise Run9ValidationError(f"skill_state must be 'DEFAULT_NEUTRAL', got {skill_state!r}")
    operator_id = data["operator_id"]
    if not isinstance(operator_id, str) or operator_id != OPERATOR_ID:
        raise Run9ValidationError(f"operator_id must be {OPERATOR_ID!r}, got {operator_id!r}")

    genome_id = data["genome_id"]
    if not isinstance(genome_id, str) or not _GENOME_ID_RE.match(genome_id):
        raise Run9ValidationError(
            f"genome_id must be exactly {_GENOME_ID_LEN} lowercase hex characters, got {genome_id!r}"
        )

    declared = Run9FounderGenome(
        voice_id=voice_id, ecosystem_role="FOUNDER_CANDIDATE", ecosystem_generation=0,
        genetic_generation=1, identity_domain=RUN9_DOMAIN_ID, coords=coords,
        profile_label=data["profile_label"], performance_seed=data["performance_seed"],
        parents=("AF0", "RITSU", "USER_DONOR"), skill_state="DEFAULT_NEUTRAL",
        operator_id=OPERATOR_ID, genome_id=genome_id,
    )

    # builder 照合（改ざん検出）: voice_id から凍結重みで正典を再構築し、
    # 宣言値と完全一致することを要求する。voice_id/coords が食い違えば
    # coords 不一致で、genome_id だけが差し替えられていれば genome_id
    # 不一致で検出される。
    canonical = build_founder(domain, voice_id)
    if declared.to_dict() != canonical.to_dict():
        raise Run9ValidationError(
            "genome document does not match the canonical reconstruction from "
            f"build_founder(domain, {voice_id!r}) — declared={declared.to_dict()!r} "
            f"canonical={canonical.to_dict()!r} (tampering or corruption)"
        )
    return canonical


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
    # design_revision 0.2 で追加（User 裁定 2026-08-24）: DESIGN_RUN9_REVISION_0.2.md
    # 自体の実 sha256（design_doc_sha256 と同じ前例方式）。
    "design_revision_doc_sha256",
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
    # design_revision 0.2 で追加: inputs/backbone_runtime_bundle.json 自体の
    # 実 sha256。bundle 内に PENDING 欄が残る場合はこの欄も PENDING とする
    # （bundle 内 PENDING 解消後に pin — CONTRACT_PIN_FIELDS のコメント規約
    # どおり loader 自体は bundle の中身までは検査しない。整合は手動運用）。
    "backbone_runtime_bundle_sha",
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


def _validate_pin_field_value_shape(name: str, value: Any) -> None:
    """PINNED 状態の pin 欄 value の欄名別整形式検証（Codex bot レビュー
    PR #315 指摘1採用）: `founder_genome_shas.R9F-0x` は **64hex sha256**
    形式（PR #315 第7巡指摘1採用 — 意味論の是正: §23 は本欄を `_sha`
    ではなく `founder_genome_shas` と命名しているが値の性質は他の `_sha`
    欄と同じ「永続 artifact のバイト sha256」であり、R9-G12
    「Genome bytes の replay 照合」が要求するのは `founders/R9F-0x_genome.json`
    という**永続 genome 文書ファイルのバイト列**の sha256 である。第1巡
    修正が採用した16hex `genome_id`（`compute_genome_id()` が返す正規形
    JSON 由来の**内容 ID**）は、genome 文書内部の1フィールドとして保持
    される値であって、文書ファイル自体のバイト凍結ではない — 同じ
    genome_id を宣言したまま notes 欄や整形（インデント等）だけ変えた
    再直列化ファイルを検出できない意味論の誤りだった）、`attempt_id` は
    正の文法 `_ATTEMPT_ID_RE` に完全一致（PR #315 第4巡指摘採用: 旧実装は
    「非空 + プレースホルダ正規表現不一致」というブラックリスト式で、
    `" <PIN_BEFORE_RUN> "`（前後空白で `strip()` 後だけ比較していたため
    素通り）や `<PIN_1>`（大文字+アンダースコア限定のブラックリスト
    正規表現の想定外）のようなプレースホルダ変種を追撃しきれなかった —
    個別変種のブラックリスト追撃ではなく、先頭英数字・以降英数字/`.`/
    `_`/`-` のみという正の文法で「`<`/`>`/空白を構造的に許容しない」形に
    終端する）、`repository_commit_sha` は git commit object ID の 40hex
    （SHA-1）形式（PR #315 第3巡指摘1: 64hex を要求すると正直な git sha
    を PINNED にしても contract が構造的に READY へ到達不能だった — 第1巡
    修正の不備）、それ以外の `_sha`/`_sha256` で終わるトップレベル欄
    （`design_doc_sha256` を含む）は 64hex sha256 形式を要求する。
    """
    if name.startswith("founder_genome_shas."):
        if not isinstance(value, str) or not _SHA256_HEX_RE.match(value):
            raise Run9ValidationError(
                f"{name}.value must be exactly 64 lowercase hex characters (sha256 of the persisted "
                "genome document file, e.g. founders/R9F-0x_genome.json — NOT the 16hex genome_id "
                "content-id, which is a field inside that document rather than a byte-freeze of the "
                f"document itself) when status is PINNED, got {value!r}"
            )
        return
    if name == "attempt_id":
        if not isinstance(value, str) or not _ATTEMPT_ID_RE.match(value):
            raise Run9ValidationError(
                f"{name}.value must match {_ATTEMPT_ID_RE.pattern!r} when status is PINNED (leading "
                "alphanumeric, then alphanumeric/'.'/'_'/'-' only — this structurally excludes "
                "whitespace and '<'/'>' placeholder markers rather than blacklisting individual "
                f"placeholder variants), got {value!r}"
            )
        return
    if name == "repository_commit_sha":
        if not isinstance(value, str) or not _SHA1_HEX_RE.match(value):
            raise Run9ValidationError(
                f"{name}.value must be exactly 40 lowercase hex characters (git SHA-1 object ID "
                f"format — this repository uses SHA-1 commit ids, not sha256) when status is PINNED, "
                f"got {value!r}"
            )
        return
    if name.endswith("_sha") or name.endswith("_sha256"):
        if not isinstance(value, str) or not _SHA256_HEX_RE.match(value):
            raise Run9ValidationError(
                f"{name}.value must be exactly 64 lowercase hex characters (sha256 format) when "
                f"status is PINNED, got {value!r}"
            )
        return


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
    if status == "PINNED":
        # PENDING/BLOCKED は従来どおり value が null でもよい（正直な未 pin
        # 表現）。PINNED を名乗る欄だけは value 非 null + 欄名別整形式を
        # load 時に強制する — 「全欄 status だけ PINNED にして READY を
        # 騙る」経路を loader 段で閉じる（Codex bot レビュー PR #315 指摘1）。
        value = field["value"]
        if value is None:
            raise Run9ValidationError(
                f"{name}.status is PINNED but value is null — a PINNED pin field must carry a real "
                "value (Codex bot review PR #315 指摘1)"
            )
        _validate_pin_field_value_shape(name, value)
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
    if not isinstance(run_id, str) or run_id != RUN_ID:
        raise Run9ValidationError(
            f"run_id must be exactly {RUN_ID!r} — branch numbers (e.g. 'RUN9A'/'RUN9B'/'RUN9C') are "
            f"forbidden (DESIGN_RUN9 §27 item 54 / header note: design changes are tracked via "
            f"design_revision, execution history via attempt_id), got {run_id!r}"
        )

    experiment_id = data["experiment_id"]
    if not isinstance(experiment_id, str) or experiment_id != EXPERIMENT_ID:
        raise Run9ValidationError(f"experiment_id must be {EXPERIMENT_ID!r}, got {experiment_id!r}")

    design_revision = data["design_revision"]
    if not isinstance(design_revision, str) or design_revision != DESIGN_REVISION:
        raise Run9ValidationError(
            f"design_revision must be exactly {DESIGN_REVISION!r} (current revision — "
            "DESIGN_RUN9_REVISION_0.2.md, User ruling 2026-08-24). A contract declaring an older "
            "revision (e.g. '0.1') is rejected by design: revising the design requires bumping "
            f"design_revision and keeping the old attempt as append-only history, got {design_revision!r}"
        )

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
    si_description = single_intervention["description"]
    if not isinstance(si_description, str) or not si_description.strip():
        raise Run9ValidationError(
            f"single_intervention.description must be a non-empty string, got {si_description!r}"
        )
    si_changed_edge = single_intervention["changed_edge"]
    if si_changed_edge != CHANGED_EDGE:
        raise Run9ValidationError(
            f"single_intervention.changed_edge must be exactly {CHANGED_EDGE!r} — RUN9 の単一介入"
            "エッジは DESIGN_RUN9 §23 で凍結されている（他のエッジへの差し替えは design_revision を"
            f"上げた別 attempt として扱う）, got {si_changed_edge!r}"
        )

    if data["baseline_run"] is not None:
        raise Run9ValidationError(f"baseline_run must be null (RUN9 has no baseline_run), got {data['baseline_run']!r}")

    parent_designs = data["parent_designs"]
    # 全要素が非空 str の非空 list であることを厳密化する（Codex bot レビュー
    # PR #315 第6巡指摘1採用: RUN9_CONTRACT.yaml 側の erratum 是正 — 設計書
    # §6 は5件の parent_designs を宣言するが §23/旧 contract は3件だった。
    # 完全側の §6 へ拡張したため、要素の型・非空も併せて厳密化する）。
    if not isinstance(parent_designs, list) or not parent_designs:
        raise Run9ValidationError("parent_designs must be a non-empty list")
    for i, item in enumerate(parent_designs):
        if not isinstance(item, str) or not item.strip():
            raise Run9ValidationError(f"parent_designs[{i}] must be a non-empty string, got {item!r}")
    # 正典（`PARENT_DESIGNS`）との順序込み厳密一致を強制する（Codex bot
    # レビュー PR #315 第7巡指摘2採用）: 第6巡修正は型・非空のみを検査して
    # おり、`['unrelated']` のような無関係な5件や、正しい5件の順序入れ替え・
    # 一部欠落は素通りしていた。DESIGN_RUN9 §6 を正とする凍結リストへの
    # 完全一致（要素・順序とも）で終端する。
    if tuple(parent_designs) != PARENT_DESIGNS:
        raise Run9ValidationError(
            f"parent_designs must be exactly {list(PARENT_DESIGNS)} (order included) — DESIGN_RUN9 "
            "§6 is the canonical dependency declaration (§23's 3-item Run Contract template is a "
            f"documented erratum; §6 governs), got {parent_designs!r}"
        )

    for name in CONTRACT_PIN_FIELDS:
        _validate_pin_field(name, data[name])

    founder_shas = data["founder_genome_shas"]
    if not isinstance(founder_shas, dict) or set(founder_shas.keys()) != set(CONTRACT_FOUNDER_IDS):
        raise Run9ValidationError(
            f"founder_genome_shas must have exactly keys {list(CONTRACT_FOUNDER_IDS)}, got {founder_shas!r}"
        )
    for founder_id in CONTRACT_FOUNDER_IDS:
        _validate_pin_field(f"founder_genome_shas.{founder_id}", founder_shas[founder_id])

    # 両 founder が PINNED のとき、value（genome_id）の相異を強制する
    # （Codex bot レビュー PR #315 第3巡指摘2採用）: 同一 genome_id は二体の
    # dual-founder 比較の前提そのものが崩れる（R9F-01/R9F-02 は異なる座標
    # から生成される別 Genome のはずで、genome_id が一致するのは改ざんか
    # コピペ誤りしかあり得ない）。片方以下が PINNED の場合は判定しない
    # （PENDING 同士・片方だけ PINNED の状態は正直な未 pin 表現として許容）。
    # 正典 founder 記録との整合（宣言 genome_id が実際に
    # build_founder(domain, founder_id) の再計算値と一致するか）は、domain
    # が必要なため contract load の責務にせず `founder_genome_from_dict()`
    # の builder 照合が担う（役割分担）。
    if all(_is_field_pinned(founder_shas[fid]) for fid in CONTRACT_FOUNDER_IDS):
        values = {fid: founder_shas[fid]["value"] for fid in CONTRACT_FOUNDER_IDS}
        if len(set(values.values())) != len(values):
            raise Run9ValidationError(
                f"founder_genome_shas values must be distinct across founders when both are PINNED, "
                f"got identical value across {list(values.keys())} — the dual-founder comparison "
                f"premise (two distinct Genomes) would be broken: {values!r}"
            )

    claim_strength = data["claim_strength_target"]
    if not isinstance(claim_strength, str) or claim_strength != "C2":
        raise Run9ValidationError(f"claim_strength_target must be 'C2', got {claim_strength!r}")

    # deepcopy（Codex bot レビュー PR #315 第2巡指摘1採用）: `dict(data)` は
    # 浅いコピーのため、ネストした pin 欄 dict（`data["lesson_sha"]` 等）は
    # 呼び出し元の入力オブジェクトとまだ共有されたままだった — 呼び出し元が
    # load 後にそのネスト dict を書き換えると `Run9RunContract.raw` も
    # 一緒に変化してしまう（validate 済みスナップショットのはずが実は
    # 可変共有だった）。deepcopy でこの共有を断つ。
    return Run9RunContract(raw=copy.deepcopy(dict(data)))


def load_run9_contract_from_yaml_text(text: str) -> Run9RunContract:
    # `yaml.safe_load()` ではなく `_StrictYAMLLoader`（重複キー fail-closed
    # 拒否）を使う — 例えば PENDING の `lesson_sha` の後に PINNED の
    # `lesson_sha` を書き足した手編集 contract が、標準 YAML の
    # last-key-wins 解決で検証をすり抜けて READY へ到達し得た
    # （Codex bot レビュー PR #315 第8巡指摘1採用）。
    data = yaml.load(text, Loader=_StrictYAMLLoader)
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

    毎回 `contract.raw` のスナップショットを `load_run9_contract()` で
    再検証してから判定する（Codex bot レビュー PR #315 第2巡指摘1採用）:
    呼び出し元が load 済みの `Run9RunContract.raw`（`Run9RunContract` は
    dataclass だが `raw: Dict` 自体はミュータブル）を直接書き換えて
    `status: "PINNED"` を騙っても、その改変内容は load 時と同じ
    fail-closed 検証（`_validate_pin_field` の PINNED 値整形式強制を含む）
    を再び通過しなければならない — 素通しの pin 判定だけを見ていた旧実装
    では、load 後の直接改変で READY を騙る経路が残っていた。
    """
    revalidated = load_run9_contract(contract.raw)
    pre_run_fields = [n for n in CONTRACT_PIN_FIELDS if n not in CONTRACT_POST_RUN_PIN_FIELDS]
    for name in pre_run_fields:
        if not _is_field_pinned(revalidated.pin_field(name)):
            return "BLOCKED"
    for founder_id in CONTRACT_FOUNDER_IDS:
        if not _is_field_pinned(revalidated.founder_genome_sha(founder_id)):
            return "BLOCKED"
    return "READY"


# ---------------------------------------------------------------------------
# User donor rights manifest 検証（DESIGN_RUN9_REVISION_0.2.md 改訂4）。
# `inputs/rights_manifest.json` が `voice_genesis/foundry/recording_kit/
# user_donor_ledger.json` の転記として過不足なく正しいことを検証する
# loader 側ヘルパ（Codex bot レビュー PR #316 第3巡指摘, 0a4d0cf, 採用: 従来
# テストは件数一致 + ledger 側からの引き当てのみで、manifest 側の重複
# card_id や、ledger に無い card_id の混入を検出できなかった — UC-017 を
# UC-016 の複製に差し替えても、件数17・両方とも ledger 側に実在する
# card_id のため素通りしていた）。attest 後の実運用でも同じ検査が効くよう
# loader 側の関数として実装する（テストはこれを呼ぶだけにする）。
# ---------------------------------------------------------------------------

# RUN9 が対象とする User donor カードの凍結集合（Codex bot レビュー PR #316
# 第6巡指摘採用, be8f448: 変種追撃ではなく User 裁定4（2026-08-24,
# DESIGN_RUN9_REVISION_0.2.md 改訂4）の逐語「UC-001〜017」の機械化漏れの
# 是正）。旧実装は rights_manifest と donor_ledger の card_id 集合が
# **互いに** 一致することしか検証しておらず、両文書が共同で期待集合を
# 定義していたため、両側同時に UC-017 を UC-999 へ差し替えても
# （相互一致は保たれたまま）通過してしまっていた。本定数を外部の凍結
# 参照点として両側と突き合わせることでこの穴を閉じる。将来 intake が
# 増えても RUN9 の donor 集合はこの17枚で凍結する — 変更は
# design_revision を上げる別事案として扱う（本定数のハードコード改変では
# ない）。
USER_DONOR_CARD_IDS: Tuple[str, ...] = (
    "UC-001", "UC-002", "UC-003", "UC-004", "UC-005", "UC-006", "UC-007",
    "UC-008", "UC-009", "UC-010", "UC-011", "UC-012", "UC-013", "UC-014",
    "UC-015", "UC-016", "UC-017",
)


def _require_rights_ledger_field(
    entry: Mapping[str, Any], field: str, *, side: str, card_id: str
) -> Any:
    """`entry[field]` の存在（キー自体の有無）を検証してから値を返す。
    `side` はエラーメッセージへ明記する「どちら側の entry か」のラベル
    （`"rights_manifest"` / `"donor_ledger"`）。値の型・書式は呼び出し元の
    `_require_*` ヘルパが別途検証する — 本関数は「フィールドが存在するか」
    だけを見る（Codex bot レビュー PR #316 第4巡指摘採用: `.get(field)` の
    黙った `None` フォールバックだと、両側から同じ必須フィールドが欠けた
    場合に `None == None` で照合が素通りしてしまっていた）。
    """
    if field not in entry:
        raise Run9ValidationError(
            f"{side}.entries[card_id={card_id!r}] is missing required field {field!r}"
        )
    return entry[field]


def _require_rights_ledger_sha256_hex(value: Any, *, side: str, card_id: str, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_HEX_RE.match(value):
        raise Run9ValidationError(
            f"{side}.entries[card_id={card_id!r}].{field} must be exactly 64 lowercase hex "
            f"characters (sha256 format), got {value!r}"
        )
    return value


def _require_rights_ledger_positive_duration(value: Any, *, side: str, card_id: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Run9ValidationError(
            f"{side}.entries[card_id={card_id!r}].duration_sec must be a number (bool rejected), "
            f"got {value!r}"
        )
    out = float(value)
    if not math.isfinite(out) or out <= 0.0:
        raise Run9ValidationError(
            f"{side}.entries[card_id={card_id!r}].duration_sec must be a positive finite number, "
            f"got {value!r}"
        )
    return out


def verify_rights_manifest_against_ledger(
    rights_manifest: Mapping[str, Any], donor_ledger: Mapping[str, Any]
) -> None:
    """`rights_manifest` の `entries` が `donor_ledger` の `entries` の
    忠実な転記（card_id/source_sha256/sha256/duration_sec）であることを
    検証する。違反は `Run9ValidationError`。検証項目:

    1. `rights_manifest.entries` の `card_id` に重複が無いこと。
    2. `rights_manifest.entries` の card_id 集合が `donor_ledger.entries`
       の card_id 集合と**完全一致**すること（過不足を両方向とも検出 —
       manifest 側に無い ledger の card_id・manifest 側にしか無い
       card_id のいずれも拒否）。
    3. 一致する各 card_id について `source_sha256`/`sha256`/
       `duration_sec` が、**両側で存在 + 整形式であることを照合前に
       強制した上で**、ledger 側の実測値とバイト/値レベルで一致する
       こと（sha 系は64hex str、duration_sec は bool でない正の有限
       数値）。存在・整形式のいずれかが欠けた側は、比較を試みる前に
       `Run9ValidationError` で拒否する（Codex bot レビュー PR #316
       第4巡指摘採用: 従来は `entry.get(field)` 同士の等値比較だけの
       ため、rights entry と ledger entry の両方から同じ必須
       フィールドが欠落すると `None == None` が真になり、両側欠落を
       検出できなかった）。
    4. `rights_manifest.schema == "run9-user-donor-rights/1.0"` /
       `donor_ledger.schema == "user-donor-ledger/0.1"`（欠落・別値・
       非 str はいずれも拒否 — Codex bot レビュー PR #316 第5巡指摘A
       採用: 意味論を理解しない版の文書を attest 経由で
       `anchor_hashes.user` へ正典束縛し得るため、版の取り違えは
       card_id/値の一致以前に拒否する）。
    5. `donor_ledger.entries` の `card_id` にも重複が無いこと（Codex bot
       レビュー PR #316 第5巡指摘B採用: 第3巡は rights 側のみ重複拒否
       しており、ledger 側は `ledger_by_id[card_id] = entry` の
       last-entry-wins で曖昧な ledger を黙って解決していた非対称が
       残っていた）。
    6. rights_manifest・donor_ledger **双方**の card_id 集合が、外部の
       凍結参照点 `USER_DONOR_CARD_IDS`（UC-001〜UC-017 の17枚、User 裁定
       4・2026-08-24 の逐語固定）と完全一致すること（Codex bot レビュー
       PR #316 第6巡指摘採用, be8f448: 変種追撃ではなく User 裁定4の
       機械化漏れの是正 — 従来は rights/ledger の**相互**一致しか
       検証しておらず、両文書が期待集合を共同定義していたため、両側
       同時に UC-017 を UC-999 のような別 ID へ差し替えても相互一致は
       保たれたまま通過してしまっていた）。将来 intake が増えても RUN9
       の donor 集合はこの17枚で凍結する — 変更は design_revision を
       上げる別事案として扱う。

    rights 検証器の堅牢化ファミリー（PR #316 第3〜6巡: card_id 完全一致・
    両側存在+整形式・schema 版・ledger 側重複拒否・期待集合の凍結）は
    本巡で全数掃討・終端する。以降に見つかる同型変種（本ファミリーが
    扱う対称性の範囲外の新しい欠陥クラス）は、都度追撃せず境界宣言で
    扱う。
    """
    rights_schema = rights_manifest.get("schema")
    if not isinstance(rights_schema, str) or rights_schema != "run9-user-donor-rights/1.0":
        raise Run9ValidationError(
            "rights_manifest.schema must be exactly 'run9-user-donor-rights/1.0', got "
            f"{rights_schema!r} (a document declaring a different or missing schema version "
            "must not be treated as this contract's rights manifest, since anchor_hashes.user "
            "binding depends on this schema's exact semantics)"
        )
    ledger_schema = donor_ledger.get("schema")
    if not isinstance(ledger_schema, str) or ledger_schema != "user-donor-ledger/0.1":
        raise Run9ValidationError(
            "donor_ledger.schema must be exactly 'user-donor-ledger/0.1', got "
            f"{ledger_schema!r}"
        )

    rights_entries_raw = rights_manifest.get("entries")
    if not isinstance(rights_entries_raw, list):
        raise Run9ValidationError(
            f"rights_manifest.entries must be a list, got {type(rights_entries_raw).__name__}"
        )
    ledger_entries_raw = donor_ledger.get("entries")
    if not isinstance(ledger_entries_raw, list):
        raise Run9ValidationError(
            f"donor_ledger.entries must be a list, got {type(ledger_entries_raw).__name__}"
        )

    rights_card_ids: List[str] = []
    rights_by_id: Dict[str, Mapping[str, Any]] = {}
    for i, entry in enumerate(rights_entries_raw):
        if not isinstance(entry, dict):
            raise Run9ValidationError(f"rights_manifest.entries[{i}] must be an object")
        card_id = entry.get("card_id")
        if not isinstance(card_id, str) or not card_id:
            raise Run9ValidationError(
                f"rights_manifest.entries[{i}].card_id must be a non-empty string, got {card_id!r}"
            )
        rights_card_ids.append(card_id)
        rights_by_id[card_id] = entry

    # item 1: rights_manifest 側の card_id 重複拒否（len(ids) == len(set(ids))）。
    if len(rights_card_ids) != len(set(rights_card_ids)):
        seen: set = set()
        duplicates = sorted({c for c in rights_card_ids if c in seen or seen.add(c)})
        raise Run9ValidationError(
            f"rights_manifest.entries has duplicate card_id value(s): {duplicates} "
            "(each donor card must appear exactly once)"
        )

    ledger_card_ids: List[str] = []
    ledger_by_id: Dict[str, Mapping[str, Any]] = {}
    for i, entry in enumerate(ledger_entries_raw):
        if not isinstance(entry, dict):
            raise Run9ValidationError(f"donor_ledger.entries[{i}] must be an object")
        card_id = entry.get("card_id")
        if not isinstance(card_id, str) or not card_id:
            raise Run9ValidationError(
                f"donor_ledger.entries[{i}].card_id must be a non-empty string, got {card_id!r}"
            )
        ledger_card_ids.append(card_id)
        ledger_by_id[card_id] = entry

    # item 5: donor_ledger 側の card_id 重複拒否（rights 側と対称。旧実装は
    # `ledger_by_id[card_id] = entry` の last-entry-wins で曖昧 ledger を
    # 黙って解決していた — Codex bot レビュー PR #316 第5巡指摘B）。
    if len(ledger_card_ids) != len(set(ledger_card_ids)):
        seen_ledger: set = set()
        ledger_duplicates = sorted(
            {c for c in ledger_card_ids if c in seen_ledger or seen_ledger.add(c)}
        )
        raise Run9ValidationError(
            f"donor_ledger.entries has duplicate card_id value(s): {ledger_duplicates} "
            "(an ambiguous ledger must not be silently resolved via last-entry-wins)"
        )

    # item 2: card_id 集合の完全一致（過不足を両方向とも検出）。
    rights_id_set = set(rights_by_id.keys())
    ledger_id_set = set(ledger_by_id.keys())
    missing_from_rights = sorted(ledger_id_set - rights_id_set)
    extra_in_rights = sorted(rights_id_set - ledger_id_set)
    if missing_from_rights or extra_in_rights:
        raise Run9ValidationError(
            "rights_manifest.entries card_id set does not exactly match donor_ledger.entries "
            f"card_id set — missing_from_rights={missing_from_rights!r} "
            f"extra_in_rights={extra_in_rights!r}"
        )

    # item 6: 両側とも外部の凍結参照点 USER_DONOR_CARD_IDS と完全一致する
    # こと（item 2 の相互一致だけでは、両側が同時に同じ ID へ差し替わる
    # 攻撃を検出できない — Codex bot レビュー PR #316 第6巡指摘）。
    expected_id_set = set(USER_DONOR_CARD_IDS)
    rights_unexpected = sorted(rights_id_set - expected_id_set)
    rights_absent = sorted(expected_id_set - rights_id_set)
    if rights_unexpected or rights_absent:
        raise Run9ValidationError(
            "rights_manifest.entries card_id set does not exactly match the frozen "
            f"USER_DONOR_CARD_IDS set (UC-001..UC-017) — unexpected={rights_unexpected!r} "
            f"absent={rights_absent!r}"
        )
    ledger_unexpected = sorted(ledger_id_set - expected_id_set)
    ledger_absent = sorted(expected_id_set - ledger_id_set)
    if ledger_unexpected or ledger_absent:
        raise Run9ValidationError(
            "donor_ledger.entries card_id set does not exactly match the frozen "
            f"USER_DONOR_CARD_IDS set (UC-001..UC-017) — unexpected={ledger_unexpected!r} "
            f"absent={ledger_absent!r}"
        )

    # item 3: 一致する card_id ごとの値照合。存在 + 整形式を両側で強制して
    # から比較する（`None == None` すり抜けの防止）。
    for card_id, rights_entry in rights_by_id.items():
        ledger_entry = ledger_by_id[card_id]

        for field in ("source_sha256", "sha256"):
            rights_raw = _require_rights_ledger_field(
                rights_entry, field, side="rights_manifest", card_id=card_id
            )
            ledger_raw = _require_rights_ledger_field(
                ledger_entry, field, side="donor_ledger", card_id=card_id
            )
            rights_value = _require_rights_ledger_sha256_hex(
                rights_raw, side="rights_manifest", card_id=card_id, field=field
            )
            ledger_value = _require_rights_ledger_sha256_hex(
                ledger_raw, side="donor_ledger", card_id=card_id, field=field
            )
            if rights_value != ledger_value:
                raise Run9ValidationError(
                    f"rights_manifest.entries[card_id={card_id!r}].{field} does not match "
                    f"donor_ledger: rights={rights_value!r} ledger={ledger_value!r}"
                )

        rights_duration_raw = _require_rights_ledger_field(
            rights_entry, "duration_sec", side="rights_manifest", card_id=card_id
        )
        ledger_duration_raw = _require_rights_ledger_field(
            ledger_entry, "duration_sec", side="donor_ledger", card_id=card_id
        )
        rights_duration = _require_rights_ledger_positive_duration(
            rights_duration_raw, side="rights_manifest", card_id=card_id
        )
        ledger_duration = _require_rights_ledger_positive_duration(
            ledger_duration_raw, side="donor_ledger", card_id=card_id
        )
        if rights_duration != ledger_duration:
            raise Run9ValidationError(
                f"rights_manifest.entries[card_id={card_id!r}].duration_sec does not match "
                f"donor_ledger: rights={rights_duration!r} ledger={ledger_duration!r}"
            )
