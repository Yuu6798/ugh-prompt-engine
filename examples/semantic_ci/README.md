# Semantic CI Fixtures

These fixtures make the deterministic semantic CI loop runnable without writing JSON by hand.

```bash
svprpe ci-check examples/semantic_ci/pass_perfect/target_svp.json examples/semantic_ci/pass_perfect/observed_rpe.json
svprpe ci-check examples/semantic_ci/repair_degraded/target_svp.json examples/semantic_ci/repair_degraded/observed_rpe.json
svprpe ci-check examples/semantic_ci/repair_budget_zero/target_svp.json examples/semantic_ci/repair_budget_zero/observed_rpe.json
```

Each scenario contains:

- `target_svp.json`: the intended semantic generation/check spec
- `observed_rpe.json`: fixture output from a generated artifact or adapter
- `expected_output.json`: the committed `svprpe ci-check` output snapshot

Refresh snapshots after intentional semantic CI behavior changes:

```bash
python scripts/regenerate_ci_fixtures.py
python scripts/regenerate_ci_fixtures.py --check
```
