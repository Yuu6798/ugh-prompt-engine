# DESIGN S3 — backfill 学習（run 4）: D3 合成データ + 第 3 ドナー投入

- 起草: 2026-08-17（`.claude/memory/2026-08-17_design_next_session.md` の昇格版。
  以後は本ファイルが S3 の設計正本）
- 前提正本: S1 = `results_s1/s1_record_2026-08-15.md` / S2 = `results_s2/s2_record_2026-08-16.md` /
  第 3 ドナー intake = `recording_kit/intake_records/intake_record_2026-08-17.md`
- 設計判断（決定済み）: **run 4 = 「D3 追加 + 第 3 話者追加」の 3 話者統合学習 1 回。
  vocoder 差し替え（BigVGAN v2）は学習から分離した推論側トラック**。
  同時投入を許す理由: 評価軸が相互独立（り→ん破綻はリツ極で判定 / 第 3 声は
  新アンカーの立ちで判定）であり、S2 の単一要因教訓（同一軸に複数変更を重ねない）に
  抵触しない。
  **【2026-08-17 改訂（run 4 実測による論拠の棄却）】** 上記「評価軸が相互独立
  のため同時投入可」は run 4 の実測で反証された: 計画時に想定しなかった
  **総合品質軸（ノイズ混入・音量交絡）に両介入が同時に寄与し得る交絡**が発生し、
  観測された劣化を単一介入へ帰属できなくなった
  （`results_s3/s3_record_2026-08-17.md` §5 の帰属限界・Q7）。本論拠を以後の
  run 設計の根拠として**再利用してはならない**。多介入の同時投入は既定で不可とし、
  介入分離または ablation の挿入を Design Memo で個別に裁定すること。

## 0. S2 が確定させた不足（backfill の根拠）

1. **り→ん破綻** = リツ成分残存量の単一要因 → リツ極の歌唱データ不足
   （VCV 録音のみで実歌唱ゼロ）が原因仮説 → **D3**（F1.4 レンダのリツ歌唱合成データ）で埋める
2. **電子音ノイズ** = NSF-HiFiGAN vocoder 由来 → **BigVGAN v2 差し替え**（推論側・Phase E）
3. **補間空間が線分**（2 アンカー）→ **第 3 話者 = User 音源**で三角形化
   （実測 B♭2〜G♯3 = 既存比約 1 オクターブ低・intake 済み 17 本）

## 1. Phase A — 第 3 ドナー正式 intake【完了 2026-08-17】

`recording_kit/intake_records/intake_record_2026-08-17.md` が実施記録の正本。
UC-001〜017 の 17 本を `user_donor_ledger.json` へ記帳（sha256 連鎖 17/17 一致）。
残る User 確認 = UC-003/007 母音対応・UC-001/002 曲同定・UC-017 クリッピング耳確認
（いずれも Phase C の音素割当前に確定させる）。

## 2. Phase B — D3 データ工場（本フェーズの設計）

### 2.1 目的と内容設計

D3 の狙いは**リツ極の「歌唱としての」データ**（持続母音・音高遷移・フレーズ構造）を
供給し、り→ん破綻（長母音サステインの崩壊）を埋めること。音素被覆の拡張は
**リツ VCV 被覆内**に限る（VCV に無い音素は合成でも作れない — ティ/ファ/ヴ系の
被覆は第 3 話者側の責務）。

内容 3 系統（いずれも `singer/score*.py` の `ScoreNote` 形式で決定論定義）:

1. **既存曲**: さくら（`score.py`）・うみ（`score_umi.py`）— S1 ゲートと同型の正曲
2. **サステイン譜（新規）**: 5 母音 × 低→中→高 3 段ロングトーン
   （recording_kit T1 カードの譜面化。り→ん対策の主力）
3. **かな短句譜（新規）**: リツ VCV 被覆内のかな連続（促音・拗音・ん を含む）
   短フレーズ数本（音高遷移＋子音移行の多様性）

### 2.2 量と変動生成

- 目標 **20–30 分**（D1 PJS = 26.86 分 / D2 リツ VCV = 33.4 分と同オーダー）
- 変動軸 = **perf seed のみ**（2026-08-17 B1 実測後の設計変更: spec/render に
  移調機構が無く、音高多様性は `d3_sustain` の 3 段（MIDI 57/62/65）が構造的に
  担うため、移調軸は導入しない。当初案「seed×移調」から変更）
