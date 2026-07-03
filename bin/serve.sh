#!/usr/bin/env bash
# Serve the dashboard at http://localhost:8077 (it re-renders on every
# recommend/auto run; just reload the page).
cd "$(dirname "$0")/../dashboard"
echo "dashboard -> http://localhost:8077"
exec python3 -m http.server 8077 --bind 127.0.0.1
