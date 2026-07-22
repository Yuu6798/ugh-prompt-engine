# Recast order sheet: golden@deterministic_manual

- generator: deterministic
- invocation_mode: prompt_only

## 手順（prompt_only: テキストのみで生成）

参照音声は使わず、`prompt.json` のテキスト + `lyrics.txt` の歌詞 + `section_tags.txt` のタグのみで生成してください。

## 保持すべき hard anchor

- harmony (harmony)
- structure (structure)

## mode_overrides 由来の注意（invocation_mode=prompt_only）

- (実測記録なし)

## 出力音源の保存

生成した音源を `builds/takes/golden@deterministic_manual/take-01.wav`（または `.mp3`）として保存し、以下のコマンドで取り込んでください:

```
svprpe recast ingest project.yaml --variant golden --backend deterministic_manual --audio builds/takes/golden@deterministic_manual/take-01.wav
```

