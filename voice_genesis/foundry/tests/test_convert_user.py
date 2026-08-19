"""test_convert_user.py — S3 Phase C `s1_dataprep/convert_user.py` の検証。

`DESIGN_S3_backfill.md` §3/§3.1 の受け入れ条件（tier 別アラインメント正常系 3
本・台帳不突合 fail-closed・T1 3 段検出失敗 fail-closed・決定論 2 回一致・
build_dataset ゲート直呼び）+ C2（dsdict.yaml グラフェム音素化: 促音/拗音/
外来語行/ヴ除外/併合規則）を高速・合成フィクスチャ（正弦波 + 無音）で検証する。
実収録音源（scratchpad の 17 本）・実 dsdict.yaml（外部素材）には依存しない
（それらを使った実測検証は別途 `$SCRATCH/c_verify/` に記録する）。

`_TEST_DSDICT_ENTRIES` は実 `dsdict.yaml`（zip sha256
`5c7b8c328180ea2971f71d89b3a675b2adfc91772664ae28cbb5915385f42530`、
`s1_dataprep/README.md` 素材3 pin と一致確認済み）を一次ソース確認した上で、
`T2_PHRASES` の全カード（UC-010 のヴ系を除く）をトークン化するのに実際に
使われたグラフェム 69 件だけを抜粋したサブセット（値は実辞書と同一）。
"""
from __future__ import annotations

import csv as csv_module
import hashlib
import json
import sys
import wave
from pathlib import Path
from typing import List

import numpy as np
import pytest
import soundfile as sf
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "s1_dataprep"))
import convert_user as cu  # noqa: E402

_FFMPEG_MISSING = cu.convert_d3.FFMPEG_PATH is None
_ffmpeg_required = pytest.mark.skipif(
    _FFMPEG_MISSING, reason="ffmpeg が見つからない環境ではスキップ（resample_to_44k1 依存）"
)

SR = 24000

# 実 dsdict.yaml から一次ソース確認済みの抜粋（モジュール docstring参照）。
# ヴ系グラフェムは実辞書どおり意図的に含めない（UC-010 除外の検証に使う）。
_TEST_DSDICT_ENTRIES = {
    "ティ": ["t", "I"], "カ": ["k", "A"], "っ": ["cl"], "プ": ["p", "U"],
    "か": ["k", "a"], "た": ["t", "a"], "て": ["t", "e"], "に": ["n", "i"],
    "ディ": ["d", "I"], "ナ": ["n", "A"], "の": ["n", "o"], "メ": ["m", "E"],
    "ロ": ["r", "O"], "ファ": ["f", "A"], "ん": ["N"], "レ": ["r", "E"],
    "フィ": ["f", "I"], "フェ": ["f", "E"], "ス": ["s", "U"], "タ": ["t", "A"],
    "で": ["d", "e"], "フォ": ["f", "O"], "ル": ["r", "U"], "テ": ["t", "E"],
    "ひ": ["h", "i"], "び": ["b", "i"], "き": ["k", "i"], "と": ["t", "o"],
    "ず": ["z", "u"], "ま": ["m", "a"], "い": ["i"], "つ": ["ts", "u"],
    "み": ["m", "i"], "わ": ["w", "a"], "す": ["s", "u"], "ぎ": ["g", "i"],
    "り": ["r", "i"], "が": ["g", "a"], "や": ["y", "a"], "く": ["k", "u"],
    "きゃ": ["ky", "a"], "せ": ["s", "e"], "ぎゃ": ["gy", "a"], "ふ": ["f", "u"],
    "う": ["u"], "きょ": ["ky", "o"], "も": ["m", "o"], "ゆ": ["y", "u"],
    "しゃ": ["sh", "a"], "ぼ": ["b", "o"], "だ": ["d", "a"], "じゃ": ["j", "a"],
    "ぷ": ["p", "u"], "ちゃ": ["ch", "a"], "ろ": ["r", "o"], "ちょ": ["ch", "o"],
    "ぱ": ["p", "a"], "ぴ": ["p", "i"], "ぽ": ["p", "o"], "ば": ["b", "a"],
    "ら": ["r", "a"], "は": ["h", "a"], "な": ["n", "a"], "ざ": ["z", "a"],
    "め": ["m", "e"], "る": ["r", "u"], "ぜ": ["z", "e"], "そ": ["s", "o"],
    "を": ["w", "o"],
}


def _write_test_dsdict(tmp_path: Path, extra: dict | None = None) -> Path:
    entries = dict(_TEST_DSDICT_ENTRIES)
    if extra:
        entries.update(extra)
    payload = {
        "entries": [{"grapheme": g, "phonemes": list(p)} for g, p in entries.items()],
    }
    path = tmp_path / "dsdict.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 合成音声フィクスチャ生成ヘルパー
# ---------------------------------------------------------------------------


def _silence(duration_sec: float, sr: int = SR) -> np.ndarray:
    return np.zeros(int(round(duration_sec * sr)), dtype=np.float64)


def _tone(freq_hz: float, duration_sec: float, sr: int = SR, amp: float = 0.3) -> np.ndarray:
    n = int(round(duration_sec * sr))
    t = np.arange(n) / sr
    # フェード無しの純音は境界でクリックを生むが、20ms フレーム RMS ゲートの
    # 判定には影響しない（境界フレームのみへの局所的な影響のため、区間長
    # 判定・f0 中央値のいずれにも実害はない）。
    return amp * np.sin(2.0 * np.pi * freq_hz * t)


def _concat(*parts: np.ndarray) -> np.ndarray:
    return np.concatenate(parts) if parts else np.array([], dtype=np.float64)


def _write_wav(path: Path, samples: np.ndarray, sr: int = SR) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), samples, sr, subtype="PCM_16")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_ledger(entries: List[dict]) -> dict:
    return {"schema": "user-donor-ledger/0.1", "entries": entries}


def _write_ledger_and_normalized(
    tmp_path: Path, cards: dict
) -> tuple[Path, Path]:
    """`cards`: card_id -> samples(np.ndarray)。台帳 + normalized-dir を書いて
    (ledger_path, normalized_dir) を返す。"""
    normalized_dir = tmp_path / "normalized"
    normalized_dir.mkdir(exist_ok=True)
    entries = []
    for card_id, samples in cards.items():
        filename = f"{card_id}_test.norm24k.wav"
        wav_path = normalized_dir / filename
        _write_wav(wav_path, samples)
        entries.append({
            "card_id": card_id,
            "source_filename": f"{card_id}_test.mp3",
            "source_sha256": "0" * 64,
            "source_size_bytes": 1,
            "normalized_path": f"user_donor_normalized/{filename}",
            "sha256": _sha256(wav_path),
            "received_at": "2026-08-17T00:00:00Z",
            "duration_sec": round(len(samples) / SR, 3),
            "sample_rate": SR,
            "rms_dbfs": -18.0,
            "peak_dbfs": -3.0,
            "alignment_status": "not_started",
        })
    ledger_path = tmp_path / "user_donor_ledger.json"
    ledger_path.write_text(json.dumps(_make_ledger(entries), ensure_ascii=False, indent=2), encoding="utf-8")
    return ledger_path, normalized_dir


# ---------------------------------------------------------------------------
# フィクスチャ: tier 別カード音声
# ---------------------------------------------------------------------------


def _t1_card_samples(freqs=(130.81, 164.81, 196.00)) -> np.ndarray:
    """UC-003〜007 型: 無音 + 3 段（各 1.2s、間 0.2s 無音、前後 0.2s 無音）。"""
    parts = [_silence(0.2)]
    for f in freqs:
        parts.append(_tone(f, 1.2))
        parts.append(_silence(0.2))
    return _concat(*parts)


def _t1_card_samples_only_2_segments() -> np.ndarray:
    return _concat(_silence(0.2), _tone(130.81, 1.2), _silence(0.2), _tone(196.00, 1.2), _silence(0.2))


def _t0_umi_samples() -> np.ndarray:
    """UC-002 型: score_umi.py の 3 フレーズへ 1:1 対応する 3 区間。"""
    parts = [_silence(0.2)]
    for _ in range(3):
        parts.append(_tone(220.0, 1.5))
        parts.append(_silence(0.4))
    parts[-1] = _silence(0.2)  # 末尾は前後無音のみ短縮
    return _concat(*parts)


