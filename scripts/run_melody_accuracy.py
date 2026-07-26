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


def _require_unchanged_since_load() -> None:
    """ロード時に pin したコード（first-party 閉包・スコアラー）の不変を要求する。"""
    current = _generator_code_sha256()
    if current != _LOADED_GENERATOR_CODE_SHA256:
        raise RuntimeError(
            f"first-party ソースが実行中に変化した（load 時 {_LOADED_GENERATOR_CODE_SHA256!r} "
            f"→ 現在 {current!r}）; 走っているのは import 済みの旧コードなので、この run の "
            "provenance は信用できない — プロセスを再起動して測り直すこと (fail-closed)"
        )
    # キャッシュは (size, mtime_ns) を鍵にするので、それらを保ったまま差し替えられた
    # bytes を見逃す。実行後の検証は必ず再 hash する（Codex P1）。
    current_scorer = _scorer_pins(use_cache=False)
    if current_scorer != _LOADED_SCORER_PINS:
        raise RuntimeError(
            f"mir_eval が実行中に差し替わった（load 時 {_LOADED_SCORER_PINS!r} → 現在 "
            f"{current_scorer!r}）; 指標を産んだのは import 済みの旧スコアラーなので、"
            "この run の pin は測定を代表しない — プロセスを再起動して測り直すこと "
            "(fail-closed)"
        )


from svp_rpe.melody.provenance import bind_inference_code_pins, package_code_sha256  # noqa: E402

# 推論コードの pin を本モジュールが soundfile/build_melody_bench を import するより
# 前に確定する（run_melody_observability.py と同じ理由・#217）。
bind_inference_code_pins()

# スコアラー（mir_eval）の pin も **実際に import される前に** 確定させる。
# `_scorer_pins()` は importlib.metadata と find_spec だけを使うので import を
# 起こさない。first-party 閉包と同じ load-time 束縛を third-party にも適用する。
def _scorer_pins(*, use_cache: bool = True) -> Dict[str, Any]:
    """指標を計算した mir_eval（third-party スコアラー）の version / code pin。

    `generator_code_sha256` は first-party 閉包に限っている（third-party を混ぜると
    環境差で digest が揺れる）ため、mir_eval の実装差はそこに現れない。一方
    `mir_eval>=0.7` は上限が無く、別リリースで測った row を同一 stack の repeats と
    数えれば「別の指標実装の出力」を再現性の証拠にしてしまう（Codex P1）。
    そこで row ではなく report レベルで、実際に呼んだスコアラーを pin する。

    **import を起こさずに** 取る: version は `importlib.metadata`（配布メタデータを
    読むだけ）、コード hash は `package_code_sha256`（find_spec で場所だけ解決）。
    `import mir_eval` してから hash すると、先に読み込まれていた旧モジュールが実行
    される一方で hash は新しいディスクを見る窓が開く——first-party 閉包と同じ #217 の
    規律を third-party スコアラーにも適用する（Codex P1）。
    """
    import importlib.metadata

    try:
        version = importlib.metadata.version("mir_eval")
    except importlib.metadata.PackageNotFoundError:
        version = None
    return {
        "mir_eval_version": version,
        "mir_eval_code_sha256": package_code_sha256("mir_eval", use_cache=use_cache),
    }


_LOADED_SCORER_PINS = _scorer_pins()

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import yaml  # noqa: E402
from build_melody_bench import build_signal  # noqa: E402

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

def _mir_eval_paths() -> List[Path]:
    """provenance のために hash する mir_eval のファイル群（`--out` 保護用）。"""
    import importlib.util

    try:
        spec = importlib.util.find_spec("mir_eval")
    except (ImportError, ValueError, AttributeError, ModuleNotFoundError):
        return []
    if spec is None or spec.origin in (None, "built-in", "frozen"):
        return []
    root = Path(spec.origin).resolve().parent
    return sorted(p.resolve() for p in root.rglob("*.py"))


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
    import importlib.metadata

    try:
        dist = importlib.metadata.distribution("mir_eval")
        for record in dist.files or ():
            located = Path(str(dist.locate_file(record)))
            if located.is_file():
                paths.add(located.resolve())
    except Exception:
        pass
    return paths


SPECS_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "m2_accuracy_specs.yaml"
BARS_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "m2_accuracy_bars.yaml"

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
    return specs, hashlib.sha256(data).hexdigest()


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


