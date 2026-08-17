"""test_gate_synth_run4.py — `s1_gate/gate_synth_run4.py`（S3 run 4 ゲート判定材料②:
spk3 単独合成ラッパー）のロジック層テスト。

**GPU 実測未実施**（`gate_synth_run4.py` 冒頭 docstring 参照）。本テストは
onnxruntime を要求しない範囲 — speaker 解決・CLI 引数検証・
`gate_synth.cmd_run` への委譲引数の形 — のみを検証する。`gate_synth.cmd_run`
自体（onnxruntime 必須）はモック/フェイクに差し替える（本環境に onnxruntime
が未導入のため実呼び出しは不可能。既存 `tests/test_gate_synth_*.py` 5 本が
同じ理由で本環境ではコレクションエラーになるのと同じ制約 — pre-existing）。

`import gate_synth_run4` がこのテストモジュールのトップレベルで
onnxruntime 無しに成功すること自体が、遅延 import 設計の中核的な検証項目
である。

review #265 R4 #11（PR #265 Codex 第4波レビュー採用分）: `--speaker-embed-file`
（embed ファイル直指定モード。判定材料④ `forge_triangle.py` の
`candidate_A.emb`〜D.emb を合成する経路）の引数排他・384 有限 float32
検証（`forge_triangle` read-only import 経由）・`gate_synth.find_speaker_embed`/
`load_speaker_embed_vector_with_sha` へのモンキーパッチ委譲とその復元
（正常時・例外時の両方）を検証する。

review #265 R6 #13/#14（PR #265 Codex 第6波レビュー採用分、#10/#11 の残穴）:
#13 は acoustic.onnx が `spk_embed`/`steps` 入力を持たない canon 単一話者
モデルを直指定モードへ渡すと `speaker_embed_vector` が黙って無視される穴の
fail-closed 化（`gate_synth.ort.InferenceSession` の read-only 再利用を
フェイク `ort` で検証）。#14 は `--speaker-embed-file` が `gate_synth.cmd_run`
の原子公開が置換する管理パス（`out_dir`/`step_<N>`/`.old`/`.build-<pid>`）の
配下に解決される場合の swap 保護漏れの fail-closed 化。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import types
from pathlib import Path
from typing import List

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "s1_gate"))

import gate_synth_run4 as gsr4  # noqa: E402


# --- speaker -> spk_id 解決 -------------------------------------------------


def test_speaker_to_spk_id_table_matches_run4_assembly_assignment() -> None:
    """`s1_dataprep/assemble_run4.py` が固定した spk_id 割当
    （ritsu=0/pjs=1/user=2）と一致すること。"""
    assert gsr4.SPEAKER_TO_SPK_ID == {"ritsu": 0, "pjs": 1, "user": 2}


@pytest.mark.parametrize(
    "speaker,expected_spk_id", [("ritsu", 0), ("pjs", 1), ("user", 2)]
)
def test_resolve_spk_id_known_speakers(speaker: str, expected_spk_id: int) -> None:
    assert gsr4.resolve_spk_id(speaker) == expected_spk_id


def test_resolve_spk_id_unknown_speaker_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unknown speaker"):
        gsr4.resolve_spk_id("unknown_speaker")


def test_speaker_choices_is_derived_from_table_and_ordered_ritsu_pjs_user() -> None:
    assert gsr4.SPEAKER_CHOICES == ("ritsu", "pjs", "user")


# --- CLI 引数検証 ------------------------------------------------------------


REQUIRED_ARGV = [
    "run",
    "--canon-model-dir", "/fake/canon",
    "--vocoder-dir", "/fake/vocoder",
    "--out-dir", "/fake/out",
]


def test_parser_accepts_speaker_user() -> None:
    parser = gsr4.build_arg_parser()
    args = parser.parse_args(REQUIRED_ARGV + ["--speaker", "user"])
    assert args.speaker == "user"


def test_parser_accepts_speaker_ritsu_and_pjs_unchanged() -> None:
    parser = gsr4.build_arg_parser()
    for speaker in ("ritsu", "pjs"):
        args = parser.parse_args(REQUIRED_ARGV + ["--speaker", speaker])
        assert args.speaker == speaker


def test_parser_speaker_and_speaker_embed_file_both_default_to_none() -> None:
    """review #265 R4 #11: raw parser レベルでは `--speaker` の既定値を
    `None` にする（`main()` が両方省略時のみ 'ritsu' を適用する。理由は
    `test_main_rejects_speaker_and_speaker_embed_file_together_even_when_*`
    docstring 参照 — argparse の mutually-exclusive-group はデフォルト値と
    明示指定値が一致すると検出をすり抜けるため、default="ritsu" のままだと
    `--speaker ritsu --speaker-embed-file x.emb` の併用を検出できない）。"""
    parser = gsr4.build_arg_parser()
    args = parser.parse_args(REQUIRED_ARGV)
    assert args.speaker is None
    assert args.speaker_embed_file is None
    assert args.speaker_embed_label is None


def test_parser_rejects_unknown_speaker() -> None:
    parser = gsr4.build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(REQUIRED_ARGV + ["--speaker", "someone_else"])


@pytest.mark.parametrize(
    "missing_flag", ["--canon-model-dir", "--vocoder-dir", "--out-dir"]
)
def test_parser_requires_mandatory_flags(missing_flag: str) -> None:
    argv = [tok for tok in REQUIRED_ARGV]
    # missing_flag とその値を除去する
    idx = argv.index(missing_flag)
    del argv[idx : idx + 2]
    parser = gsr4.build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(argv)


def test_parser_requires_cmd_subcommand() -> None:
    parser = gsr4.build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


# --- gate_synth.cmd_run への委譲引数の形（フェイク） -------------------------


class _FakeGateSynthModule:
    """`gate_synth.cmd_run` の代わりに委譲引数を捕捉するフェイク。"""

    def __init__(self) -> None:
        self.calls: List[argparse.Namespace] = []

    def cmd_run(self, args: argparse.Namespace) -> None:
        self.calls.append(args)


def test_main_delegates_to_gate_synth_cmd_run_with_full_arg_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_module = _FakeGateSynthModule()
    monkeypatch.setattr(gsr4, "_import_gate_synth", lambda: fake_module)

    argv = [
        "run",
        "--diffsinger-repo", "/fake/diffsinger",
        "--ckpt-dir", "/fake/ckpt",
        "--exp-name", "s1_run4_acoustic_v1",
        "--step", "5000",
        "--canon-model-dir", "/fake/canon",
        "--vocoder-dir", "/fake/vocoder",
        "--out-dir", "/fake/out",
        "--song", "sakura,umi",
        "--tokens", "own",
        "--speaker", "user",
    ]
    gsr4.main(argv)

    assert len(fake_module.calls) == 1
    delegated = fake_module.calls[0]
    assert delegated.speaker == "user"
    assert delegated.diffsinger_repo == "/fake/diffsinger"
    assert delegated.ckpt_dir == "/fake/ckpt"
    assert delegated.exp_name == "s1_run4_acoustic_v1"
    assert delegated.step == 5000
    assert delegated.canon_model_dir == "/fake/canon"
    assert delegated.vocoder_dir == "/fake/vocoder"
    assert delegated.out_dir == "/fake/out"
    assert delegated.song == "sakura,umi"
    assert delegated.tokens == "own"
    assert delegated.skip_export is False
    assert delegated.acoustic_dir is None
    assert delegated.notes_limit is None
    assert delegated.singer_dir is None


def test_main_delegated_namespace_carries_every_attribute_cmd_run_impl_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`gate_synth._cmd_run_impl` が読む属性集合（一次ソース: `gate_synth.py`
    の `_cmd_run_impl` 本文を grep して確認した集合）が漏れなく委譲される
    Namespace 上に存在すること。"""
    fake_module = _FakeGateSynthModule()
    monkeypatch.setattr(gsr4, "_import_gate_synth", lambda: fake_module)

    gsr4.main(REQUIRED_ARGV + ["--speaker", "user"])

    delegated = fake_module.calls[0]
    required_attrs = {
        "acoustic_dir", "canon_model_dir", "ckpt_dir", "diffsinger_repo",
        "exp_name", "notes_limit", "out_dir", "singer_dir", "skip_export",
        "song", "speaker", "step", "tokens", "vocoder_dir",
    }
    for attr in required_attrs:
        assert hasattr(delegated, attr), f"missing attribute for cmd_run delegation: {attr}"


