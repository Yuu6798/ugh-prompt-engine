# VG-F0 レンダラ裁定スモーク — 生ログ

実行のみ・採否判定なし。SawSing / GOLF 同梱 checkpoint による実推論可否の実証。

前提: env_probe_a1.md (2026-08-15 00:40 UTC 実施) の続き。torch 2.13.0+cpu /
torchaudio 2.11.0+cpu は導入済み・再インストールしない。両 fork は
/workspace/yatingmusic/ddsp-singing-vocoders, /workspace/yoyololicon/golf に
clone 済み。

## 結果表（総括）

**Round 1（本節、下記）は未パッチ状態での実行のみ・全件 blocked-with-evidence
で終了。Round 2（パッチ適用リトライ）の結果表は「## Round 2」節冒頭を参照 —
SawSing 2 backend + GOLF 3 checkpoint、計 5 件の実推論に成功し WAV を生成
（`sawsing_sins_sakura_24k.wav` / `sawsing_sawsinsub256_sakura_24k.wav` /
`golf_golfff_p360.wav` / `golf_world_p360.wav` / `golf_ddsp_p360.wav`）。**

| バックエンド | 段階 | 成否 | 所要 | 出力ファイル |
|---|---|---|---|---|
| SawSing (Sins backend) | ライセンス同定 | 成功 | <1s | — (AGPL-3.0-only 確認のみ) |
| SawSing (Sins backend) | deps（gin-config/einops/local-attention） | 成功 | 数秒×3 | — |
| SawSing (Sins backend) | deps（fast_transformers, CUDA拡張必須） | **blocked-with-evidence** | dry-download 数秒 + 静的解析 + install試行1回(300s timeout) | なし |
| SawSing (Sins backend) | ckpt load | 未到達 | — | なし |
| SawSing (Sins backend) | 前処理（mel抽出） | 未到達 | — | なし |
| SawSing (Sins backend) | 推論 | 未到達 | — | なし |
| SawSing (SawSinSub backend) | deps〜推論 | **blocked-with-evidence**（Sins と同一原因: `ddsp/pcmer.py` L10 無条件 import） | 同上 | なし |
| GOLF | ライセンス同定 | 成功 | <1s | — (MIT 確認) |
| GOLF | submodule 取得 (`git submodule update --init`) | 成功 | 数秒 | datasets/, models/audiotensor/, models/lru/ |
| GOLF | deps（lightning/diffsptk/kazane/torch_fftconv/pysptk/jsonargparse/omegaconf 等） | 成功 | 数分（累計） | — |
| GOLF | 前処理（24kHz resample + `.pv` F0抽出） | 成功 | 数秒 | `golf_input/p360/p360_001_mic1.wav`, `p360_001_mic1.pv` |
| GOLF (golf-ff, 他6モデルも同一原因で同様) | ckpt load 手前（encoder backbone `models.unet` import） | **blocked-with-evidence** | 3回の predict 試行 + 直接 import 再現 | なし |
| GOLF (golf-ff / golf-ss / golf-v1 / ddsp / world / nhv / mlsa) | 推論 | 未到達（7モデル全件が同一 `torch.Any` バグで道連れと確認済み） | — | なし |
| 計測 (Part 4) | 入力素材 f0/LTAS 分析 | 成功（生成WAV 0件のため入力素材のみ） | 数秒 | `f0_bands.json` |

総括（Round 1 時点）: 本環境（CPU-only, nvcc/CUDA 不在）では SawSing・GOLF
いずれも**実推論 0 件**。両者とも「依存導入は完走」できたが、最終段で
「CPU 環境では原理的に存在し得ないモジュール (`fast_transformers.causal_product.causal_product_cuda`)
への無条件 import」（SawSing）と「vendor submodule の型注釈バグ
(`torch.Any` が新しい torch に存在しない)」（GOLF）という、**いずれも
コード改変なしには回避不能な、リポジトリ側の実装欠陥**でブロックされた。
必要パッチ内容は各 Part 内に記録済み（Round 1 時点は未適用。Round 2 で適用
→ 下記「## Round 2」節）。

## Part 0 — 開始時ディスク状態

```
$ date -u
2026-08-15T01:15:43Z
$ df -h /
Filesystem      Size  Used Avail Use% Mounted on
/dev/vda        252G   10G   28G  27% /
```

## Part 1 — ライセンス同定

### SawSing (yatingmusic/ddsp-singing-vocoders)

```
$ wc -c LICENSE
34523 /workspace/yatingmusic/ddsp-singing-vocoders/LICENSE
$ head -5 LICENSE
                    GNU AFFERO GENERAL PUBLIC LICENSE
                       Version 3, 19 November 2007

 Copyright (C) 2007 Free Software Foundation, Inc. <https://fsf.org/>
 Everyone is permitted to copy and distribute verbatim copies
$ grep -c pattern LICENSE
GPL count: 0
MIT count: 0
Apache count: 0
```

### GOLF (yoyololicon/golf)

```
$ wc -c LICENSE
1068 /workspace/yoyololicon/golf/LICENSE
$ head -5 LICENSE
MIT License

Copyright (c) 2023 Chin-Yun Yu

Permission is hereby granted, free of charge, to any person obtaining a copy
$ grep -c pattern LICENSE
GPL count: 0
MIT count: 1
Apache count: 0
```

