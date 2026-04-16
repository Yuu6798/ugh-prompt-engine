"""eval/semantic_similarity.py — Token + synonym overlap for PoR similarity.

Design intent alignment, not truth judgment. LLM-free, embedding-free.
"""
from __future__ import annotations

from typing import Dict, Set

from svp_rpe.utils.config_loader import load_config


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def _load_synonym_map() -> Dict[str, Set[str]]:
    """Load synonym map from config. Returns {term: {synonyms}}."""
    try:
        cfg = load_config("synonym_map")
        result: Dict[str, Set[str]] = {}
        for group in cfg.get("groups", []):
            terms = set(t.lower() for t in group)
            for t in terms:
                result[t] = terms
        return result
    except FileNotFoundError:
        return {}


def _tokenize(text: str) -> Set[str]:
    """Lowercase tokenization with basic normalization."""
    tokens = set()
    for word in text.lower().replace(",", " ").replace("×", " ").replace("/", " ").split():
        word = word.strip("-_#()[]")
        if word and len(word) > 1:
            tokens.add(word)
    return tokens


def _expand_synonyms(tokens: Set[str], syn_map: Dict[str, Set[str]]) -> Set[str]:
    """Expand token set with synonyms."""
    expanded = set(tokens)
    for t in tokens:
        if t in syn_map:
            expanded |= syn_map[t]
    return expanded


def por_lexical_similarity(text_a: str, text_b: str) -> float:
    """PoR lexical similarity with synonym expansion.

    Measures design intent alignment between two PoR descriptions.
    Not a truth judgment — measures whether the same semantic concepts appear.
    """
    syn_map = _load_synonym_map()

    tokens_a = _tokenize(text_a)
    tokens_b = _tokenize(text_b)

    if not tokens_a or not tokens_b:
        return 0.0

    expanded_a = _expand_synonyms(tokens_a, syn_map)
    expanded_b = _expand_synonyms(tokens_b, syn_map)

    intersection = expanded_a & expanded_b
    union_size = max(len(expanded_a), len(expanded_b))

    return _clamp(len(intersection) / union_size) if union_size > 0 else 0.0
