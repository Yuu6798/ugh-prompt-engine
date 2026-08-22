> **正本注記（2026-08-22 編入）**: 本文書は User 策定の返済計画 v1.0 の無改変収載である。
> v1.1 裁定による訂正は `DEBT_ADJUDICATION_v1.1.md` を参照。本文と v1.1 が乖離する場合は v1.1 が勝つ。

# VoiceGenesis 技術・研究負債返済計画 v1.0

**策定日:** 2026-08-21  
**対象:** VoiceGenesis / Foundry / run4–run8 系列  
**目的:** 過去runの証拠品質を引き上げつつ、Run8とGenome Architecture PoCを止めずに前進させる。

---

## 0. 基本方針

VoiceGenesisは短期間で探索・実装が先行したため、現在の主な負債は「実装不足」よりも、

- 測定仕様の未凍結
- 因果帰属の弱さ
- 再現性の未確認
- provenance / pin / manifest の不均一
- 過去runの評価粒度不足

に集中している。

返済方針は次の3原則とする。

1. **結論を壊す負債から返す。**
2. **過去を完全復元しようとして本線を止めない。**
3. **新しいrunでは同じ負債を二度と作らない。**

---

# 1. 負債の分類

## 1.1 技術負債

実装・再現・証拠保存に関する負債。

例:

- dataset / config / checkpoint SHA の不足
- artifact manifest の不足
- ExecutionProfile の不足
- provenance の弱さ
- run間比較の自動化不足
- fail-closed 不足
- fixed probe 出力の未保存
- cost record の欠落

## 1.2 研究負債

実験設計・測定・因果推論に関する負債。

例:

- 複数介入の交絡
- 測定器の未校正
- TRF主観測4値の仕様未凍結
- H0–H5の裁定代数未凍結
- human evaluation の粒度不足
- run間変動と介入効果の分離不足
- 「改善した」と「なぜ改善した」の混同

---

# 2. 優先度

## P0 — 今後の結論を壊す負債

新しい高コスト学習runの前に返済必須。

- B-1: TRF主観測4値の測定仕様
- B-2: H0–H5の裁定代数
- run7の再現性検証（run8-R）
- run5–7の現行測定規約による再測定
- 過去の因果主張の強度再分類
- fixed probe / reference output の凍結

**P0が未完のまま新しい本学習runを増やさない。**

## P1 — 証拠力を弱める負債

本線と並行して返済。

- dataset / config / artifact pin の統一
- provenance ledger の統一
- run比較の自動レポート
- ritsu / PJS の最終変換後データ pin 強化
- cost record の標準化
- archive / manifest の正本化

## P2 — 歴史・補助情報

必要時のみ返済。

- 古い費用の厳密実測
- 旧runの補助ログ
- 既に主結論へ影響しない古い環境情報
- run4以前の完全再現

---

# 3. 実行トラック

## Track A — Debt Repayment

P0を最優先で返済する。

### A-1. B-1 測定器の凍結

今すぐ数値を紙だけで決めず、PR-1の校正実測で凍結する。

凍結対象:

- analysis window
- hop
- score boundary alignment
- voicing判定
- F0判定
- mel表現
- mel正規化
- persistence集約法
- 欠測処理
- 単位
- worked example
- reference output

手順:

```text
meta-contract freeze
        ↓
calibration set
        ↓
候補測定仕様を比較
        ↓
物理的安定性・再現性で選定
        ↓
B-1 v1.0 freeze
```

禁止:

- 本番360セルの結果を見て仕様を選ぶ
- AUC最大化で測定器を選ぶ
- run5/6/7の良否ラベルへ合わせて測定器を調整する

### A-2. B-2 裁定代数の凍結

B-1凍結後に実施。

H0–H5ごとに以下を機械判定可能にする。

- participating axes
- direction
- tolerance
- minimum supporting cells
- contradiction rule
- aggregation rule
- supported / refuted / undetermined
- worked example

