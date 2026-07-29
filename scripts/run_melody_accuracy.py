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
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import uuid
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

import soundfile as sf  # noqa: E402
import yaml  # noqa: E402
from build_melody_bench import build_signal  # noqa: E402

from svp_rpe.arrange.pathsafe import PathConfinementError, resolve_confined  # noqa: E402
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

_EXPECTED_BARS_SCHEMA = "m2-accuracy-bars/0.1"
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
}

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
    },
    "S_fullstack": {
        "kind": "fullstack",
        "composite_id": "m2_s_fullstack_mix",
        "input_kind": "full_mix",
        "route_name": "demucs_vocals_then_crepe",
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
    },
}

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


def load_bars(path: Path = BARS_PATH) -> Tuple[BarsArtifact, str]:
    """m2_accuracy_bars.yaml を single read で (BarsArtifact, sha256) として返す。

    read → hash → parse を 1 操作にまとめ、その 3 つを `BarsArtifact` に束ねる
    （digest と parsed data が切り離されないようにする。Codex P2）。
    """
    data = Path(path).read_bytes()
    bars = _yaml_load_no_dup_keys(data, what="m2_accuracy_bars.yaml")
    version = bars.get("schema_version")
    if version != _EXPECTED_BARS_SCHEMA:
        raise ValueError(
            f"unsupported m2_accuracy_bars schema_version {version!r}; "
            f"expected {_EXPECTED_BARS_SCHEMA} (fail-closed)"
        )
    if "m2_accuracy_bars" not in bars:
        raise ValueError("m2_accuracy_bars.yaml is missing the 'm2_accuracy_bars' block")
    # 「実測前に凍結した」という主張の土台なので、閾値そのものより前に登録日を検証する。
    _require_dated_registration(bars)
    _require_well_formed_bars(bars["m2_accuracy_bars"])
    sha256 = hashlib.sha256(data).hexdigest()
    return BarsArtifact(bars, sha256, data), sha256


def _require_valid_external_fixture_entry(clip_id: Any, entry: Any, *, where: str) -> None:
    """`m2c_external_fixtures.yaml` の 1 fixture entry を検証する（fail-closed）。"""
    if not isinstance(clip_id, str) or not clip_id:
        raise ValueError(f"{where}: clip id {clip_id!r} が非空文字列でない (fail-closed)")
    if not isinstance(entry, dict):
        raise ValueError(f"{where}: fixtures[{clip_id!r}] が mapping でない (fail-closed)")
    for key in ("expected_audio_sha256", "expected_annotation_sha256"):
        value = entry.get(key)
        if not _is_sha256(value):
            raise ValueError(
                f"{where}: fixtures[{clip_id!r}].{key} {value!r} が真の sha256（64 桁 "
                "lowercase hex）でない (fail-closed)"
            )


