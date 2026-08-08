# M2e R6 Session 1 完了報告 — 監査版（設計側レビュー用）

- **作成日**: 2026-08-09 (JST)
- **作成者**: Claw（実行側）
- **対象**: r6 本測定セッション1（`build/m2e/run_r6_session1.sh`）
- **実行期間**: 2026-08-05T03:21:41Z 〜 2026-08-08T17:12:51Z（UTC）
- **方針**: 本レポートは事実と証跡パスのみを記録する。pass/fail・精度値の判定は行わない（設計遵守）。生データは要約せずファイルパスで示す。

---

## Q0. 「19 parallel workers」の意味 —— **(a) 誤記であったことを訂正**

**回答: (a) 誤記。実態は「19 シャードを順次実行、各シャード内は P=2 ワーカー」。**

速報レポート（`build/m2e/M2E_R6_SESSION1_COMPLETION_REPORT.md`）の
"Configuration: 19 parallel workers" は実行側（私）の記述ミス。19 はシャード総数であり、
並列度ではない。是正・再実行の必要なし（実行自体は設計どおり P=2 で行われていた）。

### 証跡

1. **実行スクリプト**: `build/m2e/run_r6_session1.sh`
   - 4行目: `export T_DIRECT=28.4816 T_STEM=138.5850 S=337.4 P=2`
   - シャードループ: `for N in $(seq 0 $((N_SHARDS - 1)))` —— **順次実行**（並列起動なし）
   - 各シャード実行: `--workers "$P"`（= 2）、`OMP_NUM_THREADS=1 MKL_NUM_THREADS=1`
2. **全セルレコードの P（workers）値分布**（全1280レコード機械集計）:
   - `workers=2` : **1280 / 1280 件（distinct count = 1）**
   - 集計出力: `build/m2e/audit_q0_workers.txt`
3. **全シャード run レコードの workers 値分布**（全47レコード）:
   - `workers=2` : **47 / 47 件（distinct count = 1）**
   - 同上ファイルに記録
4. サンプル（任意セルレコード）: `build/m2e/store_A/cell_0007b834a3703609ecf12b6b91d7ea73d91b67838495f51a3b30bf2240158d0a.json`
   - `"workers": 2`、`thread_pinning: {MKL_NUM_THREADS: '1', OMP_NUM_THREADS: '1', torch_num_threads: 1}`（shard run レコード側）

**結論**: 実行形態の記録（decree: P=2）と実態（全レコード workers=2）は一致。
誤記は速報レポートの文言のみ。

---

## Q1. シャード別実行表（全 19 行）

ソース: 各シャードの run レコード JSON（計 47 ファイル、`build/m2e/shard_run_<N>_<UTC>_<rand>.json`）。
runner の再開規則により 1 シャードが複数 run に分かれる（B_session=7200s + hang 上限 600s で run を区切り、同一 shard_id を再実行して再開）。
詳細な run 別内訳（各 run のパス・elapsed・completed）: **`build/m2e/audit_q1_shard_table.txt`**

| shard | run数 | 開始 UTC（初回） | 終了 UTC（最終） | 壁時計合計(s) | 完了 | unavail | trunc | 各run ≤ B+grace(7800s) |
|---|---|---|---|---|---|---|---|---|
| 0 | 1 | 2026-08-05T03:21:41Z | 2026-08-05T05:06:31Z | 6290 | 70/70 | 0 | 0 | ✓ |
| 1 | 1 | 2026-08-05T05:08:06Z | 2026-08-05T06:54:49Z | 6403 | 68/68 | 0 | 0 | ✓ |
| 2 | 1 | 2026-08-05T06:56:21Z | 2026-08-05T08:40:39Z | 6258 | 68/68 | 0 | 0 | ✓ |
| 3 | 1 | 2026-08-05T08:42:12Z | 2026-08-05T10:18:57Z | 5805 | 68/68 | 0 | 0 | ✓ |
| 4 | 1 | 2026-08-05T10:20:32Z | 2026-08-05T12:08:05Z | 6454 | 68/68 | 0 | 0 | ✓ |
| 5 | 2 | 2026-08-05T12:09:38Z | 2026-08-05T15:58:43Z | 13637 | 68/68 | 0 | 0 | ✓ |
| 6 | 3 | 2026-08-05T16:00:27Z | 2026-08-05T22:08:29Z | 21876 | 68/68 | 0 | 0 | ✓ |
| 7 | 3 | 2026-08-05T22:10:12Z | 2026-08-06T04:01:54Z | 20894 | 68/68 | 0 | 0 | **✗（下記逸脱1）** |
| 8 | 3 | 2026-08-06T04:03:41Z | 2026-08-06T09:46:47Z | 20376 | 68/68 | 0 | 0 | ✓ |
| 9 | 3 | 2026-08-06T09:48:34Z | 2026-08-06T15:17:10Z | 19507 | 68/68 | 0 | 0 | ✓ |
| 10 | 3 | 2026-08-06T15:18:54Z | 2026-08-06T20:32:10Z | 18589 | 68/68 | 0 | 0 | ✓ |
| 11 | 3 | 2026-08-06T20:33:53Z | 2026-08-07T02:27:17Z | 20996 | 68/68 | 0 | 0 | ✓ |
| 12 | 3 | 2026-08-07T02:29:05Z | 2026-08-07T07:48:52Z | 18970 | 68/68 | 0 | 0 | ✓ |
| 13 | 4 | 2026-08-07T07:50:43Z | 2026-08-07T14:03:49Z | 22076 | 68/68 | 0 | 0 | ✓ |
| 14 | 3 | 2026-08-07T14:05:33Z | 2026-08-07T19:40:05Z | 19864 | 68/68 | 0 | 0 | ✓ |
| 15 | 3 | 2026-08-07T19:41:48Z | 2026-08-08T01:15:13Z | 19796 | 68/68 | 0 | 0 | ✓ |
| 16 | 3 | 2026-08-08T01:16:57Z | 2026-08-08T06:45:14Z | 19500 | 68/68 | 0 | 0 | ✓ |
| 17 | 3 | 2026-08-08T06:46:57Z | 2026-08-08T12:03:26Z | 18783 | 68/68 | 0 | 0 | ✓ |
| 18 | 3 | 2026-08-08T12:05:09Z | 2026-08-08T17:12:05Z | 18209 | 54/54 | 0 | 0 | ✓ |

