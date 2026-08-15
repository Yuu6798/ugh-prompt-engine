# VG-F1c 生ログ — Performance Transplant（時間的整合性の対照実験）

実行専任・採否判定なし。目的: F1b 耳判定（identity は転写・発声は破綻）を受け、
「時間的整合性（連続な包絡軌跡 + 人間的 f0 微細構造）が自然な発声の最後のピースか」を
対照実験で切り分ける。

## 結果表

**注記（歌詞）**: 4 条件すべて **ドナー（vocadito_2, singer S2, language=Spanish）の
元歌詞がそのまま乗る**。さくらの歌詞は載っていない。本実験の問いは「発声の自然さ」
であり歌詞ではない。

| 条件 | 包絡軌跡 (sp/ap) | f0 | 出力ファイル | 特記 |
|---|---|---|---|---|
| (iv) roundtrip | 窓 W 自身（無改変） | 窓 W 自身（無改変） | `f1c_roundtrip.wav` | WORLD 透明性の天井確認。b1k_3k=0.14725 |
| (i) transplant | 窓 W 自身（無改変） | さくらマクロ音高(vib=0, ffill) × ドナー微細構造(micro_cents, ±150c clip) | `f1c_transplant.wav` | 窓オフセット9.5s・有声率0.8589。micro clip発動1.37%(58/4223)。ノート差偏差 abs_median=4.61c / abs_p90=38.32c。ブレス区間ホールドでドナー声が乗った区間 4.83%(204/4223)。b1k_3k=0.18282 |
| (ii) robotf0 | 窓 W 自身（無改変） | さくらロボットf0(vib=30c版, ドナー有声ゲート, ホールドなし) | `f1c_robotf0.wav` | 同一窓。ブレス区間でノートトラックが0の箇所はホールドせず f0=0 に落ちる区間 4.83%(204/4223、(i)と同じ箇所)。b1k_3k=0.18115 |
| (iii) 参考（既存） | F1b テンプレート銀行（NN選択・明るさフロアなし） | score f0 直結 | `foundry_f1b/f1b_nn_neutral.wav`（合成不要・再掲のみ） | b1k_3k=0.06632（F1b記録より転記）。窓/包絡は本実験と異なり単一フレーム選択の連結列 |

### 窓選定・ドナー分析の要点

- ドナー全体 WORLD 分析: n_frames=7042, voiced_frames=5863 (0.8326), frame_period=5ms
- 窓探索: 0.5s 刻み・22 候補（offset 0.0s–10.5s）・目的関数=有声フレーム率最大
- 選定窓 W: **start_sec=9.500s**, voiced_ratio=**0.8589**（top5: 9.5s/0.8589, 10.0s/0.8585, 10.5s/0.8503, 0.5s/0.8452, 9.0s/0.8446）
- 窓長=さくら長と1:1（n_target_frames=4917、時間伸縮なし）

### マクロ音高・微細構造

- 移動median窓: 41フレーム(205ms、指示の約200msに最も近い奇数)
- 短ギャップ橋渡し閾値: 60フレーム(300ms)。窓内ギャップ10個・長さ [142,112,124,2,9,156,4,18,11,116] フレーム。うち5個（142,112,124,156,116）が閾値超で **edge-hold**（線形補間ではなく端値保持）、計650フレーム。橋渡し対象（閾値以下）は2,9,4,18,11フレームの5個・計44フレーム
  - 特記: ドナーの子音/ブレス区間は300msを超えるものが窓内で過半（650/694無声フレーム=93.7%がedge-hold域）。これは「短いギャップ」想定より長い区間が実際には多いことの正直な記録
- micro_cents: ドナー有声4223フレームで計算。±150c clip 発動 58 フレーム(1.37%)

## 前処理・グルー

- `make_note_tracks.py`（60行）: `results_f1a/glue_control.py` を複製・改変。
  voice_genesis/singer を read-only import。vibrato_depth_cents=0.0/30.0 の2本を出力
  （ポルタメント55msはデフォルト維持）
  - `f1c_note_track.npz`: vibrato=0, 590000 samples(24.583s) @24kHz, voiced_frac=0.9492
  - `f1c_note_track_vib30.npz`: vibrato=30c（f1a_control.npzと同一設計・値は再生成のため独立ファイル）
- `glue_transplant.py`（333行、1本にまとめ）: ドナー読込+resample+WORLD全体分析 →
  窓選定 → マクロ/微細構造抽出 → 4条件合成(iv)(i)(ii) → measure_bands計測用wav書き出し
  → f0偏差統計 → ログJSON書き出し

## 実行ログ（標準出力そのまま）

