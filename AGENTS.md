# AGENTS.md — Codex × Claude 連絡プロトコル

このリポジトリは **設計レビューと実装を分業** する。Claude Code（Brief 読解 / 設計メモ
起案 / PR レビュー担当）と Codex（実装 / PR 作成 / 指摘対応担当）が共有する
メッセージ・フォーマット規約を本ファイルで定める。役割分担・運用ポリシーの詳細は
[`CLAUDE.md`](CLAUDE.md) の Workflow 節を参照。

**2026-06-02 改訂**: ワークフローを **Claude Code が設計、Codex が実装** に戻す。
GitHub 移行後の通常開発では、Claude Code が仕様整理・設計レビューを担当し、Codex が
ローカル実装・検証・PR 作成・指摘対応を担当する方針にする。

両エージェントとも作業開始時に本ファイルを読むこと。

---

## メッセージフロー

```
Task Brief
  │
  ▼
Claude Code ─[Design Memo]→ User ─[paste]→ Codex ─[実装 + PR]→ GitHub
                                                                │
                                                                ▼
                                      Codex ←[再レビュー]── Claude Code ←[PR URL]─ User
                                          │
                                          ▼
                              指摘対応コミット ──→ Claude Code 再レビュー → User マージ
```

ループは User がトリガーする。Codex/Claude は各々のフォーマットで出力を出すだけで、
エージェント間で直接通信しない（Claude Code が PR レビューコメントを残す経路は GitHub
内なので User の橋渡し不要）。

---

## 1. Design Memo（Claude Code → Codex）

Claude Code が Task Brief を読んで Codex に渡す設計メモの固定フォーマット。
コピー&ペーストで Codex に渡せる単位にすること（タスク粒度は 0.5–2 日で
完結する範囲）。

````markdown
# Design Memo: <ID> — <短いタイトル>

## Phase
<roadmap_goal1.md の Q-ID または該当する設計参照>

## Goal
<1–2 文で「何を達成すれば完了か」>

## Acceptance Criteria
- [ ] 検証可能な条件 1
- [ ] 検証可能な条件 2

## Implementation Approach
<推奨実装方針、データフロー、既存パターン参照、API 設計>

## Risks
<実装で詰まりやすいポイント、後方互換破壊の可能性、性能リスク等>

## Test Strategy
- 単体テスト観点: <網羅すべきブランチ / エッジケース>
- 回帰テスト観点: <pin すべき契約 / 過去 defect の再発防止>
- 既存テストへの影響: <スナップショット更新の要否等>

## Scope
- IN: <変更してよいファイル / モジュール>
- OUT: <変更してはならないもの>

## Schema Admission（該当時）
<CompositionScore / PhysicalLayer に新フィールドを追加する場合、その欄の fixity（locked 可能か）と往復一致の実測 or 実測計画を記載>

## Allowed Dependencies (任意)
<本タスクで pyproject.toml への追加を許可する依存。例: `mir_eval>=0.7`>
<記載がない場合、新規依存追加は escalation 対象>

## Required Outputs
- ブランチ名: `codex/<topic>`
- PR タイトル: <Conventional Commits 形式>
- 期待する変更ファイル: <列挙>

## Done When
- 上記 Acceptance Criteria が全て ✓
- CI green（`ruff check .` + `pytest -q`）
- PR 本文が Completion Summary 規約に準拠（CLAUDE.md の PR 本文必須記述参照）
````

---

## 2. Completion Summary（Codex → Claude Code / User）

Codex が PR を作成する際、PR 本文の冒頭を以下フォーマットで記述する。

````markdown
# Completion Summary: <Task ID>

## Phase
<Design Memo の Phase ID をそのまま転記>

## What Changed
- <高レベルの変更点 3–5 行>

## Acceptance Criteria Status
- [x] 条件 1 — <根拠 / 該当コミット SHA>
- [x] 条件 2 — <根拠>
- [ ] 条件 3 — <未達成の場合は理由>

## Tests
- 追加: <テスト名 / 件数>
- 実行結果: <pass / fail / skip 件数>

## Self-Review
<Codex 自身が実装後にチェックした観点 3–5 件。
 Claude Code レビューに先んじて defect を捕捉する目的>

## Files Changed
<git diff --stat 相当>

## Deviations from Memo
<Design Memo から逸脱した点。なければ "None">

