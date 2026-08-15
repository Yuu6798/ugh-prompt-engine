"""adapter/joins.py — VG-F1: 単位接合（WORLD パラメータ領域のクロスフェード）。

設計書 §2 joins.py + 追補 F1.3-A に対応する。f0 はここでは扱わない（ドナー由来を
使わず、全区間 perf_genes が生成する = F2 軸の単離）。

v1（`assemble` / `CROSSFADE_MS`）: 各 note を resolve 済み frame 長でそのまま
[start,end) に書き込み（総フレーム数は timeline 由来でそのまま保存・縮めない）、
境界直前の frame を対象に単側ブレンド（直前 unit の末尾フレームを、直後 unit の
先頭フレームへ向けて上書きブレンド）する。F1.2 耳判定（息の切れ・接ぎの粗さ）の
原因のひとつと特定された（`results_f1_2/f1_2_record_2026-08-15.md` 耳判定節）。
**render.py からは呼ばれない**（テスト互換のため関数として残す。追補 F1.3-A）。

v2（`assemble_v2`）: true overlap-add。隣接 note（同一フレーズ内、breath を
挟まない）を `overlap_frames` 分だけ実際に重ねて配置し（総フレーム数が
overlap 分だけ短くなる = タイムライン総尺の重なり補正）、重なり区間で
log-sp 線形 + ap 線形のクロスフェードを行う。overlap 長は選択された donor
unit の oto overlap（UTAU バンク）を優先し、情報が無い unit（vocadito/pjs）は
`DEFAULT_OVERLAP_MS`（固定 40ms）を使う。長音ループの折返し境界にも同じ
overlap-add 機構を `units.py` から `overlap_add_concat` 経由で流用する。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

CROSSFADE_MS = 30.0  # v1（旧・単側ブレンド）専用。v2 は DEFAULT_OVERLAP_MS を使う。
DEFAULT_OVERLAP_MS = 40.0  # 追補 F1.3-A item2: oto 情報を持たない銀行の固定 overlap 長
EPS = 1e-8


@dataclass(frozen=True)
class NotePlacement:
    start_frame: int
    end_frame: int  # exclusive
    sp: np.ndarray  # (n_frames, n_bins)  resolve 済み
    ap: np.ndarray  # (n_frames, n_bins)
    has_join_to_prev: bool  # True: 直前 note と同一フレーズ内で連続（breath を挟まない）
    # 追補 F1.3-A: この note を「前の note」へ接合する際の overlap 長（フレーム）。
    # None = oto overlap 情報なし（assemble_v2 が DEFAULT_OVERLAP_MS へフォールバック）。
    # v1 の `assemble` はこのフィールドを無視する（後方互換）。
    overlap_frames: Optional[int] = None
    # P1 修正 (review #262 R2): この note 自身の oto preutterance（フレーム）。
    # None = 情報なし（vocadito/pjs。後方互換で追加トリム無し）。VCV unit
    # （donor_bank_utau）のみ実値を設定する。`assemble_v2` / `assemble_control_tracks_v2`
    # が「preutterance_frames - 実効 overlap_frames」だけ直前 note の末尾を
    # 追加でハードトリムする際に使う（overlap のブレンド幅そのものは変えず、
    # ブレンドに入る前のアキュムレータだけ先に削る = preutterance 点が
    # 名目上のビート位置へ正しく整列する。§ render.py `_build_vcv_placements` 参照）。
    preutterance_frames: Optional[int] = None


def _carry_forward_gaps(
    sp_seq: np.ndarray, ap_seq: np.ndarray, placements: Sequence[NotePlacement], n_total_frames: int
) -> int:
    covered = np.zeros(n_total_frames, dtype=bool)
    for p in placements:
        covered[p.start_frame:p.end_frame] = True
    if not covered.any():
        return n_total_frames
    first_idx = int(np.argmax(covered))
    if first_idx > 0:
        sp_seq[:first_idx] = sp_seq[first_idx]
        ap_seq[:first_idx] = ap_seq[first_idx]
    n_gap_frames = int((~covered).sum())
    last = first_idx
    for i in range(first_idx, n_total_frames):
        if covered[i]:
            last = i
        else:
            sp_seq[i] = sp_seq[last]
            ap_seq[i] = ap_seq[last]
    return n_gap_frames


def assemble(
    n_total_frames: int,
    n_bins: int,
    placements: Sequence[NotePlacement],
    frame_period_ms: float = 5.0,
    crossfade_ms: float = CROSSFADE_MS,
) -> Tuple[np.ndarray, np.ndarray, dict]:
    sp_seq = np.zeros((n_total_frames, n_bins), dtype=np.float64)
    ap_seq = np.zeros((n_total_frames, n_bins), dtype=np.float64)

    for p in placements:
        sp_seq[p.start_frame:p.end_frame] = p.sp
        ap_seq[p.start_frame:p.end_frame] = p.ap

    n_gap_frames = _carry_forward_gaps(sp_seq, ap_seq, placements, n_total_frames)

    crossfade_frames = max(1, int(round(crossfade_ms / frame_period_ms)))
    n_joins_applied = 0
    n_joins_skipped_short = 0

    for i in range(1, len(placements)):
        cur = placements[i]
        prev = placements[i - 1]
        if not cur.has_join_to_prev:
            continue
        boundary = cur.start_frame
        n = min(crossfade_frames, prev.end_frame - prev.start_frame, cur.end_frame - cur.start_frame)
        if n < 2:
            n_joins_skipped_short += 1
            continue
        w = np.linspace(0.0, 1.0, n)[:, None]
        tail_log = np.log(sp_seq[boundary - n:boundary] + EPS)
        head_log = np.log(cur.sp[:n] + EPS)
        sp_seq[boundary - n:boundary] = np.exp(tail_log * (1.0 - w) + head_log * w)
        ap_seq[boundary - n:boundary] = ap_seq[boundary - n:boundary] * (1.0 - w) + cur.ap[:n] * w
        n_joins_applied += 1

    stats = dict(
        crossfade_frames=crossfade_frames,
        n_joins_applied=n_joins_applied,
        n_joins_skipped_short=n_joins_skipped_short,
        n_gap_frames=n_gap_frames,
        n_placements=len(placements),
    )
    return sp_seq, ap_seq, stats


# ---------------------------------------------------------------------------
# 追補 F1.3-A: v2 true overlap-add
# ---------------------------------------------------------------------------


def overlap_add_concat(
    pieces: Sequence[Tuple[np.ndarray, np.ndarray]],
    overlap_frames: "int | Sequence[int]",
    extra_trim_frames: "int | Sequence[int] | None" = None,
) -> Tuple[np.ndarray, np.ndarray, dict]:
    """(sp, ap) ペアの列を true overlap-add で連結する（汎用ヘルパー）。

    各ピースは独立した音源断片（donor unit・長音ループの往復片など）を想定する。
    隣接ピース間を `overlap_frames`（int なら全 join 共通、Sequence なら
    `len(pieces)-1` 個の join 別値）だけ重ねて配置し、重なり区間を log-sp 線形 +
    ap 線形でクロスフェードする（区間長は各ピース長でクランプ・2 フレーム未満は
    スキップして単純連結にフォールバック）。総フレーム数は overlap 分だけ短く
    なる（真の overlap-add。単側ブレンドとは異なり切り詰めが発生する）。
    `units.py` のループ境界 overlap-add と `joins.assemble_v2` の両方から使う
    共通実装。

    `extra_trim_frames`（P1 修正 review #262 R2）: 各 join の直前に、ブレンドとは
    別に直前アキュムレータ末尾を追加でハードトリムするフレーム数（int なら全 join
    共通、Sequence なら join 別値、None/省略 = 全 join 0 = 従来どおり）。
    `joins.assemble_v2` が VCV unit の preutterance 消費（overlap だけでは
    consume しきれない「preutterance - overlap」分の直前 note 末尾）に使う
    （ブレンド幅=overlap は変えず、ブレンド開始点だけ前へ動かす）。
    """
    if not pieces:
        return np.zeros((0, 0)), np.zeros((0, 0)), dict(
            n_joins_applied=0, n_joins_skipped_short=0, overlap_frames_applied=[],
            n_extra_trim_frames_applied=0,
        )
    n_joins = len(pieces) - 1
    if isinstance(overlap_frames, int):
        overlaps = [overlap_frames] * n_joins
    else:
        overlaps = list(overlap_frames)
        if len(overlaps) != n_joins:
            raise ValueError(f"overlap_frames の長さ {len(overlaps)} != n_joins {n_joins}")
    if extra_trim_frames is None:
        trims = [0] * n_joins
    elif isinstance(extra_trim_frames, int):
        trims = [extra_trim_frames] * n_joins
    else:
        trims = list(extra_trim_frames)
        if len(trims) != n_joins:
            raise ValueError(f"extra_trim_frames の長さ {len(trims)} != n_joins {n_joins}")

    sp_acc, ap_acc = pieces[0][0].copy(), pieces[0][1].copy()
    n_joins_applied = 0
    n_joins_skipped_short = 0
    overlaps_applied: List[int] = []
    n_extra_trim_frames_applied = 0

    for (sp_p, ap_p), ov_req, trim_req in zip(pieces[1:], overlaps, trims):
        prev_len = sp_acc.shape[0]
        trim = max(0, min(trim_req, prev_len))
        if trim > 0:
            sp_acc = sp_acc[:-trim]
            ap_acc = ap_acc[:-trim]
            n_extra_trim_frames_applied += trim
            prev_len -= trim
        cur_len = sp_p.shape[0]
        ov = min(max(ov_req, 0), prev_len, cur_len)
        if ov < 2:
            n_joins_skipped_short += 1
            sp_acc = np.concatenate([sp_acc, sp_p], axis=0)
            ap_acc = np.concatenate([ap_acc, ap_p], axis=0)
            continue
        w = np.linspace(0.0, 1.0, ov)[:, None]
        tail_log = np.log(sp_acc[-ov:] + EPS)
        head_log = np.log(sp_p[:ov] + EPS)
        blended_sp = np.exp(tail_log * (1.0 - w) + head_log * w)
        blended_ap = ap_acc[-ov:] * (1.0 - w) + ap_p[:ov] * w
        sp_acc = np.concatenate([sp_acc[:-ov], blended_sp, sp_p[ov:]], axis=0)
        ap_acc = np.concatenate([ap_acc[:-ov], blended_ap, ap_p[ov:]], axis=0)
        n_joins_applied += 1
        overlaps_applied.append(ov)

    stats = dict(
        n_joins_applied=n_joins_applied,
        n_joins_skipped_short=n_joins_skipped_short,
        overlap_frames_applied=overlaps_applied,
        n_extra_trim_frames_applied=n_extra_trim_frames_applied,
    )
    return sp_acc, ap_acc, stats


# ---------------------------------------------------------------------------
# F1.4 R3: 絶対グリッド配置（総尺の score 準拠維持）
# ---------------------------------------------------------------------------
#
# 背景（設計判定による内部発見・PR #262 R3）: `assemble_v2`/
# `assemble_control_tracks_v2`（すぐ下の節）は run 内の join のたびに直前
# アキュムレータ末尾を実際にトリムする（overlap 分 + preutterance 消費分の
# `extra_trim_frames`）。この「トリムして詰める」方式は run 単体では
# 正しく機能するが、複数 run から成る render 全体では、後続 run の配置が
# 常に naive（score 由来の圧縮前）タイムライン上のギャップ計算に基づくため、
# 各 run の圧縮（縮み）が後続の run 全てへ伝播し、総尺が score の総尺から
# 累積的に縮む（sakura で 19% 相当・record 参照）。
#
# `assemble_absolute`/`assemble_control_tracks_absolute` はこれを「トリムして
# 詰める」のではなく「固定長の絶対タイムラインバッファへ直接配置する」方式へ
# 転換する。`placements[i].start_frame`/`end_frame` は呼び出し側
# （render.py `_build_vcv_placements`）が**あらかじめ**「拍位置 −
# lead（preutterance または overlap フォールバック）」の絶対座標として
# 計算済みである前提とする（トリムはこの関数内では一切行わない）。
# `has_join_to_prev=True` な note が、直前までに書き込み済みの区間へ実際に
# 食い込む場合（lead の結果として時間的に重なる場合）のみ、その重なり区間を
# log-sp/ap クロスフェードでブレンドする（トリムではなく同一時間軸上の
# ブレンド）。重ならない場合（lead が直前 note と釣り合っていて隙間ゼロ、
# または一部だけ隙間が残る場合）は単純上書き/隙間放置（隙間は関数末尾で
# 直前フレームの forward-fill により埋める・v1 `assemble` と同じ規約）。
#
# `donor="ritsu"`（VCV/preutterance 経路）のみがこれらの関数を使う。
# `assemble_v2`/`assemble_control_tracks_v2`（vocadito/pjs 経路）は無変更
# （既存テスト・既存挙動を維持。回帰リスクを avoid）。


def _blend_log_domain(prev_block: np.ndarray, cur_block: np.ndarray, w: np.ndarray) -> np.ndarray:
    tail_log = np.log(prev_block + EPS)
    head_log = np.log(cur_block + EPS)
    return np.exp(tail_log * (1.0 - w) + head_log * w)


def _place_absolute_pair(
    n_total_frames: int,
    n_dims: int,
    items: Sequence[Tuple[int, int, bool, np.ndarray, np.ndarray]],
    gap_fill: str = "carry_forward",
) -> Tuple[np.ndarray, np.ndarray, dict]:
    """`assemble_absolute`/`assemble_control_tracks_absolute` の共通実装。

    `items` の各要素 = `(start_frame, end_frame, has_join_to_prev, primary,
    secondary)`（`primary`/`secondary` は `(n, n_dims)` 配列。sp/ap または
    f0/振幅を単一のインターフェースで扱うための軽量タプル表現）。
    primary は log domain（sp/f0 に自然）、secondary は linear domain
    （ap/振幅に自然）でブレンドする。`gap_fill`: `"carry_forward"`（sp/ap の
    無声区間 = 直前有効フレームで埋める）| `"zero"`（f0/振幅の無声区間 =
    真に 0）。
    """
    primary_seq = np.zeros((n_total_frames, n_dims), dtype=np.float64)
    secondary_seq = np.zeros((n_total_frames, n_dims), dtype=np.float64)
    covered = np.zeros(n_total_frames, dtype=bool)

    n_joins_applied = 0
    n_joins_skipped_short = 0
    overlaps_applied: List[int] = []
    prev_end: Optional[int] = None

    for start_raw, end_raw, has_join, primary, secondary in items:
        start = max(0, min(int(start_raw), n_total_frames))
        end = max(start, min(int(end_raw), n_total_frames))
        n = end - start
        if n <= 0:
            prev_end = end if prev_end is None else max(prev_end, end)
            continue
        primary_unit = primary[:n]
        secondary_unit = secondary[:n]

        ov = 0
        if has_join and prev_end is not None and start < prev_end:
            covered_len = int(covered[start:min(prev_end, n_total_frames)].sum())
            ov = min(prev_end - start, n, covered_len)

        if ov >= 2:
            w = np.linspace(0.0, 1.0, ov)[:, None]
            primary_seq[start:start + ov] = _blend_log_domain(
                primary_seq[start:start + ov], primary_unit[:ov], w
            )
            secondary_seq[start:start + ov] = (
                secondary_seq[start:start + ov] * (1.0 - w) + secondary_unit[:ov] * w
            )
            primary_seq[start + ov:end] = primary_unit[ov:]
            secondary_seq[start + ov:end] = secondary_unit[ov:]
            n_joins_applied += 1
            overlaps_applied.append(ov)
        else:
            if ov == 1:
                n_joins_skipped_short += 1
            primary_seq[start:end] = primary_unit
            secondary_seq[start:end] = secondary_unit

        covered[start:end] = True
        prev_end = end if prev_end is None else max(prev_end, end)

    if gap_fill == "carry_forward":
        if covered.any():
            first_idx = int(np.argmax(covered))
            if first_idx > 0:
                primary_seq[:first_idx] = primary_seq[first_idx]
                secondary_seq[:first_idx] = secondary_seq[first_idx]
            n_gap_frames = int((~covered).sum())
            last = first_idx
            for i in range(first_idx, n_total_frames):
                if covered[i]:
                    last = i
                else:
                    primary_seq[i] = primary_seq[last]
                    secondary_seq[i] = secondary_seq[last]
        else:
            n_gap_frames = n_total_frames
    else:  # "zero": 何もしない（初期値 0 のまま = 無声）
        n_gap_frames = int((~covered).sum())

    stats = dict(
        n_joins_applied=n_joins_applied,
        n_joins_skipped_short=n_joins_skipped_short,
        overlap_frames_applied=overlaps_applied,
        n_gap_frames=n_gap_frames,
        n_total_frames=n_total_frames,
    )
    return primary_seq, secondary_seq, stats


def assemble_absolute(
    n_total_frames: int,
    n_bins: int,
    placements: Sequence[NotePlacement],
) -> Tuple[np.ndarray, np.ndarray, dict]:
    """F1.4 R3: 絶対グリッド配置版の sp/ap 接合（`donor="ritsu"` 専用）。

    `placements[i].start_frame`/`end_frame` は render.py 側で既に
    「拍位置 − lead」の絶対座標として確定済みである前提（このバッファは
    `n_total_frames` 固定・trim しない）。詳細は本節冒頭のコメント参照。
    """
    items = [(p.start_frame, p.end_frame, p.has_join_to_prev, p.sp, p.ap) for p in placements]
    if not items:
        empty = np.zeros((n_total_frames, n_bins), dtype=np.float64)
        return empty.copy(), empty.copy(), dict(
            n_joins_applied=0, n_joins_skipped_short=0, overlap_frames_applied=[],
            n_gap_frames=n_total_frames, n_total_frames=n_total_frames,
        )
    return _place_absolute_pair(n_total_frames, n_bins, items, gap_fill="carry_forward")


def assemble_control_tracks_absolute(
    n_total_frames: int,
    placements: Sequence[NotePlacement],
    note_primary_tracks: Sequence[np.ndarray],
    note_secondary_tracks: Sequence[np.ndarray],
) -> Tuple[np.ndarray, np.ndarray, dict]:
    """F1.4 R3: 絶対グリッド配置版の f0/振幅接合（`assemble_absolute` と同一の
    `start_frame`/`end_frame`/`has_join_to_prev` を使う。run 間ギャップは
    sp/ap と異なり 0（無声）で埋める（`assemble_control_tracks_v2` と同じ
    意味論）。
    """
    if not placements:
        empty = np.zeros(n_total_frames, dtype=np.float64)
        return empty.copy(), empty.copy(), dict(
            n_joins_applied=0, n_joins_skipped_short=0, overlap_frames_applied=[],
            n_gap_frames=n_total_frames, n_total_frames=n_total_frames,
        )
    items = [
        (
            placements[i].start_frame, placements[i].end_frame, placements[i].has_join_to_prev,
            note_primary_tracks[i].reshape(-1, 1), note_secondary_tracks[i].reshape(-1, 1),
        )
        for i in range(len(placements))
    ]
    primary2d, secondary2d, stats = _place_absolute_pair(n_total_frames, 1, items, gap_fill="zero")
    return primary2d[:, 0], secondary2d[:, 0], stats


def _resolve_join_overlap(requested: Optional[int], default_frames: int) -> int:
    return requested if requested is not None and requested >= 0 else default_frames


def _resolve_extra_trim(preutterance_frames: Optional[int], resolved_overlap_frames: int) -> int:
    """P1 修正 (review #262 R2): join の追加ハードトリム量。

    `preutterance_frames` が無い unit（vocadito/pjs）は常に 0（従来どおり
    overlap のみで接合・後方互換）。VCV unit で `preutterance_frames` が
    `resolved_overlap_frames`（実効 overlap）を上回る分だけ、ブレンド幅とは
    別に直前アキュムレータ末尾を追加でハードトリムする（overlap のみ消費する
    従来実装は preutterance 点が `preutterance_frames - overlap_frames` だけ
    名目ビートより遅延していた = review #262 R2 P1 指摘）。
    """
    if preutterance_frames is None:
        return 0
    return max(0, preutterance_frames - resolved_overlap_frames)


def compute_runs(placements: Sequence[NotePlacement]) -> List[List[int]]:
    """`placements` を「フレーズ内で連続する note（run）」へグルーピングする
    （`has_join_to_prev=True` の連鎖）。`assemble_v2` と
    `assemble_control_tracks_v2` の両方が同じグルーピングを使うための共通
    ヘルパー（P1 修正: f0/振幅トラックを sp/ap と同一の run 構造・同一の
    overlap 圧縮で再構築するために、run 分割ロジックの二重実装を避ける）。
    """
    if not placements:
        return []
    runs: List[List[int]] = [[0]]
    for i in range(1, len(placements)):
        if placements[i].has_join_to_prev:
            runs[-1].append(i)
        else:
            runs.append([i])
    return runs


def assemble_control_tracks_v2(
    placements: Sequence[NotePlacement],
    note_primary_tracks: Sequence[np.ndarray],
    note_secondary_tracks: Sequence[np.ndarray],
    frame_period_ms: float = 5.0,
    default_overlap_ms: float = DEFAULT_OVERLAP_MS,
) -> Tuple[np.ndarray, np.ndarray, dict]:
    """P1 修正 (review #262): f0/振幅などの制御トラックを、sp/ap と**同一の**
    圧縮後タイムライン（`assemble_v2` が作る run 構造・overlap 量）へ frame
    単位で正しく整列させて再構築する。

    バグの背景: 旧実装は `base_f0_persample`/`amp_env` を「圧縮前の生スコア
    サンプル時間軸」で構築し、圧縮後フレーム数 `n_total_frames` から作った
    素朴な等間隔インデックスでそれを読み出していた。`assemble_v2` は
    フレーズ内 run を true overlap-add で圧縮する（join のたびに総尺が
    overlap 分だけ縮む）ため、この読み出しは join を経るたびにズレが累積する
    （render.py:377-398 で確認された P1 バグ）。

    本関数は sp/ap と全く同じ `compute_runs` グルーピング + 同じ
    `_resolve_join_overlap` による overlap 長決定を使い、各 note の
    per-frame トラック（`note_primary_tracks[i]`/`note_secondary_tracks[i]`、
    各要素は `placements[i].sp.shape[0]` と同じフレーム数）を
    `overlap_add_concat` へ渡す（sp と同じ log ブレンド / ap と同じ線形
    ブレンドを流用: primary=log domain（f0 に自然）、secondary=linear
    domain（振幅に自然））。run 間ギャップ（フレーズ間ブレス等）は
    carry-forward せず 0 埋めする（sp/ap の carry-forward はスペクトル包絡の
    連続性のためだが、f0/振幅は無声区間で真に 0 であるべきため = 元の
    per-sample トラックが breath 区間で自然に 0 になっているのと同じ意味論）。

    戻り値の系列長は sp_seq/ap_seq の `n_total_frames` と厳密に一致する
    （同一の run 構造・同一の gap 長算出のため）。
    """
    if not placements:
        return np.zeros(0), np.zeros(0), dict(n_total_frames=0)

    default_overlap_frames = max(1, int(round(default_overlap_ms / frame_period_ms)))
    runs = compute_runs(placements)

    primary_chunks: List[np.ndarray] = []
    secondary_chunks: List[np.ndarray] = []
    prev_naive_end: Optional[int] = None

    for run in runs:
        pieces = [
            (note_primary_tracks[i].reshape(-1, 1), note_secondary_tracks[i].reshape(-1, 1)) for i in run
        ]
        join_overlaps = [
            _resolve_join_overlap(placements[i].overlap_frames, default_overlap_frames) for i in run[1:]
        ]
        # P1 修正 (review #262 R2): sp/ap（assemble_v2）と全く同じ extra_trim を
        # 適用する（preutterance 消費で run 内総尺が変わるため、f0/振幅トラックの
        # フレーム数を sp/ap と厳密一致させるには同じトリム量が必須）。
        join_extra_trims = [
            _resolve_extra_trim(placements[i].preutterance_frames, ov)
            for i, ov in zip(run[1:], join_overlaps)
        ]
        primary_block2d, secondary_block2d, _run_stats = overlap_add_concat(
            pieces, join_overlaps, join_extra_trims
        )
        primary_block = primary_block2d[:, 0]
        secondary_block = secondary_block2d[:, 0]

        naive_start = placements[run[0]].start_frame
        if prev_naive_end is None:
            if naive_start > 0:
                primary_chunks.append(np.zeros(naive_start))
                secondary_chunks.append(np.zeros(naive_start))
        else:
            gap = max(0, naive_start - prev_naive_end)
            if gap > 0:
                primary_chunks.append(np.zeros(gap))
                secondary_chunks.append(np.zeros(gap))
        primary_chunks.append(primary_block)
        secondary_chunks.append(secondary_block)
        prev_naive_end = placements[run[-1]].end_frame

    primary_seq = np.concatenate(primary_chunks, axis=0)
    secondary_seq = np.concatenate(secondary_chunks, axis=0)
    return primary_seq, secondary_seq, dict(n_total_frames=int(primary_seq.shape[0]))


def assemble_v2(
    n_bins: int,
    placements: Sequence[NotePlacement],
    frame_period_ms: float = 5.0,
    default_overlap_ms: float = DEFAULT_OVERLAP_MS,
) -> Tuple[np.ndarray, np.ndarray, dict]:
    """追補 F1.3-A: true overlap-add 版の接合（render.py はこちらのみを使う）。

    `placements` の start_frame/end_frame は render.py が overlap を考慮せず
    素朴に連結した「圧縮前」タイムライン座標（既存のカーソル方式そのまま）で
    渡ってくる。本関数はフレーズ内で連続する note（`has_join_to_prev=True`）を
    「run」としてグルーピングし、run 内は `overlap_add_concat` で true
    overlap-add 連結する（総尺がその分だけ縮む = タイムライン総尺の重なり
    補正）。run と run の間（フレーズ間ブレス等）は従来どおり gap を保持し、
    直前の有効フレームで carry-forward する。overlap 長は
    `placements[i].overlap_frames`（選択された donor unit の oto overlap）を
    優先し、None なら `default_overlap_ms`（固定 40ms）を使う。

    P1 修正 (review #262 R2): `placements[i].preutterance_frames` が設定されて
    いる場合（VCV unit）、overlap のブレンド幅そのものは変えず、ブレンド開始点の
    直前に `preutterance_frames - 実効overlap_frames` フレーム分だけ直前
    アキュムレータ末尾を追加でハードトリムする（`_resolve_extra_trim` /
    `overlap_add_concat` の `extra_trim_frames`）。これにより unit 自身の
    preutterance フレーム（真の母音アタック点）が、直前 note までの累積長
    （= 名目上のビート位置）にちょうど整列する（従来は overlap のみ消費して
    いたため `preutterance_frames - overlap_frames` 分だけ名目ビートより遅延
    していた）。`preutterance_frames=None`（vocadito/pjs）は常に trim=0 で
    従来どおり不変。
    """
    if not placements:
        return np.zeros((0, n_bins)), np.zeros((0, n_bins)), dict(
            n_joins_applied=0, n_joins_skipped_short=0, n_gap_frames=0,
            n_placements=0, overlap_frames_applied=[], overlap_frames_mean=0.0,
            default_overlap_frames=max(1, int(round(default_overlap_ms / frame_period_ms))),
            n_runs=0, n_total_frames=0, n_extra_trim_frames_applied=0,
        )

    default_overlap_frames = max(1, int(round(default_overlap_ms / frame_period_ms)))

    runs = compute_runs(placements)

    n_joins_applied = 0
    n_joins_skipped_short = 0
    overlaps_applied: List[int] = []
    n_extra_trim_frames_applied = 0
    run_blocks: List[Tuple[np.ndarray, np.ndarray]] = []

    for run in runs:
        pieces = [(placements[i].sp, placements[i].ap) for i in run]
        join_overlaps = [
            _resolve_join_overlap(placements[i].overlap_frames, default_overlap_frames) for i in run[1:]
        ]
        join_extra_trims = [
            _resolve_extra_trim(placements[i].preutterance_frames, ov)
            for i, ov in zip(run[1:], join_overlaps)
        ]
        sp_block, ap_block, run_stats = overlap_add_concat(pieces, join_overlaps, join_extra_trims)
        run_blocks.append((sp_block, ap_block))
        n_joins_applied += run_stats["n_joins_applied"]
        n_joins_skipped_short += run_stats["n_joins_skipped_short"]
        overlaps_applied.extend(run_stats["overlap_frames_applied"])
        n_extra_trim_frames_applied += run_stats["n_extra_trim_frames_applied"]

    sp_chunks: List[np.ndarray] = []
    ap_chunks: List[np.ndarray] = []
    prev_naive_end: Optional[int] = None
    n_gap_frames = 0

    for run, (sp_block, ap_block) in zip(runs, run_blocks):
        naive_start = placements[run[0]].start_frame
        if prev_naive_end is None:
            # 先頭の未配置ギャップ（あれば）は最初の有効フレームで forward-fill
            # する（v1 の _carry_forward_gaps と同じ規約）。
            if naive_start > 0:
                n_gap_frames += naive_start
                sp_chunks.append(np.repeat(sp_block[:1], naive_start, axis=0))
                ap_chunks.append(np.repeat(ap_block[:1], naive_start, axis=0))
        else:
            gap = max(0, naive_start - prev_naive_end)
            if gap > 0:
                n_gap_frames += gap
                last_sp = sp_chunks[-1][-1:]
                last_ap = ap_chunks[-1][-1:]
                sp_chunks.append(np.repeat(last_sp, gap, axis=0))
                ap_chunks.append(np.repeat(last_ap, gap, axis=0))
        sp_chunks.append(sp_block)
        ap_chunks.append(ap_block)
        prev_naive_end = placements[run[-1]].end_frame

    sp_seq = np.concatenate(sp_chunks, axis=0)
    ap_seq = np.concatenate(ap_chunks, axis=0)

    stats = dict(
        n_joins_applied=n_joins_applied,
        n_joins_skipped_short=n_joins_skipped_short,
        n_gap_frames=n_gap_frames,
        n_placements=len(placements),
        n_runs=len(runs),
        overlap_frames_applied=overlaps_applied,
        overlap_frames_mean=float(np.mean(overlaps_applied)) if overlaps_applied else 0.0,
        default_overlap_frames=default_overlap_frames,
        n_total_frames=int(sp_seq.shape[0]),
        n_extra_trim_frames_applied=n_extra_trim_frames_applied,
    )
    return sp_seq, ap_seq, stats
