# Session Memory Index

各セッションの 1 行要約。詳細は同ディレクトリの `YYYY-MM-DD.md` を参照。

- 2026-04-25: CLAUDE.md 整備 — svp-video-pipeline から汎用ポリシー移植、Architecture 同期、設計ドキュメント索引追加。Codex 自動レビュー 2 件 (Python 構文 / push policy 矛盾) 対応
- 2026-04-27: AGENTS.md / Workflow 節を新設し Claude × Codex × User 分業を明文化。roadmap_goal1.md (Q0–Q5) 策定。Q0-1 (合成 WAV 5 曲 + ground_truth + verify スクリプト, PR #9) 完了。Q4 Evidence-bearing Semantic Layer も PR #8 で実装完了（GPT-5.5pro 提案）。Codex P1 レビュー 2 件 (依存追加デッドロック / parser mojibake) 対応
- 2026-05-02: GitHub 凍結解除に伴う Codeberg → GitHub 移行。重複 7 本 / 未移行 4 本に切り分け、Q3-1/Q3-2 (PR #20)、Q3-3/Q3-4 (#21)、Q5-1 (#22)、Q3 stem validation (#23) をスタック PR で順次マージ。`compare --separate` no-op 削除（自己発見）、Codex bot P1+P2 (silent source auto-pass / stem tail loss) と User 指摘の short-stem-bundle 回帰テスト追加対応
- 2026-05-03: 長尺曲 OOM 問題の壁打ち → psutil 計測で `librosa.load` の二重呼び出しが原因と特定（PR #30 マージ）。提供アーカイブ `ugh_music_project_lite` の `DeltaECalculator` 移植前検証で「ΔE 用語衝突 / 0.85 下限クランプ飽和」を発見し移植不可と判定、現リポジトリの `compute_novelty_curve` を再活用する形で `dynamics_summary` + `dynamic_range_db` を新規実装（PR #31 マージ）。設計→実装→セルフレビュー→PR を Claude が直接担当する特例セッション
- 2026-05-03 (Session 2): AI 音楽生成の理論的ブレインストーミング。平均回帰 → 楽譜と演奏の分離 → 意味ベクトル抽出 → survivor 性 → 共同体機能 → 評価関数 → 端の定義/状態、と階層を 10 段降ろされてリポジトリの設計仮説に到達。`docs/ai_music_daw_vision.md`（542 行）を拡張検証トラックとして新設、PoC (1) を Q0 に統合する形でロードマップ更新。コミット 221058a を `claude/music-brainstorm-WHoUx` に push 済み（PR 待ち）
