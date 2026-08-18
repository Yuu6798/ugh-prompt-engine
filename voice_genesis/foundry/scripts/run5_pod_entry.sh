#!/usr/bin/env bash
# S4 run 5 — Pod 起動エントリ（DESIGN_S4_run5.md §3.1 段階 1）。
#
# RunPod Pod 作成 API の起動コマンド（dockerArgs / container start command）に
# 以下 1 行を注入して使う（環境変数 RUN5_* は Pod 作成時の env で注入する —
# S4_RUN5_RUNBOOK.md §2 参照。このファイル自体は「注入する内容の正本」として
# リポジトリに置く。Drive リンク・トークンはここに書かない）:
#
#   bash -lc 'curl -fsSL https://raw.githubusercontent.com/Yuu6798/ugh-prompt-engine/<RUN5_PIN_COMMIT>/voice_genesis/foundry/scripts/run5_pod_entry.sh | RUN5_PIN_COMMIT=<RUN5_PIN_COMMIT> bash'
#
# <RUN5_PIN_COMMIT> は run 5 実行用ブランチの pin コミット SHA（起動前に
# 確定させる — プレースホルダのまま起動しない。S1_GPU_RUNBOOK §3 の
# 「未解決プレースホルダ禁止」規律を踏襲）。
set -euo pipefail

: "${RUN5_PIN_COMMIT:?RUN5_PIN_COMMIT (pin commit SHA) を注入すること}"

WORK="${RUN5_WORK_DIR:-$HOME/s4work}"
REPO="$WORK/ugh-prompt-engine"
mkdir -p "$WORK"

git clone https://github.com/Yuu6798/ugh-prompt-engine.git "$REPO"
git -C "$REPO" checkout "$RUN5_PIN_COMMIT"

cd "$REPO"
pip install -e ".[dev]"
pip install --no-cache-dir praat-parselmouth

# 以降のステージ（4 ゲート → 素材照合 → 再生成 → pin 照合 → 学習 → 退避 →
# 自動停止）は Python 側が単一実行で担う。exit code はマーカーとして
# heartbeat 経由でも Drive へ残る（run5_bootstrap.py docstring 参照）。
exec python voice_genesis/foundry/scripts/run5_bootstrap.py --work-dir "$WORK"
