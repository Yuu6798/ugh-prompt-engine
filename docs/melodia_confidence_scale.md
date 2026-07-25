# Melodia 信頼度スケールと凍結ゲートの衝突（M1-real 診断・2026-07-25）

M1-real 実測（PR #220）で Melodia 経路が**M1-real 全 5 素材 × 両 run で
`voiced_coverage 0.000`** を返した件の診断記録。結論は「Melodia アダプタのバグでも
噛み合わせ問題でもなく、**抽出器の信頼度セマンティクスと凍結ゲート閾値の衝突**」である。

**証跡の所在**: 実測 report（run×2・素材 id・hash・route 別出力）は PR #220 の
`docs/measurements/m1real_2026-07/` にあり、**本 PR の checkout には存在しない**
（#220 は未マージ）。本ドキュメント単体で checkout から監査できるのは §2 の
実測値（合成素材 1 本 + 生 mp3 2 本 + demucs vocals stem 1 本を本 PR 提出時に測ったもの）
である。うち **checkout から再現できるのは合成素材の行だけ**で、実音源 2 本は意図的に uncommitted な machine-dependent
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
| confidence max | **0.2947** |
| confidence >= 0.30 のフレーム | **0 / 2947** |

検出音を合成仕様の全音と突き合わせた結果（`synthesis_specs.yaml#synth_mono_phrased` は
3 フレーズ・計 15 note-event・音名集合 {60, 62, 64, 65, 67, 69, 71} = C4–B4）:

| 項目 | 実測 |
|---|---|
| 検出できた音名 | 62 (D4) / 65 (F4) / 67 (G4) の **3 音のみ**（各 120 フレーム） |
| 未検出の音名 | 60 (C4) / 64 (E4) / 69 (A4) / 71 (B4) |
| 誤検出（仕様に無い音） | なし |
| 検出音の周波数誤差 | D4 −0.1 cent / F4 +0.0 cent / G4 +0.0 cent |
| 検出できた note-event 数 | 15 中 3（各音名 1 回分＝120 フレーム × 3） |

**読み取れること**: Melodia が音高を出した箇所は **±0.1 cent の精度で正しい**。これは
sampleRate の取り違えや dtype の誤解釈を否定する（それらは周波数を系統的にずらすので、
cent 単位で合うことはない）。一方で **被覆は 15 note-event 中 3** にとどまり、
「パラメータが本素材に最適でない」可能性は否定できない（§4 参照）。
にもかかわらず confidence は一度も 0.30 に届かない。

### 2.2 実音源（Melodia 本来の設計対象＝ポリフォニック混合、先頭 30 秒）

| 素材 | frames | pitch 検出 | unique conf 値 | conf max | conf mean | conf ≥ 0.30 |
|---|---|---|---|---|---|---|
| `kane_y2.mp3` | 10337 | 4147 (40%) | 51 | 0.1760 | 0.0480 | **0** |
| `crslv2_w3.mp3` | 10337 | 2920 (28%) | 36 | 0.2541 | 0.0470 | **0** |

素材の同定用 pin（波形は repo に commit しない M1-real 素材）。**本 commit 時点では
ドキュメント上の pin にとどまる**: これらの digest を `registry.yaml` の
`external_fixtures[].expected_audio_sha256` へ刻む変更は PR #220 にあり未マージなので、
現 checkout の registry には当該フィールドが無い（凍結 provenance ゲートには未接続。
`evaluate_m1_real_go_bar` は go-bar fixture がこのフィールドを欠くと fail-closed する）。
#220 マージ後に registry pin と同一値になる:

| 素材 | fixture id | sha256 |
|---|---|---|
| `kane_y2.mp3` | `real_vocal_jrock` | `8eb0237c1e8aaad41762923f436cfbafa33a246e6dc15ce3e1beed8dbd347752` |
| `crslv2_w3.mp3` | `real_vocal_waltz` | `40a9f0e79ef2636fa717946d2c0b0480b3703ab8417299705b4927b1d9e20f10` |

