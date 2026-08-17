"""test_ledger.py — VG-E0 台帳ディレクトリ I/O（`ledger.py`）のテスト。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap  # noqa: E402
import ledger as ledger_mod  # noqa: E402
import models  # noqa: E402


@pytest.fixture()
def founder():
    return bootstrap.founder_genomes()[0]


def test_write_creates_file_named_by_genome_id(tmp_path: Path, founder) -> None:
    led = ledger_mod.Ledger(tmp_path)
    path = led.write(founder)
    assert path == tmp_path / f"{founder.genome_id}.json"
    assert path.exists()


def test_write_read_roundtrip(tmp_path: Path, founder) -> None:
    led = ledger_mod.Ledger(tmp_path)
    led.write(founder)
    loaded = led.read(founder.genome_id)
    assert loaded == founder


def test_write_is_idempotent_for_identical_content(tmp_path: Path, founder) -> None:
    led = ledger_mod.Ledger(tmp_path)
    p1 = led.write(founder)
    b1 = p1.read_bytes()
    p2 = led.write(founder)
    b2 = p2.read_bytes()
    assert b1 == b2


def test_write_rejects_conflicting_overwrite(tmp_path: Path, founder) -> None:
    led = ledger_mod.Ledger(tmp_path)
    led.write(founder)
    path = tmp_path / f"{founder.genome_id}.json"
    path.write_text('{"tampered": true}\n', encoding="utf-8")
    with pytest.raises(ledger_mod.LedgerConflictError):
        led.write(founder)


def test_exists_and_list_genome_ids(tmp_path: Path) -> None:
    led = ledger_mod.Ledger(tmp_path)
    genomes = bootstrap.founder_genomes()
    assert led.list_genome_ids() == []
    for g in genomes:
        assert not led.exists(g.genome_id)
        led.write(g)
        assert led.exists(g.genome_id)
    assert led.list_genome_ids() == sorted(g.genome_id for g in genomes)


def test_read_missing_genome_raises(tmp_path: Path) -> None:
    led = ledger_mod.Ledger(tmp_path)
    with pytest.raises(FileNotFoundError):
        led.read("a" * 16)


def test_path_for_rejects_malformed_genome_id(tmp_path: Path) -> None:
    led = ledger_mod.Ledger(tmp_path)
    with pytest.raises(ledger_mod.LedgerError):
        led.path_for("not-a-valid-id")


def test_write_produces_pretty_json_with_trailing_newline(tmp_path: Path, founder) -> None:
    led = ledger_mod.Ledger(tmp_path)
    path = led.write(founder)
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert models.genome_from_json(text) == founder
