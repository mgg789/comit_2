#!/usr/bin/env bash
# Smoke gate: every Comit service must answer /healthz and the nginx routes
# must serve the four apps. Used by CI (infra/docker-compose.yml with CI ports)
# and by the deploy workflow against the prod stack (docs/ci.md, docs/deploy.md).
set -euo pipefail

WEB_PORT="${WEB_PORT:-8088}"
API_PORT="${API_PORT:-8080}"
MATCHING_PORT="${MATCHING_PORT:-8101}"
TESTING_PORT="${TESTING_PORT:-8102}"
INTEGRATION_PORT="${INTEGRATION_PORT:-8103}"
TRIES="${SMOKE_TRIES:-60}"

failures=0

check() {
  local name="$1" url="$2" expect="${3:-}"
  local body=""
  for _ in $(seq 1 "$TRIES"); do
    if body="$(curl -fsS --max-time 3 "$url" 2>/dev/null)"; then
      if [[ -z "$expect" || "$body" == *"$expect"* ]]; then
        echo "ok   $name  $url"
        return 0
      fi
    fi
    sleep 2
  done
  echo "FAIL $name  $url  (last body: ${body:-<empty>})"
  return 1
}

check "api"          "http://localhost:${API_PORT}/healthz"        '"service":"api-core"'
check "matching"     "http://localhost:${MATCHING_PORT}/healthz"   '"service":"matching-engine"'
check "testing"      "http://localhost:${TESTING_PORT}/healthz"    '"service":"testing-engine"'
check "integration"  "http://localhost:${INTEGRATION_PORT}/healthz" '"service":"integration-engine"'
check "web root"     "http://localhost:${WEB_PORT}/"               "Comit"
check "web candidate" "http://localhost:${WEB_PORT}/candidate/"    "Кабинет кандидата"
check "web employer" "http://localhost:${WEB_PORT}/employer/"      "Кабинет работодателя"
check "web testing"  "http://localhost:${WEB_PORT}/testing/"       "Сервис тестирования"
check "api via nginx" "http://localhost:${WEB_PORT}/api/healthz"   '"service":"api-core"'
check "blueprints"   "http://localhost:${TESTING_PORT}/blueprints" "algo-hash-lookup"
check "fsp mock 404" "http://localhost:${INTEGRATION_PORT}/fsp/participants/fsp-none" ""

if [[ "$failures" -gt 0 ]]; then
  echo "smoke: ${failures} failure(s)"
  exit 1
fi
echo "smoke: all green"
