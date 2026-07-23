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

from svp_rpe.melody.extractors import observe_via_route  # noqa: E402
from svp_rpe.melody.observability import (  # noqa: E402
    ObservabilityThresholds,
    assess_observability,
)
from svp_rpe.melody.routing import select_routes  # noqa: E402
from svp_rpe.rpe.learned import LearnedModelUnavailable  # noqa: E402

REGISTRY_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "registry.yaml"


def load_thresholds(registry_path: Path = REGISTRY_PATH) -> ObservabilityThresholds:
    with open(registry_path, "r", encoding="utf-8") as handle:
        registry = yaml.safe_load(handle)
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
        try:
            observation = observe_via_route(audio_path, route)
        except LearnedModelUnavailable as exc:
            rows.append(
                {
                    "route": route.name,
                    "extractor": route.extractor,
                    "outcome": "unavailable",
                    "detail": str(exc).splitlines()[0],
                    "report": None,
                }
            )
            continue
        report = assess_observability(observation, thresholds)
        rows.append(
            {
                "route": route.name,
                "extractor": route.extractor,
                "outcome": report.status,
                "report": report.to_dict(),
            }
        )
    return rows


def run_synthetic(thresholds: ObservabilityThresholds) -> Dict[str, Any]:
    specs = load_specs()
    with open(REGISTRY_PATH, "r", encoding="utf-8") as handle:
        registry = yaml.safe_load(handle)
    fixture_kinds = {f["id"]: f["input_kind"] for f in registry["fixtures"]}
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


def _sha256_file(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_external(manifest_path: Path, thresholds: ObservabilityThresholds) -> Dict[str, Any]:
    """外部素材 manifest（[{id, path, input_kind, audio_sha256?}]）の観測可能性を測る。

    provenance（AGENTS §8）: どの bytes を観測したかを後の slow-lane 実測が証明
    できるよう、各素材の実パスと content hash（audio_sha256）を出力へ記録する。
    manifest が期待 hash を持つ場合は照合し、不一致なら fail-closed で reject
    （同一 id で別 WAV が差し替わる silent swap を防ぐ）。manifest 自体の hash も
    記録する。
    """
    with open(manifest_path, "r", encoding="utf-8") as handle:
        entries = json.load(handle)
    results: Dict[str, Any] = {
        "mode": "external",
        "manifest_path": str(manifest_path),
        "manifest_sha256": _sha256_file(str(manifest_path)),
        "fixtures": {},
    }
    for entry in entries:
        audio_sha256 = _sha256_file(entry["path"])
        expected = entry.get("audio_sha256")
        if expected is not None and expected != audio_sha256:
            raise ValueError(
                f"external audio {entry['id']!r} sha256 mismatch: "
                f"{audio_sha256} != manifest {expected}"
            )
        rows = _run_routes_on_file(entry["path"], entry["input_kind"], thresholds)
        results["fixtures"][entry["id"]] = {
            "input_kind": entry["input_kind"],
            "expect_status": None,  # 正解なし実素材（観測可能性のみ）
            "audio_path": entry["path"],
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
