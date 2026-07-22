"""WI3 人間校正 v0 (2026-07-22, 12 ペア人間判定) fixture self-test + 検算照合ゲート。

`examples/arrangement/midnight_signal/observed/wi3_human_calibration/` に committed
された、WI3 事前登録・封印マッピング・回答原本・kit manifest・機械集計
（``wi3_preregistration.yaml`` / ``wi3_pair_mapping.sealed.yaml`` /
``wi3_responses.yaml`` / ``wi3_kit_manifest.json`` / ``wi3_aggregation.yaml``）を、
``tests/test_wi2_discrimination_fixture.py`` の型を踏襲して固定する回帰テスト。

設計正本: ``docs/wi3_human_calibration.md``。wav ファイル自体は非コミット
（sha256 pin のみ）— 本テストは wav に一切依存しない。

(A) は provenance 時系列 —— 事前登録の ``plan_confirmed_at_utc`` が回答原本の
``received_at_utc`` に先行することを機械照合する（聴取前凍結の担保）。
(B) は fixture 整合性 —— 回答 12 件の verdict/confidence 形式、決定論シャッフル
（``random.Random(8400)``）の presentation_order/ab_swap がバイト一致で再現
できること、kit manifest 24 エントリの sha256 形式を確認する。
(C) は **集計の決定論再現**（検算照合の恒久化）—— ``wi3_aggregation.yaml`` の
集計を、committed 入力（``wi3_responses.yaml`` / ``wi3_pair_mapping.sealed.yaml`` /
``wi2_rank_{A,C,D}_take{0-3}.json`` 12 本 +
``wi3_rank_synth_{faithful,first_take}.json`` 2 本）から事前登録 §4 の集計規約
（``pair_level_prediction_rule``）に従ってテスト内で独立に再計算し、
``wi3_aggregation.yaml`` の記録値と一致することを assert する。12 ペア全てが
committed 入力からの完全な独立再計算になっている（synth 2 素材の rank-1 状態も、
他の 12 MusicGen 素材と同様に committed identity-rank レポートから本テストが
独立に導出する — PR #202 Codex P2 対応: 以前は synth 2 素材のみ非コミットで
``wi3_aggregation.yaml`` 自身の ``material_axis_rank1`` を再利用する循環に
なっていた）。synth レポート 2 本の sha256 は
``wi3_rank_synth_provenance.yaml`` の pin と照合する。

pin（数値が食い違ったら修正せず停止して報告する対象）:

- 軸別 {判定可能, 正解, 誤り} 表: structure=(2,1,1) / harmony=(0,0,0) /
  key=(2,1,1) / bpm=(2,1,1) / brightness=(0,0,0)
- 誤りペア: structure=p07 / key=p08 / bpm=p07
- 採用 0/5 = proxy v0 は空集合（``null_results_are_valid`` に基づく有効な結果）
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

FIXTURE_DIR = Path("examples/arrangement/midnight_signal/observed/wi3_human_calibration")
WI2_FIXTURE_DIR = Path("examples/arrangement/midnight_signal/observed/wi2_discrimination")
SUNO_FIXTURE_DIR = Path("examples/arrangement/midnight_signal/observed/suno")

PREREGISTRATION_PATH = FIXTURE_DIR / "wi3_preregistration.yaml"
SEALED_MAPPING_PATH = FIXTURE_DIR / "wi3_pair_mapping.sealed.yaml"
RESPONSES_PATH = FIXTURE_DIR / "wi3_responses.yaml"
KIT_MANIFEST_PATH = FIXTURE_DIR / "wi3_kit_manifest.json"
AGGREGATION_PATH = FIXTURE_DIR / "wi3_aggregation.yaml"
SYNTH_PROVENANCE_PATH = FIXTURE_DIR / "wi3_rank_synth_provenance.yaml"
SYNTH_RENDER_SCRIPT_PATH = FIXTURE_DIR / "render_synth_takes.py"
P5_DEFERRAL_EVIDENCE_PATH = FIXTURE_DIR / "wi3_p5_deferral_evidence.md"
SUNO_TAKES_MEMO_PATH = SUNO_FIXTURE_DIR / "takes_memo.yaml"

PLAN_CONFIRMED_AT_UTC = "2026-07-21T13:56:58Z"
RECEIVED_AT_UTC = "2026-07-22T05:40:41Z"

AXES: tuple[str, ...] = ("structure", "harmony", "key", "bpm", "brightness")

#: The 12 WI2 MusicGen materials that have committed identity-rank reports
#: (`wi2_rank_{cell}_take{N}.json`), usable for a fully independent recompute.
WI2_MATERIAL_CELLS_AND_TAKES: tuple[tuple[str, int], ...] = tuple(
    (cell, take) for cell in ("A", "C", "D") for take in range(4)
)

#: The 2 decisive-synth materials (P4 pairs p09/p10/p11) with committed
#: identity-rank reports (PR #202 Codex P2: previously scratchpad-only,
#: unreproducible from a fresh checkout). `wi3_rank_synth_provenance.yaml`
#: pins the generating commands and input-wav/report sha256.
SYNTH_MATERIAL_RANK_REPORT_PATHS: dict[str, Path] = {
    "synth_faithful": FIXTURE_DIR / "wi3_rank_synth_faithful.json",
    "synth_first_take": FIXTURE_DIR / "wi3_rank_synth_first_take.json",
}

#: pin — axis-level {decidable, correct, error} counts (§4 pair_level_prediction_rule
#: applied mechanically; see wi3_aggregation.yaml axis_summary).
EXPECTED_AXIS_COUNTS: dict[str, tuple[int, int, int]] = {
    "structure": (2, 1, 1),
    "harmony": (0, 0, 0),
    "key": (2, 1, 1),
    "bpm": (2, 1, 1),
    "brightness": (0, 0, 0),
}

#: pin — the single error pair per axis (among the axes with any decidable pairs).
EXPECTED_ERROR_PAIR: dict[str, str] = {
    "structure": "p07",
    "key": "p08",
    "bpm": "p07",
}

P1_PAIR_IDS: tuple[str, ...] = ("p01", "p02")
P3_PAIR_IDS: tuple[str, ...] = ("p06", "p07", "p08")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _normalize_verdict(value: object) -> str:
    """PyYAML(1.1 resolver) coerces bare ``yes``/``no`` scalars to Python bool.

    ``wi3_responses.yaml`` relies on this being undone explicitly — the fixture's
    own ``verdict_yaml_boolean_note`` documents the same pitfall for the
    committed aggregation script. This helper is the test's counterpart.
    """
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str) and value in {"yes", "no"}:
        return value
    raise AssertionError(f"unexpected verdict value after yaml.safe_load: {value!r}")


def _human_prediction_equiv(verdict: str) -> str:
    return "same" if verdict == "yes" else "different"


def _rank1_status_from_ranking(ranking: list[dict]) -> tuple[str, str | None]:
    """Return (status, rank1_ref) for one axis's ranking list.

    The identity-rank report's own ``rank`` field assigns a single entry
    ``rank == 1``; if that entry's ``tied_with`` is non-empty the true rank-1
    is a tie (ambiguous — no single closest reference), else it is unique.
    """
    rank1_entries = [entry for entry in ranking if entry["rank"] == 1]
    assert len(rank1_entries) == 1, f"expected exactly one rank==1 entry, got {rank1_entries}"
    entry = rank1_entries[0]
    if entry["tied_with"]:
        return "tie", None
    return "unique", entry["ref_id"]


def _material_axis_rank1_from_rank_report(report: dict) -> dict[str, tuple[str, str | None]]:
    """Derive per-axis rank-1 (status, ref) from a committed `identity-rank`
    JSON report (``wi2_rank_*.json`` or ``wi3_rank_synth_*.json`` — same
    ``identity-rank-report/0.1`` schema in both cases)."""
    result: dict[str, tuple[str, str | None]] = {}
    for axis_entry in report["axes"]:
        result[axis_entry["axis"]] = _rank1_status_from_ranking(axis_entry["ranking"])
    return result


def _material_axis_rank1_from_aggregation(agg: dict, material: str) -> dict[str, tuple[str, str | None]]:
    """Read the recorded rank-1 table straight out of ``wi3_aggregation.yaml``
    (used only as the *expected* side of an equality assertion — never as a
    recompute input; see ``test_material_axis_rank1_recomputes_from_committed_*``)."""
    entry = agg["material_axis_rank1"][material]
    return {axis: (entry[axis]["status"], entry[axis]["rank1_ref"]) for axis in AXES}


def _reproduce_shuffle() -> tuple[list[str], dict[str, bool]]:
    """Re-run the seed=8400 shuffle exactly as documented in
    ``wi3_pair_mapping.sealed.yaml``'s ``shuffle_reproduction.algorithm``."""
    pair_ids = [f"p{i:02d}" for i in range(1, 13)]
    rng = random.Random(8400)

    presentation_order = pair_ids.copy()
    rng.shuffle(presentation_order)

    ab_swap = {pid: (rng.random() < 0.5) for pid in pair_ids}
    return presentation_order, ab_swap


