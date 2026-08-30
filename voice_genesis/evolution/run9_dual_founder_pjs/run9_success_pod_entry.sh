#!/usr/bin/env bash
# RUN9 success-only admission pod entry.
#
# This script generates a fresh nine-file acoustic export and immediately feeds
# those bytes into the fixed 84-render output-only evaluator.  It creates a
# registration bundle only on PASS.  The generated export remains under the
# ephemeral container work directory and is never copied to the persistent
# public directory on rejection or implementation failure.
set -euo pipefail

readonly WORK="/root/run9work"
readonly PUBLIC="/workspace/run9_public"
readonly REPO="$WORK/ugh-prompt-engine"
readonly DS="$WORK/DiffSinger"
readonly RUN_DIR="$REPO/voice_genesis/evolution/run9_dual_founder_pjs"
readonly STATUS_FILE="$PUBLIC/status.json"
readonly STAGE_FILE="$PUBLIC/.current_stage"
readonly RESULT_DIR="$PUBLIC/successful_run9"
readonly WALL_CLOCK_SECONDS=21600

mkdir -p "$WORK" "$PUBLIC"
STAGE="init"
DETAIL=""
FIREWALL_ACTIVE="false"

stage() {
  STAGE="$1"
  printf '%s' "$STAGE" > "$STAGE_FILE"
  echo "| run9: === stage: $STAGE ==="
}

die() {
  DETAIL="$*"
  echo "| run9: FATAL stage=$STAGE detail=$DETAIL" >&2
  exit 1
}

restore_network() {
  if [ "$FIREWALL_ACTIVE" = "true" ]; then
    iptables -P OUTPUT ACCEPT 2>/dev/null || true
    iptables -F OUTPUT 2>/dev/null || true
    FIREWALL_ACTIVE="false"
  fi
}

force_restore_network_for_stop() {
  # The watchdog subshell was forked before FIREWALL_ACTIVE changes, so it must
  # not rely on that shell-local flag when recovering connectivity for runpodctl.
  iptables -P OUTPUT ACCEPT 2>/dev/null || true
  iptables -F OUTPUT 2>/dev/null || true
  FIREWALL_ACTIVE="false"
}

self_stop() {
  if [ -z "${RUNPOD_POD_ID:-}" ]; then
    echo "| run9: RUNPOD_POD_ID is absent; self-stop skipped"
    return 0
  fi
  local attempt
  for attempt in 1 2 3 4 5; do
    if runpodctl stop pod "$RUNPOD_POD_ID"; then
      return 0
    fi
    echo "| run9: self-stop attempt=$attempt failed" >&2
    [ "$attempt" -lt 5 ] && sleep 30
  done
  echo "| run9: SELF-STOP FAILED; pod may still be billing: $RUNPOD_POD_ID" >&2
  return 1
}

