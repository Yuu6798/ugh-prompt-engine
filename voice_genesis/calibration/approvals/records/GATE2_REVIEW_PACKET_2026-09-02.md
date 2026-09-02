# Gate 2 レビューパケット — GATE2_C0_FREEZE（2026-09-02）

対象: `voice_genesis/calibration` C0 freeze の実行承認（Gate 2）。
作成者: Claude（サブエージェント、branch `claude/design-based-implementation-653xpl`,
HEAD `9c388b0`, clean tree）。**本パケットはレビュー資料のみであり、承認ファイルの
生成・コミット・push・武装コマンドの実行は一切行っていない。**

---

## §1. Gate 2 が承認する内容

Gate 2（`gate2_c0_freeze.json`）は **`c0_freeze.py` の armed 実行**を解錠する
承認である。武装実行（`--armed` + 環境変数 `VG_CAL_C0_FREEZE_AUTHORIZED=1` +
有効な Gate 2 承認ファイルの 3 要素が揃った状態）は以下を行う:

1. `split_secret` / `render_root_secret`（各 32 bytes、`secrets.token_bytes(32)`）
   を **repo 外**（`VG_CAL_SECRET_DIR`、既定 `~/.vg_cal/secrets/<campaign_id>/`、
   mode 0700/0600）に生成する。
2. `splitter.realize_split()` で 456 行の calibration/selection/holdout split
   を確定する。
3. **repo への書込**: `voice_genesis/calibration/campaigns/<campaign_id>/` に
   `c0_manifest.json` / `realized_split.json` / `ledger.jsonl`（freeze event）
   / `e_use_table.json`（`config/e_use_table_v1.json` のコピー）を staging →
   read-back 検証 → `os.replace` で atomic 公開する（**git commit はしない**。
   ユーザー操作）。
4. Gate 2 承認ファイルの `manifest_core_sha` を **`build_manifest()` が返す
   "core" manifest**（`approvals`/`commitments`/`realized_split`/
   `realized_split_sha`/`campaign_id`/`authorization_nonce` の 6 節を含まない）
   の正規形 sha へ束縛する。この 6 節を除いた core manifest には、
   candidates/generators/tests/schema の path+hash マップ・依存バージョン・
   `frozen_design`（meter_specs/fixture_spec/cost_caps 等）・independence
   ledger・RNG 宣言台帳が含まれる — **つまり `manifest_core_sha` は現在の
   checkout の追跡下ファイル群（99 candidate 実装・7 generator・全
   `tests/*.py` 等）のハッシュを内包する**。

### 重要: 承認前に必ず再確認すべきこと（値の再鮮度）

`manifest_core_sha` と `authorization_nonce` はいずれも **「今この瞬間の
checkout 内容」に依存して変わる値**である:

- `manifest_core_sha` は追跡下 `.py`（candidates/fixtures/generators/tests
  等、`c0_path_inventory.json` が列挙する inventory）のいずれか 1 バイトでも
  変われば変化する。本パケット §2 の dry-run はこのセッション時点
  （HEAD `9c388b0`, clean tree）の値である。
- `authorization_nonce` は `dry_run()` を呼ぶたびに `secrets.token_hex(16)`
  で新規発行される（manifest 内容とは無関係な乱数）。Gate 1/Gate 2 の両
  承認ファイルへ**同じ値**を転記しなければならず（`check_armed(GATE2)` が
  一致検証する）、1 nonce につき armed freeze は 1 回しか成功しない
  （`NONCE_ALREADY_USED`）。

したがって **gate2 承認ファイルを実際に記入・配置する直前に、必ず
`python -m voice_genesis.calibration.c0_freeze`（dry-run）をもう一度実行し、
その時点で報告される `manifest_core_sha` と `authorization_nonce` を転記する
こと**。本パケット §2 の値は「今回の確認では何もブロックしていない」ことの
証跡であり、承認ファイルへ転記してよい確定値ではない（Gate 2 承認と
armed 実行の間に別セッションが `voice_genesis/calibration/` 配下の追跡
ファイルへ触れれば、この値は無効化され `armed_freeze()` は
`MANIFEST_CORE_SHA_MISMATCH` で拒否する — fail-closed なので危険はないが、
承認のやり直しが発生する）。

---

## §2. dry-run 実行結果（verbatim）

