# Semantic CI Product V1

## Positioning

Semantic CI is the deterministic verification layer around generative AI. It does not generate
music, images, video, or text by itself. It turns a target specification into an expected RPE,
compares that expectation with an observed RPE fixture, and emits a semantic diff plus a repair
SVP.

V1 focuses on the Phase 1 core:

```text
Target SVP
  -> Expected RPE
  -> Observed RPE fixture
  -> Compare
  -> Semantic Diff
  -> Repair SVP
  -> RoundTrip Log
```

Audio or artifact extraction (`WAV -> RPE`) remains outside this first core. The goal is to prove
the product loop is deterministic before connecting it to measurement adapters.

## Core Concepts

| Concept | V1 role |
|---|---|
| Target SVP | Generation intent plus post-generation checks |
| Expected RPE | Deterministic check spec derived from Target SVP |
| Observed RPE | Fixture or adapter output representing the generated artifact |
| Semantic Diff | `missing` / `preserved` / `over_changed` diagnostic split |
| Repair SVP | Budgeted `preserve` / `restore` / `reduce` / `lock` repair plan |
| RoundTrip Log | Stable transition hashes for reproducibility |

Expected RPE is the center of the loop. It makes SVP more than a prompt: it also states what must
be checked after generation.

`TargetSVP.core` is automatically added to `preserve` during validation. This makes the main
semantic intent a protected signal even when fixture authors omit it from the explicit preserve
list.

## Implementation

Code lives in `src/svp_rpe/semantic_ci/`.

Primary API:

```python
from svp_rpe.semantic_ci import ObservedRPE, TargetSVP, run_semantic_ci

target = TargetSVP(
    id="target-001",
    domain="music",
    core="energetic driving dense",
    surface=["bright", "wide stereo"],
    grv=["bass-heavy", "148 bpm"],
    delta_e_profile="gradual build",
    preserve=["chorus lift"],
    avoid=["dark ambient"],
    lock=["148 bpm"],
    metric_targets={"bpm": 148.0},
    tolerances={"bpm": 0.0},
    change_budget=2,
)

observed = ObservedRPE(
    id="fixture-001",
    domain="music",
    signals=["148 bpm", "bass-heavy", "unexpected pad"],
    metrics={"bpm": 140.0},
)

result = run_semantic_ci(target, observed)
```

CLI:

```bash
svprpe ci-check target_svp.json observed_rpe.json
svprpe ci-check target_svp.json observed_rpe.json -o semantic_ci_result.json
svprpe ci-check examples/semantic_ci/pass_perfect/target_svp.json \
  examples/semantic_ci/pass_perfect/observed_rpe.json
```

Committed fixtures live under `examples/semantic_ci/`. Each scenario has
`target_svp.json`, `observed_rpe.json`, and `expected_output.json` so reviewers can
compare CLI output against a golden snapshot.

## Minimal Win Conditions

V1 is considered working when these conditions hold:

1. Target SVP generates Expected RPE deterministically.
2. Matching Expected RPE and Observed RPE produce `loss = 0`.
3. Degraded Observed RPE increases `loss`.
4. Semantic Diff separates `missing`, `preserved`, and `over_changed`.
5. Repair SVP separates `preserve`, `restore`, `reduce`, and `lock`.
6. `change_budget` limits applied restore/reduce edits.
7. RoundTrip Log records state transition hashes.
8. Same input produces the same output and same hash.

The test suite locks these in `tests/test_semantic_ci.py`.

V1 computes loss as:

```text
loss = min(1.0, 0.6 * missing_loss + 0.2 * over_changed_loss + 0.2 * metric_loss)
```

The weights are fixed in V1 so snapshot drift is easy to detect. Later versions can externalize
them once there is validation data.

## Repair Mapping

V1 applies the repair plan back to `TargetSVP` with a deliberately small mapping:

| Repair field | Repaired TargetSVP field |
|---|---|
| `preserve` | `preserve` |
| `restore` | `surface` |
| `reduce` | `avoid` |
| `lock` | `lock` |

Preserved observed signals are also copied into `lock` in V1. This keeps a successful fixture from
being accidentally rewritten in the next loop. Later versions can make that behavior configurable
when repair plans become generator-specific.

## Current Boundary

- RPE extraction from real artifacts is not part of this V1 core.
- Signal matching is exact canonical string matching.
- Metric comparison supports numeric exact/tolerance checks and categorical equality.
- Repair SVP is a deterministic patch plan, not a generator-specific prompt adapter.

These constraints are intentional: V1 proves the semantic CI contract before adding domain-specific
measurement adapters.
