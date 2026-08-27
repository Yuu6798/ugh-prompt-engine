# HARNESS-3b extractor spec v1.1（Fable 確定 2026-08-27 — 凍結対象）

**v1 → v1.1 訂正記録（正直会計）**: v1 §2-1 は WAV を 16-bit と仮定したが、E2 の
ヘッダ実測（wav_header_probe_85.json、decode なし）で 85/85 曲が **24-bit**/mono/48000Hz
と判明し、extractor は fail-closed で停止した（正しい挙動）。訂正は入力物理層の
実測事実への追随のみで、channel 意味論・抽出法（WORLD/式）・正規化法は無変更
（裁定 §5 の design revision 事項に該当しない）。**音響抽出は v1 凍結下で一切実施
されていない**ため、「凍結が抽出に先行」の不変条件は保たれたまま v1.1 で再凍結する。
旧 freeze record は superseded として破棄せず残す。

裁定正本 = scratchpad/run9_user_adjudication_pjs_lesson_freeze.md（2026-08-27、以下「裁定」）。
E1 実測 = harness_work/h3b/e1_recon_report.md（照合 2 点 PASS・score 実体あり）。
本ファイル確定版の sha256 を freeze record に記録した後にのみ PJS 音源への抽出を実行する（裁定 §5）。

## 0. score 正本の pin（E1 分岐の確定）

- **score 正本 = `pjsNNN.musicxml`**（score-partwise 3.1、全 100 曲、note 単位 `<lyric>` に
  モーラ埋め込み、divisions/tempo/key/tie 保持）。
- `pjsNNN.mid` は**非消費**と宣言する（musicxml と二重ソースにしない。E1 で musicxml との
  note 数照合は未実施のまま — 消費しないので照合も行わない）。`pjs015.xml`（musicxml と
  byte-identical な配布側重複）も非消費（`.musicxml` パスのみ列挙）。
- `pjsNNN.txt`（key/BPM メタ）・`pjsNNN_speech.wav`・`background_noise/` は非消費。
- 消費入力 = `pjsNNN_song.wav` + `pjsNNN.lab` + `pjsNNN.musicxml` の 3 点/曲、
  training 70 + validation 15 のみ（sealed 15 は列挙自体に入らない — 裁定 §2）。

## 1. channel 構成（裁定 §4 確定 — 変更不可）

採用 5 channel と語彙対応（三系統対応表 — schema 定数化の正本）:

| channel (A: v0.1 §11.2) | (C) extracted_traits | (B) EDUCATION_ALLOWED_CHANNELS |
|---|---|---|
| relative F0 contour | `relative_F0` | `pitch_trajectory` |
| note/mora duration ratio | `duration_ratio` | `phoneme_note_duration_relation` |
| phrase-normalized energy envelope | `energy_envelope` | `dynamics_energy_trajectory` |
| attack timing | `onset_offset` | `timing` |
| phrase-end timing | `onset_offset` | `phrase_end_control` |

- LessonRecord の `extracted_traits` = `["relative_F0", "duration_ratio",
  "energy_envelope", "onset_offset"]`（(C) の部分集合、4 正準名で 5 channel を被覆。
  `release_behavior` は**使用しない** — 裁定 §4 の phrase-end timing 意味論により
  release 系語彙を採らない）。
- advisory 6 channel は抽出・保存・loss・評価・成功判定に使用しない。extractor に
  該当コードパスを実装しない。

## 2. 前処理（決定論規則）

1. WAV 読み込み: RIFF PCM を検査し、**24-bit / mono / 48000 Hz を要求**（E2 ヘッダ
   実測で 85/85 曲がこの形式。不一致のファイルがあれば即停止して報告 — 追加の
   変換規則を発明しない）。実測ヘッダ値は freeze record に転記する。
   24-bit decode は library 依存の曖昧さを避けるため**手動 byte 復号を pin**:
   data チャンクのバイト列を `np.frombuffer(..., dtype=np.uint8).reshape(-1, 3)` で
   3 byte/サンプルに区切り、`v = b0 | (b1 << 8) | (b2 << 16)`（little-endian）を
   int32 で組み立て、`v >= 2**23` のサンプルは `v -= 2**24` で符号拡張。
   float 化 = `x = v.astype(np.float64) / 8388608.0`（2**23。値域 [-1, 1)）。
