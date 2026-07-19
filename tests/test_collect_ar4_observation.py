"""tests/test_collect_ar4_observation.py — scripts/collect_ar4_observation.py smoke tests.

`collect_ar4_observation.py` performs real MusicGen inference in its `generate`
subcommand (torch required, non-deterministic model load), so this test suite
stays confined to the pure-logic manifest-building helpers — the same split
`test_musicgen_runbook.py` uses for `collect_musicgen_takes.py`. In particular
it locks in the `performance_package` provenance shape (Codex P2 review #191,
discussion_r3610116228): the generator must never serialize a machine-specific
absolute path, and its default output for the AR4 midnight_signal fixture must
match the committed `ar4_takes_manifest.json` byte-for-byte in structure. It
also locks in the geometry-independent composite-pin verification for
``derived_score`` / ``preservation_contract`` added in Codex 4R P2 review #191
(discussion_r3610170990): a `--score` or `--arrangement` mistyped to another
*existing* repo YAML must be caught by pin mismatch, not waved through by an
existence-only check.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.collect_ar4_observation import (
    DEFAULT_ARRANGEMENT,
    DEFAULT_CAPABILITY_PROFILE,
    DEFAULT_IDENTITY_MANIFEST,
    DEFAULT_SCORE,
    ROOT,
    _generate_output_paths,
    _reject_generate_output_collision,
    build_package_provenance,
    build_takes_manifest,
    main,
)

SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from svp_rpe.arrange.contract import build_preservation_contract  # noqa: E402
from svp_rpe.arrange.identity import IdentityManifest  # noqa: E402
from svp_rpe.arrange.models import ArrangementSpec  # noqa: E402
from svp_rpe.arrange.package import (  # noqa: E402
    compute_derived_score_sha256,
    compute_device_profile_sha256,
    compute_preservation_contract_sha256,
)
from svp_rpe.arrange.resolver import resolve_arrangement  # noqa: E402
from svp_rpe.compose.device_profile import load_device_profile  # noqa: E402
from svp_rpe.compose.models import CompositionScore  # noqa: E402
from svp_rpe.compose.prompt_renderer import resolve_backend_descriptor  # noqa: E402

FIXTURE_MANIFEST_PATH = Path(
    "examples/arrangement/midnight_signal/observed/musicgen/ar4_takes_manifest.json"
)

# 誤指定シナリオ用: リポジトリに実在するが DEFAULT_SCORE / DEFAULT_ARRANGEMENT とは
# 別内容の YAML（存在はするが誤った入力を模す）。
WRONG_SCORE = ROOT / "examples/control/k2_suno_segments/structure4_score_high.yaml"
WRONG_ARRANGEMENT = ROOT / "examples/arrangement/midnight_signal/edm.identity.arrangement.yaml"


def _load_fixture_manifest() -> dict[str, Any]:
    return json.loads(FIXTURE_MANIFEST_PATH.read_text(encoding="utf-8"))


def _recipe_input_entry(path: Path) -> dict[str, str]:
    """``build_recipe.inputs.<name>`` の期待値 (``{"path": ..., "sha256": ...}``)
    を、検証対象 (`build_package_provenance`) とは独立に ``path.read_bytes()``
    -> ``hashlib.sha256`` で直接計算する（Codex 6R P2 review #191: repo 相対
    パスのみでなく、検証済み per-input raw bytes sha256 も焼き込む形式への追従。
    tautology を避けるため検証対象の内部ヘルパーは使わない）。"""
    return {
        "path": path.resolve().relative_to(ROOT).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _real_recipe_package_data() -> dict[str, Any]:
    """DEFAULT_SCORE/DEFAULT_IDENTITY_MANIFEST/DEFAULT_ARRANGEMENT/
    DEFAULT_CAPABILITY_PROFILE から実際に compile した場合に
    `performance_package.json` が持つはずの 4 pin を、production の pin 計算
    経路（`resolve_arrangement` / `compute_derived_score_sha256` /
    `build_preservation_contract` / `compute_preservation_contract_sha256`）を
    直接呼んで独立に再現する。`build_package_provenance` の内部 recompute
    ヘルパーとは別経路で期待値を作ることで、テストが検証対象と同じ計算を
    無条件に信頼してしまう（tautology）のを避ける。
    """
    score_bytes = DEFAULT_SCORE.read_bytes()
    manifest_bytes = DEFAULT_IDENTITY_MANIFEST.read_bytes()
    arrangement_bytes = DEFAULT_ARRANGEMENT.read_bytes()
    capability_bytes = DEFAULT_CAPABILITY_PROFILE.read_bytes()

    source = CompositionScore.model_validate(yaml.safe_load(score_bytes))
    manifest = IdentityManifest.model_validate(yaml.safe_load(manifest_bytes))
    spec = ArrangementSpec.model_validate(yaml.safe_load(arrangement_bytes))

    resolution = resolve_arrangement(source, spec)
    derived_score_sha256 = compute_derived_score_sha256(resolution.derived_score)

    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    spec_sha256 = hashlib.sha256(arrangement_bytes).hexdigest()
    contract = build_preservation_contract(
        manifest, spec, manifest_sha256=manifest_sha256, spec_sha256=spec_sha256
    )
    contract_sha256 = compute_preservation_contract_sha256(contract)

    # device_profile: `svprpe package` が auto-load する隠れ入力（Codex 5R P2
    # review #191, discussion_r3610193512）。production と同じ経路
    # （resolve_arrangement -> resolve_backend_descriptor -> load_device_profile
    # -> compute_device_profile_sha256）で独立に再現する。
    render_generator = resolve_backend_descriptor(
        resolution.derived_score.rendering.target_backend
    ).profile_key
    device_profile = load_device_profile(render_generator)
    assert device_profile is not None  # 前提: musicgen device profile は committed
    device_profile_entry: dict[str, Any] = {
        "generator": render_generator,
        "status": "loaded",
        "sha256": compute_device_profile_sha256(device_profile),
    }

    return {
        "inputs": {
            "identity_manifest": {"sha256": manifest_sha256},
            "capability_profile": {
                "sha256": hashlib.sha256(capability_bytes).hexdigest()
            },
            "derived_score": {"sha256": derived_score_sha256},
            "preservation_contract": {"sha256": contract_sha256},
            "device_profile": device_profile_entry,
        }
    }


_DEFAULT_RECIPE_PACKAGE_DATA = _real_recipe_package_data()


def test_build_package_provenance_never_records_absolute_scratch_path() -> None:
    """scratch ビルドディレクトリ（リポジトリ外）の package path は絶対パスとして
    記録されない — `str(package_path)` の直接埋め込みが禁じ手（Codex P2 #191）。"""
    scratch_package = Path("/tmp/ar4_scratch_build/performance_package.json")
    provenance = build_package_provenance(
        scratch_package,
        "a" * 64,
        score=DEFAULT_SCORE,
        identity_manifest=DEFAULT_IDENTITY_MANIFEST,
        arrangement=DEFAULT_ARRANGEMENT,
        capability_profile=DEFAULT_CAPABILITY_PROFILE,
    )
    serialized = json.dumps(provenance)
    assert str(scratch_package) not in serialized
    assert "/tmp/" not in serialized


