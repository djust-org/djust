# Playwright Browser Automation Tests

Manual browser automation tests using Playwright. These tests require a running development server and are not part of the automated CI test suite.

## Prerequisites

```bash
# Install Playwright
pip install playwright
playwright install chromium
```

## Running Tests

```bash
# Start development server in one terminal
make start

# Run tests in another terminal
python tests/playwright/test_loading_attribute.py
python tests/playwright/test_cache_decorator.py
python tests/playwright/test_draft_mode.py
```

## Available Tests

- **test_loading_attribute.py** - Tests @loading HTML attributes (disable, class, show, hide)
- **test_interactive_dropdown.py** - ADR-034 interactive `DropdownMenu`: two same-type server menus, forged selections, client-owned popover observations (click/Escape/outside/keyboard), no-op observers, reconnect reporting (`DROPDOWN_BASE`, default `http://localhost:18438`; standalone)
- **test_model_form.py** - ADR-035 managed edit object (`ModelFormMixin`) over WebSocket, SSE and HTTP-only: render, field error, save, Back navigation, forged identity, identical denials (`MODEL_FORM_BASE`, default `http://localhost:18437`; standalone, exits non-zero on failure)
- **test_strict_parameters.py** - ADR-036 strict event parameters over WebSocket, SSE and HTTP-only (`STRICT_BASE`, default `http://localhost:18436`; standalone, exits non-zero on failure)
- **test_cache_decorator.py** - Tests @cache decorator client-side caching
- **test_draft_mode.py** - Tests DraftModeMixin functionality

## Notes

- Scripts that claim SSE or HTTP-only runs use `_transports.py`. The page's own
  configuration script re-assigns `window.DJUST_USE_WEBSOCKET` after a plain init
  script, so the setting must be pinned. `TransportWatch` then fails the run if the
  page used another transport.

- These are **manual tests** for debugging and verification
- They run against http://localhost:8002 (default dev server)
- They use headless browsers by default, change `headless=False` to see the browser
- These tests are **not** included in `make test` - they're for manual verification only

## Why Separate from CI?

These Playwright tests:
1. Require a running development server
2. Are slower than unit/E2E tests
3. Are primarily for manual debugging and verification
4. Would add complexity to CI setup

For automated testing, see:
- `tests/e2e/` - E2E pytest tests (included in CI)
- `tests/unit/` - Unit tests (included in CI)
- `tests/js/` - JavaScript tests (included in CI)
