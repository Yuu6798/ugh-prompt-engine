# DESIGN S7 — run 8（り→ん破綻の専用調査: **観測子先行の 2 段**）

- 起草: 2026-08-20（Claude 設計）。**User 裁定 2026-08-20（第 3 次）まで反映**。
  本書は初版（診断単発・追加収録なし）を**上書き**し、第 3 次裁定で
  観測子の定義・量交絡の除去法・耳判定集合・回帰閾値を確定した
- 位置づけ: S 系列の第 7 設計書。**り→ん破綻は S3 以来の最古の未解決課題**で、
  `results_s6/s6_record_2026-08-20.md` §6-4 が「単一介入 run の副題として
  扱うより専用の調査に値する」と申し送った件の実施回
- 前提記録: [`results_s6/s6_record_2026-08-20.md`](results_s6/s6_record_2026-08-20.md)
  （run 7 closeout・り→ん破綻は run5→7 一貫の継続課題と確定）/
  [`DESIGN_S3_backfill.md`](DESIGN_S3_backfill.md)（原因仮説の初出）/
  [`../evolution/records/vgl0_control_axis_probe_2026-08-20.md`](../evolution/records/vgl0_control_axis_probe_2026-08-20.md)
  （CPU レンダ probe の実証済み経路）/
  [`recording_kit/cards.md`](recording_kit/cards.md)（収録カード規約）

## 0. 裁定（本書で凍結する設計判断）

1. **run 8 は観測子先行の 2 段構成**。観測だけで 1 run を消費せず、同一計画内で
   Gate を挟んで学習まで到達する:

   ```
   Run 8-0（GPU $0・**run 番号を消費しない**）
     a. 終端遷移台帳（SP/遷移密度の機械集計）
     b. 固定 Probe Set 360 セル（**1 target event = 1 render**）
     c. 観測子 terminal_release_failure/0.1 の全セル機械評価
     d. 層化ブラインド耳校正（40 unique + 8 duplicate）
     e. duration / SP / spk_embed の 3 レバー診断
      ↓ Gate（§7 合格条件）
   Run 8-B  User 実歌唱の標的構成だけを単一介入として 40K 学習
      ↓
   観測子 + ブラインド A/B + 回帰対照 → **効果で終端宣言**
   ```

   **第 3 次裁定で「Run 8-A」の呼称は Run 8-0 へ統合された**（観測段は
   一つの GPU $0 ブロックとして扱う）。本書中の 8-0a/8-0b… は上記の細目を指す

   直接 8-B へ進めば、改善しても「実歌唱時間」「語尾 /ri/ の標的被覆」
   「長音符」「User 話者へのローカル効果」のどれが効いたか分からない

2. **初版の「User 追加収録は当面不可」は撤回**（本裁定が上書き）。8-B は
   **5〜8 分・16 カードの標的密度の高い収録**を起こす。汎用歌唱を
   20 分追加するより、この仮説の検証には標的密度が要る。
   **ただし 5〜8 分は「候補プール」であって「学習投入量」ではない**
   （第 4 次裁定）。学習投入は run 7 と同 dosage に固定し、構成だけを
   置換する = **`dosage-fixed targeted partial replacement`**（§8）

3. **症状の定義を訂正する（§1-1）**。「り→ん」は正解が /ri/→/N/ なのではなく、
   **正解の語尾 /ri/→SP が鼻音 /N/ のように崩れる誤変換**である。本体は
   **終端解放**であり、初版はモーラ単位でしか見ていなかった

4. **未校正の値に知覚名を付けない**（VG-L0 の「アタック強度」格下げと同じ規律）。
   本書が定義する量は全て**機構名**であり、耳ラベルに対する判別性能が実測される
   まで「破綻度」「鼻音化量」等の知覚名を与えない（軸名の `nasal` 系は
   **参照対照への距離**という機構の意味でのみ使う）

5. **観測値を単一スコアに潰さない**（M3 の恒久禁止と同型）。ベクトルで記録し、
   軸別 evidence のみを出す

6. **観測子が Gate を通らない場合、8-B へ進まない**。観測値は診断レポートとして
   残すが、**自動 Gate には昇格させない**。不成立は失敗ではなく結果であり、
   成立を装った閾値調整で通してはならない。ただし**空手で終わらせない** —
   §7-2 の 3 レバー診断（GPU $0）を成果物として残す

8. **1 target event = 1 render（測定契約）**。baseline probe は
   「**終端対象を 1 個だけ持つ 1 レンダ**」に固定する。根拠 = レンダラに
   フレーズの概念が無い（§4-0）。複数セルを連結する場合は**内部 SP を明示挿入
   した「改変レンダ」**であり、現行 renderer の baseline 測定とは**別系統として
   分離**して記帳する

9. **観測子の呼称**は `terminal_release_failure`（TRF）とする。「り→ん」は
   User の percept であって機構名ではない。TRF の下位軸は §5 で 5 系統に分ける

7. 予算: **cap $4 は「1 走行あたり」であって実験全体ではない**
   （2026-08-20 訂正 — 従来の書き方だと、非決定論だった場合の完了経路が
   自分の予算 AC に必ず違反していた）。

   | 経路 | 走行 | 合計 | 位置づけ |
   |---|---|---|---|
   | **Run 8-0** | なし（CPU のみ） | **$0** | 観測段 |
   | **決定論だった場合** | 8-B + 8-R | **≈$2.80** | **因果裁定まで到達**（既定の完了経路） |
   | 非決定論だった場合（既定） | 8-B + 8-R | ≈$2.80 | **探索的**として記帳（再裁定 8）。これも正当な出口 |
   | 非決定論 + 形式的な因果裁定を要求する場合 | + 8-R2 | ≈$4.20 | **実験全体予算の User 承認が要る** |

   **各走行は cap $4 以内**（run 7 実績 ≈$1.40）。**実験全体で $3 を超える
   見込みになった時点で User 承認を取る**。User の負担は
   **ブラインド耳ラベル 1 回**と**8-B の候補プール収録 5〜8 分**
   8-R は §9-0 のとおり**因果裁定の前提**であり、無い場合は全行が
   `confounded / provisional` になって run 8 は終端しない。User の負担は
   **ブラインド耳ラベル 1 回**と**8-B の候補プール収録 5〜8 分**

## 1. 症状の再定義（PoR の特定）

### 1-1. 「り→ん」は終端解放の誤変換である

| | 内容 |
|---|---|
| **正解** | 語尾 `/ri/` → `SP`（無音）への解放 |
| **観測される崩れ** | `/ri/` の母音部が伸び続け、鼻音 `/N/` のような終端へ落ちる |
| **誤りの所在** | 音素の置換ではなく、**終端解放の失敗**（止まるべきところで止まらない） |

この読み替えの帰結:

- pjs の「みわたす**す**の連続発声」は**別症状ではなく同じ症状の別音素版**
  （語尾 `/su/`→SP の解放失敗）。s6 record は 2 つを別項目として扱っていたが、
  **同一ファミリー**として一括で観測する
- したがって観測子は「り」に閉じず、**語尾 → SP の解放**を測る

### 1-2. D3 の失敗は「合成歌唱が無効」を意味しない

`DESIGN_S3_backfill` は D3（F1.4 合成歌唱 20.005 分）を り→ん対策の主力と
位置づけたが、その「サステイン主力データ」の実体を実装で確認すると:

- `singer/score_d3_sustain.py`: `_VOWEL_KANA = ("あ","い","う","え","お")` の
  **5 母音単独ロングトーンのみ**。`onset=None`（子音なし）
- 同 docstring 逐語: 「拗音・撥音・促音は本スコアの対象外」

つまり D3 サステイン譜には **/r/ onset も・語尾解放の対照も・撥音 /N/ との
対照も含まれていない**。症状の PoR（終端解放）を直接覆っていなかった。

**よって「合成歌唱は無効」という一般化はしない**。言えるのは
「**投入した合成歌唱は症状の PoR を覆っていなかった**」までである。

### 1-3. 実曲側の構造（さくら/うみ非対称は 3 重交絡）

| 対象 | 譜面上の実体 | 尺（設計値） | 音高 | 耳判定 |
|---|---|---|---|---|
| さくら「かぎり」の**り** | `score.py:57` `(1, 4, 0)` = deg1・4 拍・**phrase final** | **3.333333 s**（72 BPM） | A3 = MIDI 57 = 220.0000 Hz | **破綻**（ritsu run5–7） |
| さくら「みわたす」の**す** | `score.py:56` `(3, 2, 0)` = deg3・2 拍 | **1.666667 s** | D4 = MIDI 62 = 293.6648 Hz | 語尾連続発声（pjs/user） |
| うみ 最長音 | `score_umi.py:41` `(62.0, 3.0)` = 3 拍 | **2.045455 s**（88 BPM） | D4 = 62 | **成立**（全話者） |

**うみには語尾 /ri/ が無く、フレーズ末も主に 2〜3 拍**。したがって現在の
さくら／うみ差は「歌唱か否か」ではなく、**音素 /ri/ × フレーズ末 × 長さ**が
交絡している。§4 の probe set はこの 3 因子を分離するために組む。

## 2. 仮説の分解

### 2-1. 総有声時間では説明できない

run 7 の話者別 voiced 時間（assembly manifest の `_voiced_ph_dur_seconds`。
amitaro は `run7_dataset_pins.json` の `dosage.actual_voiced_ph_dur_s` = 864.9 と
一致を確認済み）:

| 話者 | voiced 秒 | modality | 語尾破綻 |
|---|---|---|---|
| ritsu | **2006.668**（最大） | VCV 録音のみ（run 7 で d3synth 引退につき**歌唱素材ゼロ**） | run5–7 一貫して破綻 |
| pjs | 1187.205 | **実歌唱**（PJS 26.86 分） | **なし**（唯一） |
| amitaro | 864.9 | 朗読のみ（持続音ゼロ・曲素材ゼロ） | 単体さくらで日本語破綻 |
| user | **171.88**（最小） | 宅録カード（歌唱・T0/T1/T2） | **部分的に破綻**（「みわたす」語尾の連続発声） |

**前提の訂正（2026-08-20）**: 初版は「最小量の user が**最も改善している**」を
根拠の一部にしていたが、**この順序は撤回済み**である（s6_record §5-1: user の
改善の主要因は run 6 の正規化であって run 7 に帰属できない）。撤回した結論を
設計の前提に残すと run 8 が撤回済みの結論から出発してしまうため、
**user 行からは改善の順序を外し、破綻の有無だけを残した**。

**残る反証はどれか（再計算）**:

- **総有声時間の単調仮説は依然として反証される**。ただし根拠は user 行ではなく
  **ritsu（最大 2006.7 s・破綻）vs pjs（より少ない 1187.2 s・破綻なし）**の
  2 点だけで足りる。「有声時間が多いほど破綻しにくい」なら順序が逆でなければ
  ならない
- **user 行が寄与するのは「最小量でも pjs ほどには破綻しない」という一点のみ**で、
  改善の大小は**何の根拠にもならない**
- したがって「順序と対応するのは話者ローカルの実歌唱経験量」も
  **並びと整合するだけの読み**であり、positive evidence ではない（§2-2）。
  modality・dose・録音条件・音域被覆が話者と一緒に動いている以上、
  n = 4 では同定できない

### 2-2. より精密な仮説

> 一般的な実歌唱分数ではなく、**話者ローカルの実歌唱**と、
> **長いフレーズ末 /ri/→SP の被覆量**が効いている。

これを 2 つに分けて事前登録する:

| ID | 仮説 | 現時点の位置づけ |
|---|---|---|
| **H-local** | 話者自身の実歌唱が、その話者自身の破綻を減らす | **`confounded / provisional`** — §2-1 の並びと**整合する**が、positive evidence ではない |
| **H-shared** | User 実歌唱を増やすと、共有デコーダ経由で ritsu にも転移する | **未検証**（別仮説） |

**H-local を「支持されている」と書かない**（2026-08-20 訂正）。理由は 2 つ:

1. **§2-1 の並びでは話者と他の要因が完全交絡している** — modality・dose・
   録音条件・音域被覆・声質が話者と一緒に動いており、n = 4 で分離できない
2. **並びの一部が撤回されている** — 「user が三世代で最大改善」は s6_record
   §5-1 で訂正され、その主要因（音量交絡解消）は run 6 の介入効果であって
   run 7 に帰属できないと確定した

**自分の設計が同定できない結論から出発しない。** H-local は 8-B で
**話者内に exposure を振って初めて**評価できる（§9 の振り分け表も
run 8-R が無ければ `confounded / provisional` である = §9-0）。
本走行（8-0）が H-local について出せるのは、**破綻境界が話者で動くか**
（H4）と**終端イベント密度が話者間でどう並ぶか**（H-TTD）までで、
そのどちらも原因を同定しない。

### 2-3. 8-A で分離する因子

| ID | 仮説 | 反証条件 |
|---|---|---|
| H0 | 破綻は文脈依存（フル譜面と短い診断セルで挙動が違う） | 実曲アンカーと同条件 probe セルの軸値が一致 |
| H1 | 持続長が閾値を超えると解放が失敗する | 尺ラダー（1/2/4 拍）で単調性が出ない |
| H2 | 低音側で失敗する（音域被覆の欠落） | 音高ラダー（低/中/高）で差が出ない |
| H3 | 音素 `/ri/` に固有（`/i/`・`/su/`・`/N/` では起きない） | 対照 probe で同等に崩れる |
| H4 | **話者**で崩壊境界が動く | 4 話者で境界が同一 |
| H5 | 世代（データ構成）で境界が動く | run5/6/7 で境界が同一 |
| **H-TTD** | **関連終端イベント密度**（`terminal transition density`）が効く | 台帳の密度順と破綻順が対応しない |
| **H-dur** | duration conditioning が有効な因果レバーである | `/r/`-`/i/` 配分を振っても TRF 軸が動かない |

