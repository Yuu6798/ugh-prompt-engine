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

import ast
import hashlib
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PROBES = REPO_ROOT / "voice_genesis" / "evolution" / "probes"
RECORDS = REPO_ROOT / "voice_genesis" / "evolution" / "records"

PROBE_SCRIPT = PROBES / "vgl0_control_axis_probe.py"
REPRO_SCRIPT = PROBES / "vgl0_reproducibility_check.py"
REPRO_JSON = RECORDS / "vgl0_render_reproducibility_result.json"

# 主実測（notes_limit=8）と、フレーズ境界で切った補助実測（6 / 10）。
# **正本 record が引用している結果はすべて検査対象にする** — 一部だけ守ると
# 守られていないファイルで drift が起きる。
RESULT_JSONS = [
    RECORDS / "vgl0_control_axis_probe_result.json",
    RECORDS / "vgl0_control_axis_probe_result_n6.json",
    RECORDS / "vgl0_control_axis_probe_result_n10.json",
]
RESULT_JSON = RESULT_JSONS[0]

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


def _probe_condition_labels() -> set[str]:
    """probe の `CONDITIONS` から条件ラベルを **import せずに** 取り出す。

    probe を import すると `gate_synth` 経由で `onnxruntime` が要求され、
    CI 環境（onnxruntime 無し）では collection error になる。ラベルの正本は
    probe のソースなので、静的に読む。
    """
    tree = ast.parse(PROBE_SCRIPT.read_text(encoding="utf-8"))
    for node in tree.body:
        targets = getattr(node, "targets", []) or [getattr(node, "target", None)]
        names = {t.id for t in targets if isinstance(t, ast.Name)}
        if "CONDITIONS" not in names:
            continue
        assert isinstance(node.value, ast.List), "CONDITIONS がリテラルのリストでない"
        labels = set()
        for elt in node.value.elts:
            assert isinstance(elt, ast.Tuple) and elt.elts, "条件が (label, kwargs) でない"
            label = elt.elts[0]
            assert isinstance(label, ast.Constant) and isinstance(label.value, str)
            labels.add(label.value)
        return labels
    raise AssertionError("probe に CONDITIONS が見つからない")


@pytest.mark.parametrize(
    "path", [PROBE_SCRIPT, REPRO_SCRIPT, REPRO_JSON, *RESULT_JSONS])
def test_probe_artifacts_are_committed(path: Path) -> None:
    assert path.exists(), (
        f"{path.relative_to(REPO_ROOT)} が存在しない。probe 実装と結果は "
        "sha だけの参照ではレビュー不能なのでコミットすること"
    )


@pytest.mark.parametrize("result_json", RESULT_JSONS, ids=lambda p: p.stem)
def test_result_json_pins_the_committed_probe_script(result_json: Path) -> None:
    """結果 JSON が pin する probe sha == コミット済み probe の sha。

    落ちたときの正しい対処は **pin を書き換えることではなく probe を再実行して
    結果 JSON を再生成すること**（probe を変えたなら測定結果も変わりうる）。
    """
    pins = _load(result_json)["pins"]
    assert pins["probe_script"]["sha256"] == _sha256(PROBE_SCRIPT), (
        f"{result_json.name}: "
        "結果 JSON の pins.probe_script.sha256 がコミット済み probe と一致しない。"
        " probe を編集したなら再実行して結果 JSON を作り直すこと"
        " (voice_genesis/evolution/probes/vgl0_control_axis_probe.py --help)"
    )


@pytest.mark.parametrize("result_json", RESULT_JSONS, ids=lambda p: p.stem)
def test_result_json_binds_every_required_pin(result_json: Path) -> None:
    pins = _load(result_json)["pins"]
    missing = REQUIRED_PIN_KEYS - set(pins)
    assert not missing, f"結果 JSON に pin されていない入力がある: {sorted(missing)}"
    for key, entry in pins.items():
        digest = entry.get("sha256", "")
        assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest), (
            f"pins.{key}.sha256 が sha256 hex ではない: {digest!r}"
        )


@pytest.mark.parametrize("result_json", RESULT_JSONS, ids=lambda p: p.stem)
def test_result_json_records_execution_profile(result_json: Path) -> None:
    """決定論は ExecutionProfile を固定した上でしか主張できないので、
    どの環境で測ったかを結果自身が持っていること。"""
    profile = _load(result_json)["execution_profile"]
    for key in ("python", "platform", "numpy", "onnxruntime", "gate_synth_seed"):
        assert profile.get(key), f"execution_profile.{key} が空"


