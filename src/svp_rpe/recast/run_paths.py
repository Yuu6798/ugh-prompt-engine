"""builds_root 配下の per-(variant, backend) 出力ディレクトリ解決。

`recast/plan.py`（package の永続公開）と `recast/backend.py`（orders/takes の
公開）が共有する single source — ディレクトリ命名規約がこの 2 箇所で drift
しないようにする（Codex P2 review, PR3 #208 指摘 3 対応: package の公開先が
`builds_root/packages/<variant>@<backend>/` になったことで、3 種の出力
ディレクトリ（orders/takes/packages）の命名規約を 1 箇所に集約する必要が
生じた）。
"""
from __future__ import annotations

from pathlib import Path

from svp_rpe.recast.loader import LoadedRecastProject


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
