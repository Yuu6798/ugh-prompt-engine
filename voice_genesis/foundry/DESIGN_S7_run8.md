# DESIGN S7 — run 8（り→ん破綻の専用調査: **観測子先行の 3 段**）

- 起草: 2026-08-20（Claude 設計）。**User 裁定 2026-08-20（第 3 次）まで反映**。
  本書は初版（診断単発・追加収録なし）を**上書き**し、第 3 次裁定で
  観測子の定義・量交絡の除去法・耳判定集合・回帰閾値を確定した
- **改訂 vNext（2026-08-21・User 改修設計を反映）**: S3（Genome Architecture）の
  4 gene 知見を受け、**Gate 通過後・8-B 着手前**に `Run 8-0G`（Genome-informed
  Native Intervention Gate）/ `Run 8-0D`（教育可能性の判定）/ `Run 8-0E`
  （収録・dosage の最終確定）を新設した（**§7G**）。**run 8 の目的は変えていない** —
  `terminal_release_failure` 観測子・360 セル・校正→hold-out・
  `dosage-fixed targeted partial replacement`・8-R 再現性裁定はいずれも維持し、
  Genome 知見を**「どこを教育するか」の選択器**として 8-B の手前へ挿入する
- 位置づけ: S 系列の第 7 設計書。**り→ん破綻は S3 以来の最古の未解決課題**で、
  `results_s6/s6_record_2026-08-20.md` §6-4 が「単一介入 run の副題として
  扱うより専用の調査に値する」と申し送った件の実施回
- 前提記録: [`results_s6/s6_record_2026-08-20.md`](results_s6/s6_record_2026-08-20.md)
  （run 7 closeout・り→ん破綻は run5→7 一貫の継続課題と確定）/
  [`DESIGN_S3_backfill.md`](DESIGN_S3_backfill.md)（原因仮説の初出）/
  [`../evolution/records/vgl0_control_axis_probe_2026-08-20.md`](../evolution/records/vgl0_control_axis_probe_2026-08-20.md)
  （CPU レンダ probe の実証済み経路）/
  [`recording_kit/cards.md`](recording_kit/cards.md)（収録カード規約）/
  [`genome_s3/results/S3_RECORD.md`](genome_s3/results/S3_RECORD.md)
  （S3 = PASS・F0 / Duration / Energy / Release の 4 gene。**vNext の入力**）

## 0. 裁定（本書で凍結する設計判断）

1. **run 8 は観測子先行の 3 段構成**（vNext で 2 段 → 3 段）。観測だけで 1 run を
   消費せず、同一計画内で **2 つの Gate** を挟んで学習まで到達する:

   ```
   Run 8-0（GPU $0・**run 番号を消費しない**）
     A. 終端遷移台帳（SP/遷移密度の機械集計）                     = 旧 8-0a
     B. 固定 Probe Set 360 セル（**1 target event = 1 render**）  = 旧 8-0b/c/d
        + 観測子 terminal_release_failure/0.1 の全セル機械評価
        + 層化ブラインド耳校正（40 unique + 8 duplicate）
      ↓ **Gate 1**（§7 合格条件 = TRF 観測子の成立）
     G. **Genome-informed Native Intervention Gate**（§7G・vNext 新設）
        B0 / D（duration）/ F（f0）/ S（SP）/ R-rescue を
        **一度に 1 つだけ**介入し、TRF を実際に動かす native レバーを選定
      ↓ **Gate 2**（§7G-5 の機械判定 + §7G-7 の 2 問人間確認）
     D. **教育可能性の判定** — 選ばれたレバーが 8-B で教育できるか（§7G-8）
     E. **target recording / dosage dry-run の最終確定**（§7G-9）
      ↓
   Run 8-B  User 実歌唱の標的構成だけを単一介入として 40K 学習
      ↓
   Run 8-R  未処置の同一契約反復（§9-0）
      ↓
   観測子 + ブラインド A/B + 回帰対照 → **効果で終端宣言**
   ```

   **第 3 次裁定で「Run 8-A」の呼称は Run 8-0 へ統合された**（観測段は
   一つの GPU $0 ブロックとして扱う）。本書中の 8-0a/8-0b… は上記の細目を指す。
   **vNext の 8-0A/8-0B/8-0G/8-0D/8-0E は同じ GPU $0 ブロック内の段階名**で、
   旧 8-0a → 8-0A、旧 8-0b/c/d → 8-0B に対応する。**旧細目 e（duration / SP /
   spk_embed の 3 レバー診断）は §7-2 に残り**、Gate 1 が**不成立のとき**の
   $0 成果物として機能する。Gate 1 が成立した場合は上位互換の §7G が走り、
   **spk_embed は原因候補から Identity control へ降格**する（§7G-1）

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

10. **S3 の 4 gene を run 8 へそのまま持ち込まない**（vNext）。S3 が成立させた
    のは **WORLD 領域（sp / ap / f0）の凍結 pair 上の移植**であって、run 8 の
    実推論経路（Stage 1 dur → Stage 2 pitch → Stage 3 acoustic → Stage 4
    vocoder）に同じ入力があるとは限らない。**native 入力の有無**で
    Primary causal candidate / Diagnostic only / Identity control の 3 つへ
    振り分ける（§7G-1 の対応表が正）

11. **Energy は既定の実験系から外す**（診断のみ）。mel や waveform の gain を
    直接動かすと、TRF の energy 系観測値（`energy_decay_slope`・絶対 RMS 閾値で
    有声判定する `excess_tail_voiced_ms` / `hnr_*`）が**破綻の有無と無関係に**
    動く。「計器を操作して計器が改善した」という循環になるため。同じ理由で
    **R-rescue の機械 TRF 値には `instrument_coupled` を立てる**（§7G-4）

12. **Release は原因 probe ではなく rescue probe である**。S3 の Release は
    完全な release genome ではなく主として terminal taper なので、効いても
    言えるのは「**終端出力を適切に減衰させれば症状を救済可能**」=
    **phenotypic rescue** までである。**原因の同定には使わない**

13. **8-0G は新しい TRF metric を作らない**。primary axis と `θ` は B-1 / B-2 で
    凍結したものをそのまま使う。**介入セルの z' は B0 アームの (話者 × 世代)
    統計で凍結して当てる** — アームごとに z 化し直すと、アーム全体が一様に
    動いた介入効果が正規化で消える（§7-0 (7) が Gate 3 で踏み抜いたのと同型の穴）

14. **人間確認は 2 問だけ**（§7G-7）。360 セルを聞き直さない。機械 Gate で
    primary lever を 1 つに絞った後にだけ blind A/B を 2 問行い、2/2 一致で
    `HUMAN_CONFIRMED`、不一致なら `machine_effect_only` = **8-B BLOCKED**。
    **これは統計的証明ではない**（帰無仮説下で 2/2 が偶然出る確率は 0.25）。
    GPU 学習へ進む最低限の人間 Gate としてのみ使い、§9 の因果裁定にも §11 の
    終端宣言にも用いない

7. 予算: **cap $4 は「1 走行あたり」であって実験全体ではない**
   （2026-08-20 訂正 — 従来の書き方だと、非決定論だった場合の完了経路が
   自分の予算 AC に必ず違反していた）。

   | 経路 | 走行 | 合計 | 位置づけ |
   |---|---|---|---|
   | **Run 8-0** | なし（CPU のみ） | **$0** | 観測段（vNext の 8-0A / 8-0B / **8-0G / 8-0D / 8-0E** を全て含む。8-0G の追加レンダは CPU 300 本前後 = §7G-0） |
   | **決定論だった場合** | 8-B + 8-R | **≈$2.80** | **因果裁定まで到達**（既定の完了経路） |
   | 非決定論だった場合（既定） | 8-B + 8-R | ≈$2.80 | **探索的 / `provisional`** として記帳（再裁定 8）。**走行としては正当な停止点だが、AC の「効果で終端宣言」は充足しない**（= run 8 は終端しない・§9-0 / §11 と同一の扱い） |
   | 非決定論 + 形式的な因果裁定を要求する場合 | + 8-R2 | ≈$4.20 | **実験全体予算の User 承認が要る** |

   **各走行は cap $4 以内**（run 7 実績 ≈$1.40）。**実験全体で $3 を超える
   見込みになった時点で User 承認を取る**。User の負担は
   **ブラインド耳ラベル 2 回**（§6-1 の Gate 前 40+8 セル / §6-2 の処置後
   27+6 セル）+ **8-0G の 2 問**（§7G-7・vNext で追加）と
   **8-B の候補プール収録 5〜8 分**
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
| **H-f0**（vNext） | pitch conditioning が有効な因果レバーである | note-relative contour を振っても TRF 軸が動かない |
| **H-SP**（vNext） | 終端遷移 cue（語末 `/ri/`→SP の明示）が有効な因果レバーである | 明示 SP を入れても TRF 軸が動かない／`S-frames-only` と差が出ない |
| **H-rescue**（vNext） | 終端出力の taper で症状を**救済**できる | taper を掛けても耳判定が改善しない |

**H-TTD の集計単位（第 3 次裁定）**: 初版が想定した「総 SP 数」では測れない。
ritsu VCV には**孤立録音の頭尾 SP が大量に入り得る**が、それは
「**長い実歌唱 /ri/→SP**」と同等ではない。よって §3 の台帳は
`modality` × `preceding_phoneme` × `preceding_duration_bin` ×
`utterance_final / internal` × 遷移種別 × `pitch_bin` の
**関連終端イベント密度**で比較する。

**H-f0 / H-SP / H-rescue は vNext で新設**され、それぞれ §7G-2 の F / S /
R-rescue アームに対応する。3 本とも到達限界は H-dur と同型で、**言えるのは
「有効な因果レバーである」まで**である。加えて:

- **H-SP は stage 分離されていない** — 語末に SP トークンを足すと Stage 1 の
  入力自体が変わるので、効果は「終端遷移 cue」と「終端母音が短くなったこと」の
  和になる。`S-frames-only` 対照に勝てない場合は `duration_confounded` として
  **D 側の証拠に計上する**（§7G-3）
- **H-rescue は原因仮説ではない** — 出力後処理なので `instrument_coupled` を
  立て、phenotypic rescue までしか言わない（§0-12 / §7G-4）

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

**譜面の無いコーパスの `pitch_bin` を先に決める**（2026-08-20 追加）。
ritsu VCV は `convert_ritsu.py` が `name` / `ph_seq` / `ph_dur` しか出さず、
**音高を復元できる譜面が無い**。F0 抽出器も bin 境界も未定義のままだと、
ritsu を 6 層のどこへ入れるかが実装ごとに変わり、H-TTD の母集団が変わる:

