"""AR4 実 Suno 検収デモ (2026-07-19, Suno 5.5 A/B, section-tag script 有無) fixture self-test.

`examples/arrangement/midnight_signal/observed/suno/` に committed された、実 Suno
生成物での AR4 初観測（非 canonical 探索・n=2×2・記述統計のみ）成果物一式
（`order_sheet.md` / `takes_memo.yaml` / `summary_raw.md` / `observed/suno_{A1,A2,B1,B2}_observation.json`
/ `package/performance_package.json` / `package/compilation_report.json` /
`inputs/identity_manifest.demo.yaml` / `inputs/edm.identity.suno.arrangement.yaml` /
`inputs/identity/section_map.v2.json` / `inputs/composition_score.yaml` /
`inputs/identity/{lyrics.txt,melody_notes.json,chord_progression.json}`）を、
`tests/test_ar4_observed_fixture.py` / `tests/test_ar4_form_observed_fixture.py` の型を
踏襲して sha256 整合 + schema readback で固定する回帰テスト。

本バッチは実 Suno 生成（API キー不要のローカル計器のみ・生成自体は非決定論的な
外部サービス呼び出し）を伴うため CLI 再実行はしない — committed JSON/YAML を
読み込んで整合性だけを検査する（音声・抽出非依存・not slow）。音源 4 本自体は
非コミット（sha256 pin のみ、`takes_memo.yaml` と各観測 JSON の
`generated_artifact.sha256` に記録）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from svp_rpe.arrange.observe import ObservationReport

FIXTURE_DIR = Path("examples/arrangement/midnight_signal/observed/suno")

ORDER_SHEET_PATH = FIXTURE_DIR / "order_sheet.md"
TAKES_MEMO_PATH = FIXTURE_DIR / "takes_memo.yaml"
SUMMARY_RAW_PATH = FIXTURE_DIR / "summary_raw.md"
OBSERVATION_PATHS = {
    "A1": FIXTURE_DIR / "observed" / "suno_A1_observation.json",
    "A2": FIXTURE_DIR / "observed" / "suno_A2_observation.json",
    "B1": FIXTURE_DIR / "observed" / "suno_B1_observation.json",
    "B2": FIXTURE_DIR / "observed" / "suno_B2_observation.json",
}
PACKAGE_PATH = FIXTURE_DIR / "package" / "performance_package.json"
COMPILATION_REPORT_PATH = FIXTURE_DIR / "package" / "compilation_report.json"
MANIFEST_PATH = FIXTURE_DIR / "inputs" / "identity_manifest.demo.yaml"
ARRANGEMENT_SPEC_PATH = FIXTURE_DIR / "inputs" / "edm.identity.suno.arrangement.yaml"
SECTION_MAP_PATH = FIXTURE_DIR / "inputs" / "identity" / "section_map.v2.json"

FIXTURE_COMPOSITION_SCORE_PATH = FIXTURE_DIR / "inputs" / "composition_score.yaml"
FIXTURE_LYRICS_PATH = FIXTURE_DIR / "inputs" / "identity" / "lyrics.txt"
FIXTURE_MELODY_PATH = FIXTURE_DIR / "inputs" / "identity" / "melody_notes.json"
FIXTURE_CHORD_PATH = FIXTURE_DIR / "inputs" / "identity" / "chord_progression.json"

REPO_ROOT = Path("examples/arrangement/midnight_signal")
REPO_COMPOSITION_SCORE_PATH = REPO_ROOT / "composition_score.yaml"
REPO_LYRICS_PATH = REPO_ROOT / "identity" / "lyrics.txt"
REPO_MELODY_PATH = REPO_ROOT / "identity" / "melody_notes.json"
REPO_CHORD_PATH = REPO_ROOT / "identity" / "chord_progression.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- 0. 事前登録・provenance の周辺ファイルが存在し読める -----------------------------


def test_order_sheet_and_takes_memo_and_summary_exist() -> None:
    assert ORDER_SHEET_PATH.is_file()
    assert SUMMARY_RAW_PATH.is_file()
    memo = _load_yaml(TAKES_MEMO_PATH)
    assert [take["assigned_name"] for take in memo["takes"]] == [
        "suno_A1",
        "suno_A2",
        "suno_B1",
        "suno_B2",
    ]


# --- 1. 観測 JSON 4 本が ObservationReport schema でロード可能 -------------------------


def test_all_observation_reports_load_against_observation_report_schema() -> None:
    for take_id, path in OBSERVATION_PATHS.items():
        raw = _load_json(path)
        report = ObservationReport.model_validate(raw)
        assert report.schema_version == "observation-report/0.1"
        assert {anchor.anchor_id for anchor in report.anchors} == {
            "lyrics",
            "melody",
            "harmony",
            "structure",
        }, f"{take_id}: unexpected anchor id set"


# --- 2. package_sha256 == committed performance_package.json の実 sha256 ----------------


def test_all_observation_reports_agree_on_package_sha256_with_committed_package() -> None:
    package_sha256 = _sha256(PACKAGE_PATH)

    for take_id, path in OBSERVATION_PATHS.items():
        report = _load_json(path)
        assert report["package_sha256"] == package_sha256, (
            f"{take_id}: package_sha256 does not match committed "
            f"performance_package.json sha256"
        )

    compilation_report = _load_json(COMPILATION_REPORT_PATH)
    assert compilation_report["package_sha256"] == package_sha256


# --- 3. package.inputs.identity_manifest.sha256 == committed manifest の実 sha256 -------


def test_package_identity_manifest_sha256_matches_committed_manifest() -> None:
    package = _load_json(PACKAGE_PATH)
    manifest_sha256 = _sha256(MANIFEST_PATH)
    assert (
        package["inputs"]["identity_manifest"]["sha256"] == manifest_sha256
    ), "package.inputs.identity_manifest.sha256 does not match committed manifest sha256"


# --- 4. manifest 各 anchor の sha256 pin == inputs/identity/ 配下の実 sha256 ------------


def test_manifest_anchor_sha256_pins_match_identity_artifacts() -> None:
    manifest = _load_yaml(MANIFEST_PATH)

    source = manifest["source"]
    assert source["locator"] == "composition_score.yaml"
    assert source["sha256"] == _sha256(FIXTURE_COMPOSITION_SCORE_PATH)

    artifact_by_anchor = {
        "lyrics": FIXTURE_LYRICS_PATH,
        "melody": FIXTURE_MELODY_PATH,
        "harmony": FIXTURE_CHORD_PATH,
        "structure": SECTION_MAP_PATH,
    }
    anchors_by_id = {anchor["id"]: anchor for anchor in manifest["anchors"]}
    assert set(anchors_by_id) == set(artifact_by_anchor)

    for anchor_id, artifact_path in artifact_by_anchor.items():
        anchor = anchors_by_id[anchor_id]
        assert anchor["sha256"] == _sha256(artifact_path), (
            f"anchor {anchor_id!r}: manifest sha256 pin does not match "
            f"{artifact_path} actual sha256"
        )


# --- 5. inputs/ 直下の主要ファイルが repo 正本とバイト同一 ------------------------------


def test_fixture_inputs_are_byte_identical_to_repo_canonical_files() -> None:
    pairs = [
        (FIXTURE_COMPOSITION_SCORE_PATH, REPO_COMPOSITION_SCORE_PATH),
        (FIXTURE_LYRICS_PATH, REPO_LYRICS_PATH),
        (FIXTURE_MELODY_PATH, REPO_MELODY_PATH),
        (FIXTURE_CHORD_PATH, REPO_CHORD_PATH),
    ]
    for fixture_path, repo_path in pairs:
        assert repo_path.is_file(), f"repo canonical file missing: {repo_path}"
        assert fixture_path.read_bytes() == repo_path.read_bytes(), (
            f"{fixture_path} is not byte-identical to repo canonical {repo_path}"
        )


# --- 6. takes_memo.yaml の各 take sha256 == 対応する観測 JSON の generated_artifact.sha256 --


def test_takes_memo_sha256_matches_observation_generated_artifact_sha256() -> None:
    memo = _load_yaml(TAKES_MEMO_PATH)
    memo_sha_by_name = {take["assigned_name"]: take["sha256"] for take in memo["takes"]}

    for take_id, path in OBSERVATION_PATHS.items():
        report = _load_json(path)
        assigned_name = f"suno_{take_id}"
        assert (
            report["generated_artifact"]["sha256"] == memo_sha_by_name[assigned_name]
        ), f"{take_id}: takes_memo.yaml sha256 does not match observation generated_artifact.sha256"


# --- 7. 非 canonical 探索の位置づけを pin する（4 take とも not_observed/deferred） ------


def test_all_takes_are_not_observed_deferred_and_sequence_not_exact() -> None:
    """本デモは事前登録どおり非 canonical 探索・記述統計のみで、`preserved` /
    `exact_match` を成功条件にはしていない。4 take とも harmony/structure が
    `not_observed`/`deferred` で、`sequence_exact_match` が False だったという
    実測結果そのものを fixture として固定する（#194 D-1 閾値 Design Memo の
    試金石データであり、将来の再生成でこの事実が変わってはならない）。
    """
    for take_id, path in OBSERVATION_PATHS.items():
        report = _load_json(path)
        anchors = {anchor["anchor_id"]: anchor for anchor in report["anchors"]}

        for domain in ("harmony", "structure"):
            anchor = anchors[domain]
            assert anchor["adherence_status"] == "not_observed", (
                f"{take_id}/{domain}: expected adherence_status == 'not_observed', "
                f"got {anchor['adherence_status']!r}"
            )
            assert anchor["determination"] == "deferred", (
                f"{take_id}/{domain}: expected determination == 'deferred', "
                f"got {anchor['determination']!r}"
            )

        assert anchors["structure"]["measurements"]["sequence_exact_match"] is False, (
            f"{take_id}: expected structure.measurements.sequence_exact_match is False"
        )

        # lyrics/melody: センサー未配線（既存 AR4 配線の既知の限界、他バッチと不変）
        assert anchors["lyrics"]["determination"] == "no_sensor"
        assert anchors["melody"]["determination"] == "no_sensor"


# --- 8. arrangement spec / section map も schema-readable であること --------------------


def test_arrangement_spec_and_section_map_are_readable() -> None:
    spec = _load_yaml(ARRANGEMENT_SPEC_PATH)
    assert isinstance(spec, dict) and spec

    section_map = _load_json(SECTION_MAP_PATH)
    assert isinstance(section_map, dict) and section_map
