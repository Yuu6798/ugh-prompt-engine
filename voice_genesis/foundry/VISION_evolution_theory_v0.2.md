<!-- 収載ヘッダ（リポジトリ管理者注記・原文は下記より）-->

> **状態: 採用済み Research Draft（User 提供・合意確認 2026-08-19）。**
> 本文は User 提供の docx（VoiceGenesis_Evolution_Theory_v0.2_ja）を逐語収載した
> もの（表・コードブロックは docx の構造から機械復元・欠落 0 行を機械検証済み）。
> Fable 整合レビュー（2026-08-19）の判定 = **合意。原理レベルの衝突なし**。
> うちの実測が本書の主張を裏付ける先行例: D1–D4 決定論階層 §3.1（SIMD 1 LSB
> 分岐 = D4 不成立の実測・X86_V3/ffmpeg pin = ExecutionProfile の先取り）、
> Hidden 評価器との乖離 §9.1（run 4 の LRA×耳ギャップ = 実測第 1 号）、
> 因果の分離 §6.1（単一介入原則の一般化）。
>
> **優先規則（v0.1 収載時と同じ）**: 本書は Research Draft であり実装契約を
> 持たない。数値・schema は実装契約側（`evolution/DESIGN_VG_E0.md` /
> `evolution/DESIGN_VG_L0.md` / S 系列 Design Memo）が正。
>
> **採用注記 4 点（Fable 整合レビューより。実装時に必ず参照）**:
> 1. **Performance の語彙衝突**: VG-E0 の Performance Revision = seed 変更、
>    本書の Performance = Adapter の学習済み技能。採用側では
>    「performance rendering（seed 軸）」と「performance skill（技能軸）」を
>    区別する（VG-E0 版番改訂時に明記）
> 2. **実装位相差**: 現行実装に Performance Adapter は存在しない
>    （spk_embed のみ）。LearningTransition の重み版はエントリ条件 =
>    制御層学習（VG-L0）の天井実測後。学習回路の当面の正本 = DESIGN_VG_L0
> 3. **Identity Drift Gate の校正リスク**: 本人性の機械測定はうちの帳簿上
>    未成功（WI3 proxy v0 = 空集合・M3d = 校正標本の構造的不足で closeout）。
>    校正が観測律速で不成立になる失敗モードを §11 の失敗表に追加すべき。
>    当面の Drift Gate の実体は User の耳（ブラインド様式）
> 4. **LearningGain のスカラー禁止**: §9.1 の LearningGain は**軸別ベクトル**
>    として読む（M3 由来「総合 1 点スコア恒久禁止」= VG-E0 schema 強制の継承）
>
> V36 プリミティブ（Branch/Revision/Lease/Gate/CAS）は本リポ外の将来統合先。
> うちの対応物 = git 内容アドレス + 台帳 O_EXCL + PR レビュー + canonical は
> git 履歴。対応表が必要になった時点で別途起草する。

---

# VoiceGenesis Evolution Theory v0.2


Deterministic Learning Transition

> **設計テーマ**
>
> 進化・学習・推論を混ぜずに接続する。推論中の状態不変性を守りながら、個体固有の歌唱学習を「不変 Revision 間の再現可能な遷移」として Evolution Graph に組み込む。

| 項目 | 内容 |
| --- | --- |
| 文書版 | v0.2 |
| 状態 | Concept Design / Research Draft |
| 継承元 | VoiceGenesis Evolution Theory v0.1 |
| 主要追加 | SingerRevision / LearningTransition / Identity Drift Gate / Determinism Contract |
| 統合先 | VoiceGenesis + Evolution Graph Engine + V36 |
| 作成日 | 2026-08-19 |

「歌手が学ぶ」を、隠れた可変状態ではなく、監査可能な Revision 遷移として実装する。

# 要約：v0.2 の結論

> **研究の核心**
>
> v0.1 の「共有 Backbone は勾配学習、Voice Genome は進化探索」という分業を維持したまま、人工歌手個体が経験から歌唱技能を獲得できる層を追加する。ただし推論中のオンライン学習は禁止し、すべての学習結果を新しい SingerRevision として確定する。

