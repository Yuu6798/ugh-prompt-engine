# Code Semantic CI — Design Specification (v0.1 draft)

## 0. Statement of Scope (most important)

> **This is not a linter. This is not a type checker. This is not a test runner.**
>
> Code Semantic CI is a deterministic semantic CI layer that compares
> **declared change intent**, **expected code state**, **baseline code state**,
> and **observed code state**, and emits a **semantic diff** plus
> **repair instructions** for the next generator pass.

既存 CI ツール（lint / type / test）が見逃す **「宣言された修正意図」と「観測された差分」の意味的整合性** を、決定論的に検証することが本製品の唯一の存在理由。生成 AI（Codex / Claude Code / Cursor 等）が出した PR を gate するレイヤとして機能する。

## 1. 位置付けと差別化

| 既存ツール | 見るもの | 限界 |
|---|---|---|
| linter | 規約違反 | 意図に対する逸脱は見ない |
| type checker | 型整合性 | 「変えてはいけない型を変えた」は見ない |
| test runner | 振る舞い | テストが通っても意図ずれは検出不能 |
| **Code Semantic CI** | **declared intent vs observed delta** | — |

LLM-as-judge 系（Coderabbit / Greptile 等）との差別化:

- 決定論的（同入力 → 同出力、CI gate に使える）
- 監査可能（evidence chain を必ず出す）
- 第三者性（生成ベンダ非依存）

## 2. 中核モデル: 3-state RPE

音楽版の `Target SVP → Expected RPE → Observed RPE → Diff` モデルを **コード版では 3 状態** に拡張する:

```
Target SVP            Baseline Code         Candidate Code
    │                       │                       │
    ▼                       ▼                       ▼
Expected RPE         Baseline RPE           Observed RPE
    │                       │                       │
    └───────────┬───────────┴──────────┬───────────┘
                ▼                       ▼
        ConstraintEvaluator     CodeStateDelta
                │                       │
                └───────────┬───────────┘
                            ▼
                      Semantic Diff
                            ▼
                         Verdict (pass | repair | fail)
                            ▼
                       Repair SVP
                            ▼
              Repair Prompt / Patch Instruction
```

### なぜ Baseline RPE が必須か

リファクタリング・バグ修正の本質は「**外部契約は変えずに内部構造を変える**」こと。これを検証するには baseline との差分が必要で、Observed と Expected だけでは表現できない。

例: spec が `preserve api_surface_of: ["src.api.users.*"]` と言った時、これを評価するには baseline の API surface を知る必要がある。

### この拡張は音楽版にも遡及的に効く

将来 `preserve section_structure from baseline` のような制約を音楽版に入れる時、同じ 3-state モデルが必要になる。本設計はフレームワーク core の改善でもある。

## 3. State Schema

**共通スキーマ + 言語固有 extension** に分割する。

### 3.1 CodeState（共通スキーマ）

```yaml
CodeState:
  api_surface:           # 公開シンボル集合
    - { fqn, kind, signature, visibility }
  type_relations:        # 型関係
    - { fqn, type_expr, nullable, generic_params }
  effects:               # 効果分類
    - { fqn, effect_class: [pure|io|net|fs|process|env|time_random|stdout|dynamic_code|unsafe_deserialize|global_mutation], confidence, evidence }
  control_flow:          # CFG メトリクス（P2 以降の充実）
    - { fqn, branches, loops, exception_paths, cyclomatic }
  data_flow:             # 簡易タイント（P2 以降）
    - { source, sink, path }
  imports:               # モジュール依存
    - { module, from, symbols }
  complexity:
    - { fqn, cyclomatic, cognitive }
  test_surface:
    - { test_file, test_function, asserts, parametrize_count }
  coverage:              # P3 以降、dynamic 拡張
    - { file, line_coverage, branch_coverage }
  module_graph:
    - { module, imports, imported_by }
```

### 3.2 CodeStateDelta（first-class エンティティ）

`CodeStateDelta` は単なるレポートフィールドではなく、**制約が target にできる first-class エンティティ**。

