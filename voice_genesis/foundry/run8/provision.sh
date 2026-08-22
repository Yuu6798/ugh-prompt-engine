#!/bin/bash
# provision.sh — run 8 の外部資産を **pin 照合つきで** 再配置する（冪等・fail-closed）。
#
# なぜ要るか（2026-08-22 の実測）: このセッションのコンテナが再構築され、作業
# ディレクトリ `/home/user/s7work`（7.5 GB）が丸ごと消えた。git に入っている
# 成果物と pin は無傷だったが、**取得元の Drive file ID の一部は会話文脈にしか
# 無く**、リポジトリからは復元できなかった。失って痛いのはバイト列ではなく
# 「どこから取って、何と照合すれば同一と言えるか」の情報である。本スクリプトは
# それを実行可能な形で repo 側へ固定する。
#
# 使い方:
#     bash voice_genesis/foundry/run8/provision.sh [--root /home/user/s7work]
#
# 性質:
#   - **冪等**: 既に在って sha が一致するファイルは再取得しない（照合だけする）
#   - **fail-closed**: sha が食い違ったら**その場で停止**する。差し替えない・続行しない
#   - **推測しない**: 取得元と期待 sha は下表に固定。表に無い資産は取りに行かない
set -uo pipefail

ROOT="/home/user/s7work"
[ "${1:-}" = "--root" ] && ROOT="${2:?}"
M="$ROOT/materials"
FAIL=0
OK=0
SKIP=0

# name | dest(相対 $M) | source | sha256
#   source: `url:<URL>` か `drive:<FILE_ID>`
ASSETS=$(cat <<'TABLE'
canon_zip|NamineRitsu_DiffSinger.zip|url:https://www.canon-voice.com/voice/NamineRitsu_DiffSinger.zip|5c7b8c328180ea2971f71d89b3a675b2adfc91772664ae28cbb5915385f42530
vocoder_oudep|nsf_hifigan.oudep|url:https://github.com/xunmengshe/OpenUtau/releases/download/0.0.0.0/nsf_hifigan.oudep|e22f84009804da2e5916e7a2000f4c30278148796376e49368ec5ff8f9f58830
run5_ckpt|ckpts/run5_40k.ckpt|drive:1H_pWLMI4khQgQNQ_85PdLtSFJs_LVm5F|d3c51399cb1c3914981d4a11da8391a4e344130c84b263f0ef9774f60c3f8da5
run6_ckpt|ckpts/run6_40k.ckpt|drive:1Tm0dxUl_mv6A8-SNO1C72zsdAO8oNHzo|6a28d744642df6535000857767c32efee2e69668b390c2e7fa6486908723306a
run7_ckpt|ckpts/run7_40k.ckpt|drive:1LY_Qckwo4zTZmaTxq8EmESEoOKjQlVFW|518df090a8154e61f28b529f731418f4f97d47c3b56d1326d354e6be4629fa93
run5_config|ckpts/run5_bundle/config.yaml|drive:1iAkygWQlOeRAuemOr0WonPWAKxD1wukP|0b627cc9113ce38f46f5c0b9a4c19c58dbb8b928318226a93e12a04ad624b833
run5_spk_map|ckpts/run5_bundle/spk_map.json|drive:1ttL95_hE4WTPAKpuyAT-Borr3Gii2siE|da9748fabfa721a4a789224b50fd52743628fd2396602852f2dc25c54f2e3803
run5_lang_map|ckpts/run5_bundle/lang_map.json|drive:1U8rgbAPVFN-v5ZwL7GwqzA4wyeMI6LSt|2a6a227ee65a49f5c30e848a4b62c5cc1817926bbdab373228e6302d2c794953
run5_dict|ckpts/run5_bundle/dictionary-ja.txt|drive:1gTYt20KvIGXlsbPW-4Atp1doDGWMYAqr|b8ea0d99fcf60e82319cc84b162d9e1b4d5ce1146cfa1c6291e025fbb8be14ef
run6_config|ckpts/run6_bundle/config.yaml|drive:1xeo_m5X3LrcDdPlpsc6sL8kAxjUN_IwQ|3722072045060e316ec9fee3f307412eceacf617d3b3ece7adfcbefa0f9df9d9
run6_spk_map|ckpts/run6_bundle/spk_map.json|drive:1FaS83o-QJmjwmPRYzKUyp9FxX0_dYS7K|da9748fabfa721a4a789224b50fd52743628fd2396602852f2dc25c54f2e3803
run6_lang_map|ckpts/run6_bundle/lang_map.json|drive:1oGfu5qS-Ll0EsgzMCZZWqXCLBamz5wWH|2a6a227ee65a49f5c30e848a4b62c5cc1817926bbdab373228e6302d2c794953
run6_dict|ckpts/run6_bundle/dictionary-ja.txt|drive:1zpxVqbN8SiLqp9qA0WcWfrg0s0C55RhP|b8ea0d99fcf60e82319cc84b162d9e1b4d5ce1146cfa1c6291e025fbb8be14ef
run7_config|run7_ckpt/config.yaml|drive:1g2ax7XWwqZU5LNVSqAakZEU1ASTFGrMf|e14ac2fde724998db05070550e86391c9090e582b1539747faa58356ae18d411
run7_spk_map|run7_ckpt/spk_map.json|drive:108EYs-_nHUEit_-RqYNrZfSSA-edMwk2|e89302087ee35d8ac9b4cdf8700f9411d31da45a51a8e9cc1563bab1c654d838
run7_lang_map|run7_ckpt/lang_map.json|drive:1DxT5JoSbh2OJFgg5dVrc32u3iOJjQSMs|2a6a227ee65a49f5c30e848a4b62c5cc1817926bbdab373228e6302d2c794953
run7_dict|run7_ckpt/dictionary-ja.txt|drive:1pWKfzgtHGOfDM34GCZSRCRcAsgRM8m5l|633ee9667b4f1079aff4cb1ac66cdce407d02226beb372474bf88ef0c7fedbe4
TABLE
)

