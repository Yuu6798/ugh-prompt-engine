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
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple

import librosa
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
import screen_m3d_clips as sm  # noqa: E402

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


# V1（Codex レビュー第 5 巡）対応後、`route_provenance` は必須 sha256 pin
# （`extractor_code_sha256`/`extractor_weights_sha256`。64 桁小文字 hex）を
# 備えた mapping であり、record 内の全エントリで相互に完全同一でなければ
# ならない（`bm._verify_screening_route_binding`）。全ての `_gate_result`
# 呼び出しが既定でこの同一 dict 値を使うことで、他の（V1 と無関係な）v2
# テストは自動的に (b)(c) の要件を満たしたまま素通りする。
_VALID_ROUTE_PROVENANCE: Dict[str, object] = {
    "route": "crepe_direct",
    "extractor_code_sha256": hashlib.sha256(b"test-extractor-code").hexdigest(),
    "extractor_weights_sha256": hashlib.sha256(b"test-extractor-weights").hexdigest(),
}


def _gate_result(
    status: str,
    *,
    audio_sha256: str = "0" * 64,
    route_provenance: "Optional[Dict[str, object]]" = None,
) -> Dict[str, object]:
    """`scripts/screen_m3d_clips.py::_gate_one` が実際に emit する 1 ゲート
    結果の完備な形状（T3・Codex レビュー第 3 巡部分対応: `bm._REQUIRED_GATE_
    RESULT_KEYS` 全キー）を持つダミー entry を作る。`audio_sha256`（U2・
    Codex レビュー第 4 巡）: variant entry では既定のダミー値ではなく実際に
    generate される変形バイトの sha256 を渡す必要がある場合に上書きする
    （`_real_variant_audio_sha256_map` 参照）。`route_provenance`（V1・Codex
    レビュー第 5 巡）: 既定は `_VALID_ROUTE_PROVENANCE`（record 全体で相互に
    完全同一な有効値）。route 束縛の改変/欠落を試すテストのみ明示的に上書き
    する。"""
    return {
        "status": status,
        "reasons": [] if status == "sufficient" else ["s1_dummy"],
        "audio_sha256": audio_sha256,
        "route_provenance": (
            route_provenance if route_provenance is not None else _VALID_ROUTE_PROVENANCE
        ),
        "gate_metrics": {"status": status},
    }


def _sufficient_clip_entry(
    variant_audio_sha256: "Optional[Dict[str, str]]" = None,
) -> Dict[str, object]:
    """原音+全変形が sufficient な clip entry（`screen_m3d_clips.screen` が
    書く survivor clip の形。R1 の独立再計算（`_verify_screening_survivors`）と
    T3 の row 完全形状検証の両方を通す。

    `variant_audio_sha256`（U2・Codex レビュー第 4 巡）: `{variant_key:
    sha256}` を渡すと、各変形 entry の `audio_sha256` をその値で埋める
    （`_verify_generated_variant_audio_hashes` を実際に通す record を作る
    場合に使う——省略時はダミー値 `"0"*64` のまま、選定前で止まるテストや
    v1 経路など照合まで到達しないテストに使う）。
    """
    entry: Dict[str, object] = {
        "original": _gate_result("sufficient"),
        "s1_sufficient": True,
        "survivor": True,
    }
    for variant_key in bm.VOCADITO_VARIANT_ORDER:
        audio_sha256 = (variant_audio_sha256 or {}).get(variant_key, "0" * 64)
        entry[variant_key] = _gate_result("sufficient", audio_sha256=audio_sha256)
    return entry


def _real_variant_audio_sha256_map(audio_path: Path) -> Dict[str, str]:
    """`audio_path` から `generate_vocadito_variants` と同一の変形
    （`bm.make_variants`・同一半音/タイムレート・同一 PCM_24 WAV シリアライズ）
    を再現し、各変形バイトの sha256 を返す（U2・Codex レビュー第 4 巡テスト
    用整合ヘルパー）。builder が staging へ実際に書くバイトと同じ手順
    （decode パラメータ・書き出し subtype）で screening record 側の期待値を
    独立に作る——builder 本体のコードパスを再利用せず、テストが「builder が
    生成するはずのバイト」を自前で再現することで、`_verify_generated_
    variant_audio_hashes` が本当に正しいバイトを比較していることを検証する。
    """
    y, sr = librosa.load(str(audio_path), sr=None, mono=True)
    variants = bm.make_variants(
        y, sr, semitones=list(bm.VOCADITO_SEMITONES), time_rates=list(bm.VOCADITO_TIME_RATES)
    )
    result: Dict[str, str] = {}
    with tempfile.TemporaryDirectory() as tmp_dir:
        for variant_key, wave in variants.items():
            tmp_wav = Path(tmp_dir) / f"{variant_key}.wav"
            sf.write(tmp_wav, wave, sr, subtype="PCM_24")
            result[variant_key] = hashlib.sha256(tmp_wav.read_bytes()).hexdigest()
    return result