実音源では confidence が連続的に分布する（unique 値 51 / 36）ため、2.1 の
「0 か 0.2947 の 2 値」は合成純音（定振幅）に固有の縮退であり、一般的な挙動ではない。
一般的な挙動は「**分布はするが 0.30 に届かない**」である。

### 2.4 実経路 `demucs_vocals_then_melodia` の pre-gate 出力

「stem がそもそも使える音高を含まない」ことと「音高はあるが confidence が床未満」は
post-gate の症状が同じなので、gate を通す前の値を直接測った。

測定は **harness の実経路をそのまま呼ぶ**（`melody.extractors.observe_via_route_with_provenance`
に `routing.select_routes("vocal_track")` の経路を渡す）。前処理・分離・pin 付与はすべて
経路実装のものを使い、doc 側でパイプラインを組み直さない — 自前で組むと「decode して
mono 化して 30 秒に切ってから WAV を書く」等、実経路と違う前処理を測ることになる
（実経路は凍結した元ファイルをそのまま Demucs へ渡す）。

| 項目 | 値 |
|---|---|
| 入力 | `kane_y2.mp3` **全長**（fixture `real_vocal_jrock`） |
| 入力 sha256 | `8eb0237c1e8aaad41762923f436cfbafa33a246e6dc15ce3e1beed8dbd347752` |
| 分離 | `htdemucs_ft` v4.1.0（`shifts=0`・現在の実経路） |
| **vocals stem sha256**（harness emit） | `77244a534e748cf32aae79cdae4a6b110167c1ea85a4b7131ffbdcd08c104033` |
| melodia 実装バイナリ pin | `b29c5aea8acf1229fb546cd6f573872310a30845a2263aa687921b3075a34aaa`（kind=`library_binary`） |

pre-gate 出力（**同一 stem** に対する 2 経路）:

| 経路 | frames | pitch 検出 | conf max | conf mean | conf ≥ 0.30 |
|---|---|---|---|---|---|
| `demucs_vocals_then_melodia` | 56543 | **27171 (48.1%)** | 0.2216 | 0.0630 | **0** |
| `demucs_vocals_then_pyin`（対照） | 3534 | 2144 (60.7%) | 1.0000 | 0.2448 | **1100** |

**stem は「音高が取れない」状態ではない** — Melodia 自身が 48.1% のフレームに音高を
出しており、**同一 stem**（`stem_sha256` が両経路で一致）で pyin は 1100 フレームが
床を越える。したがって post-gate の全ゼロは「stem が壊れている」ためではなく
confidence の値域に帰属する。

pin は harness が経路実行時に emit したもので、`observe_via_route_with_provenance` は
third-party を import する**前**にコード pin を bind し、推論後に再検証する（#217）。
doc 側で別途 artifact を hash し直すより、この経路自身の pin を引用する方が
「測った経路」と「pin した対象」が一致する。

再現手順:

```python
import sys; sys.path.insert(0, "src")
import numpy as np
from svp_rpe.melody.routing import select_routes
from svp_rpe.melody.extractors import observe_via_route_with_provenance

routes = {r.name: r for r in select_routes("vocal_track")}
obs, prov = observe_via_route_with_provenance("kane_y2.mp3", routes["demucs_vocals_then_melodia"])
assert prov["preprocessing"]["stem_sha256"] == (
    "77244a534e748cf32aae79cdae4a6b110167c1ea85a4b7131ffbdcd08c104033"
), "stem mismatch (fail-closed)"
cf = np.array(obs.frame_confidence)
print(cf.max(), int((cf >= 0.30).sum()), cf.size)
```