```
pitch_bin の決め方（**量を MIDI へ揃え、規則も話者内三分位で揃える**）:
  # ★ 2026-08-20 訂正: 譜面ありを「MIDI から直接割り当てる」とだけ書いていたが、
  #   境界も話者相対かどうかも未定義だった。pjs は note_seq を出すので、
  #   **唯一許される同 modality 比較（pjs vs user）で、pjs は未定義の規則・
  #   user は f0 三分位**という非対称になり、同じ事象が別の層へ入って
  #   H-TTD の裁定が反転しうる。両者を同じ量・同じ規則へ揃える。
  1. 量を MIDI 数へ統一する
       譜面あり : note_seq の MIDI をそのまま使う
       譜面なし : 有声区間の f0 中央値 -> MIDI = 69 + 12*log2(f0/440)
  2. 境界は**話者内の三分位**で切る（絶対値で切らない）
       low  : 話者内 33.3 パーセンタイル未満
       mid  : 33.3 以上 66.7 パーセンタイル未満
       high : 66.7 パーセンタイル以上
  3. 実際に使った**カット点（MIDI 値）を話者ごとに台帳へ記帳する**
       （後から別実装が同じ層割りを再現できるようにする）
             測定器 = ANALYSIS_STACK_PIN の librosa（convert_user T2 と同じ
             経路。新しい抽出器を持ち込まない）
             有声フレームが閾値未満 / f0 が取れない行:
               pitch_bin = unknown  -> **その行は H-TTD の層から除外**し、
               除外件数を台帳に記帳する（黙って mid へ寄せない）
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
  ※ modality は話者ごとに一意に定まるため層キーから外すが、
    **外したことで modality は話者と完全交絡する**（下記の扱いに従う）

per-stratum verdict:
  各層で下の density 判定（ゼロ分岐込み）を独立に適用し
  {supported, refuted, undetermined} を出す

modality 交絡の扱い（2026-08-20 追加・層キーから外したことの代償）:
  宣言済みの内訳は ritsu=VCV / pjs=real_song / user=real_song /
  amitaro=speech であり、**コーパス構築と終端の切り方の違いだけで
  要求どおりの density 順序が出うる**（終端イベント密度が破綻と無関係でも）。
  よって:
    同一 modality 内の比較（pjs vs user = real_song 同士）
      -> per-stratum verdict を通常どおり出す
    modality をまたぐ比較（ritsu vs pjs 等）
      -> **その層は undetermined 固定**。supported の根拠に使わない
    modality をまたぐ層しか無い場合
      -> overall も undetermined

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

**層は `d3` と `d4` を分けて数える**（2026-08-20 訂正 — 初版の例は `d3+` と
集約表記になっていたが、実契約は分母を**厳密な `preceding_duration_bin`** で
索引し、投票も d3 / d4 を別々に行う。例に従って露出を合算した実装は
違う density・違う総合裁定を出す）:

| 話者 | modality | pitch | bin | `ri_to_SP` count | `eligible_terminal_count` | density | 層別 verdict |
|---|---|---|---|---|---|---|---|
| pjs | real_song | mid | **d3** | 7 | 90 | **0.0778** | — |
| user | real_song | mid | **d3** | 1 | 25 | **0.0400** | — |
| ritsu | VCV | mid | **d3** | 0 | 500 | **0.0000** | — |
| pjs | real_song | mid | **d4** | 5 | 60 | **0.0833** | — |
| user | real_song | mid | **d4** | 0 | 15 | **0.0000** | — |
| ritsu | VCV | mid | **d4** | 0 | 400 | **0.0000** | — |

層別裁定（ゼロ分岐と modality 規則を適用する）:

```
mid / d3:
  ritsu は VCV = modality またぎ -> 比較から除外（undetermined 側）
  pjs(0.0778) vs user(0.0400) は同 modality（real_song）で比較可
  min = 0.0400 > 0 なので分岐 4 -> 順序は「破綻しない pjs > 破綻する user」で成立
  比 = 0.0778 / 0.0400 = 1.94 < 2.0  ->  **refuted**

mid / d4:
  user の eligible_terminal_count = 15 < 20  ->  **undetermined**（標本不足）

overall:
  scored（undetermined でない層）= {mid/d3} の 1 層のみ
  len(scored) >= 2 を満たさない  ->  **overall = undetermined**
```

**この例が示すこと**: `d3+` と合算していれば
`pjs 12/150 = 0.0800` vs `user 1/40 = 0.0250`、比 3.2 で **supported** に見える。
層を分けて契約どおり数えると **undetermined** になる。**同じ台帳から逆の結論**が
出るので、例と契約の表記を揃えることは体裁の問題ではない。

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

### 6-2. **処置後ブラインド比較セット**（`s7_post_listening_set.json`・2026-08-20 追加）

§6-1 の listening set は **Gate の前に pin されて run5/6/7 のクリップだけ**を
含む。しかし §7-1 の human 基準と §9 の改善/回帰の裁定は**処置後の
`severity_shift`** を使う — **判定対象のクリップが §6-1 に 1 つも無い**。
この穴を放置すると、8-B の耳判定だけが事後に組まれ、§9 の結論が
再現不能な ad hoc 判定になる。よって**もう 1 つの集合を、学習を始める前に
pin する**。

```
対象世代: {run7} ∪ {全ての未処置反復} ∪ {run8-B}
          # 未処置反復 = run8-R, run8-R2, ...（k 本すべて）
          # ★ 2026-08-20 訂正: 初版は 8-R しか含めず 8-R2 を落としていた。
          #   k>=2 の因果経路では機械側が k 本の平均でドリフト補正するのに、
          #   耳側は 8-R 1 本しか見ないことになり、**機械と人手で対照母集団が
          #   食い違う**。P3（語彙一般化）に至っては {run7, run8-B} だけで、
          #   一様な走行水準シフトが「held-out の一般化」として報告されうる
cell_id の語彙は §6-1 と同一（<generation>/<speaker>/<probe>/<beats>/<pitch>）

P1 主効果（user・§6-1 A1 と同条件で三つ組にする）:
    {run7, 全未処置反復, run8-B} × user × {
        P-RI-FINAL/b4/p57, P-RI-FINAL/b2/p57, P-RI-FINAL/b4/p62,
        P-I-FINAL/b4/p57,  P-N-FINAL/b4/p57 }      -> k=1 で 15 / k=2 で 20
P2 回帰対照（未処置話者・§9 の「悪化」判定の材料）:
    {run7, 全未処置反復, run8-B} × {ritsu, pjs} × {P-RI-FINAL/b4/p57,
                                                   P-N-FINAL/b4/p57}
                                                    -> k=1 で 12 / k=2 で 16
    # ★ run8-R を必ず含める（2026-08-20・P1 訂正）。初版は {run7, run8-B} だけで、
    #   **未処置話者の知覚上のドリフトが一度もラベルされない**構成だった。
    #   §9 は bit 一致しなくても k>=2 なら package-level の因果裁定を許すので、
    #   その経路では ritsu/pjs の severity_shift が「走行が変わっただけの差」を
    #   置換パッケージの効果として拾ってしまう。
    #   8-R が実施されない場合は、この block の人手による改善/回帰の裁定を
    #   **provisional 固定**とする（§9-0 と同じ扱い）
P3 語彙一般化（**訓練に一度も入らない語**の /ri/ 終端 = §8-5-5 primary）:
    {run7, 全未処置反復, run8-B} × user × {P-RI-FINAL-HELDOUT/b4/p57,
                                           P-RI-FINAL-HELDOUT/b4/p62}
                                                    -> k=1 で 6 / k=2 で 8
                     ※ 語は `みのり` / `たより`（§8-1 の held-out 語彙から
                       文字コード昇順で 2 語）
-> unique = k=1 で 33 / k=2 で 44 + 重複提示は unique の 20%
   （P1/P2/P3 から cell_id 昇順で均等に採る）
   ※ セル数は k に依存するので**固定値で書かない**。
     実際の値は pin する `s7_post_listening_set.json` に記帳する
```

**規約は §6-1 と同一**: ランダム順・**世代とモデル名を伏せる**（`expected_terminal`
と `position` は伏せない）・重複であることを知らせない・2 値化と borderline の
扱いは §7-0 (1) の 2 キー場合分けに従う。

**pin のタイミングが要**: この集合は **8-B の学習を開始する前に**
`s7_post_listening_set.json` へ書き出して sha256 を pin する。処置後に
セルを選べると、`severity_shift` が「動いたセルを選んだ結果」になる。

**集約 — 耳側にも未処置ドリフトの補正を入れる**（2026-08-20 訂正）:

初版は素の `severity_shift` の median をそのまま使っていた。しかし
**run8-B と未処置反復が揃って 1 段動いた場合、対照が同じだけ動いていても
「パッケージによる改善/回帰」と報告してしまう**。機械側は §7-1 で
`b_bar_s`（k 本の平均シフト）を引いているのに、人手側だけ引いていなかった。

```
生の効果:     Δsev_B(cell)   = severity(run8-B) - severity(run7)
未処置の効果: Δsev_ctrl(cell) = median over 全未処置反復 r of
                                 ( severity(r) - severity(run7) )
補正後:       Δsev*(cell)     = Δsev_B(cell) - Δsev_ctrl(cell)

判定は Δsev* に対して §7-1 の基準（中央値で 1 段以上）を適用する
セル間の集約は Gate 3 と同じ median + タイは pass にしない（§7-0 (7)）
borderline は集約から除外し件数を記帳する

未処置反復が 0 本（8-R 未実施）の場合:
  Δsev_ctrl が定義できない -> **補正せず、人手の結論は provisional 固定**
  （§9-0 の「8-R 無し = 全行 confounded / provisional」と同じ扱い）
```

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

**(2b) 候補軸は「B-1 で凍結された軸」に限る**（2026-08-20 追加）。§12-0-B の
B-1 は **`primary` の 4 値だけ**を凍結対象にしているのに、本節は
**全ての機械軸**を z 化して `argmax` を取っていた。すると
`hnr_delta_db` / `N_similarity_delta` / `vowel_drift_l1` / `energy_decay_slope`
のような**測定仕様が未凍結の補助軸が primary に選ばれ、有料 Gate の合否を
決めてしまう**。§12-0-A-3 が「物理的に一番信用できる測定法を選ぶ」と定めた
規律とも矛盾する（校正していない軸は信用度が測れない）。

```
不変条件:
  Gate の primary 候補になれるのは
  `TRF measurement spec / 1.0` で**測定仕様が凍結された軸のみ**

