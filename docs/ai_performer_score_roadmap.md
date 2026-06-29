# AI 演奏者のための楽譜 — マージロードマップ（PR 3 本構成）

## North Star

目的は**独自理論の構築ではなく、「AI が演奏者として使う楽譜」という実用物**を作ること。
そのために、これまでの蓄積（特に K 系列 grip・roundtrip fixity・genre calibration）と
**既存研究の道具を遠慮なくマージする**。本ロードマップは、壁打ちで策定したマージマップを
**実装可能な 3 つの PR** に固めたもの。各 PR は着手時に個別の Design Memo（AGENTS.md §1）へ
展開する。

組織化する 1 原則:

> **楽譜 = 複数の AI 生成器の上に立つ「コンパイルターゲット」。** 楽譜の各フィールドは
> 「その生成器で grip が実証されたチャネル」だけを"保証"し、それ以外は"助言"に格下げする。

これにより既存資産が 1 目的に向く:
- **K 系列 grip** = どのフィールドを楽譜に載せてよいか（保証 vs 助言）の実証
- **roundtrip / fixity** = 演奏が楽譜を保ったかの検証層
- **genre calibration / generator bias** = 生成器ごとの癖 = デバイスプロファイル
- **RPE / SVP / Composition Score** = 楽譜本体とその抽出器

## マージする既存研究（部品取りの倉庫）

| 領域 | 借りるもの | マージ先 PR |
|---|---|---|
| MIR（tempo/key/timbre 推定）| 既存 librosa/mir_eval。検証ベンチ標準化 | PR2 |
| music captioning / auto-tagging（CLAP / MuLan / CLAMP3）| 学習センサー（prompt↔audio 適合度）| PR2 |
| controllability / control-adherence 評価 | 属性制御指標、grip の位置づけ | PR1 |
| disentanglement（DCI / MIG / modularity）| 直交性 = importance matrix の定式化 | PR3 |
| expressive performance rendering（EPR）| 楽譜=不変 / 演奏=テイク差 の線引き | PR3 |
| AI 生成音楽検出 / distribution shift | generator bias 指紋 = デバイスプロファイル | PR3 |

参照: `docs/controllability_poc.md`（K 系列 grip）, `docs/roundtrip_preservation.md`（fixity）,
`docs/genre_calibration_planning.md`（generator bias）, `docs/learned_models_policy.md`（学習層隔離）,
`docs/ai_music_daw_vision.md`（楽譜=AI 音楽の MIDI ビジョン）。

---

## PR 1 — 制御プロファイル: 楽譜が「効くチャネル」を知る

**目的**: `CompositionScore` に「どの生成器でどのフィールドが効くか（grip_class）」を持たせ、
楽譜を**自己記述的**にする。これが「AI が演奏できる楽譜」の核心的差別化。

**スコープ（in）**:
- `CompositionScore` に optional `control_profile` を追加。**既存の `fixity` ブロックの
  パターンを踏襲**（`src/svp_rpe/compose/models.py` の fixity: optional dict・physical キーに
  対する検証・空なら非 serialize・`extra="forbid"`）。
  ```
  control_profile: dict[str, GeneratorProfile] | None = None   # key = "suno" / "musicgen" / ...
  GeneratorProfile = dict[field_name -> ControlGrip]
  ControlGrip:
    grip_class: Literal["tight", "loose", "dead"]   # 保証 / 助言 / 無効
    grip: float | None      # 効果量 d
    sensor: str | None       # 観測センサー名
    evidence: str | None     # 出所参照（例 examples/control/k2/expected_grip.json）
  ```
- `field_name` は `PhysicalLayer` のキーに対して検証（fixity の検証ロジックを流用、未知キー/
  欠落を fail-fast）。
- **初期データ投入**: 既存の `examples/composition/*/composition_score.yaml` に
  `control_profile.suno = {bpm: tight(d=1.61), brightness: tight(d=0.86)}` を K2（#117）から。
- フィールド→backend 条件付けチャネルの**対応表**を docs に記述（score field → text prompt /
  melody 条件付け / 時系列曲線、機種別）。実コンパイラは PR 範囲外（forward work）。

**スコープ（out）**: 条件付けチャネルへの実コンパイル（M5 相当）。プロファイルの自動学習。

**受け入れ条件**:
- `control_profile` を持つ Score がラウンドトリップ load/serialize で不変。
- 未知 field / 不正 grip_class が fail-fast。
- K2 由来の suno プロファイルが少なくとも 1 つの example Score に載り、参照が解決可能。

**テスト**: スキーマ検証 / PhysicalLayer キー整合 / serialize-without-empty / snapshot。

**依存**: なし（K2 #117 の出力をそのまま使う）。**最初に着手すべき基盤 PR。**

**マージする研究**: controllability / control-adherence 評価（grip_class = 属性制御の
信頼度ラベル）。

---

