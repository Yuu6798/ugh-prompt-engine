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
import simplex  # noqa: E402


@pytest.fixture()
def founder():
    return bootstrap.founder_genomes()[0]


def test_write_creates_file_named_by_genome_id(tmp_path: Path, founder) -> None:
    led = ledger_mod.Ledger(tmp_path)
    result = led.write(founder)
    assert result.path == tmp_path / f"{founder.genome_id}.json"
    assert result.path.exists()
    assert result.created is True


def test_write_read_roundtrip(tmp_path: Path, founder) -> None:
    led = ledger_mod.Ledger(tmp_path)
    led.write(founder)
    loaded = led.read(founder.genome_id)
    assert loaded == founder


def test_write_is_idempotent_for_identical_content(tmp_path: Path, founder) -> None:
    led = ledger_mod.Ledger(tmp_path)
    r1 = led.write(founder)
    b1 = r1.path.read_bytes()
    assert r1.created is True
    r2 = led.write(founder)
    b2 = r2.path.read_bytes()
    assert r2.created is False
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
    path = led.write(founder).path
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
    result = led.write(child)
    assert result.path == tmp_path / f"{child.genome_id}.json"
    assert result.created is True
    assert led.exists(child.genome_id)


# --- PR #267 Codex R6 指摘3（P1）: 親検証は self.read() の完全検証で行う ----


def test_write_rejects_genome_with_broken_json_parent(tmp_path: Path, founder) -> None:
    """親 genome_id のファイルが台帳ディレクトリに存在していても、中身が
    壊れた JSON なら `exists()` は真を返すが `read()` は失敗する。publish
    前の親検証が `read()` を経由するようになったことで、この破損親を参照
    する子 genome の write は拒否される。
    """
    led = ledger_mod.Ledger(tmp_path)
    child = operators.drift(founder, rng_seed=1, step=0.02)
    parent_path = tmp_path / f"{founder.genome_id}.json"
    parent_path.parent.mkdir(parents=True, exist_ok=True)
    parent_path.write_text("{ this is not valid json", encoding="utf-8")
    assert led.exists(founder.genome_id)  # ファイルは存在する(壊れているだけ)
    with pytest.raises(ledger_mod.LedgerError):
        led.write(child)
    assert child.genome_id not in led.list_genome_ids()


def test_write_rejects_genome_with_id_mismatched_parent(tmp_path: Path, founder) -> None:
    """親 genome_id のファイルが存在し、中身は有効な genome だが、ファイル名
    が要求する genome_id と内容の自己申告 genome_id が食い違う（別個体の
    JSON を親の位置にコピーした状況を模す）場合、`exists()` は真を返すが
    `read()` はファイル名/内容束縛違反として拒否する。この不整合な親を
    参照する子 genome の write は拒否される。
    """
    led = ledger_mod.Ledger(tmp_path)
    child = operators.drift(founder, rng_seed=1, step=0.02)
    other = bootstrap.founder_genomes()[1]
    assert other.genome_id != founder.genome_id
    parent_path = tmp_path / f"{founder.genome_id}.json"
    parent_path.parent.mkdir(parents=True, exist_ok=True)
    parent_path.write_text(models.genome_to_json(other) + "\n", encoding="utf-8")
    assert led.exists(founder.genome_id)  # ファイルは存在する(ID 不一致なだけ)
    with pytest.raises(ledger_mod.LedgerError):
        led.write(child)
    assert child.genome_id not in led.list_genome_ids()


# --- PR #267 Codex R15 指摘（P2）: JSON 重複キーの黙認 ----------------------


def _inject_duplicate_key(text: str, after_marker: str, key: str, value_json: str) -> str:
    """`text`（`genome_to_json()` が出す `sort_keys=True, indent=2` の pretty
    JSON）の `after_marker` を含む行の直後に `"{key}": {value_json},` を
    差し込み、対象オブジェクトへ重複キーを注入するテスト用ヘルパー。
    """
    lines = text.split("\n")
    idx = next(i for i, line in enumerate(lines) if after_marker in line)
    indent = len(lines[idx + 1]) - len(lines[idx + 1].lstrip(" "))
    injected = f'{" " * indent}"{key}": {value_json},'
    lines.insert(idx + 1, injected)
    return "\n".join(lines)


