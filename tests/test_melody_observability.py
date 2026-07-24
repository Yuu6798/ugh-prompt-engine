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
import uuid
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


def test_chord_pad_routing_has_short_circuit_and_diagnostic():
    """負の対照は routing 短絡 + 診断用 pyin 経路の両方を持つ。"""
    routes = {r.name: r for r in select_routes("chord_pad_no_melody")}
    # routing 短絡（旋律不在の宣言的 not_observed）。
    assert routes["not_applicable"].applies is False
    assert routes["not_applicable"].extractor == "none"
    # 診断経路（ゲートが false positive を弾くことを実証する）。
    assert routes["pyin_negative_control"].applies is True
    assert routes["pyin_negative_control"].extractor == "pyin"


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


def test_synthetic_report_pins_and_verifies_waveform_bytes(monkeypatch):
    """synthetic report は各 fixture の waveform_sha256 を pin し、drift を fail-closed。

    registry_sha256 だけでは synthesis_specs / build_signal の drift を検出できず、
    dated Go/No-Go の音が再現・stale 検出不能になる（Codex 指摘・AGENTS §8）。
    external audio_sha256 と対称に、観測した raw float32 サンプルの hash を report へ
    載せ、registry provenance pin と照合することを固定する。
    """
    import scripts.run_melody_observability as harness

    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    pinned = registry["provenance"]["waveform_sha256"]
    results = harness.run_synthetic(_default_thresholds())
    for fid, info in results["fixtures"].items():
        assert info["waveform_sha256"] == pinned[fid]

    # build_signal を drift させると（registry pin と不一致）fail-closed で reject。
    real_build = harness.build_signal

    def drifted_build(fid, specs):
        y, sr = real_build(fid, specs)
        return np.asarray(y, dtype=np.float32) + np.float32(0.01), sr

    monkeypatch.setattr(harness, "build_signal", drifted_build)
    with pytest.raises(ValueError, match="waveform sha256 mismatch"):
        harness.run_synthetic(_default_thresholds())


