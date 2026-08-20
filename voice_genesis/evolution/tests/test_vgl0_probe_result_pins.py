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

SNAPSHOTS = PROBES / "snapshots"
SNAPSHOT_INDEX = SNAPSHOTS / "index.json"

# 正本結果ごとの **幾何**（レビュー指摘 P2・9 巡目）。`conditions` が空でない
# ことだけを見ていると、`--only baseline` や `--notes-limit` 指定漏れで
# 作り直された結果でも全アサーションが通ってしまい、「17 条件 / notes limit
# 6・8・10」という record の主張が支えを失ったままスイートは緑になる。
EXPECTED_GEOMETRY = {
    "vgl0_control_axis_probe_result.json": 8,
    "vgl0_control_axis_probe_result_n6.json": 6,
    "vgl0_control_axis_probe_result_n10.json": 10,
}

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


SNAPSHOT_SCHEMA = "vgl0-probe-snapshot/0.1"


def _snapshot_index() -> dict:
    """凍結台帳を読む。**schema 判別子を検査してから**中身を消費する。

    宣言した discriminator を読み飛ばすと、typo や将来の非互換 schema が
    0.1 として解釈され、壊れた provenance メタデータを黙って通す
    （レビュー指摘 P2）。
    """
    index = _load(SNAPSHOT_INDEX)
    assert index.get("schema") == SNAPSHOT_SCHEMA, (
        f"snapshots/index.json の schema が未対応: {index.get('schema')!r} "
        f"(対応 = {SNAPSHOT_SCHEMA})")
    _assert_registry_is_unambiguous(index)
    return index


def _assert_registry_is_unambiguous(index: dict) -> None:
    """台帳が **矛盾する状態を表現できない**こと（レビュー指摘 P2）。

    重複行があると、読み手（`{d["script"]: d for d in ...}` のような畳み込み）が
    黙って片方を捨て、**古い宣言と新しい宣言が同居**したまま検査が通る。
    重複は「どちらが正か」を台帳が答えられない状態なので、読み込み時点で落とす。

    1 件だけ塞ぐと同型の穴が残るので、一意であるべきものを**全数**列挙する:

    - `live_unmeasured` の `script`（同じ実装に 2 つの未実測宣言）
    - `snapshots` の `file`（同じ凍結ファイルが 2 行）
    - `snapshots` の `(script, sha256)`（同じ版が 2 行）
    - 各 entry 内の `measured_results`（同じ結果を 2 度帰属）
    - **結果ファイルの帰属先**（1 つの結果は 1 つの版からしか生まれない）
    """
    def _dupes(values):
        seen, dupes = set(), []
        for v in values:
            (dupes.append(v) if v in seen else seen.add(v))
        return sorted(set(dupes), key=str)

    live = index.get("live_unmeasured") or []
    bad = _dupes([e["script"] for e in live])
    assert not bad, (
        f"live_unmeasured に同じ script の宣言が複数ある: {bad}。"
        " どちらが正か台帳が答えられないので、1 実装 1 宣言にすること")

    snaps = index.get("snapshots") or []
    bad = _dupes([e["file"] for e in snaps])
    assert not bad, f"snapshots に同じ file の行が複数ある: {bad}"
    bad = _dupes([(e["script"], e["sha256"]) for e in snaps])
    assert not bad, f"snapshots に同じ (script, sha256) の行が複数ある: {bad}"

    owners: dict[str, list[str]] = {}
    for entry in snaps:
        bad = _dupes(entry["measured_results"])
        assert not bad, f"{entry['file']}: measured_results に重複がある: {bad}"
        for name in entry["measured_results"]:
            owners.setdefault((name, entry["script"]), []).append(entry["file"])
    contested = {k: v for k, v in owners.items() if len(v) > 1}
    assert not contested, (
        f"同じ結果が同じ script の複数 snapshot に帰属している: {contested}。"
        " 1 つの結果を生んだ版は 1 つしかない")


