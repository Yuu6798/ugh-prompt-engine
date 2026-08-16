# VoiceGenesis進化論

高品質な人工歌手の「生態系」を育成するための
進化グラフ・品質多様性・評価器共進化の設計

> **状態: 野心的将来ビジョンの記録（実装予定なし）。**
> User 決裁 2026-08-15 (UTC)「将来的なビジョンとして確保しておきたいアイデア。すぐに実装する予定ではない。だが VoiceGenesis の将来的な構造はこのような遺伝的電子交配プロトコルを目指したいという野心的記録だ」（逐語）。
>
> **実装解禁の入口条件**: S2 での仮説 α（spk_embed 補間声が Quality Floor = 耳ゲートを通る）× 仮説 β（niche 間の差を User が耳で弁別できる）の両成立。成立前に Evolution Graph Engine / MAP-Elites を実装しない（「工房より計器を先に作らない」原則）。
>
> **転生モード/創生モード/V36 基盤への言及は本リポ外の将来統合先であり、S 系列のスコープに含まれない。**

---

| | |
|---|---|
| 文書版 | v0.1 |
| 状態 | Concept Design / Research Draft |
| 対象 | VoiceGenesis + Evolution Graph Engine |
| 統合先 | 転生モード / 創生モード / V36基盤 |
| 作成日 | 2026-08-16 |

**検証状態**
本書は現時点の議論を研究設計へ再構成したものです。進化計算の有効性、人間レベルの自然さ、ゼロ・ヒューマン起点の実現性は、まだ実験で確認されていません。本文では「既にある設計」「今回追加した設計仮説」「未検証事項」を分離します。

## 要約：現時点の結論

**研究の核心**
VoiceGenesisの目標を「最高得点の歌手を1人作る」から、「一定品質以上の異なる人工歌手系統を多数成立させ、長期的に多様性を維持する」へ拡張する。進捗は最高点ではなく、品質Floorを満たすVoice Spaceの被覆率で測る。

| No. | 設計判断 | 意味 |
|---|---|---|
| 1 | 一つの巨大モデルではない | 共有Backboneが歌唱能力を持ち、各人工歌手はVoice Genome／Identity latent／小型Adapterとして表現する。 |
| 2 | 学習と進化を分離 | Backbone重みは勾配学習で更新する。進化計算は主に低次元の声ゲノム、Adapter、探索方針を扱う。 |
| 3 | 二重時間尺度 | 高速ループは「変異→推論→評価→保存」。遅いループは蓄積データを使ったBackbone／Evaluatorの再訓練。 |
| 4 | 品質と多様性を両立 | Hard Quality Gateを先に通し、その後ParetoとQuality-Diversityで異なるnicheの高品質個体を保存する。 |
| 5 | 報酬ハックを資産化 | 高得点なのにHidden／人間評価が低い個体をHack DBへ保存し、次世代Evaluatorの敵対的訓練データにする。 |
| 6 | 人間歌唱は最後の校正 | Procedural／権利明確なSyntheticで起動し、不足現象が特定された場合だけTargeted Human Calibrationを行う。 |

### 既存設計と今回の拡張

| 区分 | 既にVoiceGenesisにあった要素 | 今回追加した進化論的拡張 |
|---|---|---|
| 本体 | Voice Identity / latent、歌唱生成モデル、Vocoder、Human Voice Manifold、Identity leakage抑制 | Voice Genome、系譜グラフ、変異・配合、Quality-Diversity、報酬ハック資産化 |
| 評価 | 自然さ・明瞭度・音程・独自性などの多軸評価 | 品質Floor、Hidden evaluator、niche内競争、Coverage、近縁回避 |
| 運用 | Synthetic Donor、Procedural、最後のHuman calibration | 高速進化ループと遅い再学習ループの分離 |
| 基盤 | Branch / Revision / Gate / Pareto / Merge | Lineage、Archive、Hack DB、Evaluator versioning |

### 用語の最短定義

| 用語 | 本書での意味 |
|---|---|
| Backbone | 歌詞・MIDI・表情・Voice条件から歌唱音響を生成する共有ニューラルネット。 |
| Voice Genome | 個々の人工歌手を定義する低次元latent、発声形質、Adapter、系譜・来歴情報の組。 |
| 表現型（Phenotype） | 標準Probeを実際に歌わせた音声と、その音響・知覚評価。 |
| Lineage | 親子関係、変異、世代、評価理由を持つ人工歌手の系統。 |
| Quality Floor | 繁殖・保存対象になるために必ず超える最低品質制約。 |
| Quality-Diversity | 異なる種類ごとに高品質個体を保存する探索思想。 |
| Reward Hack | 代理評価だけを攻略し、本来の品質を満たさない候補。 |
| Coverage | 品質Floorを満たす個体がVoice Spaceの何領域を占有したか。 |

## 1. 研究の目的と境界

VoiceGenesisは、既存歌手を複製するVoice Cloneではなく、生成可能な声空間から新しい人工歌手Identityを設計し、その歌手が未知の歌詞とMIDIを一貫して歌える状態を目標とする。今回の議論では、そのIdentityを人間が一件ずつ手設計するのではなく、GPU上で多数の候補を探索し、系譜を維持しながら育成する構造へ拡張した。

ただし、進化論的表現は比喩だけではない。個体、親、変異、交叉、選択、系統、絶滅、保護、外来形質導入を、すべてデータ構造と実行規則に落とす。一方で、ニューラルネットの数千万～数億個の重みを毎個体ごとに進化させることは初期PoCの目的ではない。共有Backboneは通常の誤差逆伝播で学習し、進化計算は主にVoice Genomeと小型Adapterを探索する。

