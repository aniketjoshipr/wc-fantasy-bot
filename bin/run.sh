#!/usr/bin/env bash
# Entrypoint. Modes:
#   run.sh auto    — cron mode: emails recommendation ~30h and ~8h before each
#                    deadline, live sub-alerts during matches, settles
#                    predicted-vs-actual after rounds. Safe to run every 20 min.
#   run.sh daily   — force a full recommendation now (news refresh + email)
#   run.sh live    — force a live matchday check
set -uo pipefail
cd "$(dirname "$0")/.."

# credentials (gmail app password, telegram) live outside the repo:
ENV_FILE="$HOME/.config/wcfantasy.env"
[ -f "$ENV_FILE" ] && set -a && . "$ENV_FILE" && set +a

MODE="${1:-auto}"
case "$MODE" in
  auto)
    python3 -m wcfantasy auto
    ;;
  daily)
    ./bin/update-news.sh || echo "WARN: news update failed, using stale news.json"
    python3 -m wcfantasy recommend --notify
    ;;
  live)
    python3 -m wcfantasy live --notify
    ;;
  *)  # pass through any other wcfantasy command with credentials loaded
    python3 -m wcfantasy "$@"
    ;;
esac
