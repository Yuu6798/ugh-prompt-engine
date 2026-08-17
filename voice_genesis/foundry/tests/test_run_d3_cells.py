"""test_run_d3_cells.py — D3 コーパス再生成 CLI の単体テスト。

実音源・実 render は使わない（合成フィクスチャのみ）。対象は 3 点:

1. spec 変種のテキスト置換が byte-identity 不変条件（`"seed"` 以外は一切
   変更しない・元の seed 値のセルは base preset とバイト同一）を守ること。
2. tripwire / 全数照合ロジック（`verify_cell`/`format_report`/
   `validate_manifest_consistency`/`main` の制御フロー）が正しく
   PASS/FAIL を判定すること（render 自体は monkeypatch で差し替える）。
3. PR #265 R8 指摘 #20/#21 の回帰防止: wav/timing csv が `<out>/render/`
   に同居すること（`convert_d3.discover_pairs()` 契約）、および既存
   `--out-dir` への再実行で当該セッションの render が失敗したセルは、
   ディスク上に前回実行の残存ファイル（sha256 一致）があっても無条件
   FAIL になること（stale artifact による false PASS の防止）。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run_d3_cells as rdc  # noqa: E402


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# 1. spec 変種のテキスト置換
# ---------------------------------------------------------------------------


SYNTHETIC_PRESET_TEXT = (
    '{\n'
    '  "schema": "foundry-voice/0.1",\n'
    '  "donor": {\n'
    '    "dataset": "ritsu"\n'
    '  },\n'
    '  "perf": {\n'
    '    "jitter_cents": 3.0\n'
    '  },\n'
    '  "seed": 11\n'
    '}\n'
)


def test_find_seed_field_locates_value_span() -> None:
    original, start, end = rdc.find_seed_field(SYNTHETIC_PRESET_TEXT)
    assert original == 11
    assert SYNTHETIC_PRESET_TEXT[start:end] == "11"


def test_find_seed_field_raises_on_zero_matches() -> None:
    with pytest.raises(rdc.SpecFieldError):
        rdc.find_seed_field('{\n  "no_seed_here": 1\n}\n')


def test_find_seed_field_raises_on_multiple_matches() -> None:
    text = '{\n  "seed": 11,\n  "nested": {"seed": 22}\n}\n'
    with pytest.raises(rdc.SpecFieldError):
        rdc.find_seed_field(text)


def test_substitute_seed_changes_only_the_numeric_value() -> None:
    variant = rdc.substitute_seed(SYNTHETIC_PRESET_TEXT, 907)
    assert variant != SYNTHETIC_PRESET_TEXT
    # every byte except the seed value's digits is unchanged
    assert variant.replace("907", "11", 1) == SYNTHETIC_PRESET_TEXT
    # formatting (indentation / key order / trailing newline) preserved
    assert variant.startswith('{\n  "schema": "foundry-voice/0.1",\n')
    assert variant.endswith('"seed": 907\n}\n')


def test_substitute_seed_same_value_is_byte_identical() -> None:
    variant = rdc.substitute_seed(SYNTHETIC_PRESET_TEXT, 11)
    assert variant == SYNTHETIC_PRESET_TEXT


def test_build_spec_variants_writes_one_file_per_seed(tmp_path: Path) -> None:
    base_preset = tmp_path / "base.json"
    base_preset.write_text(SYNTHETIC_PRESET_TEXT, encoding="utf-8")
    specs_dir = tmp_path / "specs"

    variants = rdc.build_spec_variants(base_preset, [11, 101, 211], specs_dir)

    assert set(variants) == {11, 101, 211}
    for seed, variant in variants.items():
        assert variant.path == specs_dir / f"base_seed{seed}.json"
        assert variant.path.exists()
        assert variant.sha256 == _sha(variant.path.read_bytes())


def test_build_spec_variants_seed11_is_byte_identical_to_base_preset(tmp_path: Path) -> None:
    """Core invariant under test: the variant for the base preset's own seed
    value must be byte-for-byte identical to the base preset file."""
    base_preset = tmp_path / "base.json"
    base_bytes = SYNTHETIC_PRESET_TEXT.encode("utf-8")
    base_preset.write_bytes(base_bytes)
    specs_dir = tmp_path / "specs"

    variants = rdc.build_spec_variants(base_preset, [11, 101], specs_dir)

    assert variants[11].path.read_bytes() == base_bytes
    assert variants[11].sha256 == _sha(base_bytes)
    # the differing seed produced a genuinely different file
    assert variants[101].path.read_bytes() != base_bytes


def test_build_spec_variants_raises_on_identity_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If text-substitution ever produced a mismatched result for the
    original seed (tooling/encoding drift), build_spec_variants must halt
    before writing further files, rather than silently publishing a
    corrupted spec."""
    base_preset = tmp_path / "base.json"
    base_preset.write_text(SYNTHETIC_PRESET_TEXT, encoding="utf-8")
    specs_dir = tmp_path / "specs"

    original_substitute = rdc.substitute_seed

    def _broken_substitute(text: str, new_seed: int) -> str:
        result = original_substitute(text, new_seed)
        if new_seed == 11:
            # Deliberately corrupt an unrelated byte for the seed==11 variant
            # only, simulating tooling/encoding drift.
            result = result.replace('"jitter_cents": 3.0', '"jitter_cents": 4.0')
        return result

    monkeypatch.setattr(rdc, "substitute_seed", _broken_substitute)

    with pytest.raises(rdc.SpecIdentityError):
        rdc.build_spec_variants(base_preset, [11, 101], specs_dir)


def test_real_ritsu_preset_original_seed_is_11_and_byte_identical(tmp_path: Path) -> None:
    """Regression tie to the actual production preset used by the D3
    manifest (results_s3/d3_manifest.json base_preset)."""
    base_preset = rdc.DEFAULT_PRESET
    assert base_preset.exists()
    base_bytes = base_preset.read_bytes()

    variants = rdc.build_spec_variants(base_preset, [11, 101], tmp_path / "specs")

    assert variants[11].path.read_bytes() == base_bytes


# ---------------------------------------------------------------------------
# 2. manifest/results 整合性・全数照合ロジック
# ---------------------------------------------------------------------------


def _manifest(scores: List[str], seeds: List[int], tripwires: Dict[str, str]) -> dict:
    return {
        "schema": "d3-manifest/0.1",
        "scores": scores,
        "seeds": seeds,
        "tripwires": tripwires,
        "cells_total": len(scores) * len(seeds),
    }


