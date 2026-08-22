"""test_check_checkpoint_finite.py — `scripts/check_checkpoint_finite.py` の
単体テスト（Phase D0 run4 残債①。2026-08-22 セルフレビュー修正1-4 +
Codex bot レビュー #8 で拡張: ゼロ検査の偽成功防止 / pin fail-closed / 例外
ラップ漏れ / 実 pin 形式対応 / --out 衝突拒否 / atomic write）。

torch を必要としない経路（sha256 計算・報告 JSON の形状・checkpoint 不在時の
fail-closed・--pins 未指定時の挙動・--out 親ディレクトリ未存在時の fail-closed・
--out 衝突拒否・atomic write・torch.load フォールバック例外のラップ）は
常時実行する。torch が要る経路（state_dict の非有限値検査そのもの）は
`pytest.importorskip("torch")` で分離し、torch 不在環境ではスキップする
（このリポジトリの CI 環境には torch が入っていない）。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_checkpoint_finite.py"
sys.path.insert(0, str(SCRIPT_PATH.parent))

import check_checkpoint_finite as ccf  # noqa: E402


class _FakeTensor:
    """torch.Tensor らしさ（`.numel()` / `.dtype`）だけを持つ最小限のフェイク。
    `_extract_state_dict` の tensor-like 判定テストに使う（実 tensor 演算は
    行わないので torch 不要）。"""

    def numel(self) -> int:
        return 1

    @property
    def dtype(self) -> str:
        return "fake-float"


class _RaisingTorchModule:
    """`torch.load` の失敗経路（TypeError → フォールバック経路も失敗）を
    シミュレートする最小限のフェイク torch module。実 tensor 演算をしない
    テストなので torch 不要で使える。"""

    __version__ = "fake"

    def load(self, *args: Any, **kwargs: Any) -> Any:
        if "weights_only" in kwargs:
            raise TypeError("weights_only unsupported (fake old torch)")
        raise RuntimeError("fallback load failed (fake)")


def test_sha256_of_file_matches_hashlib_reference(tmp_path: Path) -> None:
    p = tmp_path / "sample.bin"
    p.write_bytes(b"voice genesis checkpoint finite check")
    expected = hashlib.sha256(p.read_bytes()).hexdigest()
    assert ccf._sha256_of_file(p) == expected


def test_sha256_of_file_wraps_os_error_for_directory(tmp_path: Path) -> None:
    """修正3: `_sha256_of_file` の OSError 系（IsADirectoryError 含む）を
    fail-closed で CheckpointReadError にラップする。"""
    directory = tmp_path / "a_directory"
    directory.mkdir()
    with pytest.raises(ccf.CheckpointReadError) as exc_info:
        ccf._sha256_of_file(directory)
    assert exc_info.value.reason == "checkpoint_read_os_error"


def test_check_one_checkpoint_raises_for_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.ckpt"

    class _FakeTorch:
        pass

    with pytest.raises(ccf.CheckpointReadError):
        ccf.check_one_checkpoint(missing, _FakeTorch())


def test_check_one_checkpoint_wraps_fallback_torch_load_exception(tmp_path: Path) -> None:
    """修正3: 旧 torch の weights_only 非対応フォールバック経路自体が失敗した
    場合も CheckpointReadError にラップされる（従来は素通しで run() の
    except CheckpointReadError に捕捉されずクラッシュし得た）。"""
    ckpt = tmp_path / "fake.ckpt"
    ckpt.write_bytes(b"not a real checkpoint")
    with pytest.raises(ccf.CheckpointReadError) as exc_info:
        ccf.check_one_checkpoint(ckpt, _RaisingTorchModule())
    assert exc_info.value.reason == "torch_load_fallback_failed"


def test_run_fails_closed_when_out_parent_dir_missing(tmp_path: Path) -> None:
    ckpt = tmp_path / "fake.ckpt"
    ckpt.write_bytes(b"not a real checkpoint")
    out_path = tmp_path / "nonexistent_dir" / "report.json"

    exit_code = ccf.run([str(ckpt), "--out", str(out_path)])
    assert exit_code == 1
    assert not out_path.exists()


def test_run_rejects_out_path_colliding_with_checkpoint_input(tmp_path: Path) -> None:
    """Codex bot レビュー #8: --out が入力 checkpoint と衝突する場合、読み込み
    前に即エラー終了し何も書き込まない（torch 不要 — 衝突判定は torch load の
    前に行う）。"""
    ckpt = tmp_path / "fake.ckpt"
    ckpt.write_bytes(b"not a real checkpoint")

    exit_code = ccf.run([str(ckpt), "--out", str(ckpt)])

    assert exit_code != 0
    assert ckpt.read_bytes() == b"not a real checkpoint"


def test_run_rejects_out_path_colliding_with_pins_input(tmp_path: Path) -> None:
    ckpt = tmp_path / "fake.ckpt"
    ckpt.write_bytes(b"not a real checkpoint")
    pins_path = tmp_path / "pins.json"
    pins_path.write_text("{}", encoding="utf-8")

    exit_code = ccf.run([str(ckpt), "--pins", str(pins_path), "--out", str(pins_path)])

    assert exit_code != 0
    assert pins_path.read_text(encoding="utf-8") == "{}"


def test_atomic_write_text_writes_full_content_and_leaves_no_tmp_file(tmp_path: Path) -> None:
    out_path = tmp_path / "report.json"
    ccf._atomic_write_text(out_path, "hello atomic")
    assert out_path.read_text(encoding="utf-8") == "hello atomic"
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "report.json"]
    assert leftovers == []


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


def test_match_pin_finds_file_sha256_subfield_format(tmp_path: Path) -> None:
    """修正4: `results_s3/run4_anchor_provenance.json` の実構造
    （`{"5K": {"file": "model_ckpt_steps_5000.ckpt", "sha256": "..."}}`）を
    照合対象に追加する。"""
    ckpt_path = tmp_path / "model_ckpt_steps_5000.ckpt"
    pins = {
        "checkpoints": {
            "5K": {
                "file": "model_ckpt_steps_5000.ckpt",
                "sha256": "abc123",
                "verification": "sha256sum -c で OK（実体照合済み）",
            },
            "10K": {"file": "model_ckpt_steps_10000.ckpt", "sha256": "def456"},
        }
    }
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
    """修正1: 'state_dict' キーが無い dict は、tensor-like value を1つ以上
    含む場合のみ dict 全体を state_dict 候補として受理する。"""
    loaded = {"w": _FakeTensor()}
    assert ccf._extract_state_dict(loaded) is loaded


def test_extract_state_dict_finds_model_wrapper_key() -> None:
    """修正1: 'state_dict' キーが無くても既知 wrapper キー ('model') を探索
    する。"""
    inner = {"w": _FakeTensor()}
    loaded = {"model": inner, "epoch": 3}
    assert ccf._extract_state_dict(loaded) is inner


def test_extract_state_dict_raises_when_no_tensor_like_values() -> None:
    """修正1: 'state_dict' キーが無く、既知 wrapper キーも見つからず、
    dict 全体候補にも tensor-like value が1つも無ければ fail-closed で拒否
    する。"""
    loaded = {"epoch": 3, "note": "no tensors here"}
    with pytest.raises(ccf.CheckpointReadError) as exc_info:
        ccf._extract_state_dict(loaded)
    assert exc_info.value.reason == "no_state_dict_candidate"


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


def test_check_one_checkpoint_raises_when_zero_tensors_checked(tmp_path: Path) -> None:
    """修正1: 検査した float tensor が0個の checkpoint は all_finite: true を
    騙らず fail-closed で拒否する（ゼロ検査の偽成功防止）。"""
    torch = pytest.importorskip("torch")
    ckpt_path = tmp_path / "int_only.ckpt"
    state_dict = {"step_counter": torch.tensor([5], dtype=torch.int64)}
    torch.save({"state_dict": state_dict}, str(ckpt_path))

    with pytest.raises(ccf.CheckpointReadError) as exc_info:
        ccf.check_one_checkpoint(ckpt_path, torch)
    assert exc_info.value.reason == "no_tensors_inspected"


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


def test_run_reports_error_entry_for_zero_tensors_checked(tmp_path: Path) -> None:
    """修正1: run() 経由でもゼロ検査の checkpoint は error エントリになり、
    exit が非ゼロになる。"""
    torch = pytest.importorskip("torch")
    ckpt = tmp_path / "int_only.ckpt"
    torch.save({"state_dict": {"step_counter": torch.tensor([5], dtype=torch.int64)}}, str(ckpt))
    out_path = tmp_path / "report.json"

    exit_code = ccf.run([str(ckpt), "--out", str(out_path)])

    assert exit_code == 1
    report = json.loads(out_path.read_text(encoding="utf-8"))
    entry = report["checkpoints"][0]
    assert entry["status"] == "error"
    assert entry["reason"] == "no_tensors_inspected"
    assert report["all_ok"] is False


def test_run_marks_pin_mismatch_as_error_and_nonzero_exit(tmp_path: Path) -> None:
    """修正2: pin_sha256_match == False は fail-closed で error 扱いになる。"""
    torch = pytest.importorskip("torch")
    ckpt = tmp_path / "model_ckpt_steps_5000.ckpt"
    torch.save({"state_dict": {"w": torch.tensor([1.0, 2.0])}}, str(ckpt))
    pins_path = tmp_path / "pins.json"
    pins_path.write_text(
        json.dumps({"checkpoints": {"model_ckpt_steps_5000.ckpt": "0" * 8}}), encoding="utf-8"
    )
    out_path = tmp_path / "report.json"

    exit_code = ccf.run([str(ckpt), "--pins", str(pins_path), "--out", str(out_path)])

    assert exit_code == 1
    report = json.loads(out_path.read_text(encoding="utf-8"))
    entry = report["checkpoints"][0]
    assert entry["status"] == "error"
    assert entry["reason"] == "pin_sha256_mismatch"
    assert entry["pin_sha256_match"] is False
    assert report["all_ok"] is False
    assert report["pin_verification"] == "requested"


def test_run_marks_pin_not_found_as_error_and_nonzero_exit(tmp_path: Path) -> None:
    """修正2: --pins 指定時に pin が見つからない checkpoint も fail-closed。"""
    torch = pytest.importorskip("torch")
    ckpt = tmp_path / "unrelated.ckpt"
    torch.save({"state_dict": {"w": torch.tensor([1.0, 2.0])}}, str(ckpt))
    pins_path = tmp_path / "pins.json"
    pins_path.write_text(json.dumps({"checkpoints": {"other.ckpt": "abc"}}), encoding="utf-8")
    out_path = tmp_path / "report.json"

    exit_code = ccf.run([str(ckpt), "--pins", str(pins_path), "--out", str(out_path)])

    assert exit_code == 1
    report = json.loads(out_path.read_text(encoding="utf-8"))
    entry = report["checkpoints"][0]
    assert entry["status"] == "error"
    assert entry["reason"] == "pin_not_found"
    assert entry["pin_sha256_match"] is None


def test_run_pin_match_ok_keeps_status_ok(tmp_path: Path) -> None:
    """修正2/4: pin が一致すれば status は ok のまま。実 pin 形式
    ({"file":..., "sha256":...}) も通ることを確認する。"""
    torch = pytest.importorskip("torch")
    ckpt = tmp_path / "model_ckpt_steps_5000.ckpt"
    torch.save({"state_dict": {"w": torch.tensor([1.0, 2.0])}}, str(ckpt))
    actual_sha256 = ccf._sha256_of_file(ckpt)
    pins_path = tmp_path / "pins.json"
    pins_path.write_text(
        json.dumps({"checkpoints": {"5K": {"file": ckpt.name, "sha256": actual_sha256}}}),
        encoding="utf-8",
    )
    out_path = tmp_path / "report.json"

    exit_code = ccf.run([str(ckpt), "--pins", str(pins_path), "--out", str(out_path)])

    assert exit_code == 0
    report = json.loads(out_path.read_text(encoding="utf-8"))
    entry = report["checkpoints"][0]
    assert entry["status"] == "ok"
    assert entry["pin_sha256_match"] is True
    assert report["all_ok"] is True
    assert report["pin_verification"] == "requested"


def test_run_without_pins_reports_not_requested(tmp_path: Path) -> None:
    """修正2: --pins 未指定時のみ照合をスキップでき、report にその旨を
    明記する。"""
    torch = pytest.importorskip("torch")
    ckpt = tmp_path / "ok.ckpt"
    torch.save({"state_dict": {"w": torch.tensor([1.0, 2.0])}}, str(ckpt))
    out_path = tmp_path / "report.json"

    exit_code = ccf.run([str(ckpt), "--out", str(out_path)])

    assert exit_code == 0
    report = json.loads(out_path.read_text(encoding="utf-8"))
    assert report["pin_verification"] == "not_requested"
    assert "pin_sha256_match" not in report["checkpoints"][0]
