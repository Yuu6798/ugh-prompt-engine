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

## 「対応を記録する」だけでは足りない（PR #303 第 3 巡 P1）

2 つのパスを**別々に hash して並べただけ**の記録は、観測していない関係を証明した
ことにする。正しい ckpt と無関係な ONNX を渡せば、`verify_export_manifest` が
受理する manifest をその場で作れてしまう。したがって記録は
**export を実行したプロセス自身が出す**必要がある。

本モジュールは束縛の由来を `binding_evidence` として記録し、レンダ側は既定で
`witnessed_export` しか受け付けない:

- `witnessed_export` — `export` サブコマンドが、**空の出力先**に対して exporter を
  起動し、その実行で**現れたファイル**を hash した。ckpt → 生成物の因果を
  このプロセスが見ている
- `unwatched_post_hoc` — 既に在るファイル群を後から hash しただけ。
  **レンダ側は既定で拒否する**（`allow_unwitnessed=True` を明示したときだけ通る）

## 書式（`s7_onnx_export_manifest/0.1`）

```json
{
  "schema": "s7_onnx_export_manifest/0.1",
  "generation": "run7",
  "binding_evidence": "witnessed_export",
  "export_command": ["python", "scripts/export.py", "acoustic", "--exp", "..."],
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


WITNESSED = "witnessed_export"
UNWITNESSED = "unwitnessed_post_hoc"


def build_manifest(
    generation: str,
    checkpoint: Path,
    artifacts: Mapping[str, Path],
    exporter: Optional[Mapping[str, str]] = None,
    binding_evidence: str = UNWITNESSED,
    export_command: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """ckpt と生成物の対応を固定する。

    **既定は `unwitnessed_post_hoc`**。2 つのパスを別々に hash しただけでは
    「その ONNX がその ckpt から出た」ことを見ていないので、そう名乗る。
    因果を見たと言えるのは `run_witnessed_export()` 経由のときだけである。
    """
    if binding_evidence not in (WITNESSED, UNWITNESSED):
        raise ValueError(f"unknown binding_evidence {binding_evidence!r}")
    doc: Dict[str, Any] = {
        "schema": EXPORT_MANIFEST_SCHEMA,
        "generation": str(generation),
        "binding_evidence": binding_evidence,
        "source_checkpoint": _entry(Path(checkpoint)),
        "exporter": dict(exporter or DEFAULT_EXPORTER),
        "environment": _versions(),
        "artifacts": {str(k): _entry(Path(v)) for k, v in sorted(artifacts.items())},
    }
    if export_command is not None:
        doc["export_command"] = [str(x) for x in export_command]
    return doc


def run_witnessed_export(
    generation: str,
    checkpoint: Path,
    out_dir: Path,
    artifact_names: Mapping[str, str],
    command: Sequence[str],
    exporter: Optional[Mapping[str, str]] = None,
    cwd: Optional[Path] = None,
) -> Dict[str, Any]:
    """**exporter をこのプロセスが起動して**、その実行で現れた物を記録する。

    因果を見たと言うために 3 つ課す:

    1. `out_dir` は**存在しないか空**であること（既に在る物を「今できた」と
       言わせない）
    2. `command` の実行が成功すること
    3. 実行後に `artifact_names` が指す全ファイルが**現れている**こと

    3 が揃って初めて「この ckpt を渡した export がこの生成物を作った」と言える。
    それでも「exporter が本当にその ckpt を読んだか」までは保証できないので、
    `export_command` を丸ごと記録して人が読めるようにする。
    """
    import subprocess

    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ExportManifestError(
            f"{out_dir} が空でない。既存物を『この export の産物』と名乗らせないため、"
            "空の出力先に対してのみ witnessed export を行う"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(list(command), cwd=str(cwd) if cwd else None)
    if proc.returncode != 0:
        raise ExportManifestError(
            f"exporter が非 0 で終了した (rc={proc.returncode}): {' '.join(command)}"
        )
    artifacts: Dict[str, Path] = {}
    missing = []
    for name, rel in sorted(artifact_names.items()):
        path = out_dir / rel
        if not path.is_file():
            missing.append(rel)
        artifacts[name] = path
    if missing:
        raise ExportManifestError(
            f"export 後に現れなかった生成物がある: {missing}（因果を主張できない）"
        )
    return build_manifest(
        generation, Path(checkpoint), artifacts, exporter,
        binding_evidence=WITNESSED, export_command=list(command),
    )


def verify_export_manifest(
    manifest_path: Path,
    generation: str,
    expected_checkpoint_sha256: str,
    artifacts: Mapping[str, Path],
    allow_unwitnessed: bool = False,
) -> Dict[str, Any]:
    """**レンダ前に**呼ぶ。1 つでも外れたらレンダしない。

    照合するのは 5 点:

    1. schema
    2. `binding_evidence` == `witnessed_export`（`allow_unwitnessed=True` のときだけ
       後付け記録を通す。既定で拒否するのは、2 つのパスを別々に hash しただけの
       記録が「観測していない関係」を証明したことにするため = PR #303 第 3 巡 P1）
    3. `generation` == 呼び出し側が名乗る世代（ラベルの貼り替えを止める）
    4. `source_checkpoint.sha256` == 事前登録が pin する checkpoint の sha
    5. 実際に読み込む各ファイルの sha == manifest の記録

    5 は「manifest に書いてあること」ではなく「**いまディスクに在るバイト列**」を
    数える。manifest を持ってきても中身が別物なら止まる。
    """
    doc, manifest_sha, _ = s7_io.read_json_with_pin(Path(manifest_path))
    if str(doc.get("schema")) != EXPORT_MANIFEST_SCHEMA:
        raise ExportManifestError(
            f"schema {doc.get('schema')!r} != {EXPORT_MANIFEST_SCHEMA!r}"
        )
    evidence = str(doc.get("binding_evidence"))
    if evidence != WITNESSED and not allow_unwitnessed:
        raise ExportManifestError(
            f"binding_evidence = {evidence!r}。後付けで 2 つのパスを hash しただけの "
            "記録は、観測していない関係を証明したことにする。export を実行した "
            "プロセス自身が出した記録（witnessed_export）を使うこと"
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
        "binding_evidence": evidence,
        "export_command": doc.get("export_command"),
        "source_checkpoint_sha256": got_ckpt,
        "exporter": doc.get("exporter"),
        "environment": doc.get("environment"),
        "verified_artifacts": sorted(artifacts),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="exporter をこのプロセスから起動し、その産物を ckpt へ縛る",
        epilog=(
            "例: s7_export_manifest.py --generation run7 --checkpoint ckpt "
            "--out-dir out --artifact acoustic_onnx=x.onnx --out out/export.json "
            "-- venv/bin/python scripts/export.py acoustic --exp run7"
        ),
    )
    ap.add_argument("--generation", required=True)
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument(
        "--out-dir", required=True,
        help="exporter の出力先。**存在しないか空**でなければ止める"
             "（既存物を『今 export した物』と名乗らせないため）",
    )
    ap.add_argument("--exporter-revision", default=DEFAULT_EXPORTER["revision"])
    ap.add_argument(
        "--artifact", action="append", default=[], metavar="NAME=RELPATH",
        help="出力先からの相対パスで、export が作るはずの物を宣言する"
             "（例: acoustic_onnx=s6_run7_acoustic.onnx）。複数可",
    )
    ap.add_argument("--cwd", default=None, help="exporter を起動する作業ディレクトリ")
    ap.add_argument("--out", required=True, help="書き出す export manifest")
    ap.add_argument(
        "command", nargs=argparse.REMAINDER,
        help="`--` の後ろに exporter のコマンドを丸ごと書く",
    )
    args = ap.parse_args(argv)

    names: Dict[str, str] = {}
    for item in args.artifact:
        if "=" not in item:
            ap.error(f"--artifact は NAME=RELPATH 形式: {item!r}")
        name, _, rel = item.partition("=")
        names[name] = rel
    if not names:
        ap.error("--artifact を 1 つ以上指定すること")
    command = [c for c in args.command if c != "--"]
    if not command:
        ap.error("`--` の後ろに exporter のコマンドを指定すること"
                 "（後付けで hash するだけの記録は作らない）")

    out = Path(args.out)
    s7_io.reject_output_collision([out], [Path(args.checkpoint)])
    doc = run_witnessed_export(
        args.generation,
        Path(args.checkpoint),
        Path(args.out_dir),
        names,
        command,
        {**DEFAULT_EXPORTER, "revision": args.exporter_revision},
        Path(args.cwd) if args.cwd else None,
    )
    s7_io.assert_json_finite(doc)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"| export manifest: {out}  evidence={doc['binding_evidence']}  "
        f"ckpt={doc['source_checkpoint']['sha256'][:16]}"
    )
    for name, e in doc["artifacts"].items():
        print(f"  {name:24s} {e['sha256'][:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