| 設計判断 | v0.2 の意味 |
| --- | --- |
| 1. 推論は不変 | 同一 SingerRevision・入力・実行プロファイルからの生成中に、Genome / Adapter / Backbone を変更しない。 |
| 2. 学習は Transition | 学習は parent Revision を上書きせず、dataset・recipe・seed・環境を固定して child Revision を生成する。 |
| 3. Identity は Freeze | 個体学習で変えてよいのは主に Performance Adapter。Identity drift が閾値を越えた場合は同一歌手として commit しない。 |
| 4. 進化と学習を別 Edge にする | Mutation / Crossover と LearningTransition を別種のグラフ遷移として保存し、原因を混同しない。 |
| 5. 再現性を Gate 化 | 学習前後の hash、ExecutionProfile、Replay 結果が揃わない Revision は Canonical Singer に昇格できない。 |
| 6. v0.1 を壊さない | 共有 Backbone、Quality Floor、QD、Hidden evaluator、Hack DB、Lineage は継承する。 |

## v0.1 から継承する前提

共有歌唱 Backbone が歌唱能力を担い、Voice Genome / Identity latent / 小型 Adapter が個体差を表現する。

高速探索では原則として重みを更新せず、選抜後に Backbone / Evaluator を遅いループで再学習する。

採用 Singer は Identity Genome を Freeze し、Identity を変える場合は新しい子系統へ Branch する。

成果指標は単一最高点ではなく、Quality Floor を満たす Voice Space の多様な成立領域と再現可能な Lineage である。

# 1. v0.1 → v0.2 変更差分

v0.2 は全面改築ではなく、v0.1 の二重時間尺度を「状態不変の推論」「個体学習遷移」「進化探索」「生態系レベル再学習」に分解して、非決定的な隠れ状態を排除する拡張である。

| 対象 | v0.1 | v0.2 |
| --- | --- | --- |
| 個体表現 | Voice Genome + latent / 必要なら Adapter | Voice Genome + immutable SingerRevision + Performance Adapter |
| 個体学習 | 明示的には未実装。Adapter は局所勾配更新候補 | LearningTransition を第一級 Graph Edge として追加 |
| 推論 | checkpoint + Genome から Probe 生成 | SingerRevision を完全 Freeze。推論時の state mutation 禁止 |
| Identity | Singer 確定後 Freeze | Freeze 継承 + Identity Drift Gate を commit 条件化 |
| 再現性 | seed / checkpoint / Genome から再現 | Dataset hash / Recipe hash / ExecutionProfile / ReplayRecord まで含める |
| 時間尺度 | 高速進化 + 遅い学習 | Inference / LearningTransition / Evolution / Ecosystem Learning の役割分離 |
| Canonical 化 | 品質・監査・系譜 | 品質 + 監査 + 系譜 + Replay PASS + no-hidden-state |

> **非目標**
>
> v0.2 では、歌唱中に自律的に重みを書き換えるオンライン学習、個体ごとの完全 Backbone、長期会話メモリのような不可視 mutable state、学習則そのものの進化は初期 PoC に含めない。

## 1.1 新しい PoR

PoR：人工歌手に「経験による上達」を与えても、同一条件の再現性・Identity・原因追跡を壊さず、進化系譜の中でその成長を監査可能にできるか。

## 1.2 設計原則

1. No mutable inference：推論は読み取り専用。

2. No in-place training：学習は親 Revision を上書きしない。

3. Every change is an edge：Mutation / Learning / Repair / Checkpoint Update はすべて遷移種別を持つ。

4. Identity before skill：声本人性と歌唱技能を別々に評価する。

5. Replay before canonical：再実行可能性を確認してから正典化する。

# 2. 全体アーキテクチャ

*図 1  v0.2：推論・個体学習・進化・生態系学習を分離した Revision Graph*

v0.2 の中心は SingerRevision である。SingerRevision は「ある瞬間の歌手を完全に再実行可能にする不変スナップショット」であり、Voice Genome、Backbone checkpoint、個体 Adapter、実行契約を参照する。生成はこの Revision を読み取るだけで、学習は必ず次の Revision を生成する。

## 2.1 構成要素

