# DESIGN S6 — run 7（教師交代: D3 引退 → あみたろ実録音発音教師・単一介入）

- 起草: 2026-08-19（Claude 設計。あみたろ = 教師役の確定裁定と run 6 先行の
  順序は **User 裁定 2026-08-19 済み** —
  [`DESIGN_DONOR_EXPANSION.md`](DESIGN_DONOR_EXPANSION.md) §0 裁定追記 /
  [`DESIGN_S5_run6.md`](DESIGN_S5_run6.md) §0-3。本書はその執行契約であり、
  User の起床後マージをもって実験契約として発効する）
- 位置づけ: S 系列の第 6 設計書。run 7 = **VoiceGenesis 第 7 学習走行**の
  実験契約。DX トラック（`DESIGN_DONOR_EXPANSION.md`）の run 7 分岐
  「Q7 = D3 起因 → あみたろ教師交代を先行」（分岐解決済み 2026-08-19・
  s4_record §6.2）の実施回
- 前提: run 6（user 正規化）の走行結果。**run 7 は run 6 の完走 checkpoint を
  重みとしては一切使わない**（スクラッチ学習の継承）が、**データ基盤 =
  run 6 と同一**（正規化 user・ritsu・pjs・辞書）を前提とするため、
  run 6 の s5 record 起草（①②③判定）後に起動する

## 0. 裁定（本書で凍結する設計判断）

1. **run 7 は単一介入 = 教師の実体交換**: 合成教師 d3synth（render 生成物・
   40 wav・実効 20.008 分）を**引退**させ、実録音発音教師 **amitaro**
   （あみたろの声素材工房・ITA コーパス読み上げ音声）を**同 dosage で投入**
   する。データ差分は「教師データの実体」の 1 変数に閉じる — user/ritsu/pjs
   データ・学習レシピ・辞書は run 6 と同一。ただし新教師の intake には
   -26.1 LUFS への水準合わせが随伴する（会計上の注意 = §2-4）
2. **spk_id 裁定: amitaro = spk_id 4・num_spk = 5・id 3（d3synth）は恒久欠番**。
   - 根拠 (a): 「既存 ID 恒久不変・追加は末尾のみ」規律（DESIGN_S4 §1.1・
     assemble の SPK_IDS 固定規約）。id 3 を amitaro に再利用すると、run 5/6
     checkpoint・ONNX・spk_map.json の全歴史資産で「spk_id 3 = 合成教師」の
     意味が壊れ、run 間比較と再現の基盤が失われる
   - 根拠 (b・実装裏取り済み): DiffSinger（pin e2307b1）の binarizer は
     datasets 毎の明示 `spk_id` と欠番を許容する（`basics/base_binarizer.py`
     build_spk_map — 検査は `max(spk_ids) < num_spk` のみ）。話者埋め込みは
     `Embedding(num_spk, hidden_size)`（`modules/toplevel.py:150`）なので
     欠番のコストは未使用行 1 つ。スクラッチ学習につき行 3 は初期値のまま
     残るだけで学習に非干渉
   - **欠番の強制検査（同 build_spk_map の罠への対策）**: DiffSinger は
     `spk_id` 未指定のエントリへ**最小の空き番号を自動採番**する（同関数
     77–84 行）— 設定ミスで amitaro の明示 spk_id が落ちると新教師が
     **黙って id 3（歴史上の合成教師）に埋まり**、DiffSinger 側検査は
     素通しになる。よって run 7 の assemble 検証と bootstrap preflight は
     **spk_map.json が期待マップ `{ritsu:0, pjs:1, user:2, amitaro:4}` と
     完全一致し、かつ id 3 が不在であることを assert**する（§3-3/§3-4 の
     実装要件）
3. **教師投入量 = D3 同等の実効 20 分（±10%）**。実測着地（2026-08-19
   pin 生成）: **採用 299 文 / 除外 25 文（全て外来音の dsdict 未収載 =
   fail-closed 記帳）・total ph_dur 1124.5 s（D3 比 -6.3%・帯内）・
   voiced 864.9 s**。run 5/6 の教師 dosage
   （ph_dur 合計 1200.5 s）に合わせ、変えるのは教師の「実体」だけにする
   （量と質を同時に動かすと Q12 の帰属が壊れる）。ITA 全 3,211 ファイルの
   大量投入は**しない** — 教師枠 = 少量投入（DX §0/§2-2 の前提）を維持
