# L0a v2 再測記録 — 開示のみ改訂 × BR-D1 contract_defect の再測

**Status**: 完了（2026-08-07 実測）。一次資料 = `examples/l0b_loop/contract_v2.md`
（v2 契約本体）・`examples/l0b_loop/battery_v2/series/br_d1_s1/` /
`br_d1_s2/`（各周回の score.yaml / intent.yaml / report.json / pareto 比較
json）。設計判定の正本 = 設計側（Fable）の判定記録（本 doc の解釈はすべて
この判定に従い、それを超える一般化は行わない）。前段記録 =
[`l0br_robustness_record.md`](l0br_robustness_record.md)（第 1 バッテリー、
本再測の動機となった `contract_defect` の一次観測）・
[`l0b_closed_loop_record.md`](l0b_closed_loop_record.md)（v2 開示項目の一次
実測元）。

## 1. 目的と設計

`l0br_robustness_record.md` は BR-D1 系列 s1（`br_d1_s1`）を 5 周回すべて
structure mismatch のまま未収束と記録し、`failure_mode: contract_defect`
（未開示のヒント解決規則——非加算・優先順位制）に分類した。本再測は、この
`contract_defect` が「開示のみの契約改訂」で解消するかどうかを実測することを
唯一の目的とする。

v2 契約（`examples/l0b_loop/contract_v2.md`）は v1（`examples/l0b_loop/contract.md`、
凍結・変更禁止）に対し、`l0b_closed_loop_record.md` §4 と `l0br_robustness_record.md`
§4 で実測済みの未開示計器特性 6 項目の開示のみを追加する:

1. 演奏ヒント解決の非加算・優先順位制（`"no kick"` 系統の支配、
   `"release"`/`"full energy"` の等価写像）
2. 認識語の照合対象が `physical` 単独ではなく `physical`+`role` の連結文字列
   であること
3. `chord_progression` はドローン扱いのセクションでは鳴らないこと
4. 自作 `chord_progression` が `physical.key` 宣言より優勢に調推定を決めうる
   こと、および 2 種の修理経路
5. 低→高エネルギー遷移直後に幅約 5 秒の過渡帯が生じ `bridge` として観測され
   うること
6. 終盤境界に約 2.6–2.7 秒の系統的早期検出があること

スキーマ（§1）・判定器（`run_round.py` / `pareto_eval.py`）・演奏者・
`AuthoringDiffReport` 正規形（§3）はいずれも v1 から変更しない。同一性の
束縛は二層で行う: 判定器 4 pin（run_round / pareto_eval / section_map_t2 /
pareto_spec）が v1 台帳と同一 sha であることは機械証明の対象（cross-ledger
テストで enforce）、`run_round.py` が import する実行エンジン閉包
（src/svp_rpe の演奏者・抽出器と config/）は台帳の
`src_config_diff_attestation`（base = #249 マージ後 main、実測全期間で
diff empty）による git 系譜 attestation で束縛する（squash / export 文脈
では証言へ格下げ——台帳ヘッダの境界宣言と同じ規約）。これにより
**統制下にある変数**を契約の開示内容のみに限定する分離設計が成立する。
ただしこの分離が束縛するのは計測器側（判定器・エンジン・課題・成功述語）
であり、著者は同一ポリシーの確率的抽出で個体サンプルがバッチ間で異なる
——v1 との到達率比較は非対応比較であり、著者サンプリング揺らぎとの交絡は
残る（§3 の限定と台帳 `result.judgement_note` が正）。

事前登録仮説（台帳 `preregistered_hypothesis`）: 「v1 の BR-D1 到達率は 1/2
（s1 が contract_defect、s2 は reached・rounds_to_success=1）。v2 で 2/2 に
なれば、開示が defect を解消したことの支持となる」。運用は 2 段 commit
（登録 → 実測）の規律を維持し、実測前に仮説・課題・契約を確定した。

## 2. 実測結果

2 系列（`l0br_v2_br_d1_s1` / `l0br_v2_br_d1_s2`）× 各最大 5 周回で実行し、
**2/2 系列が reached、いずれも rounds_to_success = 3** だった。

