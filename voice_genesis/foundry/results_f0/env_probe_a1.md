# 結果表（2026-08-15 実測: 学習ベース R1 / DDSP fork 実行可能性プローブ）

前提となった 2026-08-13 判断（「学習ベース R1（DDSP 系 fork）は本環境で実行不能」）の
再検証。本セクションは生データ収集の要約表であり、採否の設計判断は含まない。

| 項目 | 結果 | 証拠となる行 |
|---|---|---|
| torch (事前導入) | 未導入 (ModuleNotFoundError) | L93 節, `import torch` エラー出力 |
| torchaudio / tensorflow / pyworld / pyopenjtalk / crepe (事前導入) | いずれも未導入 | L93 節 |
| torch CPU wheel インストール | 成功 (torch 2.13.0+cpu, `download.pytorch.org/whl/cpu` から 191.8MB を 89.3MB/s で取得) | L178, L180 |
| torch 演算スモークテスト | 成功 (`torch.rand(64,64) @ 同`, sum=65039.796875) | L185-186 |
| torchaudio CPU wheel インストール | 成功だが **バージョン skew**: torchaudio 2.11.0+cpu が torch 2.13.0+cpu に対して解決された（同 index に torch2.13 対応 torchaudio 無し） | L196-207 |
| torchaudio import 確認 | 成功 (import は通る。バージョン skew の実害は未検証 = 本プローブの対象外) | L214 |
| pyworld インストール | 成功（ソースビルド、cmake/gcc 不要、pure C 拡張ビルドのみ） | L218-240 |
| pyworld スモークテスト (harvest/cheaptrick/d4c/synthesize 往復) | 成功 (f0_median=214.21Hz [220Hz 入力に対し妥当], out_rms=0.4333) | L245 |
| pyopenjtalk インストール | 成功（cmake ビルド経由、フォールバック不要） | L249-277 |
| pyopenjtalk g2p + 初回辞書ダウンロード | 成功 (`open_jtalk_dic_utf_8-1.11.tar.gz` 22.6MB を proxy 経由で取得、g2p('さくらさくら') → 正しい音素列) | L285-286 |
| GitHub fork clone: YatingMusic/ddsp-singing-vocoders | 成功 (`--depth 1`, git proxy 経由 anonymous) | L295 |
| YatingMusic fork: pretrained checkpoint 同梱有無 | **同梱あり**（`exp/f1-full/{sins,sawsinsub-256}/ckpts/*.pt` 計 6 ファイル、各 ~2.3MB、LFS 未使用、外部ダウンロード不要） | L339-341 |
| GitHub fork clone: yoyololicon/golf | 成功 (`--depth 1`, git proxy 経由 anonymous) | L348 |
| golf fork: pretrained checkpoint 同梱有無 | **同梱あり**（`ckpts/` 614MB、ISMIR23 + Interspeech24 の `.ckpt` 群、LFS 未使用、外部ダウンロード不要） | L426-428 |
| golf fork: git submodule 状態 | **未初期化**（`datasets`, `models/audiotensor`, `models/lru` の 3 件が `git@github.com:...` SSH URL で宣言。plain `--depth 1` clone では取得されず空ディレクトリのまま。SSH/HTTPS 到達性は本プローブでは未検証） | L447-452 |
| huggingface.co 到達性 (HEAD + API) | 到達可 (HTTP/2 200, API が JSON 応答) | L458-460 |
| zenodo.org 到達性 (HEAD) | 到達可 (HTTP/1.1 200) | L488-490 |
| github.com/yoyololicon/golf/releases 到達性 | **403** — ただし git clone 層のブロックではなく、proxy の github.com **web/API アクセスゲート**（`"GitHub access to this repository is not enabled for this session. Use add_repo..."`）。add_repo は本プローブの範囲外のため未実行 | L513-515 |
| ディスク消費 (プローブ全体) | 純増 ≈2GB（`/` 使用量 7.9G→10G）。内訳: clone 保持分 1.4GB (168MB + 1.2GB) + パッケージ導入分 ≈0.6GB。pip cache は最後に purge (368 ファイル削除) | L549-566 |
| HTTPS_PROXY 設定状況 | 設定あり (`http://127.0.0.1:46171`)、agent proxy 経由で全 HTTPS が中継される構成。TLS エラー・407 は本プローブ中に一切発生せず | L1 節（baseline）参照 |

**総括（実測のみ、判断なし）**: torch/torchaudio/pyworld/pyopenjtalk はいずれも本環境で
インストール・スモークテストに成功した。2 つの DDSP fork 候補（YatingMusic/golf）は
clone 成功、かつ両者とも pretrained checkpoint を git 本体に同梱しており外部チェックポイント
配布網への依存は（この 2 fork に関する限り）不要と判明。huggingface.co / zenodo.org への
到達性も確認できた。唯一のブロッカーは golf fork の 3 submodule（SSH URL, 未初期化）と
github.com web/API 個別ページへの proxy ゲート（git clone とは別レイヤー、add_repo 未実行）
——いずれも本プローブの手順上は「未検証」であり「実行不能」の確認ではない。