def test_harness_rejects_unknown_registry_schema(tmp_path):
    """未知の registry schema_version は fail-closed（v0.1 解釈で publish しない）。"""
    import scripts.run_melody_observability as harness

    good = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    good["schema_version"] = "melody-bench/9.9"
    bad = tmp_path / "registry.yaml"
    bad.write_text(yaml.safe_dump(good, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported melody_bench registry schema_version"):
        harness.load_thresholds(bad)


def test_atomic_write_text_preserves_prior_on_failure(tmp_path, monkeypatch):
    """--out の atomic 書き込み: 失敗時に既存 report を破壊せず temp を残さない。"""
    import scripts.run_melody_observability as harness

    out = tmp_path / "report.json"
    out.write_text('{"prev": true}', encoding="utf-8")

    # os.replace を失敗させて中断を模す。
    def boom(a, b):
        raise OSError("disk full")

    monkeypatch.setattr(harness.os, "replace", boom)
    with pytest.raises(OSError):
        harness._atomic_write_text(out, '{"new": true}')
    # 既存 report は無傷、temp file は残らない。
    assert out.read_text(encoding="utf-8") == '{"prev": true}'
    assert not list(tmp_path.glob(".report.json.*.tmp"))

    # 正常系: os.replace を戻し、完全な内容で置換される。
    monkeypatch.undo()
    harness._atomic_write_text(out, '{"new": true}')
    assert out.read_text(encoding="utf-8") == '{"new": true}'


def test_pair_generation_fails_closed_on_stem_collision(tmp_path, monkeypatch):
    """同名 basename の別ソースを同じ out_dir へ出すと fail-closed（同一ソースは冪等）。"""
    import scripts.make_melody_pairs as pairs

    sr = 22050
    t = np.linspace(0, 1.0, sr, endpoint=False)
    out_dir = tmp_path / "out"

    def _write(path, freq):
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(path, (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32), sr, subtype="FLOAT")

    # 別ディレクトリの同名ソース（内容は別 → source_sha256 が異なる）。
    src_a = tmp_path / "a" / "song.wav"
    src_b = tmp_path / "b" / "song.wav"
    _write(src_a, 330.0)
    _write(src_b, 440.0)

    def _run(src):
        monkeypatch.setattr(
            sys, "argv",
            ["make_melody_pairs", "--input", str(src), "--out-dir", str(out_dir),
             "--semitones", "2", "--time-rates", "1.05"],
        )
        return pairs.main()

    _run(src_a)  # 最初のソースは成功。
    with pytest.raises(ValueError, match="stem collision"):
        _run(src_b)  # 同名 basename の別ソース → fail-closed。
    # 同一ソースの再生成は冪等に成功する（衝突ではない）。
    _run(src_a)

    # pin 無し/破損 manifest は同一由来を証明できないので fail-closed。
    (out_dir / "song__pairs_manifest.json").write_text("{ broken json", encoding="utf-8")
    with pytest.raises(ValueError, match="stem collision"):
        _run(src_a)

    # manifest 欠落だが variant WAV の残骸がある場合も証明できず fail-closed。
    (out_dir / "song__pairs_manifest.json").unlink()
    assert list(out_dir.glob("song__*.wav"))  # 残骸が存在する前提。
    with pytest.raises(ValueError, match="stem collision"):
        _run(src_a)


def test_pair_generation_collision_handles_glob_metachar_stem(tmp_path, monkeypatch):
    """glob メタ文字を含む stem でも WAV 残骸を literal 照合して fail-closed。"""
    import scripts.make_melody_pairs as pairs

    sr = 22050
    t = np.linspace(0, 1.0, sr, endpoint=False)
    out_dir = tmp_path / "out"

    def _write(path, freq):
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(path, (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32), sr, subtype="FLOAT")

    # basename に glob メタ文字 `[` `]` を含む同名別ソース。
    src_a = tmp_path / "a" / "song[1].wav"
    src_b = tmp_path / "b" / "song[1].wav"
    _write(src_a, 330.0)
    _write(src_b, 440.0)

    def _run(src):
        monkeypatch.setattr(
            sys, "argv",
            ["make_melody_pairs", "--input", str(src), "--out-dir", str(out_dir),
             "--semitones", "2", "--time-rates", "1.05"],
        )
        return pairs.main()

    _run(src_a)
    # manifest を消し WAV 残骸だけ残す（literal 照合できないと取りこぼす条件）。
    (out_dir / "song[1]__pairs_manifest.json").unlink()
    assert any(p.name.startswith("song[1]__") for p in out_dir.iterdir())
    with pytest.raises(ValueError, match="stem collision"):
        _run(src_b)


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
        # 本スクリプトの公開 op（dst が out_dir 直下）に限って EXDEV 回避を検証する。
        # os.replace のグローバル monkeypatch は numba の JIT キャッシュ書き込み等
        # 無関係な os.replace も拾うため、dst で自分の公開だけに絞る。
        if Path(b).parent == out_dir:
            # staging(src) は out_dir の**中**にある（symlink/マウント構成に依らず
            # rename が同一 fs で成立するよう、宛先と同じ out_dir を親に持つ）。
            assert out_dir in Path(a).parents, (a, out_dir)
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
        # 本スクリプトの公開 op（dst が out_dir 直下）に限って検証する。os.replace の
        # グローバル monkeypatch は librosa/numba の JIT キャッシュ書き込み等
        # 無関係な os.replace も拾うため、dst で自分の公開だけに絞る。
        if Path(b).parent == out_dir:
            # EXDEV 回避: staging(src) は out_dir の**中**（宛先と同じ out_dir を親に持つ
            # ため symlink/マウント構成に依らず rename が同一 fs で成立する）。
            assert out_dir in Path(a).parents, (a, out_dir)
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


def test_synthetic_harness_requires_expect_status(monkeypatch, tmp_path):
    """expect_status 欠落/不正の synthetic fixture は fail-closed（None 素通り禁止）。"""
    import scripts.run_melody_observability as harness

    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    # 1 つの fixture から expect_status を落とす。
    del registry["fixtures"][0]["expect_status"]
    bad = tmp_path / "registry.yaml"
    bad.write_text(yaml.safe_dump(registry, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(harness, "REGISTRY_PATH", bad)
    with pytest.raises(ValueError, match="invalid/missing expect_status"):
        harness.run_synthetic(_default_thresholds())


def test_synthetic_harness_fails_closed_on_unregistered_spec(monkeypatch):
    """registry 未登録の合成 spec id は Go/No-Go に紛れ込ませず fail-closed。"""
    import scripts.run_melody_observability as harness

    specs = bench.load_specs()
    bogus = {**specs, "fixtures": {**specs["fixtures"], "bogus_unregistered": {
        "kind": "chord_pad", "duration_sec": 1.0, "chords": [[60]]}}}
    monkeypatch.setattr(harness, "load_specs", lambda: bogus)
    with pytest.raises(ValueError, match="without a registry.yaml"):
        harness.run_synthetic(_default_thresholds())


def test_report_pins_registry_hash_and_gate_snapshot(tmp_path):
    """report は registry_sha256 と observation_gate スナップショットを pin する。"""
    import json as _json

    import scripts.run_melody_observability as harness

    manifest = tmp_path / "empty.json"
    manifest.write_text(_json.dumps([]), encoding="utf-8")
    results = harness.run_external(manifest)  # audio 無し・registry pin のみ検証
    reg_bytes = Path(harness.REGISTRY_PATH).read_bytes()
    assert results["registry_sha256"] == hashlib.sha256(reg_bytes).hexdigest()
    # 閾値スナップショットが report に載る（passing 行の再現性）。
    assert results["observation_gate"]["min_note_count"] == 8
    # 由来（registry 派生）が明示される。
    assert results["thresholds_source"] == "registry"


def test_report_pins_actual_thresholds_not_registry_when_overridden(tmp_path):
    """thresholds override 時、report は registry snapshot でなく**実際に使った**閾値を載せる。

    override で判定しつつ registry の gate を載せると、report が使っていない gate を
    主張してしまう（Codex 指摘・AGENTS §8）。observation_gate は assess に渡した
    thresholds そのもの・thresholds_source は "override" を記録することを固定する。
    """
    import dataclasses
    import json as _json

    import scripts.run_melody_observability as harness

    # registry の min_note_count(=8) と明確に異なる override。
    override = dataclasses.replace(_default_thresholds(), min_note_count=3)
    manifest = tmp_path / "empty.json"
    manifest.write_text(_json.dumps([]), encoding="utf-8")
    results = harness.run_external(manifest, override)
    assert results["thresholds_source"] == "override"
    # report が主張する gate = 実際に使った override（registry の 8 ではない）。
    assert results["observation_gate"]["min_note_count"] == 3
    assert results["observation_gate"] == dataclasses.asdict(override)
    # synthetic モードでも同様。
    syn = harness.run_synthetic(override)
    assert syn["thresholds_source"] == "override"
    assert syn["observation_gate"]["min_note_count"] == 3


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
# M1-real Go bar 事前登録 + 機械評価器（CI 安全・機械依存の実測は含まない）
# --------------------------------------------------------------------------- #
# 凍結した M1-real 素材 ID（docs/melody_observability.md §6.1 と registry.yaml の
# single source of truth を CI で pin する）。registry.yaml の m1_real_go_bar を
# 事故で編集（positive の 1 本入替・negative の差し替え等）したら CI が赤くなり、
# 意図的変更（M2 等）でのみ本定数と registry を同時更新する（Codex 指摘: 凍結 ID を
# ランタイムにハードコードして registry と二重 SSOT 化する代わりに、registry を
# guard するテストとして pin する）。
_FROZEN_GO_BAR_POSITIVE_IDS = {
    "real_vocal_jrock",
    "real_vocal_futurepop",
    "real_vocal_band",
    "real_vocal_waltz",
}
_FROZEN_GO_BAR_NEGATIVE_IDS = {"real_instrumental_negative"}


def test_registry_has_m1_real_go_bar_preregistered():
    """m1_real_go_bar が凍結どおり登録され、正確な 4 positive + 1 negative が vocal_track で揃う。"""
    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    bar = registry["m1_real_go_bar"]
    assert bar["min_positive_sufficient"] == 3
    assert bar["max_negative_false_positive"] == 0
    assert bar["total_positive"] == 4
    assert len(bar["positive_ids"]) == 4
    assert len(bar["negative_ids"]) == 1
    # 正確な凍結 ID 集合を pin（本数だけでなく id 名まで固定し、別 fixture への
    # 事故的すり替えを CI で検出する）。
    assert set(bar["positive_ids"]) == _FROZEN_GO_BAR_POSITIVE_IDS
    assert set(bar["negative_ids"]) == _FROZEN_GO_BAR_NEGATIVE_IDS

    external_by_id = registry["external_fixtures"]
    ids_seen = [f["id"] for f in external_by_id]
    # 各 go-bar positive/negative id はちょうど 1 回だけ登録される。
    for fid in bar["positive_ids"] + bar["negative_ids"]:
        assert ids_seen.count(fid) == 1, fid

    by_id = {f["id"]: f for f in external_by_id}
    for fid in bar["positive_ids"] + bar["negative_ids"]:
        assert fid in by_id, fid
        assert by_id[fid]["input_kind"] == "vocal_track", fid


def _make_go_bar_report(
    route_outcomes: "dict[str, dict[str, str]]",
    registry_sha256: str = "f" * 64,
    default_outcome: str = "unavailable",
    run_id: "str | None" = None,
    generator_code_sha256: str = "g" * 64,
) -> "dict":
    """{fixture_id: {route_name: outcome}} から external report dict を組み立てる。

    evaluate_m1_real_go_bar のテスト専用ヘルパー。observation_gate は実際の
    registry 由来の thresholds snapshot（asdict）を使う（本物の external report
    形状に合わせる）。
    """
    import dataclasses

    thresholds = _default_thresholds()
    # 完全な vocal_track matrix を模す: 明示されない経路は unavailable で埋める
    # （評価器の full-matrix 完全性チェックを満たしつつ、未指定経路は未観測扱い）。
    route_objs = select_routes("vocal_track")
    vocal_track_routes = [r.name for r in route_objs]
    route_extractor = {r.name: r.extractor for r in route_objs}

    def _row(route, outcome):
        # measured（gate outcome）な行は harness と同じ provenance / 根拠 report を持つ。
        # 抽出器は凍結 route 定義に合わせ（評価器の extractor 照合に一致）、分離経路は
        # separation provenance も付ける（provenance 完全性チェックに合わせる）。
        row = {
            "route": route,
            "extractor": route_extractor.get(route, "crepe"),
            "outcome": outcome,
            "report": None,
        }
        if outcome in ("sufficient", "insufficient"):
            # 根拠 report payload。凍結ゲート（min_note_count 8/min_phrase_count 2 等）
            # に対し metrics から再導出した status が outcome と一致するよう構築する。
            if outcome == "sufficient":
                row["report"] = {
                    "status": "sufficient", "route": route, "voiced_coverage": 0.7,
                    "note_count": 12, "phrase_count": 3, "confidence_mean": 0.9,
                    "low_confidence_rate": 0.05, "octave_jump_rate": 0.0,
                    "cross_extractor_agreement": None,
                }
            else:
                row["report"] = {
                    "status": "insufficient", "route": route, "voiced_coverage": 0.7,
                    "note_count": 1, "phrase_count": 1, "confidence_mean": 0.9,
                    "low_confidence_rate": 0.05, "octave_jump_rate": 0.0,
                    "cross_extractor_agreement": None,
                }
            row["source_model"] = f"{route}:model"
            row["extractor_version"] = "0.0.13"
            if route.startswith("demucs_vocals_"):
                row["preprocessing"] = {
                    "preprocessing": "demucs_vocals",
                    "separation_model": "htdemucs_ft",
                    "separation_version": "4.0.1",
                    # #54: measured 分離経路は stem/weights hash 必須。repeats 間で一致させる
                    # ため route 由来の決定論値を使う（同一 fixture の 2 repeats で同値）。
                    "stem_sha256": hashlib.sha256(f"{route}:stem".encode()).hexdigest(),
                    "separation_weights_sha256": hashlib.sha256(
                        b"htdemucs_ft:4.0.1:weights"
                    ).hexdigest(),
                }
        return row

    fixtures = {}
    for fixture_id, routes in route_outcomes.items():
        full_routes = {name: default_outcome for name in vocal_track_routes}
        full_routes.update(routes)
        fixtures[fixture_id] = {
            "input_kind": "vocal_track",
            "expect_status": None,
            "audio_path": f"/fake/{fixture_id}.wav",
            # fixture ごとに別素材を模す（id 由来の決定論 hash・repeats 間で同一）。
            "audio_sha256": hashlib.sha256(fixture_id.encode()).hexdigest(),
            "routes": [_row(route, outcome) for route, outcome in full_routes.items()],
        }
    return {
        "mode": "external",
        "manifest_path": "fake_manifest.json",
        "manifest_sha256": "0" * 64,
        "registry_sha256": registry_sha256,
        "thresholds_source": "registry",
        "observation_gate": dataclasses.asdict(thresholds),
        # 各 report は既定で独立した run_id を持つ（run_external が実行ごとに発行する
        # のを模す）。テストで偽 repeats（コピー）を模すときは明示的に同一値を渡す。
        "run_id": run_id if run_id is not None else uuid.uuid4().hex,
        # generator code provenance（#50）。既定で repeats 間一致（同一 generator stack）。
        "generator_code_sha256": generator_code_sha256,
        "fixtures": fixtures,
    }


_GO_BAR_POSITIVES = (
    "real_vocal_jrock",
    "real_vocal_futurepop",
    "real_vocal_band",
    "real_vocal_waltz",
)
_GO_BAR_NEGATIVE = "real_instrumental_negative"
_GO_BAR_ROUTE = "demucs_vocals_then_crepe"


def _go_bar_registry():
    reg = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    # #53: go-bar fixture は scoring 時に expected_audio_sha256 必須。real registry は
    # slow-lane 記録前なので未設定。happy path 用に、`_make_go_bar_report` が付ける
    # report の audio_sha256（= sha256(fixture_id)）と一致する値を注入する。
    go_bar_ids = set(_GO_BAR_POSITIVES) | {_GO_BAR_NEGATIVE}
    for entry in reg.get("external_fixtures", []):
        if entry["id"] in go_bar_ids:
            entry["expected_audio_sha256"] = hashlib.sha256(entry["id"].encode()).hexdigest()
    return reg


def test_evaluate_go_bar_go_when_survivor():
    """3/4 positive が両 report で安定 sufficient・negative は insufficient → go。"""
    import scripts.run_melody_observability as harness

    def route_outcomes(waltz_outcome="insufficient"):
        outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
        outcomes["real_vocal_waltz"] = {_GO_BAR_ROUTE: waltz_outcome}
        outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
        return outcomes

    report1 = _make_go_bar_report(route_outcomes())
    report2 = _make_go_bar_report(route_outcomes())
    verdict = harness.evaluate_m1_real_go_bar([report1, report2], _go_bar_registry())
    assert verdict["verdict"] == "go"
    assert _GO_BAR_ROUTE in verdict["surviving_routes"]
    assert verdict["routes"][_GO_BAR_ROUTE]["pos_sufficient"] == 3
    assert verdict["routes"][_GO_BAR_ROUTE]["neg_false_positive"] == 0


def test_evaluate_go_bar_verdict_pins_evaluator_code_digest():
    """verdict が verdict を解釈したコード（評価器+依存モジュール）の digest を pin する。"""
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes["real_vocal_waltz"] = {_GO_BAR_ROUTE: "insufficient"}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    r1 = _make_go_bar_report(outcomes)
    r2 = _make_go_bar_report(outcomes)
    verdict = harness.evaluate_m1_real_go_bar([r1, r2], _go_bar_registry())
    assert verdict["verdict"] == "go"
    # コード digest が載り、実モジュールの再計算 digest と一致する。
    assert len(verdict["evaluator_code_sha256"]) == 64
    assert verdict["evaluator_code_sha256"] == harness._evaluator_code_sha256()


def test_evaluate_go_bar_no_go_below_threshold():
    """crepe sufficient が 2/4 positive のみ → no_go、surviving_routes は空。"""
    import scripts.run_melody_observability as harness

    outcomes = {
        "real_vocal_jrock": {_GO_BAR_ROUTE: "sufficient"},
        "real_vocal_futurepop": {_GO_BAR_ROUTE: "sufficient"},
        "real_vocal_band": {_GO_BAR_ROUTE: "insufficient"},
        "real_vocal_waltz": {_GO_BAR_ROUTE: "insufficient"},
        _GO_BAR_NEGATIVE: {_GO_BAR_ROUTE: "insufficient"},
    }
    # 確定 no_go には全経路の実測が要る（default_outcome=insufficient で 4 経路 measured）。
    report1 = _make_go_bar_report(outcomes, default_outcome="insufficient")
    report2 = _make_go_bar_report(outcomes, default_outcome="insufficient")
    verdict = harness.evaluate_m1_real_go_bar([report1, report2], _go_bar_registry())
    assert verdict["verdict"] == "no_go"
    assert verdict["surviving_routes"] == []
    assert verdict["routes"][_GO_BAR_ROUTE]["pos_sufficient"] == 2


def test_evaluate_go_bar_false_positive_disqualifies():
    """positive 4/4 だが negative でも sufficient を出す route は失格 → no_go。"""
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "sufficient"}  # 偽陽性
    report1 = _make_go_bar_report(outcomes, default_outcome="insufficient")
    report2 = _make_go_bar_report(outcomes, default_outcome="insufficient")
    verdict = harness.evaluate_m1_real_go_bar([report1, report2], _go_bar_registry())
    assert verdict["verdict"] == "no_go"
    assert verdict["surviving_routes"] == []
    assert verdict["routes"][_GO_BAR_ROUTE]["pos_sufficient"] == 4
    assert verdict["routes"][_GO_BAR_ROUTE]["neg_false_positive"] == 1


def test_evaluate_go_bar_instability_not_counted():
    """report 間で outcome が食い違う positive は stably_sufficient にならず no_go。"""
    import scripts.run_melody_observability as harness

    stable_outcomes = {
        "real_vocal_jrock": {_GO_BAR_ROUTE: "sufficient"},
        "real_vocal_futurepop": {_GO_BAR_ROUTE: "sufficient"},
        "real_vocal_waltz": {_GO_BAR_ROUTE: "insufficient"},
        _GO_BAR_NEGATIVE: {_GO_BAR_ROUTE: "insufficient"},
    }
    report1 = _make_go_bar_report(
        {**stable_outcomes, "real_vocal_band": {_GO_BAR_ROUTE: "sufficient"}},
        default_outcome="insufficient",
    )
    report2 = _make_go_bar_report(
        {**stable_outcomes, "real_vocal_band": {_GO_BAR_ROUTE: "insufficient"}},
        default_outcome="insufficient",
    )
    verdict = harness.evaluate_m1_real_go_bar([report1, report2], _go_bar_registry())
    # jrock/futurepop の 2 本のみ安定 sufficient（<3）→ no_go。
    assert verdict["verdict"] == "no_go"
    assert verdict["routes"][_GO_BAR_ROUTE]["pos_sufficient"] == 2
    assert "real_vocal_band" in verdict["routes"][_GO_BAR_ROUTE]["unstable_positive_ids"]


def test_evaluate_go_bar_fails_closed_on_missing_fixture():
    """事前登録済み fixture が report から丸ごと欠けていたら id を名指しして ValueError。"""
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    del outcomes["real_vocal_waltz"]  # 1 本まるごと欠落。
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    report1 = _make_go_bar_report(outcomes)
    report2 = _make_go_bar_report(outcomes)
    with pytest.raises(ValueError, match="real_vocal_waltz"):
        harness.evaluate_m1_real_go_bar([report1, report2], _go_bar_registry())


def test_evaluate_go_bar_fails_closed_on_registry_mismatch():
    """report 間で registry_sha256 が食い違えば fail-closed。"""
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    report1 = _make_go_bar_report(outcomes, registry_sha256="a" * 64)
    report2 = _make_go_bar_report(outcomes, registry_sha256="b" * 64)
    with pytest.raises(ValueError, match="registry_sha256"):
        harness.evaluate_m1_real_go_bar([report1, report2], _go_bar_registry())


def test_evaluate_go_bar_rejects_empty_and_non_external():
    """空の reports、および mode!='external' な report は fail-closed。"""
    import scripts.run_melody_observability as harness

    with pytest.raises(ValueError, match="reports is empty"):
        harness.evaluate_m1_real_go_bar([], _go_bar_registry())

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    report1 = _make_go_bar_report(outcomes)
    report2 = _make_go_bar_report(outcomes)
    report2["mode"] = "synthetic"
    with pytest.raises(ValueError, match="mode"):
        harness.evaluate_m1_real_go_bar([report1, report2], _go_bar_registry())


def test_evaluate_go_bar_fails_closed_below_repeats_min():
    """単一 report（n=1 < 凍結 repeats_min=2）は verdict を出さず fail-closed。"""
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    report1 = _make_go_bar_report(outcomes)
    # 単一 report では repeats_min=2 を満たさない（1 回実行の go verdict を弾く）。
    with pytest.raises(ValueError, match="repeats_min"):
        harness.evaluate_m1_real_go_bar([report1], _go_bar_registry())


def test_evaluate_go_bar_fails_closed_on_stale_reports_vs_loaded_registry():
    """report 同士は一致するが loaded registry の hash と食い違えば fail-closed。

    古い registry で測った stale な report 2 本が互いに一致しても、いまバーを load
    した registry の hash（authoritative reference）と紐づかなければ判定できない
    （バーの焼き込み保証・Codex 指摘）。
    """
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    # 両 report とも同じ「古い」registry hash で相互一致している。
    report1 = _make_go_bar_report(outcomes, registry_sha256="0" * 64)
    report2 = _make_go_bar_report(outcomes, registry_sha256="0" * 64)
    # loaded registry の authoritative hash（今日のバー）と食い違う → fail-closed。
    with pytest.raises(ValueError, match="registry_sha256"):
        harness.evaluate_m1_real_go_bar(
            [report1, report2], _go_bar_registry(), registry_sha256="9" * 64
        )
    # pin が loaded hash と一致すれば判定に到達し、verdict はその hash に紐づく。
    verdict = harness.evaluate_m1_real_go_bar(
        [report1, report2], _go_bar_registry(), registry_sha256="0" * 64
    )
    assert verdict["registry_sha256"] == "0" * 64
    assert verdict["verdict"] == "go"


def test_evaluate_go_bar_route_unmeasured_on_negative_does_not_survive():
    """positive を満たしても negative でその経路が未観測なら survive させない。

    偽陽性ゼロは「negative で実測して sufficient が出なかった」で初めて certify できる。
    negative がその経路を一度も測っていない（neg_false_positive==0 だが未観測）状態を
    Go と誤認しない（Codex 指摘・fail-closed）。
    """
    import scripts.run_melody_observability as harness

    # positive 4/4 は本命経路で sufficient。だが negative は別経路しか測っておらず
    # 本命経路が未観測（欠落）。
    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {"pyin_direct": "insufficient"}  # 本命経路は未観測
    report1 = _make_go_bar_report(outcomes)
    report2 = _make_go_bar_report(outcomes)
    verdict = harness.evaluate_m1_real_go_bar([report1, report2], _go_bar_registry())
    # どの経路も全 fixture×route で実測されていない（本命は negative 未観測）→ inconclusive。
    assert verdict["verdict"] == "inconclusive"
    assert _GO_BAR_ROUTE not in verdict["surviving_routes"]
    assert verdict["routes"][_GO_BAR_ROUTE]["pos_sufficient"] == 4
    assert (
        _GO_BAR_NEGATIVE
        in verdict["routes"][_GO_BAR_ROUTE]["negative_unmeasured_ids"]
    )


def test_evaluate_go_bar_fails_closed_on_audio_pin_mismatch_or_missing():
    """同一 fixture id の audio_sha256 が report 間で不一致/欠落なら fail-closed。"""
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}

    # 同一 id で別素材（audio_sha256 が run 間で食い違う）→ 同一素材の n>=2 と見なせない。
    report1 = _make_go_bar_report(outcomes)
    report2 = _make_go_bar_report(outcomes)
    report2["fixtures"]["real_vocal_jrock"]["audio_sha256"] = "a" * 64
    with pytest.raises(ValueError, match="audio_sha256"):
        harness.evaluate_m1_real_go_bar([report1, report2], _go_bar_registry())

    # audio_sha256 欠落（None）も素材同一性を pin できず fail-closed。
    report3 = _make_go_bar_report(outcomes)
    report4 = _make_go_bar_report(outcomes)
    report4["fixtures"]["real_vocal_band"]["audio_sha256"] = None
    with pytest.raises(ValueError, match="audio_sha256"):
        harness.evaluate_m1_real_go_bar([report3, report4], _go_bar_registry())


def test_evaluate_go_bar_fails_closed_on_duplicate_or_missing_run_id():
    """report 間で run_id が重複/欠落なら fail-closed（コピー由来の偽 repeats を弾く）。

    決定論パイプラインでは n>=2 が意味を持つのは独立実行のときだけ。run1.json を別パスへ
    コピーすると別パス・同一 bytes で path-dedup（#6）を通過し、1 回の抽出で repeats_min=2 を
    偽装できてしまう。run_external が実行ごとに発行する run_id の存在＋相互 distinct を要求する
    ことでこれを弾く（#45）。素材同一性（audio_sha256 一致）とは直交する軸。
    """
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}

    # 同一 run_id（run1.json → run2.json コピー相当）→ 独立した n>=2 repeats と見なせない。
    report1 = _make_go_bar_report(outcomes, run_id="deadbeef" * 4)
    report2 = _make_go_bar_report(outcomes, run_id="deadbeef" * 4)
    with pytest.raises(ValueError, match="run_id"):
        harness.evaluate_m1_real_go_bar([report1, report2], _go_bar_registry())

    # run_id 欠落（None / 空文字）も独立性を pin できず fail-closed。
    report3 = _make_go_bar_report(outcomes)
    report4 = _make_go_bar_report(outcomes)
    del report4["run_id"]
    with pytest.raises(ValueError, match="run_id"):
        harness.evaluate_m1_real_go_bar([report3, report4], _go_bar_registry())

    report5 = _make_go_bar_report(outcomes, run_id="")
    report6 = _make_go_bar_report(outcomes)
    with pytest.raises(ValueError, match="run_id"):
        harness.evaluate_m1_real_go_bar([report5, report6], _go_bar_registry())


def test_evaluate_go_bar_carries_run_ids_in_verdict():
    """独立 run_id を持つ正常な報告群は verdict へ run_ids を転記する（provenance 証跡）。"""
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    report1 = _make_go_bar_report(outcomes, run_id="a1" * 16)
    report2 = _make_go_bar_report(outcomes, run_id="b2" * 16)
    verdict = harness.evaluate_m1_real_go_bar([report1, report2], _go_bar_registry())
    assert verdict["run_ids"] == sorted(["a1" * 16, "b2" * 16])


def test_run_external_stamps_distinct_run_id(monkeypatch, tmp_path):
    """run_external は実行ごとに新規 run_id を発行する（コピーでなく別実行の証跡）。"""
    import dataclasses
    import json

    import scripts.run_melody_observability as harness

    # 経路実行と registry load をスタブ化し、run_id 発行だけを軽量に検証する。
    monkeypatch.setattr(harness, "_run_routes_on_file", lambda *a, **k: [])
    monkeypatch.setattr(
        harness,
        "_load_registry",
        lambda: (
            {
                "observation_gate": dataclasses.asdict(_default_thresholds()),
                "external_fixtures": [
                    {"id": "real_vocal_jrock", "input_kind": "vocal_track"}
                ],
            },
            "0" * 64,
        ),
    )
    audio = tmp_path / "x.wav"
    audio.write_bytes(b"RIFFfake")
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps([{"id": "real_vocal_jrock", "path": "x.wav", "input_kind": "vocal_track"}])
    )
    r1 = harness.run_external(manifest)
    r2 = harness.run_external(manifest)
    assert r1["run_id"] and r2["run_id"]
    assert r1["run_id"] != r2["run_id"]
    # generator_code_sha256 は実 generator コードの digest で、実行間で安定（#50）。
    assert r1["generator_code_sha256"] == r2["generator_code_sha256"]
    assert r1["generator_code_sha256"] == harness._generator_code_sha256()