```
donor loaded: 844993 samples @ 24000Hz (35.208s), resampled from 44100Hz via resample_poly(80/147)
donor wav sha256=8dcc99c3b08a9a5800b793e3d65cccfb4464961f15cf8ccde25bd4c8b853d519
donor WORLD analysis: n_frames=7042 voiced_frames=5863 (0.8326) frame_period=5.0ms
note track vib0 loaded: total_samples=590000 (24.583s) voiced_frac=0.9492
note track vib30 loaded: voiced_frac=0.9492
n_target_frames=4917 (== window length in donor frames, no time-stretch)
window search: 22 candidates @ step=0.5s (offsets 0.0s..10.5s)
window W selected: start_frame=1900 start_sec=9.500s voiced_ratio=0.8589 (max among candidates)
top5 candidate windows (start_sec, voiced_ratio): [(9.5, 0.858857), (10.0, 0.85845), (10.5, 0.850315), (0.5, 0.845231), (9.0, 0.844621)]
macro pitch: moving median window=41 frames (205ms), gap-bridge threshold=60 frames (300ms)
gap stats in window W: n_gaps=10 lengths_frames=[142, 112, 124, 2, 9, 156, 4, 18, 11, 116] n_long_gap_frames(edge-hold, > threshold)=650
micro_cents: computed at 4223 donor-voiced frames, clipped(+-150.0c) triggered on 58 frames (0.0137)
note track (vib0) zero-hold: n_zero_samples_globally=30000 (0.0508 of samples). At donor-voiced frames landing inside a sakura breath (note_hz==0, held): n=204/4223 (0.0483) -> policy: donor voice is allowed through on held note_hz (per brief)
note track (vib30) for robotf0: at donor-voiced frames where the vib30 track itself is 0 (inside a sakura breath, NOT held per brief): n=204/4223 (0.0483) -> those frames synthesize as f0=0 despite donor voicing (recorded, not corrected)
wrote f1c_roundtrip.wav: 590040 samples (24.585s), window's own f0/sp/ap, no modification, peak-norm 0.6
wrote f1c_transplant.wav: 590040 samples (24.585s), f0=note_hz(vib0,ffilled)*2^(micro_cents/1200) at donor-voiced frames, f0=0 at donor-unvoiced frames, window's own sp/ap unmodified, peak-norm 0.6
wrote f1c_robotf0.wav: 590040 samples (24.585s), f0=note_hz(vib30, unheld) at donor-voiced frames, f0=0 at donor-unvoiced frames (and at donor-voiced frames landing in a sakura breath, see above), window's own sp/ap unmodified, peak-norm 0.6
(i) transplant f0 deviation from note track (cents, clipped +-150.0): median=0.00 abs_median=4.61 abs_p90=38.32 range=[-150.00, 150.00]

glue_transplant.py line count: 333
```

## 帯域計測（measure_bands.py・記録のみ、最適化対象にしない）

`f1c_bands.json` より抜粋（(iv)/(i)/(ii) のみ計測。(iii) は f1b_bands_nn.json から
既存値を上表に転記）:

| file | b0_500 | b500_1k | b1k_3k | b3k_5k | b5k_8k | tilt_db_per_decade_1k_8k | hnr_median_db |
|---|---|---|---|---|---|---|---|
| f1c_roundtrip.wav | 0.20765 | 0.63749 | 0.14725 | 0.00693 | 0.00055 | -37.81 | 10.06 |
| f1c_transplant.wav | 0.19660 | 0.61091 | 0.18282 | 0.00858 | 0.00088 | -35.30 | 9.37 |
| f1c_robotf0.wav | 0.19767 | 0.61160 | 0.18115 | 0.00850 | 0.00087 | -38.10 | 9.33 |

観察（記録のみ・判定なし）: 3条件とも sp/ap は完全同一（窓 W そのまま）なので、
帯域差は f0 の違いが WORLD 再合成に与える影響のみに由来する。roundtrip の
b1k_3k(0.147) が transplant/robotf0(0.183/0.181) より低い一方、HNR は roundtrip
(10.06dB) が最も高い。帯域指標は F1b で自然さの代理として無効と実証済みのため、
これらの差の解釈（自然さとの関係）は行わない。

## 素材・生成物パス

- ドナー: `/tmp/.../scratchpad/foundry_f1b/vocadito/Audio/vocadito_2.wav`（F1bと同一。sha256一致確認済み）
- OUT: `/tmp/claude-0/-home-user-ugh-prompt-engine/1c025cfe-cb3e-592b-b577-d0be9640c799/scratchpad/foundry_f1c/`
  - `make_note_tracks.py`, `f1c_note_track.npz`, `f1c_note_track_vib30.npz`
  - `glue_transplant.py`
  - `f1c_roundtrip.wav`, `f1c_transplant.wav`, `f1c_robotf0.wav`
  - `f1c_bands.json`, `f1c_glue_run_log.json`
  - `f1c_raw.md`（本ファイル）
- (iii)参考: `/tmp/.../scratchpad/foundry_f1b/f1b_nn_neutral.wav`
