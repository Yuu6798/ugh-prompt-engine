from __future__ import annotations

import hashlib
import json
from pathlib import Path

from svp_rpe.eval.scorer_integrated import score_integrated
from svp_rpe.eval.scorer_rpe import score_rpe
from svp_rpe.eval.scorer_ugher import score_ugher
from svp_rpe.io.audio_loader import load_audio
from svp_rpe.rpe.extractor import extract_rpe
from svp_rpe.svp.generator import generate_svp
from svp_rpe.svp.render_yaml import render_yaml


def _write_snapshot_outputs(audio_path: str, output_dir: Path) -> str:
    audio = load_audio(audio_path)
    rpe_bundle = extract_rpe(audio)
    svp_bundle = generate_svp(rpe_bundle)
    rpe_score = score_rpe(rpe_bundle.physical)
    ugher_score = score_ugher(rpe_bundle, svp_bundle)
    integrated = score_integrated(ugher_score, rpe_score)

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "rpe.json").write_text(
        json.dumps(rpe_bundle.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "svp.yaml").write_text(render_yaml(svp_bundle), encoding="utf-8")
    (output_dir / "evaluation.json").write_text(
        json.dumps(
            {
                "rpe_score": rpe_score.model_dump(),
                "ugher_score": ugher_score.model_dump(),
                "integrated_score": integrated.model_dump(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    digest = hashlib.sha256()
    for name in ("rpe.json", "svp.yaml", "evaluation.json"):
        digest.update(name.encode("utf-8"))
        digest.update((output_dir / name).read_bytes())
    return digest.hexdigest()


def test_synthetic_audio_snapshot_outputs_are_deterministic(sine_wave_mono, tmp_path):
    first_hash = _write_snapshot_outputs(sine_wave_mono, tmp_path / "run1")
    second_hash = _write_snapshot_outputs(sine_wave_mono, tmp_path / "run2")

    assert first_hash == second_hash
    assert (tmp_path / "run1" / "rpe.json").is_file()
    assert (tmp_path / "run1" / "svp.yaml").is_file()
    assert (tmp_path / "run1" / "evaluation.json").is_file()
