"""tests/test_diagnose_m3d_notcomparable_preflight.py — `scripts/
diagnose_m3d_notcomparable.py` の Codex レビュー #255 第 10 巡指摘 2 件
（AA1・AA2）の直接検証。

外部 vocadito F0 CSV には一切触れない（fixture は全て synth 素材=1 pair の
最小合成データで、`_build_section3` の real clip ループを 0 件に保ち、
アノテーション CSV の実 I/O を発生させない。M2c 流儀通り、外部素材への
依存はテストに持ち込まない）。

(AA1) ``_reject_output_input_collisions``: ``--output`` が ``--run1``/
``--manifest``/``--registry``/``--m2c-fixtures``/消費対象アノテーション CSV
のいずれかと衝突する場合に fail-closed で拒否すること、衝突しなければ
何も起きないこと。
(AA2) ``build_diagnosis`` が返す ``inputs`` 節のパスラベルが、既定値の
ハードコード文字列ではなく実際に渡した引数由来（repo 外なら resolve 済み
絶対パス）で記録されること。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import diagnose_m3d_notcomparable as dm  # noqa: E402


# --------------------------------------------------------------------------- #
# AA1: _reject_output_input_collisions
# --------------------------------------------------------------------------- #
def test_reject_output_input_collisions_detects_run1_collision(tmp_path: Path) -> None:
    run1 = tmp_path / "run1.json"
    with pytest.raises(SystemExit, match="fail-closed"):
        dm._reject_output_input_collisions(
            output_path=run1,  # --output を誤って --run1 と同じパスに向けた
            run1_path=run1,
            manifest_path=tmp_path / "manifest.yaml",
            registry_path=tmp_path / "registry.yaml",
            m2c_fixtures_path=tmp_path / "fixtures.yaml",
            annotation_paths=[],
        )


def test_reject_output_input_collisions_detects_manifest_collision(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.yaml"
    with pytest.raises(SystemExit, match="fail-closed"):
        dm._reject_output_input_collisions(
            output_path=manifest,
            run1_path=tmp_path / "run1.json",
            manifest_path=manifest,
            registry_path=tmp_path / "registry.yaml",
            m2c_fixtures_path=tmp_path / "fixtures.yaml",
            annotation_paths=[],
        )


def test_reject_output_input_collisions_detects_registry_collision(tmp_path: Path) -> None:
    registry = tmp_path / "registry.yaml"
    with pytest.raises(SystemExit, match="fail-closed"):
        dm._reject_output_input_collisions(
            output_path=registry,
            run1_path=tmp_path / "run1.json",
            manifest_path=tmp_path / "manifest.yaml",
            registry_path=registry,
            m2c_fixtures_path=tmp_path / "fixtures.yaml",
            annotation_paths=[],
        )


def test_reject_output_input_collisions_detects_m2c_fixtures_collision(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures.yaml"
    with pytest.raises(SystemExit, match="fail-closed"):
        dm._reject_output_input_collisions(
            output_path=fixtures,
            run1_path=tmp_path / "run1.json",
            manifest_path=tmp_path / "manifest.yaml",
            registry_path=tmp_path / "registry.yaml",
            m2c_fixtures_path=fixtures,
            annotation_paths=[],
        )


def test_reject_output_input_collisions_detects_annotation_csv_collision(tmp_path: Path) -> None:
    annotation_csv = tmp_path / "Annotations" / "F0" / "vocadito_3_f0.csv"
    with pytest.raises(SystemExit, match="fail-closed"):
        dm._reject_output_input_collisions(
            output_path=annotation_csv,
            run1_path=tmp_path / "run1.json",
            manifest_path=tmp_path / "manifest.yaml",
            registry_path=tmp_path / "registry.yaml",
            m2c_fixtures_path=tmp_path / "fixtures.yaml",
            annotation_paths=[annotation_csv, tmp_path / "Annotations" / "F0" / "vocadito_5_f0.csv"],
        )


def test_reject_output_input_collisions_passes_for_disjoint_paths(tmp_path: Path) -> None:
    # 例外が飛ばなければ OK。
    dm._reject_output_input_collisions(
        output_path=tmp_path / "out" / "diagnosis.json",
        run1_path=tmp_path / "run1.json",
        manifest_path=tmp_path / "manifest.yaml",
        registry_path=tmp_path / "registry.yaml",
        m2c_fixtures_path=tmp_path / "fixtures.yaml",
        annotation_paths=[
            tmp_path / "Annotations" / "F0" / "vocadito_3_f0.csv",
            tmp_path / "Annotations" / "F0" / "vocadito_5_f0.csv",
        ],
    )


def test_consumed_annotation_paths_enumerates_real_clips_only(tmp_path: Path) -> None:
    manifest_pairs = [
        {
            "pair_id": "pt_real_tuning_vocadito_3_pitch_p3",
            "audio_a": "vocadito_3.wav",
            "audio_b": "vocadito_3__pitch_p3.wav",
        },
        {
            "pair_id": "pt_real_tuning_vocadito_5_pitch_p3",
            "audio_a": "vocadito_5.wav",
            "audio_b": "vocadito_5__pitch_p3.wav",
        },
        {
            "pair_id": "st_synth_tuning_positive",
            "audio_a": "synth_pos.wav",
            "audio_b": "synth_pos__pitch_p3.wav",
        },
    ]
    paths = dm._consumed_annotation_paths(manifest_pairs, tmp_path)
    assert paths == [
        dm._annotation_path(tmp_path, "vocadito_3"),
        dm._annotation_path(tmp_path, "vocadito_5"),
    ]


# --------------------------------------------------------------------------- #
# AA2: inputs 節が実引数由来のパスを記録する
# --------------------------------------------------------------------------- #
def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_minimal_fixture_set(base: Path) -> Dict[str, Path]:
    """synth 素材 1 pair のみの最小合成 fixture 一式を書き、
    ``_build_section3`` の real clip ループを 0 件に保つ（外部 vocadito CSV
    への依存を完全に排除する）。"""
    registry_path = base / "registry.yaml"
    registry_doc = {
        "observation_gate": {
            "min_voiced_coverage": 0.5,
            "min_note_count": 8,
            "min_phrase_count": 2,
            "min_confidence_mean": 0.5,
            "max_low_confidence_rate": 0.5,
            "max_octave_jump_rate": 0.1,
            "voiced_confidence_floor": 0.3,
            "low_confidence_floor": 0.3,
            "min_cross_extractor_agreement": None,
            "note_min_run_frames": 3,
            "median_filter_frames": 5,
            "phrase_gap_sec": 0.6,
            "octave_jump_semitones": 11.0,
        }
    }
    registry_path.write_text(yaml.safe_dump(registry_doc), encoding="utf-8")
    registry_sha256 = _sha256_bytes(registry_path.read_bytes())

    manifest_path = base / "manifest.yaml"
    manifest_doc = {
        "pairs": [
            {
                "pair_id": "st_synth_tuning_1",
                "kind": "positive_transform",
                "split": "tuning",
                "expected": "same",
                "audio_a": "synth_pos.wav",
                "audio_b": "synth_pos__pitch_p3.wav",
            }
        ]
    }
    manifest_path.write_text(yaml.safe_dump(manifest_doc), encoding="utf-8")
    manifest_sha256 = _sha256_bytes(manifest_path.read_bytes())

    run1_path = base / "run1.json"
    run1_doc: Dict[str, Any] = {
        "manifest_sha256": manifest_sha256,
        "pairs": {
            "st_synth_tuning_1": {
                "comparison": {
                    "evidence": "none",
                    "reasons": ["evidence_thresholds_uncalibrated"],
                    "coverage": {},
                    "provenance": {"m1_registry_sha256": registry_sha256},
                }
            }
        }
    }
    run1_path.write_text(json.dumps(run1_doc), encoding="utf-8")

    m2c_fixtures_path = base / "m2c_external_fixtures.yaml"
    m2c_fixtures_path.write_text(yaml.safe_dump({"fixtures": {}}), encoding="utf-8")

    return {
        "run1_path": run1_path,
        "manifest_path": manifest_path,
        "registry_path": registry_path,
        "m2c_fixtures_path": m2c_fixtures_path,
    }


def test_build_diagnosis_inputs_reflect_supplied_cli_paths_not_defaults(tmp_path: Path) -> None:
    paths = _write_minimal_fixture_set(tmp_path)
    vocadito_dir = tmp_path / "vocadito"  # 0 real clip なので実在不要

    # CC1（第 12 巡）以降、build_diagnosis は manifest を自分で読まない
    # ——呼び出し側（ここではテスト自身が main() の代わり）が 1 回読んで
    # manifest_doc/manifest_sha256 を渡す。
    manifest_doc, _manifest_bytes, manifest_sha256 = dm._load_yaml(paths["manifest_path"])

    diagnosis = dm.build_diagnosis(
        run1_path=paths["run1_path"],
        manifest_path=paths["manifest_path"],
        manifest_doc=manifest_doc,
        manifest_sha256=manifest_sha256,
        registry_path=paths["registry_path"],
        m2c_fixtures_path=paths["m2c_fixtures_path"],
        vocadito_dir=vocadito_dir,
        generated_utc=None,
    )

    inputs = diagnosis["inputs"]
    # tmp_path はリポジトリ ROOT 配下ではないため、_display_path は
    # resolve 済み絶対パス文字列にフォールバックする——是正前のハードコード
    # 既定値（"docs/measurements/m3d_2026-08/run1.json" 等）とは一致し得ない
    # ため、これらの assert は AA2 是正がなければ必ず落ちる。
    assert inputs["run1_path"] == str(paths["run1_path"].resolve())
    assert inputs["manifest_path"] == str(paths["manifest_path"].resolve())
    assert inputs["m2c_external_fixtures_path"] == str(paths["m2c_fixtures_path"].resolve())
    assert inputs["gate_registry_path"] == str(paths["registry_path"].resolve())
    assert inputs["run1_path"] != "docs/measurements/m3d_2026-08/run1.json"
    assert inputs["manifest_path"] != "tests/fixtures/melody_bench/m3d_pairs_manifest.yaml"
    assert (
        inputs["m2c_external_fixtures_path"]
        != "tests/fixtures/melody_bench/m2c_external_fixtures.yaml"
    )
    assert inputs["gate_registry_path"] != "tests/fixtures/melody_bench/registry.yaml"
    # real clip が 0 件なので annotation_sha256 は空——外部素材未使用の確認。
    assert inputs["vocadito_annotation_sha256"] == {}


def test_main_output_flag_rejects_collision_before_any_write(tmp_path: Path, monkeypatch, capsys) -> None:
    """CLI 経由（``main()``）でも --output 衝突が導出前に拒否され、衝突先の
    実ファイルが書き換えられないこと（AA1 の end-to-end 確認）。"""
    paths = _write_minimal_fixture_set(tmp_path)
    vocadito_dir = tmp_path / "vocadito"
    original_manifest_bytes = paths["manifest_path"].read_bytes()

    argv = [
        "diagnose_m3d_notcomparable.py",
        "--run1",
        str(paths["run1_path"]),
        "--manifest",
        str(paths["manifest_path"]),
        "--registry",
        str(paths["registry_path"]),
        "--m2c-fixtures",
        str(paths["m2c_fixtures_path"]),
        "--vocadito-dir",
        str(vocadito_dir),
        "--output",
        str(paths["manifest_path"]),  # 意図的に --manifest と衝突させる
    ]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit):
        dm.main()

    # manifest.yaml が診断 JSON バイト列で上書きされていないこと。
    assert paths["manifest_path"].read_bytes() == original_manifest_bytes


def test_main_output_flag_writes_when_disjoint(tmp_path: Path, monkeypatch) -> None:
    paths = _write_minimal_fixture_set(tmp_path)
    vocadito_dir = tmp_path / "vocadito"
    output_path = tmp_path / "out" / "diagnosis.json"

    argv = [
        "diagnose_m3d_notcomparable.py",
        "--run1",
        str(paths["run1_path"]),
        "--manifest",
        str(paths["manifest_path"]),
        "--registry",
        str(paths["registry_path"]),
        "--m2c-fixtures",
        str(paths["m2c_fixtures_path"]),
        "--vocadito-dir",
        str(vocadito_dir),
        "--output",
        str(output_path),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    rc = dm.main()
    assert rc == 0
    assert output_path.exists()
    written = json.loads(output_path.read_bytes())
    assert written["inputs"]["run1_path"] == str(paths["run1_path"].resolve())


# --------------------------------------------------------------------------- #
# CC1（Codex レビュー #255 第 12 巡・P1）: manifest 読みの単一化
# --------------------------------------------------------------------------- #
def test_build_diagnosis_does_not_reread_manifest_path(tmp_path: Path) -> None:
    """構造テスト: build_diagnosis は ``manifest_path`` を自分で読まない
    ——存在しないパスを渡しても、``manifest_doc``/``manifest_sha256`` さえ
    正しく渡っていれば成功することで、内部で manifest を再読みしていない
    ことを直接証明する（是正前の実装なら FileNotFoundError で失敗した）。"""
    paths = _write_minimal_fixture_set(tmp_path)
    vocadito_dir = tmp_path / "vocadito"
    manifest_doc, _manifest_bytes, manifest_sha256 = dm._load_yaml(paths["manifest_path"])

    nonexistent_manifest_path = tmp_path / "does_not_exist_manifest.yaml"
    assert not nonexistent_manifest_path.exists()

    diagnosis = dm.build_diagnosis(
        run1_path=paths["run1_path"],
        manifest_path=nonexistent_manifest_path,
        manifest_doc=manifest_doc,
        manifest_sha256=manifest_sha256,
        registry_path=paths["registry_path"],
        m2c_fixtures_path=paths["m2c_fixtures_path"],
        vocadito_dir=vocadito_dir,
        generated_utc=None,
    )
    assert diagnosis["inputs"]["manifest_sha256"] == manifest_sha256


def test_main_reads_manifest_bytes_exactly_once_end_to_end(tmp_path: Path, monkeypatch) -> None:
    """end-to-end: ``--output`` ありの ``main()`` 実行でも、manifest は
    ``_load_yaml`` 経由でちょうど 1 回しか読まれないこと——preflight 用の
    アノテーション CSV 列挙と本導出（``build_diagnosis``）が同一バッファ
    （同一パース済み文書）を共有配線されていることの直接証拠。"""
    paths = _write_minimal_fixture_set(tmp_path)
    vocadito_dir = tmp_path / "vocadito"
    output_path = tmp_path / "out" / "diagnosis.json"

    call_counts: Dict[Path, int] = {}
    original_load_yaml = dm._load_yaml

    def _counting_load_yaml(path):
        resolved = Path(path).resolve()
        call_counts[resolved] = call_counts.get(resolved, 0) + 1
        return original_load_yaml(path)

    monkeypatch.setattr(dm, "_load_yaml", _counting_load_yaml)

    argv = [
        "diagnose_m3d_notcomparable.py",
        "--run1",
        str(paths["run1_path"]),
        "--manifest",
        str(paths["manifest_path"]),
        "--registry",
        str(paths["registry_path"]),
        "--m2c-fixtures",
        str(paths["m2c_fixtures_path"]),
        "--vocadito-dir",
        str(vocadito_dir),
        "--output",
        str(output_path),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    rc = dm.main()
    assert rc == 0

    manifest_resolved = paths["manifest_path"].resolve()
    assert call_counts[manifest_resolved] == 1, (
        f"manifest was read {call_counts[manifest_resolved]} time(s) via _load_yaml; "
        "expected exactly 1 (CC1: single-read wiring)"
    )


# --------------------------------------------------------------------------- #
# CC2（Codex レビュー #255 第 12 巡・P2）: pair 集合の完全一致検証
# --------------------------------------------------------------------------- #
def test_build_diagnosis_rejects_extra_pair_in_run1_not_in_manifest(tmp_path: Path) -> None:
    """余剰 pair 注入拒否: manifest_sha256 が正しくても、run1.json が
    manifest に無い pair_id を追加で持っていれば fail-closed。"""
    paths = _write_minimal_fixture_set(tmp_path)
    registry_sha256 = _sha256_bytes(paths["registry_path"].read_bytes())
    manifest_sha256 = _sha256_bytes(paths["manifest_path"].read_bytes())

    run1_doc = json.loads(paths["run1_path"].read_bytes())
    run1_doc["pairs"]["st_synth_tuning_extra_not_in_manifest"] = {
        "comparison": {
            "evidence": "none",
            "reasons": ["evidence_thresholds_uncalibrated"],
            "coverage": {},
            "provenance": {"m1_registry_sha256": registry_sha256},
        }
    }
    assert run1_doc["manifest_sha256"] == manifest_sha256  # 束縛は保ったまま
    paths["run1_path"].write_text(json.dumps(run1_doc), encoding="utf-8")

    manifest_doc, _manifest_bytes, manifest_sha256_reloaded = dm._load_yaml(paths["manifest_path"])
    with pytest.raises(SystemExit, match="fail-closed"):
        dm.build_diagnosis(
            run1_path=paths["run1_path"],
            manifest_path=paths["manifest_path"],
            manifest_doc=manifest_doc,
            manifest_sha256=manifest_sha256_reloaded,
            registry_path=paths["registry_path"],
            m2c_fixtures_path=paths["m2c_fixtures_path"],
            vocadito_dir=tmp_path / "vocadito",
            generated_utc=None,
        )


def test_build_diagnosis_rejects_pair_missing_from_run1_present_in_manifest(tmp_path: Path) -> None:
    """欠落 pair 拒否: manifest が要求する pair_id を run1.json が持っていなければ
    fail-closed（manifest_sha256 自体は正しいまま）。"""
    paths = _write_minimal_fixture_set(tmp_path)
    manifest_sha256 = _sha256_bytes(paths["manifest_path"].read_bytes())

    run1_doc = json.loads(paths["run1_path"].read_bytes())
    assert run1_doc["manifest_sha256"] == manifest_sha256
    run1_doc["pairs"] = {}  # manifest の唯一の pair を欠落させる
    paths["run1_path"].write_text(json.dumps(run1_doc), encoding="utf-8")

    manifest_doc, _manifest_bytes, manifest_sha256_reloaded = dm._load_yaml(paths["manifest_path"])
    with pytest.raises(SystemExit, match="fail-closed"):
        dm.build_diagnosis(
            run1_path=paths["run1_path"],
            manifest_path=paths["manifest_path"],
            manifest_doc=manifest_doc,
            manifest_sha256=manifest_sha256_reloaded,
            registry_path=paths["registry_path"],
            m2c_fixtures_path=paths["m2c_fixtures_path"],
            vocadito_dir=tmp_path / "vocadito",
            generated_utc=None,
        )


def test_build_diagnosis_accepts_when_run1_and_manifest_pair_sets_match(tmp_path: Path) -> None:
    """一致受理: run1.json の pair 集合と manifest の pair_id 集合が完全一致
    していれば、CC2 の検証で拒否されないこと（既存の最小 fixture は既に
    1 pair で一致している）。"""
    paths = _write_minimal_fixture_set(tmp_path)
    manifest_doc, _manifest_bytes, manifest_sha256 = dm._load_yaml(paths["manifest_path"])

    diagnosis = dm.build_diagnosis(
        run1_path=paths["run1_path"],
        manifest_path=paths["manifest_path"],
        manifest_doc=manifest_doc,
        manifest_sha256=manifest_sha256,
        registry_path=paths["registry_path"],
        m2c_fixtures_path=paths["m2c_fixtures_path"],
        vocadito_dir=tmp_path / "vocadito",
        generated_utc=None,
    )
    assert diagnosis["1_reason_histogram"]["pairs_total"] == 1