**PoR（問いの核心）**
「最高品質の声を一つ得る」ことではなく、「品質の土台を壊さず、互いに異なる人工歌手系統を継続的に成立させられるか」を検証する。

### 1.1 目標

- 新規Voice Identityを生成し、複数の楽曲・音域・歌詞でも同一歌手として認識できること。
- 自然さ・明瞭度・音程・安定性の最低ラインを満たした上で、透明、粗い、暗い、明るい、息成分が多い、低音、高音など複数のnicheを維持すること。
- どの親・変異・Evaluator・checkpointから生まれたかを完全に追跡できること。
- 評価器の盲点や失敗条件を廃棄せず、次世代モデルの訓練資産へ変えること。
- 最終的に転生モードと創生モードの双方で選択可能な人工歌手ライブラリを形成すること。

### 1.2 非目標

- 既存の著名歌手へ近づけること、または特定個人の声を再現すること。
- 初期段階から3～5分の完成曲を一発生成する巨大Foundation Modelをゼロから訓練すること。
- 単一の総合点だけを最大化し、最強個体だけを残すこと。
- 自動Evaluatorの点数を、人間の知覚品質そのものとみなすこと。
- 権利不明の生成物・音源を学習可能と推定して取り込むこと。

### 1.3 現時点の検証状態

| 項目 | 状態 | 解釈 |
|---|---|---|
| VoiceGenesis基本構想 | 設計済み | Identity生成・歌唱生成・Vocoder・評価の分離は既存方針。 |
| 進化グラフ | 新規設計仮説 | 今回のGPU・品質多様性議論から追加。 |
| Quality-Diversity適用 | 有力候補 | 一般技術は確立しているが、人工歌手への適用は要検証。 |
| Reward-Hack資産化 | 設計可能 | Hack検出精度とEvaluator更新効果は要実験。 |
| Zero-Human Bootstrap | 未確認 | 人間歌唱なしで自然さの臨界点を超えられる保証はない。 |
| Human Calibration最小化 | 研究目標 | 必要成分を特定して短時間収録へ限定する。 |

## 2. 全体アーキテクチャ

（図1: 共有Backboneと進化探索を分離した二重時間尺度アーキテクチャ。原文書参照）

VoiceGenesis進化論は、共有歌唱モデルを一つ持ち、その上で多数のVoice Genomeを探索する構造を基本とする。各個体ごとに巨大なニューラルネットを複製・再訓練するのではなく、同じBackboneへ異なるIdentity latentやAdapterを与えて標準Probeを生成する。GPUはこの推論を大量並列・反復し、選抜後に必要なタイミングだけBackboneとEvaluatorを再訓練する。

### 2.1 五つの構成要素

| 要素 | 責務 | 初期PoCでの実装方針 |
|---|---|---|
| 共有歌唱Backbone | 歌詞・MIDI・Duration・Expression・Voice条件からMel等を生成 | 既存SVS Backboneまたは小型Diffusion/Flowモデルを利用し、最初から巨大化しない。 |
| Identity Generator | Voice Genomeをlatent／Adapterへ変換 | 低次元で操作可能な表現を優先。 |
| Evolution Graph Engine | 分岐、変異、交叉、系譜、世代、Archiveを制御 | Branch/Revisionを不変履歴として保存。 |
| Evaluator Stack | 品質Gate、Hidden監査、Pareto、QD、Human audit | 評価器を単一にせず、最適化用と監査用を分離。 |
| Learning Loop | 選抜・失敗・Hackデータで重みを更新 | 一定量が蓄積したcheckpoint単位で実施。 |

### 2.2 二重時間尺度

| ループ | 頻度 | 重み更新 | 主な出力 |
|---|---|---|---|
| 高速進化ループ | 各候補・各世代 | 原則なし | 新Genome、Probe音声、評価、Lineage、Archive更新 |
| 遅い学習ループ | 一定世代／一定データ蓄積ごと | Backbone／Evaluator／Adapterを更新 | 新checkpoint、新Evaluator version、再校正済み閾値 |

**重要な区別**
進化計算は「すべてのニューラル重みを総当たりする」ことではない。勾配学習は歌唱能力を作り、進化探索は成立する人工歌手の領域と系統を探す。両者を分業させることで、4090級の単一GPUでもPoCが現実的になる。

## 3. 学習・推論・進化の役割分担

ニューラルネットの学習は、入力から歌声を生成する巨大な関数の重みを最適化する処理である。推論は学習済み重みを固定し、新しい歌詞・MIDI・Voice Genomeから歌声を計算する処理である。進化探索は、その推論を多数回利用し、どのVoice Genomeがどの品質・nicheで成立するかを調べる。

```
学習（遅い）
データ + 正解/評価  →  誤差計算  →  Backbone / Evaluator重み更新  →  checkpoint

推論（速い）
checkpoint + MIDI + 歌詞 + Voice Genome  →  Probe歌声

進化（多数反復）
Genome変異  →  推論  →  評価  →  選抜/Archive  →  次世代Genome
```

### 3.1 何を「学習」し、何を「進化」させるか

| 対象 | 主な方法 | 理由 |
|---|---|---|
| Backboneの数千万～数億重み | 勾配降下・誤差逆伝播 | 高次元連続最適化は勾配法の方が効率的。 |
| Voice Identity latent | 変異・補間・交叉・QD探索 | 個体差を低次元で大量探索しやすい。 |
| 小型Adapter / LoRA | 進化選抜 + 必要なら局所勾配更新 | 系統固有の形質を保持しつつコストを抑える。 |
| 発声・歌唱パラメータ | 意味付きBranch変異 | 何を変えた結果かを追跡できる。 |
| Evaluator重み | 人間/Hidden/失敗データによる再訓練 | 探索が発見した盲点へ対応する。 |
| 探索方針・変異率 | Population-Based Training等の候補 | 世代・nicheごとに最適な探索強度が異なる。 |

