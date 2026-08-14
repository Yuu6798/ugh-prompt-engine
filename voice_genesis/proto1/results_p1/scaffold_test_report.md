# scaffold_test_report.md — 試作品 1 号（P1〜P7）骨格実装 実測レポート

対象: `proto1_design_memo.md` P1〜P7（VG-001/002/009/010/016 + linkability
lite）。実行環境: このリポジトリ非依存の scratch 実装（`proto1/`）。
全実行フォアグラウンド・決定論（seed 固定。wall-clock 使用は `created_at`
記録のみ）。

## 0. 実行コマンドとテスト件数

```
$ cd $PROTO   # = proto1/
$ python -m pytest tests -q
........................................................................ [ 79%]
...................                                                      [100%]
91 passed in 52.26s
```

**91 件全パス**（0 fail, 0 error, 0 skip）。内訳（ファイル別）:

| ファイル | 件数 | 対象 |
|---|---|---|
| `tests/test_genome.py` | 22 | P1: round-trip / 境界値 / out_of_physio_range / 型拒否 |
| `tests/test_bridge.py` | 7 | genome.py ↔ vt_harness Genome 橋渡し |
| `tests/test_probes.py` | 8 | P2: probe 定義・manifest・hash 決定論性 |
| `tests/test_sampler.py` | 13 | P3: sample/mutate/crossover の決定論性・境界フラグ |
| `tests/test_registry.py` | 13 | P4: JSONL append・lineage 遡上・content hash |
| `tests/test_reference_set.py` | 15 | P5: reference-set sidecar・linkability 監査・stale_audit |
| `tests/test_render_health.py` | 13 | P6: aliasing / register transition / formant sweep |
| **合計** | **91** | |

全件出力（`-v`）は `results_p1/_pytest_full_output.txt` に、`-q` の最終確認run
は `results_p1/_pytest_final_q.txt` に保存済み。

## 1. 成果物一覧

```
proto1/
├── genome.py            P1: VoiceGenome v0.2 dataclass 群 + JSON round-trip
│                             + 物理事前分布凍結表 + out_of_physio_range
├── bridge.py             P1↔R0.1: 新スキーマ → voice_r0(_1) Genome 変換 + render_note
├── hashing.py             共通 sha256 ヘルパ（波形 / 正規形 JSON、自前実装）
├── probes.py             P2: sustain/register_sweep/vibrato/phrase/cross_range
│                             fixture 生成 + manifest
├── sampler.py             P3: sample/mutate/crossover（seed 決定論）
├── registry.py           P4: genome-registry/0.1 JSONL + lineage 遡上 + content hash
├── reference_set.py       P5: reference-set/0.1 + スタンドイン gallery 8 声
│                             + E1/E2 embedding + linkability 監査 + stale_audit
├── render_health.py       P6: aliasing / register transition / formant sweep
├── tests/                 P7: 91 件（自己完結、conftest 不使用）
├── underspec_log_p1.md    メモが決めきれなかった箇所の補充判断（18 項目）
└── results_p1/
    ├── scaffold_test_report.md   本ファイル
    ├── report_data.json          本レポートの実測値の生データ (JSON)
    ├── _generate_report_data.py  上記を生成したスクリプト（再現用）
    ├── _pytest_full_output.txt   pytest -v 全出力
    └── _pytest_final_q.txt       pytest -q 最終確認 run
```

vt_harness/ 配下（`voice_r0.py` / `voice_r0_1.py` / `measure_v3.py` 等）は
**一切変更していない**（sys.path 追加による import 流用のみ）。

## 2. P6 実測値（レンダラ健全性）

生データ: `report_data.json` の `p6_*` キー。生成スクリプト:
`results_p1/_generate_report_data.py`。

### 2.1 aliasing（>0.45×sr 帯域のエネルギー比 < -40dB）

既定 Genome（`genome.build_genome("scaffold-default")`、実質 `voice_a()` 相当）
で 3 ノート測定:

| ノート | high-band energy ratio | 判定 (<-40dB) |
|---|---|---|
| A4 (69) | -67.94 dB | PASS |
| C3 (48) | -92.07 dB | PASS |
| C6 (84) | -68.63 dB | PASS |

