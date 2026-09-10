# Design Memo — RUN10-CAL リセット: Meter Bench の導入と校正基盤の凍結（v0）

作成: 2026-09-10 / 起草: Claude Code（設計） / 実装想定: Sonnet または Codex
形式: `AGENTS.md` §1 Design Memo

---

## 0. 一枚要約（これだけ読めばよい）

**問い**: VoiceGenesis の meter（tilt / aperiodicity / formants / transition / F0）は、
正解が分かっている合成音に対して正しい値を返すか。

**現状**: 返さない。7 本中 5 本が 3 回の campaign（計算 約 40 時間）で
`NOT_EVALUABLE` / `DIAGNOSTIC_ONLY`。TILT は計器の構造欠陥が特定済み
（`tilt_harmonic.py:89` `harmonic_amplitudes_db` が `k·f0` の bin を固定で読み、
ピーク探索をしないため F0 誤差が倍音次数 k 倍で増幅される）。

**診断**: 校正 campaign は「計器が正しいかを判定する装置」であって「計器を直す
装置」ではない。計器が壊れている段階で判定装置を 3 回回し、装置の堅牢化に
75,660 行（tests 含む）と設計文書 5,665 行を積んだ。装置は内部無矛盾だが、
問いには近づいていない。

**処方**: 三つを分離する。

| 層 | 目的 | 手段 | 統治 |
|---|---|---|---|
| 1. 計器開発 | 計器を直す | **Meter Bench**（新設・小さい・速い） | なし（pytest と JSON のみ） |
| 2. 計器受入 | 「動く」と宣言する | Bench の固定 case 集合 + `criteria.yaml` | PR レビューのみ |
| 3. 確定 campaign | holdout で確定し負債を返す | 既存 `campaign/` 基盤（**凍結**） | 既存 Gate（操作別直接承認） |

**即時停止**: 新 campaign / 再 freeze / 設計 v1.5 以降の起草 / D 台帳への追記
（D117 で凍結）/ Tier F（下記）へのコード変更。Bench で claim-critical 3 meter が
PASS するまで再開しない。

---

## Phase

RUN10-CAL（`VG-METER-CAL-DEBT`）の再編。負債の terminal status は
campaign `862dec28` のまま不変（`debt_discharged=false`）。本 memo は負債を返済
しない。返済の前提となる「計器が動く」状態を最短で作る。

サブフェーズ（各 0.5–2 日）:

| Phase | 内容 | 規模 |
|---|---|---|
| P0 | 文書リセット: README 一枚化 + 歴史文書の格下げ + STATUS 行書換 | 0.5 日 |
| P1 | Bench 骨格 + TILT case 集合。**現行 TILT が FAIL する JSON を commit** | 1 日 |
| P2 | TILT 計器修正（ピーク探索）。Bench PASS の JSON を commit | 1 日 |
| P3 | APERIODICITY + F0 の case 集合（初回は FAIL 見込み、結果を commit） | 1 日 |
| P4 | FORMANTS + TRANSITION の case 集合（同上） | 1 日 |
| P5 | 計器開発（meter ごと **2 セッション上限**。超えたら「不成立」で claim 範囲外へ = User 裁定） | meter ごと |
| P6 | 確定 campaign（3 meter が同一 git sha で PASS したときのみ 1 回。基盤変更なし、操作別直接承認） | 既存手順 |

本 memo の実装対象は **P0–P2**。P3 以降は P2 完了後に同形式で 1 枚ずつ起こす。

## Goal

1. 計器の「動く / 動かない」を **5 分以内・freeze なし・承認なし** で判定できる
   Bench を作る。
2. TILT 計器を直し、Bench PASS を実測で示す（v1.4 §Y4 の発見を修正まで運ぶ）。
3. 校正基盤（campaign / freeze / provenance / approvals）を「確定専用・変更凍結」
   に格下げし、これ以上育てない。
4. README を「何を示したいか / 必要な計器 / 各計器の『動く』の定義 / いま動く計器」
   の一枚に戻す。

## Acceptance Criteria

**P0**
- [ ] `voice_genesis/calibration/README.md` 冒頭 60 行以内に上記 4 項を記述。
      既存本文は「## 歴史文書（確定 campaign 基盤の仕様）」節へ格下げ（削除しない）。
- [ ] 同 README に「統治予算」節（本 memo §統治予算 を逐語転記）。
- [ ] `.claude/memory/STATUS.md` の RUN10-CAL 行を 1 行に置換: 「Bench 導入中。
      campaign / freeze / 設計改訂 / D 追記は停止。terminal status は 862dec28 不変」。
