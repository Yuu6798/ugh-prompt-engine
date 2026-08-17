# DESIGN VG-E0 — Evolution Graph 実験契約 v0.1（run 4 非依存層）

- 起草: 2026-08-17（Claude 設計）。上位構想 = User 提供
  「VoiceGenesis進化論 v0.1」（進化グラフ・品質多様性・評価器共進化。以下「進化論文書」）
- 位置づけ: 進化論文書 §14「次に実装する最小単位」のうち、**学習済みモデルを
  一切必要としない実験契約層**（schema 凍結・単体グリッド Archive・変異
  オペレータ・台帳規約）を先行実装する。Probe 合成・実評価など run 4
  checkpoint に依存する層は**インターフェース定義のみ**とし、実装は run 4
  ゲート判定後の VG-E1 で行う
- 本設計と進化論文書が食い違ったら、**数値・schema は本書が正**（文書は
  Research Draft であり実装契約を持たないため）。思想の食い違いは User 決裁

## 0. スコープ境界（run 4 依存 / 非依存の分割）

| 層 | run 4 依存 | 本書 (VG-E0) |
|---|---|---|
| VoiceGenome / Lineage / EvaluationRecord / HackRecord schema | なし | **実装** |
| 三角形単体（barycentric）上の座標演算・niche グリッド・近縁距離 | なし | **実装** |
| 変異・交配オペレータ 5 種（座標 + seed レベル） | なし | **実装** |
| MAP-Elites 型 Archive（elite 保存・保護 Archive・occupancy） | なし | **実装** |
| Probe 合成（genome → wav） | checkpoint 必須 | インターフェースのみ |
| Hard Quality Gate の閾値 | run 4 実測必須 | フィールド定義のみ（値は凍結しない） |
| ブラインド耳判定バッチ | 合成物必須 | S2 規律を参照（再実装しない） |

## 1. Voice Genome（`voice-genome/0.1`）

個体 = **3 アンカー単体上の重心座標 + 演奏 seed + 来歴**。進化論文書 §4 の
サブゲノムのうち、run 4 世代で実在するのは Identity 座標のみ（Phonation /
Performance の Adapter 系は将来フィールドとして予約だけする）。

```yaml
schema: voice-genome/0.1
genome_id: <正規形 JSON の sha256 先頭 16 hex>   # 内容アドレス。自己言及を避けるため genome_id 自身は正規形から除外
coords:            # 重心座標。合計 1.0。各値は小数 6 桁へ丸めて正規化（丸め後に再正規化し、最終桁誤差は最大成分が吸収）
  ritsu: 0.500000
  pjs:   0.200000
  user:  0.300000
seed: 11           # 演奏 seed（Performance Revision 軸）。Identity は coords が担う
lineage: L-C       # L-R / L-P / L-U / L-C / NOVELTY（§3 の帰属規則から機械決定。手書き上書き禁止）
generation: 1      # 創始個体 = 0
parents: []        # genome_id の列。創始 = 空、変異 = 1 要素、交配 = 2 要素
operator: founder  # founder / drift / vertex_pull / reseed / edge_walk / novelty_jump
operator_params: {}          # オペレータ毎の全パラメータ（再現に必要な rng seed 含む）
anchors_provenance: null     # run 4 前は null。以後 {checkpoint_sha256, embed_sha256: {ritsu, pjs, user}} を必須化
notes: ""
```

- **決定論**: genome_id は `coords/seed/lineage/generation/parents/operator/
  operator_params` の正規形 JSON（キー昇順・小数 6 桁固定表記）から導出。
  同一内容 → 同一 id。壁時計・環境情報は id に入れない
- **Identity Freeze / Performance Revision の分離**（進化論文書 §4.3）:
  coords が同一で seed だけ違う個体は「同一 Identity の別演奏」。Archive の
  elite 比較では同一 Identity 群の最良 seed を代表とする

## 2. 単体幾何（simplex 演算の凍結仕様）

- 座標空間 = 2-単体 Δ²（3 頂点 ritsu/pjs/user、成分非負・合計 1）
- **丸め規約**: 全演算の出力は小数 6 桁へ丸め、合計 1.000000 になるよう
  最大成分で残差吸収。これを `normalize()` として一元化（forge_triangle の
  有限性検証と同思想。NaN/inf は即例外）
- **距離**: 近縁度 = 重心座標の L1 距離（進化論文書 §5.3 の「latent 距離」の
  run 4 世代における唯一の実体。音響距離は VG-E1 で追加予定のフィールドのみ
  予約）。**近縁交配抑制の閾値 d_min は本書では凍結しない**（初世代分布の
  実測後に凍結 — 文書 §5.3「PoC の分布を見て校正する」に従う）

## 3. 系統（Lineage）と niche グリッド

### 3.1 系統帰属（機械決定・凍結）

| lineage | 条件（coords に対して） |
|---|---|
| L-R | ritsu ≥ 0.55 |
| L-P | pjs ≥ 0.55 |
| L-U | user ≥ 0.55 |
| L-C | 上記いずれでもない（内部均衡帯） |
| NOVELTY | novelty_jump 由来の個体は座標によらず 1 世代の間 NOVELTY 隔離（次世代で座標帰属に復帰） |

0.55 は「単一ドナー主導と呼べる最小の優勢度」として凍結する（頂点 = 1.0、
三重点 = 1/3。中点 0.5 では 2 ドナー拮抗と区別できないため 0.05 のマージンを
置く）。変更は本書の改訂 PR による。

