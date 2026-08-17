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
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

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


def test_parser_default_speaker_is_ritsu() -> None:
    """gate_synth.py の p_run と同じ既定値（無指定時 ritsu）を維持する。"""
    parser = gsr4.build_arg_parser()
    args = parser.parse_args(REQUIRED_ARGV)
    assert args.speaker == "ritsu"


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
