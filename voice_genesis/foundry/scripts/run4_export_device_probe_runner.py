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
    start_cmd = (
        f"curl -fsSL {entry_url} | "
        f"PROBE_PIN_COMMIT={pin_commit} bash 2>&1 | tee /workspace/probe_console.log"
    )
    return {
        "name": "run4-export-device-probe",
        "imageName": IMAGE_NAME,
        "gpuTypeIds": [GPU_TYPE_ID],
        "gpuCount": 1,
        "cloudType": "COMMUNITY",
        "interruptible": False,
        "containerDiskInGb": 60,
        "volumeInGb": 0,
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
        req = urllib.request.Request(url, method=method)
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
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
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
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
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


def cmd_fetch(args: argparse.Namespace) -> None:
    base = f"https://{args.pod}-8000.proxy.runpod.net"
    out_dir = args.out or os.environ.get("CLAUDE_SCRATCHPAD_DIR") or "./probe_out"
    os.makedirs(out_dir, exist_ok=True)

    print(f"| runner: polling {base}/status.json every 60s (up to {args.timeout}s)")
    deadline = time.time() + args.timeout
    status_ok = False
    poll_iteration = 0
    while time.time() < deadline:
        poll_iteration += 1
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

        if poll_iteration % 5 == 0:
            _check_pod_not_dead(args.pod)

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
