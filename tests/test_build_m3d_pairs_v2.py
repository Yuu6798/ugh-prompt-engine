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


def _real_m1_registry_sha256() -> str:
    return hashlib.sha256(harness.M1_REGISTRY_PATH.read_bytes()).hexdigest()


def _sufficient_clip_entry() -> Dict[str, object]:
    """原音+全変形が sufficient な clip entry（`screen_m3d_clips.screen` が
    書く survivor clip の形。R1 の独立再計算（`_verify_screening_survivors`）を
    通す最小限のダミー gate_metrics 付き。"""
    entry: Dict[str, object] = {
        "original": {"status": "sufficient", "reasons": []},
        "s1_sufficient": True,
        "survivor": True,
    }
    for variant_key in bm.VOCADITO_VARIANT_ORDER:
        entry[variant_key] = {"status": "sufficient", "reasons": []}
    return entry


def _insufficient_clip_entry() -> Dict[str, object]:
    """S1 で insufficient と判定された clip entry（`screen()` が S1 不十分で
    continue する形 — 変形ゲート結果を持たない非対称性も再現する）。N4 の
    full-coverage テストで「survivor ではないが record には載っている」
    非 survivor clip を表現するのに使う。"""
    return {
        "original": {"status": "insufficient", "reasons": ["s1_dummy"]},
        "s1_sufficient": False,
        "survivor": False,
    }