```
$ cd /home/user/ugh-prompt-engine && python -m voice_genesis.calibration.c0_freeze
manifest_core_sha: d0163b76b105a72f9f7445995bf0fd2f46ce40cf9b14d18338336c828c416593
campaign_id (if frozen today): RUN10-CAL-20260902-d0163b76
authorization_nonce: b8abcde102ae577410aabc5f3c5da4a6
blocked_codes: []
missing_required_keys: []
gate2.armed: False
gate2.missing_factors: ['cli_flag:--armed', 'env:VG_CAL_C0_FREEZE_AUTHORIZED=1', 'approval_file:approval file not found: /root/.vg_cal/approvals/gate2_c0_freeze.json']
EXIT:0
```

（実行環境: approval dir 既定 `~/.vg_cal/approvals/`、既存の
`gate1_campaign_execution.json` を実際に読み込んだ状態。secret dir /
campaigns dir には一切触れていない — 既定 dry-run は書込を一切行わない。）

### 解釈

- **`blocked_codes: []` / `missing_required_keys: []`**: 期待どおり、
  Gate 1 関連のブロックは一切ない。E_use evidence table
  （`config/e_use_table_v1.json`）の load/validate も違反なし
  （`_check_e_use_table` の結果が `missing_required_keys` に合流する設計だが
  空）。`cost_caps`/`stop_rules` は Gate 1 承認済み（`e_use_bound_accepted:
  true`）のため `"ABSENT:GATE1_NOT_APPROVED"` に落ちていない。
- **残るブロックは Gate 2 承認ファイル不在のみ**: `gate2.armed: False` の
  `missing_factors` は 3 要素すべて未充足（`--armed` 未指定・環境変数未設定・
  承認ファイル不在）を列挙しているが、これは dry-run が `--armed` を渡さず
  実行された当然の結果であり、**「Gate 2 承認ファイルさえ揃えば武装できる」
  という期待どおりの状態**である。Gate 1 起因のブロックは存在しない。
- **委譲された Gate 1 の推奨値がそのまま生きている**ことを確認: 既存の
  `~/.vg_cal/approvals/gate1_campaign_execution.json`
  （`cost_caps={compute:172800, storage:4294967296, budget:20}`,
  `e_use_bound_accepted:true`）がこの dry-run の入力として実際に読まれ、
  manifest の `frozen_design.cost_caps`/`stop_rules` を確定させている
  （直接は verbatim 出力に現れないが、`blocked_codes`/`missing_required_keys`
  が空である事実がこれを裏付ける — Gate 1 未承認なら `cost_caps` セクションが
  `"ABSENT:GATE1_NOT_APPROVED"` になり REQUIRED_BLOCKING で必ず block される
  設計のため）。

**producer/CLI 欠陥: なし。** 期待どおり「Gate 2 不在のみでブロック」の状態。

### pytest 全件（`-m "not slow"`）tail

```
$ python -m pytest voice_genesis/calibration/tests -q -x -m "not slow"
........................................................................ [ 11%]
........................................................................ [ 22%]
........................................................................ [ 33%]
........................................................................ [ 44%]
........................................................................ [ 55%]
........................................................................ [ 66%]
........................................................................ [ 77%]
........................................................................ [ 88%]
........................................................................ [ 99%]
...                                                                      [100%]
651 passed, 11 deselected in 13.69s
```

clean tree（HEAD `9c388b0`, `git status --porcelain` 0 行）で全 651 件 green。

---

## §3. campaign plan 実行結果（verbatim）+ Gate 1 caps 照合

`python -m voice_genesis.calibration.campaign plan` は `--campaign-dir` 必須
（`cli.py` 実装）で、design totals（`workunits.plan_counts()`）は
`fixtures.axes.TOTAL_LOGICAL_CELLS`（456）× `controls.PROBE_REPEATS`（5）他の
**matrix 定数から再導出される値であり campaign dir の内容に依存しない**。
一方 `realized`（split 後の per-stage/per-family 内訳）は実際に凍結された
campaign（`c0_manifest.json` + `realized_split` + ledger）を読める必要がある。

C0 は未凍結のため、`realized` を得るには test 専用ヘルパー
`voice_genesis/calibration/tests/_campaign_fixture.py::build_tiny_campaign()`
を使い、**`subset` に既定の 6 行サンプルではなく `build_matrix()` の
FULL 456 行**を渡して `tmp_path` 配下（本リポジトリ外）にのみ最小限の
frozen campaign 骨格（manifest + inline realized_split + ledger freeze event。
armed_freeze は一切呼ばない・secret はダミー値）を組み立てた。

