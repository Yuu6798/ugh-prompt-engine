# Foundry Roadmap — Synthetic Donor 工程（Phase 0–4）

- 日付: 2026-08-15（User 提案・同日承認。GPU 前提 = クラウドスポット確定）
- 位置づけ: F1.x unit-selection 梯子の凍結（`results_f1_4/` 耳判定・3 連続「改善あり・
  未達」= 銀行情報密度の構造限界）を受けた工程再編。設計書 v0.2 の staging
  （R0 → §1.5d テクスチャ事前分布 → R1–R3 学習レンダラ → Phase 5 Genesis）への
  復帰 + データ規模の桁上げ

## Phase 地図

| Phase | 内容 | 実行環境 | 設計書 v0.2 対応 | ゲート |
|---|---|---|---|---|
| **P0 Procedural Voice** | 物理/信号処理による大量生成 + リファレンスデータセット構築（ラベルが構成上真） | 本環境（CPU）| R0・VG-002/003 | `DESIGN_P0_reference_dataset.md` の Acceptance |
| **P1 Open Human Reference** | 公開歌唱データから「人間らしさ」を特徴量として学習（P0 手続き群との分布対照） | 本環境（CPU・軽量学習まで） | §1.5d | 特徴量が手続き/人間を分離できること（P0 データで検証） |
| **P2 Synthetic Donor Generator** | DiffSinger 系の自前学習 → 数十万〜級の Synthetic Donor 生成 | **クラウド GPU**（PoC: 4090 スポット $0.3–0.5/h・1〜3 日・$20–40。本命: 24GB 級数週 or A100 数日） | R1–R3 + **§8 RQ ゲート** | PoC = パイプライン一周（学習→ckpt→生成→P3 取込）。vocoder は community ckpt 流用/fine-tune（自前学習しない） |
| **P3 Synthetic Voice Manifold** | voice_genesis 本体: manifold 構築・Genome 航行・系譜 | 本環境 | Phase 5 Genesis + §7.5 | 立ち上げ 4 条件（工房一周/耳成立/2 個体識別/ベンチ比較） |
| **P4 Human Calibration** | 埋まらない部分のみ少人数・短時間の人間収録 | User | WI3 型 | 最後の手段（User 方針） |

## 構造的な利得と留意

- **identity の重心移動**: Synthetic Donor からの鍛造により「誰でもない」が監査対象から
  **構成上の性質**へ。ただしリスクは消えず **P2 の学習データへ移動**する（生成器は学習
  歌手の声を記憶・漏出しうる）— §8 Residual Identity Quarantine と linkability 監査の
  適用先が「出力」から「生成器 vs 学習歌手」へ変わる。設計書 §8 をそのまま適用
- **F1.x 資産の転職**（廃棄ではない）:
  - F1.4 VCV レンダラ → **P2 の学習データ工場**（score/音素タイミングが構成上真の
    アラインメント付き合成歌唱を無限生成。リツ規約で license-clean）
  - adapter 計測/決定論/record 規律・耳ゲート階層（声→言語→精度）→ P0–P3 の評価系
  - R0/R0.9/harness → P0 の生成器群
- **賭けの再定式化**: ベンチ（DiffSinger）は競合から**部品**になる。賭けは
  「学習合成の上に、可読 Genome・系譜・novelty 監査付きの航行可能な manifold を
  立てられるか — 素の DiffSinger に対し『誰でもない + 運転できる』を上乗せして
  歌手水準を維持できるか」へ
- **学習データのライセンス**: 実歌唱は PJS（CC BY-SA・27 分）を仕上げ用に、
  量は合成（F1.4/リツ + P0 手続き）で賄う 2 段構え。vocoder checkpoint 流用時は
  都度ライセンス精査

## 実行順

P0（本環境・即時）→ P1（本環境）→ P2 PoC（クラウド・$20–40）→ P2 本命判断（User）
→ P3 → P4。P0 と P1 は同一データ基盤の上で連続。
