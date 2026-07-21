"""WI1 MusicGen 無人バッチ n=20 (2026-07-21, structure/harmony 逸脱分布) fixture
self-test + 検算照合ゲート (`docs/wi1_d1_thresholds.md` §9)。

`examples/arrangement/midnight_signal/observed/wi1_musicgen_n20/` に committed
された、WI1 事前登録バッチ（`wi1_plan.yaml` / `wi1_generation_timestamps.yaml` /
`wi1_takes_manifest.json` / `wi1_observation_take{0..19}.json` /
`wi1_determinism_spot_check.yaml`）を、`tests/test_wi0b_synth_observed_fixture.py` /
`tests/test_ar4_suno_observed_fixture.py` の型を踏襲して固定する回帰テスト。

wav 20 本自体は非コミット（sha256 pin のみ、#196 前例）— 本テストは wav
ファイルに一切依存せず、committed JSON/YAML のみを読み込む。

本ファイルの中心は (B) 検算照合ゲート: `docs/wi1_d1_thresholds.md` §4 の
structure 逸脱編集分解アルゴリズム、および §5 の D-1 分類規約 (v0, 参照ポリシー)
を **ここで独立に再実装** し、committed observation JSON の
`observed_sections`（正規化後）から再計算した結果を Memo の数表と照合する
(AGENTS.md §8 検算照合ゲート)。src/ 側には一切実装を置かない（実配線は WI4）。
数値が 1 つでも Memo と食い違えば、このテストは修正せず失敗を報告する目的で
書かれている。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from svp_rpe.arrange.observe import ObservationReport

FIXTURE_DIR = Path("examples/arrangement/midnight_signal/observed/wi1_musicgen_n20")

PLAN_PATH = FIXTURE_DIR / "wi1_plan.yaml"
TIMESTAMPS_PATH = FIXTURE_DIR / "wi1_generation_timestamps.yaml"
MANIFEST_PATH = FIXTURE_DIR / "wi1_takes_manifest.json"
SPOT_CHECK_PATH = FIXTURE_DIR / "wi1_determinism_spot_check.yaml"

N_TAKES = 20

# 手計算アンカー (docs/wi1_d1_thresholds.md §4 の表) の対象 fixture。
AR4F_TAKE0_PATH = Path(
    "examples/arrangement/midnight_signal/observed/musicgen_form/ar4f_observation_take0.json"
)
AR4F_TAKE1_PATH = Path(
    "examples/arrangement/midnight_signal/observed/musicgen_form/ar4f_observation_take1.json"
)
SUNO_A2_PATH = Path(
    "examples/arrangement/midnight_signal/observed/suno/observed/suno_A2_observation.json"
)
SUNO_B1_PATH = Path(
    "examples/arrangement/midnight_signal/observed/suno/observed/suno_B1_observation.json"
)

CANONICAL_SECTIONS = ["intro", "verse", "chorus", "bridge"]

# docs/wi1_d1_thresholds.md §3.1 の分布表 (観測パターン -> take index 集合)。
EXPECTED_PATTERNS: dict[tuple[str, ...], tuple[int, ...]] = {
    ("intro", "outro"): (0, 9, 11, 17),
    ("intro", "chorus", "outro"): (3, 7, 10, 12, 13, 14, 15, 16),
    ("intro", "chorus", "chorus", "outro"): (1, 2, 4, 5, 19),
    ("intro", "chorus", "bridge", "chorus", "outro"): (8,),
    ("intro", "bridge", "chorus", "chorus", "outro"): (6, 18),
}

# 非 reorder パターンの (omission, insertion, repetition) 期待値 (§3.1 / §5)。
EXPECTED_DECOMPOSITION: dict[tuple[str, ...], tuple[int, int, int]] = {
    ("intro", "outro"): (3, 1, 0),
    ("intro", "chorus", "outro"): (2, 1, 0),
    ("intro", "chorus", "chorus", "outro"): (2, 1, 1),
    ("intro", "chorus", "bridge", "chorus", "outro"): (1, 1, 1),
}

REORDER_PATTERN = ("intro", "bridge", "chorus", "chorus", "outro")
REORDER_TAKES = frozenset({6, 18})

# §5 参照ポリシー下の分類結果 (n=20 機械適用)。
EXPECTED_OUTSIDE_TAKES = frozenset({0, 9, 11, 17, 6, 18})
EXPECTED_WITHIN_COUNT = 14
EXPECTED_OUTSIDE_COUNT = 6


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _observation_path(take_index: int) -> Path:
    return FIXTURE_DIR / f"wi1_observation_take{take_index}.json"


# --- §4 編集分解アルゴリズムのテストローカル独立実装 ---------------------------------
# (docs/wi1_d1_thresholds.md §4。src/ には置かない — WI4 で実配線する際に初めて
# src 側の実装が生まれる。ここでの独立実装は検算照合の生地であり、それ自体が
# 製品コードではない。)


def _first_occurrence_sequence(observed: list[str], canonical: list[str]) -> list[str]:
    """F(O): O から canonical に属する各ラベルの初出のみを順に取った列。"""
    canonical_set = set(canonical)
    seen: set[str] = set()
    result: list[str] = []
    for label in observed:
        if label in canonical_set and label not in seen:
            seen.add(label)
            result.append(label)
    return result


def _is_order_preserving_subsequence(f_o: list[str], canonical: list[str]) -> bool:
    """F(O) が canonical の (連続とは限らない) 部分列であれば True。"""
    indices = [canonical.index(label) for label in f_o]
    return all(indices[i] < indices[i + 1] for i in range(len(indices) - 1))


def _decompose(observed: list[str], canonical: list[str]) -> dict:
    """§4 の編集分解。reorder なら {"reorder": True} のみを返す（分解は未定義）。"""
    f_o = _first_occurrence_sequence(observed, canonical)
    if not _is_order_preserving_subsequence(f_o, canonical):
        return {"reorder": True}

    canonical_set = set(canonical)
    omission = len(canonical) - len(f_o)
    insertion = sum(1 for label in observed if label not in canonical_set)
    repetition = sum(1 for label in observed if label in canonical_set) - len(f_o)
    return {
        "reorder": False,
        "omission": omission,
        "insertion": insertion,
        "repetition": repetition,
        "f_o": f_o,
    }


def _classify(observed: list[str], canonical: list[str]) -> str:
    """§5 の D-1 分類規約 v0 (参照ポリシー下)。3 段ゲート。"""
    if len(observed) == 0:
        return "not_observed"  # ゲート 0

    decomp = _decompose(observed, canonical)
    if decomp["reorder"]:
        return "changed_outside_policy"  # ゲート 1: reorder は語彙外確定

    if decomp["insertion"] > 1:
        return "changed_outside_policy"  # ゲート 2: 挿入予算
    if len(decomp["f_o"]) < 2:
        return "changed_outside_policy"  # ゲート 2: 正典被覆床 |F(O)| >= 2

    return "changed_within_policy"


# --- (A) fixture 整合性 -------------------------------------------------------------


def test_twenty_observation_files_exist_and_load_against_observation_report_schema() -> None:
    for take_index in range(N_TAKES):
        path = _observation_path(take_index)
        assert path.is_file(), f"missing observation file: {path}"
        raw = _load_json(path)
        report = ObservationReport.model_validate(raw)
        assert report.schema_version == "observation-report/0.1"
        assert report.work_id == "midnight-signal"


def test_plan_confirmed_at_utc_precedes_all_generation_start_timestamps() -> None:
    plan = _load_yaml(PLAN_PATH)
    plan_confirmed_at_utc = plan["plan_confirmed_at_utc"]
    assert plan_confirmed_at_utc == "2026-07-21T03:49:01Z"

    timestamps = _load_yaml(TIMESTAMPS_PATH)
    calls = timestamps["generation_calls"]
    assert len(calls) == N_TAKES

    for call in calls:
        assert plan_confirmed_at_utc < call["started_at_utc"], (
            f"{call['sample_id']}: started_at_utc does not follow "
            f"plan_confirmed_at_utc (pre-registration must precede generation)"
        )


def test_takes_manifest_has_twenty_entries_with_distinct_sha256_pins() -> None:
    manifest = _load_json(MANIFEST_PATH)
    samples = manifest["samples"]
    assert len(samples) == N_TAKES

    shas = [sample["audio_sha256"] for sample in samples]
    for sha in shas:
        assert re.fullmatch(r"[0-9a-f]{64}", sha), f"not a well-formed sha256 hex digest: {sha}"
    assert len(set(shas)) == N_TAKES, "audio_sha256 pins are not all distinct"

    assert [sample["take_index"] for sample in samples] == list(range(N_TAKES))


def test_determinism_spot_check_records_two_of_two_fresh_process_matches() -> None:
    spot_check = _load_yaml(SPOT_CHECK_PATH)
    checks = spot_check["checks"]
    assert {check["sample_id"] for check in checks} == {"take0", "take19"}

    for check in checks:
        assert check["match"] is True
        assert check["pinned_sha256"] == check["regenerated_sha256"]

    assert spot_check["summary"] == "2/2 fresh-process regenerations matched the canonical pinned sha256 exactly."


# --- provenance 突合 (Codex P2 #200 review r3619778609 / r3619778615): 各 observation
# report / spot check を wi1_takes_manifest.json の sha256 pin に紐付け、pattern/
# classification 検算が別 WAV・別 package の混入報告を静かに通さないことを保証する
# (tests/test_ar4_form_observed_fixture.py の
# `test_manifest_audio_sha256_matches_observation_generated_artifact_sha256` /
# `test_observation_reports_agree_on_package_sha256_with_manifest_and_plan` /
# `test_determinism_spot_check_matches_manifest_pinned_sha256` と同型)。


def test_observation_reports_agree_with_manifest_audio_sha256_and_package_sha256_pin() -> None:
    manifest = _load_json(MANIFEST_PATH)
    plan = _load_yaml(PLAN_PATH)

    manifest_sha_by_take = {
        sample["take_index"]: sample["audio_sha256"] for sample in manifest["samples"]
    }
    assert len(manifest_sha_by_take) == N_TAKES

    package_sha256 = manifest["performance_package"]["sha256"]
    assert plan["prompt_source"]["performance_package_sha256"] == package_sha256

    for take_index in range(N_TAKES):
        report = _load_json(_observation_path(take_index))
        assert (
            report["generated_artifact"]["sha256"] == manifest_sha_by_take[take_index]
        ), (
            f"take{take_index}: observation generated_artifact.sha256 does not match "
            "wi1_takes_manifest.json audio_sha256 pin"
        )
        assert report["package_sha256"] == package_sha256, (
            f"take{take_index}: observation package_sha256 does not match "
            "wi1_takes_manifest.json performance_package.sha256 pin"
        )


def test_determinism_spot_check_matches_manifest_pinned_sha256() -> None:
    manifest = _load_json(MANIFEST_PATH)
    spot_check = _load_yaml(SPOT_CHECK_PATH)

    manifest_sha_by_id = {
        sample["sample_id"]: sample["audio_sha256"] for sample in manifest["samples"]
    }

    checks = spot_check["checks"]
    assert {check["sample_id"] for check in checks} == {"take0", "take19"}
    for check in checks:
        assert check["pinned_sha256"] == manifest_sha_by_id[check["sample_id"]], (
            f"{check['sample_id']}: spot check pinned_sha256 does not match "
            "wi1_takes_manifest.json audio_sha256 pin"
        )


# --- (B) 検算照合ゲート (本命): §4/§5 の独立再実装で Memo の数表と照合 -----------------


def _load_all_structure_observations() -> dict[int, list[str]]:
    result: dict[int, list[str]] = {}
    for take_index in range(N_TAKES):
        raw = _load_json(_observation_path(take_index))
        anchors = {anchor["anchor_id"]: anchor for anchor in raw["anchors"]}
        result[take_index] = anchors["structure"]["measurements"]["observed_sections"]
    return result


def _load_all_harmony_observations() -> dict[int, dict]:
    result: dict[int, dict] = {}
    for take_index in range(N_TAKES):
        raw = _load_json(_observation_path(take_index))
        anchors = {anchor["anchor_id"]: anchor for anchor in raw["anchors"]}
        result[take_index] = anchors["harmony"]["measurements"]
    return result


def test_five_observed_patterns_and_take_assignment_match_section_3_1_table() -> None:
    observed_by_take = _load_all_structure_observations()

    pattern_to_takes: dict[tuple[str, ...], list[int]] = {}
    for take_index, sections in observed_by_take.items():
        pattern_to_takes.setdefault(tuple(sections), []).append(take_index)

    actual = {pattern: tuple(sorted(takes)) for pattern, takes in pattern_to_takes.items()}
    assert actual == EXPECTED_PATTERNS

    total = sum(len(takes) for takes in EXPECTED_PATTERNS.values())
    assert total == N_TAKES


def test_non_reorder_decomposition_matches_section_3_1_and_section_5_values() -> None:
    observed_by_take = _load_all_structure_observations()

    for pattern, expected in EXPECTED_DECOMPOSITION.items():
        takes = EXPECTED_PATTERNS[pattern]
        for take_index in takes:
            sections = observed_by_take[take_index]
            assert tuple(sections) == pattern
            decomp = _decompose(sections, CANONICAL_SECTIONS)
            assert decomp["reorder"] is False, f"take{take_index}: unexpected reorder"
            actual = (decomp["omission"], decomp["insertion"], decomp["repetition"])
            assert actual == expected, (
                f"take{take_index} (pattern={pattern}): (om, ins, rep) = {actual}, "
                f"expected {expected}"
            )


def test_reorder_detected_only_for_take6_and_take18() -> None:
    observed_by_take = _load_all_structure_observations()

    reorder_takes = set()
    for take_index, sections in observed_by_take.items():
        decomp = _decompose(sections, CANONICAL_SECTIONS)
        if decomp["reorder"]:
            reorder_takes.add(take_index)

    assert reorder_takes == set(REORDER_TAKES)
    for take_index in REORDER_TAKES:
        assert tuple(observed_by_take[take_index]) == REORDER_PATTERN


def test_reference_policy_classification_yields_within_14_outside_6() -> None:
    observed_by_take = _load_all_structure_observations()

    within: list[int] = []
    outside: list[int] = []
    for take_index, sections in observed_by_take.items():
        status = _classify(sections, CANONICAL_SECTIONS)
        if status == "changed_within_policy":
            within.append(take_index)
        elif status == "changed_outside_policy":
            outside.append(take_index)
        else:
            raise AssertionError(f"take{take_index}: unexpected classification {status!r}")

    assert len(within) == EXPECTED_WITHIN_COUNT
    assert len(outside) == EXPECTED_OUTSIDE_COUNT
    assert set(outside) == EXPECTED_OUTSIDE_TAKES

    # outside = reorder (take6, take18) + coverage-floor rejects ([intro, outro] x4).
    coverage_floor_outside = EXPECTED_OUTSIDE_TAKES - REORDER_TAKES
    assert coverage_floor_outside == set(EXPECTED_PATTERNS[("intro", "outro")])


def test_hand_calc_anchor_ar4f_take0_and_take1_match_section_4_table() -> None:
    """docs/wi1_d1_thresholds.md §4 手計算アンカー: #193 fixture の
    ar4f_observation_take{0,1}.json に本アルゴリズムを適用し (om, ins, rep) =
    (1, 1, 1) / (3, 1, 0) と一致することを照合する (committed fixture からの
    機械再計算)。
    """
    take0 = _load_json(AR4F_TAKE0_PATH)
    take1 = _load_json(AR4F_TAKE1_PATH)

    for raw, expected in ((take0, (1, 1, 1)), (take1, (3, 1, 0))):
        anchors = {anchor["anchor_id"]: anchor for anchor in raw["anchors"]}
        sections = anchors["structure"]["measurements"]["observed_sections"]
        decomp = _decompose(sections, CANONICAL_SECTIONS)
        assert decomp["reorder"] is False
        actual = (decomp["omission"], decomp["insertion"], decomp["repetition"])
        assert actual == expected


def test_hand_calc_anchor_suno_a2_is_within_and_suno_b1_is_outside_via_reorder() -> None:
    """docs/wi1_d1_thresholds.md §4 手計算アンカー: Suno A2 は (0, 1, 3) で within、
    Suno B1 は reorder (bridge の後に verse) で outside (committed fixture からの
    機械再計算)。
    """
    a2 = _load_json(SUNO_A2_PATH)
    b1 = _load_json(SUNO_B1_PATH)

    a2_anchors = {anchor["anchor_id"]: anchor for anchor in a2["anchors"]}
    a2_sections = a2_anchors["structure"]["measurements"]["observed_sections"]
    a2_decomp = _decompose(a2_sections, CANONICAL_SECTIONS)
    assert a2_decomp["reorder"] is False
    assert (a2_decomp["omission"], a2_decomp["insertion"], a2_decomp["repetition"]) == (0, 1, 3)
    assert _classify(a2_sections, CANONICAL_SECTIONS) == "changed_within_policy"

    b1_anchors = {anchor["anchor_id"]: anchor for anchor in b1["anchors"]}
    b1_sections = b1_anchors["structure"]["measurements"]["observed_sections"]
    b1_decomp = _decompose(b1_sections, CANONICAL_SECTIONS)
    assert b1_decomp["reorder"] is True
    assert _classify(b1_sections, CANONICAL_SECTIONS) == "changed_outside_policy"


def test_verse_never_observed_and_outro_always_inserted_exactly_once() -> None:
    observed_by_take = _load_all_structure_observations()

    for take_index, sections in observed_by_take.items():
        assert "verse" not in sections, f"take{take_index}: verse unexpectedly observed"
        assert sections.count("outro") == 1, f"take{take_index}: expected exactly one outro"

        decomp = _decompose(sections, CANONICAL_SECTIONS)
        if not decomp["reorder"]:
            assert decomp["insertion"] == 1


def test_harmony_full_cycles_is_zero_for_all_twenty_takes() -> None:
    harmony_by_take = _load_all_harmony_observations()
    assert len(harmony_by_take) == N_TAKES
    for take_index, measurements in harmony_by_take.items():
        assert measurements["full_cycles"] == 0, f"take{take_index}: full_cycles != 0"


def test_harmony_take18_observed_length_is_zero() -> None:
    harmony_by_take = _load_all_harmony_observations()
    assert harmony_by_take[18]["observed_length"] == 0


# --- (C) harmony 設計材料 pin: take5 の退化例 (§6 (a) の根拠固定) -------------------


def test_harmony_take5_is_the_degenerate_length_one_full_match_example() -> None:
    """§6 (a): `collapsed_match_fraction` 単独の閾値化は不可という設計判断の根拠 —
    take5 は `collapsed_observed_length == 1` かつ `collapsed_match_fraction == 1.0`
    という長さ 1 の偶然一致による退化例であることを固定する。
    """
    harmony_by_take = _load_all_harmony_observations()
    take5 = harmony_by_take[5]
    assert take5["collapsed_observed_length"] == 1
    assert take5["collapsed_match_fraction"] == 1.0
