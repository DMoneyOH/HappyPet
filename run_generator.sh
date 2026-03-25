#!/usr/bin/env bash
# run_generator.sh
# Explicit env injection - no source/export ambiguity
set -euo pipefail
export PATH="/home/derek/bin:/usr/local/bin:/usr/bin:/bin"
export GEMINI_API_KEY="$(grep GEMINI_API_KEY /home/derek/projects/pawpicks/.env | cut -d= -f2)"
cd /home/derek/projects/pawpicks
rm -f /tmp/pawpicks_gen.lock
exec python3 generate_posts.py
