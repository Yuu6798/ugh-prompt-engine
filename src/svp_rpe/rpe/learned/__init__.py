"""rpe/learned — optional learned-model adapters.

Every adapter in this package is gated behind an opt-in pyproject extra and
is responsible for emitting `LearnedAudioAnnotations` only — never for
mutating `PhysicalRPE` / `SemanticRPE` / `SVPForGeneration`. See
`docs/learned_models_policy.md` for the full isolation policy.
"""
from __future__ import annotations

from svp_rpe.rpe.models import LearnedAudioAnnotations, RPEBundle

__all__ = [
    "LearnedModelUnavailable",
    "LearnedModelIncompatible",
    "attach_learned_annotations",
]


class LearnedModelUnavailable(RuntimeError):
    """Raised when a learned-model optional dependency is not installed.

    Single source of truth for the entire `learned/` package. Adapters
    re-export this name from their own modules for caller convenience but
    they do NOT define their own copies — catching `LearnedModelUnavailable`
    must succeed regardless of which adapter raised it.
    """


class LearnedModelIncompatible(LearnedModelUnavailable):
    """Raised when an optional dependency IS installed but its API does not match.

    Subclass of `LearnedModelUnavailable` so callers that broadly catch the
    parent for fallback purposes still catch this — but callers can also
    catch this specifically to surface "upstream package shape changed,
    please file a bug" rather than silently falling back to a deterministic
    backend.

    Examples of when an adapter should raise this instead of the parent:
        - module imported successfully but a required attribute is missing
        - upstream output shape disagrees with the recorded contract
          (e.g. label-count mismatch)
    """


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
