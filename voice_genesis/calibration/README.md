# voice_genesis/calibration — RUN10-CAL

campaign_id: `RUN10-CAL`

## 目的

VoiceGenesis の meter（M2 spectral tilt / M2 aperiodicity / M3 formants / M4
resonance / M5 transition / M6 identity / F0 control）を、正解付き合成 fixture
上で校正する技術負債返済キャンペーンの基盤コード。設計正本の核心:

> 正解付き世界で測定器自身を試験し、使える範囲・誤差・失敗条件を先に固定して
> から、VoiceGenesis の個体や遺伝を測る。

正本ドキュメント:

- [`DESIGN_VG_METER_CAL_DEBT_v1.0.md`](DESIGN_VG_METER_CAL_DEBT_v1.0.md) —
  VG-METER-CAL-DEBT-DESIGN-v1.0。技術内容の唯一の正本。
- [`IMPLEMENTATION_MAP_v1.md`](IMPLEMENTATION_MAP_v1.md) — 実装マップ
  (Design Memo)。モジュール → 設計正本 §番号の対応表。

## 授権境界（§0）

設計正本は `execution_authorized: false` / `meter_changes_authorized: false` /
`run11_measurement_entry_authorized: false`。本パッケージ（Phase A: framework
core）は **インフラ実装のみ** であり、以下は一切含まない:

- C0 freeze の実行（manifest/registry の凍結・freeze event 記録）
- secret（`split_secret` / `render_root_secret`）の生成・保存
  （全 API は secret を呼び出し側の引数として受け取るのみ。テストはテスト内
  生成のダミー secret を使う）
- 456×5 campaign の実測走行・selection/holdout の実行
- 既存 meter（`voice_genesis/harness/*` 等）の変更
- RUN11 関連の一切

`c0_validate.py`（Phase C）は dry-run 検証のみを行う（書込なし・secret 生成
なし）。武装版 freeze スクリプトは §18 Gate 2 承認後の別 PR。

## モジュール一覧（Phase A: framework core — 実装済み）

| module | 内容 | 正本 § |
|---|---|---|
| `vocab.py` | 全閉語彙（終端 status・手続 Gate・BLOCKED code・missing 理由・independence tier/claim ceiling・evidence class・Domain/Split/MeterId）、`CLAIM_CRITICAL_SET`、`debt_discharged()` / `campaign_closed()` 純導出関数 | §1, §3.3, §4.1, §10.2, §11 |
| `canonical.py` | 独自正規形 `vgcal-canon/1`（sorted keys・最小区切り・NaN/Inf 拒否・float 最短往復・`-0.0`→`0.0` 正規化）+ `row_id` / `manifest_sha` | §7, §3.3 |
| `streams.py` | HKDF-SHA256 (RFC 5869) による RNG stream 分離、`derive_seed`/`derive_generator`、`RngLedger`（secret を記録しない） | §3.3, §7 |
| `splitter.py` | HMAC-SHA256 昇順 → stratum 内 largest-remainder 50/25/25 → family 合計補正 → coverage 制約検査 → 決定的最小 swap 修復 → `RealizedSplitMap` + `verify_split` | §7 |
| `tolerance.py` | pooled dispersion（family×condition class）・`tolerance=max(k·SD,floor)`・floor 機械導出・`UNSTABLE_CELL`・`TOLERANCE_FLOOR_LIMITED` | §6 |
| `observables.py` | 二段 median・`e/AE/RE`・BIAS/MAE/q95(linear)・`U_rep`/`U_proc`（singleton 除外込み）・nuisance `dS`・FDR0/FNR1・failure boundary | §10.1 |
| `gates.py` | threshold budget `M[i]`・ABSOLUTE gate 1/2'/max'/3/4'/5・DIRECTIONAL 単位健全二連言 (+可換時合算式) resolvability。`EUseEvidenceRow.e_use_mode`（`"absolute"`\|`"relative"`、既定 `absolute`。`[UNDERSPEC-CAL-D11]`）: construct 単位の 1 スカラー `e_use_value` が truth 値への相対誤差（例: formant 5%、F0 20 cents）を宣言する場合に使う——per-instance 絶対値展開は `e_use_table.py`/campaign 側の責務、`InstanceMargin.e_use`/`threshold_margin()` は引き続き絶対量のみを消費する | §10.2–10.4 |
| `selection.py` | 族別 lexicographic 選択（丸め後比較）・`select_across_ceilings` ceiling 階級間裁定・`SELECTION_FAILED_CLOSED`。ranking vector は criteria payload が揃っている候補のみに構築し、`eligible=False` 明示 or criteria payload absent のいずれかで ineligible と判定した候補は `(candidate_id, reason)` として `SelectionOutcome.ineligible_candidates` に記録（Codex レビュー 2026-09-01 P1: 従来は全候補に無条件で vector 構築を試み、criteria payload が丸ごと欠けた候補で `ValueError` を送出し fail-closed/ranking パスへ到達できなかった） | §9, §2.6 |
| `status.py` | 終端 status first-match cascade・missing→(status,reason) 一意写像・手続 Gate 単調性検査 | §11, §1 |
| `m6_identity.py` | `u_X[j]`・sum-of-norms pairwise uncertainty・`T_null`・`distinct()`・CLAIM_CRITICAL_SET 全 member ABSOLUTE 必須（部分構成での distance 出力禁止） | §12 |
| `provenance.py` | §13 provenance schema（nested frozen dataclass）+ append-only JSONL ledger（fcntl 排他 + fsync、entry_sha 連鎖、truncated tail / tamper 区別）+ leakage 検査 | §7, §13 |

## fixtures（Phase B — 実装済み）

456 logical cell の明示列挙（IMPLEMENTATION_MAP §2.7 FROZEN spec の機械転記。
行選択の裁量なし）。

| module | 内容 |
|---|---|
| `fixtures/axes.py` | §5.1 primary/boundary 軸水準・§2.7 anchor 水準・family 別 truth 値の凍結定数 |
| `fixtures/matrix.py` | `FixtureRow`（canonical dict → `row_id`）+ `MatrixRow`（+ domain）。family 別 truth core 因子分解・正準 nuisance/boundary/negative 系列の決定的レシピ・per-family targeted interaction 列挙・D2 + §3.3 F0 帯域整合検査による domain 導出・`validate_matrix()` / `assert_no_duplicate_row_ids()` |
| `fixtures/controls.py` | `ControlClass` 閉語彙 + negative control row_id 抽出（`provenance.check_leakage` の `control_row_ids` 用。positive control は truth core 行のため leakage 除外集合には含めない）+ `positive_detection_instances()`（split 内 truth 行から N_pos を instance 数で数える） |
| `fixtures/generators/common.py` | PCM 16-bit 量子化・dBFS gain・20ms cosine ramp・100ms voiced prefix/suffix・transition-adjacent context・declared SNR noise mixing。`finalize()` の適用順序は context→gain→noise: declared gain は context 付加後の完全な assembled waveform 全体へ単一スカラーとして適用する（`make_voiced_tone` も unit peak。Codex レビュー 2026-09-01 P1: 従来は core 単体にのみ gain を適用してから固定振幅 `*0.5` の context トーンを追加しており、voiced-prefix/suffix・transition-adjacent 区間が declared gain と無関係な -6dBFS 相当に固定されていた） |
| `fixtures/generators/{f0_control,formant,tilt,aperiodicity,resonance,transition,identity_sweep}.py` | family 別決定論 generator。FORMANT_GT は resonator code path を共有しない 2 実装（cascade: `scipy.signal.lfilter` 時間領域 / additive: 閉形式 `\|H(f)\|` 周波数領域） |
| `fixtures/determinism.py` | fresh-process byte-identity 検査 → 違反 `BLOCKED_C1_GENERATOR_NONDETERMINISTIC` |