**SPDX 判定**:
- SawSing: grep パターンはいずれも 0 件だが head -5 で `GNU AFFERO GENERAL PUBLIC LICENSE Version 3` と明記 → **AGPL-3.0-only** （grep パターンは "GNU GENERAL PUBLIC" 固定のため AGPL の "AFFERO" 挿入形は非一致。head 出力が正）
- GOLF: `MIT License` 一致 1 件、head -5 に `MIT License` `Copyright (c) 2023 Chin-Yun Yu` → **MIT**

## Part 2 — SawSing (yatingmusic/ddsp-singing-vocoders)

### 2.1 エントリポイント調査

`README.md` E節 (L53-64) より推論コマンド:
```
python main.py --config ./configs/sawsinsub.yaml \
              --stage inference \
              --model SawSinSub \
              --model_ckpt ./exp/f1-full/sawsinsub-256/ckpts/vocoder_27740_70.0_params.pt \
              --input_dir  ./path/to/mel \
              --output_dir ./test_gen
```
入力形式: **mel-spectrogram (.npy)**、wav ではない（README L54: "Synthesize audio
file from existed mel-spectrograms"）。

`main.py` L155-164 (`--stage inference` 分岐) が `solver.render()` を呼ぶ。
`solver.py` L14-64 `render()`: `path_mel_dir` 配下の `*.npy` を `traverse_dir`
で列挙 → `model(mel)` → `sf.write(..., args.data.sampling_rate)`。

`preprocess.py` `Audio2Mel`（L54-110）+ `__main__`（L168-199）が mel 抽出仕様:
- `sampling_rate = 24000`, `hop_length = 240`, `win_length = 1024`, `n_mel_channels = 80`
  (L175-178)
- `x, sr = sf.read(...); assert sr == sampling_rate`（L153-154）→ **入力 wav は
  事前に 24000Hz でなければならない**（リサンプルは preprocess.py 側でやらない）

`configs/sawsinsub.yaml` / `exp/f1-full/{sins,sawsinsub-256}/config.yaml` は
いずれも `data.sampling_rate: 24000`, `data.block_size: 240` で一致（preprocess.py
の hop_length と符合）。

`main.py` L139 (`get_data_loaders(args, whole_audio=False)`) は stage 分岐の
**手前で無条件に呼ばれる**。`exp/*/config.yaml` の `train_path`/`valid_path` は
存在しない NAS パス (`/volume/wayne-nas-ai-music/...`) だが、`data_cnpop.py`
`traverse_dir()`（L13-51）は `os.walk` ベースで非存在ディレクトリでも例外を出さず
空リストを返すため（実測: 下記 2.3 参照）、`--stage inference` 実行自体は
この不整合では失敗しないと判明（ブロッカーではない）。

チェックポイント（"最新" = ファイル名中の global_step 最大）:
- Sins: `exp/f1-full/sins/ckpts/vocoder_15090_70.0_params.pt`（vs `vocoder_11874_48.0*`）
- SawSinSub: `exp/f1-full/sawsinsub-256/ckpts/vocoder_27740_70.0_params.pt`（vs `vocoder_18502_48.0*`）
- `logger/utils.py` `load_model_params()`（L106-118）は `model.load_state_dict(torch.load(path_pt))`
  のみ実行 → README 通り `_params.pt` サフィックス版（state_dict 単体）を使う。

### 2.2 fast_transformers 依存確認

```
$ grep -rn "fast_transformers\|local_attention" /workspace/yatingmusic/ddsp-singing-vocoders --include=*.py
ddsp/pcmer.py:8:from local_attention import LocalAttention
ddsp/pcmer.py:10:import fast_transformers.causal_product.causal_product_cuda
ddsp/pcmer.py:280:                import fast_transformers.causal_product.causal_product_cuda
```

`ddsp/vocoder.py` (全モデル: SawSinSub/Sins/DWS/Full/SawSub 共通) は
`from .mel2control import Mel2Control` を無条件 import (L9) →
`ddsp/mel2control.py` L8 `from .pcmer import PCmer` も無条件 →
`ddsp/pcmer.py` L10 `import fast_transformers.causal_product.causal_product_cuda`
は **モジュールトップレベルで無条件**（L280 の try/except 版とは別物、L8-10 は
ガードなし）。つまり **推論経路は fast_transformers を（トップレベル import
として）必須とする**。pytorch-fast-transformers は本環境に未導入のため、
`local_attention` (opt-in 導入) とは別に対応要否を検証した。

### 2.3 依存導入ループ（ImportError 駆動）

```
$ python3 -c "from ddsp.vocoder import Sins"
ModuleNotFoundError: No module named 'gin'          → pip install gin-config (61KB) 成功
ModuleNotFoundError: No module named 'einops'        → pip install einops (66KB) 成功
ModuleNotFoundError: No module named 'local_attention' → pip install local-attention (+hyper-connections, torch-einops-utils; 数十KB) 成功
ModuleNotFoundError: No module named 'fast_transformers' → 下記 2.4 参照
```

### 2.4 fast_transformers ブロッカー（blocked-with-evidence）

`pip download --no-deps pytorch-fast-transformers` は成功（sdist 93.6KB、
500MB 超過なし）。しかし同梱 `setup.py` を静的検査した結果、以下が判明:

```python
# setup.py L31-36
@lru_cache(None)
def cuda_toolkit_available():
    try:
        call(["nvcc"], stdout=DEVNULL, stderr=DEVNULL)
        return True
    except FileNotFoundError:
        return False
...
# setup.py L120-179 (get_extensions 内)
    if cuda_toolkit_available():
        from torch.utils.cpp_extension import CUDAExtension
        extensions += [
            ...
            CUDAExtension(
                "fast_transformers.causal_product.causal_product_cuda",
                sources=["fast_transformers/causal_product/causal_product_cuda.cu"],
                extra_compile_args=["-arch=compute_50"]
            ),
            ...
        ]
```

