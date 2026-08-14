# UGH Voice Genesis Engine v0.2 — 設計精査 + 仮想テスト報告

日付: 2026-08-13 / 実施: Claude（設計判定 = Fable、実装・実行・探索 = Sonnet 委譲）
前提: ugh-prompt-engine リポジトリは読み取り専用参照。コミット・push・PR は一切なし。
全成果物は scratchpad `vt_harness/` 配下（コード、`results/*.json`、`underspec_log.md`、`run_summary.md`、代表 WAV 12 点）。

---

## 1. 総合判定

**アーキテクチャの骨格と v0.1→v0.2 の改訂方向は健全。ただし現文面のままでは
Stage Gate 体系が機能しない。** 仮想テストで以下が実証された:

1. **§7.2 grip ゲートは、自らの正規化規定（z-score）によって構造的にほぼ通過不能**
   （最重要所見。4/4 軸 FAIL、grip_ratio 0.57–1.03 vs 閾値 3.0）
2. Phase 0 ゲートの帯域主張（§9）は**実測で再現・支持**された — ただし失敗様相は
   設計書の想定より危険（サイレントな誤検出）
3. R0 の §4.3 記述は自己完結でない（**17 件の未規定箇所**）。うち 1 件は
   無音・クリップ検査をすり抜ける縮退（周期性消滅）を実際に引き起こした
4. 設計書が名指しする外部資産（R0 正典・SOURCE QUARANTINE・`escape_evidence`
   スキーマ）は**いずれも svp-rpe に実在しない**。一方、機能的に等価な既存
   インフラは確立済みで、再利用すべき

判定: **v0.2 のまま実装着手は不可。§7.2 の再定義とゲート数値の束縛を行う
v0.3 を先に切るべき**（改訂項目は §5）。

---

## 2. 仮想テスト結果

### VT-1 — R0 再実装（§4.3 記述の自己完結性テスト）: 条件付き PASS

- §4.3 の記述のみから R0 を再実装し、voice_A/voice_B × MIDI 36–96 の
  全 122 ノートで非有限値・無音・クリップなしを確認（1.9 秒）。
  「C2–C7 で無破綻」という主張自体は再現可能。
- ただし実装には **17 件の仮定の補充**が必要だった（`underspec_log.md`）。
  主要な欠落: §3.1 応答関数の `intensity` がスキーマに不在 / `source_mode`
  未定義 / register 境界値・transition_width・フォルマント値・F1 追従式・
  高次減衰の折れ点すべて数値なし。**§4.3 だけでは「正典の再実装」は不可能** —
  正典はコード実体への content pin でしか成立しない。
- **縮退の実例**: breathiness の register 別ゲインが 1.0 を超えると、高音域で
  ノイズが倍音を凌駕し周期性が事実上消滅する。この縮退は振幅系の検査
  （無音・クリップ）を**すり抜け**、F0 計測を通してのみ発見された。
  → §7.4 plausibility（「人間的発声テクスチャの保持」）は現状**検出器を
  持たない宣言**であり、周期性床（register 別 HNR 下限等)の機械的
  不変条件として運用化する必要がある。

### VT-2 — Phase 0 Measurement Bench ゲート模擬: §9 の主張を再現、ただしより鋭い形で

voice_A の合成 GT 21 ノート（C2–C7、3 半音刻み）に対する F0 推定誤差:

| 推定器 | median \|err\| | max \|err\| | ≤50c |
|---|---|---|---|
| 自前（時間領域 YIN 式、C2–C7 設計） | 8.7 c | 50.4 c | 20/21 |
| librosa.pyin fmin=50/fmax=1000（§9 例示の既存帯域） | 15.0 c | **1895 c** | 15/21 |
| librosa.pyin fmin=60/fmax=2200（全帯域設定） | 10.6 c | **1209 c** | 19/21 |

- 帯域限定 pyin は C6（1046.5 Hz）で fmax を超えた瞬間に約 1 オクターブ
  誤り、以降 C7 まで回復しない。**§9 の「既存分析系を流用しない」判断は
  実測で支持**された。
- **設計書の想定を超える所見**: 失敗は NaN/unvoiced ではなく
  「自信ありげな誤値を返し続ける」**サイレント破綻**（n_nan_or_unvoiced=0）。
  実運用では GT がない限り検出不能。
- **fmax を広げても解決しない**: 全帯域設定でも fmax 境界近傍（C7）で
  1 点破綻。ゲートには「音域上端 + マージン（例: 上端の 1.25 倍）までの
  検証」を要求すべき。
- 自前推定器も無誤差ではない（max 50.4 c）。vibrato_depth 45 c を測る計器の
  誤差が同オーダーである — svp-rpe L0a で確立済みの**計器分解能開示**を
  Phase 0 ゲートの必須項目として輸入すべき。

