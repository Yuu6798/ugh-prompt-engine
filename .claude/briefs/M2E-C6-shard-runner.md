# Design Memo: M2E-C6 — シャード実行機（§8.6「1回の実行の契約」）

## Phase

melody M2e（`docs/DESIGN_M2e_vremix_real_bed.md` rev.6 §8.4–§8.8 /
`docs/measurements/m2e_2026-08/HANDOFF.md` §2 C6）。STATUS.md queue
「M2e r4–r7 実測」の前提コード。**r6（code change 厳禁）前に landing 必須**。
C5（census）とは混ぜない——本 Memo は実行機のみ（C5 を C2/C3 から分離したのと
同じスコープ判断）。

## Goal

設計 §8.6 の実行契約（1回 = 1シャード）を `scripts/run_melody_accuracy.py` に
実装する: **シャード地図の生成器**（§8.5 の凍結アルゴリズム）と**消費器**
（`--shard-id` 起動・動的キュー・開始許可式・ハング打ち切り・昇順強制）。
帯セル 1280 件の実測（r6）が本成果物だけで回せる状態にする。
**コードのみ。実測（r4–r7）は含まない**（machine-dependent・Codex/User 側）。

## 前提となる設計判断（決定済み — 変更する場合は escalation）

1. **地図生成器も C6 に含める**。§8.5 は「自由変数なし・入力が同じなら出力は一意」を
   要求しており、r5 の `m2e_r2_shard_map.yaml` 生成・commit を手作業にすると
   1280 行の手書き YAML になり規律が守れない。生成器は §8.5 の擬似コードの
   逐語実装であること（アルゴリズムの改良禁止）。
2. **campaign ファイル**（例: `docs/measurements/m2e_2026-08/m2e_campaign.yaml`）を
   新設し、生成器と実行機の共通入力にする。内容は**パスのみ**（各水準の
   external manifest / external fixtures の所在）。科学的パラメータは一切含めない
   （それは bars / fixtures / 地図側の責務）。読み込み時に fixtures を凍結 committed
   ファイルと digest 照合する。
3. **セル台帳の整列は §8.5 の字義どおり** `(bed_id, level, clip_id, arm, repeat_idx)`
   の lexical order。level 文字列の lexical 順はラダー表示順と異なる
   （`'+12dB' < '+6dB' < '-6dB' < '0dB'`）が、これを「直して」はならない——
   裁量の余地を作らないことが目的で、順序の美観は目的でない。
4. **shard モードは run report / verdict / census を出さない**。成果物は
   (a) store_A のセルレコード、(b) shard 実行記録（dated JSON）のみ。
   per-level の run report は、全セル完了後に既存の「1 水準まるごと」run が
   store_A から 100% resume して生成する（M2e report の単一水準不変条件を保つ。
   report 生成経路に変更なし）。
5. **ワーカーは multiprocessing（spawn context）+ initializer でモデルロード**
   （§8.4 の S = プロセスプール起動〜モデルロード完了、の定義と一致）。
   各ワーカーはスレッド 3 点固定（env 2 点は pool 起動前に fail-closed 検証、
   `torch.set_num_threads(1)` は initializer 内）。HANDOFF §3.1 のとおり 3 点目を
   欠くと stem の bit 一致が壊れる。

## Acceptance Criteria

- [ ] **生成器** `--make-shard-map`: 入力（`--t-direct` / `--t-stem` /
      `--startup-cost`（S）/ `--session-budget`（既定 7200）/ campaign ファイル /
      `--out`）から §8.5 の凍結アルゴリズムで地図 YAML を生成。
      同一入力 → **バイト一致**（テストで固定）
- [ ] 地図に記録: 入力値（S / T_direct / T_stem / B_session / cap）・セル台帳の
      digest（fixtures 4 本の sha256）・`cell → shard_id` 全対応表・`N_shards`・
      生成時刻（UTC ISO 8601）。台帳と一致しないセル集合（欠け・重複・余剰）は
      fail-closed
