"""run_melody_accuracy.py — M2a 抽出精度検証ハーネス（run/evaluate 二相）。

設計: `docs/DESIGN_M2_extraction_accuracy.md`（M2）。M1 が問うたのは「旋律らしき
系列が観測できるか」、M2 が問うのは「取れた系列は正しいか」。本ハーネスは
カテゴリ S（`tests/fixtures/melody_bench/m2_accuracy_specs.yaml` の決定論合成・
spec がそのまま正解）に対して `svp_rpe.melody.accuracy` の mir_eval ラッパを適用し、
RPA/RCA/VR/VFA/OA + 中央値 cent 誤差を算出する。**旋律同士の比較（M3）・Recast
配線（M4）はやらない。**

`scripts/run_melody_observability.py`（M1）と同型の 2 phase 構成:

- **run**（`--out`）: fixture を合成し、経路（既定 = `svp_rpe.melody.extractors`
  経由の実抽出器。crepe 未導入なら route 単位で ``unavailable``）に通して
  report JSON を書く。抽出器非依存: 経路 → f0 系列取得は `route_runner` 引数
  （既定 `observe_via_route_with_provenance`）に委譲するので、フェイク抽出器を
  注入したハーネス単体テストが実行時 DL やモデル導入なしに回る。
- **evaluate**（`--evaluate report1.json [report2.json ...]`）: n>=2 repeats の
  run report に対し `tests/fixtures/melody_bench/m2_accuracy_bars.yaml`
  （設計 §4 で実測前に凍結したバー。**`registry.yaml` とは別ファイル** — 理由は
  当該 YAML のコメント参照）を機械適用し pass/fail/diagnostic_only を出す。

対象外（設計 §8）:

- カテゴリ X（正解なし自作音源）の RPA/RCA 算出。本ハーネスの `run_accuracy` は
  カテゴリ S（合成正解つき）専用で、正解なし音声を受理する引数・経路が存在しない。
- melodia 経路（#222 裁定待ち）。
- 総合 OA 単独の合否判定（`overall_accuracy` は参考記録フィールドとして row に
  残すのみで、バー判定には使わない）。

使い方::

    python scripts/run_melody_accuracy.py --out /tmp/m2_run1.json
    python scripts/run_melody_accuracy.py --out /tmp/m2_run2.json
    python scripts/run_melody_accuracy.py --evaluate /tmp/m2_run1.json /tmp/m2_run2.json \\
        --out /tmp/m2_verdict.json
"""
from __future__ import annotations

import argparse
import ast
import concurrent.futures
import copy
import functools
import hashlib
import json
import math
import multiprocessing
import os
import platform
import re
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _force_checkout_roots_first() -> None:
    """本 checkout の src / scripts を sys.path の先頭へ**無条件に**移動する。

    `if str(SRC) not in sys.path` の存在チェックだけでは、PYTHONPATH に別 checkout の
    src が先・本 checkout の src が後ろで並ぶ環境で前置がスキップされ、**実行は別
    checkout のモジュール・hash は ROOT 配下のファイル**という乖離が生じる（Codex P1
    第 25 巡）。preload ゲートは import 済みの場合しか捕まえず、測り直しサブプロセスも
    同じ環境を継承して同様に乖離するため一致してしまう。既存の出現を取り除いた上で
    先頭へ挿入し、モジュールスナップショット凍結より前に順序を確定させる。
    """
    for entry in (str(SRC), str(ROOT / "scripts")):
        while entry in sys.path:
            sys.path.remove(entry)
    sys.path.insert(0, str(SRC))
    sys.path.insert(0, str(ROOT / "scripts"))


_force_checkout_roots_first()


def _force_fresh_bytecode() -> None:
    """以後の import を「ディスク上のソース bytes からの再コンパイル」に固定する。

    CPython の既定の `.pyc` 検証はタイムスタンプ + サイズなので、first-party の
    `.py` を同サイズ・同 mtime で差し替えると **stale bytecode が実行される一方、
    pin（`_generator_code_sha256`）は新しいソースを hash する**乖離が生じる
    （Codex P2 第 33 巡）。存在しない一意な `pycache_prefix` を設定して既存
    `__pycache__` を参照させず（キャッシュ不在 = 必ずソースからコンパイル）、
    `dont_write_bytecode` で書き込みも止める（一時 prefix への堆積を防ぐ）。

    本モジュール自身の bytecode はこのコードが走る時点で既にロード済み = ここでは
    束縛できないが、publish の最終根拠である測り直し子プロセスには
    `_run_verification_in_fresh_process` が同じ環境変数を渡すため、そこでは
    ハーネス自身も含めてソースから再コンパイルされる。stale bytecode で測った
    親 run は、子プロセスの fresh 実行と bit 一致しない限り publish されない。
    """
    sys.pycache_prefix = str(
        Path(tempfile.gettempdir()) / f"m2-pyc-{os.getpid()}-{uuid.uuid4().hex}"
    )
    sys.dont_write_bytecode = True


_force_fresh_bytecode()

# 本ハーネスが「直接パスの top-level script」として実行されたか。CPython は直接
# 実行される script に .pyc を**使わない**（毎回ソースからコンパイルする）ため、
# この形の実行だけが「実行 bytecode = ディスクのソース bytes」を構造的に保証する。
# import 経由（`python -m` / `import` + 呼び出し）は stale .pyc から実行されうる
# （Codex P2 第 34 巡）ので、run report に記録し evaluate が publishable 要件として
# True を要求する。判定は `__name__` だけでは足りない——**`python -m` も
# `__name__ == "__main__"` になる**が、`-m` は import 機構（= .pyc キャッシュ）を
# 通る（Codex P2 第 35 巡・実測確認済みの指摘）。直接ファイル実行では `__main__` の
# `__spec__` が None、`-m` では ModuleSpec が設定される、という CPython の公式な
# 区別で構造的に判定する。
_HARNESS_LOADED_AS_MAIN: bool = __name__ == "__main__" and globals().get("__spec__") is None

# 本ハーネスが読み込まれた瞬間の sys.modules。**この行より上に first-party / 計測
# 関連の import は 1 つも無い**ため、ここに写っている名前は「別経路が先に読み込んだ
# もの」だけである。監視集合（`_runtime_package_names`）は登録表からの導出のために
# `svp_rpe.melody.provenance` を import するので、集合の導出より前にスナップショット
# を凍結しておかないと、自分の import が「事前ロード」として写り込む（自己汚染）。
_SYS_MODULES_AT_LOAD: "frozenset[str]" = frozenset(sys.modules)

# --- provenance pin（あらゆる first-party import より前に確定させる・#217）--------
# ここから下の import が走る前にディスク状態を pin する。import 後に計算すると、
# 別経路で先に読み込まれていた旧モジュールが実行される一方 hash は新しいディスクを
# 見る、という窓が開く（Codex P1）。閉包計算は find_spec のみで import を起こさない。
_FIRST_PARTY_ROOTS: "Tuple[Path, ...]" = (SRC.resolve(), (ROOT / "scripts").resolve())


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _first_party_module_file(module_name: str) -> "Path | None":
    """`module_name` が first-party（`src/svp_rpe` or `scripts` 配下）なら resolved パスを返す。

    **`importlib.util.find_spec` は使わない**。ドット名を渡すと親パッケージを
    *実行* するため（`svp_rpe.melody.accuracy` の解決で `svp_rpe.melody.__init__` が
    走り、それが `observability` / `routing` を import する）、hash より前に import が
    起きて「旧モジュールが実行され digest は新しいディスクを見る」窓が開く
    （Codex P1・実測で確認）。first-party のルートは既知なので、ドット名を
    ディレクトリ階層へ直接写して存在確認する——これなら import はゼロ。

    stdlib / third-party / 解決不能な名前は None（ルート外なので写像が当たらない）。
    optional 重み依存（crepe 等）も None になるため閉包に混入せず、digest は
    どのマシンでも決定論的に安定する。
    """
    parts = module_name.split(".")
    if not all(parts):
        return None
    for root in _FIRST_PARTY_ROOTS:
        base = root.joinpath(*parts)
        module_file = base.with_suffix(".py")
        if module_file.is_file():
            return module_file.resolve()
        package_init = base / "__init__.py"
        if package_init.is_file():
            return package_init.resolve()
    return None


_SEED_MODULE_NAMES: "Tuple[str, ...]" = (
    "svp_rpe.melody.accuracy",
    "svp_rpe.melody.extractors",
    "svp_rpe.melody.observability",
    "svp_rpe.melody.routing",
    "svp_rpe.rpe.learned.crepe_adapter",
    "svp_rpe.rpe.learned.source_separation_adapter",
    "svp_rpe.io.source_separator",
)


def _generator_code_paths() -> List[Path]:
    """観測を実際に産む first-party 呼び出しグラフの閉包（AST import 走査）。

    本ハーネスと `accuracy.py` だけを hash すると、`melody/extractors.py` /
    `melody/routing.py` / `rpe/learned/crepe_adapter.py` などが変わっても digest が
    動かず、旧コードで測った row を現行バーの証拠として通してしまう（Codex P1）。
    row の `extractor_code_sha256` はサードパーティ推論パッケージの pin であって、
    この first-party オーケストレーションは覆わない。

    **seed をモジュール名で持ち、`find_spec` で場所だけ解決する**（`provenance.
    package_code_state` と同じ #217 の規律）。seed をモジュールオブジェクトで受け取ると
    閉包計算のために import が起き、「import 済みの旧コードが実行され hash は新しい
    ディスクを見る」窓ができてしまう。名前解決なら本関数を**あらゆる first-party
    import より前**に呼べる。

    `run_melody_observability._generator_code_paths` と同型だが、あちらを import すると
    M1 ハーネスのコードが本閉包に入り、M1 側の変更で M2 の report が stale 化する
    （逆も同様）ため、独立に実装する。

    **祖先パッケージの `__init__.py` も閉包に含める**（Codex P2 第 32 巡）:
    `svp_rpe.melody.accuracy` の import は `svp_rpe/__init__.py` と
    `svp_rpe/melody/__init__.py` を必ず実行するが、AST 走査は明示 import された
    名前しか辿らないため、祖先 initializer の変更が digest に写らず「別の
    first-party bytes を実行したのに同一 generator provenance」を主張できた。
    ドット名を解決するたびに、その全祖先の `__init__.py` も対象に加える。
    """

    def _files_with_ancestors(module_name: str) -> List[Path]:
        parts = module_name.split(".")
        files: List[Path] = []
        if not all(parts):
            return files
        for depth in range(1, len(parts) + 1):
            target = _first_party_module_file(".".join(parts[:depth]))
            if target is not None:
                files.append(target)
        return files

    stack: List[Path] = [Path(__file__).resolve()]
    for name in _SEED_MODULE_NAMES:
        stack.extend(_files_with_ancestors(name))

    resolved: "set[Path]" = set()
    while stack:
        file = stack.pop()
        if file in resolved or not file.exists():
            continue
        resolved.add(file)
        try:
            tree = ast.parse(file.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        candidates: "set[str]" = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    candidates.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                # 相対 import は非採用（本リポジトリは絶対 import 規約）。
                if node.level == 0 and node.module:
                    candidates.add(node.module)
                    for alias in node.names:
                        candidates.add(f"{node.module}.{alias.name}")
        for name in candidates:
            for target in _files_with_ancestors(name):
                if target not in resolved:
                    stack.append(target)
    return sorted(resolved)


def _generator_code_sha256() -> str:
    """観測を産む first-party コード閉包の digest。

    学習モデル本体（重み）の pin は対象にしない——そちらは row の
    `provenance_extractor_weights_sha256` / `preprocessing.*` が担う。ここが pin する
    のは「row を産んだ first-party のロジック（指標算出・route 選択・抽出器
    オーケストレーション・ミックス式）」である。
    """
    digest = hashlib.sha256()
    for path in _generator_code_paths():
        # repo 相対パスを混ぜ、同名ファイルの取り違えを防ぐ（checkout 非依存）。
        try:
            label = path.relative_to(ROOT).as_posix()
        except ValueError:
            label = path.name
        digest.update(label.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _closure_module_names() -> List[str]:
    """`_generator_code_paths()` が hash する全ファイルを import 名へ逆写像する。

    seed 名だけを監視すると、閉包に入っている推移的モジュール（`svp_rpe.utils.hashing`
    など）の事前ロードを見逃す（Codex P1）。digest の対象と監視対象を同じ集合から
    導出することで、この非対称が構造的に生じないようにする。
    """
    names: List[str] = []
    for path in _generator_code_paths():
        for root in _FIRST_PARTY_ROOTS:
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            parts = list(rel.parts)
            if parts[-1] == "__init__.py":
                parts = parts[:-1]
            else:
                parts[-1] = parts[-1][: -len(".py")]
            if parts:
                names.append(".".join(parts))
            break
    return sorted(set(names))


def _preloaded_seed_modules() -> List[str]:
    """本ハーネスが読み込まれる時点で既に import 済みだった監視対象モジュール。

    非空なら「メモリ上のコード」と「今 hash したディスクの bytes」が食い違いうる
    ——別経路が先に古い checkout のモジュールを読み込んでいた可能性を排除できない
    （Codex P1）。CLI から素で起動した実測 run では空になる。ここに載った run は
    `evaluate` が publish 不可として弾く。

    監視集合は **digest 閉包そのもの**（推移的モジュールを含む）+ 推論を担う
    ランタイムパッケージから導出する。seed 名だけでは閉包の一部を見逃す。

    さらに **祖先パッケージ**（`svp_rpe` / `svp_rpe.melody` 等）も監視する。
    `sys.path` の並べ替え（`_force_checkout_roots_first`）は import 済みパッケージの
    `__path__` を書き換えないため、別 checkout のトップレベル `svp_rpe` だけを先に
    import されると、以後の `svp_rpe.melody.*` はその外部 `__path__` から実行される
    のに、子モジュール名しか監視しない集合ではゲートが空のままになる（Codex P1
    第 26 巡）。同 checkout の親キャッシュでも stale メモリ窓は同型に生じるため、
    パス照合ではなく他のゲートと同じ無条件拒否に倒す。

    判定は `_SYS_MODULES_AT_LOAD`（本モジュールの先頭で凍結した sys.modules）に対して
    行う。現在の `sys.modules` を見ると、監視集合を導出するための
    `svp_rpe.melody.provenance` import 自体が「事前ロード」として写り込む。
    """
    watched = set(_closure_module_names()) | set(_SEED_MODULE_NAMES) | set(_runtime_package_names())
    for name in tuple(watched):
        parts = name.split(".")
        for depth in range(1, len(parts)):
            watched.add(".".join(parts[:depth]))
    return sorted(name for name in watched if name in _SYS_MODULES_AT_LOAD)


# 閉包 digest を **あらゆる first-party import より前に** 確定させる。
# `_generator_code_paths` は find_spec を使わずパス写像だけなので import を起こさない
# （`provenance.package_code_state` と同じ #217 の規律）。以降 `run_accuracy` はこの値を
# pin として使い、実行後に再計算して一致を確認する（`_require_unchanged_since_load`）。
# 事前ロード一覧（`_PRELOADED_SEED_MODULES`）は監視集合の導出に登録表の import を要する
# ため、下の import 群が済んだ後で確定させる——判定基準の `_SYS_MODULES_AT_LOAD` は
# 既に凍結済みなので、評価位置が後になっても値は変わらない。
_LOADED_GENERATOR_CODE_SHA256 = _generator_code_sha256()


_ALLOWED_SCORER_LOADER_QUALNAME = "_frozen_importlib_external.SourceFileLoader"

# セルフレビュー第二弾 H13: H8 の origin/loader 検査はトップレベルパッケージ名
# （`_SCORER_RUNTIME_PACKAGES`）だけを見ていたが、実際に RPA/RCA/median cent error
# を計算するのは `mir_eval` トップレベルの `__init__.py` ではなく、この 2 サブ
# モジュール——トップレベルの origin/loader が無傷でも、これらの swap-and-restore
# は検出できなかった。
_SCORER_KERNEL_SUBMODULES: "Tuple[str, ...]" = ("mir_eval.melody", "mir_eval.util")


def _check_module_matches_pinned_origin(name: str, *, pinned_origin: str) -> None:
    """`name`（`sys.modules` に載っている前提）の origin/loader を束縛時の期待と照合する。

    トップレベルパッケージ（`_require_scorer_modules_match_pinned_origin`）とカーネル
    サブモジュール（`_require_scorer_kernel_submodules_match_pinned_origin`、
    セルフレビュー第二弾 H13）の両方が共有する検査本体。`name` は `sys.modules` の
    キー（ドット付きサブモジュール名も可）としてのみ使う——呼び出し側が既に
    `sys.modules.get(name) is not None` を確認済みであることが前提。
    """
    module = sys.modules[name]
    module_spec = getattr(module, "__spec__", None)
    module_origin = getattr(module_spec, "origin", None) if module_spec else None
    if not module_origin or module_origin in ("built-in", "frozen"):
        raise RuntimeError(
            f"evaluate_m2_bars: import 済み {name!r} の __spec__.origin を解決でき"
            "ない; 束縛時の origin と突き合わせられないため fail-closed"
        )
    actual_origin = str(Path(module_origin).resolve())
    if actual_origin != pinned_origin:
        raise RuntimeError(
            f"evaluate_m2_bars: import 済み {name!r} の実行対象 {actual_origin} が"
            f" 束縛時に解決した {pinned_origin} と別; swap-and-restore の疑いがあり"
            "pin が実行された bytes を代表する保証がない (fail-closed)"
        )
    loader = getattr(module_spec, "loader", None)
    loader_qualname = _qualname_of(loader) if loader is not None else None
    if loader_qualname != _ALLOWED_SCORER_LOADER_QUALNAME:
        raise RuntimeError(
            f"evaluate_m2_bars: import 済み {name!r} の loader が "
            f"{loader_qualname!r}（期待: {_ALLOWED_SCORER_LOADER_QUALNAME!r}）; "
            "sourceless .pyc 等の非標準 loader 経路で実行された疑いがあり "
            "fail-closed"
        )


def _require_scorer_modules_match_pinned_origin() -> None:
    """post-run: import 済み scorer module の実行対象が束縛時の origin と一致するかを検証する。

    （セルフレビュー H8、第二弾 H13 でカーネルサブモジュールへ拡張）束縛
    （`_scorer_pins()`）は `import numpy`/`mir_eval` より前に行われる正しい順序だが、
    束縛後に「実行された module オブジェクトが pin したファイル由来か」を確認する
    仕掛けがこれまで無かった。`_require_unchanged_since_load()`（ディスクの再
    hash）は**ディスク上の bytes**しか見ないため、以下は素通りする: (a) hash 後・
    import 前に site-packages を差し替え、import 後に元へ戻す（swap-and-restore）
    ——pre/post の digest は一致するが実行だけが別 bytes。入力 WAV に対しては
    fd hash + `chmod 0400`（`_sha256_of_fd` 経由）でこの窓を塞いだが、コードに
    対しては同じ防御が無かった。

    ここでは import 済み（`sys.modules` に載っている）scorer module について
    `__spec__.origin` が束縛時に `find_spec` で解決した origin
    （`_SCORER_PINNED_ORIGINS`）と一致することを要求する。加えて `__spec__.loader`
    の型が `SourceFileLoader` であることも要求する（H2 の sourceless `.pyc` 検査を
    ディスク走査だけでなく実行時の loader 型でも defense-in-depth する）。
    トップレベルパッケージに続けて `_SCORER_KERNEL_SUBMODULES`（`mir_eval.melody`/
    `mir_eval.util`——実際に RPA/RCA/median cent error を計算するサブモジュール
    そのもの）も同じ検査にかける——トップレベル `mir_eval/__init__.py` の origin が
    無傷でも、指標カーネルの swap-and-restore は別途これで捕捉する。

    **境界の正直会計**: この検査は「読み込まれた module オブジェクトの出自」しか
    見ない。sitecustomize 等がロード**後**に `mir_eval.melody.evaluate` 自体を
    monkeypatch（メモリ上の関数オブジェクト差し替え）する攻撃は、`__spec__.origin`
    が無傷のまま起こりうるため対象外——同 uid 攻撃者がプロセスメモリを直接書ける
    境界は本 PR が既に明示的に線引き済み（fd hash 保護と同じ前提）。まだ import
    されていないパッケージ（run 経路によっては charset_normalizer 等が実際には
    呼ばれないことがありうる）は静かにスキップする——import されていない module は
    実行されてもいないので、origin の不一致という懸念自体が発生しない。
    """
    for name in _SCORER_RUNTIME_PACKAGES:
        if name not in sys.modules:
            continue
        pinned_origin = _SCORER_PINNED_ORIGINS.get(name)
        if pinned_origin is None:
            continue  # 束縛時点で origin を解決できなかった（walk 対象外）
        _check_module_matches_pinned_origin(name, pinned_origin=pinned_origin)
    for dotted in _SCORER_KERNEL_SUBMODULES:
        if dotted not in sys.modules:
            continue  # まだ import されていない（run 経路によってはありうる）
        pinned_origin = _SCORER_KERNEL_SUBMODULE_PINNED_ORIGINS.get(dotted)
        if pinned_origin is None:
            continue  # 束縛時点でパス構築できなかった（mir_eval 自体が未解決等）
        _check_module_matches_pinned_origin(dotted, pinned_origin=pinned_origin)


# swap-and-restore への in-memory bytes 束縛（Codex 10 巡目 P1-B、11 巡目 P1-A で
# 検出経路を是正）。
# `_require_scorer_modules_match_pinned_origin`（H8）は `__spec__.origin`（パス）の
# 一致しか見ないため、「差し替え → import（compile）→ 元へ復元」という攻撃は
# origin パスが無傷のまま素通りする——bind 時・post-run 時のどちらのディスク hash も
# 「復元済みの原本」を見るだけで、実際に compile され実行された悪意ある bytes を
# 一度も観測しない。
#
# **11 巡目 P1-A: ディスク再読ではなく compile イベントの source 引数そのものを
# hash する**。10 巡目時点の実装は `"compile"` イベント発火時に**ディスクを
# 読み直して** hash していたが、これは「差し替え → loader が読取 → 元へ復元 →
# hook がディスクを再読」という順序では**復元後の bytes**を見てしまい、
# swap-and-restore を検出できない（hook の再読はローダの読取より**後**に走る
# ため、攻撃者が hook 発火前に復元を完了させれば素通りする）。`compile()` の
# audit イベント引数 `args[0]`（`source`）は、ローダが実際に `compile()` へ
# 渡した**その bytes そのもの**——実測（本ハーネス環境で `sys.addaudithook` の
# `"compile"` イベントを実際に観測）で確認済み: `SourceFileLoader` 経由の
# module compile では `source` は `bytes`（`get_data()` が返す生バイト列と同一、
# デコード前）である。これを直接 hash すれば、ディスクの読み直しという中間
# ステップ自体が不要になり、TOCTOU 窓は「ローダ自身の `get_data()` 呼び出しと
# `compile()` 呼び出しの間」という CPython の import 機構内部の逐次呼び出し
# だけに縮む（ここは同一 C 呼び出しシーケンス内でユーザーコードが割り込む余地が
# 事実上無い）——ディスク再読方式が持っていた「hook 発火が復元後になりうる」
# という窓を根本的に閉じる。
_SCORER_LOAD_TIME_EXPECTED_HASHES: Dict[str, str] = {}

# audit hook が検出した「compile された source bytes が束縛時点の期待と不一致」の
# 記録（2 段構え・`_PRE_BOUND_NATIVE_MAPPING_LOG` と同型）。audit hook 内から
# raise すると CPython の仕様上その compile 自体を中断してしまう（正当な scorer
# import を壊す）ため、ここには常に**記録するだけ**——fail-closed は実測経路
# （`evaluate_m2_bars` 自己ゲート・report フィールド `scorer_load_time_hash_
# mismatches`）に委ねる。
_SCORER_LOAD_TIME_HASH_MISMATCHES: List[str] = []

# セルフレビュー第二弾 H16: audit hook が実際に "compile" を観測した（かつ
# `_SCORER_LOAD_TIME_EXPECTED_HASHES` に載っていた）ファイルの resolved パス集合。
# H13（symlink 化 site-packages での照合ミス）や期待値表が何らかの理由で空/部分に
# なった場合、`scorer_load_time_hash_mismatches` は「観測ゼロ」でも「無改変」でも
# 同じく `[]` になり区別が付かない——`evaluate_m2_bars` はこの集合が「実際に import
# された scorer モジュールの期待集合」を覆っていることを別途要求する
# （`_require_scorer_compile_observation_covers_imported_modules`）。
_SCORER_COMPILE_OBSERVED_PATHS: "set[str]" = set()


def _scorer_load_time_expected_hashes() -> "Dict[str, str]":
    """束縛時点（compile 前）の scorer `.py` ファイルごとの期待 sha256 を確定する。

    `_mir_eval_paths()`（`_SCORER_RUNTIME_PACKAGES` 閉包の全 `.py` ファイル、9 巡目
    P2 で単一モジュール配布の走査限定込みで確定済み）をそのまま再利用する——audit
    hook が捕捉する `"compile"` イベントは `.py` ソースの compile 呼び出しそのもの
    なので、対象集合はこれで過不足ない（native `.so` は「compile」されない=対象外、
    既存の DT_NEEDED 閉包/pre-bound mapping 検査が別途担う）。

    `file_sha256(..., use_cache=True)` を使う: この呼び出し時点で直前の
    `_scorer_pins()`（`package_code_sha256` 経由で同じ `.py` ファイル群を既に読んで
    `_FILE_DIGEST_CACHE` を温めている）が完了済みのため、ここでの呼び出しは
    ほぼ追加のディスク I/O を発生させない（bind 時点で既に払ったコストを読み出す
    だけ）——`_mir_eval_paths()` が返す全ファイル（実測: 数百〜千数百）を独立に
    再度ハッシュするコストを新たに追加しない。
    """
    from svp_rpe.utils.hashing import file_sha256

    hashes: "Dict[str, str]" = {}
    for path in _mir_eval_paths():
        if path.suffix != ".py":
            continue  # audit hook は "compile" イベント（.py ソース）専用
        try:
            hashes[str(path)] = file_sha256(path, use_cache=True)
        except OSError:
            continue  # 読めない = 比較対象にしない（無い物は比べない・#217 原則）
    return hashes


def _audit_scorer_source_load_time_hash(event: str, args: "Tuple[Any, ...]") -> None:
    """`sys.addaudithook` コールバック: scorer `.py` の compile event の source bytes を直接 hash する。

    （Codex 10 巡目 P1-B、11 巡目 P1-A で検出経路を是正）`sys.addaudithook` で
    登録したフックは CPython の仕様上**除去できない**——監視対象の import 経路を
    迂回させる手段が無く、`sys.meta_path` 改変（H3）とは異なる独立したタンパー
    耐性を持つ。

    **どのイベントを使うか**: `"compile"` イベント（`compile()` 組み込みが発火、
    引数は `(source, filename)`）を使う。実測で確認済み（本 review 対応時）:
    本ハーネスは冒頭で `_force_fresh_bytecode()` を呼び `sys.pycache_prefix` を
    非存在パスへ強制しているため、本ハーネス自身の実行中に import される scorer
    モジュールは**必ず** `.py` ソースから compile される（stale `.pyc` 再利用の
    余地が無い）——よって `"compile"` イベントは本ハーネスの制御下にある import に
    ついて確実に発火する。`"import"` イベントは実測で `filename` 引数が常に
    `None` だったため（本 review 対応時の実測）、ファイル特定に使えず採用しない。

    **11 巡目 P1-A: `source`（`args[0]`）を直接 hash する（ディスク再読は廃止）**。
    10 巡目時点は `"compile"` 発火時にディスクを**読み直して** hash していたが、
    「差し替え → ローダが `get_data()` で読取 → `compile()` 呼び出し → 元へ復元 →
    本 hook がディスクを再読」という順序では、hook のディスク再読はローダの読取
    より**後**に走るため、攻撃者が hook 発火前（同期呼び出しなので通常は
    マイクロ秒単位だが、GC 一時停止やページフォルトで遅延しうる）に復元を完了
    させれば**復元後の無害な bytes を見て素通りする**——ディスク再読方式は
    「hook がいつ読むか」に依存する脆弱な設計だった。`compile()` の audit
    イベント引数 `source` は、ローダが**実際に `compile()` へ渡した bytes その
    もの**——実測（本ハーネス環境で `sys.addaudithook` の `"compile"` イベントを
    実際に観測）で確認済み: `SourceFileLoader` 経由の module compile では
    `source` は `bytes` 型（`get_data()` が返す生バイト列と同一、PEP 263
    エンコーディング宣言に基づくデコード**前**）である。これを直接 hash すれば
    ディスクの読み直しという中間ステップ自体が不要になり、TOCTOU 窓は「ローダの
    `get_data()` 呼び出しと `compile()` 呼び出しの間」という CPython import
    機構内部の逐次 C 呼び出しシーケンスだけに縮む——ここはユーザーコードが
    割り込む余地が事実上無く、ディスク再読方式が持っていた「hook 発火の遅延」
    という現実的な回避経路を根本的に閉じる。`str` で渡ってくる経路（実測では
    未観測だが、将来の CPython 実装変化・非標準 loader 経由の可能性に備える）は
    `surrogateescape` で bytes 化してから比較する——`file_sha256` が読む生ファイル
    bytes と同じ意味論（デコード後の正規化を経ない生の内容）を保つため。

    **早期 return によるオーバーヘッド最小化（点 5）**: このフックは
    「compile」に限らずプロセス全体の**あらゆる** audit イベントで呼ばれる
    （`sys.addaudithook` は event 種別を絞れない）。まず `event != "compile"` で
    大多数を弾き、次に `filename` が `_SCORER_LOAD_TIME_EXPECTED_HASHES` の
    キー（対象 .py ファイルの絶対パス文字列）と**完全一致**するかを見る——O(1)
    の辞書参照で、対象外の import（pytest 自身・stdlib・無関係な third-party 等、
    実測でプロセスあたり数千件規模）を追加のファイル I/O 無しに即座に弾く。
    一致した場合のみ hash する（対象ファイル数は実際に compile された scorer
    ファイルの数に限られ、`_mir_eval_paths()` の全数ではない）。ディスク I/O が
    完全に無くなった分、10 巡目時点よりオーバーヘッドはさらに小さい。

    **raise しない（点 4）**: audit hook がここで例外を送出すると、CPython の
    仕様上その `compile()` 呼び出し自体が中断される——正当な scorer import を
    本ハーネス自身が壊すことになり、本末転倒。不一致は
    `_SCORER_LOAD_TIME_HASH_MISMATCHES` へ記録するだけに留め、fail-closed は
    `evaluate_m2_bars` の実測経路ゲートに委ねる（2 段構え、他の pre-bind 系
    検査と同型）。

    **pyc キャッシュ経由ロードの正直会計（点 3）**: 本ハーネス自身の import は
    上記のとおり必ず `"compile"` を経由するが、一般に「mtime/size が変わらない
    ため既存 `.pyc` を再利用し `"compile"` が発火しない」経路も存在しうる
    （他プロセスや本ハーネスの `_force_fresh_bytecode()` が及ばない状況）。この
    経路は脅威にならない——ソースを swap-and-restore しても、pyc キャッシュが
    有効な限り**そもそもソースは読まれず**、実行されるのは以前から存在する
    pyc の bytecode のまま（実行 bytes が変化しないので、pin との整合も崩れない）。
    ソースの変更を pyc に反映させるには mtime/size の変化が必要で、それは
    キャッシュ無効化 → 再 compile → 本フックが確実に捕捉、という経路に必ず戻る。
    「捕捉されない」ことと「脅威が成立する」ことは同値ではない——ここが
    正直会計の要点。
    """
    if event != "compile":
        return
    source, filename = args
    if not isinstance(filename, str):
        return
    # セルフレビュー第二弾 H13: `_SCORER_LOAD_TIME_EXPECTED_HASHES` のキーは
    # `_mir_eval_paths()`（`Path.resolve()` 済み）由来だが、`"compile"` イベントの
    # `filename` は loader が使う**未解決**パス——symlink 化された site-packages
    # （venv `lib64 -> lib`・conda・Nix・Debian `dist-packages` 等、実測を行う
    # slow-lane 機で一般的なトポロジ）では両者が文字列として一致せず、`.get()` が
    # 常に None を返して本機構全体が無言で no-op になっていた（実測反証済み）。
    # `os.path.realpath` で正規化してから引く——"compile" イベントは実際のモジュール
    # import 数に比例した頻度でしか発火しない（プロセス全体のあらゆる audit イベント
    # よりは遥かに少ない）ため、ここでの追加の path 解決コストは無視できる（点 5 の
    # 「ディスク**内容**読み取りを対象外イベントに対して行わない」という設計目標とは
    # 別軸——実体を読むわけではない、経路解決だけの軽い追加コスト）。
    resolved_filename = os.path.realpath(filename)
    expected = _SCORER_LOAD_TIME_EXPECTED_HASHES.get(resolved_filename)
    if expected is None:
        return  # scorer 閉包外の compile（大多数）。早期 return。
    # H16: 実際に compile を観測したファイルの集合を記録する（coverage 不変条件用）。
    # 「照合ゼロ = 無言成功」を防ぐため、`evaluate_m2_bars` 側で sys.modules 由来の
    # 期待集合との被覆関係を検証する。
    _SCORER_COMPILE_OBSERVED_PATHS.add(resolved_filename)
    if isinstance(source, (bytes, bytearray)):
        source_bytes = bytes(source)
    elif isinstance(source, str):
        # 実測では未観測（`SourceFileLoader` は常に bytes を渡す）だが、将来の
        # 実装変化・非標準 loader 経路に備えた防御的分岐。
        source_bytes = source.encode("utf-8", errors="surrogateescape")
    else:
        return  # 未知の型（AST 等）は比較不能。ここで諦める（他ゲートに委ねる）。
    observed = hashlib.sha256(source_bytes).hexdigest()
    if observed != expected:
        _SCORER_LOAD_TIME_HASH_MISMATCHES.append(
            f"{resolved_filename}: compile された source bytes (sha256={observed}) が"
            f"束縛時点の期待 (sha256={expected}) と不一致"
        )


def _scorer_compile_expected_paths() -> "List[str]":
    """現在 import 済みの scorer `.py`（トップレベル + カーネルサブモジュール）の resolved origin 一覧。

    （セルフレビュー第二弾 H16）audit hook が「実際に compile を観測した」集合
    （`_SCORER_COMPILE_OBSERVED_PATHS`）と比較する期待側。native（`.so`）は
    「compile」されない対象外（H14 が別途扱う——DT_NEEDED 閉包・pre-bound mapping
    検査）。

    **この関数単独の結果は無条件に安全ではない**: `_PRELOADED_SEED_MODULES`/
    `_PRE_BOUND_SCORER_NATIVE_MAPPINGS` が両方とも空（= 束縛前に何も先読みされて
    いない）ことが別ゲートで既に確認された文脈でのみ、「今 import 済みの scorer
    モジュールは必ず束縛後に compile されたはず」という前提が成り立つ——正当な
    preload シナリオ（例: pytest が他のテストで先に import 済み）では、compile が
    本ハーネスの観測窓より前に起きているため、この関数の結果と観測集合が食い違う
    のは当然であり誤検出になる。呼び出し側（`_require_fresh_process_report_
    provenance`/`_require_publishable_runs`/`evaluate_m2_bars` 自己ゲート）は、
    いずれも preloaded/pre-bound の空チェックの**後**でこの比較を行う——順序が
    この前提を担保する。
    """
    expected: "List[str]" = []
    for name in _SCORER_RUNTIME_PACKAGES:
        module = sys.modules.get(name)
        if module is None:
            continue
        spec = getattr(module, "__spec__", None)
        origin = getattr(spec, "origin", None) if spec else None
        if not origin or origin in ("built-in", "frozen"):
            continue
        origin_path = Path(origin).resolve()
        if origin_path.suffix != ".py":
            continue  # native 拡張子（単一モジュール配布の .so 等）は compile 対象外
        expected.append(str(origin_path))
    for dotted in _SCORER_KERNEL_SUBMODULES:
        module = sys.modules.get(dotted)
        if module is None:
            continue
        spec = getattr(module, "__spec__", None)
        origin = getattr(spec, "origin", None) if spec else None
        if not origin or origin in ("built-in", "frozen"):
            continue
        expected.append(str(Path(origin).resolve()))
    return sorted(set(expected))


def _require_scorer_compile_observation_covers_imported_modules(
    report: Dict[str, Any], *, context: str, exception_cls: type = ValueError
) -> None:
    """report の compile 観測集合が「実際に import された scorer `.py`」を覆うことを要求する。

    （セルフレビュー第二弾 H16）H13（symlink 化 site-packages での照合ミス）や、
    期待値表が何らかの理由で空/部分になった場合、`scorer_load_time_hash_mismatches`
    は「観測ゼロ」でも「無改変」でも同じく `[]` になり区別が付かない——「照合ゼロ =
    無言成功」を、観測集合が期待集合を覆っていることの直接検証で潰す
    （「覆えないものを覆ったと主張しない」#217 原則）。`exception_cls` は呼び出し元
    （`_require_fresh_process_report_provenance` は `RuntimeError`・
    `_require_publishable_runs` は `ValueError`）の既存の例外種別に揃えるため。
    """
    expected = report.get("scorer_compile_expected_paths")
    observed = report.get("scorer_compile_observed_paths")
    if expected is None or observed is None:
        raise exception_cls(
            f"evaluate_m2_bars: {context} が scorer_compile_expected_paths/"
            "scorer_compile_observed_paths のいずれかを欠く; audit hook の被覆を"
            "検証できない report を受理しない (fail-closed)"
        )
    missing = sorted(set(expected) - set(observed))
    if missing:
        raise exception_cls(
            f"evaluate_m2_bars: {context} で import 済みの scorer .py のうち audit "
            f"hook が compile を観測しなかったものがある {missing}; swap-and-restore "
            "検出機構が機能していない疑いがあり、覆えないものを覆ったと主張しない "
            "(fail-closed)"
        )


def _scorer_pins_required_view(pins: Dict[str, Any]) -> Dict[str, Any]:
    """`pins` から任意閉包メンバー（threadpoolctl/charset_normalizer）のフィールドを
    除いた必須メンバーのみのビュー（Codex 15 巡目 P2）。

    `_require_unchanged_since_load` の swap-and-restore 不変性検査専用。任意メンバーの
    presence は「observed import closure に participate したか」（`sys.modules`）で
    決まるため、1 回目の呼び出し（load 時、`import numpy` 等より前）は必ず absent の
    暫定値になり、run が実際にそのメンバーへ participate すれば post-run の呼び出しで
    present へ変わる——これは tamper（swap-and-restore）ではなく設計どおりの遷移
    なので、この不変性検査からは除外する。必須メンバー（mir_eval/scipy/numpy/
    decorator）の不変性は本関数の対象外にせず、従来どおり厳密に検査する。
    """
    return {
        key: value
        for key, value in pins.items()
        if not any(key.startswith(f"{name}_") for name in _SCORER_RUNTIME_PACKAGES_OPTIONAL)
    }


def _require_unchanged_since_load() -> Dict[str, Any]:
    """ロード時に pin したコード（first-party 閉包・スコアラー）の不変を要求する。

    戻り値は post-run に再計算したスコアラー pin（`_scorer_pins(use_cache=False)`）
    ——呼び出し側（`run_accuracy`）がこれを再利用して、任意閉包メンバーの
    observed import closure に基づく最終 presence を report へ刻む（Codex 15 巡目
    P2・二重再計算を避ける）。
    """
    current = _generator_code_sha256()
    if current != _LOADED_GENERATOR_CODE_SHA256:
        raise RuntimeError(
            f"first-party ソースが実行中に変化した（load 時 {_LOADED_GENERATOR_CODE_SHA256!r} "
            f"→ 現在 {current!r}）; 走っているのは import 済みの旧コードなので、この run の "
            "provenance は信用できない — プロセスを再起動して測り直すこと (fail-closed)"
        )
    # キャッシュは (size, mtime_ns) を鍵にするので、それらを保ったまま差し替えられた
    # bytes を見逃す。実行後の検証は必ず再 hash する（Codex P1）。
    # この呼び出しは module load 時の 1 回目ではない（`_SCORER_PINS_INITIAL_BIND_
    # COMPLETE` は既に True）ため、`_scorer_pins()` は匿名マッピングの即時 raise を
    # 自動的に緩和する——run 完了後（実抽出済み）に走るため、CREPE/TensorFlow 等が
    # 生成した正当な匿名 JIT 領域を「束縛前ロード済み」と誤認しない（Codex 9 巡目 CI
    # 追加分・`_reject_pre_bound_native_mappings` docstring 参照）。report の pin
    # 強度には影響しない。
    current_scorer = _scorer_pins(use_cache=False)
    # Codex 15 巡目 P2: 任意閉包メンバーは load 時点で必ず absent（何も import
    # されていない）なので、比較は必須メンバーのビューに限定する（docstring 参照）。
    # 任意メンバーが実行中に absent → present へ遷移するのは正当な observed
    # import closure の確定であり、swap-and-restore の証拠にしない。
    if _scorer_pins_required_view(current_scorer) != _scorer_pins_required_view(
        _LOADED_SCORER_PINS
    ):
        raise RuntimeError(
            f"mir_eval が実行中に差し替わった（load 時 {_LOADED_SCORER_PINS!r} → 現在 "
            f"{current_scorer!r}）; 指標を産んだのは import 済みの旧スコアラーなので、"
            "この run の pin は測定を代表しない — プロセスを再起動して測り直すこと "
            "(fail-closed)"
        )
    # H8: import 済み scorer module の実行対象が束縛時の origin と一致するかも検証する。
    _require_scorer_modules_match_pinned_origin()
    return current_scorer


def _require_scorer_native_unchanged_since_bind() -> None:
    """import 直後・実行中に、scorer native（numpy/scipy の `.so`）が bind 時と変わっていないか検証する。

    dist_native（wheel 同梱 `.libs/` の BLAS/LAPACK 等）と code
    （package-root 配下の `.so` 本体を含む `{name}_code_sha256`）の両方を
    use_cache=False で再 hash し、束縛時点の pin と突き合わせる（詳細後述）。

    （セルフレビュー第二弾 H14）P1-B の audit hook（`"compile"` イベント）は `.py`
    ソースの compile 呼び出しにしか届かない——numpy/scipy の実計算カーネル本体
    （`numpy/_core/_multiarray_umath.cpython-*.so` 等）は `ExtensionFileLoader` 経由の
    dlopen で読まれ、`"compile"` は一切発火しない。この native 実体に対する保護は
    pre（bind 時）/post（run 完了後の `_require_unchanged_since_load`）の 2 点
    ディスク hash 比較のみで、これは P1-B の設計コメントが「.py では原理的に塞げ
    ない」と明言していたのと**全く同じ**スナップショット比較——bind 後・
    `import numpy` 前に `.so` を差し替え、dlopen が差し替え版を mmap し、その後
    ディスク上だけ原本へ復元されると、pre/post のどちらの hash も原本を指す一方
    実行は差し替え版のまま、という P1-B が `.py` に対して塞いだのと同型の窓が
    native には残る。

    **採用した方式（正直会計）**: native の dlopen には `"compile"` イベントに
    相当する「実際にロードされる瞬間を覗くフック」が CPython に存在しない
    （`/proc/self/map_files/` から開いている fd 経由で mmap 済み実体を読み戻す
    方式も検討したが、実装・検証コストに対して本 PR の残作業量が既に大きいため、
    より軽量な代替として**束縛直後の import 完了時点で即座に再 hash する**
    追加チェックポイントを採る）。`import numpy as np`（本ファイル冒頭の唯一の
    module-level import）の直後、および `run_accuracy()` の各カテゴリ処理直後
    （scipy/mir_eval は `evaluate_melody_accuracy` 内で遅延 import されるため、
    最初のカテゴリ処理が終わった時点で import が完了している）の**複数チェック
    ポイント**でこの関数を呼ぶことで、「bind から run 完了まで」だった窓を
    「bind から直後の import 完了まで」+「各カテゴリの処理直後」まで縮める。

    **対象範囲（Codex 12 巡目 P1・dist_native と code の両方を再 hash する）**:
    上のパラグラフが名指しする実カーネル本体（`numpy/_core/_multiarray_umath.
    cpython-*.so` 等）は numpy/scipy の**パッケージディレクトリ配下**にあり、
    hash 対象として拾うのは `{name}_dist_native_sha256`（wheel 同梱
    `{name}.libs/` の BLAS/LAPACK 等・本体ディレクトリの**外**）ではなく
    `{name}_code_sha256`（`package_code_sha256` が `_CODE_SUFFIXES`/
    `_is_native_library` で本体ディレクトリを rglob する際に `.so` も対象に
    含める）側である。旧実装はこの checkpoint で `dist_native_sha256` しか
    再検証しておらず、package-root 配下のカーネル本体を bind→import 窓で
    差し替え→この checkpoint 通過→最終 rehash 前に復元、で素通りできた
    （docstring が「実計算カーネル本体を守る」と明言していた対象と、実際に
    再検証していた pin の乖離）。このため本 checkpoint は `{name}_code_sha256`
    も use_cache=False で再計算し、束縛時点の `_LOADED_SCORER_PINS` と突き合わせる。
    対象は `_SCORER_NATIVE_BACKEND_REQUIRED`（numpy/scipy）に限らない——判断に
    迷う場合は安全側に倒し、`_SCORER_RUNTIME_PACKAGES`（mir_eval/decorator/
    threadpoolctl/charset_normalizer を含む）のうち import 済みかつ束縛時点で
    pin できた（= 導入されていた）パッケージ全てを対象にする。required/optional
    を区別する理由がない（同じ bind→import dlopen 窓の問題は原理上どのパッケージ
    にも起こりうる）。`{name}_dist_native_sha256` の再検証は引き続き
    `_SCORER_NATIVE_BACKEND_REQUIRED` 限定のまま（他パッケージはそもそも同梱
    ネイティブを持たないことが多く、`_scorer_dist_native_sha256` 自体がその
    集合外では空 digest を正当とみなす設計のため、対象を広げても意味がない）。

    **残る境界（意図的に閉じていない・正直会計)**: この方式は依然として離散的な
    時点サンプリングであり、native の dlopen 瞬間そのものを覗いてはいない——
    「チェックポイントとチェックポイントの間」に差し替え→復元が完結すれば
    理論上見逃す。`.py` の audit hook（compile イベントで実際に渡された bytes を
    直接見る）と比べて非対称な保護であることを明記する——native の完全な保護には
    fd 経由の mmap 実体読み戻しか、bind と import を不可分化する設計変更が必要。
    この境界は `{name}_code_sha256` の再検証を追加した後も変わらない（対象集合の
    漏れを塞いだだけで、離散サンプリングという原理的限界そのものは解消しない）。

    **mid-run は直接 hash 比較のみ（セルフレビュー第三弾 H17）**: この checkpoint
    は `run_accuracy()` の各カテゴリ処理**直後**という mid-run タイミングで走る。
    `_scorer_dist_native_sha256` は既定で `_SCORER_NATIVE_BACKEND_REQUIRED` に
    ついて `_reject_pre_bound_native_mappings`（`/proc/self/maps` の全域スキャン
    ＝「束縛前に何かが先読みされていないか」を見る**初回束縛専用**のゲート）も
    実行するが、これを mid-run で再実行すると、実測機（numba/pyin・librosa・
    TensorFlow/basic_pitch・torch/demucs 等が実際に走る M2b S-fullstack のような
    環境）で他ライブラリが実抽出中に張る `memfd:`/削除済み実体バックの JIT 領域を
    「scorer 束縛前の先読み」と誤認し、numpy/scipy とは無関係なマッピングで
    over-strict に fail-closed する（`memfd:`/`(deleted)` の即時 raise は
    `treat_anonymous_as_recorded` の匿名緩和の対象外——fake-backend の CI では
    JIT が走らないため不可視で、「CI green だが実測機で壊れる」型の欠陥だった）。
    このため mid-run（本関数）は `_scorer_dist_native_sha256(..., verify_
    pre_bind_gates=False)` を渡し、maps スキャン + DT_NEEDED 再検証を丸ごと
    skip して、束縛時点の pin との**純粋なディスク hash 比較**だけを行う——
    「native bytes が bind 時から変わったか」だけを見るのが mid-run の役割で、
    「束縛前に何が起きていたか」を問う pre-bind gate は bind 時 1 回限定のまま
    弱めない。
    """
    for name in _SCORER_RUNTIME_PACKAGES:
        if name not in sys.modules:
            continue  # まだ import されていない（この時点では検証しようがない）

        expected_code = _LOADED_SCORER_PINS.get(f"{name}_code_sha256")
        if expected_code is not None:
            current_code = package_code_sha256(name, use_cache=False)
            if current_code != expected_code:
                # 対象を _SCORER_NATIVE_BACKEND_REQUIRED（numpy/scipy）以外にも一般化した
                # ため（Codex 12 巡目 P1）、mir_eval/decorator のような純 Python 配布に
                # 「native カーネル」という文言を使うと実態と食い違う——native/pure-Python
                # の両方で正しい説明になるよう名乗りを出し分ける。
                kind = (
                    "package-root native カーネル（code pin）"
                    if name in _SCORER_NATIVE_BACKEND_REQUIRED
                    else "package-root コード実体（code pin）"
                )
                raise RuntimeError(
                    f"evaluate_m2_bars: {name!r} の{kind}が bind→import 窓で"
                    f"差し替えられた疑いがある（束縛時点の pin {expected_code!r} → "
                    f"import 完了後の再 hash {current_code!r} が不一致・"
                    "swap-and-restore・#217）— プロセスを再起動して測り直すこと "
                    "(fail-closed)"
                )

        if name not in _SCORER_NATIVE_BACKEND_REQUIRED:
            continue  # dist_native(.libs) 側の再検証対象は numpy/scipy のみ
        expected = _LOADED_SCORER_PINS.get(f"{name}_dist_native_sha256")
        if expected is None:
            continue  # 束縛時点で pin できなかった（他のゲートが別途 fail-closed 済み）
        # H17: mid-run は pre-bind gate（maps スキャン + DT_NEEDED 再検証）を
        # 再実行しない——純粋な hash 比較のみ（docstring 参照）。
        current = _scorer_dist_native_sha256(
            name, use_cache=False, verify_pre_bind_gates=False
        )
        if current != expected:
            raise RuntimeError(
                f"evaluate_m2_bars: {name!r} の native 実体が import 完了後の再"
                f"hash で束縛時点の pin（{expected!r}）と不一致（現在 {current!r}）; "
                "bind と import の間に差し替えられ、dlopen が差し替え版を mmap した"
                "疑いがある（swap-and-restore・#217）— プロセスを再起動して測り"
                "直すこと (fail-closed)"
            )


from svp_rpe.melody.provenance import bind_inference_code_pins, package_code_sha256  # noqa: E402

# 推論コードの pin を本モジュールが soundfile/build_melody_bench を import するより
# 前に確定する（run_melody_observability.py と同じ理由・#217）。
bind_inference_code_pins()

# スコアラー（mir_eval + それが直接実行する数値実装）の pin も **実際に import
# される前に** 確定させる。`_scorer_pins()` は importlib.metadata と find_spec だけを
# 使うので import を起こさない。first-party 閉包と同じ load-time 束縛を third-party
# にも適用する。
#
# 閉包の線引き（#217 一軸「pin が実際に実行された実装に接続しているか」）: 本ハーネスが
# 呼ぶ `mir_eval.melody.evaluate` / `to_cent_voicing` は `mir_eval/melody.py` が
# `scipy.interpolate` を、`mir_eval/melody.py` と `mir_eval/util.py` が numpy を
# **直接 import して実行**するため、patch された scipy/numpy は RPA/RCA/median cent
# error を変えるのに mir_eval 単体の pin は動かない。よって mir_eval が直接 import
# する数値実装（scipy, numpy）までを閉包に含める。librosa 系 backend
# （`_EXTRACTOR_CODE_PACKAGES` が抽出器ごとに保持する librosa/resampy/soxr/numba 等）は
# 抽出器オーケストレーションの閉包であって **スコアラー経路には無い**ため、ここには
# 含めない（二重計上・責務混在を避ける）。
#
# **閉包拡張（セルフレビュー H1）**: 手書きの 3 パッケージ列挙は実測（fresh subprocess
# の `import mir_eval.melody` 前後の sys.modules 差分を third-party distribution まで
# 絞り込み）に対して不完全だった。実際に実行される third-party トップレベル配布:
# `mir_eval/util.py:9` の `from decorator import decorator`（`melody.evaluate` が
# `util.filter_kwargs` 経由で全指標に適用するラッパ実装そのもの）、`threadpoolctl`
# （`scipy.io.wavfile` 系のロードで牽引される・BLAS スレッド数を実行時に書き換える
# 実装）に加え、**本実測で新たに判明**した `charset_normalizer`
# （`scipy._lib.array_api_compat.numpy` が `from numpy import *` で numpy の遅延
# submodule `numpy.f2py` に触れ、`numpy/f2py/crackfortran.py` が
# `try: import charset_normalizer except ImportError: charset_normalizer = None`
# で読み込む——numpy 自身にとっては optional だが、本環境では実際に import され
# real な third-party 実行 bytes になる）。3 つとも `numpy/` `scipy/` `mir_eval/`
# の外に単体パッケージとして存在するため `package_code_sha256` の rglob に一切
# 入らない（decorator/threadpoolctl は純 Python、charset_normalizer は mypyc
# コンパイル済み `.so` を site-packages 直下の兄弟ファイルとして持つ——後述
# `_SCORER_NATIVE_BACKEND_REQUIRED` 非対象でも `_scorer_dist_native_sha256` の
# 汎用 RECORD 走査が自動的に拾う）。
#
# 「これで全部か」を手作業の再監査に頼らないため、
# `test_scorer_runtime_packages_cover_observed_mir_eval_import_closure`
# （fresh subprocess で実測し、観測された third-party トップレベルが
# `_SCORER_RUNTIME_PACKAGES` の外に出ないことを assert）を追加する——将来 mir_eval /
# scipy が新しい third-party 依存を牽引するようになったら、この定数を直さない限り
# テストが割れる構造にする。
#
# **必須/任意の分割（Codex 11 巡目 P1-B）**: `charset_normalizer` は numpy 自身の
# `numpy/f2py/crackfortran.py` が `try: import charset_normalizer except
# ImportError: charset_normalizer = None` で読む**任意 import**であり、
# numpy/scipy/mir_eval のどの pyproject にも宣言依存として現れない——本実装環境に
# たまたま入っているから観測された third-party であって、クリーンな最小インストール
# （numpy/scipy/mir_eval の宣言依存だけを解決した環境）には存在しない正当な状態。
# `threadpoolctl` も実測で確認済み（`importlib.metadata.distribution("scipy").
# requires` を精査）: scipy の宣言依存には `extra == "test"` 限定でのみ現れ、通常の
# 実行時依存ではない——実際の参照元（`scipy/io/_fast_matrix_market/__init__.py`）も
# `try: import threadpoolctl ... except ImportError: pass`（「if available」と
# 明記）の任意 import。両者とも「入っていれば pin する・入っていなくても閉包の
# 欠落として fail-closed にしない」扱いが正しい。
#
# 一方 `decorator` は `mir_eval` の `importlib.metadata.distribution("mir_eval").
# requires`（`numpy>=1.15.4` / `scipy>=1.4.0` / `decorator`、いずれも extra
# マーカー無し）で実測確認済みの**宣言依存**——mir_eval が動く前提として常に
# 存在するべき対象なので必須閉包に含める。
_SCORER_RUNTIME_PACKAGES_REQUIRED: "Tuple[str, ...]" = (
    "mir_eval",
    "scipy",
    "numpy",
    "decorator",
)
_SCORER_RUNTIME_PACKAGES_OPTIONAL: "Tuple[str, ...]" = (
    "threadpoolctl",
    "charset_normalizer",
)
_SCORER_RUNTIME_PACKAGES: "Tuple[str, ...]" = (
    _SCORER_RUNTIME_PACKAGES_REQUIRED + _SCORER_RUNTIME_PACKAGES_OPTIONAL
)

# `_scorer_dist_native_sha256` が同梱ネイティブ実体の**非空**を要求するパッケージ
# （Codex P1 5 巡目）。numpy/scipy は BLAS/LAPACK 等の数値バックエンドのネイティブ
# 実行が本質で、pip wheel（manylinux/macOS/Windows いずれも）は必ず同梱ネイティブ
# （`numpy.libs/` 等）を持つ。RECORD にネイティブが 1 つも無いのに寛容に「空集合 =
# 同梱ネイティブなし」の空入力 digest を有効な pin として通すと、conda/distro/
# ソースビルドのように wheel の外（RECORD が把握しない場所）で外部 BLAS に
# 動的リンクした install が、「実行された数値バックエンドの閉包」を一切覆わない
# まま「揃っている」と誤認される。mir_eval（純 Python・数値実行を持たない）は
# この集合に含めない——空 = 空入力 digest のままで正当（数値実行は必須化された
# numpy/scipy 側が担う）。
_SCORER_NATIVE_BACKEND_REQUIRED = frozenset({"numpy", "scipy"})

# glibc 族 + コンパイラランタイムの OS 基盤 whitelist（Codex P1 6 巡目 P1-A）。
# `_ffmpeg_library_closure`（`svp_rpe/melody/provenance.py`）の「OS 基盤へ広げない」
# 線（#217）をこの DT_NEEDED 検証にも踏襲する: これらはあらゆる ELF プロセスが暗黙に
# 持つ土台であって numpy/scipy が数値バックエンドとして選んだ実装ではないため、
# 閉包の境界としてここで探索を止める（含めると「環境全体が推論スタック」になり
# 誰も守れない線になる）。`libz`（zlib）は auditwheel の manylinux policy
# （`policy.json` の `lib_whitelist`）が「常に存在すると仮定してよい外部ライブラリ」
# として明記する対象で、実測でも scipy 同梱の `libgfortran*.so`（`.libs/`）が
# `libz.so.1` へ動的リンクすることを確認済み（本 review 対応時の実環境）。numpy/scipy
# が選んだ数値実装ではなく、manylinux ホイールの前提とする OS 基盤の一部として同じ
# 線引きに含める。
_OS_BASELINE_LIBRARY_RE = re.compile(
    r"^(libc|libm|libpthread|libdl|librt|libgcc_s|libstdc\+\+|libz)\.so(\.\d+)*$"
    r"|^ld(-linux[-\w]*|64)\.so(\.\d+)*$"
)

# BLAS/LAPACK 系数値バックエンドの命名規約（Codex P1 6 巡目 P1-B、セルフレビュー H5 で
# 是正）。`/proc/self/maps` 上でこのパターンに一致し、かつ scorer package の所有パス外に
# あるマッピングは「束縛前に既にロード済みの外部数値バックエンド」の疑いとして扱う。
# **H5 実測反証**: 本環境で実際に実行される OpenBLAS の実体名は
# `numpy.libs/libscipy_openblas64_-32a4b2a6.so` / `scipy.libs/libscipy_openblas-
# 6cdc3b4a.so`（"scipy_openblas" という共有ブランディングの wheel、numpy 2.x /
# scipy が共用）——旧正規表現は "lib" 直後が "scipy_" のため**どちらにもマッチしない**
# ことを実測で確認した。`(scipy_)?` を任意プレフィックスとして許容する。この regex
# 自体は H5 の default-deny（`_reject_pre_bound_native_mappings` 本体）の**多層防御**
# として残す（default-deny だけで已に捕捉されるが、BLAS 命名に一致するものは
# メッセージで名指しして原因究明を早める）。
_BLAS_FAMILY_LIBRARY_RE = re.compile(
    r"^lib(scipy_)?(openblas|blas|lapack|cblas|mkl)[.\-_0-9a-zA-Z]*\.so(\.\d+)*$",
    re.IGNORECASE,
)

# CPython の stdlib C 拡張・python 本体が動的リンクする OS ツールチェーンライブラリ
# （セルフレビュー H5）。`_OS_BASELINE_LIBRARY_RE`（DT_NEEDED 検証専用、numpy/scipy 自身の
# 閉包にしか使わない狭い集合）とは別に、`/proc/self/maps` の default-deny 検査
# （`_reject_pre_bound_native_mappings`）はプロセス全体を見るため、Python 自身の stdlib
# C 拡張（`_hashlib`→libcrypto、`_bz2`→libbz2、`_lzma`→liblzma、`_uuid`→libuuid、xml
# 解析→libexpat 等）が動的リンクする、より広い「解釈系ツールチェーンの一部」を許容する
# 必要がある——実測（fresh CLI で `bind_inference_code_pins()` 直後の maps）でこれらが
# 現に写像されることを確認済み。
_INTERPRETER_TOOLCHAIN_LIBRARY_RE = re.compile(
    r"^(libc|libm|libpthread|libdl|librt|libgcc_s|libstdc\+\+|libz"
    r"|libbz2|libcrypto|libexpat|liblzma|libuuid)\.so(\.\d+)*$"
    r"|^ld(-linux[-\w]*|64)\.so(\.\d+)*$"
)

# 匿名（ファイル非バックエンド）実行マッピングのうち、カーネルが常に注入する無害な
# 疑似マッピング（セルフレビュー H11）。実測（fresh CLI）で他の匿名実行マッピングが
# 無いことを確認済み——これ以外の匿名実行マッピングは境界の外の脅威（JIT/手動 mmap
# ロード）として fail-closed にする。
_ANONYMOUS_EXECUTABLE_MAPPING_ALLOWLIST = frozenset({"[vdso]", "[vsyscall]"})

# 束縛前に本ハーネス自身が import する first-party 依存の native 実体（セルフレビュー
# H5）。`bind_inference_code_pins()`（本ファイル冒頭、scorer pin 束縛より前）が
# `svp_rpe.melody.provenance` を import し、それが pydantic（`svp_rpe` の Model 基盤・
# CLAUDE.md 記載）を牽引する——実測で `pydantic_core` の compiled 実体
# （`_pydantic_core.cpython-*.so`）が束縛時点で既に mmap されていることを確認済み。
# これは攻撃ではなく本ハーネス自身の正当な import 連鎖なので、default-deny の対象から
# 除く。ハードコードされたファイル名ではなく `find_spec` 解決で判定するため、
# pydantic のバージョン更新（ファイル名のハッシュ変化）に自動追随する。
_FIRST_PARTY_BIND_CHAIN_PACKAGES: "Tuple[str, ...]" = ("pydantic_core", "pydantic")


def _is_os_baseline_library(soname: str) -> bool:
    """`soname` が glibc 族 / libgcc_s / libstdc++ の OS 基盤ライブラリか。"""
    return bool(_OS_BASELINE_LIBRARY_RE.match(soname))


def _is_interpreter_toolchain_library(basename: str) -> bool:
    """`basename` が CPython 本体・stdlib C 拡張が動的リンクする OS ツールチェーンか。"""
    return bool(_INTERPRETER_TOOLCHAIN_LIBRARY_RE.match(basename))


# Codex 10 巡目 P1-A で導入した `_system_library_directories()`/
# `_is_under_system_library_directory()`（正規システムライブラリ**ディレクトリ**の
# 集合に対するメンバシップ判定）は Codex 14 巡目 P1-A で撤去した。ディレクトリ
# メンバシップは、`/usr/local/lib` のようにベンダー/アプリライブラリが 1 つでも
# ldconfig 登録されていると、その親ディレクトリ**全体**を baseline 化してしまい、
# 同じディレクトリの cache 未登録 custom ライブラリ（`LD_LIBRARY_PATH` 経由で解決
# されるもの）まで通過させる穴になる。両 consumer（`_verify_scorer_dt_needed_
# closure` の DT_NEEDED 閉包検証・`_reject_pre_bound_native_mappings` の
# `_is_verified_interpreter_toolchain_library`）は、いま `svp_rpe.melody.provenance`
# の `_is_ldconfig_registered_path(soname, resolved)`（soname → ldconfig cache
# 登録済み exact path の一致）を使う。


def _sibling_scorer_backend_roots(exclude: str) -> "list[Path]":
    """`_SCORER_NATIVE_BACKEND_REQUIRED` のうち `exclude` 以外のパッケージの許容ルート。

    numpy/scipy は互いの BLAS 実体（`{pkg}.libs/`）を実行時に共有しうる——本実装中の
    実測で判明: 両方が同一プロセスに import 済みだと、片方の pin 束縛検査
    （例えば `scipy` 側）の時点で、もう片方（`numpy`）の `.libs/` 由来ファイルが
    既に mmap されている（`numpy.libs/libscipy_openblas64_-…so` は `scipy` の
    `natives`/`package_root` のどちらにも属さない）。これを「所有パス外の BLAS 系
    ライブラリ」と誤認すると、pytest 等 numpy/scipy が両方 import 済みの環境で
    default-deny が無関係な誤検出を起こす。`{pkg}.libs/` は auditwheel の同梱慣例
    （`package_root` の兄弟ディレクトリ）なので、ディレクトリ名パターンから解決する
    （RECORD の再走査はしない・find_spec のみで import を起こさない）。
    """
    import importlib.util

    roots: "list[Path]" = []
    for other in _SCORER_NATIVE_BACKEND_REQUIRED:
        if other == exclude:
            continue
        try:
            spec = importlib.util.find_spec(other)
        except Exception:
            continue
        if spec is None or not getattr(spec, "origin", None):
            continue
        if spec.origin in ("built-in", "frozen"):
            continue
        package_root = Path(spec.origin).resolve().parent
        roots.append(package_root)
        libs_sibling = package_root.parent / f"{package_root.name}.libs"
        if libs_sibling.is_dir():
            roots.append(libs_sibling)
    return roots


def _first_party_bind_chain_native_roots() -> "list[Path]":
    """束縛前に本ハーネス自身の import 連鎖が正当に mmap する native の許容ルート。

    `_FIRST_PARTY_BIND_CHAIN_PACKAGES` の各パッケージを `find_spec` で解決し、その
    package_root（単一モジュールならファイル自身）を返す。解決できない（未導入）
    パッケージは静かに無視する——`pydantic`/`pydantic_core` はこのプロセスに存在する
    ことが前提の許容であって、存在しないなら許容領域も無い（fail-closed 側に倒す
    必要はない。単に「その分の許容が無い」だけ）。
    """
    import importlib.util

    roots: "list[Path]" = []
    for pkgname in _FIRST_PARTY_BIND_CHAIN_PACKAGES:
        try:
            spec = importlib.util.find_spec(pkgname)
        except Exception:
            continue
        if spec is None or not getattr(spec, "origin", None):
            continue
        if spec.origin in ("built-in", "frozen"):
            continue
        origin = Path(spec.origin).resolve()
        is_pkg = getattr(spec, "submodule_search_locations", None) is not None
        roots.append(origin.parent if is_pkg else origin)
    return roots


def _stdlib_prefixes() -> "list[Path]":
    """stdlib C 拡張（`lib-dynload/*.so` 等）の許容ルート（`sysconfig` の実測値）。

    `_INTERPRETER_TOOLCHAIN_LIBRARY_RE` は OS 提供の共有ライブラリ（`libbz2.so` 等）
    の basename だけを見るため、Python 自身の stdlib C 拡張モジュール
    （`/usr/lib/python3.11/lib-dynload/_bz2.cpython-311-…so` 等、basename が
    拡張モジュール名でありライブラリ名ではない）は別に許容する必要がある。ハード
    コードパスではなく `sysconfig.get_paths()` から実測する——ディストリビューション
    ごとにインストール prefix が異なるため。
    """
    import sysconfig

    prefixes = []
    for key in ("stdlib", "platstdlib"):
        value = sysconfig.get_paths().get(key)
        if value:
            prefixes.append(Path(value).resolve())
    return prefixes


def _under_stdlib_prefix(path: Path, prefixes: "list[Path]") -> bool:
    for prefix in prefixes:
        try:
            path.relative_to(prefix)
            return True
        except ValueError:
            continue
    return False


def _interpreter_shared_library_paths() -> "list[Path]":
    """CPython 自身の共有ライブラリ実体（`--enable-shared` ビルドのみ存在）。

    インタプリタの実行ファイル自身（`sys.executable`）は既に別経路（直接パス比較）で
    許容されているが、`--enable-shared` でビルドされた CPython（GitHub Actions
    `actions/setup-python` の hostedtoolcache ビルドが該当）は、実行ファイルとは
    **別の実体**として `libpython3.X.so.1.0` 等を持つ。これは `_stdlib_prefixes()`
    （`stdlib`/`platstdlib`、通常 `.../lib/python3.X/...`）にも
    `_is_interpreter_toolchain_library`（OS ツールチェーンの basename 集合）にも
    一致しない別ディレクトリ（例 `/opt/hostedtoolcache/Python/3.12.13/x64/lib/`、
    `stdlib` の**親**であって `stdlib` 自身ではない）に置かれることを CI 実測で確認した
    （PR #225 9 巡目 CI 失敗: `test_reverification_refuses_when_stack_cannot_rerun` が
    Python 3.12 の測り直し子プロセスで `libpython3.12.so.1.0` を「所有パス外の
    default-deny 対象」として記録し、`pre_bound_scorer_native_mappings == []` 要求に
    失敗した）。

    これは scorer（numpy/scipy）の native バックエンドとは無関係な、インタプリタ
    そのものの一部であり、`sys.executable` 自身を許容する既存の判断と対称的に扱う
    べき対象——ハードコードした名前パターンではなく `sysconfig` の
    `INSTSONAME`/`LDLIBRARY`/`LIBDIR` の実測値から解決する（static ビルドでは
    存在しないため、その場合は空リストを返し許容領域を増やさない）。
    """
    import sysconfig

    libdir = sysconfig.get_config_var("LIBDIR")
    if not libdir:
        return []
    libdir_path = Path(libdir)
    if not libdir_path.is_dir():
        return []
    paths: "list[Path]" = []
    seen: "set[Path]" = set()
    for key in ("INSTSONAME", "LDLIBRARY"):
        name = sysconfig.get_config_var(key)
        if not name:
            continue
        candidate = (libdir_path / name).resolve()
        if candidate in seen:
            continue
        if candidate.is_file():
            paths.append(candidate)
            seen.add(candidate)
    return paths


def _owned_by_scorer_distribution(path: Path, *, package_root: Path, natives: "set[Path]") -> bool:
    """`path` が `name` distribution の所有物（package_root 配下 or RECORD 由来 natives）か。

    P1-A（DT_NEEDED 閉包）・P1-B（事前ロード済みマッピング）の双方が同じ「所有」の
    定義を共有する——閉包検証で「揃っている」と認めた実体だけが、事前ロード検査でも
    正当な mapping として扱われる。
    """
    if path in natives:
        return True
    try:
        path.relative_to(package_root)
        return True
    except ValueError:
        return False


def _verify_scorer_dt_needed_closure(
    name: str, *, package_root: Path, natives: "set[Path]"
) -> None:
    """`name`（numpy/scipy）の DT_NEEDED 閉包が外部数値バックエンドへ抜けていないか検証する。

    （Codex P1 6 巡目 P1-A）`_scorer_dist_native_sha256` の「RECORD にネイティブ実体が
    非空」検査は **cardinality**（1 つでもあれば通す）しか見ないため、部分ベンダリング
    ——同梱ネイティブが一部あるものの、実際に実行される BLAS/LAPACK 実装は RECORD が
    把握しない外部の system-wide ライブラリに動的リンクしている——を見逃す。

    起点は **package 本体配下のネイティブ拡張モジュール**（`import numpy` /
    `import scipy` が実際に dlopen する root）に限る——`.libs` 側のファイル
    （`libgfortran*` 等）自身は auditwheel 同梱の慣例で **自身の `DT_RPATH` を持たない**
    ことが多く、実行時にそれでも兄弟ライブラリ（`libquadmath*` 等）を解決できるのは
    ELF ローダが **`DT_RPATH`（`DT_RUNPATH` と違い）を依存の依存にまで継承する**
    ためである（`_ffmpeg_library_closure` と同じ規律・#217）。`.libs` ファイルを
    独立した起点として `rpath=()` で解決すると、この継承されたはずの RPATH が
    無いために実在する兄弟ライブラリの解決に失敗し、実際には閉じている依存を
    「解決不能」と誤検出する（本 P1-A の実装中に実測で確認: scipy 同梱
    `libgfortran-8f1e9814.so.5.0.0` は自身に RPATH を持たないが、これを要求する
    scipy 拡張モジュールの RPATH `$ORIGIN/../../scipy.libs` を継承して初めて同梱
    `libquadmath-828275a7.so.0.0.0` を見つけられる）。

    各 soname の解決先が

    1. 同一 distribution の所有ファイル（package_root 配下 or `natives` 集合内）
       → 揃っている。その実体もさらに DT_NEEDED を持ちうるので、自身の RPATH に
       起点から継承した RPATH を連ねて再帰的に検証を続ける（二重ベンダリングされた
       外部依存を見逃さないため）。
    2. OS 基盤 whitelist（`_is_os_baseline_library`）に soname が名前で一致し、
       **かつ**実際に解決した先が、その soname として ldconfig cache に登録された
       **exact path** と一致する（`svp_rpe.melody.provenance` の
       `_is_ldconfig_registered_path`）→ 揃っている。ここで探索を打ち切る（OS 基盤
       自身の依存まで遡る必要はない）。**名前一致だけでは信用しない（Codex 10
       巡目 P1-A）**: 旧実装は基盤名に一致したら無条件で `continue`（解決すら
       しない）していたため、`LD_LIBRARY_PATH`/`DT_RPATH` が攻撃者の用意した別
       ディレクトリの同名ファイル（`libm.so.6` 等）を指しても検出できなかった。
       ここでは非基盤 soname と同じ `_resolve_soname_without_loading`（rpath →
       `LD_LIBRARY_PATH` → runpath → ldconfig）で実解決し、解決先を検証する
       ——所有権検証（`_owned_by_scorer_distribution`）の代わりに ldconfig cache
       の exact path 一致を使うのは、基盤ライブラリは定義上 distribution の所有物
       ではなく OS 自身の提供物だから。**「正規システムディレクトリ配下か」という
       ディレクトリメンバシップ判定（旧 10 巡目実装）はここでは使わない（Codex 14
       巡目 P1-A）**: `/usr/local/lib` のようにベンダー/アプリライブラリが 1 つでも
       ldconfig 登録されているディレクトリでは、そのディレクトリ配下という条件
       だけでは cache 未登録の custom ライブラリ（`LD_LIBRARY_PATH` 経由で同じ
       ディレクトリへ解決されるもの）まで通してしまう。soname → cache 登録済み
       exact path の対応を要求することで、この穴を塞ぐ。

    のどちらでもなければ `RuntimeError` で fail-closed にする（soname・解決先パスの
    両方をメッセージに残す）。package_root 起点の walk で `natives` の**全ファイルに
    到達する**のが通常形（実測: 本環境の numpy/scipy はいずれも 100% 到達）。それでも
    到達しないファイルが残る場合（未参照の同梱物等）は、そのファイル自身を追加の
    起点として（自身の RPATH のみで）同じ検証にかける——「到達しないので見ない」を
    許さない防御的な最終手段。

    **dlopen は使わない**（#217 規律）——`svp_rpe.melody.provenance` が
    `_ffmpeg_library_closure` で確立した ELF プログラムヘッダ直読み
    （`_elf_dynamic_info`）と RPATH/RUNPATH 展開（`_object_rpath_dirs` /
    `_object_runpath_dirs`）+ ldconfig キャッシュ相当の解決（`_resolve_soname_without_
    loading`）をそのまま再利用する。非 ELF・パース不能な実体に当たった場合も、覆えない
    閉包を覆ったと主張しないため fail-closed にする。
    """
    from svp_rpe.melody.provenance import (
        _elf_dynamic_info,
        _is_ldconfig_registered_path,
        _is_native_library,
        _object_rpath_dirs,
        _object_runpath_dirs,
        _resolve_soname_without_loading,
    )

    roots: "list[Path]" = []
    if package_root.is_dir():
        roots.extend(
            sorted(
                path
                for path in package_root.rglob("*")
                if path.is_file()
                and "__pycache__" not in path.parts
                and _is_native_library(path)
            )
        )

    # (対象ファイル, 継承 RPATH コンテキスト) をキーにした visited（Codex 13 巡目 P1-B）。
    # 同一 owned native が異なる DT_RPATH/DT_RUNPATH を継承する 2 つの extension root
    # から到達可能な場合、path だけで dedup すると最初にソートされた context の walk
    # しか検証されない——別 context では transitive soname が external backend へ
    # 解決されうるのに、後続の walk が skip されてしまう。継承 RPATH ディレクトリ列
    # （`_object_rpath_dirs` が返す、正規化・順序保持済みのタプル）を path と組にした
    # キーにすることで、context ごとに独立して soname 解決を再検証する。無限ループ
    # 防止のため同一 (path, context) の再訪だけは引き続き skip する。
    visited: "set[tuple[Path, tuple]]" = set()
    # (対象ファイル, 継承 RPATH ディレクトリ) の pending 群。継承 RUNPATH は無い
    # （DT_RUNPATH は依存の依存へ継承しない・#217 と同じ ffmpeg closure の規律）。
    pending_roots: "list[tuple[Path, tuple]]" = [(root, ()) for root in roots]
    # Codex 10 巡目 P1-A: 基盤 whitelist 名一致は「安全」の証拠にしない。実際に
    # 解決（rpath → LD_LIBRARY_PATH → runpath → ldconfig、既存 `_resolve_soname_
    # without_loading` と同じ探索順）し、解決先が ldconfig cache 登録の exact path
    # と一致することまで検証する（Codex 14 巡目 P1-A: ディレクトリメンバシップから
    # exact path 一致へ変更。`_is_ldconfig_registered_path` docstring 参照）。

    def _walk(obj: Path, inherited_rpath: "tuple") -> None:
        # Codex 13 巡目 P1-B: path 単独ではなく (path, 継承 RPATH context) で dedup
        # する。同一 native でも継承 context が異なれば soname 解決先が変わりうる
        # ため、context ごとに独立して検証する必要がある。
        visit_key = (obj, inherited_rpath)
        if visit_key in visited:
            return
        visited.add(visit_key)
        info = _elf_dynamic_info(obj)
        if info is None:
            raise RuntimeError(
                f"evaluate_m2_bars: {name!r} の native 実体 {obj} の DT_NEEDED を読めない"
                "（非 ELF、または ELF ヘッダをパースできない）; 数値バックエンドの依存"
                "閉包を覆えないため、覆ったと主張しない (fail-closed)"
            )
        own_rpath = _object_rpath_dirs(obj, info, inherited_rpath)
        own_runpath = _object_runpath_dirs(obj, info)
        for soname in info[0]:
            if _is_os_baseline_library(soname):
                # 名前が基盤らしいだけでは信用しない（Codex 10 巡目 P1-A）: この
                # native 実体自身の RPATH/RUNPATH → LD_LIBRARY_PATH → ldconfig の
                # 探索順で実解決し、解決先が ldconfig cache 登録の exact path と
                # 一致することを要求する（Codex 14 巡目 P1-A。`_is_ldconfig_
                # registered_path` docstring 参照）。`LD_LIBRARY_PATH`/RPATH が
                # 攻撃者のディレクトリを指す同名ファイルへ差し替えても、ここで検出
                # する。解決できた上で cache 登録の exact path と一致すれば
                # 「揃っている」——OS 基盤自身の依存閉包までは遡らない（従来どおり
                # ここで探索を打ち切る）。
                resolved_baseline = _resolve_soname_without_loading(
                    soname, rpath_dirs=own_rpath, runpath_dirs=own_runpath
                )
                if resolved_baseline is None:
                    raise RuntimeError(
                        f"evaluate_m2_bars: {name!r} の native 実体 {obj} が要求する"
                        f"基盤 soname {soname!r} を解決できない; 名前が基盤らしいだけで"
                        "実体の所在を立証しないまま「揃っている」と主張しない "
                        "(fail-closed)"
                    )
                if not _is_ldconfig_registered_path(soname, resolved_baseline):
                    raise RuntimeError(
                        f"evaluate_m2_bars: {name!r} の native 実体 {obj} が要求する"
                        f"基盤 soname {soname!r} が ldconfig cache 登録の exact path と"
                        f"一致しない {resolved_baseline} に解決された; LD_LIBRARY_PATH/"
                        "RPATH が基盤 soname 解決を歪めている疑いがあり、覆えない閉包を"
                        "覆ったと主張しない (fail-closed)"
                    )
                continue
            resolved = _resolve_soname_without_loading(
                soname, rpath_dirs=own_rpath, runpath_dirs=own_runpath
            )
            if resolved is None:
                raise RuntimeError(
                    f"evaluate_m2_bars: {name!r} の native 実体 {obj} が要求する soname "
                    f"{soname!r} を解決できない; 数値バックエンドの依存閉包を覆えないため "
                    "fail-closed"
                )
            if not _owned_by_scorer_distribution(
                resolved, package_root=package_root, natives=natives
            ):
                raise RuntimeError(
                    f"evaluate_m2_bars: {name!r} の native 実体 {obj} が要求する soname "
                    f"{soname!r} が distribution 外部の {resolved} に解決された; 外部数値"
                    "バックエンド（BLAS/LAPACK/MKL 等）への部分ベンダリングの疑いがあり、"
                    "覆えない閉包を覆ったと主張しない (fail-closed)"
                )
            _walk(resolved, own_rpath)

    for root, inherited in pending_roots:
        _walk(root, inherited)

    # 未到達の natives（auditwheel 同梱物の中で package_root 起点の walk が
    # 実際に触れなかったもの）が残っていれば、それ自身を起点として追加検証する。
    # visited は (path, context) キーになった（Codex 13 巡目 P1-B）ので、path だけの
    # 集合に畳んでから差分を取る。
    visited_paths = {visited_obj for visited_obj, _ in visited}
    for leftover in sorted(natives - visited_paths):
        _walk(leftover, ())


_LD_PRELOAD_SIBLING_ENV_VARS: "Tuple[str, ...]" = ("LD_PRELOAD", "LD_AUDIT", "LD_DYNAMIC_WEAK")


def _reject_ld_preload_before_scorer_bind() -> None:
    """`LD_PRELOAD` 系の割り込み経路が設定されたままスコアラー pin を束縛しない。

    （Codex P1 6 巡目 P1-B + セルフレビュー H4）`LD_PRELOAD` は soname 解決より前に
    ローダへ割り込み、実際にシンボルを提供する実体をディスク上の「正規の」
    numpy/scipy 同梱ネイティブとは無関係な場所へすり替えうる——`_scorer_dist_native_
    sha256` はディスクの bytes を hash するだけなので、その bytes が「実際に実行
    された数値バックエンド」であるという保証が `LD_PRELOAD` 下では成立しない
    （#217 の事前ロード窓の native 版: メモリ上の実装をディスク hash で検出
    できない）。

    **兄弟経路（H4）**: `LD_AUDIT`（rtld-audit インタフェース。`la_symbind*` で
    シンボル解決そのものを横取りでき、LD_PRELOAD と同等以上の割り込み能力を持つ）、
    `LD_DYNAMIC_WEAK`（弱シンボルの優先順位を変え、後読みライブラリの実装で先読み
    ライブラリの弱シンボルを上書きできる）も同じ脅威クラスとして拒否する。
    `/etc/ld.so.preload`（環境変数を一切使わずに全プロセスへ preload を効かせる、
    root 権限が要る代わりに本プロセスの環境だけを見ても検出できない経路）も存在
    かつ非空なら同様に拒否する。値の中身を精査して無害と判定する経路は用意しない
    ——「実測はこれらの割り込み経路の外側で行う」という運用上の要求に倒す
    (fail-closed)。

    `LD_LIBRARY_PATH` はここでは拒否しない（`_resolve_soname_without_loading` が
    ローダと同じ探索順の一部として値そのものを使うため、存在自体を禁止すると通常の
    運用を壊す）——ただし記録はする（H9・report の `sys_path_and_ld_env`）。

    **格上げ検討の結論（Codex 10 巡目 P1-A、再確認・不採用）**: `LD_LIBRARY_PATH` は
    「基盤 soname の解決を歪めうる唯一の正規経路」（`DT_RPATH`/`DT_RUNPATH` は
    ELF ファイル自身に埋め込まれた値で、環境変数のように外部から後付けできない）
    という指摘は正しい。それでも fail-closed への格上げは**採用しない**——理由:
    (1) `_verify_scorer_dt_needed_closure`（P1-A）が基盤 soname を含む**全 soname**
    について、`LD_LIBRARY_PATH` 込みの探索順で実解決した**結果**（解決先が正規
    システムディレクトリ配下か / distribution の所有物か）を直接検証するため、
    `LD_LIBRARY_PATH` がどのように解決を歪めても、その帰結自体をここで既に捕捉する
    ——env var の存在有無を別途禁止する二重の防御は、脅威モデル上は冗長。
    (2) `LD_LIBRARY_PATH` は CUDA/conda 等、数値バックエンドと無関係な理由で ML
    環境が広く設定する実務上ごく一般的な変数——`LD_PRELOAD`/`LD_AUDIT`/
    `LD_DYNAMIC_WEAK`（プロセス全体のシンボル解決を横取りする、正当な用途が
    ほぼ無い狭い経路）と異なり、一律 fail-closed にすると多くの正当な実行環境を
    壊す。値の**帰結**を検証する方が、値の**存在**を禁止するより正確で運用も壊さない。
    今後、(1) の帰結検証で塞ぎきれない新しい迂回経路が判明した場合はこの判断を
    再検討すること。
    """
    non_empty_vars = [name for name in _LD_PRELOAD_SIBLING_ENV_VARS if os.environ.get(name)]
    if non_empty_vars:
        raise RuntimeError(
            f"evaluate_m2_bars: {non_empty_vars!r} が設定された状態でスコアラー pin を"
            "束縛しようとした; ディスク hash が実行中の数値バックエンド/シンボル解決を"
            "代表する保証がない (fail-closed)。これらの環境変数を外した状態で測り直す"
            "こと"
        )
    preload_file = Path("/etc/ld.so.preload")
    try:
        preload_bytes = preload_file.read_bytes()
    except FileNotFoundError:
        preload_bytes = b""
    except OSError as exc:
        raise RuntimeError(
            "evaluate_m2_bars: /etc/ld.so.preload の状態を確認できない "
            f"({type(exc).__name__}: {exc}); 全プロセス preload が無いことを立証できない"
            "ため fail-closed"
        ) from exc
    if preload_bytes.strip():
        raise RuntimeError(
            "evaluate_m2_bars: /etc/ld.so.preload が存在し非空; 環境変数を経由しない"
            "全プロセス preload が設定された状態でスコアラー pin を束縛しようとした "
            "(fail-closed)。/etc/ld.so.preload を空にした状態で測り直すこと"
        )


# H3: `sys.meta_path` / `sys.path_hooks` / `sys.path_importer_cache` の標準構成。
# CPython の 3 標準 finder に加え、実測で `setuptools` の `distutils-precedence.pth`
# （site 起動時に自動処理される、pip ベースのほぼ全環境に存在する慣行）が
# `_distutils_hack.DistutilsMetaFinder` を無条件に `sys.meta_path` へ挿すことを
# 確認した——「3 標準 finder だけ」という素朴な不変条件は fresh CLI でも成立しない
# （H5 の BLAS 命名規約と同型の「推測で許可リストを作らず実測する」教訓）。
_STANDARD_META_PATH_QUALNAMES = frozenset(
    {
        "_frozen_importlib.BuiltinImporter",
        "_frozen_importlib.FrozenImporter",
        "_frozen_importlib_external.PathFinder",
    }
)
_ALLOWED_NON_STANDARD_META_PATH_QUALNAMES = frozenset(
    {"_distutils_hack.DistutilsMetaFinder"}
)
_STANDARD_PATH_HOOK_QUALNAMES = frozenset({"zipimport.zipimporter"})
_STANDARD_PATH_HOOK_QUALNAME_PREFIXES: "Tuple[str, ...]" = (
    "_frozen_importlib_external.FileFinder.path_hook",
)
_STANDARD_PATH_IMPORTER_CACHE_QUALNAME = "_frozen_importlib_external.FileFinder"


def _qualname_of(obj: Any) -> str:
    """クラスオブジェクト・関数・インスタンスのどれでも `module.qualname` を返す。

    `sys.meta_path` の標準 finder はクラスオブジェクト自体が入り
    （`<class '_frozen_importlib.BuiltinImporter'>`）、`sys.path_hooks` の標準 hook は
    クラス（`zipimport.zipimporter`）または関数（`FileFinder.path_hook` の closure）
    が入る——クラス・関数はどちらも `__module__`/`__qualname__` を**自身が直接**
    持つので、それをそのまま使う。`_distutils_hack` 等のサードパーティ finder
    （メタクラス経由ではない通常のインスタンス）は `__qualname__` を自身は持たない
    ため、`type(obj)` にフォールバックする——`type(obj)` を先に取ると関数が
    `<class 'function'>`（`builtins.function`）に潰れて全て同じ qualname になって
    しまう（本実装中に実測で踏んだ不具合: `FileFinder.path_hook` の closure が
    `path_hooks:builtins.function` に潰れ、標準 hook 判定に失敗した）。
    """
    module = getattr(obj, "__module__", None)
    qualname = getattr(obj, "__qualname__", None)
    if module is None or qualname is None:
        target = type(obj)
        module = target.__module__
        qualname = target.__qualname__
    return f"{module}.{qualname}"


def _non_standard_import_hooks() -> "List[str]":
    """`sys.meta_path` / `sys.path_hooks` / `sys.path_importer_cache` の非標準構成を列挙する。

    （セルフレビュー H3）pin の場所解決（`importlib.util.find_spec`）は必ず
    `sys.meta_path` を経由する。`sys.meta_path` の先頭に、実 origin（pristine パス）
    を持つ spec を返しつつ `get_source`/`get_data` だけ差し替える loader を挿す
    finder を入れると、`find_spec(...).origin` は正規パスのまま・実行されるモジュール
    の中身は攻撃者の bytes、を再現できる——ディスク hash は無傷のまま pin が完全に
    一致する。numpy を **import しない**ので `_preloaded_seed_modules` の sys.modules
    スナップショットにも native でないので LD_PRELOAD/maps 検査にも映らない
    ——3 ゲートいずれの死角。

    `.pth`/`sitecustomize.py`/`PYTHONSTARTUP` に依存しない site-packages 由来 finder
    （pytest の assertion rewriting、coverage 等も同じ機構を使う）は import 文脈では
    常態なので、`_PRELOADED_SEED_MODULES`/`_PRE_BOUND_SCORER_NATIVE_MAPPINGS` と同じ
    2 段構え（記録のみ・実測経路でのみ fail-closed）をこの関数の呼び出し側で適用する。
    """
    findings: "List[str]" = []
    for finder in sys.meta_path:
        qualname = _qualname_of(finder)
        if (
            qualname in _STANDARD_META_PATH_QUALNAMES
            or qualname in _ALLOWED_NON_STANDARD_META_PATH_QUALNAMES
        ):
            continue
        findings.append(f"meta_path:{qualname}")
    for hook in sys.path_hooks:
        qualname = _qualname_of(hook)
        if qualname in _STANDARD_PATH_HOOK_QUALNAMES:
            continue
        if any(qualname.startswith(prefix) for prefix in _STANDARD_PATH_HOOK_QUALNAME_PREFIXES):
            continue
        findings.append(f"path_hooks:{qualname}")
    for key, value in sys.path_importer_cache.items():
        if value is None:
            continue
        qualname = _qualname_of(value)
        if qualname == _STANDARD_PATH_IMPORTER_CACHE_QUALNAME:
            continue
        findings.append(f"path_importer_cache[{key!r}]:{qualname}")
    return sorted(findings)


_DELETED_MAPPING_SUFFIX = " (deleted)"


def _parse_proc_self_maps_executable_mappings() -> "list[tuple[str, bool]]":
    """`/proc/self/maps` から実行可能（`x` 権限を含む）マッピングを列挙する。

    （Codex P1 6 巡目 P1-B・セルフレビュー H5/H6/H11 で拡張再設計）dlopen して
    確認するのではなく、**既にプロセスへ mmap されているファイル**を読むだけ
    （#217 の dlopen 回避規律）。`LD_PRELOAD` を外した後でも、束縛前に
    sitecustomize 等が数値バックエンドを先読みしていれば、ディスク hash は正規の
    実体と一致するのに実際に実行されるのはメモリ上の別実装——メモリ mapping は
    ディスク hash では検出できないので、専用にここで読む。読めない環境（非 Linux
    等）は「事前ロードが無い」ことを立証できないため fail-closed にする。

    **H5 で拡張したフィルタ規律**: 旧実装は `_is_native_library`（拡張子ベース）で
    先に絞り込んでいたため、命名規約に依存しない攻撃（`(deleted)` 実体・`memfd:`
    fileless ロード・拡張子なし実行マップ）を素通りさせていた（H6）。ここでは
    **権限フィールド（`x` を含むか）を先に見る**——「実行されうるか」が本質であって
    拡張子は関係が無い。呼び出し側（`_reject_pre_bound_native_mappings`）が拡張子・
    `(deleted)`・匿名の別を判定する。

    戻り値は `(raw_path, deleted)` のタプル列。`raw_path` は `(deleted)` サフィックス
    を剥がした後の文字列（named な匿名マッピングは `[vdso]` 等の疑似名、ファイル無し
    実行マッピングはそのままの文字列——`memfd:pwn` 等）。**パスフィールド自体が無い
    行**（5 フィールド、`00:00 0` のような device/inode でデバイスバッキング無しの
    純粋な匿名 mmap 領域）も、実行可能（`x`）であれば `"[anonymous]"` という合成の
    疑似名で表現する——`_ANONYMOUS_EXECUTABLE_MAPPING_ALLOWLIST`（`[vdso]`/
    `[vsyscall]` のみ）に無いため、呼び出し側の default-deny へ自然に合流する
    （Codex 9 巡目 P1: 旧実装はここで無条件に `continue` し、権限検査より前に
    候補から落としていた——匿名 exec 領域が H11 の default-deny に一度も
    到達しない穴だった。実測〈fresh CLI の `/proc/self/maps`〉でパス無し行に `x` を
    含むものは確認できなかったため、推測で許容リスト化はせず fail-closed 側に倒す）。
    `deleted=True` は呼び出し側の fail-closed 事由になる——削除済み実体は hash 突合
    が原理的に不能なため（「覆えないものを覆ったと主張しない」#217 原則）。
    """
    maps_path = Path("/proc/self/maps")
    try:
        text = maps_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise RuntimeError(
            "evaluate_m2_bars: /proc/self/maps を読めない（非 Linux 等）; 束縛前にロード"
            "済みの実行可能マッピングが無いことを立証できないため fail-closed "
            f"({type(exc).__name__}: {exc})"
        ) from exc

    mappings: "set[tuple[str, bool]]" = set()
    for line in text.splitlines():
        fields = line.split(None, 5)
        if len(fields) < 2:
            continue
        perms = fields[1]
        if "x" not in perms:
            continue
        raw_path = fields[5].strip() if len(fields) >= 6 else ""
        if not raw_path:
            # パスフィールドすら無い純粋な匿名 exec 領域。`continue` で権限検査後の
            # 候補から落とさず、named 匿名マッピングと同じ判定経路（許容リスト外は
            # fail-closed）に合流させる（Codex 9 巡目 P1）。
            raw_path = "[anonymous]"
        deleted = raw_path.endswith(_DELETED_MAPPING_SUFFIX)
        if deleted:
            raw_path = raw_path[: -len(_DELETED_MAPPING_SUFFIX)]
        mappings.add((raw_path, deleted))
    return sorted(mappings)


# `_reject_pre_bound_native_mappings` が「所有パス／bytes 一致で良性」と判定した
# 束縛前マッピングを集約する可変ログ（Codex P1 7 巡目）。load 時の最初の `_scorer_pins()`
# 呼び出し中に numpy/scipy 双方の呼び出しがここへ追記し、その直後に
# `_PRE_BOUND_SCORER_NATIVE_MAPPINGS` としてタプルへ凍結する（`_PRELOADED_SEED_MODULES`
# と同じ「load 時 1 回だけ確定」規約）。以降の再検証呼び出し（`_scorer_pins(use_cache=
# False)`）でも同じ関数がここへ追記し続けるが、凍結済みタプルは新しいコピーなので
# 影響しない——この可変ログ自体を読むのは凍結直後の 1 回だけでよい。
_PRE_BOUND_NATIVE_MAPPING_LOG: List[str] = []

# `_scorer_pins()` の**最初の**（load 時・`import numpy` 等より前の）呼び出しが完了した
# かどうか（Codex 9 巡目 CI 追加分）。この 1 回目の呼び出しだけが H11 の「束縛前に
# 何も先読みされていないはず」という不変条件が実際に成立する文脈——2 回目以降の
# 呼び出し（`_require_unchanged_since_load` の post-run 再検証、
# `_require_homogeneous_scorer`/`_require_fresh_process_report_provenance` の
# 評価器側再計算等、呼び出し元は本ファイル内に複数ある）はいずれも「同じプロセスで
# 既に実抽出（CREPE/TensorFlow 等）が走った後」でありうるため、その場合に生成される
# 正当な匿名 JIT コード領域は初回束縛時の脅威モデルの対象外——`/proc/self/maps` だけ
# では出所を区別できない（docstring 参照）。呼び出し元ごとに個別の引数を持ち回るのは
# 実測で判明した該当箇所の多さ（`_require_homogeneous_scorer` だけでなく
# `evaluate_m2_bars` の複数経路）に対して取りこぼしやすいため、`_scorer_pins`/
# `_scorer_dist_native_sha256` の**既定値そのもの**をこのフラグから自動導出する
# （テストが明示的に `treat_anonymous_as_recorded=` を渡した場合はそちらを優先）。
_SCORER_PINS_INITIAL_BIND_COMPLETE = False


def _reject_pre_bound_native_mappings(
    name: str,
    *,
    package_root: Path,
    natives: "set[Path]",
    use_cache: bool = True,
    treat_anonymous_as_recorded: bool = False,
) -> "List[str]":
    """`name` の pin 束縛前に、既にロード済みの実行可能マッピングを検出・記録する。

    （Codex P1 6/7 巡目 P1-B、セルフレビュー H5/H6/H11 で default-deny へ再設計）
    `LD_PRELOAD` 環境変数チェック（`_reject_ld_preload_before_scorer_bind`）だけでは、
    環境変数が既にクリアされた後で実体だけがメモリに残るケースや、`LD_PRELOAD` を
    経由しない `sitecustomize.py` 等の先読みを捉えられない。`/proc/self/maps` を
    実際に読み、実行可能マッピングを 1 つずつ分類する。

    **H5: 命名ホワイトリストから default-deny への反転**。6 巡目時点は「BLAS 命名
    規約 (`_BLAS_FAMILY_LIBRARY_RE`) に一致」または「pin 対象 basename と一致」の
    どちらかだけを検査し、それ以外は無条件に許容していた。実測反証: 本環境で実際に
    実行される OpenBLAS 実体名 `libscipy_openblas64_-32a4b2a6.so` /
    `libscipy_openblas-6cdc3b4a.so` はどちらの条件にも一致せず（"lib" 直後が
    "scipy_"）、素通りしていた（regex 自体は是正済みだが、命名規約に依存する限り
    次の亜種にも弱い）。ここでは反転する: 束縛時点（`import numpy` より前）で
    写像されている実行可能な実体は「(a) scorer 所有（自身または兄弟 numpy⇔scipy）
    (b) OS/解釈系ツールチェーン (c) stdlib C 拡張（`lib-dynload/*.so` 等、
    `sysconfig` 実測の prefix 判定） (d) 本ハーネス自身の first-party import 連鎖
    (`_first_party_bind_chain_native_roots`) (e) カーネル注入の無害な匿名マッピング」
    のどれかであるべきという不変条件へ倒し、それ以外の**あらゆる**実行可能マッピング
    を記録対象にする——BLAS 命名やネイティブ拡張子に一致するかは問わない。

    **(b) の実パス検証（Codex 10 巡目 P1-A）**: 上記 (b)「OS/解釈系ツールチェーン」の
    許容は `_is_interpreter_toolchain_library`（basename の命名規約）だけでは判定
    しない——`/tmp/evil/libz.so.1` のように所有権の無い場所に置かれた同名ファイルを
    basename だけで許容すると、default-deny の意味が失われる。
    `_is_verified_interpreter_toolchain_library` で basename 一致に加えて、実パスが
    その basename（soname 相当）として ldconfig cache に登録された **exact path**
    と一致することまで要求する（`svp_rpe.melody.provenance` の
    `_is_ldconfig_registered_path`。Codex 14 巡目 P1-A でディレクトリメンバシップ
    から反転）。DT_NEEDED 閉包側の `_is_os_baseline_library`（P1-A、
    `_verify_scorer_dt_needed_closure` docstring 参照）と対をなす同じ設計判断
    ——「名前が基盤らしい」ことは「実体がそこにある」ことを何も保証しない。

    **H6: `(deleted)` / `memfd:` / 拡張子なし実行マップ**。`_parse_proc_self_maps_
    executable_mappings` は権限フィールド（`x`）だけで絞るため、これらも可視化
    される。`(deleted)` 実体は OS ツールチェーン（`apt upgrade` 中の置換等、良性の
    通常運転）以外は即 fail-closed にする——削除済み実体は hash 突合が原理的に
    不能（「覆えないものを覆ったと主張しない」#217 原則）。`memfd:` 由来・拡張子なし
    実行マップ（本ハーネスの解釈系実行ファイル自身を除く）も同様に即 fail-closed
    にする——いずれも「無害な重複」を bytes 比較で救済する余地が無い
    （比較対象のディスク実体が存在しない）。

    **H11: 匿名実行マッピング**。`[vdso]`/`[vsyscall]` はカーネルが全プロセスへ
    無条件に注入する疑似マッピングで、実測（fresh CLI）でもこれ以外の匿名実行
    マッピングは存在しない——`_ANONYMOUS_EXECUTABLE_MAPPING_ALLOWLIST` に無い匿名
    実行マッピングは JIT/手動 mmap ロードの疑いとして即 fail-closed にする。ただし
    **境界の正直会計**（#217「覆えないことを明記する」規律・`ordering_is_proof`
    と同じ様式）: この検査は file-backed mapping しか覆わない前提の延長であり、
    プロセスメモリを直接書ける攻撃者（例えば mmap 済みページの実行時パッチ）は
    そもそも境界の外——`_reject_pre_bound_native_mappings` 自体が「新規マッピング
    の出現」を捕捉する仕組みであって、既存マッピングの内容改変は対象外。

    **2 段構え（Codex P1 7 巡目・`_PRELOADED_SEED_MODULES` と同型）**は維持する:
    6 巡目時点の実装は「所有パス」「bytes 一致の良性重複」を単に `continue`（黙って
    見逃す）していたが、これは pre-bind mmap による TOCTOU を見逃す——`dlopen` で
    既に mmap された実体は、その**後**でディスク上のファイルが差し替えられても
    pre/post どちらの hash も新 bytes を指す一方、実行はロード済みの旧 bytes の
    まま進む。この見逃しを「起きなかったこと」にせず、`_PRE_BOUND_NATIVE_MAPPING_
    LOG`（→ 凍結タプル `_PRE_BOUND_SCORER_NATIVE_MAPPINGS`）へ**記録**する。ただし
    記録そのものは raise しない——pytest 等の import 文脈では `svp_rpe` 経由で
    numpy/scipy が本ハーネスの読み込みより先に import されているのが常態で、その
    場合の記録が非空になるのは避けられない（`_PRELOADED_SEED_MODULES` が同じ理由で
    load 時に raise しないのと同じ）。**実測経路**（`evaluate_m2_bars` の評価器
    自身のゲート・report の `pre_bound_scorer_native_mappings` 検証）でのみ非空を
    fail-closed にする。H6/H11 の即時 raise 条件（`(deleted)`・`memfd:`・拡張子なし・
    非許容匿名）はこの 2 段構えの**外**——束縛時点で即座に fail-closed にする
    （記録して後回しにするほど無害ではない）。

    **`treat_anonymous_as_recorded`（Codex 9 巡目 CI 追加分）**: H11 の非許容匿名
    実行マッピングは既定で即 fail-closed にするが、これは「束縛前（`import numpy` 等
    より前）に既に何かがロードされている」という**初回束縛**の脅威モデルを前提に
    している。`_scorer_pins()`/`_scorer_dist_native_sha256()` はこの同じ関数を
    **初回束縛の 1 回だけでなく**、`_require_unchanged_since_load()` の post-run
    自己整合性再検証、`_require_homogeneous_scorer`・`_require_fresh_process_
    report_provenance` の評価器側スコアラー再計算等、**プロセス内の複数箇所**から
    繰り返し呼ぶ（実測: 本ハーネスの `--categories S_direct` 実 CLI run で CREPE/
    TensorFlow が一度でも import されると、以降このプロセス内で `_scorer_pins()`
    を呼ぶあらゆる箇所——テストで言えば別の無関係なテストの `run_accuracy()`/
    `evaluate_m2_bars()` 呼び出しも含む——が同じ匿名 JIT 領域（XLA/oneDNN 等）を
    検出し fail-closed した。`/proc/self/maps` は匿名領域の生成元を区別できず、
    「攻撃」と「無害な JIT」のどちらとも決定不能）。呼び出し元ごとに個別の引数を
    持ち回るのは取りこぼしやすいため、`_scorer_pins`/`_scorer_dist_native_sha256`
    の**既定値そのもの**をモジュール冒頭の `_SCORER_PINS_INITIAL_BIND_COMPLETE`
    フラグ（`_scorer_pins()` の 1 回目の呼び出し完了後に一度だけ `True` へ切り替わる）
    から自動導出する: 1 回目の呼び出し（load 時、`import numpy` 等より前）だけが
    厳格（`False`、非許容匿名は即 raise）、2 回目以降は自動的に緩和（`True`、
    `recorded` へ記録するだけ）される。**「実測経路」（`_require_fresh_process_
    report_provenance` の report フィールド検査・`_require_publishable_runs`・
    evaluate 自己ゲート）が読む report の `pre_bound_scorer_native_mappings`
    フィールドは、load 時（`import numpy` より前）の 1 回目の呼び出し結果を凍結した
    `_PRE_BOUND_SCORER_NATIVE_MAPPINGS` から取るため、この既定値変更の影響を一切
    受けない**（弱めるのは「2 回目以降の内部再チェックがクラッシュするか」だけで、
    publish 可否を左右する報告 pin の強度はそのまま）。`_reject_pre_bound_native_
    mappings` 自体の引数既定値は `False`（ハードコード、フラグ非依存）のまま——
    直接呼び出す全既存テストの厳格挙動はそのため変わらない。明示的に `True`/`False`
    を渡した呼び出しは常にそちらが優先される。

    **bytes が一致すれば「無害な重複」**——6 巡目の実装中の実測で判明した良性の衝突が
    ある: auditwheel は同梱ネイティブのファイル名にビルド内容の hash を含めるため
    （例 `libgfortran-040039e1-0352e75f.so.5.0.0`）、numpy と scipy が同一の
    gfortran ビルドを別々の `.libs/` ディレクトリへ同梱すると、**同じ basename・
    同じ bytes** のファイルが 2 か所に存在する。ELF ローダは `DT_SONAME` 単位で
    ロード済み実体を再利用するため、片方の distribution 経由で既に読み込まれた
    実体を、もう片方が指す別所在の同名ファイルの代わりに使い回すのは通常運転
    ——bytes が同一である限り、どちらの物理コピーを指しても pin は変わらない
    （#217「pin は bytes を代表する」の原則そのもの）。これも「無害」ではあるが
    「起きた」ことに変わりはないため記録対象にする——**bytes が違う**、または比較対象が
    無い（basename が `natives` に無い外部由来）場合にのみ、6 巡目と同じく即
    `RuntimeError` で fail-closed にする（これは弱めていない）。

    戻り値: このパッケージについて記録した束縛前マッピングのパス文字列一覧
    （raise しなかった owned / bytes 一致 / default-deny 対象のケース）。
    """
    import sys

    from svp_rpe.melody.provenance import _is_ldconfig_registered_path, _is_native_library
    from svp_rpe.utils.hashing import file_sha256

    mapped = _parse_proc_self_maps_executable_mappings()
    natives_by_basename: "dict[str, list[Path]]" = {}
    for path in natives:
        natives_by_basename.setdefault(path.name, []).append(path)
    first_party_roots = _first_party_bind_chain_native_roots()
    sibling_backend_roots = _sibling_scorer_backend_roots(name)
    stdlib_prefixes = _stdlib_prefixes()
    try:
        interpreter_path = Path(sys.executable).resolve()
    except OSError:
        interpreter_path = None
    interpreter_shared_libraries = _interpreter_shared_library_paths()
    # Codex 10 巡目 P1-A（maps 側）: OS/解釈系ツールチェーンの許容も basename の
    # 命名規約だけでは判定しない。`/tmp/evil/libz.so.1` のように無関係な場所に
    # 置かれた同名ファイルが mmap されていても、旧実装は basename 一致だけで
    # 許容していた。Codex 14 巡目 P1-A: 実パスが正規システムディレクトリ配下にある
    # ことを要求する旧チェック（ディレクトリメンバシップ）は撤去し、basename
    # （soname 相当）が ldconfig cache に登録した **exact path** と一致することを
    # 要求する（`_is_ldconfig_registered_path`）。

    def _is_verified_interpreter_toolchain_library(candidate_path: Path) -> bool:
        if not _is_interpreter_toolchain_library(candidate_path.name):
            return False
        try:
            resolved = candidate_path.resolve()
        except OSError:
            return False
        return _is_ldconfig_registered_path(candidate_path.name, resolved)

    def _content_matches_a_native(mapped_path: Path, candidates: "list[Path]") -> bool:
        if not candidates or not mapped_path.is_file():
            return False
        try:
            mapped_digest = file_sha256(mapped_path, use_cache=use_cache)
        except OSError:
            return False
        for candidate in candidates:
            try:
                if file_sha256(candidate, use_cache=use_cache) == mapped_digest:
                    return True
            except OSError:
                continue
        return False

    def _under_any_root(path: Path, roots: "list[Path]") -> bool:
        for root in roots:
            try:
                path.relative_to(root if root.is_dir() else root.parent)
                if root.is_dir() or path == root:
                    return True
            except ValueError:
                continue
        return False

    recorded: "List[str]" = []
    for raw_path, deleted in mapped:
        if raw_path.startswith("["):
            if raw_path in _ANONYMOUS_EXECUTABLE_MAPPING_ALLOWLIST:
                continue  # カーネル注入の無害な疑似マッピング（H11）
            if treat_anonymous_as_recorded:
                # run 完了後の自己整合性再検証（`_require_unchanged_since_load`）限定:
                # 実抽出（CREPE/TensorFlow 等）が生成した正当な JIT 匿名領域と攻撃を
                # 区別できないため、この呼び出し元だけは 2 段構え側（record）に倒す。
                # report の `pre_bound_scorer_native_mappings` は load 時の 1 回目の
                # 呼び出し結果を凍結済みで、この分岐を経由しないため実測経路の強度は
                # 変わらない（docstring 参照）。
                recorded.append(raw_path)
                continue
            raise RuntimeError(
                f"evaluate_m2_bars: {name!r} の pin 束縛前に許容外の匿名実行マッピング "
                f"{raw_path!r} を検出; JIT/手動 mmap ロードの疑いがあり file-backed で"
                "ないため hash 突合できない (fail-closed)"
            )
        if "memfd:" in raw_path:
            raise RuntimeError(
                f"evaluate_m2_bars: {name!r} の pin 束縛前に memfd 由来の fileless "
                f"実行マッピング {raw_path!r} を検出; hash 突合できる実体が無い "
                "(fail-closed)"
            )
        mapped_path = Path(raw_path)
        basename = mapped_path.name
        if deleted:
            if _is_verified_interpreter_toolchain_library(mapped_path):
                continue  # OS ツールチェーンの置換（apt upgrade 等）は良性の通常運転
            raise RuntimeError(
                f"evaluate_m2_bars: {name!r} の pin 束縛前に削除済み実行マッピング "
                f"{raw_path!r} (deleted) を検出; 削除済み実体は hash 突合が原理的に"
                "不能で、覆えないものを覆ったと主張しない (fail-closed)"
            )
        if interpreter_path is not None and mapped_path == interpreter_path:
            continue  # 本プロセスの解釈系実行ファイル自身
        if mapped_path in interpreter_shared_libraries:
            continue  # `--enable-shared` ビルドの CPython 自身の共有ライブラリ実体
        if not _is_native_library(mapped_path):
            raise RuntimeError(
                f"evaluate_m2_bars: {name!r} の pin 束縛前に拡張子なしの実行マッピング "
                f"{raw_path!r} を検出; 命名規約に依存しない fileless/偽装ロードの疑いが"
                "あり fail-closed"
            )
        if _owned_by_scorer_distribution(mapped_path, package_root=package_root, natives=natives):
            # 所有パス内 = 良性だが、pre-bind mmap の TOCTOU 対象として記録する
            # （raise はしない・上記 docstring の 2 段構え）。
            recorded.append(str(mapped_path))
            continue
        if _is_verified_interpreter_toolchain_library(mapped_path):
            continue  # OS/解釈系ツールチェーン（H5 default-deny の許容クラス）
        if _under_stdlib_prefix(mapped_path, stdlib_prefixes):
            continue  # stdlib C 拡張（`lib-dynload/*.so` 等。H5 default-deny の許容クラス）
        if _under_any_root(mapped_path, first_party_roots):
            continue  # 本ハーネス自身の first-party import 連鎖（H5 default-deny）
        if _under_any_root(mapped_path, sibling_backend_roots):
            continue  # 兄弟 scorer パッケージ（numpy⇔scipy）の所有領域（H5 default-deny）
        is_blas_family = bool(_BLAS_FAMILY_LIBRARY_RE.match(basename))
        candidates = natives_by_basename.get(basename, [])
        if _content_matches_a_native(mapped_path, candidates):
            recorded.append(str(mapped_path))  # bytes 一致 = 良性の重複ベンダリングだが記録
            continue
        if is_blas_family:
            raise RuntimeError(
                f"evaluate_m2_bars: {name!r} の pin 束縛前に BLAS 系ライブラリ "
                f"{mapped_path} が distribution の所有パス外からプロセスへ既にロード"
                "済み（bytes も pin 対象と不一致）; ディスク hash と実行中の実装が"
                "一致する保証がない (fail-closed)"
            )
        if basename in natives_by_basename:
            raise RuntimeError(
                f"evaluate_m2_bars: {name!r} の pin 対象ネイティブ {basename!r} と同名だが"
                f" 別所在 {mapped_path} からロード済みのマッピングを検出（bytes も不一致）;"
                " ディスク hash が実行中の実装を覆う保証がない (fail-closed)"
            )
        # H5 default-deny: 上記のどの許容クラスにも該当しない実行可能マッピング。
        # 即座には raise せず（pytest 等の import 文脈では他パッケージの正当な
        # native も同様に写像されて当然のため）、記録して実測経路の判断に委ねる
        # （上記 2 段構え docstring 参照）。
        recorded.append(str(mapped_path))

    _PRE_BOUND_NATIVE_MAPPING_LOG.extend(recorded)
    return recorded


def _reject_sourceless_scorer_code(
    name: str, *, origin: Path, package_root: Path, is_package: bool
) -> None:
    """scorer モジュールに「対応する `.py` の無い `.pyc`」が無いことを要求する（セルフレビュー H2）。

    `package_code_sha256`（`svp_rpe/melody/provenance.py` の `package_code_state`）は
    `_CODE_SUFFIXES`（`.py`/`.so`/`.pyd`/`.dylib`）と native 名だけを rglob し、
    `"__pycache__" not in path.parts` で除外する——**`.pyc` はどちらの経路でも
    hash 対象にならない**。`_force_fresh_bytecode`（本ファイル冒頭）は
    `sys.pycache_prefix` を非存在パスへ差し替えて **`SourceFileLoader` の
    キャッシュ経路**由来の stale bytecode を封じるが、これは `.py` を実行する経路の
    話であって、`.py` 自体が無く `.pyc` だけが置かれた場合（`SourcelessFileLoader` が
    実行）は対象外——`mir_eval/melody.py` を削除して `mir_eval/melody.pyc`
    （別ソースからコンパイル）を置くと、親 run・子測り直しプロセス・evaluate の
    再計算が**全て同じ poisoned install** を読み、pin は完全に自己整合し bit 一致も
    する。実行された bytes を 1 byte も覆わない digest が「揃っている」として通る。

    ここでは 2 経路を検査する:

    1. **`find_spec` の origin 自体が `.pyc`**（`.py` が削除され、トップレベル
       モジュール/パッケージの `__init__` 自体が sourceless で解決された）→ 即
       fail-closed。単一モジュール配布（`is_package=False`）はこれで十分
       （サブモジュールを持たない）。
    2. **パッケージ配下の任意の `.pyc`**（`is_package=True` のときのみ、`package_root`
       を rglob）: `__pycache__` 内なら（`{module}.{tag}[.opt-N].pyc` の命名規約から
       `module` 名を復元し）`__pycache__` の親に `{module}.py` が無ければ orphan
       （stale cache または捏造）。`__pycache__` 外の直置き `.pyc`
       （`SourcelessFileLoader` が実行しうる形）は同ディレクトリに同名 `.py` が
       無ければ orphan。どちらも fail-closed にする——`.py` があれば通常の
       `SourceFileLoader` 経路が優先され、隣の `.pyc` は実行されない（無害）。

    より原理的な代替案（spec.loader の型を全 scorer モジュールについて要求する）は
    H3（meta_path 検査）と同じフックで将来まとめられるが、ここでは
    ディスク走査による直接検出で十分な実効性を持つ。
    """
    if origin.suffix == ".pyc":
        raise RuntimeError(
            f"evaluate_m2_bars: {name!r} の実行対象 {origin} が sourceless .pyc "
            "（対応する .py が無い）; 実行された bytes を覆わない digest を「揃って"
            "いる」と主張しない (fail-closed)。.py を復元して測り直すこと"
        )
    if not is_package:
        return
    for pyc_path in sorted(package_root.rglob("*.pyc")):
        if "__pycache__" in pyc_path.parts:
            module_stem = pyc_path.name.split(".")[0]
            expected_source = pyc_path.parent.parent / f"{module_stem}.py"
        else:
            expected_source = pyc_path.with_suffix(".py")
        if not expected_source.is_file():
            raise RuntimeError(
                f"evaluate_m2_bars: {name!r} 配下に対応する .py の無い sourceless "
                f".pyc {pyc_path}（期待した source: {expected_source}）; 実行されうる "
                "bytes を覆わない digest を「揃っている」と主張しない (fail-closed)"
            )


def _scorer_dist_native_sha256(
    name: str,
    *,
    use_cache: bool = True,
    treat_anonymous_as_recorded: "Optional[bool]" = None,
    verify_pre_bind_gates: bool = True,
) -> str:
    """`name` の wheel 同梱ネイティブ実体（パッケージ本体ディレクトリ **外**）の pin。

    **`verify_pre_bind_gates`（セルフレビュー第三弾 H17）**: 既定 `True` は
    `_SCORER_NATIVE_BACKEND_REQUIRED` について `_reject_pre_bound_native_mappings`
    （`/proc/self/maps` の全域スキャン）と `_verify_scorer_dt_needed_closure` を
    従来どおり実行する——これは「束縛前（`import numpy` 等より前）に何かが
    先読みされていないか」を**プロセス全体**から検出する**初回束縛専用**のゲートで、
    `treat_anonymous_as_recorded` の匿名マッピング緩和では救えない `memfd:`/
    `(deleted)` 実行マッピングの即時 raise を含む（`_reject_pre_bound_native_
    mappings` docstring 参照）。`_require_scorer_native_unchanged_since_bind`
    の mid-run checkpoint（各カテゴリ抽出**後**に呼ばれる）がこの関数を
    `use_cache=False` で再呼び出しすると、このゲートも一緒に再実行されてしまい、
    実測機で numba（pyin/librosa）・TensorFlow（basic_pitch）・torch（demucs）が
    実抽出中に張る `memfd:`/削除済み実体バックの JIT 領域を「scorer 束縛前の
    先読み」と誤認して fail-closed する（numpy/scipy とは無関係なマッピングで
    over-strict に落ちる liveness 欠陥）。mid-run で本来検証すべきは「native
    bytes が bind 時から変わったか」という**純粋なディスク hash 比較**だけで
    あり、プロセス全体の maps を再スキャンする必要は無い——
    `verify_pre_bind_gates=False` を渡すと、この 2 つのゲート呼び出しを完全に
    skip し、natives 集合の enumerate と hash 計算のみ行う。**初回束縛時
    （`_scorer_pins()` の 1 回目の呼び出し）は必ず既定 `True` のまま呼ぶ**——
    弱めるのは mid-run の再検証呼び出しだけで、初回のゲートは変わらず厳格。

    numpy/scipy の一般的な wheel install は OpenBLAS 等の実行ネイティブ実体を
    パッケージ本体ディレクトリの兄弟（`numpy.libs/` / `scipy.libs/`）に置く。
    `package_code_sha256` は `find_spec` の場所（`numpy/__init__.py` を含む
    ディレクトリ）配下しか rglob しないため、この兄弟ディレクトリを差し替えても
    version pin・code pin のどちらも動かない——スコアラーは異なる bytes を実行するのに
    閉包が同一と誤認される（Codex P1 2 巡目）。

    `importlib.metadata.distribution(name).files`（RECORD = 配布が自己申告する
    所有ファイル一覧）からネイティブ拡張子のファイルを列挙し、パッケージ本体
    ディレクトリの**外**にあるものだけを対象にする（本体配下は `package_code_sha256`
    側が既に hash 済みで、含めると二重計上になる）。空集合は `name` が
    `_SCORER_NATIVE_BACKEND_REQUIRED` に無ければ（mir_eval のような純 Python 配布）
    空入力の sha256 を返す——「無い」と「hash できない」を区別するため、RECORD 自体が
    引けない・列挙したパスが実在しない場合は例外で fail-closed にする（「覆えない
    閉包を覆ったと主張しない」#217 原則）。**import を起こさない**（RECORD 読みと
    `find_spec` のみ）。

    **非 wheel 数値バックエンドの fail-closed（Codex P1 5 巡目）**: numpy/scipy
    （`_SCORER_NATIVE_BACKEND_REQUIRED`）は BLAS/LAPACK 等の数値実行が本質で、pip
    wheel は必ず同梱ネイティブを持つ。この 2 つで natives が空なのは
    conda/distro/ソースビルドのように wheel の外（RECORD が把握しない場所）で外部
    バックエンドに動的リンクしている疑いで、fail-closed に倒す（実測は wheel ベース
    環境に限定する正直会計）。

    **DT_NEEDED 閉包の包含証明（Codex P1 6 巡目 P1-A）**: 上の「natives 非空」検査は
    cardinality しか見ないため、部分ベンダリング（同梱ネイティブが一部あるが実際の
    BLAS/LAPACK 実装は外部の system-wide ライブラリに動的リンクしている）を見逃す。
    natives が非空でも `_verify_scorer_dt_needed_closure` で実際の `DT_NEEDED` soname
    を解決し、閉包全体が「distribution の所有物」か「OS 基盤」のどちらかで説明できる
    ことを検証する（5 巡目時点の「フル ELF 依存閉包の解決は実装しない」という限定は
    ここで解消済み）。

    **束縛前ロード済みマッピングの拒否（Codex P1 6 巡目 P1-B）**: `LD_PRELOAD` や
    `sitecustomize` がこの pin 束縛より前に数値バックエンドを先読みしていると、
    ディスク hash は正規の実体と一致するのに実行はメモリ上の別実装が担いうる——
    `_reject_pre_bound_native_mappings` が `/proc/self/maps` を読み、束縛対象の
    natives と所在が食い違う BLAS 系マッピングを検出する。

    **distribution/import 所有権検証（Codex P1 4 巡目）**: `importlib.metadata` の
    メタデータ側（`distribution().files` = RECORD）と `find_spec` の実行側は、
    shadow install・重複 site-packages（例: 別 sys.path エントリに同名パッケージが
    2 つ）の環境では**別のインストールを指しうる**。version pin（`importlib.metadata`
    由来）と code/native pin（`find_spec` 由来）が別インストールを指したまま一致比較
    されると、pin 全体（version・code・native の 3 つとも）が「揃っている」ことの
    保証を失う——#217 の場所軸（どのファイルを読んだか）が、そもそも同じ package の
    2 つの見方の間で揃っていない。RECORD 内の `{top}/__init__.py`（`top` は
    `find_spec` が解決したパッケージ本体ディレクトリ名）を `dist.locate_file` で
    実パスへ解決し、`find_spec` の `origin` と一致することを要求する。RECORD に
    `{top}/__init__.py` が無い（editable install 等で所有権を立証できない）場合も、
    spec が解決できない（導入済みのはずなのに namespace/zip import 等で origin が
    無い）場合も、検証不能を「揃っている」と主張しない fail-closed に倒す。
    """
    import importlib.metadata
    import importlib.util

    from svp_rpe.melody.provenance import _is_native_library
    from svp_rpe.utils.hashing import sha256_of_files

    try:
        dist = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        if name in _SCORER_NATIVE_BACKEND_REQUIRED:
            # 数値バックエンド必須パッケージ（Codex セルフレビュー H12）: dist-info が
            # 無い（vendored copy・sys.path 直置き等）のに `run_accuracy` がそのまま
            # 完走すると、run 側の native ゲート（natives 非空要求・DT_NEEDED 閉包・
            # pre-bound マッピング検査）が丸ごと skip されたまま report が書ける。
            # evaluate 側は `_validated_scorer_pin_tuple` の None version 拒否で結局
            # publish 不可になるが、「run は成功するのに evaluate で初めて落ちる」
            # 非対称と、「numpy/scipy は必ず立証する」という
            # `_SCORER_NATIVE_BACKEND_REQUIRED` の意図をこの分岐だけ免れるのは筋が
            # 通らない。束縛時点で落とす。
            raise RuntimeError(
                f"evaluate_m2_bars: 数値バックエンド必須パッケージ {name!r} の "
                "distribution が見つからない（dist-info 無し・vendored copy や "
                "sys.path 直置きの疑い）; 数値バックエンドの閉包を立証できないため "
                "fail-closed。pip でインストールした環境で実測すること"
            )
        # 配布自体が無い = 未導入。version pin と同じ規約で None にはできない
        # （戻り値は str 限定）ため、「同梱ネイティブが実行されることもない」= 空入力
        # sha256 を返す（fail-closed 対象は「導入されているのに解決できない」場合）。
        return hashlib.sha256(b"").hexdigest()
    if dist.files is None:
        raise RuntimeError(
            f"evaluate_m2_bars: distribution {name!r} の RECORD (files) が取得できない; "
            "wheel 同梱ネイティブ実体を覆えない閉包を覆ったと主張しない (fail-closed)"
        )

    try:
        spec = importlib.util.find_spec(name)
    except Exception:
        spec = None
    if spec is None or getattr(spec, "origin", None) in (None, "built-in", "frozen"):
        # distribution は引けた（導入済み）のに実行対象を解決できない。「未導入」
        # （distribution も引けない・上で空入力を返す分岐）とは区別し、こちらは
        # 「導入済みだが実体不明」として skip せず raise する（#217）。
        raise RuntimeError(
            f"evaluate_m2_bars: distribution {name!r} は導入済みだが find_spec で実行対象を "
            "解決できない（namespace/zip import 等）; 実行される package を特定できない "
            "closure を覆ったと主張しない (fail-closed)"
        )
    executed_init = Path(spec.origin).resolve()
    # 単一モジュール配布（`threadpoolctl.py` のように site-packages 直下に 1 ファイル。
    # Codex セルフレビュー H1 で `_SCORER_RUNTIME_PACKAGES` に追加した threadpoolctl で
    # 初めて踏む分岐）は、`package_root` を親ディレクトリ（site-packages 全体）にすると
    # 「本体配下は既に hash 済み」判定が site-packages 全体を本体と誤認し、無関係な
    # distribution の native まで二重計上除外してしまう
    # （`provenance.package_code_state` の単一モジュール分岐と同じ理由・#217）。
    # ファイル自身を「本体」の基準にする。
    is_package = getattr(spec, "submodule_search_locations", None) is not None
    package_root = executed_init.parent if is_package else executed_init
    owning_record_name = f"{package_root.name}/__init__.py" if is_package else f"{name}.py"

    # 所有権検証: RECORD（メタデータ側）が指す本体ファイルの実パスと、find_spec
    # （実行側）が指す origin が同一ファイルであることを要求する。
    owning_record = next(
        (record for record in dist.files if str(record) == owning_record_name), None
    )
    if owning_record is None:
        raise RuntimeError(
            f"evaluate_m2_bars: distribution {name!r} の RECORD に {owning_record_name!r} が "
            "見つからない（editable install 等で所有権を立証できない）; 覆えない閉包を "
            "覆ったと主張しない (fail-closed)"
        )
    owned_init = Path(str(dist.locate_file(owning_record))).resolve()
    if owned_init != executed_init:
        raise RuntimeError(
            f"evaluate_m2_bars: distribution {name!r} のメタデータが指す package "
            f"({owned_init}) と find_spec が実行する package ({executed_init}) が別;"
            " distribution メタデータが実行される package を所有していない ="
            " shadow/重複インストールの疑いがあり、version/native pin がどちらの"
            " 実装を指すか保証できない (fail-closed)"
        )

    # sourceless .pyc の fail-closed（セルフレビュー H2）: ownership 検証済みの
    # origin/package_root を再利用する。
    _reject_sourceless_scorer_code(
        name, origin=executed_init, package_root=package_root, is_package=is_package
    )

    natives: "set[Path]" = set()
    for record in dist.files:
        if not _is_native_library(Path(str(record))):
            continue
        located = Path(str(dist.locate_file(record))).resolve()
        if is_package:
            try:
                located.relative_to(package_root)
                continue  # 本体ディレクトリ配下は package_code_sha256 側が既に hash 済み
            except ValueError:
                pass
        elif located == package_root:
            continue  # 単一モジュール本体そのもの（ネイティブ拡張子は普通付かないが念のため）
        if not located.is_file():
            raise RuntimeError(
                f"evaluate_m2_bars: distribution {name!r} の RECORD が指すネイティブ実体 "
                f"{located} が存在しない; 覆えない閉包を覆ったと主張しない (fail-closed)"
            )
        natives.add(located)

    if not natives and name in _SCORER_NATIVE_BACKEND_REQUIRED:
        # 数値バックエンドの closure を立証できない（Codex P1 5 巡目）: wheel install
        # なら必ず `.libs`/DLL が RECORD 経由で見つかるはずなので、ここに来るのは
        # conda/distro パッケージやソースビルドで外部 BLAS/LAPACK に動的リンクした
        # install（RECORD がその外部ライブラリを把握しない）を意味する。フル ELF
        # 依存閉包の解決（`ldd` 相当の再帰的動的リンク解決）は実装せず、「実測は
        # wheel ベース環境に限定する」正直会計として fail-closed に倒す。
        raise RuntimeError(
            f"evaluate_m2_bars: distribution {name!r} の RECORD にネイティブ実体が "
            "1 つも無い; 数値バックエンドの閉包を立証できない（非 wheel インストール "
            "— conda/distro/ソースビルド等で外部 BLAS/LAPACK にリンクしている疑い）; "
            "覆えない閉包を覆ったと主張しない (fail-closed)。wheel ベースの環境で "
            "実測すること"
        )

    if name in _SCORER_NATIVE_BACKEND_REQUIRED and verify_pre_bind_gates:
        # Codex P1 6 巡目: P1-B（束縛前ロード済みマッピングの拒否）→ P1-A（DT_NEEDED
        # 閉包の包含証明）の順で検証する。ディスク上の実体を hash する前に、まず
        # 「メモリ上の実行がその実体と一致する保証があるか」を確かめるのが筋が通る
        # 順序（P1-A は disk-only の静的解析で、P1-B が拾う実行時の先読みまでは覆わない）。
        #
        # （セルフレビュー第三弾 H17）この 2 つは「束縛前に何が起きていたか」を
        # 検証する初回束縛専用ゲートであり、`verify_pre_bind_gates=False`
        # （mid-run checkpoint 経由）では丸ごと skip する——bytes hash が bind 時と
        # 一致する（下の `return` で判定される）なら、その bytes に埋め込まれた
        # DT_NEEDED soname も bind 時から変わりようがなく、P1-A の再検証は自明に
        # 冗長。P1-B（maps スキャン）はプロセス全体を見る性質上、numpy/scipy と
        # 無関係な JIT バックエンドの `memfd:`/削除済みマッピングまで拾って
        # over-strict に fail-closed しうるため、mid-run では実行しない。
        resolved_treat_anonymous = (
            _SCORER_PINS_INITIAL_BIND_COMPLETE
            if treat_anonymous_as_recorded is None
            else treat_anonymous_as_recorded
        )
        _reject_pre_bound_native_mappings(
            name,
            package_root=package_root,
            natives=natives,
            use_cache=use_cache,
            treat_anonymous_as_recorded=resolved_treat_anonymous,
        )
        _verify_scorer_dt_needed_closure(name, package_root=package_root, natives=natives)

    return sha256_of_files(sorted(natives), use_cache=use_cache)


def _scorer_optional_participated(name: str) -> bool:
    """任意閉包メンバー `name` がこのプロセスで実際に import され、observed import
    closure に participate したか（Codex 15 巡目 P2）。

    旧実装は presence を `importlib.metadata`（配布のインストール有無）だけで
    決めていたため、installed でも「選択された mir_eval/scipy/numpy 経路がその run
    で一度も import しない」場合まで一律 `present` として厳密 pin していた。結果、
    同一の環境で当該パッケージが未導入の run の report が homogeneous-scorer gate
    で「別閉包」と誤 reject されていた（同じ scorer 実装で測ったのに presence の
    自己申告だけが食い違う）。`sys.modules` メンバシップは import 以外の副作用を
    一切起こさない純粋な観測なので、これを observed import closure の判定に使う
    ——`test_scorer_runtime_packages_cover_observed_mir_eval_import_closure` が
    fresh subprocess で確認済みの「import 前後の sys.modules 差分」と同じ考え方を
    プロセス内判定に転用する。

    **16 巡目 P2-A の教訓（設計メモ）**: 素の `sys.modules` メンバシップだけを見る
    実装は、判定**時点**が「scoring そのものの import しか起きていない」ことに
    依存する——`_numeric_runtime_config()`（計測 instrumentation。`_threadpool_
    runtime_info()` 経由で threadpoolctl を import しうる）が scoring
    （category loop）**より前**に呼ばれると、そのタイミング汚染が
    `_require_unchanged_since_load()` の post-run pin 再計算に混入し、scoring
    経路が一度も使わない任意メンバーまで present と誤判定してしまう（15 巡目 P2 が
    解消したはずの false rejection が instrumentation import 経由で再発する）。

    当初はこの関数自体に「scoring 呼び出し区間で新たに import されたか」を判定する
    sys.modules baseline スナップショット機構を足す案を検討したが、`_fake_run()` が
    同一プロセス内で `run_accuracy()` を複数回呼ぶテスト（本ファイルの評価テストの
    大半が使う `reports = [_fake_run(...) for _ in range(2)]` パターン）や、評価器側
    （`_require_homogeneous_scorer`/`_require_fresh_process_report_provenance`）が
    baseline を持たず常に素の `sys.modules` を見る非対称性と衝突し、`repeats[0]`
    （初回呼び出しで baseline 未汚染）と `repeats[1]`（前回呼び出しの import が
    ambient に残り baseline 扱いされ absent 化）の presence が食い違って
    `_require_homogeneous_scorer` の repeats 間・評価環境再計算比較が偽陽性で
    fail-closed になる回帰を実測した（本番は repeat 毎に別プロセスなので本来
    起きないが、in-process シミュレーションのテストでは頻発する）。

    **採用した修正は本関数を変えず、呼び出し順序を直す（Codex 16 巡目 P2-B）
    ことに一本化した**: `run_accuracy` は `_numeric_runtime_config()` を
    category loop 完了後・かつ `_require_unchanged_since_load()`（本関数を介した
    pin 再計算）の**後**に呼ぶ。これにより、pin 再計算の時点では instrumentation
    による import がまだ一切発生していない——本関数が見る `sys.modules` は
    「scoring 自身の import 実行結果」のみを反映し、`_numeric_runtime_config()`
    が事後に行う（もし scoring が participate していなければ新規に起こる）
    threadpoolctl import は、既に確定済みの pin へ影響しない。素の `sys.modules`
    メンバシップという 15 巡目 P2 の判定方法自体は変えていないため、評価器側
    （baseline を持たない）や `_fake_run()` の repeats 間比較とも矛盾しない。
    """
    return name in sys.modules


def _ensure_scorer_optional_closure_observed() -> None:
    """observed import closure ベースの presence 判定を比較可能にするため、
    `mir_eval.melody` の import 連鎖をこのプロセスで（未 import ならこの場で）
    確定させる（Codex 15 巡目 P2）。

    `_require_homogeneous_scorer` / `_require_fresh_process_report_provenance` は
    「評価環境から再計算したスコアラー pin」と report の pin を突き合わせるが、
    評価器プロセス自身は測り直しを常に別プロセス（`_run_verification_in_fresh_
    process`）へ委譲し、`evaluate_melody_accuracy`/`mir_eval.melody` を自分では
    一度も呼ばない。presence を素朴に `sys.modules` だけで判定すると、評価器
    プロセスは常に「未 participate」＝absent になり、実際に participate した
    （present な）report と恒常的に不一致になってしまう——これは observed-closure
    化が意図した false rejection の解消ではなく、真逆の新しい false rejection
    である。

    `test_scorer_runtime_packages_cover_observed_mir_eval_import_closure` が
    fresh subprocess で実測済みのとおり、threadpoolctl/charset_normalizer の
    participate は `mir_eval.melody` を import するだけで（実際に評価関数へ
    データを渡すかどうかに関わらず）決定論的に確定する——scorer 実装コードと
    環境（scipy/numpy のバージョン・ビルド）だけに依存し、採点対象データには
    依存しない。よって、ここでの明示的な import は「実行してもいない
    participate を捏造する」ことにはならない: measured な行を 1 つでも持つ run の
    プロセスは、その実行の中で既にこの import を行っている（`evaluate_melody_
    accuracy` 内部の遅延 import）。この関数は、その run 自身は行わない評価器
    プロセスで「observed closure を比較可能にする」ための前提整備であり、
    import 以外の副作用（データ依存の計算）は一切起こさない。
    """
    if "mir_eval.melody" not in sys.modules:
        import importlib

        importlib.import_module("mir_eval.melody")


def _scorer_pins(
    *, use_cache: bool = True, treat_anonymous_as_recorded: "Optional[bool]" = None
) -> Dict[str, Any]:
    """指標を計算したスコアラー閉包（mir_eval + scipy + numpy）の version / code pin。

    **脅威モデル境界（Codex 14 巡目、決裁確定）**: この pin が守るのは受動的な
    取り違え・環境ドリフト・偶発的差し替え（別バージョンの数値ライブラリ、wheel 外
    BLAS、事前ロード等）の tamper-evidence であり、測定プロセスの env/PATH/
    site-packages/ファイルシステムを能動的に制御できる攻撃者への完全防御ではない
    （詳細: `docs/DESIGN_M2_extraction_accuracy.md` 「Scorer pin の脅威モデルと境界」）。

    `generator_code_sha256` は first-party 閉包に限っている（third-party を混ぜると
    環境差で digest が揺れる）ため、third-party の実装差はそこに現れない。一方
    `mir_eval>=0.7` は上限が無く、別リリースで測った row を同一 stack の repeats と
    数えれば「別の指標実装の出力」を再現性の証拠にしてしまう（Codex P1）。そこで
    row ではなく report レベルで、実際に呼んだスコアラー閉包（`_SCORER_RUNTIME_PACKAGES`）
    を pin する。キーは加算的な flat 形式（`{package}_version` / `{package}_code_sha256` /
    `{package}_dist_native_sha256`）。3 つ目は numpy/scipy の wheel が同梱する
    `{package}.libs/`（OpenBLAS 等）を覆う（Codex P1 2 巡目・`_scorer_dist_native_sha256`）。

    **import を起こさずに** 取る: version は `importlib.metadata`（配布メタデータを
    読むだけ）、コード hash は `package_code_sha256`（find_spec で場所だけ解決）、
    同梱ネイティブ hash は RECORD 読みのみ。`import mir_eval` してから hash すると、
    先に読み込まれていた旧モジュールが実行される一方で hash は新しいディスクを見る
    窓が開く——first-party 閉包と同じ #217 の規律を third-party スコアラーにも
    適用する（Codex P1）。

    **束縛の前に**（Codex P1 6 巡目 P1-B）`_reject_ld_preload_before_scorer_bind` で
    `LD_PRELOAD` の非空を拒否する。値そのものの精査経路は用意しない——`LD_PRELOAD`
    が立っている限り、以降どの pin を計算してもディスク hash が実行中の実装を代表
    する保証がないため、計算に入る前に fail-closed で止める。

    **任意メンバーの presence は「導入されているか」でなく「観測閉包に
    participate したか」（Codex 15 巡目 P2）**: `_SCORER_RUNTIME_PACKAGES_OPTIONAL`
    （threadpoolctl/charset_normalizer）は `_scorer_optional_participated()`
    （`sys.modules` メンバシップ、import を起こさない純粋な観測）が `True` の
    ときだけ present として厳密 pin する。1 回目の呼び出し（load 時、`import
    numpy` 等より前）は必然的に何も import されていないので必ず absent の
    暫定値になる——これは「2 段構え」の 1 段目（load 時記録）であり、確定した
    presence ではない。実測経路（run 側は実際に指標計算を行った後、evaluate 側は
    `_ensure_scorer_optional_closure_observed()` で observed closure を確定させた
    後）で呼ぶ 2 回目以降が最終値になる。`_require_unchanged_since_load` は
    この absent→present の遷移を swap-and-restore と誤認しないよう、load 時に
    absent だった任意メンバーの 4 フィールドを比較対象から除外する。

    `treat_anonymous_as_recorded`（Codex 9 巡目 CI 追加分）: 省略時（`None`）は
    `_SCORER_PINS_INITIAL_BIND_COMPLETE`（本ファイル冒頭付近、`_scorer_pins()` の
    最初の呼び出し完了後に一度だけ `True` へ切り替わる）から自動導出する——1 回目
    の呼び出し（load 時、`import numpy` 等より前）だけが厳格（`False`）、2 回目
    以降（`_require_unchanged_since_load` の post-run 再検証・
    `_require_homogeneous_scorer`・`_require_fresh_process_report_provenance` 等、
    実測で判明した呼び出し元の多さに対して個別の引数持ち回しは取りこぼしやすい）は
    自動的に緩和される。明示的に `True`/`False` を渡せばそちらを優先する（既存の
    直接呼び出しテストの厳格挙動はそのため変わらない）。詳細は
    `_reject_pre_bound_native_mappings` の docstring 参照。
    """
    import importlib.metadata

    _reject_ld_preload_before_scorer_bind()

    pins: Dict[str, Any] = {}
    for name in _SCORER_RUNTIME_PACKAGES:
        is_optional = name in _SCORER_RUNTIME_PACKAGES_OPTIONAL
        if is_optional and not _scorer_optional_participated(name):
            # Codex 15 巡目 P2: presence を「導入されているか」（旧実装）ではなく
            # 「この呼び出し時点でこのプロセスが実際に import し、observed import
            # closure に participate したか」で判定する。未導入（そもそも import
            # されようがない）と、導入済みだが選択された経路が一度も触れなかった
            # （installed-but-unused）の両方を同じ absent 記録に正直に一本化する
            # ——後者を旧実装のように present と誤記録すると、同じ scorer 実装で
            # 測ったのに未導入環境の report と homogeneous-scorer gate で誤って
            # 別閉包と判定される（false rejection、#217 原則の「pin が実際に
            # 実行された実装に接続しているか」をここにも適用）。
            # 「import されなければ absent で pin されない」ことは pin 回避の穴では
            # ない: import closure は実測で確認済みの実行閉包そのもの
            # （`test_scorer_runtime_packages_cover_observed_mir_eval_import_closure`）
            # なので、実際に指標計算へ participate しない optional は materiality が
            # ゼロであり、pin する必要そのものが無い。participate すれば必ず
            # sys.modules に現れ、下の分岐で present として厳密 pin される。
            pins[f"{name}_version"] = None
            pins[f"{name}_code_sha256"] = None
            pins[f"{name}_dist_native_sha256"] = None
            pins[f"{name}_closure_state"] = "absent"
            continue
        try:
            version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            version = None
        if is_optional and version is None:
            # participate した（sys.modules に現れた）のに配布メタデータが引けない
            # のは「import されたのに未導入を自称する」矛盾——presence 判定の前提
            # そのものが壊れているので fail-closed にする（実運用では起こりえない
            # 異常系: 通常の pip/wheel install ではあり得ない）。
            raise RuntimeError(
                f"_scorer_pins: 任意メンバー {name!r} は sys.modules に存在し観測閉包に "
                "participate したのに importlib.metadata.version が引けない; presence "
                "判定の前提（import されたなら導入されている）が壊れている (fail-closed)"
            )
        pins[f"{name}_version"] = version
        pins[f"{name}_code_sha256"] = package_code_sha256(name, use_cache=use_cache)
        pins[f"{name}_dist_native_sha256"] = _scorer_dist_native_sha256(
            name, use_cache=use_cache, treat_anonymous_as_recorded=treat_anonymous_as_recorded
        )
        if is_optional:
            # participate した任意メンバーは必須メンバーと全く同じ厳密さで完全 pin
            # を要求する（「実際に使われていれば通常運転どおり」・弱めない）。
            pins[f"{name}_closure_state"] = "present"
    return pins


def _scorer_pinned_origins() -> Dict[str, str]:
    """束縛時点で `find_spec` が解決した scorer 各パッケージの origin（実パス）。

    （セルフレビュー H8）`_require_scorer_modules_match_pinned_origin` が post-run に
    `sys.modules[name].__spec__.origin` と突き合わせる基準値。import を起こさない
    （`find_spec` のみ）ので、`_scorer_pins()` と同じ load-time 束縛規律を保つ。
    """
    import importlib.util

    origins: Dict[str, str] = {}
    for name in _SCORER_RUNTIME_PACKAGES:
        try:
            spec = importlib.util.find_spec(name)
        except Exception:
            continue
        if spec is None or not getattr(spec, "origin", None):
            continue
        if spec.origin in ("built-in", "frozen"):
            continue
        origins[name] = str(Path(spec.origin).resolve())
    return origins


def _scorer_kernel_submodule_pinned_origins() -> Dict[str, str]:
    """束縛時点で確定する `_SCORER_KERNEL_SUBMODULES` の期待 origin（import を起こさない）。

    （セルフレビュー第二弾 H13）`find_spec("mir_eval.melody")` はドット付き名前解決の
    CPython 仕様上、親パッケージ `mir_eval` の import を引き起こす——`_scorer_pins()`/
    `_scorer_pinned_origins()` が守る「束縛は import より前」の規律を破ってしまうため
    使えない。代わりに、既に束縛済みでインポート無しに解決した
    `_SCORER_PINNED_ORIGINS["mir_eval"]`（トップレベルの `find_spec` のみ）から
    package_root を導出し、サブモジュール名をドット区切りでパス結合するだけの
    純粋なファイルシステム操作（`.is_file()` の stat のみ・import なし）で期待パスを
    構築する。
    """
    origins: Dict[str, str] = {}
    for dotted in _SCORER_KERNEL_SUBMODULES:
        top, _, rest = dotted.partition(".")
        top_origin = _SCORER_PINNED_ORIGINS.get(top)
        if top_origin is None:
            continue
        package_root = Path(top_origin).parent
        candidate = package_root.joinpath(*rest.split(".")).with_suffix(".py")
        if candidate.is_file():
            origins[dotted] = str(candidate.resolve())
    return origins


_LOADED_SCORER_PINS = _scorer_pins()

# この 1 回目の呼び出しだけが「束縛前に何も先読みされていないはず」という H11 の
# 不変条件が実際に成立する文脈（Codex 9 巡目 CI 追加分）。以降 `_scorer_pins()` を
# 呼ぶすべての箇所（`treat_anonymous_as_recorded` を明示しない限り）は自動的に
# 匿名マッピングの即時 raise を緩和する。
_SCORER_PINS_INITIAL_BIND_COMPLETE = True

# H8: 束縛時点の origin を凍結する（post-run に実際の import 結果と突き合わせる基準値）。
_SCORER_PINNED_ORIGINS: Dict[str, str] = _scorer_pinned_origins()

# 第二弾 H13: カーネルサブモジュール（`mir_eval.melody`/`mir_eval.util`）の期待
# origin も同じタイミングで凍結する（`_SCORER_PINNED_ORIGINS` の直後——このヘルパー
# 自身がその値に依存するため）。
_SCORER_KERNEL_SUBMODULE_PINNED_ORIGINS: Dict[str, str] = (
    _scorer_kernel_submodule_pinned_origins()
)

# `_scorer_pins()` が numpy/scipy それぞれについて `_reject_pre_bound_native_mappings`
# を実行済み（束縛前マッピングは `_PRE_BOUND_NATIVE_MAPPING_LOG` へ追記されている）。
# ここで 1 度だけタプルへ凍結する（Codex P1 7 巡目・`_PRELOADED_SEED_MODULES` と同じ
# 「load 時 1 回だけ確定」規約。以降の再検証呼び出しがログへ追記し続けても、この
# 凍結タプルは影響を受けない）。
_PRE_BOUND_SCORER_NATIVE_MAPPINGS: "Tuple[str, ...]" = tuple(_PRE_BOUND_NATIVE_MAPPING_LOG)

# H3: meta_path/path_hooks/path_importer_cache の非標準構成も同じ「load 時 1 回だけ
# 確定」規約で凍結する（`_PRELOADED_SEED_MODULES` / `_PRE_BOUND_SCORER_NATIVE_MAPPINGS`
# と同型）。
_NON_STANDARD_IMPORT_HOOKS: "Tuple[str, ...]" = tuple(_non_standard_import_hooks())


def _mir_eval_paths() -> List[Path]:
    """provenance のために hash するスコアラー閉包のファイル群（`--out` 保護用）。

    関数名は歴史的経緯で `mir_eval` のままだが、`_scorer_pins()` が pin する閉包
    全体（`_SCORER_RUNTIME_PACKAGES` = mir_eval + scipy + numpy + セルフレビュー H1 で
    追加した decorator/threadpoolctl/charset_normalizer）を回って一般化する。
    `_scorer_pins()` の pin 対象より保護集合が狭いと、`--out` が scipy/numpy の
    ソースを指したときに「pin 済みの実行コードを report で潰す」穴が残る。

    **単一モジュール配布の走査限定（Codex 9 巡目 P2）**: `threadpoolctl`
    （`submodule_search_locations is None`）のように site-packages 直下に 1 ファイル
    だけを置く配布で `spec.origin.parent` を無条件に rglob すると、**site-packages
    全体**（他の無関係な distribution も含む数千ファイル）を巻き込む——`#217` の
    「`soundfile.py` 単一モジュールで site-packages 全体を hash してしまう」不具合と
    同型の再発（`_SCORER_RUNTIME_PACKAGES` に H1 で threadpoolctl を追加するまでは
    このコード経路を single-file 配布が一度も通らず、潜在していた）。
    `_runtime_input_paths()` が既に正しく実装している「単一モジュールは本体 +
    `_module_companion_files` の同伴物だけに限定する」規約に揃える。

    **定義位置（Codex 10 巡目 P1-B）**: 元は `import numpy as np` 等の実 scorer
    import より**後**（`_runtime_input_paths()` の隣）に定義されていたが、P1-B の
    `_scorer_load_time_expected_hashes()`（audit hook の期待値表）がこの関数を
    scorer import より**前**（束縛シーケンスの一部として）呼ぶ必要があるため、
    ここへ移動した。本体は `importlib.util.find_spec`（import を起こさない）と
    `svp_rpe.melody.provenance._module_companion_files`（関数内 import）だけに
    依存し、呼び出し側の位置制約は無い——呼び出し元をこちらへ動かすのではなく、
    定義をここへ動かす方が既存の呼び出し箇所（`_runtime_input_paths()`・CLI の
    `--out` 保護等）を一切変更せずに済む。
    """
    import importlib.util

    from svp_rpe.melody.provenance import _module_companion_files

    paths: "set[Path]" = set()
    for name in _SCORER_RUNTIME_PACKAGES:
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError, AttributeError, ModuleNotFoundError):
            continue
        if spec is None or spec.origin in (None, "built-in", "frozen"):
            continue
        origin_path = Path(spec.origin).resolve()
        root = origin_path.parent
        if getattr(spec, "submodule_search_locations", None) is None:
            # 単一モジュール: 親ディレクトリ（site-packages 全体）を rglob しない。
            # 本体 + 規約で決まる同伴物だけを対象にする（Codex 9 巡目 P2）。
            paths.add(origin_path)
            try:
                paths.update(p.resolve() for p in _module_companion_files(name, root))
            except OSError:
                continue
        else:
            paths.update(p.resolve() for p in root.rglob("*.py"))
    return sorted(paths)


# P1-B（Codex 10 巡目）: scorer .py の「束縛時点の期待 sha256」を確定し、
# swap-and-restore 検出用の audit hook を実際の scorer import より前に設置する
# （「束縛と同時に」——このブロックの直後で初めて `import numpy` 等が起きる）。
_SCORER_LOAD_TIME_EXPECTED_HASHES = _scorer_load_time_expected_hashes()
sys.addaudithook(_audit_scorer_source_load_time_hash)

import numpy as np  # noqa: E402

# セルフレビュー第二弾 H14: numpy の native カーネル（`numpy/_core/_multiarray_
# umath.cpython-*.so` 等）が bind から import 完了までの間に差し替えられていない
# ことを、import 完了の直後にできるだけ早く確認する（`_require_scorer_native_
# unchanged_since_bind` docstring 参照。境界の正直会計あり）。
_require_scorer_native_unchanged_since_bind()

def _package_code_state_for_bind(name: str) -> "Tuple[str, Optional[str]]":
    """1 パッケージのコード hash を束縛用に採る（未導入は `(state, None)`）。

    `packages_code_sha256` と同じ判定規約を単体で使うための薄い包み——未導入は
    「実行されない」ので skip、**導入済みで hash できない場合は送出**（実装の一部を
    覆わない pin を環境同一性として使わない）。
    """
    from svp_rpe.melody.provenance import (
        STATE_ABSENT,
        STATE_UNHASHABLE,
        package_code_state,
    )

    state, digest = package_code_state(name)
    if state == STATE_ABSENT:
        return state, None
    if state == STATE_UNHASHABLE or not digest:
        raise RuntimeError(
            f"実行パッケージ {name!r} は導入済みだがコード hash を採れない "
            "(namespace/zip 配置・読めないファイル); 実装を覆わない pin を環境同一性と "
            "して使わない (fail-closed)"
        )
    return state, digest


# 束縛（bind）: **`import soundfile` より前**に libsndfile 実体を pin する。import 後に
# 束縛すると、実体が差し替えられた場合にプロセスは dlopen 済みの旧 libsndfile で読み
# 続けたまま新しい bytes の digest を名乗る（numpy/scipy は `_scorer_pins` が既に
# import 前束縛済み。ここはその機構が覆っていない module scope の残り）。
# 完全集合の束縛は定義が出揃った後に `_bind_all_dist_native_pins()` が追記する。
_LOADED_DIST_NATIVE_PINS: Dict[str, str] = {
    "soundfile": _scorer_dist_native_sha256("soundfile", verify_pre_bind_gates=False)
}
# 同じ理由で **コード側**も import 前に束縛する。native だけ pin しても、
# `soundfile.py`（libsndfile を叩く Python ラッパ）が import 後に差し替えられれば、
# デコードは in-memory の旧ラッパを通り続けるのに digest は新しいディスクを指す。
# numpy/scipy 等 `_SCORER_RUNTIME_PACKAGES` のコード pin は `_scorer_pins` が既に
# import 前束縛済み——ここはその機構が覆っていない module scope の残り。
_LOADED_RUNTIME_CODE_PINS: Dict[str, str] = {}
for _eager in ("soundfile",):
    _eager_state, _eager_digest = _package_code_state_for_bind(_eager)
    if _eager_digest is not None:
        _LOADED_RUNTIME_CODE_PINS[_eager] = _eager_digest

import soundfile as sf  # noqa: E402
import yaml  # noqa: E402
from build_melody_bench import build_signal  # noqa: E402

from svp_rpe.arrange.pathsafe import (  # noqa: E402
    PathConfinementError,
    resolve_confined,
    validate_relative_locator,
)
from svp_rpe.melody.accuracy import (  # noqa: E402
    DEFAULT_TOLERANCE_CENTS,
    MelodyAccuracyResult,
    evaluate_melody_accuracy,
    reference_f0_from_monophonic_spec,
)
from svp_rpe.melody.extractors import observe_via_route_with_provenance  # noqa: E402
from svp_rpe.melody.observability import MelodyObservation  # noqa: E402
from svp_rpe.melody.routing import MelodyRoute, select_routes  # noqa: E402
from svp_rpe.rpe.learned import LearnedModelUnavailable  # noqa: E402
# M2e §8.7: セルレコードの atomic write は既存の共通実装を再利用する（新しい書き込み
# 機構を作らない、と設計が明示している）。`_atomic_write_text`（本ファイル既存）とは
# 別物 —— あちらは `--out` の verdict/report 専用に育った独自実装で、fsync まで含めて
# 意図的に厚い。セルレコードは大量（最大 1280 件）に書くため、utils 側の薄い実装を
# そのまま使う。
from svp_rpe.utils.atomic_io import atomic_write_bytes as _cell_store_atomic_write_bytes  # noqa: E402
from svp_rpe.utils.atomic_io import atomic_write_text as _cell_store_atomic_write_text  # noqa: E402


def _runtime_input_paths() -> "set[Path]":
    """route が実行時に読む third-party 推論コード + モデル重みの実パス（`--out` 保護用）。

    保護集合が bars / specs / first-party 閉包 / mir_eval 止まりだと、`--out` が
    CREPE/Demucs の重みや推論コードを指した場合に「読んで hash した入力を report で
    潰す」ことになる（Codex P2。PR Notes で M2b へ繰延していた項目）。未導入の
    パッケージ・未取得の重みは解決できない = 入力として読まれることも無いので skip。

    ファイル集合は `provenance.package_code_state` と同じ規約で列挙する（`.py` だけ
    でなくネイティブ拡張 `.so`/`.pyd`/`.dylib`・版番号付き共有ライブラリも対象。
    pin が hash する集合より保護集合が狭いと、`--out` が NumPy 等の `.so` を指した
    ときに「pin 済みの実行コードを report で潰す」穴が残る — Codex P2 第 22 巡）。
    """
    import importlib.util

    from svp_rpe.melody.provenance import (
        _CODE_SUFFIXES,
        _is_native_library,
        _module_companion_files,
    )

    paths: "set[Path]" = set()
    for name in _runtime_package_names():
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError, AttributeError, ModuleNotFoundError):
            continue
        if spec is None or spec.origin in (None, "built-in", "frozen"):
            continue
        origin_path = Path(spec.origin).resolve()
        root = origin_path.parent
        try:
            if getattr(spec, "submodule_search_locations", None) is None:
                # 単一モジュール: 親 dir を rglob すると site-packages 全体を巻き込む。
                # provenance と同じく本体 + 規約同梱物のみ。
                paths.add(origin_path)
                paths.update(p.resolve() for p in _module_companion_files(name, root))
            else:
                paths.update(
                    p.resolve()
                    for p in root.rglob("*")
                    if p.is_file()
                    and (p.suffix in _CODE_SUFFIXES or _is_native_library(p))
                    and "__pycache__" not in p.parts
                )
        except OSError:
            continue
    try:
        from svp_rpe.rpe.learned.crepe_adapter import crepe_weight_files

        paths.update(p.resolve() for p in crepe_weight_files())
    except Exception:
        pass
    try:
        from svp_rpe.rpe.learned.source_separation_adapter import locate_separation_weights

        paths.update(p.resolve() for p in locate_separation_weights())
    except Exception:
        pass
    # `separation_code_fingerprint` が読む FFmpeg/ffprobe 実行ファイル + libav* closure
    # と、`_scorer_pins` の version pin が読む mir_eval 配布メタデータ（dist-info）も
    # 保護する。パッケージツリー + 重みだけでは pin の実読集合より狭い（Codex P2
    # 第 24 巡）。demucs 未導入なら executables は空 = 読まれないので対象外。
    import shutil

    from svp_rpe.melody.provenance import (
        _ffmpeg_library_closure,
        _separation_audio_executables,
    )

    try:
        tools, _required = _separation_audio_executables()
    except Exception:
        tools = ()
    for tool in tools:
        resolved = shutil.which(tool)
        if resolved is None:
            continue
        paths.add(Path(resolved).resolve())
        try:
            paths.update(p.resolve() for p in _ffmpeg_library_closure(Path(resolved)))
        except Exception:
            continue  # closure を読めない環境では fingerprint 側も失敗 = 読まれない
    # 配布メタデータ（dist-info）は mir_eval 特例でなく **全 runtime パッケージ**分を
    # 保護する。run は `separation_version`（demucs）等のために importlib.metadata を
    # 読むので、mir_eval だけの列挙では pin の実読集合より狭い（Codex P2 第 36 巡）。
    # import 名 ≠ 配布名のケースに備え packages_distributions の写像も引く。
    import importlib.metadata

    try:
        dist_map = importlib.metadata.packages_distributions()
    except Exception:
        dist_map = {}
    dist_names: "set[str]" = set()
    for name in _runtime_package_names():
        top = name.split(".")[0]
        dist_names.add(top)
        dist_names.update(dist_map.get(top, ()))
    for dist_name in sorted(dist_names):
        try:
            dist = importlib.metadata.distribution(dist_name)
        except Exception:
            continue  # 未導入の配布は読まれない = 入力でない
        for record in dist.files or ():
            located = Path(str(dist.locate_file(record)))
            if located.is_file():
                paths.add(located.resolve())
    return paths


SPECS_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "m2_accuracy_specs.yaml"
BARS_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "m2_accuracy_bars.yaml"
# M2c: カテゴリ V（実声・外部素材）の事前登録 content pin。`m2_accuracy_bars.yaml` /
# `m2_accuracy_specs.yaml` とは意図的に別ファイル（同ファイル冒頭コメント参照・
# registry.yaml 系の凍結チェーンを外部素材の追記で drift させないため）。
EXTERNAL_FIXTURES_PATH = (
    ROOT / "tests" / "fixtures" / "melody_bench" / "m2c_external_fixtures.yaml"
)
# M2e（`docs/DESIGN_M2e_vremix_real_bed.md` §5.1）: M2e の帯は **別ファイル**に置く。
# `m2_accuracy_bars.yaml` へ追記すると `bars_sha256` が変わり、commit 済みの
# M2b / M2c verdict の pin が壊れる——それはこの機構の**偽陽性**である（M2b / M2c が
# 使ったバーの中身は 1 バイトも変わらないため）。リポジトリは同じ理由で既に 2 回
# この分離をしている（registry.yaml → m2_accuracy_bars.yaml → m2c_external_fixtures.yaml）。
# 3 例目として同じ流儀に従う。**M2b / M2c の再実測は行わない。**
M2E_BARS_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "m2e_accuracy_bars.yaml"
# M2e ベッド登録簿（r3 で pin 済み）。ハーネスは中身を解釈しないが、生成物が名乗る
# `builder.m2e_bed_fixtures_sha256` を**測る側の実体**と突き合わせるために持つ。
M2E_BED_FIXTURES_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "m2e_bed_fixtures.yaml"
# 混合式そのもの（生成器のコード）。生成物が名乗る `builder.generator_code_sha256` を
# **測る側が持っている実体**と突き合わせるために持つ（登録簿 pin と同じ扱い）。
M2E_MIXER_SCRIPT_PATH = ROOT / "scripts" / "make_vremix_fixtures.py"

_EXPECTED_BARS_SCHEMA = "m2-accuracy-bars/0.1"
# M2e のバーは `m2-accuracy-bars/0.1` を名乗らせない（設計 §5.1-2）。
_EXPECTED_M2E_BARS_SCHEMA = "m2e-accuracy-bars/0.1"
# run report 自身のスキーマ discriminator。report の形が変わっても現在検査している
# フィールドが残っていると、evaluate が新旧を区別せず旧セマンティクスで解釈しうる
# （Codex P2）。bars と同じ規律を report にも適用し、未知/欠落は評価前に弾く。
_EXPECTED_REPORT_SCHEMA = "m2-accuracy-report/0.1"
# 合成仕様のスキーマ discriminator（同じ規律を specs にも適用・Codex P2）。
_EXPECTED_SPECS_SCHEMA = "m2-accuracy-specs/0.1"
# publish される verdict 自身の discriminator。保存済み verdict を後から読む側が、
# 新形式/非互換形式を fail-closed で拒否できるようにする（Codex P2）。
_EXPECTED_VERDICT_SCHEMA = "m2-accuracy-verdict/0.1"
# 外部素材の事前登録 pin ファイルのスキーマ discriminator（M2c、同じ規律）。
_EXPECTED_EXTERNAL_FIXTURES_SCHEMA = "m2c-external-fixtures/0.1"
# M2e（設計 §10）: 帯ごとに pin ファイルを分ける（M2c の登録集合へ V-remix ミックスの
# hash を追記すると `external_fixtures_sha256` が変わり、commit 済み M2c verdict の pin が
# 壊れる）。**受理する schema の集合を r1 の時点で開けておく**——r3（`P-c`）と r6（`P-d`）は
# いずれも「code change なし」の段階なので、そこで新 schema を通すためのコード変更が
# 必要になると段階の契約が壊れる。未知の schema は従来どおり fail-closed で拒否する。
_EXPECTED_M2E_EXTERNAL_FIXTURES_SCHEMA = "m2e-external-fixtures/0.1"
_EXPECTED_EXTERNAL_FIXTURES_SCHEMAS: Tuple[str, ...] = (
    _EXPECTED_EXTERNAL_FIXTURES_SCHEMA,
    _EXPECTED_M2E_EXTERNAL_FIXTURES_SCHEMA,
)
# §8.7 セル台帳レコードの schema discriminator。**持続する成果物**なので、他の
# 登録簿と同じく版を名乗らせる——同一性フィールドが揃っていても、レコードの
# 意味論（どのフィールドが何を指すか）が変われば別物である。版が無い/未知の
# レコードは resume 対象にしない（`_cell_record_mismatches` が不一致として扱い、
# そのセルは再測定される）。
# 0.2 → 0.3（2026-08-02）: 時刻フィールドを完了時刻 `measured_utc` から**測定開始
# 時刻** `measurement_started_utc` へ改めた（事前登録の順序検査が要求するのは開始で
# あって完了ではない——長いセルは登録前に始まって登録後に終わりうる）。旧版の
# レコードは版の不一致で resume されず再測定される——時刻を後から埋めることはしない。
# 0.3 → 0.4（2026-08-02・C2）: `store_role` を必須フィールドに加えた（PR #240 Codex P1）。
# **パスの分離は計算の独立を意味しない**——`store_A` を別ディレクトリへコピーすれば
# 経路検査は通り、コピーされたレコードは他の同一性フィールドを全部満たすので、検証の子が
# run のセルを resume して publish が再び自己比較になる。役割をレコード自身に束縛し、
# evaluate のキャッシュで run 由来のレコードを resume しないことで、この経路を消す。
_EXPECTED_CELL_RECORD_SCHEMA = "m2-cell-record/0.4"

# セルレコードの**役割**（`store_role`）。run が書いたセルと evaluate の測り直しが
# 書いたセルは、同じ鍵・同じ入力・同じ環境でも**別の計算**であり、混ぜると
# 「独立に測り直して bit 一致」という publish 条件が自己比較に化ける。
_CELL_STORE_ROLE_RUN = "run"
_CELL_STORE_ROLE_EVALUATE = "evaluate"
_CELL_STORE_ROLES = (_CELL_STORE_ROLE_RUN, _CELL_STORE_ROLE_EVALUATE)

# バーを持たず「診断記録のみ」で良いカテゴリ（設計 §3/§8: Demucs は合成音色に対し
# 分布外なので S_fullstack の低値を理由に crepe を責めない）。**この集合の外の
# カテゴリが空バーで来たら fail-closed** ——`--bars` が S_direct を落としただけで
# 必須の受け入れゲートが黙って無効化されるのを防ぐ（Codex P2）。
_DIAGNOSTIC_ONLY_CATEGORIES: "frozenset[str]" = frozenset({"S_fullstack"})
# 受け入れゲートを持つカテゴリごとに、設計 §4 で事前登録した**完全な**閾値キー集合。
# `("min_rpa",)` の一律最低要件では `S_direct: {min_rpa: ...}` だけのバーが通り、
# 凍結済みの max_vfa ゲートが黙って無効化される（Codex P2 第 22 巡）。ここに無い
# ゲート付きカテゴリは loader が fail-closed で拒否する。
_REQUIRED_BAR_KEYS_BY_CATEGORY: Dict[str, Tuple[str, ...]] = {
    "S_direct": ("min_rpa", "max_vfa"),
    # M2c: V_direct（実声・分離なし）は S_direct と同じ「抽出器の健全性バー」の
    # 精神だが、正解が外部注釈（自動生成ではない）なので max_vfa は事前登録しない
    # （設計 Memo M2c・m2_accuracy_bars.yaml の V_direct 節参照）。
    "V_direct": ("min_rpa", "max_octave_gap"),
    # M2e（設計 §5.3）: 両アームとも `("min_rpa", "max_octave_gap")`。**VFA / VR は
    # バー外・診断記録のみ** —— M2d が voicing 非信頼を確定済みで、下流の M3 / M4 は
    # 抽出器 voicing を消費しない（M3 は共有有声整列のみ・M4 は axis_evidence のみ）。
    # 消費されない軸で帯全体を fail させると S_direct の再演になる。
    "V_remix_real_direct": ("min_rpa", "max_octave_gap"),
    "V_remix_real_stem": ("min_rpa", "max_octave_gap"),
}

# バーを持つカテゴリが宣言しなければならない**測定条件**キー（設計 §5.3）。
# `gate_level` / `levels` は judge が数値比較する閾値ではなく「何を測ったか」の宣言
# であり、`_BAR_THRESHOLD_RANGES`（有限数値・値域つき）を通せない。通すために検査を
# 緩めることは禁止（設計 附録A-4）。よって**バー block の兄弟**として独立の条件
# block に置き、専用の必須キー表で fail-closed 検証する。
_REQUIRED_CONDITION_KEYS_BY_CATEGORY: Dict[str, Tuple[str, ...]] = {
    "V_remix_real_direct": ("gate_level", "levels"),
    "V_remix_real_stem": ("gate_level", "levels"),
}

# M2e §3.4.1 の 20 dB 不変量。**実験全体で唯一の宣言**であり、`m2e_bed_fixtures.yaml`
# の `residual_db <= -26.0` は導出値。ここで凍結値として持つのは「条件 block が
# 宣言した値がこの不変量と一致すること」を機械検証するため（緩める方向の書き換えを
# 設計文書の目視レビューに委ねない）。
_M2E_LEVEL_MARGIN_DB = 20.0

# M2e §3.6 の水準ラダー（4 点・**順序込みで**凍結）。文字列としては並べない
# （§3.3.1: `"+12dB" / "+6dB" / "0dB" / "-6dB"` をバイト辞書順に並べると
# `+12dB, +6dB, -6dB, 0dB` となり物理量と無関係な順序になる）。整列にはこの宣言順の
# 添字 `ladder_index` を使う。
_M2E_LEVEL_LADDER: Tuple[str, ...] = ("+12dB", "+6dB", "0dB", "-6dB")
# entry id 規約（設計 §6.2）: `vremix_{clip_id}_{bed_id}_{level_tag}`。
_M2E_LEVEL_TAGS: Dict[str, str] = {
    "+12dB": "p12",
    "+6dB": "p06",
    "0dB": "p00",
    "-6dB": "m06",
}


def _m2e_ladder_index(level: str) -> int:
    """水準ラダーの宣言順添字（設計 §3.3.1 の `ladder_index`）。"""
    return _M2E_LEVEL_LADDER.index(level)


# ---------------------------------------------------------------------------
# M2e §8.7 — セルチェックポイント（opt-in・既定 off で挙動無変更）
# ---------------------------------------------------------------------------
#
# 実行単位は 1 セル = (clip_id, bed_id, level, arm, repeat_idx)。本ハーネスの
# 内部表現では `entry_id`（= manifest の id。M2e では `vremix_{clip_id}_{bed_id}_
# {level_tag}` が clip/bed/level を既に畳んでいる）と `category`（= arm。
# `V_remix_real_direct` / `V_remix_real_stem`）でこれを表す。よってセル鍵は
# `(category, level, entry_id, repeat_index)` の 4 要素タプルになる
# （`_cell_store_record_path` / `_measure_or_resume_external_clip_row` 参照）。

# `env_digest` に折り込む third-party パッケージ（設計 §8.7）。torch は demucs の
# 依存で明示列挙が要る（demucs 自体は動作しても torch のマイナーバージョン差で
# BLAS/カーネル選択が変わりうるため独立に記録する）。
_ENV_DIGEST_PACKAGES: Tuple[str, ...] = (
    "torch",
    "demucs",
    "crepe",
    "librosa",
    "soundfile",
    "numpy",
)
# 未導入パッケージ・未解決の重みを「黙って省く」のではなく明示マーカーで記録する
# （設計 §8.7 実装ノート: 欠落自体が環境の一部であり、記録から落とすと
# 「重みが再取得され別 digest になった」と「単に記録されなかった」が区別できない）。
_ENV_DIGEST_ABSENT_MARKER = "absent"


# ---------------------------------------------------------------------------
# bars ファイルの同一性とカテゴリ所有権（設計 §5.2）
# ---------------------------------------------------------------------------
# 各カテゴリは **ちょうど 1 つの bars ファイルに所有される**。所有権は
# `_CATEGORY_SPECS` の各行が持つ `bars_file` で表現し、バー検証は**所有カテゴリのみ**
# を対象に行う（他ファイルのカテゴリの不在を欠落と見なさない）。この分離がなければ、
# `_CATEGORY_SPECS` に M2e カテゴリを足した瞬間 `m2_accuracy_bars.yaml` の検証が
# 「M2e のバーが空だ」と誤爆する。
#
# ファイル同一性は **パス名ではなく `schema_version`** から決める。テストや測り直し
# 子プロセスは bytes を tmp へ凍結複製して別名で渡すため、ファイル名に依存した判定は
# その経路で崩れる。
_BARS_FILES: Dict[str, Dict[str, Any]] = {
    "m2_accuracy_bars.yaml": {
        "schema": _EXPECTED_BARS_SCHEMA,
        "block_key": "m2_accuracy_bars",
        "conditions_key": None,
        # 共有スカラー（tolerance_cents / est_voiced_confidence_floor / repeats_min）を
        # 宣言するのはこのファイルだけ（設計 §5.1-4: 二重定義は必ず食い違う）。
        "declares_shared_scalars": True,
        "default_path": BARS_PATH,
    },
    "m2e_accuracy_bars.yaml": {
        "schema": _EXPECTED_M2E_BARS_SCHEMA,
        "block_key": "m2e_accuracy_bars",
        "conditions_key": "m2e_measurement_conditions",
        "declares_shared_scalars": False,
        "default_path": M2E_BARS_PATH,
    },
}

_BARS_FILE_BY_SCHEMA: Dict[str, str] = {
    spec["schema"]: name for name, spec in _BARS_FILES.items()
}

# 共有スカラー: M2 側の値を使い、M2e 側で**再宣言しない**（設計 §5.1-4）。
_SHARED_BAR_SCALARS: Tuple[str, ...] = (
    "tolerance_cents",
    "est_voiced_confidence_floor",
    "repeats_min",
)

# カテゴリ → (fixture/composite id, select_routes 用 input_kind, 期待する route 名)。
# route 名は `svp_rpe.melody.routing._ROUTES` の既存表から**そのまま**選ぶ
# （routing.py は変更しない・設計 §1）。crepe_direct / demucs_vocals_then_crepe は
# 既に vocal 用途で配線済みの経路だが、経路自体は抽出器（前処理 + crepe）を表すだけ
# で入力の音楽的内容（旋律 vs ボーカル）を知らないため、S カテゴリ（合成旋律）にも
# 転用できる。
_CATEGORY_SPECS: Dict[str, Dict[str, str]] = {
    "S_direct": {
        "kind": "direct",
        "fixture_id": "m2_s_direct_melody",
        "input_kind": "clear_lead",
        "route_name": "crepe_direct",
        "bars_file": "m2_accuracy_bars.yaml",
    },
    "S_fullstack": {
        "kind": "fullstack",
        "composite_id": "m2_s_fullstack_mix",
        "input_kind": "full_mix",
        "route_name": "demucs_vocals_then_crepe",
        "bars_file": "m2_accuracy_bars.yaml",
    },
    # M2c: V_direct（実声・分離なし）は合成 spec ではなく `--external-manifest` が
    # 指す外部素材（音声 + 注釈 CSV）に対して測る。`kind: "external"` の row は
    # fixture_id/composite_id を持たず、`_build_category_waveform` /
    # `_reference_for_category` の対象外（呼び出し側で kind 分岐する）。
    # (input_kind, route) は S_direct と同一 — crepe_direct 経路自体は入力の
    # 音楽的内容（合成旋律 vs 実声）を知らないため転用できる（設計 Memo M2c）。
    "V_direct": {
        "kind": "external",
        "input_kind": "clear_lead",
        "route_name": "crepe_direct",
        "bars_file": "m2_accuracy_bars.yaml",
    },
    # M2e（設計 §6.1）: V-remix 実ベッド帯の 2 アーム。`kind: "external"` なので
    # `fixture_id` / `composite_id` は持たない（`V_direct` と同じ）。入力は実声
    # （vocadito）と実伴奏（MUSDB18-HQ stem）をオフラインの
    # `scripts/make_vremix_fixtures.py` が混ぜた**実ファイル**であり、正解はミックスで
    # 変化しない vocadito の注釈——`kind: "external"` の定義そのもの。ハーネスに
    # 新しい合成経路（`kind: "fullstack"`）を足さず、音声波形はリポジトリに入らない。
    "V_remix_real_direct": {
        "kind": "external",
        # §6.3 で新設した加算的キー。フルミックスを `clear_lead` と宣言すると素材の
        # 宣言が虚偽になるため、`full_mix_direct_probe` でしか表現できない。
        "input_kind": "full_mix_direct_probe",
        "route_name": "crepe_direct",
        "bars_file": "m2e_accuracy_bars.yaml",
    },
    "V_remix_real_stem": {
        "kind": "external",
        "input_kind": "full_mix",  # 既存のまま（route も既存メニューから選ぶ）
        "route_name": "demucs_vocals_then_crepe",
        "bars_file": "m2e_accuracy_bars.yaml",
    },
}


def _categories_owned_by(bars_file: str) -> "Tuple[str, ...]":
    """`bars_file` が所有するカテゴリ（設計 §5.2）。"""
    return tuple(
        sorted(
            category
            for category, spec in _CATEGORY_SPECS.items()
            if spec.get("bars_file") == bars_file
        )
    )


def _require_category_bars_ownership() -> None:
    """カテゴリ所有権の fail-closed 検証（設計 §5.2・import 時に 1 回）。

    - 所有ファイルが未指定のカテゴリがあれば拒否する。「どこにも属さない」カテゴリは
      検証を素通りする——**分離が開ける唯一の穴なのでここで塞ぐ**。
    - 未知の bars ファイル名を所有者に指名することも拒否する（typo で穴が開く）。

    「2 ファイルに同名カテゴリが現れたら拒否」は、`_CATEGORY_SPECS` が category →
    単一 `bars_file` の写像である以上ここでは構成上起こりえない。実ファイル側の同名
    block は `_require_well_formed_bars` が「そのファイルが所有しないカテゴリの block」
    として拒否する。
    """
    for category, spec in _CATEGORY_SPECS.items():
        bars_file = spec.get("bars_file")
        if not isinstance(bars_file, str) or not bars_file:
            raise RuntimeError(
                f"_CATEGORY_SPECS: category {category!r} が bars_file を宣言していない; "
                "どの bars ファイルにも所有されないカテゴリは検証を素通りする "
                "(fail-closed)"
            )
        if bars_file not in _BARS_FILES:
            raise RuntimeError(
                f"_CATEGORY_SPECS: category {category!r} の bars_file {bars_file!r} が "
                f"未登録; 既知は {sorted(_BARS_FILES)} (fail-closed)"
            )


_require_category_bars_ownership()


RouteRunner = Callable[[str, MelodyRoute], Tuple[MelodyObservation, Dict[str, Any]]]


# ---------------------------------------------------------------------------
# dup-key-safe loaders（`run_melody_observability.py` と同じ規律を独立に適用。
# #60/#46 の重複キー隠蔽を弾く一方向規律を本ハーネスにも及ぼす）
# ---------------------------------------------------------------------------


class _NoDupSafeLoader(yaml.SafeLoader):
    """重複 mapping キーを拒否する SafeLoader。"""


def _no_dup_construct_mapping(loader: "yaml.SafeLoader", node: Any, deep: bool = False) -> Dict[Any, Any]:
    mapping: Dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(
                f"duplicate YAML mapping key {key!r}; stale/手書きファイルが "
                "last-wins で block を隠す穴を弾く (fail-closed)"
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_NoDupSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_dup_construct_mapping
)


def _yaml_load_no_dup_keys(data: "bytes | str", *, what: str) -> Any:
    try:
        return yaml.load(data, Loader=_NoDupSafeLoader)  # noqa: S506
    except yaml.YAMLError as exc:
        raise ValueError(f"{what}: YAML parse error: {exc}") from exc


def _json_loads_no_dup_keys(data: "bytes | str", *, what: str) -> Any:
    def _reject_dupes(pairs: "List[tuple]") -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(
                    f"{what}: duplicate JSON object key {key!r}; stale/手書き artifact が "
                    "失敗レコードを last-wins で隠す穴を弾く (fail-closed)"
                )
            result[key] = value
        return result

    def _reject_non_finite(token: str) -> Any:
        # Python の json は既定で NaN / Infinity / -Infinity を受理するが、NaN は
        # あらゆる比較が False になるため、閾値判定を素通りして pass を生む
        # （`NaN < min_rpa` も `NaN > max_vfa` も False）。artifact 段階で弾く。
        raise ValueError(
            f"{what}: JSON に非有限リテラル {token!r} が含まれる; 未定義の測定値を "
            "凍結バー判定へ通さない (fail-closed)"
        )

    return json.loads(data, object_pairs_hook=_reject_dupes, parse_constant=_reject_non_finite)


def _atomic_write_text(path: Path, text: str) -> None:
    """`text` を UTF-8 bytes として `path` へ atomic に書く（temp file → os.replace）。

    テキストモードで開くとプラットフォーム依存の改行変換が入り、同じ文字列を書いても
    publish される bytes が環境で変わる（後段の provenance hash が別値になる）。
    ここで一度だけ encode し、binary モードで書くことで「ハーネスが選んだ bytes 列」と
    「実際に publish された bytes 列」を一致させる（Codex P1 指摘）。
    """
    import os

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = text.encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _is_sha256(value: Any) -> bool:
    """`value` が真の sha256 digest（ちょうど 64 桁 lowercase hex）文字列なら True。

    `fullmatch` で全体一致を要求する（`re.match` + `$` は末尾改行を許し 65 文字 pin が
    通る）。`run_melody_observability._is_sha256` と同じ規約だが、あちらを import すると
    M1 ハーネスのコードが本ハーネスの provenance 閉包に入り、M1 側の変更で M2 の
    report が stale 化してしまうため独立に持つ。
    """
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))


# M2c PR-M2c-1 review（Codex 第 1 巡 P1）: clip id の安全な文字集合。英数字・`.`・
# `_`・`-` のみを許し、パス区切り（`/`・`\`）・`..`・空文字列を排除する。id はそのまま
# `tmp_dir / f"{clip_id}{suffix}"` としてファイル名の一部になる（`_build_external_
# clip_row`）ため、字句レベルでこの集合に制限しておかないと、manifest/fixtures の
# どちらか一方だけが悪意ある id（例: `"../../etc/passwd"`）を持ち込んだ場合に
# tmp_dir 外への書き込みへ繋がりうる。`resolve_confined` による物理的な確認
# （`_build_external_clip_row` 参照）は字句検証を迂回しうる環境依存の抜け穴
# （シンボリックリンク等）への防御であって、字句検証の代わりにはしない——両方を
# 独立に課す（pathsafe の「lexical + physical」二段防御と同じ設計）。
_SAFE_EXTERNAL_ID_RE = re.compile(r"[A-Za-z0-9._-]+")


def _require_safe_external_id(value: Any, *, where: str) -> str:
    """外部素材の id（fixtures の clip id / manifest entry の id）を字句検証する。"""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where}: id {value!r} が非空文字列でない (fail-closed)")
    if value in (".", "..") or not _SAFE_EXTERNAL_ID_RE.fullmatch(value):
        raise ValueError(
            f"{where}: id {value!r} が安全な文字集合（英数字・`.`・`_`・`-` のみ）に "
            "一致しない、またはパス区切り/`..`/絶対パスを含む (fail-closed)"
        )
    return value


def _require_exact_cohort_match(
    fixture_ids: "set[str]", manifest_ids: "set[str]", *, where: str
) -> None:
    """M2c PR-M2c-1 review（Codex 第 1 巡 P2）: 登録 fixtures と manifest の id 集合の
    **完全一致**を要求する（部分集合を fail-closed 拒否）。

    バーは「登録済み cohort 全体」に対して事前登録された閾値なので、manifest が
    登録済み clip の一部だけ（都合の良い clip だけ）を持ち込んで測ることも、逆に
    未登録 clip を紛れ込ませることも許さない——`_build_external_clip_row` の
    per-clip 未登録チェック（manifest ⊆ fixtures）だけでは fixtures ⊆ manifest 側が
    抜けるため、両側を独立に要求する。run 側（`_run_external_category`）と evaluate
    側（`_require_registered_row_identity_external`）の両方から呼ぶ——
    fresh-process 再検証は run 側のこの関数をそのまま再実行するため、同じ条件を
    自動的に継承する（再検証専用のコードパスは存在しない）。
    """
    if fixture_ids == manifest_ids:
        return
    missing_in_manifest = sorted(fixture_ids - manifest_ids)
    unexpected_in_manifest = sorted(manifest_ids - fixture_ids)
    raise ValueError(
        f"{where}: id 集合が m2c_external_fixtures.yaml の登録 cohort と完全一致しない "
        f"（部分 cohort は fail-closed 拒否）; missing_in_manifest={missing_in_manifest} "
        f"unexpected_in_manifest={unexpected_in_manifest}"
    )


_SCORER_ABSENT_OPTIONAL_PIN_MARKER: "Tuple[str, str, str]" = (
    "__absent__",
    "__absent__",
    "__absent__",
)


def _validated_scorer_pin_tuple(
    mapping: Dict[str, Any], *, context: str
) -> "Tuple[Tuple[str, str, str], ...]":
    """report/environment の mapping からスコアラー閉包全体の pin をタプル化する。

    `_SCORER_RUNTIME_PACKAGES`（mir_eval + scipy + numpy + decorator + 任意
    threadpoolctl/charset_normalizer、Codex P1・11 巡目 P1-B で必須/任意分割）を
    順に回る。旧実装は `mir_eval_version`/`mir_eval_code_sha256` の 2 キーしか
    見なかったため、異なる（あるいは patch された）scipy/numpy で測った report が
    同一スコアラー stack として混ざり受理されてしまっていた（Codex P1）。
    `{name}_dist_native_sha256`（Codex P1 2 巡目）は numpy/scipy の wheel が本体
    ディレクトリの兄弟（`{name}.libs/`）に置く OpenBLAS 等のネイティブ実体を覆う
    ——`{name}_code_sha256` は本体ディレクトリ配下しか rglob しないため、この兄弟
    ディレクトリの差し替えは version/code pin のどちらにも現れない。
    `_require_homogeneous_scorer` / `_require_fresh_process_report_provenance` /
    verdict 転記の 3 箇所がこのヘルパーで一般化された同じ検証・同じ順序を共有する。

    **必須/任意の二値（Codex 11 巡目 P1-B）**: `_SCORER_RUNTIME_PACKAGES_REQUIRED`
    （mir_eval/scipy/numpy/decorator——いずれも実際に import・実行される数値実装
    または宣言依存）は従来どおり `{name}_version` の非空 str・
    `{name}_code_sha256`/`{name}_dist_native_sha256` の真の sha256 を無条件に
    要求する（弱めていない）。`_SCORER_RUNTIME_PACKAGES_OPTIONAL`（threadpoolctl/
    charset_normalizer——いずれも宣言依存でない try/except ImportError 経由の
    任意 import、実測確認済み）は `{name}_closure_state` を見る二値分岐にする:
    `"absent"`（未導入。version/code/dist_native は揃って `None` でなければ
    「導入されていないと自称するのに部分的な pin がある」矛盾として fail-closed）
    なら `_SCORER_ABSENT_OPTIONAL_PIN_MARKER` という固定マーカーをタプルへ積む
    （version 文字列にはなり得ない値なので、他のどんな真の pin とも衝突しない）。
    `"present"`（導入済み）なら必須メンバーと全く同じ厳格な完全 pin を要求する。
    `{name}_closure_state` 自体が欠けている（この規律より前の report・手組み
    report）場合も fail-closed にする——absent/present のどちらを自称しているか
    分からない row を「揃っている」と主張しない。

    マーカーをタプルに含めることで、repeats 間・評価環境間で**任意メンバーの
    有無自体が食い違えば**（例: 一方は threadpoolctl 導入済み・もう一方は未導入）
    通常の pin 不一致と同じ経路で fail-closed になる——「別の閉包で測った run を
    混ぜない」という不変条件は必須/任意を問わず維持される。

    戻り値は `_SCORER_RUNTIME_PACKAGES` の順で `(version, code_sha256, dist_native_sha256)`
    （または任意メンバーの absent 時は固定マーカー）を並べたタプル（等価比較・
    集合化に使える）。`context` はエラーメッセージに出す呼び出し元識別子
    （例: `"reports[3]"` や `"測り直し report"`）。
    """
    pins: List[Tuple[str, str, str]] = []
    for name in _SCORER_RUNTIME_PACKAGES:
        is_optional = name in _SCORER_RUNTIME_PACKAGES_OPTIONAL
        if is_optional:
            closure_state = mapping.get(f"{name}_closure_state")
            if closure_state not in ("present", "absent"):
                raise ValueError(
                    f"evaluate_m2_bars: {context} が {name}_closure_state を欠くか"
                    f"不正な値（{closure_state!r}）; 任意メンバー {name!r} の有無を"
                    "自称しない row を受理しない (fail-closed)"
                )
            if closure_state == "absent":
                version = mapping.get(f"{name}_version")
                code = mapping.get(f"{name}_code_sha256")
                dist_native = mapping.get(f"{name}_dist_native_sha256")
                if version is not None or code is not None or dist_native is not None:
                    raise ValueError(
                        f"evaluate_m2_bars: {context} は {name!r} を absent と自称"
                        f"しつつ version={version!r}/code={code!r}/"
                        f"dist_native={dist_native!r} を持つ; 未導入と部分 pin が"
                        "矛盾する row を受理しない (fail-closed)"
                    )
                pins.append(_SCORER_ABSENT_OPTIONAL_PIN_MARKER)
                continue
        version = mapping.get(f"{name}_version")
        if not version or not isinstance(version, str):
            raise ValueError(
                f"evaluate_m2_bars: {context} が {name}_version を欠く; "
                "どの数値実装で測ったか不明な row にバーを適用しない (fail-closed)"
            )
        code = mapping.get(f"{name}_code_sha256")
        if not _is_sha256(code):
            raise ValueError(
                f"evaluate_m2_bars: {context} の {name}_code_sha256 {code!r} が "
                f"真の sha256 でない; {name} 実装を pin できない row を受理しない "
                "(fail-closed)"
            )
        dist_native = mapping.get(f"{name}_dist_native_sha256")
        if not _is_sha256(dist_native):
            raise ValueError(
                f"evaluate_m2_bars: {context} の {name}_dist_native_sha256 {dist_native!r} "
                f"が真の sha256 でない; {name} の wheel 同梱ネイティブ実体を pin できない "
                "row を受理しない (fail-closed)"
            )
        pins.append((version, code, dist_native))
    return tuple(pins)


def _utc_now() -> str:
    """現在時刻（UTC・ISO 8601・秒精度）。run_melody_observability.py と同じ定義。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_recorded_utc(value: Any, *, where: str, field: str = "recorded_utc") -> datetime:
    """report の観測時刻フィールドを UTC timestamp として検証してパースする（fail-closed）。

    `recorded_utc`（完了時刻）と `started_utc`（開始時刻）が同じ契約
    （UTC・ISO 8601・未来でない）を共有する。
    """
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"evaluate_m2_bars: {where} に {field} が無い（または文字列でない）; "
            "dated record を名乗る report は観測時刻を必須とする (fail-closed)"
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"evaluate_m2_bars: {where} の {field} {value!r} は ISO 8601 として "
            f"解釈できない (fail-closed): {exc}"
        ) from exc
    offset = parsed.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError(
            f"evaluate_m2_bars: {where} の {field} {value!r} は UTC でない"
            "（tz 無しまたは offset≠0）(fail-closed)"
        )
    if parsed > datetime.now(timezone.utc):
        raise ValueError(
            f"evaluate_m2_bars: {where} の {field} {value!r} は未来の時刻; "
            "観測していない時点を dated record として主張させない (fail-closed)"
        )
    return parsed


def _require_reported_number(value: Any, *, where: str, field: str) -> float:
    """report 由来の数値フィールドを **型強制せずに** 検証して float で返す。

    `float("50")` は通ってしまうため、`float(...)` で比較する実装は文字列などの
    型崩れした値を黙って正規化し、malformed な report が pass の verdict へ
    そのまま転記される（Codex P2）。`MelodyAccuracyResult.to_dict()` は数値を保証
    するので、数値でないこと自体が「builder が出していない row」の証拠になる。
    bool は int のサブクラスなので明示的に除外する。
    """
    import math

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"evaluate_m2_bars: {where} の {field} {value!r} が数値でない; 型強制で "
            "正規化せず、builder が出さない値を拒否する (fail-closed)"
        )
    if not math.isfinite(float(value)):
        raise ValueError(
            f"evaluate_m2_bars: {where} の {field} が非有限（{value!r}）(fail-closed)"
        )
    return float(value)


def _parse_registered_utc(value: Any, *, where: str) -> datetime:
    """凍結アーティファクトの **登録日** を検証してパースする（fail-closed）。

    「実測前に凍結した」というバーの主張は、この登録記録の上に全部乗っている。
    `registered_utc` が欠落・不正形式・未来なら、`--bars` で渡した artifact が
    「事前登録済み」を名乗って証拠チェーンに入れてしまう（Codex P2）。

    受理する形は `YYYY-MM-DD`（UTC 深夜と解釈）または UTC の ISO 8601 timestamp。
    `_parse_recorded_utc`（report の観測時刻用）とは別関数にしてある——あちらは
    秒精度の timestamp と tz 明示を要求するが、登録日は日付粒度で運用しているため。
    """
    from datetime import date as _date

    if not isinstance(value, str) or not value:
        raise ValueError(
            f"{where}: registered_utc が無い（または文字列でない）; 事前登録の記録が "
            "無い artifact を凍結バーとして受理しない (fail-closed)"
        )
    parsed: Optional[datetime] = None
    try:
        # 日付のみ（運用上の既定形）。`fromisoformat` は `YYYY-MM-DD` を厳密に読む。
        parsed = datetime.combine(_date.fromisoformat(value), datetime.min.time(), timezone.utc)
    except ValueError:
        try:
            candidate = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(
                f"{where}: registered_utc {value!r} は日付 (YYYY-MM-DD) でも ISO 8601 "
                f"timestamp でもない (fail-closed): {exc}"
            ) from exc
        offset = candidate.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError(
                f"{where}: registered_utc {value!r} は UTC でない（tz 無しまたは "
                "offset≠0）(fail-closed)"
            )
        parsed = candidate
    if parsed > datetime.now(timezone.utc):
        raise ValueError(
            f"{where}: registered_utc {value!r} が未来; まだ到来していない時点の "
            "「事前登録」を主張させない (fail-closed)"
        )
    return parsed


def _require_dated_registration(bars: Dict[str, Any]) -> None:
    """バー artifact の登録日と、追加登録（amendments）の日付を検証する（fail-closed）。"""
    registered = _parse_registered_utc(
        bars.get("registered_utc"), where="m2_accuracy_bars.yaml"
    )
    amendments = bars.get("amendments")
    if amendments is None:
        return
    if not isinstance(amendments, list) or not amendments:
        raise ValueError(
            f"m2_accuracy_bars.yaml: amendments {amendments!r} が非空リストでない "
            "(fail-closed)"
        )
    for idx, amendment in enumerate(amendments):
        where = f"m2_accuracy_bars.yaml amendments[{idx}]"
        if not isinstance(amendment, dict):
            raise ValueError(f"{where}: mapping でない（{amendment!r}）(fail-closed)")
        amended = _parse_registered_utc(amendment.get("registered_utc"), where=where)
        if amended < registered:
            # 追加登録が元の凍結より前、という記録は成立しない（履歴の捏造を弾く）。
            raise ValueError(
                f"{where}: registered_utc {amendment.get('registered_utc')!r} が "
                f"artifact の registered_utc {bars.get('registered_utc')!r} より前 "
                "(fail-closed)"
            )
        added = amendment.get("added")
        if not isinstance(added, list) or not added:
            raise ValueError(
                f"{where}: added {added!r} が非空リストでない; 何を追加登録したか "
                "辿れない amendment を受理しない (fail-closed)"
            )


def _repo_relative_path(path: "str | Path") -> "str | None":
    """`path` を repo root 相対の論理パスで返す（repo 配下に無ければ None）。

    チェックアウトの絶対パスは originating host でしか解決できないため、
    監査側が別 checkout から辿れるよう checkout 非依存の論理パスを併記する
    （絶対パス自体は別途 machine-local な値として記録する。設計 §7 M2a 行）。
    """
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# specs / bars ロード（single read → hash → parse。TOCTOU 回避）
# ---------------------------------------------------------------------------


def load_specs(path: Path = SPECS_PATH) -> Tuple[Dict[str, Any], str]:
    """m2_accuracy_specs.yaml を single read で (parsed dict, sha256) として返す。"""
    specs, sha256, _raw = load_specs_with_raw(path)
    return specs, sha256


def load_specs_with_raw(path: Path = SPECS_PATH) -> Tuple[Dict[str, Any], str, bytes]:
    """`load_specs` + 検証済み raw bytes（測り直し子プロセスへの凍結転写用）。

    evaluate は raw を保持し、測り直し子には**この bytes の複製**を渡す——`--specs`
    の実パスを渡すと、評価器が読んだ後にファイルが差し替えられた場合に子が別 fixture
    を測ってしまう（Codex P2 第 34 巡）。
    """
    data = Path(path).read_bytes()
    specs = _yaml_load_no_dup_keys(data, what="m2_accuracy_specs.yaml")
    # スキーマ discriminator を **fixture 定義を処理する前に** 検査する（bars/report と
    # 同じ規律）。`sample_rate` / `amplitude` / `fixtures` が残ったまま合成の意味論が
    # 変わった artifact を現行セマンティクスで解釈しない（Codex P2）。
    version = specs.get("schema_version")
    if version != _EXPECTED_SPECS_SCHEMA:
        raise ValueError(
            f"unsupported m2_accuracy_specs schema_version {version!r}; "
            f"expected {_EXPECTED_SPECS_SCHEMA} (fail-closed)"
        )
    for required in ("sample_rate", "amplitude", "fixtures"):
        if required not in specs:
            raise ValueError(f"m2_accuracy_specs.yaml is missing required key {required!r}")
    return specs, hashlib.sha256(data).hexdigest(), data


class BarsArtifact:
    """凍結バーの **digest と parsed data を束ねた** 不透明アーティファクト。

    `(parsed_dict, sha256)` を別々に返すと、呼び出し側が dict を書き換えたまま元の
    digest を渡せる——例えば `S_direct.min_rpa` を下げて評価すれば、凍結バーで fail
    する report が pass になり、verdict は「無変更の凍結アーティファクトの hash」を
    名乗る（Codex P2）。そこで raw bytes を保持し、`verify()` が

    1. raw の再 hash が記録 digest と一致すること
    2. raw を**再 parse** した結果が保持している parsed data と一致すること
       （= load 後に mapping が変異していないこと）

    を確認したうえで parsed data を返す。`evaluate_m2_bars` はこの検証を通した
    data しか見ない。

    既存の `bars, bars_sha256 = load_bars(...)` / `bars["m2_accuracy_bars"]` という
    呼び出し形を保つため、Mapping 相当の read アクセスは data へ委譲する。
    """

    __slots__ = ("_data", "_sha256", "_raw")

    def __init__(self, data: Dict[str, Any], sha256: str, raw: bytes) -> None:
        self._data = data
        self._sha256 = sha256
        self._raw = raw

    @property
    def data(self) -> Dict[str, Any]:
        return self._data

    @property
    def sha256(self) -> str:
        return self._sha256

    @property
    def raw(self) -> bytes:
        return self._raw

    # --- read-only Mapping 委譲（呼び出し側の `bars[...]` / `bars.get(...)` 用）---
    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def verify(self, expected_sha256: Optional[str] = None) -> Dict[str, Any]:
        """digest と parsed data の束縛を検証して parsed data を返す（fail-closed）。"""
        actual = hashlib.sha256(self._raw).hexdigest()
        if actual != self._sha256:
            raise ValueError(
                f"BarsArtifact: 記録 digest {self._sha256!r} が raw bytes の hash "
                f"{actual!r} と不一致 (fail-closed)"
            )
        if expected_sha256 is not None and expected_sha256 != self._sha256:
            raise ValueError(
                f"BarsArtifact: 渡された bars_sha256 {expected_sha256!r} が "
                f"アーティファクトの digest {self._sha256!r} と不一致; 別世代の bars の "
                "hash を verdict に名乗らせない (fail-closed)"
            )
        reparsed = _yaml_load_no_dup_keys(self._raw, what="m2_accuracy_bars.yaml")
        if reparsed != self._data:
            raise ValueError(
                "BarsArtifact: parsed バーが load 後に変異している（raw bytes の再 parse と "
                "不一致）; 閾値を書き換えたまま元の凍結 digest を名乗る verdict を publish "
                "しない (fail-closed)"
            )
        return self._data


class ReportArtifact:
    """run report の **raw bytes / digest / parsed data を束ねた** アーティファクト。

    `report_pins` を呼び出し側から受け取ると、「元ファイルの hash を記録しつつ、
    メモリ上では別内容（バーを満たす metrics に書き換えた mapping）を評価する」ことが
    できてしまう——verdict は無変更のファイルを pin しながら、別のものを判定した
    ことになる（Codex P2）。そこで `BarsArtifact` と同じ single-read 束縛を report にも
    適用し、pin は **評価した bytes から evaluate 側が導出**する。
    """

    __slots__ = ("_data", "_sha256", "_raw", "_path")

    def __init__(
        self, data: Dict[str, Any], sha256: str, raw: bytes, path: "str | Path | None" = None
    ) -> None:
        self._data = data
        self._sha256 = sha256
        self._raw = raw
        self._path = path

    @classmethod
    def from_bytes(cls, raw: bytes, *, path: "str | Path | None" = None) -> "ReportArtifact":
        """read 済みの bytes から parse して束ねる（read と hash/parse を分離しない）。"""
        data = _json_loads_no_dup_keys(raw, what=str(path) if path is not None else "report")
        return cls(data, hashlib.sha256(raw).hexdigest(), raw, path)

    @property
    def data(self) -> Dict[str, Any]:
        return self._data

    @property
    def sha256(self) -> str:
        return self._sha256

    @property
    def path(self) -> "str | Path | None":
        return self._path

    def verify(self) -> Dict[str, Any]:
        """digest と parsed data の束縛を検証して parsed data を返す（fail-closed）。"""
        actual = hashlib.sha256(self._raw).hexdigest()
        if actual != self._sha256:
            raise ValueError(
                f"ReportArtifact: 記録 digest {self._sha256!r} が raw bytes の hash "
                f"{actual!r} と不一致 (fail-closed)"
            )
        reparsed = _json_loads_no_dup_keys(
            self._raw, what=str(self._path) if self._path is not None else "report"
        )
        if reparsed != self._data:
            raise ValueError(
                "ReportArtifact: parsed report が load 後に変異している（raw bytes の "
                "再 parse と不一致）; 元ファイルの hash を pin しながら別内容を判定した "
                "verdict を publish しない (fail-closed)"
            )
        return self._data

    def pin(self) -> Dict[str, Any]:
        """verdict へ記録する pin（**評価した bytes** の digest と論理パス）。"""
        return {
            "sha256": self._sha256,
            "path_relative": _repo_relative_path(self._path) if self._path is not None else None,
            "path_name": Path(self._path).name if self._path is not None else None,
        }


def load_report(path: "str | Path") -> ReportArtifact:
    """run report を single read で `ReportArtifact` として読む（read → hash → parse）。"""
    raw = Path(path).read_bytes()
    return ReportArtifact.from_bytes(raw, path=path)


def bars_file_identity(doc: Dict[str, Any]) -> str:
    """bars ドキュメントの `schema_version` から**所有ファイル同一性**を決める。

    パス名ではなく schema で決める理由（設計 §5.2 の実装上の要請）: 測り直し子
    プロセスやテストは bars bytes を tmp へ凍結複製して別名で渡すため、ファイル名に
    依存した判定はその経路で崩れる。schema_version は artifact 自身が名乗る
    discriminator なので複製しても付いて回る。
    """
    version = doc.get("schema_version")
    bars_file = _BARS_FILE_BY_SCHEMA.get(version)
    if bars_file is None:
        raise ValueError(
            f"unsupported m2_accuracy_bars schema_version {version!r}; "
            f"expected one of {sorted(_BARS_FILE_BY_SCHEMA)} (fail-closed)"
        )
    return bars_file


def _require_measurement_conditions(doc: Dict[str, Any], *, bars_file: str) -> None:
    """条件 block（`gate_level` / `levels` / `level_margin_db`）を検証する（設計 §5.3）。

    条件 block は **バー block の兄弟**でなければならない——バー block の中へ入れると
    loader が dict 値をカテゴリと誤認する（そして `_BAR_THRESHOLD_RANGES` が非数値の
    閾値キーとして拒否する）。バーは judge が数値比較する閾値、`gate_level` / `levels`
    は「何を測ったか」の宣言であり、別の種類の対象である（附録A-4 の是正）。

    fail-closed:

    - **バーを持つカテゴリが条件 block を欠いたら拒否**
    - `gate_level ∈ levels` でなければ拒否
    - `levels` が凍結ラダーと完全一致（**順序込み**）でなければ拒否
    - `level_margin_db` が §3.4.1 の 20 dB 不変量と一致しなければ拒否
    - そのファイルが所有しないカテゴリの条件 block があれば拒否
    """
    import math

    conditions_key = _BARS_FILES[bars_file]["conditions_key"]
    owned = _categories_owned_by(bars_file)
    if conditions_key is None:
        # 条件 block を持たないファイル（M2）で、所有カテゴリが条件を要求しないこと。
        needs = [c for c in owned if c in _REQUIRED_CONDITION_KEYS_BY_CATEGORY]
        if needs:
            raise RuntimeError(
                f"{bars_file}: category {needs} が測定条件を要求するのに、このファイルは "
                "条件 block を持たない (fail-closed)"
            )
        return

    conditions = doc.get(conditions_key)
    if not isinstance(conditions, dict) or not conditions:
        raise ValueError(
            f"{bars_file}: 条件 block {conditions_key!r} が無い/空; 水準宣言なしに帯を "
            "登録しない (fail-closed)"
        )

    margin = conditions.get("level_margin_db")
    if isinstance(margin, bool) or not isinstance(margin, (int, float)):
        raise ValueError(
            f"{bars_file}: {conditions_key}.level_margin_db {margin!r} が数値でない "
            "(fail-closed)"
        )
    if not math.isfinite(float(margin)) or float(margin) != _M2E_LEVEL_MARGIN_DB:
        raise ValueError(
            f"{bars_file}: {conditions_key}.level_margin_db {margin!r} が凍結された "
            f"20 dB 不変量 {_M2E_LEVEL_MARGIN_DB} と不一致; 余裕は実験全体で 1 つの宣言で "
            "あり、素材に合わせて動かさない (fail-closed)"
        )

    declared = {k: v for k, v in conditions.items() if isinstance(v, dict)}
    unowned = sorted(set(declared) - set(owned))
    if unowned:
        raise ValueError(
            f"{bars_file}: 条件 block に、このファイルが所有しないカテゴリ {unowned} が "
            "ある (fail-closed)"
        )

    for category in owned:
        required = _REQUIRED_CONDITION_KEYS_BY_CATEGORY.get(category)
        if required is None:
            continue
        entry = declared.get(category)
        if not entry:
            raise ValueError(
                f"{bars_file}: category {category!r} がバーを持つのに条件 block を欠く; "
                "何を測ったかの宣言なしに合否を出さない (fail-closed)"
            )
        missing = [key for key in required if key not in entry]
        if missing:
            raise ValueError(
                f"{bars_file}: category {category!r} の条件 block が必須キー {missing} を "
                "欠く (fail-closed)"
            )
        levels = entry["levels"]
        if not isinstance(levels, list) or tuple(levels) != _M2E_LEVEL_LADDER:
            raise ValueError(
                f"{bars_file}: category {category!r} の levels {levels!r} が凍結ラダー "
                f"{list(_M2E_LEVEL_LADDER)} と（順序込みで）一致しない; 水準を足す/減らす/"
                "並べ替えることは破断曲線の定義を変える (fail-closed)"
            )
        gate_level = entry["gate_level"]
        if gate_level not in levels:
            raise ValueError(
                f"{bars_file}: category {category!r} の gate_level {gate_level!r} が "
                f"levels {levels!r} に含まれない (fail-closed)"
            )


def _require_m2e_bars_provenance(doc: Dict[str, Any], *, bars_file: str) -> None:
    """分離した bars ファイルが要求する provenance を検証する（設計 §5.1-1/-3）。

    - **一方向規律の明示的継承**（`one_way_rule` を非空文字列で持つこと）。暗黙の
      継承にしない。
    - **転用値の出所の記録**（`provenance.derived_from`）。ファイルを跨いだ時点で
      出所が追えなくなるため、転用元のファイル・その時点の sha256・カテゴリを残す。
    """
    if _BARS_FILES[bars_file]["declares_shared_scalars"]:
        return  # M2 本体は既存の provenance 契約（specs_sha256 等）のまま
    block = doc[_BARS_FILES[bars_file]["block_key"]]
    one_way = block.get("one_way_rule")
    if not isinstance(one_way, str) or not one_way.strip():
        raise ValueError(
            f"{bars_file}: バー block が one_way_rule を非空文字列で持たない; 分離した "
            "ファイルへの一方向規律の継承を暗黙にしない (fail-closed)"
        )
    derived = doc.get("provenance", {}).get("derived_from")
    if not isinstance(derived, dict):
        raise ValueError(
            f"{bars_file}: provenance.derived_from が無い; 転用値の出所はファイルを跨いだ "
            "時点で追えなくなる (fail-closed)"
        )
    for key in ("file", "sha256", "category"):
        value = derived.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"{bars_file}: provenance.derived_from.{key} が非空文字列でない "
                "(fail-closed)"
            )
    if not _is_sha256(derived["sha256"]):
        raise ValueError(
            f"{bars_file}: provenance.derived_from.sha256 {derived['sha256']!r} が "
            "64 桁 lowercase hex でない (fail-closed)"
        )
    # 形だけ整った provenance は装飾にすぎない。**宣言した出所の実体へ結び付ける**:
    # 宣言ファイルが既知の bars ファイルであり、その committed bytes の digest が
    # 宣言値と一致し、宣言カテゴリが実際にそのファイルに在ることまで要求する。
    # （一方向規律により bars ファイルは凍結後に変わらないので、この一致は安定する。
    #   もし出所が変わったなら、それは「転用値がまだ出所を反映しているか」を
    #   問い直すべき時であり、黙って通してよい変化ではない。）
    source_name = derived["file"]
    if source_name not in _BARS_FILES:
        raise ValueError(
            f"{bars_file}: provenance.derived_from.file {source_name!r} が既知の bars "
            f"ファイル {sorted(_BARS_FILES)} でない (fail-closed)"
        )
    source_path = Path(_BARS_FILES[source_name]["default_path"])
    if not source_path.is_file():
        raise ValueError(
            f"{bars_file}: provenance.derived_from.file {source_name!r} の実体 "
            f"{source_path} が無い (fail-closed)"
        )
    source_bytes = source_path.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    if source_sha256 != derived["sha256"]:
        raise ValueError(
            f"{bars_file}: provenance.derived_from.sha256 {derived['sha256']} が "
            f"{source_name} の実 digest {source_sha256} と不一致; 転用元の bytes へ "
            "結び付いていない provenance を受理しない (fail-closed)"
        )
    source_doc = _yaml_load_no_dup_keys(source_bytes, what=source_name)
    source_block = source_doc.get(_BARS_FILES[source_name]["block_key"])
    if not isinstance(source_block, dict) or derived["category"] not in source_block:
        raise ValueError(
            f"{bars_file}: provenance.derived_from.category {derived['category']!r} が "
            f"{source_name} に存在しない (fail-closed)"
        )


def load_bars(path: Path = BARS_PATH) -> Tuple[BarsArtifact, str]:
    """bars YAML を single read で (BarsArtifact, sha256) として返す。

    read → hash → parse を 1 操作にまとめ、その 3 つを `BarsArtifact` に束ねる
    （digest と parsed data が切り離されないようにする。Codex P2）。

    M2e（設計 §5.2）: 対象ファイルの同一性を `schema_version` から決め、**そのファイルが
    所有するカテゴリのみ**を検証する。既定は従来どおり `m2_accuracy_bars.yaml`。
    """
    data = Path(path).read_bytes()
    bars = _yaml_load_no_dup_keys(data, what="m2_accuracy_bars.yaml")
    bars_file = bars_file_identity(bars)
    block_key = _BARS_FILES[bars_file]["block_key"]
    if block_key not in bars:
        raise ValueError(f"{bars_file} is missing the {block_key!r} block")
    # 「実測前に凍結した」という主張の土台なので、閾値そのものより前に登録日を検証する。
    _require_dated_registration(bars)
    _require_well_formed_bars(bars[block_key], bars_file=bars_file)
    _require_measurement_conditions(bars, bars_file=bars_file)
    _require_m2e_bars_provenance(bars, bars_file=bars_file)
    sha256 = hashlib.sha256(data).hexdigest()
    return BarsArtifact(bars, sha256, data), sha256


def _require_valid_external_fixture_entry(clip_id: Any, entry: Any, *, where: str) -> None:
    """`m2c_external_fixtures.yaml` の 1 fixture entry を検証する（fail-closed）。"""
    _require_safe_external_id(clip_id, where=where)
    if not isinstance(entry, dict):
        raise ValueError(f"{where}: fixtures[{clip_id!r}] が mapping でない (fail-closed)")
    for key in ("expected_audio_sha256", "expected_annotation_sha256"):
        value = entry.get(key)
        if not _is_sha256(value):
            raise ValueError(
                f"{where}: fixtures[{clip_id!r}].{key} {value!r} が真の sha256（64 桁 "
                "lowercase hex）でない (fail-closed)"
            )


def load_external_fixtures_with_raw(
    path: Path = EXTERNAL_FIXTURES_PATH,
) -> Tuple[Dict[str, Any], str, bytes]:
    """`load_external_fixtures` + 検証済み raw bytes（測り直し子プロセスへの凍結転写用）。

    `load_specs_with_raw` と対称: evaluate は raw を保持し、測り直し子には**この
    bytes の複製**を渡す——`--external-fixtures` の実パスを渡すと、評価器が読んだ後に
    ファイルが差し替えられた場合に子が別の登録集合を測ってしまう（Codex P2 第 34 巡・
    `load_specs_with_raw` と同型の TOCTOU 回避）。
    """
    data = Path(path).read_bytes()
    fixtures_doc = _yaml_load_no_dup_keys(data, what="m2c_external_fixtures.yaml")
    version = fixtures_doc.get("schema_version")
    if version not in _EXPECTED_EXTERNAL_FIXTURES_SCHEMAS:
        raise ValueError(
            f"unsupported m2c_external_fixtures schema_version {version!r}; "
            f"expected one of {list(_EXPECTED_EXTERNAL_FIXTURES_SCHEMAS)} (fail-closed)"
        )
    _parse_registered_utc(fixtures_doc.get("registered_utc"), where="m2c_external_fixtures.yaml")
    fixtures = fixtures_doc.get("fixtures")
    if not isinstance(fixtures, dict):
        raise ValueError(
            "m2c_external_fixtures.yaml: 'fixtures' が mapping でない (fail-closed)"
        )
    for clip_id, entry in fixtures.items():
        _require_valid_external_fixture_entry(
            clip_id, entry, where="m2c_external_fixtures.yaml"
        )
    sha256 = hashlib.sha256(data).hexdigest()
    return fixtures_doc, sha256, data


def _require_external_fixtures_schema_for_category(
    fixtures_doc: Dict[str, Any], *, category: str, where: str
) -> None:
    """カテゴリと pin ファイルの schema を束縛する（fail-closed）。

    水準軸を持つカテゴリ（M2e）を **M2c の pin ファイル**で回せてはならない。
    `--external-fixtures` は既定値を持つため、M2e カテゴリと M2c manifest を
    組み合わせると **ベッドの入っていない 40 clip のきれいな歌声**が cohort 一致も
    hash 照合も通り、要求水準（例 +12 dB）のゲートとして row が刻まれる。
    schema が M2e でなければ、水準の一致を見る以前に測ってはいけない。
    """
    version = fixtures_doc.get("schema_version")
    if category in _REQUIRED_CONDITION_KEYS_BY_CATEGORY:
        if version != _EXPECTED_M2E_EXTERNAL_FIXTURES_SCHEMA:
            raise ValueError(
                f"{where}: 水準軸を持つカテゴリに schema_version {version!r} の pin "
                f"ファイルが渡された（要求: {_EXPECTED_M2E_EXTERNAL_FIXTURES_SCHEMA!r}）; "
                "ベッドの入っていない素材を帯の水準として測らない (fail-closed)"
            )
    elif version == _EXPECTED_M2E_EXTERNAL_FIXTURES_SCHEMA:
        raise ValueError(
            f"{where}: 水準軸を持たないカテゴリに M2e の pin ファイルが渡された; "
            "帯のミックスを水準の宣言なしに測らない (fail-closed)"
        )


# 1 水準あたりの凍結コホート（設計 §6.2: `40 clip × 2 bed = 80 entry`）。総セル数
# 1280 = 80 × 4 水準 × 2 アーム × n=2 の内訳そのものなので、ここが縮めば帯が別物になる。
_M2E_EXPECTED_BED_COUNT = 2
_M2E_EXPECTED_CLIPS_PER_BED = 40
_M2E_EXPECTED_ENTRIES_PER_LEVEL = _M2E_EXPECTED_BED_COUNT * _M2E_EXPECTED_CLIPS_PER_BED


def _m2e_bed_slug(track_name: str) -> str:
    """MUSDB トラック名 → entry id の `bed_id`（生成器 `bed_slug()` と**同一規約**）。

    生成器（`scripts/make_vremix_fixtures.py`）を測る側から import しない代わりに、
    置換規則をここに写す——両者の一致は `tests/test_m2_accuracy_harness.py` が
    実登録簿の全トラック名で機械検証する（`_serialize_wav_float32` と同じ流儀:
    規約が 2 つに割れるより、同じものを 2 箇所で作って一致を強制する）。
    """
    return re.sub(r"[^A-Za-z0-9]+", "-", track_name).strip("-")


def _registered_m2e_bed_ids() -> "set[str]":
    """`m2e_bed_fixtures.yaml` の `accepted: true` ベッドの `bed_id` 集合。"""
    doc = _yaml_load_no_dup_keys(
        Path(M2E_BED_FIXTURES_PATH).read_bytes(), what=M2E_BED_FIXTURES_PATH.name
    )
    beds = doc.get("beds") if isinstance(doc, dict) else None
    if not isinstance(beds, dict) or not beds:
        raise ValueError(f"{M2E_BED_FIXTURES_PATH}: beds が空 (fail-closed)")
    return {
        _m2e_bed_slug(str(track))
        for track, pin in beds.items()
        if isinstance(pin, dict) and pin.get("accepted") is True
    }


def _require_registered_m2e_cohort(fixtures_doc: Dict[str, Any], *, where: str) -> None:
    """M2e fixtures が**凍結コホートそのもの**であることを要求する（fail-closed）。

    fixtures と manifest を同じだけ切り詰めると両者は一致するので、cohort 完全一致の
    比較では縮んだ帯を検出できない——「同じ clip を両 bed から落とす」「bed を丸ごと
    落とす」は矩形性すら壊さない。**生成器側の保証は生成物と一緒に旅をしない**ので、
    測る側が絶対量を独立に要求する: 2 bed × 40 clip = 80 entry（§6.2）。

    件数だけでは足りない。**各 bed の clip 集合を登録簿の 40 ID 集合そのもの**へ
    束縛する——両 bed が 40 件ずつでも中身がずれていれば（片方が `vocadito_1..40`、
    もう片方が `vocadito_2..41`）直積の 1 セルが欠け、登録されていない clip が
    紛れ込んだ帯になる。manifest を同じようにずらせば下流の cohort 比較も通る。

    テストは合成 fixtures（数 entry）を多用するため、本 gate は
    `_require_attested_external_fixtures_registration` と同じ流儀で autouse fixture が
    無効化し、**専用テスト群が実 gate を固定する**。
    """
    fixtures = fixtures_doc.get("fixtures")
    if not isinstance(fixtures, dict) or not fixtures:
        raise ValueError(f"{where}: M2e fixtures が空 (fail-closed)")
    # 生成側 provenance（`make_vremix_fixtures.build` が刻む混合式・入力登録簿の
    # digest）を、**測る側が持っている登録簿**と突き合わせる。ミックスが committed
    # pin と hash 一致していても、それが「登録済み 40 clip から凍結式で作られた」
    # ことは WAV 自身からは分からない。
    builder = fixtures_doc.get("builder")
    if not isinstance(builder, dict) or not builder.get("generator_code_sha256"):
        raise ValueError(
            f"{where}: M2e fixtures に builder provenance（generator_code_sha256 / "
            "入力登録簿 digest）が無い; どの混合式・どの登録簿から出た音か立証できない "
            "pin ファイルで測らない (fail-closed)"
        )
    # **宣言された digest は全部照合する。** clip 側だけ見て bed 側を見逃すと、
    # 採用ベッドや窓 pin を書き換えた登録簿から作られた自己整合な 80 entry bundle が、
    # コホート検査も音声 hash 照合も通ってしまう（provenance を名乗るだけで検証されない）。
    # **混合式（生成器のコード）も同じ**——非空を確かめるだけでは、改変した混合式で
    # 作った音が「凍結式の証拠」として測られる。3 本とも実体と突き合わせる。
    for key, path in (
        ("generator_code_sha256", M2E_MIXER_SCRIPT_PATH),
        ("m2c_fixtures_sha256", EXTERNAL_FIXTURES_PATH),
        ("m2e_bed_fixtures_sha256", M2E_BED_FIXTURES_PATH),
    ):
        declared = builder.get(key)
        actual = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        if declared != actual:
            raise ValueError(
                f"{where}: fixtures が名乗る {key} {declared!r} が、測る側の "
                f"{_repo_relative_path(path)} の実体 {actual!r} と不一致; "
                "別の登録簿から作られたミックスを測らない (fail-closed)"
            )
    registered_clips = set(load_external_fixtures(EXTERNAL_FIXTURES_PATH)[0]["fixtures"])
    registered_beds = _registered_m2e_bed_ids()
    by_bed: Dict[str, set] = {}
    for key in fixtures:
        parts = str(key).split("_")
        if len(parts) < 4 or parts[0] != "vremix":
            raise ValueError(
                f"{where}: entry id {key!r} が §6.2 の規約 "
                "`vremix_{clip_id}_{bed_id}_{level_tag}` に合わない (fail-closed)"
            )
        by_bed.setdefault(parts[-2], set()).add("_".join(parts[1:-2]))
    detail = {bed: len(clips) for bed, clips in sorted(by_bed.items())}
    if len(by_bed) != _M2E_EXPECTED_BED_COUNT or len(fixtures) != _M2E_EXPECTED_ENTRIES_PER_LEVEL:
        raise ValueError(
            f"{where}: M2e コホートが凍結値（{_M2E_EXPECTED_BED_COUNT} bed × "
            f"{_M2E_EXPECTED_CLIPS_PER_BED} clip = {_M2E_EXPECTED_ENTRIES_PER_LEVEL} entry）"
            f"と一致しない（bed 別 clip 数={detail} / 総数={len(fixtures)}）; 部分コホートを "
            "水準の証拠として測らない (fail-closed)"
        )
    # **bed も同定する。** 件数と clip 集合だけでは、任意の 2 本のベッドで作った帯が
    # 「凍結 2 ベッドのコホート」を名乗って通ってしまう（登録簿 digest は入力の同一性を
    # 示すだけで、その中の *どの* ベッドを使ったかは fixtures 側からしか分からない）。
    if set(by_bed) != registered_beds:
        raise ValueError(
            f"{where}: bed_id 集合 {sorted(by_bed)} が事前登録の accepted ベッド "
            f"{sorted(registered_beds)} と一致しない; 別のベッドで作った帯を凍結コホート "
            "として測らない (fail-closed)"
        )
    for bed, clips in sorted(by_bed.items()):
        if clips != registered_clips:
            missing = sorted(registered_clips - clips)
            extra = sorted(clips - registered_clips)
            raise ValueError(
                f"{where}: bed {bed!r} の clip 集合が事前登録の "
                f"{len(registered_clips)} clip と一致しない（欠落={missing[:5]} / "
                f"余分={extra[:5]}）; 登録されていない clip を含む帯を測らない (fail-closed)"
            )


def _require_external_fixtures_level_match(
    fixtures_doc: Dict[str, Any], *, level: Optional[str], where: str
) -> None:
    """M2e pin ファイルが名乗る水準と、run が要求した `--level` を束縛する（fail-closed）。

    `fixtures_*.yaml` は水準ごとに 1 本あり、top-level `level` に自分がどの水準の
    ミックスを pin しているかを書いている。run 側が `--level` をラダー所属だけで
    検証すると、`fixtures_m06.yaml` を `--level +12dB` で回した場合に **−6 dB の音を
    測って +12 dB のゲートとして row を刻む**ことが成立する。row は内部整合するので
    後段からは見えず、ゲート判定と破断曲線の両方が汚染される。

    束縛はここ 1 箇所で足りる: manifest の音声 bytes は `expected_audio_sha256` で
    fixtures の各 entry へ既に縛られているため、`level` を fixtures 側で確定すれば
    「どの水準の音を測っているか」は一意に決まる。
    """
    if fixtures_doc.get("schema_version") != _EXPECTED_M2E_EXTERNAL_FIXTURES_SCHEMA:
        return
    declared = fixtures_doc.get("level")
    if declared not in _M2E_LEVEL_LADDER:
        raise ValueError(
            f"{where}: {_EXPECTED_M2E_EXTERNAL_FIXTURES_SCHEMA} の 'level' {declared!r} が "
            f"凍結ラダー {list(_M2E_LEVEL_LADDER)} に無い; どの水準を pin したのか宣言の "
            "無い fixtures で測らない (fail-closed)"
        )
    if level is None:
        raise ValueError(
            f"{where}: fixtures が水準 {declared!r} を宣言しているのに run 側の level が "
            "無い (fail-closed)"
        )
    if declared != level:
        raise ValueError(
            f"{where}: fixtures が pin した水準 {declared!r} と run が要求した水準 "
            f"{level!r} が食い違う; 別水準の音を測って要求水準の row として刻むと、"
            "ゲート判定と破断曲線の両方が汚染される (fail-closed)"
        )
    # top-level `level` は 1 行の宣言でしかない。`fixtures_m06.yaml` を複製して
    # `level` だけ `+12dB` に書き換えると、entry id は全部 `_m06` のまま・pin された
    # 音も −6 dB のミックスのまま**宣言だけ**が gate 水準になる。id 規約（§6.2:
    # `vremix_{clip_id}_{bed_id}_{level_tag}`）は凍結されているので、宣言を id 側の
    # 実体へ束縛できる。
    fixtures = fixtures_doc.get("fixtures")
    if not isinstance(fixtures, dict) or not fixtures:
        raise ValueError(f"{where}: M2e fixtures が空 (fail-closed)")
    expected_suffix = f"_{_M2E_LEVEL_TAGS[declared]}"
    mislabeled = sorted(k for k in fixtures if not str(k).endswith(expected_suffix))
    if mislabeled:
        raise ValueError(
            f"{where}: 宣言水準 {declared!r} に対し id が {expected_suffix!r} で終わらない "
            f"entry がある（{mislabeled[:5]}{' …' if len(mislabeled) > 5 else ''}）; "
            "宣言だけ書き換えた pin ファイルで別水準の音を測らない (fail-closed)"
        )
    _require_registered_m2e_cohort(fixtures_doc, where=where)


def load_external_fixtures(path: Path = EXTERNAL_FIXTURES_PATH) -> Tuple[Dict[str, Any], str]:
    """`m2c_external_fixtures.yaml` を single read で (parsed dict, sha256) として返す。

    `load_bars` / `load_specs` と同じ read → hash → parse の単一操作規律。M2c-1 時点
    では `fixtures` が空 dict でも正当（実データは M2c-2 で追記登録する）——空自体は
    ここでは拒否せず、V_direct を要求する run/evaluate 側が「登録済み clip が無い」
    ことを fail-closed で検出する。
    """
    fixtures_doc, sha256, _raw = load_external_fixtures_with_raw(path)
    return fixtures_doc, sha256


# バー閾値の値域。`min_*` は下限、`max_*` は上限として judge 側で使う。
_BAR_THRESHOLD_RANGES: Dict[str, Tuple[float, float]] = {
    "min_rpa": (0.0, 1.0),
    "max_vfa": (0.0, 1.0),
    "max_octave_gap": (-1.0, 1.0),
}


def _require_well_formed_bars(
    bar_block: Dict[str, Any], *, bars_file: str = "m2_accuracy_bars.yaml"
) -> None:
    """凍結バー自身の型・有限性・定義域を検証する（fail-closed）。

    metrics 側の NaN は塞いだが、**バー側**にも同じ穴がある: `min_rpa: .nan` を
    書いた bars を `--bars` で渡すと `raw_pitch_accuracy < NaN` が常に False になり、
    「未定義のバー」の下で pass が publish できてしまう（Codex P1）。閾値は判定の
    基準そのものなので、読み込み時点で弾く。

    M2e（設計 §5.2）: 検証は **`bars_file` が所有するカテゴリのみ**を対象とする。
    他ファイルのカテゴリの不在を欠落と見なさない（見なすと分離した瞬間に既存
    ファイルの検証が「新帯のバーが空だ」と誤爆する）。共有スカラーを宣言できるのも
    所有権を持つ 1 ファイルだけ（§5.1-4: 二重定義は必ず食い違う）。
    """
    import math

    file_spec = _BARS_FILES[bars_file]
    if not file_spec["declares_shared_scalars"]:
        # 共有スカラーは再宣言せず参照する（設計 §5.1-4）。書けてしまうと、
        # M2 側と食い違う値が静かに効く経路が開く。
        redeclared = [key for key in _SHARED_BAR_SCALARS if key in bar_block]
        if redeclared:
            raise ValueError(
                f"{bars_file}: 共有スカラー {redeclared} を再宣言している; "
                f"{', '.join(_SHARED_BAR_SCALARS)} は m2_accuracy_bars.yaml の値を参照し、"
                "帯ごとに別値を持たせない（二重定義は必ず食い違う・fail-closed）"
            )
        _require_owned_categories_well_formed(bar_block, bars_file=bars_file)
        _require_bar_threshold_domains(bar_block, bars_file=bars_file)
        return

    tolerance = bar_block.get("tolerance_cents", DEFAULT_TOLERANCE_CENTS)
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)):
        raise ValueError(f"m2_accuracy_bars: tolerance_cents {tolerance!r} が数値でない")
    if not math.isfinite(float(tolerance)) or float(tolerance) <= 0.0:
        raise ValueError(
            f"m2_accuracy_bars: tolerance_cents {tolerance!r} が非有限または非正 (fail-closed)"
        )

    repeats_min = bar_block.get("repeats_min", 2)
    if isinstance(repeats_min, bool) or not isinstance(repeats_min, int):
        raise ValueError(f"m2_accuracy_bars: repeats_min {repeats_min!r} が整数でない")
    if repeats_min < 2:
        # `repeats_min: 1` を許すと単一 report が「不十分な repeats」検査を素通りし、
        # `_repeats_bit_identical` も singleton を自明に一致と見なすため、**一度も
        # 測り直していない** S_direct pass が publish できる（Codex P2）。ハーネスが
        # 掲げる n>=2 の決定論契約（設計 §4）を loader 段階で強制する。
        raise ValueError(
            f"m2_accuracy_bars: repeats_min {repeats_min!r} が 2 未満; 決定論確認は "
            "n>=2 の測り直しを要件とする（単一 report の bit 一致は自明に成立する）"
            " (fail-closed)"
        )

    # 有声判定閾値も凍結バーの一部（実測後に動かして VFA を改善させない）。
    floor = bar_block.get("est_voiced_confidence_floor")
    if floor is None:
        raise ValueError(
            "m2_accuracy_bars: est_voiced_confidence_floor が無い; 抽出器の "
            "frame_confidence を推定 voicing へ変換する閾値は事前登録が必須 (fail-closed)"
        )
    if isinstance(floor, bool) or not isinstance(floor, (int, float)):
        raise ValueError(
            f"m2_accuracy_bars: est_voiced_confidence_floor {floor!r} が数値でない"
        )
    if not math.isfinite(float(floor)) or not (0.0 <= float(floor) <= 1.0):
        raise ValueError(
            f"m2_accuracy_bars: est_voiced_confidence_floor {floor!r} が非有限または "
            "[0, 1] の外 (fail-closed)"
        )

    _require_owned_categories_well_formed(bar_block, bars_file=bars_file)
    _require_bar_threshold_domains(bar_block, bars_file=bars_file)


def _require_owned_categories_well_formed(
    bar_block: Dict[str, Any], *, bars_file: str
) -> None:
    """`bars_file` が所有するカテゴリのバー存在・必須キーを検証する（fail-closed）。"""
    # 受け入れゲートを持つべきカテゴリのバーが空/欠落だと、`evaluate_m2_bars` の
    # 「バーなし → diagnostic_only」分岐に落ちて RPA/VFA 判定が黙って消える。
    # 事前登録されたカテゴリのうち診断専用でないものは、閾値の存在を要求する。
    for category in _categories_owned_by(bars_file):
        if category in _DIAGNOSTIC_ONLY_CATEGORIES:
            if bar_block.get(category):
                raise ValueError(
                    f"m2_accuracy_bars: category {category!r} は診断専用（設計 §8）なので "
                    f"閾値を持てない: {bar_block.get(category)!r} (fail-closed)"
                )
            continue
        bar = bar_block.get(category)
        if not isinstance(bar, dict) or not bar:
            raise ValueError(
                f"m2_accuracy_bars: category {category!r} の閾値が空/欠落（{bar!r}）; "
                "受け入れゲートを持つカテゴリを diagnostic_only へ落として判定を無効化 "
                "させない (fail-closed)"
            )
        required = _REQUIRED_BAR_KEYS_BY_CATEGORY.get(category)
        if required is None:
            raise ValueError(
                f"m2_accuracy_bars: category {category!r} は受け入れゲートを持つが必須 "
                "閾値キー集合が事前登録されていない (fail-closed)"
            )
        missing = [key for key in required if key not in bar]
        if missing:
            raise ValueError(
                f"m2_accuracy_bars: category {category!r} が必須閾値 {missing} を欠く; "
                "部分的なバーは事前登録されたゲートの一部を黙って無効化する (fail-closed)"
            )


def _require_bar_threshold_domains(bar_block: Dict[str, Any], *, bars_file: str) -> None:
    """バー block の各閾値の型・有限性・定義域と、block の所有権を検証する。

    所有権（設計 §5.2）: **そのファイルが所有しないカテゴリの block があれば拒否**する。
    ただし `_CATEGORY_SPECS` に行を持たない名前（例: `V_fullstack` = 事前登録済み・
    未配線）は従来どおり許す——「帯の登録はハーネス配線を前提にしない」という既存の
    先例を、分離を理由に壊さないため。塞ぐべきは「他ファイルが所有するカテゴリの
    バーを、こちらのファイルにも書く」という二重定義の穴である。
    """
    import math

    for category, bar in bar_block.items():
        if not isinstance(bar, dict):
            continue
        owner = _CATEGORY_SPECS.get(category, {}).get("bars_file")
        if owner is not None and owner != bars_file:
            raise ValueError(
                f"{bars_file}: category {category!r} の block を持っているが、その所有者は "
                f"{owner!r}; 同名カテゴリのバーが 2 ファイルに現れる状態を作らない "
                "(fail-closed)"
            )
        for key, value in bar.items():
            if key not in _BAR_THRESHOLD_RANGES:
                raise ValueError(
                    f"m2_accuracy_bars: category {category!r} に未知の閾値キー {key!r}; "
                    f"評価器が解釈できない閾値を黙って無視しない (fail-closed)"
                )
            low, high = _BAR_THRESHOLD_RANGES[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"m2_accuracy_bars: category {category!r} の {key} {value!r} が数値でない"
                )
            if not math.isfinite(float(value)):
                raise ValueError(
                    f"m2_accuracy_bars: category {category!r} の {key} が非有限（{value!r}）; "
                    "未定義のバーは比較が常に False になり pass を偽造する (fail-closed)"
                )
            if not (low <= float(value) <= high):
                raise ValueError(
                    f"m2_accuracy_bars: category {category!r} の {key} {value!r} が "
                    f"定義域 [{low}, {high}] の外 (fail-closed)"
                )


def _require_specs_pin(specs_sha256: str, bars: Dict[str, Any]) -> None:
    """bars の `provenance.specs_sha256` が実 specs ファイルと一致するか確認する。

    バー（S_direct の min_rpa 等）は specs が定義する fixture を前提に凍結された
    ものなので、specs が drift したまま古いバーを適用すると「別の合成音に対する
    事前登録」を機械適用してしまう（registry.yaml の waveform_sha256 pin と同型の
    fail-closed）。
    """
    expected = bars.get("provenance", {}).get("specs_sha256")
    if expected is None:
        raise ValueError("m2_accuracy_bars.yaml lacks provenance.specs_sha256 pin (fail-closed)")
    if expected != specs_sha256:
        raise ValueError(
            f"m2_accuracy_specs.yaml sha256 mismatch: {specs_sha256} != bars pin {expected}. "
            "spec を変更したなら m2_accuracy_bars.yaml の provenance.specs_sha256 を更新し、"
            "dated 再実測すること。"
        )


# ---------------------------------------------------------------------------
# 波形合成（S_direct: 単体旋律 / S_fullstack: 旋律+伴奏ミックス）
# ---------------------------------------------------------------------------


def _sha256_of_fd(fd: int) -> str:
    """開いている fd の中身全体を先頭から hash する（path 再 open ではなく inode を読む）。

    path 経由の再 read では「digest の後に別ファイルへ差し替え、デコーダに読ませ、
    元へ戻す」replace-and-restore の窓が残る（Codex P2）。保持した fd は inode に
    束縛されるため、pre/post の hash が**同じ bytes 列**を指すことが保証される。
    """
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while True:
        chunk = os.read(fd, 1 << 20)
        if not chunk:
            break
        digest.update(chunk)
    return digest.hexdigest()


def _waveform_sha256(y: np.ndarray) -> str:
    """raw float32 サンプルの sha256（registry.yaml の waveform_sha256 と同一定義）。"""
    return hashlib.sha256(np.asarray(y, dtype=np.float32).tobytes()).hexdigest()


def _build_direct_waveform(fixture_id: str, specs: Dict[str, Any]) -> Tuple[np.ndarray, int]:
    return build_signal(fixture_id, specs)


def _build_fullstack_waveform(composite_id: str, specs: Dict[str, Any]) -> Tuple[np.ndarray, int]:
    """`composites` 定義に従い旋律 + 伴奏をミックスする（build_melody_bench.py 未変更）。

    `build_signal` は 1 fixture の波形しか知らないため、ミックスはハーネス側
    （本モジュール）の責務にする——`melody/extractors.py` も `build_melody_bench.py`
    もコード変更しないという設計 §1 のスコープ制約を満たす。
    """
    composite = specs["composites"][composite_id]
    melody_y, melody_sr = build_signal(composite["melody"], specs)
    accomp_y, accomp_sr = build_signal(composite["accompaniment"], specs)
    if melody_sr != accomp_sr:
        raise ValueError(
            f"composite {composite_id!r}: melody/accompaniment sample_rate mismatch "
            f"({melody_sr} != {accomp_sr}); m2_accuracy_specs.yaml declares a single "
            "top-level sample_rate so this should be structurally impossible"
        )
    gain = float(composite.get("accompaniment_gain", 1.0))
    melody_y = np.asarray(melody_y, dtype=np.float32)
    accomp_y = np.asarray(accomp_y, dtype=np.float32)
    n = max(len(melody_y), len(accomp_y))
    mixed = np.zeros(n, dtype=np.float32)
    mixed[: len(melody_y)] += melody_y
    mixed[: len(accomp_y)] += gain * accomp_y
    return mixed, melody_sr


def _build_category_waveform(
    category: str, category_spec: Dict[str, str], specs: Dict[str, Any], bars: Dict[str, Any]
) -> Tuple[np.ndarray, int, str]:
    """category の波形を合成し、bars の waveform_sha256 pin と照合する（fail-closed）。"""
    if category_spec["kind"] == "direct":
        y, sr = _build_direct_waveform(category_spec["fixture_id"], specs)
        pin_key = category_spec["fixture_id"]
    elif category_spec["kind"] == "fullstack":
        y, sr = _build_fullstack_waveform(category_spec["composite_id"], specs)
        pin_key = category_spec["composite_id"]
    else:  # pragma: no cover - 内部定義の不整合防止
        raise ValueError(f"unknown category kind {category_spec['kind']!r}")

    waveform_sha256 = _waveform_sha256(y)
    pins: Dict[str, str] = bars.get("provenance", {}).get("waveform_sha256", {})
    expected = pins.get(pin_key)
    if expected is None:
        raise ValueError(
            f"category {category!r} (pin key {pin_key!r}) lacks a "
            "m2_accuracy_bars.yaml provenance.waveform_sha256 pin (fail-closed 事前登録)"
        )
    if expected != waveform_sha256:
        raise ValueError(
            f"category {category!r} waveform sha256 mismatch: {waveform_sha256} != bars "
            f"pin {expected}. m2_accuracy_specs.yaml / build_melody_bench.py / composite "
            "ミックス式が drift している — bars の waveform_sha256 を更新し dated 再実測 "
            "すること。"
        )
    return y, sr, waveform_sha256


def _reference_for_category(
    category_spec: Dict[str, str], specs: Dict[str, Any], *, total_duration_sec: Optional[float]
) -> Tuple[Tuple[float, ...], Tuple[float, ...]]:
    """category の正解 f0 系列。S_direct/S_fullstack とも melody spec 単体が正解

    （「正解 = spec そのもの」は伴奏を混ぜても変わらない。設計 §3 カテゴリ S）。

    `sample_rate` は specs の合成レートをそのまま渡す——正解の区間境界は
    `build_melody_bench.py` が実際にレンダする整数標本境界と一致させる必要があり、
    秒の累積では量子化端数がずれる（Codex 指摘）。
    """
    melody_fixture_id = (
        category_spec["fixture_id"]
        if category_spec["kind"] == "direct"
        else specs["composites"][category_spec["composite_id"]]["melody"]
    )
    melody_spec = specs["fixtures"][melody_fixture_id]
    return reference_f0_from_monophonic_spec(
        melody_spec,
        sample_rate=int(specs["sample_rate"]),
        total_duration_sec=total_duration_sec,
    )


# ---------------------------------------------------------------------------
# route 選択（routing.py は変更しない。既存表から名前で引く）
# ---------------------------------------------------------------------------


def _est_freqs_with_voicing(
    observation: MelodyObservation, *, confidence_floor: float
) -> Tuple[float, ...]:
    """抽出器の (frame_hz, frame_confidence) を mir_eval の推定 voicing 表現へ変換する。

    CREPE（`crepe_adapter.extract_crepe_f0`）は**無声フレームでも最尤 F0 を正値で
    返す**契約で、有声の証拠は `frame_confidence` に分離されている。`frame_hz` を
    そのまま mir_eval に渡すと「推定は全フレーム有声」と解釈され、正解が無音の
    フレームすべてが false alarm に数えられて VFA が事実上 1.0 に張り付く——精度の
    良い CREPE run でも凍結 `max_vfa` を落とす（Codex P1）。

    変換は mir_eval が文書化している符号規約に従う（`to_cent_voicing` の docstring:
    ``est_voicing`` を渡さない場合、**負の周波数**は「無声と予測したが、有声なら
    この推定値」を意味する）。したがって:

    - ``confidence >= confidence_floor`` → ``+|hz|``（有声と予測）
    - ``confidence <  confidence_floor`` → ``-|hz|``（無声と予測・推定値は保持）
    - ``hz == 0``                        → ``0.0``（推定値そのものが無い）

    負値化は**ピッチ推定値を捨てない**ため、RPA/RCA（推定 voicing を見ない MIREX
    定義）は変換前と一致し、VR/VFA/OA だけが正しくなる。閾値は
    `m2_accuracy_bars.yaml` の `est_voiced_confidence_floor` として事前登録された
    凍結値で、実測後に動かさない（一方向規律）。
    """
    signed: List[float] = []
    for index, (hz, confidence) in enumerate(
        zip(observation.frame_hz, observation.frame_confidence)
    ):
        value = float(confidence)
        # 閾値比較の前に confidence 自体の定義域を検証する。`NaN >= floor` は False
        # なので、非有限の confidence は黙って「無声と予測」（負周波数）へ変換され、
        # 不正な観測が VFA を人工的に下げて凍結バーを通しうる（Codex P2 第 30 巡）。
        # 抽出器の契約違反は fail-closed に倒し、voicing 判定に変換しない。
        if not math.isfinite(value) or not (0.0 <= value <= 1.0):
            raise ValueError(
                f"_est_freqs_with_voicing: frame_confidence[{index}] {confidence!r} が "
                "非有限または [0, 1] の外; 抽出器の契約違反を voicing 判定へ変換しない "
                "(fail-closed)"
            )
        magnitude = abs(float(hz))
        if magnitude == 0.0:
            signed.append(0.0)
        elif value >= confidence_floor:
            signed.append(magnitude)
        else:
            signed.append(-magnitude)
    return tuple(signed)


def _serialize_wav_float32(y: "np.ndarray", sample_rate: int) -> bytes:
    """モノラル float32 波形を**決定論的な** RIFF/WAVE bytes へ直列化する。

    `sf.write(..., subtype="FLOAT")`（libsndfile）は float WAV に PEAK チャンクを
    書き、そこに**壁時計タイムスタンプ**が入るため、同一波形でも直列化 bytes が
    秒単位で変わる（実測: 2 run の WAV は offset 60 の timestamp 1 byte だけ相違）。
    それでは `input_wav_sha256` を評価器の測り直しへ束縛できない（Codex P2 第 38 巡）
    ため、fmt(IEEE float)/fact/data のみの最小 RIFF を自前で構成する——同一
    (y, sample_rate) → 同一 bytes。デコード互換性は既存の readback 検証
    （libsndfile で読み戻して波形 pin と bit 一致）が毎 run 確認する。
    """
    import struct

    samples = np.asarray(y, dtype=np.float32)
    if samples.ndim != 1:
        raise ValueError(
            f"_serialize_wav_float32: モノラル 1-D 波形のみ対応（shape {samples.shape!r}）"
        )
    data = samples.tobytes()
    rate = int(sample_rate)
    fmt_body = struct.pack("<HHIIHH", 3, 1, rate, rate * 4, 4, 32)  # WAVE_FORMAT_IEEE_FLOAT
    fact_body = struct.pack("<I", samples.size)
    payload = (
        b"WAVE"
        + b"fmt " + struct.pack("<I", len(fmt_body)) + fmt_body
        + b"fact" + struct.pack("<I", len(fact_body)) + fact_body
        + b"data" + struct.pack("<I", len(data)) + data
    )
    return b"RIFF" + struct.pack("<I", len(payload)) + payload


def _select_named_route(input_kind: str, route_name: str) -> MelodyRoute:
    for route in select_routes(input_kind):
        if route.name == route_name:
            return route
    raise ValueError(
        f"route {route_name!r} not found among select_routes({input_kind!r}) candidates; "
        "melody/routing.py の経路表が drift した可能性がある (fail-closed)"
    )


# ---------------------------------------------------------------------------
# M2c: 外部素材（音声 + 注釈 CSV）manifest。カテゴリ V_direct 専用。
# ---------------------------------------------------------------------------


def _load_external_manifest(path: "str | Path") -> Tuple[List[Dict[str, Any]], str, Path]:
    """外部素材 manifest（JSON 配列）を single read で読み、entries と束ねて返す。

    manifest は `[{id, audio_path, annotation_path}, ...]`。read → hash → parse を
    1 操作にまとめる（`load_bars`/`load_specs` と同じ TOCTOU 回避規律）。id は
    manifest 内で相互に distinct でなければならない。
    """
    manifest_path = Path(path).resolve()
    data = manifest_path.read_bytes()
    manifest_sha256 = hashlib.sha256(data).hexdigest()
    entries = _json_loads_no_dup_keys(data, what="external manifest")
    if not isinstance(entries, list) or not entries:
        raise ValueError(
            "external manifest: 非空の JSON 配列でなければならない (fail-closed)"
        )
    seen_ids: "set[str]" = set()
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"external manifest[{idx}]: mapping でない (fail-closed)")
        for key in ("id", "audio_path", "annotation_path"):
            value = entry.get(key)
            if not isinstance(value, str) or not value:
                raise ValueError(
                    f"external manifest[{idx}].{key} {value!r} が非空文字列でない "
                    "(fail-closed)"
                )
        clip_id = entry["id"]
        _require_safe_external_id(clip_id, where=f"external manifest[{idx}]")
        if clip_id in seen_ids:
            raise ValueError(
                f"external manifest: duplicate clip id {clip_id!r} (fail-closed)"
            )
        seen_ids.add(clip_id)
    return entries, manifest_sha256, manifest_path


def _resolve_external_member_path(manifest_dir: Path, value: str, *, what: str) -> Path:
    """manifest entry のパスを manifest 位置基準で解決する（既存 pathsafe 流儀）。

    M2c PR-M2c-1 review（Codex 第 3 巡 P1）: `resolve_confined`（base-dependent・
    filesystem に触れる物理検証）**単独**では、`manifest_dir` 内のシンボリックリンク
    経由の入り組んだ経路（例: `link/../../outside`——`link` が base 内の別ディレクトリを
    指す symlink で、その先から `..` を重ねて base 外へ出る形）を「最終的な解決先が
    たまたま base 配下」と誤認しうる幾何を作り込める。`resolve_confined` を呼ぶ**前**に
    `validate_relative_locator`（base に依存しない字句検証: 絶対パス・net-upward な
    `..` を機械的に拒否）を通すことで、そもそも `..` を使った疑わしい構造の入力を
    filesystem 解決の前段で弾く——pathsafe モジュール自身が想定する「lexical +
    physical」二層防御（`_build_external_clip_row` の clip_id 経路 = 字句
    `_require_safe_external_id` + 物理 `resolve_confined` と同型）を manifest member
    パスにも揃える。
    """
    try:
        validate_relative_locator(value)
        return resolve_confined(value, manifest_dir)
    except PathConfinementError as exc:
        raise ValueError(
            f"external manifest: {what} {value!r} を manifest 位置基準で解決できない "
            f"（{exc.reason}）; manifest ディレクトリ外を指すパスは許容しない "
            "(fail-closed)"
        ) from exc


def _parse_external_annotation_csv(raw: bytes, *, clip_id: str) -> Tuple[Tuple[float, ...], Tuple[float, ...]]:
    """外部注釈 CSV を (times_sec, freqs_hz) へ変換する（ネイティブタイムラインのまま）。

    1 列目 time_sec・2 列目 frequency_hz（3 列目以降は無視）。**有限**な frequency_hz が
    0 以下（無声を表す一般的な慣例: 0 / 負値）のフレームは無声 = 0.0 へ正規化する。
    10ms へのリサンプルは行わない——設計 §2 追記（M2c）: 「外部注釈はネイティブ
    タイムラインのまま評価する（mir_eval が est を ref 基準へ整列。リサンプル補間と
    いう新たな pin 対象を作らない。10ms 規約は合成正解の導出形式）」。

    M2c §8 self-audit（Codex 第 3 巡）: `np.genfromtxt` はパース不能な非数値トークン
    （例: 破損/誤フォーマットの CSV セル）を**例外を投げずに** `NaN` として黙って
    埋める。「無声の慣例（有限の 0/負値）」と「パース失敗（NaN）」を同一視して両方
    0.0 へ丸めると、破損した注釈ファイルが「全フレーム無声」という一見もっともらしい
    正解として静かに受理されてしまう（fail-closed の被覆漏れ）。frequency_hz 列は
    time_sec 列と同じく非有限値を明示的に拒否してから、**有限**値のみに対して
    「0 以下は無声」の正規化を適用する。

    Codex 第 5 巡 P2: time_sec 列は非有限値の拒否に加え、**非負かつ厳密増加**である
    ことも要求する（重複・逆順・負値は mir_eval へのネイティブタイムライン整列前提を
    静かに壊す入力として fail-closed で拒否する）。
    """
    import io

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"external annotation for clip {clip_id!r}: UTF-8 として decode できない "
            "(fail-closed)"
        ) from exc
    data = np.genfromtxt(io.StringIO(text), delimiter=",", dtype=float, ndmin=2)
    if data.size == 0 or data.ndim != 2 or data.shape[1] < 2:
        raise ValueError(
            f"external annotation for clip {clip_id!r}: 空、または 2 列（time_sec, "
            "frequency_hz）未満の CSV (fail-closed)"
        )
    times = data[:, 0]
    freqs = data[:, 1]
    if not np.all(np.isfinite(times)):
        raise ValueError(
            f"external annotation for clip {clip_id!r}: time_sec に非有限値（NaN/inf、"
            "パース不能なセルの可能性）がある (fail-closed)"
        )
    if np.any(times < 0.0):
        first_bad_index = int(np.argmax(times < 0.0))
        raise ValueError(
            f"external annotation for clip {clip_id!r}: time_sec に負値がある "
            f"(index={first_bad_index}) (fail-closed)"
        )
    if times.shape[0] >= 2 and np.any(np.diff(times) <= 0.0):
        first_bad_index = int(np.argmax(np.diff(times) <= 0.0)) + 1
        raise ValueError(
            f"external annotation for clip {clip_id!r}: time_sec が厳密増加でない "
            f"（重複または減少、index={first_bad_index}） (fail-closed)"
        )
    if not np.all(np.isfinite(freqs)):
        raise ValueError(
            f"external annotation for clip {clip_id!r}: frequency_hz に非有限値（NaN/inf、"
            "パース不能なセルの可能性）がある; 「無声の慣例（有限の 0/負値）」と "
            "「パース失敗」を同一視しない (fail-closed)"
        )
    normalized_freqs = np.where(freqs > 0.0, freqs, 0.0)
    return (
        tuple(float(t) for t in times),
        tuple(float(f) for f in normalized_freqs),
    )


@dataclass(frozen=True)
class _ExternalClipInputs:
    """1 clip の音声/注釈を読み、登録済み sha256 と照合済みの中間結果（設計 §8.7）。

    `_build_external_clip_row` の入力読み込み部分を抽出した値オブジェクト。§8.7 の
    セルチェックポイントは「抽出器を走らせる前に」入力 digest を確定させる必要が
    あるため（resume 可否をそこで判定する）、読み込み+照合だけを独立に呼べるように
    切り出した。`_build_external_clip_row` 自身もこれを受け取れるようにし、
    ハッシュ計算ロジックを複製しない。
    """

    audio_path: Path
    audio_bytes: bytes
    audio_sha256: str
    annotation_sha256: str
    ref_times: Tuple[float, ...]
    ref_freqs: Tuple[float, ...]


def _read_external_clip_inputs(
    clip_id: str,
    entry: Dict[str, Any],
    *,
    manifest_dir: Path,
    fixtures: Dict[str, Any],
) -> _ExternalClipInputs:
    """clip_id の音声/注釈を読み、登録済み sha256 と fail-closed で照合する（設計 Memo M2c）。"""
    if clip_id not in fixtures:
        raise ValueError(
            f"external manifest: clip id {clip_id!r} が m2c_external_fixtures.yaml に "
            "事前登録されていない (fail-closed)"
        )
    expected = fixtures[clip_id]

    audio_path = _resolve_external_member_path(manifest_dir, entry["audio_path"], what="audio_path")
    annotation_path = _resolve_external_member_path(
        manifest_dir, entry["annotation_path"], what="annotation_path"
    )

    # 単一 read_bytes → sha256 計算 → fail-closed 照合（設計 Memo M2c）。
    audio_bytes = audio_path.read_bytes()
    audio_sha256 = hashlib.sha256(audio_bytes).hexdigest()
    if audio_sha256 != expected["expected_audio_sha256"]:
        raise ValueError(
            f"external clip {clip_id!r}: audio sha256 mismatch ({audio_sha256} != "
            f"registered {expected['expected_audio_sha256']}) (fail-closed)"
        )
    annotation_bytes = annotation_path.read_bytes()
    annotation_sha256 = hashlib.sha256(annotation_bytes).hexdigest()
    if annotation_sha256 != expected["expected_annotation_sha256"]:
        raise ValueError(
            f"external clip {clip_id!r}: annotation sha256 mismatch ({annotation_sha256} "
            f"!= registered {expected['expected_annotation_sha256']}) (fail-closed)"
        )

    ref_times, ref_freqs = _parse_external_annotation_csv(annotation_bytes, clip_id=clip_id)
    return _ExternalClipInputs(
        audio_path=audio_path,
        audio_bytes=audio_bytes,
        audio_sha256=audio_sha256,
        annotation_sha256=annotation_sha256,
        ref_times=ref_times,
        ref_freqs=ref_freqs,
    )


# M2e §8.3 の並列不変性ゲートが比較する artifact のフォーマット識別子。
# **直列化仕様を変えたら必ずこの版も上げる**（同じ識別子で別 bytes を畳むと、
# 別仕様で採った digest 同士が「一致しなかった」ではなく「別物として一致した／
# しなかった」の区別を失う）。
_EST_TRAJECTORY_DIGEST_MAGIC = b"m2e-est-trajectory/1\n"


def _est_trajectory_sha256(
    frame_times: "Tuple[float, ...]", est_freqs: "Tuple[float, ...]"
) -> str:
    """推定ピッチ軌跡 `(frame_times, est_freqs)` の canonical 直列化の sha256。

    設計 §8.3 の並列不変性ゲート（`P=1` と `P=決定値` で**軌跡が完全一致**）が
    比較する artifact。**精度値（RPA 等）の一致では不十分**——平均化・丸め・
    フレーム集計を経た指標は、軌跡が違っても偶然一致しうる。

    直列化仕様（`_EST_TRAJECTORY_DIGEST_MAGIC` = `m2e-est-trajectory/1`。テストで pin）:

    1. マジック `b"m2e-est-trajectory/1\\n"`
    2. フレーム数 `n` を `struct.pack("<Q", n)`
    3. `i = 0..n-1` について `struct.pack("<dd", frame_times[i], est_freqs[i])`

    **float の `repr()` / `json` 表現は使わない。** テキスト表現は Python 版・
    プラットフォーム・locale で揺れうるため、同じ軌跡が別 digest を持ちうる
    （`_serialize_wav_float32` が明示 struct pack を使うのと同じ流儀）。IEEE754
    binary64 の little-endian bytes をそのまま畳む。

    正規化と fail-closed:

    - `-0.0` は `0.0` へ正規化する（bytes が違うが値は同じ。無声フレームの
      符号ゆらぎで digest が割れるのを防ぐ）。
    - 非有限値（NaN/inf）は拒否する。NaN は payload によって bytes が違い、
      「同じ NaN」が別 digest を持ちうる——digest が黙って比較不能になるより、
      その場で落ちる方がよい。
    - 長さ不一致も拒否する（時刻と周波数の対応が壊れた軌跡を畳まない）。
    """
    if len(frame_times) != len(est_freqs):
        raise ValueError(
            f"_est_trajectory_sha256: frame_times {len(frame_times)} と est_freqs "
            f"{len(est_freqs)} の長さが不一致; 時刻と周波数の対応が取れない軌跡を "
            "digest にしない (fail-closed)"
        )
    chunks: List[bytes] = [_EST_TRAJECTORY_DIGEST_MAGIC, struct.pack("<Q", len(frame_times))]
    for index, (t, f) in enumerate(zip(frame_times, est_freqs)):
        t = float(t)
        f = float(f)
        if not (math.isfinite(t) and math.isfinite(f)):
            raise ValueError(
                f"_est_trajectory_sha256: frame {index} が非有限 (time={t!r}, freq={f!r}); "
                "NaN/inf は bit 表現が一意でなく digest 比較が黙って壊れる (fail-closed)"
            )
        # `-0.0` → `0.0`（`x == 0.0` は `-0.0` にも真）。
        chunks.append(struct.pack("<dd", 0.0 if t == 0.0 else t, 0.0 if f == 0.0 else f))
    return hashlib.sha256(b"".join(chunks)).hexdigest()


def _category_records_est_trajectory(category: str) -> bool:
    """`est_trajectory_sha256` を row へ刻むカテゴリか（**M2e カテゴリに限る**）。

    設計判断 D-1(b): 全カテゴリの row へ足すと、commit 済みの M2b/M2c 記録と
    突き合わせるテストが期待値差分で割れ、そこで schema を広げて吸収する誘惑が
    生じる（PR #71 型の churn）。M2e カテゴリだけに限定すれば既存 fixture は
    バイト不変のまま、r4 で実 run に対して実ゲートを回せる。
    """
    spec = _CATEGORY_SPECS.get(category)
    return bool(spec) and spec.get("bars_file") == "m2e_accuracy_bars.yaml"


def _build_external_clip_row(
    clip_id: str,
    entry: Dict[str, Any],
    *,
    manifest_dir: Path,
    fixtures: Dict[str, Any],
    tolerance_cents: float,
    est_voiced_floor: float,
    route: MelodyRoute,
    runner: RouteRunner,
    tmp_dir: Path,
    inputs: "Optional[_ExternalClipInputs]" = None,
    record_est_trajectory: bool = False,
) -> Dict[str, Any]:
    """1 clip の外部素材を測り、per-clip row（設計 Memo M2c）を返す。

    `inputs` を渡すと音声/注釈の read + hash + 照合を再実行しない（設計 §8.7 の
    セルチェックポイントが resume 可否判定のために先に読んでいる場合。
    `_read_external_clip_inputs` 参照）。省略時は従来どおりここで読む
    （挙動無変更 —— `--cell-store` 未使用の呼び出し経路は本引数を渡さない）。

    `record_est_trajectory`（D-1(b)・M2e カテゴリのみ True）を立てると
    `outcome == "measured"` の row に `est_trajectory_sha256` を刻む（§8.3 の
    並列不変性ゲートの比較対象）。`unavailable` の row には**キーごと置かない**
    ——測っていない軌跡に sentinel を置いて schema を広げない。
    """
    if inputs is None:
        inputs = _read_external_clip_inputs(
            clip_id, entry, manifest_dir=manifest_dir, fixtures=fixtures
        )

    audio_path = inputs.audio_path
    audio_bytes = inputs.audio_bytes
    audio_sha256 = inputs.audio_sha256
    annotation_sha256 = inputs.annotation_sha256
    ref_times, ref_freqs = inputs.ref_times, inputs.ref_freqs

    row: Dict[str, Any] = {
        "clip_id": clip_id,
        "audio_sha256": audio_sha256,
        "annotation_sha256": annotation_sha256,
        "ref_frame_count": len(ref_times),
        "ref_voiced_frame_count": sum(1 for f in ref_freqs if f > 0.0),
    }

    suffix = audio_path.suffix or ".wav"
    # M2c PR-M2c-1 review（Codex 第 1 巡 P1）: clip_id は `_require_safe_external_id`
    # で字句検証済みだが、tmp_dir への join 後も物理的に tmp_dir 配下へ収まることを
    # `resolve_confined` で重ねて確認する（pathsafe の lexical + physical 二段防御と
    # 同型。字句検証だけに頼らない防御的な二重化）。
    try:
        frozen_wav_path = resolve_confined(f"{clip_id}{suffix}", tmp_dir)
    except PathConfinementError as exc:
        raise RuntimeError(
            f"external clip {clip_id!r}: 凍結コピー先 {clip_id}{suffix} が tmp_dir を "
            f"脱出する（{exc.reason}）(fail-closed)"
        ) from exc
    frozen_wav_path.write_bytes(audio_bytes)
    os.chmod(frozen_wav_path, 0o400)
    wav_fd = os.open(frozen_wav_path, os.O_RDONLY)
    try:
        pre_sha256 = _sha256_of_fd(wav_fd)
        if pre_sha256 != audio_sha256:
            raise RuntimeError(
                f"external clip {clip_id!r}: 凍結コピーの hash が読み込んだ bytes と "
                "不一致 (fail-closed)"
            )
        # M2c §8 self-audit（Codex 第 3 巡）: S カテゴリの run ループと同じ tmp_dir
        # 0500/0700 の挟み込みをここにも適用する。凍結ファイル自体は 0400 だが、
        # rename/unlink はディレクトリの書き込み権限で決まるため、ファイル権限だけでは
        # 「抽出中に同 uid プロセスが rename で差し替える」ことを防げない
        # （明示 chmod まで行う同権限攻撃者はプロセスメモリも書ける = preload ゲート群と
        # 同じ境界外、という前例の整理どおり）。
        os.chmod(tmp_dir, 0o500)
        try:
            try:
                observation, route_provenance = runner(str(frozen_wav_path), route)
            except LearnedModelUnavailable as exc:
                row["outcome"] = "unavailable"
                row["detail"] = str(exc).splitlines()[0]
                return row
        finally:
            os.chmod(tmp_dir, 0o700)
        post_sha256 = _sha256_of_fd(wav_fd)
        if post_sha256 != pre_sha256:
            raise RuntimeError(
                f"external clip {clip_id!r}: 抽出中に入力音声が差し替えられた "
                f"（{pre_sha256} → {post_sha256}）; 測っていない bytes の正解に対する "
                "採点を publish しない (fail-closed)"
            )
    finally:
        os.close(wav_fd)

    est_freqs = _est_freqs_with_voicing(observation, confidence_floor=est_voiced_floor)
    metrics: MelodyAccuracyResult = evaluate_melody_accuracy(
        ref_times,
        ref_freqs,
        observation.frame_times,
        est_freqs,
        tolerance_cents=tolerance_cents,
    )
    row["outcome"] = "measured"
    row["metrics"] = metrics.to_dict()
    row["est_frame_count"] = len(est_freqs)
    row["est_voiced_frame_count"] = sum(1 for f in est_freqs if f > 0.0)
    if record_est_trajectory:
        # §8.3 の並列不変性ゲートが比較する artifact。**採点に使ったのと同じ
        # snapshot**（`observation.frame_times` と `est_freqs`）から導く——別途
        # 読み直すと「digest を採った bytes」と「実際に採点した bytes」が
        # 食い違いうる。
        row["est_trajectory_sha256"] = _est_trajectory_sha256(
            tuple(observation.frame_times), tuple(est_freqs)
        )
    row["source_model"] = observation.source_model
    for key, value in route_provenance.items():
        row[f"provenance_{key}"] = value
    return row


# r7 blocker (f06bbaa3): `provenance_preprocessing` の中で per-clip 固有に変わって
# よいキーの allowlist。`stem_sha256` は分離器が生成した stem バイト列の指紋であり
# clip ごとの音声内容に依存するため、同一カテゴリの複数 clip 間で一致する理由が
# ない（separation の重み/コード/version/model は clip に依らないカテゴリ不変量）。
# ここに載らないキー（将来追加される未知キーを含む）は全て不変量として扱い、
# clip 間で 1 文字でも割れれば fail-closed とする——allowlist 方式（許可を明示
# 列挙し、未知は安全側＝不変量要求に倒す）。
PER_CLIP_PREPROCESSING_KEYS = frozenset({"stem_sha256"})


def split_preprocessing_invariants(
    preprocessing: "Dict[str, Any] | None",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """`provenance_preprocessing` を (カテゴリ不変量, per-clip 量) に分割する。

    per-clip 量 = `PER_CLIP_PREPROCESSING_KEYS` に載るキーのみ。それ以外の全キー
    （未知キー含む）は不変量側に残す。`preprocessing` が dict でない（分離不要
    route の row は `provenance_preprocessing` キー自体を持たず `None` になる）場合は
    空の 2 dict を返す——呼び出し側で `isinstance` チェックを重複させない。
    """
    if not isinstance(preprocessing, dict):
        return {}, {}
    invariants: Dict[str, Any] = {}
    per_clip: Dict[str, Any] = {}
    for key, value in preprocessing.items():
        if key in PER_CLIP_PREPROCESSING_KEYS:
            per_clip[key] = value
        else:
            invariants[key] = value
    return invariants, per_clip


def _average_external_clip_metrics(clip_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """measured clip 群の per-metric 算術平均（設計 Memo M2c: カテゴリ metrics）。

    `tolerance_cents` は clip 間で共通の定数（run 全体で 1 つの許容幅）のはずなので
    平均ではなく一致を要求する。`median_cent_error` は該当フレーム 0 件の clip
    （None）を平均から除く——全 clip が None なら結果も None。
    `voiced_chroma_correct_frame_count` は算術平均を float のまま保持する（カテゴリ
    レベルの記録値であり、S カテゴリの「厳密整数」契約とは別の集計値）。
    """
    if not clip_rows:
        raise ValueError("_average_external_clip_metrics: clip_rows が空 (fail-closed)")
    tolerances = {float(c["metrics"]["tolerance_cents"]) for c in clip_rows}
    if len(tolerances) > 1:
        raise ValueError(
            f"_average_external_clip_metrics: clip 間で tolerance_cents が不一致 "
            f"{sorted(tolerances)} (fail-closed)"
        )
    averaged: Dict[str, Any] = {"tolerance_cents": next(iter(tolerances))}
    numeric_keys = (
        "raw_pitch_accuracy",
        "raw_chroma_accuracy",
        "octave_gap",
        "voicing_recall",
        "voicing_false_alarm",
        "overall_accuracy",
        "voiced_chroma_correct_frame_count",
    )
    for key in numeric_keys:
        values = [float(c["metrics"][key]) for c in clip_rows]
        averaged[key] = sum(values) / len(values)
    median_values = [
        float(c["metrics"]["median_cent_error"])
        for c in clip_rows
        if c["metrics"]["median_cent_error"] is not None
    ]
    averaged["median_cent_error"] = (
        sum(median_values) / len(median_values) if median_values else None
    )
    return averaged


def _measure_or_resume_external_clip_row(
    clip_id: str,
    entry: Dict[str, Any],
    *,
    manifest_dir: Path,
    fixtures: Dict[str, Any],
    tolerance_cents: float,
    est_voiced_floor: float,
    route: MelodyRoute,
    runner: RouteRunner,
    tmp_dir: Path,
    category: str,
    level: Optional[str],
    cell_store: Optional[Path],
    repeat_index: Optional[int],
    env_digest: Optional[str],
    workers: int,
    cells_resumed: List[str],
    cells_measured: List[str],
    cell_started_utc: List[str],
    cell_written_paths: List[str],
    cell_store_mismatches: "List[Dict[str, Any]]",
    store_role: str = _CELL_STORE_ROLE_RUN,
    record_est_trajectory: bool = False,
) -> Dict[str, Any]:
    """1 clip を測るか、設計 §8.7 のセル台帳から resume する。

    `cell_store` が `None` なら `_build_external_clip_row` へそのまま素通しする
    ——**挙動無変更の契約**（`--cell-store` 未指定時は既存 report に 1 バイトも
    フィールドが増えない）。
    """
    if cell_store is None:
        return _build_external_clip_row(
            clip_id,
            entry,
            manifest_dir=manifest_dir,
            fixtures=fixtures,
            tolerance_cents=tolerance_cents,
            est_voiced_floor=est_voiced_floor,
            route=route,
            runner=runner,
            tmp_dir=tmp_dir,
            record_est_trajectory=record_est_trajectory,
        )

    assert repeat_index is not None and env_digest is not None  # run_accuracy が保証済み

    # 設計 §8.7 の順序制約: 抽出器を走らせる**前**に入力 digest を確定させる
    # （resume 可否をここで判定するため）。ハッシュ計算ロジックは
    # `_read_external_clip_inputs` に集約し複製しない。
    inputs = _read_external_clip_inputs(
        clip_id, entry, manifest_dir=manifest_dir, fixtures=fixtures
    )
    record_path = _cell_store_record_path(
        cell_store, category=category, level=level, entry_id=clip_id, repeat_index=repeat_index
    )
    if record_path.is_file():
        record = _json_loads_no_dup_keys(
            record_path.read_bytes(), what=f"cell record {record_path}"
        )
        mismatches = _cell_record_mismatches(
            record,
            category=category,
            level=level,
            entry_id=clip_id,
            repeat_index=repeat_index,
            audio_sha256=inputs.audio_sha256,
            annotation_sha256=inputs.annotation_sha256,
            env_digest=env_digest,
            tolerance_cents=tolerance_cents,
            est_voiced_floor=est_voiced_floor,
            store_role=store_role,
        )
        if not mismatches:
            # 取得時刻を**セルと一緒に旅させる**。resume だけを見ている限り、run の
            # `started_utc` は「今回の起動時刻」なので、事前登録より前に測ったセルが
            # 後の run を経由して「登録後の測定」として attestation を通ってしまう。
            try:
                _parse_recorded_utc(
                    record.get("measurement_started_utc"),
                    where=f"cell record {record_path}",
                    field="measurement_started_utc",
                )
            except ValueError as exc:
                cell_store_mismatches.append(
                    {
                        "entry_id": clip_id,
                        "field": "measurement_started_utc",
                        "expected": "UTC timestamp",
                        "actual": f"{record.get('measurement_started_utc')!r} ({exc})",
                    }
                )
            else:
                cells_resumed.append(clip_id)
                cell_started_utc.append(str(record["measurement_started_utc"]))
                # 呼び出し元がこの dict を（`row.pop` 等で）変異させても、他セルの
                # record 内容へ波及しないよう deep copy を返す。
                return copy.deepcopy(record["clip_row"])
        else:
            cell_store_mismatches.extend(mismatches)

    # **抽出を始める前**に時刻を採る。完了時刻だと、登録前に始まった長いセルが
    # 抽出中に登録が commit されることで「登録より後に測った」を名乗れてしまう
    # （事前登録の順序検査は「測定の開始」を要求しており、完了ではない）。
    measurement_started_utc = _utc_now()
    started = time.monotonic()
    row = _build_external_clip_row(
        clip_id,
        entry,
        manifest_dir=manifest_dir,
        fixtures=fixtures,
        tolerance_cents=tolerance_cents,
        est_voiced_floor=est_voiced_floor,
        route=route,
        runner=runner,
        tmp_dir=tmp_dir,
        inputs=inputs,
        record_est_trajectory=record_est_trajectory,
    )
    elapsed_seconds = time.monotonic() - started
    cells_measured.append(clip_id)
    cell_started_utc.append(measurement_started_utc)
    if row.get("outcome") == "measured":
        # `outcome == "unavailable"`（抽出器スタック未導入）はセルレコードに残さない
        # ——設計 §8.6 の「打ち切ったら『未完』として記録する（失敗値を書かない）」
        # と同じ精神。次回実行時は provisioning が整っていれば普通に再測定される。
        record = {
            "schema_version": _EXPECTED_CELL_RECORD_SCHEMA,
            "category": category,
            "level": level,
            "entry_id": clip_id,
            "repeat_index": repeat_index,
            "audio_sha256": inputs.audio_sha256,
            "annotation_sha256": inputs.annotation_sha256,
            "env_digest": env_digest,
            # `env_digest` は third-party の版しか畳まない——**自前コードを変えても
            # 動かない**。生成器コード pin をセル同一性へ入れておかないと、抽出・
            # 前処理・採点を書き換えた次のセッションが旧セルを resume し、report は
            # 新しい `generator_code_sha256` を名乗る（= 古い測定を現行コードの結果と
            # して報告する）。evaluate の再測定まで検出が遅れるのも高くつく。
            "generator_code_sha256": _LOADED_GENERATOR_CODE_SHA256,
            # 採点に効く凍結値。bars を改訂して同じ store を再利用すると、旧 clip_row が
            # 新しい bars digest を纏って resume される（採点値は旧閾値のまま）。
            "tolerance_cents": tolerance_cents,
            "est_voiced_floor": est_voiced_floor,
            # このセルを産んだのは run か、evaluate の測り直しか（C2・PR #240）。
            "store_role": store_role,
            "clip_row": row,
            # このセルの**測定を開始した**時刻。resume するとき run 側の起動時刻では
            # 立証できない「登録より後に測り始めた」を、セル自身に持たせる。
            "measurement_started_utc": measurement_started_utc,
            "elapsed_seconds": elapsed_seconds,
            "workers": workers,
        }
        _cell_store_atomic_write_text(record_path, json.dumps(record, indent=2, sort_keys=True))
        cell_written_paths.append(str(record_path))
    return row


def _run_external_category(
    category: str,
    category_spec: Dict[str, str],
    *,
    external_manifest_path: Path,
    external_fixtures_path: Path,
    tolerance_cents: float,
    est_voiced_floor: float,
    route: MelodyRoute,
    runner: RouteRunner,
    tmp_dir: Path,
    level: Optional[str] = None,
    cell_store: Optional[Path] = None,
    repeat_index: Optional[int] = None,
    env_digest: Optional[str] = None,
    workers: int = 1,
    store_role: str = _CELL_STORE_ROLE_RUN,
) -> Dict[str, Any]:
    """カテゴリ V（外部素材）1 本の run report row を作る（設計 Memo M2c）。

    `cell_store` が与えられれば設計 §8.7 のセルチェックポイントを使う。row には
    内部専用キー `_cell_store_resumed` / `_cell_store_measured` /
    `_cell_store_mismatches` を積んで返す —— 呼び出し元 `run_accuracy` がこれを
    pop して run 全体の bookkeeping へ畳み込む（複数 external カテゴリを 1 run で
    測る場合の集約点はカテゴリ単位でなく run 単位のため）。`cell_store` が `None`
    ならこれらのキーは一切現れない（挙動無変更の契約）。
    """
    fixtures_doc, fixtures_sha256 = load_external_fixtures(external_fixtures_path)
    _require_external_fixtures_schema_for_category(
        fixtures_doc, category=category, where=f"run_accuracy: category {category!r}"
    )
    _require_external_fixtures_level_match(
        fixtures_doc, level=level, where=f"run_accuracy: category {category!r}"
    )
    fixtures = fixtures_doc["fixtures"]
    if not fixtures:
        raise ValueError(
            f"run_accuracy: category {category!r} を要求したが "
            f"{external_fixtures_path} の fixtures が空; 事前登録済み clip なしに "
            "外部素材カテゴリを測らない (fail-closed)"
        )

    entries, manifest_sha256, manifest_path = _load_external_manifest(external_manifest_path)
    manifest_dir = manifest_path.parent

    _require_exact_cohort_match(
        set(fixtures), {entry["id"] for entry in entries},
        where=f"run_accuracy: category {category!r}",
    )

    # 凍結コピーは **アーム（カテゴリ）ごとの専用ディレクトリ**へ置く。
    # `_build_external_clip_row` は `tmp_dir/<clip_id>.wav` を書いた直後に 0400 を
    # 立てるため、同一 run で 2 アーム（direct / stem）を測ると 2 本目の
    # `write_bytes` が **PermissionError で落ちる**（root では再現しない・Codex P1）。
    # 「消してから書き直す」ではなくパスを分けることで、衝突を構造的に消す
    # （凍結コピーが上書きされうる経路自体を作らない）。
    arm_tmp_dir = resolve_confined(f"arm-{category}", tmp_dir)
    arm_tmp_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    cells_resumed: List[str] = []
    cells_measured: List[str] = []
    cell_started_utc: List[str] = []
    cell_written_paths: List[str] = []
    cell_store_mismatches: "List[Dict[str, Any]]" = []
    # D-1(b): 軌跡 digest は M2e カテゴリの row にだけ刻む（既存 M2b/M2c 記録は
    # バイト不変）。
    record_est_trajectory = _category_records_est_trajectory(category)

    clip_rows: List[Dict[str, Any]] = []
    # **run phase の clip ループは逐次のまま据え置く**（設計判断 D-2）。並列化は
    # evaluate phase の検証プロセス側だけに入れる——run 側のスケーリングは r5 の
    # シャード地図（`m2e_r2_shard_map.yaml`）が担う設計であり、run の実行形態を
    # 変えると r4 で校正する `T_*` の意味が変わる。
    # `test_run_phase_clip_loop_stays_sequential_even_with_many_workers` が固定。
    for entry in sorted(entries, key=lambda e: e["id"]):
        clip_row = _measure_or_resume_external_clip_row(
            entry["id"],
            entry,
            manifest_dir=manifest_dir,
            fixtures=fixtures,
            tolerance_cents=tolerance_cents,
            est_voiced_floor=est_voiced_floor,
            route=route,
            runner=runner,
            tmp_dir=arm_tmp_dir,
            category=category,
            level=level,
            cell_store=cell_store,
            repeat_index=repeat_index,
            env_digest=env_digest,
            workers=workers,
            cells_resumed=cells_resumed,
            cells_measured=cells_measured,
            cell_started_utc=cell_started_utc,
            cell_written_paths=cell_written_paths,
            cell_store_mismatches=cell_store_mismatches,
            store_role=store_role,
            record_est_trajectory=record_est_trajectory,
        )
        clip_rows.append(clip_row)

    row: Dict[str, Any] = {
        "route": route.name,
        "extractor": route.extractor,
        "input_kind": category_spec["input_kind"],
        "external_manifest_sha256": manifest_sha256,
        "external_manifest_path_relative": _repo_relative_path(manifest_path),
        "external_fixtures_sha256": fixtures_sha256,
    }
    if cell_store is not None:
        row["_cell_store_resumed"] = cells_resumed
        row["_cell_store_measured"] = cells_measured
        row["_cell_store_started_utc"] = cell_started_utc
        row["_cell_store_written_paths"] = cell_written_paths
        row["_cell_store_mismatches"] = cell_store_mismatches

    unavailable = [c for c in clip_rows if c.get("outcome") == "unavailable"]
    if unavailable:
        # 1 clip でも抽出器スタック未導入で unavailable なら、カテゴリ全体を
        # unavailable として記録する（S カテゴリの route 単位 unavailable と対称）。
        row["outcome"] = "unavailable"
        row["detail"] = unavailable[0]["detail"]
        row["clips"] = clip_rows
        return row

    row["outcome"] = "measured"
    row["clips"] = clip_rows
    row["metrics"] = _average_external_clip_metrics(clip_rows)
    row["ref_frame_count"] = sum(c["ref_frame_count"] for c in clip_rows)
    row["ref_voiced_frame_count"] = sum(c["ref_voiced_frame_count"] for c in clip_rows)
    row["est_frame_count"] = sum(c["est_frame_count"] for c in clip_rows)
    row["est_voiced_frame_count"] = sum(c["est_voiced_frame_count"] for c in clip_rows)
    row["source_model"] = clip_rows[0]["source_model"]
    for key in ("provenance_extractor_weights_sha256", "provenance_extractor_code_sha256"):
        values = {c.get(key) for c in clip_rows}
        if len(values) > 1:
            raise RuntimeError(
                f"run_accuracy: category {category!r} の clips が {key} で不一致 "
                f"{sorted(v for v in values if v is not None)}; 同一 run 内で抽出器の "
                "重み/コードが変わった (fail-closed)"
            )
        row[key] = clip_rows[0].get(key)
    # D-1/D-2 (r7 blocker f06bbaa3): カテゴリ内の複数 clip が同じ分離器
    # スタック（重み/コード/version/model）で測られたことは要求するが、
    # per-clip 固有の `stem_sha256`（分離出力そのものの指紋。clip ごとの音声
    # 内容に依存し一致する理由がない）は allowlist で除外し、不変量側だけを
    # 完全同一要求の対象にする（`split_preprocessing_invariants`）。
    preprocessing_list = [c.get("provenance_preprocessing") for c in clip_rows]
    invariants_list = [split_preprocessing_invariants(p)[0] for p in preprocessing_list]
    # `provenance_preprocessing` 自体の有無（分離要否）の混在は従来どおり不一致として
    # 検出する——invariants は preprocessing が None でも {} に潰れて区別が付かなく
    # なるため、有無フラグを比較対象へ明示的に含める。
    signatures = {
        json.dumps({"has_preprocessing": p is not None, "invariants": inv}, sort_keys=True)
        for p, inv in zip(preprocessing_list, invariants_list)
    }
    if len(signatures) > 1:
        all_keys = sorted({key for inv in invariants_list for key in inv})
        broken_keys = [
            key
            for key in all_keys
            if len({json.dumps(inv.get(key), sort_keys=True) for inv in invariants_list}) > 1
        ]
        if len({p is not None for p in preprocessing_list}) > 1:
            broken_keys = ["<provenance_preprocessing の有無>"] + broken_keys
        raise RuntimeError(
            f"run_accuracy: category {category!r} の clips が provenance_preprocessing の "
            f"カテゴリ不変量で不一致 (割れたキー: {broken_keys}) (fail-closed)"
        )
    if preprocessing_list[0] is not None:
        row["provenance_preprocessing"] = invariants_list[0]
        # per-clip 量（stem_sha256）は不変量から外した代わりに、(clip 識別子,
        # stem_sha256) の束を clip 識別子で sort して digest 化し、カテゴリ行に
        # 残す——「どの stem を分離出力として測ったか」を捨てない（D-2）。
        # stem_sha256 を持つ clip が 1 件もなければ bundle は出さない
        # （V_direct 等の preprocessing なし経路の report 形を変えない）。
        stem_pairs: List[Tuple[str, str]] = []
        for c in clip_rows:
            _, per_clip = split_preprocessing_invariants(c.get("provenance_preprocessing"))
            stem = per_clip.get("stem_sha256")
            if stem is not None:
                stem_pairs.append((c["clip_id"], stem))
        if stem_pairs:
            stem_pairs.sort(key=lambda pair: pair[0])
            row["stem_sha256_bundle"] = hashlib.sha256(
                json.dumps(stem_pairs, sort_keys=True).encode("utf-8")
            ).hexdigest()
    return row


def _runtime_package_names() -> "Tuple[str, ...]":
    """本ハーネスの route が推論で実行しうる third-party パッケージ名（+ スコアラー）。

    手書きリストでは実行スタックの一部（`tensorflow` / `keras` / `hmmlearn` /
    `librosa` / `resampy` / numba backends / 分離器の `torch` 等）を必ず取りこぼす
    （Codex P1）。リポジトリは `melody/provenance` にその完全集合を既に持っている
    ——抽出器側は `_EXTRACTOR_CODE_PACKAGES`（`extractor_code_packages_for` 経由）、
    分離器側は `SEPARATION_CODE_PACKAGES`——ので、**実際に選ばれる route から
    登録表を引いて導出**する。抽出器名も分離要否も `_CATEGORY_SPECS` の route
    そのものから読むため、カテゴリを増やしてもこの集合が置き去りにならない。

    third-party は `generator_code_sha256`（first-party 閉包）の対象外だが、事前
    ロード済みなら「メモリ上の旧実装が推論し、row の code pin は新しいディスクを
    指す」窓が開くため、監視対象に含める。

    スコアラー閉包（`_SCORER_RUNTIME_PACKAGES`）は route の抽出器選択に関係なく
    **常に**監視対象へ加える——scipy は `pyin` route の `_EXTRACTOR_CODE_PACKAGES`
    にしか登録が無く、選ばれた route が `crepe_direct` / `demucs_vocals_then_crepe`
    等の非 pyin 経路だと抽出器由来の監視集合には scipy が入らない。しかしスコアラー
    自身（mir_eval）は route の選択に関わらず必ず scipy/numpy を実行するので、
    抽出器登録表への偶然の相乗りに頼らず、スコアラー由来として独立に含める。
    """
    from svp_rpe.melody.provenance import (
        SEPARATION_CODE_PACKAGES,
        extractor_code_packages_for,
    )

    names = set(_SCORER_RUNTIME_PACKAGES)
    for category, category_spec in _CATEGORY_SPECS.items():
        route = _select_named_route(category_spec["input_kind"], category_spec["route_name"])
        packages = extractor_code_packages_for(route.extractor)
        if not packages:
            raise RuntimeError(
                f"category {category!r} の抽出器 {route.extractor!r} が "
                "melody/provenance._EXTRACTOR_CODE_PACKAGES に未登録; 監視集合を手書きで "
                "補うと実行スタックを取りこぼすため fail-closed"
            )
        names.update(packages)
        if route.requires_separation:
            if not SEPARATION_CODE_PACKAGES:
                raise RuntimeError(
                    "melody/provenance.SEPARATION_CODE_PACKAGES が空; 分離経路の実行 "
                    "スタックを監視できないため fail-closed"
                )
            names.update(SEPARATION_CODE_PACKAGES)
    return tuple(sorted(names))


# 事前ロード一覧の確定（判定基準の `_SYS_MODULES_AT_LOAD` はモジュール先頭で凍結済み
# なので、監視集合の導出に登録表の import を要するこの位置で評価しても値は変わらない）。
_PRELOADED_SEED_MODULES = _preloaded_seed_modules()

# H7: 実行時数値構成として記録する環境変数の接頭辞。BLAS/LAPACK 実装（OpenBLAS/MKL/
# GotoBLAS 系譜）のスレッド分割・CPU ターゲット選択・numpy の CPU 機能無効化・MKL の
# 数値再現モードは、いずれも**バイト列は完全に不変のまま**縮約順序を変え、結果の
# 数値を変える（実測: `median_cent_error` が 1.352838 ↔ 1.353400 でバッチ間往復した
# 事故が実在する。本環境は `sched_getaffinity` = 4、これらの env は未設定）。
# **拡張（セルフレビュー第二弾 H15）**: `VECLIB_`（macOS Accelerate）/ `BLIS_` /
# `NUMEXPR_` を接頭辞へ追加。
_NUMERIC_RUNTIME_ENV_PREFIXES: "Tuple[str, ...]" = (
    "OPENBLAS_",
    "OMP_",
    "MKL_",
    "NPY_",
    "GOTO_",
    "VECLIB_",
    "BLIS_",
    "NUMEXPR_",
)

# 接頭辞ではなく完全一致で記録する env（H15）: `GLIBC_TUNABLES`（glibc の SIMD
# memcpy 選択等、`x86_64=hwcaps` 等の tunable 文字列を介して実行時分岐に影響）・
# `LD_HWCAP_MASK`（BLAS の実行時 core 検出・ローダの hwcap 判定に影響しうる）。
_NUMERIC_RUNTIME_ENV_EXACT_NAMES: "Tuple[str, ...]" = (
    "GLIBC_TUNABLES",
    "LD_HWCAP_MASK",
)


def _numpy_simd_dispatch_info() -> "Optional[Dict[str, Any]]":
    """numpy が実際に dispatch した SIMD 拡張命令集合（セルフレビュー第二弾 H15）。

    同一 env・同一 `numeric_runtime_config` の記録内容でも、実行した CPU が
    違えば別の SIMD カーネル（別の縮約順序）が dispatch され、結果の数値が
    揺れうる——観測済みの `median_cent_error` 双安定(1.352838 ↔ 1.353400) の
    原因帰属に使える記録を残す（record-completeness が主眼、gate ではない）。
    `np.show_runtime()` 相当のロジックを直接展開する（`show_runtime()` 自体は
    `pprint` で標準出力へ印字するだけで値を返さないため、内部で読む
    `__cpu_baseline__`/`__cpu_dispatch__`/`__cpu_features__` を直接読む）。
    """
    try:
        from numpy._core._multiarray_umath import (
            __cpu_baseline__,
            __cpu_dispatch__,
            __cpu_features__,
        )
    except ImportError:
        try:
            from numpy.core._multiarray_umath import (  # numpy < 2.0 系のパス
                __cpu_baseline__,
                __cpu_dispatch__,
                __cpu_features__,
            )
        except ImportError:
            return None
    found = sorted(f for f in __cpu_dispatch__ if __cpu_features__.get(f))
    not_found = sorted(f for f in __cpu_dispatch__ if not __cpu_features__.get(f))
    return {"baseline": sorted(__cpu_baseline__), "found": found, "not_found": not_found}


def _threadpool_runtime_info() -> "Optional[List[Dict[str, Any]]]":
    """threadpoolctl 経由の実行時スレッドプール構成（セルフレビュー第二弾 H15）。

    `threadpoolctl.threadpool_limits()` による**実行時**のスレッド数変更は
    env には現れない——`threadpoolctl` 自体は任意閉包メンバー
    （`_SCORER_RUNTIME_PACKAGES_OPTIONAL`）なので、未導入環境では `None`
    （absent 相当）を返す——H(11B) の optional 閉包記録と同じ二値の考え方。
    """
    try:
        from threadpoolctl import threadpool_info
    except ImportError:
        return None
    try:
        return threadpool_info()
    except Exception:
        return None


def _numeric_runtime_config() -> Dict[str, Any]:
    """実行時数値構成（H7、第二弾 H15 で拡張）: スレッド数・CPU 機能・env を記録する（バーには使わない）。

    `_repeats_bit_identical`（後述）の bit 一致は「同一バッチ・同一環境・同一
    スレッド数」でしか成立しない条件付きの性質であって、この構成が変われば
    縮約順序が変わり数値が変わりうる——**バイト列は完全に不変のまま**。measured
    子プロセスは親の環境をそのまま継承する（`os.environ` 経由）ため、環境起因の
    数値差は親子間では原理的に検出できない（必ず同条件になる）。ここでは
    repeats 間（同一バッチ内の独立 run 間）の**同質性**だけを要求する
    （`_require_homogeneous_scorer` と同じ形）。バッチをまたぐ構成の違いまでは
    覆わない——それは verdict の attestation に正直に書く（H7 の限界）。

    **拡張（H15）**: 記録に `numpy_simd_dispatch`（`_numpy_simd_dispatch_info`）と
    `threadpool_info`（`_threadpool_runtime_info`）を追加する。gate（同質性要求）
    ではなく record-completeness が主眼——双安定の原因帰属を可能にする。

    **呼び出しタイミング（Codex 16 巡目 P2-B）**: `run_accuracy` は本関数を
    **category loop（scoring）完了後・かつ `_require_unchanged_since_load()`
    （post-run スコアラー pin 再計算）の後**に呼ぶ——2 つの理由がある。(1) loop 前に
    呼ぶと、`evaluate_melody_accuracy` 内の遅延 `mir_eval.melody` import がロード
    する scipy backend・それが追加する BLAS スレッドプールが `threadpool_info()`
    の記録に反映されない（scoring が実際に生んだ数値バックエンド構成を clean
    report が代表しない）。(2) `_require_unchanged_since_load()` より前に呼ぶと、
    本関数自身の `_threadpool_runtime_info()` 呼び出しが（scoring がまだ import
    していなければ）threadpoolctl を新規 import してしまい、その import が
    `_scorer_optional_participated`（15 巡目 P2、素の `sys.modules` メンバシップ
    判定）による post-run pin 再計算に「scoring 自身の participate」として混入する
    （Codex 16 巡目 P2-A の症状。`_scorer_optional_participated` の docstring
    参照）。両方とも「本関数を scoring 完了後・pin 確定後に呼ぶ」という単一の
    呼び出し順序で同時に塞がる。
    """
    env: Dict[str, Optional[str]] = {}
    for key in sorted(os.environ):
        if key.startswith(_NUMERIC_RUNTIME_ENV_PREFIXES) or key in _NUMERIC_RUNTIME_ENV_EXACT_NAMES:
            env[key] = os.environ[key]
    try:
        affinity_count: Optional[int] = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        affinity_count = None  # 非 Linux 等 sched_getaffinity 非対応環境
    return {
        "env": env,
        "cpu_count": os.cpu_count(),
        "sched_affinity_count": affinity_count,
        "numpy_simd_dispatch": _numpy_simd_dispatch_info(),
        "threadpool_info": _threadpool_runtime_info(),
    }


def _execution_paths() -> Dict[str, Any]:
    """実行形態・解釈系パス（H9）を記録する（検証は記録のみ・fail-closed 対象外）。

    `sys.path` / `PYTHONPATH` / `LD_LIBRARY_PATH` / `sys.executable` は差し替えれば
    import 解決先を丸ごと変えられるが、拒否まではやり過ぎ（`PYTHONPATH` 依存の
    正当な運用がある）——**記録のみ**で verdict の attestation から読めるようにする。
    測り直し子プロセスは `env = dict(os.environ)` で親の環境をそのまま継承するため、
    親子間でこれらの値が食い違うことは無い（＝この記録は「継承前提」で、子プロセス
    側の独立検証ではない）。
    """
    return {
        "sys_path": list(sys.path),
        "PYTHONPATH": os.environ.get("PYTHONPATH"),
        "LD_LIBRARY_PATH": os.environ.get("LD_LIBRARY_PATH"),
        "sys_executable": sys.executable,
    }


def _env_digest_package_versions() -> Dict[str, str]:
    """route の実行スタック全体の配布バージョンを引く（未導入は明示マーカー）。

    手書きの `_ENV_DIGEST_PACKAGES` だけでは実行スタックを取りこぼす——`tensorflow` /
    `keras` / `resampy` / `hmmlearn` / `soxr` / `numba` / `llvmlite` が動いても
    digest は変わらず、旧構成のセルが新構成を名乗る report の下で resume される。
    リポジトリは `_runtime_package_names()` に**登録表から導いた完全集合**を既に
    持っている（同じ取りこぼしを実行 pin 側で一度直している）ので、そちらと合流させる。
    手書きリストは床として残す（登録表に無い `demucs`/`crepe` 等を落とさないため）。
    """
    import importlib.metadata

    versions: Dict[str, str] = {}
    for name in sorted(set(_ENV_DIGEST_PACKAGES) | set(_runtime_package_names())):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = _ENV_DIGEST_ABSENT_MARKER
    return versions


def _env_digest_demucs_weights() -> Dict[str, Any]:
    """demucs 重み digest（設計 §8.7）。`resolve_separation_weights` を再利用する。

    未導入/未取得は `LearnedModelUnavailable` で解決失敗するので、それを拾って
    「解決できなかった事実」を記録する（例外を上へ伝播させて run 全体を落とさない）。
    理由文字列を含めるため、解決できた場合と解決できない場合とで payload の形が
    構造的に異なり、`env_digest` は必ず変わる。
    """
    try:
        from svp_rpe.rpe.learned.source_separation_adapter import (
            resolve_separation_weights,
        )

        weights = resolve_separation_weights()
    except Exception as exc:  # LearnedModelUnavailable 含む fail-closed でない記録
        return {"resolved": False, "reason": f"{type(exc).__name__}: {exc}"}
    return {"resolved": True, "sha256": weights.sha256}


def _env_digest_crepe_weights() -> Dict[str, Any]:
    """crepe 重み digest（設計 §8.7）。推論は行わず artifact 解決のみ

    （`extractor_weights_fingerprint` は解決失敗を `None` に畳んで返す非送出 API
    — `melody/provenance.py` docstring 参照）。これにより「セルを resume すべきか」
    を抽出器を実際に走らせずに判定できる（§8.7 の順序制約）。
    """
    try:
        from svp_rpe.melody.provenance import extractor_weights_fingerprint

        fingerprint = extractor_weights_fingerprint("crepe")
    except Exception as exc:  # import 失敗等も同じ形で記録する
        return {"resolved": False, "reason": f"{type(exc).__name__}: {exc}"}
    if fingerprint is None:
        return {"resolved": False, "reason": "crepe weights not provisioned"}
    return {"resolved": True, "sha256": fingerprint.sha256}


def _env_digest_thread_settings() -> Dict[str, Any]:
    """スレッド設定（設計 §8.7・§8.3 の並列不変性前提）。"""
    settings: Dict[str, Any] = {
        "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS", _ENV_DIGEST_ABSENT_MARKER),
        "MKL_NUM_THREADS": os.environ.get("MKL_NUM_THREADS", _ENV_DIGEST_ABSENT_MARKER),
    }
    try:
        import torch

        settings["torch_num_threads"] = torch.get_num_threads()
    except Exception:
        settings["torch_num_threads"] = _ENV_DIGEST_ABSENT_MARKER
    return settings


def _apply_thread_pinning() -> Dict[str, Any]:
    """スレッド 3 点固定（`OMP_NUM_THREADS` / `MKL_NUM_THREADS` / `torch.set_num_threads`）。

    HANDOFF §3.1 の実測: 前 2 点だけでは足りない。3 点目を欠くと demucs の vocals
    stem の `stem_sha256` が run 間で変わり（`residual_db` は 1e-6 dB で安定するのに
    bytes は変わる）、`_row_model_stack_signature` が per-row の `stem_sha256` /
    `stem_sha256_bundle` を run 間決定論の証拠として署名に含めるため（r7 blocker
    修正 f06bbaa3 + Codex #254 是正）、この非決定性は「別 model stack」として
    fail-closed に顕在化する。run 内の複数 clip 集約だけが stem を不変量要求から
    除外する（`_run_external_category`）。

    設計判断 D-3: **固定は run と evaluate で同一でなければならない。** 検証の子だけを
    固定すると、固定していない run が産んだ row と bit 一致しなくなる——publish 条件は
    「独立に測り直して bit 一致」なので、ここが割れると r6 が丸ごと通らない。

    本関数は**実際に測るプロセス**（run phase と、`--pin-threads` を受けた測り直しの子）
    だけが呼ぶ適用器である。評価器プロセスは何も測らないので呼ばない——代わりに
    `_thread_pinning_contract_from_reports` が評価対象 report から契約を導き、
    `_run_external_verification_in_fresh_process` が子へ伝えて子 report の申告と
    再照合する（詳細は前者の docstring）。

    env は**設定せず検査する**。OpenMP / MKL のスレッド数はランタイムのロード時に
    確定するため、プロセス開始後に `os.environ` を書いても効かない——「設定した」と
    report に書きながら実際には未固定で測る、という最悪の形になる。
    """
    unpinned = [
        f"{name}={os.environ.get(name)!r}"
        for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS")
        if os.environ.get(name) != "1"
    ]
    if unpinned:
        raise SystemExit(
            "--pin-threads: プロセス環境の " + " / ".join(unpinned) + ' が "1" でない; '
            "OpenMP/MKL のスレッド数はランタイムのロード時に確定するため、プロセス開始"
            "後に設定しても効かない（固定したと名乗りながら未固定で測ることになる）。"
            "`OMP_NUM_THREADS=1 MKL_NUM_THREADS=1` を設定して起動し直すこと (fail-closed)"
        )
    pinning: Dict[str, Any] = {"OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
    try:
        import torch
    except Exception:
        # torch 非導入の環境（direct アームだけを回す構成・素の CI）では 3 点目は
        # 存在しない。黙って省かず明示マーカーを残す（`_env_digest_*` と同じ流儀）。
        pinning["torch_num_threads"] = _ENV_DIGEST_ABSENT_MARKER
        return pinning
    torch.set_num_threads(1)
    observed = torch.get_num_threads()
    if observed != 1:
        raise SystemExit(
            f"--pin-threads: torch.set_num_threads(1) 後も torch.get_num_threads() が "
            f"{observed!r}; 固定が効いていない環境で stem の bit 一致を要求しても "
            "publish 条件が満たせない (fail-closed)"
        )
    pinning["torch_num_threads"] = observed
    return pinning


def _runtime_code_package_names() -> "Tuple[str, ...]":
    """`env_digest` が実装 hash を採る対象（束縛時と post-run 再検証で同一集合を使う）。"""
    return tuple(sorted(set(_ENV_DIGEST_PACKAGES) | set(_runtime_package_names())))


@functools.lru_cache(maxsize=1)
def _env_digest_runtime_code() -> "Tuple[str, Tuple[str, ...]]":
    """route の実行スタックの**実装 hash**（版文字列だけでは足りない）。

    `importlib.metadata.version()` は、パッケージが版を据え置いたまま in-place で
    patch/rebuild された場合に動かない——`env_digest` が同じままなので
    `_cell_record_mismatches` は旧実装で採ったセルを新ランタイムの下で resume する。
    リポジトリは実装 hash を既に持っている（`provenance.packages_code_sha256`。
    実行 pin 側が使っているのと同じ関数）ので、それを畳む。

    **hash できなければ送出をそのまま伝播させる（fail-closed）。** 失敗を理由文字列へ
    畳むと、その後にそのパッケージが in-place で patch されても理由は同じ文字列のまま
    ——`env_digest` が動かず、旧実装のセルが resume され、別実装で走った 2 本の M2e
    report が合算される（塞ごうとしている退行そのもの）。重みの未解決
    （`_env_digest_demucs_weights`）とは違い、**導入済みで hash できないパッケージは
    実際に測定を実行する**ので、「解決できなかった事実」を環境同一性として再利用しない。

    プロセス内で 1 度だけ計算する（初回 ~9 秒）。プロセス内でのコード差し替えは
    `_require_unchanged_since_load()` の post-run 再計算が別途捕まえる。
    """
    _bind_runtime_code_pins(_runtime_code_package_names())
    if not _LOADED_RUNTIME_CODE_PINS:
        raise RuntimeError(
            f"実行スタック {_runtime_code_package_names()} のうち 1 つも実装 hash を "
            "採れなかった; 何も覆っていない pin を環境同一性として使わない (fail-closed)"
        )
    return _fold_runtime_code_pins(), tuple(sorted(_LOADED_RUNTIME_CODE_PINS))


def _bind_runtime_code_pins(names: "Tuple[str, ...]") -> None:
    """`names` のコード hash を**まだ束縛していないものだけ**確定させる。

    `soundfile` のように本モジュールが module scope で import するものは、既に
    import 直前で束縛済み（`_LOADED_RUNTIME_CODE_PINS` の初期値）。残りは
    crepe / torch / demucs / librosa 等の遅延 import で、初回の `env_digest` 計算が
    import より前に走るためここで束縛できる。
    """
    for name in names:
        if name in _LOADED_RUNTIME_CODE_PINS:
            continue
        _state, digest = _package_code_state_for_bind(name)
        if digest is not None:
            _LOADED_RUNTIME_CODE_PINS[name] = digest


def _fold_runtime_code_pins() -> str:
    """束縛済みコード pin を 1 本の digest へ畳む（名前込みで順序非依存）。"""
    folded = hashlib.sha256()
    for name, digest in sorted(_LOADED_RUNTIME_CODE_PINS.items()):
        folded.update(name.encode("utf-8"))
        folded.update(b"\0")
        folded.update(digest.encode("ascii"))
        folded.update(b"\0")
    return folded.hexdigest()


def _env_digest_dist_native() -> "Tuple[Tuple[str, str], ...]":
    """**束縛時点**の同梱ネイティブ pin（`_LOADED_DIST_NATIVE_PINS`）を返す。

    ディスクを読み直さない——import 後に実体が差し替わると、プロセスは dlopen 済みの
    旧実装で推論を続けたまま新しい bytes の digest を名乗る（`env_digest` が
    「走っていない実装」を指す）。束縛は `_bind_dist_native_pins` が import より前に
    行い、drift は `_require_dist_native_unchanged_since_bind()` が post-run に
    uncached で検出する。
    """
    return tuple(sorted(_LOADED_DIST_NATIVE_PINS.items()))


def _bind_dist_native_pins(names: "Tuple[str, ...]") -> None:
    """`names` の同梱ネイティブ pin を**まだ束縛していないものだけ**確定させる。

    呼び出しは 2 段階（どちらも import より前に置く）:

    1. `import soundfile` の直前 — 本モジュールが module scope で import する
       third-party のうち、scorer 機構（`_scorer_pins` が numpy/scipy を束縛済み）が
       覆っていないもの。libsndfile はここで pin しないと「import 後に束縛」になる。
    2. 定義が出揃った後（`_runtime_package_names` が使えるようになった時点）— 残りの
       推論スタック。crepe/torch/demucs/librosa はすべて関数内 import なので、この
       時点はまだ import 前である。

    未導入は skip（実行されない）。導入済みで pin を採れない場合は送出を伝播させる。
    """
    import importlib.util

    for name in names:
        if name in _LOADED_DIST_NATIVE_PINS:
            continue
        try:
            spec = importlib.util.find_spec(name)
        except Exception:
            spec = None
        if spec is None:
            continue    # 未導入 = 実行されない
        _LOADED_DIST_NATIVE_PINS[name] = _scorer_dist_native_sha256(
            name, verify_pre_bind_gates=False
        )


def _quarantine_cell_records(paths: "List[str]") -> None:
    """この run が書いたセルレコードを resume されない名前へ退避する。

    削除しない——「何が起きたか」を後から読めるようにするため（`.quarantined-*` は
    `_cell_store_record_path` が生成する名前と一致しないので resume 経路には乗らない）。
    退避自体が失敗しても元の例外を握り潰さない（best-effort・失敗は stderr へ）。
    """
    for path in paths:
        record = Path(path)
        try:
            if record.is_file():
                record.rename(record.with_suffix(f".json.quarantined-{uuid.uuid4().hex[:8]}"))
        except OSError as exc:   # 退避不能でも元の fail-closed を優先する
            print(f"warning: セルレコードの隔離に失敗 {record}: {exc}", file=sys.stderr)


def _require_dist_native_unchanged_since_bind() -> None:
    """束縛済み同梱ネイティブが run 中に差し替わっていないことを要求する（post-run）。

    scorer 側の `_require_scorer_native_unchanged_since_bind` と同じ役割を、
    非 scorer パッケージ（soundfile/librosa/soxr…）にも与える。**uncached で読み直す**
    ——束縛値との純粋なディスク比較であり、memoize すると差し替えを見逃す。
    """
    for name, expected in sorted(_LOADED_DIST_NATIVE_PINS.items()):
        current = _scorer_dist_native_sha256(
            name, use_cache=False, verify_pre_bind_gates=False
        )
        if current != expected:
            raise RuntimeError(
                f"run_accuracy: {name!r} の同梱ネイティブ実体が束縛時点の pin "
                f"（{expected!r}）と不一致（現在 {current!r}）; dlopen 済みの旧実装で "
                "測った row に新しい実体の digest を名乗らせない (fail-closed)"
            )


def _bind_all_dist_native_pins() -> "Tuple[Tuple[str, str], ...]":
    """推論スタック全体の同梱ネイティブ（パッケージ本体ディレクトリ **外**）を束縛する。

    `packages_code_sha256` はパッケージ root 配下しか覆わないので、`numpy.libs/` /
    `scipy.libs/` に置かれる OpenBLAS/LAPACK は**実装 hash に入らない**。配布版を
    据え置いたまま BLAS バイナリが差し替え/再ビルドされると `env_digest` が動かず、
    旧バックエンドで計算したセルが新バックエンドの run で resume される。ハーネスは
    この pin を既に持っている（`_scorer_dist_native_sha256`。実行 pin 側と同じ関数）。

    `verify_pre_bind_gates=False` で呼ぶ: pre-bind ゲート（`/proc/self/maps` 全域
    スキャン）は**初回束縛専用**で、ここで再実行すると無関係な JIT マッピングを
    「束縛前の先読み」と誤認して over-strict に落ちる（`_require_scorer_native_
    unchanged_since_bind` が mid-run で同じ理由により無効化しているのと同じ）。

    未導入は skip（実行されない）。**導入済みで pin を採れない場合は送出を伝播**
    させる——失敗を安定した文字列へ畳むと、その後の差し替えでも digest が動かない。
    """
    _bind_dist_native_pins(
        tuple(sorted(set(_ENV_DIGEST_PACKAGES) | set(_runtime_package_names())))
    )
    return _env_digest_dist_native()


def _require_runtime_code_unchanged_since_bind() -> None:
    """実装 hash が束縛（初回計算）時から変わっていないことを要求する（post-run）。

    `_env_digest_runtime_code()` は memoize されるので、**crepe / tensorflow /
    librosa のような遅延 import のパッケージ**が「digest 計算後・import 前」に
    差し替えられると、推論は新しい bytes を実行するのに digest は旧 bytes を指す。
    `_require_unchanged_since_load()` は first-party と scorer しか、
    `_require_dist_native_unchanged_since_bind()` は本体ディレクトリ外のネイティブしか
    見ないため、この窓はどちらでも覆われない。**memoize を迂回して読み直す。**
    """
    from svp_rpe.melody.provenance import package_code_state

    for name, expected in sorted(_LOADED_RUNTIME_CODE_PINS.items()):
        _state, current = package_code_state(name, use_cache=False)
        if current != expected:
            raise RuntimeError(
                f"run_accuracy: {name!r} の実装 hash が束縛時点の pin（{expected!r}）と "
                f"不一致（現在 {current!r}）; 走ったコードと digest が食い違うセルを "
                "保存・resume・合算させない (fail-closed)"
            )


def _env_digest_numeric_runtime() -> Dict[str, Any]:
    """数値結果に効く実行時構成（`_numeric_runtime_config` と同じ env 集合）。

    `_env_digest_thread_settings` は `OMP_/MKL_NUM_THREADS` しか見ていなかったが、
    `OPENBLAS_NUM_THREADS` / `OPENBLAS_CORETYPE` / `NPY_DISABLE_CPU_FEATURES` /
    `MKL_CBWR` などは **バイト列を一切変えないまま縮約順序を変え、数値を変える**
    ——本リポジトリはその実測事故（`median_cent_error` の 1.352838 ↔ 1.353400
    双安定）を `_NUMERIC_RUNTIME_ENV_PREFIXES` のコメントに記録している。畳まなければ
    構成変更後のセッションが旧構成のセルを resume し、report は新構成を名乗る。

    **`threadpool_info` は意図的に含めない。** `env_digest` は category loop の**前**に
    評価されるため、ここで `_threadpool_runtime_info()` を呼ぶと threadpoolctl を新規
    import してしまい、`_scorer_optional_participated` による post-run pin 再計算へ
    「scoring 自身の participate」として混入する（`_numeric_runtime_config` docstring
    の Codex 16 巡目 P2-A の症状そのもの）。BLAS 実装の入れ替えは `packages` の版と
    ここの env で大半が動くが、**版も env も同じまま実装だけ差し替える経路は
    env_digest では捕まらない**——宣言された穴として残す（記録は
    `numeric_runtime_config` に残るので、事後の原因帰属はできる）。
    """
    env: Dict[str, Optional[str]] = {}
    for key in sorted(os.environ):
        if key.startswith(_NUMERIC_RUNTIME_ENV_PREFIXES) or key in _NUMERIC_RUNTIME_ENV_EXACT_NAMES:
            env[key] = os.environ[key]
    return {
        "env": env,
        # numpy が実際に dispatch した SIMD 集合（`NPY_DISABLE_CPU_FEATURES` は
        # cpuinfo の flags を変えないまま dispatch を変えるため、CPU 同一性だけでは
        # 足りない）。numpy は既に import 済みなので新規 import は起こさない。
        "numpy_simd_dispatch": _numpy_simd_dispatch_info(),
    }


def _env_digest_cpu_identity() -> Dict[str, Any]:
    """CPU 同一性（設計 §8.9.3・rev.6 — `env_digest` に含める）。

    2.2 倍のインスタンス間分散は**スケジューリングの問題として現れたが、再現性の
    問題でもある**。版とスレッド設定だけを畳んだ digest では、命令セットの異なる
    CPU（AVX2 / AVX-512 等）で走った 2 つのセルが同一の `env_digest` を持ちうる。
    数値経路が分岐してもそれを検出する手段が無く、**合算の可否判定そのものが壊れる**。

    畳むもの: CPU モデル名と命令セットフラグ。フラグは**ソート済みの完全集合**を使う
    （抜粋にすると、抜粋対象外のフラグが変わったときに digest が動かない）。
    読めない環境では明示マーカーを入れ、黙って省かない。
    """
    identity: Dict[str, Any] = {
        "model_name": _ENV_DIGEST_ABSENT_MARKER,
        "flags": _ENV_DIGEST_ABSENT_MARKER,
        "logical_cpus": _ENV_DIGEST_ABSENT_MARKER,
        "platform_machine": platform.machine() or _ENV_DIGEST_ABSENT_MARKER,
    }
    try:
        identity["logical_cpus"] = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        pass
    try:
        raw = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return identity
    for line in raw.splitlines():
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if key == "model name" and identity["model_name"] == _ENV_DIGEST_ABSENT_MARKER:
            identity["model_name"] = value
        elif key == "flags" and identity["flags"] == _ENV_DIGEST_ABSENT_MARKER:
            # 完全集合をソートして畳む（順序は BIOS/カーネルで揺れうるため）。
            identity["flags"] = sorted(value.split())
    return identity


def _env_digest() -> str:
    """設計 §8.7 の `env_digest`: 環境同一性を畳んだ 64-hex sha256。

    折り込む要素: Python 版・{torch, demucs, crepe, librosa, soundfile, numpy} の
    版・demucs 重み digest・crepe 重み digest・スレッド設定
    （`OMP_NUM_THREADS`/`MKL_NUM_THREADS`/`torch.get_num_threads()`）・
    **CPU 同一性（モデル名 + 命令セットフラグ。rev.6 §8.9.3）**。

    **重い import はしない**（呼び出し元が `--cell-store` 使用時にのみ本関数を呼ぶ
    ことで、モジュール import 時の重い依存 import を避ける契約 — 各ヘルパーは
    torch/demucs/crepe を関数内 import で遅延させる）。未導入パッケージ・未解決の
    重みは明示マーカーで記録し、黙って省かない（§8.7 実装ノート）。
    """
    # 束縛は**ここで**行う（module import 時ではない）。素の run（`env_digest` を
    # 名乗らない M2a/M2c）は本関数を呼ばないので、選択されていない optional
    # パッケージ（TensorFlow/Keras/Demucs 等）の配置不良で import 中に落ちることが
    # 無くなる。crepe / torch / demucs / librosa はすべて関数内 import で、本関数は
    # category loop の**前**に呼ばれるため「import 前束縛」は保たれる。
    #
    # 対象集合は**選択カテゴリで絞らない**——絞ると同じ環境でもカテゴリ選択が違う run
    # 同士で `env_digest` が変わり、セルの合算可否判定が壊れる（環境同一性は run の
    # 引数ではなく環境の性質でなければならない）。
    _bind_all_dist_native_pins()
    _runtime_code = _env_digest_runtime_code()
    payload: Dict[str, Any] = {
        "python_version": sys.version,
        "packages": _env_digest_package_versions(),
        "demucs_weights": _env_digest_demucs_weights(),
        "crepe_weights": _env_digest_crepe_weights(),
        "thread_settings": _env_digest_thread_settings(),
        # 数値に効く実行時構成（BLAS スレッド/CPU ターゲット/SIMD 無効化/再現モード）。
        "numeric_runtime": _env_digest_numeric_runtime(),
        # 実行スタックの**実装 hash**。版据え置きの in-place patch は版文字列では
        # 捕まらず、旧実装で採ったセルが新ランタイムの下で resume される。
        "runtime_code": {"sha256": _runtime_code[0], "covered": list(_runtime_code[1])},
        # 本体ディレクトリ外の同梱ネイティブ（`numpy.libs/` の OpenBLAS 等）。
        # 版据え置きの BLAS 差し替えは `runtime_code` では捕まらない。
        "dist_native": dict(_env_digest_dist_native()),
        # rev.6 §8.9.3: CPU を畳まない env_digest は「合算してよいか」の判定として
        # 壊れている。命令セットが違えば数値経路が分かれうるため。
        "cpu_identity": _env_digest_cpu_identity(),
    }
    canonical = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _cell_store_record_path(
    cell_store: Path,
    *,
    category: str,
    level: Optional[str],
    entry_id: str,
    repeat_index: int,
) -> Path:
    """セル鍵 `(category, level, entry_id, repeat_index)` からレコードパスを導く。

    ファイル名は生の `entry_id`（manifest 由来のユーザ供給文字列）から直接組み立て
    ない —— sha256 digest だけをファイル名にすることで、衝突・パス脱出の懸念を
    構造的に消す（`_require_safe_external_id` の字句検証済みとはいえ二重防御）。

    **これは同時に「別 repeat の記録を誤って再生できない」ことの実装でもある**
    （設計 §8.7 の指示: if 分岐による回避ではなく、鍵→パスの写像自体が
    `repeat_index` / `level` / `category` の 1 つでも違えば別ファイルになることを
    保証する。呼び出し元がこの写像を経由する限り、異なる repeat の記録を同じパスで
    読み書きすることは構造的に起こり得ない）。
    """
    key_json = json.dumps(
        {
            "category": category,
            "level": level,
            "entry_id": entry_id,
            "repeat_index": repeat_index,
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(key_json.encode("utf-8")).hexdigest()
    return Path(cell_store) / f"cell_{digest}.json"


def _cell_record_mismatches(
    record: Any,
    *,
    category: str,
    level: Optional[str],
    entry_id: str,
    repeat_index: int,
    audio_sha256: str,
    annotation_sha256: str,
    env_digest: str,
    tolerance_cents: float,
    est_voiced_floor: float,
    store_role: str,
) -> "List[Dict[str, Any]]":
    """既存セルレコードと現在の入力/環境の不一致を列挙する（設計 §8.7 再開規則）。

    空リストなら resume 可（=スキップ）。1 件でもあれば **スキップしない** ——
    再測定したうえで不一致の事実だけを report の `cell_store_mismatches` へ記録する
    （バーを緩めず「見なかったことにしない」という本リポジトリ全体の fail-closed
    規律をそのまま適用する）。

    鍵フィールド（category/level/entry_id/repeat_index）も比較対象に含める。通常
    この関数へ来るのは `_cell_store_record_path` が鍵の sha256 から一意に決めた
    ファイルを読んだ後なので鍵は一致しているはずだが、レコードが手で書き換え/破損
    したケースを黙って resume しないための多重防御（§8.7「複数環境のセルを1つの
    帯として合算することは禁止」と同じ精神）。
    """
    if not isinstance(record, dict):
        raise ValueError(f"cell record が JSON object でない (fail-closed): {record!r}")
    current: Dict[str, Any] = {
        # レコード形式そのものの discriminator。同一性フィールドが全部揃っていても、
        # 版が違えば「別の意味論で書かれた測定」を現行の解釈で読むことになる。
        # 版の無い旧レコードは `None` として不一致になる——それが正しい。
        "schema_version": _EXPECTED_CELL_RECORD_SCHEMA,
        "category": category,
        "level": level,
        "entry_id": entry_id,
        "repeat_index": repeat_index,
        "audio_sha256": audio_sha256,
        "annotation_sha256": annotation_sha256,
        "env_digest": env_digest,
        # 生成器コードが変われば同一セルでも別の測定である（`env_digest` は
        # third-party の版しか見ないため、自前コードの変更を捕まえられない）。
        # 旧世代のレコードにはこのキーが無く `None` として不一致になる——それが
        # 正しい: 素性の分からないセルを黙って resume しない。
        "generator_code_sha256": _LOADED_GENERATOR_CODE_SHA256,
        # 採点に効く凍結値（bars 改訂で動く）。`bars_sha256` は row へ後から刻まれる
        # ので、閾値が変わったセルを resume すると**旧採点値に新 bars の pin が付く**。
        "tolerance_cents": tolerance_cents,
        "est_voiced_floor": est_voiced_floor,
        # C2（PR #240 Codex P1）: **パスの分離は計算の独立を意味しない。**
        # `store_A` を別ディレクトリへコピーすれば `--eval-cell-store` の経路検査は
        # 通り、コピーされたレコードは上の同一性フィールドを**全部**満たす（鍵・入力・
        # 環境・生成器 digest がそのまま一致する。とくに `repeat_index` は run の
        # 0..n-1 と測り直しの 0..repeats-1 が正面から衝突する）。結果、検証の子が run の
        # セルを resume し、publish が再び自己比較になる。役割をレコード自身に束縛して、
        # evaluate のキャッシュで run 由来のレコードを resume させない。
        "store_role": store_role,
    }
    mismatches: "List[Dict[str, Any]]" = []
    for field, expected in current.items():
        actual = record.get(field)
        if actual != expected:
            mismatches.append(
                {"entry_id": entry_id, "field": field, "expected": expected, "actual": actual}
            )
    return mismatches


# ---------------------------------------------------------------------------
# run phase
# ---------------------------------------------------------------------------


def _annotate_row_bars_pin(
    row: Dict[str, Any],
    category: str,
    *,
    level: Optional[str],
    bars_path: Path,
    bars_sha256: str,
    extra_bars: Dict[str, Tuple["BarsArtifact", str, Path]],
) -> None:
    """row へ「どの bars ファイルの下で測ったか」の pin を刻む（設計 §5.2）。

    bars ファイルを分離した以上、report の top-level `bars_sha256`（= 共有スカラーを
    供給する `m2_accuracy_bars.yaml`）だけでは、M2e カテゴリがどの世代のバーの下で
    測られたかを表現できない。カテゴリ単位で**相対パスと sha256 の両方**を記録する。

    水準軸を持つカテゴリには `level` も刻む——「何を測ったか」の宣言は row 側に
    無ければ、後から別水準の row と混ぜたことを検出できない。
    """
    bars_file = _CATEGORY_SPECS[category]["bars_file"]
    if bars_file in extra_bars:
        _artifact, artifact_sha256, artifact_path = extra_bars[bars_file]
        row["bars_file"] = bars_file
        row["bars_file_relative"] = _repo_relative_path(artifact_path)
        row["bars_file_sha256"] = artifact_sha256
    else:
        row["bars_file"] = bars_file
        row["bars_file_relative"] = _repo_relative_path(bars_path)
        row["bars_file_sha256"] = bars_sha256
    if category in _REQUIRED_CONDITION_KEYS_BY_CATEGORY:
        row["level"] = level
        row["ladder_index"] = _m2e_ladder_index(level) if level is not None else None


def run_accuracy(
    *,
    categories: "tuple[str, ...]" = ("S_direct", "S_fullstack"),
    route_runner: Optional[RouteRunner] = None,
    specs_path: Path = SPECS_PATH,
    bars_path: Path = BARS_PATH,
    m2e_bars_path: Path = M2E_BARS_PATH,
    level: Optional[str] = None,
    tolerance_cents: Optional[float] = None,
    external_manifest_path: Optional[Path] = None,
    external_fixtures_path: Path = EXTERNAL_FIXTURES_PATH,
    cell_store: Optional[Path] = None,
    repeat_index: Optional[int] = None,
    workers: int = 1,
    thread_pinning: Optional[Dict[str, Any]] = None,
    cell_store_role: str = _CELL_STORE_ROLE_RUN,
) -> Dict[str, Any]:
    """カテゴリ S（合成正解つき）+ カテゴリ V（外部素材、M2c）の精度 run を実行し report dict を返す。

    `route_runner` は抽出器非依存インターフェース: ``(audio_path, route) ->
    (MelodyObservation, provenance_dict)``。既定は
    `svp_rpe.melody.extractors.observe_via_route_with_provenance`（実抽出器。
    crepe 未導入なら `LearnedModelUnavailable` を投げ、その route は
    ``outcome="unavailable"`` として記録される・実行時 DL 禁止・fail-closed）。
    テストはこれをフェイク抽出器（決定論の f0 を返す）に差し替えて run/evaluate
    の二相メカニズムだけを検証する。

    未知の `categories` 値は `_CATEGORY_SPECS` に無ければ fail-fast。`categories` に
    `kind: "external"` のカテゴリ（M2c 現在は V_direct のみ）が含まれる場合、
    `external_manifest_path` の指定が必須（未指定は fail-closed）。

    `cell_store`（設計 §8.7・opt-in）: 与えると外部素材カテゴリの各 clip を
    1 セル = `(category, level, entry_id, repeat_index)` として
    `_cell_store` 配下へ atomic write でチェックポイントし、既存レコードが
    入力/環境 digest と一致すれば再測定をスキップする（詳細は
    `_measure_or_resume_external_clip_row` / `_cell_record_mismatches`）。
    **既定 `None` では report に新フィールドが一切増えない**——`--cell-store`
    未使用の既存呼び出しはこの実装が入る前と bit 一致の report を返す契約。
    `repeat_index` は `cell_store` 指定時のみ必須（未指定・負値は fail-closed）。

    `workers` は **run phase では宣言値（記録専用）**である（設計判断 D-2 の
    非対称: evaluate phase では `evaluate_m2_bars` の実効並列度になる）。run の clip
    ループは逐次のまま据え置く——run 側のスケーリングは r5 のシャード地図が担う設計で、
    実行形態を変えると r4 で校正する `T_*` の意味が変わるため。セルレコードへは
    設計 §8.3 の cost 再現性のために記録する。

    `thread_pinning`（設計判断 D-3・`_apply_thread_pinning` の戻り値）を渡すと
    `results["thread_pinning"]` として report に刻む。evaluate 側はこの申告を
    **測り直しの契約として検証**し、子へ同じ条件を伝える（run と evaluate でスレッド
    条件が食い違うと bit 一致が壊れる）。**既定 `None` では report に新フィールドが
    一切増えない。**
    """
    if cell_store is not None and repeat_index is None:
        raise ValueError(
            "run_accuracy: cell_store が指定されたが repeat_index が無い; セル鍵 "
            "(category, level, entry_id, repeat_index) の repeat_index を欠いたまま "
            "チェックポイントを書かない (fail-closed)"
        )
    if cell_store is None and repeat_index is not None:
        raise ValueError(
            "run_accuracy: repeat_index が指定されたが cell_store が無い; セル "
            "チェックポイントを使わない run に測っていない次元を持たせない "
            "(fail-closed)"
        )
    if repeat_index is not None and repeat_index < 0:
        raise ValueError(
            f"run_accuracy: repeat_index {repeat_index!r} が負; セル鍵の "
            "repeat_index は 0 以上の整数を要求する (fail-closed)"
        )

    bind_inference_code_pins()
    # 注入された runner は正解 F0 と「それらしい」hash を自由に返せる（テストの
    # フェイク抽出器がまさにそれ）。実抽出器を一切走らせずにバーを満たす row を
    # 作れてしまうので、report 自身に注入の事実を刻み、evaluate はそれを
    # 「publish 不可」として弾く（Codex P1）。
    runner_injected = route_runner is not None
    runner: RouteRunner = route_runner or observe_via_route_with_provenance

    specs, specs_sha256 = load_specs(specs_path)
    bars, bars_sha256 = load_bars(bars_path)
    _require_specs_pin(specs_sha256, bars)

    if not categories:
        # 空選択を許すと CREPE を一度も呼ばずに `route_runner_injected=False` かつ
        # provenance 完備の「測定ゼロ report」が作れ、evaluate の全関所を素通りして
        # `categories={}` の schema-valid な verdict が publish できてしまう（Codex P2）。
        raise ValueError(
            "run_accuracy: categories が空; 少なくとも 1 つの登録済みカテゴリを測定"
            "しない report は publishable な実測記録になれない (fail-closed)"
        )
    unknown = [c for c in categories if c not in _CATEGORY_SPECS]
    if unknown:
        raise ValueError(f"unknown accuracy categories: {unknown}; expected one of {list(_CATEGORY_SPECS)}")

    external_categories = [c for c in categories if _CATEGORY_SPECS[c]["kind"] == "external"]
    if external_categories and external_manifest_path is None:
        raise ValueError(
            f"run_accuracy: category(s) {external_categories} require external_manifest_path "
            "(CLI: --external-manifest); 外部素材カテゴリを manifest なしに測らない "
            "(fail-closed)"
        )

    # M2e（設計 §6.2）: `level` は**カテゴリではなく run の次元**である。
    # `_CATEGORY_SPECS` には持ち込まず、run report の各 row と cat_result に記録する。
    bars_files_needed = sorted({_CATEGORY_SPECS[c]["bars_file"] for c in categories})
    extra_bars: Dict[str, Tuple["BarsArtifact", str, Path]] = {}
    for bars_file in bars_files_needed:
        if bars_file == "m2_accuracy_bars.yaml":
            continue  # 共有スカラーの供給元として上で既にロード済み
        if bars_file != "m2e_accuracy_bars.yaml":
            raise RuntimeError(
                f"run_accuracy: 未対応の bars_file {bars_file!r} (fail-closed)"
            )
        artifact, artifact_sha256 = load_bars(m2e_bars_path)
        if bars_file_identity(artifact.data) != bars_file:
            raise ValueError(
                f"run_accuracy: --m2e-bars {m2e_bars_path} が {bars_file!r} の "
                "schema_version を名乗っていない (fail-closed)"
            )
        extra_bars[bars_file] = (artifact, artifact_sha256, Path(m2e_bars_path))

    level_categories = [
        c for c in categories if c in _REQUIRED_CONDITION_KEYS_BY_CATEGORY
    ]
    if level_categories:
        if level is None:
            raise ValueError(
                f"run_accuracy: category(s) {level_categories} は水準軸を持つため level の "
                "指定が必須（CLI: --level）; どの水準を測ったか不明な row を作らない "
                "(fail-closed)"
            )
        for category in level_categories:
            artifact = extra_bars[_CATEGORY_SPECS[category]["bars_file"]][0]
            conditions_key = _BARS_FILES[_CATEGORY_SPECS[category]["bars_file"]][
                "conditions_key"
            ]
            declared_levels = artifact.data[conditions_key][category]["levels"]
            if level not in declared_levels:
                raise ValueError(
                    f"run_accuracy: level {level!r} が category {category!r} の事前登録 "
                    f"levels {declared_levels!r} にない (fail-closed)"
                )
    elif level is not None:
        raise ValueError(
            f"run_accuracy: level {level!r} が指定されたが、水準軸を持つカテゴリを 1 つも "
            "測っていない; 測っていない次元を report に名乗らせない (fail-closed)"
        )

    effective_tolerance = (
        tolerance_cents
        if tolerance_cents is not None
        else float(bars["m2_accuracy_bars"].get("tolerance_cents", DEFAULT_TOLERANCE_CENTS))
    )
    # 有声判定閾値は override を持たない（凍結バーからのみ読む）。`load_bars` が
    # 存在と定義域を検査済み。
    est_voiced_floor = float(bars["m2_accuracy_bars"]["est_voiced_confidence_floor"])

    # ロード時に確定した digest を使う（実行中にディスクのソースが変わっても、
    # 実際に走っているのは import 済みのコードなので、そちらを pin する）。
    results: Dict[str, Any] = {
        "schema_version": _EXPECTED_REPORT_SCHEMA,
        "mode": "synthetic_accuracy",
        "started_utc": _utc_now(),
        "run_id": uuid.uuid4().hex,
        "bars_sha256": bars_sha256,
        "specs_sha256": specs_sha256,
        "specs_path_relative": _repo_relative_path(specs_path),
        "bars_path_relative": _repo_relative_path(bars_path),
        # M2e §6.2: run の水準次元（水準軸を持つカテゴリを測らない run では None）。
        "level": level,
        "tolerance_cents": effective_tolerance,
        "est_voiced_confidence_floor": est_voiced_floor,
        "generator_code_sha256": _LOADED_GENERATOR_CODE_SHA256,
        "route_runner_injected": runner_injected,
        "preloaded_seed_modules": list(_PRELOADED_SEED_MODULES),
        "harness_loaded_as_main": _HARNESS_LOADED_AS_MAIN,
        "pre_bound_scorer_native_mappings": list(_PRE_BOUND_SCORER_NATIVE_MAPPINGS),
        "non_standard_import_hooks": list(_NON_STANDARD_IMPORT_HOOKS),
        # `numeric_runtime_config` はここでは確定させない（Codex 16 巡目 P2-B）:
        # category loop（scoring）完了後に一度だけ計算し、下で `results` へ書く。
        # loop 前に計算すると `evaluate_melody_accuracy` の遅延 mir_eval.melody
        # import がロードする scipy backend / 追加スレッドプールを記録が反映
        # できない（docstring 参照）。
        "sys_flags_optimize": sys.flags.optimize,
        "execution_paths": _execution_paths(),
        "categories": {},
    }
    results.update(_LOADED_SCORER_PINS)

    # 設計 §8.7: `env_digest` は `--cell-store` 使用時に計算する（`cell_store is None`
    # の通常 run では呼ばない —— 重い optional import を避ける契約・挙動無変更の
    # 契約の両方を満たす）。
    #
    # **例外: M2e カテゴリを含む run では常に計算する**（Codex 21 巡目 P2）。帯は
    # 「環境を跨いでセルを合算しない」を前提にしており（§8.7）、evaluate は report
    # の `env_digest` でしかそれを検査できない。`--cell-store` 未使用の 2 本を別環境
    # （別 CPU・別パッチ版）で採ると、どちらも digest を持たないまま「同じ測定の
    # 反復」として通ってしまう。M2e にとって環境は run の次元なので、記録は
    # チェックポイント機構の有無と独立に必要である。
    effective_cell_store = Path(cell_store) if cell_store is not None else None
    m2e_in_run = any(
        _CATEGORY_SPECS[cat]["bars_file"] == "m2e_accuracy_bars.yaml" for cat in categories
    )
    env_digest_value: Optional[str] = (
        _env_digest() if (effective_cell_store is not None or m2e_in_run) else None
    )
    run_cells_resumed: List[str] = []
    run_cells_measured: List[str] = []
    run_cell_started_utc: List[str] = []
    run_cell_written_paths: List[str] = []
    run_cell_store_mismatches: "List[Dict[str, Any]]" = []

    with tempfile.TemporaryDirectory(prefix="melody-accuracy-") as tmp:
        for category in categories:
            category_spec = _CATEGORY_SPECS[category]

            if category_spec["kind"] == "external":
                # M2c: 外部素材カテゴリ（合成でなく `--external-manifest` が指す
                # 実ファイル）。S カテゴリの specs 由来波形合成とは完全に別経路。
                route = _select_named_route(
                    category_spec["input_kind"], category_spec["route_name"]
                )
                assert external_manifest_path is not None  # 上で fail-closed 済み
                row = _run_external_category(
                    category,
                    category_spec,
                    external_manifest_path=external_manifest_path,
                    external_fixtures_path=external_fixtures_path,
                    tolerance_cents=effective_tolerance,
                    est_voiced_floor=est_voiced_floor,
                    route=route,
                    runner=runner,
                    tmp_dir=Path(tmp),
                    level=level,
                    cell_store=effective_cell_store,
                    repeat_index=repeat_index,
                    env_digest=env_digest_value,
                    workers=workers,
                    store_role=cell_store_role,
                )
                if effective_cell_store is not None:
                    # `_run_external_category` は run 単位の bookkeeping をカテゴリ
                    # row の内部専用キーとして積んで返す（複数 external カテゴリを
                    # 1 run で測る場合の集約点は categories dict でなく run 単位の
                    # ため）。ここで pop して run 側リストへ畳み込み、
                    # `results["categories"][category]` には残さない。
                    run_cells_resumed.extend(row.pop("_cell_store_resumed"))
                    run_cells_measured.extend(row.pop("_cell_store_measured"))
                    run_cell_started_utc.extend(row.pop("_cell_store_started_utc"))
                    run_cell_written_paths.extend(row.pop("_cell_store_written_paths"))
                    run_cell_store_mismatches.extend(row.pop("_cell_store_mismatches"))
                _annotate_row_bars_pin(
                    row,
                    category,
                    level=level,
                    bars_path=bars_path,
                    bars_sha256=bars_sha256,
                    extra_bars=extra_bars,
                )
                results["categories"][category] = row
                if row.get("outcome") == "measured":
                    _require_scorer_native_unchanged_since_bind()
                continue

            y, sr, waveform_sha256 = _build_category_waveform(category, category_spec, specs, bars)
            wav_path = Path(tmp) / f"{category}.wav"
            wav_path.write_bytes(_serialize_wav_float32(y, sr))
            # hash・デコードの前にファイル自体を read-only にする。ディレクトリ 0500 は
            # rename/unlink を塞ぐが、**既存 inode への in-place 上書き**は owner-writable
            # (0644) のファイルなら rename も chmod も要さず、pre-hash 後に書き換え →
            # デコーダに消費させ → post-hash 前に復元、が fd hash / inode 検査を素通り
            # する（Codex P2 第 39 巡）。0400 なら明示 chmod なしに書けない——明示
            # chmod まで行う同権限者はプロセスメモリも書ける = 既定の境界外。
            os.chmod(wav_path, 0o400)
            # `waveform_sha256` は合成直後の in-memory 配列 `y` の pin だが、抽出器が
            # 実際に消費するのは直列化された WAV。直列化/デコードの欠陥や並行差し替えで
            # 両者が乖離すると「測っていない bytes の正解」に対する採点を受理してしまう
            # （Codex P2）。書き出した WAV を読み戻して pin と bit 一致することを要求し、
            # さらにファイル bytes の digest を取り、抽出後に不変を再確認する。
            readback_y, readback_sr = sf.read(wav_path, dtype="float32")
            if readback_sr != sr or _waveform_sha256(readback_y) != waveform_sha256:
                raise RuntimeError(
                    f"category {category!r}: 直列化した WAV の読み戻しが pin した波形と "
                    f"一致しない（sr {readback_sr} vs {sr} / sha256 "
                    f"{_waveform_sha256(readback_y)} vs {waveform_sha256}）; 抽出器が "
                    "消費する bytes を pin が記述していない (fail-closed)"
                )
            # pre/post hash は **保持した fd（= inode）** から読む。path の再 read だと
            # 「digest 後に別ファイルへ atomic rename → デコーダに消費させ → 元へ戻す」
            # replace-and-restore の窓で pre/post とも一致してしまう（Codex P2）。
            # 併せて抽出中は temp ディレクトリを 0o500 にし、同 uid プロセスでも明示的な
            # chmod なしには rename/unlink できないようにする（明示 chmod まで行う
            # 同権限攻撃者はプロセスメモリも書ける = preload ゲート群と同じ境界外）。
            wav_fd = os.open(wav_path, os.O_RDONLY)
            try:
                input_wav_sha256 = _sha256_of_fd(wav_fd)
                fd_stat = os.fstat(wav_fd)
                path_stat = os.stat(wav_path)
                if (fd_stat.st_dev, fd_stat.st_ino) != (path_stat.st_dev, path_stat.st_ino):
                    raise RuntimeError(
                        f"category {category!r}: 入力 WAV の path が hash した inode を指して "
                        "いない (fail-closed)"
                    )
                total_duration_sec = float(len(y)) / float(sr)
                ref_times, ref_freqs = _reference_for_category(
                    category_spec, specs, total_duration_sec=total_duration_sec
                )
                route = _select_named_route(
                    category_spec["input_kind"], category_spec["route_name"]
                )

                row: Dict[str, Any] = {
                    "route": route.name,
                    "extractor": route.extractor,
                    "input_kind": category_spec["input_kind"],
                    "waveform_sha256": waveform_sha256,
                    "input_wav_sha256": input_wav_sha256,
                    "ref_frame_count": len(ref_times),
                    "ref_voiced_frame_count": sum(1 for f in ref_freqs if f > 0.0),
                }
                _annotate_row_bars_pin(
                    row,
                    category,
                    level=level,
                    bars_path=bars_path,
                    bars_sha256=bars_sha256,
                    extra_bars=extra_bars,
                )
                os.chmod(tmp, 0o500)
                try:
                    try:
                        observation, route_provenance = runner(str(wav_path), route)
                    except LearnedModelUnavailable as exc:
                        row["outcome"] = "unavailable"
                        row["detail"] = str(exc).splitlines()[0]
                        results["categories"][category] = row
                        continue
                finally:
                    os.chmod(tmp, 0o700)
                # post: 同じ inode の bytes が不変（in-place 改変の検出）かつ path が
                # 今も同じ inode を指す（rename 差し替えの検出）ことを要求する。
                wav_after_sha256 = _sha256_of_fd(wav_fd)
                if wav_after_sha256 != input_wav_sha256:
                    raise RuntimeError(
                        f"category {category!r}: 抽出中に入力 WAV が差し替えられた "
                        f"（{input_wav_sha256} → {wav_after_sha256}）; 測っていない bytes の "
                        "正解に対する採点を publish しない (fail-closed)"
                    )
                path_stat_after = os.stat(wav_path)
                if (fd_stat.st_dev, fd_stat.st_ino) != (
                    path_stat_after.st_dev,
                    path_stat_after.st_ino,
                ):
                    raise RuntimeError(
                        f"category {category!r}: 抽出中に入力 WAV の path が別 inode へ "
                        "差し替えられた; 測っていない bytes の正解に対する採点を publish "
                        "しない (fail-closed)"
                    )
            finally:
                os.close(wav_fd)

            # 抽出器の frame_hz をそのまま渡さない: CREPE は無声フレームでも最尤 F0 を
            # 正値で返すため、confidence を mir_eval の符号規約へ変換してから採点する
            # （`_est_freqs_with_voicing`・Codex P1）。
            est_freqs = _est_freqs_with_voicing(
                observation, confidence_floor=est_voiced_floor
            )
            metrics: MelodyAccuracyResult = evaluate_melody_accuracy(
                ref_times,
                ref_freqs,
                observation.frame_times,
                est_freqs,
                tolerance_cents=effective_tolerance,
            )
            row["outcome"] = "measured"
            row["metrics"] = metrics.to_dict()
            row["est_frame_count"] = len(est_freqs)
            row["est_voiced_frame_count"] = sum(1 for f in est_freqs if f > 0.0)
            row["source_model"] = observation.source_model
            for key, value in route_provenance.items():
                row[f"provenance_{key}"] = value
            results["categories"][category] = row
            # H14（セルフレビュー第二弾）: 各カテゴリの処理直後にも scorer native の
            # 即時再検証を挟む——scipy/mir_eval は `evaluate_melody_accuracy` 内で
            # 遅延 import されるため、最初のカテゴリ処理が終わった時点で import が
            # 完了している。以降のカテゴリでも繰り返し検証することで、run 全体を
            # 通じた離散的なチェックポイントを増やす（境界は docstring 参照）。
            _require_scorer_native_unchanged_since_bind()

    # 任意閉包メンバーの presence を「評価器プロセスと同じ観測条件」で確定させる
    # （PR #225 CI 実測の回帰修正）。`threadpoolctl` は `import mir_eval.melody` の
    # 連鎖（scipy 経由）で**実際にロードされる**正当な閉包メンバーだが、その import が
    # 起きるのは `evaluate_melody_accuracy` が呼ばれた時——つまり measured な行を
    # 1 つも持たない run（全カテゴリが unavailable/skip 等）では、この時点でも
    # 未 import のままになりうる。評価器側は `_require_homogeneous_scorer` /
    # `_require_fresh_process_report_provenance` で必ず
    # `_ensure_scorer_optional_closure_observed()` を通してから再計算するため、
    # run 側がこれを通さないと「子プロセス=absent / 評価環境=present」で恒常的に
    # 食い違い fail-closed する（CI test 3.11 で実測・
    # `test_reverification_refuses_when_stack_cannot_rerun` が別経路の失敗として顕在化）。
    # 両者を同じ前提へ揃えるため、pin を再計算する直前にここでも観測を確定させる。
    _ensure_scorer_optional_closure_observed()
    # 実行中にディスク上の first-party ソースが差し替わっていないか確認する。
    # 差し替わっていれば「report が pin した digest」と「次回 import されるコード」が
    # 食い違い、後続の evaluate が誤った provenance を受理しうる（Codex P1）。
    _post_run_scorer_pins = _require_unchanged_since_load()
    # 同じ規律を非 scorer パッケージの同梱ネイティブにも課す（uncached でディスクを
    # 読み直し、束縛時点の pin と比較する）。`env_digest` は束縛値を名乗るので、
    # run 中に差し替えられていればここで落とす。
    # `env_digest` を束縛した run（`--cell-store` / M2e）だけに課す。素の run は
    # `env_digest_value is None` で環境同一性を名乗らない契約であり、そこで高価な
    # 全ツリー走査を 2 本足しても drift 保護にならない上、hash 不能な optional
    # パッケージがあると従来通っていた M2a/M2c run を新たに落とす。
    if env_digest_value is not None:
        try:
            _require_dist_native_unchanged_since_bind()
            # 実装 hash も読み直す——遅延 import のパッケージは「束縛後・import 前」に
            # 差し替えられうる（上 2 つの検査はどちらもその窓を覆わない）。
            _require_runtime_code_unchanged_since_bind()
        except RuntimeError:
            # **この run が書いたセルを隔離してから落とす。** 検査は run の最後にしか
            # 走らないので（毎セルで全ツリーを再走査するのは 80 セル × 数秒で非現実的）、
            # 落ちた時点で既に書かれたセルがディスクに残る。実装を元へ戻せば次の run は
            # 同じ `env_digest` を計算し、**差し替え中の実装が産んだ row を resume して
            # しまう**。resume されない名前へ退避し、証拠としては残す。
            _quarantine_cell_records(run_cell_written_paths)
            raise
    # Codex 16 巡目 P2-B: `numeric_runtime_config` は category loop（scoring）完了後・
    # かつ上の `_require_unchanged_since_load()`（post-run スコアラー pin 再計算）の
    # **後**にここで確定させる（`_numeric_runtime_config` / `_scorer_optional_
    # participated` の docstring 参照）。この呼び出し順序により、本関数自身の
    # threadpoolctl import（scoring 自身がまだ import していなければ新規に起こる）は
    # 上で既に確定済みの任意閉包メンバー presence（`_post_run_scorer_pins` 由来）へ
    # 影響しない（Codex 16 巡目 P2-A の症状もこの順序で同時に塞がる）。
    results["numeric_runtime_config"] = _numeric_runtime_config()
    # Codex 15 巡目 P2: `results` は構築時点（ループ開始前・`_LOADED_SCORER_PINS`）の
    # スコアラー pin で初期化されているため、任意閉包メンバー（threadpoolctl/
    # charset_normalizer）は load 時点で必ず absent の暫定値のまま残っている
    # （load 時は `import numpy` 等より前なので、何も import されていない）。この
    # run が実際に測定した（＝上のループが `evaluate_melody_accuracy` 経由で
    # mir_eval.melody を import した）後に再計算した `_post_run_scorer_pins` の
    # 任意メンバー分だけをここで上書きし、observed import closure を最終値として
    # report に刻む。必須メンバー（mir_eval/scipy/numpy/decorator）は
    # `_require_unchanged_since_load` が load 時と不変であることを既に検証済みなので
    # 上書き不要（load-time-bound の値を保つ——ディスクが run 中に改変されても、
    # 実際に実行されたのは import 済みの旧コードである、という #217 の規律を
    # required メンバーについては維持する）。
    for _optional_name in _SCORER_RUNTIME_PACKAGES_OPTIONAL:
        for _suffix in ("_version", "_code_sha256", "_dist_native_sha256", "_closure_state"):
            _key = f"{_optional_name}{_suffix}"
            results[_key] = _post_run_scorer_pins[_key]
    # P1-B（Codex 10 巡目、11 巡目 P1-A で検出経路を是正）: audit hook が run 中に
    # 記録した「compile された source bytes が束縛時点の期待と不一致」の一覧を
    # report に刻む。この run の
    # 実行中に発生した compile イベントすべてを反映するため、ここ（実際の
    # extraction ループが終わった後）で読む——`results` 構築時点（ループ開始前）
    # で読むと、ループ中の compile イベントを一切反映できず常に空になってしまう。
    results["scorer_load_time_hash_mismatches"] = list(_SCORER_LOAD_TIME_HASH_MISMATCHES)
    # H16（セルフレビュー第二弾）: 「実際に compile を観測した」集合と「今 import
    # 済みの scorer .py」の期待集合を両方とも report に刻む——観測ゼロ（H13 症状）と
    # 無改変を区別するための coverage 検証に使う。
    results["scorer_compile_observed_paths"] = sorted(_SCORER_COMPILE_OBSERVED_PATHS)
    results["scorer_compile_expected_paths"] = _scorer_compile_expected_paths()
    # 設計 §8.7: `--cell-store` 使用時のみ report へ追加フィールドを積む。
    # `cell_store is None` の run にはこのブロックの副作用が一切無いため、
    # 既存呼び出しは 1 バイトも変わらない report を返す（挙動無変更の契約）。
    if effective_cell_store is not None:
        results["cell_store_relative"] = _repo_relative_path(effective_cell_store)
        results["repeat_index"] = repeat_index
        results["workers"] = workers
        results["cells_resumed"] = run_cells_resumed
        results["cells_measured"] = run_cells_measured
        results["cell_store_mismatches"] = run_cell_store_mismatches
        # この report の数値を産んだセルの**最も古い測定開始時刻**。resume したセルは
        # 今回の `started_utc` より前に測り始められているので、事前登録の順序検査は
        # こちらを見なければ「登録前の測定」を後の run で洗浄できてしまう。
        # 文字列の辞書順でなく**時刻としての最小**を採る（`+00:00` と `Z` のように
        # 同じ時刻が別表記になりうる形式で辞書順比較すると順序を取り違える）。
        if run_cell_started_utc:
            results["earliest_cell_started_utc"] = min(
                run_cell_started_utc,
                key=lambda value: _parse_recorded_utc(
                    value, where="cell record", field="measurement_started_utc"
                ),
            )
    # 設計判断 D-3: スレッド固定は run と evaluate で同一でなければならない。
    # `--pin-threads` を使った run はその事実を report に刻み、evaluate はこの申告を
    # 契約として検証する（`_thread_pinning_contract_from_reports`）。
    # `thread_pinning is None`（既定）の run は 1 バイトも変わらない。
    if thread_pinning is not None:
        results["thread_pinning"] = thread_pinning
    # M2e run では `--cell-store` の有無に依らず記録する（上の算出コメント参照）。
    if env_digest_value is not None:
        results["env_digest"] = env_digest_value
    results["recorded_utc"] = _utc_now()
    return results


# ---------------------------------------------------------------------------
# evaluate phase
# ---------------------------------------------------------------------------


def _evaluator_code_sha256() -> str:
    """verdict を解釈するコード（本モジュール + accuracy.py）の digest。"""
    return _generator_code_sha256()


def _row_model_stack_signature(row: Dict[str, Any]) -> Tuple[Any, ...]:
    """measured row の model stack 署名（`run_melody_observability._route_provenance` と同型）。

    n>=2 の repeats を「同一実行スタック下の再現」と数える前に、抽出器・分離器の
    model stack が同一であることを証明するために使う。同一 package version でも
    別 bundled/local weights や patch 済みコードなら、別 stack の run であって
    repeats ではない（#59/#217 と同じ規律）。
    """
    # D-4 (r7 blocker f06bbaa3) + Codex #254 指摘の是正: ここで比較される rows は
    # 「同一測定の run 間比較（submitted vs 検証 run / repeats）」であり、1 つの
    # run 内の複数 clip 集約（`_run_external_category`。そこでは stem 除外が正しい）
    # とは文脈が異なる。同じ clip / 同じ clip 束を同じ分離器で分離し直した stem
    # bytes は決定論で一致すべきなので、per-row の `stem_sha256`（S_fullstack 等の
    # 1 row = 1 clip 行）と `stem_sha256_bundle`（集約行。全 clip の stem 束 digest）
    # は run 間決定論の証拠として署名に含める——metrics の一致だけでは stem bytes の
    # 非決定性（`_apply_thread_pinning` が実測した型）が量子化で消えて偽の決定論
    # success を publish しうるため。
    preprocessing = row.get("provenance_preprocessing")
    if isinstance(preprocessing, dict):
        invariants, per_clip = split_preprocessing_invariants(preprocessing)
        separation: Tuple[Any, ...] = (
            invariants.get("preprocessing"),
            invariants.get("separation_model"),
            invariants.get("separation_version"),
            invariants.get("separation_weights_sha256"),
            invariants.get("separation_code_sha256"),
            per_clip.get("stem_sha256"),
            row.get("stem_sha256_bundle"),
        )
    else:
        separation = (preprocessing, None, None, None, None, None, row.get("stem_sha256_bundle"))
    return (
        row.get("source_model"),
        row.get("provenance_extractor_version"),
        row.get("provenance_extractor_weights_sha256"),
        row.get("provenance_extractor_code_sha256"),
        separation,
    )


def _require_homogeneous_model_stack(category: str, rows: List[Dict[str, Any]]) -> Tuple[Any, ...]:
    """measured rows が完全な provenance pin を持ち、repeats 間で同一 stack か検証する。

    設計 §4 の `repeats_min` は「同じ実行スタックで測り直しても同じ結論になる」ことの
    証拠なので、別の CREPE 重み・別ビルドの推論コードで測った 2 本を repeats として
    数えると、その pass は再現性を示していない（Codex P1 指摘）。`generator_code_sha256`
    はハーネス自身の digest なので、下流の学習モデル stack の差はそこに現れない
    ——ゆえに row が emit した抽出器/分離器の pin を別途突き合わせる。
    """
    for idx, row in enumerate(rows):
        for key in ("provenance_extractor_weights_sha256", "provenance_extractor_code_sha256"):
            value = row.get(key)
            if not _is_sha256(value):
                # 非空文字列で足りるとすると `"TBD"` / `"fake-..."` のようなプレースホルダが
                # 「pin 済み」を名乗れてしまい、しかも両 repeats で同一なら署名比較も
                # 素通りする（Codex P1）。真の 64 桁 hex を要求する。
                raise ValueError(
                    f"evaluate_m2_bars: category {category!r} rows[{idx}] の {key} "
                    f"{value!r} が真の sha256（64 桁 lowercase hex）でない; プレースホルダを "
                    "記録済み model pin と見なさない (fail-closed)"
                )
    # 分離を要する route（S_fullstack の demucs→crepe）は、分離器と **その出力 stem** も
    # pin されていなければ「Demucs の実行が未 pin のまま」証拠束になる。両 report が
    # 揃って欠いていると署名比較も素通りするため、存在自体を要求する（Codex P2）。
    if _CATEGORY_SPECS.get(category, {}).get("kind") == "fullstack":
        for idx, row in enumerate(rows):
            preprocessing = row.get("provenance_preprocessing")
            if not isinstance(preprocessing, dict):
                raise ValueError(
                    f"evaluate_m2_bars: category {category!r} rows[{idx}] は分離経路なのに "
                    "provenance_preprocessing を欠く; 分離の実行を pin できない row を "
                    "証拠にしない (fail-closed)"
                )
            # D-4: 不変量側（分離器の重み/コード）と per-clip 側（stem_sha256。
            # S_fullstack は 1 row = 1 clip なので `provenance_preprocessing` 直下に
            # 残る）を `split_preprocessing_invariants` で明示的に分けて検査する。
            invariants, per_clip = split_preprocessing_invariants(preprocessing)
            for key in ("separation_weights_sha256", "separation_code_sha256"):
                if not _is_sha256(invariants.get(key)):
                    raise ValueError(
                        f"evaluate_m2_bars: category {category!r} rows[{idx}] の "
                        f"preprocessing.{key} {invariants.get(key)!r} が真の sha256 でない; "
                        "分離器・分離出力が未 pin の row を証拠にしない (fail-closed)"
                    )
            if not _is_sha256(per_clip.get("stem_sha256")):
                raise ValueError(
                    f"evaluate_m2_bars: category {category!r} rows[{idx}] の "
                    f"preprocessing.stem_sha256 {per_clip.get('stem_sha256')!r} が真の "
                    "sha256 でない; 分離器・分離出力が未 pin の row を証拠にしない "
                    "(fail-closed)"
                )

    signatures = [_row_model_stack_signature(row) for row in rows]
    if len({json.dumps(sig, sort_keys=True, default=str) for sig in signatures}) > 1:
        raise ValueError(
            f"evaluate_m2_bars: category {category!r} の rows が別 model stack で測られている "
            f"（extractor/分離器の重み・コード・version 署名が repeats 間で不一致）; "
            "同一 stack 下の再現でない run を repeats と見なさない (fail-closed)"
        )
    return signatures[0]


def _environment_execution_pins(route: MelodyRoute) -> Dict[str, Any]:
    """評価環境自身から、route の実行スタックの pin を**再計算**する（実行証拠）。

    report が自己申告する `route_runner_injected` は report bytes ごと書き換えられる
    ため、単独では publish 可否の根拠にならない（Codex P1: digest の束縛は「その
    bytes がどう作られたか」を認証しない）。そこで publish 判定の根拠を、
    **評価器プロセスが自分の環境から `use_cache=False` で再計算した pin** と row の
    pin の一致に置く。これは report 内に書かれていない・書き換えられない証拠で、
    偽造 report は評価環境に実在する CREPE/Demucs スタックの pin を言い当てない限り
    通らない（同一スタックを導入して pin を写し取る同権限攻撃者は、プロセスメモリも
    書ける = preload ゲート群と同じ境界外）。
    """
    from svp_rpe.melody.provenance import (
        extractor_code_sha256,
        extractor_weights_fingerprint,
        separation_code_fingerprint,
    )

    pins: Dict[str, Any] = {}
    pins["extractor_code_sha256"] = extractor_code_sha256(route.extractor, use_cache=False)
    weights = extractor_weights_fingerprint(route.extractor, use_cache=False)
    pins["extractor_weights_sha256"] = weights.sha256 if weights is not None else None
    if route.requires_separation:
        sep_code, _covered = separation_code_fingerprint(use_cache=False)
        pins["separation_code_sha256"] = sep_code
        try:
            from svp_rpe.rpe.learned.source_separation_adapter import (
                resolve_separation_weights,
            )

            pins["separation_weights_sha256"] = resolve_separation_weights().sha256
        except Exception:
            pins["separation_weights_sha256"] = None
    return pins


def _require_execution_evidence(
    category: str, rows: List[Dict[str, Any]], route: MelodyRoute
) -> None:
    """row の pin が **評価環境から再計算した実行証拠** と一致することを要求する。

    再計算できない環境（抽出器スタック未導入 = pin が None）からは verdict を
    publish しない——実測を行った slow-lane 機で評価する運用（設計 §5）を
    fail-closed に強制する形になる。
    """
    expected = _environment_execution_pins(route)
    for key, value in expected.items():
        if value is None:
            raise RuntimeError(
                f"evaluate_m2_bars: 評価環境で category {category!r} の実行証拠 "
                f"（{key}）を再計算できない（抽出器スタック未導入/重み未取得）; "
                "report の自己申告だけを根拠に publish しない — 実測を行った "
                "slow-lane 機で評価すること (fail-closed)"
            )
    for idx, row in enumerate(rows):
        actual_pairs = [
            ("extractor_code_sha256", row.get("provenance_extractor_code_sha256")),
            ("extractor_weights_sha256", row.get("provenance_extractor_weights_sha256")),
        ]
        if route.requires_separation:
            # D-4: 実行証拠との突合は不変量側だけで行う（stem_sha256 は per-clip 量で
            # あり、この評価環境が「同じ分離器スタックで動いているか」の証拠にならない）。
            invariants, _ = split_preprocessing_invariants(row.get("provenance_preprocessing"))
            actual_pairs.extend(
                [
                    ("separation_code_sha256", invariants.get("separation_code_sha256")),
                    (
                        "separation_weights_sha256",
                        invariants.get("separation_weights_sha256"),
                    ),
                ]
            )
        for key, actual in actual_pairs:
            if actual != expected[key]:
                raise ValueError(
                    f"evaluate_m2_bars: category {category!r} rows[{idx}] の {key} "
                    f"{actual!r} が評価環境から再計算した実行証拠 {expected[key]!r} と "
                    "一致しない; この環境の実スタックで測られていない row を証拠に "
                    "しない (fail-closed)"
                )


def _bars_registration_attestation(
    bars_path: "str | Path", raw: bytes
) -> Tuple[Dict[str, Any], datetime]:
    """供給された bars bytes の事前登録を **git 履歴（不可変の記録）** で立証する。

    bars 内の `registered_utc` は自己申告であり、「未来でない」ことしか検査できない
    ——実測を見てから閾値を作り、日付を過去へ backdate した bars でも通る（Codex P2
    第 28 巡）。そこで、この **正確な bytes（blob）が履歴に最初に現れた commit の
    committer 日時**を立証値とする。

    立証範囲の正直会計（Codex P2 第 30 巡）: この立証が**証明する**のは「blob が
    HEAD の祖先 commit に存在する = 内容がその commit の一部として共有履歴に入って
    いる」ことまで。committer 日時は commit 作成者が任意に設定できる
    （`GIT_COMMITTER_DATE`）ため、**履歴に commit を書ける同権限者に対する時刻順序の
    証明ではない**（preload ゲート群と同じ境界の外）。report は commit されないので
    「子孫 artifact に対する ancestry 順序」も構成できない。順序比較は (a) 誠実な
    ミス（登録前に測り始めた run の混入）の検出と、(b) 履歴を書けない偽造者に対する
    防御として fail-closed で維持し、verdict の attestation にはこの限界を
    `ordering_is_proof: false` として明記する——日時順序を証明として名乗らない。

    立証できない（リポジトリ外・履歴に無い blob・git 不能）バーは fail-closed。
    """
    try:
        rel = Path(bars_path).resolve().relative_to(ROOT)
    except ValueError as exc:
        raise RuntimeError(
            f"evaluate_m2_bars: bars {bars_path!r} がリポジトリ外; 事前登録を git 履歴で "
            "立証できないバーで verdict を publish しない (fail-closed)"
        ) from exc

    def _git(*args: str, stdin: "bytes | None" = None) -> bytes:
        # Codex 13 巡目 H19: 非修飾 `git` は ldconfig（P1-A）と同じ PATH 注入クラス
        # ——信頼できる絶対パスから、動的リンク/ロケール解決を歪めうる環境変数を
        # 除去した最小 env で起動する（`svp_rpe.melody.provenance` の共有ヘルパー）。
        from svp_rpe.melody.provenance import _hardened_subprocess_env, _trusted_git_executable

        git_exe = _trusted_git_executable()
        proc = subprocess.run(
            [git_exe, "-C", str(ROOT), *args],
            capture_output=True,
            input=stdin,
            env=_hardened_subprocess_env(),
        )
        if proc.returncode != 0:
            stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"evaluate_m2_bars: git {' '.join(args)} が失敗 ({stderr}); 事前登録を "
                "git 履歴で立証できない環境から publish しない (fail-closed)"
            )
        return proc.stdout

    blob = _git("hash-object", "--stdin", stdin=raw).decode("ascii").strip()
    rev_list = _git("rev-list", "HEAD", "--", rel.as_posix()).decode("ascii").split()
    first_commit: Optional[str] = None
    for commit in reversed(rev_list):  # 最古 → 最新の順に、blob が最初に現れた commit
        try:
            commit_blob = _git("rev-parse", f"{commit}:{rel.as_posix()}").decode("ascii").strip()
        except RuntimeError:
            continue  # その commit に path が無い（削除期間等）
        if commit_blob == blob:
            first_commit = commit
            break
    if first_commit is None:
        raise RuntimeError(
            f"evaluate_m2_bars: 供給された bars（blob {blob}）が git 履歴のどの commit にも "
            "存在しない; 自己申告の registered_utc だけでは事前登録を名乗れない "
            "（commit 済みの凍結バーで評価すること・fail-closed）"
        )
    committed_iso = _git("show", "-s", "--format=%cI", first_commit).decode("ascii").strip()
    committed = datetime.fromisoformat(committed_iso).astimezone(timezone.utc)
    attestation = {
        "first_commit": first_commit,
        "committed_utc": committed.isoformat(),
        "source": "git_history_first_blob_occurrence",
        # 内容の立証（blob が HEAD 祖先に存在）と、日時順序の非証明性を分けて記録する。
        "content_evidence": "blob_in_head_ancestry",
        "ordering_evidence": "committer_date",
        "ordering_is_proof": False,
    }
    return attestation, committed


def _require_attested_registration(
    bars_path: "str | Path",
    raw: bytes,
    started_by_index: List[Tuple[int, datetime]],
) -> Dict[str, Any]:
    """全 report の測定開始が bars の**履歴上の登録時点**より厳密に後であることを要求する。

    自己申告 `registered_utc` に対する検査（`_parse_registered_utc` 系）に、履歴由来の
    committer 日時との順序検査を重ねる。この順序検査は誠実なミス検出 + 履歴を書けない
    偽造者への防御であり、**履歴に commit を書ける同権限者への証明ではない**
    （committer 日時は作成者設定値。`_bars_registration_attestation` の正直会計を参照。
    Codex P2 第 30 巡）——verdict の attestation は `ordering_is_proof: false` を明記する。
    """
    attestation, committed = _bars_registration_attestation(bars_path, raw)
    for idx, started in started_by_index:
        # 等値も拒否する: `_utc_now()` も git の %cI も秒精度なので、同一秒内では
        # 「commit より前に測定を開始した」ケースと順序を区別できない（Codex P2
        # 第 29 巡）。秒精度の証拠で事前登録を主張できるのは、開始が厳密に後の秒に
        # ある場合だけ。
        if started <= committed:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] の started_utc {started.isoformat()} が、"
                f"bars が git 履歴に現れた登録時点 {committed.isoformat()} より後でない"
                "（同一秒を含む）; 秒精度の証拠では同一秒内の順序を立証できず、"
                "自己申告 registered_utc の backdate では事前登録を名乗れない "
                "(fail-closed)"
            )
    return attestation


def _external_fixtures_registration_attestation(
    external_fixtures_path: "str | Path", raw: bytes
) -> Tuple[Dict[str, Any], datetime]:
    """供給された `m2c_external_fixtures.yaml` bytes の事前登録を git 履歴で立証する。

    M2c PR-M2c-1 review（Codex 第 1 巡 P1）: `_bars_registration_attestation`
    （bars.yaml 用）と**同じ機構**を外部素材の事前登録 pin ファイルへ対称適用する。
    正直会計・limitation は bars 側と同一の文言を踏襲する: この立証が**証明する**の
    は「blob が HEAD の祖先 commit に存在する」ことまで。committer 日時は commit
    作成者が任意に設定できる（`GIT_COMMITTER_DATE`）ため、**履歴に commit を書ける
    同権限者に対する時刻順序の証明ではない**（preload ゲート群と同じ境界の外）。
    順序比較は (a) 誠実なミス（登録前に測り始めた run の混入）の検出と、(b) 履歴を
    書けない偽造者に対する防御として fail-closed で維持し、attestation には
    `ordering_is_proof: false` を明記する。

    立証できない（リポジトリ外・履歴に無い blob・git 不能）fixtures は fail-closed。
    """
    try:
        rel = Path(external_fixtures_path).resolve().relative_to(ROOT)
    except ValueError as exc:
        raise RuntimeError(
            f"evaluate_m2_bars: external fixtures {external_fixtures_path!r} がリポジトリ外; "
            "事前登録を git 履歴で立証できない fixtures で verdict を publish しない "
            "(fail-closed)"
        ) from exc

    def _git(*args: str, stdin: "bytes | None" = None) -> bytes:
        # bars 側 `_bars_registration_attestation._git` と同じ硬化（Codex 13 巡目 H19）。
        from svp_rpe.melody.provenance import _hardened_subprocess_env, _trusted_git_executable

        git_exe = _trusted_git_executable()
        proc = subprocess.run(
            [git_exe, "-C", str(ROOT), *args],
            capture_output=True,
            input=stdin,
            env=_hardened_subprocess_env(),
        )
        if proc.returncode != 0:
            stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"evaluate_m2_bars: git {' '.join(args)} が失敗 ({stderr}); 事前登録を "
                "git 履歴で立証できない環境から publish しない (fail-closed)"
            )
        return proc.stdout

    blob = _git("hash-object", "--stdin", stdin=raw).decode("ascii").strip()
    rev_list = _git("rev-list", "HEAD", "--", rel.as_posix()).decode("ascii").split()
    first_commit: Optional[str] = None
    for commit in reversed(rev_list):  # 最古 → 最新の順に、blob が最初に現れた commit
        try:
            commit_blob = _git("rev-parse", f"{commit}:{rel.as_posix()}").decode("ascii").strip()
        except RuntimeError:
            continue  # その commit に path が無い（削除期間等）
        if commit_blob == blob:
            first_commit = commit
            break
    if first_commit is None:
        raise RuntimeError(
            f"evaluate_m2_bars: 供給された external fixtures（blob {blob}）が git 履歴の "
            "どの commit にも存在しない; 自己申告の registered_utc だけでは事前登録を "
            "名乗れない（commit 済みの凍結 fixtures で評価すること・fail-closed）"
        )
    committed_iso = _git("show", "-s", "--format=%cI", first_commit).decode("ascii").strip()
    committed = datetime.fromisoformat(committed_iso).astimezone(timezone.utc)
    attestation = {
        "first_commit": first_commit,
        "committed_utc": committed.isoformat(),
        "source": "git_history_first_blob_occurrence",
        "content_evidence": "blob_in_head_ancestry",
        "ordering_evidence": "committer_date",
        "ordering_is_proof": False,
    }
    return attestation, committed


def _require_attested_external_fixtures_registration(
    external_fixtures_path: "str | Path",
    raw: bytes,
    started_by_index: List[Tuple[int, datetime]],
) -> Dict[str, Any]:
    """外部素材カテゴリの全 row の測定開始が、fixtures の**履歴上の登録時点**より
    厳密に後であることを要求する（`_require_attested_registration` と対称、M2c）。
    """
    attestation, committed = _external_fixtures_registration_attestation(
        external_fixtures_path, raw
    )
    for idx, started in started_by_index:
        if started <= committed:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] の started_utc {started.isoformat()} が、"
                f"external fixtures が git 履歴に現れた登録時点 {committed.isoformat()} より"
                "後でない（同一秒を含む）; 秒精度の証拠では同一秒内の順序を立証できず、"
                "自己申告 registered_utc の backdate では事前登録を名乗れない (fail-closed)"
            )
    return attestation


def _require_fresh_process_report_provenance(
    report: Dict[str, Any], category: str, *, expected_specs_sha256: str
) -> None:
    """測り直し子プロセスの report が「本評価環境・現行コード・現行スコアラー」の
    実行であることを、metrics 比較より前に要求する（fail-closed）。

    親プロセスが `_require_execution_evidence` で pin を取った後に推論コード/重みが
    差し替わると、子プロセスは新しいスタックで測る。metrics だけ比較して report を
    捨てると、変わったスタックが同じ（または捏造に合わせた）metrics を出した場合に
    「以前のスタックを名乗る verdict」が通る（Codex P2 第 27 巡）。report レベルの
    provenance をここで、row レベルの model stack は `_require_homogeneous_model_stack`
    で提出 row と突き合わせる。
    """
    schema = report.get("schema_version")
    if schema != _EXPECTED_REPORT_SCHEMA:
        raise RuntimeError(
            f"category {category!r} の測り直し report の schema_version {schema!r} が "
            f"{_EXPECTED_REPORT_SCHEMA!r} でない (fail-closed)"
        )
    if report.get("route_runner_injected"):
        raise RuntimeError(
            f"category {category!r} の測り直し report が注入ランナーの実行を名乗っている; "
            "実抽出器による測り直しでない (fail-closed)"
        )
    if report.get("preloaded_seed_modules") != []:
        raise RuntimeError(
            f"category {category!r} の測り直し子プロセスに事前ロードがある "
            f"({report.get('preloaded_seed_modules')!r}); 素の CLI 実行でない (fail-closed)"
        )
    if report.get("pre_bound_scorer_native_mappings") != []:
        raise RuntimeError(
            f"category {category!r} の測り直し子プロセスは scorer ネイティブが束縛前に "
            f"既にロード済みだった ({report.get('pre_bound_scorer_native_mappings')!r}); "
            "mmap 済み実体は disk hash で検出できないため素の CLI 実行の証拠にしない "
            "(fail-closed)"
        )
    if report.get("non_standard_import_hooks") != []:
        raise RuntimeError(
            f"category {category!r} の測り直し子プロセスに非標準の import hook がある "
            f"({report.get('non_standard_import_hooks')!r}); find_spec の場所解決を "
            "改変しうる finder が無いことを立証できないため素の CLI 実行の証拠にしない "
            "(fail-closed)"
        )
    if report.get("scorer_load_time_hash_mismatches") != []:
        raise RuntimeError(
            f"category {category!r} の測り直し子プロセスが scorer .py の "
            f"swap-and-restore 痕跡を記録している "
            f"({report.get('scorer_load_time_hash_mismatches')!r}); compile された "
            "source bytes が束縛時点の期待と食い違うため素の CLI 実行の証拠にしない "
            "(fail-closed)"
        )
    _require_scorer_compile_observation_covers_imported_modules(
        report,
        context=f"category {category!r} の測り直し report",
        exception_cls=RuntimeError,
    )
    if report.get("sys_flags_optimize") != 0:
        raise RuntimeError(
            f"category {category!r} の測り直し子プロセスが -O/-OO 実行（sys.flags."
            f"optimize={report.get('sys_flags_optimize')!r}）; 数値バックエンドの "
            "assert ガードが除去された経路を証拠にしない (fail-closed)"
        )
    generator = report.get("generator_code_sha256")
    if generator != _LOADED_GENERATOR_CODE_SHA256:
        raise RuntimeError(
            f"category {category!r} の測り直し report の generator_code_sha256 "
            f"{generator!r} が評価器の {_LOADED_GENERATOR_CODE_SHA256!r} と不一致; "
            "測り直し中に first-party コードが変わっている (fail-closed)"
        )
    reported_scorer = _validated_scorer_pin_tuple(
        report, context=f"category {category!r} の測り直し report"
    )
    # Codex 15 巡目 P2: 任意閉包メンバーの presence 比較を observed-closure ベースへ
    # 統一するため、評価環境側の再計算より前に閉包を観測可能にする（docstring 参照）。
    _ensure_scorer_optional_closure_observed()
    expected_scorer = _validated_scorer_pin_tuple(
        _scorer_pins(use_cache=False), context="評価環境の再計算スコアラー pin"
    )
    if reported_scorer != expected_scorer:
        raise RuntimeError(
            f"category {category!r} の測り直し report のスコアラー閉包 pin "
            f"{reported_scorer!r} が評価環境の {expected_scorer!r} と不一致; 測り直し中に "
            "mir_eval/scipy/numpy のいずれかが変わっている (fail-closed)"
        )
    if report.get("harness_loaded_as_main") is not True:
        raise RuntimeError(
            f"category {category!r} の測り直し report が直接パスの script 実行でない; "
            "stale .pyc の余地を残す実行形態の測り直しを証拠にしない (fail-closed)"
        )
    reported_specs = report.get("specs_sha256")
    if reported_specs != expected_specs_sha256:
        raise RuntimeError(
            f"category {category!r} の測り直し report の specs_sha256 {reported_specs!r} "
            f"が評価器の読んだ凍結 specs {expected_specs_sha256!r} と不一致; 別 fixture を "
            "測った検証 run を証拠にしない (fail-closed)"
        )


def _run_verification_in_fresh_process(
    category: str,
    index: int,
    *,
    tmp_dir: Path,
    specs_path: Path,
    bars_path: Path,
    expected_specs_sha256: str,
) -> Dict[str, Any]:
    """測り直し 1 回分を新規プロセス（素の CLI run）で実行し、その category row を返す。

    プロセス境界により各 repeat は import・重みロード・モデル初期化から独立に行われ、
    相互 bit 一致は「run 間決定論」の実証になる（Codex P2 第 24 巡）。子プロセスは
    素の CLI なので preload ゲート群も自然に通る。失敗（非ゼロ exit / report 欠落）は
    「再実行できない環境」として fail-closed。`specs_path` は評価器が読んだ bytes の
    凍結複製（temp 配下）であり、子 report の `specs_sha256` も `expected_specs_sha256`
    と照合する（Codex P2 第 34 巡）。
    """
    report_path = tmp_dir / f"verification_{index}.json"
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--out",
        str(report_path),
        "--categories",
        category,
        "--specs",
        str(Path(specs_path).resolve()),
        "--bars",
        str(Path(bars_path).resolve()),
    ]
    # 子プロセスには fresh な bytecode キャッシュ空間を渡す: 既定のタイムスタンプ
    # 検証 .pyc は同サイズ・同 mtime の差し替えで stale bytecode を再利用しうる
    # （Codex P2 第 33 巡）。ハーネス自身を含む全 first-party がソースから
    # 再コンパイルされ、実行 bytecode が hash 対象のソース bytes に束縛される。
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPYCACHEPREFIX"] = str(tmp_dir / f"pyc-fresh-{index}")
    proc = subprocess.run(command, capture_output=True, text=True, env=env)
    if proc.returncode != 0 or not report_path.is_file():
        tail = " / ".join((proc.stderr or "").strip().splitlines()[-3:])
        raise RuntimeError(
            f"evaluate_m2_bars: category {category!r} の測り直しプロセスが失敗した "
            f"(exit={proc.returncode}: {tail}); 評価環境で再実行できないため publish "
            "しない (fail-closed)"
        )
    verification = load_report(report_path).data
    # metrics だけ取り出して report を捨てない: report レベルの provenance
    # （素の CLI・現行 first-party コード・現行スコアラー・凍結 specs）をここで
    # 検証する（Codex P2 第 27/34 巡）。
    _require_fresh_process_report_provenance(
        verification, category, expected_specs_sha256=expected_specs_sha256
    )
    row = verification.get("categories", {}).get(category)
    if not isinstance(row, dict):
        raise RuntimeError(
            f"evaluate_m2_bars: 測り直しプロセスの report に category {category!r} の "
            "row が無い; 評価環境で再実行できないため publish しない (fail-closed)"
        )
    return row


def _reverify_category_measurement(
    category: str,
    rows: List[Dict[str, Any]],
    *,
    bars: "BarsArtifact",
    specs_raw: bytes,
    repeats: int,
    verification_runner: Optional[RouteRunner] = None,
    external_manifest_path: Optional[Path] = None,
    external_fixtures_path: Path = EXTERNAL_FIXTURES_PATH,
    external_fixtures_raw: Optional[bytes] = None,
    m2e_bars_raw: Optional[bytes] = None,
    level: Optional[str] = None,
    eval_cell_store: Optional[Path] = None,
    workers: int = 1,
    thread_pinning: Optional[Dict[str, Any]] = None,
) -> None:
    """`category` の kind に応じて S（specs 由来合成）/ V（外部素材、M2c）の測り直しへ振り分ける。

    S カテゴリの挙動・シグネチャは変更しない。M2c で追加した外部素材カテゴリは
    `_reverify_external_category_measurement` へ委譲する（`--external-manifest` が
    評価に渡されていなければ fail-closed）。`external_fixtures_raw` は評価器が実際に
    読んだ fixtures bytes（`bars`/`specs_raw` と同型の凍結複製用）。

    C2/C3（`eval_cell_store` / `workers` / `thread_pinning`）は**外部素材カテゴリ
    専用**である。S カテゴリの測り直しは合成 fixture の再生成なので 10 h 級の
    コストを持たず、分離 store も並列化も要らない（触らない = 既存の挙動を 1 バイトも
    変えない）。
    """
    category_spec = _CATEGORY_SPECS[category]
    if category_spec["kind"] == "external":
        if external_fixtures_raw is None:
            raise RuntimeError(
                f"evaluate_m2_bars: category {category!r} は外部素材カテゴリだが "
                "external_fixtures_raw が渡されていない (fail-closed)"
            )
        if category_spec["bars_file"] != "m2_accuracy_bars.yaml" and m2e_bars_raw is None:
            raise RuntimeError(
                f"evaluate_m2_bars: category {category!r} は別 bars ファイルに所有される "
                "が m2e_bars_raw が渡されていない; 測り直し子へ帯登録を凍結転写できない "
                "(fail-closed)"
            )
        _reverify_external_category_measurement(
            category,
            rows,
            bars=bars,
            specs_raw=specs_raw,
            external_fixtures_raw=external_fixtures_raw,
            repeats=repeats,
            verification_runner=verification_runner,
            external_manifest_path=external_manifest_path,
            external_fixtures_path=external_fixtures_path,
            m2e_bars_raw=m2e_bars_raw,
            level=level,
            eval_cell_store=eval_cell_store,
            workers=workers,
            thread_pinning=thread_pinning,
        )
        return
    _reverify_direct_or_fullstack_category_measurement(
        category, rows, bars=bars, specs_raw=specs_raw, repeats=repeats,
        verification_runner=verification_runner,
    )


def _reverify_direct_or_fullstack_category_measurement(
    category: str,
    rows: List[Dict[str, Any]],
    *,
    bars: "BarsArtifact",
    specs_raw: bytes,
    repeats: int,
    verification_runner: Optional[RouteRunner] = None,
) -> None:
    """評価器自身が同じ凍結 fixture を **`repeats` 回独立に測り直し**、bit 一致を要求する。

    fixture の同一性も凍結する（Codex P2 第 34 巡）: 子に `--specs` の実パスを渡すと
    「評価器が読んで hash した後にファイルが差し替えられ、子は別 fixture を測る」
    TOCTOU が生じるため、**評価器が読んだ specs bytes の複製**を bars と同じ temp 配下へ
    書いて子に渡し、子 report の `specs_sha256` と検証 row の `waveform_sha256` も
    提出 row と照合する。

    環境 pin の照合（`_require_execution_evidence`）は「そのスタックが導入されて
    いる」ことまでしか証明しない——導入済みの機で pin を写し取りつつ metrics を
    捏造した report は通ってしまう（Codex P1 第 21 巡）。決定論パイプライン
    （shifts=0・PR #221）の帰結として、正しい report の metrics は**評価器が同じ
    fixture を自分で測り直した結果と bit 一致する**はずなので、それを publish の
    最終条件にする。捏造 metrics は真の抽出出力と一致しない限り通らない——一致する
    ならそれは捏造ではない。

    `verification_runner=None` は実抽出器で測り直す。スタックが無く再実行できない
    環境では publish を拒否する（slow-lane 機での評価を強制）。この引数は
    `evaluate_m2_bars` の公開シグネチャには**存在しない**——注入口を公開 API に
    置くと、捏造 report と同じフェイクランナーで測り直し検証ごと再現させて pass を
    publish できる（Codex P1 第 22 巡）。機構テストは monkeypatch（プロセスメモリ
    への同権限書き込み = preload ゲート群と同じ境界外）で本 helper を差し替える。

    測り直しは 1 回でなく **`repeats`（= 凍結 bars の `repeats_min`）回**行う。
    run_id は self-reported なので、1 つの report をコピーして run_id だけ差し替えれば
    重複検査を通り、「n>=2 の独立実測」を 1 回の実測で名乗れてしまう（Codex P2
    第 23 巡）。決定論契約下では本物の 2 run とコピーは bit 一致ゆえ観測不能なので、
    提出側の独立性を認証する代わりに、**評価器自身が要求本数の独立実行**を行い、
    相互 bit 一致（= 決定論契約がこの環境で実際に成立していること）を publish の
    条件にする。verdict の repeats 契約は evaluator 側の実行に根拠を持つ。

    実抽出器の測り直し（`verification_runner=None`）は **repeat ごとに新規プロセス**
    （素の CLI）で行う。同一プロセス内の反復は CREPE/TensorFlow のグローバル・
    ロード済み重み・モデルキャッシュを共有するため、「プロセス内初期化の後だけ
    安定する」結果でも bit 一致してしまい、契約が謳う **run 間**（プロセス間）
    決定論を実証しない（Codex P2 第 24 巡）。注入ランナー（monkeypatch 経由の
    テスト seam）はプロセス境界を越えられないため in-process のまま——その seam
    自体が境界外であることは第 22 巡の整理のとおり。
    """
    if repeats < 2:
        raise ValueError(
            f"_reverify_category_measurement: repeats {repeats!r} が 2 未満; 決定論確認は "
            "n>=2 の独立実行を要件とする (fail-closed)"
        )
    verification_metrics: List[Dict[str, Any]] = []
    verification_rows: List[Dict[str, Any]] = []
    expected_specs_sha256 = hashlib.sha256(specs_raw).hexdigest()
    with tempfile.TemporaryDirectory(prefix="m2-reverify-") as tmp:
        bars_path = Path(tmp) / "m2_accuracy_bars.yaml"
        bars_path.write_bytes(bars.raw)
        specs_copy = Path(tmp) / "m2_accuracy_specs.yaml"
        specs_copy.write_bytes(specs_raw)
        for index in range(repeats):
            if verification_runner is not None:
                verification = run_accuracy(
                    categories=(category,),
                    route_runner=verification_runner,
                    specs_path=specs_copy,
                    bars_path=bars_path,
                )
                vrow = verification["categories"][category]
            else:
                vrow = _run_verification_in_fresh_process(
                    category,
                    index,
                    tmp_dir=Path(tmp),
                    specs_path=specs_copy,
                    bars_path=bars_path,
                    expected_specs_sha256=expected_specs_sha256,
                )
            if vrow.get("outcome") != "measured":
                raise RuntimeError(
                    f"evaluate_m2_bars: category {category!r} を評価環境で再実行できない "
                    f"（outcome={vrow.get('outcome')!r}: {vrow.get('detail', '')}）; 測り直しに "
                    "よる検証なしで report の metrics を publish しない — 実測を行った "
                    "slow-lane 機で評価すること (fail-closed)"
                )
            verification_rows.append(vrow)
            verification_metrics.append(vrow["metrics"])
    # 検証 row の model stack を提出 row と突き合わせる: 測り直し中に重み/推論コードが
    # 差し替わった場合、metrics が一致しても「別スタックの実行」であり、verdict が
    # 名乗る stack の証拠にならない（Codex P2 第 27 巡）。
    _require_homogeneous_model_stack(category, rows + verification_rows)
    # 検証 row の波形 pin も提出 row と一致すること（別 fixture を測った検証 run を
    # 「同じ測定の再現」と数えない・Codex P2 第 34 巡）。提出 row の waveform は
    # `_require_registered_row_identity` で bars の登録 pin と照合済み。
    expected_waveform = rows[0].get("waveform_sha256") if rows else None
    verification_wav = (
        verification_rows[0].get("input_wav_sha256") if verification_rows else None
    )
    for vidx, vrow in enumerate(verification_rows):
        if vrow.get("waveform_sha256") != expected_waveform:
            raise ValueError(
                f"evaluate_m2_bars: category {category!r} の測り直し {vidx} 回目の "
                f"waveform_sha256 {vrow.get('waveform_sha256')!r} が提出 row の "
                f"{expected_waveform!r} と不一致; 別 fixture を測った検証 run を同じ "
                "測定の再現と数えない (fail-closed)"
            )
        if vrow.get("input_wav_sha256") != verification_wav:
            raise RuntimeError(
                f"evaluate_m2_bars: category {category!r} の測り直し {vidx} 回目の "
                f"input_wav_sha256 が検証 run 間で不一致; 直列化 WAV の決定論が "
                "この環境で成立していない (fail-closed)"
            )
    # 提出 row の直列化 WAV pin も評価器自身の測り直しへ束縛する（Codex P2 第 38 巡）:
    # waveform_sha256 だけの照合では、編集・stale な input_wav_sha256 を持つ report が
    # 「抽出器が実際に消費した bytes を偽って名乗る」まま publish されえた。
    for idx, row in enumerate(rows):
        if row.get("input_wav_sha256") != verification_wav:
            raise ValueError(
                f"evaluate_m2_bars: category {category!r} rows[{idx}] の input_wav_sha256 "
                f"{row.get('input_wav_sha256')!r} が評価器の測り直し {verification_wav!r} "
                "と不一致; 抽出器が消費した直列化 WAV を偽って名乗る row を publish "
                "しない (fail-closed)"
            )
    if not _repeats_bit_identical(verification_metrics):
        raise RuntimeError(
            f"evaluate_m2_bars: category {category!r} の評価器自身による {repeats} 回の "
            "測り直しが相互に bit 一致しない; 決定論契約（shifts=0）がこの環境で成立して "
            "いないため、いかなる repeats も決定論の証拠として publish できない "
            "(fail-closed)"
        )
    vmetrics = verification_metrics[0]
    for idx, row in enumerate(rows):
        if row["metrics"] != vmetrics:
            raise ValueError(
                f"evaluate_m2_bars: category {category!r} rows[{idx}] の metrics が "
                "評価器自身の測り直しと bit 一致しない; 決定論パイプライン（shifts=0）の "
                "下で再現しない row を publish しない (fail-closed)"
            )


def _run_external_verification_in_fresh_process(
    category: str,
    index: int,
    *,
    tmp_dir: Path,
    external_manifest_path: Path,
    specs_path: Path,
    bars_path: Path,
    external_fixtures_path: Path,
    expected_specs_sha256: str,
    m2e_bars_path: Optional[Path] = None,
    level: Optional[str] = None,
    eval_cell_store: Optional[Path] = None,
    thread_pinning: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """外部素材カテゴリ（M2c）の測り直し 1 回分を新規プロセス（素の CLI run）で実行する。

    `_run_verification_in_fresh_process`（S カテゴリ）と同型・対称（M2c PR-M2c-1
    review Codex 第 1 巡 P2）: 評価器が実際に受けた `--specs` / `--bars` /
    `--external-fixtures` を子へ**明示的に**引き渡す（凍結複製・TOCTOU 回避は
    呼び出し元 `_reverify_external_category_measurement` が用意する）。CLI 既定
    （`SPECS_PATH`/`BARS_PATH`/`EXTERNAL_FIXTURES_PATH`）へ暗黙に頼ると、カスタム
    `--specs`/`--bars`/`--external-fixtures` で評価した場合に子が別世代のファイルを
    測り、`_require_fresh_process_report_provenance` の `specs_sha256` 照合で
    （運が悪ければ）食い違わずに素通りしてしまう恐れがある。manifest は評価器の
    実パスをそのまま子へ渡す——manifest が指す音声/注釈は登録済み sha256 と run 側が
    fail-closed で照合するため、TOCTOU（子の実行前に差し替え）は測定失敗
    （sha256 mismatch）として顕在化し、偽の pass を静かに通さない。
    """
    report_path = tmp_dir / f"verification_ext_{index}.json"
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--out",
        str(report_path),
        "--categories",
        category,
        "--external-manifest",
        str(Path(external_manifest_path).resolve()),
        "--specs",
        str(Path(specs_path).resolve()),
        "--bars",
        str(Path(bars_path).resolve()),
        "--external-fixtures",
        str(Path(external_fixtures_path).resolve()),
    ]
    # M2e: 分離した帯登録と run の水準次元も**明示的に**子へ引き渡す（CLI 既定へ
    # 暗黙に頼ると、子が別世代の帯登録・別水準を測りうる）。
    if m2e_bars_path is not None:
        command += ["--m2e-bars", str(Path(m2e_bars_path).resolve())]
    if level is not None:
        command += ["--level", level]
    # C2（store 分離・rev.6 §8.9.2-(1)）: この子プロセスへは **run が使った
    # `--cell-store`（= `store_A`）を絶対に渡さない**（本機能で最も危険な穴）。
    # ここは評価器が「report の metrics が実測結果であること」を独立に確かめるための
    # 測り直しであり、子が run のチェックポイントから resume すると、測り直しは
    # 「セルレコード（= 提出 report を生んだのと同じ測定）を自分自身と比較する」
    # だけになり、bit 一致の検証が恒常的に空虚な成功を返す publish 条件になる。
    #
    # **独立性は「store を分ける」ことで保たれるのであって、「再開できない」ことで
    # 保たれるのではない。** 渡してよいのは evaluate 専用の `store_B`
    # （`--eval-cell-store`）だけで、`store_A` と重ならないことは `main()` の CLI 検査が
    # resolve 後のパス関係で fail-closed に確かめている（同一パス / 入れ子の両方向）。
    # `store_A` を積む分岐はこの関数に存在しない——
    # `test_reverification_child_never_receives_the_run_cell_store` がその不在と、
    # `store_B` が渡ることの両方を固定する。
    if eval_cell_store is not None:
        command += [
            "--cell-store",
            str(Path(eval_cell_store).resolve()),
            # セル鍵の `repeat_index` は**この測り直しの通し番号**。repeats 間で別セルに
            # なることで、「別 repeat の記録を誤って再生する」経路が鍵→パスの写像の
            # 段階で消える（`_cell_store_record_path`）。
            "--repeat-index",
            str(index),
            # C2（PR #240 Codex P1）: 子が書くセルへ **evaluate 役割**を刻ませる。
            # `store_B` が `store_A` のコピーであっても、run 由来のレコードは役割の
            # 不一致で resume されず測り直される——パスの分離だけでは計算の独立を
            # 保証できないため、独立性をレコード自身に束縛する。
            "--cell-store-role",
            _CELL_STORE_ROLE_EVALUATE,
        ]
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPYCACHEPREFIX"] = str(tmp_dir / f"pyc-fresh-ext-{index}")
    # C3 + D-3: 並列に子を起こすなら 3 点固定は必須（HANDOFF §3.1）。env 2 点は
    # **子の起動前**に置く（プロセス開始後の設定は OpenMP/MKL に効かない）。3 点目
    # （`torch.set_num_threads(1)`）は子自身が `--pin-threads` で適用し、その事実を
    # report の `thread_pinning` に刻む——親はそれを下で照合する（宣言だけで検証
    # されない値を作らない）。
    if thread_pinning is not None:
        env["OMP_NUM_THREADS"] = "1"
        env["MKL_NUM_THREADS"] = "1"
        command += ["--pin-threads"]
    proc = subprocess.run(command, capture_output=True, text=True, env=env)
    if proc.returncode != 0 or not report_path.is_file():
        tail = " / ".join((proc.stderr or "").strip().splitlines()[-3:])
        raise RuntimeError(
            f"evaluate_m2_bars: category {category!r} の測り直しプロセスが失敗した "
            f"(exit={proc.returncode}: {tail}); 評価環境で再実行できないため publish "
            "しない (fail-closed)"
        )
    verification = load_report(report_path).data
    _require_fresh_process_report_provenance(
        verification, category, expected_specs_sha256=expected_specs_sha256
    )
    if thread_pinning is not None and verification.get("thread_pinning") != thread_pinning:
        raise RuntimeError(
            f"evaluate_m2_bars: category {category!r} の測り直し {index} 回目の子 report の "
            f"thread_pinning {verification.get('thread_pinning')!r} が、評価対象 report から "
            f"導いた契約 {thread_pinning!r} と不一致; スレッド条件が食い違う測定を同じ測定の "
            "再現と数えない（D-3: 固定は run と evaluate で同一でなければならない）(fail-closed)"
        )
    row = verification.get("categories", {}).get(category)
    if not isinstance(row, dict):
        raise RuntimeError(
            f"evaluate_m2_bars: 測り直しプロセスの report に category {category!r} の "
            "row が無い; 評価環境で再実行できないため publish しない (fail-closed)"
        )
    return row


def _reverify_external_category_measurement(
    category: str,
    rows: List[Dict[str, Any]],
    *,
    bars: "BarsArtifact",
    specs_raw: bytes,
    external_fixtures_raw: bytes,
    repeats: int,
    verification_runner: Optional[RouteRunner],
    external_manifest_path: Optional[Path],
    external_fixtures_path: Path,
    m2e_bars_raw: Optional[bytes] = None,
    level: Optional[str] = None,
    eval_cell_store: Optional[Path] = None,
    workers: int = 1,
    thread_pinning: Optional[Dict[str, Any]] = None,
) -> None:
    """外部素材カテゴリ（M2c）を評価器自身が `repeats` 回独立に測り直す。

    `_reverify_direct_or_fullstack_category_measurement` と同じ「評価器自身の測り
    直しとの bit 一致を publish 条件にする」設計だが、比較対象は `row["clips"]`
    （per-clip 全体）——averaged `row["metrics"]` だけの比較では、平均化で相殺される
    clip 単位の乖離を見逃す（設計 Memo M2c の repeats bit 一致要件）。

    `bars`/`specs_raw`/`external_fixtures_raw` は**評価器が実際に読んだ bytes**を
    tmp 配下へ凍結複製し、子プロセスへ実パスでなくその複製を渡す（S カテゴリの
    `_reverify_direct_or_fullstack_category_measurement` と同型の TOCTOU 回避・
    M2c PR-M2c-1 review Codex 第 1 巡 P2 で対称化）。`expected_specs_sha256` も
    `specs_raw` から導出する（`SPECS_PATH` の暗黙再読込はしない）。

    M2c-1 時点では `m2c_external_fixtures.yaml` の `fixtures` が空のため、V_direct を
    含む run は `_run_external_category` の fail-closed（登録済み clip なし）で本関数
    に到達する前に落ちる——実データは M2c-2 で登録する。
    """
    if repeats < 2:
        raise ValueError(
            f"_reverify_external_category_measurement: repeats {repeats!r} が 2 未満; "
            "決定論確認は n>=2 の独立実行を要件とする (fail-closed)"
        )
    if workers < 1:
        raise ValueError(
            f"_reverify_external_category_measurement: workers {workers!r} が 1 未満; "
            "設計 §8.3 の並列度 P は 1 以上の整数のみ (fail-closed)"
        )
    if external_manifest_path is None:
        raise RuntimeError(
            f"evaluate_m2_bars: category {category!r} は外部素材カテゴリだが "
            "--external-manifest が評価に渡されていない; 測り直しによる検証なしで "
            "report の metrics を publish しない (fail-closed)"
        )
    expected_specs_sha256 = hashlib.sha256(specs_raw).hexdigest()
    # M2c WIP e3810b0 review（Codex 第 2 巡 P2）: 測り直しが提出 report と**同じ
    # manifest**を測ったことを束縛する。`_require_registered_row_identity_external`
    # が提出 rows 間の `external_manifest_sha256` 一致を既に検証済みなので、その
    # 代表値（`rows[0]`）を「測り直しが束縛すべき manifest」として使う——
    # `external_manifest_path` 引数それ自体は呼び出し側が渡す値で、提出 report が
    # 記録した manifest と独立に食い違いうる（例えば evaluate 呼び出し側が誤った
    # パスを渡した場合）。差異は測り直し結果の bit 不一致として間接的に顕在化しうる
    # が、原因を「別 manifest を測った」と即座に特定できるよう明示的に照合する。
    expected_manifest_sha256 = rows[0].get("external_manifest_sha256") if rows else None
    verification_rows: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="m2c-reverify-") as tmp:
        bars_path = Path(tmp) / "m2_accuracy_bars.yaml"
        bars_path.write_bytes(bars.raw)
        specs_copy = Path(tmp) / "m2_accuracy_specs.yaml"
        specs_copy.write_bytes(specs_raw)
        fixtures_copy = Path(tmp) / "m2c_external_fixtures.yaml"
        fixtures_copy.write_bytes(external_fixtures_raw)
        # M2e: 分離した帯登録も**評価器が実際に読んだ bytes**を凍結複製して子へ渡す
        # （`bars`/`specs_raw`/`external_fixtures_raw` と同型の TOCTOU 回避）。
        m2e_bars_copy: Optional[Path] = None
        if m2e_bars_raw is not None:
            m2e_bars_copy = Path(tmp) / "m2e_accuracy_bars.yaml"
            m2e_bars_copy.write_bytes(m2e_bars_raw)
        extra_run_kwargs: Dict[str, Any] = {}
        if m2e_bars_copy is not None:
            extra_run_kwargs["m2e_bars_path"] = m2e_bars_copy
        if level is not None:
            extra_run_kwargs["level"] = level
        # C2（rev.6 §8.9.2-(1)）: run が使った `store_A` は**この経路にも一切現れない**。
        # `extra_run_kwargs` へ積むのは evaluate 専用の `store_B`（`eval_cell_store`）
        # だけで、`repeat_index` は測り直しの通し番号を使う。測り直しが run の
        # チェックポイントから resume できると「レコードを自分自身と比較する」だけに
        # なって検証が空虚になる——それを防ぐのは store の分離であって、再開不能性
        # ではない（fresh process 経路の同じ規律は
        # `_run_external_verification_in_fresh_process` docstring 参照）。
        def _verify_once(index: int) -> Dict[str, Any]:
            if verification_runner is not None:
                run_kwargs = dict(extra_run_kwargs)
                if eval_cell_store is not None:
                    run_kwargs["cell_store"] = eval_cell_store
                    run_kwargs["repeat_index"] = index
                    run_kwargs["cell_store_role"] = _CELL_STORE_ROLE_EVALUATE
                if thread_pinning is not None:
                    run_kwargs["thread_pinning"] = thread_pinning
                verification = run_accuracy(
                    categories=(category,),
                    route_runner=verification_runner,
                    specs_path=specs_copy,
                    bars_path=bars_path,
                    external_manifest_path=external_manifest_path,
                    external_fixtures_path=fixtures_copy,
                    **run_kwargs,
                )
                vrow = verification["categories"][category]
            else:
                vrow = _run_external_verification_in_fresh_process(
                    category,
                    index,
                    tmp_dir=Path(tmp),
                    external_manifest_path=external_manifest_path,
                    specs_path=specs_copy,
                    bars_path=bars_path,
                    external_fixtures_path=fixtures_copy,
                    expected_specs_sha256=expected_specs_sha256,
                    m2e_bars_path=m2e_bars_copy,
                    level=level,
                    eval_cell_store=eval_cell_store,
                    thread_pinning=thread_pinning,
                )
            if vrow.get("outcome") != "measured":
                raise RuntimeError(
                    f"evaluate_m2_bars: category {category!r} を評価環境で再実行できない "
                    f"（outcome={vrow.get('outcome')!r}: {vrow.get('detail', '')}）; 測り直しに "
                    "よる検証なしで report の metrics を publish しない (fail-closed)"
                )
            if vrow.get("external_manifest_sha256") != expected_manifest_sha256:
                raise RuntimeError(
                    f"evaluate_m2_bars: category {category!r} の測り直し {index} 回目の "
                    f"external_manifest_sha256 {vrow.get('external_manifest_sha256')!r} が "
                    f"提出 report の {expected_manifest_sha256!r} と不一致; 別 manifest を "
                    "測った検証 run を同じ測定の再現と数えない (fail-closed)"
                )
            return vrow

        # C3（rev.6 §8.9.2-(2)）: 検証の子を**最大 `workers` 本まで同時に起こす**。
        # publish が要求するのは「fresh process であること」と「run の結果を読まない
        # こと」であって、逐次であることではない。
        #
        # `ProcessPoolExecutor` は使わない——測定は既に `subprocess.run` の別プロセスで
        # 走っており、in-process の worker プロセスを挟むと fresh-process 契約
        # （`_require_fresh_process_report_provenance` が検査する「素の CLI 実行」）が
        # 曖昧になる。ここのスレッドは**子の完了を待つだけ**で、測定そのものは一切
        # 実行しない。`max_workers=P` が同時起動数の上限をそのまま与える。
        #
        # 結果は `index` 順の固定席へ書き戻す（完了順に append すると `P` によって
        # 順序が変わり、下の bit 一致比較・エラー選択が `P` 依存になる）。
        slots: "List[Optional[Dict[str, Any]]]" = [None] * repeats
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_verify_once, index) for index in range(repeats)]
            failures: "List[BaseException]" = []
            for index, future in enumerate(futures):
                # `as_completed` ではなく **index 順**に回収する。複数の測り直しが
                # 失敗したとき、どのエラーが表に出るかが実行タイミングで変わっては
                # ならない（`P` に依存する量を作らない）。
                try:
                    slots[index] = future.result()
                except BaseException as exc:  # noqa: BLE001 — 最小 index の失敗を再送出
                    failures.append(exc)
            if failures:
                raise failures[0]
        verification_rows.extend(row for row in slots if row is not None)
    _require_homogeneous_model_stack(category, rows + verification_rows)
    verification_clip_lists = [vrow["clips"] for vrow in verification_rows]
    if len({json.dumps(c, sort_keys=True) for c in verification_clip_lists}) > 1:
        raise RuntimeError(
            f"evaluate_m2_bars: category {category!r} の評価器自身による {repeats} 回の "
            "測り直しが clip 単位で相互に bit 一致しない; 決定論契約がこの環境で成立して "
            "いないため publish できない (fail-closed)"
        )
    expected_clips = verification_clip_lists[0]
    for idx, row in enumerate(rows):
        if row.get("clips") != expected_clips:
            raise ValueError(
                f"evaluate_m2_bars: category {category!r} rows[{idx}] の clips が "
                "評価器自身の測り直しと bit 一致しない; 決定論パイプラインの下で再現しない "
                "row を publish しない (fail-closed)"
            )


def _require_registered_row_identity(
    category: str, rows: List[Dict[str, Any]], bars: Dict[str, Any]
) -> None:
    """row の同一性（category ラベル / route / input_kind / 波形）を事前登録と突き合わせる。

    `category` は report が名乗るラベルにすぎないので、evaluate はそれを信用できない
    ——`S_direct` と書かれた row が実際には別 fixture・別経路の観測でも、ラベルだけで
    凍結 S_direct バーの pass を publish できてしまう（Codex P1）。report bytes を
    hash しても「誤った証拠を保存する」だけなので、evaluate 側で独立に
    `_CATEGORY_SPECS` と bars の `provenance.waveform_sha256` を関所にする。

    run 側（`_build_category_waveform`）も同じ pin を照合するが、そちらは「実測時に
    正しい波形を合成したか」の検査で、こちらは「提出された row が本当にその fixture の
    観測か」の検査である（手組み・編集済み report は run 側の検査を経由しない）。
    """
    category_spec = _CATEGORY_SPECS.get(category)
    if category_spec is None:
        raise ValueError(
            f"evaluate_m2_bars: 未知の category {category!r}; 事前登録された "
            f"{sorted(_CATEGORY_SPECS)} のみ評価する (fail-closed)"
        )
    pin_key = (
        category_spec["fixture_id"]
        if category_spec["kind"] == "direct"
        else category_spec["composite_id"]
    )
    expected_waveform = bars.get("provenance", {}).get("waveform_sha256", {}).get(pin_key)
    if not expected_waveform:
        raise ValueError(
            f"evaluate_m2_bars: category {category!r} (pin key {pin_key!r}) の "
            "waveform_sha256 pin が bars に無い (fail-closed)"
        )
    for idx, row in enumerate(rows):
        for field, expected in (
            ("route", category_spec["route_name"]),
            ("input_kind", category_spec["input_kind"]),
            ("waveform_sha256", expected_waveform),
        ):
            actual = row.get(field)
            if actual != expected:
                raise ValueError(
                    f"evaluate_m2_bars: category {category!r} rows[{idx}] の {field} "
                    f"{actual!r} が事前登録値 {expected!r} と不一致; ラベルだけが "
                    f"{category!r} の row（別 fixture/別経路の観測、または編集済み "
                    "report）に凍結バーを適用しない (fail-closed)"
                )


def _require_registered_row_identity_external(
    category: str,
    rows: List[Dict[str, Any]],
    external_fixtures_data: Dict[str, Any],
    external_fixtures_sha256: str,
) -> str:
    """外部素材カテゴリ（M2c）の row 同一性を事前登録 fixtures と突き合わせる。

    `_require_registered_row_identity`（S カテゴリ・単一 fixture 波形）の外部素材版:
    row / clip の route・input_kind・audio/annotation sha256 を、evaluate 側が
    独立にロードした `m2c_external_fixtures.yaml`（`external_fixtures_data`）と
    突き合わせる。加えて repeats（複数 report）間で同一 manifest を測ったことも
    要求する（別 manifest を混ぜた repeats を「同じ測定の再現」と数えない）。

    戻り値は repeats 間で共通の `external_manifest_sha256`（cat_result 記録用）。
    """
    category_spec = _CATEGORY_SPECS.get(category)
    if category_spec is None or category_spec["kind"] != "external":
        raise ValueError(
            f"evaluate_m2_bars: category {category!r} は外部素材カテゴリでない "
            "(fail-closed)"
        )
    fixtures = external_fixtures_data.get("fixtures", {})
    manifest_shas: "set[str]" = set()
    for idx, row in enumerate(rows):
        for field, expected in (
            ("route", category_spec["route_name"]),
            ("input_kind", category_spec["input_kind"]),
        ):
            actual = row.get(field)
            if actual != expected:
                raise ValueError(
                    f"evaluate_m2_bars: category {category!r} rows[{idx}] の {field} "
                    f"{actual!r} が事前登録値 {expected!r} と不一致 (fail-closed)"
                )
        if row.get("external_fixtures_sha256") != external_fixtures_sha256:
            raise ValueError(
                f"evaluate_m2_bars: category {category!r} rows[{idx}] の "
                f"external_fixtures_sha256 {row.get('external_fixtures_sha256')!r} が "
                f"評価器の読んだ {external_fixtures_sha256!r} と不一致; 別世代の外部素材 "
                "登録で測った row に凍結バーを適用しない (fail-closed)"
            )
        manifest_sha = row.get("external_manifest_sha256")
        if not isinstance(manifest_sha, str) or not manifest_sha:
            raise ValueError(
                f"evaluate_m2_bars: category {category!r} rows[{idx}] が "
                "external_manifest_sha256 を欠く (fail-closed)"
            )
        manifest_shas.add(manifest_sha)

        clips = row.get("clips")
        if not isinstance(clips, list) or not clips:
            raise ValueError(
                f"evaluate_m2_bars: category {category!r} rows[{idx}] の clips が "
                "非空リストでない (fail-closed)"
            )
        # cohort 完全一致（run 側 `_run_external_category` と同じ関所を evaluate 側でも
        # 独立に課す。手組み・編集済み report は run 側の検査を経由しないため）。
        _require_exact_cohort_match(
            set(fixtures), {c.get("clip_id") for c in clips},
            where=f"evaluate_m2_bars: category {category!r} rows[{idx}]",
        )
        for clip_idx, clip in enumerate(clips):
            clip_id = clip.get("clip_id")
            expected_clip = fixtures.get(clip_id)
            if expected_clip is None:
                raise ValueError(
                    f"evaluate_m2_bars: category {category!r} rows[{idx}].clips[{clip_idx}] "
                    f"の clip_id {clip_id!r} が m2c_external_fixtures.yaml に事前登録 "
                    "されていない (fail-closed)"
                )
            for field, key in (
                ("audio_sha256", "expected_audio_sha256"),
                ("annotation_sha256", "expected_annotation_sha256"),
            ):
                actual = clip.get(field)
                expected = expected_clip[key]
                if actual != expected:
                    raise ValueError(
                        f"evaluate_m2_bars: category {category!r} rows[{idx}]."
                        f"clips[{clip_idx}] ({clip_id!r}) の {field} {actual!r} が "
                        f"事前登録値 {expected!r} と不一致 (fail-closed)"
                    )
    if len(manifest_shas) > 1:
        raise ValueError(
            f"evaluate_m2_bars: category {category!r} の repeats が別 manifest を測っている "
            f"{sorted(manifest_shas)}; 別 manifest の run を同じ測定の repeats と見なさない "
            "(fail-closed)"
        )
    return next(iter(manifest_shas))


def _require_external_clip_bounded_counts(category: str, rows: List[Dict[str, Any]]) -> None:
    """外部素材カテゴリ（M2c）の clip 単位で誤差モデルの母数の自己整合性を検査する。

    `_require_reference_bounded_counts`（S カテゴリ）と同じ関係式（有声かつ chroma
    一致フレーム数 <= 有声フレーム数、RCA×有声フレーム数 == 母数）を検査するが、
    上界は**凍結 spec からの独立再計算ではなく clip 自身の自己申告値**
    （`ref_frame_count`/`ref_voiced_frame_count`、run 時に注釈 CSV から算出）を使う
    ——外部注釈は evaluate 側が独立に読める「凍結 spec」を持たないため（設計 Memo
    M2c: 注釈ファイルの hash 一致は run 時点の fail-closed 照合が担う）。それでも
    内部的にありえない値（型・符号・関係式の破綻）は本関数が拒否する。
    """
    for row_idx, row in enumerate(rows):
        for clip_idx, clip in enumerate(row.get("clips", [])):
            where = f"category {category!r} rows[{row_idx}].clips[{clip_idx}] ({clip.get('clip_id')!r})"
            frame_count = clip.get("ref_frame_count")
            voiced_count = clip.get("ref_voiced_frame_count")
            for field, value in (("ref_frame_count", frame_count), ("ref_voiced_frame_count", voiced_count)):
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError(
                        f"evaluate_m2_bars: {where} の {field} {value!r} が非負整数でない "
                        "(fail-closed)"
                    )
            if voiced_count > frame_count:
                raise ValueError(
                    f"evaluate_m2_bars: {where} の ref_voiced_frame_count {voiced_count} が "
                    f"ref_frame_count {frame_count} を超える (fail-closed)"
                )
            count = int(clip["metrics"]["voiced_chroma_correct_frame_count"])
            if count > voiced_count:
                raise ValueError(
                    f"evaluate_m2_bars: {where} の voiced_chroma_correct_frame_count "
                    f"{count} が ref_voiced_frame_count {voiced_count} を超える (fail-closed)"
                )
            if voiced_count > 0:
                rca = float(clip["metrics"]["raw_chroma_accuracy"])
                implied = rca * voiced_count
                if abs(count - implied) > 1e-6:
                    raise ValueError(
                        f"evaluate_m2_bars: {where} の voiced_chroma_correct_frame_count "
                        f"{count} が raw_chroma_accuracy {rca!r} から復元される分子 "
                        f"{implied:.4f} と一致しない (fail-closed)"
                    )


def _require_external_row_metrics_match_clip_average(
    category: str, rows: List[Dict[str, Any]]
) -> None:
    """外部素材カテゴリ（M2c）の row["metrics"]（カテゴリ集計値）が、`row["clips"]` から
    評価器が**独立に再計算**した算術平均と完全一致することを要求する（fail-closed）。

    M2c WIP e3810b0 review（Codex 第 2 巡 P1）: 集計値（`row["metrics"]`）を run 側
    `_average_external_clip_metrics` が最初に書いた値のまま信頼すると、report が
    `clips` はそのままに集計値だけを書き換えた（例: RPA を水増しする）改竄をバー適用
    まで見逃す。S カテゴリの `_registered_reference_counts`（母数を凍結 spec から
    再計算し、row の自己申告を信用しない）と同じ精神——外部素材カテゴリでは「集計は
    ソース clips から導出されなければならない」という形で、evaluate 側が
    `_average_external_clip_metrics` を clips に対して再適用し、報告値との厳密一致
    （JSON ロード値の `==`。`_average_external_clip_metrics` の算出は clips の登場
    順序に依存する合計/件数の商であり、JSON はリスト順序を保存するため、改竄が無い
    限り浮動小数点は bit 一致する）を publish の条件にする。
    """
    for idx, row in enumerate(rows):
        recomputed = _average_external_clip_metrics(row["clips"])
        reported = row.get("metrics")
        if reported != recomputed:
            raise ValueError(
                f"evaluate_m2_bars: category {category!r} rows[{idx}] の metrics "
                f"{reported!r} が row['clips'] から再計算した平均 {recomputed!r} と "
                "一致しない; 集計はソース clips から導出されなければならない "
                "(fail-closed)"
            )


# 指標の値域（mir_eval の定義域）。閾値判定の前に有限性と範囲を検査する。
_METRIC_RANGES: Dict[str, Tuple[float, float]] = {
    "raw_pitch_accuracy": (0.0, 1.0),
    "raw_chroma_accuracy": (0.0, 1.0),
    "voicing_recall": (0.0, 1.0),
    "voicing_false_alarm": (0.0, 1.0),
    "overall_accuracy": (0.0, 1.0),
    "octave_gap": (-1.0, 1.0),
}


def _registered_reference_counts(
    category: str, bars: Dict[str, Any], specs: Dict[str, Any]
) -> Tuple[int, int]:
    """凍結 spec / 波形から `(ref_frame_count, ref_voiced_frame_count)` を**再計算**する。

    row の自己申告値を上界に使うと、母数と上界を**揃えて**膨らませた 2 本の report が
    そのまま通る（Codex P2）。正解フレーム数は凍結 spec の決定論的な関数なので、
    evaluate 側で独立に組み直せる——`_build_category_waveform` を通すため、
    「凍結 spec が今も bars の `waveform_sha256` pin と同じ波形をレンダするか」も
    同時に確認される（drift していればここで落ちる）。
    """
    category_spec = _CATEGORY_SPECS[category]
    y, sr, _waveform_sha256 = _build_category_waveform(category, category_spec, specs, bars)
    ref_times, ref_freqs = _reference_for_category(
        category_spec, specs, total_duration_sec=float(len(y)) / float(sr)
    )
    return len(ref_times), sum(1 for f in ref_freqs if f > 0.0)


def _require_reference_bounded_counts(
    category: str,
    rows: List[Dict[str, Any]],
    *,
    expected_frame_count: int,
    expected_voiced_frame_count: int,
) -> None:
    """誤差モデルの母数を、**再計算した**正解フレーム数で抑える（fail-closed）。

    `voiced_chroma_correct_frame_count` は「有声かつ chroma 一致フレーム」の数なので、
    定義上 `ref_voiced_frame_count` 以下（したがって `ref_frame_count` 以下）である。
    型と符号だけを見ていると 10 億のような値が bit 一致のまま pass の verdict へ
    転記される（Codex P2）。上界は row ではなく `_registered_reference_counts` が
    凍結 spec から組み直した値を使う——row の自己申告を上界にすると、母数と上界を
    揃えて書き換えるだけで迂回できるため（Codex P2 の追撃指摘）。

    row 側の `ref_*_frame_count` は凍結 spec の決定論的関数なので、再計算値との
    **厳密一致**を要求する（不一致は drift か改竄のいずれかで、どちらも証拠にしない）。
    """
    for idx, row in enumerate(rows):
        where = f"category {category!r} rows[{idx}]"
        for field, expected in (
            ("ref_frame_count", expected_frame_count),
            ("ref_voiced_frame_count", expected_voiced_frame_count),
        ):
            reported = row.get(field)
            if isinstance(reported, bool) or not isinstance(reported, int):
                raise ValueError(
                    f"evaluate_m2_bars: {where} の {field} {reported!r} が整数でない; "
                    "誤差モデルの母数を検算できない row を証拠にしない (fail-closed)"
                )
            if reported != expected:
                raise ValueError(
                    f"evaluate_m2_bars: {where} の {field} {reported} が凍結 spec から"
                    f"再計算した値 {expected} と不一致（正解フレーム数は spec の決定論的関数"
                    "なので、食い違いは drift か改竄のいずれか）(fail-closed)"
                )
        count = int(row["metrics"]["voiced_chroma_correct_frame_count"])
        if count > expected_voiced_frame_count:
            raise ValueError(
                f"evaluate_m2_bars: {where} の voiced_chroma_correct_frame_count "
                f"{count} が凍結 spec 由来の有声フレーム数 {expected_voiced_frame_count} "
                "を超える（有声かつ chroma 一致フレーム数は正解の有声フレーム数を"
                "超えられない）(fail-closed)"
            )
        # 母数は RCA の**分子そのもの**（RCA = 有声かつ chroma 一致フレーム数 / 有声
        # フレーム数）。上界だけを見ていると `RCA=1.0` かつ `count=1` のような矛盾した
        # 誤差モデルが bit 一致のまま publish できる（Codex P2）。分母は凍結 spec から
        # 再計算した値なので、比を復元して報告 RCA と突き合わせられる。
        if expected_voiced_frame_count > 0:
            rca = float(row["metrics"]["raw_chroma_accuracy"])
            implied = rca * expected_voiced_frame_count
            # **厳密な整数一致**を要求する（許容は浮動小数点誤差のみ）。以前の
            # 「1 フレーム許容」は誤りだった（Codex 指摘で撤回）: mir_eval の
            # `floor(x+0.5)` と本モジュールの `round(x)` が分岐するのは差がちょうど
            # 600+1200k cent の同点だけで、そこは残差 600 のため凍結 50 cent 許容の下で
            # **両式とも reject** する（mir_eval 実ソースで確認・比較演算子も同じ
            # strict `<`・nonzero フィルタも同一）。よって正当な 1 フレーム差は存在せず、
            # 許容 1 は「RCA=1.0 のまま count を total−1 に書き換えた」矛盾 row を
            # 通してしまう。fp 誤差は eps × count のオーダー（≪ 0.5）なので 0.5 を
            # 境界にすれば整数として厳密一致のみ受理する……つもりだったが、境界
            # ちょうど（RCA=(k−0.5)/N で diff が厳密に 0.5）の書き換えが通る
            # （Codex P2 第 21 巡）。mir_eval の分子は整数なので RCA×N は整数の
            # fp 誤差近傍（≦ N·eps ≈ 1e-13）にしか落ちない——許容は純粋な fp
            # マージン 1e-6 のみとする。
            if abs(count - implied) > 1e-6:
                raise ValueError(
                    f"evaluate_m2_bars: {where} の voiced_chroma_correct_frame_count "
                    f"{count} が raw_chroma_accuracy {rca!r} から復元される分子 "
                    f"{implied:.4f}（= RCA × 有声フレーム数 {expected_voiced_frame_count}）"
                    "と一致しない; 母数は RCA の分子そのものなので、この組は mir_eval が "
                    "返さない (fail-closed)"
                )


def _require_metrics_contract(
    category: str, metrics_list: List[Dict[str, Any]], *, tolerance_cents: float
) -> None:
    """`MelodyAccuracyResult` が保証する不変条件を evaluate 側で再検査する（fail-closed）。

    範囲表（`_METRIC_RANGES`）は連続指標しか見ておらず、誤差モデルの中心値
    （`median_cent_error`）・その母数（`voiced_chroma_correct_frame_count`）・row に
    転記された `tolerance_cents` が素通りしていた（Codex P2）。負の中央値や非整数の
    フレーム数、report の凍結 tolerance と食い違うネスト値のような **builder が
    絶対に出さない値** が verdict に写ることを防ぐ。
    """
    import math

    for repeat_idx, metrics in enumerate(metrics_list):
        where = f"category {category!r} repeat[{repeat_idx}]"

        count = metrics.get("voiced_chroma_correct_frame_count")
        if isinstance(count, bool) or not isinstance(count, int):
            raise ValueError(
                f"evaluate_m2_bars: {where} の voiced_chroma_correct_frame_count "
                f"{count!r} が整数でない (fail-closed)"
            )
        if count < 0:
            raise ValueError(
                f"evaluate_m2_bars: {where} の voiced_chroma_correct_frame_count "
                f"{count!r} が負 (fail-closed)"
            )

        median = metrics.get("median_cent_error")
        if median is not None:
            if isinstance(median, bool) or not isinstance(median, (int, float)):
                raise ValueError(
                    f"evaluate_m2_bars: {where} の median_cent_error {median!r} が "
                    "数値でも None でもない (fail-closed)"
                )
            if not math.isfinite(float(median)):
                raise ValueError(
                    f"evaluate_m2_bars: {where} の median_cent_error が非有限"
                    f"（{median!r}）(fail-closed)"
                )
            if float(median) < 0.0 or float(median) >= tolerance_cents:
                # chroma 一致フレームの残差なので、定義上 [0, tolerance) に入る。
                raise ValueError(
                    f"evaluate_m2_bars: {where} の median_cent_error {median!r} が "
                    f"定義域 [0, {tolerance_cents}) の外; chroma 一致フレームの残差として "
                    "ありえない値 (fail-closed)"
                )

        # count と median は「どちらも無い / どちらも有る」でなければならない
        # （builder は該当フレーム 0 件のとき median=None・count=0 を返す）。
        if (count == 0) != (median is None):
            raise ValueError(
                f"evaluate_m2_bars: {where} の median_cent_error {median!r} と "
                f"voiced_chroma_correct_frame_count {count!r} が矛盾; 該当フレーム 0 件なら "
                "median は None、非 0 なら median は数値 (fail-closed)"
            )

        # 導出フィールドは独立値としてではなく **関係** として検査する。
        # `octave_gap == RCA - RPA` を要求しないと、RPA 0.91 / RCA 0.10 / gap 0.0 の
        # ような不可能な誤差モデルが通り、max_octave_gap のバーも迂回される（Codex P2）。
        rpa = float(metrics["raw_pitch_accuracy"])
        rca = float(metrics["raw_chroma_accuracy"])
        gap = float(metrics["octave_gap"])
        if abs(gap - (rca - rpa)) > 1e-9:
            raise ValueError(
                f"evaluate_m2_bars: {where} の octave_gap {gap!r} が "
                f"raw_chroma_accuracy - raw_pitch_accuracy ({rca - rpa!r}) と一致しない; "
                "導出フィールドが独立に書き換えられた row を受理しない (fail-closed)"
            )
        # chroma 一致は pitch 一致の必要条件（オクターブ補正後の残差 < tolerance は
        # 補正前に一致していれば必ず成立する）なので、mir_eval は常に RCA >= RPA を
        # 返す。関係式だけを見ると RPA 0.91 / RCA 0.10 / gap -0.81 のように**整合的に
        # 書き換えた**不可能な row が残るため、符号そのものも要求する。
        if gap < -1e-9:
            raise ValueError(
                f"evaluate_m2_bars: {where} の raw_chroma_accuracy {rca!r} が "
                f"raw_pitch_accuracy {rpa!r} を下回る（octave_gap {gap!r}）; chroma 一致は "
                "pitch 一致の必要条件なので mir_eval はこの組を返さない (fail-closed)"
            )

        nested_tolerance = metrics.get("tolerance_cents")
        if nested_tolerance is None:
            raise ValueError(
                f"evaluate_m2_bars: {where} の metrics が tolerance_cents を欠く (fail-closed)"
            )
        nested_tolerance = _require_reported_number(
            nested_tolerance, where=where, field="metrics.tolerance_cents"
        )
        if nested_tolerance != float(tolerance_cents):
            raise ValueError(
                f"evaluate_m2_bars: {where} の metrics.tolerance_cents {nested_tolerance!r} が "
                f"report の凍結値 {tolerance_cents} と不一致; 指標がどの許容幅で算出されたか "
                "が report の申告と食い違う (fail-closed)"
            )


def _require_finite_metrics(category: str, metrics_list: List[Dict[str, Any]]) -> None:
    """閾値判定の前に、指標が有限な数値かつ定義域内であることを要求する（fail-closed）。

    `NaN` はあらゆる比較が False を返すため、`< min_rpa` も `> max_vfa` も成立せず
    「失敗が無い」＝ pass として通ってしまう（Codex P1）。JSON loader 側でも
    非有限リテラルを弾いているが、`evaluate_m2_bars` を直接呼ぶ経路のために
    ここでも独立に検査する（二重防御）。
    """
    import math

    for repeat_idx, metrics in enumerate(metrics_list):
        for field, (low, high) in _METRIC_RANGES.items():
            if field not in metrics:
                raise ValueError(
                    f"evaluate_m2_bars: category {category!r} repeat[{repeat_idx}] の "
                    f"metrics が {field} を欠く (fail-closed)"
                )
            value = metrics[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"evaluate_m2_bars: category {category!r} repeat[{repeat_idx}] の "
                    f"{field} {value!r} が数値でない (fail-closed)"
                )
            if not math.isfinite(float(value)):
                raise ValueError(
                    f"evaluate_m2_bars: category {category!r} repeat[{repeat_idx}] の "
                    f"{field} が非有限（{value!r}）; 未定義の測定値は比較が常に False に "
                    "なり pass を偽造するため拒否する (fail-closed)"
                )
            if not (low <= float(value) <= high):
                raise ValueError(
                    f"evaluate_m2_bars: category {category!r} repeat[{repeat_idx}] の "
                    f"{field} {value!r} が定義域 [{low}, {high}] の外 (fail-closed)"
                )


def _require_report_schema(reports: List[Dict[str, Any]]) -> None:
    """各 report が既知の `schema_version` を名乗ることを要求する（fail-closed）。

    bars には schema discriminator があるのに report には無く、フォーマットが変わっても
    現在検査しているフィールドが残っていれば evaluate が旧セマンティクスで解釈できて
    しまう（Codex P2）。未知バージョンも欠落も、意味論の食い違いを黙って飲まないため
    等しく拒否する。
    """
    for idx, report in enumerate(reports):
        version = report.get("schema_version")
        if version is None:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] が schema_version を欠く; どの世代の "
                f"report 形式か判別できない artifact を評価しない "
                f"（期待値 {_EXPECTED_REPORT_SCHEMA!r}・fail-closed）"
            )
        if version != _EXPECTED_REPORT_SCHEMA:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] の schema_version {version!r} が "
                f"未知; 期待値は {_EXPECTED_REPORT_SCHEMA!r}（旧/新形式を現行セマンティクス "
                "で解釈しない・fail-closed）"
            )


def _require_publishable_runs(reports: List[Dict[str, Any]]) -> None:
    """注入 runner で作られた report を verdict の証拠にしない（fail-closed）。

    `run_accuracy(route_runner=...)` は抽出器非依存インターフェースの検証用の口で、
    呼び出し側は正解 F0 と真の sha256 形式の任意 hash を返せる。それでも row には
    登録済み route 名と既定の generator digest が載るため、他の全チェックを通過して
    **CREPE を一度も走らせずに** S_direct pass を publish できてしまう（Codex P1）。
    唯一の確実な区別は「実抽出器を使ったか」なので、run 側が刻んだ事実を関所にする。

    `route_runner_injected` フィールド自体が欠けている report も拒否する（この規律
    より前に作られた、あるいは手組みの report を黙って通さない）。
    """
    for idx, report in enumerate(reports):
        injected = report.get("route_runner_injected")
        if injected is None:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] が route_runner_injected を欠く; "
                "実抽出器で測ったことを確認できない report を証拠にしない (fail-closed)"
            )
        if injected:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] は route_runner 注入で作られている; "
                "フェイク抽出器の出力は publish 可能な実測記録として扱わない "
                "(fail-closed)"
            )
        preloaded = report.get("preloaded_seed_modules")
        if preloaded is None:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] が preloaded_seed_modules を欠く; "
                "pin が実行バイトを束縛していたか確認できない (fail-closed)"
            )
        if preloaded:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] は閉包モジュールが事前ロード済みの "
                f"プロセスで作られている {sorted(preloaded)}; メモリ上のコードと pin した "
                "ディスク bytes が食い違いうるため publish 可能な実測にしない "
                "（素の CLI 実行で測り直すこと・fail-closed）"
            )
        loaded_as_main = report.get("harness_loaded_as_main")
        if loaded_as_main is None:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] が harness_loaded_as_main を欠く; "
                "ハーネス自身がソースから実行されたか確認できない report を証拠に "
                "しない (fail-closed)"
            )
        if loaded_as_main is not True:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] は import 経由で実行されたハーネスの "
                "report; 直接パスの script 実行だけが .pyc を経由せずソースから実行 "
                "される（stale bytecode の余地を残さない）ため、publish 可能な実測は "
                "素の CLI 起動に限る (fail-closed)"
            )
        pre_bound = report.get("pre_bound_scorer_native_mappings")
        if pre_bound is None:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] が pre_bound_scorer_native_mappings を "
                "欠く; scorer ネイティブが束縛前にロード済みでなかったか確認できない "
                "report を証拠にしない (fail-closed)"
            )
        if pre_bound:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] は scorer ネイティブが束縛前に既に "
                f"プロセスへロード済みだった {sorted(pre_bound)}; mmap 済み実体は disk "
                "hash で検出できない（TOCTOU: mmap → 差し替え → hash）ため、publish "
                "可能な実測にしない（素の CLI 実行で測り直すこと・fail-closed）"
            )
        non_standard_hooks = report.get("non_standard_import_hooks")
        if non_standard_hooks is None:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] が non_standard_import_hooks を欠く; "
                "find_spec の場所解決を改変しうる finder が無かったか確認できない "
                "report を証拠にしない (fail-closed)"
            )
        if non_standard_hooks:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] は非標準の import hook が束縛前に"
                f"存在した {sorted(non_standard_hooks)}; find_spec の origin は無傷でも"
                "実行される bytes が差し替えられうるため、publish 可能な実測にしない "
                "（素の CLI 実行で測り直すこと・fail-closed）"
            )
        load_time_mismatches = report.get("scorer_load_time_hash_mismatches")
        if load_time_mismatches is None:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] が scorer_load_time_hash_mismatches "
                "を欠く; scorer .py の swap-and-restore 痕跡が無かったか確認できない "
                "report を証拠にしない (fail-closed)"
            )
        if load_time_mismatches:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] は scorer .py の swap-and-restore "
                f"痕跡を記録している {sorted(load_time_mismatches)}; compile された "
                "source bytes が束縛時点の期待と食い違うため、publish 可能な実測に "
                "しない（素の CLI 実行で測り直すこと・fail-closed）"
            )
        _require_scorer_compile_observation_covers_imported_modules(
            report, context=f"reports[{idx}]", exception_cls=ValueError
        )
        sys_flags_optimize = report.get("sys_flags_optimize")
        if sys_flags_optimize is None:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] が sys_flags_optimize を欠く; "
                "-O/-OO 実行でなかったか確認できない report を証拠にしない (fail-closed)"
            )
        if sys_flags_optimize != 0:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] は -O/-OO 実行（sys_flags_optimize="
                f"{sys_flags_optimize!r}）; mir_eval の assert ガード（`validate_voicing`/"
                "`validate` 等）が除去された経路を publish 可能な実測にしない "
                "(fail-closed)"
            )


def _require_homogeneous_scorer(reports: List[Dict[str, Any]]) -> Dict[str, Any]:
    """全 report が同一のスコアラー閉包で測られたことを要求する（fail-closed）。

    `_SCORER_RUNTIME_PACKAGES`（mir_eval + scipy + numpy、Codex P1）は上限の無い
    バージョン制約で運用されており、別リリースは RPA/RCA/VR/VFA の定義・境界処理・
    数値実装が変わりうる。`generator_code_sha256` は first-party 閉包なので
    third-party の差を捉えない——よってスコアラー閉包自身の pin を report レベルで
    突き合わせる。mir_eval だけでなく mir_eval が直接 import して実行する
    scipy/numpy まで揃えないと、patch された数値実装で測った row を同一 stack の
    repeats と誤認する（Codex P1）。

    相互一致だけでは足りない: 全 report が同じ捏造/stale pin を名乗れば通り、verdict
    はその pin を転記するので「一度も走っていないスコアラー実装」を主張する成果物が
    publish できる（Codex P2 第 23 巡）。抽出器 pin の `_require_execution_evidence`
    と同じく、**評価環境から use_cache=False で再計算した実スコアラー pin** との
    一致を publish 条件にする（report 内に無い・書き換えられない証拠）。測り直し
    検証は評価環境の mir_eval/scipy/numpy で行われるため、この照合により「metrics を
    検証した実装」と「verdict が名乗る実装」が同一であることが保証される。
    """
    pins: List[Tuple[Tuple[str, str], ...]] = []
    for idx, report in enumerate(reports):
        pins.append(_validated_scorer_pin_tuple(report, context=f"reports[{idx}]"))
    if len(set(pins)) > 1:
        raise ValueError(
            f"evaluate_m2_bars: reports のスコアラー閉包 pin が repeats 間で不一致 "
            f"{sorted(set(pins))}; 別の数値実装（mir_eval/scipy/numpy のいずれか）で "
            "測った run を同一 stack の repeats と見なさない (fail-closed)"
        )
    # Codex 15 巡目 P2: 任意閉包メンバーの presence 比較を observed-closure ベースへ
    # 統一するため、評価環境側の再計算より前に閉包を観測可能にする（docstring 参照）。
    _ensure_scorer_optional_closure_observed()
    expected = _validated_scorer_pin_tuple(
        _scorer_pins(use_cache=False), context="評価環境の再計算スコアラー pin"
    )
    if pins[0] != expected:
        raise ValueError(
            f"evaluate_m2_bars: reports のスコアラー閉包 pin {pins[0]!r} が評価環境から "
            f"再計算した実スコアラー閉包 pin {expected!r} と一致しない; この環境の "
            "mir_eval/scipy/numpy で測られていない（または pin を捏造した）row を、その "
            "pin を名乗る verdict の証拠にしない (fail-closed)"
        )
    result: Dict[str, Any] = {}
    for name, pin in zip(_SCORER_RUNTIME_PACKAGES, pins[0]):
        is_optional = name in _SCORER_RUNTIME_PACKAGES_OPTIONAL
        if is_optional and pin == _SCORER_ABSENT_OPTIONAL_PIN_MARKER:
            # verdict にも比較用の内部マーカーをそのまま漏らさない——report と同じ
            # 「None + closure_state」の正直記録に揃える（Codex 11 巡目 P1-B）。
            result[f"{name}_version"] = None
            result[f"{name}_code_sha256"] = None
            result[f"{name}_dist_native_sha256"] = None
            result[f"{name}_closure_state"] = "absent"
            continue
        version, code, dist_native = pin
        result[f"{name}_version"] = version
        result[f"{name}_code_sha256"] = code
        result[f"{name}_dist_native_sha256"] = dist_native
        if is_optional:
            result[f"{name}_closure_state"] = "present"
    return result


def _require_homogeneous_numeric_runtime_config(reports: List[Dict[str, Any]]) -> Dict[str, Any]:
    """全 report が同一の実行時数値構成（H7）で測られたことを要求する（fail-closed）。

    BLAS/LAPACK のスレッド分割（`OPENBLAS_NUM_THREADS` 等）・CPU ターゲット選択
    （`OPENBLAS_CORETYPE`）・numpy の CPU 機能無効化（`NPY_DISABLE_CPU_FEATURES`）・
    MKL の数値再現モード（`MKL_CBWR`）は、**バイト列は完全に不変のまま**縮約順序を
    変え、結果の数値を変えうる（実測済みの事故: `median_cent_error` が
    1.352838 ↔ 1.353400 でバッチ間往復）。`_repeats_bit_identical` の bit 一致は
    「同一バッチ・同一環境・同一スレッド数」でしか成立しない条件付きの性質であり、
    この構成が repeats 間で食い違っていれば、その bit 一致は「決定論の証拠」を
    僭称している——`_require_homogeneous_scorer`（スコアラー閉包 pin）と同じ形の
    同質性検査をここでも適用する。

    **統制はしない・記録の同質性だけを要求する**: `OPENBLAS_NUM_THREADS=1` 等を
    強制する統制（holes.md 修正案 (a)）は、numpy import 前の環境変数設定という
    より広い変更を要し、本コミットの対応範囲外——ここでは repeats 間の記録が
    一致することだけを要求し、バッチをまたぐ構成の違い（例えば今回の verdict と
    別の verdict の間で `cpu_count` が違う）までは覆わない（正直会計）。バー判定
    には使わない（`_repeats_bit_identical` のバー適用パスに割り込まない）。
    """
    configs: List[Any] = []
    for idx, report in enumerate(reports):
        config = report.get("numeric_runtime_config")
        if config is None:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] が numeric_runtime_config を欠く; "
                "実行時数値構成が repeats 間で揃っていたか確認できない report を証拠に"
                "しない (fail-closed)"
            )
        configs.append(config)
    first = configs[0]
    for idx, config in enumerate(configs[1:], start=1):
        if config != first:
            raise ValueError(
                f"evaluate_m2_bars: reports の numeric_runtime_config が repeats 間で"
                f"不一致（reports[0]={first!r} vs reports[{idx}]={config!r}）; bit 一致が"
                "「同一バッチ・同一環境」の条件を満たさない状態で決定論の証拠として"
                "扱わない (fail-closed)"
            )
    return first


def _repeats_bit_identical(metrics_list: List[Dict[str, Any]]) -> bool:
    """repeats の metrics が完全一致か（bars の `repeats_min` 契約 = 決定論確認）。"""
    canonical = {json.dumps(m, sort_keys=True) for m in metrics_list}
    return len(canonical) <= 1


def _require_frozen_est_voicing_floor(
    reports: List[Dict[str, Any]], bar_block: Dict[str, Any]
) -> float:
    """各 report の `est_voiced_confidence_floor` が凍結値と厳密一致することを要求する。

    有声判定閾値は VFA/VR/OA を直接動かすので、緩い閾値で測った row に凍結
    `max_vfa` を適用すると「バーファイルを触らずにバーを緩める」のと同じ結果になる
    （`_require_frozen_tolerance` と同型の関所）。閾値を欠く report は、そもそも
    CREPE の confidence を推定 voicing へ変換していない旧世代の run である可能性が
    あるため受理しない。
    """
    frozen = float(bar_block["est_voiced_confidence_floor"])
    for idx, report in enumerate(reports):
        reported = report.get("est_voiced_confidence_floor")
        if reported is None:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] が est_voiced_confidence_floor を欠く; "
                "推定 voicing をどの閾値で決めたか不明な row にバーを適用しない "
                "(fail-closed)"
            )
        reported = _require_reported_number(
            reported, where=f"reports[{idx}]", field="est_voiced_confidence_floor"
        )
        if reported != frozen:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] の est_voiced_confidence_floor "
                f"{reported!r} が凍結値 {frozen} と不一致; 別の有声判定閾値で測った row に "
                "凍結バーを適用しない (fail-closed)"
            )
    return frozen


def _require_frozen_tolerance(reports: List[Dict[str, Any]], bar_block: Dict[str, Any]) -> float:
    """各 report の `tolerance_cents` が凍結値と厳密一致することを要求する（fail-closed）。

    `run_accuracy` は診断用に `tolerance_cents` の override を受けるが、緩い許容幅で
    測った row にバーを適用すると、**バーファイルを一切触らずに**「実測後にバーを
    緩める」のと同じ結果が得られてしまう（例: 500 cent ずれた推定を 600 cent 許容で
    測れば min_rpa を満たす）。`bars_sha256` は override では変わらないので、
    ここで報告値そのものを突き合わせるのが唯一の関所になる（Codex P1 指摘・
    設計 §4 の一方向規律）。
    """
    frozen = float(bar_block.get("tolerance_cents", DEFAULT_TOLERANCE_CENTS))
    for idx, report in enumerate(reports):
        reported = report.get("tolerance_cents")
        if reported is None:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] が tolerance_cents を欠く; "
                "どの許容幅で測ったか不明な row にバーを適用しない (fail-closed)"
            )
        reported = _require_reported_number(
            reported, where=f"reports[{idx}]", field="tolerance_cents"
        )
        if reported != frozen:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] の tolerance_cents {reported!r} が凍結値 "
                f"{frozen} と不一致; 別の許容幅で測った row に凍結バーを適用しない "
                "（バーファイルを触らずにバーを緩める経路を塞ぐ・fail-closed）"
            )
    return frozen


def _require_matching_generator_code(reports: List[Dict[str, Any]]) -> str:
    """report の `generator_code_sha256` を 3 段で照合する（fail-closed）。

    `run_melody_observability.evaluate_m1_real_go_bar` と同じ規律:

    1. 各 report が非空 str の `generator_code_sha256` を持つ（provenance 欠落を拒否）
    2. repeats 間で一致する（別 checkout で測った run を 1 つの repeats 束に混ぜない）
    3. 現 checkout の `_generator_code_sha256()` と一致する（指標算出・route 選択・
       ミックス式が変わった後の stale report を、新しいバー適用の証拠として通さない）

    3 を欠くと、手組み report や旧コード由来の row をそのまま「バーを満たした
    実測」として公開できてしまう（M1-real PR #220 で確定した規律）。
    """
    digests: List[str] = []
    for idx, report in enumerate(reports):
        digest = report.get("generator_code_sha256")
        if not digest or not isinstance(digest, str):
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] lacks a non-empty string "
                "generator_code_sha256; row を産出した generator コードの provenance を "
                "欠く report は dated record として扱えない (fail-closed)"
            )
        digests.append(digest)
    if len(set(digests)) > 1:
        raise ValueError(
            "evaluate_m2_bars: reports の generator_code_sha256 が repeats 間で不一致 "
            f"{sorted(set(digests))}; 別 checkout の generator コードで測った run を "
            "1 つの repeats 束として扱えない (fail-closed)"
        )
    current = _generator_code_sha256()
    if digests[0] != current:
        raise ValueError(
            f"evaluate_m2_bars: reports の generator_code_sha256 {digests[0]!r} が現 "
            f"checkout の {current!r} と不一致; 指標算出・route 選択・ミックス式が "
            "変わった後の stale report にバーを適用しない — dated 再実測すること "
            "(fail-closed)"
        )
    return digests[0]


def _require_eval_cell_store_disjoint_from_run_stores(
    reports: "List[ReportArtifact]", eval_cell_store: Path
) -> None:
    """C2: `store_B` が**提出 report を産んだ `store_A`** と重ならないことを要求する。

    CLI の `--cell-store` × `--eval-cell-store` 検査だけでは足りない（PR #240 Codex P1）:
    evaluate phase は `--cell-store` そのものを拒否するので、あの条件分岐は**本番の
    呼び出しでは一度も走らない**。一方 run report は `cell_store_relative` を刻んでいる
    ので、`--eval-cell-store` にその値を渡すことは実際にできてしまう——そうすると
    測り直しの子は鍵 `(category, level, entry_id, repeat_index)` が一致する run のセルを
    そのまま resume し、「独立に測り直して bit 一致」の検証が**自分自身との比較**に
    化ける（F2 で塞いだ穴が別の入口から開く）。

    したがって関所は「CLI に 2 つの引数が並んだとき」ではなく「評価対象 report が
    名乗る store と重なるとき」に置く。比較は **resolve 後**（`..`・symlink で
    抜けられる形にしない）。

    `cell_store_relative` が `None`（repo 外の store で走った run）は、重なりを
    立証も反証もできないので **fail-closed** にする——「復元できないから素通し」は
    このゲートを名目だけにする。
    """
    eval_root = Path(eval_cell_store).resolve()
    for index, report in enumerate(reports):
        if "cell_store_relative" not in report:
            continue  # `--cell-store` を使わずに測った run（重なる store が存在しない）
        declared = report.get("cell_store_relative")
        if not isinstance(declared, str) or not declared:
            raise ValueError(
                f"evaluate_m2_bars: reports[{index}] は run 用セルストアを使ったと名乗って "
                f"いるが、そのパスを復元できない（cell_store_relative={declared!r}; repo 外の "
                "store で走った run）; --eval-cell-store がその store と重なっていないことを "
                "立証できないまま測り直しを起こさない (fail-closed)"
            )
        run_root = (ROOT / declared).resolve()
        if run_root == eval_root:
            raise ValueError(
                f"evaluate_m2_bars: --eval-cell-store {eval_root} が reports[{index}] を産んだ "
                f"run のセルストアと同じパス; 測り直しの子が run のセルをそのまま resume し、"
                "「独立に測り直して bit 一致」の検証が自分自身との比較に化ける (fail-closed)"
            )
        if run_root in eval_root.parents or eval_root in run_root.parents:
            raise ValueError(
                f"evaluate_m2_bars: --eval-cell-store {eval_root} と reports[{index}] を産んだ "
                f"run のセルストア {run_root} が入れ子になっている; 独立であるべき 2 つの計算が "
                "同じ木を共有し、走査・掃除で互いを汚染しうる (fail-closed)"
            )


def _thread_pinning_contract_from_reports(
    reports: "List[ReportArtifact]",
) -> Dict[str, Any]:
    """設計判断 D-3: 評価対象の run が名乗るスレッド固定を検証し、**測り直しの契約**として返す。

    `--pin-threads` の下では測り直しの子が `OMP=1 / MKL=1 / torch=1` で走る。run 側が
    固定されていなければ、その子が産む row は提出 row と bit 一致しない——publish 条件が
    「独立に測り直して bit 一致」である以上、ここが割れると帯の本測定が丸ごと通らない
    （HANDOFF §3.1: 3 点目を欠くと stem の `stem_sha256` が run 間で変わる）。

    契約は**評価対象の report から導く**（評価器が自分で `_apply_thread_pinning()` を
    呼んで比べるのではない）。理由は 2 つ:

    - 束縛時点と使用時点を一致させる。子へ渡すべき条件は「提出 row を産んだ run の
      条件」であって、評価器プロセスのたまたまの状態ではない。
    - 評価器自身は**何も測らない**。ここで固定のために torch を import すると、評価器
      プロセスの import 集合が動き、`_require_scorer_compile_observation_covers_imported_modules`
      など「素の CLI 実行であること」を検査する自己ゲート群を余計に揺らす。

    導いた契約は `_run_external_verification_in_fresh_process` が子へ渡し、**子 report の
    申告と再照合する**——「宣言されているが検証されていない」値を作らない。
    """
    declarations: "List[Any]" = []
    for index, report in enumerate(reports):
        declared = report.get("thread_pinning")
        if declared is None:
            raise ValueError(
                f"evaluate_m2_bars: --pin-threads で評価しているが reports[{index}] が "
                "thread_pinning を名乗っていない; スレッド未固定の run が産んだ row は "
                "固定済みの測り直しと bit 一致しないため publish 条件を満たせない "
                "（D-3: 固定は run と evaluate で同一でなければならない）(fail-closed)"
            )
        declarations.append(declared)
    contract = declarations[0]
    for index, declared in enumerate(declarations):
        if declared != contract:
            raise ValueError(
                f"evaluate_m2_bars: reports[{index}] の thread_pinning {declared!r} が "
                f"reports[0] の {contract!r} と不一致; 別スレッド条件で測った run を同じ "
                "測定の反復として評価しない (fail-closed)"
            )
    # **形も要求する。** 「固定した」と名乗るだけの申告（`OMP=8` など）を契約として
    # 子へ配ると、固定されていない条件が「固定として検証済み」に化ける。
    if contract.get("OMP_NUM_THREADS") != "1" or contract.get("MKL_NUM_THREADS") != "1":
        raise ValueError(
            f"evaluate_m2_bars: report の thread_pinning {contract!r} が"
            '3 点固定の形をしていない（OMP_NUM_THREADS / MKL_NUM_THREADS がともに "1" '
            "であること）; "
            "固定を名乗るだけの申告を測り直しの契約にしない (fail-closed)"
        )
    if contract.get("torch_num_threads") not in (1, _ENV_DIGEST_ABSENT_MARKER):
        raise ValueError(
            f"evaluate_m2_bars: report の thread_pinning の torch_num_threads "
            f"{contract.get('torch_num_threads')!r} が 1 でも「未導入」マーカーでもない; "
            "3 点目が効いていない run の row を固定済みとして扱わない (fail-closed)"
        )
    return copy.deepcopy(contract)


def evaluate_m2_bars(
    reports: "List[ReportArtifact]",
    bars: BarsArtifact,
    *,
    bars_sha256: str,
    specs_path: Path = SPECS_PATH,
    bars_path: Path = BARS_PATH,
    m2e_bars_path: Path = M2E_BARS_PATH,
    external_manifest_path: Optional[Path] = None,
    external_fixtures_path: Path = EXTERNAL_FIXTURES_PATH,
    eval_cell_store: Optional[Path] = None,
    workers: int = 1,
    pin_threads: bool = False,
) -> Dict[str, Any]:
    """n>=`repeats_min` の run report に凍結バーを機械適用する（設計 §4/§6）。

    M2c: `reports` に外部素材カテゴリ（`kind: "external"`、現在は V_direct のみ）が
    含まれ、かつその測り直し（`_reverify_category_measurement`）が実体で動く場合
    （テストの `_reverify_category_measurement` monkeypatch が無い場合）、
    `external_manifest_path` の指定が必須（未指定は fail-closed）。

    - 各 report は非空 str の `run_id` を持ち、report 間で相互に distinct
      でなければならない（コピー由来の水増し repeats を fail-closed で拒否）。
    - 各 report の `recorded_utc` は UTC dated record として検証する。
    - 各 report は本 verdict が適用する `bars_sha256` と一致していなければ
      ならない（別バー世代の report を混ぜない）。
    - 各 report の `generator_code_sha256` は非空 str・repeats 間で一致・かつ
      現 checkout の `_generator_code_sha256()` と一致していなければならない
      （`run_melody_observability.evaluate_m1_real_go_bar` と同じ 3 段照合。
      指標算出・route 選択・ミックス式が変わった後の stale report を、新しい
      バー適用の証拠として通さない。手組み report も同様に弾かれる）。
    - S_direct: 全 measured repeats が `min_rpa`/`max_vfa` を満たせば pass。
      1 本でも `unavailable` なら（repeats_min 未達として）verdict は出さず
      `status="insufficient_repeats"`。
    - 各 report の `tolerance_cents` は凍結値と厳密一致でなければならない
      （`_require_frozen_tolerance`）。
    - measured rows は抽出器/分離器の pin を完備し、repeats 間で同一 model stack で
      なければならない（`_require_homogeneous_model_stack`）。
    - `reports` は `load_report()` が返す `ReportArtifact` の列。verdict の
      `report_pins` は **実際に評価した bytes** から本関数が導出する（呼び出し側の
      pin を信用しない。後から report が編集・差し替えられたことを検出できる）。
    - S_fullstack: バーが空（`{}`）なので判定せず、`status="diagnostic_only"`
      として計測値のみ記録する（設計 §8: S_fullstack の低値を理由に crepe を
      責めない）。
    - **M2e（`V_remix_real_*`）は合否を出さない。** `gate_level` ではバーを当てて
      `bar_satisfied` / `failures` に証拠を残すが `status="census_pending"` に留める
      ——設計 §6.2/§11 が「全 1280 セル（4 水準 × 2 アーム）の census が揃うまで
      帯の判定を出さない」を要求する一方、1 回の evaluate は構造上 1 水準しか
      見ないため、このコールから census を立証できない（帯の判定は r6/r7 の
      水準横断集計の仕事）。`gate_level` 以外は従来どおり `level_record_only`。

    C2（`eval_cell_store` = `store_B`・rev.6 §8.9.2-(1)）: 与えると外部素材カテゴリの
    測り直しが**evaluate 専用の**セルストアへチェックポイントし、中断から復帰できる。
    run が使った `store_A` はこの経路に一切現れない（重なりは CLI が resolve 後の
    パス関係で fail-closed に拒否する）。**publish の独立性は store を分けることで
    保たれるのであって、復帰できないことで保たれるのではない。**

    C3（`workers` = `P`・rev.6 §8.9.2-(2)）: 外部素材カテゴリの測り直しの子プロセスを
    最大 `P` 本まで同時に起こす。設計判断 D-2 の非対称に注意——`--workers` は
    **run phase では宣言値（記録のみ）/ evaluate phase では実効並列度**である。

    D-3（`pin_threads`）: 立てると (a) 評価対象の全 report が**同じ**スレッド固定を
    名乗ることを要求し（`_thread_pinning_contract_from_reports`）、(b) その契約を測り直しの
    子へ伝え、(c) 子 report の申告と再照合する。run と evaluate でスレッド条件が食い違うと
    bit 一致（= publish 条件）が壊れるため。**評価器自身は何も測らないので、評価器の
    プロセスにスレッド固定を適用することはしない**（余計な import で自己ゲートを揺らさない）。

    **3 つとも既定（`None`/`1`/`False`）では verdict は 1 バイトも変わらない。**
    """
    # digest と parsed data の束縛を検証してから閾値を読む。素の dict（手組み・load 後に
    # 変異させた mapping）は受理しない——閾値を書き換えたまま元の凍結 digest を名乗る
    # verdict を publish させないため（Codex P2）。
    if not isinstance(bars, BarsArtifact):
        raise ValueError(
            "evaluate_m2_bars: bars は load_bars() が返す BarsArtifact でなければならない"
            f"（受け取った型: {type(bars).__name__}）; parsed 閾値と digest が切り離された "
            "入力は評価しない (fail-closed)"
        )
    # **評価プロセス自身**にも run と同じ preload ゲートを適用する（Codex P1）。
    # 監視対象モジュールが本ハーネスより先に import 済みのプロセスでは、checkout を
    # 更新してからハーネスを import すると「load 時 digest は新ディスクを hash する
    # 一方、評価はキャッシュ済みの旧モジュールで走る」ため、
    # `_require_unchanged_since_load()`（ディスク不変の検査）では捕まらない。
    # その verdict の `evaluator_code_sha256` は実行されていないコードを名乗る。
    if _PRELOADED_SEED_MODULES:
        raise RuntimeError(
            f"evaluate_m2_bars: 監視対象モジュールが本ハーネスより先に import 済み "
            f"（{_PRELOADED_SEED_MODULES}）; メモリ上のコードと load 時に hash した "
            "ディスク bytes の一致を保証できないプロセスから verdict を publish しない "
            "— 素の CLI から評価し直すこと (fail-closed)"
        )
    # 評価器自身にも scorer ネイティブの pre-bind ゲートを適用する（Codex P1 7 巡目・
    # `_PRELOADED_SEED_MODULES` と同型）。正規の fresh CLI では `_scorer_pins()` の
    # 束縛が numpy/scipy の import より先に走るため、`_PRE_BOUND_SCORER_NATIVE_MAPPINGS`
    # は必ず空になる。非空は「束縛前に既に数値バックエンドがロード済みだった」証拠で、
    # mmap 済み実体は disk hash では検出できない（TOCTOU: mmap → 差し替え → hash）。
    if _PRE_BOUND_SCORER_NATIVE_MAPPINGS:
        raise RuntimeError(
            f"evaluate_m2_bars: scorer ネイティブが束縛前に既にロード済みだった "
            f"（{_PRE_BOUND_SCORER_NATIVE_MAPPINGS}）; mmap 済み実体は disk hash で検出 "
            "できず、pin が実行中の実装を代表する保証がないプロセスから verdict を "
            "publish しない — 素の CLI から評価し直すこと (fail-closed)"
        )
    # 評価器自身にも swap-and-restore 検出（P1-B）のゲートを適用する。他の 3 つの
    # 自己ゲートと異なり、この一覧は「load 時 1 回だけ確定」の凍結タプルではなく
    # **ライブ**の可変リストを直接参照する——`_SCORER_LOAD_TIME_HASH_MISMATCHES` は
    # 定義上、束縛完了「後」に実際の compile イベントが起きて初めて増える値なので、
    # 束縛時点で凍結すると恒常的に空になり無意味になる（他の 3 つは逆に「束縛時点の
    # 状態」を問うのが正しいので凍結タプルのままでよい）。評価器プロセス自身が
    # ここまでの生涯で一度でも swap-and-restore の痕跡を記録していれば、その計測を
    # 信用しない。
    if _SCORER_LOAD_TIME_HASH_MISMATCHES:
        raise RuntimeError(
            f"evaluate_m2_bars: 評価器プロセス自身が scorer .py の swap-and-restore "
            f"痕跡を記録済み（{_SCORER_LOAD_TIME_HASH_MISMATCHES}）; compile された "
            "source bytes が束縛時点の期待と食い違うプロセスから verdict を publish "
            "しない — 素の CLI から評価し直すこと (fail-closed)"
        )
    # H16（セルフレビュー第二弾）: 評価器プロセス自身についても、compile 観測集合が
    # 「今 import 済みの scorer .py」の期待集合を覆っていることを要求する——上の
    # ゲートと同じくライブの可変集合を直接参照する（束縛時点で凍結すると無意味）。
    missing_compile_observations = sorted(
        set(_scorer_compile_expected_paths()) - _SCORER_COMPILE_OBSERVED_PATHS
    )
    if missing_compile_observations:
        raise RuntimeError(
            "evaluate_m2_bars: 評価器プロセス自身で import 済みの scorer .py のうち "
            f"audit hook が compile を観測しなかったものがある "
            f"{missing_compile_observations}; swap-and-restore 検出機構が機能して"
            "いない疑いがあり、覆えないものを覆ったと主張しない — 素の CLI から"
            "評価し直すこと (fail-closed)"
        )
    # 評価器自身にも非標準 import hook のゲートを適用する（セルフレビュー H3・
    # `_PRELOADED_SEED_MODULES` と同型）。正規の fresh CLI では標準 3 finder +
    # `_distutils_hack`（実測で確認済みの許容例外）しか無いはずで、それ以外が
    # 存在すれば find_spec の場所解決が改変されている疑いを持つ。
    if _NON_STANDARD_IMPORT_HOOKS:
        raise RuntimeError(
            f"evaluate_m2_bars: 非標準の import hook が束縛前に存在した "
            f"（{_NON_STANDARD_IMPORT_HOOKS}）; find_spec の場所解決が改変されている"
            "疑いがあり、pin が実行中の実装を代表する保証がないプロセスから verdict を "
            "publish しない — 素の CLI から評価し直すこと (fail-closed)"
        )
    # 評価器自身の実行形態（-O/-OO）も検査する（セルフレビュー H9）。third-party の
    # assert ガード（mir_eval の `validate_voicing`/`validate` 等）が除去された状態で
    # 評価しても、report 側の row の正当性検証が同じ弱化を受けている可能性がある。
    if sys.flags.optimize != 0:
        raise RuntimeError(
            f"evaluate_m2_bars: 評価器プロセスが -O/-OO 実行（sys.flags.optimize="
            f"{sys.flags.optimize!r}）; 数値バックエンド・mir_eval の assert ガードが"
            "除去された状態で verdict を publish しない — 素の CLI（-O/-OO 無し）から"
            "評価し直すこと (fail-closed)"
        )
    # 評価器**自身**にも直接ソース実行を要求する（Codex P2 第 37 巡）。preload ゲートは
    # 依存モジュールしか覆わず、`python -m run_melody_accuracy --evaluate` は評価器
    # モジュール自身を stale .pyc から実行しうる——その場合、report が現行ソースでも
    # 旧評価ロジックがバーを適用し、verdict は現行ディスクの evaluator_code_sha256 を
    # 名乗る（実行後のソース再 hash では検出できない）。
    if not _HARNESS_LOADED_AS_MAIN:
        raise RuntimeError(
            "evaluate_m2_bars: 評価器が直接パスの script 実行でない（import / python -m "
            "経由）; 評価器自身の stale .pyc の余地を残す実行形態から verdict を publish "
            "しない — python scripts/run_melody_accuracy.py --evaluate ... で評価すること "
            "(fail-closed)"
        )
    bars_data = bars.verify(bars_sha256)
    bar_block = bars_data["m2_accuracy_bars"]
    repeats_min = int(bar_block.get("repeats_min", 2))

    # report も同じ single-read 束縛を要求し、pin は評価した bytes から導出する。
    for idx, artifact in enumerate(reports):
        if not isinstance(artifact, ReportArtifact):
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] は load_report() が返す ReportArtifact "
                f"でなければならない（受け取った型: {type(artifact).__name__}）; parsed "
                "内容と digest が切り離された入力は評価しない (fail-closed)"
            )
    report_artifacts: "List[ReportArtifact]" = list(reports)
    report_pins = [artifact.pin() for artifact in report_artifacts]
    reports = [artifact.verify() for artifact in report_artifacts]

    if not reports:
        raise ValueError("evaluate_m2_bars: reports must be non-empty")

    # 適用するバーの **最新の登録時点**（初回凍結 + amendments の最大値）。これより
    # 前に測ったと申告する report は、その閾値がまだ存在しなかった時点の観測なので
    # 「事前登録済みバーの下での実測」を名乗れない——実測を見てから選んだ閾値を
    # 事前登録として提示する経路になる（Codex P2）。
    latest_registration = _parse_registered_utc(
        bars_data.get("registered_utc"), where="m2_accuracy_bars.yaml"
    )
    for amendment in bars_data.get("amendments") or []:
        amended = _parse_registered_utc(
            amendment.get("registered_utc"), where="m2_accuracy_bars.yaml amendments"
        )
        if amended > latest_registration:
            latest_registration = amended

    run_ids: List[str] = []
    started_by_index: List[Tuple[int, datetime]] = []
    for idx, report in enumerate(reports):
        recorded = _parse_recorded_utc(report.get("recorded_utc"), where=f"reports[{idx}]")
        # 完了時刻だけでは「登録前に測り始め、登録後に完了した」run が通る（Codex P2）。
        # 測定は開始から完了まで一貫して登録済みバーの下で行われた必要があるので、
        # `started_utc` にも同じ契約 + 登録時点以降 + 完了時刻以前を要求する。
        started = _parse_recorded_utc(
            report.get("started_utc"), where=f"reports[{idx}]", field="started_utc"
        )
        # チェックポイントから resume した report は、**今回の起動時刻より前に測られた
        # セル**を含む。`started_utc` だけを見ると、事前登録より前に採った測定を後の
        # run 経由で「登録後の測定」として通せてしまう（洗浄経路）。セルが名乗る最古の
        # 取得時刻があれば、それを測定の開始時点として扱う。
        earliest_cell = report.get("earliest_cell_started_utc")
        if earliest_cell is not None:
            started = min(
                started,
                _parse_recorded_utc(
                    earliest_cell,
                    where=f"reports[{idx}]",
                    field="earliest_cell_started_utc",
                ),
            )
        if started > recorded:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] の測定開始時点 "
                f"{started.isoformat()} が recorded_utc "
                f"{report.get('recorded_utc')!r} より後; 開始が完了より後の測定記録は "
                "成立しない (fail-closed)"
            )
        if started < latest_registration:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] の測定開始時点 "
                f"{started.isoformat()}（started_utc={report.get('started_utc')!r} / "
                f"earliest_cell_started_utc={report.get('earliest_cell_started_utc')!r} の"
                f"早い方）が、適用するバーの最新登録時点 "
                f"{latest_registration.isoformat()} より前; 測定の開始時点で存在しなかった "
                "閾値を事前登録として提示させない (fail-closed)"
            )
        if recorded < latest_registration:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] の recorded_utc "
                f"{report.get('recorded_utc')!r} が、適用するバーの最新登録時点 "
                f"{latest_registration.isoformat()} より前; その時点に存在しなかった "
                "閾値の下での実測を名乗れない（実測後に選んだ閾値を事前登録として "
                "提示する経路を塞ぐ・fail-closed）"
            )
        started_by_index.append((idx, started))
        run_id = report.get("run_id")
        if not run_id or not isinstance(run_id, str):
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] lacks a non-empty string run_id; "
                "dated record として扱えない (fail-closed)"
            )
        run_ids.append(run_id)
        if report.get("bars_sha256") != bars_sha256:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] bars_sha256 "
                f"{report.get('bars_sha256')!r} != evaluating bars {bars_sha256!r}; "
                "別バー世代の report を混ぜない (fail-closed)"
            )
    duplicate_run_ids = sorted({r for r in run_ids if run_ids.count(r) > 1})
    if duplicate_run_ids:
        raise ValueError(
            f"evaluate_m2_bars: reports share run_id(s) {duplicate_run_ids}; "
            "同一 run のコピーを複数 repeats として扱えない (fail-closed)"
        )

    # 事前登録の立証: 自己申告の `registered_utc` は backdate できるため、供給された
    # bars bytes が git 履歴（不可変）に最初に現れた commit 日時を立証し、全測定の
    # 開始がその後であることを publish 条件にする（Codex P2 第 28 巡）。
    registration_attestation = _require_attested_registration(
        bars_path, bars.raw, started_by_index
    )

    _require_report_schema(reports)
    _require_publishable_runs(reports)
    # 凍結 spec を evaluate 側でも読み、bars の pin と report の申告の両方に照合する。
    # 母数の上界を row の自己申告から取らず、ここから組み直すために必要（Codex P2）。
    # raw bytes も保持する——測り直し子には実パスでなくこの bytes の複製を渡す
    # （評価後のファイル差し替え TOCTOU の遮断・Codex P2 第 34 巡）。
    specs, specs_sha256, specs_raw = load_specs_with_raw(specs_path)
    _require_specs_pin(specs_sha256, bars_data)
    for idx, report in enumerate(reports):
        reported_specs = report.get("specs_sha256")
        if reported_specs != specs_sha256:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] の specs_sha256 {reported_specs!r} が "
                f"評価に使う spec {specs_sha256!r} と不一致; 別世代の合成仕様で測った row に "
                "凍結バーを適用しない (fail-closed)"
            )
    # M2c: 外部素材カテゴリ（V_direct）の事前登録 pin。specs と同じく report の
    # カテゴリ構成に関わらず無条件にロードする（specs と対称・軽量）。raw bytes も
    # 保持する——測り直し子への凍結転写 + 事前登録の git 立証の両方に使う
    # （`specs_raw`/`bars.raw` と同型の TOCTOU 回避）。
    external_fixtures_data, external_fixtures_sha256, external_fixtures_raw = (
        load_external_fixtures_with_raw(external_fixtures_path)
    )
    # M2c PR-M2c-1 review（Codex 第 1 巡 P1）: 外部素材カテゴリ（`kind: "external"`）の
    # 測定が 1 つでも報告されていれば、bars と同じ事前登録の git 立証を fixtures にも
    # 課す。S オンリーの evaluate（fixtures 未使用）まで委縮させないよう、必要な時だけ
    # 課す（bars は全 evaluate が必ず使うため無条件、fixtures は使う場合のみ）。
    #
    # M2c PR-M2c-1 review（Codex 第 3 巡 P2）: 「必要な時だけ」の判定は report **集合**
    # 単位（`any(...)`）のままでよいが、`started_utc` の順序照合対象は report **単位**
    # で絞る——バッチに外部カテゴリを一切測っていない report（例: S_direct のみの
    # 旧い report）が混在すると、その report は fixtures の登録時点と無関係なのに、
    # 集合レベルの判定だけで全 report の started_utc を fixtures 側 attestation の
    # 順序チェックに巻き込むと、その report の started_utc が fixtures 登録前という
    # 理由だけで evaluate 全体を誤って fail-closed 拒否してしまう（S-only report との
    # 混在評価を誤 reject する）。fixtures の登録時点より後であることを要求すべきは、
    # **実際に外部カテゴリの row を含む report** の started_utc のみに絞る。
    categories_in_reports = {cat for report in reports for cat in report.get("categories", {})}
    external_fixtures_attestation_required = any(
        _CATEGORY_SPECS.get(cat, {}).get("kind") == "external" for cat in categories_in_reports
    )
    external_fixtures_registration_attestation: Optional[Dict[str, Any]] = None
    if external_fixtures_attestation_required:
        external_category_started_by_index = [
            (idx, started)
            for idx, started in started_by_index
            if any(
                _CATEGORY_SPECS.get(cat, {}).get("kind") == "external"
                for cat in reports[idx].get("categories", {})
            )
        ]
        external_fixtures_registration_attestation = _require_attested_external_fixtures_registration(
            external_fixtures_path, external_fixtures_raw, external_category_started_by_index
        )
    generator_code_sha256 = _require_matching_generator_code(reports)
    tolerance_cents = _require_frozen_tolerance(reports, bar_block)
    est_voiced_floor = _require_frozen_est_voicing_floor(reports, bar_block)
    scorer_pins = _require_homogeneous_scorer(reports)
    numeric_runtime_config = _require_homogeneous_numeric_runtime_config(reports)

    verdict: Dict[str, Any] = {
        "schema_version": _EXPECTED_VERDICT_SCHEMA,
        "verdict_recorded_utc": _utc_now(),
        "bars_sha256": bars_sha256,
        "generator_code_sha256": generator_code_sha256,
        "evaluator_code_sha256": _evaluator_code_sha256(),
        "tolerance_cents": tolerance_cents,
        "est_voiced_confidence_floor": est_voiced_floor,
        **scorer_pins,
        "numeric_runtime_config": numeric_runtime_config,
        "n_reports": len(reports),
        "run_ids": sorted(run_ids),
        "repeats_min": repeats_min,
        "registration_attestation": registration_attestation,
        "external_fixtures_sha256": external_fixtures_sha256,
        "external_fixtures_registration_attestation": external_fixtures_registration_attestation,
        "categories": {},
    }
    verdict["report_pins"] = report_pins
    # M2e（§5.2 / §6.2）: 使った bars ファイルの相対パスと sha256、および run の水準
    # 次元を verdict へ刻む。M2e カテゴリを含まない evaluate では None のまま残る。
    verdict["m2e_bars_sha256"] = None
    verdict["m2e_bars_path_relative"] = None
    verdict["m2e_bars_registration_attestation"] = None
    verdict["level"] = None

    all_categories = sorted({cat for report in reports for cat in report.get("categories", {})})
    if not all_categories:
        # run 側でも空選択は弾くが、手組み report が `categories: {}` を名乗る経路の
        # ために evaluate でも独立に要求する（測定が 1 つも無い verdict は証拠でない）。
        raise ValueError(
            "evaluate_m2_bars: どの report にもカテゴリの測定 row が無い; 測定ゼロの "
            "verdict を publish しない (fail-closed)"
        )
    # 未登録カテゴリは **どの分岐にも入る前に** 拒否する。per-category ループ内の
    # `_require_registered_row_identity` は insufficient_repeats のショートカットより
    # 後にあるため、未知カテゴリ（例: 設計 §8 が禁じる X）を `unavailable` row だけで
    # 提出すると、拒否される前に verdict へ記録されてしまう（Codex P2）。
    unknown_categories = sorted(set(all_categories) - set(_CATEGORY_SPECS))
    if unknown_categories:
        raise ValueError(
            f"evaluate_m2_bars: 未登録の category {unknown_categories} が report に含まれる; "
            f"事前登録された {sorted(_CATEGORY_SPECS)} 以外は insufficient_repeats としてすら "
            "verdict に記録しない (fail-closed)"
        )

    # M2e（設計 §5.2）: 所有する bars ファイルが分かれたので、M2e カテゴリが 1 つでも
    # 報告されていればその**別ファイル**を評価器自身が独立にロードし、事前登録の git
    # 立証まで課す（`m2c_external_fixtures.yaml` と同じ「使う場合のみ課す」流儀）。
    m2e_categories_in_reports = sorted(
        cat
        for cat in all_categories
        if _CATEGORY_SPECS[cat]["bars_file"] == "m2e_accuracy_bars.yaml"
    )
    m2e_bars: Optional[BarsArtifact] = None
    m2e_bars_sha256: Optional[str] = None
    m2e_bars_data: Dict[str, Any] = {}
    m2e_registration_attestation: Optional[Dict[str, Any]] = None
    m2e_level: Optional[str] = None
    if m2e_categories_in_reports:
        m2e_bars, m2e_bars_sha256 = load_bars(m2e_bars_path)
        if bars_file_identity(m2e_bars.data) != "m2e_accuracy_bars.yaml":
            raise ValueError(
                f"evaluate_m2_bars: {m2e_bars_path} が m2e_accuracy_bars.yaml の "
                "schema_version を名乗っていない (fail-closed)"
            )
        m2e_bars_data = m2e_bars.verify(m2e_bars_sha256)
        m2e_started_by_index = [
            (idx, started)
            for idx, started in started_by_index
            if any(
                cat in m2e_categories_in_reports for cat in reports[idx].get("categories", {})
            )
        ]
        m2e_registration_attestation = _require_attested_registration(
            m2e_bars_path, m2e_bars.raw, m2e_started_by_index
        )
        # 水準は run の次元（§6.2）。repeats 間で食い違う水準を「同じ測定の反復」と
        # 数えない——別水準の row を混ぜた平均は破断曲線の点ですらない。
        levels = {report.get("level") for report in reports if any(
            cat in m2e_categories_in_reports for cat in report.get("categories", {})
        )}
        if len(levels) != 1 or None in levels:
            raise ValueError(
                f"evaluate_m2_bars: M2e カテゴリを含む report の level が単一でない "
                f"（{sorted(str(v) for v in levels)}）; 別水準の run を同じ測定の反復として "
                "評価しない (fail-closed)"
            )
        m2e_level = levels.pop()
        # 同じ理由で **環境も run の次元**である（§8.7: `env_digest` を跨いだセルの
        # 合算を禁止）。repeats が別環境（別 CPU・別パッチ版）で採られていると
        # `env_digest` が食い違うが、指標が bit 一致してしまえば下流からは見えない。
        # **非 null で単一**であることを要求する——未記録同士を許すと「どちらも
        # 環境を名乗らない 2 本」が反復として通り、CPU 同一性を後から復元できない
        # （M2e run は `--cell-store` の有無に依らず必ず記録する。上の run 側参照）。
        env_digests = {
            report.get("env_digest") for report in reports if any(
                cat in m2e_categories_in_reports for cat in report.get("categories", {})
            )
        }
        # **形も要求する。** 非空文字列でありさえすればよい検査だと、`""` や
        # `"unknown"` のような placeholder を持つ別環境の report 同士が「揃っている」
        # として合算されうる——環境を名乗っていないことが、名乗っていることに化ける。
        if len(env_digests) != 1 or not _is_sha256(next(iter(env_digests))):
            raise ValueError(
                f"evaluate_m2_bars: M2e カテゴリを含む report の env_digest が揃っていない "
                f"（{sorted(str(v) for v in env_digests)}）; 環境を名乗らない report・別環境で "
                "採ったセルを同じ帯の反復として合算しない (fail-closed)"
            )
        verdict["m2e_bars_sha256"] = m2e_bars_sha256
        verdict["m2e_bars_path_relative"] = _repo_relative_path(m2e_bars_path)
        verdict["m2e_bars_registration_attestation"] = m2e_registration_attestation
        verdict["level"] = m2e_level
        # C5（設計判断 E-4）: 水準横断集計は**環境同一性を検査できなければならない**
        # （§8.7「複数環境のセルを 1 つの帯として合算することは禁止」）。verdict が
        # `env_digest` を名乗らないと、集計器は 4 水準が同じ環境で測られたかを
        # 判定する手段を持たない——上でその単一性を既に要求しているので、値をここで
        # 成果物へ持ち出す。**M2e カテゴリを含む verdict にのみ現れる**（他の verdict は
        # 1 バイトも変わらない）。
        verdict["env_digest"] = next(iter(env_digests))
        # C5（設計判断 E-13）: **混合式の provenance を成果物へ持ち出す。**
        # 水準ごとに fixtures ファイルは別なので `external_fixtures_sha256` の水準横断
        # 比較は意味を持たない——mixer（`make_vremix_fixtures.py`）を変えて一部の水準を
        # 作り直しても、id は同じ・per-level hash は元々違う・harness のコード pin は
        # mixer を含まない、で誰も気付けない。fixtures 自身が名乗る `builder` は run 側で
        # 実体と照合済み（`_require_registered_m2e_cohort`）なので、その検証済みの値を
        # verdict へ写し、集計器が水準横断の一致を要求できるようにする。
        verdict["m2e_builder_provenance"] = copy.deepcopy(
            external_fixtures_data.get("builder")
        )

    # C2: 測り直しを起こす前に、`store_B` が提出 report を産んだ `store_A` と重なって
    # いないことを確かめる（CLI の 2 引数比較は evaluate では走らないため、ここが
    # 本番で効く唯一の関所・PR #240 Codex P1）。
    if eval_cell_store is not None:
        _require_eval_cell_store_disjoint_from_run_stores(reports, eval_cell_store)
    # D-3: 測り直しを起こす前に、提出 report のスレッド条件を契約として確定させる
    # （高価な測り直しを走らせてから bit 不一致で落ちるより、原因が明示的）。
    thread_pinning = _thread_pinning_contract_from_reports(reports) if pin_threads else None
    # C2/C3: 既定（`None`/`1`/`False`）では verdict に 1 バイトも増やさない。
    if eval_cell_store is not None or workers != 1 or thread_pinning is not None:
        evaluate_execution: Dict[str, Any] = {"workers": workers}
        # **黙って頭打ちにしない**（PR #240 Codex P1）。1 カテゴリの測り直しは
        # `repeats_min` 本の子しか起こさないので、`workers > repeats_min` の分は
        # 効かない。宣言値だけを載せると「P=4 で回した」と読めてしまうため、
        # 実効値も併記する（どちらも宣言・導出された構成値であって実測量ではない）。
        evaluate_execution["effective_workers_per_category"] = min(workers, repeats_min)
        if eval_cell_store is not None:
            evaluate_execution["eval_cell_store_relative"] = _repo_relative_path(eval_cell_store)
        if thread_pinning is not None:
            evaluate_execution["thread_pinning"] = thread_pinning
        # ここに載せるのは**宣言した実行構成**だけである（`P` に依存して変わる
        # 実測量——所要時間・スケーリング比——は verdict に載せない。§8.3 の飽和
        # 判定と混同されるため、効果は別途 `P` を振った実測比で示す）。
        verdict["evaluate_execution"] = evaluate_execution

    for category in all_categories:
        rows = [report["categories"][category] for report in reports if category in report["categories"]]
        outcomes = {row["outcome"] for row in rows}
        cat_result: Dict[str, Any] = {"n_rows": len(rows), "outcomes": sorted(outcomes)}

        # scored row は `outcome == "measured"` だけ。`"unavailable"` 以外の未知値
        # （`"failed"` / `"error"` 等）を measured 扱いにすると、失敗した観測が
        # metrics を持っているだけで pass を得られる（Codex P1）。
        unknown_outcomes = sorted(o for o in outcomes if o not in {"measured", "unavailable"})
        if unknown_outcomes:
            raise ValueError(
                f"evaluate_m2_bars: category {category!r} に未知の outcome "
                f"{unknown_outcomes}; scored row は outcome='measured' のみ "
                "(fail-closed)"
            )

        if len(rows) < repeats_min or "measured" not in outcomes or "unavailable" in outcomes:
            cat_result["status"] = "insufficient_repeats"
            verdict["categories"][category] = cat_result
            continue

        category_kind = _CATEGORY_SPECS[category]["kind"]
        category_bars_file = _CATEGORY_SPECS[category]["bars_file"]
        is_m2e = category_bars_file == "m2e_accuracy_bars.yaml"

        if is_m2e:
            # 分離した bars ファイルの pin を row 単位で束縛する（設計 §5.2）。
            # M2 / M2c カテゴリにこの要求を課さないのは、それらのバー世代が report の
            # top-level `bars_sha256` で既に厳密に束縛されているため（commit 済み記録は
            # このフィールド以前の世代であり、遡って要求しても pin は強くならない）。
            for idx, row in enumerate(rows):
                if row.get("bars_file_sha256") != m2e_bars_sha256:
                    raise ValueError(
                        f"evaluate_m2_bars: category {category!r} rows[{idx}] の "
                        f"bars_file_sha256 {row.get('bars_file_sha256')!r} が評価器の読んだ "
                        f"{m2e_bars_sha256!r} と不一致; 別世代の帯登録で測った row に "
                        "凍結バーを適用しない (fail-closed)"
                    )
                if row.get("level") != m2e_level:
                    raise ValueError(
                        f"evaluate_m2_bars: category {category!r} rows[{idx}] の level "
                        f"{row.get('level')!r} が report の level {m2e_level!r} と不一致 "
                        "(fail-closed)"
                    )
            cat_result["bars_file"] = category_bars_file
            cat_result["bars_file_sha256"] = m2e_bars_sha256
            cat_result["bars_file_relative"] = _repo_relative_path(m2e_bars_path)
            cat_result["level"] = m2e_level
            cat_result["ladder_index"] = _m2e_ladder_index(m2e_level)

        # repeats として数える前に (a) row が本当にその事前登録 fixture・経路の観測か、
        # (b) 同一 model stack で測られたか を証明する（Codex P1×2）。
        if category_kind == "external":
            common_manifest_sha256 = _require_registered_row_identity_external(
                category, rows, external_fixtures_data, external_fixtures_sha256
            )
        else:
            _require_registered_row_identity(category, rows, bars_data)
        _require_homogeneous_model_stack(category, rows)
        # 実行証拠: row の pin が評価環境から再計算したスタック pin と一致すること。
        # report の自己申告フラグ（route_runner_injected 等）は bytes ごと書き換え
        # られるため、publish 可否の最終根拠はこの環境照合に置く（Codex P1）。
        _require_execution_evidence(
            category,
            rows,
            _select_named_route(
                _CATEGORY_SPECS[category]["input_kind"],
                _CATEGORY_SPECS[category]["route_name"],
            ),
        )
        # 測り直しによる検証: 評価器自身が同じ fixture を repeats_min 回独立に測り、
        # 相互 bit 一致 + 全 report row との bit 一致を要求する（導入証明でなく実行
        # 証明・Codex P1 第 21 巡。run_id はコピーで水増しできるため、repeats 契約の
        # 根拠を提出側でなく evaluator 側の独立実行に置く・Codex P2 第 23 巡）。検証用
        # ランナーの注入口は公開 API に置かない（Codex P1 第 22 巡）——常に実抽出器。
        # M2c: 外部素材カテゴリは `_reverify_category_measurement` 内部で
        # `--external-manifest` の要求（未指定 fail-closed）を含めて振り分ける。
        _reverify_category_measurement(
            category,
            rows,
            bars=bars,
            specs_raw=specs_raw,
            repeats=repeats_min,
            external_manifest_path=external_manifest_path,
            external_fixtures_path=external_fixtures_path,
            external_fixtures_raw=external_fixtures_raw,
            m2e_bars_raw=m2e_bars.raw if is_m2e and m2e_bars is not None else None,
            level=m2e_level if is_m2e else None,
            eval_cell_store=eval_cell_store,
            workers=workers,
            thread_pinning=thread_pinning,
        )

        # 判定規律（設計 §6.2・fail-closed）: **`level != gate_level` の run に
        # バーを適用しない。** `gate_level` 以外の水準は**破断曲線の記録専用**であり
        # 合否を出さない（§5.4 の「単点トリップワイヤ」の実装上の意味はこれ）。
        gate_level: Optional[str] = None
        if is_m2e:
            conditions_key = _BARS_FILES[category_bars_file]["conditions_key"]
            gate_level = m2e_bars_data[conditions_key][category]["gate_level"]
            cat_result["gate_level"] = gate_level
            bar = (
                m2e_bars_data[_BARS_FILES[category_bars_file]["block_key"]].get(category, {})
                if m2e_level == gate_level
                else {}
            )
        else:
            bar = bar_block.get(category, {})

        if category_kind == "external":
            # M2c: 母数の独立再計算は「凍結 spec」ではなく clip 単位の自己整合性
            # 検査（`_require_external_clip_bounded_counts`）に置き換える——外部注釈
            # は evaluate 側が独立に再読込できる凍結 spec を持たない（run 時点の
            # fail-closed hash 照合が音声/注釈 bytes の同一性を担保する）。
            clip_metrics_list = [c["metrics"] for row in rows for c in row["clips"]]
            _require_finite_metrics(category, clip_metrics_list)
            _require_metrics_contract(category, clip_metrics_list, tolerance_cents=tolerance_cents)
            _require_external_clip_bounded_counts(category, rows)
            # M2c WIP e3810b0 review（Codex 第 2 巡 P1）: バー適用の前に、row["metrics"]
            # （カテゴリ集計値）が `row["clips"]` から**評価器が独立に再計算**した平均と
            # 完全一致することを要求する——run 側 `_average_external_clip_metrics` の
            # 出力を row にそのまま書くだけの信頼では、report が clips はそのままに
            # metrics だけ書き換えた（RPA を水増しする等）改竄を見逃す。S カテゴリの
            # 「母数を凍結 spec から再計算し、row の自己申告を信用しない」規律
            # （`_registered_reference_counts`/`_require_reference_bounded_counts`）と
            # 同じ精神を、外部素材カテゴリでは「集計はソース clips から導出されなければ
            # ならない」という形で適用する。
            _require_external_row_metrics_match_clip_average(category, rows)
            metrics_list = [row["metrics"] for row in rows]
            _require_finite_metrics(category, metrics_list)
            cat_result["external_manifest_sha256"] = common_manifest_sha256
            cat_result["external_fixtures_sha256"] = external_fixtures_sha256
            cat_result["clip_ids"] = sorted({c["clip_id"] for row in rows for c in row["clips"]})
            cat_result["metrics"] = metrics_list
            # clip 単位の全体一致（設計 Memo M2c: repeats bit 一致は clips で判定）。
            # 平均化で相殺されうる clip 単位の乖離を、averaged metrics だけの比較より
            # 厳しく検出する。
            bit_identical = _repeats_bit_identical([row["clips"] for row in rows])
        else:
            metrics_list = [row["metrics"] for row in rows]
            _require_finite_metrics(category, metrics_list)
            _require_metrics_contract(category, metrics_list, tolerance_cents=tolerance_cents)
            expected_frames, expected_voiced = _registered_reference_counts(
                category, bars_data, specs
            )
            _require_reference_bounded_counts(
                category,
                rows,
                expected_frame_count=expected_frames,
                expected_voiced_frame_count=expected_voiced,
            )
            cat_result["reference_frame_counts"] = {
                "ref_frame_count": expected_frames,
                "ref_voiced_frame_count": expected_voiced,
                "source": "recomputed_from_frozen_specs",
            }
            cat_result["metrics"] = metrics_list
            bit_identical = _repeats_bit_identical(metrics_list)

        # bars.yaml の `repeats_min` は決定論確認（「shifts=0 後は bit 一致するはず」）
        # であって「たまたま両方バー内」ではない。乖離はバーの有無と独立に記録する。
        cat_result["repeats_bit_identical"] = bit_identical

        if is_m2e and m2e_level != gate_level:
            # 破断曲線の記録専用。合否は出さない（§5.4 / §6.2）。バーは存在するが
            # 適用しないので `diagnostic_only`（＝バーがそもそも無い帯）とも区別する。
            cat_result["status"] = "level_record_only"
            verdict["categories"][category] = cat_result
            continue

        if not bar:
            # S_fullstack: バーなし・診断記録のみ（設計 §3/§8）。
            # `load_bars` が空バーを診断専用カテゴリに限っているが、`evaluate_m2_bars` を
            # 直接呼ぶ経路（bars dict を手で組む）はそこを通らないため独立に要求する。
            if category not in _DIAGNOSTIC_ONLY_CATEGORIES:
                raise ValueError(
                    f"evaluate_m2_bars: category {category!r} は診断専用ではないのに閾値が "
                    f"空; 受け入れゲートを diagnostic_only へ落として無効化させない "
                    f"（診断専用は {sorted(_DIAGNOSTIC_ONLY_CATEGORIES)} のみ・fail-closed）"
                )
            cat_result["status"] = "diagnostic_only"
            verdict["categories"][category] = cat_result
            continue

        failures: List[str] = []
        if not bit_identical:
            failures.append(
                "repeats metrics diverge under one pinned stack; bars.yaml の repeats_min は "
                "決定論確認であり bit 一致を要求する（個々がバー内でも pass にしない）"
            )
        for repeat_idx, metrics in enumerate(metrics_list):
            if "min_rpa" in bar and metrics["raw_pitch_accuracy"] < bar["min_rpa"]:
                failures.append(
                    f"repeat[{repeat_idx}] raw_pitch_accuracy {metrics['raw_pitch_accuracy']:.4f} "
                    f"< min_rpa {bar['min_rpa']}"
                )
            if "max_vfa" in bar and metrics["voicing_false_alarm"] > bar["max_vfa"]:
                failures.append(
                    f"repeat[{repeat_idx}] voicing_false_alarm {metrics['voicing_false_alarm']:.4f} "
                    f"> max_vfa {bar['max_vfa']}"
                )
            if "max_octave_gap" in bar and metrics["octave_gap"] > bar["max_octave_gap"]:
                failures.append(
                    f"repeat[{repeat_idx}] octave_gap {metrics['octave_gap']:.4f} "
                    f"> max_octave_gap {bar['max_octave_gap']}"
                )
        cat_result["failures"] = failures
        if is_m2e:
            # 設計 §6.2（「帯の判定は `gate_level` の run が §11 のセル census を
            # 満たしたときにのみ出る」）と §11（全 1280 セルが揃うまで帯の判定を
            # 出さない）。**1 回の evaluate は構造上 1 水準しか見ない**——
            # 上流で「M2e カテゴリを含む report の level は単一」を要求しているので、
            # このコールから 4 水準 × 2 アームの census を立証する術が無い。
            # したがってバーは当てる（証拠は `bar_satisfied` / `failures` に残す）が、
            # **合否という語は出さない**。帯の判定は r6/r7 の水準横断集計が census を
            # 満たしたときに出す（その集計器は `aggregate_m2e_census`（`--census`）
            # として実装済み）。
            cat_result["bar_satisfied"] = not failures
            cat_result["status"] = "census_pending"
        else:
            cat_result["status"] = "pass" if not failures else "fail"
        verdict["categories"][category] = cat_result

    # verdict を返す（= publish する）直前に、load 時に pin したコードが
    # 実行中に差し替わっていないことを確認する。`_require_matching_generator_code`
    # は report が申告した digest を突き合わせるだけなので、**評価側**のプロセスが
    # 「メモリ上の旧 evaluator を走らせつつ、新しいディスク bytes を
    # `evaluator_code_sha256` として名乗る」窓が残っていた（Codex P1）。run phase と
    # 同じ post-execution 検査を evaluate phase にも及ぼす。
    _require_unchanged_since_load()
    return verdict


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _require_out_outside_git_metadata(out: Path) -> None:
    """`--out` が git メタデータディレクトリ内を指すことを拒否する（fail-closed）。

    事前登録の立証（`_bars_registration_attestation`）は HEAD・refs・objects を入力
    として読むが、`--out .git/HEAD` 等は既存の保護集合（ファイル単位）に載らず、
    `_atomic_write_text` が checkout を破壊しつつ「立証に使った入力を成果物で潰す」
    ことになる（Codex P2 第 31 巡）。git ディレクトリはいかなる場合も正当な出力先
    ではないため、run/evaluate 両モードで丸ごと拒否する。worktree では `.git` が
    ファイルで実体が別位置にあるため `--absolute-git-dir` / `--git-common-dir` の
    両方を解決する（解決できない環境でも既定位置 `ROOT/.git` は守る）。
    """
    metadata_dirs: List[Path] = [(ROOT / ".git").resolve()]
    # Codex 13 巡目 H19: 非修飾 `git` は ldconfig（P1-A）と同じ PATH 注入クラス——
    # 信頼できる絶対パス + 硬化 env で起動する。信頼できる git が見つからない場合も
    # 既存どおり黙って degrade する（本関数はあくまで防御的な追加保護で、既定位置
    # `ROOT/.git` の保護はこの解決に依存しないため fail-closed にはしない）。
    from svp_rpe.melody.provenance import _hardened_subprocess_env, _trusted_git_executable

    try:
        git_exe = _trusted_git_executable()
        proc = subprocess.run(
            [git_exe, "-C", str(ROOT), "rev-parse", "--absolute-git-dir", "--git-common-dir"],
            capture_output=True,
            text=True,
            env=_hardened_subprocess_env(),
        )
    except (OSError, RuntimeError):
        proc = None
    if proc is not None and proc.returncode == 0:
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            candidate = Path(line)
            if not candidate.is_absolute():
                candidate = ROOT / candidate
            metadata_dirs.append(candidate.resolve())
    resolved = Path(out).resolve()
    for directory in metadata_dirs:
        if resolved == directory or directory in resolved.parents:
            raise SystemExit(
                f"--out {out} は git メタデータ（{directory}）内を指している; 事前登録の "
                "立証が読む入力・checkout の制御ファイルを成果物で上書きしない (fail-closed)"
            )


def _external_manifest_protected_paths(
    external_manifest_path: "str | Path", external_fixtures_path: "str | Path"
) -> "set[Path]":
    """M2c PR-M2c-1 review（Codex 第 1 巡 P1）: `--out` 保護集合を外部素材へ拡張する。

    `--external-manifest` が指定されている run/evaluate では、evaluate が読む
    fixtures yaml だけでなく、**manifest が指す全 member（音声/注釈の解決済みパス）**
    も `--out` の書き込み対象から保護する必要がある——`--out` が manifest の指す
    音声ファイルと同じパスを指せば、hash して照合した後の bytes を report/verdict の
    書き出しで潰してしまう（既存の bars/specs/report 保護と同型の穴）。manifest 自体を
    preflight でパースする（`_load_external_manifest` は構造・id を検証済みなので、
    ここで初めて解決するのではなく既存の検証経路を再利用する）。
    """
    protected: "set[Path]" = {Path(external_fixtures_path).resolve()}
    entries, _manifest_sha256, manifest_path = _load_external_manifest(external_manifest_path)
    protected.add(manifest_path)
    manifest_dir = manifest_path.parent
    for entry in entries:
        protected.add(
            _resolve_external_member_path(manifest_dir, entry["audio_path"], what="audio_path")
        )
        protected.add(
            _resolve_external_member_path(
                manifest_dir, entry["annotation_path"], what="annotation_path"
            )
        )
    return protected


# ---------------------------------------------------------------------------
# C5 — 水準横断の census 集計（帯の判定を出す唯一の場所・rev.6 §6.2 / §11）
# ---------------------------------------------------------------------------
#
# `evaluate_m2_bars` は M2e カテゴリに `pass` / `fail` を出さない。**1 回の evaluate は
# 構造上 1 水準しか見ない**（M2e を含む report の level は単一であることを fail-closed で
# 要求している）ため、そのコールから 4 水準 × 2 アームの census を立証する術が無いから
# である。ここがその立証を行う唯一の場所であり、**帯の判定が出る唯一の場所**でもある。
_M2E_CENSUS_SCHEMA = "m2e-census/0.1"

# E-24: census が要求する numeric_runtime_config のトップレベルキー集合。
# `_numeric_runtime_config()` の返す形と機械同期される（テスト
# test_numeric_runtime_config_required_keys_match_the_producer が enforce）。
# census 内でその関数を直接呼ばないのは、計測 instrumentation の import
# （threadpoolctl 等）を評価器プロセスへ持ち込まないため。
_NUMERIC_RUNTIME_CONFIG_REQUIRED_KEYS = frozenset(
    {"env", "cpu_count", "sched_affinity_count", "numpy_simd_dispatch", "threadpool_info"}
)


def load_verdict(path: "str | Path") -> ReportArtifact:
    """verdict JSON を single read で読む（read → hash → parse の 1 操作）。

    `load_report` と同じ束縛（raw bytes / digest / parsed data）を使う——集計器は
    「pin した bytes と実際に集計した内容」が食い違わないことを、report と同じ強さで
    要求する。schema の検査は `aggregate_m2e_census` 側で行う。
    """
    raw = Path(path).read_bytes()
    return ReportArtifact.from_bytes(raw, path=path)


def _m2e_census_expected_cells(repeats_min: int) -> int:
    """期待セル総数を**構成要素の積として再計算する**（§6.2「総抽出回数の一致確認」）。

    `1280` という定数を書かない。コホート幅・ラダー長・アーム数・repeats のどれかが
    動いたとき、定数はそれを黙って通す一方、積は必ず食い違う。
    """
    return (
        _M2E_EXPECTED_ENTRIES_PER_LEVEL
        * len(_M2E_LEVEL_LADDER)
        * len(_categories_owned_by("m2e_accuracy_bars.yaml"))
        * repeats_min
    )


def _m2e_normalized_cohort_ids(level: str, clip_ids: "List[str]") -> "Tuple[str, ...]":
    """entry id から水準タグを剥がした**正規化コホート**（= (clip, bed) の集合）を返す。

    id 規約は `vremix_{clip_id}_{bed_id}_{level_tag}`（§6.2）なので、水準が違えば id も
    違う——水準間で id 集合をそのまま比べることはできない。タグを剥がして初めて
    「4 水準が同じ 80 個の (clip, bed) を測ったか」を問える。

    タグが期待どおりでない id は fail-closed。`level: "+6dB"` を名乗る verdict が
    `p12` の id を運んでいれば、それは**同じミックスを 2 回測って別水準として並べた**
    ものであり、破断曲線が 1 水準の複製から組み上がる（PR #241 Codex P1）。
    """
    suffix = f"_{_M2E_LEVEL_TAGS[level]}"
    normalized: "List[str]" = []
    for clip_id in clip_ids:
        if not isinstance(clip_id, str) or not clip_id.endswith(suffix):
            raise ValueError(
                f"aggregate_m2e_census: level {level!r} の entry id {clip_id!r} が期待する "
                f"水準タグ {suffix!r} で終わっていない; 別水準のミックスを当該水準の観測と "
                "して数えない（破断曲線が 1 水準の複製から組み上がる）(fail-closed)"
            )
        normalized.append(clip_id[: -len(suffix)])
    return tuple(sorted(normalized))


def _require_average_stable_metric_invariants(
    arm: str, metrics_list: "List[Dict[str, Any]]", *, frozen_tolerance_cents: float
) -> None:
    """`_require_metrics_contract` の不変条件のうち、算術平均で保存されるものだけを

    census 側で再適用する（PR #241 Codex P1・E-27）。

    まず `_require_metrics_contract` をそのまま census の平均済み metrics に当てる
    実装を試した——**通らなかった**。evaluate は external カテゴリで contract を
    **clip metrics** にのみ適用し、census が持つカテゴリ平均 metrics（`row["metrics"]`
    = `_average_external_clip_metrics` の出力）には `_require_finite_metrics` しか
    当てていない。`voiced_chroma_correct_frame_count` は算術平均で非整数 float に
    なりうるため（`_average_external_clip_metrics` docstring）、full contract の
    整数不変条件は平均後の値では成立が保証されない（実測でも 2 clip 平均が非整数に
    なるケースを確認した）。

    census は clip 単位の証拠を持たない（E-1: evaluate のゲートを二重実装しない）ので
    contract 全体は当てられない——しかし以下は**単純算術平均で厳密に保存される**ため
    census 側でも意味を持つ:

    - (a) `metrics.tolerance_cents`: 平均前は clip 間で同一値であることが
      `_average_external_clip_metrics` 自身の fail-closed 検査で保証されており、
      平均後もその値のまま残る（定数の平均は定数）。凍結値との厳密一致を要求する。
    - (b) `raw_chroma_accuracy >= raw_pitch_accuracy`: clip ごとに成立する非負差
      （`octave_gap_i = rca_i - rpa_i >= 0`）の算術平均は非負なので、平均後も成立する。
    - (c) `octave_gap == raw_chroma_accuracy - raw_pitch_accuracy`: 算術平均は
      加減算と可換（`mean(rca_i - rpa_i) == mean(rca_i) - mean(rpa_i)`）なので、
      浮動小数点誤差の範囲で成立する。
    """
    for repeat_idx, metrics in enumerate(metrics_list):
        where = f"arm {arm!r} repeat[{repeat_idx}]"
        for field in ("raw_pitch_accuracy", "raw_chroma_accuracy", "octave_gap", "tolerance_cents"):
            if field not in metrics:
                raise ValueError(f"{where} の metrics が {field} を欠く")
            value = metrics[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{where} の {field} {value!r} が数値でない")

        nested_tolerance = float(metrics["tolerance_cents"])
        if nested_tolerance != float(frozen_tolerance_cents):
            raise ValueError(
                f"{where} の metrics.tolerance_cents {nested_tolerance!r} が凍結値 "
                f"{frozen_tolerance_cents} と不一致"
            )
        rpa = float(metrics["raw_pitch_accuracy"])
        rca = float(metrics["raw_chroma_accuracy"])
        gap = float(metrics["octave_gap"])
        if rca < rpa - 1e-9:
            raise ValueError(
                f"{where} の raw_chroma_accuracy {rca!r} が raw_pitch_accuracy {rpa!r} を "
                "下回る（chroma 一致は pitch 一致の必要条件）"
            )
        if abs(gap - (rca - rpa)) > 1e-9:
            raise ValueError(
                f"{where} の octave_gap {gap!r} が raw_chroma_accuracy - raw_pitch_accuracy "
                f"({rca - rpa!r}) と一致しない"
            )


def _require_homogeneous_census_inputs(
    verdicts: "List[Dict[str, Any]]",
    *,
    m2e_bars_sha256: str,
    bars_sha256: str,
    frozen_repeats_min: int,
    frozen_tolerance_cents: float,
    frozen_est_voiced_floor: float,
) -> Dict[str, Any]:
    """集計してよい verdict 群かを検査し、共通スカラーを返す（fail-closed）。

    **異なる帯登録・異なるコード・異なる環境で出た verdict を 1 つの帯として合算しない。**
    §8.7 の「複数環境のセルを合算しない」を、水準横断の集計面へそのまま適用する
    （設計判断 E-4）。`m2e_bars_sha256` は verdict の自己申告ではなく**集計器が読んだ
    凍結ファイル**と突き合わせる——別世代のバーで出た判定を現行バーの帯として publish
    しないため。
    """
    if not verdicts:
        raise ValueError(
            "aggregate_m2e_census: verdict が 1 件も渡されていない; 集計対象なしに "
            "帯の census を名乗らない (fail-closed)"
        )
    common: Dict[str, Any] = {}
    fields = (
        "bars_sha256",
        "m2e_bars_sha256",
        "generator_code_sha256",
        "evaluator_code_sha256",
        "tolerance_cents",
        "est_voiced_confidence_floor",
        "repeats_min",
        "env_digest",
        # `env_digest` は **`threadpool_info` を意図的に含めない**（`_env_digest_numeric_
        # runtime` docstring: 計測のための threadpoolctl import が scoring の pin へ
        # 混入するのを避けるため）。その穴は「記録は `numeric_runtime_config` に残る」
        # という前提で宣言された穴として許容されている——**その記録を照合しなければ
        # 前提が成立しない**。evaluate は 1 水準の中で
        # `_require_homogeneous_numeric_runtime_config` を既に課しているので、水準を
        # 跨ぐ集計だけが弱いという非対称になっていた（PR #241 Codex P1）。
        "numeric_runtime_config",
        # 混合式の provenance（E-13・PR #241 Codex P1）。**破断曲線は主生産物であり、
        # 混合式が水準間で混ざれば曲線として成立しない。**
        #
        # 「現行 mixer とも一致していること」までは要求しない: ミックスの音声 bytes は
        # fixtures の sha256 で既に pin されており、測定の正しさは mixer の現在値に
        # 依存しない。現行一致まで要求すると、完了済みキャンペーンが後日の無関係な
        # mixer 変更で無効になり、「一度測って後で集計する」という本トラックのモデルと
        # 衝突する（run 側は測定時点で実体照合を済ませている）。
        "m2e_builder_provenance",
    )
    for field in fields:
        values = {json.dumps(v.get(field), sort_keys=True) for v in verdicts}
        if len(values) != 1:
            raise ValueError(
                f"aggregate_m2e_census: verdict 間で {field} が揃っていない "
                f"（{sorted(values)}）; 別の帯登録・別コード・別環境で出た判定を 1 つの "
                "帯として合算しない (fail-closed)"
            )
        common[field] = verdicts[0].get(field)

    # 基底 bars（共有スカラーの供給元）も**集計器が読んだ凍結ファイル**と突き合わせる
    # （PR #241 Codex P2）。`m2e_bars_sha256` だけを照合して `bars_sha256` を自己申告の
    # まま成果物へ写すのは非対称であり、共有スカラーの世代を立証しないまま名乗ることに
    # なる。とくに `repeats_min` は**census の分母を決める**——verdict が `1` を名乗れば
    # 期待セル数が半分になり、半分終わった帯が「揃った」ことになる。
    if common["bars_sha256"] != bars_sha256:
        raise ValueError(
            f"aggregate_m2e_census: verdict の bars_sha256 {common['bars_sha256']!r} が "
            f"集計器の読んだ基底バー {bars_sha256!r} と不一致; 別世代の共有スカラー"
            "（tolerance_cents / est_voiced_confidence_floor / repeats_min）の下で出た "
            "判定を現行世代の帯として publish しない (fail-closed)"
        )
    if common["repeats_min"] != frozen_repeats_min:
        raise ValueError(
            f"aggregate_m2e_census: verdict の repeats_min {common['repeats_min']!r} が "
            f"凍結値 {frozen_repeats_min!r} と不一致; census の分母を verdict の自己申告に "
            "決めさせない（小さく名乗れば未完の帯が揃ったことになる）(fail-closed)"
        )
    if common["m2e_bars_sha256"] != m2e_bars_sha256:
        raise ValueError(
            f"aggregate_m2e_census: verdict の m2e_bars_sha256 "
            f"{common['m2e_bars_sha256']!r} が集計器の読んだ帯登録 {m2e_bars_sha256!r} と "
            "不一致; 別世代のバーの下で出た判定を現行バーの帯として publish しない "
            "(fail-closed)"
        )
    # 集計器自身のコードと、判定を出したコードの一致（`evaluate_m2_bars` の 3 段照合と
    # 同型）。集計は判定を**新たに publish する**行為なので、その根拠が現 checkout で
    # 再現可能であることを要求する。
    current_generator = _generator_code_sha256()
    if common["generator_code_sha256"] != current_generator:
        raise ValueError(
            f"aggregate_m2e_census: verdict の generator_code_sha256 "
            f"{common['generator_code_sha256']!r} が現 checkout の {current_generator!r} と "
            "不一致; 別世代のコードが産んだ判定を現行コードの帯として publish しない "
            "(fail-closed)"
        )
    current_evaluator = _evaluator_code_sha256()
    if common["evaluator_code_sha256"] != current_evaluator:
        raise ValueError(
            f"aggregate_m2e_census: verdict の evaluator_code_sha256 "
            f"{common['evaluator_code_sha256']!r} が現 checkout の {current_evaluator!r} と "
            "不一致 (fail-closed)"
        )
    # **形も要求する**（`""` や `"unknown"` の placeholder が「揃っている」に化けない）。
    if not _is_sha256(common["env_digest"]):
        raise ValueError(
            f"aggregate_m2e_census: verdict の env_digest {common['env_digest']!r} が "
            "64-hex sha256 でない; 環境を名乗らない判定を帯の census に数えない "
            "(fail-closed)"
        )
    if not isinstance(common["repeats_min"], int) or common["repeats_min"] < 2:
        raise ValueError(
            f"aggregate_m2e_census: repeats_min {common['repeats_min']!r} が 2 未満または "
            "整数でない (fail-closed)"
        )
    # **「全部欠けている」を「揃っている」と見なさない**（PR #241 Codex P1）。
    # 上の等値検査は `v.get(field)` を比べるので、全 verdict がこのフィールドを持たなければ
    # `None` 同士で一致し、E-13 の照合はフィールドを剥がすだけで無効化できてしまう。
    # `env_digest` には形の要求を置いたのにここには置いていない、という非対称だった。
    builder = common["m2e_builder_provenance"]
    if not isinstance(builder, dict):
        raise ValueError(
            f"aggregate_m2e_census: verdict が m2e_builder_provenance を名乗っていない "
            f"（{builder!r}）; どの混合式で作られたミックスを測った判定なのか立証できない "
            "まま破断曲線を組まない (fail-closed)"
        )
    for key in ("generator_code_sha256", "m2c_fixtures_sha256", "m2e_bed_fixtures_sha256"):
        if not _is_sha256(builder.get(key)):
            raise ValueError(
                f"aggregate_m2e_census: m2e_builder_provenance の {key} "
                f"{builder.get(key)!r} が 64-hex sha256 でない; 混合式の素性を名乗るだけの "
                "申告を照合済みとして扱わない (fail-closed)"
            )
    # E-19: `numeric_runtime_config` にも存在と形を要求する（PR #241 Codex P1・E-17 と
    # 同じ論法）。`env_digest` は `threadpool_info` を意図的に畳まないので、この記録の
    # 照合が唯一の防壁である——全 verdict がフィールドを欠けば `None` 同士で「揃い」、
    # 別 BLAS/threadpool 構成の判定が census_complete に合流できてしまう。
    nrc = common["numeric_runtime_config"]
    if not isinstance(nrc, dict) or not nrc:
        raise ValueError(
            f"aggregate_m2e_census: verdict が numeric_runtime_config を名乗っていない "
            f"（{nrc!r}）; env_digest が threadpool_info を畳まない宣言された穴は、この"
            "記録の照合を前提に許容されている——記録なしでは前提が成立しない (fail-closed)"
        )
    # E-24: 「非空 dict」だけでは `{"unknown": True}` のような placeholder が通る
    # （PR #241 Codex P1）。生成側（`_numeric_runtime_config()`）が実際に返すトップ
    # レベルキー集合と束縛する。`generator_code_sha256` は既に現 checkout と照合済み
    # なので、生成側の形が世代間で動いてもこの検査が誤爆する経路は無い——ここで見て
    # いるのは「生成側と同じ checkout が今この形を返す」という 1 点のみである。
    if set(nrc) != _NUMERIC_RUNTIME_CONFIG_REQUIRED_KEYS:
        raise ValueError(
            f"aggregate_m2e_census: numeric_runtime_config のキー集合 {sorted(nrc)} が "
            f"生成側の形 {sorted(_NUMERIC_RUNTIME_CONFIG_REQUIRED_KEYS)} と不一致; "
            "placeholder を証拠として数えない (fail-closed)"
        )
    # 共有スカラーも census 成果物へそのまま載せるので、載せる前に形を確かめる
    # （E-14 と同じ規律。等値検査だけでは `None` 同士・文字列同士でも揃ってしまう）。
    # E-26: 有限性だけでは足りない（PR #241 Codex P1）。E-7（repeats_min）で自分が
    # 適用した規律との非対称——verdict が名乗る `tolerance_cents` / `est_voiced_
    # confidence_floor` は、census 自身が読んだ基底バーの**実値**とも突き合わせる。
    # 50 cents で測った metrics を 5 cents の測定として publish しない。
    for scalar_key, frozen_value in (
        ("tolerance_cents", frozen_tolerance_cents),
        ("est_voiced_confidence_floor", frozen_est_voiced_floor),
    ):
        scalar = common[scalar_key]
        if isinstance(scalar, bool) or not isinstance(scalar, (int, float)) or not math.isfinite(scalar):
            raise ValueError(
                f"aggregate_m2e_census: {scalar_key} {scalar!r} が有限数値でない; "
                "凍結スカラーを名乗らない判定を census に数えない (fail-closed)"
            )
        if float(scalar) != float(frozen_value):
            raise ValueError(
                f"aggregate_m2e_census: {scalar_key} {scalar!r} が凍結バーの実値 "
                f"{frozen_value!r} と不一致; 50 cents で測った metrics を 5 cents の測定と "
                "して publish しない (fail-closed)"
            )
    return common


def aggregate_m2e_census(
    verdicts: "List[ReportArtifact]",
    *,
    bars_path: Path = BARS_PATH,
    m2e_bars_path: Path = M2E_BARS_PATH,
) -> Dict[str, Any]:
    """4 水準 × 2 アームの verdict を集め、census が揃ったときにだけ帯の判定を出す。

    設計 §6.2 / §11 の実装。**この関数だけが帯の判定を出す。**

    設計判断（rev.6 §8.9.5 実装ノート）:

    - **E-1 入力は verdict であって run report ではない。** report から再判定すると
      evaluate の全ゲート（測り直しによる独立検証・provenance 照合・pin 束縛）を
      二重実装することになり、2 つの判定経路が食い違う余地を作る。verdict は既に
      それらを通過した成果物なので、集計器は「揃っているか」だけを問う。
    - **E-2 期待セル数は積として再計算する**（`_m2e_census_expected_cells`）。
    - **E-3 census が揃うまで metrics を一切載せない。** §11 は部分集合での平均 RPA・
      途中の破断曲線・見通しの表明を禁じている。「出さない」ではなく「**成果物に
      存在させない**」ことで、下流が偶然読んでしまう経路ごと消す。
    - **E-4 環境同一性を要求する**（§8.7）。
    - **E-5 通過しても昇格しない。** `promotes_route` / `unlocks_m4_g2` を常に `false`
      として成果物に埋め込む（§5.4 / §7.2: 歌声と伴奏は別の曲であるため）。

    揃っていないときに出せるのは census のみ——完了セル数 / 期待数、(水準, アーム) 別の
    完了状況、欠けている組。**判定・平均・曲線は出さない。**
    """
    m2e_bars, m2e_bars_sha256 = load_bars(m2e_bars_path)
    m2e_bars_data = m2e_bars.verify(m2e_bars_sha256)
    # 共有スカラー（`repeats_min` 等）の供給元は M2e 側ではなく基底バーである（§5.1-4）。
    # 集計器は**自分で読んだ凍結ファイル**から `repeats_min` を取り、verdict の自己申告は
    # それとの一致を要求するだけにする（PR #241 Codex P2）。
    bars, bars_sha256 = load_bars(bars_path)
    bar_block = bars.verify(bars_sha256)["m2_accuracy_bars"]
    frozen_repeats_min = int(bar_block["repeats_min"])
    # E-26: `tolerance_cents` / `est_voiced_confidence_floor` も、`evaluate_m2_bars`
    # が `_require_frozen_tolerance` / `_require_frozen_est_voicing_floor` で読むのと
    # 同じ block（`bar_block`）から抽出する。census 自身の readback がここで凍結値を
    # 独自解釈すると、evaluate の関所と census の関所が別の「凍結値」を指しうる。
    frozen_tolerance_cents = float(bar_block.get("tolerance_cents", DEFAULT_TOLERANCE_CENTS))
    frozen_est_voiced_floor = float(bar_block["est_voiced_confidence_floor"])
    arms = _categories_owned_by("m2e_accuracy_bars.yaml")

    parsed: "List[Dict[str, Any]]" = []
    pins: "List[Dict[str, Any]]" = []
    for index, artifact in enumerate(verdicts):
        if not isinstance(artifact, ReportArtifact):
            raise ValueError(
                f"aggregate_m2e_census: verdicts[{index}] は load_verdict() が返す "
                f"artifact でなければならない（受け取った型: {type(artifact).__name__}）; "
                "digest と内容が切り離された入力を集計しない (fail-closed)"
            )
        data = artifact.verify()
        if data.get("schema_version") != _EXPECTED_VERDICT_SCHEMA:
            raise ValueError(
                f"aggregate_m2e_census: verdicts[{index}] の schema_version "
                f"{data.get('schema_version')!r} が {_EXPECTED_VERDICT_SCHEMA!r} でない "
                "(fail-closed)"
            )
        if not any(arm in data.get("categories", {}) for arm in arms):
            raise ValueError(
                f"aggregate_m2e_census: verdicts[{index}] に M2e カテゴリ "
                f"{list(arms)} が 1 つも無い; 帯と無関係な verdict を census の入力に "
                "数えない (fail-closed)"
            )
        parsed.append(data)
        pins.append(artifact.pin())

    common = _require_homogeneous_census_inputs(
        parsed,
        m2e_bars_sha256=m2e_bars_sha256,
        bars_sha256=bars_sha256,
        frozen_repeats_min=frozen_repeats_min,
        frozen_tolerance_cents=frozen_tolerance_cents,
        frozen_est_voiced_floor=frozen_est_voiced_floor,
    )
    repeats_min = frozen_repeats_min
    expected_cells = _m2e_census_expected_cells(repeats_min)

    conditions = m2e_bars_data[_BARS_FILES["m2e_accuracy_bars.yaml"]["conditions_key"]]

    # (level, arm) → セル情報。**同じ組が 2 回来たら拒否する**（同じ測定を 2 回数えて
    # census を満たしたことにしない——コピーした verdict で 1280 を埋められては
    # ならない）。
    observed: "Dict[str, Dict[str, Dict[str, Any]]]" = {}
    for index, data in enumerate(parsed):
        level = data.get("level")
        if level not in _M2E_LEVEL_LADDER:
            raise ValueError(
                f"aggregate_m2e_census: verdicts[{index}] の level {level!r} が事前登録 "
                f"ラダー {list(_M2E_LEVEL_LADDER)} にない (fail-closed)"
            )
        for arm in arms:
            cat = data.get("categories", {}).get(arm)
            if cat is None:
                continue
            if arm in observed.get(level, {}):
                raise ValueError(
                    f"aggregate_m2e_census: (level={level!r}, arm={arm!r}) の verdict が "
                    "複数ある; 同じ測定を二重に数えて census を満たしたことにしない "
                    "(fail-closed)"
                )
            # E-44: category 値が object でない場合、直後の `.get` 連鎖が
            # **AttributeError** で census 全体をクラッシュさせる（PR #241 Codex P2・
            # E-32「clip_ids の非 str 要素」と同型の残り穴）。ここで isinstance を
            # 検査し、malformed マーカー付きレコードを observed へ格納する——値そのもの
            # は埋め込まず type 名のみ記録する（E-33 の規律）。per-cell 検査段が
            # このマーカーを見て他の検査をスキップし、census_incomplete として報告する。
            if not isinstance(cat, dict):
                observed.setdefault(level, {})[arm] = {
                    "verdict_index": index,
                    "malformed_category": True,
                    "malformed_category_type": type(cat).__name__,
                }
                continue
            observed.setdefault(level, {})[arm] = {
                "verdict_index": index,
                "external_manifest_sha256": cat.get("external_manifest_sha256"),
                "external_fixtures_sha256": cat.get("external_fixtures_sha256"),
                "status": cat.get("status"),
                "n_rows": cat.get("n_rows"),
                "clip_ids": cat.get("clip_ids"),
                "repeats_bit_identical": cat.get("repeats_bit_identical"),
                "outcomes": cat.get("outcomes"),
                "bar_satisfied": cat.get("bar_satisfied"),
                "failures": cat.get("failures"),
                "gate_level": cat.get("gate_level"),
                "metrics": cat.get("metrics"),
            }

    cells: "Dict[str, Dict[str, Any]]" = {}
    missing: "List[Dict[str, Any]]" = []
    normalized_by_level: "Dict[str, Tuple[str, ...]]" = {}
    # per-cell 検査を通ったセルだけを集めた木（アーム間照合はこれを使う）。
    sound_cells: "Dict[str, Dict[str, Dict[str, Any]]]" = {}
    observed_cells = 0
    for level in _M2E_LEVEL_LADDER:
        for arm in arms:
            cell = observed.get(level, {}).get(arm)
            if cell is None:
                missing.append({"level": level, "arm": arm, "reason": "verdict_absent"})
                continue
            problems: "List[str]" = []
            # E-44: 収集段が malformed マーカーを積んだセル（category 値が object
            # でなかった verdict）はここで打ち切る（PR #241 Codex P2・E-32 と同型の
            # 残り穴）。以降の per-cell 検査は `cell["clip_ids"]` 等の直接キーアクセスを
            # 前提にしており、malformed マーカーはそれらのキーを持たない——検査を続けると
            # 別の KeyError で census 全体をクラッシュさせる。
            if cell.get("malformed_category"):
                problems.append(
                    f"category record が object でない"
                    f"（{cell.get('malformed_category_type')!r}）; 形の壊れた台帳を"
                    "クラッシュではなく未完として報告する"
                )
                missing.append({"level": level, "arm": arm, "reason": "; ".join(problems)})
                cells.setdefault(level, {})[arm] = {
                    "cells": 0,
                    "complete": False,
                    "problems": problems,
                }
                continue
            clip_ids = cell["clip_ids"]
            clip_ids_all_str = isinstance(clip_ids, list) and all(
                isinstance(c, str) for c in clip_ids
            )
            if not isinstance(clip_ids, list) or len(clip_ids) != _M2E_EXPECTED_ENTRIES_PER_LEVEL:
                problems.append(
                    f"clip 数 {len(clip_ids) if isinstance(clip_ids, list) else clip_ids!r} が "
                    f"凍結コホート {_M2E_EXPECTED_ENTRIES_PER_LEVEL} と不一致"
                )
            elif not clip_ids_all_str:
                # E-32: 要素型を先に検査する（PR #241 Codex P2・E-31 と同型の残り穴）。
                # 非 str 要素（dict/list 等）が混ざると直後の `set(clip_ids)` が
                # **TypeError**（unhashable type）で census 全体をクラッシュさせる——
                # 本来出すべき `census_incomplete` が出せない。全要素 str の場合のみ
                # 重複検査（このブロック）と `_m2e_normalized_cohort_ids`（下の水準タグ
                # 検査ブロック）へ進む。値そのものは埋め込まない（個数と index のみ）。
                non_str_indices = [i for i, c in enumerate(clip_ids) if not isinstance(c, str)]
                problems.append(
                    f"clip_ids に文字列でない要素がある（{len(non_str_indices)} 件・index "
                    f"{non_str_indices[:5]}）; 形の壊れた台帳をクラッシュではなく未完として "
                    "報告する"
                )
            elif len(set(clip_ids)) != len(clip_ids):
                # **件数だけでは足りない**（PR #241 Codex P2）。80 要素あっても重複して
                # いれば異なる測定は 80 未満であり、`observed_cells` は重複を数える。
                # 全水準・全アームで同じように重複していれば下の等値検査も通るため、
                # **1280 の異なる測定なしに「1280 セル完了」を報告できてしまう**。
                # `load_verdict` は受け取った bytes を hash するだけで一意性は証明しない。
                duplicated = sorted({cid for cid in clip_ids if clip_ids.count(cid) > 1})
                problems.append(
                    f"clip_ids に重複がある（{duplicated[:3]}"
                    f"{' ほか' if len(duplicated) > 3 else ''}・"
                    f"相異なるのは {len(set(clip_ids))} 件）; 重複を数えて census を "
                    "満たしたことにしない"
                )
            if cell["n_rows"] != repeats_min:
                problems.append(f"n_rows {cell['n_rows']!r} が repeats_min {repeats_min} と不一致")
            if cell["outcomes"] != ["measured"]:
                problems.append(f"outcomes {cell['outcomes']!r} が ['measured'] でない")
            if cell["repeats_bit_identical"] is not True:
                problems.append("repeats が bit 一致していない")
            if cell["status"] not in ("census_pending", "level_record_only"):
                problems.append(f"status {cell['status']!r} が M2e の想定値でない")
            # **`metrics` の形も要求する**（PR #241 Codex P2）。`load_verdict` は
            # top-level schema と bytes 束縛しか見ないので、`metrics` が欠損・`null`・
            # 短縮でも他の検査は全部通り、`census_complete` を出したうえで
            # `level_response` に欠測が載る（帯の判定だけは `bar_satisfied` から出る）。
            # 成果物に載せる値は、載せる前に形を確かめる。
            # E-33: この節の不備理由は**計測 field 名・値を含めない**（PR #241 Codex
            # P2）。E-3 は「census が揃うまで metrics を成果物に存在させない」を宣言して
            # おり、テストは文書全 bytes への文字列不在まで検査している——その禁止は
            # `census_complete` 時の `level_response` だけでなく、`census_incomplete` 時
            # の `missing[].reason` にも及ぶ。validator 例外テキスト（field 名・値入り）
            # をそのまま埋めていたのは自己違反だった。詳細診断は census の仕事ではなく、
            # verdict 側を直接読めば得られる——ここでは一般コードだけを報告する。
            metrics_list = cell["metrics"]
            if not isinstance(metrics_list, list) or len(metrics_list) != repeats_min:
                problems.append(f"計測記録が {repeats_min} 件の list でない")
            else:
                # E-31: 要素型を先に検査する（PR #241 Codex P2）。外側 list の長さしか
                # 見ていないと、要素が `null` 等の非 dict のとき `_require_finite_metrics`
                # 内の `in` 演算が **TypeError** を投げ、`except ValueError` を素通りして
                # census 全体がクラッシュする——本来出すべき `census_incomplete` が
                # 出せない（E-11 の裁定違反状態）。`except` を `(ValueError, TypeError)`
                # へ広げる案は採らない（genuine bug を握りつぶす）——明示の型検査で先に
                # 落とすのが正しい。型検査を通った場合のみ深い検査（有限性・平均安定
                # 不変条件）へ進む。
                non_object_indices = [
                    i for i, m in enumerate(metrics_list) if not isinstance(m, dict)
                ]
                if non_object_indices:
                    problems.append(
                        f"計測記録に JSON object でない要素がある（{len(non_object_indices)} 件）"
                    )
                else:
                    # E-34: `except` は `(ValueError, OverflowError)` に広げる（PR #241
                    # Codex P2）。400 桁級の JSON 整数（`isinstance(value, int)` は
                    # 真だが `float(value)` が表現できない）が metrics に入ると、この
                    # 呼び出し内の `float()` 変換が **OverflowError** を投げ、
                    # `except ValueError` を素通りして census 全体がクラッシュする
                    # （E-31/E-32 と同型: 本来出すべき `census_incomplete` が出せない）。
                    # **E-31 とは except 拡大の裁定が異なる**: TypeError は「呼び出し側
                    # の前提（要素が mapping であること）の検査漏れ」の信号であり、
                    # 前提は明示検査で塞ぐのが正解だった。対して OverflowError は
                    # 「値が有限 float で表現できない」という**この validator が判定
                    # すべき値域違反そのもの**であり、事前検査で塞ぐには float 変換の
                    # 意味論を複製することになる——だから except を広げる。
                    try:
                        _require_finite_metrics(arm, metrics_list)
                    except (ValueError, OverflowError):
                        problems.append("計測記録が有限数値の契約を満たさない")
                    # E-27: 平均で保存される不変条件のみ再適用する（`_require_
                    # average_stable_metric_invariants` docstring に採否の経緯を記録）。
                    # `float()` 変換を含むため E-34 と同じ理由で OverflowError も拾う。
                    try:
                        _require_average_stable_metric_invariants(
                            arm, metrics_list, frozen_tolerance_cents=frozen_tolerance_cents
                        )
                    except (ValueError, OverflowError):
                        problems.append("計測記録が平均安定不変条件を満たさない")
                # E-28: `repeats_bit_identical` の申告は評価器の独立測り直しが立証する
                # が、census が**公開する** per-repeat metrics 自体が申告どおり相互
                # bit 一致していることは、boolean 申告と独立に検査できる必要条件
                # （PR #241 Codex P1）。bit 一致を名乗りながら公開 metrics が repeat 間で
                # 食い違う verdict を数えない。**十分条件ではない**——平均が偶然一致して
                # clip が異なるケースは検出できない（宣言された限界、設計ノート E-28）。
                if len({json.dumps(m, sort_keys=True) for m in metrics_list}) != 1:
                    problems.append("計測記録が repeat 間で bit 一致しない")
            # E-20: アーム対の素性 hash にも存在と形を要求する（PR #241 Codex P1・E-17 と
            # 同じ論法）。両アームが揃って欠けば `None == None` でアーム間照合が空転し、
            # 無関係な音源世代の対が「対」として数えられる。
            for provenance_key in ("external_manifest_sha256", "external_fixtures_sha256"):
                if not _is_sha256(cell[provenance_key]):
                    problems.append(
                        f"{provenance_key} {cell[provenance_key]!r} が 64-hex sha256 でない"
                        "（素性を名乗らないアームを対にしない）"
                    )
            counted = (
                len(clip_ids) * cell["n_rows"]
                if isinstance(clip_ids, list) and isinstance(cell["n_rows"], int)
                else 0
            )
            if clip_ids_all_str:
                # 水準タグの検査は**件数と独立**に行う（短いコホートでもラベルの
                # 食い違いは食い違いである）。正規化結果を水準横断の照合に使うのは
                # per-cell 検査を通ったセルだけ——短いコホートを「別コホート」として
                # 報告すると、本当の原因（件数不足）が見えなくなる。
                # E-32: `_m2e_normalized_cohort_ids` 自身も非 str 要素で ValueError を
                # 投げる（E-8 の fail-closed）が、それは「捕まえて problems へ落とす」
                # 設計ではなく「呼ばない」設計にする——`clip_ids_all_str` で事前に
                # 型を揃えたセルだけがここへ到達する。
                normalized = _m2e_normalized_cohort_ids(level, clip_ids)
                if not problems:
                    normalized_by_level[level] = normalized
            if problems:
                missing.append(
                    {"level": level, "arm": arm, "reason": "; ".join(problems)}
                )
            else:
                observed_cells += counted
                sound_cells.setdefault(level, {})[arm] = cell
            cells.setdefault(level, {})[arm] = {
                "cells": counted,
                "complete": not problems,
                "problems": problems,
            }
    # アーム間で**同じミックスを測ったこと**を要求する（§6.2「アームは manifest を
    # 分けない」の下流検査）。件数が揃っていても中身がずれていれば別の帯である。
    #
    # **id の一致だけでは足りない**（PR #241 Codex P1）。2 つの manifest が同じ 80 個の
    # `clip_ids` を持ちながら、別世代の音声・別世代の登録簿を指すことはありうる——
    # id は名前であって bytes ではない。row は既に `external_manifest_sha256` /
    # `external_fixtures_sha256` を運んでいるので、**素性の hash そのもの**を照合する。
    #
    # 照合は **per-cell 検査を通ったセル同士**でのみ行う（PR #241 Codex P2）。片アームが
    # `insufficient_repeats` で欠けている水準は「部分測定」であって「別素材」ではない
    # ——完了したアームの値と欠けたアームの `None` を突き合わせて raise すると、
    # **census が本来出すべき `census_incomplete` の報告そのものが出せなくなる**。
    # 部分測定を報告するのが census の目的なので、ここで落としてはならない。
    for level, per_arm in sorted(sound_cells.items()):
        for field, what in (
            ("clip_ids", "clip_ids"),
            ("external_manifest_sha256", "external_manifest_sha256"),
            ("external_fixtures_sha256", "external_fixtures_sha256"),
        ):
            values = {
                arm: tuple(cell[field]) if isinstance(cell[field], list) else cell[field]
                for arm, cell in per_arm.items()
            }
            if len(set(values.values())) > 1:
                raise ValueError(
                    f"aggregate_m2e_census: level {level!r} のアーム間で {what} が一致しない; "
                    "同じミックスを 2 経路で測るのがアーム比較の定義であり、別素材・別世代の "
                    "観測を同じ水準の 2 アームとして並べない (fail-closed)"
                )

    # **4 水準が同じコホートを測ったこと**を要求する（PR #241 Codex P1）。水準ごとに
    # fixtures 世代が違えば、各水準は 80 件を満たしながら**別の 80 件**でありうる——
    # アーム間の照合は同一水準の中しか見ておらず、水準を跨いだ同一性は誰も問うて
    # いなかった。id 規約の水準タグを剥がした正規化コホートで突き合わせる
    # （`normalized_by_level` は上の per-cell ループで、検査を通ったセルだけ埋まる）。
    distinct_cohorts = set(normalized_by_level.values())
    if len(distinct_cohorts) > 1:
        differing = sorted(normalized_by_level)
        raise ValueError(
            f"aggregate_m2e_census: 水準間で正規化コホートが一致しない（{differing} の "
            "うち少なくとも 1 つが別の (clip, bed) 集合）; 別コホートの水準を並べた破断"
            "曲線は 1 本の曲線ではない (fail-closed)"
        )

    complete = not missing and observed_cells == expected_cells

    census: Dict[str, Any] = {
        "schema_version": _M2E_CENSUS_SCHEMA,
        "census_recorded_utc": _utc_now(),
        "generator_code_sha256": common["generator_code_sha256"],
        "evaluator_code_sha256": common["evaluator_code_sha256"],
        "bars_sha256": bars_sha256,
        "bars_path_relative": _repo_relative_path(bars_path),
        "m2e_bars_sha256": m2e_bars_sha256,
        "m2e_bars_path_relative": _repo_relative_path(m2e_bars_path),
        "env_digest": common["env_digest"],
        # E-26: `common[...]` ではなく凍結値を書く（E-7 の `repeats_min` と同型）。
        # `_require_homogeneous_census_inputs` は verdict の申告が凍結値と厳密一致する
        # ことを既に検査しているので値は同じだが、成果物の供給源を「集計器が読んだ
        # 凍結ファイル」に統一する。
        "tolerance_cents": frozen_tolerance_cents,
        "est_voiced_confidence_floor": frozen_est_voiced_floor,
        "repeats_min": repeats_min,
        "levels": list(_M2E_LEVEL_LADDER),
        "arms": list(arms),
        "expected_cells_total": expected_cells,
        "observed_cells_total": observed_cells,
        "cells": cells,
        "missing": missing,
        "complete": complete,
        "verdict_pins": pins,
        # E-5: 通過しても昇格しない（§5.4 / §7.2）。**成果物に埋め込む**——読み手が
        # 設計文書へ戻らなくても、この帯が何を解錠しないかが判定と同じ場所にある。
        "promotes_route": False,
        "unlocks_m4_g2": False,
        "declared_limits": [
            "歌声（vocadito）と伴奏（MUSDB18-HQ）は別の曲であり、和声的に不整合である。"
            "抽出が易しくなるか難しくなるかは一意に決まらないため、本帯の結果を根拠に "
            "V_fullstack へ昇格させない（§7.2）。",
            "ベッドは MUSDB18-HQ test split の先頭 2 曲であり、ジャンル・編成の代表性を "
            "主張しない（§7.2）。",
            "+12dB は実運用のミックス balance ではなくトリップワイヤ専用の人工的水準 "
            "である（§7.2）。",
            "stem アームは水準軸に沿った単調性を仮定しないため、「+12dB で割れたら下の "
            "水準も割れる」という下方伝播を主張しない（§5.4）。",
        ],
    }

    if not complete:
        # §11: 揃わないまま出せるのは**センサスのみ**。平均 RPA・破断曲線・
        # 「通りそう / 落ちそう」の見通しを成果物に**存在させない**（E-3）。
        census["band_verdict"] = None
        census["level_response"] = None
        census["status"] = "census_incomplete"
        return census

    # ここから先は census が揃った場合のみ。
    band: Dict[str, Any] = {}
    for arm in arms:
        gate_level = conditions[arm]["gate_level"]
        cell = observed[gate_level][arm]
        if cell["status"] != "census_pending" or cell["bar_satisfied"] is None:
            raise ValueError(
                f"aggregate_m2e_census: arm {arm!r} の gate_level {gate_level!r} の "
                f"verdict にバー適用の証拠（bar_satisfied）が無い（status="
                f"{cell['status']!r}）; バーが当たっていない水準から帯の判定を出さない "
                "(fail-closed)"
            )
        # **bool そのものを要求する**（PR #241 Codex P2）。`is None` を通っただけでは
        # `"false"` のような非空文字列が残り、下の真偽評価は**真**になる——
        # **fail が pass として publish される**、この機構で最悪の失敗形である。
        # `load_verdict` は bytes 束縛と top-level schema しか見ないので、category
        # フィールドの型は集計器が独立に要求する。
        if not isinstance(cell["bar_satisfied"], bool):
            raise ValueError(
                f"aggregate_m2e_census: arm {arm!r} の bar_satisfied "
                f"{cell['bar_satisfied']!r} が bool でない; 真偽でない値を真偽として "
                "評価すると fail が pass に化ける (fail-closed)"
            )
        # E-22: 帯判定セルの gate_level 申告を凍結条件と束縛する（PR #241 Codex P1）。
        # セルの選択は verdict の自己申告（top-level `level`）に依存しているため、
        # category 側の `gate_level` 申告も凍結値（`conditions[arm]["gate_level"]`）と
        # 束縛しなければ、別水準で当てたバーの結果が帯の判定として publish されうる。
        if cell["gate_level"] != gate_level:
            raise ValueError(
                f"aggregate_m2e_census: arm {arm!r} の verdict が名乗る gate_level "
                f"{cell['gate_level']!r} が凍結条件の {gate_level!r} と不一致; 別水準で "
                "当てたバーの結果を帯の判定として publish しない (fail-closed)"
            )
        # 併記する `failures` も形を要求する（成果物へそのまま載せるため）。
        if cell["failures"] is not None and not isinstance(cell["failures"], list):
            raise ValueError(
                f"aggregate_m2e_census: arm {arm!r} の failures {cell['failures']!r} が "
                "list でない; 判定の根拠として成果物へ載せる値の形を確かめる (fail-closed)"
            )
        # E-45: `failures` の各要素も非空文字列であることを要求する（PR #241 Codex P2）。
        # `failures: [null]` は直前の list 型検査・下の `bar_satisfied == not failures`
        # 整合検査の両方を通過してしまい、非 str 要素がそのまま band_verdict へ publish
        # される。E-16/E-18/E-22 と同じ層（帯 publish の fail-closed 検査）に揃える。
        # 値そのものは埋め込まない——非 str 要素の index のみ記載する。
        if cell["failures"] is not None:
            bad_failure_indices = [
                i for i, f in enumerate(cell["failures"]) if not (isinstance(f, str) and f)
            ]
            if bad_failure_indices:
                raise ValueError(
                    f"aggregate_m2e_census: arm {arm!r} の failures の要素 index "
                    f"{bad_failure_indices} が非空文字列でない; 判定の根拠として成果物へ "
                    "載せる値の形を確かめる (fail-closed)"
                )
        # **`evaluate_m2_bars` が確立した不変条件を読み戻しで再検証する**
        # （PR #241 Codex P2）: `bar_satisfied == not failures`。型が正しくても関係が
        # 壊れていれば、`bar_satisfied: true` + 非空 `failures` は**失敗の証拠を同梱
        # したまま pass を publish する**し、逆は理由の無い fail を publish する。
        # 型を要求しただけでは足りない——**値どうしの整合も要求する**。
        arm_failures = cell["failures"] or []
        if cell["bar_satisfied"] != (not arm_failures):
            raise ValueError(
                f"aggregate_m2e_census: arm {arm!r} の bar_satisfied "
                f"{cell['bar_satisfied']!r} が failures {arm_failures!r} と矛盾する "
                "（evaluate は bar_satisfied == not failures を確立している）; "
                "根拠と結論が食い違う判定を publish しない (fail-closed)"
            )
        # E-23: 凍結閾値を census 自身が再適用し、metrics と bar_satisfied の整合を要求する
        # （PR #241 Codex P1）。E-18 は bar_satisfied↔failures を束縛したが、metrics だけを
        # 書き換えれば「凍結バーを割る metrics を level_response に載せながら pass を出す」
        # 成果物が組めた。比較方向は evaluate と同一（min_rpa は < / max_vfa は > /
        # max_octave_gap は >）。failures の文字列照合はしない——E-18 が bar_satisfied↔
        # failures を、本検査が metrics↔bar_satisfied を束縛すれば連鎖は閉じる（文字列
        # 形式への結合は brittle で over-engineering）。per-cell 検査で
        # repeats_bit_identical is True を既に要求しているため、evaluate 側で bit 不一致
        # 由来の failure が混ざるケースは band ループへ到達しない（整合は閾値のみで閉じる）。
        frozen_bar = m2e_bars_data[_BARS_FILES["m2e_accuracy_bars.yaml"]["block_key"]].get(arm, {})
        recomputed_satisfied = True
        for metrics in cell["metrics"]:
            if "min_rpa" in frozen_bar and metrics["raw_pitch_accuracy"] < frozen_bar["min_rpa"]:
                recomputed_satisfied = False
            if "max_vfa" in frozen_bar and metrics["voicing_false_alarm"] > frozen_bar["max_vfa"]:
                recomputed_satisfied = False
            if "max_octave_gap" in frozen_bar and metrics["octave_gap"] > frozen_bar["max_octave_gap"]:
                recomputed_satisfied = False
        if recomputed_satisfied != cell["bar_satisfied"]:
            raise ValueError(
                f"aggregate_m2e_census: arm {arm!r} の bar_satisfied "
                f"{cell['bar_satisfied']!r} が、凍結バーを metrics へ再適用した結果 "
                f"{recomputed_satisfied!r} と不一致; 判定と計測値が食い違う verdict を "
                "publish しない (fail-closed)"
            )
        band[arm] = {
            "gate_level": gate_level,
            "status": "pass" if cell["bar_satisfied"] else "fail",
            "failures": cell["failures"] or [],
        }
    census["band_verdict"] = band
    # §11「4 水準は常に全点提示する」——事後に「一番良かった水準」を選んで報告する
    # ことは禁止なので、成果物は常にラダー全点を持つ。
    census["level_response"] = {
        arm: [
            {
                "level": level,
                "ladder_index": _m2e_ladder_index(level),
                "metrics": observed[level][arm]["metrics"],
            }
            for level in _M2E_LEVEL_LADDER
        ]
        for arm in arms
    }
    census["status"] = "census_complete"
    return census


# ---------------------------------------------------------------------------
# C6 — シャード実行機（rev.6 §8.4-§8.8・地図生成器 `--make-shard-map` + 消費器
# `--shard-id`/`--shard-map`）
# ---------------------------------------------------------------------------
#
# 設計 Memo `.claude/briefs/M2E-C6-shard-runner.md` の実装。シャード地図は科学ではなく
# スケジューリングである（§8.5）——セル台帳（fixtures が決める 1280 セルの集合）は
# 不可侵、シャード地図（どのセルをどの回に回すか）は `T_*`/`S`/`B_session` が変われば
# 再計算してよい。本節は 2 つの成果物だけを持つ: (a) 地図生成器
# `generate_m2e_shard_map`、(b) 1 shard 分の実行機 `execute_m2e_shard`。
# **shard モードは run report / verdict / census のいずれも出さない**——per-level の
# run report は、全セル完了後に既存の「1 水準まるごと」run が store から 100% resume
# して生成する（M2e report の単一水準不変条件は無変更）。

_EXPECTED_M2E_CAMPAIGN_SCHEMA = "m2e-campaign/0.1"
_M2E_SHARD_MAP_SCHEMA = "m2e-shard-map/0.1"
_M2E_SHARD_RUN_SCHEMA = "m2e-shard-run/0.1"
# §8.5: 余裕係数（凍結・変更禁止。実測が速かったからといって上げてはならない）。
_M2E_SHARD_CAP_MARGIN = 0.85
# §8.2 の既定 B_session（秒）。CLI `--session-budget` の既定値と同じ。
_M2E_DEFAULT_SESSION_BUDGET_S = 7200.0
# §8.8: 実行回数の上限（凍結）。12 → 18 へ改訂（2026-08-05 User 決裁・
# docs/measurements/m2e_2026-08/r_max_decision_2026-08-05.md。§8.8 の 3 択で
# 「R_max を引き上げる」を採用。規模は不変・回数だけ増える）。
_M2E_R_MAX = 21
# §8.6: ハングの絶対上限は B_session + 600s（凍結）。
_M2E_HANG_GRACE_S = 600.0
# 動的キューのポーリング間隔（実装の都合値。時間予算そのものには影響しない）。
_M2E_SHARD_POLL_INTERVAL_S = 0.05


def _require_m2e_campaign_path_confined_to_root(
    value: str, *, campaign_path: Path, level: str, key: str
) -> Path:
    """campaign が指す相対パスを ROOT 配下へ封じ込める（E-60・PR #242 第4巡 Codex 是正）。

    二段検証: (1) 字句——絶対パス・`..` 成分を拒否（symlink 解決の前に意図を弾く）、
    (2) 解決後——`(ROOT / value).resolve()` が ROOT 配下（`Path.is_relative_to`）に
    留まることを要求する（symlink 経由で ROOT 外へ脱出する構成も (1) をすり抜けた
    後にここで拒否される）。`_parse_m2e_campaign_bytes` からのみ呼ぶ——生成
    （`generate_m2e_shard_map`）・実行（`execute_m2e_shard`）の双方がここを通る
    単一の campaign loader 経路（`_load_m2e_campaign_with_sha256` / `_load_m2e_campaign`）
    に一元実装する。エラーメッセージは campaign が宣言した相対パス文字列のみを含み、
    解決後の絶対パスは含めない (fail-closed 流儀)。
    """
    candidate = Path(value)
    if candidate.is_absolute():
        raise ValueError(
            f"{campaign_path}: levels[{level!r}].{key} が絶対パス {value!r} を指す; "
            "repo root からの相対パスのみ許可する (fail-closed)"
        )
    if ".." in candidate.parts:
        raise ValueError(
            f"{campaign_path}: levels[{level!r}].{key} の {value!r} が `..` 成分を含む; "
            "repo root 配下からの遡上を許可しない (fail-closed)"
        )
    resolved = (ROOT / candidate).resolve()
    if not resolved.is_relative_to(ROOT):
        raise ValueError(
            f"{campaign_path}: levels[{level!r}].{key} の {value!r} が解決後に repo root "
            "の外を指す（symlink 経由の脱出を含む）; repo root 配下のみ許可する "
            "(fail-closed)"
        )
    return resolved


def _parse_m2e_campaign_bytes(
    campaign_path: Path, data: bytes
) -> "Dict[str, Dict[str, Path]]":
    """既に読み込んだ campaign bytes を parse する（single-read 束縛の内部実装）。

    `_load_m2e_campaign_with_sha256` からのみ呼ぶ——直接呼ぶと read と parse が別の
    bytes に由来しうる（E-52 が塞ごうとしている穴そのもの）。
    """
    doc = _yaml_load_no_dup_keys(data, what=campaign_path.name)
    if not isinstance(doc, dict) or doc.get("schema_version") != _EXPECTED_M2E_CAMPAIGN_SCHEMA:
        raise ValueError(
            f"{campaign_path}: schema_version が {_EXPECTED_M2E_CAMPAIGN_SCHEMA!r} でない "
            "(fail-closed)"
        )
    levels = doc.get("levels")
    if not isinstance(levels, dict) or set(levels) != set(_M2E_LEVEL_LADDER):
        raise ValueError(
            f"{campaign_path}: levels のキー集合 "
            f"{sorted(levels) if isinstance(levels, dict) else levels!r} が凍結ラダー "
            f"{list(_M2E_LEVEL_LADDER)} と一致しない (fail-closed)"
        )
    resolved: "Dict[str, Dict[str, Path]]" = {}
    for level, level_paths in levels.items():
        if not isinstance(level_paths, dict) or set(level_paths) != {
            "external_manifest",
            "external_fixtures",
        }:
            raise ValueError(
                f"{campaign_path}: levels[{level!r}] が "
                "{external_manifest, external_fixtures} ちょうどを持たない (fail-closed)"
            )
        resolved_level: "Dict[str, Path]" = {}
        for key, value in level_paths.items():
            if not isinstance(value, str) or not value:
                raise ValueError(
                    f"{campaign_path}: levels[{level!r}].{key} が非空文字列でない "
                    "(fail-closed)"
                )
            # repo root 基準で解決する（campaign ファイル自身の位置基準ではない）。
            # HANDOFF §5 のレシピは `build/m2e/...` / `tests/fixtures/...` を repo
            # root からの相対パスとして書く規約なので、それとそのまま揃える——
            # campaign 自身は `docs/measurements/m2e_2026-08/` に置く想定であり、
            # ファイル位置基準だと `../../../build/...` のような脆いパスを強いる。
            # E-60: ROOT 配下への封じ込め（絶対パス・`..` 遡上・symlink 脱出を拒否）。
            resolved_level[key] = _require_m2e_campaign_path_confined_to_root(
                value, campaign_path=campaign_path, level=level, key=key
            )
        resolved[level] = resolved_level
    return resolved


def _load_m2e_campaign_with_sha256(
    path: "str | Path",
) -> "Tuple[Dict[str, Dict[str, Path]], str]":
    """M2e campaign ファイル（パスのみ）を single read で (parsed, sha256) として返す。

    設計判断 2（Memo M2E-C6）: campaign は**パスのみ**を持つ——各水準の external
    manifest / external fixtures の所在。科学的パラメータ（`T_*`/`S`/`B_session`等）は
    一切含めない（それは CLI 引数 / bars / fixtures / 地図側の責務）。凍結ラダー 4 水準
    ちょうどを要求する (fail-closed)。パスは repo root（`ROOT`）基準で相対解決する。

    E-52（PR #242 第2巡 Codex P2 是正）: `campaign_sha256` の計算と parse を**同一
    bytes スナップショット**から導出する。別々に `read_bytes()` すると、呼び出しの
    間にファイル（またはシンボリックリンク先）が差し替わった場合、digest と parse
    結果が別 bytes に由来しうる——地図が「実際に registry を供給した bytes ではない
    bytes」を pin することになる。
    """
    campaign_path = Path(path).resolve()
    data = campaign_path.read_bytes()
    resolved = _parse_m2e_campaign_bytes(campaign_path, data)
    return resolved, hashlib.sha256(data).hexdigest()


def _load_m2e_campaign(path: "str | Path") -> "Dict[str, Dict[str, Path]]":
    """`_load_m2e_campaign_with_sha256` の doc のみを返す薄いラッパ。

    sha256 を必要としない軽量な読み取り専用（protected-path 集合の構築等）向け。
    digest とセットで必要な経路（地図生成・実行）は
    `_load_m2e_campaign_with_sha256` を直接使うこと（E-52・単一読取束縛）。
    """
    resolved, _sha256 = _load_m2e_campaign_with_sha256(path)
    return resolved


def _m2e_parse_entry_id(entry_id: str) -> "Tuple[str, str]":
    """entry id `vremix_{clip_id}_{bed_id}_{level_tag}`（§6.2）から (clip_id, bed_id) を返す。

    `_require_registered_m2e_cohort` と同じ切り出し規約（`_m2e_bed_slug` は非英数字を
    ハイフンにしか正規化しないため bed_id は下線を含まず、末尾から 2 番目の下線区切り
    要素が bed_id と一意に決まる）。
    """
    parts = str(entry_id).split("_")
    if len(parts) < 4 or parts[0] != "vremix":
        raise ValueError(
            f"_m2e_parse_entry_id: entry id {entry_id!r} が §6.2 の規約 "
            "`vremix_{clip_id}_{bed_id}_{level_tag}` に合わない (fail-closed)"
        )
    bed_id = parts[-2]
    clip_id = "_".join(parts[1:-2])
    return clip_id, bed_id


def _m2e_entry_id(clip_id: str, bed_id: str, level: str) -> str:
    """(clip_id, bed_id, level) → entry id（§6.2 の逆写像）。"""
    return f"vremix_{clip_id}_{bed_id}_{_M2E_LEVEL_TAGS[level]}"


def _m2e_full_cell_registry(
    campaign: "Dict[str, Dict[str, Path]]",
    *,
    bars_path: Path = BARS_PATH,
    bars_snapshot: "Optional[Tuple[Any, str]]" = None,
) -> "Tuple[List[Tuple[str, str, str, str, int]], Dict[str, str], int, str, Dict[str, Dict[str, Any]]]":
    """§8.5 のセル台帳（1280 セル）を lexical order で列挙する。

    戻り値: (cells, fixtures_sha256_by_level, repeats_min, bars_sha256,
    fixtures_by_level)。`cells` の各要素は `(bed_id, level, clip_id, arm,
    repeat_index)` の 5-tuple——設計 §8.5 の `(bed_id, level, clip_id, arm,
    repeat_idx)` lexical order そのもの（level はラダー添字ではなく**文字列の
    まま**——決定済み設計判断 3: `'+12dB' < '+6dB' < '-6dB' < '0dB'` という物理量と
    無関係な順序になるが「直して」はならない）。`fixtures_by_level` は各水準の
    `fixtures_doc["fixtures"]`（検証済みの生 dict）——E-57・PR #242 第3巡 Codex P2
    是正: 呼び出し元がこの検証済みスナップショットを引き回し、同じ fixtures ファイルを
    再度開かない（TOCTOU 回避。E-52 と同族）。

    台帳は fixtures（+ 基底バーの `repeats_min`）だけから決まる——manifest はここでは
    読まない（manifest 未生成のうちに地図を作れるようにするため。§8.5「シャード地図は
    科学ではなくスケジューリングである」の実装上の帰結: 地図生成は音声実体を必要と
    しない）。

    `bars_path`（E-47・PR #242 Codex P2 是正）: `--make-shard-map --bars <custom>` が
    指定した bars を registry 構築・検証まで貫通させる。既定はモジュール既定の
    `BARS_PATH`（従来どおりの挙動）。戻り値の `bars_sha256` を地図に刻むことで、
    別世代の bars で組まれた地図を後から検出できる（`_require_m2e_shard_map_matches_
    registry` が照合する）。

    `bars_snapshot`（E-78・PR #242 第8巡 Codex 是正）: 呼び出し元が既に読んだ
    `(bars, bars_sha256)` を渡せば、ここでは bars ファイルを再度開かない（E-57/E-72
    と同族の TOCTOU 是正）。未指定（既定 `None`）なら従来どおり `bars_path` から読む。
    """
    if bars_snapshot is not None:
        bars, bars_sha256 = bars_snapshot
    else:
        bars, bars_sha256 = load_bars(bars_path)
    bar_block = bars.verify(bars_sha256)["m2_accuracy_bars"]
    repeats_min = int(bar_block["repeats_min"])
    arms = _categories_owned_by("m2e_accuracy_bars.yaml")

    fixtures_sha256_by_level: "Dict[str, str]" = {}
    fixtures_by_level: "Dict[str, Dict[str, Any]]" = {}
    cells: "List[Tuple[str, str, str, str, int]]" = []
    for level in _M2E_LEVEL_LADDER:
        if level not in campaign:
            raise ValueError(
                f"_m2e_full_cell_registry: campaign に水準 {level!r} が無い (fail-closed)"
            )
        fixtures_path = campaign[level]["external_fixtures"]
        fixtures_doc, fixtures_sha256 = load_external_fixtures(fixtures_path)
        _require_external_fixtures_level_match(
            fixtures_doc, level=level, where=f"_m2e_full_cell_registry: level {level!r}"
        )
        fixtures_sha256_by_level[level] = fixtures_sha256
        fixtures_by_level[level] = fixtures_doc["fixtures"]
        for entry_id in fixtures_doc["fixtures"]:
            clip_id, bed_id = _m2e_parse_entry_id(entry_id)
            for arm in arms:
                for repeat_index in range(repeats_min):
                    cells.append((bed_id, level, clip_id, arm, repeat_index))
    cells.sort()
    return cells, fixtures_sha256_by_level, repeats_min, bars_sha256, fixtures_by_level


def _m2e_shard_cell_cost(arm: str, *, t_direct: float, t_stem: float) -> float:
    """§8.5 の `cost(cell) = T_direct if cell.arm == "direct" else T_stem`。"""
    return t_direct if arm == "V_remix_real_direct" else t_stem


def _assign_m2e_shard_ids(
    cells: "List[Tuple[str, str, str, str, int]]",
    *,
    t_direct: float,
    t_stem: float,
    startup_cost: float,
    session_budget: float,
) -> "Tuple[List[int], float, int]":
    """§8.5 の凍結アルゴリズムを逐語実装する（アルゴリズムの改良禁止）。

    ```
    cap = 0.85 * B_session - S
    cost(cell) = T_direct if cell.arm == "direct" else T_stem
    order = 全セルを (bed_id, level, clip_id, arm, repeat_idx) の lexical order で整列
    s = 0 ; acc = 0
    for cell in order:
        c = cost(cell)
        if acc > 0 and acc + c > cap:
            s += 1 ; acc = 0
        cell.shard_id = s
        acc += c
    N_shards = s + 1
    ```

    `cells` は呼び出し元が既に lexical order で整列済みであること
    （`_m2e_full_cell_registry` が保証する）。戻り値は `(shard_ids, cap, n_shards)`。

    `session_budget`（E-62・PR #242 第4巡 Codex P2 是正）・全入力（E-68・PR #242
    第5巡 Codex P2 是正）: `inf`/`nan` を拒否する（生成・実行の両受け口——本関数は
    `generate_m2e_shard_map` と `_require_m2e_shard_map_matches_registry` の双方から
    呼ばれる共有経路）。`session_budget = inf` だと cap も無限大になり、
    `N_shards <= R_max` の関所を 1 shard のまま素通りして §8.8 の実行回数上限も
    §8.6 のハング絶対上限も無効化してしまう。`startup_cost = nan` は
    `cap = margin*B_session - NaN = NaN` を生み、以降の全比較が `False` になって
    1 セル容量ゲートも `R_max` ゲートも無検査で素通りする——`t_direct`/`t_stem` の
    非有限も `max(t_direct, t_stem)` を介して同じ穴になりうる。改変された地図
    （実行側の再計算はここを経由する）がこれらを持ち込む経路も塞ぐため、4 入力
    全数を検査する（E-68 の同型穴の列挙原則を実行側にも及ぼす）。
    """
    # E-91（PR #242 第13巡 Codex 是正）: E-68 は isfinite のみをこの共有 readback
    # 経路（本関数は generate/readback 双方から呼ばれる）へ及ぼしていたが、符号
    # 制約（T_direct>0/T_stem>0/B_session>0/S>=0）は生成器側の入口検査
    # （`_require_m2e_shard_map_finite_input`）にしか無かった——改変された地図が
    # 負の t_direct 等を持ち込んでも、isfinite さえ満たせば readback を素通り
    # しえた。単一のバリデータへ集約し、生成・読取の両方に同じ入力域制約を適用する。
    _require_m2e_shard_map_finite_input("session_budget", session_budget)
    _require_m2e_shard_map_finite_input("startup_cost", startup_cost, allow_zero=True)
    _require_m2e_shard_map_finite_input("t_direct", t_direct)
    _require_m2e_shard_map_finite_input("t_stem", t_stem)
    cap = _M2E_SHARD_CAP_MARGIN * session_budget - startup_cost
    if cap <= 0 or cap < max(t_direct, t_stem):
        raise ValueError(
            f"generate_m2e_shard_map: cap={cap!r} (= {_M2E_SHARD_CAP_MARGIN} * "
            f"B_session({session_budget!r}) - S({startup_cost!r})) が 0 以下か "
            f"max(T_direct={t_direct!r}, T_stem={t_stem!r}) を下回る; 1 セルすら 1 回の "
            "実行に収まらない。本測定は開始しない。S / T_direct / T_stem を添えて "
            "User 決裁へ差し戻す (fail-closed・設計 §8.5)"
        )
    shard_ids: "List[int]" = []
    s = 0
    acc = 0.0
    for _bed_id, _level, _clip_id, arm, _repeat_index in cells:
        c = _m2e_shard_cell_cost(arm, t_direct=t_direct, t_stem=t_stem)
        if acc > 0 and acc + c > cap:
            s += 1
            acc = 0.0
        shard_ids.append(s)
        acc += c
    n_shards = s + 1
    return shard_ids, cap, n_shards


def _require_m2e_shard_map_finite_input(name: str, value: float, *, allow_zero: bool = False) -> None:
    """生成器の float 入力に共通の isfinite + 符号検査を統一的に敷く

    （E-68・PR #242 第5巡 Codex P2 是正）。

    `--startup-cost nan` は `startup_cost < 0` を素通りし `cap = margin * B_session -
    NaN = NaN` を生む——以降の `cap <= 0` 等の全比較が NaN 相手には常に `False` に
    なるため、1 セル容量ゲートも `R_max` ゲートも無検査で素通りし、1280 セルが
    まるごと 1 shard に詰め込まれる。`t_direct > 0` のような単純な符号比較だけでは
    `inf`（正数比較は素通りする）も `nan`（比較は常に `False` になるので符号検査
    自体は偶然に落ちるが、根本原因は同じ「非有限値が算術に混入する」ことにある）も
    確実には弾けない——**同型穴の列挙原則**により、生成器の float 入力
    （`t_direct`/`t_stem`/`startup_cost`/`session_budget`）全数に `math.isfinite`
    を統一的に要求する（単一のこの関数へ集約）。

    E-91（PR #242 第13巡 Codex 是正）: `_assign_m2e_shard_ids`（生成・readback の
    共有経路）からも呼ばれる——符号制約は生成器の入口検査だけでなく、改変された
    地図を読み戻す経路にも同じ強さで適用する。
    """
    ok = math.isfinite(value) and (value >= 0 if allow_zero else value > 0)
    if not ok:
        requirement = "0 以上" if allow_zero else "正数"
        raise ValueError(
            f"shard map: {name} {value!r} は有限の{requirement}のみ許可する "
            "(fail-closed・E-68/E-91)"
        )


def _require_m2e_shard_map_integer_field(name: str, value: "Any") -> int:
    """地図の整数フィールド（`n_shards` 等）が非 bool の整数であることを要求し、

    その値をそのまま返す（E-83/E-97・PR #242 第10/15巡 Codex 是正の共通形。
    E-83 の `workers` 検証と同型——単一ヘルパへ集約）。`int(x)` は `1.5`
    （切り捨て）や `True`（bool は int のサブクラス）を黙って受理してしまう——
    地図のスケジューリング・カウント系フィールドは形が崩れた値を静かに丸めず
    fail-closed で拒否する。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"shard map: {name} {value!r} は整数（bool 不可）のみ許可する "
            "(fail-closed・E-83/E-97)"
        )
    return value


def _require_m2e_shard_map_numeric_field(name: str, value: "Any") -> float:
    """地図のスケジューリング入力（`t_direct_s` 等）が非 bool の数値スカラー

    （int/float）であることを `float()` 強制の**前**に要求し、`float(value)` を
    返す（E-101・PR #242 第17巡 Codex 是正。`_require_m2e_shard_map_integer_field`
    と同型——単一ヘルパへ集約）。`float("7200")`（文字列）や `float(True)`（bool）
    は黙って強制変換に成功してしまう——地図の生値が既に破損している兆候
    （文字列化・bool 化）を、後段の isfinite/符号検査（E-68/E-91）より前に
    fail-closed で拒否する。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"shard map: {name} {value!r} は数値（bool・文字列は不可）のみ許可する "
            "(fail-closed・E-101)"
        )
    return float(value)


def _require_m2e_cell_repeat_index(value: "Any") -> int:
    """セルレコードの `repeat_index` が非 bool の整数であることを要求し、

    その値をそのまま返す（E-108・PR #242 第20巡 Codex 是正）。Python では
    `False == 0` / `hash(False) == hash(0)` のため、`repeat_index: false` の
    セルは鍵タプル（`_require_m2e_shard_map_integer_field` 等と同型の穴）で
    `repeat_index: 0` のセルと**黙って衝突**し、台帳比較（set/dict 演算）を
    素通りしうる——鍵構築・registry 比較の**前**に fail-closed で拒否する。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"shard map: repeat_index {value!r} は整数（bool 不可）のみ許可する "
            "(fail-closed・E-108)"
        )
    return value


def _m2e_completed_cell_keys(
    cells: "List[Tuple[str, str, str, str, int]]",
    campaign: "Dict[str, Dict[str, Path]]",
    cell_store: "str | Path",
    *,
    fixtures_by_level: "Dict[str, Dict[str, Any]]",
    bars_path: Path = BARS_PATH,
    bars_snapshot: "Optional[Tuple[Any, str]]" = None,
    manifest_snapshot_by_level: "Optional[Dict[str, Tuple[List[Dict[str, Any]], Path]]]" = None,
    manifest_sha256_snapshot_by_level: "Optional[Dict[str, str]]" = None,
) -> "Tuple[set, Dict[str, str], Dict[str, Tuple[List[Dict[str, Any]], Path]]]":
    """`cells`（registry の 5-tuple 群）のうち `cell_store` に digest 一致で完了

    済みの鍵集合を返す（E-66・PR #242 第5巡 Codex 是正）。

    判定基準は `_require_prior_m2e_shards_complete` と同一
    （`_cell_store_record_path` / `_cell_record_mismatches` の resume 判定）——
    「地図生成器が除外してよい」と「シャード実行機が完了とみなす」を同じ基準に
    保つ（別基準を作ると両者が食い違い、除外したのに実行機が未完と扱う/その逆が
    起こりうる）。`fixtures_by_level` は呼び出し元が registry 構築時に既に読取・
    hash 検証済みのスナップショットで、ここでは再オープンしない（E-57 と同族）。
    manifest に無い・レコードが無い・壊れている・digest 不一致のいずれも「完了と
    立証できない」として鍵集合から単に除く（fail-closed——除外の可否は保守的に
    倒す。パッキング対象に残るだけで、測定自体は妨げない）。

    `bars_snapshot`（E-78・PR #242 第8巡 Codex 是正）: 呼び出し元が既に読んだ
    `(bars, bars_sha256)` を渡せば、ここでは bars ファイルを再度開かない。未指定
    （既定 `None`）なら従来どおり `bars_path` から読む。

    `manifest_snapshot_by_level`/`manifest_sha256_snapshot_by_level`（E-125・
    PR #242 第28巡 Codex 是正）: 呼び出し元（`generate_m2e_shard_map` 経由の
    CLI preflight・E-123 の manifest 参照パス保護）が既に読んだ manifest の
    パース済みスナップショット + sha256 を渡せば、内部の `manifest_cache` を
    これで種付けし、対応する水準の manifest をここでは再度開かない（E-57/E-72
    と同族の TOCTOU 回避）。未指定（既定 `None`）なら従来どおり内部で読む
    （直接呼ぶテスト等の後方互換経路）。両者は対で渡すこと（片方だけ渡すと、
    種付けされた水準の `manifest_sha256_by_level` が欠けたまま返る）。
    E-133（PR #242 第32巡 Codex 是正）: 種付けスナップショットは preflight が
    読んだ全水準ぶんを持ちうるが、戻り値の `manifest_sha256_by_level`/
    `manifest_by_level` は種付けの有無に関わらず常に `cells` に実際に登場した
    水準だけへ絞り込む（種付けなし経路と挙動を一致させる——さもないと E-95 の
    記録側（生成時に除外セルが実際に属する水準だけへ絞った
    `manifest_sha256_by_level`）との照合が、種付けの有無だけで結果の変わる
    誤検出を起こす）。

    戻り値は `(completed, manifest_sha256_by_level, manifest_by_level)`。
    `manifest_sha256_by_level`（E-95・PR #242 第15巡 Codex 是正）は本関数が実際に
    読んだ（`cells` に登場した水準の）manifest の sha256——呼び出し元
    （`generate_m2e_shard_map`）はこれを地図へ記録し、実行側
    （`_require_m2e_shard_map_matches_registry`）は除外真実性の再スキャンで読んだ
    manifest がこれと一致することを要求する（生成時の除外判定と実行時の真実性
    検証が、別世代の manifest を黙って跨がないようにする）。
    `manifest_by_level`（E-104・PR #242 第19巡 Codex 是正）は読んだ manifest の
    パース済みスナップショット `{level: (entries, manifest_dir)}`——実行側の
    除外真実性再スキャンが読んだこのスナップショットを `execute_m2e_shard` の
    先行 shard 検査・task 構築まで引き回せば、同じ manifest ファイルを再度
    開かない（E-72/E-57 と同族の TOCTOU 完備化）。
    """
    if bars_snapshot is not None:
        bars, bars_sha256 = bars_snapshot
    else:
        bars, bars_sha256 = load_bars(bars_path)
    bar_block = bars.verify(bars_sha256)["m2_accuracy_bars"]
    tolerance_cents = float(bar_block.get("tolerance_cents", DEFAULT_TOLERANCE_CENTS))
    est_voiced_floor = float(bar_block["est_voiced_confidence_floor"])
    env_digest = _env_digest()
    cell_store = Path(cell_store)

    # E-125: 呼び出し元が既に読んだ manifest スナップショットで種付けする——
    # 種付けされた水準は下のループで再オープンされない（`level not in
    # manifest_cache` が False になる）。
    manifest_cache: "Dict[str, Tuple[List[Dict[str, Any]], Path]]" = (
        dict(manifest_snapshot_by_level) if manifest_snapshot_by_level else {}
    )
    manifest_sha256_by_level: "Dict[str, str]" = (
        dict(manifest_sha256_snapshot_by_level) if manifest_sha256_snapshot_by_level else {}
    )
    completed: "set" = set()
    # E-133（PR #242 第32巡 Codex 是正）: 種付け（`manifest_snapshot_by_level`/
    # `manifest_sha256_snapshot_by_level`）は preflight が読んだ全水準ぶんを持ちうる
    # ため、種付けなし（内部で読む）経路と挙動を揃えるには、`cells` に実際に登場
    # した水準だけへ戻り値を絞り込む必要がある——絞り込まないと、呼び出し元
    # （`_require_m2e_shard_map_matches_registry`）の E-95 照合（生成時に記録した
    # 「除外セルが実際に属する水準」限定の `manifest_sha256_by_level` との一致
    # 検査）が、種付けの有無だけで結果の変わる誤検出を起こす。
    touched_levels: "set" = {level for _bed_id, level, _clip_id, _arm, _repeat_index in cells}
    for bed_id, level, clip_id, arm, repeat_index in cells:
        if level not in manifest_cache:
            entries, manifest_sha256, manifest_path = _load_external_manifest(
                campaign[level]["external_manifest"]
            )
            manifest_cache[level] = (entries, manifest_path.parent)
            manifest_sha256_by_level[level] = manifest_sha256
        entries, manifest_dir = manifest_cache[level]
        entry_id = _m2e_entry_id(clip_id, bed_id, level)
        entry = next((e for e in entries if e["id"] == entry_id), None)
        if entry is None:
            continue
        record_path = _cell_store_record_path(
            cell_store,
            category=arm,
            level=level,
            entry_id=entry_id,
            repeat_index=repeat_index,
        )
        if not record_path.is_file():
            continue
        try:
            inputs = _read_external_clip_inputs(
                entry_id, entry, manifest_dir=manifest_dir, fixtures=fixtures_by_level[level]
            )
            stored = _json_loads_no_dup_keys(
                record_path.read_bytes(), what=f"cell record {record_path}"
            )
        except (ValueError, OSError):
            continue
        mismatches = _cell_record_mismatches(
            stored,
            category=arm,
            level=level,
            entry_id=entry_id,
            repeat_index=repeat_index,
            audio_sha256=inputs.audio_sha256,
            annotation_sha256=inputs.annotation_sha256,
            env_digest=env_digest,
            tolerance_cents=tolerance_cents,
            est_voiced_floor=est_voiced_floor,
            store_role=_CELL_STORE_ROLE_RUN,
        )
        if not mismatches:
            completed.add((bed_id, level, clip_id, arm, repeat_index))
    # E-133: 種付けされた水準のうち `cells` に登場しないものは戻り値から落とす
    # （種付けなし経路は元々ここに現れない——`manifest_cache`/`manifest_sha256_
    # by_level` は `cells` を舐めるループでしか埋まらないため）。
    manifest_sha256_by_level = {
        level: sha256 for level, sha256 in manifest_sha256_by_level.items() if level in touched_levels
    }
    manifest_cache = {
        level: snapshot for level, snapshot in manifest_cache.items() if level in touched_levels
    }
    return completed, manifest_sha256_by_level, manifest_cache


def _require_m2e_excluded_cell_store_relative_confined_to_root(value: str) -> Path:
    """地図の `excluded_completed_cells.cell_store_relative` を ROOT 配下へ封じ込める

    （E-66・PR #242 第5巡 Codex 是正）。E-60 と同じ二段検証（字句——絶対パス・`..`
    拒否／解決後——ROOT 配下要求）を独立実装として適用する——campaign パス検証
    （`_require_m2e_campaign_path_confined_to_root`）とは別関数のまま保ち、既に
    確定した E-60 の挙動・回帰テストへ影響しない。
    """
    candidate = Path(value)
    if candidate.is_absolute():
        raise ValueError(
            "shard map: excluded_completed_cells.cell_store_relative が絶対パス "
            f"{value!r} を指す; repo root からの相対パスのみ許可する (fail-closed)"
        )
    if ".." in candidate.parts:
        raise ValueError(
            f"shard map: excluded_completed_cells.cell_store_relative の {value!r} が "
            "`..` 成分を含む; repo root 配下からの遡上を許可しない (fail-closed)"
        )
    resolved = (ROOT / candidate).resolve()
    if not resolved.is_relative_to(ROOT):
        raise ValueError(
            f"shard map: excluded_completed_cells.cell_store_relative の {value!r} が "
            "解決後に repo root の外を指す（symlink 経由の脱出を含む）; repo root 配下のみ "
            "許可する (fail-closed)"
        )
    return resolved


def generate_m2e_shard_map(
    *,
    campaign_path: "str | Path",
    t_direct: float,
    t_stem: float,
    startup_cost: float,
    workers: int,
    session_budget: float = _M2E_DEFAULT_SESSION_BUDGET_S,
    bars_path: Path = BARS_PATH,
    cell_store: "Optional[str | Path]" = None,
    campaign_snapshot: "Optional[Tuple[Dict[str, Dict[str, Path]], str]]" = None,
    manifest_snapshot_by_level: "Optional[Dict[str, Tuple[List[Dict[str, Any]], Path]]]" = None,
    manifest_sha256_snapshot_by_level: "Optional[Dict[str, str]]" = None,
) -> "Dict[str, Any]":
    """§8.5 のシャード地図を生成する（生成器 `--make-shard-map` の実体）。

    自由変数なし: 入力（`t_direct`/`t_stem`/`startup_cost`/`workers`/`session_budget`/
    campaign/`bars_path` の中身）が同じなら出力は完全にバイト一致する。

    `generated_utc` を持たない（E-67・PR #242 第5巡 Codex P2 是正・Memo AC 不整合の
    是正）: Design Memo は「地図に生成時刻を記録する」と「同一入力 → バイト一致」を
    両方要求していたが、壁時計から読む生成時刻を bytes に含めながらバイト一致を
    謳うのは自己矛盾する（起草バグ）。生成時刻は地図の**内容**ではなく生成
    **イベント**の provenance なので、地図 bytes からは外し、CLI が生成完了時に
    `generated at <ISO8601 UTC> / shard map sha256: <hex>` を stdout へ印字する形へ
    改める（HANDOFF のレシピは stdout を tee するため dated record に残る。日付の
    永続的な担保は commit 自体が持つ）。

    `bars_path`（E-47・PR #242 Codex P2 是正）: CLI `--bars` の指定を registry 構築
    （`repeats_min` の供給元）まで貫通させる。既定はモジュール既定の `BARS_PATH`。
    実効 bars の sha256 を地図へ記録する（`_require_m2e_shard_map_matches_registry`
    が消費時に照合する）。

    `workers`（E-59・PR #242 第3巡 Codex P2 是正）: r2-0 で `T_direct`/`T_stem` を
    校正したときの並列度 `P`。§8.4「production と同じ `P` で回したときの単位コスト」
    という契約を地図へ束縛する——校正時の `P` を記録しないと、実行時に別の `P` を
    渡しても検出できず、admission 判定の前提（コストは校正時の `P` で測ったもの）が
    黙って崩れる。実行機（`--shard-id`）は `--workers` 省略時にこの値を採用し、
    明示指定時は一致を要求する（fail-closed）。

    `cell_store`（E-66・PR #242 第5巡 Codex 是正）: 指定すると、registry の各セルを
    `_require_prior_m2e_shards_complete` と同じ digest 一致基準で判定し、既に
    完了しているセルをパッキングから除外する（§8.5「未完セルについてのみ再適用」の
    実装）。N_shards / R_max / cap は**残セルのみ**で評価する——完了済みセルの
    レコードは影響を受けない。除外したセルの鍵（5-tuple のみ・shard_id は持たない）
    と除外根拠（`cell_store` の repo-relative パス）を地図の
    `excluded_completed_cells` へ記録する。未指定（既定 `None`）なら従来どおり
    全セルをパッキングする（後方互換・`excluded_completed_cells.cells` は空になる）。

    `campaign_snapshot`（E-70・PR #242 第6巡 Codex 是正）: CLI の preflight（保護
    パス集合の構築）が既に読んだ `(campaign, campaign_sha256)` スナップショットを
    渡せば、ここでは campaign ファイルを再度開かない（E-52 と同族の TOCTOU 是正
    ——preflight と生成の間にファイルが差し替わっても、生成は検査時点のスナップ
    ショットのまま進む）。未指定（既定 `None`）なら `campaign_path` から読む
    （直接呼ぶテスト等の従来経路）。

    `manifest_snapshot_by_level`/`manifest_sha256_snapshot_by_level`（E-125・
    PR #242 第28巡 Codex 是正）: CLI の preflight（`--cell-store` 指定時の
    manifest 参照パス保護・E-123）が既に読んだ manifest のパース済みスナップ
    ショット（`{level: (entries, manifest_dir)}`）と sha256（`{level: sha256}`）
    を渡せば、`--cell-store` 指定時の除外真実性スキャン（`_m2e_completed_cell_
    keys`）はここで manifest ファイルを再度開かない（E-70 の manifest 版・
    TOCTOU 回避）。未指定（既定 `None`）なら従来どおりスキャン内部で読む
    （直接呼ぶテスト等の後方互換経路。`cell_store is None` なら未使用）。
    """
    # E-68（PR #242 第5巡 Codex P2 是正）: 生成器の float 入力全数（t_direct/t_stem/
    # startup_cost/session_budget）へ isfinite + 符号検査を統一的に敷く（単一の
    # バリデータへ集約。E-62 で session_budget に敷いた isfinite を他の 3 入力へも
    # 及ぼす——同型穴の列挙原則）。
    _require_m2e_shard_map_finite_input("t_direct", t_direct)
    _require_m2e_shard_map_finite_input("t_stem", t_stem)
    _require_m2e_shard_map_finite_input("startup_cost", startup_cost, allow_zero=True)
    _require_m2e_shard_map_finite_input("session_budget", session_budget)
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError(
            f"generate_m2e_shard_map: workers {workers!r} は 1 以上の整数のみ許可する"
        )

    campaign_path = Path(campaign_path).resolve()
    if campaign_snapshot is not None:
        # E-70: CLI preflight が既に読んだスナップショットを再利用する（再オープンしない）。
        campaign, campaign_sha256 = campaign_snapshot
    else:
        # E-52: campaign_sha256 と parse を単一読取から導出する。
        campaign, campaign_sha256 = _load_m2e_campaign_with_sha256(campaign_path)

    # E-89（PR #242 第12巡 Codex 是正）: bars を本関数の入口で一度だけ読み、registry
    # 構築・除外スキャン（--cell-store 指定時）の双方へ同一スナップショットを渡す
    # （E-78 の生成側対応・TOCTOU 族の完備化——実行側では既に同じ形へ揃えている）。
    bars, bars_sha256 = load_bars(bars_path)
    cells, fixtures_sha256_by_level, repeats_min, bars_sha256, fixtures_by_level = (
        _m2e_full_cell_registry(campaign, bars_path=bars_path, bars_snapshot=(bars, bars_sha256))
    )

    # E-66: --cell-store 指定時は digest 一致で完了済みのセルをパッキングから除外する。
    excluded_keys: "set" = set()
    excluded_cell_store_relative: "Optional[str]" = None
    excluded_scan_manifest_sha256_by_level: "Dict[str, str]" = {}
    if cell_store is not None:
        cell_store_resolved = Path(cell_store).resolve()
        excluded_keys, excluded_scan_manifest_sha256_by_level, _excluded_scan_manifest_cache = (
            _m2e_completed_cell_keys(
                cells,
                campaign,
                cell_store_resolved,
                fixtures_by_level=fixtures_by_level,
                bars_path=bars_path,
                bars_snapshot=(bars, bars_sha256),
                # E-125: preflight（E-123 の manifest 参照パス保護）が既に読んだ
                # スナップショットを引き回す（未指定時は None・スキャン内部が
                # 従来どおり読む）。
                manifest_snapshot_by_level=manifest_snapshot_by_level,
                manifest_sha256_snapshot_by_level=manifest_sha256_snapshot_by_level,
            )
        )
        # E-95: この生成時スキャンは登録簿全体（4 水準）を走査するため
        # `excluded_scan_manifest_sha256_by_level` も 4 水準分になるが、実行側の
        # 真実性再検証（readback）は除外セル**のみ**を対象に再スキャンする
        # ——実際に除外された水準の集合しか持たない。両者を同じ形で比較できる
        # よう、記録側も除外セルが実際に属する水準へ絞り込む。
        excluded_levels = {key[1] for key in excluded_keys}
        excluded_scan_manifest_sha256_by_level = {
            level: sha256
            for level, sha256 in excluded_scan_manifest_sha256_by_level.items()
            if level in excluded_levels
        }
        excluded_cell_store_relative = _repo_relative_path(cell_store_resolved)
        if excluded_keys and excluded_cell_store_relative is None:
            raise ValueError(
                f"generate_m2e_shard_map: --cell-store {cell_store_resolved} が repo root "
                "の外にあるため、除外根拠（cell_store_relative）を地図へ記録できない; "
                "実行側の除外検証（fail-closed）が常に落ちる地図を作らない (E-66)"
            )
        cells = [c for c in cells if c not in excluded_keys]
        # E-115（PR #242 第22巡 Codex 是正）: --cell-store により残セルが 0 件
        # （台帳の全セルが既に digest 一致で完了済み）になる場合、n_shards=1・
        # n_cells=0 の空地図を黙って生成しない——その時点で r6 は完了しており、
        # 意味のない地図という成果物を積み重ねる代わりに fail-closed で明示する
        # （HANDOFF §5 のレシピ注記参照。「地図は科学ではなくスケジューリング」
        # という §8.5 の前提は「スケジュールする対象が 1 件以上ある」ことを暗黙に
        # 含む）。
        if not cells:
            raise ValueError(
                f"generate_m2e_shard_map: --cell-store {cell_store_resolved} により残セルが "
                "0 件（台帳の全セルが既に完了済み）; 空のシャード地図は生成しない——全セル完了・"
                "地図不要（r6 は次フェーズへ進む） (fail-closed・E-115)"
            )

    shard_ids, cap, n_shards = _assign_m2e_shard_ids(
        cells,
        t_direct=t_direct,
        t_stem=t_stem,
        startup_cost=startup_cost,
        session_budget=session_budget,
    )
    if n_shards > _M2E_R_MAX:
        raise ValueError(
            f"generate_m2e_shard_map: N_shards={n_shards} が R_max={_M2E_R_MAX} を超える "
            f"(S={startup_cost!r}, T_direct={t_direct!r}, T_stem={t_stem!r}, "
            f"B_session={session_budget!r}, cap={cap!r}); 本測定は開始しない。次の3択を "
            "User 決裁へ提示する (§8.8・自動縮退はしない): "
            "(1) R_max を引き上げる（規模は不変・回数だけ増える）, "
            "(2) 実行環境を変更して P を上げる（r2-0 からやり直す）, "
            "(3) clip 数を削る（休止中の縮退手順・附録B。User の明示的決裁のみ）"
        )

    cell_records = [
        {
            "bed_id": bed_id,
            "level": level,
            "clip_id": clip_id,
            "arm": arm,
            "repeat_index": repeat_index,
            "entry_id": _m2e_entry_id(clip_id, bed_id, level),
            "shard_id": shard_id,
        }
        for (bed_id, level, clip_id, arm, repeat_index), shard_id in zip(cells, shard_ids)
    ]
    return {
        "schema_version": _M2E_SHARD_MAP_SCHEMA,
        "inputs": {
            "startup_cost_s": startup_cost,
            "t_direct_s": t_direct,
            "t_stem_s": t_stem,
            "workers": workers,
            "session_budget_s": session_budget,
            "cap_s": cap,
            "margin": _M2E_SHARD_CAP_MARGIN,
        },
        "campaign_path_relative": _repo_relative_path(campaign_path),
        "campaign_sha256": campaign_sha256,
        "bars_path_relative": _repo_relative_path(Path(bars_path).resolve()),
        "bars_sha256": bars_sha256,
        "fixtures_sha256_by_level": fixtures_sha256_by_level,
        "repeats_min": repeats_min,
        "n_cells": len(cell_records),
        "n_shards": n_shards,
        "cells": cell_records,
        # E-66: 除外した完了済みセル（鍵のみ・shard_id は持たない）+ 除外根拠。
        # cell_store 未指定時は空（後方互換・従来形と同じ挙動）。
        "excluded_completed_cells": {
            "cell_store_relative": excluded_cell_store_relative,
            "cells": [
                {
                    "bed_id": bed_id,
                    "level": level,
                    "clip_id": clip_id,
                    "arm": arm,
                    "repeat_index": repeat_index,
                }
                for bed_id, level, clip_id, arm, repeat_index in sorted(excluded_keys)
            ],
            # E-95（PR #242 第15巡 Codex 是正）: 除外判定の走査で読んだ per-level
            # manifest の sha256——実行側の除外真実性再検証（readback）が同じ
            # manifest を消費していることを照合する（別世代の manifest を挟んで
            # 除外決定と真実性検証が食い違う経路を塞ぐ）。
            "manifest_sha256_by_level": excluded_scan_manifest_sha256_by_level,
        },
    }


def _load_m2e_shard_map(path: "str | Path") -> "Tuple[Dict[str, Any], str]":
    """シャード地図 YAML を single read で `(parsed dict, sha256)` として返す。"""
    map_path = Path(path).resolve()
    data = map_path.read_bytes()
    doc = _yaml_load_no_dup_keys(data, what=map_path.name)
    if not isinstance(doc, dict) or doc.get("schema_version") != _M2E_SHARD_MAP_SCHEMA:
        raise ValueError(
            f"{map_path}: schema_version が {_M2E_SHARD_MAP_SCHEMA!r} でない (fail-closed)"
        )
    for required in (
        "inputs",
        "campaign_sha256",
        "bars_sha256",
        "fixtures_sha256_by_level",
        "repeats_min",
        "n_shards",
        "n_cells",
        "cells",
    ):
        if required not in doc:
            raise ValueError(f"{map_path}: {required!r} を欠く (fail-closed)")
    return doc, hashlib.sha256(data).hexdigest()


def _require_m2e_shard_map_matches_registry(
    map_doc: "Dict[str, Any]",
    campaign: "Dict[str, Dict[str, Path]]",
    *,
    bars_path: Path = BARS_PATH,
    cell_store: "Optional[str | Path]" = None,
    manifest_snapshot_by_level: "Optional[Dict[str, Tuple[List[Dict[str, Any]], Path]]]" = None,
    manifest_sha256_snapshot_by_level: "Optional[Dict[str, str]]" = None,
) -> "Tuple[Dict[str, Dict[str, Any]], Tuple[Any, str], Dict[str, Tuple[List[Dict[str, Any]], Path]]]":
    """地図の `cells` が現在の campaign から再計算した台帳・割当と一致することを要求する。

    欠け・重複・余剰のいずれも fail-closed（§8.5「セル台帳は不可侵」の実行時側の
    立証）。台帳が動いた（fixtures が改訂された・`repeats_min` が変わった等）状態の
    まま古い地図を消費すると、測っていないセルを完了扱いにしたり、存在しないセルへ
    `shard_id` を割り当てたりする。

    戻り値は `(fixtures_by_level, bars_snapshot, excluded_manifest_by_level)`。
    `fixtures_by_level`（E-57・PR #242 第3巡 Codex P2 是正）と
    `bars_snapshot == (bars, bars_sha256)`（E-78・PR #242 第8巡 Codex 是正）は
    いずれも検証済みスナップショット——呼び出し元（`execute_m2e_shard`）はこれらを
    実行段でも引き回し、同じ fixtures / bars ファイルを再度開かない（TOCTOU 回避。
    E-52 と同族）。`excluded_manifest_by_level`（E-104・PR #242 第19巡 Codex 是正）
    は除外真実性の再スキャンで読んだ manifest のパース済みスナップショット
    （除外なしなら空）——呼び出し元はこれを先行 shard 検査・task 構築へ引き回し、
    同じ水準の manifest を再度開かない（E-72 と同族）。

    `cell_store`（E-79・PR #242 第8巡 Codex 是正）: `execute_m2e_shard` が実際に
    書き込む store を渡すと、`excluded_completed_cells` の除外真実性検証を
    「地図が記録した store」ではなく「その実行 store」へ束縛する（地図の
    `cell_store_relative` と実行時 `--cell-store` の一致を要求した上で、真実性
    check 自体もこの実行 store に対して行う）。未指定（既定 `None`）なら従来どおり
    地図の記録した store のみで検証する（直接呼ぶテスト等の後方互換経路）。

    `manifest_snapshot_by_level`/`manifest_sha256_snapshot_by_level`（E-133・
    PR #242 第32巡 Codex 是正）: CLI の preflight（`--out` 保護入力検査・E-123）が
    既に読んだ manifest のパース済みスナップショットと sha256 を渡せば、除外
    真実性の再スキャン（`_m2e_completed_cell_keys` 経由・excluded_keys が非空の
    ときのみ発火）はここで manifest ファイルを再度開かない——除外検証・digest
    計算・戻り値（`excluded_manifest_by_level`）のすべてがこの単一スナップ
    ショット由来になる。E-125（`generate_m2e_shard_map` 側）・E-126
    （`execute_m2e_shard` の task 構築側）と同族の TOCTOU 完備化で、これにより
    `--shard-id` 経路の manifest 読取は preflight の 1 回へ完全に一本化される
    （E-126 が導入した「rescan 優先・preflight 次点」という優先順位は、rescan
    自体が同じ preflight 実体を消費するため実質的な縮退になる）。未指定
    （既定 `None`）なら従来どおり内部で読む（直接呼ぶテスト等の後方互換経路）。

    E-49（PR #242 第1巡 Codex P1 是正）: セル鍵の集合一致だけでは、鍵は保ったまま
    `shard_id` の値や `n_shards` だけを書き換えた地図（例: 全セルを shard 0 に
    寄せて `n_shards: 1` を名乗る）を素通ししてしまう——凍結パッキングアルゴリズムと
    `R_max` 契約を丸ごとバイパスできることになる。**地図は科学ではなく
    スケジューリングだが、改変検出は台帳と同格で要求する**: 地図が記録した入力
    （S/T_direct/T_stem/B_session）から `_assign_m2e_shard_ids` で割当を再計算し、
    全セルの `shard_id` と `n_shards` の完全一致を要求する。

    E-53（PR #242 第2巡 Codex P2 是正）: E-49 の再計算は鍵引き dict 比較のため、
    `map_doc["cells"]` **内の並び順**は見ていなかった——shard_id を保ったまま
    同一 shard 内でレコードを並べ替えた地図も通ってしまい、`_m2e_shard_cells_for` /
    動的キューがその改変順序のまま配布・実行する（§8.5 の凍結 lexical 配布順に
    違反する）。よって鍵集合・shard_id の一致に加え、`map_doc["cells"]` の**並び順を
    含めた完全一致**を、再生成した正準順序（`registry_cells` そのもの——§8.5 order で
    既にソート済み）と比較して要求する。

    E-58（PR #242 第3巡 Codex P2 是正）: 5-tuple（bed_id/level/clip_id/arm/
    repeat_index）が無傷でも `entry_id` だけを別セルのものへ書き換えられると、
    キューは改変された `entry_id` の manifest entry / チェックポイントを消費して
    しまう（本来の clip を測らないまま「完了」を報告しうる）。全セルについて
    `entry_id == _m2e_entry_id(clip_id, bed_id, level)`（§6.2 の正準写像）を要求する。

    `bars_path`（E-47・PR #242 第1巡 Codex P2 是正）: `--bars` の指定を検証まで
    貫通させ、地図が刻んだ `bars_sha256` と実効 bars の実体を照合する。
    """
    # E-78: bars を本関数の入口で一度だけ読み、以降（registry 構築・除外真実性検証）は
    # このスナップショットを引き回す（内部で複数回 load_bars を呼ばない）。
    bars, bars_sha256 = load_bars(bars_path)
    registry_cells, fixtures_sha256_by_level, repeats_min, _bars_sha256_unused, fixtures_by_level = (
        _m2e_full_cell_registry(campaign, bars_path=bars_path, bars_snapshot=(bars, bars_sha256))
    )
    if map_doc.get("bars_sha256") != bars_sha256:
        raise ValueError(
            f"shard map: bars_sha256 {map_doc.get('bars_sha256')!r} が --bars {bars_path} "
            f"の実体 {bars_sha256!r} と不一致; 別世代の bars（repeats_min 等の共有スカラー "
            "供給元）で組まれた地図を消費しない (fail-closed)"
        )
    if map_doc.get("fixtures_sha256_by_level") != fixtures_sha256_by_level:
        raise ValueError(
            "shard map: fixtures_sha256_by_level が現在の campaign の実体と不一致; "
            "地図が生成された後に fixtures が変わった (fail-closed)"
        )
    # E-102（PR #242 第18巡 Codex 是正）: `int(...)` は非 bool の整数以外
    # （`2.5`・`"2"`）を黙って受理してしまう——E-83/E-97 と同型の穴。
    if _require_m2e_shard_map_integer_field(
        "repeats_min", map_doc.get("repeats_min")
    ) != repeats_min:
        raise ValueError(
            f"shard map: repeats_min {map_doc.get('repeats_min')!r} が現在の凍結値 "
            f"{repeats_min!r} と不一致 (fail-closed)"
        )
    # E-58: entry_id の正準束縛（5-tuple が無傷でも entry_id だけの改変を検出する）。
    bad_entry_ids = [
        record.get("entry_id")
        for record in map_doc["cells"]
        if record.get("entry_id")
        != _m2e_entry_id(record["clip_id"], record["bed_id"], record["level"])
    ]
    if bad_entry_ids:
        raise ValueError(
            f"shard map: {len(bad_entry_ids)} 件のセルの entry_id が §6.2 の正準写像 "
            f"（vremix_{{clip_id}}_{{bed_id}}_{{level_tag}}）と一致しない"
            f"（例: {bad_entry_ids[:3]}）; entry_id だけを改変した地図で別 clip を測る "
            "経路を許さない (fail-closed)"
        )
    # E-66（PR #242 第5巡 Codex 是正）: 地図が「未完セルについてのみ」組まれている
    # 場合、除外された完了済みセルの鍵集合を読み取る。以降の台帳比較はこの除外集合を
    # 引いた「残セルの台帳」を基準にする。除外の真実性（実際に store で digest 一致
    # 完了しているか）も検証する——地図が虚偽の除外を宣言して未測定セルの存在を
    # 隠す経路を許さない。`excluded_completed_cells` を持たない旧世代の地図は空
    # 除外として扱う（後方互換・従来どおり全台帳を要求する）。
    excluded_doc = map_doc.get("excluded_completed_cells") or {}
    excluded_records = excluded_doc.get("cells") or []
    excluded_keys: "set" = set()
    excluded_duplicates: "List[Tuple[str, str, str, str, int]]" = []
    for record in excluded_records:
        excluded_key = (
            record["bed_id"],
            record["level"],
            record["clip_id"],
            record["arm"],
            _require_m2e_cell_repeat_index(record["repeat_index"]),
        )
        # E-99（PR #242 第17巡 Codex 是正）: set への追加は重複を黙って畳む——地図が
        # 同一セルを 2 回以上「除外済み」と宣言していても、その事実自体は検出でき
        # ない（除外の正当性は個々に立証されるため実害は薄いが、地図が壊れている
        # 兆候を握り潰さない。台帳は不可侵の原則に倣い fail-closed で顕在化する）。
        if excluded_key in excluded_keys:
            excluded_duplicates.append(excluded_key)
        excluded_keys.add(excluded_key)
    if excluded_duplicates:
        raise ValueError(
            "shard map: excluded_completed_cells.cells に "
            f"{len(excluded_duplicates)} 件の重複がある（例: "
            f"{excluded_duplicates[:3]}）; 除外一覧は各セル高々 1 回のみ許可する "
            "(fail-closed・E-99)"
        )
    registry_full_set = set(registry_cells)
    excluded_not_in_registry = excluded_keys - registry_full_set
    if excluded_not_in_registry:
        raise ValueError(
            "shard map: excluded_completed_cells が台帳に存在しないセルを "
            f"{len(excluded_not_in_registry)} 件含む（例: "
            f"{sorted(excluded_not_in_registry)[:3]}）; セル台帳は不可侵 (fail-closed)"
        )
    # E-104（PR #242 第19巡 Codex 是正）: 除外真実性の再スキャンで読んだ manifest
    # のパース済みスナップショット——除外なし（従来形）なら空のまま返す。
    excluded_manifest_by_level: "Dict[str, Tuple[List[Dict[str, Any]], Path]]" = {}
    if excluded_keys:
        cell_store_relative = excluded_doc.get("cell_store_relative")
        if not cell_store_relative:
            raise ValueError(
                "shard map: excluded_completed_cells.cells が非空なのに "
                "cell_store_relative を欠く; 除外の根拠を検証できない (fail-closed)"
            )
        excluded_cell_store = _require_m2e_excluded_cell_store_relative_confined_to_root(
            cell_store_relative
        )
        # E-79: 実行が実際に使う cell_store（execute_m2e_shard から渡された引数）が
        # あれば、地図が記録した store と一致することを要求し、真実性検証も**実行
        # store**へ束縛する——地図の cell_store_relative がどこか別の store 向けの
        # 宣言であっても、実行が別の --cell-store を指せば地図の除外を信用しない
        # （地図の記録した store だけを見て検証すると、実行時の実 store とすり替え
        # られていても気付けない）。
        if cell_store is not None:
            actual_cell_store = Path(cell_store).resolve()
            if actual_cell_store != excluded_cell_store:
                raise ValueError(
                    "shard map: excluded_completed_cells.cell_store_relative "
                    f"{cell_store_relative!r}（解決後 {excluded_cell_store}）が実行時の "
                    f"--cell-store {actual_cell_store} と一致しない; 地図が別 store 向けに "
                    "宣言した除外を、別の store への書き込みに束縛して信用しない "
                    "(fail-closed・E-79)"
                )
            verify_cell_store = actual_cell_store
        else:
            verify_cell_store = excluded_cell_store
        # 除外の真実性: 宣言された除外セルが実際に store で digest 一致完了して
        # いることを要求する（判定基準は生成器と同一の _m2e_completed_cell_keys）。
        # E-133: preflight（保護入力検査・E-123）が既に読んだ manifest スナップ
        # ショットを引き回す（未指定時は None・スキャン内部が従来どおり読む）。
        actually_complete, rescan_manifest_sha256_by_level, excluded_manifest_by_level = (
            _m2e_completed_cell_keys(
                sorted(excluded_keys),
                campaign,
                verify_cell_store,
                fixtures_by_level=fixtures_by_level,
                bars_path=bars_path,
                bars_snapshot=(bars, bars_sha256),
                manifest_snapshot_by_level=manifest_snapshot_by_level,
                manifest_sha256_snapshot_by_level=manifest_sha256_snapshot_by_level,
            )
        )
        not_actually_complete = excluded_keys - actually_complete
        if not_actually_complete:
            raise ValueError(
                f"shard map: excluded_completed_cells が {len(not_actually_complete)} 件の "
                f"セルを完了済みと宣言しているが、store {cell_store_relative!r} では digest "
                f"一致で完了していない（例: {sorted(not_actually_complete)[:3]}）; 除外の "
                "真実性を立証できない地図は実行しない (fail-closed)"
            )
        # E-95（PR #242 第15巡 Codex 是正）: 除外真実性の再スキャンで読んだ
        # manifest が、生成時の除外判定が読んだ manifest（地図に記録済み）と
        # 一致することを要求する——個々のセルの digest 一致だけでは、manifest
        # 全体が別世代へ差し替わっていても検出できない（entry の中身が偶然
        # 不変なら気付けない）。
        recorded_manifest_sha256_by_level = excluded_doc.get("manifest_sha256_by_level") or {}
        if recorded_manifest_sha256_by_level != rescan_manifest_sha256_by_level:
            raise ValueError(
                "shard map: excluded_completed_cells.manifest_sha256_by_level "
                f"{recorded_manifest_sha256_by_level!r} が実行時に読んだ manifest "
                f"{rescan_manifest_sha256_by_level!r} と不一致; 除外判定を下した時点の "
                "manifest と実行時に消費する manifest が食い違う地図は実行しない "
                "(fail-closed・E-95)"
            )
    expected_registry_cells = [c for c in registry_cells if c not in excluded_keys]
    registry_set = set(expected_registry_cells)
    map_set: "set" = set()
    duplicates: "List[Tuple[str, str, str, str, int]]" = []
    declared_shard_id: "Dict[Tuple[str, str, str, str, int], Any]" = {}
    for record in map_doc["cells"]:
        key = (
            record["bed_id"],
            record["level"],
            record["clip_id"],
            record["arm"],
            _require_m2e_cell_repeat_index(record["repeat_index"]),
        )
        if key in map_set:
            duplicates.append(key)
        map_set.add(key)
        # E-112（PR #242 第21巡 Codex 是正）: `False != 0` は Python では成り立たない
        # （`False == 0`）——`shard_id: false`/`shard_id: 0.0` のセルは、後段の
        # `!=` 比較だけでは `shard_id: 0` と黙って区別されない。E-108 と同じ
        # 無強制整数検証を格納の前に敷く。
        declared_shard_id[key] = _require_m2e_shard_map_integer_field(
            "cells[].shard_id", record.get("shard_id")
        )
    if duplicates:
        raise ValueError(f"shard map: セル鍵が重複している ({duplicates[:5]}) (fail-closed)")
    missing = registry_set - map_set
    extra = map_set - registry_set
    if missing or extra:
        raise ValueError(
            f"shard map: 台帳と一致しない (missing={len(missing)}, extra={len(extra)}); "
            "セル台帳は不可侵——地図は再生成すること (fail-closed)"
        )

    # E-53: 鍵集合が一致していても並び順が改変されていれば拒否する（正準順序の完全一致）。
    declared_order = [
        (
            record["bed_id"],
            record["level"],
            record["clip_id"],
            record["arm"],
            _require_m2e_cell_repeat_index(record["repeat_index"]),
        )
        for record in map_doc["cells"]
    ]
    if declared_order != expected_registry_cells:
        first_mismatch = next(
            (
                i
                for i, (declared, expected) in enumerate(
                    zip(declared_order, expected_registry_cells)
                )
                if declared != expected
            ),
            min(len(declared_order), len(expected_registry_cells)),
        )
        raise ValueError(
            "shard map: cells の並び順が凍結 lexical order（§8.5 order）と一致しない "
            f"(最初の不一致位置={first_mismatch}); 地図内でレコードを並べ替えると "
            "`_m2e_shard_cells_for` / 動的キューが改変された順序で配布・実行してしまう "
            "(fail-closed)"
        )

    inputs = map_doc["inputs"]
    # E-101（PR #242 第17巡 Codex 是正）: `float(x)` は非数値スカラー（文字列・
    # bool）を強制の前に検査せず黙って成功させてしまう——E-83/E-97 と同型の
    # 無強制型検査を先に敷く。
    expected_shard_ids, expected_cap, expected_n_shards = _assign_m2e_shard_ids(
        expected_registry_cells,
        t_direct=_require_m2e_shard_map_numeric_field("t_direct_s", inputs["t_direct_s"]),
        t_stem=_require_m2e_shard_map_numeric_field("t_stem_s", inputs["t_stem_s"]),
        startup_cost=_require_m2e_shard_map_numeric_field(
            "startup_cost_s", inputs["startup_cost_s"]
        ),
        session_budget=_require_m2e_shard_map_numeric_field(
            "session_budget_s", inputs["session_budget_s"]
        ),
    )
    # E-75（PR #242 第7巡 Codex P2 是正）: cap_s/margin/n_cells は再計算するだけで
    # 捨てていた（cap は `_cap` に破棄、margin/n_cells はそもそも比較していなかった）
    # ——地図が cells/shard_id を無傷に保ったまま、これら派生メタデータだけを改変
    # しても検出できなかった。E-49/E-69 と同枠で、再計算値との完全一致を要求する。
    # E-116（PR #242 第22巡 Codex 是正）: `float(x)`/`int(x)` は非数値スカラー
    # （文字列・bool）や `1.5`（切り捨て）を強制の前に検査せず黙って受理して
    # しまう——E-83/E-97/E-101/E-102 と同型の穴。cap_s/margin/n_cells の 3 フィールド
    # を無強制の型検査ヘルパ経由に統一し、この地図数値フィールド型検証ファミリー
    # を終端する（同型穴の一括掃討）。
    if _require_m2e_shard_map_numeric_field("cap_s", inputs.get("cap_s")) != expected_cap:
        raise ValueError(
            f"shard map: inputs.cap_s {inputs.get('cap_s')!r} が再計算値 {expected_cap!r} "
            "と不一致 (fail-closed・E-75)"
        )
    if (
        _require_m2e_shard_map_numeric_field("margin", inputs.get("margin"))
        != _M2E_SHARD_CAP_MARGIN
    ):
        raise ValueError(
            f"shard map: inputs.margin {inputs.get('margin')!r} が凍結値 "
            f"{_M2E_SHARD_CAP_MARGIN!r} と不一致 (fail-closed・E-75・設計 §8.5)"
        )
    if _require_m2e_shard_map_integer_field(
        "n_cells", map_doc.get("n_cells")
    ) != len(expected_registry_cells):
        raise ValueError(
            f"shard map: n_cells {map_doc.get('n_cells')!r} が再計算値 "
            f"{len(expected_registry_cells)!r} と不一致 (fail-closed・E-75・E-116)"
        )
    # E-69（PR #242 第5巡 Codex P2 是正）: 再計算した割当・n_shards が整合していても
    # `R_max` を超えうる——記録された入力（S/T_direct/T_stem/B_session）を改変すれば、
    # 割当・n_shards を「その改変後の入力からは正しく」再生成できてしまうため、上の
    # 完全一致検査だけでは通ってしまう。しかし `generate_m2e_shard_map` は
    # `n_shards > R_max` を fail-closed で拒否しており、**生成器が出し得ない成果物**
    # のはずである。読み戻し（実行側の検証）でも同じ `R_max` 契約を再適用し、
    # 生成器をバイパスした地図を締め出す。
    if expected_n_shards > _M2E_R_MAX:
        raise ValueError(
            f"shard map: 記録された入力から再計算した n_shards={expected_n_shards} が "
            f"R_max={_M2E_R_MAX} を超える; generate_m2e_shard_map はこの入力の組を "
            "拒否するはずであり、生成器をバイパスした地図を実行しない (fail-closed・E-69)"
        )
    expected_shard_id_by_key = dict(zip(expected_registry_cells, expected_shard_ids))
    mismatched = sorted(
        key for key in registry_set if declared_shard_id.get(key) != expected_shard_id_by_key[key]
    )
    if mismatched:
        raise ValueError(
            f"shard map: {len(mismatched)} 件のセルの shard_id が、地図の記録した入力"
            "（S/T_direct/T_stem/B_session）から凍結アルゴリズムで再計算した割当と "
            f"一致しない（例: {mismatched[:3]}）; 改変された割当で実行しない (fail-closed)"
        )
    # E-97（PR #242 第15巡 Codex 是正）: `int(...)` は非 bool の整数以外（`1.5` は
    # 切り捨てて偶然 expected と一致しうる・`true` は 1 になる）を黙って正常値と
    # して受理してしまう——E-83（workers）と同型の穴。無強制の型検査を先に敷く。
    if _require_m2e_shard_map_integer_field(
        "n_shards", map_doc.get("n_shards")
    ) != expected_n_shards:
        raise ValueError(
            f"shard map: n_shards {map_doc.get('n_shards')!r} が再計算値 "
            f"{expected_n_shards!r} と不一致 (fail-closed)"
        )
    return fixtures_by_level, (bars, bars_sha256), excluded_manifest_by_level


def _m2e_shard_cells_for(map_doc: "Dict[str, Any]", shard_id: int) -> "List[Dict[str, Any]]":
    """地図から `shard_id` に属するセルだけを、地図に記録された順序のまま抜き出す。

    地図の `cells` は §8.5 order で書かれている（`generate_m2e_shard_map` が保証）ので、
    ここでの絞り込みは順序を破らない。各要素に `cost`（秒）を付与する。

    E-116（PR #242 第22巡 Codex 是正）: `float(x)` は非数値スカラー（文字列・bool）
    を強制の前に検査せず黙って成功させてしまう——呼び出し元
    （`_require_m2e_shard_map_matches_registry` 経由の `execute_m2e_shard`）は既に
    `t_direct_s`/`t_stem_s` を検証済みだが、E-101（`session_budget`）と同じく
    execute 側でも同じ無強制ヘルパで読む一貫性を優先する。
    """
    inputs = map_doc["inputs"]
    t_direct = _require_m2e_shard_map_numeric_field("t_direct_s", inputs["t_direct_s"])
    t_stem = _require_m2e_shard_map_numeric_field("t_stem_s", inputs["t_stem_s"])
    return [
        dict(record, cost=_m2e_shard_cell_cost(record["arm"], t_direct=t_direct, t_stem=t_stem))
        for record in map_doc["cells"]
        if record["shard_id"] == shard_id
    ]


def _require_prior_m2e_shards_complete(
    map_doc: "Dict[str, Any]",
    shard_id: int,
    *,
    cell_store: Path,
    campaign: "Dict[str, Dict[str, Path]]",
    env_digest: str,
    tolerance_cents: float,
    est_voiced_floor: float,
    fixtures_by_level: "Optional[Dict[str, Dict[str, Any]]]" = None,
    manifest_by_level: "Optional[Dict[str, Tuple[List[Dict[str, Any]], Path]]]" = None,
) -> "Dict[str, Tuple[List[Dict[str, Any]], Path]]":
    """`shard_id` 未満の全 shard が digest 一致で完了していることを要求する（fail-closed）。

    §8.6「`shard_id` の昇順で実行する。飛ばしてよいのは、その shard の全セルが digest
    一致で完了済みの場合のみ」の実装。既存の resume 判定
    （`_cell_store_record_path` / `_cell_record_mismatches`）をそのまま再利用する——
    別の判定基準を作ると「シャード実行機が完了とみなした」と「1 水準まるごとの run が
    resume する」が食い違いうる（resume 互換 AC の根拠）。

    `fixtures_by_level`（E-57・PR #242 第3巡 Codex P2 是正）: 呼び出し元
    （`execute_m2e_shard`）が `_require_m2e_shard_map_matches_registry` から受け取った
    検証済みスナップショットを渡せば、ここで fixtures ファイルを再度開かない
    （TOCTOU 回避）。未指定（既定 `None` — 直接呼ぶテスト等）ならこれまでどおり
    `campaign` からその場で読む。

    戻り値は本関数が読んだ manifest のスナップショット（E-72・PR #242 第6巡 Codex
    是正）: `{level: (entries, manifest_dir)}`。呼び出し元（`execute_m2e_shard`）の
    task 構築ループは、この shard に必要な水準のうちここで既に読んだものを再利用し、
    同じ manifest ファイルを再度開かない（先行 shard 検証と task 構築が別々の
    read で同一 level の manifest を消費する TOCTOU——E-57 の fixtures 引き回しと
    同じ形）。`prior_cells` が空なら（`shard_id == 0` 等）`manifest_by_level`
    をそのまま（無ければ空の `{}`）返す。

    `manifest_by_level`（E-104・PR #242 第19巡 Codex 是正）: 呼び出し元
    （`execute_m2e_shard`）が `_require_m2e_shard_map_matches_registry` の除外
    真実性再スキャンから受け取った manifest スナップショットを渡せば、その
    水準はここで再オープンしない（fixtures 側の `fixtures_by_level` と同じ
    形の TOCTOU 回避）。未指定（既定 `None`）ならこれまでどおり必要な水準を
    その場で読む。
    """
    prior_cells = [r for r in map_doc["cells"] if r["shard_id"] < shard_id]
    manifest_cache: "Dict[str, Tuple[List[Dict[str, Any]], Path]]" = (
        dict(manifest_by_level) if manifest_by_level else {}
    )
    if not prior_cells:
        return manifest_cache
    fixtures_cache: "Dict[str, Dict[str, Any]]" = {}
    incomplete: "List[str]" = []
    for record in prior_cells:
        level = record["level"]
        if level not in fixtures_cache:
            if fixtures_by_level is not None and level in fixtures_by_level:
                fixtures_cache[level] = fixtures_by_level[level]
            else:
                fixtures_doc, _sha = load_external_fixtures(campaign[level]["external_fixtures"])
                fixtures_cache[level] = fixtures_doc["fixtures"]
        if level not in manifest_cache:
            entries, _manifest_sha256, manifest_path = _load_external_manifest(
                campaign[level]["external_manifest"]
            )
            manifest_cache[level] = (entries, manifest_path.parent)
        fixtures = fixtures_cache[level]
        entries, manifest_dir = manifest_cache[level]
        entry_id = record["entry_id"]
        entry = next((e for e in entries if e["id"] == entry_id), None)
        label = f"{entry_id}/{record['arm']}/repeat{record['repeat_index']}"
        if entry is None:
            incomplete.append(f"{label} (manifest に無い)")
            continue
        record_path = _cell_store_record_path(
            cell_store,
            category=record["arm"],
            level=level,
            entry_id=entry_id,
            repeat_index=record["repeat_index"],
        )
        if not record_path.is_file():
            incomplete.append(f"{label} (未測定)")
            continue
        inputs = _read_external_clip_inputs(
            entry_id, entry, manifest_dir=manifest_dir, fixtures=fixtures
        )
        stored = _json_loads_no_dup_keys(
            record_path.read_bytes(), what=f"cell record {record_path}"
        )
        mismatches = _cell_record_mismatches(
            stored,
            category=record["arm"],
            level=level,
            entry_id=entry_id,
            repeat_index=record["repeat_index"],
            audio_sha256=inputs.audio_sha256,
            annotation_sha256=inputs.annotation_sha256,
            env_digest=env_digest,
            tolerance_cents=tolerance_cents,
            est_voiced_floor=est_voiced_floor,
            store_role=_CELL_STORE_ROLE_RUN,
        )
        if mismatches:
            incomplete.append(f"{label} (digest 不一致)")
    if incomplete:
        raise ValueError(
            f"shard {shard_id}: 先行 shard（shard_id < {shard_id}）に digest 一致で完了して"
            f"いないセルがある（{incomplete[:10]}{' ほか' if len(incomplete) > 10 else ''}）; "
            "昇順実行を要求する。飛ばせるのは全セルが完了済みの shard のみ "
            "(fail-closed・設計 §8.6)"
        )
    return manifest_cache


def _shard_measure_and_record_cell(
    task: "Dict[str, Any]", *, runner: RouteRunner
) -> "Dict[str, Any]":
    """1 セルを測るか resume する（`_measure_or_resume_external_clip_row` の per-cell 呼び出し）。

    テスト seam: `runner` は非 picklable でもよい——この関数を直接呼ぶ経路（fake
    backend 統合テスト・resume 互換テストが使う「P=1 の in-process 経路」）と、
    `_shard_worker_measure_cell` からモジュール既定 runner で呼ばれる multiprocessing
    経路の両方から使われる。`task` 自身は picklable な primitives のみで構成する
    （spawn context を跨ぐため）。

    レコードパス・書き込み機構は `_measure_or_resume_external_clip_row` /
    `_cell_store_record_path` / `atomic_io` をそのまま共有する——これが shard 実行機の
    セルレコードが既存の「1 水準まるごと」run による resume と互換であることの根拠。
    """
    category = task["arm"]
    level = task["level"]
    entry_id = task["entry_id"]
    repeat_index = task["repeat_index"]
    entry = task["entry"]
    fixtures = task["fixtures"]
    manifest_dir = Path(task["manifest_dir"])
    category_spec = _CATEGORY_SPECS[category]
    route = _select_named_route(category_spec["input_kind"], category_spec["route_name"])
    record_est_trajectory = _category_records_est_trajectory(category)

    cells_resumed: "List[str]" = []
    cells_measured: "List[str]" = []
    cell_started_utc: "List[str]" = []
    cell_written_paths: "List[str]" = []
    cell_store_mismatches: "List[Dict[str, Any]]" = []

    with tempfile.TemporaryDirectory(prefix="m2e-shard-cell-") as tmp:
        clip_row = _measure_or_resume_external_clip_row(
            entry_id,
            entry,
            manifest_dir=manifest_dir,
            fixtures=fixtures,
            tolerance_cents=task["tolerance_cents"],
            est_voiced_floor=task["est_voiced_floor"],
            route=route,
            runner=runner,
            tmp_dir=Path(tmp),
            category=category,
            level=level,
            cell_store=Path(task["cell_store"]),
            repeat_index=repeat_index,
            env_digest=task["env_digest"],
            workers=task["workers"],
            cells_resumed=cells_resumed,
            cells_measured=cells_measured,
            cell_started_utc=cell_started_utc,
            cell_written_paths=cell_written_paths,
            cell_store_mismatches=cell_store_mismatches,
            store_role=_CELL_STORE_ROLE_RUN,
            record_est_trajectory=record_est_trajectory,
        )
    return {
        "bed_id": task["bed_id"],
        "level": level,
        "clip_id": task["clip_id"],
        "arm": category,
        "repeat_index": repeat_index,
        "entry_id": entry_id,
        "resumed": entry_id in cells_resumed,
        "measured": entry_id in cells_measured,
        "mismatches": cell_store_mismatches,
        "outcome": clip_row.get("outcome"),
        # E-46（PR #242 Codex P1 是正）: outcome == "unavailable" のときの理由
        # （`_build_external_clip_row` が刻む 1 行説明）。`execute_m2e_shard` が
        # completed から外して未完側へ回すときに使う。
        "detail": clip_row.get("detail"),
        # E-61（PR #242 第4巡 Codex P2 是正）: このセルが**新規に書いた**レコードパス
        # （resume は含まない）。`execute_m2e_shard` が post-execution の runtime pin
        # 再検証に失敗したとき、`_quarantine_cell_records` へ渡す隔離対象を集めるため。
        "written_paths": list(cell_written_paths),
    }


_M2E_CREPE_PRELOAD_CAPACITY = "full"  # crepe_adapter._DEFAULT_CAPACITY と同値（重複定義）。
_M2E_DEMUCS_PRELOAD_MODEL = "htdemucs_ft"  # source_separator.DEFAULT_MODEL と同値。


def _default_m2e_model_preload() -> None:
    """CREPE / Demucs を実際に load する既定の preload 実装。

    E-50（PR #242 第2巡 Codex P2 是正・**前回 D-6 の判断を撤回する**）: Design Memo
    決定済み判断 5「ワーカーは multiprocessing + initializer でモデルロード」（§8.4 の
    `S` = プロセスプール起動〜モデルロード完了、という定義と一致させる）から、前回の
    実装は「スレッド固定のみ適用し、モデルロードは行わない」という形で逸脱していた
    （その判断は誤りだったとして撤回する）。

    **CREPE**: `crepe` 自身が持つ既存の遅延ロード singleton
    （`crepe.core.build_and_load_model` — `crepe.predict()` が内部で参照するのと
    同じ module-level キャッシュ）を、実音声なしで直接呼んで eager 化する。新しい
    ロード機構は作らない。

    **Demucs**: `demucs.api.Separator` を initializer 内で 1 回構築し、破棄する
    （in-process 保持はしない・**既知の限界**）。`svp_rpe.io.source_separator.
    _separate_stems_with_api` は呼び出しのたびに新しい `Separator` を構築する契約
    になっており（既存テスト群（`tests/test_source_separator.py` 等）がその契約に
    依存した fake で観測する）、その per-call 構築を worker 内で恒久的に回避する
    配線は `src/svp_rpe/io/source_separator.py` の変更を要する。`src/svp_rpe/**` は
    Design Memo の Scope OUT であり、変更すれば影響範囲が本ブリーフの検証対象
    （`tests/test_m2_accuracy_harness.py`）を超えて既存の `source_separator` 系
    テスト群へ及ぶため、本セッションでは行わない。

    **preload の実際の効果（E-63・PR #242 第4巡 Codex 裁定「見送り」・是正済み記載）**:
    ここで前倒しされるのは重み**ダウンロード**（初回のみ）と OS ページキャッシュ・
    フレームワーク初期化コストという**一回性のスパイク**であり、`Separator`
    オブジェクトの in-memory 保持ではない——per-call の `Separator` 再構築コスト
    自体は全 stem セルに一様にかかり続ける。この一様な per-call コストは r2-0 の
    `T_stem` 実測にも同一コードパスで含まれるため（初回スパイクだけが `S` へ
    前倒しされていれば）、`S`/`T_*` の分離とシャード幅の正しさ（cap 計算）は
    崩れない。E-63 では in-process 保持化への昇格を見送った（`src/svp_rpe/io/
    source_separator.py` の per-call 構築契約を変更する必要があり Scope OUT のため）
    ——r4 実測で per-call 再構築コストが `T_stem` の有意割合と判明すれば、別ブリーフ
    で保持化の seam を検討する。
    """
    try:
        from svp_rpe.rpe.learned.crepe_adapter import ensure_crepe_available

        ensure_crepe_available()
        import crepe.core as _crepe_core  # 未導入環境で本体 import を汚さないため関数内 import

        _crepe_core.build_and_load_model(_M2E_CREPE_PRELOAD_CAPACITY)
    except Exception:
        pass  # crepe 未導入 / 旧版で build_and_load_model が無い等（direct のみの構成もある）
    try:
        from svp_rpe.io.source_separator import (
            DEFAULT_SHIFTS,
            _get_demucs_separator_class,
        )

        separator_cls = _get_demucs_separator_class()
        separator_cls(model=_M2E_DEMUCS_PRELOAD_MODEL, device="cpu", shifts=DEFAULT_SHIFTS)
    except Exception:
        pass  # demucs 未導入 / direct のみの構成では不要


def _shard_pool_initializer(
    preload_fn: "Optional[Callable[[], None]]" = None,
) -> None:
    """multiprocessing ワーカー起動時のスレッド 3 点固定 + モデル preload（決定済み

    設計判断 5・E-50 是正）。

    env 2 点（OMP/MKL）は**親プロセスが pool 起動前に**検証済み（`execute_m2e_shard`）
    ——spawn の子は親の環境変数をそのまま引き継ぐため、ここでの再検証は多重防御。
    `torch.set_num_threads(1)` はプロセスごとに効かせる必要があるため、initializer 内
    （＝各ワーカーで 1 回）で適用する。

    `preload_fn`（テスト seam・E-50）: 省略時は `_default_m2e_model_preload`
    （実ローダ）を呼ぶ。テストは記録用の picklable top-level fake を注入して
    「initializer が preload を呼ぶ」ことだけを検証する（実モデルはテスト環境に
    無いため）。
    """
    _apply_thread_pinning()
    (preload_fn if preload_fn is not None else _default_m2e_model_preload)()


def _shard_worker_measure_cell(task: "Dict[str, Any]") -> "Dict[str, Any]":
    """multiprocessing ワーカーの既定エントリポイント（picklable・top-level）。

    env_digest を再計算し、親が積んだ期待値と一致しなければこのセルを開始しない
    （設計 §8.6・fail-closed）。例外は `apply_async().get()` で親へ伝播し shard 実行を
    中断する——「セルレコードを書かず打ち切る」ハング経路とは別の、環境不整合による
    即時中断。
    """
    expected_env_digest = task["env_digest"]
    actual_env_digest = _env_digest()
    if actual_env_digest != expected_env_digest:
        raise RuntimeError(
            f"shard worker: env_digest 不一致 (expected={expected_env_digest!r}, "
            f"actual={actual_env_digest!r}); ワーカーの環境が親と食い違うため、この "
            "セルを開始しない (fail-closed・設計 §8.6)"
        )
    return _shard_measure_and_record_cell(task, runner=observe_via_route_with_provenance)


def _m2e_valid_cell_record(cell: "Dict[str, Any]") -> "Optional[Tuple[Path, Dict[str, Any]]]":
    """`cell`（`execute_m2e_shard` の task dict）が指すセルレコードが `cell_store` に

    digest 一致で存在するか確認する（E-54・E-82 共通の判定ロジック）。

    存在し digest も一致すれば `(record_path, record)` を返す。レコードが無い・
    壊れている・digest が食い違う場合は `None` を返す（fail-closed——「完了と
    立証できない」場合は None）。`_reconcile_truncated_m2e_cell`（打ち切り照合）と
    dispatch 前スナップショット（E-82）の両方から呼ばれる同一基準。
    """
    cell_store = Path(cell["cell_store"])
    record_path = _cell_store_record_path(
        cell_store,
        category=cell["arm"],
        level=cell["level"],
        entry_id=cell["entry_id"],
        repeat_index=cell["repeat_index"],
    )
    if not record_path.is_file():
        return None
    try:
        record = _json_loads_no_dup_keys(
            record_path.read_bytes(), what=f"cell record {record_path}"
        )
        inputs = _read_external_clip_inputs(
            cell["entry_id"],
            cell["entry"],
            manifest_dir=Path(cell["manifest_dir"]),
            fixtures=cell["fixtures"],
        )
    except (ValueError, OSError):
        return None
    mismatches = _cell_record_mismatches(
        record,
        category=cell["arm"],
        level=cell["level"],
        entry_id=cell["entry_id"],
        repeat_index=cell["repeat_index"],
        audio_sha256=inputs.audio_sha256,
        annotation_sha256=inputs.annotation_sha256,
        env_digest=cell["env_digest"],
        tolerance_cents=cell["tolerance_cents"],
        est_voiced_floor=cell["est_voiced_floor"],
        store_role=_CELL_STORE_ROLE_RUN,
    )
    if mismatches:
        return None
    return record_path, record


def _reconcile_truncated_m2e_cell(cell: "Dict[str, Any]") -> "Optional[Dict[str, Any]]":
    """打ち切り時点で in-flight だった M2e セルに、実は digest 一致で完了済みの

    セルレコードが無いか確認する（E-54・PR #242 第2巡 Codex P2 是正）。

    `pool.terminate()` は worker の atomic `os.replace()` 成功と `AsyncResult` の
    ready 化との間の窓で発火しうる——その場合、既存の resume 判定と同じ digest
    一致照合（`_m2e_valid_cell_record`）で「完全に書き上がっている」と確認できた
    セルは completed として扱う（`run_m2e_shard_queue` の `reconcile_hung_cell`
    フック経由で呼ばれる）。

    `cell` は `execute_m2e_shard` が組み立てた task dict（bed_id/level/clip_id/arm/
    repeat_index/entry_id/entry/fixtures/manifest_dir/tolerance_cents/
    est_voiced_floor/cell_store/env_digest に加え、E-82 が dispatch 前に埋める
    `_pre_dispatch_had_valid_record` を持つ）。レコードが無い・壊れている・digest
    が食い違う場合は `None` を返し（= truncated のまま）、fail-closed に倒す。

    `_pre_dispatch_had_valid_record`（E-82・PR #242 第10巡 Codex 是正）: 以前は
    ここで見つけたレコードを無条件に「この起動が書いた」として `resumed: False` /
    `written_paths` へ計上していた——しかし dispatch 前から既に有効なレコードが
    在ったセル（前回起動の resume 対象）が、worker 内の resume 判定に辿り着く前に
    ハングと誤認されて打ち切られた場合も、このレコードを"この起動が書いた"と
    誤って扱ってしまう。実行前から在ったレコードを quarantine 対象
    （`written_paths`）に含めると、この起動と無関係な既存の有効レコードまで、
    後続の pin 失敗時に消してしまいうる。dispatch 前スナップショットに照らして
    resumed / written_paths を正しく区別する。
    """
    found = _m2e_valid_cell_record(cell)
    if found is None:
        return None
    record_path, record = found
    clip_row = record.get("clip_row") if isinstance(record, dict) else None
    outcome = clip_row.get("outcome") if isinstance(clip_row, dict) else None
    detail = clip_row.get("detail") if isinstance(clip_row, dict) else None
    pre_existing = bool(cell.get("_pre_dispatch_had_valid_record"))
    return {
        "resumed": pre_existing,
        # E-92（PR #242 第14巡 Codex 是正）: 通常の worker 経路（`entry_id in
        # cells_resumed` / `entry_id in cells_measured`）は resumed/measured を
        # 互いに排他な分類として扱う——resumed なら measured ではない。以前は
        # ここで measured を無条件 True にしていたため、pre-existing（resumed）な
        # セルが cells_resumed と cells_measured の両方に二重計上されていた。
        "measured": not pre_existing,
        "mismatches": [],
        "outcome": outcome or "measured",
        "detail": detail,
        # E-61/E-82: 本起動が書いたと確定できるレコードのみ written_paths に含める
        # （実行前から在ったレコードは quarantine 対象から除外する）。
        "written_paths": [] if pre_existing else [str(record_path)],
    }


def run_m2e_shard_queue(
    cells: "List[Dict[str, Any]]",
    *,
    session_budget: float,
    hang_grace_seconds: float = _M2E_HANG_GRACE_S,
    workers: int,
    measure_fn: "Callable[[Dict[str, Any]], Dict[str, Any]]" = _shard_worker_measure_cell,
    initializer: "Optional[Callable[[], None]]" = _shard_pool_initializer,
    clock: "Callable[[], float]" = time.monotonic,
    poll_interval: float = _M2E_SHARD_POLL_INTERVAL_S,
    reconcile_hung_cell: "Optional[Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]]" = None,
    on_worker_error: "Optional[Callable[[List[Dict[str, Any]]], None]]" = None,
    start: "Optional[float]" = None,
) -> "Dict[str, Any]":
    """§8.6 の動的キュー + 開始許可式 + 打ち切りを実装する（1 shard 分）。

    `cells` は当該 shard のセルのみ、§8.5 order で既に整列済み、各要素は `cost`
    （秒）キーを持つこと（`_m2e_shard_cells_for` が組み立てる）。配布は 1 セルずつ
    （静的等分ではない）。admission 判定（`elapsed + cost(cell) <= session_budget`）は
    親側の単調クロックで行う。

    テスト可能性（設計判断・Test Strategy）: `measure_fn` は picklable な top-level
    callable を注入できる（spawn は monkeypatch を継承しないため）。**常に
    multiprocessing（spawn context）の Pool を経由する**——`workers == 1` でも
    プロセス境界を跨ぐ（§8.4 の `S` = プロセスプール起動〜モデルロード完了、という
    定義と、ハング打ち切りに実プロセス `terminate()` が要ることの両方に整合させる
    ため）。

    打ち切り: shard 開始時刻からの経過が `session_budget + hang_grace_seconds`
    （全 in-flight セル共通の絶対期限・E-65 是正）を超えたら pool を `terminate()`
    する。以降のセル（未着手）へは進まない（「超過は異常ではなく通常状態」であり、
    延長はしない）。

    E-54（PR #242 第2巡 Codex P2 是正）: `pool.terminate()` は worker の atomic
    `os.replace()` 成功と `AsyncResult` の ready 化との間の窓で発火しうる——その
    場合セルレコードは既に完全に書き上がっているのに、単純な「in-flight は全部
    打ち切り」では「未完」と誤って記録してしまう（次回実行がその実在するレコードを
    resume するのに、実行記録は「レコードを書いていない」と主張する食い違いが
    生じる）。よって `terminated_for_hang` になった in-flight セルは、
    `reconcile_hung_cell`（既定 `None` = 照合しない）が非 `None` を返せば
    **completed** として計上し、`None`（またはコールバック未指定）のときのみ
    truncated として記録する。`run_m2e_shard_queue` 自身はセルレコードの形式を
    知らない（テストは合成セルで機構を検証する）——実際の digest 一致照合
    （`_cell_store_record_path` / `_cell_record_mismatches`）は
    `execute_m2e_shard` 側の `_reconcile_truncated_m2e_cell` が担う。

    `on_worker_error`（E-77・PR #242 第7巡 Codex P2 是正）: いずれかの worker が
    例外を送出した場合、本関数は既定では `completed` を呼び出し元へ返さずに直接
    `aborted_exception` を再送出する——`execute_m2e_shard` の post-execution pin
    再検証（E-48/E-61）が完走済み worker の written_paths を一切見られず、完走分の
    隔離判断もできない穴になっていた。`on_worker_error`（既定 `None` = 何もしない）
    を渡せば、`aborted_exception` を再送出する**直前**にその時点の `completed`
    リストを渡して呼ぶ——`reconcile_hung_cell` と同じ「機構は形式を知らない・
    意味論は呼び出し元が注入する」設計。フックが例外を送出しても、本関数は
    その例外ではなく常に元の `aborted_exception` を再送出する（フック内で
    捕捉・隔離まで完結させる契約——呼び出し元が「保全済みパスを隔離してから
    元例外を伝播」を自分で実装する）。

    E-80（PR #242 第9巡 Codex 是正）: 同一ポーリングで複数の `AsyncResult` が
    同時に ready なとき、以前は最初の例外で ready_indices の for ループを即座に
    break していたため、その後ろに並んでいた既に ready だった成功セルの結果が
    `completed` へ積まれずに失われていた（`on_worker_error` の隔離ネットにも
    載らない穴）。ready バッチは最後まで drain してから中断し、`break` 時点で
    まだ `in_flight` に残っていた（ready ではなかった）セルも
    `reconcile_hung_cell`（E-54 と同じ digest 一致照合）で「実際に publish
    済みか」を確認してから `completed` へ加える——`pool.terminate()` の瞬間との
    競合で書き終えていたレコードを取りこぼさない。

    `start`（E-76・PR #242 第7巡 Codex P1 是正）: 省略（既定 `None`）なら Pool 構築
    直後にここで捕捉する（従来どおり）。呼び出し元が明示的に渡せば、その値を
    admission 会計（`elapsed = clock() - start`）と打ち切り期限
    （`start + session_budget + hang_grace_seconds`）の絶対基準として採用する——
    `execute_m2e_shard` はこれを使って、本関数呼び出しより前の preflight 所要時間を
    両方の会計へ含める。

    E-93（PR #242 第14巡 Codex P1 是正）: `async_result.get()` の外——admission
    判定・`time.sleep`・打ち切り期限チェックの `clock()` 呼び出し等——で
    `BaseException`（`KeyboardInterrupt`/`SystemExit` 等）が逸出しても、
    `pool.terminate()` と in_flight の drain/reconcile（E-80 と同じ経路）・
    `on_worker_error` フック呼び出しを必ず経由してから元例外を再送出する。以前は
    この経路だけ `aborted_exception`/`terminated_for_hang` のどちらも設定されず、
    finally が `pool.close()`（in-flight の自然完了待ち）へ落ちてハング worker が
    あれば無期限にブロックし、実行後検査（quarantine 経路）も迂回していた。
    """
    if workers < 1:
        raise ValueError(f"run_m2e_shard_queue: workers {workers!r} は 1 以上のみ許可する")

    ctx = multiprocessing.get_context("spawn")
    pool = ctx.Pool(processes=workers, initializer=initializer)
    # E-76（PR #242 第7巡 Codex P1 是正）: `start` 省略時（既定 `None`）はここで捕捉する
    # （従来どおり）。呼び出し元（`execute_m2e_shard`）が渡せば、preflight（登録簿
    # 再構築・先行セル hash・manifest 読取・pool 構築）より前の入口時刻を採用し、
    # それらの所要時間を admission 会計・打ち切り期限の両方に含める。
    start = clock() if start is None else start
    completed: "List[Dict[str, Any]]" = []
    truncated: "List[Dict[str, Any]]" = []
    not_started: "List[Dict[str, Any]]" = []
    # index -> (AsyncResult, セルが dispatch された壁時計, cell)
    in_flight: "Dict[int, Tuple[Any, float, Dict[str, Any]]]" = {}
    next_index = 0
    aborted_exception: "Optional[BaseException]" = None
    terminated_for_hang = False
    # E-107（PR #242 第20巡 Codex P1 是正）: idle 退出（in_flight ゼロで抜ける）は
    # 期限の残量に関わらず必ず terminate で畳む（下記参照）。
    idle_exit = False

    try:
        while True:
            # E-93（PR #242 第14巡 Codex P1 是正）: この try は `async_result.get()`
            # 専用の内側 try（例外セルの分類）とは別に、ループ本体**全体**を囲む。
            # `KeyboardInterrupt`/`SystemExit` 等が admission 判定・`time.sleep`・
            # `ar.ready()` 呼び出しなど、内側の get() の**外**で逸出すると、以前は
            # `aborted_exception`/`terminated_for_hang` のどちらも設定されないまま
            # finally が `pool.close()` へ落ち、ハング中の worker があれば
            # `pool.join()` が無期限にブロックしていた——さらに、この経路では
            # in_flight の drain/reconcile も `on_worker_error` フックも一切
            # 通らず、公開済みかもしれないレコードが実行後検査を迂回していた。
            # 親側から逸出するあらゆる `BaseException` を abort として扱い、
            # 必ず `pool.terminate()` → drain/reconcile → フック呼び出しの経路へ
            # 通してから元例外を再送出する。
            try:
                elapsed = clock() - start
                admitted_any = False
                while len(in_flight) < workers and next_index < len(cells):
                    cell = cells[next_index]
                    if elapsed + cell["cost"] > session_budget:
                        break
                    async_result = pool.apply_async(measure_fn, (cell,))
                    in_flight[next_index] = (async_result, clock(), cell)
                    next_index += 1
                    admitted_any = True
                    elapsed = clock() - start
                if not in_flight:
                    # E-103（PR #242 第18巡 Codex 是正）: 空のまま退出する**前**に
                    # 絶対期限（`session_budget + hang_grace_seconds`）を検査する。
                    # 素朴に `break` するだけだと、`terminated_for_hang` が立たない
                    # ため finally が `pool.close()`（in-flight の自然完了待ち）へ
                    # 落ちる——`initializer` 自体がハングしている worker がいれば
                    # （一度もタスクを dispatch していなくても）、その worker は
                    # pool-close の合図を受け取れる状態に一度も到達できず、
                    # `pool.join()` が無期限にブロックしうる。期限超過ならここでも
                    # `terminated_for_hang` を立て、E-93 と同じ abort 後始末
                    # （terminate・drain/reconcile・記録の整合）を通す
                    # （`in_flight` が空なので reconcile 対象は無いが、経路は揃える）。
                    if clock() - start > session_budget + hang_grace_seconds:
                        terminated_for_hang = True
                    # E-107（PR #242 第20巡 Codex P1 是正）: 上の期限検査（E-103・維持）
                    # とは独立に、idle 退出は**常に** terminate で畳む——期限内でも、
                    # 一度もタスクを dispatch していない worker の `initializer` が
                    # ハングしていれば `pool.close()`+`join()` は無期限にブロック
                    # しうる（in_flight が空＝reconcile 対象も無いので安全に terminate
                    # できる）。
                    idle_exit = True
                    break  # 実行中が無く、これ以上許可できるセルも無い
                ready_indices = [
                    idx for idx, (ar, _started, _cell) in in_flight.items() if ar.ready()
                ]
                for idx in ready_indices:
                    async_result, _cell_started_wall, cell = in_flight.pop(idx)
                    try:
                        result = async_result.get()
                    except BaseException as exc:  # noqa: BLE001 — 親へ即座に伝播し shard を中断する
                        # E-80（PR #242 第9巡 Codex 是正）: 同一バッチで複数の
                        # AsyncResult が同時に ready なとき、以前はここで即座に
                        # break しており、この exc より後ろに並んでいた**既に
                        # ready だった**成功セルが `completed` へ積まれずに
                        # 失われていた——`on_worker_error`（E-77）の隔離ネットにも
                        # 載らないまま、書き上がった published レコードが会計から
                        # 漏れる穴だった。最初の例外は保持しつつ、同じバッチの
                        # 残りは drain し続ける（2 件目以降の例外は握り潰す——
                        # 伝播するのは常に最初の例外 1 つ）。
                        if aborted_exception is None:
                            aborted_exception = exc
                        # E-131（PR #242 第31巡 Codex 是正）: worker がセルレコード
                        # を atomic 公開した**直後**（return 前）に例外を上げた
                        # 場合、このセルは既に `in_flight` から pop 済みのため、
                        # 下（E-80 の abort 照合・`in_flight.items()` を舐める
                        # 経路）の digest 一致再照合の対象にならない——公開済み
                        # レコードの written_path が `on_worker_error` にも渡らず、
                        # pin ドリフト時の quarantine を逃れてしまう。E-54/E-82 と
                        # 同じ digest 一致 resume 検査（`reconcile_hung_cell`）で
                        # 「実際に公開済みか」をここで確認し、公開済みなら
                        # written_paths（quarantine 対象）へ計上できるよう
                        # `completed` へ足す（pre-existing 除外規則は
                        # `reconcile_hung_cell`/`_reconcile_truncated_m2e_cell`
                        # 側に既にある・E-82 のまま変更しない）。
                        reconciled = (
                            reconcile_hung_cell(cell) if reconcile_hung_cell is not None else None
                        )
                        if reconciled is not None:
                            completed.append({"cell": cell, "result": reconciled})
                        continue
                    completed.append({"cell": cell, "result": result})
                if aborted_exception is not None:
                    break
                # E-65（PR #242 第4巡 Codex P1 是正）: 打ち切り期限は shard 開始時刻
                # （`start`）基準の絶対期限——全 in-flight セルに共通。以前は各セルの
                # dispatch 時刻 (`cell_started_wall`) 基準だったため、B_session の
                # 終盤に配布されたセルは、そこからさらに満額の
                # B_session + hang_grace_seconds を得てしまい、§8.6「1 回の実行の
                # 壁時計上限」を大きく超過しうる（例: 7200s 目に配布されたセルが
                # 14400s+ まで shard を生かし続ける）。許可式
                # （`elapsed + cost(cell) <= session_budget`）が admitted セルの
                # 開始時刻を B_session 以内に既に制約しているので、shard 開始基準
                # でも各セルは最低 hang_grace_seconds 分 + 自コスト分の猶予を持つ。
                if clock() - start > session_budget + hang_grace_seconds:
                    terminated_for_hang = True
                    break
                if not ready_indices and not admitted_any:
                    time.sleep(poll_interval)
            except BaseException as exc:  # noqa: BLE001 — E-93: 親側の逸出は必ず abort 経路へ
                if aborted_exception is None:
                    aborted_exception = exc
                break
    finally:
        # E-107: idle_exit も terminate 経路へ含める（close()/join() のハング
        # initializer 無期限待ちを avoid）。
        if aborted_exception is not None or terminated_for_hang or idle_exit:
            pool.terminate()
        else:
            pool.close()
        pool.join()

    if aborted_exception is not None:
        # E-80（PR #242 第9巡 Codex 是正）: この時点で `in_flight` に残っているのは
        # 「break した時点でまだ ready ではなかった」セルのみ（ready 済みは上の
        # for ループで既に pop・drain 済み）。`pool.terminate()`（finally 節）が
        # これらを未完のまま殺す前提だが、実際には terminate() の瞬間との窓で
        # atomic replace が先に完了していることがある（E-54 と同型の競合）。
        # `reconcile_hung_cell` による digest 一致照合で「実際に publish 済みか」を
        # 確認してから `completed` へ足す——確認せず切り捨てると、たまたま書き
        # 終えていたレコードが `on_worker_error` の隔離ネットからも漏れる。
        for _idx, (_ar, _started, cell) in in_flight.items():
            reconciled = reconcile_hung_cell(cell) if reconcile_hung_cell is not None else None
            if reconciled is not None:
                completed.append({"cell": cell, "result": reconciled})
        # フックを試すが、フック自身が例外を送出しても常に元の
        # aborted_exception を再送出する（フックの失敗で worker 例外の情報を
        # 握り潰さない・フックは自身の例外を自前で処理しきる契約だが、念のため
        # 二重の防御として本関数側でも捕捉する）。
        if on_worker_error is not None:
            try:
                on_worker_error(completed)
            except BaseException:  # noqa: BLE001 — 元例外を最優先で伝播する
                pass
        raise aborted_exception

    if terminated_for_hang:
        for _idx, (_ar, _started, cell) in in_flight.items():
            reconciled = reconcile_hung_cell(cell) if reconcile_hung_cell is not None else None
            if reconciled is not None:
                completed.append({"cell": cell, "result": reconciled})
            else:
                truncated.append(cell)
    not_started.extend(cells[next_index:])

    return {
        "completed": completed,
        "truncated": truncated,
        "not_started": not_started,
        "elapsed_seconds": clock() - start,
    }


def _m2e_collect_written_paths(completed_entries: "List[Dict[str, Any]]") -> "List[str]":
    """`run_m2e_shard_queue` の `completed` エントリ群から `written_paths` を集める。

    成功経路（E-61）と worker 例外経路（E-77・`on_worker_error` フック）の双方が
    使う共通ヘルパー——集計基準を 1 箇所に保つ。
    """
    paths: "List[str]" = []
    for entry in completed_entries:
        paths.extend(entry["result"].get("written_paths") or [])
    return paths


def _m2e_shard_claim_path(cell_store: Path, shard_id: int) -> Path:
    """`shard_id` の排他 claim ファイルのパス（`cell_store` 配下）。"""
    return Path(cell_store) / f"shard_{shard_id}.claim"


def _acquire_m2e_shard_claim(claim_path: Path) -> None:
    """`claim_path` を `O_EXCL` で作り、同一 shard の並行実行を排他する

    （E-74・PR #242 第7巡 Codex 是正・**Memo 根拠の撤回**）。

    Design Memo は「`shard_id` の昇順強制が同一 shard の並行実行を防ぐ」としていたが
    誤りだった——`_require_prior_m2e_shards_complete` は `shard_id` **未満**の shard
    しか検査しないため、同じ `shard_id` への 2 並行実行はどちらもこの検査を通過し、
    同じセル鍵を同時に測定して同じチェックポイントパスへ atomic replace で衝突しうる
    （どちらかの隔離・上書きが他方の有効なレコードを消しうる）。既存の claim があれば
    fail-closed で拒否する（並行実行は非サポート。クラッシュ孤児で claim が残った場合は
    claim ファイルを手動削除してから再実行する）。内容は診断用（PID + ISO8601）で
    機構としての意味は持たない——存在自体が排他の唯一の根拠。

    E-128（PR #242 第29巡 Codex 是正）: `os.fdopen`/`write` の区間は
    `except Exception` ではなく `except BaseException` で覆う——`_acquire_m2e_
    out_reservation`（E-124）と同型の穴。`KeyboardInterrupt`/`SystemExit`/
    `GeneratorExit` は `Exception` のサブクラスではないため、書き込み中に
    これらが飛ぶと claim は削除されずに残り、以後の同一 `shard_id` の全起動が
    「claim が既に存在する」という誤った案内で永久にブロックされる。
    """
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    payload = f"pid={os.getpid()}\nclaimed_utc={_utc_now()}\n"
    try:
        fd = os.open(str(claim_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        existing = ""
        try:
            existing = claim_path.read_text(encoding="utf-8").strip()
        except OSError:
            pass
        raise ValueError(
            f"execute_m2e_shard: shard claim {claim_path} が既に存在する"
            f"（{existing!r}）; 並行実行は非サポート。クラッシュ孤児なら claim を "
            "手動削除して再実行する (fail-closed・E-74)"
        ) from None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
    except BaseException:
        claim_path.unlink(missing_ok=True)
        raise


def _release_m2e_shard_claim(claim_path: Path) -> None:
    """`_acquire_m2e_shard_claim` が作った claim を削除する。

    正常終了・例外経路のどちらでも `execute_m2e_shard` の try/finally から確実に
    呼ばれる（"確実に削除" が E-74 の要求）。
    """
    claim_path.unlink(missing_ok=True)


def _acquire_m2e_out_reservation(out_resolved: Path, token: str) -> Path:
    """`out_resolved` の排他予約を確保し、サイドカーのパスを返す（E-94・PR #242

    第15巡 Codex 是正の共通形。E-111・PR #242 第21巡 Codex 是正で
    `--make-shard-map --out` にも流用）。

    サイドカー `<out>.claim` を `O_CREAT|O_EXCL` で作る——既に存在すれば
    `FileExistsError` を送出する（呼び出し元が文脈に応じた案内文で fail-closed
    する。既存の shard claim・E-74・`_acquire_m2e_shard_claim` と役割が重なる
    場面もあるが、`--out` は `shard_id` と独立に指定できるため別物として扱う）。
    E-109（第20巡 Codex 是正）: `os.open` の前に親ディレクトリを作る
    （`_atomic_write_text` と同じ挙動へ整合）。

    E-124（PR #242 第28巡 Codex 是正）: `os.fdopen`/`f.write` の区間は
    `except Exception` ではなく `except BaseException` で覆う——
    `KeyboardInterrupt`/`SystemExit`/`GeneratorExit` は `Exception` のサブ
    クラスではないため、書き込み中にこれらが飛ぶと（Ctrl-C・呼び出し元の
    早期 SystemExit 等）サイドカーは削除されずに残る。呼び出し元の
    `finally: out_claim_sidecar.unlink(...)` は本関数が正常 return して
    `out_claim_sidecar` を束縛できた場合にしか届かない（本関数内で例外に
    なれば呼び出し元の変数はそもそも代入されない）ため、後始末は本関数
    内で完結させる必要がある——さもないと `O_EXCL` の孤児 claim が残り、
    以後の全起動が「他の起動が予約を保持している」という誤った案内で
    永久にブロックされる（手動削除が必須になる）。
    """
    sidecar = out_resolved.with_name(f"{out_resolved.name}.claim")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(sidecar), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(token)
    except BaseException:
        sidecar.unlink(missing_ok=True)
        raise
    return sidecar


def _rollback_m2e_out_reservation(
    out_resolved: Path, *, original_bytes: "Optional[bytes]"
) -> None:
    """`out_resolved` を予約前の状態へ原状復帰する（E-96/E-106・PR #242

    第15/19巡 Codex 是正の共通形。E-111・PR #242 第21巡 Codex 是正で
    `--make-shard-map` 側にも流用）。

    `original_bytes`（予約直前に読み取った実体。無かったなら `None`）を
    そのまま書き戻す——`--make-shard-map --out` は `--force` 付きで**非空の
    既存レコード**を上書き対象にできる（`--shard-id` 側は常に 0 バイト予約
    のみ）ため、「予約前は存在した」を一律 0 バイトへ truncate すると、
    `--force` で上書きしようとした既存レコードの中身を失敗時に破壊してしまう。
    元の bytes をそのまま復元すれば、mktemp の 0 バイト予約（`original_bytes
    == b""`）・非空の既存レコード（`--force`）のどちらでも安全に原状復帰する。
    未存在だったなら（`original_bytes is None`）削除する。

    E-114（PR #242 第22巡 Codex 是正）: 以前は `_atomic_write_text(out_resolved,
    original_bytes.decode("utf-8"))` を経由していた——`--force` の上書き対象が
    非 UTF-8 な既存ファイルだった場合、この `decode("utf-8")` 自体が
    `UnicodeDecodeError` を送出し、ロールバックを呼び出した**元の**例外
    （生成失敗の本当の原因）を隠して別の例外にすり替えてしまう。text 変換を
    一切経由せず `utils/atomic_io.atomic_write_bytes` へ bytes のまま渡す形へ
    改め、この decode 失敗の経路自体を消した。
    """
    if original_bytes is not None:
        _cell_store_atomic_write_bytes(out_resolved, original_bytes)
    else:
        out_resolved.unlink(missing_ok=True)


def _m2e_best_effort_spill_payload(doc: "Dict[str, Any]") -> str:
    """公開直前の直列化失敗時、spill 用に `doc` を可能な限り堅牢に文字列化する

    （E-121・PR #242 第26巡 Codex 是正）。

    厳格な直列化（`json.dumps(doc, indent=2, sort_keys=True)` / `yaml.safe_dump(doc,
    ...)`、どちらも `default=` 相当の緩和なし）が失敗しても、実行記録・地図の実データ
    （`doc`）自体はまだメモリ上に生きている——spill による保全をここで諦めない。
    型を緩めた `json.dumps(doc, default=str)` をまず試み、それすら失敗すれば
    （`str()` 自体が壊れた極端な型混入等）最後の手段として `repr(doc)` を返す
    （組み込みコンテナ + プリミティブに対して `repr` は失敗しない）。呼び出し元は
    戻り値の形式（JSON か repr か）を仮定せず、spill ファイルは「人間が手動復旧に
    使う best-effort な記録」として扱うこと。
    """
    try:
        return json.dumps(doc, indent=2, sort_keys=True, default=str)
    except BaseException:
        return repr(doc)


def _m2e_manifest_referenced_paths(
    campaign: "Dict[str, Dict[str, Path]]",
) -> "Tuple[set[Path], Dict[str, Tuple[List[Dict[str, Any]], Path]], Dict[str, str]]":
    """`campaign` の全水準の manifest を読み、preflight 保護用の参照パス集合と

    再利用可能なスナップショットの両方を返す（E-123・PR #242 第27巡 Codex 是正
    ／E-125・PR #242 第28巡 Codex 是正で戻り値をスナップショットまで拡張）。

    地図生成（`--make-shard-map --cell-store` 指定時の除外真実性スキャン——
    `_m2e_completed_cell_keys`）・実行（`--shard-id` の先行 shard 検査・task
    構築）はどちらも campaign の manifest を実際に読み、参照する audio/
    annotation の実ファイルを開く。preflight の保護パス集合（campaign/bars/
    manifest ファイル自体・fixtures ファイル自体）はこれまで manifest の
    **ファイルパス**しか含んでおらず、manifest が**指す**個々の audio/
    annotation の実体パスは含んでいなかった——`--out` がそのいずれかと同じ
    パスを指すと、地図生成/実行の書き出しが実測入力そのものを上書きしうる。
    ここで manifest を実際に読み、参照先を展開して返す（第 1 戻り値。呼び出し元が
    `protected` 集合へ合流させる）。

    E-125: 第 2/第 3 戻り値（`manifest_by_level`/`manifest_sha256_by_level`）は
    ここで読んだ manifest のパース済みスナップショット + sha256——E-72/E-104 と
    同じ形（`{level: (entries, manifest_dir)}` / `{level: sha256}`）。呼び出し元
    （`--make-shard-map` preflight）はこれを `generate_m2e_shard_map` 経由で
    `_m2e_completed_cell_keys`（除外真実性スキャン）へ引き回せば、同じ manifest
    ファイルを再度開かない（TOCTOU 回避。E-95 が記録する manifest digest も
    このスナップショット由来のまま一貫する）。
    """
    referenced: "set[Path]" = set()
    manifest_by_level: "Dict[str, Tuple[List[Dict[str, Any]], Path]]" = {}
    manifest_sha256_by_level: "Dict[str, str]" = {}
    for level, level_paths in campaign.items():
        entries, manifest_sha256, manifest_path = _load_external_manifest(
            level_paths["external_manifest"]
        )
        manifest_dir = manifest_path.parent
        manifest_by_level[level] = (entries, manifest_dir)
        manifest_sha256_by_level[level] = manifest_sha256
        for entry in entries:
            referenced.add(
                _resolve_external_member_path(
                    manifest_dir, entry["audio_path"], what="audio_path"
                )
            )
            referenced.add(
                _resolve_external_member_path(
                    manifest_dir, entry["annotation_path"], what="annotation_path"
                )
            )
    return referenced, manifest_by_level, manifest_sha256_by_level


def execute_m2e_shard(
    *,
    map_doc: "Dict[str, Any]",
    map_sha256: str,
    shard_id: int,
    campaign: "Dict[str, Dict[str, Path]]",
    cell_store: "str | Path",
    bars_path: Path = BARS_PATH,
    workers: int = 1,
    measure_fn: "Callable[[Dict[str, Any]], Dict[str, Any]]" = _shard_worker_measure_cell,
    initializer: "Optional[Callable[[], None]]" = _shard_pool_initializer,
    require_thread_pinning: bool = True,
    clock: "Callable[[], float]" = time.monotonic,
    preflight_manifest_by_level: "Optional[Dict[str, Tuple[List[Dict[str, Any]], Path]]]" = None,
    preflight_manifest_sha256_by_level: "Optional[Dict[str, str]]" = None,
) -> "Dict[str, Any]":
    """1 shard 分の実行（§8.6「1回の実行の契約」の実体）。

    **shard モードは run report / verdict / census のいずれも出さない。** 成果物は
    (a) `cell_store` 配下のセルレコード、(b) 本関数が返す shard 実行記録のみ。

    `start`（E-76・PR #242 第7巡 Codex P1 是正）: shard 開始時刻の捕捉を、地図検証・
    先行 shard の digest 一致 hash・manifest 読取・pool 構築より**前**（本関数の
    入口）へ移す。以前は `run_m2e_shard_queue` 内部が独自に `clock()` を捕捉して
    おり、これら preflight の所要時間が admission 会計にも `B_session + 600s` の
    打ち切り期限にも含まれていなかった——後続 shard ほど preflight（先行セル全数の
    再 hash 等）が重くなるため、§8.6「1 回の実行の壁時計上限」を preflight 分だけ
    超過しうる。ここで捕捉した `start` を `run_m2e_shard_queue(start=...)` へ渡し、
    キュー側の内部捕捉を上書きする。

    claim（E-74・PR #242 第7巡 Codex 是正）: `cell_store` 配下に `shard_<id>.claim`
    を `O_EXCL` で作り、同一 `shard_id` の並行実行を排他する
    （`_acquire_m2e_shard_claim` の docstring に Memo 根拠の撤回を記録）。正常終了・
    例外経路のどちらでも try/finally で確実に解放する。

    bars スナップショット（E-78・PR #242 第8巡 Codex 是正）: `_require_m2e_shard_map_
    matches_registry` が返す `(bars, bars_sha256)` をそのまま tolerance_cents/
    est_voiced_floor の導出・worker 供給まで引き回す（再オープンしない。E-57/E-72
    と同族の TOCTOU 是正）。

    除外検証の store 束縛（E-79・PR #242 第8巡 Codex 是正）: `_require_m2e_shard_map_
    matches_registry` へ本関数が実際に書き込む `cell_store` を渡す——地図の
    `excluded_completed_cells.cell_store_relative` が実行時 `--cell-store` と一致する
    ことを要求し、除外真実性の digest 検証もこの実行 store に対して行う（地図が別
    store 向けに宣言した除外を、別の store への実行に束縛して信用しない）。

    `started_utc`（E-84・PR #242 第10巡 Codex 是正）: `start`（単調クロック）と
    **同時**に本関数入口で捕捉する（以前は claim 取得・地図検証・manifest 読取後、
    `run_m2e_shard_queue` 呼び出し直前で別途捕捉しており、`start`/`elapsed_seconds`
    が指す起点と provenance が食い違っていた）。

    `preflight_manifest_by_level`/`preflight_manifest_sha256_by_level`（E-126・
    PR #242 第29巡 Codex 是正／E-133・PR #242 第32巡 Codex 是正で除外真実性
    再スキャン自体にも種付けするよう完備化）: CLI の preflight（`--out` の
    保護入力検査・E-123）が既に読んだ manifest のパース済みスナップショット
    （`{level: (entries, manifest_dir)}`）と sha256 を渡せば、(a)
    `_require_m2e_shard_map_matches_registry` 経由の除外真実性再スキャン
    （E-104 の `_m2e_completed_cell_keys` 呼び出し）、(b) 先行 shard 検証の
    `excluded_manifest_by_level`、(c) task 構築の既存 manifest 引き回し機構
    （E-72）の全段にこれを種付けする——除外つき地図でも manifest 読取は
    preflight の 1 回へ完全に一本化される（E-126 が導入した「E-104 の除外真実性
    再スキャンが読んだもの」＞「preflight が読んだもの」という優先順位は、
    E-133 により rescan 自体が同じ preflight 実体を消費するため実質的な縮退に
    なる）。未指定（既定 `None`）なら従来どおり内部で読む（直接呼ぶテスト等の
    後方互換経路）。
    """
    if shard_id < 0:
        raise ValueError(f"execute_m2e_shard: shard_id {shard_id!r} は 0 以上のみ許可する")
    # E-97: 無強制の型検査を先に敷く（E-83 と同型）。
    n_shards = _require_m2e_shard_map_integer_field("n_shards", map_doc["n_shards"])
    if shard_id >= n_shards:
        raise ValueError(
            f"execute_m2e_shard: shard_id {shard_id!r} が地図の n_shards={n_shards!r} 以上 "
            "(fail-closed)"
        )

    # E-76: start は preflight（登録簿再構築・先行セル hash・manifest 読取・pool
    # 構築）より前に捕捉する——これらの所要時間を admission 会計・打ち切り期限に含める。
    # E-84（PR #242 第10巡 Codex 是正）: `started_utc`（実行記録の provenance）も
    # `start`（単調クロック）と**同時**にここで捕捉する——以前は
    # `run_m2e_shard_queue` 呼び出し直前（claim 取得・地図検証・manifest 読取後）
    # で別途 `_utc_now()` していたため、「shard 開始時刻」を名乗る値が実際には
    # preflight 分だけ遅れていた（`start`／`elapsed_seconds` が指す起点と食い違う
    # 自己矛盾）。
    start = clock()
    started_utc = _utc_now()
    cell_store = Path(cell_store)
    cell_store.mkdir(parents=True, exist_ok=True)
    # E-74: 同一 shard_id の並行実行を排他する（Memo の「昇順強制で足りる」という
    # 根拠は誤りだった——撤回は `_acquire_m2e_shard_claim` docstring 参照）。
    claim_path = _m2e_shard_claim_path(cell_store, shard_id)
    _acquire_m2e_shard_claim(claim_path)
    try:
        # E-57: 地図検証で読取・hash 検証済みの fixtures 文書をそのまま実行段へ引き回す
        # （再オープンしない）。E-52 と同族の TOCTOU 是正。
        # E-79: 実際に書き込む cell_store を渡し、除外真実性検証をこの store へ束縛する。
        # E-104: 除外真実性の再スキャンが読んだ manifest スナップショットも受け取り、
        # 先行 shard 検査・task 構築へ引き回す（再オープンしない）。
        # E-133: preflight（保護入力検査・E-123）が既に読んだ manifest スナップ
        # ショットを除外真実性再スキャンへ種付けする（未指定時は None・従来
        # どおり内部で読む）——manifest 読取を preflight の 1 回へ一本化する。
        (
            validated_fixtures_by_level,
            bars_snapshot,
            excluded_manifest_by_level,
        ) = _require_m2e_shard_map_matches_registry(
            map_doc,
            campaign,
            bars_path=bars_path,
            cell_store=cell_store,
            manifest_snapshot_by_level=preflight_manifest_by_level,
            manifest_sha256_snapshot_by_level=preflight_manifest_sha256_by_level,
        )

        thread_pinning = _apply_thread_pinning() if require_thread_pinning else None

        env_digest = _env_digest()
        # E-78: 地図検証で読取・検証済みの bars をそのまま実行段へ引き回す（再オープン
        # しない）。E-57/E-72 と同族の TOCTOU 是正——検証後に bars が差し替わっても、
        # 実行はここで固定したスナップショットのまま tolerance_cents/est_voiced_floor
        # を導出し、worker へ供給する。
        bars, bars_sha256 = bars_snapshot
        bar_block = bars.verify(bars_sha256)["m2_accuracy_bars"]
        tolerance_cents = float(bar_block.get("tolerance_cents", DEFAULT_TOLERANCE_CENTS))
        est_voiced_floor = float(bar_block["est_voiced_confidence_floor"])

        # E-72（PR #242 第6巡 Codex P2 是正）: 戻り値は先行 shard 検証が既に読んだ
        # manifest スナップショット——task 構築ループが同じ level を再オープンしない
        # よう引き回す（E-57 の fixtures 引き回しと同じ形の TOCTOU 是正）。
        prior_manifest_by_level = _require_prior_m2e_shards_complete(
            map_doc,
            shard_id,
            cell_store=cell_store,
            campaign=campaign,
            env_digest=env_digest,
            tolerance_cents=tolerance_cents,
            est_voiced_floor=est_voiced_floor,
            fixtures_by_level=validated_fixtures_by_level,
            # E-104: 除外真実性再スキャンが既に読んだ manifest を再利用する。
            manifest_by_level=excluded_manifest_by_level,
        )

        shard_cells = _m2e_shard_cells_for(map_doc, shard_id)
        if not shard_cells:
            raise ValueError(
                f"execute_m2e_shard: shard_id {shard_id!r} に該当するセルが地図に無い "
                "(fail-closed)"
            )

        levels_needed = sorted({record["level"] for record in shard_cells})
        # E-72: 先行 shard 検証が既に読んだ level はそのまま再利用し、未読の level だけ読む。
        manifest_by_level: "Dict[str, Tuple[List[Dict[str, Any]], Path]]" = dict(
            prior_manifest_by_level
        )
        # E-57: 地図検証時に hash 検証済みの fixtures をそのまま使う（再オープンしない）。
        fixtures_by_level = validated_fixtures_by_level
        for level in levels_needed:
            if level in manifest_by_level:
                continue
            # E-126: 次点は CLI preflight（`--out` 保護入力検査・E-123）が既に
            # 読んだスナップショット——E-104 の除外真実性スキャンで読まれて
            # いない level でも、preflight が既に読んでいれば再オープンしない。
            if preflight_manifest_by_level is not None and level in preflight_manifest_by_level:
                manifest_by_level[level] = preflight_manifest_by_level[level]
                continue
            entries, _manifest_sha256, manifest_path = _load_external_manifest(
                campaign[level]["external_manifest"]
            )
            manifest_by_level[level] = (entries, manifest_path.parent)

        tasks: "List[Dict[str, Any]]" = []
        for record in shard_cells:
            level = record["level"]
            entries, manifest_dir = manifest_by_level[level]
            entry = next((e for e in entries if e["id"] == record["entry_id"]), None)
            if entry is None:
                raise ValueError(
                    f"execute_m2e_shard: entry {record['entry_id']!r} (level {level!r}) が "
                    "manifest に無い (fail-closed)"
                )
            task = {
                "bed_id": record["bed_id"],
                "level": level,
                "clip_id": record["clip_id"],
                "arm": record["arm"],
                "repeat_index": record["repeat_index"],
                "entry_id": record["entry_id"],
                "entry": entry,
                "fixtures": fixtures_by_level[level],
                "manifest_dir": str(manifest_dir),
                "tolerance_cents": tolerance_cents,
                "est_voiced_floor": est_voiced_floor,
                "cell_store": str(cell_store),
                "env_digest": env_digest,
                "workers": workers,
                "cost": record["cost"],
            }
            # E-82（PR #242 第10巡 Codex 是正）: dispatch **前**に、このセルが既に
            # digest 一致の有効レコードを持っているかスナップショットする——
            # `_reconcile_truncated_m2e_cell` がハング打ち切り照合時に「この起動が
            # 書いた」のか「実行前から在った」のかを区別するために使う（後者を
            # 誤って前者扱いすると、pin 失敗時に無関係な既存レコードまで隔離しうる）。
            task["_pre_dispatch_had_valid_record"] = _m2e_valid_cell_record(task) is not None
            tasks.append(task)

        def _on_worker_error(completed_so_far: "List[Dict[str, Any]]") -> None:
            """E-77（PR #242 第7巡 Codex P2 是正）: worker 例外の再送出**前**に呼ばれる。

            通常の成功経路（E-48/E-61）と同じ post-execution pin 再検証（first-party・
            同梱ネイティブ・実装 hash）を通し、失敗すればここまでに完走した worker の
            written_paths を隔離する。例外を握り潰さない——`run_m2e_shard_queue` 側の
            契約により、本フックが何を投げても最終的に伝播するのは worker の元例外。
            """
            written_paths_so_far = _m2e_collect_written_paths(completed_so_far)
            try:
                _require_unchanged_since_load()
                _require_dist_native_unchanged_since_bind()
                _require_runtime_code_unchanged_since_bind()
            except RuntimeError:
                _quarantine_cell_records(written_paths_so_far)

        # E-101: 無強制の型検査を経由する（`_require_m2e_shard_map_matches_registry`
        # が既に検証済みだが、E-97 の n_shards と同じく execute 側でも同じ形で
        # 読む一貫性を優先する）。
        session_budget = _require_m2e_shard_map_numeric_field(
            "session_budget_s", map_doc["inputs"]["session_budget_s"]
        )
        # E-84: started_utc は本関数入口で `start` と同時に捕捉済み（再捕捉しない）。
        result = run_m2e_shard_queue(
            tasks,
            session_budget=session_budget,
            hang_grace_seconds=_M2E_HANG_GRACE_S,
            workers=workers,
            measure_fn=measure_fn,
            initializer=initializer,
            clock=clock,
            # E-76: preflight より前に捕捉した start をキューへ渡す（内部捕捉を上書き）。
            start=start,
            # E-54: 打ち切り時点で in-flight だったセルを、既存の digest 一致 resume 判定で
            # 照合してから truncated へ振り分ける（書き上がっていたレコードは completed）。
            reconcile_hung_cell=_reconcile_truncated_m2e_cell,
            on_worker_error=_on_worker_error,
        )

        # E-61（PR #242 第4巡 Codex P2 是正）: 本 shard が新規に書いたレコードパスを
        # 集める（resume は含まない）。runtime pin 再検証が事後に失敗した場合の隔離対象。
        shard_written_paths = _m2e_collect_written_paths(result["completed"])

        def _cell_ref(entry: "Dict[str, Any]") -> "Dict[str, Any]":
            return {
                "bed_id": entry["bed_id"],
                "level": entry["level"],
                "clip_id": entry["clip_id"],
                "arm": entry["arm"],
                "repeat_index": entry["repeat_index"],
                "entry_id": entry["entry_id"],
            }

        # E-46（PR #242 Codex P1 是正）: `_measure_or_resume_external_clip_row` は
        # `outcome == "unavailable"`（抽出器スタック未導入）のセルにチェックポイントを
        # 書かない（§8.6「未完として記録する」と同じ精神）。しかしワーカーはこの場合も
        # 例外なく正常に返るため、`run_m2e_shard_queue` の `completed` にはそのまま
        # 積まれてしまう——ここで区別しないと、レコードの無いセルを「完了」と数えた
        # shard 実行記録が、次 shard の `_require_prior_m2e_shards_complete` で初めて
        # 矛盾として顕在化する（記録は嘘をつかないが、会計が食い違う）。
        # **shard 全体は中断しない**——セルは独立でよく、許可式が既に壁時計を有界化して
        # いるため、unavailable を理由に他のセルの実行機会を奪う必要が無い。
        measured_completed = [
            c for c in result["completed"] if c["result"]["outcome"] == "measured"
        ]
        unavailable_completed = [
            c for c in result["completed"] if c["result"]["outcome"] != "measured"
        ]
        resumed_refs = [
            _cell_ref(c["cell"]) for c in measured_completed if c["result"]["resumed"]
        ]
        measured_refs = [
            _cell_ref(c["cell"]) for c in measured_completed if c["result"]["measured"]
        ]
        unavailable_refs = [
            {**_cell_ref(c["cell"]), "reason": c["result"].get("detail") or "unavailable"}
            for c in unavailable_completed
        ]
        truncated_refs = [_cell_ref(c) for c in result["truncated"]]
        not_started_refs = [_cell_ref(c) for c in result["not_started"]]

        record = {
            "schema_version": _M2E_SHARD_RUN_SCHEMA,
            "started_utc": started_utc,
            "finished_utc": _utc_now(),
            "shard_id": shard_id,
            "n_shards": n_shards,
            "env_digest": env_digest,
            "workers": workers,
            "thread_pinning": thread_pinning,
            "shard_map_sha256": map_sha256,
            "session_budget_s": session_budget,
            "hang_grace_s": _M2E_HANG_GRACE_S,
            # E-116（PR #242 第22巡 Codex 是正）: `float(x)` は非数値スカラーを
            # 無強制で受理してしまう——ここも E-101/session_budget と同じ無強制
            # ヘルパで読む一貫性を優先する（既に `_require_m2e_shard_map_matches_
            # registry` で検証済みだが、execute 側の消費点でも同じ形で敷く）。
            "t_direct_s": _require_m2e_shard_map_numeric_field(
                "t_direct_s", map_doc["inputs"]["t_direct_s"]
            ),
            "t_stem_s": _require_m2e_shard_map_numeric_field(
                "t_stem_s", map_doc["inputs"]["t_stem_s"]
            ),
            "elapsed_seconds": result["elapsed_seconds"],
            "cells_total": len(shard_cells),
            "cells_completed": len(measured_completed),
            "cells_resumed": resumed_refs,
            "cells_measured": measured_refs,
            "cells_unavailable": unavailable_refs,
            "cells_truncated": truncated_refs,
            "cells_not_started": not_started_refs,
        }
        # E-48（PR #242 Codex P2 是正）: 数時間かかりうるキュー完走後、shard 実行記録を
        # 組み立てる・書き出す**前**に、load 時に pin した first-party ソース閉包が
        # 実行中に差し替わっていないことを確認する（既存の run/evaluate/census 経路と
        # 同じ post-execution ガード。差し替わっていれば「pin した digest」と「次回
        # import されるコード」が食い違い、書いたセルの由来が保証できなくなる）。
        # E-61（PR #242 第4巡 Codex P2 是正）: E-48 は first-party ソース閉包しか見ない。
        # 通常 run 経路（`env_digest_value is not None` 分岐）と同じく、同梱ネイティブ・
        # 実装 hash の束縛後差し替えも検査する——shard モードは常に `env_digest` を
        # 束縛するため、run 側のような条件分岐は要らない。
        # E-86（PR #242 第11巡 Codex 是正）: 以前は first-party 検査（`_require_
        # unchanged_since_load`）だけがこの try の**外**にあり、単独で失敗すると
        # shard_written_paths が隔離されないまま通常 store に残っていた（dist
        # native / runtime code とは非対称な扱い）。3 種すべてを同じ
        # 「失敗時 quarantine → raise」経路へ統合する——差し替え中の実装が産んだ
        # row を次 shard が resume しないように（run 側の失敗時パターンをそのまま
        # 踏襲する）。
        try:
            _require_unchanged_since_load()
            _require_dist_native_unchanged_since_bind()
            _require_runtime_code_unchanged_since_bind()
        except RuntimeError:
            _quarantine_cell_records(shard_written_paths)
            raise
        return record
    finally:
        # E-74: 正常終了・例外経路のどちらでも claim を確実に解放する。
        _release_m2e_shard_claim(claim_path)


# census phase は「渡されたか」を問う必要がある——値の比較では既定値の明示指定を検出
# できない（PR #241 Codex P2）。
_ARGPARSE_UNSET = object()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="出力 JSON の書き出し先")
    parser.add_argument(
        "--evaluate",
        nargs="+",
        type=Path,
        metavar="REPORT.json",
        help="run report(s) にバーを適用して verdict を出す（未指定なら run phase）",
    )
    parser.add_argument(
        "--census",
        nargs="+",
        type=Path,
        metavar="VERDICT.json",
        help="C5（設計 §6.2 / §11）: 4 水準 × 2 アームの verdict を集めて census 完全性を "
        "検査し、**揃っているときにだけ**帯の判定を出す。揃っていなければ出せるのは "
        "census のみ（完了セル数 / 期待数・(水準, アーム) 別の完了状況・欠けている組）で、"
        "平均 RPA も破断曲線も成果物に存在しない。--evaluate とは排他",
    )
    parser.add_argument("--bars", type=Path, default=BARS_PATH)
    parser.add_argument(
        "--m2e-bars",
        type=Path,
        default=_ARGPARSE_UNSET,
        metavar="m2e_accuracy_bars.yaml",
        help="M2e 帯（V_remix_real_*）の事前登録バー。M2 のバーとは意図的に別ファイル "
        "（設計 §5.1: 追記すると commit 済み M2b/M2c verdict の pin が壊れる）。"
        "共有スカラー（tolerance_cents / est_voiced_confidence_floor / repeats_min）は "
        "こちらに書かず --bars 側を参照する",
    )
    parser.add_argument(
        "--level",
        default=None,
        metavar="LEVEL",
        help="M2e の水準（歌声/ベッドの RMS 比。設計 §3.6 のラダー: "
        f"{' / '.join(_M2E_LEVEL_LADDER)}）。水準軸を持つカテゴリを測る run では必須。"
        "`gate_level` 以外の水準は破断曲線の記録専用で、evaluate はバーを適用しない",
    )
    parser.add_argument("--specs", type=Path, default=_ARGPARSE_UNSET)
    parser.add_argument(
        "--categories",
        nargs="+",
        metavar="CATEGORY",
        help="run phase で測るカテゴリの部分集合（既定: --external-manifest 未指定なら "
        "S_direct/S_fullstack のみ、指定時は事前登録された全カテゴリ = V_direct も含む "
        "——manifest 供給が V_direct の実行可能条件を満たすため。evaluate の測り直し "
        "プロセスが 1 カテゴリ run に使う）",
    )
    parser.add_argument(
        "--external-manifest",
        type=Path,
        default=None,
        metavar="MANIFEST.json",
        help="外部素材カテゴリ（M2c: V_direct）が指す音声/注釈 manifest（JSON 配列 "
        "[{id, audio_path, annotation_path}]）。V_direct を run/evaluate するには必須 "
        "（未指定は fail-closed）。パスは manifest 位置基準で相対解決する",
    )
    parser.add_argument(
        "--external-fixtures",
        type=Path,
        default=_ARGPARSE_UNSET,
        metavar="m2c_external_fixtures.yaml",
        help="外部素材カテゴリの事前登録 pin ファイル（既定: 凍結 committed ファイル）。"
        "測り直し子プロセスへの明示転送・`--out` 保護のためテストでの差し替えを想定",
    )
    parser.add_argument(
        "--cell-store",
        type=Path,
        default=None,
        metavar="DIR",
        help="設計 §8.7 のセルチェックポイント用ディレクトリ（opt-in・既定 None）。"
        "指定すると外部素材カテゴリの各 clip を 1 セルとして記録し、既存レコードが "
        "入力/環境 digest と一致すれば再測定をスキップする。未指定時は report に "
        "新フィールドが一切増えない（挙動無変更）。指定時は --repeat-index が必須。"
        "--make-shard-map と併用すると（E-66・§8.5「未完セルについてのみ再適用」）、"
        "digest 一致で完了済みのセルをパッキングから除外し残セルのみで再地図を組む",
    )
    parser.add_argument(
        "--repeat-index",
        type=int,
        default=None,
        metavar="N",
        help="このプロセスが担う repeat の番号（0 始まり）。--cell-store 指定時は "
        "必須・0 以上の整数のみ（セル鍵 (category, level, entry_id, repeat_index) "
        "の一部）。--cell-store 未指定時に渡すのは fail-closed で拒否する",
    )
    parser.add_argument(
        "--eval-cell-store",
        type=Path,
        default=None,
        metavar="DIR",
        help="rev.6 §8.9.2-(1) の **evaluate 専用**セルストア（store_B・opt-in）。"
        "測り直しの子プロセスがここへチェックポイントし、中断から復帰できるようになる。"
        "--cell-store（store_A = run 用）とは独立で、同一パス・一方が他方の配下は "
        "fail-closed。run phase（--evaluate なし）で渡すのも fail-closed。"
        "未指定時は verdict に新フィールドが一切増えない（挙動無変更）",
    )
    parser.add_argument(
        "--cell-store-role",
        default=_ARGPARSE_UNSET,
        choices=list(_CELL_STORE_ROLES),
        help="このプロセスが書くセルレコードの役割（既定 run）。**evaluate は "
        "測り直しの子プロセス専用**で、評価器が自動で付ける——手で渡すものではない。"
        "パスの分離だけでは計算の独立を保証できない（store_A をコピーすれば経路検査は "
        "通り、同一性フィールドも全部一致する）ため、役割をレコード自身に束縛し、"
        "evaluate のキャッシュで run 由来のレコードを resume しない (C2)",
    )
    parser.add_argument(
        "--pin-threads",
        action="store_true",
        help="スレッド 3 点固定（OMP_NUM_THREADS / MKL_NUM_THREADS / "
        "torch.set_num_threads）。設計判断 D-3: run と evaluate で**同じ**条件でなければ "
        "測り直しの bit 一致（= publish 条件）が壊れる。run phase ではこのプロセスに固定を "
        "適用し report の thread_pinning に刻む（env 2 点は**起動前に**設定されている "
        "必要がある。未設定は fail-closed —— プロセス開始後の設定は OpenMP/MKL に効かない）。"
        "evaluate phase は何も測らないので自プロセスには適用せず、評価対象 report が名乗る "
        "固定を契約として検証し、測り直しの子へ伝えて子 report の申告と再照合する",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=_ARGPARSE_UNSET,
        metavar="P",
        help="設計 §8.3 の並列度 P。**run phase と evaluate phase で意味が非対称**"
        "（設計判断 D-2）: run phase では宣言値で実行そのものは変えない（clip ループは "
        "逐次のまま。run 側のスケーリングは r5 のシャード地図が担う）——--cell-store "
        "使用時にセルレコードへコスト再現用に記録するだけ。evaluate phase では"
        "**実効並列度**で、外部素材カテゴリの測り直しの子プロセスを最大 P 本まで同時に "
        "起こす。**ただし 1 カテゴリあたりの子は repeats_min 本しかない**ので、実効値は "
        "min(P, repeats_min) で頭打ちになる（凍結 repeats_min=2 のとき P>2 は効かない。"
        "repeat より下の粒度＝clip/シャード単位の並列化は本実装の範囲外・別ブリーフ）。"
        "verdict の evaluate_execution.effective_workers_per_category に実効値を刻む",
    )
    parser.add_argument(
        "--make-shard-map",
        action="store_true",
        help="C6（設計 §8.5）: campaign + T_direct/T_stem/S/B_session から §8.5 の凍結"
        "アルゴリズムでシャード地図を生成する。同一入力からは完全にバイト一致の地図が "
        "出る（生成時刻は地図に含めず stdout へ印字する・E-67）。"
        "--evaluate/--census/--shard-id とは排他",
    )
    parser.add_argument(
        "--campaign",
        type=Path,
        default=None,
        metavar="m2e_campaign.yaml",
        help="M2e campaign ファイル（水準ごとの external manifest / external fixtures の "
        "所在のみを持つ。科学的パラメータは含まない）。--make-shard-map と --shard-id の "
        "両方が要求する",
    )
    parser.add_argument(
        "--t-direct",
        type=float,
        default=None,
        metavar="SECONDS",
        help="r2-0 で校正した direct アームの単位コスト T_direct（秒/セル）。"
        "--make-shard-map 専用",
    )
    parser.add_argument(
        "--t-stem",
        type=float,
        default=None,
        metavar="SECONDS",
        help="r2-0 で校正した stem アームの単位コスト T_stem（秒/セル）。--make-shard-map 専用",
    )
    parser.add_argument(
        "--startup-cost",
        type=float,
        default=None,
        metavar="SECONDS",
        help="S: プロセスプール起動〜モデルロード完了までの固定コスト（秒）。"
        "--make-shard-map 専用",
    )
    parser.add_argument(
        "--session-budget",
        type=float,
        default=_ARGPARSE_UNSET,
        metavar="SECONDS",
        help=f"B_session（設計 §8.2・既定 {_M2E_DEFAULT_SESSION_BUDGET_S:.0f}s = 2.0h）。"
        "--make-shard-map 専用（実行機は地図に記録された値をそのまま使う）",
    )
    parser.add_argument(
        "--shard-map",
        type=Path,
        default=None,
        metavar="m2e_r2_shard_map.yaml",
        help="C6 実行機: `--make-shard-map` が生成した地図。--shard-id と併用必須",
    )
    parser.add_argument(
        "--shard-id",
        type=int,
        default=None,
        metavar="N",
        help="C6 実行機（設計 §8.6）: この shard のセルのみを対象に実行する（1回 = "
        "1シャード）。昇順実行を要求する（先行 shard が全セル digest 一致で完了して"
        "いないと fail-closed）。--evaluate/--census/--make-shard-map とは排他。"
        "run report / verdict / census のいずれも出さない——成果物は --cell-store の "
        "セルレコードと --out の shard 実行記録のみ",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="`--make-shard-map` で `--out` が既に存在する場合の明示上書き許可"
        "（既定は fail-closed で拒否・シャード地図の黙示上書き禁止）",
    )
    args = parser.parse_args()
    _require_out_outside_git_metadata(args.out)
    if args.make_shard_map and args.shard_id is not None:
        raise SystemExit(
            "--make-shard-map と --shard-id は排他（地図生成器と実行機は別モード）"
        )
    if args.make_shard_map and (args.evaluate or args.census):
        raise SystemExit("--make-shard-map は --evaluate / --census と排他")
    if args.shard_id is not None and (args.evaluate or args.census):
        raise SystemExit(
            "--shard-id は --evaluate / --census と排他（shard モードは run report / "
            "verdict / census のいずれも出さない）"
        )
    # E-64（PR #242 第4巡 Codex P2 是正）・E-71（PR #242 第6巡 Codex P2 是正・完備化）:
    # `--shard-map`/`--campaign`/`--t-direct`/`--t-stem`/`--startup-cost`/明示指定の
    # `--session-budget`/`--force` は shard/地図生成専用のフラグ（`--make-shard-map` か
    # `--shard-id` のどちらかと必ず組む）。どちらの shard モードも起きていないのに
    # これらが供給されていれば、黙って無視して通常 run/evaluate/census へ入るのでは
    # なく、dispatch 前に fail-closed で拒否する（`--shard-id`/`--make-shard-map` の
    # 指定漏れ等で意図しない run report が出るのを防ぐ）。
    # E-100（PR #242 第17巡 Codex 是正）: `--session-budget` は以前、既定値からの
    # 差分を「明示指定」の代理指標として使っていた——既定値と**同値**の明示指定
    # （`--session-budget 7200` 等）を「未指定」と取り違えて素通りしてしまう
    # 穴があった。他のセンチネル済みフラグ（`--specs`/`--external-fixtures`/
    # `--workers`/`--cell-store-role` 等）と同じ `_ARGPARSE_UNSET` 方式へ統一し、
    # 値によらず「明示指定されたか」を正確に判定する。
    if not args.make_shard_map and args.shard_id is None:
        for flag, supplied in (
            ("--shard-map", args.shard_map is not None),
            ("--campaign", args.campaign is not None),
            ("--t-direct", args.t_direct is not None),
            ("--t-stem", args.t_stem is not None),
            ("--startup-cost", args.startup_cost is not None),
            ("--session-budget", args.session_budget is not _ARGPARSE_UNSET),
            ("--force", args.force is True),
        ):
            if supplied:
                raise SystemExit(
                    f"{flag} は --make-shard-map か --shard-id のどちらかと組み合わせる "
                    "shard/地図生成専用フラグ; どちらも指定されていないので黙って通常の "
                    "run/evaluate/census へ入らない (fail-closed)"
                )
    if args.make_shard_map:
        for flag, rejected in (
            ("--shard-map", args.shard_map is not None),
            ("--level", args.level is not None),
            ("--categories", args.categories is not None),
            ("--external-manifest", args.external_manifest is not None),
            ("--external-fixtures", args.external_fixtures is not _ARGPARSE_UNSET),
            ("--specs", args.specs is not _ARGPARSE_UNSET),
            # E-66（PR #242 第5巡 Codex 是正）: --cell-store は --make-shard-map の
            # 除外リストから外し、opt-in の任意引数として受理する（未完セルのみの
            # 再パッキング）。--repeat-index はそれでも生成器が読まないため引き続き拒否。
            ("--repeat-index", args.repeat_index is not None),
            ("--eval-cell-store", args.eval_cell_store is not None),
            ("--cell-store-role", args.cell_store_role is not _ARGPARSE_UNSET),
            ("--pin-threads", args.pin_threads is True),
            # E-98（PR #242 第16巡 Codex 是正）: 地図生成器は M2e 帯（--m2e-bars）を
            # 一切読まない（tolerance_cents 等の共有スカラーは --bars 側から取る）——
            # E-64 ゲートの列挙に元々抜けていた。センチネル化し明示指定を拒否する。
            ("--m2e-bars", args.m2e_bars is not _ARGPARSE_UNSET),
        ):
            if rejected:
                raise SystemExit(
                    f"{flag} は --make-shard-map と併用しない（地図生成器が読むのは "
                    "--campaign / --t-direct / --t-stem / --startup-cost / "
                    "--session-budget / --workers / --cell-store / --out / --bars だけ; "
                    "黙って無視して束縛されたと誤解させない (fail-closed)）"
                )
        if args.campaign is None:
            raise SystemExit("--make-shard-map には --campaign が必須")
        if args.t_direct is None or args.t_stem is None or args.startup_cost is None:
            raise SystemExit(
                "--make-shard-map には --t-direct / --t-stem / --startup-cost が必須"
            )
        # E-59（PR #242 第3巡 Codex P2 是正）: 地図に校正時の P を記録する（§8.4「production
        # と同じ P」の契約を実行側で検証可能にするため）。--workers は E-46〜E-54 の除外
        # リストから外し、--make-shard-map 専用の必須引数として要求する。
        if args.workers is _ARGPARSE_UNSET:
            raise SystemExit(
                "--make-shard-map には --workers が必須（校正した P を地図へ記録する。"
                "実行機は --shard-id 時にこの値を採用・照合する）"
            )
        if args.workers < 1:
            raise SystemExit(f"--workers {args.workers} は 1 以上の整数のみ許可する")
        # E-47（PR #242 Codex P2 是正）: `--bars` の指定を保護集合・生成の両方まで
        # 貫通させる（従来は保護のみ `BARS_PATH` 固定で、生成は常にモジュール既定を
        # 読んでいたため、カスタム --bars が「読まれる」と help に書きながら実際には
        # 無視されていた）。
        # E-70（PR #242 第6巡 Codex 是正）: campaign は preflight（保護パス集合の構築）で
        # 一度だけ読み、同一スナップショット + digest を生成側へ引き渡す
        # （`campaign_snapshot=`）——別々に読むと、この間にファイルが差し替わった場合、
        # 保護パス検査が見た campaign と実際に registry を供給する campaign が別 bytes
        # に由来しうる（E-52 と同族の TOCTOU）。
        campaign_for_preflight, campaign_sha256_for_preflight = _load_m2e_campaign_with_sha256(
            args.campaign
        )
        protected = {Path(args.campaign).resolve(), Path(args.bars).resolve()}
        for level_paths in campaign_for_preflight.values():
            protected.update(level_paths.values())
        # E-135（PR #242 第33巡 Codex 是正）: run/evaluate/census（既存経路）が
        # 敷いている保護と同じ規律を地図生成にも及ぼす——観測を実際に産む
        # first-party ソース閉包（`_generator_code_paths`）は `--shard-id` 側の
        # 実測（`execute_m2e_shard` の worker 測定）が消費する provenance 入力
        # であり、`--out`/spill がそれと同じパスを指すとコード自体を地図/
        # spill 出力で上書きしうる。地図生成自体は実測しないが、同じ
        # campaign/cell_store を共有する2つの CLI モードとして保護集合の対称性を
        # 保つため、こちら側にも予約前に加える。
        protected.update(_generator_code_paths())
        # E-123（PR #242 第27巡 Codex 是正）: `--cell-store` 指定時、地図生成は
        # 除外真実性スキャン（`_m2e_completed_cell_keys`）で campaign の manifest
        # を実際に読む——manifest が指す audio_path/annotation_path の実体は、
        # 直上の `level_paths.values()`（manifest/fixtures の**ファイルパス**
        # 自体）には含まれていない。`--out` がそのいずれかと同じパスを指すと、
        # 地図の書き出しが実測入力そのものを上書きしうるため、保護集合へ展開
        # して加える（`--cell-store` 未指定時は地図生成が manifest を一切読まない
        # ので対象外のまま——「manifest 未生成のうちに地図を作れる」という §8.5
        # の設計判断を壊さない）。
        # E-125（PR #242 第28巡 Codex 是正）: ここで読んだ manifest スナップショット
        # （`preflight_manifest_by_level`/`preflight_manifest_sha256_by_level`）は
        # 下の `generate_m2e_shard_map(...)` 呼び出しへ引き回す——除外真実性スキャン
        # （`_m2e_completed_cell_keys`）が同じ manifest ファイルを再度開かないように
        # する（E-72/E-104 と同族の TOCTOU 回避）。`--cell-store` 未指定時は
        # `None`（従来どおり `generate_m2e_shard_map` 側で読む）。
        preflight_manifest_by_level: "Optional[Dict[str, Tuple[List[Dict[str, Any]], Path]]]" = (
            None
        )
        preflight_manifest_sha256_by_level: "Optional[Dict[str, str]]" = None
        if args.cell_store is not None:
            referenced_paths, preflight_manifest_by_level, preflight_manifest_sha256_by_level = (
                _m2e_manifest_referenced_paths(campaign_for_preflight)
            )
            protected.update(referenced_paths)
        # E-117（PR #242 第24巡 Codex 是正）: `--out` を入口で 1 回だけ resolve し、
        # 以降の保護パス検査・no-clobber 検査・予約取得・claim 書き込み・公開
        # （atomic write）・token 検証・ロールバックの全段でこの単一スナップショット
        # を使う（`args.out` は後段で経路に使わない・エラーメッセージの表示にのみ
        # 使う）。以前は保護・予約系が `out_resolved`/`out_resolved_for_map` へ
        # 複数回 `Path(args.out).resolve()` していた一方、公開の 2 箇所
        # （claim トークン・最終 payload の `_atomic_write_text`）だけが未解決の
        # `args.out` のままだった——`--out` の最終要素が symlink だと、予約系は
        # 実体パスを見て所有権を判定するのに、`_atomic_write_text` の
        # `os.replace(tmp, path)` は symlink 自体を置換する（symlink の指す実体
        # ではなく symlink エントリそのものが新しい通常ファイルに置き換わる）ため、
        # 予約が守っていたはずの実体とは別の場所（symlink の位置）に中身が書かれ、
        # 原状復帰（`_rollback_m2e_out_reservation`）は実体側を触ってしまう——
        # 予約・公開・ロールバックが同じファイルを指さない不整合を生んでいた。
        out_resolved = Path(args.out).resolve()
        if out_resolved in protected:
            raise SystemExit(
                f"--out {args.out} は地図生成の入力（campaign / manifest / fixtures / "
                "bars）と同じパスを指している; 入力を地図で上書きしない (fail-closed)"
            )
        # E-81（PR #242 第9巡 Codex 是正）: `--make-shard-map --cell-store`（E-66）は
        # 除外真実性検証のため `cell_store` 配下のセルレコードを読む——`--out` が
        # その配下（root 自身または子孫）を指すと、地図の書き出しが既存セル
        # チェックポイントを上書きしうる。`--shard-id` 側の同種保護（E-51 系）と
        # 揃え、`--force` でも例外を許さない（--cell-store が指定された場合のみ；
        # 未指定なら比較対象が無い）。
        if args.cell_store is not None:
            cell_store_root = Path(args.cell_store).resolve()
            if out_resolved == cell_store_root or cell_store_root in out_resolved.parents:
                raise SystemExit(
                    f"--out {args.out} が --cell-store {args.cell_store} 配下にある; 地図の "
                    "書き出しでセルチェックポイントを上書きしない (fail-closed・E-81)"
                )
        # E-55（PR #242 第3巡 Codex P1 是正）: `--out "$(mktemp ...)"` が作る 0 バイトの
        # 予約ファイルは上書き対象として許容し、非空の既存レコードのみ fail-closed で
        # 拒否する（--shard-id 側の E-51 no-clobber と同じ規則に揃える）。
        if out_resolved.exists() and out_resolved.stat().st_size > 0 and not args.force:
            raise SystemExit(
                f"--out {args.out} が既に存在する（0 バイトではない）; 明示 --force か "
                "別パスの --out を使う (fail-closed・シャード地図の黙示上書き禁止・"
                "設計判断 1)"
            )
        # E-100（PR #242 第17巡 Codex 是正）: --session-budget をセンチネル化した
        # （既定値との差分では「省略」と「既定値と同値の明示指定」を区別できな
        # かった）ので、ここで既定を適用する（--make-shard-map だけが実際に消費する）。
        session_budget = (
            _M2E_DEFAULT_SESSION_BUDGET_S
            if args.session_budget is _ARGPARSE_UNSET
            else args.session_budget
        )
        # E-111（PR #242 第21巡 Codex 是正）: `--make-shard-map --out` にも、
        # shard-run 側（E-94/E-96/E-106）と同じ排他予約を適用する——地図生成も
        # 長時間かかりうるため、no-clobber 検査通過〜公開の窓で 2 起動が同じ
        # 予約状態（0 バイト/不存在）を狙って通過しうる。ヘルパを共通化して流用する。
        # E-111: `--force` は非空の既存レコードも上書き対象にできる（`--shard-id`
        # 側は常に 0 バイト予約のみ）ため、原状復帰は「存在したか」の bool ではなく
        # 元の bytes そのもの（`_rollback_m2e_out_reservation` 参照）で行う。
        # E-119（PR #242 第25巡 Codex 是正）: `except OSError` は `PermissionError`
        # （書込可能なディレクトリに読取不可の既存ファイルがある等）も「存在しない」と
        # 黙って扱ってしまう——`--force` はその後 claim で既存ファイルを置換でき、
        # 生成/公開が失敗した場合のロールバックは「原状復帰すべき中身」を知らないまま
        # unlink してしまう（原本を失う）。`FileNotFoundError` のみを「未存在」として
        # 受理し、他の読取エラーは claim 取得（＝原本への最初の書き込み）より前に
        # fail-closed で中断する（`/etc/ld.so.preload` 読取の E-... と同型の作法）。
        try:
            out_original_bytes = out_resolved.read_bytes()
        except FileNotFoundError:
            out_original_bytes = None
        except OSError as exc:
            raise SystemExit(
                f"--out {args.out} の既存内容を確認できない "
                f"({type(exc).__name__}: {exc}); --force の原状復帰の基準（既存内容）を "
                "立証できないまま予約・上書きを進めない (fail-closed・E-119)"
            ) from exc
        # E-122（PR #242 第27巡 Codex 是正）: `--force` が非空の既存レコードを
        # 上書き対象にする場合、原本には一切書き込まず、最終の atomic 置換まで
        # 温存する——以前は claim token を予約直後に原本へ書き込んでいたため、
        # 生成中に SIGKILL・電源断等でプロセスが不意に落ちると、ロールバックの
        # 拠り所である `out_original_bytes`（メモリ上のみ）は失われ、原本自体も
        # token で上書きされたまま永久に失われる経路があった。所有権は
        # サイドカー（`<out>.claim`・O_CREAT|O_EXCL）だけで検証すれば十分
        # （E-94 で既にこれが唯一の真の排他プリミティブになっている——`--out`
        # 本体への token 書き込みは E-85 以来の診断用の副産物に過ぎない）ので、
        # 非空原本ケースはこれを省略する。absent/0 バイト予約ケース（`--out
        # "$(mktemp ...)"`）は失うものが無いため、従来どおり token 書き込み・
        # 読み戻し検証を維持する（同じコード経路を素直に流用でき、変更を
        # 最小化できる）。
        out_has_nonempty_original = out_original_bytes not in (None, b"")
        shard_map_claim_token = (
            "m2e-shard-map-reservation/1\n"
            f"pid={os.getpid()}\n"
            f"claimed_utc={_utc_now()}\n"
        )
        try:
            out_claim_sidecar = _acquire_m2e_out_reservation(out_resolved, shard_map_claim_token)
        except FileExistsError:
            raise SystemExit(
                f"--out {args.out} の予約は他の起動が保持している（サイドカー "
                f"{out_resolved.with_name(f'{out_resolved.name}.claim')} が "
                "既に存在する）; 並行実行は非サポート。クラッシュ孤児ならサイドカーを "
                "手動削除して再実行する (fail-closed・E-111)"
            ) from None
        try:
            if not out_has_nonempty_original:
                _atomic_write_text(out_resolved, shard_map_claim_token)
            try:
                shard_map = generate_m2e_shard_map(
                    campaign_path=args.campaign,
                    t_direct=args.t_direct,
                    t_stem=args.t_stem,
                    startup_cost=args.startup_cost,
                    session_budget=session_budget,
                    bars_path=args.bars,
                    workers=args.workers,
                    cell_store=args.cell_store,
                    campaign_snapshot=(campaign_for_preflight, campaign_sha256_for_preflight),
                    # E-125: preflight（保護パス集合の構築・E-123）が既に読んだ
                    # manifest スナップショットを引き回す（未指定時は None・
                    # `generate_m2e_shard_map` 側が従来どおり読む）。
                    manifest_snapshot_by_level=preflight_manifest_by_level,
                    manifest_sha256_snapshot_by_level=preflight_manifest_sha256_by_level,
                )
            except BaseException:
                # E-122: 非空原本ケースは原本に一切書いていないので、ロールバック
                # （原状復帰）自体が不要——サイドカー解放（`finally`）だけで済む。
                if not out_has_nonempty_original:
                    _rollback_m2e_out_reservation(out_resolved, original_bytes=out_original_bytes)
                raise
            # E-121（PR #242 第26巡 Codex 是正）: 直列化（`yaml.safe_dump`）を含む
            # 「生成完了後〜公開完了まで」の残り全段を、直上の `generate_m2e_shard_map`
            # と同じ BaseException ロールバック範囲に入れる——以前は直列化とその直後
            # の token 検証がこの範囲の外にあり、直列化自体が失敗すると `--out` に
            # 自分の claim トークンだけが残ったままロールバックされず（サイドカーは
            # `finally` で解放されるので、次回起動は壊れた `--out` を非空の既存
            # レコードとして掴んでしまう）。地図（`shard_map` dict）はまだメモリ上に
            # 生きているので、`_m2e_best_effort_spill_payload` で可能な限り堅牢な
            # 形へ落として spill してからロールバックする（spill 自体の失敗は
            # ロールバック・再送出を妨げない）。「別起動が claim を差し替えていた」
            # 分岐（既に spill 済み・fail-closed で案内済み）はロールバック対象外
            # （`out_original_bytes` を書き戻すと、別起動が公開した中身を破壊する）。
            mismatch_already_handled = False
            try:
                payload = yaml.safe_dump(
                    shard_map, sort_keys=True, default_flow_style=False, allow_unicode=True
                )
                # E-122: 非空原本ケースは原本を読み戻さず、所有権の唯一の根拠
                # （サイドカー自身）の内容が自分の token のままであることを確認
                # する——`_acquire_m2e_out_reservation` の O_CREAT|O_EXCL が
                # 唯一の書き手を保証しているため、この照合は実質的に常に成立
                # する自己整合性チェックだが、E-85 の「公開直前に所有権を再確認
                # する」精神は absent/0 バイトケースと同じ形で保つ。
                if out_has_nonempty_original:
                    try:
                        current_token_content = out_claim_sidecar.read_text(encoding="utf-8")
                    except OSError:
                        current_token_content = None
                else:
                    try:
                        current_token_content = out_resolved.read_text(encoding="utf-8")
                    except OSError:
                        current_token_content = None
                if current_token_content != shard_map_claim_token:
                    mismatch_already_handled = True
                    spill_path = out_resolved.with_name(
                        f"{out_resolved.name}.spill-{uuid.uuid4().hex[:8]}.yaml"
                    )
                    _atomic_write_text(spill_path, payload)
                    raise SystemExit(
                        f"--out {args.out} の claim が公開直前に別の内容へ差し替わっていた "
                        "（予約時に書いた自分の claim トークンと不一致）; 別起動との競合の "
                        f"疑いがある。地図は失わないよう {spill_path} へ退避した——原因を "
                        "確認してから手動で --out へ配置すること (fail-closed・E-111)"
                    )
                _atomic_write_text(out_resolved, payload)
            except BaseException:
                if not mismatch_already_handled:
                    try:
                        spill_path = out_resolved.with_name(
                            f"{out_resolved.name}.spill-{uuid.uuid4().hex[:8]}.recovery"
                        )
                        _atomic_write_text(
                            spill_path, _m2e_best_effort_spill_payload(shard_map)
                        )
                    except BaseException:
                        pass
                    # E-129（PR #242 第30巡 Codex 是正）: E-122 は「この except に
                    # 来た＝`_atomic_write_text(out_resolved, payload)` が失敗した
                    # ＝`os.replace` は失敗時に元ファイルを変えないので原本は無傷」
                    # と仮定して非空原本ケースのロールバックを省略していたが、これは
                    # 誤り——`KeyboardInterrupt`/`SystemExit` は `os.replace` が実際
                    # には**成功した直後**の任意のバイトコード境界でも配送されうる
                    # ため、「この except に来た」ことは「置換が失敗した」ことを
                    # 意味しない。その場合、非空原本ケースは新 payload が恒久的に
                    # 居座ったまま fail-closed 終了し、**終了状態（失敗）と成果物
                    # 状態（置換成功）が食い違う**——原本は永久に失われる。新 payload
                    # は直上の spill で既に保全済みなので、原本を無条件で atomic
                    # 復元しても情報は失われない（`_rollback_m2e_out_reservation` は
                    # 「実際には書いていなければ元と同じ内容を書き戻すだけ」の
                    # no-op 相当になるので、あらゆる分岐で安全に呼べる）。
                    _rollback_m2e_out_reservation(out_resolved, original_bytes=out_original_bytes)
                raise
            print(f"wrote shard map to {args.out}")
        finally:
            out_claim_sidecar.unlink(missing_ok=True)
        # E-67（PR #242 第5巡 Codex P2 是正）: 生成時刻は地図 bytes から外した
        # （同一入力 → バイト一致という Design Memo AC と矛盾しないように）ので、
        # provenance として stdout へ印字する（HANDOFF のレシピは tee するため
        # dated record に残る）。sha256 は書き出したのと同一の encoded bytes から
        # 導出する（census の E-25 / shard 実行記録の E-56 と同じ流儀）。
        print(f"  generated at {_utc_now()}")
        print(f"  shard map sha256: {hashlib.sha256(payload.encode('utf-8')).hexdigest()}")
        print(f"  n_shards: {shard_map['n_shards']} / n_cells: {shard_map['n_cells']}")
        print(f"  cap_s: {shard_map['inputs']['cap_s']:.3f}")
        return 0
    if args.shard_id is not None:
        for flag, rejected in (
            ("--level", args.level is not None),
            ("--categories", args.categories is not None),
            ("--external-manifest", args.external_manifest is not None),
            ("--external-fixtures", args.external_fixtures is not _ARGPARSE_UNSET),
            ("--specs", args.specs is not _ARGPARSE_UNSET),
            ("--repeat-index", args.repeat_index is not None),
            ("--eval-cell-store", args.eval_cell_store is not None),
            ("--cell-store-role", args.cell_store_role is not _ARGPARSE_UNSET),
            ("--pin-threads", args.pin_threads is True),
            ("--t-direct", args.t_direct is not None),
            ("--t-stem", args.t_stem is not None),
            ("--startup-cost", args.startup_cost is not None),
            # E-100（PR #242 第17巡 Codex 是正）: センチネル方式へ統一（既定値と
            # 同値の明示指定も正しく検出する）。
            ("--session-budget", args.session_budget is not _ARGPARSE_UNSET),
            # E-98（PR #242 第16巡 Codex 是正）: 実行機も M2e 帯（--m2e-bars）を
            # 一切読まない（E-71 ゲートの列挙に元々抜けていた）。
            ("--m2e-bars", args.m2e_bars is not _ARGPARSE_UNSET),
            # E-110（PR #242 第20巡 Codex 是正）: --force は --make-shard-map 専用
            # （地図生成の no-clobber 上書き許可）——実行機は no-clobber を無条件
            # fail-closed で拒否するのみで、上書き許可の概念自体が無い。列挙に
            # 抜けていた。
            ("--force", args.force is True),
        ):
            if rejected:
                raise SystemExit(
                    f"{flag} は --shard-id と併用しない（実行機が読むのは --shard-map / "
                    "--campaign / --cell-store / --workers / --bars / --out だけ; スレッド "
                    "3 点固定は常に適用するため --pin-threads は無効。黙って無視して束縛 "
                    "されたと誤解させない (fail-closed)）"
                )
        if args.shard_map is None:
            raise SystemExit("--shard-id には --shard-map が必須")
        if args.campaign is None:
            raise SystemExit("--shard-id には --campaign が必須")
        if args.cell_store is None:
            raise SystemExit(
                "--shard-id には --cell-store が必須（セル台帳への書き込み先）"
            )
        if args.shard_id < 0:
            raise SystemExit(f"--shard-id {args.shard_id} は 0 以上のみ許可する")
        map_doc, map_sha256 = _load_m2e_shard_map(args.shard_map)
        # E-59（PR #242 第3巡 Codex P2 是正）: 地図は校正時の P を記録している
        # （§8.4「production と同じ P」の契約）。--workers 省略時は地図の値を採用し、
        # 明示指定時は地図の値と一致することを要求する（不一致は fail-closed。
        # T_direct/T_stem/S は校正時の P の下で測ったものであり、異なる P で実行すると
        # コストモデルの前提が崩れる）。
        # E-83（PR #242 第10巡 Codex 是正）: `int(...)` は非 bool の正整数以外
        # （`1.5` は切り捨てて 1 に、`true` は 1 に）を黙って正常値として受理して
        # しまう——地図の workers は校正時の P（§8.4 のコスト契約の前提）なので、
        # 形が崩れた値を静かに丸めず fail-closed で拒否する（生成器
        # `generate_m2e_shard_map` の workers 検証と同じ形状要件）。
        raw_map_workers = map_doc["inputs"]["workers"]
        if (
            isinstance(raw_map_workers, bool)
            or not isinstance(raw_map_workers, int)
            or raw_map_workers < 1
        ):
            raise SystemExit(
                f"shard map: inputs.workers {raw_map_workers!r} は 1 以上の整数（bool 不可）"
                "のみ許可する; §8.4 のコスト契約の前提（校正時の P）を表す値が崩れている "
                "(fail-closed・E-83)"
            )
        map_workers = raw_map_workers
        if args.workers is _ARGPARSE_UNSET:
            workers = map_workers
        else:
            workers = args.workers
            if workers != map_workers:
                raise SystemExit(
                    f"--workers {workers} が地図の校正時 P（{map_workers}）と不一致; "
                    "§8.4「production と同じ P」の契約により、明示指定するなら地図と "
                    "一致する値のみ許可する。省略すれば地図の値を採用する (fail-closed)"
                )
        if workers < 1:
            raise SystemExit(f"--workers {workers} は 1 以上の整数のみ許可する")
        # E-52: campaign_sha256 の照合と parse を単一読取から導出する（別々の
        # read_bytes() だと、呼び出しの間にファイルが差し替わった場合に、地図と
        # 突き合わせた digest と実際に消費する campaign の中身が別 bytes 由来に
        # なりうる）。
        campaign, campaign_sha256 = _load_m2e_campaign_with_sha256(args.campaign)
        if map_doc.get("campaign_sha256") != campaign_sha256:
            raise SystemExit(
                f"--campaign {args.campaign} が地図の campaign_sha256 と不一致; 地図生成時 "
                "と別の campaign を消費しない (fail-closed)"
            )
        # E-47: `--bars`（registry 検証・tolerance/est_voiced_floor の供給元）も保護する。
        protected = {
            Path(args.shard_map).resolve(),
            Path(args.campaign).resolve(),
            Path(args.bars).resolve(),
        }
        for level_paths in campaign.values():
            protected.update(level_paths.values())
        # E-135（PR #242 第33巡 Codex 是正）: run/evaluate/census（既存経路）と
        # 同じ規律で、観測を実際に産む first-party ソース閉包
        # （`_generator_code_paths`）を保護集合へ加える——`execute_m2e_shard` の
        # worker 測定はこのコードを実際に実行して cell record（provenance）を
        # 産む唯一の消費者であり、`--out`/`--cell-store` spill がそれと同じ
        # パスを指すとコード自体を実行記録/spill 出力で上書きしうる
        # （fail-closed で予約前に拒否する）。
        protected.update(_generator_code_paths())
        # E-123（PR #242 第27巡 Codex 是正）: 実行は常に manifest を読む（先行
        # shard 検査・task 構築）ので、manifest が指す audio_path/annotation_path
        # の実体も無条件で保護集合へ展開する（`--make-shard-map` 側と同型——
        # manifest の**ファイルパス**自体は既に保護されているが、manifest が
        # **指す**実ファイルは含まれていなかった）。
        # E-126（PR #242 第29巡 Codex 是正）: 戻り値のスナップショット
        # （`preflight_manifest_by_level`/`preflight_manifest_sha256_by_level`）は
        # 破棄せず、下の `execute_m2e_shard` 呼び出しへそのまま引き渡す——
        # `execute_m2e_shard` 内の既存 `manifest_by_level` 引き回し機構
        # （E-72/E-104）へ種付けすることで、ここで既に読んだ manifest を先行
        # shard 検証・task 構築で再度開かない（E-125 の `--make-shard-map` 側
        # 対応と対になる shard 側の完備化）。E-133（PR #242 第32巡 Codex 是正）:
        # sha256 スナップショットも保持し、除外真実性再スキャン自体（E-104）へも
        # 種付けする——これにより除外つき地図でも manifest 読取が preflight の
        # 1 回へ完全に一本化される。
        referenced_paths, preflight_manifest_by_level, preflight_manifest_sha256_by_level = (
            _m2e_manifest_referenced_paths(campaign)
        )
        protected.update(referenced_paths)
        cell_store_root = Path(args.cell_store).resolve()
        # E-90（PR #242 第12巡 Codex 是正）: E-81 の逆方向——解決済み保護入力
        # （campaign が指す manifest/fixtures・shard-map・bars）が `--cell-store` の
        # root と同一または配下にあると、公開されたセルチェックポイントを「入力」
        # として消費してしまいうる（出力ツリーに入力を置かせない）。
        for protected_path in protected:
            if protected_path == cell_store_root or cell_store_root in protected_path.parents:
                raise SystemExit(
                    f"--cell-store {args.cell_store} 配下に shard 実行の入力 "
                    f"（{protected_path}）がある; 出力ツリー（cell_store）を入力として "
                    "消費しない (fail-closed・E-90)"
                )
        out_resolved = Path(args.out).resolve()
        if out_resolved in protected:
            raise SystemExit(
                f"--out {args.out} は shard 実行の入力（shard-map / campaign / manifest / "
                "fixtures / bars）と同じパスを指している; 入力を実行記録で上書きしない "
                "(fail-closed)"
            )
        if out_resolved == cell_store_root or cell_store_root in out_resolved.parents:
            raise SystemExit(
                f"--out {args.out} が --cell-store {args.cell_store} 配下にある; 実行記録で "
                "セルチェックポイントを上書きしない (fail-closed)"
            )
        # E-51（PR #242 第2巡 Codex P2 是正）: 地図生成と同じ no-clobber 規律を shard
        # 実行記録にも課す。上書きフラグは作らない——dated record は per-run 命名が
        # 前提であり、再試行は別パスの --out を使う。高価なキューに入る**前**に
        # fail-closed で拒否する。
        # E-55（PR #242 第3巡 Codex P1 是正）: `--out "$(mktemp ...)"` は 0 バイトの
        # 予約ファイルを作る。mktemp の予約は上書き対象として許容し、非空の既存
        # レコードのみ拒否する（HANDOFF の起動レシピが軒並み拒否されていた不具合の
        # 是正）。
        if out_resolved.exists() and out_resolved.stat().st_size > 0:
            raise SystemExit(
                f"--out {args.out} が既に存在する（0 バイトではない）; shard 実行記録の "
                "黙示上書き禁止（dated record は per-run 命名が前提——別パスの --out を "
                "使う。mktemp の 0 バイト予約ファイルは上書き対象として許容する）"
                "(fail-closed)"
            )
        # E-85（PR #242 第10巡 Codex 是正）: no-clobber 検査の通過〜公開
        # （`_atomic_write_text` での置換）までの窓が無防備だった——`execute_m2e_shard`
        # は数時間かかりうるため、この窓で別起動が同じ 0 バイト予約 / 不存在 --out を
        # 狙って同じ no-clobber 検査を通過しうる（二重実行・上書き競合）。検査の直後、
        # `--out` へ起動固有 claim（shard_id + PID + ISO8601）を atomic write する——
        # これでファイルは非空になり、以後の別起動は既存の no-clobber 検査（0 バイト
        # のみ許容）で弾かれる。公開直前には現在の `--out` の内容が自分の claim の
        # ままであることを確認してから置換する（不一致なら、誰かが割り込んで claim /
        # 内容を差し替えたということ——実行記録を失わないよう一時パスへ退避出力し、
        # fail-closed で案内する）。
        # E-94（PR #242 第15巡 Codex 是正）: E-85 の claim は `--out` 本体への
        # `os.replace`（atomic だが後勝ち上書き可能）だけが所有権の根拠だった——
        # 2 起動がほぼ同時に到達すると、両方が no-clobber を通過した直後に互いの
        # claim を上書きしうる（検出は公開直前のみ・事後）。真の排他プリミティブへ
        # 切り替える: サイドカー `<out>.claim` を `O_CREAT|O_EXCL` で作る——既に
        # 存在すれば即座に fail-closed で拒否する（他起動が予約を保持している）。
        # `--out` 本体への token 書き込みは引き続き行う（診断用・E-85 の公開時
        # 照合はそのまま維持）が、所有権の根拠はこのサイドカーの O_EXCL に一本化する。
        # E-111: 原状復帰は bool ではなく元の bytes そのもので行う
        # （`_rollback_m2e_out_reservation` 参照。--shard-id 側は E-51 の
        # no-clobber により常に「0 バイト予約」のみが「存在した」場合に相当する）。
        # E-119（PR #242 第25巡 Codex 是正）: 地図生成側と同型の穴——`except OSError`
        # は `PermissionError` も「存在しない」と黙って扱う。`FileNotFoundError`
        # のみを「未存在」として受理し、他の読取エラーは claim 取得前に fail-closed
        # で中断する（--shard-id 側は常に 0 バイト予約のみが対象だが、その 0 バイト
        # ファイル自体が読取不可という同型の壊れ方はありうる）。
        try:
            out_original_bytes = out_resolved.read_bytes()
        except FileNotFoundError:
            out_original_bytes = None
        except OSError as exc:
            raise SystemExit(
                f"--out {args.out} の既存内容を確認できない "
                f"({type(exc).__name__}: {exc}); 原状復帰の基準（既存内容）を立証できない "
                "まま予約を進めない (fail-closed・E-119)"
            ) from exc
        shard_run_claim_token = (
            "m2e-shard-run-reservation/1\n"
            f"shard_id={args.shard_id}\n"
            f"pid={os.getpid()}\n"
            f"claimed_utc={_utc_now()}\n"
        )
        try:
            out_claim_sidecar = _acquire_m2e_out_reservation(out_resolved, shard_run_claim_token)
        except FileExistsError:
            raise SystemExit(
                f"--out {args.out} の予約は他の起動が保持している（サイドカー "
                f"{out_resolved.with_name(f'{out_resolved.name}.claim')} が既に存在する）; "
                "並行実行は非サポート。クラッシュ孤児ならサイドカーを手動削除して再実行する "
                "(fail-closed・E-94)"
            ) from None

        # E-117（PR #242 第24巡 Codex 是正）: claim・payload の公開は入口で resolve
        # 済みの `out_resolved` を使う（`args.out` は使わない）——予約・token 検証・
        # ロールバックは既に `out_resolved` 基準だったが、公開の 2 箇所（claim
        # トークン・最終 payload）だけが未解決の `args.out` のままだった。`--out`
        # の最終要素が symlink だと `_atomic_write_text` の `os.replace` は symlink
        # 自体を置換してしまい（symlink の指す実体へは書かない）、予約が守っていた
        # 実体とは別の場所へ中身が書かれる経路があった（地図生成側と同型の穴）。
        try:
            _atomic_write_text(out_resolved, shard_run_claim_token)
            try:
                shard_run = execute_m2e_shard(
                    map_doc=map_doc,
                    map_sha256=map_sha256,
                    shard_id=args.shard_id,
                    campaign=campaign,
                    cell_store=args.cell_store,
                    bars_path=args.bars,
                    workers=workers,
                    # E-126/E-133: preflight（保護入力検査・E-123）が既に読んだ
                    # manifest スナップショット + sha256 を引き渡す（未指定時は
                    # None・従来どおり内部で読む）——除外真実性再スキャン・先行
                    # shard 検証・task 構築のすべてがこれを消費する。
                    preflight_manifest_by_level=preflight_manifest_by_level,
                    preflight_manifest_sha256_by_level=preflight_manifest_sha256_by_level,
                )
            except BaseException:
                # E-96: execute_m2e_shard が失敗するあらゆる経路（shard claim
                # 衝突・pin 失敗・不正地図等）で --out を原状復帰する。
                _rollback_m2e_out_reservation(out_resolved, original_bytes=out_original_bytes)
                raise
            # E-121（PR #242 第26巡 Codex 是正）: 直列化（`json.dumps`）を含む
            # 「実行完了後〜公開完了まで」の残り全段を、直上の `execute_m2e_shard`
            # と同じ BaseException ロールバック範囲に入れる（E-106 の範囲拡張の
            # さらなる拡張）——以前は直列化とその直後の token 検証がこの範囲の外に
            # あり、直列化自体が失敗すると `--out` に自分の claim トークンだけが
            # 残ったままロールバックされず（サイドカーは `finally` で解放される
            # ので、次回起動は壊れた `--out` を非空の既存レコードとして掴んで
            # しまう）。実行記録（`shard_run` dict）はまだメモリ上に生きているので、
            # `_m2e_best_effort_spill_payload` で可能な限り堅牢な形へ落として spill
            # してからロールバックする（spill 自体の失敗はロールバック・再送出を
            # 妨げない）。「別起動が claim を差し替えていた」分岐（既に spill 済み・
            # fail-closed で案内済み）はロールバック対象外（`out_original_bytes` を
            # 書き戻すと、別起動が公開した中身を破壊する）。
            mismatch_already_handled = False
            try:
                shard_run_payload = json.dumps(shard_run, indent=2, sort_keys=True)
                try:
                    current_out_content = out_resolved.read_text(encoding="utf-8")
                except OSError:
                    current_out_content = None
                if current_out_content != shard_run_claim_token:
                    mismatch_already_handled = True
                    spill_path = out_resolved.with_name(
                        f"{out_resolved.name}.spill-{uuid.uuid4().hex[:8]}.json"
                    )
                    _atomic_write_text(spill_path, shard_run_payload)
                    raise SystemExit(
                        f"--out {args.out} の claim が公開直前に別の内容へ差し替わっていた "
                        "（予約時に書いた自分の claim トークンと不一致）; 別起動との競合の "
                        f"疑いがある。実行記録は失わないよう {spill_path} へ退避した——原因を "
                        "確認してから手動で --out へ配置すること (fail-closed・E-85)"
                    )
                _atomic_write_text(out_resolved, shard_run_payload)
            except BaseException:
                if not mismatch_already_handled:
                    try:
                        spill_path = out_resolved.with_name(
                            f"{out_resolved.name}.spill-{uuid.uuid4().hex[:8]}.recovery"
                        )
                        _atomic_write_text(
                            spill_path, _m2e_best_effort_spill_payload(shard_run)
                        )
                    except BaseException:
                        pass
                    _rollback_m2e_out_reservation(out_resolved, original_bytes=out_original_bytes)
                raise
            print(f"wrote shard run record to {args.out}")
        finally:
            # E-94: 公開の成否によらず、サイドカーは必ず解放する（所有権の唯一の
            # 根拠なので、残したままだと --out パスが以後の全起動から永久に拒否
            # され続ける）。
            out_claim_sidecar.unlink(missing_ok=True)
        # E-56（PR #242 第3巡 Codex P2 是正）: census の E-25 と同じ流儀で、書いたのと
        # 同一の encoded bytes から sha256 を導出して stdout へ残す（HANDOFF のレシピは
        # stdout を tee するため dated record にこの pin が残る）。
        shard_run_sha256 = hashlib.sha256(shard_run_payload.encode("utf-8")).hexdigest()
        print(f"  shard record sha256: {shard_run_sha256}")
        print(
            f"  shard {shard_run['shard_id']}/{shard_run['n_shards']}: "
            f"completed={shard_run['cells_completed']}/{shard_run['cells_total']} "
            f"unavailable={len(shard_run['cells_unavailable'])} "
            f"truncated={len(shard_run['cells_truncated'])} "
            f"not_started={len(shard_run['cells_not_started'])}"
        )
        return 0
    # E-98（PR #242 第16巡 Codex 是正）: --m2e-bars をセンチネル化した（両 shard
    # モードは明示指定を拒否——上の 2 ブロックで検査済み）ので、それ以外の経路
    # （census/evaluate/run）へ渡す前に、既定の M2E_BARS_PATH へ正規化する。
    # census 分岐がこの直後で --m2e-bars を直接消費するため、この正規化は
    # census 分岐より前に置く。
    if args.m2e_bars is _ARGPARSE_UNSET:
        args.m2e_bars = M2E_BARS_PATH
    if args.census and args.evaluate:
        raise SystemExit(
            "--census と --evaluate は排他（census は evaluate が出した verdict を "
            "集める後段であり、同じ起動で両方を行うと「自分が今出した判定を自分で "
            "集計する」ことになる）"
        )
    if args.census:
        # census は run/evaluate のどのフェーズ引数とも組み合わせない。測っていない
        # 次元・使わない資源を名乗らせない規律（`--repeat-index` 等と同型）。
        # census が**実際に読む**のは `--census` / `--out` / `--bars` / `--m2e-bars` の
        # 4 つだけ。それ以外は黙って無視されるのではなく拒否する（PR #241 Codex P2）——
        # 例えば `--external-manifest` を渡した wrapper は「census がその manifest に
        # 束縛される」と信じうるが、成果物はそれを一度も読まない。
        # センチネル化した 4 つ（--specs / --external-fixtures / --workers /
        # --cell-store-role）は「渡されたか」そのものを問う（値比較では既定値の明示
        # 指定を検出できない: PR #241 Codex P2 / E-21）。--pin-threads は store_true
        # で「既定値の明示指定」が構造上不可能なので従来どおり True 判定のまま。
        for flag, rejected in (
            ("--categories", args.categories is not None),
            ("--level", args.level is not None),
            ("--cell-store", args.cell_store is not None),
            ("--eval-cell-store", args.eval_cell_store is not None),
            ("--repeat-index", args.repeat_index is not None),
            ("--external-manifest", args.external_manifest is not None),
            ("--external-fixtures", args.external_fixtures is not _ARGPARSE_UNSET),
            ("--specs", args.specs is not _ARGPARSE_UNSET),
            ("--workers", args.workers is not _ARGPARSE_UNSET),
            ("--pin-threads", args.pin_threads is True),
            ("--cell-store-role", args.cell_store_role is not _ARGPARSE_UNSET),
        ):
            if rejected:
                raise SystemExit(
                    f"{flag} は census phase では無効（census が読むのは --census / "
                    "--out / --bars / --m2e-bars だけで、測定も評価もしない; 黙って "
                    "無視して「その引数に束縛された」と誤解させない。既定値と同じ値を "
                    "渡しても、渡された事実そのものを拒否する）"
                )
        protected = {Path(p).resolve() for p in args.census}
        protected.add(Path(args.m2e_bars).resolve())
        # 基底バーも保護する（PR #241 Codex P2）。census は共有スカラーの供給元として
        # これを**読む**ので、`--out` で潰せてはならない。
        protected.add(Path(args.bars).resolve())
        protected.update(_generator_code_paths())
        if Path(args.out).resolve() in protected:
            raise SystemExit(
                f"--out {args.out} は census の入力（verdict / 帯登録 / provenance 対象の "
                "ソース）と同じパスを指している; 入力を集計結果で上書きしない (fail-closed)"
            )
        census = aggregate_m2e_census(
            [load_verdict(path) for path in args.census],
            bars_path=args.bars,
            m2e_bars_path=args.m2e_bars,
        )
        # 書き出す**直前に** load 時 pin の不変を再確認する（PR #241 Codex P2・
        # run / evaluate と同じ post-execution ガード）。集計中にソースが差し替わると、
        # census は「現 checkout と一致する」と検査したはずのコード pin を名乗りながら、
        # 実際には別 bytes の下で組まれた成果物になる。
        _require_unchanged_since_load()
        payload = json.dumps(census, indent=2, sort_keys=True)
        # E-25: 公開した bytes の pin を、書いたのと同一の snapshot から導出して stdout へ
        # 残す（PR #241 Codex P2）。runbook の流儀では stdout が *_stdout.txt として dated
        # record に保存されるため、後日の改変・部分置換をこの pin で検出できる。文書内へ
        # の自己埋め込みは自己言及になるためしない（sidecar 追加も本段階では過剰）。
        _atomic_write_text(args.out, payload)
        print(f"wrote census to {args.out}")
        print(f"  census sha256: {hashlib.sha256(payload.encode('utf-8')).hexdigest()}")
        print(
            f"  cells: {census['observed_cells_total']}/{census['expected_cells_total']} "
            f"({census['status']})"
        )
        if census["band_verdict"] is None:
            # 揃っていないときに print してよいのは census だけ（§11）。
            for gap in census["missing"]:
                print(f"  missing: {gap['level']} / {gap['arm']}: {gap['reason']}")
        else:
            for arm, result in sorted(census["band_verdict"].items()):
                print(f"  {arm} @ {result['gate_level']}: {result['status']}")
        return 0
    # センチネルを実際の既定値へ戻す（census phase 以外の経路はここから先で従来どおりの
    # 値を見る。センチネルが下流へ漏れる経路を作らない）。census 拒否検査は上のブロック
    # 内でセンチネルのまま行っており、この正規化は他の全検査より前に置く。
    if args.specs is _ARGPARSE_UNSET:
        args.specs = SPECS_PATH
    if args.external_fixtures is _ARGPARSE_UNSET:
        args.external_fixtures = EXTERNAL_FIXTURES_PATH
    if args.workers is _ARGPARSE_UNSET:
        args.workers = 1
    if args.cell_store_role is _ARGPARSE_UNSET:
        args.cell_store_role = _CELL_STORE_ROLE_RUN
    if args.workers < 1:
        raise SystemExit(f"--workers {args.workers} は 1 以上の整数のみ許可する")
    # `--cell-store-role` は run phase のセル書き込みにしか意味が無い。測っていない
    # 次元を名乗らせない規律（`--repeat-index` と同型）。
    if args.cell_store_role != _CELL_STORE_ROLE_RUN:
        if args.evaluate:
            raise SystemExit(
                "--cell-store-role は run phase 専用（evaluate 自身はセルを書かない; "
                "測り直しの子へは評価器が自動で付ける）"
            )
        if args.cell_store is None:
            raise SystemExit(
                "--cell-store-role は --cell-store と併用したときのみ有効"
                "（セルを書かない run に役割を名乗らせない）"
            )
    # store 分離（C2）の関係検査は**フェーズ判定より先に**行う。フェーズ別の拒否
    # （run 専用 / evaluate 専用）を先に通すと、2 つの store が重なった指定が
    # 「フェーズが違う」というだけの理由で弾かれ、重なりそのものは一度も検査されない。
    if args.eval_cell_store is not None:
        eval_store_root = Path(args.eval_cell_store).resolve()
        if args.cell_store is not None:
            # **resolve 後**に比較する（symlink・相対パス・`..` で素通りする形にしない）。
            run_store_root = Path(args.cell_store).resolve()
            if run_store_root == eval_store_root:
                raise SystemExit(
                    f"--eval-cell-store {args.eval_cell_store} が --cell-store "
                    f"{args.cell_store} と同じパス（resolve 後 {eval_store_root}）を指している; "
                    "evaluate が run のチェックポイントから resume できると、測り直しは "
                    "「提出 report を生んだのと同じ測定を自分自身と比較する」だけになり、"
                    "publish の独立性が消える (fail-closed)"
                )
            if run_store_root in eval_store_root.parents:
                raise SystemExit(
                    f"--eval-cell-store {args.eval_cell_store} が --cell-store "
                    f"{args.cell_store} の配下にある; 木が入れ子だと run 用と evaluate 用の "
                    "セルが同じ走査・掃除の対象になり、独立であるべき 2 つの計算が"
                    "互いを汚染しうる (fail-closed)"
                )
            if eval_store_root in run_store_root.parents:
                raise SystemExit(
                    f"--cell-store {args.cell_store} が --eval-cell-store "
                    f"{args.eval_cell_store} の配下にある; 木が入れ子だと run 用と evaluate 用の "
                    "セルが同じ走査・掃除の対象になり、独立であるべき 2 つの計算が"
                    "互いを汚染しうる (fail-closed)"
                )
        if not args.evaluate:
            raise SystemExit(
                "--eval-cell-store は evaluate phase 専用（run phase のチェックポイントは "
                "--cell-store 側。run が evaluate 用 store へ書けると、測り直しが自分の "
                "検証対象と同じセルを読むことになり store 分離の意味が消える）"
            )
        # `--cell-store` 側と同型の保護: verdict がセルチェックポイントを上書きすると、
        # 成功した evaluate が自分の再利用資産を消し、次回は高価な再測定になる。
        out_resolved = Path(args.out).resolve()
        if out_resolved == eval_store_root or eval_store_root in out_resolved.parents:
            raise SystemExit(
                f"--out {args.out} が --eval-cell-store {args.eval_cell_store} 配下にある; "
                "verdict でセルチェックポイントを上書きしない (fail-closed)"
            )
    # D-3: `--pin-threads` の意味はフェーズで役割が分かれる（条件そのものは同一）。
    #   run phase      : このプロセスに 3 点固定を**適用**し、その事実を report に刻む。
    #   evaluate phase : 何も測らないので自プロセスには適用しない。評価対象 report が
    #                    名乗る固定を**契約として検証**し、測り直しの子へ伝えて子 report の
    #                    申告と再照合する（`evaluate_m2_bars` / D-3 の実装ノート）。
    # 評価器で torch を import しないのは、`_require_scorer_compile_observation_covers_
    # imported_modules` などの「素の CLI 実行であること」を問う自己ゲートを、測定に
    # 関係しない import で揺らさないため。
    thread_pinning = _apply_thread_pinning() if (args.pin_threads and not args.evaluate) else None

    if args.evaluate:
        if args.categories:
            raise SystemExit("--categories は run phase 専用（evaluate は report 側の row を評価する）")
        # `--out` が入力（report / bars / specs / 外部素材 manifest+fixtures）を
        # 指していないか **書く前に** 確認する。上書きすると verdict の証拠そのもの
        # （repeat evidence・凍結設定）が消え、report_pins の hash も実体と食い違う
        # （Codex P2 指摘）。
        if args.level is not None:
            raise SystemExit(
                "--level は run phase 専用（evaluate は report が記録した level を読む）"
            )
        if args.cell_store is not None:
            raise SystemExit(
                "--cell-store は run phase 専用（evaluate は report が記録した値を読む）"
            )
        if args.repeat_index is not None:
            raise SystemExit(
                "--repeat-index は run phase 専用（evaluate は report が記録した値を読む）"
            )
        protected = {Path(p).resolve() for p in args.evaluate}
        protected.add(Path(args.bars).resolve())
        protected.add(Path(args.m2e_bars).resolve())
        protected.add(Path(args.specs).resolve())
        protected.add(Path(args.external_fixtures).resolve())
        if args.external_manifest is not None:
            # M2c PR-M2c-1 review（Codex 第 1 巡 P1）: manifest 自体だけでなく、
            # manifest が指す全 member（音声/注釈の解決済みパス）も保護する。
            protected.update(
                _external_manifest_protected_paths(args.external_manifest, args.external_fixtures)
            )
        # provenance のために hash する first-party ソースも保護する。これを許すと
        # 「hash してから同じファイルを JSON で潰す」ことになり、artifact が自分が
        # 記録した bytes を破壊し次回実行も壊れる（Codex P2）。
        protected.update(_generator_code_paths())
        protected.update(_mir_eval_paths())
        protected.update(_runtime_input_paths())
        if Path(args.out).resolve() in protected:
            raise SystemExit(
                f"--out {args.out} は評価入力（report / bars / specs / 外部素材 "
                "manifest+fixtures+member / provenance 対象のソース）と同じパスを指している; "
                "入力を verdict で上書きしない (fail-closed)"
            )
        # 同じ保護を **`--eval-cell-store` の木にも**課す（PR #240 Codex P1）。
        # 上の検査は `--out` という 1 本のパスしか見ないが、測り直しの子は `store_B` 配下へ
        # `cell_<digest>.json` を **atomic replace** で書く。保護入力がその木の中にあると、
        # 名前が偶然一致した入力を子が置き換えうる——チェックポイントの公開が自分の
        # 証拠を壊す経路になる。子を起こす前に、木ごと重なりを拒否する
        # （prospective な出力名を列挙するのではなく、保護入力が木の中に無いことを要求
        # する——出力名の集合はセル鍵の数だけあり、列挙は関所として脆い）。
        if args.eval_cell_store is not None:
            eval_store_root = Path(args.eval_cell_store).resolve()
            inside = sorted(
                str(p) for p in protected
                if p == eval_store_root or eval_store_root in p.parents
            )
            if inside:
                raise SystemExit(
                    f"--eval-cell-store {args.eval_cell_store} の木に評価入力が含まれている "
                    f"（{inside[:3]}{' ほか' if len(inside) > 3 else ''}）; 測り直しの子は "
                    "この木へ cell_<digest>.json を atomic replace で書くため、名前の一致した "
                    "入力を置き換えて自分の証拠を壊しうる (fail-closed)"
                )

        # read → hash → parse を `load_report` の 1 操作にまとめる（read と hash の間に
        # 差し替えが入る TOCTOU を避け、pin と評価対象を束縛する）。pin は
        # `evaluate_m2_bars` が **評価した bytes** から導出するのでここでは組まない。
        reports = [load_report(report_path) for report_path in args.evaluate]
        bars, bars_sha256 = load_bars(args.bars)
        verdict = evaluate_m2_bars(
            reports,
            bars,
            bars_sha256=bars_sha256,
            # `--specs` を転送しないと、カスタム spec + それに対応する pin を持つ bars で
            # 測った report を評価するとき committed 既定 spec を読み直して pin 不一致で
            # 落ちる（= evaluate モードで `--specs` が無効だった。Codex P2）。
            specs_path=args.specs,
            # 事前登録の git 立証（Codex P2 第 28 巡）は供給された bars の実パスに対して行う。
            bars_path=args.bars,
            m2e_bars_path=args.m2e_bars,
            external_manifest_path=args.external_manifest,
            external_fixtures_path=args.external_fixtures,
            eval_cell_store=args.eval_cell_store,
            workers=args.workers,
            pin_threads=args.pin_threads,
        )
        _atomic_write_text(args.out, json.dumps(verdict, indent=2, sort_keys=True))
        print(f"wrote verdict to {args.out}")
        for category, result in verdict["categories"].items():
            print(f"  {category}: {result['status']}")
        return 0

    run_protected = {
        Path(args.bars).resolve(),
        Path(args.m2e_bars).resolve(),
        Path(args.specs).resolve(),
        Path(args.external_fixtures).resolve(),
    }
    if args.external_manifest is not None:
        run_protected.update(
            _external_manifest_protected_paths(args.external_manifest, args.external_fixtures)
        )
    run_protected.update(_generator_code_paths())
    run_protected.update(_mir_eval_paths())
    run_protected.update(_runtime_input_paths())
    if Path(args.out).resolve() in run_protected:
        raise SystemExit(
            f"--out {args.out} は凍結入力（bars / specs / 外部素材 manifest+fixtures+member）"
            "または provenance 対象のソースと同じパスを指している; これらを run report で "
            "上書きしない (fail-closed)"
        )
    # セルストアの木も保護する。`--out` が `--cell-store` 配下を指していると、run は
    # そこにあるチェックポイントを resume に使った上で、最後の `_atomic_write_text` が
    # 同じパスを run report で**置き換える**——成功した run が自分の再利用資産を消し、
    # 次回は高価な再測定になる（セル 1 つ = crepe 推論 1 回）。
    if args.cell_store is not None:
        cell_store_root = Path(args.cell_store).resolve()
        out_resolved = Path(args.out).resolve()
        if out_resolved == cell_store_root or cell_store_root in out_resolved.parents:
            raise SystemExit(
                f"--out {args.out} が --cell-store {args.cell_store} 配下にある; "
                "run report でセルチェックポイントを上書きしない (fail-closed)"
            )
    run_kwargs: Dict[str, Any] = {}
    if args.categories:
        run_kwargs["categories"] = tuple(args.categories)
    elif args.external_manifest is not None:
        # M2c PR-M2c-1（Codex 第 4 巡 P2）: `--categories` 省略時の既定は「事前登録
        # された全カテゴリ」という help の意味論どおりにする。`--external-manifest` が
        # 供給されていれば V_direct の実行可能条件（manifest 必須）を満たすため、
        # 省略時の既定集合にも含める。manifest 無しの省略時は従来どおり
        # `run_accuracy` 自身の既定（S_direct/S_fullstack のみ）に委ねる——V_direct は
        # manifest 必須で fail-closed（`run_accuracy` の要件チェック）のため、manifest
        # が無い状態で既定に含めると省略呼び出しが即座に落ちてしまう。
        #
        # M2e（設計 §5.2）: 既定集合は **`m2_accuracy_bars.yaml` が所有するカテゴリ**に
        # 限る。M2e カテゴリ（別ファイル所有・水準軸あり・別 manifest）を暗黙の既定へ
        # 混ぜると、既存の M2c 流儀の呼び出し（`--external-manifest` のみ）が `--level`
        # 未指定で即座に落ちる。M2e は `--categories` で明示的に選ぶ。
        run_kwargs["categories"] = _categories_owned_by("m2_accuracy_bars.yaml")
    if args.external_manifest is not None:
        run_kwargs["external_manifest_path"] = args.external_manifest
    run_kwargs["external_fixtures_path"] = args.external_fixtures
    # 設計 §8.7: --repeat-index は --cell-store 指定時のみ必須・かつそのときのみ
    # 許可する（どちらか片方だけの指定は「測っていない次元を report に名乗らせない」
    # という repo 全体の規律に反するので CLI レベルで早期に落とす。`run_accuracy`
    # 自身も同じ検査を ValueError で持つ——直接呼び出すテスト経路のための多重防御）。
    if args.cell_store is not None and args.repeat_index is None:
        raise SystemExit(
            "--repeat-index は --cell-store 指定時に必須（セル鍵 "
            "(category, level, entry_id, repeat_index) の repeat_index を欠いたまま "
            "チェックポイントを書かない）"
        )
    if args.cell_store is None and args.repeat_index is not None:
        raise SystemExit(
            "--repeat-index は --cell-store と併用したときのみ有効（測っていない次元を "
            "report に名乗らせない）"
        )
    if args.repeat_index is not None and args.repeat_index < 0:
        raise SystemExit(f"--repeat-index {args.repeat_index} は 0 以上の整数のみ許可する")
    if args.cell_store is not None:
        run_kwargs["cell_store"] = args.cell_store
        run_kwargs["repeat_index"] = args.repeat_index
    result = run_accuracy(
        specs_path=args.specs,
        bars_path=args.bars,
        m2e_bars_path=args.m2e_bars,
        level=args.level,
        workers=args.workers,
        thread_pinning=thread_pinning,
        cell_store_role=args.cell_store_role,
        **run_kwargs,
    )
    _atomic_write_text(args.out, json.dumps(result, indent=2, sort_keys=True))
    print(f"wrote run report to {args.out}")
    for category, row in result["categories"].items():
        print(f"  {category}: {row['outcome']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
