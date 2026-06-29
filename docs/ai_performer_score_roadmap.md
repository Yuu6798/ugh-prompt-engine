# AI 演奏者のための楽譜 — マージロードマップ（PR 3 本構成）

## North Star

目的は**独自理論の構築ではなく、「AI が演奏者として使う楽譜」という実用物**を作ること。
そのために、これまでの蓄積（特に K 系列 grip・roundtrip fixity・genre calibration）と
**既存研究の道具を遠慮なくマージする**。本ロードマップは、壁打ちで策定したマージマップを
**実装可能な PR 群（PR1 → PR1.5 → PR2 → PR3）** に固めたもの（2026-06-29 壁打ちで PR1.5 を
新設。下記「改訂方針」を参照）。各 PR は着手時に個別の Design Memo（AGENTS.md §1）へ展開する。

組織化する 1 原則:

> **楽譜 = 複数の AI 生成器の上に立つ「コンパイルターゲット」。** 楽譜の各フィールドは
> 「その生成器で grip が実証されたチャネル」だけを"保証"し、それ以外は"助言"に格下げする。

これにより既存資産が 1 目的に向く:
- **K 系列 grip** = どのフィールドを楽譜に載せてよいか（保証 vs 助言）の実証
- **roundtrip / fixity** = 演奏が楽譜を保ったかの検証層
- **genre calibration / generator bias** = 生成器ごとの癖 = デバイスプロファイル
- **RPE / SVP / Composition Score** = 楽譜本体とその抽出器

## 改訂方針（2026-06-29 壁打ちで確定）

本ロードマップは当初 PR 3 本だったが、壁打ちで以下を確定し **PR 1.5 を新設**した。

**1. 本命は実用物（楽譜）であって測定器ではない。** 当初 3 本は PR2/PR3 が検証・計測寄りで、
「楽譜→演奏」のコンパイルループが forward work（M5）に追い出されていた。だが調査で
**コンパイル脚は既に二本ある**ことが判明:

- `compose/prompt_renderer.py` の `ExternalPromptAdapter` = 楽譜 → 外部生成器プロンプト（Suno 等）
- `perform/performer.py` の決定論 performer = 楽譜 → 音声（C4/R0 ハーネス）

足りないのは `ExternalPromptAdapter` が `control_profile` を見ていない配線に加え、それを
**検証可能にする前提移行**（フィールド粒度の segment/drop accounting・backend selector・
priority エイリアス。詳細は PR1.5 節）。いずれも既存アダプタ近傍の改修で M5（時系列条件付け
コンパイラ）には踏み込まない。よってコンパイルループを閉じる作業は M5 ではなく PR1 の隣接
作業（**PR 1.5**）として昇格させ、測定器寄りの PR2/PR3 より前に**楽譜を「演奏を出せる実用物」
として一度立てる**。

**2. 決定論 vs 非決定論は「緊張」ではなく「層」。** 物理層（PhysicalRPE）＝保証チャネル＝
決定論で grip 実証する領域（今固める段階）。意味層（SemanticRPE）＝助言チャネル＝ルールで
読み切れず**非決定論の探索（学習センサーによる読解）を要する**領域。「保証 vs 助言」の線が
「決定論 vs 探索的非決定論」の線と重なる。したがって PR2 の CLAP は「測定精度向上の補助」
ではなく **「意味層の読解器」** と位置づける（何のために入れるかをブレさせない）。

> **契約境界（重要）**: この非決定論層は **CLAP 等の API キー不要・ローカルで動く OSS 学習
> センサーに限定**し、`docs/learned_models_policy.md` の隔離原則下に置く。`CLAUDE.md` の
> Project Overview が掲げる「API キー不要・LLM 不要・完全決定論的パイプライン」契約に従い、
> **API ベースの LLM 読解は本ロードマップの out of scope**＝採用する場合は CLAUDE.md の
> LLM-free 契約の見直しという escalation（AGENTS.md の philosophy conflict 条項）を先に要する。
> PR2 の Design Memo はこの境界の内側（OSS 学習センサー）に閉じること。