本環境実測:
```
$ which nvcc ; echo $?
1  (未検出)
$ python3 -c "import torch; print(torch.cuda.is_available())"
False
```

すなわち `causal_product_cuda` 拡張は `cuda_toolkit_available()`（`nvcc` 存在
確認）が真の場合のみビルドされ、本環境（nvcc 不在・CUDA 不可）ではビルドされる
CPU 版 (`causal_product_cpu`) はあっても `causal_product_cuda` は **原理的に
生成不能**。`pip install pytorch-fast-transformers` を実行 (workaround 試行 1
回目、`timeout 300` で強制終了 = exit 143。ビルド自体は長時間化するがゴールが
setup.py 静的解析で確定済みのため 2 回目の試行 [完走待ち] は実施しなかった —
結果を変えない):
```
$ timeout 300 pip install --no-cache-dir pytorch-fast-transformers
(コンパイル継続中に 300s タイムアウトで強制終了。exit code 143)
```

`ddsp/pcmer.py` L8-10 は無条件 import であり、L276-283 のような
`try/except ImportError` ガード（`causal_linear_attention_noncuda` へのフォール
バック）が **付いていない**。CPU 環境で通すには pcmer.py L10 を
try/except で囲む変更が必須だが、これは `/workspace` リポジトリのソース改変に
当たるため実施しない。

**必要パッチ内容（未適用・記録のみ）**:
```python
# ddsp/pcmer.py L8-10 相当を以下に変更する必要がある
from local_attention import LocalAttention
import torch.nn.functional as F
try:
    import fast_transformers.causal_product.causal_product_cuda
except ImportError:
    pass  # falls back to causal_linear_attention_noncuda inside PCmer.__init__ (L276-283 で既にハンドリング済み)
```

**判定: SawSing (Sins / SawSinSub 両 backend) は本 CPU-only 環境で
blocked-with-evidence。** 段階 = deps（`ddsp.vocoder` の import 自体が
失敗するため、ckpt load / 前処理 / 推論のいずれにも到達不能）。
`local_attention`・`gin-config`・`einops` は正常導入済みで無関係と判明。

### Part 2 終了時ディスク状態
```
$ df -h /
Filesystem      Size  Used Avail Use% Mounted on
/dev/vda        252G   11G   27G  28% /
```

## Part 3 — GOLF (yoyololicon/golf)

### 3.1 submodule 初期化

```
$ git -C /workspace/yoyololicon/golf config url."https://github.com/".insteadOf "git@github.com:"
(exit 0, 出力なし)
$ git -C /workspace/yoyololicon/golf submodule update --init
Submodule 'datasets' (git@github.com:yoyololicon/pytorch-wav-datasets.git) registered for path 'datasets'
Submodule 'models/audiotensor' (git@github.com:yoyololicon/audiotensor.git) registered for path 'models/audiotensor'
Submodule 'models/lru' (git@github.com:yoyololicon/torchlru.git) registered for path 'models/lru'
Cloning into '/workspace/yoyololicon/golf/datasets'...
Cloning into '/workspace/yoyololicon/golf/models/audiotensor'...
Cloning into '/workspace/yoyololicon/golf/models/lru'...
Submodule path 'datasets': checked out '1e473a1be4e16c27d6a40a71fdcbea92cfc08f2f'
Submodule path 'models/audiotensor': checked out '7d3c66a77c3fe96c9eb8103a618135c993371038'
Submodule path 'models/lru': checked out 'b28b93eee72a0ceb2a372c402f68da6a09d122c9'
(exit 0)
```
成功。Part 3 は続行（blocked-with-evidence には該当しない）。

### 3.2 エントリポイント調査（README.md）

`README.md` "PESQ/FAD" 節（L59-65）が predict コマンド:
```bash
python autoencode.py predict -c {YOUR_CONFIG}.yaml --ckpt_path {YOUR_CHECKPOINT}.ckpt \
    --trainer.logger false --seed_everything false --data.wav_dir data/vctk \
    --trainer.callbacks+=ltng.cli.MyPredictionWriter \
    --trainer.callbacks.output_dir {YOUR_OUTPUT_DIR}
```
`ckpts/interspeech24/<model>/config.yaml` が各モデルの学習時 config
（`golf-ff`, `golf-ss`, `golf-v1`, `ddsp`, `world`, `nhv`, `mlsa` の 7 系統、
各 `checkpoints/` 配下に `.ckpt`）。

`autoencode.py`（22行）は `ltng.ae.VoiceAutoEncoderCLI`（LightningCLI ラッパ）
そのもの。`trainer_defaults` に `"accelerator": "gpu"` がハードコードされている
（L9）ため、CPU 実行時は `--trainer.accelerator cpu` を明示的に上書きする必要あり。

`ltng/data.py` L526- `class VCTK(M4Singer)`: `setup(stage="predict")` で
`VCTKInferenceDataset(wav_dir=..., split="test", f0_suffix=...)` を構築
（L546-550）。`VCTKInferenceDataset`（L250-291）は
`wav_dir.glob("**/*" + VCTKDataset.file_suffix)`（`file_suffix = "mic1.wav"`,
L244）で列挙し、各 wav の `f.parent.name.split("#")[0]` が
`VCTKDataset.test_folder_prefixes = {p360,p361,p362,p363,p364,p374,p376,s5}`
（L213-223）に一致するファイルだけが `split="test"` に入る。つまり
**入力ディレクトリ名を test 話者 ID のいずれかにし、ファイル名を
`*mic1.wav` で終える必要がある**（例: `p360/p360_001_mic1.wav`）。

