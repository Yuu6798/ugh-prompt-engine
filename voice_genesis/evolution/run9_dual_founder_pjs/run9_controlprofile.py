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

import copy
import hashlib
import json
import math
import os
import re
import sys
import tempfile
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
    と同じ改ざん耐性の考え方）。"""

    schema: str
    voice_id: str
    branch: Optional[str]
    revision: str
    parent_revision: Optional[str]
    partitions: Mapping[str, Mapping[str, Any]]
    profile_id: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "voice_id": self.voice_id,
            "branch": self.branch,
            "revision": self.revision,
            "parent_revision": self.parent_revision,
            "partitions": {k: dict(self.partitions[k]) for k in PROFILE_PARTITION_KEYS},
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

    new_partitions: Dict[str, Dict[str, Any]] = {
        k: copy.deepcopy(dict(parent.partitions[k])) for k in PROFILE_PARTITION_KEYS
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

    def read(self, profile_id: str) -> Run9ControlProfile:
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

    def exists(self, profile_id: str) -> bool:
        return self.path_for(profile_id).exists()

    def list_profile_ids(self) -> List[str]:
        if not self.directory.exists():
            return []
        return sorted(p.stem for p in self.directory.glob("*.json") if _PROFILE_ID_RE.match(p.stem))

    def _find_by_voice_and_revision(self, voice_id: str, revision: str) -> Optional[Run9ControlProfile]:
        """台帳内を走査し `(voice_id, revision)` に一致する profile を
        `read()` 経由（= フル検証込み）で探す。RUN9 の規模（Founder 2体 ×
        revision 最大5件 = 10 エントリ程度）ではディレクトリ走査で十分
        — 大規模化した場合はインデックスファイルの追加を検討する。"""
        for candidate_id in self.list_profile_ids():
            try:
                candidate = self.read(candidate_id)
            except (Run9ControlProfileError, json.JSONDecodeError):
                continue
            if candidate.voice_id == voice_id and candidate.revision == revision:
                return candidate
        return None


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
