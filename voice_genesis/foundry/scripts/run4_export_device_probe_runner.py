#!/usr/bin/env python3
"""run4 export-device probe — セッション側ランナー（RunPod REST API v1）。

対応: `run4_export_device_probe_pod.sh`（pod entry。本ランナーはこれを
`dockerStartCmd` に注入して pod を起動し、完走後に `/workspace/probe_results/`
を回収する）。VG-DEBT-008 (a-2) の単一要因掃引の runbook 側。

stdlib のみ（urllib.request / json / time / os / argparse）。プロキシは
urllib のデフォルト挙動（`HTTPS_PROXY` 環境変数を尊重）に任せる。

サブコマンド:
    launch    --script-commit <sha> [--pin-commit <sha>] [--payload-override <json-file>]
    status    --pod <id>
    fetch     --pod <id> [--out DIR]
    terminate --pod <id>

`--script-commit` と `--pin-commit` は独立している: 前者は pod entry script
（`run4_export_device_probe_pod.sh`）を origin から取得する raw.githubusercontent.com
URL に埋め込むコミット（このブランチにしか無いことが多く、起動前に origin へ
push 済みでなければならない）、後者は pod script 内の固定 pin
（`EXPECTED_PIN_COMMIT`）と一致必須の `PROBE_PIN_COMMIT` env 値。混同すると
pod 内で `PROBE_PIN_COMMIT != EXPECTED_PIN_COMMIT` として fail-closed するか、
script URL が 404 して課金だけが発生する。

RunPod REST v1 のフィールド名（`dockerStartCmd` 等）は API 版で揺れうる —
4xx を受けたら本文をそのまま印字するので、運用者がその場でペイロードを
`--payload-override` で調整できる。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

API_BASE = "https://rest.runpod.io/v1"
# 実インシデント再発防止 (FIX A): Cloudflare エッジが *.proxy.runpod.net の前段で
# User-Agent が "Python-urllib/" で始まるリクエストを 403 "error code: 1010" で
# 一律拒否する。urllib の既定 UA のまま pod 向けリクエストを送ると、retrieval が
# 構造的に不可能になる（実際に本番でこれが起きた）。本モジュール内の urllib
# リクエストは必ず `_build_request()` 経由で組み立て、この UA を注入すること
# （pod-facing poll / _download / dir-listing scrape / REST call / raw.
# githubusercontent.com pre-flight — 一つの例外もなく全て）。
USER_AGENT = "svprpe-run4-probe-runner/1.0"
PIN_COMMIT_DEFAULT = "cda36b9f2308128797c48976a9c90b28a4f1661a"
IMAGE_NAME = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"
GPU_TYPE_ID = "NVIDIA GeForce RTX 3090"
POD_ENTRY_RAW_URL_TMPL = (
    "https://raw.githubusercontent.com/Yuu6798/ugh-prompt-engine/{sha}/"
    "voice_genesis/foundry/scripts/run4_export_device_probe_pod.sh"
)

REQUEST_TIMEOUT_SEC = 120
RETRY_COUNT = 3
RETRY_SLEEP_SEC = 5

# FIX 6 (round-2 レビュー対応): `_request()` の retry-exhaustion 経路
# （下記参照）は `urllib.error.URLError`（`HTTPError` はそのサブクラスなので
# 包含済み）だけでなく、素の `TimeoutError` / `ConnectionError` もそのまま
# re-raise しうる（`except (urllib.error.URLError, TimeoutError,
# ConnectionError) as exc: ... last_exc = exc` → `raise last_exc`）。旧
# `cmd_terminate` は `except urllib.error.URLError` のみで待ち受けており、
# 素の `TimeoutError`/`ConnectionError` はここを素通りして DELETE フォール
# バックへ落ちずに `cmd_terminate` ごと落ちていた（stop 失敗時に pod が
# 削除もされず放置されうる）。`_request` が実際に re-raise しうる例外型
# 一式をこの 1 箇所に集約し、フォールバック目的でネットワークエラーを
# 捕まえる全 call site（`cmd_terminate` / `_check_pod_not_dead` /
# `_download` 等）でこのタプルを使い回す。
NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionError)

# FIX 9 (closed-world requirement contract — family termination): the probe's
# measurement contract is FIXED — 3 speakers (ritsu/pjs/user) x 2 songs
# (sakura/umi), plus acoustic.onnx. Round-3 FIX 8 derived the "required" set
# from whatever probe_results.json happened to record (`_extract_g1_expected_
# sha256` output), which meant an unknown schema or a partially-populated
# record silently shrank the requirement set to "whatever was found" instead
# of failing — a closed-world contract violation (a G1 hash record that is
# missing a digest, or has a malformed one, is a probe defect, not grounds
# to stop requiring that artifact). This constant is the single source of
# truth for what a complete G1 hash record and a complete g1/ download MUST
# contain; it is never derived from runtime extraction.
REQUIRED_G1_WAV_KEYS = (
    "ritsu_sakura", "ritsu_umi",
    "pjs_sakura", "pjs_umi",
    "user_sakura", "user_umi",
)

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def _is_well_formed_sha256(value: Any) -> bool:
    """`value` が 64 桁小文字 16 進の sha256 として well-formed かどうか
    （FIX 9）。probe_results.json 記録値の完全性判定はすべてこれを経由する
    ——文字列以外・桁数違い・大文字混入・非 hex 文字はすべて malformed 扱い。"""
    return isinstance(value, str) and bool(_SHA256_HEX_RE.fullmatch(value))


def _build_request(
    url: str,
    method: str = "GET",
    data: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
) -> urllib.request.Request:
    """本モジュール内で urllib.request.Request を作る唯一のビルダー。
    User-Agent（`USER_AGENT`）を必ず注入する — 呼び出し側はこれを経由せずに
    urllib のリクエストオブジェクトを直接構築してはならない（FIX A: UA-less
    リクエストの一律禁止。呼び出し元固有のヘッダ（Authorization 等）は
    `headers` で渡せば UA の上に merge される）。"""
    merged_headers: Dict[str, str] = {"User-Agent": USER_AGENT}
    if headers:
        merged_headers.update(headers)
    return urllib.request.Request(url, data=data, method=method, headers=merged_headers)


def _api_key() -> str:
    key = os.environ.get("RUNPOD_API_KEY", "")
    if not key:
        print("error: RUNPOD_API_KEY is not set", file=sys.stderr)
        raise SystemExit(2)
    return key


def _request(
    method: str, path: str, body: Optional[Dict[str, Any]] = None,
    retry: bool = True,
) -> Dict[str, Any]:
    """RunPod REST API v1 を呼ぶ。失敗時は本文を印字してから例外送出する
    （4xx でも運用者がペイロードを調整できるよう、隠さず全文出す）。
    最大 3 回リトライするのはネットワークエラー（接続不可・タイムアウト）
    のみ — HTTP 応答が返った場合はリトライしない（4xx/5xx をそのまま返す）。

    `retry=False` は non-idempotent なリクエスト（POST /pods でのプール作成
    ＝ pod launch）専用。ネットワークエラー時のリトライは「応答が届かな
    かった」場合しか安全に想定できず、POST /pods は応答未達でもサーバ側で
    pod 作成が実際には成功している可能性があるため、再送すると pod が
    二重生成され二重課金になりうる（fail-closed: 1 回だけ試して例外を
    そのまま呼び出し元へ渡す）。GET/DELETE のような冪等な操作は既定の
    `retry=True` のままでよい。
    """
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }
    attempts = RETRY_COUNT if retry else 1
    last_exc: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        req = _build_request(url, method=method, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as resp:
                raw = resp.read()
                text = raw.decode("utf-8", errors="replace")
                print(f"| runner: {method} {url} -> {resp.status}")
                try:
                    return json.loads(text) if text else {}
                except json.JSONDecodeError:
                    print(text)
                    return {"_raw_text": text}
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            print(f"| runner: {method} {url} -> HTTP {exc.code}", file=sys.stderr)
            print(f"| runner: response body (verbatim):\n{body_text}", file=sys.stderr)
            raise
        except NETWORK_ERRORS as exc:
            last_exc = exc
            print(f"| runner: {method} {url} attempt={attempt} network error: {exc}", file=sys.stderr)
            if attempt < attempts:
                time.sleep(RETRY_SLEEP_SEC)
    assert last_exc is not None
    raise last_exc


def build_launch_payload(script_commit: str, pin_commit: str) -> Dict[str, Any]:
    """POST /v1/pods 用ペイロードの純粋関数（テスト容易性のため cmd_launch
    から分離）。`script_commit` は raw.githubusercontent.com URL 側にのみ、
    `pin_commit` は env PROBE_PIN_COMMIT 側にのみ使う（両者を混同しないこと
    が FIX 1 の要— dockerStartCmd の raw URL には pin_commit を絶対に
    埋め込まない）。"""
    entry_url = POD_ENTRY_RAW_URL_TMPL.format(sha=script_commit)
    # FIX 4: 旧実装の `curl -fsSL <url> | ... bash` は curl が失敗すると
    # 空 stdin へ bash を渡すだけになり、bash は何もせず exit 0 する
    # （pod がサイレントに idle billing し続ける — pod 起動直後は pod.sh 自身の
    # self-stop trap もまだ設置されていないため助からない）。まず一時ファイルへ
    # download し（--retry でネットワーク瞬断を吸収）、成功したときだけ実行する。
    # download 自体が失敗した場合のみ、pod.sh の trap を待たずに直接
    # runpodctl stop pod を呼んで self-stop する（$RUNPOD_POD_ID 未設定はガード）。
    download_cmd = (
        "curl -fsSL --retry 5 --retry-all-errors --retry-delay 5 "
        "-o /tmp/probe_entry.sh " + entry_url
    )
    run_cmd = (
        "PROBE_PIN_COMMIT=" + pin_commit +
        " bash /tmp/probe_entry.sh 2>&1 | tee /workspace/probe_console.log"
    )
    fallback_cmd = (
        "{ echo 'probe entry script download failed — self-stopping to avoid "
        "idle billing' >&2; "
        '[ -n "${RUNPOD_POD_ID:-}" ] && runpodctl stop pod "$RUNPOD_POD_ID"; }'
    )
    start_cmd = f"{download_cmd} && {run_cmd} || {fallback_cmd}"
    return {
        "name": "run4-export-device-probe",
        "imageName": IMAGE_NAME,
        "gpuTypeIds": [GPU_TYPE_ID],
        "gpuCount": 1,
        "cloudType": "COMMUNITY",
        "interruptible": False,
        "containerDiskInGb": 60,
        "volumeInGb": 10,
        "volumeMountPath": "/workspace",
        "ports": ["8000/http"],
        "env": {"PROBE_PIN_COMMIT": pin_commit},
        "dockerStartCmd": ["bash", "-lc", start_cmd],
    }


def _check_raw_url_reachable(url: str) -> None:
    """`url` が HTTP 200 を返すことを起動前に確定させる。raw.githubusercontent.com
    が 404 を返すケース（`--script-commit` が origin へ未 push）は、pod 内では
    `curl -fsSL ... | bash` が「空 stdin へ bash」= サイレントに何もせず即終了
    する形で現れ、症状が課金開始後にしか分からない。ここで先に落とすことで
    pre-billing error に変える。"""
    last_status: Optional[int] = None
    for method in ("HEAD", "GET"):
        req = _build_request(url, method=method)
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as resp:
                last_status = resp.status
                if resp.status == 200:
                    print(f"| runner: pre-flight check OK: {method} {url} -> 200")
                    return
                print(f"| runner: pre-flight check: {method} {url} -> {resp.status}", file=sys.stderr)
        except urllib.error.HTTPError as exc:
            last_status = exc.code
            print(f"| runner: pre-flight check: {method} {url} -> HTTP {exc.code}", file=sys.stderr)
        except NETWORK_ERRORS as exc:
            print(f"| runner: pre-flight check: {method} {url} network error: {exc}", file=sys.stderr)
    print(
        f"| runner: ABORT — pod entry script URL did not return HTTP 200 "
        f"(last status={last_status}): {url}\n"
        "| runner: this means --script-commit was not pushed to origin, or the "
        "path/branch is wrong. Launching now would silently no-op inside the pod "
        "(curl 404 into bash) AFTER billing starts. Push the commit to origin "
        "and retry.",
        file=sys.stderr,
    )
    raise SystemExit(2)


def _extract_pod_id(result: Dict[str, Any]) -> Optional[str]:
    """POST /pods レスポンスから pod id を抽出する（FIX 9 (2): id 抽出ロジック
    を cmd_launch から分離してユニットテスト可能にする）。RunPod REST v1 の
    フィールド名揺れに備え `.id` / `.podId` の両方を見る。"""
    pod_id = result.get("id") or result.get("podId")
    return pod_id if isinstance(pod_id, str) and pod_id else None


def cmd_launch(args: argparse.Namespace) -> None:
    script_commit = args.script_commit
    pin_commit = args.pin_commit
    entry_url = POD_ENTRY_RAW_URL_TMPL.format(sha=script_commit)
    _check_raw_url_reachable(entry_url)

    payload = build_launch_payload(script_commit, pin_commit)

    if args.payload_override:
        with open(args.payload_override, encoding="utf-8") as fh:
            override = json.load(fh)
        payload.update(override)
        print(f"| runner: merged payload overrides from {args.payload_override}")

    print("| runner: launch payload:")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    try:
        result = _request("POST", "/pods", body=payload, retry=False)
    except urllib.error.HTTPError:
        print(
            "| runner: launch failed with 4xx/5xx — RunPod REST v1 field names "
            "(dockerStartCmd/dockerArgs/env shape) may differ from what this script "
            "assumes. Adapt via --payload-override <json-file> (merged over the "
            "defaults above) and retry.",
            file=sys.stderr,
        )
        raise

    print("| runner: launch result:")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    pod_id = _extract_pod_id(result)
    if pod_id:
        print(f"| runner: pod id = {pod_id}")
        return

    # FIX 9 (2): launch must fail without a pod id. A 2xx from POST /pods
    # with no id extractable from the body used to only print a WARNING and
    # exit 0 — the caller had no signal that a pod might have been created
    # (and might be billing) with no id on record to stop/terminate it by.
    # Print the full response verbatim (again, on stderr, so it survives even
    # if stdout is discarded/redirected) plus a loud reconciliation
    # instruction, then hard-fail so the caller cannot miss this.
    print("| runner: full response body (verbatim, repeated on stderr):", file=sys.stderr)
    print(json.dumps(result, indent=2, ensure_ascii=False), file=sys.stderr)
    print(
        "| runner: *** LAUNCH RECONCILIATION REQUIRED ***\n"
        "| runner: POST /pods returned an HTTP success status but no pod id "
        "could be extracted from the response body (checked .id and .podId — "
        "see the full response above). A BILLABLE POD MAY HAVE BEEN CREATED "
        "even though this script cannot name it.\n"
        "| runner: MANUAL ACTION REQUIRED — list pods via `python3 "
        f"{sys.argv[0]} status --pod <id>` (once you have found a candidate "
        "id) or the RunPod web console, identify any pod matching this "
        f"launch (name={payload.get('name')!r}), and stop/terminate it "
        "manually if one exists. Do not assume no pod was created.",
        file=sys.stderr,
    )
    raise SystemExit(3)


def cmd_status(args: argparse.Namespace) -> None:
    result = _request("GET", f"/pods/{args.pod}")
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_stop(args: argparse.Namespace) -> None:
    """pod を停止するが削除しない（FIX D）。`terminate`（stop→失敗時 DELETE の
    stop+delete）と違い、/workspace ボリューム（volumeInGb=10 で永続化される —
    FIX C）を温存したまま止める。fetch がタイムアウト/失敗したときの salvage
    手段: pod を消さずに stop しておけば、後から volume を検分・再開できる。"""
    result = _request("POST", f"/pods/{args.pod}/stop")
    print("| runner: stop result:")
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_terminate(args: argparse.Namespace) -> None:
    try:
        result = _request("POST", f"/pods/{args.pod}/stop")
        print("| runner: stop result:")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except NETWORK_ERRORS as exc:
        # FIX 3 / FIX 6: NETWORK_ERRORS covers everything `_request()` can
        # re-raise — urllib.error.URLError (parent class of HTTPError, so 4xx/5xx
        # after the immediate non-retry raise is included too) as well as the
        # bare TimeoutError/ConnectionError it can re-raise after exhausting
        # retries. `except urllib.error.URLError` alone (round-1 FIX 3) missed
        # those bare types — they used to propagate straight out of
        # cmd_terminate, skipping the DELETE fallback below entirely.
        detail = f"HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else str(exc)
        print(f"| runner: POST /stop failed ({detail}), trying DELETE /pods/{args.pod}", file=sys.stderr)
    try:
        result = _request("DELETE", f"/pods/{args.pod}")
        print("| runner: delete result:")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except NETWORK_ERRORS:
        print("| runner: DELETE also failed — see body above. Pod may need manual termination.", file=sys.stderr)
        raise


class _DirListingParser(HTMLParser):
    """`python3 -m http.server` が出す index の href を拾う最小限のパーサ。"""

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Any]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.hrefs.append(value)


def _download(url: str, dest: str) -> bool:
    last_exc: Optional[BaseException] = None
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            req = _build_request(url, method="GET")
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as resp:
                data = resp.read()
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            with open(dest, "wb") as fh:
                fh.write(data)
            print(f"| runner: fetched {url} -> {dest} ({len(data)} bytes)")
            return True
        except urllib.error.HTTPError as exc:
            print(f"| runner: fetch {url} -> HTTP {exc.code}", file=sys.stderr)
            return False
        except NETWORK_ERRORS as exc:
            last_exc = exc
            print(f"| runner: fetch {url} attempt={attempt} network error: {exc}", file=sys.stderr)
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_SLEEP_SEC)
    if last_exc is not None:
        print(f"| runner: fetch {url} FAILED after {RETRY_COUNT} attempts: {last_exc}", file=sys.stderr)
    return False


def _sha256_file(path: str) -> str:
    """ダウンロード済みファイルの sha256 をチャンク読みで計算する（FIX 7:
    probe_results.json 記録値との照合用 — 巨大な acoustic.onnx を一括
    read せず 1MiB チャンクでストリーム計算する）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_g1_wav_key(name: str) -> Optional[str]:
    """`gate_<song>_<speaker>.wav`（gate_synth.py の out_name 規約
    `gate_{song}{_speaker}.wav`）から probe_results.json 側の wav sha256
    キー形式 `<speaker>_<song>`（song/speaker の順序が入れ替わる）を組み立て
    る（FIX 7）。命名規約に一致しない名前（`gate_synth_summary_*.json` 等、
    sha ゲート対象外のファイル）は None を返す。"""
    if not (name.startswith("gate_") and name.endswith(".wav")):
        return None
    stem = name[len("gate_"):-len(".wav")]
    parts = stem.split("_")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    song, speaker = parts
    return f"{speaker}_{song}"