既定の候補集合（= B-1 の既定スコープ）:
  excess_tail_voiced_ms / release_after_score_boundary_ms /
  tail_f0_persistence / terminal_mel_persistence

補助軸を候補に入れたい場合:
  B-1 のスコープへ明示的に追加し、同じ様式（式 + 単位 + worked example +
  reference output）で凍結してからでないと候補にしない
```

未凍結の軸は**診断レポートには残すが Gate には入れない**。

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

機械側の代表値 — **単純な Δz は使わない**（2026-08-20・重大な訂正）:
  # ★ z 化は (話者 × 世代) 内で中心化するので、その母集団が**一様に動いた
  #   変化は z から完全に消える**。run7 の全セルへ +10 ms 足しても z は不変で、
  #   耳の severity だけ上がり Δz_primary = 0 -> tied_direction となって
  #   **正しく動いている計器が Gate 3 で落ちる**。しかも本書は
  #   「ritsu の 3 区間が全て同符号で動いた」= まさに一様シフト型のドリフトを
  #   走行間変動の根拠にしている。その形の変化を検出できない正規化を
  #   同じ文書で採用していた。
  # 正規化を越えて残る **target 対 control のコントラスト**を使う:

  contrast(gen) = median( z'_primary | target セル  )
                - median( z'_primary | control セル )
      target  セル = P-RI-FINAL 系（破綻が起きる想定）
      control セル = P-N-FINAL / P-RI-MEDIAL / P-I-FINAL（起きない想定）
  Δcontrast = contrast(run7) - contrast(run5)

  一様シフトは target 側と control 側の両方に同じだけ乗るので **Δcontrast から
  相殺され**、逆に `/ri/` 固有の変化は残る（= 消したいものだけ消える）。

耳側の代表値 — **同じコントラスト構造で取る**:
  severity(cell, gen) = max(該当 position/expected の break 判定に使う軸)
      # §6 の 2 キー場合分けで「使わない」と定めた軸は max に入れない
  sev_contrast(gen) = median( severity | target ) - median( severity | control )
  Δsev_contrast = sev_contrast(run7) - sev_contrast(run5)

判定:
  sign(Δcontrast) == sign(Δsev_contrast)   -> Gate 3 pass
  どちらかが 0（同値・タイ）                -> **pass にしない**
                                              status = tied_direction
  borderline ラベルのセルは耳側の計算から除外し、除外件数を記帳
  target / control のどちらかが 3 セル未満   -> undetermined（Gate 不成立）
```

**一様シフトに対する worked check（実装が必ず通す）**:

```
参加セルの z'_primary（run5）: target {+0.9, +1.1, +1.0} / control {-1.0, -0.9, -1.1}
  contrast(run5) = 1.0 - (-1.0) = 2.0

ケース A（一様シフト）: run7 で**全セル**の生値へ +10 ms
  -> (話者×世代) 内中心化により z' は run5 と同一
  -> contrast(run7) = 2.0、Δcontrast = 0     … 機械側は「変化なし」
  -> 耳側も target/control 双方が同じだけ動けば Δsev_contrast = 0 で整合
     （**旧規則ではここで tied_direction になり計器が落ちていた**）

ケース B（target 固有の改善）: run7 で target セルだけ改善
  -> contrast(run7) = 1.2、Δcontrast = -0.8  … 機械側は「target が良化」
  -> 耳側も target だけ severity が下がれば符号一致で pass
```

**生スケールの median も併記する**（Gate には使わない診断値）。一様シフトが
起きた事実自体は記録に残さないと、「何も起きなかった」と読まれてしまう。

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

| セル | 耳 | `z'(excess_tail_voiced_ms)` | `z'(tail_f0_persistence)` |
|---|---|---|---|
| A | break | +1.8 | −0.4 |
| B | break | +1.4 | +0.9 |
| C | ok | −0.9 | +0.2 |
| D | ok | −1.1 | −0.7 |

（**両方とも `primary` の 4 値から採っている** — 上記 (2b) より、未凍結の
補助軸は候補に入らない）

`margin(excess_tail_voiced) = median(+1.8,+1.4) − median(−0.9,−1.1) = 1.6 − (−1.0) = 2.6`、
`margin(tail_f0_persistence) = median(−0.4,+0.9) − median(+0.2,−0.7) = 0.25 − (−0.25) = 0.5`。
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
    # ★ 2026-08-20 訂正: 点推定が 8-R 1 本のシフトしか引いていなかった。
    #   分散側は「k 本の平均を引く」前提で (1 + 1/k) を付けているのに、
    #   点推定だけ singular という不整合。対照シフトが +10 と -10 なら
    #   真のドリフト推定は 0 なのに、旧式は +10 を引いて残りを 8-B の効果と
    #   誤帰属する（8-R2 が bound にしか効かない）
    control_runs:       R = {run 8-R, run 8-R2, ...}   # |R| = k
    per_run_shift:      b_s^(r) = mean_i( x_i(r) - x_i(run 7) )   for r in R
    mean_shift:         b_bar_s = mean_{r in R}( b_s^(r) )        # ★ k 本の平均
    within_scatter:     SD_d,s = 対応差を R 全体でプールした標本 SD（ddof=1）
                        # 群内平方和を合算してから自由度で割る
                        #（1 本だけの SD を代表に使わない）
    corrected_effect:   Δ*_s = ( x(run 8-B) - x(run 7) ) - b_bar_s
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

**vNext での位置づけ（2026-08-21）**: 本節は **Gate 1 が不成立のとき**の
GPU $0 成果物として残る。Gate 1 が成立した場合は、同じ CPU 経路の上位互換として
**§7G（Run 8-0G）が走る**:

| §7-2 のレバー | §7G での扱い |
|---|---|
| 1. `/r/`-`/i/` duration 再配分 | **D アーム**（事前登録の ladder・primary level・判定代数を持つ = §7G-2 / §7G-5） |
| 2. 明示 SP 挿入 | **S アーム**（+ 必須対照 `S-frames-only` = §7G-3） |
| 3. `spk_embed` 補間 | **原因候補から降格し Identity control 扱い**。identity を動かす操作は 8-B の教育対象へ写像できず、「別の声になった」以上のことを言えないため（§7G-1） |
| （新設） | **F アーム**（`pitch_pred` の note-relative contour）/ **R-rescue アーム**（出力終端 taper・原因判定に不参加） |

## 7G. Run 8-0G / 8-0D / 8-0E — Genome-informed Native Intervention Gate（vNext 新設・2026-08-21）

**章番号に letter suffix を使うのは、既存の §8〜§12 への参照（§8-5-2b 等が
本書内外から多数引かれている）を壊さないため**である。実行順は
§7（TRF 観測子の Gate）の直後・§8（Run 8-B）の直前。

```
Run 8-0A  exposure ledger（= §3）
   ↓
Run 8-0B  360-cell probe + TRF observer calibration（= §4〜§6・§12-0）
   ↓ Gate 1 : TRF observer 成立（= §7）
Run 8-0G  Genome-informed Native Intervention Gate（本節）
   ↓ 「TRF を実際に動かす因果レバー」を選定（§7G-5 / §7G-6 / §7G-7）
Run 8-0D  8-B で教育可能なレバーかの判定（§7G-8）
   ↓
Run 8-0E  target recording / dosage dry-run の最終確定（§7G-9）
   ↓
Run 8-B   単一介入 40K 学習（= §8）
   ↓
Run 8-R   再現性 + 因果裁定（= §9）
```

**run 8 の目的は変えていない。** TRF 観測子・360 セル・校正→hold-out・
`dosage-fixed targeted partial replacement`・8-R 再現性裁定はいずれも維持し、
S3（Genome Architecture）で得た知見を **「どこを教育するか」の選択器**として
8-B の手前へ挿入するだけである。**8-0G が不成立なら 8-B へ進まない**という
構造も §0-6 のまま変わらない（進まない条件が 1 つ増える）。

### 7G-0. 前提条件（全て満たすまで 8-0G に着手しない）

```
[ ] P0  8-0B が完了し、§7-0b の合格条件 6 項目が全て成立している
        （不成立の場合に走るのは §7-2 の 3 レバー診断であって 8-0G ではない）
[ ] P1  介入は run 7 の**同一 ONNX 束**（acoustic / pitch / linguistic / dur /
        vocoder / emb）の上で行い、sha256 が 8-0B と一致することを assert する。
        ExecutionProfile も 8-0B と同一
[ ] P2  **B0 アームの出力 wav sha256 が 8-0B の対応セルと一致する**。
        これが「介入注入口は既定 off でバイト同一」を主張できる唯一の機械証拠で
        あり、一致しない時点で 8-0G の結果は全て無効（fail-closed）
[ ] P3  介入は canonical 経路への monkeypatch では実装しない
        （VG-L0 BLOCKER 7「monkeypatch の canonical 経路禁止」）。§10 PR-2-2 と
        同じ流儀の**既定 off の注入口**を通す
[ ] P4  ladder・primary level・判定代数（§7G-2 / §7G-5）を**1 レンダも走らせる
        前に** `s7_0g_arm_spec.json` へ書き出し sha256 を pin する
        （§12-0-C2 と同じ様式。走らせてから水準を足さない）
```

費用は CPU レンダのみで **GPU $0**（§0-7 の Run 8-0 行に含まれる）。
レンダ本数の見積り:

```
primary level : レンダを要する 5 アーム（B0 / D / F / S / S-frames-only）
                × 対象セル 41（§7G-5 の T 10 + C 31）              = 205
                ※ R-rescue は B0 出力の後処理なのでレンダ不要
ladder        : target 群 T の 10 セルのみ × レンダを要する桟 8
                （D 3 = 0.5/1.0/1.5 / F 3 = -100/0/+100 / S 2 = 2,4）  =  80
                ※ R の桟 {50, 200} ms も後処理
合計           : 285 レンダ + 後処理 30 = 8-0B の 360 本と同オーダー
```

VG-L0 probe が同経路で 51 条件 × 3 走行 = 153 レンダを完走している（§4-3）。

### 7G-1. S3 の 4 gene を run 8 へそのまま持ち込まない

S3 が gene として成立させたのは **WORLD 領域（sp / ap / f0）の凍結 pair 上での
移植**であって（`genome_s3/DESIGN_GENOME_S3.md` §3 の B0/F/D/E/R）、run 8 の
実推論経路とは**入力の集合が違う**。run 8 の経路は実装上こうなっている
（`s1_gate/gate_synth.py` `run_pipeline`）:

```
Stage 1  duration predictor   -> ph_dur_pred1 -> final_phone_dur   (:1151-1167)
   ↓ ph_dur2 = [HEAD_FRAMES] + final_phone_dur + [TAIL_FRAMES]     (:1174)
Stage 2  pitch predictor      -> pitch_pred -> f0_hz               (:1204,1215)
Stage 3  acoustic model       入力 = tokens / durations / f0 / spk_embed
                              -> mel                               (:1238-1246)
Stage 4  vocoder              入力 = mel / f0 -> waveform          (:1276)
```

**Duration は Stage 1 から後段すべてへ入り、F0 は Stage 2 から acoustic と
vocoder の両方へ入る。一方 energy に相当する native 入力はどの Stage にも無い**
（Stage 3 は `tokens / durations / f0 / spk_embed / depth / steps`、Stage 4 は
`mel / f0` のみ）。したがって S3 の 4 gene は次のように振り分ける:

| S3 形質 | run 8 での位置づけ | 8-B の原因候補 | 根拠 |
|---|---|---|---|
| **Duration** | native causal intervention | **YES** | `final_phone_dur` / `ph_dur2` が実在の介入点 |
| **F0** | native causal intervention | **条件付き** | `pitch_pred` は実在の介入点だが、教育データへの符号化が未定義（§7G-8） |
| **Energy** | **native 入力が無い** | **NO・診断のみ** | mel / waveform の gain を直接動かす以外に経路が無く、それは計器の循環（§7G-4） |
| **Release** | S3 では partial taper 中心 | **NO・rescue probe のみ** | 完全な release genome ではないため原因主張に使わない（§0-12） |
| **SP / terminal transition** | gene ではないが native 構造要因 | **YES** | 語尾 `/ri/`→SP の遷移そのもの。8-B の transcription 操作へ直結 |
| **spk_embed** | Identity 側 | **control へ降格** | identity を動かす操作は教育対象へ写像できない（§7-2 レバー 3 の正直会計） |

```
Primary causal candidates      Diagnostic only        Identity control
├─ Duration                    ├─ Energy              └─ spk_embed
├─ SP / terminal transition    └─ Partial Release
└─ F0
```

### 7G-2. アーム定義（同一 checkpoint・同一 probe・**一度に 1 つだけ**介入）

各アームは **primary level 1 点で判定**し、ladder は単調性・特異性の evidence
専用とする（**ladder を掃いて通った水準を後から primary にしない**）。
**特異性の対照は必ず 1 本置く**（摂動そのものへの反応を「レバーが効いた」と
読まないため = §7G-5 の `nonspecific_response`）。ただし対照の形はアームごとに
違う:

```
D / F   : 向きが定義できるので **逆符号の桟**を ladder に含める
S       : 逆符号が定義できないので **S-frames-only 対照**が特異性検査を兼ねる（§7G-3）
R-rescue: 因果主張をしないので特異性桟を要求しない（§7G-4）
```

| アーム | 介入点 | primary level | ladder（単調性 / 特異性） |
|---|---|---|---|
| **B0** | なし | — | — |
| **D** | Stage 1 の `final_phone_dur`（終端モーラの `/r/`:`/i/` 配分） | `r_share_scale = 2.0`（= 終端母音を短くする向き） | `{0.5, 1.0, 1.5, 2.0}`。**1.0 は B0 とバイト一致でなければならない**（no-op 検算）。0.5 が逆符号の桟 |
| **F** | Stage 2 の `pitch_pred`（終端ノート内の note-relative contour） | 終端ノート後半 25% に**平均保存**の線形 contour `-200 cents` | `{-200, -100, 0, +100}`。0 は B0 とバイト一致。`+100` が逆符号の桟 |
| **S** | 音素列（語末 `/ri/`→**明示 SP トークン**）+ frame 収支 | `sp_frames = 8`（= `TAIL_FRAMES` と同尺） | `{2, 4, 8}` + **必須の対照 `S-frames-only`**（§7G-3） |
| **R-rescue** | Stage 4 出力波形の終端 taper | cosine fade `100 ms` | `{50, 100, 200} ms`。**原因判定には使わない**（§0-12） |

**F アームは絶対 Hz を PJS からコピーしない**（他話者の f0 をコピーすると
identity を動かし、かつ「破綻しない側を見て介入を決めた」ことになる）。
動かすのは **note-relative contour のみ**で、**ノート内平均 f0 を保存する**。

**F アームの変換式を凍結する（2026-08-21・Codex P2 指摘を採用）**: 「後半 25% に
線形 contour `-200 cents`」だけでは実装が割れる（`-200` は終点か・全振幅か・
定数オフセットか / 平均保存の補償を窓の内側でやるか外側でやるか）。同じ
`s7_0g_arm_spec.json` から違う TRF 値・違う A〜F 判定が出るので、**フレーム単位の
式・境界処理・平均復元規則**を事前登録する:

```
入力 : pitch_pred（MIDI 半音・float32。Stage 2 出力。f0_hz へ変換する前に当てる）
対象 : 終端ノートが占める frame 区間 [n0, n1)（ph_dur2 の HEAD_FRAMES 分を
       オフセットして note_target_frames から決定論的に導く）
L = n1 - n0
W = floor(L * 0.25)                      # 窓は**末尾 W frame** = [n1-W, n1)

1. ランプ（窓内のみ・窓外は 0）:
     i ∈ [n1-W, n1) について
       t(i)     = (i - (n1 - W) + 1) / W          # t ∈ (0, 1]、最終 frame で 1
       delta(i) = depth_cents * t(i) / 100.0      # 半音単位
     ※ **depth_cents は「最終 frame における深さ（終点値）」**である。
       全振幅でも定数オフセットでもない
     ※ i ∉ 窓 について delta(i) = 0

2. 平均復元（**ノート全体で**行う。窓の内外どちらにも同じ量を当てる）:
     m = mean_{i ∈ [n0, n1)} delta(i)
     pitch_pred'(i) = pitch_pred(i) + delta(i) - m     for i ∈ [n0, n1)
     ノート外の frame は**触らない**
     -> 構成上ノート内平均は厳密に保存される（残差は float 丸めのみ）

3. 境界処理:
     W < 4          -> セルを dropped: f0_window_too_short として記帳
     depth_cents = 0 -> delta ≡ 0・m = 0 なので **B0 とバイト一致**（no-op 検算）

4. ガード（保存が壊れていないことの検算・事後の言い訳に使わない）:
     |mean(pitch_pred') - mean(pitch_pred)| を cents で記帳し、
     5 cents を超えたら fail-closed
```

ladder の `{-200, -100, 0, +100}` は**すべて `depth_cents`（終点値）**として
解釈する。

**フレーム下限**: D / S は総 frame を固定したまま配分を動かすため、音素が
潰れうる。VG-L0 が「`notes_limit=10` で最小子音 2 frame = 下限まで残り 1」を
実測しているので、**下限 2 frame を事前登録**し、下回るセルは
`dropped: duration_floor_clipped` として記帳する（黙って丸めない）。

### 7G-3. 各アームの拘束と、**stage 局在を主張しない**理由

```
D アーム : spk_embed / 譜面 / model / SP 構成 は不変。
           **「F0 unchanged」は「直接介入しない」という意味であって、
             出力 f0 が不変という意味ではない** — ph_dur2 は Stage 2 の入力
             なので pitch_pred は当然動く。そこを固定したら
             「duration を変えた結果が後段へ伝わる」因果経路そのものを切る
F アーム : **Duration byte-identical**（`final_phone_dur` / `ph_dur2` が B0 と
           一致することを assert する。Stage 1 は決定論なので構造的に成立）。
           SP 構成・spk_embed・acoustic / vocoder も不変
S アーム : spk_embed / model / 譜面の音高・総 frame は不変。
           **ただし音素列が変わるので Stage 1 の配分も動く**（下記）
R-rescue : Stage 1〜4 は完全に B0 と同一。触るのは出力波形だけ
全アーム共通 : `spk_embed` のバイト一致・ONNX 束 sha 一致を assert する
```

**S アームは stage 分離されていない（重要）**。語末に SP トークンを足すと
`v_tokens1` が変わり、**duration predictor の入力自体が変わる**ので、S の効果は
「終端遷移 cue の効果」と「終端母音が短くなった効果」の和になる。したがって:

```
必須の対照 S-frames-only（**S の実配分から構成する** = 2026-08-21・
                          Codex P2 指摘を採用）:
  ★ 独立に Stage 1 を走らせて「同じ frame 数だけ削る」のでは対照にならない。
    SP トークンを足すと encoder 出力が変わり、duration predictor は
    sp_frames の固定移動を**超えて** /r/ と /i/ を再配分しうる
    （`s1_gate/gate_synth.py:1129-1167`）。その場合 S と対照は
    **SP cue ではなく配分そのものが違う**ので、S が勝っても cue の証拠にならない

  構成手順:
    1. S アームを走らせ、その Stage 1 出力 final_phone_dur^S を取得する
    2. SP エントリを取り除き、その frame 数を直前の /r/ へ加える
    3. **残る全音素の frame 数は final_phone_dur^S のコピーをそのまま使う**
       （対照側で Stage 1 を再実行しない）
    4. assert: 非 SP 音素の frame 配分が S と**完全一致**する
       （一致しない実装は対照として無効・fail-closed）

  残る既知の交絡（明示する）:
    sp_frames が「末尾の SP」に置かれるか「先頭側の /r/」に加わるかの差は残る。
    D アームの ladder が同程度の /r/ 伸長に感度を示した場合、S の優位は
    SP cue に帰属できない -> verdict = duration_confounded

S が EFFECTIVE_LEVER を名乗れるのは、S が §7G-5 を満たし
**かつ S-frames-only が同じ判定を満たさない**場合だけである。
両方満たした場合 -> verdict = duration_confounded
```

**`duration_confounded` のときの昇格代数（2026-08-21・Codex P2 指摘を採用）**:
「D アーム側の証拠として計上する」だけでは **D の status も flip 集合も median も
変わらない**ため、`S-frames-only` が A〜F を満たしているのに D（`r_share_scale`
という別パラメータ化）が満たさない場合、**§7G-6 に候補が 1 つも残らず primary
lever を選べない**（教育可能な duration 介入が実証されているのに 8-B が止まる）。
よって Duration を**ファミリ**として扱い、昇格を機械的に定義する:

