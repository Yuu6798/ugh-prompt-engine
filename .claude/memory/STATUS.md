# STATUS.md — ugh-prompt-engine プロジェクト状況

## Phase

2026-06-30 に **AI 演奏者のための楽譜マージロードマップ（#118）の基盤 3 段を 1 セッションで完走**
（Codex 不在・Claude 単独で実装→PR→レビュー→マージ）。**PR1(#120)** control_profile スキーマ
（生成器→物理フィールド→grip_class の自己記述・`fixity` と違い**疎を許容**・K2(#117) 初期データ）、
**PR1.5(#121)** control_profile-aware compile（`ExternalPromptAdapter` を control_profile 駆動の
フィールド粒度コンパイルへ刷新＝**tight 先頭昇格**(描画順は AC 外でユーザー確認)・`physical.optional`
束を 4 フィールド独立文へ分解・backend selector `external`→`suno`・priority エイリアス・backend
descriptor 隔離、Codex P2×3=casing 退行/time_signature 未描画/backend 誤ラベルを全対応）、
**PR2(#122)** score-adherence test（tight 宣言の `compiled_kept`(PR1.5 の drop されない保証)+
`preserved`(roundtrip 4 値診断) をフィールド単位判定・**計器であって verdict ではない**・backend
selector 共有で path 非依存）でマージ完了。楽譜が「保証チャネルを宣言→守ってコンパイル→守られたか
検証」のループとして立った。**PR2 のスコープ判断**: ロードマップ PR2 の CLAP 部は torch+2GB 重みで
本環境検証不能・`learned_models_policy` の adopt 外のため **PR2b へ分離**（依存方針の意思決定が先）。
標準コンテキスト: Genre Calib 本命 orchestral / K2 / 目的2(R0–R5・R1-audio) / Q1-5 Ph1/Ph2 は
closeout 済。**次の本命は PR2b（CLAP 学習センサー・依存判断要・本環境では spike 配線止まりの恐れ）
または PR3/K3（A/B 生成バッチが人手律速）**。いずれも今日の依存ゼロ一気通貫とは性質が異なる。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| 楽譜マージ PR2b | CLAP 学習センサー（意味層読解器） | P2 | **#122 で PR2 本体(score-adherence)から分離**。CLAP(または MuLan/CLAMP3)を learned 補助センサーとして配線し prompt↔audio/score↔audio の cosine 適合度=「学習版 grip」を算出、ルール版 grip と相互検証。`learned_models_policy.md` の**隔離原則厳守**(`LearnedAudioAnnotations` へ・ルール evidence 非混入)。**着手前に依存方針の意思決定が必要**: ①CLAP は torch+2GB 重みで現 adopt リスト外→policy 更新要 ②本環境で実推論検証不能=spike 配線止まりの恐れ ③「LLM不要・軽量」契約への影響(OSS 学習センサー限定・API LLM は out of scope)。依存=PR1/PR1.5(実コンパイル経路) |
| 楽譜マージ PR3 / K3 | K3 直交性(DCI/MIG)+機種デバイスプロファイル | P2 | grip ハーネス(`src/svp_rpe/control/`)を N×N importance matrix へ拡張(ツマミ i が観測 j を動かすか・対角=grip/非対角=干渉)、DCI/MIG で定式化。K2(#117)で対角 grip(bpm/brightness tight 転移)立証済。+generator デバイスプロファイル(genre bias を機種別補正へ構造化)。依存=PR1/PR1.5。律速=A/B 生成バッチ(人手)。`docs/ai_performer_score_roadmap.md` §PR3 / controllability_poc.md §5 |
| Q1-5 Ph2 | spectral 帯域 magnitude 再校正(残) | P3 | **大半 closeout 済**: brightness=B-3 / `high_ratio==0.0` 前提見直し=#107 / low/mid_ratio 据え置き finding=#108(low はゲートで健全・mid は未発火で繰越・general アンカー欠如)。残=`screen_2026-06-16.yaml` 再採取 + mid_ratio/magnitude 軸の bias 検証(Phase C と統合・licensing 律速)。**BPM**: #106 で sr/閾値の単一ノブ修正は大域回帰と確定(finding #6)。自動化は multi-prior/sr ensemble+octave tie-break を全 fixture 回帰検証付き Design Memo 化(K2 #117 でも bpm 素朴センサーの prior アトラクタが grip を圧縮することを再確認) |
| Genre Calib seed follow-up | EDM 5本の Drive 化 + Suno prompt 転記 | P3 | #103 で measured はインライン保全済。残=`electronic_dance_*` の Drive アップロード→`drive_file_id` 回収 + `prompt:` 本文の PENDING 解消。律速=人間作業 |
| Genre Calib 4th genre | acoustic へ域拡張 | P3 | acoustic(low_ratio<0.4 の general 落ち領域を突く coverage 拡張型)を rock(#105)と同じ協働フロー(物理層明示プロンプト→user 生成→抽出→分離分析→ルール反映)で追加。律速=人間生成バッチ |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #122 | feat(roundtrip): score-adherence test（PR2）＝control_profile-tight 保証の準拠判定計器（`svprpe score-adherence`: compiled_kept(PR1.5 の drop されない保証)+preserved(roundtrip 4 値診断) をフィールド単位判定・backend selector 共有で path 非依存・**計器であって verdict ではない**=グローバル pass/fail なし。CLAP は torch+2GB 重み・policy adopt 外で PR2b へ分離） | 2026-06-30 | AI-Performer Score Roadmap |
| #121 | feat(compose): control_profile-aware compile（PR1.5）＝コンパイルループを Suno で閉じる（ExternalPromptAdapter を control_profile 駆動のフィールド粒度コンパイルへ刷新・**tight 先頭昇格**(ユーザー確認)・physical.optional 束を 4 フィールド独立文へ分解・backend selector external→suno・priority エイリアス・backend descriptor 隔離。Codex P2×3=casing 退行/time_signature 未描画/backend 誤ラベル全対応） | 2026-06-30 | AI-Performer Score Roadmap |
| #120 | feat(compose): control_profile スキーマ（PR1）＝楽譜が効くチャネルを自己記述（生成器→物理フィールド→grip_class・`fixity` と違い**疎を許容**(K2 の Suno は bpm/brightness のみ)・未知 field fail-fast・ControlGrip(grip_class 必須/grip・sensor・evidence optional)・K2(#117) 初期データ投入・docs/control_profile.md 新規） | 2026-06-30 | AI-Performer Score Roadmap |
| #119 | docs: 楽譜マージロードマップに PR1.5(control_profile-aware compile)を新設＝壁打ち結論を反映（本命は楽譜=実用物であって測定器でない・コンパイル脚は既存 ExternalPromptAdapter/performer と判明し PR2 の前に PR1.5 昇格・決定論=物理層保証/非決定論=意味層助言で CLAP は OSS 学習センサー限定 LLM は out of scope・多生成器は Suno ルート確立後+backend seam・Codex P2 を 13 件/10 コミット全対応で PR1.5 spec が緻密化・厳密実装契約は Design Memo で確定のスコープ境界注記で発散収束） | 2026-06-29 | AI-Performer Score Roadmap |
| #118 | docs: AI 演奏者のための楽譜 マージロードマップ（3 PR 構成）＝既存研究(MIR/CLAP/DCI-MIG/制御性評価/EPR)と蓄積知見(K 系列 grip/roundtrip fixity/genre bias)をマージ。PR1=control_profile スキーマ(fixity 踏襲・K2 初期データ)/PR2=楽譜準拠テスト+CLAP(隔離下)/PR3=K3 直交性(DCI/MIG)+機種プロファイル。索引2箇所同期 | 2026-06-29 | AI-Performer Score Roadmap |
