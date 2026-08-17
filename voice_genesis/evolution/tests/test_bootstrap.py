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

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap  # noqa: E402
import ledger as ledger_mod  # noqa: E402
import models  # noqa: E402


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


# --- PR #267 Codex R11 指摘2（P2）: ブートストラップの部分 publish 防止 --------


def test_run_bootstrap_pre_check_conflict_writes_nothing(tmp_path: Path) -> None:
    """事前衝突（3件目の位置に別内容ファイル）で何も書かれないことを検証
    する。従来は逐次 `Ledger.write()` が3件目で `LedgerConflictError` を
    送出する前に1-2件目が published のまま残っていた。"""
    genomes = bootstrap.founder_genomes()
    conflicting_genome_id = genomes[2].genome_id
    conflicting_path = tmp_path / f"{conflicting_genome_id}.json"
    conflicting_path.write_text('{"not": "a founder genome"}\n', encoding="utf-8")

    with pytest.raises(ledger_mod.LedgerConflictError):
        bootstrap.run_bootstrap(tmp_path)

    # 衝突ファイル以外は一切書かれておらず、衝突ファイル自体も不変。
    remaining = sorted(p.name for p in tmp_path.glob("*.json"))
    assert remaining == [f"{conflicting_genome_id}.json"]
    assert conflicting_path.read_text(encoding="utf-8") == '{"not": "a founder genome"}\n'


def test_run_bootstrap_rolls_back_on_mid_write_failure(tmp_path: Path, monkeypatch) -> None:
    """失敗注入: 3/4件目の write を monkeypatch で失敗させ、1-2件目が消えて
    台帳が実行前状態（空）へ戻ることを検証する（並行書込み等の残余ケースに
    対する rollback）。"""
    original_write = ledger_mod.Ledger.write
    call_count = {"n": 0}

    def failing_write(self: ledger_mod.Ledger, genome: object) -> ledger_mod.WriteResult:
        call_count["n"] += 1
        if call_count["n"] == 3:
            raise RuntimeError("injected failure on 3rd write")
        return original_write(self, genome)

    monkeypatch.setattr(ledger_mod.Ledger, "write", failing_write)

    with pytest.raises(RuntimeError, match="injected failure"):
        bootstrap.run_bootstrap(tmp_path)

    remaining = list(tmp_path.glob("*.json")) if tmp_path.exists() else []
    assert remaining == []
    assert call_count["n"] == 3


def test_run_bootstrap_idempotent_success_with_all_founders_pre_written(tmp_path: Path) -> None:
    """事前検証の「全既存 + 全一致 = 冪等成功として何も書かず終了」経路:
    先に正常に書き終えた台帳に対する再実行が、新規ファイルを一切作らず既存
    パスをそのまま返すことを検証する。"""
    first_paths = sorted(bootstrap.run_bootstrap(tmp_path))
    mtimes_before = {p: p.stat().st_mtime_ns for p in first_paths}
    second_paths = sorted(bootstrap.run_bootstrap(tmp_path))
    assert first_paths == second_paths
    for p in second_paths:
        assert p.stat().st_mtime_ns == mtimes_before[p]


# --- PR #267 Codex R12 指摘1（P2）: rollback は BaseException を捕捉する ----


def test_run_bootstrap_rolls_back_on_keyboard_interrupt(tmp_path: Path, monkeypatch) -> None:
    """失敗注入: 3件目の write で `KeyboardInterrupt`（`BaseException` だが
    `Exception` のサブクラスではない）を送出させる。従来の
    `except Exception` ではこれを捕捉できず rollback されないまま部分状態
    が残り、かつ例外自体は伝播していたため一見正常に見えて実は台帳が
    汚染されていた。`except BaseException` へ改めたことで、
    `KeyboardInterrupt`/`SystemExit` でも rollback → 再送出が起きることを
    検証する。
    """
    original_write = ledger_mod.Ledger.write
    call_count = {"n": 0}

    def failing_write(self: ledger_mod.Ledger, genome: object) -> ledger_mod.WriteResult:
        call_count["n"] += 1
        if call_count["n"] == 3:
            raise KeyboardInterrupt()
        return original_write(self, genome)

    monkeypatch.setattr(ledger_mod.Ledger, "write", failing_write)

    with pytest.raises(KeyboardInterrupt):
        bootstrap.run_bootstrap(tmp_path)

    remaining = list(tmp_path.glob("*.json")) if tmp_path.exists() else []
    assert remaining == []
    assert call_count["n"] == 3