4. **規約の基準時点**: あみたろの利用規約は **2026-08-11 に改訂されている**
   （サイト告知記事）。本 run の許諾根拠は 2026-08-19T17:52:00Z 取得の
   規約ページ（§2-1 の sha256 逐語 pin）であり、調査時点（survey 08-18）の
   要約ではなく**この取得版が正**。改訂版の実質差分で本設計に効くもの:
   モデル配布の比率ルールの会計基準が「**あみたろの声が入っている部分の
   長さ（無音部を除いた発話時間）がモデル全体に占める割合**」と明文化
   （§2-4 の会計はこの基準に同型で設計する）
5. **emotion 系は不使用・recitation のみ・スタイル = ノーマル**: ITA =
   emotion 100 文 + recitation 324 文。感情演技の韻律変動は発音教師として
   交絡（教師の目的 = 音素明瞭性の供給であり感情表現ではない）。recitation
   に限定する。**配布実態の実査（2026-08-19 追記）**: 「平均 7.6 テイク・
   3,211 ファイル」の実体は 6 スタイル（ノーマル/るんるん/よふかし/
   ぷんすか/ささやき A/B）の版違い。教師素材は**ノーマル**（「ひとつひとつの
   音を正確にはっきりと発音した」音声読み上げ向けスタイル・一覧ページ逐語・
   **1 文 1 ファイル** `recitationNNN.wav`・最新版 Ver.2.2〔2026-06-05〕）に
   凍結する。**recitation ノーマル全文の実測総尺 = 1225.6 s（20.43 分・
   324 本・平均 3.78 s）** — D3 dosage 1200.5 s とほぼ同尺で、§0-3 の同
   dosage 契約は recitation 全文でぎりぎり成立する規模（除外の合計が実測
   約 145 s を超えると §2-3 の下限 assert で fail-closed になり、その場合は
   本 memo の改訂裁定に戻る）
6. 予算 cap: **$4**（run 6 と同額。データ総量は D3→amitaro 同 dosage 交換で
   ほぼ不変・40K steps 同一につき run 5 実績 ≈$1.35 と同水準の見込み）

## 1. 介入の定義

### 1.1 変更内容

- **除去**: assemble から d3synth データセットエントリを外す（spk_id 3 は
  欠番として台帳に残す）。`convert_d3.py`・d3 manifest・run 4 pin の d3
  セクションは**歴史資産として削除しない**（run 4/5/6 の再現に必要）
- **追加**: amitaro データセット（spk_id 4・教師 = Identity 空間外・
  DX §1 の実録音教師）。intake は §2 の契約に全従
- ゲート合成の教師単独枠は d3synth → amitaro に交代（run 5 判定①の
  「d3synth 単独」に相当する教師音サニティ枠）

### 1.2 検証仮説（run 7 の主検証点）

- **Q12**: 実録音教師で①の破綻矯正効果は保存されるか + 局所退行 2 点
  （みわたす語尾「す」〔ritsu/pjs さくら〕・教師由来の さ→あ 傾向）は
  消えるか（DX §3 で予告済みの主検証点。さ→あ は d3synth 自身の枠が
  消えるため、ritsu/pjs/user 側での さ行 onset 明瞭性で判定する）
- 回帰確認: ②（run 6 で無留保確定していればその維持）・③（ノイズ不在の
  維持 — **実録音教師はレンダ・アーティファクトを持ち込まない**ことの確認。
  Q7 帰属〔ノイズ = D3 起因〕の最終検証にもなる）

### 1.3 変えないもの

user 正規化（run 6 pin の -26.1 LUFS を含む変換系全体）・ritsu/pjs データ・
辞書・学習レシピ（スクラッチ + 5K finetune 再適用・40K・bf16-mixed /
lr 2e-4 / clip 1.0）・S2/S4 の教師分離機構（spk_id 付与 + Identity 空間除外）。
つくよみ第 4 頂点は本 run に**含めない**（DX §3: run 7 は 1 介入のみ・
先約は VG-E1 の多様性実測後に再浮上）。

## 2. intake 契約（あみたろ・DX §2-2 の執行）

**全充足まで学習投入不可**（つくよみ §2-1 と同じ fail-closed 順序）:

