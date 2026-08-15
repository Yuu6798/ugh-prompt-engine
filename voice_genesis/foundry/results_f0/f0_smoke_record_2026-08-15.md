# VG-F0 スモーク記録 — SawSing / GOLF 推論実証と経路裁定材料

- 日付: 2026-08-15
- 記録者: Claude（Fable 設計判定 + Sonnet 実行委譲、Claude 完結ルート）
- 目的: 「学習ベース R1（DDSP 系 fork）は本環境で実行不能」（2026-08-13 判断、R0.9
  フルスクラッチの存在理由）の最終検証 = **実推論の成否**。および F1 のレンダラ経路
  裁定材料の提供
- 生ログ: `f0_smoke_raw.md`（Round 1/2 の全コマンド・exit code・パッチ diff 全文）
- 関連: `env_probe_a1.md`（導入可否まで）、`v5_diagnosis_2026-08-15.md`（ピボット根拠）

## 1. 結論

**実推論 5/5 成功。「本環境で実行不能」は最終的に反証された。**

| バックエンド | checkpoint | 推論 | 壁時計 | RTF (CPU 4 core) | 出力 sha256 (先頭16) |
|---|---|---|---|---|---|
| SawSing Sins | `exp/f1-full/sins` | ✓ | 8.10 s / 24.55 s 音声 | 0.330 | 779688fb1d7d3991 |
| SawSing SawSinSub-256 | `exp/f1-full/sawsinsub-256` | ✓ | 7.24 s | 0.295 | e3f4a0f433df98a3 |
| GOLF golf-ff | `ckpts/interspeech24` | ✓ | 9.75 s | 0.397 | 9832ddc070b403d8 |
| GOLF world | 同上 | ✓ | 22.52 s | 0.916 | c295977e9c6bb425 |
| GOLF ddsp | 同上 | ✓ | 21.02 s | 0.855 | be758436b6838de1 |

全経路 RTF < 1.0 = CPU で実時間以内。学習なし・同梱 checkpoint のみ・外部 DL なし。
（golf-ss / golf-v1 / nhv / mlsa の 4 checkpoint は経路確認済みのため未実行）

## 2. 経緯（2 ラウンド構成）

- **Round 1**: 依存導入は両リポとも完走したが、推論 0 件。ブロッカーは両方とも
  上流コードの実装欠陥 — SawSing: `ddsp/pcmer.py` L10 が CUDA 拡張
  （nvcc 不在環境では原理的にビルド不能）を無条件 import（CPU フォールバック実装は
  L276-283 に既在、ガードが無いだけ）。GOLF: submodule `models/lru/recurrence.py` の
  型注釈タイポ `torch.Any`（torch 2.13 で import 時クラッシュ、7 checkpoint 全部が道連れ）
- **Round 2**: 設計判定でパッチ適用を承認（/workspace の実験 clone のみ・本リポへの
  vendoring 禁止は維持・diff 全文を生ログに記録）。適用後、全経路が開通

### パッチ台帳（全て /workspace clone のみ・本リポ非適用・diff は生ログ Round 2 節）

| リポ | ファイル | 内容 | 規模 |
|---|---|---|---|
| SawSing | `ddsp/pcmer.py` | CUDA 拡張 import を try/except ガード | 3 行 |
| SawSing | `preprocess.py` | `librosa.filters.mel` の kwarg 形式修正 | 1 行 |
| SawSing | `ddsp/core.py` | ハードコード `.cuda()` → `.to(device)` ×3 | 3 行 |
| GOLF | `models/lru/recurrence.py` (submodule) | `torch.Any` → `typing.Any` | 2 行 |
| GOLF | `ltng/cli.py` | `torchaudio.save` → `soundfile.write`（torchcodec の native .so ロード失敗回避 = torchaudio 2.11/torch 2.13 skew の唯一の実害顕在点） | 数行 |

いずれも推論到達に必須な最小修正。SawSing の device 修正が示すとおり、上流は
CPU 推論を想定した整備がされていない（= fork 時に CPU パスの保守は自前になる）。

## 3. ライセンス（確定）

| リポ | LICENSE | 帰結 |
|---|---|---|
| SawSing (YatingMusic/ddsp-singing-vocoders) | **AGPL-3.0-only** | 本リポ（MIT）への vendoring / fork 同梱は不可。外部プロセス・別リポ分離でのみ利用可。配布・サービス提供時は AGPL 義務が発生 |
| GOLF (yoyololicon/golf) | **MIT** | 制約なし。fork・改変・同梱可 |

