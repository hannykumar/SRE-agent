#!/usr/bin/env bash
set -euo pipefail

export DATABASE_URL="${DATABASE_URL:-sqlite:///./ops.db}"
export EXECUTOR_BASE_URL="${EXECUTOR_BASE_URL:-http://127.0.0.1:8091}"
export OPS_API_TOKENS="${OPS_API_TOKENS:-}"
export SRE_DEFAULT_API_TOKEN="${SRE_DEFAULT_API_TOKEN:-}"
export SRE_DEFAULT_TOOL_TRANSPORT="${SRE_DEFAULT_TOOL_TRANSPORT:-mcp}"
export SRE_MCP_BACKEND="${SRE_MCP_BACKEND:-mock}"
export GRAFANA_WEBHOOK_TOKEN="${GRAFANA_WEBHOOK_TOKEN:-}"
export GITOPS_REPO_DIR="${GITOPS_REPO_DIR:-data/gitops_repo}"

# Start a local executor only when the configured executor points back to this machine.
EXECUTOR_PID=""
if [[ "$EXECUTOR_BASE_URL" == "http://127.0.0.1:8091" || "$EXECUTOR_BASE_URL" == "http://localhost:8091" ]]; then
  PYTHONPATH=. uvicorn executor.api:app --host 0.0.0.0 --port 8091 &
  EXECUTOR_PID=$!
fi

PYTHONPATH=. uvicorn ops.api:app --host 0.0.0.0 --port 8090 &
API_PID=$!

cleanup() {
  if [[ -n "$EXECUTOR_PID" ]]; then
    kill "$EXECUTOR_PID" >/dev/null 2>&1 || true
  fi
  kill "$API_PID" >/dev/null 2>&1 || true
}

trap cleanup EXIT

PYTHONPATH=. streamlit run ui/app.py --server.address 0.0.0.0 --server.port 8501