```yaml
CodeStateDelta:
  api_surface_delta: { added: [...], removed: [...], changed: [...] }
  type_changes: [...]
  effect_changes: { added: [...], removed: [...] }
  cfg_delta: { new_branches, removed_branches }
  imports_delta: { added: [...], removed: [...] }
  complexity_delta: { cyclomatic: ±N, cognitive: ±N }
  test_surface_delta: { new_files, new_cases, removed_cases }
  coverage_delta: { line: ±%, branch: ±% }       # optional
  files_touched: int
  loc_delta: { added: int, removed: int }
```

### 3.3 言語固有 extension

Python / TypeScript で共通スキーマを満たしつつ、言語固有フィールドを追加可能:

```yaml
# Python extension
python_specific:
  decorators: [...]
  metaclasses: [...]
  type_var_bounds: [...]

# TypeScript extension
typescript_specific:
  generic_constraints: [...]
  conditional_types: [...]
  declaration_merging: [...]
```

## 4. Target SVP DSL

### 4.1 基本構造

```yaml
intent: "<人間可読の意図 1 行>"
change:
  primary_kind: refactor | feature | bugfix | test_update
  allowed_secondary_kinds: [...]
  scope:
    files: [...]
    modules: [...]

# StateConstraints / DeltaConstraints / RepairConstraints
constraints:
  - id: <unique id>
    kind: state | delta | repair
    target: <RPE field path>
    operator: <typed operator>
    expected: <value or "baseline">
    severity: hard | soft | info
    unknown_policy: fail | repair | warn | ignore
    tolerance: <numeric or null>
    evidence_required: true | false
    scope: file | module | function | package
```

### 4.2 change_kind は制約テンプレート展開器

`change_kind` は単なるラベルではなく、**default 制約セットを展開するキー**として扱う。

```yaml
# Target SVP に
change:
  primary_kind: refactor

# が宣言されると、ConstraintTemplate(refactor) が以下を自動展開:
#   - api_surface.public_symbols equals_baseline (hard, fail on unknown)
#   - type_signatures equals_baseline (hard)
#   - effects equals_baseline (hard)
#   - test_expectations unchanged (hard)
#   - complexity may_decrease (soft)
#   - internal_symbols may_change (soft)
```

ユーザは追加制約を `constraints:` で override / 補強できる。

### 4.3 change_kind の規範ルール（P1 範囲）

| change_kind | 必須（required） | 許可（allowed） | 禁止（forbidden） |
|---|---|---|---|
| **feature** | declared API 追加, テスト追加 | 新型, 新 config, allowed import 追加 | 既存 API 削除, 未宣言 effect |
| **bugfix** | 公開 API 不変, 回帰テスト追加 | 局所変更, 局所複雑度変化 | 新公開 API, 大規模構造変更, 未宣言 effect |
| **refactor** | 公開 API 不変, 型契約不変, effect 不変 | 内部構造変更, 複雑度低下, 命名整理 | 公開 API 変更, 型契約変更, 新 effect, テスト期待値変更 |
| **test_update** | テスト追加/修正 | fixture 更新 | production code の意味変更 |

### 4.4 複合 PR の扱い

実務では `feature + refactor`、`bugfix + dependency_update` のような混在が起きる。P1 では:

- `primary_kind` は **必須**
- `allowed_secondary_kinds` で明示宣言された範囲のみ許可
- 未宣言の secondary delta が出たら **repair**
- hard violation が出たら **fail**

### 4.5 サンプル: 機能追加

```yaml
intent: "fetch_user_profile を追加"
change:
  primary_kind: feature
  scope:
    files: ["src/api/users.py", "tests/test_users.py"]

constraints:
  - id: feature_added
    kind: delta
    target: api_surface_delta.added
    operator: includes_all
    expected: ["src.api.users.fetch_user_profile"]
    severity: hard
    unknown_policy: fail
    evidence_required: true

  - id: no_other_api_changes
    kind: delta
    target: api_surface.public_symbols
    operator: superset_of_baseline
    severity: hard
    unknown_policy: fail

  - id: no_new_io_in_models
    kind: delta
    target: effects
    operator: no_new_items
    scope: src.models.*
    severity: hard
    unknown_policy: repair

  - id: complexity_budget
    kind: delta
    target: complexity_delta.cyclomatic
    operator: less_than_or_equal
    expected: 5
    severity: soft
    unknown_policy: warn

  - id: test_added
    kind: delta
    target: test_surface_delta.new_test_cases
    operator: greater_than_or_equal
    expected: 1
    severity: hard
    unknown_policy: fail
```

