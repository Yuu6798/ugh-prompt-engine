# Session Memory Index

各セッションの 1 行要約。詳細は同ディレクトリの `YYYY-MM-DD.md` を参照。

- 2026-04-25: CLAUDE.md 整備 — svp-video-pipeline から汎用ポリシー移植、Architecture 同期、設計ドキュメント索引追加。Codex 自動レビュー 2 件 (Python 構文 / push policy 矛盾) 対応
- 2026-04-27: AGENTS.md / Workflow 節を新設し Claude × Codex × User 分業を明文化。roadmap_goal1.md (Q0–Q5) 策定。Q0-1 (合成 WAV 5 曲 + ground_truth + verify スクリプト, PR #9) 完了。Q4 Evidence-bearing Semantic Layer も PR #8 で実装完了（GPT-5.5pro 提案）。Codex P1 レビュー 2 件 (依存追加デッドロック / parser mojibake) 対応
- 2026-05-02: Codeberg → GitHub 移行、Q3/Q5 スタック PR (#20–#23) 順次マージ、stem 回帰テスト追加。[archive/2026-05/2026-05-02.md]
- 2026-05-03: 長尺曲 OOM を `librosa.load` 二重呼び出しと特定 (PR #30)、`compute_novelty_curve` 再活用で `dynamics_summary`+`dynamic_range_db` 新規実装 (PR #31)。[archive/2026-05/2026-05-03.md]
- 2026-05-03 (Session 2): AI 音楽生成の理論的ブレスト → `docs/ai_music_daw_vision.md` 新設、PoC (1) を Q0 統合。[archive/2026-05/2026-05-03.md]
- 2026-05-03 (Session 3): Q4'-6 Learned Output Validation Harness (PR #33/#34/#35)、昇格ゲート G1–G5 策定。[archive/2026-05/2026-05-03.md]
- 2026-05-26: 開発フロー移植 + Composition PoC 計画策定。PR #55（フロー移植 + `docs/composition_poc_planning.md`）/ PR #53（Q4'-8 pseudo-label consensus コンフリクト解決）マージ。discipline テスト 6 件・STATUS.md・AGENTS.md §6-8 新設。CI の click 依存不足も修正
- 2026-05-27: Composition Score プロダクトブリーフを上位文書として確立、PoC 計画を下流として刷新（8箇所の不整合解消）。壁打ちで理論的結論3点に到達（層間矛盾は表現、delta_e は仕様/structure は実装、PoC は全フィールド required）。次セッション: C1 タスクブリーフ発行 | [詳細](2026-05-27.md)
- 2026-06-02: ワークフロー再反転（設計=Claude / 実装=Codex, PR #58）。Composition Score MVP 完成 — C1 スキーマ + TargetSVP 変換 (PR #57)・C2 `svprpe compose` + ExternalPromptAdapter (PR #59) を設計→実装→レビュー→マージで完走。C1 レビュー P3（TargetSVP 正規化で順序/case 喪失）を C2 で回避（renderer 入力を CompositionScore に固定）。次: C0 / C3（audit）。[詳細](2026-06-02.md)
- 2026-06-02 (Session 2): 方向性壁打ち。観測トラックを「未完成→未検証」と再定義し並走可と判断。「AI を演奏者として突き詰める」を掘り下げ、**「パラメータは評価する値でなく制御する値」** へ重心を読み替え。新規 doc `controllability_poc.md`（制御トラック K 系列、grip=A/B 効果量）を PR #60 でマージ。Codex P2 を 4 件対応（スキーマ未定義 field / brightness センサー / key grip 一致率化 / ゼロ分散規定）。次: K0 最小方法実証（MusicGen bpm/brightness grip）。[詳細](2026-06-02.md)
- 2026-06-03: K0 grip ハーネス (PR #61) と Composition audit C0+C3 (PR #62) を設計→実装→レビュー→指摘対応→マージで完走。**audit=「裁判官でなく計器（制御盤）」**を貫徹（verdict/pass-fail なし、`_assert_no_outcome_keys` で固定）。定性↔数値は `semantic_rules.yaml` 閾値を目盛りに読み替え。bpm grip=74.98→tight。自動レビュー P2 群 + リペアブリーフ2本（audit 音源入力 / key 結合形格納）反映。loose end: `claude/daily-task-planning-U4vHb` の doc 磨き込み未マージ（破棄推奨）。[詳細](2026-06-03.md)
- 2026-06-12: セッション終了プロトコル移植 (PR #63)。wrap-up / new-brief skill 新設(8 ステップ手順・TTL 表・アンチパターン集を skill に集約、CLAUDE.md はポインタ化で 373→332 行)、SessionStart hook (リモート dev 依存自動インストール)、discipline テスト成熟化(fixtures self-test / CLAUDE.md 400 行 cap / README)。ゲートコマンドの `--no-cov` 破損も修正(pytest-cov 非依存)。[詳細](2026-06-12.md)
- 2026-06-12 (Session 2): Fable 一気通貫デモ 3 本マージ — C4 E2E(決定論演奏者で PoC 5 決定論パス実証, #64)、K1 grip 地図(dead の 2 分類=ツマミ死/センサー盲を発見, #65)、brightness 正規センサーを centroid へ再設計(dark≤1200/bright≥2500, 6 巡レビューで config 同期テスト等の恒久ガード新設, #66)。次: K2 / Q1-3。[詳細](2026-06-12.md)
