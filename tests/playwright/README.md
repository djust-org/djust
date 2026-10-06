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

- **test_component_interactions_2985.py** - #2985 SortableList, SortableGrid, JsonViewer and LogViewer hooks in headless Chromium against a real LiveView over WebSocket: mouse and keyboard reorder, server re-render agreement, expand/collapse and copy, streamed and re-rendered log following, and an app's own hook of the same name keeping its place; self-contained (`DJUST_SERVER_PYTHON`, optional `CHROMIUM_EXECUTABLE` and `SHOTS_DIR`)
- **test_component_batch3_2985.py** - #2985 batch 3 ActivityFeed, Terminal and Tour hooks in headless Chromium against a real LiveView over WebSocket: pushed events and lines, ANSI parity between server-rendered and streamed lines (computed style), following a 2,000-line stream, pathological escape sequences, tour spotlight/ring/popover placement (also in a transformed ancestor), missing and far targets, keyboard and focus, and an app's own hook of the same name keeping its place; self-contained (`DJUST_SERVER_PYTHON`, optional `CHROMIUM_EXECUTABLE` and `SHOTS_DIR`)
- **test_component_batch5_signature_2985.py** - #2985 batch 5 SignaturePad hook in headless Chromium against a real LiveView over WebSocket, with the server validating every value through `decode_signature_data_url`: mouse, CDP touch (the page does not scroll) and pen-pressure drawing, ink where the pointer was at device pixel ratios 1/2/3, in RTL and on a 320 px container, Clear/Undo/empty state, the saved PNG at the pad's own size, the 2 KB and 12 KB caps, the default 64 KiB frame limit ("Message too large" then a smaller resend), the keyboard-only typed-name path, forged values (bomb header, zlib bomb, bad CRC, trailing data...), a patch mid-stroke, the server disabling the pad, five toggles without double-binding, hostile label/name, and an app's own hook keeping its place; self-contained (`DJUST_SERVER_PYTHON`, optional `CHROMIUM_EXECUTABLE` and `SHOTS_DIR`)
- **test_keyboard_nav_escape.py** - Escape inside a `role="dialog"` only ever uses an explicit close control (`.dj-modal__close` or `data-dj-close`): a Delete control that comes first is not pressed, a dialog with no close control sends nothing, the stock modal still closes once and its Tab trap still wraps, and a component that handles Escape/Tab itself reaches the server exactly once with its value; self-contained (`DJUST_SERVER_PYTHON`, optional `CHROMIUM_EXECUTABLE`)
- **test_page_shell_fallback_3036.py** - #3036 page-shell fallback in headless Chromium, self-contained (builds and serves its own three-page project; `DJUST_SERVER_PYTHON`, optional `CHROMIUM_EXECUTABLE`): a destination with a different shell is a real load, including with a `#fragment` and on Back onto a hashed entry
- **test_loading_attribute.py** - Tests @loading HTML attributes (disable, class, show, hide)
- **test_interactive_acceptance.py** - ADR-034 C4 acceptance: async and failing callbacks, `close`, duplicate/stale observations, keyboard focus, legacy plain `DropdownMenu`, two-browser isolation (`ACCEPTANCE_BASE`, default `http://localhost:18438`; standalone)
- **test_interactive_navigation.py** - ADR-034 fixed dropdown across Back navigation: session-restore and signed-snapshot paths (`NAVIGATION_BASE`, default `http://localhost:18438`; standalone)
- **test_interactive_collection.py** - ADR-034 keyed `DropdownMenu.collection()`: independent rows, reorder, removal from the member's own callback, stale identities refused, re-add, keyed observations (`COLLECTION_BASE`, default `http://localhost:18438`; standalone)
- **test_interactive_dropdown.py** - ADR-034 interactive `DropdownMenu`: two same-type server menus, forged selections, client-owned popover observations (click/Escape/outside/keyboard), no-op observers, reconnect reporting (`DROPDOWN_BASE`, default `http://localhost:18438`; standalone)
- **test_model_form.py** - ADR-035 managed edit object (`ModelFormMixin`) over WebSocket, SSE and HTTP-only: render, field error, save, Back navigation, forged identity, identical denials (`MODEL_FORM_BASE`, default `http://localhost:18437`; standalone, exits non-zero on failure)
- **test_strict_parameters.py** - ADR-036 strict event parameters over WebSocket, SSE and HTTP-only (`STRICT_BASE`, default `http://localhost:18436`; standalone, exits non-zero on failure)
- **test_multi_view.py** - #3252: the page view, two lazy views hydrated by one `mount_batch` and a click-hydrated view on one WebSocket answer their own clicks and pushes, and mount again after a reconnect (`MULTI_VIEW_BASE`, default `http://localhost:18439`; standalone, exits non-zero on failure)
- **test_multi_view_sse_http.py** - #3252 over SSE and with no live transport: the page view and the lazy views answer their own clicks over an SSE stream (a `dj-hook` pushes to its own view, the views mount again after the stream is re-established), and with no `EventSource` lazy hydration is reported `view_unavailable` while the page view keeps answering over HTTP (`MULTI_VIEW_BASE`, default `http://localhost:18439`, same demo page as `test_multi_view.py`; optional `CHROMIUM_EXECUTABLE`; standalone, exits non-zero on failure)
- **test_embedded_directives.py** - ADR-037 row 20 / #3104: `dj-shortcut`, `dj-click-away`, `dj-paste` and `dj-click` inside an embedded `{% live_render %}` child reach the child over WebSocket, SSE and HTTP-only (`EMBEDDED_BASE`, default `http://localhost:18438`; standalone)
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
