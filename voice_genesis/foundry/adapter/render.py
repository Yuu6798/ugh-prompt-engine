"""adapter/render.py — VG-F1 パイプライン: score -> f0/units/joins/warp -> WORLD -> WAV。

設計書 §2 render.py に対応する。

CLI:
    python -m adapter.render --score sakura --voice presets/neutral.json \
        --wav <vocadito_2.wav> --notes-csv <vocadito_2_notesA1.csv> --out x.wav

同一 spec(JSON) + seed -> 同一バイト列（決定論契約。乱数は perf_genes 側のみで
`np.random.default_rng(seed)` を使用し、他に非決定要素（wall-clock 等）を
含まない）。
"""
from __future__ import annotations

import argparse
import dataclasses
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pyworld as pw
import soundfile as sf

_HERE = Path(__file__).resolve().parent
_SINGER_DIR = _HERE.parent.parent / "singer"
for _p in (_SINGER_DIR, _HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import score as sc  # noqa: E402  (singer、read-only import)
import score_umi as sc_umi  # noqa: E402  (singer、read-only import)
import performance as perf  # noqa: E402  (singer、read-only import)

import consonants as cons  # noqa: E402  (追補 F1.1-B)
import donor_bank as db  # noqa: E402
import donor_bank_lab as dbl  # noqa: E402  (追補 F1.2-C)
import donor_bank_utau as dbu  # noqa: E402  (追補 F1.2-B)
import joins as jn  # noqa: E402
import perf_genes as pg  # noqa: E402
import units as un  # noqa: E402
import voice_spec as vs  # noqa: E402
import vowel_class as vc  # noqa: E402  (追補 F1.1-A)

SR = 24000
FRAME_PERIOD_MS = 5.0
PEAK_NORM = 0.6

DONOR_CHOICES = ("vocadito", "ritsu", "pjs")
CONSONANT_SOURCE_CHOICES = ("recorded", "synthetic", "none")


def _vowel_distribution_from_labels(labels: dict) -> dict:
    """unit.index -> ラベル(str) の辞書から母音別件数分布を作る（3 ドナー共通）。"""
    dist: Dict[str, int] = {}
    for lbl in labels.values():
        dist[lbl] = dist.get(lbl, 0) + 1
    return dist


def _vcv_required_contexts(segments: list) -> List[Tuple[Optional[str], str]]:
    """追補 F1.4-A: レンダリング対象スコアが実際に必要とする VCV 文脈キーの
    重複無し決定論順リスト（`donor_bank_utau.build_donor_bank_utau_vcv` の
    `required_contexts` へそのまま渡す・キャッシュキー材料にも使う）。
    """
    seen: List[Tuple[Optional[str], str]] = []
    seen_set = set()
    for ctx in un.vcv_context_sequence(segments):
        if ctx not in seen_set:
            seen_set.add(ctx)
            seen.append(ctx)
    seen.sort(key=lambda c: (c[0] or "", c[1]))
    return seen


def prepend_recorded_consonant(
    resolved: "un.ResolvedSegment", clip: "dbu.ConsonantClip", note_index: int
) -> Tuple["un.ResolvedSegment", "cons.ConsonantEvent"]:
    """追補 F1.2-D: 録音済み子音クリップ（自然長・非伸縮）を resolved 母音核
    frame 列の先頭へ前置する。総 frame 数はクリップ分だけ増える
    （joins.assemble の 30ms クロスフェードが後続の接続を担う。決定論・純関数）。
    """
    new_sp = np.concatenate([clip.sp, resolved.sp], axis=0)
    new_ap = np.concatenate([clip.ap, resolved.ap], axis=0)
    new_resolved = dataclasses.replace(resolved, sp=new_sp, ap=new_ap, n_frames=new_sp.shape[0])
    event = cons.ConsonantEvent(
        note_index=note_index, onset=clip.onset, consonant_class="recorded", n_frames_processed=clip.n_frames,
    )
    return new_resolved, event


def _build_vcv_placements(
    segments: list, selections: List["un.VCVUnitSelection"], bank: "db.DonorBank",
) -> Tuple[List["jn.NotePlacement"], List["un.ResolvedVCVSegment"], dict]:
    """追補 F1.4-B: VCV unit の配置。接合は F1.3 と同じ機構（unit 自身の
    oto overlap を join 長として使う true overlap-add・`joins.assemble_v2`
    がフレーズ内 run 単位で処理する）を続投する。新規なのは
    **フレーズ先頭ノートの preutterance 消費**（item2/item3）: 先頭ノートの
    配置開始位置をノート開始（cursor）より `min(preutterance_frames,
    直前のブレスギャップ frames)` だけ前へずらし、母音アタックが拍に近づくよう
    補正する（フレーズ間ブレス 0.25s の範囲内でクリップ・件数記録）。

    フレーズ内部（`has_join_to_prev=True`）のノートは、`joins.assemble_v2` が
    run 内を `overlap_frames` のみで純粋に overlap-add 連結するため
    （start_frame/end_frame は run 境界以外で未使用）、明示的な cursor シフトを
    加えなくても preutterance/overlap の相対関係（overlap < preutterance <
    vowel_core、実データで確認済み）が自然にタイムライン配置を規律する
    （[実装決定・record 記録] §Open Questions 参照）。`end_frame` は常に
    shift の影響を受けないシフト前 cursor 基準で計算する（次 run のギャップ
    計算を狂わせないため）。
    """
    placements: List[jn.NotePlacement] = []
    resolved_list: List[un.ResolvedVCVSegment] = []
    cursor = 0
    prev_end_sample = 0
    n_preutterance_applied = 0
    n_preutterance_clipped = 0
    preutterance_shift_frames: List[int] = []
    head_clipped_note_indices: List[int] = []

    for note_index, (seg, sel) in enumerate(zip(segments, selections)):
        gap_samples = seg.start_sample - prev_end_sample
        gap_frames = frames_for_samples(gap_samples, SR) if gap_samples > 0 else 0
        cursor += gap_frames
        note_dur_frames = max(frames_for_samples(seg.end_sample - seg.start_sample, SR), 1)
        resolved = un.resolve_vcv_unit_to_note(
            bank, sel.unit, note_dur_frames, note_index=note_index, frame_period_ms=FRAME_PERIOD_MS
        )
        if resolved.head_clipped:
            head_clipped_note_indices.append(note_index)
        resolved_list.append(resolved)

        start_frame = cursor
        if seg.is_phrase_first:
            preutt = sel.unit.preutterance_frames or 0
            shift = min(preutt, gap_frames)
            if shift > 0:
                start_frame = cursor - shift
                n_preutterance_applied += 1
                preutterance_shift_frames.append(shift)
                if shift < preutt:
                    n_preutterance_clipped += 1
        end_frame = cursor + resolved.n_frames  # シフト非依存（次 run のギャップ計算のため）

        placements.append(
            jn.NotePlacement(
                start_frame=start_frame, end_frame=end_frame, sp=resolved.sp, ap=resolved.ap,
                has_join_to_prev=not seg.is_phrase_first, overlap_frames=sel.unit.overlap_frames,
            )
        )
        cursor = cursor + resolved.n_frames
        prev_end_sample = seg.end_sample

    stats = dict(
        n_preutterance_applied=n_preutterance_applied,
        n_preutterance_clipped=n_preutterance_clipped,
        preutterance_shift_frames=preutterance_shift_frames,
        preutterance_shift_frames_mean=(
            float(np.mean(preutterance_shift_frames)) if preutterance_shift_frames else 0.0
        ),
        n_head_clipped=len(head_clipped_note_indices),
        head_clipped_note_indices=head_clipped_note_indices,
    )
    return placements, resolved_list, stats


def _frame_period_s() -> float:
    return FRAME_PERIOD_MS / 1000.0


def frames_for_samples(n_samples: int, sr: int) -> int:
    return max(int(round(n_samples / sr / _frame_period_s())), 0)


def _note_frame_track(
    per_sample: np.ndarray, seg, note_dur_frames: int, n_frames: int, sr: int, frame_period_ms: float,
) -> np.ndarray:
    """P1 修正 (review #262): 1 note の resolve 後フレーム列（`n_frames`）に
    対応する perf_genes per-sample トラック（`base_f0_persample`/`amp_env`。
    圧縮前の生スコアサンプル時間軸で構築済み）の値を、**このノート自身の
    圧縮前・実スパン内**で決定論的に抽出する（他ノートの overlap 圧縮とは
    無関係。圧縮後タイムラインへの整列は `joins.assemble_control_tracks_v2`
    が sp/ap と同じ run/overlap 機構で後段に行う）。

    `n_frames` は録音済み子音の前置（`prepend_recorded_consonant`）で
    `note_dur_frames`（= seg の生スコア尺から素朴に求めたフレーム数）より
    長くなることがある。その場合、末尾側の `note_dur_frames` フレームが
    seg の実スパン [start_sample, end_sample) に対応するよう原点をずらす
    （先頭の余剰フレームは子音が鳴る時間帯 = seg 開始より前へ自然に延びる。
    0 側でクランプ）。
    """
    if n_frames <= 0:
        return np.zeros(0, dtype=np.float64)
    if per_sample.shape[0] == 0:
        return np.zeros(n_frames, dtype=np.float64)
    samples_per_frame = sr * frame_period_ms / 1000.0
    extra_head = max(0, n_frames - note_dur_frames)
    k = np.arange(n_frames) - extra_head
    sample_idx = np.round(seg.start_sample + k * samples_per_frame).astype(np.int64)
    sample_idx = np.clip(sample_idx, 0, per_sample.shape[0] - 1)
    return per_sample[sample_idx]


def _midi_to_hz(m: float) -> float:
    return 440.0 * 2.0 ** ((m - 69.0) / 12.0)


def build_score(name: str) -> Tuple[list, float]:
    if name == "sakura":
        return sc.build_sakura_score(), sc.TEMPO_BPM
    if name == "umi":
        return sc_umi.build_umi_score(), sc_umi.TEMPO_BPM
    raise ValueError(f"unknown score: {name!r} (expected 'sakura' or 'umi')")


def render(
    score_name: str,
    voice_spec_path: str | Path,
    wav_path: Optional[str | Path] = None,
    notes_csv_path: Optional[str | Path] = None,
    cache_dir: Optional[str | Path] = None,
    out_path: Optional[str | Path] = None,
    w_p: float = un.DEFAULT_W_P,
    w_d: float = un.DEFAULT_W_D,
    w_c: float = un.DEFAULT_W_C,
    w_v: float = un.DEFAULT_W_V,
    apply_consonants: bool = True,
    donor: str = "vocadito",
    consonant_source: Optional[str] = None,
    voicebank_root: Optional[str | Path] = None,
) -> dict:
    """VG-F1 / 追補 F1.1 / 追補 F1.2 パイプライン本体。

    `donor`: "vocadito"（既定・従来どおり）| "ritsu"（UTAU oto バンク・
    追補 F1.2-B）| "pjs"（PJS lab バンク・追補 F1.2-C）。既存 CLI 挙動は
    `donor="vocadito"` の場合に完全不変（追補 F1.2 Acceptance 「既存 CLI
    挙動は不変」）。

    `consonant_source`: "recorded"（bank 提供の録音済み子音を前置。無ければ
    synthetic へフォールバック・件数記録）| "synthetic"（従来の
    `consonants.apply_consonant_onset` のみ）| "none"（子音加工なし）。
    省略時は donor に応じた既定（vocadito -> "synthetic", ritsu/pjs ->
    "recorded"）。`apply_consonants=False` は "none" と等価（後方互換の
    `--no-consonants` フラグ用）。
    """
    if consonant_source is None:
        consonant_source = "synthetic" if donor == "vocadito" else "recorded"
    if not apply_consonants:
        consonant_source = "none"
    if consonant_source not in CONSONANT_SOURCE_CHOICES:
        raise ValueError(f"unknown consonant_source: {consonant_source!r}")
    if donor not in DONOR_CHOICES:
        raise ValueError(f"unknown donor: {donor!r} (expected one of {DONOR_CHOICES})")

    spec = vs.load_voice_spec(voice_spec_path)
    notes, tempo_bpm = build_score(score_name)
    segments, total_samples = perf.build_timeline(notes, sr=SR, tempo_bpm=tempo_bpm)
    # P1 修正 (review #262): f0/振幅の per-sample トラックは segments/total_samples
    # だけに依存する（donor 選択より前に決められる）。旧実装はこれらを
    # assemble_v2 呼び出しの後段で構築し、圧縮後フレーム数でナイーブに再インデックス
    # していたためタイムラインがズレていた（`_note_frame_track` + `jn.assemble_control_tracks_v2`
    # 経由で圧縮後タイムラインへ正しく整列させる。後段の placements 構築後に使う）。
    note_dur_frames_list = [
        max(frames_for_samples(seg.end_sample - seg.start_sample, SR), 1) for seg in segments
    ]
    portamento_ms = float(spec.perf.get("portamento_ms", 55.0))
    base_f0_persample = pg.build_perf_f0(
        segments, total_samples, SR, spec.perf, seed=spec.seed, portamento_ms=portamento_ms
    )
    amp_env = perf.build_amplitude_envelope(segments, total_samples, SR)

    consonant_clips: Dict[str, list] = {}
    donor_extra_stats: dict = {}
    vcv_selection_stats: dict = {}
    vcv_placement_stats: dict = {}
    is_vcv = donor == "ritsu"  # 追補 F1.4: ritsu は VCV unit 経路（他は v0/F1.2 のまま）

    if donor == "vocadito":
        if wav_path is None:
            raise ValueError("donor='vocadito' には --wav（ドナー wav パス）が必須です")
        bank = db.build_donor_bank(wav_path, notes_csv_path=notes_csv_path, cache_dir=cache_dir)
        # 追補 F1.1-A: donor unit を母音分類し、選択コストへ母音制約を効かせる。
        # select_units は unit.index -> ラベル文字列を期待するため、VowelClassResult
        # から label のみ抜き出す（分布・F1/F2 の record 用には unit_vowel_results を残す）。
        unit_vowel_results = vc.classify_donor_units(bank)
        unit_vowel_labels = {idx: r.label for idx, r in unit_vowel_results.items()}
    elif donor == "ritsu":
        if voicebank_root is None:
            raise ValueError("donor='ritsu' には --voicebank-root（UTAU 音源ルート）が必須です")
        # 追補 F1.4-A: VCV unit 化（donor_bank_utau v2）。旧「母音核 unit + 録音子音
        # クリップ挿入」経路（v1 `build_donor_bank_utau`）は F1.3 までの互換・単体
        # テストのため関数として残置するが render.py からはもう呼ばない。
        required_contexts = _vcv_required_contexts(segments)
        bank, unit_contexts, donor_extra_stats = dbu.build_donor_bank_utau_vcv(
            voicebank_root, cache_dir=cache_dir, required_contexts=required_contexts,
        )
        # record/CLI 表示用の母音分布（選択には使わない・文脈キー一致が代替）。
        unit_vowel_labels = {}
        for idx, ctx in unit_contexts.items():
            _onset, _v, _status = dbu.normalize_mora_kana(ctx.mora)
            if _status == "ok" and _v:
                unit_vowel_labels[idx] = _v
    else:  # donor == "pjs"
        if voicebank_root is None:
            raise ValueError("donor='pjs' には --voicebank-root（PJS コーパスルート）が必須です")
        target_median_hz = float(np.median([_midi_to_hz(seg.note.midi) for seg in segments]))
        bank, unit_vowel_labels, consonant_clips, donor_extra_stats = dbl.build_donor_bank_lab(
            voicebank_root, target_median_hz=target_median_hz, cache_dir=cache_dir
        )

    # 追補 F1.3-B item1: unit の収録時レベルを除去する（母音核区間の平均パワーで
    # sp を正規化）。ドナー種別を問わず一律に適用（DonorBank は 3 ドナー共通の
    # スキーマ）。concat cost が正規化後の実レンダー値と整合するよう
    # head/tail_log_bands も再計算済みの bank を以降で使う。
    bank, energy_norm_stats = db.normalize_unit_energy(bank)

    n_bins = bank.sp.shape[1]
    consonant_events: List[cons.ConsonantEvent] = []
    n_recorded_consonants_used = 0
    n_recorded_consonants_fallback_synthetic = 0

    if is_vcv:
        # 追補 F1.4-C: 文脈キー完全一致必須の VCV 選択（w_c/w_v は使わない
        # ——文脈フィルタが母音一致を自動充足するため）。
        vcv_targets = [
            un.VCVTargetNote(
                pitch_hz=_midi_to_hz(seg.note.midi),
                duration_sec=(seg.end_sample - seg.start_sample) / SR,
                label=seg.note.mora.kana, prev_vowel=ctx[0], mora=ctx[1],
            )
            for seg, ctx in zip(segments, un.vcv_context_sequence(segments))
        ]
        ctx_map = {idx: (c.prev_vowel, c.mora) for idx, c in unit_contexts.items()}
        selections, sel_stats = un.select_vcv_units(vcv_targets, bank.units, ctx_map, w_p=w_p, w_d=w_d)
        vcv_selection_stats = sel_stats
        placements, resolved_list, vcv_placement_stats = _build_vcv_placements(segments, selections, bank)
        # 追補 F1.4-B item3: consonants.py 合成 / recorded クリップ挿入経路は
        # VCV 経路では不使用（子音は unit に録り込み済み）。
        consonant_source = "vcv"
    else:
        targets = [
            un.TargetNote(
                pitch_hz=_midi_to_hz(seg.note.midi),
                duration_sec=(seg.end_sample - seg.start_sample) / SR,
                label=seg.note.mora.kana,
                vowel_target=un.mora_to_vowel_target(seg.note.mora),
            )
            for seg in segments
        ]
        selections, sel_stats = un.select_units(
            targets, bank.units, w_p=w_p, w_d=w_d, w_c=w_c, unit_vowels=unit_vowel_labels, w_v=w_v
        )

        placements = []
        resolved_list = []
        cursor = 0
        prev_end_sample = 0
        for note_index, (seg, sel) in enumerate(zip(segments, selections)):
            gap_samples = seg.start_sample - prev_end_sample
            gap_frames = frames_for_samples(gap_samples, SR) if gap_samples > 0 else 0
            cursor += gap_frames
            note_dur_frames = max(frames_for_samples(seg.end_sample - seg.start_sample, SR), 1)
            resolved = un.resolve_unit_to_note(
                bank, sel.unit, note_dur_frames, note_index=note_index, frame_period_ms=FRAME_PERIOD_MS
            )
            onset = seg.note.mora.onset
            if consonant_source != "none" and onset is not None:
                # 追補 F1.2-D: consonant_source="recorded" は bank 提供の録音済み
                # 子音クリップを unit 先頭へ preutterance 相当分（クリップの実長）
                # だけ前置する（joins.assemble の 30ms クロスフェードが接続を担う）。
                # クリップが無ければ synthetic（追補 F1.1-B）へフォールバックする。
                used_recorded = False
                if consonant_source == "recorded":
                    clips = consonant_clips.get(onset)
                    if clips:
                        clip = clips[0]  # 決定論選択（bank 側で句頭優先・名前昇順ソート済み）
                        resolved, event = prepend_recorded_consonant(resolved, clip, note_index)
                        consonant_events.append(event)
                        n_recorded_consonants_used += 1
                        used_recorded = True
                    else:
                        n_recorded_consonants_fallback_synthetic += 1
                if not used_recorded:
                    # 追補 F1.1-B: 母音制約選択 -> 子音オンセット加工 -> (この後の) joins/perf_genes/warp。
                    proc_sp, proc_ap, event = cons.apply_consonant_onset(
                        resolved.sp, resolved.ap, onset, SR, frame_period_ms=FRAME_PERIOD_MS
                    )
                    if event is not None:
                        consonant_events.append(dataclasses.replace(event, note_index=note_index))
                    resolved = dataclasses.replace(resolved, sp=proc_sp, ap=proc_ap)
            resolved_list.append(resolved)
            start_frame = cursor
            end_frame = start_frame + resolved.n_frames
            placements.append(
                jn.NotePlacement(
                    start_frame=start_frame, end_frame=end_frame, sp=resolved.sp, ap=resolved.ap,
                    has_join_to_prev=not seg.is_phrase_first,
                    # 追補 F1.3-A: 選択された donor unit の oto overlap（フレーム）を
                    # そのまま接合長として使う。録音子音を前置した場合も vowel unit
                    # 自身の overlap 値を流用する（子音クリップ自体は oto overlap を
                    # 持たないための近似・[実装決定・record 記録]）。None = 情報なし
                    # (vocadito/pjs) -> assemble_v2 が DEFAULT_OVERLAP_MS へフォールバック。
                    overlap_frames=sel.unit.overlap_frames,
                )
            )
            cursor = end_frame
            prev_end_sample = seg.end_sample

    # 追補 F1.3-A: v1 の単側ブレンド（jn.assemble）ではなく true overlap-add
    # （jn.assemble_v2）のみを使う。n_total_frames は overlap 分だけ短くなった
    # 実際の圧縮後タイムライン長を join_stats から受け取る。
    sp_seq, ap_seq, join_stats = jn.assemble_v2(n_bins, placements, frame_period_ms=FRAME_PERIOD_MS)
    n_total_frames = join_stats["n_total_frames"]

    sp_seq, ap_seq = vs.apply_warp(sp_seq, ap_seq, SR, spec.warp)

    # P1 修正 (review #262): f0/振幅トラックを sp/ap と同じ圧縮後タイムライン上へ
    # ノート単位で再構築する。placements[i] の実スパン（consonant 前置で
    # note_dur_frames_list[i] より長いことがある）に対して perf_genes の
    # per-sample トラックを抽出し（`_note_frame_track`）、sp/ap と全く同じ
    # run/overlap 圧縮（`jn.assemble_control_tracks_v2`）へ通すことで、
    # join を経るたびに累積していたズレ（旧: 圧縮後フレーム数から作った
    # 素朴な等間隔インデックスで圧縮前の生タイムラインを読み出していたバグ）
    # を解消する。overlap 区間は f0 を log domain（sp と同じ）・振幅を
    # linear domain（ap と同じ）でブレンドする。
    note_f0_tracks = [
        _note_frame_track(
            base_f0_persample, segments[i], note_dur_frames_list[i], p.sp.shape[0], SR, FRAME_PERIOD_MS
        )
        for i, p in enumerate(placements)
    ]
    note_amp_tracks = [
        _note_frame_track(
            amp_env, segments[i], note_dur_frames_list[i], p.sp.shape[0], SR, FRAME_PERIOD_MS
        )
        for i, p in enumerate(placements)
    ]
    f0_seq, amp_seq, control_track_stats = jn.assemble_control_tracks_v2(
        placements, note_f0_tracks, note_amp_tracks, frame_period_ms=FRAME_PERIOD_MS
    )
    assert f0_seq.shape[0] == n_total_frames, (
        f"f0_seq/sp_seq フレーム数不一致: {f0_seq.shape[0]} != {n_total_frames}"
    )
    assert amp_seq.shape[0] == n_total_frames, (
        f"amp_seq/sp_seq フレーム数不一致: {amp_seq.shape[0]} != {n_total_frames}"
    )

    y = pw.synthesize(
        np.ascontiguousarray(f0_seq), np.ascontiguousarray(sp_seq), np.ascontiguousarray(ap_seq),
        SR, FRAME_PERIOD_MS,
    )
    y = np.asarray(y, dtype=np.float64)

    # 追補 F1.3-B item2: 振幅の唯一の権威 = performance.build_amplitude_envelope
    # （フレーズアーチ）。unit 由来の振幅は F1.3-B item1 の正規化で spectral 形状
    # のみに縮退済みなので、ここで乗算するフレーズアーチ + articulation
    # エンベロープが最終波形の音量ダイナミクスを決める唯一の経路になる
    # （singer/render_song.py の amp_render 適用パターンと同じ乗算方式）。
    # P1 修正: amp_seq は sp_seq/ap_seq と同じ**フレーム**単位（圧縮後タイムライン
    # 上で正しく整列済み）のため、y（サンプル単位）へ適用する際はフレーム→サンプル
    # の素朴な等分割で読み出す（旧実装のような圧縮前タイムラインとの取り違えはない）。
    if len(y) > 0 and len(amp_seq) > 0:
        samples_per_frame = SR * FRAME_PERIOD_MS / 1000.0
        frame_idx_for_sample = np.minimum(
            (np.arange(len(y)) / samples_per_frame).astype(np.int64), len(amp_seq) - 1
        )
        y = y * amp_seq[frame_idx_for_sample]

    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    if peak > 0:
        y = y / peak * PEAK_NORM

    if out_path is not None:
        sf.write(str(out_path), y, SR, subtype="PCM_16")

    cap_modes = [r.cap_mode for r in resolved_list]
    return dict(
        y=y, sr=SR, n_total_frames=n_total_frames, total_samples_timeline=total_samples,
        selections=selections, selection_stats=sel_stats, join_stats=join_stats,
        control_track_stats=control_track_stats,
        donor_bank_stats=bank.stats, donor_bank_source=bank.source, wav_sha256=bank.wav_sha256,
        donor=donor, consonant_source=consonant_source, donor_extra_stats=donor_extra_stats,
        n_stretch_extended_looped=cap_modes.count("extended_looped"),
        n_stretch_compressed_truncated=cap_modes.count("compressed_truncated"),
        n_stretch_none=cap_modes.count("none"),
        unit_vowels=unit_vowel_labels, vowel_distribution=_vowel_distribution_from_labels(unit_vowel_labels),
        consonant_events=consonant_events,
        consonant_class_counts=dict(Counter(e.consonant_class for e in consonant_events)),
        n_recorded_consonants_used=n_recorded_consonants_used,
        n_recorded_consonants_fallback_synthetic=n_recorded_consonants_fallback_synthetic,
        energy_norm_stats=energy_norm_stats,
        is_vcv=is_vcv, vcv_selection_stats=vcv_selection_stats, vcv_placement_stats=vcv_placement_stats,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="VG-F1 Foundry Adapter render CLI")
    parser.add_argument("--score", required=True, choices=["sakura", "umi"])
    parser.add_argument("--voice", required=True, help="voice spec JSON path")
    parser.add_argument("--out", required=True, help="output WAV path")
    parser.add_argument("--donor", default="vocadito", choices=list(DONOR_CHOICES), help="追補 F1.2-D: ドナー選択")
    parser.add_argument("--wav", default=None, help="donor WAV path（donor=vocadito で必須）")
    parser.add_argument("--notes-csv", default=None, help="donor notes annotation CSV（vocadito・省略時 fallback）")
    parser.add_argument(
        "--voicebank-root", default=None,
        help="UTAU 音源ルート（donor=ritsu）または PJS コーパスルート（donor=pjs）",
    )
    parser.add_argument("--cache-dir", default=None, help="donor bank npz/pkl キャッシュディレクトリ")
    parser.add_argument(
        "--consonant-source", default=None, choices=list(CONSONANT_SOURCE_CHOICES),
        help="追補 F1.2-D: 子音供給元（省略時 donor に応じた既定）",
    )
    parser.add_argument(
        "--no-consonants", action="store_true",
        help="子音オンセット加工を無効化する（--consonant-source none と等価。旧 F1.1-B 互換フラグ）",
    )
    args = parser.parse_args()

    result = render(
        args.score, args.voice, args.wav, notes_csv_path=args.notes_csv,
        cache_dir=args.cache_dir, out_path=args.out, apply_consonants=not args.no_consonants,
        donor=args.donor, consonant_source=args.consonant_source, voicebank_root=args.voicebank_root,
    )
    print(f"wrote {args.out}: {len(result['y'])} samples ({len(result['y']) / result['sr']:.3f}s)")
    print(f"donor={result['donor']} consonant_source={result['consonant_source']}")
    print(f"donor_bank_source={result['donor_bank_source']} wav_sha256={result['wav_sha256']}")
    print(f"donor_bank_stats={result['donor_bank_stats']}")
    print(f"selection_stats={result['selection_stats']}")
    print(f"vowel_distribution={result['vowel_distribution']}")
    print(f"consonant_class_counts={result['consonant_class_counts']}")
    print(
        f"recorded_consonants_used={result['n_recorded_consonants_used']} "
        f"fallback_synthetic={result['n_recorded_consonants_fallback_synthetic']}"
    )
    print(f"join_stats={result['join_stats']}")
    print(f"energy_norm_stats={result['energy_norm_stats']}")
    print(
        f"stretch: none={result['n_stretch_none']} "
        f"extended_looped={result['n_stretch_extended_looped']} "
        f"compressed_truncated={result['n_stretch_compressed_truncated']}"
    )
    if result["is_vcv"]:
        print(f"vcv_selection_stats={result['vcv_selection_stats']}")
        print(f"vcv_placement_stats={result['vcv_placement_stats']}")


if __name__ == "__main__":
    main()
