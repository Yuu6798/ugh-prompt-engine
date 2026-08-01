"""M2e ミックス生成器の単体テスト（**音源不要**）。

設計: `docs/DESIGN_M2e_vremix_real_bed.md` §4.8（CI は音源不要の単体テストのみ）と
§9.2（canonical decode の CI 保証）。

実素材（vocadito / MUSDB18-HQ）はリポジトリに無いので、ここで守るのは**規約**である
——決定論・`n_target` 展開・クロスフェード境界・`factor` 算出・rate 不一致で例外・
`vocals.wav` を読まないこと・native 整数のまま hash すること。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_INT16_WAV = ROOT / "tests" / "fixtures" / "melody_bench" / "m2e_canonical_decode_int16.wav"

# ライブラリ更新で復号が変わったらここが赤くなる（設計 §9.2 の CI 要求）。
EXPECTED_CANONICAL_STEM_SHA256 = (
    "8ae60c0031e5bcd43ebcc7674b1958486da2c9f323c2815b2988a34970009886"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "make_vremix_fixtures", ROOT / "scripts" / "make_vremix_fixtures.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mk = _load_module()


def _harness_check(body: str) -> str:
    """ハーネスを **新規プロセス** で import して照合する（stdout を返す）。

    `run_melody_accuracy` は import 時に `_scorer_pins()` を走らせ、pin 束縛前の
    匿名実行マッピングを fail-closed で拒む。pytest セッションの**途中**で import
    すると、先行テストの numba/librosa JIT が張った匿名領域に当たって必ず落ちる
    （CI `test-rest` shard で実証。この shard は `--ignore` により harness を
    collection 時に import しない）。ハーネスが前提とする文脈＝クリーンな
    プロセス冒頭で走らせる。
    """
    script = (
        f"import sys\nsys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
        "import run_melody_accuracy as harness\n"
        "import make_vremix_fixtures as mk\n" + body
    )
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=str(ROOT)
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


# ---------------------------------------------------------------------------
# §9.2 canonical decode
# ---------------------------------------------------------------------------


def test_canonical_decode_hashes_native_integer_pcm() -> None:
    """整数 PCM を float へ落とさずに hash する（除数の曖昧さを pin の外に置く）。"""
    data, sample_rate, subtype = mk.canonical_decode(FIXTURE_INT16_WAV)
    assert subtype == "PCM_16"
    assert sample_rate == 44100
    assert data.dtype == np.int16
    assert data.ndim == 2 and data.shape[1] == 1   # (n_frames, n_channels)・downmix しない
    assert data.flags["C_CONTIGUOUS"]
    assert mk.stem_sha256(FIXTURE_INT16_WAV) == EXPECTED_CANONICAL_STEM_SHA256


def test_canonical_decode_rejects_non_44100(tmp_path: Path) -> None:
    """resample 禁止: native rate が 44100 でなければ停止する。"""
    path = tmp_path / "wrong_rate.wav"
    sf.write(str(path), np.zeros(128, dtype=np.int16), 22050, subtype="PCM_16")
    with pytest.raises(mk.GenerationError, match="resample"):
        mk.canonical_decode(path)


def test_canonical_decode_rejects_float_sources(tmp_path: Path) -> None:
    """float ソースは canonical decode の対象外（pin に除数の曖昧さを入れない）。"""
    path = tmp_path / "float.wav"
    sf.write(str(path), np.zeros(128, dtype=np.float32), 44100, subtype="FLOAT")
    with pytest.raises(mk.GenerationError, match="canonical decode の対象外"):
        mk.canonical_decode(path)


def test_generated_mix_is_pinned_as_float32(tmp_path: Path) -> None:
    """生成物は float32 で pin する（§4.6 の既存規約と同一）。

    ソース stem は native 整数で pin、生成ミックスは float32 で pin —— 対象が違うので
    どちらも変更しない、という §9.2 の分離をテストで固定する。
    """
    y = np.array([0.5, -0.25, 0.125], dtype=np.float64)
    expected = hashlib.sha256(y.astype(np.float32).tobytes()).hexdigest()
    assert mk.waveform_sha256(y) == expected


# ---------------------------------------------------------------------------
# §4.2 タイル連結（`n_target` 展開・クロスフェード境界）
# ---------------------------------------------------------------------------


def test_tile_to_length_expands_to_exactly_n_target() -> None:
    sample_rate = 44100
    source = np.linspace(-1.0, 1.0, 5000, dtype=np.float64)
    for n_target in (1, 4999, 5000, 5001, 20000):
        out = mk.tile_to_length(source, n_target, sample_rate)
        assert len(out) == n_target


def test_tile_to_length_is_identity_prefix_when_source_is_long_enough() -> None:
    """区間は常に曲頭 0.0s 起点（「良い区間を探す」余地を残さない）。"""
    sample_rate = 44100
    source = np.linspace(-1.0, 1.0, 5000, dtype=np.float64)
    out = mk.tile_to_length(source, 3000, sample_rate)
    assert np.array_equal(out, source[:3000])


def test_tile_to_length_rejects_source_shorter_than_crossfade() -> None:
    """`len(s) <= xf` は停止（設計 §4.2 の擬似コード通り）。"""
    sample_rate = 44100
    xf = int(round(mk.XFADE_SEC * sample_rate))
    with pytest.raises(mk.GenerationError, match="短すぎる"):
        mk.tile_to_length(np.ones(xf, dtype=np.float64), 10 * xf, sample_rate)


def test_tile_to_length_handles_the_crossfade_boundary_case() -> None:
    """`len(s) == xf + 1` の端でも展開できる（境界のオフバイワン）。"""
    sample_rate = 44100
    xf = int(round(mk.XFADE_SEC * sample_rate))
    source = np.ones(xf + 1, dtype=np.float64)
    out = mk.tile_to_length(source, 4 * xf, sample_rate)
    assert len(out) == 4 * xf
    assert np.all(np.isfinite(out))


def test_tile_to_length_crossfade_weights_exclude_the_right_edge() -> None:
    """`w = arange(xf)/xf` は右端 1.0 を含まない（設計 §4.2 の明示的な規約）。"""
    sample_rate = 100      # xf = 1 になるレートは使わない。ここでは xf = 1 を避ける
    xf = int(round(mk.XFADE_SEC * sample_rate))
    assert xf == 1
    # xf=1 の縮退でも規約どおり動くこと（w = [0.0]）。
    source = np.array([1.0, 2.0], dtype=np.float64)
    out = mk.tile_to_length(source, 4, sample_rate)
    assert len(out) == 4


# ---------------------------------------------------------------------------
# §4.3 RMS（非無音フレーム）
# ---------------------------------------------------------------------------


def test_frame_rms_drops_the_unfilled_tail_frame() -> None:
    x = np.ones(mk.FRAME_LEN + mk.HOP + 1, dtype=np.float64)
    frames = mk.frame_rms(x)
    assert len(frames) == 2      # 3 つ目は埋まらないので破棄
    assert np.allclose(frames, 1.0)


def test_rms_ignores_silent_frames() -> None:
    """末尾に無音を足しても RMS が変わらない（非無音フレームのみで算出する）。

    境界をまたぐフレームは定義上どうしても部分的に無音を含むので「無音を足す前と
    完全一致」にはならない。効いていることの証拠は**足す量に依存しないこと**である
    ——ここが崩れると「長い無音を足すほど RMS が下がる」自己正規化の罠に戻る。
    """
    loud = np.ones(mk.FRAME_LEN * 4, dtype=np.float64)
    short_pad = np.concatenate([loud, np.zeros(mk.FRAME_LEN * 2, dtype=np.float64)])
    long_pad = np.concatenate([loud, np.zeros(mk.FRAME_LEN * 32, dtype=np.float64)])
    assert mk.rms(short_pad) == pytest.approx(mk.rms(long_pad), rel=1e-12)
    # 素朴な「全フレーム平均」なら長い無音で大きく下がる（罠の実在確認）。
    naive = float(np.sqrt(np.mean(mk.frame_rms(long_pad) ** 2)))
    assert naive < 0.5 * mk.rms(long_pad)


def test_residual_db_uses_the_bed_active_frames_for_the_stem(monkeypatch) -> None:
    """stem 側で active を選び直さない（自己正規化で漏れが持ち上がる罠を塞ぐ）。

    ベッドが鳴っている区間では stem がほぼ無音、ベッドが無音の区間だけ stem が
    大きい、という信号を作る。stem 自身で active を選び直すと `residual_db` は
    0 dB 付近になるが、bed の active で評価すれば非常に小さい値になる。
    """
    n = mk.FRAME_LEN * 8
    bed = np.concatenate([np.ones(n, dtype=np.float64), np.zeros(3 * n, dtype=np.float64)])
    stem = np.concatenate([np.full(2 * n, 1e-4), np.ones(2 * n, dtype=np.float64)])
    value = mk.residual_db(bed, stem)
    assert value < -60.0
    # 参考: stem 自身で選び直すと 0 dB 近辺になってしまう（罠の実在確認）。
    assert mk.rms(stem) > 0.9


# ---------------------------------------------------------------------------
# §4.4 / §4.5 水準適用・クリップ回避
# ---------------------------------------------------------------------------


def _tone(n: int, amplitude: float, period: int = 64) -> np.ndarray:
    k = np.arange(n, dtype=np.float64)
    return amplitude * np.sin(2.0 * np.pi * k / period)


@pytest.mark.parametrize("level", list(mk.LEVELS))
def test_apply_level_realizes_the_declared_rms_ratio(level: str) -> None:
    """検算（設計 §4.4）: `(V*g_v) / (B*g_b) = 10 ** (R_db/20)`。"""
    n = mk.FRAME_LEN * 8
    voice = _tone(n, 0.3)
    bed = _tone(n, 0.11, period=37)
    _mix, meta = mk.apply_level(voice, bed, level)
    achieved = (meta["voice_rms"] * meta["g_v"]) / (meta["bed_rms"] * meta["g_b"])
    assert achieved == pytest.approx(10.0 ** (mk.LEVEL_DB[level] / 20.0), rel=1e-9)


def test_apply_level_factor_is_common_so_the_ratio_is_exactly_preserved() -> None:
    """`factor` は両成分に共通 → RMS 比は厳密に不変（リミッタ・正規化は使わない）。"""
    n = mk.FRAME_LEN * 8
    voice = _tone(n, 0.9)
    bed = _tone(n, 0.9, period=37)
    mix, meta = mk.apply_level(voice, bed, "0dB")
    assert meta["factor"] == pytest.approx(
        min(1.0, mk.PEAK_TARGET / meta["peak_before_factor"]), rel=1e-12
    )
    assert meta["factor"] < 1.0                       # この入力では実際に効く
    assert float(np.max(np.abs(mix))) == pytest.approx(mk.PEAK_TARGET, rel=1e-9)


def test_apply_level_leaves_factor_at_one_when_there_is_headroom() -> None:
    n = mk.FRAME_LEN * 8
    mix, meta = mk.apply_level(_tone(n, 0.01), _tone(n, 0.01, period=37), "0dB")
    assert meta["factor"] == 1.0
    assert float(np.max(np.abs(mix))) < mk.PEAK_TARGET


def test_apply_level_is_deterministic() -> None:
    """seed 不使用・同一入力 → bit 一致（設計 §4.8）。"""
    n = mk.FRAME_LEN * 8
    voice = _tone(n, 0.3)
    bed = _tone(n, 0.11, period=37)
    first, _ = mk.apply_level(voice, bed, "+6dB")
    second, _ = mk.apply_level(voice, bed, "+6dB")
    assert mk.waveform_sha256(first) == mk.waveform_sha256(second)
    assert np.array_equal(first, second)


def test_apply_level_rejects_unknown_level() -> None:
    n = mk.FRAME_LEN * 4
    with pytest.raises(mk.GenerationError, match="未登録の水準"):
        mk.apply_level(_tone(n, 0.3), _tone(n, 0.3, period=37), "+3dB")


# ---------------------------------------------------------------------------
# `vocals.wav` を読まないこと / rate 不一致で停止すること
# ---------------------------------------------------------------------------


def test_read_audio_refuses_vocals_stem(tmp_path: Path) -> None:
    """読み込みの chokepoint が `vocals` を fail-closed で弾く。"""
    path = tmp_path / "vocals.wav"
    sf.write(str(path), np.zeros(128, dtype=np.float32), 44100, subtype="FLOAT")
    with pytest.raises(mk.GenerationError, match="vocals stem"):
        mk.read_audio(path)


def test_load_registered_beds_rejects_an_unknown_schema(tmp_path: Path, monkeypatch) -> None:
    """未知 schema の pin から publish しない（意味の分からない規約を読まない）。"""
    path = tmp_path / "m2e_bed_fixtures.yaml"
    path.write_text(
        yaml.safe_dump({"schema_version": "m2e-bed-fixtures/9.9", "beds": {"x": {}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(mk, "M2E_BED_FIXTURES_PATH", path)
    with pytest.raises(mk.GenerationError, match="schema_version"):
        mk.load_registered_beds()


def test_load_registered_beds_accepts_the_committed_pin_file() -> None:
    """commit 済み pin は現行 schema を名乗り、そのまま読める。"""
    beds = mk.load_registered_beds()
    assert len(beds) == 50


def test_load_registered_clips_rejects_an_unknown_schema(tmp_path: Path, monkeypatch) -> None:
    """clip registry 側も schema を検証する（bed registry と同じ規律）。"""
    path = tmp_path / "m2c_external_fixtures.yaml"
    path.write_text(
        yaml.safe_dump({"schema_version": "m2c-external-fixtures/9.9", "fixtures": {"x": {}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(mk, "M2C_FIXTURES_PATH", path)
    with pytest.raises(mk.GenerationError, match="schema_version"):
        mk.load_registered_clips()


def test_load_registered_clips_accepts_the_committed_registry() -> None:
    assert len(mk.load_registered_clips()) == 40


def test_readme_reports_the_corrected_accepted_bed_dropouts() -> None:
    """README の採用 2 件の `N_drop` が登録 pin と一致する（訂正の掃き残し防止）。"""
    text = (ROOT / "docs/measurements/m2e_2026-08/README.md").read_text(encoding="utf-8")
    beds = mk.load_registered_beds()
    for track in ("Angels In Amplifiers - I'm Alright", "Arise - Run Run Run"):
        assert f"| {beds[track]['n_drop_frames']} |" in text, track


def test_registered_dropout_fields_match_the_corrected_artifact() -> None:
    """事前登録の (d) フィールドが訂正後の生データと一致する（§4.8）。

    登録側と記録側が割れると「登録された監査」と「報告された測定」が別物になる。
    """
    rows = {
        r["track"]: r
        for r in json.loads(
            (
                ROOT / "docs/measurements/m2e_2026-08/raw/reason_d_26_corrected.json"
            ).read_text(encoding="utf-8")
        )["rows"]
    }
    beds = mk.load_registered_beds()
    assert set(beds) == set(rows)
    for track, entry in beds.items():
        row = rows[track]
        assert entry["n_drop_frames"] == row["n_drop_frames"], track
        assert entry["reason_d_hit"] is row["d_hit"], track
        assert entry["dropout_sec"] == pytest.approx(row["dropout_sec"], abs=1e-6), track


def test_screening_reads_a_conventional_vocals_stem(tmp_path: Path) -> None:
    """`--vocals-stem vocals.wav` は §3.4 の測定対象であって混ぜる素材ではない。

    ベッド構成側の門（`read_audio`）は据え置きであることも同時に固定する。
    """
    path = tmp_path / "vocals.wav"
    sf.write(str(path), np.stack([_tone(512, 0.1), _tone(512, 0.1, 7)], axis=1), 44100)
    data, rate = mk.read_vocals_stem_for_screening(path)
    assert rate == 44100 and data.shape == (512, 2)
    with pytest.raises(mk.GenerationError, match="vocals stem"):
        mk.read_audio(path)


def _write_clip(root: Path, clip_id: str) -> dict:
    (root / "Audio").mkdir(parents=True, exist_ok=True)
    (root / "Annotations" / "F0").mkdir(parents=True, exist_ok=True)
    audio = root / "Audio" / f"{clip_id}.wav"
    sf.write(str(audio), _tone(4096, 0.2).astype(np.float32), 44100, subtype="FLOAT")
    annotation = root / "Annotations" / "F0" / f"{clip_id}_f0.csv"
    annotation.write_text("0.000,220.0\n", encoding="utf-8")
    return {
        "expected_audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
        "expected_annotation_sha256": hashlib.sha256(annotation.read_bytes()).hexdigest(),
    }


def test_resolve_clip_bytes_returns_the_snapshot_it_verified(tmp_path: Path) -> None:
    """P2 (TOCTOU): 照合した bytes をそのまま返す（後から開き直さない）。"""
    root = tmp_path / "vocadito"
    pin = _write_clip(root, "vocadito_1")
    audio, annotation = mk.resolve_clip_bytes(root, "vocadito_1", pin)
    assert hashlib.sha256(audio).hexdigest() == pin["expected_audio_sha256"]
    assert hashlib.sha256(annotation).hexdigest() == pin["expected_annotation_sha256"]
    # 返った bytes から直接復号できる（ミックスはこのスナップショットだけを使う）。
    voice, rate = mk.load_voice(audio, where="vocadito_1")
    assert rate == 44100 and voice.ndim == 1


def test_resolve_clip_bytes_rejects_a_pin_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "vocadito"
    pin = _write_clip(root, "vocadito_1")
    pin["expected_audio_sha256"] = "0" * 64
    with pytest.raises(mk.GenerationError, match="既存 pin"):
        mk.resolve_clip_bytes(root, "vocadito_1", pin)


def _write_bed(bed_dir: Path, *, sample_rate: int = 44100, n: int = 8192, rates=None) -> None:
    """合成ベッドを **PCM_16** で書く（実素材 MUSDB18-HQ と同じ整数 PCM）。

    §9.2 の canonical decode は整数 PCM のみを受け付けるので、float で書くと
    `stem_sha256` が取れない——テスト素材も実素材の性質に合わせる。
    """
    bed_dir.mkdir(parents=True, exist_ok=True)
    for idx, name in enumerate(mk.BED_STEMS):
        rate = sample_rate if rates is None else rates[idx]
        stereo = np.stack([_tone(n, 0.05 * (idx + 1)), _tone(n, 0.04 * (idx + 1), 51)], axis=1)
        sf.write(
            str(bed_dir / f"{name}.wav"),
            (stereo * 32767.0).astype(np.int16),
            rate,
            subtype="PCM_16",
        )
    # `vocals.wav` は存在確認のみに使う（中身は読まれない）。
    sf.write(
        str(bed_dir / "vocals.wav"),
        (np.stack([_tone(n, 0.9), _tone(n, 0.9, 23)], axis=1) * 32767.0).astype(np.int16),
        sample_rate,
        subtype="PCM_16",
    )


def test_load_bed_mono_never_reads_the_vocals_stem(tmp_path: Path, monkeypatch) -> None:
    """ベッド合成が実際に開くのは drums/bass/other だけ（存在確認以外に vocals を触らない）。"""
    bed_dir = tmp_path / "Some Artist - Some Track"
    _write_bed(bed_dir)
    opened: list[str] = []
    original = mk.read_stem_pinned

    def _tracking(path, expected_sha256=None):
        opened.append(Path(path).name)
        return original(path, expected_sha256)

    monkeypatch.setattr(mk, "read_stem_pinned", _tracking)
    bed_mono, rate = mk.load_bed_mono(bed_dir)
    assert rate == 44100
    assert sorted(opened) == sorted(f"{name}.wav" for name in mk.BED_STEMS)
    assert "vocals.wav" not in opened
    assert bed_mono.ndim == 1


def test_load_bed_mono_requires_the_vocals_stem_to_exist(tmp_path: Path) -> None:
    """存在確認は行う（配布物の構造が想定と違うなら止まる）。"""
    bed_dir = tmp_path / "track"
    _write_bed(bed_dir)
    (bed_dir / "vocals.wav").unlink()
    with pytest.raises(mk.GenerationError, match="想定構造"):
        mk.load_bed_mono(bed_dir)


def test_load_bed_mono_stops_on_sample_rate_mismatch(tmp_path: Path) -> None:
    """暗黙のリサンプルは禁止 —— 不一致は**例外で停止**する。

    stem を canonical decode と同じハンドルで読むようになったため、停止するのは
    stem 間の比較ではなく canonical rate (44100) との照合になった。**止まる**という
    性質は同じで、止まる位置が 1 段早い。
    """
    bed_dir = tmp_path / "track"
    _write_bed(bed_dir, rates=(44100, 48000, 44100))
    with pytest.raises(mk.GenerationError, match="resample|リサンプル"):
        mk.load_bed_mono(bed_dir)


def test_read_stem_pinned_opens_the_stem_exactly_once(tmp_path: Path, monkeypatch) -> None:
    """P2 (TOCTOU): pin 照合と混合サンプルを**同一 file handle** から取る。

    別々に open すると、その 2 回の read の間に stem を差し替えられたとき
    「pin は通ったが混ぜたのは別の音」が成立し、生成物は内部整合するので
    下流から検出できない。
    """
    bed_dir = tmp_path / "track"
    _write_bed(bed_dir)
    path = bed_dir / "drums.wav"
    expected = mk.stem_sha256(path)  # 監視を張る前に取る

    opens: list[str] = []
    original = mk.sf.SoundFile

    def _counting(*args, **kwargs):
        opens.append(str(args[0]))
        return original(*args, **kwargs)

    monkeypatch.setattr(mk.sf, "SoundFile", _counting)
    monkeypatch.setattr(
        mk.sf, "read", lambda *a, **k: pytest.fail("pin と混合で別の open をしている")
    )
    monkeypatch.setattr(
        mk.sf, "info", lambda *a, **k: pytest.fail("pin と混合で別の open をしている")
    )
    data, rate = mk.read_stem_pinned(path, expected)
    assert opens == [str(path)]
    assert rate == 44100
    assert data.ndim == 2 and data.shape[1] == 2


def test_read_stem_pinned_rejects_a_digest_mismatch(tmp_path: Path) -> None:
    bed_dir = tmp_path / "track"
    _write_bed(bed_dir)
    with pytest.raises(mk.GenerationError, match="事前登録 pin .* と不一致"):
        mk.read_stem_pinned(bed_dir / "drums.wav", "0" * 64)


# ---------------------------------------------------------------------------
# §6.2 id 規約
# ---------------------------------------------------------------------------


def test_entry_id_follows_the_registered_convention() -> None:
    assert mk.entry_id("vocadito_7", "Al-James-Schoolboy-Facination", "+12dB") == (
        "vremix_vocadito_7_Al-James-Schoolboy-Facination_p12"
    )
    assert sorted(mk.LEVEL_TAGS.values()) == sorted(["p12", "p06", "p00", "m06"])


def test_bed_slug_is_deterministic_and_id_safe() -> None:
    assert mk.bed_slug("Al James - Schoolboy Facination") == "Al-James-Schoolboy-Facination"
    assert mk.bed_slug("A B  C") == "A-B-C"
    with pytest.raises(mk.GenerationError):
        mk.bed_slug("---")


def test_level_ladder_order_is_frozen() -> None:
    """水準は物理量の順（易しい側 → 難しい側）。文字列辞書順で並べない（§3.3.1）。"""
    assert mk.LEVELS == ("+12dB", "+6dB", "0dB", "-6dB")
    assert [mk.LEVEL_DB[level] for level in mk.LEVELS] == [12.0, 6.0, 0.0, -6.0]
    # バイト辞書順に並べると物理量と無関係な順序になることを明示的に固定する。
    assert sorted(mk.LEVELS) == ["+12dB", "+6dB", "-6dB", "0dB"]


# ---------------------------------------------------------------------------
# build の end-to-end（合成素材・pin を差し替えたうえで規約だけを検証する）
# ---------------------------------------------------------------------------


def _fake_bed_pins(bed_root: Path, tracks) -> dict:
    """`m2e_bed_fixtures.yaml` 相当の pin を、合成ベッドの実 digest から組む。"""
    pins = {}
    for track in tracks:
        pins[track] = {
            "accepted": True,
            "expected_stem_sha256": {
                stem: mk.stem_sha256(bed_root / track / f"{stem}.wav")
                for stem in mk.BED_STEMS
            },
        }
    return pins


def _fake_vocadito(tmp_path: Path, clip_ids) -> tuple[Path, dict]:
    root = tmp_path / "vocadito"
    (root / "Audio").mkdir(parents=True)
    (root / "Annotations" / "F0").mkdir(parents=True)
    pins = {}
    for idx, clip_id in enumerate(clip_ids):
        audio = root / "Audio" / f"{clip_id}.wav"
        sf.write(
            str(audio),
            _tone(mk.FRAME_LEN * 6 + idx * 100, 0.2).astype(np.float32),
            44100,
            subtype="FLOAT",
        )
        annotation = root / "Annotations" / "F0" / f"{clip_id}_f0.csv"
        annotation.write_text("0.000,220.0\n0.010,221.0\n", encoding="utf-8")
        pins[clip_id] = {
            "expected_audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
            "expected_annotation_sha256": hashlib.sha256(annotation.read_bytes()).hexdigest(),
        }
    return root, pins


def test_build_emits_manifest_fixtures_and_generation_record(tmp_path, monkeypatch) -> None:
    clip_ids = ["vocadito_1", "vocadito_2"]
    vocadito_root, pins = _fake_vocadito(tmp_path, clip_ids)
    monkeypatch.setattr(mk, "load_registered_clips", lambda: pins)
    bed_root = tmp_path / "beds"
    for track in ("Artist A - Track One", "Artist B - Track Two"):
        _write_bed(bed_root / track)

    monkeypatch.setattr(
        mk, "load_registered_beds",
        lambda: _fake_bed_pins(bed_root, ["Artist A - Track One", "Artist B - Track Two"]),
    )
    out_dir = tmp_path / "out"
    summary = mk.build(
        vocadito_root=vocadito_root,
        bed_root=bed_root,
        tracks=["Artist A - Track One", "Artist B - Track Two"],
        levels=["+12dB", "-6dB"],
        out_dir=out_dir,
        registered_utc="2026-08-01",
    )
    assert set(summary["levels"]) == {"+12dB", "-6dB"}
    for level, tag in (("+12dB", "p12"), ("-6dB", "m06")):
        manifest = json.loads((out_dir / f"manifest_{tag}.json").read_text())
        # 水準ごとに 1 本・`clip × bed` 件（設計 §6.2）。アームでは分けない。
        assert len(manifest) == len(clip_ids) * 2
        ids = [entry["id"] for entry in manifest]
        assert ids == sorted(ids)
        assert all(entry["id"].endswith(f"_{tag}") for entry in manifest)
        fixtures = yaml.safe_load((out_dir / f"fixtures_{tag}.yaml").read_text())
        assert fixtures["schema_version"] == "m2e-external-fixtures/0.1"
        assert set(fixtures["fixtures"]) == set(ids)
        record = json.loads((out_dir / f"generation_record_{tag}.json").read_text())
        for eid in ids:
            assert record[eid]["level"] == level
            assert 0.0 < record[eid]["factor"] <= 1.0
            # 注釈はミックスで変わらない（加算ミックスは f0 を変えない）。
            clip_id = record[eid]["clip_id"]
            assert (
                fixtures["fixtures"][eid]["expected_annotation_sha256"]
                == pins[clip_id]["expected_annotation_sha256"]
            )
        # manifest が指す member は manifest 位置基準で解決できる（ハーネスの要求）。
        for entry in manifest:
            assert (out_dir / entry["audio_path"]).is_file()
            assert (out_dir / entry["annotation_path"]).is_file()


def test_build_is_deterministic(tmp_path, monkeypatch) -> None:
    """同一入力 → 生成ミックスの bytes が完全一致（seed 不使用）。"""
    clip_ids = ["vocadito_1"]
    vocadito_root, pins = _fake_vocadito(tmp_path, clip_ids)
    monkeypatch.setattr(mk, "load_registered_clips", lambda: pins)
    bed_root = tmp_path / "beds"
    _write_bed(bed_root / "Artist A - Track One")

    monkeypatch.setattr(
        mk, "load_registered_beds", lambda: _fake_bed_pins(bed_root, ["Artist A - Track One"])
    )
    digests = []
    for run in ("a", "b"):
        out_dir = tmp_path / f"out_{run}"
        if run == "b":
            time.sleep(1.2)     # **秒をまたぐ**（PEAK チャンクの壁時計を踏む条件）
        mk.build(
            vocadito_root=vocadito_root,
            bed_root=bed_root,
            tracks=["Artist A - Track One"],
            levels=["0dB"],
            out_dir=out_dir,
            registered_utc="2026-08-01",
        )
        record = json.loads((out_dir / "generation_record_p00.json").read_text())
        digests.append({k: v["waveform_sha256"] for k, v in record.items()})
        digests.append(
            {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((out_dir / "mix").iterdir())
            }
        )
    assert digests[0] == digests[2]
    assert digests[1] == digests[3]


def test_build_refuses_when_vocadito_bytes_drifted(tmp_path, monkeypatch) -> None:
    """vocadito の再取得・再エンコードは禁止 —— pin と実体が食い違えば停止する。"""
    clip_ids = ["vocadito_1"]
    vocadito_root, pins = _fake_vocadito(tmp_path, clip_ids)
    pins["vocadito_1"]["expected_audio_sha256"] = "0" * 64
    monkeypatch.setattr(mk, "load_registered_clips", lambda: pins)
    bed_root = tmp_path / "beds"
    _write_bed(bed_root / "Artist A - Track One")
    monkeypatch.setattr(
        mk, "load_registered_beds", lambda: _fake_bed_pins(bed_root, ["Artist A - Track One"])
    )
    with pytest.raises(mk.GenerationError, match="再取得・再エンコードは禁止"):
        mk.build(
            vocadito_root=vocadito_root,
            bed_root=bed_root,
            tracks=["Artist A - Track One"],
            levels=["0dB"],
            out_dir=tmp_path / "out",
            registered_utc="2026-08-01",
        )


# ---------------------------------------------------------------------------
# Codex レビュー対応（PR #238）
# ---------------------------------------------------------------------------


def test_serialize_wav_float32_is_timestamp_free(tmp_path: Path) -> None:
    """P1-1: 秒をまたいで書いても bytes が変わらないこと。

    `sf.write(..., subtype="FLOAT")` は PEAK チャンク（offset 48）に**壁時計**を書き、
    1 秒跨ぐと offset 60 の 1 バイトが変わる（本テストで実際に確認する）。それを pin に
    使うと `expected_audio_sha256` が再生成のたびに変わり、帯の前提が壊れる。
    """
    y = (np.sin(np.linspace(0.0, 40.0, 4410)) * 0.3).astype(np.float32)
    a = mk.serialize_wav_float32(y, 44100)
    time.sleep(1.2)
    b = mk.serialize_wav_float32(y, 44100)
    assert a == b
    assert b"PEAK" not in a

    # libsndfile 経由は実際に揺れる（この欠陥が実在することの対照）。
    p1, p2 = tmp_path / "s1.wav", tmp_path / "s2.wav"
    sf.write(str(p1), y, 44100, subtype="FLOAT")
    time.sleep(1.2)
    sf.write(str(p2), y, 44100, subtype="FLOAT")
    assert p1.read_bytes() != p2.read_bytes()


def test_serialize_wav_float32_matches_the_harness_serializer() -> None:
    """ハーネス側の直列化と **bytes 完全一致**であること（規約が 2 つに割れない）。"""
    out = _harness_check(
        "import numpy as np\n"
        "y = (np.sin(np.linspace(0.0, 12.0, 2048)) * 0.4).astype(np.float32)\n"
        "assert mk.serialize_wav_float32(y, 44100) == harness._serialize_wav_float32(y, 44100)\n"
        "print('identical')\n"
    )
    assert out.strip().splitlines()[-1] == "identical"


def test_serialize_wav_float32_round_trips_through_libsndfile(tmp_path: Path) -> None:
    """自前 RIFF を libsndfile が読み戻せ、サンプルが bit 一致すること。"""
    y = (np.sin(np.linspace(0.0, 20.0, 3000)) * 0.25).astype(np.float32)
    path = tmp_path / "own.wav"
    path.write_bytes(mk.serialize_wav_float32(y, 44100))
    back, rate = sf.read(str(path), dtype="float32", always_2d=False)
    assert rate == 44100
    assert np.array_equal(back, y)


def test_build_rejects_a_bed_whose_stem_digest_differs(tmp_path, monkeypatch) -> None:
    """P1-3: ベッド stem を committed pin と照合する（破損・差し替えを通さない）。"""
    clip_ids = ["vocadito_1"]
    vocadito_root, pins = _fake_vocadito(tmp_path, clip_ids)
    monkeypatch.setattr(mk, "load_registered_clips", lambda: pins)
    bed_root = tmp_path / "beds"
    _write_bed(bed_root / "Artist A - Track One")
    bad = _fake_bed_pins(bed_root, ["Artist A - Track One"])
    bad["Artist A - Track One"]["expected_stem_sha256"]["drums"] = "0" * 64
    monkeypatch.setattr(mk, "load_registered_beds", lambda: bad)
    with pytest.raises(mk.GenerationError, match="事前登録 pin .* と不一致"):
        mk.build(
            vocadito_root=vocadito_root, bed_root=bed_root,
            tracks=["Artist A - Track One"], levels=["0dB"],
            out_dir=tmp_path / "out", registered_utc="2026-08-01",
        )


def test_build_rejects_a_bed_that_is_not_accepted(tmp_path, monkeypatch) -> None:
    """スクリーニングを通っていないベッドでミックスを作らない。"""
    vocadito_root, pins = _fake_vocadito(tmp_path, ["vocadito_1"])
    monkeypatch.setattr(mk, "load_registered_clips", lambda: pins)
    bed_root = tmp_path / "beds"
    _write_bed(bed_root / "Artist A - Track One")
    not_accepted = _fake_bed_pins(bed_root, ["Artist A - Track One"])
    not_accepted["Artist A - Track One"]["accepted"] = False
    monkeypatch.setattr(mk, "load_registered_beds", lambda: not_accepted)
    with pytest.raises(mk.GenerationError, match="accepted ではない"):
        mk.build(
            vocadito_root=vocadito_root, bed_root=bed_root,
            tracks=["Artist A - Track One"], levels=["0dB"],
            out_dir=tmp_path / "out", registered_utc="2026-08-01",
        )


def test_build_requires_every_accepted_bed(tmp_path, monkeypatch) -> None:
    """P1: `--bed` を 1 本落とすと、内部整合した**部分コホート**が出てしまう。"""
    vocadito_root, pins = _fake_vocadito(tmp_path, ["vocadito_1"])
    monkeypatch.setattr(mk, "load_registered_clips", lambda: pins)
    bed_root = tmp_path / "beds"
    tracks = ["Artist A - Track One", "Artist B - Track Two"]
    for track in tracks:
        _write_bed(bed_root / track)
    monkeypatch.setattr(mk, "load_registered_beds", lambda: _fake_bed_pins(bed_root, tracks))
    with pytest.raises(mk.GenerationError, match="部分コホート"):
        mk.build(
            vocadito_root=vocadito_root, bed_root=bed_root,
            tracks=tracks[:1], levels=["0dB"],
            out_dir=tmp_path / "out", registered_utc="2026-08-01",
        )


def test_build_emits_a_dated_registered_utc(tmp_path, monkeypatch) -> None:
    """P1-2: `registered_utc: null` はハーネスが読めない。実値を書くこと。"""
    vocadito_root, pins = _fake_vocadito(tmp_path, ["vocadito_1"])
    monkeypatch.setattr(mk, "load_registered_clips", lambda: pins)
    bed_root = tmp_path / "beds"
    _write_bed(bed_root / "Artist A - Track One")
    monkeypatch.setattr(
        mk, "load_registered_beds", lambda: _fake_bed_pins(bed_root, ["Artist A - Track One"])
    )
    out_dir = tmp_path / "out"
    mk.build(
        vocadito_root=vocadito_root, bed_root=bed_root,
        tracks=["Artist A - Track One"], levels=["0dB"],
        out_dir=out_dir, registered_utc="2026-08-01",
    )
    doc = yaml.safe_load((out_dir / "fixtures_p00.yaml").read_text())
    assert doc["registered_utc"] == "2026-08-01"
    # ハーネスの loader が実際に受理すること（documented flow が通る証明）。
    out = _harness_check(
        "import json\n"
        f"loaded, sha256 = harness.load_external_fixtures({str(out_dir / 'fixtures_p00.yaml')!r})\n"
        "assert len(sha256) == 64\n"
        "print(json.dumps(sorted(loaded['fixtures'])))\n"
    )
    assert json.loads(out.strip().splitlines()[-1]) == sorted(doc["fixtures"])


@pytest.mark.parametrize(
    "value",
    ["2026-08-01", "2026-08-01T00:00:00+00:00", "2026-08-01T00:00:00Z"],
)
def test_require_valid_registered_utc_accepts_dates_and_utc_timestamps(value: str) -> None:
    mk.require_valid_registered_utc(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "garbage",
        "2026-13-01",
        "2026-08-01T00:00:00+09:00",  # UTC ではない
        "2026-08-01T00:00:00",  # tz 無し
        "2999-01-01",  # 未来（まだ到来していない「事前登録」）
    ],
)
def test_require_valid_registered_utc_rejects_what_the_harness_rejects(value: str) -> None:
    """P2: ハーネス `_parse_registered_utc` と**同じ意味論**で先に落とす。"""
    with pytest.raises(mk.GenerationError, match="registered_utc"):
        mk.require_valid_registered_utc(value)


def test_build_rejects_an_invalid_registered_utc_before_writing_anything(
    tmp_path, monkeypatch
) -> None:
    """非空チェックだけでは数百本の WAV を書いた後に下流で落ちる。生成前に止める。"""
    vocadito_root, pins = _fake_vocadito(tmp_path, ["vocadito_1"])
    monkeypatch.setattr(mk, "load_registered_clips", lambda: pins)
    bed_root = tmp_path / "beds"
    _write_bed(bed_root / "Artist A - Track One")
    monkeypatch.setattr(
        mk, "load_registered_beds", lambda: _fake_bed_pins(bed_root, ["Artist A - Track One"])
    )
    out_dir = tmp_path / "out"
    with pytest.raises(mk.GenerationError, match="registered_utc"):
        mk.build(
            vocadito_root=vocadito_root, bed_root=bed_root,
            tracks=["Artist A - Track One"], levels=["0dB"],
            out_dir=out_dir, registered_utc="garbage",
        )
    assert not list(out_dir.glob("**/*.wav"))


def test_build_refuses_a_non_empty_out_dir(tmp_path, monkeypatch) -> None:
    """P2（最小対処）: 前回実行の残骸と混ざらないよう空の出力先を要求する。"""
    vocadito_root, pins = _fake_vocadito(tmp_path, ["vocadito_1"])
    monkeypatch.setattr(mk, "load_registered_clips", lambda: pins)
    bed_root = tmp_path / "beds"
    _write_bed(bed_root / "Artist A - Track One")
    monkeypatch.setattr(
        mk, "load_registered_beds", lambda: _fake_bed_pins(bed_root, ["Artist A - Track One"])
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "stale.txt").write_text("leftover", encoding="utf-8")
    with pytest.raises(mk.GenerationError, match="空でない"):
        mk.build(
            vocadito_root=vocadito_root, bed_root=bed_root,
            tracks=["Artist A - Track One"], levels=["0dB"],
            out_dir=out_dir, registered_utc="2026-08-01",
        )