`__getitem__`（L279-291）: `f0 = np.loadtxt(filename.with_suffix(".pv"))` で
同名 `.pv` ファイル（5ms フレーム period の F0 系列テキスト）を要求。
`scripts/wav2f0.py`（存在確認済み）が生成器: `--method dio`（pyworld 使用、
導入済み）で `frame_period=5ms`, `f0_floor=65`, `f0_ceil=1047` がデフォルト。
`VCTKInferenceDataset` 側は `sr // 200`（200 = 1000ms/5ms）で時間軸を再構成
するため **period は 5ms 固定が前提**。

`cfg/ae/vctk.yaml` より `sample_rate: 24000`, `hop_length: 240`
（encoder 側 STFT） → **SR 24000 必須**（SawSing と同じ）。

### 3.3 入力素材の準備

```
$ python3 -c "import soundfile as sf; print(sf.info('.../ab_sakura_voiceC_floor0.10.wav'))"
samplerate: 22050 Hz, channels: 1, duration: 24.583 s, PCM_16
```
soxr (`quality='HQ'`) で 22050→24000Hz にリサンプル
(`OUT/input_24k/sakura_24k.wav`, 589997 samples, PCM_16)。GOLF 用に
`golf_input/p360/p360_001_mic1.wav` として配置（`VCTKInferenceDataset` の
test 話者判定 `p360` + `*mic1.wav` サフィックス要件を満たすため。3.2 節参照）。
`scripts/wav2f0.py --method dio`（pyworld 使用、period=5ms 既定）で
`p360_001_mic1.pv` を生成（成功、1 file, 51619 bytes）。

### 3.4 依存導入ループ（ImportError 駆動）

```
$ python3 -c "from ltng.ae import VoiceAutoEncoderCLI"
ModuleNotFoundError: No module named 'lightning'  → pip install lightning (848KB) 成功
ModuleNotFoundError: No module named 'diffsptk'    → pip install diffsptk (309KB; 副産物として torchlpc, torchcomp, penn, huggingface-hub 等を同時導入) 成功
ModuleNotFoundError: No module named 'kazane'      → pip install kazane (8.9KB) 成功
ModuleNotFoundError: No module named 'torch_fftconv' → pip install torch_fftconv (8.3KB) 成功
→ import 成功
```
`scripts/wav2f0.py` 実行時に別途:
```
ModuleNotFoundError: No module named 'pysptk' → pip install pysptk (461KB sdist, ローカルビルド成功) 成功
```
`autoencode.py predict` 実行時に別途:
```
ModuleNotFoundError: Requirement 'jsonargparse[signatures]>=4.27.7' not met
  → pip install "jsonargparse[signatures]>=4.27.7" (最新 4.50.0 解決、136KB) 成功
error: 'omegaconf' (parser_mode="omegaconf" 未解決)
  → pip install omegaconf (79KB) 成功
```
いずれの単一パッケージも 500MB 超過なし。500MB 超過による中断は発生せず。

### 3.5 predict 実行試行と設定不整合の是正（/workspace 非改変）

1回目（jsonargparse 4.50.0、`ckpts/interspeech24/golf-ff/config.yaml` をそのまま `-c`
指定）:
```
error: Parser key "trainer.logger":
  Problem with given class_path 'lightning.pytorch.loggers.WandbLogger':
      Option 'experiment' is not accepted
```
checkpoint 同梱 config.yaml 冒頭コメント `# lightning.pytorch==2.1.3` の通り、
学習時の lightning バージョンと本環境の `lightning==2.6.5`（`WandbLogger` API
差分）で非互換。`--trainer.logger false` による CLI 上書きは jsonargparse の
検証順序上ここでは効かない。**/workspace 側の config.yaml は改変せず**、
`trainer.logger` / `trainer.callbacks` / `ckpt_path` キーを除去した複製を
`OUT/golf-ff_config_stripped.yaml` として作成（OUT 側のみ・golf リポジトリ非改変）。

2回目（stripped config 使用）:
```
error: Does not validate against any of the Union subtypes
  ... Problem with given class_path 'ltng.ae.VoiceAutoEncoder':
      module 'torch' has no attribute 'Any'
```
jsonargparse 4.50.0 での挙動が Python 3.11 環境で argparse 内部と衝突する
懸念を排除するため `jsonargparse[signatures]==4.27.7`（CLI コード内で明示された
下限バージョン）に切替えて再試行（workaround 試行 2 回目・パッケージ差し替えの
みでリポジトリ非改変）→ 別エラー
`ValueError: not enough values to unpack (expected 4, got 3)`
（jsonargparse 4.27.7 内部の argparse 互換シムが Python 3.11.15 の標準
`argparse` 実装と非互換、古い jsonargparse 特有の問題と判明）。

### 3.6 根本原因の直接再現（blocked-with-evidence）

