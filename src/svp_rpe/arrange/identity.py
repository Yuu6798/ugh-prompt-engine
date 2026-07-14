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
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from svp_rpe.arrange.resolver import ArrangementError

AnchorDomain = Literal["lyrics", "melody", "harmony", "rhythm", "structure", "motif"]
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

    `section_ref` は AR2-3（structure anchor policy）が意味論を定義するまでは
    opaque な文字列として保持するのみで、本モジュールは値を検証・解釈しない。
    """

    id: str
    domain: AnchorDomain
    artifact: str
    sha256: str = Field(pattern=_SHA256_PATTERN)
    section_ref: Optional[str] = None
    required: bool


class IdentityManifest(IdentityModel):
    """work 単位の IdentityManifest。source 1 件 + anchor 0 件以上。"""

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
    """
    manifest_path = Path(path)
    try:
        data = _load_yaml_mapping(manifest_path)
    except OSError as exc:
        # artifact 読み失敗（IdentityManifestError ラップ済み）との対称性を保ち、
        # 呼び出し側の公開契約を生 OS 例外に晒さない。この時点で work_id は未知の
        # ため path のみを記録する。非 mapping の ValueError と yaml.YAMLError は
        # 他 loader（compose / arrange）との共通契約のためラップしない。
        raise IdentityManifestError(
            f"identity manifest unreadable at {manifest_path}: {exc}"
        ) from exc
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
    for anchor in manifest.anchors:
        target = f"anchor '{anchor.id}'"
        artifact_path = _resolve_confined(
            anchor.artifact, base_dir, work_id=work_id, target=target
        )
        _verify_artifact_hash(
            artifact_path,
            anchor.sha256,
            work_id=work_id,
            target=target,
        )

    return manifest


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"identity manifest must be a mapping: {path}")
    return data


def _resolve_confined(value: str, base_dir: Path, *, work_id: str, target: str) -> Path:
    """locator / artifact 文字列を base_dir 配下に閉じた実パスへ解決する。

    絶対パスは `base_dir / value` が base を黙って無視するため拒否する。
    `resolve()` は `../` と symlink の両方を追うため、解決後のパスが
    `base_dir` 配下にないものは manifest ディレクトリ脱出として拒否する
    （manifest の可搬性契約: artifact は常に親ディレクトリ相対）。
    """
    if Path(value).is_absolute():
        raise IdentityManifestError(
            f"identity manifest '{work_id}': {target} path {value!r} must be a "
            f"relative path inside the manifest directory (absolute paths are not allowed)"
        )
    base = base_dir.resolve()
    resolved = (base / value).resolve()
    if not resolved.is_relative_to(base):
        raise IdentityManifestError(
            f"identity manifest '{work_id}': {target} path {value!r} escapes the "
            f"manifest directory {base}: resolved to {resolved}"
        )
    return resolved


def _verify_artifact_hash(
    artifact_path: Path, expected_sha256: str, *, work_id: str, target: str
) -> None:
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