def _extract_g1_expected_sha256(probe_results: Dict[str, Any]) -> Dict[str, Any]:
    """probe_results.json の g1 アーム記録から acoustic.onnx / gate wav の
    期待 sha256 を防御的に取り出す（FIX 7）。スキーマは 2 系統ありうる:
    生の pod 出力 = `arms.g1.{onnx_sha256, wavs.<speaker>_<song>.sha256}`、
    アーカイブされた要約 = `arms.g1_gpu_export_full.{onnx_sha256,
    wav_sha256.<speaker>_<song>}`。どちらでも拾えるよう両方を試し、layout が
    どちらとも異なる場合は空/None を返す（呼び出し側は比較を skip する）。"""
    empty: Dict[str, Any] = {"onnx_sha256": None, "wav_sha256": {}}
    arms = probe_results.get("arms")
    if not isinstance(arms, dict):
        return empty

    g1_summary = arms.get("g1_gpu_export_full")
    if isinstance(g1_summary, dict):
        wav_sha256 = g1_summary.get("wav_sha256")
        return {
            "onnx_sha256": g1_summary.get("onnx_sha256"),
            "wav_sha256": wav_sha256 if isinstance(wav_sha256, dict) else {},
        }

    g1_raw = arms.get("g1")
    if isinstance(g1_raw, dict):
        wavs = g1_raw.get("wavs")
        wav_sha256 = {}
        if isinstance(wavs, dict):
            for key, value in wavs.items():
                if isinstance(value, dict) and isinstance(value.get("sha256"), str):
                    wav_sha256[key] = value["sha256"]
        return {"onnx_sha256": g1_raw.get("onnx_sha256"), "wav_sha256": wav_sha256}

    return empty


