# djust - Makefile
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
	@echo "$(BLUE)djust - Development Commands$(NC)"
	@echo ""
	@awk 'BEGIN {FS = ":.*##"; printf "Usage:\n  make $(YELLOW)<target>$(NC)\n"} /^[a-zA-Z_0-9-]+:.*?##/ { printf "  $(GREEN)%-15s$(NC) %s\n", $$1, $$2 } /^##@/ { printf "\n$(BLUE)%s$(NC)\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

##@ Development Server

.PHONY: start
start: ## Start the Django development server with hot reload
	@echo "$(GREEN)Starting djust development server on $(HOST):$(PORT)...$(NC)"
	@uv run python -m uvicorn demo_project.asgi:application \
		--host $(HOST) \
		--port $(PORT) \
		--log-level info \
		--reload \
		--reload-dir examples/demo_project \
		--reload-include '*.html' \
		--reload-include '*.py' \
		--app-dir examples/demo_project

.PHONY: start-bg
start-bg: stop ## Start server in background (stops existing servers first)
	@echo "$(GREEN)Starting server in background on $(HOST):$(PORT)...$(NC)"
	@(nohup uv run python -m uvicorn demo_project.asgi:application \
			--host $(HOST) \
			--port $(PORT) \
			--log-level info \
			--reload \
			--reload-dir examples/demo_project \
			--reload-include '*.html' \
			--reload-include '*.py' \
			--app-dir examples/demo_project \
			> examples/demo_project/server.log 2>&1 & echo $$! > examples/demo_project/server.pid; \
		sleep 1; \
		if [ -f examples/demo_project/server.pid ]; then \
			echo "$(GREEN)Server started with PID: $$(cat examples/demo_project/server.pid)$(NC)"; \
		else \
			echo "$(GREEN)Server started$(NC)"; \
		fi; \
		echo "$(YELLOW)Logs: $$(pwd)/examples/demo_project/server.log$(NC)")

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
test: test-python test-js test-rust ## Run all tests (Python + JavaScript + Rust)

.PHONY: test-rust
test-rust: ## Run Rust tests
	@echo "$(GREEN)Running Rust tests...$(NC)"
	@echo "$(YELLOW)Note: Excluding djust_live (PyO3 extension module - tested via Python)$(NC)"
	@PYO3_PYTHON=$$(pwd)/.venv/bin/python cargo test --workspace --exclude djust_live

.PHONY: test-python
test-python: ## Run Python tests
	@echo "$(GREEN)Running Python tests...$(NC)"
	@PYTHONPATH=. .venv/bin/python -m pytest tests/ python/tests/

.PHONY: test-python-parallel
test-python-parallel: ## Run Python tests in parallel (requires pytest-xdist)
	@echo "$(GREEN)Running Python tests in parallel...$(NC)"
	@PYTHONPATH=. .venv/bin/python -m pytest tests/ python/tests/ -n auto

.PHONY: test-js
test-js: ## Run JavaScript tests
	@echo "$(GREEN)Running JavaScript tests...$(NC)"
	@npm test

.PHONY: test-vdom
test-vdom: ## Run VDOM patching tests
	@echo "$(GREEN)Running VDOM patching tests...$(NC)"
	@PYTHONPATH=. .venv/bin/python -m pytest python/tests/test_vdom_patching_wrapper.py -v

.PHONY: test-liveview
test-liveview: ## Run LiveView core tests
	@echo "$(GREEN)Running LiveView tests...$(NC)"
	@PYTHONPATH=. .venv/bin/python -m pytest tests/unit/test_live_view.py -v

.PHONY: test-playwright
test-playwright: ## Run Playwright browser automation tests (manual, requires server running)
	@echo "$(YELLOW)Running Playwright tests (requires 'make start' in another terminal)...$(NC)"
	@echo "$(YELLOW)Note: These are manual tests not included in CI$(NC)"
	@.venv/bin/python tests/playwright/test_loading_attribute.py
	@.venv/bin/python tests/playwright/test_cache_decorator.py
	@.venv/bin/python tests/playwright/test_draft_mode.py
	@echo "$(GREEN)Playwright tests completed$(NC)"

.PHONY: lint
lint: ## Run linters
	@echo "$(GREEN)Running linters...$(NC)"
	@uv run ruff check python/
	@PYO3_PYTHON=$$(pwd)/.venv/bin/python cargo clippy -- -W clippy::all -D clippy::correctness -D clippy::suspicious

.PHONY: lint-ci
lint-ci: ## Run linters in CI mode (warnings as errors)
	@echo "$(GREEN)Running linters in CI mode (strict)...$(NC)"
	@uv run ruff check python/
	@cargo clippy -- -D warnings

.PHONY: format
format: ## Format code
	@echo "$(GREEN)Formatting code...$(NC)"
	@uv run ruff format python/
	@cargo fmt

.PHONY: pre-commit
pre-commit: ## Run pre-commit hooks on all files
	@echo "$(GREEN)Running pre-commit hooks...$(NC)"
	@uvx pre-commit run --all-files

.PHONY: pre-commit-install
pre-commit-install: ## Install pre-commit hooks (run once after clone)
	@echo "$(GREEN)Installing pre-commit hooks...$(NC)"
	@uvx pre-commit install
	@uvx pre-commit install --hook-type pre-push
	@echo "$(GREEN)Pre-commit hooks installed! They will run automatically on git commit.$(NC)"
	@echo "$(GREEN)Pre-push hooks installed! Tests will run before git push.$(NC)"

