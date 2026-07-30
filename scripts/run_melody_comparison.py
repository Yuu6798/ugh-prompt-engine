"""run_melody_comparison.py — M3d: 校正ハーネス（run/evaluate 二相）。

`svp_rpe.melody.comparison.compare_melodies`（M3c）を pair manifest に沿って走らせ
report を書き出す（run phase）。複数 report（n>=2 repeats）を突き合わせて
軌跡レベル決定論（sequence hash pin）を確認し、tuning split のみからマージン表
（positive 最小類似 − negative 最大類似）を機械導出する（evaluate phase）。

standalone script（`svprpe` サブコマンド化しない・sys.path 注入は他スクリプトと
同流儀）。`scripts/run_melody_accuracy.py` の 7000 行級 anti-tamper 要塞は複製
しない——本ハーネスが踏襲するのは以下 4 点のみ（`docs/DESIGN_M3_melody_comparator.md`
§6 / M3 実装 memo「## M3d」節）:

1. atomic write（`_atomic_write_text`）
2. レジストリ sha256 pin（`load_m3_registry` / `_load_m1_registry`）
3. route_runner 注入 seam（既定は実抽出器・注入時は `route_runner_injected: true` を
   記録し、evaluate は calibration verdict の発行を拒否する — M2 と同じ流儀）
4. protected-path（`--out` が manifest / registry / report / manifest が指す音声入力を
   上書きしない）

適用帯域: 本ハーネスの既定 route は **clear_lead 経路限定**（User 決裁 2026-07-30・
単離済み clean lead 帯）。実音声・実 crepe による slow-lane 実測は本セッションでは
実行しない——run phase の既定 route_runner は実抽出器を呼ぶが、テストは fake
route_runner を注入して run/evaluate の二相メカニズムのみを検証する。

route_runner 非注入（実測・publishable）の run は **crepe 系経路限定**（設計 §6.1
「対の両側とも実 crepe 抽出を通す」）——pyin_direct 等の非 crepe 経路を指定すると
fail-closed で拒否する。melodia 系経路は注入の有無に関わらず常に拒否する（#222
裁定前の混入禁止・設計 §8）。

使い方::

    python scripts/run_melody_comparison.py --pairs pairs.yaml --out run1.json
    python scripts/run_melody_comparison.py --pairs pairs.yaml --out run2.json
    python scripts/run_melody_comparison.py --evaluate run1.json run2.json --out verdict.json

pairs manifest（YAML）の形:

    schema: m3-comparison-pairs/0.1
    pairs:
      - pair_id: p001
        kind: positive_transform   # positive_transform|negative_cross|negative_rhythm|negative_interval
        split: tuning              # tuning|holdout
        audio_a: /path/to/a.wav
        audio_b: /path/to/b.wav
        expected: same             # same|different
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
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

import yaml  # noqa: E402

from svp_rpe.melody.comparison import compare_melodies  # noqa: E402
from svp_rpe.melody.observability import (  # noqa: E402
    MelodyObservation,
    ObservabilityThresholds,
)
from svp_rpe.melody.representation import load_m3_registry  # noqa: E402
from svp_rpe.melody.routing import select_routes  # noqa: E402

M3_REGISTRY_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "m3_comparison_registry.yaml"
M1_REGISTRY_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "registry.yaml"

_EXPECTED_RUN_SCHEMA = "m3-comparison-harness/0.1"
_EXPECTED_MANIFEST_SCHEMA = "m3-comparison-pairs/0.1"
_VALID_KINDS = {
    "positive_transform",
    "negative_cross",
    "negative_rhythm",
    "negative_interval",
}
_VALID_SPLITS = {"tuning", "holdout"}
_VALID_EXPECTED = {"same", "different"}
_REQUIRED_PAIR_KEYS = {"pair_id", "kind", "split", "audio_a", "audio_b", "expected"}
_AXES: Tuple[str, ...] = ("contour", "interval", "rhythm")

# route_runner: (audio_path) -> (MelodyObservation, provenance dict)。
RouteRunner = Callable[[str], Tuple[MelodyObservation, Dict[str, Any]]]


# --------------------------------------------------------------------------- #
# 小道具（atomic write / dup-key safe loader / registry hash pin と同型）
# --------------------------------------------------------------------------- #
def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _atomic_write_text(path: Path, text: str) -> None:
    """`text` を `path` へ atomic に書く（`run_melody_observability.py` と同型）。"""
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


class _NoDupSafeLoader(yaml.SafeLoader):
    """重複 mapping キーを拒否する SafeLoader（`representation.py` / M1 harness と同型）。"""


def _no_dup_construct_mapping(loader: "yaml.SafeLoader", node: Any, deep: bool = False) -> Dict[Any, Any]:
    mapping: Dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(
                f"duplicate YAML mapping key {key!r}; last-wins で pre-registration block を "
                "隠す穴を弾く (fail-closed)"
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_NoDupSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_dup_construct_mapping
)


def _yaml_load_no_dup_keys(data: bytes, *, what: str) -> Any:
    try:
        return yaml.load(data, Loader=_NoDupSafeLoader)  # noqa: S506 (dup-key 拒否付き SafeLoader)
    except yaml.YAMLError as exc:
        raise ValueError(f"{what}: YAML parse error: {exc}") from exc


def _json_loads_no_dup_keys(data: bytes, *, what: str) -> Any:
    def _reject_dupes(pairs: "List[tuple]") -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(
                    f"{what}: duplicate JSON object key {key!r}; 失敗レコードを last-wins で "
                    "隠す穴を弾く (fail-closed)"
                )
            result[key] = value
        return result

    return json.loads(data, object_pairs_hook=_reject_dupes)


def _load_m1_registry(path: Path) -> "Tuple[Dict[str, Any], str]":
    data = Path(path).read_bytes()
    mapping = _yaml_load_no_dup_keys(data, what="registry.yaml")
    return mapping, hashlib.sha256(data).hexdigest()


def _load_report(path: Path) -> Dict[str, Any]:
    data = Path(path).read_bytes()
    return _json_loads_no_dup_keys(data, what="run report")


# --------------------------------------------------------------------------- #
# manifest 検証
# --------------------------------------------------------------------------- #
def _validate_manifest(manifest: Any) -> List[Dict[str, Any]]:
    """pairs manifest（YAML）を検証し、pair dict のリストを返す（未知/欠落キー fail-closed）。"""
    if not isinstance(manifest, dict):
        raise ValueError("pairs manifest must be a mapping with 'schema' and 'pairs' keys")
    schema = manifest.get("schema")
    if schema != _EXPECTED_MANIFEST_SCHEMA:
        raise ValueError(
            f"unsupported pairs manifest schema {schema!r}; expected "
            f"{_EXPECTED_MANIFEST_SCHEMA!r} (fail-closed)"
        )
    pairs = manifest.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise ValueError("pairs manifest 'pairs' must be a non-empty list")

    seen_ids: set = set()
    validated: List[Dict[str, Any]] = []
    for idx, pair in enumerate(pairs):
        if not isinstance(pair, dict):
            raise ValueError(f"pairs[{idx}] must be a mapping")
        missing = _REQUIRED_PAIR_KEYS - set(pair)
        if missing:
            raise ValueError(f"pairs[{idx}] missing required keys: {sorted(missing)}")
        unknown = set(pair) - _REQUIRED_PAIR_KEYS
        if unknown:
            raise ValueError(f"pairs[{idx}] has unknown keys (fail-closed): {sorted(unknown)}")
        pair_id = pair["pair_id"]
        if pair_id in seen_ids:
            raise ValueError(f"duplicate pair_id {pair_id!r} in pairs manifest (fail-closed)")
        seen_ids.add(pair_id)
        if pair["kind"] not in _VALID_KINDS:
            raise ValueError(
                f"pairs[{idx}] ({pair_id!r}) has invalid kind {pair['kind']!r}; "
                f"expected one of {sorted(_VALID_KINDS)}"
            )
        if pair["split"] not in _VALID_SPLITS:
            raise ValueError(
                f"pairs[{idx}] ({pair_id!r}) has invalid split {pair['split']!r}; "
                f"expected one of {sorted(_VALID_SPLITS)}"
            )
        if pair["expected"] not in _VALID_EXPECTED:
            raise ValueError(
                f"pairs[{idx}] ({pair_id!r}) has invalid expected {pair['expected']!r}; "
                f"expected one of {sorted(_VALID_EXPECTED)}"
            )
        validated.append(dict(pair))
    return validated


def _manifest_audio_paths(manifest_path: Path) -> "set[Path]":
    """pairs manifest 全 pair の audio_a/audio_b を絶対パスへ解決した集合を返す。

    `--out` protected-path チェック用（run phase preflight）: manifest 自体だけ
    でなく、manifest が指す音声入力を `--out` で上書きすることも防ぐ。
    """
    manifest_bytes = Path(manifest_path).read_bytes()
    manifest = _yaml_load_no_dup_keys(manifest_bytes, what="pairs manifest")
    pairs_spec = _validate_manifest(manifest)
    paths: "set[Path]" = set()
    for pair in pairs_spec:
        paths.add(Path(pair["audio_a"]).resolve())
        paths.add(Path(pair["audio_b"]).resolve())
    return paths


# --------------------------------------------------------------------------- #
# run phase
# --------------------------------------------------------------------------- #
def _resolve_route(route_name: str) -> Any:
    """`route_name` を clear_lead 経路の中から解決する（適用帯域=clean lead 限定）。"""
    for route in select_routes("clear_lead"):
        if route.name == route_name:
            return route
    raise ValueError(
        f"unknown --route {route_name!r}; M3 の許可帯域は clear_lead 限定 "
        f"(候補: {[r.name for r in select_routes('clear_lead')]})"
    )


def _validate_route_for_run(route_name: str, route: Any, *, runner_injected: bool) -> None:
    """route が実測制約を満たすか検証する（設計 §6.1 / §8, fail-closed）。

    - melodia 系経路は route_runner 注入の有無に関わらず常に拒否（#222 裁定前の
      混入禁止・設計 §8「melodia の混入」）。
    - route_runner 非注入（実測・publishable な run）は crepe 系経路限定（設計
      §6.1「対の両側とも実 crepe 抽出を通す」）。route_runner 注入時（テスト）は
      任意経路可 — その事実は report 側 `route_runner_injected` に刻まれ、
      evaluate は calibration verdict 発行を別途拒否する。
    """
    if "melodia" in route_name.lower() or getattr(route, "extractor", "") == "melodia":
        raise ValueError(
            f"--route {route_name!r} は melodia 系経路; #222 裁定前は route_runner 注入の "
            "有無に関わらず禁止 (fail-closed, 設計 §8)"
        )
    if not runner_injected and getattr(route, "extractor", "") != "crepe":
        raise ValueError(
            f"--route {route_name!r} は実測 (route_runner 非注入) の run では crepe 系経路 "
            f"限定 (extractor={getattr(route, 'extractor', None)!r}); 設計 §6.1 "
            "「対の両側とも実 crepe 抽出を通す」要求 (fail-closed)"
        )


def _default_route_runner(route_name: str) -> RouteRunner:
    """既定の route_runner: 実抽出器（`observe_via_route_with_provenance`）。

    optional 依存（crepe/melodia）が未導入なら `LearnedModelUnavailable` が
    そのまま伝播する（実行時 DL 禁止・fail-closed。M1/M2 と同じ規律）。
    """
    from svp_rpe.melody.extractors import observe_via_route_with_provenance

    route = _resolve_route(route_name)

    def _runner(audio_path: str) -> "Tuple[MelodyObservation, Dict[str, Any]]":
        return observe_via_route_with_provenance(audio_path, route)

    return _runner


def run_comparison(
    *,
    manifest_path: Path,
    route_name: str = "pyin_direct",
    route_runner: Optional[RouteRunner] = None,
    registry_path: Path = M3_REGISTRY_PATH,
    m1_registry_path: Path = M1_REGISTRY_PATH,
) -> Dict[str, Any]:
    """pairs manifest の全ペアを `compare_melodies` へ通し run report dict を返す。

    `route_runner` は抽出器非依存の注入 seam: ``(audio_path) -> (MelodyObservation,
    provenance dict)``。既定は `_default_route_runner`（実抽出器）。テストは
    フェイク抽出器（決定論の観測を返す）に差し替えて run/evaluate の二相
    メカニズムだけを検証する。注入した事実は report 自身に
    ``route_runner_injected: true`` として刻み、evaluate はそれを calibration
    verdict 発行の拒否条件にする（M2 `run_melody_accuracy.py` と同じ規律）。
    """
    runner_injected = route_runner is not None
    route = _resolve_route(route_name)
    _validate_route_for_run(route_name, route, runner_injected=runner_injected)
    runner: RouteRunner = route_runner or _default_route_runner(route_name)

    config, m3_registry_sha256 = load_m3_registry(registry_path)
    m1_mapping, m1_registry_sha256 = _load_m1_registry(m1_registry_path)
    thresholds = ObservabilityThresholds.from_registry(m1_mapping["observation_gate"])

    manifest_bytes = Path(manifest_path).read_bytes()
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    manifest = _yaml_load_no_dup_keys(manifest_bytes, what="pairs manifest")
    pairs_spec = _validate_manifest(manifest)

    # holdout は evidence_thresholds.status が "frozen" になるまで run 時点でも
    # 開かない（evaluate 側のロックだけでは、report に holdout の axes/hash が既に
    # 書かれてしまい閲覧可能になる穴が残るため。設計 §6.3「holdout は閾値凍結後に
    # 一度だけ開く」を run phase でも enforce する）。
    holdout_locked = config.evidence_thresholds.status != "frozen"

    results: Dict[str, Any] = {
        "schema_version": _EXPECTED_RUN_SCHEMA,
        "mode": "run",
        "started_utc": _utc_now(),
        "run_id": uuid.uuid4().hex,
        "route": route_name,
        "route_runner_injected": runner_injected,
        "m3_registry_sha256": m3_registry_sha256,
        "m1_registry_sha256": m1_registry_sha256,
        "manifest_path": str(Path(manifest_path).resolve()),
        "manifest_sha256": manifest_sha256,
        "pairs": {},
    }
    for pair in pairs_spec:
        if pair["split"] == "holdout" and holdout_locked:
            # 音声も読まず比較も実行しない。split は tuning/holdout フィルタ
            # （`_holdout_pair_ids` / `_margin_table` / `_coverage_floor_candidate`）
            # のために残すが、axes/hash（`comparison`）は一切書かない。
            results["pairs"][pair["pair_id"]] = {
                "split": pair["split"],
                "status": "holdout_locked_until_frozen",
            }
            continue
        obs_a, prov_a = runner(pair["audio_a"])
        obs_b, prov_b = runner(pair["audio_b"])
        comparison = compare_melodies(
            obs_a,
            obs_b,
            observability_thresholds=thresholds,
            config=config,
            provenance_extra={
                "m3_registry_sha256": m3_registry_sha256,
                "m1_registry_sha256": m1_registry_sha256,
            },
        )
        row: Dict[str, Any] = {
            "kind": pair["kind"],
            "split": pair["split"],
            "expected": pair["expected"],
            "audio_a": pair["audio_a"],
            "audio_b": pair["audio_b"],
            "comparison": comparison.to_dict(),
        }
        if prov_a:
            row["route_provenance_a"] = prov_a
        if prov_b:
            row["route_provenance_b"] = prov_b
        results["pairs"][pair["pair_id"]] = row

    results["recorded_utc"] = _utc_now()
    return results


# --------------------------------------------------------------------------- #
# evaluate phase
# --------------------------------------------------------------------------- #
def _check_repeats_consistency(reports: List[Dict[str, Any]]) -> None:
    """repeats（n>=2 の run report）が同一 pair 集合について bit 一致することを確認する。

    軌跡レベル決定論の実測確立（M2d 残課題を閉じる測定・設計 §1）。`sequence_sha256_a/b`
    と `axes` を repeats 間で突き合わせ、1 箇所でも食い違えば fail-closed で拒否する。
    """
    if len(reports) < 2:
        return
    reference = reports[0]["pairs"]
    for idx, report in enumerate(reports[1:], start=1):
        pairs = report["pairs"]
        if set(pairs) != set(reference):
            raise ValueError(
                f"evaluate_comparison: reports[{idx}] のペア集合が reports[0] と異なる; "
                "repeats は同一 manifest に対する実行でなければならない (fail-closed)"
            )
        for pair_id, ref_pair in reference.items():
            cur_pair = pairs[pair_id]
            ref_has_comparison = "comparison" in ref_pair
            cur_has_comparison = "comparison" in cur_pair
            if ref_has_comparison != cur_has_comparison:
                raise ValueError(
                    f"evaluate_comparison: pair {pair_id!r} の holdout ロック状態が repeats 間で"
                    f"異なる (reports[0] comparison_present={ref_has_comparison} != "
                    f"reports[{idx}] comparison_present={cur_has_comparison}); registry の "
                    "evidence_thresholds.status が実行間で変化した可能性 (fail-closed)"
                )
            if not ref_has_comparison:
                # holdout ロック済みペア同士 — axes/hash が存在しないため比較不要。
                continue
            ref_comp = ref_pair["comparison"]
            cur_comp = cur_pair["comparison"]
            ref_sig = (
                ref_comp.get("provenance", {}).get("sequence_sha256_a"),
                ref_comp.get("provenance", {}).get("sequence_sha256_b"),
                ref_comp.get("axes"),
            )
            cur_sig = (
                cur_comp.get("provenance", {}).get("sequence_sha256_a"),
                cur_comp.get("provenance", {}).get("sequence_sha256_b"),
                cur_comp.get("axes"),
            )
            if ref_sig != cur_sig:
                raise ValueError(
                    f"evaluate_comparison: pair {pair_id!r} が repeats 間で "
                    f"sequence_sha256/axes 不一致 (reports[0]={ref_sig!r} != "
                    f"reports[{idx}]={cur_sig!r}); 軌跡レベル決定論が崩れている (fail-closed)"
                )


def _validate_registry_pins(
    reports: List[Dict[str, Any]],
    *,
    current_m3_sha256: str,
    current_m1_sha256: str,
) -> None:
    """report 間 + 現在ロードした registry との sha256 pin 整合を検証する (fail-closed)。

    (a) report に m3_registry_sha256 が欠落、(b) report 間で不一致、(c) 現在
    ロードした registry の sha256 と不一致 — のいずれかを検出したら拒否する。
    m1_registry_sha256 は「記録されていれば同様に」検査する（1 件でも記録が
    あれば全件で欠落なく一致 + 現在ロードした m1 registry と一致を要求する）。
    """
    for idx, report in enumerate(reports):
        m3_sha = report.get("m3_registry_sha256")
        if not m3_sha:
            raise ValueError(
                f"evaluate_comparison: reports[{idx}] に m3_registry_sha256 が記録されていない "
                "(fail-closed)"
            )
    reference_m3_sha = reports[0]["m3_registry_sha256"]
    for idx, report in enumerate(reports[1:], start=1):
        m3_sha = report["m3_registry_sha256"]
        if m3_sha != reference_m3_sha:
            raise ValueError(
                f"evaluate_comparison: reports[{idx}] の m3_registry_sha256={m3_sha!r} が "
                f"reports[0]={reference_m3_sha!r} と不一致 (fail-closed)"
            )
    if reference_m3_sha != current_m3_sha256:
        raise ValueError(
            f"evaluate_comparison: reports の m3_registry_sha256={reference_m3_sha!r} が現在"
            f"ロードした registry の sha256={current_m3_sha256!r} と不一致 (fail-closed)"
        )

    m1_shas = [report.get("m1_registry_sha256") for report in reports]
    if any(m1_shas):
        for idx, sha in enumerate(m1_shas):
            if not sha:
                raise ValueError(
                    f"evaluate_comparison: reports[{idx}] に m1_registry_sha256 が記録されて"
                    "いない (fail-closed; 他の report は記録している)"
                )
        reference_m1_sha = m1_shas[0]
        for idx, sha in enumerate(m1_shas[1:], start=1):
            if sha != reference_m1_sha:
                raise ValueError(
                    f"evaluate_comparison: reports[{idx}] の m1_registry_sha256={sha!r} が "
                    f"reports[0]={reference_m1_sha!r} と不一致 (fail-closed)"
                )
        if reference_m1_sha != current_m1_sha256:
            raise ValueError(
                f"evaluate_comparison: reports の m1_registry_sha256={reference_m1_sha!r} が"
                f"現在ロードした m1 registry の sha256={current_m1_sha256!r} と不一致 "
                "(fail-closed)"
            )


def _holdout_pair_ids(pairs: Dict[str, Dict[str, Any]]) -> List[str]:
    return sorted(pair_id for pair_id, pair in pairs.items() if pair["split"] == "holdout")


def _margin_table(
    pairs: Dict[str, Dict[str, Any]], *, split: Optional[str], min_margin: float
) -> Dict[str, Any]:
    """`split`（None なら全件）の pair から軸別マージン（positive 最小 − negative 最大）を出す。

    `evidence_thresholds` を一切見ない——校正候補は生の axes 値の分布のみから導出し、
    holdout ロックは呼び出し側（`evaluate_comparison`）が別途課す。
    """
    axis_positive: Dict[str, List[float]] = {axis: [] for axis in _AXES}
    axis_negative: Dict[str, List[float]] = {axis: [] for axis in _AXES}
    skipped: List[str] = []

    for pair_id, pair in pairs.items():
        if split is not None and pair["split"] != split:
            continue
        comparison = pair["comparison"]
        if comparison["evidence"] == "not_comparable":
            skipped.append(f"{pair_id}:not_comparable")
            continue
        bucket = axis_positive if pair["expected"] == "same" else axis_negative
        for axis, value in comparison["axes"].items():
            if value is None:
                continue
            bucket.setdefault(axis, []).append(value)

    axes_table: Dict[str, Any] = {}
    calibrated_axes: List[str] = []
    for axis in _AXES:
        positives = axis_positive.get(axis, [])
        negatives = axis_negative.get(axis, [])
        if not positives or not negatives:
            axes_table[axis] = {
                "positive_min": None,
                "negative_max": None,
                "margin": None,
                "calibrated_candidate": False,
                "reason": "insufficient_positive_or_negative_samples",
            }
            continue
        positive_min = min(positives)
        negative_max = max(negatives)
        margin = positive_min - negative_max
        calibrated = margin >= min_margin
        axes_table[axis] = {
            "positive_min": positive_min,
            "negative_max": negative_max,
            "margin": margin,
            "calibrated_candidate": calibrated,
        }
        if calibrated:
            calibrated_axes.append(axis)

    return {"axes": axes_table, "calibrated_axes": calibrated_axes, "skipped_pairs": skipped}


def _coverage_floor_candidate(pairs: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """tuning split の positive pair の被覆分布から floor 候補を出す（設計 §4.2 / §6.2）。

    凍結は行わない（人間の registry 更新 commit の仕事）——ここでは観測分布の
    記録のみを返す。
    """
    fractions: List[float] = []
    for pair in pairs.values():
        if pair["split"] != "tuning" or pair["expected"] != "same":
            continue
        coverage = pair["comparison"].get("coverage") or {}
        for key in ("aligned_note_fraction_a", "aligned_note_fraction_b"):
            value = coverage.get(key)
            if value is not None:
                fractions.append(value)
    if not fractions:
        return {"candidate": None, "reason": "no_tuning_positive_coverage_samples"}
    return {
        "candidate": min(fractions),
        "min": min(fractions),
        "max": max(fractions),
        "mean": sum(fractions) / len(fractions),
        "sample_count": len(fractions),
    }


def evaluate_comparison(
    reports: List[Dict[str, Any]],
    *,
    registry_path: Path = M3_REGISTRY_PATH,
    m1_registry_path: Path = M1_REGISTRY_PATH,
) -> Dict[str, Any]:
    """n>=1 の run report から校正 verdict（マージン表 + holdout ロック）を導出する。

    処理順（M3 実装 memo「## M3d」節）:
    1. registry sha256 pin の整合検証（report 間 + 現在ロードした registry）
    2. repeats（sequence hash / axes）の bit 一致検証
    3. route_runner_injected な report があれば calibration verdict 発行を拒否
    4. repeats が n>=2 でなければ calibration verdict 発行を拒否（n=1 は「決定論
       repeats 未検証」であり校正証拠にしない）
    5. tuning split のみで軸別マージン表を算出（`separation_margin` 閾値）
    6. holdout は `evidence_thresholds.status == "frozen"` になるまで開かない
    7. tuning positive pair の被覆分布から coverage floor 候補を emit
    """
    if not reports:
        raise ValueError("evaluate_comparison: reports が空 (fail-closed)")
    for idx, report in enumerate(reports):
        schema = report.get("schema_version")
        if schema != _EXPECTED_RUN_SCHEMA:
            raise ValueError(
                f"evaluate_comparison: reports[{idx}] の schema_version {schema!r} が "
                f"未知; 期待値は {_EXPECTED_RUN_SCHEMA!r} (fail-closed)"
            )

    config, m3_registry_sha256 = load_m3_registry(registry_path)
    _, m1_registry_sha256 = _load_m1_registry(m1_registry_path)
    _validate_registry_pins(
        reports,
        current_m3_sha256=m3_registry_sha256,
        current_m1_sha256=m1_registry_sha256,
    )

    _check_repeats_consistency(reports)

    reference_pairs = reports[0]["pairs"]

    route_runner_injected_any = any(bool(r.get("route_runner_injected")) for r in reports)
    repeats_verified = len(reports) >= 2

    verdict: Dict[str, Any] = {
        "schema_version": "m3-comparison-verdict/0.1",
        "recorded_utc": _utc_now(),
        "m3_registry_sha256": m3_registry_sha256,
        "repeats_count": len(reports),
        "repeats_verified": repeats_verified,
        "route_runner_injected": route_runner_injected_any,
        "evidence_thresholds_status": config.evidence_thresholds.status,
    }
    if repeats_verified:
        # `_check_repeats_consistency` が食い違いを検知したら既に例外で fail
        # しているため、ここに到達した時点で bit 一致は確認済み。
        verdict["repeats_consistent"] = True
    else:
        # n=1 は「決定論 repeats」を測っていない——true と書かない(正直な表現)。
        verdict["repeats_consistent"] = False

    if route_runner_injected_any:
        # フェイク抽出器の出力は「実測」ではない——他の全チェックが通っても
        # calibration verdict（マージン表・凍結提案）は発行しない（M2 `_require_
        # publishable_runs` と同じ規律）。
        verdict["calibration_verdict_status"] = "rejected_route_runner_injected"
        verdict["reason"] = (
            "1 件以上の report が route_runner 注入で作られている; フェイク抽出器の "
            "出力を calibration 証拠として publish しない (fail-closed)"
        )
        return verdict

    if not repeats_verified:
        # repeats n>=2 が事前登録の要求（設計 §6.2「repeats n≥2 + 系列 hash pin」）。
        # n=1 ではマージン表・凍結提案・coverage floor 候補を一切 emit しない。
        verdict["calibration_verdict_status"] = "rejected_insufficient_repeats"
        verdict["reason"] = f"insufficient_repeats(n={len(reports)}, required>=2)"
        return verdict

    holdout_locked = config.evidence_thresholds.status != "frozen"
    verdict["holdout_locked_until_frozen"] = holdout_locked
    verdict["holdout_pair_ids_skipped"] = _holdout_pair_ids(reference_pairs) if holdout_locked else []

    margin = _margin_table(
        reference_pairs,
        split="tuning",
        min_margin=config.separation_margin.min_same_minus_cross_margin,
    )
    verdict["margin_table"] = margin["axes"]
    verdict["calibrated_axes"] = margin["calibrated_axes"]
    verdict["skipped_pairs"] = margin["skipped_pairs"]
    verdict["freeze_proposal"] = {
        axis: {
            "strong_min": margin["axes"][axis]["positive_min"],
            "none_max": margin["axes"][axis]["negative_max"],
        }
        for axis in margin["calibrated_axes"]
    }
    verdict["coverage_floor_candidate"] = _coverage_floor_candidate(reference_pairs)
    return verdict


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="出力 JSON の書き出し先")
    parser.add_argument("--pairs", type=Path, help="pairs manifest（YAML）— run phase")
    parser.add_argument(
        "--route",
        default="pyin_direct",
        help=(
            "既定 route_runner が使う clear_lead 経路名（既定: pyin_direct）。"
            "実測 run（--pairs 経由・route_runner 非注入）は crepe 系経路限定"
            "（例: crepe_direct）。melodia 系経路は常に拒否"
        ),
    )
    parser.add_argument("--registry", type=Path, default=M3_REGISTRY_PATH)
    parser.add_argument("--m1-registry", type=Path, default=M1_REGISTRY_PATH)
    parser.add_argument(
        "--evaluate",
        nargs="+",
        type=Path,
        metavar="REPORT.json",
        help="run report(s) から校正 verdict を出す（未指定なら run phase）",
    )
    args = parser.parse_args()

    if args.evaluate:
        if args.pairs:
            raise SystemExit("--pairs は run phase 専用（evaluate は report 側を評価する）")
        protected = {Path(p).resolve() for p in args.evaluate}
        protected.add(Path(args.registry).resolve())
        protected.add(Path(args.m1_registry).resolve())
        if Path(args.out).resolve() in protected:
            raise SystemExit(
                f"--out {args.out} は評価入力（report / registry）と同じパスを指している; "
                "入力を verdict で上書きしない (fail-closed)"
            )
        reports = [_load_report(p) for p in args.evaluate]
        verdict = evaluate_comparison(
            reports, registry_path=args.registry, m1_registry_path=args.m1_registry
        )
        _atomic_write_text(args.out, json.dumps(verdict, indent=2, sort_keys=True))
        print(f"wrote verdict to {args.out}")
        print(f"  holdout_locked_until_frozen: {verdict.get('holdout_locked_until_frozen')}")
        print(f"  calibrated_axes: {verdict.get('calibrated_axes')}")
        return 0

    if not args.pairs:
        raise SystemExit("--pairs は run phase に必須（--evaluate 未指定時）")
    protected = {
        Path(args.pairs).resolve(),
        Path(args.registry).resolve(),
        Path(args.m1_registry).resolve(),
    }
    # manifest が指す音声入力（audio_a/audio_b）も protected-path に含める——
    # --out がそのいずれかと一致したら書き込み前に fail する。
    protected |= _manifest_audio_paths(args.pairs)
    if Path(args.out).resolve() in protected:
        raise SystemExit(
            f"--out {args.out} は入力（pairs manifest / registry / manifest の音声入力）と "
            "同じパスを指している; これらを run report で上書きしない (fail-closed)"
        )
    result = run_comparison(
        manifest_path=args.pairs,
        route_name=args.route,
        registry_path=args.registry,
        m1_registry_path=args.m1_registry,
    )
    _atomic_write_text(args.out, json.dumps(result, indent=2, sort_keys=True))
    print(f"wrote run report to {args.out} ({len(result['pairs'])} pairs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
