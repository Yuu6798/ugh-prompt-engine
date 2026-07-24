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
from typing import Optional, Tuple

from svp_rpe.rpe.learned import LearnedModelUnavailable
from svp_rpe.utils.hashing import sha256_of_files

__all__ = [
    "ExtractorWeights",
    "extractor_weights_fingerprint",
    "record_load_time_pin",
    "load_time_pin",
    "reset_load_time_pins",
]

KIND_MODEL_WEIGHTS = "model_weights"
KIND_LIBRARY_BINARY = "library_binary"

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
        if extractor == "crepe":
            from svp_rpe.rpe.learned.crepe_adapter import crepe_weight_files

            files = crepe_weight_files()
            return ExtractorWeights(
                extractor=extractor,
                kind=KIND_MODEL_WEIGHTS,
                sha256=sha256_of_files(files, use_cache=use_cache),
                files=tuple(p.name for p in files),
            )
        if extractor == "basic_pitch":
            from svp_rpe.rpe.learned.basic_pitch_adapter import basic_pitch_weight_files

            files, root = basic_pitch_weight_files()
            return ExtractorWeights(
                extractor=extractor,
                kind=KIND_MODEL_WEIGHTS,
                sha256=sha256_of_files(files, root=root, use_cache=use_cache),
                files=tuple(sorted(p.name for p in files)),
            )
        if extractor == "melodia":
            from svp_rpe.rpe.learned.melodia_adapter import melodia_implementation_files

            files, root = melodia_implementation_files()
            return ExtractorWeights(
                extractor=extractor,
                kind=KIND_LIBRARY_BINARY,
                sha256=sha256_of_files(files, root=root, use_cache=use_cache),
                files=tuple(sorted(p.name for p in files)),
            )
    except (LearnedModelUnavailable, OSError):
        return None
    # pyin / none: 重みを持たない DSP 経路（評価器も pin を要求しない）。
    return None


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
