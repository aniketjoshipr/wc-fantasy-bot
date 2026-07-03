#!/usr/bin/env bash
# Sync local <-> cloud runner. Run after `squad --apply-plan N` or after
# editing news/elo/config, so the GitHub Actions runs use your latest team.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --rebase
git add data/squad.json data/news.json data/elo.json data/config.json
git diff --cached --quiet || git commit -m "local update $(date -u +%F)"
git push
echo "synced."