検証済み: 456 行 (48/96/48/72/48/48/96 per family, truth/confound/boundary
sub-count 全一致) / row_id 全 456 一意 / Phase A `splitter.realize_split` を
matrix 全体へ適用すると 228/114/114（family 別も §5.2 の 50/25/25 目標に
厳密一致）。

## candidates（Phase C — 実装済み）

99 候補の宣言的定義（設計正本 §8 + `IMPLEMENTATION_MAP_v1.md` §2.6 が凍結した
パラメタグリッド）+ dry-run C0 manifest 検証器。実測 campaign・selection・
C0 freeze の実行は一切含まない（授権境界は本ファイル冒頭の節を参照）。

| module | 内容 | 正本 § |
|---|---|---|
| `candidates/registry.py` | `ALL_CANDIDATES`（99 件固定）: candidate_id / meter / construct / unit / algorithm_family / parameters / domain / missing_rule / independence_tier / claim_ceiling / complexity_rank / implementation_ref。F0 5・M3 43・M2T 13・M2A 24・M4 5・M5 7・M6 2 | §8, memo §2.6 |
| `candidates/adapter.py` | `MeterAdapter` Protocol・`MeterOutput`（typed missing/ineligible）・共通 fail filter（schema 違反・非有限値の無説明返却・within/fresh-process 不一致・negative control 偽検出・positive control 不発火） | §8 |
| `candidates/impl/f0_pyin.py` | `librosa.pyin` (frame/hop グリッド、fmin=80/fmax=600 固定) | §8 |
| `candidates/impl/formant_cepstral.py` | ケプストラム low-time liftering → 包絡ローカルピーク（M3-CEPSTRAL-POLES、baseline と同族の独立実装） | §8 |
| `candidates/impl/formant_burg.py` | Burg 法 LPC（自前再帰）→ 極抽出。`fs'=2*max_formant_hz` への決定的リサンプル必須（M3-BURG-LPC、唯一の独立 family） | §8 |
| `candidates/impl/tilt_harmonic.py` | 倍音振幅取得を単一方式（最近傍 rFFT ビン + 放物線補間）に凍結。OLS / Theil-Sen 回帰（M2T-HARMONIC-OLS/THEILSEN、K 本未満は縮退せず missing） | §8 |
| `candidates/impl/aperiodicity.py` | HNR-ACF（正規化自己相関ピーク）・harmonic-residual（comb-remove 残差比）・D4C（`pyworld` guarded import、`ModuleNotFoundError` かつ `e.name=="pyworld"` の場合のみ ineligible。ABI/共有ライブラリ破損等の他の import 失敗は re-raise = Codex レビュー 2026-09-01 P2） | §8, §3.3 |
| `candidates/impl/resonance_prominence.py` | 平滑化スペクトル包絡上の `scipy.signal.find_peaks(prominence=...)`（M4-LOCAL-PROMINENCE） | §8 |
| `candidates/impl/transition.py` | wave-discontinuity（短窓 RMS jump）・spectral-flux（frame-to-frame L1/L2 magnitude flux） | §8 |
| `candidates/impl/b0_wrappers.py` | `voice_genesis/harness/measure.py` / `measure_v3.py` の**無改変 import**（既存 `voice_genesis/singer/gate_checks.py` 等と同じ `sys.path` sibling-import パターンを踏襲）。5 つの B0 candidate をここでのみ harness へ配線する | §8 |
| `c0_validate.py` | C0 manifest dry-run 検証: REQUIRED_BLOCKING（§3.1）欠落・hollow（空文字列/空コンテナ）→ `BLOCKED_C0_MANIFEST_INCOMPLETE`（キー存在チェックに加え、path+hash マップの形状・`frozen_design.meter_specs`/`fixture_spec` の全 meter family / 全 7 fixture family 網羅とそれぞれのエントリの完全なネスト鍵集合（`METER_SPEC_REQUIRED_KEYS`/`FIXTURE_SPEC_REQUIRED_KEYS`）・campaign-level セクション（`split_spec`/`selection_spec`/`provenance_spec`/`cost_caps`）のネスト鍵集合・`independence_ledger`/`rng_ledger` のエントリ形状も検証 = Codex レビュー 2026-09-01 P1、`UNDERSPEC-CAL-C17`。`independence_ledger` はさらにキー集合が `candidates.registry.ALL_CANDIDATES` の凍結 99 candidate_id 全集合と完全一致すること、各 entry の tier が registry 宣言 tier と一致することも検証 = Codex レビュー 2026-09-01 追加 P1）。加えて、上記ネスト鍵とトップレベル `frozen_design.stop_rules` は BOUNDED shape validation（`*_hash`/`*_sha256` → 64桁hex sha256、`confound_axes`/`boundary_probes`/`negative_controls`/`stop_rules` → 非空 list、`parameter_grid` → 非空 mapping、`generator_version`/`schema_version` → 非空白 str。値の意味論的相互検証は範囲外・armed producer 実装時に別 PR で対応 = Codex レビュー 2026-09-01 P1、`UNDERSPEC-CAL-C18`）、RECORDED_OR_ABSENT（§3.2）は値または `ABSENT:<理由>` を許容し `WEAK_ENV_LOCK` 降格 annotation、pyworld 特則（§3.3、D4C のみ ineligible・campaign は BLOCK しない）、RNG 台帳の unseeded stream 検出 → `BLOCKED_C0_UNSEEDED_RNG`。**書込・secret 生成・freeze event 記録なし**。§18 Gate 2 承認後の武装版 freeze スクリプトは別 PR（未着手） | §3 |

依存の例外: `candidates/impl/b0_wrappers.py` のみ `voice_genesis/harness/*` を
read-only import する（本ファイル冒頭「テスト」節の「`voice_genesis.harness`
を import しない」は Phase A framework core 各モジュールの依存方針であり、
Phase C の B0 wrapper には適用されない。タスク境界で明示的に許可された唯一の
例外）。`candidates/impl/f0_pyin.py` は `librosa` に依存する
（`IMPLEMENTATION_MAP_v1.md` §1 が宣言する campaign 依存の一部）。

