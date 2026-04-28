# examples/expected_output/

`examples/sample_input/` の synth WAV 5 曲に対し、決定論パイプライン
（`extract` → `generate` → `evaluate`）を実行した **正解スナップショット**。
Q0-3（snapshot test）が CI で参照する hash の真値を提供する。

## 構造

```
expected_output/
├── hashes.txt                              # 全成果物の SHA-256（sha256sum 互換）
├── synth_01_slow_pad_c_major/
│   ├── rpe.json                            # extract 出力
│   ├── svp.yaml                            # generate 出力
│   └── evaluation.json                     # evaluate 出力（rpe / ugher / integrated）
├── synth_02_minor_pulse_a_minor/
│   └── ...
├── synth_03_mid_groove_g_major/
│   └── ...
├── synth_04_waltz_fsharp_minor/
│   └── ...
└── synth_05_fast_bright_d_major/
    └── ...
```

ディレクトリ名は [`../sample_input/ground_truth.yaml`](../sample_input/ground_truth.yaml)
の `id` フィールドと一致する。

## 再生成手順

パイプライン実装（`src/svp_rpe/`）または synth サンプルが意図的に変更された
場合のみ再生成する:

```bash
# 全 expected_output と hashes.txt を上書き再生成
python scripts/regenerate_expected.py

# 直後に意図した変更か確認
git diff examples/expected_output/
```

## 検証手順

`hashes.txt` と現在のパイプライン出力が一致するか確認する:

```bash
python scripts/regenerate_expected.py --check
```

不一致時は exit code 1 と diff サマリを返す。CI および Q0-3 の
snapshot test がこのモードに依存する。

## パイプラインパラメータ

- `valley_method`: `"hybrid"`（CLI default）
- 入力: `examples/sample_input/synth_*.wav`（PR #9 で追加された決定論合成サイン波）

これらを変更した場合 hash が変化する。

## 決定論性ノート

- 同一入力 + 同一パイプラインパラメータ → 同一バイト列出力を保証
- タイムスタンプ・乱数・絶対パスは出力に混入しない設計
- 浮動小数点丸めは `round(..., 4)` または `round(..., 2)` で固定
- 詳細: [`docs/architecture.md`](../../docs/architecture.md)
