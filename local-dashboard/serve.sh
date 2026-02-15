#!/usr/bin/env bash
# Serve the local tool-time dashboard
# 1. Runs analyze.py to refresh data
# 2. Copies analysis.json to dashboard directory
# 3. Starts a local HTTP server
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "Refreshing analysis..."
python3 "$PLUGIN_ROOT/analyze.py" --timezone America/Los_Angeles

# Copy analysis.json to serve directory
cp "$HOME/.claude/tool-time/analysis.json" "$SCRIPT_DIR/analysis.json"

# Find available port
PORT=8742
while command -v lsof >/dev/null 2>&1 && lsof -Pi :"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; do
  PORT=$((PORT + 1))
done

echo "Dashboard: http://localhost:$PORT"
echo "Press Ctrl+C to stop"
cd "$SCRIPT_DIR"
python3 -m http.server "$PORT"