## Open Questions / Deferred
<Claude Code / User が判断すべき事項、または次フェーズへの持ち越し>

## Review Focus
<Claude Code に重点的に見てほしい観点>
````

---

## 3. PR Review（Claude Code → Codex）

Claude Code は PR 本体の Completion Summary を読み、GitHub PR のレビュー機能で
コメントを残す。レビューコメントの粒度は inline コメント（行指定）優先、
全体総括が必要なら Review Summary を投稿。

レビュー時の必須観点:

1. **Acceptance Criteria 全項目の充足チェック**
2. **回帰テストが「契約全体を破る経路」を網羅しているか**（最も明らかな defect の
   再現だけでなく、同じ defect family の他の入力で再現できるか）
3. **Self-Review で見落とされた defect 探索**
4. **後方互換性 / 既存テストへの影響**
5. **依存追加 / philosophy 抵触の有無**

指摘の重要度 P1（致命）/ P2（重要）/ P3（minor）を明記し、Codex が対応優先順位を
判断できるようにする。

### 自動レビュー指摘への採否方針（2026-08-03 新設・実害基準）

自動レビュアー（Codex bot 等）は修正 push のたびに再レビューするため、全採用を
既定にするとレビューが構造的に収束しない（実測: PR #241 = 18 巡 44 件、
PR #242 = 38 巡 96 件。後半の巡は「検証の検証」の逓減領域に入った）。
指摘への採否は以下を既定とする:

1. **実害基準で選別する**。採用の中心は「成果物の偽成功 / 偽失敗・データ破壊・
   provenance 汚染に至る具体経路が示される指摘」。動作に実害のない仕上げや
   逓減領域の深掘りは見送りが既定
2. **同型穴はファミリー単位で全数掃討し、終端を宣言する**。1 指摘 = 1 修正の
   ドリップ対応をせず、同型サイトを grep で全数列挙して一括是正し、設計ノートに
   「このファミリーは終端」と記録する（例: 型強制サイト全数の無強制検証化）
3. **見送りは境界宣言で終端する**。保証範囲を明文で宣言して設計ノートへ記録し、
   以降の同型深掘りには宣言参照の 1 行返信で対応する（E-30 / E-63 方式）。
   宣言には再開条件を含める——**実測で実害が判明した場合に加え、偽成功・
   データ破壊・provenance 汚染への具体的な実害経路が新たに示された場合も
   再着手の対象とする**（1 の実害基準と同一の閾値。宣言参照の 1 行返信で
   退けてよいのは、宣言済み範囲の再指摘で新しい経路を示さないものに限る）
4. **3 巡を超えて同一領域の指摘が続いたら逐次対応をやめる**。境界宣言で打ち切るか、
   マージ判断を User へ提案して収束させる（レビュー巡数は品質指標であると同時に
   コスト。巡を重ねること自体が正しさを増やさない領域を見極める）。
   **打ち切りの対象は 1 の実害基準を満たさない逓減領域の指摘に限る**——第 4 巡
   以降でも、偽成功・データ破壊・provenance 汚染の新しい具体経路が示された指摘は
   巡数に関わらず採用する（打ち切り規定が実害基準を上書きしない）

---

## 4. エスカレーション

Codex は以下のいずれかに該当したら **作業を停止し** Completion Summary 形式
（または draft PR の本文）で中断状態を報告すること:

1. Acceptance Criteria が技術的に達成不可能と判明した
2. Design Memo に書かれていない設計判断が必要になった
3. 既存テストが新規変更で壊れる（後方互換破壊の疑い）
4. **Design Memo の `Allowed Dependencies` に明示されていない**依存ライブラリの追加が
   必要になる（`pyproject.toml` の dependencies 変更を伴う場合。Memo で許可された
   依存の追加は escalation 対象外）
5. 哲学原則（決定論 / LLM 不使用 / API キー不要）への抵触の可能性

> **依存追加の運用補足**: roadmap_goal1.md の各フェーズ（Q0-4: `mir_eval`、
> Q1-1: `pyloudnorm`、Q2-1: `madmom`、Q3-1: `Demucs` 等）は新規依存を要する。
> Claude Code は Design Memo 発行時に `Allowed Dependencies` を必ず明示し、Codex は
> その範囲内であれば停止せず実装してよい。

---

## 5. ブランチ規約