```
$ python /tmp/.../gate2_full_plan/build_full_campaign.py
full matrix rows: 456
campaign_dir: /tmp/.../gate2_full_plan/work/campaigns/RUN10-CAL-TESTFIXTURE
secret_root: /tmp/.../gate2_full_plan/work/secrets

$ python -m voice_genesis.calibration.campaign plan \
    --campaign-dir /tmp/.../gate2_full_plan/work/campaigns/RUN10-CAL-TESTFIXTURE \
    --secret-dir /tmp/.../gate2_full_plan/work/secrets
{
  "campaign_id": "RUN10-CAL-TESTFIXTURE",
  "campaign_state": "OK",
  "design_totals": {
    "instances_total": 2280,
    "meter_calls_per_implementation": 13680,
    "renders_total": 4560,
    "selection_order_of_magnitude": 100000
  },
  "phases_passed": [
    "PREPARATION_VALID"
  ],
  "realized": {
    "c1_render_instances": 1740,
    "c2_baseline_instances": 1140,
    "c3a_instances": 60,
    "c3b_instances_by_family": {
      "APERIODICITY_GT": 90,
      "FORMANT_GT": 120,
      "IDENTITY_CAUSAL_SWEEP": 120,
      "RESONANCE_GT": 60,
      "TILT_GT": 60,
      "TRANSITION_GT": 60
    },
    "c4_render_instances": 540
  }
}
EXIT:0
```

design_totals は依頼どおり **instances 2,280 / renders 4,560 / meter calls
13,680 per implementation** と厳密一致（`selection_order_of_magnitude:
100000` は §6/§14 の「概ね 10^5 selection call」の桁数宣言、正確な call 数
ではない）。この fixture は本物の `armed_freeze()` を一切呼んでおらず、
`tmp_path` 配下のみに書いた（本リポジトリの `campaigns/` /
`~/.vg_cal/secrets/` には一切触れていない）。

### Gate 1 caps 照合表

| 項目 | 値 | 出典 |
|---|---|---|
| `compute` cap | 172,800 s（= 48 CPU-h） | 実 gate1 承認ファイル（`~/.vg_cal/approvals/gate1_campaign_execution.json`） |
| `storage` cap | 4,294,967,296 bytes（= 4 GiB） | 同上 |
| `budget` cap | 20 | 同上 |
| instances_total（design） | 2,280 | plan 出力 `design_totals.instances_total` |
| renders_total（design） | 4,560 | plan 出力 `design_totals.renders_total`（= C1 render union + C4 render union の設計値。今回 fixture の実 realized 内訳は C1=1,740 + C4=540 = 2,280 render**instance**、1 instance あたり 2 回 fresh-process render のため render 回数は ×2） |
| meter_calls_per_implementation（design） | 13,680 | plan 出力 `design_totals.meter_calls_per_implementation`（= instances_total × (within 3 + fresh 3)） |
| selection_order_of_magnitude | 100,000（桁数宣言） | plan 出力（§6/§14 の桁数記載をそのまま転記した定数、`workunits._SELECTION_ORDER_OF_MAGNITUDE`） |

**caps を秒/bytes/budget 単位の実測見積りへ変換する producer 関数はコード内に
存在しない**（`cost_caps.py` docstring: 「cap 値の生成・実行判断は一切行わない
…実行 Go はユーザー判断」）。`CapCounters`/`check()` は**実行時**の累積消費と
cap を比較するのみで、work-unit 件数から事前の compute-seconds/storage-bytes
を機械的に導出する経路は D1/D2 のどちらにも実装されていない（意図的な範囲外、
バグではない）。したがって上表は「設計値 vs cap」を並べて見せる以上のことは
できない — cap が足りるかどうかの定量判断は、Gate 1 決定記録
（`approvals/records/GATE1_DECISION_RECORD.md` §2）に記載の見積り根拠
（「§14 の『数〜10 CPU 時間/実装』+ selection ≈10^5 call に 3–4 倍マージン」
「renders 概ね 1GB 以下に 4 倍マージン」）へのユーザー自身の再確認に委ねる。
本パケットではこれ以上の数値を新たに計算・提示しない（invented numbers 回避）。

