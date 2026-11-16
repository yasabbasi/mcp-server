.DEFAULT_GOAL := help
.PHONY: help install check lint test format sync-samples

VERSION ?= v1
PIPELINE ?= ../data-pipeline
YAML_RULES = {document-start: disable, line-length: disable, truthy: {check-keys: false}, \
	comments: {min-spaces-from-content: 1}, indentation: {spaces: 2}}

help: ## List the targets
	@awk -F ':.*## ' '/^[a-z-]+:.*## / {printf "%-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install the locked dependencies
	uv sync --locked

check: lint test ## Lint and test; run it before you finish

lint: ## Check style, types, Markdown, YAML and JSON
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	uv run pymarkdown scan --recurse --exclude .venv --exclude .worktrees .
	uv run yamllint --strict -d '{extends: default, rules: $(YAML_RULES)}' .github
	uv run python scripts/lint_json.py

test: ## Run the tests, which use the copied samples and no network
	uv run pytest

format: ## Fix style and formatting
	uv run ruff format .
	uv run ruff check --fix .

sync-samples: ## Copy VERSION's samples and contract doc from PIPELINE's contract tag
	uv run python scripts/sync_samples.py $(PIPELINE) $(VERSION)
