"""c0_validate.py の dry-run 検証テスト（設計正本 §3）。書込・secret 生成なし。"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from voice_genesis.calibration import c0_validate, streams, vocab
from voice_genesis.calibration.candidates import registry as candidate_registry
from voice_genesis.calibration.fixtures import axes as fixture_axes

_MEASUREMENT_DIRECTORY_STATUS = "ABSENT:legacy_path=voice_genesis/harness/measure_v3.py"

_ALL_METER_IDS = sorted(m.value for m in vocab.MeterId)


def _full_independence_ledger() -> dict[str, str]:
    """凍結 99 候補registry から独立性台帳を生成する（手書き 99 行を避ける）。"""
    return {
        c.candidate_id: c.independence_tier.value for c in candidate_registry.ALL_CANDIDATES
    }


def _fake_sha256(path: str) -> str:
    """テスト用の決定的な擬似 sha256（path/名前から機械的に導出。実ファイル
    内容のハッシュ値ではない）。実在ファイルの path+hash 系マップにはもう
    使わない（Codex レビュー 2026-09-01 P1 finding #1: 内容非依存の任意ハッシュ
    でも通過してしまっていたため、`_real_sha256` へ置き換えた）。壊れた
    checkout を模擬するテスト（`test_path_inventory_immune_to_missing_file_in_incomplete_checkout`）
    や `public_seed_id` のようなハッシュ内容そのものは検証されないフィールド
    でのみ引き続き使う。"""
    return hashlib.sha256(path.encode("utf-8")).hexdigest()


def _real_sha256(repo_relative_path: str) -> str:
    """実リポジトリ上のファイルバイトから実測 sha256 を計算する（Codex レビュー
    2026-09-01 P1 finding #1: manifest 側の declared hash はファイル内容と
    実際に一致していなければならないため、fixture 側も実ハッシュを使う）。
    """
    file_path = Path(c0_validate._REPO_ROOT) / repo_relative_path
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def _classify_path(path: str) -> str:
    """分類規則: `candidates/` 配下 → meter、`fixtures/generators/` 配下 →
    generator、`tests/` 配下 → test、それ以外 → schema。分類そのものは記録上
    の区分であり `_check_path_inventory_coverage` は合併集合の網羅性のみを
    要求する。"""
    if path.startswith("voice_genesis/calibration/candidates/"):
        return "meter_paths_sha256"
    if path.startswith("voice_genesis/calibration/fixtures/generators/"):
        return "generator_paths_sha256"
    if path.startswith("voice_genesis/calibration/tests/"):
        return "test_paths_sha256"
    return "schema_paths_sha256"


def _full_path_inventory_maps() -> dict[str, dict[str, str]]:
    """`c0_validate.calibration_path_inventory()`（本体側と同一の inventory
    helper。Codex レビュー 2026-09-01 P1）から 4 カテゴリの path+hash マップを
    機械生成する。宣言する sha256 は実ファイルバイトの実測値
    （`_real_sha256`。finding #1: 内容と無関係な任意ハッシュでは通過させない）。
    """
    out: dict[str, dict[str, str]] = {
        "meter_paths_sha256": {},
        "generator_paths_sha256": {},
        "schema_paths_sha256": {},
        "test_paths_sha256": {},
    }
    for path in sorted(c0_validate.calibration_path_inventory()):
        out[_classify_path(path)][path] = _real_sha256(path)
    return out


def _shape_valid_nested_value(key: str, seed: str) -> object:
    """`c0_validate._shape_violation` (BOUNDED shape validation,
    `[UNDERSPEC-CAL-C18]`) が要求する形状を満たす、`key`/`seed` に応じた
    決定的な placeholder 値を返す。`key` が形状規則の対象外なら、従来どおり
    人間可読な文字列 `f"{seed}_{key}"` を返す。"""
    if key.endswith("_hash") or key.endswith("_sha256"):
        return hashlib.sha256(f"{seed}_{key}".encode("utf-8")).hexdigest()
    if key in ("confound_axes", "boundary_probes", "negative_controls", "stop_rules"):
        return [f"{seed}_{key}_0", f"{seed}_{key}_1"]
    if key == "parameter_grid":
        return {f"{seed}_{key}_axis": [0, 1]}
    return f"{seed}_{key}"


def _full_meter_specs() -> dict[str, dict[str, object]]:
    """凍結 registry の全 meter family に対し、`METER_SPEC_REQUIRED_KEYS`
    を機械的に完全充足するエントリを生成する（設計正本 §3.1「frozen design
    全項目」。`[UNDERSPEC-CAL-C17]`）。値は `_shape_valid_nested_value` で
    BOUNDED shape validation（`[UNDERSPEC-CAL-C18]`）を満たすよう生成する
    （`parameter_grid` は非空 mapping）。"""
    return {
        meter_id: {
            key: _shape_valid_nested_value(key, meter_id.lower())
            for key in c0_validate.METER_SPEC_REQUIRED_KEYS
        }
        for meter_id in _ALL_METER_IDS
    }


def _full_fixture_spec() -> dict[str, dict[str, object]]:
    """`fixtures.axes.FixtureFamily` の全 7 family に対し、
    `FIXTURE_SPEC_REQUIRED_KEYS` を機械的に完全充足するエントリを生成する
    （`[UNDERSPEC-CAL-C17]`）。値は `_shape_valid_nested_value` で BOUNDED
    shape validation（`[UNDERSPEC-CAL-C18]`）を満たすよう生成する
    （`generator_hash` は 64 桁 hex、`confound_axes`/`boundary_probes`/
    `negative_controls` は非空 list）。"""
    return {
        family.value: {
            key: _shape_valid_nested_value(key, family.value.lower())
            for key in c0_validate.FIXTURE_SPEC_REQUIRED_KEYS
        }
        for family in fixture_axes.FixtureFamily
    }


def _full_rng_ledger() -> list[dict[str, object]]:
    """C0 closed set（`streams.expected_rng_stream_names()`）の全 9 stream を
    過不足なく含む `rng_ledger`（Codex レビュー 2026-09-01 P1 finding #2 の
    fixture 健全化。`public_seed_id` はハッシュ内容そのものは検証されない
    フィールドなので `_fake_sha256` で十分）。
    """
    return [
        {"stream_name": name, "seeded": True, "public_seed_id": _fake_sha256(name)}
        for name in sorted(streams.expected_rng_stream_names())
    ]


def _complete_manifest() -> dict[str, object]:
    return {
        "repo": {
            "url": "https://github.com/Yuu6798/ugh-prompt-engine",
            "commit_sha": "a" * 40,
            "dirty_tree": False,
        },
        "measurement_directory_status": _MEASUREMENT_DIRECTORY_STATUS,
        "candidates": _full_path_inventory_maps(),
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
            "meter_specs": _full_meter_specs(),
            "fixture_spec": _full_fixture_spec(),
            "split_spec": {
                "ratios": "50/25/25",
                "seed_scheme": "hkdf",
                "seal_commitment_rule": "hmac-sha256 pre-commit before split reveal",
            },
            "selection_spec": {
                "selection_rule": "lexicographic ABSOLUTE>DIRECTIONAL ceiling then per-family vector",
                "tie_rule": "candidate_id lexical",
                "candidate_exhaustion_rule": "SELECTION_FAILED_CLOSED when eligible pool empty",
                "holdout_fail_outcome": "meter ceiling capped at DIAGNOSTIC_ONLY",
            },
            "provenance_spec": {
                "schema_version": "vgcal-provenance/1",
                "artifact_layout": "campaign_id/family/split/row_id.wav + manifest.json",
            },
            "cost_caps": {
                "compute": "single-node CPU, no GPU budget",
                "storage": "456 logical cells * repeats <= 50GB",
                "budget": "no paid API calls (fully local pipeline)",
            },
            "stop_rules": ["ABORT_ON_UNSEEDED_RNG", "ABORT_ON_HASH_MISMATCH"],
        },
        "independence_ledger": _full_independence_ledger(),
        "rng_ledger": _full_rng_ledger(),
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
    manifest["dependencies"]["pyworld_wheel_hash"] = "a" * 64
    result = c0_validate.validate_c0_manifest(manifest)
    assert result.d4c_ineligible is False
    assert result.is_blocked is False


def test_pyworld_empty_string_wheel_hash_is_ineligible() -> None:
    """Codex レビュー 2026-09-01 P1: `""` は non-None かつ `ABSENT:` prefix
    でもないため、hollow 検査を欠いた旧実装では D4C eligible と誤判定
    していた（hollow pyworld pin values enable D4C）。"""
    manifest = _complete_manifest()
    manifest["dependencies"]["pyworld_version"] = "0.3.4"
    manifest["dependencies"]["pyworld_wheel_hash"] = ""
    result = c0_validate.validate_c0_manifest(manifest)
    assert result.d4c_ineligible is True
    assert result.d4c_ineligibility_reason is not None
    assert "pyworld_wheel_hash" in result.d4c_ineligibility_reason
    assert result.is_blocked is False


def test_pyworld_dict_wheel_hash_is_ineligible() -> None:
    """`{}` のような mapping 値は `isinstance(value, str)` を落とすため、
    型不整合も欠落と同様に ineligible 判定させる。"""
    manifest = _complete_manifest()
    manifest["dependencies"]["pyworld_version"] = "0.3.4"
    manifest["dependencies"]["pyworld_wheel_hash"] = {}
    result = c0_validate.validate_c0_manifest(manifest)
    assert result.d4c_ineligible is True
    assert result.d4c_ineligibility_reason is not None
    assert "pyworld_wheel_hash" in result.d4c_ineligibility_reason
    assert result.is_blocked is False


def test_pyworld_non_hash_string_wheel_hash_is_ineligible() -> None:
    """任意の非 hash 文字列（`^[0-9a-f]{64}$` を満たさない）は、旧実装では
    'present かつ非 None かつ non-ABSENT' の緩い条件を満たしてしまい D4C
    eligible と誤判定されていた（Codex レビュー 2026-09-01 P1）。"""
    manifest = _complete_manifest()
    manifest["dependencies"]["pyworld_version"] = "0.3.4"
    manifest["dependencies"]["pyworld_wheel_hash"] = "not-a-real-hash"
    result = c0_validate.validate_c0_manifest(manifest)
    assert result.d4c_ineligible is True
    assert result.d4c_ineligibility_reason is not None
    assert "pyworld_wheel_hash" in result.d4c_ineligibility_reason
    assert result.is_blocked is False


def test_pyworld_blank_version_is_ineligible() -> None:
    """`pyworld_version` が空白のみの文字列（non-empty だが blank）は
    「exact version」を満たさないため ineligible とする。"""
    manifest = _complete_manifest()
    manifest["dependencies"]["pyworld_version"] = "   "
    manifest["dependencies"]["pyworld_wheel_hash"] = "a" * 64
    result = c0_validate.validate_c0_manifest(manifest)
    assert result.d4c_ineligible is True
    assert result.d4c_ineligibility_reason is not None
    assert "pyworld_version" in result.d4c_ineligibility_reason
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
    ledger = _full_rng_ledger()
    for entry in ledger:
        if entry["stream_name"] == "split/tiebreak":
            entry["seeded"] = False
            entry.pop("public_seed_id", None)
    manifest["rng_ledger"] = ledger
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_UNSEEDED_RNG in result.blocked_codes
    assert "split/tiebreak" in result.unseeded_rng_streams
    # unseeded 宣言以外（closed set・shape とも）は妥当なので内容不備 BLOCK は
    # 併発しない
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
            "selection_spec": {},
            "provenance_spec": {},
            "cost_caps": {},
            "stop_rules": [],
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


def test_path_inventory_covers_full_calibration_package() -> None:
    """fixture 自体の健全性: `_full_path_inventory_maps()` の 4 カテゴリ合併集合
    が `calibration_path_inventory()` と厳密一致すること。"""
    maps = _full_path_inventory_maps()
    declared = set(maps["meter_paths_sha256"]) | set(maps["generator_paths_sha256"])
    declared |= set(maps["schema_paths_sha256"]) | set(maps["test_paths_sha256"])
    assert declared == c0_validate.calibration_path_inventory()


def test_path_inventory_dropped_file_blocks() -> None:
    """[Codex レビュー 2026-09-01 P1] ある実ファイルを 4 マップいずれからも
    省略すると、supplied entries の形状は妥当なままでも BLOCK する
    （従来は "供給された entry のみ" しか検証していなかったため通過していた）。
    """
    manifest = _complete_manifest()
    dropped = "voice_genesis/calibration/streams.py"
    schema = manifest["candidates"]["schema_paths_sha256"]
    assert dropped in schema
    del schema[dropped]
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert any(
        "missing required path" in k and dropped in k for k in result.missing_required_keys
    )


def test_path_inventory_phantom_extra_path_blocks() -> None:
    """[Codex レビュー 2026-09-01 P1] リポジトリに実在しない phantom path を
    紛れ込ませても BLOCK する（"供給された entry のみ" 検証では気づけない）。
    """
    manifest = _complete_manifest()
    phantom = "voice_genesis/calibration/not_a_real_file.py"
    manifest["candidates"]["schema_paths_sha256"][phantom] = "a" * 64
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert any(
        "unknown/extra path" in k and phantom in k for k in result.missing_required_keys
    )


def test_hash_content_mismatch_blocks() -> None:
    """[Codex レビュー 2026-09-01 P1 finding #1] 宣言済みハッシュが 64 桁
    小文字 16 進の形状としては妥当でも、実ファイルバイトの sha256 と一致しなければ
    BLOCK する（従来は形状チェックのみで、任意の well-formed ハッシュが通過して
    いた）。
    """
    manifest = _complete_manifest()
    target = "voice_genesis/calibration/streams.py"
    schema = manifest["candidates"]["schema_paths_sha256"]
    assert target in schema
    schema[target] = "b" * 64  # 形状は妥当だが実ファイル内容とは無関係な値
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert any(
        "does not match actual file content" in k and target in k
        for k in result.missing_required_keys
    )


def test_hash_map_path_duplicated_across_categories_with_conflicting_digest_blocks() -> None:
    """[Codex レビュー 2026-09-01 P1 finding #4] 同一 path が 2 カテゴリに
    矛盾する digest で宣言されていると BLOCK する（従来は 4 マップの合併を
    `declared[path] = sha` で単純マージしており、後勝ちで silently 通過して
    いた）。"""
    manifest = _complete_manifest()
    target = "voice_genesis/calibration/streams.py"
    schema = manifest["candidates"]["schema_paths_sha256"]
    assert target in schema
    correct_sha = schema[target]
    conflicting_sha = "c" * 64
    assert conflicting_sha != correct_sha
    manifest["candidates"]["meter_paths_sha256"][target] = conflicting_sha
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert any(
        "multiple categories" in k and target in k for k in result.missing_required_keys
    )


def test_hash_map_path_duplicated_across_categories_with_identical_digest_still_blocks() -> None:
    """[Codex レビュー 2026-09-01 P1 finding #4] digest が一致していても
    category assignment の一意性そのものが manifest 側の整合性要求
    （§3.1）であるため BLOCK する（重複が"無害"でも見逃さない）。"""
    manifest = _complete_manifest()
    target = "voice_genesis/calibration/streams.py"
    schema = manifest["candidates"]["schema_paths_sha256"]
    assert target in schema
    identical_sha = schema[target]
    manifest["candidates"]["meter_paths_sha256"][target] = identical_sha
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert any(
        "multiple categories" in k and target in k for k in result.missing_required_keys
    )


def test_path_inventory_immune_to_missing_file_in_incomplete_checkout(
    tmp_path, monkeypatch  # noqa: ANN001
) -> None:
    """[Codex レビュー 2026-09-01 P1 (#2)] regression: 旧実装は
    `calibration_path_inventory()` が検証対象 checkout 自身に対して
    `rglob("*.py")` を実行しており circular だった — checkout が 1 ファイルでも
    物理的に欠けていると、inventory 側（rglob の結果）からもそのファイルが消え、
    manifest 側（同じ checkout をスキャンして作る）とも自動的に一致してしまい、
    欠落を検出できなかった。

    新実装は inventory を版管理済み `c0_path_inventory.json` から読むため、
    checkout が不完全でも inventory は正しい値のままである。本テストは
    tmp_path 上に「committed inventory ファイルはあるが 1 ファイルが物理的に
    欠けている(不完全な checkout)」ツリーを構築し、(1) inventory 自体が
    checkout の欠落に影響されないこと、(2) その状態で構築した manifest が
    validator によって確実に BLOCK されること、の両方を確認する。
    """
    committed = sorted(c0_validate.calibration_path_inventory())
    dropped = "voice_genesis/calibration/streams.py"
    assert dropped in committed

    package_dir = tmp_path / "voice_genesis" / "calibration"
    package_dir.mkdir(parents=True)
    inventory_file_rel = f"voice_genesis/calibration/{c0_validate.PATH_INVENTORY_FILENAME}"
    for rel in committed:
        if rel in (dropped, inventory_file_rel):
            continue  # dropped は物理的に欠落させる。inventory file は下で別途書く
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
    (package_dir / c0_validate.PATH_INVENTORY_FILENAME).write_text(
        json.dumps(committed), encoding="utf-8"
    )

    # (1) inventory 自体は checkout の欠落に影響されず、依然として dropped を
    #     要求し続ける（circularity 断ち切りの直接確認: rglob ベースなら
    #     ここで dropped が消えて (2) の検出が成立しなくなる）。
    inventory_from_broken_checkout = c0_validate.calibration_path_inventory(
        repo_root=tmp_path
    )
    assert dropped in inventory_from_broken_checkout

    scanned = c0_validate.scan_calibration_tree_inventory(repo_root=tmp_path)
    assert dropped not in scanned  # 物理的に欠けているので live scan には現れない

    # (2) manifest の hash map は「実際に checkout 上に存在するファイルだけ」を
    #     反映して生成したとする（不完全な checkout をそのまま反映した現実的な
    #     シナリオ）。
    manifest = _complete_manifest()
    grouped: dict[str, dict[str, str]] = {
        "meter_paths_sha256": {},
        "generator_paths_sha256": {},
        "schema_paths_sha256": {},
        "test_paths_sha256": {},
    }
    for path in sorted(scanned):
        grouped[_classify_path(path)][path] = _fake_sha256(path)
    manifest["candidates"] = grouped

    monkeypatch.setattr(c0_validate, "_REPO_ROOT", tmp_path)
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert any(
        "missing required path" in k and dropped in k for k in result.missing_required_keys
    )


def test_meter_specs_missing_one_meter_family_is_listed() -> None:
    manifest = _complete_manifest()
    del manifest["frozen_design"]["meter_specs"]["M6_IDENTITY"]
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert "frozen_design.meter_specs.M6_IDENTITY" in result.missing_required_keys
    # 他の meter は欠けていないので余計な entry は列挙されない
    assert sum(1 for k in result.missing_required_keys if "meter_specs." in k) == 1


# ---------------------------------------------------------------------------
# frozen-design セクションの完全なネスト鍵集合検査（Codex レビュー
# 2026-09-01 P1 finding: `fixture_spec={"family": "F0_CONTROL"}` のような
# hollow な placeholder が非空チェックのみでは通過してしまっていた）。
# `[UNDERSPEC-CAL-C17]`。
# ---------------------------------------------------------------------------


def test_meter_spec_entry_missing_one_nested_key_is_listed() -> None:
    """`frozen_design.meter_specs.<METER_ID>` からネスト鍵を 1 つ落とすと
    `frozen_design.meter_specs.<METER_ID>.<key>` として個別に BLOCK される。
    """
    manifest = _complete_manifest()
    del manifest["frozen_design"]["meter_specs"]["M6_IDENTITY"]["baseline"]
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert "frozen_design.meter_specs.M6_IDENTITY.baseline" in result.missing_required_keys


def test_fixture_spec_placeholder_from_finding_is_blocked() -> None:
    """本 finding が直接指摘した hollow placeholder
    (`fixture_spec={"family": "F0_CONTROL"}`) が確実に BLOCK されることを
    確認する回帰テスト（コードレビュー原文の再現ケース）。"""
    manifest = _complete_manifest()
    manifest["frozen_design"]["fixture_spec"] = {"family": "F0_CONTROL"}
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    # "family" というキー自体は fixture_spec の必須ネスト鍵語彙に無いため、
    # 全 7 family が丸ごと欠落として列挙される。
    for family in fixture_axes.FixtureFamily:
        assert f"frozen_design.fixture_spec.{family.value}" in result.missing_required_keys


def test_fixture_spec_missing_one_family_is_listed() -> None:
    """`frozen_design.fixture_spec` の網羅性検査: `meter_specs` と対をなす
    fixture family 側の欠落検出。"""
    manifest = _complete_manifest()
    del manifest["frozen_design"]["fixture_spec"]["RESONANCE_GT"]
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert "frozen_design.fixture_spec.RESONANCE_GT" in result.missing_required_keys
    assert sum(1 for k in result.missing_required_keys if "fixture_spec." in k) == 1


def test_fixture_spec_entry_missing_one_nested_key_is_listed() -> None:
    manifest = _complete_manifest()
    del manifest["frozen_design"]["fixture_spec"]["FORMANT_GT"]["generator_hash"]
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert "frozen_design.fixture_spec.FORMANT_GT.generator_hash" in result.missing_required_keys


# ---------------------------------------------------------------------------
# BOUNDED shape validation（Codex レビュー 2026-09-01 P1、`[UNDERSPEC-CAL-C18]`）
# 「値は存在するが形状が壊れている」ケース。存在チェックのみでは通過して
# いたが、`_shape_violation` が形状クラスごとに個別に検出する。
# ---------------------------------------------------------------------------


def test_fixture_spec_generator_hash_not_a_hash_string_blocks() -> None:
    """finding 原文の再現ケース: `generator_hash="not-a-hash"` は非空文字列
    ではあるため従来の存在/hollow チェックのみでは通過していた。"""
    manifest = _complete_manifest()
    manifest["frozen_design"]["fixture_spec"]["FORMANT_GT"]["generator_hash"] = "not-a-hash"
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    violations = [
        k for k in result.missing_required_keys
        if k.startswith("frozen_design.fixture_spec.FORMANT_GT.generator_hash")
    ]
    assert len(violations) == 1
    assert ": shape" in violations[0]


def test_fixture_spec_confound_axes_scalar_string_blocks() -> None:
    """finding 原文の再現ケース: `confound_axes="x"` は非空文字列であるため
    従来は list 型を要求せず通過していた。"""
    manifest = _complete_manifest()
    manifest["frozen_design"]["fixture_spec"]["FORMANT_GT"]["confound_axes"] = "x"
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    violations = [
        k for k in result.missing_required_keys
        if k.startswith("frozen_design.fixture_spec.FORMANT_GT.confound_axes")
    ]
    assert len(violations) == 1
    assert ": shape" in violations[0]


def test_fixture_spec_boundary_probes_empty_list_blocks() -> None:
    """空 list は `_is_hollow` の非空コンテナチェックで既に missing 扱いだが、
    shape 規則としても「非空 list」を要求することを明示的に確認する。"""
    manifest = _complete_manifest()
    manifest["frozen_design"]["fixture_spec"]["FORMANT_GT"]["boundary_probes"] = []
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert "frozen_design.fixture_spec.FORMANT_GT.boundary_probes" in result.missing_required_keys


def test_fixture_spec_negative_controls_mapping_instead_of_list_blocks() -> None:
    manifest = _complete_manifest()
    manifest["frozen_design"]["fixture_spec"]["FORMANT_GT"]["negative_controls"] = {
        "a": 1
    }
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    violations = [
        k for k in result.missing_required_keys
        if k.startswith("frozen_design.fixture_spec.FORMANT_GT.negative_controls")
    ]
    assert len(violations) == 1
    assert ": shape" in violations[0]


def test_fixture_spec_generator_version_blank_string_blocks() -> None:
    """`generator_version` は非空チェックだけでは `"  "`（空白のみ）を検出
    できない場合がある形状クラス（version-ish フィールド）の直接検査。"""
    manifest = _complete_manifest()
    manifest["frozen_design"]["fixture_spec"]["FORMANT_GT"]["generator_version"] = "   "
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    # 空白のみ文字列は _is_hollow でも既に missing 扱いになるため、shape
    # violation としてではなく通常の missing として列挙されることを確認する
    # （二重報告しないこと自体もここで確認する）。
    violations = [
        k for k in result.missing_required_keys
        if k.startswith("frozen_design.fixture_spec.FORMANT_GT.generator_version")
    ]
    assert violations == ["frozen_design.fixture_spec.FORMANT_GT.generator_version"]


def test_meter_spec_parameter_grid_scalar_int_blocks() -> None:
    """finding 原文の再現ケース: `parameter_grid=1` は非 hollow な scalar
    (`0`/`False` と異なり `_is_hollow` の対象外) のため従来は通過していた。"""
    manifest = _complete_manifest()
    manifest["frozen_design"]["meter_specs"]["M6_IDENTITY"]["parameter_grid"] = 1
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    violations = [
        k for k in result.missing_required_keys
        if k.startswith("frozen_design.meter_specs.M6_IDENTITY.parameter_grid")
    ]
    assert len(violations) == 1
    assert ": shape" in violations[0]


def test_meter_spec_parameter_grid_empty_mapping_blocks() -> None:
    manifest = _complete_manifest()
    manifest["frozen_design"]["meter_specs"]["M6_IDENTITY"]["parameter_grid"] = {}
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert "frozen_design.meter_specs.M6_IDENTITY.parameter_grid" in result.missing_required_keys


def test_provenance_spec_schema_version_non_string_blocks() -> None:
    manifest = _complete_manifest()
    manifest["frozen_design"]["provenance_spec"]["schema_version"] = 1
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    violations = [
        k for k in result.missing_required_keys
        if k.startswith("frozen_design.provenance_spec.schema_version")
    ]
    assert len(violations) == 1
    assert ": shape" in violations[0]


def test_stop_rules_scalar_string_blocks() -> None:
    """`frozen_design.stop_rules` は `_check_required_blocking` が直接走査する
    トップレベル REQUIRED_BLOCKING キー（ネスト section ではない）だが、非空
    list 形状も要求する（Codex レビュー 2026-09-01 P1、`[UNDERSPEC-CAL-C18]`）。
    非 list scalar は、`[UNDERSPEC-CAL-C19]`（c0_validate.py:490 P1 finding、
    トップレベルコンテナ型検査）により shape 検査より先にトップレベル型
    検査で捕捉され、`": type"` 違反として報告される（`": shape"` ではない —
    型検査を通過して初めて shape 検査に到達する設計）。"""
    manifest = _complete_manifest()
    manifest["frozen_design"]["stop_rules"] = "ABORT_ON_UNSEEDED_RNG"
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    violations = [
        k for k in result.missing_required_keys if k.startswith("frozen_design.stop_rules")
    ]
    assert len(violations) == 1
    assert ": type" in violations[0]


def test_stop_rules_valid_list_is_not_blocked_by_shape() -> None:
    """既存の非空 list 値（fixture 既定値）は shape 違反として誤検出しない
    ことを確認する回帰ガード。"""
    result = c0_validate.validate_c0_manifest(_complete_manifest())
    assert not any(
        k.startswith("frozen_design.stop_rules") for k in result.missing_required_keys
    )


def test_split_spec_missing_one_nested_key_is_listed() -> None:
    manifest = _complete_manifest()
    del manifest["frozen_design"]["split_spec"]["seal_commitment_rule"]
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert "frozen_design.split_spec.seal_commitment_rule" in result.missing_required_keys


def test_selection_spec_missing_one_nested_key_is_listed() -> None:
    manifest = _complete_manifest()
    del manifest["frozen_design"]["selection_spec"]["holdout_fail_outcome"]
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert (
        "frozen_design.selection_spec.holdout_fail_outcome" in result.missing_required_keys
    )


def test_provenance_spec_missing_one_nested_key_is_listed() -> None:
    manifest = _complete_manifest()
    del manifest["frozen_design"]["provenance_spec"]["artifact_layout"]
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert "frozen_design.provenance_spec.artifact_layout" in result.missing_required_keys


def test_cost_caps_missing_one_nested_key_is_listed() -> None:
    manifest = _complete_manifest()
    del manifest["frozen_design"]["cost_caps"]["storage"]
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert "frozen_design.cost_caps.storage" in result.missing_required_keys


# ---------------------------------------------------------------------------
# トップレベルコンテナ型検査（Codex レビュー 2026-09-01 P1: c0_validate.py:490
# finding）: `meter_specs="x"` のようなスカラー値は非空チェックのみでは通過
# してしまい、deeper validator（`isinstance(value, Mapping)` 前提）が
# 早期 return するため実質検証を受けずに素通りしていた。`[UNDERSPEC-CAL-C19]`。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dotted_key", sorted(c0_validate._CONTAINER_TYPE_KEYS))
def test_structured_section_scalar_value_blocks(dotted_key: str) -> None:
    """finding 原文の再現ケース: 構造化セクションをスカラー文字列 `"x"` に
    差し替えても、非空文字列のため旧実装では存在チェックを通過し、以後の
    deeper validator も非 Mapping/非 list を早期 return して見逃していた。
    修正後はトップレベルのコンテナ型を明示検査し BLOCK する。"""
    manifest = _complete_manifest()
    parts = dotted_key.split(".")
    node = manifest
    for part in parts[:-1]:
        node = node[part]  # type: ignore[index]
    node[parts[-1]] = "x"  # type: ignore[index]

    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    violations = [
        k for k in result.missing_required_keys
        if k.startswith(dotted_key) and ": type" in k
    ]
    assert len(violations) == 1, (dotted_key, result.missing_required_keys)


def test_nested_section_entry_that_is_not_a_mapping_is_blocked() -> None:
    """`frozen_design.meter_specs.<METER_ID>` / `fixture_spec.<FAMILY>` が
    mapping ではない（例: 文字列）場合も BLOCK する。"""
    manifest = _complete_manifest()
    manifest["frozen_design"]["meter_specs"]["M6_IDENTITY"] = "not-a-mapping"
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert any(
        "frozen_design.meter_specs.M6_IDENTITY" in k and "must be a mapping" in k
        for k in result.missing_required_keys
    )


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


def test_rng_ledger_closed_set_missing_family_stream_blocks() -> None:
    """[Codex レビュー 2026-09-01 P1 finding #2] closed set のうち 1 family
    分の render stream 宣言が欠けているだけで BLOCK する（従来は well-formed
    entry が 1 件でもあれば通過していた）。"""
    manifest = _complete_manifest()
    manifest["rng_ledger"] = [
        e for e in _full_rng_ledger() if e["stream_name"] != "F0_CONTROL/render"
    ]
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert any(
        "missing required stream" in k and "F0_CONTROL/render" in k
        for k in result.missing_required_keys
    )


def test_rng_ledger_closed_set_extra_unknown_stream_blocks() -> None:
    """closed set に無い stream 名が紛れ込んでいても BLOCK する。"""
    manifest = _complete_manifest()
    ledger = _full_rng_ledger()
    ledger.append(
        {"stream_name": "NOT_A_REAL_STREAM", "seeded": True, "public_seed_id": "9" * 64}
    )
    manifest["rng_ledger"] = ledger
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert any(
        "unknown/extra stream" in k and "NOT_A_REAL_STREAM" in k
        for k in result.missing_required_keys
    )


def test_rng_ledger_closed_set_duplicate_stream_blocks() -> None:
    """同じ stream_name の重複宣言は BLOCK する。"""
    manifest = _complete_manifest()
    ledger = _full_rng_ledger()
    duplicate = dict(ledger[0])
    ledger.append(duplicate)
    manifest["rng_ledger"] = ledger
    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert any(
        "duplicate stream" in k and duplicate["stream_name"] in k
        for k in result.missing_required_keys
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
