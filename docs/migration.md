# Migration Notes

## SemanticRPE 1.0 to 2.0

Q4 changes `SemanticRPE.por_surface` from `list[str]` to evidence-bearing
`SemanticLabel` objects:

- `label`
- `layer`
- `confidence`
- `evidence`
- `source_rule`

The migration policy is **fail-fast**. Old RPE JSON with
`semantic.schema_version: "1.0"` is not automatically migrated. The previous
string labels do not contain enough information to reconstruct confidence,
matched evidence, or source rule identifiers without inventing data.

To migrate an old artifact, regenerate RPE from the original audio with the
current local pipeline:

```bash
svprpe extract track.wav -o rpe.json
svprpe generate rpe.json --format yaml
```

SVP output remains compatible at the summary layer: `AnalysisRPE.por_surface`
continues to contain plain label strings copied from the structured labels.
Comparison and evaluation flows should consume regenerated RPE JSON whenever
they need semantic-layer evidence.