on_exit() {
  local ec=$?
  set +e
  [ -n "${WATCHDOG_PID:-}" ] && kill "$WATCHDOG_PID" 2>/dev/null || true
  [ -n "${HEARTBEAT_PID:-}" ] && kill "$HEARTBEAT_PID" 2>/dev/null || true
  restore_network
  local state="failed"
  [ "$ec" -eq 0 ] && state="success"
  python3 - "$STATUS_FILE" "$state" "$STAGE" "$DETAIL" <<'PY'
import json, sys
path, status, stage, detail = sys.argv[1:]
with open(path, "w", encoding="utf-8") as stream:
    json.dump(
        {
            "schema": "run9-pod-operational-status/1.0",
            "status": status,
            "stage": stage,
            "detail": detail,
            "registration_created": status == "success",
        },
        stream,
        ensure_ascii=False,
        sort_keys=True,
    )
    stream.write("\n")
PY
  if [ -f /workspace/run9_console.log ]; then
    cp -f /workspace/run9_console.log "$PUBLIC/run9_console.log" 2>/dev/null || true
  fi
  local hold=900
  [ "$state" = "success" ] && hold=2700
  echo "| run9: holding result endpoint for ${hold}s"
  sleep "$hold"
  [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null || true
  self_stop || true
  exit "$ec"
}
trap on_exit EXIT

(
  sleep "$WALL_CLOCK_SECONDS"
  echo "| run9: watchdog reached ${WALL_CLOCK_SECONDS}s; forcing stop" >&2
  force_restore_network_for_stop
  self_stop || true
) &
WATCHDOG_PID=$!

(
  while true; do
    python3 - "$PUBLIC/heartbeat.json" "$STAGE_FILE" <<'PY'
import datetime, json, pathlib, sys
out, stage_path = map(pathlib.Path, sys.argv[1:])
stage = stage_path.read_text(encoding="utf-8") if stage_path.exists() else "init"
out.write_text(
    json.dumps(
        {
            "schema": "run9-pod-heartbeat/1.0",
            "stage": stage,
            "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
        sort_keys=True,
    ) + "\n",
    encoding="utf-8",
)
PY
    sleep 30
  done
) &
HEARTBEAT_PID=$!

python3 -m http.server 8000 --directory "$PUBLIC" >/workspace/run9_http.log 2>&1 &
SERVER_PID=$!

: "${RUN9_PIN_COMMIT:?RUN9_PIN_COMMIT must be injected as a 40-hex commit}"
[[ "$RUN9_PIN_COMMIT" =~ ^[0-9a-f]{40}$ ]] || die "RUN9_PIN_COMMIT is not 40 lowercase hex"
command -v runpodctl >/dev/null 2>&1 || die "runpodctl is unavailable"

if find "$PUBLIC" -mindepth 1 -maxdepth 1 \
  ! -name heartbeat.json ! -name .current_stage -print -quit | grep -q .; then
  die "persistent public directory is not fresh"
fi

readonly PYTHON_VERSION="3.11.15"
readonly PYTHON_TGZ_SHA="f4de1b10bd6c70cbb9fa1cd71fc5038b832747a74ee59d599c69ce4846defb50"
readonly PYTHON_TGZ_URL="https://www.python.org/ftp/python/3.11.15/Python-3.11.15.tgz"
readonly DIFFSINGER_COMMIT="e2307b1080b00f3999702ce9017cfd75c7f862fe"
readonly PJS_ID="1hPHwOkSe2Vnq6hXrhVtzNskJjVMQmvN_"
readonly PJS_SHA="683c00253ee35a62d50de0375bb9d8e003a74338d4ce3495ac3f7ad096abc1ca"
readonly CKPT_ID="1Tm0dxUl_mv6A8-SNO1C72zsdAO8oNHzo"
readonly CKPT_SHA="6a28d744642df6535000857767c32efee2e69668b390c2e7fa6486908723306a"
readonly CONFIG_ID="1xeo_m5X3LrcDdPlpsc6sL8kAxjUN_IwQ"
readonly CONFIG_SHA="3722072045060e316ec9fee3f307412eceacf617d3b3ece7adfcbefa0f9df9d9"
readonly SPK_MAP_ID="1FaS83o-QJmjwmPRYzKUyp9FxX0_dYS7K"
readonly SPK_MAP_SHA="da9748fabfa721a4a789224b50fd52743628fd2396602852f2dc25c54f2e3803"
readonly LANG_MAP_ID="1oGfu5qS-Ll0EsgzMCZZWqXCLBamz5wWH"
readonly LANG_MAP_SHA="2a6a227ee65a49f5c30e848a4b62c5cc1817926bbdab373228e6302d2c794953"
readonly DICT_ID="1zpxVqbN8SiLqp9qA0WcWfrg0s0C55RhP"
readonly DICT_SHA="b8ea0d99fcf60e82319cc84b162d9e1b4d5ce1146cfa1c6291e025fbb8be14ef"
readonly CANON_URL="https://www.canon-voice.com/voice/NamineRitsu_DiffSinger.zip"
readonly CANON_SHA="5c7b8c328180ea2971f71d89b3a675b2adfc91772664ae28cbb5915385f42530"
readonly VOCODER_URL="https://github.com/xunmengshe/OpenUtau/releases/download/0.0.0.0/nsf_hifigan.oudep"
readonly VOCODER_SHA="e22f84009804da2e5916e7a2000f4c30278148796376e49368ec5ff8f9f58830"

sha256_of() { sha256sum "$1" | cut -d' ' -f1; }

fetch_url() {
  local url="$1" dest="$2" want="$3" label="$4" attempt got
  mkdir -p "$(dirname "$dest")"
  for attempt in 1 2 3; do
    rm -f "${dest}.part"
    if curl -fsSL --retry 5 --retry-all-errors --max-time 1800 \
      -o "${dest}.part" "$url"; then
      got="$(sha256_of "${dest}.part")"
      if [ "$got" = "$want" ]; then
        mv -f "${dest}.part" "$dest"
        echo "| run9: fetched $label ${got:0:16}..."
        return 0
      fi
      echo "| run9: $label hash mismatch attempt=$attempt" >&2
    fi
    rm -f "${dest}.part"
    sleep 5
  done
  die "failed to fetch verified $label"
}

fetch_drive() {
  local id="$1" dest="$2" want="$3" label="$4"
  fetch_url \
    "https://drive.usercontent.google.com/download?id=${id}&export=download&confirm=t" \
    "$dest" "$want" "$label"
}

require_sha() {
  local path="$1" want="$2" label="$3" got
  got="$(sha256_of "$path" 2>/dev/null || true)"
  [ "$got" = "$want" ] || die "$label sha256 mismatch: got=${got:-MISSING} want=$want"
}

extract_zip() {
  local archive="$1" destination="$2"
  mkdir -p "$destination"
  python3 - "$archive" "$destination" <<'PY'
import pathlib, sys, zipfile
archive, destination = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]).resolve()
with zipfile.ZipFile(archive) as source:
    for member in source.infolist():
        target = (destination / member.filename).resolve()
        if not target.is_relative_to(destination):
            raise SystemExit(f"zip path escapes destination: {member.filename}")
    source.extractall(destination)
PY
}

