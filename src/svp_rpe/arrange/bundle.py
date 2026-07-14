"""ArrangementSpec compile 中核: derived score / provenance bundle / diff の構築。

`compile_arrangement` は Base Score YAML と ArrangementSpec YAML のパスから、
`resolve_arrangement`（`arrange/resolver.py`、無変更）を用いて決定論的に
derived score YAML・provenance bundle JSON・diff JSON の 3 成果物をメモリ上に
構築する。ファイル書き込みは CLI 層（`cli.py` の `arrange` コマンド）が担当し、
本モジュールは一切ディスクへ書き込まない（CLI 非経由でもテスト可能にするため）。

provenance の思想: 入力ファイルの SHA-256 と CLI に渡されたパス文字列をそのまま
記録し、絶対パス化や推定補完は行わない（AGENTS.md §8 の「provenance は推定で
埋めない」原則に沿う）。identity_manifest 欄は AR2 スコープのため持たない。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from svp_rpe.arrange.loader import load_arrangement_spec
from svp_rpe.arrange.resolver import resolve_arrangement
from svp_rpe.compose.loader import load_composition_score
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


def compile_arrangement(score_path: Path | str, spec_path: Path | str) -> CompiledArrangement:
    """Base Score + ArrangementSpec から derived score / bundle / diff をメモリ上に構築する。

    CLI 非経由でもテスト可能な core API。`score_path` / `spec_path` は CLI に渡された
    文字列表現をそのまま provenance record の ``path`` として保持する（絶対パス化しない）。
    失敗時（存在しない入力・invalid YAML・未知 key・hard conflict・最終 Score validation
    失敗等）は `resolve_arrangement` / `load_composition_score` / `load_arrangement_spec`
    が送出する例外がそのまま伝播する（本関数はラップしない）。
    """
    score_path_str = str(score_path)
    spec_path_str = str(spec_path)

    source = load_composition_score(score_path)
    spec = load_arrangement_spec(spec_path)

    resolution = resolve_arrangement(source, spec)

    derived_score_yaml = render_score_yaml(resolution.derived_score)
    changes = [change.model_dump(mode="json") for change in resolution.changes]

    source_sha256 = sha256_file(Path(score_path))
    spec_sha256 = sha256_file(Path(spec_path))

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
