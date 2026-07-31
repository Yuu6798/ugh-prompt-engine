# Recast Workspace — 概要（PR0–PR6 総括）

日付: 2026-07-22（`date -u` 実測確認済み）

## 1. 位置づけ

recast は既存 svp-rpe 研究計器（RPE 抽出 / SVP 生成 / ArrangementSpec 解決 /
compile / verify / observe の各層）を変更せず、その上に「編曲制作フロー」の
**製品受付層**を薄く被せるワークスペース定義（`recast-project/0.1`）。

- **内部は研究計器のまま**: `arrange/` 以下のスキーマ（`CompositionScore` /
  `IdentityManifest` / `ArrangementSpec` / `InputCapabilityProfile`）は一切
  変更しない。recast はそれらへの**参照のみ**を持つ（歌詞・旋律・楽譜本文の
  複製は禁止）
- **製品層が足すのは 3 つだけ**: (1) 既存 sidecar 一式を突き合わせる**受付**
  （`recast plan` の診断パイプライン）、(2) backend 呼び出しの**執行**
  （`recast run`/`ingest` の manual 注文書 / local invocation）、(3) 生成後の
  **検収**（`recast ingest` の observe→report、D-1 の被覆語彙で正直に報告）
- 単一の同一性スコアは出さない（`identity_assessment: {enabled: false}` は
  将来の閾値 Design Memo までの予約フィールド）。「約束するのは測定できる
  ものだけ」— 詳細は §5

## 2. recast-project/0.1 スキーマ概要

`project.yaml` 1 本が既存 sidecar への参照 + 実行方針を宣言する
（`src/svp_rpe/recast/models.py`）:

```yaml
schema_version: "recast-project/0.1"
project: { id: "...", builds_root: "builds" }
work: { score: "...", identity_manifest: "..." }        # CompositionScore / IdentityManifest への参照
variants: { <name>: { arrangement: "..." } }              # ArrangementSpec への参照（1 つ以上）
backends: { <name>: { capability_profile, invocation, invocation_mode, mode_overrides?, melody_take_band? } }
policy: { capability_mode, require_author_fields_resolved, require_verified_package }
observation: { enabled, anchors, melody? }                 # PR5/PR6: 観測スコープ（§4）+ M4: melody experimental（§8）
```

`invocation` は `manual`（外部生成器へ注文書を渡す）/ `local`（in-process
演奏者を直接呼ぶ）の二択。`invocation_mode` は `cover`（参照音声からのカバー
生成）/ `prompt_only`（テキストのみ）の二択で、`backends.<name>.mode_overrides`
（`mode-overrides/0.1`、任意）と組み合わせて §4 の invocation_mode 軸を計測する。
`backends.<name>.melody_take_band` / `observation.melody` は M4（additive、既定
省略）——melody anchor を experimental として観測する場合のみ宣言する（§8）。

## 3. 状態機械

`recast_state.json`（`src/svp_rpe/recast/state.py`）が `(variant, backend)`
ごとに追跡する到達状態:

```
draft → authored → compiled → verified
                                  │
                    ┌─────────────┴─────────────┐
                    │ local invocation           │ manual invocation
                    ▼                             ▼
                generated               awaiting_generation
                    │                             │ (外部生成 + ingest --audio)
                    │                             ▼
                    │                        generated
                    └──────────────┬──────────────┘
                                   │ (observation.enabled=true の ingest のみ自動継続)
                                   ▼
                               observed → reported

失敗系（正常系から分岐する終端）:
blocked_authoring / blocked_capability / blocked_verification /
generation_failed / observation_incomplete
```

local invocation の `recast run` は `generated` までしか進めない（PR5 の
observe→report 自動化は manual backend の `ingest` 限定 — §6 golden path が
local backend 分の report をどう作るかを示す）。

## 4. CLI フロー

