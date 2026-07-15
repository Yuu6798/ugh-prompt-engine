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

---

## 関連ドキュメント

- [`CLAUDE.md`](CLAUDE.md) — 役割分担・運用ポリシー全般、Workflow 節に概要
- [`.claude/memory/STATUS.md`](.claude/memory/STATUS.md) — プロジェクト状況、Phase、next-issue queue
- [`docs/roadmap_goal1.md`](docs/roadmap_goal1.md) — 目的1（定量観測）の Codex 実装単位
- [`docs/roadmap.md`](docs/roadmap.md) — 段階軸（PoC / Pre-prototype）の俯瞰