---

# env_probe_a1 raw log (step 0: baseline)

Timestamp: 2026-08-15T00:40:41Z

```
$ python -V
Python 3.11.15

$ pip -V
pip 24.0 from /usr/lib/python3/dist-packages/pip (python 3.11)

$ nproc
4

$ free -h
               total        used        free      shared  buff/cache   available
Mem:            15Gi       634Mi        14Gi       4.8Mi       756Mi        15Gi
Swap:             0B          0B          0B

$ df -h /
Filesystem      Size  Used Avail Use% Mounted on
/dev/vda        252G  7.9G   30G  22% /

$ df -h /tmp
Filesystem      Size  Used Avail Use% Mounted on
/dev/vda        252G  7.9G   30G  22% /

$ env | grep -i proxy
CCR_AGENT_PROXY_ENABLED=1
no_proxy=localhost,127.0.0.1,::1,127.0.0.0/8,0.0.0.0/8,::,169.254.0.0/16,anthropic.com,.anthropic.com,*.anthropic.com,registry.npmjs.org,jsr.io,npm.jsr.io,pypi.org,files.pythonhosted.org,index.crates.io,proxy.golang.org,host.docker.internal,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10,.svc.cluster.local,*.svc.cluster.local
GH_TOKEN=proxy-injected
CCR_UPSTREAM_PROXY_ENABLED=1
CLOUDSDK_PROXY_TYPE=http
GITHUB_TOKEN=proxy-injected
CLOUDSDK_AUTH_ACCESS_TOKEN=proxy-injected
CLOUDSDK_PROXY_PORT=46171
CLAUDE_CODE_PROXY_RESOLVES_HOSTS=true
CCR_TEST_GITPROXY=1
AWS_SECRET_ACCESS_KEY=proxy-injected
https_proxy=http://127.0.0.1:46171
GLOBAL_AGENT_NO_PROXY=localhost,127.0.0.1,::1,127.0.0.0/8,0.0.0.0/8,::,169.254.0.0/16,anthropic.com,.anthropic.com,*.anthropic.com,registry.npmjs.org,jsr.io,npm.jsr.io,pypi.org,files.pythonhosted.org,index.crates.io,proxy.golang.org,host.docker.internal,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10,.svc.cluster.local,*.svc.cluster.local
ELECTRON_GET_USE_PROXY=1
JAVA_TOOL_OPTIONS=-Djavax.net.ssl.trustStore=/root/.ccr/java-truststore.p12 -Djavax.net.ssl.trustStorePassword=changeit -Djavax.net.ssl.trustStoreType=PKCS12 -Dhttps.proxyHost=127.0.0.1 -Dhttps.proxyPort=46171 -Dhttp.nonProxyHosts=localhost|127.0.0.1|::1|127.*|0.*|::|169.254.*|anthropic.com|*.anthropic.com|*.anthropic.com|registry.npmjs.org|jsr.io|npm.jsr.io|pypi.org|files.pythonhosted.org|index.crates.io|proxy.golang.org|host.docker.internal|10.*|172.16.*|172.17.*|172.18.*|172.19.*|172.20.*|172.21.*|172.22.*|172.23.*|172.24.*|172.25.*|172.26.*|172.27.*|172.28.*|172.29.*|172.30.*|172.31.*|192.168.*|100.64.0.0/10|*.svc.cluster.local|*.svc.cluster.local -Djdk.http.auth.tunneling.disabledSchemes= -Djdk.http.auth.proxying.disabledSchemes=
NO_PROXY=localhost,127.0.0.1,::1,127.0.0.0/8,0.0.0.0/8,::,169.254.0.0/16,anthropic.com,.anthropic.com,*.anthropic.com,registry.npmjs.org,jsr.io,npm.jsr.io,pypi.org,files.pythonhosted.org,index.crates.io,proxy.golang.org,host.docker.internal,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10,.svc.cluster.local,*.svc.cluster.local
AWS_ACCESS_KEY_ID=proxy-injected
HTTPS_PROXY=http://127.0.0.1:46171
npm_config_noproxy=localhost,127.0.0.1,::1,127.0.0.0/8,0.0.0.0/8,::,169.254.0.0/16,anthropic.com,.anthropic.com,*.anthropic.com,registry.npmjs.org,jsr.io,npm.jsr.io,pypi.org,files.pythonhosted.org,index.crates.io,proxy.golang.org,host.docker.internal,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10,.svc.cluster.local,*.svc.cluster.local
YARN_HTTPS_PROXY=http://127.0.0.1:46171
CLOUDSDK_PROXY_ADDRESS=127.0.0.1
npm_config_https_proxy=http://127.0.0.1:46171
DOCKER_HTTPS_PROXY=http://127.0.0.1:46171
GLOBAL_AGENT_HTTPS_PROXY=http://127.0.0.1:46171
```

## step 1: existing package check

