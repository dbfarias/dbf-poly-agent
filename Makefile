.PHONY: install test lint format dev frontend build docker-dev

## Install all dependencies (Python + frontend)
install:
	uv sync --all-extras
	cd frontend && npm install

## Run all tests
test:
	uv run pytest tests/ -v --tb=short

## Run linter
lint:
	uv run ruff check bot/ api/

## Auto-format code
format:
	uv run ruff format bot/ api/

## Run bot locally in paper mode
dev:
	uv run uvicorn api.main:app --reload

## Run frontend dev server
frontend:
	cd frontend && npm run dev

## Build Docker images locally
build:
	docker compose build

## Run full stack in Docker (dev mode, local builds)
docker-dev:
	docker compose up --build