def _build_material_axis_rank1_table() -> dict[str, dict[str, tuple[str, str | None]]]:
    """Build the material -> axis -> rank-1 table entirely from committed
    identity-rank reports (12 WI2 MusicGen takes + 2 synth styles) — no
    dependency on ``wi3_aggregation.yaml``'s own recorded values."""
    table: dict[str, dict[str, tuple[str, str | None]]] = {}
    for cell, take in WI2_MATERIAL_CELLS_AND_TAKES:
        report = _load_json(WI2_FIXTURE_DIR / f"wi2_rank_{cell}_take{take}.json")
        table[f"wi2_{cell}_take{take}"] = _material_axis_rank1_from_rank_report(report)
    for material, path in SYNTH_MATERIAL_RANK_REPORT_PATHS.items():
        report = _load_json(path)
        table[material] = _material_axis_rank1_from_rank_report(report)
    return table


def _recompute_pair_predictions(
    sealed: dict,
    responses_by_pair_id: dict[str, tuple[str, int]],
    material_axis_rank1: dict[str, dict[str, tuple[str, str | None]]],
) -> dict[str, dict]:
    """Recompute per-pair, per-axis predictions per §4 pair_level_prediction_rule:

    same axis rank-1 reference (unique on both sides) -> "same"; different
    unique rank-1 refs -> "different"; either side tie (or not-observed) ->
    abstain.
    """
    out: dict[str, dict] = {}
    for pair in sealed["pairs"]:
        pair_id = pair["pair_id"]
        x_material = pair["x_material"]
        y_material = pair["y_material"]
        verdict, confidence = responses_by_pair_id[pair_id]
        human_equiv = _human_prediction_equiv(verdict)

        axes_out: dict[str, dict] = {}
        for axis in AXES:
            x_status, x_ref = material_axis_rank1[x_material][axis]
            y_status, y_ref = material_axis_rank1[y_material][axis]
            if x_status != "unique" or y_status != "unique":
                axes_out[axis] = {"prediction": None, "abstain": True, "outcome": "abstain"}
                continue
            prediction = "same" if x_ref == y_ref else "different"
            outcome = "match" if prediction == human_equiv else "mismatch"
            axes_out[axis] = {"prediction": prediction, "abstain": False, "outcome": outcome}

        out[pair_id] = {
            "human_verdict": verdict,
            "human_confidence": confidence,
            "axes": axes_out,
        }
    return out