### 4.6 サンプル: リファクタリング

```yaml
intent: "auth を middleware 化"
change:
  primary_kind: refactor

# change_kind=refactor のテンプレート展開で
# api_surface / type / effects の equals_baseline 制約が自動付与される。
# 追加で:

constraints:
  - id: complexity_should_decrease
    kind: delta
    target: complexity_delta.cyclomatic
    operator: less_than
    expected: 0
    severity: soft
    unknown_policy: warn
```

## 5. Constraint Type System

### 5.1 3 種類の制約

| kind | 何を評価するか | 例 |
|---|---|---|
| **state** | Observed RPE 単独で満たすべき性質 | `complexity ≤ 10` |
| **delta** | Baseline と Observed の関係性 | `api_surface unchanged` |
| **repair** | 次サイクルへの指示 | `restore X, reduce Y` |

これらを混同せず、評価器を分離する。

### 5.2 P1 で実装する operator 集合

```
equals
not_equals
equals_baseline           # delta: baseline と一致
not_equals_baseline       # delta: baseline と不一致
includes_all              # set: 部分集合関係
includes_any
excludes_all
subset_of
superset_of
superset_of_baseline      # delta: baseline の superset
no_new_items              # delta: 追加なし
no_removed_items          # delta: 削除なし
less_than
less_than_or_equal
greater_than
greater_than_or_equal
within_range
unchanged                 # delta: 変更なし
changed                   # delta: 何らかの変更あり
changed_only_in           # delta: 変更が指定 scope 内のみ
```

P1 ではこれ以上 operator を増やさない。**任意 Python 式や独自構文 DSL は採用しない**（決定論・安全性・再現性が壊れる）。

### 5.3 制約評価結果の構造

評価結果は単なる boolean ではなく、必ず evidence chain を持つ:

```yaml
result:
  constraint_id: public_api_preserved
  status: violated | satisfied | unknown
  severity: hard | soft | info
  target: api_surface.public_symbols
  expected: baseline
  observed_added: []
  observed_removed:
    - src.api.users.create_user
  evidence:
    extractor: griffe
    extractor_version: "0.42.0"
    field: api_surface.public_symbols
    source_location:
      file: src/api/users.py
      line: 12
  repair_hint: restore_removed_public_symbol
```

### 5.4 unknown_policy: 計測層と判定層の分離

extractor が抽出に失敗した場合（型情報不足、解析対象外パターン等）の挙動を **constraint ごと**に指定:

| policy | 挙動 |
|---|---|
| **fail** | 即時 fail。critical な制約に使う |
| **repair** | repair 経路へ。回復可能なら再生成 |
| **warn** | 警告のみ、verdict には影響しない |
| **ignore** | 完全に無視 |

これは UGH Audit Engine の「検出層・電卓層・判定層を分離する」原理と同じ。**計測器は数値や状態を出すだけで、最終判定は別層**で行う。

## 6. Extractor Architecture

### 6.1 既存ツール wrapping を基本とする

自前で型検査器・AST 解析器を作らない。**既存ツールの出力を共通スキーマに正規化する** だけが Semantic CI の責務。

| 次元 | Python | TypeScript |
|---|---|---|
| API surface | `griffe` | `ts-morph` + `@microsoft/api-extractor` |
| 型 | `mypy` / `pyright` | `tsc --emitDeclarationOnly` |
| AST / CFG | `ast` + `networkx` | `ts-morph` + `@typescript-eslint/parser` |
| 効果 | known-effect リスト + AST 走査 | 同上 |
| 複雑度 | `radon` / `lizard` | `eslintcc` / `ts-complex` |
| import 解析 | `ast` | `ts-morph` |
| test surface | `pytest --collect-only` AST | `jest --listTests` |
| coverage（P3+） | `coverage.py` | `c8` / `nyc` |

### 6.2 partial extraction tolerance

一部 extractor が落ちても、他の verdict は維持する。失敗した次元は対応する制約の `unknown_policy` に従う。

