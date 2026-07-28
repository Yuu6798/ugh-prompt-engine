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
# librosa 経由で**必ず実行される数値バックエンド**（Codex #217）。
# - `soxr`: `librosa.resample` の既定 `res_type="soxr_hq"` は SoXR のネイティブ実装が
#   リサンプルそのもの（Melodia は 44.1kHz へ、CREPE 経路も内部で resample が走る）。
# - `numba` / `llvmlite`: librosa の JIT カーネル（`librosa.sequence.viterbi` 等の
#   pyin 経路を含む）と `resampy` のリサンプルカーネルを **JIT でコンパイルして実行**する。
# 別ビルド / patch されたこれらは抽出器に届くサンプルと F0 系列を変えるのに、抽出器 pin も
# weights pin も version も動かないので、librosa を含む全閉包に足す。
_LIBROSA_BACKENDS = ("soxr", "numba", "llvmlite")

_EXTRACTOR_CODE_PACKAGES = {
    # CREPE は 16kHz 以外の入力を **内部で `resampy`** によりリサンプルしてから
    # モデルへ渡す。patch された resampy はモデルに届くサンプルを変えるのに、
    # crepe/TF/weights/version のどの pin も動かない（Codex #217）。
    # `predict(..., viterbi=True)`（本アダプタの既定）は CREPE 内部で **`hmmlearn`** の
    # HMM デコードを走らせ、F0 系列の選択そのものを決める（Codex #217）。
    "crepe": (
        "crepe",
        "tensorflow",
        "keras",
        "hmmlearn",
        "librosa",
        "resampy",
        "soundfile",
        "numpy",
    )
    + _LIBROSA_BACKENDS,
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
    )
    + _LIBROSA_BACKENDS,
    "melodia": ("essentia", "librosa", "soundfile", "numpy") + _LIBROSA_BACKENDS,
    # pyin は **scipy を直接実行する**: `extract_pyin_observation` が
    # `_highpass_melody_signal`（`scipy.signal.butter` / `sosfiltfilt`）で波形を
    # 前処理してから `librosa.pyin` へ渡す（physical_features.py:1170-1182）。
    # patch された scipy はフィルタ後の波形＝F0 トラック＝ゲート判定を変えるのに、
    # librosa の pin も version も動かない（Codex #217）。汎用数値基盤を線の外に
    # 置く原則の例外は「本層のコードが直接呼ぶ」ことを根拠とする。
    "pyin": ("librosa", "scipy", "soundfile", "numpy") + _LIBROSA_BACKENDS,
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

    **ロードせずに**解決するのが要点（Codex #217）。dlopen で解決すると、その時点で
    ライブラリがプロセスへ mmap され（`CDLL` オブジェクトを捨てても mapping は残る）、
    直後に同じファイルが in-place で書き換えられた場合、実行は**ロード済みの旧コード**
    のまま pre/post の指紋だけが新 bytes を指す——つまり「その観測を生んでいないコード」
    の pin が付く。ローダと同じ探索順（`LD_LIBRARY_PATH` → `ldconfig` キャッシュ）を
    自前で辿ってパスだけを得る。

    `find_library` は Linux では soname（`libsndfile.so.1`）しか返さない（macOS は
    絶対パスを返すのでそのまま使える）。解決できなければ `None` = fail-closed。
    """
    import ctypes.util

    found = ctypes.util.find_library(soname)
    if not found:
        return None
    candidate = Path(found)
    if candidate.is_absolute() and candidate.is_file():
        return candidate.resolve()
    return _resolve_soname_without_loading(found)


# 信頼できる ldconfig 実行ファイルの絶対パス候補（Codex 13 巡目 P1-A）。PATH 経由の
# 非修飾 `ldconfig` は、PATH 上に攻撃者が用意した同名コマンドがあればそれが実行され、
# `libm.so.6` 等の基盤 soname を偽の canonical system-library ディレクトリへ解決させ
# うる——`_is_under_system_library_directory`（`scripts/run_melody_accuracy.py`）が
# 検証の正解データとして使う集合そのものが汚染される。glibc/util-linux が実際に
# インストールする先はディストリビューションを問わずこの 4 箇所に限られるため、
# 絶対パスで存在確認してから実行する（PATH フォールバックはしない = fail-closed）。
_TRUSTED_LDCONFIG_CANDIDATES: "Tuple[str, ...]" = (
    "/sbin/ldconfig",
    "/usr/sbin/ldconfig",
    "/bin/ldconfig",
    "/usr/bin/ldconfig",
)

# `git` の信頼できる絶対パス候補（Codex 13 巡目 H19）。ldconfig と同じ「非修飾コマンド
# は PATH 上の偽物が実行されうる」クラス。`scripts/run_melody_accuracy.py` の bars 事前
# 登録立証（`_bars_registration_attestation`）と `--out` の git メタデータ保護
# （`_require_out_outside_git_metadata`）が経由する。
_TRUSTED_GIT_CANDIDATES: "Tuple[str, ...]" = (
    "/usr/bin/git",
    "/bin/git",
    "/usr/local/bin/git",
)

# 信頼実行するサブプロセス（ldconfig / git）に固定する最小 PATH（システムディレクトリ
# のみ）。
_TRUSTED_SUBPROCESS_PATH = "/sbin:/usr/sbin:/bin:/usr/bin"

# 信頼実行するサブプロセス自身の動的リンク・ロケール/gconv モジュールロード・CPU
# dispatch を歪めうる環境変数（Codex 13 巡目 P1-A + H20 拡張）。ldconfig/git のどちらも
# 動的リンクされた ELF 実行ファイルである以上、これらが立っていると「信頼できる絶対
# パスから起動した」つもりでも、起動されたプロセス自身が読み込む共有ライブラリ・
# ロケールデータが差し替えられ、出力が歪みうる——`LD_*` 系（動的リンカ）に加えて
# `GCONV_PATH`（iconv/gconv モジュールを攻撃者ディレクトリからロードさせる既知の
# code-loading ベクトル）・`GLIBC_TUNABLES`（ローダ挙動/CPU dispatch を変える）・
# `LOCPATH`/`NLSPATH`/`GETCONF_DIR`（同系のモジュール探索パス）を除去する。
_HARDENED_SUBPROCESS_ENV_BLOCKLIST: "Tuple[str, ...]" = (
    "LD_PRELOAD",
    "LD_AUDIT",
    "LD_LIBRARY_PATH",
    "LD_DYNAMIC_WEAK",
    "LD_ORIGIN_PATH",
    "LD_BIND_NOW",
    "LD_BIND_NOT",
    "LD_PROFILE",
    "GCONV_PATH",
    "GLIBC_TUNABLES",
    "LOCPATH",
    "NLSPATH",
    "GETCONF_DIR",
)


def _trusted_executable(candidates: "Tuple[str, ...]", *, tool_name: str) -> str:
    """`candidates`（絶対パス）から実在する実行ファイルを選ぶ。無ければ fail-closed。

    （Codex 13 巡目 P1-A、H19 で ldconfig 専用から一般化）候補を先頭から確認し、
    最初に実在した絶対パスを使う——PATH 上の非修飾コマンドには一切フォールバック
    しない。どの候補も存在しなければ `RuntimeError` で fail-closed にする。
    """
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            return str(path)
    raise RuntimeError(
        f"trusted {tool_name} binary not found at any of {candidates!r}; "
        f"PATH 上の非修飾 {tool_name} は信用しないため fail-closed"
    )


def _trusted_ldconfig_executable() -> str:
    """信頼できる絶対パス候補から実在する ldconfig を選ぶ（`_trusted_executable` 経由）。

    どの候補も存在しなければ「system-library 閉包を立証する土台（ldconfig
    キャッシュ）自体が信頼できる経路でしか手に入らない」ため fail-closed にする。
    """
    return _trusted_executable(_TRUSTED_LDCONFIG_CANDIDATES, tool_name="ldconfig")


def _trusted_git_executable() -> str:
    """信頼できる絶対パス候補から実在する git を選ぶ（Codex 13 巡目 H19）。

    どの候補も存在しなければ「bars 事前登録の立証・`--out` の git メタデータ保護が
    信頼できる経路でしか成立しない」ため fail-closed にする。
    """
    return _trusted_executable(_TRUSTED_GIT_CANDIDATES, tool_name="git")


def _hardened_subprocess_env() -> "dict[str, str]":
    """信頼実行するサブプロセス（ldconfig / git）に渡す最小化済み環境変数。

    （Codex 13 巡目 P1-A、H19/H20 で一般化）`PATH` を最小のシステムディレクトリ集合へ
    固定し、`_HARDENED_SUBPROCESS_ENV_BLOCKLIST` の環境変数を除去する。
    """
    import os

    env = dict(os.environ)
    env["PATH"] = _TRUSTED_SUBPROCESS_PATH
    for var in _HARDENED_SUBPROCESS_ENV_BLOCKLIST:
        env.pop(var, None)
    return env


def _ldconfig_cache_listing() -> str:
    """`ldconfig -p` の出力（library => path 一覧）を信頼できる経路から取得する。

    （Codex 13 巡目 P1-A）`_resolve_soname_without_loading`（本モジュール）と
    `_system_library_directories`（`scripts/run_melody_accuracy.py`）の両方が経由する
    共有ヘルパー。絶対パス候補から実在物を選び（`_trusted_ldconfig_executable` が
    無ければここで `RuntimeError` が伝播し fail-closed）、サブプロセスの `PATH` を
    最小集合へ固定し、動的リンク/ロケール解決を歪めうる環境変数を除去して起動する
    （`_hardened_subprocess_env`）。

    実行自体が失敗した場合（`OSError` / `SubprocessError`。ldconfig が古い / 権限が
    無い等）は空文字列を返し、呼び出し側の探索（rpath/runpath/`LD_LIBRARY_PATH`）
    だけで解決を続けさせる——これは「ldconfig 実行が通常の理由で失敗した」であって、
    「信頼できる実行ファイルが存在しない」（fail-closed 対象）とは区別する。
    """
    import subprocess

    ldconfig = _trusted_ldconfig_executable()
    env = _hardened_subprocess_env()
    try:
        return subprocess.run(
            [ldconfig, "-p"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env=env,
        ).stdout
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - 環境依存
        return ""


def _resolve_soname_without_loading(
    soname_file: str,
    *,
    rpath_dirs: "tuple" = (),
    runpath_dirs: "tuple" = (),
) -> Optional[Path]:
    """soname（`libsndfile.so.1`）→ 実体パス。**dlopen を起こさない**。

    glibc ローダと同じ探索順を辿る（Codex #217）:

    1. `DT_RPATH`（**`DT_RUNPATH` が無いときだけ**有効。祖先から継承した分を含む）
    2. `LD_LIBRARY_PATH`
    3. 参照元オブジェクトの `DT_RUNPATH`
    4. `ldconfig -p` キャッシュ（cache を読むだけでライブラリはロードしない。信頼
       できる絶対パスから起動する——`_ldconfig_cache_listing` 参照・Codex 13 巡目 P1-A）

    `rpath_dirs` / `runpath_dirs` は **展開済みのディレクトリ**を受け取る（`$ORIGIN` は
    それを宣言したオブジェクトからの相対なので、展開は収集側の責務）。
    """
    import os

    search: "list[Path]" = []
    if not runpath_dirs:
        search.extend(rpath_dirs)
    search.extend(
        Path(entry)
        for entry in os.environ.get("LD_LIBRARY_PATH", "").split(os.pathsep)
        if entry
    )
    search.extend(runpath_dirs)
    for directory in search:
        candidate = directory / soname_file
        if candidate.is_file():
            return candidate.resolve()
    output = _ldconfig_cache_listing()
    for line in output.splitlines():
        name, _, path_field = line.partition(" => ")
        if name.strip().split(" ", 1)[0] != soname_file:
            continue
        resolved = Path(path_field.strip())
        if resolved.is_file():
            return resolved.resolve()
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

    パッケージ（demucs / torch / numpy）に加え、Demucs がデコードに使う
    `ffmpeg` / `ffprobe` の実行ファイルも hash する（Codex #217）。**CLI 経路
    （`python -m demucs`）と API 経路（`Separator.separate_audio_file()` の
    `AudioFile` 読み出し）のどちらも外部 FFmpeg を叩く**ため、経路で区別しない。
    別ビルドの FFmpeg は分離へ入る波形そのものを変えるのに、demucs/torch の pin も
    model version も weights pin も動かない。

    PATH に無い場合の扱いは経路で分かれる（正直会計）:

    - CLI 経路: `_demucs_subprocess_env()` が実在を必須にしているので
      `LearnedModelUnavailable`（fail-closed）。
    - API 経路: FFmpeg 不在なら demucs 側が別デコーダへフォールバックし、
      **その実行ファイルは結果に影響しえない**ので covered から外して続行する。

    解決できたのに読めない場合は、どちらの経路でも fail-closed。
    """
    import hashlib
    import shutil

    digest, covered = packages_code_sha256(SEPARATION_CODE_PACKAGES, use_cache=use_cache)
    executables, required = _separation_audio_executables()
    if not executables:
        return digest, covered
    folded = hashlib.sha256()
    folded.update((digest or "").encode("ascii"))
    folded.update(b"\0")
    names = list(covered)
    for tool in executables:
        resolved = shutil.which(tool)
        if resolved is None:
            if not required:
                continue  # API 経路 + FFmpeg 不在 = 実行されないので pin 対象外
            raise LearnedModelUnavailable(
                f"Demucs CLI separation requires {tool!r} on PATH but it could not be "
                "resolved; pin を出せない経路で分離しないため unavailable として扱う"
            )
        try:
            tool_digest = file_sha256(Path(resolved), use_cache=use_cache)
        except OSError as exc:
            raise LearnedModelUnavailable(
                f"Demucs audio executable {resolved!r} could not be fingerprinted "
                f"({type(exc).__name__}: {exc}); unavailable として扱う"
            ) from exc
        folded.update(tool.encode("utf-8"))
        folded.update(b"\0")
        folded.update(tool_digest.encode("ascii"))
        folded.update(b"\0")
        names.append(tool)
        # 実行ファイル本体だけでは足りない: distro 版 FFmpeg はデコード実装を
        # `libavformat` / `libavcodec` / `libswresample` 等の共有ライブラリに持つ。
        for library in _ffmpeg_library_closure(Path(resolved)):
            try:
                library_digest = file_sha256(library, use_cache=use_cache)
            except OSError as exc:
                raise LearnedModelUnavailable(
                    f"FFmpeg shared library {str(library)!r} could not be fingerprinted "
                    f"({type(exc).__name__}: {exc}); unavailable として扱う"
                ) from exc
            folded.update(library.name.encode("utf-8"))
            folded.update(b"\0")
            folded.update(library_digest.encode("ascii"))
            folded.update(b"\0")
            names.append(library.name)
    return folded.hexdigest(), tuple(names)