```bash
svprpe recast init <audio> --project-dir <dir>          # 音源から project 雛形を生成（対話式 semantic.core/avoid）
svprpe recast plan  <project.yaml> --variant V --backend B  # 受付: 診断表 + recast_plan.json + state 記録
svprpe recast run   <project.yaml> --variant V --backend B  # 執行: manual=注文書 6 ファイル / local=実生成
svprpe recast ingest <project.yaml> --variant V --backend B --audio <file>  # manual 執行後の収蔵（+観測→report）
svprpe recast status <project.yaml>                       # 全 (variant, backend) の到達状態 + 次の一手
```

`plan`/`run`/`ingest` は毎回正典 `recast_plan.json` を
`<builds_root>/plans/<variant>@<backend>/`（packages/orders/takes/reports と
同じ per-(variant, backend) 命名規約）へ publish し、`inputs_digest`/
`plan_sha256` をこの正典ファイルへ pin する。`ingest`/`status` はこの pin を
`awaiting_generation` を信用する前に fail-closed 突合する（stale 検出）ため、
別の (variant, backend) に対する `plan`/`run` を実行しても無関係の run は
stale 化しない。project 直下の `recast_plan.json` は「最後に評価した
(variant, backend) の診断」を映す便宜コピーとしても書き続けるが、pin・突合
対象ではない（2026-07-23 改訂: 従来は project 直下の単一ファイルを正典と
していたため、別 run の plan 実行だけで正当な `awaiting_generation` run が
stale 誤判定されていた — Codex P2 review, PR #212 指摘）。

`inputs_digest`（生成系: 注文書・音源の同一性）は意図的に `observation` 節を
除外している一方、`observed`/`reported` の run は別途 `observation_digest`
（`observation` 節の canonical digest）を pin する二層構成（2026-07-23 改訂・
Codex P2 review, PR #212 指摘）。`observation.enabled`/`observation.anchors`
編集後に `recast status` が該当 run を「stale（observation 設定が report 生成
時から変更）」と表示したら、`recast ingest` を**同一 take**（`--audio` に
既存 `<builds_root>/takes/<variant>@<backend>/take-01.*` をそのまま指定）で
再実行すると take を再収蔵せず observe→report だけをやり直せる（再観測。
`generated`/`observed`/`reported` いずれの状態からも実行可 — `awaiting_
generation` 専用の `orders_digest` 突合はこの経路では対象外）。

## 5. 「約束するのは測定できるものだけ」

D-1（`docs/wi1_d1_thresholds.md` 系列の裁定）を継承する:

- `RecastReport.identity_assessment` は `{"enabled": false}` の予約フィールド
  のみ — 複数 anchor の観測結果を単一スコアへ縮約しない
- 被覆（coverage）は anchor 単位で `verified` / `violated` / `not_observed`
  の 3 状態のみを報告する（`recast/report.py`）。「保存されている」と
  偽って報告しない — 計測できない anchor は正直に `not_observed`

### Phase 0 ゲート帰結（melody 除外・M4 で experimental 再導入）

PR4 の縦切り hard anchor は **melody を採用せず**、`harmony`
（`chord_progression` + chord-sequence センサー）+ `structure`
（`section_map` + structure センサー）の 2 本を柱とする。根拠は独立な 2 系統
の不成立実測:

- pyin（`melody_contour`）経路: 合成和音パッド音源に対しノート系列がほぼ
  返らない（1 曲あたり 1–4 音）— DTW/LCS 以前にアルゴリズムへ渡す入力系列が
  成立しない
- note_events（basic_pitch）経路: WI0-b（類似度 0.6 < 事前登録閾値 0.8）/
  WI2（弁別非成立）の既往実測で不成立

melody は recast 初版において D-1 既存語彙の `not_observed`
（determination `no_sensor`）として扱う。新語彙は導入しない。この現行挙動
（`_observe_melody` LCS・本会計）は **今も変わらず既定** ——除外の歴史は
維持される。**M4**（`docs/DESIGN_M4_recast_melody_anchor.md`）はこれとは別の
**experimental 会計**を additive に足す（§8）。melody を main の縦切りへ
昇格させる決定ではない。

### invocation_mode 軸（cover vs prompt_only の実測差）