（`kane_y2.mp3` は commit しない M1-real 素材。sha256 は上表。demucs 重みが
未取得なら経路が `LearnedModelUnavailable` で止まる＝実行時 DL は起きない。）

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
根拠は測定した 4 入力（合成 1 + 生 mp3 2 + demucs vocals stem 1）と M1-real の 5 素材で
0.30 未達だったことのみで、
上限の普遍的な証明ではない。実際 `melodia_adapter.py` の docstring は raw salience が
稀に 1 を超えうると記し（clamp 前）、本ドキュメント §5 も理論上限が定まらないと述べている。
confidence が 0.30 に達する入力があれば、その入力では有声フレームが残り、経路は
insufficient に固定されない。

確立した事実は次の 2 点に限る:

1. **測定した全入力**（`synth_mono_phrased` / `kane_y2` 生 mp3 / `crslv2_w3` 生 mp3 /
   実経路 `demucs_vocals_then_melodia` の vocals stem / M1-real 5 素材）で Melodia の
   confidence は 0.30 に届かず、有声フレーム数が 0 になった
2. その値域は pyin の `voiced_prob`（max 1.0 / mean 0.64）と**同一の床で裁けるスケールではない**

「実制作音楽の実用帯でこの床を越える入力があるか」は未測定であり、
床の妥当性判断には別途 corpus 規模の測定を要する。

## 4. 三分岐の判定

Cowork 指定の切り分け基準に対する結論:

| 仮説 | 判定 |
|---|---|
| アダプタ設定バグのうち **sampleRate / dtype の取り違え** | **否**。44.1 kHz へリサンプル済み・mono float32 で、検出音は ±0.1 cent の精度（取り違えなら周波数が系統的にずれる） |
| アダプタ設定バグのうち **パラメータ最適化不足** | **未棄却**。§2.1 の被覆は 15 note-event 中 3。ただし被覆を上げても confidence の値域が床の外にある事実は変わらないため、本件の主因ではない |
| demucs stem との噛み合わせ問題 | **否**。§2.4 で実経路 `demucs_vocals_then_melodia` を全長素材に対して回し、Melodia 自身が 48.1% のフレームに音高を出すこと、**同一 stem** で pyin は 1100 フレームが床を越えることを確認した（stem 側の欠陥では説明できない） |
| 凍結閾値と抽出器の信頼度セマンティクスの衝突 | **是**。`voiced_confidence_floor 0.30` が Melodia の値域（**全実測入力の conf max が 0.1487–0.2947**。実経路 = 0.2216）の外にある |

なお「パラメータ最適化不足」は本件と**独立に残る課題**である（被覆 3/15 は
それ自体が低い）。ただし床の問題を解かない限り、被覆をいくら上げても
post-gate は 0 のままなので、対処順序は床の定義が先になる。

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
checkout だけで再現する。§2.2 / §2.4 の実音源由来の行は **repo に commit しない
machine-dependent 素材**（および §2.4 は demucs 重み）を要するため、この checkout からは
再現できない（素材を受領し §2.2 の sha256 と
一致することを確認した上で、同じ `extract_melodia_f0` 呼び出しを当てれば再現する）。
**合成素材だけでは 2 仮説を棄却できない**点に注意する。checkout で再現できる §2.1 が
単独で示すのは「クリーンな単旋律でも confidence が床に届かない」ことと「出た音高は
±0.1 cent で正確（= sampleRate / dtype の取り違えではない）」までである。合成素材は
demucs を通らないので stem 噛み合わせ仮説には触れられず、被覆 3/15 のためパラメータ
最適化不足も棄却できない。stem 仮説の棄却は §2.4（実 stem 上の pre-gate 測定）に、
値域の一般性は §2.2 に依存しており、どちらも machine-dependent 素材を要する。

§2 の数値は下記のビルドで得たものである。`PredominantPitchMelodia` の confidence 値は
別リリース・別ビルドで変わりうるため、**version とビルド指紋を pin しないと
「実装 drift」と「診断した scale 衝突」を区別できない**。

