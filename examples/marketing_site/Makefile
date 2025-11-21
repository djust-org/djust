# Marketing Site - Makefile
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
	@echo "$(BLUE)Marketing Site - Development Commands$(NC)"
	@echo ""
	@awk 'BEGIN {FS = ":.*##"; printf "Usage:\n  make $(YELLOW)<target>$(NC)\n"} /^[a-zA-Z_0-9-]+:.*?##/ { printf "  $(GREEN)%-15s$(NC) %s\n", $$1, $$2 } /^##@/ { printf "\n$(BLUE)%s$(NC)\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

##@ Development Server

.PHONY: start
start: ## Start the development server with hot reload
	@echo "$(GREEN)Starting marketing site on $(HOST):$(PORT)...$(NC)"
	@uv run python -m daphne \
		--bind $(HOST) \
		--port $(PORT) \
		marketing_project.asgi:application

.PHONY: start-bg
start-bg: stop ## Start server in background (stops existing servers first)
	@echo "$(GREEN)Starting server in background on $(HOST):$(PORT)...$(NC)"
	@(nohup uv run python -m daphne \
			--bind $(HOST) \
			--port $(PORT) \
			marketing_project.asgi:application \
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
	@PIDS=$$(lsof -ti:$(PORT) 2>/dev/null); \
	if [ -n "$$PIDS" ]; then \
		echo "$$PIDS" | xargs kill -9 2>/dev/null || true; \
	fi
	@if [ -f server.pid ]; then \
		rm server.pid; \
	fi
	@echo "$(GREEN)Server stopped$(NC)"

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
	@if [ -f server.log ]; then \
		tail -f server.log; \
	else \
		echo "$(RED)No log file found. Is the server running in background?$(NC)"; \
	fi

.PHONY: open
open: ## Open the site in browser
	@echo "$(GREEN)Opening http://localhost:$(PORT)$(NC)"
	@open http://localhost:$(PORT) || xdg-open http://localhost:$(PORT) || echo "Please open http://localhost:$(PORT) in your browser"
