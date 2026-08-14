"""test_registry.py — P4 (VG-010) の受け入れテスト: JSONL append、lineage 遡上、content hash。"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
from dataclasses import replace

import pytest

import genome as g
import registry as r
import sampler

FIXED_TIME = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def registry_path(tmp_path):
    return tmp_path / "genomes.jsonl"


def test_append_writes_one_jsonl_line(registry_path):
    reg = r.GenomeRegistry(registry_path)
    gen = sampler.sample(1)
    entry = reg.append(gen, op="sample", seed=1, now=FIXED_TIME)

    text = registry_path.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) == 1
    assert entry.registry_schema == "genome-registry/0.1"
    assert entry.created_at == FIXED_TIME.isoformat()
    assert len(entry.genome_id) == 12


def test_genome_content_hash_is_stable_and_deterministic():
    gen = sampler.sample(5)
    h1 = r.genome_content_hash(gen)
    h2 = r.genome_content_hash(gen)
    assert h1 == h2
    assert len(h1) == 12


def test_genome_content_hash_differs_for_different_genomes():
    a = sampler.sample(1)
    b = sampler.sample(2)
    assert r.genome_content_hash(a) != r.genome_content_hash(b)


def test_append_rejects_invalid_op(registry_path):
    reg = r.GenomeRegistry(registry_path)
    gen = sampler.sample(1)
    with pytest.raises(r.RegistryError):
        reg.append(gen, op="teleport", seed=1)


def test_load_all_round_trips_entries(registry_path):
    reg = r.GenomeRegistry(registry_path)
    gen = sampler.sample(1)
    entry = reg.append(gen, op="sample", seed=1, now=FIXED_TIME)

    loaded = reg.load_all()
    assert len(loaded) == 1
    assert loaded[0].genome_id == entry.genome_id
    restored_genome = reg.get_genome(entry.genome_id)
    assert restored_genome == gen


def test_get_returns_none_for_missing_id(registry_path):
    reg = r.GenomeRegistry(registry_path)
    assert reg.get("deadbeefdead") is None


def test_eval_defaults_to_none_placeholders(registry_path):
    """underspec_log_p1.md [UNDERSPEC-P1-8]: eval サブスコアは P1 では None プレースホルダ。"""
    reg = r.GenomeRegistry(registry_path)
    gen = sampler.sample(1)
    entry = reg.append(gen, op="sample", seed=1, now=FIXED_TIME)
    assert entry.eval == {"plausibility": None, "grip_ref": None, "novelty": None}


def test_audit_fields_default_to_not_applicable(registry_path):
    reg = r.GenomeRegistry(registry_path)
    gen = sampler.sample(1)
    entry = reg.append(gen, op="sample", seed=1, now=FIXED_TIME)
    assert entry.audit == {
        "reference_set_hash": None,
        "linkability_report_id": None,
        "residual_gate_passed": None,
    }


def test_lineage_walks_parent_chain(registry_path):
    reg = r.GenomeRegistry(registry_path)

    g1 = sampler.sample(1)
    e1 = reg.append(g1, op="sample", seed=1, now=FIXED_TIME)

    g2 = sampler.mutate(g1, seed=2, scale=0.05)
    e2 = reg.append(g2, op="mutate", seed=2, parents=[e1.genome_id], now=FIXED_TIME)

    g3 = sampler.mutate(g2, seed=3, scale=0.05)
    e3 = reg.append(g3, op="mutate", seed=3, parents=[e2.genome_id], now=FIXED_TIME)

    lineage = reg.lineage(e3.genome_id)
    assert [e.genome_id for e in lineage] == [e1.genome_id, e2.genome_id, e3.genome_id]
    assert lineage[0].op == "sample"
    assert lineage[0].parents == []


def test_lineage_unknown_genome_id_raises(registry_path):
    reg = r.GenomeRegistry(registry_path)
    with pytest.raises(r.RegistryError):
        reg.lineage("0000deadbeef")


def test_lineage_crossover_follows_primary_parent(registry_path):
    """underspec_log_p1.md [UNDERSPEC-P1-8b]: crossover は parents[0]（先頭親）だけを辿る。"""
    reg = r.GenomeRegistry(registry_path)
    a = sampler.sample(1)
    ea = reg.append(a, op="sample", seed=1, now=FIXED_TIME)
    b = sampler.sample(2)
    eb = reg.append(b, op="sample", seed=2, now=FIXED_TIME)

    child = sampler.crossover(a, b, seed=9)
    echild = reg.append(child, op="crossover", seed=9, parents=[ea.genome_id, eb.genome_id], now=FIXED_TIME)

    lineage = reg.lineage(echild.genome_id)
    assert [e.genome_id for e in lineage] == [ea.genome_id, echild.genome_id]


def test_append_creates_parent_directories(tmp_path):
    nested_path = tmp_path / "nested" / "dir" / "genomes.jsonl"
    reg = r.GenomeRegistry(nested_path)
    gen = sampler.sample(1)
    reg.append(gen, op="sample", seed=1, now=FIXED_TIME)
    assert nested_path.exists()


# --- PR#261 レビュー C3: 重複 genome_id の拒否 -------------------------------


def test_append_rejects_duplicate_genome_id(registry_path):
    """append-only ストアは同一 genome_id の再登場を来歴の書き換えとみなし拒否する
    （PR#261 レビュー C3: get()/lineage() が末尾優先で解決するため、同一内容の
    genome を異なる op/seed/parents で再 append すると既存来歴が黙って上書きされて
    見えてしまう）。"""
    reg = r.GenomeRegistry(registry_path)
    gen = sampler.sample(1)
    entry = reg.append(gen, op="sample", seed=1, now=FIXED_TIME)

    with pytest.raises(r.DuplicateGenomeError):
        # 同一内容 genome（= 同一 genome_id）を異なる op/seed で再 append。
        # parents は R24 の op="mutate" 不変条件（1 個必須）を満たすためだけの
        # 値で、genome_id 重複検出（duplicate チェック）は parent 数検証より
        # 後に走るため、この呼び出しは重複チェックまで到達して意図どおり
        # DuplicateGenomeError になる。
        reg.append(gen, op="mutate", seed=99, parents=[entry.genome_id], now=FIXED_TIME)

    # 拒否された 2 回目は書き込まれておらず、レジストリは 1 行のまま。
    assert len(reg.load_all()) == 1


def test_duplicate_genome_error_is_a_registry_error(registry_path):
    """呼び出し側が汎用 RegistryError で一括捕捉しても壊れないよう、
    DuplicateGenomeError は RegistryError のサブタイプである。"""
    assert issubclass(r.DuplicateGenomeError, r.RegistryError)


# --- PR#261 レビュー R5: load_all() 自体での重複 genome_id 検出 -------------


def test_load_all_rejects_duplicate_genome_id_even_when_not_written_via_append(registry_path):
    """append() 自身の重複拒否（C3）だけでは、append() を経由せず直接編集・
    結合された JSONL ファイルに紛れ込んだ重複 genome_id を防げない。
    load_all() 自身にも同じ規律を敷く（R5）。"""
    reg = r.GenomeRegistry(registry_path)
    gen = sampler.sample(1)
    reg.append(gen, op="sample", seed=1, now=FIXED_TIME)

    # append() の重複ガードを迂回し、同一行をファイルへ直接複製する
    # （過去バージョンからの引き継ぎ・手動結合を模す）。
    line = registry_path.read_text(encoding="utf-8").rstrip("\n")
    registry_path.write_text(line + "\n" + line + "\n", encoding="utf-8")

    with pytest.raises(r.DuplicateGenomeError):
        reg.load_all()


# --- PR#261 レビュー C5/C6 の同型穴掃討: registry_schema の検証 --------------


def test_entry_from_dict_rejects_unknown_registry_schema(registry_path):
    reg = r.GenomeRegistry(registry_path)
    gen = sampler.sample(1)
    reg.append(gen, op="sample", seed=1, now=FIXED_TIME)

    text = registry_path.read_text(encoding="utf-8")
    tampered = text.replace('"genome-registry/0.1"', '"genome-registry/9.9"')
    registry_path.write_text(tampered, encoding="utf-8")

    with pytest.raises(r.RegistryError):
        reg.load_all()


def test_entry_from_dict_rejects_missing_registry_schema_key(registry_path):
    """registry_schema キー自体が欠落している場合も、現行版へのデフォルト補完
    をせず明示的に拒否する（PR#261 レビュー R12 の同型掃討: genome.py の
    schema_version と同じ理由）。"""
    reg = r.GenomeRegistry(registry_path)
    gen = sampler.sample(1)
    reg.append(gen, op="sample", seed=1, now=FIXED_TIME)

    data = json.loads(registry_path.read_text(encoding="utf-8").strip())
    del data["registry_schema"]
    assert "registry_schema" not in data  # 前提確認
    registry_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(r.RegistryError):
        reg.load_all()


# --- PR#261 レビュー R14: エントリ version と genome.schema_version の整合 --


def test_entry_from_dict_rejects_version_mismatch_with_genome_schema_version(registry_path):
    """エントリ直下の version が埋め込み genome.schema_version と食い違う
    改ざん/破損行を拒否する。"""
    reg = r.GenomeRegistry(registry_path)
    gen = sampler.sample(1)
    reg.append(gen, op="sample", seed=1, now=FIXED_TIME)

    data = json.loads(registry_path.read_text(encoding="utf-8").strip())
    assert data["version"] == data["genome"]["schema_version"]  # 前提確認
    data["version"] = "voice-genome/9.9"  # genome.schema_version とは食い違う値
    registry_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(r.RegistryError):
        reg.load_all()


# --- PR#261 レビュー R22: エントリ読み込み時の op 検証 -----------------------


def test_entry_from_dict_rejects_op_outside_valid_ops(registry_path):
    """`append()` は op を `VALID_OPS` で検証するが、旧 `_entry_from_dict()`
    （読み込み時）は宣言値をそのまま信頼していた。手編集・インポートされた
    行が `op="delete"` のような未定義値を持っていても、読み込み経路を
    経由すれば検証されずに通ってしまう非対称性を拒否で塞ぐ。"""
    reg = r.GenomeRegistry(registry_path)
    gen = sampler.sample(1)
    reg.append(gen, op="sample", seed=1, now=FIXED_TIME)

    data = json.loads(registry_path.read_text(encoding="utf-8").strip())
    assert data["op"] in r.VALID_OPS  # 前提確認
    data["op"] = "delete"  # VALID_OPS = ("sample", "mutate", "crossover") の域外
    registry_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(r.RegistryError):
        reg.load_all()


def test_entry_from_dict_accepts_each_valid_op(registry_path):
    """非退行確認: VALID_OPS の全値（sample/mutate/crossover）は従来どおり
    読み込める。"""
    reg = r.GenomeRegistry(registry_path)
    g1 = sampler.sample(1)
    e1 = reg.append(g1, op="sample", seed=1, now=FIXED_TIME)
    g2 = sampler.mutate(g1, seed=2, scale=0.1)
    e2 = reg.append(g2, op="mutate", seed=2, parents=[e1.genome_id], now=FIXED_TIME)
    g3 = sampler.sample(3, name="parent3")
    e3 = reg.append(g3, op="sample", seed=3, now=FIXED_TIME)
    g4 = sampler.crossover(g2, g3, seed=4)
    reg.append(g4, op="crossover", seed=4, parents=[e2.genome_id, e3.genome_id], now=FIXED_TIME)

    loaded = reg.load_all()
    assert {e.op for e in loaded} == {"sample", "mutate", "crossover"}


# --- PR#261 レビュー R24: op 別の parent 数不変条件 --------------------------


def test_append_rejects_mutate_with_zero_parents(registry_path):
    """mutate は単一親からの摂動である以上、parents=[] は矛盾する。"""
    reg = r.GenomeRegistry(registry_path)
    g1 = sampler.sample(1)
    with pytest.raises(r.RegistryError):
        reg.append(g1, op="mutate", seed=1, parents=[], now=FIXED_TIME)


def test_append_rejects_crossover_with_one_parent(registry_path):
    """crossover は 2 親のフィールド単位継承である以上、parents が 1 個は矛盾する。"""
    reg = r.GenomeRegistry(registry_path)
    g1 = sampler.sample(1)
    e1 = reg.append(g1, op="sample", seed=1, now=FIXED_TIME)
    g2 = sampler.mutate(g1, seed=2, scale=0.1)
    with pytest.raises(r.RegistryError):
        reg.append(g2, op="crossover", seed=2, parents=[e1.genome_id], now=FIXED_TIME)


def test_append_rejects_sample_with_a_parent(registry_path):
    """sample は探索の起点（親なし）である以上、parents を持つのは矛盾する。"""
    reg = r.GenomeRegistry(registry_path)
    g1 = sampler.sample(1)
    e1 = reg.append(g1, op="sample", seed=1, now=FIXED_TIME)
    g2 = sampler.sample(2, name="not-really-a-child")
    with pytest.raises(r.RegistryError):
        reg.append(g2, op="sample", seed=2, parents=[e1.genome_id], now=FIXED_TIME)


def test_entry_from_dict_rejects_mutate_with_zero_parents(registry_path):
    """`append()` の parent 数チェックを迂回する手編集・インポート行（読み込み
    経路）でも同じ不変条件を拒否する（R22 の op 検証と同型の非対称性防止）。"""
    reg = r.GenomeRegistry(registry_path)
    g1 = sampler.sample(1)
    e1 = reg.append(g1, op="sample", seed=1, now=FIXED_TIME)
    g2 = sampler.mutate(g1, seed=2, scale=0.1)
    reg.append(g2, op="mutate", seed=2, parents=[e1.genome_id], now=FIXED_TIME)

    lines = registry_path.read_text(encoding="utf-8").strip().split("\n")
    data = json.loads(lines[-1])
    assert data["op"] == "mutate" and data["parents"] == [e1.genome_id]  # 前提確認
    data["parents"] = []  # mutate なのに親を消す改ざん
    lines[-1] = json.dumps(data)
    registry_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(r.RegistryError):
        reg.load_all()


def test_entry_from_dict_rejects_crossover_with_three_parents(registry_path):
    """crossover なのに parents が期待数(2)と異なる改ざんも拒否する。"""
    reg = r.GenomeRegistry(registry_path)
    g1 = sampler.sample(1)
    e1 = reg.append(g1, op="sample", seed=1, now=FIXED_TIME)
    g2 = sampler.sample(2, name="parent2")
    e2 = reg.append(g2, op="sample", seed=2, now=FIXED_TIME)
    g3 = sampler.crossover(g1, g2, seed=3)
    reg.append(g3, op="crossover", seed=3, parents=[e1.genome_id, e2.genome_id], now=FIXED_TIME)

    lines = registry_path.read_text(encoding="utf-8").strip().split("\n")
    data = json.loads(lines[-1])
    assert data["op"] == "crossover" and len(data["parents"]) == 2  # 前提確認
    data["parents"] = [e1.genome_id, e2.genome_id, e1.genome_id]  # 3 個に改ざん
    lines[-1] = json.dumps(data)
    registry_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(r.RegistryError):
        reg.load_all()


def test_append_accepts_correct_parent_counts_per_op_non_regression(registry_path):
    """非退行確認: 各 op の期待される parent 数どおりの append は従来どおり
    成功する（sample=0 / mutate=1 / crossover=2）。"""
    reg = r.GenomeRegistry(registry_path)
    g1 = sampler.sample(1)
    e1 = reg.append(g1, op="sample", seed=1, now=FIXED_TIME)
    g2 = sampler.mutate(g1, seed=2, scale=0.1)
    e2 = reg.append(g2, op="mutate", seed=2, parents=[e1.genome_id], now=FIXED_TIME)
    g3 = sampler.sample(3, name="parent3")
    e3 = reg.append(g3, op="sample", seed=3, now=FIXED_TIME)
    g4 = sampler.crossover(g2, g3, seed=4)
    e4 = reg.append(g4, op="crossover", seed=4, parents=[e2.genome_id, e3.genome_id], now=FIXED_TIME)

    loaded = reg.load_all()
    assert len(loaded) == 4
    assert e4.parents == [e2.genome_id, e3.genome_id]


# --- PR#261 レビュー R3: エントリ内 genome の検証 / genome_id・audit 整合 ----


def _load_single_entry_dict(registry_path):
    line = registry_path.read_text(encoding="utf-8").strip()
    return json.loads(line)


def _write_single_entry_dict(registry_path, data):
    registry_path.write_text(json.dumps(data), encoding="utf-8")


def test_entry_from_dict_rejects_embedded_genome_with_invalid_schema_version(registry_path):
    """R3(a): エントリ内 genome を genome.from_dict() で検証するため、
    genome 側の schema_version 不正がロード時に検出される。"""
    reg = r.GenomeRegistry(registry_path)
    gen = sampler.sample(1)
    reg.append(gen, op="sample", seed=1, now=FIXED_TIME)

    data = _load_single_entry_dict(registry_path)
    data["genome"]["schema_version"] = "voice-genome/9.9"
    _write_single_entry_dict(registry_path, data)

    with pytest.raises(r.RegistryError):
        reg.load_all()


def test_entry_from_dict_rejects_embedded_genome_with_tampered_physio_range(registry_path):
    """R3(a): genome.from_dict() 経由の physio_range 再計算検証（PR#261 C5）が
    registry の読み込み経路でも効くことを確認する。"""
    reg = r.GenomeRegistry(registry_path)
    gen = sampler.sample(1)
    reg.append(gen, op="sample", seed=1, now=FIXED_TIME)

    data = _load_single_entry_dict(registry_path)
    # 実パラメータとは無関係な偽の out_of_physio_range=True を宣言する。
    data["genome"]["physio_range"]["out_of_physio_range"] = True
    data["genome"]["physio_range"]["violated_bounds"] = ["bogus.field"]
    _write_single_entry_dict(registry_path, data)

    with pytest.raises(r.RegistryError):
        reg.load_all()


def test_entry_from_dict_rejects_genome_id_mismatch_with_content_hash(registry_path):
    """R3(b): genome_id が埋め込み genome の content hash と一致しないエントリを拒否する。"""
    reg = r.GenomeRegistry(registry_path)
    gen = sampler.sample(1)
    entry = reg.append(gen, op="sample", seed=1, now=FIXED_TIME)

    data = _load_single_entry_dict(registry_path)
    assert data["genome_id"] == entry.genome_id
    data["genome_id"] = "deadbeef0000"  # 実 content hash とは異なる 12 桁 hex
    _write_single_entry_dict(registry_path, data)

    with pytest.raises(r.RegistryError):
        reg.load_all()


def test_entry_from_dict_rejects_audit_mismatch_with_genome_audit(registry_path):
    """R3(c): エントリ直下の audit（append() が genome.audit を写した重複表現）が
    genome 側の audit セクションと食い違う場合を拒否する。"""
    reg = r.GenomeRegistry(registry_path)
    gen = sampler.sample(1)
    reg.append(gen, op="sample", seed=1, now=FIXED_TIME)

    data = _load_single_entry_dict(registry_path)
    # genome.audit は sampler.sample() の既定で全 None。エントリ側だけ改ざんする。
    data["audit"]["residual_gate_passed"] = True
    _write_single_entry_dict(registry_path, data)

    with pytest.raises(r.RegistryError):
        reg.load_all()


def test_committed_final_genome_registry_passes_r3_validation():
    """コミット済み results_final/genome_registry.jsonl が R3 の新検証
    （genome 埋め込み検証・genome_id/content-hash 一致・audit 一致）を通ることの
    回帰ガード。通らなくなった場合は成果物側ではなくコード側の不具合を疑うこと
    （PR#261 レビュー R3 指示）。"""
    committed_path = Path(__file__).resolve().parent.parent / "results_final" / "genome_registry.jsonl"
    reg = r.GenomeRegistry(committed_path)
    entries = reg.load_all()
    assert len(entries) == 4


