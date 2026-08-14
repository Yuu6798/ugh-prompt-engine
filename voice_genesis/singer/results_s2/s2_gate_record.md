# S2 識別ゲート判定記録

- 日付: 2026-08-13
- 問い: voice_C / voice_D が「別の歌手」に聞こえるか
- 判定素材: `sakura_voiceC.wav` / `sakura_voiceD.wav`
  （機械受け入れ 3 条件成立済み: 分離 4/4・tract 4.3 JND・S5 6/6 両声）
- 判定者: User（人間聴取・非盲検・単一聴取者）
- **判定: 成立**。判定者原文:
  「人間判定で識別差分ありと認める。前回よりはっきりと違う歌声とわかる
  音源になってる。成立だ。」
- 備考: 機械分離の 1 セル（E2 phrase1, margin +0.006）は薄かったが、
  人間判定が差分ありを支持したため、耳と計器の総合で成立と扱う。

## 引き継ぎ（S3 への入力）

- formant_scale は gate6 較正制約により identity 軸として未使用のまま
  → S3 で較正域拡張を試みる
- identity 対比の現行担体: tilt / bandwidth_scale / breathiness_base /
  register_gains / vibrato
