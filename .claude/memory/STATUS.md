# STATUS.md — ugh-prompt-engine プロジェクト状況

## Phase

2026-07-01（2 セッション）、実音源デモから**意味層トラック**が実データで前進（Claude 単独・実装も
Claude）。S1（#123 merged）で「同一 EDM アレンジ×歌詞あり/なし」対照から**歌詞=意味層のアンカー**
（付与する「メリハリ」は物理計器の盲点・耳が唯一のセンサー）を観測。S2（**#124 merged**）で実音源
StartinA を identity(調号/コア進行)固定のまま **EDM/Rock** へ再キャストし、歌詞あり/なし＋歌詞側の
再生成別取り(alt)を実 Suno で **n=3 追試**。結論: (1) **`dynamic_range`=歌詞アンカー説は棄却**
（Rock で反転かつ再生成ノイズ未満・EDM も instrumental alt 無しで directional 保留＝proxy に使えない）、
(2) **`mid_ratio` は最有力ボーカルセンサーだが noise 超えは Rock のみ**（EDM は方向のみ・昇格は各
ジャンル instrumental alt 込み **n≥2×2 セル要件**）、(3) 付随=**BPM grip は確度×精度の2軸**（EDM=129
精密ロック/Rock=108±5 揺れ）・**調号は grip（生成6中5）/進行は非再現**（推定器律速）。方法論として
**「効果 > 再生成ノイズ」基準を全指標に一様適用**する規律を確立（Codex P2×10 全対応で厳密化・計測ログは
audio_sha256 pin が慣例化）。**次の本命は意味層トラック続行**（検証デザイン B/C＝歌詞条件分離・
**instrumental alt を含む n≥2×2 セルを埋める**・`control_profile` への `lyrics_presence` ノブ Design
Memo）で、CLAP=PR2b の導入動機が実データ×主観の乖離で裏付いた。既存キュー
（PR2b/PR3-K3/Q1-5 Ph2/Genre Calib follow-up）は不変。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| 楽譜マージ PR2b | CLAP 学習センサー（意味層読解器） | P2 | **#122 で PR2 本体(score-adherence)から分離**。CLAP(または MuLan/CLAMP3)を learned 補助センサーとして配線し prompt↔audio/score↔audio の cosine 適合度=「学習版 grip」を算出、ルール版 grip と相互検証。`learned_models_policy.md` の**隔離原則厳守**(`LearnedAudioAnnotations` へ・ルール evidence 非混入)。**着手前に依存方針の意思決定が必要**: ①CLAP は torch+2GB 重みで現 adopt リスト外→policy 更新要 ②本環境で実推論検証不能=spike 配線止まりの恐れ ③「LLM不要・軽量」契約への影響(OSS 学習センサー限定・API LLM は out of scope)。依存=PR1/PR1.5(実コンパイル経路) |
| 楽譜マージ PR3 / K3 | K3 直交性(DCI/MIG)+機種デバイスプロファイル | P2 | grip ハーネス(`src/svp_rpe/control/`)を N×N importance matrix へ拡張(ツマミ i が観測 j を動かすか・対角=grip/非対角=干渉)、DCI/MIG で定式化。K2(#117)で対角 grip(bpm/brightness tight 転移)立証済。+generator デバイスプロファイル(genre bias を機種別補正へ構造化)。依存=PR1/PR1.5。律速=A/B 生成バッチ(人手)。`docs/ai_performer_score_roadmap.md` §PR3 / controllability_poc.md §5 |
| Q1-5 Ph2 | spectral 帯域 magnitude 再校正(残) | P3 | **大半 closeout 済**: brightness=B-3 / `high_ratio==0.0` 前提見直し=#107 / low/mid_ratio 据え置き finding=#108(low はゲートで健全・mid は未発火で繰越・general アンカー欠如)。残=`screen_2026-06-16.yaml` 再採取 + mid_ratio/magnitude 軸の bias 検証(Phase C と統合・licensing 律速)。**BPM**: #106 で sr/閾値の単一ノブ修正は大域回帰と確定(finding #6)。自動化は multi-prior/sr ensemble+octave tie-break を全 fixture 回帰検証付き Design Memo 化(K2 #117 でも bpm 素朴センサーの prior アトラクタが grip を圧縮することを再確認) |
| Genre Calib seed follow-up | EDM 5本の Drive 化 + Suno prompt 転記 | P3 | #103 で measured はインライン保全済。残=`electronic_dance_*` の Drive アップロード→`drive_file_id` 回収 + `prompt:` 本文の PENDING 解消。律速=人間作業 |
| Genre Calib 4th genre | acoustic へ域拡張 | P3 | acoustic(low_ratio<0.4 の general 落ち領域を突く coverage 拡張型)を rock(#105)と同じ協働フロー(物理層明示プロンプト→user 生成→抽出→分離分析→ルール反映)で追加。律速=人間生成バッチ |
| 意味層トラック lyrics | 歌詞=意味層アンカー仮説の検証続行 + control_profile lyrics ノブ | P2 | **2026-07-01 発見**(`docs/lyrics_semantic_anchor.md`/#123)＋**n=3 追試(S2/#124)**。追試で確定: **`dynamic_range`=歌詞アンカー説は棄却**(EDM 限定・Rock で反転かつ再生成ノイズ未満)＝もう「dynamic_range 逆相関を再検証」しない。`mid_ratio` は最有力検出子だが **noise 超えは Rock のみ・EDM は directional(instrumental alt 未取得)**＝「堅い」と断定せず**昇格は各ジャンル instrumental alt 込みの n≥2×2 セル要件**。残る未検証: (a) 検証デザイン B/C=同一アレンジ×【歌詞なし/原曲無関係歌詞/原曲由来歌詞】で「原曲寄り度」を人間評価×物理乖離(dynamic_range は棄却済につき別指標を探す)。(b) `control_profile` に `lyrics_presence` ノブ Design Memo。**n=1「ボーカル=主音の錨」は n=2 で棄却済**。CLAP=PR2b の導入動機と接続。律速=人間生成バッチ+主観評価 |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #124 | docs: 歌詞アレンジ・デモ n=3 追試（実音源 StartinA を EDM/Rock 再キャスト × 歌詞あり/なし＋歌詞側 alt を実 Suno 計測）＝前 #123 の「歌詞→dynamic_range 低下」を **棄却**（Rock 反転かつ再生成ノイズ未満・EDM も instrumental alt 無しで directional 保留）、**mid_ratio は最有力だが noise 超えは Rock のみ**（昇格=各ジャンル instrumental alt 込み n≥2×2 セル）。付随=BPM grip 確度×精度2軸・調号 grip(生成6中5)/進行 非再現。計測ログ＋audio_sha256 pin・Tier-A サーフェス同期。**「効果>再生成ノイズ」基準を全指標に一様適用**の規律確立。Codex 自動レビュー P2×10 全対応（noise baseline/sha256 provenance/生成器分母5-6 等） | 2026-07-01 | 意味層トラック |
| #123 | docs: 歌詞=意味層アンカー仮説（アレンジ・デモ発見の保全）＝実 Suno＋実音源の「同一 EDM アレンジ × 歌詞あり/なし」2 曲対照から**歌詞は意味層のアンカー**（付与する「メリハリ」は物理 dynamic_range に写らずむしろ逆＝計器の盲点・耳が唯一のセンサー）。honesty: n=1「ボーカル=主音の錨」を n=2 方向反転で棄却・halving 非法則化（n≥3 保留）。中域 mid_ratio はボーカル検出に堅い。付随=genre pop 帯欠落/低 sub EDM 誤判定/実音源 halving/m4a 非対応。n≥3 検証デザイン明記・索引2箇所同期。**※ n=3 追試 #124 で dynamic_range 逆相関を proxy 棄却・mid_ratio を Rock 限定に更新（この行の旧主張は superseded）** | 2026-07-01 | 意味層トラック（新設） |
| #122 | feat(roundtrip): score-adherence test（PR2）＝control_profile-tight 保証の準拠判定計器（`svprpe score-adherence`: compiled_kept(PR1.5 の drop されない保証)+preserved(roundtrip 4 値診断) をフィールド単位判定・backend selector 共有で path 非依存・**計器であって verdict ではない**=グローバル pass/fail なし。CLAP は torch+2GB 重み・policy adopt 外で PR2b へ分離） | 2026-06-30 | AI-Performer Score Roadmap |
| #121 | feat(compose): control_profile-aware compile（PR1.5）＝コンパイルループを Suno で閉じる（ExternalPromptAdapter を control_profile 駆動のフィールド粒度コンパイルへ刷新・**tight 先頭昇格**(ユーザー確認)・physical.optional 束を 4 フィールド独立文へ分解・backend selector external→suno・priority エイリアス・backend descriptor 隔離。Codex P2×3=casing 退行/time_signature 未描画/backend 誤ラベル全対応） | 2026-06-30 | AI-Performer Score Roadmap |
| #120 | feat(compose): control_profile スキーマ（PR1）＝楽譜が効くチャネルを自己記述（生成器→物理フィールド→grip_class・`fixity` と違い**疎を許容**(K2 の Suno は bpm/brightness のみ)・未知 field fail-fast・ControlGrip(grip_class 必須/grip・sensor・evidence optional)・K2(#117) 初期データ投入・docs/control_profile.md 新規） | 2026-06-30 | AI-Performer Score Roadmap |