"""rpe/learned/crepe_adapter.py — CREPE 単旋律 F0 抽出アダプタ。

Manual / external integration only（**published extra なし**）。CREPE（Kim et al.
2018）は畳み込み F0 推定器で、フレーム単位に (time, frequency_hz, confidence) を
返す。本アダプタはそれを melody 観測ゲートの共通 F0 トラック
（`melody.observability.MelodyObservation` の frame_* フィールド）へ渡せる素の
tuple 形で返す。決定論 RPE フィールドへは一切書き込まない
（`docs/learned_models_policy.md` Section 2）。使う場合は slow/manual lane で
手動 `pip install crepe` する（下記ライセンス注意）。`_INSTALL_HINT` と同じ方針。

Upstream API note (crepe >= 0.0.12):
    `crepe.predict(audio, sr, viterbi=True, step_size=10, verbose=0)` は
    `(time, frequency, confidence, activation)` を返す。`frequency` は Hz、
    `confidence` は [0, 1] の voicing 信頼度。`viterbi=True` は Viterbi
    デコードで滑らかな系列にする（決定論・乱数なし）。CREPE は内部で 16 kHz へ
    リサンプルするため、入力 sample rate をそのまま渡してよい。

License note（設計 §4 ライセンス方針・Codex 指摘）:
    CREPE の**コード**は MIT だが、事前学習済みモデル重み（model-*.h5、pip wheel
    同梱）の**重みライセンスは別途 inspect して pin していない**。
    `docs/learned_models_policy.md` §4 は「permissive なコードライセンスは permissive
    な重みを含意しない。上流重みはコードと別に inspect し、各 `LearnedModelInfo` は
    具体的な `weights_license` を pin せよ」と要求する。この pin が付くまで、
    installable な `[crepe]` extra を publish すると重み由来が未検証の runtime 依存を
    宣言することになり方針に反する。よって CREPE は Melodia と同様の **MANUAL /
    external 統合**（published extra なし）とし、`weights_license` は「未検証」を
    fail-closed で明示する。重みライセンスの実確認と pin は machine-dependent な
    slow-lane 課題（CLAUDE.md レビュー振り分けの Codex/User 側）であり、確認後に
    `docs/learned_models_policy.md` へ記録した上で extra 昇格を検討する。本アダプタは
    重みを同梱しない。

Determinism: `viterbi=True` の CREPE 推論は RPE の他 learned アダプタと同じく
same-machine 決定論契約（TF カーネル選択でクロスマシンの浮動小数点経路は変わり
うる）。乱数 seed は導入されない。
"""
from __future__ import annotations

import importlib
import importlib.metadata as _pkg_metadata
import sys
from pathlib import Path
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
    "crepe_weight_files",
]

# `crepe.predict` の既定 capacity。重み（model-*.h5）は wheel 同梱で、capacity ごとに
# 別ファイル。実際に読むのは既定の "full" なので、その 1 本を weights pin の対象にする。
_DEFAULT_CAPACITY = "full"

_MODULE_NAME = "crepe"
_MODEL_TASK = "pitch"
_MODEL_PROVIDER = "marl/crepe"
_SOURCE_MODEL = "crepe:predict"
_CODE_LICENSE = "MIT (marl/crepe code; model weights bundled in the pip wheel)"
# fail-closed: 重みライセンスを別途 inspect して pin するまで「未検証」を明示する
# （false permissive を主張しない・policy §4）。実確認後に具体ライセンスへ差し替える。
_WEIGHTS_LICENSE = (
    "UNVERIFIED — bundled model-*.h5 weights license not yet separately inspected "
    "and pinned (docs/learned_models_policy.md §4); CREPE kept as manual integration "
    "until a machine-dependent verification pins it"
)

# NOTE: there is deliberately no published `[crepe]` extra — CREPE's bundled
# weights license is not yet inspected/pinned (see the module docstring's
# license note) and docs/learned_models_policy.md §4 forbids advertising a
# runtime dependency with unverified weights provenance. CREPE is a MANUAL /
# external integration like Melodia: install it by hand in the slow/manual
# lane. This hint therefore points to a direct install, not an extra.
_INSTALL_HINT = (
    "crepe is not installed. CREPE is a manual/external integration only "
    "(no published extra): its bundled model weights license is not yet "
    "separately inspected and pinned (docs/learned_models_policy.md §4). "
    "Install it by hand in the slow/manual lane:\n"
    "    pip install crepe"
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


def crepe_weight_files(capacity: str = _DEFAULT_CAPACITY) -> List[Path]:
    """CREPE が推論で読む重みファイル（`crepe/model-<capacity>.h5`）のパス。

    M1-real の provenance 要求（`extractor_weights_sha256`・#59）を満たすため、
    「同一 package version でも別 bundled weights なら別 run」を content hash で
    区別できるように、実際に読まれる重みファイルを返す。

    Raises
    ------
    LearnedModelUnavailable
        `crepe` 未導入、または同梱重みが見つからないとき（pin できない状態を
        「重みなし」と偽らず fail させ、呼び出し側で emit を落とす）。
    """
    crepe_module = _load_crepe_module()
    module_file = getattr(crepe_module, "__file__", None)
    if not module_file:
        raise LearnedModelUnavailable("crepe package location is unknown; weights を pin できない")
    package_dir = Path(module_file).resolve().parent
    weights = package_dir / f"model-{capacity}.h5"
    if not weights.is_file():
        candidates = sorted(package_dir.glob("model-*.h5"))
        raise LearnedModelUnavailable(
            f"crepe weights {weights} not found (found: {[p.name for p in candidates]}); "
            "重みを pin できないため extractor_weights_sha256 を emit しない"
        )
    return [weights]


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
