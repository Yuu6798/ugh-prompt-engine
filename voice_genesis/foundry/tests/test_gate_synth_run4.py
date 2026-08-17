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
"""
from __future__ import annotations

import argparse
import hashlib
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


# --- review #265 R4 #11: --speaker-embed-file（embed ファイル直指定モード） ---


def _make_embed_lookup_fake_gate_synth() -> types.SimpleNamespace:
    """`_run_with_speaker_embed_file` の属性差し替え（モンキーパッチ）を
    検証するためのフェイク `gate_synth` モジュール代替。実モジュールと同様に
    `find_speaker_embed`/`load_speaker_embed_vector_with_sha`/`cmd_run` を
    プレーン関数として持つ（クラスのバウンドメソッド化を避け、実モジュール
    属性の差し替え挙動に近づける）。`cmd_run` は `_cmd_run_impl` の該当箇所
    と同型に `find_speaker_embed` → `load_speaker_embed_vector_with_sha` を
    呼び出し、`_run_with_speaker_embed_file` の差し替えが最終的な値まで
    届いているかを観測できるようにする。
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
        state=state,
    )
    return fake


def _write_fake_384_embed(path: Path, seed: int) -> np.ndarray:
    vector = np.random.default_rng(seed).standard_normal(384).astype(np.float32)
    path.write_bytes(vector.tobytes())
    return vector


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
    args = parser.parse_args(REQUIRED_ARGV + ["--speaker-embed-file", str(embed_path)])

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
    args = parser.parse_args(REQUIRED_ARGV + ["--speaker-embed-file", str(embed_path)])

    with pytest.raises(RuntimeError, match="simulated cmd_run failure"):
        gsr4._run_with_speaker_embed_file(args, fake)

    assert fake.find_speaker_embed is original_find
    assert fake.load_speaker_embed_vector_with_sha is original_load


def test_run_with_speaker_embed_file_default_label_is_file_stem(tmp_path: Path) -> None:
    embed_path = tmp_path / "candidate_D.emb"
    _write_fake_384_embed(embed_path, seed=3)
    fake = _make_embed_lookup_fake_gate_synth()

    parser = gsr4.build_arg_parser()
    args = parser.parse_args(REQUIRED_ARGV + ["--speaker-embed-file", str(embed_path)])
    gsr4._run_with_speaker_embed_file(args, fake)

    assert fake.state["cmd_run_calls"][0].speaker == "candidate_D"


def test_run_with_speaker_embed_file_explicit_label_overrides_stem(tmp_path: Path) -> None:
    embed_path = tmp_path / "candidate_E.emb"
    _write_fake_384_embed(embed_path, seed=4)
    fake = _make_embed_lookup_fake_gate_synth()

    parser = gsr4.build_arg_parser()
    args = parser.parse_args(
        REQUIRED_ARGV + ["--speaker-embed-file", str(embed_path), "--speaker-embed-label", "custom-label"]
    )
    gsr4._run_with_speaker_embed_file(args, fake)

    assert fake.state["cmd_run_calls"][0].speaker == "custom-label"


def test_main_delegates_speaker_embed_file_mode_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    embed_path = tmp_path / "candidate_C.emb"
    vector = _write_fake_384_embed(embed_path, seed=5)

    fake = _make_embed_lookup_fake_gate_synth()
    monkeypatch.setattr(gsr4, "_import_gate_synth", lambda: fake)

    argv = REQUIRED_ARGV + [
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
    到達する前に拒否される。"""
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