| 項目 | 値 |
|---|---|
| essentia pip version | `2.1b6.dev1389` |
| `essentia.__version__` | `2.1-beta6-dev` |
| 実装バイナリ | `_essentia.cpython-311-x86_64-linux-gnu.so` |
| バイナリ sha256 | `07852d293d1e15aaf740ef807dcb85f07240318460dc179c5117c9a81e5cc16d` |
| Python | 3.11.15（Linux x86_64） |

Melodia は学習重みを持たない DSP 算法なので、pin すべきモデル入力は実装バイナリ
そのものである（`melodia_adapter.melodia_implementation_files()` が返す指紋。
`extractor_weights_kind: library_binary` として report に載る値と同一定義）。

**essentia だけでは足りない**: `extract_melodia_f0` は 22.05 kHz の fixture を
`librosa.resample` で 44.1 kHz へ上げてから Essentia に渡し、§2.3 の pyin baseline は
librosa 実装そのものである。したがって数値は librosa / numpy / scipy / リサンプル
backend にも依存する。実測時の数値ランタイム閉包:

| パッケージ | version |
|---|---|
| `librosa` | `0.11.0` |
| `numpy` | `2.4.6` |
| `scipy` | `1.17.1` |
| `soundfile` | `0.14.0` |
| `soxr` | `1.1.0` |
| `numba` | `0.66.0` |
| `llvmlite` | `0.48.0` |

version 文字列だけでは不十分（同一 version でも別ビルドの wheel は中身が異なり、
リサンプル / pyin の出力が変わりうる。essentia のバイナリを hash しているのと同じ
失敗モード）。**各 distribution が所有するファイル**の合成指紋も pin する:

| 項目 | 値 |
|---|---|
| 対象 | 上表 7 distribution の所有ファイル（`importlib.metadata` の RECORD 由来。計 3339 ファイル） |
| 除外 | `../../../bin/*`（インストーラ生成の entry point。shebang に絶対 interpreter パスを埋める）、`*.dist-info/*`（RECORD 等の install メタデータ）、`*.pyc` |
| 合成 sha256 | `8528c163104a9d92409b46d92ab5b50256333c3b64f730854c30740c79597055` |

合成方法 = distribution 名の昇順に `[name]` を feed し、各所有ファイルについて
RECORD 上の相対パス + `\0` + そのファイルの sha256 ダイジェストを feed した sha256。

除外規則は **machine 非依存性のために必要**である: 生成 entry point（例 numpy の
`../../../bin/f2py`）は shebang に venv の絶対パスを埋め込み、`RECORD` は install 先に
依存するため、同一 wheel でも checkout / venv の場所が違うだけで不一致になる。
指紋の対象は wheel 由来の不変ファイルに限る。

> **なぜ「モジュールの親ディレクトリを走査」ではないか**（実測で判明した失敗）:
> `soundfile` 0.14 は dist-packages 直下の**単一ファイルモジュール**
> （`soundfile.py`）なので、`Path(soundfile.__file__).parent` は site-packages
> そのものになる。親ディレクトリを再帰走査すると torch や tensorflow など**無関係な
> パッケージまで指紋に混ざり**、それらを入れ替えただけで一致しなくなる。逆に
> `librosa` は native 拡張を持たないため、`*.so` だけを見る方式では
> `librosa.pyin` / `librosa.resample` の**実装 Python ソースが一度も hash されない**。
> distribution 所有ファイルを列挙すれば、完全かつ環境の他パッケージから独立になる。