def test_generator_code_paths_include_melody_and_adapters():
    """_generator_code_paths は harness ＋ melody モジュール ＋ 下流 learned adapter ＋
    pyin baseline が依存する physical_features を含む（#50/#51/#52）。"""
    import svp_rpe.io.source_separator as _sep
    import svp_rpe.melody.extractors as _extractors
    import svp_rpe.melody.observability as _obs
    import svp_rpe.melody.routing as _routing
    import svp_rpe.rpe.learned.basic_pitch_adapter as _bp
    import svp_rpe.rpe.learned.crepe_adapter as _crepe
    import svp_rpe.rpe.learned.melodia_adapter as _melodia
    import svp_rpe.rpe.learned.source_separation_adapter as _srcsep
    import svp_rpe.rpe.physical_features as _phys

    import scripts.run_melody_observability as harness

    paths = set(harness._generator_code_paths())
    assert Path(harness.__file__).resolve() in paths
    for mod in (_extractors, _obs, _routing, _crepe, _melodia, _bp, _srcsep, _sep, _phys):
        assert Path(mod.__file__).resolve() in paths


def test_generator_code_paths_is_import_closed():
    """_generator_code_paths が first-party import 閉包として閉じている（#52 遅延 import 穴の構造的封鎖）。

    返り値の各ファイルの import（関数内含む）を AST で辿り、first-party の import target が
    すべて集合内にあることを確認する。将来 generator 系モジュールに遅延 import を足して集合が
    不完全になれば、この不変条件が破れて CI が Codex より先に気づく（hand-list の取りこぼしを
    構造的に防ぐ）。
    """
    import ast

    import scripts.run_melody_observability as harness

    paths = set(harness._generator_code_paths())
    assert paths, "generator code path set is empty"
    for f in paths:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        candidates: set = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    candidates.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    candidates.add(node.module)
                    for alias in node.names:
                        candidates.add(f"{node.module}.{alias.name}")
        for name in candidates:
            target = harness._first_party_module_file(name)
            if target is not None:
                assert target in paths, (
                    f"{f.name} imports first-party {name!r} which is not in "
                    "_generator_code_paths() (import closure incomplete)"
                )