**H-TTD の集計単位（第 3 次裁定）**: 初版が想定した「総 SP 数」では測れない。
ritsu VCV には**孤立録音の頭尾 SP が大量に入り得る**が、それは
「**長い実歌唱 /ri/→SP**」と同等ではない。よって §3 の台帳は
`modality` × `preceding_phoneme` × `preceding_duration_bin` ×
`utterance_final / internal` × 遷移種別 × `pitch_bin` の
**関連終端イベント密度**で比較する。

**H-dur の到達限界（明記）**: `/r/`-`/i/` の時間配分を振って改善が出ても、
**「破綻の原因は duration であって acoustic ではない」とは断定しない**。
言えるのは「**duration conditioning が有効な因果レバーである**」までである。
局在の主張には `ph_dur → pitch_pred → mel → waveform` の**差分保存**が要る
（`localization.first_divergence_stage` はその足場であって結論ではない）。

**H4 の到達限界（明記）**: 本設計は話者内で実歌唱 exposure を振らない。
H4 が成立しても言えるのは「**境界は話者で動く**」までで、原因（実歌唱分数 /
声質 / 録音条件 / データ量 / 音域被覆）は識別されない。exposure を話者内で
振るのは 8-B の役割である。

**H5 と走行間変動**: 世代軸はモデルを跨ぐ。s5_record §5.4 は、**入力バイト
不変の ritsu ですら run5→run6 で区間ラウドネスが最大 7.94 dB 動いた**ことを
実測している。したがって H5 は**絶対値では裁定しない** — §5 の話者内差分軸
（同一モデル・同一話者・同一音高・同一尺の 3 対照間の差）でのみ評価する。
差分軸は世代間の水準シフトを構造的に相殺する。

## 3. Run 8-0 — 標的被覆台帳（`target_exposure_ledger/0.1`）

**学習データ側を「分数」でなく「標的イベント数」で数え直す。** 現在の Drive
manifest から時間と話者構成は確定できるが、**語尾 /ri/ のイベント数は
まだ機械集計されていない**。「実歌唱分数だけが原因」を正典化する前にこれを作る。

話者ごとに集計するフィールド:

```yaml
speaker
modality:                    real_song | synthetic_song | VCV | speech
local_real_singing_seconds

# --- 関連終端イベント密度（第 3 次裁定・総 SP 数では測らない） ---
terminal_events:
  key:                       # 以下の直積で層別に数える
    modality
    preceding_phoneme        # ri | i | N | su | other
    preceding_duration_bin   # **秒**で切る（beats を使わない・§3 の bin 表）
    position                 # utterance_final | internal
    transition               # ri_to_SP | i_to_SP | N_to_SP | su_to_SP
    pitch_bin                # low | mid | high（話者固有帯・§8-1）
  value:
    count
    duration_seconds

# --- 密度の分母（層別に必ず記録する） ---
  denominator:
    # ★ 層キーは密度の式（§3 の density 定義）と**完全に同一**にする。
    #   = (modality × position × pitch_bin × preceding_duration_bin)
    #   分子が d3 なのに分母を全 duration で取ると密度も話者順序も変わるため、
    #   キーの不一致は同じ台帳から違う H-TTD 裁定を生む
    eligible_terminal_count      # 同一層内の全終端イベント数（transition を問わない）
    eligible_terminal_seconds    # 同上の合計 ph_dur 秒

# --- 参考（単独では判定に使わない） ---
ri_medial_count
note_duration_bin:           1 | 2 | 4 beats
```

**尺 bin は秒で定義する（2026-08-20 訂正）**: 初版は主層を「`>= 2 beats`」と
書いていたが、**VCV / speech コーパスにはテンポが無く beats へ換算できない**。
同じ `transcriptions.csv` から実装ごとに違う層へ入り、H-TTD の裁定が変わる。
よって bin は**入力そのものの単位（秒 = `ph_dur` 由来）**で切る:

```
preceding_duration_bin:
  d0: [0.0, 0.5)
  d1: [0.5, 1.0)
  d2: [1.0, 1.5)
  d3: [1.5, 2.5)
  d4: [2.5, inf)
```

**譜面のあるコーパス**（`synthetic_song` 等）も beats のまま数えず、
`seconds = beats * 60 / tempo_bpm` で換算してから同じ秒 bin へ入れる
（換算に使った `tempo_bpm` を行ごとに記帳する）。

**主層の閾値**: 旧「`>= 2 beats`」は **`>= 1.5 s`（= `d3` 以上）**へ置き換える。
根拠 = 2 拍の実尺は さくら（72 BPM）で 1.667 s・うみ（88 BPM）で 1.364 s であり、
1.5 s はこの帯の中央。既知の破綻 2 例（「り」3.333 s = `d4` / 「す」1.667 s = `d3`）が
主層に入り、成立する うみ最長音 2.045 s も `d3` に入る — **尺だけでは破綻を
分離しない**ことは H1 の検証対象であって、主層の定義で先取りしない。

**密度の定義（事前登録・実行可能形）**

層 `k = (modality, position, pitch_bin, preceding_duration_bin)` について:

```
density(speaker, k, transition)
  = terminal_events[k][transition].count
    / denominator[k].eligible_terminal_count
```

分母は「**その層で終端イベントが起こり得た回数**」であり、
分子と**同一の層キー**から取る（別層の分母を借りない）。

**主層（primary stratum）を事前登録する** — 層をまたぐスカラー合成はしない
（§0-5 の単一スコア禁止と同型）:

```
primary stratum:
  position           = utterance_final
  transition         = ri_to_SP
  preceding_duration_bin in {d3, d4}        # = >= 1.5 s
  modality / pitch_bin は層別に並べて報告する（合成しない）
```

**層別に裁定し、合成規則も事前登録する（2026-08-20 追加）**: primary stratum は
`pitch_bin` × `preceding_duration_bin` で**複数の層に分かれる**。ある話者が
`d3/low` では上位・`d4/high` では下位、ということが普通に起こるので、
「どの層で min/max を取るか」を決めないと同じ台帳から違う裁定が出る。
**スカラーへ合成しない**（§0-5）ので、**層別裁定 + 多数決**で確定する:

```
eligible stratum:
  position   = utterance_final
  transition = ri_to_SP
  preceding_duration_bin in {d3, d4}   × pitch_bin in {low, mid, high}
  -> 最大 6 層
  ※ modality は話者ごとに一意に定まるため層キーから外し、記述子として併記する

per-stratum verdict:
  各層で下の density 判定（ゼロ分岐込み）を独立に適用し
  {supported, refuted, undetermined} を出す

overall H-TTD verdict:
  scored = undetermined でない層
  supported    : len(scored) >= 2 かつ scored の >= 2/3 が supported
                 かつ refuted が 0
  refuted      : len(scored) >= 2 かつ scored の >= 2/3 が refuted
  undetermined : それ以外（len(scored) < 2 を含む）
```

判定は **話者間で層ごとに density を並べる**ことで行い、`count` と
`duration_seconds` の生値も**層ごとに必ず併記**する（密度だけを見ると
標本の薄い層が過大評価されるため）。**層別の裁定ベクトルも結果 JSON に残す**
（多数決の中身が見えないと合成の妥当性を後から検算できない）。

**独立検算できる worked example**（数値は**説明用の架空値**・実測は台帳が出す）:

| 話者 | 層 k | `ri_to_SP` count | `eligible_terminal_count` | density |
|---|---|---|---|---|
| pjs | real_song / utterance_final / mid / d3+ | 12 | 150 | **0.0800** |
| user | real_song / utterance_final / mid / d3+ | 1 | 40 | **0.0250** |
| ritsu | VCV / utterance_final / mid / d3+ | 0 | 900 | **0.0000** |

この例では ritsu の `eligible_terminal_count` が最大（孤立録音で終端が多い）
にもかかわらず density は 0 になる。**総 SP 数で数えれば ritsu が最も豊富に
見える**のに、層別密度では最も貧しい — これが §3 冒頭で「総 SP 数では測らない」
と定めた理由の数値的な表現である。

`supported / refuted` の条件も事前登録する:

**ゼロ密度の分岐を先に決める**（2026-08-20 追加）: 上の worked example が
まさにそうであるように、**density が 0 の話者は普通に出る**（ritsu = 0）。
`max/min` をそのまま計算すると **0 除算**になり、全話者 0 なら `0/0` になる。
実装ごとに crash / `inf` で `supported` / `undetermined` に分かれるので、
分岐を事前登録する:

```
判定順（上から評価し、最初に該当した行で確定する）:

1. いずれかの話者で eligible_terminal_count < 20
      -> undetermined          # 標本不足。比は計算しない
2. 全話者の density == 0
      -> undetermined          # 事象が 1 件も無く弁別不能（refuted ではない）
3. min == 0 かつ max > 0
      -> 順序（破綻しない話者 > 破綻する話者）を満たせば supported
         満たさなければ refuted
      ※ 比は計算せず ratio = "separated_by_zero" と記帳する（inf を書かない）
4. min > 0
      -> 順序を満たし かつ max/min >= 2.0 なら supported
         それ以外は refuted
```

`count` と `duration_seconds` の生値は**どの分岐でも必ず併記**する。

**`position` を必ず割る理由**: ritsu VCV は項目ごとの孤立録音なので
**頭尾 SP が大量に入り得る**。それを総 SP 数として数えると
「ritsu は →SP の実例が豊富」という誤った反証が出る。しかし
**孤立録音の頭尾 SP は「長い実歌唱フレーズの末尾 /ri/→SP」と同等ではない** —
先行音素も先行尺も位置も違う。H-TTD はこの層別の密度でのみ検証する。

- 集計元 = 各話者の `transcriptions.csv`（音素列 + ph_dur）と譜面由来の
  フレーズ境界。**推定ではなく既知の境界から数える**
- 出力 = `results_s7/target_exposure_ledger.json` + 人間可読の表
- **この台帳が 8-B の収録量を決める**（現行の標的密度を数えて不足分を設計する）

既知の参考値: User の UC-012「みわたすかぎり ひかりかがやく」（※「かぎり」の
「り」を丁寧に）は `batch3_t2_results.json` 実測で **duration_s = 12.843 の
1 カード**。通し歌唱 2 本を含めても標的 `/ri/`→SP は低密度である。

## 4. Run 8-0b — 固定 Probe Set（36 セル）

### 4-0. 測定契約: レンダラにフレーズの概念が無い

実装で確認した事実（**probe 設計を制約する**）:

- `gate_synth` の SP は 3 箇所のみ — `v_tokens1 = [SP] + real_phones` の
  **先頭 1 個**と `sp_idx_v` / `sp_idx_a` の**パディング**
- `score.py` / `score_umi.py` に**休符ノートが無い**
- `_NoteWithMs` は `ScoreNote.phrase_index` / `is_phrase_final` を**写さない**
  （VG-L0 既報・grep 0 件）。**score 側には情報があるのに renderer が捨てている**

したがって「**語尾 → SP**」が実際に実現するのは**発話末だけ**である。
曲中のフレーズ末 /ri/ の直後には次の音素が来るだけで SP は存在しない。帰結:

1. **1 target event = 1 render**（§0-8）。複数セルを 1 レンダに連結すると
   **最後の 1 つしか「→SP」を持たず、24 中 23 セルが黙って無効になる**。
   これは結果がそれらしく出てしまう類の穴なので実装要件として縛る
2. **`terminal_SP_frames` は観測量ではない**。`TAIL_FRAMES` 由来の定数であり、
   **入力メタデータ `input_tail_sp_frames` へ格下げ**する（§5）