# --- PR#261 レビュー R10: parents の存在検証 / append-only 順序不変条件 -----


def test_append_rejects_parent_id_not_in_registry(registry_path):
    """R10(a): parents に registry 未登録の genome_id を渡すと拒否する。"""
    reg = r.GenomeRegistry(registry_path)
    gen = sampler.sample(1)
    with pytest.raises(r.MissingParentError):
        reg.append(gen, op="mutate", seed=1, parents=["0000deadbeef"], now=FIXED_TIME)

    # 拒否された append は書き込まれていない。
    assert len(reg.load_all()) == 0


def test_missing_parent_error_is_a_registry_error(registry_path):
    assert issubclass(r.MissingParentError, r.RegistryError)


def test_append_accepts_parent_id_already_in_registry(registry_path):
    """非退行確認: 既存の genome_id を親として append する通常経路は従来どおり通る。"""
    reg = r.GenomeRegistry(registry_path)
    parent_gen = sampler.sample(1)
    parent_entry = reg.append(parent_gen, op="sample", seed=1, now=FIXED_TIME)

    child_gen = sampler.mutate(parent_gen, seed=2, scale=0.05)
    child_entry = reg.append(child_gen, op="mutate", seed=2, parents=[parent_entry.genome_id], now=FIXED_TIME)
    assert child_entry.parents == [parent_entry.genome_id]


