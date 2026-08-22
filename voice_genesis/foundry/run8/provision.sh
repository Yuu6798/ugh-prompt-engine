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
#   - **fail-closed**: 失敗は 1 件残らず `FAIL` へ計上し、**最後に exit 1 で止める**。
#     途中の工程を静かに素通りさせない（PR #303 レビュー指摘: errexit が無いうえに
#     展開・clone/checkout・スタック復元の失敗が `FAIL` に計上されず、
#     **pin されていない checkout のまま最終行の成功文言へ到達できた**）。
#     即時 abort ではなく計上して続けるのは意図的で、1 回の実行で壊れている資産を
#     全部並べるため。ただし後段が前段の成功を前提にする箇所（export venv は
#     pin された DiffSinger を前提にする）は、その前段が落ちたら実行しない。
#   - **staging 経由**: 取得は `.part` へ落とし、**sha が一致したものだけ**正規の
#     名前へ移す（失敗した取得が、既に在る照合済みファイルを壊さない）
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
  # staging へ落とす。失敗した取得が、既に在る照合済みファイルを壊さないため。
  local part="$dest.part" rc=0
  rm -f "$part"
  case "$src" in
    url:*)   curl -fsS -L -o "$part" "${src#url:}" || rc=$? ;;
    drive:*) curl -fsS -L -o "$part" \
               "https://drive.usercontent.google.com/download?id=${src#drive:}&export=download&confirm=t" || rc=$? ;;
    *) echo "  $name: unknown source $src" >&2; FAIL=$((FAIL+1)); return 1 ;;
  esac
  if [ "$rc" -ne 0 ] || [ ! -s "$part" ]; then
    printf '  %-14s FAIL 取得できなかった (curl rc=%s)\n' "$name" "$rc" >&2
    rm -f "$part"; FAIL=$((FAIL+1)); return 1
  fi
  local got; got="$(sha_of "$part")"
  if [ "$got" != "$want" ]; then
    printf '  %-14s FAIL sha %s != %s\n' "$name" "${got:0:16}" "${want:0:16}" >&2
    rm -f "$part"; FAIL=$((FAIL+1)); return 1
  fi
  mv -f "$part" "$dest"
  printf '  %-14s OK   %s\n' "$name" "${got:0:16}"; OK=$((OK+1))
}

echo "| provision root: $ROOT"
echo "| 1. pin 照合つき取得"
while IFS='|' read -r name dest src want; do
  [ -z "${name:-}" ] && continue
  fetch "$name" "$dest" "$src" "$want"
done <<< "$ASSETS"

echo "| 2. 展開（**全メンバを検証済み zip と照合**して、違う物だけ入れ直す）"
# 「acoustic.onnx が在るから展開済み」という**存在検査だけの再利用**は使わない
# （PR #303 第 3 巡 P1）。古い / 壊れた展開が残っていると、容器 zip の sha は
# 合っているのに **中身が別物の linguistic / dur / pitch モデル**で測ってしまう。
# zip 自体は step 1 で sha 照合済みなので、そのメンバのバイト列を正本として使える。
mkdir -p "$M/extracted/ds" "$M/vocoder_onnx"
extract_verified () {
  local archive="$1" dest="$2"
  python - "$archive" "$dest" <<'PY'
import hashlib, os, sys, tempfile, zipfile

archive, dest = sys.argv[1], sys.argv[2]
verified = repaired = 0
with zipfile.ZipFile(archive) as z:
    for info in z.infolist():
        if info.is_dir():
            os.makedirs(os.path.join(dest, info.filename), exist_ok=True)
            continue
        want = hashlib.sha256(z.read(info)).hexdigest()
        out = os.path.join(dest, info.filename)
        got = None
        if os.path.isfile(out) and os.path.getsize(out) == info.file_size:
            h = hashlib.sha256()
            with open(out, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            got = h.hexdigest()
        if got == want:
            verified += 1
            continue
        # 違うメンバだけを差し替える（一時ファイル -> rename で途中状態を残さない）
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(out) or ".")
        with os.fdopen(fd, "wb") as fh:
            fh.write(z.read(info))
        os.replace(tmp, out)
        repaired += 1
print(f"verified={verified} repaired={repaired}")
PY
}
if EXTRACT_OUT="$(extract_verified "$M/NamineRitsu_DiffSinger.zip" "$M/extracted/ds" 2>&1)"; then
  echo "  canon zip        OK   $EXTRACT_OUT"
else
  echo "  canon zip        FAIL 展開/照合に失敗: $EXTRACT_OUT" >&2; FAIL=$((FAIL+1))
fi
if EXTRACT_OUT="$(extract_verified "$M/nsf_hifigan.oudep" "$M/vocoder_onnx" 2>&1)"; then
  echo "  vocoder oudep    OK   $EXTRACT_OUT"
else
  echo "  vocoder oudep    FAIL 展開/照合に失敗: $EXTRACT_OUT" >&2; FAIL=$((FAIL+1))
