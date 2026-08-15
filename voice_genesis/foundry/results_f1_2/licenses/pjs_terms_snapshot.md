# PJS (Phoneme-balanced Japanese Singing-voice corpus) 利用規約 スナップショット

- プロジェクトページ URL:
  https://sites.google.com/site/shinnosuketakamichi/research-topics/pjs_corpus
- 取得日: 2026-08-15（UTC、本タスク実行時に `curl` で直接取得。HTTP 200）
- 論文: Koguchi & Takamichi, "PJS: phoneme-balanced Japanese singing voice
  corpus," arXiv:2006.02959 (2020).
- 実配布物: ver.1.1（Google Drive, zip, 実測 275,179,158 bytes ≈ 0.256 GiB。
  プロジェクトページの表記「0.26 GB」と整合）。Drive ファイル ID
  `1hPHwOkSe2Vnq6hXrhVtzNskJjVMQmvN_`（`gdown` で取得。プロジェクトページ内の
  "Click here. [ver.1.1, Google Drive link, zip, 0.26 GB]" の "here" リンク先。
  同ページ内の別リンク `1NJ3_xuUFPRUfpI276yce1mcsHPpVdoCM` はサンプル単体 wav
  であり本体ではないことを確認済み・[実装決定・record]）。

## 逐語スナップショット（プロジェクトページ本文抜粋）

> Click here. [ver.1.1, Google Drive link, zip, 0.26 GB]

（"here" が Google Drive の zip 本体へのリンク。上記ファイル ID を参照）

> sample: "ところが、エリュシクトーンは、ニュムペーの制止も聞かずに、
> デーメーテールの樫を切り倒した"（singing_voice のサンプル音声リンクが併記）

## 逐語スナップショット（論文本文、arXiv:2006.02959）

Abstract:

> "CC BY-SA 4.0 license: All the data in our corpus is licensed with
> CC BY-SA 4.0. Therefore, our corpus is available for both research and
> commercial use, unlike existing corpora [...]"

Conclusion 節:

> "The PJS corpus is available on our project page [16]. All the data is
> licensed with the CC BY-SA 4.0 license."

## ライセンス解釈と本タスクでの attribution 方針

- ライセンス種別: **CC BY-SA 4.0**（コーパス全体）。研究・商用いずれも利用可
  （論文が明記）。
- **SA（ShareAlike）継承の attribution**: CC BY-SA 4.0 は改変・派生物の作成を
  許容するが、**派生物（本タスクの合成音声出力を含む）を頒布・公開する場合は
  同一ライセンス（CC BY-SA 4.0）での継承が必須**、かつ原著作者へのクレジット
  表示（Attribution）が必須。本タスクの出力（sakura/umi × pjs の WAV）は
  scratchpad に留めリポジトリへコミットしないため今回は「頒布」に該当しない
  が、将来これらの出力を公開・配布する場合は
  1) PJS コーパス由来である旨のクレジット（論文引用: Koguchi & Takamichi,
     arXiv:2006.02959）、
  2) 出力自体を CC BY-SA 4.0 で提供する旨の明記
  の両方が必要になる点を record（`f1_2_record_2026-08-15.md`）に明記する。
- 「合成音声そのものの生成・公開」を名指しした条文は論文・プロジェクトページ
  いずれにも見当たらない（survey 記載どおり、この点は明文不在＝SA 継承の
  一般原則で解釈するほかない）。
