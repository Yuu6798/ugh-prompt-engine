# Melodia 信頼度スケールと凍結ゲートの衝突（M1-real 診断・2026-07-25）

M1-real 実測（PR #220）で Melodia 経路が**全 6 素材 × 両 run で `voiced_coverage 0.000`**
を返した件の診断記録。結論は「Melodia アダプタのバグでも噛み合わせ問題でもなく、
**抽出器の信頼度セマンティクスと凍結ゲート閾値の衝突**」である。

本ドキュメントは**診断のみ**を記録する。正規化の決め方は事前登録マター（go-bar の結果を
見る前に定義する必要がある）なので、ここでは方式を提案も実装もしない。

## 1. 症状

`scripts/run_melody_observability.py --external` の実測（`docs/measurements/m1real_2026-07/`）で、
`demucs_vocals_then_melodia` は全 5 素材・両 run で一律に:

```
insufficient  reasons=voiced_coverage 0.000 < min 0.300; note_count 0 < min 8;
              phrase_count 0 < min 2; confidence_mean 0.000 < min 0.450;
              low_confidence_rate 1.000 > max 0.550
```

経路が `unavailable` に落ちたのではなく、**走ったうえで全ゼロ**である点が特徴。

## 2. 切り分け手順と実測値

essentia はこれまで CI では fake backend でしか走っておらず、これが実 essentia
（`essentia==2.1b6.dev1389`）での初実行だった。以下の順で切り分けた。

### 2.1 クリーンな単旋律合成素材（`synth_mono_phrased`）

```python
from build_melody_bench import build_signal
from svp_rpe.rpe.learned.melodia_adapter import extract_melodia_f0
y, sr = build_signal('synth_mono_phrased', specs)      # n=188520, sr=22050
t, hz, cf, _ = extract_melodia_f0(y, sr)
```

| 指標 | 値 |
|---|---|
| frames | 2947 |
| pitch 検出フレーム | 360 / 2947 |
| 検出 pitch の中央値 | 349.23 Hz（F4）、max 392.00 Hz（G4） |
| confidence max | **0.2947** |
| confidence >= 0.30 のフレーム | **0 / 2947** |

**Melodia は音高を正しく取れている**（F4/G4 は合成仕様どおりの実在音高）。
にもかかわらず confidence が一度も 0.30 に届かない。

### 2.2 実音源（Melodia 本来の設計対象＝ポリフォニック混合、先頭 30 秒）

| 素材 | frames | pitch 検出 | unique conf 値 | conf max | conf mean | conf ≥ 0.30 |
|---|---|---|---|---|---|---|
| `kane_y2.mp3` | 10337 | 4147 (40%) | 51 | 0.1760 | 0.0480 | **0** |
| `crslv2_w3.mp3` | 10337 | 2920 (28%) | 36 | 0.2541 | 0.0470 | **0** |

実音源では confidence が連続的に分布する（unique 値 51 / 36）ため、2.1 の
「0 か 0.2947 の 2 値」は合成純音（定振幅）に固有の縮退であり、一般的な挙動ではない。
一般的な挙動は「**分布はするが 0.30 に届かない**」である。

### 2.3 pyin との対比（同一入力 `synth_mono_phrased`）

| 抽出器 | conf max | conf mean | 有声フレームの conf 平均 | conf ≥ 0.30 |
|---|---|---|---|---|
| pyin (`voiced_prob`) | 1.0000 | 0.6366 | 0.6653 | 64 / 93 |
| melodia (`pitchConfidence`) | 0.2947 | 0.0360 | 0.2947 | 0 / 2947 |

pyin の `voiced_prob` は**確率**（[0,1] を素直に張る）、Melodia の `pitchConfidence` は
**salience 由来の連続値**で、実測上は 0.3 未満の帯に収まる。同じ「confidence」という
名前で別スケールの量が入っている。

## 3. 機構

`src/svp_rpe/melody/observability.py:231` が有声フレームを次で判定する:

```python
if hz > 0.0 and conf >= thresholds.voiced_confidence_floor
```

凍結値は `observation_gate.voiced_confidence_floor: 0.30`（`registry.yaml`）。
Melodia の confidence は入力によらずこの床を越えないため、**有声フレーム数が常に 0** に
なる。voiced_coverage / note_count / phrase_count / confidence_mean は
すべて有声フレーム集合から導出されるので、連鎖的に 0 になり
`low_confidence_rate` は 1.000 になる。観測された 5 つの reason は
すべてこの単一原因の派生である。

したがって Melodia 経路は**入力が何であれ構造的に insufficient を返す**。
M1-real の実測値は「Melodia が旋律を観測できなかった」ことを意味しない
（2.1 / 2.2 のとおり音高自体は取れている）。**計器が接続されていなかった**。

## 4. 三分岐の判定

Cowork 指定の切り分け基準に対する結論:

| 仮説 | 判定 |
|---|---|
| アダプタ設定バグ（sampleRate / dtype / パラメータ） | **否**。44.1 kHz へリサンプル済み、mono float32、音高は正しく取れている |
| demucs stem との噛み合わせ問題 | **否**。分離を通さない合成素材・生 mp3 でも同じく全フレームが床未満 |
| 凍結閾値と抽出器の信頼度セマンティクスの衝突 | **是**。`voiced_confidence_floor 0.30` が Melodia の値域（実測 max 0.176–0.295）の外にある |

## 5. 未決事項（事前登録マター・実装しない）

`voiced_confidence_floor` は「pyin の voiced_prob」を暗黙の基準として凍結された値であり、
別スケールの抽出器を同じ床で裁く前提が置かれていた。これをどう正すかは
**go-bar の結果を見る前に定義しなければならない**（実データを見てからの閾値変更は
`one_way_rule` に抵触する）ため、本 PR では方式を選ばない。

決めるべき論点だけを挙げる:

- 床を「抽出器ごとの値域に対する相対量」として定義し直すのか、抽出器側で
  confidence を確率スケールへ正規化するのか（どちらも**新たな事前登録**を要する）
- 正規化する場合、その写像は実データを見ずに定義できるか（Melodia の salience は
  上限が理論的に定まらないため、経験的な正規化は実データ依存になりうる）
- Melodia 経路を M1-real の候補経路から外す選択肢（`registry.yaml` の route matrix は
  `input_kind` から機械的に決まるため、外すこと自体が凍結内容の変更になる）

いずれも凍結バー・閾値の定義に触れるため、Cowork の設計判断を待つ。

## 6. 再現手順

```bash
pip install essentia            # AGPL-3.0。標準 install / CI には含めない
python - <<'PY'
import sys, numpy as np, yaml
sys.path.insert(0,'scripts'); sys.path.insert(0,'src')
from build_melody_bench import build_signal
from svp_rpe.rpe.learned.melodia_adapter import extract_melodia_f0
specs = yaml.safe_load(open('tests/fixtures/melody_bench/synthesis_specs.yaml'))
y, sr = build_signal('synth_mono_phrased', specs)
t, hz, cf, _ = extract_melodia_f0(y, sr)
cf = np.array(cf)
print("conf max:", cf.max(), "frames >= 0.30:", int((cf >= 0.30).sum()), "/", cf.size)
PY
```

## 7. 関連

- 実測記録: `docs/measurements/m1real_2026-07/`（PR #220）
- 観測ゲート設計: [`docs/melody_observability.md`](melody_observability.md)
- 凍結レジストリ: `tests/fixtures/melody_bench/registry.yaml`（`observation_gate` / `one_way_rule`）
- 決定論化 PR-A: demucs `shifts=0`（本件とは独立の別要因）
