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

## セクション別読み（emotional arc）

```bash
svprpe extract track.wav --clap-sections -o rpe.json
```

`--clap-sections` は同じ意味軸バッテリーを **構造セクション単位**
（`PhysicalRPE.structure` の各 `SectionMarker`）で読む。intro → verse →
chorus のように `vocal_presence` / `energy` / `brightness` などがどう
推移するか（emotional arc）を見るための計器で、`--clap-semantic`
（曲全体 1 読み）の **superset** — `--clap-sections` を付けると
曲全体の `semantic_axes` と各セクションの `semantic_axis_sections` の
両方が populate される。両フラグを同時指定した場合は `--clap-sections`
側のパスが優先される。

出力は `learned_annotations.semantic_axis_sections`
（`LearnedSemanticSection` のリスト、各要素が
`{section, start_sec, end_sec, axes: [LearnedSemanticAxis, ...]}`）に
隔離され、`SemanticRPE.por_surface` / `PhysicalRPE.*` への書き込みパスは
存在しない（`semantic_axes` と同じ隔離ポリシー、§「隔離ポリシー」参照）。
決定論性・CI スタンス・opt-in extra 要件も `--clap-semantic` と同じ
（`rpe/learned/clap_adapter.py` の `embed_audio_segments` は
`librosa.load` を 1 回だけ呼び、区間ごとに決定論的にスライスするだけで
RNG は導入しない）。

本センサーは「セクション単位で読める」という計器面の土台であり、
2026-07-03 の emotional-arc 方向の議論（メモリ参照）の第一歩の位置づけ:
読み値の解釈・傾向の校正・下流での利用方法は今後の検証課題として残る。

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

## 軸校正（2026-07-04 実測）

fixture provenance のピン済みチェックポイント
（`music_audioset_epoch_15_esc_90.14.pt`, sha256 `fae3e9c0…`,
`amodel=HTSAT-base`, laion-clap 1.1.7）で 5 軸バッテリーの初回実推論校正を
実施した。方法は **embedding 空間校正**: 音源そのものではなく、
`examples/learned/clap/` のサイドカー（#143）に保存済みの**実音源 CLAP
埋め込み**（実 Suno 6 本 = lyrics fixture / 実 MusicGen 32 本 = musicgen_k2
fixture）に対し、battery の probe テキストだけを埋め込み直して
`contrast_fit` を計測する。音源の再取得も再生成も不要で、
`scripts/calibrate_semantic_axes.py` として再実行可能。結果ログ:
`examples/learned/clap/semantic_axes_calibration_2026-07-04.yaml`。

### 軸ごとの結果（規律「効果 > 再生成ノイズ」を一様適用）

| 軸 | 意図した真値コントラスト | 結果 | 位置づけ |
|---|---|---|---|
| `vocal_presence` | lyrics vs inst（実 Suno・ジャンル内） | effect/regen-noise = **15.7×**(EDM) / **11.4×**(Rock) | **実証済み**（PR2b-2 #131 の再現） |
| `energy` | MusicGen bpm ノブ（n=8×2） | **d=+2.57**、brightness ノブには d=+0.18 | **実証済み・バッテリー最良の選択性**（自ノブに強く他ノブに鈍い） |
| `brightness` | MusicGen brightness ノブ（n=8×2） | 自ノブ d=+1.52（方向正）、**bpm 干渉 d=+2.37 が自ノブより強い** | 方向は本物だが **bpm 交絡**（K3-2b の bpm→centroid 結合と整合）。単独読み不可・bpm と併読 |
| `acousticness` | （強い真値なし）全 38 本＝電子系制作物 | 負符号は 19/38 のみ。ノブ応答 d≈−1.6×2（速く/明るく → 非アコースティック寄り＝方向妥当） | **探索扱い**。相対変化は読めるが**絶対符号は verdict にならない**（隔離ポリシーの実測裏付け） |
| `warmth` | （真値なし） | brightness 軸と **r=−0.827**（n=38） | **探索扱い・冗長候補**（このプールでは実質 anti-brightness。warmth 固有のコントラストが出る素材が現れるまで保留） |

補助所見: 歌詞あり/なしコントラスト（実 Suno）は 5 軸すべてで再生成ノイズ超え
（4.5×–16.2×）。これは各軸の妥当性でなく「ボーカルの有無はミックス全体を
動かす」ことの表れ＝軸間クロストークの注意材料。

### 再現性（決定論契約の実測境界）

- **同一マシン内**: probe 埋め込み 2 回とも完全一致
  （`probe_determinism_same_run: true`）。校正スクリプト 2 回実行で
  出力 YAML バイト一致。synth 5 曲の抽出側読みも 2 回とも 6 桁一致。
- **マシン間**: fixture 保存値（別マシン採取）との突き合わせで
  lyrics 6 本中 3 本 / musicgen 32 本中 21 本が 6 桁 exact、
  最大乖離はどちらも **1e-6**（6 桁丸めの 1 ulp）。→ 決定論契約は
  **同一環境内**で読むもの。マシン間差 ≤1e-6 は計測対象の効果
  （~1e-1）より 4 桁小さく、計器の用途には影響しない。
- チェックポイント整合: `scripts/calibrate_semantic_axes.py` が
  fixture の `model.checkpoint_sha256` / `model.amodel` ピンと
  `--checkpoint` / `--amodel` を自動照合し、不一致・未指定は
  モデルロード前に fail-fast する（テキスト probe と保存済み audio
  embedding が同一埋め込み空間であることの強制）。CLI 側（抽出時の
  意味層センサー）は従来どおり `--clap-checkpoint` / `--clap-amodel`
  でピンする。

### 有効帯域（素材依存性）

純合成トーン（`examples/sample_input/synth_0*.wav`、centroid 720–947 Hz）
では `energy` は bpm 順位と無相関（Spearman 0.3, n=5）・`brightness` は
レンジ圧縮（−0.19〜−0.13、唯一明確に明るい synth_05 だけ最上位に来る）と、
**実制作音源で成立する目盛りが合成素材では床に張り付く**。K1/K2 の
「センサー盲は素材依存」と同型で、本計器の有効帯域は**実制作音楽**。
合成 fixture で軸を校正してはならない。再現コマンド:

```bash
svprpe extract examples/sample_input/synth_05_fast_bright_d_major.wav \
  --clap-semantic \
  --clap-checkpoint <path>/music_audioset_epoch_15_esc_90.14.pt \
  --clap-amodel HTSAT-base -o out.json
```

### インストール補足（Debian 系環境）

Debian 系 patched setuptools 環境では `laion-clap` の依存
（`wget`/`progressbar` の legacy sdist）が `install_layout`
AttributeError でビルド失敗することがある。`pip install -U setuptools`
で解消（2026-07-04 実測。従来「リモート env で py3.11 ビルド不能」と
記録していた事象の真因で、proxy は無関係）。

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
