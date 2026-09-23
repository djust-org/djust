---
title: "Developer Tools"
slug: developer-tools
section: guides
order: 18
level: beginner
description: "Diagnose your setup with djust_doctor, simulate network latency, and get actionable error messages during development"
---

# Developer Tools

djust ships with built-in developer tooling to help you diagnose issues, test edge cases, and get clear feedback when something goes wrong. All three tools work together: `djust_doctor` catches configuration problems, enriched error messages explain runtime failures, and the latency simulator lets you test loading states under realistic conditions.

## djust_doctor

A single management command that verifies your entire djust environment -- Rust extension, Python/Django versions, Channels configuration, Redis connectivity, templates, static files, routing, and ASGI server.

### Basic Usage

```bash
python manage.py djust_doctor
```

Output:

```
djust doctor
============

  [VERSIONS]
  OK    djust 1.2.0rc10
  OK    Python 3.12.4
  OK    Django 5.1.2
  OK    Rust extension loaded (1.2.0rc10)

  [INFRASTRUCTURE]
  OK    Django Channels 4.0.0
  OK    ASGI_APPLICATION configured
  OK    CHANNEL_LAYERS configured (RedisChannelLayer)
  OK    Redis connected (('localhost', 6379))

  [TEMPLATES]
  OK    Template directories OK (2 dirs)
  OK    Rust template render: success (0.3ms)

  [STATIC]
  OK    djust/client.js found via staticfiles finders

  [ROUTING]
  OK    uvicorn installed (ASGI server)

  ----------------------------------------------------------
  All 12 checks passed.
```

### Flags

| Flag | Purpose |
|------|---------|
| `--json` | Output results as JSON (useful for CI pipelines) |
| `--quiet` | No output; exit code only (0=pass, 1=warn, 2=fail) |
| `--check NAME` | Run a single check by name (e.g., `--check redis`) |
| `--verbose` | Include timing for each check and extra detail |

### Available Checks

| Check Name | Category | What It Verifies |
|------------|----------|-----------------|
| `djust_version` | versions | djust package is installed |
| `python_version` | versions | Python >= 3.10 |
| `django_version` | versions | Django >= 4.0 |
| `rust_extension` | versions | Rust PyO3 extension is loadable |
| `channels_installed` | infrastructure | Django Channels is installed |
| `asgi_configured` | infrastructure | `ASGI_APPLICATION` is set |
| `channel_layers` | infrastructure | `CHANNEL_LAYERS` is configured |
| `redis` | infrastructure | Redis is reachable (if configured) |
| `template_dirs` | templates | Template directories exist |
| `rust_render` | templates | Rust engine can render a test template |
| `static_files` | static | `djust/client.js` is findable |
| `asgi_server` | routing | daphne or uvicorn is installed |

### CI Integration

Use `--quiet` or `--json` in CI pipelines:

```bash
# Exit code: 0=pass, 1=warn, 2=fail
python manage.py djust_doctor --quiet

# Parse structured output
python manage.py djust_doctor --json | jq '.status'
```

## Enriched Error Messages

When `DEBUG=True`, djust provides significantly more detail in error messages across both the Python backend and the JavaScript client.

### WebSocket Error Enrichment

Server-side errors sent to the client include extra fields in DEBUG mode:

| Field | Content |
|-------|---------|
| `traceback` | Last 3 stack frames |

The error frame format also reserves `debug_detail` (unsanitized error text) and `hint` (an actionable suggestion) fields, but at 1.2.0rc10 no code path populates them, so you will not see them in practice.

The `traceback` field is **never sent in production** -- it is stripped when `DEBUG=False`.

### VDOM Patch Error Messages

When a VDOM patch fails to find its target node, the client logs now include:

- **Patch type** (e.g., `replace`, `setAttribute`, `insertChild`)
- **dj-id** of the target element
- **Parent element** tag and ID for context
- **Suggested causes** -- third-party DOM modification, `{% if %}` block changes, conditional rendering mismatches