```
Duration ファミリ = { D（r_share_scale） , S-frames-only }
  ※ どちらも「終端母音の frame を削って前へ回す」介入の別パラメータ化である

ファミリ代表の決定（§7G-6 と同じ順序をファミリ内で先に適用する）:
  1. A〜F を満たしたメンバのみ候補
  2. T 上の break→ok flip 数が多い方
  3. median Δz'_primary が大きい（負に大きい）方
  4. 同点なら固定順（D > S-frames-only。実装既存の介入点が先）
  -> 代表が存在すれば **Duration は EFFECTIVE_LEVER として §7G-6 に参加**する
     （代表の flip 集合と median を Duration の値として使う）
  -> 候補が空なら Duration は NOT_EFFECTIVE

primary lever が Duration になり、その代表が S-frames-only だった場合:
  8-0D / 8-0E で教育対象として記述する形質は
  「終端母音から差し引いた frame 数（= 終端母音を短くする配分）」であり、
  **SP トークンの明示ではない**（S は duration_confounded で落ちている）
```

`S` 自身は `duration_confounded` のまま EFFECTIVE_LEVER にならないが、
**その frame 収支ぶんの効果は Duration ファミリを通じて回収される**。

同様に **D アームで改善が出ても「原因は duration であって acoustic ではない」
とは言わない**（§2-3 H-dur の到達限界がそのまま適用される）。8-0G が示せるのは
**「その入力を動かすと TRF が動く」= 有効な因果レバーである**までである。

### 7G-4. Energy を既定の実験系から外す理由と、R-rescue の `instrument_coupled`

TRF の機械軸には **RMS / エネルギーに直接依存する量**が含まれる
（`energy_decay_slope`、絶対 RMS 閾値 `1e-3` で有声判定する
`excess_tail_voiced_ms` / `hnr_median_db_*`）。mel や waveform の gain を
動かすと、これらは**破綻の有無と無関係に**動く。「計器を操作して計器が
改善した」という循環になるので、**Energy アームは既定の実験系に置かない**
（§0-11）。

**同じ理屈は R-rescue にも一部かかる。** 終端 taper は出力振幅を直接下げる
ので、機械 TRF 値は必ず「改善方向」へ動く。したがって:

```
R-rescue の機械 TRF 値には status = instrument_coupled を立てる
R-rescue は §7G-5 の EFFECTIVE_LEVER 判定に**参加しない**
R-rescue の一次証拠は §7G-7 の耳側（2 問）であり、
言えるのは「終端出力を適切に減衰させれば症状を救済可能」= **phenotypic
rescue** まで。**「Release が原因」とは言わない**

R-rescue の「効いた」の定義（§7G-10 の分岐で使う）:
  §7G-7 の 2 問が 2/2 で R-rescue 側    -> rescue_confirmed
  それ以外                              -> rescue_not_confirmed
  機械 TRF 値は instrument_coupled 付きの**参考値**として併記するだけで、
  判定には使わない
```

### 7G-5. 機械判定（**新しい TRF metric を作らない**）

primary axis と `θ` は **B-1 / B-2 で凍結したものをそのまま使う**（§12-0）。
`ε` は §12-0-C の `ε_axis`（primary 軸のもの）をそのまま使う。

**z 化の基準は B0 アームで凍結する（§0-13）**:

```
z'_primary(cell, arm)
  = orient(primary) * ( x(cell, arm) - median_ref ) / ( 1.4826 * MAD_ref )

  median_ref / MAD_ref = **8-0B の当該 (話者 × 世代) 母集団**から取った定数
                         （B0 アームはその母集団と byte 一致する部分集合なので、
                           B0 を基準にすることと同義）
  orient(primary) / θ  = B-1 / B-2 の凍結値をそのまま使う
```

アームごとに z 化し直してはならない。**アーム全体が一様に動いた効果は
再中心化で完全に消える** — §7-0 (7) が Gate 3 で踏み抜いたのと同型の穴で、
介入が効いているほど「効果 0」と報告されてしまう。

**ringing 補正（§5-0）はアーム内で独立に適用する**。介入は出力波形を変えるので、
無声対照の母集団も B0 のものを流用できない。§5-0 の比較群を
「同一話者 × 同一世代 × **同一アーム**」と読み替えて leave-one-out を適用し、
補正不能な群が出たアームは **NOT_EFFECTIVE ではなく `undetermined`**
（`ringing_uncorrected_group`）として記帳する — 生値と差分を混ぜた比較で
レバーの合否を決めない。

```
機械判定ラベル（耳の break/ok とは別物なので記号を分ける）:
  machine_break(cell, arm) := [ z'_primary(cell, arm) >= θ ]

対象集合（事前登録・cell_id で列挙して pin する）:
  target 群  T = ritsu × run7 × { P-RI-FINAL 9 セル（b∈{b1,b2,b4} × p∈{p57,p62,p65}）}
                 ∪ { run7/ritsu/P-ANCHOR/sakura-kagiri }              -> 10
  control 群 C = ritsu × run7 × { P-N-FINAL 9 / P-I-FINAL 9 / P-RI-MEDIAL 3
                                  / P-ANCHOR の「うみ」セル }
                 ∪ pjs × run7 × { P-RI-FINAL 9 }                      -> 31
  ※ C は「介入を当てるが良化を期待しない」held-out 対照であり、
    §8-4 の**学習** held-out とは別物

判定（全て primary level で評価する）:
  A  machine_break(anchor, B0) == True     # baseline が break（anchor =
                                           #   run7/ritsu/P-ANCHOR/sakura-kagiri）
  B  machine_break(anchor, arm) == False   # 介入で ok へ flip
  C  #{ c ∈ T\{anchor} : machine_break(c,B0) ∧ ¬machine_break(c,arm) } >= 1
  D  median_{T} z'_primary(arm) - median_{T} z'_primary(B0) <= -ε
  E  #{ c ∈ C : ¬machine_break(c,B0) ∧ machine_break(c,arm) } == 0
  F  独立プロセスでの再実行で A–E の判定と flip 集合が**完全一致**
     （同一プロセス内反復は independent replay の証拠にならない = §7-0b 6）

全て満たす      -> EFFECTIVE_LEVER
一つでも欠ける  -> NOT_EFFECTIVE（欠けた条件を理由コードで記帳）

特異性の検査（§7G-2 の対照から機械的に立てる）:
  D / F : 逆符号の桟でも**判定 D**（median の改善）が ε を超える
          -> status = nonspecific_response
  S     : S-frames-only も A〜F を満たす -> status = duration_confounded
          （効果は D アーム側の証拠として計上する = §7G-3）
  いずれも当該アームを EFFECTIVE_LEVER にしない（undetermined として記帳）
  ※ 「摂動なら何でも良かった」を「レバーが効いた」と読み替えないため
```

**閾値・ladder・primary level・対象集合を事後に変更して通し直さない**
（§0-6 と同じ規律）。変更が要ると判断した場合は**本 memo の改訂**として扱い、
変更前後の判定を両方記帳する。

**検出力の正直な記述**: T は 10 セルであり、`C` は 1 セルの flip で満たされる。
**8-0G は「効かない」を積極的に示す設計ではない** — 全アーム NOT_EFFECTIVE は
`undetermined`（検出力不足を排除できない）であって「native レバーは存在
しない」ではない（§9-0b と同じ規律）。

### 7G-6. 複数アームが通った場合の primary lever 選定（事後判断を避ける）

```
1. T 上の break→ok flip 数が多いアーム
2. median Δz'_primary が大きい（負に大きい）アーム
3. 同点なら固定優先順位  SP > Duration > F0
```

**この優先順位は「優れている順」ではなく、8-B の教育データへ直接写像できる順**
である（SP は transcription の音素列そのもの、Duration は発声指示と
アラインメント、F0 は符号化未定義 = §7G-8）。選ばれるのは **1 つだけ**で、
run 8-B は単一介入のまま保たれる（§8-3）。

### 7G-7. 人間確認は 2 問だけ

360 セルをもう一度聞かない。**機械 Gate で primary lever を 1 つに絞った後に
だけ**、blind A/B を 2 問行う。

```
提示ペア（**選択規則を事前登録し、機械判定から決定論的に導く**。導いた結果を
          聴取前に s7_0g_listening_pair.json へ書き出して sha256 を pin する）:
  Q1  run7/ritsu/P-ANCHOR/sakura-kagiri : B0  vs  primary lever（primary level）
  Q2  **§7G-5 の条件 C を満たしたセル**（非 anchor で break→ok に flip した
      target）のうち **cell_id 昇順で先頭**            : B0  vs  同上
      ※ 条件 C により flip 集合は**非空が保証される**
      ※ R-rescue（機械判定に参加しない = §7G-4）を聴く場合は、
        T のうち machine_break(c, B0) == True のセルから cell_id 昇順で先頭

規約は §6-1 と同一: 提示順ランダム・**どちらが介入側かを伏せる**・
モデル名 / アーム名を伏せる。質問文は 1 つだけ:
  「どちらが終端破綻が弱いか？」

判定:
  2/2 で介入側     -> HUMAN_CONFIRMED   -> 8-0D へ
  それ以外          -> machine_effect_only -> **8-B BLOCKED**
```

**Q2 を固定セルにしない理由**（2026-08-21・Codex P2 指摘を採用）: 条件 C は
「anchor + 非 anchor 1 セル」で満たせるので、**あらかじめ選んだ固定セルが
B0 で break でない / 当該アームで flip していない**ことが正当に起こる。その
セルを Q2 に据えると、**改善が存在しないペアを聴かせて 2/2 を要求する**ことに
なり、有効なレバーを `machine_effect_only` で誤ってブロックしうる。選択規則
（= flip 集合から cell_id 昇順）は事前登録され、選択自体は機械結果から
決定論的に決まるので、「動いたセルを人が選んだ」経路にはならない。

**これは統計的証明ではない**（帰無仮説のもとで 2/2 が偶然出る確率は 0.25）。
GPU 学習へ進むための**最低限の人間 Gate**としてのみ機能し、§9 の因果裁定にも
§11 の終端宣言にも使わない。R-rescue のみが効いた場合も同じ 2 問を行うが、
その結果は **phenotypic rescue の記録**であって 8-B の解錠には使わない
（§7G-10）。

### 7G-8. Run 8-0D — 8-B で教育可能なレバーかの判定

EFFECTIVE_LEVER かつ HUMAN_CONFIRMED になったレバーについてのみ行う。
**8-0G は inference-time の介入で、8-B は data-time の教育である** — 両者を
繋ぐのは写像仮説であり、それが立たないレバーは因果的でも教育できない。

