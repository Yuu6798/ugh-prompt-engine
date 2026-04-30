# Semantic CI Fixtures

These fixtures make the deterministic semantic CI loop runnable without writing JSON by hand.

```bash
svprpe ci-check examples/semantic_ci/pass_perfect/target_svp.json examples/semantic_ci/pass_perfect/observed_rpe.json
svprpe ci-check examples/semantic_ci/repair_degraded/target_svp.json examples/semantic_ci/repair_degraded/observed_rpe.json
svprpe ci-check examples/semantic_ci/repair_budget_zero/target_svp.json examples/semantic_ci/repair_budget_zero/observed_rpe.json
svprpe ci-check examples/semantic_ci/repair_degraded/target_svp.json examples/semantic_ci/repair_degraded/observed_rpe.json --format markdown
svprpe ci-check examples/semantic_ci/repair_degraded/target_svp.json examples/semantic_ci/repair_degraded/observed_rpe.json --threshold 0.6
```

`ci-check` exits with code `1` for `repair` verdicts. Use `--threshold` when a CI
job should tolerate bounded semantic drift.

Each scenario contains:

- `target_svp.json`: the intended semantic generation/check spec
- `observed_rpe.json`: fixture output from a generated artifact or adapter
- `expected_output.json`: the committed `svprpe ci-check` output snapshot
- `expected_report.md`: the committed `svprpe ci-check --format markdown` report snapshot

Refresh snapshots after intentional semantic CI behavior changes:

```bash
python scripts/regenerate_ci_fixtures.py
python scripts/regenerate_ci_fixtures.py --check
```
