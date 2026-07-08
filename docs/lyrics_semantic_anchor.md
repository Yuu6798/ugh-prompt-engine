# 歌詞＝意味層アンカー仮説：アレンジ・デモから浮上した計器の盲点

## この文書について

2026-07-01 の「楽譜アレンジ」デモセッションで**思いがけず浮上した本質的な発見**を
記録する。実 Suno 音源による「同一アレンジ × 歌詞あり / 歌詞なし」の対照を 2 曲分
行い、**ボーカル（歌詞）の有無が採譜器の読み値と、人間が感じる"曲らしさ"の双方に
効く**ことを観測した。結論を一行で言うと：

> **歌詞は物理層でなく意味層のアンカーであり、その効果は現行の物理計器にはほぼ
> 写らない。今のところ人間の耳が唯一のセンサーである。**

本文書はこの仮説と、そこへ至る過程で**一度過剰一般化して n=2 で訂正した経緯**
（honesty ゲートの実例）を保全する。法則化は n≥3 まで保留する。

関連: [`docs/genre_calibration_planning.md`](genre_calibration_planning.md)（意味層の
ジャンル語彙拡張）、[`docs/control_profile.md`](control_profile.md)（物理チャネルの
grip 自己記述）、[`docs/roundtrip_corpus_screen.md`](roundtrip_corpus_screen.md)
（BPM halving＝抽出器の癖）。

## セッションの経緯

「楽譜（CompositionScore）でどこまでアレンジできるか」のデモとして、以下を実走した。

1. Suno 音源を採譜 → key + コード進行を identity として保存 → ジャンル/テンポ/
   ダイナミクスを差し替えて EDM / ロック / J-pop へ再キャスト → `compose` で
   生成器プロンプト化。**「変えていい所（ジャンル/音色）／変えたら別曲になる所
   （調・和声）」を機械的に分離できる**ことを確認。
2. **実音源（非 Suno, m4a）** でも同じパイプラインが通ることを確認（後述の摩擦あり）。
3. その過程で、ユーザーが「同一 EDM アレンジ × 歌詞あり / 歌詞なし」の 2 版を生成。
   これが本質的な発見の入口になった。

## 検証データ

実 Suno 音源を `svprpe extract` した measured 値（小数 3 桁）。BPM は既定抽出値
（halving を含む）と高 prior 回復値を併記。

### 原曲（実音源）の identity（採譜）

| 曲 | 抽出 BPM（既定→高prior） | 抽出 key | 備考 |
|---|---|---|---|
| TOUGH BOY | 86 → 172.3 | C# major | 1986 年録音、halving |
| Song2 | 78 → 152.0 | G# major | 実音源、halving |

### 「同一 EDM アレンジ × 歌詞あり/なし」対照

| 曲 | 版 | 抽出BPM | 抽出key | mid_ratio | dynamic_range_db | harmonic_ratio | onset_density | sub_bass |
|---|---|---:|---|---:|---:|---:|---:|---:|
| TOUGH BOY | 歌詞なし(inst) | 172 | **G#**(属音) | 0.214 | 9.89 | 0.832 | 4.36 | 0.051 |
| TOUGH BOY | 歌詞あり(vocal) | **89**(半折) | **C#**(主音) | 0.310 | 6.03 | 0.814 | 5.32 | 0.037 |
| Song2 | 歌詞あり(vocal) | 143.6 | **D#**(属音) | 0.501 | 7.54 | 0.808 | 4.66 | 0.037 |
| Song2 | 歌詞なし(inst) | 152 | **G#**(主音) | 0.229 | 8.99 | 0.897 | 4.34 | 0.059 |

いずれも歌詞は**原曲の歌詞を参考に意味を寄せて**生成した（2 曲とも同条件）。

## 発見 1：ボーカル交絡は実在するが「方向」は一定でない

**再現したこと**：同一アレンジでも、ボーカルの有無で **key と BPM の読み値が変わる**。
2 曲とも歌あり版と歌なし版で別 key を返した。**採譜比較においてボーカルの有無は
統制すべき交絡変数**である（テンポを揃えたのと同じ規律で、歌詞条件も揃えるべき）。

