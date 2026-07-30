# 設計書 — M3（旋律比較器: 正規化・対応付け・多軸類似・校正）

**宛先:** Claude Code（実装）+ slow-lane ランナー
**発行元:** Cowork（設計・検収）
**前提コミット:** `30397ac`（main、M2 トラック完結）
**設計入力:** `docs/m2_error_model.md`（M2d・第1入力）+ **User 決裁 2026-07-30**「§7 の撤回範囲を音高系と voicing 系で分離し、単離済み clean lead 入力帯限定で M3 先行を許可」（選択肢 b。本決裁を M3-0 で dated 記録すること）
**スコープ:** 2つの旋律観測を比較し軸別類似と根拠つき判定を返す**決定論比較器**の構築と校正。**Recast 配線（M4）・意味軸・総合スコアはやらない。**

---

## 0. 問いと成果物（1分）

M1「聞こえるか」→ M2「聞き間違えていないか」→ **M3「同じ旋律か」**。
成果物は `MelodyComparisonReport`: 移調・テンポ変化に**不変**で、抽出誤差（M2d実測）を
**許容**し、編曲差分には**反応**する、軸別の類似判定。単一の同一性%は恒久的に出さない。

**入力帯の制約（決裁事項・拡張は実測が前提）:** 本設計の適用は**単離済み clean lead**
（合成単旋律・単独歌唱）で校正された範囲に限る。demucs vocals stem 帯は M2 誤差モデルの
被覆外（V-fullstack 未測定）のため、stem 帯への適用は別途 dated 精度実測後に解禁する。
フルミックス直は禁止のまま。

## 1. M2d 誤差モデルからの設計拘束（全て実測由来・順守必須）

| M2d の実測事実 | M3 の設計拘束 |
|---|---|
| median cent 誤差は打ち切り統計（±50cent 通過分のみ） | 音高量子化は**半音**（±50 cent）。それ未満へ狭める設計は禁止（狭めるなら再実測が先行条件） |
| オクターブ誤り direct 帯 ~0（標本最大 2.54%/clip）・対比較で最悪 ~2 倍 | ゼロと仮定しない。**オクターブ折返しガード**（§3.2）を常設 |
| **VFA 高・分散大 = voicing は信頼できない** | 音高比較は**両系列が共に音符を持つ整列対のみ**で行い、voicing 不一致は音高軸で非ペナルティ。ただし被覆信号を独立に併記（§4.2）——この意味論の形式定義と閾値校正は M3d の仕事 |
| per-clip VFA は正解付きコーパス限定統計 | 推論時の信頼重みに VFA を使わない。confidence proxy の重み付けは校正実測なしでは導入しない（初版は非導入） |
| repeats の bit 一致は要約統計レベルまで（軌跡レベル未立証） | M3d の校正 run で**系列 hash を pin**し、軌跡レベル決定論を実測で確立（M2d の残課題を閉じる） |

## 2. モジュール（`src/svp_rpe/melody/` に追加・既存変更なし）

```
representation.py   # M3a: 観測→正規化系列（音程列・輪郭列・IOI比列）
alignment.py        # M3b: 系列対応付け（ギャップ許容・決定論）
comparison.py       # M3c: 軸別類似 + MelodyComparisonReport
scripts/run_melody_comparison.py  # M3d: 校正ハーネス（run/evaluate 二相・provenance pin 同型）
```
`observability.py` / `accuracy.py` / registry の凍結値は**一切変更しない**。
比較の入口で両観測に M1 観測ゲートを課す: insufficient → `not_comparable`（比較拒否・理由つき）。

## 3. M3a — 表現と正規化

### 3.1 三系列（ノート列 `notes_from_frames`（凍結パラメータ）から導出）
- **音程列**: 隣接ノートの半音差の列（移調不変・整数量子化）
- **輪郭列**: 音程の粗ビン {↑大, ↑小, →, ↓小, ↓大}（±2半音を小/大境界。抽出ゆらぎに最鈍感な軸）
- **リズム列**: IOI 比の列（隣接 onset 間隔の比・テンポ不変）+ ノート長比
### 3.2 オクターブ折返しガード
音程列を (a) 生の半音差 (b) 12 折返し（chroma 差）の両方で計算し、両者の類似が乖離したら
`octave_artifact_suspected` を理由欄に記録（判定は (b) を優先しつつ乖離を隠さない）。
### 3.3 量子化と丸め
半音・比は事前登録の丸め（例: IOI 比を log2 で 0.25 刻み）。全定数は M3a で registry に凍結。

## 4. M3b — 対応付け

- **アルゴリズム:** 音程列上の**ギャップ許容大域整列**（Needleman–Wunsch・アフィンギャップ、
  一致/不一致/ギャップのコストは事前登録定数）。装飾音の挿入・音の削除＝ギャップとして
  吸収し、**どこを整列できなかったか**を残す。DTW でなく NW を既定にする理由: ギャップの
  明示性（削除/挿入の被覆会計）と決定論の単純さ。
- **フレーズ層:** phrase_gap 0.6s（凍結）でフレーズ分割し、フレーズ単位の対応（編曲での
  セクション増減・繰返しを許容）→ 対応フレーズ内でノート整列。未対応フレーズは
  ペナルティでなく**被覆の欠損として記録**。