def test_load_all_rejects_parent_appearing_after_child_in_file_order(registry_path):
    """R10(b): 直接編集・結合で行順が入れ替わり、親が子より後ろに出現する
    JSONL を load_all() で拒否する（append-only の順序不変条件違反）。"""
    reg = r.GenomeRegistry(registry_path)
    parent_gen = sampler.sample(1)
    parent_entry = reg.append(parent_gen, op="sample", seed=1, now=FIXED_TIME)

    child_gen = sampler.mutate(parent_gen, seed=2, scale=0.05)
    reg.append(child_gen, op="mutate", seed=2, parents=[parent_entry.genome_id], now=FIXED_TIME)

    lines = registry_path.read_text(encoding="utf-8").rstrip("\n").split("\n")
    assert len(lines) == 2
    reordered = "\n".join([lines[1], lines[0]]) + "\n"  # 子 → 親 の順に入れ替える
    registry_path.write_text(reordered, encoding="utf-8")

    with pytest.raises(r.MissingParentError):
        reg.load_all()


# --- PR#261 レビュー R28: append() の書き込み前ラウンドトリップ検証 ---------


def test_append_rejects_genome_with_source_mode_outside_closed_vocabulary(registry_path):
    """append() は渡された genome を to_dict() するだけで from_dict() 側の
    意味論検証（R26 の source_mode 閉じた語彙等）を通さないため、frozen
    dataclass を直接組み立てて不正な source_mode を持つ genome を渡すと、
    旧実装では append() 自体は成功してしまい「書けるが load_all() では
    読めない」非対称なエントリを作れてしまっていた。書き込み前の
    ラウンドトリップ検証（_entry_from_dict() を通す）でこれを拒否する。
    """
    reg = r.GenomeRegistry(registry_path)
    gen = sampler.sample(1)
    assert gen.source.source_mode in g.SOURCE_MODES  # 前提確認
    tampered = replace(gen, source=replace(gen.source, source_mode="robotic"))

    with pytest.raises(r.RegistryError):
        reg.append(tampered, op="sample", seed=1, now=FIXED_TIME)

    # 拒否されたエントリは一切書き込まれていない（ファイルが作られてすら
    # いない、または既存内容が変化していない）。
    assert not registry_path.exists() or registry_path.read_text(encoding="utf-8") == ""