# ---------------------------------------------------------------------------
# (A) provenance 時系列
# ---------------------------------------------------------------------------


def test_plan_confirmed_at_utc_precedes_responses_received_at_utc() -> None:
    prereg = _load_yaml(PREREGISTRATION_PATH)
    responses = _load_yaml(RESPONSES_PATH)

    assert prereg["plan_confirmed_at_utc"] == PLAN_CONFIRMED_AT_UTC
    assert responses["received_at_utc"] == RECEIVED_AT_UTC

    plan_dt = datetime.fromisoformat(PLAN_CONFIRMED_AT_UTC.replace("Z", "+00:00"))
    received_dt = datetime.fromisoformat(RECEIVED_AT_UTC.replace("Z", "+00:00"))
    assert plan_dt.tzinfo is not None and plan_dt.tzinfo.utcoffset(plan_dt) == timezone.utc.utcoffset(None)
    assert plan_dt < received_dt, (
        "pre-registration (plan_confirmed_at_utc) must precede response receipt "
        "(received_at_utc) — blind listening protocol requires the protocol to "
        "be frozen before the judge submits answers"
    )
    # sealed mapping carries the same plan_confirmed_at_utc pin — cross-check it too.
    sealed = _load_yaml(SEALED_MAPPING_PATH)
    assert sealed["plan_confirmed_at_utc"] == PLAN_CONFIRMED_AT_UTC


