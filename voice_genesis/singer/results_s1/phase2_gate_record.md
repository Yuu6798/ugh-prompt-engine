# Phase 2 Stage Gate 判定記録

- 日付: 2026-08-13
- ゲート（設計書 §9）: 「簡単な歌詞・メロディで歌声として成立（日本語）」
- 判定素材: `sakura_voiceA.wav`（機械前提ゲート 6/6 通過済み）、
  参考 `sakura_voiceB.wav`（5/6、breathiness grip 非退行のみ未達）
- 判定者: User（人間聴取。本プロジェクト初の「耳上」観測）
- **判定: 成立（アノテーション付き）**。判定者原文:
  「アノテーションでは成立していると認める。別の歌手と言えるほどの差分は
  耳レベルではわからない（音程の違いはわかる）成立だ。」
- provenance: human-listening / single-listener / non-blinded
  （ABX 形式ではない。正式な聴取実験ではなく開発判定）

## 併記所見（次サイクルの入力）

voice_A / voice_B は Genome 上は別個体（modal / breathy archetype）だが、
**耳では「別の歌手」と識別できない**。これは設計書 §5.3 の警告
「backend / renderer の事前分布が強い場合、Genome 上の分散が音響上の
収束に潰される事態を観測側でしか検出できない」の耳による実観測にあたる。
→ 次サイクル対象: Genome → 音響写像の identity 帯域の知覚的分離度強化。