## 4. 生成物の計測（`f0_bands_round2.json`）

| 出力 | 0–500Hz | 1k–3k | 3k–5k | 5k–8k | HNR |
|---|---|---|---|---|---|
| （入力参照: v5 sakura_voiceC） | 0.890 | 0.028 | 0.005 | 0.0000 | 9.49 |
| sawsing_sins | 0.647 | 0.072 | 0.004 | 0.00002 | 9.21 |
| sawsing_sawsinsub256 | 0.749 | 0.029 | 0.009 | 0.00001 | 8.99 |
| golf_golfff | 0.863 | 0.043 | 0.004 | 0.0 | 10.24 |
| golf_world | 0.848 | 0.065 | 0.003 | 0.0 | 10.22 |
| golf_ddsp | 0.887 | 0.027 | 0.002 | 0.0 | 10.22 |

**判読上の注意（重要）**: 全出力は v5 sakura_voiceC（暗い素材）の**分析再合成**であり、
出力帯域が入力に似るのは analysis-by-synthesis として正しい挙動。この表は
「経路が音声を壊さず通る」ことの確認であって、**音質改善の証拠ではない**。
音質上限は入力素材に拘束される — F0 が実証したのは「配管が通る」ことまで。
「良い声が出る」かは、Genome 駆動で mel / 音響特徴を**自前生成**する F1 の検証事項。

## 5. 制限事項（正直会計）

1. SawSing は mel 入力のボコーダ。Genome→歌唱に使うには mel 生成側（音響モデル相当）
   を自作するか、DDSP 制御特徴（f0 / harmonic amp / noise mag）を直接駆動する
   改造が必要 = F1 の設計対象
2. GOLF interspeech24 checkpoint は speech（VCTK 24kHz）学習。歌声 checkpoint
   （ISMIR23 = MPop600 学習）は同梱確認済みだが今回未実行
3. checkpoint はいずれも実在人間の録音で学習 = timbre 事前分布に実在者の identity が
   乗る。採用時は設計書 v0.2 §8（Residual Identity Quarantine）の監査対象
4. 耳判定素材としての生成 WAV は User へ提出済み（WAV 非同梱規約により本リポには
   sha256 のみ記録。全出力は同梱 checkpoint + 決定論前処理から再現可能）

## 6. 裁定表（F1 レンダラ経路 — 判定は User）

| 経路 | 実証状態 | 摩擦点 | ライセンス | 判定 |
|---|---|---|---|---|
| **GOLF 系 fork**（差分可能 LPC + glottal-flow wavetable） | 推論 3/3 成功・RTF 0.40–0.92・歌声 ckpt 同梱（未実行） | CPU 推論パスの保守自前・torchaudio skew 回避要 | MIT | （空欄） |
| **SawSing 系 fork**（DDSP vocoder: Sins / SawSinSub） | 推論 2/2 成功・RTF 0.30–0.33（最速） | mel 生成側の自作が必要・CPU パス未整備 | **AGPL-3.0** — 同梱不可・外部プロセス化必須 | （空欄） |
| **pyworld 直駆動アダプタ**（WORLD: 最小位相 + 非周期帯域） | 合成往復スモーク済（env_probe）・学習ゼロ・完全決定論 | 「人間らしさ」はパラメータ設計に全依存（学習 prior なし） | MIT 系 | （空欄） |
| **R0.9 継続**（自作フルスクラッチ） | 稼働中だが D1–D3 実測欠陥（v5_diagnosis 参照） | 帯域上限・零位相・演奏層固定が全て自前修理 | — | （空欄） |

**記録者推奨（拘束力なし）**: F1 は **GOLF 系（MIT・歌声 ckpt 同梱・glottal-flow 系で
設計書 R2 相当の帰納バイアスまで射程）を主候補、pyworld 直駆動を決定論並走枠**として
検討。SawSing は AGPL 制約により参考実装（アーキテクチャの読解対象）に格下げが妥当。
R0.9 は演奏層（performance.py）と Genome スキーマ・ゲート群・registry を資産として
残し、合成部のみ置換する。

## 7. F1 への引き継ぎ

- Genome → 音響特徴（f0 輪郭 / 調音 / 気息）→ 借用バックエンド、のアダプタ層設計
- Performance 遺伝子化（決定済み）はアダプタ層の f0 / dynamics 生成に同居させる
- GOLF ISMIR23（歌声）checkpoint の実行確認が最初のスパイク
- gate1 の無力化方式（演奏表現を罰する問題）の設計
