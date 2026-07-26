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
import re
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

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
    """
    stack: List[Path] = [Path(__file__).resolve()]
    for name in _SEED_MODULE_NAMES:
        target = _first_party_module_file(name)
        if target is not None:
            stack.append(target)

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
            target = _first_party_module_file(name)
            if target is not None and target not in resolved:
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


# run 中に実際の推論を担うランタイムパッケージ（登録済み route が実行しうるもの）。
# digest の対象外（third-party）だが、事前ロード済みなら「メモリ上の旧実装が推論し、
# row の code pin は新しいディスクを指す」窓が開くため、監視対象に含める。
_RUNTIME_PACKAGE_NAMES: "Tuple[str, ...]" = ("mir_eval", "crepe", "demucs", "torch", "torchaudio")


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
    """
    watched = set(_closure_module_names()) | set(_SEED_MODULE_NAMES) | set(_RUNTIME_PACKAGE_NAMES)
    return sorted(name for name in watched if name in sys.modules)


# 閉包 digest と事前ロード状況を **あらゆる first-party import より前に** 確定させる。
# `_generator_code_paths` は find_spec のみで import を起こさないので、この位置で呼べる
# （`provenance.package_code_state` と同じ #217 の規律）。以降 `run_accuracy` はこの値を
# pin として使い、実行後に再計算して一致を確認する（`_require_unchanged_since_load`）。
_PRELOADED_SEED_MODULES = _preloaded_seed_modules()
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


SPECS_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "m2_accuracy_specs.yaml"
BARS_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "m2_accuracy_bars.yaml"

_EXPECTED_BARS_SCHEMA = "m2-accuracy-bars/0.1"

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


def _parse_recorded_utc(value: Any, *, where: str) -> datetime:
    """report の `recorded_utc` を UTC timestamp として検証してパースする（fail-closed）。"""
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"evaluate_m2_bars: {where} に recorded_utc が無い（または文字列でない）; "
            "dated record を名乗る report は観測時刻を必須とする (fail-closed)"
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"evaluate_m2_bars: {where} の recorded_utc {value!r} は ISO 8601 として "
            f"解釈できない (fail-closed): {exc}"
        ) from exc
    offset = parsed.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError(
            f"evaluate_m2_bars: {where} の recorded_utc {value!r} は UTC でない"
            "（tz 無しまたは offset≠0）(fail-closed)"
        )
    if parsed > datetime.now(timezone.utc):
        raise ValueError(
            f"evaluate_m2_bars: {where} の recorded_utc {value!r} は未来の時刻; "
            "観測していない時点を dated record として主張させない (fail-closed)"
        )
    return parsed


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
    for required in ("sample_rate", "amplitude", "fixtures"):
        if required not in specs:
            raise ValueError(f"m2_accuracy_specs.yaml is missing required key {required!r}")
    return specs, hashlib.sha256(data).hexdigest()