def _g1_wav_filename_from_key(key: str) -> Optional[str]:
    """`_parse_g1_wav_key` の逆写像（FIX 8）。probe_results.json 側の wav
    sha256 キー `<speaker>_<song>` から g1/ 配下のファイル名
    `gate_<song>_<speaker>.wav` を組み立てる。キーが `<speaker>_<song>` の
    2 パーツ split に一致しない場合は None を返す（防御的 — 現行スキーマでは
    起きない想定）。"""
    parts = key.split("_")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    speaker, song = parts
    return f"gate_{song}_{speaker}.wav"


def _required_g1_filenames() -> List[str]:
    """FIX 9 (closed-world requirement contract): 必ず取得できていなければ
    ならない g1/ ファイル名集合を `REQUIRED_G1_WAV_KEYS` + "acoustic.onnx"
    という固定契約から導出する。round-3 FIX 8 の "record（probe_results.json）
    から required を導出する" 方式はここで廃止した——記録側の完全性は
    `_g1_hash_record_problems` が別途ゲートするため、required 自体は record
    の中身に依存させず常に同じ 7 ファイルを返す（未知スキーマ・欠落 digest
    は required を縮小する理由にはならない、というのが本 FIX の核）。"""
    names = ["acoustic.onnx"]
    for key in REQUIRED_G1_WAV_KEYS:
        name = _g1_wav_filename_from_key(key)
        assert name is not None, f"REQUIRED_G1_WAV_KEYS entry not well-formed: {key!r}"
        names.append(name)
    return sorted(names)


