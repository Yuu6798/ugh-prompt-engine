"""Q3-1 source separation adapter tests."""
from __future__ import annotations

from pathlib import Path
import hashlib
import subprocess

import numpy as np
import pytest
import soundfile as sf

import svp_rpe.io.source_separator as source_separator
from svp_rpe.rpe.learned import LearnedModelUnavailable
from svp_rpe.rpe.learned.source_separation_adapter import ensure_separation_available
from svp_rpe.io.source_separator import (
    DEFAULT_SHIFTS,
    REQUIRED_STEMS,
    STEM_NAMES,
    SeparatorNotAvailableError,
    StemBundle,
    separate_stems,
)


def _stems(n_samples: int = 8) -> dict[str, np.ndarray]:
    return {
        name: np.linspace(-0.5, 0.5, n_samples, dtype=np.float32)
        for name in STEM_NAMES
    }


def test_stem_bundle_creation() -> None:
    bundle = StemBundle(
        source_path="fixture.wav",
        model_name="htdemucs_ft",
        sample_rate=44100,
        duration_sec=0.5,
        stems=_stems(),
    )

    assert bundle.source_path == "fixture.wav"
    assert bundle.model_name == "htdemucs_ft"
    assert bundle.sample_rate == 44100
    assert set(bundle.stems) == REQUIRED_STEMS


def test_stem_bundle_requires_all_expected_stems() -> None:
    stems = _stems()
    del stems["other"]

    with pytest.raises(ValueError, match="missing=\\['other'\\]"):
        StemBundle(
            source_path="fixture.wav",
            model_name="htdemucs_ft",
            sample_rate=44100,
            duration_sec=0.5,
            stems=stems,
        )


def test_stem_bundle_accepts_extra_stems() -> None:
    """6-stem Demucs variants (guitar/piano) should not be rejected."""
    stems = _stems()
    stems["guitar"] = np.linspace(-0.5, 0.5, 8, dtype=np.float32)
    stems["piano"] = np.linspace(-0.5, 0.5, 8, dtype=np.float32)

    bundle = StemBundle(
        source_path="fixture.wav",
        model_name="htdemucs_6s",
        sample_rate=44100,
        duration_sec=0.5,
        stems=stems,
    )

    assert REQUIRED_STEMS.issubset(bundle.stems)
    assert "guitar" in bundle.stems
    assert "piano" in bundle.stems


def test_stem_bundle_rejects_non_mono_stems() -> None:
    stems = _stems()
    stems["vocals"] = np.zeros((2, 8), dtype=np.float32)

    with pytest.raises(ValueError, match="mono 1D"):
        StemBundle(
            source_path="fixture.wav",
            model_name="htdemucs_ft",
            sample_rate=44100,
            duration_sec=0.5,
            stems=stems,
        )


def test_stem_bundle_rejects_non_float32_stems() -> None:
    stems = _stems()
    stems["vocals"] = np.zeros(8, dtype=np.float64)

    with pytest.raises(ValueError, match="dtype float32"):
        StemBundle(
            source_path="fixture.wav",
            model_name="htdemucs_ft",
            sample_rate=44100,
            duration_sec=0.5,
            stems=stems,
        )


def test_no_demucs_raises_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(source_separator, "_HAS_DEMUCS", False)
    monkeypatch.setattr(source_separator, "_DemucsAPI", None)
    monkeypatch.setattr(source_separator, "_DemucsSeparate", None)

    with pytest.raises(SeparatorNotAvailableError, match="svp-rpe\\[separate\\]"):
        separate_stems(tmp_path / "fixture.wav")