- 完了セル合計: 70 + 68×17 + 54 = **1280**
- 総 run 数: **47**、壁時計総計: 304,281s ≒ 84.5h
- 全 run の `session_budget_s=7200.0`、`hang_grace_s=600.0`（各 run レコード内に記録）

### 逸脱 1（境界事象・要設計側判読）
- **shard 7 の第2 run** `build/m2e/shard_run_7_20260806T001238Z_wj54XW.json`:
  `elapsed_seconds = 7800.3006` —— B_session + hang 上限 = 7800.0s を **0.30s 超過**。
  当該 run は completed=48/68 で正常記録を書き、後続 run で再開・完走
  （unavailable=0, truncated=0）。elapsed の計測点が budget 打ち切り処理の
  前後どちらを含むかは実行側では判定せず、事実のみ記録する。
- 他 46 run はすべて 7800.0s 以内。

---

## Q2. 地図と campaign の pin 照合

### shard map sha256 照合 —— 一致（停止案件なし）

| 対象 | sha256 |
|---|---|
| commit 済み `docs/measurements/m2e_2026-08/m2e_r2_shard_map.yaml` | `3e57c1c1c19fda6716920655ea3b7bec8313de7bd06aaf42349714aee2379226` |
| 実行時 pin スナップショット `build/m2e/shard_map_pinned_20260805T032048Z_R7PF8E.yaml` | `3e57c1c1c19fda6716920655ea3b7bec8313de7bd06aaf42349714aee2379226` |
| console preflight 出力（`build/m2e/r6_session1_console.log` 1行目） | `preflight ok: N_SHARDS=19 pinned=3e57c1c1…` |
| 全 47 shard run レコードの `shard_map_sha256` | 同値（スクリプト内検証が全 run で record 側と pin 側の一致を強制。不一致なら fail-closed で停止する設計） |

- commit: `32288aa8`（"M2e r4/r5: … shard map commit (N_shards=19)"）。git status 上、当該ファイルに未 commit 変更なし。

### campaign・校正変数の実使用値と decree の整合

| 変数 | 実使用値 | ソース |
|---|---|---|
| S | 337.4 | `run_r6_session1.sh` 4行目 export |
| T_direct | 28.4816 | 同上（全 run レコードの `t_direct_s=28.4816` と一致） |
| T_stem | 138.5850 | 同上（全 run レコードの `t_stem_s=138.585` と一致） |
| P | 2 | 同上（Q0 で全 1280 セル・全 47 run の workers=2 を確認済み） |
| N_shards | 19 | shard map `n_shards: 19`（8984行目）、preflight 出力、全 run レコード `n_shards: 19` |
| R_max | 21（decree: N_shards=19 ≤ 22 → R_max = 19+2 = 21） | 決裁記録 `docs/measurements/m2e_2026-08/r_max_decision2_2026-08-05.md`。スクリプト preflight が `1 ≤ n_shards ≤ 21` を強制（「R_max rev.8 2026-08-05, design section 8.8」を明記）。N_shards=19 ≤ R_max=21 |
| campaign | `docs/measurements/m2e_2026-08/m2e_campaign.yaml`（schema `m2e-campaign/0.1`、4水準のmanifest/fixturesパス保持） | shard map 内 `campaign_sha256: 5f670db740a2cc44213d70ab4e493001111ccb0594b5abf6b9b69159e103f486` |