### 3.2 なぜ一個体＝一つの完全モデルにしないか

完全モデルを個体として複製すると、VRAM・保存容量・訓練時間が急増し、Lineageの比較も難しくなる。初期設計では「種としての共有Backbone」と「個体としてのVoice Genome」を分ける。将来、特定系統にしか出せない形質が確認された場合のみ、その系統へ小型Adapterを付与する。

| 表現方式 | コスト | 柔軟性 | PoC推奨 |
|---|---|---|---|
| 共有Backbone + latent | 低 | 高 | 最優先 |
| 共有Backbone + 小型Adapter | 中 | 非常に高い | 第2段階 |
| 個体ごとに完全checkpoint | 非常に高い | 高いが管理困難 | 初期は非推奨 |

## 4. Voice Genome：人工歌手の遺伝子設計

Voice Genomeは「音声ファイル」ではなく、人工歌手Identityを再現可能にする構造化データである。Genomeから同じ標準Probeを何度でも生成でき、親子関係、変異箇所、使用checkpoint、乱数seed、権利来歴まで追跡できる必要がある。

### 4.1 推奨サブゲノム

| サブゲノム | 例 | 継承方針 |
|---|---|---|
| Identity / Anatomy | identity latent、声道長、formant配置、スペクトル包絡、倍音構造 | 歌手Identityの中核。Singer確定後は原則Freeze。 |
| Phonation | breathiness、声門閉鎖、粗さ、aspiration、spectral tilt | 限定範囲で変異。自然さGateと強く結合。 |
| Register | 胸声・頭声・混声の比率、切替位置、音域安定性 | 音域別Probeで評価し、破綻条件を記録。 |
| Performance Prior | vibrato rate/depth、attack/release、dynamics傾向 | 歌手固有傾向として継承。ただし曲ごとの制御と分離。 |
| Context Control | 感情、ジャンル、強度、フレージング | 原則として一時的な演奏条件。Identityと混ぜない。 |
| Provenance | 親ID、世代、変異、checkpoint、seed、権利class | 必須の監査情報。 |

### 4.2 遺伝子と表現型を分ける

同じGenomeでも、歌詞・音程・音域・強弱が違えば出力は変わる。そのため個体評価は一つのデモ音源ではなく、統制されたProbe Set全体で行う。Genomeが遺伝子、各Probe音声が表現型、評価値が環境適応度に相当する。

| Probe群 | 確認する現象 |
|---|---|
| 音素Probe | 母音・子音・音素遷移・明瞭度 |
| 音域Probe | 低音／中音／高音、声区切替、破綻点 |
| 発声Probe | 弱声、強声、息成分、粗さ、立ち上がり |
| 音程Probe | 跳躍、glide、ロングトーン、微細F0 |
| 表現Probe | vibrato、dynamics、attack/release、フレーズ終端 |
| 一貫性Probe | 複数曲・複数日・複数seedで同一Identityが維持されるか |

### 4.3 Identity Freeze

**歌手確定後の原則**
採用された人工歌手はIdentity GenomeをSHA固定し、以後の曲ではPerformance Controlだけを変更する。Identityを改変する場合は同一歌手の更新ではなく、新しい子系統としてBranchを作る。

この分離がないと、曲ごとの最適化が歌手そのものを変形させ、同一人物性が失われる。V36のFrozen Scoreと同様に、VoiceGenesisでも「Identity Freeze」と「Performance Revision」を分離する。

## 5. Evolution Graph Engine

（図2: 一世代の分岐、評価、Archive、次世代選択。原文書参照）

進化グラフでは、各候補を不変Revisionとして保存する。親を直接上書きせず、どの変異がどの結果を生んだかをBranch単位で残す。良い結果だけでなく、破綻・Hack・均質化に寄与した系統も研究資産として保持する。

### 5.1 最小Branch構成

| Branch | 主な操作 | 用途 |
|---|---|---|
| Identity | latent方向、formant、スペクトル包絡を限定変更 | 新しい声質系統を作る。 |
| Phonation | breath、closure、roughness、aspirationを変更 | 発声源の質感を探索。 |
| Performance | vibrato、attack、dynamics、register transitionを変更 | 歌い方の安定性・表現を改善。 |
| Repair | Evaluatorが特定した失敗成分だけを修正 | 因果を保った品質改善。 |
| Novelty Injection | 未占有nicheへ新規latent・Procedural形質を投入 | 局所最適・血統偏重から脱出。 |

### 5.2 意味付き変異

ランダムに全要素を同時変更すると、品質が上がっても原因を特定できない。基本Branchでは一つまたは少数のサブゲノムだけを変え、mutation operator、変化量、対象軸を記録する。複数要素を大きく変える操作はNovelty Branchへ隔離する。

```
例：限定変異
parent = VG-00421
branch = phonation
mutation = {
  breathiness: +0.08,
  glottal_closure: -0.03
}
base_revision = rev-0192
seed = 481105
→ child = VG-00421-PH-07
```

### 5.3 配合と近縁回避

Crossoverはlatent全体の単純平均ではなく、Identity、Phonation、Performanceなど意味単位で行う。親同士が近すぎる場合は、品質が高くても配合を抑制する。近縁度は系譜だけでなく、latent距離と実際の音響距離を組み合わせる。

```
d(A, B) = w_l · d_latent + w_a · d_acoustic + w_p · d_pedigree

if d(A, B) < θ_mating:
    crossover = reject / penalty
else:
    crossover = allow
```

距離の重みと閾値は仮定であり、PoCの分布を見て校正する。遠縁であること自体を品質と混同せず、「品質Gateを通った上で系統多様性を保つ」ために使用する。