fi
VOC_WANT=a3e26672a8c655e3faf65f31cb4339a7fbca7758ba86be9af89e03dced7c3fa4
VOC_GOT="$(sha_of "$M/vocoder_onnx/nsf_hifigan.onnx")"
if [ "$VOC_GOT" != "$VOC_WANT" ]; then
  echo "  nsf_hifigan.onnx FAIL sha ${VOC_GOT:0:16} != ${VOC_WANT:0:16}" >&2; FAIL=$((FAIL+1))
else
  echo "  nsf_hifigan.onnx OK   ${VOC_GOT:0:16}"
fi
DS_ACOUSTIC="$M/extracted/ds/NamineRitsu_DiffSinger/acoustic.onnx"
if [ ! -f "$DS_ACOUSTIC" ]; then
  echo "  canon zip        FAIL 展開後に acoustic.onnx が無い" >&2; FAIL=$((FAIL+1))
fi

echo "| 3. DiffSinger (openvpi e2307b1)"
DS_OK=0
if [ ! -d "$M/DiffSinger/.git" ]; then
  git clone -q https://github.com/openvpi/DiffSinger.git "$M/DiffSinger" \
    || { echo "  DiffSinger     FAIL clone できなかった" >&2; FAIL=$((FAIL+1)); }
fi
# checkout が落ちたまま先へ進むと、**pin されていない版から export** してしまう。
if ( cd "$M/DiffSinger" && git checkout -q e2307b1 ) 2>/dev/null; then
  DS_REV="$( cd "$M/DiffSinger" && git rev-parse --short HEAD )"
  if [ "$DS_REV" = "e2307b1" ]; then
    echo "  DiffSinger     OK   $DS_REV"; DS_OK=1
  else
    echo "  DiffSinger     FAIL HEAD $DS_REV != e2307b1" >&2; FAIL=$((FAIL+1))
  fi
else
  echo "  DiffSinger     FAIL checkout e2307b1 に失敗" >&2; FAIL=$((FAIL+1))
fi

echo "| 3.5 export 入力の組み立て（ckpt を bundle へ exporter が要る名前で置く）"
# 表は checkpoint を `ckpts/run7_40k.ckpt` として落とし、config.yaml 等は別の
# bundle ディレクトリへ落とす。ところが `s7_export_manifest.py --ckpt-dir` は
# **同じディレクトリに `model_ckpt_steps_<N>.ckpt` と `config.yaml` が揃っている**
# ことを要求する。従来この段が無く、下の復旧レシピは必ず「入力が無い」で止まった
# （PR #303 第 6 巡 P2）。ここで bundle 側へ置き直して、レシピを実際に通す。
#
#   世代 | bundle(相対 $M) | ckpt(相対 $M) | ステップ数
STAGE=$(cat <<'STAGETABLE'
run5|ckpts/run5_bundle|ckpts/run5_40k.ckpt|40000
run6|ckpts/run6_bundle|ckpts/run6_40k.ckpt|40000
run7|run7_ckpt|ckpts/run7_40k.ckpt|40000
STAGETABLE
)
while IFS='|' read -r gen bundle ckpt steps; do
  [ -z "${gen:-}" ] && continue
  src="$M/$ckpt"; dst="$M/$bundle/model_ckpt_steps_${steps}.ckpt"
  # 置いた実体は**資産表の pin と直接**照合する。`src` と比べるだけだと
  # (a) hardlink では同一 inode なので照合が空振りし、(b) step 1 が先に走った
  # という**順序への依存**が残る。表を単一の正本にする。
  want="$(printf '%s\n' "$ASSETS" | awk -F'|' -v d="$ckpt" '$2 == d {print $4}')"
  if [ -z "$want" ]; then
    echo "  $gen            FAIL（資産表に $ckpt の pin が無い）" >&2; FAIL=$((FAIL+1)); continue
  fi
  if [ ! -f "$src" ]; then
    echo "  $gen            SKIP（$ckpt が無い）" >&2; continue
  fi
  note="verified"
  if [ "$(sha_of "$dst")" != "$want" ]; then
    # 同一 FS なら hardlink（556 MB x 3 の複製を避ける）。駄目ならコピー。
    rm -f "$dst"
    ln "$src" "$dst" 2>/dev/null || cp "$src" "$dst"
    note="restaged"
  fi
  got="$(sha_of "$dst")"
  if [ "$got" = "$want" ] && [ -f "$M/$bundle/config.yaml" ]; then
    printf '  %-14s OK   %-9s %s/{model_ckpt_steps_%s.ckpt, config.yaml}\n' \
      "$gen" "$note" "$bundle" "$steps"
  elif [ "$got" != "$want" ]; then
    echo "  $gen            FAIL sha ${got:0:16} != pin ${want:0:16}" >&2; FAIL=$((FAIL+1))
  else
    echo "  $gen            FAIL（$bundle に config.yaml が無い）" >&2; FAIL=$((FAIL+1))
  fi
