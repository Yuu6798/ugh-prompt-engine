# RUN10-CAL 実装マップ v1（Design Memo — Fable 設計）

正本: [`DESIGN_VG_METER_CAL_DEBT_v1.0.md`](DESIGN_VG_METER_CAL_DEBT_v1.0.md)
（VG-METER-CAL-DEBT-DESIGN-v1.0。以下「設計正本」。§番号は全て設計正本を指す）。

## 0. 授権境界（最重要）

設計正本は `execution_authorized: false` / `meter_changes_authorized: false`。
ユーザーの今回指示は「設計に基づく **コード実装**」であり、以下は本実装に**含めない**:

- C0 freeze の実行（manifest/registry の凍結 artifact 生成・freeze event 記録）
- secret（`split_secret` / `render_root_secret`）の生成・保存。全 API は secret を
  **引数として受け取る**のみ。テストはテスト内生成のダミー secret を使う
- 456×5 の campaign 実行・selection/holdout の実測走行
- 既存 meter（`voice_genesis/harness/*` / `af_measure.py` 等）の変更。B0 wrapper は
  **無改変 import** のみ
- RUN11 関連の一切

`c0_validate.py` は **dry-run 検証のみ**（書込なし・secret 生成なし）。武装版 freeze
スクリプトは §18 Gate 2 承認後の別 PR とする。

## 1. 配置と依存

- パッケージ: `voice_genesis/calibration/`（`__init__.py` あり。import は
  `voice_genesis.calibration.*` の完全修飾。実 import 可否は pytest で実証する）
- svp-rpe 本体（`src/`）を import しない（voice_genesis 非依存宣言に従う）
- 依存: numpy / scipy / librosa / soundfile / stdlib（hashlib, hmac, json, subprocess）。
  pyworld は **guarded import**（不在 → 当該候補 ineligible。§3.3 pyworld 特則）
- 値オブジェクトは frozen dataclass（voice_genesis 規約。pydantic 不使用）
- 全モジュール `from __future__ import annotations`・型ヒント必須・ruff (E4,E7,E9,F) clean

## 2. モジュール → 設計正本 対応表

| module | 実装内容（正本§） |
|---|---|
| `vocab.py` | 閉語彙全部: 終端 status・手続 Gate（§1）・BLOCKED codes（§3.3）・missing 理由コード（§11）・independence tier / claim ceiling（§4.1）・evidence_class（§10.2）・Domain/Split enum・`CLAIM_CRITICAL_SET` 定数・`debt_discharged()` 純導出関数（D1） |
| `canonical.py` | バージョン付き正規形 **`vgcal-canon/1`**（sorted keys・区切り最小・NaN/Inf 拒否・float は Python repr 最短往復 + `-0.0`→`0.0` 正規化）+ `row_id = sha256(row_json)` + `manifest_sha`。設計正本 §7 の「RFC 8785 相当」は **byte 互換を主張しない**: RFC 8785 と数値表記が異なる（例 `1e-07` vs `1e-7`）ため、正規形を独自名で版管理し、検証器は同一実装で照合する（Codex レビュー 2026-09-01 採用） |
| `streams.py` | HKDF-SHA256 stream 分離（`info` = 長さ接頭辞付き field 連結: campaign_id/family/split/row_id/probe_index/purpose）→ 64bit seed → `np.random.Generator(PCG64)`。RNG 台帳 dataclass（§3.3, §7） |
| `splitter.py` | HMAC-SHA256(secret,row_id) 昇順 → stratum 内 largest-remainder 50/25/25 → 端数の偶奇交互配分 → 制約検査 → 決定的最小 swap 修復 + swap 記録 → 実現済み row→split 表と検証器（§7） |
| `tolerance.py` | pooled dispersion（family×condition class）・`tolerance=max(k·pooled_SD, floor)`・floor 機械導出・`UNSTABLE_CELL`・`TOLERANCE_FLOOR_LIMITED`（§6） |
| `observables.py` | 二段 median `m[i]`・`e/AE/RE`（zero guard）・BIAS/MAE/q95(linear 固定)・`U_rep`/`U_proc`（多 process 一般化）・nuisance `dS`・FDR0/FNR1（missing/invalid を分子算入・最小数 N≥10）・failure boundary（§10.1） |
| `gates.py` | threshold budget `M[i]=E_use−U_GT−U_num`（加算・非RSS）・ABSOLUTE gate 1/2'/max'/3/4'/5（per-instance margin `G[i]`）・DIRECTIONAL `R_ij` / resolvable / ≥3 pairs（=3 は provenance flag）（§10.2–10.4） |
| `selection.py` | 族別 lexicographic（ABSOLUTE 系列 / DIRECTIONAL 系列）・丸め後比較（error=有効3桁, rate=0.001, complexity=int）・丸め前後 vector 記録・`SELECTION_FAILED_CLOSED`（§9） |
| `status.py` | first-match cascade（§11 の 5 段）・missing→(status, reason) 一意写像・手続 Gate 単調性検査（§1 R1） |
| `m6_identity.py` | `u_X[j]`・sum-of-norms `U_obs_pair`・`T_null=q95(D_null+U_null_pair)`・`distinct()`・空 critical set → NOT_EVALUABLE・等重み L1/L2 のみ（§12） |
| `provenance.py` | §13 必須 field の dataclass schema + append-only JSONL ledger（entry sha 連鎖）+ leakage 検査（holdout row の unseal 前初出 → `BLOCKED_LEAKAGE`）（§7, §13）。**単一 writer 境界を契約とする**: append は fcntl 排他ロック + flush/fsync で直列化し、並列 meter 実行（§14）は per-worker 記録 → 直列 append の集約で行う。`verify_chain()` は途中破損（truncated 末尾行・sibling 分岐）を検出して報告する（Codex レビュー 2026-09-01 採用） |
| `fixtures/axes.py` | §5.1 primary/boundary 軸水準の凍結定数 |
| `fixtures/matrix.py` | 456 logical cell の**明示列挙**（family 別内訳・cal/sel/holdout 数は §5.2 表と厳密一致をテストで enforce）・domain tag 導出・F0 帯域整合検査 → BOUNDARY 再タグ（§3.3）・nuisance block（一因子主効果 + 6 targeted interactions の全行列挙） |
| `fixtures/controls.py` | family 別 negative control + 対 positive control（両側条件）（§4.2） |
| `fixtures/generators/*` | family 別決定論 generator（§4.2）。FORMANT_GT は **resonator 非共有の2実装**（filter cascade 系 / 閉形式加算合成系）。PCM 量子化・dBFS gain・ramp/context は `common.py` に集約 |
| `fixtures/determinism.py` | generator repeat=2 fresh process（subprocess）byte 一致検査 → 違反 `BLOCKED_C1_GENERATOR_NONDETERMINISTIC`（§3.3, §6） |
| `candidates/registry.py` | 99 候補の宣言的定義（§8。数の検算: F0 5 + M3 43 + M2T 13 + M2A 24 + M4 5 + M5 7 + M6 2 = 99）。candidate_id/construct/unit/family/params/domain/missing rule/tier/ceiling/complexity rank |
| `candidates/adapter.py` | meter adapter interface + 共通 fail filter（schema 違反・非有限値・within/fresh 不一致・negative 偽検出・positive 不発火）（§8） |
| `candidates/impl/*` | 候補 algorithm family 実装（pyin=librosa / cepstral poles / Burg LPC（`fs'=2*max_formant` 決定的 resample 必須）/ harmonic OLS・Theil-Sen / ACF HNR / harmonic residual / D4C=pyworld guarded / local prominence / wave discontinuity / spectral flux）+ `b0_wrappers.py`（harness 無改変 import） |
| `c0_validate.py` | C0 manifest の dry-run 検証（REQUIRED_BLOCKING / RECORDED_OR_ABSENT の二層判定・BLOCKED_* code 発行・書込なし）（§3） |

## 2.5 設計正本の実装解釈（correction 候補。正本は read-only のため append-only 記録）

Codex レビュー 2026-09-01（第 2 巡）採用分。正本改訂は §0 保存規則により本実装では行わず、
以下の解釈で実装し、C0 freeze 承認時のユーザーレビュー対象とする:

- **U_rep（§6/§10.1）**: 6 call 構成（within-process 3 call + fresh-process 3×1 call）では
  singleton process の range が構造ゼロとして q95 を希釈する。`u_rep` は **repeat 数 ≥ 2 の
  process group のみ**を母集団とする（n=1 の range は 0 でなく未定義。除外は U_rep を
  大きくする fail 側の保守的読み）