# FFmpeg 自身のデコード実装を提供する共有ライブラリ群の名前規約。
# libc/libm 等の OS 基盤まで広げると「環境全体が推論スタック」になって誰も守れない線に
# なるので、**デコードの実装そのもの**（FFmpeg のライブラリ）で線を引く（Codex #217）。
_FFMPEG_LIBRARY_PREFIXES = ("libav", "libsw", "libpostproc")


def _ffmpeg_library_closure(executable: Path) -> "list[Path]":
    """`executable` が動的リンクする FFmpeg ライブラリ群を**実行せずに**解決する。

    ELF の `DT_NEEDED` を読んで soname を集め（推移的にたどる）、ローダと同じ探索順で
    実体パスへ解決する（`ldd` は対象を実行しうるので使わない・#217 と同じ規律）。
    **各オブジェクトの `DT_RPATH` / `DT_RUNPATH`（`$ORIGIN` 展開込み）を尊重する** —
    conda やアプリ同梱の FFmpeg は同じ soname の同梱 `libav*` を読むので、これを見ないと
    「同名のシステムライブラリを hash したが、デコードしたのは同梱版」になりうる
    （Codex #217）。

    静的リンク（自己完結ビルドの ELF）は依存が無いことを**読んで確認**できるので空を返す。
    一方、**非 ELF**（Mach-O / PE / ラッパスクリプト）は closure を読めないため
    `LearnedModelUnavailable` に落とす——「読めなかった」を「依存なし」と主張しない。
    解決できない FFmpeg ライブラリがある場合も同様（デコード実装の一部を覆わない pin は
    出さない）。この結果、分離経路の pin は現状 **Linux/ELF 限定**で成立する。
    """
    root_info = _elf_dynamic_info(executable)
    if root_info is None:
        # 非 ELF（Mach-O / PE / ラッパスクリプト）。依存 closure を読めないので
        # 「空 = 依存なし」と主張できない——動的リンクなら libav* の差し替えが
        # pin に写らない（Codex #217）。読めないものを覆ったことにせず落とす。
        raise LearnedModelUnavailable(
            f"dependency closure of {str(executable)!r} cannot be read (non-ELF binary); "
            "デコード実装を覆う pin を出せないため unavailable として扱う"
        )
    # (soname, 参照元の rpath ディレクトリ（継承込み）, その runpath ディレクトリ)
    pending = [
        (name, _object_rpath_dirs(executable, root_info, ()), _object_runpath_dirs(
            executable, root_info
        ))
        for name in root_info[0]
    ]
    # dedup キーは (soname, rpath_dirs, runpath_dirs)（Codex 13 巡目 H18・harness (B) と
    # 同型の是正）。soname のみで dedup すると、同一 soname が異なる継承 RPATH context
    # から多重到達したとき、先に pop された context でしか子 walk・解決を行わない——
    # 別 context では transitive soname が bundled/system のどちらか一方にしか解決され
    # ないケースを見逃す（harness `_verify_scorer_dt_needed_closure` が visited を
    # `(path, context)` にした理由と同じ構造的欠陥）。ここでは soname が同じでも
    # context ごとに独立して解決・子 walk を再検証し、`resolved` は soname ごとに
    # **観測された全ての解決先の集合**を保持する（同名 soname が context で異なる実体へ
    # 解決される場合、両方を pin に含める——「実際にロードされうる全ての実体」を
    # 過小に覆わないため。単一実体しか観測されなければ通常どおり 1 件のみ）。
    seen: "set[tuple[str, tuple, tuple]]" = set()
    resolved_by_soname: "dict[str, set[Path]]" = {}
    while pending:
        soname, rpath_dirs, runpath_dirs = pending.pop()
        visit_key = (soname, rpath_dirs, runpath_dirs)
        if visit_key in seen:
            continue
        seen.add(visit_key)
        if not soname.startswith(_FFMPEG_LIBRARY_PREFIXES):
            continue  # OS 基盤ライブラリは線の外（上のコメント参照）
        path = _resolve_soname_without_loading(
            soname, rpath_dirs=rpath_dirs, runpath_dirs=runpath_dirs
        )
        if path is None:
            raise LearnedModelUnavailable(
                f"FFmpeg shared library {soname!r} could not be located; "
                "デコード実装の一部を覆わない pin を publish しないため unavailable として扱う"
            )
        resolved_by_soname.setdefault(soname, set()).add(path)
        info = _elf_dynamic_info(path)
        if info is None:
            continue
        # `DT_RPATH` は **依存の依存にも継承される**（`DT_RUNPATH` は継承しない）。
        # 同梱 FFmpeg が実行ファイルの RPATH で libavformat を掴み、その libavformat が
        # 自前のパスを持たずに libavcodec を要求する構成では、ローダは継承 RPATH の
        # 同梱ディレクトリを使う。継承しないとシステム版を掴む（Codex #217）。
        child_rpath = _object_rpath_dirs(path, info, rpath_dirs)
        child_runpath = _object_runpath_dirs(path, info)
        pending.extend((name, child_rpath, child_runpath) for name in info[0])
    return sorted({path for paths in resolved_by_soname.values() for path in paths})