### 5.4 V36基盤への対応

| V36プリミティブ | VoiceGenesisでの役割 |
|---|---|
| Branch | 変異系統・交叉案・Repair案を分離。 |
| Revision | Genome、Probe、評価、Evaluator versionを不変保存。 |
| Lease | 同一個体・同一checkpointの重複GPU実行を防止。 |
| Gate | Quality Floor、権利来歴、Identity leakage、Artifact条件を強制。 |
| CAS / single-writer | 採用Singer／Canonical Archiveの競合更新を防止。 |
| 監査ログ | 誰／どのモデル／どのmutation／どのseedで生成したかを記録。 |
| 正典変更時の失効 | BackboneやProbe Setが変わった場合、旧評価を自動で再検証対象にする。 |

## 6. 選抜：品質Floor、Pareto、Quality-Diversity

品質と多様性の両立には、すべてを一つの重み付き総合点へ潰さないことが重要である。まず絶対に破ってはいけない品質制約をGateとして適用し、その後に少数の目的軸でPareto選抜を行い、最後にBehavior SpaceのnicheごとにEliteを保存する。

### 6.1 Hard Quality Gate

```
Feasible(v) = 1  iff
  Naturalness(v)      ≥ θ_N
  Intelligibility(v)  ≥ θ_I
  PitchAccuracy(v)    ≥ θ_P
  IdentityConsistency(v) ≥ θ_C
  ArtifactGate(v)     = PASS
  IdentityLeakage(v)  ≤ θ_L
  ProvenanceGate(v)   = PASS
```

各θは固定の普遍値ではない。評価器、Probe Set、対象言語のvalidation分布から決め、最終testへ入る前にFreezeする。実験途中で閾値を都合よく動かすと、結果の比較可能性が失われる。

### 6.2 Paretoは「品質のトレードオフ」を残す

Gate通過後に、自然さ、独自性、表現力、安定性などを多目的最適化する。NSGA-IIのような非劣解選抜は、一つの総合点では消える複数の優良案を保持できる[2]。ただし目的軸が多すぎるとほぼ全候補が非劣解になるため、Phaseごとに3～5軸程度へ絞り、基本品質はGateへ移す。

### 6.3 Quality-Diversityは「種類ごとの高品質」を残す

Paretoだけでは、似た個体がPareto Frontへ密集する可能性がある。MAP-ElitesやQuality-Diversityは、ユーザーが定めたBehavior Descriptorで空間をnicheへ分割し、各nicheの最高品質個体を保存する[3][4]。VoiceGenesisでは、最高品質の一人ではなく、透明系、粗い系、低音系、息成分系などの代表を同時に維持する。

（図3: Quality Floorを満たすVoice Spaceの被覆を増やす。原文書参照）

### 6.4 研究指標：Quality-Constrained Diversity Coverage

```
maximize   Coverage_Q
subject to Feasible(v) = 1

Coverage_Q = 品質Floorを満たす占有niche数 / 事前定義niche数
```

最高品質0.96を0.97へ上げることだけでなく、「品質Floor以上の成立領域が18%から35%へ広がった」と報告できる。これは品質均質化を直接評価し、VoiceGenesisが作れる歌手の種類そのものを研究対象にする指標である。

## 7. 品質の均質化：評価が正しくても起きる崩壊

**報酬ハックとの違い**
報酬ハックは「実際は悪いのに代理評価だけ高い」。均質化は「実際に良いが、同じ無難な解ばかり残る」。前者は評価関数の破壊、後者は探索空間の崩壊である。

### 7.1 均質化の発生源

| 層 | 原因 | 対策 |
|---|---|---|
| データ | 特定音域・声質・発声の偏り | niche別サンプリング、希少形質の再重み付け、Provenance分離。 |
| モデル | 平均化Loss、過度な正則化、IdentityとPerformanceの混線 | Disentangle、Adapter、niche別Decoder/MoEを将来検討。 |
| 探索 | 最高点だけを繁殖、近縁交叉、mutation弱化 | Lineage保護、距離制約、Novelty Injection、動的mutation。 |
| 評価 | 「自然＝平均的」とするEvaluator bias | niche内比較、Hidden evaluator、人間の層化評価。 |

### 7.2 系統保存の三層Archive

| Archive | 保存対象 | 意味 |
|---|---|---|
| Elite Archive | 各nicheの現時点最高品質 | 現在強い系統。 |
| Diversity Archive | 品質Floorを通る希少・遠縁系統 | 将来の配合余地を守る。 |
| Failed-but-Interesting | 低品質だが特殊形質・新規挙動を持つ個体 | Repairや新モデルで再評価する種。 |

### 7.3 「ウイニングポスト」型の育種対応

| 育種ゲームの概念 | VoiceGenesis |
|---|---|
| 血統 | Lineage / Identity latent系統 |
| 能力値 | 品質指標・安定性・表現力 |
| 配合 | サブゲノムCrossover |
| 突然変異 | 限定Mutation / Novelty Injection |
| インブリード | latent・音響・共通祖先が過度に近い配合 |
| 系統確立 | niche内で高品質かつ再現可能なLineage |
| 海外血統導入 | Procedural / licensed synthetic由来の新規形質 |
| 種牡馬・繁殖牝馬入り | Elite / Diversity Archiveへの昇格 |

この比喩の核心は、現在最強の血統だけを残すと将来の探索余地が失われる点にある。低品質を無条件に保存するのではなく、品質Floorを満たした複数系統を維持し、現在弱いが特殊な形質は別Archiveへ保留する。

### 7.4 均質化の監視指標

