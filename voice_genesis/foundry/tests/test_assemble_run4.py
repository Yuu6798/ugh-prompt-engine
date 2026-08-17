"""test_assemble_run4.py — S3 Phase D `s1_dataprep/assemble_run4.py` の検証。

`S3_RUN4_RUNBOOK.md` §3 / `DESIGN_S3_backfill.md` §2.4 の受け入れ条件
（正常 3 話者 / 名前衝突 fail / ゲート違反 fail / 決定論 2 回一致）を
高速・合成フィクスチャで検証する（`test_convert_d3.py` と同じ流儀:
実レンダ・実 voicebank には依存しない）。
"""
from __future__ import annotations

import csv
import json
import sys
import wave
from pathlib import Path
from typing import Sequence

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "s1_dataprep"))
import assemble_run4  # noqa: E402
import build_dataset  # noqa: E402

_RITSU_HEADER = ["name", "ph_seq", "ph_dur"]
_FULL_HEADER = ["name", "ph_seq", "ph_dur", "ph_num", "note_seq", "note_dur"]


def _write_wav(path: Path, duration_sec: float, *, rate: int = 24000) -> None:
    """`rate` Hz mono PCM_16 の無音 wav を `duration_sec` 秒ぶん書く。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    n_frames = round(duration_sec * rate)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(b"\x00\x00" * n_frames)


def _write_csv(path: Path, header: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(list(header))
        for row in rows:
            writer.writerow(list(row))


def _make_ritsu_raw_dir(root: Path) -> Path:
    """D2 (ritsu VCV) の最小フィクスチャ: 3 列ヘッダ (note_dur 等を持たない)。"""
    out = root / "ritsu_raw"
    _write_csv(
        out / "transcriptions.csv",
        _RITSU_HEADER,
        [
            ["ritsu_A3_001", "a i", "0.5 0.5"],
            ["ritsu_A3_002", "u", "0.8"],
        ],
    )
    _write_wav(out / "wavs" / "ritsu_A3_001.wav", 1.0)
    _write_wav(out / "wavs" / "ritsu_A3_002.wav", 0.8)
    return out


def _make_d3_raw_dir(root: Path, *, extra_row: Sequence[str] | None = None) -> Path:
    """D3 の最小フィクスチャ: 6 列ヘッダ (ph_num/note_seq/note_dur を持つ)。"""
    out = root / "d3_raw"
    rows = [
        ["sakura_seed11", "a k a", "0.3 0.3 0.4", "1 2", "60 62", "0.3 0.7"],
        ["umi_seed11", "i", "0.6", "1", "64", "0.6"],
    ]
    if extra_row is not None:
        rows.append(list(extra_row))
    _write_csv(out / "transcriptions.csv", _FULL_HEADER, rows)
    _write_wav(out / "wavs" / "sakura_seed11.wav", 1.0)
    _write_wav(out / "wavs" / "umi_seed11.wav", 0.6)
    if extra_row is not None:
        _write_wav(out / "wavs" / f"{extra_row[0]}.wav", 1.0)
    return out


def _make_pjs_raw_dir(root: Path) -> Path:
    out = root / "pjs_raw"
    _write_csv(
        out / "transcriptions.csv",
        _FULL_HEADER,
        [["pjs_song1_001", "s o", "0.4 0.4", "1 1", "60 62", "0.4 0.4"]],
    )
    _write_wav(out / "wavs" / "pjs_song1_001.wav", 0.8)
    return out


def _make_user_raw_dir(root: Path, *, ph_seq: str = "a", ph_dur: str = "0.5") -> Path:
    out = root / "user_raw"
    _write_csv(
        out / "transcriptions.csv",
        _FULL_HEADER,
        [["UC-001", ph_seq, ph_dur, "1", "60", "0.5"]],
    )
    _write_wav(out / "wavs" / "UC-001.wav", 0.5)
    (out / "exclusions.json").write_text(
        json.dumps({"excluded": []}, ensure_ascii=False), encoding="utf-8"
    )
    return out


# ---------------------------------------------------------------------------
# 1. 正常 3 話者
# ---------------------------------------------------------------------------


def test_normal_three_speaker_assembly_publishes_manifest(tmp_path: Path) -> None:
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    d3_raw = _make_d3_raw_dir(tmp_path)
    pjs_raw = _make_pjs_raw_dir(tmp_path)
    user_raw = _make_user_raw_dir(tmp_path)
    out_dir = tmp_path / "run4_raw"

    manifest = assemble_run4.assemble(
        ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir, pjs_is_fixture=True
    )

    # --- ritsu(=D2+D3) 合流結果 ---
    ritsu_rows = build_dataset.read_transcriptions(out_dir / "ritsu" / "transcriptions.csv")
    assert {r["name"] for r in ritsu_rows} == {
        "ritsu_A3_001", "ritsu_A3_002", "sakura_seed11", "umi_seed11",
    }
    # D2 由来行は note_dur 列を持たない (欠落 = None) ことを確認する。
    d2_row = next(r for r in ritsu_rows if r["name"] == "ritsu_A3_001")
    assert d2_row.get("note_dur") is None
    assert d2_row.get("ph_num") is None
    d3_row = next(r for r in ritsu_rows if r["name"] == "sakura_seed11")
    assert d3_row["note_dur"] == "0.3 0.7"
    for stem in ("ritsu_A3_001", "ritsu_A3_002", "sakura_seed11", "umi_seed11"):
        assert (out_dir / "ritsu" / "wavs" / f"{stem}.wav").exists()

    # --- pjs / user はそのまま複製 ---
    pjs_rows = build_dataset.read_transcriptions(out_dir / "pjs" / "transcriptions.csv")
    assert {r["name"] for r in pjs_rows} == {"pjs_song1_001"}
    assert (out_dir / "pjs" / "wavs" / "pjs_song1_001.wav").exists()

    user_rows = build_dataset.read_transcriptions(out_dir / "user" / "transcriptions.csv")
    assert {r["name"] for r in user_rows} == {"UC-001"}
    assert (out_dir / "user" / "exclusions.json").exists()

    # --- 3 話者ゲートは 0 issue ---
    for name, spk_dir, rows in (
        ("ritsu", out_dir / "ritsu", ritsu_rows),
        ("pjs", out_dir / "pjs", pjs_rows),
        ("user", out_dir / "user", user_rows),
    ):
        problems = build_dataset.validate_speaker(name, spk_dir, rows)
        problems += build_dataset.check_ph_dur_duration(name, spk_dir / "wavs", rows)
        problems += build_dataset.check_note_dur_consistency(name, rows)
        assert problems == [], f"{name}: {problems}"

    # --- 辞書統合 ---
    dict_text = (out_dir / "dict.txt").read_text(encoding="utf-8")
    symbols = {line.split("\t")[0] for line in dict_text.splitlines() if line}
    assert symbols == {"a", "i", "u", "k", "s", "o"}

    # --- manifest ---
    assert manifest["spk_id"] == {"ritsu": 0, "pjs": 1, "user": 2}
    assert manifest["speakers"]["ritsu"]["row_count"] == 4
    assert manifest["speakers"]["ritsu"]["wav_count"] == 4
    assert manifest["speakers"]["ritsu"]["components"] == ["d2", "d3"]
    assert manifest["speakers"]["pjs"]["row_count"] == 1
    assert manifest["speakers"]["pjs"]["is_fixture"] is True
    assert manifest["speakers"]["user"]["row_count"] == 1
    assert manifest["speakers"]["user"]["has_exclusions_json"] is True
    assert manifest["collision_check"]["ritsu_d3_name_collisions"] == []
    assert manifest["collision_check"]["ritsu_d3_wav_filename_collisions"] == []
    assert manifest["gate"]["problems"] == []
    assert manifest["dict"]["symbol_count"] == 6
    assert "notes" in manifest  # pjs_is_fixture=True の明記

    manifest_on_disk = json.loads((out_dir / "assembly_manifest.json").read_text(encoding="utf-8"))
    assert manifest_on_disk == manifest


def _make_empty_pjs_raw_dir(root: Path) -> Path:
    """pjs の空データセット（ヘッダのみ・データ行 0 件）フィクスチャ。"""
    out = root / "pjs_raw_empty"
    _write_csv(out / "transcriptions.csv", _FULL_HEADER, [])
    (out / "wavs").mkdir(parents=True, exist_ok=True)
    return out


# ---------------------------------------------------------------------------
# 1.5 P1 修正 (review #265): 空話者コーパス（transcriptions.csv 行 0 件）は
# fail-closed（`validate_speaker`等が空リストに no-op で通過する false-success
# 経路を明示的に閉じる）
# ---------------------------------------------------------------------------


def test_empty_speaker_corpus_fails_closed(tmp_path: Path) -> None:
    """pjs の raw dir がヘッダのみ・データ行 0 件の場合、
    `validate_speaker`/`check_ph_dur_duration`/`check_note_dur_consistency`
    はいずれも空リストに対して no-op（`problems=[]`）で通過してしまうため、
    ゲート回付の前に明示的に検出して fail-closed する（既存出力の false
    -success な置換を防止）。"""
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    d3_raw = _make_d3_raw_dir(tmp_path)
    pjs_raw = _make_empty_pjs_raw_dir(tmp_path)
    user_raw = _make_user_raw_dir(tmp_path)
    out_dir = tmp_path / "run4_raw"

    with pytest.raises(assemble_run4.GateValidationError) as exc_info:
        assemble_run4.assemble(ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir)
    assert any("zero row" in p and "pjs" in p for p in exc_info.value.problems)
    assert not out_dir.exists()


def test_empty_speaker_corpus_does_not_clobber_existing_out_dir(tmp_path: Path) -> None:
    """既存の `out_dir`（前回の正常な組み立て結果）がある状態で、空の pjs
    コーパスから再度 `assemble()` を呼んでも既存の出力は破壊されない。"""
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    d3_raw = _make_d3_raw_dir(tmp_path)
    pjs_raw = _make_pjs_raw_dir(tmp_path)
    user_raw = _make_user_raw_dir(tmp_path)
    out_dir = tmp_path / "run4_raw"
    assemble_run4.assemble(ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir, pjs_is_fixture=True)
    existing_manifest_bytes = (out_dir / "assembly_manifest.json").read_bytes()

    empty_pjs_raw = _make_empty_pjs_raw_dir(tmp_path)
    with pytest.raises(assemble_run4.GateValidationError):
        assemble_run4.assemble(ritsu_raw, d3_raw, empty_pjs_raw, user_raw, out_dir)

    assert (out_dir / "assembly_manifest.json").read_bytes() == existing_manifest_bytes


# ---------------------------------------------------------------------------
# 1.6 P1 修正 (review #265): 衝突検査を assemble() 自身が行う（CLI 経由でなくても
# fail-closed）
# ---------------------------------------------------------------------------


def test_assemble_rejects_out_dir_colliding_with_raw_dir_without_cli(tmp_path: Path) -> None:
    """CLI `main()` を経由しない直接呼び出しでも `--out-dir` が 4 つの raw dir
    のいずれかと衝突していれば `assemble()` 自身が fail-closed で拒否する
    （review #265 P1: 旧実装は CLI のみが preflight していたため、非 CLI
    経路はこの検査を素通りしていた）。"""
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    d3_raw = _make_d3_raw_dir(tmp_path)
    pjs_raw = _make_pjs_raw_dir(tmp_path)
    user_raw = _make_user_raw_dir(tmp_path)

    with pytest.raises(assemble_run4.convert_d3.OutputCollisionError):
        assemble_run4.assemble(ritsu_raw, d3_raw, pjs_raw, user_raw, user_raw)


def test_assemble_rejects_out_dir_inside_raw_dir_without_cli(tmp_path: Path) -> None:
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    d3_raw = _make_d3_raw_dir(tmp_path)
    pjs_raw = _make_pjs_raw_dir(tmp_path)
    user_raw = _make_user_raw_dir(tmp_path)
    out_dir = d3_raw / "nested_out"

    with pytest.raises(assemble_run4.convert_d3.OutputCollisionError):
        assemble_run4.assemble(ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir)


# ---------------------------------------------------------------------------
# 2. 名前衝突 fail-closed
# ---------------------------------------------------------------------------


def test_name_collision_between_ritsu_and_d3_fails_closed(tmp_path: Path) -> None:
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    # D3 側に ritsu と同名の行を混入させる（row/wav 双方衝突）。
    d3_raw = _make_d3_raw_dir(
        tmp_path, extra_row=["ritsu_A3_001", "a", "1.0", "1", "60", "1.0"]
    )
    pjs_raw = _make_pjs_raw_dir(tmp_path)
    user_raw = _make_user_raw_dir(tmp_path)
    out_dir = tmp_path / "run4_raw"

    with pytest.raises(assemble_run4.NameCollisionError, match="ritsu_A3_001"):
        assemble_run4.assemble(ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir)
    assert not out_dir.exists()


# ---------------------------------------------------------------------------
# 3. ゲート違反 fail-closed
# ---------------------------------------------------------------------------


def test_gate_violation_fails_closed(tmp_path: Path) -> None:
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    d3_raw = _make_d3_raw_dir(tmp_path)
    pjs_raw = _make_pjs_raw_dir(tmp_path)
    # user 行を ph_seq/ph_dur 長さ不一致で壊す（validate_speaker が検出する）。
    user_raw = _make_user_raw_dir(tmp_path, ph_seq="a b", ph_dur="0.5")
    out_dir = tmp_path / "run4_raw"

    with pytest.raises(assemble_run4.GateValidationError) as exc_info:
        assemble_run4.assemble(ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir)
    assert exc_info.value.problems  # 非空
    assert any("length mismatch" in p for p in exc_info.value.problems)
    assert not out_dir.exists()


# ---------------------------------------------------------------------------
# 4. 決定論: 同一入力 -> 同一出力バイト列
# ---------------------------------------------------------------------------


def test_assembly_is_deterministic_across_two_runs(tmp_path: Path) -> None:
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    d3_raw = _make_d3_raw_dir(tmp_path)
    pjs_raw = _make_pjs_raw_dir(tmp_path)
    user_raw = _make_user_raw_dir(tmp_path)
    out_dir_1 = tmp_path / "run4_raw_1"
    out_dir_2 = tmp_path / "run4_raw_2"

    assemble_run4.assemble(ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir_1, pjs_is_fixture=True)
    assemble_run4.assemble(ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir_2, pjs_is_fixture=True)

    rel_paths = [
        Path("ritsu/transcriptions.csv"),
        Path("ritsu/wavs/ritsu_A3_001.wav"),
        Path("ritsu/wavs/ritsu_A3_002.wav"),
        Path("ritsu/wavs/sakura_seed11.wav"),
        Path("ritsu/wavs/umi_seed11.wav"),
        Path("pjs/transcriptions.csv"),
        Path("pjs/wavs/pjs_song1_001.wav"),
        Path("user/transcriptions.csv"),
        Path("user/wavs/UC-001.wav"),
        Path("user/exclusions.json"),
        Path("dict.txt"),
        Path("assembly_manifest.json"),
    ]
    for rel in rel_paths:
        b1 = (out_dir_1 / rel).read_bytes()
        b2 = (out_dir_2 / rel).read_bytes()
        assert b1 == b2, f"mismatch: {rel}"
