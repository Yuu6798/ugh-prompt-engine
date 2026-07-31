"""`recast/plan.py` の build_recast_plan テスト（PR2）。"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Optional

import pytest
import yaml

from svp_rpe.recast import RecastError, load_recast_project
from svp_rpe.recast.loader import LoadedRecastProject, load_mode_overrides
from svp_rpe.recast.models import ModeOverridesConfig, ObservationConfig
from svp_rpe.recast.plan import (
    RecastPlanResult,
    _normalize_diagnostic,
    build_recast_plan,
    build_recast_plan_artifacts,
    compute_observation_digest,
    mode_support_for_path,
)
from svp_rpe.recast.run_paths import collect_protected_input_paths
from svp_rpe.recast.state import load_recast_state, record_state

DEMO_PROJECT = Path("examples/recast/demo_project")
EXPECTED_PLAN = DEMO_PROJECT / "expected" / "recast_plan_edm_suno.json"
# R8-2 (Codex round8 P2 対応): 凍結済みの実 M1 registry（`tests/fixtures/
# melody_bench/registry.yaml`）をコピーのみで再利用する（バイト不変・
# tests/fixtures/melody_bench/*.yaml は変更禁止のためコピー元として読むだけ）。
REAL_M1_REGISTRY = Path("tests/fixtures/melody_bench/registry.yaml")


def _persist_state(
    loaded: LoadedRecastProject, variant: str, backend: str, result: RecastPlanResult
) -> None:
    """`svprpe recast plan` CLI が plan JSON publish 成功後に行う状態記録を模倣する
    （Codex P2 #207 で `build_recast_plan` からこの副作用を除去し CLI 側の責務に
    移した — このヘルパーはユニットテストから同じ手順を再現する）。`note` は
    `result.mode_gate_reasons`（strict/advisory ゲートが確定した診断一式の
    single source, Codex P2 fifth round #207）から組み立てる。"""
    if result.plan.blocked is not None:
        note = "; ".join(result.plan.blocked.reasons)
    else:
        note = "; ".join(result.mode_gate_reasons) if result.mode_gate_reasons else None
    record_state(
        loaded.project_dir,
        variant,
        backend,
        result.plan.state_reached,
        note,
        inputs_digest=result.inputs_digest,
        protected_inputs=collect_protected_input_paths(loaded, variant, backend),
    )


def _copy_demo_project(tmp_path: Path) -> Path:
    """demo_project の入力一式（project/score/identity/arrangements）を
    tmp_path 配下へコピーする（`expected/` snapshot は意図的に除外 —
    テストが自由に破壊改変できる作業コピーに、比較専用の committed 期待値を
    混ぜない）。project.yaml への path を返す。"""
    dest = tmp_path / "demo_project"
    dest.mkdir()
    shutil.copy(DEMO_PROJECT / "project.yaml", dest / "project.yaml")
    shutil.copy(DEMO_PROJECT / "composition_score.yaml", dest / "composition_score.yaml")
    shutil.copy(DEMO_PROJECT / "identity.yaml", dest / "identity.yaml")
    shutil.copytree(DEMO_PROJECT / "identity", dest / "identity")
    shutil.copytree(DEMO_PROJECT / "arrangements", dest / "arrangements")
    return dest / "project.yaml"


# --- happy path --------------------------------------------------------------


def test_demo_project_reaches_verified(tmp_path: Path) -> None:
    """demo backend (`suno`) は mode_overrides を宣言しているため、
    `semantic.grv.*` / `semantic.delta_e.overall` / `physical.brightness`
    （suno mode_overrides に prompt_only エントリが無い＝unknown/未実測）が
    advisory ゲート対象に入り、`recommendation`/`warnings` に反映される
    （Codex P2 fifth round #207: 宣言済み backend の unknown を fail-closed に
    扱う opt-in 計器）。state_reached 自体は `verified` のまま（advisory は
    到達状態を降格しない）。"""
    project_path = _copy_demo_project(tmp_path)
    loaded = load_recast_project(project_path)

    result = build_recast_plan(loaded, variant="edm", backend="suno")

    assert result.plan.blocked is None
    assert result.plan.state_reached == "verified"
    assert {a.anchor_id for a in result.plan.anchors} == {"lyrics", "melody", "harmony"}
    assert result.mode_gate_reasons  # unknown（未実測）ゲート対象が存在する
    assert any(
        "semantic.grv.primary" in reason and "unknown" in reason
        for reason in result.mode_gate_reasons
    )
    assert "未実測です" in result.plan.recommendation
    assert all(reason in result.plan.warnings for reason in result.mode_gate_reasons)

    _persist_state(loaded, "edm", "suno", result)
    state_file = load_recast_state(loaded.project_dir)
    assert state_file.runs["edm@suno"].state == "verified"
    assert state_file.runs["edm@suno"].inputs_digest == result.inputs_digest


