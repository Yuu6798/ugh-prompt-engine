"""VG-L0 制御軸 probe の結果 JSON と、コミット済み probe 実装の整合を検査する。

背景（PR #289 レビュー指摘・chatgpt-codex-connector）: 正本 record の中核主張は
`gate_synth.py` 本体ではなく **probe の monkeypatch 実装内容**に依存する。
実際、初回実測では probe 自身の欠陥（`is_vowel_flags` の参照時期を誤り patch が
一度も適用されていなかった）で「軸が動かない」という誤った結論を出している。
したがって probe と結果をコミットしたうえで、**pin されたハッシュが実物と
一致していること**を機械で検査する必要がある。

本テストが閉じるのは「probe を編集したが結果 JSON を再生成していない」という
fixture drift。実際に本 PR の作業中にもこの取り違えが 1 度発生している
（lint 修正で probe を編集した直後の結果が旧 sha を pin していた）。

**gate_synth 本体の sha は照合しない**: gate_synth は活発に変更されるため、
無関係な編集で本テストが落ちると偽陽性を量産する。結果 JSON は測定時点の
gate_synth sha を*記録*していれば provenance の役目を果たす（記録された値と
現在の実物がずれること自体は、日付つき実測記録として正常）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PROBES = REPO_ROOT / "voice_genesis" / "evolution" / "probes"
RECORDS = REPO_ROOT / "voice_genesis" / "evolution" / "records"

PROBE_SCRIPT = PROBES / "vgl0_control_axis_probe.py"
REPRO_SCRIPT = PROBES / "vgl0_reproducibility_check.py"
RESULT_JSON = RECORDS / "vgl0_control_axis_probe_result.json"
REPRO_JSON = RECORDS / "vgl0_render_reproducibility_result.json"

# provenance の穴（本体 sha が同じでも monkeypatch で別 WAV が出る）を閉じる
# ために結果へ束縛することを決めた pin キー。
REQUIRED_PIN_KEYS = {
    "probe_script",
    "gate_synth",
    "acoustic_onnx",
    "acoustic_dsconfig",
    "acoustic_phonemes_json",
    "speaker_embed",
    "canon_phonemes",
    "canon_linguistic_onnx",
    "canon_dur_onnx",
    "canon_pitch_onnx",
    "vocoder_onnx",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", [PROBE_SCRIPT, REPRO_SCRIPT, RESULT_JSON, REPRO_JSON])
def test_probe_artifacts_are_committed(path: Path) -> None:
    assert path.exists(), (
        f"{path.relative_to(REPO_ROOT)} が存在しない。probe 実装と結果は "
        "sha だけの参照ではレビュー不能なのでコミットすること"
    )


def test_result_json_pins_the_committed_probe_script() -> None:
    """結果 JSON が pin する probe sha == コミット済み probe の sha。

    落ちたときの正しい対処は **pin を書き換えることではなく probe を再実行して
    結果 JSON を再生成すること**（probe を変えたなら測定結果も変わりうる）。
    """
    pins = _load(RESULT_JSON)["pins"]
    assert pins["probe_script"]["sha256"] == _sha256(PROBE_SCRIPT), (
        "結果 JSON の pins.probe_script.sha256 がコミット済み probe と一致しない。"
        " probe を編集したなら再実行して結果 JSON を作り直すこと"
        " (voice_genesis/evolution/probes/vgl0_control_axis_probe.py --help)"
    )


def test_result_json_binds_every_required_pin() -> None:
    pins = _load(RESULT_JSON)["pins"]
    missing = REQUIRED_PIN_KEYS - set(pins)
    assert not missing, f"結果 JSON に pin されていない入力がある: {sorted(missing)}"
    for key, entry in pins.items():
        digest = entry.get("sha256", "")
        assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest), (
            f"pins.{key}.sha256 が sha256 hex ではない: {digest!r}"
        )


def test_result_json_records_execution_profile() -> None:
    """決定論は ExecutionProfile を固定した上でしか主張できないので、
    どの環境で測ったかを結果自身が持っていること。"""
    profile = _load(RESULT_JSON)["execution_profile"]
    for key in ("python", "platform", "numpy", "onnxruntime", "gate_synth_seed"):
        assert profile.get(key), f"execution_profile.{key} が空"


def test_every_condition_records_measurements_and_invariants() -> None:
    payload = _load(RESULT_JSON)
    conditions = payload["conditions"]
    assert conditions, "条件が 1 件も記録されていない"
    for cond in conditions:
        label = cond.get("label", "<no label>")
        assert "error" not in cond, f"{label}: 実測が失敗している — {cond.get('error')}"
        assert len(cond["wav_sha256"]) == 64, f"{label}: wav_sha256 が無い"
        inv = cond["phone_frame_invariant"]
        assert inv["measured"], f"{label}: 音素長不変条件が実測されていない"
        # 総和保存は run_pipeline が assert しているので破れないはずだが、
        # 「破れていないこと」を結果側にも残して回帰検知に使う。
        assert inv["sum_preserved"], (
            f"{label}: sum(ph_dur) != sum(note_target_frames)")
        assert inv["n_phones_below_1_frame"] == 0, (
            f"{label}: 1 フレーム未満の音素が {inv['n_phones_below_1_frame']} 個ある")


def test_reproducibility_result_is_fresh_process_based() -> None:
    """Render Reproducibility は **独立プロセス間**で確認されていること。

    同一プロセス内の反復は independent replay の証拠にならない（レビュー指摘）
    ので、結果の `in_process_repeat` は別枠であって findings に混ぜない。
    """
    payload = _load(REPRO_JSON)
    checks = {f["check"] for f in payload["findings"]}
    assert "fresh_process_replay" in checks, "fresh-process 反復の検査結果が無い"
    assert "order_independence" in checks, "実行順非依存性の検査結果が無い"
    assert payload["n_processes"] >= 2, (
        f"独立プロセス数が {payload['n_processes']} — 2 以上必要")
    for finding in payload["findings"]:
        assert finding.get("match"), f"再現性検査が不一致: {finding}"
    assert payload["verdict"] == "PASS", f"verdict={payload['verdict']} / {payload['failures']}"
