"""`svprpe recast run` CLI テスト（PR3）+ E2E 受け入れ条件（CliRunner + API 併用）。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from svp_rpe.cli import app
from svp_rpe.recast import load_recast_project
from svp_rpe.recast.backend import resolve_invoker, run_context_from_plan_artifacts
from svp_rpe.recast.plan import build_recast_plan_artifacts
from svp_rpe.recast.state import load_recast_state

DEMO_PROJECT = Path("examples/recast/demo_project")

runner = CliRunner()


def _copy_demo_project(tmp_path: Path) -> Path:
    dest = tmp_path / "demo_project"
    dest.mkdir()
    shutil.copy(DEMO_PROJECT / "project.yaml", dest / "project.yaml")
    shutil.copy(DEMO_PROJECT / "composition_score.yaml", dest / "composition_score.yaml")
    shutil.copy(DEMO_PROJECT / "identity.yaml", dest / "identity.yaml")
    shutil.copytree(DEMO_PROJECT / "identity", dest / "identity")
    shutil.copytree(DEMO_PROJECT / "arrangements", dest / "arrangements")
    return dest / "project.yaml"


def _add_deterministic_variant(project_path: Path) -> None:
    """`tests/test_recast_backend.py:_add_target_backend_variant` と同じ手法
    （target_backend override 用の専用 variant を追加する。理由は同モジュールの
    docstring / `examples/recast/demo_project/project.yaml` のコメント参照）。"""
    project_dir = project_path.parent
    arrangements_dir = project_dir / "arrangements"
    edm_arrangement = (arrangements_dir / "edm.yaml").read_text(encoding="utf-8")
    overridden = edm_arrangement.replace(
        "target:\n  semantic:",
        'target:\n  rendering:\n    target_backend: "deterministic"\n  semantic:',
        1,
    )
    assert overridden != edm_arrangement  # sanity
    overridden = overridden.replace(
        "preservation:\n  score_fields:\n",
        "preservation:\n  score_fields:\n    rendering.target_backend: free\n",
        1,
    )
    (arrangements_dir / "edm_deterministic.yaml").write_text(overridden, encoding="utf-8")

    project_text = project_path.read_text(encoding="utf-8")
    updated = project_text.replace(
        "variants:\n  edm:\n    arrangement: arrangements/edm.yaml\n",
        "variants:\n  edm:\n    arrangement: arrangements/edm.yaml\n"
        "  edm_deterministic:\n    arrangement: arrangements/edm_deterministic.yaml\n",
        1,
    )
    assert "edm_deterministic:" in updated  # sanity
    project_path.write_text(updated, encoding="utf-8")


# --- recast run: manual (order publication) -------------------------------------


def test_recast_run_manual_publishes_orders_and_records_awaiting_generation(
    tmp_path: Path,
) -> None:
    project_path = _copy_demo_project(tmp_path)

    result = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )

    assert result.exit_code == 0, result.output
    order_dir = project_path.parent / "builds" / "orders" / "edm@suno"
    for name in (
        "prompt.json",
        "lyrics.txt",
        "section_tags.txt",
        "order_sheet.md",
        "expected_artifacts.json",
        "next_command.txt",
    ):
        assert (order_dir / name).is_file(), name

    state_file = load_recast_state(project_path.parent)
    assert state_file.runs["edm@suno"].state == "awaiting_generation"


def test_recast_run_manual_is_idempotent(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)

    first = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    second = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    order_dir = project_path.parent / "builds" / "orders" / "edm@suno"
    assert (order_dir / "prompt.json").is_file()


# --- recast run: blocked (not compiled) -----------------------------------------


def test_recast_run_exits_nonzero_when_backend_generator_mismatched(tmp_path: Path) -> None:
    """demo fixture の `edm` variant は `rendering.target_backend: "external"` を
    宣言しており、capability_profile 側 generator が "suno" 以外の backend
    （ここでは "deterministic"）と組み合わせると常に blocked_capability に到達する
    （`examples/recast/demo_project/project.yaml` のコメント参照）。`recast run`
    はこれを診断表示した上で exit 1 する。"""
    project_path = _copy_demo_project(tmp_path)

    result = runner.invoke(
        app,
        ["recast", "run", str(project_path), "--variant", "edm", "--backend", "deterministic"],
    )

    assert result.exit_code == 1
    assert "blocked_capability" in result.output


def test_recast_run_unknown_variant_exits_nonzero(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)

    result = runner.invoke(
        app,
        [
            "recast",
            "run",
            str(project_path),
            "--variant",
            "does-not-exist",
            "--backend",
            "suno",
        ],
    )

    assert result.exit_code == 1


def test_recast_help_lists_run() -> None:
    result = runner.invoke(app, ["recast", "--help"])

    assert result.exit_code == 0
    assert "run" in result.output


# --- recast ingest ---------------------------------------------------------------


def test_recast_ingest_transitions_awaiting_generation_to_generated(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    run_result = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert run_result.exit_code == 0, run_result.output

    # next_command.txt が実在コマンドを案内していることの機械 assert（Codex P2
    # review round 2 指摘 5）: 文字列冒頭が `svprpe recast ingest ` であること
    # の直接検証は tests/test_recast_backend.py 側（実行までの機械 assert）で
    # 行う。ここでは案内された通りの --audio 相対パスへ音声を置いてから
    # ingest する実運用手順そのものを検証する。
    order_dir = project_path.parent / "builds" / "orders" / "edm@suno"
    next_command = (order_dir / "next_command.txt").read_text(encoding="utf-8").strip()
    assert next_command.startswith("svprpe recast ingest ")

    takes_dir = project_path.parent / "builds" / "takes" / "edm@suno"
    takes_dir.mkdir(parents=True, exist_ok=True)
    audio_path = takes_dir / "take-01.wav"
    audio_path.write_bytes(b"RIFF....WAVEfake-audio-bytes")

    ingest_result = runner.invoke(
        app,
        [
            "recast",
            "ingest",
            str(project_path),
            "--variant",
            "edm",
            "--backend",
            "suno",
            "--audio",
            str(audio_path),
        ],
    )
    assert ingest_result.exit_code == 0, ingest_result.output
    # demo fixture の project.yaml は observation.enabled: false のため、
    # ingest は observe/report 段へ進まず `generated` で止まる（PR5: 有効化
    # されたプロジェクトの ingest→observe→report 経路は
    # tests/test_recast_ingest_report.py が別途検証する）。無効時の次の一手は
    # 実在する `svprpe observe` コマンドへの手動フォールバック案内であり、
    # 存在しない `svprpe recast report` サブコマンドは案内しない。
    assert "svprpe recast report" not in ingest_result.output

    state_file = load_recast_state(project_path.parent)
    run = state_file.runs["edm@suno"]
    assert run.state == "generated"
    assert run.inputs_digest is not None
    assert run.plan_sha256 is not None

    take_json = json.loads((takes_dir / "take.json").read_text(encoding="utf-8"))
    assert take_json["source"] == "manual"


def test_recast_ingest_rejects_when_not_awaiting_generation(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    audio_path = tmp_path / "external.wav"
    audio_path.write_bytes(b"RIFF....WAVEfake-audio-bytes")

    result = runner.invoke(
        app,
        [
            "recast",
            "ingest",
            str(project_path),
            "--variant",
            "edm",
            "--backend",
            "suno",
            "--audio",
            str(audio_path),
        ],
    )

    assert result.exit_code == 1
    assert "awaiting_generation" in result.output


def test_recast_ingest_rejects_when_inputs_are_stale(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    run_result = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert run_result.exit_code == 0, run_result.output

    score_path = project_path.parent / "composition_score.yaml"
    score_path.write_text(
        score_path.read_text(encoding="utf-8").replace(
            'core: "introspective night drive"', 'core: "changed after awaiting_generation"'
        ),
        encoding="utf-8",
    )
    audio_path = tmp_path / "external.wav"
    audio_path.write_bytes(b"RIFF....WAVEfake-audio-bytes")

    result = runner.invoke(
        app,
        [
            "recast",
            "ingest",
            str(project_path),
            "--variant",
            "edm",
            "--backend",
            "suno",
            "--audio",
            str(audio_path),
        ],
    )

    assert result.exit_code == 1
    assert "stale" in result.output

    # ingest が拒否された run に対して status も stale 表示し、ingest 案内を
    # 出さない（Codex P2 review round 2 指摘 4 の受け入れ条件）。
    status_result = runner.invoke(app, ["recast", "status", str(project_path)])
    assert status_result.exit_code == 0
    assert "stale" in status_result.output
    assert "ingest" not in status_result.output


def test_recast_ingest_succeeds_with_same_take_after_observation_anchors_typo_fixed(
    tmp_path: Path,
) -> None:
    """Codex P2 review（PR #212 指摘・plan.py:783）の回復フロー受け入れ条件:
    `observation.anchors` の typo 修正（注文書・生成音声のいずれにも影響しない
    編集）だけでは `inputs_digest` は変化せず、`recast run` 済みの
    `awaiting_generation` run を stale 化させない — 同じ take で `recast
    ingest` がそのまま成功することを検証する。

    `_project_identity_digest_component` が `observation` 節を除いた
    canonical 射影を使うようになる前は、project.yaml の raw bytes 全体を
    ハッシュしていたため、この typo 修正だけで stale 判定され、注文書と
    無関係な理由で `recast run` の再実行を強いられていた（fix 前の挙動は
    `test_recast_ingest_rejects_when_inputs_are_stale` と同型の「project.yaml
    が変わったら常に stale」だった）。"""
    project_path = _copy_demo_project(tmp_path)

    # 「typo」を模す: observation.anchors に typo 済みの anchor id を持たせた
    # 状態で run する（demo fixture は既定で observation.enabled: false /
    # anchors: [] のため、まず typo 版を書き込む）。
    project_text = project_path.read_text(encoding="utf-8")
    typo_project_text = project_text.replace(
        "observation:\n  enabled: false\n  anchors: []\n",
        'observation:\n  enabled: false\n  anchors: ["harmny"]\n',
        1,
    )
    assert typo_project_text != project_text  # sanity
    project_path.write_text(typo_project_text, encoding="utf-8")

    run_result = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert run_result.exit_code == 0, run_result.output

    takes_dir = project_path.parent / "builds" / "takes" / "edm@suno"
    takes_dir.mkdir(parents=True, exist_ok=True)
    audio_path = takes_dir / "take-01.wav"
    audio_bytes = b"RIFF....WAVEfake-audio-bytes"
    audio_path.write_bytes(audio_bytes)

    # typo を修正する（注文書・音源のいずれにも影響しない project.yaml 編集）。
    fixed_project_text = typo_project_text.replace(
        'anchors: ["harmny"]', 'anchors: ["harmony"]', 1
    )
    assert fixed_project_text != typo_project_text  # sanity
    project_path.write_text(fixed_project_text, encoding="utf-8")

    ingest_result = runner.invoke(
        app,
        [
            "recast",
            "ingest",
            str(project_path),
            "--variant",
            "edm",
            "--backend",
            "suno",
            "--audio",
            str(audio_path),
        ],
    )

    assert ingest_result.exit_code == 0, ingest_result.output
    assert "stale" not in ingest_result.output

    state_file = load_recast_state(project_path.parent)
    run = state_file.runs["edm@suno"]
    assert run.state == "generated"

    # 同じ take(同一 bytes)がそのまま収蔵されている(再生成を強いられていない)。
    take_json = json.loads((takes_dir / "take.json").read_text(encoding="utf-8"))
    assert take_json["source"] == "manual"
    ingested_audio = (takes_dir / "take-01.wav").read_bytes()
    assert ingested_audio == audio_bytes


def test_recast_ingest_rejects_when_non_observation_project_field_edited(
    tmp_path: Path,
) -> None:
    """`observation` 以外の project.yaml フィールド（例: `project.id`）の編集は
    従来どおり stale 判定される — `_project_identity_digest_component` が
    落とすのは `observation` 節だけで、それ以外は依然として raw な差分検出の
    対象であることを確認する回帰テスト（PR #212 指摘の対応範囲確認）。"""
    project_path = _copy_demo_project(tmp_path)
    run_result = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert run_result.exit_code == 0, run_result.output

    project_text = project_path.read_text(encoding="utf-8")
    updated_text = project_text.replace(
        'id: "midnight-signal-demo"', 'id: "midnight-signal-demo-renamed"', 1
    )
    assert updated_text != project_text  # sanity
    project_path.write_text(updated_text, encoding="utf-8")

    audio_path = tmp_path / "external.wav"
    audio_path.write_bytes(b"RIFF....WAVEfake-audio-bytes")

    result = runner.invoke(
        app,
        [
            "recast",
            "ingest",
            str(project_path),
            "--variant",
            "edm",
            "--backend",
            "suno",
            "--audio",
            str(audio_path),
        ],
    )

    assert result.exit_code == 1
    assert "stale" in result.output