def test_separate_stems_with_cli_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "fixture.wav"
    sf.write(source, np.zeros(16, dtype=np.float32), 44100)

    def fake_run(command, *, check, capture_output, text, env):
        output_dir = Path(command[command.index("-o") + 1])
        stem_dir = output_dir / "htdemucs_ft"
        stem_dir.mkdir(parents=True)
        stereo = np.column_stack([
            np.ones(16, dtype=np.float32),
            np.zeros(16, dtype=np.float32),
        ])
        for stem_name in STEM_NAMES:
            sf.write(stem_dir / f"{stem_name}.wav", stereo, 44100, subtype="FLOAT")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(source_separator, "_HAS_DEMUCS", True)
    monkeypatch.setattr(source_separator, "_DemucsAPI", None)
    monkeypatch.setattr(source_separator, "_DemucsSeparate", object())
    monkeypatch.setattr(source_separator, "_demucs_subprocess_env", lambda: {})
    monkeypatch.setattr(source_separator, "_subprocess_run", fake_run)

    bundle = separate_stems(source, model="htdemucs_ft", device="cpu")

    assert set(bundle.stems) == REQUIRED_STEMS
    assert bundle.sample_rate == 44100
    assert bundle.duration_sec == pytest.approx(16 / 44100, abs=1e-4)
    for stem in bundle.stems.values():
        assert stem.shape == (16,)
        np.testing.assert_allclose(stem, np.full(16, 0.5, dtype=np.float32))


def test_separate_stems_with_fake_demucs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakeSeparator:
        samplerate = 44100

        def __init__(self, *, model: str, device: str, shifts: int) -> None:
            self.model = model
            self.device = device
            self.shifts = shifts

        def separate_audio_file(self, path: Path):
            stereo = np.vstack([
                np.ones(16, dtype=np.float32),
                np.zeros(16, dtype=np.float32),
            ])
            return path, {name: stereo for name in STEM_NAMES}

    monkeypatch.setattr(source_separator, "_HAS_DEMUCS", True)
    monkeypatch.setattr(source_separator, "_DemucsAPI", FakeSeparator)

    bundle = separate_stems(tmp_path / "fixture.wav", model="htdemucs_ft", device="cpu")

    assert set(bundle.stems) == REQUIRED_STEMS
    assert bundle.sample_rate == 44100
    assert bundle.duration_sec == pytest.approx(16 / 44100, abs=1e-4)
    for stem in bundle.stems.values():
        assert stem.ndim == 1
        assert stem.dtype == np.float32
        np.testing.assert_allclose(stem, np.full(16, 0.5, dtype=np.float32))


def test_api_separation_pins_shifts_to_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """API 経路が `shifts=0` を明示すること（既定 1 = ランダムシフトで stem が揺れる）。

    demucs の既定に戻ると `stem_sha256` が run 毎に変わり、M1-real go-bar 評価器が
    repeats を「別 model stack」として fail-closed する。既定への回帰をここで止める。
    """
    seen: dict[str, int] = {}

    class RecordingSeparator:
        samplerate = 44100

        def __init__(self, *, model: str, device: str, shifts: int) -> None:
            seen["shifts"] = shifts

        def separate_audio_file(self, path: Path):
            mono = np.ones(16, dtype=np.float32)
            return path, {name: mono for name in STEM_NAMES}

    monkeypatch.setattr(source_separator, "_HAS_DEMUCS", True)
    monkeypatch.setattr(source_separator, "_DemucsAPI", RecordingSeparator)

    separate_stems(tmp_path / "fixture.wav", model="htdemucs_ft", device="cpu")

    assert seen["shifts"] == 0
    assert DEFAULT_SHIFTS == 0


