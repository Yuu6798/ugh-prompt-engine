"""scripts/calibrate_semantic_axes.py — semantic-axis battery calibration harness.

Calibrates `config/semantic_probe_axes.yaml`'s CLAP semantic-axis battery
against STORED REAL audio embeddings — embedding-space only, no audio
files and no fresh audio inference. The two fixtures used are the
real-inference collections already committed under `examples/learned/clap/`:

- `lyrics_vocal_contrast_fixture(.embeddings).json` — 6 real Suno samples
  (edm/rock x lyrics/lyrics_alt/inst), from the lyrics-vocal-anchor probe
  (see `docs/lyrics_semantic_anchor.md`).
- `musicgen_k2_contrast_fixture(.embeddings).json` — 32 real MusicGen
  samples (bpm low/high x8, brightness low/high x8), from K2
  (see `docs/musicgen_backend.md` / `docs/controllability_poc.md`).

This script only re-embeds axis PROBE TEXTS (`embed_texts`) and reuses the
already-collected audio embeddings from each fixture's `.embeddings.json`
sidecar — it never re-embeds audio, so it needs no audio bytes and stays
cheap to re-run. `laion_clap` is only imported inside `load_clap_model`
(via `clap_adapter`), never at this module's import time, so importing
this script (e.g. from tests) never requires the `semantic-embed` extra.

Text probes and stored audio embeddings must share the same embedding
space: before loading the CLAP model, `build_calibration` fails fast
(`ValueError`) unless `--checkpoint`/`--amodel` match every loaded
fixture's pinned `model.checkpoint_sha256` / `model.amodel` exactly — a
rerun with a different or omitted checkpoint refuses to silently write
mixed-embedding-space calibration values.

The checkpoint pin alone does not guarantee a trustworthy run: `build_calibration`
also fails fast if `probe_determinism_same_run` is `False` (nondeterministic text
tower) or if any fixture's `reproduction_check.max_abs_deviation` exceeds
`_REPRODUCTION_TOLERANCE` (drifted CLAP/tokenizer behavior) — see those checks
below for the tolerance rationale.

What it measures:

1. `probe_determinism_same_run` — embedding every axis's probe texts
   twice in the same process and comparing the raw vectors, a minimal
   sanity check that the text tower is deterministic within a run.
2. `readings` — every axis's `contrast_fit` against every fixture sample's
   stored audio embedding (the calibration substrate for the stats below).
3. `reproduction_check` — re-embeds each sample's OWN `prompt_groups`
   probes (the axis the fixture was originally collected against) and
   compares the recomputed `contrast_fit` to the fixture's stored value,
   evidence that a fresh run reproduces the original collection.
4. `stats` — calibration reads used to annotate
   `config/semantic_probe_axes.yaml`'s per-axis `notes`:
   - `lyrics_effect_vs_regen_noise`: does the lyrics-vs-instrumental
     effect exceed the regen-to-regen (lyrics vs lyrics_alt) noise floor?
   - `musicgen_knob_cells`: Cohen's d of each axis against the MusicGen
     bpm / brightness knobs (grip cross-talk check).
   - `genre_effect_vs_regen_noise`: per-axis EDM-vs-Rock matched-pair
     contrast (same underlying song, `lyrics_vocal_contrast` fixture) vs.
     the lyrics/lyrics_alt regeneration-noise floor — a genre-axis
     ground-truth check generalized from the same discipline as
     `lyrics_effect_vs_regen_noise`.
   - `axis_correlation_matrix`: Pearson r for every axis pair, pooled over
     the full 38-sample readings (generalizes the old
     `warmth_brightness_pearson_r` redundancy check to any axis pair).
   - `sign_census`: per-axis negative/total sign counts across the full
     38-sample pool (generalizes the old `acousticness_sign_census` to
     every axis).

This script also accepts `--axes-config PATH` to calibrate an alternate
axis battery (e.g. a variant-sweep YAML under a scratchpad) instead of the
canonical `config/semantic_probe_axes.yaml` — used to A/B candidate probe
wordings without touching the canonical config. The alternate battery is
validated with the exact same fail-fast rules
(`svp_rpe.rpe.learned.semantic_axes.validate_axes_battery`) as the
canonical loader.

Usage:
    python scripts/calibrate_semantic_axes.py \\
        --checkpoint music_audioset_epoch_15_esc_90.14.pt --amodel HTSAT-base \\
        --output examples/learned/clap/semantic_axes_calibration_2026-07-04.yaml \\
        --run-date 2026-07-04

    # variant sweep against an alternate battery, canonical config untouched:
    python scripts/calibrate_semantic_axes.py \\
        --checkpoint music_audioset_epoch_15_esc_90.14.pt --amodel HTSAT-base \\
        --axes-config /path/to/sweep_battery.yaml \\
        --output /path/to/sweep_calibration.yaml
"""
from __future__ import annotations