def _snapshot_path(entry: dict) -> Path:
    """台帳の `file` を **registry ディレクトリ内**へ限定して解決する。

    `file` に絶対パスや `../` が入ると `SNAPSHOTS / entry["file"]` が
    `probes/snapshots` の外を指し、マシンローカルのバイト列を「コミット済み
    実装」として hash してしまう（レビュー指摘 P2）。字句検査と解決後の
    包含検査の両方で弾く。
    """
    raw = entry["file"]
    assert raw and "/" not in raw and "\\" not in raw and raw not in (".", ".."), (
        f"snapshot の file はディレクトリ区切りを含まない単一のファイル名で"
        f"あること: {raw!r}")
    assert not Path(raw).is_absolute(), f"snapshot の file が絶対パス: {raw!r}"
    resolved = (SNAPSHOTS / raw).resolve()
    assert resolved.is_relative_to(SNAPSHOTS.resolve()), (
        f"snapshot {raw!r} が probes/snapshots の外を指している: {resolved}")
    return resolved


def _record_path(name: str) -> Path:
    """台帳の結果名を **`evolution/records` 内**へ限定して解決する。

    `snapshots[].file` は先に閉じたが、**`measured_results` と
    `revalidation_results` は同じ形のまま `RECORDS / name` へ join していた**
    （レビュー指摘 P2）。絶対パスや `../` が入れば records 外の JSON を読み、
    細工した外部ファイルが snapshot 帰属や再検証被覆を満たしてしまう。
    台帳由来の文字列がパスになる箇所は**全数**この helper を通す。
    """
    assert name and "/" not in name and "\\" not in name and name not in (".", ".."), (
        f"結果名はディレクトリ区切りを含まない単一のファイル名であること: {name!r}")
    assert not Path(name).is_absolute(), f"結果名が絶対パス: {name!r}"
    resolved = (RECORDS / name).resolve()
    assert resolved.is_relative_to(RECORDS.resolve()), (
        f"結果名 {name!r} が evolution/records の外を指している: {resolved}")
    return resolved


def _pinned_script_sha(result_name: str, script_name: str) -> str | None:
    """正本結果が **その script について** pin している sha を取り出す。

    probe は `pins.probe_script`（再現性結果も pins を持つ）、checker は
    結果直下の `checker_script`。対応が無ければ None。
    """
    payload = _load(_record_path(result_name))
    if script_name == PROBE_SCRIPT.name:
        pinned = (payload.get("pins") or {}).get("probe_script")
        return (pinned or {}).get("sha256")
    if script_name == REPRO_SCRIPT.name:
        return (payload.get("checker_script") or {}).get("sha256")
    return None


def _resolve_source(script: Path, sha: str) -> Path:
    """pin された sha を **repo 内の実体**へ解決する（live か snapshot）。

    live 実装と一致すればそれが正解。一致しないときは
    `probes/snapshots/` に凍結された「その結果を生んだ版」を探す
    （運用は `probes/snapshots/README.md`）。どちらでも解決できなければ
    fixture drift = 「実装を編集したが結果を再生成せず、旧版も凍結して
    いない」なので落とす。
    """
    if _sha256(script) == sha:
        return script
    for entry in _snapshot_index()["snapshots"]:
        if entry["script"] != script.name or entry["sha256"] != sha:
            continue
        frozen = _snapshot_path(entry)
        assert frozen.exists(), f"index.json が指す snapshot が無い: {entry['file']}"
        assert _sha256(frozen) == sha, (
            f"snapshot {entry['file']} の実体が index.json の sha と一致しない")
        return frozen
    raise AssertionError(
        f"pin された {script.name} の sha {sha[:12]}… が live 実装とも "
        f"snapshot とも一致しない。実装を編集したなら (a) 再実行して結果 JSON を "
        f"作り直すか、(b) 実測を生んだ版を probes/snapshots/ へ凍結して "
        f"index.json へ登録すること（probes/snapshots/README.md）")


