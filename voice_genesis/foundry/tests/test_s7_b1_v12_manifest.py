"""test_s7_b1_v12_manifest.py — 1.2 校正音源 manifest の**事前登録への結び付け**。

PR #303 レビュー第 1 巡 P1: `source_12` は「振幅 rung の ID が入っていること」しか
見ておらず、schema・事前登録 sha・条件集合の厳密一致・重複 ID を検査していなかった。
手編集や古い manifest でも通り、**別の条件で測った値に現行の事前登録 pin を貼った
成果物**が出せてしまう（1.0 の `real_render_source` は同じ検査を持っていた）。

ここでは実レンダを要求しない。合成 WAV で manifest を組み立て、
**外し方 1 つにつき 1 テスト**で fail-closed を固定する。

実行: `python -m pytest voice_genesis/foundry/tests/test_s7_b1_v12_manifest.py -q`
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pytest

_RUN8 = Path(__file__).resolve().parent.parent / "run8"
if str(_RUN8) not in sys.path:
    sys.path.insert(0, str(_RUN8))

import s7_b1_v12 as v12  # noqa: E402
import s7_io  # noqa: E402
import s7_spec as sp  # noqa: E402


@pytest.fixture(scope="module")
def prereg():
    cs, cal, rule, pins = v12.load_prereg_12()
    return cal


@pytest.fixture(scope="module")
def cal_1_0():
    doc, sha, _ = s7_io.read_json_with_pin(v12.b1.CALIBRATION_SET_PATH)
    return doc, sha


def _parent_window() -> Dict[str, Any]:
    """pin 済み親 manifest の測定窓（継承条件はこれと一致しなければならない）。"""
    parent, _, _ = s7_io.read_json_with_pin(v12.PARENT_MANIFEST_PATH)
    c = parent["conditions"][0]
    return {
        k: c[k] for k in
        ("note_onset_s", "commanded_note_end_s", "score_boundary_s",
         "tail_window_ms", "beats", "tempo_bpm")
    }


_PARENT_WINDOW = _parent_window()


def _build_manifest(tmp_path: Path, cal: Dict[str, Any], cal_1_0_doc) -> Path:
    """事前登録どおりの条件集合を持つ、**合成波形の** 1.2 manifest を組む。"""
    import soundfile as sf

    out_dir = tmp_path / "wav"
    out_dir.mkdir()
    _, cal_1_2_sha, _ = s7_io.read_json_with_pin(v12.CALIBRATION_SET_PATH)
    zero_ids = {str(z["id"]) for z in cal_1_0_doc["real_render_set"].get("zero_buffers", [])}

    conditions = []
    for i, cid in enumerate(v12._expected_condition_ids_12(cal, cal_1_0_doc)):
        n = 4410
        if cid in zero_ids:
            y = np.zeros(n, dtype=np.float32)
        else:
            t = np.arange(n, dtype=np.float32) / 44100.0
            y = (0.1 * np.sin(2 * np.pi * (220.0 + i) * t)).astype(np.float32)
        wav = out_dir / f"{cid}.wav"
        sf.write(str(wav), y, 44100, subtype="FLOAT")
        conditions.append({
            "condition_id": cid,
            "wav": wav.name,
            "wav_sha256": s7_io.sha256_bytes(wav.read_bytes()),
            "samples_sha256": s7_io.sha256_bytes(np.ascontiguousarray(y).tobytes()),
            "maps_to": "synthetic_for_test",
            **_PARENT_WINDOW,
        })

    doc = {
        "schema": sp.REAL_RENDER_MANIFEST_SCHEMA_1_2,
        "out_dir": str(out_dir),
        "conditions": conditions,
        "calibration_set_1_2": {"path": v12.CALIBRATION_SET_PATH.name, "sha256": cal_1_2_sha},
        "extends": {
            "manifest": "synthetic",
            "sha256": str(cal["inherits"]["real_render_manifest"]["sha256"]),
        },
        "render_path": {"model_sha256": {"synthetic": "0" * 64}},
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def _rewrite(path: Path, mutate) -> Path:
    doc = json.loads(path.read_text(encoding="utf-8"))
    mutate(doc)
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def test_a_manifest_matching_the_preregistration_is_accepted(tmp_path, prereg, cal_1_0):
    path = _build_manifest(tmp_path, prereg, cal_1_0[0])
    src = v12.source_12(path, prereg)
    expected = v12._expected_condition_ids_12(prereg, cal_1_0[0])
    assert sorted(src["stimuli"]) == sorted(expected)


def test_a_foreign_schema_is_rejected(tmp_path, prereg, cal_1_0):
    path = _build_manifest(tmp_path, prereg, cal_1_0[0])
    _rewrite(path, lambda d: d.update(schema=sp.REAL_RENDER_MANIFEST_SCHEMA))
    with pytest.raises(v12.Manifest12Error):
        v12.source_12(path, prereg)


def test_a_manifest_rendered_against_another_preregistration_is_rejected(
    tmp_path, prereg, cal_1_0
):
    path = _build_manifest(tmp_path, prereg, cal_1_0[0])
    _rewrite(path, lambda d: d["calibration_set_1_2"].update(sha256="f" * 64))
    with pytest.raises(v12.Manifest12Error):
        v12.source_12(path, prereg)


def test_a_manifest_extending_another_parent_batch_is_rejected(tmp_path, prereg, cal_1_0):
    """親 manifest が事前登録の pin と違う = **別バッチのレンダを継承している**。"""
    path = _build_manifest(tmp_path, prereg, cal_1_0[0])
    _rewrite(path, lambda d: d["extends"].update(sha256="a" * 64))
    with pytest.raises(v12.Manifest12Error):
        v12.source_12(path, prereg)


def test_a_hand_edited_extra_condition_is_rejected(tmp_path, prereg, cal_1_0):
    """rung ID が全部揃っていても、**余計な条件**が混ざっていたら測らない
    （旧実装の部分集合検査はこれを通していた）。"""
    path = _build_manifest(tmp_path, prereg, cal_1_0[0])

    def _add(d):
        extra = dict(d["conditions"][0])
        extra["condition_id"] = "rr_hand_added"
        d["conditions"].append(extra)

    _rewrite(path, _add)
    with pytest.raises(v12.Manifest12Error):
        v12.source_12(path, prereg)


def test_a_missing_base_condition_is_rejected(tmp_path, prereg, cal_1_0):
    path = _build_manifest(tmp_path, prereg, cal_1_0[0])
    _rewrite(path, lambda d: d["conditions"].pop(0))
    with pytest.raises(v12.Manifest12Error):
        v12.source_12(path, prereg)


def test_a_duplicated_condition_id_is_rejected(tmp_path, prereg, cal_1_0):
    """同じ ID が 2 回出ると、後勝ちで**どちらを測ったか言えなくなる**。"""
    path = _build_manifest(tmp_path, prereg, cal_1_0[0])
    _rewrite(path, lambda d: d["conditions"].append(dict(d["conditions"][0])))
    with pytest.raises(v12.Manifest12Error):
        v12.source_12(path, prereg)


def test_a_swapped_wav_is_rejected(tmp_path, prereg, cal_1_0):
    path = _build_manifest(tmp_path, prereg, cal_1_0[0])
    doc = json.loads(path.read_text(encoding="utf-8"))
    target = doc["conditions"][0]
    import soundfile as sf

    sf.write(str(Path(doc["out_dir"]) / target["wav"]),
             np.zeros(4410, dtype=np.float32), 44100, subtype="FLOAT")
    with pytest.raises(v12.Manifest12Error):
        v12.source_12(path, prereg)


# --- 第 9 巡 P1: 測定窓のメタデータ -------------------------------------------


def test_a_hand_edited_score_boundary_is_rejected(tmp_path, prereg, cal_1_0):
    """条件 ID と系譜が合っていても、`score_boundary_s` を書き換えれば
    **B-1 は別の窓を測る**。ID を見るだけでは足りない。"""
    path = _build_manifest(tmp_path, prereg, cal_1_0[0])
    _rewrite(path, lambda d: d["conditions"][0].update(score_boundary_s=0.9))
    with pytest.raises(v12.Manifest12Error):
        v12.source_12(path, prereg)


@pytest.mark.parametrize("key", ["note_onset_s", "commanded_note_end_s"])
def test_a_hand_edited_command_window_is_rejected(tmp_path, prereg, cal_1_0, key):
    path = _build_manifest(tmp_path, prereg, cal_1_0[0])
    _rewrite(path, lambda d: d["conditions"][1].update({key: 0.123}))
    with pytest.raises(v12.Manifest12Error):
        v12.source_12(path, prereg)


def test_a_tail_window_that_differs_from_the_preregistration_is_rejected(
    tmp_path, prereg, cal_1_0
):
    """`tail_window_ms` は事前登録が pin している唯一の境界。全条件で照合する。"""
    path = _build_manifest(tmp_path, prereg, cal_1_0[0])
    _rewrite(path, lambda d: [c.update(tail_window_ms=999.0) for c in d["conditions"]])
    with pytest.raises(v12.Manifest12Error):
        v12.source_12(path, prereg)


def test_a_rung_whose_window_drifts_from_its_base_is_rejected(tmp_path, prereg, cal_1_0):
    """振幅 rung は base のゲイン倍なので、時間構造は base と一致していなければならない。"""
    path = _build_manifest(tmp_path, prereg, cal_1_0[0])
    rung = str(prereg["amplitude_ladder"]["rungs"][0]["id"])

    def _drift(d):
        for c in d["conditions"]:
            if c["condition_id"] == rung:
                c["score_boundary_s"] = float(c["score_boundary_s"]) + 0.05

    _rewrite(path, _drift)
    with pytest.raises(v12.Manifest12Error):
        v12.source_12(path, prereg)


def test_a_missing_boundary_field_is_rejected(tmp_path, prereg, cal_1_0):
    path = _build_manifest(tmp_path, prereg, cal_1_0[0])
    _rewrite(path, lambda d: d["conditions"][0].pop("score_boundary_s"))
    with pytest.raises(v12.Manifest12Error):
        v12.source_12(path, prereg)


def test_a_uniformly_edited_window_is_rejected_against_the_pinned_parent(
    tmp_path, prereg, cal_1_0
):
    """**全条件を一様に**書き換えても、pin された親 manifest とは食い違う。

    第 9 巡の相互一致検査だけでは通ってしまう経路（第 10 巡 P1）。私はそのとき
    「系譜検査が先に落ちる」と書いたが、系譜検査は親の sha しか見ておらず、
    親の境界を読んでいなかった。
    """
    path = _build_manifest(tmp_path, prereg, cal_1_0[0])

    def _shift(d):
        for c in d["conditions"]:
            c["score_boundary_s"] = float(c["score_boundary_s"]) + 0.25
            c["commanded_note_end_s"] = float(c["commanded_note_end_s"]) + 0.25

    _rewrite(path, _shift)
    with pytest.raises(v12.Manifest12Error):
        v12.source_12(path, prereg)


@pytest.mark.parametrize("key", ["beats", "tempo_bpm"])
def test_uniformly_missing_command_fields_are_rejected(tmp_path, prereg, cal_1_0, key):
    """一様に欠けていると、相互一致だけは成立してしまう。存在を要求する。"""
    path = _build_manifest(tmp_path, prereg, cal_1_0[0])
    _rewrite(path, lambda d: [c.pop(key, None) for c in d["conditions"]])
    with pytest.raises(v12.Manifest12Error):
        v12.source_12(path, prereg)


def test_a_parent_manifest_that_drifted_from_the_pin_is_rejected(tmp_path, prereg, cal_1_0):
    """正本として読む親 manifest 自身も、1.2 事前登録の pin と一致していること。"""
    path = _build_manifest(tmp_path, prereg, cal_1_0[0])
    v12.source_12(path, prereg)   # 素の状態では通る
    import unittest.mock as mock

    fake = tmp_path / "parent.json"
    fake.write_text('{"conditions": []}', encoding="utf-8")
    with mock.patch.object(v12, "PARENT_MANIFEST_PATH", fake):
        with pytest.raises(v12.Manifest12Error):
            v12.source_12(path, prereg)