## Phase D — 未武装の実行基盤（D1: freeze producer + 承認 Gate — 実装済み）

§18 の 3 承認 Gate が通ればコード変更なしで C0 freeze と campaign 実行に入れる
状態まで基盤を用意する。本 Phase 自身は freeze も実測も行わない（既定 =
dry-run。武装経路はテストが `tmp_path` 配下の test-local な approval/secret/
campaigns dir に対してのみ実行し、本リポジトリへの実 freeze は行わない =
IMPLEMENTATION_MAP §0）。

| module | 内容 | 正本 § |
|---|---|---|
| `approvals.py` | Gate 1–3 承認ファイル（`VG_CAL_APPROVAL_DIR`、既定 `~/.vg_cal/approvals/`、checkout 外）の loader + shape 検証 + `design_doc_sha256`/`memo_sha256` の実ファイル hash 照合 + 三要素武装判定 `check_armed()`（`--armed` AND 環境変数（gate1/gate3=`VG_CAL_CAMPAIGN_AUTHORIZED=1`、gate2=`VG_CAL_C0_FREEZE_AUTHORIZED=1`）AND 有効な承認ファイル。1 つでも欠ければ `AUTHORIZATION_REQUIRED`）。承認ファイルは `campaign_id` を含まない（PR レビュー第 2 巡: ハッシュ循環回避）。gate1/gate2 は共通 `authorization_nonce` を必須とし、`check_armed(GATE2)` は gate1 が承認済みなら両者の nonce 一致を検証する（不一致 → `AUTHORIZATION_REQUIRED`、理由 `nonce_mismatch`。PR レビュー第 5 巡: 承認の一回性）。gate3 record 型・loader はここに用意するが `c0_freeze.py` はこれを manifest / freeze event のいずれにも埋め込まない（seal 受容は C0 freeze 後に成立する概念のため。D2 runner が別途 `GATE3_ACCEPTED` ledger event で束縛する設計、本 Phase の範囲外）。`check_armed()` は `preloaded=`（`load_all_approvals()` のスナップショット）を受け取れば承認ファイルを再読込しない（PR レビュー第 6 巡 #5: 二重読み排除、`c0_freeze.py` が利用）。**`refresh_document_hashes(approval_path, repo_root)`**（Part A/D1b）: 既存の承認ファイルを再読込し `design_doc_sha256`/`memo_sha256` のみを現在の実ファイルハッシュへ atomic に書き戻す（他フィールド不変、旧/新ハッシュを返す）——メモ編集の都度ハッシュ束縛が無効化されるため、承認者の再承認を代替しない機械的な再スタンプ手段として提供する。CLI: `python -m voice_genesis.calibration.approvals refresh --gate {gate1,gate2,gate3} [--approval-dir] [--repo-root]` | §18, memo §6.1 |
| `e_use_table.py` | `gates.EUseEvidenceRow`（14 列: 必須 13 列 + `e_use_mode`。`[UNDERSPEC-CAL-D11]`）の JSON loader/validator + `generate_template()`（`registry.ALL_CANDIDATES` の一意な `(construct, unit, domain)` タプルごとに 1 行、全て `evidence_class=UNJUSTIFIED` かつ `e_use_value=null`。数値 placeholder なし）+ `auto_ceiling()`（`gates.auto_ceiling_for_unjustified` 委譲）+ `USER_ACCEPTED_USE_BOUND` 行に Gate 1 `e_use_bound_accepted` 承諾を要求する横断検証。実データ = `config/e_use_table_v1.json`（Gate 1 委任決定、`approvals/records/GATE1_DECISION_RECORD.md` 参照） | §10.2, memo §6.3 |
| `cost_caps.py` | `CostCaps`（`compute`/`storage`/`budget` の 3 キー。`c0_validate.COST_CAPS_REQUIRED_KEYS` と一致。単位: 秒/bytes/課金単位）+ `CapCounters` 累積器 + `check()` 超過判定（stop event payload 生成） | §14, §18 |
| `c0_freeze.py` | manifest producer: git HEAD/dirty-tree・path+hash 実測・依存 exact version（pyworld guarded）・sample format policy・`frozen_design`（registry/matrix から機械導出）・independence ledger・RNG 宣言台帳・RECORDED_OR_ABSENT env、を `build_manifest()` でコード生成（"core" manifest。`approvals`/`commitments`/`realized_split`/`realized_split_sha`/`campaign_id`/`authorization_nonce` の 6 節を含まない）。`core_payload()`/`manifest_core_sha()` はこの 6 節を（frozen/full manifest から渡されても）除いてから hash する（PR レビュー第 4/5 巡: 定義の精緻化。dry-run の `manifest_core_sha` は armed 後の frozen manifest から同じ 6 節を除いて再計算した値と一致する）。`campaign_id_for()` = `RUN10-CAL-<YYYYMMDD>-<core_sha[:8]>`。`dry_run()` は書込・secret なしで、呼び出しごとに `authorization_nonce`（`secrets.token_hex(16)`。承認の一回性、PR レビュー第 5 巡）も新規発行して報告する。`armed_freeze()` は `check_armed(GATE2)`（Gate 1/Gate 2 の nonce 一致も検証済み）→ Gate 2 承認の `manifest_core_sha` 一致検証（不一致は "承認は別 manifest 用" として拒否）→ `campaigns_dir` 配下の既公開 manifest を同じ `authorization_nonce` で走査し、ヒットすれば `NONCE_ALREADY_USED` で副作用なく拒否（同一承認ファイルによる再 freeze を防ぐ）→ secret 生成（`secrets.token_bytes(32)` ×2）→ `splitter.realize_split()` → 設計正本 §7「正本は C0 manifest に列挙した実現済み row→split 表」に従い `realized_split` を frozen manifest 本体へインライン（`realized_split.json` は同内容の便宜コピー）→ validation → staging（`campaigns/.staging-<id>/` + `secret_dir/.staging-<id>/`、secret dir 0700・secret file 0600）→ read-back（`validate_c0_manifest`/`verify_split`/`Ledger.verify_chain`）→ **`os.replace` を secret dir → campaign dir の順に固定**（PR レビュー第 2 巡）で atomic 公開。二根公開と `detect_orphans()` は同一 `<secret_dir>/.publish.lock` 排他ロックを共有する: `armed_freeze()` は blocking 取得（公開区間全体を保持）、`detect_orphans()` は **non-blocking** 取得（競合時は何もせず即 return。PR レビュー第 4 巡）。ロックを取得できたこと自体が「生きた公開処理は無い」ことの証明になるため、secret のみ（campaign 対応なし）は取得できた時点で常に stale と判定して削除、secret+campaign が揃うのに `.publishing` マーカーが残る（公開完了・マーカー削除だけ未了）場合はマーカーのみ除去する。campaign 側 rename 失敗時は公開済み secret dir を削除してロールバックし、いずれの失敗でも staging を全削除して secret を残さない。**git commit はしない**（ユーザー操作）。**E_use evidence table**（Part A/D1b, `[UNDERSPEC-CAL-D10]`）: `dry_run()`/`armed_freeze()` は既定 `config/e_use_table_v1.json`（`e_use_table_path` で上書き可）を `e_use_table.load_e_use_table()`/`validate_e_use_table()` で load+validate し、違反は `"e_use_table: ..."` detail 付きで `BLOCKED_C0_MANIFEST_INCOMPLETE` へ合流させる（`gate1_e_use_bound_accepted` は Gate 1 承認の `e_use_bound_accepted` から導出）。armed freeze はこのファイルを `campaigns/<id>/e_use_table.json` へコピーし、sha256 を manifest 非-core 節 `frozen_inputs.e_use_table_sha256` と freeze event の双方へ記録する。**PR レビュー第 6 巡（commit 6494395 対象）で 6 件採用**: (1) CLI dry-run は `authorization_nonce` も表示、(2) `detect_orphans()` は既定 dry-run では呼ばない（`--armed` 経路の認可成立後、または明示 `--maintenance-orphans` のみ。副作用ゼロを維持）、(3) 上記 E_use table 統合、(4) 承認の一回性 nonce 走査は `_publish_lock` 取得前の早期チェックに加え、ロック保持中に権威的再チェックを行う（TOCTOU window を閉じる）、(5) 承認ファイルは `load_all_approvals()` を 1 回だけ呼び、`check_armed(preloaded=...)` へそのスナップショットを渡して二重読みを排除、(6) read-back で `render_root_secret.bin` も長さ・生成値・commitment ハッシュの 3 点を検証してから公開し、`_write_secret_file()` は `os.write()` の短い書込みを検出して例外にする。CLI: `python -m voice_genesis.calibration.c0_freeze [--armed] [--repo-root] [--approval-dir] [--secret-dir] [--campaigns-dir] [--e-use-table-path] [--maintenance-orphans]` | §3, §7, §10.2, §18, memo §6.3 |
| `approvals/README.md` | 承認ファイルの外部配置先（`VG_CAL_APPROVAL_DIR`、既定 `~/.vg_cal/approvals/`）・3 ファイル名・スキーマの説明のみ。承認 json 実体はリポジトリに一切格納しない | memo §6.1 |