stage "preflight-system"
readonly NATIVE_INSTALL_LOCK="$RUN_DIR/inputs/measurement_native_install_lock.txt"
readonly NATIVE_MANIFEST="$RUN_DIR/inputs/measurement_native_manifest.txt"
[ -s "$NATIVE_INSTALL_LOCK" ] || die "committed native install lock is missing"
[ -s "$NATIVE_MANIFEST" ] || die "committed native manifest is missing"
mapfile -t NATIVE_PACKAGES < "$NATIVE_INSTALL_LOCK"
[ "${#NATIVE_PACKAGES[@]}" -gt 0 ] || die "native install lock is empty"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
  --allow-downgrades "${NATIVE_PACKAGES[@]}"
dpkg-query -W -f='${binary:Package}=${Version}\n' | LC_ALL=C sort \
  > "$WORK/measurement_native_environment.actual"
if ! cmp -s "$NATIVE_MANIFEST" "$WORK/measurement_native_environment.actual"; then
  diff -u "$NATIVE_MANIFEST" "$WORK/measurement_native_environment.actual" >&2 || true
  die "native measurement environment does not match committed manifest"
fi

stage "python-3.11.15"
fetch_url "$PYTHON_TGZ_URL" "$WORK/Python-${PYTHON_VERSION}.tgz" \
  "$PYTHON_TGZ_SHA" "CPython source"
tar -xzf "$WORK/Python-${PYTHON_VERSION}.tgz" -C "$WORK"
(
  cd "$WORK/Python-${PYTHON_VERSION}"
  ./configure --prefix="/opt/python-${PYTHON_VERSION}" --with-ensurepip=install >/dev/null
  make -j"$(nproc)" >/dev/null
  make altinstall >/dev/null
)
readonly PY="/opt/python-${PYTHON_VERSION}/bin/python3.11"
[ "$($PY -c 'import platform; print(platform.python_version())')" = "$PYTHON_VERSION" ] \
  || die "compiled Python version mismatch"

stage "source-checkout"
git clone -q https://github.com/Yuu6798/ugh-prompt-engine.git "$REPO"
git -C "$REPO" checkout -q "$RUN9_PIN_COMMIT"
[ "$(git -C "$REPO" rev-parse HEAD)" = "$RUN9_PIN_COMMIT" ] \
  || die "repository checkout does not match RUN9_PIN_COMMIT"
[ -z "$(git -C "$REPO" status --porcelain --untracked-files=no)" ] \
  || die "repository checkout is dirty"
git clone -q https://github.com/openvpi/DiffSinger.git "$DS"
git -C "$DS" checkout -q "$DIFFSINGER_COMMIT"
[ "$(git -C "$DS" rev-parse HEAD)" = "$DIFFSINGER_COMMIT" ] \
  || die "DiffSinger checkout mismatch"
[ -z "$(git -C "$DS" status --porcelain)" ] || die "DiffSinger checkout is dirty"

