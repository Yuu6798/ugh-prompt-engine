"""rpe/learned/crepe_adapter.py — CREPE 単旋律 F0 抽出アダプタ。

Optional via the `crepe` extra. CREPE（Kim et al. 2018, MIT ライセンス）は
畳み込み F0 推定器で、フレーム単位に (time, frequency_hz, confidence) を返す。
本アダプタはそれを melody 観測ゲートの共通 F0 トラック
（`melody.observability.MelodyObservation` の frame_* フィールド）へ渡せる素の
tuple 形で返す。決定論 RPE フィールドへは一切書き込まない
（`docs/learned_models_policy.md` Section 2）。

Upstream API note (crepe >= 0.0.12):
    `crepe.predict(audio, sr, viterbi=True, step_size=10, verbose=0)` は
    `(time, frequency, confidence, activation)` を返す。`frequency` は Hz、
    `confidence` は [0, 1] の voicing 信頼度。`viterbi=True` は Viterbi
    デコードで滑らかな系列にする（決定論・乱数なし）。CREPE は内部で 16 kHz へ
    リサンプルするため、入力 sample rate をそのまま渡してよい。

License note:
    CREPE のコードは MIT。事前学習済みモデル重みは pip パッケージに同梱され
    （`crepe` の PyPI wheel が model-*.h5 を含む）、上流 README に基づき研究利用
    可能。重みの由来検証は導入時（実推論を回す環境）に行う slow-lane の課題で
    あり、本アダプタは重みを同梱しない。

Determinism: `viterbi=True` の CREPE 推論は RPE の他 learned アダプタと同じく
same-machine 決定論契約（TF カーネル選択でクロスマシンの浮動小数点経路は変わり
うる）。乱数 seed は導入されない。
"""
from __future__ import annotations

import importlib
import importlib.metadata as _pkg_metadata
import sys
from typing import Any, List, Optional, Tuple

import numpy as np

from svp_rpe.rpe.learned import LearnedModelIncompatible, LearnedModelUnavailable
from svp_rpe.rpe.models import LearnedModelInfo

__all__ = [
    "LearnedModelUnavailable",
    "LearnedModelIncompatible",
    "ensure_crepe_available",
    "extract_crepe_f0",
    "crepe_model_info",
]

_MODULE_NAME = "crepe"
_MODEL_TASK = "pitch"
_MODEL_PROVIDER = "marl/crepe"
_SOURCE_MODEL = "crepe:predict"
_CODE_LICENSE = "MIT (marl/crepe code; model weights bundled in the pip wheel)"
_WEIGHTS_LICENSE = "bundled with the crepe pip wheel; provenance verified at adoption time"

_INSTALL_HINT = (
    "crepe is not installed. Install it via the optional `crepe` extra:\n"
    '    pip install -e ".[crepe]"'
)


def _load_crepe_module() -> Any:
    try:
        return importlib.import_module(_MODULE_NAME)
    except ImportError as exc:
        raise LearnedModelUnavailable(_INSTALL_HINT) from exc


def ensure_crepe_available() -> None:
    """`crepe` が import 可能かを probe（`LearnedModelUnavailable` で fail）。

    モデル構築も推論もしない — 依存欠落を早期に fail させるだけ。
    """
    _load_crepe_module()


def _detect_crepe_version() -> Optional[str]:
    root = sys.modules.get(_MODULE_NAME)
    if root is not None:
        candidate = getattr(root, "__version__", None)
        if isinstance(candidate, str) and candidate:
            return candidate
    try:
        return _pkg_metadata.version(_MODULE_NAME)
    except _pkg_metadata.PackageNotFoundError:
        return None


def crepe_model_info() -> LearnedModelInfo:
    """`LearnedAudioAnnotations.enabled_models` へ添える provenance 記録。"""
    return LearnedModelInfo(
        name=_MODULE_NAME,
        version=_detect_crepe_version(),
        provider=_MODEL_PROVIDER,
        task=_MODEL_TASK,
        license=_CODE_LICENSE,
        weights_license=_WEIGHTS_LICENSE,
    )


def extract_crepe_f0(
    y: np.ndarray,
    sr: int,
    *,
    step_size_ms: int = 10,
    viterbi: bool = True,
) -> Tuple[List[float], List[float], List[float], str]:
    """CREPE で F0 トラックを採り (times, hz, confidence, source_model) を返す。

    返り値の 3 配列は同じ長さ。``hz`` は Hz（無声推定でも CREPE は最尤 F0 を返す
    ため 0 埋めはしない — 有声判定は confidence 閾値で観測ゲート側が行う）。

    Raises
    ------
    LearnedModelUnavailable
        `crepe` 未導入のとき。
    LearnedModelIncompatible
        `crepe.predict` が見つからない / 想定外の戻り形のとき。
    """
    crepe_module = _load_crepe_module()
    predict_fn = getattr(crepe_module, "predict", None)
    if predict_fn is None:
        raise LearnedModelIncompatible(
            "crepe.predict not found; incompatible crepe version"
        )

    y = np.asarray(y, dtype=np.float32)
    result = predict_fn(y, sr, viterbi=viterbi, step_size=step_size_ms, verbose=0)
    if not isinstance(result, (tuple, list)) or len(result) < 3:
        raise LearnedModelIncompatible(
            f"crepe.predict returned unexpected shape: {type(result).__name__}"
        )
    time, frequency, confidence = result[0], result[1], result[2]
    times = [round(float(t), 4) for t in np.asarray(time, dtype=float)]
    freqs = [round(float(f), 3) for f in np.asarray(frequency, dtype=float)]
    confs = [round(float(c), 4) for c in np.asarray(confidence, dtype=float)]
    if not (len(times) == len(freqs) == len(confs)):
        raise LearnedModelIncompatible(
            "crepe.predict returned mismatched time/frequency/confidence lengths"
        )
    return times, freqs, confs, _SOURCE_MODEL
