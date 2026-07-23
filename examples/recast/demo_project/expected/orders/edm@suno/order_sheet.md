# Recast order sheet: edm@suno

- generator: suno
- invocation_mode: prompt_only

## 作業ディレクトリ（cwd）

このファイルおよび `next_command.txt` 内の相対パス（project 引数・`--audio`）はすべて **project.yaml のあるディレクトリ**（この注文書（`edm@suno/`）から見て `../../..`）を基準にしている。以下のコマンドは必ずそのディレクトリで実行すること。

## 手順（prompt_only: テキストのみで生成）

参照音声は使わず、`prompt.json` のテキスト + `lyrics.txt` の歌詞 + `section_tags.txt` のタグのみで生成してください。

## 保持すべき hard anchor

- lyrics (lyrics)
- melody (melody)
- harmony (harmony)

## mode_overrides 由来の注意（invocation_mode=prompt_only）

- physical.bpm (experimental): 152 bpm 指定 4 本中 3 本が 152 付近へ着地。cover よりは届くが 全数一致ではない。

## 出力音源の保存

生成した音源を `builds/takes/edm@suno/take-01.wav`（または `.mp3`）として保存し、project ディレクトリ（上記「作業ディレクトリ」参照）へ移動したうえで以下のコマンドで取り込んでください:

```
cd ../../..  # この注文書ディレクトリから project.yaml のあるディレクトリへ
svprpe recast ingest project.yaml --variant edm --backend suno --audio builds/takes/edm@suno/take-01.wav
```

