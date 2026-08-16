# DESIGN S2 — 工房一周（P3-min）: 補間声の耳ゲート実測

- 日付: 2026-08-16
- 位置づけ: `FOUNDRY_ROADMAP.md` S2（P3-min 工房一周）。S1 ゲート通過
  （`results_s1/s1_record_2026-08-15.md`「S1 ゲート判定」節、run3 40K checkpoint
  受け入れ = `s1_gate_40k_run3/s1_run3_40k_gate_record.md`）を受けた実行設計
- 状態: 完了（2026-08-16・一周達成・backfill 処方箋確定。総括評定 =
  `results_s2/s2_record_2026-08-16.md` §14）
- 参照: `VISION_evolution_theory_v0.1.md`（付録A VoiceGenome 最小スキーマ・§4
  推奨サブゲノム）、`s1_gate/gate_synth.py`（spk_embed 経路の実装）

## 0. 前提

S1 は「リツ + PJS の 2 話者を spk_embed 付きで 1 回の acoustic 学習に統合」した
（`DESIGN_S1_p2poc.md` §1）。run3 40K checkpoint
（sha256: `052376e81b1e4672bf676802ba4348283037f352bd622e0f8a9ad0ac514c5ce2`）の
export 済み acoustic 束は話者ごとの 384 次元 spk_embed
（`s1_ritsu_pjs_acoustic_v1.{ritsu,pjs}.emb`）を持つ。この 2 ベクトルが
**2 アンカー**（リツ = e_r・PJS = e_p）であり、S2 はこの spk_embed 空間上での
補間・摂動を第一の探索軸とする（`DESIGN_S1_p2poc.md` §1「spk_embed の補間 =
S2『2 アンカー間の補間候補』の第一軸がそのまま手に入る」がそのまま実装対象）。

実測: e_r・e_p の L2 ノルムはそれぞれ 1.2269 / 1.3936、コサイン類似度 -0.335
（直交に近く縮退なし。slerp が数値的に安定する条件を満たす）。

## 1. 目的

仮説の実測 = 「誰でもない声」第1号の鍛造:

- **仮説 α（Quality Floor）**: 補間声が S1 と同じ耳ゲート（土俵に乗るか）を通るか
- **仮説 β（弁別可能性）**: 候補間の差を User が耳で弁別できるか（niche が
  立つか — `VISION` 収載時レビュー注記「第二リスク」= WI2 実績を踏まえた
  懸念の直接検証）

両成立が `VISION_evolution_theory_v0.1.md` の実装解禁条件（Evolution Graph
Engine / MAP-Elites を実装しない、の解除トリガー）。

## 2. 方式

- **追加学習なし・GPU不要・全て決定論**（seed 固定）。run3 40K acoustic
  checkpoint はそのまま推論のみに使う（`gate_synth.py` の reflow 多話者
  acoustic 経路を read-only 流用。§3 参照）
- **補間空間**: run3 export 済み `spk_embed`（384 次元 float32）。e_r と e_p の
  2 点間で:
  - **線形補間 (lerp)**: `v(α) = (1-α)·e_r + α·e_p`、α ∈ {0.2, 0.5, 0.8}
  - **球面線形補間 (slerp)**: 方向は単位球面上で球面補間、大きさ（ノルム）は
    線形補間して乗じる（`v = slerp_dir(α)·((1-α)·|e_r| + α·|e_p|)`）。
    α=0.5（中点）のみバッチ1で使用
- 各埋め込みで**さくら**を合成（`voice_genesis/singer/score.py`）。
  seed=42・reflow sampling steps=20・depth=`min(1.0, dsconfig max_depth)` は
  S1 gate と完全同一（`gate_synth.py` の定数・run3 record 実測値を踏襲。
  acoustic.onnx / dsconfig.yaml / canon linguistic・dur・pitch・vocoder は
  run3 の export 済み束をそのまま再利用し、変更しない）

## 3. 実装方針

`s1_gate/gate_synth.py` は変更しない（read-only 流用）。CLI 経由では
`--speaker {ritsu,pjs}` の 2 択しか spk_embed を選べないため、scratchpad に薄い
スクリプト `s2_forge.py` を作り、`gate_synth.py` を **モジュールとして import**
して `run_pipeline()` を直接呼び出す（linguistic/dur/pitch/acoustic/vocoder の
5 セッション構築・二重符号化・reflow spk_embed 配線はすべて `gate_synth.py` の
実装をそのまま使う。補間ベクトルの計算と WAV 書き出し・sha256 記録だけを
`s2_forge.py` が担う）。これにより「補間声の生成」は S1 の実装から一切分岐
しない。

`s2_forge.py` はリポジトリへコミットしない（scratchpad 完結。既存の
`s1_gate_5k`/`s1_gate_40k*` runbook 実行記録と同じ運用規約）。

## 4. バッチ分割判定

User 決裁 2026-08-16「一度の判定は4候補に絞って分割」（逐語）。総予算 10 体。

