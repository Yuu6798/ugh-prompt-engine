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
    "extractor_code_fingerprint",
    "extractor_code_packages_for",
    "package_code_sha256",
    "packages_code_sha256",
    "package_code_state",
    "SEPARATION_CODE_PACKAGES",
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
# 動かない（Codex #217）。numpy/scipy は汎用数値基盤として線の外に置く。
_EXTRACTOR_CODE_PACKAGES = {
    # CREPE は 16kHz 以外の入力を **内部で `resampy`** によりリサンプルしてから
    # モデルへ渡す。patch された resampy はモデルに届くサンプルを変えるのに、
    # crepe/TF/weights/version のどの pin も動かない（Codex #217）。
    "crepe": ("crepe", "tensorflow", "keras", "librosa", "resampy"),
    "basic_pitch": (
        "basic_pitch",
        "tensorflow",
        "keras",
        "onnxruntime",
        "coremltools",
        "tflite_runtime",
        "librosa",
    ),
    "melodia": ("essentia", "librosa"),
    # pyin は **scipy を直接実行する**: `extract_pyin_observation` が
    # `_highpass_melody_signal`（`scipy.signal.butter` / `sosfiltfilt`）で波形を
    # 前処理してから `librosa.pyin` へ渡す（physical_features.py:1170-1182）。
    # patch された scipy はフィルタ後の波形＝F0 トラック＝ゲート判定を変えるのに、
    # librosa の pin も version も動かない（Codex #217）。汎用数値基盤を線の外に
    # 置く原則の例外は「本層のコードが直接呼ぶ」ことを根拠とする。
    "pyin": ("librosa", "scipy"),
}

# 分離器（Demucs）の推論を実行するパッケージ群。
SEPARATION_CODE_PACKAGES = ("demucs", "torch")

# コードとみなす拡張子（Python source + ネイティブ拡張）。モデル artifact（.h5 等）は
# `extractor_weights_sha256` 側で別途 pin するので、二重計上せず責務を分ける。
_CODE_SUFFIXES = (".py", ".so", ".pyd", ".dylib")


# パッケージのコード hash 解決結果。`absent`（未導入 = optional backend が無いだけ）と
# `unhashable`（**導入済みで推論に使われうるのに hash できない**）を区別する（Codex #217）。
# 後者を skip して他のパッケージだけで digest を作ると、実行された実装の一部を覆わない
# pin を「揃っている」として publish してしまう。
STATE_OK = "ok"
STATE_ABSENT = "absent"
STATE_UNHASHABLE = "unhashable"


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
    root = Path(origin).resolve().parent
    try:
        files = sorted(
            path
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix in _CODE_SUFFIXES
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
    targets = {"separation": SEPARATION_CODE_PACKAGES}
    targets.update(_EXTRACTOR_CODE_PACKAGES)
    for name, packages in targets.items():
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
