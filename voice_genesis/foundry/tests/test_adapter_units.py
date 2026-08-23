"""test_adapter_units.py — 単位選択の決定論 + 伸縮キャップとループの検証。
合成ミニドナー（DonorUnit を手組み）で高速・実 vocadito 非依存。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapter"))

import numpy as np
import pytest

from _optional_runtime_stubs import stub_pyworld_if_missing

with stub_pyworld_if_missing():
    import donor_bank as db
    import units as un


def _make_unit(index: int, start: int, end: int, median_f0: float, head=None, tail=None) -> db.DonorUnit:
    n = db.N_LOG_BANDS
    if head is None:
        head = np.zeros(n)
    if tail is None:
        tail = np.zeros(n)
    return db.DonorUnit(
        index=index, start_frame=start, end_frame=end, median_f0=median_f0,
        duration_s=(end - start) * 5.0 / 1000.0, head_log_bands=np.asarray(head, dtype=np.float64),
        tail_log_bands=np.asarray(tail, dtype=np.float64),
    )


def test_select_units_deterministic_repeat() -> None:
    units = [
        _make_unit(0, 0, 100, 220.0),
        _make_unit(1, 100, 180, 330.0),
        _make_unit(2, 180, 260, 220.0),
    ]
    targets = [
        un.TargetNote(pitch_hz=220.0, duration_sec=0.5),
        un.TargetNote(pitch_hz=330.0, duration_sec=0.4),
    ]
    sel1, stats1 = un.select_units(targets, units)
    sel2, stats2 = un.select_units(targets, units)
    assert [s.unit.index for s in sel1] == [s.unit.index for s in sel2]
    assert stats1 == stats2


def test_select_units_tie_break_smallest_index() -> None:
    # unit 0 と unit 2 が全コスト同点になるよう設計 (同じ pitch/duration/head)
    units = [
        _make_unit(0, 0, 100, 220.0, head=[1.0] * db.N_LOG_BANDS),
        _make_unit(1, 100, 180, 500.0, head=[9.0] * db.N_LOG_BANDS),  # 遠い pitch (候補外)
        _make_unit(2, 180, 280, 220.0, head=[1.0] * db.N_LOG_BANDS),
    ]
    targets = [un.TargetNote(pitch_hz=220.0, duration_sec=0.5)]
    sel, _ = un.select_units(targets, units)
    assert sel[0].unit.index == 0  # tie は最小 index


def test_select_units_expands_candidate_range_when_empty() -> None:
    units = [_make_unit(0, 0, 100, 220.0)]
    # 700Hz は 220Hz から 3 半音レンジを大きく外れる -> 段階拡張が発生するはず
    targets = [un.TargetNote(pitch_hz=700.0, duration_sec=0.3)]
    sel, stats = un.select_units(targets, units)
    assert sel[0].expanded is True
    assert sel[0].unit.index == 0
    assert stats["n_expansions"] == 1


def test_select_units_concat_cost_prefers_continuity() -> None:
    # 2 note 目で、pitch/duration が同等な 2 候補のうち head が prev.tail に近い方を選ぶはず
    prev_tail = np.array([5.0] * db.N_LOG_BANDS)
    close_head = prev_tail + 0.01
    far_head = prev_tail + 50.0
    units = [
        _make_unit(0, 0, 100, 220.0, tail=prev_tail),
        _make_unit(1, 100, 200, 220.0, head=close_head),
        _make_unit(2, 200, 300, 220.0, head=far_head),
    ]
    targets = [
        un.TargetNote(pitch_hz=220.0, duration_sec=0.5),
        un.TargetNote(pitch_hz=220.0, duration_sec=0.5),
    ]
    sel, _ = un.select_units(targets, units)
    assert sel[0].unit.index == 0
    assert sel[1].unit.index == 1  # close_head の方が concat cost 小


def test_select_units_empty_units_raises() -> None:
    with pytest.raises(ValueError):
        un.select_units([un.TargetNote(220.0, 0.5)], [])


class _FakeBank:
    def __init__(self, sp: np.ndarray, ap: np.ndarray):
        self.sp = sp
        self.ap = ap


def _sp_ap_for_unit(n_frames: int, n_bins: int, base: float) -> tuple[np.ndarray, np.ndarray]:
    sp = np.full((n_frames, n_bins), base) + np.arange(n_frames)[:, None] * 0.01
    ap = np.full((n_frames, n_bins), 0.1)
    return sp, ap


def test_resolve_unit_to_note_no_cap_exact_length() -> None:
    n_bins = 4
    sp, ap = _sp_ap_for_unit(100, n_bins, 1.0)
    bank = _FakeBank(sp, ap)
    unit = _make_unit(0, 0, 100, 220.0)
    target_n_frames = 120  # ratio 1.2 -> [0.5,2.0] 内
    resolved = un.resolve_unit_to_note(bank, unit, target_n_frames)
    assert resolved.n_frames == target_n_frames
    assert resolved.cap_mode == "none"
    assert resolved.sp.shape == (target_n_frames, n_bins)
    assert resolved.true_ratio == pytest.approx(1.2)


def test_resolve_unit_to_note_extended_loop_for_long_note() -> None:
    n_bins = 4
    sp, ap = _sp_ap_for_unit(50, n_bins, 1.0)
    bank = _FakeBank(sp, ap)
    unit = _make_unit(0, 0, 50, 220.0)
    target_n_frames = 400  # ratio 8.0 >> 2.0 -> loop 必須
    resolved = un.resolve_unit_to_note(bank, unit, target_n_frames)
    assert resolved.n_frames == target_n_frames
    assert resolved.cap_mode == "extended_looped"
    assert resolved.applied_ratio == pytest.approx(2.0)
    assert resolved.n_loop_cycles >= 1
    assert resolved.sp.shape == (target_n_frames, n_bins)


def test_resolve_unit_to_note_compressed_truncated_for_short_target() -> None:
    n_bins = 4
    sp, ap = _sp_ap_for_unit(200, n_bins, 1.0)
    bank = _FakeBank(sp, ap)
    unit = _make_unit(0, 0, 200, 220.0)
    target_n_frames = 40  # ratio 0.2 << 0.5 -> 圧縮キャップ + 切り詰め
    resolved = un.resolve_unit_to_note(bank, unit, target_n_frames)
    assert resolved.n_frames == target_n_frames
    assert resolved.cap_mode == "compressed_truncated"
    assert resolved.applied_ratio == pytest.approx(0.5)
    assert resolved.sp.shape == (target_n_frames, n_bins)


def _trailing_frozen_frame_count(sp: np.ndarray) -> int:
    """`sp` 末尾から、直前フレームと完全一致するフレームが何本連続するかを
    数える（`_force_length` の末尾フレーム複製＝凍結プラトーの直接計測）。"""
    n = sp.shape[0]
    if n == 0:
        return 0
    last = sp[-1]
    count = 1
    i = n - 2
    while i >= 0 and np.array_equal(sp[i], last):
        count += 1
        i -= 1
    return count


def test_ping_pong_pad_v2_progresses_when_seg_len_le_overlap() -> None:
    """P2 修正 (review #262 R6・`r3789428506`): `seg_len <= overlap_frames`
    （短い unit の中央区間 <= ループ overlap 長）では、`jn.overlap_add_concat`
    の内部クランプにより旧実装は join ごとの正味増分が 0 フレームになり、
    何ピース足しても出力長が伸びなかった（`_force_length` の凍結補填で
    ほぼ全区間が埋まる）。実効 overlap のクランプ（`seg_len - 1` 上限）に
    より、`deficit` に対して十分な長さが生成されることを確認する。
    """
    n_bins = 3
    seg_len = 6
    overlap_frames = 8  # overlap_frames > seg_len（旧バグの発火条件）
    sp = np.arange(seg_len * n_bins, dtype=np.float64).reshape(seg_len, n_bins) + 1.0
    ap = np.full((seg_len, n_bins), 0.2)
    deficit = 300

    pad_raw_target = un._loop_pad_raw_target(seg_len, deficit, overlap_frames)
    sp_pad, ap_pad, n_cycles = un._ping_pong_pad_v2(sp, ap, pad_raw_target, overlap_frames)

    assert n_cycles > 1  # 複数ピースを積んでいる（旧バグでも n_cycles 自体は増える）
    # 正味の出力長が deficit に対して十分（旧実装は seg_len 付近で頭打ちし、
    # 何ピース追加しても伸びなかった）。
    assert sp_pad.shape[0] >= deficit
    assert ap_pad.shape[0] == sp_pad.shape[0]


def test_resolve_unit_to_note_no_frozen_plateau_when_seg_len_le_overlap() -> None:
    """P2 修正 (review #262 R6・`r3789428506`): 短い unit を 2 倍超で伸長し、
    ループ中央区間（`center_hi - center_lo`）がループ overlap 長
    （既定 8 frame=40ms/5ms）以下になるケースで、凍結補填（`_force_length` の
    末尾フレーム複製）が高々 1 フレームに収まり、出力が往復ループの周期性を
    保ったまま継続する（凍結プラトーへ縮退しない）ことを確認する。
    """
    n_bins = 4
    sp, ap = _sp_ap_for_unit(6, n_bins, 1.0)  # unit_len=6 -> base_len=12 -> seg_len=6
    bank = _FakeBank(sp, ap)
    unit = _make_unit(0, 0, 6, 220.0)
    target_n_frames = 600  # true_ratio=100 -> 2.0 キャップ -> 大幅な往復ループ延伸
    resolved = un.resolve_unit_to_note(bank, unit, target_n_frames, frame_period_ms=5.0)

    assert resolved.n_frames == target_n_frames
    assert resolved.cap_mode == "extended_looped"
    assert _trailing_frozen_frame_count(resolved.sp) <= 1

    # 出力が往復ループの周期性を保ったまま継続していること（中盤区間が
    # 単調な凍結値ではなく、フレーム間に有意な変化があることを見る）。
    mid = resolved.sp[200:400]
    interior_jumps = np.linalg.norm(np.diff(mid, axis=0), axis=1)
    assert np.max(interior_jumps) > 0.0


def test_resolve_unit_to_note_deterministic_repeat() -> None:
    n_bins = 4
    sp, ap = _sp_ap_for_unit(70, n_bins, 1.0)
    bank = _FakeBank(sp, ap)
    unit = _make_unit(0, 0, 70, 220.0)
    r1 = un.resolve_unit_to_note(bank, unit, 500)
    r2 = un.resolve_unit_to_note(bank, unit, 500)
    assert np.array_equal(r1.sp, r2.sp)
    assert np.array_equal(r1.ap, r2.ap)
    assert r1.n_loop_cycles == r2.n_loop_cycles


# --- 追補 F1.3-A item3: 長音ループ境界の overlap-add ---


def _interior_jumps(sp: np.ndarray) -> np.ndarray:
    diffs = np.log(sp[1:] + 1e-8) - np.log(sp[:-1] + 1e-8)
    return np.linalg.norm(diffs, axis=1)


def test_ping_pong_pad_v2_matches_v1_total_length() -> None:
    n_bins = 4
    seg_len = 20
    sp = np.abs(np.random.default_rng(5).standard_normal((seg_len, n_bins))) + 1.0
    ap = np.full((seg_len, n_bins), 0.2)
    deficit = 90  # 4.5 cycles 相当

    sp_v1, n1 = un._ping_pong_pad(sp, deficit)
    sp_v2, ap_v2, n2 = un._ping_pong_pad_v2(sp, ap, deficit, overlap_frames=6)

    assert n1 == n2  # サイクル数（ピース数）は overlap-add の有無で不変
    assert sp_v1.shape[0] == deficit  # v1: 硬い折返し・deficit ちょうど
    # v2: overlap-add で継ぎ目ごとに短くなる（ピース数-1 回の join 分）。
    assert sp_v2.shape[0] < deficit
    assert ap_v2.shape[0] == sp_v2.shape[0]


def test_ping_pong_pad_v2_seam_smoother_than_hard_fold() -> None:
    """折返し境界の overlap-add が「硬い折返し」より継ぎ目の跳びを増大させない
    ことを確認する（`_ping_pong_pad` (v1) との比較。完全な平滑化は主張しないが、
    overlap-add 適用箇所の跳びが単位内部の跳び分布を極端に超えないことを見る）。
    """
    n_bins = 5
    seg_len = 30
    rng = np.random.default_rng(9)
    sp = np.abs(1.0 + 0.3 * rng.standard_normal((seg_len, n_bins)).cumsum(axis=0) * 0.05) + 0.5
    ap = np.full((seg_len, n_bins), 0.2)
    deficit = 3 * seg_len  # ちょうど 3 ピース（2 継ぎ目）

    sp_v2, _ap_v2, n_cycles = un._ping_pong_pad_v2(sp, ap, deficit, overlap_frames=8)
    assert n_cycles == 3
    jumps_v2 = _interior_jumps(sp_v2)
    jumps_interior = _interior_jumps(sp)
    # 折返し継ぎ目付近の跳びが unit 内部の跳び分布の最大値を極端に超えない
    # （硬い折返しであれば方向反転点で跳びが跳ね上がりうる）。
    assert np.max(jumps_v2) <= np.max(jumps_interior) * 3.0


def test_resolve_unit_to_note_extended_loop_v2_seams_use_overlap_add() -> None:
    """resolve_unit_to_note が実際に v2 ループパディングを使っており、
    (a) 最終形状は target_n_frames ちょうど、(b) n_loop_cycles >= 1、
    という既存 acceptance を壊さないことを確認する（後方互換）。"""
    n_bins = 4
    sp, ap = _sp_ap_for_unit(50, n_bins, 1.0)
    bank = _FakeBank(sp, ap)
    unit = _make_unit(0, 0, 50, 220.0)
    target_n_frames = 400
    resolved = un.resolve_unit_to_note(bank, unit, target_n_frames, frame_period_ms=5.0)
    assert resolved.n_frames == target_n_frames
    assert resolved.cap_mode == "extended_looped"
    assert resolved.n_loop_cycles >= 1
    assert resolved.sp.shape == (target_n_frames, n_bins)


def test_resolve_unit_to_note_loop_deterministic_with_frame_period() -> None:
    n_bins = 4
    sp, ap = _sp_ap_for_unit(50, n_bins, 1.0)
    bank = _FakeBank(sp, ap)
    unit = _make_unit(0, 0, 50, 220.0)
    r1 = un.resolve_unit_to_note(bank, unit, 350, frame_period_ms=5.0)
    r2 = un.resolve_unit_to_note(bank, unit, 350, frame_period_ms=5.0)
    assert np.array_equal(r1.sp, r2.sp)
    assert np.array_equal(r1.ap, r2.ap)
    assert r1.n_loop_cycles == r2.n_loop_cycles


# --- 追補 F1.4-A/C: VCV unit の文脈キー選択 ---


def _make_vcv_unit(
    index: int, start: int, end: int, median_f0: float, vcore_rel: int,
    overlap_frames: int = 4, preutterance_frames: int = 12,
) -> db.DonorUnit:
    n = db.N_LOG_BANDS
    return db.DonorUnit(
        index=index, start_frame=start, end_frame=end, median_f0=median_f0,
        duration_s=(end - start) * 5.0 / 1000.0,
        head_log_bands=np.zeros(n), tail_log_bands=np.zeros(n),
        overlap_frames=overlap_frames, preutterance_frames=preutterance_frames,
        vowel_core_start_frame=vcore_rel,
    )


class _FakeSeg:
    class _Mora:
        def __init__(self, kana: str, vowel: str) -> None:
            self.kana = kana
            self.vowel = vowel

    class _Note:
        def __init__(self, mora: "_FakeSeg._Mora", midi: float = 57.0) -> None:
            self.mora = mora
            self.midi = midi

    def __init__(self, kana: str, vowel: str, is_phrase_first: bool, midi: float = 57.0) -> None:
        self.note = _FakeSeg._Note(_FakeSeg._Mora(kana, vowel), midi=midi)
        self.is_phrase_first = is_phrase_first


def test_vcv_context_sequence_phrase_initial_is_none() -> None:
    segs = [_FakeSeg("さ", "a", True), _FakeSeg("く", "u", False), _FakeSeg("ら", "a", False)]
    ctx = un.vcv_context_sequence(segs)
    assert ctx == [(None, "さ"), ("a", "く"), ("u", "ら")]


def test_vcv_context_sequence_new_phrase_resets_prev_vowel() -> None:
    segs = [
        _FakeSeg("さ", "a", True), _FakeSeg("く", "u", False),
        _FakeSeg("や", "a", True),  # 新フレーズ -> prev_vowel は None（直前の "u" を継承しない）
    ]
    ctx = un.vcv_context_sequence(segs)
    assert ctx == [(None, "さ"), ("a", "く"), (None, "や")]


def test_select_vcv_units_prefers_exact_context_match() -> None:
    units = [
        _make_vcv_unit(0, 0, 100, 220.0, vcore_rel=20),  # context (None, "か") 相当
        _make_vcv_unit(1, 100, 200, 220.0, vcore_rel=20),  # context ("a", "か")
    ]
    unit_contexts = {0: (None, "か"), 1: ("a", "か")}
    targets = [un.VCVTargetNote(pitch_hz=220.0, duration_sec=0.4, prev_vowel="a", mora="か")]
    sels, stats = un.select_vcv_units(targets, units, unit_contexts)
    assert sels[0].unit.index == 1
    assert sels[0].match_kind == "exact"
    assert stats == dict(n_notes=1, n_exact=1, n_near_fallback=0, n_global_fallback=0, fallback_notes=[])


def test_select_vcv_units_falls_back_to_mora_only_when_context_missing() -> None:
    units = [_make_vcv_unit(0, 0, 100, 220.0, vcore_rel=20)]
    unit_contexts = {0: ("i", "か")}  # 要求は ("a","か") だが存在しない -> mora のみ一致でフォールバック
    targets = [un.VCVTargetNote(pitch_hz=220.0, duration_sec=0.4, prev_vowel="a", mora="か")]
    sels, stats = un.select_vcv_units(targets, units, unit_contexts)
    assert sels[0].unit.index == 0
    assert sels[0].match_kind == "near"
    assert stats["n_near_fallback"] == 1
    assert stats["fallback_notes"] == [(0, "a", "か")]


def test_select_vcv_units_global_fallback_when_mora_absent() -> None:
    units = [_make_vcv_unit(0, 0, 100, 220.0, vcore_rel=20)]
    unit_contexts = {0: ("i", "き")}  # mora "か" 自体が存在しない
    targets = [un.VCVTargetNote(pitch_hz=220.0, duration_sec=0.4, prev_vowel="a", mora="か")]
    sels, stats = un.select_vcv_units(targets, units, unit_contexts)
    assert sels[0].unit.index == 0
    assert sels[0].match_kind == "global"
    assert stats["n_global_fallback"] == 1


def test_select_vcv_units_picks_min_pitch_distance_among_exact_matches() -> None:
    units = [
        _make_vcv_unit(0, 0, 100, 220.0, vcore_rel=20),  # A3
        _make_vcv_unit(1, 100, 200, 349.23, vcore_rel=20),  # F4
    ]
    unit_contexts = {0: ("a", "か"), 1: ("a", "か")}
    targets = [un.VCVTargetNote(pitch_hz=330.0, duration_sec=0.4, prev_vowel="a", mora="か")]  # F4 寄り
    sels, _stats = un.select_vcv_units(targets, units, unit_contexts)
    assert sels[0].unit.index == 1


def test_select_vcv_units_tie_break_smallest_index() -> None:
    units = [
        _make_vcv_unit(0, 0, 100, 220.0, vcore_rel=20),
        _make_vcv_unit(1, 100, 200, 220.0, vcore_rel=20),
    ]
    unit_contexts = {0: ("a", "か"), 1: ("a", "か")}
    targets = [un.VCVTargetNote(pitch_hz=220.0, duration_sec=0.4, prev_vowel="a", mora="か")]
    sels, _stats = un.select_vcv_units(targets, units, unit_contexts)
    assert sels[0].unit.index == 0


def test_select_vcv_units_deterministic_repeat() -> None:
    units = [
        _make_vcv_unit(0, 0, 100, 220.0, vcore_rel=20),
        _make_vcv_unit(1, 100, 200, 245.0, vcore_rel=15),
    ]
    unit_contexts = {0: ("a", "か"), 1: ("a", "か")}
    targets = [un.VCVTargetNote(pitch_hz=230.0, duration_sec=0.4, prev_vowel="a", mora="か")]
    s1, _ = un.select_vcv_units(targets, units, unit_contexts)
    s2, _ = un.select_vcv_units(targets, units, unit_contexts)
    assert s1[0].unit.index == s2[0].unit.index
    assert s1[0].cost_total == pytest.approx(s2[0].cost_total)


def test_select_vcv_units_empty_units_raises() -> None:
    with pytest.raises(ValueError):
        un.select_vcv_units([un.VCVTargetNote(220.0, 0.5, mora="か")], [], {})


# --- 追補 F1.4-A/5: VCV unit の尺合わせ（録音済み調音遷移は伸縮せず保持） ---


def test_resolve_vcv_unit_to_note_preserves_head_unstretched() -> None:
    n_bins = 4
    # 先頭 20 フレーム = 録音済み調音遷移（固定値 5.0）、以降 = 母音定常部（1.0 系列）。
    sp = np.concatenate([np.full((20, n_bins), 5.0), np.full((80, n_bins), 1.0)], axis=0)
    ap = np.full((100, n_bins), 0.1)
    bank = _FakeBank(sp, ap)
    unit = _make_vcv_unit(0, 0, 100, 220.0, vcore_rel=20)
    resolved = un.resolve_vcv_unit_to_note(bank, unit, target_n_frames=120)
    assert resolved.head_frames == 20
    assert resolved.head_clipped is False
    # 先頭 20 フレームは録音済み値そのまま（伸縮なし）。
    assert np.array_equal(resolved.sp[:20], sp[:20])
    assert resolved.n_frames == 120


def test_resolve_vcv_unit_to_note_core_loops_for_long_note() -> None:
    n_bins = 4
    sp = np.concatenate([np.full((10, n_bins), 5.0), np.full((40, n_bins), 1.0)], axis=0)
    ap = np.full((50, n_bins), 0.1)
    bank = _FakeBank(sp, ap)
    unit = _make_vcv_unit(0, 0, 50, 220.0, vcore_rel=10)
    resolved = un.resolve_vcv_unit_to_note(bank, unit, target_n_frames=500)
    assert resolved.n_frames == 500
    assert resolved.head_frames == 10
    assert resolved.cap_mode == "extended_looped"
    assert np.array_equal(resolved.sp[:10], sp[:10])  # head は往復ループの対象外


def test_resolve_vcv_unit_to_note_core_truncated_for_short_note() -> None:
    n_bins = 4
    sp = np.concatenate([np.full((10, n_bins), 5.0), np.full((190, n_bins), 1.0)], axis=0)
    ap = np.full((200, n_bins), 0.1)
    bank = _FakeBank(sp, ap)
    unit = _make_vcv_unit(0, 0, 200, 220.0, vcore_rel=10)
    resolved = un.resolve_vcv_unit_to_note(bank, unit, target_n_frames=40)
    assert resolved.n_frames == 40
    assert resolved.cap_mode == "compressed_truncated"
    assert resolved.head_frames == 10


def test_resolve_vcv_unit_to_note_head_clipped_when_target_shorter_than_transition() -> None:
    n_bins = 4
    sp = np.full((100, n_bins), 1.0)
    ap = np.full((100, n_bins), 0.1)
    bank = _FakeBank(sp, ap)
    unit = _make_vcv_unit(0, 0, 100, 220.0, vcore_rel=50)  # 遷移がとても長い
    resolved = un.resolve_vcv_unit_to_note(bank, unit, target_n_frames=10)  # 目標が遷移より短い
    assert resolved.head_clipped is True
    assert resolved.head_frames < 50
    assert resolved.n_frames == 10


def test_resolve_vcv_unit_to_note_none_vcore_behaves_like_whole_unit_loop() -> None:
    """`vowel_core_start_frame=None`（後方互換）は unit 全体が母音定常部扱い
    （head_frames=0）になることを確認する。"""
    n_bins = 4
    sp, ap = _sp_ap_for_unit(50, n_bins, 1.0)
    bank = _FakeBank(sp, ap)
    unit = _make_unit(0, 0, 50, 220.0)  # vowel_core_start_frame は既定 None
    resolved = un.resolve_vcv_unit_to_note(bank, unit, target_n_frames=120)
    assert resolved.head_frames == 0
    assert resolved.n_frames == 120


def test_resolve_vcv_unit_to_note_deterministic_repeat() -> None:
    n_bins = 4
    sp = np.concatenate([np.full((8, n_bins), 5.0), np.full((42, n_bins), 1.0)], axis=0)
    ap = np.full((50, n_bins), 0.1)
    bank = _FakeBank(sp, ap)
    unit = _make_vcv_unit(0, 0, 50, 220.0, vcore_rel=8)
    r1 = un.resolve_vcv_unit_to_note(bank, unit, 350)
    r2 = un.resolve_vcv_unit_to_note(bank, unit, 350)
    assert np.array_equal(r1.sp, r2.sp)
    assert np.array_equal(r1.ap, r2.ap)
    assert r1.n_loop_cycles == r2.n_loop_cycles


# --- 追補 F1.4-B: 接合が母音定常部で行われることの構造的検証 ---


def test_vcv_unit_overlap_falls_before_vowel_core_boundary() -> None:
    """接合長（unit.overlap_frames）は常に母音核開始フレーム
    （vowel_core_start_frame）より手前 = 録音済みの前母音尾部分に収まる
    べきという構造不変条件（実データでの実測: 波音リツ oto.ini 全 1059
    非フレーズ先頭エイリアスで overlap_ms < consonant_ms が 100% 成立。
    `results_f1_4` record 参照）。"""
    unit = _make_vcv_unit(0, 0, 200, 220.0, vcore_rel=74, overlap_frames=20, preutterance_frames=60)
    assert unit.overlap_frames < unit.vowel_core_start_frame
    assert unit.preutterance_frames < unit.vowel_core_start_frame
    assert unit.overlap_frames <= unit.preutterance_frames