# ---------------------------------------------------------------------------
# (B) 整合性: 回答 12 件・presentation_order 再現・kit manifest sha256
# ---------------------------------------------------------------------------


def test_responses_have_twelve_entries_with_valid_verdict_and_confidence() -> None:
    responses = _load_yaml(RESPONSES_PATH)
    entries = responses["responses"]
    assert len(entries) == 12

    seen_pairs = set()
    for entry in entries:
        pair_key = entry["pair"]
        assert re.fullmatch(r"pair(0[1-9]|1[0-2])", pair_key), f"unexpected pair key: {pair_key!r}"
        seen_pairs.add(pair_key)

        # PyYAML(1.1) coerces bare yes/no to bool -- this is the known pitfall
        # the fixture's own verdict_yaml_boolean_note documents; the raw loaded
        # value must be a bool here (proving the coercion actually happens),
        # and _normalize_verdict must undo it correctly.
        assert isinstance(entry["verdict"], bool), (
            f"expected PyYAML to coerce {pair_key}'s verdict to bool (safe_load "
            "default resolver) -- if this now fails, the aggregation script's "
            "documented normalization workaround may be silently dead code"
        )
        verdict = _normalize_verdict(entry["verdict"])
        assert verdict in {"yes", "no"}

        confidence = entry["confidence"]
        assert isinstance(confidence, int)
        assert 1 <= confidence <= 5

    assert seen_pairs == {f"pair{i:02d}" for i in range(1, 13)}


def test_presentation_order_and_ab_swap_reproduce_byte_for_byte_from_seed_8400() -> None:
    sealed = _load_yaml(SEALED_MAPPING_PATH)
    shuffle = sealed["shuffle_reproduction"]
    assert shuffle["seed"] == 8400

    presentation_order, ab_swap = _reproduce_shuffle()

    assert presentation_order == shuffle["presentation_order_pair_ids"]
    assert ab_swap == shuffle["ab_swap"]

    # cross-check derived presentation_slot / ab_swap fields on each pair entry.
    slot_of_pair = {pid: i + 1 for i, pid in enumerate(presentation_order)}
    for pair in sealed["pairs"]:
        pair_id = pair["pair_id"]
        assert pair["presentation_slot"] == slot_of_pair[pair_id]
        assert pair["ab_swap"] == ab_swap[pair_id]


def test_kit_manifest_has_24_entries_with_well_formed_sha256() -> None:
    manifest = _load_json(KIT_MANIFEST_PATH)
    assert manifest["kit_file_count"] == 24
    files = manifest["files"]
    assert len(files) == 24

    expected_filenames = {
        f"pair{slot:02d}_{side}.wav" for slot in range(1, 13) for side in ("A", "B")
    }
    seen_filenames = {entry["kit_filename"] for entry in files}
    assert seen_filenames == expected_filenames

    for entry in files:
        sha = entry["sha256"]
        assert re.fullmatch(r"[0-9a-f]{64}", sha), f"not a well-formed sha256 hex digest: {sha}"


# ---------------------------------------------------------------------------
# (C) 集計の決定論再現（検算照合の恒久化）
# ---------------------------------------------------------------------------


def test_material_axis_rank1_recomputes_from_committed_wi2_rank_reports() -> None:
    """12 の MusicGen 素材について、committed ``wi2_rank_*.json`` から
    rank-1 状態（tie/unique・rank1_ref）を独立に導出し、
    ``wi3_aggregation.yaml`` の ``material_axis_rank1`` と一致することを assert する。
    """
    agg = _load_yaml(AGGREGATION_PATH)

    for cell, take in WI2_MATERIAL_CELLS_AND_TAKES:
        material = f"wi2_{cell}_take{take}"
        report = _load_json(WI2_FIXTURE_DIR / f"wi2_rank_{cell}_take{take}.json")
        computed = _material_axis_rank1_from_rank_report(report)
        recorded = _material_axis_rank1_from_aggregation(agg, material)
        assert computed == recorded, f"{material}: rank-1 status mismatch {computed} != {recorded}"