def test_append_accepts_valid_genome_non_regression(registry_path):
    """非退行確認: 正常な genome（builder が発行する source_mode 等、全フィールド
    が意味論的に妥当）は書き込み前ラウンドトリップ検証を問題なく通過し、
    従来どおり append・load_all() できる。"""
    reg = r.GenomeRegistry(registry_path)
    gen = sampler.sample(1)
    entry = reg.append(gen, op="sample", seed=1, now=FIXED_TIME)

    loaded = reg.load_all()
    assert len(loaded) == 1
    assert loaded[0].genome_id == entry.genome_id


# --- PR#261 レビュー R29: seed フィールドの実行時型検証 ----------------------


def test_entry_from_dict_rejects_string_seed(registry_path):
    """`RegistryEntry.seed: Optional[int]` は型注釈に過ぎず、旧
    `_entry_from_dict()` は `data.get("seed")` をそのまま代入していたため、
    手編集・インポートされた行が `seed="303"`（文字列）を持っていても
    無検証で通過してしまっていた。"""
    reg = r.GenomeRegistry(registry_path)
    gen = sampler.sample(1)
    reg.append(gen, op="sample", seed=1, now=FIXED_TIME)

    data = json.loads(registry_path.read_text(encoding="utf-8").strip())
    assert isinstance(data["seed"], int)  # 前提確認
    data["seed"] = "303"  # 文字列（型不一致）
    registry_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(r.RegistryError):
        reg.load_all()