**producer/CLI 欠陥: なし。** ただし運用上の観察: `plan` サブコマンドの
`design_totals` は campaign dir が読み込めなくても（`campaign_state:
"UNAVAILABLE"`）常に出力される。凍結前でも「設計値だけ見たい」用途には使える
反面、今回のように per-family `realized` 内訳を見るには凍結相当の
campaign 骨格が必要 — C0 が実際に凍結されるまで、本物の realized split 内訳
は確認できない設計になっている（`splitter.realize_split` が secret 依存の
HMAC 順位付けを行うため、これは意図された挙動）。

---

## §4. UNDERSPEC-CAL 台帳（全件、`README.md` より逐語抽出）+ レビュー用一言

`README.md` 本文の「UNDERSPEC 台帳」表（`01`–`08`, `B01`–`B12`, `C01`–`C18`,
`D01`–`D11`）に加えて、Phase D2 (`campaign/`) モジュール表の本文中にのみ
インラインでタグ付けされ、台帳の表そのものには再掲されていない `D12`–`D20`
（`campaign/*.py` の設計判断）も、完全性のため本節に含める。

凡例（review question 列）: **A**=このまま受容 / **C**=変更を要求（意見を
記入）。空欄は「未検討」。

### 無印シリーズ（`01`–`08`, Phase A framework core）

| tag | module | 内容 | review question (A/C) |
|---|---|---|---|
| `UNDERSPEC-CAL-01` | `vocab.py` | `IndependenceTier.CROSS_IMPLEMENTATION` の claim ceiling 写像を `ABSOLUTE`（tier が許す上限。実際の到達は gate 判定で下方に絞られる）とした | |
| `UNDERSPEC-CAL-02` | `streams.py` | HKDF `info` のフィールド連結を、区切り文字連結ではなく衝突耐性のある長さ接頭辞（4-byte big-endian）方式で実装した | |
| `UNDERSPEC-CAL-03` | `splitter.py` | stratum 内 largest-remainder の closed-form 導出（`n mod 4` で場合分け）と、SEL/HOLD 端数 tie を stratum 内 HMAC 順位最大行の末尾ニブル偶奇で決める規則 | |
| `UNDERSPEC-CAL-04` | `splitter.py` | family 合計の厳密一致は pairwise swap だけでは原理的に不可能なため、`reason="family_total"` の片道移動を導入し `reason="coverage"` の真の 2 行交換と区別した | |
| `UNDERSPEC-CAL-05` | `tolerance.py` | floor 導出式を「PCM 量子化半ステップ・float eps bound・meter 宣言分解能の `max()`」として機械導出した | |
| `UNDERSPEC-CAL-06` | `splitter.py` | coverage repair の donor 選択に、victim と対称な `_safe_to_remove` 安全性検査を適用した（振動防止） | |
| `UNDERSPEC-CAL-07` | `provenance.py` | ledger `entry_sha` の digest 対象に `payload`+`prev_sha` に加えて `seq` も含めた（chain 上の位置を署名に取り込む保守的選択） | |
| `UNDERSPEC-CAL-08` | `m6_identity.py` | M6 component の識別子型を `vocab.MeterId` に固定した（他 meter の校正 status と直接突合できる一貫性を優先） | |

### Bシリーズ（`B01`–`B12`, fixture matrix 456 行の凍結数値）

