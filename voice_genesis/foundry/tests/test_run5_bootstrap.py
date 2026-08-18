"""test_run5_bootstrap.py — `scripts/run5_bootstrap.py` ロジック層の検証。

本開発環境には GPU・torch・rclone・runpodctl が無いため、検証対象は
ロジック層のみ（stage 計画・pin 検証・phase config 導出・milestone 検知・
wall-clock 判定・heartbeat 記帳・自動停止コマンド組み立て）。実行系
（render/binarize/train/rclone の subprocess）の初回実測は run 5 本番が
兼ねる（`run5_bootstrap.py` docstring 冒頭の正直会計と対）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import run5_bootstrap as r5b  # noqa: E402


# --- material pins（PENDING fail-closed） -----------------------------------


def _write_pins(tmp_path: Path, *, ffmpeg_sha, vocoder_sha) -> Path:
    pins = {
        "schema": "run5-material-pins/0.1",
        "materials": {
            "ritsu_voicebank_zip": {"url": "https://example/r.zip", "sha256": "a" * 64},
            "ffmpeg_static": {"url": "https://example/f.tar.xz", "sha256": ffmpeg_sha},
            "vocoder_nsf_hifigan_onnx": {"url": "https://example/v.onnx", "sha256": vocoder_sha},
            "diffsinger_repo": {"url": "https://example/ds.git", "commit": "e2307b1"},
        },
    }
    path = tmp_path / "pins.json"
    path.write_text(json.dumps(pins, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_material_pins_rejects_pending_entries(tmp_path: Path) -> None:
    """sha256 が null（クロー報告値の未転記 = DESIGN_S4 §3.2 の起動前必須
    先行タスク未完了）の素材が 1 件でもあれば `PinPendingError` で
    fail-closed し、素材名を列挙する。"""
    path = _write_pins(tmp_path, ffmpeg_sha=None, vocoder_sha=None)
    with pytest.raises(r5b.PinPendingError) as exc_info:
        r5b.load_material_pins(path)
    assert exc_info.value.pending == ["ffmpeg_static", "vocoder_nsf_hifigan_onnx"]


def test_load_material_pins_accepts_fully_pinned_table(tmp_path: Path) -> None:
    path = _write_pins(tmp_path, ffmpeg_sha="b" * 64, vocoder_sha="c" * 64)
    materials = r5b.load_material_pins(path)
    assert materials["ffmpeg_static"]["sha256"] == "b" * 64
    # sha256 キーを持たないエントリ（git commit pin）は PENDING 判定の対象外。
    assert materials["diffsinger_repo"]["commit"] == "e2307b1"


def test_committed_material_pins_file_is_loadable_and_lists_known_pendings() -> None:
    """コミット済みの `run5_material_pins.json` 自体が (1) JSON として読め、
    (2) 現時点の PENDING がまさに ffmpeg_static / vocoder_nsf_hifigan_onnx の
    2 件である（転記が完了したらこのテストの期待値を空に更新する = 転記
    忘れ・転記完了の両方向を検出する）。"""
    with pytest.raises(r5b.PinPendingError) as exc_info:
        r5b.load_material_pins(r5b.MATERIAL_PINS_PATH)
    assert exc_info.value.pending == ["ffmpeg_static", "vocoder_nsf_hifigan_onnx"]


def test_check_required_env_lists_missing_vars() -> None:
    assert r5b.check_required_env({}) == list(r5b.REQUIRED_ENV_VARS)
    complete = {name: "x" for name in r5b.REQUIRED_ENV_VARS}
    assert r5b.check_required_env(complete) == []
    partial = dict(complete)
    partial["RUN5_DRIVE_FOLDER_ID"] = ""  # 空文字は「無い」扱い
    assert r5b.check_required_env(partial) == ["RUN5_DRIVE_FOLDER_ID"]


# --- dataset pin 照合 --------------------------------------------------------


def _make_dataset(tmp_path: Path, name: str, wavs: dict) -> Path:
    d = tmp_path / name
    (d / "wavs").mkdir(parents=True)
    (d / "transcriptions.csv").write_bytes(b"name,ph_seq,ph_dur\nx,a,0.5\n")
    for wav_name, content in wavs.items():
        (d / "wavs" / wav_name).write_bytes(content)
    return d


def test_verify_dataset_against_pins_passes_on_exact_match(tmp_path: Path) -> None:
    d = _make_dataset(tmp_path, "ds", {"a.wav": b"AAA", "b.wav": b"BBB"})
    pin = {
        "transcriptions_csv_sha256": r5b.sha256_file(d / "transcriptions.csv"),
        "wav_sha256": {
            "a.wav": r5b.sha256_file(d / "wavs" / "a.wav"),
            "b.wav": r5b.sha256_file(d / "wavs" / "b.wav"),
        },
    }
    assert r5b.verify_dataset_against_pins(d, pin, "d3") == []


def test_verify_dataset_against_pins_detects_byte_and_set_mismatch(tmp_path: Path) -> None:
    d = _make_dataset(tmp_path, "ds", {"a.wav": b"AAA"})
    pin = {
        "transcriptions_csv_sha256": "0" * 64,
        "wav_sha256": {"a.wav": "1" * 64, "missing.wav": "2" * 64},
    }
    diffs = r5b.verify_dataset_against_pins(d, pin, "d3")
    assert any("transcriptions.csv" in x for x in diffs)
    assert any("file set mismatch" in x for x in diffs)


def test_verify_dataset_against_pins_checks_exclusions_json_when_pinned(tmp_path: Path) -> None:
    d = _make_dataset(tmp_path, "ds", {"a.wav": b"AAA"})
    (d / "exclusions.json").write_bytes(b"{}")
    pin = {
        "transcriptions_csv_sha256": r5b.sha256_file(d / "transcriptions.csv"),
        "wav_sha256": {"a.wav": r5b.sha256_file(d / "wavs" / "a.wav")},
        "exclusions_json_sha256": r5b.sha256_file(d / "exclusions.json"),
    }
    assert r5b.verify_dataset_against_pins(d, pin, "user") == []
    (d / "exclusions.json").write_bytes(b"{tampered}")
    diffs = r5b.verify_dataset_against_pins(d, pin, "user")
    assert any("exclusions.json" in x for x in diffs)


def test_verify_assembly_against_run4_pins_maps_d3synth_to_d3_section() -> None:
    """4 話者 manifest の d3synth（run 5 の第 4 話者）は run 4 pin 表の `d3`
    セクションと、user は `user` セクションと照合される（DESIGN_S4 §1.1:
    データ内容は run 4 と同一 — 変わるのは帰属ラベルのみ）。"""
    wav_map = {"x.wav": "a" * 64}
    manifest = {
        "speakers": {
            "d3synth": {"transcriptions_csv_sha256": "d" * 64, "wav_sha256": dict(wav_map)},
            "user": {
                "transcriptions_csv_sha256": "u" * 64,
                "wav_sha256": {"y.wav": "b" * 64},
                "exclusions_json_sha256": "e" * 64,
            },
        }
    }
    pins = {
        "d3": {"transcriptions_csv_sha256": "d" * 64, "wav_sha256": dict(wav_map)},
        "user": {
            "transcriptions_csv_sha256": "u" * 64,
            "wav_sha256": {"y.wav": "b" * 64},
            "exclusions_json_sha256": "e" * 64,
        },
    }
    assert r5b.verify_assembly_against_run4_pins(manifest, pins) == []

    pins_tampered = json.loads(json.dumps(pins))
    pins_tampered["d3"]["wav_sha256"]["x.wav"] = "f" * 64
    diffs = r5b.verify_assembly_against_run4_pins(manifest, pins_tampered)
    assert diffs and any("d3synth" in x for x in diffs)


# --- phase config 導出 -------------------------------------------------------


_LIVE_CONFIG = {
    "datasets": [
        {"speaker": "ritsu", "spk_id": 0},
        {"speaker": "pjs", "spk_id": 1},
        {"speaker": "user", "spk_id": 2},
        {"speaker": "d3synth", "spk_id": 3},
    ],
    "num_spk": 4,
    "max_updates": 40000,
    "val_check_interval": 5000,
    "num_ckpt_keep": 10,
}


def test_derive_phase_a_config_is_scratch_5k_with_training_fields() -> None:
    cfg = r5b.derive_phase_config(_LIVE_CONFIG, phase="a")
    assert cfg["finetune_enabled"] is False
    assert cfg["max_updates"] == 5000
    assert "finetune_ckpt_path" not in cfg
    assert cfg["pl_trainer_precision"] == "bf16-mixed"
    assert cfg["optimizer_args"] == {"lr": 0.0002}
    assert cfg["clip_grad_norm"] == 1.0
    # live config 由来のフィールドは不変
    assert cfg["datasets"] == _LIVE_CONFIG["datasets"]
    assert cfg["num_spk"] == 4
    assert cfg["val_check_interval"] == 5000


def test_derive_phase_b_config_refinetunes_from_phase_a_5k() -> None:
    cfg = r5b.derive_phase_config(
        _LIVE_CONFIG, phase="b", finetune_ckpt_path="/ckpt/model_ckpt_steps_5000.ckpt"
    )
    assert cfg["finetune_enabled"] is True
    assert cfg["finetune_ckpt_path"] == "/ckpt/model_ckpt_steps_5000.ckpt"
    assert cfg["max_updates"] == 40000
    assert cfg["pl_trainer_precision"] == "bf16-mixed"


def test_derive_phase_b_without_ckpt_path_is_rejected() -> None:
    with pytest.raises(ValueError, match="finetune_ckpt_path"):
        r5b.derive_phase_config(_LIVE_CONFIG, phase="b")


def test_derive_phase_config_does_not_mutate_live_config() -> None:
    before = json.loads(json.dumps(_LIVE_CONFIG))
    r5b.derive_phase_config(_LIVE_CONFIG, phase="a")
    assert _LIVE_CONFIG == before


def test_derive_phase_config_rejects_unknown_phase() -> None:
    with pytest.raises(ValueError, match="phase"):
        r5b.derive_phase_config(_LIVE_CONFIG, phase="c")


# --- milestone 検知 / wall-clock ---------------------------------------------


def test_parse_ckpt_step_parses_diffsinger_naming() -> None:
    assert r5b.parse_ckpt_step("model_ckpt_steps_5000.ckpt") == 5000
    assert r5b.parse_ckpt_step("model_ckpt_steps_40000.ckpt") == 40000
    assert r5b.parse_ckpt_step("config.yaml") is None
    assert r5b.parse_ckpt_step("model_ckpt_steps_5000.ckpt.tmp") is None


def test_find_milestone_ckpts_only_returns_milestone_steps(tmp_path: Path) -> None:
    for step in (1000, 5000, 15000, 20000):
        (tmp_path / f"model_ckpt_steps_{step}.ckpt").write_bytes(b"x")
    (tmp_path / "config.yaml").write_bytes(b"y")
    found = r5b.find_milestone_ckpts(tmp_path)
    assert sorted(found) == [5000, 20000]


def test_find_milestone_ckpts_on_missing_dir_is_empty(tmp_path: Path) -> None:
    assert r5b.find_milestone_ckpts(tmp_path / "nope") == {}


def test_new_milestones_returns_only_unseen_sorted(tmp_path: Path) -> None:
    current = {5000: tmp_path, 10000: tmp_path, 20000: tmp_path}
    assert r5b.new_milestones([5000], current) == [10000, 20000]
    assert r5b.new_milestones([5000, 10000, 20000], current) == []


def test_remaining_seconds_wall_clock_budget() -> None:
    assert r5b.remaining_seconds(0.0, 3600.0, limit=7200) == 3600.0
    assert r5b.remaining_seconds(0.0, 90000.0) <= 0  # 24h 上限超過
    assert r5b.remaining_seconds(100.0, 100.0) == r5b.WALL_CLOCK_LIMIT_SECONDS


# --- stage 計画 / heartbeat / self-stop --------------------------------------


def test_stage_plan_matches_design_s4_ordering() -> None:
    """DESIGN_S4 §3.1 の段階（ゲート → 素材照合 → 再生成 → pin 照合 →
    学習 → 退避 → 自動停止）の順序が保存されていること。"""
    plan = r5b.build_stage_plan()
    assert plan == (
        "preflight", "gates", "materials", "datasets", "assemble",
        "binarize", "train_phase_a", "train_phase_b", "salvage", "self_stop",
    )
    assert plan.index("gates") < plan.index("materials") < plan.index("datasets")
    assert plan.index("assemble") < plan.index("binarize") < plan.index("train_phase_a")
    assert plan.index("train_phase_b") < plan.index("salvage") < plan.index("self_stop")


def test_heartbeat_marks_stage_and_pushes_marker(tmp_path: Path) -> None:
    pushed = []
    hb = r5b.Heartbeat(tmp_path / "hb", pushed.append)
    marker = hb.mark("gates", "ok", detail="4/4 passed")
    assert marker.exists()
    data = json.loads(marker.read_text(encoding="utf-8"))
    assert data["stage"] == "gates"
    assert data["status"] == "ok"
    assert data["detail"] == "4/4 passed"
    assert data["utc"].endswith("Z")
    assert pushed == [marker]


def test_self_stop_command_stops_not_removes() -> None:
    """DESIGN_S4 §3.3 裁定 (c): 停止のみで Pod ディスクは保険として残置する
    — remove ではなく stop であること。"""
    argv = r5b.self_stop_command("abc123")
    assert argv == ["runpodctl", "stop", "pod", "abc123"]
    assert "remove" not in argv


def test_training_fields_match_runbook_section4_values() -> None:
    """runbook §4 の 4 項目のうち自動付与分（bf16-mixed / lr 0.0002 /
    clip 1.0）が凍結値のまま保たれていること（finetune 系 2 キーは phase
    導出側が付与する）。"""
    assert r5b.TRAINING_FIELDS == {
        "pl_trainer_precision": "bf16-mixed",
        "optimizer_args": {"lr": 0.0002},
        "clip_grad_norm": 1.0,
    }
    assert r5b.PHASE_A_MAX_UPDATES == 5000
    assert r5b.PHASE_B_MAX_UPDATES == 40000
    assert r5b.WALL_CLOCK_LIMIT_SECONDS == 24 * 3600


def test_plan_cli_prints_stages_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert r5b.main(["--plan"]) == 0
    out = capsys.readouterr().out
    for stage in r5b.build_stage_plan():
        assert stage in out


def test_main_without_env_fails_closed_before_any_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """必須環境変数が無ければ素材取得どころか pin 読みにも進まず exit 1。"""
    for name in r5b.REQUIRED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    assert r5b.main(["--work-dir", str(tmp_path / "w")]) == 1
    err = capsys.readouterr().err
    assert "missing required env var" in err


def test_main_with_env_but_pending_pins_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """env が揃っていても、コミット済み pin 表に PENDING が残る現状では
    preflight で fail-closed する（クロー報告値の転記が起動前必須である
    ことの実行時表現）。"""
    for name in r5b.REQUIRED_ENV_VARS:
        monkeypatch.setenv(name, "dummy")
    assert r5b.main(["--work-dir", str(tmp_path / "w")]) == 1
    err = capsys.readouterr().err
    assert "PENDING" in err