- Codex が実装するブランチ: `codex/<topic>`（タスクごとに新規）
- Claude Code は基本ブランチを作らない（PR レビュー / Design Memo のみ）。例外的に
  Claude Code が小規模な fix-up を出す場合は `claude/<topic>`
- main への直接 push は CLAUDE.md の例外条項（`.claude/memory/` の運用ログ等）
  に該当する場合のみ

---

## 6. Tiered Attention Budget（注意予算の階層化）

AI エージェントのコンテキストウィンドウは有限資源。セッション開始時に全ドキュメントを
読み込むのではなく、タスクの段階に応じて読む対象を階層化する。

### Tier A — 常時必須（目標: 800 行以内）

セッション開始時に必ず読む。どのタスクでも必要な普遍的コンテキスト。

- `CLAUDE.md`
- `.claude/memory/STATUS.md` — Phase セクション + next-issue queue
- `.claude/memory/_index.md` — 直近 5 件のエントリ
- `AGENTS.md` §1–§5

### Tier B — Brief 起草前に読む（目標: 300 行以内）

新しい Task Brief / Design Memo を起草するタスクでのみ読む。

- `AGENTS.md` §7 Brief 起草チェックリスト
- 該当する `docs/` の planning / roadmap セクション
- `.claude/briefs/_index.md`

### Tier C — 特定タスクのオンデマンド

実装や深掘り調査で必要に応じて読む。

- `docs/` の個別設計ドキュメント
- 直近のセッションログ（`.claude/memory/YYYY-MM-DD.md`）
- `docs/roadmap_goal1.md` の特定フェーズ詳細

### Tier D — デバッグ・考古学

問題調査や過去の設計判断の掘り起こしでのみ読む。

- `.claude/memory/archive/` 配下のアーカイブ済みログ
- 古いセッションログ
- `archive/STATUS_MERGED_LOG.md`

---

## 7. Brief 起草チェックリスト

Design Memo / Task Brief を起草する前に以下を確認する。
semantic-ci-code での 20 ラウンド以上の経験から蒸留した項目。

1. **STATUS.md の next-issue queue を確認** — 既に同等のタスクがないか
2. **roadmap の Phase 位置を特定** — タスクがどのフェーズに属するか明確にする
3. **前提となる設計判断を列挙** — 未決定事項があれば Brief 内で選択肢を提示する
4. **Acceptance Criteria を検証可能な形で書く** — 「〜を改善する」ではなく「〜が X を返す」
5. **Scope IN/OUT を明示** — 変更してよいファイルと変更禁止のファイルを列挙
6. **依存追加の有無を確認** — 新規依存が必要なら Allowed Dependencies に明記
7. **タスク粒度が 0.5–2 日か確認** — 大きすぎる場合はフェーズ分割
8. **レビュー回数の予測** — 0 回が理想。3 回以上かかりそうなら Brief の仕様が不足している
9. **CompositionScore / PhysicalLayer 新フィールドの入場試験を確認** —
   Design Memo には、その欄の **fixity（locked 可能か）** と
   **往復一致の実測 or 実測計画**（[`docs/roundtrip_preservation.md`](docs/roundtrip_preservation.md) 参照）を必ず記載する
10. **locked file と未検出フィールドを初手で縛る** — Scope OUT に挙げた「変更禁止
    ファイル」（特に共有スキーマ: `compose/models.py` 等）は実装者が edge case 対応で
    破ってよいものではないと明記する。あわせて、**計測値が未検出/低信頼になりうる
    フィールドの扱い**（素直に欠落させる / sentinel を置く / schema は触らない、の
    いずれか）を Brief 段階で確定しておく。未確定のまま実装に渡すと、実装者が locked
    schema を広げて吸収し、それが自動レビュアーの連鎖指摘（P2 スパイラル）を誘発する
    — PR #71（T1）で 10+ ラウンドの churn を生んだ実例。
11. **永続成果物タスクは安全ゲートを組み込む** — 新規 loader / 配布物 / 永続成果物の
    書き込み系タスクの Memo には、§8 の Persistent Artifact Safety Gate を組み込む。
    出典: #175–#180 の Codex P2 計 14 件 + 2026-07-14 提案者フィードバック承認。

