"""scripts/collect_clap_fixture.py — CLAP fixture-collection runbook.

Requires the `semantic-embed` extra (`pip install -e ".[semantic-embed]"`)
at runtime. This script is NEVER run in CI — it is a manual runbook for
collecting real CLAP embeddings + cosine-similarity fixtures from local
audio, mirroring the K-series fixture-driven DD-A pattern
(`scripts/measure_grip.py`): audio + inference happen here, once, and the
resulting numeric fixture is what tests / `similarity.py` consumers run
against deterministically afterward.

Manifest format (YAML or JSON), a list of samples:

    - sample_id: "sample_01"
      audio_path: "path/to/audio.wav"
      prompts:
        positive: ["a bright uplifting song"]
        negative: ["a dark somber song"]
      condition: {level: "high", knob: "brightness"}   # passthrough, optional

A relative `audio_path` is resolved against the manifest file's own
directory (not the process CWD), so manifests stay portable and can be
run from anywhere — see `_resolve_audio_path`. Absolute `audio_path`
entries pass through unchanged for embedding, but the output fixture
records portable provenance: relative manifest values are kept as
written, and absolute paths are reduced to a repo-relative path when
possible or a basename otherwise.

`condition` (and any other extra keys) are passed through to the sample's
row in the output fixture untouched. Output fixture JSON keeps only
numeric / string data — no audio bytes, only a `audio_sha256` provenance
hash.

Usage:
    python scripts/collect_clap_fixture.py --manifest manifest.yaml --output fixture.json

    # music_* checkpoints require --amodel HTSAT-base (upstream README):
    python scripts/collect_clap_fixture.py \\
        --manifest manifest.yaml --output fixture.json \\
        --checkpoint music_audioset_epoch_15_esc_90.14.pt --amodel HTSAT-base
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from svp_rpe.rpe.learned import LearnedModelUnavailable  # noqa: E402
from svp_rpe.rpe.learned.clap_adapter import (  # noqa: E402
    embed_audio_file,
    embed_texts,
    load_clap_model,
)
from svp_rpe.rpe.learned.similarity import contrast_fit, prompt_audio_fit  # noqa: E402
from svp_rpe.rpe.models import LearnedAudioAnnotations  # noqa: E402

SCHEMA_VERSION = "1.0"
GENERATOR = "collect_clap_fixture.py"
_PASSTHROUGH_EXCLUDE = {"sample_id", "audio_path", "prompts"}


def load_manifest(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        import yaml

        raw = yaml.safe_load(text)
    else:
        raw = json.loads(text)
    if not isinstance(raw, list):
        raise ValueError(f"manifest must be a JSON/YAML list of samples: {path}")
    return raw


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _round_vector(vector: list[float], digits: int = 6) -> list[float]:
    return [round(float(value), digits) for value in vector]


def _resolve_audio_path(audio_path: Path, *, manifest_dir: Path) -> Path:
    """Resolve a manifest `audio_path` entry against the manifest's own directory.

    Absolute paths pass through unchanged; relative paths are resolved
    against `manifest_dir` (the directory containing the manifest file)
    rather than the process CWD, mirroring the precedent in
    `svp_rpe.calibration.analyze._resolve_audio_path` /
    `svp_rpe.roundtrip.manifest.resolve_audio_path`: manifests are meant
    to be portable and runnable from any working directory.
    """
    return audio_path if audio_path.is_absolute() else (manifest_dir / audio_path).resolve()


def _portable_path(path: Path, *, base: Path = ROOT) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(base.resolve()).as_posix()
    except ValueError:
        return resolved.name


def _recorded_audio_path(
    raw_audio_path: str, *, resolved_audio_path: Path, manifest_dir: Path
) -> str:
    raw_path = Path(raw_audio_path)
    if not raw_path.is_absolute():
        return raw_path.as_posix()
    try:
        return resolved_audio_path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        try:
            return resolved_audio_path.resolve().relative_to(manifest_dir.resolve()).as_posix()
        except ValueError:
            return resolved_audio_path.name


def _recorded_checkpoint(checkpoint: str | None) -> str | None:
    if checkpoint is None:
        return None
    candidate = Path(checkpoint)
    return candidate.name or checkpoint


def collect_sample(
    sample: dict[str, Any],
    *,
    model: Any,
    manifest_dir: Path,
    amodel: str | None = None,
) -> tuple[dict[str, Any], LearnedAudioAnnotations]:
    """1 サンプルを処理し、(fixture 行, annotations) を返す。

    重い処理はすべて `clap_adapter` / `similarity` に委譲する — ここは
    manifest フィールドの受け渡しと丸めのみを担当する薄いラッパー。
    """
    sample_id = str(sample["sample_id"])
    raw_audio_path = str(sample["audio_path"])
    audio_path = _resolve_audio_path(Path(raw_audio_path), manifest_dir=manifest_dir)
    prompts = sample.get("prompts", {})
    positive_texts = list(prompts.get("positive", []))
    negative_texts = list(prompts.get("negative", []))

    annotations = embed_audio_file(str(audio_path), model=model, amodel=amodel)
    audio_embedding = annotations.embedding.vector if annotations.embedding else []

    cosines: dict[str, float] = {}
    if positive_texts or negative_texts:
        all_texts = positive_texts + negative_texts
        all_embeddings = embed_texts(all_texts, model=model)
        fits = prompt_audio_fit(audio_embedding, all_embeddings)
        cosines = {text: round(fit, 6) for text, fit in zip(all_texts, fits, strict=True)}

    contrast: float | None = None
    if positive_texts and negative_texts:
        positive_embeddings = embed_texts(positive_texts, model=model)
        negative_embeddings = embed_texts(negative_texts, model=model)
        contrast = round(
            contrast_fit(audio_embedding, positive_embeddings, negative_embeddings), 6
        )

    passthrough = {
        key: value for key, value in sample.items() if key not in _PASSTHROUGH_EXCLUDE
    }

    # provenance は「実際に埋め込んだバイト列」から必ず計算する。manifest 側に
    # audio_sha256 pin があれば照合し、不一致は fail-fast（stale な pin を fixture が
    # 主張してしまう事故を防ぐ — 計算値が passthrough に上書きされない順序も担保）。
    computed_sha256 = _sha256_file(audio_path)
    manifest_pin = passthrough.pop("audio_sha256", None)
    if manifest_pin is not None and str(manifest_pin) != computed_sha256:
        raise ValueError(
            f"sample {sample_id!r}: manifest audio_sha256 pin does not match the "
            f"embedded file (pin={manifest_pin}, computed={computed_sha256})"
        )

    row = {
        **passthrough,
        "sample_id": sample_id,
        "audio_path": _recorded_audio_path(
            raw_audio_path, resolved_audio_path=audio_path, manifest_dir=manifest_dir
        ),
        "audio_sha256": computed_sha256,
        "audio_embedding": _round_vector(audio_embedding),
        "cosines": cosines,
        "contrast_fit": contrast,
    }
    return row, annotations


def collect_fixture(
    manifest_path: Path, *, checkpoint: str | None = None, amodel: str | None = None
) -> dict[str, Any]:
    samples = load_manifest(manifest_path)
    model = load_clap_model(checkpoint, amodel)
    manifest_dir = manifest_path.resolve().parent

    rows: list[dict[str, Any]] = []
    model_info: list[dict[str, Any]] = []
    for index, sample in enumerate(samples):
        row, annotations = collect_sample(
            sample, model=model, manifest_dir=manifest_dir, amodel=amodel
        )
        rows.append(row)
        if index == 0:
            model_info = [info.model_dump() for info in annotations.enabled_models]

    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "manifest": _portable_path(manifest_path),
        "model": {
            "name": "laion_clap",
            "checkpoint": _recorded_checkpoint(checkpoint),
            "amodel": amodel,
            "info": model_info,
        },
        "samples": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Collect real CLAP audio/text embeddings + cosine similarity fixtures. "
            "Requires the `semantic-embed` extra; never run in CI."
        ),
    )
    parser.add_argument("--manifest", type=Path, required=True, help="YAML/JSON sample manifest")
    parser.add_argument("--output", type=Path, required=True, help="output fixture JSON path")
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
            "music_* checkpoints (music_audioset_epoch_15_esc_90.14.pt and similar) per "
            "the upstream README; default: upstream default encoder (HTSAT-tiny family)."
        ),
    )
    args = parser.parse_args(argv)

    try:
        fixture = collect_fixture(
            args.manifest.resolve(), checkpoint=args.checkpoint, amodel=args.amodel
        )
    except LearnedModelUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
