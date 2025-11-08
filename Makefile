# Django Rust Live - Makefile
# Default port for development server
PORT ?= 8002
HOST ?= 0.0.0.0

# Colors for output
BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m # No Color

.DEFAULT_GOAL := help

##@ Help

.PHONY: help
help: ## Display this help message
	@echo "$(BLUE)Django Rust Live - Development Commands$(NC)"
	@echo ""
	@awk 'BEGIN {FS = ":.*##"; printf "Usage:\n  make $(YELLOW)<target>$(NC)\n"} /^[a-zA-Z_0-9-]+:.*?##/ { printf "  $(GREEN)%-15s$(NC) %s\n", $$1, $$2 } /^##@/ { printf "\n$(BLUE)%s$(NC)\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

##@ Development Server

.PHONY: start
start: ## Start the Django development server with hot reload
	@echo "$(GREEN)Starting Django Rust Live development server on $(HOST):$(PORT)...$(NC)"
	@cd examples/demo_project && \
		uv run uvicorn demo_project.asgi:application \
			--host $(HOST) \
			--port $(PORT) \
			--log-level info \
			--reload \
			--reload-include '*.html' \
			--reload-include '*.py'

.PHONY: start-bg
start-bg: stop ## Start server in background (stops existing servers first)
	@echo "$(GREEN)Starting server in background on $(HOST):$(PORT)...$(NC)"
	@(cd examples/demo_project && \
		nohup uv run uvicorn demo_project.asgi:application \
			--host $(HOST) \
			--port $(PORT) \
			--log-level info \
			--reload \
			--reload-include '*.html' \
			--reload-include '*.py' \
			> server.log 2>&1 & echo $$! > server.pid; \
		sleep 1; \
		if [ -f server.pid ]; then \
			echo "$(GREEN)Server started with PID: $$(cat server.pid)$(NC)"; \
		else \
			echo "$(GREEN)Server started$(NC)"; \
		fi; \
		echo "$(YELLOW)Logs: $$(pwd)/server.log$(NC)")

.PHONY: stop
stop: ## Stop the development server
	@echo "$(YELLOW)Stopping development server on port $(PORT)...$(NC)"
	@if lsof -ti:$(PORT) > /dev/null 2>&1; then \
		lsof -ti:$(PORT) | xargs kill -9 2>/dev/null || true; \
		echo "$(GREEN)Server stopped$(NC)"; \
	else \
		echo "$(YELLOW)No server running on port $(PORT)$(NC)"; \
	fi
	@if [ -f examples/demo_project/server.pid ]; then \
		rm examples/demo_project/server.pid; \
	fi

.PHONY: restart
restart: stop start ## Restart the development server

.PHONY: status
status: ## Check if the development server is running
	@if lsof -ti:$(PORT) > /dev/null 2>&1; then \
		echo "$(GREEN)Server is running on port $(PORT)$(NC)"; \
		lsof -i:$(PORT) | grep LISTEN; \
	else \
		echo "$(RED)No server running on port $(PORT)$(NC)"; \
	fi

.PHONY: logs
logs: ## Tail server logs (for background server)
	@if [ -f examples/demo_project/server.log ]; then \
		tail -f examples/demo_project/server.log; \
	else \
		echo "$(RED)No log file found. Is the server running in background?$(NC)"; \
	fi

##@ Setup & Installation

.PHONY: install
install: ## Install Python and Rust dependencies
	@echo "$(GREEN)Installing dependencies with uv...$(NC)"
	@uv sync --extra dev
	@echo "$(GREEN)Building Rust extensions...$(NC)"
	@uv run maturin develop --release
	@echo "$(GREEN)Installation complete!$(NC)"

.PHONY: install-quick
install-quick: ## Quick install without rebuilding Rust
	@echo "$(GREEN)Installing Python dependencies only...$(NC)"
	@uv sync --extra dev

.PHONY: build
build: ## Build Rust extensions in release mode
	@echo "$(GREEN)Building Rust extensions (release mode)...$(NC)"
	@uv run maturin develop --release