def test_main_prints_resolved_spk_id_for_user(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_module = _FakeGateSynthModule()
    monkeypatch.setattr(gsr4, "_import_gate_synth", lambda: fake_module)

    gsr4.main(REQUIRED_ARGV + ["--speaker", "user"])

    captured = capsys.readouterr()
    assert "speaker=user" in captured.out
    assert "spk_id=2" in captured.out


def test_main_resolves_default_speaker_to_ritsu_when_neither_flag_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_module = _FakeGateSynthModule()
    monkeypatch.setattr(gsr4, "_import_gate_synth", lambda: fake_module)

    gsr4.main(REQUIRED_ARGV)

    assert fake_module.calls[0].speaker == "ritsu"


def test_main_rejects_speaker_and_speaker_embed_file_together_even_when_speaker_equals_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """review #265 R4 #11: `--speaker ritsu`（既定値と同一の明示指定）を
    `--speaker-embed-file` と併用しても確実に拒否されること
    （`add_mutually_exclusive_group` 依存だと `--speaker` の default が
    'ritsu' の場合にこの組み合わせだけ検出漏れが起きることを実測で確認済み
    — `main()` の post-parse 明示チェックで担保する）。"""
    embed_path = tmp_path / "candidate_A.emb"
    embed_path.write_bytes(b"\x00" * (384 * 4))
    fake_module = _FakeGateSynthModule()
    monkeypatch.setattr(gsr4, "_import_gate_synth", lambda: fake_module)

    argv = REQUIRED_ARGV + ["--speaker", "ritsu", "--speaker-embed-file", str(embed_path)]
    with pytest.raises(SystemExit, match="mutually exclusive"):
        gsr4.main(argv)
    assert fake_module.calls == []


def test_main_rejects_speaker_and_speaker_embed_file_together_non_default_speaker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    embed_path = tmp_path / "candidate_A.emb"
    embed_path.write_bytes(b"\x00" * (384 * 4))
    fake_module = _FakeGateSynthModule()
    monkeypatch.setattr(gsr4, "_import_gate_synth", lambda: fake_module)

    argv = REQUIRED_ARGV + ["--speaker", "user", "--speaker-embed-file", str(embed_path)]
    with pytest.raises(SystemExit, match="mutually exclusive"):
        gsr4.main(argv)
    assert fake_module.calls == []


# --- review #265 R4 #11 / R6 #13・#14: --speaker-embed-file（embed ファイル
# 直指定モード） ---------------------------------------------------------------


_REFLOW_MULTI_SPEAKER_ACOUSTIC_INPUT_NAMES = frozenset(
    {"tokens", "durations", "f0", "spk_embed", "depth", "steps"}
)
_CANON_SINGLE_SPEAKER_ACOUSTIC_INPUT_NAMES = frozenset(
    {"tokens", "durations", "f0", "depth", "speedup"}
)


class _FakeOnnxValueInfo:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeInferenceSession:
    def __init__(self, path, providers=None, *, input_names) -> None:
        self.path = path
        self.providers = providers
        self._input_names = input_names

    def get_inputs(self):
        return [_FakeOnnxValueInfo(name) for name in self._input_names]


def _make_fake_ort(input_names=_REFLOW_MULTI_SPEAKER_ACOUSTIC_INPUT_NAMES) -> types.SimpleNamespace:
    """review #265 R6 #13: `gate_synth.ort`（`gate_synth.py` が
    `import onnxruntime as ort` 済みのモジュール属性）の read-only 再利用を
    検証するためのフェイク。既定は reflow 多話者モデル相当
    （`spk_embed`/`steps` を含む）。"""

    def _inference_session(path, providers=None):
        return _FakeInferenceSession(path, providers=providers, input_names=input_names)

    return types.SimpleNamespace(InferenceSession=_inference_session)


def _make_embed_lookup_fake_gate_synth(
    acoustic_input_names=_REFLOW_MULTI_SPEAKER_ACOUSTIC_INPUT_NAMES,
) -> types.SimpleNamespace:
    """`_run_with_speaker_embed_file` の属性差し替え（モンキーパッチ）を
    検証するためのフェイク `gate_synth` モジュール代替。実モジュールと同様に
    `find_speaker_embed`/`load_speaker_embed_vector_with_sha`/`cmd_run` を
    プレーン関数として持つ（クラスのバウンドメソッド化を避け、実モジュール
    属性の差し替え挙動に近づける）。`cmd_run` は `_cmd_run_impl` の該当箇所
    と同型に `find_speaker_embed` → `load_speaker_embed_vector_with_sha` を
    呼び出し、`_run_with_speaker_embed_file` の差し替えが最終的な値まで
    届いているかを観測できるようにする。`ort`（review #265 R6 #13）は
    `acoustic_input_names` で構成したフェイク onnxruntime。
    """
    state = {
        "cmd_run_calls": [],
        "find_speaker_embed_calls": [],
        "load_speaker_embed_vector_with_sha_calls": [],
        "observed_vector": None,
        "observed_sha": None,
    }

    def find_speaker_embed(acoustic_dir, speaker, export_basename=None):
        # 差し替えられていなければ到達する「素の」実装（呼ばれたら差し替え
        # が効いていない証拠）。
        state["find_speaker_embed_calls"].append((acoustic_dir, speaker, export_basename))
        return None

    def load_speaker_embed_vector_with_sha(path):
        state["load_speaker_embed_vector_with_sha_calls"].append(path)
        raise AssertionError(
            "unpatched load_speaker_embed_vector_with_sha should not be reached in "
            "--speaker-embed-file mode"
        )

    def cmd_run(args):
        state["cmd_run_calls"].append(args)
        found_path = fake.find_speaker_embed(Path("/fake/acoustic-dir"), args.speaker)
        vector, sha = fake.load_speaker_embed_vector_with_sha(found_path)
        state["observed_vector"] = vector
        state["observed_sha"] = sha

    fake = types.SimpleNamespace(
        find_speaker_embed=find_speaker_embed,
        load_speaker_embed_vector_with_sha=load_speaker_embed_vector_with_sha,
        cmd_run=cmd_run,
        ort=_make_fake_ort(acoustic_input_names),
        state=state,
    )
    return fake


def _write_fake_384_embed(path: Path, seed: int) -> np.ndarray:
    vector = np.random.default_rng(seed).standard_normal(384).astype(np.float32)
    path.write_bytes(vector.tobytes())
    return vector


def _make_acoustic_dir_with_onnx(tmp_path: Path, name: str = "acoustic_dir") -> Path:
    """review #265 R6 #13 が要求する `--skip-export --acoustic-dir` の
    土台。フェイク `ort.InferenceSession` は中身を読まないため
    `acoustic.onnx` はプレースホルダで足りる（存在確認のみが実チェック）。
    """
    acoustic_dir = tmp_path / name
    acoustic_dir.mkdir(parents=True, exist_ok=True)
    (acoustic_dir / "acoustic.onnx").write_bytes(b"\x00")
    return acoustic_dir


def _required_argv_with_valid_acoustic(
    tmp_path: Path, *, out_dir: str = "/fake/out",
) -> List[str]:
    """embed ファイル直指定モードの「正常系」テストの土台 argv
    （review #265 R6 #13 の `--skip-export`/`--acoustic-dir` 要件を満たす）。
    `--speaker-embed-file` はテスト側で追加する。"""
    acoustic_dir = _make_acoustic_dir_with_onnx(tmp_path)
    return [
        "run",
        "--canon-model-dir", "/fake/canon",
        "--vocoder-dir", "/fake/vocoder",
        "--out-dir", out_dir,
        "--skip-export", "--acoustic-dir", str(acoustic_dir),
    ]


def test_parser_accepts_speaker_embed_file_alone() -> None:
    parser = gsr4.build_arg_parser()
    args = parser.parse_args(REQUIRED_ARGV + ["--speaker-embed-file", "candidate_A.emb"])
    assert args.speaker is None
    assert args.speaker_embed_file == "candidate_A.emb"


def test_run_with_speaker_embed_file_monkeypatches_and_delegates_loaded_vector(
    tmp_path: Path,
) -> None:
    embed_path = tmp_path / "candidate_A.emb"
    vector = _write_fake_384_embed(embed_path, seed=1)

    fake = _make_embed_lookup_fake_gate_synth()
    original_find = fake.find_speaker_embed
    original_load = fake.load_speaker_embed_vector_with_sha

    parser = gsr4.build_arg_parser()
    argv = _required_argv_with_valid_acoustic(tmp_path) + ["--speaker-embed-file", str(embed_path)]
    args = parser.parse_args(argv)

    gsr4._run_with_speaker_embed_file(args, fake)

    assert len(fake.state["cmd_run_calls"]) == 1
    delegated = fake.state["cmd_run_calls"][0]
    assert delegated.speaker == "candidate_A"  # ファイル名 stem 由来のラベル
    assert delegated.out_dir == "/fake/out"  # 他の引数もそのまま透過している

    # 差し替えられた値が run_pipeline 相当の消費点まで届いている
    assert np.array_equal(fake.state["observed_vector"], vector)
    assert fake.state["observed_sha"] == hashlib.sha256(vector.tobytes()).hexdigest()
    # 素の（未差し替え）実装は一度も呼ばれていない
    assert fake.state["find_speaker_embed_calls"] == []
    assert fake.state["load_speaker_embed_vector_with_sha_calls"] == []

    # 呼び出し後、フェイクモジュールの属性は元の関数へ復元されている
    assert fake.find_speaker_embed is original_find
    assert fake.load_speaker_embed_vector_with_sha is original_load


def test_run_with_speaker_embed_file_restores_originals_even_if_cmd_run_raises(
    tmp_path: Path,
) -> None:
    embed_path = tmp_path / "candidate_B.emb"
    _write_fake_384_embed(embed_path, seed=2)

    fake = _make_embed_lookup_fake_gate_synth()
    original_find = fake.find_speaker_embed
    original_load = fake.load_speaker_embed_vector_with_sha

    def _boom(_args):
        raise RuntimeError("simulated cmd_run failure")

    fake.cmd_run = _boom

    parser = gsr4.build_arg_parser()
    argv = _required_argv_with_valid_acoustic(tmp_path) + ["--speaker-embed-file", str(embed_path)]
    args = parser.parse_args(argv)

    with pytest.raises(RuntimeError, match="simulated cmd_run failure"):
        gsr4._run_with_speaker_embed_file(args, fake)

    assert fake.find_speaker_embed is original_find
    assert fake.load_speaker_embed_vector_with_sha is original_load


def test_run_with_speaker_embed_file_default_label_is_file_stem(tmp_path: Path) -> None:
    embed_path = tmp_path / "candidate_D.emb"
    _write_fake_384_embed(embed_path, seed=3)
    fake = _make_embed_lookup_fake_gate_synth()

    parser = gsr4.build_arg_parser()
    argv = _required_argv_with_valid_acoustic(tmp_path) + ["--speaker-embed-file", str(embed_path)]
    args = parser.parse_args(argv)
    gsr4._run_with_speaker_embed_file(args, fake)

    assert fake.state["cmd_run_calls"][0].speaker == "candidate_D"


def test_run_with_speaker_embed_file_explicit_label_overrides_stem(tmp_path: Path) -> None:
    embed_path = tmp_path / "candidate_E.emb"
    _write_fake_384_embed(embed_path, seed=4)
    fake = _make_embed_lookup_fake_gate_synth()

    parser = gsr4.build_arg_parser()
    argv = _required_argv_with_valid_acoustic(tmp_path) + [
        "--speaker-embed-file", str(embed_path), "--speaker-embed-label", "custom-label",
    ]
    args = parser.parse_args(argv)
    gsr4._run_with_speaker_embed_file(args, fake)

    assert fake.state["cmd_run_calls"][0].speaker == "custom-label"


def test_main_delegates_speaker_embed_file_mode_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    embed_path = tmp_path / "candidate_C.emb"
    vector = _write_fake_384_embed(embed_path, seed=5)

    fake = _make_embed_lookup_fake_gate_synth()
    monkeypatch.setattr(gsr4, "_import_gate_synth", lambda: fake)

    argv = _required_argv_with_valid_acoustic(tmp_path) + [
        "--speaker-embed-file", str(embed_path), "--speaker-embed-label", "my-label",
    ]
    gsr4.main(argv)

    delegated = fake.state["cmd_run_calls"][0]
    assert delegated.speaker == "my-label"
    assert np.array_equal(fake.state["observed_vector"], vector)


def test_main_speaker_embed_file_rejects_truncated_embed_before_delegating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """review #265 R4 #11(a): forge_triangle の 384 有限 float32 validator を
    read-only import して再利用していること — 壊れた embed は `cmd_run` へ
    到達する前に拒否される（embed 検証は #13/#14 より先に行われるため、
    --skip-export/--acoustic-dir 無しの base argv でも到達を確認できる）。"""
    bad_path = tmp_path / "truncated.emb"
    bad_vector = np.random.default_rng(6).standard_normal(383).astype(np.float32)
    bad_path.write_bytes(bad_vector.tobytes())

    fake = _make_embed_lookup_fake_gate_synth()
    monkeypatch.setattr(gsr4, "_import_gate_synth", lambda: fake)

    argv = REQUIRED_ARGV + ["--speaker-embed-file", str(bad_path)]
    with pytest.raises(ValueError, match="expected exactly 384"):
        gsr4.main(argv)
    assert fake.state["cmd_run_calls"] == []


def test_main_speaker_embed_file_rejects_nan_embed_before_delegating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad_path = tmp_path / "nan.emb"
    bad_vector = np.random.default_rng(7).standard_normal(384).astype(np.float32)
    bad_vector[10] = np.nan
    bad_path.write_bytes(bad_vector.tobytes())

    fake = _make_embed_lookup_fake_gate_synth()
    monkeypatch.setattr(gsr4, "_import_gate_synth", lambda: fake)

    argv = REQUIRED_ARGV + ["--speaker-embed-file", str(bad_path)]
    with pytest.raises(ValueError, match="non-finite"):
        gsr4.main(argv)
    assert fake.state["cmd_run_calls"] == []


# --- review #265 R6 #13: canon/単一話者モデルへの直指定を fail-closed 拒否 ----


def test_require_reflow_multi_speaker_acoustic_accepts_reflow_model(tmp_path: Path) -> None:
    acoustic_dir = _make_acoustic_dir_with_onnx(tmp_path)
    args = argparse.Namespace(
        skip_export=True, acoustic_dir=str(acoustic_dir),
    )
    fake = types.SimpleNamespace(ort=_make_fake_ort(_REFLOW_MULTI_SPEAKER_ACOUSTIC_INPUT_NAMES))
    gsr4._require_reflow_multi_speaker_acoustic(args, fake)  # 例外なし = 合格


def test_require_reflow_multi_speaker_acoustic_rejects_canon_model(tmp_path: Path) -> None:
    acoustic_dir = _make_acoustic_dir_with_onnx(tmp_path)
    args = argparse.Namespace(
        skip_export=True, acoustic_dir=str(acoustic_dir),
    )
    fake = types.SimpleNamespace(ort=_make_fake_ort(_CANON_SINGLE_SPEAKER_ACOUSTIC_INPUT_NAMES))
    with pytest.raises(SystemExit, match="canon モデルは直指定モード不可"):
        gsr4._require_reflow_multi_speaker_acoustic(args, fake)


def test_require_reflow_multi_speaker_acoustic_requires_skip_export(tmp_path: Path) -> None:
    args = argparse.Namespace(skip_export=False, acoustic_dir=None)
    fake = types.SimpleNamespace(ort=_make_fake_ort())
    with pytest.raises(SystemExit, match="--skip-export"):
        gsr4._require_reflow_multi_speaker_acoustic(args, fake)


def test_require_reflow_multi_speaker_acoustic_requires_acoustic_dir(tmp_path: Path) -> None:
    args = argparse.Namespace(skip_export=True, acoustic_dir=None)
    fake = types.SimpleNamespace(ort=_make_fake_ort())
    with pytest.raises(SystemExit, match="--acoustic-dir"):
        gsr4._require_reflow_multi_speaker_acoustic(args, fake)


def test_require_reflow_multi_speaker_acoustic_requires_existing_acoustic_onnx(
    tmp_path: Path,
) -> None:
    empty_dir = tmp_path / "empty_acoustic_dir"
    empty_dir.mkdir()
    args = argparse.Namespace(skip_export=True, acoustic_dir=str(empty_dir))
    fake = types.SimpleNamespace(ort=_make_fake_ort())
    with pytest.raises(SystemExit, match="not found"):
        gsr4._require_reflow_multi_speaker_acoustic(args, fake)


# --- review #265 R12 P1: 検証 acoustic.onnx と cmd_run 実読み込みの TOCTOU --


def test_require_reflow_multi_speaker_acoustic_returns_verified_sha256(tmp_path: Path) -> None:
    """P1 修正 (review #265 R12): 戻り値は検証に使ったバイト列そのものから
    計測した acoustic.onnx の sha256 である。"""
    acoustic_dir = _make_acoustic_dir_with_onnx(tmp_path)
    acoustic_onnx_path = acoustic_dir / "acoustic.onnx"
    args = argparse.Namespace(skip_export=True, acoustic_dir=str(acoustic_dir))
    fake = types.SimpleNamespace(ort=_make_fake_ort(_REFLOW_MULTI_SPEAKER_ACOUSTIC_INPUT_NAMES))

    digest = gsr4._require_reflow_multi_speaker_acoustic(args, fake)

    assert digest == hashlib.sha256(acoustic_onnx_path.read_bytes()).hexdigest()


def test_verify_acoustic_onnx_unchanged_after_cmd_run_accepts_matching_sha256(
    tmp_path: Path,
) -> None:
    acoustic_dir = _make_acoustic_dir_with_onnx(tmp_path)
    acoustic_onnx_path = acoustic_dir / "acoustic.onnx"
    args = argparse.Namespace(
        acoustic_dir=str(acoustic_dir), out_dir=str(tmp_path / "out"), step=None,
    )
    verified_sha256 = hashlib.sha256(acoustic_onnx_path.read_bytes()).hexdigest()

    gsr4._verify_acoustic_onnx_unchanged_after_cmd_run(args, verified_sha256)  # 例外なし = 合格


def test_verify_acoustic_onnx_unchanged_after_cmd_run_rejects_mismatch_and_quarantines_output(
    tmp_path: Path,
) -> None:
    """P1 修正 (review #265 R12) の核心シナリオ: 検証時の sha256 と事後再
    ハッシュが不一致なら、`cmd_run` が既に書いた出力（`synth_out_dir` =
    `--step` 未指定時は `out_dir` そのもの）を削除してから fail-closed する
    （blind バッチを公開したまま残さない）。"""
    acoustic_dir = _make_acoustic_dir_with_onnx(tmp_path)
    acoustic_onnx_path = acoustic_dir / "acoustic.onnx"
    verified_sha256 = hashlib.sha256(acoustic_onnx_path.read_bytes()).hexdigest()

    # モデルが cmd_run 実行中に差し替わったことを模擬する。
    acoustic_onnx_path.write_bytes(b"\xff" * 999)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "song.wav").write_bytes(b"published-but-should-be-quarantined")

    args = argparse.Namespace(acoustic_dir=str(acoustic_dir), out_dir=str(out_dir), step=None)

    with pytest.raises(SystemExit, match="acoustic.onnx"):
        gsr4._verify_acoustic_onnx_unchanged_after_cmd_run(args, verified_sha256)

    assert not out_dir.exists()  # 公開拒否: cmd_run が書いた出力が quarantine された


