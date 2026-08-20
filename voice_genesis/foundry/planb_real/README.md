# planb_real/ — Real-Corpus PoC（Ritsu Identity × PJS Performance）

`VoiceGenesis Genome Architecture / Real-Corpus PoC Execution Instruction v1.0`
の実行ハーネス。surrogate PoC（[`../planb/`](../planb/)）で成立した分離機構を
実コーパスへ当てるための配管で、**結果は surrogate とは別 ledger** に持つ。

- 実験契約: [`DESIGN_PLANB_REAL_poc.md`](DESIGN_PLANB_REAL_poc.md)
- 現時点の状態: [`REAL_CORPUS_POC_RECORD.md`](REAL_CORPUS_POC_RECORD.md)

## 現在の到達点（第 1 走行 2026-08-20）

```
G-MATERIAL      PASS      リツ歌声DB Ver2.0.2 / PJS ver1.1 を取得・SHA 固定・repo 外配置
G-LICENSE       PASS      配布物同梱の専用規約を正本として固定・全項目確認
G-CENSUS        PASS      Ritsu 110 対 / PJS 100 対、probe 4/5 種が両側に実在
G1-determinism  PASS      8 probe ペアで R0–R4 の sha256 再現
G3-intervention FAIL      軸の交絡（R2 で f0 軸、R1 で energy 軸が動く）
G4-donor        PASS      PJS テクスチャへの単調接近なし
G5-attribution  PASS
G6-identity     PASS      全段で出力は PJS より Ritsu に近い
G7-TRF          FAIL      8 ペア中 2 ペアが不通過（内訳は record 参照）
G-ear           BLOCKED   聴感未実施（機械で代替しない）
                                            success_level = S1_REAL_RENDER
```

**主要な実測**: 事前登録 TRF 主軸は **終端 /ri/ でだけ +5.6〜+6.2 dB 立ち**、
語中 /ri/ と終端 /N/ では立たない。改善は **R4 でのみ**起きる（R1/R2/R3 では動かない）
= プラン §5 Case C 型。詳細 = [`REAL_CORPUS_POC_RECORD.md`](REAL_CORPUS_POC_RECORD.md)。

instruction §13 に従い、**S2「部分分離が成立した」とはまだ言わない**（G3 が
「独立に交換できている」ことを示せていないため）。

## 実行

```bash
pip install pyworld soundfile

# 1. 資材の来歴固定（archive / 展開先 / 同梱規約 の 3 点が要る）
python pr_run.py acquire \
  --ritsu-archive  <path>.zip --ritsu-extracted <dir> --ritsu-license <bundled_terms> \
  --ritsu-version "Ver2.0.2" --ritsu-origin "https://www.canon-voice.com/voicebanks/" \
  --pjs-archive    <path>.zip --pjs-extracted   <dir> --pjs-license   <terms_snapshot> \
  --pjs-version "ver1.1" --pjs-origin "<project page>"

# 2. 許諾台帳を人が記入（自動では埋めない）
$EDITOR results/LICENSE_LEDGER.json

# 3-6. census -> ラダー -> ゲート
python pr_run.py census
python pr_run.py ladder --max-pairs 4
python pr_run.py gates

python pr_run.py status     # いつでも現在地
```

いずれのコマンドも **fail-closed**。BLOCKED / FAIL のとき exit code は非ゼロで、
原因と「次に何をすればよいか」だけを出す（推測で先へ進まない = instruction §16）。

## モジュール

| ファイル | instruction 対応 |
|---|---|
| `pr_status.py` | §16 停止規則・状態語彙・ledger |
| `pr_manifest.py` | §1 source_manifest / §2 LICENSE_LEDGER（fail-closed） |
| `pr_lab.py` | ラベルの寛容読み取り（時間単位を推定し wav 尺で独立検算） |
| `pr_census.py` | §3 corpus census + coverage_comparison |
| `pr_performance.py` | §4 Performance（timing/pitch/dynamics/release）+ 構造 tripwire |
| `pr_identity.py` | §5 Identity（Ritsu 実歌唱の sp/ap） |
| `pr_match.py` | §6 正規化転写（比率と形だけを移す） |
| `pr_ladder.py` | §7 R0–R4 + P0、書き出した WAV バイト列の sha256 |
| `pr_gates.py` | §10 G1–G7 + G-ear、§11 exploratory 軸の隔離 |
| `pr_run.py` | §17 実行順オーケストレータ |

## 出力

`results/` に instruction §15 の成果物が入る（`wav/` と raw corpus は gitignore、
`ear_excerpts/` = 耳判定用 16bit 抜粋のみ同梱）。

## 設計上の要点

- **Performance に 2 次元配列を持たせない**（§4）。`assert_no_spectral_payload` が
  型で拒否し、合成器のアクセス集合を tripwire が実測する
- **絶対値を写さない**（§6）。移すのは子音/母音の share・note-relative cents・
  正規化した終端カーブ。`matching_report` に `absolute_hz_copied: false` /
  `absolute_seconds_copied: false` を明記する
- **timing 転写が no-op になる場合を検出する**。PJS と Ritsu の子音/母音比が
  同じ（一様なテンポ差だけ）なら R2 は R0 と同一になる。仕様の性質であって
  バグではないが、黙って通すと「交換した」と誤読されるため
  `timing_transplant_is_noop` として記録する
- **事前登録 primary axis を結果で変えない**（§0-8）。`nasal_gain_shape_db` は
  `CANDIDATE_SECONDARY_AXIS` として併記のみ、ゲートに使わない（§11）
- **耳を機械で上書きしない**（§0-10）。G-ear は `results/ear_answers.json` に
  4 設問の回答が入るまで BLOCKED