def _g1_hash_record_problems(g1_expected: Dict[str, Any]) -> List[str]:
    """FIX 9: probe_results.json の G1 ハッシュ記録が closed-world 契約
    （acoustic.onnx + `REQUIRED_G1_WAV_KEYS` の 6 キー、全て well-formed な
    64 桁 sha256）を満たしているかを検査する。欠落/malformed な digest ごと
    に人間可読な問題説明を返す（空リスト = 記録は完全）。旧 FIX 8 は未知
    スキーマ／欠落 digest を「required から静かに除外する」ことで吸収して
    いたが、closed-world 契約下ではそれらは probe 側の欠陥であり fetch の
    失敗として検出しなければならない。"""
    problems: List[str] = []

    onnx_sha256 = g1_expected.get("onnx_sha256")
    if not _is_well_formed_sha256(onnx_sha256):
        problems.append(f"acoustic.onnx: malformed/missing sha256 (got {onnx_sha256!r})")

    wav_sha256 = g1_expected.get("wav_sha256")
    if not isinstance(wav_sha256, dict):
        wav_sha256 = {}
    for key in REQUIRED_G1_WAV_KEYS:
        value = wav_sha256.get(key)
        if not _is_well_formed_sha256(value):
            problems.append(f"{key}: malformed/missing sha256 (got {value!r})")

    return problems


