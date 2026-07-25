"""Optional Demucs source separation adapter.

This module intentionally keeps Demucs behind an optional import so the normal
package install and CI path do not require torch/Demucs.
"""
from __future__ import annotations

import contextlib
import os
import random
import shutil
import subprocess
import sys
import tempfile
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from pydantic import BaseModel, ConfigDict, field_validator

try:
    from demucs.api import Separator as _DemucsAPI  # pragma: no cover - optional dependency
except ImportError:
    _DemucsAPI = None

try:
    import demucs.separate as _DemucsSeparate  # pragma: no cover - optional dependency
except ImportError:
    _DemucsSeparate = None

_HAS_DEMUCS = _DemucsAPI is not None or _DemucsSeparate is not None

# Module-local indirection so tests can stub out the Demucs CLI invocation
# without monkeypatching the process-wide `subprocess.run` (which collides
# with numpy's lazy `numpy.testing` import doing its own `lscpu` subprocess
# call — see tests/test_source_separator.py).
_subprocess_run = subprocess.run

DEFAULT_MODEL = "htdemucs_ft"
DEFAULT_SAMPLE_RATE = 44100
STEM_NAMES = ("vocals", "drums", "bass", "other")
REQUIRED_STEMS = frozenset(STEM_NAMES)

# Demucs の "random shift trick"（randomized equivariant stabilization）を無効化する。
#
# `demucs.apply.apply_model` は `shifts > 0` のとき stdlib の `random.randint(0, max_shift)`
# で mix を 0–0.5 秒のランダム量だけ時間シフトし、逆シフトした出力を `shifts` 回平均する
# （demucs 4.1.0 `apply.py`）。API (`demucs.api.Separator`) と CLI (`python -m demucs`) の
# **既定はどちらも 1** なので、既定のままでは同一入力を 2 回分離しただけで stem の bytes が
# 変わる。M1-real の go-bar 評価器は vocals stem の hash (`stem_sha256`) を repeats の
# 同一性署名 (`_route_provenance`) に含めるため、この非決定性は「別 model stack の run」として
# fail-closed を招く（実測 2026-07-25: demucs を通る 15 経路すべてが `stem_sha256` のみ不一致、
# 分離を通らない経路は完全一致）。
#
# `shifts=0` はシフト分岐自体をスキップさせるので、乱数を一切引かずに決定論化できる。
# 平均を取らなくなる分だけ分離品質は理論上わずかに落ちるが、計器としての再現性を優先する
# （観測値が run 毎に変わる計器は pin できない）。
DEFAULT_SHIFTS = 0

# 決定論化の防御的措置として分離中だけ固定 seed を敷く際の値。`DEFAULT_SHIFTS = 0` で
# 既知の乱数消費は無くなるが、上流実装が将来別の乱数を引いても stem が揺れないようにする。
SEPARATION_RNG_SEED = 0


@contextlib.contextmanager
def _deterministic_rng(device: str = "cpu") -> Iterator[None]:
    """分離の間だけ stdlib `random` と torch の RNG を固定 seed に据える。

    プロセス全体の RNG を書き換えたまま帰ると呼び出し側の乱数列を壊すので、
    前の state を退避して `finally` で必ず復元する（グローバル副作用を残さない）。
    torch は optional 依存なので、入っていなければ stdlib のみを固定する。

    torch 側は **`device` が指す backend だけ**を対象にする。`torch.manual_seed` は CPU に
    加えて CUDA / MPS / XPU 等の全 accelerator generator も seed するため、これを使うと
    分離が触ってもいない backend の乱数列を呼び出し側から奪う。よって CPU は
    `torch.default_generator`（CPU 専用）で seed し、accelerator を要求されたときだけ
    その backend の `manual_seed` を呼ぶ。

    退避・復元は `torch.random.fork_rng` に委ねる（CPU state は常に fork される）。
    `devices` は必ず明示する: `None` 既定だと **全デバイスを列挙・初期化**するため、
    `device="cpu"` の分離でも CUDA context が作られ、起動コスト増や
    「使っていない GPU が触れず失敗」を招く。CPU 分離では `devices=[]` を渡して
    accelerator に一切触らない。
    """
    random_state = random.getstate()
    random.seed(SEPARATION_RNG_SEED)
    try:
        import torch  # pragma: no cover - torch は optional
    except ImportError:
        torch = None  # type: ignore[assignment]
    try:
        with contextlib.ExitStack() as stack:
            if torch is not None:  # pragma: no cover - torch 導入環境のみ
                torch_device = torch.device(device)
                if torch_device.type == "cpu":
                    # accelerator を列挙も初期化もしない（CPU state は常に fork される）。
                    stack.enter_context(torch.random.fork_rng(devices=[]))
                else:
                    index = 0 if torch_device.index is None else torch_device.index
                    stack.enter_context(
                        torch.random.fork_rng(
                            devices=[index], device_type=torch_device.type
                        )
                    )
                # CPU generator のみを seed（torch.manual_seed だと全 backend に及ぶ）。
                torch.default_generator.manual_seed(SEPARATION_RNG_SEED)
                if torch_device.type != "cpu":
                    backend = getattr(torch, torch_device.type, None)
                    if backend is not None and hasattr(backend, "manual_seed"):
                        # `torch.cuda.manual_seed` 等は **current device** だけを seed する。
                        # `cuda:1` を要求されたのに current が 0 のままだと、fork した
                        # device 1 は seed されず、fork していない device 0 が書き換わって
                        # 復元されない（要求 device の外に副作用を出す）。要求 index の
                        # device context に入ってから seed する。
                        device_ctx = getattr(backend, "device", None)
                        if callable(device_ctx):
                            with device_ctx(index):
                                backend.manual_seed(SEPARATION_RNG_SEED)
                        else:
                            # index を持たない backend（例: mps）は current 概念が無い。
                            backend.manual_seed(SEPARATION_RNG_SEED)
            yield
    finally:
        random.setstate(random_state)


