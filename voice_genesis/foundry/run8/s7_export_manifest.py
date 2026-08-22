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

- `witnessed_export` — **空の出力先**に対して exporter を起動し、その実行で
  **現れたファイル**を hash した。さらに `source_checkpoint` は独立に渡された
  パスではなく、**exporter がその起動で開くファイルとして導出した**もの
  （PR #303 第 4 巡 P1）。ckpt と生成物が別々の主張にならない
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


def _build(
    generation: str,
    checkpoint: Path,
    artifacts: Mapping[str, Path],
    exporter: Optional[Mapping[str, str]],
    binding_evidence: str,
    export_command: Optional[Sequence[str]] = None,
    source_config: Optional[Path] = None,
) -> Dict[str, Any]:
    doc: Dict[str, Any] = {
        "schema": EXPORT_MANIFEST_SCHEMA,
        "generation": str(generation),
        "binding_evidence": binding_evidence,
        "source_checkpoint": _entry(Path(checkpoint)),
        "exporter": dict(exporter or DEFAULT_EXPORTER),
        "environment": _versions(),
        "artifacts": {str(k): _entry(Path(v)) for k, v in sorted(artifacts.items())},
    }
    if source_config is not None:
        doc["source_config"] = _entry(Path(source_config))
    if export_command is not None:
        doc["export_command"] = [str(x) for x in export_command]
    return doc