| tag | module | 内容 | review question (A/C) |
|---|---|---|---|
| `UNDERSPEC-CAL-B01` | `fixtures/axes.py` | negative control の `TOO_SHORT` (0.02s) / `INVALID_SR` (8000Hz) 具体数値。boundary probe（0.10s/16000Hz）より外側の値とした | |
| `UNDERSPEC-CAL-B02` | `fixtures/axes.py` | targeted interaction の "low-SR/high-SR" を boundary SR 極値 (16000/96000) と定義した（primary 極値のみだと row_id 衝突が生じるため） | |
| `UNDERSPEC-CAL-B03` | `fixtures/axes.py` | FORMANT_GT の confound/boundary/negative 行の `generator_impl` を `cascade` に固定した | |
| `UNDERSPEC-CAL-B04` | `fixtures/axes.py` | RESONANCE_GT / TRANSITION_GT の励起/context 用 F0 を primary domain 中央値 C4 (261.626Hz) に固定した | |
| `UNDERSPEC-CAL-B05` | `fixtures/axes.py` | TRANSITION_GT の 3 severity（discontinuity magnitude）を low=0.15/medium=0.35/high=0.65（無次元）とした | |
| `UNDERSPEC-CAL-B06` | `fixtures/axes.py` | TRANSITION_GT の 2 duration class（join 遷移窓長）を short=5ms/long=50ms とした | |
| `UNDERSPEC-CAL-B07` | `fixtures/axes.py` | IDENTITY_CAUSAL_SWEEP の 4 founder の具体値。primary F0 4 水準 + FORMANT_GT pole set の一部を再利用 | |
| `UNDERSPEC-CAL-B08` | `fixtures/axes.py` | IDENTITY_CAUSAL_SWEEP の 3 trait の generator-unit→物理量換算則（F0: 1unit=5cents、FORMANT_SHIFT: 1unit=pole周波数2%、TILT_SLOPE: 1unit=1dB/oct）を凍結した | |
| `UNDERSPEC-CAL-B09` | `fixtures/axes.py` | F0_CONTROL の第 2 confound anchor（正本は単一 anchor C4@48k のみ明記するが件数検算が 2-anchor 構造を要求）を G4@48k とした | |
| `UNDERSPEC-CAL-B10` | `fixtures/axes.py` | single-anchor 4 family の positive control 用第 2 anchor を、truth core grid 上で A1 と最も対照的な点とした | |
| `UNDERSPEC-CAL-B11` | `fixtures/generators/tilt.py` | TILT_GT の dB/oct slope 定義を `A_k[dB]=slope*log2(k)`（k=1 を 0dB 基準）の 1 定義に凍結した | |
| `UNDERSPEC-CAL-B12` | `fixtures/generators/transition.py` | `duration_class` を 4 join type 全てで「join time を中心とした raised-cosine 遷移窓の物理的長さ」として具現化した（Codex レビュー 2026-09-01 P1 修正: 従来 3 join type は瞬時切り替えで無視していた） | |

### Cシリーズ（`C01`–`C18`, candidates registry / c0_validate 検証規則）

| tag | module | 内容 | review question (A/C) |
|---|---|---|---|
| `UNDERSPEC-CAL-C01` | `candidates/impl/b0_wrappers.py` | 5 つの B0 candidate と harness 関数の配線対応（`F0-B0-CURRENT`→`estimate_f0_hps` 等）を候補名一致から導いた | |
| `UNDERSPEC-CAL-C02` | `candidates/impl/formant_cepstral.py` | M3 formant 系のピーク missing 閾値を「帯域内ピーク 0 個で OUTPUT_MISSING」とした | |
| `UNDERSPEC-CAL-C03` | `candidates/impl/formant_burg.py` | Burg LPC 実装詳細（リサンプラ/preemphasis/窓関数/極選択）を機械的に選んだ | |
| `UNDERSPEC-CAL-C04` | `candidates/impl/resonance_prominence.py` | 包絡平滑化を移動平均（box filter）とした | |
| `UNDERSPEC-CAL-C05` | `candidates/registry.py` | `complexity_rank` を宣言順の 0-based 連番とした（実計算コストの実測値ではない） | |
| `UNDERSPEC-CAL-C06` | `candidates/registry.py` | `M2T-B0-CURRENT-HYBRID` を `INVALID_CIRCULAR`/`ClaimCeiling.NONE` へ割り当てた（専用 tier が閉語彙に無いための代用） | |
| `UNDERSPEC-CAL-C07` | `c0_validate.py` | RECORDED_OR_ABSENT キーの全欠落を REQUIRED_BLOCKING 相当の missing 扱いとした。`WEAK_ENV_LOCK` 降格は §3.2 の 5 項目全てに一律適用 | |
| `UNDERSPEC-CAL-C08` | `c0_validate.py` | RNG 台帳 entry のフィールド名を `{"stream_name": str, "seeded": bool}` に固定した | |
| `UNDERSPEC-CAL-C09` | `candidates/registry.py`, `candidates/impl/aperiodicity.py` | M2A-HARMONIC-RESIDUAL の残差帯域グリッド「0–Nyquist」を実装トークン `broadband` へ写像した | |
| `UNDERSPEC-CAL-C10` | `c0_validate.py` | path+hash 系マップの各エントリを `path(非空)→sha256(64桁hex)` 形状として検証した | |
| `UNDERSPEC-CAL-C11` | `c0_validate.py` | `frozen_design.meter_specs` が全 meter family を網羅することを要求する規則にした | |
| `UNDERSPEC-CAL-C12` | `c0_validate.py` | `independence_ledger` の tier 値検証 + キー集合が registry の凍結 99 candidate_id 全集合と完全一致することを要求した | |
| `UNDERSPEC-CAL-C13` | `c0_validate.py` | `rng_ledger` エントリに `seeded=true` の場合の非空 `public_seed_id` を必須とした | |
| `UNDERSPEC-CAL-C14` | `c0_validate.py` | path+hash 系マップの inventory を版管理済み `c0_path_inventory.json` として機械定義した | |
| `UNDERSPEC-CAL-C15` | `fixtures/generators/resonance.py` | declared `noise_snr_db`/`context` の nuisance 折り込みを較正パス（prominence floor 測定前）へ組み込む式を採用した（Codex レビュー 2026-09-01 P1 修正） | |
| `UNDERSPEC-CAL-C16` | `streams.py`, `c0_validate.py` | `rng_ledger` 記録粒度を family 別 1 stream ∪ `split/hmac` ∪ `split/tiebreak` の 9 stream closed set に固定した | |
| `UNDERSPEC-CAL-C17` | `c0_validate.py` | `frozen_design` 各セクションの完全なネスト鍵集合を module-level frozen 定数として定義した（`*_SPEC_REQUIRED_KEYS` 群） | |
| `UNDERSPEC-CAL-C18` | `c0_validate.py` | `frozen_design` ネスト鍵/`stop_rules` に BOUNDED shape validation（sha256/非空list/非空mapping/非空白str）を追加した。**値の意味論的相互検証（registry/matrix との突合）は armed producer 実装時の別 PR の責務**と明記 | |

