# examples/expected_output/

`examples/sample_input/` の synth WAV 5 曲に対し、決定論パイプライン
（`extract` → `generate` → `evaluate`）を実行した **正解スナップショット**。
Q0-3（snapshot test）が CI で参照する hash の真値。

## 構造

```
expected_output/
├── hashes.txt                              # 全成果物の SHA-256（sha256sum 互換）
├── README.md
└── synth_<NN>_<descriptor>/
    ├── rpe.json                            # extract 出力
    ├── svp.yaml                            # generate 出力
    └── evaluation.json                     # evaluate 出力（rpe / ugher / integrated）
```

ディレクトリ名は [`../sample_input/ground_truth.yaml`](../sample_input/ground_truth.yaml)
の `id` と一致。`audio_file` / `source_artifact.path` は repo-relative
（`examples/sample_input/<filename>`）に正規化されており checkout 場所に依存しない。

## 再生成

```bash
python scripts/regenerate_expected.py        # 全 expected_output と hashes.txt を上書き
git diff examples/expected_output/           # 意図した変更か確認
```

## 検証

```bash
python scripts/regenerate_expected.py --check  # 不一致なら exit 1 + diff サマリ
```

`--check` は (1) ディスクと hashes.txt の一致、(2) 現在の pipeline 出力との
一致、(3) 孤児ファイル不在 を確認する。CI / Q0-3 snapshot test がこれに依存。

## 固定パラメータ

- `valley_method`: `"hybrid"`（CLI default）
- 入力: `examples/sample_input/synth_*.wav`（PR #9 で追加された決定論合成サイン波）

これらや `src/svp_rpe/` の実装が変わると hash が変化する。