2. サンプルレート正規化: `scipy.signal.resample_poly(x, up=147, down=160)` → 44100 Hz
   （identity_domain_run9_v1.json の pin 済み規則を再利用）。
3. WORLD F0: `pyworld.harvest(x_44k, 44100, frame_period=FP)`。FP は
   `voice_genesis/foundry/adapter/donor_bank.py` の `FRAME_PERIOD_MS` と同値を採り、
   数値を freeze record に転記する。voiced_mask = f0 > 0。
4. `.lab` パース: 空白区切り 3 列（開始 100ns / 終了 100ns / 音素）→ 秒 = 値 × 1e-7。
   mora グルーピングは `donor_bank_lab.group_lab_to_morae` 同型ロジックを run9 内へ
   独立再実装（svp_rpe / foundry モジュールを import しない既存原則）。
5. musicxml パース（stdlib xml.etree のみ）: `divisions` / `<sound tempo>`（全変化を累積）
   / `<note>`（pitch step/alter/octave, duration, tie, rest, lyric）から、各発音 note の
   記号時刻（秒）と音高（Hz, A440 平均律: `440 * 2**((midi-69)/12)`）を導出。
   tie で連結された note は 1 つの発音 note に併合。lyric を持たない発音 note
   （メリスマ継続）は直前の lyric note に併合し持続を延長。rest は note に数えない。
   tempo 指定が 1 つも無い曲は停止対象（テンポを発明しない）。

## 3. アラインメント（fail-closed）

- score モーラ列 = §2-5 の lyric 併合後 note 列（時刻順）。lab モーラ列 = §2-4 の
  グルーピング結果（`pau` を除く）。
- **総数一致ゲート**: 曲ごとに `len(lab_morae) == len(score_morae)` のときのみ
  1:1 順序対応を採用（`alignment_status: "aligned"`）。不一致の曲は
  `alignment_status: "count_mismatch"` とし、当該曲の全 channel を
  `not_extracted`（理由つき）で記録する — **補間・推測アラインメントを行わない**。
  不一致曲数と song_id は正直会計として bundle と record に明記する。
- phrase 分割の正本 = `.lab` の `pau` 境界（annotation-side、裁定 §4 の
  symbolic/annotation 意味論と整合）。phrase = 連続する非 pau モーラの最大列。
- **per-phrase anchor**: phrase p の offset_p = (lab 先頭モーラ onset 秒) −
  (対応 score モーラ記号 onset 秒)。timing 残差は全て offset_p 控除後の値
  （帰結: 各 phrase 先頭 note の attack 残差は構成上 0 — この事実を spec/record に
  明記する。テンポドリフトを phrase 単位で吸収する設計判断）。

## 4. channel 定義式（v0.1 §11.3 の byte-pin 式の実装解釈 — 凍結）

- **relative_F0**: 各 aligned モーラ i の .lab 時間区間 [t0_i, t1_i) に対し、区間内の
  WORLD フレーム t で `F0_lesson(t) = F0_PJS(t) − F0_score(i)`（Hz の literal 減算 —
  §11.3 式そのまま。単位変換を発明しない）。F0_score(i) = 対応 score note の Hz。
  unvoiced フレーム（f0=0）は値を出さず voiced_mask で記録。pau 区間はチャネル外。
- **duration_ratio**: aligned モーラ i ごとに `(t1_i − t0_i) / score_duration_i`
  （score_duration_i = 併合後 note の記号持続秒）。score_duration_i = 0 は停止対象。
- **energy_envelope**: hop = round(44100 × FP/1000) サンプルの非重畳ブロック RMS
  `E_k = sqrt(mean(block**2))`（numpy のみ）。phrase の .lab 時間範囲内のフレームに
  ついて `phrase_normalize(E)_k = E_k / max(E within phrase)`。max = 0 の phrase は
  `not_extracted`（理由つき）。**corpus 統計は使用しない**（全 channel が per-phrase /
  per-mora 自己正規化・相対量で完結 — 裁定 §2 の training-only 統計要件は
  「corpus-level normalization statistics: NONE」の明示宣言で充足し、空統計を
  「training から算出」と偽装しない）。
- **attack timing**（onset_offset）: aligned モーラ i の
  `(t0_i − score_onset_i − offset_p)` 秒。
