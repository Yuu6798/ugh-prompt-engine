# Melodia 信頼度スケールと凍結ゲートの衝突（M1-real 診断・2026-07-25）

M1-real 実測（PR #220）で Melodia 経路が**M1-real 全 5 素材 × 両 run で
`voiced_coverage 0.000`** を返した件の診断記録。結論は「Melodia アダプタのバグでも
噛み合わせ問題でもなく、**抽出器の信頼度セマンティクスと凍結ゲート閾値の衝突**」である。

**証跡の所在**: 実測 report（run×2・素材 id・hash・route 別出力）は PR #220 の
`docs/measurements/m1real_2026-07/` にあり、**本 PR の checkout には存在しない**
（#220 は未マージ）。本ドキュメント単体で checkout から監査できるのは §2 の
実測値（合成素材 1 本 + 実音源 2 本を本 PR 提出時に再測したもの）である。うち **checkout から
再現できるのは合成素材の行だけ**で、実音源 2 本は意図的に uncommitted な machine-dependent
素材のため、§2.2 に sha256 を pin して同定可能にしてある（波形は別途受領が必要・§6 参照）。
#220 マージ後に report を参照できるようになる。

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

素材の同定用 pin（波形は repo に commit しない M1-real 素材。`registry.yaml` の
`external_fixtures[].expected_audio_sha256` と同一値）:

| 素材 | fixture id | sha256 |
|---|---|---|
| `kane_y2.mp3` | `real_vocal_jrock` | `8eb0237c1e8aaad41762923f436cfbafa33a246e6dc15ce3e1beed8dbd347752` |
| `crslv2_w3.mp3` | `real_vocal_waltz` | `40a9f0e79ef2636fa717946d2c0b0480b3703ab8417299705b4927b1d9e20f10` |

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
**測定した全入力で** Melodia の confidence はこの床を越えなかったため、それらの入力では
**有声フレーム数が 0** になった。voiced_coverage / note_count / phrase_count /
confidence_mean はすべて有声フレーム集合から導出されるので、連鎖的に 0 になり
`low_confidence_rate` は 1.000 になる。観測された 5 つの reason は
すべてこの単一原因の派生である。

床を越える入力があればその入力では有声フレームが残るので、上の帰結は
「confidence が床未満に収まる入力」に限る（§3.1 参照）。

したがって Melodia 経路は、**信頼度が 0.30 に届かない入力に対しては**、旋律を
正しく取れていても構造的に insufficient を返す。M1-real の実測値は「Melodia が旋律を
観測できなかった」ことを意味しない（2.1 / 2.2 のとおり音高自体は取れている）。
**計器が接続されていなかった**。

### 3.1 主張の適用範囲（重要）

「Melodia の confidence は**入力によらず** 0.30 を越えない」とは主張できない。
根拠は測定した 3 入力（合成 1 + 実音源 2）と M1-real の 5 素材で 0.30 未達だったことのみで、
上限の普遍的な証明ではない。実際 `melodia_adapter.py` の docstring は raw salience が
稀に 1 を超えうると記し（clamp 前）、本ドキュメント §5 も理論上限が定まらないと述べている。
confidence が 0.30 に達する入力があれば、その入力では有声フレームが残り、経路は
insufficient に固定されない。

確立した事実は次の 2 点に限る:

1. **測定した全入力**（`synth_mono_phrased` / `kane_y2` / `crslv2_w3` / M1-real 5 素材）で
   Melodia の confidence は 0.30 に届かず、有声フレーム数が 0 になった
2. その値域は pyin の `voiced_prob`（max 1.0 / mean 0.64）と**同一の床で裁けるスケールではない**

「実制作音楽の実用帯でこの床を越える入力があるか」は未測定であり、
床の妥当性判断には別途 corpus 規模の測定を要する。

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

**再現できる範囲**: 下記手順は §2.1（合成素材 `synth_mono_phrased`）と §2.3（pyin 対比）を
checkout だけで再現する。§2.2 の実音源 2 行は **repo に commit しない machine-dependent
素材**を要するため、この checkout からは再現できない（素材を受領し §2.2 の sha256 と
一致することを確認した上で、同じ `extract_melodia_f0` 呼び出しを当てれば再現する）。
診断の結論は合成素材だけでも成立する — §2.1 は「クリーンな単旋律ですら床に届かない」を
示しており、これが「アダプタ設定バグ」「demucs stem との噛み合わせ」の 2 仮説を棄却する。
実音源 2 行は「合成純音固有の縮退ではない」ことを補強する追加証拠である。

§2 の数値は下記のビルドで得たものである。`PredominantPitchMelodia` の confidence 値は
別リリース・別ビルドで変わりうるため、**version とビルド指紋を pin しないと
「実装 drift」と「診断した scale 衝突」を区別できない**。

| 項目 | 値 |
|---|---|
| pip version | `2.1b6.dev1389` |
| `essentia.__version__` | `2.1-beta6-dev` |
| 実装バイナリ | `_essentia.cpython-311-x86_64-linux-gnu.so` |
| バイナリ sha256 | `07852d293d1e15aaf740ef807dcb85f07240318460dc179c5117c9a81e5cc16d` |
| Python | 3.11.15（Linux x86_64） |

Melodia は学習重みを持たない DSP 算法なので、pin すべきモデル入力は実装バイナリ
そのものである（`melodia_adapter.melodia_implementation_files()` が返す指紋。
`extractor_weights_kind: library_binary` として report に載る値と同一定義）。

```bash
pip install -e ".[dev]"                 # src レイアウトなので svp_rpe を import 可能にする
pip install "essentia==2.1b6.dev1389"   # AGPL-3.0。標準 install / CI には含めない

# 実装バイナリ指紋の照合。**不一致なら以降の測定に進まない**（別ビルドの数値を
# 本ドキュメントの記録値と比較すると、実装 drift を scale 衝突と誤診する）。
python - <<'PY'
import hashlib, sys
sys.path.insert(0, "src")
from svp_rpe.rpe.learned.melodia_adapter import melodia_implementation_files

EXPECTED = {
    "_essentia.cpython-311-x86_64-linux-gnu.so":
        "07852d293d1e15aaf740ef807dcb85f07240318460dc179c5117c9a81e5cc16d",
}
files, _ = melodia_implementation_files()
actual = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
if actual != EXPECTED:
    print("essentia build mismatch (fail-closed):")
    print("  expected:", EXPECTED)
    print("  actual  :", actual)
    raise SystemExit(1)
print("essentia build matches the recorded diagnosis build")
PY
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