**崩れたこと（n=1 の過剰一般化の訂正）**：

- TOUGH BOY だけを見た時点では「**ボーカルが正しい主音を錨、インストは属音へ
  ドリフト**」という綺麗な法則に見えた（歌あり→C# 正、歌なし→G# 属音）。
- Song2 で**方向が反転**した（歌あり→D# 属音、歌なし→G# 正）。よって
  「ボーカル＝主音の錨」は**法則ではなく n=1 の偶然**だった。実在するのは
  「ボーカルが key 読みを揺らす」という交絡そのものだけで、揺れる向きは不定。
- **BPM halving はボーカルの効果ではない**。TOUGH BOY は歌あり版が 89 へ半折り
  したが、Song2 はどちらも折れなかった。halving は曲の編成/リズム構造依存で、
  ボーカルの一般法則ではない（→ 下方修正）。

**最有力検出器（当時の n=2 評価）**：中域エネルギー（`mid_ratio`）はボーカル有無を
2/2 で分離した（歌あり 0.310 / 0.501 vs 歌なし 0.214 / 0.229）。歌声は中域に乗るため、
`mid_ratio` は「ボーカルの有無」の有力な物理検出子候補である。**ただし後述 n=3 追試で、
同じ「効果 > 再生成ノイズ」基準を適用すると noise 超えは Rock のみと限定される**
（EDM は directional のみ＝結論 2）。この節の 2/2 分離は「全面 validation」ではなく
**方向の一貫性**として読むこと。

## 発見 2（本質）：歌詞は意味層をアンカーし、その効果は物理計器に写らない

ユーザーの主観評価（耳）：

- **歌詞あり版はメリハリがあり「アレンジとして成立」していた。**
- **歌詞なし版は EDM さはあるが「曲に全然寄れていない」**（ジャンルの練習問題）。

ところが**物理計器は逆を測っている**。「メリハリ」があると感じられた歌あり版の
`dynamic_range_db` はむしろ**小さい**（Song2: 歌あり 7.54 < 歌なし 8.99、
TOUGH BOY: 歌あり 6.03 < 歌なし 9.89）。つまり：

> ユーザーが感じた「メリハリ」は**音量の起伏（物理センサーが測る量）ではない**。
> 旋律と歌詞が作る**構造的・意味的な起伏**（ヴァース↔サビの対比、感情の起承転結、
> トップラインが刻む段落感）であり、**現行の物理計器はこの層を見ていない**。

この乖離（主観◎ × 物理 dynamic_range が逆）こそが核心である。歌詞・旋律が入ると
曲は「ジャンルの器」から「構造と感情を持った"曲"」になる。この差は
**このプロジェクトが一貫して盲点と認めてきた意味層そのもの**であり、
**歌詞はその意味層を動かす最も強い操作ハンドル（ノブ）**である。

物理層のノブ（bpm / brightness は K2=#117 で tight grip 実証済み）に対し、
**歌詞＝意味層のノブ**。しかもその効果は今の計器では測れないため、
**現時点では人間の耳が唯一のセンサー**である。

### 「原曲らしさ」は歌詞条件だけでは決まらない

重要な nuance：2 曲とも歌詞を原曲参照で生成したにもかかわらず、

- TOUGH BOY は歌あり版が「原曲に寄っている」と感じられた。
- Song2 は歌あり/なしとも「原曲っぽさはなかった」（歌あり版は"アレンジらしさ"は
  出たが原曲その曲には寄らなかった）。

したがって「歌詞→意味層アンカー→曲らしさ／メリハリ」は 2/2 で支持されるが、
**「特定の原曲その曲に寄るか」は歌詞条件だけでは決まらない**（アレンジ距離・
和声の乖離など他要因が絡む）。仮説は二段構えで整理する：

1. **歌詞の有無** → 意味層が錨されるか（曲らしさ／メリハリ）。〔n=2 主観で支持〕
2. **歌詞内容が原曲由来か + アレンジ距離** → 特定の原曲に寄るか。〔未分離・要検証〕