- 1 パス（sakura 24.585s + umi 11.410s + d3_sustain 63.500s + d3_kana 20.535s
  = 120.03s）× seed 10 個 = 40 セル = **20.005 分** → 目標帯下端に入る
  （erratum 2026-08-17: 起草時の umi 27.0s は User 録音の実測長との混同で誤り。
  トイスコアの実長 11.41s に基づく実測合計へ訂正。B2 実測で確認）。
  具体の組は D3 マニフェスト（§2.4）で事前登録する
- 事前検証: `presets/ritsu_neutral.json` ベース（warp ニュートラル）。
  スタイル変奏は今回の目的（データ量×歌唱構造）に不要のため導入しない
- **トリップワイヤ**: seed=11（preset 焼き込み値）の sakura/umi セルは B1
  ベースライン sha256（`833d65d8…` / `9f561fa8…`）とバイト一致しなければならない

### 2.3 実装構成（3 段・すべて Sonnet 委譲/ローカル $0）

- **B1 — render.py の score registry 化 + タイミング export**:
  `--score` の choices 固定を registry 化して新譜を登録可能にする。
  併せて ph/note タイミング（converter が必要とする ph_seq/ph_dur/note_seq/note_dur
  相当）を render 側から export する経路を設ける（render と converter で
  タイミング計算を**二重実装しない**）。
  **AC = 既存 sakura/umi の同一 spec+seed 出力が本環境で変更前後バイト同一**
  （f1_4 record の 3 回一致プロトコル準拠。変更前 sha256 を先に採取してから着手）
- **B2 — D3 コーパス生成**: マニフェスト（§2.4）に従い全セルを render。
  生成後に全 wav の sha256 を記帳
- **B3 — `s1_dataprep/convert_d3.py` 新設**: 入力 = D3 wav + B1 の export タイミング。
  出力 = `transcriptions.csv`（ph_seq/ph_dur/ph_num/note_seq/note_dur）+ `wavs/`
  （44.1kHz リサンプル — render 24kHz との差の解消は本変換器の責務）。
  **既存の `convert_ritsu.py` / `convert_pjs.py` / `build_dataset.py` には触れない**
  （共有部に触れないことで実 PJS 100 曲回帰の発火条件を回避する）。
  AC = `build_dataset.py` の 3 ゲート（validate_speaker / check_ph_dur_duration /
  check_note_dur_consistency）通過

binarize（openvpi/DiffSinger `scripts/binarize.py`）は S1 と同じ分担 =
GPU 実行環境（クロー）側。ローカル AC は build_dataset ゲートまで。

### 2.4 決定論と provenance

- **D3 マニフェスト事前登録**: スコア ID × seed × 移調の全セル列挙を JSON で
  コミットしてから生成する。生成後に各セルの wav sha256 を追記
  （殻→実測の 2 段コミット。捏造なし）
- リツ voicebank は s1_dataprep README の pin
  （zip `88c7b3ef…` 189,261,648 bytes・voicebank_sha256 `86e1c57b…`）で検証してから使用
  （render.py の `_validate_spec_donor` fail-closed に加えて取得時点で照合）
- 話者別データ分離の構成を維持（D3 は spk=ritsu 側に追加。PJS CC BY-SA の
  出力継承リスク管理と同型の会計）

## 3. Phase C — 第 3 話者データ prep（`convert_user.py`）

歌詞既知（カード指定文句）→ 音素列確定、時刻は決定論アラインメント。
受け入れ条件は「単独で強いアンカー」ではなく「spk_embed が立ち、補間三角形の
第 3 頂点として方向が聞き分けられる」。前提の User 確認（UC-003/007・UC-001/002・
UC-017）は **2026-08-17 完了** → 着手可。
新規コードにも rename/move→記帳の禁止（完了判定はファイルシステム状態から導出）を
最初から適用する。

### 3.1 tier 別アラインメント戦略（2026-08-17 設計確定）

- **T1（UC-003〜007・母音のばし）**: RMS ゲートで 3 段の有声区間を切り出し
  （batch2 検査と同手法）、各段 = 単一母音音素。note は段内 f0 中央値の最近傍 MIDI。
  最も確実な帯 — ここだけで 5 母音 × 3 音高の持続データが立つ
- **T2（UC-008〜017・短句）**: 既知歌詞 → モーラ/音素列を確定し、有声ラン境界
  （ブレス/休止ギャップ）をフレーズ区切りとして割当、ラン内はモーラ数比例 +
  子音/母音の定率ヒューリスティックで配分。粗いが AC は「spk_embed が立つ」であり
  精密アラインメントを要求しない（方法を record に正直記載）
- **T0（UC-001/002・通し歌唱）**: 検査実測のフレーズギャップ（A=6 セグメント /
  B=4 セグメント）を score.py / score_umi.py のフレーズ構造へ対応付け、
  フレーズ内はスコアの拍配分比で配分