> **PR #71 教訓**: `compose/models.py` を Scope OUT に書いていたが、未検出
> フィールドの扱いを Brief で決めていなかったため、実装が `bpm: int → int|str` と
> schema を拡張して吸収。物理層 sentinel が audit/render/compare への横断契約を
> 発生させ、自動レビュアーが confidence-gating 系 P2 を連鎖発行した。

---

## 8. 経験外部化規律（Experience Externalization Discipline）

AI エージェントはセッション間で記憶を持たない。したがって、セッション中に得られた
知見は必ず明示的な成果物（ドキュメント、テスト、チェックリスト）に変換する。

### 原則

- **暗黙知は存在しない** — 言語化されていない知見は次のセッションで消失する
- **レビュー回数は品質指標** — 0 回 = Brief の仕様が十分、10+ 回 = 仕様欠落の兆候
- **パターンの再発は外部化の失敗** — 同じ問題が 2 回起きたら、防止策をドキュメント or テストに変換

### 外部化先の選択基準

| 知見の種類 | 外部化先 |
|---|---|
| 開発プロセス上の教訓 | `CLAUDE.md` or `AGENTS.md` に追記 |
| 特定機能の設計判断 | `docs/<topic>.md` |
| 再現可能な不変条件 | `tests/` に自動テストとして追加 |
| セッション固有の文脈 | `.claude/memory/YYYY-MM-DD.md` |
| 反復的な手順 | チェックリストとして `AGENTS.md` に追加 |

### Persistent Artifact Safety Gate（永続成果物安全ゲート）

新規 loader、配布物、永続成果物の書き込み経路は、Design Memo と実装の両方で次の
10 項目を確認する。出典: #175–#180 のレビューで反復した path、TOCTOU、原子性、
読み戻し、packaged 資源整合の指摘型。

1. **単一 read で parse + hash** — 同一バイト列から parse と hash を行い、TOCTOU を排除する
2. **path 脱出検査を二段に分離** — lexical validation（`../` 遡上判定・base 不要）と、
   resolved containment（symlink 解決後の `is_relative_to`）を分離して両方実施する
3. **build → dump → validate の読み戻しテスト** — builder が保証する不変条件を schema
   validator でも強制する
4. **duplicate ID 拒否** — 同じ ID に矛盾する状態や artifact を重ねて持ち込ませない
5. **unknown の推測補完禁止** — 省略欄を `supported` / `free` 等へ倒さない
6. **全構築後公開** — staging + atomic rename を使い、部分成果物を残さない
7. **公開途中失敗の注入テスト** — 公開処理の途中で失敗させ、部分成果物が残らないことを検証する
8. **input + output hash 記録** — 入力と公開成果物の双方に内容 hash を残す
9. **checkout vs installed の資源差テスト** — package-data と packaged コピーの整合を検証する
10. **schema_version 未知値拒否** — `Literal` 等で未知バージョンの読み込みを拒否する

### デモ昇格チェックリスト（scratchpad デモ → repo ツール）

一回性デモの成果物（スクリプト・ページ・レポート）を repo に常設ツールとして
出荷する前に確認する。出典: #154 実測 — このギャップが Codex P2 30 件・
14 ラウンドの主因（固定結論文 vs 計測値 ~22 件 + provenance 欠如 6 件）。
#156 でも発火不能になった説明叙述の残置を同じ規律で除去した。

1. **叙述の honesty 掃引** — 計測値・結論を断言する文（見出し・verdict・終幕・
   バッジ・脚注を含む）が、固定文でなく実際の計測値から導出されているか。
   デモの入力では真でも任意入力で偽になり得る断言は、計測導出に置換するか
   削除する。発火条件が構造的に消えた説明叙述（dead narrative）は残置しない
2. **キャッシュ再利用フラグには最初から provenance fail-fast** —
   `--skip-generate` 類の再利用フラグを付けるなら、キャッシュと現入力の
   provenance（prompt / source / model）照合ゲートを同時に実装する。
   silent corruption 経路を「過剰」と後回しにしない（#154 で当初棄却 →
   具体経路の提示で採用に転じた判定変更の教訓）
3. **PR 前セルフレビュー一周** — 大型デモ PR は提出前に実装外の目
   （Sonnet self-review）を一周通し、上記 1–2 の線引き漏れを拾ってから出す

### 計測系委譲の検算照合ゲート（設計値の独立検算）

