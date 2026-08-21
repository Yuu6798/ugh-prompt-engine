"""test_s7_b1_calibration.py — B-1 事前登録 / 校正ハーネス / 凍結 spec の番人。

守るのは §12-0 の**順序規律と抜け穴**であって、測定値そのものではない:

- 事前登録 3 点の schema と pin（`trf_measurement_spec.json` が記録した sha が
  実体と一致する = 「校正前に pin した JSON をそのまま使った」の機械照合）
- spec に**候補空間に無い候補が現れていない**こと（校正後に候補を足す経路の閉塞）
- Gate の primary 候補が B-1 で凍結された 4 軸のみであること（§7-0-(2b)）
- 順位付けキーに分離性能が入っていないこと + 数値キーが丸めてから比較されること
- `reference_output` の再計算一致（軽い候補で常時 / 選定候補で slow）

実行: `python -m pytest voice_genesis/foundry/tests/test_s7_b1_calibration.py -q`
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_FOUNDRY = Path(__file__).resolve().parent.parent
_RUN8 = _FOUNDRY / "run8"
if str(_RUN8) not in sys.path:
    sys.path.insert(0, str(_RUN8))

import s7_b1_calibration as b1  # noqa: E402
import s7_spec as sp  # noqa: E402

SPEC_PATH = _FOUNDRY / "results_s7" / "trf_measurement_spec.json"


@pytest.fixture(scope="module")
def prereg():
    return b1.load_prereg()


@pytest.fixture(scope="module")
def spec():
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


# --- 事前登録の形状 --------------------------------------------------------


def test_prereg_schemas_and_shape(prereg):
    assert prereg.candidate_space["window_ms"] == [100.0, 200.0, 300.0]
    assert prereg.candidate_space["hop_ms"] == [5.0, 10.0]
    assert [v["id"] for v in prereg.candidate_space["voicing"]] == [
        "A_pyin_voiced_flag",
        "B_rms_autocorr_gate",
    ]
    assert len(prereg.candidate_space["mel"]) == 3
    assert len(prereg.calibration_set["stimuli"]) == 13
    assert len(prereg.selection_rule["hard_requirements"]) == 6


def test_candidate_space_excludes_unfrozen_auxiliary_axes(prereg):
    """§7-0-(2b): 未凍結の補助軸は Gate の primary 候補に入れない。"""
    listed = set(prereg.candidate_space["out_of_scope"]["axes_not_in_b1_scope"])
    assert listed == set(sp.AUXILIARY_AXES_NOT_IN_GATE)
    assert set(prereg.candidate_space["axis_dimension_map"]) == set(sp.PRIMARY_AXES)


def test_enumerated_candidates_match_the_pinned_space(prereg):
    cands = b1.enumerate_candidates(prereg)
    assert sum(1 for c in cands if c.kind == "voicing") == 12
    assert sum(1 for c in cands if c.kind == "mel") == 3


# --- 凍結 spec と pin ------------------------------------------------------


def test_spec_pins_match_the_prereg_files_on_disk(spec, prereg):
    """spec が記録した sha が実体と一致する（校正前 pin をすり替えていない）。"""
    assert spec["prereg_pins"] == prereg.pins


def test_spec_contains_no_candidate_outside_the_pinned_space(spec, prereg):
    allowed = {c.candidate_id for c in b1.enumerate_candidates(prereg)}
    for axis, entry in spec["axes"].items():
        assert set(entry["candidate_records"]) <= allowed, axis
        if entry["selected_candidate"] is not None:
            assert entry["selected_candidate"] in allowed


def test_spec_freezes_exactly_the_four_primary_axes(spec):
    assert set(spec["axes"]) == set(sp.PRIMARY_AXES)
    assert set(spec["auxiliary_axes_not_in_gate"]) == set(sp.AUXILIARY_AXES_NOT_IN_GATE)


def test_every_frozen_axis_has_formula_unit_worked_example_and_epsilon(spec):
    for axis, entry in spec["axes"].items():
        if entry["status"] != sp.AxisStatus.FROZEN.value:
            continue
        assert entry["unit"] and entry["formula"]
        assert entry["epsilon"] is not None and entry["epsilon"] > 0
        assert axis in spec["worked_example"]
        assert axis in spec["reference_output"]


def test_epsilon_is_derived_from_measurement_side_only(spec):
    for entry in spec["axes"].values():
        if entry["status"] != sp.AxisStatus.FROZEN.value:
            continue
        d = entry["epsilon_derivation"]
        assert entry["epsilon"] == pytest.approx(
            max(d["numerical_floor"], d["reproducibility_bound"])
        )


# --- 選択規則の不変条件 ----------------------------------------------------


def test_rank_key_has_no_separation_term_and_rounds_before_comparing():
    base = {
        "candidate_id": "X",
        "hop_ms": 5.0,
        "window_ms": 100.0,
        "metrics": {
            "gain_invariance_error": 0.0,
            "silence_residual": 0.0,
            "monotone_min_step": 0.13333333333333341,
        },
    }
    other = json.loads(json.dumps(base))
    other["candidate_id"] = "Y"
    other["metrics"]["monotone_min_step"] = 0.13333333333333330
    # 5e-17 の差では順位が付かず、後続キー（ここでは candidate_id）で決まる
    assert b1.rank_key(base)[:3] == b1.rank_key(other)[:3]
    assert sorted([base, other], key=b1.rank_key)[0]["candidate_id"] == "X"


def test_selection_rule_forbids_auc(prereg):
    assert "separation" in prereg.selection_rule["prohibition"]["rule"].lower()
    keys = {k["key"] for k in prereg.selection_rule["ranking_among_survivors"]["keys"]}
    assert not (keys & {"auc", "accuracy", "margin", "youden_j"})


def test_analysis_stack_pin_is_enforced_before_measuring(prereg, monkeypatch):
    """宣言 pin と実行時の版が違えば**測る前に**停止する（PR #300 Codex P1）。

    repo には「numba 0.67.0 x numpy 2.4.6 は librosa.pyin が SIGSEGV・0.66.0 で解消」
    という実測記録があり（scripts/run5_bootstrap.py の ANALYSIS_STACK_PIN）、
    宣言と違う実装が測った値を宣言 pin の産物として記帳するのは provenance の破壊。
    """
    observed = b1.verify_analysis_stack(prereg)
    declared = {
        k: v for k, v in prereg.candidate_space["analysis_stack_pin"].items() if k != "note"
    }
    assert observed == declared

    monkeypatch.setattr(b1.importlib.metadata, "version", lambda pkg: "0.0.0")
    with pytest.raises(b1.AnalysisStackMismatch):
        b1.verify_analysis_stack(prereg)


def test_spec_records_the_observed_analysis_stack(spec):
    stack = spec["analysis_stack"]
    assert stack["verified"] is True
    assert stack["observed"] == stack["declared"]


def test_skipped_cross_process_check_is_not_a_pass():
    """`--no-cross-process` が hard requirement を通してしまわない（PR #300 Codex P2）。"""
    entry = {
        "candidate": b1.Candidate(
            candidate_id="fake", kind="voicing", voicing_id="B_rms_autocorr_gate",
            window_ms=100.0, hop_ms=5.0,
        ),
        "first": {},
        "repeat": {},
        "cross": {},
    }
    axis = "excess_tail_voiced_ms"
    stims = ("clean_terminal_ri", "clean_i", "clean_N", "long_tail_000", "long_tail_040",
             "long_tail_080", "long_tail_160", "dur_perturb_r020", "dur_perturb_r035",
             "dur_perturb_r050", "silence", "gain_x100", "gain_x050")
    values = {"long_tail_000": 0.0, "long_tail_040": 40.0, "long_tail_080": 80.0,
              "long_tail_160": 160.0, "silence": 0.0, "gain_x100": 80.0, "gain_x050": 80.0}
    for s in stims:
        entry["first"][s] = {axis: values.get(s, 10.0)}
    prereg_local = b1.load_prereg()
    record = b1.evaluate_axis_candidate(axis, entry, prereg_local)
    assert record["checks"]["cross_process_reproducibility"] is False
    assert record["survives"] is False