- Voice latentの分散とpairwise distance。
- 音響特徴空間のentropyとクラスタ数。
- Lineage数、共通祖先集中度、近縁交叉率。
- MAP-ElitesのArchive occupancyとQuality-Diversity score。
- 世代ごとの最高品質とCoverageの同時推移。
- 品質上昇に対してHuman評価の声質多様性が低下していないか。

**自動回復条件**
Qualityが上がってもDiversityが急落した場合は、mutation強度増加、希少系統優遇、古いArchiveからの復活、未占有nicheへの新規latent注入を自動実行する。

## 8. 報酬ハッキングを研究資産へ変換する

進化探索はEvaluatorの癖を人間より速く見つける。高周波、特定スペクトル、極端な独自性などによって代理スコアだけを上げる個体が生まれ得る。これは事故であると同時に、Evaluatorの脆弱性を自動発見したデータでもある。報酬ハッキングはAI安全研究で独立した問題として扱われており[6][7]、VoiceGenesisでは検出・記録・再訓練までを標準ループへ入れる。

### 8.1 監査スタック

| 段階 | 見える評価 | 目的 |
|---|---|---|
| Training Evaluator | 進化側が最適化に使用 | 高速な選抜。 |
| Hidden Evaluator | 進化側へ非公開 | 特定評価器攻略の検出。 |
| Adversarial Detector | 高周波・clipping・スペクトル異常等 | 既知Hack patternのGate。 |
| Human Listening Audit | 層化抽出した少数候補 | 真の知覚品質との相関確認。 |
| Distribution Shift Test | 別歌詞・別音域・別seed | Probeだけへの過適合検出。 |

### 8.2 Hack Gap

```
HackGap(v) = Score_training(v) - Aggregate(Score_hidden(v), Score_human(v))

if HackGap(v) > θ_H:
    status = REWARD_HACK_SUSPECT
    archive = HackDB
    reproduction = blocked
```

Human scoreは全候補へ付ける必要はない。上位、境界、ランダム、過去Hack類似の各層から少数を抽出し、Training Evaluatorとの順位相関を世代別に監視する。人間の歌唱収録を避ける方針と、人間が生成音を評価する方針は別である。後者は比較的低コストで、Evaluator drift防止に重要となる。

### 8.3 Hack DBの資産価値

| 保存内容 | 用途 |
|---|---|
| 生成音声・Genome・親・Mutation | 再現と原因追跡。 |
| Training / Hidden / Human score | どの評価器だけを攻略したか判定。 |
| 音響特徴・スペクトル統計 | 既知Hack detectorの訓練。 |
| Evaluator version | 修正前後の脆弱性比較。 |
| 修正結果・再発有無 | Evaluator改善の有効性検証。 |

**共進化**
Generatorが良い声を探索する一方、その探索がEvaluatorの穴を炙り出す。EvaluatorはHack DBで更新され、次世代Generatorへより強い選択圧を返す。この「Generator－Evaluator共進化」が長期品質保証の中核となる。

## 9. データ戦略と権利来歴

人間の生歌を高コストな最終手段とする方針は維持する。ただし「人間ドナーを使わない」と「人間らしさを評価しない」は別である。最初はProceduralと再学習権が明確なSyntheticを中心に起動し、研究専用データはEvaluatorや構造解析へ限定する。最後に不足現象が明確になった場合だけ、短時間のTargeted Human Calibrationを行う。

### 9.1 データTier

| Tier | 内容 | Production学習での扱い |
|---|---|---|
| Tier 0: Procedural | 声帯・声道・formant・F0・noiseを自前生成 | 原則利用可能。生成手順・seedを保存。 |
| Tier B: Licensed Synthetic | 再学習・派生モデル・商用利用が明示許諾された合成音声 | 契約範囲内で利用。出力の再学習権を個別確認。 |
| R&D Reference | 研究データ、再配布不可／非商用など | Evaluator・特徴解析・比較に限定し、Production重みと分離。 |
| Tier A: Targeted Human | 不足現象だけを契約収録 | 最後の校正。AI学習・派生・商用を契約明記。 |

### 9.2 公開する研究データ

論文再現性のために必ずしも生WAVを全公開する必要はない。権利に応じてRaw dataとDataset lineageを分離し、次の情報を公開可能な形式で保存する。

- データ生成手順、前処理コード、設定、乱数seed。
- 件数、時間、音域、音素、niche分布、学習・検証・test分割。
- ファイルhash、Provenance class、利用制限、削除・失効履歴。
- checkpoint、Evaluator version、Lineage graph、世代別Coverage。
- Elite、Failure、Reward-Hackの集計・特徴量・代表サンプル。

**権利上の原則**
「生成物を商用利用できる」ことと「生成物を別AIの学習へ使える」ことは別許諾である。再学習権が明示されない外部生成物は、Synthetic Donorへ自動昇格させない。

## 10. GPUで何をスケールさせるか

GPUの価値は一回の生成を速くすることだけではなく、同じBackboneで多数のVoice Genomeを推論し、評価・選抜・変異を何世代も回す探索能力にある。VRAMは一回の計算規模、GPU速度・台数・稼働時間は総試行回数を決める。

### 10.1 4090 24GBの適切な役割

| 用途 | 適性 | 備考 |
|---|---|---|
| 短尺SVS推論・Probe大量生成 | 高い | 多数候補の高速進化ループ。 |
| Identity latent / Adapter学習 | 高い | PoCの中心。 |
| 小～中規模Backbone fine-tune | 条件付きで高い | mixed precision、gradient accumulation等を利用。 |
| Evaluator訓練・特徴抽出 | 高い | 失敗・Hack・Human labelを反映。 |
| 数分フル曲Foundation Modelのゼロから事前学習 | 低い | モデル・系列が巨大。初期目標外。 |

