"""AR4 form 実観測バッチ (2026-07-20, MusicGen local 30s) fixture self-test.

`examples/arrangement/midnight_signal/observed/musicgen_form/` に committed された
実観測成果物一式（`ar4f_plan.yaml` / `ar4f_takes_manifest.json` /
`ar4f_observation_take{0,1}.json` / `ar4f_generation_timestamps.yaml` /
`ar4f_determinism_spot_check.yaml`）を schema readback + sha256 整合
（takes manifest の `audio_sha256` <-> observation report の
`generated_artifact.sha256`）で固定する回帰テスト。`tests/test_ar4_observed_fixture.py`
の型を踏襲するが、本バッチは AR2-3 解凍条件 (b)（form が存在する長尺 artifact）を
狙った 30s クリップ・structure anchor 追加済みの別 manifest
（`identity_manifest.form.yaml`）を使うため、既存 #191 fixture
（`observed/musicgen/`、`identity_manifest.yaml` 不変）とは別ファイルとして新設する
（既存ファイル・既存テストは変更しない）。本バッチも実 MusicGen 推論を伴う
（torch 必須・非決定論的モデルロード時間）ため CLI 再実行はしない — committed
JSON/YAML を読み込んで整合性だけを検査する（torch 非依存・not slow）。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import yaml

FIXTURE_DIR = Path("examples/arrangement/midnight_signal/observed/musicgen_form")

PLAN_PATH = FIXTURE_DIR / "ar4f_plan.yaml"
TAKES_MANIFEST_PATH = FIXTURE_DIR / "ar4f_takes_manifest.json"
OBSERVATION_PATHS = {
    0: FIXTURE_DIR / "ar4f_observation_take0.json",
    1: FIXTURE_DIR / "ar4f_observation_take1.json",
}
TIMESTAMPS_PATH = FIXTURE_DIR / "ar4f_generation_timestamps.yaml"
SPOT_CHECK_PATH = FIXTURE_DIR / "ar4f_determinism_spot_check.yaml"

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


def test_all_committed_ar4f_files_exist_and_parse() -> None:
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


# --- 2. takes manifest: schema shape (30s / seed 8100 系列) --------------------------


def test_takes_manifest_has_expected_shape_for_both_takes() -> None:
    manifest = _load_json(TAKES_MANIFEST_PATH)

    assert manifest["fixture_id"] == "ar4f_musicgen_midnight_signal_edm_form"
    assert manifest["model_id"] == "facebook/musicgen-small"
    assert manifest["model_revision"] == "4c8334b02c6ec4e8664a91979669a501ec497792"
    assert manifest["duration_seconds"] == 30.0
    assert isinstance(manifest["prompt"], str) and manifest["prompt"].strip()
    assert "performance_package" in manifest
    assert len(manifest["performance_package"]["sha256"]) == 64

    samples = manifest["samples"]
    assert [sample["sample_id"] for sample in samples] == ["take0", "take1"]
    assert [sample["take_index"] for sample in samples] == [0, 1]
    # seed 系列 8100 + take_index — #191 の 8000 系列と非衝突の新レンジ
    # (ar4f_plan.yaml#seeds.formula と同一定義)。
    assert [sample["seed"] for sample in samples] == [8100, 8101]
    for sample in samples:
        assert sample["duration_seconds"] == 30.0
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


# --- 4. observation report: D-1 3分岐のみ / harmony + structure が実測されていること --


def test_observation_reports_only_use_the_three_registered_determination_branches() -> None:
    for path in OBSERVATION_PATHS.values():
        report = _load_json(path)
        anchors = {anchor["anchor_id"]: anchor for anchor in report["anchors"]}

        # #191 の {lyrics, melody, harmony} に structure anchor が加わっている点が
        # 本バッチ固有の差分（identity_manifest.form.yaml が structure anchor を
        # 追加宣言しているため）。
        assert set(anchors) == {"lyrics", "melody", "harmony", "structure"}
        for anchor in anchors.values():
            assert anchor["adherence_status"] in _VALID_ADHERENCE_STATUS
            assert anchor["determination"] in _VALID_DETERMINATION

        # lyrics/melody: センサー未配線（既存 AR4 配線の既知の限界、fixture 不変）
        assert anchors["lyrics"]["determination"] == "no_sensor"
        assert anchors["melody"]["determination"] == "no_sensor"

        # harmony/structure: 両方とも実測された（sensor.available == True）——
        # 判定の事前登録どおり、preserved を成功条件にはしていない。本バッチの
        # 実測ではどちらも not_observed/deferred だった（30s クリップは正典
        # harmony/structure 系列を完全再現しなかった）— この事実そのものが
        # AR2-3 解凍条件 (b) の判定材料であり、fixture として固定する。
        harmony = anchors["harmony"]
        assert harmony["sensor"]["available"] is True
        assert "full_cycles" in harmony["measurements"]

        structure = anchors["structure"]
        assert structure["sensor"]["available"] is True
        assert structure["sensor"]["name"] == "section_sequence_match"
        assert structure["measurements"]["canonical_sections"] == [
            "intro",
            "verse",
            "chorus",
            "bridge",
        ]
        assert "sequence_exact_match" in structure["measurements"]
        assert "position_match_rate" in structure["measurements"]


def test_observation_report_generated_artifact_path_is_machine_independent() -> None:
    """`generated_artifact.path` は `observe` の `<audio>` 引数文字列をそのまま記録する
    （`svp_rpe/cli/observe_cmd.py` の `generated_artifact_path=audio`）。committed
    provenance fixture でビルド機固有の絶対パス（例: `/tmp/...` scratch ディレクトリ）が
    焼き込まれると、他の checkout/scratch から再観測した際に path フィールドだけが
    ドリフトし byte reproducibility が壊れる（#191 の教訓、恒久ゲート）。本バッチの
    observe 実行は Phase 2/4 で生成物ディレクトリを cwd にして相対パス引数
    （`take{N}.wav`）を渡すことでこれを満たした — その事実をここで固定する。
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
        # 本 fixture も #191 と同様、fresh-process 2/2 byte 一致を canonical の
        # 根拠として主張している — `match` の自己整合性だけでなく一致そのものを
        # enforce する。
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

    # seed 系列 (8100 + take_index) は ar4f_plan.yaml の宣言と一致する。
    seeds_by_take = {call["take_index"]: call["seed"] for call in generation_calls}
    assert seeds_by_take == {0: 8100, 1: 8101}