- **phrase-end timing**（onset_offset / phrase_end_control）: phrase p の
  `(lab 最終モーラ t1 − score 最終モーラ記号終了秒 − offset_p)` 秒。
  **symbolic/annotation 終了時刻差のみ**。release persistence / terminal mel / HNR /
  vowel drift の音響解析コードパスを持たない（裁定 §4 逐語）。

## 5. バンドル形式（D1 確定）

- training / validation 各 1 本の JSON。内部宣言 `"format": "run9-technique-lesson-bundle/1.0"`。
- 構成: `lesson_record`（`run9-lesson-record/1.0` 準拠、`validate_lesson_record()` PASS
  必須。`extracted_traits` = §1 の 4 正準名。`explicitly_excluded_identity_traits` =
  `IDENTITY_EXCLUDED_TRAIT_VOCAB` 7 項目完全含有。provenance 5 欄 = PJS README 由来の
  事実文字列、不明欄は `<UNRESOLVED_EXTERNAL>`。`rights_manifest` /
  `provenance_manifest` = 実在参照〔裁定 txt / zip・identity pin の所在〕）+
  `rights_status_declaration`（裁定 §3 逐語反映: scoped 承認による技術生成・hash 凍結物
  であり、rights-clean / learning-eligible は R9-G1 Rights Gate 成立まで宣言しない）+
  `channel_vocabulary_map`（§1 の三系統対応表）+ `spec_freeze`（freeze record の
  sha256 参照）+ `split`（"training" / "validation"）+ `songs`（song_id 昇順、
  per-song: alignment_status / phrase 構成 / 各 channel データ or not_extracted 理由）。
- **founder 非依存の単一ファイル**（per-founder 分岐構造なし —
  `identical_lesson_bytes_across_founders` を構造で自明化）。
- 直列化: `json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
  + "\n"` を UTF-8 で書き出し。float は Python repr（丸め加工しない）。
- validation バンドルは training と同一 extractor・同一 freeze で生成し、
  ControlProfile 探索・candidate 選択・停止判断へ feedback しない（学習ループ側契約
  へ転記 — HARNESS-3c 設計入力）。

## 6. 凍結手順（裁定 §5 の機械表現 — 実行順序）

1. 依存確定: pyworld を install し、python / pyworld / numpy / scipy の実測版を記録
   （pyworld が現行 numpy と非互換なら互換組合せを実測で確定し、組合せ全体を記録 —
   その場合も既存 repo 依存を壊さない venv 分離で行う）。
2. extractor 実装（workdir 内 `education_lesson_extractor.py` 単一ファイル）。PJS 音源に
   触れない合成データ unit 検証のみ実施可。
3. **freeze record**（`h3b_freeze_record.json`）を書く: 本 spec 確定版 sha256 /
   extractor コード sha256 / 依存実測版 / FP 数値 / WAV ヘッダ実測値 / metric version
   （= 本 spec の版名 "h3b-extractor-spec/1"）。
4. freeze record 記録**後に初めて** PJS 85 曲への抽出を実行。extractor は起動時に
   spec / 自身のコード sha256 を freeze record と照合してから走る（順序の機械強制）。
5. 独立 2 回実行（別プロセス）で両バンドルの byte 一致を確認（run1/run2 sha256 記録）。
6. 変更規律: 仕様不一致の実装バグ修正 = 同 revision 新 attempt（freeze record を
   作り直し、旧 record は破棄せず記録に残す）。channel 意味論・抽出法・正規化法の
   変更 = design revision 変更として扱い、本 spec では行わない。

## 7. sealed / 情報境界（裁定 §2・D3）

- extractor の対象列挙は split manifest の `row_ids.training` / `row_ids.validation`
  のみから構成（sealed の row_ids はコードパス上、列挙に入らない）。
- sealed 15 曲への decode / 特徴抽出 / lesson 生成 / 統計混入 / 試聴 / preview なし
  （E1 で per-file sha256 + ID 確認は完了済み — 裁定が明示的に許可する範囲）。
- repo 配下への書き込みなし（workdir 完結）。repo 成果物化は後続 PR で bundle sha /
  manifest / builder コードのみ収載し、音源・音源由来の生 WAV バイトは収載しない。