def _results(scores: List[str], seeds: List[int], cells: List[dict]) -> dict:
    return {
        "schema": "d3-manifest-results/0.1",
        "registered_manifest_scores": scores,
        "registered_manifest_seeds": seeds,
        "cells_total": len(cells),
        "cells": cells,
    }


def test_validate_schema_versions_accepts_matching_pair() -> None:
    manifest = _manifest(["sakura", "umi"], [11, 101], {})
    results = _results(["sakura", "umi"], [11, 101], [])
    rdc.validate_schema_versions(manifest, results)  # no raise


def test_validate_schema_versions_rejects_manifest_schema_mismatch() -> None:
    """P2 修正 (review #265 R13): `--manifest` の schema が誤記・旧版・未来版
    でも表面互換なキー名だけで消費してしまわない（fail-closed）。"""
    manifest = _manifest(["sakura", "umi"], [11, 101], {})
    manifest["schema"] = "d3-manifest/0.2"
    results = _results(["sakura", "umi"], [11, 101], [])
    with pytest.raises(rdc.SchemaMismatchError, match="manifest schema"):
        rdc.validate_schema_versions(manifest, results)


def test_validate_schema_versions_rejects_missing_manifest_schema() -> None:
    manifest = _manifest(["sakura", "umi"], [11, 101], {})
    del manifest["schema"]
    results = _results(["sakura", "umi"], [11, 101], [])
    with pytest.raises(rdc.SchemaMismatchError, match="manifest schema"):
        rdc.validate_schema_versions(manifest, results)


def test_validate_schema_versions_rejects_results_schema_mismatch() -> None:
    manifest = _manifest(["sakura", "umi"], [11, 101], {})
    results = _results(["sakura", "umi"], [11, 101], [])
    results["schema"] = "d3-manifest-results/0.2"
    with pytest.raises(rdc.SchemaMismatchError, match="results schema"):
        rdc.validate_schema_versions(manifest, results)


def test_validate_schema_versions_rejects_missing_results_schema() -> None:
    manifest = _manifest(["sakura", "umi"], [11, 101], {})
    results = _results(["sakura", "umi"], [11, 101], [])
    del results["schema"]
    with pytest.raises(rdc.SchemaMismatchError, match="results schema"):
        rdc.validate_schema_versions(manifest, results)


