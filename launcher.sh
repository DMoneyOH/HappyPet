#!/usr/bin/env bash
# launcher.sh - starts generate_posts.py as persistent background process
set -euo pipefail
REPO="/home/derek/projects/pawpicks"
LOG="/tmp/pawpicks_gen.log"
PIDFILE="/tmp/pawpicks_gen.pid"

# Kill any existing instance
if [[ -f "$PIDFILE" ]]; then
    OLD_PID=$(cat "$PIDFILE")
    kill "$OLD_PID" 2>/dev/null && echo "Killed old PID $OLD_PID"
    rm -f "$PIDFILE"
fi

rm -f "$LOG" /tmp/pawpicks_gen.lock

# Source env
cd "$REPO"
set -a; source .env; set +a
export PATH="/home/derek/bin:/usr/local/bin:/usr/bin:/bin"

# Launch detached under setsid so it survives WSL session close
setsid python3 "$REPO/generate_posts.py" >> "$LOG" 2>&1 &
PID=$!
echo $PID > "$PIDFILE"
echo "Generator launched PID=$PID"
echo "Log: $LOG"
sleep 3
echo "--- Initial log ---"
cat "$LOG"