import argparse
import hashlib
import statistics
import sys
from importlib import metadata as _pkg_metadata
from math import sqrt
from pathlib import Path
from typing import Any, Optional

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.collect_clap_fixture import load_fixture  # noqa: E402
from svp_rpe.rpe.learned import LearnedModelUnavailable  # noqa: E402
from svp_rpe.rpe.learned.clap_adapter import embed_texts, load_clap_model  # noqa: E402
from svp_rpe.rpe.learned.semantic_axes import validate_axes_battery  # noqa: E402
from svp_rpe.rpe.learned.similarity import contrast_fit  # noqa: E402
from svp_rpe.utils.config_loader import load_config  # noqa: E402

SCHEMA_VERSION = "1.1"
KIND = "semantic_axes_calibration"

FIXTURE_DIR = ROOT / "examples" / "learned" / "clap"
FIXTURE_NAMES = ["lyrics_vocal_contrast", "musicgen_k2_contrast"]

_ROUND_DIGITS = 6

# Measured cross-machine max deviation is 1e-6 (1 ulp of the 6-decimal
# rounding; see examples/learned/clap/semantic_axes_calibration_2026-07-04.yaml
# reproduction_check). 1e-5 gives a 10x margin above that noise floor while
# still catching library/tokenizer drift (typically >=1e-3). A legitimate
# re-calibration after a deliberate CLAP upgrade must re-collect the fixtures
# under new provenance instead of overriding this gate.
_REPRODUCTION_TOLERANCE = 1e-5

# MusicGen K2 sample_id prefixes: `<knob>_<level>_NN`, n=8 per cell.
_MUSICGEN_KNOBS = ("bpm", "brightness")
_MUSICGEN_LEVELS = ("low", "high")
_MUSICGEN_CELL_SIZE = 8

_LYRICS_GENRES = ("edm", "rock")

# `genre_effect_vs_regen_noise` matched-pair variants: the lyrics_vocal_contrast
# fixture's edm_* / rock_* samples are the SAME underlying song (StartinA) in
# 3 variants, so each variant gives one edm-vs-rock genre-contrast reading.
_GENRE_VARIANTS = ("lyrics", "lyrics_alt", "inst")


def _round(value: float) -> float:
    return round(float(value), _ROUND_DIGITS)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checkpoint_sha256(checkpoint: Optional[str]) -> Optional[str]:
    if checkpoint is None:
        return None
    checkpoint_path = Path(checkpoint)
    if not checkpoint_path.is_file():
        return None
    return _sha256_file(checkpoint_path)


def _package_version(name: str) -> Optional[str]:
    try:
        return _pkg_metadata.version(name)
    except _pkg_metadata.PackageNotFoundError:
        return None


def _validate_embedding_space_pin(
    fixtures: dict[str, dict[str, Any]], *, checkpoint: Optional[str], amodel: Optional[str]
) -> str:
    """Fail fast unless `checkpoint`/`amodel` match every fixture's model pin.

    Text probes and stored audio embeddings must live in the same embedding
    space, so calibration refuses to run cross-model. Returns the computed
    checkpoint sha256 (already validated) so callers can reuse it without
    hashing the checkpoint file a second time.

    Raises
    ------
    ValueError
        If `checkpoint` is missing/not a local file, or its sha256 or
        `amodel` does not match a fixture's pinned `model` block.
    """
    computed_sha256 = _checkpoint_sha256(checkpoint)
    for name, fixture in fixtures.items():
        expected_sha256 = fixture["model"]["checkpoint_sha256"]
        expected_amodel = fixture["model"]["amodel"]
        if computed_sha256 != expected_sha256 or amodel != expected_amodel:
            raise ValueError(
                f"embedding-space mismatch for fixture {name!r}: text probes must be "
                f"embedded in the same embedding space as the stored audio embeddings, "
                f"so calibration refuses to run cross-model. Fixture is pinned to "
                f"checkpoint_sha256={expected_sha256!r} amodel={expected_amodel!r}; "
                f"got --checkpoint={checkpoint!r} (sha256={computed_sha256!r}) "
                f"--amodel={amodel!r}. Pass the matching --checkpoint/--amodel."
            )
    return computed_sha256