```
$ python -c "import torch; print(torch.__version__)"
Traceback (most recent call last):
  File "<string>", line 1, in <module>
ModuleNotFoundError: No module named 'torch'
exit: 1

$ python -c "import torchaudio; print(torchaudio.__version__)"
Traceback (most recent call last):
  File "<string>", line 1, in <module>
ModuleNotFoundError: No module named 'torchaudio'
exit: 1

$ python -c "import tensorflow; print(tensorflow.__version__)"
Traceback (most recent call last):
  File "<string>", line 1, in <module>
ModuleNotFoundError: No module named 'tensorflow'
exit: 1

$ python -c "import pyworld; print(pyworld.__version__)"
Traceback (most recent call last):
  File "<string>", line 1, in <module>
ModuleNotFoundError: No module named 'pyworld'
exit: 1

$ python -c "import pyopenjtalk; print(pyopenjtalk.__version__)"
Traceback (most recent call last):
  File "<string>", line 1, in <module>
ModuleNotFoundError: No module named 'pyopenjtalk'
exit: 1

$ python -c "import crepe; print(crepe.__version__)"
Traceback (most recent call last):
  File "<string>", line 1, in <module>
ModuleNotFoundError: No module named 'crepe'
exit: 1

```

## step 2: torch CPU install

```
$ pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
(head 20 lines)
Looking in indexes: https://download.pytorch.org/whl/cpu
Collecting torch
  Downloading https://download-r2.pytorch.org/whl/cpu/torch-2.13.0%2Bcpu-cp311-cp311-manylinux_2_28_x86_64.whl.metadata (37 kB)
Collecting filelock (from torch)
  Downloading filelock-3.32.3-py3-none-any.whl.metadata (2.0 kB)
Requirement already satisfied: typing-extensions>=4.10.0 in /usr/local/lib/python3.11/dist-packages (from torch) (4.16.0)
Collecting setuptools>=77.0.3 (from torch)
  Downloading https://download.pytorch.org/whl/setuptools-78.1.0-py3-none-any.whl.metadata (6.6 kB)
Collecting sympy>=1.13.3 (from torch)
  Downloading sympy-1.14.0-py3-none-any.whl.metadata (12 kB)
Collecting networkx>=2.5.1 (from torch)
  Downloading networkx-3.6.1-py3-none-any.whl.metadata (6.8 kB)
Requirement already satisfied: jinja2 in /root/.local/lib/python3.11/site-packages (from torch) (3.1.6)
Collecting fsspec>=0.8.5 (from torch)
  Downloading fsspec-2026.7.0-py3-none-any.whl.metadata (10 kB)
Collecting mpmath<1.4,>=1.1.0 (from sympy>=1.13.3->torch)
  Downloading mpmath-1.3.0-py3-none-any.whl.metadata (8.6 kB)
Requirement already satisfied: MarkupSafe>=2.0 in /root/.local/lib/python3.11/site-packages (from jinja2->torch) (3.0.3)
Downloading https://download-r2.pytorch.org/whl/cpu/torch-2.13.0%2Bcpu-cp311-cp311-manylinux_2_28_x86_64.whl (191.8 MB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 191.8/191.8 MB 89.3 MB/s eta 0:00:00
...
(tail 20 lines)
Downloading fsspec-2026.7.0-py3-none-any.whl (206 kB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 206.6/206.6 kB 68.2 MB/s eta 0:00:00
Downloading networkx-3.6.1-py3-none-any.whl (2.1 MB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 2.1/2.1 MB 64.0 MB/s eta 0:00:00
Downloading https://download.pytorch.org/whl/setuptools-78.1.0-py3-none-any.whl (1.3 MB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 1.3/1.3 MB 11.0 MB/s eta 0:00:00
Downloading sympy-1.14.0-py3-none-any.whl (6.3 MB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 6.3/6.3 MB 65.2 MB/s eta 0:00:00
Downloading filelock-3.32.3-py3-none-any.whl (98 kB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 98.9/98.9 kB 65.2 MB/s eta 0:00:00
Downloading mpmath-1.3.0-py3-none-any.whl (536 kB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 536.2/536.2 kB 77.9 MB/s eta 0:00:00
Installing collected packages: mpmath, sympy, setuptools, networkx, fsspec, filelock, torch
  Attempting uninstall: setuptools
    Found existing installation: setuptools 68.1.2
    Uninstalling setuptools-68.1.2:
      Successfully uninstalled setuptools-68.1.2
Successfully installed filelock-3.32.3 fsspec-2026.7.0 mpmath-1.3.0 networkx-3.6.1 setuptools-78.1.0 sympy-1.14.0 torch-2.13.0+cpu
WARNING: Running pip as the 'root' user can result in broken permissions and conflicting behaviour with the system package manager. It is recommended to use a virtual environment instead: https://pip.pypa.io/warnings/venv
EXIT_CODE=0
```

```
$ python -c "import torch; print(torch.__version__); x=torch.rand(64,64); print((x@x).sum().item())"
2.13.0+cpu
65039.796875
exit: 0
```