def test_validate_schema_versions_accepts_real_production_manifests() -> None:
    """受け入れ条件 4: 正常系（実 d3_manifest.json / d3_manifest_results.json）
    の schema 値が `MANIFEST_SCHEMA`/`RESULTS_SCHEMA` の期待値と一致すること
    を担保する（本テストが落ちる = 期待定数が実ファイルの実測値とずれている
    ことを意味する）。"""
    manifest = json.loads(rdc.DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    results = json.loads(rdc.DEFAULT_RESULTS.read_text(encoding="utf-8"))
    rdc.validate_schema_versions(manifest, results)  # no raise


def test_validate_manifest_consistency_accepts_matching_pair() -> None:
    manifest = _manifest(["sakura", "umi"], [11, 101], {})
    results = _results(["sakura", "umi"], [11, 101], [])
    rdc.validate_manifest_consistency(manifest, results)  # no raise


def test_validate_manifest_consistency_rejects_cells_total_mismatch() -> None:
    manifest = _manifest(["sakura", "umi"], [11, 101], {})
    manifest["cells_total"] = 999
    results = _results(["sakura", "umi"], [11, 101], [])
    with pytest.raises(rdc.ManifestConsistencyError):
        rdc.validate_manifest_consistency(manifest, results)


def test_validate_manifest_consistency_rejects_score_list_mismatch() -> None:
    manifest = _manifest(["sakura", "umi"], [11, 101], {})
    results = _results(["sakura", "d3_kana"], [11, 101], [])
    with pytest.raises(rdc.ManifestConsistencyError):
        rdc.validate_manifest_consistency(manifest, results)


def test_validate_manifest_consistency_rejects_seed_list_mismatch() -> None:
    manifest = _manifest(["sakura", "umi"], [11, 101], {})
    results = _results(["sakura", "umi"], [11, 999], [])
    with pytest.raises(rdc.ManifestConsistencyError):
        rdc.validate_manifest_consistency(manifest, results)


def test_validate_manifest_consistency_accepts_real_production_manifests() -> None:
    manifest = json.loads(rdc.DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    results = json.loads(rdc.DEFAULT_RESULTS.read_text(encoding="utf-8"))
    rdc.validate_manifest_consistency(manifest, results)  # no raise


def test_index_results_cells_keys_by_score_seed() -> None:
    cells = [
        {"score": "sakura", "seed": 11, "wav_sha256": "a"},
        {"score": "umi", "seed": 101, "wav_sha256": "b"},
    ]
    idx = rdc.index_results_cells({"cells": cells})
    assert idx[("sakura", 11)]["wav_sha256"] == "a"
    assert idx[("umi", 101)]["wav_sha256"] == "b"
    assert ("sakura", 101) not in idx


def test_index_results_cells_rejects_duplicate_score_seed_entries() -> None:
    """P2 修正 (review #265 R12): 同一 (score, seed) の重複エントリを後勝ちで
    黙って畳まず、index 構築時に `DuplicateCellError` で fail-closed する。
    2 つのエントリは意図的に矛盾する `wav_sha256` を持つ — 旧実装なら後方の
    値だけが採用され、前方の矛盾した記述は無検査のまま消えていた。"""
    cells = [
        {"score": "sakura", "seed": 11, "wav_sha256": "aaa"},
        {"score": "sakura", "seed": 11, "wav_sha256": "bbb"},  # conflicting duplicate
        {"score": "umi", "seed": 101, "wav_sha256": "ccc"},
    ]
    with pytest.raises(rdc.DuplicateCellError, match=r"sakura.*11|11.*sakura"):
        rdc.index_results_cells({"cells": cells})


def test_validate_results_cell_grid_accepts_exact_match() -> None:
    cells = [
        {"score": "sakura", "seed": 11}, {"score": "sakura", "seed": 101},
        {"score": "umi", "seed": 11}, {"score": "umi", "seed": 101},
    ]
    idx = rdc.index_results_cells({"cells": cells})
    rdc.validate_results_cell_grid(idx, ["sakura", "umi"], [11, 101])  # no raise


def test_validate_results_cell_grid_rejects_extra_cell_outside_registered_grid() -> None:
    """P2 修正 (review #265 R13) の核心シナリオ: 一意だが登録グリッド外の
    余剰セル（score="obsolete", seed=1）は `DuplicateCellError`（R12）を
    誘発しないが、`validate_results_cell_grid` が余剰として fail-closed で
    検出する（旧実装だと `main()` の検証ループが `manifest["scores"]`/
    `["seeds"]` の直積しか走査しないため、このセルは一度も訪問されず矛盾を
    含む results でも 40/40 相当の PASS が出ていた）。"""
    cells = [
        {"score": "sakura", "seed": 11}, {"score": "sakura", "seed": 101},
        {"score": "umi", "seed": 11}, {"score": "umi", "seed": 101},
        {"score": "obsolete", "seed": 1},  # unique key, but outside the registered grid
    ]
    idx = rdc.index_results_cells({"cells": cells})
    with pytest.raises(rdc.CellGridMismatchError, match="extra"):
        rdc.validate_results_cell_grid(idx, ["sakura", "umi"], [11, 101])


def test_validate_results_cell_grid_rejects_missing_cell() -> None:
    cells = [
        {"score": "sakura", "seed": 11}, {"score": "sakura", "seed": 101},
        {"score": "umi", "seed": 11},  # missing (umi, 101)
    ]
    idx = rdc.index_results_cells({"cells": cells})
    with pytest.raises(rdc.CellGridMismatchError, match="missing"):
        rdc.validate_results_cell_grid(idx, ["sakura", "umi"], [11, 101])


def test_validate_results_cell_grid_accepts_real_production_manifests() -> None:
    """受け入れ条件 4: 正常系（実 d3_manifest.json / d3_manifest_results.json、
    40 セル）の cells キー集合が scores x seeds 直積と厳密一致すること。"""
    manifest = json.loads(rdc.DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    results = json.loads(rdc.DEFAULT_RESULTS.read_text(encoding="utf-8"))
    idx = rdc.index_results_cells(results)
    rdc.validate_results_cell_grid(idx, manifest["scores"], manifest["seeds"])  # no raise


def test_verify_cell_pass(tmp_path: Path) -> None:
    wav = tmp_path / "x.wav"
    timing = tmp_path / "x.csv"
    wav.write_bytes(b"WAVDATA")
    timing.write_bytes(b"TIMINGDATA")
    expected = {"wav_sha256": _sha(b"WAVDATA"), "timing_csv_sha256": _sha(b"TIMINGDATA")}

    v = rdc.verify_cell("sakura", 11, wav, timing, expected)

    assert v.status == "PASS"
    assert v.reasons == []


def test_verify_cell_fails_on_wav_sha_mismatch(tmp_path: Path) -> None:
    wav = tmp_path / "x.wav"
    timing = tmp_path / "x.csv"
    wav.write_bytes(b"WRONG")
    timing.write_bytes(b"TIMINGDATA")
    expected = {"wav_sha256": _sha(b"WAVDATA"), "timing_csv_sha256": _sha(b"TIMINGDATA")}

    v = rdc.verify_cell("sakura", 11, wav, timing, expected)

    assert v.status == "FAIL"
    assert any("wav_sha256 mismatch" in r for r in v.reasons)


def test_verify_cell_fails_on_timing_sha_mismatch(tmp_path: Path) -> None:
    wav = tmp_path / "x.wav"
    timing = tmp_path / "x.csv"
    wav.write_bytes(b"WAVDATA")
    timing.write_bytes(b"WRONG")
    expected = {"wav_sha256": _sha(b"WAVDATA"), "timing_csv_sha256": _sha(b"TIMINGDATA")}

    v = rdc.verify_cell("sakura", 11, wav, timing, expected)

    assert v.status == "FAIL"
    assert any("timing_csv_sha256 mismatch" in r for r in v.reasons)


def test_verify_cell_fails_on_missing_files(tmp_path: Path) -> None:
    wav = tmp_path / "missing.wav"
    timing = tmp_path / "missing.csv"
    expected = {"wav_sha256": "x", "timing_csv_sha256": "y"}

    v = rdc.verify_cell("sakura", 11, wav, timing, expected)

    assert v.status == "FAIL"
    assert any("wav missing" in r for r in v.reasons)
    assert any("timing csv missing" in r for r in v.reasons)


def test_verify_cell_fails_when_expected_is_none(tmp_path: Path) -> None:
    wav = tmp_path / "x.wav"
    timing = tmp_path / "x.csv"
    wav.write_bytes(b"WAVDATA")
    timing.write_bytes(b"TIMINGDATA")

    v = rdc.verify_cell("sakura", 11, wav, timing, None)

    assert v.status == "FAIL"
    assert "no expected cell" in v.reasons[0]


def test_format_report_counts_pass_and_includes_tripwire_lines() -> None:
    verifications = [
        rdc.CellVerification("sakura", 11, "PASS", [], "a", "b"),
        rdc.CellVerification("umi", 11, "FAIL", ["wav_sha256 mismatch (...)"], "c", "d"),
    ]
    report = rdc.format_report(verifications, ["tripwire sakura seed=11: match=True"])

    assert "TOTAL: 1/2 PASS" in report
    assert "tripwire sakura seed=11: match=True" in report
    assert "FAIL" in report
    assert "wav_sha256 mismatch" in report


# ---------------------------------------------------------------------------
# 3. main() 制御フロー（render は monkeypatch — 実 render/subprocess は呼ばない）
# ---------------------------------------------------------------------------


SCORES = ["sakura", "umi", "d3_sustain", "d3_kana"]
SEEDS = [11, 101]


def _cell_bytes(score: str, seed: int) -> Tuple[bytes, bytes]:
    return f"WAV:{score}:{seed}".encode(), f"TIMING:{score}:{seed}".encode()


def _spec_sha256_for_seed(seed: int) -> str:
    """The sha256 that `build_spec_variants(rdc.DEFAULT_PRESET, ...)` will
    actually produce for `seed`, computed independently via the same
    text-substitution primitive so fixtures stay tied to the real preset
    without hardcoding a digest."""
    base_text = rdc.DEFAULT_PRESET.read_text(encoding="utf-8")
    variant_text = rdc.substitute_seed(base_text, seed)
    return _sha(variant_text.encode("utf-8"))


def _write_fixture_manifest_and_results(tmp_path: Path) -> Tuple[Path, Path]:
    tripwires = {}
    for score in rdc.TRIPWIRE_SCORES:
        wav_bytes, _ = _cell_bytes(score, rdc.TRIPWIRE_SEED)
        tripwires[f"{score}_seed{rdc.TRIPWIRE_SEED}_wav_sha256"] = _sha(wav_bytes)

    manifest = _manifest(SCORES, SEEDS, tripwires)
    cells = []
    for score in SCORES:
        for seed in SEEDS:
            wav_bytes, timing_bytes = _cell_bytes(score, seed)
            cells.append(
                {
                    "score": score,
                    "seed": seed,
                    "spec_sha256": _spec_sha256_for_seed(seed),
                    "wav_sha256": _sha(wav_bytes),
                    "timing_csv_sha256": _sha(timing_bytes),
                }
            )
    results = _results(SCORES, SEEDS, cells)

    manifest_path = tmp_path / "manifest.json"
    results_path = tmp_path / "results.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    results_path.write_text(json.dumps(results), encoding="utf-8")
    return manifest_path, results_path


def _install_fake_render(
    monkeypatch: pytest.MonkeyPatch,
    calls: List[Tuple[str, int]],
    corrupt: Optional[Tuple[str, int]] = None,
) -> None:
    """Replace `render_cell` with a fake that deterministically writes bytes
    keyed by (score, seed), recording call order in `calls`. If `corrupt`
    matches a (score, seed) pair, its wav content is perturbed so the
    resulting sha256 will not match the expected fixture value."""

    def _fake_render_cell(*, score, spec_path, out_wav, out_timing, voicebank_root, cache_dir, **_kw):
        seed = int(out_wav.stem.rsplit("_seed", 1)[1])
        calls.append((score, seed))
        wav_bytes, timing_bytes = _cell_bytes(score, seed)
        if corrupt is not None and (score, seed) == corrupt:
            wav_bytes = wav_bytes + b"CORRUPT"
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        out_timing.parent.mkdir(parents=True, exist_ok=True)
        out_wav.write_bytes(wav_bytes)
        out_timing.write_bytes(timing_bytes)
        return rdc.RenderOutcome(
            ok=True, returncode=0, stdout="", stderr="", wav_path=out_wav, timing_path=out_timing
        )

    monkeypatch.setattr(rdc, "render_cell", _fake_render_cell)


def test_main_end_to_end_all_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest_path, results_path = _write_fixture_manifest_and_results(tmp_path)
    voicebank_root = tmp_path / "voicebank"
    voicebank_root.mkdir()
    out_dir = tmp_path / "out"

    calls: List[Tuple[str, int]] = []
    _install_fake_render(monkeypatch, calls)

    rc = rdc.main(
        [
            "--voicebank-root", str(voicebank_root),
            "--out-dir", str(out_dir),
            "--preset", str(rdc.DEFAULT_PRESET),
            "--manifest", str(manifest_path),
            "--results", str(results_path),
        ]
    )

    assert rc == 0
    report = (out_dir / "verify_report.txt").read_text(encoding="utf-8")
    assert f"TOTAL: {len(SCORES) * len(SEEDS)}/{len(SCORES) * len(SEEDS)} PASS" in report
    # tripwire cells rendered before the rest, in (sakura, umi) order
    assert calls[0][0] == "sakura"
    assert calls[1][0] == "umi"
    assert len(calls) == len(SCORES) * len(SEEDS)


def test_main_halts_on_tripwire_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest_path, results_path = _write_fixture_manifest_and_results(tmp_path)
    voicebank_root = tmp_path / "voicebank"
    voicebank_root.mkdir()
    out_dir = tmp_path / "out"

    calls: List[Tuple[str, int]] = []
    _install_fake_render(monkeypatch, calls, corrupt=("sakura", rdc.TRIPWIRE_SEED))

    rc = rdc.main(
        [
            "--voicebank-root", str(voicebank_root),
            "--out-dir", str(out_dir),
            "--preset", str(rdc.DEFAULT_PRESET),
            "--manifest", str(manifest_path),
            "--results", str(results_path),
        ]
    )

    assert rc == 1
    # halted immediately after the sakura tripwire mismatch — umi tripwire
    # and the remaining 38-equivalent cells must never have been rendered
    assert calls == [("sakura", rdc.TRIPWIRE_SEED)]
    assert not (out_dir / "verify_report.txt").exists()


def test_main_reports_failure_for_non_tripwire_cell_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path, results_path = _write_fixture_manifest_and_results(tmp_path)
    voicebank_root = tmp_path / "voicebank"
    voicebank_root.mkdir()
    out_dir = tmp_path / "out"

    calls: List[Tuple[str, int]] = []
    _install_fake_render(monkeypatch, calls, corrupt=("d3_kana", 101))

    rc = rdc.main(
        [
            "--voicebank-root", str(voicebank_root),
            "--out-dir", str(out_dir),
            "--preset", str(rdc.DEFAULT_PRESET),
            "--manifest", str(manifest_path),
            "--results", str(results_path),
        ]
    )

    assert rc == 1
    report = (out_dir / "verify_report.txt").read_text(encoding="utf-8")
    assert "d3_kana" in report
    assert "FAIL" in report
    total_cells = len(SCORES) * len(SEEDS)
    assert f"TOTAL: {total_cells - 1}/{total_cells} PASS" in report
    # every cell was still attempted (fail-closed reporting, not early abort)
    assert len(calls) == total_cells


def test_main_rejects_missing_voicebank_root(tmp_path: Path) -> None:
    manifest_path, results_path = _write_fixture_manifest_and_results(tmp_path)
    rc = rdc.main(
        [
            "--voicebank-root", str(tmp_path / "does-not-exist"),
            "--out-dir", str(tmp_path / "out"),
            "--manifest", str(manifest_path),
            "--results", str(results_path),
        ]
    )
    assert rc == 1


def test_main_fails_closed_on_duplicate_cell_before_rendering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2 修正 (review #265 R12) 受け入れ条件: `d3_manifest_results.json` に
    矛盾する重複 (score, seed) エントリがあると、`main()` は render_cell を
    一度も呼ばずに即時 FAIL する（矛盾重複入りでも 40/40 PASS が出る旧穴の
    回帰防止）。"""
    manifest_path, results_path = _write_fixture_manifest_and_results(tmp_path)
    results = json.loads(results_path.read_text(encoding="utf-8"))
    dup_cell = dict(results["cells"][0])
    dup_cell["wav_sha256"] = "conflicting-" + dup_cell["wav_sha256"]
    results["cells"].append(dup_cell)
    results_path.write_text(json.dumps(results), encoding="utf-8")

    voicebank_root = tmp_path / "voicebank"
    voicebank_root.mkdir()
    out_dir = tmp_path / "out"

    calls: List[Tuple[str, int]] = []
    _install_fake_render(monkeypatch, calls)

    rc = rdc.main(
        [
            "--voicebank-root", str(voicebank_root),
            "--out-dir", str(out_dir),
            "--manifest", str(manifest_path),
            "--results", str(results_path),
        ]
    )

    assert rc == 1
    assert calls == []  # render_cell は一度も呼ばれていない（render 前に拒否）
    assert not (out_dir / "render").exists()


def test_main_fails_closed_on_grid_extra_cell_before_rendering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2 修正 (review #265 R13) 受け入れ条件: 一意だが登録グリッド外の余剰
    セル（`DuplicateCellError` は誘発しない）があると、`main()` は
    render_cell を一度も呼ばずに即時 FAIL する（旧実装なら検証ループが
    このセルを一度も訪問せず、矛盾を含む results でも 40/40 相当の PASS が
    出ていた）。"""
    manifest_path, results_path = _write_fixture_manifest_and_results(tmp_path)
    results = json.loads(results_path.read_text(encoding="utf-8"))
    results["cells"].append(
        {
            "score": "obsolete", "seed": 1,
            "spec_sha256": "0" * 64, "wav_sha256": "0" * 64, "timing_csv_sha256": "0" * 64,
        }
    )
    results_path.write_text(json.dumps(results), encoding="utf-8")

    voicebank_root = tmp_path / "voicebank"
    voicebank_root.mkdir()
    out_dir = tmp_path / "out"

    calls: List[Tuple[str, int]] = []
    _install_fake_render(monkeypatch, calls)

    rc = rdc.main(
        [
            "--voicebank-root", str(voicebank_root),
            "--out-dir", str(out_dir),
            "--manifest", str(manifest_path),
            "--results", str(results_path),
        ]
    )

    assert rc == 1
    assert calls == []  # render_cell は一度も呼ばれていない（render 前に拒否）
    assert not (out_dir / "render").exists()


def test_main_fails_closed_on_grid_missing_cell_before_rendering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2 修正 (review #265 R13) 受け入れ条件: 登録グリッドの一部セルが
    `results["cells"]` に欠落していると、`main()` は render_cell を一度も
    呼ばずに即時 FAIL する。"""
    manifest_path, results_path = _write_fixture_manifest_and_results(tmp_path)
    results = json.loads(results_path.read_text(encoding="utf-8"))
    del results["cells"][0]  # drop one registered (score, seed) cell
    results_path.write_text(json.dumps(results), encoding="utf-8")

    voicebank_root = tmp_path / "voicebank"
    voicebank_root.mkdir()
    out_dir = tmp_path / "out"

    calls: List[Tuple[str, int]] = []
    _install_fake_render(monkeypatch, calls)

    rc = rdc.main(
        [
            "--voicebank-root", str(voicebank_root),
            "--out-dir", str(out_dir),
            "--manifest", str(manifest_path),
            "--results", str(results_path),
        ]
    )

    assert rc == 1
    assert calls == []  # render_cell は一度も呼ばれていない（render 前に拒否）
    assert not (out_dir / "render").exists()


def test_main_fails_closed_on_manifest_schema_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2 修正 (review #265 R13) 受け入れ条件: `--manifest` の schema が
    誤記だと `main()` は render_cell を一度も呼ばずに即時 FAIL する。"""
    manifest_path, results_path = _write_fixture_manifest_and_results(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema"] = "d3-manifest/0.2"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    voicebank_root = tmp_path / "voicebank"
    voicebank_root.mkdir()
    out_dir = tmp_path / "out"

    calls: List[Tuple[str, int]] = []
    _install_fake_render(monkeypatch, calls)

    rc = rdc.main(
        [
            "--voicebank-root", str(voicebank_root),
            "--out-dir", str(out_dir),
            "--manifest", str(manifest_path),
            "--results", str(results_path),
        ]
    )

    assert rc == 1
    assert calls == []
    assert not (out_dir / "render").exists()


def test_main_fails_closed_on_results_schema_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2 修正 (review #265 R13) 受け入れ条件: `--results` の schema が
    欠落していても `main()` は render_cell を一度も呼ばずに即時 FAIL する。"""
    manifest_path, results_path = _write_fixture_manifest_and_results(tmp_path)
    results = json.loads(results_path.read_text(encoding="utf-8"))
    del results["schema"]
    results_path.write_text(json.dumps(results), encoding="utf-8")

    voicebank_root = tmp_path / "voicebank"
    voicebank_root.mkdir()
    out_dir = tmp_path / "out"

    calls: List[Tuple[str, int]] = []
    _install_fake_render(monkeypatch, calls)

    rc = rdc.main(
        [
            "--voicebank-root", str(voicebank_root),
            "--out-dir", str(out_dir),
            "--manifest", str(manifest_path),
            "--results", str(results_path),
        ]
    )

    assert rc == 1
    assert calls == []
    assert not (out_dir / "render").exists()


# ---------------------------------------------------------------------------
# 4. PR #265 R8 #20: wav/timing csv は <out>/render/ に同居する
# ---------------------------------------------------------------------------


def test_main_colocates_wav_and_timing_in_render_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`convert_d3.discover_pairs()` は単一ディレクトリ非再帰で `*.wav`/
    `*.csv` を stem 突き合わせする契約のため、両者は同一ディレクトリへ
    同居させる必要がある（別ディレクトリだと後続の convert_d3 実行に
    未文書のマージ作業が要る = PR #265 R8 指摘 #20）。"""
    manifest_path, results_path = _write_fixture_manifest_and_results(tmp_path)
    voicebank_root = tmp_path / "voicebank"
    voicebank_root.mkdir()
    out_dir = tmp_path / "out"

    calls: List[Tuple[str, int]] = []
    _install_fake_render(monkeypatch, calls)

    rc = rdc.main(
        [
            "--voicebank-root", str(voicebank_root),
            "--out-dir", str(out_dir),
            "--manifest", str(manifest_path),
            "--results", str(results_path),
        ]
    )

    assert rc == 0
    render_dir = out_dir / "render"
    wav_stems = {p.stem for p in render_dir.glob("*.wav")}
    csv_stems = {p.stem for p in render_dir.glob("*.csv")}
    total_cells = len(SCORES) * len(SEEDS)
    assert len(wav_stems) == total_cells
    # every wav has a same-stem csv co-located in the same directory
    # (this is exactly what convert_d3.discover_pairs() requires)
    assert wav_stems == csv_stems
    # the old separate-directory layout must not reappear
    assert not (out_dir / "wavs").exists()
    assert not (out_dir / "timing").exists()


# ---------------------------------------------------------------------------
# 5. PR #265 R8 #21: 再実行時の stale 成果物による false PASS の防止
# ---------------------------------------------------------------------------


def test_render_cell_deletes_stale_outputs_before_invoking_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unit test on the real `render_cell` (not the main()-level fake): if a
    stale wav/csv from a previous run already sits at the target path, and
    this run's subprocess invocation fails, the stale files must not survive
    the call (belt-and-suspenders layer #1 of the #21 fix)."""
    render_dir = tmp_path / "render"
    render_dir.mkdir()
    out_wav = render_dir / "sakura_seed11.wav"
    out_timing = render_dir / "sakura_seed11.csv"
    out_wav.write_bytes(b"STALE_WAV_FROM_PREVIOUS_RUN")
    out_timing.write_bytes(b"STALE_TIMING_FROM_PREVIOUS_RUN")

    class _FakeCompletedProcess:
        returncode = 1
        stdout = ""
        stderr = "simulated render.py failure"

    def _fake_subprocess_run(cmd, cwd, capture_output, text):
        # Simulate a failing render.py invocation that writes nothing.
        return _FakeCompletedProcess()

    monkeypatch.setattr(rdc.subprocess, "run", _fake_subprocess_run)

    outcome = rdc.render_cell(
        score="sakura",
        spec_path=tmp_path / "spec.json",
        out_wav=out_wav,
        out_timing=out_timing,
        voicebank_root=tmp_path / "voicebank",
        cache_dir=tmp_path / "cache",
    )

    assert outcome.ok is False
    assert not out_wav.exists()
    assert not out_timing.exists()


def _install_fake_render_no_cleanup_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    calls: List[Tuple[str, int]],
    fail_cell: Tuple[str, int],
) -> None:
    """Worst-case fake: for `fail_cell`, returns ok=False WITHOUT touching
    the filesystem at all (i.e. it does *not* perform the render_cell
    pre-clean step) — this isolates and exercises defense layer #2 alone
    (the `render_failures` tracking / forced-FAIL in `main()`), independent
    of layer #1 (`render_cell`'s own pre-clean, which this fake bypasses
    entirely by replacing `render_cell` itself)."""

    def _fake_render_cell(*, score, spec_path, out_wav, out_timing, voicebank_root, cache_dir, **_kw):
        seed = int(out_wav.stem.rsplit("_seed", 1)[1])
        calls.append((score, seed))
        if (score, seed) == fail_cell:
            return rdc.RenderOutcome(
                ok=False, returncode=1, stdout="", stderr="simulated transient failure",
                wav_path=out_wav, timing_path=out_timing,
            )
        wav_bytes, timing_bytes = _cell_bytes(score, seed)
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        out_timing.parent.mkdir(parents=True, exist_ok=True)
        out_wav.write_bytes(wav_bytes)
        out_timing.write_bytes(timing_bytes)
        return rdc.RenderOutcome(
            ok=True, returncode=0, stdout="", stderr="", wav_path=out_wav, timing_path=out_timing
        )

    monkeypatch.setattr(rdc, "render_cell", _fake_render_cell)


def test_main_forces_fail_despite_stale_correct_artifact_from_previous_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Core regression test for PR #265 R8 #21: re-running against an
    existing `--out-dir` that already holds a *correct* (sha256-matching)
    wav/csv for a cell from a previous successful run, where this run's
    render for that same cell fails. Even with a fake `render_cell` that
    performs none of layer #1's cleanup, `main()` must still report that
    cell as FAIL — not silently PASS off the stale artifact."""
    manifest_path, results_path = _write_fixture_manifest_and_results(tmp_path)
    voicebank_root = tmp_path / "voicebank"
    voicebank_root.mkdir()
    out_dir = tmp_path / "out"

    fail_cell = ("d3_kana", 101)
    # Pre-populate render/ with a stale-but-correct artifact for fail_cell,
    # simulating a leftover from an earlier successful run.
    render_dir = out_dir / "render"
    render_dir.mkdir(parents=True)
    stale_wav_bytes, stale_timing_bytes = _cell_bytes(*fail_cell)
    (render_dir / f"{fail_cell[0]}_seed{fail_cell[1]}.wav").write_bytes(stale_wav_bytes)
    (render_dir / f"{fail_cell[0]}_seed{fail_cell[1]}.csv").write_bytes(stale_timing_bytes)

    calls: List[Tuple[str, int]] = []
    _install_fake_render_no_cleanup_on_failure(monkeypatch, calls, fail_cell)

    rc = rdc.main(
        [
            "--voicebank-root", str(voicebank_root),
            "--out-dir", str(out_dir),
            "--manifest", str(manifest_path),
            "--results", str(results_path),
        ]
    )

    assert rc == 1
    report = (out_dir / "verify_report.txt").read_text(encoding="utf-8")
    # the stale artifact's sha256 does match the fixture's expected value —
    # proving that a naive sha256-only check would have PASSed this cell.
    expected_wav_sha = _sha(stale_wav_bytes)
    idx = rdc.index_results_cells(json.loads(results_path.read_text(encoding="utf-8")))
    assert idx[fail_cell]["wav_sha256"] == expected_wav_sha
    # main() must still report FAIL for it, with a reason that names the
    # render failure (not a sha256 mismatch verdict based on the stale file).
    # (row prefix matches format_report's own `{score:<12}{seed:>6}` layout)
    row_prefix = f'{fail_cell[0]:<12}{fail_cell[1]:>6}'
    fail_line = next(line for line in report.splitlines() if line.startswith(row_prefix))
    assert "FAIL" in fail_line
    assert "render subprocess failed this run" in fail_line
    total_cells = len(SCORES) * len(SEEDS)
    assert f"TOTAL: {total_cells - 1}/{total_cells} PASS" in report


# ---------------------------------------------------------------------------
# 6. rerun 時のコーパス喪失防止 (staging + atomic swap)
# ---------------------------------------------------------------------------


def test_main_preserves_existing_render_dir_bytes_when_a_rerun_cell_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Core regression test: re-running against an already-valid `--out-dir`
    (a prior successful run populated `render/` with a full, verified
    corpus) where this run's render fails partway through — including a
    tripwire-adjacent non-tripwire cell — must leave every byte of the
    existing `render/` untouched and exit non-zero, instead of losing the
    previously-valid pairs mid-update (all render output must go through
    `.staging_render/` and only swap into `render/` on an all-40-cells PASS).
    """
    manifest_path, results_path = _write_fixture_manifest_and_results(tmp_path)
    voicebank_root = tmp_path / "voicebank"
    voicebank_root.mkdir()
    out_dir = tmp_path / "out"

    # --- first run: fully successful, populates a real render/ corpus. ---
    calls1: List[Tuple[str, int]] = []
    _install_fake_render(monkeypatch, calls1)
    rc1 = rdc.main(
        [
            "--voicebank-root", str(voicebank_root),
            "--out-dir", str(out_dir),
            "--manifest", str(manifest_path),
            "--results", str(results_path),
        ]
    )
    assert rc1 == 0
    render_dir = out_dir / "render"
    staging_dir = out_dir / ".staging_render"
    assert render_dir.is_dir()
    assert not staging_dir.exists()  # swapped away, nothing left behind
    before = {p.name: p.read_bytes() for p in render_dir.iterdir()}
    assert before  # sanity: the first run actually wrote something

    # --- second run: one non-tripwire cell fails render this session. ---
    fail_cell = ("d3_kana", 101)
    calls2: List[Tuple[str, int]] = []
    _install_fake_render_no_cleanup_on_failure(monkeypatch, calls2, fail_cell)
    rc2 = rdc.main(
        [
            "--voicebank-root", str(voicebank_root),
            "--out-dir", str(out_dir),
            "--manifest", str(manifest_path),
            "--results", str(results_path),
        ]
    )
    assert rc2 == 1

    after = {p.name: p.read_bytes() for p in render_dir.iterdir()}
    assert after == before, (
        "render/ must remain byte-for-byte unchanged when any rerun cell "
        "fails — the previously-valid corpus must never be lost mid-update"
    )


# ---------------------------------------------------------------------------
# 7. P1 修正 (review #265 R11): containment preflight — 入力パスが管理下の
#    出力パス（`--out-dir`/`.staging_render/`/`render/`）配下にある場合、
#    削除・書き込み開始前に fail-closed で拒否する
# ---------------------------------------------------------------------------


def test_reject_output_collision_rejects_out_paths_colliding_with_each_other() -> None:
    p = Path("/tmp/same")
    with pytest.raises(rdc.OutputCollisionError):
        rdc._reject_output_collision([p, p], protected_roots=[])


def test_reject_output_collision_rejects_protected_root_inside_out_path(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    protected_root = out_dir / "voicebank"  # out_dir の内側にある保護 root
    protected_root.mkdir(parents=True)

    with pytest.raises(rdc.OutputCollisionError):
        rdc._reject_output_collision([out_dir], protected_roots=[protected_root])


def test_reject_output_collision_rejects_out_path_inside_protected_root(tmp_path: Path) -> None:
    protected_root = tmp_path / "voicebank"
    protected_root.mkdir()
    out_dir = protected_root / "nested_out"  # 保護 root の内側にある出力パス

    with pytest.raises(rdc.OutputCollisionError):
        rdc._reject_output_collision([out_dir], protected_roots=[protected_root])


def test_reject_output_collision_rejects_protected_file_inside_out_path(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    protected_file = out_dir / "manifest.json"  # out_dir 配下にある保護ファイル
    out_dir.mkdir()
    protected_file.write_text("{}", encoding="utf-8")

    with pytest.raises(rdc.OutputCollisionError):
        rdc._reject_output_collision(
            [out_dir], protected_roots=[], protected_files=[protected_file]
        )


def test_reject_output_collision_sibling_paths_do_not_raise(tmp_path: Path) -> None:
    """出力パスが保護入力の単なる兄弟であるだけなら誤検知しない（一般的な
    運用: `--out-dir` と `--voicebank-root`/`--preset`/`--manifest`/
    `--results` を同じ親ディレクトリ直下に置く）。"""
    protected_root = tmp_path / "voicebank"
    protected_root.mkdir()
    protected_file = tmp_path / "preset.json"
    protected_file.write_text("{}", encoding="utf-8")
    out_dir = tmp_path / "out"

    rdc._reject_output_collision(
        [out_dir], protected_roots=[protected_root], protected_files=[protected_file]
    )  # no raise


def test_main_rejects_voicebank_root_inside_out_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--voicebank-root` が `--out-dir` 配下にあると、`.staging_render/` の
    無条件クリーンアップがこの入力を巻き込んで削除し得る。render は一切
    呼ばれず（preflight で即座に停止する）非 0 exit で拒否されることを
    確認する。"""
    manifest_path, results_path = _write_fixture_manifest_and_results(tmp_path)
    out_dir = tmp_path / "out"
    voicebank_root = out_dir / "voicebank"  # out_dir の内側 — 衝突を誘発
    voicebank_root.mkdir(parents=True)

    calls: List[Tuple[str, int]] = []
    _install_fake_render(monkeypatch, calls)

    rc = rdc.main(
        [
            "--voicebank-root", str(voicebank_root),
            "--out-dir", str(out_dir),
            "--manifest", str(manifest_path),
            "--results", str(results_path),
        ]
    )

    assert rc == 1
    assert calls == []  # render は一切呼ばれていない
    # out_dir 自体が既に存在していたが、preflight で拒否されたので
    # voicebank_root が失われていないことを確認する（本 P1 修正の核心）。
    assert voicebank_root.exists()


def test_main_rejects_results_file_inside_staging_render_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--results` が `<out-dir>/.staging_render/` 配下にあると、次回
    run 冒頭の `shutil.rmtree(staging_dir)` がこの入力ファイルを巻き込んで
    削除し得る。preflight が render 前に拒否することを確認する。"""
    voicebank_root = tmp_path / "voicebank"
    voicebank_root.mkdir()
    out_dir = tmp_path / "out"
    staging_dir = out_dir / ".staging_render"
    staging_dir.mkdir(parents=True)

    manifest_path, results_path_original = _write_fixture_manifest_and_results(tmp_path)
    results_path = staging_dir / "results.json"  # .staging_render/ 配下に配置
    results_path.write_text(results_path_original.read_text(encoding="utf-8"), encoding="utf-8")

    calls: List[Tuple[str, int]] = []
    _install_fake_render(monkeypatch, calls)

    rc = rdc.main(
        [
            "--voicebank-root", str(voicebank_root),
            "--out-dir", str(out_dir),
            "--manifest", str(manifest_path),
            "--results", str(results_path),
        ]
    )

    assert rc == 1
    assert calls == []
    assert results_path.exists()  # preflight で拒否され、削除されていない


def test_main_normal_layout_with_sibling_paths_still_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回帰防止: 通常運用（`--voicebank-root`/`--manifest`/`--results` が
    `--out-dir` の外側・兄弟パスにある一般的な配置）は preflight 追加後も
    従来どおり成功する。"""
    manifest_path, results_path = _write_fixture_manifest_and_results(tmp_path)
    voicebank_root = tmp_path / "voicebank"
    voicebank_root.mkdir()
    out_dir = tmp_path / "out"

    calls: List[Tuple[str, int]] = []
    _install_fake_render(monkeypatch, calls)

    rc = rdc.main(
        [
            "--voicebank-root", str(voicebank_root),
            "--out-dir", str(out_dir),
            "--preset", str(rdc.DEFAULT_PRESET),
            "--manifest", str(manifest_path),
            "--results", str(results_path),
        ]
    )

    assert rc == 0
    assert len(calls) == len(SCORES) * len(SEEDS)


# ---------------------------------------------------------------------------
# 8. P2 修正 (review #265 R11): d3_manifest_results.json の path フィールドは
#    コンテナ固有の絶対パスではなく basename でなければならない（回帰防止の
#    走査テスト。sha256 値は一切変更されないことも合わせて確認する）。
# ---------------------------------------------------------------------------


def test_default_results_manifest_cell_paths_are_basenames_not_absolute() -> None:
    """`results_s3/d3_manifest_results.json` の全 40 セルの `spec_path`/
    `wav_path`/`timing_csv_path` が basename（区切り文字を含まない単一
    ファイル名）であり、コンテナ固有の絶対パス（`/tmp/...` 等）を含まない
    ことを走査する。実行環境が変わってもリポジトリのチェックアウトが
    汚染されないことを保証する回帰テスト。"""
    results = json.loads(rdc.DEFAULT_RESULTS.read_text(encoding="utf-8"))
    cells = results["cells"]
    assert len(cells) == 40

    for cell in cells:
        for key in ("spec_path", "wav_path", "timing_csv_path"):
            value = cell[key]
            assert "/" not in value, f"{key} is not a basename: {value!r}"
            assert not value.startswith("/tmp"), f"{key} still contains a container path: {value!r}"
            assert Path(value).name == value


def test_default_results_manifest_sha256_values_unchanged_by_basename_migration() -> None:
    """basename 化は `spec_sha256`/`wav_sha256`/`timing_csv_sha256`（計測済み
    実測値）には一切触れていないことを確認する（本テストが張る具体値は
    リポジトリの `d3_manifest_results.json` 実測値からの直接引用であり、
    もし今後の再計測で正当に変わるならこのテストも合わせて更新すればよい
    — ここでの主眼は「basename 移行の diff がハッシュ値を巻き込んでいない
    こと」）。"""
    results = json.loads(rdc.DEFAULT_RESULTS.read_text(encoding="utf-8"))
    cells = {(c["score"], c["seed"]): c for c in results["cells"]}
    sakura_11 = cells[("sakura", 11)]
    assert sakura_11["wav_sha256"] == (
        "833d65d8b1394d3e7fc655d0a41acc212e94a83adcc3ac7686b560b17469ec0c"
    )
    assert sakura_11["spec_sha256"] == (
        "b0be5fa5adabaeb12472a179b3c5a4a86263c441b57bcfa6cb39e5d55cd7abb1"
    )
    assert sakura_11["timing_csv_sha256"] == (
        "7337ecf611f77d06523e37683741c23ca2d89658ca91eef0291cb6777494ea1b"
    )
    # tripwire 一致確認（`validate_manifest_consistency` 系とは独立に、
    # basename 化の前後でトップレベルの tripwire_check セクションが
    # 変わっていないことも確認する）。
    assert results["tripwire_check"]["sakura_match"] is True
    assert results["tripwire_check"]["umi_match"] is True


def test_verify_cell_fails_on_spec_sha256_mismatch_despite_matching_wav_timing(
    tmp_path: Path,
) -> None:
    """A cell whose generated spec has drifted from the pinned
    `d3_manifest_results.json` `spec_sha256` must FAIL verification even
    when its wav/timing csv bytes happen to match the expected hashes
    (an un-pinned input must not be able to produce a silent false PASS)."""
    wav = tmp_path / "x.wav"
    timing = tmp_path / "x.csv"
    wav.write_bytes(b"WAVDATA")
    timing.write_bytes(b"TIMINGDATA")
    expected = {
        "wav_sha256": _sha(b"WAVDATA"),
        "timing_csv_sha256": _sha(b"TIMINGDATA"),
        "spec_sha256": _sha(b"PINNED_SPEC_BYTES"),
    }

    v = rdc.verify_cell(
        "sakura", 11, wav, timing, expected, spec_sha256=_sha(b"DRIFTED_SPEC_BYTES")
    )

    assert v.status == "FAIL"
    assert any("spec_sha256 mismatch" in r for r in v.reasons)
    # wav/timing agreement is independently confirmed (proves this isn't a
    # side effect of an unrelated wav/timing failure)
    assert not any("wav_sha256 mismatch" in r for r in v.reasons)
    assert not any("timing_csv_sha256 mismatch" in r for r in v.reasons)


def test_main_fails_cell_when_manifest_spec_sha256_has_drifted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end wiring check: if `d3_manifest_results.json` records a
    `spec_sha256` that does not match what `build_spec_variants` actually
    produces for that seed (e.g. the pinned preset bytes drifted), `main()`
    must FAIL that cell even though the fake render still writes wav/timing
    bytes that match the fixture's `wav_sha256`/`timing_csv_sha256`."""
    manifest_path, results_path = _write_fixture_manifest_and_results(tmp_path)
    results = json.loads(results_path.read_text(encoding="utf-8"))
    drifted_cell = ("sakura", 101)
    for cell in results["cells"]:
        if (cell["score"], cell["seed"]) == drifted_cell:
            cell["spec_sha256"] = "0" * 64
    results_path.write_text(json.dumps(results), encoding="utf-8")

    voicebank_root = tmp_path / "voicebank"
    voicebank_root.mkdir()
    out_dir = tmp_path / "out"

    calls: List[Tuple[str, int]] = []
    _install_fake_render(monkeypatch, calls)

    rc = rdc.main(
        [
            "--voicebank-root", str(voicebank_root),
            "--out-dir", str(out_dir),
            "--manifest", str(manifest_path),
            "--results", str(results_path),
        ]
    )

    assert rc == 1
    report = (out_dir / "verify_report.txt").read_text(encoding="utf-8")
    row_prefix = f'{drifted_cell[0]:<12}{drifted_cell[1]:>6}'
    fail_line = next(line for line in report.splitlines() if line.startswith(row_prefix))
    assert "FAIL" in fail_line
    assert "spec_sha256 mismatch" in fail_line
    # the staging -> render swap must not have happened (not all cells PASSed):
    # this is a fresh out_dir, so render/ must never have been created at all.
    assert not (out_dir / "render").exists()
