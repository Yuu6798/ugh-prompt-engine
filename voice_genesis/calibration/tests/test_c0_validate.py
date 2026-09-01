"""c0_validate.py の dry-run 検証テスト（設計正本 §3）。書込・secret 生成なし。"""

from __future__ import annotations

import copy

from voice_genesis.calibration import c0_validate, vocab

_MEASUREMENT_DIRECTORY_STATUS = "ABSENT:legacy_path=voice_genesis/harness/measure_v3.py"


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
            "meter_specs": {"F0_CONTROL": {"construct": "fundamental_frequency"}},
            "fixture_spec": {"family": "F0_CONTROL"},
            "split_spec": {"split": "50/25/25", "seed": "hkdf"},
            "selection_rule": {"tie_rule": "candidate_id lexical"},
            "provenance_spec": {"schema": "vgcal-provenance/1"},
        },
        "independence_ledger": {"F0_CONTROL": "INDEPENDENT_ANALYTIC"},
        "rng_ledger": [
            {"stream_name": "split_secret", "seeded": True},
            {"stream_name": "generator/F0_CONTROL/row0", "seeded": True},
        ],
        "env": {
            "container_image_digest": "ABSENT:no_container_used",
            "blas_fft_backend": "openblas-0.3.27",
            "os_kernel_cpu_arch": "linux-6.18-x86_64",
            "wheel_hashes": {"numpy": "f" * 64},
            "world_build_flags": "ABSENT:wheel_used",
        },
    }


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
        {"stream_name": "split_secret", "seeded": True},
        {"stream_name": "tie_break", "seeded": False},
    ]
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_UNSEEDED_RNG in result.blocked_codes
    assert "tie_break" in result.unseeded_rng_streams


def test_all_seeded_rng_ledger_has_no_unseeded_block() -> None:
    result = c0_validate.validate_c0_manifest(_complete_manifest())
    assert vocab.BlockedCode.BLOCKED_C0_UNSEEDED_RNG not in result.blocked_codes
    assert result.unseeded_rng_streams == ()


def test_validate_c0_manifest_is_pure_no_io(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """dry-run 検証は書込を一切行わない（同一ディレクトリのファイル一覧が不変）。"""
    monkeypatch.chdir(tmp_path)
    before = sorted(tmp_path.iterdir())
    c0_validate.validate_c0_manifest(_complete_manifest())
    after = sorted(tmp_path.iterdir())
    assert before == after == []
