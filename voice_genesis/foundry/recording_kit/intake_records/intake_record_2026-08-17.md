# 第 3 ドナー正式 intake 記録 — S3 Phase A（2026-08-17）

- 実施日 (UTC): 2026-08-17
- 位置づけ: S3 設計書 Phase A（第 3 ドナー正式 intake）の実施記録。
  台帳 = 同ディレクトリ `../user_donor_ledger.json`（`user-donor-ledger/0.1`）
- 受領検査の正本 = 本ディレクトリの `batch1_inspection.md` / `batch2_t1_inspection.md` /
  `batch3_t2_inspection.md`（2026-08-16 実施・前セッションから回収）

## 1. 揮発インシデントと回収連鎖（provenance の注記）

受領検査（2026-08-16）の成果物は前セッションのコンテナ scratchpad にのみ存在し、
セッション交代でコンテナが揮発した。本 intake の入力は以下の連鎖で回収し、
**全 17 本の sha256 が受領検査時の記帳値とバイト同一**であることを確認済み:

1. **batch2/batch3 の 15 本**: User が Drive「音楽サンプル」へ 2026-08-16 に
   アップロード済みだった実体を再取得（サイズ・sha256 全数一致）
2. **batch1 の 2 本 (T0)**: User が本セッションへ直接再送付
   （`donor_A`=0b46ce5c… / `donor_B`=4609617a… — 受領検査記帳値と完全一致。
   再エンコードなし）
3. **検査記録（md/json/sha256sums/received_at/解析 .py）**: 前セッション
   「VoiceGenesis開発継続」をセッション間メッセージで起動し、揮発前に Drive
   `user_donor_evac` フォルダへ退避させて回収

検査再現用の解析スクリプト（`analyze_donor.py` / `analyze_batch2.py` /
`analyze_batch3.py`）はリポジトリの lint 対象を汚さないため本ディレクトリには
同梱せず、Drive `user_donor_evac` にのみ保管する。

## 2. カード対応表（台帳記帳の根拠）

対応は受領検査（batch1 §2 / batch2 §4 / batch3 §3）の判定を正とする。
incoming ファイル名は `UC-xxx_<元アップロード識別子>` 形式で命名し、
元ファイル名の識別子を provenance として保持した。

| card_id | incoming 名 | source_sha256 (先頭 12) | 判定確信度 | 備考 |
|---|---|---|---|---|
| UC-001 | UC-001_80716cf0.mp3 | 0b46ce5ce1a6 | 中 | さくら判定は構造/輪郭照合。DTW 未実施 |
| UC-002 | UC-002_5d74f451.mp3 | 4609617a4207 | 中 | うみ判定は構造/輪郭照合。DTW 未実施 |
| UC-003 | UC-003_ab3c5660.m4a | fbe70b3fa246 | medium | あ（フォルマント距離比 2.7） |
| UC-004 | UC-004_f1f3a966.m4a | 10ce2c03afc3 | high | い |
| UC-005 | UC-005_635d1e27.m4a | e5b4ea15dc6b | high | う |
| UC-006 | UC-006_41c5bc98.m4a | bfe1429e5085 | high | え |
| UC-007 | UC-007_97f9bd01.m4a | 506e8b40bd04 | medium | お（フォルマント距離比 2.3） |
| UC-008 | UC-008_aa2ea9fd.m4a | 831403f5dde8 | high | ASR 照合 |
| UC-009 | UC-009_db84a284.m4a | efcff1980270 | high | ASR 照合 |
| UC-010 | UC-010_6df2a7ca.m4a | b80043469a86 | high | ASR 照合（ヴ行の転写揺れは他候補なし） |
| UC-011 | UC-011_962746ab.m4a | 7368852936ca | high | ASR 完全一致 |
| UC-012 | UC-012_aab88fc2.m4a | 7a0d8ee3d461 | high | ASR 照合 |
| UC-013 | UC-013_ebb16df4.m4a | d4f648ed1c6a | high | ASR 照合 |
| UC-014 | UC-014_a44523eb.m4a | 9a4e913086f0 | high | ASR 照合 |
| UC-015 | UC-015_af57a031.m4a | 34d54ffe27f6 | high | ASR 照合 |
| UC-016 | UC-016_04eb0dc7.m4a | ed1fb5ac8b1f | high | クリッピング 6 サンプル (0.002%)・軽微 |
| UC-017 | UC-017_1ec889e1.m4a | b2aa14257fec | high | クリッピング 167 サンプル (0.042%)・要耳確認 |

