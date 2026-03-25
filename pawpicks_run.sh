#!/usr/bin/env bash
# pawpicks_run.sh - definitive single-instance launcher
# Solves: PATH issues, duplicate processes, log buffering, env inheritance

set -euo pipefail
export PATH=/usr/bin:/bin:/usr/local/bin:/home/derek/bin
export PYTHONUNBUFFERED=1

REPO=/home/derek/projects/pawpicks
LOG=/tmp/pawpicks_gen.log
LOCK=/tmp/pawpicks_gen.lock
PYTHON=/usr/bin/python3

# Kill any existing instance
if [[ -f "$LOCK" ]]; then
    OLD=$(cat "$LOCK" 2>/dev/null || echo 0)
    if [[ "$OLD" -gt 0 ]] && kill -0 "$OLD" 2>/dev/null; then
        echo "Killing existing PID=$OLD"
        kill "$OLD" 2>/dev/null || true
        sleep 2
    fi
    rm -f "$LOCK"
fi

rm -f "$LOG"

# Load key
export GEMINI_API_KEY
GEMINI_API_KEY=$(grep GEMINI_API_KEY "$REPO/.env" | cut -d= -f2 | tr -d '[:space:]')

echo "Key: ${GEMINI_API_KEY:0:20}..."
echo "Python: $($PYTHON --version 2>&1)"
echo "Launching..."

# Single process via setsid, unbuffered, explicit paths
cd "$REPO"
setsid "$PYTHON" -u "$REPO/generate_posts.py" > "$LOG" 2>&1 &
PID=$!
echo $PID > "$LOCK"
echo "Generator running PID=$PID"
echo "Log: $LOG"