# --- 測定の物理的性質（軽い候補で常時検査） --------------------------------


@pytest.fixture(scope="module")
def stimuli(prereg):
    return b1.build_calibration_set(prereg)


@pytest.fixture(scope="module")
def light_candidate(prereg):
    return next(
        c
        for c in b1.enumerate_candidates(prereg)
        if c.voicing_id == "B_rms_autocorr_gate" and c.window_ms == 100.0 and c.hop_ms == 5.0
    )


def test_silence_measures_zero(stimuli, light_candidate):
    out = b1.measure_candidate(light_candidate, stimuli["silence"])
    assert out["excess_tail_voiced_ms"] == 0.0
    assert out["release_after_score_boundary_ms"] == 0.0
    assert out["tail_f0_persistence"] == 0.0


def test_monotone_ladder_increases(stimuli, light_candidate):
    values = [
        b1.measure_candidate(light_candidate, stimuli[s])["excess_tail_voiced_ms"]
        for s in ("long_tail_000", "long_tail_040", "long_tail_080", "long_tail_160")
    ]
    assert values == sorted(values) and values[-1] > values[0]


def test_measurement_is_reproducible_in_process(stimuli, light_candidate):
    a = b1.measure_candidate(light_candidate, stimuli["long_tail_080"])
    b = b1.measure_candidate(light_candidate, stimuli["long_tail_080"])
    assert a == b


