.PHONY: install dev lint format type test cov eval train demo api mcp docker up down check clean

install:      ## runtime install
	pip install -e .
dev:          ## dev install (+ CPU torch for fine-tuning tests)
	pip install -e ".[dev]" && pip install torch --index-url https://download.pytorch.org/whl/cpu
lint:
	ruff check src tests && ruff format --check src tests
format:
	ruff check src tests --fix && ruff format src tests
type:
	mypy src
test:
	pytest
cov:
	pytest --cov=sentinel --cov-report=term-missing --cov-report=html
eval:
	python scripts/evaluate.py
train:
	python -m sentinel.ml.train --model-dir var/models
demo:
	python scripts/demo.py
api:
	uvicorn sentinel.api.main:create_app --factory --reload --port 8000
mcp:
	sentinel-mcp
docker:
	docker build -t sentinel-dd:local .
up:
	docker compose up --build -d
down:
	docker compose down
check: lint type cov eval
clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml var dist build