**3. 多生成器は Suno ルート確立後。ただしスキーマの形は今から保つ。** 生成器の抽象化は
机上では設計できず、1 本（Suno）を端から端まで通して初めて seam の引き方が分かる。よって
**2 本目の作業（grip 実験・生成バッチ）は遅らせる**が、**control_profile の生成器キー構造は
今から維持**し、PR1.5 では Suno 固有の癖（Style 欄字数・Exclude 欄・欄レイアウト）を
アダプタ本体に直書きせず**薄い backend descriptor の裏に隔離**する（seam を名前で引く）。
豊かな generator-bias プロファイルは PR3 まで待つ。

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
- `field_name` は `PhysicalLayer` のキーに対して検証。**fixity からは「未知キー fail-fast」
  のみ流用し、fixity の「全 physical フィールド網羅必須」ルール（`models.py` の
  validate_fixity_keys 後段）は引き継がない**＝`control_profile` は **疎（sparse）を許容**する。
  未知キーは fail-fast、欠落フィールドは「未プロファイル」として正当（PR1.5 で
  `rendering.priority` フォールバックに回る）。K2 由来の Suno プロファイルは bpm/brightness の
  みの疎な dict であり、網羅必須だと初期データ自体が弾かれるため、この差異は必須。
- **初期データ投入**: 既存の `examples/composition/*/composition_score.yaml` に
  `control_profile.suno = {bpm: tight(d=1.61), brightness: tight(d=0.86)}` を K2（#117）から。
- フィールド→backend 条件付けチャネルの**対応表**を docs に記述（score field → text prompt /
  melody 条件付け / 時系列曲線、機種別）。実コンパイラは PR 範囲外（forward work）。

**スコープ（out）**: 条件付けチャネルへの実コンパイル（M5 相当）。プロファイルの自動学習。

**受け入れ条件**:
- `control_profile` を持つ Score がラウンドトリップ load/serialize で不変。
- 未知 field / 不正 grip_class が fail-fast。
- **疎なプロファイル（例: `suno = {bpm, brightness}` のみ）が受理される**＝欠落フィールドで
  fail-fast しない（fixity の網羅必須を引き継がない）。
- K2 由来の suno プロファイルが少なくとも 1 つの example Score に載り、参照が解決可能。

**テスト**: スキーマ検証 / PhysicalLayer キー整合 / serialize-without-empty / snapshot。

**依存**: なし（K2 #117 の出力をそのまま使う）。**最初に着手すべき基盤 PR。**

**マージする研究**: controllability / control-adherence 評価（grip_class = 属性制御の
信頼度ラベル）。

---

## PR 1.5 — コンパイルループを閉じる: control_profile-aware compile

**目的**: PR1 の `control_profile` を `ExternalPromptAdapter` に配線し、**楽譜を「保証
チャネルを守って演奏に変換できる実用物」として一度立てる**。中心は既存アダプタ
（`compose/prompt_renderer.py`）への配線だが、**それを検証可能にする前提移行を伴う**
（下記スコープ in のフィールド粒度 segment/drop accounting・backend selector・priority
エイリアス）＝wiring-only ではない。M5（時系列条件付けコンパイラ）には踏み込まない範囲で、
「測定器でなく楽譜」を最短で形にする一手（改訂方針 1）。

> **本節の粒度**: ロードマップは PR1.5 の**設計意図と既知の前提**を記す。selector の
> エイリアス規則・token 移行マップの具体・validator 差異の実装など**厳密な実装契約は
> PR1.5 着手時の Design Memo（AGENTS.md §1）で確定**する。下記スコープは Design Memo の
> 入力であり、実装の完全仕様ではない。

**スコープ（in）**:
- アダプタの優先度を **`control_profile` の grip_class で駆動**（control_profile が覆う
  `PhysicalLayer` フィールドのみ）。tight（保証）フィールドを落とさない芯として優先描画し、
  loose/dead は助言＝真っ先の削減候補に格下げ（限られたプロンプト枠を効くツマミに配分）。
  **grip_class を持たないセグメントは従来どおり `score.rendering.priority` を fallback／
  tie-breaker として順位付けする**＝control_profile 外の非物理セグメント（`semantic.grv` /
  `semantic.core` / `structure` / `semantic.avoid`）に加え、**control_profile 未掲載の物理
  フィールド**（実 Suno profile は K2 の bpm/brightness のみのため `physical.key` /
  `stereo_width` / `active_rate_target` / `valley_depth_target` 等は未プロファイル）も同じ
  fallback に乗せる（grip_class を全面置換にせず、既存の prompt-order／drop 挙動を回帰させない）。
- **前提: セグメント／`dropped_elements` を `PhysicalLayer` フィールド粒度へ分解する**。
  現行 `ExternalPromptAdapter` は粗いセグメントでしか drop できず、特に `physical.optional`
  が **brightness / stereo_width / active_rate_target / valley_depth_target を 1 トークンに
  束ねている**（`compose/prompt_renderer.py`）。このままでは tight な brightness と loose な
  valley_depth_target を
  独立に keep/drop できず、`dropped_elements` も不透明な 1 トークンを返すため、下記の
  tight-last / dead-first が**検証不能**になる。よって PR1.5 はまず粗セグメントをフィールド
  単位へ割り（brightness が `semantic.core` と `physical.optional` に重複している点も整理）、
  drop 単位とトークンをフィールド粒度に揃えることを前提作業に含める。**フィールド ID は
  `PhysicalLayer.model_fields` の正式名（`active_rate_target` / `valley_depth_target` 等、短縮形
  不可）に一致させる**＝PR1 の未知フィールド fail-fast と control_profile／fixture／
  `dropped_elements` テストを齟齬なく接続する。