def _write_screening_record(
    path: Path,
    *,
    survivor_clip_ids: list,
    m1_registry_sha256: str = None,
    m2c_external_fixtures_sha256: str = "1" * 64,
    clips: Dict[str, object] = None,
) -> str:
    """screening record（schema: m3d-screening/0.1）を書く。

    既定では `clips` に survivor_clip_ids 各々の「原音+全変形 sufficient」な
    entry を自動生成する——`_verify_screening_survivors`（R1 独立再計算）を
    そのまま通す正当な record を作るため。`m1_registry_sha256` 省略時は実際の
    `run_melody_comparison.M1_REGISTRY_PATH` の現物バイト sha256 を使う
    （R1 の入力 digest 束縛検証をデフォルトで通すため）。改ざん/不一致を
    意図的に作るテストは `clips`/`m1_registry_sha256`/
    `m2c_external_fixtures_sha256` を明示的に上書きする。
    """
    if m1_registry_sha256 is None:
        m1_registry_sha256 = _real_m1_registry_sha256()
    if clips is None:
        clips = {cid: _sufficient_clip_entry() for cid in survivor_clip_ids}
    doc = {
        "schema": "m3d-screening/0.1",
        "started_utc": "2026-08-09T00:00:00+00:00",
        "recorded_utc": "2026-08-09T00:01:00+00:00",
        "route": "crepe_direct",
        "m1_registry_sha256": m1_registry_sha256,
        "m2c_external_fixtures_sha256": m2c_external_fixtures_sha256,
        "gate_parameters": {},
        "transform_parameters": {"semitones": [3.0, -5.0], "time_rates": [0.87, 1.12]},
        "clips": clips,
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
    _fixtures, fixtures_sha256 = bm.load_m2c_fixtures(fixtures_path)
    survivors = [f"vocadito_{i}" for i in range(1, n_clips + 1)]
    screening_path = tmp_path / "screening_v2.json"
    _write_screening_record(
        screening_path,
        survivor_clip_ids=survivors,
        m2c_external_fixtures_sha256=fixtures_sha256,
    )

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
    # R2 対応: path も併記される（check_existing が現物を再照合できるように）。
    assert pins_doc["screening_record_path"] == bm._repo_rel(screening_path)
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
    """fixtures に未登録の clip id を `survivor_clip_ids` に紛れ込ませた record
    は拒否される。N4（Codex レビュー第 2 巡）対応後は、`clips` の key 集合が
    fixtures 全数と完全一致することを要求するため、未登録 id を `clips` 側へ
    紛れ込ませること自体がまず不可能になった——本テストはその一段前の経路
    （`clips` は登録済み 20 件で fixtures と完全一致させたまま、未登録 id を
    `survivor_clip_ids` 側にだけ追加する改ざん）を踏ませ、`_verify_screening_
    survivors` の独立再計算との不一致（recorded 側の余剰）として、より早い
    段階で fail-closed 拒否されることを確認する（run_build 末尾に残る
    fixtures 非登録チェックは、N4 後は record 構造上到達不能な防御的コード
    として残置している）。
    """
    n_clips = 20
    vocadito_dir, fixtures_path, _expected = _make_vocadito_pool(tmp_path, n_clips=n_clips)
    _fixtures, fixtures_sha256 = bm.load_m2c_fixtures(fixtures_path)
    all_ids = [f"vocadito_{i}" for i in range(1, n_clips + 1)]
    # clips は登録済み 20 件のみで fixtures と完全一致させる（N4 の coverage
    # チェックを通すため）— 未登録 id は clips に一切登場させない。
    clips = {cid: _sufficient_clip_entry() for cid in all_ids}
    screening_path = tmp_path / "screening_v2.json"
    _write_screening_record(
        screening_path,
        survivor_clip_ids=all_ids + ["vocadito_not_registered"],
        m2c_external_fixtures_sha256=fixtures_sha256,
        clips=clips,
    )

    with pytest.raises(bm.BuildM3dPairsError, match="survivor_clip_ids"):
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
    n_clips = 20
    vocadito_dir, fixtures_path, _expected = _make_vocadito_pool(tmp_path, n_clips=n_clips)
    _fixtures, fixtures_sha256 = bm.load_m2c_fixtures(fixtures_path)
    screening_path = tmp_path / "screening_v2.json"
    # N=8 survivor のみ → holdout=2<3 で停止条件に抵触。ただし N4（Codex レビュー
    # 第 2 巡）対応後は `clips` が fixtures 全数（20 件）を網羅している必要が
    # あるため、残り 12 件は insufficient な非 survivor entry として明示的に
    # 埋める（R1 の独立再計算/digest 束縛は満たしたまま、このテストが検証
    # したい停止条件の伝播だけを踏ませる）。
    all_ids = [f"vocadito_{i}" for i in range(1, n_clips + 1)]
    survivors = all_ids[:8]
    clips = {
        cid: (_sufficient_clip_entry() if cid in survivors else _insufficient_clip_entry())
        for cid in all_ids
    }
    _write_screening_record(
        screening_path,
        survivor_clip_ids=survivors,
        m2c_external_fixtures_sha256=fixtures_sha256,
        clips=clips,
    )
    out_dir = tmp_path / "external_m3d" / "m3d_pairs_v2"

    with pytest.raises(bm.BuildM3dPairsError, match="停止条件"):
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


def test_run_and_publish_v2_rejects_truncated_census_that_would_otherwise_pass_split(
    tmp_path: Path,
):
    """N4（Codex レビュー第 2 巡）end-to-end 回帰ガード: fixtures 登録 20 件の
    うち 9 件だけを `clips` に載せ、その 9 件は全て matching sufficient/
    survivor として矛盾なく記録されている（9 >= 6+3 の最小分割閾値を満たし、
    `select_clips_v2` まで到達し得る規模）record を `run_and_publish` へ渡すと、
    生成（`out_dir` への書き込み）を一切始めず fail-closed で拒否されることを
    確認する——切り詰め census が「full census から選定された」と偽装した
    manifest を公開してしまう穴が塞がれていることの配線確認。"""
    n_clips = 20
    vocadito_dir, fixtures_path, _expected = _make_vocadito_pool(tmp_path, n_clips=n_clips)
    _fixtures, fixtures_sha256 = bm.load_m2c_fixtures(fixtures_path)
    present_ids = [f"vocadito_{i}" for i in range(1, 10)]  # 9 件のみ（fixtures は 20 件）
    clips = {cid: _sufficient_clip_entry() for cid in present_ids}
    screening_path = tmp_path / "screening_v2.json"
    _write_screening_record(
        screening_path,
        survivor_clip_ids=present_ids,
        m2c_external_fixtures_sha256=fixtures_sha256,
        clips=clips,
    )
    out_dir = tmp_path / "external_m3d" / "m3d_pairs_v2"

    with pytest.raises(bm.BuildM3dPairsError, match="欠落"):
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


# --------------------------------------------------------------------------- #
# R1 対応: survivor 独立再計算・入力 digest 束縛（Codex レビュー #255）
# --------------------------------------------------------------------------- #
def test_recompute_survivor_ids_from_clips_matches_hand_built_gate_results():
    doc = {
        "clips": {
            "vocadito_1": _sufficient_clip_entry(),
            "vocadito_2": {
                "original": {"status": "insufficient"},
                "survivor": False,
            },
        }
    }
    assert bm._recompute_survivor_ids_from_clips(
        doc, source="test", fixture_clip_ids=["vocadito_1", "vocadito_2"]
    ) == ["vocadito_1"]


def test_recompute_survivor_ids_requires_all_variants_sufficient():
    entry = _sufficient_clip_entry()
    # 1 変形だけ insufficient に落とす — 原音 sufficient でも survivor から外れる。
    entry[bm.VOCADITO_VARIANT_ORDER[0]] = {"status": "insufficient"}
    doc = {"clips": {"vocadito_1": entry}}
    assert (
        bm._recompute_survivor_ids_from_clips(doc, source="test", fixture_clip_ids=["vocadito_1"])
        == []
    )


# --------------------------------------------------------------------------- #
# N4 対応（Codex レビュー第 2 巡）: `clips` の key 集合が fixtures 全数と
# 完全一致することを要求する（切り詰め census の受理を封じる）
# --------------------------------------------------------------------------- #
def test_recompute_survivor_ids_from_clips_rejects_truncated_census_missing_ids():
    """`clips` が fixtures 登録済みの一部（ここでは 9/20）しか載せていない
    切り詰め record は、その 9 件全てが矛盾なく sufficient/survivor であっても
    fail-closed で拒否する——reviewer 指摘の「9 件 sufficient・残り 11 件
    省略」シナリオそのものの回帰ガード。"""
    present_ids = [f"vocadito_{i}" for i in range(1, 10)]  # 9 件のみ
    full_fixture_ids = [f"vocadito_{i}" for i in range(1, 21)]  # 本来は 20 件
    doc = {"clips": {cid: _sufficient_clip_entry() for cid in present_ids}}
    with pytest.raises(bm.BuildM3dPairsError, match="欠落"):
        bm._recompute_survivor_ids_from_clips(
            doc, source="test", fixture_clip_ids=full_fixture_ids
        )


def test_recompute_survivor_ids_from_clips_rejects_extra_unregistered_ids():
    """`clips` に fixtures 非登録の余剰 clip id が紛れ込んでいる record も
    fail-closed で拒否する（欠落だけでなく余剰も対象）。"""
    doc = {
        "clips": {
            "vocadito_1": _sufficient_clip_entry(),
            "vocadito_not_registered": _sufficient_clip_entry(),
        }
    }
    with pytest.raises(bm.BuildM3dPairsError, match="余剰"):
        bm._recompute_survivor_ids_from_clips(
            doc, source="test", fixture_clip_ids=["vocadito_1"]
        )


def test_verify_screening_survivors_accepts_matching_record():
    survivors = ["vocadito_1", "vocadito_2"]
    doc = {
        "clips": {cid: _sufficient_clip_entry() for cid in survivors},
        "survivor_clip_ids": survivors,
    }
    bm._verify_screening_survivors(
        doc, source="test", fixture_clip_ids=survivors
    )  # raises なし


def test_verify_screening_survivors_rejects_tampered_survivor_list():
    """record の `survivor_clip_ids` に、per-clip ゲート結果では sufficient で
    ない clip（未スクリーニング/insufficient）が紛れ込んでいる改ざんを検出する
    （Codex レビュー R1 (a)）。"""
    doc = {
        "clips": {
            "vocadito_1": _sufficient_clip_entry(),
            "vocadito_2": {
                "original": {"status": "insufficient"},
                "survivor": False,
            },
        },
        # vocadito_2 は clips 上では insufficient なのに、survivor_clip_ids には
        # 手編集で紛れ込ませている。
        "survivor_clip_ids": ["vocadito_1", "vocadito_2"],
    }
    with pytest.raises(bm.BuildM3dPairsError, match="survivor_clip_ids"):
        bm._verify_screening_survivors(
            doc, source="test", fixture_clip_ids=["vocadito_1", "vocadito_2"]
        )


def test_verify_screening_survivors_rejects_record_missing_true_survivor():
    """clips 上は sufficient なのに survivor_clip_ids から落とされている
    （過少申告）改ざんも同じく fail-closed で検出する。"""
    doc = {
        "clips": {"vocadito_1": _sufficient_clip_entry(), "vocadito_2": _sufficient_clip_entry()},
        "survivor_clip_ids": ["vocadito_1"],
    }
    with pytest.raises(bm.BuildM3dPairsError, match="survivor_clip_ids"):
        bm._verify_screening_survivors(
            doc, source="test", fixture_clip_ids=["vocadito_1", "vocadito_2"]
        )


def test_verify_screening_survivors_rejects_truncated_census_even_when_internally_consistent():
    """reviewer 指摘そのもの: fixtures 登録 20 件のうち 9 件だけを `clips` に
    載せ、その 9 件は全て sufficient/survivor として矛盾なく記録されている
    （6/3 の最小分割閾値も満たしうる規模）record が、`_verify_screening_
    survivors` 単体でも fail-closed で拒否されることを確認する。"""
    present_ids = [f"vocadito_{i}" for i in range(1, 10)]  # 9 件
    full_fixture_ids = [f"vocadito_{i}" for i in range(1, 21)]  # fixtures は 20 件
    doc = {
        "clips": {cid: _sufficient_clip_entry() for cid in present_ids},
        "survivor_clip_ids": present_ids,
    }
    with pytest.raises(bm.BuildM3dPairsError, match="欠落"):
        bm._verify_screening_survivors(
            doc, source="test", fixture_clip_ids=full_fixture_ids
        )


def test_verify_screening_input_digests_accepts_matching_record():
    doc = {
        "m2c_external_fixtures_sha256": "a" * 64,
        "m1_registry_sha256": _real_m1_registry_sha256(),
    }
    bm._verify_screening_input_digests(doc, source="test", fixtures_sha256="a" * 64)  # raises なし


def test_verify_screening_input_digests_rejects_fixtures_mismatch():
    doc = {
        "m2c_external_fixtures_sha256": "a" * 64,
        "m1_registry_sha256": _real_m1_registry_sha256(),
    }
    with pytest.raises(bm.BuildM3dPairsError, match="m2c_external_fixtures"):
        bm._verify_screening_input_digests(doc, source="test", fixtures_sha256="b" * 64)


def test_verify_screening_input_digests_rejects_m1_registry_mismatch():
    doc = {
        "m2c_external_fixtures_sha256": "a" * 64,
        "m1_registry_sha256": "0" * 64,  # 現物と不一致
    }
    with pytest.raises(bm.BuildM3dPairsError, match="m1 registry"):
        bm._verify_screening_input_digests(doc, source="test", fixtures_sha256="a" * 64)


def test_verify_screening_input_digests_rejects_missing_fixtures_field():
    """record にフィールド自体が無い場合はスキーマ上の欠落として fail-closed
    （黙認しない — Codex レビュー R1 (b)）。"""
    doc = {"m1_registry_sha256": _real_m1_registry_sha256()}
    with pytest.raises(bm.BuildM3dPairsError, match="m2c_external_fixtures_sha256"):
        bm._verify_screening_input_digests(doc, source="test", fixtures_sha256="a" * 64)


def test_verify_screening_input_digests_rejects_missing_m1_registry_field():
    doc = {"m2c_external_fixtures_sha256": "a" * 64}
    with pytest.raises(bm.BuildM3dPairsError, match="m1_registry_sha256"):
        bm._verify_screening_input_digests(doc, source="test", fixtures_sha256="a" * 64)


def test_run_and_publish_v2_rejects_survivor_list_tampered_beyond_gate_results(tmp_path: Path):
    """`run_and_publish`（v2 経路）に、per-clip ゲート結果と矛盾する
    survivor_clip_ids を持つ screening record を渡すと fail-closed で拒否
    される（配線確認・end-to-end。Codex レビュー R1 (a)）。"""
    n_clips = 20
    vocadito_dir, fixtures_path, _expected = _make_vocadito_pool(tmp_path, n_clips=n_clips)
    _fixtures, fixtures_sha256 = bm.load_m2c_fixtures(fixtures_path)
    all_ids = [f"vocadito_{i}" for i in range(1, n_clips + 1)]
    # clips 上は全 20 clip とも sufficient として記録するが、survivor_clip_ids
    # には 1 件だけ落とす（過少申告の改ざんを模す）。
    clips = {cid: _sufficient_clip_entry() for cid in all_ids}
    screening_path = tmp_path / "screening_v2.json"
    _write_screening_record(
        screening_path,
        survivor_clip_ids=all_ids[:-1],
        m2c_external_fixtures_sha256=fixtures_sha256,
        clips=clips,
    )

    with pytest.raises(bm.BuildM3dPairsError, match="survivor_clip_ids"):
        bm.run_and_publish(
            vocadito_dir=vocadito_dir,
            out_dir=tmp_path / "external_m3d" / "m3d_pairs_v2",
            manifest_out=tmp_path / "manifest_v2.yaml",
            pins_out=tmp_path / "pins_v2.json",
            fixtures_path=fixtures_path,
            synth_specs_path=REAL_SYNTH_SPECS_V2_PATH,
            screening_record_path=screening_path,
        )


def test_run_and_publish_v2_rejects_stale_fixtures_digest(tmp_path: Path):
    """screening record が保持する `m2c_external_fixtures_sha256` が、現在の
    build 入力の実バイトと不一致なら fail-closed（配線確認・end-to-end。
    Codex レビュー R1 (b)）。"""
    n_clips = 20
    vocadito_dir, fixtures_path, _expected = _make_vocadito_pool(tmp_path, n_clips=n_clips)
    survivors = [f"vocadito_{i}" for i in range(1, n_clips + 1)]
    screening_path = tmp_path / "screening_v2.json"
    _write_screening_record(
        screening_path,
        survivor_clip_ids=survivors,
        m2c_external_fixtures_sha256="f" * 64,  # 現在の fixtures と一致しない
    )

    with pytest.raises(bm.BuildM3dPairsError, match="m2c_external_fixtures"):
        bm.run_and_publish(
            vocadito_dir=vocadito_dir,
            out_dir=tmp_path / "external_m3d" / "m3d_pairs_v2",
            manifest_out=tmp_path / "manifest_v2.yaml",
            pins_out=tmp_path / "pins_v2.json",
            fixtures_path=fixtures_path,
            synth_specs_path=REAL_SYNTH_SPECS_V2_PATH,
            screening_record_path=screening_path,
        )


# --------------------------------------------------------------------------- #
# R2 対応: screening pin を check_existing の fail-closed 検証に接続
# （--check-only スコープ。Codex レビュー #255）
# --------------------------------------------------------------------------- #
def _build_v2_bundle(tmp_path: Path) -> Dict[str, Path]:
    n_clips = 20
    vocadito_dir, fixtures_path, _expected = _make_vocadito_pool(tmp_path, n_clips=n_clips)
    _fixtures, fixtures_sha256 = bm.load_m2c_fixtures(fixtures_path)
    survivors = [f"vocadito_{i}" for i in range(1, n_clips + 1)]
    screening_path = tmp_path / "screening_v2.json"
    _write_screening_record(
        screening_path,
        survivor_clip_ids=survivors,
        m2c_external_fixtures_sha256=fixtures_sha256,
    )
    manifest_out = tmp_path / "manifest_v2.yaml"
    pins_out = tmp_path / "pins_v2.json"
    out_dir = tmp_path / "external_m3d" / "m3d_pairs_v2"
    bm.run_and_publish(
        vocadito_dir=vocadito_dir,
        out_dir=out_dir,
        manifest_out=manifest_out,
        pins_out=pins_out,
        fixtures_path=fixtures_path,
        synth_specs_path=REAL_SYNTH_SPECS_V2_PATH,
        screening_record_path=screening_path,
    )
    return {
        "vocadito_dir": vocadito_dir,
        "fixtures_path": fixtures_path,
        "manifest_out": manifest_out,
        "pins_out": pins_out,
        "screening_path": screening_path,
    }


def test_check_existing_detects_screening_record_removed_after_build(tmp_path: Path):
    """v2 ビルド成功後に screening record が削除されると `--check-only` が
    fail-closed で検出する（従来は不可視だった穴。Codex レビュー R2）。"""
    bundle = _build_v2_bundle(tmp_path)
    bundle["screening_path"].unlink()

    with pytest.raises(bm.BuildM3dPairsError, match="screening record"):
        bm.check_existing(
            manifest_out=bundle["manifest_out"],
            pins_out=bundle["pins_out"],
            vocadito_dir=bundle["vocadito_dir"],
            fixtures_path=bundle["fixtures_path"],
            synth_specs_path=REAL_SYNTH_SPECS_V2_PATH,
        )


def test_check_existing_detects_screening_record_content_drift_after_build(tmp_path: Path):
    """v2 ビルド成功後に screening record の中身が改変されると `--check-only`
    が sha256 不一致で fail-closed 検出する（Codex レビュー R2）。"""
    bundle = _build_v2_bundle(tmp_path)
    doc = json.loads(bundle["screening_path"].read_text(encoding="utf-8"))
    doc["s1_summary"] = {"tampered": True}
    bundle["screening_path"].write_text(json.dumps(doc, indent=2, sort_keys=True), encoding="utf-8")

    with pytest.raises(bm.BuildM3dPairsError, match="screening record sha256 mismatch"):
        bm.check_existing(
            manifest_out=bundle["manifest_out"],
            pins_out=bundle["pins_out"],
            vocadito_dir=bundle["vocadito_dir"],
            fixtures_path=bundle["fixtures_path"],
            synth_specs_path=REAL_SYNTH_SPECS_V2_PATH,
        )


def test_check_existing_passes_when_screening_record_untouched(tmp_path: Path):
    """screening record が無傷なら `--check-only` は引き続き OK（新規検証が
    正常系を壊していないことの回帰ガード）。"""
    bundle = _build_v2_bundle(tmp_path)
    summary = bm.check_existing(
        manifest_out=bundle["manifest_out"],
        pins_out=bundle["pins_out"],
        vocadito_dir=bundle["vocadito_dir"],
        fixtures_path=bundle["fixtures_path"],
        synth_specs_path=REAL_SYNTH_SPECS_V2_PATH,
    )
    assert summary["total"] > 0


def test_load_and_validate_pins_rejects_screening_path_without_sha(tmp_path: Path):
    """`screening_record_path`/`screening_record_sha256` は両方存在するか
    両方欠落かのいずれかでなければならない（片方のみは sidecar 破損）。"""
    doc = {
        "schema": bm._PINS_SCHEMA,
        "generated_utc": "2026-08-09T00:00:00+00:00",
        "m2c_external_fixtures_sha256": "a" * 64,
        "m3d_synth_specs_sha256": "b" * 64,
        "manifest_sha256": "c" * 64,
        "audio_sha256": {},
        "material": {},
        "screening_record_path": "build/external_m3d/m3d_screening_v2.json",
        # screening_record_sha256 を欠落させる。
    }
    path = tmp_path / "pins.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(bm.BuildM3dPairsError, match="screening_record_path.*screening_record_sha256"):
        bm._load_and_validate_pins(path)