| バッチ | 内容 | 状態 |
|---|---|---|
| **バッチ1** | α = 0.2 / 0.5 / 0.8 の線形補間 3 体 + 中点 slerp（α=0.5）1 体 = 計 4 体・ブラインドラベル A〜D | 本ドキュメントと同時に実装 |
| バッチ2 | バッチ1の判定結果から設計（4体以内） | バッチ1判定後に設計 |
| バッチ3 | 最大2体・任意 | バッチ2判定後に要否判断 |

## 5. ブラインド規律

- 対応表（ラベル → α/摂動・seed）は **判定完了まで User に開示しない**
- `results_s2/s2_record_<date>.md` は生成条件・WAV sha256（候補ラベル単位）・
  対応表ファイル自体の sha256 のみを記載する（対応表の**中身**は記載しない）
- `results_s2/genome_ledger.json`（Genome台帳）はパラメータ（α・補間方式・
  seed 等）を voice_id 単位で保持してよいが、**候補ラベル（A〜D）や最終 WAV
  の sha256 は含めない**（ledger と record の双方に WAV sha256 を載せると、
  ハッシュ一致から対応表が逆算できてしまうため。台帳とラベル対応表を意図的に
  非交差に保つのがブラインド維持の実装）
- ブラインド用シャッフルも seed 固定・再現可能とし、その seed とシャッフル
  実装は非コミットの `s2_forge.py`（scratchpad 完結）にのみ存在する

## 6. 判定4問（User 耳判定・バッチ1完了後）

1. 歌声として成立するか
2. リツにも PJS にも聞こえないか
3. 候補同士は聞き分けられるか
4. 良いと感じたのはどれか

## 7. Genome 台帳

`results_s2/genome_ledger.json` に各候補を `VISION_evolution_theory_v0.1.md`
付録A.1 `VoiceGenome` 最小スキーマで記録する:

- `voice_id`: `VG-S2-001`..`VG-S2-004`
- `parent_ids`: `["VG-S2-ANCHOR-RITSU", "VG-S2-ANCHOR-PJS"]`（run3 acoustic の
  話者 spk_embed 2 本を疑似アンカー個体として参照）
- `backbone_checkpoint`: `sha256:052376e81b1e4672bf676802ba4348283037f352bd622e0f8a9ad0ac514c5ce2`
  （run3 40K acoustic ckpt）
- `identity_latent_ref`: 補間後ベクトルの sha256（`blob:sha256:...`）
- `mutation_ops`: `["INTERP_LERP_ALPHA_0.20"]` 等（方式+パラメータを機械可読に記録）
- `seed`: 42（合成 seed。ブラインドシャッフル seed とは別で、こちらは公開情報）
- `rights_class`: 学習データ由来を反映した合成注記
  （リツ原音 = 商用可・改変可、PJS = **CC BY-SA 4.0 継承**。補間声はこの
  2 系統の合成であるため、PJS 由来の share-alike 義務を保守的に継承する
  ものとして扱う — `SYNTHETIC_INTERP_PJS_CC_BY_SA_INHERITED`）
- `status`: `CANDIDATE`

## 8. Acceptance Criteria

**実績（2026-08-16・バッチ1〜3完了・§14 総括評定済み。詳細は
`results_s2/s2_record_2026-08-16.md` §14 を参照）**:

- [x] バッチ1の4体（lerp α=0.2/0.5/0.8 + slerp α=0.5）が合成され、
      `candidate_A.wav`〜`candidate_D.wav`（ブラインドシャッフル済み）として
      判定に付された（§5 判定確定）
- [x] `results_s2/genome_ledger.json` に候補が記録された（§7 スキーマ）。
      当初想定のバッチ1・4候補から、バッチ2（4候補）・バッチ3（2候補）の
      追加設計・鍛造を経て計10候補（VG-S2-001〜010）+ アンカー2本へ拡張
- [x] `results_s2/s2_record_2026-08-16.md` に生成条件・WAV sha256・対応表
      sha256・決定論確認・RMS/非無音確認が記載された（バッチ1〜3全件。
      判定4問も全バッチで判定確定・開封・分析済み — §5/§9.4/§13.1）
- [x] α×β の判定確定（バッチ1〜3の User 耳判定・開封・分析を完了し、§14
      S2 総括評定で立ち上げ4条件を①工房一周・②耳成立・③2個体識別=達成、
      ④「誰でもない」=部分達成と評定。2026-08-16 付で一周達成として
      クローズ。④の完全達成（「第三の声×破綻なし」の同時達成）は §14.2 の
      backfill 処方箋（(a) D3 リツ長母音データ投入 → (b) vocoder 差し替え
      → (c) 自前 variance 学習、優先度順）完了後の S2 候補再判定へ持ち越し）

## 9. Scope

- IN: scratchpad `s2_forge.py`（非コミット）・`results_s2/` record・genome
  ledger・ブラインドシャッフル
- OUT: `src/svp_rpe/**`・`s1_gate/gate_synth.py` の変更（read-only 流用のみ）・
  追加学習・GPU 実行・Evolution Graph Engine / MAP-Elites 実装
  （`VISION` の解禁条件が本バッチで満たされるまで）
