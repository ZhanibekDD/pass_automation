#!/usr/bin/env bash
# Проверка работоспособности Pass Docs AI.
# Использование: ./scripts/healthcheck.sh [host:port]
set -euo pipefail

HOST="${1:-127.0.0.1:8090}"
BASE="http://${HOST}"

echo "=== Pass Docs AI Health Check ==="
echo "Host: $BASE"
echo ""

# 1. Health endpoint
echo "--- /api/ai/health ---"
curl -sf "${BASE}/api/ai/health" | python3 -m json.tool 2>/dev/null || curl -sf "${BASE}/api/ai/health"
echo ""

# 2. GPU status (хост)
echo "--- GPU Status ---"
if command -v nvidia-smi &>/dev/null; then
    nvidia-smi --query-gpu=name,memory.used,memory.free,memory.total,utilization.gpu \
        --format=csv,noheader
else
    echo "nvidia-smi не найден"
fi
echo ""

# 3. Docker containers
echo "--- Docker Containers ---"
docker ps --filter "name=pass_automation" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || echo "Docker недоступен"
echo ""

echo "=== Done ==="