def load_axes_config(axes_config_path: Optional[Path]) -> dict[str, Any]:
    """Load the axis-battery config dict: `axes_config_path` if given, else
    the canonical `config/semantic_probe_axes.yaml` (via `load_config`,
    which prefers the local `config/` copy over the packaged one).

    Only reads the raw dict (`schema_version` / `axes`) — callers must
    still validate `axes` themselves via `validate_axes_battery` before
    trusting it (this function does not validate).
    """
    if axes_config_path is None:
        return load_config("semantic_probe_axes")
    with axes_config_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"axes config must be a mapping: {axes_config_path}")
    return data


def load_fixtures() -> dict[str, dict[str, Any]]:
    """Load both committed fixtures, merged with their embedding sidecars.

    `load_fixture` (from `collect_clap_fixture.py`) re-attaches each
    sample's `audio_embedding` from the `*.embeddings.json` sidecar — no
    audio decoding, purely reading the two already-committed JSON files.
    """
    fixtures: dict[str, dict[str, Any]] = {}
    for name in FIXTURE_NAMES:
        main_path = FIXTURE_DIR / f"{name}_fixture.json"
        fixtures[name] = load_fixture(main_path)
    return fixtures


def embed_axis_battery(
    axes: list[dict], model: Any
) -> tuple[dict[str, tuple[list[list[float]], list[list[float]]]], bool]:
    """Embed every axis's positive/negative probes, twice, for a determinism check.

    Returns `(axis_embeddings, probe_determinism_same_run)`:
    `axis_embeddings` maps axis name -> `(positive_embeddings, negative_embeddings)`
    from the FIRST embedding pass (reused for every `contrast_fit` computation
    below); `probe_determinism_same_run` is `True` only if every axis's second
    embedding pass reproduced the first pass's vectors exactly.
    """
    axis_embeddings: dict[str, tuple[list[list[float]], list[list[float]]]] = {}
    determinism = True
    for axis in axes:
        positive_probes = list(axis["positive"])
        negative_probes = list(axis["negative"])
        pos_emb_1 = embed_texts(positive_probes, model=model)
        neg_emb_1 = embed_texts(negative_probes, model=model)
        pos_emb_2 = embed_texts(positive_probes, model=model)
        neg_emb_2 = embed_texts(negative_probes, model=model)
        if pos_emb_1 != pos_emb_2 or neg_emb_1 != neg_emb_2:
            determinism = False
        axis_embeddings[axis["name"]] = (pos_emb_1, neg_emb_1)
    return axis_embeddings, determinism


def compute_readings(
    fixtures: dict[str, dict[str, Any]],
    axis_embeddings: dict[str, tuple[list[list[float]], list[list[float]]]],
) -> dict[str, dict[str, dict[str, float]]]:
    """Every axis's `contrast_fit` against every fixture sample's stored audio embedding."""
    readings: dict[str, dict[str, dict[str, float]]] = {}
    for fixture_name, fixture in fixtures.items():
        fixture_readings: dict[str, dict[str, float]] = {}
        for sample in fixture["samples"]:
            audio_vec = sample["audio_embedding"]
            row: dict[str, float] = {}
            for axis_name, (pos_emb, neg_emb) in axis_embeddings.items():
                row[axis_name] = _round(contrast_fit(audio_vec, pos_emb, neg_emb))
            fixture_readings[sample["sample_id"]] = row
        readings[fixture_name] = fixture_readings
    return readings