- 校正変数の導出元: セッション冒頭の P=2 校正 run（`build/m2e/store_calib_p2/`、
  記録 `build/m2e/calib/shard0_calib_p2.json`、ログ `build/m2e/calib/calib_p2.log`）。
  スクリプト冒頭コメントに「T_*/S は本セッション冒頭の P=2 校正 run (store_calib_p2) から導出済み」と明記。

---

## Q3. env_digest の一致記録 —— distinct count = 1

- 全 **1280** セルレコード: `env_digest = 3a2a9445ac13791a73bb763ed59595e945b71af1db873a925a55eed1e30fca7d`（**distinct count = 1**）
- 全 **47** shard run レコード: 同一値（**distinct count = 1**）
- §8.7 違反シャード: **なし**
- **ソース（集計コマンドと出力）**: `build/m2e/audit_q3_env_digest.txt`
  （Python で全レコードの env_digest を Counter 集計。コマンド本文も同ディレクトリの監査スクリプト出力に含む）
- 参考: 環境ロック `docs/measurements/m2e_2026-08/m2e_env_lock_2026-08-05.txt`

---

## Q4. セッション毎再校正（§8.4 rev.6）の記録 —— 単一連続実行・中間再校正なし（正直記録）

- **セッション（コンテナ/プロセス実体）数: 1**。
  `run_r6_session1.sh` は単一 bash プロセスとして 2026-08-05T03:20:48Z 頃に起動され、
  中断・再起動なしに 2026-08-08T17:12:51Z の ALL SHARDS COMPLETE まで連続実行された。
  - 証跡: console ログ `build/m2e/r6_session1_console.log` に `preflight ok` 行が**1回のみ**
    （プロセス再起動があれば複数回出現する）。mktemp による pin スナップショットも
    `build/m2e/shard_map_pinned_*.yaml` が**1ファイルのみ**。
- **セッション境界での 2 波再校正 + S 再導出: 実施機会なし**（境界が存在しないため）。
  該当する再校正記録ファイル: **なし**（隠さず明記する）。
- セッション**冒頭**の校正（1回）は実施済み:
  - 記録: `build/m2e/calib/shard0_calib_p2.json`（P=2、2026-08-05T00:39:28Z〜02:39:46Z）
  - ログ: `build/m2e/calib/calib_p2.log`、ストア: `build/m2e/store_calib_p2/`
  - 導出値: T_direct=28.4816 / T_stem=138.5850 / S=337.4（スクリプト export と全 run レコードに反映）
  - 地図引き直し: この校正に基づく shard map（N_shards=19）を commit `32288aa8` で確定後、
    本セッションで使用。セッション中の引き直しは発生していない。
- runner 内部の「run 区切り」（B_session=2h ごと、計 47 run）はコンテナ実体の
  切り替わりではなく、同一プロセス・同一環境（Q3: env_digest 単一値が傍証）内の
  再開ループである。§8.4 の「セッション」該当性の解釈は設計側の判読に委ねる。

---

## Q5. store_A とセル台帳の突合 —— 欠落 0・余剰 0・digest 不一致 0

突合スクリプト出力: **`build/m2e/audit_q5_q6_output.txt`** および **`build/m2e/audit_q5_digest_output.txt`**

1. **1:1 対応**（台帳 = commit 済み shard map の cells 1280 件 ↔ `build/m2e/store_A/` の 1280 レコード、
   キー = (entry_id, arm/category, level, repeat_index)）:
   - 欠落（台帳にあり store に無し）: **0**
   - 余剰（store にあり台帳に無し）: **0**
   - 台帳側・store 側とも重複キー: 0（assert で機械検証）
2. **入力 digest 検証**（全 1280 レコード、manifest 経由で実ファイルを再ハッシュして照合）:
   - mix waveform: レコード `audio_sha256` vs `build/m2e/mix/*.wav` 実ハッシュ → **不一致 0 / 1280**
   - 注釈: レコード `annotation_sha256` vs `build/m2e/annotations/*_f0.csv` 実ハッシュ → **不一致 0 / 1280**
   - 再ハッシュした実ファイル数: 360（wav 320 + csv 40）
   - 参照 manifest: `build/m2e/manifest_{p12,p06,p00,m06}.json`
3. **重み digest**: `provenance_extractor_weights_sha256 = fb369944d4feb5964cae189dceba1e554d6471f0be712aad61fc087afaef4a55`（model-full.h5）
   —— 全 1280 レコードで**単一値（distinct count = 1）**
4. 付随確認: `generator_code_sha256` も全 1280 レコードで単一値
   （`5cc0d5f9bba92ce8aa679eeebc32845e7702b6ac8e2bb1f561ba37c37ab965a4`）

