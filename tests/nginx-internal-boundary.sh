#!/usr/bin/env bash
# Real nginx normalization regression; disposable container, no published ports or live-service changes.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
docker run --rm --pull=never --add-host api:127.0.0.1 --entrypoint sh \
  -v "$repo_root/infra/nginx/nginx.conf:/etc/nginx/conf.d/default.conf:ro" \
  "${NGINX_TEST_IMAGE:-nginx:1.27-alpine}" -ec '
    printf "server { listen 8080; location = /healthz { return 200; } location = /metrics { return 200 \"private-metrics\"; } }\n" > /etc/nginx/conf.d/upstream.conf
    # Explicit server logging avoids relying on image-global access_log defaults.
    sed "s@listen 80;@listen 80; access_log /tmp/oauth-access.log combined;@" /etc/nginx/conf.d/default.conf > /tmp/probe.conf
    sed -i "s@include /etc/nginx/conf.d/\\*.conf;@include /tmp/probe.conf; include /etc/nginx/conf.d/upstream.conf;@" /etc/nginx/nginx.conf
    nginx -t
    nginx >/dev/null 2>&1
    for path in /api/internal /api/internal/ /api/internal/vacancies/123/combine \
      /api/internal%2fpool //api/internal/pool /api/public/../internal/pool /api/internal?probe=1 \
      /api/metrics /api/metrics/ /api/metrics?probe=1 //api/metrics /api%2fmetrics; do
      result=$(wget -S -O /dev/null "http://127.0.0.1$path" 2>&1 || true)
      printf "%s\n" "$result" | grep -q "HTTP/1.1 404" || { printf "%s\n" "$result"; exit 1; }
    done
    wget -q -O /dev/null http://127.0.0.1/healthz
    wget -q -O /dev/null http://127.0.0.1/api/healthz
    response=$(wget -S -O /dev/null http://127.0.0.1/api/healthz 2>&1)
    for header in X-Content-Type-Options X-Frame-Options Referrer-Policy Content-Security-Policy; do
      printf "%s\n" "$response" | grep -qi "$header:" || exit 1
    done
    printf "%s\n" "$response" | grep -q "wasm-unsafe-eval" || exit 1
    wget -q -O - http://127.0.0.1:8080/metrics | grep -q private-metrics
    echo "PASS security headers and public metrics deny with private scrape control"
    for role in candidate employer; do
      wget -q -O /dev/null "http://127.0.0.1/api/auth/$role/yandex/callback?code=synthetic-sensitive-code&state=synthetic-sensitive-state" || true
    done
    # Graceful shutdown flushes worker logs before checking under a busy Docker VM.
    nginx -s quit
    for attempt in 1 2 3 4 5; do
      [ ! -e /var/run/nginx.pid ] && break
      sleep 1
    done
    # Ordinary API access remains observable; callback credentials must be absent.
    grep -q /api/healthz /tmp/oauth-access.log || { cat /tmp/oauth-access.log; exit 1; }
    if grep -q "synthetic-sensitive" /tmp/oauth-access.log; then exit 1; fi
    echo "PASS OAuth callback queries excluded from access logs"
    echo "PASS nginx internal exact/prefix/encoded/normalized deny and public API proxy control"
  '