| 要素 | 責務 | 可変性 |
| --- | --- | --- |
| Shared Backbone | 歌詞・MIDI・Duration・Expression・Voice 条件から歌唱表現を生成 | checkpoint 単位で更新。推論中は固定 |
| Voice Genome | Identity / Anatomy / Phonation / 系譜・来歴 | 進化 Branch で変更。採用後 Identity は Freeze |
| Performance Adapter | 発声技能・register transition・attack・vibrato・phrasing など個体固有技能 | LearningTransition でのみ更新 |
| SingerRevision | Genome / Backbone / Adapter / policy / hash の不変参照 | immutable |
| LearningTransition Controller | データ・recipe・seed・ExecutionProfile を固定し child Revision を作る | commit 前のみ計算 |
| Evolution Graph Engine | Mutation / Crossover / Repair / Novelty / lineage / archive | Revision 追加のみ |
| Evaluator Stack | Quality / Hidden / Identity Drift / Hack / Replay Gate | versioned |
| ExecutionProfile | GPU / precision / deterministic flags / dependencies を固定 | versioned |

# 3. Determinism Contract

> **最重要契約**
>
> 同一 Revision を推論しただけでは、Singer の状態は 1 bit も進まない。状態を変える操作はすべて Graph Transition であり、入力・変換規則・出力 Revision が追跡可能でなければならない。

## 3.1 決定性を二段階に分ける

| レベル | 保証 | v0.2 方針 |
| --- | --- | --- |
| D1：状態決定性 | どの状態からどの状態へ遷移したかが一意に追跡できる | 必須。Revision / hash / dataset / recipe / seed / parent を保存 |
| D2：実行再現性 | 同じ ExecutionProfile で同じ計算結果を再現できる | PoC の必須 Gate。deterministic kernel / seed / data order を固定 |
| D3：byte-identical | 別 run の重み・WAV が byte 単位で完全一致 | 可能な backend では強制。難しい backend は tolerance と hash-of-state を併用 |
| D4：cross-hardware | 異なる GPU / CUDA でも完全一致 | 初期 PoC の必須条件ではない。ExecutionProfile ごとに再現性を定義 |

## 3.2 禁止される可変状態

推論 API 呼び出しによる Adapter / EMA / BatchNorm 統計の暗黙更新。

ランダム seed 未記録の sampling、augmentation、data shuffle。

同一 Singer ID の checkpoint を in-place overwrite する更新。

実行ノード固有の未記録 cache / memory が次の生成へ影響すること。

Evaluator version を後から差し替えて過去 decision を再解釈すること。

## 3.3 Commit 原則

```
parent_revision = VG-042:r17
transition = LEARN_PERFORMANCE
dataset_sha = sha256:...
recipe_sha  = sha256:...
seed        = 481105
execution_profile = EP-0003

train(parent_revision) -> candidate_revision
replay(candidate_revision) -> PASS
identity_drift(candidate_revision) <= theta_drift
quality_gate(candidate_revision)   == PASS

COMMIT -> VG-042:r18
```

# 4. LearningTransition：個体学習を Revision 化する

*図 2  学習は parent を変形せず、Gate を通った child Revision を生成する*

## 4.1 学習可能領域

| 領域 | 初期 v0.2 | 理由 |
| --- | --- | --- |
| Identity latent / Anatomy | 原則不可 | 本人性の中核。変更は Evolution Branch として新 Voice ID へ。 |
| Phonation core | 限定・原則 Evolution 側 | breath / closure / roughness は本人性と強く結合しやすい。 |
| Performance Adapter | 学習可 | 歌唱技能として解釈しやすく、Identity Freeze と両立しやすい。 |
| Register transition | 学習可 | 音域別 Probe で上達・破綻を測定できる。 |
| Attack / release / vibrato / phrasing | 学習可 | 曲間で蓄積する歌唱 skill として扱える。 |
| Shared Backbone | 個体 Transition では不可 | 生態系共通 checkpoint の更新は別の Slow Ecosystem Learning で扱う。 |

## 4.2 学習遷移の入力契約

親 SingerRevision ID と全参照 hash。

Experience Dataset manifest：音声・MIDI・歌詞・ラベル・rights class・split・生成元。

Training Recipe：loss、optimizer、learning rate、steps、augmentation、freeze mask。

Seed bundle：model / data loader / augmentation / sampler。

ExecutionProfile：GPU type、driver、CUDA/cuDNN、precision、deterministic flags、container image。

Expected gates：Quality、Identity drift、Artifact、Rights、Replay。