def build_manifest(
    generation: str,
    checkpoint: Path,
    artifacts: Mapping[str, Path],
    exporter: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """既に在るファイル群を後から hash した**記録のみ**を作る。

    **`witnessed_export` はここからは作れない**（PR #303 第 5 巡 P1）。
    `binding_evidence` を引数で受け取っていた頃は、`_run_witnessed_export` を
    private にしても `build_manifest(..., binding_evidence=WITNESSED)` で
    同じ偽の由来を名乗れた（私自身のテスト fixture がそうしていた）。
    観測済みを名乗れるのは exporter を実際に起動する経路だけにする。
    """
    return _build(generation, checkpoint, artifacts, exporter, UNWITNESSED)


def _artifact_inside(out_dir: Path, rel: str) -> Path:
    """宣言された成果物が**この起動で作った出力先の中**に閉じていることを課す。

    絶対パスや `../` を許すと、`out_dir / rel` が外へ出て、**export とは無関係な
    既存ファイル**が `is_file()` を満たして witnessed 成果物として認証される
    （PR #303 第 5 巡 P1）。連結の検査は `s7_io.child_path` に 1 本化してある
    （同型が run8 の 7 箇所にあったため）。
    """
    try:
        return s7_io.child_path(Path(out_dir), rel)
    except s7_io.PathEscapeError as exc:
        raise ExportManifestError(str(exc)) from exc


def _run_witnessed_export(
    generation: str,
    checkpoint: Path,
    out_dir: Path,
    artifact_names: Mapping[str, str],
    command: Sequence[str],
    checkpoint_derivation: str,
    exporter: Optional[Mapping[str, str]] = None,
    cwd: Optional[Path] = None,
    source_config: Optional[Path] = None,
) -> Dict[str, Any]:
    """**exporter をこのプロセスが起動して**、その実行で現れた物を記録する。

    **private**。公開の入口は `export_diffsinger_acoustic()` だけである
    （PR #303 第 4 巡 P1: 任意の `command` と任意の `checkpoint` を独立に受け取ると、
    ckpt を一切読まないコマンドでも `witnessed_export` を名乗れてしまう。
    呼び出し側が両方を勝手に組み合わせられる限り、束縛は主張のままになる）。

    因果を見たと言うために 3 つ課す:

    1. `out_dir` は**存在しないか空**であること（既に在る物を「今できた」と
       言わせない）
    2. `command` の実行が成功すること
    3. 実行後に `artifact_names` が指す全ファイルが**現れている**こと

    `checkpoint_derivation` は「なぜこの checkpoint がこの command の入力だと
    言えるのか」を人が読める形で残す。導出できない呼び出しは公開しない。
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
        path = _artifact_inside(out_dir, rel)
        if not path.is_file():
            missing.append(rel)
        artifacts[name] = path
    if missing:
        raise ExportManifestError(
            f"export 後に現れなかった生成物がある: {missing}（因果を主張できない）"
        )
    doc = _build(
        generation, Path(checkpoint), artifacts, exporter, WITNESSED,
        export_command=list(command), source_config=source_config,
    )
    doc["checkpoint_derivation"] = checkpoint_derivation
    return doc


# --- 公開の入口: DiffSinger acoustic ------------------------------------------


def diffsinger_checkpoint_path(exporter_root: Path, exp: str, ckpt_steps: int) -> Path:
    """`scripts/export.py acoustic --exp EXP --ckpt STEPS` が**開くファイル**（staging 先）。

    DiffSinger の exporter は checkpoint の**パスを受け取らない**。`--exp`（
    `<root>/checkpoints/` 配下のフォルダ名）と `--ckpt`（ステップ数）から自分で
    解決する。したがって「checkpoint を command へ渡す」ことはできず、代わりに
    **exporter と同じ規則で解決した先**を `source_checkpoint` にする。
    """
    return _exp_dir(Path(exporter_root), exp) / f"model_ckpt_steps_{int(ckpt_steps)}.ckpt"


def _exp_dir(exporter_root: Path, exp: str) -> Path:
    """`<root>/checkpoints/<exp>` を返す。ただし **checkpoints の中に閉じている**ときだけ。

    `--exp` が絶対パスや `../` を含むと連結が checkpoints の外へ出て、staging の
    書き込みが**無関係な `model_ckpt_steps_<N>.ckpt` と `config.yaml` を上書き**
    する（PR #303 第 6 巡 P1）。CLI は「厳密なフォルダ名」と説明していたのに、
    その不変条件を強制していなかった。封じ込めは `s7_io.child_path` に 1 本化
    してあるのでそれを使う。
    """
    try:
        return s7_io.child_path(Path(exporter_root) / "checkpoints", exp)
    except s7_io.PathEscapeError as exc:
        raise ExportManifestError(f"--exp {exp!r}: {exc}") from exc


def export_diffsinger_acoustic(
    generation: str,
    exporter_root: Path,
    exp: str,
    ckpt_steps: int,
    ckpt_dir: Path,
    out_dir: Path,
    artifact_names: Mapping[str, str],
    python_executable: str,
    expected_checkpoint_sha256: Optional[str] = None,
    expected_config_sha256: Optional[str] = None,
    exporter: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """DiffSinger acoustic を export し、**exporter が消費する入力全部**を束縛する。

    exporter の入力は checkpoint だけではない。`config.yaml` が checkpoint の隣に
    置かれ、同じく exporter に読まれる（`s1_gate/gate_synth.py` の staging と同型）。
    したがって正しい checkpoint の隣に**古い / 書き換えられた config** を置けば、
    別の成果物が出るのに manifest は事前登録 checkpoint を名乗る
    （PR #303 第 5 巡 P1）。

    そこで staging をこの関数が行い、**一度読んだバイト列**を

    1. sha256 の計算
    2. `checkpoints/<exp>/` への書き出し（exporter が実際に開く実体）

    の両方へ使う。記録したハッシュ = exporter が消費したバイト列、を構造的に
    保証する（`gate_synth._read_bytes_and_sha256` と同じ規律。review #264 R20 P2）。

    `--exp` のフォルダ名は**厳密**に扱う。DiffSinger の `find_exp` は名前が合わない
    とき前方一致で別の実験へ落ちるため、staging 先を自分で作って exact-name 分岐
    しか通らないようにする。
    """
    exporter_root, ckpt_dir, out_dir = Path(exporter_root), Path(ckpt_dir), Path(out_dir)
    src_ckpt = ckpt_dir / f"model_ckpt_steps_{int(ckpt_steps)}.ckpt"
    src_config = ckpt_dir / "config.yaml"
    for path in (src_ckpt, src_config):
        if not path.is_file():
            raise ExportManifestError(f"{path} が無い（exporter の入力が揃っていない）")

    ckpt_bytes, ckpt_sha, _ = s7_io.read_bytes_with_pin(src_ckpt)
    config_bytes, config_sha, _ = s7_io.read_bytes_with_pin(src_config)
    for label, observed, expected in (
        ("checkpoint", ckpt_sha, expected_checkpoint_sha256),
        ("config.yaml", config_sha, expected_config_sha256),
    ):
        if expected is not None and observed != str(expected):
            raise ExportManifestError(
                f"{label}: sha256 {observed} != 事前登録 pin {expected}"
            )

    staged = _exp_dir(exporter_root, exp)
    staged.mkdir(parents=True, exist_ok=True)
    staged_ckpt = staged / src_ckpt.name
    staged_config = staged / "config.yaml"
    staged_ckpt.write_bytes(ckpt_bytes)
    staged_config.write_bytes(config_bytes)

    command = [
        str(python_executable), str(exporter_root / "scripts" / "export.py"), "acoustic",
        "--exp", str(exp), "--ckpt", str(int(ckpt_steps)), "--out", str(out_dir),
    ]
    return _run_witnessed_export(
        generation, staged_ckpt, out_dir, artifact_names, command,
        checkpoint_derivation=(
            f"この関数が {src_ckpt} と {src_config} を一度読み、その同じバイト列を "
            f"checkpoints/{exp}/ へ書き出してから "
            f"scripts/export.py acoustic --exp {exp} --ckpt {ckpt_steps} を起動した。"
            "記録した sha256 は exporter が開いた実体そのもの"
        ),
        exporter=exporter, cwd=exporter_root, source_config=staged_config,
    )


def verify_export_manifest(
    manifest_path: Path,
    generation: str,
    expected_checkpoint_sha256: str,
    artifacts: Mapping[str, Path],
    allow_unwitnessed: bool = False,
    expected_config_sha256: Optional[str] = None,
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
    if expected_config_sha256 is not None:
        got_config = str((doc.get("source_config") or {}).get("sha256"))
        if got_config != str(expected_config_sha256):
            raise ExportManifestError(
                f"export 時の config.yaml {got_config} が pin "
                f"{expected_config_sha256} と違う"
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
        "source_config_sha256": (doc.get("source_config") or {}).get("sha256"),
        "checkpoint_derivation": doc.get("checkpoint_derivation"),
        "exporter": doc.get("exporter"),
        "environment": doc.get("environment"),
        "verified_artifacts": sorted(artifacts),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="DiffSinger acoustic を export し、開かれる ckpt へ束縛して記録する",
        epilog=(
            "例: venv/bin/python s7_export_manifest.py --generation run7 "
            "--exporter-root materials/DiffSinger --exp run7_phase_b --ckpt-steps 40000 "
            "--expected-checkpoint-sha256 518df090... --out-dir out/onnx_run7 "
            "--artifact acoustic_onnx=s6_run7_acoustic.onnx --out out/onnx_run7/export.json"
        ),
    )
    ap.add_argument("--generation", required=True)
    ap.add_argument(
        "--exporter-root", required=True,
        help="DiffSinger の checkout。checkpoint はここから **exporter と同じ規則で** "
             "導出する（独立にパスを受け取らない = PR #303 第 4 巡 P1）",
    )
    ap.add_argument("--exp", required=True, help="checkpoints/ 配下の**厳密な**フォルダ名")
    ap.add_argument("--ckpt-steps", required=True, type=int)
    ap.add_argument(
        "--ckpt-dir", required=True,
        help="model_ckpt_steps_<N>.ckpt と config.yaml が在るディレクトリ。"
             "この 2 つを一度読み、同じバイト列を checkpoints/<exp>/ へ staging する",
    )
    ap.add_argument("--expected-config-sha256", default=None)
    ap.add_argument(
        "--expected-checkpoint-sha256", default=None,
        help="事前登録が pin する checkpoint の sha256（照合してから export する）",
    )
    ap.add_argument(
        "--out-dir", required=True,
        help="exporter の出力先。**存在しないか空**でなければ止める",
    )
    ap.add_argument("--exporter-revision", default=DEFAULT_EXPORTER["revision"])
    ap.add_argument(
        "--artifact", action="append", default=[], metavar="NAME=RELPATH",
        help="出力先からの相対パスで、export が作るはずの物を宣言する。複数可",
    )
    ap.add_argument("--out", required=True, help="書き出す export manifest")
    args = ap.parse_args(argv)

    names: Dict[str, str] = {}
    for item in args.artifact:
        if "=" not in item:
            ap.error(f"--artifact は NAME=RELPATH 形式: {item!r}")
        name, _, rel = item.partition("=")
        names[name] = rel
    if not names:
        ap.error("--artifact を 1 つ以上指定すること")

    out = Path(args.out)
    ckpt_dir = Path(args.ckpt_dir)
    s7_io.reject_output_collision(
        [out], [ckpt_dir / f"model_ckpt_steps_{args.ckpt_steps}.ckpt", ckpt_dir / "config.yaml"]
    )
    doc = export_diffsinger_acoustic(
        args.generation,
        Path(args.exporter_root),
        args.exp,
        args.ckpt_steps,
        ckpt_dir,
        Path(args.out_dir),
        names,
        sys.executable,
        args.expected_checkpoint_sha256,
        args.expected_config_sha256,
        {**DEFAULT_EXPORTER, "revision": args.exporter_revision},
    )
    s7_io.assert_json_finite(doc)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"| export manifest: {out}  evidence={doc['binding_evidence']}  "
        f"ckpt={doc['source_checkpoint']['sha256'][:16]}"
    )
    print(f"  derivation: {doc['checkpoint_derivation']}")
    for name, e in doc["artifacts"].items():
        print(f"  {name:24s} {e['sha256'][:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