Toleranceは「群を綺麗に分ける値」ではなく、

- 同一条件反復誤差
- process間再現誤差
- 数値量子化誤差

から導出する。

### A-3. run8-R 再現性Gate

run7を同一条件で再実行する。

固定対象:

- dataset bytes
- row order
- config
- seed
- initial state
- dependency pins
- ExecutionProfile
- sampler settings

比較対象:

- 40K checkpoint
- exported ONNX
- speaker embeddings
- phonemes.json
- fixed probe WAVs
- relevant feature outputs

判定:

```text
bit-identical
→ 単一run差分の因果主張を強くできる

non-identical
→ run8-B単発の因果主張を exploratory へ格下げ
```

---

## Track B — Run8

現行DiffSinger系の診断・修復。

役割:

- terminal release failure の原因候補を絞る
- Duration / SP / speaker conditioning の因果レバーを確認
- 現行アーキテクチャの上限を確定
- 将来Genome Architectureとの比較基準線を作る

**新規本学習はTrack AのP0 Gate通過後。**

---

## Track C — Genome Architecture PoC

Run8とは独立して並行可能。

目的:

> Identity と Performance Skill を別経路から供給し、再結合可能かを既存資産で検証する。

最初のP0 PoC:

```text
R0 = Ritsu Identity + neutral
R1 = Ritsu Identity + PJS F0
R2 = Ritsu Identity + PJS Duration
R3 = Ritsu Identity + PJS F0 + Duration
R4 = Ritsu Identity + PJS F0 + Duration + Release
P0 = PJS reference
```

PoC成立条件は重くしすぎない。

1. Ritsu系Identityとして成立
2. PJS由来Performance要素の交換で明瞭な変化
3. 同一設定で再現可能

PoC成立後のみ、

- Separation P1
- Architecture Candidate P2
- Identity × Performance Genome 正式化

へ進む。

---

# 4. 過去runの救済優先順位

## Tier 1 — run5–7

最優先。

理由:

- 単一介入設計に近い
- 比較価値が高い
- current architecture の主要基準線
- run8の直前世代

返済内容:

- current TRF v1.0で再測定
- fixed probeで再生成
- human evaluationを現行書式へ再記帳
- causal claim strengthを再分類
- artifact / pin / provenance の欠損確認
- run差分レポート生成

### 完済条件

```text
[ ] dataset identity確定
[ ] config identity確定
[ ] code commit確定
[ ] checkpoint identity確定
[ ] fixed probe再生成
[ ] TRF v1.0再測定
[ ] human評価再記帳
[ ] causal claim strength再分類
[ ] unresolved itemsを明示
```

## Tier 2 — run4

完全復元は非目標。

既に回収済みの資産を基に、

- checkpoint SHA
- train log
- TensorBoard
- NaN/Inf
- gate command
- anchor WAV provenance

を整理する。

ただし、

```text
D3追加 + User追加
```

の複数介入交絡は、文書整理で因果復元しない。

必要なら将来、別の対照実験として再構成する。

### run4で禁止する主張

```text
「D3が改善させた」
「Userが改善させた」
```

をrun4単体から断定しない。

許容:

```text
「run4で改善が観測された」
```

まで。

## Tier 3 — run1–3 / F系の旧資産

必要になった時のみ救済。

全歴史を現行基準へ無理に再構築しない。

---

# 5. 因果主張の強度ラベル

今後の全recordで使用する。

## C3 — Strong

- 単一介入
- baseline再現
- 決定論または十分なreplicate
- 測定仕様凍結済み
- controlあり
- artifact完全pin

## C2 — Moderate

- 単一介入
- baselineあり
- 走行間変動の完全排除なし
- 測定仕様は凍結済み

## C1 — Suggestive

- 相関
- 少数話者
- 不完全control
- 介入以外の差分が残る

## C0 — Descriptive

- 観測記録のみ
- 因果帰属不可

過去runの文章は消さず、現在の強度ラベルを追記する。