### VT-3 — Grip Matrix（§7.2）well-posedness: 4/4 軸 FAIL、かつ定義自体の構造欠陥を実証

probe suite {C3,E4,A4,C5,C6} × 4 軸 × 5 点 sweep:

| 軸 | intended | grip (sweep_wide) | grip (per_note) | 方向一致 | ゲート |
|---|---|---|---|---|---|
| breathiness | HNR | 1.025 | 0.874 | 80% | FAIL |
| formant_scale | centroid | 0.571 | 1.385 | 100% | FAIL |
| spectral_tilt | tilt | 0.819 | 0.582 | 75% | FAIL |
| vibrato_depth | F0 変調深度 | 0.682 | 0.978 | 68%* | FAIL |

**構造欠陥の機序**（設計判定として最重要）:

- z-score を sweep 集合内で取ると、**単調に応答するどんな特徴量も物理量の
  大小に関係なく同じ z 幅に引き伸ばされる**。5 点等間隔 sweep の線形応答の
  端点 Δz は理論上 ≈2.83 が上限で、実測でも intended 側の per_note Δz は
  1.49–2.77 と全軸この天井に張り付いた。side 側も単調でありさえすれば同じ
  天井に達するため、**grip_ratio は効果量の支配性ではなく「単調な side が
  1 つでもあるか」を測る量に退化**し、≈1 に収束する。
- 閾値 3.0 は intended の理論上限 ≈2.83 と side の偶然変動 O(1) の比として
  **理論天井の上に置かれており、ほぼ通過不能**。
- すなわち §7.2 は「偽 entanglement 判定を防ぐ」ために感度比を導入したのに、
  z-score 正規化がスケール情報を消すことで**より強い形の偽 entanglement
  判定を再導入している**。自己目的に反する定義。
- z-score 母集団の未規定（underspec #14）も実害を確認: 定義の取り方で
  grip_ratio が 2 倍以上変動（formant_scale: 0.57 ↔ 1.39）。
- 副次所見 2 件: (a) side 支配特徴が 3/4 軸で HNR — 物理的軸間干渉か
  HNR 近似（倍音周辺固定帯域窓）の計器アーティファクトか本 VT では
  切り分け不能。**§7.3 の「共有計器問題」と同型の懸念が grip 側にも
  存在する**が、設計書は grip に instrument-validity caveat を適用して
  いない。(b) C3 × 大深度 vibrato で自前 F0 推定器が約 3 倍音への外れ値
  （748.79 c）を出し、vibrato_depth 軸の計測自体を汚染 — grip 計測にも
  caveat 付き measured の記録様式が必要。

**修正案**: 各特徴量の正規化基準を「sweep 内分散」でなく**計器再現性ノイズ
σ_noise**（Genome 固定・seed/jitter 実現違いの反復レンダで推定）に置く。
grip(θ) = 検出可能度(intended) / max(検出可能度(side), 1)、
検出可能度 = |Δfeature| / σ_noise。床 1 により「検出不能な side」が比を
膨らませない。さらに **side 特徴集合を軸ごとに凍結して宣言**する
（max_j は特徴を増やすほど単調に grip を下げるため、集合が動くと
ゲートの意味が変わる）。

---

## 3. リポジトリ照合（読み取りのみ）

| 設計書の前提 | 実在性 | 所見 |
|---|---|---|
| `examples/r0_diagnostic/voice_r0.py`（R0 正典） | **なし** | リポジトリ全域 grep 0 件。正典化するならリポジトリ名 + content pin（`utils/hashing.py` の既存規約）で係留必須 |
| 「R0」という記号 | **衝突** | svp-rpe では R0 = 往復保存性診断（`roundtrip/harness.py`, `perform/performer.py`）として既使用。改名推奨（例: VR0） |
| SOURCE QUARANTINE / 創生モード（§8.3） | **なし** | `quarantine` ヒットは Demucs 分離等の別文脈のみ |
| `escape_evidence` 共通スキーマ（§15） | **なし** | 依存先が未実在。§15 は「どちらが正本か + 未実在時の暫定措置」を書くべき |
| provenance 語彙 | **機能的等価物あり** | `authoring/report.py` の band 語彙 `measured / out_of_band / not_observed`、`arrange/observe.py` の D-1 語彙、`melody/provenance.py` の fail-closed instrument pin。新語彙を作らずこれに整合させるべき |
| versioned sidecar パターン（reference-set/0.1 用） | **あり** | `recast-project/0.1` / `intent-graph/0.1` が雛形 |
| 決定論シンセ | **あり** | `perform/`（performer + synth + 即時 hash 化）。R0 相当の土台に直接再利用可 |
| ABX / 人間校正の前例 | **概念はあり** | WI2 弁別ハーネス / WI3 人間校正（事前登録・被覆正直会計）。「ABX」を新造せず WI 系プロトコルを継承すべき |
| intent graph の voice/genesis ノード | **なし** | トラック開始時に登録が必要（遷移規約: evidence 必須・PR レビュー経由） |