def test_recast_ingest_rejects_when_plan_artifact_is_stale(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    run_result = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert run_result.exit_code == 0, run_result.output

    plan_path = project_path.parent / "recast_plan.json"
    plan_path.unlink()  # recast_plan.json 削除（P2 fourth round: 不在も stale 扱い）
    audio_path = tmp_path / "external.wav"
    audio_path.write_bytes(b"RIFF....WAVEfake-audio-bytes")

    result = runner.invoke(
        app,
        [
            "recast",
            "ingest",
            str(project_path),
            "--variant",
            "edm",
            "--backend",
            "suno",
            "--audio",
            str(audio_path),
        ],
    )

    assert result.exit_code == 1
    assert "plan artifact" in result.output


def test_recast_ingest_rejects_when_pin_is_missing(tmp_path: Path) -> None:
    """Codex P2 review round 4（PR3 #208 指摘 9）: 旧形式/手動コピーされた
    `recast_state.json`（`inputs_digest`/`plan_sha256` が `None`）を「未確認
    だから判定スキップ」で信用してしまうと、実際には変更済みかもしれない
    入力に対して素通りで ingest してしまう。`None` は stale と同扱いにして
    拒否することを検証する（`recast plan`/`recast run` の再実行を促す）。"""
    from svp_rpe.recast.state import record_state

    project_path = _copy_demo_project(tmp_path)
    run_result = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert run_result.exit_code == 0, run_result.output

    state_file = load_recast_state(project_path.parent)
    run = state_file.runs["edm@suno"]
    assert run.state == "awaiting_generation"
    # pin を落とした state を直接記録する（旧 schema や手動コピーの模倣）。
    record_state(
        project_path.parent,
        "edm",
        "suno",
        run.state,
        note=run.note,
        inputs_digest=None,
        plan_sha256=None,
        protected_inputs=[],
    )

    audio_path = tmp_path / "external.wav"
    audio_path.write_bytes(b"RIFF....WAVEfake-audio-bytes")

    result = runner.invoke(
        app,
        [
            "recast",
            "ingest",
            str(project_path),
            "--variant",
            "edm",
            "--backend",
            "suno",
            "--audio",
            str(audio_path),
        ],
    )

    assert result.exit_code == 1
    assert "no pin" in result.output.lower() or "pin" in result.output.lower()
    assert "stale" in result.output.lower()

    status_result = runner.invoke(app, ["recast", "status", str(project_path)])
    assert status_result.exit_code == 0
    assert "pin" in status_result.output


def test_recast_ingest_rejects_when_inputs_swapped_during_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 eighth round #207 指摘16（recast_cmd.py:555）: `recast ingest`
    の precheck（`compute_recast_inputs_digest` vs 記録済み pin）と、その直後の
    plan rebuild（`build_recast_plan_artifacts`）は別々の入力読み取りであり
    atomic ではない。precheck を通過させた**後**・rebuild が実際に入力を読む
    **直前**に composition_score.yaml を差し替えることで、この窓を再現する
    — rebuild は新しい（差し替え後の）入力から新しい plan/inputs_digest を
    構築してしまうが、その新 digest は記録済み pin（旧入力に対する digest）
    とは一致しないはずで、publish/collect の前に検出・拒否されるべきである。
    `svp_rpe.recast.plan.build_recast_plan_artifacts` をラップして「呼び出し
    直前に score を書き換えてから本物を呼ぶ」スタブに monkeypatch する —
    `recast_ingest_cmd` はこれをローカル `from ... import` で毎回名前解決する
    ため、定義元モジュールの属性を差し替えれば意図した箇所を確実に横取り
    できる。"""
    import svp_rpe.recast.plan as recast_plan_module

    project_path = _copy_demo_project(tmp_path)
    run_result = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert run_result.exit_code == 0, run_result.output

    project_dir = project_path.parent
    plan_path = project_dir / "recast_plan.json"
    plan_bytes_before = plan_path.read_bytes()
    state_before = load_recast_state(project_dir)
    assert state_before.runs["edm@suno"].state == "awaiting_generation"

    # `recast run` が plan 段（`build_recast_plan_artifacts(..., publish=True)`）
    # で既に公開済みの package（Codex P2, #210 round 3 指摘4 の対象そのもの）。
    package_path = project_dir / "builds" / "packages" / "edm@suno" / "performance_package.json"
    assert package_path.is_file()  # sanity: recast run が公開済み
    package_bytes_before = package_path.read_bytes()

    takes_dir = project_dir / "builds" / "takes" / "edm@suno"
    takes_dir.mkdir(parents=True, exist_ok=True)
    audio_path = takes_dir / "take-01.wav"
    audio_path.write_bytes(b"RIFF....WAVEfake-audio-bytes")

    score_path = project_dir / "composition_score.yaml"
    original_score_text = score_path.read_text(encoding="utf-8")
    swapped_score_text = original_score_text.replace(
        'core: "introspective night drive"', 'core: "swapped during rebuild window"'
    )
    assert swapped_score_text != original_score_text  # sanity

    real_build_recast_plan_artifacts = recast_plan_module.build_recast_plan_artifacts

    def _swap_inputs_then_build(*args: object, **kwargs: object):
        # precheck は既に通過済み（この関数に到達した時点）— rebuild が実際に
        # composition_score.yaml を読む直前に差し替える。
        score_path.write_text(swapped_score_text, encoding="utf-8")
        return real_build_recast_plan_artifacts(*args, **kwargs)

    monkeypatch.setattr(
        recast_plan_module, "build_recast_plan_artifacts", _swap_inputs_then_build
    )

    result = runner.invoke(
        app,
        [
            "recast",
            "ingest",
            str(project_path),
            "--variant",
            "edm",
            "--backend",
            "suno",
            "--audio",
            str(audio_path),
        ],
    )

    assert result.exit_code == 1
    assert "stale" in result.output.lower()

    # 旧 plan は非破壊（rebuild 済みの新入力向け plan で上書き publish されない）。
    assert plan_path.read_bytes() == plan_bytes_before

    # 旧 package も非破壊（Codex P2, #210 round 3 指摘4: rebuild を
    # `publish=False` で行い、digest 突合を通過するまで package を
    # publish しないことの機械 assert — 従来は rebuild 自体が
    # `publish=True` で走っていたため、この後の digest 拒否より前に
    # 差し替え後の入力で package が上書きされてしまっていた）。
    assert package_path.read_bytes() == package_bytes_before

    # take は収蔵されない（旧注文向け音声が新 plan の generated として記録されない）。
    assert not (takes_dir / "take.json").exists()

    # state も awaiting_generation のまま（generated へ誤って遷移しない）。
    state_after = load_recast_state(project_dir)
    assert state_after.runs["edm@suno"].state == "awaiting_generation"


def test_recast_ingest_does_not_overwrite_packages_when_inputs_swapped_during_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 review round 13（PR3 #208 指摘27, recast_cmd.py:577）: `recast
    ingest` の rebuild が `publish=True` のままだと、rebuild 直後の digest
    再突合で reject される swapped-input なケースでも `builds/packages/
    <variant>@<backend>/` が新しい（差し替え後の）入力の compile 結果で
    上書きされてしまう — pr5 #210 の同型修正（127891c）に実装形を寄せ、
    `publish=False` で rebuild → digest 再突合 → 通過後にのみ in-memory の
    `artifacts.compiled` を公開する経路に変更した（二重コンパイルなし）。
    `test_recast_ingest_rejects_when_inputs_swapped_during_rebuild` と同じ
    swap 注入手法を使い、reject 後も packages/ の旧
    performance_package.json/compilation_report.json の bytes が一切
    変わらないことを検証する。"""
    import svp_rpe.recast.plan as recast_plan_module

    project_path = _copy_demo_project(tmp_path)
    run_result = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert run_result.exit_code == 0, run_result.output

    project_dir = project_path.parent
    package_dir = project_dir / "builds" / "packages" / "edm@suno"
    package_bytes_before = (package_dir / "performance_package.json").read_bytes()
    report_bytes_before = (package_dir / "compilation_report.json").read_bytes()

    takes_dir = project_dir / "builds" / "takes" / "edm@suno"
    takes_dir.mkdir(parents=True, exist_ok=True)
    audio_path = takes_dir / "take-01.wav"
    audio_path.write_bytes(b"RIFF....WAVEfake-audio-bytes")

    score_path = project_dir / "composition_score.yaml"
    original_score_text = score_path.read_text(encoding="utf-8")
    swapped_score_text = original_score_text.replace(
        'core: "introspective night drive"', 'core: "swapped during rebuild window"'
    )
    assert swapped_score_text != original_score_text  # sanity

    real_build_recast_plan_artifacts = recast_plan_module.build_recast_plan_artifacts

    def _swap_inputs_then_build(*args: object, **kwargs: object):
        # precheck（inputs_digest/plan_sha256/orders_digest）は既に通過済み
        # （この関数に到達した時点）— rebuild が実際に composition_score.yaml
        # を読む直前に差し替える。
        score_path.write_text(swapped_score_text, encoding="utf-8")
        return real_build_recast_plan_artifacts(*args, **kwargs)

    monkeypatch.setattr(
        recast_plan_module, "build_recast_plan_artifacts", _swap_inputs_then_build
    )

    result = runner.invoke(
        app,
        [
            "recast",
            "ingest",
            str(project_path),
            "--variant",
            "edm",
            "--backend",
            "suno",
            "--audio",
            str(audio_path),
        ],
    )

    assert result.exit_code == 1
    assert "stale" in result.output.lower()

    # 旧 packages は非破壊（rebuild 済みの新入力向け compile 結果で上書き
    # publish されない — 指摘27 の中核検証）。
    assert (package_dir / "performance_package.json").read_bytes() == package_bytes_before
    assert (package_dir / "compilation_report.json").read_bytes() == report_bytes_before

    state_after = load_recast_state(project_dir)
    assert state_after.runs["edm@suno"].state == "awaiting_generation"


def test_recast_ingest_rejects_when_order_file_edited_after_run(tmp_path: Path) -> None:
    """Codex P2 review round 13（PR3 #208 指摘28, recast_cmd.py:622）: `recast
    run` が公開した注文書 6 ファイルのうち 1 つ（`prompt.json`）を人手で編集
    してから `recast ingest` を呼ぶと、従来は `invoker.prepare(ctx)` の
    無条件再公開が編集を黙って rebuild 結果で上書きし、「編集済み注文で
    生成された音声」を正規の注文書由来として受理してしまっていた。
    orders_digest 突合により ingest が拒否され、かつ注文書が republish
    されない（編集した bytes がそのまま残る — 証拠保全）ことを検証する。"""
    project_path = _copy_demo_project(tmp_path)
    run_result = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert run_result.exit_code == 0, run_result.output

    project_dir = project_path.parent
    order_dir = project_dir / "builds" / "orders" / "edm@suno"
    prompt_json_path = order_dir / "prompt.json"
    original_prompt_bytes = prompt_json_path.read_bytes()
    tampered_bytes = original_prompt_bytes.replace(b'"text"', b'"TAMPERED_text"', 1)
    assert tampered_bytes != original_prompt_bytes  # sanity: replacement matched
    prompt_json_path.write_bytes(tampered_bytes)

    takes_dir = project_dir / "builds" / "takes" / "edm@suno"
    takes_dir.mkdir(parents=True, exist_ok=True)
    audio_path = takes_dir / "take-01.wav"
    audio_path.write_bytes(b"RIFF....WAVEfake-audio-bytes")

    result = runner.invoke(
        app,
        [
            "recast",
            "ingest",
            str(project_path),
            "--variant",
            "edm",
            "--backend",
            "suno",
            "--audio",
            str(audio_path),
        ],
    )

    assert result.exit_code == 1
    assert "orders_digest" in result.output.lower() or "order files changed" in result.output.lower()

    # republish されない: 編集した bytes がそのまま残っている（証拠保全 —
    # 指摘28 の中核検証）。
    assert prompt_json_path.read_bytes() == tampered_bytes

    # take も収蔵されない・state も awaiting_generation のまま。
    assert not (takes_dir / "take.json").exists()
    state_after = load_recast_state(project_dir)
    assert state_after.runs["edm@suno"].state == "awaiting_generation"


def test_recast_ingest_republished_package_locator_matches_run_and_verifies_at_published_position(
    tmp_path: Path,
) -> None:
    """Codex P2 review round 14（PR3 #208 指摘29, recast_cmd.py:659）:
    `recast ingest` の `publish=False` 再ビルド（指摘27）は project_dir 直下の
    ephemeral tempdir で compile しており、`resolve_packages_dir(...)` とは
    深さが食い違う `artifact_base.locator` を焼き込んでいた。突合通過後に
    その bytes をそのまま実公開先へ publish すると、`recast run` が公開した
    （正しい深さの）値を**誤った深さの値で上書き**してしまう — 入力は
    run/ingest 間で一切変わっていないにも関わらず。

    `recast run` → `recast ingest` の順で実行し、両方が公開した
    `performance_package.json` の `artifact_base.locator` が byte 一致する
    こと、かつ ingest 後に published 位置（`builds/packages/edm@suno/`）で
    `verify_package` を直接呼んで pass することを検証する（locator が
    実際にその場所から manifest 参照 artifact を正しく解決できることの
    実地証明 — 文字列比較だけでは「両方とも同じ間違った値」を見逃す）。"""
    from svp_rpe.arrange.verify import verify_package

    project_path = _copy_demo_project(tmp_path)
    project_dir = project_path.parent

    run_result = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert run_result.exit_code == 0, run_result.output

    package_dir = project_dir / "builds" / "packages" / "edm@suno"
    package_path = package_dir / "performance_package.json"

    def _lyrics_locator() -> str:
        # `artifact_base` は package top-level ではなく、各 channel_artifacts
        # entry（ここでは demo project が唯一持つ "lyrics_text" channel）に
        # 付く（`ChannelArtifactReference.artifact_base`）。
        payload = json.loads(package_path.read_text(encoding="utf-8"))
        return payload["channel_artifacts"]["lyrics_text"][0]["artifact_base"]["locator"]

    locator_after_run = _lyrics_locator()

    verify_after_run = verify_package(package_path, project_dir / "identity.yaml")
    assert verify_after_run.ok, verify_after_run.checks  # sanity: run 直後は当然 pass する

    takes_dir = project_dir / "builds" / "takes" / "edm@suno"
    takes_dir.mkdir(parents=True, exist_ok=True)
    audio_path = takes_dir / "take-01.wav"
    audio_path.write_bytes(b"RIFF....WAVEfake-audio-bytes")

    ingest_result = runner.invoke(
        app,
        [
            "recast",
            "ingest",
            str(project_path),
            "--variant",
            "edm",
            "--backend",
            "suno",
            "--audio",
            str(audio_path),
        ],
    )
    assert ingest_result.exit_code == 0, ingest_result.output

    locator_after_ingest = _lyrics_locator()
    assert locator_after_ingest == locator_after_run

    verify_after_ingest = verify_package(package_path, project_dir / "identity.yaml")
    assert verify_after_ingest.ok, verify_after_ingest.checks


def test_recast_help_lists_ingest() -> None:
    result = runner.invoke(app, ["recast", "--help"])

    assert result.exit_code == 0
    assert "ingest" in result.output


# --- E2E acceptance: manual run -> deterministic invoke -> ingest CLI -> generated


@pytest.mark.slow
def test_e2e_manual_awaiting_generation_then_ingest_cli_reaches_generated(
    tmp_path: Path,
) -> None:
    """受け入れ条件（PR3 指示書 + Codex P2 review round 2 指摘 5 対応）:
    ① manual run（CLI）→ awaiting_generation
    ② deterministic invoke（API）で「外部生成の代役」音声を合成
    ③ svprpe recast ingest（CLI — next_command.txt が実際に案内するコマンド）で
       takes へ atomic 収蔵
    ④ 状態が generated へ遷移
    """
    project_path = _copy_demo_project(tmp_path)
    _add_deterministic_variant(project_path)

    # ① CLI: manual run against edm@suno -> awaiting_generation.
    result = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert result.exit_code == 0, result.output
    state_after_run = load_recast_state(project_path.parent)
    assert state_after_run.runs["edm@suno"].state == "awaiting_generation"

    order_dir = project_path.parent / "builds" / "orders" / "edm@suno"
    next_command = (order_dir / "next_command.txt").read_text(encoding="utf-8").strip()
    assert next_command.startswith("svprpe recast ingest ")

    # ② API: deterministic invoke synthesizes a stand-in "externally generated"
    # take. This deliberately stays API-level rather than going through
    # `svprpe recast run --backend deterministic`: `recast_plan.json` is a
    # single file shared by the whole project directory, so running a second
    # backend's plan through the CLI would overwrite it and make the suno
    # run's `plan_sha256` stale — exactly what an *external* generation step
    # (Suno UI, not this repo's own CLI) must not do.
    loaded = load_recast_project(project_path)
    det_artifacts = build_recast_plan_artifacts(
        loaded, variant="edm_deterministic", backend="deterministic", publish=True
    )
    assert det_artifacts.result.plan.state_reached in ("compiled", "verified")
    det_ctx = run_context_from_plan_artifacts(
        loaded,
        variant="edm_deterministic",
        backend="deterministic",
        artifacts=det_artifacts,
    )
    det_invoker = resolve_invoker(det_artifacts.backend_ref, det_ctx.profile)
    det_prepared = det_invoker.prepare(det_ctx)
    det_take = det_invoker.invoke(det_prepared)
    assert det_take.audio_path.is_file()

    # ③ CLI: svprpe recast ingest — the exact command next_command.txt advertises.
    ingest_result = runner.invoke(
        app,
        [
            "recast",
            "ingest",
            str(project_path),
            "--variant",
            "edm",
            "--backend",
            "suno",
            "--audio",
            str(det_take.audio_path),
        ],
    )
    assert ingest_result.exit_code == 0, ingest_result.output

    # ④ state transitioned to generated.
    final_state = load_recast_state(project_path.parent)
    run = final_state.runs["edm@suno"]
    assert run.state == "generated"
    assert run.plan_sha256 is not None

    takes_dir = project_path.parent / "builds" / "takes" / "edm@suno"
    take_json = json.loads((takes_dir / "take.json").read_text(encoding="utf-8"))
    assert take_json["source"] == "manual"
    assert (takes_dir / "take-01.wav").read_bytes() == det_take.audio_path.read_bytes()
