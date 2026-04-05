#!/bin/bash
cd /home/derek/projects/pawpicks
export PATH=/home/derek/bin:/usr/local/bin:/usr/bin:/bin
export GIT_TERMINAL_PROMPT=0

git add -A _posts/
git commit -m "chore: remove old placeholder articles — v11 regenerating fresh"
git push origin main
echo "Done: $?"
rm /home/derek/projects/pawpicks/cleanup_posts.sh
