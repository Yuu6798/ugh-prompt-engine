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
    """
    if not isinstance(parent, Run9ControlProfile):
        raise Run9ControlProfileError(f"parent must be a Run9ControlProfile, got {type(parent).__name__}")
    if not isinstance(updates, dict):
        raise Run9ControlProfileError(f"updates must be an object, got {type(updates).__name__}")

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

    new_partitions: Dict[str, Dict[str, Any]] = {
        k: copy.deepcopy(dict(parent.partitions[k])) for k in PROFILE_PARTITION_KEYS
    }
    for partition_key, value in updates.items():
        new_partitions[partition_key] = dict(value)

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
        """
        path = self.path_for(profile.profile_id)
        payload = (json.dumps(profile.to_dict(), sort_keys=True, indent=2) + "\n").encode("utf-8")

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

        fd, tmp_name = tempfile.mkstemp(dir=self.directory, prefix=f"{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(tmp_name, path)
            except FileExistsError:
                self._reject_symlink_escape(path)
                existing = path.read_bytes()
                if existing == payload:
                    return Run9ProfileWriteResult(path=path, created=False)
                raise Run9ProfileLedgerConflictError(
                    f"profile_id {profile.profile_id!r} already exists in the ledger with different "
                    "content (append-only ledger — changes must go through a PR, not an overwrite)"
                ) from None
        finally:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        return Run9ProfileWriteResult(path=path, created=True)

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
