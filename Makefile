.PHONY: dev down reset test lint typecheck prod prod-down migrate ml-train

dev:
	docker compose up --build

down:
	docker compose down

reset:
	docker compose down --volumes --remove-orphans

test:
	docker compose run --rm backend pytest
	docker compose run --rm frontend npm run test:run

lint:
	docker compose run --rm backend ruff check .
	docker compose run --rm frontend npm run lint

typecheck:
	docker compose run --rm backend mypy app
	docker compose run --rm frontend npm run typecheck

migrate:
	docker compose run --rm backend alembic upgrade head

prod:
	docker compose -f compose.prod.yml up --build -d

prod-down:
	docker compose -f compose.prod.yml down

ml-train:
	docker compose -f compose.ml.yml run --build --rm ml