sha_of () { sha256sum "$1" 2>/dev/null | cut -d' ' -f1; }

fetch () {
  local name="$1" dest="$M/$2" src="$3" want="$4"
  mkdir -p "$(dirname "$dest")"
  if [ -f "$dest" ] && [ "$(sha_of "$dest")" = "$want" ]; then
    printf '  %-14s SKIP (already pinned)\n' "$name"; SKIP=$((SKIP+1)); return 0
  fi
  case "$src" in
    url:*)   curl -sS -L -o "$dest" "${src#url:}" ;;
    drive:*) curl -sS -L -o "$dest" \
               "https://drive.usercontent.google.com/download?id=${src#drive:}&export=download&confirm=t" ;;
    *) echo "  $name: unknown source $src" >&2; FAIL=$((FAIL+1)); return 1 ;;
  esac
  local got; got="$(sha_of "$dest")"
  if [ "$got" != "$want" ]; then
    printf '  %-14s FAIL sha %s != %s\n' "$name" "${got:0:16}" "${want:0:16}" >&2
    FAIL=$((FAIL+1)); return 1
  fi
  printf '  %-14s OK   %s\n' "$name" "${got:0:16}"; OK=$((OK+1))
}

echo "| provision root: $ROOT"
echo "| 1. pin 照合つき取得"
while IFS='|' read -r name dest src want; do
  [ -z "${name:-}" ] && continue
  fetch "$name" "$dest" "$src" "$want"
done <<< "$ASSETS"

echo "| 2. 展開（展開後の実体も照合する）"
mkdir -p "$M/extracted/ds" "$M/vocoder_onnx"
if [ ! -f "$M/extracted/ds/NamineRitsu_DiffSinger/acoustic.onnx" ]; then
  ( cd "$M/extracted/ds" && unzip -o -q "$M/NamineRitsu_DiffSinger.zip" )
fi
if [ ! -f "$M/vocoder_onnx/nsf_hifigan.onnx" ]; then
  ( cd "$M/vocoder_onnx" && unzip -o -q "$M/nsf_hifigan.oudep" )
fi
VOC_WANT=a3e26672a8c655e3faf65f31cb4339a7fbca7758ba86be9af89e03dced7c3fa4
VOC_GOT="$(sha_of "$M/vocoder_onnx/nsf_hifigan.onnx")"
if [ "$VOC_GOT" != "$VOC_WANT" ]; then
  echo "  nsf_hifigan.onnx FAIL sha ${VOC_GOT:0:16} != ${VOC_WANT:0:16}" >&2; FAIL=$((FAIL+1))
else
  echo "  nsf_hifigan.onnx OK   ${VOC_GOT:0:16}"
fi

echo "| 3. DiffSinger (openvpi e2307b1)"
if [ ! -d "$M/DiffSinger/.git" ]; then
  git clone -q https://github.com/openvpi/DiffSinger.git "$M/DiffSinger"
fi
( cd "$M/DiffSinger" && git checkout -q e2307b1 && echo "  DiffSinger     OK   $(git rev-parse --short HEAD)" )

echo "| 4. ANALYSIS_STACK_PIN（測定側インタプリタ）"
python - <<'PY'
import importlib.metadata as md, subprocess, sys
PIN = {"numba": "0.66.0", "librosa": "0.11.0", "numpy": "2.4.6", "pyloudnorm": "0.2.0"}
bad = {p: md.version(p) for p in PIN if md.version(p) != PIN[p]}
if not bad:
    print("  analysis stack OK   " + " / ".join(f"{k} {v}" for k, v in PIN.items()))
else:
    print(f"  analysis stack MISMATCH {bad} -> 復元する（pin は差し替えない）")
    for p in bad:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", f"{p}=={PIN[p]}"], check=True)
    still = {p: md.version(p) for p in PIN if md.version(p) != PIN[p]}
    print("  analysis stack " + ("RESTORED" if not still else f"STILL BAD {still}"))
PY

echo "| result: OK=$OK SKIP=$SKIP FAIL=$FAIL"
[ "$FAIL" -eq 0 ] || { echo "| fail-closed: pin 不一致があるので止める" >&2; exit 1; }
echo "| 次: export（venv_export）→ 校正レンダ再生成 → samples_sha256 照合"
