"""rpe/learned/source_separation_adapter.py — melody 経路向け Demucs vocals 分離。

Optional via the `separate` extra. 既存の `io.source_separator.separate_stems`
（本リポジトリ唯一の Demucs エントリポイント）を再利用し、melody routing 層が
必要とする **vocals stem だけ**を返す薄いラッパである。`SeparatorNotAvailableError`
を `LearnedModelUnavailable` へ写像することで、CREPE / Melodia など他の optional
抽出器と**同一の失敗形**（`LearnedModelUnavailable` で優雅に落ちる）へ揃える
（`docs/learned_models_policy.md` の slow-lane 隔離、`docs/melody_observability.md`
M1a）。

重みプロビジョニング・ゲート（D-1）:
    可用性は 2 値（import 可否）ではなく **3 値**である——(1) demucs 未導入、
    (2) 導入済 + 重みローカル取得済、(3) **導入済だが重み未取得**。(3) を
    「利用可能」と誤認すると、実行時に Demucs が CDN からチェックポイントを
    download しようとし、遮断環境では `urllib.error.URLError` が
    `observe_via_route` を貫通して `--external` run 全体を落とす（部分行も report も
    残らない）。本モジュールは (3) を **`LearnedModelUnavailable`**（= route
    `unavailable` → verdict `inconclusive`）へ落とす門を持つ:

    - `ensure_separation_available()` が `DEFAULT_MODEL` の要求チェックポイントの
      **ローカル実在**まで検査する。探索先は **demucs が実際に読む場所**
      （torch hub の checkpoints cache）だけで、任意ディレクトリは探索しない
      — 別の場所を hash して demucs は既定 cache から読む、という「pin と
      モデル入力の乖離」を構造的に作らないため（置き場所を変えるなら torch
      ネイティブの `TORCH_HOME` を使う。demucs も本ゲートも同じ規約で解決する）。
    - `isolate_vocals()` は分離実行**前**に必ずこの門を通し、さらに重み取得起因の
      失敗（`URLError` / `HTTPError` / `OSError` / torch hub の `RuntimeError`）を
      `LearnedModelUnavailable` へ写像する。
    - **実行時 download は一切行わない**（リトライも代替 URL もミラーも足さない）。
      重みは slow-lane で事前取得する（`docs/melody_observability.md` §6.4）。

stem / weights hash の emit（D-2）:
    評価器は measured な分離経路に `preprocessing.stem_sha256` と
    `separation_weights_sha256` を必須とする（#54）。`isolate_vocals_with_provenance`
    がその 2 つを実測して返す（stem は float32 生サンプル hash、weights は実際に
    読んだチェックポイントファイルの hash）。

このモジュールは決定論 RPE フィールドを一切書かない。分離波形（numpy 配列）を
呼び出し側（`melody.extractors`）へ返すだけで、`LearnedAudioAnnotations` すら
生成しない — vocals stem は観測ゲートへの中間入力であって注釈ではない。
"""
from __future__ import annotations

import importlib
import importlib.metadata as _pkg_metadata
import os
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

from svp_rpe.io.source_separator import (
    DEFAULT_MODEL,
    SeparatorNotAvailableError,
    separate_stems,
)
from svp_rpe.rpe.learned import LearnedModelUnavailable
from svp_rpe.utils.hashing import sha256_of_files, sha256_of_float32

__all__ = [
    "LearnedModelUnavailable",
    "SeparationWeights",
    "VocalsSeparation",
    "describe_separation_weights",
    "ensure_separation_available",
    "isolate_vocals",
    "isolate_vocals_with_provenance",
    "locate_separation_weights",
    "resolve_separation_weights",
]

_INSTALL_HINT = (
    "demucs is not installed. Install it via the optional `separate` extra:\n"
    '    pip install -e ".[separate]"'
)


@dataclass(frozen=True)
class SeparationWeights:
    """ローカルに実在が確認できた分離器チェックポイント一式。"""

    model: str
    paths: Tuple[Path, ...]
    sha256: str
    version: Optional[str]

    @property
    def filenames(self) -> Tuple[str, ...]:
        return tuple(p.name for p in self.paths)


