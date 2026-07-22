"""builds_root 配下の per-(variant, backend) 出力ディレクトリ解決 + 衝突ガード。

`recast/plan.py`（package の永続公開）と `recast/backend.py`（orders/takes の
公開）が共有する single source — ディレクトリ命名規約がこの 2 箇所で drift
しないようにする（Codex P2 review, PR3 #208 指摘 3 対応: package の公開先が
`builds_root/packages/<variant>@<backend>/` になったことで、3 種の出力
ディレクトリ（orders/takes/packages）の命名規約を 1 箇所に集約する必要が
生じた）。

`collect_protected_input_paths` もここに置く（`plan.py`/`backend.py` 双方が
呼ぶ必要があり、`backend.py` は `plan.py` を import するため `backend.py` 側に
置くと `plan.py` → `backend.py` の逆方向 import で循環する — 両者が安全に
import できる本モジュールへ集約する、PR3 #208 指摘 6/7 対応）。
"""
from __future__ import annotations

from pathlib import Path

import yaml

from svp_rpe.arrange.identity import IdentityManifest
from svp_rpe.arrange.pathsafe import resolve_confined
from svp_rpe.recast.loader import LoadedRecastProject
from svp_rpe.recast.models import RecastError


def run_key(variant: str, backend: str) -> str:
    return f"{variant}@{backend}"


def resolve_orders_dir(loaded: LoadedRecastProject, variant: str, backend: str) -> Path:
    """manual invoker が注文書 6 ファイルを公開する先。"""
    return loaded.builds_root / "orders" / run_key(variant, backend)


def resolve_takes_dir(loaded: LoadedRecastProject, variant: str, backend: str) -> Path:
    """生成済みテイクの収蔵先（manual collect / local invoke 共通）。"""
    return loaded.builds_root / "takes" / run_key(variant, backend)


def resolve_packages_dir(loaded: LoadedRecastProject, variant: str, backend: str) -> Path:
    """`build_recast_plan_artifacts` が compile 済み `PerformancePackage` /
    `CompilationReport` を公開する先（PR3 #208 指摘 3: tempfile 幾何依存を
    排し、`artifact_base_locator` を checkout-stable にするための永続公開先）。"""
    return loaded.builds_root / "packages" / run_key(variant, backend)


def collect_protected_input_paths(
    loaded: LoadedRecastProject, variant: str, backend: str
) -> list[Path]:
    """`(variant, backend)` run が参照する外部入力一式（project.yaml 自身 /
    score / identity manifest / manifest が参照する source・各 anchor
    artifact / arrangement spec / capability_profile / mode_overrides）を
    集める。

    recast の**全公開サイト**（orders 公開・manual collect の takes 公開・
    deterministic invoke の takes 公開・plan の packages 公開）が publish 前に
    これを渡し、出力ファイルパスがこれらのいずれとも衝突しないことを保証する
    共通規約（Codex P2 review, PR3 #208 指摘 2/6/7: 従来は orders 公開にしか
    適用されておらず、takes/packages 公開は無防備だった）。

    意図的に他ステージの出力（orders/takes/packages 自身のファイル）は含めない
    — 含めると、例えば packages 公開サイト自身がここを呼んで自分の出力先を
    チェックすると常に「自分自身と衝突している」と誤検出してしまう（自己参照
    パラドックス）。本関数が返すのはあくまで「recast の外側から来る入力」の
    集合であり、公開先ディレクトリが異なる限り（orders/takes/packages は
    `run_paths.py` の命名規約により常に別ディレクトリ）他ステージの出力との
    衝突はディレクトリ構造上そもそも起こらない。
    """
    paths: list[Path] = [
        loaded.path,
        loaded.score_path,
        loaded.identity_manifest_path,
        loaded.arrangement_paths[variant],
        loaded.capability_profile_paths[backend],
    ]
    mode_override_path = loaded.mode_override_paths.get(backend)
    if mode_override_path is not None:
        paths.append(mode_override_path)

    manifest_dir = loaded.identity_manifest_path.resolve().parent
    manifest_data = yaml.safe_load(loaded.identity_manifest_path.read_bytes())
    if not isinstance(manifest_data, dict):
        raise RecastError(
            f"identity manifest must be a mapping: {loaded.identity_manifest_path}"
        )
    manifest = IdentityManifest.model_validate(manifest_data)
    paths.append(resolve_confined(manifest.source.locator, manifest_dir))
    for anchor in manifest.anchors:
        paths.append(resolve_confined(anchor.artifact, manifest_dir))

    return paths