def test_build_package_provenance_scratch_build_records_structured_recipe() -> None:
    """4 入力が全て repo 相対に解決でき、かつ package の pin と幾何非依存の
    合成 pin（derived_score / preservation_contract / device_profile）が一致
    すれば、入力パス + コンパイルコマンドの構造化レシピ (`build_recipe`) を
    記録する。device_profile は `svprpe package` の隠れた auto-load 入力
    なので `inputs` に追記され、`auto_loaded_inputs` にその旨の注記が付く
    （Codex 5R P2 review #191, discussion_r3610193512）。"""
    scratch_package = Path("/tmp/ar4_scratch_build/performance_package.json")
    package_sha256 = "b" * 64
    provenance = build_package_provenance(
        scratch_package,
        package_sha256,
        _DEFAULT_RECIPE_PACKAGE_DATA,
        score=DEFAULT_SCORE,
        identity_manifest=DEFAULT_IDENTITY_MANIFEST,
        arrangement=DEFAULT_ARRANGEMENT,
        capability_profile=DEFAULT_CAPABILITY_PROFILE,
    )
    assert provenance["sha256"] == package_sha256
    assert "path" not in provenance  # 旧フィールド名は残らない
    recipe = provenance["build_recipe"]
    assert recipe["inputs"] == {
        "score": _recipe_input_entry(DEFAULT_SCORE),
        "identity_manifest": _recipe_input_entry(DEFAULT_IDENTITY_MANIFEST),
        "arrangement": _recipe_input_entry(DEFAULT_ARRANGEMENT),
        "capability_profile": _recipe_input_entry(DEFAULT_CAPABILITY_PROFILE),
        "device_profile": _recipe_input_entry(ROOT / "config/device_profiles/musicgen.yaml"),
    }
    assert recipe["auto_loaded_inputs"].keys() == {"device_profile"}
    assert "auto-loaded" in recipe["auto_loaded_inputs"]["device_profile"]
    assert "not a compile_command argument" in recipe["auto_loaded_inputs"]["device_profile"]
    assert recipe["compile_command"] == (
        "svprpe package examples/arrangement/midnight_signal/composition_score.yaml "
        "examples/arrangement/midnight_signal/identity_manifest.yaml "
        "examples/arrangement/midnight_signal/edm.identity.musicgen.arrangement.yaml "
        "--capability-profile config/capability_profiles/musicgen.yaml "
        "--output-dir <output-dir>"
    )
    # レシピの全ての入力パスが実在する repo 相対パスであること（架空パスの捏造禁止）、
    # かつ焼き込まれた sha256 が実ファイルの sha256 と一致すること
    for entry in recipe["inputs"].values():
        input_path = ROOT / entry["path"]
        assert input_path.is_file()
        assert entry["sha256"] == hashlib.sha256(input_path.read_bytes()).hexdigest()
    # `_DEFAULT_RECIPE_PACKAGE_DATA` には channel_artifacts が無い（このテストの
    # 検証対象は inputs pin のみ）ため、output_geometry は「幾何非依存」注記に
    # 落ちる（Codex 7R P2 review #191, discussion_r3610250841）。
    assert recipe["output_geometry"] == {
        "note": (
            "no channel artifact locators (channel_artifacts is empty for this "
            "package, e.g. because no anchor's delivery status is delivered/"
            "experimental); performance_package.sha256 is independent of "
            "--output-dir geometry for this recipe (Codex 7R P2 review #191, "
            "discussion_r3610250841)."
        )
    }