@dataclass(frozen=True)
class VocalsSeparation:
    """vocals stem と、その stem を作った入力の content pin。"""

    waveform: np.ndarray
    sample_rate: int
    model: str
    stem_sha256: str
    weights_sha256: str
    weights_filenames: Tuple[str, ...]
    version: Optional[str]


def _demucs_version() -> Optional[str]:
    try:
        return _pkg_metadata.version("demucs")
    except Exception:
        return None


def _has_demucs() -> bool:
    """`io.source_separator` の import フラグを**呼び出し時**に読む。

    module 属性を直接 import せず getattr するのは、テストが
    `source_separator._HAS_DEMUCS` を monkeypatch して 3 状態を再現できるようにする
    ため（既存挙動を踏襲）。
    """
    from svp_rpe.io import source_separator

    return bool(getattr(source_separator, "_HAS_DEMUCS", False))


def _demucs_remote_dir() -> Optional[Path]:
    """demucs パッケージ同梱の remote メタデータ（files.txt / <model>.yaml）の場所。"""
    try:
        demucs = importlib.import_module("demucs")
    except ImportError:
        return None
    module_file = getattr(demucs, "__file__", None)
    if module_file:
        candidate = Path(module_file).resolve().parent / "remote"
        if candidate.is_dir():
            return candidate
    try:  # pragma: no cover - レイアウトが変わった demucs 向けの保険
        pretrained = importlib.import_module("demucs.pretrained")
    except ImportError:
        return None
    remote_root = getattr(pretrained, "REMOTE_ROOT", None)
    if remote_root is not None and Path(remote_root).is_dir():
        return Path(remote_root)
    return None


def _parse_remote_files(text: str) -> Dict[str, str]:
    """demucs の `remote/files.txt` を signature → 相対 URL パスへ parse する。

    upstream（`demucs.pretrained._parse_remote_files`）と同じ規則: `root:` 行が
    以降の行の prefix を切り替え、各行の `-` 前がモデル signature になる。
    ダウンロード先ファイル名は行の basename（torch hub は URL の basename で
    cache する）。
    """
    root = ""
    models: Dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("root:"):
            root = line.split(":", 1)[1].strip()
            continue
        sig = line.split("-", 1)[0]
        models[sig] = root + line
    return models


def _model_signatures(remote_dir: Path, model: str) -> List[str]:
    """`model` が要求するチェックポイント signature 一覧。

    bag（`htdemucs_ft.yaml` の `models:` リスト）なら列挙された signature 群、
    単体モデル名（= signature 自身）ならそれ 1 本。
    """
    bag_yaml = remote_dir / f"{model}.yaml"
    if bag_yaml.is_file():
        import yaml

        spec = yaml.safe_load(bag_yaml.read_text(encoding="utf-8")) or {}
        signatures = spec.get("models") or []
        if not signatures:
            raise LearnedModelUnavailable(
                f"demucs bag definition {bag_yaml} lists no models; "
                "重み構成を解決できないため分離経路を unavailable として扱う"
            )
        return [str(sig) for sig in signatures]
    return [model]


def _bag_metadata_file(remote_dir: Path, model: str) -> Optional[Path]:
    """bag モデルの定義 YAML（`htdemucs_ft.yaml`）。単体モデルなら None。

    bag YAML は **実行時のモデル入力**である: 使用する checkpoint signature を選び、
    `weights`（ソース別重み付け）や `segment` など `BagOfModels` 構築時に読まれる
    フィールドを持つ。同じ `.th` でも bag メタデータが違えば別の vocals stem が出る
    ため、`separation_weights_sha256` の hash 対象に含める（Codex #217）。
    """
    bag_yaml = remote_dir / f"{model}.yaml"
    return bag_yaml if bag_yaml.is_file() else None


