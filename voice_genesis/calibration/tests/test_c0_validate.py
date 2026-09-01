"""c0_validate.py の dry-run 検証テスト（設計正本 §3）。書込・secret 生成なし。"""

from __future__ import annotations

import copy

from voice_genesis.calibration import c0_validate, vocab
from voice_genesis.calibration.candidates import registry as candidate_registry

_MEASUREMENT_DIRECTORY_STATUS = "ABSENT:legacy_path=voice_genesis/harness/measure_v3.py"

_ALL_METER_IDS = sorted(m.value for m in vocab.MeterId)


def _full_independence_ledger() -> dict[str, str]:
    """凍結 99 候補registry から独立性台帳を生成する（手書き 99 行を避ける）。"""
    return {
        c.candidate_id: c.independence_tier.value for c in candidate_registry.ALL_CANDIDATES
    }


def _complete_manifest() -> dict[str, object]:
    return {
        "repo": {
            "url": "https://github.com/Yuu6798/ugh-prompt-engine",
            "commit_sha": "a" * 40,
            "dirty_tree": False,
        },
        "measurement_directory_status": _MEASUREMENT_DIRECTORY_STATUS,
        "candidates": {
            "meter_paths_sha256": {"voice_genesis/calibration/candidates/registry.py": "b" * 64},
            "generator_paths_sha256": {"voice_genesis/calibration/fixtures/generators": "c" * 64},
            "schema_paths_sha256": {"voice_genesis/calibration/vocab.py": "d" * 64},
            "test_paths_sha256": {"voice_genesis/calibration/tests": "e" * 64},
        },
        "dependencies": {
            "python_version": "3.11.9",
            "numpy_version": "2.4.6",
            "scipy_version": "1.17.1",
            "librosa_version": "0.11.0",
            "soundfile_version": "0.13.1",
            "pyworld_version": "ABSENT:not_installed",
            "pyworld_wheel_hash": "ABSENT:not_installed",
        },
        "sample_format": {
            "dtype": "float32",
            "channel_policy": "mono",
            "resampling_impl": "scipy.signal.resample_poly",
        },
        "frozen_design": {
            "claim_critical_set": ["M3_FORMANTS", "M2_SPECTRAL_TILT", "M2_APERIODICITY"],
            "meter_specs": {
                meter_id: {"construct": f"{meter_id.lower()}_construct"}
                for meter_id in _ALL_METER_IDS
            },
            "fixture_spec": {"family": "F0_CONTROL"},
            "split_spec": {"split": "50/25/25", "seed": "hkdf"},
            "selection_rule": {"tie_rule": "candidate_id lexical"},
            "provenance_spec": {"schema": "vgcal-provenance/1"},
        },
        "independence_ledger": _full_independence_ledger(),
        "rng_ledger": [
            {"stream_name": "split_secret", "seeded": True, "public_seed_id": "1" * 64},
            {
                "stream_name": "generator/F0_CONTROL/row0",
                "seeded": True,
                "public_seed_id": "2" * 64,
            },
        ],
        "env": {
            "container_image_digest": "ABSENT:no_container_used",
            "blas_fft_backend": "openblas-0.3.27",
            "os_kernel_cpu_arch": "linux-6.18-x86_64",
            "wheel_hashes": {"numpy": "f" * 64},
            "world_build_flags": "ABSENT:wheel_used",
        },
    }


def test_complete_manifest_meter_specs_covers_registry_meters() -> None:
    """fixture 自体の健全性: registry の全 meter family を meter_specs が持つこと。"""
    registry_meters = {c.meter.value for c in candidate_registry.ALL_CANDIDATES}
    assert set(_ALL_METER_IDS) == registry_meters


def test_complete_manifest_has_no_blocks() -> None:
    result = c0_validate.validate_c0_manifest(_complete_manifest())
    assert result.blocked_codes == ()
    assert result.missing_required_keys == ()
    assert result.is_blocked is False


def test_complete_manifest_records_weak_env_lock_downgrades() -> None:
    result = c0_validate.validate_c0_manifest(_complete_manifest())
    assert f"env.container_image_digest:{c0_validate.WEAK_ENV_LOCK}" in result.downgrade_annotations
    assert f"env.world_build_flags:{c0_validate.WEAK_ENV_LOCK}" in result.downgrade_annotations
    # 実値が記録されているキーには downgrade を付けない
    assert not any(a.startswith("env.blas_fft_backend:") for a in result.downgrade_annotations)


