"""test_identity_metrics.py — PR#261 レビュー R21 同型掃討: measure_separation() の
within 閾値計算に Python 組込み max() を使うと、NaN が第 2 引数だと無条件に
無視される順序依存の挙動をする（`max(nan, 5) == nan` だが `max(5, nan) == 5`）。

`measure_separation()` は `render_sakura()`（曲全体の音声合成）を要するため、
`rs.render_sakura` と `normalized_e1_e2`/`cosine_distance` を monkeypatch して
軽量に検証する（実音声合成なしで NaN 伝播ロジックだけを単体テストする）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import identity_metrics as im


def _patch_lightweight(monkeypatch, cosine_sequence):
    """render_sakura/normalized_e1_e2 をダミー化し、cosine_distance の戻り値を
    呼び出し順に `cosine_sequence` から払い出す。"""
    monkeypatch.setattr(im.rs, "render_sakura", lambda genome: genome)
    monkeypatch.setattr(im, "normalized_e1_e2", lambda result, phrase_index: (np.array([1.0, 0.0]), np.array([1.0, 0.0])))
    it = iter(cosine_sequence)
    monkeypatch.setattr(im, "cosine_distance", lambda a, b: next(it))


# measure_separation() 内の cosine_distance 呼び出し順（実装どおり）:
#   between_e1[0], between_e1[1], between_e2[0], between_e2[1],
#   within_x_e1, within_x_e2, within_y_e1, within_y_e2


def test_measure_separation_within_max_propagates_nan_when_second_arg_is_nan(monkeypatch):
    """旧実装 `max(within_x_e1, within_y_e1)` は within_y_e1(第 2 引数)が NaN
    だと無条件にこれを無視し、within_x_e1(有限値)をそのまま返してしまう
    順序依存バグ(PR#261 R21 同型)。修正後は NaN が正しく伝播し、比較を
    経て `all_separated=False` に fail-closed することを確認する。
    """
    sequence = [0.5, 0.5, 0.5, 0.5, 0.1, 0.1, float("nan"), 0.1]
    _patch_lightweight(monkeypatch, sequence)

    report = im.measure_separation("voice_x", object(), "voice_y", object())

    assert np.isnan(report.within_y_e1)  # 前提確認: within_y_e1 自体は NaN のまま保持
    assert report.separation_e1_phrase0 is False  # NaN との比較は常に False = fail-closed
    assert report.separation_e1_phrase1 is False
    assert report.all_separated is False


def test_measure_separation_within_max_propagates_nan_when_first_arg_is_nan(monkeypatch):
    """非退行確認: within_x_e1(第 1 引数)が NaN の場合は旧実装でも
    `max(nan, x) == nan` で正しく伝播していた組み合わせ。修正後も同様に
    fail-closed することを確認する。
    """
    sequence = [0.5, 0.5, 0.5, 0.5, float("nan"), 0.1, 0.1, 0.1]
    _patch_lightweight(monkeypatch, sequence)

    report = im.measure_separation("voice_x", object(), "voice_y", object())

    assert np.isnan(report.within_x_e1)
    assert report.separation_e1_phrase0 is False
    assert report.separation_e1_phrase1 is False
    assert report.all_separated is False


def test_measure_separation_all_separated_true_for_clean_finite_inputs(monkeypatch):
    """非退行確認: 全て有限で between > within が成立する典型ケースは
    従来どおり all_separated=True になる。"""
    # between (e1_p0, e1_p1, e2_p0, e2_p1) はすべて 0.5、within (x_e1, x_e2, y_e1, y_e2)
    # はすべて 0.1 で between > within が全軸で成立する。
    sequence = [0.5, 0.5, 0.5, 0.5, 0.1, 0.1, 0.1, 0.1]
    _patch_lightweight(monkeypatch, sequence)

    report = im.measure_separation("voice_x", object(), "voice_y", object())

    assert report.all_separated is True