## PR 2 — 検証層: 楽譜準拠テスト + 学習センサー（CLAP）

**目的**: 「AI の演奏が楽譜を守ったか」を測る検証ループを製品化する。ルールセンサー（MIR・
既存）で測れない timbre/genre 忠実度の穴を、学習センサー（CLAP）で埋める。

**スコープ（in）**:
- 既存の roundtrip ハーネス（`src/svp_rpe/roundtrip/`）を「**楽譜準拠テスト**」として再定義:
  Score → 演奏（生成）→ extract → Score と比較し、`control_profile` が tight と宣言した
  フィールドが実際に保たれたかを判定。
- **CLAP（または MuLan/CLAMP3）を learned 補助センサーとして配線**。
  `docs/learned_models_policy.md` の**隔離原則を厳守**（`LearnedAudioAnnotations` へ隔離、
  ルール evidence に混入させない、OSS ライセンス確認）。prompt↔audio / score↔audio の
  cosine 適合度を「学習版 grip」として算出し、ルール版 grip と相互検証。

**スコープ（out）**: CLAP の fine-tune。学習センサーをルール層の置き換えにすること（あくまで補助）。

**受け入れ条件**:
- 楽譜準拠テストが、tight 宣言フィールドの保持/非保持を判定し表で出す。
- CLAP センサーは隔離境界を越えない（`_assert` 系で混入を固定、policy テスト追加）。
- CLAP 不在環境ではフォールバックして決定論パスが動く（オプショナル依存契約）。

**テスト**: 楽譜準拠判定 / 隔離境界 / CLAP オプショナル依存フォールバック。

**依存**: PR1（どのフィールドを検証するかは control_profile が示す・緩い依存）。CLAP 依存追加。

**マージする研究**: MIR（ルールセンサー・既存）+ music captioning 埋め込み（CLAP/MuLan/CLAMP3）。

---

## PR 3 — 制御品質: 直交性（K3=DCI/MIG）+ 機種デバイスプロファイル

**目的**: ツマミ同士の独立性（操作盤としての質）と、生成器ごとの癖補正を入れる。

**スコープ（in）**:
- **K3 直交性を DCI / MIG で定式化**。grip ハーネス（`src/svp_rpe/control/`）を N×N の
  importance matrix へ拡張（ツマミ i が観測 j をどれだけ動かすか）。対角 = grip（既存）、
  非対角 = 干渉。DCI の disentanglement/modularity 定義を流用し、tight/loose/dead を
  informativeness の離散化として位置づける。
- **generator デバイスプロファイル**。`docs/genre_calibration_planning.md` の generator bias
  （例: Suno は bright 得意/dark 苦手・脱トーナル化・mid 削り）を**機種ごとの補正プロファイル**
  として構造化し、楽譜コンパイル時のヒントにする。

**スコープ（out）**: 補正の自動学習。全機種網羅（まず suno・musicgen）。

**受け入れ条件**:
- K3 行列が出力され、DCI/MIG 指標値が算出される。fixture→matrix は決定論で snapshot 固定。
- 1 機種（suno）のデバイスプロファイルが構造化され、PR1 の control_profile と接続可能。

**テスト**: 直交性行列の決定論 snapshot / DCI・MIG 計算 / デバイスプロファイル スキーマ。

**依存**: PR1（profile 構造）。K3 は追加の A/B 生成バッチ（人手律速）。calibration データ。

**マージする研究**: disentanglement（DCI/MIG）+ EPR（楽譜/演奏分離）+ distribution shift
（device profiling）。

---

## 順序と律速

```
PR1（基盤・依存ゼロ・即着手可）
  └─> PR2（検証層・CLAP 依存追加）
  └─> PR3（制御品質・生成バッチ人手律速）
```

- **PR1 が最優先**: K2 の実測がそのまま初期データになり、依存ゼロで「楽譜が効くチャネルを
  知る」状態を立てられる。
- PR2/PR3 は PR1 の `control_profile` を土台に並走可（PR2=学習依存、PR3=生成バッチ律速）。

## 本ロードマップで「先行研究が薄い＝独自貢献」な点（記録）

実用物が目的なので独自性は狙わないが、結果的に新規な部分は記録しておく:
- **黒箱生成器を決定論・APIキー不要・ルールベースの計器で測る**（業界標準は学習埋め込み）。
- **「センサー盲（測れてない）」と「死んだツマミ（効いてない）」の弁別**（K1→K2 で素材依存と判明）。
- **roundtrip の fixity / 4 値診断**。

## Forward work（3 PR の外）

- M5 相当: 楽譜フィールド → 条件付けチャネル（MusicGen melody / Music ControlNet 曲線）への
  実コンパイラ。
- EPR の performance-parameter を使った「楽譜=不変 / 演奏=テイク差」の線引き精緻化。
- デバイスプロファイルの機種拡張（udio 等）。