def _select_named_route(input_kind: str, route_name: str) -> MelodyRoute:
    for route in select_routes(input_kind):
        if route.name == route_name:
            return route
    raise ValueError(
        f"route {route_name!r} not found among select_routes({input_kind!r}) candidates; "
        "melody/routing.py の経路表が drift した可能性がある (fail-closed)"
    )


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
    """
    from svp_rpe.melody.provenance import (
        SEPARATION_CODE_PACKAGES,
        extractor_code_packages_for,
    )

    names = {"mir_eval"}
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
) -> Dict[str, Any]:
    """カテゴリ S（合成正解つき）の精度 run を実行し report dict を返す。

    `route_runner` は抽出器非依存インターフェース: ``(audio_path, route) ->
    (MelodyObservation, provenance_dict)``。既定は
    `svp_rpe.melody.extractors.observe_via_route_with_provenance`（実抽出器。
    crepe 未導入なら `LearnedModelUnavailable` を投げ、その route は
    ``outcome="unavailable"`` として記録される・実行時 DL 禁止・fail-closed）。
    テストはこれをフェイク抽出器（決定論の f0 を返す）に差し替えて run/evaluate
    の二相メカニズムだけを検証する。

    未知の `categories` 値は `_CATEGORY_SPECS` に無ければ fail-fast。
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
        "categories": {},
    }
    results.update(_LOADED_SCORER_PINS)

    with tempfile.TemporaryDirectory(prefix="melody-accuracy-") as tmp:
        for category in categories:
            category_spec = _CATEGORY_SPECS[category]
            y, sr, waveform_sha256 = _build_category_waveform(category, category_spec, specs, bars)
            wav_path = Path(tmp) / f"{category}.wav"
            sf.write(wav_path, y, sr, subtype="FLOAT")
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

    # 実行中にディスク上の first-party ソースが差し替わっていないか確認する。
    # 差し替わっていれば「report が pin した digest」と「次回 import されるコード」が
    # 食い違い、後続の evaluate が誤った provenance を受理しうる（Codex P1）。
    _require_unchanged_since_load()
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
        proc = subprocess.run(
            ["git", "-C", str(ROOT), *args], capture_output=True, input=stdin
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


def _require_fresh_process_report_provenance(report: Dict[str, Any], category: str) -> None:
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
    generator = report.get("generator_code_sha256")
    if generator != _LOADED_GENERATOR_CODE_SHA256:
        raise RuntimeError(
            f"category {category!r} の測り直し report の generator_code_sha256 "
            f"{generator!r} が評価器の {_LOADED_GENERATOR_CODE_SHA256!r} と不一致; "
            "測り直し中に first-party コードが変わっている (fail-closed)"
        )
    scorer = _scorer_pins(use_cache=False)
    reported_scorer = (report.get("mir_eval_version"), report.get("mir_eval_code_sha256"))
    expected_scorer = (scorer["mir_eval_version"], scorer["mir_eval_code_sha256"])
    if reported_scorer != expected_scorer:
        raise RuntimeError(
            f"category {category!r} の測り直し report のスコアラー pin {reported_scorer!r} "
            f"が評価環境の {expected_scorer!r} と不一致; 測り直し中に mir_eval が変わって "
            "いる (fail-closed)"
        )


def _run_verification_in_fresh_process(
    category: str,
    index: int,
    *,
    tmp_dir: Path,
    specs_path: Path,
    bars_path: Path,
) -> Dict[str, Any]:
    """測り直し 1 回分を新規プロセス（素の CLI run）で実行し、その category row を返す。

    プロセス境界により各 repeat は import・重みロード・モデル初期化から独立に行われ、
    相互 bit 一致は「run 間決定論」の実証になる（Codex P2 第 24 巡）。子プロセスは
    素の CLI なので preload ゲート群も自然に通る。失敗（非ゼロ exit / report 欠落）は
    「再実行できない環境」として fail-closed。
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
    proc = subprocess.run(command, capture_output=True, text=True)
    if proc.returncode != 0 or not report_path.is_file():
        tail = " / ".join((proc.stderr or "").strip().splitlines()[-3:])
        raise RuntimeError(
            f"evaluate_m2_bars: category {category!r} の測り直しプロセスが失敗した "
            f"(exit={proc.returncode}: {tail}); 評価環境で再実行できないため publish "
            "しない (fail-closed)"
        )
    verification = load_report(report_path).data
    # metrics だけ取り出して report を捨てない: report レベルの provenance
    # （素の CLI・現行 first-party コード・現行スコアラー）をここで検証する
    # （Codex P2 第 27 巡）。
    _require_fresh_process_report_provenance(verification, category)
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
    specs_path: Path,
    repeats: int,
    verification_runner: Optional[RouteRunner] = None,
) -> None:
    """評価器自身が同じ凍結 fixture を **`repeats` 回独立に測り直し**、bit 一致を要求する。

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
    with tempfile.TemporaryDirectory(prefix="m2-reverify-") as tmp:
        bars_path = Path(tmp) / "m2_accuracy_bars.yaml"
        bars_path.write_bytes(bars.raw)
        for index in range(repeats):
            if verification_runner is not None:
                verification = run_accuracy(
                    categories=(category,),
                    route_runner=verification_runner,
                    specs_path=specs_path,
                    bars_path=bars_path,
                )
                vrow = verification["categories"][category]
            else:
                vrow = _run_verification_in_fresh_process(
                    category,
                    index,
                    tmp_dir=Path(tmp),
                    specs_path=specs_path,
                    bars_path=bars_path,
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


def _require_homogeneous_scorer(reports: List[Dict[str, Any]]) -> Dict[str, Any]:
    """全 report が同一の mir_eval スコアラーで測られたことを要求する（fail-closed）。

    `mir_eval>=0.7` は上限が無く、別リリースは RPA/RCA/VR/VFA の定義や境界処理が
    変わりうる。`generator_code_sha256` は first-party 閉包なので third-party の差を
    捉えない——よってスコアラー自身の pin を report レベルで突き合わせる（Codex P1）。

    相互一致だけでは足りない: 両 report が同じ捏造/stale pin を名乗れば通り、verdict
    はその pin を転記するので「一度も走っていないスコアラー実装」を主張する成果物が
    publish できる（Codex P2 第 23 巡）。抽出器 pin の `_require_execution_evidence`
    と同じく、**評価環境から use_cache=False で再計算した実スコアラー pin** との
    一致を publish 条件にする（report 内に無い・書き換えられない証拠）。測り直し
    検証は評価環境の mir_eval で行われるため、この照合により「metrics を検証した
    実装」と「verdict が名乗る実装」が同一であることが保証される。
    """
    pins: List[Tuple[Any, Any]] = []
    for idx, report in enumerate(reports):
        version = report.get("mir_eval_version")
        code = report.get("mir_eval_code_sha256")
        if not version or not isinstance(version, str):
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] が mir_eval_version を欠く; "
                "どの指標実装で測ったか不明な row にバーを適用しない (fail-closed)"
            )
        if not _is_sha256(code):
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] の mir_eval_code_sha256 {code!r} が "
                "真の sha256 でない; スコアラー実装を pin できない row を受理しない "
                "(fail-closed)"
            )
        pins.append((version, code))
    if len(set(pins)) > 1:
        raise ValueError(
            f"evaluate_m2_bars: reports の mir_eval pin が repeats 間で不一致 "
            f"{sorted(set(pins))}; 別の指標実装で測った run を同一 stack の repeats と "
            "見なさない (fail-closed)"
        )
    environment = _scorer_pins(use_cache=False)
    expected = (environment["mir_eval_version"], environment["mir_eval_code_sha256"])
    if pins[0] != expected:
        raise ValueError(
            f"evaluate_m2_bars: reports の mir_eval pin {pins[0]!r} が評価環境から "
            f"再計算した実スコアラー pin {expected!r} と一致しない; この環境の "
            "mir_eval で測られていない（または pin を捏造した）row を、その pin を "
            "名乗る verdict の証拠にしない (fail-closed)"
        )
    return {"mir_eval_version": pins[0][0], "mir_eval_code_sha256": pins[0][1]}


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
) -> Dict[str, Any]:
    """n>=`repeats_min` の run report に凍結バーを機械適用する（設計 §4/§6）。

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
    specs, specs_sha256 = load_specs(specs_path)
    _require_specs_pin(specs_sha256, bars_data)
    for idx, report in enumerate(reports):
        reported_specs = report.get("specs_sha256")
        if reported_specs != specs_sha256:
            raise ValueError(
                f"evaluate_m2_bars: reports[{idx}] の specs_sha256 {reported_specs!r} が "
                f"評価に使う spec {specs_sha256!r} と不一致; 別世代の合成仕様で測った row に "
                "凍結バーを適用しない (fail-closed)"
            )
    generator_code_sha256 = _require_matching_generator_code(reports)
    tolerance_cents = _require_frozen_tolerance(reports, bar_block)
    est_voiced_floor = _require_frozen_est_voicing_floor(reports, bar_block)
    scorer_pins = _require_homogeneous_scorer(reports)

    verdict: Dict[str, Any] = {
        "schema_version": _EXPECTED_VERDICT_SCHEMA,
        "verdict_recorded_utc": _utc_now(),
        "bars_sha256": bars_sha256,
        "generator_code_sha256": generator_code_sha256,
        "evaluator_code_sha256": _evaluator_code_sha256(),
        "tolerance_cents": tolerance_cents,
        "est_voiced_confidence_floor": est_voiced_floor,
        "mir_eval_version": scorer_pins["mir_eval_version"],
        "mir_eval_code_sha256": scorer_pins["mir_eval_code_sha256"],
        "n_reports": len(reports),
        "run_ids": sorted(run_ids),
        "repeats_min": repeats_min,
        "registration_attestation": registration_attestation,
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

        # repeats として数える前に (a) row が本当にその事前登録 fixture・経路の観測か、
        # (b) 同一 model stack で測られたか を証明する（Codex P1×2）。
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
        _reverify_category_measurement(
            category,
            rows,
            bars=bars,
            specs_path=specs_path,
            repeats=repeats_min,
        )

        bar = bar_block.get(category, {})
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

        # bars.yaml の `repeats_min` は決定論確認（「shifts=0 後は bit 一致するはず」）
        # であって「たまたま両方バー内」ではない。乖離はバーの有無と独立に記録する。
        bit_identical = _repeats_bit_identical(metrics_list)
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
    try:
        proc = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--absolute-git-dir", "--git-common-dir"],
            capture_output=True,
            text=True,
        )
    except OSError:
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
    args = parser.parse_args()
    _require_out_outside_git_metadata(args.out)

    if args.evaluate:
        if args.categories:
            raise SystemExit("--categories は run phase 専用（evaluate は report 側の row を評価する）")
        # `--out` が入力（report / bars / specs）を指していないか **書く前に** 確認する。
        # 上書きすると verdict の証拠そのもの（repeat evidence・凍結設定）が消え、
        # report_pins の hash も実体と食い違う（Codex P2 指摘）。
        protected = {Path(p).resolve() for p in args.evaluate}
        protected.add(Path(args.bars).resolve())
        protected.add(Path(args.specs).resolve())
        # provenance のために hash する first-party ソースも保護する。これを許すと
        # 「hash してから同じファイルを JSON で潰す」ことになり、artifact が自分が
        # 記録した bytes を破壊し次回実行も壊れる（Codex P2）。
        protected.update(_generator_code_paths())
        protected.update(_mir_eval_paths())
        protected.update(_runtime_input_paths())
        if Path(args.out).resolve() in protected:
            raise SystemExit(
                f"--out {args.out} は評価入力（report / bars / specs / provenance 対象の "
                "ソース）と同じパスを指している; 入力を verdict で上書きしない (fail-closed)"
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
        )
        _atomic_write_text(args.out, json.dumps(verdict, indent=2, sort_keys=True))
        print(f"wrote verdict to {args.out}")
        for category, result in verdict["categories"].items():
            print(f"  {category}: {result['status']}")
        return 0

    run_protected = {Path(args.bars).resolve(), Path(args.specs).resolve()}
    run_protected.update(_generator_code_paths())
    run_protected.update(_mir_eval_paths())
    run_protected.update(_runtime_input_paths())
    if Path(args.out).resolve() in run_protected:
        raise SystemExit(
            f"--out {args.out} は凍結入力（bars / specs）または provenance 対象のソースと "
            "同じパスを指している; これらを run report で上書きしない (fail-closed)"
        )
    run_kwargs: Dict[str, Any] = {}
    if args.categories:
        run_kwargs["categories"] = tuple(args.categories)
    result = run_accuracy(specs_path=args.specs, bars_path=args.bars, **run_kwargs)
    _atomic_write_text(args.out, json.dumps(result, indent=2, sort_keys=True))
    print(f"wrote run report to {args.out}")
    for category, row in result["categories"].items():
        print(f"  {category}: {row['outcome']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
