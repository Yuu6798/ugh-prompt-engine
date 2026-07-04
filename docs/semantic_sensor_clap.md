# CLAP semantic-axis sensor（extraction-stage）

## 動機

これまでの CLAP 配線（`rpe/learned/clap_adapter.py` + `similarity.py`）は
`scripts/collect_clap_fixture.py` 経由の post-hoc A/B コンパレータに限定
されていた — 生成済み出力（Suno 生成物など）に対する fixture 駆動の
比較のみで、`rpe/learned/__init__.py` の `attach_learned_annotations` も
「Public CLI integration is deferred to a later PR」と明記していた。

本 PR はこれを一歩進め、`svprpe extract` の opt-in フラグとして CLAP を
**抽出段階の意味層センサー**として配線する。SOURCE 音声そのものを CLAP に
読ませ、固定の意味軸バッテリーに対する A/B `contrast_fit` を計測する —
生成物ではなく入力音声を対象にする点が post-hoc 比較との違い。

## 軸バッテリー

`config/semantic_probe_axes.yaml`（`src/svp_rpe/config/` に同期コピー）に
5 軸を定義:

- `vocal_presence` — ボーカル有無。`docs/lyrics_semantic_anchor.md` で
  `mid_ratio` プロキシより 10-15 倍頑健と判明した実証済み軸。probe 文言は
  `examples/learned/clap/lyrics_vocal_contrast_manifest.yaml` と揃えて
  継続性を保つ。
- `brightness` — 物理層 `spectral_centroid` ベースの brightness センサーの
  クロスチェック。
- `energy` — 高エネルギー/低エネルギーの対比。
- `acousticness` — アコースティック/エレクトロニックの対比。
- `warmth` — 暖色/寒色的な質感の対比。

各軸は `{name, positive: [...], negative: [...], notes?}` の形。
`notes` は任意の注釈で、`rpe/learned/semantic_axes.py` の検証は
`name` / `positive` / `negative` が非空であることのみを要求する。

### バッテリーの拡張

新しい軸を追加するには `config/semantic_probe_axes.yaml`
**と** `src/svp_rpe/config/semantic_probe_axes.yaml` の両方に同じ内容を
追記する（`tests/test_config.py::test_packaged_configs_match_repo_configs`
がバイト一致を enforce する）。`positive` / `negative` は 1 件以上の
プロンプト文字列のリストであればよい。

## 隔離ポリシー

`rpe/learned/semantic_axes.py` の出力は
`LearnedAudioAnnotations.semantic_axes`（`LearnedSemanticAxis` のリスト）
にのみ格納される。`SemanticRPE.por_surface` / `PhysicalRPE.*` /
`SVPForGeneration.style_tags` への書き込みパスは存在せず、本 PR もそれを
作らない。詳細は [`docs/learned_models_policy.md`](learned_models_policy.md)
§2 を参照。

`contrast_fit` は符号付きの A/B コントラスト（positive probe とのコサイン
平均 − negative probe とのコサイン平均）であり、`svp_rpe.control.
grip_effect_size` と同じ「学習版 grip」の哲学で読む — `[0, 1]` の
confidence ではなく、verdict でもない。クランプや再解釈をしてはならない。

## CLI 使用法

```bash
svprpe extract track.wav --clap-semantic -o rpe.json
```

`--clap-semantic` は opt-in（デフォルト無効）。`semantic-embed` extra
（`pip install -e ".[semantic-embed]"`）が未導入の場合は
`LearnedModelUnavailable` が送出され、CLI はインストールヒントを
`stderr` 相当（`console.print`）に出して exit code 1 で終了する。

出力 JSON は `learned_annotations.semantic_axes` に軸ごとの
`{axis, contrast_fit, positive_probes, negative_probes, source_model}`
を含む。`learned_annotations.inference_config` には
`semantic_axes_config_version`（軸バッテリー config の
`schema_version`）と `n_semantic_axes`（実際に評価した軸数）が
`embed_audio_file` 由来の既存キー（`checkpoint` / `amodel` / `n_chunks`
等）に追加される。

## 採譜（transcribe）での advisory 利用

```bash
svprpe transcribe track.wav --clap-semantic -o draft_score.yaml
```

`svprpe transcribe` は音源から draft `CompositionScore` を起こすが、意味層
（`semantic.core` / `grv` / `delta_e`）は **人間が書く欄**として
`TODO(transcribe): ...` プレースホルダのまま残す（DD-D、
[`docs/score_centric_planning.md`](score_centric_planning.md) §5）。
`--clap-semantic` を付けると、CLAP 意味軸の読みを **YAML コメントブロック**として
draft の先頭に添える:

```yaml
# ---- CLAP semantic sensor (advisory) ----
# Learned A/B contrast_fit readings of the SOURCE audio (signed grip,
# not a verdict). Instrument context for authoring the semantic.*
# fields below — they are NOT auto-filled: semantic.core / grv /
# delta_e stay TODO for the author (DD-D). See docs/semantic_sensor_clap.md.
#   vocal_presence : +0.2475
#   brightness     : -0.0300
#   ...
# source_model: laion_clap:CLAP_Module
# ------------------------------------------
meta:
  ...
```

全行が YAML コメントのため draft は loader-valid のまま
（`load_composition_score` はコメントを無視する）。意味層フィールドを
**自動で埋めない**点が肝心で、「計器 = 作曲前のパラメータ取得道具」
（score_centric §1）の役割に忠実に、作者が空欄を書くための計器読みとして
横に添えるにとどめる。DD-D の解除（意味フィールドへの write-through）は
別途 promotion gate と校正基準の文書化を要する将来課題。

## 決定論

`rpe/learned/clap_adapter.py` の `embed_audio_file` / `embed_texts` は
決定論的に構成されている（同ファイルのモジュール docstring 参照:
librosa 自前デコード + 固定ウィンドウ分割で upstream の `rand_trunc`
random-crop パスを回避）。`semantic_axes.py` はその上に numpy の
コサイン演算（`similarity.contrast_fit`）を積むだけで、RNG は一切
導入しない。`contrast_fit` は小数点 6 桁に丸める
（`scripts/collect_clap_fixture.py` の採取スクリプトと同じ慣習）。

## CI スタンス

実推論（本物の `laion_clap` + 重みロード）は CI に持ち込まない。
`tests/test_semantic_axes.py` は `tests/test_clap_adapter.py` の
fake バックエンド（`_install_fake_clap` / `_write_wav` / `_make_bundle`）
を再利用し、決定論的な密閉テストのみを行う。実推論には
`semantic-embed` extra とローカル環境（torch + laion-clap の重み
ダウンロード）が必要。

## 既知の限界

- 学習モデル出力は G1-G5（`docs/learned_models_policy.md` §7）の
  promotion gate を満たしておらず、ルールベース評価層（`SemanticRPE`
  / `PhysicalRPE`）への write-through は一切ない。あくまで補助センサー。
- 実推論は環境依存（torch のインストール状態、CLAP チェックポイントの
  可用性、CPU/GPU 差異）であり、`docs/learned_models_policy.md` の
  G5（決定論）検証はモデルロード自体の再現性までは保証しない。
- 5 軸バッテリーは開始点であり網羅的ではない。他ジャンル・他属性の
  軸は今後の拡張候補（config 追記のみで対応可能）。
- CLAP のテキストエンコーダは短い自然言語プロンプトを前提としており、
  各軸の positive/negative probe セットの語彙選択が `contrast_fit` の
  感度に直接影響する（`docs/lyrics_semantic_anchor.md` の知見と同様、
  probe 文言のチューニングは継続課題）。