```
$ df -h /
Filesystem      Size  Used Avail Use% Mounted on
/dev/vda        252G  8.7G   29G  24% /
```

## step 3: torchaudio CPU install

```
Looking in indexes: https://download.pytorch.org/whl/cpu
Collecting torchaudio
  Downloading https://download-r2.pytorch.org/whl/cpu/torchaudio-2.11.0%2Bcpu-cp311-cp311-manylinux_2_28_x86_64.whl.metadata (6.9 kB)
Downloading https://download-r2.pytorch.org/whl/cpu/torchaudio-2.11.0%2Bcpu-cp311-cp311-manylinux_2_28_x86_64.whl (341 kB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 341.3/341.3 kB 3.8 MB/s eta 0:00:00
Installing collected packages: torchaudio
Successfully installed torchaudio-2.11.0+cpu
WARNING: Running pip as the 'root' user can result in broken permissions and conflicting behaviour with the system package manager. It is recommended to use a virtual environment instead: https://pip.pypa.io/warnings/venv
EXIT_CODE=0
```

NOTE: pip resolved torchaudio 2.11.0+cpu against torch 2.13.0+cpu (no matching torchaudio build for torch 2.13 on this index) — version skew, but import succeeded (see below).

```
$ python -c "import torchaudio; print(torchaudio.__version__)"
2.11.0+cpu
EXIT=0
```

## step 4: pyworld install + smoke test

```
Collecting pyworld
  Downloading pyworld-0.3.5.tar.gz (261 kB)
     ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 261.0/261.0 kB 175.6 MB/s eta 0:00:00
  Installing build dependencies: started
  Installing build dependencies: finished with status 'done'
  Getting requirements to build wheel: started
  Getting requirements to build wheel: finished with status 'done'
  Preparing metadata (pyproject.toml): started
  Preparing metadata (pyproject.toml): finished with status 'done'
Requirement already satisfied: numpy in /usr/local/lib/python3.11/dist-packages (from pyworld) (2.4.6)
Building wheels for collected packages: pyworld
  Building wheel for pyworld (pyproject.toml): started
  Building wheel for pyworld (pyproject.toml): finished with status 'done'
  Created wheel for pyworld: filename=pyworld-0.3.5-cp311-cp311-linux_x86_64.whl size=927104 sha256=5a4d4d61f9bef7e0762abca72b41b840a44559587eab11a3c1d19629d549c39c
  Stored in directory: /tmp/pip-ephem-wheel-cache-ddjfzx5p/wheels/26/f0/db/ebcd5cdfe5ad7d229917d3a8db6f18f0cf40f099bf878e294d
Successfully built pyworld
Installing collected packages: pyworld
Successfully installed pyworld-0.3.5
WARNING: Running pip as the 'root' user can result in broken permissions and conflicting behaviour with the system package manager. It is recommended to use a virtual environment instead: https://pip.pypa.io/warnings/venv
EXIT_CODE=0
```

```
smoke test script: harvest/cheaptrick/d4c/synthesize round trip on 220Hz sine
f0_median 214.2138603494134 out_rms 0.43328227595283747
EXIT=0
```

## step 5: pyopenjtalk install + g2p test

```
Collecting pyopenjtalk
  Downloading pyopenjtalk-0.4.1.tar.gz (1.4 MB)
     ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 1.4/1.4 MB 181.4 MB/s eta 0:00:00
  Installing build dependencies: started
  Installing build dependencies: finished with status 'done'
  Getting requirements to build wheel: started
  Getting requirements to build wheel: finished with status 'done'
  Preparing metadata (pyproject.toml): started
  Preparing metadata (pyproject.toml): finished with status 'done'
Requirement already satisfied: numpy>=1.20.0 in /usr/local/lib/python3.11/dist-packages (from pyopenjtalk) (2.4.6)
Collecting tqdm (from pyopenjtalk)
  Downloading tqdm-4.70.0-py3-none-any.whl.metadata (57 kB)
     ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 57.3/57.3 kB 64.2 MB/s eta 0:00:00
Downloading tqdm-4.70.0-py3-none-any.whl (80 kB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 80.2/80.2 kB 142.3 MB/s eta 0:00:00
Building wheels for collected packages: pyopenjtalk
  Building wheel for pyopenjtalk (pyproject.toml): started
  Building wheel for pyopenjtalk (pyproject.toml): still running...
  Building wheel for pyopenjtalk (pyproject.toml): finished with status 'done'
  Created wheel for pyopenjtalk: filename=pyopenjtalk-0.4.1-cp311-cp311-linux_x86_64.whl size=5763859 sha256=08699265cb778f2ac9d2f118cefe0de944e6e92137796e04aa8de42b82bc18c6
  Stored in directory: /tmp/pip-ephem-wheel-cache-n_u9ncuv/wheels/1e/c0/4f/d17fa12db5fee142d7455b1af3c5ad45b751e038a2e926fb41
Successfully built pyopenjtalk
Installing collected packages: tqdm, pyopenjtalk
Successfully installed pyopenjtalk-0.4.1 tqdm-4.70.0
WARNING: Running pip as the 'root' user can result in broken permissions and conflicting behaviour with the system package manager. It is recommended to use a virtual environment instead: https://pip.pypa.io/warnings/venv
EXIT_CODE=0
```