def _g1_missing_required(required: List[str], g1_download_ok: Dict[str, bool]) -> List[str]:
    """`required`（record 由来）のうち、g1/ の directory listing に一度も
    現れなかった（= pod が publish し損ねた）ものを返す（FIX 8）。listing に
    現れたが download 自体が失敗したものはここでは対象外 — そちらは既存の
    `g1_failed` ゲート（`g1_download_ok[name] is False`）が捕捉する。"""
    return sorted(name for name in required if name not in g1_download_ok)


_DEAD_STATUS_VALUES = {"exited", "terminated", "stopped", "dead", "failed"}


def pod_dead_status_value(pod_json: Dict[str, Any]) -> Optional[str]:
    """`pod_json` を防御的に走査し、pod がもう走っていないことを示す値が
    あればそれを返す（RunPod REST v1 のスキーマは版によって揺れうるため、
    "desiredStatus"/"status"/"runtime" のよくあるフィールド名を大文字小文字
    無視で見る。1 段だけネストした {"status": ...} 形も見る）。"""
    for key in ("desiredStatus", "status", "runtime"):
        value = pod_json.get(key)
        if isinstance(value, dict):
            value = value.get("status") or value.get("desiredStatus")
        if isinstance(value, str) and value.strip().lower() in _DEAD_STATUS_VALUES:
            return value
    return None