def _expected_weight_filenames(model: str) -> List[str]:
    """`model` の実行に必要なチェックポイントのファイル名（torch hub cache 上の名前）。

    解決できない場合は `LearnedModelUnavailable`（fail-closed）。「解決できないから
    とりあえず走らせる」は実行時 DL を招くので採らない。
    """
    remote_dir = _demucs_remote_dir()
    if remote_dir is None:
        raise LearnedModelUnavailable(
            "demucs remote metadata (remote/files.txt) not found; "
            "重み取得状況を検証できないため分離経路を unavailable として扱う"
            "（実行時 DL は行わない）"
        )
    files_txt = remote_dir / "files.txt"
    if not files_txt.is_file():
        raise LearnedModelUnavailable(
            f"demucs remote file list {files_txt} not found; "
            "重み取得状況を検証できないため分離経路を unavailable として扱う"
            "（実行時 DL は行わない）"
        )
    catalogue = _parse_remote_files(files_txt.read_text(encoding="utf-8"))
    filenames: List[str] = []
    for sig in _model_signatures(remote_dir, model):
        relative = catalogue.get(sig)
        if relative is None:
            raise LearnedModelUnavailable(
                f"demucs model signature {sig!r} (model {model!r}) is not listed in "
                f"{files_txt}; 期待チェックポイントを特定できない（実行時 DL は行わない）"
            )
        filenames.append(Path(relative).name)
    return filenames


def _uses_cli_separation() -> bool:
    """`separate_stems` が**子プロセス**（`python -m demucs`）経路を採るか。

    `io.source_separator.separate_stems` は demucs API が import できればプロセス内で
    分離し、できなければ CLI へフォールバックする。CLI 経路の子プロセスは
    `os.environ` を継承するだけなので、**親プロセスの `torch.hub.set_dir()` は届かない**
    （子は `TORCH_HOME` / 既定 cache から解決する）。重みの探索先はこの分岐で変わる。
    """
    from svp_rpe.io import source_separator

    return getattr(source_separator, "_DemucsAPI", None) is None


def _env_derived_checkpoint_dir() -> Path:
    """torch の規約（`TORCH_HOME` → `XDG_CACHE_HOME` → `~/.cache`）で組む cache パス。

    子プロセス（CLI 経路）や torch を import できない場合に、実際に読まれる場所を
    親プロセスの in-process 状態に依存せず再現するための解決。
    """
    torch_home = os.environ.get("TORCH_HOME")
    if torch_home:
        return Path(torch_home).expanduser() / "hub" / "checkpoints"
    cache_home = os.environ.get("XDG_CACHE_HOME") or "~/.cache"
    return Path(cache_home).expanduser() / "torch" / "hub" / "checkpoints"


def _torch_hub_checkpoint_dirs() -> List[Path]:
    """torch hub のチェックポイント cache（`torch.hub.get_dir()/checkpoints`）。

    **ここが唯一の探索先**である。`separate_stems` は demucs の既定 repo
    （`RemoteRepo` → `torch.hub.load_state_dict_from_url`）で重みを読むので、
    分離器が実際に読む場所以外を探索すると「pin した bytes」と「モデル入力に
    なった bytes」が乖離しうる（別ディレクトリに置いた別版 .th を hash しつつ、
    demucs は hub cache の別ファイルで分離する）。任意ディレクトリの探索は
    その乖離を作るので**持たない** — 置き場所を変えたい場合は torch ネイティブの
    `TORCH_HOME` を使う（demucs も本ゲートも同じ規約で解決する）。

    どこを見るかは `separate_stems` がどちらの経路で分離するかで決まる:

    - **API 経路（プロセス内）**: `torch.hub.get_dir()` **だけ**を見る（active hub dir が
      唯一の真実。`torch.hub.set_dir()` を呼んだプロセスでは `TORCH_HOME` と食い違い
      うるので、env 由来のパスを併記すると demucs が読まないファイルを hash しうる）。
    - **CLI 経路（子プロセス `python -m demucs`）**: 子は `os.environ` を継承するだけで
      親の `torch.hub.set_dir()` は届かないので、**env 由来の解決**（子が実際に使う
      規約）を見る。親の active hub dir を見ると、子が読む cache と乖離する。

    torch を import できないときも env 由来へ落ちる（重み未取得判定のために torch の
    import を必須にしないための代替解決）。いずれも Codex #217 の指摘。
    """
    if not _uses_cli_separation():
        try:  # pragma: no cover - torch は optional
            import torch

            return [Path(torch.hub.get_dir()) / "checkpoints"]
        except Exception:
            pass
    return [_env_derived_checkpoint_dir()]