3. 陽性対照は成立する。さくら「かぎり」は**最終フレーズの 4 拍 /ri/** であり、
   その後に tail SP が来る
4. 連結レンダ（内部 SP を明示挿入した「改変レンダ」）は**別系統**として
   分離記帳する。これは §7-2 のレバー 2 の実験系でもある

### 4-1. 割付

フル楽曲ではなく、**音素・長さ・位置を分離した短い譜面**を使う。

| Probe | 条件 | 条件数 |
|---|---|---|
| P-RI-FINAL | 語尾 `/ri/`→SP：1・2・4 拍 × 低・中・高 | **9** |
| P-RI-MEDIAL | 同じ `/ri/` を**フレーズ途中**に配置（位置の分離） | **3** |
| P-I-FINAL | 語尾 `/i/`→SP：**r なし対照**（1・2・4 拍 × 低・中・高） | **9** |
| P-N-FINAL | 正しい語尾 `/N/`→SP：**鼻音プロトタイプ**（同 3×3） | **9** |
| P-SU-FINAL | 語尾 `/su/`→SP：「みわたす」系対照 | **3** |
| P-ANCHOR | **実曲アンカー**: 「かぎり」「みわたす」「うみ」 | **3** |
| | **合計** | **36** |

**対照 2 系を 3×3 へ拡張した理由（事前登録割付の改訂・2026-08-20）**:
§5-1 の中核手法（話者内 3 対照差分）は **同一話者・同一音高・同一尺**での
比較を要求する。初版は `/ri/` を 9 セル置きながら `/i/` と `/N/` を各 3 セル
しか置いていなかったため、**9 セル中 6 セルで 3 対照差分が定義できず**、
`N_similarity_delta` / `i_reference_distance` と H3・H5 の裁定が構造的に
欠測する。中核手法を 2/3 のセルで諦めるより、CPU レンダを増やす方が安い
（GPU $0・追加 12 セル/世代話者）。**割付の改訂は本 memo の改訂として記帳する**
（黙って増やさない）。

音高帯 = 低 A3(57) / 中 D4(62) / 高 F4(65)（`score.py` 都節音階 deg1/deg3/deg5・
`score_d3_sustain.py` と同一バンク帯。新規音律を導入しない）。
**これは合成側の帯**であり、**8-B の収録側の帯は User 固有に作り直す**（§8-1）。

### 4-2. 実曲アンカーは**フル譜面レンダ**である（H0 の要）

`gate_synth.run_pipeline` は `notes_raw` **全体**を 1 本のトークン列
（`v_tokens1 = [SP] + real_phones` 以下同様）へ符号化して dur / pitch /
acoustic の各 predictor に渡す（`gate_synth.py:1108-1133` を実コードで確認）。
したがって短い診断セルはフル譜面の該当区間と**別のモデル入力**である。

- P-ANCHOR は**さくら/うみのフル譜面をレンダし、該当区間を切り出す**
- **陽性対照は P-ANCHOR「かぎり」**（User の耳判定で破綻が確定している唯一の実体）
- P-ANCHOR と同条件の P-RI-FINAL（4 拍・低）の差が **H0 そのもの**であり、
  他の H に先立って裁定する。不一致なら診断セットの外挿可能性が制約される
- **P-ANCHOR は診断セルと同一バッチ・同一ジェネレータフローで再レンダする**
  （既存 wav の流用では、不一致がジェネレータ/環境ドリフト由来か文脈由来か
  分離できない）。再レンダした P-ANCHOR が既知陽性ラベルを保つことを確認する

### 4-3. 世代 × 話者への展開（360 セル）

```
run5 × {ritsu, pjs, user}            = 108
run6 × {ritsu, pjs, user}            = 108
run7 × {ritsu, pjs, user, amitaro}   = 144
                                合計 = 360
```

- run5/6 の gate 素材と 40K checkpoint は Drive に保管済み。run7 は
  40K checkpoint・ONNX/emb の sha256・CPU 生成コマンドが s6_record §3 に記録済み
- **ONNX 実体が手元にない場合も 40K checkpoint から再 export し、記録済み
  sha256 と照合する**（照合不一致は fail-closed）
- CPU レンダ 360 本の実行可能性: VG-L0 probe が **51 条件 × 3 走行 = 153 レンダ**を
  同経路で完走済み（同 record §4）。同オーダーである

### 4-4. 辞書被覆の扱い

`かぎり` / `ひかり` / `いのり` / `めぐり` / `みわたす` / `うみ` / 撥音 `ん` は
いずれも既存の変換系が扱う範囲（`convert_*` の `_normalize_sokuon_nasal` が
ッ/ン を処理）。**未収載が出た場合は代用せず fail-closed で除外して記帳**し、
該当する H の裁定を `undetermined` に落とす（黙って縮小しない）。

## 5. 観測ベクトル `terminal_release_failure/0.1`（TRF）

**単一スコアに潰さない。** また**観測子名に percept を使わない** — 「り→ん」は
User の聴取語であって機構名ではない（§0-9）。セルごとに以下を記録する:

```yaml
# --- 入力メタデータ（観測量ではない・§4-0） ---
input_meta:
  input_tail_sp_frames              # TAIL_FRAMES 由来の定数。測定値ではない
  commanded_note_frames             # dur モデル出力の命令区間

# --- 主観測値（第 3 次裁定で確定） ---
primary:
  excess_tail_voiced_ms             # 命令終端を越えた有声の超過
  release_after_score_boundary_ms   # 譜面境界を越えた解放遅れ
  tail_f0_persistence               # 同区間の f0 継続
  terminal_mel_persistence          # 終端 mel の持続

# --- 補助 ---
duration:
  r_frames                          # /r/ 区間フレーム数
  i_frames                          # 母音区間フレーム数
  duration_overshoot                # 命令区間に対する超過
acoustic:
  N_similarity_delta                # 終端 mel の /N/ 対照への接近量（機構名）
  i_reference_distance              # 終端 mel の正常 /i/ 対照からの距離
  hnr_median_db_p1                  # 前半窓の HNR 中央値
  hnr_median_db_p2                  # 後半窓の HNR 中央値
  hnr_delta_db                      # p2 - p1（非周期性の増加 = 声門化の機構量）
  vowel_drift_l1                    # 終端母音区間の前 1/3 mel と後 1/3 mel の L1
waveform:
  energy_decay_slope                # 終端エネルギー減衰の傾き
localization:
  first_divergence_stage            # duration | pitch | acoustic | vocoder
```

### 5-0a. 下位軸と機械量の対応（**4/5 のみ機械軸を持つ**）

§0-9 で TRF の下位軸を 5 つ宣言したが、**機械量を持つのは 4 つだけ**である。
宣言と schema の乖離で「形状テストは通るのに宣言した軸が無い」偽成功が
起きないよう、対応を明示する:

| TRF 下位軸 | 機械量 | 備考 |
|---|---|---|
| nasal-like | `N_similarity_delta` / `i_reference_distance` | 話者内 3 対照差分（§5-1） |
| glottalization / vocal fry | `hnr_delta_db`（+ `hnr_median_db_p2`） | 非周期性の増加。**知覚名は与えない** |
| prolonged voicing | `excess_tail_voiced_ms` / `tail_f0_persistence` | 主観測値 |
| vowel drift | `vowel_drift_l1` | 終端母音内の mel 変位 |
| **intelligibility loss** | **なし（耳側専用）** | §6 の耳ラベル 3 軸目でのみ取る。**機械軸を持たないことを宣言する**（持たない軸を持つと書かない） |

したがって AC の「全セルに軸値」は **機械 4 軸**に対する要求であり、
intelligibility loss は**耳ラベルを取ったセルにのみ**存在する。

### 5-0. 無声対照との差分で測る（vocoder ringing の除去）

主観測値はいずれも「命令終端を越えて何かが残る量」である。**vocoder は
それ自体が固有の ringing を持つ**ため、生値をそのまま「解放の失敗」と
読むと計器が vocoder の性質を測ってしまう。

したがって**主観測値は全て無声対照との差分で報告する**。ただし
**無声対照の取り方に 2 つの罠がある**（2026-08-20 改訂）:

1. **`/su/` は本質的に無声ではない** — `/su/` の終端は母音 `/u/` であり、
   歌唱の長ノートでは無声化しない。「`/su/` だから無声」と決め打つと、
   有声のまま鳴っている波形を ringing 基準にしてしまう
2. **`/su/` は評価対象でもある** — `/su/` には「みわたす」で実際に観測された
   連続発声の破綻がある。**破綻しているセルを基準にして差し引くと、
   欠陥そのものを引き算する**ことになり偽陰性を生む

よって無声対照は次の規則で作る:

```
1. 機械検査で無声を確認する:
     終端窓の voiced_frames == 0
   を満たしたレンダ**だけ**を ringing 基準の母集団に入れる
   （「/su/ だから無声」という前提は使わない）

2. leave-one-out:
     評価対象セル自身は基準母集団から必ず除外する
   （破綻セルが自分の基準に混じらないようにする）

3. 基準が空なら差し引かない:
     status = ringing_uncorrected
   を立てて**生値のまま**報告する
```

- **`/su/` の二重役割を廃止する**: `/su/` は §8-4 の held-out 対照として
  評価される側であり、**既定の ringing 基準としては使わない**。上記 1 の
  検査を通った `/su/` レンダが結果的に基準母集団へ入ることはあるが、
  それは「検査を通ったから」であって「`/su/` だから」ではない
- 対照が取れないセルは `status = ringing_uncorrected` で明示し、
  **生値を差分と偽って記帳しない**
- **`ringing_uncorrected` のセルを校正・Gate 計算に混ぜない**（2026-08-20 追加）。
  一部のセルだけ生値・残りは参照との差分、という状態で同じ軸を z 化して
  閾値を引くと、**Gate が「TRF がラベルを分離したから」ではなく
  「生値と差分が混ざっていたから」通る/落ちる**。実際 leave-one-out では
  「唯一検査を通った `/su/` レンダ」が自分だけ `ringing_uncorrected` になり、
  他セルはそれを基準に補正される、という非対称が起こりうる。
  よって:

  ```
  比較群（= 同一話者 × 同一世代）ごとに fallback を 1 つに揃える:
    群内の全セルが補正可能  -> 全て補正値を使う
    1 つでも補正不能        -> **その群は Gate から除外**し、
                               status = ringing_uncorrected_group として記帳
  校正セット・hold-out・Gate 1〜4 の計算には
  **補正済みのセルだけ**を入れる（生値は診断レポートには残す）
  ```

### 5-1b. TRF の下位軸（percept を 1 本に潰さない）

耳が「ん」と呼ぶ現象は単一機構とは限らない。TRF は次の 5 系統に分けて記帳し、
**`N_similarity_delta` が動かないのに耳が「ん」と判定する経路を許容する**:

| 下位軸 | 機構 |
|---|---|
| `nasal_like` | 終端 mel が `/N/` 対照へ接近 |
| `glottalization` | 声門化 / vocal fry（低 f0・非周期性の増大） |
| `prolonged_voicing` | 解放せず有声が続く |
| `vowel_drift` | 母音の定常が別の母音へ流れる |
| `intelligibility_loss` | 語として不成立（耳のみ） |

**`N_similarity_delta` が動かず耳が「ん」と言った場合、それは計器の失敗ではなく
「percept の実体が `/N/` ではなかった」という所見である** — 事前登録しておき、
後から機構名を percept に合わせて曲げない（§0-4 の規律を観測子自身へ適用）。

### 5-1. 中核手法 — 話者内 3 対照差分

同一話者・同一音高・同一長さで生成した 3 つを比較する:

| 対照 | probe |
|---|---|
| 正しい `/ri/`→SP | P-RI-FINAL |
| 正しい `/N/`→SP（鼻音プロトタイプ） | P-N-FINAL |
| `/r/` なしの `/i/`→SP | P-I-FINAL |

**`/ri/` 出力の終端 mel が `/N/` 対照へ近づくほど鼻音化が強い**、と話者内差分で
測る。話者間・世代間の絶対水準差を構造的に相殺するため、§2-3 の H5 で問題に
なる走行間変動に対して頑健である。

### 5-2. 計器への制約

- 一次指標は **正規化前の生波形**で測る。`gate_synth.synth_song` は
  `wav_peak_raw` / `wav_rms_raw` のファイル全体スカラーしか record.json に
  残さずピーク正規化した PCM16 を書き出すため、**P1/P2 の生サンプル窓が
  再構成できない**。`hnr_median_db` は絶対 RMS 閾値（`1e-3`）で有声判定するので
  正規化後波形で測ると有声フレーム集合が変わる。よって
  **正規化前波形の該当窓を測定用に保存する経路を実装で確保する**
  （VG-L0 probe が踏み抜いた「ピーク 0.6 正規化後 RMS は実質クレストファクタ」の再演を避ける）
- 計測不能は例外でなく `status` 付きの記録
- **ASR の文字認識は主判定にしない**。細かな鼻音化を一般 ASR が正しく校正できる
  保証がないため、**補助軸に留める**

## 6. ブラインド耳校正（40 unique + 8 duplicate）

三世代の音源を**ランダム化し、モデル名・話者・世代を隠して**提示する。
各クリップを 3 軸で 0〜3 判定:

| 軸 | 0 | 3 |
|---|---|---|
| `nasalization` | なし | 明確に「ん」 |
| `continuous_voicing` | 正常解放 | 語尾が止まらない |
| `intelligibility` | 明瞭 | 語として不成立 |

- **20% を重複提示して自己一致率（intra-rater agreement）も記録する**。
  この値が**計器の目標値**になる — 自己一致率を超えられない計器は成立と呼べない
- 既存 record（s4/s5/s6）の耳判定は**再ラベルなしの追加 hold-out** として使う

### 6-1. 耳判定の対象集合（事前固定）

**360 セルは全て機械評価**する。耳判定はその部分集合に限り、
**事前に固定する**（事後に足すと選択バイアスが入る）:

**割付は Gate の充足可能性から逆算する**（2026-08-20・P1 訂正）。初版の割付は
ブロック A だけが run7 以前を供給し、**校正セット（run5+run6）が 6 セルしか
なかった** — §7-0 (4b) は各分割で `scored >= 12` かつ `n(break) >= 5` かつ
`n(ok) >= 5` を要求し、Gate 3 は user の run5↔run7 対応ペアを 5 組要求するのに
**1 組しか無かった**。つまり**完璧な計器でも 8-B が無条件でブロックされる**
割付だった。世代方向へ配り直す:

```
cell_id = <generation>/<speaker>/<probe>/<beats>/<pitch>
  generation ∈ {run5, run6, run7}
  beats      ∈ {b1, b2, b4}        # ★ §3 の秒 bin d0..d4 とは別名前空間
  pitch      ∈ {p57, p62, p65}
  P-ANCHOR は <generation>/<speaker>/P-ANCHOR/<region>

A 世代横断コア (24) — 校正セットと Gate 3 の供給源:
  A1 user の run5↔run7 対応ペア（Gate 3 の 5 組・破綻寄りと正常寄りを混ぜる）
       {run5, run7} × user × {
           P-RI-FINAL/b4/p57, P-RI-FINAL/b2/p57, P-RI-FINAL/b4/p62,
           P-I-FINAL/b4/p57,  P-N-FINAL/b4/p57 }              -> 10
  A2 {run5, run6} × ritsu × {P-RI-FINAL/b4/p57, P-RI-FINAL/b2/p57,
                             P-N-FINAL/b4/p57}                ->  6
  A3 {run5, run6} × pjs   × {P-RI-FINAL/b4/p57, P-I-FINAL/b4/p57,
                             P-RI-MEDIAL/b4/p57}              ->  6
  A4 run6 × user × {P-RI-FINAL/b4/p57, P-N-FINAL/b4/p57}      ->  2

B run7 終端条件スイープ (12) — hold-out の主力（A と p62 で分離し衝突回避）:
  run7 × {ritsu, pjs, amitaro}
       × {P-RI-FINAL, P-RI-MEDIAL, P-N-FINAL, P-SU-FINAL} / b4 / p62

C 極値 (2):
  run7/ritsu/P-RI-FINAL/b1/p57,  run7/ritsu/P-RI-FINAL/b1/p65

D 実曲アンカー (2):
  run7/ritsu/P-ANCHOR/sakura-kagiri   （§0-4 の陽性対照）
  run7/pjs/P-ANCHOR/sakura-kagiri     （同条件の陰性）

-> unique 40（重複ゼロを assert する）
```

**充足可能性の検算**（この表を満たさない割付は pin しない）:

| 要求元 | 条件 | 本割付の供給 |
|---|---|---|
| §7-0 (4b) 校正 | `scored >= 12` | run5+run6 = **19** ✓ |
| §7-0 (4b) hold-out | `scored >= 12` | run7 = **21** ✓ |
| Gate 3 | user の run5↔run7 対応 `>= 5` | A1 で **5 組** ✓ |
| §0-4 陽性対照 | ritsu「かぎり」 | D に収載 ✓ |

**クラス充足（`n(break)>=5` / `n(ok)>=5`）は耳が付くまで確定しない**ので、
割付側でできるのは**両分割に破綻寄り（`/ri/` 終端・ritsu 系）と正常寄り
（`/N/`・語中・pjs 系）を十分に混ぜておく**ことまでである。それでも足りなければ
§7-0 (4b) の `insufficient_class_support` で fail-closed する
（**耳ラベルを事後に足して埋めない**）。

**重複提示 8 件（20%）**: A/B/C/D の各ブロックから `cell_id` 昇順で先頭 2 件ずつ。

**この 40 + 8 を `s7_listening_set.json` へ書き出し、sha256 を pin してから
レンダを開始する。** 生成 → pin → レンダ の順序を守れば、機械結果を見てから
対象を選ぶ経路が構造的に閉じる。レンダ後に集合を変えたくなった場合は
**本 memo の改訂**として扱う。

提示は**ランダム順・モデル名/話者/世代を伏せる**（伏せないのは §6 の
`expected_terminal` と `position`）。重複分は被験者に重複であることを
知らせない。

## 7. Gate — 観測子の合格条件

### 7-0. 実行規則（**事前登録・実装間で結果が変わらない形にする**）

Gate は「有料の 8-B を起こすか」を決める。**「陽性」「分離」「同方向」
「誤陽性」に式と閾値が無ければ、同じデータで実装ごとに合否が変わる**。
以下を事前登録する。

**(0) 正規化** — 各機械軸は **(話者 × 世代) 内で z 化**する:

```
z(x) = (x - median_{cell in speaker×generation}(x)) / (1.4826 * MAD_{same})
```

話者内・世代内で中心化するので、§2-3 の走行間の水準シフトを構造的に落とす。
MAD == 0 の軸はその (話者×世代) で `status = degenerate_axis` とし、
primary 軸の候補から外す。

**(1) 耳ラベルの 2 値化と borderline の扱い** — §6 の 0〜3 判定から:

**`break` は「期待した終端からの逸脱」で定義する**（2026-08-20・P1 訂正）。
初版は全セル一律に `nasalization >= 2` を `break` にしていたが、
**P-N-FINAL は終端が `/N/` であるのが正解**なので、正しくレンダされた
`/N/` ほど `nasalization` が高く出て `break` に分類されてしまう。
一方 Gate 4（§7-0 (8)）は同じ `/N/` セルを**陰性**として FPR <= 0.10 を要求する。
つまり **hold-out accuracy は `/N/` を break と呼ぶほど上がり、Gate 4 は
それを罰する**という自己矛盾になり、計器がラベルと一致していても
8-B を偽ブロックしうる。よってセルの `expected_terminal` で場合分けする:

**position も併せて場合分けする**（2026-08-20 追加）。`expected_terminal` だけで
切ると **P-RI-MEDIAL** が取り残される — 語中 `/ri/` は**次の音素が続くので
境界後に有声が継続するのが正しい挙動**であり、`continuous_voicing >= 2` を
そのまま `break` にすると、Gate 4 が陰性として要求する同じセルを
hold-out 側は break と呼ぶほど得点する。`/N/` と同型の自己矛盾になる。

```
セル定義から機械的に決まる 2 つのキーで場合分けする:
  expected_terminal ∈ {ri, i, su, N}
  position          ∈ {final, medial}

position == final かつ expected ∈ {ri, i, su}（鼻音化も継続発声も逸脱）:
  break : max(nasalization, continuous_voicing) >= 2
  ok    : max(nasalization, continuous_voicing) <= 1 かつ intelligibility <= 1

position == final かつ expected == N（鼻音であるのが正解）:
  break : continuous_voicing >= 2  または  intelligibility >= 2
  ok    : continuous_voicing <= 1  かつ    intelligibility <= 1
  ※ nasalization は **この行では使わない**

position == medial（次音素が続くので終端解放は起きなくてよい）:
  break : nasalization >= 2  または  intelligibility >= 2
  ok    : nasalization <= 1  かつ    intelligibility <= 1
  ※ continuous_voicing は **この行では使わない**
     （語中の有声継続を失敗ラベルにしない）

いずれも上記に当たらなければ borderline
```

**Gate 4（§7-0 (8)）の陰性集合も同じ 2 キーの定義で判定する。** これで
「正しい `/N/`」と「正しい語中 `/ri/`」は自分の基準で `ok` になり、
hold-out accuracy と FPR ゲートが逆を向く経路が閉じる。

**Gate 4 の陰性集合もこの `expected_terminal` 相対の定義で判定する**ので、
正しくレンダされた `/N/` は自分の基準で `ok` になり、矛盾は消える。
§6 の耳ラベル提示でも **どの終端が期待されているかは伏せない**
（伏せると聴取者が `/N/` セルを「鼻音化した失敗」と採点してしまう）。
伏せるのは**話者・世代・モデル名**であって、譜面上の期待終端ではない。

- **borderline は校正から除外**する（閾値を borderline に合わせない）
- **hold-out では除外せず `unscored` として保持**し、件数を報告する
  （捨てた数が見えないと、都合の悪いセルを borderline に流す抜け道になる）

**(2) 軸の向き（orientation）を先に決める** — **絶対値の margin だけでは
向きが失われる**。`hnr_delta_db` のように「小さいほど break」の軸が primary に
なった場合、実装 A が `x >= θ` を break、実装 B が `x <= θ` を break と読み、
**同じデータから逆の Gate 結果**が出る。よって:

```
orient(axis) = sign( median(z_axis | break) - median(z_axis | ok) )
               ※ 0 の場合は degenerate_axis 扱いで候補から外す
z'_axis      = orient(axis) * z_axis      # 「大きいほど break」へ正規化
```

以降の **margin / AUC / 閾値判定 / Δz_primary は全て `z'` で行う**
（向きを一度だけ確定し、下流で二度と解釈しない）。

**事前登録された期待符号との照合**: §5 で期待符号を宣言済みの軸について、
校正で得た `orient` が期待と**食い違った場合は黙って反転させない** —
`status = orientation_conflict` を立てて記帳する（期待が外れたこと自体が所見）。

**(3) primary 軸の選定**（校正セットのみで実行・hold-out は見ない）:

```
margin(axis) = median(z'_axis | break) - median(z'_axis | ok)   # 定義上 >= 0
primary      = argmax_axis margin(axis)
tie-break    = 軸名の辞書順昇順（再現可能に固定）
```

**(4) 閾値の決定**（校正セットのみ）: primary 軸の `z'` 上で
**Youden's J（= 感度 + 特異度 − 1）を最大化する点** `θ` を採る。同点なら
`θ` は候補の**中央値**を採る。分類規則は **`z' >= θ` を break** に固定する。

**(4b) 分割のクラス充足検査（fail-closed・2026-08-20 追加）**: 除外セルや
`borderline` の落ち方によっては、校正セットや hold-out が **片クラスだけ**に
なりうる（本書自身がその経路を許している）。その状態では
**orientation・AUC・Youden 閾値がいずれも未定義**である一方、
単一クラス集合に対する素の accuracy は高く見えてしまう。したがって:

```
必要条件（校正セット・hold-out それぞれで独立に検査）:
  n(break) >= 5  かつ  n(ok) >= 5
  かつ scored（= borderline でない）セルが各分割で >= 12

満たさない場合:
  status = insufficient_class_support
  -> Gate 不成立（§0-6）。8-B へ進まない
     ※ accuracy だけを見て「通った」と読まない
```

不足が起きても**耳ラベルの対象セルを事後に足して埋めない**
（事後に標本を足すと事前登録が崩れる）。足す必要があるなら
**本 memo の改訂**として層化設計をやり直す。

**(5) Gate 1「陽性として拾う」**: P-ANCHOR「かぎり」(ritsu・run7) が
`θ` で **break 側に分類される**こと。1 セルの二値判定であり、閾値調整で
これを通してはならない（§0-6）。

**(6) Gate 2「分離する」**: hold-out 上で

```
AUC(primary) >= 0.80
かつ 校正で固定した θ での accuracy >= 0.75
```

**(7) Gate 3「同方向に並べる」**: user について run5→run7 の
`Δz_primary` の**符号**が、耳 severity の変化の符号と一致すること。
**差分軸でのみ**評価する（絶対値は走行間変動を拾う）。

**符号は「1 つ」ではないので集約規則を凍結する**（2026-08-20 追加）。user には
run5/run7 で対応するセルが複数あり、耳 severity も 3 軸ある。セルごと・軸ごとに
向きが割れると、実装によって Gate 3 が逆の結論を出す:

```
参加セル:
  §6 の listening set のうち generation ∈ {run5, run7} かつ speaker == user
  かつ 両世代で対応が取れている cell_id（片方欠測は除外し件数を記帳）

機械側の代表値:
  Δz_primary(cell) = z'_primary(run7) - z'_primary(run5)
  代表値 = 参加セルにわたる **median**（平均でなく median。外れセルに強い）

耳側の代表値:
  severity(cell, gen) = max(該当 position/expected の break 判定に使う軸)
      # §6 の 2 キー場合分けで「使わない」と定めた軸は max に入れない
  Δseverity(cell) = severity(run7) - severity(run5)
  代表値 = 参加セルにわたる **median**

判定:
  sign(median Δz_primary) == sign(median Δseverity)   -> Gate 3 pass
  どちらかが 0（同値・タイ）                          -> **pass にしない**
                                                         status = tied_direction
  borderline ラベルのセルは Δseverity の計算から除外し、除外件数を記帳
  参加セルが 5 未満                                    -> undetermined（Gate 不成立）
```

**タイを pass 側に倒さない**のは、「動かなかった」を「同方向だった」と
読み替えないためである（§9-0b と同じ規律）。

**(8) Gate 4「誤陽性にしない」**: うみ・語中 `/ri/`・正しい `/N/` のセル群で

```
false_positive_rate = #(θ で break 判定) / #(当該セル群) <= 0.10
```

**(9) 聴取信頼性との対照**（**尺度を揃える**・2026-08-20 訂正）:

初版は「hold-out accuracy は weighted κ を超えられない」と書いたが**誤り**
だった。**weighted κ は 0〜3 の順序尺度に対する偶然補正済み一致**であり、
accuracy は**導出後の 2 値ラベル**に対する生の一致率である。**尺度も対象も
違うので κ は accuracy の上界にならない** — クラス不均衡やラベル変換だけで
`accuracy > κ` は正当に起こりうる。この規則のままだと**妥当な計器を
`suspect_overfit` と誤判定して 8-B を止めてしまう**。

したがって対照は**同じ対象・同じ尺度**で取る:

```
rater_self_consistency_binary
  = 重複提示ペアのうち、(1) の規則で導出した break/ok が
    2 回とも一致したペアの割合          # 2 値・生の一致率
```

- 比較対象は **hold-out accuracy vs `rater_self_consistency_binary`**
- **weighted κ は残すが、Gate には使わない** — 聴取者の品質診断
  （rater-quality diagnostic）として別枠で報告する
- `accuracy > rater_self_consistency_binary` は**停止条件ではなく flag**:
  `status = exceeds_rater_consistency` を立てて人が見る。ノイズのある
  ラベルに対して機械が真値側で上回ることは原理的にありうるため、
  **自動停止にはしない**

**独立検算できる worked example**（数値は**説明用の架空値**）:

| セル | 耳 | `z(excess_tail_voiced_ms)` | `z(hnr_delta_db)` |
|---|---|---|---|
| A | break | +1.8 | −0.4 |
| B | break | +1.4 | +0.9 |
| C | ok | −0.9 | +0.2 |
| D | ok | −1.1 | −0.7 |

`margin(excess_tail_voiced) = |median(+1.8,+1.4) − median(−0.9,−1.1)| = |1.6 − (−1.0)| = 2.6`、
`margin(hnr_delta) = |median(−0.4,+0.9) − median(+0.2,−0.7)| = |0.25 − (−0.25)| = 0.5`。
よって primary = `excess_tail_voiced_ms`。Youden's J 最大点は
`θ = 0.25`（break 2/2・ok 0/2 → J = 1.0）。この `θ` を hold-out へ持ち込む。

### 7-0b. 合格条件（上記の式で判定する）

最低でも次の全てを満たすこと:

1. 既知の ritsu「かぎり」（P-ANCHOR・陽性対照）を**陽性として拾う**
2. PJS の同条件を**陰性として分離する**
3. user の run 5→7 方向を**耳判定と同方向に並べる**（§2-3 のとおり
   **差分軸でのみ**評価する。絶対値は走行間変動を拾う）
4. **うみ・語中 `/ri/`・正しい `/N/` を誤陽性にしない**
5. **run 5/6 で校正し、未使用の run 7 でも成立する**（hold-out は世代分割）
6. 同一 `ExecutionProfile` の**独立プロセス再生成**で特徴量・WAV が再現する
   （VG-L0 で確立した Render Reproducibility の様式。同一プロセス内反復は
   independent replay の証拠にならない）

**通らない場合**: 観測値は診断レポートとして残すが**自動 Gate には昇格させず、
8-B へ進まない**（§0-6）。

### 7-1. 回帰・変化判定の閾値（**8-0 完了後・8-B 開始前に固定**）

事後に閾値を決めると何でも有意にできる。**Gate 通過直後、8-B の収録を始める
前に凍結**し、以後変更しない:

```yaml
machine:
  # --- 段階 0: まず決定論を試す（最も安く最も強い） ---
  determinism_test:
    run 8-R は run 7 の完全同一設定なので、出力が **bit 一致**なら
    走行間ドリフトは 0 であり、以降の補正は一切要らない。
    まずこれを検査する（§12 OQ1 の seed pin 状況の実地回答にもなる）。

    固定するもの（再裁定 8・1 つでも動けば determinism_test は無効）:
      dataset bytes / row 順序 / config / seed / checkpoint 初期値 /
      dependency pins / ExecutionProfile / sampler 設定

    比較する hash:
      40K checkpoint / exported ONNX / speaker embeds /
      phonemes.json / 固定 probe WAV

    判定:
      全て bit 一致 -> run 8-B との差は「dosage 固定 target 置換の差」として
                       強い因果解釈が可能
      一致しない   -> run 8-B を実行してよいが **結果は探索的**。
                       単一 run だけで「target 置換が原因」と断定しない

  # --- 段階 1: bit 一致しなかった場合 ---
  drift_model:
    per_speaker_shift:  b_s = mean_i( x_i(run 8-R) - x_i(run 7) )   # 平均シフト
    within_scatter:     SD_d,s = SD_i( 同上の対応差 )（ddof=1）
    corrected_effect:   Δ*_s = ( x(run 8-B) - x(run 7) ) - b_s
      # ★ 初版の誤り: MDC95 = 1.96 * SD_d だけを使い b_s を無視していた。
      #   未処置セルが一律 +10 ドリフトすると SD_d = 0 -> MDC95 = 0 になり、
      #   run 8-B の同じ +10 が「標的パックの効果」と誤判定される。
      #   §2-3 の実測（ritsu の 3 区間が +1.65/+5.35/+7.94 と**同符号**）は
      #   まさに平均シフト型のドリフトであり、この穴は実データで踏む
    between_run_variance: sigma_between
      # k 本の未処置反復があるとき b_s の標本分散から推定する。
      # **k = 1 では推定できない**（1 点に分散は無い）
    bound:
      # ★ 2026-08-20 訂正: 初版は sigma_between^2 をそのまま使っていたが、
      #   処置走行 1 本と未処置 k 本の**平均**を引く以上、走行水準の分散は
      #   sigma_between^2 * (1 + 1/k) になる（1/k は対照平均側の分）。
      #   セル雑音も同じ係数がかかる。初版の bound は小さすぎ、
      #   ただのドリフトを「パックの効果」と呼ぶ側へ倒れていた
      Var(delta*) = ( sigma_between,s^2 + SD_d,s^2 / N ) * (1 + 1/k)
      MDC95_s     = t(0.975, df = k - 1) * sqrt( Var(delta*) )
      # sigma_between は k 本から推定するので df = k-1。少数走行なので
      # 正規近似（1.96）ではなく t を使う
      k >= 2:  |Δ*_s| > MDC95_s のとき「変化した」と判定でき、
               §9 の行を**因果裁定として引ける**
      k == 1:  Δ*_s は報告するが sigma_between が未推定のため
               **判定は provisional 止まり**（§9-0 の confounded 扱いを維持）
    grouping:  軸 × 話者ごとに独立算出（軸間・話者間でプールしない）
    min_n:     その (軸 × 話者) で対応の取れたセルが N >= 20
      # N < 20 は話者をプールし status = mdc_pooled。
      # プールしても N < 20 なら当該軸は undetermined
  rule: 上記 bound を満たしたときのみ「変化した」と判定する
human:
  severity_shift:  中央値で 1 段以上（0–3 スケール）
  agreement_floor: 20% 重複提示から得た weighted κ を事前固定し、
                   これを下回る軸は判定に使わない
                   # κ は信頼性の下限フィルタとしてのみ使う。
                   # 機械 accuracy の天井には使わない（§7-0 (9)）
```

**独立検算できる worked example**（数値は**説明用の架空値**）:
(軸 = `excess_tail_voiced_ms`, 話者 = user) で N = 25 セル。

```
run 8-R vs run 7:  b_user = +10.0 ms,  SD_d,user = 2.0 ms
run 8-B vs run 7:  生の Δ = +12.0 ms
drift 補正後:      Δ* = 12.0 - 10.0 = +2.0 ms
```

**初版の式なら** `MDC95 = 1.96 * 2.0 = 3.92`、生の `Δ = 12.0 > 3.92` で
「変化した」と誤判定していた — 実体は**未処置でも起きる +10 のドリフト**である。
補正後は `Δ* = 2.0` で、k = 1 なら `sigma_between` が未推定につき
**provisional**。仮に 8-R を 2 本取って `sigma_between,user = 3.0 ms` を得たなら
（**初版はここで 1.96 を使い `(1 + 1/k)` を落としていた。式だけ直して
例を直さないと、独立検算する実装が直したはずの過小評価をそのまま再現する**）:

```
k = 2, N = 25, SD_d = 2.0, sigma_between = 3.0

Var(delta*) = ( 3.0^2 + 2.0^2 / 25 ) * (1 + 1/2)
            = ( 9.0 + 0.16 ) * 1.5 = 13.740
MDC95       = t(0.975, df=1) * sqrt(13.740)
            = 12.706 * 3.7068 = 47.10 ms

|Δ*| = 2.0 << 47.10  ->  「変化した」とは判定しない
```

**47.10 ms という値がこの設計の本音**である。初版の 5.93 ms と 8 倍近く違い、
k = 2 では現実的な効果量がまず届かない。§7-1 の「k=2 は形式的には裁定可・
実質は検出力ほぼ無し」はこの数字のことである。

**k が小さいと bound が実用にならない（正直な帰結）**: `t(0.975, df=k-1)` は

| k | df | t | 係数 `t * sqrt(1 + 1/k)` |
|---|---|---|---|
| 2 | 1 | **12.71** | **15.6** |
| 3 | 2 | 4.30 | 4.97 |
| 5 | 4 | 2.78 | 3.05 |

**k = 2 では正規近似の約 8 倍**の幅になり、よほど大きな効果でないと
「変化した」と言えない。

**この係数は厳密保証ではない（再裁定 7）**: 上の式は
**(a) run 間誤差が独立・(b) 同分散・(c) おおむね正規** を仮定している。
学習 run の出力分布は**実測されていない**ので、これらは検証済みの前提ではない。
したがって係数は「この閾値を超えたら本物」という保証としてではなく、
**「少数反復では検出力が極端に低い」ことを示す警告**として使う。
数値を有意性の免罪符に使わない。

したがって:

- **段階 0（bit 一致）が実質的に唯一の現実的な因果経路**である。
  一致すればドリフト 0 が実証され、この bound 自体が不要になる
- 一致しなかった場合、**k = 2 は「形式的には因果裁定可・実質的には検出力ほぼ無し」**
  と正直に記帳する。実用的な検出力には **k >= 3**（8-R ×3 = 追加 ≈$4.20）が要り、
  それでも中程度の効果は拾えない
- **この算術は「反復を増やせば解決する」話ではない**。学習が非決定論なら、
  この規模の実験で単一介入の因果を主張すること自体が高くつく、という事実を
  §9-0 の判断材料として User へ渡す

**費用への含意**: 段階 0 で bit 一致すれば追加費用ゼロで因果裁定に到達する。
一致しない場合は上表のとおり、**費用よりも検出力が律速**になる。
**先に段階 0 を回す**のが最短かつ最安であり、かつ唯一の現実的な道筋である。

**PJS・うみ・語中 `/ri/`・`/su/` の「悪化」判定にも同一規則を使う**
（改善だけ緩い基準で見ない）。

### 7-2. Gate 不成立時の GPU $0 成果物（3 レバー診断）

観測子が Gate を通らなくても**空手で終わらせない**（§0-6）。3 本とも
**診断であって修復策ではない**:

| レバー | 実験系 | 言えること |
|---|---|---|
| 1. `/r/`-`/i`/ duration 再配分 | note 総長を固定したまま配分比を **0.5 / 1.0 / 1.5 / 2.0** で振る | **duration conditioning の感度**。§2-3 H-dur の到達限界のとおり、改善しても原因の局在は主張しない |
| 2. 明示 SP 挿入 | 発話末には既に tail SP があるため、**最終母音の一部を SP へ再配分し総尺を固定**して試す | **終端 cue の感度**。§4-0-4 の「改変レンダ」系統として分離記帳 |
| 3. ritsu ⇄ pjs `spk_embed` 補間 | **0 / .25 / .5 / .75 / 1** で単調性を見る（`gate_synth.find_speaker_embed` の 384 次元ベクトル） | **speaker conditioning の直接診断**。単調に消えるなら破綻は話者埋め込みが担っている |

**レバー 3 の正直会計**: 補間は identity を動かす。破綻が消えても
「ritsu が治った」のではなく「**別の声になった**」であり、
**修復策ではなく診断限定**である（第三の声の議論と混同しない）。

## 8. Run 8-B — **dosage-fixed targeted partial replacement**

**正式名称 = `dosage-fixed targeted partial replacement`**（User 裁定 2026-08-20
第 4 次で確定）。**「5〜8 分を全部学習投入する設計ではない」**ことを名称に
含める。5〜8 分は**候補プール**であり、学習投入量は run 7 と同一に固定される
（§8-1 / §8-3）。

### 8-1. 候補プールの収録設計（`capture_pool` = 300–480 s・16 カード）

**5〜8 分は「収録量」であって「学習投入量」ではない**（第 4 次裁定 1）。
3 つの量を別名で固定し、混同を構造的に防ぐ:

```yaml
capture_pool:                  # 収録する量（User の作業量）
  raw_seconds: 300–480

train_user_dose:               # 学習へ投入する量（run 7 と同一・不変量）
  voiced_seconds:   171.88
  total_ph_dur_s:   233.395
  row_count:        15

heldout:                       # 候補プールの余剰（評価専用）
  = capture_pool − train_user_dose に相当する分 + §8-4 の control cards
```

`train_user_dose` の 3 値は run 7 実測（`_voiced_ph_dur_seconds` / assembly
manifest / `run7_dataset_pins.json` の `user.n_wavs` = 15）であり、
**本書で再宣言せず run 7 の pin を単一ソースとして継承する**。

既存キット規約（**1 カード 20〜60 秒・スマホ録音可・順不同**・
`recording_kit/README.md`）を維持する。

```
9 target cards:
  3 durations（1 / 2 / 4 拍）× 3 pitch bands（低 / 中 / 高）
  各カード: かぎり / ひかり / いのり / めぐり から **決定論規則で 3 語**
  （語リストを文番号順に回して 1 語ずつ落とす）を各 1 回
  → 語尾 /ri/→SP を **27 イベント**（★ 初版の 72 は dosage 固定と
    両立しない = §8-5-2b。**User 裁定が要る変更**）

3 held-out target cards:  # ★ 学習には入れない — primary held-out の実体
  語尾 /ri/→SP・**訓練で一度も使わない別語句**
  みのり / たより / みどり / のぼり を各 2 回
  3 pitch bands（低/中/高）× 4 拍固定（最も破綻しやすい帯）
  → 語彙を越えた一般化の検定用に 24 イベント

3 control cards:   # ★ 学習には入れない（§8-3）
  phrase-medial /ri/
  phrase-final  /i/
  phrase-final  /N/

1 control card:    # ★ 必須。学習には入れない（§8-3）
  phrase-final /su/（みわたす系）
```

**held-out target cards を別立てにする理由（2026-08-20 追加）**: §8-5-2 は
target 9 セルを**全て学習へ入れる**と定めており、9 セルが使う語句は
`かぎり / ひかり / いのり / めぐり` の 4 語だけである。したがって
**余剰テイクは必ず同一語句**になり、§8-5-5 の **primary held-out
（別語句・別 take）が構造的に空になる** — 語彙を越えた一般化が原理的に
検定できなくなる。訓練に一度も入らない語句を**事前登録して別カードで収録**
することでこれを塞ぐ。

**辞書被覆**: `みのり` / `たより` / `みどり` / `のぼり` はいずれも基本モーラのみ。
未収載が出たら代用せず fail-closed で除外し、**primary held-out が空になった
場合は §8-5-5 のとおり当該主張を `undetermined` で記帳**する。

**カード枚数と候補プールの整合**: 9 target + 3 held-out target + 4 control =
**16 カード**。§8-1 の `capture_pool` 上限 480 s に収めるには
**1 カード平均 30 s 以内**（規約の 20〜60 s の下寄り）で組む。
超過する場合に削るのは **control ではなく target の反復回数**とし、
**held-out target は削らない**（削ると primary held-out が空に戻るため）。

**重要 — イベント数ぶんの短いファイルにはしない。** 9 個の target カード内に 27 イベントを
まとめる。学習側が**行単位でサンプリング**する場合、ファイル数そのものが
別の介入になるため。

各カードには**固定テンポ・ガイド音・終端後の明示的な無音**を入れる。

**音高帯は A3/D4/F4 固定にしない（第 3 次裁定）**。User が安定して出せる帯で
なければ「低/中/高」という因子が成立しないため、**既存 User T1 録音の実測
中央値から User 固有の low / mid / high ガイドを作る**。intake では:

```yaml
achieved_f0_median      # 実測 f0 中央値
achieved_midi           # 同 MIDI 換算
achieved_band           # 実測から割り当てた帯
```

を記録し、**意図した帯から外れたテイクは実測帯へ再ラベルするか除外**する。
**意図した帯として黙って記帳しない**（黙って記帳すると音高因子が壊れる）。

### 8-2. アラインメント

今回の対象は**数十〜数百ミリ秒の語尾**である。既存 T2 の
「モーラ数比例の粗いヒューリスティック」では解像度が足りない。
**score 由来の既知境界 + 手動スポット検査**でアラインメントする。

### 8-3. 単一介入の会計 — **dosage 固定・置換設計**（第 3 次裁定の核心・第 4 次で精緻化）

初版は「run7 corpus **+** 標的 pack」だった。しかし user の現行 voiced は
**171.88 s** しかなく、5〜8 分（300–480 s）を**足す**と user データは
**3〜4 倍**になる。すると「**標的だから効いた**」と「**単に量が増えたから
効いた**」が交絡し、8-B の主張が立たない。

**したがって量を増やさない。user の有効 dosage を固定し、既存の非標的素材の
一部を同秒数の /ri/ 標的素材へ置換する**:

```
不変量（fail-closed で assert）:
  user_effective_dosage(run8) == user_effective_dosage(run7)

変える:
  user 素材の**構成**のみ
    既存非標的素材から N 秒を退避
    → 同じ N 秒を user_phrase_final_ri_pack_v0.1 で置換

変えない:
  user 総 dosage / amitaro teacher / PJS・ritsu 素材 / 正規化条件 /
  spk_id / 学習設定 / seed・ExecutionProfile / 40K 手順 / sampler weighting
```

**追加素材を別 teacher ID へ複製したり oversampling したりしない**
（二介入になるため）。

**量を増やす設計を採る場合**（置換が成立しなかった場合の退避路）、
結論は「**標的内容 または user 量増加のどちらかが効いた**」までに
**限定して記帳する** — 分離できていないものを分離したと書かない。

### 8-4. 対照は学習に入れない（held-out 専用）

`/su/`→SP・`/i/`→SP・phrase-medial `/ri/` は **8-B の学習素材から除外**し、
**held-out 評価専用**にする。

**理由**: これらを学習 pack に入れると negative control ではなく
**active control**（=それ自体が介入）になり、対照として機能しなくなる。
収録はする（§8-1 の control cards）が、**学習コーパスには投入しない**。

| 対照 | 収録 | 学習投入 | 用途 |
|---|---|---|---|
| `/su/`→SP | する | **しない** | 標的音素対策か一般解放技能かの弁別 + §5-0 の無声対照 |
| `/i/`→SP | する | **しない** | `/r/` の寄与の分離 |
| phrase-medial `/ri/` | する | **しない** | 位置（終端性）の分離 |

### 8-5. 置換の実行規則（第 4 次裁定・実装契約）

裁定の 2 項目は数字の上で衝突する — §8-1 の収録 300–480 s に対し、
学習投入枠は voiced 171.88 s しかない。**(b) 部分投入 + 余剰 held-out** で
解く（採用理由 = 標的内容とデータ量の交絡を 3 案中で最も抑えられる）:

| 案 | 内容 | 評価 |
|---|---|---|
| (a) 完全置換 | user 素材を全て標的 pack へ差し替え | dosage は固定できるが**非標的の一般語彙を失う**。回帰リスクが高い |
| **(b) 部分投入 + 余剰 held-out** ★採用 | 収録は候補プールとして 300–480 s 行い、**学習投入は run 7 と同 dosage だけ**決定論規則で選抜。残りは held-out へ | **両裁定を同時に満たす**。余剰が §8-4 の評価セルを増やす副次利得もある |
| (c) 量を増やす | §8-3 の退避路 | 結論が「標的内容 **または** 量増加」に限定される |

#### 8-5-1. 「部分投入」の定義（(a) への退化を防ぐ）

**新規 target だけで 171.88 s を構成したら、それは実質 (a) 完全置換である。**
(b) は必ず次の形を取る:

```
既存 user 一般素材の一部（retained baseline）
  +
新規 /ri/ target 素材
  =
run 7 と同一 dosage
```

#### 8-5-2. 既定構成（15 row）

| 区分 | row 数 | 内容 |
|---|---|---|
| **target** | **9** | §8-1 の 9 セル（3 durations × 3 pitch bands）を**全て収載**。原則 **1 セル 1 row**。各 row 内の `/ri/` 終端は **`transcriptions` 上で明示的に SP へ接続する** |
| **retained baseline** | **6** | 既存 user 素材から、T0/T1/T2 と音素被覆を維持するよう**メタデータだけで決定論選抜** |
| 合計 | **15** | = `train_user_dose.row_count` |

**`/ri/`→SP を transcriptions で明示接続することが、標的 exposure が実際に
モデルへ入る唯一の経路である**。§I の実装事実（レンダラ側はフレーズ概念を
持たず、SP は先頭 1 個と末尾パッドのみ）より、モデルが終端解放を学べるのは
**学習データ側の音素列に `→SP` 遷移が実在する場合に限る**。ここは
H-terminal-transition-density の直接の操作点にあたる。

#### 8-5-2b. **target 秒数の上限**（**E=3 conditional approval** — User 再裁定 2026-08-20）

**初版の「4 語 × 2 回 = 72 イベント」は dosage 固定と両立しない。**
row は原子単位で、9 target row + 6 baseline row = 15 row が
`train_user_dose`（voiced 171.88 s / total 233.395 s）に収まらねばならない。
収録してから初めて破綻するのを避けるため、**先に算術を通しておく**。

**拍と秒の内訳を分けて記帳する**（再裁定 1）。初版は「2.50 / 3.33 / 5.00 s」を
「終端音符の尺」であるかのように置いていたが、**これはイベント全尺**であり、
**固定前置文脈 2 拍を含む**。混同すると E の最大値が変わる（下記）ので、
台帳・manifest は必ず 4 つを別フィールドで持つ:

```yaml
terminal_note_beats     # 1 | 2 | 4
fixed_context_beats     # 2（本設計の既定・助走「か」「ぎ」相当）
total_event_beats       # = fixed_context_beats + terminal_note_beats
total_event_seconds     # = total_event_beats * 60 / tempo_bpm
```

固定テンポ **72 BPM**（1 拍 = 60/72 = **0.8333 s**）での内訳:

| terminal_note_beats | 終端音符のみ | fixed_context 2 拍 | total_event_beats | **total_event_seconds** |
|---|---|---|---|---|
| 1 | 0.833 s | 1.667 s | 3 拍 | **2.50 s** |
| 2 | 1.667 s | 1.667 s | 4 拍 | **3.33 s** |
| 4 | 3.333 s | 1.667 s | 6 拍 | **5.00 s** |

**この 2 拍の前置が E の解を決めている**（再裁定 1 の但し書き）:

| 前置文脈 | 1 E あたり（3 pitch × 3 duration） | 103.128 s 以下の最大 E |
|---|---|---|
| **あり（2 拍・本設計）** | 3 × (2.50+3.33+5.00) = **32.5 s** | **E = 3** |
| なし（終端音符のみ） | 3 × (0.833+1.667+3.333) = **17.5 s** | E = 5 |

**前置文脈を落とすなら E を取り直す**。設計書・score 生成器・dry-run manifest の
3 箇所で `fixed_context_beats` が一致していることを機械検査する。

§8-5-3 の要求（**セル間でイベント数を揃える**）より、1 セルあたり `E` イベント
とすると 9 セル合計は `E × 32.5 s`:

| E | target voiced | 対 171.88 s | 残る baseline 枠（6 row） | 可否 |
|---|---|---|---|---|
| 8（初版） | 260.0 s | **151%** | 負 | **不成立**（100% 置換でも入らない） |
| 5 | 162.5 s | 94.5% | 9.4 s | 不成立（6 row に配れない） |
| 4 | 130.0 s | 75.6% | 41.9 s ≒ 7.0 s/row | 際どい |
| **3** | **97.5 s** | **56.7%** | **74.4 s ≒ 12.4 s/row** | **voiced 面のみ成立**（下記の但し書き） |

**事前登録する上限と最大化目的**（再裁定 4 で表現を訂正）:

```
制約:   target_voiced <= 0.60 * train_user_dose.voiced = 103.128 s
目的:   制約下で target イベント数 E を **最大化**する
解:     E = 3（E=1, E=2 も制約は満たすので、E=3 は
        「唯一解」ではなく **最大整数解**である）
```

**「一意に決まる」は誤りだったので撤回する。** 目的関数（イベント数の最大化）を
書かずに解だけ書くと、E を減らす方向の改訂が制約違反に見えてしまう。

**User 再裁定（2026-08-20）**: 順位は **(i) > (ii) > (iii)** で不変。
**(i) E = 3 を採用**するが、**無条件確定ではなく `E=3 conditional approval`**
とし、§8-5-2c の承認条件を全て満たした時点で確定する。

| 選択肢 | 裁定 |
|---|---|
| **(i) E = 3** | **採用（条件付き）** — dosage 固定と 9 セル均等の両方を保てる |
| (ii) target 比を 60% 超へ | **却下** — baseline 被覆をさらに削り、完全置換 (a) へ近づく |
| (iii) dosage 固定を解除 | **却下** — 量と内容を再び交絡させる |

**★ 上表は「成立を証明した」ものではない**（2026-08-20 訂正）。確認できたのは
**voiced 秒の 1 面だけ**で、実際の選抜は次の 2 つを**同時に** ±1% で満たす
6 row の部分集合が存在することを要求する:

```
voiced        = 171.88 s
total ph_dur  = 233.395 s
```

既存 user 15 row の**行別の尺は repo の pin に含まれていない**（`wav_sha256` は
あるが per-row duration は無い）ため、本書ではこの部分集合の存在を証明できない。
また **target 側の total ph_dur（= voiced + SP 分）も未算出**である。

したがって **feasibility は「未証明」として扱い、収録の前に必ず通す**:

```
収録前ゲート = dry-run manifest（Run 8-0 の最後に実行・fail-closed）:

  算出する量（再裁定 2）:
    target_voiced_seconds
    target_SP_seconds
    target_total_ph_dur_seconds
    retained_baseline_voiced_seconds
    retained_baseline_total_ph_dur_seconds
    final_row_count

  合格条件（3 つを**同時に**満たす）:
    final_row_count = 15
    voiced 総量        : run 7 比 ±1% 以内（171.88 s）
    total ph_dur 総量  : run 7 比 ±1% 以内（233.395 s）

  満たさない場合:
    -> E や target 比を本 memo の改訂で決め直す。**収録を発注しない**
```

**完全一致が不能な場合も、収録後に耳で調整しない**（§8-5-4 の決定論規則で選ぶ）。

**この順序を守れば「収録してから初めて破綻する」経路が閉じる。**
収録は User の実作業なので、空振りさせない責任は設計側にある。

**収録後の検算**: `capture_pool` から選抜した 9 target row の voiced 合計が
上限を超えていたら、**学習投入前に fail-closed**（収録し直しでなく、
§8-5-4 の選抜規則で落とす）。

#### 8-5-2c. **E=3 の承認条件**（全て満たした時点で確定・User 再裁定 2026-08-20）

```
[ ] 1. 2.50 / 3.33 / 5.00 s の内訳（terminal / fixed_context / total）を明示
[ ] 2. 32.5 s / E を score 生成器または dry-run manifest で検証
[ ] 3. target の total ph_dur（SP 込み）を計算
[ ] 4. 27 件すべてが /ri/→SP として明示されていることを機械確認（§8-5-2d）
[ ] 5. baseline 6 row を**収録前に**決定論選抜（§8-5-4b）
[ ] 6. voiced / total ph_dur / row_count を**同時に**閉じる
[ ] 7. run 8-R の bit 一致 Gate を実施（§9-0c）
```

**E=3 は「数字から自動的に確定」するものではない。** full-event 尺・SP 込み
ph_dur・baseline subset・sampler exposure まで閉じた時点で確定する。

**7 項目は全て「作業」であって、User の追加裁定を要しない**（裁定は済んでいる）。
担当と段階を明示して、どれが誰の手番かを曖昧にしない:

| # | 種別 | 担当・段階 | 現状 |
|---|---|---|---|
| 1 | 明示（設計） | 本 memo | **充足**（§8-5-2b の拍内訳表） |
| 2 | 検証 | PR-1（score 生成器 / dry-run manifest） | 未 — 算術は memo にあるが実物と照合していない |
| 3 | 計算 | PR-1（SP budget の確定が要る） | 未 |
| 4 | 機械確認 | 収録 → 変換時（`ri_to_SP_count == 27`） | 未 — 規則は §8-5-2d で凍結済み、検査は未実行 |
| 5 | 決定論選抜の**実行** | PR-1（既存 15 row の行別尺が要る = repo pin に無い） | 未 — 規則は §8-5-4b で凍結済み |
| 6 | 同時充足の検算 | PR-1（収録前ゲート = §8-5-2b） | 未 |
| 7 | bit 一致 Gate の**実施** | Run 8-0 完了後（GPU 走行 1 本 = run 8-R） | 未 |

**律速は 2/3/5/6 の 4 件で、いずれも「既存 user 15 row の (voiced, total ph_dur)
を実測して台帳化する」ところから始まる。** これは PR-1 の最初の作業であり、
本 memo の段階では実行できない（行別の尺が repo の pin に含まれていないため）。

#### 8-5-2d. **27 イベントを 27 遷移として記録する**（再裁定 3・重要）

**row 内の 3 イベント間に明示的な SP が無ければ、`/ri/`→SP として学習されるのは
row 末尾の 1 件だけになる。** その場合の実効遷移数は **27 ではなく 9** に落ちる。

これは §4-0 の実装事実（レンダラはフレーズ概念を持たず、SP は先頭 1 個と
末尾パッドのみ）の**学習データ側の対応物**である。モデルが終端解放を学べるのは
**音素列に `→SP` が実在する場合に限る**（§8-5-2 と同じ理由）。

```
target row の ph_seq は必ずこの形にする:

  ... r i SP ... r i SP ... r i SP
      ^^^^^^^^     ^^^^^^^^     ^^^^^^^^
      event 1      event 2      event 3

検査（fail-closed）:
  1. ph_seq と ph_dur の**双方**で SP の実在と長さを確認する
     （ph_seq にあっても ph_dur が 0 なら遷移として成立しない）
  2. manifest へ ri_to_SP_count = 27 を**機械記帳**する
  3. 27 に満たない場合は学習投入前に停止する
```

**収録側の要件**: 各カードで反復間に**明示的な休止**を入れて歌う
（連続して歌い切ると SP が立たない）。カード指示書にこれを明記する。

#### 8-5-3. 「9 セル均等」の定義

**均等にするのは秒数ではなく `/ri/`→SP のイベント数**（第 4 次裁定 4）。

1 拍・2 拍・4 拍を**同秒数**にすると、短いセルだけイベント数が増えて
尺因子とイベント密度が交絡する。よって **1 セルあたりのイベント数を揃える**:
各セル **E = 3** イベント（4 語から決定論規則で 3 語 × 1 回）× 9 セル
= **27 イベント**（§8-5-2b の上限から導出）。

**均等の意味を限定する（再裁定 6）**: これは **event-balanced であって
frame-balanced ではない**。1 拍セル（3×2.50 = 7.5 s）と 4 拍セル
（3×5.00 = 15.0 s）ではフレーム量が 2 倍違う。**セル間で秒数が違うのは
設計どおり**だが、「均等」という語を無限定に使うと frame 均等と誤読される。

**loader の実効 exposure も併記する**: 学習側が **row 均等サンプリング /
frame 比例 / random crop** のどれを使うかで実効 dosage は変わる。
run 8 manifest に次を必ず書く:

```yaml
balance_kind:              event-balanced   # not frame-balanced
target_row_share:          0.600            # 9 / 15
target_voiced_share:       0.567            # 97.5 / 171.88
loader_sampling_mode:      <実測して記入>    # row_uniform | frame_proportional | random_crop
expected_target_exposure:  <上記から導出>
```

**`loader_sampling_mode` は推測で書かない** — 実装を読んで確定し、
読めなければ `unknown` として exposure の主張をしない。

#### 8-5-4b. baseline 6 row の選抜は**全探索**で収録前に確定する（再裁定 5）

既存 15 row から 6 row を選ぶ組合せは **C(15,6) = 5005** しかないので、
**全探索できる**。ヒューリスティックや事後調整を入れる理由がない。

```
選抜順（上から順に適用し、決定論的に 1 つへ絞る）:

  1. 必須被覆条件を満たす部分集合に限定する
       T0 / T1 / T2 の各カード種を含む
       母音の被覆
       語中 / 語尾 の両方
       一般語彙（target 語彙に寄せない）
  2. voiced 総量の誤差を最小化
  3. total ph_dur の誤差を最小化
  4. 同率なら card_id 昇順 -> さらに同率なら source SHA 昇順
```

**6 row では必要被覆を維持できない場合、収録後に無理に選ばない。**
`9 target + 6 baseline` という row 構成**自体を再設計する**
（本 memo の改訂として扱う）。被覆を削って数合わせをすると、
retained baseline が「一般語彙を保つ」という役割を失い、
§8-5-1 の (a) 完全置換への退化と実質同じことになる。

#### 8-5-4. 選抜規則（決定論・耳を入れない）

使用可否は**機械判定できる 5 項目だけ**で決める:

```
clipping / 歌詞一致 / alignment 成功 / pitch 実測 / duration 実測
```

- **耳の良し悪しでは選ばない**（選定に耳を入れると標的素材の来歴が壊れる）
- 同順位は **source SHA 昇順**などで固定する
- **row 途中・音素途中の切断は禁止**

**完全一致は原則として不可能である**（row が原子単位なので、任意の秒数に
ぴったり合わせられない）。よって次を**事前登録し、差分を必ず記帳する** —
これは例外処理ではなく**通常系**として扱う:

```
voiced / total ph_dur = run 7 比 ±1% 以内
row_count             = 15 固定
```

#### 8-5-5. held-out の分離（語彙一般化の証拠を守る）

| 区分 | 定義 | 何の証拠になるか |
|---|---|---|
| **primary held-out** | 学習と**別の語句**・**別 take**・同じ `/ri/`→SP 条件 = §8-1 の **held-out target cards**（`みのり` / `たより` / `みどり` / `のぼり`。**訓練に一度も入らない**） | **語彙を越えた一般化** |
| **secondary held-out** | **同じ語句・同じセル**の別 take | 同一語句内での再現 |

**同一文句の余剰 take だけでは、語彙を越えた一般化の証拠にしない。**
primary が空なら、その主張は `undetermined` で記帳する。

`/su/`→SP・`/i/`→SP・語中 `/ri/`・`/N/`→SP は §8-4 のとおり
**必須 held-out 対照として維持**する。

#### 8-5-6. 結果の主張範囲（事前確定）

| 実行された形 | 主張できる範囲 |
|---|---|
| **dosage 固定 + 部分置換**（既定） | 「**user 総 dosage を固定したまま、target 終端経験へ内容を置換した効果**」 |
| target 素材だけで全置換（(a) へ退化） | **一般語彙喪失との交絡が残る** — 改善を標的効果に帰属できない |
| 171.88 s を**超えて追加**（(c)） | 「**target 内容 または 量増加が効いた**」までに限定 |

#### 8-5-7. 会計の記帳

`user_ri_pack_selection.json` に「録音した全カード（capture_pool）」
「学習へ投入したカード/秒数（train_user_dose）」「held-out へ回したカード
（primary / secondary の別を含む）」を **3 分割で記帳**する。投入と余剰の
境界が後から動かせると dosage 固定が形骸化するため、**選抜結果は pin して
8-B 実行前に凍結する**。

## 9. 結果による振り分け

| run 8-B 結果 | 結論 |
|---|---|
| user と ritsu の**両方**が改善 | 標的実歌唱が共有デコーダ経由で転移。**H-local と H-shared を支持** |
| **user だけ**改善・ritsu 不変 | 話者ローカル実歌唱は効くが**話者間転移しない**。User 録音追加だけでは ritsu は直らない |
| **ritsu のみ**改善 | 共有音響技能への作用が中心。User 話者固有効果より**教師的効果**が強い |
| **全話者不変** | **`undetermined`**（★ 2026-08-20 訂正: 初版は「実歌唱分数仮説を**棄却**」としていたが誤り = §9-0b）|
| `/ri/` だけ改善・`/su/` 不変 | **標的音素対策**として成功 |
| `/ri/` と `/su/` が改善 | **一般的なフレーズ末解放技能**として成功 |
| PJS・うみ・語中対照が**悪化** | 回帰として **run 8 を不採用** |

### 9-0. **この表は既定では因果裁定ではない**（2026-08-20 追加・P1 対応）

run 8-B と run 7 の比較は**別 checkpoint 間の比較**であり、
**§2-3 の実測（入力バイト不変の ritsu が run5→run6 で区間ラウドネス最大
7.94 dB 動いた）が示すとおり、走行間ドリフトが介入効果と同程度以上ありうる**。
probe を各 checkpoint 内で再レンダしてもこれは消えない（消えるのは
レンダ側の変動だけで、学習/モデル側のドリフトは残る）。

さらに **8-B で入力が変わるのは user だけ**である。ritsu / pjs の入力は不変
なので、**その変化は「共有デコーダ経由の転移」と「走行間ドリフト」の和**で
あり、単走行では分離できない。

したがって:

| 条件 | 表の読み方 |
|---|---|
| **未処置の同一契約反復（run 8-R）が無い**（既定） | 表の各行は**仮説対応表**であって因果裁定ではない。結果は `confounded / provisional` で記帳し、**因果の断定語（「転移した」「効いた」）を使わない** |
| run 8-R がある | ドリフト幅が実測されるので、それを超えた差についてのみ表の行を**因果裁定として引ける** |

**run 8-R = run 7 の完全同一設定での反復走行**（≈$1.4・未処置の対照）。

**初版は「user だけが動く行は run 8-R なしでも限定付きで読める」と書いたが
撤回する**（2026-08-20・Codex P1 指摘）。理由: **「user が唯一の被処置話者で
ある」ことは「user に起きた変化が処置由来である」ことを意味しない**。
学習の確率的ドリフトは**特定の話者だけに出ることもある** — 現に §2-3 の実測は
「入力不変の ritsu が大きく動き、pjs はほとんど動かなかった」という
**話者ごとに不揃いなドリフト**を示している。したがって user 単独の改善も、
標的パックなしで起こりえた。同じ理由で「全話者不変」も、ドリフトが
実効果を覆い隠した可能性（偽陰性）を排除できない。

**結論: 4 行すべてが run 8-R を要求する。**

| 未処置対照の状態 | 表の扱い |
|---|---|
| **run 8-R が run 7 と bit 一致**（= 学習が決定論） | ドリフト 0 が実証されるので、**追加費用ゼロで因果裁定として引ける**（最良ケース） |
| **bit 一致せず・未処置反復 k = 1**（8-R のみ） | 平均シフト `b_s` は補正できるが、その**ばらつき `sigma_between` が 1 点からは推定できない**。よって `provisional` 止まり |
| **bit 一致せず・k >= 2**（8-R + 8-R2） | `sigma_between` が推定でき、§7-1 の bound を超えた差についてのみ**因果裁定として引ける** |
| **run 8-R なし** | **全行が `confounded / provisional`**。因果の断定語を使わず、§11 の「効果で終端宣言」も**充足しない**（= run 8 は終端しない） |

**まず 8-R を 1 本回して bit 一致を検査する**（§7-1 段階 0）。一致すれば
そこで打ち止め。一致しなかった場合にのみ 8-R2 の要否を判断する
（この順序が最短かつ最安）。

**費用**: 8-B ≈$1.40 + 8-R ≈$1.40 = **約 $2.80**。bit 一致しなかった場合に
8-R2 を足すと **約 $4.20**（各走行は cap $4 内）。
run 8-R は 8-B と**同一契約・同一素材**（= run 7 の設定そのまま）なので
新規の設計判断はゼロであり、実装コストも増えない。

これは s5_record §6.2 が「帰属の確定には同一契約の反復走行（未実施）が要る」と
自ら記録した**未払い分の取り立て**である。

### 9-0b. **「検出されなかった」を「効果が無い」に読み替えない**（2026-08-20・P1 対応）

初版は「全話者不変 → 実歌唱分数仮説を棄却」としていたが、**MDC95 は
「変化が検出されたか」しか決めない量であり、効果の不在（同等性）を
示さない**。特に §7-1 のとおり **k = 2 の検出力はほぼ無い**（worked example で
MDC95 ≈ 47 ms）ので、この読み替えは**小さな真の効果を偽の棄却に変える**。
棄却は run 9 の方向を speaker embed 側へ振り替える決定に直結するため、
偽陰性のコストが高い。

```
「不変」の扱い（事前登録）:

1. 同等性マージン Δ_eq を事前に置き、TOST（two one-sided tests）で
   |効果| < Δ_eq を積極的に示せた場合のみ
      -> refuted（＝実歌唱分数仮説を棄却してよい）
2. 示せない場合（Δ_eq 未設定・検出力不足・k=2 を含む）
      -> undetermined。**棄却と書かない**
3. Δ_eq は 8-0 完了後・8-B 開始前に §7-1 と同時に凍結する
   （事後に決めると「効果が無かった」を作れてしまう）
```

**Δ_eq を置けるだけの検出力が無いなら、この行の結論は最初から
`undetermined` である**と認めた上で走らせる。それが正直な設計である。

**この表が引けるのは §8-4 の held-out 規律が守られた場合だけ**である。
`/su/` を学習に入れてしまうと「`/su/` 不変」の行は原理的に引けない
（入れた対照が動かないことは対照の情報にならない）。
また**改善・悪化の判定は §7-1 で凍結した MDC95 / severity / κ 床による**。

**「user だけ改善して ritsu が変わらない」場合が特に重要**である。その結果は
「実歌唱が必要」という仮説を否定せず、**技能が speaker-local であること**を示す。
その場合の次レバーは追加録音量ではなく、VG-L0 側の speaker-independent な
Performance Skill / TRANSFER_SKILL、または**話者条件と技能条件を分離する構造**になる。

## 10. 必要コード変更（PR 経由・3 本に分割）

### PR-1（8-0: 台帳 + **B-1 校正ハーネス**）

1. `results_s7/target_exposure_ledger.json` 生成器（§3 のフィールド）+ 形状テスト
2. 集計は既存 `transcriptions.csv` と譜面境界から。**推定しない**
3. **B-1 calibration harness**（§12-0-B）— 校正音源だけを通して TRF 主観測 4 値の
   測定仕様を確定し、`TRF measurement spec / 1.0` として凍結する。
   **本番 360 セルは 1 セルも通さない**
4. **B-2 裁定代数の凍結**（§12-0-C）— B-1 凍結の直後・同じ PR 内で行う

### PR-2（8-0b/c/d: 計器と probe）

1. **`gate_synth` の song loader 最小拡張** — 現在 `song not in ("sakura","umi")`
   で弾く（`gate_synth.py:252`）。診断スコアモジュールを **sha256 pin したまま**
   読めるよう拡張する。これは **VG-TR0 実装第 1 タスク (a) と同一物**で
   二重投資にならない。既存 sakura/umi 経路は**返り値・sha ともバイト同一**を
   テストで縛る
2. **正規化前波形の測定経路**（§5-2）— `synth_song` の read-only 契約を壊さず、
   測定用に生波形窓を取り出せる差し込み口。既定 off で現行とバイト同一
3. **診断スコア生成器**（新規 `singer/score_diag_pf.py`）— §4 の 36 セルを
   決定論的に展開。`score.py` / `score_umi.py` は**無改変**で `ScoreNote` 型のみ
   import 流用（`score_d3_sustain.py` と同じ流儀）
4. **probe**（`evolution/probes/s7a_pf_probe.py`）— VG-L0 probe の
   `synth_once` / `collect_pins` / `execution_profile` / `verify_consumed_bytes` /
   `assert_writes_do_not_touch_inputs` を流用。**VG-L0 probe 自体は無改変**
5. **観測ベクトル実装**（§5）+ 結果 JSON（セルごとに因子水準・wav sha256・
   命令区間・全軸値・`status`・pins・`execution_profile`）+ 形状テスト

### PR-3（8-B: 収録と学習）

1. `recording_kit/cards.md` へ標的カード 16 枚を追加（§8-1: target 9 /
   held-out target 3 / control 4）
2. `convert_user` の標的パック対応（§8-2 のアラインメント）
3. `run8_dataset_pins.json` + assemble/bootstrap の run8 プロファイル

## 11. Acceptance Criteria

### Run 8-0

- [ ] 標的被覆台帳が 4 話者分そろい、`phrase_final_ri_count` 等が**機械集計**で埋まっている
- [ ] 台帳から 8-B の収録量が導出されている（「5〜8 分」の根拠が数字で出ている）

### Run 8-0b/c/d（probe・計器・耳校正）

- [ ] `load_song_module("sakura")` / `("umi")` の返り値と module sha が拡張前後で不変
- [ ] 診断スコア生成器が §4 の 36 セルを過不足なく展開（重複 0・欠落 0）+ 決定論テスト
- [ ] 結果 JSON が **360 セルを全て列挙**し、各セルが `rendered`（wav sha256・
      命令区間・全軸値あり）か `dropped`（事前登録された理由コードあり）で埋まっている。
      **達成条件は「360 セル全てがレンダされたこと」ではなく「360 セル全ての帰結が
      記帳されたこと」**。脱落時は**削減後の有効セル数と、どの H が `undetermined` に
      落ちるか**を s7 record に明記する
- [ ] **H0 の結果が記帳されている**（P-ANCHOR と同条件 probe セルの一致/不一致）。
      不一致なら他の H の裁定にその制約が明記されている
- [ ] ブラインド耳ラベルを取得し、**20% 重複提示の自己一致率が記録されている**
- [ ] **§7 の合格条件 6 項目の合否が個別に記帳されている**
- [ ] H0–H5 それぞれに `supported / refuted / undetermined` の裁定が付き、
      交絡が残る項目は **undetermined のまま**記帳される
- [ ] GPU 費用 $0

### Run 8-B（Gate 通過時のみ）

- [ ] §8-3 の「変えないもの」が実測で確認されている（pins 差分が標的パックのみ）
- [ ] **dosage 固定の機械検証**: `voiced` / `total ph_dur` が run 7 比 **±1% 以内**、
      `row_count` == **15**（§8-5-4）。到達しない場合は fail-closed で停止し、
      差分を記帳する（黙って枠外の dosage で学習しない）
- [ ] **構成の機械検証**: target 9 row + retained baseline 6 row（§8-5-2）。
      各 target row の `/ri/` 終端が `transcriptions` 上で **SP へ接続**されている
- [ ] **イベント数の均等性**: 9 セルの `/ri/`→SP イベント数が**セル間で同一**
      （秒数ではなくイベント数で均等 = §8-5-3）
- [ ] **選抜が機械 5 項目のみで決まっている**（§8-5-4）。耳を使った形跡がなく、
      同順位の tie-break が固定規則で再現する
- [ ] `user_ri_pack_selection.json` が capture_pool / train_user_dose /
      held-out（primary・secondary の別を含む）の **3 分割**で記帳され、
      **8-B 実行前に pin 凍結**されている（§8-5-7）
- [ ] **primary held-out（別語句・別 take）が非空**である。空の場合、
      「語彙を越えた一般化」の主張を `undetermined` で記帳する（§8-5-5）
- [ ] 学習 40K 完走（NaN ゼロ）または fail-closed 停止の証跡
- [ ] 観測子 + ブラインド A/B + 回帰対照（PJS・うみ・語中）の結果が出ている
- [ ] §9 の振り分け表のどの行に該当するかが裁定されている
- [ ] **run 8-R（未処置の同一契約反復）が実施され、まず run 7 との bit 一致が
      検査されている**（§7-1 段階 0）。一致しなかった場合は平均シフト `b_s` で
      補正し、`sigma_between` が推定できる k >= 2 を満たしているか、
      満たさないなら `provisional` である旨が記帳されている
- [ ] 8-R が無い、または k = 1 のまま因果を断定していないこと — §9 の全行を
      `confounded / provisional` で記帳した場合、**本 AC の「効果で終端宣言」は
      充足しない**（§9-0）
- [ ] **効果で終端宣言**されている（「投入した」で終わらせない）
- [ ] **主張範囲が §8-5-6 の表のどの行かが明記されている**（dosage 固定の
      部分置換 / (a) への退化 / (c) 量増加のいずれか）
- [ ] 費用 ≤ $4

## 12. Open Questions

### 12-0. **B-1 / B-2 の二段階凍結**（User 裁定 2026-08-20 で確定・境界宣言は解消）

Codex レビュー 12 巡で残した 2 件（B-1 = TRF 主観測 4 値の測定仕様 /
B-2 = H0–H5 の裁定代数）について、**「今すぐ全部を数値凍結」も「PR-2 直前まで
何も決めない」も却下**され、**二段階凍結**が裁定された。

```
今（本 memo）  : 凍結プロセスそのものを凍結する（meta-contract freeze）
PR-1           : calibration harness -> B-1 実測校正 -> B-1 freeze -> B-2 freeze
PR-2           : 本番 360 セルの production probe
```

**なぜ二段階か**。B-1 は**測定器そのもの**で、

```
音声 -> B-1 -> TRF 4 値 -> z 値 -> primary axis -> AUC / Gate
```

と一直線に効く。よって **360 セルを作ってから B-1 を調整するのは不可**
（結果を見ながら物差しを変えることになる）。一方、実音声を一度も通さずに
窓長・F0 閾値・mel normalization を紙だけで固定するのも危険で、VG-L0 で実際に
起きた「**測定器の意味を誤認して結果を後から撤回**」を再現しうる。

#### 12-0-A. **今すぐ凍結する（数値ではなく「どう決めるか」）**

**(A-1) calibration set を先に固定する** — **本番 360 セルとは完全分離**。
少数の既知条件だけで構成する:

```
clean terminal /ri/
clean /i/
clean /N/
forced long-tail            （終端を意図的に伸ばした条件）
forced duration perturbation（配分を意図的にずらした条件）
silence                     （無音）
gain 違い                    （同一素材の振幅だけ変えた対）
```

**run5/6/7 の「どれが良い / 悪い」を B-1 の選定に使わない。**
本番仮説の答えを見て測定器を選ぶ経路を構造的に断つ。

**(A-2) B-1 の候補空間を事前登録する** — 校正後に**新しい候補を足さない**:

```
analysis window : 100 / 200 / 300 ms
hop             : 5 / 10 ms
voicing         : algorithm A / B
mel             : FFT / hop / mel bins を固定値で列挙
```

**(A-3) B-1 の選択基準を固定する — `AUC 最大化は禁止`**:

```
再現性 / gain 不変性 / silence で 0 付近 /
controlled perturbation への単調応答 / process 間再現性 / 数値安定性
```

**「病気を一番よく分離する測定法」ではなく「物理的に一番信用できる測定法」を選ぶ。**
分離性能で測定器を選ぶと、測定器が仮説を先取りしてしまう。

#### 12-0-B. **PR-1 で行う — B-1 calibration harness と `TRF measurement spec / 1.0`**

校正音源だけを通して、主観測 4 値
（`excess_tail_voiced_ms` / `release_after_score_boundary_ms` /
`tail_f0_persistence` / `terminal_mel_persistence`）について次を確定する:

```
解析窓 / boundary alignment / voicing 判定 / F0 欠損処理 /
mel 表現 / normalization / aggregation
```

確定したら **`TRF measurement spec / 1.0`** として凍結する。凍結物には
**式 + 単位 + worked example + reference output** を持たせる
（§7-0 / §3 と同じ様式）。

#### 12-0-C. **B-1 凍結の直後に B-2 を閉じる**

B-2 が B-1 より後なのは正しい順序である。ただし **B-2 の許容差を決めるために
本番ラベル（「ritsu は悪い」「pjs は良い」）を見てはならない**。ε は
**測定側の性質だけ**から決める:

```
ε_axis = max( numerical_floor, reproducibility_bound )

  numerical_floor       : 数値量子化誤差
  reproducibility_bound : 同一条件反復 + 独立 process 反復のばらつき
```

**「群を一番綺麗に分ける ε」にはしない。**

H0–H5 それぞれに次を機械的に定義し、**必ず三値へ機械変換できる形**にする:

```
participating axes / direction / tolerance /
minimum supporting cells / contradiction rule / final aggregation
```

書き方の例（H-dur）:

```
supported     : duration intervention で primary TRF 軸が ε を超えて改善
                かつ 少なくとも 2/3 の pitch セルで同方向
refuted       : 2/3 以上のセルで ε を超える逆方向
その他        : undetermined
```

#### 12-0-D. **PR-2 の開始 Gate（全て揃うまで着手禁止）**

```
[ ] B-1 calibration set 固定
[ ] B-1 候補空間 固定
[ ] B-1 選択規則 固定
[ ] B-1 measurement spec 1.0 凍結
[ ] B-1 worked example 一致
[ ] B-2 H0–H5 代数 凍結
[ ] B-2 tolerance の由来記録（ε がどの反復・どの量子化から出たか）
[ ] B-2 worked example
[ ] **本番 360 セルを一度も見ていない**
```

最後の 1 行が本裁定の要である。これにより **VG-L0 型「測定器の欠陥で偽結論」**と
**「本番結果を見てから物差しを調整する」**の**両方**を避けられる。


1. **学習 seed の pin 状況**: §8-3 は seed / `ExecutionProfile` を「変えない」と
   宣言するが、**repo 側の実行契約（`run5_bootstrap.py` / runbook / DESIGN 各書）に
   学習 seed の pin は見当たらない**。8-B 実行前に (a) 実際に固定されているか、
   (b) 固定できるか、を確認して明示する必要がある。固定できない場合、
   「変えない」は**宣言できない**ので会計をそう書き換える
2. ~~**同一契約の反復走行**~~ — **本書で解決済み（2026-08-20）**。
   s5_record §6.2 の未払い分は **run 8-R として §9-0 で必須化**した
   （当初は「争点になった場合の手段」「user 単独行は免除」としていたが、
   ドリフトが話者ごとに不揃いである実測〔§2-3〕より免除は成立しないと判明し
   撤回）。**§5-1 の話者内差分は観測子の交絡を落とすが、checkpoint 間の
   ドリフトは落とせない** — 回避と解決は別である、という初版の但し書きが
   そのまま効いた形
3. ~~**s6_record §5-1 の文言**~~ — **解決済み（2026-08-20）**。User 裁定を受けて
   s6_record §5-1 を訂正し（「代替として機能する」を撤回）、§5-1b で
   「さ→あ 未裁定」と AC の部分充足を記帳した。さらに Codex 指摘を受けて
   **§5-1 の見出しから「articulation 側に限り達成」も撤回**し、事前登録の
   articulation 測度（さ行 onset）が未判定である以上 `undetermined` へ
   格下げした。正典 3 面（s6_record §5-1 / §4-2 表 / STATUS.md）を同期済み