CLI 経由の変数を排除し、Python import のみで問題を直接再現:
```
$ python3 -c "import models.unet"
Traceback (most recent call last):
  File "models/unet.py", line 11, in <module>
    from .lru import LRU
  File "models/lru/__init__.py", line 1, in <module>
    from .lru import LRU
  File "models/lru/lru.py", line 6, in <module>
    from .recurrence import RecurrenceCUDA
  File "models/lru/recurrence.py", line 28, in RecurrenceCUDA
    def backward(ctx: torch.Any, grad_out: torch.Tensor) -> torch.Tensor:
                      ^^^^^^^^^
  File ".../torch/__init__.py", line 2963, in __getattr__
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
AttributeError: module 'torch' has no attribute 'Any'
```
`models/lru/` は `models/audiotensor/` 同様 git submodule
（`yoyololicon/torchlru`, commit `b28b93eee72a0ceb2a372c402f68da6a09d122c9`）。
`recurrence.py` L28 の型注釈 `torch.Any` は `from __future__ import annotations`
が無いため **クラス定義時 (import 時) に即時評価** され、torch 2.13.0+cpu には
存在しない属性のため `AttributeError` で落ちる（おそらく開発時の torch では
`typing.Any` が偶発的に `torch` 名前空間へ再エクスポートされていた版を前提と
した誤記）。

`ckpts/interspeech24/{golf-v1,mlsa,ddsp,world,nhv,golf-ss,golf-ff}/config.yaml`
の 7 モデル **全件** が `encoder_init_args.backbone_type: models.unet.UNetEncoder`
を指定しており（実測: 全 7 件で一致確認）、`UNetEncoder`（`models/unet.py` L11
`from .lru import LRU`）が必ず `models/lru/recurrence.py` を import する。
→ **Interspeech24 系 7 checkpoint 全てが同一原因で同一箇所にてブロックされる**
（GOLF-ff 個別の問題ではない）。

**必要パッチ内容（未適用・記録のみ）**:
```python
# models/lru/recurrence.py 冒頭 import 節に追加
from typing import Any
...
# L28
    def backward(ctx: Any, grad_out: torch.Tensor) -> torch.Tensor:  # torch.Any → Any
```

**判定: GOLF (Interspeech24 系 7 checkpoint 全件、golf-ff を含む) は本環境で
blocked-with-evidence。** 段階 = ckpt load 手前のモデル構築（encoder backbone
import）。前処理（wav 24kHz 化・`.pv` 生成）は成功済み、依存導入も完了済みで、
唯一の障害は submodule `torchlru` の型注釈バグ。

### Part 3 終了時ディスク状態
```
Filesystem      Size  Used Avail Use% Mounted on
/dev/vda        252G   11G   27G  28% /
```

## Part 4 — 計測

SawSing / GOLF いずれも blocked-with-evidence のため、生成 WAV は **0 件**。
`analyze.py` の `analyze_file()` を import して流用し、入力素材（原音 22.05kHz、
24kHz リサンプル版、GOLF 入力配置版）に対してのみ計測を実施し
`OUT/f0_bands.json` に保存した。

```
$ python3 -c "import analyze; ...analyze_file(...)"
ab_sakura_voiceC_floor0.10.wav  sha256=d0954efe... f0_median=326.53Hz (22050Hz)
sakura_24k.wav                  sha256=8222c5ef... f0_median=326.69Hz (24000Hz resample)
p360_001_mic1.wav               sha256=8222c5ef... f0_median=326.69Hz (= sakura_24k.wav の複製、GOLF入力配置)
```
（`sakura_24k.wav` と `p360_001_mic1.wav` は同一内容のためハッシュ一致 = 複製の
整合性確認になっている。soxr HQ リサンプルによる f0_median の変化は
+0.16Hz で無視できる範囲）

保存先: `OUT/f0_bands.json`


## Round 2（パッチ適用リトライ・設計判定による承認）

設計判定: Round 1 で記録した 2 件のパッチ（SawSing `pcmer.py` L10 try/except、
GOLF `models/lru/recurrence.py` L28 `torch.Any`→`Any`）の `/workspace` clone
への適用が承認された（F0 の Goal = 推論可否実証のため。`/home/user/ugh-prompt-engine`
本リポ・vendoring は引き続き全面禁止）。追加ブロッカー対応として、承認済み 2 件に
加えリポジトリごとに最大 3 件までの 1〜5 行規模パッチが許可された。

### Round 2 結果表

| バックエンド | 段階 | 成否 | 所要 | 出力ファイル |
|---|---|---|---|---|
| SawSing (Sins) | パッチ適用 (`pcmer.py` try/except) | 成功 | <1s | — |
| SawSing (Sins/SawSinSub 共通) | 追加パッチ (`preprocess.py` librosa kwarg, `ddsp/core.py` `.cuda()`→`.to(device)`×3) | 成功 | <1s | — |
| SawSing (Sins/SawSinSub 共通) | deps 追加 (`matplotlib`) | 成功 | 数秒 | — |
| SawSing (Sins/SawSinSub 共通) | mel 前処理 (24kHz sakura → mel .npy) | 成功 | 1.33s | `sawsing_mel_in/sakura_24k.npy` |
| SawSing (Sins backend) | 推論 (`main.py --stage inference`) | **成功** | 8.10s (RTF 0.330) | `sawsing_sins_sakura_24k.wav` |
| SawSing (SawSinSub-256 backend) | 推論 | **成功** | 7.24s (RTF 0.295) | `sawsing_sawsinsub256_sakura_24k.wav` |
| GOLF | パッチ適用 (`models/lru/recurrence.py` `torch.Any`→`Any`) | 成功 | <1s | — |
| GOLF | 追加パッチ (`ltng/cli.py` `torchaudio.save`→`soundfile.write`) | 成功 | <1s | — |
| GOLF | deps 追加/更新 (`jsonargparse`→4.50.0 に再アップグレード) | 成功 | 数秒 | — |
| GOLF (golf-ff) | predict (`autoencode.py predict`) | **成功** | 9.75s (RTF 0.397) | `golf_golfff_p360.wav` |
| GOLF (world) | predict | **成功**（余裕枠で追加実行） | 22.52s (RTF 0.916) | `golf_world_p360.wav` |
| GOLF (ddsp) | predict | **成功**（余裕枠で追加実行） | 21.02s (RTF 0.855) | `golf_ddsp_p360.wav` |
| 計測 (Round 2) | 生成 5 WAV の f0/LTAS 分析 + sha256 | 成功 | 数秒 | `f0_bands_round2.json` |