- [ ] D 台帳（IMPLEMENTATION_MAP / DESIGN v1.4 の D 節）は **D117 で凍結**と README に明記。
      Bench の発見は D 番号を発番せず `meter_bench/results/*.json` に記録する。

**P1**
- [ ] `python -m voice_genesis.meter_bench --meter M2_SPECTRAL_TILT --out <dir>` が
      CPU で **120 秒以内**に終了し `<dir>/M2_SPECTRAL_TILT.json` を書く。
- [ ] JSON は `{"meter", "git_sha", "claimable": false, "cases": [...], "verdict"}`
      を持ち、`claimable` は常に `false`（定数。設定不可）。
- [ ] `pytest voice_genesis/meter_bench -q` が **300 秒以内**に pass
      （Bench 自体のテスト。計器の PASS/FAIL はテスト成否に含めない）。
- [ ] import 境界テスト: `voice_genesis/meter_bench/` 配下の全 .py を ast 走査し、
      `voice_genesis.calibration.campaign` / `provenance` / `approvals` / `c0_freeze` /
      `c0_validate` / `splitter` / `gates` / `selection` / `e_use_table` / `cost_caps` /
      `authorization_guard` を import していないことを assert。
- [ ] 決定論テスト: 同一入力で 2 回実行した JSON が `timestamp` 以外で一致。
- [ ] `meter_bench/results/M2_SPECTRAL_TILT.json` を commit し、`verdict: "FAIL"` と
      `f0_error_pct != 0` の case で `|err| > tolerance` が読めること
      （= v1.4 §Y4 (1) の再現。**FAIL を隠さない**）。

**P2**
- [ ] `candidates/impl/tilt_harmonic.py` に `harmonic_amplitudes_db_peak()` を**追加**
      （既存関数は不変）。`k·f0` の周囲 `±max(1 bin, k·f0·tol)`（`tol` 既定 0.03）で
      局所最大 bin を探索し、その bin で放物線補間する。
- [ ] `candidates/registry.py` に新 candidate（例 `M2T-PEAK-OLS-K8-HANN` /
      `M2T-PEAK-THEILSEN-K8-HANN`）を**追加**。既存 candidate は不変
      （歴史 campaign が registry sha を pin しているため、変更でなく追加）。
- [ ] Bench の TILT case 集合で新 candidate が `verdict: "PASS"`。結果 JSON を commit
      （P1 の FAIL JSON は履歴として git に残る。上書きでよい）。
- [ ] 旧 candidate の FAIL / 新 candidate の PASS が同一 JSON 内で並ぶ
      （candidate ごとに verdict を持つ）。

## Implementation Approach

### Meter Bench（新設 `voice_genesis/meter_bench/`、上限 800 行 tests 込み）

```
voice_genesis/meter_bench/
  __init__.py
  cases.py        # BenchCase(meter, family, gen_params, truth, check) と meter 別 case 集合
  run.py          # run(meter) -> BenchReport; __main__ 相当の CLI
  criteria.yaml   # meter 別許容値（v0 値は下表。変更は Bench 結果を根拠に PR で）
  results/        # commit する JSON（meter ごと 1 ファイル、最新のみ）
voice_genesis/meter_bench/tests/
  test_bench.py   # import 境界 / 決定論 / CLI 終了コード / JSON schema
```

再利用するもの（Tier K）: `calibration/fixtures/generators/*`（`render_row` または
family 生成器を直接呼ぶ）、`calibration/candidates/adapter.py::measure`、
`calibration/candidates/registry.py`、`calibration/vocab.py::MeterId`、
`calibration/streams.py`（決定論 RNG）。`campaign/diagnose.py` は参照してよいが
import しない（diagnose は `measure_stage` 経由で campaign 層に依存しているため）。

**Case の形**: 1 case = 生成パラメータ + 正解値 + 判定式。判定式は 3 種のみ:
`abs_error(tol)` / `monotone(kendall_tau_min)` / `no_fire()`（負例で値を出さない、
または `hnr_acf_db` 等の検出述語が偽）。これ以外の判定を足さない。

**TILT case 集合（P1）**:

| 軸 | 値 |
|---|---|
| `slope_db_per_oct` | −3, −6, −9, −12 |
| `f0_hz` | 110, 220, 440 |
| `f0_error_pct`（計器へ渡す f0 を真値からずらす） | 0, ±1, ±2, ±3 |
| 判定 | `abs_error(tol=1.0 dB/oct)` |
| 負例 | NOISE_ONLY, SILENCE → `no_fire()` |

計 84 正例 + 2 負例。1 case は 1 秒音 1 本なので合計 2 分以内に収まる見込み
（超える場合は `f0_hz` を 220 のみに縮める。P1 で実測して決める）。

