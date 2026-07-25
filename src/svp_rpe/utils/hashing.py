"""utils/hashing.py — content pin（sha256）の共通ヘルパ。

M1-real の provenance 要求（`docs/melody_observability.md` §6.5）は、観測が実際に
読んだ入力——分離後 vocals stem の**生サンプル**とモデル重みファイルの**bytes**——を
sha256 で pin することを求める。ここはその 2 種の digest を single source of truth
として定義する。

- `sha256_of_float32`: 波形を float32 の生サンプル bytes として hash する
  （`registry.yaml` の `waveform_sha256` と同方式。soundfile のコンテナ差・
  ビット深度差に依存しない）。
- `sha256_of_files`: 複数ファイルを **名前 + NUL + bytes** の順で 1 本の digest に
  畳む（`run_melody_observability.py` の `_generator_code_sha256` と同方式）。
  Demucs の bag モデル（`htdemucs_ft` = 4 チェックポイント）や basic-pitch の
  SavedModel ディレクトリのように、**複数ファイルで 1 モデル**になる場合に使う。

`sha256_of_files` は同一プロセス内の再 hash を (path, size, mtime_ns) キーで
memoize する。M1-real の slow-lane 実測は 5 素材 × 4 経路 × n≥2 で同じ重み
（Demucs bag は約 300MB）を何度も読むため、素朴に毎回読むと計測時間の大半が
hash になる。mtime/size が変われば別キーになるので stale digest は返さない。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Iterable, List, Tuple, Union

if TYPE_CHECKING:  # pragma: no cover - 型注釈専用
    import numpy as np

__all__ = [
    "sha256_of_float32",
    "sha256_of_files",
    "file_sha256",
    "combine_named_digests",
]

_READ_CHUNK = 1 << 20  # 1 MiB

# (resolved path, size, mtime_ns) -> digest。プロセス内キャッシュ。
_FILE_DIGEST_CACHE: Dict[Tuple[str, int, int], str] = {}


def sha256_of_float32(samples: "np.ndarray") -> str:
    """波形を float32 の生サンプル bytes として hash する。

    numpy は**関数内 import** にしている。本モジュールは `melody/provenance` の
    import 閉包に入るため、module レベルで numpy を引くと「numpy のコード pin を
    bind する前に numpy が import されて cache される」窓が開く（Codex #217）。
    """
    import numpy as np

    return hashlib.sha256(np.ascontiguousarray(samples, dtype=np.float32).tobytes()).hexdigest()


def file_sha256(path: Union[str, Path], *, use_cache: bool = True) -> str:
    """1 ファイルの sha256（chunk 読み・(path, size, mtime) で memoize）。

    `use_cache=False` は memo を**読まずに**必ず実バイトを読み直す。使用時点での
    再検証（TOCTOU 検出: hash した後に別プロセスが同じ path のファイルを差し替えた
    可能性を潰す）に使う — size/mtime が偶然一致する差し替えでも検出できるように、
    キャッシュ判定そのものを迂回する。
    """
    resolved = Path(path).resolve()
    stat = resolved.stat()
    key = (str(resolved), stat.st_size, stat.st_mtime_ns)
    cached = _FILE_DIGEST_CACHE.get(key) if use_cache else None
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_READ_CHUNK), b""):
            digest.update(chunk)
    hexdigest = digest.hexdigest()
    _FILE_DIGEST_CACHE[key] = hexdigest
    return hexdigest


def sha256_of_files(
    paths: Iterable[Union[str, Path]],
    *,
    root: Union[str, Path, None] = None,
    use_cache: bool = True,
) -> str:
    """複数ファイルを 1 本の digest に畳む（名前 + NUL + 内容 digest、名前順）。

    ファイル本体でなく各ファイルの sha256 を連結対象にすることで、巨大な重み
    （数百 MB）でも memoize が効く。名前を混ぜるのは、同一内容のファイルが別名で
    並んだ構成（basic-pitch SavedModel の variants 等）を区別するため。

    `root` を渡すと名前は `root` 相対 POSIX パス（ディレクトリ構造を持つ
    SavedModel 等で basename 衝突を避ける）、渡さなければ basename を使う。
    いずれも**絶対パスは digest に混ぜない**（同じ重みをどこに置いても同じ digest
    になるべきで、install prefix の差で pin が揺れてはならない）。
    """
    entries: List[Tuple[str, str]] = []
    root_path = Path(root).resolve() if root is not None else None
    for path in paths:
        resolved = Path(path)
        if root_path is not None:
            try:
                label = resolved.resolve().relative_to(root_path).as_posix()
            except ValueError:
                label = resolved.name
        else:
            label = resolved.name
        entries.append((label, file_sha256(resolved, use_cache=use_cache)))
    return combine_named_digests(entries)


def combine_named_digests(entries: Iterable[Tuple[str, str]]) -> str:
    """(名前, digest) の集合を 1 本の digest へ畳む（名前順・名前 + NUL + digest）。

    `sha256_of_files` と**同一の合成規則**。ファイルから読んだ digest と、既に
    メモリ上にあるスナップショット（例: 一度だけ読んだ demucs メタデータ bytes）の
    digest を混ぜて畳むために公開する（同じ bytes を 2 度読まないための TOCTOU 対策）。
    """
    ordered = sorted(entries)
    combined = hashlib.sha256()
    for name, digest in ordered:
        combined.update(name.encode("utf-8"))
        combined.update(b"\0")
        combined.update(digest.encode("ascii"))
        combined.update(b"\0")
    return combined.hexdigest()