**総括: Round 2 で SawSing 2 backend + GOLF 3 checkpoint、計 5 件の実推論に
成功。生成 WAV 0 件だった Round 1 から全転換。** golf-ss / golf-v1 / nhv /
mlsa の残り 4 checkpoint は未実行（`golf-ff`/`world`/`ddsp` で経路確認済みのため
必須要件は満たしたと判断、余裕時間の配分外）。

### Part R2-1 — パッチ適用（承認済み 2 件）と diff 全文

#### SawSing `ddsp/pcmer.py` L10（承認済みパッチ）

```
$ git -C /workspace/yatingmusic/ddsp-singing-vocoders diff -- ddsp/pcmer.py
diff --git a/ddsp/pcmer.py b/ddsp/pcmer.py
index ef4f711..f13d4d7 100755
--- a/ddsp/pcmer.py
+++ b/ddsp/pcmer.py
@@ -7,7 +7,10 @@ from einops import rearrange, repeat
 
 from local_attention import LocalAttention
 import torch.nn.functional as F
-import fast_transformers.causal_product.causal_product_cuda
+try:
+    import fast_transformers.causal_product.causal_product_cuda
+except ImportError:
+    pass  # falls back to causal_linear_attention_noncuda inside PCmer.__init__
 
 def softmax_kernel(data, *, projection_matrix, is_query, normalize_data=True, eps=1e-4, device = None):
     b, h, *_ = data.shape
```

適用後検証: `python3 -c "from ddsp.vocoder import Sins, SawSinSub"` → 例外なく import 成功
（Round 1 でブロックしていた `ModuleNotFoundError`/トップレベル import failure が解消）。

#### GOLF submodule `models/lru/recurrence.py` L28（承認済みパッチ）

```
$ git -C /workspace/yoyololicon/golf/models/lru diff
diff --git a/recurrence.py b/recurrence.py
index 45bd4c6..b48627c 100644
--- a/recurrence.py
+++ b/recurrence.py
@@ -1,3 +1,5 @@
+from typing import Any
+
 import torch
 import torch.nn.functional as F
 from torch.autograd import Function
@@ -25,7 +27,7 @@ class RecurrenceCUDA(Function):
         return out
 
     @staticmethod
-    def backward(ctx: torch.Any, grad_out: torch.Tensor) -> torch.Tensor:
+    def backward(ctx: Any, grad_out: torch.Tensor) -> torch.Tensor:
         decay, initial_state, out = ctx.saved_tensors
         grad_decay = grad_impulse = grad_initial_state = None
         n_dims, _ = decay.shape
```

GOLF 親リポ側 (`git -C /workspace/yoyololicon/golf diff`) は submodule ポインタが
`b28b93eee...-dirty` に変わるのみ（サブモジュール内部の作業ツリー差分を親が
"dirty" と検出しているだけで、コミットは切っていない。親リポのファイル自体は
無変更）。

適用後検証: `python3 -c "import models.unet"` → 例外なく import 成功
（Round 1 でブロックしていた `AttributeError: module 'torch' has no attribute 'Any'`
が解消）。

### Part R2-2 — SawSing 追加ブロッカーと対応（追加パッチ 2 件 + config 複製 2 件・deps 1 件）

承認済みパッチ適用で `ddsp.vocoder` の import 自体は通ったが、推論到達までに
以下 3 段の新規ブロッカーが順に出現した（Round 1 は import 失敗で先に進めず
未検出だった段）。いずれも 1〜5 行規模のため許容枠内（SawSing リポ 2/3 消費）。

**1. `preprocess.py` L69-71 — librosa 0.11.0 は `librosa.filters.mel` を
keyword-only にした**（本環境 librosa==0.11.0、リポジトリは旧 API の位置引数
呼び出し前提）:
```
$ python3 -c "from preprocess import Audio2Mel; Audio2Mel(hop_length=240, sampling_rate=24000, n_mel_channels=80, win_length=1024)"
TypeError: mel() takes 0 positional arguments but 5 were given
```
パッチ（`sr=`/`n_fft=`/`n_mels=`/`fmin=`/`fmax=` の keyword 化）:
```diff
--- a/preprocess.py
+++ b/preprocess.py
@@ -67,7 +67,7 @@ class Audio2Mel(torch.nn.Module):
         window = torch.hann_window(win_length).float()
         mel_basis = librosa_mel_fn(
-            sampling_rate, n_fft, n_mel_channels, mel_fmin, mel_fmax
+            sr=sampling_rate, n_fft=n_fft, n_mels=n_mel_channels, fmin=mel_fmin, fmax=mel_fmax
         )
         mel_basis = torch.from_numpy(mel_basis).float()
```
適用後、mel 抽出成功（`sawsing_mel_in/sakura_24k.npy`, shape (2455, 80), 1.33s）。

