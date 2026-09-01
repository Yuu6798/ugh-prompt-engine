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
