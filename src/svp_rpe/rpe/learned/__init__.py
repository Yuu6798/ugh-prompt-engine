"""rpe/learned — optional learned-model adapters.

Every adapter in this package is gated behind an opt-in pyproject extra and
is responsible for emitting `LearnedAudioAnnotations` only — never for
mutating `PhysicalRPE` / `SemanticRPE` / `SVPForGeneration`. See
`docs/learned_models_policy.md` for the full isolation policy.
"""
from __future__ import annotations

from svp_rpe.rpe.models import LearnedAudioAnnotations, RPEBundle

__all__ = ["attach_learned_annotations"]


def attach_learned_annotations(
    bundle: RPEBundle,
    annotations: LearnedAudioAnnotations,
) -> RPEBundle:
    """Return a copy of `bundle` with `learned_annotations` set.

    Internal helper for backends to populate `RPEBundle.learned_annotations`
    cleanly. Does not mutate the input. Public CLI integration (e.g. a
    `--learned` flag on `extract_rpe_from_file`) is deferred to a later PR.
    """
    return bundle.model_copy(update={"learned_annotations": annotations})