def compute_reproduction_check(
    fixtures: dict[str, dict[str, Any]], model: Any
) -> dict[str, dict[str, Any]]:
    """Re-embed each sample's OWN `prompt_groups` probes and diff against the
    fixture's stored `contrast_fit` — cross-machine determinism evidence.

    Identical `prompt_groups` (shared across many samples in a fixture) are
    embedded only once via `probe_cache`.
    """
    probe_cache: dict[tuple[tuple[str, ...], tuple[str, ...]], tuple[list, list]] = {}
    result: dict[str, dict[str, Any]] = {}
    for fixture_name, fixture in fixtures.items():
        deviations: list[float] = []
        n_exact = 0
        samples = fixture["samples"]
        for sample in samples:
            positive = list(sample["prompt_groups"]["positive"])
            negative = list(sample["prompt_groups"]["negative"])
            key = (tuple(positive), tuple(negative))
            if key not in probe_cache:
                probe_cache[key] = (
                    embed_texts(positive, model=model),
                    embed_texts(negative, model=model),
                )
            pos_emb, neg_emb = probe_cache[key]
            computed = _round(contrast_fit(sample["audio_embedding"], pos_emb, neg_emb))
            stored = sample["contrast_fit"]
            deviations.append(abs(computed - stored))
            if computed == stored:
                n_exact += 1
        result[fixture_name] = {
            "n_samples": len(samples),
            "n_exact_6dp": n_exact,
            "max_abs_deviation": _round(max(deviations)) if deviations else 0.0,
        }
    return result


def compute_lyrics_effect_vs_regen_noise(
    readings: dict[str, dict[str, dict[str, float]]], axis_names: list[str]
) -> dict[str, dict[str, Any]]:
    """Per axis x genre: is the lyrics-vs-instrumental effect bigger than
    the lyrics-vs-lyrics_alt regeneration noise floor?

    `effect = mean(reading[f"{g}_lyrics"], reading[f"{g}_lyrics_alt"]) - reading[f"{g}_inst"]`,
    `regen_noise = |reading[f"{g}_lyrics"] - reading[f"{g}_lyrics_alt"]|`,
    `ratio = |effect| / regen_noise` (null when `regen_noise` is ~0, to avoid a
    division by ~zero). `exceeds_noise = |effect| > regen_noise` is computed
    directly (not via `ratio`) so the gate still applies when `ratio` is null —
    a zero (or near-zero) noise floor with a nonzero effect is the strongest
    possible pass, not a failure.
    """
    lyrics_readings = readings["lyrics_vocal_contrast"]
    stats: dict[str, dict[str, Any]] = {}
    for axis_name in axis_names:
        stats[axis_name] = {}
        for genre in _LYRICS_GENRES:
            lyrics_v = lyrics_readings[f"{genre}_lyrics"][axis_name]
            alt_v = lyrics_readings[f"{genre}_lyrics_alt"][axis_name]
            inst_v = lyrics_readings[f"{genre}_inst"][axis_name]
            effect = (lyrics_v + alt_v) / 2.0 - inst_v
            regen_noise = abs(lyrics_v - alt_v)
            ratio: Optional[float] = abs(effect) / regen_noise if regen_noise > 1e-12 else None
            exceeds_noise = abs(effect) > regen_noise
            stats[axis_name][genre] = {
                "effect": _round(effect),
                "regen_noise": _round(regen_noise),
                "ratio": _round(ratio) if ratio is not None else None,
                "exceeds_noise": exceeds_noise,
            }
    return stats


def _musicgen_cells(musicgen_readings: dict[str, dict[str, float]]) -> dict[str, dict[str, list[str]]]:
    cells: dict[str, dict[str, list[str]]] = {
        knob: {level: [] for level in _MUSICGEN_LEVELS} for knob in _MUSICGEN_KNOBS
    }
    for sample_id in musicgen_readings:
        for knob in _MUSICGEN_KNOBS:
            for level in _MUSICGEN_LEVELS:
                if sample_id.startswith(f"{knob}_{level}_"):
                    cells[knob][level].append(sample_id)
    for knob in _MUSICGEN_KNOBS:
        for level in _MUSICGEN_LEVELS:
            n = len(cells[knob][level])
            if n != _MUSICGEN_CELL_SIZE:
                raise ValueError(
                    f"expected {_MUSICGEN_CELL_SIZE} samples for {knob}_{level}_*, got {n}"
                )
    return cells


