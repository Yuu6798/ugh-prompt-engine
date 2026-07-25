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

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from svp_rpe.rpe.learned import LearnedModelUnavailable
from svp_rpe.utils.hashing import file_sha256, sha256_of_files

__all__ = [
    "ARTIFACT_BEARING_EXTRACTORS",
    "ExtractorWeights",
    "extractor_weights_fingerprint",
    "require_extractor_weights_fingerprint",
    "extractor_code_sha256",
    "extractor_code_fingerprint",
    "extractor_code_packages_for",
    "package_code_sha256",
    "packages_code_sha256",
    "package_code_state",
    "SEPARATION_CODE_PACKAGES",
    "separation_code_fingerprint",
    "bind_inference_code_pins",
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
# 抽出器 → 推論を実行するパッケージ群（自身 + **バックエンド** + **本アダプタが推論前に
# 適用する前処理ライブラリ**）。basic-pitch / CREPE は
# TensorFlow(/Keras) がモデルグラフを実行するので、ローカル patch された backend は
# 抽出器パッケージだけを hash しても検出できない（Codex #217）。Melodia は essentia の
# ネイティブ実装自身が算法、pyin は librosa 自身が算法（numpy/scipy は汎用数値基盤で
# 「モデルを走らせる backend」ではないため線をここで引く）。
# import できないものは飛ばし、**実際に覆った名前を report に列挙**する（被覆の正直会計）。
# **librosa は全経路に入る**: 本アダプタ層が librosa を使って (a) 非分離経路の波形
# decode（`_load_route_waveform`）、(b) Melodia 入力のリサンプル、(c) basic-pitch の
# 被覆分母となる実尺取得（`librosa.get_duration`）を行うため。patch された librosa は
# 抽出器へ渡る波形やゲート指標を変えるのに、source audio hash も抽出器 pin も version も
# 動かない（Codex #217）。**numpy / scipy も本層のコードが直接呼ぶので閉包に入れる** —
# `extractors.py` は `asarray` / `isfinite` / `where` / `nan_to_num` で観測値そのものを
# 組み立て、分離側も stem を numpy で正規化する。patch された numpy は観測とゲート指標を
# 変えるのに、抽出器 pin も weights pin も version も動かない。
_EXTRACTOR_CODE_PACKAGES = {
    # CREPE は 16kHz 以外の入力を **内部で `resampy`** によりリサンプルしてから
    # モデルへ渡す。patch された resampy はモデルに届くサンプルを変えるのに、
    # crepe/TF/weights/version のどの pin も動かない（Codex #217）。
    "crepe": ("crepe", "tensorflow", "keras", "librosa", "resampy", "soundfile", "numpy"),
    "basic_pitch": (
        "basic_pitch",
        "tensorflow",
        "keras",
        "onnxruntime",
        "coremltools",
        "tflite_runtime",
        "librosa",
        "soundfile",
        "numpy",
    ),
    "melodia": ("essentia", "librosa", "soundfile", "numpy"),
    # pyin は **scipy を直接実行する**: `extract_pyin_observation` が
    # `_highpass_melody_signal`（`scipy.signal.butter` / `sosfiltfilt`）で波形を
    # 前処理してから `librosa.pyin` へ渡す（physical_features.py:1170-1182）。
    # patch された scipy はフィルタ後の波形＝F0 トラック＝ゲート判定を変えるのに、
    # librosa の pin も version も動かない（Codex #217）。汎用数値基盤を線の外に
    # 置く原則の例外は「本層のコードが直接呼ぶ」ことを根拠とする。
    "pyin": ("librosa", "scipy", "soundfile", "numpy"),
}

# 分離器（Demucs）の推論を実行するパッケージ群。
SEPARATION_CODE_PACKAGES = ("demucs", "torch", "numpy")

# コードとみなす拡張子（Python source + ネイティブ拡張）。モデル artifact（.h5 等）は
# `extractor_weights_sha256` 側で別途 pin するので、二重計上せず責務を分ける。
_CODE_SUFFIXES = (".py", ".so", ".pyd", ".dylib")

# ネイティブ共有ライブラリ名の規約（版番号付き `.so.1` / macOS の `.1.dylib` / Windows）。
_NATIVE_LIBRARY_RE = re.compile(r"\.(so|dylib|dll|pyd)(\.\d+)*$")


# パッケージのコード hash 解決結果。`absent`（未導入 = optional backend が無いだけ）と
# `unhashable`（**導入済みで推論に使われうるのに hash できない**）を区別する（Codex #217）。
# 後者を skip して他のパッケージだけで digest を作ると、実行された実装の一部を覆わない
# pin を「揃っている」として publish してしまう。
STATE_OK = "ok"
STATE_ABSENT = "absent"
STATE_UNHASHABLE = "unhashable"


# 単一モジュール配布の同梱物（モジュール名 → 兄弟のモジュール名 / データディレクトリ名）。
# `soundfile` は `soundfile.py` 単体 + cffi バインディング `_soundfile.py` +
# `_soundfile_data/`（**libsndfile 本体のネイティブ共有ライブラリ**）で構成される。
# デコードの実体は libsndfile なので、これを外すと「別ビルドの libsndfile が返した
# サンプル」を pin 済みとして扱ってしまう（Codex #217）。
_MODULE_COMPANIONS = {
    "soundfile": ("_soundfile.py", "_soundfile_data"),
}

# 単一モジュールが **同梱ネイティブを持たない install 形態**（distro / source ビルド）で
# dlopen するシステム共有ライブラリ（モジュール名 → `ctypes.util.find_library` 名）。
# wheel なら `_soundfile_data/libsndfile*.so` が同梱されるが、`apt install python3-soundfile`
# 等では soundfile.py が **システムの libsndfile** を読む。デコードの実体はそちらなので、
# 同梱物が無い形態で「wrapper だけ pin」して済ませると、別ビルドの libsndfile が返した
# サンプルに同一 provenance が付く（Codex #217）。解決できなければ unhashable = fail-closed。
_MODULE_SYSTEM_LIBRARIES = {
    "soundfile": ("sndfile",),
}


def _module_companion_files(package: str, root: Path) -> "list[Path]":
    """単一モジュール配布の同梱物（兄弟モジュール / データディレクトリ）を集める。

    データディレクトリは**拡張子で絞らない**（`libsndfile_x86_64.so` のように
    プラットフォーム名や `.so.1` 形式の版番号が付き、`_CODE_SUFFIXES` で漏れるため）。
    """
    files: "list[Path]" = []
    bundled_native = False
    for name in _MODULE_COMPANIONS.get(package, ()):
        target = root / name
        if target.is_file():
            files.append(target)
        elif target.is_dir():
            found = sorted(
                path
                for path in target.rglob("*")
                if path.is_file() and "__pycache__" not in path.parts
            )
            files.extend(found)
            bundled_native = bundled_native or any(_is_native_library(p) for p in found)
    if bundled_native:
        return files
    # 同梱ネイティブが無い install 形態 → 実際に dlopen されるシステムライブラリを解決する。
    for soname in _MODULE_SYSTEM_LIBRARIES.get(package, ()):
        resolved = _system_library_path(soname)
        if resolved is None:
            raise OSError(f"system library lib{soname} could not be resolved for {package}")
        files.append(resolved)
    return files


def _is_native_library(path: Path) -> bool:
    """`libsndfile.so.1` / `libc10.so` / `.dylib` / `.dll` 等のネイティブ共有ライブラリか。

    **版番号付き（`lib*.so.1`）と Windows の `.dll`** を拾うのが要点（Codex #217）。
    TensorFlow / PyTorch はこの形のバックエンドライブラリを推論時にロードするため、
    `_CODE_SUFFIXES` の 4 拡張子だけで走査すると差し替えを検出できない。
    """
    return bool(_NATIVE_LIBRARY_RE.search(path.name))


def _system_library_path(soname: str) -> Optional[Path]:
    """`ctypes.util.find_library(soname)` が指すライブラリの実体パスを解決する。

    `find_library` は Linux では soname（`libsndfile.so.1`）しか返さないため、
    実体パスは dlopen 後の `/proc/self/maps` から引く（macOS は絶対パスが返る）。
    dlopen 自体は Python の import ではないので、コード pin を import より前に
    bind する規律とは衝突しない（同じライブラリを後で soundfile が読む）。
    """
    import ctypes.util

    found = ctypes.util.find_library(soname)
    if not found:
        return None
    candidate = Path(found)
    if candidate.is_absolute() and candidate.is_file():
        return candidate.resolve()
    try:
        ctypes.CDLL(found)
    except OSError:
        return None
    maps = Path("/proc/self/maps")
    if not maps.is_file():  # pragma: no cover - 非 Linux
        return None
    try:
        for line in maps.read_text(errors="replace").splitlines():
            path_field = line.split(" ", 5)[-1].strip()
            if path_field.endswith(found) or f"/{found}" in path_field:
                resolved = Path(path_field)
                if resolved.is_file():
                    return resolved.resolve()
    except OSError:  # pragma: no cover - /proc 読み取り失敗
        return None
    return None


def package_code_state(
    package: str, *, use_cache: bool = True
) -> "tuple[str, Optional[str]]":
    """`package` のコード hash を (state, digest) で返す（state は上記 3 値）。

    **import を起こさずに** `importlib.util.find_spec` で場所だけを解決する（Codex #217）。
    `import_module` で解決すると、hash より先にモジュールが読み込まれて cache され、
    「import 済みの旧コードが実行され、hash は新ファイルを見る」窓ができる。find_spec は
    モジュールを実行しないので、bind を**あらゆる import より前**に置ける。
    """
    import importlib.util

    try:
        spec = importlib.util.find_spec(package)
    except Exception:
        # finder 自体の失敗（sys.modules に載っているが `__spec__` が無い =`ValueError`、
        # meta path finder の例外等）。**未導入なら top-level 名は None を返す**ので、
        # 例外は「導入されている可能性があるのに解決できない」状態を意味する。
        # absent として skip すると、実行されうる実装を覆わない digest を「揃った」と
        # 見なして publish してしまうため unhashable = fail-closed に倒す（Codex #217）。
        return STATE_UNHASHABLE, None
    if spec is None:
        return STATE_ABSENT, None
    origin = getattr(spec, "origin", None)
    if not origin or origin in ("built-in", "frozen"):
        # namespace / zip import 等。**導入されている**のに hash 対象を特定できない。
        return STATE_UNHASHABLE, None
    origin_path = Path(origin).resolve()
    if getattr(spec, "submodule_search_locations", None) is None:
        # **単一モジュール**（`soundfile.py` のように site-packages 直下に 1 ファイル）。
        # パッケージと同じく親ディレクトリを rglob すると **site-packages 全体**を
        # hash してしまい、無関係な install で pin が動く（かつ極端に遅い）。
        # モジュール本体と、規約で決まる同梱物（`_soundfile.py` /
        # `_soundfile_data/` の libsndfile 等）だけを対象にする（Codex #217）。
        root = origin_path.parent
        try:
            files = [origin_path] + _module_companion_files(package, root)
        except OSError:
            return STATE_UNHASHABLE, None
    else:
        root = origin_path.parent
        try:
            files = sorted(
                path
                for path in root.rglob("*")
                if path.is_file()
                and (path.suffix in _CODE_SUFFIXES or _is_native_library(path))
                and "__pycache__" not in path.parts
            )
        except OSError:
            return STATE_UNHASHABLE, None
    if not files:
        return STATE_UNHASHABLE, None
    try:
        return STATE_OK, sha256_of_files(files, root=root, use_cache=use_cache)
    except OSError:
        return STATE_UNHASHABLE, None


def package_code_sha256(package: str, *, use_cache: bool = True) -> Optional[str]:
    """`package`（third-party）の推論コード一式の content hash（取れなければ None）。

    パッケージディレクトリ配下の `.py` / ネイティブ拡張を再帰的に hash する。
    同一 version のまま patch された install を、version 比較では検出できないため
    （Codex #217）。名前はパッケージ root からの相対 POSIX パスで安定させる。
    """
    return package_code_state(package, use_cache=use_cache)[1]


def packages_code_sha256(
    packages: "tuple", *, use_cache: bool = True
) -> "tuple[Optional[str], tuple]":
    """複数パッケージのコード hash を 1 本へ畳み、(digest, 覆った名前) を返す。

    import できないパッケージは飛ばす（optional backend は環境によって異なるため）。
    覆った名前を返すのは、pin が**何を含んでいるか**を report 側で明示するため。
    """
    import hashlib

    covered = []
    digest = hashlib.sha256()
    for package in packages:
        state, package_digest = package_code_state(package, use_cache=use_cache)
        if state == STATE_ABSENT:
            continue  # 未導入の optional backend は「実行されていない」ので対象外
        if state == STATE_UNHASHABLE:
            # **導入済み = 実行されうる**のに hash できない。skip して他だけで digest を
            # 作ると、実行された実装の一部を覆わない pin を「揃っている」と誤認する。
            raise LearnedModelUnavailable(
                f"inference package {package!r} is installed but its code cannot be "
                "fingerprinted (namespace/zip layout or unreadable files); "
                "実装の一部を覆わない pin を publish しないため unavailable として扱う"
            )
        covered.append(package)
        digest.update(package.encode("utf-8"))
        digest.update(b"\0")
        digest.update(package_digest.encode("ascii"))
        digest.update(b"\0")
    if not covered:
        return None, ()
    return digest.hexdigest(), tuple(covered)


def separation_code_fingerprint(*, use_cache: bool = True) -> "tuple[Optional[str], tuple]":
    """分離（Demucs）が実行するコードの hash と、覆った名前を返す。

    パッケージ（demucs / torch）に加え、**CLI 経路のときだけ** `python -m demucs` が
    デコードに使う `ffmpeg` / `ffprobe` の実行ファイルも hash する（Codex #217）。
    `_demucs_subprocess_env()` は両者の PATH 実在を必須にしており、別ビルドの FFmpeg は
    分離へ入る波形そのものを変えるのに、demucs/torch の pin も model version も
    weights pin も動かない。API 経路（in-process）は FFmpeg を経由しないので対象外
    ——「実行されていないものを pin したことにしない」正直会計。

    実行ファイルを解決できない / 読めない場合は `LearnedModelUnavailable`（fail-closed）。
    """
    import hashlib
    import shutil

    digest, covered = packages_code_sha256(SEPARATION_CODE_PACKAGES, use_cache=use_cache)
    executables = _cli_separation_executables()
    if not executables:
        return digest, covered
    folded = hashlib.sha256()
    folded.update((digest or "").encode("ascii"))
    folded.update(b"\0")
    names = list(covered)
    for tool in executables:
        resolved = shutil.which(tool)
        if resolved is None:
            raise LearnedModelUnavailable(
                f"Demucs CLI separation requires {tool!r} on PATH but it could not be "
                "resolved; pin を出せない経路で分離しないため unavailable として扱う"
            )
        try:
            tool_digest = file_sha256(Path(resolved), use_cache=use_cache)
        except OSError as exc:
            raise LearnedModelUnavailable(
                f"Demucs CLI executable {resolved!r} could not be fingerprinted "
                f"({type(exc).__name__}: {exc}); unavailable として扱う"
            ) from exc
        folded.update(tool.encode("utf-8"))
        folded.update(b"\0")
        folded.update(tool_digest.encode("ascii"))
        folded.update(b"\0")
        names.append(tool)
    return folded.hexdigest(), tuple(names)


def _cli_separation_executables() -> "tuple":
    """CLI 経路で demucs に渡す実行ファイル名（API 経路なら空）。

    **判定のために demucs を import しない**（それでは pre-import bind の意味が消える）。
    `io.source_separator` が既に読み込まれていればその実測値を使い、まだなら
    `find_spec("demucs.api")` の有無で代替する。代替判定が外れた場合（spec はあるが
    import に失敗して CLI へ落ちる等）は、分離**後**の再 hash が実測値で計算されて
    digest が食い違うため、既存の before/after 比較が fail-closed で拾う。
    """
    import importlib.util
    import sys

    module = sys.modules.get("svp_rpe.io.source_separator")
    if module is not None:
        if getattr(module, "_DemucsAPI", None) is not None:
            return ()
        return ("ffmpeg", "ffprobe")
    try:
        if importlib.util.find_spec("demucs.api") is not None:
            return ()
    except Exception:
        return ()
    return ("ffmpeg", "ffprobe")


def extractor_code_packages_for(extractor: str) -> "tuple":
    """`extractor` の推論を実行するパッケージ名（未定義なら空 tuple）。"""
    return _EXTRACTOR_CODE_PACKAGES.get(extractor, ())


def extractor_code_sha256(extractor: str, *, use_cache: bool = True) -> Optional[str]:
    """`extractor` の推論を実行するパッケージ群（自身 + backend）のコード hash。"""
    return extractor_code_fingerprint(extractor, use_cache=use_cache)[0]


def extractor_code_fingerprint(
    extractor: str, *, use_cache: bool = True
) -> "tuple[Optional[str], tuple]":
    """`extractor` のコード hash と、実際に覆ったパッケージ名を返す。"""
    packages = _EXTRACTOR_CODE_PACKAGES.get(extractor)
    if packages is None:
        return None, ()
    return packages_code_sha256(packages, use_cache=use_cache)


def bind_inference_code_pins() -> "dict":
    """推論パッケージのコード pin を**プロセス起動直後**に確定する（#217）。

    ハーネスは route を回す前に `_generator_code_sha256()` を計算し、その閉包探索が
    `svp_rpe.io.source_separator` を import → `demucs.api` を cache する。route 内で
    bind すると、その import より後になり「cache 済みの旧コードが分離し、hash は新
    ファイルを見る」窓が残る。run の最初にここを呼べば、**どの optional runtime を
    import するより前**に digest が固定される（`find_spec` なので import を起こさない）。

    未導入（absent）は飛ばし、hash 不能（unhashable）はここでは握って進む
    ——実際にその経路を使う時点の `_bind_code_pin(required=True)` が fail-closed する。
    戻り値は bind した {key: digest}（診断用）。
    """
    bound = {}
    try:
        separation_digest, _ = separation_code_fingerprint()
    except LearnedModelUnavailable:
        separation_digest = None
    if separation_digest is not None:
        bound["separation:code"] = record_load_time_pin("separation:code", separation_digest)
    for name, packages in _EXTRACTOR_CODE_PACKAGES.items():
        try:
            digest, _ = packages_code_sha256(packages)
        except LearnedModelUnavailable:
            continue
        if digest is not None:
            bound[f"{name}:code"] = record_load_time_pin(f"{name}:code", digest)
    return bound


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
