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
| `gates.py` | threshold budget `M[i]`・ABSOLUTE gate 1/2'/max'/3/4'/5・DIRECTIONAL 単位健全二連言 (+可換時合算式) resolvability | §10.2–10.4 |
| `selection.py` | 族別 lexicographic 選択（丸め後比較）・`select_across_ceilings` ceiling 階級間裁定・`SELECTION_FAILED_CLOSED` | §9, §2.6 |
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
| `fixtures/generators/common.py` | PCM 16-bit 量子化・dBFS gain・20ms cosine ramp・100ms voiced prefix/suffix・transition-adjacent context・declared SNR noise mixing |
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
| `c0_validate.py` | C0 manifest dry-run 検証: REQUIRED_BLOCKING（§3.1）欠落・hollow（空文字列/空コンテナ）→ `BLOCKED_C0_MANIFEST_INCOMPLETE`（キー存在チェックに加え、path+hash マップの形状・`frozen_design.meter_specs` の全 meter family 網羅・`independence_ledger`/`rng_ledger` のエントリ形状も検証 = Codex レビュー 2026-09-01 P1。`independence_ledger` はさらにキー集合が `candidates.registry.ALL_CANDIDATES` の凍結 99 candidate_id 全集合と完全一致すること、各 entry の tier が registry 宣言 tier と一致することも検証 = Codex レビュー 2026-09-01 追加 P1）、RECORDED_OR_ABSENT（§3.2）は値または `ABSENT:<理由>` を許容し `WEAK_ENV_LOCK` 降格 annotation、pyworld 特則（§3.3、D4C のみ ineligible・campaign は BLOCK しない）、RNG 台帳の unseeded stream 検出 → `BLOCKED_C0_UNSEEDED_RNG`。**書込・secret 生成・freeze event 記録なし**。§18 Gate 2 承認後の武装版 freeze スクリプトは別 PR（未着手） | §3 |

依存の例外: `candidates/impl/b0_wrappers.py` のみ `voice_genesis/harness/*` を
read-only import する（本ファイル冒頭「テスト」節の「`voice_genesis.harness`
を import しない」は Phase A framework core 各モジュールの依存方針であり、
Phase C の B0 wrapper には適用されない。タスク境界で明示的に許可された唯一の
例外）。`candidates/impl/f0_pyin.py` は `librosa` に依存する
（`IMPLEMENTATION_MAP_v1.md` §1 が宣言する campaign 依存の一部）。

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