`mode_overrides`（`mode-overrides/0.1`、`config/mode_overrides/suno.yaml`）は
「同じ generator でも invocation_mode が変わると同じ score field の override
がどれだけ届くか」の実測記録（`InputCapabilityProfile` とは別軸）。Cowork
実測 2026-07-17〜19（n=4×2、cover 4 本 + prompt_only 4 本）の帰結:

| field | cover | prompt_only |
|---|---|---|
| `physical.time_signature` | unsupported（4/4→3/4 override 0/4 反映） | experimental（3/4 override 2/4 成功、分散大） |
| `physical.bpm` | experimental（原曲テンポへスナップ傾向） | experimental（152bpm 指定 3/4 が着地、cover より届く） |
| `physical.key` | unsupported（原曲キーへ強くアンカー） | experimental（分散増・ジャンル慣用句の交絡） |
| `physical.valley_depth_target` | unsupported（cover にチャネル自体が無い） | unsupported（prompt_only でもチャネルが無い） |

cover は「原曲へのアンカーが強い＝override が届きにくい」、prompt_only は
「届きやすいが分散も大きい（ジャンル慣用句との交絡あり）」という非対称が
実測されている。`recast plan` の strict/advisory ゲートはこの support
ラベル（`unsupported`/`unknown`）を changed_fields 診断へ折り込む。

## 6. golden path の回し方

`examples/recast/golden_project/`（PR6）は 1 作品（score + harmony/structure
の 2 anchor manifest）× 1 編曲 × deterministic backend の 2 エントリ
（`deterministic`=local, `deterministic_manual`=manual）× 2 take の committed
fixture。CI 全経路回帰は `tests/test_recast_golden_path.py`
（`@pytest.mark.slow`）が担う:

1. take-01: `deterministic` backend で `recast plan` → `recast run`
   （local invocation, PR3 `DeterministicInvoker` 固定 style seed=12）
2. take-01 の観測: local backend は `recast run` だけでは `generated` までし
   か進まないため、テストは CLI ingest 尾部と同じ手順
   （`observe_generated_artifact` → `build_recast_report` → atomic publish →
   `record_state`）を直接呼んで report まで完走させる
3. take-02: `deterministic_manual` backend で `recast plan` → `recast run`
   （注文書 6 ファイル公開）→ 別 seed（99）の `PerformanceStyle` で
   `perform()` した音源を `recast ingest --audio` で収蔵（manual 執行の代役。
   `observation.enabled: true` のため ingest が observe→report まで自動継続）

committed 固定は「軽量成果物 + sha256 pin」方式（wav はコミットしない）:
`examples/recast/golden_project/expected/` に `plans/<variant>@<backend>/
recast_plan.json`（正典 per-run ファイル、両 backend 分をそれぞれ commit）/
注文書 6 ファイル/ 両 backend の `recast_report.json` + `recast_summary.md`/
両 take の sha256 pin（`expected/takes.json`）を commit し、テストが全経路を
実行して byte 一致（JSON/md）+ take sha256 一致を検証する。両 take とも
harmony/structure いずれも exact match せず coverage は
`{"verified": 0, "violated": 0, "not_observed": 2}`（決定論シンセ演奏者の
出力が正典進行/正典セクション系列と厳密一致しないため — §5 の「測定できる
ものだけ約束する」の実例）。

```bash
pytest tests/test_recast_golden_path.py -m slow -q   # 全経路回帰（数分）
```

## 7. `observation.anchors` 配線（PR6）

`project.yaml` の `observation.anchors`（非空リスト）は観測・レポートを
その anchor 集合へ絞り込む（空リスト = 全 anchor、既定）。実装は
`recast/report.py:build_recast_report` の `observation_anchors` 引数（純粋な
フィルタリング）。