def test_evaluate_go_bar_fails_closed_on_missing_or_differing_generator_code(monkeypatch):
    """generator_code_sha256 が欠落/repeats 間不一致なら fail-closed（#50）。

    route 行・gate metrics を産出した generator コードの digest が無い、または別 checkout の
    generator で測った run（digest 不一致）を混ぜたら、stale extraction を素通りさせないため
    verdict を出さない（verdict の evaluator_code_sha256 と対の生成側 provenance）。
    """
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}

    # 欠落 → fail-closed。
    r1 = _make_go_bar_report(outcomes)
    r2 = _make_go_bar_report(outcomes)
    del r2["generator_code_sha256"]
    with pytest.raises(ValueError, match="generator_code_sha256"):
        harness.evaluate_m1_real_go_bar([r1, r2], _go_bar_registry())

    # repeats 間で不一致（別 generator stack）→ fail-closed。
    r3 = _make_go_bar_report(outcomes, generator_code_sha256="a" * 64)
    r4 = _make_go_bar_report(outcomes, generator_code_sha256="b" * 64)
    with pytest.raises(ValueError, match="generator_code_sha256"):
        harness.evaluate_m1_real_go_bar([r3, r4], _go_bar_registry())


def test_evaluate_go_bar_carries_generator_code_sha256_in_verdict():
    """repeats 一致した generator_code_sha256 を verdict へ転記する（#50 provenance）。"""
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    r1 = _make_go_bar_report(outcomes, generator_code_sha256="c" * 64)
    r2 = _make_go_bar_report(outcomes, generator_code_sha256="c" * 64)
    verdict = harness.evaluate_m1_real_go_bar([r1, r2], _go_bar_registry())
    assert verdict["generator_code_sha256"] == "c" * 64


def test_evaluate_go_bar_requires_expected_audio_sha256_for_every_fixture():
    """go-bar fixture が registry に expected_audio_sha256 を持たなければ fail-closed（#53）。

    未記録だと manifest が frozen id を誤った audio に向けても両 repeats で一致してしまい、
    事前登録素材として一度も pin されていない material に Go が出る。scoring 時点で必須化。
    """
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes["real_vocal_waltz"] = {_GO_BAR_ROUTE: "insufficient"}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    r1 = _make_go_bar_report(outcomes)
    r2 = _make_go_bar_report(outcomes)

    # 素の real registry（expected_audio_sha256 未記録）→ Go を出さず fail-closed。
    bare_reg = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="expected_audio_sha256"):
        harness.evaluate_m1_real_go_bar([r1, r2], bare_reg)

    # 1 本だけ欠落しても fail-closed（`_go_bar_registry` は全 fixture に注入するので、
    # negative の 1 本を消して部分欠落を作る）。
    reg = _go_bar_registry()
    for entry in reg["external_fixtures"]:
        if entry["id"] == _GO_BAR_NEGATIVE:
            entry.pop("expected_audio_sha256", None)
    with pytest.raises(ValueError, match="expected_audio_sha256"):
        harness.evaluate_m1_real_go_bar([r1, r2], reg)


def test_evaluate_go_bar_requires_stem_and_weights_for_measured_separation(monkeypatch):
    """measured 分離経路が stem/weights hash を欠けば stable Go に数えず fail-closed（#54）。

    同一 htdemucs_ft/version でも別 cached weights や再生成 stem bytes だと実際の前処理入力が
    変わるため、これらの pin なしに分離経路を Go survivor に数えない。
    """
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes["real_vocal_waltz"] = {_GO_BAR_ROUTE: "insufficient"}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    r1 = _make_go_bar_report(outcomes)
    r2 = _make_go_bar_report(outcomes)
    # 本命の分離経路（measured/sufficient）から stem/weights を除去 → provenance 不足で fail-closed。
    for report in (r1, r2):
        for row in report["fixtures"]["real_vocal_jrock"]["routes"]:
            if row["route"] == _GO_BAR_ROUTE:
                row["preprocessing"] = {
                    "preprocessing": "demucs_vocals",
                    "separation_model": "htdemucs_ft",
                    "separation_version": "4.0.1",
                }
    with pytest.raises(ValueError, match="stem_sha256/separation_weights_sha256"):
        harness.evaluate_m1_real_go_bar([r1, r2], _go_bar_registry())


def test_json_loads_rejects_duplicate_object_keys():
    """`_json_loads_no_dup_keys` は任意ネスト階層の重複 object キーを弾く（#46）。"""
    import scripts.run_melody_observability as harness

    # top-level 重複。
    with pytest.raises(ValueError, match="duplicate JSON object key"):
        harness._json_loads_no_dup_keys('{"k": 1, "k": 2}', what="report x")
    # ネスト（fixtures 相当）階層の重複 = 失敗版→合格版で失敗 repeat を隠すケース。
    nested = (
        '{"fixtures": {"real_vocal_jrock": {"outcome": "insufficient"}, '
        '"real_vocal_jrock": {"outcome": "sufficient"}}}'
    )
    with pytest.raises(ValueError, match="duplicate JSON object key"):
        harness._json_loads_no_dup_keys(nested, what="report y")
    # 正常な JSON はそのまま parse される。
    assert harness._json_loads_no_dup_keys('{"a": 1, "b": {"c": 2}}', what="ok") == {
        "a": 1,
        "b": {"c": 2},
    }


def test_evaluate_go_bar_cli_rejects_report_with_duplicate_fixture_key(tmp_path, monkeypatch):
    """--evaluate-go-bar が重複 fixture キーを持つ report を parse 時点で fail-closed（#46）。

    json.loads は last-wins で畳むため、失敗版→合格版の 2 重 `real_vocal_jrock` を
    採点前に弾かないと、矛盾する bytes を pin しつつ合格版だけ採点して go を publish
    しうる。dup-key 拒否 hook で採点前に弾くことを end-to-end で確認する。
    """
    import json as _json

    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes["real_vocal_waltz"] = {_GO_BAR_ROUTE: "insufficient"}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    reg_sha = hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()
    r1 = _make_go_bar_report(outcomes, registry_sha256=reg_sha)
    r2 = _make_go_bar_report(outcomes, registry_sha256=reg_sha)
    # r1 の raw JSON に、本物の real_vocal_jrock の前へ失敗版 stub を注入して重複キーを作る。
    stub = (
        '"real_vocal_jrock": {"input_kind": "vocal_track", "expect_status": null, '
        '"audio_path": "x", "audio_sha256": "0", "routes": []}, '
    )
    dup_raw = _json.dumps(r1).replace('"fixtures": {', '"fixtures": {' + stub, 1)
    run1 = tmp_path / "run1.json"
    run1.write_text(dup_raw, encoding="utf-8")
    run2 = tmp_path / "run2.json"
    run2.write_text(_json.dumps(r2), encoding="utf-8")
    out = tmp_path / "verdict.json"
    monkeypatch.setattr(
        sys, "argv",
        ["run_melody_observability", "--evaluate-go-bar", str(run1), str(run2), "--out", str(out)],
    )
    with pytest.raises(ValueError, match="duplicate JSON object key"):
        harness.main()
    assert not out.exists()  # 採点前に弾くので verdict は書かれない。


def test_evaluate_go_bar_fails_closed_on_expected_audio_sha256_mismatch():
    """registry に expected_audio_sha256 があれば report の audio と一致要求（forward-compat）。

    frozen 素材の expected audio hash の **記録**（emit）は slow-lane 生成時の
    machine-dependent な dated pin だが、記録されて registry に載れば評価器は照合し、
    manifest typo/差替で別素材に verdict を出すのを弾く（存在すれば比較・未記録なら no-op）。
    """
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    r1 = _make_go_bar_report(outcomes)
    r2 = _make_go_bar_report(outcomes)
    # real_vocal_jrock に事前登録 expected_audio_sha256 を pin（report の実 hash と別値）。
    reg = _go_bar_registry()
    for entry in reg["external_fixtures"]:
        if entry["id"] == "real_vocal_jrock":
            entry["expected_audio_sha256"] = "e" * 64
    with pytest.raises(ValueError, match="expected_audio_sha256"):
        harness.evaluate_m1_real_go_bar([r1, r2], reg)


