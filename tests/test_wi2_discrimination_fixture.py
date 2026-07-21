"""WI2 弁別判定ハーネス (2026-07-21, 4 セル 13 clips) fixture self-test + 検算照合ゲート。

`examples/arrangement/midnight_signal/observed/wi2_discrimination/` に committed
された、WI2 事前登録バッチ（``wi2_plan.yaml`` / ``wi2_generation_timestamps.yaml`` /
``wi2_takes_manifest.json`` / ``wi2_determinism_spot_check.yaml`` /
``wi2_channel_death_proof.yaml`` / ``wi2_references.yaml`` / 12 本の
``wi2_rank_{cell}_take{N}.json``）を、``tests/test_wi1_musicgen_n20_fixture.py``
の型を踏襲して固定する回帰テスト。

wav ファイル自体は非コミット（sha256 pin のみ）— 本テストは wav に一切依存せず、
committed JSON/YAML と抽出済み bundle（``wi2_*.rpe.json``、audio と共に非コミットの
候補生成物ではなく既にリポジトリへ commit 済みの RPE 抽出結果）のみを読み込む。

Codex P2 (#200) 教訓の先取り: (A) は "pin 突合の全数接続" ——
plan/timestamps/manifest/spot-check/channel-death-proof の各 sha256・時刻が
互いに矛盾なく繋がっていることを機械照合する。(B) は受け入れ条件の CI 接地 ——
``identity_rank_from_paths`` をテスト内で再実行し、committed
``wi2_rank_*.json`` と byte 一致することを assert する（順位表の決定論再現）。
(C) は判読済みの実測値 pin ——数値が 1 つでも Design Memo の判読結果と食い違えば、
このテストは修正せず失敗を報告する目的で書かれている。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from svp_rpe.roundtrip.identity_rank import identity_rank_from_paths

FIXTURE_DIR = Path("examples/arrangement/midnight_signal/observed/wi2_discrimination")

PLAN_PATH = FIXTURE_DIR / "wi2_plan.yaml"
TIMESTAMPS_PATH = FIXTURE_DIR / "wi2_generation_timestamps.yaml"
MANIFEST_PATH = FIXTURE_DIR / "wi2_takes_manifest.json"
DETERMINISM_SPOT_CHECK_PATH = FIXTURE_DIR / "wi2_determinism_spot_check.yaml"
CHANNEL_DEATH_PROOF_PATH = FIXTURE_DIR / "wi2_channel_death_proof.yaml"
REFERENCES_PATH = FIXTURE_DIR / "wi2_references.yaml"

PLAN_CONFIRMED_AT_UTC = "2026-07-21T06:27:42Z"

#: The 12 extracted takes ranked by identity-rank (cell P / wi2_P_take0 is the
#: channel-death-proof clip and is excluded from RPE extraction — see
#: wi2_takes_manifest.json's note on that sample).
RANKED_CELLS_AND_TAKES: tuple[tuple[str, int], ...] = tuple(
    (cell, take) for cell in ("A", "C", "D") for take in range(4)
)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _rank_report_path(cell: str, take: int) -> Path:
    return FIXTURE_DIR / f"wi2_rank_{cell}_take{take}.json"


def _bundle_path(cell: str, take: int) -> Path:
    return FIXTURE_DIR / f"wi2_{cell}_take{take}.rpe.json"


def _load_rank_report(cell: str, take: int) -> dict:
    return _load_json(_rank_report_path(cell, take))


def _axis(report: dict, axis_name: str) -> dict:
    for axis in report["axes"]:
        if axis["axis"] == axis_name:
            return axis
    raise AssertionError(f"axis {axis_name!r} not present in report")


def _ranking_by_ref_id(axis: dict) -> dict[str, dict]:
    return {entry["ref_id"]: entry for entry in axis["ranking"]}


# ---------------------------------------------------------------------------
# (A) fixture 整合性: pin 突合の全数接続
# ---------------------------------------------------------------------------


def test_plan_confirmed_at_utc_precedes_all_generation_start_timestamps() -> None:
    plan = _load_yaml(PLAN_PATH)
    assert plan["plan_confirmed_at_utc"] == PLAN_CONFIRMED_AT_UTC

    timestamps = _load_yaml(TIMESTAMPS_PATH)
    calls = timestamps["generation_calls"] + timestamps["spot_check_calls"]
    assert len(calls) >= 5, "expected at least the 5 generation_calls + 2 spot_check_calls"

    for call in calls:
        assert PLAN_CONFIRMED_AT_UTC < call["started_at_utc"], (
            f"{call['call_id']}: started_at_utc does not follow plan_confirmed_at_utc "
            "(pre-registration must precede generation)"
        )


def test_takes_manifest_has_thirteen_entries_with_one_intentional_sha256_duplicate() -> None:
    manifest = _load_json(MANIFEST_PATH)
    samples = manifest["samples"]
    assert len(samples) == 13

    for sample in samples:
        sha = sample["audio_sha256"]
        assert re.fullmatch(r"[0-9a-f]{64}", sha), f"not a well-formed sha256 hex digest: {sha}"

    shas = [sample["audio_sha256"] for sample in samples]
    # Every sha256 is distinct except the deliberate wi2_A_take0 / wi2_P_take0
    # duplicate (cell P is performed with the same seed as A take0 specifically
    # to prove the harmony prompt channel is dead — see wi2_channel_death_proof.yaml).
    duplicates: dict[str, list[str]] = {}
    for sample in samples:
        duplicates.setdefault(sample["audio_sha256"], []).append(sample["sample_id"])
    duplicate_groups = {sha: ids for sha, ids in duplicates.items() if len(ids) > 1}
    assert duplicate_groups == {
        next(sha for sha, ids in duplicates.items() if set(ids) == {"wi2_A_take0", "wi2_P_take0"}): [
            "wi2_A_take0",
            "wi2_P_take0",
        ]
    }
    assert len(set(shas)) == 12, "expected exactly one sha256 collision (A take0 / P take0)"


def test_determinism_spot_check_matches_manifest_pinned_audio_sha256() -> None:
    manifest = _load_json(MANIFEST_PATH)
    spot_check = _load_yaml(DETERMINISM_SPOT_CHECK_PATH)

    manifest_sha_by_cell_take = {
        (sample["cell"], sample["take"]): sample["audio_sha256"] for sample in manifest["samples"]
    }

    checks = spot_check["spot_checks"]
    assert {(check["cell"], check["take"]) for check in checks} == {("A", 0), ("D", 3)}
    for check in checks:
        assert check["match"] is True
        pinned = manifest_sha_by_cell_take[(check["cell"], check["take"])]
        assert check["batch_sha256"] == pinned, (
            f"cell {check['cell']} take {check['take']}: spot check batch_sha256 does not "
            "match wi2_takes_manifest.json audio_sha256 pin"
        )
        assert check["fresh_process_sha256"] == pinned, (
            f"cell {check['cell']} take {check['take']}: spot check fresh_process_sha256 does "
            "not match wi2_takes_manifest.json audio_sha256 pin"
        )


def test_channel_death_proof_sha256_pair_matches_each_other_and_manifest() -> None:
    manifest = _load_json(MANIFEST_PATH)
    proof = _load_yaml(CHANNEL_DEATH_PROOF_PATH)

    manifest_sha_by_id = {sample["sample_id"]: sample["audio_sha256"] for sample in manifest["samples"]}

    a_sha = proof["inputs"]["a_faithful"]["audio_sha256"]
    bp_sha = proof["inputs"]["bp_harmony_destroyed"]["audio_sha256"]

    # The channel-death proof itself: the two clips are byte-identical.
    assert a_sha == bp_sha
    assert proof["verdict"]["sha256_byte_identical"] is True

    # And both sides trace back to the manifest's pin for wi2_A_take0 / wi2_P_take0
    # (the machine pin the design memo's channel-death claim rests on).
    assert a_sha == manifest_sha_by_id["wi2_A_take0"]
    assert bp_sha == manifest_sha_by_id["wi2_P_take0"]


# ---------------------------------------------------------------------------
# (B) 順位表の決定論再現: committed bundle から再実行し byte 一致 (12/12)
# ---------------------------------------------------------------------------


def test_identity_rank_reruns_byte_identical_to_committed_reports_for_all_twelve_takes() -> None:
    mismatches: list[str] = []
    for cell, take in RANKED_CELLS_AND_TAKES:
        bundle_path = _bundle_path(cell, take)
        report = identity_rank_from_paths(bundle_path, REFERENCES_PATH)
        regenerated = json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)
        committed = _rank_report_path(cell, take).read_text(encoding="utf-8")
        if regenerated != committed:
            mismatches.append(f"{cell}_take{take}")
    assert mismatches == [], f"non-byte-identical rank reports: {mismatches}"


# ---------------------------------------------------------------------------
# (C) 実測値 pin: 判読済みの数値を機械接地
# ---------------------------------------------------------------------------


def test_key_axis_a_take1_canonical_distance_is_zero_regression_pin() -> None:
    """A take1 の canonical key 距離 == 0.0 — key axis バグ修正 (bundle が
    physical.key/physical.mode を別フィールドで持つのに対し reference は単一
    "<tonic> <mode>" 文字列という食い違いの是正) の恒久 pin。"""

    report = _load_rank_report("A", 1)
    key_axis = _axis(report, "key")
    ranking = _ranking_by_ref_id(key_axis)
    assert ranking["canonical"]["distance"] == 0.0


def test_bpm_axis_canonical_wins_seven_of_eight_a_and_c_takes_other_work_wins_all_d() -> None:
    """bpm 弁別: canonical 群 vs other_work の rank1 — A/C 8 take 中 7 take で
    canonical 側（例外は A take3 = 89.1 アトラクタで other_work）/ D 4 take
    全てで other_work。"""

    canonical_wins: list[tuple[str, int]] = []
    other_work_wins: list[tuple[str, int]] = []
    for cell, take in (("A", 0), ("A", 1), ("A", 2), ("A", 3), ("C", 0), ("C", 1), ("C", 2), ("C", 3)):
        report = _load_rank_report(cell, take)
        rank1 = _axis(report, "bpm")["ranking"][0]
        if rank1["ref_id"] == "canonical":
            canonical_wins.append((cell, take))
        elif rank1["ref_id"] == "other_work":
            other_work_wins.append((cell, take))
        else:
            raise AssertionError(f"{cell} take{take}: unexpected bpm rank1 {rank1['ref_id']!r}")

    assert other_work_wins == [("A", 3)]
    assert len(canonical_wins) == 7

    for take in range(4):
        report = _load_rank_report("D", take)
        rank1 = _axis(report, "bpm")["ranking"][0]
        assert rank1["ref_id"] == "other_work", f"D take{take}: expected other_work bpm rank1"


def test_harmony_axis_eleven_of_twelve_takes_tie_canonical_and_harmony_decoy_at_1_0() -> None:
    """harmony tie: 12 take 中 11 take で canonical と harmony_decoy の距離が
    ともに 1.0 の tie（tied_with に相互記載）・C take1 のみ harmony_decoy 0.75
    の単独勝ち。"""

    tie_takes: list[tuple[str, int]] = []
    single_winner_takes: list[tuple[str, int]] = []
    for cell, take in RANKED_CELLS_AND_TAKES:
        report = _load_rank_report(cell, take)
        harmony = _ranking_by_ref_id(_axis(report, "harmony"))
        canonical = harmony["canonical"]
        decoy = harmony["harmony_decoy"]
        if canonical["distance"] == decoy["distance"] == 1.0:
            tie_takes.append((cell, take))
            assert "harmony_decoy" in canonical["tied_with"]
            assert "canonical" in decoy["tied_with"]
        else:
            single_winner_takes.append((cell, take))

    assert single_winner_takes == [("C", 1)]
    assert len(tie_takes) == 11

    c_take1_report = _load_rank_report("C", 1)
    c_take1_harmony = _ranking_by_ref_id(_axis(c_take1_report, "harmony"))
    assert c_take1_harmony["harmony_decoy"]["distance"] == 0.75
    assert c_take1_harmony["harmony_decoy"]["rank"] == 1
    assert c_take1_harmony["harmony_decoy"]["tied_with"] == []
    assert c_take1_harmony["canonical"]["distance"] == 1.0


def test_structure_axis_a_group_three_takes_destroyed_beats_canonical() -> None:
    """structure 非弁別: A 群の [Intro,Chorus,Outro] 3 take (take0/1/3) で
    destroyed(0.5) < canonical(0.75)（逆転の pin = 長さバイアスの実測記録）。"""

    for take in (0, 1, 3):
        report = _load_rank_report("A", take)
        structure = _ranking_by_ref_id(_axis(report, "structure"))
        assert structure["structure_destroyed"]["distance"] == 0.5
        assert structure["canonical"]["distance"] == 0.75
        assert structure["structure_destroyed"]["distance"] < structure["canonical"]["distance"]


def test_brightness_axis_is_a_four_way_tie_for_every_take() -> None:
    """brightness 全行 tie（4 参照同距離）。"""

    for cell, take in RANKED_CELLS_AND_TAKES:
        report = _load_rank_report(cell, take)
        brightness = _axis(report, "brightness")
        distances = {entry["distance"] for entry in brightness["ranking"]}
        assert len(distances) == 1, f"{cell} take{take}: brightness distances are not all equal"
        assert len(brightness["ranking"]) == 4
        for entry in brightness["ranking"]:
            assert set(entry["tied_with"]) == {
                other["ref_id"] for other in brightness["ranking"] if other["ref_id"] != entry["ref_id"]
            }
