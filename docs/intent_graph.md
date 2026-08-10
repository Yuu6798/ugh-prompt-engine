# Intent Graph v0 — 充足/不足のポインタ台帳

Status: v0 実装完了。データ = `docs/intent/graph.yaml`（`intent-graph/0.1`）。
実装 = `src/svp_rpe/intent/`（models / loader / frontier）+
`svprpe intent-status`（`src/svp_rpe/cli/intent.py`）。

## 1. 目的

Intent「AI の楽譜を作る」に対する充足/不足を、`docs/`・PR 履歴に散在する証拠への
**ポインタ台帳**として機械可読化し、フロンティア（次に検証すべきノード）を
決定論的に導出する。ループ運用（選択→Design Memo→実装→判定→更新）の選択器。

## 2. スキーマ（`intent-graph/0.1`）

トップレベル: `{schema_version: "intent-graph/0.1", nodes: [...]}`（未知 key は
fail-closed で拒否、`svp_rpe.intent.models.IntentGraph`）。

ノード（`IntentNode`、frozen）:

| フィールド | 型 | 規約 |
|---|---|---|
| `id` | str | `^[a-z0-9_]+(\.[a-z0-9_]+)+$`。全体で一意 |
| `claim` | str | 検証可能な主張、1 文（非空） |
| `status` | Literal | `verified` / `partial` / `dead` / `untested` / `machine_dependent` |
| `evidence` | list[str] | `untested` 以外は 1 件以上必須。`/` を含む項目はリポジトリ相対パスとみなし実在検証、含まない項目（例 `"PR #171"`）は参照として素通し |
| `depends_on` | list[str] | 参照先 id の実在必須・DAG（循環拒否） |
| `reentry` | Optional[str] | `status=dead` のとき必須、それ以外は任意 |
| `note` | Optional[str] | 自由記述の補足 |

## 3. status 語彙と遷移規約

| status | 意味 |
|---|---|
| `verified` | 主張が実測 evidence で裏付けられている |
| `partial` | 部分的に裏付けられている（`note` に境界を記す） |
| `dead` | 反証済み、または見送り確定（`reentry` に再入条件を記す） |
| `untested` | まだ検証していない（`evidence` は空でよい） |
| `machine_dependent` | 実機・実素材律速で保留（frontier に入らない） |

**status の遷移は evidence 必須・PR レビュー経由の手動編集のみ**。本ツールは
status を自動更新・自動推論しない（最小主義の境界、§5）。遷移するときは
`evidence` を更新後の状態を裏付ける内容へ差し替え、`dead` へ落とすときは
`reentry` を必ず埋める。

## 4. frontier・blocked 意味論（`svp_rpe.intent.frontier.derive_frontier`、純関数）

stored `status` は一切書き換えない — 以下は表示上の導出分類。

- **blocked**: 推移的 `depends_on` のどこかに `status=dead` のノードがある
  （自身の status は問わない）
- **frontier**: stored `untested` かつ blocked でなく、直接 `depends_on` の
  全ノードの status が `verified` または `partial`
- **pending**: stored `untested` で frontier でも blocked でもない
- `machine_dependent` は frontier に入れない（別枠で列挙）

## 5. 最小主義の境界（やらないこと）

- status の自動更新・自動推論はしない（更新は PR レビュー経由の手動編集のみ）
- roadmap / STATUS.md の置換はしない（グラフは叙述禁止・ポインタのみ）
- 重み付け・スコアリング・可視化（描画）はしない
- config 二重コピーには乗せない（グラフは docs 側・パッケージ非同梱）

## 6. CLI

```bash
svprpe intent-status                       # 既定 docs/intent/graph.yaml（cwd 起点）
svprpe intent-status --graph path/to/graph.yaml
```

出力はプレーンに: status 別件数 → frontier 一覧（id + claim）→ blocked 一覧
（id + 阻害している dead 祖先）→ machine_dependent 一覧。read-only（`--graph` へ
一切書き込まない）。repo-dev 用ツールでありインストール実行（`pip install
svp-rpe` のみで `docs/intent/graph.yaml` が手元にない環境）は想定外。

## 7. ループ運用 5 ステップ

1. `svprpe intent-status` で frontier を確認し、次に検証するノードを選ぶ
2. そのノードの `claim` を検証する Design Memo を起こす
3. Codex/Claude が実装・実測する
4. 結果を Fable が裁定し、`docs/intent/graph.yaml` の該当ノードの `status` /
   `evidence` / `note` / `reentry` を PR で更新する
5. `svprpe intent-status` を再実行し、新しい frontier を確認する（1 に戻る）

## 8. 整合検証（`svp_rpe.intent.loader.load_intent_graph`、fail-fast）

一意 id / `depends_on` 実在 / 非循環（DAG）/ `dead` は `reentry` 必須 /
`evidence` パス実在 / status 語彙 / YAML 重複キー拒否 / `evidence` パスの
repo 内封じ込め（絶対パス・`..` 成分・symlink 脱出を拒否）。違反はすべて集約し
1 回の `ValueError` にまとめて報告する。

`graph.yaml` は canonical 配置（`docs/intent/graph.yaml`）が前提。他配置は
`load_intent_graph(path, repo_root=...)` で repo root を明示指定した場合のみ
サポートする（任意配置の自動解決は v0 非目標。既定 CLI `svprpe intent-status`
は canonical 配置のまま変更なし）。