（§8.7 再開規則が要求する照合と同型の突合。スクリプト内の run 時検証に加え、
本監査で事後に独立再計算した。）

---

## Q6. セル構成の完全性（census の分母）—— 積の成立を機械集計で確認

集計コマンドと出力: **`build/m2e/audit_q6_output.txt`**（shard map と store_A の両側から独立集計）

| 軸 | 水準数 | 内訳（各水準のセル数） |
|---|---|---|
| clip | 40 | 全 40 clip が各 32 セル（分布 {32: 40}、偏りなし） |
| bed | 2 | Angels-In-Amplifiers-I-m-Alright: 640 / Arise-Run-Run-Run: 640 |
| 水準 (level) | 4 | +12dB: 320 / +6dB: 320 / 0dB: 320 / −6dB: 320 |
| アーム (arm) | 2 | V_remix_real_direct: 640 / V_remix_real_stem: 640 |
| repeat | 2 | repeat_index 0: 640 / 1: 640 |

- **積: 40 × 2 × 4 × 2 × 2 = 1280**。5-tuple (clip, bed, level, arm, repeat) の
  distinct 数 = **1280**（重複・欠落なし）。
- store_A 側の (level, arm, repeat) 集計も同値（`audit_q6_output.txt` 前半）。

---

## 補記: スワップ監視（停止条件関連）

- 監視ログ: `build/m2e/swap_watch_r6s1.log`（60s 間隔、5,517 サンプル、実行全期間をカバー）
- **so（swap-out）≠ 0 のサンプル数: 0 / 5,517**（停止条件に該当なし）
- si（swap-in）は散発的に微小値（最大 18）を観測したが、so=0 のため停止条件外。事実として記録。

---

## 逸脱まとめ

| # | 内容 | 影響 | 状態 |
|---|---|---|---|
| 1 | shard 7 第2 run の elapsed が 7800.30s（B+grace 7800.0s を 0.30s 超過） | 当該 run は正常記録を書き後続 run で完走。unavailable/truncated 0 | 事実記録のみ。判読は設計側 |
| 2 | 速報レポートの「19 parallel workers」は誤記（実態: 19 シャード順次 × P=2） | 実行自体は decree どおり。記録文言のみの誤り | 本監査版 Q0 で訂正済み |
| 3 | セッション境界再校正の記録なし（単一連続実行のため機会が存在せず） | §8.4 rev.6 の該当性判読は設計側 | Q4 に正直記録 |

Q0（実行形態）・Q2（sha256 照合）に停止案件は検出されなかった。

---

## Next Steps（差し替え）

- **正**: **r7 = evaluate（C2/C3 経路・`--eval-cell-store` で store_B 生成）→ census（C5）。
  帯の判定を出せるのは census のみ**。store_A の直接分析は不可。
- evaluate は長時間（実測 10h 級）につき、開始前に**分割・再開計画**を策定して一言添える:
  実行側の現時点の腹案は r6 と同型の「時間予算区切り + 同一ストア再開」方式
  （B_session 相当の区切り・再開時 digest 照合・スワップ監視・進捗 cron）。
  分割粒度と予算値は r7 起動レシピ確定時に設計側へ提示する。

---

## 証跡ファイル一覧（本文で参照した生データ・ログ）

| 種別 | パス |
|---|---|
| 実行スクリプト | `build/m2e/run_r6_session1.sh` |
| console ログ | `build/m2e/r6_session1_console.log` |
| shard run レコード（47件） | `build/m2e/shard_run_<N>_<UTC>_<rand>.json`（stdout 対も同名 `_stdout_`） |
| セルストア | `build/m2e/store_A/`（cell_*.json × 1280） |
| pin スナップショット | `build/m2e/shard_map_pinned_20260805T032048Z_R7PF8E.yaml` |
| commit 済み地図 | `docs/measurements/m2e_2026-08/m2e_r2_shard_map.yaml`（commit 32288aa8） |
| campaign | `docs/measurements/m2e_2026-08/m2e_campaign.yaml` |
| R_max 決裁 | `docs/measurements/m2e_2026-08/r_max_decision2_2026-08-05.md` |
| 環境ロック | `docs/measurements/m2e_2026-08/m2e_env_lock_2026-08-05.txt` |
| 冒頭校正 | `build/m2e/calib/shard0_calib_p2.json`, `build/m2e/calib/calib_p2.log`, `build/m2e/store_calib_p2/` |
| スワップ監視 | `build/m2e/swap_watch_r6s1.log` |
| 監査集計出力 | `build/m2e/audit_q0_workers.txt`, `audit_q1_shard_table.txt`, `audit_q3_env_digest.txt`, `audit_q5_q6_output.txt`, `audit_q5_digest_output.txt`, `audit_q6_output.txt` |
