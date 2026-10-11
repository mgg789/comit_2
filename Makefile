COMPOSE := docker compose --env-file .env -f infra/docker-compose.yml
DEV_COMPOSE := COMIT_LOCAL_PROJECT=comit-dev ./infra/local-dev.sh

.PHONY: up down build logs smoke regression regression-golden-update clean dev-up dev-down dev-logs dev-models compose-check

## Start the production stack (builds images, publishes ports per .env)
up:
	$(COMPOSE) up -d --build

## Stop the stack while preserving all named volumes
down:
	$(COMPOSE) down

## Explicit local development stack with isolated volume namespace
dev-up:
	$(DEV_COMPOSE)

## Download the exact pinned public model into the isolated dev cache, then verify it
dev-models:
	$(DEV_COMPOSE) run --rm --build --no-deps model-cache-init

dev-down:
	$(DEV_COMPOSE) stop

dev-logs:
	$(DEV_COMPOSE) logs -f --tail=100

## Build all images without starting
build:
	$(COMPOSE) build

## Follow stack logs
logs:
	$(COMPOSE) logs -f --tail=100

## Smoke gate: wait for every service /healthz and key web routes (tests/smoke.sh)
smoke:
	./tests/smoke.sh

## Validate Compose modes, required secrets and migration ordering with synthetic configuration
compose-check:
	python3 tests/compose-config.py

## Full Python regression suite; export PIPELINE_TEST_DATABASE_URL to include PostgreSQL-backed tests
regression:
	pytest services/matching-engine/tests services/testing-engine/tests services/integration-engine/tests services/verification-engine/tests -q

## Regenerate the scoring golden snapshot after an intended scoring change (explain the diff in the commit)
regression-golden-update:
	UPDATE_GOLDEN=1 pytest services/matching-engine/tests/test_combine_regression.py -q

## Remove build artifacts of all services
clean:
	rm -rf apps/*/dist packages/*/dist apps/api/dist
	find services -name __pycache__ -type d -prune -exec rm -rf {} +