def _probe_condition_labels(source: Path = PROBE_SCRIPT) -> set[str]:
    """probe の `CONDITIONS` から条件ラベルを **import せずに** 取り出す。

    probe を import すると `gate_synth` 経由で `onnxruntime` が要求され、
    CI 環境（onnxruntime 無し）では collection error になる。ラベルの正本は
    probe のソースなので、静的に読む。

    `source` は既定で live 実装だが、**結果 JSON の幾何を検査するときは
    その結果を生んだ版**（`_resolve_source`）を渡す。期待集合を live から
    取ると、条件を増減した瞬間に過去の結果が「欠けている」と誤検出される。
    """
    tree = ast.parse(source.read_text(encoding="utf-8"))
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
    # live 実装と一致しない場合は、その結果を生んだ版が snapshot として
    # 凍結・登録されていることを要求する（drift はここで落ちる）。
    _resolve_source(PROBE_SCRIPT, pins["probe_script"]["sha256"])


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
    _resolve_source(PROBE_SCRIPT, pins["probe_script"]["sha256"])
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
    payload = _load(REPRO_JSON)
    expected = _probe_condition_labels(
        _resolve_source(PROBE_SCRIPT, payload["probe_script"]["sha256"]))
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
    _resolve_source(REPRO_SCRIPT, payload["checker_script"]["sha256"])
    _resolve_source(PROBE_SCRIPT, payload["probe_script"]["sha256"])


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
        assert run["consumed_ok"], f"{run['tag']}: 消費モデルバイト検査が ok でない"
        # 楽譜バイトゲートは 7 巡目フォローアップで追加。導入前の checker が
        # 生んだ正本にはキーが無いので、**在るときだけ**検査する（rc ゲートが
        # 落ちる側は上の returncode で覆われている）。
        if run.get("consumed_score_ok") is not None:
            assert run["consumed_score_ok"], (
                f"{run['tag']}: 実行された楽譜バイト検査が ok でない")
        assert not run["failures"], f"{run['tag']}: {run['failures']}"


def test_checker_self_pin_is_load_time_and_stable() -> None:
    """検査スクリプトの sha が **ロード時に固定**され、実行後も一致していること。

    実行後にディスクから読むと、10 本のサブプロセスが走る数分の間にファイルが
    編集された場合「判定を出したのは旧コード / 記録は新ファイルの hash」に
    なり、この fixture テストが別実装を検証してしまう（レビュー指摘 P2）。
    """
    payload = _load(REPRO_JSON)
    checker = payload["checker_script"]
    committed = _resolve_source(REPRO_SCRIPT, checker["sha256"])
    assert checker["sha256_after_run"] == _sha256(committed), (
        "実行後の再 hash が、判定を出した版のスクリプトと一致しない")
    stable = next(f for f in payload["findings"]
                  if f["check"] == "checker_unchanged_during_run")
    assert stable["match"], f"実行中に検査スクリプトが変化した: {stable}"


def test_pins_identical_across_every_subprocess() -> None:
    """10 プロセスすべてが同一の pin セットで走ったこと。

    forward の pin だけ保持して比較しないと、**出力に影響しないバイト変更**が
    途中で入っても WAV は一致し PASS が出る（レビュー指摘 P2）。
    """
    payload = _load(REPRO_JSON)
    finding = next(f for f in payload["findings"]
                   if f["check"] == "pins_identical_across_processes")
    assert finding["match"], f"プロセス間で pin が異なる: {finding['diffs']}"
    assert finding["n_compared"] == 10, (
        f"pin を比較したプロセス数が 10 でない: {finding['n_compared']}")


@pytest.mark.parametrize("result_json", RESULT_JSONS, ids=lambda p: p.stem)
def test_implementation_pins_are_load_time_and_stable(result_json: Path) -> None:
    """probe / gate_synth の sha が **import 前に固定**され、実行後も不変。

    import 後に read すると、その間の編集で「実行は旧 in-memory 定義 / 記録は
    新 hash」になり、この fixture が**実行されていないコード**に対する PASS を
    検証してしまう（レビュー指摘 P2）。
    """
    check = _load(result_json)["implementation_pin_check"]
    assert check["ok"], (
        f"{result_json.name}: 実行中に実装が変化した {check['changed_during_run']}")
    measured = _resolve_source(PROBE_SCRIPT, check["sha_at_load"]["probe_script"])
    assert check["sha_after_run"]["probe_script"] == _sha256(measured)
    # gate_synth はロード時と実行後で一致していることだけ見る（コミット済み
    # 実体との照合はしない — 活発に変更されるため偽陽性になる）
    assert (check["sha_at_load"]["gate_synth"]
            == check["sha_after_run"]["gate_synth"])


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