def test_read_rejects_top_level_duplicate_genome_id_key(tmp_path: Path, founder) -> None:
    """台帳ファイル自体の JSON に `genome_id` が重複していると、標準
    `json.loads` は後勝ちで黙って採用してしまう。`read()` は
    `models.loads_strict()` 経由でこれを fail-closed 拒否する（矛盾する
    genome_id 宣言が `genome_from_dict()` の検証をすり抜けるのを防ぐ）。
    """
    led = ledger_mod.Ledger(tmp_path)
    path = led.path_for(founder.genome_id)
    text = models.genome_to_json(founder)
    dup_text = _inject_duplicate_key(text, '"genome_id"', "genome_id", '"0000000000000000"')
    path.write_text(dup_text + "\n", encoding="utf-8")
    with pytest.raises(models.DuplicateJSONKeyError):
        led.read(founder.genome_id)


def test_read_rejects_nested_duplicate_key_in_operator_params(tmp_path: Path, founder) -> None:
    """`operator_params` のような入れ子オブジェクト内の重複キーも、
    `object_pairs_hook` が全階層で呼ばれるため `read()` は同様に拒否する
    （矛盾する operator_params 宣言が `genome_from_dict()` の検証をすり抜ける
    のを防ぐ）。
    """
    led = ledger_mod.Ledger(tmp_path)
    child = operators.drift(founder, rng_seed=1, step=0.02)
    path = led.path_for(child.genome_id)
    text = models.genome_to_json(child)
    dup_text = _inject_duplicate_key(text, '"operator_params": {', "rng_seed", "999")
    path.write_text(dup_text + "\n", encoding="utf-8")
    with pytest.raises(models.DuplicateJSONKeyError):
        led.read(child.genome_id)


def test_write_rejects_genome_with_duplicate_key_parent(tmp_path: Path, founder) -> None:
    """親 genome_id のファイルが台帳ディレクトリに存在していても、中身に
    重複キー（例: `genome_id` 2回）があれば `read()` は
    `DuplicateJSONKeyError`（`json.JSONDecodeError` のサブクラス）を送出
    する。publish 前の親検証はこれを既存の `except json.JSONDecodeError`
    節で捕捉し `LedgerError` へ正規化して拒否する（矛盾する親宣言を経由
    した publish を防ぐ）。
    """
    led = ledger_mod.Ledger(tmp_path)
    child = operators.drift(founder, rng_seed=1, step=0.02)
    parent_path = tmp_path / f"{founder.genome_id}.json"
    parent_path.parent.mkdir(parents=True, exist_ok=True)
    text = models.genome_to_json(founder)
    dup_text = _inject_duplicate_key(text, '"genome_id"', "genome_id", '"0000000000000000"')
    parent_path.write_text(dup_text + "\n", encoding="utf-8")
    assert led.exists(founder.genome_id)  # ファイルは存在する(重複キーがあるだけ)
    with pytest.raises(ledger_mod.LedgerError):
        led.write(child)
    assert child.genome_id not in led.list_genome_ids()


def test_write_idempotent_no_op_unaffected_by_strict_loader(tmp_path: Path, founder) -> None:
    """重複キー拒否の導入が、正常な（重複キーの無い）台帳ファイルに対する
    冪等 no-op 経路の動作を変えないことを確認する（write の round-trip
    検証・read の parent 検証はいずれも整形済み JSON をそのまま通す）。
    """
    led = ledger_mod.Ledger(tmp_path)
    r1 = led.write(founder)
    assert r1.created is True
    r2 = led.write(founder)
    assert r2.created is False
    assert r1.path.read_bytes() == r2.path.read_bytes()
    assert led.read(founder.genome_id) == founder


# --- PR #267 Codex R7 指摘1（P1）: publish 時の子の再導出検証 ---------------


