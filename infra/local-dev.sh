#!/bin/sh
# Reproducible isolated local stack; preserve the caller's env file and named volumes.
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
env_file=/dev/null
judge=false
while [ "$#" -gt 0 ]; do
  case "$1" in
    --env-file) env_file=$2; shift 2 ;;
    --with-judge) judge=true; shift ;;
    *) break ;;
  esac
done
project=${COMIT_LOCAL_PROJECT:-comit-local}
case "$project" in comit-local|comit-local-*|comit-prerelease|comit-dev) ;; *) echo "Use an isolated comit-local[-name] comit-dev or comit-prerelease project" >&2; exit 2 ;; esac
if [ "$#" -eq 0 ]; then set -- up -d --build --wait --wait-timeout 600; fi
if [ "$judge" = true ]; then
  exec docker compose --project-name "$project" --env-file "$env_file" -f "$repo/infra/docker-compose.dev.yml" -f "$repo/infra/docker-compose.local.yml" -f "$repo/infra/docker-compose.local-judge.yml" --profile dev "$@"
fi
# Default local mode always disables testing, even with judge credentials in the env file.
export TESTING_ENABLED=false
exec docker compose --project-name "$project" --env-file "$env_file" -f "$repo/infra/docker-compose.dev.yml" -f "$repo/infra/docker-compose.local.yml" --profile dev "$@"