def test_unknown_variant_raises_recast_error(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    loaded = load_recast_project(project_path)

    with pytest.raises(RecastError):
        build_recast_plan(loaded, variant="does-not-exist", backend="suno")


def test_unknown_backend_raises_recast_error(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    loaded = load_recast_project(project_path)

    with pytest.raises(RecastError):
        build_recast_plan(loaded, variant="edm", backend="does-not-exist")


def test_unknown_observation_anchor_does_not_block_plan(tmp_path: Path) -> None:
    """`observation.anchors` に identity manifest 側に無い anchor id（typo 等）
    を列挙していても、plan 段はそれを検証しない（PR6 の当初実装は plan 段で
    即時 `RecastError` を送出していたが、その後 `observe_generated_artifact` +
    `cli.recast_cmd.recast_ingest_cmd`（Codex P2, #210 round 9 指摘11）へ
    一本化した — plan/run が manual backend を `awaiting_generation`/
    `generated` まで進められることを優先し、観測スコープの妥当性は ingest の
    observe 直前でのみ検証する。plan.py の設計判断ログ参照）。"""
    project_path = _copy_demo_project(tmp_path)
    text = project_path.read_text(encoding="utf-8")
    assert "observation:\n  enabled: false\n  anchors: []\n" in text  # sanity
    text = text.replace(
        "observation:\n  enabled: false\n  anchors: []\n",
        "observation:\n  enabled: false\n  anchors: [does-not-exist]\n",
        1,
    )
    project_path.write_text(text, encoding="utf-8")
    loaded = load_recast_project(project_path)

    result = build_recast_plan(loaded, variant="edm", backend="suno")
    assert result.plan.blocked is None
    assert result.plan.state_reached == "verified"


def test_known_observation_anchor_subset_is_accepted(tmp_path: Path) -> None:
    """`observation.anchors` が manifest に実在する anchor id の部分集合でも
    plan 段は通常どおり評価される。"""
    project_path = _copy_demo_project(tmp_path)
    text = project_path.read_text(encoding="utf-8")
    text = text.replace(
        "observation:\n  enabled: false\n  anchors: []\n",
        "observation:\n  enabled: false\n  anchors: [harmony]\n",
        1,
    )
    project_path.write_text(text, encoding="utf-8")
    loaded = load_recast_project(project_path)

    result = build_recast_plan(loaded, variant="edm", backend="suno")
    assert result.plan.state_reached == "verified"


# --- byte-pin snapshot ---------------------------------------------------------


def test_demo_project_plan_matches_committed_snapshot_byte_for_byte(tmp_path: Path) -> None:
    """`svprpe recast plan` が publish する canonical JSON（sort_keys+indent=2+
    末尾改行）は `build_recast_plan` が返す `plan` を同じ規約で直列化したものと
    等しい — CLI を介さずここで直接そのバイト列を再現し、committed
    `expected/recast_plan_edm_suno.json` と一致することを検証する。"""
    project_path = _copy_demo_project(tmp_path)
    loaded = load_recast_project(project_path)

    result = build_recast_plan(loaded, variant="edm", backend="suno")
    canonical = (
        json.dumps(
            result.plan.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )

    expected = EXPECTED_PLAN.read_text(encoding="utf-8")
    assert canonical == expected


# --- single-read bundle (Codex P2 sixth round #207) -----------------------------


def test_build_recast_plan_reads_each_bundle_input_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """digest 用の読み取りと compile 用の読み取りが分離していた旧実装は、実行中
    の入力差し替え A→B→A で「B で compile した plan を A の digest で pin」
    しうる TOCTOU があった（AGENTS §8-A）。single-read 束化後は
    arrangement spec / capability profile / mode_overrides / device profile の
    各ファイルが `Path.read_bytes()` されるのはちょうど 1 回であることを
    monkeypatch カウンタで検証する。

    identity manifest と score は、束の外側にある別の single-read 規律を持つ
    副系（`verify_package` 自身の独立した V3 再検証）からも読まれるため 1 回
    では収まらない（1 回に固定すると無関係な副系の実装詳細でテストが壊れ
    やすくなるため、正確な期待値をここに明記する — この副系自体は本ゲートの
    対象外の正当な read）:
    - identity manifest: 束（1 回）+ `verify_package` の V3 再検証（manifest
      ファイル自体の read は 1 回、`manifest_bytes` を渡してパースし直す
      だけ）… 計 2 回。PR3 の packages 公開前衝突ガード（`protected_inputs`）は
      束が既に読んだ/parse 済みの manifest オブジェクトから副作用なく再構成
      するため追加 read は発生しない（Codex P2 review round 7, PR3 #208
      指摘 13 — 従来は `collect_protected_input_paths` を独立に呼んで
      manifest を再 parse しており、それが計 3 回目の read だったが、
      再 parse 自体が「blocked plan でも publish される」契約を壊す不具合
      だったため single-read 束からの再構成へ置き換えた副次効果でここも
      1 回減った）。
    - score: demo fixture の identity `source.locator` が
      `composition_score.yaml` 自身を指すため、束の直接 read（1 回）+ 束内の
      `parse_identity_manifest_with_artifacts` が source artifact として
      検証する read（1 回）+ `verify_package` の V3 再検証がもう一度 source
      artifact を検証する read（1 回）… 計 3 回。"""
    project_path = _copy_demo_project(tmp_path)
    loaded = load_recast_project(project_path)

    read_counts: dict[str, int] = {}
    original_read_bytes = Path.read_bytes

    def _counting_read_bytes(self: Path) -> bytes:
        key = str(self.resolve())
        read_counts[key] = read_counts.get(key, 0) + 1
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _counting_read_bytes)

    result = build_recast_plan(loaded, variant="edm", backend="suno")
    assert result.plan.state_reached == "verified"

    def _count(path: Path) -> int:
        return read_counts.get(str(path.resolve()), 0)

    assert _count(loaded.arrangement_paths["edm"]) == 1
    assert _count(loaded.capability_profile_paths["suno"]) == 1
    assert _count(loaded.mode_override_paths["suno"]) == 1
    assert _count(Path("config/device_profiles/suno.yaml")) == 1
    assert _count(loaded.identity_manifest_path) == 2
    assert _count(loaded.score_path) == 3


# --- diagnostic path normalization (Codex P2 seventh/eighth round #207) --------


def test_normalize_diagnostic_relativizes_paths_under_project_dir() -> None:
    project_dir = Path("/tmp/x/demo_project")
    text = (
        "identity manifest 'w': anchor 'lyrics' sha256 mismatch at "
        "/tmp/x/demo_project/identity/lyrics.txt: expected a, got b"
    )
    normalized = _normalize_diagnostic(text, project_dir)
    assert "identity/lyrics.txt" in normalized
    assert "/tmp/x/demo_project" not in normalized


def test_normalize_diagnostic_masks_sibling_directory_with_shared_prefix() -> None:
    """`project_dir` と文字列 prefix が一致するだけの兄弟ディレクトリ
    （例 `demo_project_evil`）は project_dir 配下ではない — 無境界な
    `str.replace(project_dir_str, ...)` だと `._evil/...` のような機械依存の
    断片が残ってしまう（Codex P2 eighth round #207）。境界判定を厳密にし、
    こうした兄弟パスは丸ごと `<external-path>` へマスクされる（project 相対の
    断片が残らない）ことを検証する。"""
    project_dir = Path("/tmp/x/demo_project")
    text = (
        "identity manifest 'w': anchor 'lyrics' sha256 mismatch at "
        "/tmp/x/demo_project_evil/identity/lyrics.txt: expected a, got b"
    )
    normalized = _normalize_diagnostic(text, project_dir)
    assert "._evil" not in normalized
    assert "demo_project_evil" not in normalized
    assert "/tmp/x/demo_project" not in normalized
    assert "<external-path>" in normalized


def test_normalize_diagnostic_leaves_bare_project_dir_relative() -> None:
    """`project_dir` 自身への完全一致（サブパスなし）は "." へ正規化される
    （境界判定: 直後が path 継続文字でないケース）。"""
    project_dir = Path("/tmp/x/demo_project")
    text = "identity manifest unreadable at /tmp/x/demo_project"
    normalized = _normalize_diagnostic(text, project_dir)
    assert "/tmp/x/demo_project" not in normalized
    assert normalized.endswith(".")


def test_normalize_diagnostic_relativizes_windows_drive_letter_paths() -> None:
    """Windows 実行時は `project_dir` 自身がバックスラッシュ区切りの文字列
    になる（`str(Path)` は OS ネイティブの区切り文字を使う）。`Path` は
    POSIX ランナー上でもバックスラッシュを含む文字列をそのまま保持する
    （PosixPath はバックスラッシュを区切り文字として解釈しない）ため、実際の
    OS に依存しない純文字列テストとして Windows 風パスを検証できる
    （Codex P2 thirteenth round #207, 指摘20: 旧実装は POSIX の `/...`
    トークンしか認識せず、ドライブレター形式の絶対パスがマスク・相対化
    されずそのまま漏れていた）。project 配下相対化の出力は常に POSIX 区切り
    （`/`）へ正規化される。"""
    project_dir = Path("C:\\tmp\\demo_project")
    text = (
        "identity manifest 'w': anchor 'lyrics' sha256 mismatch at "
        "C:\\tmp\\demo_project\\identity\\lyrics.txt: expected a, got b"
    )
    normalized = _normalize_diagnostic(text, project_dir)
    assert "identity/lyrics.txt" in normalized
    assert "C:\\tmp\\demo_project" not in normalized
    assert "\\" not in normalized  # 相対化された locator は POSIX 区切りのみ


def test_normalize_diagnostic_masks_external_windows_drive_letter_path() -> None:
    """project_dir 外を指す Windows ドライブレター絶対パスは `<external-path>`
    へマスクされる（Codex P2 thirteenth round #207, 指摘20）。"""
    project_dir = Path("C:\\tmp\\demo_project")
    text = "escaped containment to D:\\other\\place\\evil.yaml during resolve"
    normalized = _normalize_diagnostic(text, project_dir)
    assert "D:\\other" not in normalized
    assert "<external-path>" in normalized


def test_normalize_diagnostic_masks_external_unc_path() -> None:
    """project_dir 外を指す UNC パス（`\\\\server\\share\\...`）も
    `<external-path>` へマスクされる（Codex P2 thirteenth round #207, 指摘20）。"""
    project_dir = Path("C:\\tmp\\demo_project")
    text = "escaped containment to \\\\server\\share\\evil.yaml during resolve"
    normalized = _normalize_diagnostic(text, project_dir)
    assert "\\\\server" not in normalized
    assert "<external-path>" in normalized


# --- scenario (a): blocked_authoring via unresolved TODO sentinel --------------


def test_unresolved_author_field_blocks_authoring(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    score_path = project_path.parent / "composition_score.yaml"
    original = score_path.read_text(encoding="utf-8")
    mutated = original.replace(
        'core: "introspective night drive"',
        'core: "TODO(transcribe): author input required"',
    )
    assert mutated != original  # sanity: the replacement actually matched
    score_path.write_text(mutated, encoding="utf-8")

    loaded = load_recast_project(project_path)
    result = build_recast_plan(loaded, variant="edm", backend="suno")

    assert result.plan.state_reached == "blocked_authoring"
    assert result.plan.blocked is not None
    assert result.plan.blocked.state == "blocked_authoring"
    assert any("semantic.core" in reason for reason in result.plan.blocked.reasons)
    assert str(tmp_path) not in " ".join(result.plan.blocked.reasons)

    _persist_state(loaded, "edm", "suno", result)
    state_file = load_recast_state(loaded.project_dir)
    assert state_file.runs["edm@suno"].state == "blocked_authoring"


def test_unresolved_structure_role_blocks_authoring(tmp_path: Path) -> None:
    """`structure[].role` は semantic 層の外にある author 欄 — 全走査ゲートが
    semantic 限定の旧実装を置換したことを検証する回帰テスト（Codex P2 #207）。"""
    project_path = _copy_demo_project(tmp_path)
    score_path = project_path.parent / "composition_score.yaml"
    original = score_path.read_text(encoding="utf-8")
    mutated = original.replace(
        'role: "establish loneliness"',
        'role: "TODO(transcribe): author input required"',
    )
    assert mutated != original  # sanity: the replacement actually matched
    score_path.write_text(mutated, encoding="utf-8")

    loaded = load_recast_project(project_path)
    result = build_recast_plan(loaded, variant="edm", backend="suno")

    assert result.plan.state_reached == "blocked_authoring"
    assert result.plan.blocked is not None
    assert result.plan.blocked.state == "blocked_authoring"
    assert any("structure[0].role" in reason for reason in result.plan.blocked.reasons)
    assert str(tmp_path) not in " ".join(result.plan.blocked.reasons)

    _persist_state(loaded, "edm", "suno", result)
    state_file = load_recast_state(loaded.project_dir)
    assert state_file.runs["edm@suno"].state == "blocked_authoring"


# --- scenario (b): blocked_capability via strict capability_mode ---------------


def test_strict_capability_mode_blocks_on_hard_unsupported_anchor(tmp_path: Path) -> None:
    """demo_project の arrangement は melody / harmony を hard preservation で
    宣言している。melody の artifact_type (note_events_json) は suno の
    InputCapabilityProfile で symbolic_melody=unsupported、harmony
    (chord_sequence_json) は ARTIFACT_TYPE_CHANNEL 未対応で delivery=unknown。
    capability_mode: strict にすると `build_performance_package` は両方を
    hard anchor の strict failure として `PackageCompilationError` を送出する
    （`package.py` の `strict_failures` 収集 — `mode=="hard" and delivery_status
    in ("unsupported", "unknown")`）。"""
    project_path = _copy_demo_project(tmp_path)
    original = project_path.read_text(encoding="utf-8")
    mutated = original.replace("capability_mode: advisory", "capability_mode: strict")
    assert mutated != original
    project_path.write_text(mutated, encoding="utf-8")

    loaded = load_recast_project(project_path)
    result = build_recast_plan(loaded, variant="edm", backend="suno")

    assert result.plan.state_reached == "blocked_capability"
    assert result.plan.blocked is not None
    assert result.plan.blocked.state == "blocked_capability"
    reasons_text = " ".join(result.plan.blocked.reasons)
    assert "melody" in reasons_text
    assert "strict capability check failed" in reasons_text
    assert str(tmp_path) not in reasons_text

    _persist_state(loaded, "edm", "suno", result)
    state_file = load_recast_state(loaded.project_dir)
    assert state_file.runs["edm@suno"].state == "blocked_capability"


# --- scenario (c): blocked_verification via tampered anchor artifact -----------


def test_tampered_anchor_artifact_blocks_verification(tmp_path: Path) -> None:
    """1 byte 改竄した anchor artifact は `parse_identity_manifest_with_artifacts`
    の sha256 mismatch メッセージ（解決済み絶対パス入り）を `blocked.reasons` へ
    そのまま乗せていた（Codex P2 seventh round #207: blocked plan が機械依存
    bytes になり、ローカル FS レイアウトが `recast_plan.json` へ漏洩していた）。
    `_normalize_diagnostic` が project 相対へ正規化するため、publish される
    canonical JSON に tmp_path の絶対パス文字列が一切含まれないことを検証する。"""
    project_path = _copy_demo_project(tmp_path)
    lyrics_path = project_path.parent / "identity" / "lyrics.txt"
    original_bytes = lyrics_path.read_bytes()
    lyrics_path.write_bytes(original_bytes + b"X")  # 1 byte tamper, hash now stale

    loaded = load_recast_project(project_path)
    result = build_recast_plan(loaded, variant="edm", backend="suno")

    assert result.plan.state_reached == "blocked_verification"
    assert result.plan.blocked is not None
    assert result.plan.blocked.state == "blocked_verification"
    assert any("sha256" in reason for reason in result.plan.blocked.reasons)
    # 相対 locator へ正規化されたことの直接確認（identity/lyrics.txt が anchor
    # 'lyrics' の artifact path）。
    assert any("identity/lyrics.txt" in reason for reason in result.plan.blocked.reasons)

    canonical = (
        json.dumps(
            result.plan.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    assert str(tmp_path) not in canonical

    _persist_state(loaded, "edm", "suno", result)
    state_file = load_recast_state(loaded.project_dir)
    assert state_file.runs["edm@suno"].state == "blocked_verification"


# --- unsupported changed field: strict/advisory ゲート (Codex P2 second round #207) --


def _cover_key_override_project(tmp_path: Path, *, capability_mode: str) -> Path:
    """demo project を変形する: backend の invocation_mode を cover にし、
    physical.key を override する（cover の physical.key は
    `config/mode_overrides/suno.yaml` で unsupported と実測済み）。identity_anchors
    は melody/harmony の hard 宣言に由来する既存の strict failure（本ゲートとは
    無関係な別経路 — `test_strict_capability_mode_blocks_on_hard_unsupported_anchor`
    参照）を避けるため free に緩め、changed_fields の unsupported ゲートだけを単離する。"""
    project_path = _copy_demo_project(tmp_path)

    project_text = project_path.read_text(encoding="utf-8")
    mutated_project = project_text.replace("invocation_mode: prompt_only", "invocation_mode: cover")
    assert mutated_project != project_text
    mutated_project = mutated_project.replace(
        "capability_mode: advisory", f"capability_mode: {capability_mode}"
    )
    project_path.write_text(mutated_project, encoding="utf-8")

    arrangement_path = project_path.parent / "arrangements" / "edm.yaml"
    arrangement_text = arrangement_path.read_text(encoding="utf-8")
    mutated_arrangement = arrangement_text.replace(
        '  physical:\n    bpm: 132\n    brightness: "bright"\n',
        '  physical:\n    bpm: 132\n    brightness: "bright"\n    key: "A minor"\n',
    )
    assert mutated_arrangement != arrangement_text
    mutated_arrangement = mutated_arrangement.replace(
        "    physical.key: hard", "    physical.key: free"
    )
    mutated_arrangement = mutated_arrangement.replace("mode: hard", "mode: free")
    arrangement_path.write_text(mutated_arrangement, encoding="utf-8")

    return project_path


def test_unsupported_changed_field_blocks_capability_in_strict_mode(tmp_path: Path) -> None:
    project_path = _cover_key_override_project(tmp_path, capability_mode="strict")
    loaded = load_recast_project(project_path)

    result = build_recast_plan(loaded, variant="edm", backend="suno")

    assert result.plan.state_reached == "blocked_capability"
    assert result.plan.blocked is not None
    assert result.plan.blocked.state == "blocked_capability"
    reasons_text = " ".join(result.plan.blocked.reasons)
    assert "physical.key" in reasons_text
    assert "invocation_mode cover" in reasons_text
    assert "unsupported" in reasons_text
    assert str(tmp_path) not in reasons_text

    _persist_state(loaded, "edm", "suno", result)
    state_file = load_recast_state(loaded.project_dir)
    assert state_file.runs["edm@suno"].state == "blocked_capability"


def test_strict_mode_gate_does_not_publish_packages(tmp_path: Path) -> None:
    """Codex P2 review round 11（PR3 #208 指摘21）: `publish=True` で
    verification 自体は通っても、strict の mode gate（changed_fields の
    unsupported）で最終的に blocked_capability へ降格するケースでは、
    package/report を builds_root へ永続公開してはいけない — 従来は
    verification の**前**に永続公開していたため、blocked な plan なのに
    「使えそうな成果物」が builds_root に残ってしまっていた。本テストは
    `publish=True` を直接使い、`build_recast_plan_artifacts` 呼び出し後も
    `builds_root/packages/edm@suno/` が一切作られないことを検証する。"""
    project_path = _cover_key_override_project(tmp_path, capability_mode="strict")
    loaded = load_recast_project(project_path)
    package_dir = loaded.builds_root / "packages" / "edm@suno"
    assert not loaded.builds_root.exists()  # デモ fixture は builds/ を同梱しない

    artifacts = build_recast_plan_artifacts(loaded, variant="edm", backend="suno", publish=True)

    assert artifacts.result.plan.state_reached == "blocked_capability"
    assert not package_dir.exists()  # gate 通過前なので packages は publish されない


def test_strict_mode_gate_does_not_overwrite_existing_packages(tmp_path: Path) -> None:
    """`test_strict_mode_gate_does_not_publish_packages` の変種: 既に旧
    package/report が `builds_root/packages/edm@suno/` に存在する状態
    （advisory で一度 verified に到達した後、strict へ切り替えて同じ
    unsupported changed field で mode gate に引っかかるケースを模す）で、
    strict の mode gate により blocked_capability へ降格した場合に、その
    既存内容が上書きされない（旧内容のまま残る）ことを検証する。"""
    project_path = _cover_key_override_project(tmp_path, capability_mode="advisory")
    loaded = load_recast_project(project_path)

    # advisory では unsupported changed field があっても verified まで到達し、
    # publish=True で旧 package/report が実際に公開される。
    first = build_recast_plan_artifacts(loaded, variant="edm", backend="suno", publish=True)
    assert first.result.plan.state_reached == "verified"
    package_dir = loaded.builds_root / "packages" / "edm@suno"
    old_package_bytes = (package_dir / "performance_package.json").read_bytes()
    old_report_bytes = (package_dir / "compilation_report.json").read_bytes()

    # capability_mode を strict へ切り替え、同じ unsupported changed field で
    # 今度は mode gate に引っかからせる。
    project_text = project_path.read_text(encoding="utf-8")
    updated = project_text.replace("capability_mode: advisory", "capability_mode: strict")
    assert updated != project_text  # sanity
    project_path.write_text(updated, encoding="utf-8")
    loaded_strict = load_recast_project(project_path)

    second = build_recast_plan_artifacts(
        loaded_strict, variant="edm", backend="suno", publish=True
    )
    assert second.result.plan.state_reached == "blocked_capability"

    # 既存の package/report は一切上書きされていない（旧 verified 時点の
    # bytes のまま）。
    assert (package_dir / "performance_package.json").read_bytes() == old_package_bytes
    assert (package_dir / "compilation_report.json").read_bytes() == old_report_bytes


def test_unsupported_changed_field_warns_but_verifies_in_advisory_mode(tmp_path: Path) -> None:
    project_path = _cover_key_override_project(tmp_path, capability_mode="advisory")
    loaded = load_recast_project(project_path)

    result = build_recast_plan(loaded, variant="edm", backend="suno")

    assert result.plan.blocked is None
    assert result.plan.state_reached == "verified"
    warnings_text = " ".join(result.plan.warnings)
    assert "physical.key" in warnings_text
    assert "invocation_mode cover" in warnings_text
    assert "unsupported" in warnings_text
    assert "届きません" in result.plan.recommendation

    _persist_state(loaded, "edm", "suno", result)
    state_file = load_recast_state(loaded.project_dir)
    assert state_file.runs["edm@suno"].state == "verified"
    assert state_file.runs["edm@suno"].note is not None
    assert "physical.key" in state_file.runs["edm@suno"].note


# --- unknown changed field (未実測): opt-in ゲート (Codex P2 fifth round #207) --


def _declared_backend_project(tmp_path: Path, *, capability_mode: str) -> Path:
    """demo project（backend `suno` は mode_overrides "suno" を宣言済み）を、
    identity_anchors の hard 宣言由来の別経路 strict failure（本ゲートとは
    無関係 — `test_strict_capability_mode_blocks_on_hard_unsupported_anchor`
    参照）を避けるため free に緩めた一時 project にする。invocation_mode は
    demo 既定の prompt_only のまま（`semantic.grv.*` / `semantic.delta_e.overall`
    / `physical.brightness` は suno mode_overrides の prompt_only に
    エントリが無いため mode_support=="unknown" になる — unknown（未実測）
    ゲート単体を検証する目的）。"""
    project_path = _copy_demo_project(tmp_path)

    project_text = project_path.read_text(encoding="utf-8")
    mutated_project = project_text.replace(
        "capability_mode: advisory", f"capability_mode: {capability_mode}"
    )
    # demo の既定は advisory なので capability_mode="advisory" 呼び出しではテキストが
    # 変わらない（no-op）— これは意図どおりであり sanity assert の対象外とする。
    project_path.write_text(mutated_project, encoding="utf-8")

    arrangement_path = project_path.parent / "arrangements" / "edm.yaml"
    arrangement_text = arrangement_path.read_text(encoding="utf-8")
    mutated_arrangement = arrangement_text.replace("mode: hard", "mode: free")
    assert mutated_arrangement != arrangement_text
    arrangement_path.write_text(mutated_arrangement, encoding="utf-8")

    return project_path


def test_unknown_changed_field_blocks_capability_in_strict_mode_when_declared(
    tmp_path: Path,
) -> None:
    project_path = _declared_backend_project(tmp_path, capability_mode="strict")
    loaded = load_recast_project(project_path)

    result = build_recast_plan(loaded, variant="edm", backend="suno")

    assert result.plan.state_reached == "blocked_capability"
    assert result.plan.blocked is not None
    reasons_text = " ".join(result.plan.blocked.reasons)
    assert "physical.brightness" in reasons_text
    assert "invocation_mode prompt_only" in reasons_text
    assert "unknown（未実測）" in reasons_text
    assert str(tmp_path) not in reasons_text

    _persist_state(loaded, "edm", "suno", result)
    state_file = load_recast_state(loaded.project_dir)
    assert state_file.runs["edm@suno"].state == "blocked_capability"


def test_unknown_changed_field_warns_but_verifies_in_advisory_mode_when_declared(
    tmp_path: Path,
) -> None:
    project_path = _declared_backend_project(tmp_path, capability_mode="advisory")
    loaded = load_recast_project(project_path)

    result = build_recast_plan(loaded, variant="edm", backend="suno")

    assert result.plan.blocked is None
    assert result.plan.state_reached == "verified"
    warnings_text = " ".join(result.plan.warnings)
    assert "physical.brightness" in warnings_text
    assert "invocation_mode prompt_only" in warnings_text
    assert "unknown（未実測）" in warnings_text
    assert "未実測です" in result.plan.recommendation

    _persist_state(loaded, "edm", "suno", result)
    state_file = load_recast_state(loaded.project_dir)
    assert state_file.runs["edm@suno"].state == "verified"
    assert state_file.runs["edm@suno"].note is not None
    assert "physical.brightness" in state_file.runs["edm@suno"].note


def test_unknown_changed_field_does_not_gate_backend_without_mode_overrides(
    tmp_path: Path,
) -> None:
    """mode_overrides を宣言していない backend では、changed_fields が丸ごと
    unknown（`mode_overrides` 未参照のため）になっても従来どおり無視される —
    opt-in 計器の線引き（宣言していない backend は invocation_mode 軸自体を
    計測対象にしていないため、全 changed_field が unknown になるのは仕様
    どおりで異常ではない）を確認する回帰テスト。"""
    project_path = _copy_demo_project(tmp_path)
    project_text = project_path.read_text(encoding="utf-8")
    # `suno:` backend ブロック直後（唯一の `mode_overrides: "suno"` 行の直後）へ
    # 挿入する — PR3 で `deterministic:` backend が `suno:` の後に追加されたため、
    # `policy:` への直接隣接は前提にできない（backends マッピング内での挿入位置は
    # YAML/pydantic の dict なのでどこでもよい）。
    mutated = project_text.replace(
        '    mode_overrides: "suno"\n',
        '    mode_overrides: "suno"\n'
        "  suno_bare:\n"
        '    capability_profile: "suno"\n'
        "    invocation: manual\n"
        "    invocation_mode: prompt_only\n",
        1,
    )
    assert mutated != project_text
    project_path.write_text(mutated, encoding="utf-8")

    loaded = load_recast_project(project_path)
    result = build_recast_plan(loaded, variant="edm", backend="suno_bare")

    assert result.plan.blocked is None
    assert result.plan.state_reached == "verified"
    assert result.mode_gate_reasons == []
    assert result.plan.recommendation == "run へ進行可。"
    assert all(c.mode_support == "unknown" for c in result.plan.changed_fields)
    assert not any(w.startswith("field ") for w in result.plan.warnings)


# --- mode_overrides: ★invocation_mode 軸 ---------------------------------------


def _suno_mode_overrides() -> ModeOverridesConfig:
    return load_mode_overrides(Path("config/mode_overrides/suno.yaml"))


def test_mode_support_differs_between_cover_and_prompt_only() -> None:
    config = _suno_mode_overrides()

    cover_support = mode_support_for_path("physical.time_signature", "cover", config)
    prompt_only_support = mode_support_for_path(
        "physical.time_signature", "prompt_only", config
    )

    assert cover_support == "unsupported"
    assert prompt_only_support == "experimental"
    assert cover_support != prompt_only_support


def test_mode_support_falls_back_to_unknown_for_undeclared_path() -> None:
    config = _suno_mode_overrides()

    assert mode_support_for_path("semantic.core", "cover", config) == "unknown"


def test_mode_support_falls_back_to_unknown_when_no_config() -> None:
    assert mode_support_for_path("physical.bpm", "cover", None) == "unknown"


# --- collect_protected_input_paths: unknown variant/backend (Codex P2, #210 round 14 指摘19) ---


def test_collect_protected_input_paths_rejects_unknown_variant(tmp_path: Path) -> None:
    """`collect_protected_input_paths` 自身も未知 variant を actionable な
    `RecastError` で拒否する（defense in depth — CLI の
    `_validate_variant_backend_declared` を経由しないプログラム的呼び出し
    向け。従来は `loaded.arrangement_paths[variant]` の生 `KeyError` だった）。"""
    project_path = _copy_demo_project(tmp_path)
    loaded = load_recast_project(project_path)

    with pytest.raises(RecastError, match="unknown variant 'does-not-exist'"):
        collect_protected_input_paths(loaded, "does-not-exist", "suno")


def test_collect_protected_input_paths_rejects_unknown_backend(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    loaded = load_recast_project(project_path)

    with pytest.raises(RecastError, match="unknown backend 'does-not-exist'"):
        collect_protected_input_paths(loaded, "edm", "does-not-exist")


# --- R1-3 (Codex round1 P2): compute_observation_digest melody content hash --


def _observation_config(*, melody: dict | None = None, enabled: bool = True) -> ObservationConfig:
    payload: dict = {"enabled": enabled, "anchors": []}
    if melody is not None:
        payload["melody"] = melody
    return ObservationConfig.model_validate(payload)


def _melody_observation_payload(*, reference: str = "score", **overrides: str) -> dict:
    payload = {
        "reference": reference,
        "comparison_registry": "m3_comparison_registry.yaml",
        "m1_registry": "registry.yaml",
        "route": "crepe_direct",
    }
    payload.update(overrides)
    return payload


def test_compute_observation_digest_without_melody_matches_pre_r1_3_formula() -> None:
    """R1-3: `observation.melody` 未設定のプロジェクトでは、digest は本対応
    **前**の実装（``sha256(canonical_json(observation))``）とバイト単位で
    完全に不変——既存 golden project fixture の digest への影響ゼロを直接
    証明する（golden project 自体には observation.melody が無い —
    `examples/recast/*/project.yaml` 参照）。"""
    observation = _observation_config()
    payload = observation.model_dump(mode="json", exclude_none=True)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    assert compute_observation_digest(observation) == expected
    # project_dir を渡しても（melody 未設定なら）無視されて同じ値のまま。
    assert (
        compute_observation_digest(observation, project_dir=Path("/does/not/exist"))
        == expected
    )


def test_compute_observation_digest_requires_project_dir_when_melody_set(
    tmp_path: Path,
) -> None:
    (tmp_path / "m3_comparison_registry.yaml").write_bytes(b"m3")
    (tmp_path / "registry.yaml").write_bytes(b"m1")
    observation = _observation_config(melody=_melody_observation_payload())

    with pytest.raises(ValueError, match="project_dir is required"):
        compute_observation_digest(observation)


def test_compute_observation_digest_changes_when_melody_registry_bytes_change(
    tmp_path: Path,
) -> None:
    """`observation` 節自体（project.yaml 上の文字列参照）は無変更のまま、
    参照先レジストリだけを in-place で書き換える——Codex 指摘の再現条件その
    ものが digest を変化させる。"""
    (tmp_path / "m3_comparison_registry.yaml").write_bytes(b"m3-v1")
    (tmp_path / "registry.yaml").write_bytes(b"m1-v1")
    observation = _observation_config(melody=_melody_observation_payload())
    digest_before = compute_observation_digest(observation, project_dir=tmp_path)

    (tmp_path / "m3_comparison_registry.yaml").write_bytes(b"m3-v2")
    digest_after = compute_observation_digest(observation, project_dir=tmp_path)

    assert digest_before != digest_after


def test_compute_observation_digest_stable_when_melody_files_unchanged(
    tmp_path: Path,
) -> None:
    (tmp_path / "m3_comparison_registry.yaml").write_bytes(b"m3")
    (tmp_path / "registry.yaml").write_bytes(b"m1")
    observation = _observation_config(melody=_melody_observation_payload())

    first = compute_observation_digest(observation, project_dir=tmp_path)
    second = compute_observation_digest(observation, project_dir=tmp_path)

    assert first == second


def test_compute_observation_digest_includes_reference_audio_only_for_audio_reference(
    tmp_path: Path,
) -> None:
    (tmp_path / "m3_comparison_registry.yaml").write_bytes(b"m3")
    (tmp_path / "registry.yaml").write_bytes(b"m1")
    (tmp_path / "ref.wav").write_bytes(b"audio-v1")
    observation = _observation_config(
        melody=_melody_observation_payload(reference="audio", reference_audio="ref.wav")
    )
    digest_before = compute_observation_digest(observation, project_dir=tmp_path)

    (tmp_path / "ref.wav").write_bytes(b"audio-v2")
    digest_after = compute_observation_digest(observation, project_dir=tmp_path)

    assert digest_before != digest_after


# --- R3-2 (Codex round3 P2): observation.enabled=False skips melody dereference --


def test_compute_observation_digest_skips_melody_dereference_when_disabled() -> None:
    """`observation.enabled is False` のときは melody 参照先ファイルを一切
    dereference しない——参照先が存在しない project_dir を渡しても（ひいては
    project_dir を渡さなくても）エラーにならず、設定のみの base_digest が
    得られる。従来は呼び出し側のガード順（`_precheck_observation_anchors`
    が `observation.enabled` を見てから呼ぶ）に依存しており、本関数自身は
    `observation.enabled` を見ずに常に melody 参照を resolve+hash していた
    ため、observation 無効な project でも melody 参照ファイル欠落だけで
    参照エラーになり得た。"""
    observation = _observation_config(melody=_melody_observation_payload(), enabled=False)
    payload = observation.model_dump(mode="json", exclude_none=True)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # project_dir 省略でもエラーにならない（melody 未設定時と同じ経路）。
    assert compute_observation_digest(observation) == expected
    # melody 参照先が存在しない project_dir を渡してもエラーにならず、同じ
    # base_digest のまま（dereference 自体が発生しない）。
    assert (
        compute_observation_digest(observation, project_dir=Path("/does/not/exist"))
        == expected
    )


def test_compute_observation_digest_enabled_true_still_dereferences_melody(
    tmp_path: Path,
) -> None:
    """`observation.enabled is True` では R3-2 対応後も従来どおり melody 参照
    先の content hash が digest へ編入される（enabled=False ガードが
    enabled=True 経路の挙動を変えないことの確認 — R1-3 の既存テスト群
    （`test_compute_observation_digest_changes_when_melody_registry_bytes_
    change` 等）と対になる直接確認）。"""
    (tmp_path / "m3_comparison_registry.yaml").write_bytes(b"m3")
    (tmp_path / "registry.yaml").write_bytes(b"m1")
    observation = _observation_config(melody=_melody_observation_payload(), enabled=True)

    payload = observation.model_dump(mode="json", exclude_none=True)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    base_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    digest = compute_observation_digest(observation, project_dir=tmp_path)
    assert digest != base_digest


# --- R4-4 (Codex round4 P2・lenient-missing): 未使用 melody 参照の欠落で digest --
# --- 計算を落とさない --------------------------------------------------------


def test_compute_observation_digest_missing_melody_registry_does_not_raise(
    tmp_path: Path,
) -> None:
    """enabled=True でも、参照先の m3/m1 registry ファイルが存在しなければ
    （axis_policy 撤去後・活性化前の準備段階を想定）、digest は raise せず
    決定論的センチネルを fold して得られる——`resolve_melody_observation_
    paths(..., require_exists=False)` + `_sha256_file_or_missing_sentinel`
    の lenient-missing 方式（旧仕様は `RecastError` で fail-closed だった）。"""
    observation = _observation_config(melody=_melody_observation_payload())
    # tmp_path 配下に m3_comparison_registry.yaml / registry.yaml を意図的に
    # 用意しない（欠落を再現）。
    digest = compute_observation_digest(observation, project_dir=tmp_path)
    assert isinstance(digest, str) and len(digest) == 64


def test_compute_observation_digest_missing_to_present_melody_registry_changes_digest(
    tmp_path: Path,
) -> None:
    """R4-4: 欠落→出現の遷移で digest が変わる（staleness 検出が正しく働く
    ——sentinel と実 sha256 は異なる値になる）。"""
    observation = _observation_config(melody=_melody_observation_payload())
    digest_missing = compute_observation_digest(observation, project_dir=tmp_path)

    (tmp_path / "m3_comparison_registry.yaml").write_bytes(b"m3")
    (tmp_path / "registry.yaml").write_bytes(b"m1")
    digest_present = compute_observation_digest(observation, project_dir=tmp_path)

    assert digest_missing != digest_present


def test_compute_observation_digest_missing_reference_audio_does_not_raise(
    tmp_path: Path,
) -> None:
    """R4-4: `reference == "audio"` の `reference_audio` 参照が欠落していても
    digest は raise しない（m3/m1 と同じ lenient-missing 方式を reference_audio
    にも一貫して適用する）。"""
    (tmp_path / "m3_comparison_registry.yaml").write_bytes(b"m3")
    (tmp_path / "registry.yaml").write_bytes(b"m1")
    observation = _observation_config(
        melody=_melody_observation_payload(reference="audio", reference_audio="ref.wav")
    )
    digest = compute_observation_digest(observation, project_dir=tmp_path)
    assert isinstance(digest, str) and len(digest) == 64


def test_compute_observation_digest_melody_confinement_violation_still_raises(
    tmp_path: Path,
) -> None:
    """R4-4: lenient-missing は「実在チェック」だけを緩める——封じ込め違反
    （project 外脱出、それ自体は設定エラー）は従来どおり fail-closed のまま
    `RecastError` を送出する。"""
    observation = _observation_config(
        melody=_melody_observation_payload(comparison_registry="../outside.yaml")
    )
    with pytest.raises(RecastError, match="invalid"):
        compute_observation_digest(observation, project_dir=tmp_path)


# --- R1-5 (Codex round1 P2): melody resolved paths join protected-input set --


def _add_melody_observation(
    project_path: Path, *, route: str = "crepe_direct", observation_enabled: bool = True
) -> None:
    """demo_project の project.yaml に `observation.melody` を追加し、参照先
    レジストリのプレースホルダファイルを project_dir 直下へ用意する
    （`resolve_melody_observation_paths` は存在チェックのみ・内容は検証
    しないため中身は任意バイトでよい）。``observation_enabled``（既定 True。
    R8-1・Codex round8 P2 対応）: demo_project の既定は `observation.enabled:
    false` のため、protected-input 収集を検証するテストはここで明示的に
    True へ上書きする必要がある——False にすると `observation.enabled: false`
    の generated-only 運用（disabled 対称性ガードの対象）を再現する。"""
    project_dir = project_path.parent
    (project_dir / "m3_comparison_registry.yaml").write_bytes(b"m3-placeholder")
    (project_dir / "registry.yaml").write_bytes(b"m1-placeholder")
    project_data = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    project_data["observation"]["enabled"] = observation_enabled
    project_data["observation"]["melody"] = {
        "reference": "score",
        "comparison_registry": "m3_comparison_registry.yaml",
        "m1_registry": "registry.yaml",
        "route": route,
    }
    project_path.write_text(yaml.safe_dump(project_data, sort_keys=False), encoding="utf-8")


def test_collect_protected_input_paths_includes_melody_resolved_paths(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    _add_melody_observation(project_path)
    loaded = load_recast_project(project_path)

    resolved = {p.resolve() for p in collect_protected_input_paths(loaded, "edm", "suno")}

    assert (loaded.project_dir / "m3_comparison_registry.yaml").resolve() in resolved
    assert (loaded.project_dir / "registry.yaml").resolve() in resolved


def test_collect_protected_input_paths_without_melody_config_is_unaffected(
    tmp_path: Path,
) -> None:
    """melody 未設定のプロジェクトは従来どおり（R1-5 は additive）。"""
    project_path = _copy_demo_project(tmp_path)
    loaded = load_recast_project(project_path)

    paths = collect_protected_input_paths(loaded, "edm", "suno")

    assert not any(
        p.name in ("m3_comparison_registry.yaml", "registry.yaml") for p in paths
    )


def test_build_recast_plan_protected_inputs_include_melody_registries(
    tmp_path: Path,
) -> None:
    """R1-5: `RecastPlanResult.protected_inputs`（plan 由来の protected set。
    `build_recast_plan_artifacts` 内で束から再構成される集合）にも
    `observation.melody` の resolved パスが編入される。"""
    project_path = _copy_demo_project(tmp_path)
    _add_melody_observation(project_path)
    loaded = load_recast_project(project_path)

    result = build_recast_plan(loaded, variant="edm", backend="suno")

    resolved = {p.resolve() for p in result.protected_inputs}
    assert (loaded.project_dir / "m3_comparison_registry.yaml").resolve() in resolved
    assert (loaded.project_dir / "registry.yaml").resolve() in resolved
    # melody 設定を追加しても plan 到達状態そのものには影響しない（additive）。
    assert result.plan.state_reached == "verified"


# --- R6-1 (Codex round6 P2 対応): melody 保護パスの独立収集 (all-or-nothing 排除) --


def _add_melody_observation_audio_reference(
    project_path: Path,
    *,
    reference_audio: str,
    reference_band: str = "clear_lead",
    observation_enabled: bool = True,
) -> None:
    """demo_project に ``reference: audio`` の `observation.melody` を足す。
    `reference_audio` はあえて実ファイルを作らない呼び出しにも使えるよう、
    存在有無は呼び出し側の判断に委ねる（M3/M1 registry は常に用意する）。
    ``observation_enabled``（既定 True。R8-1）: `_add_melody_observation` と
    同じ理由——demo_project の既定 `observation.enabled: false` を検証用に
    明示的へ上書きする seam。"""
    project_dir = project_path.parent
    (project_dir / "m3_comparison_registry.yaml").write_bytes(b"m3-placeholder")
    (project_dir / "registry.yaml").write_bytes(b"m1-placeholder")
    project_data = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    project_data["observation"]["enabled"] = observation_enabled
    project_data["observation"]["melody"] = {
        "reference": "audio",
        "reference_audio": reference_audio,
        "reference_band": reference_band,
        "comparison_registry": "m3_comparison_registry.yaml",
        "m1_registry": "registry.yaml",
        "route": "crepe_direct",
    }
    project_path.write_text(yaml.safe_dump(project_data, sort_keys=False), encoding="utf-8")


def test_collect_protected_input_paths_includes_registries_despite_missing_reference_audio(
    tmp_path: Path,
) -> None:
    """R6-1: `reference_audio` が未生成（実ファイル不在）でも、M3/M1 registry の
    保護は道連れにならない——旧実装は 1 回の `resolve_melody_observation_paths`
    呼び出しが reference_audio の実在検証（既定 require_exists=True）で
    ``RecastError`` を送出し、`except RecastError: pass` が 3 パス全ての保護を
    諦めていた（有効な M3 registry が report と alias していても
    protected_inputs に入らず publish に上書きされ得た）。"""
    project_path = _copy_demo_project(tmp_path)
    _add_melody_observation_audio_reference(
        project_path, reference_audio="reference_take.wav"
    )
    loaded = load_recast_project(project_path)

    resolved = {p.resolve() for p in collect_protected_input_paths(loaded, "edm", "suno")}

    assert (loaded.project_dir / "m3_comparison_registry.yaml").resolve() in resolved
    assert (loaded.project_dir / "registry.yaml").resolve() in resolved


def test_collect_protected_input_paths_containment_violation_does_not_drop_other_melody_paths(
    tmp_path: Path,
) -> None:
    """R6-1: `reference_audio` が封じ込め違反（project 外への絶対パス）でも、
    M3/M1 registry は独立に保護され続ける（1 パスの違反が他パスの保護を
    道連れにしない——パス単位の独立 append）。"""
    project_path = _copy_demo_project(tmp_path)
    _add_melody_observation_audio_reference(
        project_path, reference_audio="/etc/passwd"
    )
    loaded = load_recast_project(project_path)

    resolved = {p.resolve() for p in collect_protected_input_paths(loaded, "edm", "suno")}

    assert (loaded.project_dir / "m3_comparison_registry.yaml").resolve() in resolved
    assert (loaded.project_dir / "registry.yaml").resolve() in resolved
    assert Path("/etc/passwd") not in resolved


def test_build_recast_plan_protected_inputs_include_registries_despite_missing_reference_audio(
    tmp_path: Path,
) -> None:
    """R6-1: plan 由来の `protected_inputs`（`build_recast_plan_artifacts` が
    束から再構成する集合）でも同じ all-or-nothing 排除が効く。"""
    project_path = _copy_demo_project(tmp_path)
    _add_melody_observation_audio_reference(
        project_path, reference_audio="reference_take.wav"
    )
    loaded = load_recast_project(project_path)

    result = build_recast_plan(loaded, variant="edm", backend="suno")

    resolved = {p.resolve() for p in result.protected_inputs}
    assert (loaded.project_dir / "m3_comparison_registry.yaml").resolve() in resolved
    assert (loaded.project_dir / "registry.yaml").resolve() in resolved


# --- R8-1 (Codex round8 P2 対応): observation.enabled=False では melody 保護 --
# --- パスも収集しない（R3-2/R4-1 の disabled 対称性の完成） ------------------


def test_collect_protected_input_paths_skips_melody_when_observation_disabled(
    tmp_path: Path,
) -> None:
    """`observation.enabled: false` では `collect_protected_input_paths`
    （`recast/run_paths.py`）が melody の resolved パスを protected_inputs へ
    編入しない——観測無効時 melody locator は入力として読まれないため保護
    不要（従来は enabled を見ずに常に編入していた）。"""
    project_path = _copy_demo_project(tmp_path)
    _add_melody_observation(project_path, observation_enabled=False)
    loaded = load_recast_project(project_path)

    paths = collect_protected_input_paths(loaded, "edm", "suno")

    assert not any(p.name in ("m3_comparison_registry.yaml", "registry.yaml") for p in paths)


def test_build_recast_plan_protected_inputs_skip_melody_when_observation_disabled(
    tmp_path: Path,
) -> None:
    """R8-1: `build_recast_plan_artifacts`（`recast/plan.py`）側の鏡像ブロック
    でも同じガードが効く——plan 由来の `protected_inputs` にも melody の
    resolved パスが編入されない。"""
    project_path = _copy_demo_project(tmp_path)
    _add_melody_observation(project_path, observation_enabled=False)
    loaded = load_recast_project(project_path)

    result = build_recast_plan(loaded, variant="edm", backend="suno")

    resolved = {p.resolve() for p in result.protected_inputs}
    assert (loaded.project_dir / "m3_comparison_registry.yaml").resolve() not in resolved
    assert (loaded.project_dir / "registry.yaml").resolve() not in resolved


def _add_melody_observation_aliasing_package_output(
    project_path: Path, *, observation_enabled: bool
) -> None:
    """`observation.melody.comparison_registry` を「これから公開される
    package 出力ファイル」（`builds/packages/edm@suno/performance_package.
    json`、demo project の `project.builds_root: "builds"` 前提）へ alias
    させる——dormant な melody locator が生成物パスを指す設定ミスの再現
    （R8-1 が修正する具体的シナリオ）。参照先ファイルは publish 前には
    存在しないが、`resolve_melody_observation_paths_for_protection` は
    封じ込め検証のみ（`require_exists=False`）で解決するため実在は不要。"""
    project_data = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    project_data["observation"]["enabled"] = observation_enabled
    project_data["observation"]["melody"] = {
        "reference": "score",
        "comparison_registry": "builds/packages/edm@suno/performance_package.json",
        "m1_registry": "builds/packages/edm@suno/performance_package.json",
        "route": "crepe_direct",
    }
    project_path.write_text(yaml.safe_dump(project_data, sort_keys=False), encoding="utf-8")


def test_disabled_observation_allows_publish_when_melody_locator_aliases_package_output(
    tmp_path: Path,
) -> None:
    """R8-1: `observation.enabled: false` なら、melody locator が publish 先
    package 出力（`performance_package.json`）を alias していても衝突ガードの
    対象に入らないため、publish が拒否されず成功する——観測を一切行わない
    project でも無関係な衝突で生成がブロックされていた不具合の再現終了確認。"""
    project_path = _copy_demo_project(tmp_path)
    _add_melody_observation_aliasing_package_output(project_path, observation_enabled=False)
    loaded = load_recast_project(project_path)
    package_dir = loaded.builds_root / "packages" / "edm@suno"

    artifacts = build_recast_plan_artifacts(loaded, variant="edm", backend="suno", publish=True)

    assert artifacts.result.plan.state_reached == "verified"
    assert (package_dir / "performance_package.json").exists()
    assert (package_dir / "compilation_report.json").exists()


def test_enabled_observation_still_rejects_publish_when_melody_locator_aliases_package_output(
    tmp_path: Path,
) -> None:
    """対称確認: `observation.enabled: true` では同じ alias 設定が従来どおり
    衝突として publish を拒否する（R8-1 が disabled 側だけを緩め、enabled
    側の保護を弱めていないことの回帰確認）。package 公開サイトの衝突検出
    （`_atomic_publish_text_bundle` が送出する `ValueError`）はここでは
    `except ValueError` により `blocked_capability` へ変換される
    （raise を伝播させる `_preflight_reject_plan_state_output_collision` の
    経路とは別サイト——`build_recast_plan_artifacts` の package 公開 try/except
    参照）ため、raise ではなく blocked 状態と reasons への反映で確認する。"""
    project_path = _copy_demo_project(tmp_path)
    _add_melody_observation_aliasing_package_output(project_path, observation_enabled=True)
    loaded = load_recast_project(project_path)
    package_dir = loaded.builds_root / "packages" / "edm@suno"

    artifacts = build_recast_plan_artifacts(loaded, variant="edm", backend="suno", publish=True)

    assert artifacts.result.plan.state_reached == "blocked_capability"
    assert artifacts.result.plan.blocked is not None
    assert any(
        "collides with a protected input path" in reason
        for reason in artifacts.result.plan.blocked.reasons
    )
    assert not (package_dir / "performance_package.json").exists()
    assert not (package_dir / "compilation_report.json").exists()


# --- R3-3 (Codex round3 P2): melody 診断は package 公開前に完了させる ---------


def _add_melody_axis_policy_project(
    tmp_path: Path,
    *,
    m3_registry_bytes: bytes,
    observation_enabled: bool = True,
    m1_registry_bytes: Optional[bytes] = None,
) -> Path:
    """demo_project (variant edm/backend suno) に axis_policy 付き melody
    anchor + `observation.melody` 配線を足した作業コピーを組み立てる
    （`_add_melody_observation` は axis_policy を宣言しないため
    `melody_experimental_plan_warnings` が起動しない——本ヘルパーはそれに
    加えて `arrangements/edm.yaml` の melody identity anchor へ axis_policy
    を足し、`backends.suno.melody_take_band` も宣言して M4c 経路を実際に
    起動させる）。``m3_registry_bytes`` は呼び出し側が用意する
    `m3_comparison_registry.yaml` の生 bytes（破損/欠落の再現に使う）。
    ``observation_enabled``（既定 True・R4-1）: False にすると
    `observation.enabled: false` の generated-only 運用を再現する。
    ``m1_registry_bytes``（既定 None・R8-2・Codex round8 P2 対応）: 省略時は
    `REAL_M1_REGISTRY`（凍結済みの実 M1 registry）のバイトをそのまま使う
    ——旧来の `b"m1-placeholder"` は `observation_gate` を持たない不正な
    registry であり、G1 通過後に `_load_m1_registry` を呼ぶ R8-2 の下では
    常に `m1_registry_unavailable` になってしまうため、「M1 は正常」を前提と
    する既存の "ok" 系テストが壊れないよう既定値を正規の registry へ差し替える
    （M1 の欠落/破損を再現したい呼び出し側は明示的に ``m1_registry_bytes`` を
    渡す）。"""
    project_path = _copy_demo_project(tmp_path)
    project_dir = project_path.parent

    (project_dir / "m3_comparison_registry.yaml").write_bytes(m3_registry_bytes)
    if m1_registry_bytes is None:
        shutil.copy(REAL_M1_REGISTRY, project_dir / "registry.yaml")
    else:
        (project_dir / "registry.yaml").write_bytes(m1_registry_bytes)

    arrangement_path = project_dir / "arrangements" / "edm.yaml"
    arrangement_data = yaml.safe_load(arrangement_path.read_text(encoding="utf-8"))
    arrangement_data["preservation"]["identity_anchors"]["melody"]["axis_policy"] = {
        "contour": "hard",
        "interval": "elastic",
        "rhythm": "free",
    }
    arrangement_path.write_text(yaml.safe_dump(arrangement_data, sort_keys=False), encoding="utf-8")

    project_data = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    project_data["backends"]["suno"]["melody_take_band"] = "clear_lead"
    project_data["observation"]["enabled"] = observation_enabled
    project_data["observation"]["melody"] = {
        "reference": "score",
        "comparison_registry": "m3_comparison_registry.yaml",
        "m1_registry": "registry.yaml",
        "route": "crepe_direct",
    }
    project_path.write_text(yaml.safe_dump(project_data, sort_keys=False), encoding="utf-8")
    return project_path


def test_corrupted_melody_registry_does_not_publish_package(tmp_path: Path) -> None:
    """R3-3 (Codex round3 P2 対応・all-build-then-publish の回復):
    `melody_experimental_plan_warnings`（呼び出し先 `load_m3_registry` 経由の
    `M3ComparisonConfig.from_registry`）が破損 registry で raise するとき、
    その raise は永続公開（`_atomic_publish_text_bundle`）より前に起きる
    ——failed invocation の package が builds_root に残置されないことを、
    公開先ディレクトリの不存在で直接証明する。"""
    # schema フィールドが不正な m3_comparison_registry.yaml（構造的に破損）
    # ——`M3ComparisonConfig.from_registry` が即座に `ValueError` を送出する。
    corrupted_m3_bytes = yaml.safe_dump({"schema": "not-a-real-schema"}).encode("utf-8")
    project_path = _add_melody_axis_policy_project(tmp_path, m3_registry_bytes=corrupted_m3_bytes)
    loaded = load_recast_project(project_path)
    package_dir = loaded.builds_root / "packages" / "edm@suno"
    assert not loaded.builds_root.exists()  # デモ fixture は builds/ を同梱しない

    with pytest.raises(ValueError):
        build_recast_plan_artifacts(loaded, variant="edm", backend="suno", publish=True)

    # melody 診断の raise が publish より前に起きたので、package/report は
    # 一切公開されていない（半端な公開が残置されない）。
    assert not package_dir.exists()
    assert not (package_dir / "performance_package.json").exists()
    assert not (package_dir / "compilation_report.json").exists()


def _valid_calibrated_m3_registry_bytes() -> bytes:
    """校正済み（``evidence_thresholds.status == "frozen"``）な最小 M3
    comparison registry の生 bytes を組み立てる（G1 を通過させる目的専用の
    テスト fixture・M3 凍結スキーマ実体とは無関係）。"""
    return yaml.safe_dump(
        {
            "schema": "m3-comparison/0.1",
            "registered_utc": "2026-07-31T00:00:00Z",
            "representation": {
                "pitch_quantization_semitones": 1,
                "contour_small_max_semitones": 2,
                "ioi_ratio_log2_step": 0.25,
                "duration_ratio_log2_step": 0.25,
                "chroma_fold_semitones": 12,
                "octave_artifact_divergence": 0.10,
            },
            "alignment": {
                "match_score": 1.0,
                "mismatch_score": -1.0,
                "gap_open": -1.0,
                "gap_extend": -0.5,
                "traceback_preference": ["diag", "up", "left"],
                "phrase_gap_sec": 0.6,
                "phrase_gap_score": 0.25,
            },
            "coverage": {"floor": 0.5, "floor_status": "frozen"},
            "evidence_thresholds": {
                "status": "frozen",
                "axes": {
                    axis: {"strong_min": 0.8, "none_max": 0.3}
                    for axis in ("contour", "interval", "rhythm")
                },
            },
            "separation_margin": {"min_same_minus_cross_margin": 0.15},
        }
    ).encode("utf-8")


def test_uncorrupted_melody_registry_still_publishes_package(tmp_path: Path) -> None:
    """回帰確認: axis_policy 付き melody anchor があっても registry が校正済み
    かつ正常なら、従来どおり package/report が公開される（R3-3 対応が
    正常系の publish を妨げないことの確認）。M1 registry も正常（既定の
    `REAL_M1_REGISTRY`）なので R8-2 の可用性診断も "ok" のまま。"""
    project_path = _add_melody_axis_policy_project(
        tmp_path, m3_registry_bytes=_valid_calibrated_m3_registry_bytes()
    )
    loaded = load_recast_project(project_path)
    package_dir = loaded.builds_root / "packages" / "edm@suno"

    artifacts = build_recast_plan_artifacts(loaded, variant="edm", backend="suno", publish=True)

    assert artifacts.result.plan.state_reached == "verified"
    assert (package_dir / "performance_package.json").exists()
    assert (package_dir / "compilation_report.json").exists()
    assert any(
        "melody anchor 'melody': experimental observability — ok" == w
        for w in artifacts.result.plan.warnings
    )


# --- R4-1 (Codex round4 P2): observation.enabled=False skips melody plan diagnosis --


def test_disabled_observation_skips_melody_plan_diagnosis_with_corrupted_registry(
    tmp_path: Path,
) -> None:
    """`observation.enabled: false`（generated-only 運用）では、破損した
    m3_comparison_registry.yaml があっても `recast plan` は melody 診断
    （`melody_experimental_plan_warnings`）を一切呼ばない——R3-2 の digest
    ガードと対称。従来は `observation.enabled` を見ずに常に呼んでいたため、
    generated-only 運用でも registry 破損だけで `recast plan` が
    `RecastError`/`ValueError` で失敗していた。"""
    corrupted_m3_bytes = yaml.safe_dump({"schema": "not-a-real-schema"}).encode("utf-8")
    project_path = _add_melody_axis_policy_project(
        tmp_path, m3_registry_bytes=corrupted_m3_bytes, observation_enabled=False
    )
    loaded = load_recast_project(project_path)
    package_dir = loaded.builds_root / "packages" / "edm@suno"

    artifacts = build_recast_plan_artifacts(loaded, variant="edm", backend="suno", publish=True)

    assert artifacts.result.plan.state_reached == "verified"
    assert (package_dir / "performance_package.json").exists()
    assert (package_dir / "compilation_report.json").exists()
    assert not any(
        w.startswith("melody anchor 'melody': experimental observability")
        for w in artifacts.result.plan.warnings
    )


def test_disabled_observation_skips_melody_plan_diagnosis_with_missing_registry(
    tmp_path: Path,
) -> None:
    """R4-1: registry ファイル自体が存在しない（`resolve_melody_observation_
    paths` が `RecastError` を送出する）ケースでも、`observation.enabled:
    false` なら melody 診断は呼ばれず plan は成功する。"""
    project_path = _add_melody_axis_policy_project(
        tmp_path, m3_registry_bytes=b"placeholder", observation_enabled=False
    )
    project_dir = project_path.parent
    (project_dir / "m3_comparison_registry.yaml").unlink()

    loaded = load_recast_project(project_path)
    artifacts = build_recast_plan_artifacts(loaded, variant="edm", backend="suno", publish=True)

    assert artifacts.result.plan.state_reached == "verified"
    assert not any(
        w.startswith("melody anchor 'melody': experimental observability")
        for w in artifacts.result.plan.warnings
    )
