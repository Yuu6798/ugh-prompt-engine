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
echo "session-start: dev dependencies ready"