def test_write_accepts_genuine_operator_outputs_for_all_single_parent_operators(tmp_path: Path, founder) -> None:
    """drift/reseed/edge_walk/novelty_jump の正規の operator 出力はいずれも
    再導出検証を通過して publish が成功する（親1個体の対応表の確認）。
    """
    led = ledger_mod.Ledger(tmp_path)
    led.write(founder)

    drift_child = operators.drift(founder, rng_seed=1, step=0.02)
    led.write(drift_child)
    assert led.exists(drift_child.genome_id)

    reseed_child = operators.reseed(founder, new_seed=42)
    led.write(reseed_child)
    assert led.exists(reseed_child.genome_id)

    edge_walk_child = operators.edge_walk(founder, rng_seed=3, edge=("ritsu", "pjs"), step=0.05)
    led.write(edge_walk_child)
    assert led.exists(edge_walk_child.genome_id)

    novelty_child = operators.novelty_jump(founder, rng_seed=7)
    led.write(novelty_child)
    assert led.exists(novelty_child.genome_id)


def test_write_accepts_genuine_vertex_pull_two_parent_child(tmp_path: Path) -> None:
    """vertex_pull の正規の operator 出力（親2個体）は、`parents[0]`/
    `parents[1]` の順にロードして再導出する経路を通過して publish が
    成功する。
    """
    led = ledger_mod.Ledger(tmp_path)
    ritsu, pjs, *_ = bootstrap.founder_genomes()
    led.write(ritsu)
    led.write(pjs)
    child = operators.vertex_pull(ritsu, pjs, weight=0.4, vertex="user", pull=0.1)
    assert child.parents == (ritsu.genome_id, pjs.genome_id)
    led.write(child)
    assert led.exists(child.genome_id)


def test_write_rejects_forged_child_with_tampered_coords(tmp_path: Path, founder) -> None:
    """実在親を正しく参照しつつ座標だけを無関係な値へすり替えた偽装子は、
    ロード済み親からの決定論的再導出が別の genome_id を生むため拒否される
    （親の実在検証だけでは検出できなかったケース）。
    """
    led = ledger_mod.Ledger(tmp_path)
    led.write(founder)
    genuine = operators.drift(founder, rng_seed=1, step=0.02)
    forged_coords = simplex.normalize({"ritsu": 0.1, "pjs": 0.1, "user": 0.8})
    forged = models.build_genome(
        coords=forged_coords, seed=genuine.seed, lineage=simplex.assign_lineage(forged_coords),
        generation=genuine.generation, parents=genuine.parents, operator=genuine.operator,
        operator_params=genuine.operator_params, anchors_provenance=genuine.anchors_provenance,
    )
    assert forged.genome_id != genuine.genome_id
    with pytest.raises(ledger_mod.LedgerError):
        led.write(forged)
    assert forged.genome_id not in led.list_genome_ids()


def test_write_rejects_forged_child_with_tampered_generation(tmp_path: Path, founder) -> None:
    """実在親 + 正規の coords/operator_params を宣言しつつ generation だけ
    無関係な値（999）へすり替えた偽装子は、親からの再導出 generation
    （`parent.generation + 1`）と食い違うため拒否される。
    """
    led = ledger_mod.Ledger(tmp_path)
    led.write(founder)
    genuine = operators.drift(founder, rng_seed=1, step=0.02)
    forged = models.build_genome(
        coords=genuine.coords, seed=genuine.seed, lineage=genuine.lineage, generation=999,
        parents=genuine.parents, operator=genuine.operator, operator_params=genuine.operator_params,
        anchors_provenance=genuine.anchors_provenance,
    )
    assert forged.genome_id != genuine.genome_id
    with pytest.raises(ledger_mod.LedgerError):
        led.write(forged)
    assert forged.genome_id not in led.list_genome_ids()


def test_write_rejects_forged_child_declaring_a_different_rng_seeds_coords(tmp_path: Path, founder) -> None:
    """`operator_params={"rng_seed": 1, ...}` を宣言しつつ、実際には
    `rng_seed=2` から生じる coords を宣言した偽装子（params 改竄 = 別
    rng_seed の座標を宣言するケース）は、親からの再導出が rng_seed=1 の
    本来の coords を再現するため拒否される。
    """
    led = ledger_mod.Ledger(tmp_path)
    led.write(founder)
    genuine_seed1 = operators.drift(founder, rng_seed=1, step=0.02)
    genuine_seed2 = operators.drift(founder, rng_seed=2, step=0.02)
    assert genuine_seed1.coords != genuine_seed2.coords

    forged = models.build_genome(
        coords=genuine_seed2.coords, seed=genuine_seed2.seed, lineage=genuine_seed2.lineage,
        generation=genuine_seed2.generation, parents=genuine_seed2.parents, operator="drift",
        operator_params={"rng_seed": 1, "step": 0.02}, anchors_provenance=genuine_seed2.anchors_provenance,
    )
    with pytest.raises(ledger_mod.LedgerError):
        led.write(forged)
    assert forged.genome_id not in led.list_genome_ids()


