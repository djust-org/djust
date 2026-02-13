# DX Issues Log

Tracking developer experience issues encountered in real-world djust usage.
Each issue should lead to either: a better error message, a `djust_checks` system check, or a framework fix.

---

## DX-001: `document.addEventListener` for djust custom events (silent failure)

**Severity**: High — completely silent, no errors anywhere
**Status**: Mitigated (T004 check added)

**What happens**: Developer writes `document.addEventListener('djust:push_event', ...)` in a template's `<script>` block. The event never fires because `djust:push_event` (and all `djust:*` custom events) are dispatched on `window`, not `document`.

**How developer encounters it**: Copy-paste from generic DOM event examples, or habit of using `document.addEventListener` for custom events. No error in console, no warning — the handler just silently never fires.

**Resolution**:
- **Framework fix**: N/A (dispatching on `window` is correct — it's the global event bus)
- **System check**: `djust.T004` — scans templates for `document.addEventListener('djust:` and warns to use `window.addEventListener`
- **Docs**: Should document that all `djust:*` events use `window` as the target

---

## DX-002: Loading state stuck when using `_skip_render` with `push_event`

**Severity**: Medium — button visually stuck in loading state
**Status**: Fixed in framework

**What happens**: Developer creates an event handler that uses `push_event()` with `_skip_render = True`. The client button enters loading state (spinner/disabled), but since `_skip_render` means no patch/html response is sent, the loading state is never cleared.

**How developer encounters it**: Following the `push_event` pattern for client-side-only state (e.g., opening a panel, toggling UI). Button click triggers loading indicator that never resolves.

**Resolution**:
- **Framework fix**: `03-websocket.js` now calls `globalLoadingManager.stopLoading()` in the `push_event` case of the WebSocket message handler
- **System check**: Not feasible as a static check (runtime behavior)
- **Future**: Consider making `_skip_render` automatically imply "this will be a push_event-only response" and document the pattern

---

## DX-003: 0-patch VDOM diff indistinguishable from first render

**Severity**: High — causes page mangle (full HTML replacement)
**Status**: Fixed in framework

**What happens**: When an event handler modifies state that's rendered outside `<div dj-root>` (e.g., in `base.html` while VDOM root is in `content.html`), the Rust VDOM diff returns 0 patches. Previously, both "first render" and "0 changes" returned `None`, so Python fell through to sending full HTML — which mangled the page.

**How developer encounters it**: Template inheritance with state split between base template and child template. Event handler updates state used in the base template (outside the VDOM root). Page visually breaks on interaction.

**Resolution**:
- **Framework fix**: Rust now returns `Some("[]")` for 0-change diffs, `None` only for first render
- **Runtime warning**: Python logs `[djust] Event 'X' on Y produced no DOM changes` when this occurs
- **djust-monitor**: `no_change` reason added to `_on_full_html_update` signal for monitoring
- **System check**: Not feasible as a static check (depends on runtime state)

---

## DX-004: State outside `dj-root` causes silent DOM mangle

**Severity**: High — page breaks with no clear error
**Status**: Mitigated (runtime warning added)

**What happens**: Developer puts a `{% if show_panel %}` block in `base.html` (outside the VDOM root) and an event handler that sets `self.show_panel = True`. The VDOM diff can't see changes outside its root, so it either returns 0 patches (now handled) or the full HTML fallback overwrites the entire page.

**How developer encounters it**: Natural Django pattern of putting UI chrome (sidebars, modals, panels) in the base template. Works fine with traditional Django, breaks with LiveView because the VDOM root is only the child template's content.

**Resolution**:
- **Runtime warning**: Python now logs a warning when 0-patch diff is detected (DX-003 fix)
- **Recommended pattern**: Use `push_event` for UI state that's outside the VDOM root
- **Future**: Consider a V0xx check that inspects `get_context_data()` keys vs variables inside `dj-root`
- **Docs**: Should prominently document the "VDOM root boundary" concept

---

## DX-005: `data-dj-*` event parameters silently mapped to wrong handler params

**Severity**: High — handler receives default values, not user input. Completely silent.
**Status**: Fixed in framework

**What happens**: Developer uses `data-dj-mode="dark"` on a button with `dj-click="set_theme_mode"`. The JS extracted this as `{dj_mode: "dark"}` (stripping `data-`, converting kebab to snake_case). But the handler expected `mode`, not `dj_mode`. The value ended up in `**kwargs` while `mode` got its default value.

**How developer encounters it**: Natural to prefix data attributes with `dj-` to namespace them (e.g. `data-dj-preset`, `data-dj-mode`). There was no error — the handler ran with defaults, and the `dj_*` values were silently absorbed by `**kwargs`.

**Root cause**: The JS `extractTypedParams()` stripped only the `data-` prefix (per HTML spec), not the `dj-` namespace prefix. So `data-dj-foo` → `dj_foo`, not `foo`.

**Resolution**:
- **Framework fix**: JS `extractTypedParams()` now strips the `dj_` prefix after kebab→snake conversion, so `data-dj-preset` → `preset` (not `dj_preset`)
- **Convention**: `data-dj-*` is the correct namespace for event params. Consistent with `dj-click`, `dj-change`, `data-dj-id`, etc.
- **Both work**: `data-dj-preset` → `preset`, `data-preset` → `preset` (plain attrs still work)

---

## DX-006: `data-dj-*` prefix stripping — breaking change for handler params

**Severity**: High — existing handler kwargs silently renamed
**Status**: Fixed (intentional breaking change)

**What happens**: Before this change, `data-dj-preset="dark"` was extracted as `{dj_preset: "dark"}` on the client side. Now, the `dj_` prefix is stripped so handlers receive `{preset: "dark"}`.

**How developer encounters it**: Any existing event handler that accepts `dj_preset` (or any `dj_*` kwarg) as a parameter name will stop receiving that value. The value will end up in `**kwargs` instead, and the explicitly-named parameter gets its default value. No error is raised — completely silent.

**Migration**: Rename handler parameters from `dj_foo` to `foo`:

```python
# Before (broken after this change)
@event_handler()
def set_theme(self, dj_mode: str = "light", **kwargs):
    self.mode = dj_mode

# After (correct)
@event_handler()
def set_theme(self, mode: str = "light", **kwargs):
    self.mode = mode
```

**Resolution**:
- **Framework fix**: `extractTypedParams()` in `08-event-parsing.js` now strips the `dj_` prefix after kebab→snake conversion
- **Convention**: `data-dj-*` is the correct namespace for event params. Both `data-dj-preset` and `data-preset` now produce `preset`
- **Docs**: This DX issue documents the breaking change

---

## Template for new issues

```
## DX-NNN: Short description

**Severity**: Low/Medium/High
**Status**: Open / Mitigated / Fixed

**What happens**: ...

**How developer encounters it**: ...

**Resolution**:
- **Framework fix**: ...
- **System check**: ...
- **Docs**: ...
```