In DEBUG mode with `globalThis.djustDebug = true` set in the browser console, a collapsed console group shows the full patch object and the suggested causes. With `DEBUG=True` alone the group is empty:

```
[LiveView] Patch failed (replace): node not found at path=0/2/1, dj-id=counter-display
  > [LiveView] Patch detail (replace)
    [LiveView] Full patch object: {"type":"replace","path":[0,2,1],...}
    [LiveView] Suggested causes:
      - The DOM may have been modified by third-party JS
      - A template {% if %} block may have changed the node count
      - A conditional rendering path produced a different DOM structure
```

Batch patch operations now report which specific patch indices failed:

```
[LiveView] 2/8 patches failed (indices: 3, 5)
```

### Debug Panel Warning Interceptor

The debug panel automatically intercepts `console.warn` calls with the `[LiveView]` prefix and surfaces them as a warning badge on the debug button. This means VDOM patch warnings and other LiveView issues are visible without having the browser console open.

Configure auto-open behavior in your template:

```html
<script>
window.LIVEVIEW_CONFIG = {
    debug_auto_open_on_error: true  // Auto-open debug panel on first warning
};
</script>
```

## Latency Simulator

The latency simulator adds artificial delay to WebSocket messages, letting you test loading states, optimistic updates, and transitions under real-world network conditions. It lives in the debug panel and is only active when `DEBUG_MODE=true`.

### How It Works

Latency is injected on **both** WebSocket send and receive, simulating full round-trip delay. When you set 200ms latency, the total added delay is approximately 400ms (200ms on send + 200ms on receive).

### Using the Controls

The latency controls appear as a strip in the debug panel:

- **Presets**: Off, 50ms, 100ms, 200ms, 500ms -- click to apply instantly
- **Custom**: Enter any value (0--5000ms) in the input field
- **Jitter**: Add randomness as a percentage (0--100%) of the base latency

When latency is active, a badge on the debug button shows the current setting (e.g., `~200ms`).

### Persistence

Settings are saved to `localStorage` under the key `djust_debug_latency` and persist across page reloads. The latency simulator is never active in production -- it requires `DEBUG_MODE=true` which is only set when Django's `DEBUG=True`.

### Testing Loading States

The latency simulator pairs naturally with djust's loading directives:

```html
<!-- This spinner will be visible for the simulated delay -->
<button dj-click="save" dj-loading.disable>
    <span dj-loading.for="save" dj-loading.hide>Save</span>
    <span dj-loading.for="save" dj-loading.show style="display:none">Saving...</span>
</button>
```

The inner spans need `dj-loading.for="save"`: a loading directive takes its event from its own element and never from an ancestor's `dj-click`.

Set latency to 500ms or higher to visually verify that:

1. Loading indicators appear immediately when events fire
2. Buttons are properly disabled during the round-trip
3. Optimistic updates feel responsive
4. The UI recovers correctly when the response arrives

### Programmatic Access

The latency simulator exposes its state on `window.djust`:

```javascript
// Read current settings
window.djust._simulatedLatency   // Base latency in ms (0 = off)
window.djust._simulatedJitter    // Jitter factor (0.0 to 1.0)

// Set programmatically (via the debug panel API)
window.djustDebugPanel._setLatency(300);        // 300ms, keep current jitter
window.djustDebugPanel._setLatency(200, 0.25);  // 200ms with 25% jitter
```

## Putting It All Together

A typical development workflow with these tools:

1. **Run `djust_doctor`** after setup to catch configuration issues before they become runtime errors
2. **Enable `DEBUG=True`** to get enriched error messages with hints and tracebacks
3. **Open the debug panel** to monitor warnings without keeping the console open
4. **Set latency to 200--500ms** to verify loading states look correct under realistic conditions
5. **Run `djust_doctor --json`** in CI to catch regressions in environment configuration
