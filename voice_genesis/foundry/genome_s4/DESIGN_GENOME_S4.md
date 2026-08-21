# DESIGN — Genome Architecture S4

**Multi-Gene Co-expression & Retention PoC / 実装契約 v1.0**

本ディレクトリは User 提示の設計・実行契約
`VoiceGenesis Genome Architecture S4 — Claude 実装専用設計・実行契約 v1.0`
（策定 2026-08-21）の実装である。以下は **実装側の対応表と解釈ノート**であり、
契約そのものを上書きしない。契約と本書が食い違った場合は **契約が勝つ**。

## 1. S4 が答える問い

> F0 gene と Duration gene を同時に ON にしたとき、片方が片方を消さず、
> Ritsu Identity を保ったまま、両方の増分効果が出力と耳に残るか？

対象は **同一 Performance donor（PJS）内の複数 gene 共発現**。
multi-parent crossover・完全 disentanglement・自然さ改善は目的にしない（契約 §1 / §2）。

## 2. 入力として凍結する既知事実

| 正本 | 凍結事実 |
|---|---|
| `genome_s3/results/s3_results.json` | overall `PASS` / f0・duration・energy・release = `SUPPORTED` |
| `genome_s35/results/s35_results.json` | overall `S4_READY` / f0・duration = `PERCEPTIBLE_CANDIDATE` |

S4 が持ち込む gene は **`f0` と `duration` だけ**。Energy / Release は記録上
保持するが、S4 の入力・Gate・出力条件へ入れない（契約 §0.2 / §2）。
S4 はどの結果でも S2 / S3 / S3.5 の判定を変更しない（契約 §16）。

## 3. 実験デザイン — 2×2 factorial

| 条件 | F0 | Duration |
|---|---:|---:|
| `B0` | OFF | OFF |
| `F` | ON | OFF |
| `D` | OFF | ON |
| `FD` | ON | ON |

増分比較は 4 本（契約 §3）:

```text
F0       単独 : B0 -> F         F0       Duration 背景 : D -> FD
Duration 単独 : B0 -> D         Duration F0       背景 : F -> FD
```

`E` / `R` / `FE` / `DR` / `FDE` / `FDER` は作らない（`s4_spec.CONDITIONS` が 4 条件のみ）。

## 4. モジュール対応表

| ファイル | 契約 | 責務 |
|---|---|---|
| `s4_spec.py` | §19 | 凍結定義のみ（条件・閾値・verdict enum・metric 名・context 名・選択 hash）。I/O と音響処理を持たない |
| `s4_runner.py` | §5 / §6 / §19 | 正本の一回読み・来歴 Gate G0-1〜G0-5・candidate 導出・B0/F/D/FD 生成・S3 replay・metric 記録・same/cross process 反復 |
| `s4_gates.py` | §7〜§16 | 構造 Gate・決定論 Gate・replay Gate・§9 組合せ Gate・§10 pair verdict・§11 overall・§15 人間 Gate 判定 |
| `s4_blind.py` | §13〜§15 | 決定論 pair 選択・6 問 pack・private key + commitment・回答凍結・採点 |
| `s4_report.py` | §17 / §20〜§22 | `s4_results.json` / `S4_RECORD.md` の生成、原子的公開、PASS 時の freeze bundle |

`planb/` `planb_real/` `genome_s3/` `genome_s35/` は **read-only import**（契約 §18）。

## 5. 実行

```bash
# Phase A — Mechanistic（clean worktree が前提。§5 G0-5）
python voice_genesis/foundry/genome_s4/s4_report.py

# 機械 Overall PASS のときだけ results/ear_pack/ に 6 問が出る。
# Phase B — User が results/answers.template.json を写して answers.json に回答。
# Phase C — Final
python voice_genesis/foundry/genome_s4/s4_report.py --phase-c
```

終了コード: `0` = PASS / `1` = 不成立 / `3` = BLOCKED。

### 素材の所在（Phase A の前提）

`planb_real/results/ladder_manifest.json` は S2 走行機の **絶対パス**で
Ritsu / PJS の lab・wav を指す。Phase A は G0-4（source pin 照合）でこの実体を
読むため、**そのパスが解決できる環境**でしか走らない。別マシンで回す場合は、
`source_manifest.json` に pin されたアーカイブ（`archive_sha256` 一致）を取得し、
manifest が指す位置へ展開する。素材が無い環境では設計どおり `BLOCKED` で止まり、
S4 の結果は出さない（契約 §4 / §24）。素材の取得・配置は machine 依存作業であり、
S4 実装側で母集団を縮小して回避しない。

## 6. 解釈ノート（契約に明示が無い箇所の fail-closed な読み）

実装が独自判断で契約を広げた箇所は無い。以下は**書かれていない細部を
どちらへ倒したか**の記録であり、閾値・母集団・metric・問数は一切変えていない
（契約 §24）。

