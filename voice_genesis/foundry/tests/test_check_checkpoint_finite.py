"""test_check_checkpoint_finite.py — `scripts/check_checkpoint_finite.py` の
単体テスト（Phase D0 run4 残債①）。

torch を必要としない経路（sha256 計算・報告 JSON の形状・checkpoint 不在時の
fail-closed・--pins 未指定時の挙動・--out 親ディレクトリ未存在時の fail-closed）
は常時実行する。torch が要る経路（state_dict の非有限値検査そのもの）は
`pytest.importorskip("torch")` で分離し、torch 不在環境ではスキップする
（このリポジトリの CI 環境には torch が入っていない）。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_checkpoint_finite.py"
sys.path.insert(0, str(SCRIPT_PATH.parent))

import check_checkpoint_finite as ccf  # noqa: E402


def test_sha256_of_file_matches_hashlib_reference(tmp_path: Path) -> None:
    p = tmp_path / "sample.bin"
    p.write_bytes(b"voice genesis checkpoint finite check")
    expected = hashlib.sha256(p.read_bytes()).hexdigest()
    assert ccf._sha256_of_file(p) == expected


def test_check_one_checkpoint_raises_for_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.ckpt"

    class _FakeTorch:
        pass

    with pytest.raises(ccf.CheckpointReadError):
        ccf.check_one_checkpoint(missing, _FakeTorch())


def test_run_fails_closed_when_out_parent_dir_missing(tmp_path: Path) -> None:
    ckpt = tmp_path / "fake.ckpt"
    ckpt.write_bytes(b"not a real checkpoint")
    out_path = tmp_path / "nonexistent_dir" / "report.json"

    exit_code = ccf.run([str(ckpt), "--out", str(out_path)])
    assert exit_code == 1
    assert not out_path.exists()


def test_load_pins_returns_none_when_not_specified() -> None:
    assert ccf._load_pins(None) is None


def test_load_pins_raises_for_missing_pins_file(tmp_path: Path) -> None:
    missing_pins = tmp_path / "no_such_pins.json"
    with pytest.raises(ccf.CheckpointReadError):
        ccf._load_pins(missing_pins)


def test_load_pins_reads_json(tmp_path: Path) -> None:
    pins_path = tmp_path / "pins.json"
    pins_path.write_text(json.dumps({"a": {"b.ckpt": "deadbeef"}}), encoding="utf-8")
    loaded = ccf._load_pins(pins_path)
    assert loaded == {"a": {"b.ckpt": "deadbeef"}}


def test_match_pin_finds_nested_filename_key(tmp_path: Path) -> None:
    ckpt_path = tmp_path / "model_ckpt_steps_5000.ckpt"
    pins = {"d3": {"wav_sha256": {}}, "checkpoints": {"model_ckpt_steps_5000.ckpt": "abc123"}}
    assert ccf._match_pin(ckpt_path, "abc123", pins) is True
    assert ccf._match_pin(ckpt_path, "wrong", pins) is False


def test_match_pin_returns_none_when_not_found(tmp_path: Path) -> None:
    ckpt_path = tmp_path / "unrelated.ckpt"
    pins = {"checkpoints": {"model_ckpt_steps_5000.ckpt": "abc123"}}
    assert ccf._match_pin(ckpt_path, "abc123", pins) is None


def test_extract_state_dict_from_wrapped_dict() -> None:
    loaded = {"state_dict": {"w": "tensor-placeholder"}, "epoch": 3}
    assert ccf._extract_state_dict(loaded) == {"w": "tensor-placeholder"}


def test_extract_state_dict_from_bare_dict() -> None:
    loaded = {"w": "tensor-placeholder"}
    assert ccf._extract_state_dict(loaded) == {"w": "tensor-placeholder"}


def test_extract_state_dict_raises_for_non_dict() -> None:
    with pytest.raises(ccf.CheckpointReadError):
        ccf._extract_state_dict(["not", "a", "dict"])


def test_load_torch_exits_2_when_torch_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """torch の有無に依存しないよう、builtins.__import__ を monkeypatch して
    'torch' import を強制的に ModuleNotFoundError にする（torch が実際に
    インストールされている環境でも本テストは決定論的に通る）。"""
    import builtins

    real_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "torch":
            raise ModuleNotFoundError("No module named 'torch'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    with pytest.raises(SystemExit) as exc_info:
        ccf._load_torch()
    assert exc_info.value.code == 2


# --- torch を要する経路 ----------------------------------------------------
#
# モジュール先頭で `pytest.importorskip("torch")` すると collection 時点で
# モジュール全体（torch 不要な上記テストも含む）がスキップされてしまうため、
# 各テスト内で個別に importorskip し、torch 不要部分は torch の有無に関係なく
# 実行されるようにする。


def test_check_one_checkpoint_detects_finite_state_dict(tmp_path: Path) -> None:
    torch = pytest.importorskip("torch")
    ckpt_path = tmp_path / "finite.ckpt"
    state_dict = {"layer.weight": torch.tensor([1.0, 2.0, 3.0])}
    torch.save({"state_dict": state_dict}, str(ckpt_path))

    result = ccf.check_one_checkpoint(ckpt_path, torch)
    assert result["status"] == "ok"
    assert result["all_finite"] is True
    assert result["total_non_finite"] == 0
    assert result["non_finite_by_tensor"] == {}
    assert result["sha256"] == ccf._sha256_of_file(ckpt_path)


def test_check_one_checkpoint_detects_non_finite_values(tmp_path: Path) -> None:
    torch = pytest.importorskip("torch")
    ckpt_path = tmp_path / "nonfinite.ckpt"
    state_dict = {
        "layer.weight": torch.tensor([1.0, float("nan"), float("inf")]),
        "layer.bias": torch.tensor([0.5]),
    }
    torch.save({"state_dict": state_dict}, str(ckpt_path))

    result = ccf.check_one_checkpoint(ckpt_path, torch)
    assert result["all_finite"] is False
    assert result["total_non_finite"] == 2
    assert result["non_finite_by_tensor"] == {"layer.weight": 2}


def test_run_writes_report_json_and_exit_code_reflects_finiteness(tmp_path: Path) -> None:
    torch = pytest.importorskip("torch")
    ckpt_ok = tmp_path / "ok.ckpt"
    torch.save({"state_dict": {"w": torch.tensor([1.0, 2.0])}}, str(ckpt_ok))
    ckpt_bad = tmp_path / "bad.ckpt"
    torch.save({"state_dict": {"w": torch.tensor([float("nan")])}}, str(ckpt_bad))

    out_path = tmp_path / "report.json"
    exit_code = ccf.run([str(ckpt_ok), str(ckpt_bad), "--out", str(out_path)])

    assert exit_code == 3
    report = json.loads(out_path.read_text(encoding="utf-8"))
    assert report["schema"] == "voicegenesis-checkpoint-finite-report/0.1"
    assert report["all_ok"] is False
    assert len(report["checkpoints"]) == 2
    statuses = {c["path"]: c["all_finite"] for c in report["checkpoints"]}
    assert statuses[str(ckpt_ok)] is True
    assert statuses[str(ckpt_bad)] is False


def test_run_reports_error_entry_for_missing_checkpoint_and_nonzero_exit(tmp_path: Path) -> None:
    pytest.importorskip("torch")  # run() 全体が _load_torch() を経由するため
    missing = tmp_path / "missing.ckpt"
    out_path = tmp_path / "report.json"

    exit_code = ccf.run([str(missing), "--out", str(out_path)])

    assert exit_code == 1
    report = json.loads(out_path.read_text(encoding="utf-8"))
    assert report["checkpoints"][0]["status"] == "error"
    assert "error" in report["checkpoints"][0]