## 7. effect_db の設計

### 7.1 保守的な副作用シグネチャ辞書

完全な副作用解析は P1 範囲外。**known-effect の API シグネチャ辞書** として始める。

```yaml
effects:
  - id: builtin_open
    language: python
    match:
      call: open
    effect: fs
    access: unknown
    severity: medium

  - id: os_remove
    language: python
    match:
      call: os.remove
    effect: fs
    access: write
    severity: high

  - id: urllib_urlopen
    language: python
    match:
      call: urllib.request.urlopen
    effect: net
    access: read
    severity: high
```

### 7.2 検出結果には confidence と resolution_level を必ず持たせる

```yaml
detected_effect:
  effect: fs
  access: write
  confidence: 0.7
  evidence:
    call: write_text
    file: src/config.py
    line: 44
  resolution_level: method_name_only  # direct_call | imported_alias | method_name_only
```

これにより誤検出と確定検出を区別できる。

### 7.3 解決レベルの段階的実装

| Level | 例 | P1 範囲 |
|---|---|---|
| 1: direct call | `open()`, `os.remove()` | ✓ |
| 2: imported alias | `from os import remove as rm; rm()` | 一部 |
| 3: object method | `Path("x").write_text()` | ✗（P2 以降） |

完全な型・名前解決は P1 では狙わない。

### 7.4 P1 seed: Python 標準ライブラリ

| effect | entries（抜粋） |
|---|---|
| **fs** | `open`, `pathlib.Path.open`, `Path.read_text`, `Path.write_text`, `os.remove`, `os.unlink`, `os.rename`, `os.replace`, `os.mkdir`, `os.makedirs`, `shutil.copy`, `shutil.copyfile`, `shutil.copytree`, `shutil.rmtree`, `shutil.move` |
| **net** | `socket.socket`, `urllib.request.urlopen`, `http.client.HTTPConnection`, `http.client.HTTPSConnection`, `ftplib.FTP`, `smtplib.SMTP`, `imaplib.IMAP4`, `poplib.POP3`, `xmlrpc.client.ServerProxy` |
| **process** | `subprocess.run`, `subprocess.Popen`, `subprocess.call`, `os.system`, `os.exec*`, `os.spawn*` |
| **env** | `os.environ`, `os.getenv`, `os.putenv`, `os.unsetenv` |
| **time_random** | `time.time`, `datetime.datetime.now`, `datetime.date.today`, `random.*`, `secrets.*`, `uuid.uuid4` |
| **stdout** | `print`, `sys.stdout.write`, `sys.stderr.write`, `logging.*` |
| **dynamic_code** | `eval`, `exec`, `compile`, `__import__`, `importlib.import_module` |
| **unsafe_deserialize** | `pickle.load`, `pickle.loads`, `marshal.load`, `marshal.loads` |
| **global_mutation** | `global` 文, module-level 再代入, module global の変更 |

## 8. Verdict 設計

### 8.1 3-tier semantics

| verdict | 条件 |
|---|---|
| **pass** | hard 制約全て満たす + soft 違反は tolerance 内 + unknown は non-critical |
| **repair** | hard 違反があるが回復可能 / soft 違反が tolerance 超過 / 宣言意図と observed delta が不整合だが回復可能 |
| **fail** | spec 矛盾 / critical 制約での extractor 失敗 / 禁止 effect 検出 / refactor で公開 API 破壊 / セキュリティ関連 dynamic_code 導入 |

### 8.2 階層的判定（lock 違反は即 fail）

```
1. lock 違反が 1 つでも → fail（即時、loss 計算不要）
2. preserve 違反 → repair（修復可能、loss に加算）
3. over_changed → repair（change_budget 超過分を縮減）
4. metric tolerance 内逸脱 → loss 加算
5. 全て tolerance 内 → pass
```

### 8.3 exit code policy

```
pass   → exit 0
repair → exit 1（CI を block）
fail   → exit 2（CI を block、より重篤）
```

## 9. Repair SVP

### 9.1 構造

