#!/usr/bin/env bash
# dc_keepalive.sh - keeps Desktop Commander conversation active
# Runs every 4 minutes via cron, writes timestamp to a log
# This prevents WSL from going idle and DC from losing context
export PATH=/usr/bin:/bin:/usr/local/bin:/home/derek/bin
STAMP=$(date '+%Y-%m-%d %H:%M:%S')
echo "[$STAMP] DC keepalive ping" >> /tmp/dc_keepalive.log
# Rotate log to prevent growth
tail -100 /tmp/dc_keepalive.log > /tmp/dc_keepalive.tmp && mv /tmp/dc_keepalive.tmp /tmp/dc_keepalive.log
