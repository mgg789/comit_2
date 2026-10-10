#!/usr/bin/env bash
# Real nginx normalization regression; disposable container, no published ports or live-service changes.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
docker run --rm --pull=never --add-host api:127.0.0.1 --entrypoint sh \
  -v "$repo_root/infra/nginx/nginx.conf:/etc/nginx/conf.d/default.conf:ro" \
  "${NGINX_TEST_IMAGE:-nginx:1.27-alpine}" -ec '
    printf "server { listen 8080; location = /healthz { return 200; } }\n" > /etc/nginx/conf.d/upstream.conf
    nginx -t
    nginx >/dev/null 2>&1
    for path in /api/internal /api/internal/ /api/internal/vacancies/123/combine \
      /api/internal%2fpool //api/internal/pool /api/public/../internal/pool /api/internal?probe=1; do
      result=$(wget -S -O /dev/null "http://127.0.0.1$path" 2>&1 || true)
      printf "%s\n" "$result" | grep -q "HTTP/1.1 404" || { printf "%s\n" "$result"; exit 1; }
    done
    wget -q -O /dev/null http://127.0.0.1/healthz
    wget -q -O /dev/null http://127.0.0.1/api/healthz
    echo "PASS nginx internal exact/prefix/encoded/normalized deny and public API proxy control"
  '