def test_complete_manifest_pyworld_absent_only_ineligible_not_blocked() -> None:
    result = c0_validate.validate_c0_manifest(_complete_manifest())
    assert result.d4c_ineligible is True
    assert result.is_blocked is False
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE not in result.blocked_codes


def test_pyworld_present_makes_d4c_eligible() -> None:
    manifest = _complete_manifest()
    manifest["dependencies"]["pyworld_version"] = "0.3.4"
    manifest["dependencies"]["pyworld_wheel_hash"] = "g" * 64
    result = c0_validate.validate_c0_manifest(manifest)
    assert result.d4c_ineligible is False
    assert result.is_blocked is False


def _delete_dotted(manifest: dict[str, object], dotted_path: str) -> dict[str, object]:
    manifest = copy.deepcopy(manifest)
    parts = dotted_path.split(".")
    node = manifest
    for part in parts[:-1]:
        node = node[part]  # type: ignore[index]
    del node[parts[-1]]  # type: ignore[arg-type]
    return manifest


def test_each_required_blocking_key_omission_blocks_with_correct_code() -> None:
    for key in c0_validate.REQUIRED_BLOCKING_KEYS:
        manifest = _delete_dotted(_complete_manifest(), key)
        result = c0_validate.validate_c0_manifest(manifest)
        assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes, key
        assert key in result.missing_required_keys, key


def test_dirty_tree_true_is_treated_as_missing() -> None:
    manifest = _complete_manifest()
    manifest["repo"]["dirty_tree"] = True
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert any(k.startswith("repo.dirty_tree") for k in result.missing_required_keys)


def test_recorded_or_absent_key_missing_entirely_blocks() -> None:
    manifest = _delete_dotted(_complete_manifest(), "env.container_image_digest")
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert "env.container_image_digest" in result.missing_required_keys


def test_recorded_or_absent_key_with_real_value_no_downgrade() -> None:
    manifest = _complete_manifest()
    manifest["env"]["container_image_digest"] = "sha256:" + "0" * 64
    result = c0_validate.validate_c0_manifest(manifest)
    assert result.is_blocked is False
    assert not any(a.startswith("env.container_image_digest:") for a in result.downgrade_annotations)


def test_unseeded_rng_stream_blocks() -> None:
    manifest = _complete_manifest()
    manifest["rng_ledger"] = [
        {"stream_name": "split_secret", "seeded": True, "public_seed_id": "1" * 64},
        {"stream_name": "tie_break", "seeded": False},
    ]
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_UNSEEDED_RNG in result.blocked_codes
    assert "tie_break" in result.unseeded_rng_streams
    # unseeded 宣言以外は shape として妥当なので内容不備 BLOCK は併発しない
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE not in result.blocked_codes


def test_all_seeded_rng_ledger_has_no_unseeded_block() -> None:
    result = c0_validate.validate_c0_manifest(_complete_manifest())
    assert vocab.BlockedCode.BLOCKED_C0_UNSEEDED_RNG not in result.blocked_codes
    assert result.unseeded_rng_streams == ()


def test_hollow_empty_container_manifest_is_blocked() -> None:
    """Codex レビュー 2026-09-01 P1: 全キーが存在するが空コンテナ/空文字列の
    「hollow」manifest は、存在チェックのみでは通ってしまっていた。内容検証で
    ちゃんと BLOCK されることを確認する。
    """
    hollow: dict[str, object] = {
        "repo": {"url": "", "commit_sha": "", "dirty_tree": False},
        "measurement_directory_status": "",
        "candidates": {
            "meter_paths_sha256": {},
            "generator_paths_sha256": {},
            "schema_paths_sha256": {},
            "test_paths_sha256": {},
        },
        "dependencies": {
            "python_version": "",
            "numpy_version": "",
            "scipy_version": "",
            "librosa_version": "",
            "soundfile_version": "",
        },
        "sample_format": {"dtype": "", "channel_policy": "", "resampling_impl": ""},
        "frozen_design": {
            "claim_critical_set": [],
            "meter_specs": {},
            "fixture_spec": {},
            "split_spec": {},
            "selection_rule": {},
            "provenance_spec": {},
        },
        "independence_ledger": {},
        "rng_ledger": [],
    }
    result = c0_validate.validate_c0_manifest(hollow)
    assert result.is_blocked is True
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    # `repo.dirty_tree=False` は hollow ではなく正しい記録値そのものなので missing
    # に現れない。それ以外の全 REQUIRED_BLOCKING キーは hollow のため missing。
    expected_missing = set(c0_validate.REQUIRED_BLOCKING_KEYS) - {"repo.dirty_tree"}
    assert expected_missing.issubset(set(result.missing_required_keys))
    assert "repo.dirty_tree" not in result.missing_required_keys


