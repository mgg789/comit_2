#!/usr/bin/env bash
# Smoke gate: every Comit service must answer /healthz and the nginx routes
# must serve the four apps. Used by CI (infra/docker-compose.yml with CI ports)
# and by the deploy workflow against the prod stack (docs/ci.md, docs/deploy.md).
#
# Checks run against STATIC html (curl does not execute js), so web expectations
# match the <title> of each app's index.html.
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

# url must respond with the given http status (e.g. the mandatory 404 for
# candidates without FSP history — it is a normal state, not an error).
check_status() {
  local name="$1" url="$2" want="$3"
  local got=""
  for _ in $(seq 1 "$TRIES"); do
    if got="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$url" 2>/dev/null)" && [[ "$got" == "$want" ]]; then
      echo "ok   $name  $url  ($got)"
      return 0
    fi
    sleep 2
  done
  echo "FAIL $name  $url  (want $want, got ${got:-none})"
  return 1
}

check "api"           "http://localhost:${API_PORT}/healthz"          '"service":"api-core"' || failures=$((failures + 1))
check "matching"      "http://localhost:${MATCHING_PORT}/healthz"     '"service":"matching-engine"' || failures=$((failures + 1))
if [[ -n "${AGENTS_PORT:-}" ]]; then
  check "agents" "http://localhost:${AGENTS_PORT}/healthz" '"service":"agents-service"' || failures=$((failures + 1))
  check "agents DB ready" "http://localhost:${AGENTS_PORT}/ready" '"status":"ready"' || failures=$((failures + 1))
fi
check "testing"       "http://localhost:${TESTING_PORT}/healthz"      '"service":"testing-engine"' || failures=$((failures + 1))
check "integration"   "http://localhost:${INTEGRATION_PORT}/healthz"  '"service":"integration-engine"' || failures=$((failures + 1))
check "web root"      "http://localhost:${WEB_PORT}/"                 "работа настигает сама" || failures=$((failures + 1))
check "web candidate" "http://localhost:${WEB_PORT}/candidate/"       "кабинет кандидата" || failures=$((failures + 1))
check "web employer"  "http://localhost:${WEB_PORT}/employer/"        "кабинет работодателя" || failures=$((failures + 1))
check "web testing"   "http://localhost:${WEB_PORT}/testing/"         "сервис тестирования" || failures=$((failures + 1))
check "api via nginx" "http://localhost:${WEB_PORT}/api/healthz"      '"service":"api-core"' || failures=$((failures + 1))
check "blueprints"    "http://localhost:${TESTING_PORT}/blueprints"   "algo-hash-lookup" || failures=$((failures + 1))
check_status "fsp mock 404" "http://localhost:${INTEGRATION_PORT}/fsp/participants/fsp-none" 404 || failures=$((failures + 1))
check_status "fsp mock 200" "http://localhost:${INTEGRATION_PORT}/fsp/participants/fsp-001" 200 || failures=$((failures + 1))

if [[ "$failures" -gt 0 ]]; then
  echo "smoke: ${failures} failure(s)"
  exit 1
fi
echo "smoke: all green"