def test_evaluate_go_bar_route_unmeasured_on_positive_does_not_survive():
    """positive の 1 本でその経路が未観測なら、残り 3 本が sufficient でも survive させない。

    「≥3/4 sufficient」は 4 本すべてを実測した上での 3 本以上を意味する。1 本が
    unavailable/欠落なら matrix 未完で go を publish しない（Codex 指摘・negative 側と対称）。
    """
    import scripts.run_melody_observability as harness

    # jrock/futurepop/band は本命経路で sufficient、waltz はその経路が未観測（別経路のみ）。
    outcomes = {
        "real_vocal_jrock": {_GO_BAR_ROUTE: "sufficient"},
        "real_vocal_futurepop": {_GO_BAR_ROUTE: "sufficient"},
        "real_vocal_band": {_GO_BAR_ROUTE: "sufficient"},
        "real_vocal_waltz": {"pyin_direct": "insufficient"},  # 本命経路は未観測
        _GO_BAR_NEGATIVE: {_GO_BAR_ROUTE: "insufficient"},
    }
    report1 = _make_go_bar_report(outcomes)
    report2 = _make_go_bar_report(outcomes)
    verdict = harness.evaluate_m1_real_go_bar([report1, report2], _go_bar_registry())
    # 本命経路は waltz が未観測で完全実測でない・他経路も unavailable → inconclusive。
    assert verdict["verdict"] == "inconclusive"
    assert _GO_BAR_ROUTE not in verdict["surviving_routes"]
    # pos_sufficient は 3 だが未観測 positive があるため survive しない。
    assert verdict["routes"][_GO_BAR_ROUTE]["pos_sufficient"] == 3
    assert (
        "real_vocal_waltz"
        in verdict["routes"][_GO_BAR_ROUTE]["positive_unmeasured_ids"]
    )


def test_resolve_unique_report_paths_rejects_duplicates(tmp_path):
    """同一 report ファイルの二重指定はパス正規化して fail-closed（内容一致では弾かない）。"""
    import scripts.run_melody_observability as harness

    run1 = tmp_path / "run1.json"
    run1.write_text("[]", encoding="utf-8")
    run2 = tmp_path / "run2.json"
    run2.write_text("[]", encoding="utf-8")

    # 同一ファイルを 2 回（resolve 後に一致）→ fail-closed。
    with pytest.raises(ValueError, match="duplicate report path"):
        harness._resolve_unique_report_paths([run1, run1])
    # ./run1.json のような別表記でも resolve で同一と判定して弾く。
    with pytest.raises(ValueError, match="duplicate report path"):
        harness._resolve_unique_report_paths([run1, tmp_path / "." / "run1.json"])
    # 別パス（内容が同一でも）は正当な n>=2 繰返しなので通す。
    resolved = harness._resolve_unique_report_paths([run1, run2])
    assert resolved == [run1.resolve(), run2.resolve()]


def test_evaluate_go_bar_fails_closed_on_incomplete_route_matrix():
    """truncated report（vocal_track 4 経路の一部が丸ごと欠落）は fail-closed。

    candidate_routes は「存在する経路」からしか作られないため、ある経路が全 fixture
    から欠けても未観測として弾かれない。期待経路集合（select_routes）と突き合わせて
    欠落を検出する（Codex 指摘・docs §6.3 の完全 matrix）。
    """
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    report1 = _make_go_bar_report(outcomes)
    report2 = _make_go_bar_report(outcomes)
    # truncate: 1 fixture から本命経路以外の全 route row を削り、pyin_direct のみ残す。
    for report in (report1, report2):
        rows = report["fixtures"]["real_vocal_jrock"]["routes"]
        report["fixtures"]["real_vocal_jrock"]["routes"] = [
            row for row in rows if row["route"] == _GO_BAR_ROUTE
        ]
    with pytest.raises(ValueError, match="missing route"):
        harness.evaluate_m1_real_go_bar([report1, report2], _go_bar_registry())


def test_evaluate_go_bar_fails_closed_on_overridden_or_tampered_gate():
    """override ゲート / 凍結ゲートと不一致な observation_gate の report は fail-closed。"""
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}

    # thresholds_source=override（緩いゲートで測定した可能性）→ fail-closed。
    r1 = _make_go_bar_report(outcomes)
    r2 = _make_go_bar_report(outcomes)
    r1["thresholds_source"] = "override"
    with pytest.raises(ValueError, match="thresholds_source"):
        harness.evaluate_m1_real_go_bar([r1, r2], _go_bar_registry())

    # observation_gate が凍結ゲートと不一致（両 report で一致していても）→ fail-closed。
    r3 = _make_go_bar_report(outcomes)
    r4 = _make_go_bar_report(outcomes)
    r3["observation_gate"] = {**r3["observation_gate"], "min_note_count": 3}
    r4["observation_gate"] = {**r4["observation_gate"], "min_note_count": 3}
    with pytest.raises(ValueError, match="observation_gate"):
        harness.evaluate_m1_real_go_bar([r3, r4], _go_bar_registry())


def test_evaluate_go_bar_fails_closed_on_mislabeled_input_kind():
    """report が fixture を registry 凍結 kind と別の input_kind と誤申告したら fail-closed。"""
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    r1 = _make_go_bar_report(outcomes)
    r2 = _make_go_bar_report(outcomes)
    # registry では vocal_track。report が clear_lead と誤申告（易しい matrix へのすり替え）。
    for report in (r1, r2):
        report["fixtures"]["real_vocal_jrock"]["input_kind"] = "clear_lead"
    with pytest.raises(ValueError, match="input_kind"):
        harness.evaluate_m1_real_go_bar([r1, r2], _go_bar_registry())


