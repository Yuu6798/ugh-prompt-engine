# VoiceGenesis コンセプト動画（約32秒・1440×1440・60fps）

「瞬間の声から、紡ぐ声へ」を、1枚の「声のカード」で見せるデモ動画。
カード上端の輪郭 = その声のスペクトル包絡（実測）。分かれる・混ざる・縮む・戻る、を1ショットで見せる。

声はすべて `build_audio.py` で合成した概念用の音（Drive の実験データは参照のみ）。
子・孫は「低域／高域を交差点 2,520 Hz で継ぐ」配合規則で親の包絡から作る。

## 作り方

必要なもの: Python（numpy・soundfile・imageio-ffmpeg）、Node.js、Google Fonts へのネットワーク
（Geist / Noto Sans JP を読み込めないとレンダーは停止する）。

```bash
pip install imageio-ffmpeg
npm install                                # playwright（Chromium は npx playwright install chromium）
python build_audio.py                      # 声の合成・包絡実測・data.js / cue_sheet.json
node render.mjs stills stills 0 7.2 20.5   # 静止フレーム点検
node render.mjs frames /tmp/vgframes 4     # 240fps サブフレーム
mkdir -p out
FFMPEG=$(python -c "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())")
$FFMPEG -framerate 240 -i /tmp/vgframes/%05d.jpg -i audio/mix.wav \
  -filter_complex "[0:v]tmix=frames=4:weights='1 1 1 1',select='eq(mod(n\,4)\,3)',setpts=N/(60*TB),format=yuv420p[v]" \
  -map "[v]" -map 1:a -r 60 -c:v libx264 -preset slow -crf 16 -c:a aac -b:a 192k -shortest out/voicegenesis_demo.mp4
```

`index.html?preview` をローカルサーバで開くと音つきでループ再生（クリックで再生/停止）。

## 設計メモ

- 見た目はすべて `seek(t)` の純粋関数。スプリングは閉形式、ターゲットが変わる値はスプリングの和
- 操作・字幕のタイミングは各クリップの実測オンセット基準。声と声の間の無音は最大 0.386 秒
- ドラッグ中の音は、ハンドル位置と同じ式（`handle_hz` / `handleHz`）で交差点を動かした掃引音
- 最終フレーム＝最初のフレーム（レンダー元で画素差 0）
