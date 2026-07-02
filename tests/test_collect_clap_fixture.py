"""tests/test_collect_clap_fixture.py — collect_clap_fixture.py smoke test.

Fake `laion_clap` module only via `sys.modules` monkeypatch; no real
learned-model dependency needed.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from scripts.collect_clap_fixture import collect_fixture, main


def _install_fake_clap(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCLAPModule:
        def __init__(self, **kwargs):
            pass

        def load_ckpt(self, checkpoint=None):
            pass

        def get_audio_embedding_from_filelist(self, paths, use_tensor=False):
            return np.asarray([[3.0, 4.0]], dtype=np.float64)

        def get_text_embedding(self, texts, use_tensor=False):
            vectors = {"bright": [1.0, 0.0], "dark": [0.0, 1.0]}
            return np.asarray(
                [vectors.get(text, [1.0, 1.0]) for text in texts], dtype=np.float64
            )

    fake_root = types.ModuleType("laion_clap")
    fake_root.CLAP_Module = FakeCLAPModule
    fake_root.__version__ = "0.0.0-fake"
    monkeypatch.setitem(sys.modules, "laion_clap", fake_root)


def _write_manifest(tmp_path: Path, audio_path: Path) -> Path:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            [
                {
                    "sample_id": "s1",
                    "audio_path": str(audio_path),
                    "prompts": {"positive": ["bright"], "negative": ["dark"]},
                    "condition": {"level": "high"},
                }
            ]
        ),
        encoding="utf-8",
    )
    return manifest_path


def test_collect_fixture_writes_expected_keys(monkeypatch, tmp_path):
    _install_fake_clap(monkeypatch)
    audio_path = tmp_path / "a.wav"
    audio_path.write_bytes(b"fake-audio-bytes")
    manifest_path = _write_manifest(tmp_path, audio_path)

    fixture = collect_fixture(manifest_path)

    assert fixture["schema_version"] == "1.0"
    assert fixture["generator"] == "collect_clap_fixture.py"
    assert len(fixture["samples"]) == 1

    sample = fixture["samples"][0]
    assert sample["sample_id"] == "s1"
    assert "audio_sha256" in sample and len(sample["audio_sha256"]) == 64
    assert sample["condition"] == {"level": "high"}
    assert sample["audio_embedding"] == pytest.approx([0.6, 0.8])
    assert set(sample["cosines"].keys()) == {"bright", "dark"}
    assert sample["contrast_fit"] is not None

    assert fixture["model"]["name"] == "laion_clap"
    assert fixture["model"]["info"][0]["name"] == "laion_clap"
    assert fixture["model"]["info"][0]["version"] == "0.0.0-fake"


def test_main_writes_output_file(monkeypatch, tmp_path):
    _install_fake_clap(monkeypatch)
    audio_path = tmp_path / "a.wav"
    audio_path.write_bytes(b"fake-audio-bytes")
    manifest_path = _write_manifest(tmp_path, audio_path)
    output_path = tmp_path / "fixture.json"

    exit_code = main(["--manifest", str(manifest_path), "--output", str(output_path)])

    assert exit_code == 0
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["samples"][0]["sample_id"] == "s1"


def test_main_reports_install_hint_when_unavailable(monkeypatch, tmp_path, capsys):
    monkeypatch.setitem(sys.modules, "laion_clap", None)
    audio_path = tmp_path / "a.wav"
    audio_path.write_bytes(b"fake-audio-bytes")
    manifest_path = _write_manifest(tmp_path, audio_path)
    output_path = tmp_path / "fixture.json"

    exit_code = main(["--manifest", str(manifest_path), "--output", str(output_path)])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "semantic-embed" in captured.err
    assert not output_path.exists()
