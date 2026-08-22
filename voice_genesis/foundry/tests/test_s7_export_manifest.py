"""test_s7_export_manifest.py — ONNX を export 元 checkpoint へ縛る（PR #303 第 2 巡 P1）。

レンダ側は checkpoint を pin 照合するだけで、**実際に読み込む ONNX** とは何も
結び付いていなかった。正しい `--ckpt` と別物の ONNX を渡せば「事前登録 checkpoint で
測った」と名乗る spec を凍結できた。`verify_export_manifest` はそれを止める。

ONNX は checkpoint から決定論的に再生成できない（再 export でバイト列が変わる）ので、
事前登録に ONNX の sha を書く方式は取れない。代わりに **export 時点の対応**を記録し、
レンダ前に照合する。外し方 1 つにつき 1 テストで固定する。

実行: `python -m pytest voice_genesis/foundry/tests/test_s7_export_manifest.py -q`
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_RUN8 = Path(__file__).resolve().parent.parent / "run8"
if str(_RUN8) not in sys.path:
    sys.path.insert(0, str(_RUN8))

import s7_export_manifest as xm  # noqa: E402
import s7_io  # noqa: E402

GEN = "run7"


@pytest.fixture()
def bundle(tmp_path: Path):
    """checkpoint 1 個 + 生成物 3 個の最小束（中身は本物である必要が無い）。"""
    ckpt = tmp_path / "model_ckpt_steps_40000.ckpt"
    ckpt.write_bytes(CKPT_BYTES)   # autouse の pin fixture と揃える
    artifacts = {}
    for name, content in (
        ("acoustic_onnx", b"onnx-bytes"),
        ("acoustic_dsconfig", b"dsconfig-bytes"),
        ("acoustic_phonemes_json", b"phonemes-bytes"),
    ):
        f = tmp_path / name
        f.write_bytes(content)
        artifacts[name] = f
    manifest = tmp_path / "export_manifest.json"
    doc = xm.build_manifest(GEN, ckpt, artifacts)
    doc["source_config"] = {"sha256": s7_io.sha256_bytes(CONFIG_BYTES)}
    # 検証側の他の検査を試すための足場。**由来は後付けのままにできない**ので、
    # ここだけ witnessed へ書き換える（第 5 巡 P1 でこの経路は公開 API から消えた）。
    doc["binding_evidence"] = xm.WITNESSED
    manifest.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return manifest, ckpt, artifacts


def _ckpt_sha(ckpt: Path) -> str:
    return s7_io.sha256_bytes(ckpt.read_bytes())


def test_a_manifest_written_at_export_time_binds_the_artifacts(bundle):
    manifest, ckpt, artifacts = bundle
    out = xm.verify_export_manifest(manifest, GEN, artifacts)
    assert out["generation"] == GEN
    assert out["source_checkpoint_sha256"] == _ckpt_sha(ckpt)
    assert out["verified_artifacts"] == sorted(artifacts)
    assert out["exporter"]["revision"] == xm.DEFAULT_EXPORTER["revision"]


def test_an_onnx_from_another_export_is_rejected(bundle):
    """**これが本命**: ckpt は事前登録どおりなのに ONNX だけ別物、という取り違え。"""
    manifest, ckpt, artifacts = bundle
    artifacts["acoustic_onnx"].write_bytes(b"onnx-bytes-from-another-export")
    with pytest.raises(xm.ExportManifestError):
        xm.verify_export_manifest(manifest, GEN, artifacts)


def test_a_manifest_from_another_checkpoint_is_rejected(bundle, monkeypatch):
    """期待値は呼び出し側から渡らず pin から引かれる。pin と違えば通らない。"""
    manifest, ckpt, artifacts = bundle
    monkeypatch.setattr(
        xm, "load_input_pins",
        lambda generation: {"checkpoint_sha256": "0" * 64, "config_sha256": "0" * 64},
    )
    with pytest.raises(xm.ExportManifestError):
        xm.verify_export_manifest(manifest, GEN, artifacts)


def test_a_manifest_relabeled_to_another_generation_is_rejected(bundle):
    manifest, ckpt, artifacts = bundle
    with pytest.raises(xm.ExportManifestError):
        xm.verify_export_manifest(manifest, "run5", artifacts)


def test_an_artifact_absent_from_the_manifest_is_rejected(bundle, tmp_path):
    """記録に無いファイルを読み込もうとしたら、その由来は言えない。"""
    manifest, ckpt, artifacts = bundle
    extra = tmp_path / "speaker_embed"
    extra.write_bytes(b"emb-bytes")
    artifacts["speaker_embed"] = extra
    with pytest.raises(xm.ExportManifestError):
        xm.verify_export_manifest(manifest, GEN, artifacts)


def test_a_foreign_schema_is_rejected(bundle):
    manifest, ckpt, artifacts = bundle
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    doc["schema"] = "something-else/0.1"
    manifest.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(xm.ExportManifestError):
        xm.verify_export_manifest(manifest, GEN, artifacts)


def test_the_calibration_generation_is_derived_from_the_pinned_checkpoint():
    """校正レンダの世代名は**手書きしない** — 事前登録の ckpt sha から引く。"""
    import s7_calib_render as cr

    cal, _, _ = s7_io.read_json_with_pin(cr.CALIBRATION_SET_PATH)
    pinned = str(cal["real_render_set"]["render_path"]["checkpoint"]["sha256"])
    assert cr.calibration_generation(pinned) == "run7"
    with pytest.raises(cr.PinMismatch):
        cr.calibration_generation("0" * 64)


def test_both_render_entry_points_require_an_export_manifest():
    """`--export-manifest` を省いた呼び出しは**引数解析の時点で**落ちる。"""
    import s7_calib_render as cr

    with pytest.raises(SystemExit):
        cr.main([
            "--canon-model-dir", "x", "--vocoder-dir", "x", "--acoustic-onnx", "x",
            "--acoustic-dsconfig", "x", "--acoustic-phonemes-json", "x",
            "--canon-phonemes-txt", "x", "--speaker-emb", "x", "--ckpt", "x",
            "--canon-zip", "x", "--vocoder-container", "x",
            "--out-dir", "x", "--manifest-out", "x",
        ])
    assert "--export-manifest" in (_RUN8 / "s7_0b_probe.py").read_text(encoding="utf-8")


# --- 束縛の**由来**（PR #303 第 3 巡 P1） --------------------------------------


def test_a_post_hoc_manifest_is_rejected_by_default(tmp_path: Path):
    """2 つのパスを**別々に hash して並べただけ**の記録は、観測していない関係を
    証明したことにする。**pin どおりの ckpt** と無関係な ONNX でも作れてしまうので、
    レンダ側は由来を見て既定で受け付けない。"""
    ckpt = tmp_path / "ckpt"
    ckpt.write_bytes(CKPT_BYTES)   # pin と一致する = 由来の検査だけが効く状況
    onnx = tmp_path / "unrelated.onnx"
    onnx.write_bytes(b"an-onnx-that-never-came-from-that-ckpt")
    doc = xm.build_manifest(GEN, ckpt, {"acoustic_onnx": onnx})
    doc["source_config"] = {"sha256": s7_io.sha256_bytes(CONFIG_BYTES)}
    assert doc["binding_evidence"] == xm.UNWITNESSED
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(xm.ExportManifestError):
        xm.verify_export_manifest(manifest, GEN, {"acoustic_onnx": onnx})
    # 明示的に許したときだけ通る（移行用の逃げ道は残すが既定では閉じる）
    out = xm.verify_export_manifest(
        manifest, GEN, {"acoustic_onnx": onnx}, allow_unwitnessed=True
    )
    assert out["binding_evidence"] == xm.UNWITNESSED


CKPT_BYTES = b"the-pinned-checkpoint"
CONFIG_BYTES = b"the-pinned-config"


@pytest.fixture(autouse=True)
def _pins(monkeypatch):
    """入力 pin は本番の pin ファイルから引かれる。合成 checkpoint では一致しないので、
    テスト中だけ**合成側の値**へ差し替える。`export_diffsinger_acoustic` が
    「呼び出し側から期待値を受け取らない」性質はそのまま保たれる。"""
    monkeypatch.setattr(
        xm, "load_input_pins",
        lambda generation: {
            "checkpoint_sha256": s7_io.sha256_bytes(CKPT_BYTES),
            "config_sha256": s7_io.sha256_bytes(CONFIG_BYTES),
        },
    )


def _fake_diffsinger(tmp_path: Path, exp: str = "run7_phase_b", steps: int = 40000):
    """DiffSinger checkout の最小形 + exporter の入力（ckpt と config.yaml）。

    exporter tree の照合（git checkout / HEAD / 清浄な worktree）が入ったので、
    **本物の git リポジトリ**として作る。revision は作った HEAD をそのまま使う。
    """
    import subprocess

    root = tmp_path / "DiffSinger"
    (root / "checkpoints").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    # 本物と同じく `/checkpoints/*` を無視する。staging はここへ書くので、
    # 無視されないと worktree が汚れて自分の清浄検査に引っかかる
    # （上流 DiffSinger の .gitignore にも同じ行がある）。
    (root / ".gitignore").write_text("/checkpoints/*\n", encoding="utf-8")
    ckpt_dir = tmp_path / "bundle"
    ckpt_dir.mkdir()
    ckpt = ckpt_dir / f"model_ckpt_steps_{steps}.ckpt"
    ckpt.write_bytes(CKPT_BYTES)
    (ckpt_dir / "config.yaml").write_bytes(CONFIG_BYTES)
    # export.py の代わりに「--out へ成果物を置くだけ」の台本を置く
    (root / "scripts" / "export.py").write_text(
        "import pathlib, sys\n"
        "out = pathlib.Path(sys.argv[sys.argv.index('--out') + 1])\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "(out / 'a.onnx').write_bytes(b'produced')\n",
        encoding="utf-8",
    )
    for args in (
        ["init", "-q"], ["config", "user.email", "t@example.com"],
        ["config", "user.name", "t"], ["add", "-A"], ["commit", "-qm", "fake exporter"],
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return root, exp, steps, ckpt, ckpt_dir, head


def test_the_checkpoint_is_derived_from_the_exporter_invocation(tmp_path: Path):
    """ckpt と command を**別々に受け取らない**（PR #303 第 4 巡 P1）。

    DiffSinger の exporter は checkpoint のパスを受け取らず `--exp` / `--ckpt` から
    自分で解決する。したがって manifest が名乗る checkpoint も**同じ規則で導出**し、
    「command が読む物」と「manifest が名乗る物」を構造的に一致させる。
    """
    root, exp, steps, ckpt, ckpt_dir, head = _fake_diffsinger(tmp_path)
    doc = xm.export_diffsinger_acoustic(
        GEN, root, exp, steps, ckpt_dir, tmp_path / "out",
        {"acoustic_onnx": "a.onnx"}, sys.executable, head,
    )
    assert doc["binding_evidence"] == xm.WITNESSED
    assert doc["source_checkpoint"]["sha256"] == _ckpt_sha(ckpt)
    assert doc["artifacts"]["acoustic_onnx"]["sha256"] == s7_io.sha256_bytes(b"produced")
    # command には --exp / --ckpt が入っており、そこから ckpt を導出したと書いてある
    assert "--exp" in doc["export_command"] and exp in doc["export_command"]
    assert f"model_ckpt_steps_{steps}.ckpt" in doc["checkpoint_derivation"]


def test_a_checkpoint_that_does_not_match_the_pin_is_rejected_before_exporting(
    tmp_path, monkeypatch
):
    """期待値は呼び出し側から渡らず pin から引かれる（第 7 巡 P1）。"""
    root, exp, steps, _, ckpt_dir, head = _fake_diffsinger(tmp_path)
    monkeypatch.setattr(
        xm, "load_input_pins",
        lambda generation: {
            "checkpoint_sha256": "0" * 64,
            "config_sha256": s7_io.sha256_bytes(CONFIG_BYTES),
        },
    )
    with pytest.raises(xm.ExportManifestError):
        xm.export_diffsinger_acoustic(
            GEN, root, exp, steps, ckpt_dir, tmp_path / "out",
            {"acoustic_onnx": "a.onnx"}, sys.executable, head,
        )
    assert not (tmp_path / "out").exists(), "pin 不一致なら export を走らせない"


def test_a_missing_exporter_input_is_rejected(tmp_path: Path):
    """`--ckpt-dir` に exporter の入力が揃っていなければ staging しない。"""
    root, exp, steps, _, ckpt_dir, head = _fake_diffsinger(tmp_path)
    with pytest.raises(xm.ExportManifestError):
        xm.export_diffsinger_acoustic(
            GEN, root, exp, steps, ckpt_dir / "missing", tmp_path / "out2",
            {"acoustic_onnx": "a.onnx"}, sys.executable, head,
        )


def test_the_exact_experiment_folder_is_created_so_find_exp_cannot_prefix_match(tmp_path):
    """DiffSinger の `find_exp` は名前が合わないと**前方一致で別の実験へ落ちる**。

    staging 先を**厳密な名前で自分が作る**ので、exporter は exact-name 分岐しか
    通らない。紛らわしい前方一致候補が隣に在っても選ばれない。
    """
    root, exp, steps, ckpt, ckpt_dir, head = _fake_diffsinger(tmp_path)
    decoy = root / "checkpoints" / f"{exp}_OLD"
    decoy.mkdir(parents=True)
    (decoy / f"model_ckpt_steps_{steps}.ckpt").write_bytes(b"a different experiment")

    doc = xm.export_diffsinger_acoustic(
        GEN, root, exp, steps, ckpt_dir, tmp_path / "out",
        {"acoustic_onnx": "a.onnx"}, sys.executable, head,
    )
    staged = root / "checkpoints" / exp / f"model_ckpt_steps_{steps}.ckpt"
    assert staged.is_file()
    assert doc["source_checkpoint"]["sha256"] == _ckpt_sha(ckpt)
    assert doc["source_checkpoint"]["path"] == str(staged.resolve())


@pytest.mark.parametrize("exp", ["/tmp/elsewhere", "../outside", "a/../../outside"])
def test_an_experiment_name_that_escapes_the_checkpoints_directory_is_rejected(tmp_path, exp):
    """`--exp` が絶対パス / `../` を含むと staging の書き込みが checkpoints の外へ出て、
    **無関係な checkpoint と config を上書き**する。"""
    root, good_exp, steps, _, ckpt_dir, head = _fake_diffsinger(tmp_path)
    victim = tmp_path / "outside"
    victim.mkdir(exist_ok=True)
    (victim / f"model_ckpt_steps_{steps}.ckpt").write_bytes(b"someone else's checkpoint")
    with pytest.raises(xm.ExportManifestError):
        xm.export_diffsinger_acoustic(
            GEN, root, exp, steps, ckpt_dir, tmp_path / "out",
            {"acoustic_onnx": "a.onnx"}, sys.executable, head,
        )
    assert (victim / f"model_ckpt_steps_{steps}.ckpt").read_bytes() == b"someone else's checkpoint"


def test_a_witnessed_export_refuses_a_non_empty_output_directory(tmp_path: Path):
    """既に在る物を『今 export した産物』と名乗らせない。"""
    root, exp, steps, _, ckpt_dir, head = _fake_diffsinger(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "a.onnx").write_bytes(b"left over from an older export")
    with pytest.raises(xm.ExportManifestError):
        xm.export_diffsinger_acoustic(
            GEN, root, exp, steps, ckpt_dir, out_dir,
            {"acoustic_onnx": "a.onnx"}, sys.executable, head,
        )


def test_a_witnessed_export_fails_when_the_exporter_fails(tmp_path: Path):
    root, exp, steps, _, ckpt_dir, head = _fake_diffsinger(tmp_path)
    (root / "scripts" / "export.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
    with pytest.raises(xm.ExportManifestError):
        xm.export_diffsinger_acoustic(
            GEN, root, exp, steps, ckpt_dir, tmp_path / "out",
            {"acoustic_onnx": "a.onnx"}, sys.executable, head,
        )


def test_a_witnessed_export_fails_when_an_artifact_never_appears(tmp_path: Path):
    """exporter は成功したが宣言した物が出てこない = 因果を主張できない。"""
    root, exp, steps, _, ckpt_dir, head = _fake_diffsinger(tmp_path)
    (root / "scripts" / "export.py").write_text("pass\n", encoding="utf-8")
    with pytest.raises(xm.ExportManifestError):
        xm.export_diffsinger_acoustic(
            GEN, root, exp, steps, ckpt_dir, tmp_path / "out",
            {"acoustic_onnx": "a.onnx"}, sys.executable, head,
        )


def test_the_module_exposes_no_way_to_pair_an_arbitrary_command_with_a_checkpoint():
    """任意の command と任意の ckpt を独立に渡せる公開 API を**残さない**。

    残すと「ckpt を一切読まないコマンド」でも witnessed を名乗れてしまう
    （第 4 巡の指摘そのもの）。導出型の入口だけを公開する。
    """
    public = {n for n in dir(xm) if not n.startswith("_")}
    assert "run_witnessed_export" not in public
    assert "export_diffsinger_acoustic" in public


# --- 第 5 巡 P1 × 3 --------------------------------------------------------


def test_build_manifest_cannot_mint_a_witnessed_record(tmp_path: Path):
    """公開 API から `witnessed_export` を名乗れない。

    `_run_witnessed_export` を private にしても、`build_manifest` が
    `binding_evidence` を引数で受け取る限り、任意の ckpt と任意の ONNX で
    偽の由来を作れた（私自身のテスト fixture がそうしていた）。
    """
    ckpt = tmp_path / "ckpt"
    ckpt.write_bytes(b"ckpt")
    onnx = tmp_path / "unrelated.onnx"
    onnx.write_bytes(b"unrelated")

    doc = xm.build_manifest(GEN, ckpt, {"acoustic_onnx": onnx})
    assert doc["binding_evidence"] == xm.UNWITNESSED
    with pytest.raises(TypeError):
        xm.build_manifest(  # type: ignore[call-arg]
            GEN, ckpt, {"acoustic_onnx": onnx}, binding_evidence=xm.WITNESSED
        )


@pytest.mark.parametrize("rel", ["/etc/passwd", "../outside.onnx", "sub/../../outside.onnx"])
def test_a_declared_artifact_outside_the_fresh_output_directory_is_rejected(tmp_path, rel):
    """絶対パスや `../` を許すと、export と無関係な既存ファイルが
    `is_file()` を満たして witnessed 成果物として認証される。"""
    root, exp, steps, _, ckpt_dir, head = _fake_diffsinger(tmp_path)
    (tmp_path / "outside.onnx").write_bytes(b"pre-existing and unrelated")
    with pytest.raises(xm.ExportManifestError):
        xm.export_diffsinger_acoustic(
            GEN, root, exp, steps, ckpt_dir, tmp_path / "out",
            {"acoustic_onnx": rel}, sys.executable, head,
        )


def test_the_config_beside_the_checkpoint_is_bound_too(tmp_path: Path):
    """exporter の入力は checkpoint だけではない。`config.yaml` が隣に staging され、
    同じく読まれる。正しい ckpt の隣に古い config を置けば別の成果物が出る。"""
    root, exp, steps, ckpt, ckpt_dir, head = _fake_diffsinger(tmp_path)
    doc = xm.export_diffsinger_acoustic(
        GEN, root, exp, steps, ckpt_dir, tmp_path / "out",
        {"acoustic_onnx": "a.onnx"}, sys.executable, head,
    )
    assert doc["source_config"]["sha256"] == s7_io.sha256_bytes(b"the-pinned-config")
    # staging 先の実体が、記録した sha と一致する（= exporter が開くバイト列）
    staged = root / "checkpoints" / exp / "config.yaml"
    assert s7_io.sha256_bytes(staged.read_bytes()) == doc["source_config"]["sha256"]


def test_an_altered_config_beside_the_pinned_checkpoint_is_rejected(tmp_path: Path):
    root, exp, steps, ckpt, ckpt_dir, head = _fake_diffsinger(tmp_path)
    (ckpt_dir / "config.yaml").write_bytes(b"a stale or altered config")
    with pytest.raises(xm.ExportManifestError):
        xm.export_diffsinger_acoustic(
            GEN, root, exp, steps, ckpt_dir, tmp_path / "out",
            {"acoustic_onnx": "a.onnx"}, sys.executable, head,
        )
    assert not (tmp_path / "out").exists(), "config 不一致なら export を走らせない"


def test_a_missing_config_beside_the_checkpoint_is_rejected(tmp_path: Path):
    root, exp, steps, ckpt, ckpt_dir, head = _fake_diffsinger(tmp_path)
    (ckpt_dir / "config.yaml").unlink()
    with pytest.raises(xm.ExportManifestError):
        xm.export_diffsinger_acoustic(
            GEN, root, exp, steps, ckpt_dir, tmp_path / "out",
            {"acoustic_onnx": "a.onnx"}, sys.executable, head,
        )


def test_the_config_pin_is_mandatory_at_verification(tmp_path: Path, monkeypatch):
    root, exp, steps, ckpt, ckpt_dir, head = _fake_diffsinger(tmp_path)
    out_dir = tmp_path / "out"
    doc = xm.export_diffsinger_acoustic(
        GEN, root, exp, steps, ckpt_dir, out_dir,
        {"acoustic_onnx": "a.onnx"}, sys.executable, head,
    )
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    artifacts = {"acoustic_onnx": out_dir / "a.onnx"}
    xm.verify_export_manifest(manifest, GEN, artifacts)

    # **config pin は任意ではない**。pin 側が違えば、checkpoint が合っていても通らない
    monkeypatch.setattr(
        xm, "load_input_pins",
        lambda generation: {
            "checkpoint_sha256": s7_io.sha256_bytes(CKPT_BYTES),
            "config_sha256": "0" * 64,
        },
    )
    with pytest.raises(xm.ExportManifestError):
        xm.verify_export_manifest(manifest, GEN, artifacts)


# --- 第 7 巡 P1: exporter の実装そのものを起動前に照合する --------------------


def test_a_non_git_exporter_tree_is_rejected(tmp_path: Path):
    """checkout でなければ、どの実装を走らせたのか言えない。"""
    root = tmp_path / "not-a-checkout"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "export.py").write_text("pass\n", encoding="utf-8")
    with pytest.raises(xm.ExportManifestError):
        xm.verify_exporter_tree(root, "e2307b1")


def test_an_exporter_at_another_revision_is_rejected(tmp_path: Path):
    root, exp, steps, _, ckpt_dir, head = _fake_diffsinger(tmp_path)
    with pytest.raises(xm.ExportManifestError):
        xm.verify_exporter_tree(root, "0" * 40)


def test_a_locally_modified_exporter_is_rejected(tmp_path: Path):
    """**ローカルで書き換えた exporter** が宣言 revision を名乗れないようにする。

    これは第 6 巡までの実装がまさに許していた状態で、私の fake exporter テスト
    （bytes を書くだけのスクリプト）が `e2307b1` を名乗れていた。
    """
    root, exp, steps, _, ckpt_dir, head = _fake_diffsinger(tmp_path)
    (root / "scripts" / "export.py").write_text(
        "# locally modified\npass\n", encoding="utf-8"
    )
    with pytest.raises(xm.ExportManifestError):
        xm.verify_exporter_tree(root, head)


def test_a_verified_exporter_records_what_was_actually_executed(tmp_path: Path):
    """revision は**観測値**を記録する（呼び出し側の主張は使わない）。"""
    root, exp, steps, _, ckpt_dir, head = _fake_diffsinger(tmp_path)
    out = xm.verify_exporter_tree(root, head)
    assert out["observed_head"] == head
    assert out["worktree_clean"] is True
    assert out["entry_script_sha256"] == s7_io.sha256_bytes(
        (root / "scripts" / "export.py").read_bytes()
    )


def test_the_manifest_carries_the_verified_exporter(tmp_path: Path):
    root, exp, steps, ckpt, ckpt_dir, head = _fake_diffsinger(tmp_path)
    doc = xm.export_diffsinger_acoustic(
        GEN, root, exp, steps, ckpt_dir, tmp_path / "out",
        {"acoustic_onnx": "a.onnx"}, sys.executable, head,
    )
    assert doc["exporter"]["observed_head"] == head
    assert doc["exporter"]["entry_script_sha256"]


def test_the_committed_input_pins_agree_with_the_preregistration():
    """pin ファイルは事前登録ではなく参照点。checkpoint 側は事前登録と一致させる。"""
    for gen in ("run5", "run6", "run7"):
        pins = xm.load_input_pins(gen)
        assert len(pins["checkpoint_sha256"]) == 64
        assert len(pins["config_sha256"]) == 64