```yaml
repair:
  preserve:                     # 維持を引き継ぎ
    - api_of: src.auth.login
  restore:                      # 削除されたものを復旧
    - public_function: src.api.users.delete_user
  reduce:                       # 過剰変更を縮減
    - imports_added: ["aiohttp"]
    - files_touched: ["src/utils/helpers.py"]
  defer:                        # 今回は保留
    - "complexity_reduction_in: src/core/engine.py"
  lock:
    - public_api_of: src.models.*
  repair_order:                 # 修復の優先順
    - restore_api
    - reduce_imports
    - retry_feature_addition
```

### 9.2 Repair SVP は **コードを直接変更しない**

Repair SVP は決定論的な修復指示を出すのみ。実際のコード patch は外部 generator（Codex / Claude Code 等）が次サイクルで適用する。これにより:

- Semantic CI は決定論を維持
- 生成は generator の責務
- 両者の責任分界が明確

### 9.3 Repair Compiler（P5 範囲）

将来、Repair SVP を generator-specific な prompt patch に変換する layer を追加する:

```
Repair SVP → Repair Compiler → {
  Codex prompt patch,
  Claude Code instruction,
  Cursor edit hint,
  ...
}
```

P1 では Repair SVP を JSON / YAML として emit するのみ。

## 10. Hash Trail（reproducibility）

### 10.1 hash に含めるべき要素

```python
input_hash = hash(
    target_svp_hash,
    baseline_code_hash,
    candidate_code_hash,
    schema_version,
    extractor_versions = {
        "griffe": "0.42.0",
        "mypy": "1.8.0",
        "radon": "6.0.1",
        ...
    },
    effect_db_version,
    constraint_operator_version,
    python_version,            # interpreter version も影響する
    config_hash,               # 設定 yaml の hash
    threshold,                 # 既出: 閾値も状態の一部
)
```

### 10.2 なぜ extractor version が必須か

`mypy` / `pyright` / `radon` / `griffe` などはバージョンアップで出力が変わる可能性がある。Semantic CI が再現性を名乗るなら、これらすべての version を hash trail に含める必要がある。

### 10.3 Round-trip Log

各段階の中間生成物の hash を chain として記録:

```yaml
round_trip:
  - stage: extract_baseline
    input_hash: <baseline_code_hash>
    output_hash: <baseline_rpe_hash>
    extractor_versions: {...}
  - stage: extract_observed
    input_hash: <candidate_code_hash>
    output_hash: <observed_rpe_hash>
  - stage: compile_expected
    input_hash: <target_svp_hash>
    output_hash: <expected_rpe_hash>
  - stage: evaluate_constraints
    input_hash: <expected_rpe_hash + baseline_rpe_hash + observed_rpe_hash>
    output_hash: <constraint_results_hash>
  - stage: semantic_diff
    output_hash: <diff_hash>
  - stage: verdict
    output_hash: <verdict_hash>
  - stage: repair_svp
    output_hash: <repair_svp_hash>
```

## 11. アーキテクチャ

### 11.1 Framework / Domain 分離

```
svp-rpe-code/
├── framework/              # ← モダリティ非依存（音楽版と将来共通化）
│   ├── target_svp.py       # 仕様モデル
│   ├── constraint_types.py # state / delta / repair の型定義
│   ├── operators.py        # typed operator 実装
│   ├── expected_compiler.py
│   ├── constraint_evaluator.py
│   ├── diff.py             # CodeStateDelta + Semantic Diff
│   ├── repair_compiler.py
│   ├── verdict.py
│   └── hash_trail.py
├── domain_code/            # ← コードドメイン固有
│   ├── state_schema.py     # CodeState + CodeStateDelta
│   ├── change_kind_templates.py
│   ├── languages/
│   │   ├── python/
│   │   │   ├── api_surface.py    # griffe wrapper
│   │   │   ├── type_diff.py      # mypy/pyright wrapper
│   │   │   ├── effect.py         # AST + effect_db
│   │   │   ├── complexity.py     # radon wrapper
│   │   │   ├── imports.py        # ast wrapper
│   │   │   └── test_surface.py   # pytest collect wrapper
│   │   └── typescript/           # P3 以降
│   │       └── ...
│   ├── effect_db/
│   │   ├── python_io_apis.yaml
│   │   ├── python_net_apis.yaml
│   │   └── typescript_*.yaml     # P3 以降
│   └── rules/
│       ├── change_kind.yaml      # refactor/feature/fix 分類規則
│       └── repair_priority.yaml
├── adapters/                # ← 外部統合（P3+）
│   ├── github_action.py
│   ├── claude_code.py
│   └── cursor.py
└── tests/
    └── fixtures/
        ├── feature_clean/
        ├── feature_with_breaking_change/
        ├── refactor_clean/
        ├── refactor_violates_lock/
        ├── bugfix_minimal/
        └── bugfix_over_changed/
```

