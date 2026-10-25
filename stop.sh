#!/bin/bash
# ============================================================
#  Meeting Copilot — Stop everything
#  Usage: bash stop.sh
# ============================================================

echo ""
echo "🛑 Stopping Meeting Copilot..."

# Stop backend container
if docker ps --format '{{.Names}}' | grep -q "^meeting-copilot$"; then
  docker stop meeting-copilot && docker rm meeting-copilot
  echo "✅ Backend stopped"
else
  echo "ℹ️  Backend was not running"
fi

# Quit desktop app
pkill -f "Meeting Copilot" 2>/dev/null && echo "✅ Desktop overlay closed" || echo "ℹ️  Desktop app was not running"

echo ""
echo "👋 Meeting Copilot stopped."
echo ""