- **R_ij の単位分解（§10.4）**: v1.0 の `R_ij` は truth 単位の `U_GT/U_num` と output 単位の
  `U_rep/U_proc` を加算しており、truth と output の construct 単位が異なる候補（例: M2A 系 =
  truth は injected fraction / output は HNR dB）では無意味になる。実装は単位健全な二連言に
  分解する: (a) truth 側 resolvability `Delta_truth(i,j) > (U_GT_i+U_num_i)+(U_GT_j+U_num_j)`
  （truth 単位のみ・事前決定可能）、(b) output 側有意性 `|Delta_output(i,j)| > 2*(U_rep+U_proc)`
  （output 単位。§10.4 既存の「各 effect が noise floor 超過」連言の定式化）。単位可換な
  construct（フラグで宣言）には v1.0 の合算式も**追加で**課し、定義可能な範囲で v1.0 の
  保守性を弱めない（Codex レビュー第 3 巡 2026-09-01 採用）
- **row_id 一意性（§5/§7）**: fixture matrix バリデーションは件数一致に加えて全 456 行の
  canonical row_id の一意性を必須とする（重複 row は件数検査を素通りして計画セルを暗黙に
  欠落させるため）。splitter も入力 row_id 重複を拒否する（Codex レビュー第 3 巡採用）
- **M6 × CLAIM_CRITICAL_SET（§12）**: §12「CALIBRATED_ABSOLUTE component のみで構成」を
  部分集合の再構成と読まない。D1（C0 後の縮小禁止）・§8（1件でも missing/ineligible なら
  NOT_EVALUABLE）・§15（全 critical component ABSOLUTE 必須）と整合する唯一の読みとして、
  **凍結済み CLAIM_CRITICAL_SET の全 member が CALIBRATED_ABSOLUTE のときのみ** M6 distance を
  計算し、それ以外は NOT_EVALUABLE（部分データからの distance 出力は禁止）

## 2.6 結果を左右する選択の設計時凍結（Codex レビュー第 4 巡採用）

実装者裁量に残さず、本 memo で凍結する（C0 freeze 承認時のユーザーレビュー対象である
位置づけは不変。変更は本 memo の改訂として行い、コード内の暗黙変更を禁止する）:

- **selection の ceiling 階級間裁定（§9 の補完）**: meter family 内の selection は
  ceiling 階級の優先順で単一 pool を選んでから族別 lexicographic を適用する。
  (1) ABSOLUTE ceiling の eligible 候補が 1 つでもあれば、その pool のみで ABSOLUTE 族
  criteria により選抜。(2) 無ければ DIRECTIONAL ceiling pool で DIRECTIONAL 族 criteria。
  (3) それも無ければ `SELECTION_FAILED_CLOSED`。DIAGNOSTIC_ONLY ceiling の候補は holdout
  claim の selection 対象にならない（M4 は §8/§16 により selection を回さず全候補
  DIAGNOSTIC_ONLY で閉じる）
- **§8 の未確定パラメタグリッド**（件数は §8 と厳密一致）:
  - M2A-HNR-ACF 8 = frame {25, 40} ms × hop {10, 20} ms × window {hann, blackman_harris}
  - M2A-HARMONIC-RESIDUAL 12 = K {8, 10, 12} × window {hann, blackman_harris} ×
    residual band {0–Nyquist, 0–6 kHz}
  - M2A-D4C 3 = 集計帯 {broadband, 0–3 kHz, 3–6 kHz}（F0 入力は選択済み F0_CONTROL 固定）
  - M4-LOCAL-PROMINENCE 4 = prominence 閾値 {6, 12} dB × envelope 平滑帯域 {150, 300} Hz
  - M5-WAVE-DISCONTINUITY 3 = 検出窓 {2, 5, 10} ms
  - M5-SPECTRAL-FLUX 4 = frame {512, 1024} × flux ノルム {L1, L2}
  - F0-PYIN 4 = frame {2048, 4096} × hop {256, 512}（設計正本で確定済み・再掲）
- **declared sweep（§10.4 DIRECTIONAL gate 前提。UNDERSPEC-CAL-D76 def A、
  D75 ruling (1)/(2) を SUPERSEDE — 詳細は `README.md` D76 entry）**: family
  の declared sweep は、PRIMARY domain の凍結 fixture matrix のうち
  nuisance/covariate 設定（gain/duration/noise/context 等）を固定し truth
  水準だけが動く truth-core block の行集合（`fixtures.matrix.
  declared_sweeps_by_family()`。旧 D75 の「nuisance_tag が変動させる
  confound 軸そのもの」という定義は誤りで、group 内 truth が anchor 固定
  になり全 pair `delta_truth == 0` を生む構造的欠陥があった）。C0 freeze
  時にこの関数で導出し `frozen_design.fixture_spec.<FAMILY>.declared_
  sweeps`（sweep_id → member row_id 一覧）として記録する（`manifest_
  core_sha` に含まれる）。`campaign.holdout_stage.declared_axes_for_
  family()`（`confound_axes` 読み戻し）は gate4' invariance 軸専用のまま
  変更しない——D18 の「同じ confound_axes 列を DIRECTIONAL sweep_id
  としても再利用する」という旧写像は誤りとして訂正した（`holdout_
  stage.py` の D18 note 参照）。
- **holdout 上の DIRECTIONAL 到達不能性（既知の設計事実。UNDERSPEC-CAL-
  D76）**: 上記 def A を凍結 matrix 全体で評価すれば 7 family 全てが
  「各 declared sweep で truth level >= 3」を満たすが、HOLDOUT split
  （`STRATUM_FACTOR_NAMES = (block, domain)` 層別 25%）は sweep 構造を
  層別因子に含まないため、各 sweep の holdout 残存行が構造的に 1–2 行へ
  薄まる。結果として DIRECTIONAL-ceiling 候補は holdout 上で
  `NOT_EVALUABLE`（`DIRECTIONAL_SWEEP_UNRESOLVABLE_ON_HOLDOUT`）に
  倒れるのが通常の帰結であり、これは matrix/split の設計事実であって
  回避すべきバグではない（sweep-aware stratification は design v1.1
  改訂候補）。

## 2.7 fixture matrix の設計時凍結（Codex レビュー第 5 巡採用。§5.2 件数と厳密一致）

実装は本節の機械的転記とし、行選択の裁量を残さない。

**truth core の因子分解**（軸水準は §5.1 の値。明記なき軸は下記 anchor 水準）:

- F0_CONTROL 12 = F0 4 × SR 3（generator = harmonic pulse train）
- FORMANT_GT 60 = pole set 5 × bandwidth anchor 3 × 実装 2（cascade / additive）× F0 {C3, G4}
- TILT_GT 30 = slope 5 × SR 3 × F0 {C3, G4}
- APERIODICITY_GT 36 = fraction 6 × F0 {C3, G4} × SR 3、bandwise 24 = band 4 × fraction 6
  （anchor F0=C3・SR 48k）
- RESONANCE_GT 24 = center 4 × bandwidth 3 × prominence 2
- TRANSITION_GT 24 = join type 4 × severity 3 × duration class 2
- IDENTITY_CAUSAL_SWEEP 60 = founder 4 × trait 3 × delta 5

**anchor 水準**: SR 48000 / gain −12 dBFS / duration 1.00 s / noise clean /
context steady-isolated。family anchor（confound の基点）: F0_CONTROL = C4@48k、
FORMANT_GT A1 = pole set (500,1900,2600)・bw 100・C3 / A2 = (500,900,2400)・bw 100・G4、
TILT_GT = slope −12・C3@48k、APERIODICITY = fraction 0.10・C3@48k、
RESONANCE = center 1000・bw 150・prom 12、TRANSITION = amplitude step・中 severity・長 class、
IDENTITY A1 = founder 1・trait 1・delta 0 / A2 = founder 3・trait 2・delta 0。

**confound block（決定的レシピ）**: 正準 nuisance 系列 =
`[gain→−24, gain→−6, dur→0.25, dur→0.50, dur→2.00, noise→40, noise→20, noise→10,
context→ramp, context→prefix/suffix, context→transition-adjacent]`（11 行、family anchor に
適用）→ §5.1 の 6 targeted interactions（記載順）→ 第 2 anchor（A2）への同系列、の順に連結し、
**family の confound 件数 N の先頭 N 件**を採る。ただし family の truth construct を変える軸は
「適用外」として系列から除外する（APERIODICITY は noise 軸・その関与 interaction を除外、
TRANSITION は context 軸を除外。§10.1「truth 自体が変わる軸は invariance に混ぜない」準拠）。
件数検算: F0 24 = 11+6+7、FORMANT 24 = 11+6+7、TILT 12 = 11+1、APER 6 =（noise 除外 8 行系列の）
先頭 6、RESONANCE 12 = 11+1、TRANSITION 12 =（context 除外 8 行系列）+ 適用可 interaction 4
= 12、IDENTITY 24 = 11+6+7。