# 5. Identity Freeze と Learning Drift

v0.2 では「同じ歌手が上達する」と「別の歌手へ変異する」を明確に分ける。LearningTransition は Voice ID を維持できるが、Identity drift が許容範囲を越えた場合、その結果を同一歌手として commit してはならない。

## 5.1 Identity Drift Gate

```
Drift(v_parent, v_child) =
    w_e * d_identity_embedding
  + w_a * d_acoustic_identity
  + w_p * d_probe_consistency

if Drift <= theta_same_singer:
    commit = SAME_VOICE_NEW_REVISION
elif quality_pass and novelty_valid:
    commit = FORK_CHILD_LINEAGE
else:
    commit = REJECT
```

重みと閾値は v0.2 で固定値を仮定しない。v0.1 と同様、Probe Set と validation 分布から校正し、test 前に Freeze する。

## 5.2 同一 Singer として許容する変化

| 変化 | 扱い | 例 |
| --- | --- | --- |
| 歌唱技能の改善 | 同一 Voice ID / 新 Revision | 高音 transition が滑らかになる、ブレス位置が安定する |
| 表現 prior の更新 | 同一 Voice ID / 新 Revision | attack、vibrato、phrase-end の適応 |
| 声質コアの大幅変化 | 新 Child Voice Lineage | formant 構造や identity embedding が閾値超過 |
| 品質悪化 | Reject / Failed Archive | 明瞭度や naturalness が Floor 未満 |
| Evaluator 攻略 | Hack DB | Training score のみ上昇し Hidden/Human と乖離 |

> **重要**
>
> 「成長」は Voice ID の自由な変形ではない。Identity を不変核として保持し、その周囲の技能状態だけを Revision 化する。

# 6. 学習・推論・進化・生態系更新の役割分担

| ループ | 状態更新 | 対象 | 頻度 | 主な出力 |
| --- | --- | --- | --- | --- |
| Immutable Inference | なし | 固定 SingerRevision | 各 Probe / 各曲 | WAV / Mel / 評価入力 |
| LearningTransition | あり・child Revision を生成 | Performance Adapter / 許可された skill params | 経験データ蓄積時 | SingerRevision r(n+1) |
| Evolution | あり・new Voice Genome / Lineage | Identity / Phonation / mutation policy | 各世代 | 新 Voice ID / Branch |
| Slow Ecosystem Learning | あり・checkpoint 更新 | Shared Backbone / Evaluator | 一定世代・一定データごと | Backbone/Evaluator version |

## 6.1 因果の分離

品質が上がったとき「Genome の変異が効いたのか」「個体学習が効いたのか」「Backbone 更新が効いたのか」を一つの Revision 差分で混ぜない。比較実験では一度に一種類の Edge だけを有効化し、他レイヤーの hash を固定する。

```
A/B contract
A = parent SingerRevision + no learning
B = same parent + LEARN_PERFORMANCE only

assert genome_hash(A)   == genome_hash(B)
assert backbone_hash(A) == backbone_hash(B)
assert probe_set_sha(A) == probe_set_sha(B)

Delta = Eval(B) - Eval(A)
# 変化の原因を LearningTransition に帰属できる
```

## 6.2 将来拡張：Plasticity Genome

学習率や freeze mask、Adapter 容量など「学び方」自体を進化対象にする Plasticity Genome は有力だが、v0.2 PoC では研究仮説に留める。先に固定された学習遷移が再現可能に動くことを証明する。

# 7. Evolution Graph Engine v0.2

## 7.1 Edge Type を明示する

| Edge | Voice ID | Identity変更 | 重み更新 | 用途 |
| --- | --- | --- | --- | --- |
| MUTATE_IDENTITY | 新規 | あり | 原則なし | 新しい声質系統 |
| CROSSOVER | 新規 | あり得る | 原則なし | 意味単位の配合 |
| LEARN_PERFORMANCE | 維持 | 禁止 | Adapter のみ | 歌唱技能の獲得 |
| REPAIR_PERFORMANCE | 維持 | 禁止 | Adapter / control 限定 | 失敗成分の局所修復 |
| FORK_DRIFTED_LEARNING | 新規 | あり | 学習済み Adapter を継承可 | 学習結果が別 Identity へ逸脱した場合 |
| CHECKPOINT_REBASE | 維持候補 | 原則なし | Backbone/Evaluator 更新 | 新 checkpoint 上で再評価・再校正 |
| CALIBRATION | 維持候補 | 禁止 | 限定 Adapter | Targeted Human Calibration |