def locate_separation_weights(model: str = DEFAULT_MODEL) -> List[Path]:
    """`model` の**モデル入力ファイル**がローカルに実在することを確認してパスを返す。

    返すのは (1) チェックポイント（`.th`）と (2) bag モデルの定義 YAML。後者も
    `BagOfModels` 構築時に読まれる実行時入力（signature 選択・`weights`・`segment`）
    なので、同じ `.th` でも bag メタデータが違えば別の stem が出る。両方を pin 対象に
    含めることで `separation_weights_sha256` が分離器の構成まで識別する（Codex #217）。

    チェックポイントの探索先は `_torch_hub_checkpoint_dirs()`（= `separate_stems` が
    実際に重みを読む場所）のみ。ここで返すパスがそのまま hash 対象になるので、
    「pin した bytes = モデル入力になった bytes」が保たれる。

    hash は取らない（`ensure_separation_available` の probe を I/O 軽量に保つ）。

    Raises
    ------
    LearnedModelUnavailable
        demucs 未導入、期待ファイル名を解決できない、または重みが未取得のとき。
        いずれの場合も download は試みない（呼び出し側は route を `unavailable`
        として記録し、run は完走する）。
    """
    if not _has_demucs():
        raise LearnedModelUnavailable(_INSTALL_HINT)
    filenames = _expected_weight_filenames(model)
    search_dirs = _torch_hub_checkpoint_dirs()
    found: List[Path] = []
    missing: List[str] = []
    for filename in filenames:
        for directory in search_dirs:
            candidate = directory / filename
            if candidate.is_file():
                found.append(candidate)
                break
        else:
            missing.append(filename)
    if missing:
        primary = search_dirs[0]
        expected = ", ".join(str(primary / name) for name in missing)
        raise LearnedModelUnavailable(
            f"demucs weights not provisioned: {expected}; 事前取得が必要"
            "（実行時 DL は行わない）。取得手順は docs/melody_observability.md §6.4。"
            "重みは demucs が読む torch hub cache に置くこと（別の場所へ置きたい場合は "
            "TORCH_HOME を設定する）。分離経路="
            f"{'CLI 子プロセス（env 由来解決）' if _uses_cli_separation() else 'API（in-process hub dir）'}、"
            f"探索したディレクトリ: {[str(d) for d in search_dirs]}"
        )
    # bag 定義 YAML（demucs パッケージ同梱）も実行時のモデル入力なので pin 対象に含める。
    remote_dir = _demucs_remote_dir()
    if remote_dir is not None:
        bag_yaml = _bag_metadata_file(remote_dir, model)
        if bag_yaml is not None:
            found.append(bag_yaml)
    return found


def resolve_separation_weights(model: str = DEFAULT_MODEL) -> SeparationWeights:
    """`locate_separation_weights` + content hash（provenance pin つきで返す）。

    Raises
    ------
    LearnedModelUnavailable
        demucs 未導入・期待ファイル名を解決できない・重みが未取得のとき。
    """
    found = locate_separation_weights(model)
    return SeparationWeights(
        model=model,
        paths=tuple(found),
        sha256=sha256_of_files(found),
        version=_demucs_version(),
    )


def ensure_separation_available(model: str = DEFAULT_MODEL) -> None:
    """Demucs が **実際に走れる**かを事前に確認し、無理なら `LearnedModelUnavailable`。

    「import できる」だけでは不十分で、`model` のチェックポイントがローカルに
    取得済みであることまで検査する（D-1: 導入済 + 重み未取得 = 第 3 状態）。
    実分離は走らせない・download もしない・hash も取らない軽量 probe。
    """
    locate_separation_weights(model)


def describe_separation_weights(model: str = DEFAULT_MODEL) -> Dict[str, Any]:
    """事前取得した重みの provenance 記録用サマリ（registry へ転記する dated 欄）。

    `docs/melody_observability.md` §6.4 の重み取得手順で、operator が
    `registry.yaml` の `provenance.model_weights.demucs` を埋めるために使う。
    """
    weights = resolve_separation_weights(model)
    return {
        "model": weights.model,
        "version": weights.version,
        "sha256": weights.sha256,
        "files": list(weights.filenames),
        "paths": [str(p) for p in weights.paths],
        "source": "事前取得（実行時DL禁止）",
    }


