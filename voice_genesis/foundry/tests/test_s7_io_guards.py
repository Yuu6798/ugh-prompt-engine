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


# --- WAV を測る前の pin 照合（PR #303 レビュー第 1 巡 P1） --------------------

#: WAV を復号する行のうち、`s7_io.read_wav_with_pins` を通さなくてよい唯一の場所。
#: **書いた直後に自分で読み返す自己検査**であり、pin はまさにここで作られるので
#: 「照合すべき記録済み pin」がまだ存在しない。
WAV_READ_ALLOWLIST = {
    "s7_io.py": 1,            # 単一実装そのもの（ここが唯一の復号口）
    "s7_calib_render.py": 1,  # zero buffer を書いた直後の厳密ゼロ検査
}

SF_READ_PATTERN = re.compile(r"\bsf\.read\(")


def _run8_modules():
    return sorted(p.name for p in _RUN8.glob("*.py"))


@pytest.mark.parametrize("module", _run8_modules())
def test_every_wav_consumer_verifies_recorded_pins_before_decoding(module: str):
    """記録済み pin を持つ WAV を**照合せずに**復号する経路を run8 全体で 0 にする。

    PR #303 のレビューは `s7_0b_remeasure_12.py` 1 箇所を指したが、数えたら同型が
    4 箇所（remeasure ×2 / amp_rungs / calib_render の派生条件）あった。1 箇所
    直して終わりにしないため、**モジュールを固定列挙せず glob で全数を数える**
    （新しい reader を足すとここが落ちる = 再発防止）。
    """
    n_raw = len(SF_READ_PATTERN.findall(_source(module)))
    allowed = WAV_READ_ALLOWLIST.get(module, 0)
    assert n_raw <= allowed, (
        f"{module}: pin 照合を通さない sf.read が {n_raw} 個ある"
        f"（許容 {allowed}）。s7_io.read_wav_with_pins を使うこと"
    )


def _write_float_wav(path: Path, y):
    import soundfile as sf

    sf.write(str(path), y, 44100, subtype="FLOAT")


def test_read_wav_with_pins_accepts_matching_pins_and_returns_float64(tmp_path: Path):
    import numpy as np

    y = np.linspace(-0.5, 0.5, 512, dtype=np.float32)
    wav = tmp_path / "a.wav"
    _write_float_wav(wav, y)
    container = s7_io.sha256_bytes(wav.read_bytes())
    samples = s7_io.sha256_bytes(np.ascontiguousarray(y).tobytes())

    got, sr = s7_io.read_wav_with_pins(wav, container, samples)
    assert sr == 44100
    assert got.dtype == np.float64
    # float32 -> float64 の拡大は厳密。直接 float64 で読んだ場合とビット一致する。
    import soundfile as sf

    direct, _ = sf.read(str(wav), dtype="float64", always_2d=False)
    assert np.array_equal(got, np.asarray(direct))


def test_read_wav_with_pins_rejects_a_swapped_file(tmp_path: Path):
    """**標本が違う**ファイルに差し替わったら測らせない（容器 sha で先に落ちる）。"""
    import numpy as np

    y = np.linspace(-0.5, 0.5, 512, dtype=np.float32)
    wav = tmp_path / "a.wav"
    _write_float_wav(wav, y)
    container = s7_io.sha256_bytes(wav.read_bytes())
    samples = s7_io.sha256_bytes(np.ascontiguousarray(y).tobytes())

    _write_float_wav(wav, y * np.float32(0.5))  # 別バッチに差し替わった
    with pytest.raises(s7_io.WavPinMismatch):
        s7_io.read_wav_with_pins(wav, container, samples)


def test_read_wav_with_pins_rejects_a_rerender_that_only_matches_the_container(
    tmp_path: Path,
):
    """容器 sha だけ合わせても、**標本 sha が違えば**測らせない。

    float WAV の容器は libsndfile の PEAK チャンクに書き出し時刻を含むため、
    同じ標本の再レンダでも容器 sha は変わる。逆に容器 sha だけを信じると、
    再 export で 1e-8 ずれた標本を「同一」と記帳してしまう。
    """
    import numpy as np

    y = np.linspace(-0.5, 0.5, 512, dtype=np.float32)
    wav = tmp_path / "a.wav"
    _write_float_wav(wav, y)
    container = s7_io.sha256_bytes(wav.read_bytes())
    other = s7_io.sha256_bytes(np.ascontiguousarray(y * np.float32(1.0000001)).tobytes())
    with pytest.raises(s7_io.WavPinMismatch):
        s7_io.read_wav_with_pins(wav, container, other)


# --- 文書由来の名前をディレクトリへ連結する（PR #303 第 5 巡 P1 と同型） -------

#: 事前登録 JSON / manifest / 群 JSON から来た名前を連結する全モジュール。
#: レビューは export 側 1 箇所を指したが、数えたら run8 に 7 箇所あった。
DOCUMENT_JOIN_MODULES = (
    "s7_0b_remeasure.py",
    "s7_0b_remeasure_12.py",
    "s7_b1_calibration.py",
    "s7_b1_v12.py",
    "s7_calib_amp_rungs.py",
    "s7_calib_render.py",
    "s7_export_manifest.py",
)

def _raw_document_joins(source: str):
    """`<なにか>_dir / doc[...]` 形の**素の連結**を AST で数える。

    正規表現だと docstring 内の説明まで拾ってしまうので、構文木で見る
    （コメント・文字列は最初から対象外になる）。
    """
    import ast

    hits = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
            continue
        left = node.left
        if not (isinstance(left, ast.Name) and left.id.endswith("_dir")):
            continue
        right = node.right
        # str(...) を剥がしてから、文書由来（添字アクセス）かどうかを見る
        if isinstance(right, ast.Call) and getattr(right.func, "id", None) == "str":
            right = right.args[0] if right.args else right
        if isinstance(right, ast.Subscript):
            hits.append(f"line {node.lineno}")
    return hits


@pytest.mark.parametrize("module", DOCUMENT_JOIN_MODULES)
def test_document_supplied_names_are_never_joined_without_containment(module: str):
    """`"wav": "../../x.wav"` のような値で、pin 照合の対象が外のファイルにならない。"""
    source = _source(module)
    assert "child_path(" in source, f"{module} が s7_io.child_path を使っていない"
    raw = _raw_document_joins(source)
    assert raw == [], f"{module} に封じ込め無しの連結が残っている: {raw}"


def test_child_path_accepts_a_plain_relative_name(tmp_path: Path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.wav").write_bytes(b"x")
    assert s7_io.child_path(tmp_path, "sub/a.wav") == (tmp_path / "sub" / "a.wav").resolve()


@pytest.mark.parametrize("name", ["/etc/passwd", "../a.wav", "sub/../../a.wav"])
def test_child_path_rejects_names_that_escape(tmp_path: Path, name: str):
    with pytest.raises(s7_io.PathEscapeError):
        s7_io.child_path(tmp_path, name)
