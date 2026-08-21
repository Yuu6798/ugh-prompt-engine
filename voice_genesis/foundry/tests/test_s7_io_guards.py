"""test_s7_io_guards.py — run 8 の入出力ガードの**棚卸し**（同型穴の終端宣言を固定する）。

PR #300 のレビューで、同じ穴が 2 系統・3 モジュールに散らばっていると分かった:

1. 出力先が入力と衝突したまま書き込む（入力を破壊する）
2. parse と sha256 を**別々に読む**（間に差し替わると、古いバイトで計算した結果を
   新しいバイトの sha で pin する）

1 ファイル直したので終わり、にしないため（CLAUDE.md「grep で残数 0 を示してから
宣言する」）、**run8 の全書き込み口と全ファイル読み口を機械的に数える**。
新しい writer / reader を足したときにこのテストが落ちる = 掃討の再発防止になる。

実行: `python -m pytest voice_genesis/foundry/tests/test_s7_io_guards.py -q`
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_RUN8 = Path(__file__).resolve().parent.parent / "run8"
if str(_RUN8) not in sys.path:
    sys.path.insert(0, str(_RUN8))

import s7_io  # noqa: E402

#: 実行体（CLI を持ち、成果物を書くモジュール）。
CLI_MODULES = ("s7_b1_calibration.py", "s7_b2_algebra.py", "s7_ledger.py")

WRITE_PATTERN = re.compile(r"\.write_text\(|\.write_bytes\(|open\([^)]*['\"]w")
READ_PATTERN = re.compile(r"\.read_text\(|\.read_bytes\(")


def _source(name: str) -> str:
    return (_RUN8 / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("module", CLI_MODULES)
def test_every_writer_module_guards_against_non_finite_artifacts(module: str):
    """全 CLI が書き込み前に `assert_json_finite` を通す（Infinity / NaN を書かない）。"""
    assert "assert_json_finite(" in _source(module), f"{module} に非有限値ガードが無い"


@pytest.mark.parametrize("module", CLI_MODULES)
def test_every_writer_module_guards_against_output_collision(module: str):
    source = _source(module)
    n_writes = len(WRITE_PATTERN.findall(source))
    assert n_writes > 0, f"{module} に書き込み口が無い（テストの前提が古い）"
    assert "reject_output_collision(" in source, (
        f"{module} は書き込み口を {n_writes} 個持つのに衝突検査が無い"
    )


@pytest.mark.parametrize("module", CLI_MODULES + ("s7_spec.py",))
def test_no_module_reads_files_outside_the_shared_single_read_helper(module: str):
    """生の `read_text` / `read_bytes` は `s7_io` 以外に無い（parse と sha の分離読み禁止）。"""
    hits = READ_PATTERN.findall(_source(module))
    assert hits == [], f"{module} に s7_io を経由しないファイル読み込みがある: {hits}"


def test_shared_helper_rejects_collisions_and_pins_the_bytes_it_parsed(tmp_path: Path):
    src = tmp_path / "input.json"
    src.write_text('{"a": 1}', encoding="utf-8")

    with pytest.raises(s7_io.OutputCollisionError):
        s7_io.reject_output_collision([src], [src])
    s7_io.reject_output_collision([tmp_path / "out.json"], [src])

    doc, sha, n = s7_io.read_json_with_pin(src)
    assert doc == {"a": 1}
    assert sha == s7_io.sha256_bytes(src.read_bytes())
    assert n == len(src.read_bytes())


def test_collision_check_resolves_symlinks(tmp_path: Path):
    src = tmp_path / "real.csv"
    src.write_text("x", encoding="utf-8")
    link = tmp_path / "link.csv"
    link.symlink_to(src)
    with pytest.raises(s7_io.OutputCollisionError):
        s7_io.reject_output_collision([link], [src])