def load_bars(path: Path = BARS_PATH) -> Tuple[Dict[str, Any], str]:
    """m2_accuracy_bars.yaml を single read で (parsed dict, sha256) として返す。"""
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
    _require_well_formed_bars(bars["m2_accuracy_bars"])
    return bars, hashlib.sha256(data).hexdigest()


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
    if repeats_min < 1:
        raise ValueError(f"m2_accuracy_bars: repeats_min {repeats_min!r} が 1 未満 (fail-closed)")

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
    """
    melody_fixture_id = (
        category_spec["fixture_id"]
        if category_spec["kind"] == "direct"
        else specs["composites"][category_spec["composite_id"]]["melody"]
    )
    melody_spec = specs["fixtures"][melody_fixture_id]
    return reference_f0_from_monophonic_spec(melody_spec, total_duration_sec=total_duration_sec)


# ---------------------------------------------------------------------------
# route 選択（routing.py は変更しない。既存表から名前で引く）
# ---------------------------------------------------------------------------


def _select_named_route(input_kind: str, route_name: str) -> MelodyRoute:
    for route in select_routes(input_kind):
        if route.name == route_name:
            return route
    raise ValueError(
        f"route {route_name!r} not found among select_routes({input_kind!r}) candidates; "
        "melody/routing.py の経路表が drift した可能性がある (fail-closed)"
    )


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

    unknown = [c for c in categories if c not in _CATEGORY_SPECS]
    if unknown:
        raise ValueError(f"unknown accuracy categories: {unknown}; expected one of {list(_CATEGORY_SPECS)}")

    effective_tolerance = (
        tolerance_cents
        if tolerance_cents is not None
        else float(bars["m2_accuracy_bars"].get("tolerance_cents", DEFAULT_TOLERANCE_CENTS))
    )

    # ロード時に確定した digest を使う（実行中にディスクのソースが変わっても、
    # 実際に走っているのは import 済みのコードなので、そちらを pin する）。
    results: Dict[str, Any] = {
        "mode": "synthetic_accuracy",
        "started_utc": _utc_now(),
        "run_id": uuid.uuid4().hex,
        "bars_sha256": bars_sha256,
        "specs_sha256": specs_sha256,
        "specs_path_relative": _repo_relative_path(specs_path),
        "bars_path_relative": _repo_relative_path(bars_path),
        "tolerance_cents": effective_tolerance,
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
            total_duration_sec = float(len(y)) / float(sr)
            ref_times, ref_freqs = _reference_for_category(
                category_spec, specs, total_duration_sec=total_duration_sec
            )
            route = _select_named_route(category_spec["input_kind"], category_spec["route_name"])

            row: Dict[str, Any] = {
                "route": route.name,
                "extractor": route.extractor,
                "input_kind": category_spec["input_kind"],
                "waveform_sha256": waveform_sha256,
                "ref_frame_count": len(ref_times),
                "ref_voiced_frame_count": sum(1 for f in ref_freqs if f > 0.0),
            }
            try:
                observation, route_provenance = runner(str(wav_path), route)
            except LearnedModelUnavailable as exc:
                row["outcome"] = "unavailable"
                row["detail"] = str(exc).splitlines()[0]
                results["categories"][category] = row
                continue

            metrics: MelodyAccuracyResult = evaluate_melody_accuracy(
                ref_times,
                ref_freqs,
                observation.frame_times,
                observation.frame_hz,
                tolerance_cents=effective_tolerance,
            )
            row["outcome"] = "measured"
            row["metrics"] = metrics.to_dict()
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

        nested_tolerance = metrics.get("tolerance_cents")
        if nested_tolerance is None:
            raise ValueError(
                f"evaluate_m2_bars: {where} の metrics が tolerance_cents を欠く (fail-closed)"
            )
        if float(nested_tolerance) != float(tolerance_cents):
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
    return {"mir_eval_version": pins[0][0], "mir_eval_code_sha256": pins[0][1]}


def _repeats_bit_identical(metrics_list: List[Dict[str, Any]]) -> bool:
    """repeats の metrics が完全一致か（bars の `repeats_min` 契約 = 決定論確認）。"""
    canonical = {json.dumps(m, sort_keys=True) for m in metrics_list}
    return len(canonical) <= 1


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
        if float(reported) != frozen:
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
    reports: List[Dict[str, Any]],
    bars: Dict[str, Any],
    *,
    bars_sha256: str,
    report_pins: Optional[List[Dict[str, Any]]] = None,
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
    - `report_pins` を渡すと、verdict が消費した report ファイルの sha256 を記録する
      （後から report が編集・差し替えられたことを検出できるようにする）。
    - S_fullstack: バーが空（`{}`）なので判定せず、`status="diagnostic_only"`
      として計測値のみ記録する（設計 §8: S_fullstack の低値を理由に crepe を
      責めない）。
    """
    bar_block = bars["m2_accuracy_bars"]
    repeats_min = int(bar_block.get("repeats_min", 2))

    if not reports:
        raise ValueError("evaluate_m2_bars: reports must be non-empty")
    if report_pins is not None and len(report_pins) != len(reports):
        raise ValueError(
            f"evaluate_m2_bars: report_pins 件数 {len(report_pins)} が reports 件数 "
            f"{len(reports)} と一致しない (fail-closed)"
        )

    run_ids: List[str] = []
    for idx, report in enumerate(reports):
        _parse_recorded_utc(report.get("recorded_utc"), where=f"reports[{idx}]")
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

    _require_publishable_runs(reports)
    generator_code_sha256 = _require_matching_generator_code(reports)
    tolerance_cents = _require_frozen_tolerance(reports, bar_block)
    scorer_pins = _require_homogeneous_scorer(reports)

    verdict: Dict[str, Any] = {
        "verdict_recorded_utc": _utc_now(),
        "bars_sha256": bars_sha256,
        "generator_code_sha256": generator_code_sha256,
        "evaluator_code_sha256": _evaluator_code_sha256(),
        "tolerance_cents": tolerance_cents,
        "mir_eval_version": scorer_pins["mir_eval_version"],
        "mir_eval_code_sha256": scorer_pins["mir_eval_code_sha256"],
        "n_reports": len(reports),
        "run_ids": sorted(run_ids),
        "repeats_min": repeats_min,
        "categories": {},
    }
    if report_pins is not None:
        verdict["report_pins"] = report_pins

    all_categories = sorted({cat for report in reports for cat in report.get("categories", {})})
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
        _require_registered_row_identity(category, rows, bars)
        _require_homogeneous_model_stack(category, rows)

        bar = bar_block.get(category, {})
        metrics_list = [row["metrics"] for row in rows]
        _require_finite_metrics(category, metrics_list)
        _require_metrics_contract(category, metrics_list, tolerance_cents=tolerance_cents)
        cat_result["metrics"] = metrics_list

        # bars.yaml の `repeats_min` は決定論確認（「shifts=0 後は bit 一致するはず」）
        # であって「たまたま両方バー内」ではない。乖離はバーの有無と独立に記録する。
        bit_identical = _repeats_bit_identical(metrics_list)
        cat_result["repeats_bit_identical"] = bit_identical

        if not bar:
            # S_fullstack: バーなし・診断記録のみ（設計 §3/§8）。
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

    return verdict


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


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
    args = parser.parse_args()

    if args.evaluate:
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
        if Path(args.out).resolve() in protected:
            raise SystemExit(
                f"--out {args.out} は評価入力（report / bars / specs / provenance 対象の "
                "ソース）と同じパスを指している; 入力を verdict で上書きしない (fail-closed)"
            )

        reports = []
        report_pins: List[Dict[str, Any]] = []
        for report_path in args.evaluate:
            data = Path(report_path).read_bytes()
            reports.append(_json_loads_no_dup_keys(data, what=str(report_path)))
            # parse したのと **同じ bytes** を hash する（read と hash の間に差し替えが
            # 入る TOCTOU を避ける）。パスは checkout 非依存の論理パスを併記する。
            report_pins.append(
                {
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "path_relative": _repo_relative_path(report_path),
                    "path_name": Path(report_path).name,
                }
            )
        bars, bars_sha256 = load_bars(args.bars)
        verdict = evaluate_m2_bars(
            reports, bars, bars_sha256=bars_sha256, report_pins=report_pins
        )
        _atomic_write_text(args.out, json.dumps(verdict, indent=2, sort_keys=True))
        print(f"wrote verdict to {args.out}")
        for category, result in verdict["categories"].items():
            print(f"  {category}: {result['status']}")
        return 0

    run_protected = {Path(args.bars).resolve(), Path(args.specs).resolve()}
    run_protected.update(_generator_code_paths())
    run_protected.update(_mir_eval_paths())
    if Path(args.out).resolve() in run_protected:
        raise SystemExit(
            f"--out {args.out} は凍結入力（bars / specs）または provenance 対象のソースと "
            "同じパスを指している; これらを run report で上書きしない (fail-closed)"
        )
    result = run_accuracy(specs_path=args.specs, bars_path=args.bars)
    _atomic_write_text(args.out, json.dumps(result, indent=2, sort_keys=True))
    print(f"wrote run report to {args.out}")
    for category, row in result["categories"].items():
        print(f"  {category}: {row['outcome']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
