"""tests/test_section_features.py — section_features フォールバック可視化テスト。

`_spectral_centroid` / `_onset_density` / `_spectral_flux_mean` / `_chroma_change`
は librosa 呼び出しが例外を投げても 0.0 にフォールバックする。監査指摘: この
フォールバックはクラッシュを正当な測定値 0.0 に偽装するため、logging.warning で
可視化する（値そのものは不変）。
"""
from __future__ import annotations

import logging

import numpy as np
import pytest

from svp_rpe.rpe import section_features as section_features_module
from svp_rpe.rpe.models import SectionMarker


def test_extract_section_features_warns_and_falls_back_to_zero_on_crash(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """librosa 呼び出しが例外を投げても、warning を出しつつ 0.0 を返す。"""
    sr = 22050
    duration_sec = 1.0
    y = np.zeros(int(sr * duration_sec), dtype=np.float32)
    sections = [SectionMarker(label="section_01", start_sec=0.0, end_sec=duration_sec)]

    def _boom(*args, **kwargs):
        raise RuntimeError("forced librosa failure")

    monkeypatch.setattr(section_features_module.librosa.feature, "spectral_centroid", _boom)
    monkeypatch.setattr(section_features_module.librosa.onset, "onset_detect", _boom)
    monkeypatch.setattr(section_features_module.librosa, "stft", _boom)
    monkeypatch.setattr(section_features_module.librosa.feature, "chroma_cqt", _boom)

    with caplog.at_level(logging.WARNING, logger=section_features_module.__name__):
        features = section_features_module.extract_section_features(y, sr, sections)

    assert len(features) == 1
    feature = features[0]
    assert feature.spectral_centroid == 0.0
    assert feature.onset_density == 0.0
    assert feature.spectral_flux_mean == 0.0
    assert feature.chroma_change == 0.0

    warning_funcs = {
        record.getMessage().split(" ", 1)[0] for record in caplog.records
        if record.levelno == logging.WARNING
    }
    assert warning_funcs == {
        "_spectral_centroid",
        "_onset_density",
        "_spectral_flux_mean",
        "_chroma_change",
    }
    assert all("forced librosa failure" in record.getMessage() for record in caplog.records)