.PHONY: dev-build
dev-build: ## Build Rust extensions in development mode
	@echo "$(GREEN)Building Rust extensions (dev mode)...$(NC)"
	@uv run maturin develop

##@ Testing & Quality

.PHONY: test
test: ## Run all tests
	@echo "$(GREEN)Running tests...$(NC)"
	@PYTHONPATH=. uv run pytest

.PHONY: test-rust
test-rust: ## Run Rust tests
	@echo "$(GREEN)Running Rust tests...$(NC)"
	@cargo test

.PHONY: test-python
test-python: ## Run Python tests
	@echo "$(GREEN)Running Python tests...$(NC)"
	@PYTHONPATH=. uv run pytest python/

.PHONY: lint
lint: ## Run linters
	@echo "$(GREEN)Running linters...$(NC)"
	@uv run ruff check python/
	@cargo clippy

.PHONY: format
format: ## Format code
	@echo "$(GREEN)Formatting code...$(NC)"
	@uv run ruff format python/
	@cargo fmt

.PHONY: check
check: lint test ## Run linters and tests

##@ Database

.PHONY: migrate
migrate: ## Run Django migrations
	@echo "$(GREEN)Running migrations...$(NC)"
	@cd examples/demo_project && uv run python manage.py migrate

.PHONY: migrations
migrations: ## Create new Django migrations
	@echo "$(GREEN)Creating migrations...$(NC)"
	@cd examples/demo_project && uv run python manage.py makemigrations

.PHONY: db-reset
db-reset: ## Reset database (WARNING: destroys all data)
	@echo "$(RED)WARNING: This will destroy all data!$(NC)"
	@read -p "Are you sure? [y/N] " -n 1 -r; \
	echo; \
	if [[ $$REPLY =~ ^[Yy]$$ ]]; then \
		rm -f examples/demo_project/db.sqlite3; \
		$(MAKE) migrate; \
	fi

##@ Cleaning

.PHONY: clean
clean: ## Remove build artifacts
	@echo "$(YELLOW)Cleaning build artifacts...$(NC)"
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name '*.pyc' -delete 2>/dev/null || true
	@find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	@rm -rf build/ dist/ target/ .pytest_cache/ .ruff_cache/ 2>/dev/null || true
	@rm -f examples/demo_project/server.log examples/demo_project/server.pid 2>/dev/null || true
	@echo "$(GREEN)Clean complete!$(NC)"

.PHONY: clean-all
clean-all: clean ## Remove all generated files including venv
	@echo "$(YELLOW)Removing virtual environment...$(NC)"
	@rm -rf .venv
	@echo "$(GREEN)Deep clean complete!$(NC)"

##@ Utilities

.PHONY: shell
shell: ## Open Django shell
	@cd examples/demo_project && uv run python manage.py shell

.PHONY: urls
urls: ## Show all URL patterns
	@cd examples/demo_project && uv run python manage.py show_urls 2>/dev/null || uv run python manage.py shell -c "from django.urls import get_resolver; print('\\n'.join(str(p) for p in get_resolver().url_patterns))"

.PHONY: open
open: ## Open the application in browser
	@if lsof -ti:$(PORT) > /dev/null 2>&1; then \
		open http://localhost:$(PORT); \
	else \
		echo "$(RED)Server is not running. Start it with 'make start'$(NC)"; \
	fi

.PHONY: info
info: ## Show project information
	@echo "$(BLUE)Django Rust Live - Project Information$(NC)"
	@echo "Server URL:    http://localhost:$(PORT)"
	@echo "Python:        $$(uv run python --version)"
	@echo "Rust:          $$(rustc --version)"
	@echo "Django:        $$(uv run python -c 'import django; print(django.get_version())')"
	@echo ""
	@echo "$(BLUE)Useful URLs:$(NC)"
	@echo "  Home:              http://localhost:$(PORT)/"
	@echo "  Forms:             http://localhost:$(PORT)/forms/"
	@echo "  Framework Compare: http://localhost:$(PORT)/forms/auto/compare/"
