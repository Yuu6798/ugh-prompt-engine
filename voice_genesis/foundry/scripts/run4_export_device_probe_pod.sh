#!/usr/bin/env bash
# run4 export-device probe — Pod 起動エントリ。
#
# 対応: `docs/`（本ファイルは新規、design memo は本 PR 提出時点でまだ
# `docs/` 化されていない — セッション内 scratchpad
# `design_memo_export_device_probe.md` 参照）。VG-DEBT-008 の残余候補
# (a-2) の単一要因掃引: acoustic ONNX export の実行時デバイス（CUDA vs
# CPU）が、pin 済み入力（run4 40K ckpt + config 4 点 + DiffSinger e2307b1 +
# torch 2.13.0 同版）のもとで ONNX バイト（sha256）を変えるかを測る。
#
# 起動方法（RunPod Pod 作成 API の dockerStartCmd に注入する。
# `run4_export_device_probe_runner.py launch --script-commit <SHA> [--pin-commit <SHA>]`
# が組み立てる。両者は独立: --script-commit は本ファイルを取得する raw URL 側
# （origin へ push 済みの任意コミットでよい）、--pin-commit は下記
# PROBE_PIN_COMMIT env 側で、本ファイルが要求する固定コミット
# cda36b9f... と一致しなければならない）:
#
#   curl -fsSL https://raw.githubusercontent.com/Yuu6798/ugh-prompt-engine/<SCRIPT_COMMIT>/voice_genesis/foundry/scripts/run4_export_device_probe_pod.sh \
#     | PROBE_PIN_COMMIT=<PIN_COMMIT> bash 2>&1 | tee /workspace/probe_console.log
#
# <PIN_COMMIT> は本ファイルが要求する固定コミット cda36b9f... と一致しなければ
# ならない（起動前に確定させる — プレースホルダのまま起動しない。
# S1_GPU_RUNBOOK §3 の「未解決プレースホルダ禁止」規律を踏襲）。
#
# 性質（run5_pod_entry.sh / run8/provision.sh の踏襲）:
#   - fail-closed: 素材 sha 照合が 1 つでも不一致なら測定を実行せず停止する
#   - self-stop 保証: 正常/異常いずれの exit パスでも pod terminate を保証する
#     （グローバル trap + 10800s wall-clock watchdog の二重化）
#   - 結果回収: /workspace/probe_results/ 配下に軽量 JSON 群（+ G1 のみ
#     wav/onnx 実体）を置き、python http.server で回収窓を開く
set -euo pipefail

# ============================================================================
# 0. self-stop 保証の骨組み（trap + watchdog）を、他のどの exit パス
# （pin 未設定・pin 不一致・素材 sha 不一致等）よりも先に確立する。
# ここから下で die()/exit する経路はすべてこの trap を通る。
# 参照するのは静的パスのみ（WORK/RESULTS 等）— PROBE_PIN_COMMIT の検証は
# この後（§1）で行う。
# ============================================================================

readonly WORK="/root/s1work"
readonly REPO="$WORK/ugh-prompt-engine"
readonly DS="$WORK/DiffSinger"
readonly RESULTS="/workspace/probe_results"
readonly GATE_RUN4="$REPO/voice_genesis/foundry/s1_gate/gate_synth_run4.py"

mkdir -p "$WORK" "$RESULTS"

STAGE="init"
DETAIL=""
STAGE_FILE="$RESULTS/.current_stage"

# heartbeat writer loop（背景プロセス）が $STAGE を読めるように、stage() 遷移の
# たびにファイルへも書き出す（背景プロセスは fork 時点のシェル変数しか見えない
# ため、プロセス間で最新値を渡すにはファイル経由しかない — FIX B）。
stage() {
  STAGE="$1"
  echo "| probe: === stage: $1 ==="
  printf '%s' "$STAGE" > "$STAGE_FILE" 2>/dev/null || true
}

die() {
  DETAIL="$*"
  echo "| probe: FATAL stage=$STAGE detail=$DETAIL" >&2
  exit 1
}