`.gitignore` に `**/.vg_cal/`・`voice_genesis/calibration/campaigns/*/renders/`・
`voice_genesis/calibration/campaigns/*/measurements/`・
`voice_genesis/calibration/campaigns/.staging-*/` を追加した。

## campaign（Phase D2 — 未武装の campaign runner、実装済み）

`c0_freeze.armed_freeze()` が公開した凍結 campaign dir を読み込み、手続 Gate
単位（C1 fixtures → C2 baseline → C3a F0 selection → C3b selection → unseal →
C4 holdout → close）でキャンペーンを進行させる runner。本 Phase 自身は C0
freeze・secret 生成・RUN11 関連を一切行わない。既定はサブコマンドごとの
dry-run（`plan`、または `--armed` を渡さない全サブコマンド）であり、
`--armed` + 環境変数 `VG_CAL_CAMPAIGN_AUTHORIZED=1` + 有効な Gate 1 承認
ファイルが揃わなければ `AUTHORIZATION_REQUIRED` で副作用ゼロのまま拒否する。

| module | 内容 | 正本 § |
|---|---|---|
| `campaign/state.py` | 凍結 campaign dir（`c0_manifest.json`/`realized_split`/`ledger.jsonl`）+ `<secret_dir>/<campaign_id>/` の secret 2 ファイルを読み込み検証する `load_frozen_campaign()`（ledger chain 検証・freeze event 先頭確認・commitment sha256 照合・`realized_split_sha` 照合。1 つでも失敗すれば `CampaignStateError`）。`detect_orphans()` と同じ fail-closed 意味論（campaign dir はあるが対応する secret dir が無ければ拒否）をここでも単体適用する。D2 runner 固有の拡張手続フェーズ `CampaignPhase`（`PREPARATION_VALID`/`FIXTURE_VALID`/`BASELINE_AUDITED`/`F0_SELECTION_FROZEN`/`SELECTION_FROZEN`/`UNSEALED`/`HOLDOUT_EXECUTED_VALID`/`CAMPAIGN_CLOSED`）を ledger event の `kind` から導出し、前半 5 値は `vocab.ProcedureGate`/`vocab.procedure_gates_monotonic()` へ委譲して単調性を検査する（`[UNDERSPEC-CAL-D19]`） | §1, §6.4 |
| `campaign/workunits.py` | C1/C4 render work unit（instance = row×probe_index、2 fresh-process render）・C2/C3a/C3b/C4 の測定 instance 集合・meter call work unit（instance×candidate×{within 3, fresh 3}）の決定論的列挙。`plan_counts()` は §6/§14 の設計値（instances 2,280 / renders 4,560 / meter calls 13,680 per implementation）を `axes.TOTAL_LOGICAL_CELLS`/`controls.PROBE_REPEATS` から再導出し、`realized_plan()` は実 realized split 上の内訳を返す | §6, §6.4, §14 |
| `campaign/render_stage.py` + `campaign/_render_worker.py` | C1/C4 render: `render_root_secret` から streams 派生 RNG で 2 回 fresh-process render（`_render_worker.py` を subprocess 起動。`fixtures.determinism.render_row_pcm_hex` の薄いラッパー）し byte 一致を要求、不一致は `BLOCKED_C1_GENERATOR_NONDETERMINISTIC` stop event + fail-closed。一致すれば `renders/<row_id>/<probe>.pcm` + sha256 を書き `render` ledger event を記帳。resume は ledger 記録済み sha と現ファイル sha の一致時のみスキップ、不一致/欠損は stale として fail-closed。C4 は render 前に `provenance.Ledger.check_leakage()` を呼び unseal 前なら `BLOCKED_LEAKAGE` で拒否する | §3.3, §6, §7, memo §6.4 |
| `campaign/measure_stage.py` + `campaign/_measure_worker.py` | meter call: within-process 3 call（プロセス内直接呼び出し）+ fresh-process 3 call（`_measure_worker.py` を subprocess 起動、PCM ファイルを再読込）。`implementation_ref` は `importlib` で解決。単一 writer 境界: worker は stdout 経由で結果を返すのみで ledger には触れず、呼び出し元が直列に `meter_call` event を append する（`[UNDERSPEC-CAL-D14]`）。`cost_caps.check()` の超過で `stop_event` 記帳 + `CostCapExceededError`。`PRIMARY_OUTPUT_FIELD_BY_ALGORITHM_FAMILY`（`[UNDERSPEC-CAL-D12]`）が `MeterOutput.values` の主要スカラーフィールドを候補の algorithm_family から機械的に定める | §6, §8, §14 |
| `campaign/baseline_stage.py` | C2: B0 候補（`candidate_id` に `-B0-` を含む全候補）× CALIBRATION split を実測 → `tolerance.pooled_dispersion()`（プール粒度 = candidate_id 単位、`[UNDERSPEC-CAL-D15]`）→ `tolerance = max(k·pooled_SD, floor)` を候補ごとに導出 → `baseline_audit`（artifact_sha）+ `baseline_audited` event | §6, memo §6.4 |
| `campaign/selection_stage.py` | C3a（F0_CONTROL candidates）→ `f0_selection_frozen` event（unseal chain には参加しない）。C3b（F0_CONTROL を除く各 family、`select_across_ceilings()`）→ `candidate_space`/`selection_rule`/`selected_candidate`（全 non-F0 family の選択結果を 1 event へ集約、`[UNDERSPEC-CAL-D20]`）の 3 前提 event + `baseline_audit`（呼び出し側が C2 の event entry_sha を渡す）を合わせた 4 前提 **entry_sha** 参照を持つ `selection_frozen` event。`build_candidate_criteria()`（`[UNDERSPEC-CAL-D16]`）は実測 record から normalized MAE/bias/q95(AE)（ABSOLUTE）・Kendall tau/隣接反転率（DIRECTIONAL）を集計する。`truth_value_for_row()`（`[UNDERSPEC-CAL-D13]`）は family の主要 truth スカラーを返す（FORMANT_GT は F1 代表） | §7, §9, memo §2.6 |
| `campaign/unseal.py` | §7 の 5 sha 相互参照検査: `selection_frozen` event を見つけ、その 4 前提 sha（entry_sha 参照）が実在し正しい kind を指すことを検証 → Gate 3（`approvals.Gate.GATE3_SEAL_ACCEPTANCE`、`seal_protection_level_accepted`）承認を検証 → `gate3_accepted` event（承認ファイル content sha256 記録）→ `holdout_unseal` event（`provenance.Ledger.check_leakage`/`_verified_holdout_unseal_seq` が権威的に認識する形）。いずれか欠ければ `UnsealError`、ledger には一切追記しない | §7, §18, memo §6.4 |
| `campaign/holdout_stage.py` | C4 gate 判定 + 終端 status cascade。building blocks（`build_instance_margins`/`build_directional_pairs`/`evaluate_absolute_meter`/`evaluate_directional_meter`）は `observables.py`/`gates.py`/`status.py` を呼ぶ薄いラッパーで、合成観測値のみで単体テスト可能。`declared_axes_for_family()`（`[UNDERSPEC-CAL-D18]`）は凍結 `frozen_design.fixture_spec.<FAMILY>.confound_axes` を gate4' invariance 軸/DIRECTIONAL sweep_id 宣言として再利用する。`load_e_use_rows()`/`split_e_use_rows_by_mode()` は `gates.EUseEvidenceRow.e_use_mode`（absolute/relative）で E_use 行を分割する。`render_and_measure_holdout()`/`run_holdout_stage()` は実 audio 経路のオーケストレーション。`HOLDOUT_EXECUTED_VALID` は全 meter の終端 status + reason code をまとめた単一 event | §10, §11, memo §6.4 |
| `campaign/close.py` | `CAMPAIGN_CLOSED` event: 全 `vocab.MeterId` が終端 status を持つことを要求（`vocab.campaign_closed()`）し、`derived.debt_discharged`（`vocab.debt_discharged()` の派生値の写しのみ、宣言フィールド化はしない = D1）を記録。CLAIM_CRITICAL_SET 全 member が CALIBRATED_ABSOLUTE かつ M6 component/E_use が明示供給された場合のみ `m6_identity.m6_distance()` を計算。`reveal_split_secret()`（`[UNDERSPEC-CAL-D09]`、`--reveal-split-secret` 経由のみ）は CAMPAIGN_CLOSED **後**にのみ `split_secret` の commit-reveal event を許可する | §1 D1, §7, §12, memo §6.4 |
| `campaign/cli.py` + `campaign/__main__.py` | `python -m voice_genesis.calibration.campaign <plan\|c1-fixtures\|c2-baseline\|c3a-f0-selection\|c3b-selection\|unseal\|c4-holdout\|close> --campaign-dir ... --secret-dir ... [--armed] [--workers N] [--reveal-split-secret]`。`plan` は常に副作用ゼロで設計値 vs realized split の work unit 件数を報告。他サブコマンドは `--armed` 無しなら当該 stage の計画のみ表示、`--armed` ありなら三要素武装判定（`approvals.check_armed(GATE1)`）→ 未武装なら `AUTHORIZATION_REQUIRED`（副作用ゼロ）→ 武装済みなら該当 stage 関数を実行。`c4-holdout` の E_use 拘束 absolute/directional gate 組立の完全な CLI 配線は本 D2 infra の範囲外とした（`[UNDERSPEC-CAL-D17]`。`holdout_stage.evaluate_absolute_meter`/`evaluate_directional_meter` は実 gate 配線込みでテストで直接検証する） | §18, memo §6.1, §6.4 |