def test_mel_axis_is_gain_invariant(stimuli, prereg):
    mel = next(c for c in b1.enumerate_candidates(prereg) if c.kind == "mel")
    hi = b1.measure_candidate(mel, stimuli["gain_x100"])["terminal_mel_persistence"]
    lo = b1.measure_candidate(mel, stimuli["gain_x050"])["terminal_mel_persistence"]
    assert hi == pytest.approx(lo, abs=1e-9)


def test_reference_output_reproduces_for_the_mel_axis(spec, stimuli, prereg):
    entry = spec["axes"]["terminal_mel_persistence"]
    if entry["status"] != sp.AxisStatus.FROZEN.value:
        pytest.skip("mel axis is not frozen")
    cand = next(
        c for c in b1.enumerate_candidates(prereg) if c.candidate_id == entry["selected_candidate"]
    )
    for stim_id, expected in spec["reference_output"]["terminal_mel_persistence"].items():
        got = b1.measure_candidate(cand, stimuli[stim_id])["terminal_mel_persistence"]
        assert round(got, 9) == pytest.approx(expected, abs=1e-9), stim_id


@pytest.mark.slow
def test_reference_output_reproduces_for_every_frozen_axis(spec, stimuli, prereg):
    """選定候補が pyin 側でも `reference_output` が再現することを確かめる（重い）。"""
    for axis, entry in spec["axes"].items():
        if entry["status"] != sp.AxisStatus.FROZEN.value:
            continue
        cand = next(
            c
            for c in b1.enumerate_candidates(prereg)
            if c.candidate_id == entry["selected_candidate"]
        )
        for stim_id, expected in spec["reference_output"][axis].items():
            got = b1.measure_candidate(cand, stimuli[stim_id])[axis]
            assert round(got, 9) == pytest.approx(expected, abs=1e-9), f"{axis}/{stim_id}"


def test_b1_has_no_path_to_production_or_label_data():
    """B-1 が本番セル / 聴取ラベルへ到達する入力口を持たないことの機械検査。

    `s7_b1_selection_rule.json` の `prohibition.machine_check` に対応する検査で、
    実体は**このテスト**である（`select_candidates` 内の実行時 assert ではない）。
    読み込む実ファイルが事前登録 3 点だけであることを固定する。
    """
    source = (_RUN8 / "s7_b1_calibration.py").read_text(encoding="utf-8")
    forbidden_paths = (
        "results_s3",
        "results_s4",
        "results_s5",
        "results_s6",
        "planb",
        "genome_s3",
        "s7_post_listening_set",
        "s7_listening",
        "target_exposure_ledger",
    )
    hits = [token for token in forbidden_paths if token in source]
    assert hits == [], f"B-1 が参照してはならないパスを含む: {hits}"
    # 実ファイルを読むのは load_prereg の 3 呼び出し（事前登録 3 点）だけ
    assert source.count("read_text(") == 3
    for name in ("candidate_space_path", "calibration_set_path", "selection_rule_path"):
        assert f"{name}.read_text(" in source
    assert source.count("read_bytes(") == 1         # sha256_file の pin 計算だけ
