#!/usr/bin/env bash
# Ejecuta los 5 microservicios + Redis en la máquina local (sin Docker).
# Uso: ./scripts/run_local.sh      (Ctrl+C para detener todo)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export PYTHONPATH="common:services/catalog:services/orders:services/payments:services/notifications:services/gateway"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
export INTERNAL_TOKEN="${INTERNAL_TOKEN:-local-internal-token}"
export GATEWAY_API_KEY="${GATEWAY_API_KEY:-frescoruta-demo-key}"
export APP_ENV="${APP_ENV:-local}"

if command -v redis-cli >/dev/null && ! redis-cli -u "$REDIS_URL" ping >/dev/null 2>&1; then
  if command -v redis-server >/dev/null; then
    echo ">> Iniciando redis-server local"
    redis-server --save "" --appendonly no --daemonize yes >/dev/null
  else
    echo "!! Redis no está disponible: los eventos no viajarán entre procesos." >&2
  fi
fi

PIDS=()
start() {  # nombre módulo puerto
  echo ">> $1 en http://localhost:$3"
  PORT="$3" SERVICE_URL="http://localhost:$3" \
    python -m uvicorn "$2" --host 0.0.0.0 --port "$3" --log-level warning &
  PIDS+=($!)
}

trap 'echo; echo ">> Deteniendo servicios"; kill "${PIDS[@]}" 2>/dev/null || true' EXIT INT TERM

start catalog-service      catalog_service.main:app      8001
start order-service        order_service.main:app        8002
start payment-service      payment_service.main:app      8003
start notification-service notification_service.main:app 8004
start api-gateway          gateway_service.main:app      8000

echo ">> Panel: http://localhost:8000   ·   OpenAPI: http://localhost:8000/docs"
wait
