"""`recast/backend.py` + `recast/backends/*` テスト（PR3）。"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from svp_rpe.recast import RecastError, load_recast_project
from svp_rpe.recast.backend import (
    RecastBackendUnavailable,
    build_recast_run_context,
    load_backend_capability_profile,
    resolve_invoker,
    run_context_from_plan_artifacts,
)
from svp_rpe.recast.backends.deterministic import DeterministicInvoker
from svp_rpe.recast.backends.manual import ManualInvoker
from svp_rpe.recast.backends.musicgen import MusicgenInvoker
from svp_rpe.recast.plan import build_recast_plan_artifacts

DEMO_PROJECT = Path("examples/recast/demo_project")
EXPECTED_ORDERS = DEMO_PROJECT / "expected" / "orders" / "edm@suno"


def _copy_demo_project(tmp_path: Path) -> Path:
    """`tests/test_recast_plan.py` と同じ working-copy 構築（`expected/` は除外）。"""
    dest = tmp_path / "demo_project"
    dest.mkdir()
    shutil.copy(DEMO_PROJECT / "project.yaml", dest / "project.yaml")
    shutil.copy(DEMO_PROJECT / "composition_score.yaml", dest / "composition_score.yaml")
    shutil.copy(DEMO_PROJECT / "identity.yaml", dest / "identity.yaml")
    shutil.copytree(DEMO_PROJECT / "identity", dest / "identity")
    shutil.copytree(DEMO_PROJECT / "arrangements", dest / "arrangements")
    return dest / "project.yaml"


def _add_target_backend_variant(project_path: Path, *, variant_name: str, target_backend: str) -> None:
    """既存 demo project の working copy に、`rendering.target_backend` を
    `target_backend` へ override する variant を追加する（`deterministic`/
    `musicgen` local backend の検証に共通で使う）。

    実装ノート（PR3 逸脱事項・詳細は project.yaml のコメント参照）: demo の
    composition_score.yaml は `rendering.target_backend: "external"` を宣言して
    おり、`package.py` の generator 一致検査（`profile.generator ==
    resolve_backend_descriptor(target_backend).profile_key`、strict 無関係に
    無条件）により、既存 "edm" variant に対しては capability_profile 側 generator
    が "suno" 以外の backend（deterministic/musicgen）は常に blocked_capability
    に到達する（target_backend が score-level の静的宣言であり backend 選択と
    独立、という既存アーキテクチャの制約）。local backend を実際に
    compiled/verified まで機能させて検証するため、この専用 variant だけ
    target_backend を上書きする。
    """
    project_dir = project_path.parent
    arrangements_dir = project_dir / "arrangements"
    edm_arrangement = (arrangements_dir / "edm.yaml").read_text(encoding="utf-8")
    overridden = edm_arrangement.replace(
        "target:\n  semantic:",
        f'target:\n  rendering:\n    target_backend: "{target_backend}"\n  semantic:',
        1,
    )
    assert overridden != edm_arrangement  # sanity: replacement matched
    overridden = overridden.replace(
        "preservation:\n  score_fields:\n",
        "preservation:\n  score_fields:\n    rendering.target_backend: free\n",
        1,
    )
    (arrangements_dir / f"{variant_name}.yaml").write_text(overridden, encoding="utf-8")

    project_text = project_path.read_text(encoding="utf-8")
    updated = project_text.replace(
        "variants:\n  edm:\n    arrangement: arrangements/edm.yaml\n",
        "variants:\n  edm:\n    arrangement: arrangements/edm.yaml\n"
        f"  {variant_name}:\n    arrangement: arrangements/{variant_name}.yaml\n",
        1,
    )
    assert f"{variant_name}:" in updated  # sanity: replacement matched
    project_path.write_text(updated, encoding="utf-8")


def _prepare_manual(project_path: Path):
    loaded = load_recast_project(project_path)
    ctx = build_recast_run_context(loaded, variant="edm", backend="suno")
    invoker = resolve_invoker(ctx.backend_ref, ctx.profile)
    assert isinstance(invoker, ManualInvoker)
    prepared = invoker.prepare(ctx)
    return loaded, invoker, prepared


# --- manual: order file content -----------------------------------------------


def test_manual_order_files_match_committed_snapshot(tmp_path: Path) -> None:
    """全 6 ファイルを byte-pin する — `prompt.json`/`expected_artifacts.json` の
    `content_digest`/`package_sha256` も含む。以前はこれらを正規化して除外していた
    （`plan.py` が `tempfile.TemporaryDirectory()` を package の一時置き場に使い、
    `artifact_base.locator` が呼び出し環境のテンポラリパス深さに依存し
    package_sha256/content_digest が非決定だったため）が、Codex P2 review
    （PR3 #208 指摘 3）で `plan.py` が `<builds_root>/packages/<variant>@<backend>/`
    へ package/report を永続公開するよう修正され、`artifact_base.locator` は
    project_dir 配下の固定深さのみに依存する checkout-stable な値になった —
    正規化 workaround は不要になったため撤去した。"""
    project_path = _copy_demo_project(tmp_path)
    _loaded, _invoker, prepared = _prepare_manual(project_path)

    for name in (
        "prompt.json",
        "lyrics.txt",
        "section_tags.txt",
        "order_sheet.md",
        "expected_artifacts.json",
        "next_command.txt",
    ):
        actual = (prepared.order_dir / name).read_text(encoding="utf-8")
        expected = (EXPECTED_ORDERS / name).read_text(encoding="utf-8")
        assert actual == expected, f"order file drift: {name}"


_TIMESTAMP_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def test_manual_order_files_have_no_timestamps_or_absolute_paths(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    _loaded, _invoker, prepared = _prepare_manual(project_path)

    for name in (
        "prompt.json",
        "lyrics.txt",
        "section_tags.txt",
        "order_sheet.md",
        "expected_artifacts.json",
        "next_command.txt",
    ):
        content = (prepared.order_dir / name).read_text(encoding="utf-8")
        assert not _TIMESTAMP_PATTERN.search(content), f"{name} leaks a timestamp"
        assert str(tmp_path) not in content, f"{name} leaks an absolute path"
        assert str(prepared.order_dir) not in content, f"{name} leaks an absolute path"


def test_manual_order_files_are_byte_identical_across_reruns(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    _loaded, _invoker, prepared_first = _prepare_manual(project_path)
    first = {
        name: (prepared_first.order_dir / name).read_bytes()
        for name in ("prompt.json", "order_sheet.md", "next_command.txt")
    }

    _loaded2, _invoker2, prepared_second = _prepare_manual(project_path)
    for name, expected_bytes in first.items():
        assert (prepared_second.order_dir / name).read_bytes() == expected_bytes


# --- manual: cover mode branch --------------------------------------------------


def test_manual_cover_mode_order_sheet_references_identity_source(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    arrangement_path = project_path.parent / "arrangements" / "edm.yaml"
    original = arrangement_path.read_text(encoding="utf-8")
    mutated = original.replace(
        "  physical:\n    bpm: 132\n    brightness: \"bright\"\n",
        "  physical:\n    bpm: 132\n    brightness: \"bright\"\n"
        '    time_signature: "3/4"\n',
        1,
    )
    assert mutated != original  # sanity
    mutated = mutated.replace(
        "preservation:\n  score_fields:\n",
        "preservation:\n  score_fields:\n    physical.time_signature: free\n",
        1,
    )
    arrangement_path.write_text(mutated, encoding="utf-8")

    project_text = project_path.read_text(encoding="utf-8")
    project_text = project_text.replace("invocation_mode: prompt_only", "invocation_mode: cover")
    project_path.write_text(project_text, encoding="utf-8")

    loaded = load_recast_project(project_path)
    ctx = build_recast_run_context(loaded, variant="edm", backend="suno")
    assert ctx.backend_ref.invocation_mode == "cover"
    invoker = resolve_invoker(ctx.backend_ref, ctx.profile)
    prepared = invoker.prepare(ctx)

    order_sheet = (prepared.order_dir / "order_sheet.md").read_text(encoding="utf-8")
    assert "cover" in order_sheet
    assert prepared.identity_source_locator in order_sheet
    assert prepared.identity_source_sha256 in order_sheet
    assert "physical.time_signature" in order_sheet
    assert "unsupported" in order_sheet  # cover の time_signature override は届かない実測


# --- manual: invoke/collect contract --------------------------------------------


def test_manual_invoke_raises_recast_error(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    _loaded, invoker, prepared = _prepare_manual(project_path)

    with pytest.raises(RecastError):
        invoker.invoke(prepared)


def test_manual_collect_rejects_missing_file(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    _loaded, invoker, prepared = _prepare_manual(project_path)

    with pytest.raises(RecastError):
        invoker.collect(prepared, tmp_path / "does-not-exist.wav")


def test_manual_collect_rejects_unaccepted_extension(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    _loaded, invoker, prepared = _prepare_manual(project_path)
    bogus = tmp_path / "take.flac"
    bogus.write_bytes(b"not really audio")

    with pytest.raises(RecastError):
        invoker.collect(prepared, bogus)


def test_manual_collect_writes_take_and_take_json(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    _loaded, invoker, prepared = _prepare_manual(project_path)
    supplied = tmp_path / "external_take.wav"
    supplied.write_bytes(b"RIFF....WAVEfake-audio-bytes")

    take = invoker.collect(prepared, supplied)

    assert take.source == "manual"
    assert take.audio_path == prepared.takes_dir / "take-01.wav"
    assert take.audio_path.read_bytes() == supplied.read_bytes()

    import hashlib

    assert take.sha256 == hashlib.sha256(supplied.read_bytes()).hexdigest()

    take_json = json.loads((prepared.takes_dir / "take.json").read_text(encoding="utf-8"))
    assert take_json["sha256"] == take.sha256
    assert take_json["source"] == "manual"
    assert take_json["backend_name"] == "suno"


def test_manual_collect_take_json_failure_leaves_no_orphaned_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 review（PR3 #208 指摘 1）: `take-01.wav` を先に最終名へ publish
    してから `take.json` を書くと、後者が失敗したときに provenance の無い音声
    だけが `takes_dir` に残ってしまう。`atomic_publish_bytes_bundle` は両方を
    staging してから publish するため、`take.json` 側の最終 rename を失敗
    注入しても `take-01.wav` が残らないことを検証する。"""
    import os

    project_path = _copy_demo_project(tmp_path)
    _loaded, invoker, prepared = _prepare_manual(project_path)
    supplied = tmp_path / "external_take.wav"
    supplied.write_bytes(b"RIFF....WAVEfake-audio-bytes")

    real_replace = os.replace

    def flaky_replace(src: object, dst: object) -> None:
        if str(dst).endswith("take.json"):
            raise OSError("simulated disk full while publishing take.json")
        real_replace(src, dst)

    monkeypatch.setattr("os.replace", flaky_replace)

    with pytest.raises(OSError):
        invoker.collect(prepared, supplied)

    assert not (prepared.takes_dir / "take-01.wav").exists()
    assert not (prepared.takes_dir / "take.json").exists()


