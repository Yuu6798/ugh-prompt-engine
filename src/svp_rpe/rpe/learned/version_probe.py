"""`rpe/learned` アダプタ共通のインストール済みバージョン検出ヘルパー。

7 つのアダプタ（crepe / basic_pitch / melodia(essentia) / beat_this / lyrics
(faster_whisper) / panns / clap(laion_clap)）が同一のフォールバックチェーン
（`sys.modules[<pkg>].__version__` → `importlib.metadata.version(<dist>)` →
`None`）を個別に実装していたため、ここに一本化する。各アダプタの
`_detect_<x>_version()` は本モジュールへの薄いラッパーとして残す
（他コード・テストからの参照/monkeypatch 互換のため）。
"""

from __future__ import annotations

import importlib.metadata as _pkg_metadata
import sys
from typing import Optional


def detect_installed_version(module_name: str, *, dist_name: Optional[str] = None) -> Optional[str]:
    """`module_name` のインストール済みバージョンをベストエフォートで検出する。

    フォールバックチェーン:

    1. `sys.modules[module_name]` が import 済みなら、その `__version__`
       属性（非空文字列のみ採用）
    2. `importlib.metadata.version(dist_name or module_name)`
       （PyPI 配布名がモジュール名と異なる場合は `dist_name` で指定する。
       例: basic-pitch は配布名がハイフン区切り）
    3. どちらも解決しなければ `None`
    """
    root = sys.modules.get(module_name)
    if root is not None:
        candidate = getattr(root, "__version__", None)
        if isinstance(candidate, str) and candidate:
            return candidate
    try:
        return _pkg_metadata.version(dist_name or module_name)
    except _pkg_metadata.PackageNotFoundError:
        return None