---

## 4. 仮想テスト対象外の設計レベル指摘（文書精読による）

1. **差分文書の自己完結性違反**: 「v0.1 維持」が約 20 箇所あるが v0.1 は
   非同梱。付録 A（Genome JSON 例）も v0.1 参照のみで、VG-001 の schema は
   この文書だけでは書けない。Supersedes を名乗る版は独立に読めるべき。
2. **ゲート数値の未束縛**: §8 RQ-1 の ε、RQ-2 の δ、§9 Phase 0 の
   「許容誤差」、§7.5 の「チャンスレベル帯」、§1.5e/§7.5-6 の
   「人間的中音域」境界がすべて未定義。fail-closed ゲートは数値が凍結されて
   初めてゲートになる。VT-2 の実測から Phase 0 の凍結候補を提案できる:
   「全ノートでオクターブ誤り 0（≤100 c）かつ median ≤20 c、検証帯域は
   音域上端 + 3 半音以上」。
3. **§7.5 linkability の検定手続き未指定**: gallery サイズ・probe 数に対する
   閾値帯の依存、および具体 metric（EER / Cllr / linkability score のいずれか）
   を VoicePrivacy の語彙で固定すべき。
4. **複数 embedding の「系統の異なる」の定義**: アーキテクチャ差ではなく
   **訓練データ分布の独立**を要件にしないと、VoxCeleb 系を共有する 2 モデルは
   名目上の ensemble にしかならない（§7.3 の趣旨を骨抜きにする）。
5. **VG バックログの欠落依存**: VG-018（linkability）は識別 embedding の
   調達（ライセンス・オフライン実行可否）に依存するが、調達タスクが
   バックログにない。日本語歌唱データの具体候補列挙（§6.1）も同様に
   クリティカルパス未記述。
6. **Phase 2 ゲート「歌声として成立」が主観的**: LyricIntelligibility gate は
   言及のみで測定手続きがない。WI3 型の事前登録人間判定に接続すべき。

---

## 5. v0.3 への勧告（優先順）

1. **§7.2 全面改訂**: z-score 正規化を廃し、計器再現性ノイズ基準の
   検出可能度比 + side 特徴集合の凍結宣言 + 分母床 + grip への
   instrument-validity caveat 適用（§2 VT-3 の修正案）。改訂後に本 VT の
   ハーネスで再検証可能。
2. **ゲート数値の束縛**: ε / δ / 許容誤差 / チャンスレベル帯 / 中音域境界を
   数値凍結（Phase 0 は VT-2 実測値を初期値に）。「誰がいつどの evidence で
   数値を改訂できるか」の手続きも 1 節で明文化。
3. **Phase 0 ゲートに 2 項追加**: (a) fmax 境界マージン要件、
   (b) サイレント破綻対策としての合成 GT カナリアの常設 + 計器分解能開示
   （L0a 規約の輸入）。
4. **§7.4 plausibility の運用化**: 周期性床（register 別 HNR 下限）等の
   機械的不変条件を定義（VT-1 の縮退が現行検査をすり抜けた実例に基づく）。
5. **provenance 修復**: R0 正典を repo + content pin で係留、「R0」を改名
   （svp-rpe の既存 R0 と衝突）、§15 の依存方向を明記、schema/語彙は
   svp-rpe の band/D-1 語彙・recast sidecar パターンに整合。
6. **自己完結化**: v0.1 の維持部分（特に Genome スキーマ全体と付録 A）を
   v0.3 本文に取り込む。underspec_log.md の 17 件を仕様値として本文に転記。

---

## 6. 成果物一覧（scratchpad、リポジトリ外）

- `vt_harness/voice_r0.py` — §4.3 準拠 R0 再実装（VoiceGenome + 応答関数）
- `vt_harness/measure.py` / `vt1_check.py` / `vt2_bench.py` / `vt3_grip.py`
- `vt_harness/results/underspec_log.md` — 未規定 17 件 + 副次所見 2 件
- `vt_harness/results/bench_f0.json` / `grip_report.json` / `vt1_check.json` — 生データ
- `vt_harness/results/run_summary.md` — 実行ログ要約
- `vt_harness/results/sample_wav/` — 代表 12 レンダ

リポジトリへの書き込み・コミット・PR は行っていない。