# --- 7. 既存 fixture (identity_manifest.yaml / observed/musicgen/) を巻き込んでいないこと --


def test_new_fixture_files_do_not_touch_the_existing_ar4_musicgen_batch() -> None:
    """Design Memo のスコープ境界（既存 committed fixture の非改変）を、
    #191 fixture が今も存在し中身が変わっていないことで直接確認する。"""
    legacy_dir = Path("examples/arrangement/midnight_signal/observed/musicgen")
    legacy_manifest = _load_json(legacy_dir / "ar4_takes_manifest.json")
    assert legacy_manifest["fixture_id"] == "ar4_musicgen_midnight_signal_edm"
    assert [sample["seed"] for sample in legacy_manifest["samples"]] == [8000, 8001]

    legacy_identity_manifest = Path(
        "examples/arrangement/midnight_signal/identity_manifest.yaml"
    )
    assert legacy_identity_manifest.is_file()
    legacy_manifest_data = yaml.safe_load(legacy_identity_manifest.read_text(encoding="utf-8"))
    anchor_ids = {anchor["id"] for anchor in legacy_manifest_data["anchors"]}
    assert anchor_ids == {"lyrics", "melody", "harmony"}, (
        "identity_manifest.yaml must stay unchanged (no structure anchor) — the "
        "structure anchor lives only in the new identity_manifest.form.yaml variant"
    )