def test_build_package_provenance_repo_relative_package_path() -> None:
    """package_path 自体がリポジトリ内ファイルなら repo 相対パスをそのまま記録する
    （scratch レシピより優先）。"""
    in_repo_package = ROOT / "examples/arrangement/midnight_signal/composition_score.yaml"
    provenance = build_package_provenance(in_repo_package, "c" * 64)
    assert provenance == {
        "sha256": "c" * 64,
        "repo_relative_path": "examples/arrangement/midnight_signal/composition_score.yaml",
    }


def test_build_package_provenance_falls_back_to_note_without_recipe_inputs() -> None:
    """scratch build かつレシピ入力が一切与えられない場合は sha256 + note のみ
    （部分的な入力から不完全なレシピを捏造しない）。"""
    scratch_package = Path("/tmp/ar4_scratch_build/performance_package.json")
    provenance = build_package_provenance(scratch_package, "d" * 64)
    assert provenance["sha256"] == "d" * 64
    assert "build_recipe" not in provenance
    assert "note" in provenance
    assert str(scratch_package) not in provenance["note"]


def test_build_package_provenance_falls_back_to_note_with_partial_recipe_inputs() -> None:
    """レシピ入力が一部のみ与えられた場合も不完全なレシピを組み立てず note へ
    フォールバックする。"""
    scratch_package = Path("/tmp/ar4_scratch_build/performance_package.json")
    provenance = build_package_provenance(
        scratch_package,
        "e" * 64,
        score=DEFAULT_SCORE,
        identity_manifest=DEFAULT_IDENTITY_MANIFEST,
        # arrangement / capability_profile は未指定
    )
    assert "build_recipe" not in provenance
    assert "note" in provenance