**boundary/negative block（決定的レシピ）**: 正準 boundary 系列 =
`[F0→G2, F0→C5, SR→16k, SR→96k, gain→−36, gain→−1, dur→0.10, dur→4.00, noise→0dB]`
（family anchor に適用・適用外軸は除外）+ negative control 系列 =
`[silence, noise-only, pure-sine（F0_CONTROL 以外）, out-of-band-pole（FORMANT/RESONANCE）,
too-short, invalid-SR]`。family の件数 N に対し **boundary 系列の先頭 (N−3) 件 + negative
系列の先頭 3 件**（N=6 の family は boundary 4 + negative 2）。negative には §4.2 両側条件の
対 positive 指定を付す。件数: F0 12 / FORMANT 12 / TILT 6 / APER 6 / RESONANCE 12 /
TRANSITION 12 / IDENTITY 12。

**targeted interactions の per-family 実列挙**（規則 = §5.1 記載順の先頭 k 件、
truth construct を変える軸が関与するものは適用外。第 6 巡レビュー採用）:

- F0_CONTROL / FORMANT_GT / IDENTITY: 全 6 件
- TILT_GT (k=1): high-F0×low-SR
- RESONANCE_GT (k=1): high-F0×low-SR
- TRANSITION_GT (k=4): high-F0×low-SR, high-F0×short-duration, high-F0×low-SNR,
  low-F0×high-SR（context 軸関与の transition×short-duration と、次順の low-gain×noise を
  規則により不採用）
- APERIODICITY_GT (k=0): noise 軸関与 2 件を除いた 4 件は N=6 が nuisance 系列で
  埋まるため不採用

**control 共有契約**（第 6 巡レビュー採用。N_neg≥10 と split 配分の構造矛盾の解消）:

- §10.1 の最小数 N_neg/N_pos は **instance 数**（logical cell × probe repeat 5）で数える
- negative control 行は HMAC 配分で home split を持つ（§5.2 の件数会計は不変）が、
  **sweep truth を運ばない control class**（§4.2 の直交宣言）として全段階（selection の
  共通 fail filter / holdout gate 5）で評価可とする。leakage 検査（`BLOCKED_LEAKAGE`）は
  control 行を明示除外し、除外集合と本契約を C0 manifest と台帳に記録する
- 対 positive control（§4.2 両側条件）= family anchor の truth core 行 2 件を C0 で指定
  （truth 行としての役割は不変。designated anchor 行の metadata としては残す）。
  **leakage 除外はこの positive 行には適用しない**（第 7 巡レビュー採用: positive control
  は truth core 行そのものであり、home split が HOLDOUT なら holdout seal の対象そのもの
  である。leakage 除外集合に含めると unseal 前に sweep truth を観測できてしまうため除外
  集合は negative のみとする）。**positive 検出証拠 = 評価対象 split 内の当該 family の
  全 truth-core 行（×5 repeats）**（第 8 巡レビュー採用、DESIGN RULING: designated
  2-anchor 方式は instance 数の計算対象としては撤廃。2 行の home split が同一 split に
  偏ると selection/holdout の一方で `N_pos=0` になり両 split で `N_pos>=10` を同時に
  満たせない構造欠陥があったため、母集団を「評価対象 split 内の当該 family の全
  truth-core 行」へ拡張した。selection 段階 = selection split 内の truth-core 行、
  holdout gate 5 = unseal 後の holdout split 内の truth-core 行。family の truth core
  行数は最小でも 12 件あり、50/25/25 split の下で両 split とも `N_pos>=10` を安定して
  充足し、seal を跨がない）
- これにより N=6 family（negative 2 cell = 10 instances）でも gate 5 の最小数と
  selection fail filter を同時充足する
- **`splitter.py` の coverage 制約は `truth_level="TRUTH_CORE"` のみ split 当たり被覆下限を
  2 に拡張する**（第 9 巡レビュー採用: 従来の下限 1（存在保証のみ）では、ある実 secret で
  HOLDOUT 側の truth-core 行が 1 件に留まり `N_pos=5 (<10)` となる family が生じたため。
  family の truth core 行数は最小でも 12 件あり infeasible にはならない）

本節の変更は memo 改訂としてのみ行い、コード内での暗黙変更を禁止する。C0 freeze 承認時の
ユーザーレビュー対象。

## 3. underspec の解決規約

設計正本が数値・グリッドを確定していない箇所（例: family 別 truth×confound の因子直積の
具体形、M2A-HNR-ACF 8 / M2A-HARMONIC-RESIDUAL 12 / M4 4 候補のパラメタグリッド、HKDF の
field 区切り）は、**§5.2 / §8 の件数と厳密一致する最も単純な選択**を採り、当該モジュール
docstring に `[UNDERSPEC-CAL-nn]` タグで記録し、`README.md` の UNDERSPEC 台帳に集約する。
これらは C0 freeze 承認時のユーザーレビュー対象であり、コードが正本を上書きするものではない。

## 4. テスト要件

- `voice_genesis/calibration/tests/` を pyproject `testpaths` に追加（依存は本体必須依存
  のみ）。`tests/discipline/` と `.github/test-paths/foundry-core.txt` の同期規律に抵触
  しないことを実行で確認
- 高速性: CI で音声を扱うテストは少数 row の短尺生成に限定。456×5 の全展開は行わない
  （matrix は列挙とカウント検証のみ）
- 必須検証: 456/228/114/114 の厳密一致・99 候補数・split の決定性と制約充足・gate 式の
  境界ケース（G[i]=0 は PASS 側）・status cascade の網羅/排他・M6 空集合→NOT_EVALUABLE・
  canonical JSON の安定性（key 順・float 往復・`-0.0` 正規化）・generator の同 seed 再現
- **式単位の独立数値オラクル**（Codex レビュー 2026-09-01 採用）: 結果を左右する全式
  （二段 median / q95 margin `G[i]` / gate3 / gate4' invariance / `R_ij`・resolvable /
  FDR0・FNR1 / selection 丸め / M6 sum-of-norms・`T_null`）に、実装から独立に手計算した
  入力ベクトル→期待値の固定オラクルを 1 件以上与え、不一致は即 fail とする
- generator determinism の fresh-process 検査は `@pytest.mark.slow` 可

## 5. 実装フェーズ（委譲単位）

- **A: framework core** — vocab / canonical / streams / splitter / tolerance /
  observables / gates / selection / status / m6_identity / provenance + tests + 配線
- **B: fixtures** — axes / matrix / controls / generators / determinism + tests
- **C: candidates + c0_validate** — registry / adapter / impl / b0_wrappers /
  c0_validate + tests

B・C は A 完了後に並行。ファイル所有は排他（共有ファイルは A が作る `__init__.py` と
README のみ。B/C は README の自セクションのみ追記）。

## 6. Phase D — 未武装の実行基盤（PR #342 マージ後の次段。2026-09-01 設計）

目的: §18 の 3 承認 Gate が通れば**コード変更なしで**C0 freeze と campaign 実行に入れる
状態まで基盤を完成させる。本 Phase 自身は freeze も実測も行わない（既定 = dry-run）。

### 6.1 武装プロトコル（三要素。1 つでも欠ければ `AUTHORIZATION_REQUIRED` で拒否）

- CLI フラグ `--armed`
- 環境変数 `VG_CAL_C0_FREEZE_AUTHORIZED=1`（campaign 実行は `VG_CAL_CAMPAIGN_AUTHORIZED=1`）
- 承認ファイルは**チェックアウト外**の `VG_CAL_APPROVAL_DIR`（既定
  `~/.vg_cal/approvals/`）配下に置く: `gate1_campaign_execution.json`（Gate 1: cost
  caps 3 値 + E_use bound 受容）/ `gate2_c0_freeze.json`（Gate 2）/
  `gate3_seal_acceptance.json`（Gate 3）。理由: checkout 内の未追跡ファイルは
  dirty-tree 判定で武装経路を自己否定し、コミットすれば HEAD が変わり manifest 派生の
  campaign identity が動くため（PR #343 第 1 巡採用）。各 {approver, approved_at_utc,
  design_doc_sha256, memo_sha256} を持ち、**campaign_id は含まない**——campaign_id は
  `manifest_core_sha` から事後導出される値であり承認ファイル作成時点では確定しない
  （導出規則は §6.2）（PR #343 第 2 巡採用）。sha は**実ファイルのハッシュと
  照合**。各承認ファイルの content sha256 は manifest（`approvals.<gate>_sha256`）と
  freeze event の双方に記録する（content-pin）（PR #343 第 1 巡採用）。承認ファイルは
  **1 回のバイト読み取り**で取得し、parse（JSON decode）と sha256 計算は**同一バッファ**
  から行う（読み取りを重ねない。TOCTOU・二重読み不整合の回避）（PR #343 第 4 巡採用）
