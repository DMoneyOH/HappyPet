#!/usr/bin/env bash
# autopublish.sh — weekly content generation + git push for PawPicks
# Cron: 0 7 * * 1  (every Monday 7am ET)
set -Eeuo pipefail

REPO_DIR="/home/derek/projects/pawpicks"
LOG_FILE="${REPO_DIR}/autopublish.log"
PATH="/home/derek/bin:/usr/local/bin:/usr/bin:/bin"

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG_FILE"; }

cd "$REPO_DIR"

log "START autopublish"

# Load Gemini key from .env if present
if [[ -f "${REPO_DIR}/.env" ]]; then
    # shellcheck source=/dev/null
    source "${REPO_DIR}/.env"
fi

: "${GEMINI_API_KEY:?GEMINI_API_KEY not set — aborting}"

log "Running content generator..."
python3 "${REPO_DIR}/generate_posts.py" >> "$LOG_FILE" 2>&1

NEW_FILES=$(git status --porcelain | grep -c "^??" || true)

if [[ "$NEW_FILES" -gt 0 ]]; then
    log "Committing ${NEW_FILES} new post(s)..."
    git add _posts/
    git commit -m "auto: weekly content $(date '+%Y-%m-%d')"
    git push origin main
    log "PUSH complete"
else
    log "No new posts generated — nothing to push"
fi

log "END autopublish"