## 7.2 Revision immutability

親 Revision を直接更新しない。

すべての Edge は parent_ids、operator、config、seed、checkpoint、Evaluator version を保存する。

同じ Voice ID でも Revision は別オブジェクトとして保存し、Canonical pointer だけを CAS で更新する。

Backbone / Probe Set / Evaluator が変わった場合、過去 Revision は失効せず「revalidation required」とする。

## 7.3 Canonical Singer pointer

```
voice_id = VG-000421
canonical_revision = r18

r17 -> immutable historical state
r18 -> current canonical state
r19 -> candidate only

CAS(canonical_revision, expected=r18, new=r19)
# Gate PASS + Replay PASS のときだけ更新
```

# 8. Learning Dataset / Provenance Contract

v0.2 では個体学習を追加するため、v0.1 の権利来歴ルールを学習遷移単位まで引き下げる。Dataset は「使った音源の集合」ではなく、再実行可能な manifest として固定する。

| 項目 | 必須記録 |
| --- | --- |
| dataset_id | 論理 ID と content hash |
| source class | PROCEDURAL / LICENSED_SYNTHETIC / R&D_REFERENCE / TARGETED_HUMAN |
| training right | 再学習・派生・商用・再配布の可否 |
| content | 音声 / MIDI / lyrics / alignment / labels の hash |
| split | train / validation / identity-audit / replay-test |
| generator provenance | 生成器 version / seed / recipe / parent asset |
| retention | 削除期限・失効・契約変更の追跡 |
| taint policy | Production weight へ入れてよいか、Evaluator 限定か |

> **境界**
>
> R&D Reference は Evaluator・特徴解析・比較に限定し、Production Adapter / Backbone の学習へ自動流入させない。外部生成物は「商用利用可」と「再学習可」を別許諾として扱う。

## 8.1 Experience Dataset の最小単位

初期 PoC はフル曲を学習単位にせず、3–10 秒の統制 Probe / training fragment を中心にする。音域、音素、強弱、register transition、vibrato、phrase-end など「何を学習したか」が測れる単位を優先する。

# 9. Evaluator Stack v0.2

| Gate / Evaluator | 役割 | Commit への影響 |
| --- | --- | --- |
| Quality Floor | Naturalness / Intelligibility / Pitch / Artifact | FAIL → Reject |
| Identity Consistency | parent と child の本人性維持 | drift 超過 → Reject または Child Lineage |
| Training Evaluator | 学習最適化に使用 | 改善判断に利用するが単独では commit 不可 |
| Hidden Evaluator | Training 評価器攻略の検出 | 乖離大 → Hack DB / block |
| Distribution Shift | 別歌詞・別音域・別 seed | Probe 過適合 → block / repair |
| Replay Gate | 同一遷移の再実行整合性 | FAIL → non-canonical |
| Human Listening Audit | 少数層化サンプル | Evaluator drift の校正 |

## 9.1 Learning Gain と Hack を分離する

```
LearningGain = Score_child(hidden_test) - Score_parent(hidden_test)
TrainGain    = Score_child(train_eval) - Score_parent(train_eval)
HackDelta    = TrainGain - LearningGain

if TrainGain > 0 and LearningGain <= 0:
    suspect = OVERFIT_OR_REWARD_HACK
if identity_drift > theta_same_singer:
    suspect = IDENTITY_DRIFT
```

LearningTransition が学習データだけに強くなり、別 Probe で崩れる場合は「成長」とみなさない。v0.1 の Hack DB は v0.2 では学習過適合・Identity drift の失敗例も含む拡張監査資産になる。

# 10. ExecutionProfile と Replay

## 10.1 ExecutionProfile

| フィールド | 例 |
| --- | --- |
| profile_id | EP-0003 |
| container_image | sha256:... |
| gpu | RTX 4090 24GB |
| driver / CUDA / cuDNN | 固定 version |
| precision | fp32 / bf16 / fp16 |
| deterministic_algorithms | true |
| TF32 | disabled など明示 |
| seed bundle | model / loader / augmentation / sampler |
| dependency_lock | hash |
| kernel allowlist | 決定論保証した演算のみ |