```
$ python -c "import pyopenjtalk; print(pyopenjtalk.g2p('さくらさくら'))"  (includes first-run dict download over proxy)
Downloading: "https://github.com/r9y9/open_jtalk/releases/download/v1.11.1/open_jtalk_dic_utf_8-1.11.tar.gz"
  0%|          | 0/23646843 [00:00<?, ?it/s]  4%|▍         | 926k/22.6M [00:00<00:02, 9.37MB/s] 48%|████▊     | 10.9M/22.6M [00:00<00:00, 65.1MB/s] 94%|█████████▍| 21.3M/22.6M [00:00<00:00, 85.1MB/s]100%|██████████| 22.6M/22.6M [00:00<00:00, 75.4MB/s]
Extracting tar file
s a k u r a s a k u r a
EXIT_CODE=0
```

## step 6: fork clones

### 6a. YatingMusic/ddsp-singing-vocoders

```
Cloning into '/workspace/yatingmusic/ddsp-singing-vocoders'...
EXIT_CODE=0
```

```
=== du -sh ===
168M	/workspace/yatingmusic/ddsp-singing-vocoders

=== ls top level ===
total 128
drwxr-xr-x 10 root root  4096 Aug 15 00:45 .
drwxr-xr-x  3 root root  4096 Aug 15 00:44 ..
drwxr-xr-x  8 root root  4096 Aug 15 00:45 .git
-rw-r--r--  1 root root  1910 Aug 15 00:45 .gitignore
-rw-r--r--  1 root root 34523 Aug 15 00:45 LICENSE
-rw-r--r--  1 root root  3893 Aug 15 00:45 README.md
-rw-r--r--  1 root root  1986 Aug 15 00:45 compare.py
drwxr-xr-x  2 root root  4096 Aug 15 00:45 configs
drwxr-xr-x  2 root root  4096 Aug 15 00:45 data
-rwxr-xr-x  1 root root  5019 Aug 15 00:45 data_cnpop.py
drwxr-xr-x  2 root root  4096 Aug 15 00:45 ddsp
drwxr-xr-x  2 root root  4096 Aug 15 00:45 docs
drwxr-xr-x  3 root root  4096 Aug 15 00:45 exp
drwxr-xr-x  2 root root  4096 Aug 15 00:45 logger
-rwxr-xr-x  1 root root  4576 Aug 15 00:45 main.py
drwxr-xr-x  3 root root  4096 Aug 15 00:45 postprocessing
-rwxr-xr-x  1 root root  5744 Aug 15 00:45 preprocess.py
-rwxr-xr-x  1 root root    80 Aug 15 00:45 requirements.txt
-rwxr-xr-x  1 root root  9704 Aug 15 00:45 solver.py

=== README grep (pretrained/checkpoint/weights/zenodo/drive/huggingface) ===
readme file: README.md
71:* Checkpoints
74:  * The full experimental records, reports and checkpoints can be found under the [`exp`](./exp/) folder.

=== requirements.txt / pyproject ===
/workspace/yatingmusic/ddsp-singing-vocoders/requirements.txt
--- requirements.txt content (if exists) ---
pyworld
gin-config
einops
local_attention
tensorboardX
pytorch-fast-transformers```

FINDING: pretrained checkpoints (`exp/f1-full/{sins,sawsinsub-256}/ckpts/*.pt`, ~2.3MB each,
6 files, no LFS pointer) are committed directly in git history — no external download needed
for this repo's included checkpoints. `find -iname '*ckpt*' -o -iname '*.pt'` listing above.

### 6b. yoyololicon/golf