# --- manual: orders publish collision guard --------------------------------------


def test_manual_orders_publish_rejects_mode_overrides_colliding_with_output(
    tmp_path: Path,
) -> None:
    """Codex P2 review（PR3 #208 指摘 2）: 従来の衝突ガードは
    project/score/identity_manifest/arrangement のみが対象で、project ローカルの
    capability_profile / mode_overrides は対象外だった。ここでは
    `backends.suno.mode_overrides` を orders 出力パス（`prompt.json`）の位置に
    直接置き、`prepare()` が publish 前に fail-closed で拒否する（かつ既存内容を
    上書きしない）ことを検証する。"""
    project_path = _copy_demo_project(tmp_path)
    project_dir = project_path.parent

    colliding_path = project_dir / "builds" / "orders" / "edm@suno" / "prompt.json"
    colliding_path.parent.mkdir(parents=True, exist_ok=True)
    original_mode_overrides = (
        Path("config/mode_overrides/suno.yaml").read_bytes()
    )
    colliding_path.write_bytes(original_mode_overrides)

    project_text = project_path.read_text(encoding="utf-8")
    updated = project_text.replace(
        'mode_overrides: "suno"',
        'mode_overrides: "builds/orders/edm@suno/prompt.json"',
        1,
    )
    assert updated != project_text  # sanity
    project_path.write_text(updated, encoding="utf-8")

    loaded = load_recast_project(project_path)
    ctx = build_recast_run_context(loaded, variant="edm", backend="suno")
    invoker = resolve_invoker(ctx.backend_ref, ctx.profile)

    with pytest.raises(ValueError):
        invoker.prepare(ctx)

    # fail-closed: 衝突先の既存内容（mode_overrides YAML）が上書きされていない。
    assert colliding_path.read_bytes() == original_mode_overrides


