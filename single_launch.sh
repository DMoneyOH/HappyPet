#!/usr/bin/env bash
# single_launch.sh v2 - explicit env injection, guaranteed single instance
set -euo pipefail
export PATH=/usr/bin:/bin:/usr/local/bin:/home/derek/bin

LOG=/tmp/pawpicks_gen.log
LOCK=/tmp/pawpicks_gen.lock
REPO=/home/derek/projects/pawpicks

if [ -f "$LOCK" ]; then
    echo "Already running (PID=$(cat $LOCK))"
    exit 1
fi

# Read key directly from file and export explicitly
GEMINI_API_KEY=$(grep GEMINI_API_KEY "$REPO/.env" | cut -d= -f2)
export GEMINI_API_KEY

echo "Key loaded: ${GEMINI_API_KEY:0:20}..."
echo "Launching generator..."

cd "$REPO"
rm -f "$LOG"

nohup env GEMINI_API_KEY="$GEMINI_API_KEY" PATH="$PATH" python3 generate_posts.py > "$LOG" 2>&1 &
PID=$!
echo "Launched PID=$PID"
echo $PID > "$LOCK"