`sampler.sample(seed)` seed=0..9 の 10 genome でも全て PASS
（-45.6dB 〜 -89.3dB の範囲、`report_data.json.p6_sampled_seeds_0_9` 参照）。
R0.1 の加算合成が `n_harmonics=60` でも Nyquist 近傍で確実に減衰しており、
折り返しノイズの懸念は実測上ない。

### 2.2 register transition（register_sweep probe、隣接ノート RMS 差 <= 6dB）

既定 Genome、MIDI 45→90 (46 音) の中央 50% 窓 RMS(dB) 隣接差:

- 最大隣接差: **0.558 dB**（閾値 6.0dB に対し十分な余裕）
- 先頭 5 音 RMS(dB): -19.27, -19.36, -19.03, -19.29, -19.20
- 末尾 5 音 RMS(dB): -19.02, -19.40, -19.19, -19.33, -19.25

seed=0..9 の 10 genome でも最大隣接差は 0.28〜1.31 dB の範囲で全て PASS。
5 声区のシグモイド混合（`transition_width` による滑らかな遷移）が振幅面で
実測上連続であることを確認した。

### 2.3 formant sweep（formant_scale 0.85→1.15、formant_centroid_v3 の方向一致率）

既定 Genome、MIDI 69 (A4) での 7 点掃引（`FORMANT_SWEEP_SCALES` =
0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15）:

```
formant_centroid_log2hz = [10.235, 10.306, 9.587, 9.635, 9.643, 9.648, 9.672]
direction_consistency   = 0.833（6 区間中 5 区間が非負方向）
net_direction_positive  = False（診断用付随情報。gate には使わない）
判定                    = PASS（閾値 0.60 以上）
```

seed=0..9 の 10 genome では **8/10 PASS**（seed=2, seed=4 は
direction_consistency=0.5 で FAIL）。既存 grip v3 実測
（`vt_harness/results_v3/grip_report_v3.json`, axis=formant_scale,
config_b, intended_feature=formant_centroid）の `direction_consistency=0.75`
と同水準の分散であり、「cepstral top-2-by-magnitude ピーク選択が掃引途中で
どの 2 フォルマントを拾うか切り替わる」という既知の測定不安定性が本実装でも
再現していることを確認した（厳密な単調性ではなく方向一致率で判定する設計に
した理由の裏付け。詳細は `underspec_log_p1.md` [UNDERSPEC-P1-13b]）。

## 3. P4 実測サンプル（registry + lineage）

`sample(1) → mutate(seed=2) → [+ sample(3)] → crossover(seed=4)` の 4 段
lineage を実際に JSONL へ append し、`lineage()` で遡上した結果:

```
genome_id 一覧: 0de225e6148f (sample) → f4039c3df700 (mutate)
                887531cb29d2 (sample, 別系統)
                → ca96f03fcf9e (crossover, parents=[f4039c3df700, 887531cb29d2])

lineage(ca96f03fcf9e) = [0de225e6148f(sample), f4039c3df700(mutate), ca96f03fcf9e(crossover)]
```

crossover は先頭親（`f4039c3df700`）側のみを主系列として遡上する設計どおり
（`underspec_log_p1.md` [UNDERSPEC-P1-8b]）。genome_id（内容 sha256 先頭12桁）
は同一 genome に対して再計算しても一致することを `test_registry.py` で確認済み。

## 4. P5 実測サンプル（reference-set/0.1 + linkability 監査、memo 規定どおり n_permutations=200）

生成: `reference_set.build_reference_set(n_permutations=200)`。
所要時間: gallery 構築（8 声 × probe render/embed）+ チャンス帯推定
（200 candidate × probe render/embed）で **142.1 秒**、監査サンプル込みで
合計 144.7 秒（内訳は `report_data.json._timing_p5_*`）。

### 4.1 reference-set/0.1 sidecar

```json
{
  "schema_version": "reference-set/0.1",
  "id": "standin-gallery-v1",
  "version": 1,
  "created_at": "2026-08-13T12:00:00+00:00",
  "source_datasets": ["standin-synthetic-gallery-v1 (proto1 sampler.sample, no real singer audio)"],
  "embedding_models": [
    {"id": "E1-measure_v3-agg", "provenance": "measured (instrument-validity caveat: stand-in embeddings)", ...},
    {"id": "E2-logmel64-agg",   "provenance": "measured (instrument-validity caveat: stand-in embeddings)", ...}
  ],
  "coverage_notes": "合成スタンドイン。実在歌手 embedding は machine_dependent で未実装。...",
  "sha256": "fbfc0d383fa6297d064e30840f7bac5254ea62075c774300f500cdf96dd459b4"
}
```

