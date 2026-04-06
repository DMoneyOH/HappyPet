#!/usr/bin/env bash
# ============================================================
# happypet_cron.sh
# Full autopilot pipeline for Happy Pet Product Reviews
#
# What it does (in order):
#   1. Load env vars
#   2. Run article generator (Gemini content)
#      - For each new article:
#        a. Generate branded Pinterest pin image
#        b. Append row to Google Sheet (with pin URL)
#        c. Commit article + pin to GitHub (triggers live deploy)
#   3. Log completion
#
# Cron setup (runs daily at 6am):
#   crontab -e
#   0 6 * * * /home/derek/projects/pawpicks/happypet_cron.sh >> /tmp/happypet_cron.log 2>&1
#
# Manual run:
#   bash /home/derek/projects/pawpicks/happypet_cron.sh
# ============================================================

set -euo pipefail

REPO="/home/derek/projects/pawpicks"
LOG="/tmp/happypet_cron.log"
ENV_FILE="$HOME/.env"

echo "======================================" | tee -a "$LOG"
echo "$(date '+%Y-%m-%d %H:%M:%S') START happypet_cron" | tee -a "$LOG"

# Load env vars
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
    echo "$(date '+%H:%M:%S') ENV loaded" | tee -a "$LOG"
else
    echo "ERROR: $ENV_FILE not found" | tee -a "$LOG"
    exit 1
fi

# Verify required keys
for VAR in GEMINI_API_KEY HAPPYPET_SHEET_ID_DOGS HAPPYPET_SHEET_ID_CATS; do
    if [ -z "${!VAR:-}" ]; then
        echo "ERROR: $VAR not set in .env" | tee -a "$LOG"
        exit 1
    fi
done

export PATH="/usr/bin:/bin:/home/derek/bin:$PATH"

cd "$REPO"

# Pull latest before running
git pull origin main --quiet
echo "$(date '+%H:%M:%S') Git pull done" | tee -a "$LOG"

# Run article generator
# This internally:
#   - Generates article content via Gemini
#   - Generates branded pin image
#   - Appends to Google Sheet with pin URL
#   - Commits + pushes article + pin
echo "$(date '+%H:%M:%S') Starting article generator..." | tee -a "$LOG"
python3 "$REPO/generate_posts.py" 2>&1 | tee -a "$LOG"

echo "$(date '+%H:%M:%S') DONE happypet_cron" | tee -a "$LOG"
echo "======================================" | tee -a "$LOG"
