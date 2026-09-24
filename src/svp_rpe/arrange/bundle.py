"""ArrangementSpec compile 中核: derived score / provenance bundle / diff の構築。

`compile_arrangement` は Base Score YAML と ArrangementSpec YAML のパスから、
`resolve_arrangement`（`arrange/resolver.py`、無変更）を用いて決定論的に
derived score YAML・provenance bundle JSON・diff JSON の 3 成果物をメモリ上に
構築する。ファイル書き込みは CLI 層（`cli.py` の `arrange` コマンド）が担当し、
本モジュールは一切ディスクへ書き込まない（CLI 非経由でもテスト可能にするため）。

provenance の思想: 入力ファイルの SHA-256 と CLI に渡されたパス文字列をそのまま
記録し、絶対パス化や推定補完は行わない（AGENTS.md §8 の「provenance は推定で
埋めない」原則に沿う）。identity_manifest 欄は AR2 スコープのため持たない。

各入力ファイルは **1 回だけ** `read_bytes` で読み、同一バイト列を sha256 計算と
YAML パースの両方に使う（PR #176 P2-1: 実行中に入力が差し替えられても
「derived の元データ」と「記録された sha256」が乖離しないことの保証）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from svp_rpe.arrange.models import ArrangementChange, ArrangementSpec
from svp_rpe.arrange.resolver import resolve_arrangement
from svp_rpe.compose.models import CompositionScore

BUNDLE_SCHEMA_VERSION = "arrangement-bundle/0.2"
DIFF_SCHEMA_VERSION = "arrangement-diff/0.1"

DERIVED_SCORE_FILENAME = "derived_score.yaml"
BUNDLE_FILENAME = "arrangement_bundle.json"
DIFF_FILENAME = "arrangement_diff.json"

# content_digest の定義（arrange / package で統一。Design Memo §2）:
# sha256(canonical_json({artifact_name: sha256_hex, ...}))。canonical_json は
# `semantic_ci/core.py:canonical_json` と同型思想（sort_keys=True + 圧縮
# separators）だが、`arrange/` は `semantic_ci` に依存しないためここで自前実装する。
CONTENT_DIGEST_BASIS = "sha256-of-canonical-json-artifact-name-to-sha256/v1"

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class BundleReaderModel(BaseModel):
    """`arrangement_bundle.json` 読み取り専用スキーマの共通基底。未知 key を拒否する。

    **reader 専用**: `compile_arrangement`（生成側）はこのモデルを一切経由しない
    — dict をそのまま構築し続け、byte 互換を厳密に維持する（既存 fixture /
    CLI 出力の bytes を一切変更しない）。当初の Design Memo は
    「`ArrangementBundle` のモデル化は読む側の需要が未発生」として scope out
    していたが、builds-root の blessing 経路
    （`cli.py:_check_existing_builds_root_publication`、PR #184 review round 9）
    という reader が実際に現れたことでこの設計判断を解除する。
    """

    model_config = ConfigDict(extra="forbid")


class BundleFileRef(BundleReaderModel):
    """bundle 内の `{path, sha256}` 参照（`source_score` / `arrangement_spec` /
    `outputs` の各エントリに共通する形）。"""

    path: str
    sha256: str = Field(pattern=_SHA256_PATTERN)


class ArrangementBundleDescriptor(BundleReaderModel):
    """`arrangement_bundle.json`（schema `arrangement-bundle/0.2`）の読み取り専用モデル。

    builds-root の blessing 経路が、既存 digest ディレクトリの descriptor
    全体をスキーマ検証するために使う（whitelist 欄の形状も含め pydantic の
    型/pattern で保証する）。`changes` は `resolve_arrangement` が返す
    `ArrangementChange` をそのまま再利用する（生成側と読み取り側で同一の
    leaf-change 表現を共有し、二重定義を避ける）。
    """

    schema_version: Literal["arrangement-bundle/0.2"]
    arrangement_id: str
    source_score: BundleFileRef
    arrangement_spec: BundleFileRef
    changes: list[ArrangementChange]
    outputs: dict[str, BundleFileRef]
    content_digest: str = Field(pattern=_SHA256_PATTERN)
    content_digest_basis: Literal["sha256-of-canonical-json-artifact-name-to-sha256/v1"]


@dataclass(frozen=True)
class CompiledArrangement:
    """`compile_arrangement` の戻り値。書き込み前にメモリ上で完全構築済みの3成果物。"""

    derived_score_yaml: str
    bundle: dict[str, Any]
    diff: dict[str, Any]


def sha256_file(path: Path) -> str:
    """ファイルの raw bytes に対する SHA-256（小文字 hex）。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compute_content_digest(artifact_hashes: dict[str, str]) -> str:
    """`{artifact_name: sha256_hex}` の canonical JSON に対する SHA-256。

    `content_digest_basis` = `CONTENT_DIGEST_BASIS` が示す定義そのもの。
    bundle / report は自身の記述対象（self-hash 不能）なので `artifact_hashes`
    に含めない — `outputs` / `package_sha256` と同じ理由（report の docstring
    参照）。
    """
    canonical = json.dumps(artifact_hashes, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def render_score_yaml(score: CompositionScore) -> str:
    """CompositionScore を決定論的 YAML へレンダリングする（`svp/render_yaml.py` と同型の規約）。"""
    data = score.model_dump(mode="json", exclude_none=True)
    return yaml.safe_dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _parse_yaml_mapping(raw_bytes: bytes, label: str, path_str: str) -> dict[str, Any]:
    """raw bytes を YAML mapping としてパースする（非 mapping は loader と同文の ValueError）。"""
    data = yaml.safe_load(raw_bytes)
    if not isinstance(data, dict):
        raise ValueError(f"{label} must be a mapping: {path_str}")
    return data


def _parse_composition_score(raw_bytes: bytes, path_str: str) -> CompositionScore:
    data = _parse_yaml_mapping(raw_bytes, "composition score", path_str)
    return CompositionScore.model_validate(data)


def _parse_arrangement_spec(raw_bytes: bytes, path_str: str) -> ArrangementSpec:
    data = _parse_yaml_mapping(raw_bytes, "arrangement spec", path_str)
    return ArrangementSpec.model_validate(data)


def compile_arrangement(score_path: Path | str, spec_path: Path | str) -> CompiledArrangement:
    """Base Score + ArrangementSpec から derived score / bundle / diff をメモリ上に構築する。

    CLI 非経由でもテスト可能な core API。`score_path` / `spec_path` は絶対パス化せず、
    provenance record の ``path`` では区切りを POSIX 形式に正規化する。
    各入力ファイルは 1 回だけ読み、sha256 計算とパースに同一バイト列を使う。
    失敗時（存在しない入力・invalid YAML・未知 key・hard conflict・最終 Score validation
    失敗等）は read / parse / `resolve_arrangement` が送出する例外がそのまま伝播する
    （本関数はラップしない。存在しない入力は `read_bytes` の `FileNotFoundError`）。
    """
    score_path_obj = Path(score_path)
    spec_path_obj = Path(spec_path)
    score_path_str = score_path_obj.as_posix()
    spec_path_str = spec_path_obj.as_posix()

    score_bytes = score_path_obj.read_bytes()
    spec_bytes = spec_path_obj.read_bytes()

    source = _parse_composition_score(score_bytes, score_path_str)
    spec = _parse_arrangement_spec(spec_bytes, spec_path_str)

    resolution = resolve_arrangement(source, spec)

    derived_score_yaml = render_score_yaml(resolution.derived_score)
    changes = [change.model_dump(mode="json") for change in resolution.changes]

    source_sha256 = hashlib.sha256(score_bytes).hexdigest()
    spec_sha256 = hashlib.sha256(spec_bytes).hexdigest()

    diff: dict[str, Any] = {
        "schema_version": DIFF_SCHEMA_VERSION,
        "arrangement_id": spec.meta.id,
        "changes": changes,
    }

    # outputs の sha256 は「公開されるバイト列そのもの」から計算する（TOCTOU 先取り
    # の同一バイト列規律）。diff は CLI が書き込む際と同じ
    # `json.dumps(..., ensure_ascii=False, indent=2)` で先にシリアライズしてから
    # hash する — 同一 dict は同一 bytes へ決定論的にシリアライズされるため、CLI
    # 側で改めて `json.dumps` した publish 済みバイト列と本 hash は常に一致する。
    derived_score_sha256 = hashlib.sha256(derived_score_yaml.encode("utf-8")).hexdigest()
    diff_json = json.dumps(diff, ensure_ascii=False, indent=2)
    diff_sha256 = hashlib.sha256(diff_json.encode("utf-8")).hexdigest()

    content_digest = compute_content_digest(
        {
            DERIVED_SCORE_FILENAME: derived_score_sha256,
            DIFF_FILENAME: diff_sha256,
        }
    )

    bundle: dict[str, Any] = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "arrangement_id": spec.meta.id,
        "source_score": {"path": score_path_str, "sha256": source_sha256},
        "arrangement_spec": {"path": spec_path_str, "sha256": spec_sha256},
        "changes": changes,
        "outputs": {
            "derived_score": {"path": DERIVED_SCORE_FILENAME, "sha256": derived_score_sha256},
            "arrangement_diff": {"path": DIFF_FILENAME, "sha256": diff_sha256},
        },
        "content_digest": content_digest,
        "content_digest_basis": CONTENT_DIGEST_BASIS,
    }

    return CompiledArrangement(derived_score_yaml=derived_score_yaml, bundle=bundle, diff=diff)