done <<< "$STAGE"

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
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", f"{p}=={PIN[p]}"])
        if r.returncode != 0:
            print(f"  analysis stack FAIL {p}=={PIN[p]} を入れられなかった", file=sys.stderr)
            raise SystemExit(1)
    still = {p: md.version(p) for p in PIN if md.version(p) != PIN[p]}
    if still:
        print(f"  analysis stack STILL BAD {still}", file=sys.stderr)
        raise SystemExit(1)
    print("  analysis stack RESTORED")
PY
[ "$?" -eq 0 ] || { echo "  analysis stack FAIL 復元できなかった" >&2; FAIL=$((FAIL+1)); }

echo "| 5. export 用の隔離 venv（DiffSinger は numpy<2 を要求する）"
# 測定側インタプリタ（numpy 2.4.6 / librosa 0.11.0）とは **別** に保つ。
# 2026-08-22 実測: 再構築後に requirements を入れ直すと numpy が 2.x へ引き上げられて
# export が壊れる。requirements.txt を入れた **後で** numpy を pin へ戻す順序が要る。
VENV="$ROOT/venv_export"
if [ "$DS_OK" -ne 1 ]; then
  # requirements.txt は DiffSinger の checkout に属する。pin されていない版から
  # 依存を入れると、export 環境の由来が言えなくなる。
  echo "  export venv    SKIP（DiffSinger が e2307b1 に pin されていないので組まない）" >&2
else
VENV_FAIL=0
if [ ! -x "$VENV/bin/python" ]; then
  python -m venv "$VENV" || VENV_FAIL=1
  "$VENV/bin/pip" -q install --upgrade pip >/dev/null 2>&1 || VENV_FAIL=1
  "$VENV/bin/pip" -q install torch==2.13.0 --index-url https://download.pytorch.org/whl/cpu \
    >/dev/null 2>&1 || VENV_FAIL=1
fi
# この 2 つは既存 venv でも毎回走る。**終了コードを捨てない**（PR #303 第 4 巡 P2:
# 失敗しても import 検査が 3 つしか見ていなかったため、onnx が欠けたまま exit 0 に
# なりうる = 由来の言えない環境で export できてしまう）。
"$VENV/bin/pip" -q install -r "$M/DiffSinger/requirements.txt" >/dev/null 2>&1 || VENV_FAIL=1
"$VENV/bin/pip" -q install "numpy==1.26.4" >/dev/null 2>&1 || VENV_FAIL=1
# 検査は「export に要る依存を全部」import する（numpy の版も pin どおりか見る）。
VENV_CHECK="$("$VENV/bin/python" - <<'VPY' 2>/dev/null
import numpy, torch, lightning, onnx, onnxsim
assert numpy.__version__.startswith("1.26"), numpy.__version__
print(f"numpy {numpy.__version__} / torch {torch.__version__} / "
      f"lightning {lightning.__version__} / onnx {onnx.__version__}")
VPY
)"
if [ "$VENV_FAIL" -ne 0 ]; then
  echo "  export venv    FAIL（依存のインストールが失敗した）" >&2
  FAIL=$((FAIL+1))
elif [ -n "$VENV_CHECK" ]; then
  echo "  export venv    OK   $VENV_CHECK"
else
  echo "  export venv    FAIL（numpy<2 で torch/lightning/onnx が揃っていない）" >&2
  FAIL=$((FAIL+1))
fi
fi

echo "| result: OK=$OK SKIP=$SKIP FAIL=$FAIL"
[ "$FAIL" -eq 0 ] || { echo "| fail-closed: pin 不一致があるので止める" >&2; exit 1; }
cat <<'NEXT'
| 次: export → 校正レンダ再生成 → samples_sha256 照合
|
| export は **s7_export_manifest.py 経由で回す**。checkpoint と config.yaml を
| --ckpt-dir から一度読み、その同じバイト列を checkpoints/<exp>/ へ staging して
| から exporter を起動するので、「exporter が開く物」と「manifest が名乗る物」が
| 構造的に一致する（PR #303 第 3–5 巡）:
|
|   "$ROOT/venv_export/bin/python" \
|     voice_genesis/foundry/run8/s7_export_manifest.py \
|     --generation run7 \
|     --exporter-root "$M/DiffSinger" --exp <checkpoints/ 配下の厳密なフォルダ名> \
|     --ckpt-steps 40000 --ckpt-dir "$M/run7_ckpt" \
|     --expected-checkpoint-sha256 518df090a8154e61f28b529f731418f4f97d47c3b56d1326d354e6be4629fa93 \
|     --expected-config-sha256 e14ac2fde724998db05070550e86391c9090e582b1539747faa58356ae18d411 \
|     --out-dir "$ROOT/out/onnx_export_s6_run7_acoustic" \
|     --artifact acoustic_onnx=s6_run7_acoustic.onnx \
|     --artifact acoustic_dsconfig=dsconfig.yaml \
|     --artifact acoustic_phonemes_json=s6_run7_acoustic.phonemes.json \
|     --out "$ROOT/out/onnx_export_s6_run7_acoustic/export_manifest.json"
NEXT