数値の設計判断（閾値適用・効果量・判定規約の機械適用）を伴う計測・集計を委譲する
ブリーフには、設計側（Fable）の事前計算値を埋め込み、実装側に「独立に厳密計算し
設計値と照合、不一致（許容誤差超）は停止して報告」を義務付ける。出典: 2026-07-08
実測 — このゲートが設計側の暗算丸め誤差 ±0.0001 を実際に検出し、厳密値を
canonical と裁定できた。片側計算のみではどちら側の誤りも検出できない。

適用範囲の拡張（2026-07-12 採用）: 検算照合の対象は委譲した計測値だけでなく、
**設計側が新設する式・閾値・ゲート条件**を含む。新設式は導出過程を Memo に
明記し、実装前に独立検算（数値例の手計算照合）を通す。あわせて、入力データの
分解能・粒度でその式が検証可能か（分解能要件）を事前に確認する。出典: #168 R6
（均衡ゲート B 式の分母係数 2 欠落＝設計側の暗算起点）+ #169 R1（タイムスタンプ
分解能の検証漏れ）。

### 計測比較の交絡隔離規律（同一バッチ・同一モデル）

生成物の効果量比較（grip 判定・A/B 効果測定）は、比較対象セルを**同一バッチ・
同一モデル・同一生成フロー**で揃えて設計する。再利用 baseline × 新規セルの
cross-batch 比較は、測りたい効果を generator / model / batch drift と分離できず、
「効果 > 再生成ノイズ」基準を満たしても断定に使えない。経済化のためにやむを得ず
跨ぐ場合は設計段階で **confounded を pre-mark** し、結果の断定（効く / 打ち消せる /
留保解消）を禁止する。跨いだ後から片セルのみ追加生成しても同一バッチ性は回復しない
（事後判断の救済生成は事前登録違反）。出典: 2026-07-09 実測 — K2-seg Exclude 追試が
excl セル（ブラウザ生成・モデル未確認）× baseline（batch-1 流用）の交絡により
d=−1.66 / +1.64 を「未確定」へ全 docs 横断で格下げ（Codex 9 レビューラウンドの主因）。

### 実験 provenance の推定補完禁止（生成者確認・証跡取得）

実験データの provenance（生成順・生成経路・素材の出自・採用テイクの選別過程）が
欠落している場合、もっともらしい推定で補完してはならない。確定手段は
(a) 生成者への直接確認、(b) 証跡の取得（タイムスタンプ・ログ・sha256 等）のみ。
どちらも得られない場合は「未確定」のまま格付けし、verdict はその不確実性を
反映させる（canonical を主張しない・confounded 等へ格下げ）。訂正が発生した
場合は correction_history に推定→確認→訂正の全段階を透明記録する。出典:
2026-07-10 実測（K2-seg バッチ 2、#166→#168）。生成順の推定補完が格下げ→
復元→撤回の 3 往復を生んだ（生成者への 1 問で確定できた）。

### ローカル決定論バッチの canonical 条件（人手 UI バッチとの差分）

人手 UI 生成バッチの canonical 4 条件のうち ABBA カウンターバランスと均衡ゲート B は
「人手生成の時間ドリフト」への対策であり、決定論 seed によるローカルスクリプト
生成にはドリフト機構が不在のため適用しない。ただし、この非適用は次の 2 点を
満たす場合に限り canonical と認める:

1. **生成前の事前登録** — ABBA/均衡ゲートを適用しない旨とその根拠（決定論
   seed・スクリプト生成にドリフト機構が不在）を、生成開始前に plan へ
   事前登録する
2. **fresh-process 決定論スポット検証** — 時間的に最遠のクリップ 2 本以上を
   別プロセスで再生成して sha256 byte 一致を確認し、「出力は壁時計順序と
   無関係」を当該バッチ内の実測証拠として fixture に記録する

canonical 根拠の記述は実行の実態（例: fresh-process 16 本）に即して書く —
「同一プロセス」等の事実と異なる根拠づけは是正対象になった前例がある。出典:
2026-07-12 実測（バッチ M1、#171）。sha256 スポット照合 2/2 一致。

### 計測スケール変更の消費者全数インベントリ（凍結閾値・fixture・比較器）

