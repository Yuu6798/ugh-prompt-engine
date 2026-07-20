# WI0-b コマンド verbatim ログ

## 環境 probe

```
python --version
# Python 3.11.15

df -h .
# Filesystem      Size  Used Avail Use% Mounted on
# /dev/vda        252G  8.0G   30G  22% /
```

## melody 経路インストール

初回 `pip install -e ".[pitch]"` は `pretty-midi` の legacy `setup.py bdist_wheel`
が `AttributeError: install_layout` で失敗した（vendored setuptools._distutils に
Debian パッチの `install_layout` オプションが無いための既知の非互換）。
`SETUPTOOLS_USE_DISTUTILS=stdlib` で stdlib 版 distutils を強制して回避した:

```
pip install -e ".[pitch]"
# -> FAILED building wheel for pretty-midi (AttributeError: install_layout)

SETUPTOOLS_USE_DISTUTILS=stdlib pip install -e ".[pitch]"
# -> Successfully installed ... basic-pitch-0.4.0 ... numpy-1.26.4 (downgraded
#    from 2.4.6 due to tensorflow<2.15.1 pin) ... pretty-midi-0.2.11 ...
#    tensorflow-2.15.0.post1 ...
```

検証:
```
python -c "import basic_pitch; print(basic_pitch.__file__)"
# /usr/local/lib/python3.11/dist-packages/basic_pitch/__init__.py
python -c "from basic_pitch.inference import predict; print('ok')"
# ok
```

## melody レンダリング（決定論 performer, faithful take, transpose=0）

`svprpe perform` という CLI サブコマンドは存在しない（`svprpe --help` で確認 —
コマンド一覧に perform なし）。決定論的レンダリングは
`svp_rpe.roundtrip.harness.run_roundtrip` が内部で使っているのと同じ Python API
（`svp_rpe.perform.FAITHFUL_TAKE` / `perform()` / `wav_bytes()`）を直接呼ぶ
スクリプトで実施した。`FAITHFUL_TAKE = PerformanceStyle(name="faithful_take",
seed=12)` は transpose のデフォルト値 0 を継承しており、要求どおり
transpose=0 の faithful テイクである。

`wi0b_run/render_faithful.py`:
```python
from svp_rpe.compose.loader import load_composition_score
from svp_rpe.perform import FAITHFUL_TAKE, perform, wav_bytes

score = load_composition_score(score_path)
samples = perform(score, FAITHFUL_TAKE)
out_path.write_bytes(wav_bytes(samples))
```

実行（別プロセスで 2 回、決定論確認のため）:
```
python wi0b_run/render_faithful.py \
  examples/arrangement/midnight_signal/composition_score.yaml \
  wi0b_run/faithful_take_run1.wav

python wi0b_run/render_faithful.py \
  examples/arrangement/midnight_signal/composition_score.yaml \
  wi0b_run/faithful_take_run2.wav

sha256sum wi0b_run/faithful_take_run1.wav wi0b_run/faithful_take_run2.wav
# 両方とも: 4d8c83f67c1b2441e09fa84debdc47ec0131c1a13ee1b813b0ef55e874903e90
# -> 一致 (2/2)
```
以降の observe には `faithful_take_run1.wav` を `faithful_take.wav` としてコピーして使用。

## melody anchor observe（既存 committed fixture を使用）

package/manifest ペアの選定: `examples/arrangement/midnight_signal/expected/e2e_edm/performance_package.json`
の `inputs.identity_manifest.sha256` (`9ef82c9b490e2c2029f8047470e92b76f6dbd9cdb27983c6eabcaad5a143687a`)
が、実ファイル `examples/arrangement/midnight_signal/identity_manifest.yaml` の
実測 sha256 と一致することを確認済み（D-3 手前の事前チェック）。manifest に
`id: melody, domain: melody, artifact: identity/melody_notes.json` の anchor が
定義されている。

```
svprpe observe \
  examples/arrangement/midnight_signal/expected/e2e_edm/performance_package.json \
  wi0b_run/faithful_take.wav \
  --manifest examples/arrangement/midnight_signal/identity_manifest.yaml \
  -o wi0b_run/observed/wi0b_melody_observation_run1.json
# exit code: 0

svprpe observe \
  examples/arrangement/midnight_signal/expected/e2e_edm/performance_package.json \
  wi0b_run/faithful_take.wav \
  --manifest examples/arrangement/midnight_signal/identity_manifest.yaml \
  -o wi0b_run/observed/wi0b_melody_observation_run2.json
# exit code: 0

diff wi0b_run/observed/wi0b_melody_observation_run1.json wi0b_run/observed/wi0b_melody_observation_run2.json
# no diff (byte-identical); sha256 両方とも
# 4d7a53279c2524e15dd0cc983c81d7444217dff65236aa8b8625b300bbaacf25
```

## lyrics 経路インストール

```
SETUPTOOLS_USE_DISTUTILS=stdlib pip install -e ".[lyrics]"
# -> Successfully installed ... faster-whisper-1.2.1 ctranslate2-4.8.1
#    demucs-4.1.0 torch-2.13.0 ... (フル stdout: wi0b_run/pip_install_lyrics.log)
```
容量: install 前 28G free -> install 後 19G free (torch/demucs/CUDA wheel 群で
約 9G 消費。書込割当 30G 中の余裕内)。

## lyrics 境界スモーク（同じ instrumental wav に対して）

