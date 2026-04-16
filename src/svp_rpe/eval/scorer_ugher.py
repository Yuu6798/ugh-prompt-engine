"""eval/scorer_ugher.py — UGHer semantic consistency scoring.

Evaluates how well SVP preserves the semantic intent captured by RPE.
MVP uses token-overlap heuristics; future versions may use embeddings.
"""
from __future__ import annotations

from svp_rpe.eval.models import UGHerScore
from svp_rpe.rpe.models import RPEBundle
from svp_rpe.svp.models import SVPBundle


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _token_overlap(text_a: str, text_b: str) -> float:
    """Simple token overlap ratio. MVP metric, not embedding-based."""
    tokens_a = set(text_a.lower().split())
    tokens_b = set(text_b.lower().split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    return len(intersection) / max(len(tokens_a), len(tokens_b))


def score_ugher(rpe: RPEBundle, svp: SVPBundle) -> UGHerScore:
    """Score SVP against RPE for semantic consistency.

    - por_similarity: how well SVP preserves por_core
    - grv_consistency: BPM/key/duration/anchor match
    - delta_e_assessment: energy transition preserved
    - physical_accuracy: RPE → SVP reflection rate
    """
    sem = rpe.semantic
    phys = rpe.physical
    gen = svp.svp_for_generation
    analysis = svp.analysis_rpe

    # por_similarity: token overlap between por_core and SVP prompt
    por_similarity = _token_overlap(sem.por_core, gen.prompt_text)

    # grv_consistency: check if anchor, BPM, key are reflected
    checks = 0
    total = 0

    # Anchor in prompt
    total += 1
    if sem.grv_anchor.primary.lower() in gen.prompt_text.lower():
        checks += 1

    # BPM preserved
    if phys.bpm:
        total += 1
        if analysis.bpm and abs(analysis.bpm - phys.bpm) < 5:
            checks += 1

    # Key preserved
    if phys.key:
        total += 1
        if analysis.key == phys.key:
            checks += 1

    grv_consistency = checks / max(total, 1)

    # delta_e_assessment: transition type preserved
    delta_e_assessment = 1.0 if (
        sem.delta_e_profile.transition_type in svp.evaluation_criteria.delta_e_check
    ) else 0.5

    # physical_accuracy: how many physical checks are generated
    physical_accuracy = _clamp(len(svp.evaluation_criteria.physical_checks) / 4.0)

    overall = round(
        (por_similarity * 0.3 + grv_consistency * 0.3
         + delta_e_assessment * 0.2 + physical_accuracy * 0.2),
        4,
    )

    return UGHerScore(
        por_similarity=round(por_similarity, 4),
        grv_consistency=round(grv_consistency, 4),
        delta_e_assessment=round(delta_e_assessment, 4),
        physical_accuracy=round(physical_accuracy, 4),
        overall=overall,
    )