### 3.2 niche グリッド（MAP-Elites の behavior 空間）

- 単体を **辺分割数 N=5 の三角格子（小三角形 25 セル）**に分割する。セル id は
  `(floor(r*N), floor(p*N), floor(u*N))` から一意導出（境界は辞書順で下側
  セルへ割当・全セル網羅かつ非重複を実装のプロパティテストで担保）
- 25 セルは「人間が 1 世代を追跡できる規模」（文書 §11.3）に合わせた初期値。
  拡張は Archive の再構築を伴うため schema 版番を上げる
- **Archive**: セル毎に elite 1 個体（品質スコア最大）+ **保護スロット 1**
  （品質床未満だが系統的に唯一な個体の保留 — 文書 §7.3）。追い出しは
  記録付き（絶滅も研究資産）

## 4. オペレータ 5 種（座標 + seed レベル・全決定論）

| operator | 入力 | 動作 | operator_params |
|---|---|---|---|
| drift | 親 1 | 重心座標を一様ランダム方向へ最大 step の微動（単体内へ射影） | {rng_seed, step≤0.08} |
| vertex_pull | 親 2 | 2 親の重み付き平均 → 指定頂点方向へ pull 混合 | {weight, vertex, pull≤0.2} |
| reseed | 親 1 | coords 不変・seed のみ変更（Performance Revision） | {new_seed} |
| edge_walk | 親 1 | 指定 2 頂点を結ぶ辺方向のみに沿って移動（軸制限変異） | {rng_seed, edge, step≤0.1} |
| novelty_jump | 親 1 | 一様サンプルした遠方座標へ跳躍。NOVELTY 隔離付与 | {rng_seed} |

- 乱数は必ず `operator_params.rng_seed` から `random.Random(rng_seed)` で
  生成（グローバル乱数禁止・NumPy 非依存 = SIMD 契約の外に置く）
- 上限 step / pull は凍結値。文書 §5.2「意味付き変異 — 少数の要素だけを
  変え原因を特定可能に保つ」の実装
- 交配の**系統内 / 系統間判定**はオペレータではなく台帳が行う: 親 2 個体の
  lineage が異なる vertex_pull は自動的に NOVELTY 隔離（文書 §5.2 の
  Novelty Branch 隔離を機械化）

## 5. EvaluationRecord / HackRecord（フィールド凍結・値は VG-E1）

```yaml
schema: evaluation-record/0.1
genome_id: ...
probe_set: d3-probe/0.1        # SCORE_REGISTRY 4 スコア × pin 済み spec（既存資産をそのまま Probe Set と再宣言）
evaluator: {kind: training|hidden|human, version: ...}
axes: {}                        # 軸名→値。軸語彙は VG-E1 で凍結（総合点フィールドは定義しない — M3 の「総合スコア恒久禁止」を継承）
blind_batch: null               # 人間評価時のみ: S2 規律のバッチ id
verdict: null                   # gate 判定（pass/fail/hold）。閾値は VG-E1 凍結後のみ記入可
```

```yaml
schema: hack-record/0.1
genome_id: ...
symptom: ""                     # 代理スコアと隠し評価の乖離の記述
evaluator_version: ...
discovered_by: ...              # blind batch id / 監査手順の参照
disposition: retained           # retained（敵対的資産として保存）/ superseded
```

- **総合 1 点スコアのフィールドを作らない**ことを schema レベルで強制
  （進化論文書 §6 と本リポジトリ M3 の確定裁定の合流点）

## 6. 台帳と provenance（既存規律の継承）

- 全個体・全評価・全 Hack は append-only の台帳ファイル
  （`voice_genesis/evolution/ledger/` 配下 JSON、1 個体 1 ファイル・
  ファイル名 = genome_id）に記帳し、**変更は PR 経由のみ**（intent graph /
  台帳系列と同じ運用）
- run 4 checkpoint が確定したら `anchors_provenance` を全創始個体に記入して
  凍結（それまでの genome は「座標定義のみの仮個体」であり合成物を持たない）

## 7. Acceptance Criteria（VG-E0 出口）

- [ ] schema 4 種の loader / validator（fail-closed・未知フィールド拒否・
  genome_id 再計算一致検証）
- [ ] simplex 演算（normalize / L1 距離 / セル割当）のプロパティテスト
  （網羅・非重複・丸め安定・決定論）
- [ ] オペレータ 5 種の決定論テスト（同一入力 → バイト同一の genome）+
  単体内保証 + 系統間 vertex_pull の NOVELTY 隔離
- [ ] Archive の elite 更新・保護スロット・追い出し記録のテスト
- [ ] 創始 3 個体（L-R/L-P/L-U 各頂点）+ 中央 1 個体（L-C）を生成する
  ブートストラップが動き、台帳に 4 ファイルが決定論的に生成される

## 8. VG-E1 への引き継ぎ（run 4 依存・本書の対象外）

- Probe 合成配線（genome.coords → forge_triangle → gate_synth_run4）
- Training evaluator 軸語彙の凍結（svp_rpe 抽出メトリクスからの選定）と
  Hard Gate 閾値の凍結（run 4 ゲート③の実測分布から）
- 第 0 世代（run 4 判定材料④の個体群）の台帳取り込みと初回 MAP-Elites 充填
- 近縁閾値 d_min・音響距離の校正