stage "external-assets"
readonly ASSETS="$WORK/assets"
fetch_drive "$CKPT_ID" "$ASSETS/run6/model_ckpt_steps_40000.ckpt" "$CKPT_SHA" "run6 checkpoint"
fetch_drive "$CONFIG_ID" "$ASSETS/run6/config.yaml" "$CONFIG_SHA" "run6 config"
fetch_drive "$SPK_MAP_ID" "$ASSETS/run6/spk_map.json" "$SPK_MAP_SHA" "run6 speaker map"
fetch_drive "$LANG_MAP_ID" "$ASSETS/run6/lang_map.json" "$LANG_MAP_SHA" "run6 language map"
fetch_drive "$DICT_ID" "$ASSETS/run6/dictionary-ja.txt" "$DICT_SHA" "run6 dictionary"
fetch_drive "$PJS_ID" "$ASSETS/PJS_corpus_ver1.1.zip" "$PJS_SHA" "PJS corpus"
fetch_url "$CANON_URL" "$ASSETS/NamineRitsu_DiffSinger.zip" "$CANON_SHA" "canon model"
fetch_url "$VOCODER_URL" "$ASSETS/nsf_hifigan.oudep" "$VOCODER_SHA" "vocoder"
extract_zip "$ASSETS/NamineRitsu_DiffSinger.zip" "$ASSETS/canon"
extract_zip "$ASSETS/nsf_hifigan.oudep" "$ASSETS/vocoder"
extract_zip "$ASSETS/PJS_corpus_ver1.1.zip" "$ASSETS/pjs"
readonly CANON_DIR="$ASSETS/canon/NamineRitsu_DiffSinger"
readonly VOCODER_DIR="$ASSETS/vocoder"
readonly PJS_ROOT="$ASSETS/pjs/PJS_corpus_ver1.1"
require_sha "$CANON_DIR/linguistic.onnx" \
  "1c9ec9f67277a2ba4b9c3f815150251ed7d87ad54eed3e22f8d85dbda74705b6" \
  "canon linguistic"
require_sha "$CANON_DIR/dsdur/dur.onnx" \
  "11bbfad5c489a57e05bd6ed7e239b3fce913a6b644d9281ae152126563a3d288" \
  "canon duration"
require_sha "$CANON_DIR/dspitch/pitch.onnx" \
  "e361ad13053c4b49331a44296148bb33396092f57ca477ceed60e59cdbdfb3b9" \
  "canon pitch"
require_sha "$CANON_DIR/phonemes.txt" \
  "1489af3c4806ad2cfc10e663ec27a1bf7c6bf0d6f9a047263948c5cbe36eebfb" \
  "canon phonemes"
require_sha "$VOCODER_DIR/nsf_hifigan.onnx" \
  "a3e26672a8c655e3faf65f31cb4339a7fbca7758ba86be9af89e03dced7c3fa4" \
  "vocoder ONNX"

stage "export-environment"
readonly VENV_EXPORT="$WORK/venv_export"
"$PY" -m venv --clear "$VENV_EXPORT"
"$VENV_EXPORT/bin/python" - "$RUN_DIR/inputs/reexport_manifest.json" \
  "$WORK/requirements_export.lock" <<'PY'
import json, pathlib, sys
manifest = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
pathlib.Path(sys.argv[2]).write_text(
    "\n".join(manifest["export_environment_lock"]) + "\n",
    encoding="utf-8",
)
PY
"$VENV_EXPORT/bin/pip" install --no-deps -r "$WORK/requirements_export.lock" \
  --extra-index-url https://download.pytorch.org/whl/cpu
"$VENV_EXPORT/bin/python" - "$RUN_DIR/inputs/reexport_manifest.json" <<'PY'
import json, pathlib, subprocess, sys
manifest = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
actual = subprocess.run(
    [sys.executable, "-m", "pip", "freeze", "--all"],
    check=True,
    capture_output=True,
    text=True,
).stdout.strip().splitlines()
expected = manifest["export_environment_lock"]
if sorted(actual) != sorted(expected):
    raise SystemExit(
        f"export lock mismatch extra={sorted(set(actual)-set(expected))} "
        f"missing={sorted(set(expected)-set(actual))}"
    )
PY

