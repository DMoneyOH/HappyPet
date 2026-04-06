#!/bin/bash
# Happy Pet Product Reviews — Scheduled Article Generator
# Triggered by Windows Task Scheduler Mon/Thu 9AM Eastern
# Logs to /tmp/pawpicks_gen.log

SCRIPT_DIR="/home/derek/projects/pawpicks"
LOG="/tmp/pawpicks_gen.log"
ENV_FILE="$HOME/.env"

echo "$(date '+%Y-%m-%d %H:%M:%S') === SCHEDULER TRIGGERED ===" >> "$LOG"

# Load env vars
if [ -f "$ENV_FILE" ]; then
    export $(grep -v '^#' "$ENV_FILE" | xargs)
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') ERROR: ~/.env not found" >> "$LOG"
    exit 1
fi

# Check API key
if [ -z "$GEMINI_API_KEY" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') ERROR: GEMINI_API_KEY not set" >> "$LOG"
    exit 1
fi

export PATH=/home/derek/bin:/usr/local/bin:/usr/bin:/bin

cd "$SCRIPT_DIR" || exit 1

python3 generate_posts.py >> "$LOG" 2>&1

echo "$(date '+%Y-%m-%d %H:%M:%S') === SCHEDULER DONE ===" >> "$LOG"