### 2.1 系列 s1（br_d1_s1）

| 周回 | structure observed | verdict | position_match_rate | pareto improved |
|---|---|---|---|---|
| round1 | [intro, chorus, chorus, bridge, outro] | mismatch | 0.6 | — (round1) |
| round2 | [intro, chorus, chorus, bridge, outro] | mismatch | 0.6 | false（structure delta=0） |
| round3 | [intro, chorus, chorus, outro] | **exact_match** | **1.0** | **true**（structure delta=-1） |

- key・brightness は round1〜round3 の全周回で `preserved`（D minor / dark）。
- 境界秒（round1・round2、曲総長 30.0s）: intro 0.0–6.5945 / chorus
  6.5945–12.2137 / chorus 12.2137–17.8329 / bridge 17.8329–23.4289
  （round1）/ 23.4522（round2）/ outro –30.0。round2 で chorus1 が
  5.6192s→5.0155s に短縮、chorus2 が 5.6192s→6.2229s に伸長（bridge 混入
  自体は解消せず）。
- round3（曲総長 30.0s→32.5s、chorus2 bars 3→4 延伸）の境界秒: intro
  0.0–6.6177 / chorus 6.6177–12.2601 / chorus 12.2601–25.8902 / outro
  25.8902–32.5。bridge は出現せず 4 要素のみ観測。
- 解消レバー: **chorus2 の bars を 3→4 に延伸**（曲総長 30.0s→32.5s）。
  著者の「境界スメア仮説」（round1/round2 で宣言境界 15.0s が
  11.61s/17.83s の 2 候補にスメアしていたとの解釈）に基づく単変数変更。
- failure_mode: なし（reached のため適用外）。off_contract_events: 0 件。

### 2.2 系列 s2（br_d1_s2）

| 周回 | structure observed | verdict | position_match_rate | pareto improved |
|---|---|---|---|---|
| round1 | [intro, chorus, chorus, bridge, outro] | mismatch | 0.6 | — (round1) |
| round2 | [intro, chorus, chorus, bridge, outro] | mismatch | 0.6 | false（structure delta=0、境界秒が round1 と byte 同一） |
| round3 | [intro, chorus, chorus, outro] | **exact_match** | **1.0** | **true**（structure delta=-1） |

- key・brightness は round1〜round3 の全周回で `preserved`（D minor / dark）。
- 境界秒（round1・round2、曲総長 30.0s、両周回で完全同一）: intro
  0.0–6.5945 / chorus 6.5945–12.2137 / chorus 12.2137–17.8329 / bridge
  17.8329–23.4522 / outro –30.0。round2 の outro tier3→tier5 変更は
  observed_sections に一切影響しなかった（byte 同一）。
- round3（曲総長 30.0s 不変、outro tier5→tier2/ドローン化）の境界秒: intro
  0.0–6.5945 / chorus 6.5945–12.2137 / chorus 12.2137–17.2989 / outro
  17.2989–30.0。bridge は出現せず 4 要素のまま観測、chorus1/chorus2 の
  併合退行（著者が事前予告したリスク）も顕在化しなかった。
- 解消レバー: **outro の physical tier5（既定）→tier2（"low density" による
  ドローン化）**（曲総長不変）。著者は round2 の「tier 順の音量ギャップ」
  仮説を棄却し、round3 で「パルス有無」という質的軸へ転換した。
- failure_mode: なし。off_contract_events: 0 件。

### 2.3 両系列の解消レバーの違い

同一症状（chorus2/outro 境界付近での `bridge` 過剰検出、5 要素観測 vs
要求 4 要素）に対し、両系列は異なる修理経路で reached に到達した:
s1 は直前セクション（chorus2）の bars 延伸による境界の時間的分離、s2 は
終端セクション（outro）のドローン化によるパルス有無の質的断絶。いずれも
著者が単変数変更として設計・自己申告している。

## 3. 事前登録仮説の判定