def test_verify_acoustic_onnx_unchanged_after_cmd_run_quarantines_step_dir(
    tmp_path: Path,
) -> None:
    """`--step` 指定時は `<out_dir>/step_<N>` のみを quarantine し、
    `out_dir` 自体は残す（`gate_synth.py` の `synth_out_dir` 算出と同一）。"""
    acoustic_dir = _make_acoustic_dir_with_onnx(tmp_path)
    acoustic_onnx_path = acoustic_dir / "acoustic.onnx"
    verified_sha256 = hashlib.sha256(acoustic_onnx_path.read_bytes()).hexdigest()
    acoustic_onnx_path.write_bytes(b"\xff" * 999)

    out_dir = tmp_path / "out"
    step_dir = out_dir / "step_5000"
    step_dir.mkdir(parents=True)
    (step_dir / "song.wav").write_bytes(b"published-but-should-be-quarantined")
    (out_dir / "unrelated.txt").write_bytes(b"keep-me")

    args = argparse.Namespace(acoustic_dir=str(acoustic_dir), out_dir=str(out_dir), step=5000)

    with pytest.raises(SystemExit, match="acoustic.onnx"):
        gsr4._verify_acoustic_onnx_unchanged_after_cmd_run(args, verified_sha256)

    assert not step_dir.exists()
    assert (out_dir / "unrelated.txt").exists()