.PHONY: check
check: lint test ## Run linters and tests

##@ Benchmarks

.PHONY: benchmark
benchmark: benchmark-rust benchmark-python ## Run all benchmarks

.PHONY: benchmark-rust
benchmark-rust: ## Run Rust benchmarks (Criterion)
	@echo "$(GREEN)Running Rust benchmarks...$(NC)"
	@echo "$(YELLOW)Note: This may take several minutes$(NC)"
	@PYO3_PYTHON=$$(pwd)/.venv/bin/python cargo bench --workspace --exclude djust_live 2>&1 | tee benchmark-rust.log
	@echo "$(GREEN)Rust benchmark results saved to benchmark-rust.log$(NC)"
	@echo "$(YELLOW)HTML reports available in target/criterion/$(NC)"

.PHONY: benchmark-python
benchmark-python: ## Run Python benchmarks (pytest-benchmark)
	@echo "$(GREEN)Running Python benchmarks...$(NC)"
	@PYTHONPATH=. .venv/bin/python -m pytest tests/benchmarks/ -v --benchmark-only --benchmark-autosave

.PHONY: benchmark-python-compare
benchmark-python-compare: ## Compare Python benchmarks against saved baseline
	@echo "$(GREEN)Comparing Python benchmarks against baseline...$(NC)"
	@PYTHONPATH=. .venv/bin/python -m pytest tests/benchmarks/ -v --benchmark-compare

.PHONY: benchmark-quick
benchmark-quick: ## Run quick benchmarks (minimal iterations)
	@echo "$(GREEN)Running quick Python benchmarks...$(NC)"
	@PYTHONPATH=. .venv/bin/python -m pytest tests/benchmarks/ -v --benchmark-only \
		--benchmark-min-rounds=3 \
		--benchmark-warmup=off \
		--benchmark-disable-gc

.PHONY: benchmark-e2e
benchmark-e2e: ## Run end-to-end LiveView benchmarks
	@echo "$(GREEN)Running end-to-end benchmarks...$(NC)"
	@PYTHONPATH=. .venv/bin/python -m pytest tests/benchmarks/test_e2e.py -v --benchmark-only \
		--benchmark-min-rounds=5

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

##@ Deployment

.PHONY: docker-build
docker-build: ## Build and push Docker image to ghcr.io
	@echo "$(GREEN)Building and pushing Docker image...$(NC)"
	@./k8s/build.sh

.PHONY: k8s-deploy
k8s-deploy: ## Deploy to Kubernetes cluster
	@echo "$(GREEN)Deploying to Kubernetes...$(NC)"
	@./k8s/deploy.sh

.PHONY: deploy
deploy: docker-build k8s-deploy ## Build Docker image and deploy to Kubernetes

.PHONY: k8s-status
k8s-status: ## Check Kubernetes deployment status
	@echo "$(BLUE)Kubernetes Deployment Status$(NC)"
	@kubectl get pods,svc,ingress -n djust
	@echo ""
	@echo "$(BLUE)Certificate Status$(NC)"
	@kubectl get certificate -n djust

.PHONY: k8s-logs
k8s-logs: ## View Kubernetes pod logs
	@kubectl logs -f deployment/djust-live -n djust

.PHONY: k8s-restart
k8s-restart: ## Restart Kubernetes deployment
	@kubectl rollout restart deployment/djust-live -n djust

##@ Developer Tools

.PHONY: stats
stats: ## Show state backend statistics
	@cd examples/demo_project && uv run python -m djust stats

.PHONY: health
health: ## Run health checks on djust backends
	@cd examples/demo_project && uv run python -m djust health

.PHONY: profile
profile: ## Show profiling statistics
	@cd examples/demo_project && uv run python -m djust profile

.PHONY: profile-verbose
profile-verbose: ## Show detailed profiling statistics
	@cd examples/demo_project && uv run python -m djust profile -v

.PHONY: analyze
analyze: ## Analyze LiveView templates for optimization opportunities
	@echo "$(BLUE)Analyzing LiveView templates...$(NC)"
	@cd examples/demo_project && uv run python -m djust analyze .

.PHONY: clear-cache
clear-cache: ## Clear state backend caches (prompts for confirmation)
	@cd examples/demo_project && uv run python -m djust clear

.PHONY: clear-cache-force
clear-cache-force: ## Force clear all state backend caches (no confirmation)
	@cd examples/demo_project && uv run python -m djust clear --force --all

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
	@echo "$(BLUE)djust - Project Information$(NC)"
	@echo "Server URL:    http://localhost:$(PORT)"
	@echo "Python:        $$(uv run python --version)"
	@echo "Rust:          $$(rustc --version)"
	@echo "Django:        $$(uv run python -c 'import django; print(django.get_version())')"
	@echo ""
	@echo "$(BLUE)Useful URLs:$(NC)"
	@echo "  Home:              http://localhost:$(PORT)/"
	@echo "  Forms:             http://localhost:$(PORT)/forms/"
	@echo "  Framework Compare: http://localhost:$(PORT)/forms/auto/compare/"
	@echo ""
	@echo "$(BLUE)CLI Commands:$(NC)"
	@echo "  djust stats          Show state backend statistics"
	@echo "  djust health         Run health checks"
	@echo "  djust profile        Show profiling statistics"
	@echo "  djust analyze <path> Analyze templates for optimization"
	@echo "  djust clear          Clear state backend caches"