def test_cli_separation_pins_shifts_to_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CLI 経路も `--shifts 0` を渡すこと（CLI 既定も 1 なので経路差で再発させない）。"""
    source = tmp_path / "fixture.wav"
    sf.write(source, np.zeros(16, dtype=np.float32), 44100)
    seen: dict[str, list[str]] = {}

    def fake_run(command, *, check, capture_output, text, env):
        seen["command"] = list(command)
        output_dir = Path(command[command.index("-o") + 1])
        stem_dir = output_dir / "htdemucs_ft"
        stem_dir.mkdir(parents=True)
        for stem_name in STEM_NAMES:
            sf.write(stem_dir / f"{stem_name}.wav", np.zeros(16, dtype=np.float32), 44100)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(source_separator, "_HAS_DEMUCS", True)
    monkeypatch.setattr(source_separator, "_DemucsAPI", None)
    monkeypatch.setattr(source_separator, "_DemucsSeparate", object())
    monkeypatch.setattr(source_separator, "_demucs_subprocess_env", lambda: {})
    monkeypatch.setattr(source_separator, "_subprocess_run", fake_run)

    separate_stems(source, model="htdemucs_ft", device="cpu")

    command = seen["command"]
    assert "--shifts" in command
    assert command[command.index("--shifts") + 1] == "0"


def test_real_demucs_separation_is_bitwise_reproducible(tmp_path: Path) -> None:
    """受け入れ条件: 同一入力を 2 回分離して vocals stem の sha256 が一致すること。

    実 demucs（`separate` extra）と事前取得済み重みを要する machine-dependent テスト。
    **決定論の根拠は `shifts=0` であり RNG の seed 固定には依存しない**ので、本テストが
    その不変条件の唯一の回帰ガードになる。`shifts` を既定の 1 に戻すとここが落ちる。

    demucs が入っていても**重みが未取得なら skip** する。nightly の core-extras は
    `lyrics` extra 経由で demucs を入れたうえで slow 含む全件を回すが、重みは
    provisioning しない。import 可否だけで判定すると、そこで `separate_stems` が
    torch hub の実行時ダウンロードに落ちて workflow の install-only/no-inference
    契約を破る。可用性は「未導入 / 取得済 / 導入済だが重み未取得」の 3 値で見る。
    """
    pytest.importorskip("demucs.api")
    try:
        ensure_separation_available()
    except LearnedModelUnavailable as exc:
        pytest.skip(f"demucs weights are not provisioned locally: {exc}")

    sample_rate = 44100
    t = np.arange(sample_rate * 2, dtype=np.float32) / sample_rate
    # 単純なサイン波 + ノイズ。分離品質は問わない（見るのは再現性だけ）。
    rng = np.random.default_rng(0)
    signal = (0.4 * np.sin(2 * np.pi * 220.0 * t) + 0.05 * rng.standard_normal(t.size)).astype(
        np.float32
    )
    source = tmp_path / "fixture.wav"
    sf.write(source, signal, sample_rate)

    def _vocals_digest() -> str:
        bundle = separate_stems(source, model="htdemucs_ft", device="cpu")
        return hashlib.sha256(
            np.asarray(bundle.stems["vocals"], dtype=np.float32).tobytes()
        ).hexdigest()

    assert _vocals_digest() == _vocals_digest()


def test_separate_stems_accepts_single_batch_dim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeSeparator:
        samplerate = 44100

        def __init__(self, *, model: str, device: str, shifts: int = 0) -> None:
            pass

        def separate_audio_file(self, path: Path):
            stereo = np.stack([
                np.ones(16, dtype=np.float32),
                np.zeros(16, dtype=np.float32),
            ])
            return path, {name: stereo[None, :, :] for name in STEM_NAMES}

    monkeypatch.setattr(source_separator, "_HAS_DEMUCS", True)
    monkeypatch.setattr(source_separator, "_DemucsAPI", FakeSeparator)

    bundle = separate_stems(tmp_path / "fixture.wav")

    for stem in bundle.stems.values():
        assert stem.shape == (16,)
        np.testing.assert_allclose(stem, np.full(16, 0.5, dtype=np.float32))


def test_separate_stems_warns_when_samplerate_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeSeparator:
        def __init__(self, *, model: str, device: str, shifts: int = 0) -> None:
            pass

        def separate_audio_file(self, path: Path):
            return path, _stems(n_samples=16)

    monkeypatch.setattr(source_separator, "_HAS_DEMUCS", True)
    monkeypatch.setattr(source_separator, "_DemucsAPI", FakeSeparator)

    with pytest.warns(RuntimeWarning, match="did not expose samplerate"):
        bundle = separate_stems(tmp_path / "fixture.wav")

    assert bundle.sample_rate == source_separator.DEFAULT_SAMPLE_RATE
    assert bundle.duration_sec == pytest.approx(
        16 / source_separator.DEFAULT_SAMPLE_RATE,
        abs=1e-4,
    )


def test_separate_stems_rejects_incomplete_demucs_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeSeparator:
        samplerate = 44100

        def __init__(self, *, model: str, device: str, shifts: int = 0) -> None:
            pass

        def separate_audio_file(self, path: Path):
            return path, {"vocals": np.zeros(16, dtype=np.float32)}

    monkeypatch.setattr(source_separator, "_HAS_DEMUCS", True)
    monkeypatch.setattr(source_separator, "_DemucsAPI", FakeSeparator)

    with pytest.raises(ValueError, match="missing="):
        separate_stems(tmp_path / "fixture.wav")