# --- review #265 R13 P2: drift ロールバックは gate_synth.py の swap 意味論に
# 合わせて .old から旧世代を復元する（単純 rmtree では正本不在 + .old 取り残し
# になる穴の修正）。ここから。 -----------------------------------------------


def test_rollback_or_remove_published_step_dir_restores_from_old_when_present(
    tmp_path: Path,
) -> None:
    """`.old` に旧世代が存在する場合、新規公開分を退けて `.old` から復元する
    （`gate_synth.py` `_swap_step_dir_into_place`/`_rollback_swapped_step_dir`
    と同一の意味論）。"""
    synth_out_dir = tmp_path / "out"
    synth_out_dir.mkdir()
    (synth_out_dir / "song.wav").write_bytes(b"NEW-GENERATION-JUST-PUBLISHED")

    old_dir = tmp_path / "out.old"
    old_dir.mkdir()
    (old_dir / "song.wav").write_bytes(b"PREVIOUS-VALID-GENERATION")

    restored = gsr4._rollback_or_remove_published_step_dir(synth_out_dir)

    assert restored is True
    assert not old_dir.exists()  # .old が消費されて synth_out_dir へ戻った
    assert synth_out_dir.exists()
    assert (synth_out_dir / "song.wav").read_bytes() == b"PREVIOUS-VALID-GENERATION"