**判定: supports**（v1 の br_d1 到達率 1/2 → v2 で 2/2 reached）。

正直会計（事前登録どおり明記）:

- n=2 であり、著者サンプリング揺らぎとの完全な分離はできない。
- rounds_to_success は両系列とも 3 で、v1 s2 の 1 より遅い。**観測上、
  到達可否は 1/2→2/2 に転じたが、構造軸の収束速度は改善していない**
  （この到達率差を開示の孤立効果と読まないこと——因果帰属は下記のとおり
  交絡している）。収束が遅かった理由は §4 に記す残存未開示特性（高→低
  遷移側の bridge 過剰検出）に構造軸の失敗が集中したためである。
- v1 契約欠陥（key 転倒・ドローン無音化等）は v2 開示により**全著者・全
  周回で初回から回避された**: 両系列とも key/brightness は round1 から
  `preserved` であり、著者はいずれも events（自作 chord_progression）省略、
  ドローン語彙（"no kick"/"low density" 等）を chorus では使わない設計、
  日本語の role 文による部分文字列安全化（役割語を認識語と重ねない書き方）
  を、v2 §2 の該当開示を明示的に参照した上で選択していた。開示チャネルの
  機構的証拠（著者が開示を読んで回避を選択した事実）としてはバッチ内で
  直接観測されている——ただし到達率差（1/2→2/2）そのものの因果帰属は、
  バッチ間で著者個体サンプルが異なる非対応比較（モデルスナップショット・
  サンプリング設定不記録、v1 自体が 1/2 の著者間分散）のため著者揺らぎと
  交絡したままである（`supports` は事前登録された観測分岐のラベルであり
  因果の証明ではない——台帳 `result.judgement_note` と同一の限定）。

## 4. 新規計器特性（v3 開示候補）

1. **高→低遷移側の bridge 過剰検出**（v3 開示候補 #1・最重要。契約 v2 の
   非対称性節への erratum を含む）: v2 §2 は「低→高遷移直後の過渡帯が
   bridge 化、逆方向（強→弱）では同様の過剰検出は観測されていない——
   立ち上がり側と立ち下がり側で非対称である」と開示していたが、本再測では
   chorus(tier4) → outro(弱) の**高→低**境界前後で bridge が独立設計の
   2 系列 × 計 4 周回（s1 round1/round2、s2 round1/round2）にわたり再現
   した。この契約文は二つの主張に分解される: 前段「観測されていない」は
   起草時点の実測報告として正確だったが、後段「非対称である」は観測報告を
   超えた規則の断定であり、**本実測により反証された（erratum）**。
   contract_v2.md は実測時に著者へ配送された pin 済み計測器（sha256
   f224d360…）のため本文は凍結のまま変更しない——本節がその正誤表であり、
   v3 契約では非対称性の断定を撤去し方向非依存の「実測点」形式へ是正する
   ことを義務とする。両著者は契約の一般則より自己実測を優先する推論で
   この記述矛盾を自力で乗り越えた（§4-3）。修理経路は 2 種が独立に実証
   された: (a) 直前セクションの bars 延伸による境界の時間的分離（s1）、
   (b) 終端セクションのドローン化によるパルス有無の質的断絶（s2）。
2. **終端セクションの tier3↔tier5 無差別帯**（v3 開示候補 #2。実測点で
   あり一般則ではない）: s2 round2 で outro を tier3(sparse)→tier5(既定)
   に変更しても、observed_sections が round1 と byte 同一だった。一方
   round3 の tier5→tier2 変更は検出を動かした。実測が確立するのはこの
   score/構成（bpm=128・4/4・4 セクション・chorus×2 full energy 直後の
   終端位置）における当該 tier ペアの挙動のみであり、「終端位置の
   tier3/tier5 差は常に観測不能」「tier2 以下でのみ検出が変化する」と
   いう一般則までは支持しない——v3 開示に載せる場合は他構成への掃引で
   帯域を確定してから、契約の可行窓開示と同じ「実測点」形式で書くこと。