def _object_rpath_dirs(obj: Path, info: "tuple", inherited: "tuple") -> "tuple":
    """`obj` の `DT_RPATH` を展開し、祖先から継承した分を後ろに連ねる。"""
    own = _expand_loader_paths(info[1], obj)
    if own is None:
        raise LearnedModelUnavailable(
            f"DT_RPATH of {str(obj)!r} contains loader tokens that cannot be expanded; "
            "実際にロードされるライブラリを特定できないため unavailable として扱う"
        )
    return own + tuple(inherited)


def _object_runpath_dirs(obj: Path, info: "tuple") -> "tuple":
    """`obj` の `DT_RUNPATH` を展開する（継承しない）。"""
    own = _expand_loader_paths(info[2], obj)
    if own is None:
        raise LearnedModelUnavailable(
            f"DT_RUNPATH of {str(obj)!r} contains loader tokens that cannot be expanded; "
            "実際にロードされるライブラリを特定できないため unavailable として扱う"
        )
    return own


def _expand_loader_path(entry: str, origin: "Optional[Path]") -> Optional[Path]:
    """ローダ変数を展開する（展開できるのは `$ORIGIN` のみ）。

    展開できなければ `None`。呼び出し側はそれを **fail-closed** として扱う
    ——「展開できない候補を飛ばして ldconfig に落ちる」と、実際にはローダが同梱
    ライブラリを読んでいるのに同名のシステムライブラリを pin しうる（Codex #217）。
    """
    if origin is not None:
        base = str(origin.resolve().parent)
        entry = entry.replace("${ORIGIN}", base).replace("$ORIGIN", base)
    elif "$ORIGIN" in entry or "${ORIGIN}" in entry:
        return None
    if "$" in entry:
        # `$LIB` / `$PLATFORM` は **ローダが決める値**で、外から確定できない
        # （Debian の `$LIB` は `lib/x86_64-linux-gnu`、`$PLATFORM` は `haswell` の
        # ように CPU チューニング名になりうる。ポインタ幅や `uname().machine` からの
        # 推測は外れる・Codex #217）。外すと同名のシステムライブラリを掴むので、
        # 推測せず fail-closed に倒す。
        return None
    return Path(entry)