### Dシリーズ（`D01`–`D20`, Phase D1/D2 の freeze producer / campaign runner）

| tag | module | 内容 | review question (A/C) |
|---|---|---|---|
| `UNDERSPEC-CAL-D01` | `c0_freeze.py` | `measurement_directory_status` を固定文字列 `"ABSENT:legacy_path=voice_genesis/harness/measure_v3.py"` とした | |
| `UNDERSPEC-CAL-D02` | `c0_freeze.py` | `repo.url` は `git remote get-url origin` 実測、失敗時のみ CLAUDE.md 記載の canonical URL へ fallback | |
| `UNDERSPEC-CAL-D03` | `c0_freeze.py` | path+hash マップの producer 側カテゴリ分類規則をテスト fixture と揃えた（`candidates/`→meter、`fixtures/generators/`→generator、`tests/`→test、他→schema） | |
| `UNDERSPEC-CAL-D04` | `c0_freeze.py` | pyworld wheel hash は常に `ABSENT:wheel_hash_not_recorded`（version は `importlib.metadata` 実測） | |
| `UNDERSPEC-CAL-D05` | `c0_freeze.py` | `sample_format` policy を `common.py`/`formant_burg.py` の実装事実から機械転記した | |
| `UNDERSPEC-CAL-D06` | `c0_freeze.py` | `frozen_design.fixture_spec.<FAMILY>.confound_axes`/`.boundary_probes` を matrix.py の実列挙を二重管理しない粗い軸名宣言に留めた | |
| `UNDERSPEC-CAL-D07` | `c0_freeze.py` | `rng_ledger` を "declaration form" とし `public_seed_id=sha256("declared:"+stream_name)`（secret 由来の実 OKM ではない placeholder） | |
| `UNDERSPEC-CAL-D08` | `c0_freeze.py` | `splitter.realize_split` の stratum 化因子を `("truth_level","boundary_class")` に固定した | |
| `UNDERSPEC-CAL-D09` | `armed_freeze()` | CAMPAIGN_CLOSED **後**に `split_secret` を ledger へ reveal する commit-reveal 運用を新規提案（設計正本は reveal 手順を明記しない。**本 Phase 未実装**） | |
| `UNDERSPEC-CAL-D10` | `c0_freeze.py` | E_use evidence table の既定 path を `config/e_use_table_v1.json`（repo-relative、追跡下）に固定した | |
| `UNDERSPEC-CAL-D11` | `gates.py`, `e_use_table.py` | `EUseEvidenceRow` に 14 列目 `e_use_mode`（`"absolute"`\|`"relative"`、既定 `"absolute"`）を追加した。`"relative"` 行の per-instance 絶対値展開は本拡張の範囲外 | |
| `UNDERSPEC-CAL-D12` | `campaign/measure_stage.py` | `PRIMARY_OUTPUT_FIELD_BY_ALGORITHM_FAMILY` が `MeterOutput.values` の主要スカラーを候補の `algorithm_family` から機械的に定める | |
| `UNDERSPEC-CAL-D13` | `campaign/selection_stage.py` | `truth_value_for_row()` は family の主要 truth スカラーを返す（FORMANT_GT は F1 代表） | |
| `UNDERSPEC-CAL-D14` | `campaign/measure_stage.py` | 単一 writer 境界: worker は stdout 経由で結果を返すのみで ledger には触れず、呼び出し元が直列に `meter_call` event を append する | |
| `UNDERSPEC-CAL-D15` | `campaign/baseline_stage.py` | `tolerance.pooled_dispersion()` のプール粒度を candidate_id 単位とした | |
| `UNDERSPEC-CAL-D16` | `campaign/selection_stage.py` | `build_candidate_criteria()` は実測 record から normalized MAE/bias/q95(AE)（ABSOLUTE）・Kendall tau/隣接反転率（DIRECTIONAL）を集計する | |
| `UNDERSPEC-CAL-D17` | `campaign/cli.py` | `c4-holdout` の E_use 拘束 absolute/directional gate 組立の完全な CLI 配線は D2 infra の範囲外とした（`holdout_stage` の building block はテストで直接検証） | |
| `UNDERSPEC-CAL-D18` | `campaign/holdout_stage.py` | `declared_axes_for_family()` は凍結 `frozen_design.fixture_spec.<FAMILY>.confound_axes` を gate4' invariance 軸/DIRECTIONAL sweep_id 宣言として再利用する | |
| `UNDERSPEC-CAL-D19` | `campaign/state.py` | D2 runner 固有の拡張手続フェーズ `CampaignPhase` を ledger event の `kind` から導出する | |
| `UNDERSPEC-CAL-D20` | `campaign/selection_stage.py` | C3b は全 non-F0 family の選択結果を 1 event（`candidate_space`/`selection_rule`/`selected_candidate`）へ集約する | |

