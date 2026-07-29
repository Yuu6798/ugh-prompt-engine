# M2b — カテゴリ S 帯 実測記録（確定実測 run7・2026-07-29）

設計: [`docs/DESIGN_M2_extraction_accuracy.md`](../../DESIGN_M2_extraction_accuracy.md) §5 M2b。
ハーネス: `scripts/run_melody_accuracy.py`（run/evaluate 二相）。

本ディレクトリは **PR #225（scorer pin 閉包）マージ後の main** で実施した確定実測
（通算 7 回目 = run7）の dated 記録である。先行 6 回（2026-07-26〜07-29・ハーネス 5 版
跨ぎ）はセッション作業領域のみで commit されていない（要約は
`.claude/memory/2026-07-29.md`）。判定は 7 回すべて不変。

## ファイル

| ファイル | 内容 |
|---|---|
| `m2b_run1.json` / `m2b_run2.json` | run phase の report（repeats n=2） |
| `m2b_verdict.json` | evaluate phase の判定（凍結 bars の機械適用 + 測り直し検証） |
| `run1_stdout.txt` / `run2_stdout.txt` / `evaluate_stdout.txt` | 実行時 stdout/stderr（TF/HF の import 時警告を含む・無編集） |

pin 整合は `tests/test_m2b_committed_record.py` が CI で強制する
（report_pins の sha256 照合・凍結 bars/specs の digest 照合・判定固定）。

## 判定（`m2_accuracy_bars.yaml` 凍結バーの機械適用）

| 帯 | RPA | RCA | octave_gap | median cent | VR | VFA | 判定 |
|---|---|---|---|---|---|---|---|
| S-direct (`crepe_direct`) | **1.000** | 1.000 | 0.000 | 1.353 | 1.000 | **0.259** | **fail**（VFA > 0.15 単独超過） |
| S-fullstack (`demucs_vocals_then_crepe`) | 0.640 | 0.643 | 0.003 | 13.187 | 0.903 | 0.585 | diagnostic_only（バーなし・設計 §3） |

- 各 run 内 repeats n=2 は両帯とも bit 一致（`repeats_bit_identical: true`）。
- S-direct の不合格因子は voicing 単独。音高系（RPA/RCA/octave/cent）は事実上完璧。
- 一方向規則（bars の `one_way_rule`）により、バー・`est_voiced_confidence_floor` の
  事後調整は行わない。

## 計器知見（先行 6 回 + run7 で確定）

### 1. `median_cent_error` のバッチ間双安定

`median_cent_error` は run 間で **1.352838 ↔ 1.353400** の 2 値を往復する（run7 は
1.353400 側）。各 run 内の repeats n=2 は毎回 bit 一致し、フレーム分類系
（RPA/RCA/VFA/VR/カウント）は全 run で完全一致。300 有声フレーム中 1 フレームの
f0 補間値が ~1e-3 cent 揺れ、偶数個 median の中央 2 要素平均に現れたもので、
crepe TF CPU 推論のバッチ間非決定性に帰属する。判定影響ゼロ。
**「同一バッチ内 repeats の bit 一致は、バッチ間決定論を含意しない」**。

### 2. S-direct fail の帰属（M2d 診断対象）

VFA 0.259 の単独超過で、音高系は完璧（RPA/RCA 1.000）。最有力仮説は
「休符の合成リリース尾が spec 上は無声・音響上は有声」という**正解境界
アーティファクト**（合成 fixture の正解導出と、センサーが見る音響の境界不一致）。
crepe 経路の欠陥と断定しないが、設計 §7 の分岐上は fail を fail として記録する
（帰属診断は M2d の対象）。

## 実行環境・provenance（要約。全 pin は JSON 本体を参照）

- 2026-07-29 UTC・CPU 4 コア / GPU なし。generator=evaluator コード sha256
  `e79d945e6e0763464afd4f3e4bb4696837701c33ed747d09c64f8d324a65a635`（両者一致）。
- crepe 0.0.16 + tensorflow-cpu 2.21.0 / demucs 4.1.0 + torch 2.13.0 / mir_eval 0.8.2
  （scorer 閉包 pin: mir_eval / scipy / numpy / decorator / charset_normalizer は
  verdict 本体に記録）。
- CREPE 重み `model-full.h5`: 89,038,936 bytes・ファイル単体 sha256
  `b6fd2758b03a8625a16fe86cd474ff0d8f30ad9a05e4bee2244e13e98664f860`
  （report の `provenance_extractor_weights_sha256` はファイル列→1 digest の合成値
  `fb369944d4feb5964cae189dceba1e554d6471f0be712aad61fc087afaef4a55`）。
- Demucs 重み `htdemucs_ft`（4 signature + files.txt + htdemucs_ft.yaml の合成 digest）:
  `bf1218da42cb354bb995fb41b0a1dc8fa3cd47d63ccdaefec12dad03f8377b86`。
- 重みは実行前に事前配置（アダプタは実行時ダウンロードを行わない・欠如は
  fail-closed で `unavailable`）。取得元は公式配布 URL のみ（crepe: marl/crepe
  `models` ブランチ配布物、demucs: dl.fbaipublicfiles.com）。ミラー探索なし。