```bash
pip install -e ".[dev]"                 # src レイアウトなので svp_rpe を import 可能にする
pip install "essentia==2.1b6.dev1389"   # AGPL-3.0。標準 install / CI には含めない
# 数値ランタイム閉包も実測時の値へ固定する（librosa の resample / pyin が数値に効く）
pip install "librosa==0.11.0" "numpy==2.4.6" "scipy==1.17.1" \
            "soundfile==0.14.0" "soxr==1.1.0" "numba==0.66.0" "llvmlite==0.48.0"

# 実装バイナリ指紋の照合。**不一致なら以降の測定に進まない**（別ビルドの数値を
# 本ドキュメントの記録値と比較すると、実装 drift を scale 衝突と誤診する）。
python - <<'PY'
import hashlib, sys
sys.path.insert(0, "src")
from svp_rpe.rpe.learned.melodia_adapter import melodia_implementation_files

import importlib.metadata as meta

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

# 数値ランタイム閉包も照合する（essentia の hash が合っても librosa/numpy/scipy が
# 変われば confidence 値は動きうる）。
CLOSURE = {
    "librosa": "0.11.0", "numpy": "2.4.6", "scipy": "1.17.1",
    "soundfile": "0.14.0", "soxr": "1.1.0",
    "numba": "0.66.0", "llvmlite": "0.48.0",
}
drift = {p: (v, meta.version(p)) for p, v in CLOSURE.items() if meta.version(p) != v}
if drift:
    print("numerical runtime version drift (fail-closed):", drift)
    raise SystemExit(1)

# version が同じでも別ビルドなら数値が動くので、distribution 所有ファイルの
# 合成指紋まで照合する（環境の他パッケージには依存しない）。
def dist_digest(names):
    h = hashlib.sha256()
    for name in sorted(names):
        dist = meta.distribution(name)
        h.update(f"[{name}]".encode())
        for f in sorted(dist.files or [], key=str):
            rel = str(f)
            # インストーラ生成物 / install メタデータは環境依存なので除外する。
            if rel.startswith("..") or ".dist-info/" in rel or f.suffix == ".pyc":
                continue
            try:
                data = dist.locate_file(f).read_bytes()
            except OSError:
                continue
            h.update(rel.encode())
            h.update(b"\0")
            h.update(hashlib.sha256(data).digest())
    return h.hexdigest()

NUMERICAL_SHA = "8528c163104a9d92409b46d92ab5b50256333c3b64f730854c30740c79597055"
got = dist_digest(CLOSURE)
if got != NUMERICAL_SHA:
    print("numerical runtime artifact drift (fail-closed):", got)
    raise SystemExit(1)
print("essentia build and numerical runtime match the recorded diagnosis environment")
PY
python - <<'PY'
import sys, numpy as np, yaml
sys.path.insert(0,'scripts'); sys.path.insert(0,'src')
from build_melody_bench import build_signal
from svp_rpe.rpe.learned.melodia_adapter import extract_melodia_f0
from svp_rpe.melody.extractors import extract_pyin_observation
specs = yaml.safe_load(open('tests/fixtures/melody_bench/synthesis_specs.yaml'))
y, sr = build_signal('synth_mono_phrased', specs)

# §2.1: Melodia
t, hz, cf, _ = extract_melodia_f0(y, sr)
cf, hz = np.array(cf), np.array(hz)
print("melodia: frames=%d pitch_nonzero=%d conf_max=%.4f conf_mean=%.4f frames>=0.30=%d"
      % (cf.size, int((hz > 0).sum()), cf.max(), cf.mean(), int((cf >= 0.30).sum())))

# §2.3: pyin baseline（同一入力。スケールの対比がこの診断の中心証拠）
obs = extract_pyin_observation(y, sr)
pc, ph = np.array(obs.frame_confidence), np.array(obs.frame_hz)
print("pyin   : frames=%d conf_max=%.4f conf_mean=%.4f voiced_conf_mean=%.4f frames>=0.30=%d"
      % (pc.size, pc.max(), pc.mean(), pc[ph > 0].mean(), int((pc >= 0.30).sum())))
PY
```

## 7. 関連

- 実測記録: `docs/measurements/m1real_2026-07/`（PR #220）
- 観測ゲート設計: [`docs/melody_observability.md`](melody_observability.md)
- 凍結レジストリ: `tests/fixtures/melody_bench/registry.yaml`（`observation_gate` / `one_way_rule`）
- 決定論化 PR-A: demucs `shifts=0`（#221・マージ済み。本件とは独立の別要因）
