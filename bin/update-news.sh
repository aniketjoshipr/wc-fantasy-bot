#!/usr/bin/env bash
# Refresh data/news.json using headless Claude (web search for injuries,
# suspensions, predicted lineups, sentiment). Needs `claude` CLI on PATH.
set -euo pipefail
cd "$(dirname "$0")/.."

claude -p --permission-mode acceptEdits "$(cat <<'PROMPT'
You maintain data/news.json for a FIFA World Cup 2026 fantasy engine.

1. Read data/rounds.json to see the next scheduled round and which teams play.
2. Web-search latest news for EVERY team in that round: injuries, suspensions
   (yellow-card accumulation, reds), predicted lineups, rotation risk, and
   strong sentiment (e.g. 'X expected to be rested', 'Y in doubt').
3. Read data/players.json and resolve each affected player to their feed id.
4. REWRITE data/news.json keeping its existing schema:
   {"updated": "<utc iso now>", "players": {"<id>": {"status": "out|doubt|fit|nailed",
    "note": "<short reason + source>", "p_play": <optional 0-1 override>}}}
   - Include every player already in the file (update or clear stale entries).
   - Only include players genuinely newsworthy; keep notes under 100 chars.
5. Do not modify any other file.
PROMPT
)"
echo "news.json updated: $(date -u +%FT%TZ)"
