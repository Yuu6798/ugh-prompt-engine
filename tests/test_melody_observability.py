"""tests/test_melody_observability.py — melody 観測センサー M0/M1 のテスト。

CI 安全（重依存なし）で回る層:
- ゲート指標ロジック（`assess_observability` / `notes_from_frames` /
  `cross_extractor_agreement`）の単体検証。
- routing（`select_routes`）の経路列挙。
- optional 抽出器アダプタが未導入時に `LearnedModelUnavailable` で優雅に落ちる。
- M0 レジストリ / 合成仕様のロードと事前登録閾値の整合。

`slow` マーカー: 合成 → 実 pyin 抽出を回す統合テスト（正の対照 = sufficient /
負の対照 = insufficient）。CI は全件実行、日常は `pytest -m "not slow"` で除外。
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
import yaml

import scripts.build_melody_bench as bench
from svp_rpe.melody.observability import (
    MelodyNote,
    MelodyObservation,
    ObservabilityThresholds,
    assess_observability,
    cross_extractor_agreement,
    notes_from_frames,
)
from svp_rpe.melody.routing import INPUT_KINDS, select_routes
from svp_rpe.rpe.learned import LearnedModelUnavailable

ROOT = Path(__file__).resolve().parents[1]
BENCH_DIR = ROOT / "tests" / "fixtures" / "melody_bench"
REGISTRY_PATH = BENCH_DIR / "registry.yaml"
SPECS_PATH = BENCH_DIR / "synthesis_specs.yaml"


# --------------------------------------------------------------------------- #
# ヘルパー
# --------------------------------------------------------------------------- #
def _default_thresholds() -> ObservabilityThresholds:
    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    return ObservabilityThresholds.from_registry(registry["observation_gate"])


def _frame_track_from_midi(
    midis, *, frames_per_note=6, hop_sec=0.05, confidence=0.9
):
    """MIDI 系列を等長フレームの F0 トラックへ展開する（ノート導出のテスト入力）。"""
    times, hz, conf = [], [], []
    t = 0.0
    for midi in midis:
        freq = 440.0 * 2 ** ((midi - 69) / 12) if midi is not None else 0.0
        for _ in range(frames_per_note):
            times.append(round(t, 4))
            hz.append(freq)
            conf.append(confidence if midi is not None else 0.0)
            t += hop_sec
    return times, hz, conf


# --------------------------------------------------------------------------- #
# ゲート指標ロジック（CI 安全）
# --------------------------------------------------------------------------- #
def test_thresholds_from_registry_roundtrip():
    th = _default_thresholds()
    assert th.min_note_count == 8
    assert th.min_phrase_count == 2
    assert th.voiced_confidence_floor == pytest.approx(0.30)


def test_thresholds_reject_unknown_keys():
    with pytest.raises(ValueError, match="unknown observation_gate keys"):
        ObservabilityThresholds.from_registry(
            {"min_note_count": 8, "bogus_key": 1}
        )


def test_notes_from_frames_derives_notes_from_clean_track():
    th = _default_thresholds()
    midis = [60, 62, 64, 65, 67, 69]
    times, hz, conf = _frame_track_from_midi(midis)
    notes = notes_from_frames(times, hz, conf, th)
    assert len(notes) == len(midis)
    assert [round(n.pitch_midi) for n in notes] == midis


def test_chord_pad_track_falls_insufficient():
    """持続単音（和音パッドが pyin で縮退した姿）は insufficient に落ちる。"""
    th = _default_thresholds()
    # 1 音を長く持続 = ノート 1 個（Phase 0 の pyin 縮退の再現）。
    times, hz, conf = _frame_track_from_midi([60], frames_per_note=80)
    obs = MelodyObservation(
        route="pyin_direct",
        source_model="test",
        frame_times=tuple(times),
        frame_hz=tuple(hz),
        frame_confidence=tuple(conf),
    )
    report = assess_observability(obs, th)
    assert report.status == "insufficient"
    assert report.note_count == 1
    assert any("note_count" in reason for reason in report.reasons)


def test_clean_phrased_track_is_sufficient():
    th = _default_thresholds()
    # 2 フレーズ・12 音（フレーズ間に無音フレームを挟む）。
    phrase = [60, 62, 64, 65, 67, 69]
    silence = [None] * 20  # phrase_gap_sec(0.6) を超える無音（hop 0.05*20=1.0s）
    midis = phrase + silence + [67, 65, 64, 62, 60, 59]
    times, hz, conf = _frame_track_from_midi(midis)
    obs = MelodyObservation(
        route="pyin_direct",
        source_model="test",
        frame_times=tuple(times),
        frame_hz=tuple(hz),
        frame_confidence=tuple(conf),
    )
    report = assess_observability(obs, th)
    assert report.status == "sufficient", report.reasons
    assert report.note_count >= th.min_note_count
    assert report.phrase_count >= th.min_phrase_count


def test_octave_jump_rate_flags_octave_errors():
    th = _default_thresholds()
    # 交互にオクターブ跳躍する系列 → octave_jump_rate 高。
    midis = [60, 72, 60, 72, 60, 72, 60, 72, 60, 72]
    times, hz, conf = _frame_track_from_midi(midis)
    obs = MelodyObservation(
        route="crepe_direct",
        source_model="test",
        frame_times=tuple(times),
        frame_hz=tuple(hz),
        frame_confidence=tuple(conf),
    )
    report = assess_observability(obs, th)
    assert report.octave_jump_rate > th.max_octave_jump_rate
    assert any("octave_jump_rate" in reason for reason in report.reasons)


def test_low_confidence_track_falls_insufficient():
    th = _default_thresholds()
    midis = [60, 62, 64, 65, 67, 69, 71, 72, 74]
    times, hz, conf = _frame_track_from_midi(midis, confidence=0.35)
    # confidence 0.35: voiced(>=0.30) だが low_confidence_floor(0.50) 未満 → 低信頼率 1.0
    obs = MelodyObservation(
        route="pyin_direct",
        source_model="test",
        frame_times=tuple(times),
        frame_hz=tuple(hz),
        frame_confidence=tuple(conf),
    )
    report = assess_observability(obs, th)
    assert report.status == "insufficient"
    assert any("low_confidence_rate" in reason for reason in report.reasons)


def test_note_level_extractor_uses_note_confidence():
    """ノート系抽出器（フレーム無し）は confidence をノートから採る。"""
    th = _default_thresholds()
    notes = tuple(
        MelodyNote(start_sec=i * 0.5, end_sec=i * 0.5 + 0.4, pitch_midi=60 + i, confidence=0.8)
        for i in range(10)
    )
    obs = MelodyObservation(route="basic_pitch_direct", source_model="test", notes=notes)
    report = assess_observability(obs, th)
    assert report.note_count == 10
    assert report.confidence_mean == pytest.approx(0.8)


def test_note_only_observation_gets_nonzero_coverage_and_can_be_sufficient():
    """note-only 表現（basic-pitch）が被覆 0.0 で一律 insufficient にならない（回帰）。

    フレームを持たないノート系抽出器でも、ノート区間合併長 / 総尺で被覆を代用し、
    密なノート列は sufficient になれる（full_mix の basic_pitch 経路が構造的に
    永久 insufficient になる不具合の回帰テスト）。
    """
    th = _default_thresholds()
    notes = []
    # 2 フレーズ・各 6 音（フレーズ間に 0.8s ギャップ）。
    for phrase_start in (0.0, 4.0):
        for i in range(6):
            start = phrase_start + i * 0.5
            notes.append(MelodyNote(start_sec=start, end_sec=start + 0.45, pitch_midi=60 + i, confidence=0.85))
    obs = MelodyObservation(
        route="basic_pitch_direct",
        source_model="test",
        notes=tuple(notes),
        total_duration_sec=max(n.end_sec for n in notes),
    )
    report = assess_observability(obs, th)
    assert report.voiced_coverage > 0.0
    assert report.status == "sufficient", report.reasons


def test_agreement_fails_closed_when_unavailable():
    """min_cross_extractor_agreement 設定時、agreement が採れなければ insufficient。"""
    from dataclasses import replace

    base = _default_thresholds()
    th = replace(base, min_cross_extractor_agreement=0.5)
    # 十分に密で他条件は満たすフレーム系観測（agreement は reference なしで None）。
    midis = [60, 62, 64, 65, 67, 69, 71, 72] + [None] * 20 + [67, 65, 64, 62, 60, 59]
    times, hz, conf = _frame_track_from_midi(midis)
    obs = MelodyObservation(
        route="basic_pitch_direct",
        source_model="test",
        frame_times=tuple(times),
        frame_hz=tuple(hz),
        frame_confidence=tuple(conf),
    )
    report = assess_observability(obs, th)  # reference_notes 無し → agreement None
    assert report.status == "insufficient"
    assert any("cross_extractor_agreement unavailable" in r for r in report.reasons)


def test_cross_extractor_agreement_identical_and_disjoint():
    a = [MelodyNote(0, 1, 60, 0.9), MelodyNote(1, 2, 62, 0.9), MelodyNote(2, 3, 64, 0.9)]
    b_same = list(a)
    b_diff = [MelodyNote(0, 1, 48, 0.9), MelodyNote(1, 2, 50, 0.9)]
    assert cross_extractor_agreement(a, b_same) == pytest.approx(1.0)
    assert cross_extractor_agreement(a, b_diff) == pytest.approx(0.0)
    assert cross_extractor_agreement(a, []) is None


def test_observation_rejects_mismatched_frame_lengths():
    with pytest.raises(ValueError, match="same length"):
        MelodyObservation(
            route="r",
            source_model="m",
            frame_times=(0.0, 0.1),
            frame_hz=(440.0,),
            frame_confidence=(0.9, 0.9),
        )


# --------------------------------------------------------------------------- #
# routing（CI 安全）
# --------------------------------------------------------------------------- #
def test_select_routes_covers_all_input_kinds():
    for kind in INPUT_KINDS:
        routes = select_routes(kind)
        assert routes, kind


def test_chord_pad_routing_short_circuits_to_not_observed():
    routes = select_routes("chord_pad_no_melody")
    assert len(routes) == 1
    assert routes[0].applies is False
    assert routes[0].extractor == "none"


def test_vocal_track_routes_include_demucs_and_crepe():
    names = {r.name for r in select_routes("vocal_track")}
    assert "demucs_vocals_then_crepe" in names
    assert "demucs_vocals_then_melodia" in names
    assert any(r.requires_separation for r in select_routes("vocal_track"))


def test_select_routes_unknown_kind_raises():
    with pytest.raises(ValueError, match="unknown input_kind"):
        select_routes("nope")


# --------------------------------------------------------------------------- #
# optional 抽出器アダプタ（未導入時に優雅に落ちる・CI 安全）
# --------------------------------------------------------------------------- #
def test_adapters_raise_learned_unavailable_when_missing():
    pytest.importorskip  # keep import local below
    from svp_rpe.rpe.learned import crepe_adapter, melodia_adapter, source_separation_adapter

    probes = [
        crepe_adapter.ensure_crepe_available,
        melodia_adapter.ensure_melodia_available,
        source_separation_adapter.ensure_separation_available,
    ]
    for probe in probes:
        try:
            probe()
        except LearnedModelUnavailable:
            continue
        except Exception as exc:  # pragma: no cover - only if the dep IS installed
            pytest.skip(f"{probe.__name__} raised {type(exc).__name__}; dep likely installed")
        # 到達 = 依存が導入済み（本環境では想定外だが skip）。
        pytest.skip(f"{probe.__name__} did not raise; optional dep installed")


def test_observe_via_route_propagates_unavailable_for_demucs(tmp_path):
    """Demucs 経路は demucs 未導入なら LearnedModelUnavailable を伝播する。"""
    from svp_rpe.melody.extractors import observe_via_route
    from svp_rpe.melody.routing import MelodyRoute

    wav = tmp_path / "x.wav"
    sf.write(wav, np.zeros(2048, dtype=np.float32), 22050, subtype="FLOAT")
    route = MelodyRoute("demucs_vocals_then_crepe", "demucs_vocals", "crepe")
    try:
        observe_via_route(str(wav), route)
    except LearnedModelUnavailable:
        return
    pytest.skip("demucs installed; separation path did not raise")


# --------------------------------------------------------------------------- #
# M0 レジストリ / 合成仕様（CI 安全）
# --------------------------------------------------------------------------- #
def test_registry_and_specs_are_consistent():
    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    specs = yaml.safe_load(SPECS_PATH.read_text(encoding="utf-8"))
    spec_ids = set(specs["fixtures"])
    for fixture in registry["fixtures"]:
        # 各 committed fixture の spec 参照が実在すること。
        ref = fixture["spec"].split("#", 1)[1]
        assert ref in spec_ids, ref
    # splits の全 id が committed fixture に含まれること。
    committed = {f["id"] for f in registry["fixtures"]}
    for split_ids in registry["splits"].values():
        assert set(split_ids).issubset(committed)


def test_synthesis_specs_provenance_pin():
    """synthesis_specs.yaml の完全 digest pin（波形の pin）。

    registry.yaml の `provenance.synthesis_specs_sha256` に固定した digest と実
    ファイルの sha256 の**完全一致**を機械検証する。仕様を変えると digest が変わり
    このテストが赤くなるので、無言の stale（数値だけ据え置き）が起きず、pin 更新 +
    dated 再実測が強制される（設計 §5 事前登録厳守 / AGENTS §8 provenance）。
    """
    actual = hashlib.sha256(SPECS_PATH.read_bytes()).hexdigest()
    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    pinned = registry["provenance"]["synthesis_specs_sha256"]
    assert actual == pinned, (
        f"synthesis_specs.yaml digest changed: {actual} != pinned {pinned}. "
        "仕様を変更したなら registry.yaml の synthesis_specs_sha256 を更新すること。"
    )


def test_waveform_pin_matches_generated_samples():
    """波形サンプルの pin（builder + spec = 波形）。builder 変更でも赤くなる。

    synthesis_specs.yaml の sha256 pin は仕様だけを固定するが、生成波形は builder
    (`build_melody_bench.build_signal`) にも依存する。ここでは各 fixture を実際に
    合成し、float32 サンプルの sha256 が registry の `provenance.waveform_sha256`
    と完全一致することを検証する（builder が音を変えれば赤くなり dated 再実測を
    強制する）。
    """
    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    pinned = registry["provenance"]["waveform_sha256"]
    specs = bench.load_specs()
    assert set(pinned) == set(specs["fixtures"]), "pin と spec の fixture 集合が不一致"
    for fid in specs["fixtures"]:
        y, _sr = bench.build_signal(fid, specs)
        actual = hashlib.sha256(np.asarray(y, dtype=np.float32).tobytes()).hexdigest()
        assert actual == pinned[fid], (
            f"波形が変化: {fid} {actual} != pinned {pinned[fid]}. "
            "spec か builder を変更したなら registry の waveform_sha256 を更新すること。"
        )


def test_harness_rejects_unknown_registry_schema(tmp_path):
    """未知の registry schema_version は fail-closed（v0.1 解釈で publish しない）。"""
    import scripts.run_melody_observability as harness

    good = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    good["schema_version"] = "melody-bench/9.9"
    bad = tmp_path / "registry.yaml"
    bad.write_text(yaml.safe_dump(good, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported melody_bench registry schema_version"):
        harness.load_thresholds(bad)


def test_unique_id_map_rejects_duplicate_registry_ids():
    """registry の重複 fixture id は last-wins でなく fail-closed で reject。"""
    import scripts.run_melody_observability as harness

    entries = [
        {"id": "suno_vocals_stem", "input_kind": "vocal_track"},
        {"id": "suno_vocals_stem", "input_kind": "clear_lead"},  # 重複・別 kind
    ]
    with pytest.raises(ValueError, match="duplicate fixture id"):
        harness._unique_id_map(entries, "external_fixtures")
    # 重複なしは通常の map を返す。
    ok = harness._unique_id_map([{"id": "a", "input_kind": "clear_lead"}], "fixtures")
    assert ok == {"a": "clear_lead"}


def test_build_bench_publishes_atomically(tmp_path, monkeypatch):
    """build_melody_bench.main は staging→公開で manifest を最後に配置する。"""
    import scripts.build_melody_bench as bench_mod

    out_dir = tmp_path / "bench"
    real_replace = os.replace
    seen: dict = {}

    def tracking_replace(a, b):
        if str(b).endswith("manifest.json"):
            seen["wavs"] = sorted(p.name for p in out_dir.glob("*.wav"))
        return real_replace(a, b)

    monkeypatch.setattr(bench_mod.os, "replace", tracking_replace)
    monkeypatch.setattr(
        sys, "argv", ["build_melody_bench", "--out-dir", str(out_dir)]
    )
    bench_mod.main()
    specs = bench.load_specs()
    expected_wavs = sorted(f"{fid}.wav" for fid in specs["fixtures"])
    # manifest 公開時に全 WAV が揃っている。
    assert seen["wavs"] == expected_wavs
    assert (out_dir / "manifest.json").exists()
    for fid in specs["fixtures"]:
        assert (out_dir / f"{fid}.wav").exists()


def test_pair_generation_is_atomic_manifest_last(tmp_path, monkeypatch):
    """pair 生成は staging→公開で、manifest 出現時に全 variant が揃っている。"""
    import scripts.make_melody_pairs as pairs

    sr = 22050
    t = np.linspace(0, 1.0, sr, endpoint=False)
    src = tmp_path / "src.wav"
    sf.write(src, (0.3 * np.sin(2 * np.pi * 330 * t)).astype(np.float32), sr, subtype="FLOAT")
    out_dir = tmp_path / "out"

    real_replace = os.replace
    seen_when_manifest_published: dict = {}

    def tracking_replace(a, b):
        # manifest を公開する瞬間、out_dir に既に全 variant が存在していること。
        if str(b).endswith("__pairs_manifest.json"):
            seen_when_manifest_published["variants"] = sorted(
                p.name for p in out_dir.glob("*.wav")
            )
        return real_replace(a, b)

    monkeypatch.setattr(pairs.os, "replace", tracking_replace)
    monkeypatch.setattr(
        sys, "argv",
        ["make_melody_pairs", "--input", str(src), "--out-dir", str(out_dir),
         "--semitones", "2", "--time-rates", "1.05"],
    )
    pairs.main()
    # 公開順序: manifest 出現時に base + pitch + time の 3 variant が揃っている。
    assert seen_when_manifest_published["variants"] == [
        "src__base.wav", "src__pitch_+2st.wav", "src__time_x1.05.wav"
    ]
    assert (out_dir / "src__pairs_manifest.json").exists()


def test_build_signal_is_deterministic():
    specs = bench.load_specs()
    y1, sr1 = bench.build_signal("synth_mono_phrased", specs)
    y2, sr2 = bench.build_signal("synth_mono_phrased", specs)
    assert sr1 == sr2
    assert np.array_equal(y1, y2)


# --------------------------------------------------------------------------- #
# 抽出層 / ハーネス（CI 安全）
# --------------------------------------------------------------------------- #
def test_audio_duration_reads_real_clip_length(tmp_path):
    """note-only coverage の分母は実音声尺（最終ノート終端でなく）を用いる。"""
    from svp_rpe.melody.extractors import _audio_duration_sec

    sr = 22050
    wav = tmp_path / "clip.wav"
    sf.write(wav, np.zeros(int(sr * 2.5), dtype=np.float32), sr, subtype="FLOAT")
    assert _audio_duration_sec(str(wav)) == pytest.approx(2.5, abs=0.01)
    assert _audio_duration_sec(str(tmp_path / "missing.wav")) is None


def test_synthetic_harness_fails_closed_on_unregistered_spec(monkeypatch):
    """registry 未登録の合成 spec id は Go/No-Go に紛れ込ませず fail-closed。"""
    import scripts.run_melody_observability as harness

    specs = bench.load_specs()
    bogus = {**specs, "fixtures": {**specs["fixtures"], "bogus_unregistered": {
        "kind": "chord_pad", "duration_sec": 1.0, "chords": [[60]]}}}
    monkeypatch.setattr(harness, "load_specs", lambda: bogus)
    with pytest.raises(ValueError, match="without a registry.yaml"):
        harness.run_synthetic(_default_thresholds())


def test_external_harness_records_and_verifies_audio_hash(tmp_path):
    """external モードは audio_sha256 / manifest_sha256 を記録し不一致で fail-closed。"""
    import json as _json

    import scripts.run_melody_observability as harness

    sr = 22050
    t = np.linspace(0, 1.0, sr, endpoint=False)
    wav = tmp_path / "ext.wav"
    sf.write(wav, (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), sr, subtype="FLOAT")
    manifest = tmp_path / "ext.json"
    # real_lead_synth は registry の external_fixtures に clear_lead で登録済み。
    manifest.write_text(
        _json.dumps([{"id": "real_lead_synth", "path": str(wav), "input_kind": "clear_lead"}]),
        encoding="utf-8",
    )
    results = harness.run_external(manifest, _default_thresholds())
    entry = results["fixtures"]["real_lead_synth"]
    assert entry["audio_sha256"] == hashlib.sha256(wav.read_bytes()).hexdigest()
    assert entry["audio_path"] == str(wav.resolve())
    assert len(results["manifest_sha256"]) == 64

    # 期待 hash 不一致は fail-closed。
    manifest.write_text(
        _json.dumps(
            [{"id": "real_lead_synth", "path": str(wav), "input_kind": "clear_lead",
              "audio_sha256": "0" * 64}]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="sha256 mismatch"):
        harness.run_external(manifest, _default_thresholds())


def test_external_harness_resolves_relative_paths_against_manifest(tmp_path):
    """external の相対 path は manifest ディレクトリ基準で解決し正規化して記録する。"""
    import json as _json

    import scripts.run_melody_observability as harness

    manifest_dir = tmp_path / "bundle"
    manifest_dir.mkdir()
    sr = 22050
    t = np.linspace(0, 1.0, sr, endpoint=False)
    wav = manifest_dir / "clip.wav"
    sf.write(wav, (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), sr, subtype="FLOAT")
    manifest = manifest_dir / "m.json"
    # 相対 path "clip.wav"（cwd でなく manifest_dir 基準で解決されるべき）。
    manifest.write_text(
        _json.dumps([{"id": "real_lead_synth", "path": "clip.wav", "input_kind": "clear_lead"}]),
        encoding="utf-8",
    )
    results = harness.run_external(manifest, _default_thresholds())
    entry = results["fixtures"]["real_lead_synth"]
    assert entry["audio_path"] == str(wav.resolve())
    assert entry["audio_sha256"] == hashlib.sha256(wav.read_bytes()).hexdigest()


def test_route_assist_populates_cross_extractor_agreement(monkeypatch, tmp_path):
    """assist を宣言した経路は補助抽出器を走らせ cross_extractor_agreement を実測する。"""
    import scripts.run_melody_observability as harness
    from svp_rpe.melody.observability import MelodyObservation
    from svp_rpe.melody.routing import MelodyRoute

    main_notes = tuple(
        MelodyNote(i * 0.5, i * 0.5 + 0.4, 60 + i, 0.9) for i in range(10)
    )
    assist_notes = tuple(
        MelodyNote(i * 0.5, i * 0.5 + 0.4, 60 + i, 0.9) for i in range(10)
    )

    def fake_observe(audio_path, route):
        # 主経路 = ノート系観測、assist 経路名には "__assist_" が入る。
        if "__assist_" in route.name:
            return MelodyObservation(route=route.name, source_model="assist", notes=assist_notes)
        return MelodyObservation(route=route.name, source_model="main", notes=main_notes)

    monkeypatch.setattr(harness, "observe_via_route", fake_observe)
    monkeypatch.setattr(
        "svp_rpe.melody.extractors.observe_via_route", fake_observe
    )

    route = MelodyRoute("basic_pitch_direct", "none", "basic_pitch", assist="melodia")
    # select_routes をこの単一 route に差し替えて 1 経路だけ回す。
    monkeypatch.setattr(harness, "select_routes", lambda kind: [route])
    result = harness._run_routes_on_file("x.wav", "full_mix", _default_thresholds())
    assert len(result) == 1
    row = result[0]
    assert row["assist_extractor"] == "melodia"
    assert row["assist_status"] == "measured"
    assert row["report"]["cross_extractor_agreement"] == pytest.approx(1.0)
    # provenance: 主・補助抽出器の source_model が行に記録される。
    assert row["source_model"] == "main"
    assert row["assist_source_model"] == "assist"
    assert "extractor_version" in row and "assist_extractor_version" in row


def test_separation_route_records_preprocessing_provenance(monkeypatch, tmp_path):
    """demucs_vocals_then_* 行は分離 provenance（model/version）を記録する。"""
    import scripts.run_melody_observability as harness
    from svp_rpe.melody.routing import MelodyRoute

    # demucs 未導入なら observe_via_route が LearnedModelUnavailable を投げる経路でも
    # preprocessing provenance が行に載ることを検証する（CI 安全）。
    route = MelodyRoute("demucs_vocals_then_crepe", "demucs_vocals", "crepe")
    monkeypatch.setattr(harness, "select_routes", lambda kind: [route])

    def raise_unavailable(audio_path, r):
        raise LearnedModelUnavailable("demucs not installed")

    monkeypatch.setattr(harness, "observe_via_route", raise_unavailable)
    rows = harness._run_routes_on_file("x.wav", "vocal_track", _default_thresholds())
    assert len(rows) == 1
    assert rows[0]["outcome"] == "unavailable"
    prov = rows[0]["preprocessing"]
    assert prov["preprocessing"] == "demucs_vocals"
    assert prov["separation_model"] == "htdemucs_ft"
    assert "separation_version" in prov


def test_external_manifest_hash_matches_parsed_bytes(tmp_path):
    """manifest_sha256 は parse したのと同じ bytes の hash である。"""
    import json as _json

    import scripts.run_melody_observability as harness

    sr = 22050
    wav = tmp_path / "ext.wav"
    sf.write(wav, np.zeros(sr, dtype=np.float32), sr, subtype="FLOAT")
    manifest = tmp_path / "m.json"
    manifest.write_text(
        _json.dumps([{"id": "real_lead_synth", "path": str(wav), "input_kind": "clear_lead"}]),
        encoding="utf-8",
    )
    results = harness.run_external(manifest, _default_thresholds())
    assert results["manifest_sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()


def test_external_harness_rejects_unregistered_and_mismatched_ids(tmp_path):
    """external mode は registry 未登録 id / input_kind 不整合を fail-closed。"""
    import json as _json

    import scripts.run_melody_observability as harness

    sr = 22050
    wav = tmp_path / "ext.wav"
    sf.write(wav, np.zeros(sr, dtype=np.float32), sr, subtype="FLOAT")

    unregistered = tmp_path / "u.json"
    unregistered.write_text(
        _json.dumps([{"id": "not_registered", "path": str(wav), "input_kind": "clear_lead"}]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not pre-registered"):
        harness.run_external(unregistered, _default_thresholds())

    # suno_vocals_stem は vocal_track 登録。clear_lead と誤記すれば reject。
    mismatched = tmp_path / "m.json"
    mismatched.write_text(
        _json.dumps([{"id": "suno_vocals_stem", "path": str(wav), "input_kind": "clear_lead"}]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="input_kind"):
        harness.run_external(mismatched, _default_thresholds())


def test_pair_manifest_pins_source_sha256(tmp_path, monkeypatch):
    """controlled-pair manifest が source の生バイト sha256 を pin する。"""
    import json as _json

    import scripts.make_melody_pairs as pairs

    sr = 22050
    t = np.linspace(0, 1.0, sr, endpoint=False)
    src = tmp_path / "src.wav"
    sf.write(src, (0.3 * np.sin(2 * np.pi * 330 * t)).astype(np.float32), sr, subtype="FLOAT")
    out_dir = tmp_path / "out"
    monkeypatch.setattr(
        sys,
        "argv",
        ["make_melody_pairs", "--input", str(src), "--out-dir", str(out_dir),
         "--semitones", "2", "--time-rates", "1.05"],
    )
    pairs.main()
    manifest = _json.loads((out_dir / "src__pairs_manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_sha256"] == hashlib.sha256(src.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
# slow lane: 合成 → 実 pyin 抽出の統合（正/負の対照）
# --------------------------------------------------------------------------- #
@pytest.mark.slow
@pytest.mark.parametrize(
    "fixture_id,expect",
    [
        ("synth_mono_phrased", "sufficient"),
        ("synth_mono_two_phrase", "sufficient"),
        ("synth_chord_pad", "insufficient"),
        ("synth_unison_drone", "insufficient"),
    ],
)
def test_pyin_route_gate_on_synthetic_fixtures(fixture_id, expect):
    """pyin 経路（core librosa）で合成 fixture のゲート結果が事前期待と一致する。

    正の対照（単旋律）= sufficient / 負の対照（和音パッド・ドローン）= insufficient。
    これは M1 の pyin 経路の実測（`docs/melody_observability.md` §Go/No-Go）を
    回帰として固定する。
    """
    from svp_rpe.melody.extractors import extract_pyin_observation

    specs = bench.load_specs()
    y, sr = bench.build_signal(fixture_id, specs)
    obs = extract_pyin_observation(y, sr)
    report = assess_observability(obs, _default_thresholds())
    assert report.status == expect, report.to_dict()