@pytest.mark.parametrize("result_json", RESULT_JSONS, ids=lambda p: p.stem)
def test_canonical_result_geometry_supports_the_record(result_json: Path) -> None:
    """正本結果が **record の主張どおりの幾何**で測られていること。

    `conditions` が非空であることだけを検査していると、`--only baseline` で
    作り直した結果や `--notes-limit` を渡し忘れた結果でも後続の全アサーションが
    「たまたま在る行」だけを見て通る。record は「1 走行 17 条件」「notes limit
    は 6 / 8 / 10」を主張しているので、その 3 点を機械で固定する
    （レビュー指摘 P2・9 巡目）。

    期待条件集合は **その結果を生んだ版**の `CONDITIONS` から取る。live から
    取ると、条件を増減した瞬間に過去の正本が誤って落ちる。
    """
    payload = _load(result_json)
    source = _resolve_source(PROBE_SCRIPT, payload["pins"]["probe_script"]["sha256"])
    expected = _probe_condition_labels(source)

    assert payload["single_condition"] is None, (
        f"{result_json.name}: --only={payload['single_condition']} で作られた"
        " 単一条件の結果が正本の位置に置かれている")
    assert payload["order"] == "forward", (
        f"{result_json.name}: 正本は forward 順の走行であること"
        f"（order={payload['order']}）")

    labels = [c["label"] for c in payload["conditions"]]
    dupes = sorted({lbl for lbl in labels if labels.count(lbl) > 1})
    assert not dupes, f"{result_json.name}: 条件ラベルが重複している {dupes}"
    assert set(labels) == expected, (
        f"{result_json.name}: 条件集合が測定版の CONDITIONS と一致しない "
        f"欠け={sorted(expected - set(labels))} / 余分={sorted(set(labels) - expected)}")

    want = EXPECTED_GEOMETRY[result_json.name]
    assert payload["notes_limit"] == want, (
        f"{result_json.name}: notes_limit={payload['notes_limit']} だが "
        f"record はこのファイルを notes_limit={want} の走行として引用している")


@pytest.mark.parametrize("result_json", RESULT_JSONS, ids=lambda p: p.stem)
def test_consumed_score_bytes_match_the_pins(result_json: Path) -> None:
    """実行された楽譜バイトが pin と一致していること。

    `load_song_module()` は消費した楽譜モジュールの digest を per-call で返す。
    それを捨てると、走行途中で `score.py` / `phoneme_jp.py` が差し替わっても
    「古い pin を記録したまま新しいバイトで鳴らした WAV」が success で残る
    （レビュー指摘 P2・7 巡目）。

    この検査を持たない版で測った正本もあるため、**キーがある結果にだけ**
    適用する。live 実装が検査を出すことは
    `test_live_probe_still_verifies_consumed_score_bytes` が別に固定する。
    """
    payload = _load(result_json)
    check = payload.get("consumed_score_bytes_check")
    if check is None:
        pytest.skip("この正本は楽譜バイト検査の導入前に測られている")
    assert check["ok"], f"{result_json.name}: {check['mismatches']}"
    assert check["distinct_bundles"] == 1, (
        f"{result_json.name}: 条件間で楽譜バイト列が異なる "
        f"(distinct={check['distinct_bundles']})")
    pins = payload["pins"]
    for cond in payload["conditions"]:
        for key, digest in (cond.get("consumed_score_sha256") or {}).items():
            assert pins[key]["sha256"] == digest, (
                f"{cond['label']}: 実行した楽譜 {key} が pins と不一致")


def test_live_probe_still_verifies_consumed_score_bytes() -> None:
    """live probe が楽譜バイト検査を保持していること（是正の逆戻り検知）。

    正本側は導入前の版で測られているので skip されうる。**実装側**が
    検査を落としていないことは静的に固定しておく。
    """
    src = PROBE_SCRIPT.read_text(encoding="utf-8")
    for token in ("consumed_score_sha256", "verify_consumed_score_bytes",
                  "consumed_score_bytes_check"):
        assert token in src, f"probe から {token} が消えている"


