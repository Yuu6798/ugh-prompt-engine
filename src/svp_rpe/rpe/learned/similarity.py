"""rpe/learned/similarity.py — CLAP embedding similarity instrument.

Pure numpy functions over already-computed embedding vectors — NO
`torch` import and NO dependency on `laion_clap` being installed. These
functions are usable directly against a fixture's numeric arrays (e.g.
from `scripts/collect_clap_fixture.py`) without the `semantic-embed`
extra.

Cosine similarity here is a learned-model-derived "fit" signal — a
*learned grip*, in the same sense as `svp_rpe.control.grip_effect_size`:
it is read as an A/B contrast (`contrast_fit`), never as an absolute
verdict of prompt/audio correctness or music quality. See
`docs/learned_models_policy.md` for the isolation policy this instrument
operates under.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

__all__ = ["cosine_similarity", "prompt_audio_fit", "contrast_fit"]

_ZERO_NORM_EPS = 1e-12


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """コサイン類似度。

    どちらかのベクトルのノルムが 0 に近い場合は 0.0 を返す（0-safe）。理論上の
    範囲は [-1, 1] だが浮動小数の丸め誤差でわずかに超えることがあるため clamp する。
    """
    va = np.asarray(a, dtype=np.float64)
    vb = np.asarray(b, dtype=np.float64)
    norm_a = float(np.linalg.norm(va))
    norm_b = float(np.linalg.norm(vb))
    if norm_a < _ZERO_NORM_EPS or norm_b < _ZERO_NORM_EPS:
        return 0.0
    cosine = float(np.dot(va, vb) / (norm_a * norm_b))
    return max(-1.0, min(1.0, cosine))


def prompt_audio_fit(
    audio_embedding: Sequence[float],
    text_embeddings: Sequence[Sequence[float]],
) -> list[float]:
    """`audio_embedding` と各 `text_embeddings` 要素とのコサイン適合度のリスト。

    prompt<->audio の学習版 grip の基礎統計量。単体では verdict を出さない —
    `contrast_fit` の A/B 比較で読むための素材。
    """
    return [
        cosine_similarity(audio_embedding, text_embedding)
        for text_embedding in text_embeddings
    ]


def contrast_fit(
    audio_embedding: Sequence[float],
    positive_texts_emb: Sequence[Sequence[float]],
    negative_texts_emb: Sequence[Sequence[float]],
) -> float:
    """positive / negative プロンプト集合とのコサイン適合度の平均差。

    絶対値でなく A/B コントラストで読む —
    `svp_rpe.control.grip_effect_size` と同じ哲学で、verdict を出さない計器。

    Raises
    ------
    ValueError
        `positive_texts_emb` または `negative_texts_emb` が空の場合。
    """
    if len(positive_texts_emb) == 0:
        raise ValueError("positive_texts_emb must not be empty")
    if len(negative_texts_emb) == 0:
        raise ValueError("negative_texts_emb must not be empty")
    positive_scores = prompt_audio_fit(audio_embedding, positive_texts_emb)
    negative_scores = prompt_audio_fit(audio_embedding, negative_texts_emb)
    return float(np.mean(positive_scores) - np.mean(negative_scores))
