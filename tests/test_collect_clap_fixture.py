"""tests/test_collect_clap_fixture.py — collect_clap_fixture.py smoke test.

Fake `laion_clap` module only via `sys.modules` monkeypatch; no real
learned-model dependency needed. `embed_audio_file` decodes audio for
real via `librosa.load` (see `clap_adapter`'s determinism-by-construction
docstring), so the manifest's `audio_path` must point at a real (tiny)
WAV file rather than placeholder bytes.
"""
from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from scripts.collect_clap_fixture import (
    collect_fixture,
    load_fixture,
    main,
    merge_fixture,
    sidecar_path_for,
    split_fixture,
    write_fixture,
)


def _write_wav(path: Path, *, seconds: float = 0.05, sample_rate: int = 48000) -> None:
    n_samples = max(1, int(round(seconds * sample_rate)))
    t = np.linspace(0, seconds, n_samples, endpoint=False)
    y = (0.2 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
    sf.write(str(path), y, sample_rate)


def _install_fake_clap(monkeypatch: pytest.MonkeyPatch) -> dict:
    captured: dict = {}

    class FakeCLAPModule:
        def __init__(self, **kwargs):
            captured["init_kwargs"] = dict(kwargs)

        def load_ckpt(self, checkpoint=None):
            pass

        def get_audio_embedding_from_data(self, x, use_tensor=False):
            # One row per chunk; a short fixture file is always a single
            # chunk, so this reduces to the fixed [3.0, 4.0] row the
            # existing assertions expect.
            return np.asarray([[3.0, 4.0] for _ in x], dtype=np.float64)

        def get_text_embedding(self, texts, use_tensor=False):
            vectors = {"bright": [1.0, 0.0], "dark": [0.0, 1.0]}
            return np.asarray(
                [vectors.get(text, [1.0, 1.0]) for text in texts], dtype=np.float64
            )

    fake_root = types.ModuleType("laion_clap")
    fake_root.CLAP_Module = FakeCLAPModule
    fake_root.__version__ = "0.0.0-fake"
    monkeypatch.setitem(sys.modules, "laion_clap", fake_root)
    return captured


def _write_manifest(manifest_path: Path, audio_path: Path | str) -> Path:
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
    _write_wav(audio_path)
    manifest_path = _write_manifest(tmp_path / "manifest.json", audio_path)

    fixture = collect_fixture(manifest_path)

    assert fixture["schema_version"] == "1.1"
    assert fixture["generator"] == "collect_clap_fixture.py"
    assert fixture["manifest"] == "manifest.json"
    assert len(fixture["samples"]) == 1

    sample = fixture["samples"][0]
    assert sample["sample_id"] == "s1"
    assert "audio_sha256" in sample and len(sample["audio_sha256"]) == 64
    assert sample["condition"] == {"level": "high"}
    assert sample["audio_embedding"] == pytest.approx([0.6, 0.8])
    assert set(sample["cosines"].keys()) == {"bright", "dark"}
    assert sample["contrast_fit"] is not None

    assert fixture["model"]["name"] == "laion_clap"
    assert fixture["model"]["checkpoint_sha256"] is None
    assert fixture["model"]["info"][0]["name"] == "laion_clap"
    assert fixture["model"]["info"][0]["version"] == "0.0.0-fake"


def test_collect_fixture_forwards_amodel_and_records_it(monkeypatch, tmp_path):
    captured = _install_fake_clap(monkeypatch)
    audio_path = tmp_path / "a.wav"
    _write_wav(audio_path)
    manifest_path = _write_manifest(tmp_path / "manifest.json", audio_path)

    checkpoint_path = tmp_path / "music_audioset_epoch_15_esc_90.14.pt"
    checkpoint_path.write_bytes(b"fake-checkpoint")
    fixture = collect_fixture(
        manifest_path, checkpoint=str(checkpoint_path), amodel="HTSAT-base"
    )

    assert captured["init_kwargs"] == {"enable_fusion": False, "amodel": "HTSAT-base"}
    assert fixture["model"]["amodel"] == "HTSAT-base"
    assert fixture["model"]["checkpoint"] == "music_audioset_epoch_15_esc_90.14.pt"
    assert fixture["model"]["checkpoint_sha256"] == hashlib.sha256(
        b"fake-checkpoint"
    ).hexdigest()


def test_collect_fixture_records_null_checkpoint_hash_for_auto_download(
    monkeypatch, tmp_path
):
    _install_fake_clap(monkeypatch)
    audio_path = tmp_path / "a.wav"
    _write_wav(audio_path)
    manifest_path = _write_manifest(tmp_path / "manifest.json", audio_path)

    fixture = collect_fixture(manifest_path)

    assert fixture["model"]["checkpoint"] is None
    assert fixture["model"]["checkpoint_sha256"] is None


def test_main_amodel_cli_arg_reaches_model_info_block(monkeypatch, tmp_path):
    captured = _install_fake_clap(monkeypatch)
    audio_path = tmp_path / "a.wav"
    _write_wav(audio_path)
    manifest_path = _write_manifest(tmp_path / "manifest.json", audio_path)
    output_path = tmp_path / "fixture.json"

    exit_code = main(
        [
            "--manifest",
            str(manifest_path),
            "--output",
            str(output_path),
            "--amodel",
            "HTSAT-base",
        ]
    )

    assert exit_code == 0
    assert captured["init_kwargs"] == {"enable_fusion": False, "amodel": "HTSAT-base"}
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["model"]["amodel"] == "HTSAT-base"
    assert "audio_embedding" not in written["samples"][0]
    assert sidecar_path_for(output_path).exists()
    assert load_fixture(output_path)["model"]["amodel"] == "HTSAT-base"


def test_main_writes_output_file(monkeypatch, tmp_path):
    _install_fake_clap(monkeypatch)
    audio_path = tmp_path / "a.wav"
    _write_wav(audio_path)
    manifest_path = _write_manifest(tmp_path / "manifest.json", audio_path)
    output_path = tmp_path / "fixture.json"

    exit_code = main(["--manifest", str(manifest_path), "--output", str(output_path)])

    assert exit_code == 0
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["samples"][0]["sample_id"] == "s1"
    assert "audio_embedding" not in written["samples"][0]
    assert written["embeddings_sidecar"] == sidecar_path_for(output_path).name

    sidecar_path = sidecar_path_for(output_path)
    assert sidecar_path.exists()
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert sidecar["embeddings"]["s1"] == pytest.approx([0.6, 0.8])

    reloaded = load_fixture(output_path)
    assert reloaded["samples"][0]["audio_embedding"] == pytest.approx([0.6, 0.8])


def test_main_reports_install_hint_when_unavailable(monkeypatch, tmp_path, capsys):
    monkeypatch.setitem(sys.modules, "laion_clap", None)
    audio_path = tmp_path / "a.wav"
    _write_wav(audio_path)
    manifest_path = _write_manifest(tmp_path / "manifest.json", audio_path)
    output_path = tmp_path / "fixture.json"

    exit_code = main(["--manifest", str(manifest_path), "--output", str(output_path)])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "semantic-embed" in captured.err
    assert not output_path.exists()


def test_relative_audio_path_resolves_against_manifest_directory(monkeypatch, tmp_path):
    """A relative `audio_path` must resolve against the manifest file's
    own directory, not the process CWD — the manifest and its audio can
    live in a subdirectory invoked from anywhere.
    """
    _install_fake_clap(monkeypatch)

    manifest_subdir = tmp_path / "corpus" / "sub"
    manifest_subdir.mkdir(parents=True)
    audio_path = manifest_subdir / "a.wav"
    _write_wav(audio_path)
    # Bare filename, relative to the manifest's own directory.
    manifest_path = _write_manifest(manifest_subdir / "manifest.json", "a.wav")

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    fixture = collect_fixture(manifest_path)

    sample = fixture["samples"][0]
    assert sample["audio_path"] == "a.wav"
    assert sample["audio_embedding"] == pytest.approx([0.6, 0.8])


def test_absolute_audio_path_records_portable_identifier(monkeypatch, tmp_path):
    _install_fake_clap(monkeypatch)

    audio_path = (tmp_path / "a.wav").resolve()
    _write_wav(audio_path)
    manifest_path = _write_manifest(tmp_path / "manifest.json", audio_path)

    fixture = collect_fixture(manifest_path)

    assert fixture["samples"][0]["audio_path"] == "a.wav"


def _write_manifest_with_extras(
    manifest_path: Path, audio_path: Path, extras: dict
) -> Path:
    manifest_path.write_text(
        json.dumps(
            [
                {
                    "sample_id": "s1",
                    "audio_path": str(audio_path),
                    "prompts": {"positive": ["bright"], "negative": ["dark"]},
                    **extras,
                }
            ]
        ),
        encoding="utf-8",
    )
    return manifest_path


def test_manifest_sha256_pin_matching_passes(monkeypatch, tmp_path):
    """manifest の audio_sha256 pin が実ファイルと一致すれば照合を通り、
    出力には計算値が載る。"""
    import hashlib

    _install_fake_clap(monkeypatch)
    audio_path = tmp_path / "a.wav"
    _write_wav(audio_path)
    true_sha = hashlib.sha256(audio_path.read_bytes()).hexdigest()
    manifest_path = _write_manifest_with_extras(
        tmp_path / "manifest.json", audio_path, {"audio_sha256": true_sha}
    )

    fixture = collect_fixture(manifest_path)

    assert fixture["samples"][0]["audio_sha256"] == true_sha


def test_manifest_sha256_pin_mismatch_raises(monkeypatch, tmp_path):
    """stale な pin は「埋め込んだバイト列と違うハッシュを fixture が主張する」
    事故の兆候なので fail-fast する。"""
    _install_fake_clap(monkeypatch)
    audio_path = tmp_path / "a.wav"
    _write_wav(audio_path)
    manifest_path = _write_manifest_with_extras(
        tmp_path / "manifest.json", audio_path, {"audio_sha256": "deadbeef" * 8}
    )

    with pytest.raises(ValueError, match="audio_sha256 pin does not match"):
        collect_fixture(manifest_path)


def test_passthrough_cannot_override_reserved_output_keys(monkeypatch, tmp_path):
    """manifest の余剰キーが計算済みの予約キー（cosines / audio_embedding 等）を
    上書きできないこと（passthrough を先に展開し計算値が常に勝つ順序）。"""
    _install_fake_clap(monkeypatch)
    audio_path = tmp_path / "a.wav"
    _write_wav(audio_path)
    manifest_path = _write_manifest_with_extras(
        tmp_path / "manifest.json",
        audio_path,
        {"cosines": "stale-garbage", "audio_embedding": "stale-garbage"},
    )

    fixture = collect_fixture(manifest_path)

    sample = fixture["samples"][0]
    assert sample["audio_embedding"] == pytest.approx([0.6, 0.8])
    assert isinstance(sample["cosines"], dict)


# ---------------------------------------------------------------------------
# CLAP embedding sidecar: split/merge round-trip + file IO
# ---------------------------------------------------------------------------


def _sample_fixture() -> dict:
    return {
        "schema_version": "1.1",
        "generator": "collect_clap_fixture.py",
        "manifest": "manifest.json",
        "model": {"name": "laion_clap", "checkpoint": None, "checkpoint_sha256": None},
        "samples": [
            {"sample_id": "s1", "audio_embedding": [0.6, 0.8], "cosines": {"bright": 0.6}},
            {"sample_id": "s2", "audio_embedding": [0.0, 1.0], "cosines": {"bright": 0.0}},
        ],
    }


def test_sidecar_path_for_replaces_json_suffix_with_embeddings_json():
    assert sidecar_path_for(Path("foo.json")) == Path("foo.embeddings.json")
    assert sidecar_path_for(Path("dir/bar.json")) == Path("dir/bar.embeddings.json")


def test_split_fixture_rejects_duplicate_sample_ids():
    fixture = _sample_fixture()
    fixture["samples"][1]["sample_id"] = "s1"  # duplicate id

    with pytest.raises(ValueError, match="duplicate sample_id"):
        split_fixture(fixture, "foo.embeddings.json")


def test_split_fixture_removes_embeddings_and_adds_sidecar_pointer():
    fixture = _sample_fixture()

    main_fixture, sidecar = split_fixture(fixture, "foo.embeddings.json")

    assert "audio_embedding" not in main_fixture["samples"][0]
    assert "audio_embedding" not in main_fixture["samples"][1]
    assert main_fixture["embeddings_sidecar"] == "foo.embeddings.json"
    assert sidecar["schema_version"] == "1.0"
    assert sidecar["embeddings"] == {"s1": [0.6, 0.8], "s2": [0.0, 1.0]}
    # split_fixture is pure and does not mutate its input.
    assert "audio_embedding" in fixture["samples"][0]


def test_merge_fixture_is_the_inverse_of_split_fixture():
    fixture = _sample_fixture()

    main_fixture, sidecar = split_fixture(fixture, "foo.embeddings.json")
    merged = merge_fixture(main_fixture, sidecar)

    assert merged == fixture


def test_write_fixture_and_load_fixture_round_trip(tmp_path):
    fixture = _sample_fixture()
    main_path = tmp_path / "foo.json"

    write_fixture(fixture, main_path)

    assert main_path.exists()
    sidecar_path = sidecar_path_for(main_path)
    assert sidecar_path.exists()
    assert sidecar_path.name == "foo.embeddings.json"

    written_main = json.loads(main_path.read_text(encoding="utf-8"))
    assert "audio_embedding" not in written_main["samples"][0]
    assert written_main["embeddings_sidecar"] == "foo.embeddings.json"
    # Formatting matches the repo's existing convention: indent=2 + trailing newline.
    assert main_path.read_text(encoding="utf-8").endswith("}\n")
    assert sidecar_path.read_text(encoding="utf-8").endswith("}\n")

    written_sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert written_sidecar["of_fixture"] == "foo.json"
    assert written_sidecar["embeddings"]["s1"] == pytest.approx([0.6, 0.8])

    loaded = load_fixture(main_path)
    assert loaded == fixture