計測値のスケールや定義を変える変更（指標の再定義・単位変更・正規化方式の変更）は、
実装に着手する前に、旧スケールの消費者 — 凍結閾値を持つ config、fixture の期待値、
比較器・スコアラー・generator の読み取り点 — を src 全域で全数インベントリし、
「新値へ移行 / 旧値供給で凍結維持 / 根拠付き据え置き」のいずれかに分類してから
実装する。個別指摘への逐次対応で消費者を一つずつ塞ぐのはモグラ叩きであり、
クラスとして閉鎖できるのは全数スイープだけである。

出典: 2026-07-18 #188 実測 — Codex P2 4 ラウンド 6 指摘が全て「v2 スケール値の
凍結閾値消費者への漏れ」という同根で、最終ラウンドで行った src 全域 `.valley_depth`
直読の全数分類を最初に行っていれば 1 ラウンドで閉じた。

### 記録日付の実測確認（date -u してから書く）

永続成果物（Design Memo・fixture・observation report・docs の dated log・
correction_history）に記録日付を書くときは、推定や体感の「今日」で書かず、
書き込み直前に `date -u` を実測して確認した UTC 日付を使う。セッションが UTC の
日付境界を跨ぐ場合や長時間セッションでは体感と実クロックが乖離する。誤日付が
provenance 連鎖（hash が pin する manifest 等）に入った後の是正は、コメント正誤より
hash 連鎖保全を優先する判断を伴い高くつく。出典: 2026-07-19 #193 実測 — Memo 日付を
2026-07-20 と誤記し decision trail に混入（Codex P2）。実クロック（生成タイムスタンプ・
authored date）は全て 07-19 UTC で、是正時に hash 連鎖が pin する manifest 内コメントの
誤日付は意図的残置となった。

### provenance 成果物の再現レシピは全入力の pin 検証接続後に emit

再現レシピ（コマンド列・スクリプト・generation plan 等）を committed provenance
成果物として emit する場合は、emit 前に「レシピの**全入力**（プロンプト・素材・
config・モデル・スクリプト自身）を package の pin（sha256 / manifest hash）へ
検証付きで接続し、マシン非依存で再現可能にする」設計を先に固める。一部入力だけ
pin して残りを指摘駆動で塞ぐと、同一ファミリーの穴（per-input hash 欠落・出力幾何・
衝突ガード・パス衛生）が逐次露呈する。出典: 2026-07-19 #191 実測 — Codex 9 ラウンドが
全て「committed provenance 成果物のマシン非依存・全入力の pin 検証接続」という同根で、
最初にこの設計を固めていれば 1–2 ラウンドで閉じた。

**emit 前チェックリスト**（2026-07-22 運用具体化。#202 実測: Codex 12 巡 14 指摘が
全て以下のいずれかに該当し、事前適用なら 1 巡で閉じた）:

1. **入力全数列挙は呼び出しグラフ実読で**: 直接引数 → 間接解決先（参照ファイルが
   指す先）→ 実行経路が読む config まで。除外は消費点単位の個別根拠のみ
   （一括 scope-out 宣言は 1 件の反例で崩れ再指摘を招く）
2. **実行物も pin**: レシピが呼ぶスクリプト自身を fixture 収載 + sha256 pin
   （scratchpad 参照の残置禁止）
3. **レシピは逐語シェル実行可能**: プレースホルダ・未定義変数・全角記号ゼロ。
   emit 前に一字一句コピペで再実行し出力 byte 一致を実測
4. **パスは checkout-stable**: 絶対 / scratchpad パス禁止。成果物に焼き込まれる
   path 引数も repo 相対、レシピの出力先は committed fixture パスを直接指す
5. **pin は fixture テストで全数突合**: working tree の実 sha256 と照合し、
   将来の入力変更を機械検出（エントリ追加で自動拡張されるループ実装で）
6. **時系列主張は消費文脈 2 層**: 履歴参照可能な文脈 = コミット祖先関係で担保 /
   squash・export 等の非参照文脈 = attestation へ格下げして読む旨を明記
7. **委譲生成物の検収でタイムスタンプ全欄を突合**: date -u 実測でない時刻
   （丸め・未来値）は委譲ブリーフに実測を明記しても擦り抜ける（#202 実例）

**pin の接続 3 問**（2026-07-25 追加。#217 実測: Codex 49 指摘（P1 1 / P2 48）が
例外なく「**pin は取ったが、実際に実行された実装に接続していない**」の同型で、
事前適用なら大半を 1 巡目で自己検出できた）。pin を emit する前に、その pin に
ついて次の 3 問へ答えられるかを確認する:

- **場所**: そのモデル / デコーダが**実際に読む場所**だけを探索したか。独自の
  探索パスや環境変数を足すと「pin はしたが実行時は別ファイルを読む」乖離を作る
  （#217: `TORCH_HOME` へ一本化・API 経路=torch hub active dir / CLI 経路=env 由来と
  経路別に解決。「参照はするが demucs が読まない場所」を指す案は不採用）
- **時点**: hash した bytes と実行された bytes の間に **cache の窓**が無いか。
  import 済みモジュール・dlopen 済み共有ライブラリ・プロセス内 model cache は
  「ディスクは新 bytes / 実行は旧コード」を成立させる。解決は (a) hash を**あらゆる
  import・ロードより前**に確定する（`find_spec` / ヘッダ直読みなど、対象を実行しない
  手段を使う）、(b) 実行後に **memo 迂回で再 hash** して一致を検証、(c) in-memory
  cache には**プロセス内 load-time pin** を持ち食い違ったら publish しない
- **被覆**: 覆えなかったものを「無い」と主張していないか。**読めない ≠ 依存なし**
  （#217: 非 ELF バイナリ・展開できないローダトークン・fallback デコーダ・導入済み
  なのに hash 不能なパッケージ）。覆えないなら route 単位で fail-closed にし、
  逆に**実行されていないものを pin したことにもしない**（実行経路で分岐する依存は
  経路判定してから covered へ入れ、覆った名前を report に列挙する）

線引きの原則: 閉包は「本層のコードが直接呼ぶか / 推論・デコードの実装そのものか」
まで広げ、OS 基盤（libc 等）へは広げない。**どの環境でも fail-closed になる線は
誰も守れないので線ではない**。

### 永続成果物の「公開サイト」設計チェックリスト

新しい公開サイト（CLI/ライブラリがファイル群を書き出す場所）を追加するときは、
Design Memo と実装の両方で以下 6 点を先回り適用する:

1. **staged 一括公開**: 全ファイルを staging に書いてから単一フェーズで rename
   公開する。rollback は `except BaseException`（KeyboardInterrupt/SystemExit
   含む）で snapshot 復元し、部分更新を残さない
2. **衝突ガードは必須引数**: 解決済み入力パス全数（protected inputs）を公開
   ヘルパーの**デフォルトなし必須引数**にし、渡し忘れを呼び出し時エラーで
   検出する。CLI 側は公開前 preflight でも同検査を行う
3. **bytes 経路**: encode は 1 回だけ行い、その同一 bytes を書き込みと hash の
   両方に使う（text モード書き込みは禁止 — 改行変換で pin と実 bytes が乖離する）
4. **成果物 hash の pin**: 公開した bytes の hash を state に記録し、後続工程は
   使用前に突合する。pin の欠落は検証スキップでなく stale と同扱いの
   fail-closed にする
5. **per-run パス**: 複数 run が共有する global ファイルを正典にしない
   （`<root>/<kind>/<run-id>/` 形式）。共有の便宜コピーを残す場合は pin・
   突合対象外であることを明示する
6. **表示コマンドは shlex**: 成果物や画面に出すコマンド文字列は `shlex.join`
   で構築する（`cd` 行を含む）

出典: 2026-07-23 recast トラック実測 — #207/#208/#210 の Codex P2 同型指摘 15
件超がこの 6 類型に該当し、事前適用なら各 1 巡で閉じた（詳細 =
.claude/memory/2026-07-23.md）。

### 委譲停滞の回復レシピ（成果物判定 × 分割同期再実行）

サブエージェント委譲が「バックグラウンド実行 / monitor の完了通知を待つ」と
述べて停止する事象は、同期実行 3 点固定文言をブリーフに明記しても発生する
（2026-07-07〜07-23 で計 9 回実測）。回復は次の 2 段を標準とする:

1. **停滞判定は報告文でなく成果物**: リモートブランチ先端（`git fetch` +
   `log`）・結果ファイル・exit code で「push・出力が実在するか」を確認して
   から停滞と判定する
2. **再開指示は分割同期**: SendMessage で「完了済み工程は成果物で確認して
   スキップし、実行結果が確認できていないテストは**分割して同期再実行**
   （`-m "not slow"` / `-m slow -k <subset>` 等、各コマンドで timeout 明示・
   フォアグラウンドのみ）を指示する。transcript 消失などで再開不能の場合、
   共有コードの修正は下流ブランチ側で代替実装しマージ順で到達させる