- `AUTHORIZATION_REQUIRED` は §3.3 閉語彙（BLOCKED_*）とは別軸の pre-campaign 拒否コード
- **one-time authorization nonce**（承認ファイル再利用による別日 freeze と支出倍増の
  遮断）: 各承認ファイルは上記フィールドに加えて `authorization_nonce` を持つ
  （`dry-run` が発行する値をそのまま転記する。発行・照合・記録の手続きは §6.3）。
  Gate 1（`gate1_campaign_execution.json`）と Gate 2（`gate2_c0_freeze.json`）の
  承認ファイルは**同一の** `authorization_nonce` を保持しなければならず、不一致は
  `AUTHORIZATION_REQUIRED`（理由コード `"nonce_mismatch"`）で拒否する（PR #343 第 5
  巡採用）

### 6.2 secret と出力レイアウト

- secret（split_secret / render_root_secret）は `VG_CAL_SECRET_DIR`（既定
  `~/.vg_cal/secrets/<campaign_id>/`）に生成。**ディレクトリ
  `<VG_CAL_SECRET_DIR>/<campaign_id>/` は mode 0700**（探索可能）、**ファイル
  （split_secret / render_root_secret）は mode 0600**（PR #343 第 3 巡採用）。
  **リポジトリには commitment `sha256(secret)` のみ**。`.gitignore` に
  `voice_genesis/calibration/**/secrets/` と `campaigns/*/renders/` を追加
- 承認ファイル（Gate 1–3）も同様にチェックアウト外に置く: `VG_CAL_APPROVAL_DIR`
  （既定 `~/.vg_cal/approvals/`）配下の `gate1_campaign_execution.json` /
  `gate2_c0_freeze.json` / `gate3_seal_acceptance.json`（配置理由・sha256 記録先 =
  §6.1）（PR #343 第 1 巡採用）
- `manifest_core_sha` = manifest 本体から `approvals` セクション・secret-commitment
  フィールド・`realized_split` セクション・`campaign_id` を除いた**凍結前
  authorization payload**の canonical sha（dry-run 時点で計算可能な値）。
  `campaign_id = RUN10-CAL-<YYYYMMDD>-<manifest_core_sha[:8]>`。Gate 2 承認
  （`gate2_c0_freeze.json`）はこの `manifest_core_sha` を束縛対象とする（PR #343
  第 2 巡採用。定義を「凍結前 authorization payload の sha」として明確化 =
  PR #343 第 4 巡採用）
- freeze 済み manifest は上記 authorization payload に加えて `realized_split`
  （設計正本 §7 の「正本は C0 manifest に列挙した実現済み row→split 表」に従う、
  実現済み row_id→split 表）を**インライン**で保持し、`realized_split_sha`・
  commitments・`approvals`（gate1/gate2 の sha256）を併せ持つ。この最終形全体の
  sha は `manifest_core_sha` とは別に freeze event へ `manifest_sha` として記録
  する。`realized_split.json` はこのインライン表の convenience copy（写し）で
  あり、正本はあくまで manifest 内のインライン表である（PR #343 第 2 巡採用。
  realized_split の正本所在とインライン保持を明確化 = PR #343 第 4 巡採用）
- Gate 3（seal 受容）は freeze **後**に成立するため manifest には含まれない: D2
  runner は sealed-stage 作業に入る前に、承認ファイルの sha256 を伴う
  `GATE3_ACCEPTED` ledger event で束縛する（PR #343 第 2 巡採用）
- `voice_genesis/calibration/campaigns/<campaign_id>/`: `c0_manifest.json` /
  `realized_split.json` / `ledger.jsonl` / `events/*.json` / `renders/`（gitignore）/
  `measurements/`（gitignore）

### 6.3 D1 — freeze producer + 承認 Gate + ユーザー著述入力（`c0_freeze.py`, `approvals.py`, `approvals/README.md`, `e_use_table.py`, `cost_caps.py`）

- `approvals/README.md` は**外部配置場所の説明のみ**を記す（`VG_CAL_APPROVAL_DIR` /
  既定 `~/.vg_cal/approvals/` 配下の 3 ファイル名。承認 json 実体はリポジトリに
  一切格納しない）（PR #343 第 1 巡採用）
- `approvals.py` の承認ファイル読み取りは §6.1 と同じ**1 回のバイト読み取り**を
  実装契約とする: parse と sha256 は同一バッファから計算し、ファイルを 2 度読まない
  （PR #343 第 4 巡採用）

- producer は manifest を**コードから生成**: git HEAD full SHA・dirty-tree・path inventory
  実ハッシュ・Python/numpy/scipy/librosa/soundfile exact version・sample format policy・
  frozen design 各節（registry / matrix / memo §2.6–2.7 定数から導出）・independence
  ledger（registry 99 件）・RNG 宣言台帳・RECORDED_OR_ABSENT 環境項目（取得不能は
  `ABSENT:<理由>`）→ `validate_c0_manifest` を通す
- dry-run: manifest を生成・検証して報告するだけ（書込なし・secret なし）。この際
  `manifest_core_sha`（§6.2）を報告し、Gate 2 承認はこの値を束縛対象とする
  （PR #343 第 2 巡採用）。**同時に** `authorization_nonce`（`secrets.token_hex(16)`）
  を新規発行して報告する。Gate 1・Gate 2 双方の承認ファイルはこの nonce をそのまま
  転記する（nonce の一致要件・不一致時の拒否コードは §6.1）（PR #343 第 5 巡採用）
- armed: **secret 生成に入る前**に one-time authorization nonce を検査する（PR #343
  第 5 巡採用）——既存の全 `campaigns/*/c0_manifest.json` を走査し、同一
  `authorization_nonce`（§6.1）を保持する manifest が**既に存在する**場合は副作用なし
  （secret 生成・書込のいずれも行わない）で拒否する（理由コード `"nonce_already_used"`）。
  この検査を通過した場合のみ armed 手続きへ進み、凍結 manifest には検査済みの
  `authorization_nonce` をそのまま記録する（次回以降の armed 実行が同じ承認ファイルを
  再利用しても上記走査で検出できるようにするため。PR #343 第 5 巡採用）→ secret 生成 → commitment 記入 → splitter 実行 → 実現 split 表 → `c0_freeze` event を
  ledger 先頭に記帳 → **直後に `split_frozen` event（`realized_split_map_hash`/
  `seal_commitment`）を記帳**（`c0_freeze.split_frozen_event_payload()` が正本。
  round 14 finding #1: `provenance.Ledger.check_leakage()` の
  `_verified_split_freeze_commitment()` はこの event を要求するが、旧稿はこの
  producer 側の記帳を欠いており実際の C0→...→C4 flow が常に `BLOCKED_LEAKAGE` に
  なっていた。`[UNDERSPEC-CAL-D28]`）→ **全成果物を staging に書く**（同一 FS 上の
  `campaigns/.staging-<id>/` と secret_dir 側 staging）→ read-back で
  `validate_c0_manifest` / `verify_split` / `Ledger.verify_chain` を再実行 → 全て通れば
  **公開順序を固定**する: まず secret 側を `os.replace`、続いて campaign 側を
  `os.replace`（secret を先に公開してから campaign を公開する）。**公開（2 回の
  `os.replace`）と `detect_orphans()` は単一の排他ロック `<secret_dir>/.publish.lock`
  （`fcntl.flock`）の下で実行し、両者が競合しないことを保証する**（PR #343 第 3 巡
  採用）。**最初の `os.replace` の直前**に secret dir 側へ in-progress マーカー
  `.publishing` を置き、campaign 側の公開が完了した時点で削除する（PR #343 第 3 巡
  採用）。campaign 側の rename が失敗した場合は**既に公開済みの secret dir を削除し、
  何も公開されていない状態へロールバック**する（PR #343 第 2 巡採用）。staging 検証段
  の失敗時は staging を削除し何も公開しない（secret も残さない）。テスト要件: **2 回の
  `os.replace` の間**に例外を注入しても `campaigns/<id>/` と secret のどちらも残らない
  こと（PR #343 第 2 巡採用。旧稿の「公開直前の例外注入」から対象タイミングを訂正）。
  **git commit はしない**（ユーザー操作）
