"""AR4 実観測バッチ (2026-07-19, MusicGen local) fixture self-test.

`examples/arrangement/midnight_signal/observed/musicgen/` に committed された
AR4 実観測成果物一式（`ar4_plan.yaml` / `ar4_takes_manifest.json` /
`ar4_observation_take{0,1}.json` / `ar4_generation_timestamps.yaml` /
`ar4_determinism_spot_check.yaml`）を schema readback + sha256 整合
（takes manifest の `audio_sha256` <-> observation report の
`generated_artifact.sha256`）で固定する回帰テスト。`tests/test_arrangement_examples.py`
の committed-fixture self-test の型を踏襲するが、AR4 は実 MusicGen 推論
（torch 必須・非決定論的モデルロード時間）を伴うため CLI 再実行はしない —
committed JSON/YAML を読み込んで整合性だけを検査する（torch 非依存・not slow）。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import yaml

FIXTURE_DIR = Path("examples/arrangement/midnight_signal/observed/musicgen")

PLAN_PATH = FIXTURE_DIR / "ar4_plan.yaml"
TAKES_MANIFEST_PATH = FIXTURE_DIR / "ar4_takes_manifest.json"
OBSERVATION_PATHS = {
    0: FIXTURE_DIR / "ar4_observation_take0.json",
    1: FIXTURE_DIR / "ar4_observation_take1.json",
}
TIMESTAMPS_PATH = FIXTURE_DIR / "ar4_generation_timestamps.yaml"
SPOT_CHECK_PATH = FIXTURE_DIR / "ar4_determinism_spot_check.yaml"

_VALID_ADHERENCE_STATUS = {"preserved", "not_observed"}
_VALID_DETERMINATION = {"exact_match", "deferred", "no_sensor"}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _parse_utc(value: str) -> datetime:
    assert value.endswith("Z"), f"expected UTC 'Z' suffix, got {value!r}"
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


# --- 1. schema readback: 全ファイルが期待どおりパースできる ------------------------


def test_all_committed_ar4_files_exist_and_parse() -> None:
    plan = _load_yaml(PLAN_PATH)
    manifest = _load_json(TAKES_MANIFEST_PATH)
    timestamps = _load_yaml(TIMESTAMPS_PATH)
    spot_check = _load_yaml(SPOT_CHECK_PATH)

    assert plan["schema_version"] == "1.0"
    assert manifest["schema_version"] == "1.0"
    assert timestamps["schema_version"] == "1.0"
    assert spot_check["schema_version"] == "1.0"

    for take_index, path in OBSERVATION_PATHS.items():
        report = _load_json(path)
        assert report["schema_version"] == "observation-report/0.1"


# --- 2. takes manifest: schema shape ------------------------------------------------


def test_takes_manifest_has_expected_shape_for_both_takes() -> None:
    manifest = _load_json(TAKES_MANIFEST_PATH)

    assert manifest["fixture_id"] == "ar4_musicgen_midnight_signal_edm"
    assert manifest["model_id"] == "facebook/musicgen-small"
    assert manifest["model_revision"] == "4c8334b02c6ec4e8664a91979669a501ec497792"
    assert isinstance(manifest["prompt"], str) and manifest["prompt"].strip()
    assert "performance_package" in manifest
    assert len(manifest["performance_package"]["sha256"]) == 64

    samples = manifest["samples"]
    assert [sample["sample_id"] for sample in samples] == ["take0", "take1"]
    assert [sample["take_index"] for sample in samples] == [0, 1]
    assert [sample["seed"] for sample in samples] == [8000, 8001]
    for sample in samples:
        assert len(sample["audio_sha256"]) == 64
        assert sample["audio_sha256"] != "0" * 64


# --- 3. sha256 整合: takes manifest <-> observation report --------------------------


def test_manifest_audio_sha256_matches_observation_generated_artifact_sha256() -> None:
    manifest = _load_json(TAKES_MANIFEST_PATH)
    manifest_sha_by_take = {
        sample["take_index"]: sample["audio_sha256"] for sample in manifest["samples"]
    }

    for take_index, path in OBSERVATION_PATHS.items():
        report = _load_json(path)
        assert (
            report["generated_artifact"]["sha256"] == manifest_sha_by_take[take_index]
        ), f"take{take_index}: manifest audio_sha256 does not match observation generated_artifact.sha256"


def test_observation_reports_agree_on_package_sha256_with_manifest_and_plan() -> None:
    manifest = _load_json(TAKES_MANIFEST_PATH)
    plan = _load_yaml(PLAN_PATH)
    package_sha256 = manifest["performance_package"]["sha256"]

    assert plan["prompt_source"]["performance_package_sha256"] == package_sha256

    for path in OBSERVATION_PATHS.values():
        report = _load_json(path)
        assert report["package_sha256"] == package_sha256


# --- 4. observation report: D-1 3分岐のみ / harmony が実測されていること -----------


def test_observation_reports_only_use_the_three_registered_determination_branches() -> None:
    for path in OBSERVATION_PATHS.values():
        report = _load_json(path)
        anchors = {anchor["anchor_id"]: anchor for anchor in report["anchors"]}

        assert set(anchors) == {"lyrics", "melody", "harmony"}
        for anchor in anchors.values():
            assert anchor["adherence_status"] in _VALID_ADHERENCE_STATUS
            assert anchor["determination"] in _VALID_DETERMINATION

        # lyrics/melody: センサー未配線（既存 AR4 配線の既知の限界、fixture 不変）
        assert anchors["lyrics"]["determination"] == "no_sensor"
        assert anchors["melody"]["determination"] == "no_sensor"

        # harmony: 実測された（sensor.available == True）— これが AR4 実観測の核心
        harmony = anchors["harmony"]
        assert harmony["sensor"]["available"] is True
        assert "full_cycles" in harmony["measurements"]


def test_observation_report_generated_artifact_path_is_machine_independent() -> None:
    """`generated_artifact.path` は `observe` の `<audio>` 引数文字列をそのまま記録する
    （`svp_rpe/cli/observe_cmd.py` の `generated_artifact_path=audio`）。committed
    provenance fixture でビルド機固有の絶対パス（例: `/tmp/...` scratch ディレクトリ）が
    焼き込まれると、他の checkout/scratch から再観測した際に path フィールドだけが
    ドリフトし byte reproducibility が壊れる（Codex PR #191 round 2 review,
    discussion_r3610138834）。恒久ゲートとして、先頭が `/` の絶対パスでないこと、
    かつ `/tmp/` を含まないことを固定する。
    """
    for path in OBSERVATION_PATHS.values():
        report = _load_json(path)
        artifact_path = report["generated_artifact"]["path"]
        assert not artifact_path.startswith("/"), (
            f"{path}: generated_artifact.path must not be an absolute path, "
            f"got {artifact_path!r}"
        )
        assert "/tmp/" not in artifact_path, (
            f"{path}: generated_artifact.path must not leak a machine-specific "
            f"scratch directory, got {artifact_path!r}"
        )


# --- 5. 決定論スポット検証: pinned == regenerated ------------------------------------


def test_determinism_spot_check_matches_manifest_pinned_sha256() -> None:
    manifest = _load_json(TAKES_MANIFEST_PATH)
    spot_check = _load_yaml(SPOT_CHECK_PATH)
    manifest_sha_by_id = {sample["sample_id"]: sample["audio_sha256"] for sample in manifest["samples"]}

    assert len(spot_check["checks"]) == len(manifest["samples"])  # n=2 全数
    for check in spot_check["checks"]:
        assert check["pinned_sha256"] == manifest_sha_by_id[check["sample_id"]]
        assert check["match"] == (check["pinned_sha256"] == check["regenerated_sha256"])
        # 本 fixture は fresh-process 2/2 byte 一致を canonical の根拠として主張して
        # いるため、`match` の自己整合性だけでなく一致そのものを enforce する —
        # 将来 AR4 が再生成されて乖離した場合にこのゲートで fail させる
        # （Codex P2 review #191, discussion_r3610116230）。
        assert check["match"] is True
        assert check["regenerated_sha256"] == check["pinned_sha256"]


# --- 6. 事前登録が生成に先行する（実測 UTC タイムスタンプで裏付け）------------------


def test_preregistration_precedes_generation_which_precedes_nothing_later() -> None:
    plan = _load_yaml(PLAN_PATH)
    timestamps = _load_yaml(TIMESTAMPS_PATH)

    plan_confirmed_at = _parse_utc(plan["plan_confirmed_at_utc"])
    generation_calls = timestamps["generation_calls"]
    assert len(generation_calls) == 2

    for call in generation_calls:
        started_at = _parse_utc(call["started_at_utc"])
        ended_at = _parse_utc(call["ended_at_utc"])
        assert plan_confirmed_at <= started_at, (
            "Phase 1 preregistration must precede Phase 2 generation "
            f"(plan_confirmed_at_utc={plan_confirmed_at!r}, "
            f"generation started_at_utc={started_at!r})"
        )
        assert started_at <= ended_at

    # seed 系列 (8000 + take_index) は ar4_plan.yaml の宣言と一致する。
    seeds_by_take = {call["take_index"]: call["seed"] for call in generation_calls}
    assert seeds_by_take == {0: 8000, 1: 8001}
