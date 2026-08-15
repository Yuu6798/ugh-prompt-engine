# DESIGN P0 — リファレンスデータセット（Procedural Voice 大量生成）

- 日付: 2026-08-15
- 位置づけ: `FOUNDRY_ROADMAP.md` Phase 0。本環境（CPU）で完結
- 状態: 実装指示（Claude 完結ルート）

## 0. 目的（3 役を 1 つのデータ基盤で）

1. **計器較正 GT**: 生成パラメータが構成上真のラベル → 特徴抽出器（P1）の較正基準
2. **人間らしさの対照群**: P1 で公開歌唱（vocadito/PJS）と分布比較する手続き側の母集団
3. **P2 pretraining プール**: 学習の事前データ（合成 → 実データ仕上げの 2 段構え）

## 1. サンプルスキーマ `ref-sample/0.1`

```json
{"schema": "ref-sample/0.1",
 "id": "p0-<generator>-<seed>-<idx>",
 "generator": "r0|r09|world_param|vcv_ritsu",
 "params": {"...生成パラメータ全次元を逐語..."},
 "audio": {"sr": 24000, "dur_s": 3.0, "sha256": "...", "regen_cmd": "..."},
 "features": {"f0_median": 0.0, "f0_p10p90": [], "band_shares": {}, "hnr_db": 0.0,
              "world": {"sp_stats": "...", "ap_stats": "..."}},
 "license": "synthetic|CC-BY-4.0|ritsu-terms",
 "provenance": {"commit": "...", "generated_at": "..."}}
```

- **音声はリポ非コミット**（WAV 非同梱規約）。manifest（JSONL）+ 生成コード + seed を
  コミットし、決定論再生成可能性で担保。音声実体は scratchpad / 将来は外部ストレージ
- manifest は shard 分割（1 shard = 1 万行目安）

## 2. 生成器 4 系統（全て既存資産・read-only import）

| 系統 | 実体 | パラメータ次元（例） |
|---|---|---|
| r0 | harness/voice_r0.py（加算合成・5 声区） | tilt, decay, register 閾値, vibrato, F0 |
| r09 | singer/render_song*（フルスクラッチ歌唱） | Genome 全欄 + score 断片 |
| world_param | WORLD 直駆動（F1b glue 系譜） | フォルマント 4–6 極, 帯域幅, ap 帯域設計, f0 輪郭 |
| vcv_ritsu | F1.4 VCV レンダラ | voice_spec 全欄（warp/perf）+ score 断片 |

- 素材: 短フレーズ（1.5–5 秒）。score 断片はさくら/うみのフレーズ + 合成音列
  （音高・音価をパラメトリックに振る）
- サンプリング: 各系統でパラメータ空間を決定論 LHS（seed 固定の擬似 Latin Hypercube、
  `np.random.default_rng`）+ 境界値グリッド。被覆計画を manifest に同梱

## 3. 特徴量スタック（全て既存・決定論）

measure_bands（帯域比 + HNR）/ WORLD 分析統計（f0 系・sp/ap 要約）/
harness measure_v3 の主要指標。**特徴量は記録であり最適化目標ではない**（凍結事項）。
P1 でこのスタックを基底に「人間らしさ特徴量」を選定する。

## 4. 規模と予算（2026-08-15 改訂: 最小スケール原則・FOUNDRY_ROADMAP S3）

- **P0-min = 500–1,000 本**: LHS 大量生成でなく**軸別スイープ**（4 生成器 × 主要 ~10 軸 ×
  ~10 水準 + 反復）。較正 GT には掃引が乱数被覆に勝り、人間側対照（vocadito+PJS ≈ 140 件）
  が律速のため手続き側の増量は検定力に寄与しない
- 実行タイミングは S2（工房一周）の後（ROADMAP 実行順を正とする）
- スキーマと manifest は 10 万本級へそのままスケールする設計（shard 前提）— 増量は
  S4 で不足が実測された場合のみ（backfill 原則）
- 生成スループットを record に実測記録（増量時の所要見積りに使う）

## 5. Acceptance Criteria

- [ ] `voice_genesis/foundry/refset/`（新設）に generator/manifest/features のコード一式
- [ ] P0-min バッチ（軸別スイープ 500–1,000 本）が scratchpad に生成され、manifest（JSONL shard）が
  リポにコミットされる（音声非コミット・sha256 と regen_cmd で再生成可能）
- [ ] 決定論: 同一 seed で manifest が bit 一致（抜き取り 100 本の音声 sha 一致）
- [ ] 4 系統すべて非空・パラメータ被覆の要約統計が record に記録
- [ ] 特徴量が全サンプルに付与され、既知ケース（例: tilt を振ると band 比が単調応答）の
  サニティが録れている — **較正であり最適化ではない**
- [ ] `ruff check .` / foundry テスト全 green / singer 38 本非破壊
- [ ] `results_p0/p0_record_<date>.md`（統計・スループット実測・Open Questions）

## 6. Scope

- IN: `voice_genesis/foundry/refset/**`・`foundry/tests/`・`foundry/results_p0/`
- OUT: 既存 adapter/harness/proto1/singer のコード変更（read-only import のみ）・
  `src/svp_rpe/**`・pyproject.toml・帯域指標での生成パラメータ最適化