```
Cloning into '/workspace/yoyololicon/golf'...
Updating files:  13% (19/136)Updating files:  14% (20/136)Updating files:  15% (21/136)Updating files:  16% (22/136)Updating files:  17% (24/136)Updating files:  18% (25/136)Updating files:  19% (26/136)Updating files:  20% (28/136)Updating files:  21% (29/136)Updating files:  22% (30/136)Updating files:  23% (32/136)Updating files:  24% (33/136)Updating files:  25% (34/136)Updating files:  26% (36/136)Updating files:  27% (37/136)Updating files:  28% (39/136)Updating files:  29% (40/136)Updating files:  30% (41/136)Updating files:  31% (43/136)Updating files:  32% (44/136)Updating files:  33% (45/136)Updating files:  34% (47/136)Updating files:  35% (48/136)Updating files:  36% (49/136)Updating files:  36% (50/136)Updating files:  37% (51/136)Updating files:  38% (52/136)Updating files:  39% (54/136)Updating files:  40% (55/136)Updating files:  41% (56/136)Updating files:  42% (58/136)Updating files:  43% (59/136)Updating files:  44% (60/136)Updating files:  45% (62/136)Updating files:  46% (63/136)Updating files:  47% (64/136)Updating files:  48% (66/136)Updating files:  49% (67/136)Updating files:  50% (68/136)Updating files:  51% (70/136)Updating files:  52% (71/136)Updating files:  53% (73/136)Updating files:  54% (74/136)Updating files:  55% (75/136)Updating files:  56% (77/136)Updating files:  57% (78/136)Updating files:  58% (79/136)Updating files:  59% (81/136)Updating files:  60% (82/136)Updating files:  61% (83/136)Updating files:  62% (85/136)Updating files:  63% (86/136)Updating files:  64% (88/136)Updating files:  65% (89/136)Updating files:  66% (90/136)Updating files:  67% (92/136)Updating files:  68% (93/136)Updating files:  69% (94/136)Updating files:  70% (96/136)Updating files:  71% (97/136)Updating files:  72% (98/136)Updating files:  73% (100/136)Updating files:  74% (101/136)Updating files:  75% (102/136)Updating files:  76% (104/136)Updating files:  77% (105/136)Updating files:  78% (107/136)Updating files:  79% (108/136)Updating files:  80% (109/136)Updating files:  81% (111/136)Updating files:  82% (112/136)Updating files:  83% (113/136)Updating files:  84% (115/136)Updating files:  85% (116/136)Updating files:  86% (117/136)Updating files:  87% (119/136)Updating files:  88% (120/136)Updating files:  89% (122/136)Updating files:  90% (123/136)Updating files:  91% (124/136)Updating files:  92% (126/136)Updating files:  93% (127/136)Updating files:  94% (128/136)Updating files:  95% (130/136)Updating files:  96% (131/136)Updating files:  97% (132/136)Updating files:  98% (134/136)Updating files:  99% (135/136)Updating files: 100% (136/136)Updating files: 100% (136/136), done.
EXIT_CODE=0
```

```
=== du -sh ===
1.2G	/workspace/yoyololicon/golf

=== ls top level ===
total 124
drwxr-xr-x 13 root root 4096 Aug 15 00:45 .
drwxr-xr-x  3 root root 4096 Aug 15 00:45 ..
drwxr-xr-x  8 root root 4096 Aug 15 00:45 .git
-rw-r--r--  1 root root  218 Aug 15 00:45 .gitignore
-rw-r--r--  1 root root  300 Aug 15 00:45 .gitmodules
-rw-r--r--  1 root root 1068 Aug 15 00:45 LICENSE
-rw-r--r--  1 root root 6550 Aug 15 00:45 README.md
-rw-r--r--  1 root root 5006 Aug 15 00:45 V1-README.md
-rw-r--r--  1 root root  681 Aug 15 00:45 autoencode.py
-rw-r--r--  1 root root 3160 Aug 15 00:45 biquads.py
drwxr-xr-x  3 root root 4096 Aug 15 00:45 cfg
drwxr-xr-x  4 root root 4096 Aug 15 00:45 ckpts
-rw-r--r--  1 root root  791 Aug 15 00:45 convert2v2.py
drwxr-xr-x  2 root root 4096 Aug 15 00:45 datasets
-rw-r--r--  1 root root 1772 Aug 15 00:45 eval_pesq.py
-rw-r--r--  1 root root 4114 Aug 15 00:45 fad.py
-rw-r--r--  1 root root 3756 Aug 15 00:45 harm_and_noise.py
drwxr-xr-x  2 root root 4096 Aug 15 00:45 loss
drwxr-xr-x  2 root root 4096 Aug 15 00:45 ltng
-rw-r--r--  1 root root  666 Aug 15 00:45 main.py
drwxr-xr-x  4 root root 4096 Aug 15 00:45 medias
drwxr-xr-x  4 root root 4096 Aug 15 00:45 models
drwxr-xr-x  5 root root 4096 Aug 15 00:45 notebooks
-rw-r--r--  1 root root  181 Aug 15 00:45 requirements.txt
drwxr-xr-x  2 root root 4096 Aug 15 00:45 scripts
-rw-r--r--  1 root root 8179 Aug 15 00:45 test_rtf.py
drwxr-xr-x  2 root root 4096 Aug 15 00:45 tests

=== README grep (pretrained/checkpoint/weights/zenodo/drive/huggingface) ===
readme file: README.md
4:[![DOI](https://zenodo.org/badge/615456464.svg)](https://zenodo.org/doi/10.5281/zenodo.12786788)
6:The accompanying code for the papers [Differentiable Time-Varying Linear Prediction in the Context of End-to-End Analysis-by-Synthesis](https://arxiv.org/abs/2406.05128) (accepted at Interspeech 2024) and [Singing Voice Synthesis Using Differentiable LPC and Glottal-Flow-Inspired Wavetables](https://zenodo.org/records/10265377) (published at ISMIR 2023).
43:By default, the checkpoints are automatically saved under `checkpoints/` directory. 
51:After training the models, you can evaluate the models using the following command. Replace `{YOUR_CONFIG}` and `{YOUR_CHECKPOINT}` with the corresponding configuration file and checkpoint.
54:python autoencode.py test -c {YOUR_CONFIG}.yaml --ckpt_path {YOUR_CHECKPOINT}.ckpt --data.duration 2 --data.overlap 0 --seed_everything false --data.wav_dir data/vctk --data.batch_size 32 --trainer.logger false
59:For PESQ/FAD evaluation, you'll first need to store the synthesised waveforms in a directory. Replace `{YOUR_CONFIG}`, `{YOUR_CHECKPOINT}`, and `{YOUR_OUTPUT_DIR}` with the corresponding configuration file, checkpoint, and output directory.
62:python autoencode.py predict -c {YOUR_CONFIG}.yaml --ckpt_path {YOUR_CHECKPOINT}.ckpt --trainer.logger false --seed_everything false --data.wav_dir data/vctk --trainer.callbacks+=ltng.cli.MyPredictionWriter --trainer.callbacks.output_dir {YOUR_OUTPUT_DIR}
94:Please use the checkpoints trained with `golf.yaml` for the GOLF-fs model. Append `--model.decoder.end_filter models.filters.LTVMinimumPhaseFilterPrecise` to the evaluation commands above (`test/predict`) to use the sample-wise filter.
105:## Checkpoints
107:The checkpoints we used for evaluation are provided [here](ckpts/interspeech24).
111:Use the following command to benchmark the real-time factor of the models. Replace `{YOUR_CONFIG}` and `{YOUR_CHECKPOINT}` with the corresponding configuration file and checkpoint. Add `--cuda` to benchmark on GPU.
114:python test_rtf.py {YOUR_CONFIG}.yaml {YOUR_CHECKPOINT}.ckpt {EXAMPLE_FILE}.wav
137:    doi={10.5281/zenodo.10265377},

=== requirements.txt / pyproject / environment.yml ===
/workspace/yoyololicon/golf/requirements.txt
--- requirements.txt content (if exists) ---
numpy
scipy
pandas
torch>=2.0.0
torchaudio>=2.0.0
lightning[pytorch-extra]
tqdm
matplotlib
kazane
torch_fftconv
pyworld
pysptk
diffsptk
pyloudnorm
soxr
soundfile
fadtk
pesq
torchlpc--- pyproject.toml deps (if exists) ---
NOT FOUND
```