### 2-1. 規約の逐語 pin 台帳（`amitaro_intake_ledger.json` 新設）

取得済み素材（2026-08-19T17:52:00Z・curl・生 HTML は **Drive
`run7/intake_evidence/run7_intake_evidence_2026-08-19.tar.gz` に退避済み**
〔tar sha256 `e9d2c0aa965bbac15aebdeb900c6a01b7fd673d6872b258feb7c94cd66c7e661`〕
= repo には規約ページ全文を収載しない。run 6 の `run6/pin_evidence/` と
同じ流儀）:

| ページ | URL | sha256 |
|---|---|---|
| フリー声素材ご利用規約 | https://amitaro.net/voice/voice_rule/ | `471d36a8b026279cf4d9abf1c771529b639671327d73c292d40c72117f5a009a` |
| ITA コーパス配布記事 | https://amitaro.net/wp/ita_01/ | `4f0c5887c8478edac79456202234f1cc04b0ee5e1c2bfec9ace03dec85fa4c8e` |
| ITA 読み上げ音声一覧 | https://amitaro.net/voice/corpus-list/ita/ | `8b49c73dbb82fc2d36da8e0710ba6eebdbe4aca8c62930a73133aaa5e2f71f76` |

台帳に記帳する逐語（**全て 1 行目の規約ページ〔sha `471d36a8…`〕から**。
要旨でなく引用 — 引用と pin 対象ページの対応を台帳に持たせる）:

- AI 学習許可: 「このページの規約を守れば、音声合成モデルや AI ボイス
  チェンジャーモデルの学習データとしても使えます。学術研究も OK です」
  「学習・実験・研究目的での利用・共有は OK です。事前確認もいりません」
- クレジット（必須・2 要件）: 「学習データにあみたろの声を使用していることを
  明記してください」「あみたろ本人が作ったものではないことも明記して
  ください」。書式 = 「学習データ：あみたろの声素材工房（https://amitaro.net/）」
- 配布比率ルール: 「あみたろの声が入っている部分の長さ（無音部を除いた
  発話時間）がモデル全体に占める割合」で、おおむね 50% 未満 = 単体有料
  販売可（全素材共通ルール適用）/ 50% 以上 = 無料公開のみ / あみたろのみ =
  配布条件つき。「迷った場合は『50%以上』として扱ってください」
- 禁止用途: 詐欺電話（vishing）・ディープフェイク・NFT/暗号資産・
  ギャンブル/薬物/武器宣伝・年齢制限・政治宗教 等

### 2-2. 素材 pin

- **配布実体 = 個別 wav 直 URL**（実査 2026-08-19: 一括 zip の直リンクは
  一次ページ群に存在しない）:
  `https://amitaro.net/download/corpus/ita-sample/recitation/recitation{NNN}.wav`
  （一覧ページ「全文試聴＆個別ダウンロード」系列・48kHz/PCM_16/mono を
  取得プローブで実測確認）。取得ファイルの **sha256 全数**を run 7 pins
  （`amitaro.staged_source_sha256`）に pin し、bootstrap は Drive 退避コピー
  （`run7/amitaro_sources/`）をこの pin と**集合ごと**照合してから変換する
- ITA テキスト正本 = GitHub mmorise/ita-corpus（配布記事からの公式参照・
  パブリックドメイン宣言）。`recitation_transcript_utf8.txt` を commit +
  sha256 で pin し、PD につき**バイト同一で repo に収載**する
  （`s1_dataprep/data/` — かな読みの音素化入力になるため、転写のドリフトは
  変換出力を変える。来歴 = intake 台帳）

### 2-3. 選定規則（決定論・記帳可能）

1. recitation 324 文のみ（§0-5）を**文番号昇順**に走査
2. 各文につき **ノーマルスタイルの 1 ファイル**（`recitationNNN.wav` —
   §0-5 の配布実態どおり 1 文 1 ファイルで、テイク品質の手動選抜は
   存在しない/しない。選定に耳を入れると「教師データの決定論的来歴」が
   壊れる）
3. **除外は走査中に適用**: dsdict 未収載 grapheme を含む文は既存
   fail-closed 流儀でその場で除外・記帳（convert_user の exclusions.json
   と同型・run 4 の UC-010「ヴ」前例あり）。除外文の ph_dur は累積に
   **算入しない**