### 10.2 「100万試行」と「巨大な1試行」は別

```
小さな1試行 × 100万回
→ 24GBに収まるなら、時間方向・複数Podで実行可能

巨大な1試行 = 40GB以上必要
→ 1回目から24GB GPUへ載らない
```

VoiceGenesis初期PoCは、3～10秒のProbeを多数流す「小～中規模モデル × 大量探索」が適している。全曲の構造はGlobal Contextとして別に持ち、音響生成を短尺へ分割することで、局所品質と長期一貫性を段階的に統合する。

### 10.3 Checkpoint設計

| Checkpoint | 保存対象 | 目的 |
|---|---|---|
| Training checkpoint | Backbone／Evaluator重み、optimizer、step | 停止・再開、比較、ロールバック。 |
| Evolution checkpoint | 世代、人口、Archive、Lineage、mutation policy | 進化探索の再開と再現。 |
| Canonical Singer snapshot | Frozen Genome、Backbone hash、Probe結果 | 人工歌手Identityの固定。 |
| Audit snapshot | Hidden evaluator、Human sample、Hack DB version | 評価の後付け変更を防ぐ。 |

### 10.4 スモールスケールから拡張する理由

- 評価器が間違った状態で大量計算すると、間違った方向へ高速化するだけだから。
- 小規模ならどの変異が効いたかを人間が確認しやすいから。
- 短尺で成立しない微細発声は、フル曲へ伸ばしても改善しないから。
- 24GBの限界を実測した後に48GB／80GBへ上げる方が費用対効果を判断できるから。
- Coverage、Hack Gap、Lineage diversityが正しく動くことを先に検証できるから。

## 11. PoC v0.1 実験計画

**PoCの目的**
Suno級の完成曲生成ではない。共有Backbone上で、追跡可能なVoice Genomeが複数nicheに成立し、品質Floor・多様性・Hack監査が一つの閉ループとして動くことを証明する。

### 11.1 最小構成

| コンポーネント | v0.1仕様 |
|---|---|
| Backbone | 既存または小型の歌声合成Backbone。3～10秒Probeを生成可能。 |
| Genome | Identity / Phonation / Performanceの構造化vector。 |
| Branch | Identity、Phonation、Performance、Repair、Noveltyの5種。 |
| Probe Set | 音素、音域、ロングトーン、跳躍、声区、vibrato、複数seed。 |
| Evaluator | Pitch、明瞭度、Artifact、Identity一貫性、知覚自然さの代理。 |
| Audit | 別モデルHidden evaluator + 少数Human listening。 |
| Archive | Pareto Front、MAP-Elites grid、Lineage、Hack DB。 |

### 11.2 フェーズ

| Phase | 実施内容 | 判定 |
|---|---|---|
| 0. Freeze | Genome schema、Probe Set、評価指標、閾値決定手順、rights classを固定。 | 比較可能な実験単位を作る。 |
| 1. Baseline | ランダム／手設計Genomeを同一Probeで生成し、評価分布を把握。 | Quality Floorとbehavior descriptorを校正。 |
| 2. Small Evolution | 小人口・少世代で限定Mutationを実行。 | Graph、Lineage、原因追跡を確認。 |
| 3. QD Archive | nicheを定義し、各セルにEliteを保存。 | 品質均質化を抑制できるか検証。 |
| 4. Hack Audit | Hidden／Humanとの乖離個体をHack DBへ保存。 | Evaluator攻略を検出し資産化。 |
| 5. Slow Learning | Elite・Failure・HackでAdapter／Evaluatorを更新。 | Coverageと監査相関が改善するか確認。 |
| 6. Scale | 候補数、世代、Probe、GPU時間を段階拡大。 | 小規模で成立した仮説だけをスケール。 |

### 11.3 初期実験値の考え方

人口、世代、Probe数は固定の正解ではない。最初にGPU throughputと評価時間を測り、1世代を人間が追跡できる小ささから始める。例として「数十～百程度の個体」「数十のProbe」「十数世代」から始め、ログと監査が正しいことを確認後に増やす。これらは仕様値ではなく、計測前の初期レンジである。

### 11.4 成功条件

- 最高品質だけでなく、Quality-Constrained Coverageが世代とともに増える。
- 複数Lineageが残り、共通祖先集中度とpairwise similarityが許容範囲にある。
- Training EvaluatorとHidden／Humanの順位相関が維持または改善する。
- Reward Hackの再発率がEvaluator更新後に低下する。
- Frozen Genomeが別Probe・別曲でも同一Voice Identityを維持する。
- seed、checkpoint、Genomeから結果を再現できる。

### 11.5 反証・中止条件

- 代理スコアだけ上がり、Hidden／Human品質が停滞または低下する。
- Quality Floorを上げるとnicheがほぼ一つに崩壊する。
- IdentityとPerformanceを分離できず、曲ごとに歌手が別人化する。
- Synthetic／Proceduralだけでは微細発声の壁を越えず、同じ欠損が継続する。
- Lineage追跡不能、権利来歴不明、再現不能な候補が混入する。

最後の条件群は「研究失敗」ではなく、設計仮説の修正点を特定するための停止規則である。特にZero-Human Bootstrapが成立しない場合は、欠損現象を特定してTargeted Human Calibrationへ移行する。

## 12. 転生モード・創生モードとの統合

（図4: VoiceGenesisは転生と創生の共通歌手・歌唱層になる。原文書参照）

VoiceGenesisは独立した声研究で終わらず、転生モードと創生モードの共通Performance層になる。転生モードではFrozen ScoreとSource-Free境界を通った新アレンジへ人工歌手を割り当てる。創生モードでは複数作曲Branchの各候補へ異なるVoice Lineageを組み合わせ、曲と歌手の適合もPareto評価できる。

