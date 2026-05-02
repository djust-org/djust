# Opt-in extensions

Canonicalized from v0.9.2-6 PRs #1302, #1303, #1304.

## Pattern

Framework primitives gain new capability via **opt-in surfaces** — methods
or attributes that are inert until explicitly used. Existing callers see
zero behavior change. This is the preferred shape for adding capability to
stable primitives.

## Two shapes

### Method-based opt-in

Add a public method to an existing class. Callers who don't invoke it pay
no cost.

| PR | Primitive | Method | What it adds |
|---|---|---|---|
| #1302 | `AsyncResult` (frozen dataclass) | `.to_dict()` | JSON-serializable dict so templates can access `loading`/`ok`/`failed`/`result`/`error` |
| #1303 | `debounce()` wrapper (JS) | `.flush()` | Fires the pending debounced invocation immediately; no-op if idle |

```python
# AsyncResult.to_dict() — opt-in serialization shape (#1302)
@dataclass(frozen=True)
class AsyncResult:
    loading: bool = True
    ok: bool = False
    failed: bool = False
    result: Any = None
    error: Optional[BaseException] = None

    def to_dict(self) -> dict:
        """Return a JSON-serializable dict. Callers who don't need
        serialization never call this — no behavior change."""
        return {
            "loading": self.loading,
            "ok": self.ok,
            "failed": self.failed,
            "result": self.result,
            "error": str(self.error) if self.error is not None else None,
        }
```

```javascript
// debounced.flush() — opt-in eager-fire (#1303, #1278)
debounced.flush = function () {
    if (timeout === null) return;      // no-op when idle
    clearTimeout(timeout);
    timeout = null;
    func.apply(pendingThis, pendingArgs);
};
```

### Attribute-based opt-in

Add an HTML/template attribute. Authors who don't set the attribute see
no behavior change.

| PR | Element | Attribute | What it wires |
|---|---|---|---|
| #1304 | `<dialog>` | `dj-dialog-close-event="on_dialog_close"` | Native `<dialog>` close event (ESC, `dialog.close()`) dispatches to server handler |

```html
<!-- No attribute — dialog closes silently (pre-#1304 behavior, unchanged) -->
<dialog class="dj-dialog">...</dialog>

<!-- Attribute set — close event dispatches to server -->
<dialog class="dj-dialog" dj-dialog-close-event="on_dialog_close">...</dialog>
```

The JS side binds a native event listener keyed on the attribute presence:

```javascript
if (dialogEl.hasAttribute('dj-dialog-close-event')) {
    const eventName = dialogEl.getAttribute('dj-dialog-close-event');
    dialogEl.addEventListener('close', () => {
        window.djust.liveViewInstance.handleEvent(eventName, {});
    });
}
```

## When to pick each

| Shape | Use when | Counter-indication |
|---|---|---|
| **Method** | The capability is data-shape or behavior on an existing object | A class has dozens of methods already (bloat) |
| **Attribute** | The capability wires a native DOM event or widget behavior | The attribute needs to carry structured data (use JSON in `data-*` instead) |

## Backward-compat invariant

1. **Default behavior is unchanged.** Users who don't call the method / set
   the attribute see exactly what they saw before.
2. **Opt-in is explicit.** No magic detection, no auto-wiring, no
   `hasattr()` probing.
3. **Default value is the pre-existing path.** `None`/empty string/falsey
   → the old code path executes. The new code path only activates when the
   user explicitly provides a non-default value.