4. 変換（§2-4）後の**採用文 ph_dur 累積が 1200.5 s に到達した文で打ち切り**
   （到達文を含む）
5. **dosage の fail-closed 検査**: 最終合計が **1200.5 s ±10%
   （1080.45〜1320.55 s）に入ることを assert**。recitation 全文を走査し
   きっても下限に届かない場合は**エラー停止**（黙って過少 dosage の教師
   セットを出力しない — §0-3 の同 dosage 契約が Q12 帰属の前提のため。
   その場合は選定規則を本 memo の改訂で裁定し直す）
6. 選定後の被覆検査: ①局所退行に対応する目標音素（さ行 onset・語尾
   「す」）が選定セットに含まれることを機械検査。不足時は**本 memo の
   改訂で選定規則を裁定し直す**（黙って手動追加しない）

### 2-4. 変換系（`convert_amitaro.py`・convert_user の T2 機構を再利用）

- 48kHz/16bit/mono → 44.1kHz は **pinned ffmpeg（n6.1.2）** で変換
- 音素化 = dsdict 逐語 lookup・タイミング = RMS ラン分割・音高 =
  f0 中央値 MIDI（convert_user T2 と同一機構。ANALYSIS_STACK_PIN =
  numba 0.66.0 / librosa 0.11.0 / pyloudnorm 0.2.0 環境で実行）
- **カナ→かな表記ゆれ正規化の 2 段 lookup（2026-08-19 実測追記・同日訂正）**:
  ritsu dsdict は基本モーラを**ひらがな = 小文字母音**（す→[s,u]）で、
  同モーラの**カタカナ**を**大文字母音 = 別音素クラス**（ス→[s,U]）で、
  外来音結合（ティ/ファ 等）を**カタカナのみ**で持つ（実測）。ITA の
  カタカナ読みを生のまま引くと (a) 基本モーラの多くが unmapped（324 文中
  304 除外）、(b) 当たった場合も大文字母音クラスへ落ちて歌唱コーパスの
  通常母音と不連続（§2-3-6 の被覆検査〔文末 s u〕が fail-closed で検出 —
  被覆検査の初仕事）。よって lookup は「**ひらがな写像 → 生カタカナ**」の
  順の 2 段とする — 基本モーラは通常母音へ、外来音結合のみ生カタカナへ
  フォールバック（user T2 カードの表記慣行と同じ音素割当）。これは
  `convert_user._normalize_sokuon_nasal`（ッ/ン）と同じ「**同一音素の表記
  ゆれの正規化であり、辞書に無い別の音への代用ではない**」原則の全カナ版。
  ヴ 等どちらでも当たらない音は従来どおり fail-closed 除外（代用しない）
- **投入前ラウドネス正規化 = -26.1 LUFS**（目標値の単一ソース =
  `run6_dataset_pins.json` の `normalization_target_lufs` — 手打ち再宣言
  しない）+ 会計 json（loudness_normalization.json 同型）。
  **正直会計**: run 6 が凍結したのは「user だけを動かす」単一介入であり、
  正規化はプロジェクトの常設 intake 方針として宣言されたものでは**ない**。
  本 run で新素材 amitaro に正規化を適用するのは、run 6 で根治したばかりの
  音量交絡（測定交絡）を無正規化の新素材で再導入しないための設計判断で
  ある。引退する d3synth は無正規化だったため、**厳密には「教師の実体」に
  「新教師の水準合わせ」が随伴する** — §0-1 の単一介入会計はこの随伴込みで
  読むこと（Q12 の帰属に効く場合はこの複合を明記して記帳する）
- **50% 会計の材料**: カード毎の発話時間（無音部除く = 有声実長）を変換
  manifest に記帳（§2-5 の分子）

### 2-5. 50% 会計（assembly manifest 配線）

- assemble 時に話者毎の**無音部を除いた発話時間**（規約の会計基準に同型）と
  構成比を manifest に記帳し、`amitaro_share < 0.50` を**機械検証**
  （assert・fail-closed）。教師枠 20 分 vs 全学習データにつき構造的に
  安全圏（概算 <10%）だが、会計は恒久配線する（DX §2-2-2）
- 「迷った場合は 50% 以上扱い」の規約文言に対し、本会計は**保守側**
  （有声実長のみを分母側でも同基準で集計）で設計する

### 2-6. クレジット・禁止用途の記帳