被覆: UC-001〜UC-017 を過不足なく 1 回ずつ（重複なし・欠落なし）。
T3（UC-018〜020）は未収録（任意カード）。

## 3. 登録 ≠ 採用（暫定フラグと User 確認事項）

台帳記帳は「録ったものは失わない」ための受領記録であり、学習素材としての
**採用判定ではない**。以下は正式採用（Phase C `convert_user.py` / run 4 投入）前の
User 確認事項として登録し、**2026-08-17 に User が 3 件すべて確認完了**した:

1. **UC-001/UC-002 の曲同定**（確信度=中）: さくら/うみの割当 →
   **User 確認済み（2026-08-17）**。台帳のカード対応を確定とする
2. **UC-003「あ」/ UC-007「お」の母音対応**（確信度=medium）→
   **User 確認済み（2026-08-17）**。対応確定
3. **UC-017 のクリッピング実害**（167 サンプル・0.042%）→
   **User 確認済み（2026-08-17）**・再録不要と判断。現テイクを採用可とする

## 4. 保管配置と復元手順（コンテナ揮発対策）

| 資産 | 恒久保管先 |
|---|---|
| 台帳 `user_donor_ledger.json` | 本リポジトリ（コミット） |
| 受領検査記録 (md/json/txt) | 本ディレクトリ（コミット）+ Drive `user_donor_evac` |
| 原本音源 batch2/3 の 15 本 | Drive「音楽サンプル」（sha256 一致確認済み） |
| 原本音源 batch1 の 2 本 (T0) | Drive「音楽サンプル」（2026-08-17 User 配置・サイズ完全一致確認済み） |
| 正規化 24kHz mono wav 17 本 | 恒久保管なし（台帳 sha256 で pin・下記手順で原本から再生成） |

正規化 wav は MCP 経由の base64 アップロードがコンテキスト規模的に非現実的
（計約 11.5MB）のため Drive 保管せず、原本からの再生成で復元する:
`ffmpeg -y -i <原本> -ac 1 -ar 24000 -sample_fmt s16 <name>.norm24k.wav`
（intake.py の変換コマンドと同一パラメータ。本 intake の実行環境 =
**ffmpeg 6.1.1-3ubuntu5**）。再生成後は
台帳の `sha256` とバイト一致を確認すること。ffmpeg 版差でバイト不一致になった
場合は、一致する版で再生成するか、台帳の該当規約に従い再 intake を設計する
（黙って台帳値を書き換えない）。

将来の append 実行（T3 追加収録等）の前提: intake.py の preflight は out_dir に
既存正規化 wav の実在と sha256 一致を要求する。新しいコンテナでは上記の
再生成 + sha256 照合で out_dir を復元してから append を実行すること。

## 5. 実行手順の記録

```
cd <scratchpad>/intake_run
python <repo>/voice_genesis/foundry/recording_kit/intake.py \
    --incoming-dir incoming \
    --out-dir user_donor_normalized \
    --ledger <repo>/voice_genesis/foundry/recording_kit/user_donor_ledger.json
```

incoming 構築時に全 17 本の sha256 を受領検査記帳値と照合してから実行
（照合ログ = intake_run/verify.txt、揮発性のため結果要約を §6 に転記）。

## 6. 実行結果（2026-08-17 実測）

- exit code 0・**17 件全件を単一バッチで記帳**（部分バッチなし）
- 事前照合: incoming 17 本の sha256 = 受領検査記帳値と **17/17 PASS**
- 事後検証: entries=17・card_id 被覆 missing/extra/dupes すべて空・null フィールドなし・
  正規化 wav 17 本生成・`normalized_path` は相対パスのみ（コンテナ固有絶対パス混入なし）
- 台帳エントリのフィールド: `card_id / source_filename / source_sha256 /
  source_size_bytes / sha256 / normalized_path / duration_sec / sample_rate /
  rms_dbfs / peak_dbfs / received_at / alignment_status`
- アラインメント（音素境界付け）は intake.py のスコープ外（`alignment_status` は
  未処理を示す初期値）— Phase C `convert_user.py` の担当