# --- deterministic: invoke + determinism ----------------------------------------


@pytest.mark.slow
def test_deterministic_invoke_writes_take(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    _add_target_backend_variant(project_path, variant_name="edm_deterministic", target_backend="deterministic")
    loaded = load_recast_project(project_path)
    ctx = build_recast_run_context(loaded, variant="edm_deterministic", backend="deterministic")
    assert ctx.plan_result.plan.state_reached in ("compiled", "verified")

    invoker = resolve_invoker(ctx.backend_ref, ctx.profile)
    assert isinstance(invoker, DeterministicInvoker)
    prepared = invoker.prepare(ctx)

    take = invoker.invoke(prepared)

    assert take.source == "local"
    assert take.audio_path == prepared.takes_dir / "take-01.wav"
    assert take.audio_path.is_file()
    take_json = json.loads((prepared.takes_dir / "take.json").read_text(encoding="utf-8"))
    assert take_json["sha256"] == take.sha256
    assert take_json["source"] == "local"


@pytest.mark.slow
def test_deterministic_invoke_take_json_failure_leaves_no_orphaned_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 review（PR3 #208 指摘 1）: `DeterministicInvoker.invoke` も
    manual collect と同じ atomic bundle publish を使う — `take.json` の最終
    rename を失敗注入しても `take-01.wav` が残らないことを検証する。"""
    import os

    project_path = _copy_demo_project(tmp_path)
    _add_target_backend_variant(
        project_path, variant_name="edm_deterministic", target_backend="deterministic"
    )
    loaded = load_recast_project(project_path)
    ctx = build_recast_run_context(loaded, variant="edm_deterministic", backend="deterministic")
    invoker = resolve_invoker(ctx.backend_ref, ctx.profile)
    prepared = invoker.prepare(ctx)

    real_replace = os.replace

    def flaky_replace(src: object, dst: object) -> None:
        if str(dst).endswith("take.json"):
            raise OSError("simulated disk full while publishing take.json")
        real_replace(src, dst)

    monkeypatch.setattr("os.replace", flaky_replace)

    with pytest.raises(OSError):
        invoker.invoke(prepared)

    assert not (prepared.takes_dir / "take-01.wav").exists()
    assert not (prepared.takes_dir / "take.json").exists()


@pytest.mark.slow
def test_deterministic_invoke_is_byte_identical_across_runs(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    _add_target_backend_variant(project_path, variant_name="edm_deterministic", target_backend="deterministic")
    loaded = load_recast_project(project_path)
    ctx = build_recast_run_context(loaded, variant="edm_deterministic", backend="deterministic")
    invoker = resolve_invoker(ctx.backend_ref, ctx.profile)
    prepared = invoker.prepare(ctx)

    take_first = invoker.invoke(prepared)
    first_bytes = take_first.audio_path.read_bytes()
    take_second = invoker.invoke(prepared)
    second_bytes = take_second.audio_path.read_bytes()

    assert take_first.sha256 == take_second.sha256
    assert first_bytes == second_bytes


def test_deterministic_collect_raises_recast_error(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    _add_target_backend_variant(project_path, variant_name="edm_deterministic", target_backend="deterministic")
    loaded = load_recast_project(project_path)
    ctx = build_recast_run_context(loaded, variant="edm_deterministic", backend="deterministic")
    invoker = resolve_invoker(ctx.backend_ref, ctx.profile)
    prepared = invoker.prepare(ctx)

    with pytest.raises(RecastError):
        invoker.collect(prepared, tmp_path / "whatever.wav")


# --- musicgen: stub -------------------------------------------------------------


def test_musicgen_invoke_raises_unavailable(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    _add_target_backend_variant(project_path, variant_name="edm_musicgen", target_backend="musicgen")
    project_text = project_path.read_text(encoding="utf-8")
    project_text = project_text.replace(
        "  deterministic:\n    capability_profile: \"deterministic\"\n"
        "    invocation: local\n    invocation_mode: prompt_only\n",
        "  deterministic:\n    capability_profile: \"deterministic\"\n"
        "    invocation: local\n    invocation_mode: prompt_only\n"
        "  musicgen:\n    capability_profile: \"musicgen\"\n"
        "    invocation: local\n    invocation_mode: prompt_only\n",
        1,
    )
    assert "musicgen:" in project_text  # sanity
    project_path.write_text(project_text, encoding="utf-8")

    loaded = load_recast_project(project_path)
    ctx = build_recast_run_context(loaded, variant="edm_musicgen", backend="musicgen")
    invoker = resolve_invoker(ctx.backend_ref, ctx.profile)
    assert isinstance(invoker, MusicgenInvoker)
    prepared = invoker.prepare(ctx)

    with pytest.raises(RecastBackendUnavailable):
        invoker.invoke(prepared)

    with pytest.raises(RecastError):
        invoker.collect(prepared, tmp_path / "whatever.wav")


@pytest.mark.skip(reason="musicgen backend is stubbed in PR3; no real inference wiring yet")
def test_musicgen_real_inference_not_yet_wired() -> None:
    """PR3 時点では `musicgen` backend に実推論の配線がないためスキップする
    プレースホルダ（`docs/musicgen_backend.md` の runbook 完了後の将来 PR 用）。"""


# --- resolve_invoker / run_context_from_plan_artifacts errors -------------------


def test_resolve_invoker_unknown_local_generator_raises(tmp_path: Path) -> None:
    from svp_rpe.recast.models import BackendRef

    backend_ref = BackendRef(
        capability_profile="suno", invocation="local", invocation_mode="prompt_only"
    )
    from svp_rpe.arrange.capabilities import InputCapabilityProfile

    profile = InputCapabilityProfile.model_validate(
        {
            "schema_version": "input-capability/0.2",
            "generator": "unknown-generator",
            "generator_variant": "standard",
            "profile_version": "2026-07",
            "input_channels": {},
        }
    )
    with pytest.raises(RecastError):
        resolve_invoker(backend_ref, profile)


def test_run_context_from_plan_artifacts_raises_when_not_compiled(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    score_path = project_path.parent / "composition_score.yaml"
    score_path.write_text(
        score_path.read_text(encoding="utf-8").replace(
            'core: "introspective night drive"',
            'core: "TODO(transcribe): author input required"',
        ),
        encoding="utf-8",
    )
    loaded = load_recast_project(project_path)
    artifacts = build_recast_plan_artifacts(loaded, variant="edm", backend="suno")
    assert artifacts.result.plan.state_reached == "blocked_authoring"
    profile = load_backend_capability_profile(loaded, "suno")

    with pytest.raises(RecastError):
        run_context_from_plan_artifacts(
            loaded, variant="edm", backend="suno", artifacts=artifacts, profile=profile
        )