出典: 2026-07-23 recast セッション実測 — 停滞 6 回を全て本レシピで回復
（+担当エージェント transcript 消失 1 回の代替実装）。

### Claude 自己 PR のレビュー運用規律（2026-07-30 制定）

Claude が自ら実装して PR を出すルート（Claude 完結ルート）の標準運用。
出典: PR #234（Codex 7 ラウンド計 8 指摘）/ #235 の実測。

1. **デフォルトフロー = PR 作成〜subscribe まで一気通貫**: push →
   `mcp__github__create_pull_request`（CLAUDE.md 規定本文必須）→
   `subscribe_pr_activity` → 60 分周期の self check-in（send_later）再アーム。
   マージ/クローズで監視終了し、残った check-in trigger を削除する
2. **レビュー対応の採否はオーバーエンジニアリング回避を第一規律とする**:
   実害のある指摘のみ採用し、到達可能ケースのない一般化（攻撃者モデルの
   漸進強化、resolve 後 containment 等）は**境界宣言で系統ごと終端**する。
   境界宣言はスレッド返信に加え、対象成果物に応じた永続位置（Python コード
   なら docstring/コメント、docs・config・fixture 等なら該当する設計 doc か
   本節）にも残し、以降の同型指摘の打ち切り根拠にする。採否判定は Fable
   （設計判断）、スレッド取得・返信は Sonnet/Opus 委譲、**修正実装は Sonnet
   委譲**（CLAUDE.md Advisor Strategy の役割表と同一）
3. **対応済みスレッドは resolve 表示まで行って完了**: （採用時のみ）修正
   push → 対応内容のスレッド返信 → `resolve_review_thread` で resolved 化、
   の 3 点セットが 1 指摘の完了条件。返信要件: **採用時は対応 commit hash を
   必須**、**見送り時は hash 不要**で代わりに見送り根拠（境界宣言 or 反証）を
   必須とする。返信前にスレッド本文を照合してから投稿する（誤スレッド投稿の
   再発防止）。返信末尾には attribution footer を付ける — 本文のあとに空行 +
   `---` 行 + `_Generated by [Claude Code](https://claude.ai/code)_` の
   イタリックリンク行、の並びをリテラルに使う

### 全件テスト実行中にソースを編集しない（2026-08-02 制定）

`scripts/run_melody_accuracy.py` は自分のコード閉包の digest を **import 時に pin し、
実行後に再計算して照合する**（`_require_unchanged_since_load`）。したがって全件テスト
実行中にソースを編集すると、**実体のある回帰と区別のつかない大量失敗**が出る。

- 実測（2026-08-02・PR #238）: 全件実行中にレビュー対応の編集を入れ、**122 failed**。
  クリーンな作業ツリーで失敗テストを再実行して自己汚染と切り分けるまでに、全件実行
  1 回分（約 20 分）を失った。
- 運用: レビュー対応が来たら **走行中の全件実行を先に停止してから編集する**。
  次の commit で結果はどのみち無効になるので、途中結果を惜しまない。停止手段は
  実行環境依存で、Claude Code はバックグラウンドタスクの停止（`TaskStop`）、
  Codex は当該 pytest を走らせている terminal session の中断（`Ctrl-C` / kill）を
  使う。全件は **ラウンドが途切れた時点で 1 回通す**（各 push で CI が独立に全件を
  回すため、ローカル全件をラウンド毎に回す冗長性は不要）。
- 判定: 大量失敗を見たら、まず「その実行中に自分がソースを触っていないか」を確認する。
  触っていれば結論を出す前にクリーンツリーで代表failure を再実行して切り分ける。

---

## 関連ドキュメント

- [`CLAUDE.md`](CLAUDE.md) — 役割分担・運用ポリシー全般、Workflow 節に概要
- [`.claude/memory/STATUS.md`](.claude/memory/STATUS.md) — プロジェクト状況、Phase、next-issue queue
- [`docs/roadmap_goal1.md`](docs/roadmap_goal1.md) — 目的1（定量観測）の Codex 実装単位
- [`docs/roadmap.md`](docs/roadmap.md) — 段階軸（PoC / Pre-prototype）の俯瞰