FINDING: repo is 1.2G total; 614M is `ckpts/` (ISMIR23 + Interspeech24 pretrained checkpoints,
.ckpt files, committed directly in git, not LFS) — no external checkpoint host needed for these.

```
$ cat .gitmodules
[submodule "datasets"]
	path = datasets
	url = git@github.com:yoyololicon/pytorch-wav-datasets.git
[submodule "models/audiotensor"]
	path = models/audiotensor
	url = git@github.com:yoyololicon/audiotensor.git
[submodule "models/lru"]
	path = models/lru
	url = git@github.com:yoyololicon/torchlru.git

$ git submodule status  (after plain --depth 1 clone, submodules NOT initialized)
-1e473a1be4e16c27d6a40a71fdcbea92cfc08f2f datasets
-7d3c66a77c3fe96c9eb8103a618135c993371038 models/audiotensor
-b28b93eee72a0ceb2a372c402f68da6a09d122c9 models/lru
```

FINDING: 3 git submodules (`datasets`, `models/audiotensor`, `models/lru`) declared with
`git@github.com:...` SSH URLs in .gitmodules. They are required runtime deps (imported by
models/ code, and `datasets` in requirements is separate pip pkg). SSH-URL submodules were
NOT fetched by the plain --depth 1 clone (empty placeholder dirs, see `git submodule status`
'-' prefix = uninitialized). Whether HTTPS-rewrite or SSH access works through the git proxy
was not tested in this probe (out of scope: instructions said clone the two repos only).

## step 7: checkpoint distribution reachability

