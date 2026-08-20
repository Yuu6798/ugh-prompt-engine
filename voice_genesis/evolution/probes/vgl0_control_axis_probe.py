"""VG-L0 実装第 1 タスク: 制御軸表現力の実測（`../DESIGN_VG_L0.md` §6）。

問い: **現行 gate_synth の入力で、歌い方の制御軸をどこまで表現できるか。**

DESIGN_VG_L0 §6 が候補に挙げる 3 軸（ブレス位置オフセット・アタック強度・
フレーズ終端処理）について、合成パイプラインのどこへ決定論的な前処理を
挟めば動くのか、そして**実際に出力が動くのか**を実測する。

正直会計（本スクリプトの位置づけ）:

- **製品コードではない**。gate_synth 本体は read-only で扱い、介入は
  monkeypatch で行う（製品実装 = 前処理層の設計は本実測の結果を見てから
  別 PR で決める）。実測記録の再現性のためにコミットしているだけで、
  本番合成経路からは一切参照されない
- GPU ゼロ（CPU 推論のみ）。曲は `--notes-limit` で先頭 N ノートに絞る
- 出力 = 結果 JSON（各条件の wav sha256 / 総尺 / 音素長配分 / RMS）。
  一次指標は **gate_synth が record.json に残す正規化前の生値**
  （`wav_rms_raw` / `wav_peak_raw`）。gate_synth は wav をピーク 0.6 へ
  正規化して書き出すため、wav から測った RMS は実質クレストファクタで
  単一サンプルの peak に支配される（初回実測はここを踏み抜いた）

**フレーズ軸の定義について（2 巡目で全面改訂）**:

初版は「ブレス位置」を `HEAD_FRAMES`/`TAIL_FRAMES`、「フレーズ終端処理」を
`note_target_frames[-1]` で代表させたが、いずれも定義が誤っていた:

- `HEAD_FRAMES`/`TAIL_FRAMES` は **stage2/stage3 のトークン列の前後へ足す
  曲全体の SP パディング**（`gate_synth.py` の `ph_dur2` / `a_tokens2`）で、
  フレーズ間のブレスではない。曲頭・曲尾の無音長を動かしているだけ
- `--notes-limit 8` のさくらでは最終ノートは**フレーズ 2（やよいの）の
  2 音目**であり、フレーズ終端ではない。`note_target_frames[-1]` の伸長は
  「最後にレンダリングしたノートを伸ばす」であってフレーズ終端処理ではない

本版は `ScoreNote.phrase_index` / `is_phrase_final` から**実際のフレーズ境界**を
取り、(a) 境界への SP ノート挿入 = フレーズ間ブレス、(b) フレーズ終端ノートの
伸長 = フレーズ終端処理、として測り直す。初版の 2 条件も
`song_pad_head/tail` / `last_note_x1.5` へ改名して**何を測っているかを
名前で正す**形で残す。

**gate_synth の配線ギャップ**: `ScoreNote` は `phrase_index` /
`is_phrase_final` を持つが、`gate_synth._NoteWithMs` が midi / mora /
`_dur_ms` の 3 つしか写さないためフレーズ情報は合成経路へ届かない
（`grep -c 'phrase_index\\|is_phrase_final' gate_synth.py` = 0）。
本スクリプトはこのギャップを probe 側で埋めており、**製品側では前処理層の
実装時に `_NoteWithMs` の拡張が必要**になる（次段への申し送り）。
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import soundfile as sf

REPO = Path(__file__).resolve().parents[3]
FOUNDRY = REPO / "voice_genesis" / "foundry"
DEFAULT_SINGER_DIR = REPO / "voice_genesis" / "singer"

sys.path.insert(0, str(FOUNDRY / "s1_gate"))
import gate_synth as gs  # noqa: E402


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_path(p: Path) -> str:
    return sha256_bytes(p.read_bytes())


# --- 介入点 1: duration 予測の後段（子音時間配分） ---------------------------
# run_pipeline は sess_dur の `ph_dur_pred` をノート目標長へ per-note で
# rescale してから音素長にする。その **前** に係数をかければ「同じノート長の
# まま、子音と母音の配分だけ」が動く（= アタック強度）。ノート目標長そのものを
# 動かすと楽譜が変わる（歌い方ではない）ので、両者は区別して測る。

class _DurOutputPatch:
    """`onnxruntime.InferenceSession.run` をラップし、dur モデルの出力
    `ph_dur_pred` の子音要素に係数を適用する。"""

    def __init__(self, consonant_duration_scale: float = 1.0):
        self.consonant_duration_scale = consonant_duration_scale
        self.applied = 0
        self.dur_stats: Optional[dict] = None

    def wrap(self, sess, captured: Dict[str, object]) -> None:
        """`captured` は **遅延参照**する（セッション生成は `build_inputs` より
        先に起きるため、wrap 時点では is_vowel_flags がまだ空 — ここを
        先読みにすると patch が一度も適用されず「軸が動かない」という
        誤った実測になる。初回実測でこの欠陥を踏んだ）。"""
        orig_run = sess.run
        names = [o.name for o in sess.get_outputs()]
        if "ph_dur_pred" not in names:
            return
        idx = names.index("ph_dur_pred")
        probe = self

        def patched(output_names, input_feed, **kw):
            out = orig_run(output_names, input_feed, **kw)
            if probe.consonant_duration_scale == 1.0:
                return out
            flags = captured.get("is_vowel_flags") or []
            phones = captured.get("real_phones") or []
            pred = np.array(out[idx], dtype=np.float64)
            body = pred[0]
            changed = 0
            pre_cons = pre_vow = post_cons = 0.0
            for i, is_vowel in enumerate(flags):
                j = i + 1  # run_pipeline は offset=1（先頭 SP 分）から読む
                if j >= len(body):
                    continue
                # ブレスとして挿入した SP は子音でも母音でもないので係数の
                # 対象から外す（`phone_frame_invariant` と同じ扱いに揃える）。
                # 揃えないと併用条件の子音比が SP を含んだ値になり、
                # 子音軸単独の条件と比較できなくなる。
                if i < len(phones) and phones[i] == "SP":
                    continue
                if is_vowel:
                    pre_vow += float(body[j])
                    continue
                pre_cons += float(body[j])
                body[j] = body[j] * probe.consonant_duration_scale
                post_cons += float(body[j])
                changed += 1
            probe.applied += changed
            probe.dur_stats = {
                "pred_consonant_frames": round(pre_cons, 2),
                "pred_vowel_frames": round(pre_vow, 2),
                "patched_consonant_frames": round(post_cons, 2),
                "pred_consonant_ratio": round(pre_cons / (pre_cons + pre_vow), 4)
                if (pre_cons + pre_vow) else None,
                "patched_consonant_ratio": round(post_cons / (post_cons + pre_vow), 4)
                if (post_cons + pre_vow) else None,
            }
            out = list(out)
            out[idx] = pred.astype(np.float32)
            return out

        sess.run = patched  # type: ignore[method-assign]


# --- 介入点 2: build_inputs の出力（フレーズ軸） -----------------------------
# `build_inputs` が返す 5 配列（real_phones / note_phone_counts / note_tones /
# note_target_frames / is_vowel_flags）は互いに整合していなければならない
# （run_pipeline は `len(final_phone_dur) == n_real` と
#  `sum(final_phone_dur) == sum(note_target_frames)` を assert する）。
# フレーズ間ブレスは「SP を 1 音素持つ長さ `breath_frames` のノート」を
# 境界へ挿入することで表現する。SP は variance / acoustic 双方の音素辞書に
# 存在する（gate_synth は曲頭・曲尾の SP で既に使っている）。

def phrase_final_indices(notes_raw: Sequence[object]) -> List[int]:
    """レンダリング対象ノート列のうち **フレーズ終端** のノート index。

    末尾ノートは `is_phrase_final` が真でも「曲の終わり」であって
    フレーズ間境界ではないので、境界としては別扱いにする（本関数は
    終端ノート集合を返し、境界の判定は `phrase_boundary_indices` が行う）。
    """
    out = []
    for i, n in enumerate(notes_raw):
        if getattr(n, "is_phrase_final", False):
            out.append(i)
    return out


def phrase_boundary_indices(notes_raw: Sequence[object]) -> List[int]:
    """**内部**フレーズ境界（この index のノートの直後にブレスが入る）。

    `notes_limit` で切り詰めた結果、最後のフレーズが途中で切れている場合
    その切断点は境界ではない。末尾ノートも曲末なので除外する。
    """
    n_last = len(notes_raw) - 1
    return [i for i in phrase_final_indices(notes_raw) if i != n_last]


class _BuildInputsPatch:
    """`build_inputs` の出力へフレーズ軸の介入を加える。"""

    def __init__(
        self,
        *,
        phrase_breath_frames: int = 0,
        phrase_breath_at: Sequence[int] = (),
        phrase_final_scale: float = 1.0,
        phrase_final_at: Sequence[int] = (),
        last_note_scale: float = 1.0,
    ):
        self.phrase_breath_frames = phrase_breath_frames
        self.phrase_breath_at = list(phrase_breath_at)
        self.phrase_final_scale = phrase_final_scale
        self.phrase_final_at = list(phrase_final_at)
        self.last_note_scale = last_note_scale
        self.inserted = 0
        self.scaled_notes = 0

    def apply(self, d: dict) -> dict:
        counts = list(d["note_phone_counts"])
        tones = list(d["note_tones"])
        frames = list(d["note_target_frames"])
        phones = list(d["real_phones"])
        vowels = list(d["is_vowel_flags"])

        # (a) フレーズ終端ノートの伸長 = フレーズ終端処理
        if self.phrase_final_scale != 1.0:
            for i in self.phrase_final_at:
                frames[i] = int(round(frames[i] * self.phrase_final_scale))
                self.scaled_notes += 1

        # (b) 最後にレンダリングしたノートの伸長（初版の `final_x1.5` 相当。
        #     `notes_limit` の切断点はフレーズ終端とは限らない）
        if self.last_note_scale != 1.0:
            frames[-1] = int(round(frames[-1] * self.last_note_scale))
            self.scaled_notes += 1

        # (c) フレーズ境界への SP ノート挿入 = フレーズ間ブレス。
        #     index がずれないよう **後ろから** 挿入する。
        if self.phrase_breath_frames > 0 and self.phrase_breath_at:
            for i in sorted(self.phrase_breath_at, reverse=True):
                phone_end = sum(counts[: i + 1])
                phones.insert(phone_end, "SP")
                vowels.insert(phone_end, False)
                counts.insert(i + 1, 1)
                tones.insert(i + 1, tones[i])
                frames.insert(i + 1, self.phrase_breath_frames)
                self.inserted += 1

        assert len(phones) == len(vowels) == sum(counts)
        assert len(counts) == len(tones) == len(frames)
        d = dict(d)
        d.update(
            real_phones=phones, is_vowel_flags=vowels,
            note_phone_counts=counts, note_tones=tones,
            note_target_frames=frames,
        )
        return d


HOP_SIZE = 512  # gate_synth.run_pipeline のフレーム長（sample_rate 44100 と対）


def _capture_durations(sess, captured: Dict[str, object]) -> None:
    """acoustic セッションが受け取る `durations`（= `[HEAD] + final_phone_dur
    + [TAIL]`）を捕まえる。

    `final_phone_dur` は `run_pipeline` のローカル変数で record にも残らないが、
    **音素長の下限不変条件**（すべての ph_dur >= 1 frame）を検査するには
    実際に acoustic へ渡った値が要る。acoustic の入力を横取りするのが
    gate_synth 無改変で取れる唯一の経路。"""
    orig_run = sess.run

    def patched(output_names, input_feed, **kw):
        d = input_feed.get("durations")
        if d is not None:
            captured["acoustic_durations"] = [int(x) for x in np.asarray(d).reshape(-1)]
        return orig_run(output_names, input_feed, **kw)

    sess.run = patched  # type: ignore[method-assign]


def note_region_energy(
    data: np.ndarray, note_target_frames: Sequence[int], head_frames: int,
) -> List[dict]:
    """ノート領域ごとの RMS/peak。

    acoustic は `durations = [HEAD] + final_phone_dur + [TAIL]` をそのまま
    フレーム長として受け取り、vocoder は hop 512 で波形化するので、
    ノート i の波形範囲は「HEAD + 先行ノートのフレーム合計」から
    frames 個ぶんで決まる。**同一ファイル内の比**を見ること — gate_synth は
    ピーク 0.6 へ正規化して書き出すため、絶対値はファイル間で比較できない。
    """
    out: List[dict] = []
    pos = head_frames
    for i, f in enumerate(note_target_frames):
        seg = data[pos * HOP_SIZE: (pos + f) * HOP_SIZE]
        out.append({
            "note_index": i, "frames": int(f),
            "rms": round(float(np.sqrt(np.mean(seg ** 2))), 6) if seg.size else 0.0,
            "peak": round(float(np.max(np.abs(seg))), 6) if seg.size else 0.0,
        })
        pos += f
    return out


def synth_once(
    label: str,
    cfg: "ProbeConfig",
    *,
    head_frames: int = 8,
    tail_frames: int = 8,
    consonant_duration_scale: float = 1.0,
    phrase_breath_frames: int = 0,
    phrase_final_scale: float = 1.0,
    last_note_scale: float = 1.0,
) -> dict:
    """1 条件を合成し、実測値を返す。gate_synth 本体は無改変で、
    モジュール定数 / `build_inputs` / dur 出力のみ monkeypatch する。"""
    build_fn, beats_to_seconds, tempo, _p, _shas = gs.load_song_module(
        cfg.song, cfg.singer_dir)
    model_bytes, _ = gs.load_model_bundle_bytes(
        cfg.canon, cfg.vocoder, cfg.acoustic_onnx, cfg.acoustic_dsconfig)
    # **推論へ実際に渡ったバイト列**そのものを hash する（レビュー指摘）。
    # `collect_pins` はパスを別途 read するので、その read と合成が使う read の
    # 間に差し替えが起きると記録と実体がずれる（gate_synth が
    # `load_model_bundle_bytes` を導入したのと同じ TOCTOU の理屈）。
    # `load_model_bundle_bytes` は 1 回だけ read したバッファを返すので、
    # ここで hash すれば記録と推論入力が同一バッファ由来だと構造的に言える。
    consumed = {k: sha256_bytes(v) for k, v in sorted(model_bytes.items())}
    variance_phonemes = gs.load_canon_phonemes(cfg.canon / "phonemes.txt")
    acoustic_phonemes = gs.load_own_phonemes_json(cfg.acoustic_phonemes_json)
    emb = gs.load_speaker_embed_vector(cfg.speaker_emb)

    notes_raw = build_fn()[: cfg.notes_limit]
    boundaries = phrase_boundary_indices(notes_raw)
    finals = phrase_final_indices(notes_raw)

    orig_head, orig_tail = gs.HEAD_FRAMES, gs.TAIL_FRAMES
    orig_build_inputs = gs.build_inputs
    orig_session = gs.ort.InferenceSession
    dur_patch = _DurOutputPatch(consonant_duration_scale)
    build_patch = _BuildInputsPatch(
        phrase_breath_frames=phrase_breath_frames,
        phrase_breath_at=boundaries,
        phrase_final_scale=phrase_final_scale,
        # 終端伸長は `is_phrase_final` が真なノート **全部** を対象にする
        # （末尾ノートがフレーズ終端なら、それも終端処理の対象。曲末と
        # フレーズ末の区別が要るのはブレス挿入側だけ — 曲の最後に
        # フレーズ間ブレスは入らない）。
        phrase_final_at=finals,
        last_note_scale=last_note_scale,
    )
    captured: Dict[str, object] = {}

    def build_inputs_probe(notes, frame_ms):
        d = build_patch.apply(orig_build_inputs(notes, frame_ms))
        captured["is_vowel_flags"] = d["is_vowel_flags"]
        captured["note_target_frames"] = list(d["note_target_frames"])
        captured["real_phones"] = list(d["real_phones"])
        return d

    def session_probe(*a, **kw):
        sess = orig_session(*a, **kw)
        names = [o.name for o in sess.get_outputs()]
        if "ph_dur_pred" in names:
            dur_patch.wrap(sess, captured)  # captured は遅延参照
        if "mel" in names:
            _capture_durations(sess, captured)
        return sess

    gs.HEAD_FRAMES, gs.TAIL_FRAMES = head_frames, tail_frames
    gs.build_inputs = build_inputs_probe  # type: ignore[assignment]
    gs.ort.InferenceSession = session_probe  # type: ignore[assignment]
    out_dir = cfg.out_dir / label
    if out_dir.exists():
        # 同じ --out-dir を別の --notes-limit で使い回すと、条件ディレクトリに
        # 前回の wav が残る。gate_synth の wav 名は notes_limit を含むため
        # `sorted(...)[0]` が**前回の測定結果**を掴みうる（例: _n10 が _n6 より
        # 前に並ぶ）。fail-closed にせず作り直す。
        shutil.rmtree(out_dir)
    try:
        with open(cfg.out_dir / f"{label}.log", "w") as logf, \
                contextlib.redirect_stdout(logf):
            gs.synth_song(
                cfg.song, cfg.notes_limit, build_fn, beats_to_seconds, tempo,
                model_bytes, variance_phonemes, acoustic_phonemes, out_dir,
                speaker_name=cfg.speaker, speaker_embed_vector=emb,
            )
    finally:
        gs.HEAD_FRAMES, gs.TAIL_FRAMES = orig_head, orig_tail
        gs.build_inputs = orig_build_inputs  # type: ignore[assignment]
        gs.ort.InferenceSession = orig_session  # type: ignore[assignment]

    wavs = sorted(out_dir.rglob("*.wav"))
    assert len(wavs) == 1, (
        f"{label}: wav が 1 本であるべきだが {len(wavs)} 本ある "
        f"({[w.name for w in wavs]}) — どれを測ったか一意に決まらない")
    data, sr = sf.read(str(wavs[0]))
    regions = note_region_energy(data, captured.get("note_target_frames") or [],
                                 head_frames)
    # **gate_synth は wav をピーク 0.6 へ正規化して書き出す**ため、wav から
    # 測った rms は実質クレストファクタで単一サンプルの peak に支配される。
    # 正規化前の生値は gate_synth 自身が record.json に残しているので、
    # そちらを一次指標として拾う（初回実測はこれを拾わず、正規化後 rms の
    # 見かけの非単調を「音響応答の非単調」と誤読した）。
    recs = sorted(out_dir.rglob("*_record.json"))
    raw: dict = {}
    if recs:
        rec = json.loads(recs[0].read_text(encoding="utf-8"))
        raw = {k: rec[k] for k in
               ("wav_rms_raw", "wav_peak_raw", "wav_rms", "wav_peak",
                "wav_duration_sec", "stage1_elapsed_sec", "stage2_elapsed_sec",
                "stage3_elapsed_sec") if k in rec}
    return {
        "label": label,
        "params": {
            "head_frames": head_frames, "tail_frames": tail_frames,
            "consonant_duration_scale": consonant_duration_scale,
            "phrase_breath_frames": phrase_breath_frames,
            "phrase_final_scale": phrase_final_scale,
            "last_note_scale": last_note_scale,
        },
        "phrase_structure": {
            "phrase_index_per_note": [getattr(n, "phrase_index", None) for n in notes_raw],
            "is_phrase_final_per_note": [getattr(n, "is_phrase_final", None) for n in notes_raw],
            "internal_boundaries": boundaries,
            "last_note_is_phrase_final": bool(getattr(notes_raw[-1], "is_phrase_final", False)),
        },
        "wav": wavs[0].name,
        "wav_sha256": sha256_bytes(wavs[0].read_bytes()),
        "duration_s": round(len(data) / sr, 4),
        "rms_normalized": round(float(np.sqrt(np.mean(data ** 2))), 6),
        "note_target_frames": captured.get("note_target_frames"),
        "n_phones": len(captured.get("real_phones") or []),
        "dur_patch_applied": dur_patch.applied,
        "dur_stats": dur_patch.dur_stats,
        "breath_notes_inserted": build_patch.inserted,
        "breath_note_indices": [i + 1 + k for k, i in enumerate(boundaries)]
        if build_patch.inserted else [],
        "notes_scaled": build_patch.scaled_notes,
        "note_region_energy": regions,
        "consumed_model_sha256": consumed,
        "phone_frame_invariant": phone_frame_invariant(
            captured.get("acoustic_durations") or [],
            captured.get("note_target_frames") or [],
            captured.get("is_vowel_flags") or [],
            captured.get("real_phones") or []),
        "raw_measures": raw,
    }


def phone_frame_invariant(
    acoustic_durations: Sequence[int], note_target_frames: Sequence[int],
    is_vowel_flags: Sequence[bool], real_phones: Sequence[str],
) -> dict:
    """音素長の下限不変条件と総和保存を実測で検査する。

    `run_pipeline` は per-note rescale のあと `[int(round(x)) for x in rescaled]`
    で丸め、端数 `resid` を **そのノートの最後の音素へ加算**する
    （`gate_synth.py` の Stage 1）。この処理には**下限ガードが無い**ため、
    ノート目標長が短い / 係数が極端な場合に `ph_dur == 0`（あるいは負）が
    出うる。本フィールドはその実測値であり、「さくら 8 ノートでは出なかった」
    以上のことは主張しない（他曲・他ノート長での安全性の証明ではない）。
    """
    if not acoustic_durations:
        return {"measured": False}
    body = list(acoustic_durations)[1:-1] if len(acoustic_durations) >= 2 else []
    # 挿入した SP（ブレス）は子音でも母音でもないので両方から外す
    triples = list(zip(body, is_vowel_flags, real_phones))
    cons = [x for x, v, p in triples if not v and p != "SP"]
    vows = [x for x, v, p in triples if v]
    return {
        "measured": True,
        "head_tail": [int(acoustic_durations[0]), int(acoustic_durations[-1])],
        "n_phones": len(body),
        "min_phone_frames": min(body) if body else None,
        "max_phone_frames": max(body) if body else None,
        # 子音/母音を分けて見る: 係数が効くのは子音側なので、下限に先に
        # ぶつかるのも子音側。`margin_to_floor` = 最小音素が 1 frame の
        # 下限まで何フレーム残しているか（この曲・この係数での実測余裕）。
        "min_consonant_frames": min(cons) if cons else None,
        "min_vowel_frames": min(vows) if vows else None,
        "margin_to_floor": (min(body) - 1) if body else None,
        "n_phones_below_1_frame": sum(1 for x in body if x < 1),
        "sum_preserved": (sum(body) == sum(note_target_frames)),
        "sum_phone_frames": sum(body),
        "sum_note_target_frames": sum(note_target_frames),
    }


CONDITIONS: List[Tuple[str, dict]] = [
    ("baseline", {}),
    ("baseline_repeat", {}),          # 決定論の確認（同一条件 2 回）
    # 軸 A: 子音時間配分 `consonant_duration_scale`（機構名。知覚的な
    # 「アタック強度」という意味名は未校正なので付けない — レビュー指摘）
    ("cdur_x0.25", {"consonant_duration_scale": 0.25}),
    ("cdur_x0.5", {"consonant_duration_scale": 0.5}),
    ("cdur_x1.5", {"consonant_duration_scale": 1.5}),
    ("cdur_x2.0", {"consonant_duration_scale": 2.0}),
    ("cdur_x3.0", {"consonant_duration_scale": 3.0}),
    # 制御軸を**有効にした状態**での決定論（baseline 反復では patch 経路が
    # consonant_duration_scale == 1.0 の早期 return で一度も通らない）
    ("cdur_x2.0_repeat", {"consonant_duration_scale": 2.0}),
    # 軸 B': 曲全体の SP パディング（初版が「ブレス」と誤ラベルした条件。
    # フレーズ間ブレスではなく曲頭/曲尾の無音長）
    ("song_pad_head40", {"head_frames": 40}),
    ("song_pad_tail40", {"tail_frames": 40}),
    # 軸 B: フレーズ間ブレス（内部フレーズ境界へ SP ノートを挿入）
    ("phrase_breath_10f", {"phrase_breath_frames": 10}),
    ("phrase_breath_20f", {"phrase_breath_frames": 20}),
    ("phrase_breath_20f_repeat", {"phrase_breath_frames": 20}),
    # 軸 C: フレーズ終端処理（**フレーズ終端ノート**の伸長）
    ("phrase_final_x1.25", {"phrase_final_scale": 1.25}),
    ("phrase_final_x1.5", {"phrase_final_scale": 1.5}),
    # 初版の `final_x1.5` = 最後にレンダリングしたノートの伸長。
    # notes_limit の切断点はフレーズ終端とは限らない（さくら 8 ノートでは
    # フレーズ 2「やよいの」の 2 音目 = 非終端）
    ("last_note_x1.5", {"last_note_scale": 1.5}),
    # 軸の同時指定（直交性の主張はしない — 破綻しないことの確認）
    ("cdur_x2.0_phrase_breath_20f",
     {"consonant_duration_scale": 2.0, "phrase_breath_frames": 20}),
]


class ProbeConfig:
    def __init__(self, a: argparse.Namespace):
        self.canon = Path(a.canon_dir)
        self.vocoder = Path(a.vocoder_dir)
        self.acoustic_dir = Path(a.acoustic_dir)
        self.acoustic_onnx = Path(a.acoustic_onnx)
        self.acoustic_dsconfig = self.acoustic_dir / "dsconfig.yaml"
        # 末尾の .onnx だけを差し替える（`str.replace` は全出現を置換するので、
        # 親ディレクトリ名に .onnx を含むパスで壊れる）
        stem = self.acoustic_onnx.with_suffix("")
        self.acoustic_phonemes_json = stem.with_name(stem.name + ".phonemes.json")
        self.speaker = a.speaker
        self.speaker_emb = stem.with_name(f"{stem.name}.{a.speaker}.emb")
        self.singer_dir = Path(a.singer_dir)
        self.song = a.song
        self.notes_limit = a.notes_limit
        self.out_dir = Path(a.out_dir)


def collect_pins(cfg: "ProbeConfig") -> dict:
    """**この probe が実際に読んだバイト列**の sha256 を全部束ねる。

    monkeypatch 方式には provenance の穴がある — gate_synth 本体・モデル・
    楽譜の sha が全部同じでも、patch の中身次第で別の WAV が出る。だから
    probe 自身と gate_synth 本体の sha を**モデルと同格で**結果へ直接束縛する
    （レビュー指摘）。実行環境も同じ理由で記録する（決定論は環境を固定した
    上でしか主張できない）。
    """
    gate_synth_path = FOUNDRY / "s1_gate" / "gate_synth.py"
    self_path = Path(__file__).resolve()
    # 楽譜側は gate_synth 自身の provenance 機構（`load_song_module` の
    # 5 番目の戻り値 = 本体 + 推移的依存の sha）をそのまま流用する。
    _, _, _, _, module_shas = gs.load_song_module(cfg.song, cfg.singer_dir)
    pins = {
        "probe_script": {"path": str(self_path), "sha256": sha256_path(self_path)},
        "gate_synth": {"path": str(gate_synth_path),
                       "sha256": sha256_path(gate_synth_path)},
        "acoustic_onnx": {"path": str(cfg.acoustic_onnx),
                          "sha256": sha256_path(cfg.acoustic_onnx)},
        "acoustic_dsconfig": {"path": str(cfg.acoustic_dsconfig),
                              "sha256": sha256_path(cfg.acoustic_dsconfig)},
        "acoustic_phonemes_json": {"path": str(cfg.acoustic_phonemes_json),
                                   "sha256": sha256_path(cfg.acoustic_phonemes_json)},
        "speaker_embed": {"path": str(cfg.speaker_emb),
                          "sha256": sha256_path(cfg.speaker_emb)},
        "canon_phonemes": {"path": str(cfg.canon / "phonemes.txt"),
                           "sha256": sha256_path(cfg.canon / "phonemes.txt")},
        "canon_linguistic_onnx": {"path": str(cfg.canon / "linguistic.onnx"),
                                  "sha256": sha256_path(cfg.canon / "linguistic.onnx")},
        "canon_dur_onnx": {"path": str(cfg.canon / "dsdur" / "dur.onnx"),
                           "sha256": sha256_path(cfg.canon / "dsdur" / "dur.onnx")},
        "canon_pitch_onnx": {"path": str(cfg.canon / "dspitch" / "pitch.onnx"),
                             "sha256": sha256_path(cfg.canon / "dspitch" / "pitch.onnx")},
        "vocoder_onnx": {"path": str(cfg.vocoder / "nsf_hifigan.onnx"),
                         "sha256": sha256_path(cfg.vocoder / "nsf_hifigan.onnx")},
    }
    for key, (path, digest) in module_shas.items():
        pins[key] = {"path": str(path), "sha256": digest}
    return pins


def execution_profile() -> dict:
    import platform

    import onnxruntime  # 記録目的の遅延 import
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "numpy": np.__version__,
        "onnxruntime": onnxruntime.__version__,
        "onnxruntime_providers": list(onnxruntime.get_available_providers()),
        "soundfile": sf.__version__,
        "gate_synth_seed": gs.SEED,
    }


# `load_model_bundle_bytes` のキー -> `collect_pins` のキー
_CONSUMED_TO_PIN = {
    "acoustic_onnx": "acoustic_onnx",
    "acoustic_dsconfig_yaml": "acoustic_dsconfig",
    "canon_linguistic_onnx": "canon_linguistic_onnx",
    "canon_variance_dur_onnx": "canon_dur_onnx",
    "canon_variance_pitch_onnx": "canon_pitch_onnx",
    "vocoder_onnx": "vocoder_onnx",
}


def verify_consumed_bytes(results: List[dict], pins: dict) -> dict:
    """全条件が同じモデルバイト列を消費し、かつそれが pin と一致するか。

    「記録された hash と実際に推論へ使われたバイト列が食い違う」窓を閉じる。
    ずれた場合は結果 JSON に不一致として残す（黙って通さない）。

    射程の正直会計: ここで閉じられるのは `load_model_bundle_bytes` が返す
    6 バッファ（canon 3 + acoustic + dsconfig + vocoder）だけ。話者 embed /
    音素辞書 / 楽譜モジュールは gate_synth 側がパス read するため、
    依然として `pins` のパス read に依存する。
    """
    seen = {json.dumps(r["consumed_model_sha256"], sort_keys=True)
            for r in results if "consumed_model_sha256" in r}
    mismatches = []
    if len(seen) > 1:
        mismatches.append(f"条件間でモデルバイト列が異なる (distinct={len(seen)})")
    for r in results:
        for ck, digest in (r.get("consumed_model_sha256") or {}).items():
            pin_key = _CONSUMED_TO_PIN.get(ck)
            if pin_key and pins.get(pin_key, {}).get("sha256") != digest:
                mismatches.append(
                    f"{r['label']}: 消費バイト列 {ck} が pins.{pin_key} と不一致")
    return {
        "distinct_bundles": len(seen),
        "covered_keys": sorted(_CONSUMED_TO_PIN),
        "not_covered": ["speaker_embed", "canon_phonemes",
                        "acoustic_phonemes_json", "score_module"],
        "mismatches": mismatches,
        "ok": not mismatches,
    }


def build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--canon-dir", required=True,
                    help="NamineRitsu_DiffSinger 展開先（linguistic/dsdur/dspitch）")
    ap.add_argument("--vocoder-dir", required=True, help="nsf_hifigan.onnx のあるディレクトリ")
    ap.add_argument("--acoustic-dir", required=True, help="ONNX export ディレクトリ")
    ap.add_argument("--acoustic-onnx", required=True, help="使用する acoustic onnx のパス")
    ap.add_argument("--speaker", default="ritsu")
    ap.add_argument("--singer-dir", default=str(DEFAULT_SINGER_DIR))
    ap.add_argument("--song", default="sakura")
    ap.add_argument("--notes-limit", type=int, default=8)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--result-json", required=True)
    ap.add_argument(
        "--only", default=None,
        help="この label の条件だけ合成する（1 条件 = 1 プロセス にするためのモード。"
             "Render Reproducibility を **独立プロセス間**で検証するには同一"
             "プロセス内の反復では足りない — レビュー指摘）")
    ap.add_argument(
        "--reverse", action="store_true",
        help="条件の実行順を逆にする（順序汚染の検査用）")
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    a = build_argparser().parse_args(argv)

    cfg = ProbeConfig(a)
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    pins = collect_pins(cfg)

    conditions = list(CONDITIONS)
    if a.reverse:
        conditions.reverse()
    if a.only:
        conditions = [(lbl, kw) for lbl, kw in conditions if lbl == a.only]
        if not conditions:
            raise SystemExit(f"unknown condition label: {a.only}")

    results = []
    for label, kw in conditions:
        print(f"| synthesizing {label} {kw}", flush=True)
        try:
            results.append(synth_once(label, cfg, **kw))
        except Exception as exc:  # 実測なので失敗も記録して続ける
            results.append({"label": label, "params": kw, "error": repr(exc)[:400]})
            print(f"|   FAILED: {exc!r}"[:300], flush=True)

    consumed_check = verify_consumed_bytes(results, pins)
    payload = {
        "song": cfg.song, "notes_limit": cfg.notes_limit, "speaker": cfg.speaker,
        "consumed_model_bytes_check": consumed_check,
        "order": "reverse" if a.reverse else "forward",
        "single_condition": a.only,
        "pins": pins, "execution_profile": execution_profile(),
        "conditions": results,
    }
    Path(a.result_json).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    base = next((r for r in results
                 if r["label"] == "baseline" and "wav_sha256" in r), None)
    print("\n=== 表現力の実測 ===")
    for r in results:
        if "error" in r:
            print(f"{r['label']:30s} ERROR {r['error'][:80]}")
            continue
        raw = r.get("raw_measures") or {}
        if base is None:
            # --only モードや baseline 失敗時は比較対象が無い。比較していない
            # ことを "identical" と書くと嘘になるので明示する。
            verdict = "(no baseline)"
        else:
            verdict = "CHANGED" if r["wav_sha256"] != base["wav_sha256"] else "identical"
        print(f"{r['label']:30s} dur={r['duration_s']:7.3f}s "
              f"rms_raw={raw.get('wav_rms_raw', float('nan')):.6f} "
              f"peak_raw={raw.get('wav_peak_raw', float('nan')):.6f} "
              f"{verdict}")
    # --- Fix: 全条件が失敗しても 0 を返していた（呼び出し側が検知できない）
    failed = [r["label"] for r in results if "error" in r]
    if failed:
        print(f"\nFAILED conditions: {failed}")
        return 1
    if not consumed_check["ok"]:
        print(f"\nCONSUMED BYTES MISMATCH: {consumed_check['mismatches']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