## 10.2 Replay Test

1. 同一 parent Revision、dataset、recipe、seed、ExecutionProfile から学習を二回実行する。

2. child Adapter state hash、training log、validation score を比較する。

3. 固定 Probe を推論し、WAV hash または許容誤差内の特徴量一致を確認する。

4. 不一致なら原因を GPU kernel / data order / augmentation / mixed precision へ分解する。

5. Replay PASS の Transition だけ Canonical Singer 候補にする。

> **現実的な線引き**
>
> GPU backend によって byte-identical が不可能な場合でも、「どの ExecutionProfile でどの許容誤差まで再現できるか」を仕様化する。非決定性を黙認するのではなく、観測可能な再現性契約へ閉じ込める。

# 11. 失敗モードと停止規則

| 失敗 | 症状 | 自動処理 |
| --- | --- | --- |
| Hidden state mutation | 同じ Revision の連続推論で出力が変わる | 推論 backend を FAIL、Canonical から除外 |
| Non-replayable training | 同一遷移の再学習で state / score が再現しない | ExecutionProfile を隔離、原因特定まで commit 禁止 |
| Identity drift | 品質は上がるが本人性が変わる | 同一 Singer commit 禁止。Child lineage 候補へ |
| Catastrophic forgetting | 一部技能改善と引き換えに別音域・音素が崩れる | Distribution Shift FAIL、Repair または rollback |
| Reward hacking | Training Evaluator のみ上昇 | Hack DB、繁殖・Canonical 昇格を block |
| Overfitting to Probe | 固定 Probe は改善、未知曲で崩れる | Probe Set 分離・Hidden test 強化 |
| Lineage contamination | rights / source class 不明 | Provenance Gate FAIL |
| Revision explosion | 微小更新が大量 Revision を作る | commit threshold / squashing は履歴保持の上で pointer 整理 |

## 11.1 停止条件

個体学習が Quality を上げても Identity Consistency を継続的に下げる。

決定論設定で throughput が実用範囲を大きく下回り、Replay 成功率も安定しない。

Adapter 学習が Shared Backbone の能力不足を補えず、特定現象の欠損が反復する。

LearningGain が Hidden / Human で再現せず、Training Evaluator 攻略に偏る。

権利的に安全な Experience Dataset では有効な学習信号が不足する。

# 12. PoC v0.2 実験計画

> **PoC の目的**
>
> 「歌手が学ぶ」こと自体ではない。個体学習を導入しても、推論状態不変・Identity Freeze・Replay・Lineage・Quality Gate が一つの閉ループとして成立することを証明する。

| Phase | 実施内容 | 判定 |
| --- | --- | --- |
| 0. Contract Freeze | SingerRevision / LearningTransition / ExecutionProfile / ReplayRecord schema を固定 | 比較可能な実験単位 |
| 1. v0.1 Baseline | 学習なしで固定 SingerRevision を Probe | 基準品質・Identity・replay 分布 |
| 2. Deterministic Adapter Train | Performance Adapter のみ局所学習 | 同一遷移の Replay 成功 |
| 3. Identity Drift Audit | parent / child を別 Probe・別曲で比較 | same-singer 閾値を校正 |
| 4. Commit Gate | Quality + Hidden + Drift + Rights + Replay を統合 | Canonical pointer を安全に更新 |
| 5. Learning vs Mutation A/B | 同一 Backbone で Learning Edge と Evolution Edge を比較 | 原因分離と効率比較 |
| 6. Lineage Integration | 学習 Revision を親に次世代 Mutation / Crossover | 履歴と系譜が破綻しない |
| 7. Scale | 個体数・世代・training fragments を増加 | 小規模で成立した契約のみ拡大 |

## 12.1 最小実験対象

1–3 個の固定 Voice Genome。

1 個の Shared Backbone checkpoint。

1 個の Performance Adapter 方式（LoRA / adapter block など一つに固定）。

3–10 秒の学習 fragment と Hidden Probe。

1 GPU / 1 ExecutionProfile。

学習前後 Revision を 2 回以上 Replay。

# 13. 成功条件・反証条件

## 13.1 成功条件

Replay：同一 LearningTransition が規定の再現性レベルを満たす。

