"""Regression coverage for the terminal rev 0.6 formal CLI boundary."""
from __future__ import annotations

import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_RUN_DIR = _THIS_DIR.parent
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

import birth_probe_executor as bp  # noqa: E402


def test_terminal_rev06_formal_cli_aborts_before_any_external_asset_access(
    tmp_path: Path,
    capsys,
) -> None:
    missing = tmp_path / "does-not-exist"
    result = bp.main(
        [
            "--acoustic-dir",
            str(missing / "acoustic"),
            "--canon-model-dir",
            str(missing / "canon"),
            "--vocoder-dir",
            str(missing / "vocoder"),
            "--pjs-corpus-root",
            str(missing / "pjs"),
            "--out",
            str(tmp_path / "formal-evidence"),
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "formal RUN9 rev 0.6 CLI is permanently disabled" in captured.err
    assert "2026-08-28 IMPLEMENTATION_FAILED" in captured.err
    assert not (tmp_path / "formal-evidence").exists()
