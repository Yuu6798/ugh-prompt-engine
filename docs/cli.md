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
```

### `svprpe generate <rpe.json>`

Generate SVP from RPE JSON.

```bash
svprpe generate rpe.json --output-dir ./output --format yaml
svprpe generate rpe.json --format text
```

### `svprpe evaluate --audio <audio> [--svp <svp.yaml>]`

Evaluate audio quality and SVP consistency.

```bash
svprpe evaluate --audio track.wav -o evaluation.json
svprpe evaluate --audio track.wav --svp svp.yaml -o evaluation.json
```

### `svprpe run <audio>`

Run full pipeline: extract → generate → evaluate.

```bash
# Save all outputs to directory
svprpe run track.wav --output-dir ./output

# Print to stdout
svprpe run track.wav --no-save
```

## Options

| Option | Description |
|--------|-------------|
| `--output` / `-o` | Output file path |
| `--output-dir` | Output directory (creates if needed) |
| `--format` | Output format: `yaml` (default) or `text` |
| `--no-save` | Print output to stdout instead of saving |
| `--help` | Show help |