- `detect_orphans()`: campaign dir はあるが対応する secret が無い → fail-closed
  （runner は当該 campaign の実行を拒否する）。secret はあるが対応する campaign dir
  が無い → orphan secret として削除する（PR #343 第 2 巡採用）。**ただし `.staging-*`
  ディレクトリは削除対象から除外する**。`detect_orphans()` 自体も上記の
  `.publish.lock` を取得してから走査する（PR #343 第 3 巡採用）。
  **`.publishing` マーカーを持つ secret dir の回収規則**: `.publish.lock` を
  **取得できた場合**（= 他 fd がロックを保持していない = 生存中の公開処理が無い
  ことを意味する）、対応する campaign dir の有無で分岐する——campaign dir が
  **無ければ**公開が完了しなかった stale な中断とみなし secret dir ごと削除する。
  campaign dir が**あれば**公開自体は完了済みでマーカー削除だけが中断したとみなし、
  マーカーのみを削除して当該 campaign を正当なものとして扱う（secret 本体は
  削除しない）。**ロックが他 fd に保持されている（= 生存中の公開処理が進行中）場合は
  マーカーの有無・campaign dir の有無に関わらず一切触れない**（PR #343 第 4 巡
  採用）。テスト要件: marker あり・対応する campaign dir 無し・lock 取得可能 →
  secret dir が削除されること／lock が他 fd に保持されている場合はマーカー付き
  secret dir が一切変更されないこと（PR #343 第 4 巡採用。旧稿の「in-progress
  マーカーは常に削除しない」という一律規則を、lock 取得可否と campaign dir 有無に
  基づく上記の回収規則へ訂正）
- E_use evidence table: §10.2 の 13 列 schema + loader/validator + テンプレート生成
  （全 construct 行を `evidence_class: UNJUSTIFIED` かつ `e_use_value: null` で出力。
  数値 placeholder 禁止）。UNJUSTIFIED 行は自動 ceiling（DIRECTIONAL/DIAGNOSTIC_ONLY）
- cost caps / stop rules: `c0_validate.COST_CAPS_REQUIRED_KEYS` と一致する 3 キー
  `compute`（**CPU 秒数**。wall-clock ではない — round 14 finding #2:
  `--workers>1` 下では wall time は並行実行分の CPU 時間を過小計上するため、
  各 fresh-process worker が自身の `resource.getrusage` 由来の `cpu_seconds` を
  報告しそれを課金する。`[UNDERSPEC-CAL-D29]`）/ `storage`（bytes）/ `budget`
  （通貨単位）の loader、超過判定 API
  （PR #343 第 2 巡採用）

### 6.4 D2 — campaign runner（`campaign/` サブパッケージ）

**runner 運用契約**（round 15 finding #2 見送り・境界宣言。`[UNDERSPEC-CAL-D32]`、
README 参照）: 1 campaign に対して armed プロセスは同時に 1 つのみを運用契約とする
（single-operator の逐次起動。並行複数プロセスはロックで排除するのではなく、
round 13/14 の duplicate-key 再構成 fail-closed（`StaleMeasurementError` 等）と
`[UNDERSPEC-CAL-D31]`（round 15 finding #3。counters を ledger 由来に束縛）により
過小計上を防ぐ形で運用契約違反を検出する）。

**プロセス境界の運用契約**（round 17 finding #4 見送り・境界宣言。
`[UNDERSPEC-CAL-D40]`、README 参照）: `cli.py` の canonical path 照合
（`_canonical_path_violations`。§6.4 上記の finding #7）が保証するのは
「stage 呼び出しごとに新規 `python -m voice_genesis.calibration.campaign`
プロセスを起動し、各プロセスが自身の起動時に path-hash 照合を実行してから
（同一プロセス内で既に import 済みの）モジュールを使う」運用を前提とした
場合に限る。in-process で `main()` を長時間・繰り返し呼ぶ、またはモジュールを
プロセスをまたいで再利用するような呼び出し方は本運用契約の対象外（その
ような呼び出し方の下では、照合後にファイルが書き換わってもプロセスが
再 import しない限り検出できない）。

**変更禁止の運用契約**（round 18 finding 2 件見送り・境界宣言。
`[UNDERSPEC-CAL-D41]`/`[UNDERSPEC-CAL-D42]`、README 参照）: armed stage の実行中は
checkout（凍結済み generator/candidate ファイル）も campaign dir 配下の artifact
（render 済み PCM 等）も一切変更してはならない。canonical な変更は新規 campaign を
要する。

- 手続 Gate 単位のサブコマンド: `c1-fixtures`（**calibration + selection split の行と
  negative control 行のみ** render + determinism 検査（同部分集合）+ ledger。**negative
  control 行の render 済み artifact は sha256 で ledger へ pin し、`c4-holdout` 段では
  再 render せずそのまま再利用する**（render 会計は本節末尾の union 式を参照。PR #343
  第 5 巡採用）。holdout
  行の render は行わない — `unseal` 後の `c4-holdout` 段で行う。これは §7 leakage
  契約（holdout 非 control 行の unseal 前 render は `BLOCKED_LEAKAGE`）と整合させる
  ための制約（PR #343 第 1 巡採用））→ `c2-baseline`（B0 × calibration split・
  tolerance 導出）→ `c3a-f0-selection`（**F0_CONTROL candidates × (selection 行 ∪
  F0_CONTROL の全 negative control instance、home split に依らない) →
  F0 selection → `F0_SELECTION_FROZEN` event**。F0_CONTROL は唯一 F0 を出力する
  meter であり、F0 依存候補の入力を確定させるため他の selection に**先立って**完了
  させなければならない）→ `c3b-selection`（**F0_CONTROL を除く**全候補 × (自 family
  selection 行 ∪ 当該 family の全 negative control instance、home split に依らない)・
  fail filter・lexicographic・`SELECTION_FROZEN` event（round 17 finding #1 採用:
  §2.7 control 共有契約により negative control は home split が CALIBRATION/
  HOLDOUT でも C3a/C3b の測定・fail filter 対象に含める——C1 で「全 control」として
  既に render 済みのため追加 render は発生しない。宣言された negative control 行の
  一部でも record を欠けば `negative_controls_incomplete` fail filter で
  ineligible とする。`[UNDERSPEC-CAL-D37]`）。**F0 依存候補
  （D4C・harmonic-residual）は fixture の truth F0 ではなく `c3a-f0-selection` で
  選択された F0 candidate の実測出力を instance 単位で入力とする**（fixture truth F0
  を直接使うことは絶対にない。設計正本 §8）。`c3a-f0-selection` 完了（`F0_SELECTION_FROZEN`
  event の記帳）前に `c3b-selection` を開始することはできない（PR #343 第 5 巡採用。
  旧稿の単一 `c3-selection` を F0 選択の先行完了を強制する 2 段に分割））→
  `unseal`（§7 の 5 sha 相互参照検査）→
  `c4-holdout`（選択 1 候補 + B0 × holdout・**holdout 非 control 行の render**（control
  行は `c1-fixtures` で render 済みの artifact を ledger sha256 突合の上で再利用し、
  再 render しない。PR #343 第 5 巡採用）（C1 と**同一の**
  generator determinism 検査を holdout 行にも適用する: 2 回の fresh-process
  render で byte-identical PCM を確認し、両 render を holdout evidence 受理前に
  ledger へ記帳する。`HOLDOUT_EXECUTED_VALID` はこの determinism 検査通過を必須
  とする（PR #343 第 4 巡採用））・gate・terminal
  status cascade）→ `close`（CAMPAIGN_CLOSED・debt_discharged 導出・M6）
- 手続 Gate の単調性を ledger event で強制。各段は ledger 駆動で**再開可能**だが、
  work unit をスキップしてよいのは**ledger に記帳された artifact の sha256 が
  render/measurement ファイルの現在バイト列と一致する場合のみ**。ファイル欠損また
  は sha 不一致は stale 扱いで fail-closed（無言スキップ・無言再 render のいずれも
  禁止）（PR #343 第 2 巡採用）
- meter 反復: within-process 3 call + fresh-process 3（subprocess worker）。並列 worker は
  per-worker JSONL → 直列 append 集約（provenance 契約）
- cost cap / stop rule 超過 → stop event 記帳 + fail-closed 終了。`cli.py main()` は
  `counters.json` を読み戻した**直後**（stage dispatch の前）にも同じ超過判定を
  1 回実行し、既に breach 済みの永続化 counter を再読込しただけで dispatch が
  素通りする（retry のたびに 1 work unit 分課金が進む）ことを防ぐ。この事前
  チェックは既に記録済みの stop_event と同一内容なら重複記帳しない（idempotent。
  round 13 finding #2、`[UNDERSPEC-CAL-D26]`）
