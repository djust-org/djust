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

- **test_insert_subtree_namespace.py** - an `{% if %}` / `{% elif %}` block inserted inside `<svg>`, `<math>`, an SVG or MathML root, `foreignObject`, HTML `annotation-xml` or a plain `<div>`, rendered by the real Rust renderer and applied with the real `client.js` in headless Chromium: the patched page has the same elements (namespace, name, attributes, text) and the same load, error and navigation events as a full render; self-contained, no server (`DJUST_PYTHON` imports `djust._rust`, optional `CHROMIUM_EXECUTABLE`)
- **test_loading_attribute.py** - Tests @loading HTML attributes (disable, class, show, hide)
- **test_cache_decorator.py** - Tests @cache decorator client-side caching
- **test_draft_mode.py** - Tests DraftModeMixin functionality

## Notes

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
