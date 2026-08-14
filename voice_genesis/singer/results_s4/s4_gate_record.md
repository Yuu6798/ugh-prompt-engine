# S4 耳判定記録 — Genesis Graph v0 成立

- 日付: 2026-08-13
- 素材: `sakura_genesis3.wav`（複合 2.459 vs C / 2.950 vs D、
  フル gate1-5 + gate6-v2 全通過、linkability 合格、同一 seed 再現性照合済み）
- 判定者: User（人間聴取・非盲検）
- 判定者原文: 「今回の生成物はCともDとも人間の耳レベルで差分があることを
  確認した。成立だ」
- **判定: 成立** — 設計書 §5 の中核主張「Genome の分岐・評価・淘汰により
  未知の歌手を探索し、再現可能に固定できる」が、機械ゲート + novelty 監査 +
  系譜凍結 + 人間聴取の全チェーンで実証された

## JND 複合 ↔ 耳の較正表（最終版）

| 素材 | 複合 JND | 耳判定 |
|---|---|---|
| genesis2 vs C | 0.895 | 境界線上（微妙） |
| **genesis3 vs C/D** | **2.459 / 2.950** | **明確に識別** |
| voice_C vs D | tract 4.3 | 明確に識別 |

→ 識別床 2.0 は「明確」の下限として実用に耐えることが 3 点で較正された。

## 本セッションで成立した実証の全体像

1. 試作品 1 号（PoC）: 測定・監査・版管理の因果チェーン（VG-001〜010+016）
2. Phase 2: 日本語歌唱の成立（R0.9、耳判定）
3. S2: 設計による歌手の作り分け（voice_C/D、耳判定）
4. S3〜S4: **探索による新歌手の鍛造**（genesis3、耳判定）— 工房の中核機能

## 未決の引き継ぎ（次サイクル候補）

- voice_C/D の gate6-v2 正式再監査（voice_D は不合格転落を確認済み）
- 適応 GAIN_FLOOR の本格実装（formant_scale 復権の前提）
- formant_scale grip open issue（vt_harness v6）
- 実在話者 embedding / ABX 形式化（machine_dependent / not_observed のまま）