---

# 6. 新規runの「負債を増やさない契約」

今後の全本学習runで以下を必須とする。

```text
Run Contract

[ ] single intervention宣言
[ ] baseline run / parent run宣言
[ ] code commit SHA
[ ] dataset manifest SHA
[ ] dataset row order
[ ] config SHA
[ ] dependency pins
[ ] ExecutionProfile
[ ] seed / sampler settings
[ ] expected speaker map
[ ] checkpoint SHA
[ ] artifact manifest
[ ] fixed probe set
[ ] measurement spec version
[ ] hypothesis algebra version
[ ] human evaluation protocol
[ ] cost record
[ ] failure / abort criteria
[ ] claim-strength target
```

1項目でも必須欄が欠ける場合は、

```text
本学習開始禁止
```

とする。

---

# 7. Debt Ledger

負債はファイル単位・run単位で台帳化する。

推奨スキーマ:

```yaml
debt_id: VG-DEBT-001
scope: run7
class: research
priority: P0

problem:
  summary: run-level determinism unknown

impact:
  affected_claims:
    - teacher replacement effect
    - run7 vs run6 comparison

repayment:
  method: run8-R
  evidence_required:
    - checkpoint hash
    - ONNX hash
    - fixed WAV hash

status: open

close_condition:
  - deterministic rerun adjudicated
  - claim-strength relabeled
```

status:

- `open`
- `in_progress`
- `repaid`
- `accepted_residual`
- `unrecoverable`

`unrecoverable` は失敗ではなく、証拠境界の明示とする。

---

# 8. 返済しないもの

以下は意図的に返済対象外とする。

- 過去すべての環境を完全再現する
- run4以前の全耳判定を再現する
- 古いcostを推測で埋める
- 失われた情報を推定値で正本化する
- 既に主結論へ影響しないP2を先に直す

原則:

> **unknownを埋めるために推測しない。**

---

# 9. 実行順

```text
Phase D0
Debt ledger作成
+ 新規Run Contract凍結

        ↓

Phase D1
B-1 calibration / freeze

        ↓

Phase D2
B-2 H0–H5 algebra freeze

        ↓

Phase D3
run8-R

        ↓

Phase D4
run5–7 current-spec再測定

        ↓

Phase D5
causal claim strength再分類

        ↓

Phase D6
P1 pin/provenance強化
```

並行:

```text
Track C Genome P0
CPU / WORLD PoC
```

P0返済中もGenome P0は実行可。

---

# 10. 返済完了の定義

VoiceGenesis全体の「Debt Repayment v1 完了」は次で定義する。

```text
[ ] B-1 v1.0 freeze
[ ] B-2 v1.0 freeze
[ ] run7 reproducibility adjudicated
[ ] run5–7 remeasurement complete
[ ] run5–7 claim-strength relabeled
[ ] new Run Contract enforced
[ ] P0 debt ledger = 0 open blockers
```

P1/P2が残っていても、上記を満たせばv1返済フェーズは完了とする。

---

# 11. この計画の非目標

この返済計画は、

```text
VoiceGenesisの開発を止めて
過去を完全に掃除する計画
```

ではない。

目的は、

```text
過去の重要な証拠を救済
+
今後の実験品質を固定
+
Run8 / Genome Architectureを継続
```

することである。

---

# 12. 最終裁定

VoiceGenesisは、探索速度が測定・因果検証を上回ったことで研究負債を抱えた。

しかし現時点では、

- run4の監査資産回収
- checkpoint hash実体照合
- fail-closed
- single intervention
- dosage accounting
- provenance
- manifest
- TRF設計
- run8-R計画

へ移行しており、負債は「増加局面」から「返済可能な管理局面」へ入っている。

したがって今後は、

> **P0だけを先に完済し、P1/P2は本線と並行で返す。  
> 新しい高コストrunでは同じ負債を作らない。**

これをVoiceGenesisの標準運用とする。