- **budget accounting mode**（round 13 finding #3、`[UNDERSPEC-CAL-D27]`）:
  `cost_caps.CostCaps` は `compute`/`storage`/`budget` の 3 値に加え
  `budget_accounting_mode`（closed vocabulary: `"local_zero_cost"` | `"per_unit_fixed"`
  + `"per_unit_fixed"` 時必須の `budget_unit_cost`）を持つ。render/measurement の
  各 work unit はこの宣言に従って `budget_used` へ加算する（`"local_zero_cost"` は
  常に 0、`"per_unit_fixed"` は `budget_unit_cost` を一律加算）— 会計規則が
  存在しないまま `CostCaps.budget` cap が常に非発火という状態を終端させた。mode が
  欠落/閉語彙外なら `BudgetAccountingUndeclaredError`（`BUDGET_ACCOUNTING_UNDECLARED`）
  で dispatch を fail-closed に拒否する。Gate 1 承認 payload の `cost_caps` に
  この mode を含め、凍結 manifest の `frozen_design.cost_caps` へそのまま埋め込む
  ため `manifest_core_sha` の対象。本キャンペーンの承認値は `"local_zero_cost"`
  （`approvals/records/GATE1_DECISION_RECORD.md` §2 参照）
- dry-run（既定）: 計画のみ — work unit 件数（instances 2,280 / renders = campaign 合計
  4,560。**4,560 = 2,280 instances × 2 fresh-process renders**（各 instance を
  generator determinism 検査のため 2 回 fresh-process render し byte 一致を確認する
  ——上記 `c1-fixtures` の determinism 検査・`c4-holdout` の determinism 検査の
  双方がこの 2 render を構成する）。**render 会計は和集合（union）で定まる**（PR #343
  第 5 巡採用。`c1-fixtures` と `c4-holdout` の render 対象 instance 集合は互いに素
  であり二重計上しない）: `c1-fixtures` = (calibration + selection split の
  non-control instances + **全 control instances**) × 2、`c4-holdout` = holdout
  split の **non-control** instances × 2（control instances は `c1-fixtures` で
  render 済みの artifact を再利用し `c4-holdout` では再 render しない）。realized
  split は secret 依存のため確定内訳は freeze
  後に定まり、dry-run 段階は §5.2 の想定 split 比で概算表示。**合計 4,560（=
  2,280 instances × 2）は不変**（PR
  #343 第 1 巡採用。render 内訳の算出式を「instances × 2 fresh-process renders」に
  訂正 = PR #343 第 4 巡採用。render 会計を「union: c1 = (non-control + 全 control) ×2
  / c4 = holdout non-control ×2」として明確化し、control instances の c4 再 render を
  否定 = PR #343 第 5 巡採用） / meter calls 13,680 per impl / selection ≈10^5）と
  cap の照合表を出力

### 6.5 D2 — runner のスライス/再開堅牢化（`[UNDERSPEC-CAL-D79]`。design memo
`design_runner_robustness.md`, 2026-09-03 user-approved「推奨で続行」）

rehearsal（`freeze_execution_13.txt`, 2026-09-02）: production stage は 8h+ 走るが
実行環境が約 4 分ごとにプロセスを kill する。(1) meter_call group の途中で kill
されると次回起動が `StaleMeasurementError`（fail-closed by design、round 9
finding #9）で詰まり、手動 ledger 修復なしに再開できない。(2) instance 境界で
stage を安全に止める手段が無い。(3) resume するたびに ledger 全体を再走査する
（superlinear）。以下 R1〜R3 で対応する。**新規 BlockedCode なし。新フラグ不在時の
挙動は不変**（無中断 run のledger 内容はバイト同一）。

- **R1 — 部分 meter_call group の明示的 operator recovery**: 新規 CLI flag
  `--discard-partial-groups`（`c2-baseline`/`c3a-f0-selection`/`c3b-selection`/
  `c4-holdout` — measure する全 stage）。既定 OFF（従来どおり
  `StaleMeasurementError`）。ON 時、`measure_stage._completed_meter_call_records`
  （`MeterCallIndex.completed_records`）が PARTIAL（`StaleMeasurementError.kind ==
  "partial"` — 一部 repeat key のみ記帳・重複なし）を検出したら、`run_
  measurement_for_instance` は `stop_event`/re-raise の代わりに ledger event
  `{"kind": "meter_call_group_discarded", "row_id", "probe_index",
  "candidate_id", "discarded_repeat_keys": [[repeat_kind, repeat_index], ...]
  (sorted), "discarded_count", "reason":
  "operator_discard_partial_group_after_interrupt", "stage"}` を 1 件記帳し、
  フルグループ（within3+fresh3 全 6 call）を測定・記帳して継続する。
  **reconstruction rule**（`meter_call` を読むあらゆる箇所に適用 —
  `_completed_meter_call_records`/`MeterCallIndex`/selection・holdout・close・
  `caps.cap_counters_from_ledger` 含む）: あるキー K への discard event は K の
  累積をリセットする。K についてその event **以降**に記帳された `meter_call` の
  みが完全性判定・重複判定・scoring の対象。discard 前の記録は ledger には残る
  （append-only）が、以降の完全性判定からは除外される — ただし compute/storage
  counter へは引き続き課金される（work は実際に行われた。round 25
  `[UNDERSPEC-CAL-D57]` の per-attempt charging 方針と整合。`caps.
  cap_counters_from_ledger` は discard event でその key の dedup 集計を
  リセットし、discard 前の attempt と discard 後の remeasure の双方の
  `cpu_seconds` を個別に課金する）。DUPLICATE（`kind == "duplicate"`）はフラグの
  有無に関わらず常に `stop_event`+re-raise のまま（discard 対象は PARTIAL のみ）。
- **R2 — instance 境界でのスライス実行**: 新規 CLI flag
  `--time-budget-seconds N`（float > 0。`c1-fixtures`/`c2-baseline`/
  `c3a-f0-selection`/`c3b-selection`/`c4-holdout`）。既定 `None`＝無制限
  （従来どおり）。dispatch 開始（`campaign/time_budget.py` の `TimeBudget`、
  `time.monotonic()` 基準）から N 秒経過したら新規 instance を dispatch しない
  （既に dispatch 済みの instance——`--workers` の worker pool 分含む——は完走する）。
  instance 境界は render では 1 `(row_id, probe_index)` render 単位、measure では
  1 `(row_id, probe_index)` の全 candidate 測定単位（`run_measure_stage`/
  `_build_f0_by_instance` の outer loop）。超過時、stage は phase transition も
  `stage_summary` ledger event も一切記帳せず exit 0 で
  `{"result": "PARTIAL_SLICE", "stage": <subcommand>, "slice": {
  "time_budget_seconds", "elapsed_seconds", "instances_completed_this_run",
  "instances_remaining"}}` を報告する（`cli._partial_slice_report`）。parent CPU
  は通常の phase transition と同額だけ `cap_counters`/`counters.json` へ課金する
  （`cli.main()` の `finally` 節は `stage_summary` event の記帳だけを PARTIAL_SLICE
  時にスキップし、CPU 課金自体は常に実行——caps がスライスを跨いで正直であり
  続ける）。同一コマンドの再実行は既存 resume 経路で継続し、残 instance が全て
  完了すれば budget が 0 でも通常どおり phase transition + `stage_summary` を行う。
  `c3b-selection`/`c4-holdout` は複数サブフェーズ（F0 測定・render・family 別
  measure）を持つため、それら全てが**同一の** `TimeBudget` インスタンスを共有する
  （`time_budget.SliceStatus.aggregate()` で単純合算するだけで stage 全体の完走
  可否・進捗が正しく合成される——budget 切れ後に呼ばれるサブフェーズは自分の
  instance を 1 件も dispatch せず自分の総数をそのまま `instances_remaining` として
  返す）。
- **R3 — stage 呼び出し 1 回につき ledger 走査 1 回**: `measure_stage.MeterCallIndex`
  が `run_measure_stage`（および `cli._build_f0_by_instance`）の呼び出しごとに
  ledger を 1 回だけ走査してメモリ上に索引化し（`campaign.ledger.entries` への
  唯一のアクセス）、以降は `run_measurement_for_instance` が `Ledger.append()` の
  戻り値を index へ直接反映する（`observe_entry()`、O(1) 増分更新——ledger の
  再取得なし）。`_completed_meter_call_records`（1 回スキャン版）と
  `MeterCallIndex.completed_records`（索引版）は同一の判定関数
  （`_resolve_meter_group`）を共有するため構造的に等価——`test_campaign_measure.py`
  の equivalence test が増分更新と 1 回スキャンの結果一致を complete/partial/
  duplicate/discarded の全パターンで検証する。

#### 6.5.1 追補（Codex PR #345 レビュー採用、2026-09-03）

