"""tests/_shared_helpers.py — 複数テストファイルで byte-identical に重複していた
private ヘルパーの集約先（機械的リファクタ、挙動は不変）。"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import yaml


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


#: Recast デモプロジェクトの入力一式が置かれているディレクトリ。CWD 非依存に
#: するため `__file__` を起点に解決する（`cd tests && pytest test_x.py` のように
#: リポジトリルート以外から実行されても崩れない）。
DEMO_PROJECT = Path(__file__).resolve().parents[1] / "examples" / "recast" / "demo_project"


def copy_demo_project(tmp_path: Path, *, label: str | None = None) -> Path:
    """demo_project の入力一式（project/score/identity/arrangements）を
    tmp_path 配下へコピーする（`expected/` snapshot は意図的に除外 —
    テストが自由に破壊改変できる作業コピーに、比較専用の committed 期待値を
    混ぜない）。project.yaml への path を返す。

    `label` を渡すと `demo_project_<label>` へコピーする（同一テスト内で複数の
    独立した作業コピーを並存させたい呼び出し元向け）。省略時は従来通り
    `demo_project` 固定名。
    """
    dest = tmp_path / ("demo_project" if label is None else f"demo_project_{label}")
    dest.mkdir()
    shutil.copy(DEMO_PROJECT / "project.yaml", dest / "project.yaml")
    shutil.copy(DEMO_PROJECT / "composition_score.yaml", dest / "composition_score.yaml")
    shutil.copy(DEMO_PROJECT / "identity.yaml", dest / "identity.yaml")
    shutil.copytree(DEMO_PROJECT / "identity", dest / "identity")
    shutil.copytree(DEMO_PROJECT / "arrangements", dest / "arrangements")
    return dest / "project.yaml"