def test_rollback_or_remove_published_step_dir_removes_only_when_no_old(
    tmp_path: Path,
) -> None:
    """`.old` が存在しない（初回公開）場合は、従来どおり撤去のみで正本不在の
    状態に戻す。"""
    synth_out_dir = tmp_path / "out"
    synth_out_dir.mkdir()
    (synth_out_dir / "song.wav").write_bytes(b"NEW-GENERATION-JUST-PUBLISHED")

    restored = gsr4._rollback_or_remove_published_step_dir(synth_out_dir)

    assert restored is False
    assert not synth_out_dir.exists()
    assert not (tmp_path / "out.old").exists()


def test_verify_acoustic_onnx_unchanged_after_cmd_run_restores_old_generation_on_drift(
    tmp_path: Path,
) -> None:
    """受け入れ条件（review #265 R13 #3）: drift 検出時、`.old` に旧有効世代が
    残っている場合は撤去だけでなくそこから復元する（正本を空のまま残さず、
    `.old` も取り残さない）。"""
    acoustic_dir = _make_acoustic_dir_with_onnx(tmp_path)
    acoustic_onnx_path = acoustic_dir / "acoustic.onnx"
    verified_sha256 = hashlib.sha256(acoustic_onnx_path.read_bytes()).hexdigest()
    # モデルが cmd_run 実行中に差し替わったことを模擬する。
    acoustic_onnx_path.write_bytes(b"\xff" * 999)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "song.wav").write_bytes(b"blind-batch-just-published")

    old_dir = tmp_path / "out.old"
    old_dir.mkdir()
    (old_dir / "song.wav").write_bytes(b"previous-valid-generation")

    args = argparse.Namespace(acoustic_dir=str(acoustic_dir), out_dir=str(out_dir), step=None)

    with pytest.raises(SystemExit, match="acoustic.onnx"):
        gsr4._verify_acoustic_onnx_unchanged_after_cmd_run(args, verified_sha256)

    assert out_dir.exists()  # 正本不在にはしない — 旧世代を復元する
    assert (out_dir / "song.wav").read_bytes() == b"previous-valid-generation"
    assert not old_dir.exists()  # .old は復元に消費され、取り残されない