## UNDERSPEC 台帳

設計正本が数値・グリッド・エンコーディングを確定していない箇所について、
実装マップ §3 の規約（最も単純で安全な選択を採り docstring にタグ記録）に
従って導入した判断の一覧。C0 freeze 承認時のユーザーレビュー対象であり、
コードが設計正本を上書きするものではない。

| tag | module | 内容 |
|---|---|---|
| `UNDERSPEC-CAL-01` | `vocab.py` | `IndependenceTier.CROSS_IMPLEMENTATION` の claim ceiling 写像を `ABSOLUTE`（tier が許す上限。実際の到達は gate 判定で下方に絞られる）とした |
| `UNDERSPEC-CAL-02` | `streams.py` | HKDF `info` のフィールド連結を、区切り文字連結ではなく衝突耐性のある長さ接頭辞（4-byte big-endian）方式で実装した |
| `UNDERSPEC-CAL-03` | `splitter.py` | stratum 内 largest-remainder の closed-form 導出（`n mod 4` で場合分け）と、SEL/HOLD 端数 tie（構造的に常に同値）を stratum 内 HMAC 順位最大行の末尾ニブル偶奇で決める規則 |
| `UNDERSPEC-CAL-04` | `splitter.py` | family 合計の厳密一致は pairwise swap だけでは原理的に不可能なため、`reason="family_total"` の片道移動を導入し `reason="coverage"` の真の 2 行交換と区別した |
| `UNDERSPEC-CAL-05` | `tolerance.py` | floor 導出式を「PCM 量子化半ステップ・float eps bound・meter 宣言分解能の `max()`」として機械導出した |
| `UNDERSPEC-CAL-06` | `splitter.py` | coverage repair の donor 選択に、victim と対称な `_safe_to_remove` 安全性検査を適用した（同一行が違反間を往復し repair が収束しない振動を防ぐ） |
| `UNDERSPEC-CAL-07` | `provenance.py` | ledger `entry_sha` の digest 対象に `payload`+`prev_sha` に加えて `seq` も含めた（chain 上の位置を署名に取り込む保守的選択） |
| `UNDERSPEC-CAL-08` | `m6_identity.py` | M6 component の識別子型を `vocab.MeterId` に固定した（他 meter の校正 status と直接突合できる一貫性を優先） |
| `UNDERSPEC-CAL-B01` | `fixtures/axes.py` | negative control の `TOO_SHORT` (0.02s) / `INVALID_SR` (8000Hz) 具体数値。boundary probe（0.10s/16000Hz）より外側の値とし、boundary 探査点と negative 探査点を区別した |
| `UNDERSPEC-CAL-B02` | `fixtures/axes.py` | targeted interaction の "low-SR/high-SR" を boundary SR 極値 (16000/96000) と定義した。primary SR 極値 (24000/48000) のみで表現すると、anchor F0 が既に primary 低域にある family（FORMANT_GT 等）で interaction 行が truth core anchor 行と完全一致する退化（row_id 衝突）が生じるため |
| `UNDERSPEC-CAL-B03` | `fixtures/axes.py` | FORMANT_GT の confound/boundary/negative 行（truth core 以外）の `generator_impl` を `cascade` に固定した（implementation は truth core でのみ因子として直積される） |
| `UNDERSPEC-CAL-B04` | `fixtures/axes.py` | RESONANCE_GT / TRANSITION_GT（truth core に F0 を因子として持たない family）の励起/context 用 F0 を primary domain 中央値 C4 (261.626Hz) に固定した |
| `UNDERSPEC-CAL-B05` | `fixtures/axes.py` | TRANSITION_GT の 3 severity（設計正本が「C0 表で固定」として据え置く discontinuity magnitude）を low=0.15/medium=0.35/high=0.65（無次元、join_type ごとに解釈）とした |
| `UNDERSPEC-CAL-B06` | `fixtures/axes.py` | TRANSITION_GT の 2 duration class（join 遷移窓長。primary "duration" 軸のクリップ全長とは別概念）を short=5ms/long=50ms とした |
| `UNDERSPEC-CAL-B07` | `fixtures/axes.py` | IDENTITY_CAUSAL_SWEEP の 4 founder（distinct F0/formant-set/tilt parameter bundle）の具体値。primary F0 4 水準 + FORMANT_GT pole set の一部を再利用し内部一貫性のある bundle を凍結した |
| `UNDERSPEC-CAL-B08` | `fixtures/axes.py` | IDENTITY_CAUSAL_SWEEP の 3 trait の generator-unit→物理量換算則（F0: 1 unit=5 cents、FORMANT_SHIFT: 1 unit=pole周波数 2% scale、TILT_SLOPE: 1 unit=1 dB/oct）を凍結した |
| `UNDERSPEC-CAL-B09` | `fixtures/axes.py` | F0_CONTROL の第 2 confound anchor（設計正本 §2.7 の anchor 一覧は単一 anchor C4@48k のみ明記するが、confound 件数検算「F0 24=11+6+7」は FORMANT_GT/IDENTITY と同型の 2-anchor 構造を要求する）を G4@48k（F0 のみ変更）とした |
| `UNDERSPEC-CAL-B10` | `fixtures/axes.py` | single-anchor family（TILT/APERIODICITY/RESONANCE/TRANSITION）の positive control 用第 2 anchor（§2.7 control 共有契約は「2 anchor truth rows per family」を要求するが、これら 4 family の anchor 一覧は単一 anchor のみ明記）を、truth core grid 上で A1 と最も対照的な点（primary sweep 軸の反対端）とした |
| `UNDERSPEC-CAL-B11` | `fixtures/generators/tilt.py` | TILT_GT の dB/oct slope 定義を `A_k[dB]=slope*log2(k)`（高調波次数 k の log2、`k=1` を 0dB 基準）の 1 定義に凍結した（回帰ルーチンは使わず、meter 側の tilt 推定と非共有） |
| `UNDERSPEC-CAL-B12` | `fixtures/generators/transition.py` | `duration_class`（short=5ms/long=50ms, `UNDERSPEC-CAL-B06`）を 4 join type（amplitude-step/phase-jump/spectral-envelope-switch/crossfade）全てで「join time を中心とした raised-cosine 遷移窓の物理的な長さ」として具現化した（Codex レビュー 2026-09-01 P1: 従来 crossfade 以外の 3 join type は瞬時切り替えで `duration_class` を無視しており、同一 severity の short/long 行が byte-identical に render されていた） |
| `UNDERSPEC-CAL-C01` | `candidates/impl/b0_wrappers.py` | 5 つの B0 candidate と harness 関数の配線対応（`F0-B0-CURRENT`→`estimate_f0_hps`、`M3-B0-CURRENT-CENTROID`/`M4-B0-CURRENT-CENTROID`→ともに `formant_centroid_and_f1`（M3/M4 で同一の現行 centroid 実装を診断的に再利用）、`M2T-B0-CURRENT-HYBRID`→`source_tilt_v2`（regression/h1h2 の unit 混在そのものが「HYBRID」の実体。`spectral_tilt_db_per_oct` は legacy 参照値として diagnostics に同梱）、`M2A-B0-AUTOCORR-PERIODICITY`→`hnr_db_approx`）を設計正本の候補名一致から導いた |
| `UNDERSPEC-CAL-C02` | `candidates/impl/formant_cepstral.py` | M3 formant 系のピーク missing 閾値を「帯域内ピーク 0 個で OUTPUT_MISSING」とした（M2T の「K 本未満は missing」に相当する明記が M3 にはないため、最も単純な閾値を採用） |
| `UNDERSPEC-CAL-C03` | `candidates/impl/formant_burg.py` | Burg LPC 実装詳細（リサンプラ=`scipy.signal.resample_poly`（有理比を `Fraction` で厳密化）、preemphasis=1 次ハイパス時定数として `preemph_hz` を解釈、窓関数=Hamming、極選択=単位円内・虚部正・周波数昇順）を機械的に選んだ |
| `UNDERSPEC-CAL-C04` | `candidates/impl/resonance_prominence.py` | 包絡平滑化を移動平均（box filter、帯域幅 Hz→ビン数換算）とした（設計正本は平滑化アルゴリズムそのものは規定しない） |
| `UNDERSPEC-CAL-C05` | `candidates/registry.py` | `complexity_rank` を「本モジュール内の宣言順（B0→§8 記載順の family→grid 軸宣言順）の 0-based 連番」とした（meter family 内で一意な全順序を与える最も単純な規則。実計算コストの実測値ではない） |
| `UNDERSPEC-CAL-C06` | `candidates/registry.py` | `M2T-B0-CURRENT-HYBRID`（「そのままでは INVALID」）を vocab 4-tier のうち意味が最も近い `INVALID_CIRCULAR`/`ClaimCeiling.NONE` へ割り当てた（真の circular ではなく unit/construct 不一致が実体。専用 tier が閉語彙に存在しないための代用） |
| `UNDERSPEC-CAL-C07` | `c0_validate.py` | RECORDED_OR_ABSENT（§3.2）キーが manifest に全く存在しない場合を REQUIRED_BLOCKING と同様の missing 扱いとした（「値または `ABSENT:<理由>` を必須記録」の「必須記録」を文字通り読んだ）。`WEAK_ENV_LOCK` 降格 annotation は §3.2 の 5 項目全てに一律適用する（設計正本の明示例は container/image digest の 1 件のみ） |
| `UNDERSPEC-CAL-C08` | `c0_validate.py` | RNG 台帳 entry のフィールド名を `{"stream_name": str, "seeded": bool}` に固定した（設計正本は entry の具体的なフィールド名までは規定しない） |
| `UNDERSPEC-CAL-C09` | `candidates/registry.py`, `candidates/impl/aperiodicity.py` | M2A-HARMONIC-RESIDUAL の残差帯域グリッド「0–Nyquist」を実装トークン `broadband`（D4C 側の帯域トークンと統一）へ写像した |
| `UNDERSPEC-CAL-C10` | `c0_validate.py` | path+hash 系マップ（`candidates.*_paths_sha256`）の各エントリを `path(非空文字列) -> sha256(64 桁小文字 16 進)` 形状として検証した（Codex レビュー 2026-09-01 P1） |
| `UNDERSPEC-CAL-C11` | `c0_validate.py` | `frozen_design.meter_specs` が `candidates.registry.ALL_CANDIDATES` の全 meter family（vocab.MeterId 全件）をカバーすることを要求し、欠落 meter を個別キーとして列挙する規則にした（Codex レビュー 2026-09-01 P1） |
| `UNDERSPEC-CAL-C12` | `c0_validate.py` | `independence_ledger` の各エントリ値を `vocab.IndependenceTier` の閉語彙メンバーであることまで検証した（Codex レビュー 2026-09-01 P1）。加えて、ledger のキー集合は `candidates.registry.ALL_CANDIDATES` の凍結 99 candidate_id 全集合と完全一致（欠落・unknown/extra を個別列挙）、各 entry の tier は registry 宣言 tier と一致（不一致を個別列挙）することを要求する（Codex レビュー 2026-09-01 追加 P1: 従来はキー集合の網羅性を一切検査していなかった） |
| `UNDERSPEC-CAL-C13` | `c0_validate.py` | `rng_ledger` エントリの形状を `{"stream_name": str(非空), "seeded": bool}` に加え、`seeded=true` の場合は非空 `public_seed_id`（`streams.RngLedgerEntry.public_seed_id` と命名を揃えた seed 参照）を必須とした（Codex レビュー 2026-09-01 P1: §3.3「stream 列挙 + seed 参照」の反映） |
| `UNDERSPEC-CAL-C14` | `c0_validate.py` | path+hash 系マップ（`candidates.*_paths_sha256`）4 カテゴリの合併集合が要求すべき inventory を、版管理されコミットされた閉じた inventory ファイル `c0_path_inventory.json`（`calibration_path_inventory()` が parse-strict に読む。public 関数として freeze script 再利用可）として機械定義した（Codex レビュー 2026-09-01 P1: 従来は supplied entries のみを検証しファイル省略・phantom path 混入を検出できなかった。P1 #2 改訂: inventory 自体を検証対象 checkout への `rglob` から算出していたのは circular だったため、版管理済み JSON へ切替。実ツリーとのドリフト検知は `scan_calibration_tree_inventory()` + `tests/test_c0_path_inventory_sync.py` が別途担う） |
| `UNDERSPEC-CAL-C15` | `fixtures/generators/resonance.py` | declared `noise_snr_db` の nuisance noise を prominence 較正の floor 測定より前に解析的に折り込む式（pre-gain スケールでの SNR 式適用。gain は mixed signal 全体への単一スカラー倍のため相対 dB 比を保存する）を採用した（Codex レビュー 2026-09-01 P1: 従来 `common.finalize()` が較正後にこの noise を加えており、noise 軸 confound 行で実現 prominence が declared 値を下回っていた）。同様に `context`（cosine-ramp/voiced-prefix-suffix/transition-adjacent）も較正パスへ折り込み、連結型 context は "steady" core 区間に測定窓を限定した（Codex レビュー 2026-09-01 P1） |
| `UNDERSPEC-CAL-C16` | `streams.py`, `c0_validate.py` | C0 の `rng_ledger` 記録粒度を「family ごとの generator render stream 1 個 (`streams.stream_name()` が purpose を `"<family>/render"` へ折り畳む) ∪ `"split/hmac"` ∪ `"split/tiebreak"`」の 9 stream closed set に固定した（`streams.expected_rng_stream_names()` が code-derived に導出。row/probe 単位の実 HKDF 導出はこの per-family stream の sub-derivation として個別列挙しない）。`c0_validate._check_rng_ledger_closed_set` がこの closed set との厳密一致（欠落・unknown/extra・重複を個別列挙）を要求する（Codex レビュー 2026-09-01 P1 finding #2: 従来は entry 形状のみを検証し stream 集合が閉じているかを一切見ていなかった） |
| `UNDERSPEC-CAL-C17` | `c0_validate.py` | `frozen_design` の各セクションが持つべき完全なネスト鍵集合を module-level frozen 定数として定義した: 「meter 別 construct/unit/domain/algorithm family/有限 parameter grid/baseline/fallback/missing・failure rule」を `METER_SPEC_REQUIRED_KEYS`、「fixture family・generator version/hash・known-truth field・confound 軸・boundary probes・negative controls」を `FIXTURE_SPEC_REQUIRED_KEYS`（`fixtures.axes.FixtureFamily` の全 7 family をキーとする mapping、`meter_specs` と対称な網羅性検査を追加）、「split・seed・seal」を `SPLIT_SPEC_REQUIRED_KEYS`、「selection rule・tie rule・candidate exhaustion rule・holdout FAIL 後の固定 outcome」を `SELECTION_SPEC_REQUIRED_KEYS`（旧 `frozen_design.selection_rule` を `selection_spec` へ改名・4 項目に拡張）、「provenance schema・artifact layout」を `PROVENANCE_SPEC_REQUIRED_KEYS`、「cost cap」を `COST_CAPS_REQUIRED_KEYS`（compute/storage/budget の 3 次元に固定）とした。`stop_rules` は `frozen_design` の新規 REQUIRED_BLOCKING キーとして追加したが、設計正本がネスト構造を規定しないため非空チェックのみとした（Codex レビュー 2026-09-01 P1: `fixture_spec={"family": "F0_CONTROL"}` のような hollow な placeholder manifest が、メータ ID 網羅性チェックのみでは通過してしまっていた） |
| `UNDERSPEC-CAL-C18` | `c0_validate.py` | `frozen_design` ネスト鍵（`meter_specs`/`fixture_spec`/`split_spec`/`selection_spec`/`provenance_spec`/`cost_caps` 配下）と `stop_rules` に BOUNDED shape validation を追加した: フィールド名が `*_hash`/`*_sha256` で終わるものは bare 64 桁小文字 16 進 sha256 文字列、`confound_axes`/`boundary_probes`/`negative_controls`/`stop_rules` は非空 list、`parameter_grid` は非空 mapping、`generator_version`/`schema_version` は非空白 str であることを機械的に要求する規則をフィールド名の命名規則から導出した（設計正本 §3.1 はネストしたリーフ値の型までは規定しないため）。**値の意味論的相互検証（registry/matrix との突合）は本 validator の範囲外とし、armed C0 freeze producer 実装時（§18 Gate 2 承認後の別 PR）の責務とする設計判断**（Codex レビュー 2026-09-01 P1: `generator_hash="not-a-hash"`・`confound_axes="x"`・`parameter_grid=1` のような、非 hollow だが型として明らかに壊れた scalar 値が従来の存在/hollow チェックのみでは通過してしまっていた） |

