---
title: "Hot View Replacement"
slug: hot-view-replacement
section: guides
order: 30
level: intermediate
description: "State-preserving Python code reload in development (React Fast Refresh parity)"
---

# Hot View Replacement

**New in v0.6.1.** Edit a LiveView's Python file, hit save, and your
browser updates *without* losing form input, counter values, scroll
position, or open tabs. This is React Fast Refresh parity for djust.

Gated on `DEBUG=True` and enabled by default. Zero cost in production.

## Quick start

Hot View Replacement (HVR) rides on top of the existing
`enable_hot_reload()` machinery. If you already enabled hot reload, HVR
is already working — no new config needed.

```python
# myapp/apps.py
from django.apps import AppConfig

class MyAppConfig(AppConfig):
    name = "myapp"

    def ready(self):
        from django.conf import settings
        if settings.DEBUG:
            from djust import enable_hot_reload
            enable_hot_reload()
```

```python
# settings.py (defaults shown)
LIVEVIEW_CONFIG = {
    "hot_reload": True,    # file watcher on
    "hvr_enabled": True,   # v0.6.1 — state-preserving reload
}
```

Make sure `watchdog` is installed — `djust_check` will print `C401` if
it's missing:

```bash
pip install watchdog
```

## What gets preserved

When you edit a LiveView's Python file:

1. The file-watcher detects the change.
2. djust calls `importlib.reload()` on the module.
3. Every connected consumer's view instance has its `__class__`
   swapped to the new class, **in place**, so `instance.__dict__` is
   untouched.
4. A VDOM diff runs against the new class's render output and the
   patches stream to the browser.
5. The client dispatches a `djust:hvr-applied` CustomEvent and (in
   debug mode) paints a small green toast in the bottom-right.

The user keeps:

- form field values
- scroll position
- counter values / any public attribute on the view
- active tab, open modal, etc.
- child view state (sticky LiveViews are recursed through)

## When it falls back to a full reload

HVR is **conservative**. If any of the following changed between saves,
djust emits a plain `{"type": "reload"}` frame and the page refreshes:

| Change                                | Reason                         |
| ------------------------------------- | ------------------------------ |
| `__slots__` layout changed            | Python can't reassign `__class__` |
| An `@event_handler` method was **removed** | Old bound calls would fail |
| A handler's positional-parameter **names** changed | Bound args would mismatch |
| A class was **deleted** from the module | Live instances would be orphaned |
| The file has a `SyntaxError`          | Old code stays live until you fix it |

Everything else swaps cleanly:

- Adding new `@event_handler` methods
- Changing handler **bodies** (e.g. `self.count += 1` → `self.count += 2`)
- Changing `template`, `template_name`, `page_meta`, `tick_interval`
- Adding public attributes
- Editing `mount()` (not re-run on swap, but affects next mount)

## React Fast Refresh parity

| Capability                          | React Fast Refresh | djust HVR |
|-------------------------------------|--------------------|-----------|
| Preserves state on code change      | yes                | yes       |
| Preserves scroll/input/DOM state    | yes                | yes       |
| Incremental compile step            | required           | none      |
| State-compat check                  | hook rules         | `__slots__` + handler signatures |
| Falls back to full reload on incompat | yes              | yes       |
| Dev-only (zero prod cost)           | yes                | yes       |

## Observing HVR from application code

Every successful swap dispatches a CustomEvent on `document`:

```javascript
document.addEventListener("djust:hvr-applied", (ev) => {
    console.log("HVR applied:", ev.detail.view, "v" + ev.detail.version);
});
```

The detail object mirrors the server frame:

```json
{
    "type": "hvr-applied",
    "view": "app.views.Dashboard",
    "version": 3,
    "file": "/abs/path/to/app/views/dashboard.py"
}
```

Use this to clear caches, re-wire third-party widgets, or just log
reloads in your own dev overlay.

## Limitations

- **Single-process only.** If you run multiple Gunicorn/Uvicorn workers
  in dev, only the process that reloaded the module sees the new class.
  Use a single worker in dev (the djust dev server does this by
  default).
- **No Rust code reload.** Edits to the Rust crates require a wheel
  rebuild; `make dev-build` + restart.
- **No `models.py` reload for migration.** HVR reloads the module in
  memory but doesn't run `migrate`. Restart if you changed models.
- **No mixin slot growth.** Adding a new `__slots__` entry to a mixin
  always triggers a full reload.
- **Mixin edits trigger a full reload.** HVR only reloads the module
  whose file was saved. Editing a mixin's source file does not
  propagate to views that inherit from it until you also save those
  views' files — and if the mixin's MRO contribution changes, HVR will
  reject the swap (`mro_changed`) and fall back to a full page reload.

## What HVR doesn't do (caveats)

HVR runs `importlib.reload()` on the edited module, which **re-executes
the module body**. Any side effects at module top level fire *again*:

- `signals.connect(...)` calls — will register a second receiver and
  fire handlers twice on the next save.
- `URL.register(...)` / custom registry appends.
- `threading.Thread(...).start()` — spawns a fresh thread every save.
- Top-level `print(...)` / logging calls — fire per save.

Keep the module top-level pure (class definitions and imports only).
Move side effects into `AppConfig.ready()`, a guarded
`if not hasattr(module, "_djust_initialized")` block, or an explicit
`@lru_cache()`-wrapped init helper. This is standard Django hygiene
(`AppConfig.ready()` exists for exactly this reason), and following it
makes HVR behave the same as a cold start.

## Configuration

```python
LIVEVIEW_CONFIG = {
    # Master switch (default True). Disable to restore pre-v0.6.1
    # behavior (full page reload on every .py change).
    "hvr_enabled": True,

    # File-watcher dirs (unchanged from hot_reload).
    "hot_reload_watch_dirs": None,   # None = settings.BASE_DIR
    "hot_reload_exclude_dirs": None,
}
```

## Troubleshooting

**"HVR applied" toast never appears.** Check that `globalThis.djustDebug
= true` is set in your dev console — the toast is debug-mode-only.
Check `djust_check` output for `C401` (watchdog missing).

**Full reload on every save.** Look for `HVR incompat: <reason>` in the
Django log. Most common: you renamed a handler parameter or added a
`__slots__` entry. Save once to pay the full reload, then subsequent
saves should swap in place.

**Stale class after reload.** If you see `ImportError` in the log, the
module import itself failed — the old class stays live. Fix the
`SyntaxError` / import and resave.