同じ `faithful_take.wav`（melody 計測と同一ファイル、instrumental）に対し、
lyrics extra 導入後の `svprpe observe` を 1 回実行（package/manifest/wav は
melody と同一入力。manifest 側に `id: lyrics, artifact: identity/lyrics.txt`
の canonical anchor が既にある）:

```
svprpe observe \
  examples/arrangement/midnight_signal/expected/e2e_edm/performance_package.json \
  wi0b_run/faithful_take.wav \
  --manifest examples/arrangement/midnight_signal/identity_manifest.yaml \
  -o wi0b_run/observed/wi0b_lyrics_smoke_observation.json
# exit code: 0
```

補足で、observe の lyrics anchor 測定（`match_lyrics` の要約統計のみ）には
segment 単位の `no_speech_prob` が含まれないため、同じ wav に対して
`svprpe extract --lyrics`（`LearnedAudioAnnotations.lyrics_transcription`,
schema_version 1.2）も 1 回実行し、seg 単位の `no_speech_prob` /
`avg_logprob` / `language_probability` を採取した:

```
svprpe extract wi0b_run/faithful_take.wav \
  --lyrics \
  -o wi0b_run/observed/wi0b_lyrics_extract.json
# exit code: 0
```

モデル: faster-whisper `small`（デフォルト、明示指定せず）、
`compute_type: int8`, `device: cpu`。Demucs `htdemucs_ft`（デフォルトの
vocal separation、`--lyrics-no-separate` は使わず）。両モデルとも初回
DL がこの呼び出し中に発生した（`~/.cache/huggingface/hub` に
`models--Systran--faster-whisper-small` と `models--adefossez--HTDemucs-ft`
が新規作成された。実測: `du -sh ~/.cache/huggingface` = 785M 合計、
2 モデル分の内訳は未分離）。

## Re-run（相対パス安定化・`date -u` 実測日時）

実測日時 (UTC): 2026-07-20T17:08:23Z

PR #199 レビュー Codex P2 対応（`results.md` §4 参照）。上記ログの
`generated_artifact.path` がこの計測を実施したコンテナのローカル
`/tmp/.../scratchpad` パスだった問題を是正するため、同一 wav（sha256 pin 一致
確認済み）に対しリポジトリ相対パスで `svprpe observe` / `svprpe extract --lyrics`
を再実行した。committed JSON の手編集はしない — 以下の CLI 実行の生出力で
差し替えている。

```
# 1. wav を決定論再生成（sha256 が初回計測時の pin と一致することを確認）
python examples/arrangement/midnight_signal/observed/wi0b_synth/render_faithful.py \
  examples/arrangement/midnight_signal/composition_score.yaml \
  /tmp/.../scratchpad/wi0b_rerun/faithful_take.wav

sha256sum /tmp/.../scratchpad/wi0b_rerun/faithful_take.wav
# 4d8c83f67c1b2441e09fa84debdc47ec0131c1a13ee1b813b0ef55e874903e90
# -> pin と一致

# 2. repo 内相対位置にコピー（非コミット・検証後に削除）
cp /tmp/.../scratchpad/wi0b_rerun/faithful_take.wav \
   examples/arrangement/midnight_signal/observed/wi0b_synth/faithful_take.wav

# 3. melody 観測（相対パス）
svprpe observe \
  examples/arrangement/midnight_signal/expected/e2e_edm/performance_package.json \
  examples/arrangement/midnight_signal/observed/wi0b_synth/faithful_take.wav \
  --manifest examples/arrangement/midnight_signal/identity_manifest.yaml \
  -o <scratch>/wi0b_melody_observation_new.json
# exit code: 0

# 4. lyrics smoke 観測（相対パス、同じ wav・同じコマンド形）
svprpe observe \
  examples/arrangement/midnight_signal/expected/e2e_edm/performance_package.json \
  examples/arrangement/midnight_signal/observed/wi0b_synth/faithful_take.wav \
  --manifest examples/arrangement/midnight_signal/identity_manifest.yaml \
  -o <scratch>/wi0b_lyrics_smoke_observation_new.json
# exit code: 0

# 5. extract 証跡（no_speech_prob 系、新規収載分）
svprpe extract examples/arrangement/midnight_signal/observed/wi0b_synth/faithful_take.wav \
  --lyrics \
  -o <scratch>/wi0b_lyrics_extract_new.json
# exit code: 0
```

新旧 diff（`results.md` §4 に要約）: `melody` / `harmony` anchor は新旧で完全一致
（byte 単位）。`lyrics` anchor のみ差分があった —
`wi0b_melody_observation.json` は初回計測時に lyrics extra 未導入で
`sensor.available: false` だったが、本 re-run のコンテナには両 extras が
事前導入済みのため `available: true` に変わった（melody anchor 自体は無傷）。
`wi0b_lyrics_smoke_observation.json` / `lyrics_anchor_extracted.json` は
faster-whisper の別プロセス間非決定性により `no_speech_prob` /
`language_probability` / `overall_similarity` の実測値が変動した（境界挙動の
結論自体は不変）。`path` フィールド以外にも上記の差分があったため、committed
値はすべて本 re-run の実測値で統一した（`results.md` §4 に記録、隠さず報告）。

検証後、repo 内の一時 wav (`faithful_take.wav`) は削除し、未追跡ファイルなしを
確認した。
