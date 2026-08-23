"""test_run4_export_device_probe.py — run4 export-device probe の
セッション側ランナー（`run4_export_device_probe_runner.py`）と pod entry
script（`run4_export_device_probe_pod.sh`）の依存フリー検証。

本開発環境には RunPod API・GPU・torch が無いため、検証対象はロジック層
（payload 組み立ての純粋関数・HTML パーサ）と pod script の静的ガード
（テキストとして読んで trap 設置順序・pin 値の well-formed 性を確認）のみ。
実際の RunPod launch / pod 内 export の実測は本番 pod 走行が兼ねる
（`test_run5_bootstrap.py` docstring 冒頭の正直会計と対）。

import は run5 側 sibling test（`test_run5_bootstrap.py`）の
`sys.path.insert(0, .../"scripts"); import run5_bootstrap as r5b` パターンを
踏襲する（scripts/ はパッケージ化されていないスクリプト置き場のため）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import run4_export_device_probe_runner as runner  # noqa: E402

POD_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "run4_export_device_probe_pod.sh"
)


# --- (a) launch payload 組み立て（FIX 1 の回帰: script-commit / pin-commit 独立） ---


def test_build_launch_payload_shape() -> None:
    """POST /v1/pods payload の形状。VG-DEBT-008 probe の GPU/image/disk 仕様が
    そのまま埋め込まれていること。"""
    payload = runner.build_launch_payload(
        script_commit="1111111111111111111111111111111111abcd",
        pin_commit=runner.PIN_COMMIT_DEFAULT,
    )
    assert payload["imageName"] == "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"
    assert payload["gpuTypeIds"] == ["NVIDIA GeForce RTX 3090"]
    assert payload["containerDiskInGb"] == 60
    assert "8000/http" in payload["ports"]


def test_build_launch_payload_volume_survives_pod_stop() -> None:
    """FIX C の回帰固定: /workspace（結果 + console log）が pod stop を跨いで
    残るよう、volumeInGb と volumeMountPath が付与されていること
    （volumeInGb=0 だった旧実装は、コンテナディスクごと結果が消えていた —
    実インシデントの一因）。"""
    payload = runner.build_launch_payload(
        script_commit="1111111111111111111111111111111111abcd",
        pin_commit=runner.PIN_COMMIT_DEFAULT,
    )
    assert payload["volumeInGb"] == 10
    assert payload["volumeMountPath"] == "/workspace"
    # containerDiskInGb は FIX C の対象外 — 60 のまま変わらないこと。
    assert payload["containerDiskInGb"] == 60


def test_build_launch_payload_pin_commit_wiring() -> None:
    """env PROBE_PIN_COMMIT は pin_commit（既定 = 固定 pin 定数）を使う。"""
    payload = runner.build_launch_payload(
        script_commit="1111111111111111111111111111111111abcd",
        pin_commit=runner.PIN_COMMIT_DEFAULT,
    )
    assert payload["env"]["PROBE_PIN_COMMIT"] == runner.PIN_COMMIT_DEFAULT
    assert runner.PIN_COMMIT_DEFAULT == "cda36b9f2308128797c48976a9c90b28a4f1661a"


def test_build_launch_payload_keeps_script_commit_and_pin_commit_independent() -> None:
    """FIX 1 の回帰固定: dockerStartCmd に埋め込む raw URL は --script-commit
    のみを使い、--pin-commit は使わない（両者が異なる値のとき、raw URL に
    pin_commit の文字列が紛れ込んでいないこと）。混同すると pod script 側で
    PROBE_PIN_COMMIT != EXPECTED_PIN_COMMIT の fail-closed か、raw URL 404 の
    いずれかで課金だけが発生する（design 上の致命的回帰）。"""
    script_commit = "2222222222222222222222222222222222dcba"
    pin_commit = runner.PIN_COMMIT_DEFAULT
    assert script_commit != pin_commit

    payload = runner.build_launch_payload(script_commit=script_commit, pin_commit=pin_commit)
    docker_start_cmd = payload["dockerStartCmd"]
    assert docker_start_cmd[0:2] == ["bash", "-lc"]
    start_cmd = docker_start_cmd[2]

    raw_url = runner.POD_ENTRY_RAW_URL_TMPL.format(sha=script_commit)
    assert raw_url in start_cmd
    assert script_commit in raw_url
    assert pin_commit not in raw_url


# --- (a2) USER_AGENT / _build_request（FIX A の回帰: Cloudflare エッジが
# "Python-urllib/" prefix の UA を 403 "error code: 1010" で一律拒否した実
# インシデントの再発防止。ネットワークなし — Request オブジェクトの検査のみ）---


def test_user_agent_constant_does_not_start_with_python_urllib() -> None:
    """urllib の既定 UA（"Python-urllib/<version>"）は Cloudflare エッジに
    403 1010 で一律拒否されるため、本モジュールは必ず独自 UA を名乗ること。"""
    assert runner.USER_AGENT
    assert not runner.USER_AGENT.startswith("Python-urllib")


def test_build_request_injects_user_agent_with_no_extra_headers() -> None:
    req = runner._build_request("https://example.invalid/status.json", method="GET")
    assert req.get_header("User-agent") == runner.USER_AGENT
    assert req.get_method() == "GET"


def test_build_request_merges_caller_headers_without_dropping_user_agent() -> None:
    """`_request()` のような呼び出し元固有ヘッダ（Authorization 等）を渡しても
    User-Agent が上書きされて消えないこと（呼び出し元ヘッダは追加で乗る）。"""
    req = runner._build_request(
        "https://example.invalid/pods/abc",
        method="POST",
        data=b'{"x": 1}',
        headers={"Authorization": "Bearer secret", "Content-Type": "application/json"},
    )
    assert req.get_header("User-agent") == runner.USER_AGENT
    assert req.get_header("Authorization") == "Bearer secret"
    assert req.get_header("Content-type") == "application/json"
    assert req.get_method() == "POST"


def test_build_request_caller_headers_cannot_override_user_agent() -> None:
    """呼び出し元が誤って User-Agent を渡しても、`_build_request` の既定 merge
    順序（既定 UA を先に置き、呼び出し元 headers で update）では上書き可能だが、
    どの呼び出し元も本モジュール内では User-Agent を明示的に渡していない
    （= 常に USER_AGENT が最終的に使われる）ことを別途固定する。"""
    req = runner._build_request("https://example.invalid/x", method="GET", headers={})
    assert req.get_header("User-agent") == runner.USER_AGENT


def test_no_call_site_constructs_ualess_request() -> None:
    """本モジュールのソースを走査し、`urllib.request.Request(` の直接呼び出しが
    `_build_request` の定義自身（内部で唯一 1 回呼ぶ箇所）以外に無いこと。
    新しい call site が `_build_request` を経由せず追加される回帰を機械的に
    検出する。"""
    import inspect

    source = inspect.getsource(runner)
    call_count = source.count("urllib.request.Request(")
    assert call_count == 1, (
        f"expected exactly 1 raw urllib.request.Request( call (inside "
        f"_build_request itself), found {call_count} — every other call site "
        "must go through _build_request() to inject USER_AGENT"
    )


# --- (b) _DirListingParser: http.server index HTML の href scrape ---


def test_dir_listing_parser_extracts_hrefs_from_http_server_index() -> None:
    """`python3 -m http.server` の index HTML から href だけを拾えること
    （cmd_fetch の g1/ 配下列挙が依存する挙動）。"""
    html = (
        "<!DOCTYPE html>\n"
        "<html><head><title>Directory listing for /g1/</title></head>\n"
        "<body>\n<h1>Directory listing for /g1/</h1>\n<hr>\n<ul>\n"
        '<li><a href="../">../</a></li>\n'
        '<li><a href="acoustic.onnx">acoustic.onnx</a></li>\n'
        '<li><a href="gate_synth_summary_ritsu.json">gate_synth_summary_ritsu.json</a></li>\n'
        '<li><a href="sakura.wav">sakura.wav</a></li>\n'
        "</ul>\n<hr>\n</body>\n</html>\n"
    )
    parser = runner._DirListingParser()
    parser.feed(html)
    assert parser.hrefs == [
        "../",
        "acoustic.onnx",
        "gate_synth_summary_ritsu.json",
        "sakura.wav",
    ]


def test_dir_listing_parser_ignores_non_anchor_tags() -> None:
    html = '<html><body><p>no links here</p><a href="x.json">x.json</a></body></html>'
    parser = runner._DirListingParser()
    parser.feed(html)
    assert parser.hrefs == ["x.json"]


# --- (c) pod script の静的ガード（テキストとして検査。実行はしない） ---


def _pod_script_text() -> str:
    assert POD_SCRIPT_PATH.is_file(), f"pod script not found: {POD_SCRIPT_PATH}"
    return POD_SCRIPT_PATH.read_text(encoding="utf-8")


def test_pod_script_trap_installed_before_pin_guard_and_first_stage() -> None:
    """FIX 2 の回帰固定: self-stop trap（`trap on_exit EXIT`）は、
    PROBE_PIN_COMMIT の存在チェック / pin 不一致ガードよりも先に、かつ
    最初の実測ステージ（materials 取得）よりも先にインストールされている
    こと。どの exit パスも self-stop 保証の傘の中に入る。"""
    text = _pod_script_text()

    trap_idx = text.index('trap on_exit EXIT')
    pin_presence_guard_idx = text.index(
        'PROBE_PIN_COMMIT (pin commit SHA) を注入すること'
    )
    first_stage_idx = text.index('stage "1-materials-repo"')

    assert trap_idx < pin_presence_guard_idx < first_stage_idx, (
        "trap on_exit EXIT must be installed before both the PROBE_PIN_COMMIT "
        "guard and the first materials stage"
    )


def test_pod_script_watchdog_started_before_pin_guard() -> None:
    """watchdog（WATCHDOG_PID）も trap と同様、pin ガードより先に起動される
    こと（FIX 2 / FIX 5 の前提: on_exit が WATCHDOG_PID を参照する時点で
    それが必ず設定済みであるための順序保証）。"""
    text = _pod_script_text()
    watchdog_pid_assign_idx = text.index('WATCHDOG_PID=$!')
    pin_presence_guard_idx = text.index(
        'PROBE_PIN_COMMIT (pin commit SHA) を注入すること'
    )
    assert watchdog_pid_assign_idx < pin_presence_guard_idx


def test_pod_script_result_server_starts_at_t0_before_first_stage() -> None:
    """FIX B の回帰固定: 結果 HTTP サーバー（`http.server 8000`）は exit trap
    の中だけでなく t=0（trap/watchdog 確立直後）でも起動されていること —
    旧実装は on_exit 内の 1 箇所でしか起動しておらず、コンテナディスクと
    ともに結果が消える前に外から進捗を観測する手段が無かった。'http.server
    8000' の最初の出現が、最初の実測ステージ（materials 取得）マーカーより
    前にあることを固定する。"""
    text = _pod_script_text()
    server_idx = text.index("http.server 8000")
    first_stage_idx = text.index('stage "1-materials-repo"')
    assert server_idx < first_stage_idx, (
        "'http.server 8000' must appear before the first stage-1 marker in "
        "file order — the result server must start at t=0, not only in the "
        "exit trap"
    )


def test_pod_script_heartbeat_writer_present() -> None:
    """FIX B の回帰固定: heartbeat writer loop が存在すること（runner の
    cmd_fetch は heartbeat.txt/heartbeat.json を「channel is up」の早期
    reachability probe として使う — FIX D）。"""
    text = _pod_script_text()
    assert "heartbeat" in text
    assert "heartbeat.txt" in text
    assert "heartbeat.json" in text


def test_pod_script_heartbeat_writer_starts_before_first_stage() -> None:
    """heartbeat writer（HEARTBEAT_PID 起動）も結果サーバーと同様、最初の
    実測ステージより前に起動されていること。"""
    text = _pod_script_text()
    heartbeat_pid_assign_idx = text.index("HEARTBEAT_PID=$!")
    first_stage_idx = text.index('stage "1-materials-repo"')
    assert heartbeat_pid_assign_idx < first_stage_idx


def test_pod_script_on_exit_does_not_double_start_server() -> None:
    """FIX B の回帰固定: on_exit は SERVER_PID の生存確認をしてから起動する
    （t=0 起動分が生きていれば二重起動しない）。"""
    text = _pod_script_text()
    assert 'kill -0 "$SERVER_PID"' in text
    assert "not starting a second one" in text


def test_pod_script_pin_guards_use_die_not_bare_exit() -> None:
    """FIX 2: PROBE_PIN_COMMIT の存在チェック・不一致チェックはいずれも
    die() 経由（bare `exit 1` ではない）で、trap の傘に入った状態で落ちる
    こと。"""
    text = _pod_script_text()
    assert '[ -n "${PROBE_PIN_COMMIT:-}" ] || die "PROBE_PIN_COMMIT' in text
    assert 'die "PROBE_PIN_COMMIT=$PROBE_PIN_COMMIT != expected $EXPECTED_PIN_COMMIT"' in text
    # 旧実装のような bare exit ガード（trap 未設置時点の名残）が残っていない
    # ことも合わせて固定する。
    assert '  exit 1\nfi\n\nreadonly WORK=' not in text


# FIX 5: pod script が埋め込む唯一の 40 桁 hex pin は commit SHA
# （PROBE_PIN_COMMIT の期待値と DiffSinger の pin commit）のみ。他はすべて
# sha256（64 桁）のはず。
_KNOWN_40_CHAR_HEX_PINS = {
    runner.PIN_COMMIT_DEFAULT,  # == EXPECTED_PIN_COMMIT ("cda36b9f...")
    "e2307b1080b00f3999702ce9017cfd75c7f862fe",  # DIFFSINGER_COMMIT
}

# 実測してハードコードした 64 桁 sha256 pin の期待件数。pin を追加/削除したら
# この数値も同じ PR で更新すること（このテストはズレを検出するために意図的に
# 固定値で比較する）。
_EXPECTED_64_CHAR_HEX_PIN_COUNT = 24


def test_pod_script_embedded_hex_pins_are_well_formed() -> None:
    """埋め込み hex pin（40 桁 commit SHA / 64 桁 sha256）がすべて正しい桁数の
    どちらかであること。旧実装は 60〜70 桁の範囲でしか拾っておらず、60 桁
    未満に桁落ち・誤 truncate された pin は regex にすら引っかからず素通り
    していた——40〜80 桁まで広く拾ってから桁数で分類することでそれも検出する。
    40 桁は既知の commit pin 定数のみ許可し、64 桁以外の桁数は問答無用で fail、
    64 桁の件数は実測値にハードコードして意図しない増減を検出する。"""
    text = _pod_script_text()
    hexish = re.findall(r'"([0-9a-f]{40,80})"', text)
    assert len(hexish) >= 15, f"expected many hex pins, found {len(hexish)}"

    count_64 = 0
    for value in hexish:
        length = len(value)
        if length == 40:
            assert value in _KNOWN_40_CHAR_HEX_PINS, (
                f"unexpected 40-char hex value (not a known commit pin): {value!r}"
            )
        elif length == 64:
            assert re.fullmatch(r"[0-9a-f]{64}", value), f"non-hex pin: {value!r}"
            count_64 += 1
        else:
            pytest.fail(f"malformed pin length {length}: {value!r}")

    assert count_64 == _EXPECTED_64_CHAR_HEX_PIN_COUNT, (
        f"expected exactly {_EXPECTED_64_CHAR_HEX_PIN_COUNT} 64-hex sha256 pins in "
        f"the pod script, found {count_64}"
    )


def test_pod_script_expected_pin_commit_matches_runner_constant() -> None:
    """pod script の EXPECTED_PIN_COMMIT とランナーの --pin-commit 既定値が
    常に同じ 1 つの固定コミットを指していること（食い違いは即 fail-closed
    を招くため、乖離を機械的に検出する）。"""
    text = _pod_script_text()
    match = re.search(r'readonly EXPECTED_PIN_COMMIT="([0-9a-f]{40})"', text)
    assert match, "EXPECTED_PIN_COMMIT not found or not a 40-hex commit SHA"
    assert match.group(1) == runner.PIN_COMMIT_DEFAULT
    assert match.group(1) == "cda36b9f2308128797c48976a9c90b28a4f1661a"


# --- (d) FIX 3: CUDA 診断 + 3 択 gate（venv A unavailable でも system torch へ
# fallback できるようにする — 1 回の relaunch で host 固有破損 vs
# cu126-build×driver 相性の両仮説をカバーする）---


def test_pod_script_cuda_diagnostics_stage_present() -> None:
    """FIX 3 新設の stage "2d-cuda-diagnostics" が存在し、cuda_diagnostics.json
    を書き出すこと。"""
    text = _pod_script_text()
    assert 'stage "2d-cuda-diagnostics"' in text
    assert "cuda_diagnostics.json" in text
    assert '"cuda_diagnostics_ref": "cuda_diagnostics.json"' in text


def test_pod_script_cuda_diagnostics_captures_required_fields() -> None:
    """診断採取の対象（/dev/nvidia* / CUDA・NVIDIA env / ldconfig / torch.zeros
    試行の stderr・warning / venv A の pip freeze）が実装されていること。"""
    text = _pod_script_text()
    assert "/dev/nvidia*" in text
    assert "CUDA|NVIDIA" in text
    assert "libcuda|libnvidia-ml" in text
    assert 'torch.zeros(1, device="cuda")' in text
    assert "-W always" in text
    assert "cuda_zeros_attempt_text" in text
    assert "pip_freeze_torch_nvidia_lightning_onnx" in text


def test_pod_script_three_way_cuda_branch_markers_present() -> None:
    """3 択分岐（venv A CUDA OK / system torch fallback / 両方 unavailable）の
    各リテラルが存在すること。"""
    text = _pod_script_text()
    assert 'venv_a_torch_2.13.0+cu126' in text
    assert 'PRIMARY_GPU_STACK="system_torch"' in text
    assert 'stage "2-cuda-unavailable-host"' in text


def test_pod_script_old_single_branch_hard_cuda_gate_removed() -> None:
    """旧実装の一択 fail-closed gate（venv A unavailable = 即 die、system
    torch へのフォールバック無し）が残っていないこと（FIX 3 の回帰固定）。"""
    text = _pod_script_text()
    assert "venv A CUDA gate check" not in text
    assert "will not fall through to CPU export for G1/G2" not in text


def test_pod_script_both_cuda_unavailable_dies_with_distinct_stage() -> None:
    """venv A / system torch のどちらも CUDA unavailable のときは、host 固有
    破損とみなして distinct stage "2-cuda-unavailable-host" で die() すること
    （agent が別ホストで relaunch できるように、trap の傘の中で status.json
    に detail_tail として system+venv のエラーテキストが残る）。"""
    text = _pod_script_text()
    die_idx = text.index('die "both CUDA stacks unavailable on this host')
    stage_idx = text.index('stage "2-cuda-unavailable-host"')
    assert stage_idx < die_idx, (
        "stage must be set to the distinct failure marker before die() so "
        "status.json.stage reflects it"
    )


def test_pod_script_g1_only_runs_via_py_primary_never_hardcoded_py_a() -> None:
    """FIX 3 の中核回帰固定: G1（stage 3）は PY_PRIMARY 経由でのみ動く。
    PY_PRIMARY は venv A CUDA available 分岐か system_torch fallback 分岐の
    いずれかでしか代入されず、両方 unavailable 分岐は die() で先に script を
    落とすため、G1 が CUDA unavailable のまま走るコードパスは存在しない。"""
    text = _pod_script_text()
    g1_stage_idx = text.index('stage "3-arm-g1"')
    g1_section = text[g1_stage_idx : g1_stage_idx + 1500]
    assert 'gate_run_launch1 "$PY_PRIMARY"' in g1_section
    assert 'gate_run_launch1 "$PY_A"' not in g1_section

    # PY_PRIMARY の代入元は 2 か所のみ（venv A 分岐 / venv B fallback 分岐）。
    assert text.count('PY_PRIMARY="$PY_A"') == 1
    assert text.count('PY_PRIMARY="$PY_B"') == 1

    # 両方 unavailable 分岐の die() は PY_PRIMARY 代入より前（= G1 到達前）に
    # script を止める。
    unavailable_die_idx = text.index('die "both CUDA stacks unavailable on this host')
    assert unavailable_die_idx < g1_stage_idx


def test_pod_script_venv_b_fallback_creates_system_site_packages_venv() -> None:
    """system_torch fallback（venv B）は --system-site-packages で作成し、
    torch を再install/upgradeしない（--no-deps 経由の不足分のみ補う）こと。"""
    text = _pod_script_text()
    assert 'python3 -m venv --system-site-packages "$VENV_B"' in text
    assert '"$VENV_B/bin/pip" install --no-cache-dir --no-deps' in text
    # torch 自体を venv B へ pip install する行が無いこと（system-site 経由で
    # 可視化するだけ — 別 torch を引き込むと単一要因比較が壊れる）。
    venv_b_idx = text.index('VENV_B="$WORK/venv_sys"')
    verify_idx = text.index("VENV_B_VERIFY_OK")
    venv_b_section = text[venv_b_idx:verify_idx]
    assert "install torch" not in venv_b_section
    assert "torch==" not in venv_b_section


def test_pod_script_venv_b_verification_dies_on_mismatch() -> None:
    """venv B 作成後の検証（torch import 可能・バージョン不変・CUDA available）
    に失敗したら die() で fail-closed すること。"""
    text = _pod_script_text()
    assert "VENV_B_VERIFY_OK" in text
    assert 'die "venv_b (system-site fallback) verification failed' in text


def test_pod_script_c1s_arm_present_and_uses_venv_b() -> None:
    """FIX 3 新設の C1S（within-stack device 比較: venv B の GPU 版 vs CPU 版）
    が system_torch fallback 時のみ venv B（PY_B）で走ること。"""
    text = _pod_script_text()
    assert 'stage "5c-arm-c1s"' in text
    assert "C1S_TRIGGERED" in text
    c1s_idx = text.index('stage "5c-arm-c1s"')
    c1s_section = text[c1s_idx : c1s_idx + 1200]
    assert 'gate_run_launch1 "$PY_B"' in c1s_section


def test_pod_script_c1_still_forced_to_venv_a_regardless_of_branch() -> None:
    """C1（session baseline との tie-in）は分岐に関わらず常に venv A（PY_A）
    固定であること（PY_PRIMARY に置き換わっていないこと）。"""
    text = _pod_script_text()
    c1_stage_idx = text.index('stage "5-arm-c1"')
    c1b_stage_idx = text.index('stage "5b-arm-c1b"')
    c1_section = text[c1_stage_idx:c1b_stage_idx]
    assert 'gate_run_launch1 "$PY_A"' in c1_section
    assert 'gate_run_launch1 "$PY_PRIMARY"' not in c1_section


def test_pod_script_arm_b_skipped_with_promoted_to_primary_reason() -> None:
    """system_torch fallback 時は旧 Arm B（system python3 で GPU export を
    試す）が完全に重複するため、reason="promoted_to_primary" で skip される
    こと。"""
    text = _pod_script_text()
    assert 'ARM_B_REASON="promoted_to_primary"' in text
    assert '[ "$PRIMARY_GPU_STACK" = "system_torch" ]' in text


def test_pod_script_primary_gpu_stack_visible_in_heartbeat_and_results() -> None:
    """PRIMARY_GPU_STACK の決定が probe_results.json だけでなく heartbeat.json
    からも見えること（FIX 3 要件: 判定の早期可視化）。"""
    text = _pod_script_text()
    assert "PRIMARY_STACK_FILE" in text
    assert '"primary_gpu_stack": "%s"' in text  # heartbeat.json 側のフィールド
    assert '"primary_gpu_stack": primary_gpu_stack' in text  # probe_results.json 側


def test_pod_script_probe_results_extended_fields_present() -> None:
    """probe_results.json 組み立てに arm_stacks / cross_stack_note_applies /
    g1_onnx_eq_c1s_onnx が追加されていること。"""
    text = _pod_script_text()
    assert '"arm_stacks": arm_stacks' in text
    assert '"cross_stack_note_applies"' in text
    assert '"g1_onnx_eq_c1s_onnx"' in text
    assert '"venv_b_ref"' in text


# --- (e) FIX 6 (round-2 レビュー対応): pod.sh の restart 時 stale marker 退避 /
# runner の NETWORK_ERRORS 統一 / cmd_fetch の download-succeeded-now ゲート ---


def test_pod_script_prev_run_archive_present_before_t0_server_start() -> None:
    """FIX 6-1 の回帰固定: /workspace は pod stop/restart を跨いで永続化
    される（volumeInGb=10）ため、restart 直後の t=0 時点で前回 run の完了
    マーカーが $RESULTS に残っていれば、これから起動する t=0 HTTP サーバー
    より前に "prev_run_<timestamp>/" へ退避されていること（既存の
    order-guard 群と同じ「テキスト順序を index() で比較する」スタイル）。"""
    text = _pod_script_text()

    archive_var_idx = text.index('PREV_RUN_ARCHIVE="$RESULTS/prev_run_$(date -u +%Y%m%dT%H%M%SZ)"')
    # ピンポイントで t=0 起動分の http.server 呼び出しを指す（on_exit() 内の
    # フォールバック起動と同じ文字列 "http.server 8000" が定義順で先に本文中に
    # 現れるため、素の text.index("http.server 8000") は使えない — 既存の
    # test_pod_script_result_server_starts_at_t0_before_first_stage も同じ
    # 理由でこれを上限としてしか使っていない）。
    server_idx = text.index("starting result HTTP server on :8000 (dir=$RESULTS) at t=0")
    heartbeat_pid_assign_idx = text.index("HEARTBEAT_PID=$!")
    watchdog_pid_assign_idx = text.index("WATCHDOG_PID=$!")
    first_stage_idx = text.index('stage "1-materials-repo"')

    # watchdog 設置（trap/watchdog installation）の後、t=0 HTTP サーバー /
    # heartbeat writer の起動より前、かつ最初の実測ステージより前。
    assert watchdog_pid_assign_idx < archive_var_idx < server_idx
    assert archive_var_idx < heartbeat_pid_assign_idx
    assert archive_var_idx < first_stage_idx


def test_pod_script_prev_run_archive_covers_all_named_markers() -> None:
    """FIX 6-1: 退避判定の対象は status.json / probe_results.json /
    cuda_diagnostics.json / g1 の 4 つであること（Design Memo が明示的に
    名指しした完了マーカー一式）。"""
    text = _pod_script_text()
    markers_idx = text.index("_PREV_RUN_MARKERS=(")
    markers_section = text[markers_idx : markers_idx + 120]
    for marker in ("status.json", "probe_results.json", "cuda_diagnostics.json", "g1"):
        assert marker in markers_section, f"missing marker in _PREV_RUN_MARKERS: {marker}"


def test_pod_script_prev_run_archive_moves_markers_before_rest() -> None:
    """FIX 6-1: 完了マーカー自体を先に mv してから、残りを best-effort で
    退避する順序であること（stale status.json 等が配信されうる窓を作らない
    ための順序保証 — 本文内でのテキスト順序を固定する）。"""
    text = _pod_script_text()
    markers_loop_idx = text.index("for _m in \"${_PREV_RUN_MARKERS[@]}\"; do\n    if [ -e \"$RESULTS/$_m\" ]; then\n      mv -f")
    rest_loop_idx = text.index('for _f in "$RESULTS"/* "$RESULTS"/.[!.]*; do')
    assert markers_loop_idx < rest_loop_idx


def _runner_source() -> str:
    import inspect

    return inspect.getsource(runner)


def test_runner_defines_network_errors_tuple() -> None:
    """FIX 6-2 の回帰固定: `_request()` が retry-exhaustion 経路で実際に
    re-raise しうる例外型一式（URLError / TimeoutError / ConnectionError）を
    集約した module-level `NETWORK_ERRORS` が定義されていること。"""
    assert isinstance(runner.NETWORK_ERRORS, tuple)
    assert runner.NETWORK_ERRORS == (
        runner.urllib.error.URLError,
        TimeoutError,
        ConnectionError,
    )


def test_runner_cmd_terminate_uses_network_errors_for_both_attempts() -> None:
    """FIX 6-2 の回帰固定: cmd_terminate は stop 試行・DELETE 試行の両方で
    `NETWORK_ERRORS` を参照すること（旧実装は `except urllib.error.URLError`
    のみで、`_request` が retry 尽き後に re-raise しうる素の TimeoutError /
    ConnectionError を素通りさせ、DELETE フォールバックに落ちなかった）。"""
    import inspect

    source = inspect.getsource(runner.cmd_terminate)
    assert source.count("except NETWORK_ERRORS") == 2
    # The old round-1 guard (`except urllib.error.URLError`) must no longer be
    # an actual except clause — a mention inside an explanatory comment is
    # fine (and present, documenting the round-2 fix), so check the specific
    # clause form rather than bare substring containment.
    assert "except urllib.error.URLError as exc:" not in source
    assert "except urllib.error.URLError:" not in source


def test_runner_pod_health_and_download_use_network_errors() -> None:
    """FIX 6-2: cmd_fetch のポッド健全性インターリーブ（`_check_pod_not_dead`）
    と `_download` も同じ `NETWORK_ERRORS` タプルを使うこと（フォールバック
    目的でネットワークエラーを捕まえる call site 群の一貫性）。"""
    import inspect

    check_source = inspect.getsource(runner._check_pod_not_dead)
    assert "except NETWORK_ERRORS as exc:" in check_source

    download_source = inspect.getsource(runner._download)
    assert "except NETWORK_ERRORS as exc:" in download_source


def test_runner_cmd_fetch_success_gate_uses_download_result_not_bare_isfile() -> None:
    """FIX 6-3 の回帰固定: fetch の成功ゲートは「このダウンロードで
    probe_results.json / status.json を実際に取得できたか」
    (`marker_downloaded_now[...]`) を見ること。同じ --out ディレクトリに
    残っていた前回 invocation の stale ファイルが `os.path.isfile(...
    probe_results.json)` だけで成功扱いされていた回帰の固定（安価な
    tripwire — シミュレーションではなくソーススキャン）。"""
    source = _runner_source()
    assert 'probe_results_downloaded = marker_downloaded_now["probe_results.json"]' in source
    assert 'os.path.isfile(os.path.join(out_dir, "probe_results.json"))' not in source
    assert 'if not marker_downloaded_now["status.json"]:' in source


def test_runner_cmd_fetch_archives_stale_markers_before_polling() -> None:
    """FIX 6-3: fetch 開始時、out_dir に前回 invocation の status.json /
    probe_results.json が残っていれば "<out>/prev_fetch_<timestamp>/" へ
    退避してからポーリングに入ること（退避処理がポーリングループより前に
    呼ばれていることをソース順序で固定する）。"""
    source = _runner_source()
    archive_call_idx = source.index("_archive_stale_fetch_markers(out_dir)")
    poll_loop_idx = source.index("while time.time() < deadline:")
    assert archive_call_idx < poll_loop_idx

    import inspect

    helper_source = inspect.getsource(runner._archive_stale_fetch_markers)
    assert "prev_fetch_" in helper_source
    assert "_FETCH_COMPLETION_MARKERS" in helper_source


# --- (f) FIX 7: g1/ ダウンロード結果が success gate に配線されていること ---


def test_runner_cmd_fetch_captures_g1_download_result() -> None:
    """FIX 7 の回帰固定: 旧実装は g1/ ループ内の `_download()` 戻り値を破棄
    していた — listing 自体が成功しても、列挙された個々のバイナリ
    （acoustic.onnx / gate_*.wav）がリトライを使い切って失敗すると、fetch は
    それに気づかず 2 つの JSON マーカーが届くだけで exit 0 していた
    （安価な tripwire — シミュレーションではなくソーススキャン）。"""
    import inspect

    source = _runner_source()
    fetch_source = inspect.getsource(runner.cmd_fetch)

    # 列挙した g1 ファイルごとに _download() の戻り値を捨てずに記録している。
    assert "g1_download_ok[name] = _download(" in fetch_source
    assert "for name, ok in g1_download_ok.items()" in fetch_source

    # success gate（末尾）は g1_download_ok を参照して失敗させる。
    assert "g1_failed = sorted(name for name, ok in g1_download_ok.items() if not ok)" in source
    assert "if g1_failed:" in source
    gate_idx = source.index("if g1_failed:")
    raise_idx = source.index("raise SystemExit(2)", gate_idx)
    assert gate_idx < raise_idx

    # sha256 の再計算・照合も success gate に配線されていること（listing は
    # 成功してダウンロードも成功したが中身が破損/差し替わっているケースを
    # 「今回ダウンロード成功」だけでは検出できないため）。
    assert "_sha256_file(onnx_path)" in source
    assert "_sha256_file(wav_path)" in source
    assert "_extract_g1_expected_sha256(probe_results_payload)" in source
    assert "sha_mismatches" in source


def test_runner_g1_wav_key_mapping_swaps_song_and_speaker() -> None:
    """FIX 7: g1/ の wav ファイル名は `gate_<song>_<speaker>.wav`
    （gate_synth.py の out_name 規約）だが、probe_results.json 側の
    wav sha256 キーは `<speaker>_<song>`（順序が入れ替わる）。この対応が
    ずれると sha ゲートが常に不一致扱いになる（誤検知）か、常に
    比較対象なしでスキップされる（サイレントに無検査化する）ため、
    代表的な組み合わせで固定する。"""
    assert runner._parse_g1_wav_key("gate_sakura_ritsu.wav") == "ritsu_sakura"
    assert runner._parse_g1_wav_key("gate_umi_pjs.wav") == "pjs_umi"
    # マップ対象外のファイル（summary JSON 等）は None — sha ゲート対象外。
    assert runner._parse_g1_wav_key("gate_synth_summary_ritsu.json") is None
    assert runner._parse_g1_wav_key("acoustic.onnx") is None


def test_runner_extract_g1_expected_sha256_handles_both_schemas() -> None:
    """FIX 7: probe_results.json の g1 アーム記録は生の pod 出力
    (`arms.g1.{onnx_sha256, wavs.<key>.sha256}`) とアーカイブされた要約
    (`arms.g1_gpu_export_full.{onnx_sha256, wav_sha256.<key>}`) の 2 系統が
    ありうる（`docs/` 実例参照）。どちらの layout でも防御的に拾えること。"""
    raw_schema = {
        "arms": {
            "g1": {
                "onnx_sha256": "aaa",
                "wavs": {"ritsu_sakura": {"sha256": "bbb"}},
            }
        }
    }
    archived_schema = {
        "arms": {
            "g1_gpu_export_full": {
                "onnx_sha256": "ccc",
                "wav_sha256": {"ritsu_sakura": "ddd"},
            }
        }
    }
    unknown_schema = {"arms": {"something_else": {}}}

    assert runner._extract_g1_expected_sha256(raw_schema) == {
        "onnx_sha256": "aaa",
        "wav_sha256": {"ritsu_sakura": "bbb"},
    }
    assert runner._extract_g1_expected_sha256(archived_schema) == {
        "onnx_sha256": "ccc",
        "wav_sha256": {"ritsu_sakura": "ddd"},
    }
    assert runner._extract_g1_expected_sha256(unknown_schema) == {
        "onnx_sha256": None,
        "wav_sha256": {},
    }