## 発見 3（副次）：デモ中に露呈した計器の穴

- **genre ルールに "pop" 帯がない**：明るく中域主体の J-pop が
  `low_ratio<0.4 & mid_ratio≥0.5` で orchestral 側ルールに掛かり誤分類。
- **低 sub の EDM を rock と誤判定**：EDM 判別を brilliance / sub_bass の 2 軸のみで
  行うため、「深い sub を持たず dynamics で稼ぐ EDM」が rock 帯に落ちる
  （`sub_bass<0.052`）。深 sub 指定の EDM は正しく bass-music と分類でき、
  **プロンプトの一語（deep sub kick）が物理指紋を経て分類の正誤まで決めた**因果を確認。
- **halving は実音源でも再現**：抽出器のテンポ折りたたみは Suno 特有でなく普遍的な癖。
- **フォーマット**：`svprpe` は wav/mp3/flac のみ。実音源の m4a/AAC は変換前処理が要る。

これらは単一サンプルで閾値を動かさず、n≥3 のコーパスで校正線を引き直す
（Phase A の罠を避ける）方針を踏襲する。

## n=3 追試（2026-07-01 Session 2）：dynamic_range 説の棄却 + mid_ratio は Rock 限定

同一手法を **実音源 StartinA_COMPLETE(m4a)** に適用し、identity(調号/コア進行)を
固定して **EDM / Rock** へ再キャスト。各アレンジで歌詞あり/なしを生成し、加えて
**歌詞あり(present)条件については「別取り」(alt=再生成2テイク目)** を取った。これで
**歌詞側の再生成ノイズのベースライン**が得られる。**ただし instrumental(absent)条件は
両ジャンルとも 1 テイクのみで、absent 側の再生成スプレッドは未測**＝本デモは完全な
n≥2×2 セルではない（後述の結論はこの非対称を前提に読むこと）。measured 値の全量は
[`examples/real_audio_validation/lyrics_arrange_demo_2026-07-01.yaml`](../examples/real_audio_validation/lyrics_arrange_demo_2026-07-01.yaml)。

原曲 identity: tonal center C#(短調寄り, C#m ペダル) / BPM 107.7 / 4/4 /
コア進行 C#m→D→C#m→C#→C#m→G#→C#。

| take | 歌詞 | bpm | key | dynamic_range | mid_ratio | ♯4内 |
|---|---|---:|---|---:|---:|---:|
| EDM 歌詞あり | present | 129.2 | E | 7.43 | 0.226 | 100% |
| EDM 歌詞あり alt | present | 129.2 | E | 7.55 | 0.221 | 100% |
| EDM 歌詞なし | absent | 129.2 | E | **8.23** | 0.217 | 100% |
| Rock 歌詞あり | present | 107.7 | E | 6.70 | 0.245 | 93% |
| Rock 歌詞あり alt | present | 103.4 | E | **5.61** | 0.229 | 100% |
| Rock 歌詞なし | absent | 112.4 | C# | 5.75 | 0.208 | 61% |

### 結論 1：`dynamic_range` = 歌詞アンカー説は棄却

前節の「歌詞あり版は dynamic_range が小さい」を n=3 で検証し、**棄却**した。

- **EDM では方向は残るが validation は保留**：歌詞あり {7.43, 7.55} は歌詞なし 8.23
  より低く、条件差(0.74)は**歌詞側**スプレッド(0.12)を上回る。ただし **instrumental は
  1 テイクのみで absent 側の再生成スプレッドは未測**＝absent 側ノイズを排除できていない
  （mid_ratio に instrumental alt を要求したのと同じ非対称）。よって EDM の効果も
  **directional 止まり・instrumental alt 取得まで保留**とし、「生きた EDM proxy」とは
  断定しない。
- **Rock では反転かつノイズ未満**：歌詞あり {6.70, **5.61**} と歌詞なし 5.75 で、
  2 本目の歌詞ありテイクが instrumental より低い。効果はテイク間の揺れ幅(1.09)に埋もれる。