- 成果物（モデル・合成音の公開物）公開時の必須クレジット 2 要件 + 書式を
  intake 台帳と s6 record に記帳（DX §2-2-3）
- 禁止用途はプロジェクト既存倫理線（なりすまし不可・来歴清潔）と整合 —
  逸脱用途が生じないことを intake 台帳に明記（DX §2-2-4）

## 3. 必要コード変更（事前実装・PR 経由）

1. **`amitaro_intake_ledger.json`**（§2-1/2-2/2-6）+ 形状テスト
2. **`convert_amitaro.py`**（§2-3/2-4: 選定規則・変換・正規化・会計）+
   追随テスト（決定論・打ち切り規則・被覆検査・unmapped fail-closed）
3. **assemble の run 7 対応**: SPK_IDS の run 7 版（ritsu=0/pjs=1/user=2/
   amitaro=4・num_spk=5・d3 エントリなし）。検証関数は run 依存の期待
   マッピングを引数化し、run 5/6 系（4 話者・num_spk 4）の検証は不変に保つ
   + 構成比会計（§2-5）+ **spk_id 欠番の強制検査**（§0-2: 期待マップ
   完全一致 + id 3 不在の assert）+ テスト
4. **bootstrap の run 7 プロファイル**: RUN_PROFILES へ run7 追加
   （run_id=s6_run7・exp=s6_run7_acoustic_{scratch,v1}・
   pins=run7_dataset_pins.json・REMOTE_PREFIX="run7"）+ amitaro 素材の
   搬入/検証段 + gate_synth 教師単独枠の交代 + **binarize 後の
   spk_map.json 検査**（§0-2 の assert を Pod 側でも実施 — 自動採番の
   罠は binarize 実行時に発現するため）
5. **`run7_dataset_pins.json` のローカル生成**（pinned 環境 = run 6 pinlab
   同一手順）: d3 セクションなし・user = run 6 pin の逐語継承・amitaro =
   新実測（wav sha 全数・transcriptions/exclusions・loudness 会計・
   選定規則の来歴）
6. 素材 pin 追記（§2-2）

## 4. 実行契約

- runbook = S4_RUN5_RUNBOOK を run 7 差分で継承（RUN_PROFILE=run7・
  退避 prefix `run7/`・heartbeat 監視様式同一）
- preflight の前回 manifest 比較の相手 = run 6 manifest（repo_commit /
  pins / datasets 構成の差分が info で出るのが正常系）
- 予算 cap $4・24h 自己停止・NaN/pin 不一致 fail-closed は全継承

## 5. 判定材料（run 6 様式の継承）

1. **① 回帰 + Q12**: さくら/うみ × ritsu/pjs + **amitaro 単独**（教師音
   サニティ・d3synth 単独枠の後継）。局所退行 2 点の消長が主判定
2. **② 維持確認**: run 6 の A/B/C 再判定結果を基準に同一手順で組成
3. **③ 三世代比較**: run 5 / run 6 / run 7（ノイズ不在の維持 = Q7 帰属の
   最終検証）
4. **④ は生成しない**（founding = run 5 checkpoint 固定・VG-E1 の管轄）

## 6. Acceptance Criteria（run 7 出口）

- [ ] §2 intake 契約の全充足（台帳・素材 pin・選定規則の機械記帳・
  50% 会計配線・クレジット記帳）— **充足前の学習投入は不可**
- [ ] §3 のコード変更が PR で main に入っている
- [ ] 学習 40K 完走（NaN ゼロ）または fail-closed 停止の証跡
- [ ] 判定材料 ①②③ の生成と User 耳判定の記録（s6 record 起草）
- [ ] Q12 の裁定記帳（①矯正保存の成否 + 局所退行 2 点の消長の帰属）
- [ ] 費用 ≤ $4

## 7. Open Questions / 送り

- Q12: §1.2 の通り run 7 の主検証点
- run 8 候補（run 7 の結果で分岐・本書では裁定しない）: (a) つくよみ第 4
  頂点（VG-E1 の多様性実測後・DX §2-1 の intake 前提充足が先）、(b) T3
  追加収録（run 6 の Q11 が「データ量・音域被覆」側を示した場合のみ）
- 教師 dosage の増量実験（20 分 → recitation 全文）は「教師量」を単一介入
  とする別 run の候補として記録のみ