def test_build_package_provenance_rejects_nonexistent_repo_relative_recipe_input() -> None:
    """レシピ入力の1つが repo 相対に解決できても実在しない場合（誤指定パス）は
    偽の `build_recipe` を出さず sha256-only フォールバックする（Codex 3R P2
    review #191, discussion_r3610153978）。"""
    scratch_package = Path("/tmp/ar4_scratch_build/performance_package.json")
    bogus_score = ROOT / "examples/arrangement/midnight_signal/does_not_exist.yaml"
    assert not bogus_score.is_file()  # 前提: このパスは本当に存在しない
    provenance = build_package_provenance(
        scratch_package,
        "f" * 64,
        score=bogus_score,
        identity_manifest=DEFAULT_IDENTITY_MANIFEST,
        arrangement=DEFAULT_ARRANGEMENT,
        capability_profile=DEFAULT_CAPABILITY_PROFILE,
    )
    assert "build_recipe" not in provenance
    assert "note" in provenance
    assert "does not exist" in provenance["note"]
    assert "score:" in provenance["note"]


def test_build_package_provenance_rejects_pin_mismatch() -> None:
    """レシピ入力が実在しても、package の pin と bytes が食い違う場合（改ざん/
    差し替え）は build_recipe を出さず sha256-only フォールバックする。"""
    scratch_package = Path("/tmp/ar4_scratch_build/performance_package.json")
    real_capability_sha256 = hashlib.sha256(
        DEFAULT_CAPABILITY_PROFILE.read_bytes()
    ).hexdigest()
    package_data = {
        "inputs": {
            # identity_manifest の pin をわざと不一致にする（tampered manifest 相当）
            "identity_manifest": {"sha256": "0" * 64},
            "capability_profile": {"sha256": real_capability_sha256},
        }
    }
    provenance = build_package_provenance(
        scratch_package,
        "1" + "a" * 63,
        package_data,
        score=DEFAULT_SCORE,
        identity_manifest=DEFAULT_IDENTITY_MANIFEST,
        arrangement=DEFAULT_ARRANGEMENT,
        capability_profile=DEFAULT_CAPABILITY_PROFILE,
    )
    assert "build_recipe" not in provenance
    assert "note" in provenance
    assert "identity_manifest:" in provenance["note"]
    assert "does not match package-pinned sha256" in provenance["note"]


def test_build_package_provenance_emits_recipe_when_pins_match() -> None:
    """package の pin（identity_manifest / capability_profile の raw bytes pin
    と、derived_score / preservation_contract / device_profile の幾何非依存
    合成 pin）が実ファイル/実計算と一致する場合（正しい既定入力）は、従来どおり
    build_recipe を記録する — pin 突合の追加が正当な入力を巻き添えにしないこと
    の回帰確認。"""
    scratch_package = Path("/tmp/ar4_scratch_build/performance_package.json")
    provenance = build_package_provenance(
        scratch_package,
        "2" + "b" * 63,
        _DEFAULT_RECIPE_PACKAGE_DATA,
        score=DEFAULT_SCORE,
        identity_manifest=DEFAULT_IDENTITY_MANIFEST,
        arrangement=DEFAULT_ARRANGEMENT,
        capability_profile=DEFAULT_CAPABILITY_PROFILE,
    )
    assert "note" not in provenance
    recipe = provenance["build_recipe"]
    assert recipe["inputs"] == {
        "score": _recipe_input_entry(DEFAULT_SCORE),
        "identity_manifest": _recipe_input_entry(DEFAULT_IDENTITY_MANIFEST),
        "arrangement": _recipe_input_entry(DEFAULT_ARRANGEMENT),
        "capability_profile": _recipe_input_entry(DEFAULT_CAPABILITY_PROFILE),
        "device_profile": _recipe_input_entry(ROOT / "config/device_profiles/musicgen.yaml"),
    }
    assert "output_geometry" in recipe
    assert "note" in recipe["output_geometry"]