| `UNDERSPEC-CAL-D01` | `c0_freeze.py` | `measurement_directory_status` を `tests/test_c0_validate.py` の慣例と同一の固定文字列 `"ABSENT:legacy_path=voice_genesis/harness/measure_v3.py"` として producer 側にも転記した |
| `UNDERSPEC-CAL-D02` | `c0_freeze.py` | `repo.url` は `git remote get-url origin` を実測し、取得失敗時のみ CLAUDE.md 記載の canonical URL へ fallback する |
| `UNDERSPEC-CAL-D03` | `c0_freeze.py` | path+hash 系マップの producer 側カテゴリ分類規則を `tests/test_c0_validate.py::_classify_path` と同一（`candidates/`→meter、`fixtures/generators/`→generator、`tests/`→test、他→schema）に揃えた（`c0_validate.py` はカテゴリ単位の完全性を要求しないため、テスト fixture との整合性のみを目的とする選択） |
| `UNDERSPEC-CAL-D04` | `c0_freeze.py` | pyworld wheel hash は本環境から安価に取得できないため常に `ABSENT:wheel_hash_not_recorded`（pyworld version は `importlib.metadata` で実測、未インストールなら `ABSENT:not_installed`） |
| `UNDERSPEC-CAL-D05` | `c0_freeze.py` | `sample_format` policy を `fixtures/generators/common.py` の実装事実（`quantize_pcm16` の最終 PCM int16 出力・全 generator が 1 次元=mono）と `candidates/impl/formant_burg.py` の唯一のリサンプラ実装から機械転記した |
| `UNDERSPEC-CAL-D06` | `c0_freeze.py` | `frozen_design.fixture_spec.<FAMILY>.confound_axes`/`.boundary_probes` を、matrix.py が既に厳密に持つ per-family targeted interaction 実列挙（memo §2.7）を二重管理しない、より粗い「変動しうる primary/boundary 軸名」の宣言に留めた（非空 list 要求は満たす。値の意味論的相互検証は `c0_validate.py` の範囲外） |
| `UNDERSPEC-CAL-D07` | `c0_freeze.py` | C0 manifest の `rng_ledger` を "declaration form" とし、`public_seed_id` を `sha256("declared:"+stream_name)`（公開情報のみから導出する placeholder）とした。`build_manifest()` は dry-run/armed 双方から同一シグネチャで呼ばれ secret を一切受け取らないため（secret から実導出した OKM の digest ではない） |
| `UNDERSPEC-CAL-D08` | `c0_freeze.py` | `splitter.realize_split` の stratum 化因子を `("truth_level", "boundary_class")` に固定した（`splitter._COVERAGE_AXES` のうち row 単位で常に定義される 2 軸。`generator_impl` は FORMANT_GT 以外で常に `None` のため除外） |
| `UNDERSPEC-CAL-D09` | `armed_freeze()`（設計正本 §7） | campaign close 後に `split_secret` を ledger へ reveal（commit-reveal）し、検証器（第三者）が split を再計算・再現できるようにする運用を新規提案した。設計正本 §7 は secret の生成時保持と sha256 commitment の記録は明記するが、CAMPAIGN_CLOSED 後の reveal 手順を明記しない（`approvals/records/GATE1_DECISION_RECORD.md` §5.1）。C0 レビュー対象、本 Phase では未実装 |
| `UNDERSPEC-CAL-D10` | `c0_freeze.py` | E_use evidence table（設計正本 §10.2）の既定 path を `voice_genesis/calibration/config/e_use_table_v1.json`（repo-relative、追跡下）に固定した。承認ファイルとは異なり、これはユーザーが事前記入・コミットするデータであり dirty-tree 判定の対象内で構わない。dry-run は load+validate のみ（違反は `BLOCKED_C0_MANIFEST_INCOMPLETE` に `"e_use_table: ..."` detail で合流）、armed freeze は `campaigns/<id>/e_use_table.json` へコピーし sha256 を非-core 節 `frozen_inputs.e_use_table_sha256` と freeze event へ記録する |
| `UNDERSPEC-CAL-D11` | `gates.py`, `e_use_table.py` | `EUseEvidenceRow` に 14 列目 `e_use_mode: "absolute"\|"relative"`（既定 `"absolute"`、旧 13 列のみの行と後方互換）を追加した。設計正本 §10.2 は E_use を「用途許容誤差」とのみ述べ、construct 単位の 1 スカラーが truth 値に対する相対誤差（例: formant の 5%、F0 の 20 cents）を宣言する手段までは規定しない。`"relative"` 行の per-instance 絶対値展開（`e_use_value × declared_truth`）は本拡張の範囲外（campaign 側の責務） |

以下は Codex レビュー（2026-09-01、複数巡）で採用され `IMPLEMENTATION_MAP_v1.md`
に凍結された仕様であり、上表の UNDERSPEC には数えない（正本の一部として実装
済み）: `canonical.py` の `vgcal-canon/1` 版管理と `-0.0` 正規化、
`provenance.py` の単一 writer 境界（fcntl 排他 + fsync）と `verify_chain()` の
truncated/tamper 区別、`gates.py` の DIRECTIONAL resolvability 単位健全二連言
分解、`splitter.py` の重複 `row_id` 拒否、`selection.py` の ceiling 階級間裁定
規則、`observables.py` の `u_rep` singleton 除外、`m6_identity.py` の
CLAIM_CRITICAL_SET 全 member ABSOLUTE 必須化。

## テスト

```bash
python -m pytest voice_genesis/calibration/tests -q
ruff check voice_genesis/calibration/
```

依存は numpy / scipy / stdlib のみ（`svp_rpe` / `voice_genesis.harness` を
import しない）。