**R シリーズ: リポジトリ内に `UNDERSPEC-CAL-R*` タグは存在しない**
（`README.md`/`IMPLEMENTATION_MAP_v1.md`/`GATE_REVIEW_BRIEF_v1.md` を
`grep -r "UNDERSPEC-CAL-R"` した結果 0 件）。

参考: 上記に加えて Codex レビュー各巡で採用され `IMPLEMENTATION_MAP_v1.md`
に凍結された仕様（`canonical.py` の版管理・`provenance.py` の単一 writer
境界・`gates.py` の DIRECTIONAL resolvability 分解・`splitter.py` の重複
row_id 拒否・`selection.py` の ceiling 階級間裁定・`observables.py` の
`u_rep` singleton 除外・`m6_identity.py` の CLAIM_CRITICAL_SET 全 member
ABSOLUTE 必須化）は正本の一部として実装済みのため、上記 UNDERSPEC 台帳には
数えていない（`README.md` 末尾の脚注どおり）。

---

## §5. 正本への correction 候補（`IMPLEMENTATION_MAP_v1.md` §2.5）

`IMPLEMENTATION_MAP_v1.md` §2.5 は本文中に **4 個**の解釈判断を列挙している
（`U_rep singleton 除外` / `R_ij の単位分解` / `row_id 一意性` /
`M6 all-member rule`）が、`GATE_REVIEW_BRIEF_v1.md` §3.2 はこのうち
「正本の記述を書き換える提案に相当する」3 件のみを「correction 候補」として
切り出している（`row_id 一意性` は正本の曖昧箇所の解釈ではなく実装の
安全側強化——重複 row が計画セルを暗黙に欠落させることへの対策——であり、
性質が異なるため候補一覧から除外されている）。依頼どおり **3 件**として
以下を提示する。

