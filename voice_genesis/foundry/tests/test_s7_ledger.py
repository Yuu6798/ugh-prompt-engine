"""test_s7_ledger.py — `run8/s7_ledger.py`（`target_exposure_ledger/0.1`）の形状 / 契約テスト。

重点は「設計書 §3 が事前登録した数え方から実装がずれないこと」。特に:

- 終端イベントの層キーが **密度の式と完全に同一**（分子と分母で別のキーを使わない）
- `events` 内訳が主層（`utterance_final` × `ri_to_SP`）に必ず付く
- §3 の worked example が**そのまま再現**する（`d3`/`d4` を分けると
  `overall = undetermined`、合算すると `supported` に見えるという差が出る）

実行: `python -m pytest voice_genesis/foundry/tests/test_s7_ledger.py -q`
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

import pytest

_RUN8 = Path(__file__).resolve().parent.parent / "run8"
if str(_RUN8) not in sys.path:
    sys.path.insert(0, str(_RUN8))

import s7_ledger as L  # noqa: E402
import s7_spec as sp  # noqa: E402


def _write_csv(tmp_path: Path, name: str, rows: list[str], header: str) -> Path:
    path = tmp_path / f"{name}.csv"
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


HEADER_FULL = "name,ph_seq,ph_dur,ph_num,note_seq,note_dur"
HEADER_MIN = "name,ph_seq,ph_dur"


# --- 終端イベント抽出 ------------------------------------------------------


def test_terminal_event_classification_and_position():
    row = L.Row(
        name="r1",
        ph_seq=("SP", "k", "a", "g", "i", "r", "i", "SP", "N", "SP"),
        ph_dur=(0.1, 0.2, 0.2, 0.2, 0.2, 0.3, 1.4, 0.3, 0.6, 0.2),
    )
    events = L.extract_terminal_events("user", row)
    assert [e.transition for e in events] == ["ri_to_SP", "N_to_SP"]
    # 最初の SP の後ろに音素が続くので internal、最後は utterance_final
    assert [e.position for e in events] == ["internal", "utterance_final"]
    ri = events[0]
    assert ri.onset_phone_seconds == pytest.approx(0.3)
    assert ri.vowel_phone_seconds == pytest.approx(1.4)
    assert ri.preceding_duration_seconds == pytest.approx(1.7)
    assert ri.r_ratio == pytest.approx(0.3 / 1.7)
    assert ri.event_id == "user/r1/6"


def test_duration_bins_are_seconds_not_beats():
    assert L.duration_bin(0.4) == "d0"
    assert L.duration_bin(0.5) == "d1"
    assert L.duration_bin(1.0) == "d2"
    assert L.duration_bin(1.5) == "d3"
    assert L.duration_bin(2.4999) == "d3"
    assert L.duration_bin(2.5) == "d4"
    assert L.duration_bin(3.333) == "d4"   # 既知の破綻例「り」3.333 s
    assert L.duration_bin(1.667) == "d3"   # 既知の破綻例「す」1.667 s


def test_ph_seq_length_mismatch_is_fail_closed(tmp_path: Path):
    path = _write_csv(tmp_path, "bad", ["b1,SP a SP,0.1 0.2"], HEADER_MIN)
    with pytest.raises(ValueError):
        L.parse_rows(path)


def test_numerator_and_denominator_share_the_same_stratum_key(tmp_path: Path):
    path = _write_csv(
        tmp_path,
        "user",
        [
            "u1,SP k a g i r i SP,0.1 0.2 0.2 0.2 0.2 0.3 1.4 0.3,1 1 1 1 2 1,"
            "rest C4 D4 E4 F4 rest,0.1 0.2 0.2 0.2 1.7 0.3",
            "u2,SP m i w a t a s u SP,0.1 0.2 0.2 0.2 0.2 0.2 0.2 0.2 1.5 0.2,"
            "1 1 1 1 1 1 1 2 1,rest C4 D4 E4 F4 G4 A4 B4 rest,"
            "0.1 0.2 0.2 0.2 0.2 0.2 0.2 1.7 0.2",
        ],
        HEADER_FULL,
    )
    section = L.build_speaker_section(L.SpeakerInput("user", "real_song", path))
    assert set(section["terminal_events"]) <= set(section["denominator"])
    for key, transitions in section["terminal_events"].items():
        total = sum(cell["count"] for cell in transitions.values())
        assert total <= section["denominator"][key]["eligible_terminal_count"]
    # 主層のセルには event-level 内訳が必ず付く（§3・2026-08-21 追加）
    for key, transitions in section["terminal_events"].items():
        if sp.PRIMARY_POSITION in key and sp.PRIMARY_TRANSITION in transitions:
            events = transitions[sp.PRIMARY_TRANSITION]["events"]
            assert events and all("r_ratio" in e for e in events)


def test_pitch_bin_unknown_is_recorded_not_folded_into_mid(tmp_path: Path):
    path = _write_csv(
        tmp_path,
        "ritsu",
        ["v1,SP r i SP,0.1 0.3 1.6 0.2", "v2,SP r i SP,0.1 0.3 1.7 0.2"],
        HEADER_MIN,
    )
    section = L.build_speaker_section(L.SpeakerInput("ritsu", "VCV", path))
    assert section["pitch_bin_assignment"]["unknown_events"] == 2
    assert all("|unknown|" in key for key in section["denominator"])


# --- §3 worked example -----------------------------------------------------


def _section(modality: str, per_bin: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
    """worked example の表 1 行分（`{d3: {count, eligible}}`）から話者セクションを作る。"""
    terminal_events: Dict[str, Any] = {}
    denominator: Dict[str, Any] = {}
    for dbin, values in per_bin.items():
        key = f"{modality}|{sp.PRIMARY_POSITION}|mid|{dbin}"
        terminal_events[key] = {
            sp.PRIMARY_TRANSITION: {"count": values["count"], "duration_seconds": 0.0}
        }
        denominator[key] = {
            "eligible_terminal_count": values["eligible"],
            "eligible_terminal_seconds": 0.0,
        }
    return {"modality": modality, "terminal_events": terminal_events, "denominator": denominator}


WORKED_SPEAKERS = {
    "pjs": _section("real_song", {"d3": {"count": 7, "eligible": 90}, "d4": {"count": 5, "eligible": 60}}),
    "user": _section("real_song", {"d3": {"count": 1, "eligible": 25}, "d4": {"count": 0, "eligible": 15}}),
    "ritsu": _section("VCV", {"d3": {"count": 0, "eligible": 500}, "d4": {"count": 0, "eligible": 400}}),
}


def test_section_3_worked_example_reproduces_exactly():
    verdict = L.httd_verdict(WORKED_SPEAKERS, breaking=["user"], non_breaking=["pjs"])
    mid_d3 = verdict["per_stratum"]["mid/d3"]
    mid_d4 = verdict["per_stratum"]["mid/d4"]
    # mid/d3: pjs 0.0778 vs user 0.0400 -> 順序は成立するが比 1.94 < 2.0 -> refuted
    assert mid_d3["verdict"] == sp.Verdict.REFUTED.value
    assert mid_d3["ratio"] == pytest.approx(1.944444, abs=1e-5)
    assert mid_d3["excluded_speakers"] == ["ritsu"]      # VCV = modality またぎで除外
    # mid/d4: user の eligible 15 < 20 -> 標本不足で undetermined
    assert mid_d4["verdict"] == sp.Verdict.UNDETERMINED.value
    assert mid_d4["reason"] == "insufficient_sample"
    # overall: scored が 1 層しかない -> undetermined
    assert verdict["scored_strata"] == 1
    assert verdict["overall"] == sp.Verdict.UNDETERMINED.value


def test_aggregating_d3_and_d4_would_flip_the_verdict():
    """§3 が「例と契約の表記を揃えることは体裁の問題ではない」と述べた差の再現。

    `d3+` へ合算すると pjs 12/150 vs user 1/40 = 比 3.2 で **supported に見える**。
    層を分けて数える契約では `undetermined` である（上のテスト）。
    """
    merged = {
        "pjs": _section("real_song", {"d3": {"count": 12, "eligible": 150}}),
        "user": _section("real_song", {"d3": {"count": 1, "eligible": 40}}),
    }
    stratum = L.httd_verdict(merged, breaking=["user"], non_breaking=["pjs"])["per_stratum"]["mid/d3"]
    assert stratum["verdict"] == sp.Verdict.SUPPORTED.value
    assert stratum["ratio"] == pytest.approx(3.2, abs=1e-6)


def test_zero_density_branches_do_not_divide_by_zero():
    speakers = {
        "pjs": _section("real_song", {"d3": {"count": 9, "eligible": 90}}),
        "user": _section("real_song", {"d3": {"count": 0, "eligible": 30}}),
    }
    v = L.httd_verdict(speakers, breaking=["user"], non_breaking=["pjs"])["per_stratum"]["mid/d3"]
    assert v["ratio"] == sp.RATIO_SEPARATED_BY_ZERO      # inf を書かない
    assert v["verdict"] == sp.Verdict.SUPPORTED.value

    all_zero = {
        "pjs": _section("real_song", {"d3": {"count": 0, "eligible": 90}}),
        "user": _section("real_song", {"d3": {"count": 0, "eligible": 30}}),
    }
    v0 = L.httd_verdict(all_zero, breaking=["user"], non_breaking=["pjs"])["per_stratum"]["mid/d3"]
    assert v0["verdict"] == sp.Verdict.UNDETERMINED.value
    assert v0["reason"] == "all_zero_density"


def test_cross_modality_only_stratum_is_undetermined():
    speakers = {
        "pjs": _section("real_song", {"d3": {"count": 9, "eligible": 90}}),
        "ritsu": _section("VCV", {"d3": {"count": 0, "eligible": 500}}),
    }
    v = L.httd_verdict(speakers, breaking=["ritsu"], non_breaking=["pjs"])["per_stratum"]["mid/d3"]
    assert v["verdict"] == sp.Verdict.UNDETERMINED.value
    assert v["reason"] == "no_same_modality_comparison"


def test_unclassified_speakers_never_produce_a_verdict():
    speakers = {
        "pjs": _section("real_song", {"d3": {"count": 9, "eligible": 90}}),
        "amitaro": _section("real_song", {"d3": {"count": 1, "eligible": 90}}),
    }
    v = L.httd_verdict(speakers, breaking=[], non_breaking=["pjs"])["per_stratum"]["mid/d3"]
    assert v["verdict"] == sp.Verdict.UNDETERMINED.value


def test_breath_token_counts_as_terminal_but_stays_separable(tmp_path: Path):
    """`AP`（breath）も終端として数えるが、内訳で分離できることを固定する。"""
    path = _write_csv(
        tmp_path,
        "pjs",
        [
            "p1,SP h i k a r i AP k a SP,0.1 0.2 0.2 0.2 0.2 0.3 1.6 0.2 0.2 0.2 0.2",
            "p2,SP h i k a r i SP,0.1 0.2 0.2 0.2 0.2 0.3 1.7 0.2",
        ],
        HEADER_MIN,
    )
    section = L.build_speaker_section(L.SpeakerInput("pjs", "real_song", path))
    assert section["silence_token_counts"] == {"AP": 1, "SP": 2}
    tokens = {
        event["silence_token"]
        for transitions in section["terminal_events"].values()
        for cell in transitions.values()
        for event in cell.get("events", [])
    }
    assert tokens == {"SP"}      # AP 直前の /ri/ は internal なので主層に入らない
