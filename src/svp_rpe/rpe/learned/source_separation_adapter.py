"""rpe/learned/source_separation_adapter.py — melody 経路向け Demucs vocals 分離。

Optional via the `separate` extra. 既存の `io.source_separator.separate_stems`
（本リポジトリ唯一の Demucs エントリポイント）を再利用し、melody routing 層が
必要とする **vocals stem だけ**を返す薄いラッパである。`SeparatorNotAvailableError`
を `LearnedModelUnavailable` へ写像することで、CREPE / Melodia など他の optional
抽出器と**同一の失敗形**（`LearnedModelUnavailable` で優雅に落ちる）へ揃える
（`docs/learned_models_policy.md` の slow-lane 隔離、`docs/melody_observability.md`
M1a）。

このモジュールは決定論 RPE フィールドを一切書かない。分離波形（numpy 配列）を
呼び出し側（`melody.extractors`）へ返すだけで、`LearnedAudioAnnotations` すら
生成しない — vocals stem は観測ゲートへの中間入力であって注釈ではない。
"""
from __future__ import annotations

from pathlib import Path
from typing import Tuple, Union

import numpy as np

from svp_rpe.io.source_separator import (
    DEFAULT_MODEL,
    SeparatorNotAvailableError,
    separate_stems,
)
from svp_rpe.rpe.learned import LearnedModelUnavailable

__all__ = [
    "LearnedModelUnavailable",
    "ensure_separation_available",
    "isolate_vocals",
]

_INSTALL_HINT = (
    "demucs is not installed. Install it via the optional `separate` extra:\n"
    '    pip install -e ".[separate]"'
)


def ensure_separation_available() -> None:
    """Demucs が利用可能かを事前に確認し、無ければ `LearnedModelUnavailable`。

    実分離は走らせない（重み download もしない）。他の optional 抽出器の
    ``ensure_*_available`` と同じく、依存欠落を早期に fail させるための probe。
    """
    from svp_rpe.io import source_separator

    if not getattr(source_separator, "_HAS_DEMUCS", False):
        raise LearnedModelUnavailable(_INSTALL_HINT)


def isolate_vocals(
    audio_path: Union[str, Path],
    *,
    model: str = DEFAULT_MODEL,
    device: str = "cpu",
) -> Tuple[np.ndarray, int]:
    """`audio_path` から Demucs で vocals stem を分離し (waveform, sample_rate)。

    `separate_stems` に委譲し、その `"vocals"` stem（mono float32）と sample_rate を
    返す。Demucs 未導入時に `separate_stems` が送出する
    `SeparatorNotAvailableError` を `LearnedModelUnavailable` へ写像して、
    melody routing 層が他の抽出器と同じ except 節で拾えるようにする。

    Raises
    ------
    LearnedModelUnavailable
        demucs が未導入のとき。
    """
    try:
        bundle = separate_stems(audio_path, model=model, device=device)
    except SeparatorNotAvailableError as exc:
        raise LearnedModelUnavailable(_INSTALL_HINT) from exc
    return np.asarray(bundle.stems["vocals"], dtype=np.float32), int(bundle.sample_rate)