def load_external_fixtures(path: Path = EXTERNAL_FIXTURES_PATH) -> Tuple[Dict[str, Any], str]:
    """`m2c_external_fixtures.yaml` を single read で (parsed dict, sha256) として返す。

    `load_bars` / `load_specs` と同じ read → hash → parse の単一操作規律。M2c-1 時点
    では `fixtures` が空 dict でも正当（実データは M2c-2 で追記登録する）——空自体は
    ここでは拒否せず、V_direct を要求する run/evaluate 側が「登録済み clip が無い」
    ことを fail-closed で検出する。
    """
    data = Path(path).read_bytes()
    fixtures_doc = _yaml_load_no_dup_keys(data, what="m2c_external_fixtures.yaml")
    version = fixtures_doc.get("schema_version")
    if version != _EXPECTED_EXTERNAL_FIXTURES_SCHEMA:
        raise ValueError(
            f"unsupported m2c_external_fixtures schema_version {version!r}; "
            f"expected {_EXPECTED_EXTERNAL_FIXTURES_SCHEMA!r} (fail-closed)"
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
    return fixtures_doc, sha256


# バー閾値の値域。`min_*` は下限、`max_*` は上限として judge 側で使う。
_BAR_THRESHOLD_RANGES: Dict[str, Tuple[float, float]] = {
    "min_rpa": (0.0, 1.0),
    "max_vfa": (0.0, 1.0),
    "max_octave_gap": (-1.0, 1.0),
}


def _require_well_formed_bars(bar_block: Dict[str, Any]) -> None:
    """凍結バー自身の型・有限性・定義域を検証する（fail-closed）。

    metrics 側の NaN は塞いだが、**バー側**にも同じ穴がある: `min_rpa: .nan` を
    書いた bars を `--bars` で渡すと `raw_pitch_accuracy < NaN` が常に False になり、
    「未定義のバー」の下で pass が publish できてしまう（Codex P1）。閾値は判定の
    基準そのものなので、読み込み時点で弾く。
    """
    import math

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

    # 受け入れゲートを持つべきカテゴリのバーが空/欠落だと、`evaluate_m2_bars` の
    # 「バーなし → diagnostic_only」分岐に落ちて RPA/VFA 判定が黙って消える。
    # 事前登録されたカテゴリのうち診断専用でないものは、閾値の存在を要求する。
    for category in sorted(_CATEGORY_SPECS):
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

    for category, bar in bar_block.items():
        if not isinstance(bar, dict):
            continue
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
        if clip_id in seen_ids:
            raise ValueError(
                f"external manifest: duplicate clip id {clip_id!r} (fail-closed)"
            )
        seen_ids.add(clip_id)
    return entries, manifest_sha256, manifest_path


def _resolve_external_member_path(manifest_dir: Path, value: str, *, what: str) -> Path:
    """manifest entry のパスを manifest 位置基準で解決する（既存 pathsafe 流儀）。

    `svp_rpe.arrange.pathsafe.resolve_confined` を使い、絶対パス・ディレクトリ脱出
    （`..` によるものだけでなくシンボリックリンク経由の脱出も含む）を fail-closed で
    拒否する。
    """
    try:
        return resolve_confined(value, manifest_dir)
    except PathConfinementError as exc:
        raise ValueError(
            f"external manifest: {what} {value!r} を manifest 位置基準で解決できない "
            f"（{exc.reason}）; manifest ディレクトリ外を指すパスは許容しない "
            "(fail-closed)"
        ) from exc


def _parse_external_annotation_csv(raw: bytes, *, clip_id: str) -> Tuple[Tuple[float, ...], Tuple[float, ...]]:
    """外部注釈 CSV を (times_sec, freqs_hz) へ変換する（ネイティブタイムラインのまま）。

    1 列目 time_sec・2 列目 frequency_hz（3 列目以降は無視）。周波数が非有限または
    0 以下（無声を表す一般的な慣例: 0 / 負値）のフレームは無声 = 0.0 へ正規化する。
    10ms へのリサンプルは行わない——設計 §2 追記（M2c）: 「外部注釈はネイティブ
    タイムラインのまま評価する（mir_eval が est を ref 基準へ整列。リサンプル補間と
    いう新たな pin 対象を作らない。10ms 規約は合成正解の導出形式）」。
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
            f"external annotation for clip {clip_id!r}: time_sec に非有限値がある "
            "(fail-closed)"
        )
    normalized_freqs = np.where(np.isfinite(freqs) & (freqs > 0.0), freqs, 0.0)
    return (
        tuple(float(t) for t in times),
        tuple(float(f) for f in normalized_freqs),
    )


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
) -> Dict[str, Any]:
    """1 clip の外部素材を測り、per-clip row（設計 Memo M2c）を返す。"""
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

    row: Dict[str, Any] = {
        "clip_id": clip_id,
        "audio_sha256": audio_sha256,
        "annotation_sha256": annotation_sha256,
        "ref_frame_count": len(ref_times),
        "ref_voiced_frame_count": sum(1 for f in ref_freqs if f > 0.0),
    }

    suffix = audio_path.suffix or ".wav"
    frozen_wav_path = tmp_dir / f"{clip_id}{suffix}"
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
        try:
            observation, route_provenance = runner(str(frozen_wav_path), route)
        except LearnedModelUnavailable as exc:
            row["outcome"] = "unavailable"
            row["detail"] = str(exc).splitlines()[0]
            return row
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
    row["source_model"] = observation.source_model
    for key, value in route_provenance.items():
        row[f"provenance_{key}"] = value
    return row


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
) -> Dict[str, Any]:
    """カテゴリ V（外部素材）1 本の run report row を作る（設計 Memo M2c）。"""
    fixtures_doc, fixtures_sha256 = load_external_fixtures(external_fixtures_path)
    fixtures = fixtures_doc["fixtures"]
    if not fixtures:
        raise ValueError(
            f"run_accuracy: category {category!r} を要求したが "
            f"{external_fixtures_path} の fixtures が空; 事前登録済み clip なしに "
            "外部素材カテゴリを測らない (fail-closed)"
        )

    entries, manifest_sha256, manifest_path = _load_external_manifest(external_manifest_path)
    manifest_dir = manifest_path.parent

    clip_rows: List[Dict[str, Any]] = []
    for entry in sorted(entries, key=lambda e: e["id"]):
        clip_row = _build_external_clip_row(
            entry["id"],
            entry,
            manifest_dir=manifest_dir,
            fixtures=fixtures,
            tolerance_cents=tolerance_cents,
            est_voiced_floor=est_voiced_floor,
            route=route,
            runner=runner,
            tmp_dir=tmp_dir,
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
    preprocessing_values = {json.dumps(c.get("provenance_preprocessing"), sort_keys=True) for c in clip_rows}
    if len(preprocessing_values) > 1:
        raise RuntimeError(
            f"run_accuracy: category {category!r} の clips が provenance_preprocessing で "
            "不一致 (fail-closed)"
        )
    if "provenance_preprocessing" in clip_rows[0]:
        row["provenance_preprocessing"] = clip_rows[0]["provenance_preprocessing"]
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


# ---------------------------------------------------------------------------
# run phase
# ---------------------------------------------------------------------------


def run_accuracy(
    *,
    categories: "tuple[str, ...]" = ("S_direct", "S_fullstack"),
    route_runner: Optional[RouteRunner] = None,
    specs_path: Path = SPECS_PATH,
    bars_path: Path = BARS_PATH,
    tolerance_cents: Optional[float] = None,
    external_manifest_path: Optional[Path] = None,
    external_fixtures_path: Path = EXTERNAL_FIXTURES_PATH,
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
    """
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
    preprocessing = row.get("provenance_preprocessing")
    if isinstance(preprocessing, dict):
        separation: Tuple[Any, ...] = (
            preprocessing.get("preprocessing"),
            preprocessing.get("separation_model"),
            preprocessing.get("separation_version"),
            preprocessing.get("separation_weights_sha256"),
            preprocessing.get("stem_sha256"),
            preprocessing.get("separation_code_sha256"),
        )
    else:
        separation = (preprocessing, None, None, None, None, None)
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
            for key in (
                "separation_weights_sha256",
                "separation_code_sha256",
                "stem_sha256",
            ):
                if not _is_sha256(preprocessing.get(key)):
                    raise ValueError(
                        f"evaluate_m2_bars: category {category!r} rows[{idx}] の "
                        f"preprocessing.{key} {preprocessing.get(key)!r} が真の sha256 でない; "
                        "分離器・分離出力が未 pin の row を証拠にしない (fail-closed)"
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
            preprocessing = row.get("provenance_preprocessing") or {}
            actual_pairs.extend(
                [
                    ("separation_code_sha256", preprocessing.get("separation_code_sha256")),
                    (
                        "separation_weights_sha256",
                        preprocessing.get("separation_weights_sha256"),
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
) -> None:
    """`category` の kind に応じて S（specs 由来合成）/ V（外部素材、M2c）の測り直しへ振り分ける。

    S カテゴリの挙動・シグネチャは変更しない。M2c で追加した外部素材カテゴリは
    `_reverify_external_category_measurement` へ委譲する（`--external-manifest` が
    評価に渡されていなければ fail-closed）。
    """
    category_spec = _CATEGORY_SPECS[category]
    if category_spec["kind"] == "external":
        _reverify_external_category_measurement(
            category,
            rows,
            repeats=repeats,
            verification_runner=verification_runner,
            external_manifest_path=external_manifest_path,
            external_fixtures_path=external_fixtures_path,
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
    expected_specs_sha256: str,
) -> Dict[str, Any]:
    """外部素材カテゴリ（M2c）の測り直し 1 回分を新規プロセス（素の CLI run）で実行する。

    `_run_verification_in_fresh_process`（S カテゴリ）と同型。manifest は評価器の
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
    ]
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPYCACHEPREFIX"] = str(tmp_dir / f"pyc-fresh-ext-{index}")
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
    repeats: int,
    verification_runner: Optional[RouteRunner],
    external_manifest_path: Optional[Path],
    external_fixtures_path: Path,
) -> None:
    """外部素材カテゴリ（M2c）を評価器自身が `repeats` 回独立に測り直す。

    `_reverify_direct_or_fullstack_category_measurement` と同じ「評価器自身の測り
    直しとの bit 一致を publish 条件にする」設計だが、比較対象は `row["clips"]`
    （per-clip 全体）——averaged `row["metrics"]` だけの比較では、平均化で相殺される
    clip 単位の乖離を見逃す（設計 Memo M2c の repeats bit 一致要件）。

    M2c-1 時点では `m2c_external_fixtures.yaml` の `fixtures` が空のため、V_direct を
    含む run は `_run_external_category` の fail-closed（登録済み clip なし）で本関数
    に到達する前に落ちる——実データは M2c-2 で登録する。
    """
    if repeats < 2:
        raise ValueError(
            f"_reverify_external_category_measurement: repeats {repeats!r} が 2 未満; "
            "決定論確認は n>=2 の独立実行を要件とする (fail-closed)"
        )
    if external_manifest_path is None:
        raise RuntimeError(
            f"evaluate_m2_bars: category {category!r} は外部素材カテゴリだが "
            "--external-manifest が評価に渡されていない; 測り直しによる検証なしで "
            "report の metrics を publish しない (fail-closed)"
        )
    specs, specs_sha256 = load_specs(SPECS_PATH)
    verification_rows: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="m2c-reverify-") as tmp:
        for index in range(repeats):
            if verification_runner is not None:
                verification = run_accuracy(
                    categories=(category,),
                    route_runner=verification_runner,
                    external_manifest_path=external_manifest_path,
                    external_fixtures_path=external_fixtures_path,
                )
                vrow = verification["categories"][category]
            else:
                vrow = _run_external_verification_in_fresh_process(
                    category,
                    index,
                    tmp_dir=Path(tmp),
                    external_manifest_path=external_manifest_path,
                    expected_specs_sha256=specs_sha256,
                )
            if vrow.get("outcome") != "measured":
                raise RuntimeError(
                    f"evaluate_m2_bars: category {category!r} を評価環境で再実行できない "
                    f"（outcome={vrow.get('outcome')!r}: {vrow.get('detail', '')}）; 測り直しに "
                    "よる検証なしで report の metrics を publish しない (fail-closed)"
                )
            verification_rows.append(vrow)
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


def evaluate_m2_bars(
    reports: "List[ReportArtifact]",
    bars: BarsArtifact,
    *,
    bars_sha256: str,
    specs_path: Path = SPECS_PATH,
    bars_path: Path = BARS_PATH,
    external_manifest_path: Optional[Path] = None,
    external_fixtures_path: Path = EXTERNAL_FIXTURES_PATH,
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
        if started > recorded:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] の started_utc "
                f"{report.get('started_utc')!r} が recorded_utc "
                f"{report.get('recorded_utc')!r} より後; 開始が完了より後の測定記録は "
                "成立しない (fail-closed)"
            )
        if started < latest_registration:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] の started_utc "
                f"{report.get('started_utc')!r} が、適用するバーの最新登録時点 "
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
    # カテゴリ構成に関わらず無条件にロードする（specs と対称・軽量）。
    external_fixtures_data, external_fixtures_sha256 = load_external_fixtures(
        external_fixtures_path
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
        "categories": {},
    }
    verdict["report_pins"] = report_pins

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
        )

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
        cat_result["status"] = "pass" if not failures else "fail"
        cat_result["failures"] = failures
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
    parser.add_argument("--bars", type=Path, default=BARS_PATH)
    parser.add_argument("--specs", type=Path, default=SPECS_PATH)
    parser.add_argument(
        "--categories",
        nargs="+",
        metavar="CATEGORY",
        help="run phase で測るカテゴリの部分集合（既定: 事前登録された全カテゴリ。"
        "evaluate の測り直しプロセスが 1 カテゴリ run に使う）",
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
    args = parser.parse_args()
    _require_out_outside_git_metadata(args.out)

    if args.evaluate:
        if args.categories:
            raise SystemExit("--categories は run phase 専用（evaluate は report 側の row を評価する）")
        # `--out` が入力（report / bars / specs / 外部素材 manifest）を指していないか
        # **書く前に** 確認する。上書きすると verdict の証拠そのもの（repeat
        # evidence・凍結設定）が消え、report_pins の hash も実体と食い違う（Codex P2 指摘）。
        protected = {Path(p).resolve() for p in args.evaluate}
        protected.add(Path(args.bars).resolve())
        protected.add(Path(args.specs).resolve())
        if args.external_manifest is not None:
            protected.add(Path(args.external_manifest).resolve())
        # provenance のために hash する first-party ソースも保護する。これを許すと
        # 「hash してから同じファイルを JSON で潰す」ことになり、artifact が自分が
        # 記録した bytes を破壊し次回実行も壊れる（Codex P2）。
        protected.update(_generator_code_paths())
        protected.update(_mir_eval_paths())
        protected.update(_runtime_input_paths())
        if Path(args.out).resolve() in protected:
            raise SystemExit(
                f"--out {args.out} は評価入力（report / bars / specs / 外部素材 manifest / "
                "provenance 対象のソース）と同じパスを指している; 入力を verdict で上書き "
                "しない (fail-closed)"
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
            external_manifest_path=args.external_manifest,
        )
        _atomic_write_text(args.out, json.dumps(verdict, indent=2, sort_keys=True))
        print(f"wrote verdict to {args.out}")
        for category, result in verdict["categories"].items():
            print(f"  {category}: {result['status']}")
        return 0

    run_protected = {Path(args.bars).resolve(), Path(args.specs).resolve()}
    if args.external_manifest is not None:
        run_protected.add(Path(args.external_manifest).resolve())
    run_protected.update(_generator_code_paths())
    run_protected.update(_mir_eval_paths())
    run_protected.update(_runtime_input_paths())
    if Path(args.out).resolve() in run_protected:
        raise SystemExit(
            f"--out {args.out} は凍結入力（bars / specs / 外部素材 manifest）または "
            "provenance 対象のソースと同じパスを指している; これらを run report で上書き "
            "しない (fail-closed)"
        )
    run_kwargs: Dict[str, Any] = {}
    if args.categories:
        run_kwargs["categories"] = tuple(args.categories)
    if args.external_manifest is not None:
        run_kwargs["external_manifest_path"] = args.external_manifest
    result = run_accuracy(specs_path=args.specs, bars_path=args.bars, **run_kwargs)
    _atomic_write_text(args.out, json.dumps(result, indent=2, sort_keys=True))
    print(f"wrote run report to {args.out}")
    for category, row in result["categories"].items():
        print(f"  {category}: {row['outcome']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
