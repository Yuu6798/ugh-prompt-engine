"""melody/provenance.py — 観測経路が読んだモデル入力の content pin（D-2）。

M1-real の評価器（`scripts/run_melody_observability.py` の
`evaluate_m1_real_go_bar`）は、measured な学習抽出器経路に
`extractor_weights_sha256` を必須とする（#59）。同一 package version でも別
bundled/local weights ならモデル入力が変わるためで、この pin なしに学習抽出器経路
を stable Go survivor に数えない。本モジュールは各抽出器について「実際に読まれる
モデル artifact」を解決し、その content hash を返す。

emit しないケース（`None` を返す）:

- **pyin**: librosa の DSP。重みファイルを持たない（評価器も要求しない）。
- **依存未導入 / artifact を特定できない**: 推測で digest を作らず `None`。
  評価器側が pin 欠落として fail-closed するので、ここで嘘をつく必要はない。

`extractor_weights_kind`:

- ``model_weights``: 学習済み重みファイル（CREPE の ``model-full.h5``、
  basic-pitch の SavedModel など）。
- ``library_binary``: **重みを持たない**実装（Essentia の Melodia は DSP 算法）に
  対する実装バイナリの指紋。フィールド名は評価器契約に合わせて
  `extractor_weights_sha256` のままだが、何を pin したのかを kind で明示する
  （「重みを持たないものに重み hash を主張しない」正直会計）。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from svp_rpe.rpe.learned import LearnedModelUnavailable
from svp_rpe.utils.hashing import sha256_of_files

__all__ = [
    "ARTIFACT_BEARING_EXTRACTORS",
    "ExtractorWeights",
    "extractor_weights_fingerprint",
    "require_extractor_weights_fingerprint",
    "extractor_code_sha256",
    "package_code_sha256",
    "record_load_time_pin",
    "load_time_pin",
    "reset_load_time_pins",
]

KIND_MODEL_WEIGHTS = "model_weights"
KIND_LIBRARY_BINARY = "library_binary"

# モデル artifact を**必ず持つ**抽出器（評価器が `extractor_weights_sha256` を必須とする
# 集合と一致）。これらで指紋が取れないのは「重みが無い」ではなく **provisioning 失敗**
# なので、観測を続けさせない（Codex #217）。pyin は DSP で artifact を持たない。
ARTIFACT_BEARING_EXTRACTORS = frozenset({"crepe", "basic_pitch", "melodia"})

# プロセス内で最初に観測した artifact digest（extractor → sha256）。
# CREPE は `crepe.core.models` にロード済みモデルを**プロセス global で cache** する。
# 一度ロードした後に `model-full.h5` が差し替わると、ディスクの pre/post 指紋は新 bytes で
# 一致するのに `crepe.predict()` は**古い in-memory モデル**を使い続ける。その状態で新 pin を
# emit すると、旧重みが生んだ観測に新しい pin が付き、同一プロセスの 2 repeats が揃って
# それを主張しうる（Codex #217）。そこで「最初のロード時点の digest」をプロセス内に保持し、
# 以降それと食い違ったら fail-closed にする（どちらのモデルが使われているか判別できない以上、
# pin を publish しないのが正しい）。
_LOAD_TIME_PINS: dict = {}


@dataclass(frozen=True)
class ExtractorWeights:
    """抽出器が読んだモデル artifact の指紋。"""

    extractor: str
    kind: str
    sha256: str
    files: Tuple[str, ...]


def extractor_weights_fingerprint(
    extractor: str, *, use_cache: bool = True
) -> Optional[ExtractorWeights]:
    """`extractor` が読むモデル artifact の指紋（無い/取れないなら None）。

    抽出そのものは行わない（artifact の解決と hash のみ）。依存未導入・artifact
    未特定は `LearnedModelUnavailable` として adapter 側から上がってくるので、
    ここで握って `None` に落とす（観測 run 自体は落とさない）。

    `use_cache=False` は digest の memo を迂回して実バイトを読み直す。推論の前後で
    artifact が差し替わっていないかを検証する post-pass に使う（size/mtime が偶然
    一致する差し替えでも検出できるようにするため・Codex #217）。
    """
    try:
        return _resolve_fingerprint(extractor, use_cache=use_cache)
    except (LearnedModelUnavailable, OSError):
        return None


def require_extractor_weights_fingerprint(
    extractor: str, *, use_cache: bool = True
) -> Optional[ExtractorWeights]:
    """artifact を持つ抽出器では、指紋を採れないことを `LearnedModelUnavailable` にする。

    `extractor_weights_fingerprint` は解決失敗を `None` に畳むため、そのまま推論へ
    進むと (a) 未 cache の抽出器が生の I/O 例外を投げて `--external` run 全体を落とす、
    (b) cache 済みの抽出器が **評価器が要求する hash を欠いた measured 行**を返し、
    後続の Go-bar 評価が「その経路だけ unavailable」ではなく丸ごと fail-closed する、
    のいずれかになる（Codex #217）。artifact を持つ抽出器は provisioning 失敗として
    早期に落とし、当該 route だけを `unavailable` にする。

    `pyin` 等の artifact を持たない経路は従来どおり `None` を返す（評価器も要求しない）。
    """
    if extractor not in ARTIFACT_BEARING_EXTRACTORS:
        return extractor_weights_fingerprint(extractor, use_cache=use_cache)
    try:
        fingerprint = _resolve_fingerprint(extractor, use_cache=use_cache)
    except OSError as exc:
        raise LearnedModelUnavailable(
            f"{extractor} model artifact is unreadable ({type(exc).__name__}: {exc}); "
            "pin を出せない経路で観測しないため unavailable として扱う"
        ) from exc
    if fingerprint is None:  # pragma: no cover - 上の集合と _resolve の分岐は対応済み
        raise LearnedModelUnavailable(
            f"{extractor} model artifact could not be resolved; "
            "pin を出せない経路で観測しないため unavailable として扱う"
        )
    return fingerprint


def _resolve_fingerprint(
    extractor: str, *, use_cache: bool = True
) -> Optional[ExtractorWeights]:
    """指紋の解決本体（失敗を畳まず送出する）。"""
    if extractor == "crepe":
        from svp_rpe.rpe.learned.crepe_adapter import crepe_weight_files

        files = crepe_weight_files()
        return ExtractorWeights(
            extractor=extractor,
            kind=KIND_MODEL_WEIGHTS,
            sha256=sha256_of_files(files, use_cache=use_cache),
            files=tuple(f.name for f in files),
        )
    if extractor == "basic_pitch":
        from svp_rpe.rpe.learned.basic_pitch_adapter import basic_pitch_weight_files

        files, root = basic_pitch_weight_files()
        return ExtractorWeights(
            extractor=extractor,
            kind=KIND_MODEL_WEIGHTS,
            sha256=sha256_of_files(files, root=root, use_cache=use_cache),
            files=tuple(sorted(f.name for f in files)),
        )
    if extractor == "melodia":
        from svp_rpe.rpe.learned.melodia_adapter import melodia_implementation_files

        files, root = melodia_implementation_files()
        return ExtractorWeights(
            extractor=extractor,
            kind=KIND_LIBRARY_BINARY,
            sha256=sha256_of_files(files, root=root, use_cache=use_cache),
            files=tuple(sorted(f.name for f in files)),
        )
    # pyin / none: 重みを持たない DSP 経路（評価器も pin を要求しない）。
    return None


# 抽出器 → 推論コードを提供する third-party パッケージ（import 名）。
# 重み hash と distribution version は「同じ bytes か / 同じリリースか」しか保証せず、
# **同一 version のままローカルで patch / repack された**パッケージは素通りする
# （`_generator_code_paths()` は first-party しか含めない）。実際に推論した third-party
# コードの content hash も pin する（Codex #217）。
_EXTRACTOR_CODE_PACKAGE = {
    "crepe": "crepe",
    "basic_pitch": "basic_pitch",
    "melodia": "essentia",
    "pyin": "librosa",
}

# コードとみなす拡張子（Python source + ネイティブ拡張）。モデル artifact（.h5 等）は
# `extractor_weights_sha256` 側で別途 pin するので、二重計上せず責務を分ける。
_CODE_SUFFIXES = (".py", ".so", ".pyd", ".dylib")


def package_code_sha256(package: str, *, use_cache: bool = True) -> Optional[str]:
    """`package`（third-party）の推論コード一式の content hash（取れなければ None）。

    パッケージディレクトリ配下の `.py` / ネイティブ拡張を再帰的に hash する。
    同一 version のまま patch された install を、version 比較では検出できないため
    （Codex #217）。名前はパッケージ root からの相対 POSIX パスで安定させる。
    """
    import importlib

    try:
        module = importlib.import_module(package)
    except Exception:
        return None
    module_file = getattr(module, "__file__", None)
    if not module_file:
        return None
    root = Path(module_file).resolve().parent
    files = sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in _CODE_SUFFIXES
        and "__pycache__" not in path.parts
    )
    if not files:
        return None
    try:
        return sha256_of_files(files, root=root, use_cache=use_cache)
    except OSError:
        return None


def extractor_code_sha256(extractor: str, *, use_cache: bool = True) -> Optional[str]:
    """`extractor` の推論を提供する third-party パッケージのコード hash。"""
    package = _EXTRACTOR_CODE_PACKAGE.get(extractor)
    if package is None:
        return None
    return package_code_sha256(package, use_cache=use_cache)


def record_load_time_pin(extractor: str, sha256: str) -> str:
    """`extractor` のプロセス内 load-time pin を記録し、確定値を返す（初回のみ記録）。"""
    return _LOAD_TIME_PINS.setdefault(extractor, sha256)


def load_time_pin(extractor: str) -> Optional[str]:
    """`extractor` のプロセス内 load-time pin（未記録なら None）。"""
    return _LOAD_TIME_PINS.get(extractor)


def reset_load_time_pins() -> None:
    """load-time pin を全消去する（**新しいプロセスと同じ状態**へ戻す）。

    実運用では呼ばない — pin の意味は「このプロセスがモデルをロードした時点の
    artifact」であり、途中でリセットすると in-memory cache との対応が崩れる。
    テストが 1 プロセスで複数の artifact 構成を模すためのフック。
    """
    _LOAD_TIME_PINS.clear()