def test_build_package_provenance_records_output_geometry_locator_when_channel_artifacts_present() -> (  # noqa: E501
    None
):
    """`channel_artifacts` に `artifact_base.locator` を持つエントリがあれば、
    `build_recipe.output_geometry` はその locator と幾何制約の説明を記録する
    （Codex 7R P2 review #191, discussion_r3610250841: `--output-dir
    <output-dir>` プレースホルダは自由な値ではなく、
    ``os.path.relpath(identity_manifest の親 dir, --output-dir)`` が
    `artifact_base.locator` に一致する geometry でなければ recompile 結果の
    `performance_package.sha256` が pin と一致しない、という欠陥への修正）。
    複数 distinct な locator がある場合は配列で記録することも併せて確認する。"""
    scratch_package = Path("/tmp/ar4_scratch_build/performance_package.json")
    package_data_with_locator = {
        **_DEFAULT_RECIPE_PACKAGE_DATA,
        "channel_artifacts": {
            "symbolic_melody": [
                {
                    "anchor_id": "melody",
                    "artifact_base": {
                        "kind": "identity_manifest_directory",
                        "relative_to": "performance_package_directory",
                        "locator": "../../identity_manifest_dir",
                    },
                }
            ]
        },
    }
    provenance = build_package_provenance(
        scratch_package,
        "3" + "c" * 63,
        package_data_with_locator,
        score=DEFAULT_SCORE,
        identity_manifest=DEFAULT_IDENTITY_MANIFEST,
        arrangement=DEFAULT_ARRANGEMENT,
        capability_profile=DEFAULT_CAPABILITY_PROFILE,
    )
    recipe = provenance["build_recipe"]
    assert recipe["output_geometry"]["artifact_base_locator"] == "../../identity_manifest_dir"
    assert "constraint" in recipe["output_geometry"]
    assert "artifact_base_locator" in recipe["output_geometry"]["constraint"]
    assert "performance_package.sha256 depends on this relative geometry" in (
        recipe["output_geometry"]["constraint"]
    )

    # 複数 distinct な locator がある場合は配列で記録する。
    package_data_with_multiple_locators = {
        **_DEFAULT_RECIPE_PACKAGE_DATA,
        "channel_artifacts": {
            "symbolic_melody": [
                {
                    "anchor_id": "melody",
                    "artifact_base": {
                        "kind": "identity_manifest_directory",
                        "relative_to": "performance_package_directory",
                        "locator": "../a",
                    },
                }
            ],
            "reference_audio": [
                {
                    "anchor_id": "harmony",
                    "artifact_base": {
                        "kind": "identity_manifest_directory",
                        "relative_to": "performance_package_directory",
                        "locator": "../b",
                    },
                }
            ],
        },
    }
    provenance_multi = build_package_provenance(
        scratch_package,
        "4" + "d" * 63,
        package_data_with_multiple_locators,
        score=DEFAULT_SCORE,
        identity_manifest=DEFAULT_IDENTITY_MANIFEST,
        arrangement=DEFAULT_ARRANGEMENT,
        capability_profile=DEFAULT_CAPABILITY_PROFILE,
    )
    assert provenance_multi["build_recipe"]["output_geometry"]["artifact_base_locator"] == [
        "../a",
        "../b",
    ]


def test_build_package_provenance_rejects_mistyped_score_with_existing_yaml() -> None:
    """`--score` が実在するが別内容の YAML（別 work の score）に誤指定された
    場合、existence-only では検出できないが derived_score の合成 pin 不一致で
    検出され、build_recipe を出さず sha256-only フォールバックする（Codex 4R P2
    review #191, discussion_r3610170990）。"""
    assert WRONG_SCORE.is_file()  # 前提: 実在する別 YAML であること
    scratch_package = Path("/tmp/ar4_scratch_build/performance_package.json")
    provenance = build_package_provenance(
        scratch_package,
        "3" + "c" * 63,
        _DEFAULT_RECIPE_PACKAGE_DATA,
        score=WRONG_SCORE,
        identity_manifest=DEFAULT_IDENTITY_MANIFEST,
        arrangement=DEFAULT_ARRANGEMENT,
        capability_profile=DEFAULT_CAPABILITY_PROFILE,
    )
    assert "build_recipe" not in provenance
    assert "note" in provenance
    assert "derived_score:" in provenance["note"]
    assert "does not match package-pinned inputs.derived_score.sha256" in provenance["note"]


