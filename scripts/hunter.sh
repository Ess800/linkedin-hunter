#!/usr/bin/env bash
# ──────────────────────────────────────────────
# LinkedIn Hunter — Service Management Script
# ──────────────────────────────────────────────
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="linkedin-hunter"
SERVICE_FILE="${PROJECT_DIR}/scripts/linkedin-hunter.service"
BRIDGE_SCRIPT="${PROJECT_DIR}/bridge/job_worker.py"
BRIDGE_PORT=8766
PID_FILE="${PROJECT_DIR}/.bridge.pid"

# Secrets live in .env (mode 600, never committed).
if [ -f "${PROJECT_DIR}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "${PROJECT_DIR}/.env"
  set +a
fi

case "${1:-help}" in
  start)
    echo "🚀 Starting LinkedIn Hunter bridge worker..."
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "⚠️  Already running (PID: $(cat "$PID_FILE"))"
      exit 0
    fi
    cd "$PROJECT_DIR"
    nohup python3 "$BRIDGE_SCRIPT" > "${PROJECT_DIR}/output/bridge.log" 2>&1 &
    echo $! > "$PID_FILE"
    sleep 1
    if curl -s "http://127.0.0.1:${BRIDGE_PORT}/ping" | grep -q PONG; then
      echo "✅ Bridge worker started (PID: $(cat "$PID_FILE"))"
    else
      echo "⚠️  Process started but ping failed — check output/bridge.log"
    fi
    ;;

  stop)
    echo "🛑 Stopping LinkedIn Hunter bridge worker..."
    if [ -f "$PID_FILE" ]; then
      kill "$(cat "$PID_FILE")" 2>/dev/null || true
      rm -f "$PID_FILE"
      echo "✅ Stopped"
    else
      # Try to find by port
      PID=$(lsof -ti:${BRIDGE_PORT} 2>/dev/null || true)
      if [ -n "$PID" ]; then
        kill "$PID" 2>/dev/null || true
        echo "✅ Stopped (PID: $PID)"
      else
        echo "ℹ️  Not running"
      fi
    fi
    ;;

  restart)
    "$0" stop
    sleep 1
    "$0" start
    ;;

  status)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      PID=$(cat "$PID_FILE")
      echo "✅ Running (PID: $PID)"
      curl -s "http://127.0.0.1:${BRIDGE_PORT}/ping" | python3 -m json.tool 2>/dev/null || true
    else
      echo "❌ Not running"
    fi
    ;;

  ping)
    curl -s "http://127.0.0.1:${BRIDGE_PORT}/ping" | python3 -m json.tool
    ;;

  stats)
    curl -s -H "Authorization: Bearer ${BRIDGE_AUTH_TOKEN}" \
      "http://127.0.0.1:${BRIDGE_PORT}/stats" | python3 -m json.tool
    ;;

  install-systemd)
    echo "📦 Installing systemd service..."
    sudo cp "$SERVICE_FILE" /etc/systemd/system/${SERVICE_NAME}.service
    sudo systemctl daemon-reload
    sudo systemctl enable ${SERVICE_NAME}
    sudo systemctl start ${SERVICE_NAME}
    echo "✅ Service installed and started"
    ;;

  import-n8n)
    echo "📥 Importing n8n workflow..."
    docker cp "${PROJECT_DIR}/n8n/linkedin_hunter_workflow.json" n8n:/tmp/workflow.json
    docker exec n8n n8n import:workflow --input=/tmp/workflow.json
    echo "✅ Workflow imported — activate it from the n8n dashboard"
    ;;

  test)
    echo "🧪 Running end-to-end test..."
    curl -s -X POST "http://127.0.0.1:${BRIDGE_PORT}/tailor" \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer ${BRIDGE_AUTH_TOKEN}" \
      -d '{
        "external_id": "test-'$(date +%s)'",
        "job_title": "Customer Operations Lead",
        "company": "Test Company UAE",
        "location": "Abu Dhabi, UAE",
        "workplace_type": "Hybrid",
        "url": "https://www.linkedin.com/jobs/view/test-123",
        "description": "We are looking for a Customer Operations Lead to manage our customer service team in Abu Dhabi. You will lead a team of 15+ agents, optimize workflows, build automation pipelines, and ensure SLA compliance. Requirements: 5+ years customer operations experience, strong leadership skills, experience with process automation and workflow tools, multilingual preferred (English/Arabic). Nice to have: experience with AI/ML tools, cloud infrastructure knowledge, health insurance or financial services background."
      }' | python3 -m json.tool
    ;;

  help|*)
    echo "LinkedIn Hunter Bridge Manager"
    echo ""
    echo "Usage: $0 {start|stop|restart|status|ping|stats|test|install-systemd|import-n8n}"
    echo ""
    echo "  start            Start the bridge worker (background process)"
    echo "  stop             Stop the bridge worker"
    echo "  restart          Restart the bridge worker"
    echo "  status           Check if bridge is running + health check"
    echo "  ping             Quick ping test"
    echo "  stats            Show pipeline statistics"
    echo "  test             Run end-to-end test with sample job"
    echo "  install-systemd  Install as systemd service (requires sudo)"
    echo "  import-n8n       Import workflow into n8n container"
    ;;
esac