def _weights_failure_message(exc: BaseException) -> str:
    first_line = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    return (
        f"demucs separation failed ({type(exc).__name__}: {first_line}); "
        "重み取得起因（実行時 DL の遮断・cache 破損・torch hub エラー）の可能性が高い。"
        "重みは事前取得すること（実行時 DL・リトライ・代替 URL は行わない）。"
        "取得手順は docs/melody_observability.md §6.4。"
    )


def isolate_vocals_with_provenance(
    audio_path: Union[str, Path],
    *,
    model: str = DEFAULT_MODEL,
    device: str = "cpu",
) -> VocalsSeparation:
    """vocals stem を分離し、stem / weights の content pin つきで返す。

    分離**前**に重みプロビジョニング・ゲート（`resolve_separation_weights`）を必ず
    通す。これにより「導入済だが重み未取得」の環境で torch hub の download 経路に
    入らない（D-1）。それでも重み取得に起因する例外が漏れた場合は
    `LearnedModelUnavailable` へ写像し、`--external` run を落とさない。

    分離**後**に重みを再 hash して pin と一致することを確認する（TOCTOU）。別プロセスが
    実行中に cache を差し替えると、pin は旧 bytes・stem は新 bytes 由来という乖離が
    起きうるため、一致しなければ stem を返さず `LearnedModelUnavailable` にする。

    Raises
    ------
    LearnedModelUnavailable
        demucs 未導入 / 重み未取得 / 重み取得起因の失敗 / 実行中に重みが変化したとき。
    """
    weights = resolve_separation_weights(model)
    try:
        bundle = separate_stems(audio_path, model=model, device=device)
    except SeparatorNotAvailableError as exc:
        raise LearnedModelUnavailable(_INSTALL_HINT) from exc
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, RuntimeError) as exc:
        # 重み取得に起因する失敗（CDN 遮断・cache 破損・torch hub の RuntimeError）を
        # 他の optional 抽出器と同じ失敗形へ写像する。元例外は `from exc` で連鎖を保つ。
        raise LearnedModelUnavailable(_weights_failure_message(exc)) from exc
    # 使用時点での再検証（TOCTOU・Codex #217）。hash を採ってから demucs が独立に
    # 同じファイルを開くまでの間に、別プロセスが cache を provisioning / upgrade /
    # 差し替えすると、pin は旧 bytes を指しつつ stem は新 bytes 由来になりうる。
    # 分離後にもう一度（memo を迂回して）実バイトを読み、pin と一致しなければ
    # **stem を返さない** — 対応しない pin を publish するより unavailable が正しい。
    try:
        post_sha256 = sha256_of_files(weights.paths, use_cache=False)
    except OSError as exc:
        raise LearnedModelUnavailable(
            f"demucs weights disappeared during separation ({type(exc).__name__}: {exc}); "
            "pin と stem の対応を保証できないため unavailable として扱う"
        ) from exc
    if post_sha256 != weights.sha256:
        raise LearnedModelUnavailable(
            "demucs weights changed during separation "
            f"(before={weights.sha256}, after={post_sha256}); "
            "pin が stem を生んだモデル入力と対応しないため unavailable として扱う"
            "（重みの provisioning/更新と実測 run を同時に走らせないこと）"
        )
    waveform = np.asarray(bundle.stems["vocals"], dtype=np.float32)
    return VocalsSeparation(
        waveform=waveform,
        sample_rate=int(bundle.sample_rate),
        model=model,
        stem_sha256=sha256_of_float32(waveform),
        weights_sha256=weights.sha256,
        weights_filenames=weights.filenames,
        version=weights.version,
    )


def isolate_vocals(
    audio_path: Union[str, Path],
    *,
    model: str = DEFAULT_MODEL,
    device: str = "cpu",
) -> Tuple[np.ndarray, int]:
    """`audio_path` から Demucs で vocals stem を分離し (waveform, sample_rate)。

    provenance を要らない呼び出し側向けの薄いラッパ
    （`isolate_vocals_with_provenance` に委譲する）。

    Raises
    ------
    LearnedModelUnavailable
        demucs 未導入 / 重み未取得 / 重み取得起因の失敗のとき。
    """
    result = isolate_vocals_with_provenance(audio_path, model=model, device=device)
    return result.waveform, result.sample_rate
