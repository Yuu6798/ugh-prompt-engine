"""run9_controlprofile.py — RUN9 Phase 3: Performance ControlProfile 基盤
（DESIGN_RUN9_REVISION_0.3.md 改訂A「書き込み境界」節、User 外部レビュー
PR #317 P1-1 の実装続き）。

`run9_schema.py` の sibling import 流儀（`_THIS_DIR` を `sys.path` へ挿入）を
踏襲し、本モジュールは `run9_schema` を import する（run9_schema 自身の
docstring が予告していた「現時点では単一ファイル」からの最初の分岐）。

ここで実装する ControlProfile は、rev 0.2 改訂1が導入した「Founder ごとの
versioned Performance ControlProfile」の**書き込み境界を機械強制する具体
データ構造**である。中心規律は2つ:

1. **partitions は `trait_control`/`technique_control` の2節のみ**
   （`run9_schema.STATE_PARTITIONS` の3分割のうち `IDENTITY_STATE` を
   意図的に欠落させる）。IDENTITY_STATE は profile のスキーマ上
   **存在しない** — 書込どころか表現も不能にすることで、
   `run9_schema.IMMUTABLE_STATE_PARTITIONS` が定める不変条件を型レベルで
   強制する（「書けない」を「妥当性検証で拒否する」より強い保証にする）。
2. **`derive_profile()` は `run9_schema.validate_branch_write()` /
   `BRANCH_WRITABLE_PARTITIONS` を必ず経由する** — 枝ごとの書込許可
   policy（`inputs/branch_write_policy.json` と同期済みの定数）を
   ControlProfile の実際の更新経路へ配線する。

`Run9ProfileLedger` は VG-E0 `voice_genesis/evolution/ledger.py` の
append-only 台帳の意味論（tmp→fsync→`os.link` 排他 create・バイト同一
冪等・差異は conflict・重複キー拒否読込・symlink escape guard・
ファイル名↔内容の自己申告 ID 束縛）を run-local に踏襲する。genome ledger
との違いは、`parents`（genome_id のタプル）の代わりに単一の
`parent_revision`（親 profile の **revision ラベル**、例 `"r0"`）で親を
参照する点 — revision ラベルは同一 `voice_id` 内で一意（Founder ごとに
`r0`/`replay`/`r_sham`/`r_practice`/`r_taught` の最大5件）であるため、
`(voice_id, revision)` の組で台帳を検索すれば実在検証ができる。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import tempfile
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Mapping, NamedTuple, Optional, Tuple

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))
import run9_schema as _rs  # noqa: E402

# ---------------------------------------------------------------------------
# 共通定数
# ---------------------------------------------------------------------------

SCHEMA_CONTROL_PROFILE = "run9-control-profile/1.0"

_PROFILE_ID_LEN = 16
_PROFILE_ID_RE = re.compile(rf"^[0-9a-f]{{{_PROFILE_ID_LEN}}}$")

# profile.partitions が持てるキーの完全な集合（IDENTITY_STATE は含まない
# — STATE_PARTITIONS 3分割のうち意図的に2つだけを profile schema へ写す）。
PROFILE_PARTITION_KEYS: Tuple[str, str] = ("trait_control", "technique_control")

# profile partition キー（小文字・snake_case）→ run9_schema の
# state partition 名（大文字）への対応表。`validate_branch_write()` へ
# 渡す際にこの対応表を経由する。
_PARTITION_KEY_TO_STATE_PARTITION: Dict[str, str] = {
    "trait_control": "TRAIT_CONTROL",
    "technique_control": "TECHNIQUE_CONTROL",
}

# r0（出生中立）は BRANCH_REVISIONS のどの枝からも導出されない特別な
# revision ラベル。DESIGN_RUN9_REVISION_0.3.md 改訂A: 「r0 は in-place
# 更新しない」「各枝は独立 Revision として保存する」の起点。
NEUTRAL_REVISION = "r0"

# 有効な revision ラベルの全集合（r0 + BRANCH_REVISIONS が定める4つの
# 枝別 revision）。`run9_schema.BRANCH_REVISIONS` が正本 — ここでは
# その値を展開して平坦なタプルにするだけで、値そのものは重複定義しない。
VALID_REVISIONS: Tuple[str, ...] = (NEUTRAL_REVISION,) + tuple(
    v if isinstance(v, str) else vv
    for v in _rs.BRANCH_REVISIONS.values()
    for vv in ([v] if isinstance(v, str) else v.values())
)


def _valid_revisions_for_branch(branch: Optional[str]) -> FrozenSet[str]:
    """`branch` が正当に名乗れる `revision` 値の集合を
    `run9_schema.BRANCH_REVISIONS`（単一の正本）から導出する（Codex bot
    レビュー PR #318 第1巡 Fix 3 採用）。`branch is None` は r0（出生
    中立・枝分岐前）専用の扱い — CONTROL の一部としてではなく、r0 自身の
    ための専用センチネルとして schema 上明確化する（r0 は「無介入枝」
    ではなく「まだどの枝にも分岐していない起点」であり、`CONTROL` 枝の
    revision 集合 `{"replay", "r_sham"}` とは意味論が異なる）。
    """
    if branch is None:
        return frozenset({NEUTRAL_REVISION})
    if branch not in _rs.BRANCH_REVISIONS:
        raise Run9ControlProfileError(
            f"branch must be null or one of {sorted(_rs.BRANCH_REVISIONS)}, got {branch!r}"
        )
    mapped = _rs.BRANCH_REVISIONS[branch]
    if isinstance(mapped, str):
        return frozenset({mapped})
    return frozenset(mapped.values())


_PROFILE_TOP_LEVEL_KEYS: FrozenSet[str] = frozenset({
    "schema", "voice_id", "branch", "revision", "parent_revision", "partitions", "profile_id",
})


class Run9ControlProfileError(ValueError):
    """Run9ControlProfile / Run9ProfileLedger の構築・書込み時の型・構造
    不正。`run9_schema.Run9ValidationError` とは意図的に別の例外階層に
    する（本モジュールは run9_schema の関数を呼ぶ際は
    `run9_schema.Run9ValidationError` をそのまま伝播させる — 呼び出し元が
    「境界検証の失敗」と「profile 固有の失敗」を区別できるようにする）。"""


class Run9ProfileLedgerConflictError(Run9ControlProfileError):
    """append-only 台帳: 既存 profile_id に異なる内容で上書きしようとした
    （`voice_genesis/evolution/ledger.py` `LedgerConflictError` と同型）。"""


# ---------------------------------------------------------------------------
# Run9ControlProfile
# ---------------------------------------------------------------------------


def _reject_non_finite(value: Any, *, path: str) -> None:
    """`value`（JSON 互換の任意構造 — dict/list/str/int/float/bool/None）
    の中に NaN/inf を含む float が無いことを再帰的に検証する（Codex bot
    レビュー PR #318 第2巡 Fix 8 採用）: partition 値は自由形の
    trait/technique パラメータ（例 `{"breathiness": 0.1}`）を許容するが、
    Python の `json` モジュールは既定で NaN/Infinity/-Infinity を寛容に
    受け付けてしまう（`allow_nan=True` が既定）。これらが profile_id の
    正規形入力や距離計算へ紛れ込むと、決定論性は保たれても値そのものが
    JSON 標準の外側にある汚染源になる。bool は int のサブクラスだが
    NaN/inf になり得ないため素通しする。
    """
    if isinstance(value, bool):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise Run9ControlProfileError(f"{path}: non-finite float value rejected (NaN/inf), got {value!r}")
        return
    if isinstance(value, dict):
        for key, sub in value.items():
            _reject_non_finite(sub, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, sub in enumerate(value):
            _reject_non_finite(sub, path=f"{path}[{index}]")
        return
    # int/str/None はそのまま許容する（int は常に有限。json.loads() が
    # NaN/inf を生成するのは float リテラルとしてのみなので int 経路には
    # 現れない）。


def _deep_freeze(value: Any) -> Any:
    """JSON 互換の任意構造（dict/list/str/int/float/bool/None — `_reject_
    non_finite()` と同じ入力形状の前提）を再帰的に不変化する（Codex bot
    レビュー PR #318 第4巡 Fix 14 採用）: 旧 `__post_init__` は partitions
    の外側 dict にのみ `types.MappingProxyType` を適用しており、ネストされた
    dict/list はそのまま mutable な素の構造として残っていた（`profile.
    partitions["trait_control"]["nested"]["x"] = ...` のような深い直接
    変異が型レベルで防げていなかった）。dict は再帰的に `MappingProxyType`
    へ、list は再帰的に `tuple` へ変換する（tuple の要素自体も再帰的に
    凍結するため、`([{"x": 1}],)` のようにさらに dict/list を含む要素も
    末端まで不変化される）。str/int/float/bool/None はそもそも immutable
    なのでそのまま返す — 新しいコンテナを都度生成するため、元の入力
    コンテナとの参照共有（エイリアス）もこの変換一回で完全に断たれる
    （`copy.deepcopy()` の事前呼び出しは不要になった）。"""
    if isinstance(value, dict):
        return types.MappingProxyType({k: _deep_freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(v) for v in value)
    return value


def _deep_thaw(value: Any) -> Any:
    """`_deep_freeze()` の逆変換: 凍結済み構造（`MappingProxyType`/`tuple`
    の再帰ネスト）を再帰的に plain な dict/list へ変換して返す（Codex bot
    レビュー PR #318 第4巡 Fix 14 採用）。`to_dict()`（および `derive_
    profile()` が親の partitions を書き換え用に取り込む経路）が呼び出し
    元へ渡す表現は、呼び出し元がその後 in-place で変異させても profile
    本体（または凍結済み parent）へは一切波及してはならない — 旧 `to_dict()`
    の `dict(self.partitions[k])` は外側 1 階層だけをコピーする浅いコピー
    だったため、`profile.to_dict()["partitions"]["trait_control"]["nested"]
    ["x"] = ...` のような深い変異が内部の凍結構造（の中のさらにネストされた
    可変オブジェクト）へ波及し得た。`isinstance(value, MappingProxyType)`
    は `isinstance(value, dict)` では捕捉できない（`MappingProxyType` は
    `dict` のサブクラスではない）ため明示的に判定する。"""
    if isinstance(value, types.MappingProxyType):
        return {k: _deep_thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_deep_thaw(v) for v in value]
    return value


def _validate_partitions_shape(partitions: Any) -> Dict[str, Dict[str, Any]]:
    """`partitions` が `{"trait_control": {...}, "technique_control": {...}}`
    という完全な2キー構造であることを検証する。`IDENTITY_STATE`/
    `identity_state` に相当するキーが1つでも現れたら fail-closed 拒否する
    （profile schema からの IDENTITY_STATE 排除を実装レベルで強制する
    唯一の関門）。
    """
    if not isinstance(partitions, dict):
        raise Run9ControlProfileError(f"partitions must be an object, got {type(partitions).__name__}")
    unknown = set(partitions.keys()) - set(PROFILE_PARTITION_KEYS)
    if unknown:
        raise Run9ControlProfileError(
            f"partitions has unknown key(s): {sorted(unknown)} — only "
            f"{list(PROFILE_PARTITION_KEYS)} are representable in a ControlProfile (IDENTITY_STATE "
            "is structurally absent, not merely write-protected)"
        )
    missing = set(PROFILE_PARTITION_KEYS) - set(partitions.keys())
    if missing:
        raise Run9ControlProfileError(f"partitions missing required key(s): {sorted(missing)}")
    validated: Dict[str, Dict[str, Any]] = {}
    for key in PROFILE_PARTITION_KEYS:
        value = partitions[key]
        if not isinstance(value, dict):
            raise Run9ControlProfileError(
                f"partitions.{key} must be an object, got {type(value).__name__}"
            )
        _reject_non_finite(value, path=f"partitions.{key}")
        validated[key] = dict(value)
    return validated


def _compute_profile_id(
    *,
    voice_id: str,
    branch: Optional[str],
    revision: str,
    parent_revision: Optional[str],
    partitions: Mapping[str, Mapping[str, Any]],
) -> str:
    """profile_id = sha256(正規形JSON)[:16]（`run9_schema._compute_founder_
    genome_id()` と同一パターン — `_canonical_json()` を共有する）。"""
    payload = {
        "schema": SCHEMA_CONTROL_PROFILE,
        "voice_id": voice_id,
        "branch": branch,
        "revision": revision,
        "parent_revision": parent_revision,
        "partitions": {k: dict(partitions[k]) for k in PROFILE_PARTITION_KEYS},
    }
    canonical = _rs._canonical_json(payload)  # noqa: SLF001 - sibling module, see module docstring
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:_PROFILE_ID_LEN]


@dataclass(frozen=True)
class Run9ControlProfile:
    """`run9-control-profile/1.0`。`profile_id` は
    `_compute_profile_id()` の再計算値以外を持てない（構築時にのみ導出
    され、公開コンストラクタから直接指定できない — `founder_genome_id`
    と同じ改ざん耐性の考え方）。

    `partitions` の構築時凍結（Codex bot レビュー PR #318 第3巡 Fix 11
    項目2 採用、`Run9IdentityDomain.__post_init__`
    — run9_schema.py — と同型）: `dataclass(frozen=True)` はトップレベル
    属性の再代入だけを禁止し、属性が指す nested dict（`partitions["trait_
    control"]` 等）自体は素の mutable dict のままだったため、
    `build_neutral_profile()` の返り値を `profile.partitions["trait_
    control"]["injected"] = ...` のように構築後に書き換えても型レベルでは
    防げていなかった（`derive_profile()` の parent 検証が `revision ==
    "r0"` というラベルだけを見ていた旧実装と組み合わさると、汚染された
    nested dict が有効な r0 として derive へ渡り、汚染内容が子へコピー
    されてしまう）。`__post_init__` で渡された partitions を
    `types.MappingProxyType` で二段階（partitions 自体 + 各 partition の
    中身）不変ビュー化する — 呼び出し元が保持する元の dict を後から
    書き換えても profile 自身の内部状態は影響を受けず、`profile.
    partitions[...][...] = ...` のような直接変異も `TypeError` で拒否
    される。Fix 11 項目1（`derive_profile()` の正準全形検証）と対になる
    防御的二重検証 — 項目1は「derive 時点で parent の中身を検査する」
    経路、本凍結は「そもそも構築後に中身を変えさせない」経路で、どちらか
    片方が抜けてももう片方が汚染を止める。

    **再帰凍結**（Codex bot レビュー PR #318 第4巡 Fix 14 採用、上記の
    是正）: 旧実装は `MappingProxyType` をパーティションの外側 dict にしか
    適用しておらず、その中にネストされた dict/list（例
    `partitions["trait_control"]["nested"]`）は依然 mutable な素の構造の
    ままだったため、`profile.partitions["trait_control"]["nested"]["x"]
    = ...` のような深い直接変異が型レベルで防げていなかった（`profile_id`
    はこの変異を検知できず、既に発行済みの digest のまま内容だけが
    ずれてしまう）。`_deep_freeze()`（本モジュール、`_reject_non_finite()`
    と同じ「JSON 互換の任意構造」を前提とする再帰関数）を使い、dict は
    再帰的に `MappingProxyType`、list は再帰的に `tuple` へ変換する —
    末端の value まで含め、あらゆる深さの変異が `TypeError`（dict の
    item 代入）または構造的に不可能（tuple は要素代入自体を持たない）に
    なる。`_deep_freeze()` は再帰の過程で新しいコンテナを都度生成する
    ため、これ単体で元入力とのエイリアスも完全に断たれる（旧実装が別途
    行っていた `copy.deepcopy()` の事前呼び出しは不要になった）。"""

    schema: str
    voice_id: str
    branch: Optional[str]
    revision: str
    parent_revision: Optional[str]
    partitions: Mapping[str, Mapping[str, Any]]
    profile_id: str

    def __post_init__(self) -> None:
        # frozen dataclass では `self.x = ...` が使えないため
        # `object.__setattr__` で直接代入する（run9_schema.py
        # `Run9IdentityDomain.__post_init__` と同じ回避手段）。`_deep_
        # freeze()` が dict(self.partitions[key]) の中身を再帰的に不変化
        # する（Fix 14 — docstring 参照。`dict(...)` で外側だけ先に plain
        # dict 化しておくのは、self.partitions[key] 自体が既に凍結済み
        # object（別 profile から取り出した partitions を再利用する経路）
        # であっても `_deep_freeze()` の dict 分岐へ確実に載せるため）。
        frozen: Dict[str, Mapping[str, Any]] = {}
        for key in PROFILE_PARTITION_KEYS:
            frozen[key] = _deep_freeze(dict(self.partitions[key]))
        object.__setattr__(self, "partitions", types.MappingProxyType(frozen))

    def to_dict(self) -> Dict[str, Any]:
        # `_deep_thaw()` で内部の凍結構造（MappingProxyType/tuple の再帰
        # ネスト）を plain な dict/list へ深く変換する（Fix 14 —
        # docstring 参照）: 旧実装の `dict(self.partitions[k])` は外側
        # 1階層だけの浅いコピーだったため、返り値のネストした中身を
        # 呼び出し元が変異させると profile 本体の凍結構造（の中の
        # さらにネストされた可変オブジェクト）へ波及し得た。
        return {
            "schema": self.schema,
            "voice_id": self.voice_id,
            "branch": self.branch,
            "revision": self.revision,
            "parent_revision": self.parent_revision,
            "partitions": {k: _deep_thaw(self.partitions[k]) for k in PROFILE_PARTITION_KEYS},
            "profile_id": self.profile_id,
        }


def _require_founder_voice_id(voice_id: Any) -> str:
    if voice_id not in _rs.CONTRACT_FOUNDER_IDS:
        raise Run9ControlProfileError(
            f"voice_id must be one of {list(_rs.CONTRACT_FOUNDER_IDS)}, got {voice_id!r}"
        )
    return voice_id


def build_neutral_profile(voice_id: str) -> Run9ControlProfile:
    """出生中立の r0 profile を決定論的に生成する唯一の経路。全 partition
    は恒等値（空 dict）— PoR §10「r0 は in-place 更新しない」の起点として、
    r0 自体は一切の制御パラメータを持たない中立状態を表す。

    C1（`ZERO_CONTROLPROFILE_SHAM`）が「中立 profile の付与のみ・学習
    step ゼロ」を表すのは、この neutral profile を CONTROL 枝の
    `derive_profile()` 呼び出しへ**そのまま**（updates 空のまま）渡す
    ことで表現する — 中立 profile を複製するだけで新しい revision
    （`r_sham`）を刻む、という設計。

    `voice_id` が同じであれば、何度呼び出しても bit 単位で同一の
    `Run9ControlProfile.to_dict()` を返す（乱数・時刻等の非決定要素を
    一切含まない）。
    """
    _require_founder_voice_id(voice_id)
    partitions: Dict[str, Dict[str, Any]] = {k: {} for k in PROFILE_PARTITION_KEYS}
    profile_id = _compute_profile_id(
        voice_id=voice_id, branch=None, revision=NEUTRAL_REVISION, parent_revision=None,
        partitions=partitions,
    )
    return Run9ControlProfile(
        schema=SCHEMA_CONTROL_PROFILE, voice_id=voice_id, branch=None, revision=NEUTRAL_REVISION,
        parent_revision=None, partitions=partitions, profile_id=profile_id,
    )


def _resolve_derived_revision(branch: str, control_condition: Optional[str]) -> str:
    if branch == _rs.CONTROL_BRANCH:
        if control_condition not in _rs.CONTROL_CONDITIONS:
            raise Run9ControlProfileError(
                f"control_condition must be one of {list(_rs.CONTROL_CONDITIONS)} when branch is "
                f"{_rs.CONTROL_BRANCH!r}, got {control_condition!r}"
            )
        return _rs.BRANCH_REVISIONS[_rs.CONTROL_BRANCH][control_condition]
    if branch not in _rs.BRANCH_WRITABLE_PARTITIONS:
        raise Run9ControlProfileError(
            f"branch must be one of {sorted(_rs.BRANCH_WRITABLE_PARTITIONS)}, got {branch!r}"
        )
    if control_condition is not None:
        raise Run9ControlProfileError(
            f"control_condition must be None when branch is {branch!r} (only {_rs.CONTROL_BRANCH!r} "
            f"has sub-conditions), got {control_condition!r}"
        )
    revision = _rs.BRANCH_REVISIONS[branch]
    assert isinstance(revision, str)  # PRACTICE_FROM_AUDIO/TRANSFER_TECHNIQUE は単一文字列
    return revision


def derive_profile(
    parent: Run9ControlProfile,
    branch: str,
    updates: Mapping[str, Mapping[str, Any]],
    *,
    control_condition: Optional[str] = None,
) -> Run9ControlProfile:
    """`parent` から `branch` 上で `updates` を適用した子 profile を導出
    する ControlProfile 更新の**唯一の公開経路**。

    書き込み境界の機械強制（User 外部レビュー PR #317 P1-1）:
    `run9_schema.validate_branch_write(branch, partition)` を、
    `updates` に含まれる各 partition キーについて必ず呼ぶ。EDUCATION
    （`TRANSFER_TECHNIQUE`）が `trait_control` を更新しようとした場合や、
    `CONTROL` が非空の `updates` を渡された場合は fail-closed で
    `run9_schema.Run9ValidationError`（境界違反）または
    `Run9ControlProfileError`（構造違反）を送出する。

    `branch == "CONTROL"` の場合は `control_condition`
    （`run9_schema.CONTROL_CONDITIONS` のいずれか）を**必須**とし、
    `updates` は空でなければならない（CONTROL の writable 集合は空 —
    書込許可が無い以上、何かを書こうとする呼び出し自体を構造的に拒否する。
    `validate_branch_write()` 単体でも各キーは拒否されるが、CONTROL は
    そもそも1件も書けないことを呼び出し時点で明示的に伝える）。

    **親は必ず r0（出生中立の正典）でなければならない**（Codex bot レビュー
    PR #318 第1巡 Fix 1 採用）: PoR §4 の「二体の Founder は出生後、同一の
    r0 から三経路（CONTROL / PRACTICE / EDUCATION）へ分岐する」という
    all-arms-from-r0 フローを機械強制する。`parent.revision != "r0"` の
    呼び出し（例: `r_practice` を親に `TRANSFER_TECHNIQUE` を導出する —
    稽古の結果を教育の出発点にする、または `r_taught`/`r_sham`/`replay`
    同士をさらに繋ぐ等）は枝汚染（cross-arm contamination）として
    fail-closed で拒否する。

    **parent は revision ラベルだけでなく正準全形で検証する**（Codex bot
    レビュー PR #318 第3巡 Fix 11 項目1 採用、Fix 1 の是正）: `Run9Control
    Profile` は浅い frozen dataclass のため、`revision=="r0"` というラベル
    だけを見る検証は「`build_neutral_profile()` の返り値の nested
    partitions dict を構築後に書き換えても `revision` フィールド自体は
    `"r0"` のまま」という抜け道を防げない（稽古様の trait 状態が教育結果へ
    混入し得る）。本関数は revision ラベルの一致に加え、`parent.partitions`
    が `build_neutral_profile(parent.voice_id)` の返す正準 neutral 全形と
    深い一致であること、かつ `parent.profile_id` もその正準値と一致する
    ことを検証する（`Run9ControlProfile.__post_init__` の partitions 凍結
    — Fix 11 項目2 — により通常はこの経路自体が構造的に塞がれるが、
    frozen dataclass を直接構築する経路（テストや将来の呼び出し）は
    `__post_init__` の凍結を経由しつつも、凍結**前**の入力 dict 自体を
    汚染して渡すことは可能であるため、本検証は独立した第二関門として残す
    — 「そもそも書き換えさせない」（Fix 11-2）と「書き換わっていないか
    derive 時点で確かめる」（Fix 11-1）の二重防御）。

    **境界宣言（本 schema の対象外）**: 将来「同一枝内での profile
    版重ね」（例: PRACTICE_FROM_AUDIO の反復学習で `r_practice` からさらに
    次の `r_practice` 版を導出する）が必要になった場合、その対応は本
    schema・本関数の対象外とする。r0-only 制約はあくまで「異なる枝の間」
    の汚染を防ぐものであり、「同一枝内の反復」を予め設計していない —
    必要になった時点で VG-L0 ハーネス実装時に別途設計する（例えば
    revision 語彙の拡張、または枝内の世代番号を別フィールドとして持たせる
    等、複数の設計選択肢があり、ここで先取りして決め打ちしない）。
    """
    if not isinstance(parent, Run9ControlProfile):
        raise Run9ControlProfileError(f"parent must be a Run9ControlProfile, got {type(parent).__name__}")
    if not isinstance(updates, dict):
        raise Run9ControlProfileError(f"updates must be an object, got {type(updates).__name__}")
    if parent.revision != NEUTRAL_REVISION:
        raise Run9ControlProfileError(
            f"derive_profile() requires parent.revision == {NEUTRAL_REVISION!r} (birth-neutral "
            f"canonical origin), got parent.revision={parent.revision!r} (parent.branch="
            f"{parent.branch!r}) — 全枝は r0 から独立分岐する（PoR §4）。r_practice/r_taught/"
            "replay/r_sham のいずれかを親として別の枝を導出する cross-arm contamination は "
            "拒否する（同一枝内の版重ねは本 schema の対象外 — docstring の境界宣言を参照）"
        )

    # revision ラベルだけでなく正準全形で parent を検証する（Fix 11 項目1
    # — docstring 参照）。voice_id は Run9ControlProfile 型検査は既に
    # 通過済みだが値の妥当性は未検証のため、build_neutral_profile() が
    # _require_founder_voice_id() 経由でここでも改めて検証する
    # （不正な voice_id の parent は「canonical と一致しない」以前に
    # ここで Run9ControlProfileError になる）。
    canonical_r0 = build_neutral_profile(parent.voice_id)
    if parent.partitions != canonical_r0.partitions or parent.profile_id != canonical_r0.profile_id:
        raise Run9ControlProfileError(
            "derive_profile() requires parent to be byte-for-byte the canonical birth-neutral r0 "
            f"profile for voice_id={parent.voice_id!r} (build_neutral_profile()'s exact output) — "
            "parent.revision reads 'r0' but parent.partitions and/or parent.profile_id do not "
            "deep-equal the canonical neutral form (contents were mutated after construction, or a "
            "hand-assembled/tampered document was passed directly as parent) — 汚染された trait/"
            "technique 状態を r0 のふりをして子へコピーさせる経路を拒否する（Fix 11 項目1）"
        )

    revision = _resolve_derived_revision(branch, control_condition)

    if branch == _rs.CONTROL_BRANCH and updates:
        raise Run9ControlProfileError(
            f"CONTROL branch has an empty writable partition set (run9_schema."
            f"BRANCH_WRITABLE_PARTITIONS['{_rs.CONTROL_BRANCH}'] == ()) — updates must be empty, "
            f"got non-empty updates with key(s) {sorted(updates.keys())}"
        )

    unknown_update_keys = set(updates.keys()) - set(PROFILE_PARTITION_KEYS)
    if unknown_update_keys:
        raise Run9ControlProfileError(
            f"updates has unknown partition key(s): {sorted(unknown_update_keys)} — only "
            f"{list(PROFILE_PARTITION_KEYS)} are representable"
        )

    for partition_key in updates:
        state_partition = _PARTITION_KEY_TO_STATE_PARTITION[partition_key]
        # ここで run9_schema.Run9ValidationError が飛べばそのまま呼び出し
        # 元へ伝播する（境界違反は run9_schema の例外型のまま返す — この
        # モジュール固有の Run9ControlProfileError へラップし直さない）。
        _rs.validate_branch_write(branch, state_partition)
        if not isinstance(updates[partition_key], dict):
            raise Run9ControlProfileError(
                f"updates[{partition_key!r}] must be an object, got "
                f"{type(updates[partition_key]).__name__}"
            )
        # 非有限値の拒否（Codex bot レビュー PR #318 第2巡 Fix 8 採用）:
        # updates はここでしか検証されない自由形の値であり、
        # `_validate_partitions_shape()`（from_dict 側の関門）を経由しない
        # ため、builder 側にも同じ関門を独立に配線する。
        _reject_non_finite(updates[partition_key], path=f"updates.{partition_key}")

    # `_deep_thaw()`（Fix 14）で parent の凍結済み partitions
    # （MappingProxyType/tuple の再帰ネスト）を plain な dict/list へ深く
    # 変換する — 旧 `copy.deepcopy(dict(parent.partitions[k]))` は
    # parent.partitions[k] の中身が既に凍結構造（ネストした
    # MappingProxyType/tuple）になった後は `copy.deepcopy()` が
    # mappingproxy を pickle 経由で複製できず失敗するため、単純な
    # deepcopy 呼び出しはもう成立しない。
    new_partitions: Dict[str, Dict[str, Any]] = {
        k: _deep_thaw(parent.partitions[k]) for k in PROFILE_PARTITION_KEYS
    }
    for partition_key, value in updates.items():
        new_partitions[partition_key] = dict(value)

    # builder 側の防御的二重検証（Codex bot レビュー PR #318 第1巡 Fix 3
    # 採用、「builder 側も」）: `_resolve_derived_revision()` は
    # `run9_schema.BRANCH_REVISIONS` から revision を導出するため、この
    # assertion は理論上常に真になる（矛盾組合せを作る経路が無い）。
    # それでも `_valid_revisions_for_branch()` を再度通し、将来の実装変更
    # が誤って矛盾する組合せを生成しないことを builder 自身にも保証させる
    # — `control_profile_from_dict()` 側の検証だけに頼らない。
    if revision not in _valid_revisions_for_branch(branch):
        raise Run9ControlProfileError(  # pragma: no cover - 到達不能（防御的二重検証）
            f"internal invariant violated: derive_profile() resolved revision={revision!r} for "
            f"branch={branch!r}, which is not in {sorted(_valid_revisions_for_branch(branch))}"
        )

    profile_id = _compute_profile_id(
        voice_id=parent.voice_id, branch=branch, revision=revision,
        parent_revision=parent.revision, partitions=new_partitions,
    )
    return Run9ControlProfile(
        schema=SCHEMA_CONTROL_PROFILE, voice_id=parent.voice_id, branch=branch, revision=revision,
        parent_revision=parent.revision, partitions=new_partitions, profile_id=profile_id,
    )


def control_profile_from_dict(data: Any) -> Run9ControlProfile:
    """JSON dict から `Run9ControlProfile` を再構築する。fail-closed
    （未知キー拒否）+ 宣言された `profile_id` が実際の再計算値と一致する
    ことを要求する（改ざん検出 — `run9_schema.founder_genome_from_dict()`
    と同型の規律）。"""
    if not isinstance(data, dict):
        raise Run9ControlProfileError(f"control profile document must be an object, got {type(data).__name__}")
    unknown = set(data.keys()) - _PROFILE_TOP_LEVEL_KEYS
    if unknown:
        raise Run9ControlProfileError(f"control profile document has unknown key(s): {sorted(unknown)}")
    missing = _PROFILE_TOP_LEVEL_KEYS - set(data.keys())
    if missing:
        raise Run9ControlProfileError(f"control profile document missing required key(s): {sorted(missing)}")

    schema = data["schema"]
    if schema != SCHEMA_CONTROL_PROFILE:
        raise Run9ControlProfileError(f"schema must be {SCHEMA_CONTROL_PROFILE!r}, got {schema!r}")

    voice_id = _require_founder_voice_id(data["voice_id"])

    branch = data["branch"]
    if branch is not None and branch not in _rs.BRANCH_WRITABLE_PARTITIONS:
        raise Run9ControlProfileError(
            f"branch must be null or one of {sorted(_rs.BRANCH_WRITABLE_PARTITIONS)}, got {branch!r}"
        )

    revision = data["revision"]
    if not isinstance(revision, str) or revision not in VALID_REVISIONS:
        raise Run9ControlProfileError(
            f"revision must be one of {list(VALID_REVISIONS)}, got {revision!r}"
        )

    parent_revision = data["parent_revision"]
    if parent_revision is not None and (
        not isinstance(parent_revision, str) or parent_revision not in VALID_REVISIONS
    ):
        raise Run9ControlProfileError(
            f"parent_revision must be null or one of {list(VALID_REVISIONS)}, got {parent_revision!r}"
        )

    # r0 とそれ以外で branch/parent_revision の null 性が対になっている
    # ことを強制する（r0 だけが親を持たない起点 — 中間欠損や逆転を防ぐ）。
    if revision == NEUTRAL_REVISION:
        if branch is not None or parent_revision is not None:
            raise Run9ControlProfileError(
                f"revision {NEUTRAL_REVISION!r} (birth-neutral origin) must declare branch=null and "
                f"parent_revision=null, got branch={branch!r} parent_revision={parent_revision!r}"
            )
    else:
        if branch is None or parent_revision is None:
            raise Run9ControlProfileError(
                f"revision {revision!r} (branch-derived) must declare a non-null branch and "
                f"parent_revision, got branch={branch!r} parent_revision={parent_revision!r}"
            )
        # loader 側の親 r0 限定（Codex bot レビュー PR #318 第2巡 Fix 6
        # 採用）: `derive_profile()` 側の Fix 1（builder が親 revision を
        # r0 に強制）と対称の防御を、文書を直接読み込む loader 側にも配線
        # する。Fix 1 が防ぐのは「これから derive する」経路の枝汚染だが、
        # 手で組み立てた（または改ざんされた）文書が直接
        # `control_profile_from_dict()` に渡された場合、builder を経由
        # しないため Fix 1 だけでは防げない — 例えば
        # parent_revision="r_practice" を宣言する r_taught 文書は、
        # 個々のフィールド検証（revision は既知語彙・parent_revision も
        # 既知語彙）だけでは検出できず、ここで初めて拒否される。
        if parent_revision != NEUTRAL_REVISION:
            raise Run9ControlProfileError(
                f"revision {revision!r} (branch-derived) must declare parent_revision == "
                f"{NEUTRAL_REVISION!r} (birth-neutral canonical origin) — 全枝は r0 から独立分岐する"
                f"（PoR §4）。got parent_revision={parent_revision!r} (branch={branch!r}) — a "
                "document claiming a non-r0 parent is cross-arm contamination, whether produced by "
                "derive_profile() or hand-assembled"
            )
        # 全ての矛盾する (branch, revision) 組合せを網羅的に拒否する
        # （Codex bot レビュー PR #318 第1巡 Fix 3 採用）: r0 以外は
        # PRACTICE_FROM_AUDIO→r_practice / TRANSFER_TECHNIQUE→r_taught /
        # CONTROL→{replay, r_sham} のいずれかへ厳密対応しなければならない
        # — 例えば TRANSFER_TECHNIQUE + r_practice のような取り違えは
        # 個々のフィールド検証（branch は既知の枝・revision は既知の
        # revision 語彙）だけでは検出できず、この交差検証で初めて拒否
        # される。
        allowed_revisions = _valid_revisions_for_branch(branch)
        if revision not in allowed_revisions:
            raise Run9ControlProfileError(
                f"branch {branch!r} may only declare revision in {sorted(allowed_revisions)} "
                f"(run9_schema.BRANCH_REVISIONS is the source of truth), got revision={revision!r} "
                "— mismatched (branch, revision) pair (e.g. TRANSFER_TECHNIQUE declaring "
                "r_practice) is rejected"
            )

    partitions = _validate_partitions_shape(data["partitions"])

    # 枝書き込み境界のパーティション「内容」への適用（Codex bot レビュー
    # PR #318 第3巡 Fix 10 採用）: ここまでの検証は partitions の存在形状
    # （2キー・dict・非有限値なし）のみで、branch write policy を内容へは
    # 適用していなかった。手組みの TRANSFER_TECHNIQUE + r_taught 文書に
    # 非空 trait_control を入れても、あるいは CONTROL 枝文書に非 neutral
    # な partition を入れても、profile_id さえ再計算に通れば受理されて
    # しまい、`derive_profile()` の `validate_branch_write()` 関門を丸ごと
    # 迂回して禁止パーティションへの効果を直接公開できていた。ここでは
    # 「branch が書き込めないパーティションは neutral 値
    # （`build_neutral_profile()` の当該パーティション内容）と完全一致
    # しなければならない」を強制する — r0（revision=="r0"、writable 集合は
    # 常に空扱い）は全パーティション neutral 一致、枝派生 revision は
    # `run9_schema.BRANCH_WRITABLE_PARTITIONS[branch]` に無い partition
    # だけ neutral 一致を強制する（書き込める側は制約しない — その中身の
    # 正当性は `derive_profile()` 側の検証に委ねる）。
    neutral_partitions = build_neutral_profile(voice_id).partitions
    if revision == NEUTRAL_REVISION:
        writable_state_partitions: FrozenSet[str] = frozenset()
    else:
        writable_state_partitions = frozenset(_rs.BRANCH_WRITABLE_PARTITIONS[branch])
    for partition_key in PROFILE_PARTITION_KEYS:
        state_partition = _PARTITION_KEY_TO_STATE_PARTITION[partition_key]
        if state_partition in writable_state_partitions:
            continue
        if partitions[partition_key] != neutral_partitions[partition_key]:
            raise Run9ControlProfileError(
                f"partitions.{partition_key} is not writable on branch {branch!r} (revision "
                f"{revision!r}) — this partition must exactly match the birth-neutral value "
                "(build_neutral_profile()'s content for this partition) because "
                f"run9_schema.BRANCH_WRITABLE_PARTITIONS[{branch!r}] does not include "
                f"{state_partition!r} — a hand-assembled or tampered document with non-neutral "
                "content here would bypass derive_profile()'s validate_branch_write() boundary "
                "enforcement and publish a forbidden-partition effect directly"
            )

    profile_id = data["profile_id"]
    if not isinstance(profile_id, str) or not _PROFILE_ID_RE.match(profile_id):
        raise Run9ControlProfileError(
            f"profile_id must be exactly {_PROFILE_ID_LEN} lowercase hex characters, got {profile_id!r}"
        )

    recomputed = _compute_profile_id(
        voice_id=voice_id, branch=branch, revision=revision, parent_revision=parent_revision,
        partitions=partitions,
    )
    if recomputed != profile_id:
        raise Run9ControlProfileError(
            f"profile_id mismatch: declared {profile_id!r} but recomputed {recomputed!r} from "
            "(voice_id, branch, revision, parent_revision, partitions) — tampering or corruption"
        )

    return Run9ControlProfile(
        schema=schema, voice_id=voice_id, branch=branch, revision=revision,
        parent_revision=parent_revision, partitions=partitions, profile_id=profile_id,
    )


# ---------------------------------------------------------------------------
# Run9ProfileLedger — append-only 台帳（voice_genesis/evolution/ledger.py
# の意味論を run-local に踏襲）
# ---------------------------------------------------------------------------


class Run9ProfileWriteResult(NamedTuple):
    """`Run9ProfileLedger.write()` の戻り値（`ledger.WriteResult` と同型）。
    `created=True` は実際に新規作成、`created=False` はバイト同一内容の
    冪等 no-op。"""

    path: Path
    created: bool


class Run9ProfileLedger:
    """`directory` 配下の ControlProfile 台帳。1 profile = 1 JSON ファイル
    （`<profile_id>.json`）。"""

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def path_for(self, profile_id: str) -> Path:
        if not _PROFILE_ID_RE.match(profile_id):
            raise Run9ControlProfileError(f"invalid profile_id format: {profile_id!r}")
        return self.directory / f"{profile_id}.json"

    def _reject_symlink_escape(self, path: Path) -> None:
        """`<ledger>/<profile_id>.json` が symlink である、または解決後の
        パスが `self.directory` の外側を指す場合は拒否する
        （`ledger.py Ledger._reject_symlink_escape()` と同型）。"""
        if path.is_symlink():
            raise Run9ControlProfileError(
                f"refusing to access {path}: ledger entries must be regular files, not symlinks"
            )
        directory_resolved = self.directory.resolve()
        resolved = path.resolve()
        try:
            resolved.relative_to(directory_resolved)
        except ValueError:
            raise Run9ControlProfileError(
                f"refusing to access {path}: resolved path {resolved} escapes the ledger directory "
                f"{directory_resolved}"
            ) from None

    def _alias_path_for(self, voice_id: str, revision: str) -> Path:
        """`(voice_id, revision)` の一意性主張を排他 hard link で担持する
        tuple-alias ファイルのパス（`byrev_<voice_id>_<revision>.link`）。
        `voice_id`/`revision` は共に固定の閉じた語彙
        （`run9_schema.CONTRACT_FOUNDER_IDS` / `VALID_REVISIONS`）に限定
        され、`_require_founder_voice_id()`/`control_profile_from_dict()`
        側で常に検証済みの値しかここへは渡らないため、任意文字列由来の
        path traversal の懸念は無い。"""
        return self.directory / f"byrev_{voice_id}_{revision}.link"

    def write(self, profile: Run9ControlProfile) -> Run9ProfileWriteResult:
        """`profile` を `<profile_id>.json` として排他 create で公開する。
        同一 profile_id に既に同一内容が書かれていれば冪等 no-op。
        異なる内容での既存ファイルとの衝突は
        `Run9ProfileLedgerConflictError`（append-only 規律の機械的補強
        — r0/親を含むどの既存 revision も in-place 更新できない）。

        親revision実在検証: `profile.parent_revision` が非 None（= r0 で
        ない）場合、`(profile.voice_id, profile.parent_revision)` の組が
        台帳に実在することを `read()` 相当のフル検証込みで確認する
        （`ledger.py` の parents 実在検証 — `self.exists()` ではなく
        `self.read()` 相当を使う設計を踏襲）。未 publish・typo の親は
        fail-closed で拒否する。

        `(voice_id, revision)` 一意性の原子化（Codex bot レビュー PR #318
        第2巡 Fix 5 採用、第1巡 Fix 4 の是正）: **tuple-alias hard link
        方式**。第1巡時点の実装は「publish 前に台帳を走査して既存 entry を
        探す」という preflight 検査であり、実際のファイル書込みとは
        分離していたため、2つの並行 publish が両方とも preflight を通過
        してしまう窓が原理的に残っていた（TOCTOU）。本巡では、
        `(voice_id, revision)` の一意性主張そのものを
        `byrev_<voice_id>_<revision>.link` という専用ファイルへ集約し、
        その内容（claim している `profile_id`）を `os.link()` の排他
        create で確定させる — `os.link()` は OS レベルで atomic な
        操作であるため、同一 tuple を主張する複数の書込みが競合しても、
        どちらか一方だけが確実に claim に成功する。

        方式選択（lockfile 方式との比較）: VG-E0 系には filelock による
        直列化の先例がある（PR #261）。lockfile 方式でも一意性は守れるが、
        「lock 取得後・release 前にプロセスが crash すると stale lock が
        残り、以降の publish が人手復旧待ちで止まる」という crash-safety
        上の弱点がある。tuple-alias hard link 方式は、claim 自体が
        単一の `os.link()` 呼び出しで完結する（lock の取得/解放という
        二段階の状態を持たない）ため、途中で crash してもロック待ちで
        後続処理が詰まることが無い — 最悪ケースでも「本体ファイルは
        存在するが、どの alias からも参照されない孤児」が残るだけで、
        これは以降の publish を一切ブロックしない（同じ内容の
        再 publish は当該 profile_id ファイルへの `os.link()` が
        既存バイト列と一致するため冪等に成功し、異なる内容の publish は
        新しい profile_id を持つため無関係のファイルとして扱われる）。
        crash-safety を優先し、tuple-alias hard link 方式を採用する。

        **alias の存在をコミットの正とする**（Codex bot レビュー PR #318
        第3巡 Fix 9 採用）: 上記の「孤児」は書込み側では許容する設計だが、
        読者側（`list_profile_ids()`/`_find_by_voice_and_revision()`/
        `read()` 経由の全消費者）が孤児本体を誤って live として扱うと、
        後から別 profile が同じ `(voice_id, revision)` の alias を
        claim した際に同じ tuple に可読 profile が2つ並び、parent lookup
        が sorted ID で孤児を選び得る不整合になる。これを防ぐため、全
        読者経路は `_resolve_live_profile_id_for_alias()` を介して
        「対応する alias が存在し、その claim する profile_id の本体が
        実在してフル検証を通る」場合にのみ live とみなす — 孤児は
        「以降の publish をブロックしない」だけでなく「読者から恒久的に
        不可視」になる。

        書込シーケンス: ①本体ファイル（`<profile_id>.json`）を
        profile_id 単位の排他 create で書く（既存ロジック、同一
        profile_id 衝突の冪等/conflict 判定はここで完結）→ ②
        tuple-alias ファイルを排他 create で claim する。②が
        「既存 alias が別の profile_id を指す」ために失敗した場合、
        ①でこの呼び出しが新規作成した本体ファイルを削除して後始末する
        （部分生成物を残さない — 既存ファイルへの republish だった場合は
        削除しない。他の publish が正当に所有するファイルを壊さないため）。
        """
        path = self.path_for(profile.profile_id)
        alias_path = self._alias_path_for(profile.voice_id, profile.revision)
        # allow_nan=False（Codex bot レビュー PR #318 第2巡 Fix 8 採用、
        # `_reject_non_finite()` との二重防御）: profile はここへ来るまでに
        # 構築経路（`build_neutral_profile()`/`derive_profile()`）または
        # 読込経路（`control_profile_from_dict()`）のいずれかで非有限値
        # 拒否を通過済みのはずだが、`Run9ControlProfile` を dataclass
        # コンストラクタ経由で直接構築する経路（テストや将来の呼び出し）は
        # それらの関門を経由しない。publish 直前のこの直列化で
        # allow_nan=False にしておけば、そうした経路が万一 NaN/inf を
        # 台帳へ書き込もうとしても TypeError で即座に失敗する。
        payload = (
            json.dumps(profile.to_dict(), sort_keys=True, indent=2, allow_nan=False) + "\n"
        ).encode("utf-8")

        # publish 直前の round-trip 検証。
        try:
            control_profile_from_dict(_loads_strict_json(payload.decode("utf-8")))
        except Run9ControlProfileError as exc:
            raise Run9ControlProfileError(
                f"refusing to publish profile_id {profile.profile_id!r}: serialized payload failed "
                f"round-trip validation via control_profile_from_dict() ({exc})"
            ) from exc

        if profile.parent_revision is not None:
            parent_found = self._find_by_voice_and_revision(profile.voice_id, profile.parent_revision)
            if parent_found is None:
                raise Run9ControlProfileError(
                    f"refusing to publish profile_id {profile.profile_id!r}: parent revision "
                    f"(voice_id={profile.voice_id!r}, revision={profile.parent_revision!r}) does not "
                    "exist in the ledger (parents must already be published — an unpublished or "
                    "mistyped parent revision would leave a dangling edge in the lineage)"
                )

        self.directory.mkdir(parents=True, exist_ok=True)

        # ① 本体ファイルの書込み（profile_id 単位の排他 create — 既存
        # ロジックそのまま）。
        main_freshly_created = False
        fd, tmp_name = tempfile.mkstemp(dir=self.directory, prefix=f"{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(tmp_name, path)
                main_freshly_created = True
            except FileExistsError:
                self._reject_symlink_escape(path)
                existing = path.read_bytes()
                if existing != payload:
                    raise Run9ProfileLedgerConflictError(
                        f"profile_id {profile.profile_id!r} already exists in the ledger with "
                        "different content (append-only ledger — changes must go through a PR, not "
                        "an overwrite)"
                    ) from None
                # existing == payload: 同一 profile_id への冪等 republish。
        finally:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass

        # ② tuple-alias hard link による (voice_id, revision) 一意性の
        # 原子化 claim（Fix 5 — docstring 参照）。
        alias_claim = profile.profile_id.encode("ascii")
        fd2, tmp_alias_name = tempfile.mkstemp(
            dir=self.directory, prefix=f"{alias_path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd2, "wb") as handle:
                handle.write(alias_claim)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(tmp_alias_name, alias_path)
            except FileExistsError:
                self._reject_symlink_escape(alias_path)
                existing_claim = alias_path.read_bytes()
                if existing_claim != alias_claim:
                    # 別の profile_id が既にこの tuple を claim 済み —
                    # 本体ファイルをこの呼び出しで新規作成していた場合のみ
                    # 後始末する（部分生成物を残さない。既存ファイルへの
                    # republish だった場合は他の publish の正当な所有物
                    # なので削除しない）。
                    if main_freshly_created:
                        try:
                            os.unlink(path)
                        except OSError:
                            pass
                    raise Run9ProfileLedgerConflictError(
                        f"refusing to publish profile_id {profile.profile_id!r}: ledger already has a "
                        f"different profile_id {existing_claim.decode('ascii', 'replace')!r} claiming "
                        f"(voice_id={profile.voice_id!r}, revision={profile.revision!r}) via the "
                        "tuple-alias hard link — a given (voice_id, revision) tuple must resolve to "
                        "exactly one profile content (byte-identical republish under the same "
                        "profile_id remains an idempotent no-op)"
                    ) from None
                # existing_claim == alias_claim: 同一 profile_id が既に
                # このタプルを claim 済み（冪等）。
        finally:
            try:
                os.unlink(tmp_alias_name)
            except OSError:
                pass

        return Run9ProfileWriteResult(path=path, created=main_freshly_created)

    def _read_raw(self, profile_id: str) -> Run9ControlProfile:
        """`profile_id` 単位の本体ファイルを、alias 照合なしで読み込む
        **内部専用** reader（Codex bot レビュー PR #318 第4巡 Fix 12
        採用）。schema/型・`profile_id` 再計算一致・symlink escape guard
        を含むフル検証は行うが、`(voice_id, revision)` tuple-alias の
        存在・claim 一致は検証しない。公開 `read()` はこの raw reader を
        alias 照合でラップした上位関数であり、`_resolve_live_profile_id_
        for_alias()` は逆に本メソッドを直接使う（`read()` を使うと
        「alias 解決の途中でまた alias 解決を呼ぶ」無限再帰になるため）。
        呼び出し元は本メソッドの結果を「live である」とみなしてはならない
        — alias 照合込みの生存判定が必要な経路は必ず `read()` または
        `_resolve_live_profile_id_for_alias()` を経由すること。"""
        path = self.path_for(profile_id)
        self._reject_symlink_escape(path)
        data = _loads_strict_json(path.read_text(encoding="utf-8"))
        profile = control_profile_from_dict(data)
        if profile.profile_id != profile_id:
            raise Run9ControlProfileError(
                f"profile_id mismatch: requested {profile_id!r} but {path} declares "
                f"{profile.profile_id!r} (filename/content binding violated — renamed or corrupted file)"
            )
        return profile

    def read(self, profile_id: str) -> Run9ControlProfile:
        """`profile_id` の本体を読み込む唯一の公開経路。**alias 照合を
        必須とする**（Codex bot レビュー PR #318 第4巡 Fix 12 採用、
        第3巡 Fix 9 の是正）: 旧実装は body ファイルが `_read_raw()`
        相当のフル検証（schema/型・`profile_id` 再計算一致）を通れば、
        `(voice_id, revision)` tuple-alias の存在・claim を一切確認せず
        受理していた。Fix 9 は「alias の存在を publish のコミットポイント
        とする」という原則を `list_profile_ids()`/`_find_by_voice_and_
        revision()` には適用したが、公開 `read()` 自体はこの原則の外に
        残っていた — その結果、本体書込み①後・alias claim②前で crash
        した孤児（Fix 9 参照）を、profile_id さえ知っていれば
        （例えば crash 前に発行元プロセスが自分の profile_id をログへ
        出していた等の経路で）直接 `read()` でき、かつ後から別の body が
        同じ `(voice_id, revision)` tuple を claim した後も、両方の body
        が並行して可読なまま残ってしまっていた（alias コミットポイントと
        「一つの tuple には一つの来歴」という不変条件が公開 read() の
        層で破れる）。

        ここでは、本体を `_read_raw()` でフル検証してから、`_alias_path_
        for(profile.voice_id, profile.revision)` が実際に存在し、その
        claim がこの `profile_id` と一致することを追加で要求する
        （`_resolve_live_profile_id_for_alias()` へ委譲— Fix 13 の
        tuple 束縛検証も自動的に効く）。孤児 body、または別 profile が
        同じ tuple を横取りした後に取り残された旧 body は、公開 read()
        からも恒久的に不可視になる。"""
        profile = self._read_raw(profile_id)
        alias_path = self._alias_path_for(profile.voice_id, profile.revision)
        resolved = self._resolve_live_profile_id_for_alias(
            alias_path, expected_voice_id=profile.voice_id, expected_revision=profile.revision,
        )
        if resolved != profile_id:
            raise Run9ControlProfileError(
                f"refusing to read profile_id {profile_id!r}: no live (voice_id={profile.voice_id!r}, "
                f"revision={profile.revision!r}) tuple-alias claims this profile_id — the body file "
                "is structurally valid on its own but is either an unclaimed crash orphan (write() "
                "wrote the body but crashed before claiming the tuple-alias) or a stale body that a "
                "different profile_id has since superseded for this tuple; public read() only ever "
                "exposes a tuple's current live claimant, never an orphan or a superseded body"
            )
        return profile

    def exists(self, profile_id: str) -> bool:
        """`profile_id` が公開 `read()` で読める（= live である）かを
        判定する（Codex bot レビュー PR #318 第4巡 Fix 12 採用）: 旧実装
        `self.path_for(profile_id).exists()` はディレクトリ上のファイル
        存在だけを見ており、alias 未 claim の孤児本体や、別 profile に
        tuple を奪われた旧 body に対しても `True` を返してしまっていた
        — `exists()` は `read()` と同じ「live」の定義を共有しなければ、
        呼び出し元が `if ledger.exists(x): ledger.read(x)` のような
        ガード付き読み込みを書いた際に矛盾する（`exists()` が True を
        返した直後に `read()` が拒否する）。`read()` をそのまま呼び、
        例外を bool へ変換するだけの薄いラッパーとする。"""
        try:
            self.read(profile_id)
        except (Run9ControlProfileError, OSError, json.JSONDecodeError):
            return False
        return True

    def _resolve_live_profile_id_for_alias(
        self, alias_path: Path, *, expected_voice_id: str, expected_revision: str
    ) -> Optional[str]:
        """`alias_path`（`byrev_<voice_id>_<revision>.link`）が claim する
        profile_id を読み、それが実際に生存 (live) しているかを判定する
        （Codex bot レビュー PR #318 第3巡 Fix 9 採用: **alias の存在を
        publish のコミットポイントとする**）。

        `Run9ProfileLedger.write()` は①本体ファイル（`<profile_id>.json`）
        → ②tuple-alias ファイルの順に書く。①後・②前で crash / I/O 失敗が
        起きると、alias が claim していない「孤児本体」が残り得る
        （`profile.py` 側の write() docstring 参照）。この孤児は、
        `read()`/`list_profile_ids()` が単純にディレクトリを走査する旧実装
        だと live として扱われてしまい、後から別 profile が同じ
        `(voice_id, revision)` の alias を claim すると同じ tuple に
        可読 profile が2つ並ぶ・parent lookup が sorted ID で孤児を選び
        得る、という不整合を招く。

        本メソッドは alias ファイルの側から出発し、①alias が存在し
        （symlink でなく）②その claim が profile_id 形式として妥当で、
        ③対応する本体ファイルが実在し、④本体を `_read_raw()` でフル検証
        （schema/型/profile_id 再計算一致・symlink escape guard を含む。
        `read()` ではなく `_read_raw()` を使う — `read()` 自身が本メソッド
        へ委譲するため `read()` を呼ぶと無限再帰になる）して claim と
        一致する場合にのみ、その profile_id を「live」候補とする。

        **tuple 束縛検証**（Codex bot レビュー PR #318 第4巡 Fix 13
        採用）: ③④だけでは、本体の内部整合（自己の profile_id と一致
        するか）しか見ていない — ロードした profile 自身の `voice_id`/
        `revision` が、`alias_path` が実際に表す tuple（`expected_voice_
        id`/`expected_revision` として呼び出し元から渡される）と一致する
        ことまでは検証していなかった。複製・改名・破損した
        `byrev_R9F-02_r0.link` が（例えば）R9F-01 の valid な r0 body を
        誤って指してしまうと、`_find_by_voice_and_revision("R9F-02",
        "r0")` が R9F-01 の profile を返してしまい、R9F-02 の子 publish
        が「親が存在する」という誤った判定を通ってしまう。本メソッドは
        ⑤として、ロードした `profile.voice_id == expected_voice_id` かつ
        `profile.revision == expected_revision` であることを追加で要求し
        （fail-closed — 一致しなければ `None` を返し「live ではない」と
        扱う。例外にはしない — 本メソッドの既存の意味論〈孤児・破損・
        改ざんは無条件で `None`〉に合わせ、呼び出し元の分岐を変えない）。

        いずれかの条件が欠ければ `None`（孤児・破損・改ざん・tuple 不一致
        は無条件で不可視 — fail-closed）。"""
        if not alias_path.is_file() or alias_path.is_symlink():
            return None
        try:
            claimed = alias_path.read_bytes().decode("ascii")
        except (OSError, UnicodeDecodeError):
            return None
        if not _PROFILE_ID_RE.match(claimed):
            return None
        try:
            profile = self._read_raw(claimed)
        except (Run9ControlProfileError, OSError, json.JSONDecodeError):
            return None
        if profile.voice_id != expected_voice_id or profile.revision != expected_revision:
            return None
        return claimed

    def _parse_alias_filename(self, name: str) -> Optional[Tuple[str, str]]:
        """`byrev_<voice_id>_<revision>.link` という alias ファイル名から
        `(voice_id, revision)` を復元する（Fix 13 の一部: `list_profile_
        ids()` は `glob("byrev_*.link")` で見つけたファイルの期待
        `(voice_id, revision)` をあらかじめ知らないため、`_resolve_live_
        profile_id_for_alias()` の tuple 束縛検証へ渡す `expected_voice_
        id`/`expected_revision` をファイル名自体から復元する必要がある）。
        `voice_id`（`run9_schema.CONTRACT_FOUNDER_IDS`）と `revision`
        （`VALID_REVISIONS`）はどちらも小さな閉じた語彙のため、既知の
        全組合せで `_alias_path_for()` が生成する名前と突き合わせる全探索
        で一意に復元できる（`voice_id`/`revision` 自体が任意文字列を含み
        得ないため、`_`区切りでの単純分割では曖昧になり得る箇所を語彙側の
        全探索で確実化する）。どの既知組合せにも一致しなければ `None`
        （未知・改ざんされたファイル名 — 該当 alias は無視する）。"""
        for voice_id in _rs.CONTRACT_FOUNDER_IDS:
            for revision in VALID_REVISIONS:
                if name == self._alias_path_for(voice_id, revision).name:
                    return voice_id, revision
        return None

    def list_profile_ids(self) -> List[str]:
        """台帳内の live な profile_id を列挙する。alias ファイル
        （`byrev_*.link`）の側から出発する（Fix 9 — `_resolve_live_
        profile_id_for_alias()` docstring 参照）: 本体 JSON を直接
        `glob("*.json")` する旧実装は、alias が claim していない孤児本体も
        無条件に live として返してしまっていた。alias ファイル名から
        `_parse_alias_filename()`（Fix 13）で期待 `(voice_id, revision)`
        を復元し、`_resolve_live_profile_id_for_alias()` の tuple 束縛
        検証へ渡す。"""
        if not self.directory.exists():
            return []
        live: set = set()
        for alias_path in self.directory.glob("byrev_*.link"):
            parsed = self._parse_alias_filename(alias_path.name)
            if parsed is None:
                continue
            voice_id, revision = parsed
            resolved = self._resolve_live_profile_id_for_alias(
                alias_path, expected_voice_id=voice_id, expected_revision=revision,
            )
            if resolved is not None:
                live.add(resolved)
        return sorted(live)

    def _find_by_voice_and_revision(self, voice_id: str, revision: str) -> Optional[Run9ControlProfile]:
        """`(voice_id, revision)` の tuple-alias ファイルを直接引き、その
        claim が live（Fix 13 の tuple 束縛検証込み）であれば対応する
        profile を返す（Fix 9 — 台帳全体を走査する旧実装から、alias パス
        1件の直接解決へ置き換え。alias の naming convention
        `byrev_<voice_id>_<revision>.link` により `(voice_id, revision)`
        と alias ファイルは1:1に対応するため、走査は元々不要だった）。
        孤児本体（alias 未 claim）、alias が claim する内容と本体が
        食い違う場合、または本体の `(voice_id, revision)` が alias の
        主張する tuple と一致しない場合（Fix 13）は live 扱いしない。
        呼び出し時点で `voice_id`/`revision` を既に知っているため、
        確認済みの `resolved` を改めて alias 経由の `read()` へ通さず
        `_read_raw()` で直接読む（tuple 束縛は本メソッド内で既に検証
        済み）。"""
        alias_path = self._alias_path_for(voice_id, revision)
        resolved = self._resolve_live_profile_id_for_alias(
            alias_path, expected_voice_id=voice_id, expected_revision=revision,
        )
        if resolved is None:
            return None
        return self._read_raw(resolved)


def _loads_strict_json(text: str) -> Any:
    """`run9_schema._loads_strict_json()`（重複キー fail-closed 拒否）を
    再利用する。private 命名（先頭アンダースコア）だが、run9_schema
    自身が VG-E0 `models.loads_strict()` を参考に独立実装した run-local
    正本であり、sibling モジュールでの重複キー拒否ロジックの二重実装を
    避けるためそのまま re-export する（呼び出し規約は変わらない）。"""
    try:
        return _rs._loads_strict_json(text)  # noqa: SLF001 - sibling module, see module docstring
    except _rs.Run9ValidationError as exc:
        raise Run9ControlProfileError(str(exc)) from exc


# ---------------------------------------------------------------------------
# practice trace（PoR §3.2 の保存要件、User 外部レビュー PR #317 P2-1）:
# 中身の生成はハーネス実装時（本 PR の範囲外）。schema と最低要件検証のみ
# ここで凍結する。
# ---------------------------------------------------------------------------

SCHEMA_PRACTICE_TRACE = "run9-practice-trace/1.0"

# PoR §7 の「Founder が選択した模倣対象、内部差分、探索履歴は、practice
# trace として保存する」を機械可読キー名へ写した最低要件。
PRACTICE_TRACE_REQUIRED_KEYS: Tuple[str, ...] = (
    "voice_id",
    "imitation_target_selection_log",  # 模倣対象選択の記録（時系列 list）
    "internal_diff_estimation_log",  # 自己/教師の内部差分推定の記録
    "search_history",  # 許可された可変領域内の探索履歴
)

_PRACTICE_TRACE_LOG_KEYS: FrozenSet[str] = frozenset({
    "imitation_target_selection_log", "internal_diff_estimation_log", "search_history",
})


def validate_practice_trace(data: Mapping[str, Any]) -> None:
    """practice trace の最低要件を検証する。中身（各ログ要素の詳細
    スキーマ）はハーネス実装時に確定するため、本関数は「必須欄が揃い、
    ログ欄が list であること」までを検証する（PoR P2-1 の保存要件を
    schema レベルで凍結する第一歩 — builder 自体は本 PR の範囲外）。
    """
    if not isinstance(data, dict):
        raise Run9ControlProfileError(f"practice trace must be an object, got {type(data).__name__}")
    schema = data.get("schema")
    if schema != SCHEMA_PRACTICE_TRACE:
        raise Run9ControlProfileError(
            f"practice trace schema must be exactly {SCHEMA_PRACTICE_TRACE!r}, got {schema!r}"
        )
    missing = [k for k in PRACTICE_TRACE_REQUIRED_KEYS if k not in data]
    if missing:
        raise Run9ControlProfileError(f"practice trace missing required key(s): {sorted(missing)}")
    _require_founder_voice_id(data["voice_id"])
    for key in _PRACTICE_TRACE_LOG_KEYS:
        if not isinstance(data[key], list):
            raise Run9ControlProfileError(
                f"practice trace.{key} must be a list, got {type(data[key]).__name__}"
            )
