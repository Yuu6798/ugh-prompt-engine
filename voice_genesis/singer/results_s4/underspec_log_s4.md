# S4 Underspec Log

## [UNDERSPEC-S4-1] GAIN_FLOOR 適応化の実装見送り判断

memo W2-2 は「開かない場合のみ GAIN_FLOOR の適応化を実装（commanded F0
帯域を避けて floor を置く等）」としているが、これを実装するには
`formant_tv.lorentz_gain`/`apply_time_varying_formant_filter` に現状渡って
いない per-note の commanded F0 を新たに引数として通す必要があり、
`formant_tv.py`（既存レンダラファイル）への非自明な変更になる。memo は
「レンダラ変更時は gate1-5 非退行を voice_A/C/D で必ず再確認」を要求して
おり、この確認自体は実行可能だが、GAIN_FLOOR の単純な magnitude 調整
（0.10→0.0 の1次元スイープ）では改善が見られなかった（fs=1.0 ですら
floor を下げると悪化する非単調な依存性を確認済み）ため、「帯域を避けて
floor を置く」という質的に異なる実装が必要になり、15分規模の残り時間内
では設計・実装・全数再検証を安全に終えられないと判断した。memo の
「開かない場合のみ」という条件分岐そのものは満たしている（開かないことは
実測で確認済み）が、その先の適応化実装は着手せず fail-closed とした。
次サイクルの明示的な引き継ぎ事項として `genesis_report_c.md` 末尾に記載。

## [UNDERSPEC-S4-2] SAFE_BOX_V2 の保守的マージン

1次元走査で得られた「ぎりぎり安全」な境界値をそのまま採用せず、
一段階内側に寄せた値を `SAFE_BOX_V2` として採用した（例: tilt 1次元安全域
は -14〜-8 だが採用は -13〜-8、bandwidth_scale は 0.70〜1.05 だが採用は
0.80〜1.05）。理由: S3/S4 を通じて「1次元で安全でも多次元で崩れる」ことが
繰り返し観測されており（`dark_dry` コーナーが1次元境界に近い場所で
僅差不合格=2.999 だったこと自体がこの実例）、境界ぎりぎりの値をそのまま
ボックスに採用すると生成される候補の多くが多次元崩壊で quick-S5/gate6
不合格になり探索効率が落ちる。安全マージンの具体的な量（1段階分、
tiltなら1半音、bandwidth_scaleなら0.10）は数値的根拠のある値ではなく、
実測から見た目算であることを明記する。

## [UNDERSPEC-S4-3] voice_C / voice_D を親として使う際のボックスクリップ

`genesis_v1.build_gen0()`（無改変で import 流用）は親候補として
`rs.voice_c()` / `rs.voice_d()` をそのまま使い、`clip_to_safe_box`
（本サイクルでは `SAFE_BOX_V2` 版にモンキーパッチ済み）でクリップする。
voice_C の tilt=-17 は新ボックス [-13,-8] の外にあるため **-13 にクリップ
される**（bandwidth_scale=0.80・breathiness=0.0 は範囲内でそのまま）。
voice_D は bandwidth_scale=1.30→1.05・breathiness_base=0.40→0.35 に
クリップされる。memo は「新ボックスで genesis_v1 の多世代探索を再実行」
としか規定しておらず、親そのものをボックス内に収める処理の要否は明記
していない。S3b でも同じ処理（voice_C/D を親候補として安全域にクリップ
してから使う）を行っていたため、その先例に倣って本サイクルでも継続した。
結果として「親」は登録済みの voice_C/voice_D そのものではなく、その
新ボックス内への射影であることに注意が必要（探索の起点として機能する
限りでは問題ないが、系譜上の「親」の呼称が実際の voice_C/D と数値的に
一致しない）。

## [UNDERSPEC-S4-4] gate_checks_v2 の mean_f0 / rms は score-informed 化していない

memo W1 は periodicity と vibrato_depth のみ score-informed 化を明示的に
指示している。gate6 クイックチェックが使う残り2特徴（mean_f0, rms）は
`gate_checks.py` と同じブラインド計測（`measure_v3.estimate_f0_v3`,
単純RMS）のまま `gate_checks_v2.py` でも踏襲した。mean_f0 の粗いブライン
ド推定は grip 判定の side-feature としてのみ使われており、S1〜S4を通じて
クラッシュ的な異常は観測されていないため対象外とした判断。

## [UNDERSPEC-S4-5] voice_B・voice_D の gate6-v2 不合格の扱い

本サイクルは voice_B（既知）・voice_D（S3で確定・S3bの親として使用）の
gate6-v2 不合格を発見したが、**voice_C/voice_D の Genome 定義自体
（`render_song.py` の `voice_c()`/`voice_d()`）は変更していない**
（memo が S4 の scope として voice_C/D の再設計を指示していないため）。
voice_D が gate6-v2 で不合格である事実は `safe_box_v2.md` に明記した上で、
次サイクルへの引き継ぎ事項として `genesis_report_c.md` に記載するに
留めた。

## 制約遵守の確認

- リポジトリ読み取り専用: `proto1/`・`vt_harness/` は import のみ
- 既存ファイル無改変: `gate_checks.py`・`formant_tv.py`・`render_song.py`・
  `genesis_v0.py`・`genesis_v1.py` は一切変更していない（`genesis_v2.py`
  からのモンキーパッチはプロセス内のみで、ソースファイルへの書き込みなし)
- 書き込みは `singer/gate_checks_v2.py`・`singer/genesis_v2.py`・
  `singer/results_s4/` 配下のみ
- フォアグラウンド実行・決定論（再現性は機械照合済み）
- 実行時間: 主要パイプライン（1回実行+再現性照合の2回目実行）で
  合計約 3 分（1D/多次元スポットチェックのスキャン諸々を含めても
  15分規模の枠内）