def test_synth_material_axis_rank1_recomputes_from_committed_rank_reports() -> None:
    """PR #202 Codex P2 対応: synth_faithful / synth_first_take についても、
    (WI2 側 12 素材と同じ型で) committed ``wi3_rank_synth_{faithful,first_take}.json``
    から rank-1 状態を独立に導出し、``wi3_aggregation.yaml`` の
    ``material_axis_rank1`` と一致することを assert する（以前は非コミットの
    scratchpad レポートしか存在せず、この 2 素材だけ ``wi3_aggregation.yaml``
    自身の記録値を再計算の入力に使う循環になっていた — 本テストが独立検証に
    置き換える）。
    """
    agg = _load_yaml(AGGREGATION_PATH)
    for material, path in SYNTH_MATERIAL_RANK_REPORT_PATHS.items():
        report = _load_json(path)
        computed = _material_axis_rank1_from_rank_report(report)
        recorded = _material_axis_rank1_from_aggregation(agg, material)
        assert computed == recorded, f"{material}: rank-1 status mismatch {computed} != {recorded}"


def test_synth_rank_reports_sha256_matches_provenance_pin() -> None:
    """committed synth rank レポート 2 本の sha256 が
    ``wi3_rank_synth_provenance.yaml`` の ``report_sha256`` pin と一致することを
    assert する（stale 差し替え検出 — provenance サイドカーが指す実体と、
    集計に使われる実ファイルが機械的に同一であることの保証）。あわせて
    provenance サイドカーが記録する入力 wav sha256 が ``wi3_kit_manifest.json``
    （キット同梱 wav の pin）とも一致することを確認する。
    """
    provenance = _load_yaml(SYNTH_PROVENANCE_PATH)
    report_sha256 = provenance["report_sha256"]

    for material, path in SYNTH_MATERIAL_RANK_REPORT_PATHS.items():
        actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        pinned_sha256 = report_sha256[path.name]
        assert actual_sha256 == pinned_sha256, (
            f"{path.name}: committed file sha256 {actual_sha256!r} does not match "
            f"wi3_rank_synth_provenance.yaml pin {pinned_sha256!r}"
        )

    manifest = _load_json(KIT_MANIFEST_PATH)
    kit_sha256_by_filename = {entry["kit_filename"]: entry["sha256"] for entry in manifest["files"]}
    input_audio_sha256 = provenance["input_audio_sha256"]
    assert input_audio_sha256["faithful_take"]["sha256"] == kit_sha256_by_filename["pair06_B.wav"]
    assert input_audio_sha256["first_take"]["sha256"] == kit_sha256_by_filename["pair06_A.wav"]


def test_synth_rank_reports_audio_file_is_checkout_stable_relative_path() -> None:
    """PR #202 Codex P2 第2ラウンド対応: 各 synth rank レポートの ``audio_file``
    が本リポジトリ配下の相対パスであり、scratchpad 等の絶対パスを含まないこと
    を assert する（fresh checkout での再検証可能性のゲート — 生成手順自体が
    リポジトリルートからの相対パス実行に是正されたことの機械的な裏付け）。
    """
    for path in SYNTH_MATERIAL_RANK_REPORT_PATHS.values():
        report = _load_json(path)
        audio_file = report["audio_file"]
        assert not audio_file.startswith("/"), (
            f"{path.name}: audio_file {audio_file!r} is an absolute path "
            "(expected a repo-relative path, e.g. under examples/...)"
        )
        assert "scratchpad" not in audio_file, (
            f"{path.name}: audio_file {audio_file!r} still references a scratchpad path"
        )
        assert audio_file.startswith(
            "examples/arrangement/midnight_signal/observed/wi3_human_calibration/synth/"
        ), f"{path.name}: unexpected audio_file {audio_file!r}"


def test_render_script_sha256_matches_provenance_pin() -> None:
    """PR #202 Codex P2 第3ラウンド対応: synth wav の再現レシピが scratchpad
    限定の未収載スクリプトに依存していた不備を是正 — レンダースクリプト
    (``render_synth_takes.py``) 自体を fixture 収載し、その sha256 が
    ``wi3_rank_synth_provenance.yaml`` の ``render_script_sha256`` pin と
    一致することを assert する（stale 差し替え検出。これにより fresh
    checkout だけで render -> extract -> identity-rank の全手順が pin 済み
    実行物のみから再現できることの機械的裏付けとなる）。
    """
    provenance = _load_yaml(SYNTH_PROVENANCE_PATH)
    pinned_sha256 = provenance["render_script_sha256"][SYNTH_RENDER_SCRIPT_PATH.name]
    actual_sha256 = hashlib.sha256(SYNTH_RENDER_SCRIPT_PATH.read_bytes()).hexdigest()
    assert actual_sha256 == pinned_sha256, (
        f"{SYNTH_RENDER_SCRIPT_PATH.name}: committed file sha256 {actual_sha256!r} does not match "
        f"wi3_rank_synth_provenance.yaml pin {pinned_sha256!r}"
    )