**2. `main.py` L10 `from logger import utils, report` → `logger/report.py`
L5 `import matplotlib.pyplot as plt`** で `ModuleNotFoundError: No module
named 'matplotlib'`（コード改変ではなく deps 追加で解消。`pip install
--no-cache-dir matplotlib` 成功、10.0MB wheel、500MB 超過なし）。

**3. `main.py` L122-128 の実行順序バグ — `load_model_params` 呼び出し (L124-125)
が `args.device` 自動判定 (L128 `args.device = 'cuda' if torch.cuda.is_available()
else 'cpu'`) より前**にあるため、config の `device: cuda`（GPU 学習時デフォルト）
がそのまま `torch.load(..., map_location='cuda')` に渡り CPU-only 環境で失敗:
```
RuntimeError: Attempting to deserialize object on a CUDA device but
torch.cuda.is_available() is False. If you are running on a CPU-only machine,
please use torch.load with map_location=torch.device('cpu').
```
これはコードのタイミングバグだが、`configs/{sins,sawsinsub}.yaml` の
`device: cuda` を **OUT 側の config 複製** で `device: cpu` に書き換えることで
`/workspace` 非改変のまま回避可能と判断（`main.py` 本体へのパッチは不要）。
`$OUT/sawsing_configs/sins_cpu.yaml`, `$OUT/sawsing_configs/sawsinsub_cpu.yaml`
を作成し `sed -i 's/^device: cuda/device: cpu/'` のみ適用（他キー無改変）。

**4. `ddsp/core.py` L142/L171/L213 — `frequency_filter`/`apply_window_to_impulse_response`
経路内で無条件 `.cuda()` が 3 箇所**（`fft_convolve` 内 2 箇所 +
`apply_window_to_impulse_response` 内 1 箇所）。config 修正後、推論実行で到達:
```
AssertionError: Torch not compiled with CUDA enabled
  (ddsp/core.py:213, apply_window_to_impulse_response 内
   window = nn.Parameter(torch.hann_window(window_size), requires_grad=False).cuda())
```
パッチ（該当 3 箇所を `.cuda()` → `.to(<同一演算で使われるテンソルの device>)`
に置換。GPU 実行時は従来どおり cuda テンソルになるため挙動不変）:
```diff
--- a/ddsp/core.py
+++ b/ddsp/core.py
@@ -139,7 +139,7 @@ def fft_convolve(audio,
     if frame_size!=audio_size:
-        filters = torch.eye(frame_size).unsqueeze(1).cuda()
+        filters = torch.eye(frame_size).unsqueeze(1).to(audio.device)
@@ -168,7 +168,7 @@ def fft_convolve(audio,
     if frame_size!=audio_size:
-        overlap_add_filter = torch.eye(audio_frames_out.size(-1), requires_grad = False).unsqueeze(1).cuda()
+        overlap_add_filter = torch.eye(audio_frames_out.size(-1), requires_grad = False).unsqueeze(1).to(audio_frames_out.device)
@@ -210,7 +210,7 @@ def apply_window_to_impulse_response(impulse_response,
-    window = nn.Parameter(torch.hann_window(window_size), requires_grad = False).cuda()
+    window = nn.Parameter(torch.hann_window(window_size), requires_grad = False).to(impulse_response.device)
```
（1 blocker = ファイル単位 1 patch としてカウント。SawSing 追加パッチ消費数:
`preprocess.py` 1 件 + `ddsp/core.py` 1 件 = 2/3。`matplotlib` deps 追加・
config 複製はパッチ枠に非カウント）

上記適用後、両 backend とも `main.py --stage inference` が完走:
```
$ python3 main.py --config $OUT/sawsing_configs/sins_cpu.yaml --stage inference \
    --model Sins --model_ckpt exp/f1-full/sins/ckpts/vocoder_15090_70.0_params.pt \
    --input_dir $OUT/sawsing_mel_in --output_dir $OUT/sawsing_out_sins
exit=0, elapsed=8.10s
 > path_pred: .../sawsing_out_sins/pred/sakura_24k.wav

$ python3 main.py --config $OUT/sawsing_configs/sawsinsub_cpu.yaml --stage inference \
    --model SawSinSub --model_ckpt exp/f1-full/sawsinsub-256/ckpts/vocoder_27740_70.0_params.pt \
    --input_dir $OUT/sawsing_mel_in --output_dir $OUT/sawsing_out_sawsinsub
exit=0, elapsed=7.24s
 > path_pred: .../sawsing_out_sawsinsub/pred/sakura_24k.wav
```
出力を `$OUT/sawsing_sins_sakura_24k.wav`, `$OUT/sawsing_sawsinsub256_sakura_24k.wav`
にコピー（両者とも 24000Hz / mono / PCM_16 / 24.550s）。

### Part R2-3 — GOLF 追加ブロッカーと対応（追加パッチ 1 件 + deps 1 件 + config 複製 3 件）

承認済みパッチで `models.unet` import は通ったが、predict CLI 実行で以下 2 段の
新規ブロッカーが出た（GOLF リポ追加パッチ消費: 1/3）。

**1. jsonargparse バージョン後退の再発** — Round 1 の workaround 試行で
`jsonargparse==4.27.7` に切り替えたままだったため、本 Round 開始時点の環境は
4.27.7 のまま。これは Round 1 の実測（§3.5）どおり Python 3.11.15 標準
`argparse` と非互換（`ValueError: not enough values to unpack (expected 4, got 3)`）
で predict CLI が起動不能。パッチではなく **deps 更新のみ**で解消:
```
$ pip install --no-cache-dir --upgrade "jsonargparse[signatures]"
  jsonargparse 4.27.7 -> 4.50.0
```

