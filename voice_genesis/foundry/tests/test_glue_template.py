"""test_glue_template.py — VG-F1b glue_template.py の CLI 化 (R11) と衝突拒否 (R13) 検証。

11 本の実験 glue スクリプトのうち代表 1 本（`results_f1b/glue_template.py`）を
smoke + 衝突拒否で検証する。残り 10 本は同型実装（`_reject_output_collision` /
`OUTPUT_NAMES` パターンの目視 + `results_f1_4/f1_4_record_2026-08-15.md` の
監査表参照）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

GLUE_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "results_f1b"
GLUE_TEMPLATE_PATH = GLUE_TEMPLATE_DIR / "glue_template.py"

sys.path.insert(0, str(GLUE_TEMPLATE_DIR))
import glue_template as gt  # noqa: E402


# ---------------------------------------------------------------------------
# --help smoke (R11: 実行可能な argparse CLI であることの確認)
# ---------------------------------------------------------------------------


def test_help_smoke() -> None:
    result = subprocess.run(
        [sys.executable, str(GLUE_TEMPLATE_PATH), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "--donor-wav" in result.stdout
    assert "--control-npz" in result.stdout
    assert "--out" in result.stdout


# ---------------------------------------------------------------------------
# 衝突拒否 (R13・P1: results_f1b/glue_template.py 系スレッド)
# ---------------------------------------------------------------------------


def test_reject_output_collision_donor_wav_equals_output_raises(tmp_path: Path) -> None:
    out_path = tmp_path / "f1b_template_neutral.wav"
    out_path.write_bytes(b"donor bytes")
    with pytest.raises(gt.OutputCollisionError):
        gt._reject_output_collision(out_path, [out_path])


def test_reject_output_collision_control_npz_equals_output_raises(tmp_path: Path) -> None:
    out_path = tmp_path / "f1b_glue_run_log.json"
    out_path.write_bytes(b"{}")
    with pytest.raises(gt.OutputCollisionError):
        gt._reject_output_collision(out_path, [None, out_path])


def test_reject_output_collision_unrelated_paths_do_not_raise(tmp_path: Path) -> None:
    donor = tmp_path / "donor.wav"
    donor.write_bytes(b"donor bytes")
    control = tmp_path / "f1a_control.npz"
    control.write_bytes(b"control bytes")
    out_path = tmp_path / "f1b_template_neutral.wav"
    gt._reject_output_collision(out_path, [donor, control])  # no raise


def test_reject_output_collision_symlinked_donor_resolves_before_compare(tmp_path: Path) -> None:
    real_out = tmp_path / "f1b_template_neutral.wav"
    real_out.write_bytes(b"donor bytes")
    alias = tmp_path / "donor_alias.wav"
    alias.symlink_to(real_out)
    with pytest.raises(gt.OutputCollisionError):
        gt._reject_output_collision(real_out, [alias])


def test_reject_output_collision_missing_input_is_skipped(tmp_path: Path) -> None:
    out_path = tmp_path / "f1b_template_neutral.wav"
    missing = tmp_path / "does_not_exist.wav"
    gt._reject_output_collision(out_path, [missing])  # no raise (input never existed)


def test_main_rejects_donor_wav_aliasing_fixed_output(tmp_path: Path, monkeypatch) -> None:
    """CLI 経由（`--donor-wav <out>/f1b_template_neutral.wav`）でも fail-closed で
    拒否され、ドナー入力が生成音声で上書きされないことを確認する（実害の再現）。
    衝突検査は donor 読込・分析の前に走るため、donor_wav が実際の WAV フォーマット
    である必要はない（存在すれば十分）。
    """
    out_dir = tmp_path
    donor_wav = out_dir / "f1b_template_neutral.wav"
    donor_wav.write_bytes(b"pretend donor wav bytes")
    control_npz = tmp_path / "f1a_control.npz"
    control_npz.write_bytes(b"pretend control npz bytes")
    before_bytes = donor_wav.read_bytes()

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "glue_template.py",
            "--out",
            str(out_dir),
            "--donor-wav",
            str(donor_wav),
            "--control-npz",
            str(control_npz),
        ],
    )
    with pytest.raises(gt.OutputCollisionError):
        gt.main()
    assert donor_wav.read_bytes() == before_bytes  # ドナー入力は無傷のまま


def test_main_rejects_control_npz_aliasing_fixed_output(tmp_path: Path, monkeypatch) -> None:
    out_dir = tmp_path
    donor_wav = tmp_path / "donor.wav"
    donor_wav.write_bytes(b"pretend donor wav bytes")
    control_npz = out_dir / "f1b_glue_run_log.json"
    control_npz.write_bytes(b"pretend control npz bytes")
    before_bytes = control_npz.read_bytes()

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "glue_template.py",
            "--out",
            str(out_dir),
            "--donor-wav",
            str(donor_wav),
            "--control-npz",
            str(control_npz),
        ],
    )
    with pytest.raises(gt.OutputCollisionError):
        gt.main()
    assert control_npz.read_bytes() == before_bytes  # control npz は無傷のまま
