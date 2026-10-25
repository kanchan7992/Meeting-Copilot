#!/bin/bash
# ============================================================
#  Meeting Copilot — One-click start script for Kanchan
#  Usage: bash start.sh
# ============================================================

PROJ="/Users/kanchanrautela/Downloads/PROJECT 101"
ENV_FILE="$PROJ/backend/.env.docker"
BRIEF="$PROJ/backend/company_brief.md"
APP="$PROJ/desktop/release/mac-arm64/Meeting Copilot.app"

echo ""
echo "🚀 Starting Meeting Copilot..."
echo ""

# ── 1. Start Docker Desktop if not running ──────────────────
if ! docker info &>/dev/null; then
  echo "🐳 Starting Docker Desktop..."
  open -a Docker
  echo "   Waiting for Docker to be ready..."
  for i in $(seq 1 30); do
    sleep 2
    if docker info &>/dev/null; then break; fi
    echo "   ... ($((i*2))s)"
  done
fi

if ! docker info &>/dev/null; then
  echo "❌ Docker failed to start. Please open Docker Desktop manually and try again."
  exit 1
fi
echo "✅ Docker is running"

# ── 2. Start backend container ──────────────────────────────
if docker ps --format '{{.Names}}' | grep -q "^meeting-copilot$"; then
  echo "✅ Backend already running — skipping"
else
  echo "🔧 Starting backend..."
  docker rm -f meeting-copilot 2>/dev/null
  docker run -d \
    --name meeting-copilot \
    --env-file "$ENV_FILE" \
    -p 127.0.0.1:8765:8765 \
    -v mc-data:/srv/data \
    -v "$BRIEF:/srv/company_brief.md:ro" \
    --restart unless-stopped \
    meeting-copilot-backend

  echo "   Waiting for backend to be healthy..."
  for i in $(seq 1 15); do
    sleep 2
    if curl -sf http://127.0.0.1:8765/healthz &>/dev/null; then break; fi
    echo "   ... ($((i*2))s)"
  done

  if curl -sf http://127.0.0.1:8765/healthz &>/dev/null; then
    echo "✅ Backend is healthy at http://127.0.0.1:8765"
  else
    echo "❌ Backend failed to start. Check logs with: docker logs meeting-copilot"
    exit 1
  fi
fi

# ── 3. Launch desktop overlay ───────────────────────────────
echo "🖥  Launching Meeting Copilot overlay..."
open "$APP"
sleep 2

echo ""
echo "============================================"
echo "  ✅ Meeting Copilot is running!"
echo "============================================"
echo "  Backend : http://127.0.0.1:8765"
echo "  Overlay : Look for the ring icon in menu bar"
echo ""
echo "  Settings → Backend URL: http://127.0.0.1:8765"
echo "           → Token: (leave blank)"
echo "           → Save"
echo ""
echo "  Hotkeys:"
echo "   Ctrl+Alt+A  → Answer latest question"
echo "   Ctrl+Alt+H  → Show / hide overlay"
echo "   Ctrl+Alt+P  → Pause / resume listening"
echo "   Ctrl+Alt+S  → Type a question manually"
echo "============================================"
echo ""
echo "  To STOP everything: bash stop.sh"
echo ""