def test_write_rejects_forged_vertex_pull_child_with_tampered_weight(tmp_path: Path) -> None:
    """vertex_pull の親2個体は実在するが、`operator_params.weight` を
    実際に使われた値と異なる値へすり替えた偽装子（coords は元の weight から
    産出された値のまま）は、親からの再導出が別の coords/genome_id を生む
    ため拒否される。
    """
    led = ledger_mod.Ledger(tmp_path)
    ritsu, pjs, *_ = bootstrap.founder_genomes()
    led.write(ritsu)
    led.write(pjs)
    genuine = operators.vertex_pull(ritsu, pjs, weight=0.4, vertex="user", pull=0.1)
    forged = models.build_genome(
        coords=genuine.coords, seed=genuine.seed, lineage=genuine.lineage, generation=genuine.generation,
        parents=genuine.parents, operator="vertex_pull",
        operator_params={"weight": 0.6, "vertex": "user", "pull": 0.1},
        anchors_provenance=genuine.anchors_provenance,
    )
    with pytest.raises(ledger_mod.LedgerError):
        led.write(forged)
    assert forged.genome_id not in led.list_genome_ids()


# --- PR #267 Codex R9 指摘3（P1）: symlink による台帳外脱出 -----------------


def test_read_rejects_symlink_to_external_valid_genome(tmp_path: Path, founder) -> None:
    """`<ledger>/<genome_id>.json` が台帳ディレクトリ外の、有効な genome を
    指す symlink であっても `read()` は追従せず拒否する（外部実体が
    たまたま有効な genome JSON であっても、台帳エントリとしては認めない）。
    """
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    external_path = outside_dir / "external.json"
    external_path.write_text(models.genome_to_json(founder) + "\n", encoding="utf-8")

    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()
    led = ledger_mod.Ledger(ledger_dir)
    link_path = ledger_dir / f"{founder.genome_id}.json"
    link_path.symlink_to(external_path)
    assert link_path.is_symlink()

    with pytest.raises(ledger_mod.LedgerError):
        led.read(founder.genome_id)


def test_write_idempotent_branch_rejects_symlink_to_external_entity(tmp_path: Path, founder) -> None:
    """`write()` の既存ファイル分岐（`os.link` が `FileExistsError` を送出
    する経路）でも、宛先が台帳ディレクトリ外への symlink なら冪等比較の
    前に拒否される（バイト内容がたとえ一致していても、外部実体を台帳
    エントリと同一視しない）。
    """
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    external_path = outside_dir / "external.json"
    payload = models.genome_to_json(founder) + "\n"
    external_path.write_text(payload, encoding="utf-8")

    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()
    led = ledger_mod.Ledger(ledger_dir)
    link_path = ledger_dir / f"{founder.genome_id}.json"
    link_path.symlink_to(external_path)

    with pytest.raises(ledger_mod.LedgerError):
        led.write(founder)
    # 外部実体は書き換えられていない（fail-closed で早期拒否）。
    assert external_path.read_text(encoding="utf-8") == payload


def test_write_and_read_regular_file_behavior_unchanged(tmp_path: Path, founder) -> None:
    """通常ファイル（symlink でない）の write/read 挙動は symlink ガード
    追加後も不変（回帰確認）。"""
    led = ledger_mod.Ledger(tmp_path)
    result1 = led.write(founder)
    path = result1.path
    assert result1.created is True
    assert not path.is_symlink()
    loaded = led.read(founder.genome_id)
    assert loaded == founder
    # 冪等 no-op（既存ファイル分岐）も引き続き成功する。
    result2 = led.write(founder)
    assert result2.path == path
    assert result2.created is False


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
