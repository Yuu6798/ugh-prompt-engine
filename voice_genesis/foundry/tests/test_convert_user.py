"""test_convert_user.py — S3 Phase C `s1_dataprep/convert_user.py` の検証。

`DESIGN_S3_backfill.md` §3/§3.1 の受け入れ条件（tier 別アラインメント正常系 3
本・台帳不突合 fail-closed・T1 3 段検出失敗 fail-closed・決定論 2 回一致・
build_dataset ゲート直呼び）を高速・合成フィクスチャ（正弦波 + 無音）で検証する。
実収録音源（scratchpad の 17 本）には依存しない（それらを使った実測検証は
別途 `$SCRATCH/c_verify/` に記録する）。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import List

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "s1_dataprep"))
import convert_user as cu  # noqa: E402

_FFMPEG_MISSING = cu.convert_d3.FFMPEG_PATH is None
_ffmpeg_required = pytest.mark.skipif(
    _FFMPEG_MISSING, reason="ffmpeg が見つからない環境ではスキップ（resample_to_44k1 依存）"
)

SR = 24000


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
    normalized_dir.mkdir()
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
    for i, f in enumerate(freqs):
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
# 2. T2 かな -> モーラ化: 促音/カタカナ外来語/濁音行 の除外を確認
# ---------------------------------------------------------------------------


def test_t2_phrase_morae_uc012_and_uc013_succeed() -> None:
    for card_id in ("UC-012", "UC-013"):
        morae, err = cu._phrase_morae(card_id, cu.T2_PHRASES[card_id])
        assert err is None, err
        assert morae is not None and sum(len(m) for m in morae) > 0


@pytest.mark.parametrize("card_id", ["UC-008", "UC-009", "UC-010", "UC-011", "UC-014", "UC-015", "UC-016", "UC-017"])
def test_t2_phrase_morae_unsupported_cards_are_excluded_with_reason(card_id: str) -> None:
    """`phoneme_jp.kana_to_morae` の対応範囲外（促音・カタカナ外来語音・濁音行
    ば/だ/ざ・半濁音ぱ行）に当たるカードは、実装を止めずに理由付きで除外される。"""
    morae, err = cu._phrase_morae(card_id, cu.T2_PHRASES[card_id])
    assert morae is None
    assert err is not None and card_id in err and "unmapped kana" in err


# ---------------------------------------------------------------------------
# 3. T0/T1/T2 セグメンテーション・アラインメント単体
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


def test_t2_process_card_uc012_builds_row() -> None:
    samples = _t2_uc012_samples()
    row, err = cu._process_t2_card("UC-012", samples, SR)
    assert err is None, err
    assert row is not None
    n_notes = len([t for t in row["note_seq"].split() if t != "rest"])
    assert n_notes == 14  # 「みわたすかぎり」7 + 「ひかりかがやく」7
    ph_total = sum(float(x) for x in row["ph_dur"].split())
    assert ph_total == pytest.approx(len(samples) / SR, abs=1e-6)


# ---------------------------------------------------------------------------
# 4. convert() エンドツーエンド: 正常系 3 tier + 個別カード除外 + build_dataset ゲート
# ---------------------------------------------------------------------------


@_ffmpeg_required
def test_convert_end_to_end_mixed_tiers_publishes_dataset(tmp_path: Path) -> None:
    cards = {
        "UC-001": _t0_sakura_samples(),
        "UC-002": _t0_umi_samples(),
        "UC-004": _t1_card_samples(),
        "UC-005": _t1_card_samples_only_2_segments(),  # 3 段検出失敗 -> 除外される
        "UC-012": _t2_uc012_samples(),
        "UC-008": _t2_uc012_samples(),  # かな未対応 (カタカナ外来語) -> 除外される
    }
    ledger_path, normalized_dir = _write_ledger_and_normalized(tmp_path, cards)
    out_dir = tmp_path / "out"

    summary = cu.convert(normalized_dir, ledger_path, out_dir)

    assert set(summary["included_cards"]) == {"UC-001", "UC-002", "UC-004", "UC-012"}
    excluded_ids = {e["card_id"] for e in summary["excluded_cards"]}
    assert excluded_ids == {"UC-005", "UC-008"}
    uc005 = next(e for e in summary["excluded_cards"] if e["card_id"] == "UC-005")
    assert uc005["tier"] == "T1" and "found 2" in uc005["reason"]
    uc008 = next(e for e in summary["excluded_cards"] if e["card_id"] == "UC-008")
    assert uc008["tier"] == "T2" and "unmapped kana" in uc008["reason"]

    csv_path = out_dir / "transcriptions.csv"
    assert csv_path.exists()
    import csv as csv_module
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv_module.DictReader(f))
    assert {r["name"] for r in rows} == {"UC-001", "UC-002", "UC-004", "UC-012"}

    for card_id in ("UC-001", "UC-002", "UC-004", "UC-012"):
        wav_path = out_dir / "wavs" / f"{card_id}.wav"
        assert wav_path.exists()
        import wave
        with wave.open(str(wav_path), "rb") as w:
            assert w.getframerate() == 44100
            assert w.getsampwidth() == 2

    exclusions = json.loads((out_dir / "exclusions.json").read_text(encoding="utf-8"))
    assert exclusions["n_included"] == 4
    assert exclusions["n_excluded"] == 2


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
        "UC-013": _t2_uc012_samples(),
    }
    ledger_path, normalized_dir = _write_ledger_and_normalized(tmp_path, cards)
    out_dir = tmp_path / "out"
    cu.convert(normalized_dir, ledger_path, out_dir)

    rows = build_dataset.read_transcriptions(out_dir / "transcriptions.csv")
    problems = build_dataset.validate_speaker("user", out_dir, rows)
    problems += build_dataset.check_ph_dur_duration("user", out_dir / "wavs", rows)
    problems += build_dataset.check_note_dur_consistency("user", rows)
    assert problems == []


def test_convert_all_cards_excluded_fails_closed(tmp_path: Path) -> None:
    cards = {"UC-005": _t1_card_samples_only_2_segments()}
    ledger_path, normalized_dir = _write_ledger_and_normalized(tmp_path, cards)
    out_dir = tmp_path / "out"
    with pytest.raises(cu.AllCardsExcludedError):
        cu.convert(normalized_dir, ledger_path, out_dir)
    assert not out_dir.exists()


# ---------------------------------------------------------------------------
# 5. 決定論: 同一入力 -> 同一出力バイト列
# ---------------------------------------------------------------------------


@_ffmpeg_required
def test_convert_is_deterministic_across_two_runs(tmp_path: Path) -> None:
    cards = {
        "UC-002": _t0_umi_samples(),
        "UC-004": _t1_card_samples(),
        "UC-012": _t2_uc012_samples(),
    }
    ledger_path, normalized_dir = _write_ledger_and_normalized(tmp_path, cards)

    out_dir_1 = tmp_path / "out1"
    out_dir_2 = tmp_path / "out2"
    cu.convert(normalized_dir, ledger_path, out_dir_1)
    cu.convert(normalized_dir, ledger_path, out_dir_2)

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