- **§4.2 被覆信号（M2d が要件化を M3 に委ねた部分の形式定義）:**
  - `aligned_note_fraction_a/b` = 整列されたノート数 / 各系列の全ノート数（両側明記）
  - `phrase_coverage_a/b` = 対応づいたフレーズ数 / 全フレーズ数
  - **被覆下限ゲート**: `min(aligned_note_fraction_a, b) < floor` なら軸類似を出さず
    `not_comparable(insufficient_overlap)`（僅かな重複での過大類似を封じる）。floor は
    M3d の tuning split で導出し、holdout 前に凍結。

## 5. M3c — 軸別類似とレポート

```
MelodyComparisonReport:
  axes: {contour: float, interval: float, rhythm: float}   # 各0–1・整列対上で算出
  coverage: {aligned_note_fraction_a, _b, phrase_coverage_a, _b}
  octave_artifact_suspected: bool
  evidence: "strong" | "weak" | "none" | "not_comparable"  # 事前登録マージンから機械導出
  reasons: [str]
  provenance: {route, sequence_sha256_a/b, registry_sha256, ...}   # 既存 pin 流儀
```
- `evidence` は軸別閾値（M3d で校正・凍結）からの機械導出のみ。**総合スコア禁止**（軸間の
  重み付き平均を作らない）。軸が割れたら割れたまま報告（例: 輪郭 strong・リズム none）。
- 語彙は「同一旋律の証拠」であり **preserved ではない**（保存判定は契約と結び付く M4 の仕事）。

## 6. M3d — 校正（slow-lane・正解対つき）

### 6.1 素材（clean lead 帯のみ・すべて既存資産から決定論生成）
- **positive 対**: 同一旋律の変形 — vocadito clip の ±2〜5 半音 pitch-shift / ±8〜15%
  time-stretch（`make_melody_pairs.py` 既存・librosa 決定論）+ 合成旋律の移調/変速版。
  **重要:** 対の両側とも**実 crepe 抽出を通す**（比較器は抽出器の出力分布の上で校正する。
  クリーン正解列同士で校正した閾値は現実に使えない）。
- **negative 対**: 異なる clip 同士（vocadito 40 clip から系統抽出）+ **狙い撃ち negative**
  （同リズム別音程列・同音程列別リズムの合成対 = 各軸の弁別を単独検証）。
- 分割: tuning / holdout（M0 流儀）。holdout は閾値凍結後に一度だけ開く。
### 6.2 事前登録（測る前に凍結）
- 軸別マージン要求: **positive 最小類似 − negative 最大類似 ≥ 0.15**（M0 の
  `separation_gate.min_same_minus_cross_margin` を継承。新値を発明しない）
- 変形範囲（±5半音・±15%）を超える変形への外挿は主張しない
- repeats n≥2 + 系列 hash pin（§1 軌跡決定論の確立を兼ねる）
### 6.3 判定
- **校正成立**: マージン達成の軸を「calibrated axis」として記録 → M4 の experimental
  anchor 候補資格（配線は別設計）。
- **軸別不成立**: 落ちた軸は not-calibrated として除外（軸単位の一方向）。輪郭だけ生き
  残っても前進——多軸設計の眼目は部分成立を許すこと。
- **全滅**: 「clean lead 帯でも比較器が弁別できない」の dated 記録。M4 へ進まない。

## 7. PR 分割

| PR | 内容 | 受け入れ条件 |
|---|---|---|
| M3-0 | M2 §7 文言改訂 + User 決裁 2026-07-30 の dated 記録（docs のみ） | 撤回範囲の軸分離が明文化。凍結値 diff ゼロ |
| M3a | representation + 定数凍結 | 手計算一致テスト。移調/変速不変性の性質テスト（同旋律の移調→音程列同一） |
| M3b | alignment + 被覆信号 | ギャップ・フレーズ増減 fixture。被覆会計の合計整合テスト |
| M3c | comparison + report | 拒否経路（gate 不通過/被覆不足）。総合スコア不在のスキーマテスト |
| M3d | 校正ハーネス + slow-lane 実測 + 凍結 + 判定 doc | tuning→凍結→holdout の順序が記録で証明可能。マージン表 + 軸別判定 |

## 8. やってはいけないこと

- 総合同一性スコアの導入（恒久禁止）。軸間重み付き平均も同罪。
- ±50 cent より細かい音高許容の導入（再実測なしに）。
- 抽出器の voicing を信頼した判定・VFA の推論時重み利用。
- holdout を見てからの閾値・コスト調整。マージン 0.15 の緩和。
- stem 帯・フルミックス帯への適用や外挿（dated 実測まで）。
- insufficient 観測同士の比較で類似値を出す（not_comparable を返す）。
- melodia の混入（#222 裁定前）。M1/M2 凍結値の変更。

一文: **M3 は「同じ旋律か」に軸別で答える比較器を、実抽出器の誤差の上で校正する。移調と変速には黙り、編曲には反応し、測れない対には正直に沈黙する——目盛りは M2 の実測、マージンは M0 の凍結値から継承する。**
