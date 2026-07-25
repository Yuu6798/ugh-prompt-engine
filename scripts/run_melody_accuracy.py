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
import hashlib
import json
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

from svp_rpe.melody.provenance import bind_inference_code_pins  # noqa: E402

# 推論コードの pin を本モジュールが soundfile/build_melody_bench を import するより
# 前に確定する（run_melody_observability.py と同じ理由・#217）。
bind_inference_code_pins()

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

    return json.loads(data, object_pairs_hook=_reject_dupes)


def _atomic_write_text(path: Path, text: str) -> None:
    """`text` を `path` へ atomic に書く（同一ディレクトリの temp file → os.replace）。"""
    import os

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


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
    return bars, hashlib.sha256(data).hexdigest()


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


def _generator_code_sha256() -> str:
    """本ハーネス自身（M2a の新規 2 モジュール）の digest。

    実抽出器（crepe 等）の重み/コード pin は `observe_via_route_with_provenance`
    が返す provenance dict（`extractor_weights_sha256` / `extractor_code_sha256` /
    `preprocessing.*`）としてこのハーネスがそのまま row へ転記する——ここで作る
    digest は「row の外形（メトリクス算出ロジック・route 選択）を生んだのはどの
    コードか」を pin するもので、下流の学習モデル本体は対象にしない
    （学習モデル本体の pin は既存 `melody/provenance.py` の責務のまま）。
    """
    digest = hashlib.sha256()
    for path in sorted(
        [Path(__file__).resolve(), (SRC / "svp_rpe" / "melody" / "accuracy.py").resolve()],
        key=lambda p: p.name,
    ):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


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

    results: Dict[str, Any] = {
        "mode": "synthetic_accuracy",
        "started_utc": _utc_now(),
        "run_id": uuid.uuid4().hex,
        "bars_sha256": bars_sha256,
        "specs_sha256": specs_sha256,
        "specs_path_relative": _repo_relative_path(specs_path),
        "bars_path_relative": _repo_relative_path(bars_path),
        "tolerance_cents": effective_tolerance,
        "generator_code_sha256": _generator_code_sha256(),
        "categories": {},
    }

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

    results["recorded_utc"] = _utc_now()
    return results


# ---------------------------------------------------------------------------
# evaluate phase
# ---------------------------------------------------------------------------


def _evaluator_code_sha256() -> str:
    """verdict を解釈するコード（本モジュール + accuracy.py）の digest。"""
    return _generator_code_sha256()


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
    - S_fullstack: バーが空（`{}`）なので判定せず、`status="diagnostic_only"`
      として計測値のみ記録する（設計 §8: S_fullstack の低値を理由に crepe を
      責めない）。
    """
    bar_block = bars["m2_accuracy_bars"]
    repeats_min = int(bar_block.get("repeats_min", 2))

    if not reports:
        raise ValueError("evaluate_m2_bars: reports must be non-empty")

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

    generator_code_sha256 = _require_matching_generator_code(reports)

    verdict: Dict[str, Any] = {
        "verdict_recorded_utc": _utc_now(),
        "bars_sha256": bars_sha256,
        "generator_code_sha256": generator_code_sha256,
        "evaluator_code_sha256": _evaluator_code_sha256(),
        "n_reports": len(reports),
        "run_ids": sorted(run_ids),
        "repeats_min": repeats_min,
        "categories": {},
    }

    all_categories = sorted({cat for report in reports for cat in report.get("categories", {})})
    for category in all_categories:
        rows = [report["categories"][category] for report in reports if category in report["categories"]]
        outcomes = {row["outcome"] for row in rows}
        cat_result: Dict[str, Any] = {"n_rows": len(rows), "outcomes": sorted(outcomes)}

        if len(rows) < repeats_min or "unavailable" in outcomes:
            cat_result["status"] = "insufficient_repeats"
            verdict["categories"][category] = cat_result
            continue

        bar = bar_block.get(category, {})
        metrics_list = [row["metrics"] for row in rows]
        cat_result["metrics"] = metrics_list

        if not bar:
            # S_fullstack: バーなし・診断記録のみ（設計 §3/§8）。
            cat_result["status"] = "diagnostic_only"
            verdict["categories"][category] = cat_result
            continue

        failures: List[str] = []
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
        reports = []
        for report_path in args.evaluate:
            data = Path(report_path).read_bytes()
            reports.append(_json_loads_no_dup_keys(data, what=str(report_path)))
        bars, bars_sha256 = load_bars(args.bars)
        verdict = evaluate_m2_bars(reports, bars, bars_sha256=bars_sha256)
        _atomic_write_text(args.out, json.dumps(verdict, indent=2, sort_keys=True))
        print(f"wrote verdict to {args.out}")
        for category, result in verdict["categories"].items():
            print(f"  {category}: {result['status']}")
        return 0

    result = run_accuracy(specs_path=args.specs, bars_path=args.bars)
    _atomic_write_text(args.out, json.dumps(result, indent=2, sort_keys=True))
    print(f"wrote run report to {args.out}")
    for category, row in result["categories"].items():
        print(f"  {category}: {row['outcome']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