```
=== curl -sI https://huggingface.co ===
HTTP/1.1 200 Connection Established

HTTP/2 200 
content-type: text/html; charset=utf-8
content-length: 178618
date: Sat, 15 Aug 2026 00:46:31 GMT
etag: W/"2b9ba-pXSAWQGydm2bHdj4mG6CG0h2nKE"
x-powered-by: huggingface-moon
x-request-id: Root=1-6a7fb6e7-3cad181613e2743044dbf7ab
ratelimit: "pages";r=94;t=53
ratelimit-policy: "fixed window";"pages";q=100;w=300
cross-origin-opener-policy: same-origin
referrer-policy: strict-origin-when-cross-origin
link: </.well-known/ai-catalog.json>; rel="ai-catalog"; type="application/ai-catalog+json"
link: </.well-known/api-catalog>; rel="api-catalog"
x-frame-options: DENY
x-cache: Hit from cloudfront
via: 1.1 422bc44e4c277c4908c02cee9cf0a588.cloudfront.net (CloudFront)
x-amz-cf-pop: JFK50-P9
alt-svc: h3=":443"; ma=86400
x-amz-cf-id: TXoWbF0qo3d_AscNNdyO4lr-1T9AWMa4J-0vTRE688UEQ-tVNEiwCA==
age: 14

exit: 0

=== curl -s "https://huggingface.co/api/models?limit=1" (first 200 chars) ===
[{"_id":"6a72f2e302294c7d32dd278e","id":"Qwen/Qwen3.8-27B","likes":8983,"trendingScore":8677,"private":false,"downloads":2,"tags":["transformers","safetensors","qwen3_5","image-text-to-text","conversa
exit: 0

=== curl -sI https://zenodo.org ===
HTTP/1.1 200 Connection Established

HTTP/1.1 200 OK
server: nginx
date: Sat, 15 Aug 2026 00:46:46 GMT
content-type: text/html; charset=utf-8
content-length: 71621
vary: Accept-Encoding
x-ratelimit-limit: 10
x-ratelimit-remaining: 9
x-ratelimit-reset: 1786754808
retry-after: 1
permissions-policy: interest-cohort=()
x-frame-options: sameorigin
x-xss-protection: 1; mode=block
x-content-type-options: nosniff
content-security-policy: default-src 'self' fonts.googleapis.com *.gstatic.com data: 'unsafe-inline' 'unsafe-eval' blob: zenodo-broker.web.cern.ch zenodo-broker-qa.web.cern.ch maxcdn.bootstrapcdn.com cdnjs.cloudflare.com ajax.googleapis.com webanalytics.web.cern.ch
strict-transport-security: max-age=31556926; includeSubDomains
referrer-policy: strict-origin-when-cross-origin
set-cookie: session=ee3f8fe237d72286_6a7fb6f6.LCvWMjaxH9RUkwgU5RLMyLV9OIM; Expires=Thu, 20 Aug 2026 00:46:46 GMT; Secure; HttpOnly; Path=/; SameSite=Lax
x-source: ui

exit: 0

=== curl -sIL https://github.com/yoyololicon/golf/releases ===
HTTP/1.1 200 Connection Established

HTTP/1.1 403 Forbidden
Content-Type: application/json; charset=utf-8
Content-Length: 378
Connection: close

exit: 0
```

NOTE on github.com/yoyololicon/golf/releases 403: this is NOT a git-clone-layer block (git clone
over https succeeded fine, see step 6). It is the agent proxy's github.com WEB/API access gate —
body: `{"message":"GitHub access to this repository is not enabled for this session. Use add_repo
to request access..."}`. Confirmed via `curl -sS "$HTTPS_PROXY/__agentproxy/status"` (recorded below);
proxy itself is enabled/healthy with gitConfigInjection=true, gitSshRewrite=true, no recentRelayFailures.
add_repo was NOT called (out of scope per task instructions: reachability probe only, HEAD/light GET).

```
$ curl -sS "$HTTPS_PROXY/__agentproxy/status"
{
  "enabled": true,
  "port": 46171,
  "caBundlePath": "/root/.ccr/ca-bundle.crt",
  "hasSystemCa": true,
  "noProxy": "localhost,127.0.0.1,::1,127.0.0.0/8,0.0.0.0/8,::,169.254.0.0/16,anthropic.com,.anthropic.com,*.anthropic.com,registry.npmjs.org,jsr.io,npm.jsr.io,pypi.org,files.pythonhosted.org,index.crates.io,proxy.golang.org,host.docker.internal,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10,.svc.cluster.local,*.svc.cluster.local",
  "selective": false,
  "standalone": false,
  "toolScoped": false,
  "javaTrustStorePath": "/root/.ccr/java-truststore.p12",
  "readmePath": "/root/.ccr/README.md",
  "gitConfigInjection": true,
  "gitSshRewrite": true,
  "recentRelayFailures": []
}
```

## step 8: cleanup + final disk

```
=== pip cache purge ===
Files removed: 368
exit: 0

=== df -h / (final) ===
Filesystem      Size  Used Avail Use% Mounted on
/dev/vda        252G   10G   28G  27% /

=== workspace clone sizes (retained) ===
168M	/workspace/yatingmusic/ddsp-singing-vocoders
1.2G	/workspace/yoyololicon/golf
1.4G	/workspace
```

Disk delta: baseline (step 0) `/` used=7.9G avail=30G -> final used=10G avail=28G.
Net consumption ≈2G: 1.4G retained clones (168M ddsp-singing-vocoders + 1.2G golf, mostly
committed .ckpt/.pt checkpoint files in both repos) + ≈0.6G installed packages
(torch 2.13.0+cpu, torchaudio 2.11.0+cpu, pyworld 0.3.5, pyopenjtalk 0.4.1 + its OpenJTalk
UTF-8 dictionary download ≈23MB). pip cache purged (368 files removed) at the end; clones
were left in place per instructions.
