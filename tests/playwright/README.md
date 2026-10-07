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
- **test_component_batch5_cropper_2985.py** - #2985 batch 5 ImageCropper hook in headless Chromium against a real LiveView over WebSocket, with the server cropping through `djust.components.cropping.crop_image` (needs Pillow in the server's interpreter): the box in natural pixels (800, 1600 and 6000 px images, a 150 px one smaller than its minimum), mouse/CDP-touch/keyboard moving, resizing and drawing, a locked 16:9 and 1:1, the right pixels cropped (four-quadrant image), an EXIF-rotated JPEG cropped upright where a raw-pixel crop gets another region, window resizes, a swapped image, a patch mid-drag, five toggles, forged boxes and a gigapixel-header image refused, a broken image, RTL, hostile alt text and an app's own hook; self-contained (`DJUST_SERVER_PYTHON`, optional `CHROMIUM_EXECUTABLE` and `SHOTS_DIR`)
- **test_keyboard_nav_escape.py** - Escape inside a `role="dialog"` only ever uses an explicit close control (`.dj-modal__close` or `data-dj-close`): a Delete control that comes first is not pressed, a dialog with no close control sends nothing, the stock modal still closes once and its Tab trap still wraps, and a component that handles Escape/Tab itself reaches the server exactly once with its value; self-contained (`DJUST_SERVER_PYTHON`, optional `CHROMIUM_EXECUTABLE`)
- **test_page_shell_fallback_3036.py** - #3036 page-shell fallback in headless Chromium, self-contained (builds and serves its own three-page project; `DJUST_SERVER_PYTHON`, optional `CHROMIUM_EXECUTABLE`): a destination with a different shell is a real load, including with a `#fragment` and on Back onto a hashed entry
- **test_loading_attribute.py** - Tests @loading HTML attributes (disable, class, show, hide)
- **test_component_batch5_image_upload_2985.py** - #2985 batch 5 ImageUploadPreview hook on djust's own upload pipeline in headless Chromium against a real LiveView (`UploadMixin`) over WebSocket: thumbnails at once from object URLs, bytes and event count at the server, type/size/count pre-checks (the server's registration count proves a refused file was never sent), a server-refused SVG marked, Cancel of a 40 MB upload in flight, single-image replace, drag and drop, a plain input with no slot, object URLs given back on remove/replace/teardown/toggle, the server's previews taking over, keyboard and focus ring, announcements, RTL at 320 px, hostile file names, `img-src` with and without `blob:`, and an app's own hook keeping its place; self-contained (`DJUST_SERVER_PYTHON`, optional `CHROMIUM_EXECUTABLE` and `SHOTS_DIR`)
- **test_interactive_acceptance.py** - ADR-034 C4 acceptance: async and failing callbacks, `close`, duplicate/stale observations, keyboard focus, legacy plain `DropdownMenu`, two-browser isolation (`ACCEPTANCE_BASE`, default `http://localhost:18438`; standalone)
- **test_interactive_navigation.py** - ADR-034 fixed dropdown across Back navigation: session-restore and signed-snapshot paths (`NAVIGATION_BASE`, default `http://localhost:18438`; standalone)
- **test_interactive_collection.py** - ADR-034 keyed `DropdownMenu.collection()`: independent rows, reorder, removal from the member's own callback, stale identities refused, re-add, keyed observations (`COLLECTION_BASE`, default `http://localhost:18438`; standalone)
- **test_interactive_dropdown.py** - ADR-034 interactive `DropdownMenu`: two same-type server menus, forged selections, client-owned popover observations (click/Escape/outside/keyboard), no-op observers, reconnect reporting (`DROPDOWN_BASE`, default `http://localhost:18438`; standalone)
- **test_model_form.py** - ADR-035 managed edit object (`ModelFormMixin`) over WebSocket, SSE and HTTP-only: render, field error, save, Back navigation, forged identity, identical denials (`MODEL_FORM_BASE`, default `http://localhost:18437`; standalone, exits non-zero on failure)
- **test_strict_parameters.py** - ADR-036 strict event parameters over WebSocket, SSE and HTTP-only (`STRICT_BASE`, default `http://localhost:18436`; standalone, exits non-zero on failure)
- **test_component_batch4_2985.py** - #2985 batch 4 DashboardGrid, MentionsInput, CursorsOverlay and CollabSelection hooks in headless Chromium against a real LiveView over WebSocket: pointer, touch and keyboard moves and resizes sent as `{id, col, row}` / `{id, width, height}` (also right-to-left and scaled, a patch shifting the panels mid-drag, an event the server ignores, forged payloads against the documented handlers), the @-mention combobox and its `{text, mentions}` submit, cursor labels kept inside and apart (also after a resize, with reduced motion), CSS-highlight anchoring of a target's text (scrolling, edits, transformed ancestors, hostile colours, teardown), and an app's own hook of the same name keeping its place; self-contained (`DJUST_SERVER_PYTHON`, optional `CHROMIUM_EXECUTABLE` and `SHOTS_DIR`)
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

- `test_component_batch5_voice_2985.py`: VoiceInput browser UI and real LiveView/WebSocket transcript delivery. Simulates SpeechRecognition callbacks; does not open a microphone or validate vendor recognition services. Covers final/interim delivery, focus/teardown aborts, permission feedback, unsupported API, escaped transcripts and custom hooks.