def test_verify_acoustic_onnx_unchanged_after_cmd_run_first_publish_leaves_canonical_absent(
    tmp_path: Path,
) -> None:
    """受け入れ条件（review #265 R13 #3）: `.old` が無い初回実行では従来どおり
    撤去のみを行い、エラーメッセージで正本不在を明示する。"""
    acoustic_dir = _make_acoustic_dir_with_onnx(tmp_path)
    acoustic_onnx_path = acoustic_dir / "acoustic.onnx"
    verified_sha256 = hashlib.sha256(acoustic_onnx_path.read_bytes()).hexdigest()
    acoustic_onnx_path.write_bytes(b"\xff" * 999)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "song.wav").write_bytes(b"blind-batch-just-published")

    args = argparse.Namespace(acoustic_dir=str(acoustic_dir), out_dir=str(out_dir), step=None)

    with pytest.raises(SystemExit, match="ABSENT") as excinfo:
        gsr4._verify_acoustic_onnx_unchanged_after_cmd_run(args, verified_sha256)

    assert not out_dir.exists()
    assert not (tmp_path / "out.old").exists()
    assert "no previous generation existed" in str(excinfo.value)


# --- review #265 R13 P2 ここまで ---------------------------------------------


def test_run_with_speaker_embed_file_rejects_publish_when_model_swapped_during_cmd_run(
    tmp_path: Path,
) -> None:
    """統合テスト（受け入れ条件 4）: `cmd_run` をモックし、その実行中に
    acoustic.onnx を差し替えると、`_run_with_speaker_embed_file` は
    公開拒否（`SystemExit`）となり、`cmd_run` が書いた出力を quarantine
    する。`cmd_run` 自体は 1 回だけ呼ばれる（委譲は起きた——TOCTOU の窓は
    委譲の"前"ではなく"後"にあるため #13 の事前検査だけでは検出できない
    ことの実証）。"""
    embed_path = tmp_path / "candidate_A.emb"
    _write_fake_384_embed(embed_path, seed=11)

    acoustic_dir = _make_acoustic_dir_with_onnx(tmp_path)
    acoustic_onnx_path = acoustic_dir / "acoustic.onnx"
    out_dir = tmp_path / "out"

    fake = _make_embed_lookup_fake_gate_synth()

    def _cmd_run_swaps_model_mid_flight(run_args):
        fake.state["cmd_run_calls"].append(run_args)
        # 実 gate_synth.cmd_run と同じく、ここで実際に公開結果を書く。
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "song.wav").write_bytes(b"blind-batch-should-be-quarantined")
        # TOCTOU を模擬: 検証済みの acoustic.onnx が実行中に別内容へ差し替わる。
        acoustic_onnx_path.write_bytes(b"\x99" * 1234)

    fake.cmd_run = _cmd_run_swaps_model_mid_flight

    parser = gsr4.build_arg_parser()
    argv = [
        "run",
        "--canon-model-dir", "/fake/canon",
        "--vocoder-dir", "/fake/vocoder",
        "--out-dir", str(out_dir),
        "--skip-export", "--acoustic-dir", str(acoustic_dir),
        "--speaker-embed-file", str(embed_path),
    ]
    args = parser.parse_args(argv)

    with pytest.raises(SystemExit, match="acoustic.onnx"):
        gsr4._run_with_speaker_embed_file(args, fake)

    assert len(fake.state["cmd_run_calls"]) == 1  # 委譲は実際に起きた
    assert not out_dir.exists()  # だが公開結果は quarantine され残らない