### 12.1 統合フロー

```
転生モード
Source → 完全採譜 → Frozen Score → Source-Free Arrangement
       → VoiceGenesis Singer → 新演奏 → Gate / Repair → WAV

創生モード
Theme → Composition Branches → Pareto / Merge → Arrangement
      → VoiceGenesis Singer → Performance → Mix / Master → WAV
```

### 12.2 共通評価資産

| 資産 | 転生 | 創生 | VoiceGenesis |
|---|---|---|---|
| Branch / Revision | Repair案・Arrangement案 | 作曲案・Merge案 | Voice系統・変異案 |
| Gate | Score fidelity / Source-Free | 構造・制約・品質 | Quality Floor / Identity leakage |
| Pareto | 忠実度・自然さ・新規性 | 曲構造・感情・独自性 | 自然さ・独自性・表現・安定性 |
| Hack DB | Gate迂回・評価器攻略 | 評価だけ高い無内容曲 | 知覚不良だが高得点の声 |
| Archive | 成功Repair | 高品質Arrangement群 | 人工歌手生態系 |

### 12.3 最終像

**Mini-Sunoを超える点**
創生による0→1、転生による音→構造→新演奏、VoiceGenesisによる0→1の人工歌手、共通Evaluator／Repairを一体化する。単なる小型Sunoではなく、生成・再構築・歌手育成・自己監査を持つ小型音楽Foundation Systemとなる。

## 13. 研究仮説と想定される新規性

### 13.1 検証可能な仮説

| ID | 仮説 |
|---|---|
| H1 | 品質Floorを先に適用し、その上でQuality-Diversityを行うと、単一スコア最適化より高品質Lineage数を維持できる。 |
| H2 | latent・音響・系譜を組み合わせた近縁制約は、品質を大きく落とさず均質化を遅らせる。 |
| H3 | 意味付きBranch変異は、全要素ランダム変異より少ない試行でRepair原因を特定できる。 |
| H4 | Reward-Hack ArchiveでEvaluatorを再訓練すると、既知Hackの再発率とTraining-Hidden gapが低下する。 |
| H5 | 最高品質よりQuality-Constrained Coverageの方が、人工歌手生成能力の拡張をよく表す。 |
| H6 | 共有Backbone + evolvable latent/Adapterは、個体別完全checkpointより計算効率と系譜管理に優れる。 |
| H7 | 短尺高品質生成 + Global Contextの階層化は、初期PoCでフル曲End-to-Endより効率的である。 |
| H8 | Procedural／Licensed Synthetic起点で不足成分を特定すれば、人間収録をTargeted Calibrationへ縮小できる。 |

### 13.2 本研究で提案する統合概念

- Dual-Timescale Voice Evolution：高速Genome進化と遅い重み学習の分離。
- Quality-Constrained Diversity Coverage：品質下限を満たすVoice Space被覆を主要指標化。
- Lineage-Aware Graph Engineering：変異・配合・Repair・権利来歴を不変グラフで追跡。
- Reward-Hack Assetization：評価器攻略を失敗ではなく敵対的訓練資産として保存。
- Identity Freeze / Performance Revision：歌手本人性と曲ごとの歌い方を分離。
- Artificial Singer Ecosystem：単一最適解ではなく複数の高品質系統を研究成果とする。

### 13.3 不確実性

進化計算やQuality-Diversity自体は既存研究に基づくが、人工歌手Identityの品質・権利・知覚評価を同時に扱う本構成は未検証である。特に、Voice latent上の距離が人間の知覚差と一致するか、Synthetic起点で自然な微細発声が成立するか、Hidden evaluatorを長期に非攻略状態で保てるかは実験で確認する必要がある。

## 14. 次に実装する最小単位

次の作業は巨大モデルの訓練ではなく、VoiceGenesis Evolution Graph v0.1の「実験契約」を実装することである。優先順位は次の通り。

1. VoiceGenome、Lineage、EvaluationRecord、HackRecordのschemaを固定する。
2. 共有BackboneとVoice条件の差し込み点を一つ選ぶ。
3. 標準Probe Setを作り、Score／音素／音域／表情をSHA固定する。
4. Quality GateとTraining／Hidden evaluatorを分離する。
5. 5種類のBranch operatorを実装し、各変異をRevisionとして保存する。
6. MAP-Elites型ArchiveとLineage距離を実装する。
7. 小人口で一世代を回し、ログ・再現性・監査を確認する。
8. Human listeningの層化サンプルを作り、Evaluator相関を測る。
9. 成立後にのみ世代数・個体数・GPU時間を増やす。

**最初の完成判定**
「良い声が一つ出た」ではなく、同じcheckpointとProbe Setの下で複数nicheのEliteが残り、各Lineageの親・変異・評価・権利来歴を再現でき、Hack監査が一周すること。

## 付録A. 最小データschema

### A.1 VoiceGenome

```json
{
  "voice_id": "VG-000421",
  "generation": 12,
  "parent_ids": ["VG-000188", "VG-000377"],
  "backbone_checkpoint": "sha256:...",
  "identity_latent_ref": "blob:...",
  "identity": {"formant": {}, "spectral_envelope": {}},
  "phonation": {"breathiness": 0.31, "roughness": 0.12},
  "performance_prior": {"vibrato_rate": 5.4, "attack": 0.46},
  "mutation_ops": ["PHONATION_BREATH_PLUS"],
  "seed": 481105,
  "rights_class": "PROCEDURAL",
  "status": "CANDIDATE"
}
```

### A.2 EvaluationRecord