def test_build_package_provenance_rejects_mistyped_arrangement_with_existing_yaml() -> None:
    """`--arrangement` が実在するが別 variant の arrangement spec に誤指定された
    場合、existence-only では検出できないが preservation_contract の合成 pin
    不一致で検出され、build_recipe を出さず sha256-only フォールバックする
    （Codex 4R P2 review #191, discussion_r3610170990）。"""
    assert WRONG_ARRANGEMENT.is_file()  # 前提: 実在する別 YAML であること
    assert WRONG_ARRANGEMENT != DEFAULT_ARRANGEMENT
    scratch_package = Path("/tmp/ar4_scratch_build/performance_package.json")
    provenance = build_package_provenance(
        scratch_package,
        "4" + "d" * 63,
        _DEFAULT_RECIPE_PACKAGE_DATA,
        score=DEFAULT_SCORE,
        identity_manifest=DEFAULT_IDENTITY_MANIFEST,
        arrangement=WRONG_ARRANGEMENT,
        capability_profile=DEFAULT_CAPABILITY_PROFILE,
    )
    assert "build_recipe" not in provenance
    assert "note" in provenance
    assert "preservation_contract:" in provenance["note"]
    assert (
        "does not match package-pinned inputs.preservation_contract.sha256"
        in provenance["note"]
    )


def test_build_package_provenance_rejects_device_profile_pin_mismatch() -> None:
    """`inputs.device_profile.sha256` が実際に auto-load される
    `config/device_profiles/musicgen.yaml` の pin と食い違う場合（device profile
    差し替え/改ざんを模す）、旧 4 入力の pin が全て一致していても build_recipe
    を出さず sha256-only フォールバックする（Codex 5R P2 review #191,
    discussion_r3610193512: device profile が recipe 検証から漏れていた
    "false reproduction recipe" 欠陥への修正）。"""
    scratch_package = Path("/tmp/ar4_scratch_build/performance_package.json")
    tampered_package_data = {
        "inputs": {
            **_DEFAULT_RECIPE_PACKAGE_DATA["inputs"],
            "device_profile": {
                "generator": "musicgen",
                "status": "loaded",
                "sha256": "0" * 64,
            },
        }
    }
    provenance = build_package_provenance(
        scratch_package,
        "5" + "e" * 63,
        tampered_package_data,
        score=DEFAULT_SCORE,
        identity_manifest=DEFAULT_IDENTITY_MANIFEST,
        arrangement=DEFAULT_ARRANGEMENT,
        capability_profile=DEFAULT_CAPABILITY_PROFILE,
    )
    assert "build_recipe" not in provenance
    assert "note" in provenance
    assert "device_profile:" in provenance["note"]
    assert (
        "does not match package-pinned inputs.device_profile" in provenance["note"]
    )


def test_build_package_provenance_missing_device_profile_pin_falls_back() -> None:
    """package JSON に `inputs.device_profile` 自体が存在しない（旧世代の package
    または欠落）場合も、レシピの正当性を主張できないため build_recipe を出さず
    フォールバックする（derived_score/preservation_contract と同型の規律）。"""
    scratch_package = Path("/tmp/ar4_scratch_build/performance_package.json")
    package_data_without_device_profile = {
        "inputs": {
            key: value
            for key, value in _DEFAULT_RECIPE_PACKAGE_DATA["inputs"].items()
            if key != "device_profile"
        }
    }
    provenance = build_package_provenance(
        scratch_package,
        "6" + "f" * 63,
        package_data_without_device_profile,
        score=DEFAULT_SCORE,
        identity_manifest=DEFAULT_IDENTITY_MANIFEST,
        arrangement=DEFAULT_ARRANGEMENT,
        capability_profile=DEFAULT_CAPABILITY_PROFILE,
    )
    assert "build_recipe" not in provenance
    assert "note" in provenance
    assert "device_profile:" in provenance["note"]
    assert "no readable inputs.device_profile entry" in provenance["note"]


def test_recompute_device_profile_pin_not_found_is_legitimate(tmp_path: Path) -> None:
    """generator に対応する `config/device_profiles/<generator>.yaml` が存在しない
    場合、`_recompute_device_profile_pin` は例外にせず ``status="not_found"`` /
    ``sha256=None`` を返す（`DeviceProfileInput` 上正当な状態 —
    `src/svp_rpe/arrange/package.py` の `_device_profile_input` 参照）。device
    profile の pin 有無が package の必須フィールドかどうかを package.py の実装で
    確認した上での設計判断（Design Memo point 2）を固定するテスト。"""
    from scripts.collect_ar4_observation import _recompute_device_profile_pin

    unprofiled_backend = "unprofiled_test_backend"
    assert not (
        ROOT / "config" / "device_profiles" / f"{unprofiled_backend}.yaml"
    ).is_file()  # 前提: このジェネレータ名には device profile が存在しない

    arrangement_text = DEFAULT_ARRANGEMENT.read_text(encoding="utf-8").replace(
        'target_backend: "musicgen"', f'target_backend: "{unprofiled_backend}"'
    )
    assert f'target_backend: "{unprofiled_backend}"' in arrangement_text  # 置換が効いたこと

    tmp_arrangement = tmp_path / "unprofiled.arrangement.yaml"
    tmp_arrangement.write_text(arrangement_text, encoding="utf-8")

    result = _recompute_device_profile_pin(
        DEFAULT_SCORE.read_bytes(), tmp_arrangement.read_bytes()
    )
    assert result == (unprofiled_backend, "not_found", None)


