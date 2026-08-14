.DEFAULT_GOAL := help
.PHONY: help run db migrate migration test test-integration lint typecheck build check

help: ## List the available targets
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

run: db migrate ## Dev environment: postgres, migrations, backend and frontend together
	@cd frontend && npm run --silent dev:all

db: ## Start the dev postgres on 127.0.0.1:5433 and wait for it
	@docker compose -f docker-compose.dev.yml up -d --wait

migrate: ## Apply migrations to the dev database
	@cd backend && uv run alembic upgrade head

migration: ## Autogenerate a migration: make migration m="add widgets"
	@cd backend && uv run alembic revision --autogenerate -m "$(m)"

test: ## Pure backend suite, no database
	@cd backend && uv run pytest

test-integration: ## Integration suite against a throwaway postgres
	@scripts/test-integration.sh

lint: ## Ruff over the backend
	@cd backend && uv run ruff check .

typecheck: ## vue-tsc over the frontend
	@cd frontend && npm run typecheck

build: ## Build the frontend (type-checks as it goes)
	@cd frontend && npm run build

check: lint test typecheck ## Everything a commit should pass