def _insufficient_clip_entry() -> Dict[str, object]:
    """S1 で insufficient と判定された clip entry（`screen()` が S1 不十分で
    continue する形 — 変形ゲート結果を持たない非対称性も再現する）。N4 の
    full-coverage テストで「survivor ではないが record には載っている」
    非 survivor clip を表現するのに使う。"""
    return {
        "original": _gate_result("insufficient"),
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
    variant_audio_sha256_by_clip: "Optional[Dict[str, Dict[str, str]]]" = None,
) -> str:
    """screening record（schema: m3d-screening/0.1）を書く。

    既定では `clips` に survivor_clip_ids 各々の「原音+全変形 sufficient」な
    entry を自動生成する——`_verify_screening_survivors`（R1 独立再計算）を
    そのまま通す正当な record を作るため。`m1_registry_sha256` 省略時は実際の
    `run_melody_comparison.M1_REGISTRY_PATH` の現物バイト sha256 を使う
    （R1 の入力 digest 束縛検証をデフォルトで通すため）。改ざん/不一致を
    意図的に作るテストは `clips`/`m1_registry_sha256`/
    `m2c_external_fixtures_sha256` を明示的に上書きする。

    `variant_audio_sha256_by_clip`（U2・Codex レビュー第 4 巡）:
    `{clip_id: {variant_key: sha256}}` を渡すと、自動生成される各 clip の
    変形 entry の `audio_sha256` をその値で埋める——`run_and_publish` の
    v2 経路を実際に成功させる（`_verify_generated_variant_audio_hashes` を
    通す）record を作る場合に必須。省略時はダミー値のままで、選定前に
    fail-closed で止まる想定のテスト（照合まで到達しない）に使う。
    """
    if m1_registry_sha256 is None:
        m1_registry_sha256 = _real_m1_registry_sha256()
    if clips is None:
        clips = {
            cid: _sufficient_clip_entry(
                (variant_audio_sha256_by_clip or {}).get(cid)
            )
            for cid in survivor_clip_ids
        }
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
    # U2（Codex レビュー第 4 巡）: record の variant audio_sha256 を、builder が
    # 実際に生成するバイトと一致させる（`_verify_generated_variant_audio_
    # hashes` を通すため）——`_real_variant_audio_sha256_map` で builder と
    # 同一手順の期待値を独立に再現する。
    variant_audio_sha256_by_clip = {
        cid: _real_variant_audio_sha256_map(bm.vocadito_audio_path(vocadito_dir, cid))
        for cid in survivors
    }
    screening_path = tmp_path / "screening_v2.json"
    _write_screening_record(
        screening_path,
        survivor_clip_ids=survivors,
        m2c_external_fixtures_sha256=fixtures_sha256,
        variant_audio_sha256_by_clip=variant_audio_sha256_by_clip,
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
    # 第 6 巡 W2 対応（部分採用 (b)）: 抽出器 pin（route_provenance 由来）も
    # 併記される——record 内で全エントリが `_VALID_ROUTE_PROVENANCE` に相互
    # 一致しているため、その値と一致する。
    assert pins_doc["screening_extractor_code_sha256"] == _VALID_ROUTE_PROVENANCE[
        "extractor_code_sha256"
    ]
    assert pins_doc["screening_extractor_weights_sha256"] == _VALID_ROUTE_PROVENANCE[
        "extractor_weights_sha256"
    ]
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
                "original": _gate_result("insufficient"),
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
    entry[bm.VOCADITO_VARIANT_ORDER[0]] = _gate_result("insufficient")
    doc = {"clips": {"vocadito_1": entry}}
    assert (
        bm._recompute_survivor_ids_from_clips(doc, source="test", fixture_clip_ids=["vocadito_1"])
        == []
    )


# --------------------------------------------------------------------------- #
# T3（Codex レビュー第 3 巡・部分採用）: row（clips 各エントリ）の完全な
# screener 出力形状を要求する（status のみ・gate_metrics 欠落は fail-closed）
# --------------------------------------------------------------------------- #
def test_recompute_survivor_ids_from_clips_accepts_full_shape_entries():
    """完全形状（status/reasons/audio_sha256/route_provenance/gate_metrics
    の全キー）の entry は受理される（正常系の回帰ガード）。"""
    doc = {"clips": {"vocadito_1": _sufficient_clip_entry()}}
    assert bm._recompute_survivor_ids_from_clips(
        doc, source="test", fixture_clip_ids=["vocadito_1"]
    ) == ["vocadito_1"]


def test_recompute_survivor_ids_from_clips_rejects_status_only_original_entry():
    """`original` が `status` のみ（`gate_metrics`/`reasons`/`audio_sha256`/
    `route_provenance` 欠落）の row は、内部的には矛盾なく見えても機械的に
    拒否する——「parse 可能 ≠ 形状正しい」の強制。"""
    doc = {"clips": {"vocadito_1": {"original": {"status": "sufficient"}, "survivor": True}}}
    with pytest.raises(bm.BuildM3dPairsError, match="必須キーが欠落"):
        bm._recompute_survivor_ids_from_clips(doc, source="test", fixture_clip_ids=["vocadito_1"])


def test_recompute_survivor_ids_from_clips_rejects_gate_metrics_missing():
    """`gate_metrics` 単体の欠落だけでも fail-closed（他のキーが揃っていても
    救済しない）。"""
    entry = _sufficient_clip_entry()
    original = dict(entry["original"])
    del original["gate_metrics"]
    entry["original"] = original
    doc = {"clips": {"vocadito_1": entry}}
    with pytest.raises(bm.BuildM3dPairsError, match="必須キーが欠落"):
        bm._recompute_survivor_ids_from_clips(doc, source="test", fixture_clip_ids=["vocadito_1"])


def test_recompute_survivor_ids_from_clips_rejects_status_only_variant_entry():
    """variant entry が `status` のみの場合も同様に拒否する（原音側だけでなく
    変形側にも同じ形状強制がかかることの確認）。"""
    entry = _sufficient_clip_entry()
    entry[bm.VOCADITO_VARIANT_ORDER[0]] = {"status": "sufficient"}
    doc = {"clips": {"vocadito_1": entry}}
    with pytest.raises(bm.BuildM3dPairsError, match="必須キーが欠落"):
        bm._recompute_survivor_ids_from_clips(doc, source="test", fixture_clip_ids=["vocadito_1"])


# --------------------------------------------------------------------------- #
# 第 6 巡 W1（採用）: `status` の閉集合検証（"sufficient"/"insufficient" 以外は
# fail-closed）。producer（`svp_rpe.melody.observability.assess_observability`）
# は 2 値 closed enumeration しか emit しない——typo/新規値は record 改ざんの
# 疑いとして拒否する。
# --------------------------------------------------------------------------- #
def test_validate_gate_result_shape_accepts_sufficient_and_insufficient():
    """producer 契約の 2 値はいずれも受理される（正常系の回帰ガード）。"""
    for status in ("sufficient", "insufficient"):
        bm._validate_gate_result_shape(
            _gate_result(status), source="test", label="entry"
        )  # raises なし


def test_validate_gate_result_shape_rejects_typo_status_value():
    """`"sufficent"`（typo）は文字列としては valid だが、producer の閉集合
    契約に無い値として拒否される——`_recompute_survivor_ids_from_clips` に
    非 "sufficient" として素通しさせず、明示的に fail-closed とする。"""
    entry = _gate_result("sufficient")
    entry["status"] = "sufficent"
    with pytest.raises(bm.BuildM3dPairsError, match="既知の値でない"):
        bm._validate_gate_result_shape(entry, source="test", label="entry")


def test_validate_gate_result_shape_rejects_unknown_status_value():
    """既知の 2 値のどちらでもない新規/未知の値（typo に限らない）も同様に
    拒否される。"""
    entry = _gate_result("sufficient")
    entry["status"] = "maybe"
    with pytest.raises(bm.BuildM3dPairsError, match="既知の値でない"):
        bm._validate_gate_result_shape(entry, source="test", label="entry")


def test_recompute_survivor_ids_from_clips_rejects_typo_status_in_original_entry():
    """`_recompute_survivor_ids_from_clips` 経由でも同じ typo status が
    fail-closed で拒否されることを end-to-end に確認する（本来 survivor に
    数えられるはずの entry が、typo を "insufficient" 扱いで無音に取りこぼす
    穴を閉じたことの直接証拠）。"""
    entry = _sufficient_clip_entry()
    original = dict(entry["original"])
    original["status"] = "sufficent"
    entry["original"] = original
    doc = {"clips": {"vocadito_1": entry}}
    with pytest.raises(bm.BuildM3dPairsError, match="既知の値でない"):
        bm._recompute_survivor_ids_from_clips(doc, source="test", fixture_clip_ids=["vocadito_1"])


def test_recompute_survivor_ids_from_clips_rejects_typo_status_in_variant_entry():
    entry = _sufficient_clip_entry()
    entry[bm.VOCADITO_VARIANT_ORDER[0]] = _gate_result("sufficent")  # type: ignore[arg-type]
    doc = {"clips": {"vocadito_1": entry}}
    with pytest.raises(bm.BuildM3dPairsError, match="既知の値でない"):
        bm._recompute_survivor_ids_from_clips(doc, source="test", fixture_clip_ids=["vocadito_1"])


def test_screen_m3d_clips_producer_status_contract_is_closed_two_value_enumeration():
    """`scripts/screen_m3d_clips.py::_gate_one` が実際に emit する `status` は
    `svp_rpe.melody.observability.assess_observability`（single source of
    truth）が返す値をそのまま転記しているだけであり、その関数自体が
    `"sufficient"`/`"insufficient"` の 2 値以外を返し得ないことをソースから
    機械確認する（W1 の前提「producer 契約は既に閉じた 2 値 enumeration」の
    実在検証・回帰ガード）。"""
    import inspect

    from svp_rpe.melody import observability as obs_module

    source = inspect.getsource(obs_module.assess_observability)
    assert 'status = "sufficient" if not reasons else "insufficient"' in source

    gate_one_source = inspect.getsource(sm._gate_one)
    assert '"status": report.status,' in gate_one_source


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
                "original": _gate_result("insufficient"),
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


# --------------------------------------------------------------------------- #
# T1（Codex レビュー第 3 巡・採用）: screening record の transform_parameters
# を builder 定数（VOCADITO_SEMITONES/VOCADITO_TIME_RATES）と選定前に照合する
# --------------------------------------------------------------------------- #
def _matching_transform_parameters() -> Dict[str, list]:
    return {
        "semitones": list(bm.VOCADITO_SEMITONES),
        "time_rates": list(bm.VOCADITO_TIME_RATES),
    }


def test_verify_screening_transform_parameters_accepts_matching_record():
    doc = {"transform_parameters": _matching_transform_parameters()}
    bm._verify_screening_transform_parameters(doc, source="test")  # raises なし


def test_verify_screening_transform_parameters_rejects_altered_semitones():
    doc = {
        "transform_parameters": {
            "semitones": [3.0, -6.0],  # -5.0 から改変
            "time_rates": list(bm.VOCADITO_TIME_RATES),
        }
    }
    with pytest.raises(bm.BuildM3dPairsError, match="semitones"):
        bm._verify_screening_transform_parameters(doc, source="test")


def test_verify_screening_transform_parameters_rejects_altered_time_rates():
    doc = {
        "transform_parameters": {
            "semitones": list(bm.VOCADITO_SEMITONES),
            "time_rates": [0.87, 1.20],  # 1.12 から改変
        }
    }
    with pytest.raises(bm.BuildM3dPairsError, match="time_rates"):
        bm._verify_screening_transform_parameters(doc, source="test")


def test_verify_screening_transform_parameters_rejects_missing_field():
    """`transform_parameters` フィールド自体が欠落している record も
    fail-closed（黙認しない）。"""
    doc: Dict[str, object] = {}
    with pytest.raises(bm.BuildM3dPairsError, match="transform_parameters"):
        bm._verify_screening_transform_parameters(doc, source="test")


def test_verify_screening_transform_parameters_rejects_missing_subfield():
    """`transform_parameters` は存在するが `time_rates` サブフィールドが
    欠落している record も fail-closed。"""
    doc = {"transform_parameters": {"semitones": list(bm.VOCADITO_SEMITONES)}}
    with pytest.raises(bm.BuildM3dPairsError, match="time_rates"):
        bm._verify_screening_transform_parameters(doc, source="test")


# --------------------------------------------------------------------------- #
# V1（Codex レビュー第 5 巡・採用）: screening record の観測経路
# （top-level route + 全エントリの route_provenance）を prereg_v2 §2 の
# 事前登録値（crepe_direct）へ選定前に束縛する
# --------------------------------------------------------------------------- #
def test_verify_screening_route_binding_accepts_matching_record():
    doc = {
        "route": "crepe_direct",
        "clips": {
            "vocadito_1": _sufficient_clip_entry(),
            "vocadito_2": _sufficient_clip_entry(),
        },
    }
    # 第 6 巡 W2 対応（部分採用 (b)）: 戻り値は record 内で相互一致確認済みの
    # 単一 route_provenance から取り出した抽出器 pin（呼び出し側 `run_build`
    # が pins サイドカーへ記録するために使う）。
    result = bm._verify_screening_route_binding(doc, source="test")
    assert result == {
        "extractor_code_sha256": _VALID_ROUTE_PROVENANCE["extractor_code_sha256"],
        "extractor_weights_sha256": _VALID_ROUTE_PROVENANCE["extractor_weights_sha256"],
    }


def test_verify_screening_route_binding_rejects_when_no_route_provenance_entries_found():
    """`clips` はあるが `route_provenance` を持つエントリが 1 件も無い record
    （= 全 clip が S1 insufficient で `original` すら route_provenance を
    持たない、または clips 自体が空）は、抽出器 pin を導出できないため
    fail-closed（第 6 巡 W2 対応・部分採用 (b) の前提）。"""
    doc = {"route": "crepe_direct", "clips": {}}
    with pytest.raises(bm.BuildM3dPairsError, match="route_provenance を持つエントリ"):
        bm._verify_screening_route_binding(doc, source="test")


def test_verify_screening_route_binding_skips_absent_variant_entries_for_insufficient_clip():
    """S1 insufficient な clip（`original` のみで変形結果を持たない非対称性
    — `_recompute_survivor_ids_from_clips` と同じ契約）でも、存在する entry
    （`original`）だけで route 束縛検証が完走する。"""
    doc = {
        "route": "crepe_direct",
        "clips": {
            "vocadito_1": _sufficient_clip_entry(),
            "vocadito_2": _insufficient_clip_entry(),
        },
    }
    bm._verify_screening_route_binding(doc, source="test")  # raises なし


def test_verify_screening_route_binding_rejects_altered_route():
    doc = {
        "route": "clap_direct",  # 事前登録値 crepe_direct から改変
        "clips": {"vocadito_1": _sufficient_clip_entry()},
    }
    with pytest.raises(bm.BuildM3dPairsError, match="route"):
        bm._verify_screening_route_binding(doc, source="test")


def test_verify_screening_route_binding_rejects_missing_route_field():
    doc = {"clips": {"vocadito_1": _sufficient_clip_entry()}}
    with pytest.raises(bm.BuildM3dPairsError, match="route"):
        bm._verify_screening_route_binding(doc, source="test")


def test_verify_screening_route_binding_rejects_provenance_mismatch_across_entries():
    """1 clip の `original` entry だけ別の `route_provenance`（= 別環境/別
    抽出器 pin の観測）を持つ record は、record 内の相互一致要件で拒否される
    ——CREPE weights/code drift や複数環境のマージによる混在 census を検出する。
    """
    tampered_provenance = dict(_VALID_ROUTE_PROVENANCE)
    tampered_provenance["extractor_weights_sha256"] = hashlib.sha256(
        b"different-weights"
    ).hexdigest()
    tampered_entry = _sufficient_clip_entry()
    tampered_entry["original"] = _gate_result("sufficient", route_provenance=tampered_provenance)
    doc = {
        "route": "crepe_direct",
        "clips": {
            "vocadito_1": _sufficient_clip_entry(),
            "vocadito_2": tampered_entry,
        },
    }
    with pytest.raises(bm.BuildM3dPairsError, match="混在"):
        bm._verify_screening_route_binding(doc, source="test")


def test_verify_screening_route_binding_rejects_missing_weights_sha256():
    incomplete_provenance = {"route": "crepe_direct", "extractor_code_sha256": "a" * 64}
    entry = _sufficient_clip_entry()
    entry["original"] = _gate_result("sufficient", route_provenance=incomplete_provenance)
    doc = {"route": "crepe_direct", "clips": {"vocadito_1": entry}}
    with pytest.raises(bm.BuildM3dPairsError, match="extractor_weights_sha256"):
        bm._verify_screening_route_binding(doc, source="test")


def test_verify_screening_route_binding_rejects_non_hex_code_sha256():
    bad_provenance = {
        "route": "crepe_direct",
        "extractor_code_sha256": "x" * 64,  # 非 hex
        "extractor_weights_sha256": "b" * 64,
    }
    entry = _sufficient_clip_entry()
    entry["original"] = _gate_result("sufficient", route_provenance=bad_provenance)
    doc = {"route": "crepe_direct", "clips": {"vocadito_1": entry}}
    with pytest.raises(bm.BuildM3dPairsError, match="extractor_code_sha256"):
        bm._verify_screening_route_binding(doc, source="test")


def test_verify_screening_route_binding_rejects_non_mapping_route_provenance():
    entry = _sufficient_clip_entry()
    entry["original"] = {**_gate_result("sufficient"), "route_provenance": "not-a-mapping"}
    doc = {"route": "crepe_direct", "clips": {"vocadito_1": entry}}
    with pytest.raises(bm.BuildM3dPairsError, match="mapping"):
        bm._verify_screening_route_binding(doc, source="test")


def test_run_and_publish_v2_rejects_when_screening_route_mismatches_preregistered_value(
    tmp_path: Path,
):
    """end-to-end（配線確認）: record の top-level `route` が事前登録値
    （crepe_direct）と異なる場合、`run_and_publish` は生成を一切始めず
    fail-closed で拒否する（V1 の核心主張）。"""
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
    doc = json.loads(screening_path.read_text(encoding="utf-8"))
    doc["route"] = "clap_direct"  # crepe_direct から改変
    screening_path.write_text(json.dumps(doc, indent=2, sort_keys=True), encoding="utf-8")
    out_dir = tmp_path / "external_m3d" / "m3d_pairs_v2"

    with pytest.raises(bm.BuildM3dPairsError, match="route"):
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


def test_committed_screening_v2_record_passes_route_binding_verification():
    """committed `docs/measurements/m3d_2026-08/screening_v2.json`（実測記録
    そのもの）が V1 検証チェーン（top-level route の事前登録値との逐語一致・
    record 内 route_provenance の相互一致・必須 sha256 pin の書式）を満たす
    ことを確認する（crepe/tensorflow 非依存の実物 fixture テスト・恒久回帰
    ガード。要求どおり、満たさなければここで停止し record 自体は書き換え
    ない）。"""
    path = ROOT / "docs" / "measurements" / "m3d_2026-08" / "screening_v2.json"
    doc, _digest = bm._load_screening_record(path)
    result = bm._verify_screening_route_binding(doc, source=str(path))
    # 第 6 巡 W2 対応（部分採用 (a) の実在検証・恒久回帰ガード）: committed
    # record が実際に単一の抽出器 pin へ束縛されていることを、返り値の
    # 具体的な digest 値で確認する（`tests/test_m3d_bit_identity_record.py`
    # の run1/run2 照合テストが期待するのと同じ値）。
    assert result == {
        "extractor_code_sha256": "bc9987ac6245b47f67d4ff7fd71be723e790b2feae23f3adb6ada6fa35882fcd",
        "extractor_weights_sha256": "fb369944d4feb5964cae189dceba1e554d6471f0be712aad61fc087afaef4a55",
    }


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
    # U2（Codex レビュー第 4 巡）: 実際に公開まで到達させるため builder と
    # 同一手順の変形バイト sha256 を record へ埋める。
    variant_audio_sha256_by_clip = {
        cid: _real_variant_audio_sha256_map(bm.vocadito_audio_path(vocadito_dir, cid))
        for cid in survivors
    }
    screening_path = tmp_path / "screening_v2.json"
    _write_screening_record(
        screening_path,
        survivor_clip_ids=survivors,
        m2c_external_fixtures_sha256=fixtures_sha256,
        variant_audio_sha256_by_clip=variant_audio_sha256_by_clip,
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


# --------------------------------------------------------------------------- #
# 第 6 巡 W2（部分採用 (b)）: pins サイドカーへ記録した抽出器 pin
# （screening_extractor_code_sha256/screening_extractor_weights_sha256）を
# `check_existing` が record 現物と fail-closed で再照合する
# --------------------------------------------------------------------------- #
def test_build_v2_bundle_pins_sidecar_contains_extractor_pins(tmp_path: Path):
    """`_build_v2_bundle`（＝ `run_and_publish` v2 経路）が書く pins サイド
    カーに、record 内の単一 route_provenance から導出した抽出器 pin が
    記録されていることの回帰ガード。"""
    bundle = _build_v2_bundle(tmp_path)
    pins_doc = json.loads(bundle["pins_out"].read_text(encoding="utf-8"))
    assert pins_doc["screening_extractor_code_sha256"] == _VALID_ROUTE_PROVENANCE[
        "extractor_code_sha256"
    ]
    assert pins_doc["screening_extractor_weights_sha256"] == _VALID_ROUTE_PROVENANCE[
        "extractor_weights_sha256"
    ]


def test_check_existing_detects_screening_extractor_code_pin_tampered_in_sidecar(
    tmp_path: Path,
):
    """screening record 自体は無傷のまま、pins サイドカー側の
    `screening_extractor_code_sha256` だけを直接書き換えた場合——
    `screening_record_sha256` によるバイト一致検証は素通りする（record の
    バイト列は変わっていないため）——`check_existing` は record 現物から
    再導出した値との不一致で fail-closed 検出する（本対応がクローズする
    新規の攻撃面: 既存の record バイト pin は pins.json 内の派生フィールド
    自体の改ざんを検出しない）。"""
    bundle = _build_v2_bundle(tmp_path)
    pins_doc = json.loads(bundle["pins_out"].read_text(encoding="utf-8"))
    pins_doc["screening_extractor_code_sha256"] = "f" * 64
    bundle["pins_out"].write_text(json.dumps(pins_doc, indent=2, sort_keys=True), encoding="utf-8")

    with pytest.raises(bm.BuildM3dPairsError, match="screening_extractor_code_sha256 mismatch"):
        bm.check_existing(
            manifest_out=bundle["manifest_out"],
            pins_out=bundle["pins_out"],
            vocadito_dir=bundle["vocadito_dir"],
            fixtures_path=bundle["fixtures_path"],
            synth_specs_path=REAL_SYNTH_SPECS_V2_PATH,
        )


def test_check_existing_detects_screening_extractor_weights_pin_tampered_in_sidecar(
    tmp_path: Path,
):
    bundle = _build_v2_bundle(tmp_path)
    pins_doc = json.loads(bundle["pins_out"].read_text(encoding="utf-8"))
    pins_doc["screening_extractor_weights_sha256"] = "f" * 64
    bundle["pins_out"].write_text(json.dumps(pins_doc, indent=2, sort_keys=True), encoding="utf-8")

    with pytest.raises(bm.BuildM3dPairsError, match="screening_extractor_weights_sha256 mismatch"):
        bm.check_existing(
            manifest_out=bundle["manifest_out"],
            pins_out=bundle["pins_out"],
            vocadito_dir=bundle["vocadito_dir"],
            fixtures_path=bundle["fixtures_path"],
            synth_specs_path=REAL_SYNTH_SPECS_V2_PATH,
        )


def test_find_screening_record_extractor_provenance_returns_matching_pin():
    doc = {
        "clips": {
            "vocadito_1": _sufficient_clip_entry(),
        }
    }
    result = bm._find_screening_record_extractor_provenance(doc)
    assert result == {
        "extractor_code_sha256": _VALID_ROUTE_PROVENANCE["extractor_code_sha256"],
        "extractor_weights_sha256": _VALID_ROUTE_PROVENANCE["extractor_weights_sha256"],
    }


def test_find_screening_record_extractor_provenance_returns_none_when_absent():
    assert bm._find_screening_record_extractor_provenance({"clips": {}}) is None
    assert bm._find_screening_record_extractor_provenance({}) is None
    doc_missing_provenance = {
        "clips": {"vocadito_1": {"original": {"status": "sufficient"}}}
    }
    assert bm._find_screening_record_extractor_provenance(doc_missing_provenance) is None


# --------------------------------------------------------------------------- #
# 第 6 巡 W2（部分採用 (b)）: `_load_and_validate_pins` の
# screening_extractor_*_sha256 pair 整合・書式検証
# --------------------------------------------------------------------------- #
def _base_pins_doc_with_screening_record() -> Dict[str, object]:
    return {
        "schema": bm._PINS_SCHEMA,
        "generated_utc": "2026-08-09T00:00:00+00:00",
        "m2c_external_fixtures_sha256": "a" * 64,
        "m3d_synth_specs_sha256": "b" * 64,
        "manifest_sha256": "c" * 64,
        "audio_sha256": {},
        "material": {},
        "screening_record_path": "build/external_m3d/m3d_screening_v2.json",
        "screening_record_sha256": "d" * 64,
    }


def test_load_and_validate_pins_accepts_paired_extractor_fields(tmp_path: Path):
    doc = _base_pins_doc_with_screening_record()
    doc["screening_extractor_code_sha256"] = "e" * 64
    doc["screening_extractor_weights_sha256"] = "f" * 64
    path = tmp_path / "pins.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    loaded = bm._load_and_validate_pins(path)  # raises なし
    assert loaded["screening_extractor_code_sha256"] == "e" * 64


def test_load_and_validate_pins_rejects_extractor_code_without_weights(tmp_path: Path):
    doc = _base_pins_doc_with_screening_record()
    doc["screening_extractor_code_sha256"] = "e" * 64
    # screening_extractor_weights_sha256 を欠落させる。
    path = tmp_path / "pins.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(
        bm.BuildM3dPairsError,
        match="screening_extractor_code_sha256.*screening_extractor_weights_sha256",
    ):
        bm._load_and_validate_pins(path)


def test_load_and_validate_pins_rejects_malformed_extractor_sha256(tmp_path: Path):
    doc = _base_pins_doc_with_screening_record()
    doc["screening_extractor_code_sha256"] = "not-a-sha256"
    doc["screening_extractor_weights_sha256"] = "f" * 64
    path = tmp_path / "pins.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(bm.BuildM3dPairsError, match="screening_extractor_code_sha256"):
        bm._load_and_validate_pins(path)


def test_load_and_validate_pins_rejects_extractor_pins_without_screening_record_pin(
    tmp_path: Path,
):
    """`screening_record_sha256`（記録元の record 自体の pin）が無いのに
    `screening_extractor_*_sha256`（その派生値）だけが単独で存在するのは
    sidecar 破損とみなし fail-closed とする。"""
    doc = {
        "schema": bm._PINS_SCHEMA,
        "generated_utc": "2026-08-09T00:00:00+00:00",
        "m2c_external_fixtures_sha256": "a" * 64,
        "m3d_synth_specs_sha256": "b" * 64,
        "manifest_sha256": "c" * 64,
        "audio_sha256": {},
        "material": {},
        # screening_record_path/screening_record_sha256 は両方欠落（v1 経路）。
        "screening_extractor_code_sha256": "e" * 64,
        "screening_extractor_weights_sha256": "f" * 64,
    }
    path = tmp_path / "pins.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(bm.BuildM3dPairsError, match="screening_record_sha256"):
        bm._load_and_validate_pins(path)


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


# --------------------------------------------------------------------------- #
# U1（Codex レビュー第 4 巡・採用）: screen_m3d_clips.py のスナップショット
# 束縛（同一 snapshot バイトから観測・sha256 記録・4 変形生成を全て行う）
# --------------------------------------------------------------------------- #
class _FakeObservabilityReport:
    """`assess_observability` の戻り値スタブ（crepe/tensorflow 非依存）。"""

    def __init__(self, status: str) -> None:
        self.status = status
        self.reasons: list = []

    def to_dict(self) -> Dict[str, object]:
        return {"status": self.status}


def test_freeze_and_verify_vocadito_snapshot_returns_matching_snapshot(tmp_path: Path):
    audio_path = tmp_path / "orig.wav"
    audio_path.write_bytes(b"vocadito-bytes")
    expected_sha256 = hashlib.sha256(b"vocadito-bytes").hexdigest()
    snapshot_dir = tmp_path / "snap"
    snapshot_dir.mkdir()

    snapshot_path = sm._freeze_and_verify_vocadito_snapshot(
        "vocadito_1", audio_path, expected_sha256, snapshot_dir
    )
    assert snapshot_path == snapshot_dir / "vocadito_1.wav"
    assert snapshot_path.read_bytes() == b"vocadito-bytes"


def test_freeze_and_verify_vocadito_snapshot_rejects_pin_mismatch(tmp_path: Path):
    audio_path = tmp_path / "orig.wav"
    audio_path.write_bytes(b"vocadito-bytes")
    snapshot_dir = tmp_path / "snap"
    snapshot_dir.mkdir()

    with pytest.raises(sm.ScreenM3dClipsError, match="sha256 が pin と不一致"):
        sm._freeze_and_verify_vocadito_snapshot("vocadito_1", audio_path, "f" * 64, snapshot_dir)


def test_screen_uses_single_snapshot_for_observation_and_variant_decode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`screen()` end-to-end（fake route runner・crepe 非依存）: (a) 観測入力
    (c) 4 変形生成の decode 入力が同一のスナップショット path（原本ではない）
    であること、および (b) 記録される `audio_sha256` がそのスナップショット
    バイト（= pin）から計算されていることを直接確認する（U1 の核心主張の
    構造的検証）。"""
    n_clips = 1
    vocadito_dir, fixtures_path, expected = _make_vocadito_pool(tmp_path, n_clips=n_clips)
    clip_id = "vocadito_1"

    observed_paths: list = []

    def fake_observe(audio_path, route):  # noqa: ANN001, ANN201 (test stub)
        observed_paths.append(audio_path)
        return None, {"fake_provenance": True}

    def fake_assess(observation, thresholds):  # noqa: ANN001, ANN201 (test stub)
        return _FakeObservabilityReport("sufficient")

    monkeypatch.setattr(sm, "observe_via_route_with_provenance", fake_observe)
    monkeypatch.setattr(sm, "assess_observability", fake_assess)

    decode_paths: list = []
    real_load = sm.librosa.load

    def spy_load(path, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN201
        decode_paths.append(path)
        return real_load(path, *args, **kwargs)

    monkeypatch.setattr(sm.librosa, "load", spy_load)

    doc = sm.screen(
        vocadito_dir=vocadito_dir,
        fixtures_path=fixtures_path,
        m1_registry_path=harness.M1_REGISTRY_PATH,
    )

    original_resolved_path = bm.vocadito_audio_path(vocadito_dir, clip_id).resolve()
    assert observed_paths, "observe_via_route_with_provenance が呼ばれていない"
    # U1: 観測入力は原本ではなくスナップショット path（原本とはバイト内容が
    # 同一でも path 自体は異なる——原本が差し替わっても影響を受けない構造）。
    assert Path(observed_paths[0]).resolve() != original_resolved_path
    assert decode_paths, "librosa.load（S2 の 4 変形生成 decode）が呼ばれていない"
    # U1 の核心: 観測が読んだ path と変形生成が decode した path が完全に同一
    # ——同一スナップショットバイトから (a) と (c) の両方を行っていることの
    # 直接証拠。
    assert str(Path(decode_paths[0]).resolve()) == str(Path(observed_paths[0]).resolve())
    # U1: 記録される audio_sha256 はスナップショットバイト（= pin と一致する
    # バイト）から計算されている。
    assert doc["clips"][clip_id]["original"]["audio_sha256"] == expected[clip_id]
    assert doc["survivor_clip_ids"] == [clip_id]


# --------------------------------------------------------------------------- #
# U2（Codex レビュー第 4 巡・採用）: 生成変形 WAV のバイトを screening record
# の audio_sha256 と publish 前に照合する（不一致は fail-closed・公開しない）
# --------------------------------------------------------------------------- #
def test_verify_generated_variant_audio_hashes_accepts_matching_bytes(tmp_path: Path):
    staging = tmp_path / "staging"
    staging.mkdir()
    variant_bytes = b"fake-variant-bytes"
    (staging / "vocadito_1__pitch_p3.wav").write_bytes(variant_bytes)
    expected_sha256 = hashlib.sha256(variant_bytes).hexdigest()
    screening_doc = {
        "clips": {"vocadito_1": {"pitch_+3st": {"audio_sha256": expected_sha256}}}
    }
    vocadito_variants = {
        "vocadito_1": {"pitch_+3st": Path("out_dir") / "vocadito_1__pitch_p3.wav"}
    }
    bm._verify_generated_variant_audio_hashes(
        screening_doc,
        source="test",
        vocadito_variants=vocadito_variants,
        staging_wav_dir=staging,
    )  # raises なし


def test_verify_generated_variant_audio_hashes_rejects_tampered_bytes(tmp_path: Path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "vocadito_1__pitch_p3.wav").write_bytes(b"actually-generated-bytes")
    screening_doc = {
        # record は別のバイト列を screening 時に見ていたと主張している。
        "clips": {"vocadito_1": {"pitch_+3st": {"audio_sha256": "0" * 64}}}
    }
    vocadito_variants = {
        "vocadito_1": {"pitch_+3st": Path("out_dir") / "vocadito_1__pitch_p3.wav"}
    }
    with pytest.raises(bm.BuildM3dPairsError, match="sha256 mismatch"):
        bm._verify_generated_variant_audio_hashes(
            screening_doc,
            source="test",
            vocadito_variants=vocadito_variants,
            staging_wav_dir=staging,
        )


def test_verify_generated_variant_audio_hashes_rejects_missing_clip_entry(tmp_path: Path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "vocadito_1__pitch_p3.wav").write_bytes(b"x")
    screening_doc = {"clips": {}}
    vocadito_variants = {
        "vocadito_1": {"pitch_+3st": Path("out_dir") / "vocadito_1__pitch_p3.wav"}
    }
    with pytest.raises(bm.BuildM3dPairsError, match="欠落"):
        bm._verify_generated_variant_audio_hashes(
            screening_doc,
            source="test",
            vocadito_variants=vocadito_variants,
            staging_wav_dir=staging,
        )


def test_verify_generated_variant_audio_hashes_rejects_missing_audio_sha256_field(
    tmp_path: Path,
):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "vocadito_1__pitch_p3.wav").write_bytes(b"x")
    screening_doc = {"clips": {"vocadito_1": {"pitch_+3st": {}}}}
    vocadito_variants = {
        "vocadito_1": {"pitch_+3st": Path("out_dir") / "vocadito_1__pitch_p3.wav"}
    }
    with pytest.raises(bm.BuildM3dPairsError, match="audio_sha256"):
        bm._verify_generated_variant_audio_hashes(
            screening_doc,
            source="test",
            vocadito_variants=vocadito_variants,
            staging_wav_dir=staging,
        )


def test_run_and_publish_v2_rejects_when_screening_variant_hash_mismatches_generated_bytes(
    tmp_path: Path,
):
    """end-to-end（配線確認）: screening record の 1 variant の `audio_sha256`
    だけが実際に生成されるバイトと不一致な場合、`run_and_publish` は生成を
    完了させても公開せず fail-closed で拒否する（U2 の核心主張）。"""
    n_clips = 20
    vocadito_dir, fixtures_path, _expected = _make_vocadito_pool(tmp_path, n_clips=n_clips)
    _fixtures, fixtures_sha256 = bm.load_m2c_fixtures(fixtures_path)
    survivors = [f"vocadito_{i}" for i in range(1, n_clips + 1)]
    variant_audio_sha256_by_clip = {
        cid: _real_variant_audio_sha256_map(bm.vocadito_audio_path(vocadito_dir, cid))
        for cid in survivors
    }
    # 実際に選定される（tuning/holdout に入る）clip を狙い撃ちして改ざんする
    # ——選定されない 2 件（N=20 のうち 18 件のみ使用）を改ざんしても
    # `_verify_generated_variant_audio_hashes` は素通りしてしまうため。
    tuning_ids, _holdout_ids = bm.select_clips_v2(survivors)
    tampered_clip = tuning_ids[0]
    tampered_variant = bm.VOCADITO_VARIANT_ORDER[0]
    variant_audio_sha256_by_clip[tampered_clip][tampered_variant] = "f" * 64

    screening_path = tmp_path / "screening_v2.json"
    _write_screening_record(
        screening_path,
        survivor_clip_ids=survivors,
        m2c_external_fixtures_sha256=fixtures_sha256,
        variant_audio_sha256_by_clip=variant_audio_sha256_by_clip,
    )
    out_dir = tmp_path / "external_m3d" / "m3d_pairs_v2"
    manifest_out = tmp_path / "manifest_v2.yaml"
    pins_out = tmp_path / "pins_v2.json"

    with pytest.raises(bm.BuildM3dPairsError, match="audio_sha256"):
        bm.run_and_publish(
            vocadito_dir=vocadito_dir,
            out_dir=out_dir,
            manifest_out=manifest_out,
            pins_out=pins_out,
            fixtures_path=fixtures_path,
            synth_specs_path=REAL_SYNTH_SPECS_V2_PATH,
            screening_record_path=screening_path,
        )
    assert not out_dir.exists()
    assert not manifest_out.exists()
    assert not pins_out.exists()