stage "fresh-export"
readonly EXP_DIR="$DS/checkpoints/s5_run6_acoustic_v1"
mkdir -p "$EXP_DIR"
cp "$ASSETS/run6/model_ckpt_steps_40000.ckpt" "$EXP_DIR/"
cp "$ASSETS/run6/config.yaml" "$EXP_DIR/"
cp "$ASSETS/run6/spk_map.json" "$EXP_DIR/"
cp "$ASSETS/run6/lang_map.json" "$EXP_DIR/"
cp "$ASSETS/run6/dictionary-ja.txt" "$EXP_DIR/"
require_sha "$EXP_DIR/model_ckpt_steps_40000.ckpt" "$CKPT_SHA" "staged checkpoint"
require_sha "$EXP_DIR/config.yaml" "$CONFIG_SHA" "staged config"
require_sha "$EXP_DIR/spk_map.json" "$SPK_MAP_SHA" "staged speaker map"
require_sha "$EXP_DIR/lang_map.json" "$LANG_MAP_SHA" "staged language map"
require_sha "$EXP_DIR/dictionary-ja.txt" "$DICT_SHA" "staged dictionary"
readonly GENERATED="$WORK/generated_export"
[ ! -e "$GENERATED" ] || die "fresh export directory already exists"
(
  cd "$DS"
  "$VENV_EXPORT/bin/python" scripts/export.py acoustic \
    --exp s5_run6_acoustic_v1 --ckpt 40000 --out "$GENERATED"
)

stage "measurement-environment"
readonly VENV_RENDER="$WORK/venv_render"
readonly MEASUREMENT_LOCK="$RUN_DIR/inputs/measurement_environment_lock.txt"
[ -s "$MEASUREMENT_LOCK" ] || die "committed measurement environment lock is missing"
"$PY" -m venv --clear "$VENV_RENDER"
PIP_PIN="$(grep -Ei '^pip==' "$MEASUREMENT_LOCK")"
[ -n "$PIP_PIN" ] || die "measurement lock does not pin pip"
"$VENV_RENDER/bin/python" -m pip install --no-cache-dir --upgrade "$PIP_PIN"
for build_package in setuptools wheel Cython numpy; do
  build_pin="$(grep -Ei "^${build_package}==" "$MEASUREMENT_LOCK")"
  [ -n "$build_pin" ] || die "measurement lock does not pin $build_package"
  "$VENV_RENDER/bin/python" -m pip install --no-cache-dir --no-deps "$build_pin"
done
grep -Evi '^pip==' "$MEASUREMENT_LOCK" > "$WORK/measurement_environment.install.lock"
"$VENV_RENDER/bin/python" -m pip install --no-cache-dir --no-deps --no-build-isolation \
  -r "$WORK/measurement_environment.install.lock"
"$VENV_RENDER/bin/python" - "$MEASUREMENT_LOCK" <<'PY'
import pathlib, platform, subprocess, sys
lock_path = pathlib.Path(sys.argv[1])
expected = sorted(
    line.strip()
    for line in lock_path.read_text(encoding="utf-8").splitlines()
    if line.strip()
)
actual = sorted(
    line.strip()
    for line in subprocess.run(
        [sys.executable, "-m", "pip", "freeze", "--all"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    if line.strip()
)
if actual != expected:
    raise SystemExit(
        f"measurement closure mismatch extra={sorted(set(actual)-set(expected))} "
        f"missing={sorted(set(expected)-set(actual))}"
    )
if platform.python_version() != "3.11.15":
    raise SystemExit(f"compiled Python version drift: {platform.python_version()}")
PY
"$VENV_RENDER/bin/python" -m pip freeze --all > "$WORK/measurement_environment.freeze"

stage "render-network-isolation"
iptables -F OUTPUT
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
iptables -A OUTPUT -j REJECT
iptables -P OUTPUT DROP
FIREWALL_ACTIVE="true"

stage "success-only-admission"
export ORT_TELEMETRY_DISABLED=1
set +e
"$VENV_RENDER/bin/python" "$RUN_DIR/run9_success_admission.py" \
  --acoustic-dir "$GENERATED" \
  --canon-model-dir "$CANON_DIR" \
  --vocoder-dir "$VOCODER_DIR" \
  --pjs-corpus-root "$PJS_ROOT" \
  --source-commit "$RUN9_PIN_COMMIT" \
  --out "$RESULT_DIR"
ADMISSION_EC=$?
set -e
if [ "$ADMISSION_EC" -eq 0 ]; then
  [ -f "$RESULT_DIR/SUCCESS.json" ] || die "PASS returned without SUCCESS.json"
  stage "success-published"
  exit 0
fi
if [ "$ADMISSION_EC" -eq 3 ]; then
  [ ! -e "$RESULT_DIR" ] || die "rejected artifact created a registration directory"
  DETAIL="generated artifact rejected; registry unchanged"
  stage "artifact-rejected-no-registration"
  exit 3
fi
[ ! -e "$RESULT_DIR" ] || die "aborted execution created a registration directory"
DETAIL="success-only admission aborted before registration"
stage "admission-aborted-no-registration"
exit "$ADMISSION_EC"