def test_replay_input_pins_match_working_tree_sha256() -> None:
    """PR #202 Codex P2 第7ラウンド対応: リプレイ（render -> extract ->
    identity-rank）の間に実際に読まれる全ファイル（レシピ直接引数の
    ``composition_score.yaml``/``wi2_references.yaml``、および
    ``wi2_references.yaml`` の 4 参照が解決する先の score/progression 6 本、
    計 8 ファイル）の sha256 が working tree の実ファイルと一致することを
    assert する恒久ガード — このうちどれか 1 つでも将来変更されれば、
    identity-rank レポートの内容（distance/rank-1）が変わりうるにもかかわらず
    それを検出する術がなかった（第7ラウンド Codex P2 で指摘された不備の
    クラス全体を閉じる）。1 ファイルでも変更されれば本テストが赤くなる。
    """
    provenance = _load_yaml(SYNTH_PROVENANCE_PATH)
    pins = provenance["replay_input_pins"]
    assert len(pins) == 8, f"expected 8 replay_input_pins entries, got {len(pins)}"

    for entry in pins:
        path = Path(entry["path"])
        assert path.is_file(), f"replay_input_pins entry {entry['path']!r} does not exist in the working tree"
        actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual_sha256 == entry["sha256"], (
            f"{entry['path']}: working tree sha256 {actual_sha256!r} does not match "
            f"wi3_rank_synth_provenance.yaml replay_input_pins pin {entry['sha256']!r} "
            f"(role: {entry.get('role')!r})"
        )


def test_suno_p5_materials_have_sha256_pin_and_no_drive_file_id() -> None:
    """PR #202 Codex P2 第4ラウンド対応: ``wi3_p5_deferral_evidence.md``
    (a) 節の機械検証可能な事実を pin する — P5（実 Suno 正典 form take vs
    順序破壊 take）に必要な 4 take (``suno_A1``/``suno_A2``/``suno_B1``/
    ``suno_B2``) の provenance 正本 ``observed/suno/takes_memo.yaml`` の
    各エントリが ``sha256`` フィールドを持ち、``drive_file_id`` フィールドを
    一切持たないことを assert する（``wi3_preregistration.yaml`` §2 の P5
    status「sha256 pin のみで Drive 未回収」の committed 裏付け。
    ``wi3_preregistration.yaml`` 自体は聴取前凍結の歴史的記録のため本テストは
    それを変更・再検証しない — 参照するのは ``takes_memo.yaml`` の実データの
    みである）。
    """
    memo = _load_yaml(SUNO_TAKES_MEMO_PATH)
    takes = memo["takes"]
    assert {t["assigned_name"] for t in takes} == {"suno_A1", "suno_A2", "suno_B1", "suno_B2"}

    for take in takes:
        name = take["assigned_name"]
        assert re.fullmatch(r"[0-9a-f]{64}", take["sha256"]), f"{name}: not a well-formed sha256 hex digest"
        assert "drive_file_id" not in take, f"{name}: unexpectedly has a drive_file_id field"

    # the evidence doc itself must exist (committed backing for the
    # preregistration's scratchpad-only wi3_audio_inventory.md reference).
    assert P5_DEFERRAL_EVIDENCE_PATH.is_file()