```json
{
  "voice_id": "VG-000421",
  "probe_set_sha": "sha256:...",
  "evaluator_version": "EV-0007",
  "gate": {"artifact": "PASS", "provenance": "PASS"},
  "training_scores": {"naturalness": 0.91, "pitch": 0.96},
  "hidden_scores": {"naturalness": 0.83},
  "human_sample": null,
  "behavior_descriptor": [0.22, -0.61, 0.48, 0.11],
  "hack_gap": 0.08,
  "archive_cell": "dark-clean-mid",
  "decision": "ELITE_REPLACE"
}
```

### A.3 HackRecord

```json
{
  "hack_id": "RH-000093",
  "voice_id": "VG-000421",
  "target_evaluator": "EV-0006",
  "symptom": "high_frequency_exploit",
  "training_score": 0.98,
  "hidden_score": 0.41,
  "human_score": 0.28,
  "feature_snapshot": "blob:...",
  "patch_evaluator": "EV-0007",
  "retest": "BLOCKED"
}
```

## 付録B. 実験ログとして必ず残すもの

| カテゴリ | 必須項目 |
|---|---|
| 再現性 | commit、container image、dependency lock、GPU type、precision、seed、config。 |
| 学習 | step、loss、optimizer、learning rate、checkpoint hash、再開元。 |
| 進化 | population、generation、parent、mutation、crossover、lineage、archive decision。 |
| 評価 | Probe Set、Evaluator version、各score、閾値、Human sample、順位相関。 |
| 権利 | source class、license／contract ref、再学習可否、公開可否、削除期限。 |
| 障害 | OOM、破損checkpoint、失敗音声、Hack pattern、回復操作。 |

## 付録C. 関連する既存概念・参考文献

本書の統合設計は独自の研究仮説だが、構成要素は歌声合成、進化計算、Quality-Diversity、AI安全の既存研究に接続する。

[1] Liu, J., Li, C., Ren, Y., Chen, F., & Zhao, Z. (2022). DiffSinger: Singing Voice Synthesis via Shallow Diffusion Mechanism. AAAI 36(10). arXiv:2105.02446.

[2] Deb, K., Pratap, A., Agarwal, S., & Meyarivan, T. (2002). A Fast and Elitist Multiobjective Genetic Algorithm: NSGA-II. IEEE Transactions on Evolutionary Computation, 6(2), 182–197. DOI: 10.1109/4235.996017.

[3] Mouret, J.-B., & Clune, J. (2015). Illuminating Search Spaces by Mapping Elites. arXiv:1504.04909.

[4] Pugh, J. K., Soros, L. B., & Stanley, K. O. (2016). Quality Diversity: A New Frontier for Evolutionary Computation. Frontiers in Robotics and AI, 3:40. DOI: 10.3389/frobt.2016.00040.

[5] Lehman, J., & Stanley, K. O. (2011). Abandoning Objectives: Evolution Through the Search for Novelty Alone. Evolutionary Computation, 19(2), 189–223. DOI: 10.1162/EVCO_a_00025.

[6] Amodei, D., Olah, C., Steinhardt, J., Christiano, P., Schulman, J., & Mané, D. (2016). Concrete Problems in AI Safety. arXiv:1606.06565.

[7] Skalse, J., Howe, N. H. R., Krasheninnikov, D., & Krueger, D. (2022). Defining and Characterizing Reward Hacking. Advances in Neural Information Processing Systems, 35, 9460–9471.

[8] Jaderberg, M. et al. (2017). Population Based Training of Neural Networks. arXiv:1711.09846.

## 最終定義

**VoiceGenesis Evolution Theory v0.1**
共有歌唱Backboneを勾配学習で育て、その上で追跡可能なVoice Genomeを進化探索する。候補は品質Floor、Hidden監査、Pareto、Quality-Diversityを通して系統保存され、失敗と報酬ハックはEvaluatorの敵対的訓練資産へ変換される。成果は単一の最高品質歌手ではなく、品質制約を満たす多様な人工歌手生態系と、その生成・評価・系譜を再現可能にする研究基盤である。

---

## 収載時レビュー注記（2026-08-16 壁打ち）

**本リポ実測と既に整合する点**

- **Quality Floor 先行** = 本リポの耳ゲート階層（声→言語→精度の順で先に通す設計、`FOUNDRY_ROADMAP.md`）と同型。
- **報酬ハック資産化** = F1b の帯域指標逆転で実証済みの教訓と一致（代理指標だけを最適化すると知覚品質と逆転する事例が既に観測されている）。
- **Identity Freeze** = `voice_spec.py`（`foundry-voice/0.1`）の identity pin 運用として既に実働している。
- **二重時間尺度** = S1 アーキテクチャそのもの（Backbone = S1 acoustic モデル学習、Identity latent = spk_embed による個体差表現）と対応する。

**最大リスク = 評価器ブートストラップ問題**

知覚代理の校正は本リポで全敗の実績がある: F1b 指標/耳逆転、M3d 校正不成立（`docs/m3d_calibration_record.md`）、WI3 proxy 空集合（`docs/wi3_human_calibration.md`）。自然さゲートは当面 Human Audit（User の耳）が律速であり、本書が想定する高速ループのスループット見込みは下方修正が必要。

**第二リスク = Behavior Descriptor の弁別妥当性**

WI2 弁別判定ハーネス（`docs/wi2_discrimination_harness.md`）では、弁別成立は 5 軸中 bpm のみだった。niche を定義する Behavior Descriptor の採用には、「別 niche 2 個体の耳弁別テスト」を通過条件として課すべきである。

**安い接続点**

Genome schema（付録A.1 `VoiceGenome`）は `foundry-voice/0.1` の lineage/provenance 拡張として、実装解禁前でも本 S2 記録から先行して参照・流用してよい。
