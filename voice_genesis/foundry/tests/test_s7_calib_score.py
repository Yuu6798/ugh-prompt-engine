"""test_s7_calib_score.py — 校正専用 real-render セットの譜面/命令の番人。

守るのは「事前登録どおりの条件を、事前登録どおりの命令で鳴らしたか」であって
音の良し悪しではない:

- レンダ条件が事前登録 `real_render_set` と 1:1（派生 gain は再レンダしない）
- 音素表と事前登録 `score` 記述の相互照合（表だけ書き換える事故の閉塞）
- override の不変量（延長は総和を増やす / 配分は総和を変えない）
- 境界が**命令から**算出されていること（波形非依存）
- 実レンダ manifest の 3 段照合（事前登録 sha / 条件集合 / WAV sha）が
  fail-closed であること

実行: `python -m pytest voice_genesis/foundry/tests/test_s7_calib_score.py -q`
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

_FOUNDRY = Path(__file__).resolve().parent.parent
_RUN8 = _FOUNDRY / "run8"
if str(_RUN8) not in sys.path:
    sys.path.insert(0, str(_RUN8))

import s7_b1_calibration as b1  # noqa: E402
import s7_calib_score as cs  # noqa: E402
import s7_io  # noqa: E402
import s7_spec as sp  # noqa: E402

CALIBRATION_SET_PATH = _FOUNDRY / "results_s7" / "s7_b1_calibration_set.json"


@pytest.fixture(scope="module")
def rrs():
    doc = json.loads(CALIBRATION_SET_PATH.read_text(encoding="utf-8"))
    return doc["real_render_set"]


@pytest.fixture(scope="module")
def renders(rrs):
    return cs.build_all_renders(rrs)


def test_render_count_matches_prereg(rrs, renders):
    assert len(renders) == int(rrs["n_renders"])
    derived = cs.derived_conditions(rrs)
    assert len(renders) + len(derived) == len(rrs["conditions"])
    assert {c["id"] for c in derived} == {"rr_gain_x100", "rr_gain_x050"}


def test_render_ids_are_exactly_prereg_ids(rrs, renders):
    prereg_ids = [str(c["id"]) for c in rrs["conditions"] if "derived_from" not in c]
    assert [r.condition_id for r in renders] == prereg_ids


def test_mora_table_covers_only_prereg_conditions(rrs):
    prereg_ids = {str(c["id"]) for c in rrs["conditions"] if "derived_from" not in c}
    assert set(cs.MORA_PHONES) == prereg_ids


def test_prereg_score_text_and_phone_table_agree(rrs):
    """表を書き換えて別の音素を鳴らす（= 事前登録の意味が後から変わる）経路の閉塞。"""
    previous = None
    for cond in rrs["conditions"]:
        if "derived_from" in cond:
            continue
        cs.assert_consistent_with_prereg(cond, previous)
        previous = cs.MORA_PHONES[str(cond["id"])]


def test_phone_table_mismatch_is_rejected():
    with pytest.raises(ValueError):
        cs.assert_consistent_with_prereg(
            {"id": "rr_clean_i", "score": "終端モーラ /ri/ + SP"}
        )


def test_continuation_without_predecessor_is_rejected():
    with pytest.raises(ValueError):
        cs.assert_consistent_with_prereg({"id": "rr_long_tail_040", "score": "同上"}, None)


def test_boundaries_are_computed_from_commands(renders):
    for r in renders:
        assert r.note_onset_s == pytest.approx(cs.HEAD_FRAMES * cs.FRAME_MS / 1000.0)
        expected_boundary = r.note_onset_s + cs.beats_to_seconds(r.beats, r.tempo_bpm)
        assert r.score_boundary_s == pytest.approx(expected_boundary)
        expected_cmd = r.note_onset_s + sum(r.note_target_frames) * cs.FRAME_MS / 1000.0
        assert r.commanded_note_end_s == pytest.approx(expected_cmd)


def test_ladder_shares_score_and_boundaries(renders):
    """梯子は**同じ譜面・同じ境界**で、終端延長量だけが増える（合成側と同型）。"""
    ladder = [r for r in renders if r.condition_id.startswith("rr_long_tail_")]
    assert len(ladder) == 4
    assert len({r.score_boundary_s for r in ladder}) == 1
    assert len({r.commanded_note_end_s for r in ladder}) == 1
    assert len({tuple(tuple(cs._phones_of(n.mora)) for n in r.notes) for r in ladder}) == 1
    frames = [r.terminal_extension_frames for r in ladder]
    assert frames == sorted(frames) and frames[0] == 0 and frames[-1] > frames[0]


def test_tail_extension_override_only_grows_the_last_phone():
    override = cs.make_tail_extension_override(7)
    ctx = {"note_phone_counts": [1], "note_target_frames": [108], "real_phones": ["i"]}
    out = override([108], ctx)
    assert out == [115]


def test_onset_ratio_override_preserves_total():
    ctx = {"note_phone_counts": [2], "note_target_frames": [108], "real_phones": ["r", "i"]}
    for ratio in (0.2, 0.35, 0.5):
        out = cs.make_onset_ratio_override(ratio)([20, 88], ctx)
        assert sum(out) == 108
        assert out[0] == max(1, min(107, round(ratio * 108)))
        assert min(out) >= 1


def test_onset_ratio_override_rejects_single_phone_terminal():
    ctx = {"note_phone_counts": [1], "note_target_frames": [108], "real_phones": ["i"]}
    with pytest.raises(ValueError):
        cs.make_onset_ratio_override(0.35)([108], ctx)


def test_clean_conditions_have_no_override(renders):
    for r in renders:
        expect_override = r.terminal_extension_frames > 0 or r.onset_ratio is not None
        assert (r.override is not None) == expect_override
        if r.condition_id.startswith(("rr_clean_", "rr_silence", "rr_long_tail_000")):
            assert r.override is None


def test_padded_total_covers_boundary_plus_tail_window(renders):
    n = cs.padded_total_samples(renders)
    for r in renders:
        need = (r.score_boundary_s + r.tail_window_ms / 1000.0) * cs.SAMPLE_RATE
        assert n >= need


def test_silence_condition_is_sp_only(renders):
    sil = next(r for r in renders if r.condition_id == cs.SILENCE_ID)
    assert [cs._phones_of(n.mora) for n in sil.notes] == [["SP"]]
    assert sil.pitch_midi_prereg is None
    assert sil.pitch_midi_applied == cs.SILENCE_PITCH_MIDI_APPLIED


# --- 実レンダ manifest の 3 段照合 ------------------------------------------


def _fake_manifest(tmp_path: Path, prereg_sha: str, rrs) -> Path:
    out_dir = tmp_path / "wav"
    out_dir.mkdir()
    conditions = []
    ids = [(str(c["id"]), str(c["maps_to"])) for c in rrs["conditions"]]
    ids += [(str(z["id"]), "synthetic_zero_buffer") for z in rrs.get("zero_buffers", [])]
    for cid, maps_to in ids:
        y = np.zeros(1024, dtype=np.float32)
        wav = out_dir / f"{cid}.wav"
        sf.write(str(wav), y, cs.SAMPLE_RATE, subtype="FLOAT")
        _, sha, _ = s7_io.read_bytes_with_pin(wav)
        conditions.append(
            {
                "condition_id": cid,
                "maps_to": maps_to,
                "wav": wav.name,
                "wav_sha256": sha,
                # 標本 sha は**必須**（PR #303 レビュー対応で optional をやめた。
                # キーが無いだけで最強の照合が黙って飛ぶのは穴だった）。
                "samples_sha256": s7_io.sha256_bytes(np.ascontiguousarray(y).tobytes()),
                "note_onset_s": 0.1,
                "commanded_note_end_s": 1.0,
                "score_boundary_s": 1.0,
                "tail_window_ms": 300.0,
            }
        )
    manifest = {
        "schema": sp.REAL_RENDER_MANIFEST_SCHEMA,
        "prereg": {"sha256": prereg_sha},
        "out_dir": str(out_dir),
        "conditions": conditions,
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return path


def test_real_render_source_accepts_a_consistent_manifest(tmp_path, rrs):
    prereg = b1.load_prereg()
    path = _fake_manifest(tmp_path, prereg.pins[CALIBRATION_SET_PATH.name], rrs)
    source = b1.real_render_source(prereg, path)
    assert source.name == "real_render_v1"
    expected = {str(c["id"]) for c in rrs["conditions"]}
    expected |= {str(z["id"]) for z in rrs["zero_buffers"]}
    assert set(source.stimuli) == expected
    assert source.roles is rrs["roles"] or source.roles == rrs["roles"]
    assert CALIBRATION_SET_PATH.name in source.extra_pins or source.extra_pins


def test_real_render_source_rejects_stale_prereg_pin(tmp_path, rrs):
    prereg = b1.load_prereg()
    path = _fake_manifest(tmp_path, "0" * 64, rrs)
    with pytest.raises(b1.RealRenderManifestError):
        b1.real_render_source(prereg, path)


def test_real_render_source_rejects_tampered_wav(tmp_path, rrs):
    prereg = b1.load_prereg()
    path = _fake_manifest(tmp_path, prereg.pins[CALIBRATION_SET_PATH.name], rrs)
    doc = json.loads(path.read_text(encoding="utf-8"))
    victim = Path(doc["out_dir"]) / doc["conditions"][0]["wav"]
    sf.write(str(victim), np.ones(1024, dtype=np.float32), cs.SAMPLE_RATE, subtype="FLOAT")
    with pytest.raises(b1.RealRenderManifestError):
        b1.real_render_source(prereg, path)


def test_real_render_source_rejects_missing_condition(tmp_path, rrs):
    prereg = b1.load_prereg()
    path = _fake_manifest(tmp_path, prereg.pins[CALIBRATION_SET_PATH.name], rrs)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["conditions"] = doc["conditions"][:-1]
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(b1.RealRenderManifestError):
        b1.real_render_source(prereg, path)


# --- 2026-08-21 amendment: zero_input_false_positive -------------------------


def test_zero_input_requirement_replaced_silence_zero(rrs):
    """hard requirement と役割の改称が事前登録側で完了していること。"""
    rule = json.loads((_FOUNDRY / "results_s7" / "s7_b1_selection_rule.json").read_text("utf-8"))
    ids = [r["id"] for r in rule["hard_requirements"]]
    assert sp.ZERO_INPUT_REQUIREMENT in ids
    assert "silence_zero" not in ids
    cal = json.loads((_FOUNDRY / "results_s7" / "s7_b1_calibration_set.json").read_text("utf-8"))
    for roles in (cal["roles"], cal["real_render_set"]["roles"]):
        assert sp.ZERO_INPUT_REQUIREMENT in roles
        assert "silence_zero" not in roles


def test_ranking_keys_were_not_touched_by_the_amendment():
    """ranking key は改称に巻き込まない（User 裁定「ranking key も従来どおり」）。"""
    rule = json.loads((_FOUNDRY / "results_s7" / "s7_b1_selection_rule.json").read_text("utf-8"))
    keys = [k["key"] for k in rule["ranking_among_survivors"]["keys"]]
    assert keys == [
        "gain_invariance_error",
        "silence_residual",
        "monotone_min_step",
        "hop_ms",
        "window_ms",
        "candidate_id",
    ]


def test_rr_silence_is_a_diagnostic_not_a_requirement(rrs):
    """`rr_silence` は削除せず、pass/fail から外れていること。"""
    ids = {str(c["id"]) for c in rrs["conditions"]}
    assert "rr_silence" in ids
    assert rrs["roles"][sp.ZERO_INPUT_REQUIREMENT] == ["rr_zero_buffer"]
    for role, members in rrs["roles"].items():
        flat = members if isinstance(members, list) else []
        flat = [m for grp in flat for m in (grp if isinstance(grp, list) else [grp])]
        assert "rr_silence" not in flat, role
    diag = {d["id"]: d for d in rrs["diagnostics"]}
    assert diag[sp.PIPELINE_SILENCE_DIAGNOSTIC]["stimulus"] == "rr_silence"
    assert diag[sp.PIPELINE_SILENCE_DIAGNOSTIC]["used_for_pass_fail"] is False


def test_zero_buffer_must_be_bit_exact_zero(tmp_path, rrs):
    """厳密ゼロでない緩衝を掴まされたら測らない（fail-closed）。"""
    prereg = b1.load_prereg()
    path = _fake_manifest(tmp_path, prereg.pins[CALIBRATION_SET_PATH.name], rrs)
    doc = json.loads(path.read_text(encoding="utf-8"))
    victim = Path(doc["out_dir"]) / "rr_zero_buffer.wav"
    y = np.zeros(1024, dtype=np.float32)
    y[500] = 1e-9
    sf.write(str(victim), y, cs.SAMPLE_RATE, subtype="FLOAT")
    for cond in doc["conditions"]:
        if cond["condition_id"] == "rr_zero_buffer":
            _, sha, _ = s7_io.read_bytes_with_pin(victim)
            cond["wav_sha256"] = sha
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(b1.RealRenderManifestError):
        b1.real_render_source(prereg, path)


def test_real_render_source_rejects_a_manifest_without_sample_pins(tmp_path, rrs):
    """`samples_sha256` が無い manifest は測らない。

    以前はこのキーを optional 扱いしていたので、**キーを消すだけで**標本照合を
    回避できた（容器 sha は再レンダで必ず変わるので、実質「照合なし」になる）。
    PR #303 レビュー対応で必須にした。
    """
    prereg = b1.load_prereg()
    path = _fake_manifest(tmp_path, prereg.pins[CALIBRATION_SET_PATH.name], rrs)
    doc = json.loads(path.read_text(encoding="utf-8"))
    for c in doc["conditions"]:
        c.pop("samples_sha256")
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(b1.RealRenderManifestError):
        b1.real_render_source(prereg, path)
