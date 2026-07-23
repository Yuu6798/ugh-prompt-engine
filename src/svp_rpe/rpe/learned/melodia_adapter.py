"""rpe/learned/melodia_adapter.py — Essentia PredominantPitchMelodia F0 抽出。

Optional via the `melodia` extra. Melodia（Salamon & Gómez 2012）はポリフォニック
混合音源から支配的旋律 F0 を推定するアルゴリズムで、Essentia の
`PredominantPitchMelodia` として実装されている。フレーム単位に
(pitch_hz, pitchConfidence) を返し、本アダプタはそれを melody 観測ゲートの共通
F0 トラックへ渡せる素の tuple 形で返す。決定論 RPE フィールドへは書き込まない。

⚠ ライセンス注意（設計 §5「Essentia/Melodia はライセンス確認の上 optional」）:
    **Essentia は AGPL-3.0**（コピーレフト）である。本リポジトリの MIT ライセンスと
    両立させるには、Essentia を **標準 install / CI に絶対に含めず**、実推論を回す
    slow/manual lane の環境でのみ opt-in する必要がある。`melodia` extra は既定の
    `dev` にも含めない。この隔離は他の optional 抽出器（MIT/Apache/CC0）より一段
    強い制約であり、`docs/learned_models_policy.md` の許容ライセンス方針の
    例外として明示記録する。Melodia アルゴリズム自体の特許・利用条件も導入時に
    実確認する（本アダプタは重みを持たないルールベース算法だが、Essentia 経由の
    配布ライセンスが AGPL である事実が律速）。

Upstream API note (essentia >= 2.1b6):
    `essentia.standard.PredominantPitchMelodia(sampleRate=44100, ...)` を構築し、
    mono float32 波形（44.1 kHz 前提）を渡すと `(pitch, pitchConfidence)` を返す。
    `pitch` は Hz（無声フレームは 0）、`pitchConfidence` は概ね [0, ~1] の
    salience 値。フレーム hop は既定 128 サンプル。本アダプタは入力を 44.1 kHz へ
    リサンプルしてから渡す（`librosa.resample`・決定論）。
"""
from __future__ import annotations

import importlib
import importlib.metadata as _pkg_metadata
import sys
from typing import Any, List, Optional, Tuple

import numpy as np

from svp_rpe.rpe.learned import LearnedModelIncompatible, LearnedModelUnavailable
from svp_rpe.rpe.models import LearnedModelInfo
from svp_rpe.utils.clamp import clamp

__all__ = [
    "LearnedModelUnavailable",
    "LearnedModelIncompatible",
    "ensure_melodia_available",
    "extract_melodia_f0",
    "melodia_model_info",
]

_MODULE_NAME = "essentia"
_STANDARD_MODULE = "essentia.standard"
_MODEL_TASK = "pitch"
_MODEL_PROVIDER = "MTG/essentia (PredominantPitchMelodia)"
_SOURCE_MODEL = "essentia:PredominantPitchMelodia"
# Essentia expects 44.1 kHz mono for PredominantPitchMelodia's default frame math.
_MELODIA_SAMPLE_RATE = 44100
_MELODIA_HOP_SAMPLES = 128

_CODE_LICENSE = (
    "AGPL-3.0 (Essentia) — copyleft; MUST stay out of standard install / CI, "
    "opt-in only in the slow/manual lane (see module docstring license note)"
)

# NOTE: there is deliberately no published `[melodia]` extra — Essentia is
# AGPL-3.0 and docs/learned_models_policy.md restricts runtime deps to
# permissive licenses (see the module docstring's license note). Melodia is a
# MANUAL / external integration: a user who accepts the AGPL terms installs
# essentia by hand. This hint therefore points to a direct install, not an
# extra, so the advertised dependency surface stays consistent with the policy.
_INSTALL_HINT = (
    "essentia is not installed. Melodia is a manual/external integration only "
    "(no published extra): Essentia is AGPL-3.0 and is excluded from the "
    "packaged extras by docs/learned_models_policy.md. If you accept the AGPL "
    "terms, install it by hand in the slow/manual lane:\n"
    "    pip install essentia"
)


def _load_essentia_standard() -> Any:
    try:
        return importlib.import_module(_STANDARD_MODULE)
    except ImportError as exc:
        raise LearnedModelUnavailable(_INSTALL_HINT) from exc


def ensure_melodia_available() -> None:
    """`essentia.standard` が import 可能かを probe（`LearnedModelUnavailable`）。"""
    _load_essentia_standard()


def _detect_essentia_version() -> Optional[str]:
    root = sys.modules.get(_MODULE_NAME)
    if root is not None:
        candidate = getattr(root, "__version__", None)
        if isinstance(candidate, str) and candidate:
            return candidate
    try:
        return _pkg_metadata.version(_MODULE_NAME)
    except _pkg_metadata.PackageNotFoundError:
        return None


def melodia_model_info() -> LearnedModelInfo:
    """`LearnedAudioAnnotations.enabled_models` へ添える provenance 記録。"""
    return LearnedModelInfo(
        name=_MODULE_NAME,
        version=_detect_essentia_version(),
        provider=_MODEL_PROVIDER,
        task=_MODEL_TASK,
        license=_CODE_LICENSE,
        weights_license=None,  # ルールベース算法（学習重みなし）。
    )


def extract_melodia_f0(
    y: np.ndarray,
    sr: int,
) -> Tuple[List[float], List[float], List[float], str]:
    """Melodia で支配的旋律 F0 を採り (times, hz, confidence, source_model)。

    入力は 44.1 kHz mono へリサンプルしてから Essentia に渡す。``confidence`` は
    Essentia の pitchConfidence を [0, 1] へクランプしたもの（salience は稀に 1 を
    超えるため）。``hz`` は無声フレームで 0.0。

    Raises
    ------
    LearnedModelUnavailable
        `essentia` 未導入のとき。
    LearnedModelIncompatible
        `PredominantPitchMelodia` が見つからない / 想定外の戻り形のとき。
    """
    standard = _load_essentia_standard()
    algo_cls = getattr(standard, "PredominantPitchMelodia", None)
    if algo_cls is None:
        raise LearnedModelIncompatible(
            "essentia.standard.PredominantPitchMelodia not found; "
            "incompatible essentia version"
        )

    import librosa

    y = np.asarray(y, dtype=np.float32)
    if sr != _MELODIA_SAMPLE_RATE and y.size:
        y = np.asarray(
            librosa.resample(y, orig_sr=sr, target_sr=_MELODIA_SAMPLE_RATE),
            dtype=np.float32,
        )

    algo = algo_cls(sampleRate=_MELODIA_SAMPLE_RATE)
    result = algo(y)
    if not isinstance(result, (tuple, list)) or len(result) < 2:
        raise LearnedModelIncompatible(
            f"PredominantPitchMelodia returned unexpected shape: {type(result).__name__}"
        )
    pitch, confidence = np.asarray(result[0], dtype=float), np.asarray(result[1], dtype=float)
    if pitch.shape != confidence.shape:
        raise LearnedModelIncompatible(
            "PredominantPitchMelodia returned mismatched pitch/confidence lengths"
        )

    hop_sec = _MELODIA_HOP_SAMPLES / _MELODIA_SAMPLE_RATE
    times = [round(i * hop_sec, 4) for i in range(len(pitch))]
    freqs = [round(float(max(p, 0.0)), 3) for p in pitch]
    confs = [round(clamp(float(c)), 4) for c in confidence]
    return times, freqs, confs, _SOURCE_MODEL
