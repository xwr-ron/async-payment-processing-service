.PHONY: install lint format typecheck test check up down logs smoke

install:
	uv sync --all-groups

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check . --fix
	uv run ruff format .

typecheck:
	uv run mypy src

test:
	uv run pytest --cov=payment_service --cov-report=term-missing

check: lint typecheck test

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f api consumer

smoke:
	@set -a; \
	if [ -f .env ]; then . ./.env; fi; \
	set +a; \
	RUN_COMPOSE_INTEGRATION=1 uv run pytest tests/integration -m integration -q