class SeparatorNotAvailableError(RuntimeError):
    """Raised when source separation is requested without Demucs installed."""


class StemBundle(BaseModel):
    """Separated mono stems returned by the source-separation adapter."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source_path: str
    model_name: str
    sample_rate: int
    duration_sec: float
    stems: dict[str, np.ndarray]

    @field_validator("sample_rate")
    @classmethod
    def sample_rate_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("sample_rate must be positive")
        return value

    @field_validator("duration_sec")
    @classmethod
    def duration_non_negative(cls, value: float) -> float:
        if value < 0.0:
            raise ValueError("duration_sec must be non-negative")
        return value

    @field_validator("stems")
    @classmethod
    def stems_are_complete_mono_float32(
        cls,
        value: dict[str, np.ndarray],
    ) -> dict[str, np.ndarray]:
        keys = frozenset(value)
        if not REQUIRED_STEMS.issubset(keys):
            missing = sorted(REQUIRED_STEMS - keys)
            raise ValueError(f"stems must contain at least {sorted(REQUIRED_STEMS)}; "
                             f"missing={missing}")

        for name, stem in value.items():
            if not isinstance(stem, np.ndarray):
                raise ValueError(f"stem {name!r} must be a numpy array")
            if stem.ndim != 1:
                raise ValueError(f"stem {name!r} must be mono 1D")
            if stem.dtype != np.float32:
                raise ValueError(f"stem {name!r} must have dtype float32")
        return value


def _as_numpy(value: Any) -> np.ndarray:
    """Convert a Demucs tensor-like value to a numpy array."""
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        return value.numpy()
    return np.asarray(value)


def _to_mono_float32(value: Any) -> np.ndarray:
    arr = _as_numpy(value)
    while arr.ndim > 2 and arr.shape[0] == 1:
        arr = arr[0]
    if arr.ndim == 2:
        arr = np.mean(arr, axis=0)
    elif arr.ndim != 1:
        raise ValueError(f"expected stem tensor with 1D or 2D shape, got {arr.shape}")
    return np.asarray(arr, dtype=np.float32)


def _audio_file_to_mono_float32(path: Path) -> tuple[np.ndarray, int]:
    arr, sample_rate = sf.read(path, always_2d=False, dtype="float32")
    if arr.ndim == 2:
        arr = np.mean(arr, axis=1)
    elif arr.ndim != 1:
        raise ValueError(f"expected audio file with 1D or 2D shape, got {arr.shape}")
    return np.asarray(arr, dtype=np.float32), int(sample_rate)


def _get_demucs_separator_class() -> type[Any]:
    if not _HAS_DEMUCS or _DemucsAPI is None:
        raise SeparatorNotAvailableError(
            "demucs is not installed. Install the optional extra with: "
            "pip install 'svp-rpe[separate]'"
        )
    return _DemucsAPI


def _separator_sample_rate(separator: Any) -> int:
    sample_rate = getattr(separator, "samplerate", None)
    if sample_rate is None:
        warnings.warn(
            "Demucs separator did not expose samplerate; using 44100 Hz.",
            RuntimeWarning,
            stacklevel=2,
        )
        return DEFAULT_SAMPLE_RATE
    sample_rate = int(sample_rate)
    if sample_rate <= 0:
        raise ValueError("Demucs samplerate must be positive")
    return sample_rate


def _separate_stems_with_api(
    source_path: Path,
    *,
    model: str,
    device: str,
) -> StemBundle:
    separator_cls = _get_demucs_separator_class()
    # shifts は明示する（既定 1 = ランダムシフト平均で stem が run 毎に変わる）。
    separator = separator_cls(model=model, device=device, shifts=DEFAULT_SHIFTS)
    with _deterministic_rng(device):
        _, separated = separator.separate_audio_file(source_path)

    if not isinstance(separated, dict):
        raise ValueError("Demucs separator returned an invalid stem mapping")

    stems = {name: _to_mono_float32(stem) for name, stem in separated.items()}
    sample_rate = _separator_sample_rate(separator)
    n_samples = max((len(stem) for stem in stems.values()), default=0)

    return StemBundle(
        source_path=str(source_path),
        model_name=model,
        sample_rate=sample_rate,
        duration_sec=round(n_samples / sample_rate, 4),
        stems=stems,
    )


def _find_cli_stem_file(output_dir: Path, stem_name: str) -> Path:
    matches = sorted(output_dir.rglob(f"{stem_name}.wav"))
    if not matches:
        raise ValueError(f"Demucs CLI did not produce {stem_name!r} stem")
    return matches[0]


def _demucs_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    path = env.get("PATH")
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool, path=path) is None]
    if missing:
        raise SeparatorNotAvailableError(
            "Demucs CLI requires FFmpeg and FFprobe on PATH. Install a full/shared "
            f"FFmpeg build and restart the shell. Missing: {', '.join(missing)}"
        )
    return env


def _separate_stems_with_cli(
    source_path: Path,
    *,
    model: str,
    device: str,
) -> StemBundle:
    if not _HAS_DEMUCS or _DemucsSeparate is None:
        raise SeparatorNotAvailableError(
            "demucs is not installed. Install the optional extra with: "
            "pip install 'svp-rpe[separate]'"
        )

    with tempfile.TemporaryDirectory(prefix="svp-rpe-demucs-") as tmp:
        output_dir = Path(tmp)
        command = [
            sys.executable,
            "-m",
            "demucs",
            "-n",
            model,
            "-d",
            device,
            # API 経路と同値に固定する（CLI 既定も 1 なので、片方だけ直すと経路差で再発する）。
            "--shifts",
            str(DEFAULT_SHIFTS),
            "--float32",
            "--filename",
            "{stem}.{ext}",
            "-o",
            str(output_dir),
            str(source_path),
        ]
        env = _demucs_subprocess_env()
        try:
            _subprocess_run(
                command,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
        except subprocess.CalledProcessError as exc:
            details = (exc.stderr or exc.stdout or "").strip()
            message = "Demucs CLI separation failed"
            if details:
                message = f"{message}: {details}"
            raise RuntimeError(message) from exc

        stems: dict[str, np.ndarray] = {}
        sample_rates: set[int] = set()
        for stem_name in STEM_NAMES:
            stem_path = _find_cli_stem_file(output_dir, stem_name)
            stem, sample_rate = _audio_file_to_mono_float32(stem_path)
            stems[stem_name] = stem
            sample_rates.add(sample_rate)

        if len(sample_rates) != 1:
            raise ValueError(f"Demucs CLI produced inconsistent sample rates: {sample_rates}")
        sample_rate = sample_rates.pop()
        n_samples = max((len(stem) for stem in stems.values()), default=0)

        return StemBundle(
            source_path=str(source_path),
            model_name=model,
            sample_rate=sample_rate,
            duration_sec=round(n_samples / sample_rate, 4),
            stems=stems,
        )


def separate_stems(
    path: str | Path,
    *,
    model: str = DEFAULT_MODEL,
    device: str = "cpu",
) -> StemBundle:
    """Separate an audio file into vocals, drums, bass, and other stems."""
    source_path = Path(path)
    if _DemucsAPI is not None:
        return _separate_stems_with_api(source_path, model=model, device=device)
    return _separate_stems_with_cli(source_path, model=model, device=device)
