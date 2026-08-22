"""run8/s7_export_manifest.py — ONNX を**その由来の checkpoint へ縛る**記録（PR #303 第 2 巡 P1）。

## 塞いでいる穴

レンダ側（`s7_calib_render.py` / `s7_0b_probe.py`）は checkpoint を
**pin 照合するだけ**で、実際に読み込む ONNX とは何も結び付いていなかった:

- `s7_calib_render.py` は `--ckpt` を事前登録 sha と照合するが、推論に使うのは
  `--acoustic-onnx` であり、両者は無関係。正しい `--ckpt` と**別物の ONNX** を
  渡せば、「事前登録 checkpoint で測った」と名乗る spec を凍結できた
- `s7_0b_probe.py` はさらに弱く、`--acoustic-dir` / `--acoustic-stem` に
  `--generation` ラベルを**貼るだけ**だった（probe spec は世代ごとに
  `checkpoint_sha256` を pin しているのに、それを使っていなかった）

## なぜ ONNX の sha を事前登録に pin できないか

**ONNX は checkpoint から決定論的に再生成できない**。同じ ckpt を再 export すると
バイト列が変わり、そこからのレンダも 1e-8 桁で動く（実測 =
`s7_reproducibility_finding.md` / `s7_b1_1_2_provenance_finding.md`）。したがって
「事前登録に ONNX の sha を書いておく」方式は成立しない。

代わりに **export した時点で** ckpt → 生成物の対応を記録し、レンダ側はその記録を
照合する。記録が無ければ**測らない**（fail-closed）。

## 書式（`s7_onnx_export_manifest/0.1`）

```json
{
  "schema": "s7_onnx_export_manifest/0.1",
  "generation": "run7",
  "source_checkpoint": {"path": "...", "sha256": "...", "bytes": 556022498},
  "exporter": {"repo": "openvpi/DiffSinger", "revision": "e2307b1"},
  "environment": {"python": "...", "numpy": "...", "torch": "...", "onnx": "..."},
  "artifacts": {"acoustic_onnx": {"path": "...", "sha256": "...", "bytes": ...}, ...}
}
```
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import s7_io  # noqa: E402

EXPORT_MANIFEST_SCHEMA = "s7_onnx_export_manifest/0.1"

#: `--exporter-revision` の既定。run 8 の全 export はこの版で行った。
DEFAULT_EXPORTER = {"repo": "openvpi/DiffSinger", "revision": "e2307b1"}


class ExportManifestError(ValueError):
    """export manifest が checkpoint と生成物を結び付けていない（fail-closed）。"""


def _entry(path: Path) -> Dict[str, Any]:
    raw, sha, size = s7_io.read_bytes_with_pin(Path(path))
    del raw
    return {"path": str(Path(path).resolve()), "sha256": sha, "bytes": size}


def _versions() -> Dict[str, Optional[str]]:
    import importlib.metadata as md

    out: Dict[str, Optional[str]] = {"python": platform.python_version()}
    for pkg in ("numpy", "torch", "onnx"):
        try:
            out[pkg] = md.version(pkg)
        except Exception:  # noqa: BLE001 — 版が取れないこと自体を記録に残す
            out[pkg] = None
    return out


def build_manifest(
    generation: str,
    checkpoint: Path,
    artifacts: Mapping[str, Path],
    exporter: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """export 直後に呼ぶ。ckpt と生成物の対応をその場で固定する。"""
    return {
        "schema": EXPORT_MANIFEST_SCHEMA,
        "generation": str(generation),
        "source_checkpoint": _entry(Path(checkpoint)),
        "exporter": dict(exporter or DEFAULT_EXPORTER),
        "environment": _versions(),
        "artifacts": {str(k): _entry(Path(v)) for k, v in sorted(artifacts.items())},
    }


def verify_export_manifest(
    manifest_path: Path,
    generation: str,
    expected_checkpoint_sha256: str,
    artifacts: Mapping[str, Path],
) -> Dict[str, Any]:
    """**レンダ前に**呼ぶ。1 つでも外れたらレンダしない。

    照合するのは 4 点:

    1. schema
    2. `generation` == 呼び出し側が名乗る世代（ラベルの貼り替えを止める）
    3. `source_checkpoint.sha256` == 事前登録が pin する checkpoint の sha
    4. 実際に読み込む各ファイルの sha == manifest の記録

    4 は「manifest に書いてあること」ではなく「**いまディスクに在るバイト列**」を
    数える。manifest を持ってきても中身が別物なら止まる。
    """
    doc, manifest_sha, _ = s7_io.read_json_with_pin(Path(manifest_path))
    if str(doc.get("schema")) != EXPORT_MANIFEST_SCHEMA:
        raise ExportManifestError(
            f"schema {doc.get('schema')!r} != {EXPORT_MANIFEST_SCHEMA!r}"
        )
    if str(doc.get("generation")) != str(generation):
        raise ExportManifestError(
            f"export manifest の世代 {doc.get('generation')!r} が "
            f"レンダの世代 {generation!r} と違う"
        )
    got_ckpt = str((doc.get("source_checkpoint") or {}).get("sha256"))
    if got_ckpt != str(expected_checkpoint_sha256):
        raise ExportManifestError(
            f"export 元 checkpoint {got_ckpt} が事前登録の pin "
            f"{expected_checkpoint_sha256} と違う"
        )
    recorded = doc.get("artifacts") or {}
    for name, path in sorted(artifacts.items()):
        if name not in recorded:
            raise ExportManifestError(
                f"{name}: export manifest に記録が無い（この ONNX の由来を言えない）"
            )
        _, observed, _ = s7_io.read_bytes_with_pin(Path(path))
        expected = str(recorded[name].get("sha256"))
        if observed != expected:
            raise ExportManifestError(
                f"{name}: {path} の sha256 {observed} != export 記録 {expected}"
            )
    return {
        "manifest_path": str(Path(manifest_path).resolve()),
        "manifest_sha256": manifest_sha,
        "generation": str(doc["generation"]),
        "source_checkpoint_sha256": got_ckpt,
        "exporter": doc.get("exporter"),
        "environment": doc.get("environment"),
        "verified_artifacts": sorted(artifacts),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="export 直後に ckpt→生成物の対応を固定する")
    ap.add_argument("--generation", required=True)
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--exporter-revision", default=DEFAULT_EXPORTER["revision"])
    ap.add_argument(
        "--artifact", action="append", default=[], metavar="NAME=PATH",
        help="記録する生成物（例: acoustic_onnx=/path/to/x.onnx）。複数可",
    )
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    artifacts: Dict[str, Path] = {}
    for item in args.artifact:
        if "=" not in item:
            ap.error(f"--artifact は NAME=PATH 形式: {item!r}")
        name, _, path = item.partition("=")
        artifacts[name] = Path(path)
    if not artifacts:
        ap.error("--artifact を 1 つ以上指定すること")

    out = Path(args.out)
    s7_io.reject_output_collision([out], [Path(args.checkpoint), *artifacts.values()])
    doc = build_manifest(
        args.generation,
        Path(args.checkpoint),
        artifacts,
        {**DEFAULT_EXPORTER, "revision": args.exporter_revision},
    )
    s7_io.assert_json_finite(doc)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"| export manifest: {out}  ckpt={doc['source_checkpoint']['sha256'][:16]}")
    for name, e in doc["artifacts"].items():
        print(f"  {name:24s} {e['sha256'][:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