def _check_pod_not_dead(pod_id: str) -> None:
    """cmd_fetch のポーリング中、5 周に 1 回（約 5 分毎）呼ぶ健全性チェック。
    結果 HTTP エンドポイントが一度も応答していない状態で pod が
    EXITED/TERMINATED/STOPPED 相当、または 404（もう存在しない）なら、
    タイムアウトまで無駄にポーリングを続けず即座に中断する。"""
    try:
        pod_json = _request("GET", f"/pods/{pod_id}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            print(
                f"| runner: ABORT — pod {pod_id} lookup 404s (no longer exists) while "
                "the HTTP result endpoint has never answered. It died before serving "
                "results.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        print(
            f"| runner: pod health check got HTTP {exc.code} (non-fatal — continuing to poll)",
            file=sys.stderr,
        )
        return
    except NETWORK_ERRORS as exc:
        print(f"| runner: pod health check network error (non-fatal — continuing to poll): {exc}", file=sys.stderr)
        return

    dead_value = pod_dead_status_value(pod_json)
    if dead_value is not None:
        print(
            f"| runner: ABORT — pod {pod_id} status indicates it already exited "
            f"(matched value={dead_value!r}) while the HTTP result endpoint has "
            "never answered. Pod died before serving results. Pod JSON:",
            file=sys.stderr,
        )
        print(json.dumps(pod_json, indent=2, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)


_HEARTBEAT_STALL_WARN_SEC = 600


def _poll_once(url: str) -> Optional[str]:
    """`url` を 1 回だけ GET する。200 なら本文（デコード済み）を返し、それ以外
    （4xx/5xx・ネットワークエラー）は None を返す（cmd_fetch のポーリングは
    「まだ来ていない」を例外にしない設計 — FIX D）。"""
    req = _build_request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status == 200:
                return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError:
        pass
    except NETWORK_ERRORS:
        pass
    return None


# FIX 6 (round-2 レビュー対応): cmd_fetch の成功判定は「このダウンロードで
# status.json/probe_results.json を取得できたか」を見る（stale ファイル
# 混入を許さない）ため、この 2 つを固定リストとして名指しする。
_FETCH_COMPLETION_MARKERS = ("status.json", "probe_results.json")


def _archive_stale_fetch_markers(out_dir: str) -> None:
    """cmd_fetch のポーリング開始前に呼ぶ。`out_dir`（同一 `--out` を使った
    前回 invocation の置き土産）に status.json / probe_results.json が既に
    存在するなら "<out>/prev_fetch_<UTC timestamp>/" へ退避してからログする
    ——今回 invocation が失敗して診断用に残す status.json/probe_results.json
    が前回分と紛れないようにする（今回ダウンロードが 1 つも起きなくても
    out_dir 直下は今回分だけになる）。"""
    stale = [
        name for name in _FETCH_COMPLETION_MARKERS
        if os.path.isfile(os.path.join(out_dir, name))
    ]
    if not stale:
        return
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    archive_dir = os.path.join(out_dir, f"prev_fetch_{ts}")
    os.makedirs(archive_dir, exist_ok=True)
    for name in stale:
        shutil.move(os.path.join(out_dir, name), os.path.join(archive_dir, name))
    print(
        f"| runner: found stale marker(s) from a previous fetch invocation in "
        f"{out_dir} ({stale}) — archived into {archive_dir} so this invocation's "
        "download-success gate and any failure diagnostics are unambiguously "
        "from this run"
    )


def cmd_fetch(args: argparse.Namespace) -> None:
    base = f"https://{args.pod}-8000.proxy.runpod.net"
    out_dir = args.out or os.environ.get("CLAUDE_SCRATCHPAD_DIR") or "./probe_out"
    os.makedirs(out_dir, exist_ok=True)
    _archive_stale_fetch_markers(out_dir)

    print(
        f"| runner: polling {base}/heartbeat.txt (channel-up probe, printed once) "
        f"and {base}/status.json (completion signal) every 60s (up to {args.timeout}s)"
    )
    poll_started_at = time.time()
    deadline = poll_started_at + args.timeout
    status_ok = False
    heartbeat_ever_seen = False
    heartbeat_printed = False
    heartbeat_stall_warned = False
    poll_iteration = 0
    while time.time() < deadline:
        poll_iteration += 1

        # --- reachability probe: heartbeat is written from t=0 in pod.sh (FIX B),
        # so it is a much earlier "is the channel up" signal than status.json
        # (which only appears once the whole probe has finished/died). ---
        for hb_name in ("heartbeat.txt", "heartbeat.json"):
            hb_body = _poll_once(f"{base}/{hb_name}")
            if hb_body is not None:
                heartbeat_ever_seen = True
                if not heartbeat_printed:
                    print(f"| runner: heartbeat channel up — {hb_name}: {hb_body.strip()}")
                    heartbeat_printed = True
                break

        # --- completion signal: status.json is written once by pod.sh's exit
        # trap — reaching it means the whole probe (ok or failed) has finished
        # and the full result set is ready to download. ---
        # FIX 6: reuse _poll_once instead of re-implementing the same GET/200
        # check inline (this loop and _poll_once had drifted into two separate
        # implementations of "poll a URL, treat non-200/network-error as not
        # ready yet").
        if _poll_once(f"{base}/status.json") is not None:
            status_ok = True
            break
        print("| runner: status.json not ready yet")

        elapsed = time.time() - poll_started_at
        if not heartbeat_ever_seen and not heartbeat_stall_warned and elapsed > _HEARTBEAT_STALL_WARN_SEC:
            heartbeat_stall_warned = True
            print(
                f"| runner: *** WARNING *** pod has been polled for {int(elapsed)}s "
                "and the heartbeat channel has NEVER responded (no heartbeat.txt/"
                "heartbeat.json seen at any point). This usually means the result "
                "channel is unreachable (network/proxy/User-Agent issue) or the pod "
                "is stuck before it reaches stage 0. This script will NOT "
                "auto-terminate the pod — consider operator abort "
                f"(pod={args.pod}).",
                file=sys.stderr,
            )

        if poll_iteration % 5 == 0:
            try:
                _check_pod_not_dead(args.pod)
            except SystemExit:
                print(
                    "| runner: pod NOT deleted; use `stop` to preserve the /workspace "
                    f"volume (python3 {sys.argv[0]} stop --pod {args.pod})",
                    file=sys.stderr,
                )
                raise

        time.sleep(60)

    if not status_ok:
        print(f"| runner: TIMEOUT after {args.timeout}s waiting for {base}/status.json", file=sys.stderr)
        print(
            "| runner: pod NOT deleted; use `stop` to preserve the /workspace volume "
            f"(python3 {sys.argv[0]} stop --pod {args.pod})",
            file=sys.stderr,
        )
        raise SystemExit(1)

    print("| runner: status.json reachable — downloading result set")
    # FIX 1: the previous hardcoded list silently dropped any top-level file
    # added later to pod.sh's $RESULTS output (e.g. cuda_diagnostics.json,
    # venv_b_env.json, arm_b_output.log were all missing here). Scrape the
    # served ROOT directory index the same way g1/ is scraped below, and
    # union it with the hardcoded baseline — the union keeps known files even
    # if the root listing scrape itself fails, while any future addition to
    # $RESULTS is discovered automatically instead of requiring this list to
    # be kept in sync by hand.
    top_level_files = [
        "status.json", "probe_results.json", "env_snapshot.json",
        "venv_a_env.json", "probe_console.log", "watchdog.log",
    ]
    root_index_url = f"{base}/"
    req = _build_request(root_index_url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        parser = _DirListingParser()
        parser.feed(html)
        root_names = [h for h in parser.hrefs if h not in ("../", "./") and not h.endswith("/")]
        print(f"| runner: root listing: {root_names}")
        top_level_files = sorted(set(top_level_files) | set(root_names))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        print(
            f"| runner: could not list {root_index_url} (falling back to hardcoded "
            f"top-level file list): {exc}",
            file=sys.stderr,
        )

    # FIX 6: track *this invocation's* _download() outcome for the completion
    # markers specifically — the success gate below must require
    # download-succeeded-now, not merely file-exists (a stale file left over
    # from a previous invocation in the same --out dir used to pass the old
    # `os.path.isfile(...)` gate even though nothing was downloaded this run).
    marker_downloaded_now: Dict[str, bool] = {name: False for name in _FETCH_COMPLETION_MARKERS}

    ok_count = 0
    for name in top_level_files:
        downloaded = _download(f"{base}/{name}", os.path.join(out_dir, name))
        if downloaded:
            ok_count += 1
        if name in marker_downloaded_now:
            marker_downloaded_now[name] = downloaded

    # g1/ 配下は index listing を href scrape して列挙する（内容は run 依存で
    # 固定できないため）。
    # FIX 7: 旧実装は _download() の戻り値を破棄していた — listing 自体は
    # 成功しても、列挙された個々のバイナリ（acoustic.onnx / gate_*.wav 等）が
    # リトライを使い切って失敗しても fetch は気づかず、2 つの JSON マーカー
    # さえ届けば exit 0 していた（列挙された G1 成果物の欠落を偽成功にする
    # 経路）。列挙した各ファイル名と `_download()` の結果を
    # `g1_download_ok` に記録し、末尾のゲートで「今回 invocation で列挙され
    # た g1 ファイルが 1 つでも取得できなかったら失敗」として扱う。
    g1_download_ok: Dict[str, bool] = {}
    g1_index_url = f"{base}/g1/"
    req = _build_request(g1_index_url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        parser = _DirListingParser()
        parser.feed(html)
        g1_names = [h for h in parser.hrefs if h not in ("../", "./") and not h.endswith("/")]
        print(f"| runner: g1/ listing: {g1_names}")
        for name in g1_names:
            g1_download_ok[name] = _download(
                f"{g1_index_url}{name}", os.path.join(out_dir, "g1", name)
            )
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        print(f"| runner: could not list {g1_index_url}: {exc}", file=sys.stderr)

    print(f"| runner: fetch complete ({ok_count}/{len(top_level_files)} top-level files). out_dir={out_dir}")

    # FIX 1 (P1 偽成功経路): ここまでは診断のため常に全ダウンロードを試みるが、
    # status.json の中身を見ずに exit 0 していた旧実装は probe 失敗（status!=ok）
    # や probe_results.json 欠落でも「fetch complete」と成功終了していた。
    # exit 0 は「status=ok AND probe_results.json 回収済み」の場合のみに限定する。
    #
    # FIX 6 (round-2 レビュー対応): 旧ゲートは `os.path.isfile(...
    # probe_results.json)` — 同じ --out ディレクトリに残っていた前回
    # invocation の stale ファイルでも true になってしまっていた
    # （`_archive_stale_fetch_markers` で入口では退避済みだが、二重の保険と
    # して）。ゲートは必ず「このダウンロードで実際に成功したか」
    # （`marker_downloaded_now`）を見る。
    if not marker_downloaded_now["status.json"]:
        print(
            "| runner: *** PROBE FETCH FAILED *** status.json was not "
            "downloaded by this invocation (missing/stale) — treating as "
            "failure.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    status_path = os.path.join(out_dir, "status.json")
    try:
        with open(status_path, encoding="utf-8") as fh:
            status_payload = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"| runner: *** PROBE FETCH FAILED *** could not parse downloaded "
            f"status.json ({exc}) — treating as failure.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    status_value = status_payload.get("status")
    probe_results_downloaded = marker_downloaded_now["probe_results.json"]

    if status_value != "ok":
        detail_tail = status_payload.get("detail_tail", "")
        print(
            f"| runner: *** PROBE FAILED *** status.json status={status_value!r} "
            f"(expected 'ok'). detail_tail:\n{detail_tail}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    if not probe_results_downloaded:
        print(
            "| runner: *** PROBE FETCH FAILED *** status=ok but probe_results.json "
            "was not downloaded — result set incomplete.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    # FIX 7: g1/ ゲート（download-succeeded-now + sha256 一致）。best-effort の
    # 診断・回収は上ですべて終わらせた後、既存の status/marker ゲートに続けて
    # 末尾でまとめて判定する。
    g1_failed = sorted(name for name, ok in g1_download_ok.items() if not ok)
    if g1_failed:
        print(
            "| runner: *** PROBE FETCH FAILED *** g1/ artifact(s) listed by "
            f"{g1_index_url} failed to download in this invocation: {g1_failed}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    probe_results_path = os.path.join(out_dir, "probe_results.json")
    try:
        with open(probe_results_path, encoding="utf-8") as fh:
            probe_results_payload = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"| runner: *** PROBE FETCH FAILED *** could not parse downloaded "
            f"probe_results.json ({exc}) — cannot verify g1/ sha256.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    g1_expected = _extract_g1_expected_sha256(probe_results_payload)

    # FIX 9 (closed-world requirement contract, gate (a)): probe_results.json
    # must supply a well-formed 64-hex sha256 for acoustic.onnx AND for every
    # one of REQUIRED_G1_WAV_KEYS — judged against the fixed contract, never
    # against what _extract_g1_expected_sha256 happened to find. Unknown
    # schema / missing / malformed digests are FAILURES here, not an
    # emptied requirement set (that was FIX 8's now-obsolete behavior).
    hash_record_problems = _g1_hash_record_problems(g1_expected)
    if hash_record_problems:
        print(
            "| runner: *** PROBE FETCH FAILED *** incomplete G1 hash record — "
            "probe_results.json does not supply a well-formed sha256 for every "
            f"required artifact: {hash_record_problems}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    # FIX 8 (gate (b), now driven by the FIX 9 constant): required set is the
    # fixed closed-world contract, not the record — listing must supply every
    # required filename (pod best-effort cp failures that never reach the g1/
    # listing are detected here, independent of what transport happened to
    # deliver).
    g1_required = _required_g1_filenames()
    g1_missing_from_listing = _g1_missing_required(g1_required, g1_download_ok)
    if g1_missing_from_listing:
        print(
            "| runner: *** PROBE FETCH FAILED *** recorded artifact not published "
            f"by pod — absent from {g1_index_url} listing entirely (never "
            f"downloaded, sha256 unverifiable): {g1_missing_from_listing}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    sha_mismatches: List[str] = []

    expected_onnx_sha = g1_expected["onnx_sha256"]
    onnx_path = os.path.join(out_dir, "g1", "acoustic.onnx")
    if isinstance(expected_onnx_sha, str) and expected_onnx_sha and os.path.isfile(onnx_path):
        actual_onnx_sha = _sha256_file(onnx_path)
        if actual_onnx_sha != expected_onnx_sha:
            sha_mismatches.append(
                f"g1/acoustic.onnx: downloaded={actual_onnx_sha} recorded={expected_onnx_sha}"
            )
            print(
                f"| runner: *** SHA256 MISMATCH *** g1/acoustic.onnx downloaded="
                f"{actual_onnx_sha} recorded(probe_results.json)={expected_onnx_sha}",
                file=sys.stderr,
            )

    expected_wav_sha256 = g1_expected["wav_sha256"]
    for name in sorted(g1_download_ok):
        key = _parse_g1_wav_key(name)
        if key is None:
            continue  # gate_synth_summary_*.json 等 — マップされていないので sha ゲート対象外
        expected_wav_sha = expected_wav_sha256.get(key)
        if not isinstance(expected_wav_sha, str) or not expected_wav_sha:
            continue  # probe_results.json 側に対応する記録値が無い
        wav_path = os.path.join(out_dir, "g1", name)
        if not os.path.isfile(wav_path):
            continue  # 既に g1_failed ゲートで捕捉済みのはず — 二重報告しない
        actual_wav_sha = _sha256_file(wav_path)
        if actual_wav_sha != expected_wav_sha:
            sha_mismatches.append(f"g1/{name}: downloaded={actual_wav_sha} recorded={expected_wav_sha}")
            print(
                f"| runner: *** SHA256 MISMATCH *** g1/{name} downloaded={actual_wav_sha} "
                f"recorded(probe_results.json)={expected_wav_sha}",
                file=sys.stderr,
            )

    if sha_mismatches:
        print(
            "| runner: *** PROBE FETCH FAILED *** g1/ artifact sha256 mismatch against "
            f"probe_results.json: {sha_mismatches}",
            file=sys.stderr,
        )
        raise SystemExit(2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_launch = sub.add_parser("launch", help="RunPod pod を起動する")
    p_launch.add_argument(
        "--script-commit", required=True,
        help=(
            "pod entry script (run4_export_device_probe_pod.sh) を取得する raw."
            "githubusercontent.com URL に埋め込むコミット SHA。origin へ push 済みで、"
            "voice_genesis/foundry/scripts/run4_export_device_probe_pod.sh を含んで"
            "いなければならない（--pin-commit とは独立 — 混同しないこと）"
        ),
    )
    p_launch.add_argument(
        "--pin-commit", default=PIN_COMMIT_DEFAULT,
        help=(
            "env PROBE_PIN_COMMIT の値。pod script 内の固定 pin "
            f"(既定 {PIN_COMMIT_DEFAULT}) と一致しなければ pod 側で fail-closed する"
            "（--script-commit とは独立 — 混同しないこと）"
        ),
    )
    p_launch.add_argument("--payload-override", default=None, help="POST /v1/pods payload へ merge する JSON ファイル")
    p_launch.set_defaults(func=cmd_launch)

    p_status = sub.add_parser("status", help="pod の状態を取得する")
    p_status.add_argument("--pod", required=True)
    p_status.set_defaults(func=cmd_status)

    p_stop = sub.add_parser(
        "stop", help="pod を停止する（削除しない — terminate と違い /workspace ボリュームを温存する salvage 手段）"
    )
    p_stop.add_argument("--pod", required=True)
    p_stop.set_defaults(func=cmd_stop)

    p_fetch = sub.add_parser("fetch", help="結果一式をポーリングして回収する")
    p_fetch.add_argument("--pod", required=True)
    p_fetch.add_argument("--out", default=None, help="保存先ディレクトリ（既定: $CLAUDE_SCRATCHPAD_DIR または ./probe_out）")
    p_fetch.add_argument("--timeout", type=int, default=10800, help="status.json ポーリングの最大待ち秒数")
    p_fetch.set_defaults(func=cmd_fetch)

    p_terminate = sub.add_parser("terminate", help="pod を停止/削除する")
    p_terminate.add_argument("--pod", required=True)
    p_terminate.set_defaults(func=cmd_terminate)

    return parser


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
