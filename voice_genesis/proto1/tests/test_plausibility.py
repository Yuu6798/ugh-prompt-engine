"""test_plausibility.py — F1 plausibility ゲートの受け入れテスト（PR#261 レビュー R38 中心）。"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import genome as g
import plausibility as pl
import probes


def test_plausibility_report_passes_for_default_genome_non_regression():
    """非退行確認: 通常経路（probes.render_probe() は常に notes_midi と同数の
    waveforms を返す）では probe_count_mismatches が空で、従来どおり評価される。
    """
    gen = g.build_genome("plausibility-default")
    report = pl.plausibility_report(gen)
    assert report.probe_count_mismatches == []
    assert report.n_notes > 0
    assert report.passed is True


def test_plausibility_report_fails_closed_when_probe_returns_fewer_waveforms_than_expected(monkeypatch):
    """probes.render_probe() が notes_midi より少ない waveforms を返す退行を注入する
    （旧実装なら zip() が黙って切り詰めていた状況）。R38 により評価前に測定不能
    FAIL になり、欠落した probe のノートは 1 件も notes へ追加されないことを確認する。
    """
    gen = g.build_genome("plausibility-mismatch")
    real_render_probe = probes.render_probe

    def _fake_render_probe(genome, probe_name):
        result = real_render_probe(genome, probe_name)
        if probe_name == "sustain":
            return replace(result, waveforms=result.waveforms[:-1])  # 1 本欠落
        return result

    monkeypatch.setattr(pl.probes, "render_probe", _fake_render_probe)

    report = pl.plausibility_report(gen)
    assert report.probe_count_mismatches == ["sustain"]
    assert report.passed is False
    assert all(n.probe_name != "sustain" for n in report.notes)
    # 他 probe（phrase/cross_range）は不整合が無いため通常どおり評価される。
    assert any(n.probe_name == "phrase" for n in report.notes)


def test_plausibility_report_fails_closed_when_probe_returns_zero_waveforms(monkeypatch):
    """全欠落（waveforms=[]）でも同様に測定不能 FAIL として拒否されることを確認する。"""
    gen = g.build_genome("plausibility-all-missing")
    real_render_probe = probes.render_probe

    def _fake_render_probe(genome, probe_name):
        result = real_render_probe(genome, probe_name)
        if probe_name == "phrase":
            return replace(result, waveforms=[])
        return result

    monkeypatch.setattr(pl.probes, "render_probe", _fake_render_probe)

    report = pl.plausibility_report(gen)
    assert report.probe_count_mismatches == ["phrase"]
    assert report.passed is False
    assert all(n.probe_name != "phrase" for n in report.notes)