**2. `ltng/cli.py` L36 `torchaudio.save()` が内部で `torchcodec` を要求し、
`torchcodec` 0.16.0 のネイティブ拡張が本環境でロード不能**（golf-ff の predict
は forward pass まで到達 → 保存段でクラッシュ、というモデル本体は健全である
ことを示す好材料だった）:
```
ModuleNotFoundError: No module named 'torchcodec'
  → pip install --no-cache-dir torchcodec (9.5MB, 500MB 超過なし) 成功
  → しかし import torchcodec 自体が失敗:
OSError: Could not load this library:
  /usr/local/lib/python3.11/dist-packages/torchcodec/libtorchcodec_image.so
```
torchcodec 0.16.0 のネイティブ `.so` が本環境の torch 2.13.0+cpu ビルドと
バイナリ非互換（またはリンクする FFmpeg 共有ライブラリ不在）と判明。
torchcodec 側のビルド問題を追う代わりに、`ltng/cli.py` の保存呼び出しを
`torchaudio.save` から既存導入済みの `soundfile.write` に置換（同一 PCM 出力・
torchcodec 依存を完全に回避）:
```diff
--- a/ltng/cli.py
+++ b/ltng/cli.py
@@ -1,5 +1,6 @@
 import pathlib
 import os
+import soundfile as sf
 import torchaudio
@@ -33,11 +34,11 @@ class MyPredictionWriter(BasePredictionWriter):
         out_path.parent.mkdir(parents=True, exist_ok=True)
-        torchaudio.save(
-            out_path,
-            pred.as_tensor().cpu(),
-            sample_rate=sr,
-        )
+        audio = pred.as_tensor().cpu().numpy().squeeze(0)
+        sf.write(str(out_path), audio, sr)
```

**config 複製（`/workspace` 非改変）**: Round 1 §3.5 で作成済みの
`golf-ff_config_stripped.yaml`（`ckpt_path`/`trainer.logger`/`trainer.callbacks`
キー除去）に加え、`world`/`ddsp` checkpoint 用に同じ除去ロジックを Python
`yaml.safe_load`/`safe_dump` で適用し `golf-world_config_stripped.yaml`,
`golf-ddsp_config_stripped.yaml` を新規作成（除去キーの集合は golf-ff と同一、
3 config とも OUT 側のみ）。

上記適用後、`autoencode.py predict` が 3 checkpoint 全てで完走
（`--trainer.accelerator cpu --trainer.devices 1` で README のハードコード
`gpu` を上書き、`--data.wav_dir $OUT/golf_input` で VCTK test 話者ディレクトリを
差し替え）:
```
$ python3 autoencode.py predict -c $OUT/golf-ff_config_stripped.yaml \
    --ckpt_path ckpts/interspeech24/golf-ff/checkpoints/epoch=539-step=996840-val_loss=3.073.ckpt \
    --trainer.accelerator cpu --trainer.devices 1 --trainer.logger false --seed_everything false \
    --data.wav_dir $OUT/golf_input \
    --trainer.callbacks+=ltng.cli.MyPredictionWriter --trainer.callbacks.output_dir $OUT/golf_output_golfff
exit=0, elapsed=9.75s
Predicting 1/1 ... 0.00it/s

（world, ddsp も同一パターンで exit=0。world: 22.52s、ddsp: 21.02s）
```
出力を `$OUT/golf_golfff_p360.wav`, `$OUT/golf_world_p360.wav`,
`$OUT/golf_ddsp_p360.wav` にコピー（3 件とも 24000Hz / mono / PCM_16 / 24.580s）。
golf-ss / golf-v1 / nhv / mlsa の残り 4 checkpoint は同一コード経路（`models.unet`
経由）のため推論可否は golf-ff/world/ddsp の成功で実証済みと判断し、時間配分の
都合で未実行（Goal である「推論到達実証」に対して追加実行の限界効用が低いと
判断・未実行の対象を明記）。

### Part R2-4 — 計測

```
$ python3 -c "import analyze; ... analyze_file(p) for p in [5 files] ..."
golf_ddsp_p360.wav                    f0_median=326.25Hz sha256=be758436b6838de1...
golf_golfff_p360.wav                  f0_median=326.14Hz sha256=9832ddc070b403d8...
golf_world_p360.wav                   f0_median=326.12Hz sha256=c295977e9c6bb425...
sawsing_sawsinsub256_sakura_24k.wav   f0_median=281.78Hz sha256=e3f4a0f433df98a3...
sawsing_sins_sakura_24k.wav           f0_median=304.51Hz sha256=779688fb1d7d3991...
```
入力素材 f0_median（Part 4, 24kHz リサンプル版）326.69Hz との比較参考値
（採否判定は本ログの範囲外・判読は別途）:
- GOLF 3 backend はいずれも入力 326Hz 台とほぼ整合（差 <1Hz）
- SawSing 2 backend はいずれも入力より低め（Sins: -22Hz, SawSinSub: -45Hz）

保存先: `OUT/f0_bands_round2.json`（sha256 込み、5 ファイル全件）

### Round 2 終了時ディスク状態
```
$ df -h /
Filesystem      Size  Used Avail Use% Mounted on
/dev/vda        252G   11G   27G  29% /
$ date -u
2026-08-15T01:45:18Z
```

## 最終ディスク状態
```
Filesystem      Size  Used Avail Use% Mounted on
/dev/vda        252G   11G   27G  28% /
```