def test_snapshot_index_matches_its_files() -> None:
    """凍結台帳の各行が、実体・sha・参照先の 3 点で整合していること。"""
    index = _snapshot_index()
    for entry in index["snapshots"]:
        frozen = _snapshot_path(entry)
        assert frozen.exists(), f"index.json が指す snapshot が無い: {entry['file']}"
        assert _sha256(frozen) == entry["sha256"], (
            f"{entry['file']}: 実体の sha が index.json の記載と一致しない")
        assert entry["measured_results"], (
            f"{entry['file']}: この版が生んだ結果が 1 件も登録されていない")
        for name in entry["measured_results"]:
            assert _record_path(name).exists(), (
                f"{entry['file']}: 参照先の結果 {name} が無い")
            # **帰属そのものを検証する**（レビュー指摘 P2）: 存在確認だけだと、
            # 正本の一部だけを live 版で再生成したとき、その結果が古い
            # snapshot の measured_results に残り続け、台帳が「この版が生んだ」
            # と偽って主張する。pin を突き合わせて部分再実測での汚染を防ぐ。
            pinned = _pinned_script_sha(name, entry["script"])
            assert pinned == entry["sha256"], (
                f"{entry['file']}: {name} は {entry['script']} の sha "
                f"{(pinned or '<pin 無し>')[:12]}… を pin しており、この snapshot "
                f"({entry['sha256'][:12]}…) が生んだ結果ではない。再実測したなら "
                f"measured_results から外すこと")

    listed = {_snapshot_path(e).name for e in index["snapshots"]}
    on_disk = {p.name for p in SNAPSHOTS.glob("*.py")}
    assert on_disk == listed, (
        f"snapshots/ の中身と index.json がずれている: "
        f"未登録={sorted(on_disk - listed)} / 実体無し={sorted(listed - on_disk)}")


def test_no_orphan_snapshots() -> None:
    """どの結果からも pin されていない snapshot を残さない。

    実測をやり直して結果を作り直したら、古い版は参照が切れる。放置すると
    「凍結してあるから安全」という誤った印象だけが残るので、掃除を強制する。
    """
    pinned = set()
    for result_json in RESULT_JSONS:
        pinned.add(_load(result_json)["pins"]["probe_script"]["sha256"])
    repro = _load(REPRO_JSON)
    pinned.add(repro["probe_script"]["sha256"])
    pinned.add(repro["checker_script"]["sha256"])

    for entry in _snapshot_index()["snapshots"]:
        assert entry["sha256"] in pinned, (
            f"{entry['file']} はどの結果からも pin されていない孤児。"
            " 参照が切れた snapshot は index.json ごと削除すること")


def _revalidation_coverage(entry: dict, script: Path) -> tuple[set[str], set[str]]:
    """宣言された再検証セットのうち、live sha を pin できている結果 / いない結果。"""
    live = _sha256(script)
    required = set(entry["revalidation_results"])
    covered = {name for name in required
               if _pinned_script_sha(name, script.name) == live}
    return covered, required - covered