def compute_musicgen_knob_cells(
    readings: dict[str, dict[str, dict[str, float]]], axis_names: list[str]
) -> dict[str, dict[str, Any]]:
    """Per axis x knob: low/high mean + sample stdev and Cohen's d.

    `pooled = sqrt((var_lo + var_hi) / 2)`; `cohens_d` is null when both
    cells have zero variance (`pooled == 0`).
    """
    musicgen_readings = readings["musicgen_k2_contrast"]
    cells = _musicgen_cells(musicgen_readings)

    stats: dict[str, dict[str, Any]] = {}
    for axis_name in axis_names:
        stats[axis_name] = {}
        for knob in _MUSICGEN_KNOBS:
            low_vals = [musicgen_readings[sid][axis_name] for sid in cells[knob]["low"]]
            high_vals = [musicgen_readings[sid][axis_name] for sid in cells[knob]["high"]]
            low_mean = statistics.mean(low_vals)
            high_mean = statistics.mean(high_vals)
            low_stdev = statistics.stdev(low_vals)
            high_stdev = statistics.stdev(high_vals)
            pooled = sqrt((low_stdev**2 + high_stdev**2) / 2.0)
            cohens_d = (high_mean - low_mean) / pooled if pooled > 1e-12 else None
            stats[axis_name][knob] = {
                "low_mean": _round(low_mean),
                "low_stdev": _round(low_stdev),
                "high_mean": _round(high_mean),
                "high_stdev": _round(high_stdev),
                "cohens_d": _round(cohens_d) if cohens_d is not None else None,
            }
    return stats


def _pooled_axis_values(
    readings: dict[str, dict[str, dict[str, float]]], axis_names: list[str]
) -> dict[str, list[float]]:
    """Every axis's readings pooled across both fixtures' samples (38 total)."""
    values: dict[str, list[float]] = {name: [] for name in axis_names}
    for fixture_readings in readings.values():
        for row in fixture_readings.values():
            for name in axis_names:
                values[name].append(row[name])
    return values


def axis_correlation_matrix(
    readings: dict[str, dict[str, dict[str, float]]], axis_names: list[str]
) -> dict[str, dict[str, float]]:
    """Pearson r for every axis pair, pooled over the full readings pool.

    Generalizes the old hardcoded `warmth_brightness_pearson_r` redundancy
    check to any axis pair in the config. Output is `{axis_a: {axis_b: r}}`
    keyed in config axis order — each unordered pair appears exactly once
    (`axis_a` always precedes `axis_b` in `axis_names`), the diagonal is
    omitted, and `r` is rounded to 6 digits.
    """
    values = _pooled_axis_values(readings, axis_names)
    matrix: dict[str, dict[str, float]] = {}
    for i, axis_a in enumerate(axis_names):
        for axis_b in axis_names[i + 1 :]:
            r = statistics.correlation(values[axis_a], values[axis_b])
            matrix.setdefault(axis_a, {})[axis_b] = _round(r)
    return matrix


def sign_census(
    readings: dict[str, dict[str, dict[str, float]]], axis_names: list[str]
) -> dict[str, dict[str, int]]:
    """Per-axis negative/total sign counts across the full readings pool.

    Generalizes the old hardcoded `acousticness_sign_census` (sign-reading
    reliability check) to every axis in the config.
    """
    values = _pooled_axis_values(readings, axis_names)
    return {
        name: {
            "negative": sum(1 for value in axis_values if value < 0),
            "total": len(axis_values),
        }
        for name, axis_values in values.items()
    }


