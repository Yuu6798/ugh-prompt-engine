"""`recast/plan.py` の build_recast_plan テスト（PR2）。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from svp_rpe.recast import RecastError, load_recast_project
from svp_rpe.recast.loader import LoadedRecastProject, load_mode_overrides
from svp_rpe.recast.models import ModeOverridesConfig
from svp_rpe.recast.plan import (
    RecastPlanResult,
    _normalize_diagnostic,
    build_recast_plan,
    build_recast_plan_artifacts,
    mode_support_for_path,
)
from svp_rpe.recast.run_paths import collect_protected_input_paths
from svp_rpe.recast.state import load_recast_state, record_state

DEMO_PROJECT = Path("examples/recast/demo_project")
EXPECTED_PLAN = DEMO_PROJECT / "expected" / "recast_plan_edm_suno.json"


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


def test_unknown_observation_anchor_raises_recast_error(tmp_path: Path) -> None:
    """PR6: `observation.anchors` が identity manifest に無い anchor id を列挙
    している project は、manifest ロード直後（plan 段）に fail-closed で
    `RecastError` を送出する（`ObservationConfig` の重複拒否と同様の即時失敗
    — demo_project の manifest 側 anchor id は lyrics/melody/harmony のみ）。"""
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

    with pytest.raises(RecastError, match="does-not-exist"):
        build_recast_plan(loaded, variant="edm", backend="suno")


def test_known_observation_anchor_subset_is_accepted(tmp_path: Path) -> None:
    """`observation.anchors` が manifest に実在する anchor id の部分集合なら
    plan 段は通常どおり評価される（unknown 検証は id 集合の非部分集合のみを
    弾く — 空リストと全 anchor 列挙のどちらも通す既存契約を壊さない）。"""
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
