"""run_melody_observability.py — M1 観測成立帯域ハーネス（評価 CLI スケルトン）。

全 fixture × 全経路について `melody.observability` のゲート指標を採取し、
「どの経路がどの入力帯で `sufficient` になるか」の表を JSON で書き出す。
**比較（M3）は行わない**——観測が成立するか否かだけを測る（設計 §4）。

2 つのモード:

- 合成 fixture（既定・CI 安全）: `tests/fixtures/melody_bench/synthesis_specs.yaml`
  を決定論合成し、その `input_kind` の経路のうち**利用可能な抽出器を持つ経路だけ**
  を回す。本環境で回るのは pyin 経路（core librosa）のみ。CREPE / Melodia /
  Demucs 経路は optional 依存が未導入なら `unavailable` として表に記録する
  （fail ではない・slow-lane 隔離）。
- 外部素材（`--external <manifest.json>`）: 正解 MIDI を持たない実素材
  （Suno vocals stem 等）の観測可能性のみを測る slow/manual lane 用。

使い方::

    python scripts/run_melody_observability.py --out /tmp/melody_obs.json
    python scripts/run_melody_observability.py --external ext.json --out /tmp/ext_obs.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import soundfile as sf
import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from build_melody_bench import build_signal, load_specs  # noqa: E402

from svp_rpe.melody.extractors import observe_assist_notes, observe_via_route  # noqa: E402
from svp_rpe.melody.observability import (  # noqa: E402
    ObservabilityThresholds,
    assess_observability,
)
from svp_rpe.melody.routing import select_routes  # noqa: E402
from svp_rpe.rpe.learned import LearnedModelUnavailable  # noqa: E402

REGISTRY_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "registry.yaml"

# 本ハーネスが解釈できる registry スキーマ契約。新スキーマで閾値の意味論が
# 変わった場合に v0.1 の解釈で結果を publish しないよう、registry を消費する前に
# fail-closed で検証する（Codex 指摘・AGENTS §8）。
_EXPECTED_REGISTRY_SCHEMA = "melody-bench/0.1"


def _require_registry_schema(registry: Dict[str, Any]) -> None:
    version = registry.get("schema_version")
    if version != _EXPECTED_REGISTRY_SCHEMA:
        raise ValueError(
            f"unsupported melody_bench registry schema_version {version!r}; "
            f"expected {_EXPECTED_REGISTRY_SCHEMA} (fail-closed)"
        )


def _unique_id_map(entries: List[Dict[str, Any]], where: str) -> Dict[str, str]:
    """`entries` の id → input_kind マップを、重複 id を fail-closed で作る。

    dict 内包表記は重複 id を黙って last-wins で上書きするため、事前登録が
    曖昧（同一 id に別 input_kind）でも slow-lane 実行が通ってしまう。重複を
    検出して reject し、曖昧な事前登録の下に観測を publish させない（Codex 指摘）。
    """
    ids = [entry["id"] for entry in entries]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(
            f"duplicate fixture id(s) in registry.yaml {where}: {duplicates} (fail-closed)"
        )
    return {entry["id"]: entry["input_kind"] for entry in entries}

# 抽出器名 → PyPI distribution 名（provenance の installed version 採取用）。
_EXTRACTOR_DIST = {
    "pyin": "librosa",
    "crepe": "crepe",
    "melodia": "essentia",
    "basic_pitch": "basic-pitch",
}


def _dist_version(dist: str) -> "str | None":
    """PyPI distribution の installed version を best-effort で返す（未導入なら None）。"""
    import importlib.metadata as _md

    try:
        return _md.version(dist)
    except Exception:
        return None


def _extractor_version(extractor: str) -> "str | None":
    """抽出器の installed package version を best-effort で返す（未導入なら None）。"""
    dist = _EXTRACTOR_DIST.get(extractor)
    return _dist_version(dist) if dist else None


def _preprocessing_provenance(route: Any) -> "Dict[str, Any] | None":
    """分離前処理（Demucs）の provenance。分離不要な経路は None。

    同一 audio_sha256 でも Demucs のパッケージ/モデル/重みが違えば vocals stem が
    変わり下流のピッチ結果も変わるため、`requires_separation` 行に分離器の
    モデル名と installed version を記録する（Codex 指摘・AGENTS §8）。
    stem hash レベルの provenance は observe_via_route が stem を露出する必要が
    あり、Demucs 不在では検証できないため本 PR では見送る。
    """
    if not getattr(route, "requires_separation", False):
        return None
    from svp_rpe.io.source_separator import DEFAULT_MODEL

    return {
        "preprocessing": route.preprocessing,
        "separation_model": DEFAULT_MODEL,
        "separation_version": _dist_version("demucs"),
    }


def load_thresholds(registry_path: Path = REGISTRY_PATH) -> ObservabilityThresholds:
    with open(registry_path, "r", encoding="utf-8") as handle:
        registry = yaml.safe_load(handle)
    _require_registry_schema(registry)
    return ObservabilityThresholds.from_registry(registry["observation_gate"])


def _run_routes_on_file(
    audio_path: str,
    input_kind: str,
    thresholds: ObservabilityThresholds,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for route in select_routes(input_kind):
        if not route.applies:
            # 旋律不在入力: 抽出せず not_observed へ落とす（設計 §4.2）。
            rows.append(
                {
                    "route": route.name,
                    "extractor": route.extractor,
                    "outcome": "not_observed_by_routing",
                    "report": None,
                }
            )
            continue
        preprocessing = _preprocessing_provenance(route)
        try:
            observation = observe_via_route(audio_path, route)
        except LearnedModelUnavailable as exc:
            unavailable_row: Dict[str, Any] = {
                "route": route.name,
                "extractor": route.extractor,
                "outcome": "unavailable",
                "detail": str(exc).splitlines()[0],
                "report": None,
            }
            if preprocessing is not None:
                unavailable_row["preprocessing"] = preprocessing
            rows.append(unavailable_row)
            continue
        # assist 抽出器が宣言されていれば（full_mix の basic-pitch × Melodia など）、
        # 補助抽出器を同一音声に走らせて reference notes を採り、cross_extractor_
        # agreement を実測する（設計 §4.2「一致時のみ」）。assist が未導入なら
        # agreement は null のまま（graceful・slow-lane 隔離）。
        reference_notes = None
        assist_status = None
        assist_source_model = None
        if route.assist:
            try:
                reference_notes, assist_source_model = observe_assist_notes(
                    audio_path, route, thresholds
                )
                assist_status = "measured"
            except LearnedModelUnavailable:
                assist_status = "unavailable"
        report = assess_observability(
            observation, thresholds, reference_notes=reference_notes
        )
        # provenance: 同一 audio_sha256 でも抽出器ビルド/モデル差で結果が変わりうる
        # ため、主・補助抽出器の source_model と installed version を行に記録する。
        row: Dict[str, Any] = {
            "route": route.name,
            "extractor": route.extractor,
            "outcome": report.status,
            "report": report.to_dict(),
            "source_model": observation.source_model,
            "extractor_version": _extractor_version(route.extractor),
        }
        if preprocessing is not None:
            row["preprocessing"] = preprocessing
        if route.assist:
            row["assist_extractor"] = route.assist
            row["assist_status"] = assist_status
            row["assist_source_model"] = assist_source_model
            row["assist_extractor_version"] = _extractor_version(route.assist)
        rows.append(row)
    return rows


def run_synthetic(thresholds: ObservabilityThresholds) -> Dict[str, Any]:
    specs = load_specs()
    with open(REGISTRY_PATH, "r", encoding="utf-8") as handle:
        registry = yaml.safe_load(handle)
    _require_registry_schema(registry)
    fixture_kinds = _unique_id_map(registry["fixtures"], "fixtures")
    expect = {f["id"]: f.get("expect_status") for f in registry["fixtures"]}

    # fail-closed: 全ての合成 spec id は registry に事前登録されていなければならない。
    # 未登録 id を既定の input_kind へ黙って分類すると、事前登録の期待値を持たない
    # ケースが Go/No-Go 出力へ紛れ込む（設計 §5 事前登録厳守）。推論せず reject する。
    unregistered = [fid for fid in specs["fixtures"] if fid not in fixture_kinds]
    if unregistered:
        raise ValueError(
            f"synthesis spec ids without a registry.yaml fixtures entry: {unregistered}. "
            "全ての spec id を registry へ事前登録すること（input_kind 推論は禁止）。"
        )

    results: Dict[str, Any] = {"mode": "synthetic", "fixtures": {}}
    with tempfile.TemporaryDirectory(prefix="melody-bench-") as tmp:
        for fid in specs["fixtures"]:
            y, sr = build_signal(fid, specs)
            wav_path = Path(tmp) / f"{fid}.wav"
            sf.write(wav_path, y, sr, subtype="FLOAT")
            input_kind = fixture_kinds[fid]
            rows = _run_routes_on_file(str(wav_path), input_kind, thresholds)
            results["fixtures"][fid] = {
                "input_kind": input_kind,
                "expect_status": expect.get(fid),
                "routes": rows,
            }
    return results


def run_external(manifest_path: Path, thresholds: ObservabilityThresholds) -> Dict[str, Any]:
    """外部素材 manifest（[{id, path, input_kind, audio_sha256?}]）の観測可能性を測る。

    provenance（AGENTS §8）: どの bytes を観測したかを後の slow-lane 実測が証明
    できるよう、各素材の実パスと content hash（audio_sha256）を出力へ記録する。
    manifest が期待 hash を持つ場合は照合し、不一致なら fail-closed で reject
    （同一 id で別 WAV が差し替わる silent swap を防ぐ）。manifest 自体の hash も
    記録する。
    """
    # manifest の bytes を一度だけ読み、その bytes を hash し、同じ buffer から
    # JSON を parse する。別々に open すると、pin する manifest_sha256 と実際に
    # entries を供給した manifest がズレる TOCTOU が残る（Codex 指摘。audio bytes
    # の凍結と同型）。
    manifest_bytes = Path(manifest_path).read_bytes()
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    entries = json.loads(manifest_bytes)
    with open(REGISTRY_PATH, "r", encoding="utf-8") as handle:
        registry = yaml.safe_load(handle)
    _require_registry_schema(registry)

    # fail-closed: 各 manifest entry の id は registry.yaml の external_fixtures に
    # 事前登録され、input_kind も登録値と一致していなければならない。typo や
    # ミスラベル（例: suno_vocals_stem を clear_lead と誤記）で誤った経路集合を
    # 走らせ、未登録/不整合な fixture の下に一見妥当な観測を publish するのを防ぐ
    # （合成側 fail-closed と対称・設計 §5）。
    registered = _unique_id_map(registry.get("external_fixtures", []), "external_fixtures")
    seen_ids: set[str] = set()

    # 相対 path は manifest の位置を基準に解決する。cwd 基準だと、可搬 manifest を
    # 別ディレクトリから起動したとき launch dir の同名ファイルを観測して一見妥当な
    # 結果を publish しうる（Codex 指摘）。解決後の正規化パスを記録する。
    manifest_dir = Path(manifest_path).resolve().parent

    results: Dict[str, Any] = {
        "mode": "external",
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_sha256,
        "fixtures": {},
    }
    with tempfile.TemporaryDirectory(prefix="melody-ext-") as tmp:
        for entry in entries:
            entry_id = entry["id"]
            if entry_id in seen_ids:
                raise ValueError(f"duplicate external fixture id {entry_id!r} in manifest")
            seen_ids.add(entry_id)
            if entry_id not in registered:
                raise ValueError(
                    f"external fixture id {entry_id!r} is not pre-registered in "
                    "registry.yaml external_fixtures (fail-closed)"
                )
            if entry["input_kind"] != registered[entry_id]:
                raise ValueError(
                    f"external fixture {entry_id!r} input_kind {entry['input_kind']!r} "
                    f"!= registered {registered[entry_id]!r}"
                )

            raw_path = Path(entry["path"])
            resolved = raw_path if raw_path.is_absolute() else (manifest_dir / raw_path)
            resolved = resolved.resolve()

            # 観測する bytes と pin する hash を一致させる: entry のバイト列を一度だけ
            # 読み、その hash を取り、**同じバイト列**を temp file へ凍結して観測する。
            # 別々に 2 回 open すると、間にファイルが再生成・差し替えられた場合に pin
            # と観測波形がズレる TOCTOU が残る（Codex 指摘）。
            data = resolved.read_bytes()
            audio_sha256 = hashlib.sha256(data).hexdigest()
            expected = entry.get("audio_sha256")
            if expected is not None and expected != audio_sha256:
                raise ValueError(
                    f"external audio {entry_id!r} sha256 mismatch: "
                    f"{audio_sha256} != manifest {expected}"
                )
            frozen = Path(tmp) / f"{entry_id}{resolved.suffix or '.wav'}"
            frozen.write_bytes(data)

            rows = _run_routes_on_file(str(frozen), entry["input_kind"], thresholds)
            results["fixtures"][entry_id] = {
                "input_kind": entry["input_kind"],
                "expect_status": None,  # 正解なし実素材（観測可能性のみ）
                "audio_path": str(resolved),  # 正規化パス
                "audio_sha256": audio_sha256,
                "routes": rows,
            }
    return results


def summarize(results: Dict[str, Any]) -> List[str]:
    lines = [f"# melody observability ({results['mode']} mode)"]
    for fid, info in results["fixtures"].items():
        lines.append(f"\n## {fid}  (input_kind={info['input_kind']}, expect={info['expect_status']})")
        for row in info["routes"]:
            report = row.get("report")
            detail = ""
            if report and report.get("reasons"):
                detail = "  reasons=" + "; ".join(report["reasons"])
            lines.append(f"  - {row['route']:<28} [{row['extractor']:<11}] -> {row['outcome']}{detail}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="観測表 JSON の出力先")
    parser.add_argument(
        "--external", type=Path, help="外部素材 manifest（正解なし実素材の観測可能性）"
    )
    args = parser.parse_args()

    thresholds = load_thresholds()
    if args.external is not None:
        results = run_external(args.external, thresholds)
    else:
        results = run_synthetic(thresholds)

    for line in summarize(results):
        print(line)
    if args.out is not None:
        args.out.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