def _t0_umi_samples_over_segmented() -> np.ndarray:
    """UC-002 型・過分割: 3 フレーズのはずが内部で 1 区間が割れて 4 区間検出される
    ケース（`batch1_inspection.md` の B=4 セグメント実測を模す）。真のフレーズ
    境界ギャップ（0.4s）より短いが `T0_MIN_GAP_SEC`（0.3s）以上のブレス性の
    内部ギャップ（0.32s）で 2 番目のフレーズ相当区間を 2 分割する — この
    ギャップが 4 区間中最小になるため `_reconcile_segment_count` の
    「ギャップ最小の隣接ペアを結合」規則で 3 区間へ戻ることを検証する。"""
    parts = [
        _silence(0.2),
        _tone(220.0, 1.5), _silence(0.4),
        _tone(220.0, 0.7), _silence(0.32), _tone(220.0, 0.7),  # 内部ギャップ 0.32s (>= 0.3s だが最小)
        _silence(0.4),
        _tone(220.0, 1.5),
        _silence(0.2),
    ]
    return _concat(*parts)


def _t0_sakura_samples() -> np.ndarray:
    """UC-001 型: score.py の 6 フレーズへ 1:1 対応する 6 区間。"""
    parts = [_silence(0.2)]
    for _ in range(6):
        parts.append(_tone(196.0, 0.8))
        parts.append(_silence(0.4))
    parts[-1] = _silence(0.2)
    return _concat(*parts)


def _t2_uc012_samples() -> np.ndarray:
    """UC-012 型: 「みわたすかぎり ひかりかがやく」2 フレーズへ 1:1 対応する 2 区間。"""
    parts = [_silence(0.2), _tone(174.61, 1.4), _silence(0.3), _tone(196.00, 1.4), _silence(0.2)]
    return _concat(*parts)


def _t2_uc012_samples_over_segmented(n_extra_splits: int = 4) -> np.ndarray:
    """UC-012 型・過分割: 2 フレーズのはずが各フレーズ内部が短いギャップで
    余分に割れ、`n_extra_splits` 個の追加ランが生じるケース（実測 UC-012:
    2 フレーズ期待に対し 6 ラン検出、を模す）。すべての内部ギャップは
    `T2_MIN_GAP_SEC`（0.1s）以上・フレーズ間ギャップ（0.3s）未満に収める。"""
    def _split_tone(total_sec: float, n_splits: int, freq: float) -> List[np.ndarray]:
        piece = total_sec / (n_splits + 1)
        parts: List[np.ndarray] = []
        for i in range(n_splits + 1):
            parts.append(_tone(freq, piece))
            if i < n_splits:
                parts.append(_silence(0.12))
        return parts

    n1 = n_extra_splits // 2
    n2 = n_extra_splits - n1
    parts = [_silence(0.2)]
    parts += _split_tone(1.4, n1, 174.61)
    parts.append(_silence(0.3))
    parts += _split_tone(1.4, n2, 196.00)
    parts.append(_silence(0.2))
    return _concat(*parts)


# ---------------------------------------------------------------------------
# 0.5 P2 修正 (review #265 R7): 台帳 schema の完全一致検査
# （`recording_kit/intake.py` `load_ledger` と同型の fail-closed）
# ---------------------------------------------------------------------------