- したがって「歌詞→dynamic_range 低下」は **EDM 限定の交絡であり一般法則ではない**。
  前節の「n≥3 まで保留」判断が正しかったことを示す（law とせず棄却）。

**方法論的教訓**：`alt`(同一条件の別取り)で **within-condition 分散**を測らなければ、
条件差（歌詞あり/なし）が本物かノイズかを判定できない。BPM・歌詞に続き
**再生成そのものを統制対象**に加える。

### 結論 2：`mid_ratio` は最有力のボーカル検出子だが、ノイズ超えは Rock のみ

**歌詞ありテイク 4 本すべて**が、それぞれの instrumental より高い mid_ratio を示した
（EDM: 0.226/0.221 > 0.217、Rock: 0.245/0.229 > 0.208）。方向は 2 ジャンル × 再生成で
一貫する。ただし結論 1 と**同じ「効果 > 再生成ノイズ」の基準を一様適用**すると、
robustness が言えるのは **Rock だけ**である：

- **Rock**：条件差 0.021（0.229−0.208）> 歌詞あり内スプレッド 0.016（0.245−0.229）＝**ノイズ超え**。
- **EDM**：条件差わずか 0.004（0.221−0.217）< 歌詞あり内スプレッド 0.005（0.226−0.221）＝
  **ノイズ未満**。しかも instrumental の alt を取っていないため inst 側の分散は未測。EDM は
  「向きが合う」以上のことは言えない。

したがって「mid_ratio がボーカルを拾う」は **4/4 で方向一致する最有力候補**だが、
**唯一の頑健センサーと断定するのは EDM について過大主張**（Codex #124 P2 指摘を採用）。
`dynamic_range` を棄却したのと同じ規律で、mid_ratio も **Rock で noise 超え / EDM は
directional のみ**と限定する。昇格には **各ジャンルで instrumental の alt を含む n≥2×2
セル**を揃える必要がある（次回の生成バッチで instrumental も別取りする）。
（superseded: 2026-07-08 節参照 — instrumental alt 込み n≥2×2 セルで再検証した結果、
mid_ratio の昇格は両ジャンルとも棄却に確定した。）

### 結論 3（副次）：grip はチャネルで崩れ方が違う