1. **S3 replay 不一致の扱い** — 契約 §6 は「1 件でも不一致なら `BLOCKED`、
   S4 の結果を出さない」、§10 は pair verdict の `FAILED` 要因、§16 は overall
   `FAILED` 要因として同じ事象を挙げる。実装は **§6 と §23-19 を優先**し、
   `run_all` が走行を止めて `BLOCKED` を記録する。`s4_gates.replay_gate` /
   `pair_verdict` の `FAILED` 経路は、Gate を直接呼んだ場合の防御として残す。
2. **clean worktree 判定の除外範囲** — 判定対象から外すのは
   `voice_genesis/foundry/genome_s4/results/` だけ。**その走行が生む成果物を
   その走行の前提条件にはできない**ため。コード・docs・他モジュールの汚れは
   除外しない（`s4_runner.WORKTREE_EXCLUDE_PREFIXES`）。
3. **G0-3 に input manifest を足した** — 契約は S3.5 → S3 の SHA 連結だけを
   要求するが、S3 が記録する `input_manifest_sha256` と実 manifest も突き合わせる。
   これを見ないと「S3 と別の凍結入力から素材を組み立てながら S3 の pin を来歴と
   して提示する」経路が開く（契約 §25 B「source / output / record が食い違う
   具体経路」は必ず採用）。判定閾値は増やしていない。
4. **`NOT_EVALUABLE` の会計** — evaluable にも combinable にも数えない。
   support_ratio の分母は `COMBINABLE + UNSUPPORTED`（契約 §11「COMBINABLE /
   evaluable」）。
5. **機械 FAIL の overall 写像** — 構造 / 決定論 / replay の**違反**は
   `FAILED`、違反ではない不足（support_ratio 不足など）は `NOT_ESTABLISHED`
   （契約冒頭「複合発現だけを `NOT_ESTABLISHED` とする」）。
6. **private key を最終 transaction に載せた** — 先に書くと後段失敗時に
   「新しい key + 古い manifest」が残り commitment 検証が通らなくなる（§22）。
7. **G0-4b: donor 側スペクトルを凍結 manifest へ接続した**（`assert_donor_pin`）—
   契約 §5 G0-4 が要求する pin は `identity_sha256`（Ritsu の sp/ap）と
   `performance_sha256`（設計上スペクトルを持たない 1-D 制御）だけで、
   `identity_margin_db` のもう一方の入力である **donor 側包絡（`donor_core`）が
   どちらにも入らない**。donor 素材が変われば §9.5 の `identity_margin_db(FD) > 0`
   が反転し、`COMBINABLE` と S4 PASS が偽になる。凍結 manifest は同じトグル状態で
   測った `identity_lsd_db` / `donor_lsd_db` を rung として持つので、
   **toggle label で引いて**突き合わせる（rung 名の並びに依存しない）。
   比較精度は manifest 自身の記録精度（4 桁）であり、新しい許容誤差ではない。
   pin が無い条件は検証スキップにせず BLOCKED。metric は増やしていない
   （記録するのは §8 が既に定義している差の内訳）。
8. **回答を「聴いた pack」へ結び付けた**（`verify_answer_binding`）— trial_id は
   `T001`…と固定なので、Phase A を回し直して salt と正解対応が変わっても
   **古い `answers.json` がそのまま採点できてしまう**（偽の不成立、確率次第で
   偽の PASS）。`answers.template.json` に `key_commitment` を刻み、Phase C が
   一致を要求する。
9. **採点前に pack の音を再ハッシュする**（`verify_pack_audio`）— commitment が
   守るのは「実験者が回答後に正解を変えないこと」だけで、Phase A の後に WAV が
   差し替わった・壊れた・消えた場合は素通りする。`blind_manifest.audio_sha256` の
   全ファイルを読み直して突き合わせ、欠落・不一致・pin 自体の欠落は BLOCKED。

7〜9 は PR #299 の自動レビュー（P1 × 3）由来。いずれも契約 §25 A（verdict が
誤る具体経路）/ B（source・output・record の食い違い）/ E（blind 破壊）に当たるため
**実害基準で採用**した。閾値・母集団・対象 gene / context・問数・metric は変えていない。

## 7. 既知の範囲外事象（`observed_but_out_of_scope`）

修正しない。記録だけ行う（契約 §2）。

- ABX の `X` は `A` か `B` と byte-identical なので、聴取者が 3 ファイルを
  sha256 で突き合わせれば聴かずに正答できる。commitment 方式が守るのは
  「実験者が回答後に正解を変えないこと」であって聴取者の自己申告ではない
  （S3.5 と同じ既知の性質。プロトコル変更は契約 §24 で禁止）。
- ABX 4 問の偶然一致確率は 1/16。本 Gate は統計的有意差ではなく工学的進行 Gate
  である（契約 §15.1 が明記）。

## 8. テスト

```bash
ruff check .
python -m pytest voice_genesis/foundry/genome_s4/tests -q
```

`tests/test_genome_s4.py` は契約 §23 の 43 要件を番号で追える形に並べる
（`test_01_…` 〜 `test_43_…`。補助検査は `test_NNb_…`）。unit は実コーパスを
必要とせず、実素材が要る経路は `planb_real/results/ladder_manifest.json` と
corpus が揃っているときだけ走る。