Immutability：推論のみでは SingerRevision の hash / state が変化しない。

LearningGain：Hidden test で parent より child の Performance 指標が改善する。

Identity：同一 Voice ID として commit した child が別 Probe / 別曲でも本人性を維持する。

Isolation：Genome / Backbone を固定した A/B で学習効果を独立に測定できる。

Lineage：parent、dataset、recipe、seed、ExecutionProfile、Evaluator、decision を完全追跡できる。

Rollback：Canonical pointer を旧 Revision へ戻して同じ出力を再現できる。

## 13.2 反証条件

個体学習のたびに Identity が大きく変化し、skill と本人性を分離できない。

決定論設定を強制しても Training replay が安定せず、原因を ExecutionProfile 内に閉じ込められない。

Performance Adapter の改善が未知曲へ一般化しない。

学習を入れることで QD / Lineage の多様性が一方向へ崩れる。

v0.1 の単純な「Genome 進化 + 共有学習」より計算効率・品質・監査性のいずれも改善しない。

> **反証の意味**
>
> v0.2 の目的は個体学習を必ず採用することではない。再現性と Identity を守れない場合は、LearningTransition を研究分岐として閉じ、v0.1 の二重時間尺度へ戻せること自体が設計上の安全性である。

# 14. V36 基盤への対応

| V36 Primitive | v0.2 での役割 |
| --- | --- |
| Branch | MUTATE / LEARN / REPAIR / REBASE を別 Branch として隔離 |
| Revision | SingerRevision / GenomeRevision / CheckpointRevision を不変保存 |
| Lease | 同一 LearningTransition の重複 GPU 実行・重複 commit を防止 |
| Gate | Quality / Identity Drift / Rights / Replay / Artifact / Hidden audit |
| CAS / single-writer | Canonical Singer pointer と Archive 更新の競合を防止 |
| Source-Free / Rights | Experience Dataset が Production 学習可能かを強制 |
| Audit Log | dataset / recipe / seed / ExecutionProfile / mutation / evaluator / decision を記録 |
| Invalidation | Backbone / Probe Set / Evaluator 更新時に旧評価を revalidation 対象へ |

## 14.1 API / Tool 境界の原則

Inference Tool は read-only SingerRevision を受け取り、state mutation API を持たない。

Learning Tool は candidate Revision しか作れず、Canonical pointer を直接変更できない。

Commit Tool は Gate 結果と ReplayRecord が揃った場合のみ CAS 更新する。

Evolution Tool は Identity を変える場合に必ず新 Voice ID / child lineage を発行する。

# 15. 最小データ Schema

## 15.1 SingerRevision

```json
{
  "voice_id": "VG-000421",
  "revision_id": "r18",
  "parent_revision": "r17",
  "identity_genome_sha": "sha256:...",
  "backbone_checkpoint_sha": "sha256:...",
  "performance_adapter_sha": "sha256:...",
  "execution_profile": "EP-0003",
  "identity_status": "FROZEN",
  "rights_class": "PROCEDURAL",
  "canonical": true
}
```

## 15.2 LearningTransitionRecord

```json
{
  "transition_id": "LT-000093",
  "parent_revision": "VG-000421:r17",
  "child_revision": "VG-000421:r18",
  "edge_type": "LEARN_PERFORMANCE",
  "dataset_sha": "sha256:...",
  "recipe_sha": "sha256:...",
  "seed_bundle": "SB-0011",
  "execution_profile": "EP-0003",
  "replay_record": "RP-000044",
  "identity_drift": 0.07,
  "quality_gate": "PASS",
  "decision": "COMMIT_SAME_VOICE"
}
```

## 15.3 ExecutionProfile

```json
{
  "profile_id": "EP-0003",
  "container_sha": "sha256:...",
  "gpu": "RTX4090-24GB",
  "cuda": "...",
  "cudnn": "...",
  "precision": "fp32",
  "deterministic_algorithms": true,
  "tf32": false,
  "dependency_lock_sha": "sha256:...",
  "kernel_policy": "DETERMINISTIC_ONLY"
}
```

## 15.4 ReplayRecord