- **BPM grip は「確度」と「精度」の 2 軸**：EDM は 108 指定を無視し 129 に**精密ロック**
  （四つ打ち prior=強アトラクタ、誤りだがブレない）／Rock は 108 中心に 103–112 で
  **揺れる**（弱 prior でノブに緩く追従、正確だが不精密）。grip は二値でなく
  「当たるか × 揃うか」で見る。K2(#117) の「bpm は prior アトラクタで圧縮」の生成側像。
- **調号は grip、進行は grip しない**：生成器の key 保存を示せるのは**生成6テイク**のみ
  （`orig` は原曲＝基準で分母に数えない）。**生成6中5**が ♯4(E major / C# minor)ファミリー
  内 93–100%＝**転調なし・音の集合は保存**（残る 1 本 rock_inst は次項）。一方トニック
  重心(top_chord)は B/E/Em/C# に散り、原曲の C#m ペダルを再現したテイクは皆無＝
  **「同じ調号でハーモニーは毎回書き直し」**。
- **外れ値 rock_inst(歌詞なし)のみ調号逸脱(61%)**：A#m/Fm が混入。歌詞なしが identity
  最弱という仮説にはなるが、**歪みギター instrumental のクロマ推定ノイズの可能性が大きく
  n=1 では断定しない**（Phase A の罠を踏まない）。

## 相互検証①: CLAP vocal contrast × mid_ratio（2026-07-02、committed データのみ）

PR2b の設計意図「学習版 grip をルール版と相互検証する（置き換えない）」の第一段。
**新規生成・新規推論ゼロ** — #131 の CLAP fixture
（`examples/learned/clap/lyrics_vocal_contrast_fixture.json`）と本書 n=3 追試の計測ログ
（`examples/real_audio_validation/lyrics_arrange_demo_2026-07-01.yaml`）を
**audio_sha256 で突き合わせ**（6/6 テイク完全リンク＝同一バイト列を 2 系統のセンサーで
測った比較であることが暗号学的に保証される）。

| take | 条件 | mid_ratio | CLAP contrast_fit |
|---|---|---:|---:|
| edm_lyrics | present | 0.226 | +0.2475 |
| edm_lyrics_alt | present_alt | 0.221 | +0.2550 |
| edm_inst | absent | 0.217 | +0.1341 |
| rock_lyrics | present | 0.245 | +0.2581 |
| rock_lyrics_alt | present_alt | 0.229 | +0.2324 |
| rock_inst | absent | 0.208 | −0.0487 |

**一致（consistency）**: 条件レベルの方向は両センサーで完全一致 — 2 ジャンルとも
present > absent、6 テイクの並びでも inst 2 本を両者が最下位に置く。
**2 本の独立なセンサーが同じ現実（ボーカルの有無という潜在因子）を指している**ことの確認。

**感度差（sensitivity）**: 「効果 > 再生成ノイズ」を n=3 追試と同じ保守的規約
（条件差 = present 2 値の小さい方 − absent、ノイズ = present 2 値のスプレッド）で計算:

| | mid_ratio 効果/ノイズ | CLAP 効果/ノイズ |
|---|---|---|
| EDM | 0.004 / 0.005 = **0.8×（基準未達）** | 0.113 / 0.008 = **15.1×** |
| Rock | 0.021 / 0.016 = **1.3×（辛勝）** | 0.281 / 0.026 = **10.9×** |

読み: mid_ratio が「方向は合うが EDM でノイズに埋もれる」センサーだったのに対し、
CLAP は両ジャンルで基準を大差で満たす。**意味層の読解はルール物理量の間接プロキシ
より学習センサーの直接読みが桁で有利**、が committed データだけで言えた。

**honesty / 限界**: (a) n=6・absent 側 alt 未取得の非対称は両センサー共通の留保
（完全対称化は Suno 追加生成＝人手律速）。(b) within-condition の順序（present と
present_alt のどちらが高いか）は両センサーで**一致しない** — ノイズ帯域内の順序に
意味はなく、主張は条件レベルの方向までに限定する。(c) 相関係数などの統計量は n=6 では
主張しない（方向の一致確認であって statistical validation ではない）。
本一致は `tests/test_clap_similarity.py` の cross-consistency テストで pin し、
どちらかの committed データが変わったら再検証を強制する。

## プロジェクトへの含意

- **意味層センサー導入の実データ根拠**：「物理計器に写らないが人間の耳は捉える差」を
  実データ × 主観の食い違いで裏付けた。CLAP 等の意味層読解器（`ai_performer_score_roadmap.md`
  の PR2b、現状 policy 外）を入れる動機がここにある。
- **PR2b-2 実 fixture**：`examples/learned/clap/lyrics_vocal_contrast_fixture.json`
  に、StartinA EDM/Rock の歌詞あり・歌詞なし 6 テイクで CLAP vocal contrast を採取した。
  小標本の方向観測であり、verdict ではない。サンプルごとの `audio_embedding` は
  `lyrics_vocal_contrast_fixture.embeddings.json` サイドカーに退避済み（provenance 保持、
  本体 fixture は `cosines`/`contrast_fit` を pin）。
- **control_profile への lyrics チャネル案**：楽譜が「歌詞の有無で効き方が変わる」ことを
  自己記述できるよう、`control_profile` に意味層ノブ（例 `lyrics_presence`）を足す議論の入口。
  → SEM-1 で実装済み（[`control_profile.md`](control_profile.md) 参照）。
- **比較実験の統制項目**：BPM に加え**歌詞条件も matched-pair の統制対象**に加える。

## 仮説の検証デザイン（n≥3 への昇格計画）

同一アレンジに対し 3 条件を生成し、人間評価と物理計器の乖離を記録する：

| 条件 | 歌詞 | 期待 |
|---|---|---|
| A | なし | 意味層アンカー無し＝「曲らしさ」低・原曲寄り低 |
| B | 原曲無関係の歌詞 | 意味層は錨されるが原曲その曲には寄らない |
| C | 原曲由来の歌詞 | 意味層が原曲の意味に錨＝原曲寄り高 |

評価軸：①「曲らしさ／メリハリ」②「原曲寄り度」（人間）、③物理指紋の乖離。
**③の代理指標は `dynamic_range_db` を使わない**（n=3 追試で歌詞アンカー proxy として
棄却済＝結論 1）。候補は `mid_ratio`（ボーカル検出は Rock で noise 超え／各ジャンルで
instrumental alt を含む n≥2×2 セルで昇格）と、未探索の意味層代理（旋律/段落構造を
拾う特徴、将来は CLAP=PR2b 等の学習センサー）。各セルで instrumental の別取り(alt)を
取り、条件差が再生成ノイズを超えるかを **3 曲以上**で確認してから法則化する。

## honesty / 未解決

- **法則化保留**：歌詞→意味層アンカーは n=2 の主観で支持されるが、物理計器で
  定量化できていないため law とはしない。「ボーカル＝主音の錨」「ボーカル＝halving
  誘発」は n=1 の過剰一般化として**明示的に棄却**する。
- **`dynamic_range`→歌詞 の棄却（n=3 で確定）**：EDM で方向は残るが Rock で反転し、
  かつ Rock では再生成ノイズ未満（2026-07-01 Session 2）。EDM の方向も absent 側
  instrumental alt 未取得で directional 止まり（結論 1）。dynamic_range を歌詞アンカーの
  物理代理指標にはできない。ボーカルの最有力検出子は **mid_ratio** だが、同じノイズ基準では
  **Rock でのみ noise 超え・EDM は directional のみ**（instrumental alt 未取得）＝
  「唯一の頑健センサー」とは断定せず、n≥2×2 セルでの昇格を要件とする（結論 2）。
- **コード進行の測定は推定器律速**：「進行が保存されない」は Suno の再ハーモナイズと
  コード推定器の分散が混在し分離不能。信頼できるのは粗い**調号ファミリーテスト**まで。
- **唯一のセンサーが人間**：発見 2 の核（メリハリ＝意味層）は現状 objective に
  測れない。意味層センサー導入まで主観記録で保全する。
- **原曲寄り度の交絡未分離**：歌詞内容とアレンジ距離を分離する対照（検証デザイン
  B/C）が未実施。

## 2026-07-08 対称ブロック（CLAP ③ closeout）

結論 2（mid_ratio 昇格は Rock のみノイズ超え）の未解決課題だった
「各ジャンルで instrumental の alt を含む n≥2×2 セル」を、#124 とは**別プロンプトの
独立ブロック（v2）**で埋めた。本節でこの追試を確定させ、`lyrics_presence` 昇格判定を
closeout する。

### ブロック設計

- EDM/Rock 各ジャンル内で Style / Exclude Styles を完全共通化し、差は Instrumental
  トグルと歌詞欄のみ（matched-pair）。各条件（歌詞 present / absent）は 2 テイク
  （1 本目 + 再生成 alt）＝ instrumental 側も alt を初取得し、n≥2×2 セルを充足。
- プロンプト pin（発行文面のまま）：
  - EDM Style: "Melodic EDM, 129 BPM, E major, four-on-the-floor kick, bright supersaw
    leads, sidechained sub bass, energetic drop, steady tempo, no key change" /
    Exclude: "orchestral, acoustic, rock guitar, tempo change"
  - Rock Style: "Anthemic rock, 108 BPM, E major, driving electric guitars, live drums,
    punchy bass, verse-chorus dynamics, steady tempo, no key change" /
    Exclude: "EDM, electronic dance, orchestral, tempo change"
  - 歌詞: StartinA の歌詞を present 4 テイクに同一貼付。採用規則: 各条件 1 本目機械採用。
- measured 値の全量は
  [`examples/real_audio_validation/lyrics_symmetric_block_2026-07-08.yaml`](../examples/real_audio_validation/lyrics_symmetric_block_2026-07-08.yaml)、
  CLAP fixture は
  [`examples/learned/clap/lyrics_vocal_contrast_v2_fixture.json`](../examples/learned/clap/lyrics_vocal_contrast_v2_fixture.json)。

### 事前登録規約

法則化の恣意的な閾値選びを避けるため、計測前に判定規約を固定した:

- **効果** = 条件間の最近差 = min(present 側の値) − max(absent 側の値)
- **ノイズ** = max(present スプレッド, absent スプレッド)
- 効果 > ノイズ のときのみ「昇格」とする（n=3 追試・相互検証①と同じ保守的規約の踏襲）。

### 判定表

| センサー | ジャンル | 効果 | ノイズ | 判定 |
|---|---|---:|---:|---|
| `mid_ratio` | EDM | 0.010 | 0.025 | ✗ 棄却 |
| `mid_ratio` | Rock | 0.017 | 0.019 | ✗ 棄却 |
| CLAP vocal contrast | EDM | 0.136 | 0.064 | ✓ 充足（2.1×） |
| CLAP vocal contrast | Rock | 0.155 | 0.083 | ✓ 充足（1.9×） |

### mid_ratio 昇格は棄却で確定

n=3 追試（#124）で「Rock のみノイズ超え（辛勝 1.3×）」としていた判定は、**instrumental
側の再生成スプレッドを未計測のまま absent 側ノイズをゼロ扱いした過大評価**だった。
本ブロックで absent 側 alt を取得し実測した結果、Rock の absent スプレッド
（|0.230 − 0.211| = 0.019）が効果（0.017）を上回り、**Rock も含め両ジャンルで
mid_ratio の昇格は棄却**に確定する。EDM も同様に棄却（効果 0.010 < ノイズ 0.025）。
`mid_ratio` はボーカル有無の**方向一致（4/4 present > absent）**は保つが、
再生成ノイズを安定して超える頑健センサーではない、という #124 の限定的な結論が
n≥2×2 セルでも覆らなかった。

### CLAP vocal contrast は両ジャンルで充足を確定

CLAP vocal contrast は本ブロック（v2, 4本×2ジャンル）でも #131 の初期観測
（`lyrics_vocal_contrast_fixture.json`）と同方向・同オーダーで効果がノイズを
大きく上回った。present 側の contrast_fit は新旧・両ジャンル横断で
**0.23–0.29**（精密には 0.232–0.285）に安定して収まり、absent 側は本ブロックで
0.009–0.117 まで広く分布するが、present の最小値が absent の最大値を常に上回る
（完全分離）。これにより、`docs/control_profile.md` DD-4 のセンサー定義を
CLAP vocal contrast へ改訂する（後述）。

### honesty

- byte_size 弁別は実効ビットレートがほぼ一定のため実質 duration 弁別であり、
  present/absent 割り当ての確定根拠は byte_size ではなく CLAP vocal contrast の
  完全分離である。
- Rock の instrumental 2 本（69.28s / 30.16s）は歌詞側（~183s）より大幅に短く、
  duration 交絡は本ブロックでも残置（解消していない）。
- `edm_inst_v2_alt` の bpm=89.1 は他 3 本（136.0）に対し比 1.526 で R2-2f
  （[`roundtrip_corpus_screen.md`](roundtrip_corpus_screen.md)）の 3:2 窓内だが、
  `bpm_prior_disagreement` は非発火（false）— halving か真に遅い生成かは未裁定。
  本判定には使わない副次観測として記録する。
- 事前登録規約と判定値は上記のとおり転記であり、本節で再計算・改変していない。

**昇格実施（2026-07-08 追記）**: 昇格ゲート条件 2（K3 ジャンル干渉分離）の formal 判定も
CLAP vocal contrast で充足（両歌詞条件で干渉 < ノイズ・歌詞効果 > 干渉。計算値と事前登録
規約は [`control_profile.md`](control_profile.md) DD-4）。両条件充足により
`lyrics_presence` の loose→tight 昇格を config（suno device profile / midnight_signal）へ反映した。
