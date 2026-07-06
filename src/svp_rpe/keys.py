"""Key-label comparison: two intentionally-distinct notions, kept separate.

このモジュールは「調 (key) ラベルの一致度」を扱う 2 つの異なる概念を集約する。
**どちらか片方に統合してはならない** — 意味が異なる:

1. **加重一致スコア (``weighted_key_score``)**: mir_eval の music key evaluation
   （近縁調に部分点を与える連続値 [0,1]）。grip 効果量や audit needle など、
   「どれだけ近い調か」を段階的に測りたい場面で使う（例: 完全五度=0.5,
   平行調=0.3）。mir_eval が無い環境では casefold 完全一致のフォールバックに
   落ちる（呼び出し側の要求次第で fail-fast も選べる）。
2. **異名同音の等価判定 (``keys_enharmonically_equal``)**: roundtrip 診断が
   「往復で調が保存されたか」を判定する二値 verdict。C# と Db は異名同音として
   等価に扱うべきだが、加重スコアの近縁調とは無関係の概念（例えば長 3 度違いの
   調は加重スコアでは部分点を持つが、異名同音判定では単純に不一致）。

この 2 つは「grip/audit は段階評価が要る」「roundtrip は二値の往復保存性が
要る」という別々の下流要求に対応しており、統合すると両方の診断の意味が変わる
（`docs/` 上の grip/roundtrip 設計判断を参照）。重複していたのは加重スコア側の
4 コピーのみで、それらは「mir_eval 欠如時にフォールバックするか fail-fast する
か」という 1 軸でしか違わなかったため、ここでパラメータ化して集約した。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WeightedKeyScore:
    """mir_eval 加重 key 一致スコアとその算出センサー。"""

    score: float
    sensor: str


def weighted_key_score(
    target: Any, observed: Any, *, require_mir_eval: bool = False
) -> WeightedKeyScore:
    """mir_eval 加重の key 一致スコア (∈[0,1]) を返す。

    mir_eval が無い環境では、``require_mir_eval=True`` なら fail-fast (``RuntimeError``)
    し、そうでなければ casefold 完全一致のフォールバックへ落ちる
    （``sensor="exact_key_match"``）。
    """
    try:
        import mir_eval.key as mir_eval_key
    except ModuleNotFoundError as exc:
        if require_mir_eval:
            raise RuntimeError(
                "mir_eval is required for weighted key scoring but is not installed. "
                "Install the dev extra first: pip install -e '.[dev]'"
            ) from exc
        score = (
            1.0
            if " ".join(str(target).casefold().split()) == " ".join(str(observed).casefold().split())
            else 0.0
        )
        return WeightedKeyScore(score, sensor="exact_key_match")

    try:
        score = float(mir_eval_key.evaluate(str(target), str(observed))["Weighted Score"])
    except ValueError:
        score = 0.0
    return WeightedKeyScore(score, sensor="mir_eval.key.evaluate")


def keys_enharmonically_equal(expected: Any, observed: Any) -> bool:
    """異名同音の等価性を考慮した key ラベルの二値一致判定。"""

    expected_key = _parse_key_label(expected)
    observed_key = _parse_key_label(observed)
    if expected_key is not None and observed_key is not None:
        return expected_key == observed_key
    return _normalize_label(expected) == _normalize_label(observed)


def _parse_key_label(value: Any) -> tuple[int, str] | None:
    """key ラベルを決定論的 performer の key パーサでパースする。

    ``svp_rpe.perform`` はモジュールロード時に ``svp_rpe.compose`` /
    ``svp_rpe.semantic_ci``（本モジュールの利用側を含む）へ依存するため、
    循環 import を避けて関数内 import に留める。
    """

    if not isinstance(value, str):
        return None

    from svp_rpe.perform import parse_key

    normalized = (
        value.strip()
        .replace("♯", "#")
        .replace("＃", "#")
        .replace("♭", "b")
    )
    try:
        return parse_key(normalized)
    except ValueError:
        return None


def _normalize_label(value: Any) -> str:
    return " ".join(str(value).strip().lower().split())
