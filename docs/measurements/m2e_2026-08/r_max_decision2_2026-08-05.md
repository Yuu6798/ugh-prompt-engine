# §8.8 User 決裁記録（第2回） — R_max 改訂（18 → 21）・S 測定方法の明確化

**決裁日:** 2026-08-05（JST）
**決裁者:** User（Yuu）
**根拠条項:** DESIGN_M2e_vremix_real_bed.md §8.8 / §8.4
**形式:** 条件付き事前決裁（数字を添えた決裁・往復 1 回に畳む形式）

---

## 1. P=2 再校正（r2-0 再実行）の実測

実行環境: 4 コア / 16 GB RAM。B_session = 7200 s。P=2。
run: `build/m2e/calib/shard0_calib_p2.json`（2026-08-05 00:39:28 UTC 開始、
pid 3500563、shard record sha256 `0308666948834...`）。
完了 84/86（not_started=2・B_session 到達）、unavailable=0、truncated=0。

### 1.1 劣化判定（§8.3・P=1 基準線 ×1.5）— 合格

| アーム | P=1 基準線 (med) | P=2 実測 (med) | 許容上限 | 判定 |
|---|---|---|---|---|
| direct | 38.3 s | 52.1 s | 57.4 s | ✅ 合格 |
| stem | 207.0 s | 262.2 s | 310.5 s | ✅ 合格 |

swap: `swap_watch_p2.log` 全点 so=0（swap-out ゼロ）。

### 1.2 並列不変性ゲート（§8.3・P=1 vs P=2）— 合格

P=1 store（121 レコード）と P=2 store（84 レコード）の共通セル **41 件全数**で
`clip_row.est_trajectory_sha256` 完全一致・不一致ゼロ。補助照合
（audio_sha256 / annotation_sha256 / est_frame_count / est_voiced_frame_count）も
41/41 全一致。

### 1.3 S の測定方法（案 A・User 決裁）

実装は遅延ロードのため「プール起動〜モデルロード完了」がプール起動ギャップと
各アーム初回セル内ロードに分散する。S は次の 3 項の和として導出（§8.4 rev.8 に明文化）:

| 項 | 実測 |
|---|---|
| プール起動ギャップ（run 開始 00:39:28Z → 初セル開始 00:40:45Z） | 77.0 s |
| 初回 direct セルのロード超過（155.8 s − med 52.0 s） | 103.9 s |
| 初回 stem セルのロード超過（417.1 s − med 260.6 s） | 156.5 s |
| **S** | **337.4 s** |

### 1.4 T_*(P=2) の確定

各アーム初回セル（ロード超過は S に計上済み）を除外し、per-cell 壁時計の平均を
P=2 で割った値（§8.4 の `(壁時計 − S) / (2P)` と等価な per-cell 形）:

| | n | mean（壁時計/セル） | T_* = mean / P |
|---|---|---|---|
| direct | 41 | 56.96 s | **28.4816 s/セル** |
| stem | 41 | 277.17 s | **138.5850 s/セル** |

## 2. 地図生成の結果と条件付き決裁の適用

生成器 `--make-shard-map`（S=337.4, T_direct=28.4816, T_stem=138.585, P=2,
B_session=7200, cap=5782.6, `--cell-store build/m2e/store_A`〔空・除外なし〕）は
**N_shards=19 > R_max=18** で §8.8 の fail-closed 停止を正しく発火した
（2026-08-05 02:5x UTC）。

User は事前に次の条件付き決裁を数字を添えて発行済み:

> S=337.4s と T_*(P=2) で地図を生成し、**N_shards ≤ 22 ならば R_max = N_shards + 2
> として確定**し、そのまま進行してよい。22 を超えたら数字を添えて差し戻し。

N_shards = 19 ≤ 22 につき、**R_max = 19 + 2 = 21** で確定（3 択のうち
「1. R_max を引き上げる」を条件付きで採用——規模は不変・回数だけ増える）。

## 3. 改訂の実装

- `scripts/run_melody_accuracy.py`: `_M2E_R_MAX = 18` → `21`
- `docs/DESIGN_M2e_vremix_real_bed.md` §8.8: `R_max = 18 回` → `21 回`（rev.8）
- `docs/DESIGN_M2e_vremix_real_bed.md` §8.4: S の測定方法の明確化＋セッション毎
  S 再導出手順を rev.8 として追記
- `docs/measurements/m2e_2026-08/HANDOFF.md`: preflight の `1 <= n_shards <= 18`
  検査と R_max(18) 参照を 21 へ更新
- 過去の実測記録・E-ログ中の「R_max = 12 / 18」への言及は当時の事実として原文のまま残す

## 4. 地図の確定（2026-08-05 03:03:30 UTC 生成）

- `m2e_r2_shard_map.yaml` commit（本測定開始前・§8.5）
- **N_shards = 19 / n_cells = 1280 / cap = 5782.6 s**
- shard map sha256: `3e57c1c1c19fda6716920655ea3b7bec8313de7bd06aaf42349714aee2379226`
- stdout dated log: `shard_map_stdout_20260805T030229Z_SAcZtq.txt`
- **旧地図 diff: 旧地図は存在しない（本件が初回の地図 commit）。** §8.5 の
  「再計算のたびに旧地図と新地図の diff を dated 記録する」は次回引き直し以降に適用。
- `--cell-store build/m2e/store_A` は空（本測定初回）につき除外セルなし
  （全 1280 セルのパッキングと同一・E-143 の正規手順どおり）。

## 5. env_digest 確定・lockfile

- env_digest: `3a2a9445ac13791a73bb763ed59595e945b71af1db873a925a55eed1e30fca7d`
  （P=1 store / P=2 store の全レコードで同一値を確認）
- lockfile: `m2e_env_lock_2026-08-05.txt`（`.venv-m2e` の pip freeze +
  Python 3.11.15 + env_digest を記載）を同 PR / commit 群で commit

## 6. 付帯

- B フェーズ解析の生ノート: `build/m2e/calib/b_phase_analysis_2026-08-05_problem.md`
  （S 定義のレイヤーズレ検出 → 本決裁の「案 A」で解決）
- 各セッション開始時の 2 波再校正では T_* と併せて S も同一手順で再導出し、
  地図を未完セルについて引き直す（§8.4 rev.8）。