def test_build_takes_manifest_matches_committed_fixture_with_default_recipe_inputs() -> None:
    """修正後の生成器（既定のレシピ入力 + 実 package pin）が、committed
    `ar4_takes_manifest.json` と構造的に一致する manifest を組み立てることを
    確認する — 完了条件: 生成器の出力形式と fixture の直接一致。

    `package_data` には `_DEFAULT_RECIPE_PACKAGE_DATA`（DEFAULT_* 入力から
    独立に再計算した実 pin）を渡す — Codex 4R P2 review #191 の修正で
    `package_data` なしでは幾何非依存 pin を突合できず build_recipe を
    emit しなくなったため、committed fixture の `build_recipe` を再現するには
    実 pin が必要（design memo point 4）。

    また、committed fixture の `build_recipe.inputs.<name>.sha256` 各値が
    実ファイルの sha256 と一致することも独立に確認する（Codex 6R P2 review
    #191: per-input sha256 焼き込みの追加で、fixture 自体が捏造されていない
    ことの回帰確認）。
    """
    fixture = _load_fixture_manifest()
    result = {
        "prompt": fixture["prompt"],
        "model_id": fixture["model_id"],
        "model_revision": fixture["model_revision"],
        "duration_seconds": fixture["duration_seconds"],
        "guidance_scale": fixture["guidance_scale"],
        "samples": fixture["samples"],
    }
    scratch_package = Path("/tmp/ar4_takes/performance_package.json")
    manifest = build_takes_manifest(
        result,
        fixture_id=fixture["fixture_id"],
        package_path=scratch_package,
        package_sha256=fixture["performance_package"]["sha256"],
        package_data=_DEFAULT_RECIPE_PACKAGE_DATA,
        score=DEFAULT_SCORE,
        identity_manifest=DEFAULT_IDENTITY_MANIFEST,
        arrangement=DEFAULT_ARRANGEMENT,
        capability_profile=DEFAULT_CAPABILITY_PROFILE,
    )
    assert manifest == fixture

    # fixture の build_recipe.inputs.<name>.sha256 が実ファイルの sha256 と
    # 一致すること（捏造禁止の直接検証。上の `manifest == fixture` は生成器の
    # 出力とfixtureの一致のみを見るため、fixture 自体の正しさは別途確認する）。
    fixture_inputs = fixture["performance_package"]["build_recipe"]["inputs"]
    for name, entry in fixture_inputs.items():
        input_path = ROOT / entry["path"]
        assert input_path.is_file()
        assert entry["sha256"] == hashlib.sha256(input_path.read_bytes()).hexdigest(), (
            f"fixture build_recipe.inputs.{name}.sha256 does not match the real "
            f"file sha256 at {entry['path']}"
        )


# ---------------------------------------------------------------------------
# Codex 8R P2 review #191: `--timestamps-out` が `--manifest-out` と同一パス
# の場合、manifest 書き込み後に timestamps 書き込みが silent に上書きし、両方
# 書けたと報告しながら takes manifest が失われる欠陥への回帰テスト。生成本体
# (torch 推論) を一切呼ばずに検証できるよう、`_generate_output_paths` /
# `_reject_generate_output_collision` を単体テストし、`_cmd_generate` レベル
# (`main(["generate", ...])`) では collision があれば `generate_ar4_takes` に
# 到達する前に exit 1 し、何も書かれないことを確認する。
# ---------------------------------------------------------------------------


