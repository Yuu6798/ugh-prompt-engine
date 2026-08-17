"""test_ledger.py — VG-E0 台帳ディレクトリ I/O（`ledger.py`）のテスト。"""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Dict, Tuple

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap  # noqa: E402
import ledger as ledger_mod  # noqa: E402
import models  # noqa: E402
import operators  # noqa: E402


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


# --- Codex 指摘1（P1）: 並行初回書込みの排他性 ------------------------------


def test_write_is_exclusive_under_concurrent_first_writers(tmp_path: Path) -> None:
    """同一 genome_id（notes のみ異なる — genome_id 計算対象外のフィールド）
    の2並行「初回」書込みは、片方が必ず `LedgerConflictError` で負ける
    （tmp→os.replace の後勝ちでは黙って上書きされ得た）。os.link による
    排他 create のため、スレッドの実際のスケジューリングに関わらず結果は
    決定的（勝者1・敗者1）。
    """
    base_kwargs = dict(
        coords=models.Coords(1.0, 0.0, 0.0), seed=0, lineage="L-R", generation=0,
        parents=(), operator="founder", operator_params={},
    )
    genome_a = models.build_genome(notes="writer-a", **base_kwargs)
    genome_b = models.build_genome(notes="writer-b", **base_kwargs)
    assert genome_a.genome_id == genome_b.genome_id  # 同一 identity, 異なる payload

    led = ledger_mod.Ledger(tmp_path)
    results: Dict[str, Tuple[str, object]] = {}
    barrier = threading.Barrier(2)

    def _writer(name: str, genome: models.VoiceGenome) -> None:
        barrier.wait()
        try:
            results[name] = ("ok", led.write(genome))
        except ledger_mod.LedgerConflictError as exc:
            results[name] = ("conflict", exc)

    t_a = threading.Thread(target=_writer, args=("a", genome_a))
    t_b = threading.Thread(target=_writer, args=("b", genome_b))
    t_a.start()
    t_b.start()
    t_a.join()
    t_b.join()

    outcomes = [results["a"][0], results["b"][0]]
    assert outcomes.count("ok") == 1
    assert outcomes.count("conflict") == 1

    path = tmp_path / f"{genome_a.genome_id}.json"
    final_bytes = path.read_bytes()
    payload_a = (models.genome_to_json(genome_a) + "\n").encode("utf-8")
    payload_b = (models.genome_to_json(genome_b) + "\n").encode("utf-8")
    # 最終ファイルは片方の payload と完全一致し、混在（torn write）していない。
    assert final_bytes in (payload_a, payload_b)


# --- Codex 指摘2（P1）: publish 直前の round-trip 検証 ----------------------


def test_write_rejects_genome_broken_after_construction(tmp_path: Path) -> None:
    """呼び出し側が構築後に `operator_params` の dict を変異させると、
    宣言された `genome_id` と実際の内容が食い違う。publish 直前の
    round-trip 検証（`genome_from_dict`）がこれを検出し、何も書き込まずに
    `LedgerError` で拒否する。
    """
    center = bootstrap.founder_genomes()[3]
    child = operators.drift(center, rng_seed=1, step=0.02)
    original_step = child.operator_params["step"]
    # frozen dataclass だが operator_params の dict 自体はミュータブル —
    # in-place 変異は build_genome() の検証を経由しない。
    child.operator_params["step"] = round(original_step + 0.01, 6)
    assert child.operator_params["step"] != original_step

    led = ledger_mod.Ledger(tmp_path)
    with pytest.raises(ledger_mod.LedgerError):
        led.write(child)
    assert led.list_genome_ids() == []


# --- Codex 指摘5（P2）: read の要求 ID 束縛 ----------------------------------


# --- PR #267 Codex R5 指摘1（P1）: 台帳の孤児親参照 -------------------------


def test_write_rejects_genome_with_unpublished_parent(tmp_path: Path, founder) -> None:
    """親 genome がまだ台帳に write されていない状態で子 genome を write
    しようとすると、系譜グラフに宙吊りエッジが生まれる前に `LedgerError`
    で拒否される（親を write せずに参照するのは未 publish / typo のいずれ
    でも区別できないため fail-closed）。
    """
    led = ledger_mod.Ledger(tmp_path)
    child = operators.drift(founder, rng_seed=1, step=0.02)
    assert not led.exists(founder.genome_id)
    with pytest.raises(ledger_mod.LedgerError):
        led.write(child)
    assert led.list_genome_ids() == []


def test_write_accepts_genome_after_parent_published(tmp_path: Path, founder) -> None:
    """親を先に write すれば、同一の子 genome の write は成功する。"""
    led = ledger_mod.Ledger(tmp_path)
    child = operators.drift(founder, rng_seed=1, step=0.02)
    led.write(founder)
    path = led.write(child)
    assert path == tmp_path / f"{child.genome_id}.json"
    assert led.exists(child.genome_id)


def test_read_rejects_renamed_file(tmp_path: Path, founder) -> None:
    """ファイルがリネームされ、ファイル名（要求 genome_id）と内容の自己申告
    genome_id が食い違う場合、`read()` は拒否する（自己申告 ID の再計算一致
    検証だけではファイル名との束縛までは検証できないため）。
    """
    led = ledger_mod.Ledger(tmp_path)
    led.write(founder)
    other = bootstrap.founder_genomes()[1]
    assert other.genome_id != founder.genome_id
    wrong_path = tmp_path / f"{other.genome_id}.json"
    (tmp_path / f"{founder.genome_id}.json").rename(wrong_path)
    with pytest.raises(ledger_mod.LedgerError):
        led.read(other.genome_id)
