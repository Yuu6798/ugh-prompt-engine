"""test_s7_0b_aggregate_guards.py — 群 JSON を消費する前の不変条件（PR #303 第 2 巡 P1）。

集計器は群 JSON を**ファイル名**（`{世代}_{話者}.json`）で選び、そこに入っていた
セルを「ファイル名から導いた群」として貼り替えていた。改名・取り違え・古い probe
spec での実行が起きても素通りするため、Gate 会計が別のモデル / 話者の観測を根拠に
できてしまう。`validate_group_document` はそれを消費前に止める。

外し方 1 つにつき 1 テストで固定する。

実行: `python -m pytest voice_genesis/foundry/tests/test_s7_0b_aggregate_guards.py -q`
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

_RUN8 = Path(__file__).resolve().parent.parent / "run8"
if str(_RUN8) not in sys.path:
    sys.path.insert(0, str(_RUN8))

import s7_0b_aggregate as agg  # noqa: E402
import s7_io  # noqa: E402
import s7_spec as sp  # noqa: E402

GROUPS_DIR = _RUN8.parent / "results_s7" / "probe_0b_groups"


@pytest.fixture(scope="module")
def spec_and_sha():
    return s7_io.read_json_with_pin(agg.SPEC_PATH)[:2]


@pytest.fixture(scope="module")
def cell_ids(spec_and_sha):
    spec, _ = spec_and_sha
    return [str(c["cell_id"]) for c in spec["cells"]]


@pytest.fixture()
def group(spec_and_sha):
    """実際にコミットされている群 JSON を 1 つ読む（合成しない）。"""
    path = GROUPS_DIR / "run5_pjs.json"
    doc, _, _ = s7_io.read_json_with_pin(path)
    return copy.deepcopy(doc), path


@pytest.fixture(scope="module")
def measurement(spec_and_sha):
    return spec_and_sha[0]["measurement"]


def _check(doc, path, cell_ids, sha, meas, generation="run5", speaker="pjs"):
    agg.validate_group_document(doc, path, generation, speaker, cell_ids, sha, meas)


def test_every_committed_group_file_passes(spec_and_sha, cell_ids):
    """コミット済みの 10 群すべてが検査を通る（新検査が既存成果物を壊していない）。"""
    spec, sha = spec_and_sha
    n = 0
    for gen, info in spec["expansion"]["generations"].items():
        for speaker in info["speakers"]:
            path = GROUPS_DIR / f"{gen}_{speaker}.json"
            if not path.exists():
                continue
            doc, _, _ = s7_io.read_json_with_pin(path)
            agg.validate_group_document(
                doc, path, gen, speaker, cell_ids, sha, spec["measurement"]
            )
            n += 1
    assert n == 10, f"検査した群が {n} 個（10 群あるはず）"


def test_a_foreign_schema_is_rejected(group, cell_ids, spec_and_sha, measurement):
    doc, path = group
    doc["schema"] = sp.PROBE_GROUP_SCHEMA + "x"
    with pytest.raises(agg.GroupFileError):
        _check(doc, path, cell_ids, spec_and_sha[1], measurement)


def test_a_file_renamed_to_another_speaker_is_rejected(group, cell_ids, spec_and_sha, measurement):
    """中身は run5/pjs のまま `run5_ritsu.json` へ改名した、という取り違え。"""
    doc, path = group
    with pytest.raises(agg.GroupFileError):
        _check(doc, path, cell_ids, spec_and_sha[1], measurement, speaker="ritsu")


def test_a_file_relabeled_to_another_generation_is_rejected(group, cell_ids, spec_and_sha, measurement):
    doc, path = group
    with pytest.raises(agg.GroupFileError):
        _check(doc, path, cell_ids, spec_and_sha[1], measurement, generation="run7")


def test_a_group_run_against_an_older_probe_spec_is_rejected(group, cell_ids, spec_and_sha, measurement):
    """従来は「群どうしで一致するか」しか見ておらず、10 群すべてが同じ古い spec で
    回っていれば素通りした。"""
    doc, path = group
    doc["probe_spec_sha256"] = "0" * 64
    with pytest.raises(agg.GroupFileError):
        _check(doc, path, cell_ids, spec_and_sha[1], measurement)


def test_a_duplicated_cell_id_is_rejected(group, cell_ids, spec_and_sha, measurement):
    """`by_id` は後勝ちで畳むので、重複を許すとどちらを集計したか言えなくなる。"""
    doc, path = group
    doc["cells"].append(copy.deepcopy(doc["cells"][0]))
    with pytest.raises(agg.GroupFileError):
        _check(doc, path, cell_ids, spec_and_sha[1], measurement)


def test_a_cell_absent_from_the_preregistration_is_rejected(group, cell_ids, spec_and_sha, measurement):
    """事前登録に無いセルは MAD / 縮退判定へ黙って参加してしまう。"""
    doc, path = group
    extra = copy.deepcopy(doc["cells"][0])
    extra["cell_id"] = "HAND-ADDED|low|b1"
    doc["cells"].append(extra)
    with pytest.raises(agg.GroupFileError):
        _check(doc, path, cell_ids, spec_and_sha[1], measurement)


def test_a_partially_run_group_is_still_accepted(group, cell_ids, spec_and_sha, measurement):
    """**欠落は許す** — 部分実行は `cell_absent_from_group_file` として記帳する
    正当な状態であって、取り違えではない。"""
    doc, path = group
    doc["cells"].pop()
    _check(doc, path, cell_ids, spec_and_sha[1], measurement)


# --- 第 5 巡 P1: 群の測定 spec 束縛 -------------------------------------------


@pytest.mark.parametrize(
    "mutate, why",
    [
        (lambda ms: ms.__setitem__("sha256", "0" * 64), "凍結 spec の sha が違う"),
        (lambda ms: ms.__setitem__("spec_version", "9.9"), "spec の版が違う"),
        (
            lambda ms: ms["selected_candidates"].__setitem__(
                "excess_tail_voiced_ms", "OTHER_detector|win50|hop5"
            ),
            "別の検出器で測っている",
        ),
        (
            lambda ms: ms["epsilon"].__setitem__("excess_tail_voiced_ms", 999.0),
            "別の許容で測っている",
        ),
    ],
)
def test_a_group_measured_with_another_instrument_is_rejected(
    group, cell_ids, spec_and_sha, measurement, mutate, why
):
    """probe spec の sha が合っていても、群が別の計器・別の許容で測っていれば
    H0 は別の計器の結果になる。集計は「現行の凍結計器で出た」と名乗るので、
    ここが食い違ったまま通すと計器の主張そのものが壊れる。"""
    doc, path = group
    mutate(doc["measurement_spec"])
    with pytest.raises(agg.GroupFileError):
        _check(doc, path, cell_ids, spec_and_sha[1], measurement)


def test_the_aggregate_epsilon_comes_from_the_preregistration_not_the_last_group(
    spec_and_sha,
):
    """以前は群ごとに `epsilon` を上書きしていて、**最後に読んだ群**の値が集計全体の
    `epsilon` として出ていた。事前登録から 1 度だけ取る。"""
    spec, sha = spec_and_sha
    doc = agg.aggregate(spec, GROUPS_DIR, sha)
    want = {a: float(v["epsilon"]) for a, v in spec["measurement"]["primary_axes"].items()}
    assert doc["epsilon"] == want