- [ ] `cap <= 0` または `cap < max(T_direct, T_stem)` → 生成せず S / T_* / P を
      添えて User 決裁差し戻しのエラー（§8.5）
- [ ] `N_shards > R_max = 12` → 生成せず §8.8 の 3 択（R_max 引き上げ / 環境変更で
      P 増 / clip 削減=User 決裁のみ）を提示するエラー。**自動縮退はしない**
- [ ] **実行機** `--shard-id N --shard-map PATH`: 当該 shard のセルのみ対象。
      先行 shard（`< N`）に digest 一致で完了していないセルが 1 つでもあれば
      fail-closed（昇順強制と「飛ばせるのは完了済み shard のみ」を同時に実装）
- [ ] 開始許可式: 新しいセルを開始するのは `elapsed + cost(cell) <= B_session` の
      ときのみ（cost は地図が記録した T_* から引く。テストはクロック注入で境界を検証）
- [ ] 実行中セルが `B_session + 600s` を超えたら打ち切り: **セルレコードを書かず**、
      shard 実行記録に「未完」として記録（失敗値を書かない・§8.6）
- [ ] shard 実行記録（dated JSON・atomic write）: `shard_id` / `env_digest` / `P` /
      完了・resume・打ち切り・未着手のセル数と一覧 / 所要秒 / 使用した T_*・
      B_session / 地図ファイルの sha256
- [ ] **resume 互換**: シャード実行機が書いたセルレコードを、既存の
      「1 水準まるごと」run phase が digest 一致で resume する（テストで実証。
      レコードの鍵・schema・digest 計算を既存経路と共有していることの証明）
- [ ] shard モードは run report / verdict / census を出さない。既存テスト全 green
      （逐次 pin `test_run_phase_clip_loop_stays_sequential_even_with_many_workers`
      を含む・既存セルレコード schema へのフィールド追加ゼロ）
- [ ] `ruff check .` pass / `pytest -q --tb=short` pass

## Implementation Approach

- CLI: 既存 parser に `--make-shard-map` / `--shard-map` / `--shard-id` /
  `--session-budget` / `--startup-cost` / `--t-direct` / `--t-stem` /
  `--campaign` を追加。`--evaluate` / `--census` とは相互排他（C5 の
  `_ARGPARSE_UNSET` 検査と同じ流儀で、無関係フラグの明示指定も拒否）
- セル測定は既存 `_measure_or_resume_external_clip_row` の per-clip 経路を
  per-cell（(bed_id, level, clip_id, arm, repeat_idx)）に呼ぶ。レコードパスは
  `_cell_store_record_path`、書き込みは `svp_rpe.utils.atomic_io`（新機構禁止）。
  これが resume 互換 AC の根拠
- 動的キュー: 親プロセスが §8.5 order で 1 セルずつワーカーへ配る（静的等分禁止）。
  admission 判定は親側の単調クロックで行う
- 打ち切り: 対象ワーカーを terminate → pool を畳んで shard を終了する
  （elapsed は既に B_session 超のため継続しない）。「超過は異常ではなく通常状態」
  ——エラー終了ではなく実行記録に事実を残して正常終了する
- テスト可能性: キュー / 許可式 / 打ち切りの機構は**セル測定 callable を注入可能**
  にする（spawn は monkeypatch を継承しないため、picklable なトップレベル fake を
  注入して機構をテストする）。fake backend での統合テストは P=1 の in-process 経路
  で行う。P 並列の bit 一致検証は r2-0 の実測手続（並列不変性ゲート）であり
  CI の責務にしない
- 各ワーカーは env_digest を再計算し、親の値と一致しなければそのワーカーの
  セルを開始しない（fail-closed）。セルレコードの env_digest 記録は既存経路のまま
- 永続成果物ゲート（AGENTS.md §8）: 地図・実行記録とも atomic write・既存ファイルの
  黙示上書き禁止（地図の再生成は明示 `--out` の別パスまたは明示上書きフラグ）・
  読み込み時 fail-closed 検証（YAML 重複キー拒否は既存流儀に従う）

## Risks

