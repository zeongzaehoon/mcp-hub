#!/usr/bin/env bash
# Isolated e2e: brings up a self-contained throwaway compose project (deploy/docker/docker-compose.e2e.yml, its own DynamoDB/MinIO/
# Redis/DBs), runs the tests against it, then removes it with its volumes, pass or fail. The dev stack is untouched.
#   scripts/e2e.sh mcp   MCP servers + gateway (host-side pytest)
#   scripts/e2e.sh ui    admin console (Playwright container)
#   E2E_KEEP=1 ...       leave the stack up for debugging (then: scripts/e2e.sh down)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export COMPOSE_PROJECT_NAME=mcp-cluster-e2e
export COMPOSE_FILE="$ROOT/deploy/docker/docker-compose.yml:$ROOT/deploy/docker/docker-compose.e2e.yml"

teardown() {
  docker compose --profile e2e down -v --remove-orphans >/dev/null 2>&1 || true
}

on_exit() {
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "e2e: failed (exit $rc); last logs:" >&2
    docker compose logs --tail 200 >&2 || true
  fi
  if [ "${E2E_KEEP:-0}" = "1" ]; then
    echo "e2e: E2E_KEEP=1, stack left up (project $COMPOSE_PROJECT_NAME)" >&2
  else
    teardown
  fi
  exit "$rc"
}

mode="${1:-mcp}"
if [ "$mode" = "down" ]; then
  teardown
  exit 0
fi

teardown  # leftovers from an interrupted run (same fixed ports / project)
trap on_exit EXIT

docker compose up -d --build --wait
for _ in $(seq 1 60); do curl -fsS http://localhost:29000/healthz >/dev/null 2>&1 && break; sleep 2; done
curl -fsS http://localhost:29000/healthz >/dev/null

case "$mode" in
  mcp)
    # everything goes through the nginx gateway (29000): /api/v1, /mcp (mcp-gateway), /mcp-<type> (direct servers)
    export E2E_BACKEND=http://localhost:29000/api/v1
    export E2E_GATEWAY=http://localhost:29000/mcp
    export E2E_MCP_PG=http://localhost:29000/mcp-postgres
    export E2E_MCP_CH=http://localhost:29000/mcp-clickhouse
    export E2E_PG_ADMIN_DSN=postgresql://mcp:mcp-dev-password@localhost:65432/sample
    export E2E_S3_PUBLIC=http://localhost:29090
    # postgres first so its server is registered; the gateway last (it uses every server)
    for name in mcp-postgres mcp-clickhouse mcp-mongo mcp-hacker mcp-gateway; do
      files=$(cd "$name" && ls tests/test_e2e.py tests/test_generic.py tests/test_client_guide.py tests/test_inspector_compat.py tests/test_oauth.py 2>/dev/null || true)
      [ -n "$files" ] || continue
      echo "== $name"
      # shellcheck disable=SC2086
      (cd "$name" && E2E_MCP="http://localhost:29000/$name" uv run pytest -q -p no:warnings -rfE --tb=short $files)
    done
    ;;
  ui)
    docker compose --profile e2e run --rm --build frontend-e2e
    ;;
  *)
    echo "usage: scripts/e2e.sh mcp|ui|down" >&2
    exit 2
    ;;
esac
