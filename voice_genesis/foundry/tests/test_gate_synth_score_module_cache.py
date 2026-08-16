"""test_gate_synth_score_module_cache.py — review #264 R2 P2 再現テスト。

`load_song_module`（`s1_gate/gate_synth.py`）は呼び出し対象の song モジュール
（`score`/`score_umi`）を import 前に `sys.modules` から evict することで、
別 `singer_dir` から連続ロードした際に古い内容がキャッシュされたまま使われる
「ハッシュ偽装」（review #264 R1）を封じていた。しかし `score_umi.py` は
`from score import ScoreNote` で `score` モジュールへ推移的に依存するため、
`song == "umi"` の呼び出しが `score_umi` のみを evict すると、`score` 自体は
evict されずキャッシュが残り得る（R1 と同型のバグが依存モジュール側で再発）。

本テストは、内容だけが異なる `score.py`（`MARKER` 属性で識別）を持つ 2 つの
`singer_dir` から「umi」を連続ロードし、2 回目の呼び出しが新しい
`singer_dir` の `score.py` 内容（推移的依存先）を正しく反映することを検証
する（review #264 R2 の修正対象そのものの再現）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "s1_gate"))

import gate_synth as gs  # noqa: E402

_PHONEME_JP_SRC = '"""fake phoneme_jp.py for cache eviction test."""\n'

_SCORE_SRC_TEMPLATE = '''"""fake score.py for cache eviction test (marker={marker!r})."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

MARKER = {marker!r}
TEMPO_BPM = 120.0


@dataclass
class ScoreNote:
    midi: int


def beats_to_seconds(beats: float, tempo_bpm: float) -> float:
    return beats * 60.0 / tempo_bpm


def build_sakura_score() -> "List[ScoreNote]":
    return []
'''

_SCORE_UMI_SRC = '''"""fake score_umi.py for cache eviction test."""
from __future__ import annotations

from typing import List

import phoneme_jp as pj  # noqa: F401  (real score_umi.py has the same dependency)
from score import MARKER, ScoreNote  # noqa: F401  (transitive dependency under test)

TEMPO_BPM = 120.0


def beats_to_seconds(beats: float, tempo_bpm: float) -> float:
    return beats * 60.0 / tempo_bpm


def build_umi_score() -> "List[ScoreNote]":
    return []
'''


def _make_fake_singer_dir(base: Path, name: str, marker: str) -> Path:
    d = base / name
    d.mkdir(parents=True)
    (d / "phoneme_jp.py").write_text(_PHONEME_JP_SRC, encoding="utf-8")
    (d / "score.py").write_text(_SCORE_SRC_TEMPLATE.format(marker=marker), encoding="utf-8")
    (d / "score_umi.py").write_text(_SCORE_UMI_SRC, encoding="utf-8")
    return d


@pytest.fixture(autouse=True)
def _isolate_score_family_modules() -> Iterator[None]:
    """score 系モジュールキャッシュ・`sys.path` へのテスト用エントリを、
    テスト前後で確実に片付ける（他テストへの汚染防止）。"""
    original_sys_path = list(sys.path)
    yield
    for name in ("score", "score_umi", "phoneme_jp"):
        sys.modules.pop(name, None)
    sys.path[:] = original_sys_path


def test_load_song_module_umi_reflects_new_score_py_across_singer_dirs(
    tmp_path: Path,
) -> None:
    dir_a = _make_fake_singer_dir(tmp_path, "singer_a", marker="A")
    dir_b = _make_fake_singer_dir(tmp_path, "singer_b", marker="B")

    _, _, _, path_a = gs.load_song_module("umi", dir_a)
    assert path_a == (dir_a / "score_umi.py").resolve()
    import score as sc_after_a  # sys.modules に載った直後の状態を直接検査

    assert sc_after_a.MARKER == "A"

    # [P2 修正] (review #264 R2) の再現対象: dir_b（score.py の MARKER="B"）
    # から2回目の「umi」ロードを行った際、旧実装（score_umi のみ evict）だと
    # `score` モジュールは evict されず sys.modules にキャッシュされたままの
    # dir_a 版（MARKER="A"）が使われ続けてしまっていた。
    _, _, _, path_b = gs.load_song_module("umi", dir_b)
    assert path_b == (dir_b / "score_umi.py").resolve()
    import score as sc_after_b

    assert sc_after_b.MARKER == "B"


def test_load_song_module_evicts_full_score_family_before_import(tmp_path: Path) -> None:
    """`load_song_module` が song に関わらず `score`/`score_umi`/`phoneme_jp`
    の全てを evict すること自体を直接検証する（挙動の単体確認、上記は結果的な
    end-to-end 再現）。"""
    dir_a = _make_fake_singer_dir(tmp_path, "singer_a", marker="A")
    dir_b = _make_fake_singer_dir(tmp_path, "singer_b", marker="B")

    gs.load_song_module("sakura", dir_a)
    assert "score" in sys.modules
    assert sys.modules["score"].MARKER == "A"

    # sakura ロードは score_umi/phoneme_jp を import しないが、evict 自体は
    # song に関わらず score 系全体に対して行われるため、次の umi ロードが
    # dir_b の score.py を正しく反映することを確認する。
    gs.load_song_module("umi", dir_b)
    assert sys.modules["score"].MARKER == "B"
