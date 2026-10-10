#!/usr/bin/env bash
# Smoke gate: every Comit service must answer /healthz and the nginx routes
# must serve the three product apps. Used by CI (infra/docker-compose.yml with CI ports)
# and by the deploy workflow against the prod stack (docs/ci.md, docs/deploy.md).
#
# Checks run against STATIC html (curl does not execute js), so web expectations
# match the <title> of each app's index.html.
set -euo pipefail

WEB_PORT="${WEB_PORT:-8088}"
API_PORT="${API_PORT:-28080}"
MATCHING_PORT="${MATCHING_PORT:-28101}"
TESTING_PORT="${TESTING_PORT:-8102}"
INTEGRATION_PORT="${INTEGRATION_PORT:-8103}"
TRIES="${SMOKE_TRIES:-20}"
MODE="${SMOKE_MODE:-production}"
AGENTS_PORT="${AGENTS_PORT:-28104}"
VERIFICATION_PORT="${VERIFICATION_PORT:-28105}"
[[ "$MODE" == production || "$MODE" == dev ]] || { echo "invalid SMOKE_MODE"; exit 2; }
[[ "$TRIES" =~ ^[1-9][0-9]*$ && "$TRIES" -le 60 ]] || { echo "SMOKE_TRIES must be 1..60"; exit 2; }

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
    sleep 1
  done
  echo "FAIL $name  $url  (expected response unavailable)"
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
    sleep 1
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
if [[ -n "${VERIFICATION_PORT:-}" ]]; then
  check "verification" "http://localhost:${VERIFICATION_PORT}/healthz" '"service":"verification-engine"' || failures=$((failures + 1))
fi
check "web root"      "http://localhost:${WEB_PORT}/"                 "работа настигает сама" || failures=$((failures + 1))
check "web candidate" "http://localhost:${WEB_PORT}/candidate/"       "кабинет кандидата" || failures=$((failures + 1))
check "web employer"  "http://localhost:${WEB_PORT}/employer/"        "кабинет работодателя" || failures=$((failures + 1))
check_status "testing UI excluded" "http://localhost:${WEB_PORT}/testing/" 404 || failures=$((failures + 1))
check_status "internal API hidden" "http://localhost:${WEB_PORT}/api/internal/strict-schema" 404 || failures=$((failures + 1))
check "api via nginx" "http://localhost:${WEB_PORT}/api/healthz"      '"service":"api-core"' || failures=$((failures + 1))
check "blueprints"    "http://localhost:${TESTING_PORT}/blueprints"   "algo-hash-lookup" || failures=$((failures + 1))
check "api ready" "http://localhost:${API_PORT}/ready" '"service":"api-core"' || failures=$((failures + 1))
check "matching DB/model ready" "http://localhost:${MATCHING_PORT}/ready" '"status":"ok"' || failures=$((failures + 1))
check_status "candidate auth boundary" "http://localhost:${WEB_PORT}/api/auth/candidate/me" 401 || failures=$((failures + 1))
check_status "employer auth boundary" "http://localhost:${WEB_PORT}/api/auth/employer/me" 401 || failures=$((failures + 1))
# The public showcase joins actual matching storage; an empty pool is valid.
check "candidate showcase" "http://localhost:${WEB_PORT}/api/public/showcase/candidates" '"items"' || failures=$((failures + 1))
check "vacancy showcase" "http://localhost:${WEB_PORT}/api/public/showcase/vacancies" '"items"' || failures=$((failures + 1))
if [[ "$MODE" == dev ]]; then
  check_status "fsp dev 404" "http://localhost:${INTEGRATION_PORT}/fsp/participants/fsp-none" 404 || failures=$((failures + 1))
  check_status "fsp dev 200" "http://localhost:${INTEGRATION_PORT}/fsp/participants/fsp-001" 200 || failures=$((failures + 1))
else
  check_status "fsp fixtures disabled" "http://localhost:${INTEGRATION_PORT}/fsp/participants/fsp-001" 503 || failures=$((failures + 1))
fi

if [[ "$failures" -gt 0 ]]; then
  echo "smoke: ${failures} failure(s)"
  exit 1
fi
echo "smoke: all green"