def test_live_scripts_are_measured_or_declared_unmeasured() -> None:
    """live 実装が「**完全に**実測済み」か「未実測と宣言済み」のどちらかであること。

    snapshot 機構は再実測の免除ではない。live 実装がどの正本からも pin されて
    いないなら、それは **実測証拠の無いコード**であり、`live_unmeasured` に
    理由と再検証条件を書かせて可視化する（黙って PASS の余韻を借りない）。

    **部分再実測で宣言を消させない**（レビュー指摘 P2）: 「どれか 1 本でも live
    sha を pin していれば実測済み」とすると、`revalidation` が要求する 6/8/10
    ノート 3 走行 + 10 プロセス再現性のうち 1 本を作り直しただけで宣言が消え、
    **残りの幾何が未実測であるという唯一の警告が失われる**。宣言に
    `revalidation_results`（再検証で作り直すべき結果の全数）を持たせ、
    **全数が live sha を pin して初めて実測済み**とする。

    **宣言は live sha へ束縛する**（外部レビュー指摘）: live を再編集したら、
    古い版に対する未実測宣言を流用できない。
    """
    declared = {d["script"]: d for d in _snapshot_index()["live_unmeasured"]}
    for script in (PROBE_SCRIPT, REPRO_SCRIPT):
        entry = declared.get(script.name)
        if entry is None:
            # 宣言が無いなら「実測済み」の主張。全 revalidation 対象が live sha を
            # pin していることを、snapshot 側の宣言に頼らず直接確かめる。
            live = _sha256(script)
            names = [r.name for r in (*RESULT_JSONS, REPRO_JSON)]
            relevant = [n for n in names
                        if _pinned_script_sha(n, script.name) is not None]
            stale = [n for n in relevant if _pinned_script_sha(n, script.name) != live]
            assert not stale, (
                f"{script.name}: live_unmeasured に宣言が無い（= 実測済みの主張）"
                f"のに、{stale} が別の版を pin している。未実測なら宣言を書くこと")
            continue

        assert entry.get("sha256") == _sha256(script), (
            f"{script.name}: live_unmeasured.sha256 が現在の live sha と一致しない。"
            " live を編集したら、古い版に対する未実測宣言は流用できない"
            " — 宣言を書き直すか、再実測して宣言を消すこと")
        for key in ("reason", "revalidation"):
            assert entry.get(key), f"{script.name}: live_unmeasured.{key} が空"
        assert entry.get("revalidation_results"), (
            f"{script.name}: revalidation_results（再検証で作り直す結果の全数）が空"
            " — 何をもって実測済みとするかが機械で判定できない")

        covered, remaining = _revalidation_coverage(entry, script)
        assert remaining, (
            f"{script.name}: revalidation_results が全数 live sha を pin している"
            f"（= 再検証完了）のに宣言が残っている。該当行を消すこと")
        # 部分再実測は「宣言を消してよい」ではなく「まだ途中」。covered が
        # 非空でも宣言は残り続ける（このアサートが無いと逆に消せてしまう）。
        assert covered != set(entry["revalidation_results"])


def test_declared_revalidation_sets_name_real_results() -> None:
    """`revalidation_results` が実在する正本を指し、その script の pin を持つこと。"""
    for entry in _snapshot_index()["live_unmeasured"]:
        assert entry["revalidation_results"], f"{entry['script']}: 再検証セットが空"
        for name in entry["revalidation_results"]:
            assert _record_path(name).exists(), (
                f"{entry['script']}: revalidation_results の {name} が無い")
            assert _pinned_script_sha(name, entry["script"]) is not None, (
                f"{entry['script']}: {name} は {entry['script']} の sha を pin して"
                f"いないので、再検証の対象になり得ない")


def test_registry_result_names_stay_inside_records() -> None:
    """台帳が名指しする結果名が **records 配下**に閉じていること。

    `snapshots[].file` と同型の穴が `measured_results` /
    `revalidation_results` に残っていた（レビュー指摘 P2）。台帳由来の文字列が
    パスになる箇所は全数 `_record_path` を通す — ここはその契約の番人。
    """
    index = _snapshot_index()
    names = [n for e in index["snapshots"] for n in e["measured_results"]]
    names += [n for e in index["live_unmeasured"] for n in e["revalidation_results"]]
    assert names, "台帳が結果を 1 件も名指ししていない"
    for name in names:
        assert _record_path(name).exists()

    for bad in ("../secret.json", "/etc/passwd", "sub/dir.json", "..", ""):
        with pytest.raises(AssertionError):
            _record_path(bad)


def test_checker_binds_the_probe_module_version_it_imported() -> None:
    """checker が **自分で import した probe の版**を verdict へ束縛すること。

    checker 側の検証（衝突ガード・期待条件集合）は import 時の in-memory 定義で
    走る。その版が実行後の実体ともサブプロセスの pin とも一致して初めて、PASS を
    probe のバイト列へ帰属できる（レビュー指摘 P2）。

    導入前に測った正本にはこの finding が無いので、**在るときだけ**内容を
    検査し、実装側が出し続けることは静的に固定する。
    """
    payload = _load(REPRO_JSON)
    finding = next((f for f in payload["findings"]
                    if f["check"] == "checker_probe_module_pinned"), None)
    if finding is not None:
        assert finding["match"], f"checker の probe 版が一致していない: {finding}"
        assert finding["sha_at_import"] == finding["sha_after_run"]
        assert finding["sha_at_import"] == finding["sha_subprocess_pin"]

    src = REPRO_SCRIPT.read_text(encoding="utf-8")
    for token in ("_PROBE_SHA_AT_IMPORT", "checker_probe_module_pinned"):
        assert token in src, f"checker から {token} が消えている"