- **第 1 巡 finding #1（③、`cli.py` ~627）— R3 が実際には C3b/C4 の F0 再開 path
  に効いていなかった**: R3 の docstring は「`_build_f0_by_instance` の呼び出し
  ごとに `MeterCallIndex` を 1 回だけ構築する」と書いていたが、実装は index の
  **構築**をこの関数の責務にしておらず、`meter_call_index` 引数を素通しする
  だけだった。`_run_c3b`/`_run_c4`（`c3b-selection`/`c4-holdout` の CLI
  dispatch）はこの引数を渡さないまま `_build_f0_by_instance` を呼んでいたため
  既定 `None` のままとなり、`_reusable_f0_values_by_process` は instance ごとに
  `measure_stage._completed_meter_call_records`（素朴な 1 回スキャン版）へ
  フォールバックしていた——R3 が解消したはずの superlinear rescan が、resume の
  主経路である C3b/C4 の F0 再利用 path でだけ温存されていたことになる。
  修正: `_run_c3b`/`_run_c4` が F0 再利用ループへ入る直前に
  `measure_stage.MeterCallIndex.build(campaign.ledger.entries)` を 1 回だけ構築し、
  以降の `_build_f0_by_instance` 呼び出し（time-budget あり/なし双方の分岐）へ
  素通しする。`c1-fixtures`/`c2-baseline`/`c3a-f0-selection` はこの F0 再利用
  path を経由しない（c3a はまだ F0 選出前、c2 は `run_measure_stage` を直接
  呼ぶ——`run_measure_stage` 自身は既に呼び出しごとに 1 回だけ index を構築
  しており対象外）ため、監査の結果このパターンは c3b/c4 の 4 箇所（time-budget
  あり/なしの各 2 呼び出し）のみだった。
- **第 1 巡 finding #2（③、`cli.py` ~2376）— `PARTIAL_SLICE` の parent CPU が
  ledger に記帳されず `cap_counters_from_ledger()` が undercount する**:
  `PARTIAL_SLICE` exit は R2 の設計どおり `cap_counters`/`counters.json` へ
  この dispatch の parent CPU を無条件で課金する一方、`stage_summary` ledger
  event は（意図どおり）記帳しない——ゆえに、この CPU 分は ledger のどこにも
  現れなかった。`caps.cap_counters_from_ledger()`/`reconcile_cap_counters()`
  は ledger のみを正本として compute を再構成するため、`counters.json` が
  失われた（あるいは意図的に削除された）状態でのreconciliationは、スライスで
  課金済みだった compute を一切拾えず永続的に undercount する——compute cap を
  実際より低く見せる false-success 経路になり得る。修正: 新規の
  **non-transition** ledger event kind **`slice_summary`**
  （`{"kind": "slice_summary", "stage": <subcommand>, "parent_cpu_seconds":
  <このdispatchのparent CPU全量>, "time_budget_seconds", "elapsed_seconds",
  "instances_completed_this_run", "instances_remaining"}`——後者 4 フィールドは
  CLI report の `slice` dict とフィールド名が一致する）を `PARTIAL_SLICE` exit
  ごとに 1 件記帳する（`stage_summary` とは意図的に別 kind——phase transition が
  一切起きていないことを ledger 自身が表明する）。`caps.
  cap_counters_from_ledger()`/`reconcile_cap_counters()` は `slice_summary.
  parent_cpu_seconds` を `stage_summary.parent_cpu_seconds` と全く同じ規則
  （1 event = 1 dispatch = 1:1 で加算、dedup なし）で合算する。**二重計上なし**の
  根拠: 1 回の CLI dispatch（1 OS process）は必ずどちらか一方のみを記帳する——
  `PARTIAL_SLICE` で終わる dispatch は `slice_summary` を 1 件だけ記帳し
  `stage_summary` は記帳しない、stage を完走させる dispatch は
  `stage_summary` を 1 件だけ記帳し（自分自身の parent CPU 全量のみ——他の
  dispatch の分は含まない）`slice_summary` は記帳しない。ゆえに同一 stage の
  全 dispatch にわたる `slice_summary` の総和 + 最終 `stage_summary` の値は、
  互いに重複なく合算されて `counters.json` が累積してきた総量と一致する。
- **第 2 巡 finding #3（③、`render_stage.py` ~542）— 再開 slice で完了済み
  render unit が毎回フル ledger 再走査 + PCM 再ハッシュされる**: `c1-fixtures`/
  `c4-holdout` の再開 slice では `run_render_stage()` のループが常に先頭の
  unit から再開し、既に完了した unit も 1 件ずつ `render_instance()` へ入る——
  そこで `_recorded_render_sha()` が ledger 全体を再走査し、さらに resume 判定の
  ために PCM ファイルを読んで sha256 を再計算する。完了済み prefix が育つほど、
  固定長の `--time-budget-seconds` slice はその再走査・再ハッシュだけで budget を
  使い果たし、未完了の unit に到達する前に `PARTIAL_SLICE` で終わってしまう
  （繰り返し再開しても実質的な進捗がない）。修正: `run_render_stage()` の
  呼び出しの先頭で ledger を 1 回だけ走査し `{(row_id, probe_index): sha256}`
  の render index（`_render_index_from_ledger`——`MeterCallIndex` と同じ「stage
  呼び出しあたり ledger 走査 1 回」の考え方を render loop 側にも適用したもの）を
  構築する。ループはこの index に載っている unit を `render_instance()` に一切
  渡さず（ledger 再走査も PCM 読み込み/再ハッシュも発生しない）
  `status="skipped_resume"` の `RenderOutcome` を直接組み立てて進む——測定時の
  PCM 整合性検査（`measure_stage._verify_and_load_rendered_pcm`、render された
  unit は必ずこの後段の検査を通る）はそのまま残るため、stale な PCM を fail-open
  で見逃す経路は増えない。`render_instance()` 自身の resume 判定
  （`_recorded_render_sha` + PCM sha 照合）は変更していない——直接呼ぶ他の呼び
  出し元・既存テストの契約は不変。
- **第 3 巡 finding F5（③、`render_stage.py` ~579）— 上記 index skip は
  completing invocation でも一切検証を行わず、削除・破損した PCM の上に
  falsely advance した状態を許してしまう**: 第 2 巡の index skip は「measure
  時に必ず fail-closed する」と述べたが、その fail-closed には前提が要る——
  `c1-fixtures` が一度 FIXTURE_VALID（`fixture_valid` event）へ到達すると、
  `cli._stage_already_complete()` により以降の `c1-fixtures` 再実行は
  `run_render_stage()` を一切呼ばない真の no-op（`NOOP_ALREADY_COMPLETE`）に
  なる（§6.3 系の round 19 finding #3 仕様）。よって、index に載っている unit の
  PCM が render 完了後に削除・破損しても、それを検出するのは後段の C2/measure
  だけになり、しかもその時点では render へ戻る resumable path が既に失われて
  いる——falsely advanced campaign。修正: `run_render_stage()` はループ完走後
  （`completed_all` — `PARTIAL_SLICE` 終了時は対象外、index skip は O(1) のまま）
  かつ stage 遷移（c1 の `fixture_valid` 記帳、または c4-holdout の render
  サブフェーズから measure サブフェーズへの引き渡し）の**直前**に一度だけ、
  この呼び出しが `skipped_resume` にした unit を全数検証する
  （`_validate_skipped_resume_outcomes` — ファイル存在 + sha256 が index の
  記録値と一致するかのみ。`_recorded_render_sha` の再呼び出しなし、ledger
  再走査なし、O(1) 特性は保つ）。不一致があれば遷移させず `stop_event`
  （`reason="RENDER_RESUME_INDEX_INTEGRITY_MISMATCH"`、失敗 unit 全件を
  `units` に列挙）を記帳し `RenderResumeIndexIntegrityError` で fail-closed
  する（`raise` 前に append 済みなので non-zero exit でも記録は残る）。**回復
  経路**: render は `render_root_secret` + row + campaign_id + family +
  split + row_id + probe_index の純粋関数（決定論。モジュール docstring）
  なので、同一 instance を外部から再 render すれば byte-identical な PCM を
  再現できる——これを ledger に触れず `renders/<row_id>/<probe_index>.pcm`
  （+ `.sha256` sidecar）へ書き戻すだけで、次回呼び出しの検証と
  `measure_stage._verify_and_load_rendered_pcm` の両方が再び通り、stage は
  通常どおり遷移する。ledger へ 2 件目の `render` event を追記する経路は
  **不採用**——`_recorded_render_sha()`/`_render_index_from_ledger()` は共に
  同一キーの**最初**の `render` event を採用する実装（ledger 順走査で最初の
  一致を返す）ため、2 件目を足しても既存の全 reader（measure 時の pin 照合含む）
  から無視される。ファイル書き戻しのみが契約を壊さない回復経路。
