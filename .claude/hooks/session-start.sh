#!/bin/bash
# SessionStart hook: install the project + dev extras so that ruff,
# pytest, and the /wrap-up discipline-test gate (python -m pytest
# tests/discipline/ -q) work in Claude Code on the web sessions.
# Synchronous so deps are guaranteed ready before the agent loop starts.
set -euo pipefail

# Only run in the remote (Claude Code on the web) environment; local dev
# machines manage their own virtualenv.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"

# SessionStart stdout is injected into the model context, so keep pip's
# progress / "Requirement already satisfied" chatter out of it: capture
# all install output and only surface it (on stderr) if the install fails.
# Idempotent: pip install -e is safe to re-run and leverages the cached
# container state.
log="$(mktemp)"
if ! python -m pip install -q -e ".[dev]" >"$log" 2>&1; then
  echo "session-start: pip install -e \".[dev]\" failed:" >&2
  cat "$log" >&2
  exit 1
fi
# ANALYSIS_STACK_PIN の復元（2026-08-22 の実測で追加）。
# `pip install -e ".[dev]"` は librosa 経由で numba を最新へ引き上げる。ところが
# このリポジトリには「numba 0.67.0 × numpy 2.4.6 は `librosa.pyin` が SIGSEGV・
# 0.66.0 で解消」という実測記録があり（scripts/run5_bootstrap.py の
# ANALYSIS_STACK_PIN / run8 の B-1 校正）、コンテナ再構築のたびに宣言 pin から
# 静かにずれる。**pin を実装に合わせて書き換えるのではなく、実装を pin へ戻す。**
if ! python - <<'PIN' >>"$log" 2>&1
import importlib.metadata as md, subprocess, sys
PIN = {"numba": "0.66.0"}
bad = {p: md.version(p) for p in PIN if md.version(p) != PIN[p]}
if bad:
    for p in bad:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", f"{p}=={PIN[p]}"], check=True)
    print("restored", bad, "->", {p: md.version(p) for p in PIN})
PIN
then
  echo "session-start: ANALYSIS_STACK_PIN の復元に失敗（測定は fail-closed で止まる）" >&2
  cat "$log" >&2
fi

echo "session-start: dev dependencies ready"
