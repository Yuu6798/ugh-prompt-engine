"""VG-L0 probe / checker の **破壊的書き込みガード**を実測なしで検査する。

PR #289 の 7 巡目レビュー（chatgpt-codex-connector）が指摘した 2 件を閉じた
ことを、モデル資産なしで固定するためのテスト:

1. **checker の派生 acoustic 入力が保護されていない** — `--acoustic-onnx` が
   `--acoustic-dir` の外にあると、そこから派生する `*.phonemes.json` /
   `*.<spk>.emb` は checker 側の保護リストに載らず、`--result-json` がそれを
   指すと全 probe 完走後にモデル入力を上書きする
2. **所有外の checker result ファイルを unlink する** — `--work-dir` を既存の
   無関係なディレクトリにすると、`order_forward.json` のような固定名の
   ファイルが所有検査なしに消える

どちらも「合成が終わってから入力を壊す」型なので、**起動前に fail-closed で
弾けているか**だけを見る。ONNX 資産も onnxruntime も要らない。

`gate_synth` が module import 時に `onnxruntime` を要求するため、import だけ
スタブで通す（実行経路には一切入らない — 本テストはどれも合成の手前で
`SystemExit` になることを確認する）。
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PROBES = REPO_ROOT / "voice_genesis" / "evolution" / "probes"


def _import_checker():
    """onnxruntime をスタブして checker を import する。

    スタブは import の間だけ差し込み、後片付けする（他テストが本物の
    onnxruntime の有無で分岐するのを壊さないため）。
    """
    if "vgl0_reproducibility_check" in sys.modules:
        return sys.modules["vgl0_reproducibility_check"]
    stub = types.ModuleType("onnxruntime")
    stub.InferenceSession = object
    stub.get_available_providers = lambda: ["CPUExecutionProvider"]
    stub.__version__ = "0.0.0-stub"
    had = "onnxruntime" in sys.modules
    sys.modules.setdefault("onnxruntime", stub)
    sys.path.insert(0, str(PROBES))
    try:
        import vgl0_reproducibility_check as chk  # noqa: PLC0415
    except ModuleNotFoundError as exc:  # numpy / soundfile / yaml が無い環境
        pytest.skip(f"probe の依存が無い環境: {exc}")
    finally:
        sys.path.remove(str(PROBES))
        if not had and sys.modules.get("onnxruntime") is stub:
            del sys.modules["onnxruntime"]
    return chk


@pytest.fixture(scope="module")
def chk():
    return _import_checker()


def _model_args(tmp_path: Path) -> list[str]:
    """`--acoustic-onnx` を `--acoustic-dir` の **外**に置いた引数一式。

    この配置が指摘 1 の前提そのもの（派生ファイルは onnx のパスから作られる
    ので、acoustic-dir だけを保護しても覆えない）。
    """
    acoustic_dir = tmp_path / "export"
    onnx_dir = tmp_path / "elsewhere"
    acoustic_dir.mkdir(exist_ok=True)
    onnx_dir.mkdir(exist_ok=True)
    return [
        "--canon-dir", str(tmp_path / "canon"),
        "--vocoder-dir", str(tmp_path / "vocoder"),
        "--acoustic-dir", str(acoustic_dir),
        "--acoustic-onnx", str(onnx_dir / "acoustic_v1.onnx"),
        "--speaker", "ritsu",
    ]


def _derived(tmp_path: Path, suffix: str) -> Path:
    return tmp_path / "elsewhere" / f"acoustic_v1{suffix}"


@pytest.mark.parametrize(
    "suffix", [".phonemes.json", ".ritsu.emb", ".onnx"])
def test_checker_refuses_result_json_pointing_at_a_derived_model_input(
    chk, tmp_path: Path, suffix: str,
) -> None:
    """派生入力（phonemes.json / 話者 embed）と本体 onnx を、checker が
    `--result-json` の書き込み先として拒否すること。

    保護リストを checker 側で書き写していた頃は、`--acoustic-dir` の外に
    ある onnx から派生する 2 ファイルが抜けていた（レビュー指摘 P2）。
    いまは probe の `ProbeConfig.protected_inputs()` を流用しているので、
    probe が知っている入力は自動で覆われる。
    """
    target = _derived(tmp_path, suffix)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"model-bytes")
    with pytest.raises(SystemExit):
        chk.main([*_model_args(tmp_path),
                  "--work-dir", str(tmp_path / "work"),
                  "--result-json", str(target)])
    # 弾かれた = 1 バイトも触っていないこと
    assert target.read_bytes() == b"model-bytes"


def test_checker_refuses_result_json_inside_the_singer_tree(
    chk, tmp_path: Path,
) -> None:
    """既定 singer ディレクトリの中身（楽譜）も書き込み先にできないこと。"""
    # checker が保持している probe モジュールを使う。ここで
    # `import vgl0_control_axis_probe` と書くと、checker が import 時に
    # `sys.path` へ入れた副作用に依存してしまう（本テストの fixture は
    # 後片付けで path を戻すため、順序次第で ImportError になりうる）。
    score = chk.probe_mod.DEFAULT_SINGER_DIR / "score.py"
    with pytest.raises(SystemExit):
        chk.main([*_model_args(tmp_path),
                  "--work-dir", str(tmp_path / "work"),
                  "--result-json", str(score)])


def test_claim_work_dir_marks_a_fresh_directory(chk, tmp_path: Path) -> None:
    work = chk.claim_work_dir(tmp_path / "work")
    assert (work / chk.WORK_OWNER_MARKER).exists()
    # 2 度目は所有済みなので通る（再実行を妨げない）
    chk.claim_work_dir(work)


def test_claim_work_dir_refuses_an_unowned_non_empty_directory(
    chk, tmp_path: Path,
) -> None:
    """既存の無関係なディレクトリを work に使わせない。

    ここを通すと、その直下の `order_forward.json` 等の固定名ファイルが
    起動ごとに unlink される（レビュー指摘 P2）。
    """
    work = tmp_path / "someone_elses"
    work.mkdir()
    victim = work / "order_forward.json"
    victim.write_text("大事なデータ", encoding="utf-8")
    with pytest.raises(SystemExit):
        chk.claim_work_dir(work)
    assert victim.read_text(encoding="utf-8") == "大事なデータ"


def test_claim_work_dir_accepts_an_existing_empty_directory(
    chk, tmp_path: Path,
) -> None:
    work = tmp_path / "empty"
    work.mkdir()
    chk.claim_work_dir(work)
    assert (work / chk.WORK_OWNER_MARKER).exists()


def test_run_probe_refuses_to_unlink_outside_the_owned_work_dir(
    chk, tmp_path: Path,
) -> None:
    """結果ファイルが所有 work ディレクトリの直下にないなら、
    サブプロセスを起動する前に止まること（unlink の手前）。"""
    work = chk.claim_work_dir(tmp_path / "work")
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit):
        chk.run_probe("/nonexistent/python", [], work / "cond", outside,
                      owned_work_dir=work)
    assert outside.exists()


def test_run_probe_requires_the_owned_work_dir_argument(chk) -> None:
    """`owned_work_dir` は **キーワード必須**（渡し忘れを呼び出し時に落とす）。"""
    with pytest.raises(TypeError):
        chk.run_probe("python", [], Path("/tmp/out"), Path("/tmp/r.json"))


# --------------------------------------------------------------------------
# consumed_* 検査の**キー集合完全一致**（外部レビュー指摘）
#
# 「在るキーだけ照合して PASS」だと、空バンドル / キー欠落 / 未知キーが素通り
# する。期待集合との完全一致を要求していることを、5 通りで固定する。
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def probe(chk):
    return chk.probe_mod


def _model_pins(probe) -> dict:
    """`_CONSUMED_TO_PIN` の全 pin キーを持つ最小の pins。"""
    return {pin: {"sha256": f"sha-{pin}"} for pin in probe._CONSUMED_TO_PIN.values()}


def _model_consumed(probe) -> dict:
    return {ck: f"sha-{pin}" for ck, pin in probe._CONSUMED_TO_PIN.items()}


def _score_pins() -> dict:
    return {
        "score_module_sakura": {"sha256": "sha-score"},
        "score_module_sakura_dep_phoneme_jp": {"sha256": "sha-dep"},
        # 期待集合は score_module_* だけ。無関係な pin は混ざってはいけない
        "acoustic_onnx": {"sha256": "sha-acoustic"},
    }


def _score_consumed() -> dict:
    return {"score_module_sakura": "sha-score",
            "score_module_sakura_dep_phoneme_jp": "sha-dep"}


def test_consumed_model_check_passes_on_the_exact_key_set(probe) -> None:
    out = probe.verify_consumed_bytes(
        [{"label": "baseline", "consumed_model_sha256": _model_consumed(probe)}],
        _model_pins(probe))
    assert out["ok"], out["mismatches"]
    assert out["distinct_bundles"] == 1


@pytest.mark.parametrize("mutate,why", [
    (lambda c: {}, "空バンドル"),
    (lambda c: {k: v for k, v in list(c.items())[1:]}, "キー欠落"),
    (lambda c: {**c, "unknown_onnx": "sha-x"}, "未知キー"),
])
def test_consumed_model_check_rejects_wrong_key_sets(probe, mutate, why) -> None:
    consumed = mutate(_model_consumed(probe))
    out = probe.verify_consumed_bytes(
        [{"label": "baseline", "consumed_model_sha256": consumed}], _model_pins(probe))
    assert not out["ok"], f"{why} が素通りした: {out}"


def test_consumed_model_check_rejects_a_digest_mismatch(probe) -> None:
    consumed = _model_consumed(probe)
    consumed["vocoder_onnx"] = "sha-tampered"
    out = probe.verify_consumed_bytes(
        [{"label": "baseline", "consumed_model_sha256": consumed}], _model_pins(probe))
    assert not out["ok"]
    assert any("vocoder_onnx" in m for m in out["mismatches"])


def test_consumed_model_check_rejects_a_run_with_no_records(probe) -> None:
    """記録が 1 件も無い走行を「検査に通った」と読み替えないこと。"""
    out = probe.verify_consumed_bytes([{"label": "baseline", "error": "boom"}],
                                      _model_pins(probe))
    assert not out["ok"]


def test_consumed_score_check_passes_on_the_exact_key_set(probe) -> None:
    out = probe.verify_consumed_score_bytes(
        [{"label": "baseline", "consumed_score_sha256": _score_consumed()}],
        _score_pins())
    assert out["ok"], out["mismatches"]


@pytest.mark.parametrize("mutate,why", [
    (lambda c: {}, "空バンドル"),
    (lambda c: {"score_module_sakura": c["score_module_sakura"]}, "依存キー欠落"),
    (lambda c: {**c, "score_module_umi": "sha-y"}, "pins に無い未知キー"),
])
def test_consumed_score_check_rejects_wrong_key_sets(probe, mutate, why) -> None:
    out = probe.verify_consumed_score_bytes(
        [{"label": "baseline", "consumed_score_sha256": mutate(_score_consumed())}],
        _score_pins())
    assert not out["ok"], f"{why} が素通りした: {out}"


def test_consumed_score_check_rejects_a_digest_mismatch(probe) -> None:
    consumed = _score_consumed()
    consumed["score_module_sakura"] = "sha-tampered"
    out = probe.verify_consumed_score_bytes(
        [{"label": "baseline", "consumed_score_sha256": consumed}], _score_pins())
    assert not out["ok"]
    assert any("score_module_sakura" in m for m in out["mismatches"])


def test_consumed_score_check_rejects_missing_score_pins(probe) -> None:
    """pins に `score_module_*` が無いと期待集合を決められない = FAIL。"""
    out = probe.verify_consumed_score_bytes(
        [{"label": "baseline", "consumed_score_sha256": _score_consumed()}],
        {"acoustic_onnx": {"sha256": "sha-acoustic"}})
    assert not out["ok"]


def test_consumed_checks_reject_bundles_differing_between_conditions(probe) -> None:
    """条件ごとにバイト列が違う走行を PASS にしない。"""
    a = _model_consumed(probe)
    b = dict(a, vocoder_onnx="sha-other")
    out = probe.verify_consumed_bytes(
        [{"label": "a", "consumed_model_sha256": a},
         {"label": "b", "consumed_model_sha256": b}], _model_pins(probe))
    assert not out["ok"]
    assert out["distinct_bundles"] == 2


# --------------------------------------------------------------------------
# 所有マーカーの symlink 追従（レビュー指摘 P2）
#
# `Path.exists()` はリンクを追うので、マーカーが保護対象入力への symlink だと
# 「所有している」と誤判定し、書き込みがリンク先を切り詰める。probe / checker
# の**両方**が同じ形の穴を持っていたのでファミリーとして掃討した。
# --------------------------------------------------------------------------


def test_checker_rejects_a_symlinked_ownership_marker(chk, tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    victim = tmp_path / "victim.onnx"
    victim.write_bytes(b"MODEL")
    (work / chk.WORK_OWNER_MARKER).symlink_to(victim)
    (work / "filler.txt").write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit):
        chk.claim_work_dir(work)
    assert victim.read_bytes() == b"MODEL", "symlink 先が切り詰められた"


def test_probe_rejects_a_symlinked_ownership_marker(probe, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"KEEP")
    (out_dir / probe.OWNER_MARKER).symlink_to(victim)
    with pytest.raises(SystemExit):
        probe.reset_condition_dir(out_dir)
    assert victim.read_bytes() == b"KEEP", "symlink 先が壊れた"
    assert out_dir.exists(), "所有していないディレクトリを rmtree した"


def test_own_marker_predicate_accepts_only_its_own_writes(
    probe, tmp_path: Path,
) -> None:
    mine = tmp_path / "mine"
    probe.write_own_marker(mine, "owned\n")
    assert probe.is_own_marker(mine)

    # 判別子の無い通常ファイルは所有物ではない（**hard link 対策の要**:
    # 保護対象入力への別名は lstat では通常ファイルに見えるので、型では
    # 区別できず中身で判定するしかない）
    foreign = tmp_path / "foreign"
    foreign.write_text("x", encoding="utf-8")
    assert not probe.is_own_marker(foreign)

    link = tmp_path / "link"
    link.symlink_to(mine)
    assert not probe.is_own_marker(link), "symlink をマーカーと認めた"

    a_dir = tmp_path / "dir"
    a_dir.mkdir()
    assert not probe.is_own_marker(a_dir)
    assert not probe.is_own_marker(tmp_path / "missing")


def test_own_marker_predicate_rejects_a_hard_link_to_a_protected_input(
    probe, tmp_path: Path,
) -> None:
    """保護対象入力への **hard link** をマーカーと誤認しないこと。

    hard link は `lstat` でも通常ファイルに見え、`O_NOFOLLOW` でも弾けない。
    型では区別できないので判別子で落とす（レビュー指摘 P2）。
    """
    protected = tmp_path / "acoustic.onnx"
    protected.write_bytes(b"MODEL-BYTES")
    alias = tmp_path / "marker"
    alias.hardlink_to(protected)
    assert not probe.is_own_marker(alias)


def test_write_own_marker_never_truncates_an_existing_path(
    probe, tmp_path: Path,
) -> None:
    """マーカー書き込みは **新規作成のみ**（`O_EXCL`）。

    `O_TRUNC` で開くと、マーカー名が保護対象入力への hard link だったときに
    共有 inode を切り詰める。symlink / hard link / 通常ファイルのいずれでも
    既存を開かないことで、この経路を構造的に塞ぐ（レビュー指摘 P2）。
    """
    victim = tmp_path / "victim"
    victim.write_bytes(b"KEEP")

    link = tmp_path / "sym"
    link.symlink_to(victim)
    with pytest.raises(OSError):
        probe.write_own_marker(link, "owned\n")

    alias = tmp_path / "hard"
    alias.hardlink_to(victim)
    with pytest.raises(FileExistsError):
        probe.write_own_marker(alias, "owned\n")

    plain = tmp_path / "plain"
    plain.write_bytes(b"KEEP2")
    with pytest.raises(FileExistsError):
        probe.write_own_marker(plain, "owned\n")

    assert victim.read_bytes() == b"KEEP"
    assert plain.read_bytes() == b"KEEP2"


def test_checker_rejects_a_hard_linked_ownership_marker(
    chk, tmp_path: Path,
) -> None:
    """work ディレクトリのマーカーが保護対象への hard link でも壊さないこと。"""
    work = tmp_path / "work"
    work.mkdir()
    victim = tmp_path / "acoustic.onnx"
    victim.write_bytes(b"MODEL")
    (work / chk.WORK_OWNER_MARKER).hardlink_to(victim)
    with pytest.raises(SystemExit):
        chk.claim_work_dir(work)
    assert victim.read_bytes() == b"MODEL", "hard link 先が切り詰められた"


def test_claim_work_dir_keeps_an_existing_owned_marker_intact(
    chk, tmp_path: Path,
) -> None:
    """再実行時に既存の自前マーカーを書き換えない（O_EXCL の前提）。"""
    work = chk.claim_work_dir(tmp_path / "work")
    marker = work / chk.WORK_OWNER_MARKER
    before = marker.read_bytes()
    chk.claim_work_dir(work)
    assert marker.read_bytes() == before


def test_checker_executes_the_probe_bytes_it_hashed(chk) -> None:
    """checker は **hash したバッファそのもの**を probe として実行すること。

    `read_bytes()` して別途 `import` すると 2 回 read する窓が残り、その間に
    差し替えて元へ戻されると 3 つの hash が揃ったまま PASS が出る
    （レビュー指摘 P2・6 巡目）。compile/exec なら「hash したバイト列」と
    「実行したバイト列」が構造的に同一。
    """
    import hashlib  # noqa: PLC0415

    src = (PROBES / "vgl0_reproducibility_check.py").read_text(encoding="utf-8")
    assert "exec(compile(_PROBE_SOURCE" in src, (
        "checker が hash 済みバッファから probe を実行していない")
    assert "import vgl0_control_axis_probe" not in src, (
        "パス経由の import が残っている（2 回 read の窓が開く）")

    live = hashlib.sha256(
        (PROBES / "vgl0_control_axis_probe.py").read_bytes()).hexdigest()
    assert chk._PROBE_SHA_AT_IMPORT == live
    assert chk.probe_mod.__name__ == "vgl0_control_axis_probe"
    assert hasattr(chk.probe_mod, "ProbeConfig")
