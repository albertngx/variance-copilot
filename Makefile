.DEFAULT_GOAL := help
SHELL := /bin/bash

UV ?= uv
PROFILE ?= dev
SEED ?= 20261010
MODE ?= replay

.PHONY: help up down lint format test test-unit coverage-gate migrate seed corpus eval

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-14s %s\n", $$1, $$2}'

up: ## Start PostgreSQL + pgvector, OpenTelemetry collector and Jaeger
	docker compose up -d --wait

down: ## Stop the local stack (data volume kept)
	docker compose down

lint: ## Ruff lint, format check and mypy --strict
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run mypy

format: ## Apply Ruff fixes and formatting
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

test: ## Unit, property and integration tests with coverage (needs Docker)
	$(UV) run pytest --cov --cov-report=term --cov-report=json
	$(UV) run python scripts/coverage_gate.py

test-unit: ## Unit and property tests only (no Docker)
	$(UV) run pytest -m "unit or property"

migrate: ## Apply database migrations
	@echo "not implemented (M1)" >&2; exit 1

seed: ## Generate and load synthetic data (PROFILE, SEED)
	@echo "not implemented (M1)" >&2; exit 1

corpus: ## Render memos and ingest the corpus
	@echo "not implemented (M4)" >&2; exit 1

eval: ## Run the evaluation suite (MODE)
	@echo "not implemented (M7)" >&2; exit 1
