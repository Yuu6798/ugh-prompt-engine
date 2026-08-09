"""tests/test_build_m3d_pairs_v2.py — `scripts/build_m3d_pairs.py` の v2 経路
（`--screening-record`）と `scripts/screen_m3d_clips.py` の記録スキーマ整合の
テスト。

実音声・crepe 非依存（CI 安全・`pytest -m "not slow"` に含む）: `tests/
test_build_m3d_pairs.py`（v1）と同じ流儀で微小な合成 WAV を使い、v2 の選定・
分割規則（`select_clips_v2`）の決定論・N<18 規則・停止条件 fail-closed・
スクリーニング記録ローダの検証・v2 manifest の end-to-end 構築を確認する。
v1 資産（`tests/test_build_m3d_pairs.py` 含む）は不変更。
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Dict, Tuple

import numpy as np
import pytest
import soundfile as sf
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_m3d_pairs as bm  # noqa: E402
import run_melody_comparison as harness  # noqa: E402

REAL_SYNTH_SPECS_V2_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "m3d_synth_specs_v2.yaml"

_SAMPLE_RATE = 22050
_TONE_DURATION_SEC = 0.35


def _write_tone_wav(path: Path, *, freq: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(round(_SAMPLE_RATE * _TONE_DURATION_SEC))
    t = np.linspace(0.0, _TONE_DURATION_SEC, n, endpoint=False)
    y = (0.2 * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)
    sf.write(path, y, _SAMPLE_RATE, subtype="FLOAT")


def _make_vocadito_pool(tmp_path: Path, n_clips: int) -> Tuple[Path, Path, Dict[str, str]]:
    """`tests/test_build_m3d_pairs.py::_make_vocadito_pool` と同型（重複は意図的
    ——builder はハーネスを import しない設計と同じ理由で、テストファイル間の
    結合を避けるため独立に複製する）。"""
    vocadito_dir = tmp_path / "external_m3d" / "vocadito"
    clip_ids = [f"vocadito_{i}" for i in range(1, n_clips + 1)]
    fixtures_doc: Dict[str, object] = {
        "schema_version": "m2c-external-fixtures/0.1",
        "registered_utc": "2026-01-01",
        "fixtures": {},
    }
    for idx, clip_id in enumerate(clip_ids):
        audio_path = vocadito_dir / "Audio" / f"{clip_id}.wav"
        _write_tone_wav(audio_path, freq=180.0 + idx * 7.0)
        digest = hashlib.sha256(audio_path.read_bytes()).hexdigest()
        fixtures_doc["fixtures"][clip_id] = {  # type: ignore[index]
            "expected_audio_sha256": digest,
            "expected_annotation_sha256": "0" * 64,
        }
    fixtures_path = tmp_path / "m2c_external_fixtures.yaml"
    fixtures_path.write_text(yaml.safe_dump(fixtures_doc, sort_keys=False), encoding="utf-8")
    expected = {
        cid: entry["expected_audio_sha256"]  # type: ignore[index]
        for cid, entry in fixtures_doc["fixtures"].items()  # type: ignore[union-attr]
    }
    return vocadito_dir, fixtures_path, expected


def _write_screening_record(
    path: Path, *, survivor_clip_ids: list, m1_registry_sha256: str = "0" * 64
) -> str:
    doc = {
        "schema": "m3d-screening/0.1",
        "started_utc": "2026-08-09T00:00:00+00:00",
        "recorded_utc": "2026-08-09T00:01:00+00:00",
        "route": "crepe_direct",
        "m1_registry_sha256": m1_registry_sha256,
        "m2c_external_fixtures_sha256": "1" * 64,
        "gate_parameters": {},
        "transform_parameters": {"semitones": [3.0, -5.0], "time_rates": [0.87, 1.12]},
        "clips": {},
        "s1_summary": {},
        "s2_variant_dropout_count": {},
        "survivor_clip_ids": survivor_clip_ids,
        "survivor_clip_ids_sha256_sorted": sorted(
            survivor_clip_ids, key=lambda cid: hashlib.sha256(cid.encode("utf-8")).hexdigest()
        ),
        "survivor_count": len(survivor_clip_ids),
    }
    data = json.dumps(doc, indent=2, sort_keys=True).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# select_clips_v2: 決定論・分割規則（prereg_v2 §3）
# --------------------------------------------------------------------------- #
def test_select_clips_v2_matches_hand_computed_split_for_n_ge_18():
    clip_ids = [f"vocadito_{i}" for i in range(1, 21)]  # N=20 >= 18
    tuning, holdout = bm.select_clips_v2(clip_ids)

    ranked = sorted(
        sorted(clip_ids), key=lambda cid: hashlib.sha256(cid.encode("utf-8")).hexdigest()
    )
    assert tuning == ranked[:12]
    assert holdout == ranked[12:18]


def test_select_clips_v2_is_deterministic_across_calls():
    clip_ids = [f"vocadito_{i}" for i in range(1, 25)]
    first = bm.select_clips_v2(clip_ids)
    second = bm.select_clips_v2(clip_ids)
    assert first == second


@pytest.mark.parametrize(
    "n",
    [9, 10, 12, 15, 17],
)
def test_select_clips_v2_applies_ceil_floor_formula_for_n_lt_18(n: int):
    clip_ids = [f"vocadito_{i}" for i in range(1, n + 1)]
    tuning, holdout = bm.select_clips_v2(clip_ids)

    expected_tuning_n = math.ceil(2 * n / 3)
    expected_holdout_n = n // 3
    assert len(tuning) == expected_tuning_n
    assert len(holdout) == expected_holdout_n

    ranked = sorted(
        sorted(clip_ids), key=lambda cid: hashlib.sha256(cid.encode("utf-8")).hexdigest()
    )
    assert tuning == ranked[:expected_tuning_n]
    assert holdout == ranked[expected_tuning_n : expected_tuning_n + expected_holdout_n]
    # tuning/holdout は互いに素かつ survivor の部分集合。
    assert set(tuning).isdisjoint(set(holdout))
    assert set(tuning) | set(holdout) <= set(clip_ids)


def test_select_clips_v2_n_18_uses_fixed_12_6_not_formula():
    """N=18 は「N>=18」分岐（固定 12/6）を使う——ceil(2*18/3)=12/floor(18/3)=6 と
    数値的には一致するが、閾値の境界（N=18 がどちら側の分岐か）を明示的に固定する
    回帰ガード。"""
    clip_ids = [f"vocadito_{i}" for i in range(1, 19)]  # N=18
    tuning, holdout = bm.select_clips_v2(clip_ids)
    assert len(tuning) == 12
    assert len(holdout) == 6


def test_select_clips_v2_stop_condition_fail_closed_when_holdout_below_3():
    # N=8: ceil(16/3)=6 (tuning ok), 8//3=2 < 3 (holdout NG) → fail-closed。
    clip_ids = [f"vocadito_{i}" for i in range(1, 9)]
    with pytest.raises(bm.BuildM3dPairsError):
        bm.select_clips_v2(clip_ids)


def test_select_clips_v2_stop_condition_fail_closed_when_tuning_below_6():
    # N=3: ceil(6/3)=2 < 6 (tuning NG) → fail-closed（緩和・救済なし）。
    clip_ids = [f"vocadito_{i}" for i in range(1, 4)]
    with pytest.raises(bm.BuildM3dPairsError):
        bm.select_clips_v2(clip_ids)


def test_select_clips_v2_boundary_n_9_does_not_raise():
    # N=9: ceil(18/3)=6 (tuning ちょうど下限), 9//3=3 (holdout ちょうど下限) →
    # 両方とも下限を満たすため fail-closed にならない（停止条件の境界確認）。
    clip_ids = [f"vocadito_{i}" for i in range(1, 10)]
    tuning, holdout = bm.select_clips_v2(clip_ids)
    assert len(tuning) == 6
    assert len(holdout) == 3


def test_select_clips_v2_dedupes_duplicate_survivor_ids_defensively():
    clip_ids = [f"vocadito_{i}" for i in range(1, 21)] + ["vocadito_1", "vocadito_2"]
    tuning, holdout = bm.select_clips_v2(clip_ids)
    ranked = sorted(
        sorted(set(clip_ids)), key=lambda cid: hashlib.sha256(cid.encode("utf-8")).hexdigest()
    )
    assert tuning == ranked[:12]
    assert holdout == ranked[12:18]


# --------------------------------------------------------------------------- #
# _load_screening_record: schema/型検証（fail-closed）
# --------------------------------------------------------------------------- #
def test_load_screening_record_round_trips_survivor_and_sha256(tmp_path: Path):
    path = tmp_path / "screening.json"
    survivors = [f"vocadito_{i}" for i in range(1, 21)]
    expected_sha256 = _write_screening_record(path, survivor_clip_ids=survivors)

    doc, digest = bm._load_screening_record(path)
    assert digest == expected_sha256
    assert doc["survivor_clip_ids"] == survivors
    assert doc["schema"] == "m3d-screening/0.1"


def test_load_screening_record_rejects_wrong_schema(tmp_path: Path):
    path = tmp_path / "screening.json"
    path.write_bytes(json.dumps({"schema": "not-the-right-schema/0.1"}).encode("utf-8"))
    with pytest.raises(bm.BuildM3dPairsError):
        bm._load_screening_record(path)


def test_load_screening_record_rejects_non_string_survivor_list(tmp_path: Path):
    path = tmp_path / "screening.json"
    doc = {"schema": "m3d-screening/0.1", "survivor_clip_ids": [1, 2, 3]}
    path.write_bytes(json.dumps(doc).encode("utf-8"))
    with pytest.raises(bm.BuildM3dPairsError):
        bm._load_screening_record(path)


def test_load_screening_record_rejects_path_traversal_in_survivor_id(tmp_path: Path):
    path = tmp_path / "screening.json"
    doc = {"schema": "m3d-screening/0.1", "survivor_clip_ids": ["../../etc/passwd"]}
    path.write_bytes(json.dumps(doc).encode("utf-8"))
    with pytest.raises(bm.BuildM3dPairsError):
        bm._load_screening_record(path)


# --------------------------------------------------------------------------- #
# v2 end-to-end manifest 構築（fake 音声・crepe 非依存）
# --------------------------------------------------------------------------- #
def test_run_and_publish_v2_path_builds_manifest_from_screening_record(tmp_path: Path):
    n_clips = 20
    vocadito_dir, fixtures_path, _expected = _make_vocadito_pool(tmp_path, n_clips=n_clips)
    survivors = [f"vocadito_{i}" for i in range(1, n_clips + 1)]
    screening_path = tmp_path / "screening_v2.json"
    _write_screening_record(screening_path, survivor_clip_ids=survivors)

    manifest_out = tmp_path / "manifest_v2.yaml"
    pins_out = tmp_path / "pins_v2.json"
    out_dir = tmp_path / "external_m3d" / "m3d_pairs_v2"

    summary = bm.run_and_publish(
        vocadito_dir=vocadito_dir,
        out_dir=out_dir,
        manifest_out=manifest_out,
        pins_out=pins_out,
        fixtures_path=fixtures_path,
        synth_specs_path=REAL_SYNTH_SPECS_V2_PATH,
        screening_record_path=screening_path,
    )

    assert manifest_out.exists()
    assert pins_out.exists()

    expected_tuning, expected_holdout = bm.select_clips_v2(survivors)
    manifest_doc = yaml.safe_load(manifest_out.read_text(encoding="utf-8"))
    pairs = harness._validate_manifest(manifest_doc)

    def _clip_id_from_real_positive_pair_id(pair_id: str, split: str) -> str:
        # pair_id = f"pt_real_{split}_{clip_id}_{label}"（clip_id 自体が
        # アンダースコアを含む — vocadito_N — ため素朴な split("_") は使えない。
        # 既知の prefix/label suffix を剥がして clip_id だけを取り出す。
        prefix = f"pt_real_{split}_"
        assert pair_id.startswith(prefix), pair_id
        rest = pair_id[len(prefix) :]
        for label in bm.VOCADITO_VARIANT_LABELS.values():
            suffix = f"_{label}"
            if rest.endswith(suffix):
                return rest[: -len(suffix)]
        raise AssertionError(f"unrecognized variant label suffix in pair_id {pair_id!r}")

    # positive_transform (vocadito) の clip 集合が select_clips_v2 の結果と一致する。
    tuning_clip_ids = {
        _clip_id_from_real_positive_pair_id(p["pair_id"], "tuning")
        for p in pairs
        if p["kind"] == "positive_transform" and p["split"] == "tuning" and "_real_" in p["pair_id"]
    }
    holdout_clip_ids = {
        _clip_id_from_real_positive_pair_id(p["pair_id"], "holdout")
        for p in pairs
        if p["kind"] == "positive_transform"
        and p["split"] == "holdout"
        and "_real_" in p["pair_id"]
    }
    assert tuning_clip_ids == set(expected_tuning)
    assert holdout_clip_ids == set(expected_holdout)

    # v1 側 negative_rhythm/negative_interval と同じ pair 数（synth specs v2 も
    # 同じ 2 対ずつの構成）。
    assert summary["by_kind_split"]["negative_rhythm"] == {"tuning": 2}
    assert summary["by_kind_split"]["negative_interval"] == {"tuning": 2}

    # pins サイドカーに screening_record_sha256 が optional フィールドとして
    # 記録され、v1 必須フィールドは維持される。
    pins_doc = json.loads(pins_out.read_text(encoding="utf-8"))
    assert pins_doc["schema"] == bm._PINS_SCHEMA
    assert "screening_record_sha256" in pins_doc
    for key in bm._REQUIRED_PINS_KEYS:
        assert key in pins_doc

    # --check-only（v2 相当）が OK で通る。
    check_summary = bm.check_existing(
        manifest_out=manifest_out,
        pins_out=pins_out,
        vocadito_dir=vocadito_dir,
        fixtures_path=fixtures_path,
        synth_specs_path=REAL_SYNTH_SPECS_V2_PATH,
    )
    assert check_summary["total"] == summary["total"]


def test_run_and_publish_v2_path_rejects_unknown_survivor_clip_id(tmp_path: Path):
    vocadito_dir, fixtures_path, _expected = _make_vocadito_pool(tmp_path, n_clips=20)
    screening_path = tmp_path / "screening_v2.json"
    _write_screening_record(
        screening_path, survivor_clip_ids=["vocadito_1", "vocadito_not_registered"]
    )

    with pytest.raises(bm.BuildM3dPairsError):
        bm.run_and_publish(
            vocadito_dir=vocadito_dir,
            out_dir=tmp_path / "external_m3d" / "m3d_pairs_v2",
            manifest_out=tmp_path / "manifest_v2.yaml",
            pins_out=tmp_path / "pins_v2.json",
            fixtures_path=fixtures_path,
            synth_specs_path=REAL_SYNTH_SPECS_V2_PATH,
            screening_record_path=screening_path,
        )


def test_run_and_publish_v2_path_stop_condition_propagates_before_any_generation(
    tmp_path: Path,
):
    """survivor が停止条件に抵触する場合、生成（staging 書き込み）を一切始めず
    fail-closed で拒否する——`out_dir` が作られないことまで確認する。"""
    vocadito_dir, fixtures_path, _expected = _make_vocadito_pool(tmp_path, n_clips=20)
    screening_path = tmp_path / "screening_v2.json"
    # N=8 survivor のみ登録 → holdout=2<3 で停止条件に抵触。
    _write_screening_record(
        screening_path, survivor_clip_ids=[f"vocadito_{i}" for i in range(1, 9)]
    )
    out_dir = tmp_path / "external_m3d" / "m3d_pairs_v2"

    with pytest.raises(bm.BuildM3dPairsError):
        bm.run_and_publish(
            vocadito_dir=vocadito_dir,
            out_dir=out_dir,
            manifest_out=tmp_path / "manifest_v2.yaml",
            pins_out=tmp_path / "pins_v2.json",
            fixtures_path=fixtures_path,
            synth_specs_path=REAL_SYNTH_SPECS_V2_PATH,
            screening_record_path=screening_path,
        )
    assert not out_dir.exists()


def test_run_and_publish_without_screening_record_is_v1_behavior_unchanged(tmp_path: Path):
    """`screening_record_path` 未指定（既定）は v1 の固定 12/6 選定を使う——
    v2 フラグの追加が v1 経路の挙動を変えていないことの回帰ガード
    （`tests/test_build_m3d_pairs.py` の既存 v1 統合テストと同じ流儀）。"""
    vocadito_dir, fixtures_path, _expected = _make_vocadito_pool(tmp_path, n_clips=18)
    real_v1_synth_specs_path = (
        ROOT / "tests" / "fixtures" / "melody_bench" / "m3d_synth_specs.yaml"
    )

    summary = bm.run_and_publish(
        vocadito_dir=vocadito_dir,
        out_dir=tmp_path / "external_m3d" / "m3d_pairs",
        manifest_out=tmp_path / "manifest.yaml",
        pins_out=tmp_path / "pins.json",
        fixtures_path=fixtures_path,
        synth_specs_path=real_v1_synth_specs_path,
    )

    pins_doc = json.loads((tmp_path / "pins.json").read_text(encoding="utf-8"))
    assert "screening_record_sha256" not in pins_doc
    # 18 clip（TUNING_COUNT(12) + HOLDOUT_COUNT(6)ちょうど）→ vocadito
    # positive_transform: tuning 12*4=48 / holdout 6*4=24、+ synth positive
    # （tuning/holdout 各 1 base * 2 variant = 2）で by_kind_split の
    # positive_transform 合計は tuning 50 / holdout 26（v1 crosstab は
    # real/synth を kind×split でまとめる — material 別内訳は
    # `by_kind_material` 側）。
    assert summary["by_kind_split"]["positive_transform"]["tuning"] == 50
    assert summary["by_kind_split"]["positive_transform"]["holdout"] == 26