| 軸 | 内容 | SP | Duration | F0 |
|---|---|---|---|---|
| **T1** 写像先の存在 | 学習データ側の操作へ写像できるか | `transcriptions` の音素列で `/ri/`→SP を明示（**現行 8-B が既に行う操作** = §8-5-2） | 収録カードの発声指示 + アラインメント由来の `ph_dur` 配分 | **未定義**（note-relative contour を録音の何で教えるかが決まっていない） |
| **T2** 機械検証可能性 | 「教えたい形質が入っている」ことを dataset 組み立て時に機械検査できるか | §8-5-4 の選抜 5 項目でそのまま検査可 | 配分の事前登録が要る（どの `/r/:/i/` 比を「教育」と呼ぶか） | T1 が無いので評価不能 |
| **T3** dosage 固定との両立 | 15 row・±1% の枠内で実現できるか（§8-3） | 現行設計で充足済み | 同左（構成のみ変更） | 同上 |

```
判定:
  T1–T3 すべて充足     -> trainable                      -> 8-0E へ
  T1 を欠く            -> causal_but_not_trainable_yet   -> **8-B へ進まない**。
                          符号化 mini-spec を凍結してから再入する（§12 OQ4）
  T2 / T3 を欠く       -> not_trainable_under_current_contract -> run 9 候補
```

**F0 の既定経路は `causal_but_not_trainable_yet` である。** 「F0 を変えたら
TRF が動いた」は「TRF は pitch trajectory に因果感度を持つ」までしか言わず、
**それだけでは 8-B の録音設計へ直結させない**。

### 7G-9. Run 8-0E — target recording / dosage dry-run の最終確定

`trainable` と判定されたレバーについて、**User に実収録を発注する前に**
（= User の実作業を空振りさせない）次を確定する。ここまで GPU $0。

```
[ ] §8-1 の収録カード 16 枚を、選ばれたレバーに合わせて確定する
      SP       -> 現行カードのまま（/ri/→SP 明示の指示）
      Duration -> 8-0G の primary level の**向き**に対応する発声指示を追加
                  （成功した配分側へ寄せる。数値そのものを読み上げさせない）
[ ] **選ばれたレバーの形質受け入れ規則を `s7_0e_pack_spec.json` へ凍結する**
    （2026-08-21・Codex P1 指摘を採用。**発声指示だけでは T2「機械検証可能性」を
      満たさない** — 歌い手のテイクが実際に配分を動かしていなくても §8-5-4 の
      選抜は通り、**選ばれたレバーを含まないパックで 40K 学習が回る**）
      SP       -> 既存: 各 target row の `/ri/` 終端が `transcriptions` 上で
                  SP へ接続していること（§8-5-2・すでに機械検証可）
      Duration -> **アラインメント後の target row で数値規則を検証する**:
                  方向規則 = 当該 row の終端モーラの `/r/` frame 比
                             r_ratio = r / (r + i) が、**同一話者の
                             baseline 15 row の中央値より大きい**こと
                             （= 8-0G の primary level の向きと同符号。
                               絶対閾値は候補プール実測後に凍結してよいが、
                               **選抜を 1 度も走らせる前に**この JSON へ書く）
                  検証単位 = row。満たさない row は**選抜から落とす**
      F0       -> §7G-8 のとおり符号化 mini-spec が凍結されるまで受け入れ規則を
                  定義できない -> `causal_but_not_trainable_yet` のまま進まない
[ ] **受け入れ規則を満たす target row が 9 本に満たない場合は fail-closed**。
    dosage を埋めるために規則を満たさない row を混ぜない（混ぜた時点で
    「その形質を教育した」という主張が成立しなくなる）
[ ] §8-5-2c の E=3 承認条件 7 項目のうち未充足分（2/3/5/6）を閉じる
[ ] §8-5-2b の**収録前ゲート**（決定論選抜の空回しで voiced と total ph_dur の
    両方が ±1% に入る 6 row 部分集合の存在を確認）を通す
[ ] dosage dry-run: target 9 row + retained baseline 6 row = 15 row の選抜を
    **候補プール未収録のまま**、既存 user 15 row の実測値で空回しする
[ ] 出力 `s7_0e_pack_spec.json` を sha256 で pin する（§8-5-7 の会計へ接続）
```

**この Gate を通るまで収録を発注しない。** 逆に、通った時点で
「何を教育するか」「その形質が入ったことをどう機械検証するか」「dosage を
どう固定するか」の 3 つが揃っており、8-B は**設計判断ゼロで実行できる**。

### 7G-10. 8-B への接続（結果別の分岐）

| 8-0G の結果 | 8-B の扱い |
|---|---|
| **S が primary** | 現行の transition-density 設計を**そのまま採用**（`/ri/`→SP 明示・E=3・target 9 + retained baseline 6・dosage 固定）。設計変更なしで 8-B へ |
| **D が primary** | 同じ dosage 固定構造を維持し、target 9 セルを「**duration 形質を教育する pack**」として使う。`/r/ : /i/` 配分は 8-0G で成功した側へ寄せた発声指示にする（変更は収録カードの指示文のみ） |
| **F が primary** | **即 8-B へ行かない**。F0 形質を training data へどう符号化するかを別 mini-spec で凍結してから 8-B（既定は `causal_but_not_trainable_yet` = §7G-8） |
| **R-rescue だけ効く** | **8-B へ進まない**。原因は未同定。ただし「出力側で救済可能」を記録し、推論後処理トラックとして run 9 候補へ回す |
| **何も効かない** | **8-B へ進まない**。現行 target recording を**発注しない**。全アームの結果を診断レポートとして残し、裁定は `undetermined`（§7G-5 の検出力の但し書き） |

これにより「**録音して GPU 学習したが、そもそも何を教育していたのか分からない**」
を構造的に防ぐ。

### 7G-11. 8-0G の到達限界（言えること / 言えないこと）

```
言える  : 「run 7 checkpoint 上で、この native 入力を動かすと TRF 観測値が
           θ を跨いで動く」= 有効な因果レバーである
言えない: 「学習データ中のその形質が破綻の原因である」
           （8-0G は inference-time の介入であり、data-time の主張をしない）
言えない: 「破綻は Stage N に局在する」
           （D は下流全段へ伝播し、S は Stage 1 から効く = §7G-3）
言えない: 「Release が原因」（phenotypic rescue まで = §0-12）
言えない: 「native レバーは存在しない」（全アーム NOT_EFFECTIVE は
           undetermined = §7G-5）
```

改修後の run 8 は次の一本の因果鎖になる:

```
S2 / S3 : Voice / Performance を（部分的に）分解できる
   ↓
8-0G    : そのうちどの native 形質が TRF を動かすか
   ↓
8-B     : その形質を狙った単一の教育介入
   ↓
8-R     : 教育前後で本当に TRF が変わったか（ドリフトを差し引いて）
```

**最終的な成功宣言は次の 1 文に限定する**（§8-5-6 の主張範囲表と §11 の
終端宣言を上書きしない）:

> **dosage 固定の標的置換パッケージ**（§7G-6 の primary lever が指す形質を
> 狙って構成したもの）による単一介入によって、**非標的終端文脈で測る限り
> Identity を変更せず**、TRF を再現可能に改善した。

**帰属は package-level に留める**（2026-08-21 改訂・Codex P1 指摘を採用）。
初版の宣言文は「**特定の Performance / transition 形質への**単一教育介入」と
書いており、**§8-5-6 / §9 が `undetermined` と定めた機構レベルの帰属を
終端宣言の側で先取りしていた**。8-0G が示すのは inference-time の感度であって、
**8-B で実際に効いたのが標的素材か・収録セッション差か・語彙分布の変化か・
baseline 9 row の除去かは分離されていない**（尺を揃えた非標的置換対照が
無い限り = §8-5-6 の 3）。形質レベルの帰属を名乗るには run 9 のその対照が要る。

**Identity 句は無条件では名乗れない**（2026-08-21 改訂）。上は **§7G-12 経路 1
（identity preservation check を per (cell, axis) で全 pass）を通した場合の
最大限**であり、射程は**非標的終端文脈で測った identity**に限られる。
測定が成立しない場合（経路 2）と実測して超過した場合（経路 3）の宣言文は
**§7G-12 が正**である。**無条件の `Identity を変更せず` はどの経路でも
名乗れない**。

### 7G-12. 「Identity を変更せず」を名乗る条件（2026-08-21・Codex P1 指摘を採用）

上の宣言文の **`Identity を変更せず` は、本書のどの Gate によっても支えられて
いなかった**。8-0G は inference-time で `spk_embed` を触らないが、**8-B は
40K の fine-tune で共有デコーダを動かす**ので、TRF が改善しつつ**実現された
声が別人へ寄る**ことは起こりうる。その場合、全ての Gate を通ったまま宣言文が
偽の成功を報告する。よって宣言の条件を事前登録する:

```
経路 1（実測して名乗る）— identity preservation check:
  射程     : **非標的終端文脈（`/i/` `/N/` 系）で測った identity のみ**。
             標的 `/ri/` 文脈は TRF 変化と分離できないため射程外（下記 ※ 2 つ目）
  対象セル : **被処置話者 user を必ず含む**（2026-08-21・Codex P1 指摘を採用。
             初版は §6-2 の P2 = {ritsu, pjs} だけを見ており、
             **8-B が user の声だけを動かした場合にゲートが素通りする**）
             未処置側 : {run7, 全未処置反復, run8-B}
                        × {ritsu, pjs} × {P-RI-FINAL/b4/p57, P-N-FINAL/b4/p57}
                        （= §6-2 の P2 と同一）
             被処置側 : {run7, 全未処置反復, run8-B}
                        × user × {P-I-FINAL/b4/p57, P-N-FINAL/b4/p57}
                        （= §6-2 の P1 のうち **標的でない終端**の 2 セル）
             ※ user の `/ri/` 終端セルを identity 判定に**入れない**理由:
               そこは 8-B が**意図して変える**場所であり、identity 距離が
               「意図した TRF 変化」と交絡する。値は診断として併記するが
               判定には使わない（意図した改善を identity 逸脱として罰しない）
             ※ **その代償として、経路 1 が保証する射程は「非標的終端文脈で
               測った identity」に限られる**（2026-08-21・Codex P1 指摘を採用）。
               標的 `/ri/` 文脈で identity だけが動いた場合、本ゲートは
               検出しない。よって**宣言文の側で射程を明示する**（下記）と同時に、
               標的文脈の identity 保存は **`undetermined` と記帳する**
  計器     : **既存計器のみ**。`singer/identity_metrics.py` の
             正規化 E1/E2（`normalized_e1_e2`）と `cosine_distance`
             （S2 T1 の識別軸。**新しい identity 計器は作らない** = §0-5 の
               単一スコア禁止・§12-0-A-3 の「分離性能で計器を選ばない」と同型）

  集約規則（2026-08-21・Codex P2 指摘を採用。**スカラー `d_spk` を作らない**）:
             計器は **cell ごと・軸ごと（E1 / E2）に別々の距離**を出すので、
             どこで平均を取るかで同じ実測から違う裁定が出る。よって
             **軸もセルも潰さず、per (cell, axis) で判定して連言を取る**:

               d_B(cell, axis)   = cosine_distance( E_axis(run7, cell),
                                                    E_axis(8-B,  cell) )
               d_ref(cell, axis) = median over 全未処置反復 r of
                                   cosine_distance( E_axis(run7,  cell),
                                                    E_axis(8-R_r, cell) )
                 ※ 反復の集約が median なのは §6-2 の Δsev_ctrl と同じ流儀
                   （k = 1 ならその 1 本の値）

               pass(cell, axis) := d_B(cell, axis) <= d_ref(cell, axis) + ε_id

             **軸間・セル間の平均や max を取らない**（1 つでも落ちれば落ちる
             = fail-closed）。落ちた (cell, axis) と両辺の値を必ず記帳する

  判定     : **全話者（user を含む）× 全対象セル × 両軸が pass のときのみ**
             経路 1 で宣言可。**1 つでも超えたら経路 3**（= 実測された逸脱で
             あり「証拠が無い」ではない・2026-08-21・Codex P2 指摘を採用）
  ε_id     : §7-1 の閾値と**同時に**（8-B 開始前に）凍結する。事後に決めない
  未処置反復が 0 本（8-R 未実施）/ 計器が適用できない場合は d_ref が定義できず
  **測定そのものが成立しない** -> 経路 2（§9-0 と同じ扱い）

経路 2（名乗らない）— **測定が成立しない場合に限る**
  （実測を試みていない / 計器が適用できない / 8-R が無く d_ref が定義できない）。
  **実測して超えた場合はここへ来ない**（それは経路 3）:
  宣言文を次へ**縮退**させ、実現された identity の不変は undetermined と記帳:
  > **dosage 固定の標的置換パッケージ**による単一介入によって、
  > **Identity 入力（spk_embed / 話者構成 / dosage）を変更せず**
  > TRF を再現可能に改善した（実現された identity の保存は undetermined）。

経路 3（実測して**超えた**場合・1 つでも pass しなかったら必ずここ）—
  「未実測」と同じ扱いにしない:
  status = identity_not_preserved
           （超えた (speaker, cell, axis) と d_B / d_ref / ε_id を記帳）
  宣言文から **Identity 句を落とす**（入力側の不変も主張しない）:
  > **dosage 固定の標的置換パッケージ**による単一介入によって、
  > TRF を再現可能に改善した。**ただし実現された identity は保存されなかった**
  > （話者 <spk> で d(run7, 8-B) が未処置ドリフト + ε_id を超過）。
  ※ この場合 run 8 は「TRF は改善したが Identity 代償を伴う」結果であり、
    §8-5-6 の主張範囲表にもその旨を記帳する
```

**経路 1 で名乗ってよい宣言文（射程を文中に残す・2026-08-21 追加）**:

> **dosage 固定の標的置換パッケージ**（§7G-6 の primary lever が指す形質を
> 狙って構成したもの）による単一介入によって、**非標的終端文脈で測る限り
> Identity を変更せず**、TRF を再現可能に改善した
> （標的 `/ri/` 文脈の identity 保存は `undetermined`）。

**無条件の `Identity を変更せず` は本設計のどの経路でも名乗れない。** 標的文脈で
identity と TRF を分離するには、終端窓を除いたノート核で測る変種
（`identity_metrics._vowel_core` 相当の窓）を **B-1 と同じ様式（式 + 単位 +
worked example + reference output）で凍結する**必要があり、本走行の射程外である
（§12 OQ6）。**射程を広げたいという理由で計器を後から作らない。**

**適用可否の未確認事項（正直な記述）**: `identity_metrics` は `render_song` 系の
`RenderResult` を前提にしており、**`gate_synth` 出力へそのまま適用できるかは
未確認**である。適用できない場合に新しい計器を起こして経路 1 を通そうとしない —
**既定は経路 2（宣言の縮退）**であり、計器の新設は本 memo の改訂として扱う。

## 8. Run 8-B — **dosage-fixed targeted partial replacement**

**正式名称 = `dosage-fixed targeted partial replacement`**（User 裁定 2026-08-20
第 4 次で確定）。**「5〜8 分を全部学習投入する設計ではない」**ことを名称に
含める。5〜8 分は**候補プール**であり、学習投入量は run 7 と同一に固定される
（§8-1 / §8-3）。

**vNext の前提（2026-08-21）**: 本節の構造（dosage 固定・target 9 + retained
baseline 6・単一介入）は**変更しない**。変わるのは **何を教育するかが §7G-6 の
primary lever で決まる**ことと、**8-0E（§7G-9）を通るまで収録を発注しない**
ことの 2 点だけである。レバー別の接続は §7G-10 の分岐表が正:

```
S が primary  -> 本節をそのまま採用（/ri/→SP 明示の transition-density 設計）
D が primary  -> 同じ dosage 固定構造のまま、target 9 セルを
                 「duration 形質を教育する pack」として使う（変更は指示文のみ）
F が primary  -> 符号化 mini-spec を凍結するまで 8-B へ進まない（§12 OQ4）
R-rescue のみ / 何も効かない -> **8-B へ進まない**（収録を発注しない）
```

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
| **dosage 固定 + 部分置換**（既定） | 「**user 総 dosage を固定したまま、置換パッケージ全体を入れ替えた効果**」（下記の限定を必ず併記） |
| target 素材だけで全置換（(a) へ退化） | **一般語彙喪失との交絡が残る** — 改善を標的効果に帰属できない |
| 171.88 s を**超えて追加**（(c)） | 「**target 内容 または 量増加が効いた**」までに限定 |

**「標的 `/ri/` 歌唱が効いた」とまでは言えない**（2026-08-20・P1 訂正）。
run 8-R が bit 一致でドリフトを排除できたとしても、8-B が入れ替えるのは
**パッケージ一式**である:

```
8-B で同時に変わるもの:
  新規収録セッションそのもの（別日・別コンディション）
  語句の集合 / 音高・尺の分布 / 無音密度
  retained baseline として**残した 6 row の選抜**（= 9 row を落とした効果）
一方 8-R は元の 15 row を一切触らない
```

したがって **「有害だった baseline row を外したから良くなった」**や
**「新しく録った素材なら何でも良かった」**が、そのまま「標的歌唱の効果」と
ラベルされうる。機構レベルで分離するには**尺を揃えた非標的置換対照**
（同じ 9 row を同秒数の**非標的**新規収録で置き換えた走行）が要り、
収録 1 本 + 走行 1 本の追加になる。

**本走行では対照を追加しない。** 代わりに:

1. **主張を「置換パッケージ全体の効果」に留める**。§9 の振り分け表も
   この射程で読み、機構語（「標的音素対策として成功」等）は下記 2 の条件を
   満たしたときだけ使う
2. **機構側の部分的な証拠は pack 内の held-out 対照から取る** — `/ri/` が
   改善して `/su/`・`/i/`・語中 `/ri/` が動かなければ、「新規収録なら何でも
   良かった」は説明力を失う（汎用的な改善なら対照側も動くはず）。
   ただしこれは**「有害 baseline row の除去」を否定しない**ので、
   そこは `undetermined` で記帳する
3. 尺を揃えた非標的置換対照は **run 9 以降の候補**として §12 に残す

#### 8-5-7. 会計の記帳

`user_ri_pack_selection.json` に「録音した全カード（capture_pool）」
「学習へ投入したカード/秒数（train_user_dose）」「held-out へ回したカード
（primary / secondary の別を含む）」を **3 分割で記帳**する。投入と余剰の
境界が後から動かせると dosage 固定が形骸化するため、**選抜結果は pin して
8-B 実行前に凍結する**。

## 9. 結果による振り分け

**この表は「置換パッケージ全体」の効果として読む**（2026-08-20・P1 訂正）。
§8-5-6 で主張範囲を package-level へ限定したのに、この表の行が
「標的実歌唱が転移」「H-local / H-shared を支持」と**機構レベルの帰属を
再導入していた**。bit 一致の 8-R が消せるのは学習ドリフトだけで、
**標的素材と「収録セッション・語彙/分布の変化・baseline 9 row の除去」を
分離しない**。尺を揃えた非標的置換対照が無い限り、機構仮説は
`undetermined` のままである。

| run 8-B 結果 | 結論（**package-level**） |
|---|---|
| user と ritsu の**両方**が改善 | 置換パッケージの効果が**被処置話者を越えて共有側にも及んだ**。H-shared は **`undetermined`**（標的素材か・収録差か・baseline 除去かを分離していない） |
| **user だけ**改善・ritsu 不変 | 置換パッケージの効果が**被処置話者に留まった**。H-local は **`undetermined`**（同上） |
| **ritsu のみ**改善 | 効果が**被処置話者以外にだけ**出た = パッケージ由来と説明しにくい。**ドリフト残差か共有側の別経路**を疑う |
| **全話者不変** | **`undetermined`**（★ 2026-08-20 訂正: 初版は「実歌唱分数仮説を**棄却**」としていたが誤り = §9-0b）|
| `/ri/` だけ改善・`/su/` 不変 | **音素特異的**な効果。「新規収録なら何でも良かった」は説明力を失う（§8-5-6 の 2）。ただし**「有害 baseline row の除去」は否定しない**ので、機構の断定はしない |
| `/ri/` と `/su/` が改善 | **非特異的**な効果。標的音素対策としては `undetermined` |
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
**run 8-R の素材は「8-B と同一」ではなく「run 7 と同一」である**
（2026-08-20・P1 訂正）。初版は「8-B と同一契約・同一素材」と書いていたが、
そのとおり実装すると **8-R が標的パックを含み、対照が処置で汚染される**。
汚染された対照は**介入そのものをドリフトとして差し引き、効果なしと誤報する**。

```
run 8-R が使うもの: run 7 の**未改変**データセット（15 row そのまま）
run 8-B が使うもの: target 9 row + retained baseline 6 row

control manifest に両者の dataset digest を並べ、向きを両方 assert する:
  digest(8-R.dataset) == digest(run7.dataset)
  digest(8-B.dataset) != digest(run7.dataset)
```