def test_pair_level_predictions_and_axis_summary_recompute_from_committed_inputs() -> None:
    """恒久検算ゲート: wi3_aggregation.yaml の集計値を、committed 入力
    （responses / sealed mapping / wi2_rank_*.json 12 本 +
    wi3_rank_synth_{faithful,first_take}.json 2 本）から事前登録 §4 の
    pair_level_prediction_rule に基づきテスト内で独立に再計算し、記録値と
    一致することを assert する。

    数値が食い違ったら、この結果自体を修正するのではなく停止して報告する
    （タスクブリーフの指示どおり）。
    """
    prereg = _load_yaml(PREREGISTRATION_PATH)
    sealed = _load_yaml(SEALED_MAPPING_PATH)
    responses = _load_yaml(RESPONSES_PATH)
    agg = _load_yaml(AGGREGATION_PATH)

    # sanity: the aggregation rule text itself is the one we implement here.
    assert "same" in prereg["aggregation"]["pair_level_prediction_rule"]

    presentation_order, _ab_swap = _reproduce_shuffle()
    pair_of_slot = {i + 1: pid for i, pid in enumerate(presentation_order)}

    responses_by_pair_id: dict[str, tuple[str, int]] = {}
    for entry in responses["responses"]:
        slot = int(entry["pair"].replace("pair", ""))
        pair_id = pair_of_slot[slot]
        responses_by_pair_id[pair_id] = (_normalize_verdict(entry["verdict"]), entry["confidence"])
    assert set(responses_by_pair_id) == {f"p{i:02d}" for i in range(1, 13)}

    material_axis_rank1 = _build_material_axis_rank1_table()
    computed_pairs = _recompute_pair_predictions(sealed, responses_by_pair_id, material_axis_rank1)

    # --- pair-level cross-check against wi3_aggregation.yaml ---
    recorded_pairs = {p["pair_id"]: p for p in agg["pair_level_predictions"]}
    assert set(computed_pairs) == set(recorded_pairs)

    for pair_id, computed in computed_pairs.items():
        recorded = recorded_pairs[pair_id]
        assert computed["human_verdict"] == recorded["human_verdict"], pair_id
        assert computed["human_confidence"] == recorded["human_confidence"], pair_id
        for axis in AXES:
            c_axis = computed["axes"][axis]
            r_axis = recorded["axes"][axis]
            assert c_axis["prediction"] == r_axis["prediction"], f"{pair_id}/{axis} prediction"
            assert c_axis["abstain"] == r_axis["abstain"], f"{pair_id}/{axis} abstain"
            assert c_axis["outcome"] == r_axis["outcome"], f"{pair_id}/{axis} outcome"

    # --- axis-level summary recompute ---
    for axis in AXES:
        n_decidable = 0
        n_correct = 0
        n_errors = 0
        n_abstain = 0
        error_pairs: list[str] = []
        for pair_id, computed in computed_pairs.items():
            outcome = computed["axes"][axis]["outcome"]
            if outcome == "abstain":
                n_abstain += 1
            else:
                n_decidable += 1
                if outcome == "match":
                    n_correct += 1
                else:
                    n_errors += 1
                    error_pairs.append(pair_id)

        recorded_summary = agg["axis_summary"][axis]
        assert (n_decidable, n_correct, n_errors) == EXPECTED_AXIS_COUNTS[axis], axis
        assert n_decidable + n_abstain == 12, axis
        assert recorded_summary["n_decidable_pairs"] == n_decidable, axis
        assert recorded_summary["n_correct"] == n_correct, axis
        assert recorded_summary["n_errors"] == n_errors, axis
        assert recorded_summary["n_abstain"] == n_abstain, axis

        if axis in EXPECTED_ERROR_PAIR:
            assert error_pairs == [EXPECTED_ERROR_PAIR[axis]], axis
        else:
            assert error_pairs == [], axis

        # P1/P3 contrast-pair classification + proxy v0 adoption condition.
        p1_status = {pid: computed_pairs[pid]["axes"][axis]["outcome"] for pid in P1_PAIR_IDS}
        p3_status = {pid: computed_pairs[pid]["axes"][axis]["outcome"] for pid in P3_PAIR_IDS}
        p1_all_correct = all(status == "match" for status in p1_status.values())
        p3_all_correct = all(status == "match" for status in p3_status.values())
        condition_met = (n_errors <= 1) and p1_all_correct and p3_all_correct

        assert recorded_summary["p1_pair_status"] == p1_status, axis
        assert recorded_summary["p3_pair_status"] == p3_status, axis
        assert recorded_summary["p1_all_correctly_classified"] == p1_all_correct, axis
        assert recorded_summary["p3_all_correctly_classified"] == p3_all_correct, axis
        assert recorded_summary["proxy_v0_adoption_condition_met"] == condition_met, axis
        # pin: this fixture's v0 result is that no axis meets the adoption condition.
        assert condition_met is False, axis

    assert agg["overall_conclusion"]["any_axis_meets_proxy_v0_adoption"] is False
