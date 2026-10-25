#!/usr/bin/env bash
# Reproducible end-to-end demo for Agent Logger.
#
# Creates a throwaway git repo, starts a tracked session, simulates agent
# edits, ends the session (recording files + summary), then prints status
# and the change summary. Uses a temp SQLite DB; nothing in your real repos
# is touched.
#
# Usage:  bash scripts/demo.sh
set -euo pipefail

cd "$(dirname "$0")/.."
PY="${PYTHON:-.venv/bin/python3}"

WORK="${TMPDIR:-/tmp}/agent-logger-demo-$$"
DEMO_REPO="$WORK/repo"
export AGENT_LOGGER_DB_PATH="$WORK/sessions.db"

cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

echo "==> Demo workspace: $WORK"

# 1. Create a scratch repo with one commit
mkdir -p "$DEMO_REPO"
git init -q -b main "$DEMO_REPO" 2>/dev/null || git -C "$DEMO_REPO" init -q
git -C "$DEMO_REPO" config user.email demo@example.com
git -C "$DEMO_REPO" config user.name demo
echo "print('hello')" > "$DEMO_REPO/hello.py"
git -C "$DEMO_REPO" add .
git -C "$DEMO_REPO" commit -qm "initial commit"

echo
echo "==> 1. Start a tracked session"
"$PY" -m agent_logger start "$DEMO_REPO" --agent demo-agent

echo
echo "==> 2. Simulate agent edits (modify one file, create one new file)"
echo "print('hello world v2')" > "$DEMO_REPO/hello.py"
cat > "$DEMO_REPO/utils.py" <<'EOF'
def add(a, b):
    return a + b
EOF

echo
echo "==> 3. End the session (records changed/new files, generates summary)"
"$PY" -m agent_logger end

echo
echo "==> 4. Show session status"
"$PY" -m agent_logger status

echo
echo "==> 5. Show the change summary"
"$PY" -m agent_logger changes

echo
echo "==> Demo complete. (Workspace was temporary and has been cleaned up.)"