def test_generate_output_paths_lists_output_dir_manifest_timestamps_and_take_wavs(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "out"
    manifest_out = tmp_path / "manifest.json"
    timestamps_out = tmp_path / "timestamps.yaml"

    paths = _generate_output_paths(output_dir, manifest_out, timestamps_out, [0, 1])

    assert paths == [
        output_dir.resolve(),
        manifest_out.resolve(),
        timestamps_out.resolve(),
        (output_dir / "take0.wav").resolve(),
        (output_dir / "take1.wav").resolve(),
    ]


def test_generate_output_paths_omits_timestamps_when_not_requested(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    manifest_out = tmp_path / "manifest.json"

    paths = _generate_output_paths(output_dir, manifest_out, None, [0])

    assert paths == [
        output_dir.resolve(),
        manifest_out.resolve(),
        (output_dir / "take0.wav").resolve(),
    ]


def test_reject_generate_output_collision_rejects_manifest_timestamps_same_path(
    tmp_path: Path,
) -> None:
    same_path = tmp_path / "manifest.json"
    output_paths = _generate_output_paths(
        tmp_path / "out", same_path, same_path, [0, 1]
    )

    with pytest.raises(ValueError, match="collide"):
        _reject_generate_output_collision(output_paths, input_paths=[])


def test_reject_generate_output_collision_rejects_output_matching_input(
    tmp_path: Path,
) -> None:
    package_path = tmp_path / "performance_package.json"
    package_path.write_text("{}", encoding="utf-8")
    output_paths = _generate_output_paths(
        tmp_path / "out", package_path, None, [0]
    )

    with pytest.raises(ValueError, match="collides with input path"):
        _reject_generate_output_collision(output_paths, input_paths=[package_path.resolve()])


def test_reject_generate_output_collision_allows_disjoint_paths(tmp_path: Path) -> None:
    output_paths = _generate_output_paths(
        tmp_path / "out", tmp_path / "manifest.json", tmp_path / "timestamps.yaml", [0, 1]
    )
    input_paths = [DEFAULT_SCORE.resolve(), DEFAULT_ARRANGEMENT.resolve()]

    _reject_generate_output_collision(output_paths, input_paths)  # must not raise


def _write_minimal_package(path: Path) -> None:
    path.write_text(json.dumps({"prompt": {"text": "a minimal test prompt"}}), encoding="utf-8")


def test_cmd_generate_rejects_manifest_timestamps_collision_before_writing(
    tmp_path: Path, capsys: Any
) -> None:
    """`--manifest-out` と `--timestamps-out` が同一パスなら、`generate` は
    生成 (torch 推論) にも manifest 書き込みにも到達せず exit 1 する（Codex 8R
    P2 review #191 の回帰確認: 生成器が両方書けたと報告しながら実際は
    timestamps 書き込みが manifest を silent に上書きしていた欠陥）。
    """
    package_path = tmp_path / "performance_package.json"
    _write_minimal_package(package_path)
    output_dir = tmp_path / "out"
    same_path = tmp_path / "ar4_takes_manifest.json"

    exit_code = main(
        [
            "generate",
            "--package",
            str(package_path),
            "--output-dir",
            str(output_dir),
            "--manifest-out",
            str(same_path),
            "--timestamps-out",
            str(same_path),
        ]
    )

    assert exit_code == 1
    assert not same_path.exists()
    assert not output_dir.exists()
    captured = capsys.readouterr()
    assert "collide" in captured.err


def test_cmd_generate_rejects_output_pointing_at_input_package(
    tmp_path: Path, capsys: Any
) -> None:
    """出力パス (`--manifest-out`) が provenance 入力 (`--package`) を指す場合
    も、生成前に exit 1 し、入力ファイルは上書きされない（Codex 8R P2 review
    #191: manifest/timestamps だけでなく package のような入力の上書き防止に
    も同型のガードを適用する）。
    """
    package_path = tmp_path / "performance_package.json"
    _write_minimal_package(package_path)
    original_bytes = package_path.read_bytes()
    output_dir = tmp_path / "out"

    exit_code = main(
        [
            "generate",
            "--package",
            str(package_path),
            "--output-dir",
            str(output_dir),
            "--manifest-out",
            str(package_path),
        ]
    )

    assert exit_code == 1
    assert package_path.read_bytes() == original_bytes
    assert not output_dir.exists()
    captured = capsys.readouterr()
    assert "collides with input path" in captured.err