- 落とした助言フィールドは（フィールド粒度の）`GeneratedPrompt.dropped_elements` で返し、
  「どのフィールドを保証し、どれを助言に落としたか」をフィールド単位で可視化する。
- Suno 固有の機械的制約（Style 欄字数・Exclude＝negative チャネル・欄レイアウト）を
  **薄い backend descriptor** に隔離。`ExternalPromptAdapter.backend = "external"` が
  暗黙に "suno" 化するのを防ぐ（seam を名前で引く・改訂方針 3）。
- **フィールド分割に伴う 2 つの移行も前提作業に含める**（分割の帰結として既存契約を壊さない）:
  - **backend selector**: 既存 Score は `rendering.target_backend: external` を選ぶ一方、
    profile は `control_profile.suno` に入る。`backend = "external"` を暗黙 Suno にしない方針と
    整合させるため、**`target_backend` → control_profile キーの明示セレクタ**（`external`→`suno`
    エイリアス or 移行）を設け、アダプタが決定論的に `suno` descriptor／profile を選ぶ。これが
    無いと real-Suno 受け入れが「未プロファイルの external render」として黙って通る。
  - **priority エイリアス／移行マップ**: 既存 `rendering.priority`（`RenderingConfig.priority:
    List[str]`）は旧セグメントトークン（`physical.optional` / `physical.bpm` / `physical.key` 等）
    で書かれている。フィールド分割後は drop トークンが field ID（`brightness` / `stereo_width` /
    `bpm` / `key` 等）になるため、**旧セグメントトークン → field トークンのエイリアスマップ**を
    用意し、未プロファイル物理フィールドの fallback 順位が既存 priority 契約どおりに付く
    （実装順に落ちない）ようにする。

**スコープ（out）**: 時系列条件付けへのコンパイル（melody contour / 制御曲線 → MusicGen
melody 条件付け等）。これは引き続き M5 forward work。プロファイルの自動学習。**実 2 本目の
生成器（MusicGen 等）の採用・grip 実験**（Suno ルート確立後・改訂方針 3。本 PR は合成
descriptor で generator-awareness のみ検証し、実 backend は Suno に閉じる）。

**受け入れ条件**:
- **実 backend は Suno のみ**: 楽譜が Suno の control_profile に従い tight/loose/dead を
  尊重してコンパイルされる（実プロファイルデータは K2 の bpm/brightness）。
- **backend selector が決定論的に `control_profile.suno` を解決する**＝既存の
  `target_backend: external` Score が「未プロファイルの external render」に黙って落ちない。
- **priority エイリアスマップで、分割後も未プロファイル物理フィールドの drop 順位が既存
  `rendering.priority` 契約どおり**（実装順に落ちない）を snapshot で固定。
- 生成器横断の仕組み（control_profile キーで同一楽譜が別プロンプトへ分岐すること）は
  **合成 descriptor/profile fixture** で検証する＝実 2 本目の生成器（MusicGen 等）は引き続き
  out of scope（Suno ルート確立後・改訂方針 3）。スキーマが生成器キー駆動であることだけを
  toy fixture で固定し、実 backend を 2 本目に増やさない。
- セグメント／`dropped_elements` が **`PhysicalLayer` フィールド粒度**で、tight な
  brightness を残しつつ loose な valley_depth_target を落とす等の**独立した keep/drop が検証できる**
  （粗トークン 1 個に束ねない）。
- tight フィールドは max_chars 削減で**最後まで残る**。dead/loose が先に落ちる（フィールド
  単位の単体テストで tight-last・dead-first を確認）。
- backend 固有の制約が descriptor 側に分離され、アダプタ core から Suno 直書きが消える。
- 既存 example Score のコンパイルが決定論で snapshot 固定。

**正直な限界**: 現状 Suno の tight は bpm/brightness の 2 本のみ。PR1.5 はこの薄さを
**正直に可視化する**だけで、フィールドを grip させはしない。芯は K 系列 grip の拡張で
後から厚くなり、その都度コード変更なしに助言→保証へ昇格する（コンパイラが今後の全 grip
証拠の現金化点になる）。