```json
{
  "replay_id": "RP-000044",
  "transition_id": "LT-000093",
  "runs": 2,
  "adapter_state_match": "PASS",
  "probe_output_match": "PASS_WITH_TOLERANCE",
  "tolerance_profile": "TP-AUDIO-001",
  "max_feature_delta": 0.0008,
  "status": "PASS"
}
```

実装時は V36 の Branch / Revision / Lease / Gate / CAS に対応する永続 schema として定義し、JSON は交換表現に留める。

# 16. 研究仮説

| ID | 仮説 |
| --- | --- |
| H9 | 推論時の状態更新を禁止し LearningTransition を Revision 化すれば、個体学習を追加しても v0.1 の再現性・Lineage を維持できる。 |
| H10 | Performance Adapter に限定した局所学習は、Identity Genome を固定したまま未知 Probe の歌唱技能を改善できる。 |
| H11 | Identity Drift Gate は「同じ歌手の上達」と「別歌手への変異」を実験的に分離できる。 |
| H12 | Learning Edge と Evolution Edge を分離した A/B は、品質改善の原因を一括最適化より追跡しやすい。 |
| H13 | Replay Gate を Canonical 化条件にすると、非決定的 backend / recipe の混入を早期に検出できる。 |
| H14 | 個体学習を持つ系統でも Quality-Constrained Diversity Coverage を維持できる。 |
| H15 | 将来、Plasticity policy を進化対象へ拡張すると「高品質な歌手」だけでなく「学習効率の高い歌手系統」を探索できる。 |

## 16.1 v0.2 で新規性として主張できる可能性がある統合概念

Deterministic Learning Transition：個体学習を immutable Revision 間の明示的遷移として扱う。

Identity-Preserving Singer Learning：Identity Freeze を維持したまま Performance skill を更新する。

Replay-Gated Canonicalization：再学習・再推論可能性を Singer 採用条件へ組み込む。

Learning/Evolution Causal Separation：学習 Edge と進化 Edge を分離して原因追跡する。

Multi-Timescale Artificial Singer Evolution：推論・個体学習・系統進化・生態系学習を別時間尺度で統合する。

# 17. 実装優先順位

1. SingerRevision、LearningTransitionRecord、ExecutionProfile、ReplayRecord の schema を固定する。

2. 既存 Backbone の推論 path を完全 read-only 化し、state mutation test を追加する。

3. Performance Adapter の学習対象を一つ選び、freeze mask を固定する。

4. Experience Dataset manifest と Training Recipe hash を実装する。

5. 同一 Transition を二回 replay する harness を作る。

6. Identity Drift Gate を既存 Identity / Probe 評価へ接続する。

7. LearningTransition を V36 Branch / Revision / Lease / Gate / CAS の契約下へ置く。

8. Canonical Singer pointer の commit / rollback を実装する。

9. Learning vs Mutation A/B を小人口で回す。

10. 成立後にのみ Plasticity Genome、複数 Adapter、複数 GPU へ拡張する。

## 17.1 最初の完成判定

> **v0.2 Alpha の完成条件**
>
> 同一 Voice Genome / Backbone から固定 SingerRevision を生成し、Performance Adapter の学習で r17→r18 を作る。r17 は不変のまま再生でき、r18 は Replay Gate と Identity Drift Gate を通過し、Canonical pointer を CAS で更新・rollback できる。

## 17.2 今回はまだ入れないもの

リアルタイム自己学習・歌唱中の自律重み更新。

個体ごとの完全 Backbone checkpoint。

学習規則・Optimizer 自体の進化。

永続的な非構造化 Memory による暗黙人格変化。

複数 GPU / cross-hardware byte-identical を前提とした大規模学習。

# 18. 最終定義

> **VoiceGenesis Evolution Theory v0.2**
>
> 共有歌唱 Backbone を勾配学習で育て、Voice Genome を進化探索する v0.1 の二重構造を継承する。その上で、人工歌手個体の歌唱技能学習を Performance Adapter の LearningTransition として追加する。推論中は全状態を Freeze し、学習・進化・Repair・checkpoint 更新はすべて不変 Revision 間の明示的 Edge として記録する。Identity Drift、Quality、Rights、Hidden audit、Replay を Gate とし、同一歌手の成長と新系統への変異を分離する。成果は「学ぶ歌手」ではなく、「学習しても本人性・再現性・系譜・監査可能性を失わない人工歌手生態系」である。