def test_hash_map_entry_with_malformed_sha256_blocks() -> None:
    manifest = _complete_manifest()
    manifest["candidates"]["meter_paths_sha256"] = {
        "voice_genesis/calibration/candidates/registry.py": "not-a-valid-sha256"
    }
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert any(
        k.startswith("candidates.meter_paths_sha256[") for k in result.missing_required_keys
    )


def test_hash_map_entry_with_empty_path_blocks() -> None:
    manifest = _complete_manifest()
    manifest["candidates"]["meter_paths_sha256"] = {"": "a" * 64}
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert any(
        k.startswith("candidates.meter_paths_sha256[") for k in result.missing_required_keys
    )


def test_meter_specs_missing_one_meter_family_is_listed() -> None:
    manifest = _complete_manifest()
    del manifest["frozen_design"]["meter_specs"]["M6_IDENTITY"]
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert "frozen_design.meter_specs.M6_IDENTITY" in result.missing_required_keys
    # 他の meter は欠けていないので余計な entry は列挙されない
    assert sum(1 for k in result.missing_required_keys if "meter_specs." in k) == 1


def test_independence_ledger_invalid_tier_value_blocks() -> None:
    manifest = _complete_manifest()
    manifest["independence_ledger"]["F0-B0-CURRENT"] = "NOT_A_REAL_TIER"
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert any(k.startswith("independence_ledger[") for k in result.missing_required_keys)


def test_independence_ledger_missing_candidate_id_blocks() -> None:
    """[Codex レビュー 2026-09-01 P1] ledger のキー集合は凍結 99 候補
    registry と完全一致しなければならない: 1 件欠落しただけで BLOCK。"""
    manifest = _complete_manifest()
    del manifest["independence_ledger"]["F0-B0-CURRENT"]
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert any(
        "missing candidate_id: 'F0-B0-CURRENT'" in k for k in result.missing_required_keys
    )


def test_independence_ledger_unknown_extra_candidate_id_blocks() -> None:
    """registry に存在しない candidate_id が ledger に紛れ込んでいても BLOCK。"""
    manifest = _complete_manifest()
    manifest["independence_ledger"]["NOT-A-REAL-CANDIDATE-ID"] = "INDEPENDENT_ANALYTIC"
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert any(
        "unknown/extra candidate_id: 'NOT-A-REAL-CANDIDATE-ID'" in k
        for k in result.missing_required_keys
    )


def test_independence_ledger_tier_mismatch_blocks() -> None:
    """ledger の tier が registry の宣言 tier と食い違えば BLOCK（cross-check）。"""
    manifest = _complete_manifest()
    # registry は F0-B0-CURRENT を INDEPENDENT_ANALYTIC と宣言する。
    manifest["independence_ledger"]["F0-B0-CURRENT"] = "CROSS_IMPLEMENTATION"
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert any(
        "tier mismatch" in k and "F0-B0-CURRENT" in k for k in result.missing_required_keys
    )


def test_rng_ledger_entry_missing_stream_name_blocks() -> None:
    manifest = _complete_manifest()
    manifest["rng_ledger"] = [{"seeded": True, "public_seed_id": "3" * 64}]
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert any(".stream_name" in k for k in result.missing_required_keys)


def test_rng_ledger_seeded_entry_missing_seed_reference_blocks() -> None:
    """`seeded=true` だが seed 参照（`public_seed_id`）が記録されていない
    entry は、`seeded=false` の明示宣言（BLOCKED_C0_UNSEEDED_RNG）とは別に、
    manifest 内容不備として BLOCKED_C0_MANIFEST_INCOMPLETE で捕捉する。
    """
    manifest = _complete_manifest()
    manifest["rng_ledger"] = [{"stream_name": "split_secret", "seeded": True}]
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert any(".public_seed_id" in k for k in result.missing_required_keys)
    # seeded=true と明示宣言されているので unseeded 扱いにはしない
    assert vocab.BlockedCode.BLOCKED_C0_UNSEEDED_RNG not in result.blocked_codes


def test_validate_c0_manifest_is_pure_no_io(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """dry-run 検証は書込を一切行わない（同一ディレクトリのファイル一覧が不変）。"""
    monkeypatch.chdir(tmp_path)
    before = sorted(tmp_path.iterdir())
    c0_validate.validate_c0_manifest(_complete_manifest())
    after = sorted(tmp_path.iterdir())
    assert before == after == []