### 11.2 framework/ は将来音楽版と共通化する

`Baseline RPE` 概念・`StateConstraint / DeltaConstraint / RepairConstraint` 型システム・`unknown_policy`・`evidence chain` などは modality 非依存。Code Edition で確立後、音楽版に逆輸入する。

## 12. フェーズ計画

### P1: Python Static Semantic CI MVP（3–4 週）

- Python のみ
- 静的特徴のみ（coverage は除外）
- 抽出: `api_surface`, `type_surface`, `imports`, `complexity`, `test_surface`, `effects_light`
- `effects_light` は AST + import/call pattern による保守的検出（Level 1 + 一部 Level 2）
- `Baseline RPE` + `Observed RPE` 両方向抽出
- `Target SVP` YAML パーサ
- `Expected RPE` compiler（change_kind テンプレート展開込み）
- typed `Constraint Evaluator`
- `CodeStateDelta`
- `Semantic Diff`
- `Repair SVP`
- CLI + JSON レポート
- `hash_trail`（extractor version 含む）
- fixtures 6 件で round-trip テスト pass

**Exit criteria**: fixtures 全件で verdict 安定 + 決定論テスト pass + hash trail が再現可能

### P2: Python Repair Core Completion（3–4 週）

- effect_db 拡張（resolution Level 2 / 一部 3）
- 部分 CFG / 簡易 data flow
- `repair_order` の優先順制御
- `reduce` / `defer` / `lock` の完全実装
- Markdown レポート
- snapshot tests（fixture diff の自動検出）

### P3a: GitHub Action 配布（Python only）（2–3 週）

empirical alignment データ収集を急ぐため、TypeScript 対応より先に実 PR で回す。

- GitHub Action として配布
- 実 PR 100 件で人間 reviewer 判定 vs ツール verdict の比較データセット構築
- 一致率 / 不一致パターンを分析しルール調整

### P3b: TypeScript Edition（4–6 週）

- `ts-morph` ベースの extractor 一式
- TypeScript extension schema
- TypeScript fixtures
- 言語横断スキーマの妥当性検証

### P4: CI Integration の本格化

- Codeberg 対応
- manifest runner（複数 PR / 複数 artifact 一括処理）
- artifact upload
- exit code policy の本番運用

### P5: Generator Adapter

- `Repair SVP` → generator-specific prompt patch の compiler
- Codex / Claude Code adapter
- 生成 → 観測 → 修復 → 再生成の自動ループ

## 13. 範囲外（P1 で **やらない** こと）

意図的に除外:

- coverage 計算（dynamic 拡張、P3+）
- 完全な CFG 解析（P2）
- 完全な data flow 解析（P2+）
- behavioral correctness 証明（範囲外、Rice 定理）
- 自動コード patch 生成（範囲外、generator の責務）
- 多言語同時対応（P3 以降）
- LLM 補助 critique（別 layer、advisory tier）
- spec 自動推論（P4 以降）

これらに手を出すと、Semantic CI の核（**spec → expected → observe → diff → repair**）が固まる前に普通の静的解析ツール開発に逸れる。

## 14. 検証戦略

### 14.1 8 層の検証

| # | 層 | 検証内容 | 検証手段 |
|---|---|---|---|
| 1 | extractor unit test | 各抽出器が正しく動く | 既知 input → 既知 output |
| 2 | fixture test | エンドツーエンドで verdict が正しい | 人間が作った before/after + 期待 verdict |
| 3 | determinism test | 同入力で同出力 | 自分自身を 2 回走らせて bit-equal 比較 |
| 4 | round-trip test | Observed と Expected が同じ state space | 実コード → Observed → 仕様化 → Expected → 一致 |
| 5 | mutation test | gate が違反を検知する | passing fixture を意図的に破壊 → 落ちるか |
| 6 | anti-mutation test | gate が false positive を出さない | 無害な変更（コメント等）→ pass のままか |
| 7 | human alignment test | 人間判断と一致する | 実 PR 100 件で人手分類と比較 |
| 8 | self-application | 自分自身に当てる | Code Semantic CI のコード自身を gate |

