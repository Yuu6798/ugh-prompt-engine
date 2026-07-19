"""IdentityManifest: hash 付き sidecar として「元曲として残すもの」を表現する。

`ArrangementSpec` が canonical `CompositionScore` の override を表現するのに対し、
`IdentityManifest` は canonical schema に一切 field を追加せず、独立した sidecar
ファイルとして「オリジナリティの根拠（source 音源 + 意味的 anchor）」を hash 付きで
記録する（AR2-1）。

sidecar 隔離の原則: 本モジュールは `CompositionScore` を import しない。artifact
（歌詞・メロディ・和声・structure 等）の内容を `CompositionScore` へ複製すること
はなく、hash による同一性の宣言だけを保持する。preservation contract との結合
（AR2-2）、CLI 統合、adherence 観測は本モジュールのスコープ外。

`IdentityManifestError` は `resolver.py` の `ArrangementError` 階層に連なる
（既存 arrange 系エラー階層との整合。この継承のために `arrange.resolver` を
import するが、`identity.py` 自身が `CompositionScore` 型を参照することはない）。

AR2-3（`design_memo_ar2_3.md`）: `IdentityAnchor.section_ref` の意味論をここで
定義する。manifest 内に `format_version == "section-map/0.2"` の structure
anchor（`domain == "structure" and artifact_type == "section_map"`）が
1 件以上存在する場合、非 `None` の `section_ref` は必ずそれら anchor の
（複数あれば合併した）section id 集合のいずれかに解決されなければならず、
解決できない参照（dangling）は `IdentityManifestError` で fail-fast する。
0.2 structure anchor が manifest に 1 件も無い場合、`section_ref` は従来どおり
opaque な文字列として一切検証しない（AR2-2 以前の後方互換）。この検証は
`parse_identity_manifest_with_artifacts` が行う（artifact bytes を既に読んでいる
経路に意味検証を置く既存の 2 層構造 — schema 検証は `IdentityManifest` 自身、
artifact 対面検証は本関数 — にそのまま従う。`parse_identity_manifest` は
`collect=None` でこの関数へ委譲する薄いラッパーのままなので、section_ref
解決も同じく効く）。stable id は section-map artifact 自身（`arrange.section_map`）
が持つ — `IdentityAnchor` 側には id を複製しない（sidecar-first / D1: id は
「1 artifact 内の部分」の識別子であり、manifest 側の関心事ではない）。

section-map スキーマ（`SectionMapArtifact` / `SectionMapArtifactV2`）は
`arrange.section_map` にある。`observe.py` が本モジュールを import する一方
向の依存（`observe.py` -> `identity.py`）を崩さないため、`identity.py` は
`observe.py` を import しない — section-map の parse ロジックは双方が依存できる
第三のモジュールへ切り出してある（詳細は `arrange/section_map.py` の
モジュール docstring）。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable, List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from svp_rpe.arrange.pathsafe import PathConfinementError, resolve_confined
from svp_rpe.arrange.resolver import ArrangementError
from svp_rpe.arrange.section_map import (
    SECTION_MAP_ARTIFACT_SCHEMA_0_2,
    parse_section_map_artifact_0_2,
)

AnchorDomain = Literal["lyrics", "melody", "harmony", "rhythm", "structure", "motif"]
ArtifactType = Literal[
    "lyrics_text",
    "midi_clip",
    "note_events_json",
    "chord_sequence_json",
    "audio_excerpt",
    "section_map",
]
RightsBasis = Literal["original", "licensed", "permission_confirmed", "public_domain", "unknown"]

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class IdentityModel(BaseModel):
    """identity 側スキーマの共通基底。未知 key を拒否する。"""

    model_config = ConfigDict(extra="forbid")


class IdentityMeta(IdentityModel):
    work_id: str
    version: str | float


class IdentitySource(IdentityModel):
    """元音源の同一性宣言。"""

    locator: str
    sha256: str = Field(pattern=_SHA256_PATTERN)
    # 推測補完しない: rights_basis に default は与えない（不明なら明示的に "unknown"）。
    rights_basis: RightsBasis
    note: Optional[str] = None


class IdentityAnchor(IdentityModel):
    """1 つの意味的 anchor（歌詞・メロディ・和声・structure・motif 等）の同一性宣言。

    `section_ref` は「この anchor がどのセクションに対応するか」を指す
    forward reference で、manifest 内のどの structure anchor が
    ``format_version == "section-map/0.2"`` を宣言しているかで意味論が変わる
    （AR2-3、`design_memo_ar2_3.md`。本モジュールの
    `parse_identity_manifest_with_artifacts` が検証する）:

    - 0.2 structure anchor が manifest に 1 件以上あれば、`section_ref` は
      それら anchor の（複数あれば合併した）section id のいずれかでなければ
      ならない。解決できなければ `IdentityManifestError`（dangling reference,
      fail-fast）。
    - 0.2 structure anchor が manifest に無ければ、`section_ref` は従来どおり
      opaque な文字列として保持するのみで、本モジュールは値を検証・解釈しない
      （AR2-2 以前の後方互換 — 既存 manifest を無停止で読める）。
    """

    id: str
    domain: AnchorDomain
    artifact: str
    artifact_type: ArtifactType
    media_type: str = Field(min_length=1)
    format_version: Optional[str] = None
    sha256: str = Field(pattern=_SHA256_PATTERN)
    section_ref: Optional[str] = None
    required: bool


class IdentityManifest(IdentityModel):
    """work 単位の IdentityManifest。source 1 件 + anchor 0 件以上。"""

    schema_version: Literal["identity-manifest/0.1"]
    meta: IdentityMeta
    source: IdentitySource
    anchors: List[IdentityAnchor]

    @model_validator(mode="after")
    def _validate_unique_anchor_ids(self) -> "IdentityManifest":
        seen: set[str] = set()
        duplicates: set[str] = set()
        for anchor in self.anchors:
            if anchor.id in seen:
                duplicates.add(anchor.id)
            seen.add(anchor.id)
        if duplicates:
            raise ValueError(
                f"duplicate anchor id(s) in identity manifest: {', '.join(sorted(duplicates))}"
            )
        return self


class IdentityManifestError(ArrangementError):
    """IdentityManifest のロード・hash 照合に関するエラー。"""


def load_identity_manifest(path: Path | str) -> IdentityManifest:
    """IdentityManifest YAML をロードし、source / 各 anchor の宣言 sha256 を実 bytes と照合する。

    `source.locator` と各 `anchor.artifact` は manifest ファイルの親ディレクトリ
    からの相対パスとして解決する（cwd に依存しない）。絶対パス、および `../` や
    symlink 経由で manifest ディレクトリの外を指すパスは hash 照合より前に
    fail-fast で拒否する（manifest の可搬性契約: artifact は常に親ディレクトリ
    配下に閉じる）。各 artifact は `read_bytes` で 1 回だけ読み、そのバイト列から
    sha256 を計算して宣言値と比較する（TOCTOU を単一読み取りで構造的に排除）。
    path が存在しない・ディレクトリである・hash が一致しない場合も
    `IdentityManifestError` を送出する。

    manifest ファイル自身は本関数がここで 1 回だけ読む（`parse_identity_manifest`
    へ委譲）。呼び出し側が既に manifest bytes を別目的（例: 呼び出し側自身の
    provenance chain 検証用 hash 計算）で読んでいる場合は、二重読み込みを避ける
    ため `parse_identity_manifest` を直接呼ぶこと（`svprpe observe` の D-3 検証が
    そうする — PR #187 review）。
    """
    manifest_path = Path(path)
    try:
        raw_bytes = manifest_path.read_bytes()
    except OSError as exc:
        # artifact 読み失敗（IdentityManifestError ラップ済み）との対称性を保ち、
        # 呼び出し側の公開契約を生 OS 例外に晒さない。この時点で work_id は未知の
        # ため path のみを記録する。
        raise IdentityManifestError(
            f"identity manifest unreadable at {manifest_path}: {exc}"
        ) from exc
    return parse_identity_manifest(raw_bytes, manifest_path)


def parse_identity_manifest(raw_bytes: bytes, manifest_path: Path | str) -> IdentityManifest:
    """`load_identity_manifest` の中核（bytes を既に読んだ呼び出し側向けの経路）。

    `raw_bytes` を manifest YAML としてパース・検証し、source / 各 anchor の
    宣言 sha256 を実ファイルの bytes と照合する。`manifest_path` はエラー
    メッセージと anchor/source locator の解決基点（親ディレクトリ）にのみ使う
    — ファイルは読まない（呼び出し側がそれを担う）。非 mapping の `ValueError`
    と `yaml.YAMLError` は `load_identity_manifest` と同じ契約でラップしない
    （他 loader との共通挙動）。

    anchor artifact の中身（検証済み bytes）も必要な呼び出し側は
    `parse_identity_manifest_with_artifacts` を使うこと（本関数はその bytes を
    一切保持しない薄いラッパー — `collect=None` で呼ぶため、各 anchor の bytes
    は hash 照合直後に破棄され、O(total anchor bytes) のメモリを保持しない。
    PR #187 review round 11: 大きな `audio_excerpt` 等を参照する manifest でも
    manifest-only 経路にメモリスパイクを持ち込まない）。
    """
    manifest, _artifact_bytes = parse_identity_manifest_with_artifacts(
        raw_bytes, manifest_path, collect=None
    )
    return manifest


def parse_identity_manifest_with_artifacts(
    raw_bytes: bytes,
    manifest_path: Path | str,
    *,
    collect: Callable[[IdentityAnchor], bool] | None = None,
) -> tuple[IdentityManifest, dict[str, bytes]]:
    """`parse_identity_manifest` と同じ検証を行い、加えて `collect` が選んだ
    anchor の検証済み artifact bytes（`anchor_id -> bytes`）を返す。

    この bytes は hash 照合のために既に読んだものと同一であり（`anchor` ループ
    内で 1 回だけ `_read_and_verify_artifact_hash` を呼ぶ — 二重実装ではなく
    `parse_identity_manifest` と共有する内部コア）、artifact の中身を必要とする
    呼び出し側（例: `svprpe observe` の harmony センサー）はこれを再利用すれば
    ファイルを二度読まずに済む（PR #187 review round 2）。

    `collect`（PR #187 review round 11）は「戻り値の dict にどの anchor の
    bytes を保持するか」を選ぶ述語で、**デフォルトの `None` は一切保持しない**
    （hash 照合で読んだ bytes を anchor ごとに即座に破棄する — 呼び出し側が
    artifact の中身を必要としない場合の既定挙動。大きな `audio_excerpt` 等を
    参照する manifest で不要な O(total anchor bytes) のメモリ保持を避ける）。
    `collect(anchor)` が True を返した anchor だけが戻り値の dict に入る
    （例: `svprpe observe` は `is_harmony_sensor_anchor` を渡し、harmony
    センサーが実際に中身を読む chord_sequence_json anchor のみ保持する）。
    `source` artifact の bytes はこの dict の対象外のまま（呼び出し側が必要と
    したことがないため — 必要になれば追補する）。

    AR2-3: `section_ref` の意味検証もここで行う（`IdentityAnchor.section_ref`
    の docstring参照）。manifest 中の 0.2 structure anchor（`domain ==
    "structure" and artifact_type == "section_map" and format_version ==
    "section-map/0.2"`）それぞれの artifact を（`collect` の選択とは無関係に、
    常に）parse して section id を集める。2 件以上あれば合併し、合併内で
    同じ id が複数 anchor から宣言されていれば `IdentityManifestError`。0.2
    anchor が 1 件も無ければ検証をスキップし、全 anchor の `section_ref` を
    そのまま opaque に保持する。0.2 anchor が 1 件以上あれば、manifest 中の
    全 anchor（domain を問わない）の非 `None` な `section_ref` がこの合併
    id 集合に含まれるかを検査し、含まれないもの（dangling）があれば
    `IdentityManifestError` で一括報告する。
    """
    manifest_path = Path(manifest_path)
    data = _parse_yaml_mapping(raw_bytes, manifest_path)
    manifest = IdentityManifest.model_validate(data)

    base_dir = manifest_path.resolve().parent
    work_id = manifest.meta.work_id

    source_path = _resolve_confined(
        manifest.source.locator, base_dir, work_id=work_id, target="source"
    )
    _verify_artifact_hash(
        source_path,
        manifest.source.sha256,
        work_id=work_id,
        target="source",
    )

    artifact_bytes: dict[str, bytes] = {}
    # AR2-3: section id 群を anchor id 単位で集める（`section_ref` の merge
    # resolution はループ完了後、hash 照合済みの bytes が全て揃ってから行う）。
    section_ids_by_anchor: dict[str, list[str]] = {}
    for anchor in manifest.anchors:
        target = f"anchor '{anchor.id}'"
        artifact_path = _resolve_confined(
            anchor.artifact, base_dir, work_id=work_id, target=target
        )
        verified_bytes = _read_and_verify_artifact_hash(
            artifact_path,
            anchor.sha256,
            work_id=work_id,
            target=target,
        )
        if _is_section_map_v2_structure_anchor(anchor):
            section_ids_by_anchor[anchor.id] = _load_section_map_v2_ids(
                verified_bytes,
                artifact_path=artifact_path,
                work_id=work_id,
                target=target,
            )
        if collect is not None and collect(anchor):
            artifact_bytes[anchor.id] = verified_bytes

    _validate_section_ref_resolution(manifest, section_ids_by_anchor, work_id=work_id)

    return manifest, artifact_bytes


def _is_section_map_v2_structure_anchor(anchor: IdentityAnchor) -> bool:
    """Whether `anchor` is a `section-map/0.2` structure anchor — the triple
    whose artifact carries the stable section ids `section_ref` resolves
    against (AR2-3). Same-shaped condition as `observe.py`'s
    `is_structure_sensor_anchor` for the 0.2 case, deliberately not imported
    from there (`identity.py` never imports `observe.py` — see this module's
    docstring); the expression itself is simple enough that duplicating it
    carries negligible drift risk, unlike duplicating the section-map schema
    itself (which is shared via `arrange.section_map` instead).
    """
    return (
        anchor.domain == "structure"
        and anchor.artifact_type == "section_map"
        and anchor.format_version == SECTION_MAP_ARTIFACT_SCHEMA_0_2
    )


def _load_section_map_v2_ids(
    raw_bytes: bytes, *, artifact_path: Path, work_id: str, target: str
) -> list[str]:
    """Parse a `section-map/0.2` artifact's ids (`parse_section_map_artifact_0_2`
    already enforces artifact-internal id uniqueness). `raw_bytes` is the
    bytes `_read_and_verify_artifact_hash` already hash-verified in the
    caller's loop — this function never reads the file itself. Content-level
    parse failures (malformed JSON, schema mismatch) are rewrapped as
    `IdentityManifestError` to match this module's existing error contract
    (path confinement / hash mismatch already raise this type)."""
    try:
        artifact = parse_section_map_artifact_0_2(raw_bytes, artifact_path=artifact_path)
    except ValueError as exc:
        raise IdentityManifestError(
            f"identity manifest '{work_id}': {target} section-map/0.2 artifact "
            f"failed validation: {exc}"
        ) from exc
    return [entry.id for entry in artifact.sections]


def _validate_section_ref_resolution(
    manifest: IdentityManifest,
    section_ids_by_anchor: dict[str, list[str]],
    *,
    work_id: str,
) -> None:
    """Cross-validate every anchor's `section_ref` against the merged section
    id set declared by the manifest's 0.2 structure anchor(s) (AR2-3,
    `IdentityAnchor.section_ref` docstring). A no-op — `section_ref` stays
    opaque — when the manifest declares no 0.2 structure anchor at all
    (`section_ids_by_anchor` empty), preserving the pre-AR2-3 behaviour
    `test_note_and_section_ref_are_optional_and_preserved` pins.
    """
    if not section_ids_by_anchor:
        return

    owner_by_id: dict[str, str] = {}
    duplicate_ids: set[str] = set()
    for anchor_id, section_ids in section_ids_by_anchor.items():
        for section_id in section_ids:
            existing_owner = owner_by_id.get(section_id)
            if existing_owner is not None and existing_owner != anchor_id:
                duplicate_ids.add(section_id)
            owner_by_id[section_id] = anchor_id
    if duplicate_ids:
        raise IdentityManifestError(
            f"identity manifest '{work_id}': section id(s) declared by more than "
            f"one section-map/0.2 structure anchor: {', '.join(sorted(duplicate_ids))}"
        )

    known_ids = set(owner_by_id)
    dangling = [
        f"{anchor.id} -> {anchor.section_ref!r}"
        for anchor in manifest.anchors
        if anchor.section_ref is not None and anchor.section_ref not in known_ids
    ]
    if dangling:
        raise IdentityManifestError(
            f"identity manifest '{work_id}': section_ref does not resolve to any "
            f"section-map/0.2 id: {', '.join(dangling)}"
        )


def _parse_yaml_mapping(raw_bytes: bytes, path: Path) -> dict[str, Any]:
    data = yaml.safe_load(raw_bytes)
    if not isinstance(data, dict):
        raise ValueError(f"identity manifest must be a mapping: {path}")
    return data


def _resolve_confined(value: str, base_dir: Path, *, work_id: str, target: str) -> Path:
    """locator / artifact 文字列を base_dir 配下に閉じた実パスへ解決する。

    絶対パスは `base_dir / value` が base を黙って無視するため拒否する。
    `resolve()` は `../` と symlink の両方を追うため、解決後のパスが
    `base_dir` 配下にないものは manifest ディレクトリ脱出として拒否する
    （manifest の可搬性契約: artifact は常に親ディレクトリ相対）。

    確認は共通 utility `arrange.pathsafe.resolve_confined` に委譲する
    （item 15: path 検証共通化）。本関数はここで捕捉した
    `PathConfinementError` を既存の `IdentityManifestError` 型・既存メッセージ
    書式へ再送出するだけで、挙動は移行前と完全に同一（lexical 深さカウンタは
    このパスに追加しない — 追加すると traversal 系の失敗タイミング/メッセージが
    変わり、挙動保存の契約に反する）。
    """
    try:
        return resolve_confined(value, base_dir)
    except PathConfinementError as exc:
        if exc.reason == "absolute":
            raise IdentityManifestError(
                f"identity manifest '{work_id}': {target} path {value!r} must be a "
                f"relative path inside the manifest directory (absolute paths are not allowed)"
            ) from exc
        raise IdentityManifestError(
            f"identity manifest '{work_id}': {target} path {value!r} escapes the "
            f"manifest directory {exc.base}: resolved to {exc.resolved}"
        ) from exc


def _verify_artifact_hash(
    artifact_path: Path, expected_sha256: str, *, work_id: str, target: str
) -> None:
    """Thin wrapper over `_read_and_verify_artifact_hash` for callers (e.g.
    `package.py`) that only need the hash check, not the artifact bytes."""
    _read_and_verify_artifact_hash(
        artifact_path, expected_sha256, work_id=work_id, target=target
    )


def _read_and_verify_artifact_hash(
    artifact_path: Path, expected_sha256: str, *, work_id: str, target: str
) -> bytes:
    """Read `artifact_path` once, verify its sha256, and return the bytes read
    (the single-read core both `_verify_artifact_hash` and
    `parse_identity_manifest_with_artifacts` share — PR #187 review round 2)."""
    try:
        raw_bytes = artifact_path.read_bytes()
    except OSError as exc:
        raise IdentityManifestError(
            f"identity manifest '{work_id}': {target} artifact unreadable "
            f"at {artifact_path}: {exc}"
        ) from exc

    actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    if actual_sha256 != expected_sha256:
        raise IdentityManifestError(
            f"identity manifest '{work_id}': {target} sha256 mismatch at {artifact_path}: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )
    return raw_bytes