on_exit() {
  local ec=$?
  set +e
  local status="ok"
  [ "$ec" -ne 0 ] && status="failed"
  echo "| probe: on_exit ec=$ec status=$status stage=$STAGE"

  # watchdog を真っ先に止める（自分自身が self-stop を保証したので、もう
  # 独立した安全弁は要らない — 結果配信用の長い sleep を挟む前に確実に
  # 止める。setsid 経由で新規プロセスグループに入れているため、そのグルー
  # プごと落とせば sleep 10800 の子プロセスも道連れにできる。setsid が
  # 使えない環境（fallback subshell 経路）向けに、素の kill と直接の子への
  # pkill -P も併用する（どちらか一方が効けば十分）。
  if [ -n "${WATCHDOG_PID:-}" ]; then
    kill -- "-$WATCHDOG_PID" 2>/dev/null || true
    kill "$WATCHDOG_PID" 2>/dev/null || true
    pkill -P "$WATCHDOG_PID" 2>/dev/null || true
  fi

  # heartbeat writer loop も同様に止める（結果 HTTP サーバーはこの後も使うため
  # 生かしたまま — 止めるのは heartbeat の定期書き込みだけ。t=0 起動分は
  # §0b 参照）。
  if [ -n "${HEARTBEAT_PID:-}" ]; then
    kill -- "-$HEARTBEAT_PID" 2>/dev/null || true
    kill "$HEARTBEAT_PID" 2>/dev/null || true
    pkill -P "$HEARTBEAT_PID" 2>/dev/null || true
  fi

  python3 - "$status" "$STAGE" "$DETAIL" "$RESULTS/status.json" <<'PY'
import json, sys
status, stage, detail, out = sys.argv[1:5]
json.dump(
    {"status": status, "stage": stage, "detail_tail": detail},
    open(out, "w"), indent=2, ensure_ascii=False,
)
PY

  if [ -f /workspace/probe_console.log ]; then
    cp -f /workspace/probe_console.log "$RESULTS/probe_console.log" 2>/dev/null || true
  fi

  # 結果 HTTP サーバーは §0b で t=0 からすでに起動している想定 — ここでは
  # SERVER_PID が生きているかを確認し、生きていれば二重起動しない（同じ
  # ポート 8000 への 2 プロセス目は bind に失敗するだけなので実害は薄いが、
  # プロセス管理を単純に保つため確認する）。SERVER_PID 未設定/死んでいる
  # 場合（§0b が何らかの理由でスキップされた等）のみここで起動する。
  if [ -n "${SERVER_PID:-}" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "| probe: result HTTP server already running on :8000 (pid=$SERVER_PID, started at t=0) — not starting a second one"
  else
    echo "| probe: starting result HTTP server on :8000 (dir=$RESULTS)"
    python3 -m http.server 8000 --directory "$RESULTS" &
    SERVER_PID=$!
  fi

  local hold=2700
  [ "$status" != "ok" ] && hold=900
  echo "| probe: holding ${hold}s so the runner can fetch $RESULTS over :8000"
  sleep "$hold"

  kill "$SERVER_PID" 2>/dev/null || true

  if [ -n "${RUNPOD_POD_ID:-}" ]; then
    echo "| probe: self-stop via runpodctl (pod=$RUNPOD_POD_ID)"
    runpodctl stop pod "$RUNPOD_POD_ID" || echo "| probe: runpodctl stop failed (non-fatal)" >&2
  else
    echo "| probe: RUNPOD_POD_ID unset — self-stop skipped (local run?)"
  fi
}
trap on_exit EXIT

# 10800s (3h) wall-clock watchdog: 主フローが trap まで到達できずハング
# した場合でも、独立したバックグラウンド process が課金を止める。
# setsid で新規セッション/プロセスグループを起こせる環境では、on_exit が
# そのグループごと kill できるようにする（sleep 10800 の子プロセスの
# 生き残りを防ぐ）。setsid が無い環境では素の subshell にフォールバックし、
# on_exit 側の pkill -P で直接の子（sleep）を狙い撃ちする。
_watchdog_body() {
  sleep 10800
  {
    echo "| probe: watchdog: 10800s wall-clock reached — forcing self-stop"
    if [ -n "${RUNPOD_POD_ID:-}" ]; then
      runpodctl stop pod "$RUNPOD_POD_ID" || true
    fi
  } >>"$RESULTS/watchdog.log" 2>&1
}
export -f _watchdog_body
export RESULTS
export RUNPOD_POD_ID
if command -v setsid >/dev/null 2>&1; then
  setsid bash -c _watchdog_body &
else
  ( _watchdog_body ) &
fi
WATCHDOG_PID=$!
disown || true

# ============================================================================
# 0b. heartbeat writer + 結果 HTTP サーバーを t=0 で起動する。
# 旧実装は結果サーバーを exit trap の中でしか起動していなかったため、
# コンテナディスク（volumeInGb=0 だった時代の /workspace 相当）が pod
# 停止とともに消えると、中間経過を外から一切観測できなかった
# （実インシデントの一因）。ここで早期に開けば、runner 側は heartbeat.txt /
# heartbeat.json をポーリングするだけで「channel is up」を確認でき、
# probe_console.partial.log で進捗ログも追える。
# ============================================================================

_heartbeat_body() {
  local tick=0
  local now stg
  while :; do
    now="$(date -u +%FT%TZ)"
    date -u +%FT%TZ > "$RESULTS/heartbeat.txt.tmp" && mv -f "$RESULTS/heartbeat.txt.tmp" "$RESULTS/heartbeat.txt"

    stg="$(cat "$STAGE_FILE" 2>/dev/null || echo init)"
    printf '{"utc": "%s", "stage": "%s"}\n' "$now" "$stg" > "$RESULTS/heartbeat.json.tmp" \
      && mv -f "$RESULTS/heartbeat.json.tmp" "$RESULTS/heartbeat.json"
    echo "| probe: heartbeat utc=$now stage=$stg"

    tick=$((tick + 1))
    # probe_console.log は /workspace の外（tee 先が dockerStartCmd 起動時点の
    # /workspace/probe_console.log 自体なので実は $RESULTS 配下ではない —
    # 60s ごと（3 tick * 20s）に $RESULTS 側へコピーして回収対象にする。
    if [ "$((tick % 3))" -eq 0 ] && [ -f /workspace/probe_console.log ]; then
      cp -f /workspace/probe_console.log "$RESULTS/probe_console.partial.log" 2>/dev/null || true
    fi
    sleep 20
  done
}
export -f _heartbeat_body
export RESULTS
export STAGE_FILE
if command -v setsid >/dev/null 2>&1; then
  setsid bash -c _heartbeat_body &
else
  ( _heartbeat_body ) &
fi
HEARTBEAT_PID=$!
disown || true

echo "| probe: starting result HTTP server on :8000 (dir=$RESULTS) at t=0"
python3 -m http.server 8000 --directory "$RESULTS" &
SERVER_PID=$!
disown || true

# ============================================================================
# 1. PROBE_PIN_COMMIT の検証（trap/watchdog/heartbeat/server 確立後 — ここより
# 下の die() はすべて self-stop 保証の傘の中）
# ============================================================================

[ -n "${PROBE_PIN_COMMIT:-}" ] || die "PROBE_PIN_COMMIT (pin commit SHA) を注入すること"

# ============================================================================
# 2. 定数（pin 群。すべて facts note / design memo から埋め込み）
# ============================================================================

readonly EXPECTED_PIN_COMMIT="cda36b9f2308128797c48976a9c90b28a4f1661a"
readonly GATE_SYNTH_RUN4_SHA="006cd867042d8f976822f2d74dc6bf184c5687a6a125e41914fba0c5c0faf87f"
# gate_synth.py / score.py / score_umi.py / phoneme_jp.py の blob sha256 は
# 本 PR 提出時点で `git show cda36b9f...:<path> | sha256sum` によりセッション内で
# 実測して埋め込んだ値（facts note にはこれらの pin が無かったため — 実装ノート参照）。
readonly GATE_SYNTH_SHA="4c23b5d85cadcf0b3f7ffa7a7b5b1f2b2b34dc5a3a9535075b76f707e2ba187a"
readonly SCORE_PY_SHA="24a6004daeb0b5d02c55c87642c6eac2cb2ca931c97570d1be666b6a57ade116"
readonly SCORE_UMI_PY_SHA="59106e629bd523d8f6272e768ff6b96a4fcb22756314f382c865da2e1c68561e"
readonly PHONEME_JP_PY_SHA="9cbc7e1d34771bb0db9a44a2fa1dbd3bbdc96e837afc497b3ca97dade55e70fc"

readonly DIFFSINGER_COMMIT="e2307b1080b00f3999702ce9017cfd75c7f862fe"

readonly CKPT_PART00_ID="18p0-nTbCQTtGf3IDdrLOoSCuQgX7Casb"
readonly CKPT_PART01_ID="1FB5xhCm1SUrjlt4eLOlHumfg9-5f38K1"
readonly CKPT_PART02_ID="1w4JfwdpD-zmjd8gvCXgwbTNXmJj9qawu"
readonly CKPT_PART00_SIZE=188743680
readonly CKPT_PART01_SIZE=188743680
readonly CKPT_PART02_SIZE=178521250
readonly CKPT_SHA="3921f96ac19a9cf5bc8ab246a147cf778044cf5949dd24cd87246f24e52c76ec"

readonly CONFIG_YAML_ID="1N1NoTPyZzv_KCZRI03zkPryPzx7DAylY"
readonly CONFIG_YAML_SHA="419baf782ea8a3b4b5e9ab37d94f738883a98bfff25ead4e5ae671f18b954ef7"
readonly SPK_MAP_ID="1hVGRgNglSZZkK-UggyEOHiRBJzzeQLTZ"
readonly SPK_MAP_SHA="0908f079e397fad4fc63be46da7852dc25545a3ad667444ff4d5d27cd5c4a13c"
readonly LANG_MAP_ID="1mRhqFd8aGqm2rRMt598YBw_IDmiQUMQb"
readonly LANG_MAP_SHA="2a6a227ee65a49f5c30e848a4b62c5cc1817926bbdab373228e6302d2c794953"
readonly DICT_ID="1s7LEOX7xebYFLeQOA-oTIfkjc_BQwaJq"
readonly DICT_SHA="b8ea0d99fcf60e82319cc84b162d9e1b4d5ce1146cfa1c6291e025fbb8be14ef"

readonly CANON_ZIP_URL="https://www.canon-voice.com/voice/NamineRitsu_DiffSinger.zip"
readonly CANON_ZIP_SHA="5c7b8c328180ea2971f71d89b3a675b2adfc91772664ae28cbb5915385f42530"
# 4 消費メンバの sha256 は facts note に無かったため、本 PR 提出時点で
# セッション内でこの zip（全体 sha は facts note pin と一致確認済み）を取得し、
# python zipfile 経由の per-member streaming hash で実測して埋め込んだ
# （実装ノート参照。zip 自体は入れ子: 全メンバが `NamineRitsu_DiffSinger/` 配下）。
readonly CANON_LINGUISTIC_SHA="1c9ec9f67277a2ba4b9c3f815150251ed7d87ad54eed3e22f8d85dbda74705b6"
readonly CANON_DUR_SHA="11bbfad5c489a57e05bd6ed7e239b3fce913a6b644d9281ae152126563a3d288"
readonly CANON_PITCH_SHA="e361ad13053c4b49331a44296148bb33396092f57ca477ceed60e59cdbdfb3b9"
readonly CANON_PHONEMES_SHA="1489af3c4806ad2cfc10e663ec27a1bf7c6bf0d6f9a047263948c5cbe36eebfb"

readonly VOC_OUDEP_URL="https://github.com/xunmengshe/OpenUtau/releases/download/0.0.0.0/nsf_hifigan.oudep"
readonly VOC_OUDEP_SHA="e22f84009804da2e5916e7a2000f4c30278148796376e49368ec5ff8f9f58830"
readonly VOC_ONNX_SHA="a3e26672a8c655e3faf65f31cb4339a7fbca7758ba86be9af89e03dced7c3fa4"

# 判定用の記録済み参照値（gate ではなく比較対象。probe_results.json へ embed）。
readonly SESSION_CPU_ONNX_SHA="a6da561a66ece62d64d4a0635a5ef525c2b6143eb311c1a7e1d595d9abd3a676"
readonly RECORDED_WAV_RITSU_SAKURA="55bd14c28437dbfcf06b564343f4bcff02b9bea6c9c1f752a72dbf78f3ec9ef9"
readonly RECORDED_WAV_RITSU_UMI="a12ef548d9649f86854804f9676e88aa8ed76528ea0592859f752fed9064bb9e"
readonly RECORDED_WAV_PJS_SAKURA="b9ce3454854e870075ee001fa5cdf937dc11891f047c497a1e40fcf44aabf279"
readonly RECORDED_WAV_PJS_UMI="43d6bff14b1eebdd6e8f9e8aa58bde6bae21e2c23df21e29205974f2d491150f"
readonly RECORDED_WAV_USER_SAKURA="e2f7b270c2840012178e50147491a7db1f38f47aee9d4ff487ace60e12c94ea0"
readonly RECORDED_WAV_USER_UMI="ac8b3455b4204a6e55d34b7db2e81709f1e6f8bc6f20d6d7462767a4dcf28979"

if [ "$PROBE_PIN_COMMIT" != "$EXPECTED_PIN_COMMIT" ]; then
  die "PROBE_PIN_COMMIT=$PROBE_PIN_COMMIT != expected $EXPECTED_PIN_COMMIT"
fi

# ============================================================================
# ユーティリティ
# ============================================================================

sha256_of() { sha256sum "$1" 2>/dev/null | cut -d' ' -f1; }

require_sha() {
  local path="$1" want="$2" label="$3" got
  # sha256_of の内側パイプは pipefail 下で「右端の非ゼロ終了」= sha256sum の
  # 失敗（対象ファイル不在）をそのまま拾って非ゼロを返す。単純代入
  # `got="$(...)"` はコマンド置換の終了ステータスをそのまま自分の終了ス
  # テータスとするため、-e 下ではここで MISSING 診断（次行の die）に届く
  # 前にスクリプトごと落ちる。`|| echo MISSING` で必ず 0 終了させ、die の
  # 診断メッセージへ到達させる。
  got="$(sha256_of "$path" 2>/dev/null || echo MISSING)"
  if [ "$got" != "$want" ]; then
    die "sha256 mismatch: $label ($path) got=${got:-MISSING} want=$want"
  fi
  echo "| probe: sha OK: $label ${got:0:16}..."
}

# Google Drive 直リンク（unauth endpoint）から sha256 pin 付きで取得する。
# 失敗/不一致は最大 3 回リトライ。.part へ落としてから検証済みのみ mv。
fetch_drive() {
  local id="$1" dest="$2" want="$3" label="$4"
  local url="https://drive.usercontent.google.com/download?id=${id}&export=download&confirm=t"
  mkdir -p "$(dirname "$dest")"
  local attempt got
  for attempt in 1 2 3; do
    echo "| probe: fetch_drive $label attempt=$attempt"
    rm -f "${dest}.part"
    if curl -fsS -L --max-time 600 -o "${dest}.part" "$url"; then
      got="$(sha256_of "${dest}.part")"
      if [ "$got" = "$want" ]; then
        mv -f "${dest}.part" "$dest"
        echo "| probe: fetch_drive OK $label ${got:0:16}..."
        return 0
      fi
      echo "| probe: fetch_drive sha mismatch $label attempt=$attempt got=${got:-MISSING}" >&2
    else
      echo "| probe: fetch_drive curl failed $label attempt=$attempt" >&2
    fi
    rm -f "${dest}.part"
    sleep 5
  done
  die "fetch_drive: failed after 3 attempts: $label (id=$id)"
}

# サイズのみで検証する ckpt パート取得（個々のパートに sha256 pin は無い —
# 結合後の最終ファイルにのみ pin がある。facts note 準拠）。
fetch_drive_sized() {
  local id="$1" dest="$2" want_size="$3" label="$4"
  local url="https://drive.usercontent.google.com/download?id=${id}&export=download&confirm=t"
  mkdir -p "$(dirname "$dest")"
  local attempt got_size
  for attempt in 1 2 3; do
    echo "| probe: fetch_drive_sized $label attempt=$attempt"
    rm -f "${dest}.part"
    if curl -fsS -L --max-time 900 -o "${dest}.part" "$url"; then
      got_size="$(stat -c%s "${dest}.part" 2>/dev/null || stat -f%z "${dest}.part")"
      if [ "$got_size" = "$want_size" ]; then
        mv -f "${dest}.part" "$dest"
        echo "| probe: fetch_drive_sized OK $label size=$got_size"
        return 0
      fi
      echo "| probe: fetch_drive_sized size mismatch $label attempt=$attempt got=$got_size want=$want_size" >&2
    else
      echo "| probe: fetch_drive_sized curl failed $label attempt=$attempt" >&2
    fi
    rm -f "${dest}.part"
    sleep 5
  done
  die "fetch_drive_sized: failed after 3 attempts: $label (id=$id)"
}

# 素の URL から sha256 pin 付きで取得する（canon zip / vocoder oudep）。
fetch_url() {
  local url="$1" dest="$2" want="$3" label="$4"
  mkdir -p "$(dirname "$dest")"
  local attempt got
  for attempt in 1 2 3; do
    echo "| probe: fetch_url $label attempt=$attempt url=$url"
    rm -f "${dest}.part"
    if curl -fsS -L --max-time 600 -o "${dest}.part" "$url"; then
      got="$(sha256_of "${dest}.part")"
      if [ "$got" = "$want" ]; then
        mv -f "${dest}.part" "$dest"
        echo "| probe: fetch_url OK $label ${got:0:16}..."
        return 0
      fi
      echo "| probe: fetch_url sha mismatch $label attempt=$attempt got=${got:-MISSING}" >&2
    else
      echo "| probe: fetch_url curl failed $label attempt=$attempt" >&2
    fi
    rm -f "${dest}.part"
    sleep 5
  done
  die "fetch_url: failed after 3 attempts: $label"
}

# <stepdir> 直下の *.wav を列挙する（sort 済みなので song 名のアルファベット
# 順 = sakura, umi の順で安定する）。process substitution 経由で呼ばれるため
# ここでは die() を呼ばない（`<(...)` はサブシェルで走り、その中の `exit` は
# 呼び出し元スクリプトを止めない — fail-closed が壊れる）。件数チェック +
# die() は呼び出し元（mapfile で受けた後）で行う。
list_wavs() {
  local stepdir="$1"
  find "$stepdir" -maxdepth 1 -type f -name '*.wav' | sort
}

# mapfile で受けた配列がちょうど 2 件であることを確定する（不一致は
# 呼び出し元の shell で直接 die() する — 上記コメント参照）。
require_two_wavs() {
  local -n _arr="$1"
  local label="$2"
  if [ "${#_arr[@]}" -ne 2 ]; then
    die "expected 2 wavs ($label), found ${#_arr[@]}: ${_arr[*]:-none}"
  fi
}

# CUDA 可視性を切り替えて gate_synth_run4.py launch1（export+ritsu）を走らせる。
# $1=py_bin $2=diffsinger_repo $3=ckpt_dir $4=canon_dir $5=vocoder_dir
# $6=out_dir $7=cvd_mode ("gpu"|"cpu")
gate_run_launch1() {
  local py="$1" ds="$2" ckptdir="$3" canon="$4" voc="$5" outdir="$6" cvd_mode="$7"
  echo "| probe: gate_run_launch1 out=$outdir cvd_mode=$cvd_mode py=$py"
  if [ "$cvd_mode" = "cpu" ]; then
    env CUDA_VISIBLE_DEVICES= "$py" "$GATE_RUN4" run \
      --diffsinger-repo "$ds" --ckpt-dir "$ckptdir" --exp-name s3_run4_acoustic --step 40000 \
      --canon-model-dir "$canon" --vocoder-dir "$voc" --out-dir "$outdir" \
      --song sakura,umi --speaker ritsu
  else
    "$py" "$GATE_RUN4" run \
      --diffsinger-repo "$ds" --ckpt-dir "$ckptdir" --exp-name s3_run4_acoustic --step 40000 \
      --canon-model-dir "$canon" --vocoder-dir "$voc" --out-dir "$outdir" \
      --song sakura,umi --speaker ritsu
  fi
}

# --skip-export で既存 acoustic-dir を再利用する launch2/3 相当（pjs/user）。
gate_run_skip_export() {
  local py="$1" ds="$2" ckptdir="$3" acoustic_dir="$4" canon="$5" voc="$6" outdir="$7" speaker="$8"
  echo "| probe: gate_run_skip_export out=$outdir speaker=$speaker"
  "$py" "$GATE_RUN4" run \
    --diffsinger-repo "$ds" --ckpt-dir "$ckptdir" --exp-name s3_run4_acoustic --step 40000 \
    --skip-export --acoustic-dir "$acoustic_dir" \
    --canon-model-dir "$canon" --vocoder-dir "$voc" --out-dir "$outdir" \
    --song sakura,umi --speaker "$speaker"
}

# ============================================================================
# Stage 0: env snapshot
# ============================================================================
stage "0-env-snapshot"

NVIDIA_SMI_OUT="$(nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>&1 || echo 'nvidia-smi unavailable')"
CPU_MODEL="$(grep -m1 'model name' /proc/cpuinfo 2>/dev/null | sed 's/^[^:]*:[[:space:]]*//' || echo unknown)"
LDD_VERSION="$(ldd --version 2>&1 | head -1 || echo unknown)"
OS_RELEASE_HEAD="$(head -2 /etc/os-release 2>/dev/null || echo unknown)"
IMAGE_NAME="${RUNPOD_POD_IMAGE:-${RUNPOD_IMAGE_NAME:-unknown}}"
DATE_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

export NVIDIA_SMI_OUT CPU_MODEL LDD_VERSION OS_RELEASE_HEAD IMAGE_NAME DATE_UTC RUNPOD_POD_ID
python3 - "$RESULTS/env_snapshot.json" <<'PY'
import json, os, sys
out = sys.argv[1]
json.dump({
    "nvidia_smi": os.environ.get("NVIDIA_SMI_OUT", ""),
    "cpu_model_name": os.environ.get("CPU_MODEL", ""),
    "ldd_version": os.environ.get("LDD_VERSION", ""),
    "os_release_head": os.environ.get("OS_RELEASE_HEAD", ""),
    "image_name": os.environ.get("IMAGE_NAME", ""),
    "date_utc": os.environ.get("DATE_UTC", ""),
    "runpod_pod_id": os.environ.get("RUNPOD_POD_ID", ""),
}, open(out, "w"), indent=2, ensure_ascii=False)
PY
cat "$RESULTS/env_snapshot.json"

# ============================================================================
# Stage 1: materials（fail-closed。1 件でも sha 不一致なら測定せず停止する）
# ============================================================================
stage "1-materials-repo"

git clone https://github.com/Yuu6798/ugh-prompt-engine.git "$REPO"
git -C "$REPO" checkout "$PROBE_PIN_COMMIT"
ACTUAL_HEAD="$(git -C "$REPO" rev-parse HEAD)"
[ "$ACTUAL_HEAD" = "$PROBE_PIN_COMMIT" ] || die "repo checkout HEAD=$ACTUAL_HEAD != pin $PROBE_PIN_COMMIT"

require_sha "$REPO/voice_genesis/foundry/s1_gate/gate_synth_run4.py" "$GATE_SYNTH_RUN4_SHA" "gate_synth_run4.py"
require_sha "$REPO/voice_genesis/foundry/s1_gate/gate_synth.py" "$GATE_SYNTH_SHA" "gate_synth.py"
require_sha "$REPO/voice_genesis/singer/score.py" "$SCORE_PY_SHA" "singer/score.py"
require_sha "$REPO/voice_genesis/singer/score_umi.py" "$SCORE_UMI_PY_SHA" "singer/score_umi.py"
require_sha "$REPO/voice_genesis/singer/phoneme_jp.py" "$PHONEME_JP_PY_SHA" "singer/phoneme_jp.py"

stage "1-materials-diffsinger"
git clone https://github.com/openvpi/DiffSinger.git "$DS"
git -C "$DS" checkout "$DIFFSINGER_COMMIT"
DS_HEAD="$(git -C "$DS" rev-parse HEAD)"
[ "$DS_HEAD" = "$DIFFSINGER_COMMIT" ] || die "DiffSinger HEAD=$DS_HEAD != pin $DIFFSINGER_COMMIT"

stage "1-materials-ckpt"
CKPT_DIR="$DS/checkpoints/s3_run4_acoustic"
mkdir -p "$CKPT_DIR"
fetch_drive_sized "$CKPT_PART00_ID" "$CKPT_DIR/part00" "$CKPT_PART00_SIZE" "ckpt-part00"
fetch_drive_sized "$CKPT_PART01_ID" "$CKPT_DIR/part01" "$CKPT_PART01_SIZE" "ckpt-part01"
fetch_drive_sized "$CKPT_PART02_ID" "$CKPT_DIR/part02" "$CKPT_PART02_SIZE" "ckpt-part02"
cat "$CKPT_DIR/part00" "$CKPT_DIR/part01" "$CKPT_DIR/part02" > "$CKPT_DIR/model_ckpt_steps_40000.ckpt"
require_sha "$CKPT_DIR/model_ckpt_steps_40000.ckpt" "$CKPT_SHA" "run4-40k-ckpt"
rm -f "$CKPT_DIR/part00" "$CKPT_DIR/part01" "$CKPT_DIR/part02"

stage "1-materials-config-bundle"
fetch_drive "$CONFIG_YAML_ID" "$CKPT_DIR/config.yaml" "$CONFIG_YAML_SHA" "config.yaml"
fetch_drive "$SPK_MAP_ID" "$CKPT_DIR/spk_map.json" "$SPK_MAP_SHA" "spk_map.json"
fetch_drive "$LANG_MAP_ID" "$CKPT_DIR/lang_map.json" "$LANG_MAP_SHA" "lang_map.json"
fetch_drive "$DICT_ID" "$CKPT_DIR/dictionary-ja.txt" "$DICT_SHA" "dictionary-ja.txt"

stage "1-materials-canon-zip"
CANON_ZIP="$WORK/NamineRitsu_DiffSinger.zip"
fetch_url "$CANON_ZIP_URL" "$CANON_ZIP" "$CANON_ZIP_SHA" "canon-zip"
mkdir -p "$WORK/ritsu_diffsinger_extracted"
python3 -m zipfile -e "$CANON_ZIP" "$WORK/ritsu_diffsinger_extracted"
CANON_DIR="$WORK/ritsu_diffsinger_extracted/NamineRitsu_DiffSinger"
require_sha "$CANON_DIR/linguistic.onnx" "$CANON_LINGUISTIC_SHA" "canon-linguistic.onnx"
require_sha "$CANON_DIR/dsdur/dur.onnx" "$CANON_DUR_SHA" "canon-dsdur-dur.onnx"
require_sha "$CANON_DIR/dspitch/pitch.onnx" "$CANON_PITCH_SHA" "canon-dspitch-pitch.onnx"
require_sha "$CANON_DIR/phonemes.txt" "$CANON_PHONEMES_SHA" "canon-phonemes.txt"
rm -f "$CANON_ZIP"

stage "1-materials-vocoder"
VOC_OUDEP="$WORK/nsf_hifigan.oudep"
fetch_url "$VOC_OUDEP_URL" "$VOC_OUDEP" "$VOC_OUDEP_SHA" "vocoder-oudep"
mkdir -p "$WORK/vocoder_onnx"
python3 -m zipfile -e "$VOC_OUDEP" "$WORK/vocoder_onnx"
require_sha "$WORK/vocoder_onnx/nsf_hifigan.onnx" "$VOC_ONNX_SHA" "vocoder-nsf-hifigan.onnx"
rm -f "$VOC_OUDEP"

stage "1-materials-pristine-snapshot"
# C1b（path-embedding sensitivity）用の DiffSinger 清浄スナップショット。
# ここまでの全素材（checkpoints/s3_run4_acoustic 含む）配置後・export 実行前に
# 取得するため、tar 展開だけで s1work_alt 側に「一度も export していない
# DiffSinger clone」を用意できる（別コミットの再取得は不要 — バイト同一性が
# tar により構造的に保証される）。
tar -C "$WORK" -czf "$WORK/diffsinger_pristine.tar.gz" DiffSinger
echo "| probe: pristine DiffSinger snapshot sha256=$(sha256_of "$WORK/diffsinger_pristine.tar.gz")"

# ============================================================================
# Stage 2: venv A（GPU export 用。run8/provision.sh の export venv レシピを
# 踏襲しつつ、torch は cu126 index から取得する — これが今回の操作変数）
# ============================================================================
stage "2-venv-a"

VENV_A="$WORK/venv_export"
python3 -m venv "$VENV_A"
PY_A="$VENV_A/bin/python3"
"$VENV_A/bin/pip" install --upgrade pip
"$VENV_A/bin/pip" install torch==2.13.0+cu126 --index-url https://download.pytorch.org/whl/cu126
"$VENV_A/bin/pip" install -r "$DS/requirements.txt"
# DiffSinger requirements.txt は numpy<2.0.0 / onnx>=1.21.0 のレンジ指定のみで
# pin ではない。provision.sh と同じ順序（requirements 後に再 pin）で
# 今回の固定 pin へ戻す。
"$VENV_A/bin/pip" install \
  numpy==1.26.4 onnx==1.22.0 onnxruntime==1.29.0 lightning==2.3.3 pyyaml==6.0.3

echo "| probe: venv A CUDA gate check"
if ! "$PY_A" -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)"; then
  die "venv A gate failed: torch.cuda.is_available() is False (fail-closed — will not fall through to CPU export for G1/G2)"