def _expand_loader_paths(
    entries: "tuple", origin: "Optional[Path]"
) -> "Optional[tuple]":
    """ローダ探索パス列を展開する（1 つでも展開できなければ None = fail-closed）。"""
    expanded = []
    for entry in entries:
        path = _expand_loader_path(entry, origin)
        if path is None:
            return None
        expanded.append(path)
    return tuple(expanded)


def _needed_sonames(path: Path) -> "Optional[list[str]]":
    """ELF の `DT_NEEDED` を読み、依存 soname を返す（ELF でなければ None）。"""
    info = _elf_dynamic_info(path)
    return None if info is None else info[0]


def _elf_dynamic_info(path: Path) -> "Optional[tuple]":
    """ELF の `.dynamic` から `(DT_NEEDED, DT_RPATH, DT_RUNPATH)` を読む。

    実行も dlopen もせず、ヘッダとセグメントを直接読むだけ（Codex #217）。
    **セクションヘッダではなくプログラムヘッダ（`PT_DYNAMIC`）を読む** — 強く strip
    されたバイナリはセクションヘッダを持たないが、ローダは `PT_DYNAMIC` で依存を解決
    するため、セクションが無いことを「静的リンク」の証拠にしてはならない（Codex #217）。
    静的リンクの確証は「`PT_DYNAMIC` が存在しないこと」で得る。

    ELF でない、または動的リンクなのに解決できない場合は `None`（呼び出し側が
    fail-closed に倒す）。
    """
    import struct

    try:
        blob = path.read_bytes()
    except OSError:
        return None
    if len(blob) < 64 or blob[:4] != b"\x7fELF":
        return None
    is_64 = blob[4] == 2
    endian = "<" if blob[5] == 1 else ">"
    try:
        if is_64:
            e_phoff, = struct.unpack_from(endian + "Q", blob, 0x20)
            e_phentsize, e_phnum = struct.unpack_from(endian + "HH", blob, 0x36)
        else:
            e_phoff, = struct.unpack_from(endian + "I", blob, 0x1C)
            e_phentsize, e_phnum = struct.unpack_from(endian + "HH", blob, 0x2A)
    except struct.error:
        return None

    loads: "list[tuple]" = []  # (vaddr, filesz, offset)
    dynamic: "Optional[tuple]" = None  # (offset, filesz)
    for index in range(e_phnum):
        base = e_phoff + index * e_phentsize
        try:
            p_type, = struct.unpack_from(endian + "I", blob, base)
            if is_64:
                p_offset, p_vaddr = struct.unpack_from(endian + "QQ", blob, base + 0x08)
                p_filesz, = struct.unpack_from(endian + "Q", blob, base + 0x20)
            else:
                p_offset, p_vaddr = struct.unpack_from(endian + "II", blob, base + 0x04)
                p_filesz, = struct.unpack_from(endian + "I", blob, base + 0x10)
        except struct.error:
            return None
        if p_type == 1:  # PT_LOAD
            loads.append((p_vaddr, p_filesz, p_offset))
        elif p_type == 2:  # PT_DYNAMIC
            dynamic = (p_offset, p_filesz)
    if dynamic is None:
        # `PT_DYNAMIC` が無い = 静的リンク（依存が無いことを**読んで確認**できた）。
        return [], (), ()

    def _vaddr_to_offset(vaddr: int) -> Optional[int]:
        for seg_vaddr, seg_filesz, seg_offset in loads:
            if seg_vaddr <= vaddr < seg_vaddr + seg_filesz:
                return seg_offset + (vaddr - seg_vaddr)
        return None

    entry_size = 16 if is_64 else 8
    fmt = endian + ("Qq" if is_64 else "Ii")
    needed_offsets: "list[int]" = []
    rpath_offsets: "list[int]" = []
    runpath_offsets: "list[int]" = []
    strtab_vaddr: Optional[int] = None
    strsz = 0
    dyn_offset, dyn_size = dynamic
    for position in range(dyn_offset, dyn_offset + dyn_size, entry_size):
        try:
            tag, value = struct.unpack_from(fmt, blob, position)
        except struct.error:
            break
        if tag == 0:  # DT_NULL
            break
        if tag == 1:  # DT_NEEDED
            needed_offsets.append(value)
        elif tag == 5:  # DT_STRTAB
            strtab_vaddr = value
        elif tag == 10:  # DT_STRSZ
            strsz = value
        elif tag == 15:  # DT_RPATH
            rpath_offsets.append(value)
        elif tag == 29:  # DT_RUNPATH
            runpath_offsets.append(value)
    if strtab_vaddr is None:
        return None  # 動的リンクなのに文字列表を引けない = fail-closed
    str_offset = _vaddr_to_offset(strtab_vaddr)
    if str_offset is None:
        return None
    str_size = strsz or (len(blob) - str_offset)

    def _string(value: int) -> Optional[str]:
        start = str_offset + value
        if not (str_offset <= start < str_offset + str_size):
            return None
        end = blob.find(b"\0", start)
        if end == -1:
            return None
        return blob[start:end].decode("utf-8", errors="replace")

    names = [text for text in map(_string, needed_offsets) if text]

    def _paths(offsets: "list[int]") -> "tuple":
        entries: "list[str]" = []
        for value in offsets:
            text = _string(value)
            if not text:
                continue
            entries.extend(part for part in text.split(":") if part)
        return tuple(entries)

    return names, _paths(rpath_offsets), _paths(runpath_offsets)

