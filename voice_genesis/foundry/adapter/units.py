"""adapter/units.py — VG-F1: 単位選択（target cost + concatenation cost・貪欲・決定論）
+ 尺合わせ（伸縮キャップ + 往復ループ）。

設計書 §2 units.py に対応する。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from donor_bank import DonorBank, DonorUnit  # noqa: F401  (adapter sibling import)
import joins as jn  # adapter sibling import・追補 F1.3-A: ループ境界 overlap-add
import vowel_class as vc  # noqa: F401  (adapter sibling import・追補 F1.1-A)

DEFAULT_W_P = 1.0
DEFAULT_W_D = 0.3
DEFAULT_W_C = 1.0
# 追補 F1.1-A item3: 母音不一致ペナルティ。設計書の指定値は「既定 10.0・
# 強制力のある大きさ」。
#
# [実装決定・要 record] 実ドナー（vocadito clip 2, A1 notes, 74 units）で
# 実測すると、既存の接合コスト cost_concat（24 log 帯域 L2 距離、w_c=1.0）は
# 異なる母音の unit 間で ~15-22 の値を取る（母音間で sp 包絡が大きく異なる
# ため、母音の異同と強く相関する構造的な性質）。w_v=10.0（既定値）のままだと
# この concat cost 差に負けて、母音制約が「音域内に候補が実在するのに」
# 無視される事例が sakura/umi 実測で複数発生した（w_v=10.0: 母音一致率
# 16/32、真に候補が存在しない 1 件を除いても 3 件が回避可能な不一致）。
# w_v を 18.0 まで引き上げると実測データで不一致 0 件（32/32 一致）に達した
# ため、安全域込みで 25.0 を採用する（"強制力のある大きさ" という設計意図を
# 実際の concat cost スケールで担保する較正。帯域指標での最適化ではなく、
# 母音制約という新しい選択軸を機能させるための重み較正）。
DEFAULT_W_V = 25.0

# 追補 F1.1-A item3: 近縁母音への半ペナルティフォールバック表（a↔o, i↔e, u↔o）。
# "↔" は双方向を意味する（例: a の近縁は o、かつ o の近縁は a と u の両方）。
NEAR_VOWEL_FALLBACK: Dict[str, Tuple[str, ...]] = {
    "a": ("o",),
    "o": ("a", "u"),
    "i": ("e",),
    "e": ("i",),
    "u": ("o",),
}


def mora_to_vowel_target(mora) -> Optional[str]:
    """score mora -> 選択コストの母音制約ターゲット。

    設計書追補 F1.1-A item4「目標母音時系列は score の mora から導出
    （F1a glue の母音解決を共通化）」に対応する。F1a glue（`results_f1a/glue_control.py`）
    は `seg.note.mora.vowel` をそのまま母音区間表に使っており、本関数もそれを
    踏襲する。

    [実装決定・record 記録] 撥音「ん」（`mora.vowel == "N"`）は鼻音として
    子音層（consonants.py）に処理を委譲し、母音選択には制約を課さない
    （None を返す = 制約なし）。sakura/umi の歌詞には「ん」は出現しないため
    現時点では未発動の分岐だが、将来の歌詞拡張に備えて実装しておく。
    """
    if mora.vowel in vc.VOWELS_5:
        return mora.vowel
    return None

INITIAL_SEMITONE_RANGE = 3.0
SEMITONE_EXPANSION_STEP = 3.0
# 段階拡張の安全弁（3 オクターブ分。実装決定・record 記録）。ここまで空なら
# 全 unit を候補にフォールバックする。
MAX_SEMITONE_RANGE = 36.0

STRETCH_RATIO_MIN = 0.5
STRETCH_RATIO_MAX = 2.0
# 往復ループに使う unit 中央区間の割合（中央 50%）。
LOOP_CENTER_FRACTION = 0.5
# 追補 F1.3-A item3: 長音ループ（往復）境界の overlap-add 長。joins.py
# DEFAULT_OVERLAP_MS（oto 情報を持たない銀行の固定値）と同じ規約を踏襲する
# （ループ折返しは donor 側の oto overlap 情報を持たないため常に固定値）。
LOOP_OVERLAP_MS = jn.DEFAULT_OVERLAP_MS


def _semitone_dist(f_a: float, f_b: float) -> float:
    f_a = max(f_a, 1e-6)
    f_b = max(f_b, 1e-6)
    return float(abs(12.0 * np.log2(f_a / f_b)))


@dataclass(frozen=True)
class TargetNote:
    pitch_hz: float
    duration_sec: float
    label: str = ""
    # 追補 F1.1-A: 母音制約ターゲット（a/i/u/e/o のいずれか、None=制約なし）。
    # `mora_to_vowel_target()` で score の mora から導出する。
    vowel_target: Optional[str] = None


@dataclass(frozen=True)
class UnitSelection:
    note_index: int
    unit: DonorUnit
    cost_pitch: float
    cost_duration: float
    cost_concat: float
    cost_vowel: float
    cost_total: float
    n_candidates: int
    semitone_range_used: float
    expanded: bool
    # 追補 F1.1-A: 母音制約の結果記録（vowel_target=None なら常に unconstrained）。
    vowel_target: Optional[str] = None
    vowel_selected: Optional[str] = None
    vowel_fallback: bool = False


def select_units(
    targets: Sequence[TargetNote],
    units: Sequence[DonorUnit],
    w_p: float = DEFAULT_W_P,
    w_d: float = DEFAULT_W_D,
    w_c: float = DEFAULT_W_C,
    unit_vowels: Optional[Dict[int, str]] = None,
    w_v: float = DEFAULT_W_V,
) -> Tuple[List[UnitSelection], dict]:
    """target ノート列に対し、貪欲逐次 argmin（決定論）で donor unit を選ぶ。

    候補 = |Δpitch(semitone)| <= 現在の半音レンジの unit。空なら段階拡張し
    （拡張履歴は expanded フラグ + semitone_range_used で結果に残す）、
    それでも空なら全 unit へフォールバックする。tie は unit.index 昇順
    （argmin を index 昇順に走査し「厳密に小さい場合のみ更新」することで
    決定論を保証する）。

    追補 F1.1-A item3: `unit_vowels`（unit.index -> ラベル、`vowel_class.classify_donor_units`
    の戻り値）と `note.vowel_target` が両方指定されている場合、候補コストに
    母音不一致ペナルティ `w_v` を加算する（一致=0 / 近縁母音=半ペナルティ
    （フォールバック発動として記録）/ それ以外（"x" 含む）=w_v 満額）。
    `unit_vowels=None` または `vowel_target=None` の note は無制約（既定挙動と
    完全後方互換）。
    """
    if not units:
        raise ValueError("units is empty: donor bank に単位が 1 つもない")

    sorted_units = sorted(units, key=lambda u: u.index)

    selections: List[UnitSelection] = []
    last_unit: Optional[DonorUnit] = None
    n_expansions = 0
    n_vowel_unconstrained = 0
    n_vowel_exact = 0
    n_vowel_near_fallback = 0
    n_vowel_mismatch = 0

    for i, note in enumerate(targets):
        semitone_range = INITIAL_SEMITONE_RANGE
        expanded = False
        candidates = [u for u in sorted_units if _semitone_dist(u.median_f0, note.pitch_hz) <= semitone_range]
        while not candidates and semitone_range < MAX_SEMITONE_RANGE:
            semitone_range += SEMITONE_EXPANSION_STEP
            expanded = True
            candidates = [u for u in sorted_units if _semitone_dist(u.median_f0, note.pitch_hz) <= semitone_range]
        if not candidates:
            candidates = list(sorted_units)
            expanded = True

        target_vowel = note.vowel_target if unit_vowels is not None else None

        # 追補 F1.1-A item3「目標母音の unit が音域内に無い場合は近縁母音へ
        # フォールバック」に対応: 現在の候補内に一致/近縁ラベルの unit が
        # 1 つも無い場合、既存の段階拡張と同じ機構（同じ step / 安全弁）で
        # 探索範囲を広げる（[実装決定・record 記録] ピッチ空集合時の拡張を
        # 母音空集合時にも適用する自然な拡張。既存の候補生成そのものは
        # unit_vowels=None の呼び出しで完全後方互換）。
        if target_vowel is not None:

            def _has_vowel_candidate(cands: List[DonorUnit]) -> bool:
                for u in cands:
                    lbl = unit_vowels.get(u.index, vc.LOW_CONFIDENCE_LABEL)  # type: ignore[union-attr]
                    if lbl == target_vowel or lbl in NEAR_VOWEL_FALLBACK.get(target_vowel, ()):
                        return True
                return False

            while not _has_vowel_candidate(candidates) and semitone_range < MAX_SEMITONE_RANGE:
                semitone_range += SEMITONE_EXPANSION_STEP
                expanded = True
                candidates = [
                    u for u in sorted_units if _semitone_dist(u.median_f0, note.pitch_hz) <= semitone_range
                ]
            if not _has_vowel_candidate(candidates):
                candidates = list(sorted_units)
                expanded = True

        if expanded:
            n_expansions += 1

        best: Optional[Tuple[float, DonorUnit, float, float, float, float, bool]] = None
        for u in candidates:  # sorted_units 由来なので index 昇順を保つ
            cp = _semitone_dist(u.median_f0, note.pitch_hz)
            cd = abs(float(np.log(max(u.duration_s, 1e-6) / max(note.duration_sec, 1e-6))))
            if last_unit is None:
                cc = 0.0
            else:
                cc = float(np.linalg.norm(last_unit.tail_log_bands - u.head_log_bands))
            if target_vowel is None:
                cv, fb = 0.0, False
            else:
                u_label = unit_vowels.get(u.index, vc.LOW_CONFIDENCE_LABEL)  # type: ignore[union-attr]
                if u_label == target_vowel:
                    cv, fb = 0.0, False
                elif u_label in NEAR_VOWEL_FALLBACK.get(target_vowel, ()):
                    cv, fb = 0.5 * w_v, True
                else:
                    cv, fb = w_v, False
            total = w_p * cp + w_d * cd + w_c * cc + cv
            if best is None or total < best[0]:
                best = (total, u, cp, cd, cc, cv, fb)

        assert best is not None
        total, u, cp, cd, cc, cv, fb = best

        vowel_selected: Optional[str] = None
        if target_vowel is not None:
            vowel_selected = unit_vowels.get(u.index, vc.LOW_CONFIDENCE_LABEL)  # type: ignore[union-attr]
            if vowel_selected == target_vowel:
                n_vowel_exact += 1
            elif fb:
                n_vowel_near_fallback += 1
            else:
                n_vowel_mismatch += 1
        else:
            n_vowel_unconstrained += 1

        selections.append(
            UnitSelection(
                note_index=i, unit=u, cost_pitch=cp, cost_duration=cd, cost_concat=cc, cost_vowel=cv,
                cost_total=total, n_candidates=len(candidates), semitone_range_used=semitone_range,
                expanded=expanded, vowel_target=target_vowel, vowel_selected=vowel_selected, vowel_fallback=fb,
            )
        )
        last_unit = u

    stats = dict(
        n_notes=len(targets), n_expansions=n_expansions,
        n_vowel_unconstrained=n_vowel_unconstrained, n_vowel_exact=n_vowel_exact,
        n_vowel_near_fallback=n_vowel_near_fallback, n_vowel_mismatch=n_vowel_mismatch,
    )
    return selections, stats


# ---------------------------------------------------------------------------
# 追補 F1.4-C: VCV unit 選択（文脈キー完全一致必須 + 音高最小差）
# ---------------------------------------------------------------------------


def vcv_context_sequence(segments: Sequence) -> List[Tuple[Optional[str], str]]:
    """score タイムライン（`performance.TimelineSegment` 列）から各 note の
    VCV 文脈キー (prev_vowel, mora_kana) を導出する（追補 F1.4-C）。

    フレーズ先頭（`seg.is_phrase_first`）は prev_vowel=None（oto の "-" 先頭形
    に対応）、それ以外は直前ノートの `mora.vowel` を使う。`performance.py` が
    既に計算済みの `is_phrase_first` をそのまま使う（フレーズ境界判定ロジックの
    二重実装を避ける・[実装決定]）。duck-typed（`seg.is_phrase_first` /
    `seg.note.mora.kana` / `seg.note.mora.vowel` / `seg.note.mora.is_moraic_nasal`
    を持つ列であれば良い）。

    S3 Phase B1 修正（`score_d3_kana.py` docstring 参照）: 直前モーラが撥音
    「ん」（`mora.is_moraic_nasal=True`、`mora.vowel="N"`）の場合、oto.ini の
    VCV エイリアス prev トークンは小文字 `"n"`（UTAU 慣習。実測: 波音リツ
    強連続音 Ver1.5.1 に `"n わ"` 等のエイリアスが実在）を使うため、
    `mora.vowel`（大文字 `"N"`）をそのまま使うと厳密一致が常に外れ `near`
    フォールバックへ縮退する。撥音直後のみ prev トークンを `"n"` へ変換する
    （sakura/umi は「ん」を含まないためこの分岐は発火せず無影響——変更前後の
    byte-identity 回帰で実証済み）。
    """
    out: List[Tuple[Optional[str], str]] = []
    prev_vowel: Optional[str] = None
    prev_is_moraic_nasal = False
    for seg in segments:
        if seg.is_phrase_first:
            pv = None
        elif prev_is_moraic_nasal:
            pv = "n"
        else:
            pv = prev_vowel
        out.append((pv, seg.note.mora.kana))
        prev_vowel = seg.note.mora.vowel
        # `getattr` with default: duck-typed 呼び出し元（テストの `_FakeSeg`
        # 等）は `is_moraic_nasal` を持たないことがあるため、無ければ従来どおり
        # False 扱い（分岐発火なし = 旧挙動）とする。
        prev_is_moraic_nasal = getattr(seg.note.mora, "is_moraic_nasal", False)
    return out


@dataclass(frozen=True)
class VCVTargetNote:
    pitch_hz: float
    duration_sec: float
    label: str = ""
    prev_vowel: Optional[str] = None  # None = フレーズ先頭（oto "-" 形）
    mora: str = ""  # 対象モーラかな（oto エイリアスの右トークンと同じ表記）


@dataclass(frozen=True)
class VCVUnitSelection:
    note_index: int
    unit: DonorUnit
    cost_pitch: float
    cost_duration: float
    cost_total: float
    n_candidates: int
    prev_vowel_target: Optional[str]
    mora_target: str
    # "exact" = (prev_vowel, mora) 完全一致 / "near" = mora 一致・prev_vowel を
    # x 扱いしたフォールバック / "global" = mora すら一致する候補が無く
    # 全 unit へ最終フォールバック（想定外・record で要注視）。
    match_kind: str


def select_vcv_units(
    targets: Sequence[VCVTargetNote],
    units: Sequence[DonorUnit],
    unit_contexts: Dict[int, Tuple[Optional[str], str]],
    w_p: float = DEFAULT_W_P,
    w_d: float = DEFAULT_W_D,
) -> Tuple[List[VCVUnitSelection], dict]:
    """追補 F1.4-C: VCV unit 選択。候補 = 文脈キー (prev_vowel, mora) 完全一致
    必須（`unit_contexts` = donor_bank_utau.build_donor_bank_utau_vcv が返す
    unit.index -> (prev_vowel, mora)）。完全一致が無い mora は近縁文脈
    （prev_vowel を x 扱い = mora のみ一致）へフォールバックする（発動記録）。
    それでも候補が無い場合（想定外）は全 unit へ最終フォールバックする。

    候補内では音高差 |Δsemitone|（w_p）+ 長さ差（w_d）の従来コストで
    argmin（決定論・tie は unit.index 昇順）。母音不一致ペナルティは文脈キー
    フィルタで自動充足済みのため使わない（設計書 F1.4-C の記述どおり）。
    """
    if not units:
        raise ValueError("units is empty: donor bank に単位が 1 つもない")
    sorted_units = sorted(units, key=lambda u: u.index)

    selections: List[VCVUnitSelection] = []
    n_exact = 0
    n_near_fallback = 0
    n_global_fallback = 0
    fallback_moras: List[Tuple[int, Optional[str], str]] = []

    for i, note in enumerate(targets):
        exact = [u for u in sorted_units if unit_contexts.get(u.index) == (note.prev_vowel, note.mora)]
        if exact:
            candidates = exact
            match_kind = "exact"
        else:
            near = [u for u in sorted_units if unit_contexts.get(u.index, (None, ""))[1] == note.mora]
            if near:
                candidates = near
                match_kind = "near"
                n_near_fallback += 1
                fallback_moras.append((i, note.prev_vowel, note.mora))
            else:
                candidates = list(sorted_units)
                match_kind = "global"
                n_global_fallback += 1
                fallback_moras.append((i, note.prev_vowel, note.mora))

        if match_kind == "exact":
            n_exact += 1

        best: Optional[Tuple[float, DonorUnit, float, float]] = None
        for u in candidates:  # sorted_units 由来なので index 昇順を保つ
            cp = _semitone_dist(u.median_f0, note.pitch_hz)
            cd = abs(float(np.log(max(u.duration_s, 1e-6) / max(note.duration_sec, 1e-6))))
            total = w_p * cp + w_d * cd
            if best is None or total < best[0]:
                best = (total, u, cp, cd)

        assert best is not None
        total, u, cp, cd = best
        selections.append(
            VCVUnitSelection(
                note_index=i, unit=u, cost_pitch=cp, cost_duration=cd, cost_total=total,
                n_candidates=len(candidates), prev_vowel_target=note.prev_vowel, mora_target=note.mora,
                match_kind=match_kind,
            )
        )

    stats = dict(
        n_notes=len(targets), n_exact=n_exact, n_near_fallback=n_near_fallback,
        n_global_fallback=n_global_fallback, fallback_notes=fallback_moras,
    )
    return selections, stats


def _linear_resample_frames(x: np.ndarray, out_len: int) -> np.ndarray:
    """frame 軸の線形リサンプル（各周波数ビンごとに独立に線形補間）。"""
    n = x.shape[0]
    if out_len <= 0:
        out_len = 1
    if n == 1:
        return np.repeat(x, out_len, axis=0)
    src_pos = np.linspace(0.0, n - 1, out_len)
    idx0 = np.floor(src_pos).astype(np.int64)
    idx1 = np.minimum(idx0 + 1, n - 1)
    w = (src_pos - idx0)[:, None]
    return x[idx0] * (1.0 - w) + x[idx1] * w


def _ping_pong_pad(seg: np.ndarray, deficit: int) -> Tuple[np.ndarray, int]:
    """[v1・テスト互換のため残置。render.py からは呼ばれない]
    seg（中央 50% 区間）を往復（forward/backward 交互）で並べ deficit フレーム分
    作る（硬い折り返し・overlap-add 無し）。追補 F1.3-A item3 で `_ping_pong_pad_v2`
    に置き換えられた。"""
    seg_len = seg.shape[0]
    if seg_len == 0 or deficit <= 0:
        return seg[:0], 0
    pieces = []
    total = 0
    forward = True
    n_cycles = 0
    while total < deficit:
        piece = seg if forward else seg[::-1]
        take = min(seg_len, deficit - total)
        pieces.append(piece[:take])
        total += take
        forward = not forward
        n_cycles += 1
    return np.concatenate(pieces, axis=0), n_cycles


def _ping_pong_pad_v2(
    sp_seg: np.ndarray, ap_seg: np.ndarray, deficit: int, overlap_frames: int
) -> Tuple[np.ndarray, np.ndarray, int]:
    """追補 F1.3-A item3: (sp_seg, ap_seg)（中央 50% 区間）を往復で並べ deficit
    フレーム分作る。折返し境界（forward/backward 交互の継ぎ目）を
    `joins.overlap_add_concat` の true overlap-add で平滑化する（`_ping_pong_pad`
    の硬い折返しを廃止）。ピースが 1 個のみ（deficit <= seg_len）の場合は
    継ぎ目が無いため overlap-add を適用しない。
    """
    seg_len = sp_seg.shape[0]
    if seg_len == 0 or deficit <= 0:
        return sp_seg[:0], ap_seg[:0], 0
    sp_pieces: List[np.ndarray] = []
    ap_pieces: List[np.ndarray] = []
    total = 0
    forward = True
    n_cycles = 0
    while total < deficit:
        sp_piece = sp_seg if forward else sp_seg[::-1]
        ap_piece = ap_seg if forward else ap_seg[::-1]
        take = min(seg_len, deficit - total)
        sp_pieces.append(sp_piece[:take])
        ap_pieces.append(ap_piece[:take])
        total += take
        forward = not forward
        n_cycles += 1
    if len(sp_pieces) == 1:
        return sp_pieces[0], ap_pieces[0], n_cycles
    pairs = list(zip(sp_pieces, ap_pieces))
    # P2 修正 (review #262 R6・`r3789428506`): `overlap_frames` が `seg_len` 以上
    # だと `jn.overlap_add_concat` の内部クランプ（`ov = min(ov_req, prev_len,
    # cur_len)`）で `ov == cur_len` となり、`sp_p[ov:]` が空になって join ごとの
    # 正味増分が 0 フレームになる（何ピース足しても出力が伸びない）。
    # `_loop_pad_raw_target` と同じ実効 overlap（`min(overlap_frames,
    # seg_len - 1)`・下限 0）へクランプしてから渡し、進捗ゼロを構造的に排除する
    # （§ `_effective_loop_overlap` docstring 参照。両関数で同一の実効値を使う
    # ことが前提 —— ここが `_loop_pad_raw_target` の見積りとずれると、過小/過大
    # 生成のどちらかに戻る）。
    effective_overlap = _effective_loop_overlap(seg_len, overlap_frames)
    sp_out, ap_out, _stats = jn.overlap_add_concat(pairs, effective_overlap)
    return sp_out, ap_out, n_cycles


def _effective_loop_overlap(seg_len: int, overlap_frames: int) -> int:
    """P2 修正 (review #262 R6): ループ piece 長 `seg_len` に対する実効 overlap。

    `overlap_frames` をそのまま `seg_len` 以上の値で使うと、
    `jn.overlap_add_concat` が `ov` を `cur_len`（= `seg_len`）へクランプし、
    piece 全体がブレンド区間に飲まれて join ごとの正味増分が 0 になる
    （`_ping_pong_pad_v2`/`_loop_pad_raw_target` 双方が影響を受ける）。
    `seg_len - 1`（下限 0 = 非重畳連結へ縮退）へ制限し、`sp_p[ov:]` に
    最低 1 フレームは残ることを構造的に保証する。
    """
    if seg_len <= 0:
        return 0
    return max(0, min(overlap_frames, seg_len - 1))


def _loop_pad_raw_target(seg_len: int, deficit: int, overlap_frames: int) -> int:
    """P2 修正: 往復ループ材の overlap 損失補償。

    `_ping_pong_pad_v2` はピース間 join のたびに実効 overlap
    （`_effective_loop_overlap` 参照）分だけ尺を縮める（true overlap-add）。
    さらにその結果 (`sp_pad`) は `sp_base` との base<->pad join でもう一度
    `overlap_frames` 分縮む。単純に `deficit` ちょうどの生ループ材を作ると、
    この 2 段の overlap 損失（内部 join 損失 + base 接合損失）の分だけ最終
    出力が `target_n_frames` に届かず、呼び出し側の `_force_length` が末尾
    フレーム複製（凍結プラトー）で不足を埋めることになる（実測: 10 倍伸長で
    末尾 325ms が完全な静止フレームになるバグ。P2 review）。

    `seg_len <= overlap_frames`（短い unit を大きく伸長する場合に発生）では
    旧実装の `max(seg_len - overlap_frames, 1)` が実際の正味増分（0 —— 上記
    `_effective_loop_overlap` 参照）と乖離し、常に過小な `n_cycles_needed` を
    返して凍結プラトーが再発していた（review #262 R6・`r3789428506`）。
    `_ping_pong_pad_v2` と同じ `_effective_loop_overlap` で正味増分を見積もる
    ことで、この場合も `net_per_cycle >= 1` が構造的に保証される。

    base 接合損失は `_ping_pong_pad_v2` 内部の実効 overlap ではなく、
    呼び出し側（`resolve_unit_to_note`）が渡す**素の** `overlap_frames`
    そのもの（`sp_pad` は通常 `overlap_frames` より十分長いためクランプ
    されない）。`seg_len <= overlap_frames` の場合、この base 接合損失
    （`overlap_frames`）が旧来の安全マージン（ピース 1 個分 = `seg_len`
    フレーム）を上回りうるため、単純な「+1 ピース」だけでは埋め合わせが
    不足し、`_force_length` が数フレームの凍結補填を残すことがあった
    （review #262 R6 の実測: `seg_len=6・overlap_frames=8` で 2 フレーム
    残存）。ここでは `deficit + overlap_frames - seg_len` を正味増分で
    ちょうど埋め合わせる周回数を求め、さらに 1 ピース分の余剰マージンを
    積む（呼び出し側の `_force_length` が `n > target_len` の切り詰め分岐
    （`x[:target_len]`）で target へトリムするため、生成過多は往復ループの
    周期性を保ったまま末尾側を捨てるだけでフリーズは発生しない）。
    """
    if seg_len <= 0 or deficit <= 0:
        return deficit
    effective_overlap = _effective_loop_overlap(seg_len, overlap_frames)
    net_per_cycle = max(seg_len - effective_overlap, 1)  # 1 ピース追加あたりの正味増分（圧縮後）
    # 内部 join 損失を吸収した後、さらに base 接合損失（overlap_frames）分の
    # 「正味増分の積み増し」が必要（seg_len 分は最初のピースで既に確保済み）。
    needed_extra = deficit + overlap_frames - seg_len
    n_cycles_needed = -(-needed_extra // net_per_cycle) if needed_extra > 0 else 0  # ceil division
    return (n_cycles_needed + 2) * seg_len  # 初期 1 ピース + 端数/base 接合の安全マージン 1 ピース


def _force_length(x: np.ndarray, target_len: int) -> np.ndarray:
    n = x.shape[0]
    if n == target_len:
        return x
    if n > target_len:
        return x[:target_len]
    pad_n = target_len - n
    if n == 0:
        return np.zeros((target_len,) + x.shape[1:], dtype=x.dtype)
    pad = np.repeat(x[-1:], pad_n, axis=0)
    return np.concatenate([x, pad], axis=0)


@dataclass(frozen=True)
class ResolvedSegment:
    note_index: int
    sp: np.ndarray  # (n_frames, n_bins)
    ap: np.ndarray  # (n_frames, n_bins)
    n_frames: int
    true_ratio: float
    applied_ratio: float
    cap_mode: str  # "none" | "extended_looped" | "compressed_truncated"
    n_loop_cycles: int


def resolve_unit_to_note(
    bank: DonorBank, unit: DonorUnit, target_n_frames: int, note_index: int = -1,
    frame_period_ms: float = 5.0,
) -> ResolvedSegment:
    """donor unit を note の目標フレーム長へ尺合わせする。

    比率 target/unit_len を [0.5, 2.0] にキャップして線形リサンプルする。
    2.0 を超える長音は、キャップ後の unit 中央 50% 区間を往復ループして
    不足分を延伸する（追補 F1.3-A item3: 折返し境界・sp_base との接続境界とも
    `joins.overlap_add_concat` の true overlap-add で平滑化する）。0.5 未満
    （過圧縮）はキャップ後の結果を先頭から target 長へ切り詰める（最終フレーム
    で不足すれば末尾フレームを保持して埋める。設計書に明記のない edge case の
    ため実装決定・record 記録）。`frame_period_ms` はループ overlap 長（既定
    40ms）をフレーム数へ換算するためだけに使う（既定値はプロジェクト全体で
    使われる 5ms・全既存呼び出しは省略可）。
    """
    unit_len = unit.end_frame - unit.start_frame
    if unit_len <= 0:
        raise ValueError(f"degenerate unit (empty frame range): {unit}")
    target_n_frames = max(target_n_frames, 1)

    sp_src = bank.sp[unit.start_frame:unit.end_frame]
    ap_src = bank.ap[unit.start_frame:unit.end_frame]

    true_ratio = target_n_frames / unit_len
    applied_ratio = min(max(true_ratio, STRETCH_RATIO_MIN), STRETCH_RATIO_MAX)
    base_len = max(1, int(round(unit_len * applied_ratio)))
    sp_base = _linear_resample_frames(sp_src, base_len)
    ap_base = _linear_resample_frames(ap_src, base_len)

    n_loop_cycles = 0
    if true_ratio > STRETCH_RATIO_MAX:
        cap_mode = "extended_looped"
        deficit = target_n_frames - base_len
        center_lo = base_len // 4
        center_hi = base_len - base_len // 4
        if center_hi <= center_lo:
            center_hi = min(base_len, center_lo + 1)
        loop_overlap_frames = max(1, int(round(LOOP_OVERLAP_MS / frame_period_ms)))
        pad_raw_target = _loop_pad_raw_target(center_hi - center_lo, deficit, loop_overlap_frames)
        sp_pad, ap_pad, n_loop_cycles = _ping_pong_pad_v2(
            sp_base[center_lo:center_hi], ap_base[center_lo:center_hi], pad_raw_target, loop_overlap_frames
        )
        sp_out, ap_out, _join_stats = jn.overlap_add_concat(
            [(sp_base, ap_base), (sp_pad, ap_pad)], loop_overlap_frames
        )
    elif true_ratio < STRETCH_RATIO_MIN:
        cap_mode = "compressed_truncated"
        sp_out = sp_base[:target_n_frames]
        ap_out = ap_base[:target_n_frames]
    else:
        cap_mode = "none"
        sp_out, ap_out = sp_base, ap_base

    sp_out = _force_length(sp_out, target_n_frames)
    ap_out = _force_length(ap_out, target_n_frames)

    return ResolvedSegment(
        note_index=note_index, sp=sp_out, ap=ap_out, n_frames=target_n_frames,
        true_ratio=true_ratio, applied_ratio=applied_ratio, cap_mode=cap_mode,
        n_loop_cycles=n_loop_cycles,
    )


# ---------------------------------------------------------------------------
# 追補 F1.4-A/5: VCV unit の尺合わせ（録音済み調音遷移は伸縮せず保持し、
# 母音定常部のみ伸縮/往復ループする）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedVCVSegment:
    note_index: int
    sp: np.ndarray  # (n_frames, n_bins)
    ap: np.ndarray  # (n_frames, n_bins)
    n_frames: int
    head_frames: int  # 録音済み調音遷移（伸縮なし）の長さ
    core_true_ratio: float
    core_applied_ratio: float
    cap_mode: str  # "none" | "extended_looped" | "compressed_truncated" | "degenerate_empty_core"
    n_loop_cycles: int
    head_clipped: bool  # 目標長が調音遷移長未満の縮退ケース（発動を記録すること）


def resolve_vcv_unit_to_note(
    bank: DonorBank, unit: DonorUnit, target_n_frames: int, note_index: int = -1,
    frame_period_ms: float = 5.0,
) -> ResolvedVCVSegment:
    """追補 F1.4-A item1 / F1.4 §5: VCV unit を note の目標フレーム長へ尺合わせする。

    録音済み調音遷移（unit 先頭 〜 `vowel_core_start_frame`）は**伸縮せず**その
    まま保持する（「録音済み調音遷移を切らずに保持する」= VCV 銀行の資産をその
    まま使う）。母音定常部（`vowel_core_start_frame` 〜 unit 終端）のみを
    `resolve_unit_to_note` と同じ機構（[0.5,2.0] キャップ + 往復ループの
    overlap-add）で目標残長へ伸縮する。両者を単純連結する（境界は同一の連続
    録音由来のため、伸縮による時間ワープはあっても振幅・スペクトルの不連続は
    生じない・[実装決定]）。

    `unit.vowel_core_start_frame` が None（後方互換）の場合は境界 0 として
    扱う（= unit 全体が母音定常部・実質 `resolve_unit_to_note` と同じ挙動）。

    目標長が調音遷移長未満（縮退ケース。実コーパスでは note 最短でも
    調音遷移よりずっと長いため通常発動しない）は調音遷移を先頭から
    `target_n_frames - 1` フレームへ切り詰め、母音定常部に最低 1 フレームを
    残す（`head_clipped=True` で record 記録対象・設計書に明記のない edge case
    のため実装決定）。
    """
    unit_len = unit.end_frame - unit.start_frame
    if unit_len <= 0:
        raise ValueError(f"degenerate unit (empty frame range): {unit}")
    target_n_frames = max(target_n_frames, 1)

    vcore_rel = unit.vowel_core_start_frame if unit.vowel_core_start_frame is not None else 0
    vcore_rel = max(0, min(vcore_rel, unit_len))

    head_len = vcore_rel
    head_clipped = False
    if head_len >= target_n_frames:
        head_len = max(0, target_n_frames - 1)
        head_clipped = True

    head_sp = bank.sp[unit.start_frame:unit.start_frame + head_len]
    head_ap = bank.ap[unit.start_frame:unit.start_frame + head_len]

    core_target = max(1, target_n_frames - head_len)
    core_src_sp = bank.sp[unit.start_frame + vcore_rel:unit.end_frame]
    core_src_ap = bank.ap[unit.start_frame + vcore_rel:unit.end_frame]
    core_len = core_src_sp.shape[0]

    if core_len <= 0:
        # 縮退（母音定常部が空）: 直前フレーム（無ければ調音遷移末尾）を保持して埋める。
        fill_sp = bank.sp[unit.end_frame - 1:unit.end_frame]
        fill_ap = bank.ap[unit.end_frame - 1:unit.end_frame]
        if fill_sp.shape[0] == 0:
            fill_sp = np.zeros((1, bank.sp.shape[1]), dtype=bank.sp.dtype)
            fill_ap = np.zeros((1, bank.ap.shape[1]), dtype=bank.ap.dtype)
        core_sp = np.repeat(fill_sp, core_target, axis=0)
        core_ap = np.repeat(fill_ap, core_target, axis=0)
        true_ratio = 0.0
        applied_ratio = 0.0
        cap_mode = "degenerate_empty_core"
        n_loop_cycles = 0
    else:
        true_ratio = core_target / core_len
        applied_ratio = min(max(true_ratio, STRETCH_RATIO_MIN), STRETCH_RATIO_MAX)
        base_len = max(1, int(round(core_len * applied_ratio)))
        sp_base = _linear_resample_frames(core_src_sp, base_len)
        ap_base = _linear_resample_frames(core_src_ap, base_len)

        n_loop_cycles = 0
        if true_ratio > STRETCH_RATIO_MAX:
            cap_mode = "extended_looped"
            deficit = core_target - base_len
            center_lo = base_len // 4
            center_hi = base_len - base_len // 4
            if center_hi <= center_lo:
                center_hi = min(base_len, center_lo + 1)
            loop_overlap_frames = max(1, int(round(LOOP_OVERLAP_MS / frame_period_ms)))
            pad_raw_target = _loop_pad_raw_target(center_hi - center_lo, deficit, loop_overlap_frames)
            sp_pad, ap_pad, n_loop_cycles = _ping_pong_pad_v2(
                sp_base[center_lo:center_hi], ap_base[center_lo:center_hi], pad_raw_target, loop_overlap_frames
            )
            core_sp, core_ap, _stats = jn.overlap_add_concat(
                [(sp_base, ap_base), (sp_pad, ap_pad)], loop_overlap_frames
            )
        elif true_ratio < STRETCH_RATIO_MIN:
            cap_mode = "compressed_truncated"
            core_sp = sp_base[:core_target]
            core_ap = ap_base[:core_target]
        else:
            cap_mode = "none"
            core_sp, core_ap = sp_base, ap_base

        core_sp = _force_length(core_sp, core_target)
        core_ap = _force_length(core_ap, core_target)

    sp_out = np.concatenate([head_sp, core_sp], axis=0) if head_len > 0 else core_sp
    ap_out = np.concatenate([head_ap, core_ap], axis=0) if head_len > 0 else core_ap
    sp_out = _force_length(sp_out, target_n_frames)
    ap_out = _force_length(ap_out, target_n_frames)

    return ResolvedVCVSegment(
        note_index=note_index, sp=sp_out, ap=ap_out, n_frames=target_n_frames,
        head_frames=head_len, core_true_ratio=true_ratio, core_applied_ratio=applied_ratio,
        cap_mode=cap_mode, n_loop_cycles=n_loop_cycles, head_clipped=head_clipped,
    )