@pytest.mark.parametrize("result_json", RESULT_JSONS, ids=lambda p: p.stem)
def test_every_condition_records_measurements_and_invariants(result_json: Path) -> None:
    payload = _load(result_json)
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


@pytest.mark.parametrize("result_json", RESULT_JSONS, ids=lambda p: p.stem)
def test_consumed_model_bytes_match_the_pins(result_json: Path) -> None:
    """推論へ実際に渡ったバイト列の hash が、pin と一致していること。

    パスを別 read して pin するだけでは、その read と合成が使う read の間に
    差し替えが起きたときに記録と実体がずれる（レビュー指摘）。probe は
    `load_model_bundle_bytes` が返した**そのバッファ**を hash している。
    """
    payload = _load(result_json)
    check = payload["consumed_model_bytes_check"]
    assert check["ok"], f"{result_json.name}: {check['mismatches']}"
    assert check["distinct_bundles"] == 1, (
        f"{result_json.name}: 条件間でモデルバイト列が異なる "
        f"(distinct={check['distinct_bundles']})")
    pins = payload["pins"]
    for cond in payload["conditions"]:
        consumed = cond["consumed_model_sha256"]
        assert consumed["acoustic_onnx"] == pins["acoustic_onnx"]["sha256"]
        assert consumed["vocoder_onnx"] == pins["vocoder_onnx"]["sha256"]


def test_reproducibility_result_binds_the_input_pin_set() -> None:
    """PASS が「どのモデル・楽譜に対する PASS か」を結果から辿れること。"""
    payload = _load(REPRO_JSON)
    pins = payload.get("pins")
    assert pins, "再現性結果に入力 pin セットが束縛されていない"
    missing = REQUIRED_PIN_KEYS - set(pins)
    assert not missing, f"再現性結果の pins に欠けがある: {sorted(missing)}"
    assert pins["probe_script"]["sha256"] == _sha256(PROBE_SCRIPT)
    # 主実測と同じ入力に対する verdict であること
    main_pins = _load(RESULT_JSON)["pins"]
    for key in sorted(REQUIRED_PIN_KEYS):
        assert pins[key]["sha256"] == main_pins[key]["sha256"], (
            f"再現性結果と主実測で {key} の pin が違う")


def test_reproducibility_covers_every_probe_condition() -> None:
    """順序非依存性の検査が probe の全条件を覆っていること。

    両実行が同じ条件を揃って落とすと突き合わせだけでは検出できないため、
    期待条件集合そのものと照合する（レビュー指摘）。
    """
    expected = _probe_condition_labels()
    payload = _load(REPRO_JSON)
    covered = {f["label"] for f in payload["findings"]
               if f["check"] == "order_independence"}
    assert covered == expected, (
        f"order_independence が全条件を覆っていない: 欠け={sorted(expected - covered)} / "
        f"余分={sorted(covered - expected)}")


def test_reproducibility_result_pins_its_own_checker_and_probe() -> None:
    """PASS 判定を出したコード自身が結果に束縛されていること。

    probe だけ pin して検査スクリプトを pin しないと、「どのロジックで PASS に
    なったか」が canonical provenance から辿れない（レビュー指摘）。
    """
    payload = _load(REPRO_JSON)
    assert payload["checker_script"]["sha256"] == _sha256(REPRO_SCRIPT), (
        "再現性結果の checker_script.sha256 がコミット済みスクリプトと一致しない。"
        " 検査スクリプトを編集したなら再実行して結果を作り直すこと")
    assert payload["probe_script"]["sha256"] == _sha256(PROBE_SCRIPT), (
        "再現性結果が pin する probe sha がコミット済み probe と一致しない")


def test_every_probe_subprocess_passed_all_gates() -> None:
    """各サブプロセスが rc=0 かつ消費バイト検査 ok であること。

    条件レベルの `error` だけを見ると、probe の provenance ゲート
    （消費バイトと pin の一致）が落ちた場合を見逃す — そちらは終了コードに
    しか出ない。WAV hash が一致しているだけで PASS にならないよう、
    起動ごとのゲート結果を検査する（レビュー指摘 P1）。
    """
    runs = _load(REPRO_JSON)["probe_runs"]
    assert runs, "サブプロセスのゲート結果が記録されていない"
    # replay 4 条件 x 2 プロセス + 順序 2 プロセス = 10
    assert len(runs) == 10, f"検査したサブプロセス数が 10 でない: {len(runs)}"
    for run in runs:
        assert run["returncode"] == 0, f"{run['tag']}: rc={run['returncode']}"
        assert run["consumed_ok"], f"{run['tag']}: 消費バイト検査が ok でない"
        assert not run["failures"], f"{run['tag']}: {run['failures']}"


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