**criteria.yaml v0 値**（P3/P4 用も先に置く。数値は提案で、Bench 実測で改訂）:

| meter | 判定 | v0 値 |
|---|---|---|
| M2_SPECTRAL_TILT | abs_error | 1.0 dB/oct |
| M2_APERIODICITY | monotone | τ ≥ 0.9（noise_fraction 0→0.6 の 6 点） |
| F0 | abs_error | 10 cents |
| M3_FORMANTS | abs_error | F1/F2 各 5 % |
| M5_TRANSITION | abs_error | join_time 10 ms |
| 全 meter 負例 | no_fire | NOISE_ONLY / SILENCE |

### TILT 修正（P2）

`harmonic_amplitudes_db_peak(signal, sr, f0_hz, k_max, window_name, tol=0.03)`:
`target_bin = k·f0/bin_hz`、`half = max(1, ceil(k·f0·tol/bin_hz))`、
`idx = argmax(log_mag_db[target_bin−half : target_bin+half+1])`、`idx` で
`_parabolic_interp_db`。`_regression_inputs` 以降は共用。

### 校正基盤の凍結（P0、コード変更なし）

| Tier | 対象 | 扱い |
|---|---|---|
| K（保持・開発対象） | `candidates/impl/*`, `candidates/adapter.py`, `candidates/registry.py`（追加のみ）, `fixtures/generators/*`, `fixtures/axes.py`, `vocab.py`, `streams.py`, 新 `meter_bench/` | 通常開発 |
| F（凍結・確定専用） | `campaign/*`, `c0_freeze.py`, `c0_validate.py`, `provenance.py`, `approvals.py`, `splitter.py`, `gates.py`, `selection.py`, `e_use_table.py`, `cost_caps.py`, `authorization_guard.py`, `tools/*`, `.github/workflows/vg-campaign-authorization-guard.yml` | 変更禁止。例外は (a) P6 の確定 campaign で踏んだ実バグ (b) セキュリティ。新 Gate / 承認種別 / 台帳形式の追加禁止。**ファイル移動禁止**（歴史 manifest が path+hash を pin） |
| R（歴史文書） | `DESIGN_VG_METER_CAL_DEBT_v1.0〜v1.4.md`, `GATE_REVIEW_BRIEF_v1.md`, `IMPLEMENTATION_MAP_v1.md`, `approvals/records/*` | 編集しない。README の「歴史文書」節からリンクのみ |

`campaign/diagnose.py` は Tier F に含める（削除も改修もしない）。Bench が P1 で
動いた時点で README に「diagnose は Bench に置き換え」と 1 行書く。

### 統治予算（README へ逐語転記）

1. 科学的発見 1 件につき設計改訂は 1 版まで。手続き上の失敗には規約の追加でなく
   既存規約の削除で応える。
2. レビューは PR あたり 10 巡で打ち切る。残りは境界宣言にまとめて次 PR へ
   （`CLAUDE.md` 既定の実効化。超過を常態にしない）。
3. `meter_bench/` は tests 込み 800 行上限。承認・nonce・台帳・封印を持ち込まない。
4. 確定 campaign は「claim-critical 3 meter が同一 git sha で Bench PASS」のときのみ、
   その PASS 集合につき 1 回。操作別の直接承認を得る。
5. Bench の PASS は校正証拠ではない（`claimable: false`）。計算資源を使う許可に
   すぎない。
6. D 台帳は D117 で凍結。以後の発見は Bench 結果 JSON と PR 本文に書く。

## Risks

| リスク | 対処 |
|---|---|
| Bench PASS が「校正済み」と誤読される（AI が最も起こしやすい） | JSON の `claimable:false` 定数 + README の明文。campaign 入場条件としてのみ参照 |
| criteria.yaml の v0 値が恣意的 | v0 と明記。変更は Bench 結果 JSON を根拠に PR。値を緩めて PASS させる変更は禁止（README） |
| Bench が第 2 の campaign 基盤に育つ | 800 行上限 + import 境界テスト + 判定式 3 種限定 |
| 歴史 campaign が impl / registry の hash を pin している | 既存関数・candidate を変更せず追加のみ |
| 実装者が campaign 層の便利関数へ手を伸ばす | Scope OUT の列挙 + ast テストで機械的に拒否 |
| TILT の修正だけでは PASS しない（窓長・k_max 由来の誤差） | P2 で FAIL なら結果 JSON を commit して P2 を閉じ、原因を次 memo へ。P2 内で計器を作り込まない |
| FORMANTS / TRANSITION が P5 の上限内で直らない | 「不成立」として claim 範囲外へ。返済条件の再定義は User 裁定（§15 preregistration） |