- **multiprocessing × テスト**: spawn はテストの monkeypatch / fixture を継承しない。
  注入 seam を最初から設計に入れないと「機構が実測でしか検証できない」状態になる
- **打ち切りの後始末**: terminate したワーカーの共有リソース（pool・queue）の
  クリーンアップ漏れでプロセスがハングしうる。context manager で管理する
- **レコード同一性の静かな乖離**: シャード実行機が測定引数（fixtures 束縛・
  bars digest・thread pinning）を 1 つでも既存経路と変えると、書いたセルが
  resume されず r6 で全セル再測定になる。resume 互換 AC のテストが防波堤
- **書き込み競合**: セル鍵がレコードパスを一意化するため同一セルの並行書き込みは
  設計上発生しない（昇順強制が同一 shard の並行実行も防ぐ）。新たな lock 機構を
  足さないこと
- 既存 run phase の逐次 pin・M2e report 単一水準不変条件・C5 census 経路を
  壊さないこと（shard モードは独立フェーズとして追加する）

## Test Strategy

- 単体: 生成器の決定論（バイト一致）/ 拒否 3 分岐（cap<=0・cap<max(T)・
  N_shards>R_max）/ 台帳不一致 fail-closed / lexical order の固定（level 文字列順を
  含むスナップショット）/ admission 境界（ちょうど B_session に収まる・収まらない）/
  打ち切り（注入 fake の遅延セル）/ 昇順 fail-closed / 完了済み shard の skip
- 回帰: resume 互換（shard 実行機のレコード → 既存 run が resume）/
  shard モードが report・verdict・census を出さない / セルレコード schema 無変更 /
  store_role は `run` のまま / 既存テスト全 green
- 既存テストへの影響: なし（スナップショット更新不要のはず。必要になった場合は
  設計逸脱のサイン——escalation）

## Scope

- IN: `scripts/run_melody_accuracy.py` / `tests/test_m2_accuracy_harness.py` /
  `docs/DESIGN_M2e_vremix_real_bed.md` §8.9.4 への実装ノート追記 /
  `docs/measurements/m2e_2026-08/HANDOFF.md` の C6 節・§5 レシピ更新 /
  campaign ファイル新設
- OUT（**edge case 対応でも破らない**）: 凍結値（余裕係数 0.85 / `R_max = 12` /
  B_session 既定 7200 s / 水準ラダー / `repeats_min`）・
  `m2e_accuracy_bars.yaml`・`m2e_bed_fixtures.yaml`・
  `m2e_vremix_fixtures_*.yaml`（セル台帳は不可侵）・
  **セルレコード schema（フィールド追加もバージョン bump も禁止**——resume 互換が
  正しさの根拠）・evaluate / census の経路・`src/svp_rpe/**`（`utils/atomic_io` は
  利用のみ、変更禁止）・既存テストの pin
- 未検出/低信頼の扱い: 打ち切り・未着手セルは**素直に欠落**（セルレコードを
  書かない・sentinel を置かない）。欠落の事実は shard 実行記録側にのみ載せる

## Schema Admission

該当なし（CompositionScore / PhysicalLayer に非接触）。

## Allowed Dependencies

なし（stdlib `multiprocessing`・既存 PyYAML の範囲。`pyproject.toml` 変更が
必要になったら escalation）。

## Required Outputs

- ブランチ名: `codex/m2e-c6-shard-runner`
- PR タイトル: `feat(m2e): シャード実行機 — §8.6 の実行契約（地図生成器 + shard_id 起動・動的キュー・開始許可式・打ち切り）`
- 期待する変更ファイル: `scripts/run_melody_accuracy.py` /
  `tests/test_m2_accuracy_harness.py` / `docs/DESIGN_M2e_vremix_real_bed.md` /
  `docs/measurements/m2e_2026-08/HANDOFF.md` / campaign ファイル

## Done When

- 上記 Acceptance Criteria が全て ✓
- CI green（`ruff check .` + `pytest -q --tb=short`）
- PR 本文が Completion Summary 規約に準拠（AGENTS.md §2）