def compute_genre_effect_vs_regen_noise(
    readings: dict[str, dict[str, dict[str, float]]], axis_names: list[str]
) -> dict[str, dict[str, Any]]:
    """Per axis: does the EDM-vs-Rock matched-pair genre contrast exceed the
    lyrics/lyrics_alt regeneration-noise floor?

    The `lyrics_vocal_contrast` fixture's edm_* / rock_* samples are a
    MATCHED PAIR (the same underlying song, StartinA, in 3 variants), so —
    unlike the vocal-presence-focused `lyrics_effect_vs_regen_noise` — this
    reads the pair as a genre-axis ground-truth contrast:

    `pair_diff_v = reading[f"edm_{v}"] - reading[f"rock_{v}"]` for each
    variant `v` in `{lyrics, lyrics_alt, inst}`; `effect = mean(pair_diff_v)`.
    `regen_noise = max(|edm_lyrics - edm_lyrics_alt|, |rock_lyrics -
    rock_lyrics_alt|)` (worst-case regeneration noise across both genres).
    `ratio = |effect| / regen_noise` (null when `regen_noise` is ~0, same
    division-by-~zero guard as `lyrics_effect_vs_regen_noise`).
    `exceeds_noise = |effect| > regen_noise` is computed directly (not via
    `ratio`) so the gate still applies when `ratio` is null.
    """
    lyrics_readings = readings["lyrics_vocal_contrast"]
    stats: dict[str, dict[str, Any]] = {}
    for axis_name in axis_names:
        edm_vals = {v: lyrics_readings[f"edm_{v}"][axis_name] for v in _GENRE_VARIANTS}
        rock_vals = {v: lyrics_readings[f"rock_{v}"][axis_name] for v in _GENRE_VARIANTS}
        pair_diffs = [edm_vals[v] - rock_vals[v] for v in _GENRE_VARIANTS]
        effect = statistics.mean(pair_diffs)
        regen_noise = max(
            abs(edm_vals["lyrics"] - edm_vals["lyrics_alt"]),
            abs(rock_vals["lyrics"] - rock_vals["lyrics_alt"]),
        )
        ratio: Optional[float] = abs(effect) / regen_noise if regen_noise > 1e-12 else None
        exceeds_noise = abs(effect) > regen_noise
        stats[axis_name] = {
            "effect": _round(effect),
            "regen_noise": _round(regen_noise),
            "ratio": _round(ratio) if ratio is not None else None,
            "exceeds_noise": exceeds_noise,
        }
    return stats