### 14.2 regress の底

「検証器を検証する検証器を…」という無限後退は 3 つで止まる:

1. **fixture（人手で作った ground truth）** — `tests/fixtures/*/expected_verdict.yaml` に人間が宣言
2. **決定論性（数学的性質）** — 外部 ground truth 不要、自分で確認可能
3. **ルールが人間可読** — `change_kind.yaml` 等を人間が直接 audit

### 14.3 LLM-as-judge を ground truth に使わない

便利だが、これをやると:

- LLM の癖がツールに転写される
- 非決定論性が ground truth に混入
- 監査可能性が消える

**人間 reviewer が ground truth、LLM はせいぜい補助**に留める。

## 15. 重要な設計判断（決定済み）

| # | 論点 | 決定 |
|---|---|---|
| 1 | spec 記述形式 | YAML（人手）+ JSON Schema（機械検証） |
| 2 | spec のスコープ | PR 単位 default |
| 3 | 粒度 | function / file / module の混在を許可 |
| 4 | 言語スキーマ | 共通スキーマ + 言語固有 extension |
| 5 | spec 著者 | 人手が default、推論は assist（P4+） |
| 6 | verdict tier | 3-tier (pass / repair / fail) |
| 7 | threshold | per-metric default、aggregate は fallback |
| 8 | extractor 実装 | 既存ツール wrapping のみ |
| 9 | LLM の役割 | 不使用（決定論維持）。critique は将来の別 layer |
| 10 | Constraint DSL | YAML + typed operator のみ。任意 Python 式 / 独自構文 DSL は非採用 |
| 11 | Constraint kind | state / delta / repair の 3 種を明示分離 |
| 12 | Baseline RPE | 必須（refactor / bugfix 検証に不可欠） |
| 13 | change_kind | 宣言制、自動推論は advisory のみ |
| 14 | unknown_policy | per constraint で fail / repair / warn / ignore |
| 15 | Repair SVP | コードを直接変更しない、指示のみ emit |
| 16 | hash trail | extractor version / interpreter version / config hash 全含む |

## 16. 既存プラットフォームとの関係

| 既存 CI | Code Semantic CI |
|---|---|
| 補完関係（置換ではない） | declared intent との整合性 layer |
| lint / type / test を実行 | これらの上に意味的整合性 gate を追加 |
| failure を見せる | failure を意味的に分類 + repair 指示 |

`lint pass + test pass + Code Semantic CI pass` の 3 段ゲートで PR を gate するのが想定運用。

## 17. 関連ドキュメント

- [`CLAUDE.md`](../CLAUDE.md) — リポジトリ全体の運用ポリシー
- [`AGENTS.md`](../AGENTS.md) — Claude × Codex 連絡プロトコル（Task Brief / Completion Summary）
- [`docs/architecture.md`](architecture.md) — 音楽版の三層設計（Code Edition の framework 層が将来共通化）
- [`docs/roadmap.md`](roadmap.md) — 全体ロードマップ

## 18. 次のアクション

本設計を Codex 実装に落とすため、以下の順で Task Brief を発行する:

| Brief | 範囲 | 想定 PR |
|---|---|---|
| **Brief 1** | schema 定義（`CodeState` / `CodeStateDelta` / `Constraint` 型 / `Target SVP` DSL JSON Schema） | `codex/code-semantic-ci-schema` |
| **Brief 2** | extractor 実装（Python のみ、6 次元） | `codex/code-semantic-ci-py-extractors` |
| **Brief 3** | pipeline 統合（compiler / evaluator / diff / repair） | `codex/code-semantic-ci-pipeline` |
| **Brief 4** | CLI + JSON report + fixture テスト | `codex/code-semantic-ci-cli` |

各 Brief 完了ごとに Claude が Completion Summary を review し、次 Brief を発行する。
