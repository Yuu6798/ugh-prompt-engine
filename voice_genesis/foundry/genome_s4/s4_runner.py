"""genome_s4/s4_runner.py — 正本検証・候補導出・B0/F/D/FD 生成・S3 replay（設計書 §19）。

責務は 7 つだけ。**研究判断は行わない。**

1. S3 / S3.5 正本と S3 input manifest を **1 回だけ** bytes として読む（§5 G0-6）
2. 来歴 Gate G0-1〜G0-5 を検査する
3. candidate pair を S3 正本から機械的に導出する（§4。件数を手書きしない）
4. 各 candidate pair で B0 / F / D / FD を生成する（§3）
5. S3 canonical の B0 / F / D と sample_sha256 が一致することを確認する（§6）
6. §8 の既存 metric だけを記録する
7. same-process repeat と cross-process 再計算用の出力を作る（§12）

`planb/` `planb_real/` `genome_s3/` `genome_s35/` は **read-only import**（§18）。
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import soundfile as sf

_HERE = Path(__file__).resolve().parent
_FOUNDRY = _HERE.parent
_REPO = _FOUNDRY.parent.parent
for _p in (_HERE, _FOUNDRY / "genome_s3", _FOUNDRY / "planb", _FOUNDRY / "planb_real"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pb_compose as pc  # noqa: E402
import pb_gates as pbg  # noqa: E402

import pr_ladder  # noqa: E402
# **遅延 import にしない。** `closure_digest()` は `sys.modules` を走査するので、
# 再配置検証で初めて読み込むと「素材の同一性を決める実装」が pin から漏れる。
import pr_manifest  # noqa: E402
import pr_performance as prp  # noqa: E402

import s3_runner as s3r  # noqa: E402
import s4_spec as sp  # noqa: E402
from s4_spec import S4Stop  # noqa: E402  — 後方互換の再エクスポート

S3_RESULTS = _FOUNDRY / "genome_s3" / "results" / "s3_results.json"
S35_RESULTS = _FOUNDRY / "genome_s35" / "results" / "s35_results.json"
S3_INPUT_MANIFEST = _FOUNDRY / "planb_real" / "results" / "ladder_manifest.json"
S3_SOURCE_MANIFEST = _FOUNDRY / "planb_real" / "results" / "source_manifest.json"

RESULTS = _HERE / "results"
WAV_DIR = RESULTS / "wav"
WAV_STAGING = RESULTS / "wav.staging"

#: 走行中に書き換わる可能性がある **S4 自身の出力**。clean worktree 判定から外す
#: （この走行が生む成果物を、その走行の前提条件にはできない）。コード・docs・
#: 他モジュールの汚れは除外しない。
WORKTREE_EXCLUDE_PREFIXES: Tuple[str, ...] = (
    "voice_genesis/foundry/genome_s4/results/",)


#: 走行中に通過した Gate の記録。**判定には使わない**（BLOCKED 記録の可読性のため
#: だけに残す）。どこまで進んで何で止まったかを、記録だけを見た人が追えるようにする。
_PREFLIGHT: Dict[str, Any] = {}


def preflight_record() -> Dict[str, Any]:
    return dict(_PREFLIGHT)


def reset_preflight() -> None:
    _PREFLIGHT.clear()


# ---------------------------------------------------------------------------
# §5 G0-6 — 一回読み
# ---------------------------------------------------------------------------
#: path -> (parsed, sha256)。**parse したのと同じ bytes** の digest を保持する。
_READ_ONCE: Dict[Path, Tuple[Dict[str, Any], str]] = {}


def reset_read_cache() -> None:
    """テスト用。走行中には呼ばない（G0-6 の一回読みを壊すため）。"""
    _READ_ONCE.clear()


def read_once(path: Path, what: str, minimal_fix: str) -> Tuple[Dict[str, Any], str]:
    """`path` を 1 回だけ bytes として読み、その同じ bytes から parse と SHA を返す。

    2 回目以降はキャッシュを返す。実験中に再読み込みして**別 bytes の SHA**を
    記録に載せる経路を塞ぐ（§5 G0-6）。
    """
    cached = _READ_ONCE.get(path)
    if cached is not None:
        return cached
    if not path.exists():
        raise S4Stop(cause=f"{what} が無い（{path}）",
                     impact="S4 の入力・来歴を確定できず、判定を実行できない",
                     minimal_fix=minimal_fix)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise S4Stop(cause=f"{what} が JSON として読めない: {exc}",
                     impact="正本を parse できず、来歴 Gate を実行できない",
                     minimal_fix=minimal_fix) from exc
    if not isinstance(data, dict):
        raise S4Stop(cause=f"{what} の根が object でない: {type(data).__name__}",
                     impact="正本の構造が前提と違い、来歴 Gate を実行できない",
                     minimal_fix=minimal_fix)
    _READ_ONCE[path] = (data, digest)
    return data, digest


def load_s3() -> Tuple[Dict[str, Any], str]:
    return read_once(S3_RESULTS, "S3 正本 s3_results.json",
                     "genome_s3 を実行して results/s3_results.json を用意する")


def load_s35() -> Tuple[Dict[str, Any], str]:
    return read_once(S35_RESULTS, "S3.5 正本 s35_results.json",
                     "genome_s35 を実行して results/s35_results.json を用意する")


def load_input_manifest() -> Tuple[Dict[str, Any], str]:
    return read_once(S3_INPUT_MANIFEST, "S3 input manifest ladder_manifest.json",
                     "planb_real の ladder を実行して results/ladder_manifest.json を用意する")


def load_source_manifest() -> Tuple[Dict[str, Any], str]:
    return read_once(S3_SOURCE_MANIFEST, "S2 source manifest source_manifest.json",
                     "planb_real の acquire を実行して results/source_manifest.json を用意する")


# ---------------------------------------------------------------------------
# relocatable rematerialization（User 裁定 2026-08-21）
#
# 裁定 7:「同じ絶対パス」は再現条件ではない。「同じ archive と同じ再構築 pair」が
# 再現条件である。凍結 manifest は S2 走行機の絶対パスを持つが、**それを書き換えず**、
# 走行時メモリ上でだけ新 root へ写像する。
# ---------------------------------------------------------------------------
MATERIAL_ROOTS_SCHEMA = "voicegenesis-s4-material-roots/1.0"
#: 配置定義の既定位置。`S4_MATERIAL_ROOTS` 環境変数で上書きできる。
MATERIAL_ROOTS_PATH = RESULTS / "material_roots.json"


def material_roots_path() -> Path:
    env = os.environ.get("S4_MATERIAL_ROOTS")
    return Path(env) if env else MATERIAL_ROOTS_PATH


def load_material_roots() -> Optional[Dict[str, Any]]:
    """再配置定義を読む。無ければ `None`（= 凍結 manifest の絶対パスをそのまま使う）。"""
    path = material_roots_path()
    if not path.exists():
        return None
    data, _sha = read_once(path, f"material roots 定義 ({path.name})",
                           "S4 の再配置定義を確認する")
    if data.get("schema") != MATERIAL_ROOTS_SCHEMA:
        raise S4Stop(
            cause=f"material roots 定義の schema が {data.get('schema')!r}"
                  f"（要求 {MATERIAL_ROOTS_SCHEMA!r}）",
            impact="素材の再配置定義を解釈できず、pair を再構築できない",
            minimal_fix=f"{path} の schema を確認する")
    sources = data.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise S4Stop(
            cause="material roots 定義に sources が無い（または空）",
            impact="旧 root -> 新 root の写像を作れない",
            minimal_fix=f"{path} の sources を確認する")
    return data


def _sha_dir_consumed(root: Path) -> Tuple[str, int]:
    """`pr_manifest.aggregate_extracted_sha256` と同じ集約（相対パス + 内容）。

    絶対パスを含まないので、**別 root へ展開しても同じ値**になる。
    再配置後の木が S2 走行時と同一であることを、この 1 値で検査できる。
    """
    digest, count = pr_manifest.aggregate_extracted_sha256(root)
    return str(digest), int(count)


def resolve_material_roots(config: Dict[str, Any], source_manifest: Dict[str, Any],
                           ) -> Dict[str, Any]:
    """裁定 4 の開始条件を検査し、`old_root -> new_root` の写像を作る。

    1 件でも不一致なら BLOCKED（裁定 5「合理的推測で続行しない」）。
    """
    entries = source_manifest.get("entries")
    if not isinstance(entries, dict) or not entries:
        raise S4Stop(
            cause="source_manifest.json に entries が無い",
            impact="archive SHA / 展開物 SHA の正本を引けず、再配置を検証できない",
            minimal_fix="planb_real/results/source_manifest.json を確認する")
    mapping: List[Dict[str, Any]] = []
    for source, spec in sorted(config["sources"].items()):
        entry = entries.get(source)
        if not isinstance(entry, dict):
            raise S4Stop(
                cause=f"source_manifest.json に {source!r} の記録が無い",
                impact="再配置した素材が S2 の正本素材と同一かを確認できない",
                minimal_fix="material roots 定義の source 名を source_manifest に合わせる")
        old_root = entry.get("extracted_path")
        if not isinstance(old_root, str) or not old_root:
            raise S4Stop(
                cause=f"{source}: source_manifest に extracted_path が無い",
                impact="旧 root を正本から導出できず、写像が推測になる",
                minimal_fix="source_manifest.json の extracted_path を確認する")
        new_root = spec.get("new_root")
        if not isinstance(new_root, str) or not new_root:
            raise S4Stop(
                cause=f"{source}: material roots 定義に new_root が無い",
                impact="再配置先が分からず素材を読めない",
                minimal_fix="material roots 定義に new_root を書く")
        new_path = Path(new_root).resolve()
        if not new_path.is_dir():
            raise S4Stop(
                cause=f"{source}: new_root が存在しない（{new_path}）",
                impact="再配置先に素材が無く、pair を再構築できない",
                minimal_fix="archive を new_root へ展開する")
        # 裁定 4: raw corpus は repository 外であること（規約 第3条1 の転載禁止）
        if new_path == _REPO or _REPO in new_path.parents:
            raise S4Stop(
                cause=f"{source}: new_root が repository 内にある（{new_path}）",
                impact="raw corpus が repository へ入り、歌声データベース利用規約"
                       "第3条1（転載禁止）に抵触する",
                minimal_fix="repository 外の scratch 領域へ展開し直す")
        row: Dict[str, Any] = {"source": source, "old_root": old_root,
                               "new_root": str(new_path)}
        row.update(_verify_archive(source, spec, entry))
        row.update(_verify_extracted(source, new_path, entry))
        mapping.append(row)
    return {"schema": MATERIAL_ROOTS_SCHEMA, "sources": mapping}


def _verify_archive(source: str, spec: Dict[str, Any],
                    entry: Dict[str, Any]) -> Dict[str, Any]:
    """裁定 4「archive SHA 一致」。archive を提示しない運用も明示的に記録する。"""
    want = entry.get("archive_sha256")
    archive = spec.get("archive_path")
    if not archive:
        # 展開物の集約 SHA（相対パス + 内容）が一致していれば「同じ archive から
        # 出た木」であることは示せるが、archive そのものは検査していない。
        # 黙って通さず、記録に残す。
        return {"archive_sha256": want, "archive_verified": False,
                "archive_path": None,
                "archive_note": "archive_path 未提示。展開物の集約 SHA でのみ同一性を確認"}
    path = Path(archive)
    if not path.is_file():
        raise S4Stop(
            cause=f"{source}: archive が存在しない（{path}）",
            impact="裁定 4 の開始条件「archive SHA 一致」を検査できない",
            minimal_fix="source_manifest に記録された archive を取得する")
    got = _sha_bytes_of_file(path)
    if got != want:
        raise S4Stop(
            cause=f"{source}: archive SHA が S2 正本と一致しない "
                  f"(got {got[:16]}… / pin {str(want)[:16]}…)",
            impact="S2 が使ったのと別の配布物から素材を作ることになる",
            minimal_fix="source_manifest に記録された版の archive を取得する")
    return {"archive_sha256": got, "archive_verified": True, "archive_path": str(path)}


def _verify_extracted(source: str, new_root: Path,
                      entry: Dict[str, Any]) -> Dict[str, Any]:
    """展開物の集約 SHA（相対パス + 内容）が S2 正本と一致すること。

    archive SHA だけでは展開後の 1 ファイル差し替えを検出できない。集約 SHA は
    絶対パスを含まないので、**別 root でも同じ値**になる = 再配置の正しい検査。
    """
    want_sha = entry.get("extracted_sha256")
    want_n = entry.get("extracted_file_count")
    if not want_sha:
        raise S4Stop(
            cause=f"{source}: source_manifest に extracted_sha256 が無い",
            impact="再配置した木が S2 走行時と同一かを検査できない",
            minimal_fix="source_manifest.json を確認する")
    got_sha, got_n = _sha_dir_consumed(new_root)
    if got_sha != want_sha:
        raise S4Stop(
            cause=f"{source}: 展開物の集約 SHA が S2 正本と一致しない "
                  f"(got {got_sha[:16]}…/{got_n} files / "
                  f"pin {str(want_sha)[:16]}…/{want_n} files)",
            impact="再配置した木が S2 走行時と別物であり、pair の再構築が別素材になる",
            minimal_fix="archive から展開し直す（除外規則も S2 と揃える）")
    return {"extracted_sha256": got_sha, "extracted_file_count": got_n,
            "extracted_verified": True}


def remap_pair(pair: Dict[str, Any], mapping: Dict[str, Any]) -> Dict[str, Any]:
    """pair の素材パスを新 root へ写像した **コピー**を返す。

    凍結 manifest の dict は書き換えない（正本は変更しない = 裁定 3d）。
    """
    rows = mapping.get("sources") or []
    out = dict(pair)
    for field_name in ("ritsu_file", "pjs_file"):
        old = pair.get(field_name)
        if not isinstance(old, str):
            continue
        for row in rows:
            old_root = row["old_root"].rstrip("/")
            if old == old_root or old.startswith(old_root + "/"):
                out[field_name] = row["new_root"].rstrip("/") + old[len(old_root):]
                break
        else:
            raise S4Stop(
                cause=f"{pair.get('pair_key')}: {field_name} が どの old_root にも"
                      f"当てはまらない（{old}）",
                impact="素材の写像先を決められず、推測で別ファイルを読む危険がある",
                minimal_fix="material roots 定義の source を増やす（推測で補完しない）")
    return out


def to_canonical_path(path: str, mapping: Dict[str, Any]) -> str:
    """新 root のパスを **正本（S2）の絶対パス**へ戻す（`remap_pair` の逆写像）。"""
    for row in mapping.get("sources") or []:
        new_root = row["new_root"].rstrip("/")
        if path == new_root or path.startswith(new_root + "/"):
            return row["old_root"].rstrip("/") + path[len(new_root):]
    return path


@contextlib.contextmanager
def canonical_source_labels(mapping: Optional[Dict[str, Any]]):
    """再配置時、Performance digest に入る `source_file` を正本のパスへ戻す shim。

    `RealPerformanceTrack.content_sha256()` は
    `source_id|source_file|probe_kind` をハッシュに含む
    （`planb_real/pr_performance.py`）。つまり **凍結 pin は絶対パスを
    埋め込んでいる**。素材を別 root へ再配置すると、中身が 1 bit も違わなくても
    `performance_sha256` は必ず外れる。

    User 裁定 7「『同じ絶対パス』は再現条件ではない。『同じ archive と同じ
    再構築 pair』が再現条件である」に従い、**bytes は新 root から読み、
    ラベルだけを正本のパスへ戻す**。

    ラベル以外（f0 逸脱・尺・energy・release の全配列とスカラ、音素ラベル列）は
    一切触らないので、pin 照合の強度は落ちない。むしろ pin が一致することが
    「path 以外は完全に同一」の証明になる。

    `planb_real` のファイルは変更しない（read-only）。この shim は再配置が
    有効なときだけ、この with ブロックの中でだけ効き、必ず元へ戻す。
    """
    if mapping is None:
        yield []
        return
    original = prp.extract_performance
    applied: List[Dict[str, str]] = []

    def _shim(*args: Any, **kwargs: Any):
        src = kwargs.get("source_file")
        if isinstance(src, str):
            canonical = to_canonical_path(src, mapping)
            if canonical != src:
                applied.append({"read_from": src, "labelled_as": canonical})
                kwargs["source_file"] = canonical
        return original(*args, **kwargs)

    prp.extract_performance = _shim
    try:
        yield applied
    finally:
        prp.extract_performance = original


def assert_pair_materials(pair: Dict[str, Any]) -> None:
    """裁定 4「必須 wav/lab 存在」。読む前に存在を確かめ、原因を明示して止める。"""
    for field_name in ("ritsu_file", "pjs_file"):
        lab = Path(str(pair.get(field_name)))
        if not lab.is_file():
            raise S4Stop(
                cause=f"{pair.get('pair_key')}: {field_name} が読めない（{lab}）",
                impact="candidate pair を再構築できず、母集団が縮小する",
                minimal_fix="new_root への展開が完全かを確認する（母集団は縮小しない）")


# ---------------------------------------------------------------------------
# §5 G0-1 / G0-2 / G0-3 — 正本 Gate
# ---------------------------------------------------------------------------
def gate_s3(s3: Dict[str, Any]) -> None:
    """G0-1: S3 overall PASS かつ F0 / Duration が SUPPORTED。"""
    overall = (s3.get("overall") or {}).get("verdict")
    if overall != sp.REQUIRED_S3_OVERALL:
        raise S4Stop(
            cause=f"S3 overall が {overall!r}（要求 {sp.REQUIRED_S3_OVERALL!r}）",
            impact="S4 は S3 の単独 gene 成立を入力として前提にするため開始できない",
            minimal_fix="S3 を再裁定しない。S3 正本を確認して User へ戻す")
    genes = s3.get("genes") or {}
    for gene in sp.Gene:
        v = (genes.get(gene.value) or {}).get("verdict")
        if v != sp.S3_SUPPORTED:
            raise S4Stop(
                cause=f"S3 の {gene.value} gene verdict が {v!r}（要求 {sp.S3_SUPPORTED!r}）",
                impact="複合発現の対象 gene が S3 で成立していないため S4 の問いが立たない",
                minimal_fix="S3 を再裁定しない。対象 gene の選定を User 裁定へ戻す")


def gate_s35(s35: Dict[str, Any]) -> None:
    """G0-2: S3.5 overall S4_READY かつ F0 / Duration が PERCEPTIBLE_CANDIDATE。"""
    overall = (s35.get("overall") or {}).get("verdict")
    if overall != sp.REQUIRED_S35_OVERALL:
        raise S4Stop(
            cause=f"S3.5 overall が {overall!r}（要求 {sp.REQUIRED_S35_OVERALL!r}）",
            impact="S4 へ進む条件（知覚候補 2 gene）が満たされていない",
            minimal_fix="S3.5 を再裁定しない。S3.5 正本を確認して User へ戻す")
    genes = s35.get("genes") or {}
    for gene in sp.Gene:
        v = (genes.get(gene.value) or {}).get("verdict")
        if v != sp.REQUIRED_S35_GENE_VERDICT:
            raise S4Stop(
                cause=f"S3.5 の {gene.value} verdict が {v!r}"
                      f"（要求 {sp.REQUIRED_S35_GENE_VERDICT!r}）",
                impact="S4 に持ち込む gene の知覚候補性が正本で確認できない",
                minimal_fix="S3.5 を再裁定しない。対象 gene の選定を User 裁定へ戻す")


def gate_chain(s3_sha: str, s35: Dict[str, Any], manifest_sha: str,
               s3: Dict[str, Any]) -> None:
    """G0-3: 正本連結。S3.5 が指す S3、S3 が指す input manifest の両方を突き合わせる。

    後者を見ないと、S3 と別の凍結入力から candidate pair の素材を組み立てながら
    S3 の pin を来歴として提示できてしまう（§25 B）。
    """
    want = s35.get("s3_results_sha256")
    if want != s3_sha:
        raise S4Stop(
            cause=f"S3.5 が記録する s3_results_sha256 ({str(want)[:16]}…) が"
                  f"実ファイルの SHA ({s3_sha[:16]}…) と一致しない",
            impact="S3 と S3.5 が別の正本を指しており、S4 の入力連結が偽になる",
            minimal_fix="s3_results.json / s35_results.json のどちらが正かを User が裁定する")
    want_mf = s3.get("input_manifest_sha256")
    if want_mf != manifest_sha:
        raise S4Stop(
            cause=f"S3 が記録する input_manifest_sha256 ({str(want_mf)[:16]}…) が"
                  f"実 manifest の SHA ({manifest_sha[:16]}…) と一致しない",
            impact="S3 と別の凍結入力から素材を組み立てながら S3 の pin を"
                   "来歴として提示することになり、S3 replay の意味が失われる",
            minimal_fix="ladder_manifest.json が S3 走行時から変わっていないかを確認する")


# ---------------------------------------------------------------------------
# §5 G0-5 — 実装 closure pin / clean worktree
# ---------------------------------------------------------------------------
def _git(*args: str) -> Optional[bytes]:
    try:
        proc = subprocess.run(["git", *args], cwd=str(_HERE),
                              capture_output=True, check=True)
        return proc.stdout
    except Exception:  # noqa: BLE001
        return None


def worktree_state(exclude_prefixes: Sequence[str] = WORKTREE_EXCLUDE_PREFIXES,
                   ) -> Dict[str, Any]:
    """clean worktree 判定。`git` が使えないときは **clean を偽らない**。

    `-z` は NUL 区切りで C クォートをしないため、非 ASCII パスをそのまま扱える。
    """
    porcelain = _git("status", "--porcelain=v1", "-z", "--untracked-files=all")
    if porcelain is None:
        return {"clean": None, "entries": [], "excluded": list(exclude_prefixes)}
    entries: List[str] = []
    for chunk in porcelain.split(b"\x00"):
        if not chunk.strip():
            continue
        text = os.fsdecode(chunk)
        path = text[3:] if len(text) > 3 else text
        if any(path.startswith(pre) for pre in exclude_prefixes):
            continue
        entries.append(text)
    return {"clean": not entries, "entries": sorted(entries)[:50],
            "excluded": list(exclude_prefixes)}


def _sha_bytes_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def loaded_foundry_modules() -> List[str]:
    """`sys.modules` から **実際に読み込まれた** foundry 配下の実装を集める。

    設計書 §5 G0-5 の「直接 import / 動的 load した追加 module があれば自動収集」。
    最低被覆リストだけの部分 pin にしない。
    """
    out: List[str] = []
    for mod in list(sys.modules.values()):
        f = getattr(mod, "__file__", None)
        if not f:
            continue
        try:
            rel = Path(f).resolve().relative_to(_FOUNDRY)
        except (ValueError, OSError):
            continue
        if rel.suffix == ".py":
            out.append(rel.as_posix())
    return sorted(set(out))


def closure_digest() -> Dict[str, Any]:
    """実行に関与する module closure の内容 digest（§5 G0-5）。

    最低被覆（`s4_spec.MIN_CLOSURE_FILES`）に、実際に読み込まれた foundry
    モジュールを **足し合わせる**。最低被覆のファイルが 1 つでも読めなければ
    「pin できていない」ので止める。
    """
    names = sorted(set(sp.MIN_CLOSURE_FILES) | set(loaded_foundry_modules()))
    files: Dict[str, str] = {}
    h = hashlib.sha256()
    for name in names:
        path = _FOUNDRY / name
        if not path.is_file():
            if name in sp.MIN_CLOSURE_FILES:
                raise S4Stop(
                    cause=f"closure pin の最低被覆 {name} が読めない",
                    impact="判定に関与する実装を pin できず、記録の再現性が担保できない",
                    minimal_fix=f"{path} の存在を確認する")
            continue
        digest = _sha_bytes_of_file(path)
        files[name] = digest
        h.update(name.encode("utf-8"))
        h.update(bytes.fromhex(digest))
    return {"digest": h.hexdigest(), "file_count": len(files), "files": files,
            "min_closure": list(sp.MIN_CLOSURE_FILES)}


_CODE_STATE: Optional[Dict[str, Any]] = None


def code_state(*, require_clean: bool = True) -> Dict[str, Any]:
    """実際に走ったコードの状態を **1 回だけ**確定して使い回す（§5 G0-5）。"""
    global _CODE_STATE
    if _CODE_STATE is not None:
        return dict(_CODE_STATE)
    head = _git("rev-parse", "HEAD")
    commit = head.decode("utf-8", "replace").strip() if head else "unknown"
    wt = worktree_state()
    state: Dict[str, Any] = {"commit": commit, "worktree": wt,
                             "closure": closure_digest()}
    if require_clean and wt["clean"] is not True:
        reason = ("git 状態を確認できない" if wt["clean"] is None
                  else f"未コミットの変更がある: {wt['entries'][:5]}")
        raise S4Stop(
            cause=f"canonical run の前提である clean worktree が満たされない（{reason}）",
            impact="記録した closure digest と実際に走ったコードの対応が保証できず、"
                   "S4 の結果が再現不能になる",
            minimal_fix="変更を commit してから canonical run を実行する")
    _CODE_STATE = state
    return dict(state)


def reset_code_state() -> None:
    """テスト用。"""
    global _CODE_STATE
    _CODE_STATE = None


# ---------------------------------------------------------------------------
# §4 — candidate pair 導出（件数を手書きしない）
# ---------------------------------------------------------------------------
def candidate_pairs(s3: Dict[str, Any]) -> List[Tuple[str, str]]:
    """`(pair_key, context_id)` を S3 正本から機械的に導出する。

        candidate = S3 の f0 pair verdict == SUPPORTED
                    ∩ S3 の duration pair verdict == SUPPORTED

    **結果を見て絞らない・広げない。** 重複 pair_key は拒否する。
    """
    genes = s3.get("genes") or {}
    per_gene: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for gene in sp.Gene:
        pairs = (genes.get(gene.value) or {}).get("pairs")
        if not isinstance(pairs, dict) or not pairs:
            raise S4Stop(
                cause=f"S3 正本の {gene.value} に pairs が無い（または空）",
                impact="candidate pair を導出できず、S4 の母集団が確定しない",
                minimal_fix="s3_results.json の genes[*].pairs を確認する")
        per_gene[gene.value] = pairs
    f0_pairs = per_gene[sp.Gene.F0.value]
    du_pairs = per_gene[sp.Gene.DURATION.value]
    out: List[Tuple[str, str]] = []
    seen: set = set()
    for pk, rec in f0_pairs.items():
        if rec.get("verdict") != sp.S3_SUPPORTED:
            continue
        if (du_pairs.get(pk) or {}).get("verdict") != sp.S3_SUPPORTED:
            continue
        if pk in seen:
            raise S4Stop(
                cause=f"candidate に pair_key の重複がある: {pk}",
                impact="同一観測が evaluable / support_ratio を二重計上する",
                minimal_fix="s3_results.json の pairs を確認する")
        seen.add(pk)
        ctx = rec.get("context_id")
        if not isinstance(ctx, str) or not ctx:
            raise S4Stop(
                cause=f"{pk}: S3 正本に context_id が無い",
                impact="distinct context の集計が成立しない",
                minimal_fix="s3_results.json の pairs[*].context_id を確認する")
        out.append((pk, ctx))
    if len(out) < sp.MIN_CANDIDATE_PAIRS:
        raise S4Stop(
            cause=f"candidate pair が {len(out)} 件（要求 {sp.MIN_CANDIDATE_PAIRS} 件以上）",
            impact="§4 の必須条件を満たさず、母集団として S4 を実行できない",
            minimal_fix="母集団を勝手に縮小せず User 裁定へ戻す")
    ctxs = {c for _pk, c in out}
    if len(ctxs) < sp.MIN_CONTEXTS:
        raise S4Stop(
            cause=f"candidate の distinct context が {len(ctxs)} 件"
                  f"（要求 {sp.MIN_CONTEXTS} 件以上）",
            impact="§4 の必須条件を満たさず、文脈横断の主張が立たない",
            minimal_fix="母集団を勝手に縮小せず User 裁定へ戻す")
    return out


def s3_canonical_shas(s3: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    """S3 正本 `reproducibility` から `{pair_key: {condition: sample_sha256}}`。"""
    rows = s3.get("reproducibility")
    if not isinstance(rows, list) or not rows:
        raise S4Stop(
            cause="S3 正本に reproducibility 記録が無い",
            impact="S3 replay Gate（§6）を実行できず、同一基盤であることを確認できない",
            minimal_fix="s3_results.json の reproducibility を確認する")
    out: Dict[str, Dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        pk, cond, sha = row.get("pair_key"), row.get("condition"), row.get("sample_sha256")
        if isinstance(pk, str) and isinstance(cond, str) and isinstance(sha, str):
            out.setdefault(pk, {})[cond] = sha
    return out


def s3_canonical_pins(s3: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    """S3 正本 `reproducibility` から `{pair_key: {identity/performance sha}}`（G0-4）。"""
    rows = s3.get("reproducibility") or []
    out: Dict[str, Dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        pk = row.get("pair_key")
        if not isinstance(pk, str):
            continue
        cur = out.setdefault(pk, {})
        for k in ("identity_sha256", "performance_sha256"):
            v = row.get(k)
            if isinstance(v, str):
                cur[k] = v
    return out


def assert_canonical_unchanged() -> None:
    """裁定 4「S2/S3 正本を一切変更していない」を走行の**最後**に再確認する。

    一回読み（G0-6）は走行中の読み直しを防ぐが、記録を書く直前に正本が
    書き換わっていた場合を検出しない。読んだ bytes の digest と現物を突き合わせる。
    """
    for path, (_data, digest) in list(_READ_ONCE.items()):
        if not path.is_file():
            raise S4Stop(
                cause=f"走行中に正本が消えた: {path}",
                impact="記録が主張する来歴と実際の入力が食い違う",
                minimal_fix="走行中に results/ を書き換えない")
        now = _sha_bytes_of_file(path)
        if now != digest:
            raise S4Stop(
                cause=f"走行中に正本が変わった: {path} "
                      f"({digest[:16]}… -> {now[:16]}…)",
                impact="S2/S3 正本を変更しないという裁定 4 の開始条件を満たさない",
                minimal_fix="走行中に正本を書き換えない。変更の意図を User 裁定へ戻す")


def machine_info() -> Dict[str, Any]:
    """裁定 6「実行 machine 情報」。判定には使わない記録専用。"""
    import platform  # noqa: PLC0415
    info: Dict[str, Any] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
    }
    for mod in ("numpy", "pyworld", "soundfile"):
        try:
            info[mod] = __import__(mod).__version__
        except Exception:  # noqa: BLE001
            info[mod] = None
    return info


def material_provenance(material: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """裁定 6 の記録: archive SHA / 新 root / 写像 / machine 情報。"""
    return {"relocated": material is not None,
            "sources": (material or {}).get("sources", []),
            # Performance digest は source_file（絶対パス）を含むため、
            # 再配置時はラベルだけ正本へ戻している。何を戻したかを記録に残す。
            "performance_labels_canonicalized":
                (material or {}).get("performance_labels_canonicalized", []),
            "machine": machine_info()}


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------
@dataclass
class ConditionOutput:
    condition: str
    toggles: str
    sample_sha256: str
    wav_sha256: str
    wav_path: str
    metrics: Dict[str, Optional[float]]
    tripwire_status: str = ""
    tripwire_accessed: List[str] = field(default_factory=list)
    #: `identity_margin_db` の内訳と、その凍結 pin との突き合わせ結果（G0-4b）。
    identity_components: Dict[str, Optional[float]] = field(default_factory=dict)
    donor_pin: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"condition": self.condition, "toggles": self.toggles,
                "sample_sha256": self.sample_sha256, "wav_sha256": self.wav_sha256,
                "wav_path": self.wav_path, "metrics": self.metrics,
                "tripwire_status": self.tripwire_status,
                "tripwire_accessed": list(self.tripwire_accessed),
                "identity_components": self.identity_components,
                "donor_pin": self.donor_pin}


@dataclass
class PairRun:
    pair_key: str
    context_id: str
    identity_sha256: str
    performance_sha256: str
    performance_payload_1d: bool = True
    conditions: Dict[str, ConditionOutput] = field(default_factory=dict)
    intervention: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    repeat_sample_sha256: Dict[str, str] = field(default_factory=dict)
    s3_replay: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    #: metric 入力である donor 側包絡の内容 digest（来歴接続用。判定には使わない）。
    donor_core_sha256: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"pair_key": self.pair_key, "context_id": self.context_id,
                "identity_sha256": self.identity_sha256,
                "performance_sha256": self.performance_sha256,
                "performance_payload_1d": self.performance_payload_1d,
                "conditions": {k: v.as_dict() for k, v in self.conditions.items()},
                "intervention": self.intervention,
                "repeat_sample_sha256": self.repeat_sample_sha256,
                "s3_replay": self.s3_replay,
                "donor_core_sha256": self.donor_core_sha256}


def _clean(v: float) -> Optional[float]:
    return None if not np.isfinite(v) else round(float(v), 6)


def measure(condition: str, result: pc.ComposeResult, wav_path: Path, bank, pos,
            perf, donor_core, track) -> Tuple[Dict[str, Optional[float]],
                                              Dict[str, Optional[float]]]:
    """§8 の既存 metric **だけ**を記録する。新しい acoustic metric は足さない。

    2 つ目の戻り値は `identity_margin_db` の**構成要素**（`donor_lsd_db -
    identity_lsd_db`）。新しい計器ではなく、§8 が既に定義している差の内訳であり、
    donor 側スペクトル（`donor_core`）を来歴へ接続するための pin 材料として使う。
    """
    m = pr_ladder.measure_rung(condition, result, wav_path, bank, pos, perf,
                               donor_core, track)
    metrics = {"f0_dev_rmse_cents": _clean(m.f0_dev_rmse_cents),
               "note_split_mae_ms": _clean(m.note_split_mae_ms),
               sp.IDENTITY_METRIC: _clean(m.identity_margin_db)}
    components = {"identity_lsd_db": _clean(m.identity_lsd_db),
                  "donor_lsd_db": _clean(m.donor_lsd_db)}
    return metrics, components


#: 凍結 manifest が rung metric を記録している桁数（`RungMeasurement.as_dict` の
#: `round(v, 4)`）。**新しい許容誤差ではなく**、pin 側が持っている精度そのもの。
MANIFEST_PIN_PRECISION = 4


def manifest_rung_pins(pair: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    """凍結 manifest の rung から `{toggle label: {identity/donor lsd}}` を作る。

    rung 名（R0/R1/…）ではなく **toggle label** で引く。ラダーの並びが変わっても
    「同じトグル状態の測定」を取り違えない。
    """
    out: Dict[str, Dict[str, float]] = {}
    for rung in (pair.get("rungs") or {}).values():
        if not isinstance(rung, dict):
            continue
        label = rung.get("toggles")
        vals = {k: rung.get(k) for k in ("identity_lsd_db", "donor_lsd_db")}
        if isinstance(label, str) and all(isinstance(v, (int, float))
                                          and not isinstance(v, bool)
                                          for v in vals.values()):
            out[label] = {k: float(v) for k, v in vals.items()}
    return out


def assert_donor_pin(pair_key: str, condition: str, label: str,
                     components: Dict[str, Optional[float]],
                     pins: Dict[str, Dict[str, float]]) -> Dict[str, Any]:
    """`identity_margin_db` の**入力**を凍結 manifest へ接続する（G0-4b）。

    `identity_sha256` は Ritsu 側 sp/ap しか覆わず、`performance_sha256` は設計上
    スペクトルを持たない。donor 側の包絡（`donor_core`）はどちらの pin にも入らない
    のに `identity_margin_db` を動かし、`COMBINABLE` / S4 PASS を反転させうる。
    凍結 manifest は同じトグル状態で測った `identity_lsd_db` / `donor_lsd_db` を
    持っているので、それと突き合わせて donor 側の素材変化を検出する。

    pin が無い条件は**検証スキップにせず** BLOCKED（AGENTS.md の
    「pin の欠落は stale と同扱いの fail-closed」）。
    """
    frozen = pins.get(label)
    if frozen is None:
        raise S4Stop(
            cause=f"{pair_key}: 凍結 manifest にトグル {label!r}（条件 {condition}）の "
                  f"rung metric が無く、donor 側スペクトルを pin できない",
            impact="identity_margin_db の入力が来歴へ接続されず、"
                   "donor 素材が変わっても COMBINABLE / PASS が反転しうる",
            minimal_fix="ladder_manifest.json の rungs に当該トグルの測定があるか確認する")
    detail: Dict[str, Any] = {"toggles": label, "frozen": frozen, "measured": {}}
    for key, want in frozen.items():
        got = components.get(key)
        if got is None:
            raise S4Stop(
                cause=f"{pair_key}/{condition}: {key} を測定できなかった",
                impact="identity_margin_db の内訳を凍結 pin と突き合わせられない",
                minimal_fix="measure_rung が有限値を返さない原因を確認する")
        rounded = round(float(got), MANIFEST_PIN_PRECISION)
        detail["measured"][key] = rounded
        if rounded != round(float(want), MANIFEST_PIN_PRECISION):
            raise S4Stop(
                cause=f"{pair_key}/{condition}: {key} が凍結 manifest と一致しない "
                      f"(got {rounded} / pin {want})",
                impact="identity_margin_db の入力（donor 側スペクトル等）が S2/S3 走行時"
                       "から変わっており、identity 判定の来歴が偽になる",
                minimal_fix="PJS / Ritsu の参照素材と WORLD 解析環境が"
                            "凍結時から変わっていないかを確認する")
    return detail


def assert_pins(pair_key: str, bank_sha: str, perf_sha: str,
                pins: Dict[str, str]) -> None:
    """G0-4: 再構築した identity / performance が **S3 正本の pin** と一致すること。"""
    for what, got in (("identity", bank_sha), ("performance", perf_sha)):
        want = pins.get(f"{what}_sha256")
        if want is None:
            raise S4Stop(
                cause=f"{pair_key}: S3 正本に {what}_sha256 の pin が無い",
                impact="S4 が S3 と同じ素材を使っていることを確認できない",
                minimal_fix="s3_results.json の reproducibility を確認する")
        if got != want:
            raise S4Stop(
                cause=f"{pair_key}: {what} の再構築ハッシュが S3 正本の pin と一致しない "
                      f"(got {got[:16]}… / pin {want[:16]}…)",
                impact="S3 と別の素材を評価しながら S3 の pin を来歴として提示することになる",
                minimal_fix="参照先の lab/wav が S3 走行時から変わっていないかを確認する")


def run_pair(pair: Dict[str, Any], ctx: int, canonical_shas: Dict[str, str],
             pins: Dict[str, str], *, write_wav: bool = True,
             wav_root: Optional[Path] = None) -> PairRun:
    """1 candidate pair について B0 / F / D / FD を生成し、S3 replay を確認する。"""
    pair_key = pair["pair_key"]
    try:
        bank, pos, track, perf, donor_core = s3r.build_inputs(pair, ctx)
    except s3r.S3Stop as stop:
        raise S4Stop(cause=stop.cause, impact=stop.impact,
                     minimal_fix=stop.minimal_fix) from stop
    assert_pins(pair_key, bank.content_sha256(), perf.content_sha256(), pins)
    run = PairRun(pair_key=pair_key, context_id=str(pair["probe_kind"]),
                  identity_sha256=bank.content_sha256(),
                  performance_sha256=perf.content_sha256())
    run.intervention = s3r.intervention_amounts(bank, pos, track, perf)
    if donor_core is not None:
        run.donor_core_sha256 = hashlib.sha256(
            np.ascontiguousarray(donor_core, dtype=np.float64).tobytes()).hexdigest()
    rung_pins = manifest_rung_pins(pair)
    leaf = pair_key.replace("|", "__").replace("#", "-")
    out_dir = (wav_root or WAV_DIR) / leaf
    out_dir.mkdir(parents=True, exist_ok=True)
    published_dir = WAV_DIR / leaf
    try:
        prp.assert_no_spectral_payload(perf)
        run.performance_payload_1d = True
    except ValueError:
        run.performance_payload_1d = False
    for cond, toggles in sp.CONDITIONS.items():
        res = pc.compose(bank, track, toggles)
        wav_path = out_dir / f"{cond}.wav"
        if write_wav:
            sf.write(wav_path, res.wav.astype(np.float32), res.sr, subtype="FLOAT")
            wav_sha = _sha_bytes_of_file(wav_path)
        else:
            wav_sha = ""
        tw = pbg.gate_tripwire(pc.compose, bank, track, toggles)
        if write_wav:
            metrics, components = measure(cond, res, wav_path, bank, pos, perf,
                                          donor_core, track)
            donor_pin = assert_donor_pin(pair_key, cond, toggles.label, components,
                                         rung_pins)
        else:
            metrics, components, donor_pin = {}, {}, {}
        run.conditions[cond] = ConditionOutput(
            condition=cond, toggles=toggles.label, sample_sha256=res.sha256(),
            wav_sha256=wav_sha, wav_path=str(published_dir / f"{cond}.wav"),
            metrics=metrics,
            tripwire_status=tw.status,
            tripwire_accessed=list(tw.evidence.get("accessed", [])),
            identity_components=components, donor_pin=donor_pin)
        run.repeat_sample_sha256[cond] = pc.compose(bank, track, toggles).sha256()
    run.s3_replay = replay_check(run, canonical_shas)
    return run


def replay_check(run: PairRun, canonical: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    """§6: S4 が生成した B0 / F / D が S3 canonical と **bytes で**一致するか。"""
    out: Dict[str, Dict[str, Any]] = {}
    for cond in sp.REPLAY_CONDITIONS:
        want = canonical.get(cond)
        got = run.conditions[cond].sample_sha256
        out[cond] = {"match": bool(want is not None and want == got),
                     "s3_sample_sha256": (want or "")[:16],
                     "s4_sample_sha256": got[:16],
                     "s3_recorded": want is not None}
    return out


def publish_wav(staging: Path = WAV_STAGING, dest: Path = WAV_DIR) -> Optional[Path]:
    """staging の WAV 群を公開先へ入れ替え、旧版の退避先を返す（§22）。"""
    if not staging.exists():
        return None
    backup = dest.with_name(dest.name + ".prev")
    if backup.exists():
        shutil.rmtree(backup)
    had_dest = dest.exists()
    try:
        if had_dest:
            os.replace(dest, backup)
        os.replace(staging, dest)
    except BaseException:
        if had_dest and backup.exists() and not dest.exists():
            os.replace(backup, dest)
        raise
    return backup if had_dest else None


def rollback_wav(dest: Path, backup: Optional[Path]) -> None:
    shutil.rmtree(dest, ignore_errors=True)
    if backup is not None and backup.exists():
        os.replace(backup, dest)


def drop_backup(backup: Optional[Path]) -> None:
    if backup is not None and backup.exists():
        shutil.rmtree(backup)


# ---------------------------------------------------------------------------
# §12 決定論 — cross-process
# ---------------------------------------------------------------------------
def recompute_sample_shas() -> Dict[str, Dict[str, str]]:
    """別プロセス用: pin 済み入力から candidate pair 全条件の sample sha256 を作る。"""
    s3, _ = load_s3()
    manifest, _ = load_input_manifest()
    ctx = int(manifest["context_phones"])
    wanted = {pk for pk, _c in candidate_pairs(s3)}
    by_key = {p["pair_key"]: p for p in manifest["pairs"]}
    roots_cfg = load_material_roots()
    material = None
    if roots_cfg is not None:
        source_manifest, _sm = load_source_manifest()
        material = resolve_material_roots(roots_cfg, source_manifest)
    out: Dict[str, Dict[str, str]] = {}
    with canonical_source_labels(material):
        for pk in sorted(wanted):
            pair = by_key.get(pk)
            if pair is None:
                continue
            if material is not None:
                pair = remap_pair(pair, material)
            bank, _pos, track, _perf, _dc = s3r.build_inputs(pair, ctx)
            out[pk] = {c: pc.compose(bank, track, tg).sha256()
                       for c, tg in sp.CONDITIONS.items()}
    return out


def cross_process_shas() -> Dict[str, Dict[str, str]]:
    """別プロセスで同じ入力から sample sha256 を作り直す（§12）。

    サブプロセスが落ちた場合は**空を返さない**（決定論違反と実行失敗を混同しない）。
    """
    proc = subprocess.run([sys.executable, str(_HERE / "s4_runner.py"), "--recompute"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise S4Stop(
            cause=f"別プロセス再計算が異常終了した（rc={proc.returncode}）: "
                  f"{proc.stderr.strip()[-500:]}",
            impact="§12 の cross-process 決定論が検査できず、S4 の判定を出せない",
            minimal_fix="`python s4_runner.py --recompute` を単体で実行して原因を特定する")
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as exc:  # noqa: BLE001
        raise S4Stop(
            cause=f"別プロセス再計算の出力が JSON として読めない: {exc}",
            impact="§12 の cross-process 決定論が検査できず、S4 の判定を出せない",
            minimal_fix="`python s4_runner.py --recompute` の標準出力を確認する") from exc


# ---------------------------------------------------------------------------
# 走行
# ---------------------------------------------------------------------------
def run_all(*, write_wav: bool = True, require_clean: bool = True,
            ) -> Tuple[List[PairRun], Dict[str, Any], Optional[Path]]:
    """Phase A の生成部（§20）。判定は `s4_gates` が行う。"""
    s3, s3_sha = load_s3()
    s35, s35_sha = load_s35()
    manifest, manifest_sha = load_input_manifest()
    reset_preflight()
    gate_s3(s3)
    _PREFLIGHT["G0-1_s3_canonical"] = "pass"
    gate_s35(s35)
    _PREFLIGHT["G0-2_s35_canonical"] = "pass"
    gate_chain(s3_sha, s35, manifest_sha, s3)
    _PREFLIGHT["G0-3_provenance_chain"] = "pass"
    cs = code_state(require_clean=require_clean)
    _PREFLIGHT["G0-5_clean_worktree"] = (
        "pass" if (cs["worktree"] or {}).get("clean") else "not-required")
    _PREFLIGHT["G0-5_closure_digest"] = cs["closure"]["digest"]
    _PREFLIGHT["G0-5_closure_file_count"] = cs["closure"]["file_count"]
    _PREFLIGHT["commit"] = cs["commit"]
    _PREFLIGHT["s3_results_sha256"] = s3_sha
    _PREFLIGHT["s35_results_sha256"] = s35_sha
    _PREFLIGHT["input_manifest_sha256"] = manifest_sha

    ctx = manifest.get("context_phones")
    if not isinstance(ctx, int) or isinstance(ctx, bool) or ctx <= 0:
        raise S4Stop(
            cause=f"S3 input manifest の context_phones が無い/不正: {ctx!r}",
            impact="S3 と同じ context 条件で素材を組み直せず、replay が成立しない",
            minimal_fix="ladder_manifest.json の context_phones を確認する")
    roots_cfg = load_material_roots()
    material: Optional[Dict[str, Any]] = None
    if roots_cfg is not None:
        source_manifest, _sm_sha = load_source_manifest()
        material = resolve_material_roots(roots_cfg, source_manifest)
        _PREFLIGHT["material_relocation"] = "pass"
        _PREFLIGHT["material_roots"] = {r["source"]: r["new_root"]
                                        for r in material["sources"]}
    cands = candidate_pairs(s3)
    _PREFLIGHT["candidate_pairs"] = len(cands)
    _PREFLIGHT["candidate_pair_keys"] = [pk for pk, _c in cands]
    _PREFLIGHT["candidate_contexts"] = sorted({c for _pk, c in cands})
    canonical = s3_canonical_shas(s3)
    pins = s3_canonical_pins(s3)
    by_key = {p["pair_key"]: p for p in manifest.get("pairs", [])
              if isinstance(p, dict) and isinstance(p.get("pair_key"), str)}
    _PREFLIGHT["§4_candidate_derivation"] = "pass"
    missing = [pk for pk, _c in cands if pk not in by_key]
    if missing:
        raise S4Stop(
            cause=f"candidate pair が S3 input manifest に無い: {missing}",
            impact="S3 正本に存在した候補 pair を再構築できず、母集団が縮小する",
            minimal_fix="母集団を勝手に縮小せず User 裁定へ戻す（§4）")

    if write_wav and WAV_STAGING.exists():
        shutil.rmtree(WAV_STAGING)
    runs: List[PairRun] = []
    with canonical_source_labels(material) as relabelled:
        for pk, _ctx_id in cands:
            pair = by_key[pk] if material is None else remap_pair(by_key[pk], material)
            assert_pair_materials(pair)
            runs.append(run_pair(pair, ctx, canonical.get(pk, {}), pins.get(pk, {}),
                                 write_wav=write_wav,
                                 wav_root=WAV_STAGING if write_wav else None))
    if material is not None:
        material["performance_labels_canonicalized"] = relabelled
    # §6: S3 replay が 1 件でも不一致なら **S4 の結果を出さない**。
    mismatched = [(r.pair_key, c) for r in runs for c in sp.REPLAY_CONDITIONS
                  if not (r.s3_replay.get(c) or {}).get("match", False)]
    if mismatched:
        raise S4Stop(
            cause=f"S3 replay 不一致: {mismatched[:6]}",
            impact="S4 が S3 と同じ基盤・入力・単独 gene 実装を使っていることを"
                   "結果 bytes で確認できないため、S4 の結果を出さない",
            minimal_fix="S3 正本・凍結素材・実装 closure のどれが変わったかを"
                        "特定して User 裁定へ戻す")
    backup = publish_wav() if write_wav else None
    assert_canonical_unchanged()
    meta = {
        "material_provenance": material_provenance(material),
        "s3_results_sha256": s3_sha,
        "s35_results_sha256": s35_sha,
        "input_manifest_sha256": manifest_sha,
        "context_phones": ctx,
        "identity_ap_scale": manifest.get("identity_ap_scale"),
        "candidate_pairs": [{"pair_key": pk, "context_id": c} for pk, c in cands],
        "code_state": cs,
    }
    return runs, meta, backup


if __name__ == "__main__":
    if "--recompute" in sys.argv:
        print(json.dumps(recompute_sample_shas()))
    else:
        _runs, _meta, _bk = run_all()
        drop_backup(_bk)
        print(json.dumps({"meta": _meta, "pairs": [r.as_dict() for r in _runs]},
                         ensure_ascii=False, indent=2)[:2000])