def test_main_speaker_embed_file_rejects_canon_model_before_cmd_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """review #265 R6 #13 の受け入れ条件: canon モデルでは `cmd_run` へ一切
    到達せず（= 候補 A〜D が同一声のまま成立してしまう実害が起こり得ない）。
    """
    embed_path = tmp_path / "candidate_A.emb"
    _write_fake_384_embed(embed_path, seed=8)

    fake = _make_embed_lookup_fake_gate_synth(
        acoustic_input_names=_CANON_SINGLE_SPEAKER_ACOUSTIC_INPUT_NAMES,
    )
    monkeypatch.setattr(gsr4, "_import_gate_synth", lambda: fake)

    argv = _required_argv_with_valid_acoustic(tmp_path) + ["--speaker-embed-file", str(embed_path)]
    with pytest.raises(SystemExit, match="canon モデルは直指定モード不可"):
        gsr4.main(argv)
    assert fake.state["cmd_run_calls"] == []


def test_main_speaker_embed_file_requires_skip_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    embed_path = tmp_path / "candidate_A.emb"
    _write_fake_384_embed(embed_path, seed=9)

    fake = _make_embed_lookup_fake_gate_synth()
    monkeypatch.setattr(gsr4, "_import_gate_synth", lambda: fake)

    argv = REQUIRED_ARGV + ["--speaker-embed-file", str(embed_path)]  # --skip-export 無し
    with pytest.raises(SystemExit, match="--skip-export"):
        gsr4.main(argv)
    assert fake.state["cmd_run_calls"] == []


# --- review #265 R6 #14: swap 保護漏れ（--speaker-embed-file が原子公開の
# 管理パス配下に解決される）の fail-closed 拒否 ------------------------------


