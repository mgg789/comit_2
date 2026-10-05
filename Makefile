COMPOSE := docker compose -f infra/docker-compose.yml

.PHONY: up down build logs smoke clean

## Start the dev stack (builds images, publishes ports per .env)
up:
	$(COMPOSE) up -d --build

## Stop the stack and drop volumes (fresh DB on next up)
down:
	$(COMPOSE) down -v --remove-orphans

## Build all images without starting
build:
	$(COMPOSE) build

## Follow stack logs
logs:
	$(COMPOSE) logs -f --tail=100

## Smoke gate: wait for every service /healthz and key web routes (tests/smoke.sh)
smoke:
	./tests/smoke.sh

## Remove build artifacts of all services
clean:
	rm -rf apps/*/dist packages/*/dist apps/api/dist
	find services -name __pycache__ -type d -prune -exec rm -rf {} +