run 8-R は run 7 の設定をそのまま再実行するだけなので、新規の設計判断はゼロで
実装コストも増えない。

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

**「user だけ改善して ritsu が変わらない」場合が実務上は最も示唆的**である。
ただし**それが示すのは「効果が被処置話者に留まった」までで、「技能が
speaker-local である」ことではない**（package-level の限定より）。
機構をここから先へ進めるには 2 つのうちどちらかが要る:

1. **尺を揃えた非標的置換対照**（§8-5-6 の 3・run 9 候補）— 「標的素材か
   収録差か」を分離する
2. **話者内で exposure を振る設計** — H-local を positive evidence にする
   唯一の道（§2-2）

いずれも本走行の射程外なので、run 9 の候補として §12 に残す。
早まって VG-L0 の speaker-independent Performance Skill / TRANSFER_SKILL へ
舵を切る根拠には**まだならない**。

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

### PR-2G（8-0G/0D/0E: native intervention harness・vNext 新設）

1. **アーム注入口**（§7G-0 P3）— `run_pipeline` の Stage 1 出力
   （`final_phone_dur`）/ Stage 2 出力（`pitch_pred`）/ 音素列 / 出力波形に対する
   **既定 off の差し込み口**。off のとき現行とバイト同一であることをテストで縛る
   （monkeypatch は使わない）。PR-2-2 の測定経路と同じ流儀・同じ差し込み方
2. **`s7_0g_arm_spec.json`**（§7G-0 P4）— アーム定義・ladder・primary level・
   対象セル（T / C の `cell_id` 全列挙）・判定代数を**1 レンダも走らせる前に**
   コミットし sha256 を pin する。§12-0-C2 と同じ運用規律
3. **B0 一致検査**（§7G-0 P2）— B0 アーム出力 wav sha256 が 8-0B の対応セルと
   一致することを assert（不一致は fail-closed で全結果無効）
4. **判定器**（§7G-5）— `machine_break` / A〜F 判定 / `nonspecific_response` /
   `duration_confounded` / EFFECTIVE_LEVER / primary lever 選定（§7G-6）を
   **純ロジックとして実装**し、worked example つきの形状テストを持たせる
   （B-1/B-2 の凍結値は入力として受け取り、**新しい metric を定義しない**）
5. **`s7_0g_listening_pair.json`**（§7G-7）— 2 問の提示ペアを聴取前に pin
6. **`s7_0e_pack_spec.json`**（§7G-9）— 8-0D / 8-0E の判定結果と収録発注仕様

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

### Run 8-0G / 8-0D / 8-0E（Gate 1 通過時のみ・vNext）

- [ ] §7G-0 の前提 P0–P4 が全て記帳されている（特に **B0 アームと 8-0B の
      wav sha256 一致**と、`s7_0g_arm_spec.json` が**校正済み結果を見る前に**
      pin されていること）
- [ ] 全アーム × 全対象セルの帰結が `rendered` / `dropped`（事前登録の理由コード）
      で記帳されている（8-0B の AC と同じ様式 — 「全部レンダされたこと」ではなく
      「全部の帰結が記帳されたこと」が達成条件）
- [ ] `D` の ladder 1.0 と `F` の ladder 0 が **B0 とバイト一致**している（no-op 検算）
- [ ] **因果アーム（D / F / S / S-frames-only）**の A〜F 判定が**個別に**記帳され、
      `EFFECTIVE_LEVER` / `NOT_EFFECTIVE` / `nonspecific_response` /
      `duration_confounded` / **`undetermined`（`ringing_uncorrected_group` 等の
      事前登録された理由コード付き）** のいずれかで確定している。
      **R-rescue は A〜F に参加せず**（§7G-4）、`rescue_confirmed` /
      `rescue_not_confirmed` で記帳される（機械値は `instrument_coupled` 付きの
      参考値）— fail-closed した走行が AC を満たせなくなる語彙にしない
- [ ] **新しい TRF metric を作っていない**こと（primary axis・θ・ε が B-1/B-2 の
      凍結値と一致することの機械照合）
- [ ] 複数成立時の primary lever が §7G-6 の 3 段規則で**再現可能に**決まっている
- [ ] §7G-7 の 2 問が**聴取前に pin された提示ペア**で行われ、
      `HUMAN_CONFIRMED` / `machine_effect_only` が記帳されている
- [ ] 8-0D の判定（`trainable` / `causal_but_not_trainable_yet` /
      `not_trainable_under_current_contract`）が T1–T3 の軸別に記帳されている
- [ ] `trainable` の場合のみ 8-0E の項目が全て閉じ、`s7_0e_pack_spec.json` が
      pin されている。**収録の発注はこの pin より後**
- [ ] **選ばれたレバーの形質受け入れ規則が `s7_0e_pack_spec.json` に凍結され、
      アラインメント後の target row で機械検証されている**（§7G-9）。
      規則を満たす row が 9 本に満たない場合は fail-closed で 8-B へ進まない
- [ ] 全アーム NOT_EFFECTIVE の場合、裁定が **`undetermined`** で記帳されている
      （「native レバーは存在しない」と書いていない = §7G-5 / §9-0b）
- [ ] GPU 費用 $0

### Run 8-B（Gate 1 + Gate 2 通過時のみ）

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
- [ ] **§6-2 の処置後ブラインド比較セットが、8-B の学習開始前に pin されている**
      （sha256 記帳。処置後にセルを選んでいないことの証跡）
- [ ] 観測子 + ブラインド A/B + 回帰対照（PJS・うみ・語中）の結果が出ている
- [ ] §9 の振り分け表のどの行に該当するかが裁定されている
- [ ] **run 8-R（未処置の同一契約反復）が実施され、まず run 7 との bit 一致が
      検査されている**（§7-1 段階 0）。一致しなかった場合は平均シフト `b_s` で
      補正し、`sigma_between` が推定できる k >= 2 を満たしているか、
      満たさないなら `provisional` である旨が記帳されている
- [ ] 8-R が無い、または k = 1 のまま因果を断定していないこと — §9 の全行を
      `confounded / provisional` で記帳した場合、**本 AC の「効果で終端宣言」は
      充足しない**（§9-0）
- [ ] **8-B が教育した形質が §7G-6 の primary lever と一致している**
      （8-0G が選んだレバーと違うものを教育していない）
- [ ] **効果で終端宣言**されている（「投入した」で終わらせない）。宣言文は
      §7G-11 の 1 文を超えない（単一教育介入・再現可能な改善）
- [ ] **Identity に言及する場合、§7G-12 の 3 経路のどれに該当するかが
      記帳されている**（`ε_id` は 8-B 開始前に凍結）:
      経路 1 = per (cell, axis) の全 pass -> **射程つき**の宣言文
      （「非標的終端文脈で測る限り」）/ 経路 2 = **測定が成立しない**場合のみ
      （未実測・計器不適用・8-R 無し）-> 縮退した宣言文 + `undetermined` /
      経路 3 = **実測して 1 つでも超過** -> `identity_not_preserved` を記帳し
      Identity 句を落とす。**超過を経路 2（undetermined）へ流さない**
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

#### 12-0-C2. **境界宣言 1 件 — B-1 候補空間の実名列挙は PR-1 で行う**

§12-0-A-2 の候補空間は `voicing: algorithm A / B`、`mel: FFT / hop / mel bins`
と**プレースホルダのまま**である。この状態では PR-1 が校正結果を見てから
候補を選び足しても「meta-contract に従った」と主張でき、凍結した measurement
spec と有料 Gate が再現不能になる — という指摘（Codex）は正しい。

**それでも本 memo では実名を書かない。** 理由は §12-0 冒頭と同じで、
どの voicing 実装・どの FFT 長が候補たりうるかは
`ANALYSIS_STACK_PIN` の実バージョンと実データを一度通さないと決められず、
紙の上で列挙すると「実在しない候補を凍結する」ことになる。

**代わりに凍結する手順**（これで抜け穴は塞がる）:

```
PR-1 の calibration harness 実装の**最初のコミット**で、
候補空間を実名・実数値で列挙した `s7_b1_candidate_space.json` を
**校正を 1 度も走らせる前に**コミットし、sha256 を pin する。

  voicing : 実装名 + バージョン（例: 依存 pin 上の関数名まで特定する）
  window  : ミリ秒の実数値を列挙
  hop     : 同上
  mel     : n_fft / hop_length / n_mels を実数値で列挙

以後この JSON へ**候補を追加しない**。追加が必要になった場合は
本 memo の改訂として扱い、追加した事実と理由を記帳する。
PR-2 開始 Gate の「B-1 候補空間 固定」はこの JSON の存在と pin で判定する。
```

**残る穴の正直な記述**: 「校正前にコミットする」という順序は**運用規律であって
機械強制ではない**。PR-1 でこれを CI で縛れるか（候補 JSON の commit 時刻が
校正成果物より前であることの検査）は実装時に判断する。

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

4. **F0 形質の教育データ符号化 mini-spec（vNext・OQ4）**: F アームが
   EFFECTIVE_LEVER になった場合、8-0D は既定で
   `causal_but_not_trainable_yet` を返す（§7G-8 の T1 が空）。8-B へ入れるには
   「note-relative contour を**収録の何で教えるか**」（発声指示 / 譜面側の
   表現 / アラインメント後の f0 分布のどれを教育対象と呼ぶか）と、
   「入ったことを dataset 組み立て時にどう機械検査するか」を別 memo で
   凍結する必要がある。**この mini-spec が無い状態で F を 8-B の設計へ
   流し込まない**

6. **標的文脈で identity と TRF を分離する計器（vNext・OQ6）**: §7G-12 経路 1 は
   非標的終端文脈でしか identity を測らないので、**標的 `/ri/` 文脈で identity
   だけが動いた場合を検出しない**。分離には「終端窓を除いたノート核」で測る
   変種（`identity_metrics._vowel_core` 相当）を B-1 と同じ様式で凍結する必要が
   あり、本走行の射程外（run 9 候補）。**それまで無条件の `Identity を変更せず`
   は名乗らない**

5. **8-0G の検出力（vNext・OQ5）**: target 群は 10 セルで、条件 C は 1 セルの
   flip で満たされる。**「効かない」を積極的に示す設計ではない**ので、全アーム
   NOT_EFFECTIVE は `undetermined` である（§7G-5）。同等性を主張したい場合は
   §9-0b と同じく Δ_eq を事前に置く必要があり、それは本走行の射程外（run 9 候補）