def test_load_ledger_rejects_wrong_schema_value(tmp_path: Path) -> None:
    ledger_path = tmp_path / "user_donor_ledger.json"
    ledger_path.write_text(
        json.dumps({"schema": "user-donor-ledger/0.2", "entries": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(cu.LedgerSchemaError, match="schema"):
        cu.load_ledger(ledger_path)


def test_load_ledger_rejects_missing_schema_field(tmp_path: Path) -> None:
    ledger_path = tmp_path / "user_donor_ledger.json"
    ledger_path.write_text(json.dumps({"entries": []}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(cu.LedgerSchemaError):
        cu.load_ledger(ledger_path)


def test_load_ledger_accepts_exact_expected_schema(tmp_path: Path) -> None:
    ledger_path = tmp_path / "user_donor_ledger.json"
    ledger_path.write_text(
        json.dumps({"schema": cu.LEDGER_SCHEMA, "entries": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert cu.load_ledger(ledger_path) == []


def test_convert_rejects_ledger_with_wrong_schema_without_publishing(tmp_path: Path) -> None:
    """`convert()` エンドツーエンドでも schema 不一致が fail-closed で拒否され、
    `out_dir` へは一切公開されない。"""
    normalized_dir = tmp_path / "normalized"
    normalized_dir.mkdir()
    ledger_path = tmp_path / "user_donor_ledger.json"
    ledger_path.write_text(
        json.dumps({"schema": "not-the-right-schema/9.9", "entries": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    dsdict_path = _write_test_dsdict(tmp_path)
    out_dir = tmp_path / "out"

    with pytest.raises(cu.LedgerSchemaError):
        cu.convert(normalized_dir, ledger_path, out_dir, dsdict_path)
    assert not out_dir.exists()


# ---------------------------------------------------------------------------
# 1. 台帳突合: fail-closed（欠落・sha256 不一致・余剰）
# ---------------------------------------------------------------------------


def test_reconcile_ledger_success(tmp_path: Path) -> None:
    ledger_path, normalized_dir = _write_ledger_and_normalized(
        tmp_path, {"UC-012": _t2_uc012_samples()}
    )
    entries = cu.load_ledger(ledger_path)
    by_card = cu.reconcile_ledger(normalized_dir, entries)
    assert set(by_card) == {"UC-012"}


def test_reconcile_ledger_missing_file_fails_closed(tmp_path: Path) -> None:
    ledger_path, normalized_dir = _write_ledger_and_normalized(
        tmp_path, {"UC-012": _t2_uc012_samples()}
    )
    entries = cu.load_ledger(ledger_path)
    # 台帳が指すファイルを消す。
    (normalized_dir / "UC-012_test.norm24k.wav").unlink()
    with pytest.raises(cu.LedgerMismatchError, match="missing"):
        cu.reconcile_ledger(normalized_dir, entries)


def test_reconcile_ledger_sha256_mismatch_fails_closed(tmp_path: Path) -> None:
    ledger_path, normalized_dir = _write_ledger_and_normalized(
        tmp_path, {"UC-012": _t2_uc012_samples()}
    )
    entries = cu.load_ledger(ledger_path)
    # ファイルを書き換えて sha256 を不一致にする。
    _write_wav(normalized_dir / "UC-012_test.norm24k.wav", _t2_uc012_samples() * 0.5)
    with pytest.raises(cu.LedgerMismatchError, match="sha256 mismatch"):
        cu.reconcile_ledger(normalized_dir, entries)


def test_reconcile_ledger_extra_wav_fails_closed(tmp_path: Path) -> None:
    ledger_path, normalized_dir = _write_ledger_and_normalized(
        tmp_path, {"UC-012": _t2_uc012_samples()}
    )
    entries = cu.load_ledger(ledger_path)
    _write_wav(normalized_dir / "UC-999_unexpected.norm24k.wav", _t2_uc012_samples())
    with pytest.raises(cu.LedgerMismatchError, match="extra wav"):
        cu.reconcile_ledger(normalized_dir, entries)


def test_reconcile_ledger_collects_multiple_violations(tmp_path: Path) -> None:
    """欠落 + 余剰の両方を同時に発生させ、1 回の例外メッセージへ両方が
    含まれる（全収集してから fail-closed）ことを確認する。"""
    ledger_path, normalized_dir = _write_ledger_and_normalized(
        tmp_path, {"UC-012": _t2_uc012_samples(), "UC-013": _t2_uc012_samples()}
    )
    entries = cu.load_ledger(ledger_path)
    (normalized_dir / "UC-012_test.norm24k.wav").unlink()
    _write_wav(normalized_dir / "UC-999_unexpected.norm24k.wav", _t2_uc012_samples())
    with pytest.raises(cu.LedgerMismatchError) as excinfo:
        cu.reconcile_ledger(normalized_dir, entries)
    msg = str(excinfo.value)
    assert "UC-012" in msg and "missing" in msg
    assert "extra wav" in msg and "UC-999" in msg


# ---------------------------------------------------------------------------
# 1.4 P1 修正 (review #265 R7): 単一 read 束縛（sha256 照合・音声解析・resample
# が同じスナップショットのみを読む・TOCTOU 対策）
# ---------------------------------------------------------------------------


def test_reconcile_ledger_with_snapshot_dir_points_entry_at_snapshot(tmp_path: Path) -> None:
    """`snapshot_dir` を指定すると `LedgerEntry.path` は `normalized_dir` 原本
    ではなく `snapshot_dir` 配下のコピーを指し、その内容は原本と一致する。"""
    ledger_path, normalized_dir = _write_ledger_and_normalized(
        tmp_path, {"UC-012": _t2_uc012_samples()}
    )
    entries = cu.load_ledger(ledger_path)
    snapshot_dir = tmp_path / "snapshot"

    by_card = cu.reconcile_ledger(normalized_dir, entries, snapshot_dir=snapshot_dir)

    entry = by_card["UC-012"]
    assert entry.path.parent == snapshot_dir
    assert entry.path != normalized_dir / "UC-012_test.norm24k.wav"
    assert entry.path.read_bytes() == (normalized_dir / "UC-012_test.norm24k.wav").read_bytes()


def test_reconcile_ledger_snapshot_dir_none_keeps_original_path(tmp_path: Path) -> None:
    """`snapshot_dir` 省略時（`None`、既定）は従来どおり `normalized_dir` の
    原本パスを指す（本関数単体テストの後方互換）。"""
    ledger_path, normalized_dir = _write_ledger_and_normalized(
        tmp_path, {"UC-012": _t2_uc012_samples()}
    )
    entries = cu.load_ledger(ledger_path)

    by_card = cu.reconcile_ledger(normalized_dir, entries)

    assert by_card["UC-012"].path == normalized_dir / "UC-012_test.norm24k.wav"


@_ffmpeg_required
def test_convert_wav_paths_point_to_snapshot_not_normalized_dir(tmp_path: Path, monkeypatch) -> None:
    """`convert()` エンドツーエンドで、実際に resample へ渡される wav パス
    （公開された wav の生成元）は `.snapshot-<pid>` 配下であり、
    `normalized_dir` 直下ではない。"""
    cards = {"UC-012": _t2_uc012_samples()}
    ledger_path, normalized_dir = _write_ledger_and_normalized(tmp_path, cards)
    dsdict_path = _write_test_dsdict(tmp_path)
    out_dir = tmp_path / "out"

    captured_srcs = []
    real_resample = cu.convert_d3.resample_to_44k1

    def _capture(src, dst, ffmpeg_bin=None):
        captured_srcs.append(Path(src))
        return real_resample(src, dst, ffmpeg_bin=ffmpeg_bin)

    monkeypatch.setattr(cu.convert_d3, "resample_to_44k1", _capture)

    cu.convert(normalized_dir, ledger_path, out_dir, dsdict_path)

    assert len(captured_srcs) == 1
    assert captured_srcs[0].parent != normalized_dir
    assert ".snapshot-" in captured_srcs[0].parent.name


@_ffmpeg_required
def test_convert_publishes_snapshot_bytes_even_if_normalized_dir_wav_mutated_after_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    """P1 修正 (review #265 R7) の核心シナリオ: 音声解析（snapshot 取得後）の
    タイミングで `normalized_dir` 原本の wav が別内容へ差し替わっても、実際に
    公開される wav バイト列は snapshot 取得時点のまま変化しない（旧実装は
    sha256 照合・解析・resample が別々に `normalized_dir` 原本を open して
    いたため、この間の差し替えが公開バイト列に反映されうる TOCTOU だった）。"""
    cards = {"UC-012": _t2_uc012_samples()}
    ledger_path, normalized_dir = _write_ledger_and_normalized(tmp_path, cards)
    dsdict_path = _write_test_dsdict(tmp_path)
    out_dir = tmp_path / "out"

    real_load_mono = cu._load_mono
    mutated = {"done": False}

    def _mutate_normalized_dir_then_load(path):
        # この時点で `path` は既にスナップショットのはず（normalized_dir
        # 原本ではない）。副作用として原本を別内容（長さの異なる無音 wav）
        # へ差し替える。
        if not mutated["done"]:
            _write_wav(normalized_dir / "UC-012_test.norm24k.wav", _silence(9.0))
            mutated["done"] = True
        return real_load_mono(path)

    monkeypatch.setattr(cu, "_load_mono", _mutate_normalized_dir_then_load)

    summary = cu.convert(normalized_dir, ledger_path, out_dir, dsdict_path)
    assert mutated["done"]
    assert summary["included_cards"] == ["UC-012"]

    published_bytes = (out_dir / "wavs" / "UC-012.wav").read_bytes()

    direct_from_mutated = tmp_path / "direct_from_mutated.wav"
    cu.convert_d3.resample_to_44k1(normalized_dir / "UC-012_test.norm24k.wav", direct_from_mutated)
    assert published_bytes != direct_from_mutated.read_bytes()


# ---------------------------------------------------------------------------
# 1.5 P2 修正 (review #265): card_id 重複を黙殺上書きせず fail-closed する
# ---------------------------------------------------------------------------


def test_reconcile_ledger_duplicate_card_id_fails_closed(tmp_path: Path) -> None:
    """再録 take の積み立て運用では複数の台帳エントリが同一 card_id を指す
    ことが正常系としてあり得るが、`reconcile_ledger` は黙って後勝ちで
    上書きせず reconciliation エラーとして検出する（旧実装は
    `by_card[str(card_id)] = ...` の後勝ち代入で先の take を黙殺していた）。"""
    normalized_dir = tmp_path / "normalized"
    normalized_dir.mkdir()
    samples = _t2_uc012_samples()
    take1 = normalized_dir / "UC-012_take1.norm24k.wav"
    take2 = normalized_dir / "UC-012_take2.norm24k.wav"
    _write_wav(take1, samples)
    _write_wav(take2, samples * 0.5)
    entries = [
        {
            "card_id": "UC-012", "normalized_path": f"user_donor_normalized/{take1.name}",
            "sha256": _sha256(take1),
        },
        {
            "card_id": "UC-012", "normalized_path": f"user_donor_normalized/{take2.name}",
            "sha256": _sha256(take2),
        },
    ]
    with pytest.raises(cu.LedgerMismatchError, match="duplicate") as excinfo:
        cu.reconcile_ledger(normalized_dir, entries)
    msg = str(excinfo.value)
    assert "UC-012" in msg
    assert take1.name in msg and take2.name in msg


def test_convert_duplicate_card_id_ledger_publishes_nothing(tmp_path: Path) -> None:
    """`convert()` エンドツーエンドでも card_id 重複が fail-closed で拒否され、
    `out_dir` へは一切公開されない。"""
    normalized_dir = tmp_path / "normalized"
    normalized_dir.mkdir()
    samples = _t2_uc012_samples()
    take1 = normalized_dir / "UC-012_take1.norm24k.wav"
    take2 = normalized_dir / "UC-012_take2.norm24k.wav"
    _write_wav(take1, samples)
    _write_wav(take2, samples)
    entries = [
        {
            "card_id": "UC-012", "normalized_path": f"user_donor_normalized/{take1.name}",
            "sha256": _sha256(take1),
        },
        {
            "card_id": "UC-012", "normalized_path": f"user_donor_normalized/{take2.name}",
            "sha256": _sha256(take2),
        },
    ]
    ledger_path = tmp_path / "user_donor_ledger.json"
    ledger_path.write_text(json.dumps(_make_ledger(entries), ensure_ascii=False), encoding="utf-8")
    dsdict_path = _write_test_dsdict(tmp_path)
    out_dir = tmp_path / "out"

    with pytest.raises(cu.LedgerMismatchError, match="duplicate"):
        cu.convert(normalized_dir, ledger_path, out_dir, dsdict_path)
    assert not out_dir.exists()


# ---------------------------------------------------------------------------
# 1.5 P2 修正 (review #265 R13): normalized_path / sha256 の重複追跡
# （card_id 重複検査だけでは見逃す、異なる card_id 2 枚が同一録音を指すケース）
# ---------------------------------------------------------------------------


def test_reconcile_ledger_duplicate_normalized_path_fails_closed(tmp_path: Path) -> None:
    """核心シナリオ (review #265 R13): 異なる card_id 2 枚が同一
    `normalized_path`（+同一 sha256）を参照すると、card_id 重複検査は素通し
    するが（card_id 自体は異なるため）、`reconcile_ledger` は同一録音が 2
    カード分として重複投入されるのを fail-closed で拒否する。"""
    normalized_dir = tmp_path / "normalized"
    normalized_dir.mkdir()
    samples = _t2_uc012_samples()
    shared_wav = normalized_dir / "shared_recording.norm24k.wav"
    _write_wav(shared_wav, samples)
    entries = [
        {
            "card_id": "UC-012", "normalized_path": f"user_donor_normalized/{shared_wav.name}",
            "sha256": _sha256(shared_wav),
        },
        {
            "card_id": "UC-013", "normalized_path": f"user_donor_normalized/{shared_wav.name}",
            "sha256": _sha256(shared_wav),
        },
    ]
    with pytest.raises(cu.LedgerMismatchError, match="normalized_path") as excinfo:
        cu.reconcile_ledger(normalized_dir, entries)
    msg = str(excinfo.value)
    assert "UC-012" in msg and "UC-013" in msg
    assert shared_wav.name in msg


def test_convert_duplicate_normalized_path_ledger_publishes_nothing(tmp_path: Path) -> None:
    """`convert()` エンドツーエンドでも normalized_path 重複が fail-closed で
    拒否され、`out_dir` へは一切公開されない。"""
    normalized_dir = tmp_path / "normalized"
    normalized_dir.mkdir()
    samples = _t2_uc012_samples()
    shared_wav = normalized_dir / "shared_recording.norm24k.wav"
    _write_wav(shared_wav, samples)
    entries = [
        {
            "card_id": "UC-012", "normalized_path": f"user_donor_normalized/{shared_wav.name}",
            "sha256": _sha256(shared_wav),
        },
        {
            "card_id": "UC-013", "normalized_path": f"user_donor_normalized/{shared_wav.name}",
            "sha256": _sha256(shared_wav),
        },
    ]
    ledger_path = tmp_path / "user_donor_ledger.json"
    ledger_path.write_text(json.dumps(_make_ledger(entries), ensure_ascii=False), encoding="utf-8")
    dsdict_path = _write_test_dsdict(tmp_path)
    out_dir = tmp_path / "out"

    with pytest.raises(cu.LedgerMismatchError, match="normalized_path"):
        cu.convert(normalized_dir, ledger_path, out_dir, dsdict_path)
    assert not out_dir.exists()


def test_reconcile_ledger_duplicate_sha256_under_different_filenames_fails_closed(
    tmp_path: Path,
) -> None:
    """異なる card_id 2 枚が異なる `normalized_path`（別ファイル名）だが同一
    sha256（同一バイト列 = 物理的に同一録音）を参照する場合も fail-closed で
    拒否する（同一バイトの別ファイル名も物理的に同一録音という docstring の
    規約）。"""
    normalized_dir = tmp_path / "normalized"
    normalized_dir.mkdir()
    samples = _t2_uc012_samples()
    wav_a = normalized_dir / "UC-012_a.norm24k.wav"
    wav_b = normalized_dir / "UC-013_b.norm24k.wav"
    _write_wav(wav_a, samples)
    _write_wav(wav_b, samples)  # byte-identical to wav_a but a different filename
    assert wav_a.read_bytes() == wav_b.read_bytes()
    entries = [
        {
            "card_id": "UC-012", "normalized_path": f"user_donor_normalized/{wav_a.name}",
            "sha256": _sha256(wav_a),
        },
        {
            "card_id": "UC-013", "normalized_path": f"user_donor_normalized/{wav_b.name}",
            "sha256": _sha256(wav_b),
        },
    ]
    with pytest.raises(cu.LedgerMismatchError, match="sha256") as excinfo:
        cu.reconcile_ledger(normalized_dir, entries)
    msg = str(excinfo.value)
    assert "UC-012" in msg and "UC-013" in msg


def test_reconcile_ledger_distinct_recordings_do_not_trigger_false_positive(
    tmp_path: Path,
) -> None:
    """通常系の回帰防止: 異なる card_id が genuinely 異なる録音（別
    normalized_path・別 sha256）を参照する場合は、新しい重複検査に一切
    抵触しない。"""
    normalized_dir = tmp_path / "normalized"
    normalized_dir.mkdir()
    wav_a = normalized_dir / "UC-012_a.norm24k.wav"
    wav_b = normalized_dir / "UC-013_b.norm24k.wav"
    _write_wav(wav_a, _t2_uc012_samples())
    _write_wav(wav_b, _t0_umi_samples())
    entries = [
        {
            "card_id": "UC-012", "normalized_path": f"user_donor_normalized/{wav_a.name}",
            "sha256": _sha256(wav_a),
        },
        {
            "card_id": "UC-013", "normalized_path": f"user_donor_normalized/{wav_b.name}",
            "sha256": _sha256(wav_b),
        },
    ]
    by_card = cu.reconcile_ledger(normalized_dir, entries)
    assert set(by_card) == {"UC-012", "UC-013"}


# ---------------------------------------------------------------------------
# 1.6 P1 修正 (review #265): 衝突検査を convert() 自身が行う（CLI 経由でなくても
# fail-closed）
# ---------------------------------------------------------------------------


def test_convert_rejects_out_dir_colliding_with_normalized_dir_without_cli(tmp_path: Path) -> None:
    """CLI `main()` を経由しない直接呼び出しでも `--out-dir` が
    `normalized_dir` と衝突していれば `convert()` 自身が fail-closed で
    拒否する（review #265 P1）。"""
    ledger_path, normalized_dir = _write_ledger_and_normalized(
        tmp_path, {"UC-012": _t2_uc012_samples()}
    )
    dsdict_path = _write_test_dsdict(tmp_path)

    with pytest.raises(cu.convert_d3.OutputCollisionError):
        cu.convert(normalized_dir, ledger_path, normalized_dir, dsdict_path)


def test_convert_rejects_out_dir_equal_to_ledger_file_without_cli(tmp_path: Path) -> None:
    """`--out-dir` が `--ledger` ファイル自身と一致する場合も fail-closed で
    拒否する（`protected_files` の完全一致検査）。"""
    ledger_path, normalized_dir = _write_ledger_and_normalized(
        tmp_path, {"UC-012": _t2_uc012_samples()}
    )
    dsdict_path = _write_test_dsdict(tmp_path)

    with pytest.raises(cu.convert_d3.OutputCollisionError):
        cu.convert(normalized_dir, ledger_path, ledger_path, dsdict_path)


@_ffmpeg_required
def test_convert_allows_out_dir_sibling_of_ledger_file(tmp_path: Path) -> None:
    """`--out-dir` が `--ledger` ファイルと同じ親ディレクトリの兄弟パスに
    あるだけなら誤検知しない（`protected_files` は完全一致・祖先包含のみを
    保護し、`ledger.parent` 全体（＝兄弟パス全部）は保護しない設計。既存
    テスト fixture がまさにこの形 — `ledger_path`/`out_dir` を共通の
    `tmp_path` 直下に置く — であり、もし誤検知していれば全 convert()
    エンドツーエンドテストが壊れる）。"""
    ledger_path, normalized_dir = _write_ledger_and_normalized(
        tmp_path, {"UC-012": _t2_uc012_samples()}
    )
    dsdict_path = _write_test_dsdict(tmp_path)
    out_dir = tmp_path / "out_sibling"  # ledger_path と同じ tmp_path 直下

    summary = cu.convert(normalized_dir, ledger_path, out_dir, dsdict_path)
    assert summary["included_cards"] == ["UC-012"]


def test_convert_rejects_out_dir_that_is_ancestor_of_ledger_file_without_cli(tmp_path: Path) -> None:
    """P1 修正 (review #265 R3): `--out-dir` が `--ledger` ファイルの**祖先
    ディレクトリ**の場合も fail-closed で拒否する（完全一致だけでなく双方向
    の包含判定）。`normalized_dir` とは無関係な別ディレクトリへ台帳を置き、
    `protected_roots`（`normalized_dir`）側の既存検査ではなく
    `protected_files`（`ledger_path`）側の新規検査で拒否されることを分離
    して確認する。"""
    normalized_dir = tmp_path / "normalized"
    normalized_dir.mkdir()
    samples = _t2_uc012_samples()
    wav_path = normalized_dir / "UC-012_test.norm24k.wav"
    _write_wav(wav_path, samples)
    ledger_dir = tmp_path / "ledger_home"  # normalized_dir とは無関係な別ディレクトリ
    ledger_dir.mkdir()
    ledger_path = ledger_dir / "user_donor_ledger.json"
    entries = [{
        "card_id": "UC-012", "source_filename": "UC-012_test.mp3",
        "source_sha256": "0" * 64, "source_size_bytes": 1,
        "normalized_path": f"user_donor_normalized/{wav_path.name}",
        "sha256": _sha256(wav_path), "received_at": "2026-08-17T00:00:00Z",
        "duration_sec": round(len(samples) / SR, 3), "sample_rate": SR,
        "rms_dbfs": -18.0, "peak_dbfs": -3.0, "alignment_status": "not_started",
    }]
    ledger_path.write_text(json.dumps(_make_ledger(entries), ensure_ascii=False), encoding="utf-8")
    dsdict_path = _write_test_dsdict(tmp_path)

    with pytest.raises(cu.convert_d3.OutputCollisionError):
        cu.convert(normalized_dir, ledger_path, ledger_dir, dsdict_path)  # out_dir = ledger の親


def test_convert_rejects_out_dir_equal_to_dsdict_path_without_cli(tmp_path: Path) -> None:
    """P1 修正 (review #265 R3): `--out-dir` が `--dsdict` ファイル自身と
    一致する場合も fail-closed で拒否する（旧実装は `dsdict_path` を保護
    入力集合に含めておらず、この衝突を検出できなかった）。"""
    ledger_path, normalized_dir = _write_ledger_and_normalized(
        tmp_path, {"UC-012": _t2_uc012_samples()}
    )
    dsdict_path = _write_test_dsdict(tmp_path)

    with pytest.raises(cu.convert_d3.OutputCollisionError):
        cu.convert(normalized_dir, ledger_path, dsdict_path, dsdict_path)


def test_convert_rejects_out_dir_that_is_ancestor_of_dsdict_path_without_cli(tmp_path: Path) -> None:
    """`--out-dir` が `--dsdict` ファイルの祖先ディレクトリの場合も
    fail-closed で拒否する。"""
    ledger_path, normalized_dir = _write_ledger_and_normalized(
        tmp_path, {"UC-012": _t2_uc012_samples()}
    )
    dsdict_dir = tmp_path / "dsdict_home"  # ledger/normalized とは無関係な別ディレクトリ
    dsdict_dir.mkdir()
    dsdict_path = _write_test_dsdict(dsdict_dir)

    with pytest.raises(cu.convert_d3.OutputCollisionError):
        cu.convert(normalized_dir, ledger_path, dsdict_dir, dsdict_path)  # out_dir = dsdict の親


# ---------------------------------------------------------------------------
# 2. dsdict.yaml ロード + グラフェム最長一致トークン化（C2）
# ---------------------------------------------------------------------------


def test_load_dsdict_builds_table_and_sha256(tmp_path: Path) -> None:
    path = _write_test_dsdict(tmp_path)
    dsdict = cu.load_dsdict(path)
    assert dsdict.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert dsdict.table["か"] == ["k", "a"]
    assert dsdict.table["っ"] == ["cl"]
    assert len(dsdict.table) == len(_TEST_DSDICT_ENTRIES)


def test_load_dsdict_missing_entries_key_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump({"not_entries": []}), encoding="utf-8")
    with pytest.raises(cu.DsDictError, match="entries"):
        cu.load_dsdict(path)


def test_tokenize_sokuon_maps_to_cl(tmp_path: Path) -> None:
    dsdict = cu.load_dsdict(_write_test_dsdict(tmp_path))
    toks = cu.tokenize_with_dsdict(dsdict, "ぱっと")
    assert toks == [("ぱ", ["p", "a"]), ("っ", ["cl"]), ("と", ["t", "o"])]


def test_tokenize_yoon_longest_match_prefers_2char_grapheme(tmp_path: Path) -> None:
    """拗音「きゃ」は 2 文字グラフェムとして 1 トークンで拾われ、「き」+「ゃ」の
    ような 1 文字分割にはならない（最長一致の確認）。"""
    dsdict = cu.load_dsdict(_write_test_dsdict(tmp_path))
    toks = cu.tokenize_with_dsdict(dsdict, "きゃく")
    assert toks[0] == ("きゃ", ["ky", "a"])
    assert toks[1] == ("く", ["k", "u"])


def test_tokenize_gairaigo_katakana_two_char_grapheme(tmp_path: Path) -> None:
    """外来語行（ティ等）はカタカナ 2 文字グラフェムとして拾われ、大文字母音
    （`I`）を持つ（`phoneme_jp.py` では先頭の「テ」だけで未対応判定になり
    C1 では拾えなかった文字種）。"""
    dsdict = cu.load_dsdict(_write_test_dsdict(tmp_path))
    toks = cu.tokenize_with_dsdict(dsdict, "ティーカップ")
    assert toks[0] == ("ティ", ["t", "I"])
    assert toks[1] == ("ー", [])  # 長音記号は音素を持たない独立トークン
    assert toks[2] == ("カ", ["k", "A"])
    assert toks[3] == ("っ", ["cl"])  # カタカナ「ッ」->ひらがな「っ」正規化
    assert toks[4] == ("プ", ["p", "U"])


def test_tokenize_katakana_moraic_nasal_normalizes_to_hiragana(tmp_path: Path) -> None:
    dsdict = cu.load_dsdict(_write_test_dsdict(tmp_path))
    toks = cu.tokenize_with_dsdict(dsdict, "ファンファーレ")
    kana_seq = [g for g, _ in toks]
    assert kana_seq == ["ファ", "ん", "ファ", "ー", "レ"]  # カタカナ「ン」->ひらがな「ん」正規化


def test_tokenize_vu_grapheme_absent_raises_and_no_substitution(tmp_path: Path) -> None:
    """ヴ系グラフェムは辞書に実在しないため `DsDictTokenizeError` を送出する
    （代用マッピング禁止 — UC-010 が除外され続ける根拠）。"""
    dsdict = cu.load_dsdict(_write_test_dsdict(tmp_path))
    with pytest.raises(cu.DsDictTokenizeError, match="'ヴ'"):
        cu.tokenize_with_dsdict(dsdict, "ヴァイオリン")


def test_weighted_tokens_sokuon_has_smaller_weight_than_normal(tmp_path: Path) -> None:
    dsdict = cu.load_dsdict(_write_test_dsdict(tmp_path))
    weighted = cu.weighted_tokens_for_phrase(dsdict, "ぱっと")
    phonemes_weights = {tuple(p): w for p, w in weighted}
    assert phonemes_weights[("cl",)] == cu.SOKUON_WEIGHT
    assert phonemes_weights[("p", "a")] == 1.0
    assert cu.SOKUON_WEIGHT < 1.0


def test_weighted_tokens_choon_extends_previous_token_weight(tmp_path: Path) -> None:
    dsdict = cu.load_dsdict(_write_test_dsdict(tmp_path))
    weighted = cu.weighted_tokens_for_phrase(dsdict, "ティーカップ")
    # 「ティ」の直後の「ー」は独立ノートを持たず、「ティ」の重みへ加算される。
    assert weighted[0][0] == ["t", "I"]
    assert weighted[0][1] == pytest.approx(1.0 + cu.CHOON_EXTRA_WEIGHT)
    assert len(weighted) == 4  # ティ(+ー) / カ / っ / プ = 4 ノート（ー は独立ノートなし）


@pytest.mark.parametrize(
    "card_id",
    ["UC-008", "UC-009", "UC-011", "UC-012", "UC-013", "UC-014", "UC-015", "UC-016", "UC-017"],
)
def test_t2_recovery_cards_all_tokenize_with_dsdict(tmp_path: Path, card_id: str) -> None:
    """C2 復旧対象 9 枚は全て dsdict でトークン化できる（代用マッピングなし）。"""
    dsdict = cu.load_dsdict(_write_test_dsdict(tmp_path))
    tokens, err = cu._phrase_tokens_dsdict(card_id, cu.T2_PHRASES[card_id], dsdict)
    assert err is None, err
    assert tokens is not None and sum(len(t) for t in tokens) > 0


def test_t2_uc010_excluded_dsdict_reason(tmp_path: Path) -> None:
    dsdict = cu.load_dsdict(_write_test_dsdict(tmp_path))
    tokens, err = cu._phrase_tokens_dsdict("UC-010", cu.T2_PHRASES["UC-010"], dsdict)
    assert tokens is None
    assert err is not None and "unmapped grapheme in dsdict" in err and "'ヴ'" in err


# ---------------------------------------------------------------------------
# 3. T2 併合規則（C2: ラン数 > フレーズ数のみ、数が合うまで最小ギャップ併合）
# ---------------------------------------------------------------------------


def test_reconcile_t2_segment_count_merges_down_to_expected() -> None:
    # 6 ラン -> 期待 2 (実測 UC-012 の傾向を模した合成ケース)。
    segs = [(0.0, 0.5), (0.6, 1.0), (1.1, 1.4), (2.0, 2.4), (2.5, 2.9), (3.0, 3.5)]
    out, err = cu._reconcile_t2_segment_count(segs, 2)
    assert err is None
    assert out is not None and len(out) == 2
    assert out[0] == (0.0, 1.4)  # 最初の 3 ラン (ギャップ 0.1/0.1) が併合される
    assert out[1] == (2.0, 3.5)  # 後半 3 ラン (ギャップ 0.1/0.1) が併合される


def test_reconcile_t2_segment_count_too_few_runs_fails_closed() -> None:
    segs = [(0.0, 1.0)]
    out, err = cu._reconcile_t2_segment_count(segs, 2)
    assert out is None
    assert err is not None and "got 1" in err


def test_reconcile_t2_segment_count_exact_match_is_noop() -> None:
    segs = [(0.0, 1.0), (1.5, 2.5)]
    out, err = cu._reconcile_t2_segment_count(segs, 2)
    assert err is None
    assert out == segs


# ---------------------------------------------------------------------------
# 4. T0/T1/T2 セグメンテーション・アラインメント単体
# ---------------------------------------------------------------------------


def test_t1_process_card_detects_3_segments_and_builds_row() -> None:
    samples = _t1_card_samples()
    row, err = cu._process_t1_card("UC-004", samples, SR)
    assert err is None, err
    assert row is not None
    assert row["ph_seq"].split().count("i") == 3  # UC-004 = い、SP を除き 3 音素
    assert row["note_seq"].count("rest") >= 2  # 段間ギャップ
    ph_total = sum(float(x) for x in row["ph_dur"].split())
    assert ph_total == pytest.approx(len(samples) / SR, abs=1e-6)


def test_t1_process_card_wrong_segment_count_fails_closed() -> None:
    samples = _t1_card_samples_only_2_segments()
    row, err = cu._process_t1_card("UC-004", samples, SR)
    assert row is None
    assert err is not None and "found 2" in err


def test_t0_umi_process_card_builds_row_from_score_phrases() -> None:
    samples = _t0_umi_samples()
    row, err = cu._process_t0_card("UC-002", samples, SR, cu.build_umi_score)
    assert err is None, err
    assert row is not None
    # score_umi.py は 3 フレーズ・計 3+4+5=12 モーラ (各1音素、onset無しモーラのみ)
    # プラス onset 有りモーラ分の音素追加を考慮して note 数で検証する。
    n_notes = len([t for t in row["note_seq"].split() if t != "rest"])
    assert n_notes == 3 + 4 + 5
    ph_total = sum(float(x) for x in row["ph_dur"].split())
    assert ph_total == pytest.approx(len(samples) / SR, abs=1e-6)


def test_t0_umi_process_card_reconciles_plus_one_oversegmentation() -> None:
    """`batch1_inspection.md` の B=4 セグメント実測（score は 3 フレーズ）を模す:
    内部ギャップが `T0_MIN_GAP_SEC` 未満で割れた 4 区間が 3 区間へ結合され、
    フレーズ対応が成立することを確認する。"""
    samples = _t0_umi_samples_over_segmented()
    segs = cu._segments_by_gap(
        samples, SR, silence_db=cu.SILENCE_DB, frame_sec=cu.FRAME_SEC,
        min_gap_sec=cu.T0_MIN_GAP_SEC, min_run_sec=0.0,
    )
    assert len(segs) == 4
    row, err = cu._process_t0_card("UC-002", samples, SR, cu.build_umi_score)
    assert err is None, err
    assert row is not None


def test_reconcile_segment_count_off_by_two_fails_closed() -> None:
    segs = [(0.0, 1.0), (1.5, 2.5), (3.0, 4.0), (4.5, 5.5), (6.0, 7.0)]
    out, err = cu._reconcile_segment_count(segs, 3)
    assert out is None
    assert err is not None and "expected 3" in err and "got 5" in err


def test_t0_sakura_process_card_builds_6_phrase_row() -> None:
    samples = _t0_sakura_samples()
    row, err = cu._process_t0_card("UC-001", samples, SR, cu.build_sakura_score)
    assert err is None, err
    assert row is not None
    n_notes = len([t for t in row["note_seq"].split() if t != "rest"])
    assert n_notes == 3 + 3 + 4 + 3 + 4 + 3  # score.py の 6 フレーズのモーラ数合計
    ph_total = sum(float(x) for x in row["ph_dur"].split())
    assert ph_total == pytest.approx(len(samples) / SR, abs=1e-6)


def test_t2_process_card_uc012_builds_row(tmp_path: Path) -> None:
    dsdict = cu.load_dsdict(_write_test_dsdict(tmp_path))
    samples = _t2_uc012_samples()
    row, err = cu._process_t2_card("UC-012", samples, SR, dsdict)
    assert err is None, err
    assert row is not None
    n_notes = len([t for t in row["note_seq"].split() if t != "rest"])
    assert n_notes == 14  # 「みわたすかぎり」7 + 「ひかりかがやく」7
    ph_total = sum(float(x) for x in row["ph_dur"].split())
    assert ph_total == pytest.approx(len(samples) / SR, abs=1e-6)


def test_t2_process_card_uc012_over_segmented_merges_via_reconcile(tmp_path: Path) -> None:
    """実測（UC-012: 期待 2 フレーズに対し 6 ラン検出）を模した過分割音声でも、
    併合規則（C2）で 2 ランへ吸収されて行が構築できることを確認する。"""
    dsdict = cu.load_dsdict(_write_test_dsdict(tmp_path))
    samples = _t2_uc012_samples_over_segmented()
    row, err = cu._process_t2_card("UC-012", samples, SR, dsdict)
    assert err is None, err
    assert row is not None
    n_notes = len([t for t in row["note_seq"].split() if t != "rest"])
    assert n_notes == 14
    ph_total = sum(float(x) for x in row["ph_dur"].split())
    assert ph_total == pytest.approx(len(samples) / SR, abs=1e-6)


# ---------------------------------------------------------------------------
# 5. convert() エンドツーエンド: 正常系 3 tier + 個別カード除外 + build_dataset ゲート
# ---------------------------------------------------------------------------


@_ffmpeg_required
def test_convert_end_to_end_mixed_tiers_publishes_dataset(tmp_path: Path) -> None:
    cards = {
        "UC-001": _t0_sakura_samples(),
        "UC-002": _t0_umi_samples(),
        "UC-004": _t1_card_samples(),
        "UC-005": _t1_card_samples_only_2_segments(),  # 3 段検出失敗 -> 除外される
        "UC-012": _t2_uc012_samples(),
    }
    ledger_path, normalized_dir = _write_ledger_and_normalized(tmp_path, cards)
    dsdict_path = _write_test_dsdict(tmp_path)
    out_dir = tmp_path / "out"

    summary = cu.convert(normalized_dir, ledger_path, out_dir, dsdict_path)

    assert set(summary["included_cards"]) == {"UC-001", "UC-002", "UC-004", "UC-012"}
    excluded_ids = {e["card_id"] for e in summary["excluded_cards"]}
    assert excluded_ids == {"UC-005"}
    uc005 = next(e for e in summary["excluded_cards"] if e["card_id"] == "UC-005")
    assert uc005["tier"] == "T1" and "found 2" in uc005["reason"]
    assert summary["dsdict"]["sha256"] == hashlib.sha256(dsdict_path.read_bytes()).hexdigest()

    csv_path = out_dir / "transcriptions.csv"
    assert csv_path.exists()
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv_module.DictReader(f))
    assert {r["name"] for r in rows} == {"UC-001", "UC-002", "UC-004", "UC-012"}

    for card_id in ("UC-001", "UC-002", "UC-004", "UC-012"):
        wav_path = out_dir / "wavs" / f"{card_id}.wav"
        assert wav_path.exists()
        with wave.open(str(wav_path), "rb") as w:
            assert w.getframerate() == 44100
            assert w.getsampwidth() == 2

    exclusions = json.loads((out_dir / "exclusions.json").read_text(encoding="utf-8"))
    assert exclusions["n_included"] == 4
    assert exclusions["n_excluded"] == 1
    assert exclusions["dsdict"]["sha256"] == summary["dsdict"]["sha256"]


# ---------------------------------------------------------------------------
# 5.5 P2 修正 (review #265 追加分): exclusions.json の dsdict.path はコンテナ
# 固有の絶対パスを焼き込まず basename のみにする（pin 不変性）
# ---------------------------------------------------------------------------


@_ffmpeg_required
def test_exclusions_json_dsdict_path_is_basename_only_no_absolute_path(tmp_path: Path) -> None:
    """`exclusions.json` の `dsdict.path` は `--dsdict` の basename のみを
    記録し、呼び出し環境固有の絶対パス（ディレクトリ区切り含む）を含まない
    （`run4_dataset_pins.json` の `exclusions_json_sha256` pin が実行環境に
    依存しないようにするため）。`sha256`/`n_graphemes` は辞書の実体を一意に
    識別する provenance として不変のまま残す。"""
    cards = {"UC-012": _t2_uc012_samples()}
    ledger_path, normalized_dir = _write_ledger_and_normalized(tmp_path, cards)
    # dsdict をネストしたディレクトリ配下に置き、絶対パスに複数のパス区切りが
    # 含まれる状態を作る（basename 化されていなければ確実に検出できる）。
    nested_dir = tmp_path / "nested" / "dsdict_dir"
    nested_dir.mkdir(parents=True)
    dsdict_path = _write_test_dsdict(nested_dir)
    out_dir = tmp_path / "out"

    summary = cu.convert(normalized_dir, ledger_path, out_dir, dsdict_path)

    exclusions = json.loads((out_dir / "exclusions.json").read_text(encoding="utf-8"))
    assert exclusions["dsdict"]["path"] == "dsdict.yaml"
    assert "/" not in exclusions["dsdict"]["path"]
    assert str(nested_dir) not in json.dumps(exclusions)
    # provenance として意味を持つ sha256/n_graphemes は不変。
    assert exclusions["dsdict"]["sha256"] == hashlib.sha256(dsdict_path.read_bytes()).hexdigest()
    assert exclusions["dsdict"]["sha256"] == summary["dsdict"]["sha256"]
    assert exclusions["dsdict"]["n_graphemes"] == summary["dsdict"]["n_graphemes"]


@_ffmpeg_required
def test_convert_uc010_excluded_with_dsdict_reason(tmp_path: Path) -> None:
    # UC-010 gets a trailing-silence pad so its wav bytes (and thus sha256)
    # differ from UC-012's below — otherwise the two cards would coincide on
    # the new normalized_path/sha256 duplicate-artifact fail-closed check
    # (review #265 R13). UC-010 fails at dsdict tokenize before any segment
    # analysis runs, so the extra trailing silence has no effect on the
    # assertions below.
    cards = {"UC-010": _concat(_t2_uc012_samples(), _silence(0.05)), "UC-012": _t2_uc012_samples()}
    ledger_path, normalized_dir = _write_ledger_and_normalized(tmp_path, cards)
    dsdict_path = _write_test_dsdict(tmp_path)
    out_dir = tmp_path / "out"

    summary = cu.convert(normalized_dir, ledger_path, out_dir, dsdict_path)

    assert summary["included_cards"] == ["UC-012"]
    uc010 = next(e for e in summary["excluded_cards"] if e["card_id"] == "UC-010")
    assert "ヴ" in uc010["reason"] and "dsdict" in uc010["reason"]


@_ffmpeg_required
def test_convert_output_passes_build_dataset_gates(tmp_path: Path) -> None:
    """`build_dataset.py` の 3 ゲート（validate_speaker / check_ph_dur_duration /
    check_note_dur_consistency）を User 出力データセットに対して直接呼び出す
    （build_dataset.py 自身は改変しない。read-only import、`convert_d3.py`
    のテストと同型）。"""
    import build_dataset

    cards = {
        "UC-001": _t0_sakura_samples(),
        "UC-002": _t0_umi_samples(),
        "UC-003": _t1_card_samples(freqs=(110.0, 146.83, 174.61)),
        "UC-012": _t2_uc012_samples(),
        # UC-013 gets a trailing-silence pad so its wav bytes differ from
        # UC-012's above (avoids the new normalized_path/sha256 duplicate
        # fail-closed check, review #265 R13). UC-013's phrase count (4)
        # already mismatches this 2-segment audio and gets excluded either
        # way, so the extra silence changes nothing this test asserts.
        "UC-013": _concat(_t2_uc012_samples(), _silence(0.07)),
    }
    ledger_path, normalized_dir = _write_ledger_and_normalized(tmp_path, cards)
    dsdict_path = _write_test_dsdict(tmp_path)
    out_dir = tmp_path / "out"
    cu.convert(normalized_dir, ledger_path, out_dir, dsdict_path)

    rows = build_dataset.read_transcriptions(out_dir / "transcriptions.csv")
    problems = build_dataset.validate_speaker("user", out_dir, rows)
    problems += build_dataset.check_ph_dur_duration("user", out_dir / "wavs", rows)
    problems += build_dataset.check_note_dur_consistency("user", rows)
    assert problems == []


def test_convert_all_cards_excluded_fails_closed(tmp_path: Path) -> None:
    cards = {"UC-005": _t1_card_samples_only_2_segments()}
    ledger_path, normalized_dir = _write_ledger_and_normalized(tmp_path, cards)
    dsdict_path = _write_test_dsdict(tmp_path)
    out_dir = tmp_path / "out"
    with pytest.raises(cu.AllCardsExcludedError):
        cu.convert(normalized_dir, ledger_path, out_dir, dsdict_path)
    assert not out_dir.exists()


def test_convert_bad_dsdict_fails_closed(tmp_path: Path) -> None:
    cards = {"UC-012": _t2_uc012_samples()}
    ledger_path, normalized_dir = _write_ledger_and_normalized(tmp_path, cards)
    bad_dsdict = tmp_path / "bad.yaml"
    bad_dsdict.write_text(yaml.safe_dump({"not_entries": []}), encoding="utf-8")
    out_dir = tmp_path / "out"
    with pytest.raises(cu.DsDictError):
        cu.convert(normalized_dir, ledger_path, out_dir, bad_dsdict)
    assert not out_dir.exists()


# ---------------------------------------------------------------------------
# 5.5 P2 修正 (review #265 R5): duration_tolerance_sec の nan/inf/負値は
# fail-closed（convert_d3 と共有の検査関数を read-only import で再利用）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf"), -0.01])
def test_convert_rejects_non_finite_or_negative_duration_tolerance(
    tmp_path: Path, bad_value: float
) -> None:
    """`nan`/`+inf` は乖離検査 (`internal duration self-check`) を常時
    無条件で素通りさせる。`-inf`/負値も無意味な入力のため、まとめて公開
    API 入口で fail-closed 拒否する（`convert_d3.InvalidDurationToleranceError`
    を read-only import で共有）。"""
    cards = {"UC-012": _t2_uc012_samples()}
    ledger_path, normalized_dir = _write_ledger_and_normalized(tmp_path, cards)
    dsdict_path = _write_test_dsdict(tmp_path)
    out_dir = tmp_path / "out"

    with pytest.raises(cu.convert_d3.InvalidDurationToleranceError):
        cu.convert(normalized_dir, ledger_path, out_dir, dsdict_path, duration_tolerance_sec=bad_value)
    assert not out_dir.exists()


# ---------------------------------------------------------------------------
# 6. 決定論: 同一入力 -> 同一出力バイト列
# ---------------------------------------------------------------------------


@_ffmpeg_required
def test_convert_is_deterministic_across_two_runs(tmp_path: Path) -> None:
    cards = {
        "UC-002": _t0_umi_samples(),
        "UC-004": _t1_card_samples(),
        "UC-012": _t2_uc012_samples(),
    }
    ledger_path, normalized_dir = _write_ledger_and_normalized(tmp_path, cards)
    dsdict_path = _write_test_dsdict(tmp_path)

    out_dir_1 = tmp_path / "out1"
    out_dir_2 = tmp_path / "out2"
    cu.convert(normalized_dir, ledger_path, out_dir_1, dsdict_path)
    cu.convert(normalized_dir, ledger_path, out_dir_2, dsdict_path)

    csv_1 = (out_dir_1 / "transcriptions.csv").read_bytes()
    csv_2 = (out_dir_2 / "transcriptions.csv").read_bytes()
    assert csv_1 == csv_2

    excl_1 = (out_dir_1 / "exclusions.json").read_bytes()
    excl_2 = (out_dir_2 / "exclusions.json").read_bytes()
    assert excl_1 == excl_2

    for card_id in cards:
        h1 = _sha256(out_dir_1 / "wavs" / f"{card_id}.wav")
        h2 = _sha256(out_dir_2 / "wavs" / f"{card_id}.wav")
        assert h1 == h2


# ---------------------------------------------------------------------------
# 7. T0/T1 出力バイト不変性の回帰（C2 は T2 の音素化経路のみを変更する）
# ---------------------------------------------------------------------------


@_ffmpeg_required
def test_t0_t1_rows_unaffected_by_dsdict_choice(tmp_path: Path) -> None:
    """T0/T1 は dsdict を一切参照しないため、`--dsdict` の内容を変えても
    T0/T1 カードの出力行は不変であることを確認する（C2 item4 の直接的な
    単体レベルの裏付け。実 17 本での v1/v2 バイト比較は別途 c_verify に記録）。"""
    cards = {"UC-001": _t0_sakura_samples(), "UC-002": _t0_umi_samples(), "UC-004": _t1_card_samples()}
    ledger_path, normalized_dir = _write_ledger_and_normalized(tmp_path, cards)

    dsdict_a = _write_test_dsdict(tmp_path)
    out_a = tmp_path / "out_a"
    summary_a = cu.convert(normalized_dir, ledger_path, out_a, dsdict_a)

    # 別内容の dsdict（T0/T1 が参照しないはずの余剰グラフェムを追加）でも
    # T0/T1 の出力は変わらないはず。
    dsdict_b = tmp_path / "dsdict_b.yaml"
    dsdict_b.write_text(
        yaml.safe_dump({"entries": [{"grapheme": "ぬ", "phonemes": ["n", "u"]}]}, allow_unicode=True),
        encoding="utf-8",
    )
    out_b = tmp_path / "out_b"
    summary_b = cu.convert(normalized_dir, ledger_path, out_b, dsdict_b)

    assert summary_a["included_cards"] == summary_b["included_cards"] == ["UC-001", "UC-002", "UC-004"]
    csv_a = (out_a / "transcriptions.csv").read_bytes()
    csv_b = (out_b / "transcriptions.csv").read_bytes()
    assert csv_a == csv_b
    for card_id in cards:
        assert _sha256(out_a / "wavs" / f"{card_id}.wav") == _sha256(out_b / "wavs" / f"{card_id}.wav")


# ---------------------------------------------------------------------------
# 8. run 6: カード単位ラウドネス正規化（DESIGN_S5_run6.md §1.1）
# ---------------------------------------------------------------------------


def _write_pcm16_wav(path: Path, samples: "np.ndarray", sr: int = 44100) -> None:
    sf.write(path, samples, sr, subtype="PCM_16")


def _integrated_lufs(path: Path) -> float:
    import pyloudnorm

    data, sr = sf.read(path)
    return float(pyloudnorm.Meter(sr).integrated_loudness(data))


def test_normalize_wav_loudness_in_place_hits_target_and_keeps_dynamics(
    tmp_path: Path,
) -> None:
    """線形ゲインのみで目標 LUFS に着地し、カード内の相対ダイナミクス
    （前半/後半の RMS 比）が保存されること（DESIGN_S5 §1.1: T1 の 3 段強弱の
    ような意図された時間変動を消さない）。"""
    sr = 44100
    t = np.arange(sr * 2) / sr
    quiet = 0.02 * np.sin(2 * np.pi * 220 * t[: sr])
    loud = 0.08 * np.sin(2 * np.pi * 220 * t[sr:])
    wav = tmp_path / "card.wav"
    _write_pcm16_wav(wav, np.concatenate([quiet, loud]), sr)
    pre_ratio_data, _ = sf.read(wav)
    pre_ratio = float(np.sqrt(np.mean(pre_ratio_data[sr:] ** 2))
                      / np.sqrt(np.mean(pre_ratio_data[:sr] ** 2)))

    entry = cu.normalize_wav_loudness_in_place(wav, -23.0)

    assert abs(_integrated_lufs(wav) - (-23.0)) < 0.1
    assert abs(entry["post_lufs"] - (-23.0)) < 0.1
    post_data, _ = sf.read(wav)
    post_ratio = float(np.sqrt(np.mean(post_data[sr:] ** 2))
                       / np.sqrt(np.mean(post_data[:sr] ** 2)))
    assert abs(post_ratio - pre_ratio) < 0.01  # 相対ダイナミクス保存（線形ゲイン）
    assert entry["peak_after_gain"] <= 32767.0 / 32768.0


def test_normalize_wav_loudness_peak_guard_fails_closed(tmp_path: Path) -> None:
    """ゲイン適用後に PCM_16 上限を超えるなら、クリップさせずに停止する。"""
    sr = 44100
    t = np.arange(sr * 2) / sr
    wav = tmp_path / "hot.wav"
    _write_pcm16_wav(wav, 0.9 * np.sin(2 * np.pi * 330 * t), sr)
    before = _sha256(wav)
    with pytest.raises(cu.LoudnessNormalizationError, match="peak"):
        cu.normalize_wav_loudness_in_place(wav, -3.0)  # 大幅な正ゲイン要求
    assert _sha256(wav) == before  # fail 時に元ファイルへ書き込まない


def test_normalize_wav_loudness_non_finite_measurement_fails_closed(
    tmp_path: Path,
) -> None:
    """無音（統合ラウドネスが -inf）はゲインを定義できない — fail-closed。"""
    sr = 44100
    wav = tmp_path / "silence.wav"
    _write_pcm16_wav(wav, np.zeros(sr * 2), sr)
    with pytest.raises(cu.LoudnessNormalizationError, match="finite"):
        cu.normalize_wav_loudness_in_place(wav, -23.0)


@_ffmpeg_required
def test_convert_with_normalization_reports_and_hits_target(tmp_path: Path) -> None:
    """convert(normalize_loudness_lufs=…) の E2E: 全採用カードが目標 LUFS に
    着地し、会計 loudness_normalization.json（pin 対象）が out_dir に載り、
    summary にも同内容が返ること。2 回実行のバイト決定論も固定する。"""
    cards = {
        "UC-002": _t0_umi_samples(),
        "UC-004": _t1_card_samples(),
        "UC-012": _t2_uc012_samples(),
    }
    ledger_path, normalized_dir = _write_ledger_and_normalized(tmp_path, cards)
    dsdict_path = _write_test_dsdict(tmp_path)

    out1 = tmp_path / "out_norm1"
    out2 = tmp_path / "out_norm2"
    summary = cu.convert(normalized_dir, ledger_path, out1, dsdict_path,
                         normalize_loudness_lufs=-23.0)
    cu.convert(normalized_dir, ledger_path, out2, dsdict_path,
               normalize_loudness_lufs=-23.0)

    report_path = out1 / cu.LOUDNESS_REPORT_NAME
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema"] == cu.LOUDNESS_REPORT_SCHEMA
    assert report["target_lufs"] == -23.0
    assert set(report["entries"]) == {f"{c}.wav" for c in summary["included_cards"]}
    assert summary["loudness_normalization"]["entries"] == report["entries"]
    for card_id in summary["included_cards"]:
        assert abs(_integrated_lufs(out1 / "wavs" / f"{card_id}.wav") - (-23.0)) < 0.1
        assert (_sha256(out1 / "wavs" / f"{card_id}.wav")
                == _sha256(out2 / "wavs" / f"{card_id}.wav"))
    assert (report_path.read_bytes()
            == (out2 / cu.LOUDNESS_REPORT_NAME).read_bytes())


@_ffmpeg_required
def test_convert_without_normalization_is_legacy_and_reportless(
    tmp_path: Path,
) -> None:
    """既定（None）は run 4/5 の従来動作: 会計ファイルを作らず、summary の
    loudness_normalization も None（バイト再現性の回帰は既存の determinism
    テストが担う）。"""
    cards = {"UC-002": _t0_umi_samples()}
    ledger_path, normalized_dir = _write_ledger_and_normalized(tmp_path, cards)
    dsdict_path = _write_test_dsdict(tmp_path)
    out_dir = tmp_path / "out_legacy"
    summary = cu.convert(normalized_dir, ledger_path, out_dir, dsdict_path)
    assert not (out_dir / cu.LOUDNESS_REPORT_NAME).exists()
    assert summary["loudness_normalization"] is None


def test_normalize_wav_loudness_too_short_fails_closed(tmp_path: Path) -> None:
    """pyloudnorm のゲーティングブロック長（0.4s）未満は素の ValueError を
    投げる — fail-closed の分類語彙（LoudnessNormalizationError）に収容
    されること（セルフレビュー #3 の回帰）。"""
    sr = 44100
    wav = tmp_path / "short.wav"
    _write_pcm16_wav(wav, 0.1 * np.sin(2 * np.pi * 440 * np.arange(sr // 10) / sr), sr)
    with pytest.raises(cu.LoudnessNormalizationError, match="unmeasurable"):
        cu.normalize_wav_loudness_in_place(wav, -23.0)