- **第 3 巡 finding F6（②、`render_stage.py` ~674）— `SliceStatus.
  instances_completed_this_run` が index skip 分まで含めて過大計上する**:
  `len(outcomes)` は新規 render された unit と `skipped_resume` unit の両方を
  含むため、再開 slice は毎回自分の実際の進捗より多く報告し、極端な場合
  「完了済み prefix しか辿らず新規 render が 0 件」の slice でも 0 でない
  progress を報告してしまう。修正: `sum(1 for o in outcomes if o.status ==
  "rendered")` に変更——`instances_remaining` は `len(units) - len(outcomes)`
  のまま不変（未処理 unit の総数という定義自体は正しかったため）。

#### 6.5.2 リハーサル 4 追補（2026-09-03、`freeze_execution_15.txt` +
`rehearsal4/slice_table.out`。D/G 採用、C は運用ルールの明文化のみ）

- **finding D（③、`measure_stage.py` `run_measure_stage`/`_instance_has_
  pending_candidate` 新設）— 測定 stage（c2-baseline/c3a-f0-selection/
  c3b-selection/c4-holdout の measure サブフェーズ、いずれも
  `measure_stage.run_measure_stage()` を共有）は R3 の index 化後も、完了済み
  instance を毎回フル `MeasurementRecord` へ再構成していた**: c3b 実測
  （`rehearsal4/slice_table.out`）で parent CPU が 71.7s→78.9s→84.3s→88.3s と
  スライスごとに増加した一方、pre-loop（`MeterCallIndex.build()` 単体）の
  コストは 0.38s（budget=0.001 probe）で頭打ち——各スライスの新規 render は
  一定 2 instance のみ。原因は R3 の `MeterCallIndex` 導入後も
  `run_measure_stage()` の内側ループが完了済み instance × candidate ごとに
  `run_measurement_for_instance()` を無条件に呼び続けていたこと:
  `meter_call_index.completed_records()`（`_resolve_meter_group` 経由で
  `meter_output_from_dict()` を repeat 数分呼ぶ）が O(1) の index lookup では
  なく、完了済みセルであっても毎回 `MeasurementRecord` を再構成していた——
  `cli._run_c3b`/`_run_c4` は `slice_status.completed_all is False` の
  `records` を丸ごと破棄する（`_partial_slice_report()`）ため、この再構成は
  PARTIAL_SLICE では純粋な浪費だった。同型の `cli._build_f0_by_instance()`
  （F0 再利用ループ）は 1 candidate のみの再構成でありコスト寄与は小さい
  ため対象外——本 finding は `run_measure_stage()` 一本化で c2/c3a/c3b/c4 の
  measure サブフェーズ全てを同時に是正する。修正: 新設
  `MeterCallIndex.is_complete()`（`meter_output_from_dict()` を一切呼ばない
  O(1) presence 判定——PCM 読み込みなし・record 再構成なし）で完了済み
  セルを `run_measurement_for_instance()` を呼ばずに skip する
  （`skipped_complete_cells` へ記録するのみ）。完全な `records` が要る呼び
  出し（`time_budget=None` の単発実行、または slice が完走した completing
  invocation）だけ、ループ後の 1 パス（`_rebuild_skipped_records`）で
  skip したセルをまとめて再構成する——**PARTIAL_SLICE では一切再構成しない**
  （`records` は本来から呼び出し元が破棄するため、空でも契約は変わらない）。
  テスト: 完了済み prefix を歩く PARTIAL_SLICE で `MeterCallIndex.
  completed_records()` の呼び出し回数が 0 であることを直接カウントする
  regression（`test_run_measure_stage_partial_slice_skips_completed_prefix_
  without_reconstruction`）と、大きな完了済み prefix + 極小 budget でも
  budget+1 instance 分の時間で完走する regression
  （`test_run_measure_stage_large_completed_prefix_partial_slice_stays_fast`）
  を追加。
- **finding G（②、`render_stage.py`/`measure_stage.py` の `SliceStatus.
  instances_remaining` 算出）— budget が最初の instance の dispatch 前に
  尽きた invocation は `instances_remaining` を過大報告し、非増加が
  保証されない**: `rehearsal4/slice_table.out` の c3b、budget=0.001 行:
  `instances_remaining` が直前の 77 から 85 へ後退（8 件の完了済み instance
  を無視した過大報告）。原因は両モジュールとも「この呼び出しが実際に歩いた
  instance 数」ベースの引き算だったこと——`render_stage.run_render_stage()`
  は `len(units) - len(outcomes)`（`outcomes` は budget 切れ前に歩いた分のみ）、
  `measure_stage.run_measure_stage()` は `total_instances -
  instances_completed_this_run`。どちらも budget 切れが 1 instance 目の
  境界検査より前に来ると `outcomes`/`instances_completed_this_run` が空/0の
  まま確定し、`instances_remaining` が「このスライスで新たに歩いた分」を
  真の残数と取り違えて全 instance 数まで跳ね上がる。修正: 両モジュールとも
  「この呼び出し後に完了しているか」を index から直接数え直す——
  `render_stage`: 呼び出し先頭で構築した `completed_units`（ledger 由来、
  この呼び出しの新規分は含まない）+ このスライスの新規 render 数
  （`newly_rendered_count`）を `len(units)` から引く。`measure_stage`: 新設
  `_instance_has_pending_candidate()` で `sorted_instances` 全件を
  `MeterCallIndex.is_complete()`（PCM 読み込みなし、O(1)）だけで 1 パス
  再判定する——この呼び出しがループ中に**歩かなかった** instance も含めて
  真の未完了数を数える（budget が最初の境界検査で尽きても、この pass 自体は
  必ず実行される。O(1) presence 判定のみのため、既に完了しているとの判定に
  必要な reconstruction コストは発生しない = finding D の是正と両立）。
  `instances_completed_this_run` は変更なし（finding G は `remaining` のみが
  対象——`completed_this_run` の意味論は既に「このスライスが歩いた instance
  数」で一貫しており、rehearsal4 でもこの値自体の誤りは観測されていない）。
  テスト: 0.001s budget を折り返し済みの半完了キャンペーンへ与え、
  `instances_remaining == true_remaining` かつ `instances_completed_this_run
  == 0`（非増加）であることを固定する regression を render_stage/
  measure_stage 双方に追加
  （`test_c1_render_time_budget_remaining_matches_true_completed_state`/
  `test_run_measure_stage_time_budget_remaining_matches_true_completed_state`）。
- **finding C（境界宣言、docs のみ。コード変更なし）— `--time-budget-
  seconds` は dispatch 開始境界のみを縛るため、wall time は「残り budget +
  in-flight instance の最長所要時間」に達し得る**: `rehearsal4/
  slice_table.out` の c3b、budget=150 の 4 スライスで wall time 208.8〜225.1s
  （budget に対し 39〜50% 超過）。`--workers 3` 時 c3b の 1 instance は
  約 70s（`--workers 1` では約 105s）——R2 の契約どおり in-flight instance は
  budget 切れ後も完走するため（本 memo 上記 R2「instance の途中では止めない」）
  これは design 上の正しい振る舞いであり finding D/G のような bug ではない。
  **operator rule**（このリポジトリの実行環境が課す外部プロセス寿命制限
  ── 例: 本セッションの Bash ツール 240s ── の下で `--time-budget-seconds`
  を選ぶ運用者向け）: `--time-budget-seconds` は
  `外部プロセス寿命制限 - その stage の最長 in-flight instance 所要時間`
  以下に設定する。c3b の実測値（`--workers 3` で約 70s/instance、
  `--workers 1` で約 105s/instance）を用いる場合、240s 制限下では
  `--workers 3 --time-budget-seconds <=170` または
  `--workers 1 --time-budget-seconds <=135` が安全側の上限（rehearsal4 は
  budget=150 かつ `--workers 1` 相当の設定で実際に 240s 制限を超過し
  SIGTERM された——`slice_table.out` c3b_s5 行）。
- **completing invocation の ledger event（本 memo 上記 R2/finding G 補足
  ── コード変更なし、意味論の確認のみ）**: stage が完走する（budget 切れず
  完走、または budget 自体を渡さない）invocation は phase transition と
  同時に **`stage_summary`**（その invocation 自身の parent CPU 全量）を
  記帳し **`slice_summary`** は一切記帳しない——`slice_summary` は
  §6.5.1 finding #2 のとおり `PARTIAL_SLICE` exit（phase transition なし）
  専用の non-transition event であり、両者は 1 dispatch につきどちらか
  一方のみが記帳される（本 memo 上記「二重計上なしの根拠」参照）。
