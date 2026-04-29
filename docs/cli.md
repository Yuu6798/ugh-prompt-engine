# CLI Reference

## Installation

```bash
pip install -e ".[dev]"
```

## Commands

### `svprpe extract <audio>`

Extract RPE (physical + semantic) from an audio file.

```bash
svprpe extract track.wav -o rpe.json
svprpe extract track.wav --valley-method rms_percentile -o rpe.json
```

### `svprpe generate <rpe.json>`

Generate SVP from RPE JSON.

```bash
svprpe generate rpe.json --output-dir ./output --format yaml
svprpe generate rpe.json --format text
```

### `svprpe evaluate --audio <audio> [--svp <svp.yaml>]`

Evaluate audio. Without `--svp`: self-evaluate. With `--svp`: compare against external SVP.

```bash
# Self-evaluation
svprpe evaluate --audio track.wav -o evaluation.json
svprpe evaluate --audio track.wav --baseline edm -o evaluation.json

# Compare against external SVP
svprpe evaluate --audio track.wav --svp design.yaml -o evaluation.json
```

Output includes `action_hints` when `--svp` is provided.

### `svprpe compare`

Compare reference audio against candidate audio/SVP.

```bash
# Reference audio vs candidate SVP
svprpe compare --reference-audio ref.wav --candidate-svp candidate.yaml

# Reference audio vs candidate audio
svprpe compare --reference-audio ref.wav --candidate-audio gen.wav

# With reference SVP
svprpe compare --reference-audio ref.wav --candidate-audio gen.wav --reference-svp ref.yaml
```

Output: `semantic_diff`, `physical_diff`, `action_hints`, `overall_score`.

### `svprpe run <audio>`

Run full pipeline: extract → generate → evaluate.

```bash
svprpe run track.wav --output-dir ./output
svprpe run track.wav --no-save
svprpe run track.wav --valley-method section_ar --output-dir ./output
svprpe run track.wav --baseline acoustic --output-dir ./output
```

### `svprpe batch <dir>`

Batch process multiple audio files.

```bash
# Evaluate all audio files in directory
svprpe batch ./audio_files --output-dir ./batch_out
svprpe batch ./audio_files --baseline loud_pop --output-dir ./batch_out

# Compare each audio against SVP candidates
svprpe batch ./audio_files --svp-dir ./svp_candidates --mode compare --output-dir ./batch_out
```

Outputs: `ranking.json`, `summary.csv`, `summary.json`, `next_action.md`.

## Global Options

| Option | Description |
|--------|-------------|
| `--output` / `-o` | Output file path |
| `--output-dir` | Output directory (creates if needed) |
| `--format` | Output format: `yaml` (default) or `text` |
| `--no-save` | Print output to stdout instead of saving |
| `--valley-method` | Valley depth method: `hybrid` (default), `rms_percentile`, `section_ar` |
| `--baseline` | RPE baseline profile: `pro`, `loud_pop`, `acoustic`, or `edm` |
| `--svp` | External SVP file for comparison |
| `--svp-dir` | Directory with SVP candidates (batch mode) |
| `--mode` | Batch mode: `evaluate` (default) or `compare` |
| `--help` | Show help |