def test_reject_speaker_embed_file_swap_collision_rejects_embed_inside_out_dir(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    leaked = out_dir / "leftover_candidate.emb"
    leaked.write_bytes(b"\x00" * (384 * 4))

    args = argparse.Namespace(out_dir=str(out_dir), step=None, speaker_embed_file=str(leaked))
    with pytest.raises(gsr4.forge_triangle.convert_d3.OutputCollisionError, match="inside"):
        gsr4._reject_speaker_embed_file_swap_collision(args)


def test_reject_speaker_embed_file_swap_collision_rejects_embed_inside_step_dir(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "out"
    step_dir = out_dir / "step_5000"
    step_dir.mkdir(parents=True)
    leaked = step_dir / "leftover_candidate.emb"
    leaked.write_bytes(b"\x00" * (384 * 4))

    args = argparse.Namespace(out_dir=str(out_dir), step=5000, speaker_embed_file=str(leaked))
    with pytest.raises(gsr4.forge_triangle.convert_d3.OutputCollisionError, match="inside"):
        gsr4._reject_speaker_embed_file_swap_collision(args)


def test_reject_speaker_embed_file_swap_collision_rejects_embed_inside_old_dir(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "out"
    old_dir = tmp_path / "out.old"
    old_dir.mkdir(parents=True)
    leaked = old_dir / "prior_generation_candidate.emb"
    leaked.write_bytes(b"\x00" * (384 * 4))

    args = argparse.Namespace(out_dir=str(out_dir), step=None, speaker_embed_file=str(leaked))
    with pytest.raises(gsr4.forge_triangle.convert_d3.OutputCollisionError, match="inside"):
        gsr4._reject_speaker_embed_file_swap_collision(args)


def test_reject_speaker_embed_file_swap_collision_rejects_embed_inside_build_dir(
    tmp_path: Path,
) -> None:
    """`build_dir` の命名は `<synth_out_dir>.build-<pid>`（本プロセスの
    `os.getpid()`）— `cmd_run` は同一プロセス内で呼ばれるため、テスト側で
    同じ pid を使って構築すれば実際の管理パスと一致する。"""
    out_dir = tmp_path / "out"
    build_dir = tmp_path / f"out.build-{os.getpid()}"
    build_dir.mkdir(parents=True)
    leaked = build_dir / "leftover_candidate.emb"
    leaked.write_bytes(b"\x00" * (384 * 4))

    args = argparse.Namespace(out_dir=str(out_dir), step=None, speaker_embed_file=str(leaked))
    with pytest.raises(gsr4.forge_triangle.convert_d3.OutputCollisionError, match="inside"):
        gsr4._reject_speaker_embed_file_swap_collision(args)


def test_reject_speaker_embed_file_swap_collision_allows_embed_outside_managed_paths(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    sibling = tmp_path / "forge_batch1" / "candidate_A.emb"
    sibling.parent.mkdir(parents=True)
    sibling.write_bytes(b"\x00" * (384 * 4))

    args = argparse.Namespace(out_dir=str(out_dir), step=None, speaker_embed_file=str(sibling))
    gsr4._reject_speaker_embed_file_swap_collision(args)  # 例外なし = 合格


def test_reject_speaker_embed_file_swap_collision_noop_when_nothing_exists_yet(
    tmp_path: Path,
) -> None:
    """`out_dir`（および `.old`/`.build-<pid>`）がまだ存在しない初回実行では
    何も検査対象が無く no-op（#9 と同じ「まだ無ければ許可」意味論）。"""
    out_dir = tmp_path / "never_created"
    embed_path = tmp_path / "candidate_A.emb"
    embed_path.write_bytes(b"\x00" * (384 * 4))

    args = argparse.Namespace(out_dir=str(out_dir), step=None, speaker_embed_file=str(embed_path))
    gsr4._reject_speaker_embed_file_swap_collision(args)  # 例外なし = 合格


def test_main_speaker_embed_file_rejects_when_embed_inside_out_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """review #265 R6 #14 の実害例そのもの: `--out-dir` 配下（前回バッチの
    残留物等）を `--speaker-embed-file` に指定すると `cmd_run` へ一切到達せず
    拒否される。"""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    leaked = out_dir / "leftover_candidate.emb"
    leaked_vector = _write_fake_384_embed(leaked, seed=10)

    fake = _make_embed_lookup_fake_gate_synth()
    monkeypatch.setattr(gsr4, "_import_gate_synth", lambda: fake)

    acoustic_dir = _make_acoustic_dir_with_onnx(tmp_path)
    argv = [
        "run",
        "--canon-model-dir", "/fake/canon",
        "--vocoder-dir", "/fake/vocoder",
        "--out-dir", str(out_dir),
        "--skip-export", "--acoustic-dir", str(acoustic_dir),
        "--speaker-embed-file", str(leaked),
    ]
    with pytest.raises(gsr4.forge_triangle.convert_d3.OutputCollisionError, match="inside"):
        gsr4.main(argv)
    assert fake.state["cmd_run_calls"] == []
    # 拒否前の状態のまま（入力ファイルは変更されない）
    assert leaked.read_bytes() == leaked_vector.tobytes()


def test_forge_triangle_sibling_import_succeeds_without_onnxruntime() -> None:
    """`gate_synth_run4.py` がモジュールトップレベルで `forge_triangle` を
    import できること（onnxruntime 非依存であることの裏付け。docstring
    「embed ファイル直指定モード」冒頭の sibling import 前提）。"""
    assert hasattr(gsr4, "forge_triangle")
    assert hasattr(gsr4.forge_triangle, "load_embed_vector_with_sha")


# --- 遅延 import そのもの ----------------------------------------------------


def test_import_gate_synth_module_lookup_uses_sibling_directory() -> None:
    """`_import_gate_synth` は同ディレクトリの `gate_synth.py` を指す
    （実呼び出しは onnxruntime 必須のため本環境では発火させない — 経路の
    存在のみ確認する）。"""
    gate_synth_path = Path(__file__).resolve().parent.parent / "s1_gate" / "gate_synth.py"
    assert gate_synth_path.exists()


def test_import_gate_synth_raises_modulenotfounderror_without_onnxruntime() -> None:
    """本環境（onnxruntime 未導入）で実際に `_import_gate_synth()` を呼ぶと
    `gate_synth.py` トップレベルの `import onnxruntime` で失敗することを
    確認する（GPU 実測不可の裏付け・正直明記の実測）。onnxruntime が
    導入済みの環境（run 4 クロー側）ではこのテストは同じ理由でスキップする
    必要がある — その判定を先に行う。"""
    try:
        import onnxruntime  # noqa: F401

        pytest.skip("onnxruntime is installed in this environment; nothing to assert here")
    except ModuleNotFoundError:
        pass

    with pytest.raises(ModuleNotFoundError):
        gsr4._import_gate_synth()