def _separation_audio_executables() -> "tuple[tuple, bool]":
    """Demucs がデコードに使う実行ファイル名と、それが必須かを返す。

    戻り値は `(名前, required)`。**CLI / API どちらの経路も FFmpeg を叩く**ので名前は
    共通で、`required` だけが経路で変わる（CLI は `_demucs_subprocess_env()` が実在を
    必須にする / API は不在ならフォールバックするので必須でない）。

    **判定のために demucs を import しない**（それでは pre-import bind の意味が消える）。
    `io.source_separator` が既に読み込まれていればその実測値を使い、まだなら
    `find_spec("demucs.api")` の有無で代替する。代替判定が外れた場合（spec はあるが
    import に失敗して CLI へ落ちる等）は、分離**後**の再 hash が実測値で計算されて
    digest が食い違うため、既存の before/after 比較が fail-closed で拾う。

    demucs 自体を解決できない環境では空を返す（分離が起きないので pin 対象もない）。
    """
    import importlib.util
    import sys

    tools = ("ffmpeg", "ffprobe")
    module = sys.modules.get("svp_rpe.io.source_separator")
    if module is not None:
        if not getattr(module, "_HAS_DEMUCS", False):
            return (), False
        return tools, getattr(module, "_DemucsAPI", None) is None
    try:
        spec = importlib.util.find_spec("demucs")
    except Exception:
        return (), False
    if spec is None:
        return (), False
    # **`find_spec("demucs.api")` は使わない**: サブモジュールの探索は親パッケージを
    # import して実行するため、pin を bind する前に demucs が cache されてしまう
    # （Codex #217）。API の有無はパッケージディレクトリ上の `api.py` の実在で判定する
    # （ファイルを読むだけで import を起こさない）。
    locations = list(getattr(spec, "submodule_search_locations", None) or ())
    if not locations:
        origin = getattr(spec, "origin", None)
        locations = [str(Path(origin).parent)] if origin else []
    has_api = any(
        (Path(location) / "api.py").is_file() or (Path(location) / "api").is_dir()
        for location in locations
    )
    return tools, not has_api


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
