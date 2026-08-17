"""test_bootstrap.py — VG-E0 創始個体ブートストラップ（`bootstrap.py`）の
テスト。

DESIGN_VG_E0.md §7 AC 最終項目「創始3個体（L-R/L-P/L-U 各頂点）+ 中央1個体
（L-C）を生成するブートストラップが動き、台帳に4ファイルが決定論的に生成
される」を直接検証する。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap  # noqa: E402
import ledger as ledger_mod  # noqa: E402


def test_founder_genomes_count_and_lineages() -> None:
    genomes = bootstrap.founder_genomes()
    assert len(genomes) == 4
    lineages = sorted(g.lineage for g in genomes)
    assert lineages == ["L-C", "L-P", "L-R", "L-U"]


def test_founder_genomes_generation_zero_and_founder_operator() -> None:
    for g in bootstrap.founder_genomes():
        assert g.generation == 0
        assert g.operator == "founder"
        assert g.parents == ()
        assert g.operator_params == {}


def test_founder_vertices_reproduce_exact_anchors() -> None:
    genomes = {g.lineage: g for g in bootstrap.founder_genomes()}
    assert (genomes["L-R"].coords.ritsu, genomes["L-R"].coords.pjs, genomes["L-R"].coords.user) == (1.0, 0.0, 0.0)
    assert (genomes["L-P"].coords.ritsu, genomes["L-P"].coords.pjs, genomes["L-P"].coords.user) == (0.0, 1.0, 0.0)
    assert (genomes["L-U"].coords.ritsu, genomes["L-U"].coords.pjs, genomes["L-U"].coords.user) == (0.0, 0.0, 1.0)


def test_founder_center_sums_to_one() -> None:
    genomes = {g.lineage: g for g in bootstrap.founder_genomes()}
    center = genomes["L-C"]
    total = center.coords.ritsu + center.coords.pjs + center.coords.user
    assert abs(total - 1.0) < 1e-9


def test_founder_genomes_are_deterministic() -> None:
    a = bootstrap.founder_genomes()
    b = bootstrap.founder_genomes()
    assert [g.genome_id for g in a] == [g.genome_id for g in b]


def test_run_bootstrap_writes_four_files(tmp_path: Path) -> None:
    paths = bootstrap.run_bootstrap(tmp_path)
    assert len(paths) == 4
    for p in paths:
        assert p.exists()
        assert p.parent == tmp_path


def test_run_bootstrap_written_genomes_validate_via_ledger(tmp_path: Path) -> None:
    bootstrap.run_bootstrap(tmp_path)
    led = ledger_mod.Ledger(tmp_path)
    ids = led.list_genome_ids()
    assert len(ids) == 4
    for gid in ids:
        genome = led.read(gid)  # genome_id 再計算一致検証込み
        assert genome.operator == "founder"


def test_run_bootstrap_twice_is_byte_identical(tmp_path: Path) -> None:
    """AC: 「ブートストラップ2回実行のバイト同一」。"""
    dir_a = tmp_path / "run_a"
    dir_b = tmp_path / "run_b"
    paths_a = sorted(bootstrap.run_bootstrap(dir_a))
    paths_b = sorted(bootstrap.run_bootstrap(dir_b))
    names_a = [p.name for p in paths_a]
    names_b = [p.name for p in paths_b]
    assert names_a == names_b
    hashes_a = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths_a]
    hashes_b = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths_b]
    assert hashes_a == hashes_b


def test_run_bootstrap_rerun_same_dir_is_idempotent(tmp_path: Path) -> None:
    first = sorted(p.name for p in bootstrap.run_bootstrap(tmp_path))
    second = sorted(p.name for p in bootstrap.run_bootstrap(tmp_path))
    assert first == second