**テスト**: フィールド粒度の drop accounting / control_profile 駆動の優先度 / tight 残存・
dead 先落ち / backend descriptor 分離 / コンパイル決定論 snapshot。

**依存**: PR1（control_profile スキーマ）。**PR2 の前に置く**＝PR2 の楽譜準拠テストが
手書きプロンプトでなく**実コンパイル経路**を検証できるようになる。

**マージする研究**: なし（既存アダプタへの配線＋検証可能化の前提移行）。実用物としての楽譜の核。

---

## PR 2 — 検証層: 楽譜準拠テスト + 学習センサー（CLAP）

**目的**: 「AI の演奏が楽譜を守ったか」を測る検証ループを製品化する。ルールセンサー（MIR・
既存）で測れない timbre/genre 忠実度の穴を、学習センサー（CLAP）で埋める。CLAP は
「測定精度の補助」ではなく **意味層（SemanticRPE）の読解器** と位置づける（決定論で読み切れ
ない助言チャネルを非決定論で探索する。改訂方針 2）。

**スコープ（in）**:
- 既存の roundtrip ハーネス（`src/svp_rpe/roundtrip/`）を「**楽譜準拠テスト**」として再定義:
  Score → 演奏（生成）→ extract → Score と比較し、`control_profile` が tight と宣言した
  フィールドが実際に保たれたかを判定。**PR1.5 の実コンパイル経路**（楽譜→アダプタ→生成器→
  extract→比較）を検証対象にする（手書きプロンプトの proxy でなく）。
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

**依存**: PR1（どのフィールドを検証するかは control_profile が示す）+ **PR1.5（楽譜準拠テストが
検証する「実コンパイル経路」は PR1.5 が立てる＝必須依存。これが無いと compiled score→adapter
経路が存在せず本スコープを満たせない）**。CLAP 依存追加。

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

**依存**: PR1（profile 構造）+ **PR1.5（device-profile ヒントが接続する compile seam＝
backend descriptor／control_profile-aware adapter は PR1.5 が立てる。これが無いと device
profile に取り付ける compile seam が存在しない）**。K3 は追加の A/B 生成バッチ（人手律速）。
calibration データ。

**マージする研究**: disentanglement（DCI/MIG）+ EPR（楽譜/演奏分離）+ distribution shift
（device profiling）。

---

## 順序と律速

```
PR1（基盤・依存ゼロ・即着手可）
  └─> PR1.5（コンパイルループを閉じる・既存アダプタ配線＋検証可能化の前提移行を伴う）
        └─> PR2（検証層・PR1.5 の実コンパイル経路を検証・CLAP=意味層読解器）
        └─> PR3（制御品質・生成バッチ人手律速）
```

- **PR1 → PR1.5 を最優先**: K2 の実測がそのまま PR1 の初期データになり、PR1.5 で楽譜が
  「演奏を出せる実用物」として一度立つ（測定器の副産物にしない）。
- PR2/PR3 は PR1.5 のコンパイル経路を土台に並走可（PR2=学習依存、PR3=生成バッチ律速）。
- **多生成器（MusicGen 等）は Suno ルート確立後**。スキーマの生成器キー構造は今から保ち、
  2 本目は backend descriptor + その control_profile の追加（additive）で入る（改訂方針 3）。
- **人手生成の律速を束ねる**: acoustic 4th genre / K3 の A/B バッチ等は生成が人手律速。
  2 本以上が同時に design-ready になったら 1 回のユーザーセッションへ束ねる（Genre Calib
  「1 ジャンルまとめて n=3」の一段上）。現時点は acoustic 単独で束ねる相手が無いため、
  K3 が design-ready になるまで頭の片隅メモに留める。

## 本ロードマップで「先行研究が薄い＝独自貢献」な点（記録）

実用物が目的なので独自性は狙わないが、結果的に新規な部分は記録しておく:
- **黒箱生成器を決定論・APIキー不要・ルールベースの計器で測る**（業界標準は学習埋め込み）。
- **「センサー盲（測れてない）」と「死んだツマミ（効いてない）」の弁別**（K1→K2 で素材依存と判明）。
- **roundtrip の fixity / 4 値診断**。

## Forward work（PR の外）

- **M5 = 時系列条件付けコンパイラ**: 楽譜フィールド → 条件付けチャネル（MusicGen melody /
  Music ControlNet 曲線）への実コンパイラ。**テキストプロンプトへのコンパイルは PR1.5 で
  完了**するため、ここに残るのは条件付け信号への脚のみ。
- EPR の performance-parameter を使った「楽譜=不変 / 演奏=テイク差」の線引き精緻化。
- デバイスプロファイルの機種拡張（udio 等）。
