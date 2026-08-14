"""test_registry.py — P4 (VG-010) の受け入れテスト: JSONL append、lineage 遡上、content hash。"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

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
