.PHONY: install dev ui lint format type test cov e2e eval security db sources train serve mcp docker up down check clean

install:      ## runtime install
	pip install -e .
dev:          ## dev install (+ CPU torch for the fine-tuning research tests, + Playwright for browser e2e)
	pip install -e ".[dev]" playwright && pip install torch --index-url https://download.pytorch.org/whl/cpu
ui:           ## build the analyst dashboard (served by the API at /)
	cd dashboard && npm ci --no-audit --no-fund && npm run build
lint:
	ruff check src tests scripts && ruff format --check src tests scripts
format:
	ruff check src tests scripts --fix && ruff format src tests scripts
type:
	python -m mypy src scripts && python scripts/gen_config_docs.py --check && cd dashboard && npx tsc -b --noEmit
test:
	pytest
cov:
	pytest --cov=sentinel --cov-report=term-missing --cov-report=html
e2e:          ## browser e2e only (needs `make ui` and `python -m playwright install chromium`)
	pytest tests/e2e/test_dashboard.py
eval:
	sentinel eval
security:
	bandit -r src scripts -ll -q && pip-audit --progress-spinner off --skip-editable && cd dashboard && npm audit --omit=dev --audit-level=high
db:
	sentinel db upgrade
sources:      ## download the official watch lists (live)
	sentinel sources refresh --all
train:        ## train + gate + register the peer model on SEC XBRL frames (live)
	sentinel ml train --year 2024
serve:
	uvicorn sentinel.api.main:create_app --factory --port 8000
mcp:
	sentinel-mcp
docker:
	docker build -t sentinel-dd:2.0.0 .
up:
	docker compose up -d --build
down:
	docker compose down
check: lint type cov eval security
clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml dist build dashboard/dist