fi

"$PY_A" - "$RESULTS/venv_a_env.json" <<'PY'
import json, sys
out = sys.argv[1]
info = {}
try:
    import torch
    info["torch_version"] = torch.__version__
    info["torch_cuda_version"] = torch.version.cuda
    info["torch_cuda_available"] = torch.cuda.is_available()
    info["torch_cuda_device_name"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
except Exception as e:
    info["torch_error"] = str(e)
try:
    import numpy as np
    info["numpy_version"] = np.__version__
    try:
        info["numpy_cpu_features"] = dict(np.core._multiarray_umath.__cpu_features__)
    except Exception as e:
        info["numpy_cpu_features_error"] = str(e)
    try:
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            np.show_config()
        info["numpy_show_config_text"] = buf.getvalue()
    except Exception as e:
        info["numpy_show_config_error"] = str(e)
except Exception as e:
    info["numpy_error"] = str(e)
try:
    import onnxruntime as ort
    info["onnxruntime_version"] = ort.__version__
    info["onnxruntime_providers"] = ort.get_available_providers()
except Exception as e:
    info["onnxruntime_error"] = str(e)
json.dump(info, open(out, "w"), indent=2, ensure_ascii=False)
print(json.dumps(info, indent=2, ensure_ascii=False))
PY

# ============================================================================
# Stage 3: Arm G1（CUDA visible, venv A）— 3 launches 通し
# ============================================================================
stage "3-arm-g1"

G1_RITSU_DIR="$WORK/gate40k_run4"
G1_PJS_DIR="$WORK/gate40k_run4_pjs"
G1_USER_DIR="$WORK/gate40k_run4_user"

gate_run_launch1 "$PY_A" "$DS" "$CKPT_DIR" "$CANON_DIR" "$WORK/vocoder_onnx" "$G1_RITSU_DIR" gpu
G1_ONNX="$G1_RITSU_DIR/onnx_gate_40000/acoustic.onnx"
[ -f "$G1_ONNX" ] || die "G1: acoustic.onnx not produced at $G1_ONNX"

gate_run_skip_export "$PY_A" "$DS" "$CKPT_DIR" "$G1_RITSU_DIR/onnx_gate_40000" "$CANON_DIR" "$WORK/vocoder_onnx" "$G1_PJS_DIR" pjs
gate_run_skip_export "$PY_A" "$DS" "$CKPT_DIR" "$G1_RITSU_DIR/onnx_gate_40000" "$CANON_DIR" "$WORK/vocoder_onnx" "$G1_USER_DIR" user

mapfile -t G1_RITSU_WAVS < <(list_wavs "$G1_RITSU_DIR/step_40000")
require_two_wavs G1_RITSU_WAVS "g1-ritsu"
mapfile -t G1_PJS_WAVS < <(list_wavs "$G1_PJS_DIR/step_40000")
require_two_wavs G1_PJS_WAVS "g1-pjs"
mapfile -t G1_USER_WAVS < <(list_wavs "$G1_USER_DIR/step_40000")
require_two_wavs G1_USER_WAVS "g1-user"

G1_ONNX_SHA="$(sha256_of "$G1_ONNX")"
G1_RITSU_SAKURA_SHA="$(sha256_of "${G1_RITSU_WAVS[0]}")"
G1_RITSU_UMI_SHA="$(sha256_of "${G1_RITSU_WAVS[1]}")"
G1_PJS_SAKURA_SHA="$(sha256_of "${G1_PJS_WAVS[0]}")"
G1_PJS_UMI_SHA="$(sha256_of "${G1_PJS_WAVS[1]}")"
G1_USER_SAKURA_SHA="$(sha256_of "${G1_USER_WAVS[0]}")"
G1_USER_UMI_SHA="$(sha256_of "${G1_USER_WAVS[1]}")"

echo "| probe: G1 onnx=$G1_ONNX_SHA"
echo "| probe: G1 wavs ritsu=($G1_RITSU_SAKURA_SHA,$G1_RITSU_UMI_SHA) pjs=($G1_PJS_SAKURA_SHA,$G1_PJS_UMI_SHA) user=($G1_USER_SAKURA_SHA,$G1_USER_UMI_SHA)"

mkdir -p "$RESULTS/g1"
cp -f "$G1_ONNX" "$RESULTS/g1/acoustic.onnx"
cp -f "${G1_RITSU_WAVS[@]}" "$RESULTS/g1/" 2>/dev/null || true
cp -f "${G1_PJS_WAVS[@]}" "$RESULTS/g1/" 2>/dev/null || true
cp -f "${G1_USER_WAVS[@]}" "$RESULTS/g1/" 2>/dev/null || true
cp -f "$G1_RITSU_DIR/step_40000/gate_synth_summary.json" "$RESULTS/g1/gate_synth_summary_ritsu.json" 2>/dev/null || true
cp -f "$G1_PJS_DIR/step_40000/gate_synth_summary.json" "$RESULTS/g1/gate_synth_summary_pjs.json" 2>/dev/null || true
cp -f "$G1_USER_DIR/step_40000/gate_synth_summary.json" "$RESULTS/g1/gate_synth_summary_user.json" 2>/dev/null || true

# ============================================================================
# Stage 4: Arm G2（CUDA visible, venv A, 決定論チェック — launch1 のみ再実行）
# ============================================================================
stage "4-arm-g2"

G2_DIR="$WORK/gate40k_run4_rep"
gate_run_launch1 "$PY_A" "$DS" "$CKPT_DIR" "$CANON_DIR" "$WORK/vocoder_onnx" "$G2_DIR" gpu
G2_ONNX="$G2_DIR/onnx_gate_40000/acoustic.onnx"
[ -f "$G2_ONNX" ] || die "G2: acoustic.onnx not produced at $G2_ONNX"
mapfile -t G2_WAVS < <(list_wavs "$G2_DIR/step_40000")
require_two_wavs G2_WAVS "g2"

G2_ONNX_SHA="$(sha256_of "$G2_ONNX")"
G2_SAKURA_SHA="$(sha256_of "${G2_WAVS[0]}")"
G2_UMI_SHA="$(sha256_of "${G2_WAVS[1]}")"
echo "| probe: G2 onnx=$G2_ONNX_SHA wavs=($G2_SAKURA_SHA,$G2_UMI_SHA)"

# ============================================================================
# Stage 5: Arm C1（CUDA_VISIBLE_DEVICES="", venv A）+ 条件付き C1b
# ============================================================================
stage "5-arm-c1"

C1_DIR="$WORK/gate40k_run4_c1"
gate_run_launch1 "$PY_A" "$DS" "$CKPT_DIR" "$CANON_DIR" "$WORK/vocoder_onnx" "$C1_DIR" cpu
C1_ONNX="$C1_DIR/onnx_gate_40000/acoustic.onnx"
[ -f "$C1_ONNX" ] || die "C1: acoustic.onnx not produced at $C1_ONNX"
mapfile -t C1_WAVS < <(list_wavs "$C1_DIR/step_40000")
require_two_wavs C1_WAVS "c1"

C1_ONNX_SHA="$(sha256_of "$C1_ONNX")"
C1_SAKURA_SHA="$(sha256_of "${C1_WAVS[0]}")"
C1_UMI_SHA="$(sha256_of "${C1_WAVS[1]}")"
echo "| probe: C1 onnx=$C1_ONNX_SHA wavs=($C1_SAKURA_SHA,$C1_UMI_SHA)"

C1B_TRIGGERED="false"
C1B_ONNX_SHA=""
C1B_SAKURA_SHA=""
C1B_UMI_SHA=""
if [ "$C1_ONNX_SHA" != "$SESSION_CPU_ONNX_SHA" ]; then
  stage "5b-arm-c1b"
  C1B_TRIGGERED="true"
  echo "| probe: C1 onnx != session CPU onnx pin -> running C1b (path-embedding sensitivity) under /root/s1work_alt"
  ALT_WORK="/root/s1work_alt"
  mkdir -p "$ALT_WORK"
  tar -C "$ALT_WORK" -xzf "$WORK/diffsinger_pristine.tar.gz"
  cp -a "$WORK/ritsu_diffsinger_extracted" "$ALT_WORK/"
  cp -a "$WORK/vocoder_onnx" "$ALT_WORK/"
  ALT_DS="$ALT_WORK/DiffSinger"
  ALT_CKPT_DIR="$ALT_DS/checkpoints/s3_run4_acoustic"
  ALT_CANON_DIR="$ALT_WORK/ritsu_diffsinger_extracted/NamineRitsu_DiffSinger"
  ALT_VOC_DIR="$ALT_WORK/vocoder_onnx"
  C1B_DIR="$ALT_WORK/gate40k_run4_c1b"
  gate_run_launch1 "$PY_A" "$ALT_DS" "$ALT_CKPT_DIR" "$ALT_CANON_DIR" "$ALT_VOC_DIR" "$C1B_DIR" cpu
  C1B_ONNX="$C1B_DIR/onnx_gate_40000/acoustic.onnx"
  [ -f "$C1B_ONNX" ] || die "C1b: acoustic.onnx not produced at $C1B_ONNX"
  mapfile -t C1B_WAVS < <(list_wavs "$C1B_DIR/step_40000")
  require_two_wavs C1B_WAVS "c1b"
  C1B_ONNX_SHA="$(sha256_of "$C1B_ONNX")"
  C1B_SAKURA_SHA="$(sha256_of "${C1B_WAVS[0]}")"
  C1B_UMI_SHA="$(sha256_of "${C1B_WAVS[1]}")"
  echo "| probe: C1b onnx=$C1B_ONNX_SHA wavs=($C1B_SAKURA_SHA,$C1B_UMI_SHA)"
else
  echo "| probe: C1 onnx == session CPU onnx pin -> C1b skipped (not needed)"
fi

# ============================================================================
# Stage 6: Arm B（best-effort — 失敗しても probe 全体は失敗させない）。
# system python3（image 既定 torch 2.4.0+cu124）で export できるかを見る。
# ============================================================================
stage "6-arm-b"

ARM_B_SKIPPED="false"
ARM_B_REASON=""
ARM_B_ONNX_SHA=""
ARM_B_SAKURA_SHA=""
ARM_B_UMI_SHA=""
ARM_B_VERSIONS=""

# `arm_b_out="$(arm_b_run ...)"` はコマンド置換 = サブシェルで走る。サブシェル内での
# 通常のグローバル変数代入は `$(...)` 完了後に消える（bash のサブシェル分離）ため、
# ARM_B_* をここで直接代入しても親シェルには伝播しない。結果はファイル経由で
# 親シェルへ受け渡す（$RESULTS/.arm_b_vars に KEY=VALUE で書き出し、成功時のみ
# 呼び出し元が source する）。
ARM_B_VARS_FILE="$RESULTS/.arm_b_vars"

arm_b_run() {
  local sys_py="python3"
  "$sys_py" -m pip install --no-cache-dir --user onnx==1.22.0 onnxruntime==1.29.0 lightning==2.3.3
  # DiffSinger requirements.txt から torch を除いた依存を、torch を巻き込まない
  # よう --no-deps で入れる（image 同梱の torch 2.4.0+cu124 を動かさないため）。
  "$sys_py" -m pip install --no-cache-dir --user --no-deps \
    click einops h5py "librosa<0.10.0" matplotlib MonkeyType \
    praat-parselmouth==0.4.3 pyworld==0.3.4 PyYAML resampy \
    "scipy>=1.10.0" tensorboard tensorboardX torchmetrics tqdm
  # onnxsim は onnx/protobuf 依存のみで torch を引かないため通常 install。
  "$sys_py" -m pip install --no-cache-dir --user "onnxsim>=0.6.5"

  local outdir="$WORK/gate40k_run4_armb"
  gate_run_launch1 "$sys_py" "$DS" "$CKPT_DIR" "$CANON_DIR" "$WORK/vocoder_onnx" "$outdir" gpu
  local onnx="$outdir/onnx_gate_40000/acoustic.onnx"
  [ -f "$onnx" ] || { echo "arm_b: acoustic.onnx missing at $onnx" >&2; return 1; }
  local -a wavs
  mapfile -t wavs < <(find "$outdir/step_40000" -maxdepth 1 -type f -name '*.wav' | sort)
  [ "${#wavs[@]}" -eq 2 ] || { echo "arm_b: expected 2 wavs, found ${#wavs[@]}" >&2; return 1; }

  local onnx_sha sakura_sha umi_sha versions
  onnx_sha="$(sha256_of "$onnx")"
  sakura_sha="$(sha256_of "${wavs[0]}")"
  umi_sha="$(sha256_of "${wavs[1]}")"
  versions="$("$sys_py" -m pip freeze 2>/dev/null | grep -Ei '^(torch|numpy|onnx|onnxruntime|lightning|onnxsim)==' | tr '\n' ';')"
  {
    printf 'ARM_B_ONNX_SHA=%q\n' "$onnx_sha"
    printf 'ARM_B_SAKURA_SHA=%q\n' "$sakura_sha"
    printf 'ARM_B_UMI_SHA=%q\n' "$umi_sha"
    printf 'ARM_B_VERSIONS=%q\n' "$versions"
  } > "$ARM_B_VARS_FILE"
  echo "| probe: arm_b onnx=$onnx_sha wavs=($sakura_sha,$umi_sha) versions=$versions"
  return 0
}

# arm_b_out は pip install / DiffSinger export の全出力を含み得るため
# （観測上 KB〜MB 級になりうる）、いかなる場合も export はしない — env は
# execve のたびに全プロセスへコピーされ、E2BIG で以降のコマンド起動が
# 丸ごと落ちる（このスクリプト自体が execve E2BIG で死ぬと self-stop trap
# も動けなくなる）。全文は常にファイルへ書き、以後の使用・JSON 埋め込みは
# 末尾 4000 バイトへ truncate した ARM_B_REASON のみを通す。
if arm_b_out="$(arm_b_run 2>&1)"; then
  echo "$arm_b_out"
  printf '%s\n' "$arm_b_out" > "$RESULTS/arm_b_output.log"
  # shellcheck disable=SC1090
  source "$ARM_B_VARS_FILE"
  rm -f "$ARM_B_VARS_FILE"
else
  ARM_B_SKIPPED="true"
  printf '%s\n' "$arm_b_out" > "$RESULTS/arm_b_output.log"
  ARM_B_REASON="$(printf '%s' "$arm_b_out" | tail -c 4000)"
  echo "| probe: arm_b SKIPPED (best-effort, non-fatal). full output: $RESULTS/arm_b_output.log" >&2
  echo "| probe: arm_b reason (tail 4000 bytes): $ARM_B_REASON" >&2
fi

# ============================================================================
# Stage 7: probe_results.json 組み立て
# ============================================================================
stage "7-results"

export PROBE_PIN_COMMIT DIFFSINGER_COMMIT SESSION_CPU_ONNX_SHA \
  RECORDED_WAV_RITSU_SAKURA RECORDED_WAV_RITSU_UMI \
  RECORDED_WAV_PJS_SAKURA RECORDED_WAV_PJS_UMI \
  RECORDED_WAV_USER_SAKURA RECORDED_WAV_USER_UMI \
  G1_ONNX_SHA G1_RITSU_SAKURA_SHA G1_RITSU_UMI_SHA \
  G1_PJS_SAKURA_SHA G1_PJS_UMI_SHA G1_USER_SAKURA_SHA G1_USER_UMI_SHA \
  G2_ONNX_SHA G2_SAKURA_SHA G2_UMI_SHA \
  C1_ONNX_SHA C1_SAKURA_SHA C1_UMI_SHA \
  C1B_TRIGGERED C1B_ONNX_SHA C1B_SAKURA_SHA C1B_UMI_SHA \
  ARM_B_SKIPPED ARM_B_REASON ARM_B_ONNX_SHA ARM_B_SAKURA_SHA ARM_B_UMI_SHA ARM_B_VERSIONS

python3 - "$RESULTS/probe_results.json" "$RESULTS/g1" <<'PY'
import json, os, sys

out_path, g1_dir = sys.argv[1], sys.argv[2]
env = os.environ.get


def eq(a, b):
    return bool(a) and bool(b) and a == b


def summary_song(path, song):
    try:
        data = json.load(open(path, encoding="utf-8"))
        rec = data.get("results", {}).get(song, {})
        return {"wav_rms": rec.get("wav_rms"), "wav_duration_sec": rec.get("wav_duration_sec")}
    except Exception as e:
        return {"error": str(e)}


g1_ritsu_summary = os.path.join(g1_dir, "gate_synth_summary_ritsu.json")
g1_pjs_summary = os.path.join(g1_dir, "gate_synth_summary_pjs.json")
g1_user_summary = os.path.join(g1_dir, "gate_synth_summary_user.json")

expected_wavs = {
    "ritsu_sakura": env("RECORDED_WAV_RITSU_SAKURA"),
    "ritsu_umi": env("RECORDED_WAV_RITSU_UMI"),
    "pjs_sakura": env("RECORDED_WAV_PJS_SAKURA"),
    "pjs_umi": env("RECORDED_WAV_PJS_UMI"),
    "user_sakura": env("RECORDED_WAV_USER_SAKURA"),
    "user_umi": env("RECORDED_WAV_USER_UMI"),
}
g1_wavs = {
    "ritsu_sakura": {"sha256": env("G1_RITSU_SAKURA_SHA"), **summary_song(g1_ritsu_summary, "sakura")},
    "ritsu_umi": {"sha256": env("G1_RITSU_UMI_SHA"), **summary_song(g1_ritsu_summary, "umi")},
    "pjs_sakura": {"sha256": env("G1_PJS_SAKURA_SHA"), **summary_song(g1_pjs_summary, "sakura")},
    "pjs_umi": {"sha256": env("G1_PJS_UMI_SHA"), **summary_song(g1_pjs_summary, "umi")},
    "user_sakura": {"sha256": env("G1_USER_SAKURA_SHA"), **summary_song(g1_user_summary, "sakura")},
    "user_umi": {"sha256": env("G1_USER_UMI_SHA"), **summary_song(g1_user_summary, "umi")},
}
g1_wav_match_detail = {k: eq(g1_wavs[k]["sha256"], expected_wavs[k]) for k in expected_wavs}

arm_b_skipped = env("ARM_B_SKIPPED") == "true"

result = {
    "probe": "run4_export_device_probe",
    "generated_at_utc": env("DATE_UTC"),
    "pin_commit": env("PROBE_PIN_COMMIT"),
    "diffsinger_commit": env("DIFFSINGER_COMMIT"),
    "env_snapshot_ref": "env_snapshot.json",
    "venv_a_ref": "venv_a_env.json",
    "expected": {
        "session_cpu_onnx_sha256": env("SESSION_CPU_ONNX_SHA"),
        "recorded_run4_wav_sha256": expected_wavs,
    },
    "arms": {
        "g1": {"onnx_sha256": env("G1_ONNX_SHA"), "wavs": g1_wavs},
        "g2": {
            "onnx_sha256": env("G2_ONNX_SHA"),
            "wavs": {"sakura": env("G2_SAKURA_SHA"), "umi": env("G2_UMI_SHA")},
        },
        "c1": {
            "onnx_sha256": env("C1_ONNX_SHA"),
            "wavs": {"sakura": env("C1_SAKURA_SHA"), "umi": env("C1_UMI_SHA")},
        },
        "c1b": {
            "triggered": env("C1B_TRIGGERED") == "true",
            "onnx_sha256": env("C1B_ONNX_SHA") or None,
            "wavs": (
                {"sakura": env("C1B_SAKURA_SHA"), "umi": env("C1B_UMI_SHA")}
                if env("C1B_TRIGGERED") == "true" else None
            ),
        },
        "arm_b": (
            {"skipped": True, "reason": env("ARM_B_REASON")}
            if arm_b_skipped else
            {
                "skipped": False,
                "onnx_sha256": env("ARM_B_ONNX_SHA"),
                "wavs": {"sakura": env("ARM_B_SAKURA_SHA"), "umi": env("ARM_B_UMI_SHA")},
                "resolved_versions_raw": env("ARM_B_VERSIONS"),
            }
        ),
    },
    "comparisons": {
        "g1_onnx_eq_session_cpu": eq(env("G1_ONNX_SHA"), env("SESSION_CPU_ONNX_SHA")),
        "g2_onnx_eq_g1_onnx": eq(env("G2_ONNX_SHA"), env("G1_ONNX_SHA")),
        "c1_onnx_eq_session_cpu": eq(env("C1_ONNX_SHA"), env("SESSION_CPU_ONNX_SHA")),
        "c1_onnx_eq_g1_onnx": eq(env("C1_ONNX_SHA"), env("G1_ONNX_SHA")),
        "g1_wav_matches_recorded_detail": g1_wav_match_detail,
        "g1_wav_matches_recorded_count": sum(1 for v in g1_wav_match_detail.values() if v),
        "arm_b_onnx_eq_g1_onnx": (
            None if arm_b_skipped else eq(env("ARM_B_ONNX_SHA"), env("G1_ONNX_SHA"))
        ),
    },
}
json.dump(result, open(out_path, "w"), indent=2, ensure_ascii=False)
print(json.dumps(result, indent=2, ensure_ascii=False))
PY

echo "| probe: done. results at $RESULTS"