# --- PR #267 Codex R12 指摘2（P1）: rollback は WriteResult.created のみ信頼 -


def test_run_bootstrap_rollback_does_not_delete_concurrently_published_file(
    tmp_path: Path, monkeypatch
) -> None:
    """pre-check 通過後、この実行の write() 呼び出しの合間に別プロセスが
    genomes[1] を同一バイトで先置きする（並行 publish を模す）。この実行の
    `Ledger.write(genomes[1])` は `os.link` の `FileExistsError` を経て
    バイト同一の冪等成功（`created=False`）を返すため、後続失敗時の
    rollback はこのファイルを削除してはならない——「pre-check 時に
    存在しなかったはず」という推定だけに基づく旧ロジックなら、genomes[1]
    も pre-check 時点では未存在だったため誤って rollback 対象に含めて
    unlink していた。
    """
    genomes = bootstrap.founder_genomes()
    concurrent_path = tmp_path / f"{genomes[1].genome_id}.json"
    concurrent_payload = (models.genome_to_json(genomes[1]) + "\n").encode("utf-8")

    original_write = ledger_mod.Ledger.write
    call_count = {"n": 0}

    def racing_write(self: ledger_mod.Ledger, genome: object) -> ledger_mod.WriteResult:
        call_count["n"] += 1
        if call_count["n"] == 1:
            # 別プロセスが genomes[1] を同一バイトで先置きする（pre-check
            # の後、この実行が genomes[1] を write する前）。
            concurrent_path.write_bytes(concurrent_payload)
        if call_count["n"] == 4:
            raise RuntimeError("injected failure on 4th write")
        return original_write(self, genome)

    monkeypatch.setattr(ledger_mod.Ledger, "write", racing_write)

    with pytest.raises(RuntimeError, match="injected failure"):
        bootstrap.run_bootstrap(tmp_path)

    # 別プロセスが先置きした genomes[1] は rollback で削除されていない。
    assert concurrent_path.exists()
    assert concurrent_path.read_bytes() == concurrent_payload
    # この実行が実際に新規作成した genomes[0]/genomes[2] は rollback で
    # 削除されている。
    for idx in (0, 2):
        assert not (tmp_path / f"{genomes[idx].genome_id}.json").exists()
    # genomes[3] は4件目の失敗時点で書かれていない。
    assert not (tmp_path / f"{genomes[3].genome_id}.json").exists()


# --- PR #267 Codex R12 指摘3（P2）: pre-check の symlink ガード迂回 ---------


def test_run_bootstrap_rejects_all_founders_as_external_symlinks(tmp_path: Path) -> None:
    """4件すべてを台帳ディレクトリ外への symlink（内容はバイト同一）に
    差し替えた状態で bootstrap を実行する。従来の pre-check は生
    `read_bytes()` で比較していたため、「全既存 + 全一致」の早期 return
    経路（`Ledger.write()` を一度も呼ばない）が symlink であることを見逃し、
    冪等成功として素通ししていた。pre-check が `Ledger` の
    symlink/containment ガードを経由するようになったことで、fail-closed
    で拒否されることを検証する。
    """
    genomes = bootstrap.founder_genomes()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()

    for genome in genomes:
        payload = (models.genome_to_json(genome) + "\n").encode("utf-8")
        external_path = outside_dir / f"{genome.genome_id}.json"
        external_path.write_bytes(payload)
        link_path = ledger_dir / f"{genome.genome_id}.json"
        link_path.symlink_to(external_path)

    with pytest.raises(ledger_mod.LedgerError):
        bootstrap.run_bootstrap(ledger_dir)

    # symlink 自体は変更されない（write() は一度も呼ばれていない）。
    for genome in genomes:
        link_path = ledger_dir / f"{genome.genome_id}.json"
        assert link_path.is_symlink()