未知 anchor id（identity manifest に存在しない id、typo/削除済み anchor 等）
は観測経路そのもの（`arrange/observe.py:observe_generated_artifact`）で
fail-closed する（Codex P2, #210 round 9 指摘11）: フィルタは「一致する id
だけ残す」実装のため、未知 id をそのまま通すと単に無視され、ゼロ anchor の
report/summary が「成功」として publish されてしまう — フィルタ適用前に
`anchor_scope ⊆ manifest anchor id 集合` を検証し、外れている id を列挙した
`ValueError` で拒否する。`recast ingest`（`cli/recast_cmd.py`）はこれとは
別に、コマンド冒頭（他の precheck 群 — `inputs_digest`/`plan_sha256`/
`orders_digest` — と同じ位置、すなわち take 収蔵・publish・state 記録の
**前**）でも同型の事前検査を行い、設定ミスを plain な Error + exit 1
（state は `awaiting_generation` のまま変更しない — 実行時の観測失敗用の
`observation_incomplete` とは区別）として扱う（Codex P2, #210 round 10
指摘13: 当初は take 収蔵/`generated` 記録の**後**に検査していたため、
typo 修正後に `awaiting_generation` から同じ take で ingest をやり直せ
なかった — この前倒しで解消）。**`recast plan`/`run` はこの検証を行わない**
— manual backend が注文書公開・`awaiting_generation` まで進めることを
優先し、観測スコープの妥当性は ingest 冒頭でのみ判定する。

## 8. melody anchor 配線（M4・experimental）

上位設計書: [`docs/DESIGN_M4_recast_melody_anchor.md`](DESIGN_M4_recast_melody_anchor.md)
（起動ゲート G1–G3・写像規則・PR 分割の正）。実装は `src/svp_rpe/recast/experimental.py`
（`recast/` 配下・`melody/` の凍結境界の外）。要点のみ、詳細は上位設計書参照:

- **スキーマ（additive・既定は現行維持）**: `arrange/contract.py:ContractAnchor.
  axis_policy`（軸単位の保持方針、`DOMAIN_AXIS_VOCAB` で melody={contour,
  interval,rhythm}）が opt-in トリガー——無ければ現行 `_observe_melody`
  （LCS・本会計）のまま。`recast/models.py:MelodyObservationConfig`
  （`ObservationConfig.melody`）が観測設定（reference=score|audio・比較
  registry 参照・route）、`BackendRef.melody_take_band` が backend 単位の
  帯域自己記述。
- **ゲート（機械判定・G1–G3）**: G1 校正（M3 registry が frozen）→ G2 帯域
  （校正済み集合 `{"clear_lead"}` のみ）→ G3 観測（M1 gate + M3 coverage）。
  いずれか不成立は `not_observed(reason)` へ正直に落ちる（G1 未成立を
  エラーにしない — M3d 校正実測は未完了のため現状は常に `not_observed
  (comparator_uncalibrated)`）。axis_policy が frozen 軸の外を指す場合のみ
  fail-closed error（load/実行時）。
- **写像（純関数・翻訳のみ）**: M3 の軸別 evidence（strong/weak/none/
  uncalibrated）を axis_policy（hard/elastic/free）へ照らし、D-1 語彙
  4 値（preserved/changed_within_policy/changed_outside_policy/
  not_observed）へ機械的に写す。新しい閾値・重み・単一スコアは一切作らない。
- **会計分離**: `RecastReport.experimental_anchors`（additive、空なら
  serialize に現れない）は melody 翻訳結果を載せるが、`coverage`
  （verified/violated/not_observed の分母）には一切算入しない——main の
  anchors/coverage は完全に無関係のまま。`recast plan` は生成前に
  「observability 見込み」を `warnings` へ 1 行足す（抽出はしない・G1/G2/
  config 不在のみ判定）。
- **golden E2E**: `tests/test_recast_m4d_melody_e2e.py`
  （`examples/recast/demo_project` を tmp_path へコピー+パッチ、既存
  `examples/recast/golden_project/` は無変更）が校正済み分岐（extractor
  注入で preserved/changed_within_policy/changed_outside_policy の 3 判定）
  + G1 不成立分岐（抽出が呼ばれないことも確認）+ 決定論を検証する。実 pyin
  抽出は Phase 0 縮退既知のためスコープ外（route_runner 注入で代替）。