3. **著者の仮説形成の質**（記録価値。開示候補ではない）: 両著者が独立に
   契約開示と自己実測の食い違いを認識し、契約を「観測の下限」として扱い
   自己実測を優先する推論を行った（s2 著者は v2 §2 の非対称性記述と round1
   実測の食い違いを明記した上で自己実測を優先すると宣言）。単変数変更に
   よる切り分け設計も両系列で維持された。

## 5. 運用ノート

- **分業**: 著者 spawn はセッション本体（設計側）が実施し、本再測の
  コーディネーターは judge 実行・組成・evidence 保存・facts 記録を担当した
  （役割分離は分業確定前の制約——コーディネーター側に個体 spawn 用ツールが
  なかったこと——への対応として確定）。
- **spawn 逐語性の機械検証手順**: s1 round2 / s2 round1 では、配送プロンプト
  を task JSONL から抽出し `author_prompt.txt` と機械照合（strip 後バイト
  同一、差分は末尾改行 1 文字のみ）して逐語配送を確認した。この手順（spawn
  直後に JSONL vs `author_prompt.txt` を機械照合）を以降の標準手順とした。
  転写破損による**spawn 前中断が 2 件**発生している——s1 round3 の初回 spawn
  （§3「スキーマが固定され」→「スキーマApril固定され」の 1 箇所破損）と
  s2 round3 の初回 spawn（task.md「異名同音」→「異名異音」の 1 箇所破損）。
  いずれも応答生成前（assistant メッセージ 0 件）に kill しており、応答を
  一切消費していないため off-contract イベントには該当しない。修正適用後の
  全文一致で破損が当該 1 箇所のみであることを両件とも機械確認済み。
- **intent.yaml の YAML 構文事故 2 件と周回計上の裁定**: s1 round2 と
  s2 round3 の intent.yaml は単体では有効な YAML としてパースできなかった
  （`yaml.safe_load` がそれぞれ line 25 col 22 / line 12 col 36 で
  `ParserError` を送出）。両系列とも `score.yaml` は全周回で単体有効な
  YAML だった（保存はフェンス内容そのまま、修正なし）。
  **裁定（設計判定）**: この 2 周回（成功周回 s2 round3 を含む）は到達
  会計にそのまま計上する。理由: (a) 事前登録した成功述語（symbolic pass
  ∧ key preserved ∧ brightness preserved ∧ structure exact_match）と
  off-contract 規約（情報境界違反 = ツール使用・ペイロード外情報）の
  いずれにも intent sidecar の機械 parse 可能性は含まれておらず、実測後に
  これを除外基準へ追加することこそが事後的な判定基準変更にあたる。
  (b) 構文事故は失敗周回（s1 round2）と成功周回（s2 round3）の双方で
  発生しており、結果を特定方向へ形成していない。(c) intent.yaml は契約
  §0 で「自由形式 YAML・エンジン非消費・人間参照専用」と宣言されており、
  D2 情報境界の監査目的（著者推論の可読性）は損なわれていない。
  **L0a v3 契約材料**: 契約 §0 の「YAML 文書」という文言と自由形式運用の
  間の緊張は v3 で解消すべき明確化課題である——intent に機械 parse 可能な
  YAML を要求して周回受付時に検証するか、明示的に自由文へ格下げするかの
  二択を v3 設計時に裁定する。
- 加えて、`tools_used: none` 宣言の出現位置（フェンス内 / フェンス外 /
  両方）が周回・著者によりばらついたが、フェンス抽出・保存には影響しな
  かった（正規表現抽出が最初にマッチしたフェンスのみを対象とするため）。

## 6. 次の一手

§4 の v3 開示候補 2 件（高→低遷移側 bridge・終端 tier3↔tier5 無差別帯)の
契約反映は、次バッテリーの設計判断として持ち越す。本再測では契約凍結
（v2 のまま変更しない）を維持し、v3 への改訂は行わない。v3 で義務となる
是正は §4-1 の erratum（非対称性の断定の撤去）であり、v2 を単独仕様として
参照する後続著者は §4-1 の正誤表を併読すること。