def test_entry_from_dict_rejects_bool_seed(registry_path):
    """Python の bool は int のサブクラスのため、素朴な isinstance チェック
    だけでは JSON の真偽値 `seed=true` を int として誤って受理し得る。
    `_as_optional_int()` は bool を明示的に排除する（JSONL への実注入で確認）。
    """
    reg = r.GenomeRegistry(registry_path)
    gen = sampler.sample(1)
    reg.append(gen, op="sample", seed=1, now=FIXED_TIME)

    data = json.loads(registry_path.read_text(encoding="utf-8").strip())
    data["seed"] = True  # JSON の真偽値（bool は int のサブクラス）
    registry_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(r.RegistryError):
        reg.load_all()


def test_as_optional_int_rejects_bool_directly():
    """`_as_optional_int()` 単体でも True/False の両方を拒否することを確認する
    （isinstance(True, int) が True になる Python の罠に対する直接的な回帰確認）。"""
    with pytest.raises(r.RegistryError):
        r._as_optional_int(True, "seed")
    with pytest.raises(r.RegistryError):
        r._as_optional_int(False, "seed")


def test_entry_from_dict_accepts_null_and_int_seed_non_regression(registry_path):
    """非退行確認: seed=null（省略時）と通常の int seed はいずれも従来どおり
    受理される。"""
    reg = r.GenomeRegistry(registry_path)
    g1 = sampler.sample(1)
    e1 = reg.append(g1, op="sample", seed=None, now=FIXED_TIME)  # seed=null
    g2 = sampler.sample(2, name="parent2")
    e2 = reg.append(g2, op="sample", seed=42, now=FIXED_TIME)  # seed=int

    loaded = reg.load_all()
    assert len(loaded) == 2
    assert {e.genome_id: e.seed for e in loaded} == {e1.genome_id: None, e2.genome_id: 42}