def test_evaluate_go_bar_fails_closed_on_duplicate_audio_across_fixtures():
    """別素材であるべき go-bar fixture が同一 audio_sha256 を指したら fail-closed。"""
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    r1 = _make_go_bar_report(outcomes)
    r2 = _make_go_bar_report(outcomes)
    # jrock と futurepop が同一 WAV を指す（実質 1 素材で ≥3/4 を満たすのを防ぐ）。
    for report in (r1, r2):
        report["fixtures"]["real_vocal_jrock"]["audio_sha256"] = "c" * 64
        report["fixtures"]["real_vocal_futurepop"]["audio_sha256"] = "c" * 64
    # #53 の expected_audio 一致チェックを通した上で cross-fixture 重複を検出させるため、
    # 両 fixture の registry expected を共有値に合わせる（重複検出は expected 検証の後段）。
    reg = _go_bar_registry()
    for entry in reg["external_fixtures"]:
        if entry["id"] in ("real_vocal_jrock", "real_vocal_futurepop"):
            entry["expected_audio_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="same audio_sha256"):
        harness.evaluate_m1_real_go_bar([r1, r2], reg)


def test_evaluate_go_bar_fails_closed_on_provenance_mismatch():
    """同一 fixture×route の model provenance が repeats 間で食い違えば fail-closed。"""
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    r1 = _make_go_bar_report(outcomes)
    r2 = _make_go_bar_report(outcomes)

    def _set_source_model(report, fid, route, model):
        for row in report["fixtures"][fid]["routes"]:
            if row["route"] == route:
                row["source_model"] = model

    # 同一 route を別バージョンの抽出器 model で測った 2 run（マシン跨ぎ/アップグレード）。
    _set_source_model(r1, "real_vocal_jrock", _GO_BAR_ROUTE, "crepe-0.0.13")
    _set_source_model(r2, "real_vocal_jrock", _GO_BAR_ROUTE, "crepe-0.0.14")
    with pytest.raises(ValueError, match="model provenance"):
        harness.evaluate_m1_real_go_bar([r1, r2], _go_bar_registry())


def test_evaluate_go_bar_fails_closed_on_missing_provenance_fields():
    """measured route が provenance を欠く（全 None 署名）と fail-closed。"""
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    r1 = _make_go_bar_report(outcomes)
    r2 = _make_go_bar_report(outcomes)
    # provenance を pin しない古い/手書き report を模す: 両 report で jrock の
    # measured route から source_model を削る（全 None 署名同士は一致してしまう）。
    for report in (r1, r2):
        for row in report["fixtures"]["real_vocal_jrock"]["routes"]:
            if row["route"] == _GO_BAR_ROUTE:
                row.pop("source_model", None)
    with pytest.raises(ValueError, match="lacks provenance"):
        harness.evaluate_m1_real_go_bar([r1, r2], _go_bar_registry())


def test_evaluate_go_bar_ignores_routes_outside_frozen_matrix():
    """report に混入した非登録経路は candidate に入らず verdict に効かない。

    正規の vocal_track 4 経路が 1 本も survive していないのに、混入した架空経路で
    go が出ないことを確認する（Codex 指摘）。
    """
    import scripts.run_melody_observability as harness

    # 正規 4 経路は全 fixture で insufficient（誰も survive しない・全経路 measured）。
    outcomes = {fid: {_GO_BAR_ROUTE: "insufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    r1 = _make_go_bar_report(outcomes, default_outcome="insufficient")
    r2 = _make_go_bar_report(outcomes, default_outcome="insufficient")
    # 非登録の架空経路を positive に混入（sufficient）＋ negative に insufficient。
    for report in (r1, r2):
        for fid in _GO_BAR_POSITIVES:
            report["fixtures"][fid]["routes"].append(
                {"route": "bogus_route", "extractor": "x", "outcome": "sufficient",
                 "report": None, "source_model": "x", "extractor_version": "1"}
            )
        report["fixtures"][_GO_BAR_NEGATIVE]["routes"].append(
            {"route": "bogus_route", "extractor": "x", "outcome": "insufficient",
             "report": None, "source_model": "x", "extractor_version": "1"}
        )
    verdict = harness.evaluate_m1_real_go_bar([r1, r2], _go_bar_registry())
    assert verdict["verdict"] == "no_go"
    assert "bogus_route" not in verdict["routes"]
    assert verdict["surviving_routes"] == []


def test_evaluate_go_bar_fails_closed_on_duplicate_route_rows():
    """同一 fixture に同名 route 行が重複したら fail-closed（last-wins 隠蔽を防ぐ）。"""
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    r1 = _make_go_bar_report(outcomes)
    r2 = _make_go_bar_report(outcomes)
    # jrock に本命経路の行を重複追加（後勝ちで失敗/未観測を隠す想定）。
    for report in (r1, r2):
        report["fixtures"]["real_vocal_jrock"]["routes"].append(
            {"route": _GO_BAR_ROUTE, "extractor": "crepe", "outcome": "sufficient",
             "report": None, "source_model": "demucs_vocals+crepe", "extractor_version": "0.0.13",
             "preprocessing": {"preprocessing": "demucs_vocals",
                               "separation_model": "htdemucs_ft", "separation_version": "4.0.1"}}
        )
    with pytest.raises(ValueError, match="duplicate route row"):
        harness.evaluate_m1_real_go_bar([r1, r2], _go_bar_registry())


def test_evaluate_go_bar_cli_pins_report_hashes(tmp_path, monkeypatch):
    """--evaluate-go-bar の verdict.json が消費した report の content hash を pin する。"""
    import json as _json

    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes["real_vocal_waltz"] = {_GO_BAR_ROUTE: "insufficient"}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    # CLI は _load_registry の実 hash を authoritative reference として渡すため、
    # report の registry_sha256 を実ファイル hash に合わせる。
    reg_sha = hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()
    # slow-lane 記録後の registry 状態（expected_audio_sha256 済み・#53）を模す。CLI は
    # _load_registry を呼ぶので、pin 注入済み registry と実 hash を返すよう差し替える。
    monkeypatch.setattr(harness, "_load_registry", lambda: (_go_bar_registry(), reg_sha))
    r1 = _make_go_bar_report(outcomes, registry_sha256=reg_sha)
    r2 = _make_go_bar_report(outcomes, registry_sha256=reg_sha)
    run1 = tmp_path / "run1.json"
    run1.write_text(_json.dumps(r1), encoding="utf-8")
    run2 = tmp_path / "run2.json"
    run2.write_text(_json.dumps(r2), encoding="utf-8")
    out = tmp_path / "verdict.json"
    # 相対パスで渡す（tmp_path を cwd に）。report_pins は resolve せず渡された相対
    # パスをそのまま載せ、verdict.json を可搬に保つことを検証する。
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys, "argv",
        ["run_melody_observability", "--evaluate-go-bar", "run1.json", "run2.json",
         "--out", str(out)],
    )
    assert harness.main() == 0
    verdict = _json.loads(out.read_text(encoding="utf-8"))
    assert verdict["verdict"] == "go"
    pins = {p["sha256"] for p in verdict["report_pins"]}
    assert pins == {
        hashlib.sha256(run1.read_bytes()).hexdigest(),
        hashlib.sha256(run2.read_bytes()).hexdigest(),
    }
    assert len(verdict["report_pins"]) == 2
    # path は resolve 済み絶対でなく、渡された相対パスのまま（可搬・非 machine-local）。
    assert {p["path"] for p in verdict["report_pins"]} == {"run1.json", "run2.json"}


def test_evaluate_go_bar_fails_closed_on_duplicate_or_overlapping_bar_ids():
    """凍結バーの positive_ids 重複 / positive-negative overlap は fail-closed。"""
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    r1 = _make_go_bar_report(outcomes)
    r2 = _make_go_bar_report(outcomes)

    # positive_ids に typo 重複 → 1 素材の二重計上を防ぐため fail-closed。
    reg_dup = _go_bar_registry()
    reg_dup["m1_real_go_bar"]["positive_ids"] = (
        list(reg_dup["m1_real_go_bar"]["positive_ids"]) + ["real_vocal_jrock"]
    )
    with pytest.raises(ValueError, match="positive_ids"):
        harness.evaluate_m1_real_go_bar([r1, r2], reg_dup)

    # positive と negative が同一素材を共有 → fail-closed。
    reg_overlap = _go_bar_registry()
    reg_overlap["m1_real_go_bar"]["negative_ids"] = ["real_vocal_jrock"]
    with pytest.raises(ValueError, match="重複"):
        harness.evaluate_m1_real_go_bar([r1, r2], reg_overlap)


def test_evaluate_go_bar_fails_closed_on_bar_cardinality_mismatch():
    """凍結バーの positive 本数が total_positive と不一致 / negative 空は fail-closed。"""
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    r1 = _make_go_bar_report(outcomes)
    r2 = _make_go_bar_report(outcomes)

    # positive を 1 本落とす（3 本 != total_positive 4）→ 「3/3 で go」を防ぐ fail-closed。
    reg_short = _go_bar_registry()
    reg_short["m1_real_go_bar"]["positive_ids"] = list(
        reg_short["m1_real_go_bar"]["positive_ids"]
    )[:3]
    with pytest.raises(ValueError, match="total_positive"):
        harness.evaluate_m1_real_go_bar([r1, r2], reg_short)

    # negative_ids を空に → 偽陽性ガードなしで go を防ぐ fail-closed。
    reg_no_neg = _go_bar_registry()
    reg_no_neg["m1_real_go_bar"]["negative_ids"] = []
    with pytest.raises(ValueError, match="negative_ids"):
        harness.evaluate_m1_real_go_bar([r1, r2], reg_no_neg)


def test_evaluate_go_bar_fails_closed_on_repeats_min_below_two():
    """凍結バーの repeats_min が 2 未満（typo で 1 等）なら fail-closed（n>=2 仕様）。"""
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    r1 = _make_go_bar_report(outcomes)
    r2 = _make_go_bar_report(outcomes)
    reg = _go_bar_registry()
    reg["m1_real_go_bar"]["repeats_min"] = 1
    with pytest.raises(ValueError, match="repeats_min"):
        harness.evaluate_m1_real_go_bar([r1, r2], reg)


def test_evaluate_go_bar_fails_closed_on_incoherent_pass_fail_thresholds():
    """pass/fail 閾値の semantic 自己整合違反（min=0 / 偽陽性ガード空洞化）は fail-closed。"""
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    r1 = _make_go_bar_report(outcomes)
    r2 = _make_go_bar_report(outcomes)

    # min_positive_sufficient=0 → 0 本 sufficient で go できてしまう。
    reg0 = _go_bar_registry()
    reg0["m1_real_go_bar"]["min_positive_sufficient"] = 0
    with pytest.raises(ValueError, match="min_positive_sufficient"):
        harness.evaluate_m1_real_go_bar([r1, r2], reg0)

    # max_negative_false_positive=1 で negative 1 本 → 全 negative で偽陽性でも pass（空洞化）。
    reg_vac = _go_bar_registry()
    reg_vac["m1_real_go_bar"]["max_negative_false_positive"] = 1
    with pytest.raises(ValueError, match="max_negative_false_positive"):
        harness.evaluate_m1_real_go_bar([r1, r2], reg_vac)


def test_evaluate_go_bar_compares_stem_hash_across_repeats_when_present():
    """measured 分離経路の stem_sha256 は必須で、repeats 間で不一致なら fail-closed（#54）。

    stem/weights hash の **emit** は実 Demucs を要する machine-dependent な slow-lane 課題
    だが、**要求**（measured 分離経路は stem/weights を pin しないと stable Go に数えない）は
    machine-independent。ここでは同一 model/version でも別 stem なら別 run として弾くことを確認。
    """
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes["real_vocal_waltz"] = {_GO_BAR_ROUTE: "insufficient"}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    r1 = _make_go_bar_report(outcomes, default_outcome="insufficient")
    r2 = _make_go_bar_report(outcomes, default_outcome="insufficient")

    def _set_stem(report, stem):
        for row in report["fixtures"]["real_vocal_jrock"]["routes"]:
            if row["route"] == _GO_BAR_ROUTE:
                row["preprocessing"] = {**row["preprocessing"], "stem_sha256": stem}

    # 同一 model/version だが別 vocals stem（再生成・別 weights 相当）を repeats 間で。
    _set_stem(r1, "a" * 64)
    _set_stem(r2, "b" * 64)
    with pytest.raises(ValueError, match="model provenance"):
        harness.evaluate_m1_real_go_bar([r1, r2], _go_bar_registry())


def test_evaluate_go_bar_fails_closed_on_extractor_label_mismatch():
    """route ラベルと実測 extractor がすり替わっていたら fail-closed。"""
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    r1 = _make_go_bar_report(outcomes)
    r2 = _make_go_bar_report(outcomes)
    # 本命経路（crepe ラベル）を実際は pyin で測ったと偽装（両 report で一致させる）。
    for report in (r1, r2):
        for row in report["fixtures"]["real_vocal_jrock"]["routes"]:
            if row["route"] == _GO_BAR_ROUTE:
                row["extractor"] = "pyin"
    with pytest.raises(ValueError, match="すり替え"):
        harness.evaluate_m1_real_go_bar([r1, r2], _go_bar_registry())


def test_evaluate_go_bar_fails_closed_on_preprocessing_on_direct_route():
    """分離不要な直接経路（pyin_direct）が preprocessing を持てば fail-closed。"""
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes["real_vocal_waltz"] = {_GO_BAR_ROUTE: "insufficient"}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    r1 = _make_go_bar_report(outcomes, default_outcome="insufficient")
    r2 = _make_go_bar_report(outcomes, default_outcome="insufficient")
    # pyin_direct（分離不要）行に demucs preprocessing を捏造。
    for report in (r1, r2):
        for row in report["fixtures"]["real_vocal_jrock"]["routes"]:
            if row["route"] == "pyin_direct":
                row["preprocessing"] = {
                    "preprocessing": "demucs_vocals",
                    "separation_model": "htdemucs_ft",
                    "separation_version": "4.0.1",
                }
    with pytest.raises(ValueError, match="preprocessing"):
        harness.evaluate_m1_real_go_bar([r1, r2], _go_bar_registry())


def test_evaluate_go_bar_inconclusive_when_all_routes_unavailable():
    """optional 依存欠如で全経路 unavailable の report は no_go でなく inconclusive。"""
    import scripts.run_melody_observability as harness

    # どの fixture も gate outcome を出さない（全 4 経路 auto-fill unavailable）。
    outcomes = {fid: {} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {}
    report1 = _make_go_bar_report(outcomes)
    report2 = _make_go_bar_report(outcomes)
    verdict = harness.evaluate_m1_real_go_bar([report1, report2], _go_bar_registry())
    # 測っていない（no_go=測って落ちた ではない）→ inconclusive。
    assert verdict["verdict"] == "inconclusive"
    assert verdict["surviving_routes"] == []
    assert verdict["fully_measured_routes"] == []


def test_evaluate_go_bar_inconclusive_when_only_baseline_measured():
    """pyin_direct のみ実測・本命経路が unavailable なら no_go でなく inconclusive。

    強化版 No-Go は全候補経路の完全実測を要する。baseline だけ測って本命
    （Demucs/CREPE/Melodia）が未導入の report で「生き残りなし＝no_go」と偽らない。
    """
    import scripts.run_melody_observability as harness

    outcomes = {fid: {"pyin_direct": "insufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {"pyin_direct": "insufficient"}
    r1 = _make_go_bar_report(outcomes)  # 他 3 経路は default unavailable
    r2 = _make_go_bar_report(outcomes)
    verdict = harness.evaluate_m1_real_go_bar([r1, r2], _go_bar_registry())
    assert verdict["verdict"] == "inconclusive"
    assert verdict["surviving_routes"] == []
    assert verdict["fully_measured_routes"] == ["pyin_direct"]


def test_evaluate_go_bar_fails_closed_on_outcome_report_status_mismatch():
    """measured 行の outcome が根拠 report.status と食い違えば fail-closed。"""
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes["real_vocal_waltz"] = {_GO_BAR_ROUTE: "insufficient"}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    r1 = _make_go_bar_report(outcomes, default_outcome="insufficient")
    r2 = _make_go_bar_report(outcomes, default_outcome="insufficient")
    # outcome だけ sufficient に改竄し、根拠 report.status は insufficient のまま食い違わせる。
    for report in (r1, r2):
        for row in report["fixtures"]["real_vocal_jrock"]["routes"]:
            if row["route"] == _GO_BAR_ROUTE:
                row["report"] = {"status": "insufficient"}
    with pytest.raises(ValueError, match="matching report payload"):
        harness.evaluate_m1_real_go_bar([r1, r2], _go_bar_registry())


def test_evaluate_go_bar_fails_closed_on_metrics_gate_inconsistency():
    """outcome/report.status を sufficient に揃えても payload metrics が凍結ゲート未満なら fail-closed。"""
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes["real_vocal_waltz"] = {_GO_BAR_ROUTE: "insufficient"}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    r1 = _make_go_bar_report(outcomes, default_outcome="insufficient")
    r2 = _make_go_bar_report(outcomes, default_outcome="insufficient")
    # status/outcome は sufficient のまま、payload metrics を凍結ゲート未満へ改竄
    # （note_count 1 < min 8 / phrase_count 1 < min 2）。再導出 status=insufficient と食い違う。
    for report in (r1, r2):
        for row in report["fixtures"]["real_vocal_jrock"]["routes"]:
            if row["route"] == _GO_BAR_ROUTE:
                row["report"] = {**row["report"], "note_count": 1, "phrase_count": 1}
    with pytest.raises(ValueError, match="metrics"):
        harness.evaluate_m1_real_go_bar([r1, r2], _go_bar_registry())


def test_evaluate_go_bar_fails_closed_on_negative_insufficient_hiding_passing_metrics():
    """negative で outcome=insufficient なのに metrics が明確に passing なら fail-closed。

    direction 限定にすると、negative 側の「実は sufficient（偽陽性）を insufficient と
    ラベルして隠す」改竄を見逃す。丸め許容誤差込みの両方向照合で捕捉する（Codex 指摘）。
    """
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    r1 = _make_go_bar_report(outcomes, default_outcome="insufficient")
    r2 = _make_go_bar_report(outcomes, default_outcome="insufficient")
    # negative 本命経路: outcome/status=insufficient のまま metrics は**明確に** passing
    # （丸め幅外・実際は sufficient で route が negative で偽陽性を出しているのを隠蔽）。
    for report in (r1, r2):
        for row in report["fixtures"][_GO_BAR_NEGATIVE]["routes"]:
            if row["route"] == _GO_BAR_ROUTE:
                row["report"] = {
                    "status": "insufficient", "route": _GO_BAR_ROUTE,
                    "voiced_coverage": 0.9, "note_count": 12, "phrase_count": 3,
                    "confidence_mean": 0.9, "low_confidence_rate": 0.05,
                    "octave_jump_rate": 0.0, "cross_extractor_agreement": None,
                }
    with pytest.raises(ValueError, match="食い違い"):
        harness.evaluate_m1_real_go_bar([r1, r2], _go_bar_registry())


def test_evaluate_go_bar_fails_closed_on_report_route_mismatch():
    """report payload の route が行 route と食い違えば fail-closed（別経路 payload のすり替え）。"""
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes["real_vocal_waltz"] = {_GO_BAR_ROUTE: "insufficient"}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    r1 = _make_go_bar_report(outcomes, default_outcome="insufficient")
    r2 = _make_go_bar_report(outcomes, default_outcome="insufficient")
    # jrock 本命経路の report payload の route を別経路（pyin_direct）へ貼り替え。
    for report in (r1, r2):
        for row in report["fixtures"]["real_vocal_jrock"]["routes"]:
            if row["route"] == _GO_BAR_ROUTE:
                row["report"] = {**row["report"], "route": "pyin_direct"}
    with pytest.raises(ValueError, match="payload route"):
        harness.evaluate_m1_real_go_bar([r1, r2], _go_bar_registry())


def test_evaluate_go_bar_fails_closed_on_source_model_route_contradiction():
    """extractor ラベルを偽装しても source_model が別抽出器ファミリなら fail-closed。"""
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes["real_vocal_waltz"] = {_GO_BAR_ROUTE: "insufficient"}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    r1 = _make_go_bar_report(outcomes, default_outcome="insufficient")
    r2 = _make_go_bar_report(outcomes, default_outcome="insufficient")
    # 本命 crepe 経路（extractor は crepe のまま）だが source_model は pyin ファミリ。
    for report in (r1, r2):
        for row in report["fixtures"]["real_vocal_jrock"]["routes"]:
            if row["route"] == _GO_BAR_ROUTE:
                row["source_model"] = "librosa:pyin"
    with pytest.raises(ValueError, match="source_model"):
        harness.evaluate_m1_real_go_bar([r1, r2], _go_bar_registry())


def test_evaluate_go_bar_fails_closed_on_non_finite_or_out_of_range_metric():
    """NaN/Infinity や範囲外の payload metric は再導出前に fail-closed。"""
    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes["real_vocal_waltz"] = {_GO_BAR_ROUTE: "insufficient"}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}

    # NaN（NaN < min は False で passing すり抜けを狙う）。
    r1 = _make_go_bar_report(outcomes, default_outcome="insufficient")
    r2 = _make_go_bar_report(outcomes, default_outcome="insufficient")
    for report in (r1, r2):
        for row in report["fixtures"]["real_vocal_jrock"]["routes"]:
            if row["route"] == _GO_BAR_ROUTE:
                row["report"] = {**row["report"], "voiced_coverage": float("nan")}
    with pytest.raises(ValueError, match="有限値でない"):
        harness.evaluate_m1_real_go_bar([r1, r2], _go_bar_registry())

    # 範囲外（confidence_mean > 1）。
    r3 = _make_go_bar_report(outcomes, default_outcome="insufficient")
    r4 = _make_go_bar_report(outcomes, default_outcome="insufficient")
    for report in (r3, r4):
        for row in report["fixtures"]["real_vocal_jrock"]["routes"]:
            if row["route"] == _GO_BAR_ROUTE:
                row["report"] = {**row["report"], "confidence_mean": 1.5}
    with pytest.raises(ValueError, match="有限値でない"):
        harness.evaluate_m1_real_go_bar([r3, r4], _go_bar_registry())


def test_evaluate_go_bar_cli_rejects_out_colliding_with_report(tmp_path, monkeypatch):
    """--out が入力 report path と衝突したら書き込み前に fail-closed（report を破壊しない）。"""
    import json as _json

    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes["real_vocal_waltz"] = {_GO_BAR_ROUTE: "insufficient"}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    reg_sha = hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()
    r1 = _make_go_bar_report(outcomes, registry_sha256=reg_sha)
    r2 = _make_go_bar_report(outcomes, registry_sha256=reg_sha)
    run1 = tmp_path / "run1.json"
    run1.write_text(_json.dumps(r1), encoding="utf-8")
    run2 = tmp_path / "run2.json"
    run2.write_text(_json.dumps(r2), encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv",
        ["run_melody_observability", "--evaluate-go-bar", str(run1), str(run2), "--out", str(run1)],
    )
    with pytest.raises(ValueError, match="衝突"):
        harness.main()
    # run1 は verdict で上書きされず無傷。
    assert _json.loads(run1.read_text(encoding="utf-8")) == r1


def test_evaluate_go_bar_cli_rejects_out_overwriting_registry(tmp_path, monkeypatch):
    """--out が凍結 registry を指したら書き込み前に fail-closed（registry を破壊しない）。"""
    import json as _json

    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes["real_vocal_waltz"] = {_GO_BAR_ROUTE: "insufficient"}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    reg_sha = hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()
    r1 = _make_go_bar_report(outcomes, registry_sha256=reg_sha)
    r2 = _make_go_bar_report(outcomes, registry_sha256=reg_sha)
    run1 = tmp_path / "run1.json"
    run1.write_text(_json.dumps(r1), encoding="utf-8")
    run2 = tmp_path / "run2.json"
    run2.write_text(_json.dumps(r2), encoding="utf-8")
    # --out が凍結 registry を指す（typo）→ 書き込み前に fail-closed。
    monkeypatch.setattr(
        sys, "argv",
        ["run_melody_observability", "--evaluate-go-bar", str(run1), str(run2),
         "--out", str(REGISTRY_PATH)],
    )
    before = REGISTRY_PATH.read_bytes()
    with pytest.raises(ValueError, match="保護対象"):
        harness.main()
    # 凍結 registry は verdict で上書きされず無傷。
    assert REGISTRY_PATH.read_bytes() == before


def test_evaluate_go_bar_cli_rejects_out_overwriting_evaluator_code(tmp_path, monkeypatch):
    """--out が評価器コード（evaluator_code_sha256 で hash 済み）を指したら fail-closed。"""
    import json as _json

    import scripts.run_melody_observability as harness

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes["real_vocal_waltz"] = {_GO_BAR_ROUTE: "insufficient"}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    reg_sha = hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()
    r1 = _make_go_bar_report(outcomes, registry_sha256=reg_sha)
    r2 = _make_go_bar_report(outcomes, registry_sha256=reg_sha)
    run1 = tmp_path / "run1.json"
    run1.write_text(_json.dumps(r1), encoding="utf-8")
    run2 = tmp_path / "run2.json"
    run2.write_text(_json.dumps(r2), encoding="utf-8")
    code_path = harness._evaluator_code_paths()[0]  # 評価器ソースの 1 つ。
    monkeypatch.setattr(
        sys, "argv",
        ["run_melody_observability", "--evaluate-go-bar", str(run1), str(run2),
         "--out", str(code_path)],
    )
    before = code_path.read_bytes()
    with pytest.raises(ValueError, match="保護対象"):
        harness.main()
    # 評価器コードは verdict で上書きされず無傷。
    assert code_path.read_bytes() == before


def test_evaluate_go_bar_cli_rejects_out_overwriting_report_source_audio(tmp_path, monkeypatch):
    """--evaluate-go-bar の --out が report fixture の source audio を指したら fail-closed（#48）。

    report を pin/採点した直後に、その report の repeat/検証に必要な slow-lane source
    音源を verdict JSON で破壊するのを防ぐ（external モードの同名保護と対称）。
    """
    import json as _json

    import scripts.run_melody_observability as harness

    # 実在の source WAV を用意し、r1 の fixture audio_path をそこへ向ける。
    sr = 22050
    t = np.linspace(0, 1.0, sr, endpoint=False)
    wav = tmp_path / "real_vocal_jrock_source.wav"
    sf.write(wav, (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), sr, subtype="FLOAT")

    outcomes = {fid: {_GO_BAR_ROUTE: "sufficient"} for fid in _GO_BAR_POSITIVES}
    outcomes["real_vocal_waltz"] = {_GO_BAR_ROUTE: "insufficient"}
    outcomes[_GO_BAR_NEGATIVE] = {_GO_BAR_ROUTE: "insufficient"}
    reg_sha = hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()
    r1 = _make_go_bar_report(outcomes, registry_sha256=reg_sha)
    r1["fixtures"]["real_vocal_jrock"]["audio_path"] = str(wav)
    r2 = _make_go_bar_report(outcomes, registry_sha256=reg_sha)
    run1 = tmp_path / "run1.json"
    run1.write_text(_json.dumps(r1), encoding="utf-8")
    run2 = tmp_path / "run2.json"
    run2.write_text(_json.dumps(r2), encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv",
        ["run_melody_observability", "--evaluate-go-bar", str(run1), str(run2),
         "--out", str(wav)],
    )
    before = wav.read_bytes()
    with pytest.raises(ValueError, match="保護対象"):
        harness.main()
    # source audio は verdict で上書きされず無傷。
    assert wav.read_bytes() == before


def test_external_cli_rejects_out_colliding_with_manifest_or_source(tmp_path, monkeypatch):
    """--external の --out が manifest / source audio を指したら書き込み前に fail-closed。"""
    import json as _json

    import scripts.run_melody_observability as harness

    sr = 22050
    t = np.linspace(0, 1.0, sr, endpoint=False)
    wav = tmp_path / "clip.wav"
    sf.write(wav, (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), sr, subtype="FLOAT")
    manifest = tmp_path / "m.json"
    manifest.write_text(
        _json.dumps([{"id": "real_lead_synth", "path": str(wav), "input_kind": "clear_lead"}]),
        encoding="utf-8",
    )

    # --out == manifest → 拒否・manifest 無傷。
    monkeypatch.setattr(
        sys, "argv",
        ["run_melody_observability", "--external", str(manifest), "--out", str(manifest)],
    )
    manifest_before = manifest.read_bytes()
    with pytest.raises(ValueError, match="保護対象"):
        harness.main()
    assert manifest.read_bytes() == manifest_before

    # --out == source audio → 拒否・audio 無傷。
    monkeypatch.setattr(
        sys, "argv",
        ["run_melody_observability", "--external", str(manifest), "--out", str(wav)],
    )
    wav_before = wav.read_bytes()
    with pytest.raises(ValueError, match="保護対象"):
        harness.main()
    assert wav.read_bytes() == wav_before


def test_synthetic_cli_rejects_out_overwriting_synthesis_specs(monkeypatch):
    """default synthetic モードの --out が synthesis_specs.yaml を指したら fail-closed。"""
    import scripts.run_melody_observability as harness

    # run_synthetic を軽量スタブ化（実抽出を回さず protected 判定だけ検証）。
    monkeypatch.setattr(
        harness, "run_synthetic", lambda: {"mode": "synthetic", "fixtures": {}}
    )
    monkeypatch.setattr(
        sys, "argv", ["run_melody_observability", "--out", str(SPECS_PATH)]
    )
    before = SPECS_PATH.read_bytes()
    with pytest.raises(ValueError, match="保護対象"):
        harness.main()
    # committed synthesis_specs.yaml は無傷。
    assert SPECS_PATH.read_bytes() == before


def test_synthetic_cli_rejects_out_overwriting_builder(monkeypatch):
    """default synthetic モードの --out が build_melody_bench.py（builder）を指したら fail-closed。"""
    import scripts.run_melody_observability as harness

    monkeypatch.setattr(
        harness, "run_synthetic", lambda: {"mode": "synthetic", "fixtures": {}}
    )
    builder_path = Path(bench.__file__)
    monkeypatch.setattr(
        sys, "argv", ["run_melody_observability", "--out", str(builder_path)]
    )
    before = builder_path.read_bytes()
    with pytest.raises(ValueError, match="保護対象"):
        harness.main()
    # builder コードは無傷。
    assert builder_path.read_bytes() == before


def test_report_mode_cli_rejects_out_overwriting_harness_source(monkeypatch, tmp_path):
    """非 evaluate モードの --out が harness 自身を指したら fail-closed（#47）。

    `--external manifest.json --out scripts/run_melody_observability.py` の typo で
    report を生成している harness スクリプト自体を JSON で atomic 上書きさせない
    （evaluate 分岐が評価器コードを守るのと対称）。synthetic / external 両分岐で守る。
    """
    import json as _json

    import scripts.run_melody_observability as harness

    harness_path = Path(harness.__file__)

    # synthetic 分岐（run_synthetic をスタブ化し protected 判定だけ検証）。
    monkeypatch.setattr(
        harness, "run_synthetic", lambda: {"mode": "synthetic", "fixtures": {}}
    )
    monkeypatch.setattr(
        sys, "argv", ["run_melody_observability", "--out", str(harness_path)]
    )
    before = harness_path.read_bytes()
    with pytest.raises(ValueError, match="保護対象"):
        harness.main()
    assert harness_path.read_bytes() == before  # harness コードは無傷。

    # external 分岐（軽量 manifest。run_external をスタブ化）。
    monkeypatch.setattr(
        harness, "run_external", lambda *a, **k: {"mode": "external", "fixtures": {}}
    )
    manifest = tmp_path / "m.json"
    manifest.write_text(_json.dumps([]), encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv",
        ["run_melody_observability", "--external", str(manifest), "--out", str(harness_path)],
    )
    with pytest.raises(ValueError, match="保護対象"):
        harness.main()
    assert harness_path.read_bytes() == before  # harness コードは無傷。


def test_report_mode_cli_rejects_out_overwriting_generator_modules(monkeypatch, tmp_path):
    """非 evaluate モードの --out が generator モジュール/adapter を指したら fail-closed（#49/#51）。

    report 生成は extractors / observability / routing を使って route matrix・gate を組み、
    optional 経路では下流 learned adapter / source_separator まで到達する。
    `--external m.json --out src/svp_rpe/rpe/learned/crepe_adapter.py` の typo でその
    generator コードを JSON で atomic 上書き破壊させない（`_generator_code_paths()` で
    harness ＋ melody モジュール ＋ adapter を一括保護）。
    """
    import json as _json

    import svp_rpe.io.source_separator as _sep
    import svp_rpe.melody.extractors as _extractors
    import svp_rpe.melody.observability as _obs
    import svp_rpe.melody.routing as _routing
    import svp_rpe.rpe.learned.basic_pitch_adapter as _bp
    import svp_rpe.rpe.learned.crepe_adapter as _crepe
    import svp_rpe.rpe.learned.melodia_adapter as _melodia
    import svp_rpe.rpe.learned.source_separation_adapter as _srcsep
    import svp_rpe.rpe.physical_features as _phys

    import scripts.run_melody_observability as harness

    module_paths = [
        Path(_extractors.__file__),
        Path(_obs.__file__),
        Path(_routing.__file__),
        Path(_crepe.__file__),
        Path(_melodia.__file__),
        Path(_bp.__file__),
        Path(_srcsep.__file__),
        Path(_sep.__file__),
        Path(_phys.__file__),
    ]
    # `_generator_code_paths()` がこれら全モジュール（+ harness）を含むことを直接確認。
    protected = set(harness._generator_code_paths())
    for mp in module_paths:
        assert mp.resolve() in protected

    manifest = tmp_path / "m.json"
    manifest.write_text(_json.dumps([]), encoding="utf-8")
    monkeypatch.setattr(
        harness, "run_synthetic", lambda: {"mode": "synthetic", "fixtures": {}}
    )
    monkeypatch.setattr(
        harness, "run_external", lambda *a, **k: {"mode": "external", "fixtures": {}}
    )
    for mp in module_paths:
        before = mp.read_bytes()
        # synthetic 分岐。
        monkeypatch.setattr(
            sys, "argv", ["run_melody_observability", "--out", str(mp)]
        )
        with pytest.raises(ValueError, match="保護対象"):
            harness.main()
        assert mp.read_bytes() == before  # generator モジュールは無傷。
        # external 分岐。
        monkeypatch.setattr(
            sys, "argv",
            ["run_melody_observability", "--external", str(manifest), "--out", str(mp)],
        )
        with pytest.raises(ValueError, match="保護対象"):
            harness.main()
        assert mp.read_bytes() == before


# --------------------------------------------------------------------------- #
# slow lane: 合成 → 実 pyin 抽出の統合（正/負の対照）
# --------------------------------------------------------------------------- #
@pytest.mark.slow
def test_harness_synthetic_exercises_negative_controls():
    """committed ハーネス（run_synthetic）が負の対照に pyin を当て insufficient を出す。

    負の対照が routing 短絡だけで処理されると、ゲートが抽出器 false positive を弾く
    様子が harness 出力に現れない。診断経路 `pyin_negative_control` により、和音
    パッド/ドローンに pyin を実際に当てて `insufficient` を記録することを検証する。
    """
    import scripts.run_melody_observability as harness

    results = harness.run_synthetic(_default_thresholds())
    for fid in ("synth_chord_pad", "synth_unison_drone"):
        rows = {r["route"]: r for r in results["fixtures"][fid]["routes"]}
        # routing 短絡は残る。
        assert rows["not_applicable"]["outcome"] == "not_observed_by_routing"
        # 診断 pyin 経路がゲートを実行し insufficient を出す。
        diag = rows["pyin_negative_control"]
        assert diag["outcome"] == "insufficient", diag
        assert diag["report"]["status"] == "insufficient"


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
