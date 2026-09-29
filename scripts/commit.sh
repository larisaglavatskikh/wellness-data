#!/usr/bin/env bash
# Commit whatever changed under data/ and secrets/ and push, retrying on races.
set -euo pipefail
git config user.name  "wellness-bot"
git config user.email "wellness-bot@users.noreply.github.com"
git add data secrets
if git diff --cached --quiet; then echo "Nothing to commit"; exit 0; fi
git commit -q -m "$1"
for i in 1 2 3 4 5; do
  if git pull -q --rebase && git push -q; then echo "Pushed"; exit 0; fi
  sleep $((i * 5))
done
echo "Push failed after retries" >&2; exit 1