def build_calibration(
    *,
    checkpoint: Optional[str],
    amodel: Optional[str],
    run_date: Optional[str] = None,
    axes_config_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Run the full calibration and return the loader-ready payload dict.

    Parameters
    ----------
    axes_config_path
        Optional path to an alternate axis-battery YAML (e.g. a
        variant-sweep config). Validated with the same
        `validate_axes_battery` fail-fast rules as the canonical
        `config/semantic_probe_axes.yaml`. Default (`None`): the canonical
        config, matching prior behavior exactly.

    Raises
    ------
    ValueError
        If `checkpoint`/`amodel` do not match every fixture's pinned
        `model` block (embedding-space mismatch; see
        `_validate_embedding_space_pin`); if the axis battery (canonical or
        `axes_config_path`) fails `validate_axes_battery`; if
        `probe_determinism_same_run` is `False` (text tower nondeterministic
        within a single run); or if any fixture's
        `reproduction_check.max_abs_deviation` exceeds
        `_REPRODUCTION_TOLERANCE` (reproduction drift against the stored
        fixture `contrast_fit`). The checkpoint pin alone does not
        guarantee a trustworthy run, so these instrument sanity signals
        are enforced as gates rather than merely recorded.
    LearnedModelUnavailable
        If `laion_clap` is not installed (propagated from `load_clap_model`).
    """
    axes_config = load_axes_config(axes_config_path)
    axes = validate_axes_battery(axes_config.get("axes", []))
    axis_names = [axis["name"] for axis in axes]

    fixtures = load_fixtures()
    checkpoint_sha256 = _validate_embedding_space_pin(fixtures, checkpoint=checkpoint, amodel=amodel)
    model = load_clap_model(checkpoint, amodel)

    axis_embeddings, probe_determinism = embed_axis_battery(axes, model)
    readings = compute_readings(fixtures, axis_embeddings)
    reproduction_check = compute_reproduction_check(fixtures, model)

    if not probe_determinism:
        raise ValueError(
            "text tower nondeterministic within a single run; instrument broken, "
            "refusing to write calibration"
        )
    for fixture_name, info in reproduction_check.items():
        deviation = info["max_abs_deviation"]
        if deviation > _REPRODUCTION_TOLERANCE:
            raise ValueError(
                f"reproduction drift against stored fixture {fixture_name!r} contrast_fit "
                f"exceeds tolerance: max_abs_deviation={deviation!r} > "
                f"tolerance={_REPRODUCTION_TOLERANCE!r}; readings not comparable to fixture "
                f"provenance, refusing to write"
            )

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
    }
    if run_date:
        payload["run_date"] = run_date
    payload["model"] = {
        "name": "laion_clap",
        "checkpoint": Path(checkpoint).name if checkpoint else None,
        "checkpoint_sha256": checkpoint_sha256,
        "amodel": amodel,
        "laion_clap_version": _package_version("laion_clap"),
        "torch_version": _package_version("torch"),
    }
    payload["axes_config_version"] = axes_config.get("schema_version")
    payload["probe_determinism_same_run"] = probe_determinism
    payload["readings"] = readings
    payload["reproduction_check"] = reproduction_check
    payload["stats"] = {
        "lyrics_effect_vs_regen_noise": compute_lyrics_effect_vs_regen_noise(readings, axis_names),
        "musicgen_knob_cells": compute_musicgen_knob_cells(readings, axis_names),
        "genre_effect_vs_regen_noise": compute_genre_effect_vs_regen_noise(readings, axis_names),
        "axis_correlation_matrix": axis_correlation_matrix(readings, axis_names),
        "sign_census": sign_census(readings, axis_names),
    }
    return payload


def _print_summary(payload: dict[str, Any]) -> None:
    print(f"probe_determinism_same_run: {payload['probe_determinism_same_run']}")
    print("reproduction_check:")
    for fixture_name, info in payload["reproduction_check"].items():
        print(
            f"  {fixture_name}: n_samples={info['n_samples']} "
            f"n_exact_6dp={info['n_exact_6dp']} max_abs_deviation={info['max_abs_deviation']}"
        )
    stats = payload["stats"]
    print("lyrics_effect_vs_regen_noise (exceeds_noise):")
    for axis_name, per_genre in stats["lyrics_effect_vs_regen_noise"].items():
        flags = {genre: row["exceeds_noise"] for genre, row in per_genre.items()}
        print(f"  {axis_name}: {flags}")
    print("musicgen_knob_cells (cohens_d):")
    for axis_name, per_knob in stats["musicgen_knob_cells"].items():
        ds = {knob: row["cohens_d"] for knob, row in per_knob.items()}
        print(f"  {axis_name}: {ds}")
    print("genre_effect_vs_regen_noise (exceeds_noise):")
    for axis_name, row in stats["genre_effect_vs_regen_noise"].items():
        print(f"  {axis_name}: exceeds_noise={row['exceeds_noise']} ratio={row['ratio']}")
    print("axis_correlation_matrix (top 5 |r| pairs):")
    pairs: list[tuple[str, str, float]] = [
        (axis_a, axis_b, r)
        for axis_a, row in stats["axis_correlation_matrix"].items()
        for axis_b, r in row.items()
    ]
    pairs.sort(key=lambda item: abs(item[2]), reverse=True)
    for axis_a, axis_b, r in pairs[:5]:
        print(f"  {axis_a} x {axis_b}: {r}")
    print("sign_census:")
    for axis_name, census in stats["sign_census"].items():
        print(f"  {axis_name}: {census['negative']}/{census['total']} negative")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Calibrate the CLAP semantic-axis battery (config/semantic_probe_axes.yaml) "
            "against stored real audio embeddings. Embedding-space only — no audio, no "
            "fresh audio inference. Requires the `semantic-embed` extra."
        ),
    )
    parser.add_argument(
        "--checkpoint",
        default=None,
        help="CLAP checkpoint path/id (default: upstream default checkpoint)",
    )
    parser.add_argument(
        "--amodel",
        default=None,
        help=(
            "CLAP audio-encoder architecture id, e.g. 'HTSAT-base'. Required for "
            "music_* checkpoints per the upstream README; default: upstream default "
            "encoder (HTSAT-tiny family)."
        ),
    )
    parser.add_argument("--output", type=Path, required=True, help="output calibration YAML path")
    parser.add_argument(
        "--run-date",
        default=None,
        help="optional explicit run date (e.g. 2026-07-04) recorded verbatim; omitted if unset",
    )
    parser.add_argument(
        "--axes-config",
        type=Path,
        default=None,
        help=(
            "path to an alternate axis-battery YAML (e.g. a variant-sweep config) "
            "to calibrate instead of the canonical config/semantic_probe_axes.yaml; "
            "validated with the same fail-fast rules as the canonical loader"
        ),
    )
    args = parser.parse_args(argv)

    try:
        payload = build_calibration(
            checkpoint=args.checkpoint,
            amodel=args.amodel,
            run_date=args.run_date,
            axes_config_path=args.axes_config,
        )
    except (ValueError, LearnedModelUnavailable) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False)

    print(f"wrote {args.output}")
    _print_summary(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
