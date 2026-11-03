#!/usr/bin/env bash
# Wippi Simulator Dashboard Runner (Powered by Kimi K3)
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Use existing venv python if present, else system python
if [ -f "$WORKSPACE_ROOT/OrchestrationBackend homework_v0/.venv/bin/python" ]; then
    PYTHON_EXEC="$WORKSPACE_ROOT/OrchestrationBackend homework_v0/.venv/bin/python"
    UVICORN_EXEC="$WORKSPACE_ROOT/OrchestrationBackend homework_v0/.venv/bin/uvicorn"
else
    PYTHON_EXEC="$(which python3)"
    UVICORN_EXEC="$(which uvicorn)"
fi

export AWS_BEARER_TOKEN_BEDROCK="${AWS_BEARER_TOKEN_BEDROCK:-REMOVED_ROTATED_BEDROCK_TOKEN"
export AWS_REGION="${AWS_REGION:-us-east-1}"
export KIMI_MODEL_ID="${KIMI_MODEL_ID:-us.moonshotai.kimi-k3}"
export PORT="${PORT:-8090}"
export HOST="${HOST:-0.0.0.0}"

echo "=========================================================="
echo " Starting Wippi Voice Agent Simulator Dashboard"
echo " Powered by: Moonshot AI Kimi K3 (${KIMI_MODEL_ID})"
echo " Target URL: http://localhost:${PORT}"
echo " Python:     ${PYTHON_EXEC}"
echo " WorkingDir: ${SCRIPT_DIR}"
echo "=========================================================="

cd "$SCRIPT_DIR"
exec "$UVICORN_EXEC" app:app --host "$HOST" --port "$PORT" --reload