## Test Strategy

- `voice_genesis/meter_bench/tests/test_bench.py`: import 境界（ast）/ 決定論 /
  CLI 終了コード 0 / JSON schema（キー集合と `claimable is False`）。
- TILT 既知傾斜テスト: 合成倍音列 `A_k = slope·log2(k)` を Bench の生成器で作り、
  新 candidate の推定が tol 内（`f0_error_pct=±2` を含む）。旧 candidate は
  `f0_error_pct=0` のみ tol 内であることを**明示的に**テスト（退行検知でなく、
  欠陥の記録として）。
- 実行時間: `pytest voice_genesis/meter_bench -q` を CI の既存 `test-calibration`
  job に **追加**（新 job を作らない）。
- `ruff check .` / 既存 `pytest -q --tb=short` 全 pass。

## Scope

**IN**
- 新規: `voice_genesis/meter_bench/**`
- 追加のみ: `voice_genesis/calibration/candidates/impl/tilt_harmonic.py`
  （新関数）, `voice_genesis/calibration/candidates/registry.py`（新 candidate）
- 書換: `voice_genesis/calibration/README.md`（冒頭一枚化 + 歴史文書節 + 統治予算節）,
  `.claude/memory/STATUS.md`（RUN10-CAL 行 1 行化）
- 1 行追加: `.github/workflows/ci.yml`（`test-calibration` job に bench tests のパス）,
  `CLAUDE.md` 設計ドキュメント索引（本 memo 1 行）

**OUT（変更禁止）**
- Tier F 全ファイル（上表）と `voice_genesis/calibration/campaigns/**`
- Tier R 全文書（DESIGN v1.0–v1.4 / GATE_REVIEW_BRIEF / IMPLEMENTATION_MAP / approvals/records）
- `vocab.py` の `CLAIM_CRITICAL_SET`（返済条件の変更は User 裁定 + preregistration）
- 既存 candidate の `implementation_ref` / パラメータ
- `fixtures/generators/*` の生成式（Bench はパラメータを渡すだけ。正解値の定義を変えない）

## Allowed Dependencies

追加なし。`numpy` / `scipy` / `pyyaml` は既存。

## Required Outputs

- Completion Summary（`AGENTS.md` §2）。
- `meter_bench/results/M2_SPECTRAL_TILT.json`（P1: FAIL、P2: PASS。両方 git 履歴に残す）。
- README 冒頭一枚（P0）。
- `ruff` / `pytest` の実行ログ（Bench の実測所要秒を含む）。

## Done When

- P0–P2 の Acceptance Criteria 全項が PR 上で確認でき、マージされている。
- `python -m voice_genesis.meter_bench --meter M2_SPECTRAL_TILT` が新 candidate で
  PASS、旧 candidate で FAIL を同一 JSON に出す。
- P0 以降、Tier F への変更コミットが 0 件、D 発番が 0 件、設計改訂が 0 版。
- 次の memo（P3）が本 memo と同じ形式・同じ分量以下で書けている。

---

## 付録 A: 今回の反省と本 memo の対応

| 反省（2026-09-06 / 09-10） | 本 memo の対応 |
|---|---|
| 計器開発と校正を同じループに入れた | 層 1（Bench）と層 3（campaign）を分離。campaign は PASS 後のみ |
| 探索段階を本番経路でしか回せない | Bench は freeze / 承認 / 台帳なし、5 分以内 |
| 失敗のたびに規約と装置を足した | Tier F 凍結 + 統治予算 1（規約は削除で応える） |
| 承認の粒度が装置の厳格さに追いつかない | 装置を使う回数を「PASS 集合につき 1 回」に減らす。承認は既存 Gate のまま |
| 「完走」「全マージ」を進捗と報告した | 進捗単位を「Bench PASS した meter の本数」に固定 |
| 設計文書が 5 版 2,341 行に膨張 | 本 memo は 1 枚要約 + 標準形式。次 memo は同量以下 |

## 付録 B: 規模の参考値（origin/main, 2026-09-10）

| 対象 | 行数 |
|---|---|
| `voice_genesis/calibration/**/*.py`（tests 込み） | 75,660 |
| うち meter 実装 `candidates/impl/*` | 1,047 |
| うち fixture 生成器 `fixtures/generators/*` | 1,017 |
| うち `campaign/*` | 約 14,400 |
| うち `c0_freeze.py` + `c0_validate.py` | 5,915 |
| 設計・運用文書（.md） | 5,665 |
| 本 memo が新設する上限 | 800 |