- **促音っ（T2 歌詞に含まれる）**: s1_dataprep の統合辞書に実在する促音記号
  （DiffSinger 慣行では `cl`）を実装時に一次ソース確認し、**辞書に実在する記号のみ
  emit**。辞書に無い場合はその音素を含む行の扱いを設計へ差し戻す（黙って
  スキップ/代用しない）。§7 Q5 の部分回答
- 出力契約は `convert_d3.py` と同一（transcriptions.csv + 44.1kHz wavs・
  build_dataset 3 ゲート通過）。spk=user の話者別ディレクトリ

## 4. Phase D — run 4 GPU 実行（クロー・RunPod）

構成 = run 3 踏襲（finetune 機構・bf16+clip・LR 0.0002・40K・各 5K 節目 NaN スキャン）
+ datasets に spk3 追加 + D3 追加。早期ゲート 5K/10K → 40K。
費用見積 $2–5（上限 $8 で打ち切り）。ゲート判定材料:
①リツ極再現でり→ん破綻の消長（D3 効果）②spk3 アンカー単独合成（第 3 声の立ち）
③既存 2 アンカーの回帰（S1 ゲート 5 点）④三角形内部の補間バッチ
（S2 と同じブラインド規律・4 候補分割・隠しコントロール）。
**着手は User の費用承認とクロー稼働タイミング待ち**。

## 5. Phase E — vocoder トラック（分離・run 4 の後）

BigVGAN v2 の mel 互換性検証（44kHz vs 現行 24kHz mel — 非互換なら再設計を
run 4 と切り離して判断）→ 互換なら全個体 A/B（電子音ノイズの消長を耳判定）。

## 6. Acceptance Criteria（S3 出口）

- [x] 17 本の台帳記帳完了（source_sha256・カード対応）+ 検査記録の恒久退避（Phase A・2026-08-17）
- [x] D3 データセットが build_dataset ゲートを通過し実効分数が record に記録される（Phase B・`results_s3/d3_dataset_record.md`）
- [x] convert_user.py が動き、User 音源の実効分数・音素被覆が record に記録される（Phase C・`results_s3/user_dataset_record.md`・15/17 採用）
- [x] run 4 が回り、ゲート判定材料 ①〜③ が User に届く（Phase D・2026-08-17。
  **④ は run 5 へ延期 — User 承認 2026-08-17**。理由と判定 =
  `results_s3/s3_record_2026-08-17.md` §3/§5）
- [x] `results_s3/s3_record_2026-08-17.md` 起草（耳判定逐語・D3/spk3 の
  効果帰属・Open Questions）
- [ ] 費用の最終実測値（ダッシュボード値）の s3_record §7 への転記
  （2026-08-17 時点で未受領のため未完了として分離計上）

## 7. Open Questions（User 決裁 / 実測待ち）

- **Q1**: BigVGAN v2 44kHz と現行 24kHz mel の互換性（Phase E で実測）
- **Q2**: run 4 費用承認（見積 $2–5・上限 $8）→ **2026-08-17 User 承認済み**
  （タイミング任意 = 「いつでもいい」。クロー稼働は素材パッケージ完成後に依頼）
- **Q3**: UC-017 再録 → **不要（2026-08-17 User 確認）**。T3（UC-018〜020）の
  追加収録は引き続き任意
- **Q4**: PJS CC BY-SA の出力継承リスク精査のタイミング（リリース判断前で可）
- **Q5**（B1 実測で追加）: 促音「っ」の合成対応。`phoneme_jp.kana_to_morae` が
  未実装 raise・`donor_bank_utau.normalize_mora_kana` が unmapped `"sokuon"`・
  リツ oto.ini に促音専用エイリアス無し、の三重に閉じており B1 スコープ外と
  判定（`score_d3_kana.py` は促音を含めず設計）。対応するなら音素・タイミング
  モデル拡張の独立タスク

## 8. 運用注意（S1/S2 の教訓の適用）

- 委譲プロンプトに検証資産の所在を明記し、回帰実測を省略した完了報告は差し戻す
- rename/move→Python 代入記帳の禁止（完了判定はファイルシステム状態から導出）
- gate_synth / convert_ritsu / convert_pjs / build_dataset に触れる変更は
  canon 回帰 5 本 + 実素材 byte-identity を実測必須（本設計はこれらに触れない構成を選択）
- render.py に触れる変更（B1）は sakura/umi の同環境 byte-identity 回帰を必須とする
- 同一 exp_name への並行 export はしない
- PR #262/#263 follow-up 11 件は S3 実装 PR に同梱可能なら同梱、無理なら別 PR
