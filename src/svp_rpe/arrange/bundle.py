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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from svp_rpe.arrange.models import ArrangementSpec
from svp_rpe.arrange.resolver import resolve_arrangement
from svp_rpe.compose.models import CompositionScore

BUNDLE_SCHEMA_VERSION = "arrangement-bundle/0.1"
DIFF_SCHEMA_VERSION = "arrangement-diff/0.1"

DERIVED_SCORE_FILENAME = "derived_score.yaml"
BUNDLE_FILENAME = "arrangement_bundle.json"
DIFF_FILENAME = "arrangement_diff.json"


@dataclass(frozen=True)
class CompiledArrangement:
    """`compile_arrangement` の戻り値。書き込み前にメモリ上で完全構築済みの3成果物。"""

    derived_score_yaml: str
    bundle: dict[str, Any]
    diff: dict[str, Any]


def sha256_file(path: Path) -> str:
    """ファイルの raw bytes に対する SHA-256（小文字 hex）。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


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

    CLI 非経由でもテスト可能な core API。`score_path` / `spec_path` は CLI に渡された
    文字列表現をそのまま provenance record の ``path`` として保持する（絶対パス化しない）。
    各入力ファイルは 1 回だけ読み、sha256 計算とパースに同一バイト列を使う。
    失敗時（存在しない入力・invalid YAML・未知 key・hard conflict・最終 Score validation
    失敗等）は read / parse / `resolve_arrangement` が送出する例外がそのまま伝播する
    （本関数はラップしない。存在しない入力は `read_bytes` の `FileNotFoundError`）。
    """
    score_path_str = str(score_path)
    spec_path_str = str(spec_path)

    score_bytes = Path(score_path).read_bytes()
    spec_bytes = Path(spec_path).read_bytes()

    source = _parse_composition_score(score_bytes, score_path_str)
    spec = _parse_arrangement_spec(spec_bytes, spec_path_str)

    resolution = resolve_arrangement(source, spec)

    derived_score_yaml = render_score_yaml(resolution.derived_score)
    changes = [change.model_dump(mode="json") for change in resolution.changes]

    source_sha256 = hashlib.sha256(score_bytes).hexdigest()
    spec_sha256 = hashlib.sha256(spec_bytes).hexdigest()

    bundle: dict[str, Any] = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "arrangement_id": spec.meta.id,
        "source_score": {"path": score_path_str, "sha256": source_sha256},
        "arrangement_spec": {"path": spec_path_str, "sha256": spec_sha256},
        "changes": changes,
        "outputs": {
            "derived_score": DERIVED_SCORE_FILENAME,
            "arrangement_diff": DIFF_FILENAME,
        },
    }

    diff: dict[str, Any] = {
        "schema_version": DIFF_SCHEMA_VERSION,
        "arrangement_id": spec.meta.id,
        "changes": changes,
    }

    return CompiledArrangement(derived_score_yaml=derived_score_yaml, bundle=bundle, diff=diff)
