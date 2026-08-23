#!/usr/bin/env python3
"""run4 export-device probe — セッション側ランナー（RunPod REST API v1）。

対応: `run4_export_device_probe_pod.sh`（pod entry。本ランナーはこれを
`dockerStartCmd` に注入して pod を起動し、完走後に `/workspace/probe_results/`
を回収する）。VG-DEBT-008 (a-2) の単一要因掃引の runbook 側。

stdlib のみ（urllib.request / json / time / os / argparse）。プロキシは
urllib のデフォルト挙動（`HTTPS_PROXY` 環境変数を尊重）に任せる。

サブコマンド:
    launch    --commit <sha> [--payload-override <json-file>]
    status    --pod <id>
    fetch     --pod <id> [--out DIR]
    terminate --pod <id>

RunPod REST v1 のフィールド名（`dockerStartCmd` 等）は API 版で揺れうる —
4xx を受けたら本文をそのまま印字するので、運用者がその場でペイロードを
`--payload-override` で調整できる。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

API_BASE = "https://rest.runpod.io/v1"
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


def _api_key() -> str:
    key = os.environ.get("RUNPOD_API_KEY", "")
    if not key:
        print("error: RUNPOD_API_KEY is not set", file=sys.stderr)
        raise SystemExit(2)
    return key


def _request(
    method: str, path: str, body: Optional[Dict[str, Any]] = None,
    full_url: Optional[str] = None,
) -> Dict[str, Any]:
    """RunPod REST API v1 を呼ぶ。失敗時は本文を印字してから例外送出する
    （4xx でも運用者がペイロードを調整できるよう、隠さず全文出す）。
    最大 3 回リトライするのはネットワークエラー（接続不可・タイムアウト）
    のみ — HTTP 応答が返った場合はリトライしない（4xx/5xx をそのまま返す）。
    """
    url = full_url if full_url is not None else f"{API_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }
    last_exc: Optional[BaseException] = None
    for attempt in range(1, RETRY_COUNT + 1):
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
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
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_exc = exc
            print(f"| runner: {method} {url} attempt={attempt} network error: {exc}", file=sys.stderr)
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_SLEEP_SEC)
    assert last_exc is not None
    raise last_exc


def cmd_launch(args: argparse.Namespace) -> None:
    commit = args.commit
    entry_url = POD_ENTRY_RAW_URL_TMPL.format(sha=commit)
    start_cmd = (
        f"curl -fsSL {entry_url} | "
        f"PROBE_PIN_COMMIT={commit} bash 2>&1 | tee /workspace/probe_console.log"
    )
    payload: Dict[str, Any] = {
        "name": "run4-export-device-probe",
        "imageName": IMAGE_NAME,
        "gpuTypeIds": [GPU_TYPE_ID],
        "gpuCount": 1,
        "cloudType": "COMMUNITY",
        "interruptible": False,
        "containerDiskInGb": 60,
        "volumeInGb": 0,
        "ports": ["8000/http"],
        "env": {"PROBE_PIN_COMMIT": commit},
        "dockerStartCmd": ["bash", "-lc", start_cmd],
    }

    if args.payload_override:
        with open(args.payload_override, encoding="utf-8") as fh:
            override = json.load(fh)
        payload.update(override)
        print(f"| runner: merged payload overrides from {args.payload_override}")

    print("| runner: launch payload:")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    try:
        result = _request("POST", "/pods", body=payload)
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
    pod_id = result.get("id") or result.get("podId")
    if pod_id:
        print(f"| runner: pod id = {pod_id}")
    else:
        print("| runner: WARNING — could not find pod id in response (see full JSON above)", file=sys.stderr)


def cmd_status(args: argparse.Namespace) -> None:
    result = _request("GET", f"/pods/{args.pod}")
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_terminate(args: argparse.Namespace) -> None:
    try:
        result = _request("POST", f"/pods/{args.pod}/stop")
        print("| runner: stop result:")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except urllib.error.HTTPError as exc:
        print(f"| runner: POST /stop failed ({exc.code}), trying DELETE /pods/{args.pod}", file=sys.stderr)
    try:
        result = _request("DELETE", f"/pods/{args.pod}")
        print("| runner: delete result:")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except urllib.error.HTTPError:
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
            req = urllib.request.Request(url, method="GET")
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
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_exc = exc
            print(f"| runner: fetch {url} attempt={attempt} network error: {exc}", file=sys.stderr)
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_SLEEP_SEC)
    if last_exc is not None:
        print(f"| runner: fetch {url} FAILED after {RETRY_COUNT} attempts: {last_exc}", file=sys.stderr)
    return False


def cmd_fetch(args: argparse.Namespace) -> None:
    base = f"https://{args.pod}-8000.proxy.runpod.net"
    out_dir = args.out or os.environ.get("CLAUDE_SCRATCHPAD_DIR") or "./probe_out"
    os.makedirs(out_dir, exist_ok=True)

    print(f"| runner: polling {base}/status.json every 60s (up to {args.timeout}s)")
    deadline = time.time() + args.timeout
    status_ok = False
    while time.time() < deadline:
        req = urllib.request.Request(f"{base}/status.json", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status == 200:
                    status_ok = True
                    break
        except urllib.error.HTTPError as exc:
            print(f"| runner: status.json not ready yet (HTTP {exc.code})")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            print(f"| runner: status.json not reachable yet: {exc}")
        time.sleep(60)

    if not status_ok:
        print(f"| runner: TIMEOUT after {args.timeout}s waiting for {base}/status.json", file=sys.stderr)
        raise SystemExit(1)

    print("| runner: status.json reachable — downloading result set")
    top_level_files = [
        "status.json", "probe_results.json", "env_snapshot.json",
        "venv_a_env.json", "probe_console.log", "watchdog.log",
    ]
    ok_count = 0
    for name in top_level_files:
        if _download(f"{base}/{name}", os.path.join(out_dir, name)):
            ok_count += 1

    # g1/ 配下は index listing を href scrape して列挙する（内容は run 依存で
    # 固定できないため）。
    g1_index_url = f"{base}/g1/"
    req = urllib.request.Request(g1_index_url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        parser = _DirListingParser()
        parser.feed(html)
        g1_names = [h for h in parser.hrefs if h not in ("../", "./") and not h.endswith("/")]
        print(f"| runner: g1/ listing: {g1_names}")
        for name in g1_names:
            _download(f"{g1_index_url}{name}", os.path.join(out_dir, "g1", name))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        print(f"| runner: could not list {g1_index_url}: {exc}", file=sys.stderr)

    print(f"| runner: fetch complete ({ok_count}/{len(top_level_files)} top-level files). out_dir={out_dir}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_launch = sub.add_parser("launch", help="RunPod pod を起動する")
    p_launch.add_argument("--commit", default=PIN_COMMIT_DEFAULT, help="PROBE_PIN_COMMIT (pin commit SHA)")
    p_launch.add_argument("--payload-override", default=None, help="POST /v1/pods payload へ merge する JSON ファイル")
    p_launch.set_defaults(func=cmd_launch)

    p_status = sub.add_parser("status", help="pod の状態を取得する")
    p_status.add_argument("--pod", required=True)
    p_status.set_defaults(func=cmd_status)

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