（`coverage_notes` 全文・`embedding_models` 全 2 件の説明文は `report_data.json`
の `p5_reference_set_sidecar` を参照。provenance caveat 必須の要件を満たす。）

### 4.2 チャンス帯（permutation 200 回、95 パーセンタイル）

| embedding | chance_band_p95（コサイン類似度） |
|---|---|
| E1 (measure_v3 集約) | 0.9679 |
| E2 (log-mel 64帯域) | 0.9436 |

字義通りの「gallery ラベルシャッフル」が有限固定集合で縮退する問題を、
gallery と無関係な合成候補 200 個の最近傍類似分布に置き換えて推定した
（`underspec_log_p1.md` [UNDERSPEC-P1-9] に詳細と根拠）。

### 4.3 監査サンプル実行結果

**gallery メンバー自身を監査**（linkable であるべき陽性対照。FAIL が正しい）:

| | E1 | E2 |
|---|---|---|
| max_similarity | 0.999999999... | 0.999999999... |
| chance_band_p95 | 0.9679 | 0.9436 |
| pass | **False**（正しく検出） | **False**（正しく検出） |

overall_pass = **False**。自分自身との照合で両系統とも確実にチャンス帯を
超え、監査ロジックが linkable なケースを正しく FAIL させることを確認した
（gate の陽性対照試験として機能）。

**gallery と無関係な新規候補 3 個を監査**（PASS が期待される陰性対照）:

| candidate seed | E1 sim | E1 pass | E2 sim | E2 pass | overall |
|---|---|---|---|---|---|
| 777001 | 0.7693 | PASS | 0.6520 | PASS | **PASS** |
| 777002 | 0.7370 | PASS | 0.6667 | PASS | **PASS** |
| 777003 | 0.5574 | PASS | 0.8828 | PASS | **PASS** |

3/3 が両系統で PASS。gallery と無関係にサンプルした声が、期待どおりチャンス
帯以下（＝統計的に見分けがつかない水準）に収まることを確認した。全レポート
の `provenance` フィールドは `measured (instrument-validity caveat:
stand-in embeddings)` で統一されている（実在人間声の識別器ではない旨の
正直会計）。

### 4.4 stale_audit 再監査トリガー

4 件（自己一致 1 件 + 無関係候補 3 件）を監査ログへ append した後:

- 現行の `reference_set_hash`（`fbfc0d38...`）でマーキング → **0 件**変更
  （まだ stale ではない、が正しい）
- reference set が再構築されたと仮定した別ハッシュでマーキング → **4 件**
  全てに `stale_audit=True` が立った（再監査トリガーが正しく発火）

冪等性（`test_mark_stale_is_idempotent`）: 同じハッシュで 2 回目を呼んでも
二重カウントされないことを確認済み。

## 5. 既知の限界・後続サイクルへの引き継ぎ

- P8 スコープ外どおり、E2E 一括実行（sample→render→measure→audit→register）
  と受け入れチェックリスト判定は未実装。各層は個別テスト済みだが、統合
  smoke は grip v4 完成を待つ（メモの明示的スコープ外）。
- registry の `eval.{plausibility,grip_ref,novelty}` は P1 では `None`
  プレースホルダ（`underspec_log_p1.md` [UNDERSPEC-P1-8]）。grip v4 接続時に
  実値を埋める設計にしてある。
- `audit.residual_gate_passed` は R3（残差ゲート）未実装のため常に `None`
  （memo 指定どおり `not_applicable`）。
- formant sweep の「単調性」は grip v3 実測（direction_consistency=0.75）と
  同水準の分散を持つ既知の測定不安定性であり、本試作品の実装バグではない
  （§2.3・`underspec_log_p1.md` [UNDERSPEC-P1-13b]）。
- チャンス帯推定（§4.2）は「gallery ラベルシャッフル」の字義通り実装ではなく
  統計的に健全な代替手続きに読み替えている（[UNDERSPEC-P1-9]）。この設計
  判断の妥当性はレビュー対象になり得る。