| 候補 | 対象 § | 一行理由 |
|---|---|---|
| **U_rep singleton 除外** | §6/§10.1 | repeat 数 ≥2 の process group のみを `u_rep` の母集団とする。n=1 の range は 0 でなく未定義であり、singleton を含めると range が構造ゼロとして q95 を不当に希釈するため（除外は U_rep を大きくする fail 側の保守的読み） |
| **R_ij の単位分解** | §10.4 | v1.0 の合算式は truth 単位（U_GT/U_num）と output 単位（U_rep/U_proc）を無条件加算しており、construct 単位が異なる候補（例: M2A 系）では無意味になる。truth 側 resolvability と output 側有意性の二連言に分解し、単位可換な construct には v1.0 式も追加で課す（保守性を弱めない） |
| **M6 all-member rule** | §12 | 「CALIBRATED_ABSOLUTE component のみで構成」を部分集合の再構成と読まず、凍結済み CLAIM_CRITICAL_SET の全 member が CALIBRATED_ABSOLUTE のときのみ M6 distance を計算し、それ以外は NOT_EVALUABLE とする（D1・§8・§15 と整合する唯一の読み） |

参考として除外された 4 件目（`IMPLEMENTATION_MAP_v1.md` §2.5 原文）:

> **row_id 一意性（§5/§7）**: fixture matrix バリデーションは件数一致に加えて
> 全 456 行の canonical row_id の一意性を必須とする（重複 row は件数検査を
> 素通りして計画セルを暗黙に欠落させるため）。splitter も入力 row_id 重複を
> 拒否する（Codex レビュー第 3 巡採用）

---

## §6. 承認後に起こること

1. **本パケットのレビュー** — User が §4 の UNDERSPEC 台帳 + §5 の
   correction 候補を確認し、受容 (A) するか変更を要求 (C) するかを判断する。
   変更要求があれば実装側（Codex/Claude）へ差し戻す。
2. **gate2 承認ファイルの記入・配置** — §1 で述べたとおり、配置直前に
   `python -m voice_genesis.calibration.c0_freeze`（dry-run）を再実行し、
   その時点の `manifest_core_sha`/`authorization_nonce` を
   `~/.vg_cal/approvals/gate2_c0_freeze.json`（checkout 外）へ転記する
   （`authorization_nonce` は Gate 1 承認ファイル側にも同じ値を転記し直す
   必要がある — 両者の nonce 不一致は `AUTHORIZATION_REQUIRED`
   (`nonce_mismatch`) で拒否される）。
3. **武装実行** —
   `VG_CAL_C0_FREEZE_AUTHORIZED=1 python -m voice_genesis.calibration.c0_freeze --armed`。
   3 要素（`--armed` + 環境変数 + 有効な Gate 2 承認ファイル）が揃って
   初めて: secret 生成（repo 外）→ `splitter.realize_split()` → staging →
   read-back 検証 → `voice_genesis/calibration/campaigns/<campaign_id>/`
   への atomic 公開（**git commit はしない** — ユーザー操作として残る）。
   いずれかの検証に失敗すれば staging を全削除し secret も残さない
   fail-closed。
4. **Gate 3 承認** — `gate3_seal_acceptance.json`
   （seal 保護水準——事故的 leakage・事後改竄の検出まで、外部鍵管理なしに
   敵対的実行者は防げない——の受容）を配置する。C0 freeze の**後**に
   成立する概念のため C0 manifest には一切含まれない。
5. **D2 runner（`campaign/` CLI）による手続 Gate 進行** — `--armed` +
   `VG_CAL_CAMPAIGN_AUTHORIZED=1` + 有効な Gate 1 承認ファイルが揃うと、
   `c1-fixtures` → `c2-baseline` → `c3a-f0-selection` → `c3b-selection` →
   `unseal`（Gate 3 承認を検証）→ `c4-holdout` → `close` の順に
   ledger 駆動でキャンペーンが進行する。`c1-fixtures` は
   calibration/selection/negative-control 行のみ render し、holdout 行の
   render は `unseal` 後の `c4-holdout` で初めて行われる（§7 leakage 契約）。

---

## 付記: 実行環境・再現コマンド

```bash
cd /home/user/ugh-prompt-engine
python -m voice_genesis.calibration.c0_freeze
python -m pytest voice_genesis/calibration/tests -q -x -m "not slow"
```

full-matrix plan 照合の再現（tmp_path 配下のみに書く。本リポジトリ・
`~/.vg_cal` には一切触れない）:

```python
import sys
sys.path.insert(0, "voice_genesis/calibration/tests")
from _campaign_fixture import build_tiny_campaign
from voice_genesis.calibration.fixtures.matrix import build_matrix
campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=build_matrix())
```

```bash
python -m voice_genesis.calibration.campaign plan \
  --campaign-dir <campaign_dir> --secret-dir <secret_root>
```
