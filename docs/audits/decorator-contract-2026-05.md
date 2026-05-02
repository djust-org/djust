# djust Decorator/Tag Contract Audit — 2026-05

**Status**: Snapshot in time. Synthesized from `python/djust/decorators.py` (12 decorators), `python/djust/components/templatetags/djust_components.py` (28+ tag-emit defaults), `components/mixins/data_table.py` (24 handler defs), `python/djust/websocket_utils.py:155-198` (dispatcher's exact-match handler lookup).

**Companion audit**: `lifecycle-2026-05.md` (sibling — orthogonal state-flow surface).

**Scope**: Every decorator's promise vs. implementation; every template-tag emit-name default vs. matching mixin handler; the dispatcher's name-resolution contract; the symbol-cross-reference linter that would have caught #1275 at import time.

---

## 1. Architecture at a glance

```
{% data_table sort_event="table_sort" ... %}    # tag default in djust_components.py:509
        ↓
template renders dj-click="{{ sort_event }}"     # table.html:31 → "table_sort"
        ↓
JS click handler in 09-event-binding.js
        ↓
WS frame: {"event": "table_sort", ...}            # event name = the literal value
        ↓
LiveViewConsumer.handle_event                     # websocket.py:2529
        ↓
_validate_event_security(ws, event_name, view, ...)   # websocket_utils.py:155
        ↓
handler = getattr(view, event_name, None)         # websocket_utils.py:173 — EXACT MATCH
        ↓
if not handler: return _format_handler_not_found_error(view, event_name)
        ↓
is_event_handler(handler)?    # checks func._djust_decorators["event_handler"]
        ↓
handler(self, **coerced_params)
```

### The contract — three places must agree

| Layer | Who decides the name | Where |
|---|---|---|
| Tag-emit default | author of `djust_components.py:509` | template-tag function param |
| DOM attribute | template author | `table.html:31` (`dj-click="{{ sort_event }}"`) |
| Handler method | author of mixin/view | `data_table.py:476` (`def on_table_sort`) |

**Failure mode**: any of the three drifts and the WS dispatch hits `_format_handler_not_found_error`. Currently surfaces as a "no handler found for event: <name>" error frame to the client, with typo suggestions in DEBUG mode (`websocket_utils.py:36-88`).

---

## 2. Decorator inventory

12 decorators in `python/djust/decorators.py`. Cataloged with the contract each promises and the actual implementation behavior.

| Decorator | `_djust_decorators` key | Re-render trigger | Documented promise | Drift status |
|---|---|---|---|---|
| `@event_handler` | `"event_handler"` | Yes — full handler path; `_changed_keys` populated post-handler | "Mark method as event handler with parameter introspection. Stores metadata for validation and debug panel." | ✓ Promise matches implementation |
| `@action` | `"action"` (+ implicit `"event_handler"`) | Yes; sets `_action_state[name]` at entry, `result` or `error` at exit | "Auto-tracked pending/error/result state. Templates access via `{{ create_todo.pending }}`." | **✗ #1276** — exception **re-raised** at `decorators.py:379`; consumer (`handle_event` exception handler) sends a `{"type":"error"}` frame instead of triggering re-render. Template never sees `error` field. |
| `@server_function` | `"server_function"` | No — RPC return value JSON-serialized straight to caller | "Same-origin browser RPC. No VDOM re-render, no assigns diff." | ✓ Promise matches |
| `@background` | (only `"background": True` flag) | Yes — flushes current state, then re-renders on `start_async` callback completion | "Run handler in background after flushing current state. Current view state flushed to client before handler runs." | ✓ matches; **gap**: docstring silent on return-value handling. Sync/async return value is discarded unless the method is also `@action`. |
| `@debounce(wait, max_wait)` | `"debounce"` | No (client-side) | "Client-side debounce. Metadata for JS to debounce events." | ✓ matches; **gap**: docstring doesn't say "client-side only". The server runs the handler exactly once per dispatch. |
| `@throttle(interval, leading, trailing)` | `"throttle"` | No (client-side) | "Client-side throttle." | ✓ matches; same gap as debounce |
| `@cache(ttl, key_params)` | `"cache"` | No (client-side cache) | "Client-side response cache. Browser caches handler responses." | ✓ matches; **gap**: no manual invalidation API documented |
| `@rate_limit(rate, burst)` | `"rate_limit"` | Conditional — drops event when bucket empty, no re-render | "Server-side rate limiting using per-handler token bucket." | ✓ matches |
| `@permission_required(perms)` | `"permission_required"` | Conditional — denies → no re-render, sends error frame | "Require Django permission(s) before handler executes." | ✓ matches; enforcement happens in dispatcher (`auth.py:check_handler_permission`), not in decorator (decorator is metadata-only) |
| `@reactive` | (returns `property` descriptor) | Yes on setter — calls `self.update()` if `hasattr(self, 'update')` | "Create a reactive property that triggers re-render on change." | **🟡 Drift** — silent no-op on subclasses missing `update()` (e.g., a non-LiveView subclass or one that forgot `super().__init__()`). No warning. |
| `@computed` | (returns `_ComputedProperty` descriptor) | No — lazy property | "Memoized or plain computed property." | ✓ matches; **gap**: cache-dict mutation in `__set__` not thread-safe (no lock); `start_async` callbacks running concurrently could race |
| `@event` (deprecated) | delegates to `@event_handler` | Yes (delegated) | "Deprecated alias for @event_handler." | ✓ matches; emits `DeprecationWarning` at decoration time (not call time) |

### Decorator stackability

Order matters for some pairs:

- `@event_handler` outer, `@background` inner: ✓ standard
- `@event_handler` outer, `@action` inner: ✓ `@action` builds on `@event_handler` semantics
- `@event_handler` + `@server_function`: **✗ Mutually exclusive** — guarded at decoration time (`decorators.py:450-456`)
- `@debounce` / `@throttle` / `@cache` / `@rate_limit`: pure metadata, freely stack on `@event_handler` or `@action`

The marker contract is documented (`is_event_handler` / `is_action` predicates at `decorators.py:220-230, 396-398`) but the **stackability constraints are not user-visible** beyond the import-time guard for `@event_handler`/`@server_function`.

---

## 3. Tag-emit vs handler-name cross-reference

The most distinctive new finding of this audit. The dispatcher does **exact-match** lookup on the event name in the WS frame:

```python
# websocket_utils.py:173
handler = getattr(owner_instance, event_name, None)
```

The template renders `dj-click="{{ sort_event }}"` literally (`table.html:31, 87, 92, 94, 96`). So if `sort_event="table_sort"` (the default in `djust_components.py:509`), the WS frame's `event_name` is **`"table_sort"`** — and the dispatcher looks for `view.table_sort`, not `view.on_table_sort`.

### `data_table` defaults vs `DataTableMixin` handlers

| Tag default | Tag file:line | Expected handler (exact match) | Mixin handler defined? | Mixin file:line | Status |
|---|---|---|---|---|---|
| `sort_event="table_sort"` | djust_components.py:509 | `table_sort` | ✗ only `on_table_sort` | data_table.py:476 | **✗ #1275** |
| `prev_event="table_prev"` | :512 | `table_prev` | ✗ no `on_table_prev` either | — | **✗ NEW (filed below)** |
| `next_event="table_next"` | :513 | `table_next` | ✗ no `on_table_next` either | — | **✗ NEW (filed below)** |
| `select_event="table_select"` | :516 | `table_select` | ✗ only `on_table_select` | data_table.py:505 | ✗ same shape as #1275 |
| `search_event="table_search"` | :520 | `table_search` | ✗ only `on_table_search` | data_table.py:486 | ✗ same shape |
| `filter_event="table_filter"` | :523 | `table_filter` | ✗ only `on_table_filter` | data_table.py:492 | ✗ same shape |
| `page_event="table_page"` | :529 | `table_page` | ✗ only `on_table_page` | data_table.py:523 | ✗ same shape |
| `edit_event="table_cell_edit"` | :534 | `table_cell_edit` | ✗ only `on_table_cell_edit` | data_table.py:534 | ✗ same shape |
| `reorder_event="table_reorder"` | :537 | `table_reorder` | ✗ only `on_table_reorder` | data_table.py:552 | ✗ same shape |
| `visibility_event="table_visibility"` | :541 | `table_visibility` | ✗ only `on_table_visibility` | data_table.py:559 | ✗ same shape |
| `density_event="table_density"` | :544 | `table_density` | ✗ only `on_table_density` | data_table.py:565 | ✗ same shape |
| `edit_row_event="table_row_edit"` | :547 | `table_row_edit` | ✗ only `on_table_row_edit` | data_table.py:572 | ✗ same shape |
| `save_row_event="table_row_save"` | :548 | `table_row_save` | ✗ only `on_table_row_save` | data_table.py:579 | ✗ same shape |
| `cancel_row_event="table_row_cancel"` | :549 | `table_row_cancel` | ✗ only `on_table_row_cancel` | data_table.py:587 | ✗ same shape |
| `expand_event="table_expand"` | :553 | `table_expand` | ✗ only `on_table_expand` | data_table.py:600 | ✗ same shape |
| `bulk_action_event="table_bulk_action"` | :556 | `table_bulk_action` | ✗ only `on_table_bulk_action` | data_table.py:609 | ✗ same shape |
| `export_event="table_export"` | :558 | `table_export` | ✗ only `on_table_export` | data_table.py:619 | ✗ same shape |
| `group_event="table_group"` | :561 | `table_group` | ✗ only `on_table_group` | data_table.py:651 | ✗ same shape |
| `group_toggle_event="table_group_toggle"` | :562 | `table_group_toggle` | ✗ only `on_table_group_toggle` | data_table.py:656 | ✗ same shape |
| `row_drag_event="table_row_drag"` | :580 | `table_row_drag` | ✗ only `on_table_row_drag` | data_table.py:667 | ✗ same shape |
| `copy_event="table_copy"` | :582 | `table_copy` | ✗ only `on_table_copy` | data_table.py:689 | ✗ same shape |
| `import_event="table_import"` | :586 | `table_import` | ✗ only `on_table_import` | data_table.py:724 | ✗ same shape |
| `expression_event="table_expression"` | :595 | `table_expression` | ✗ only `on_table_expression` | data_table.py:804 | ✗ same shape |
| `row_click_event=""` | :599 | (disabled) | N/A | — | ✓ explicit opt-out |

**Summary**: 23 out of 24 default emit names dispatch to a handler that **does not exist**. The one exception (`row_click_event=""`) is intentionally disabled. The bug isn't just #1275 — it's the **whole `data_table` integration over WebSocket**.

The mixin author chose the `on_*` prefix convention (Phoenix LiveView style); the tag author chose bare names. Neither is wrong in isolation; the framework needs to pick one and enforce it.

### Other tag families

| Tag | Emit param | Default | Handler convention | Notes |
|---|---|---|---|---|
| `{% pagination %}` | `prev_event="page_prev"`, `next_event="page_next"` (djust_components.py:836) | bare names | unspecified — caller wires | OK as long as the caller knows |
| `{% toast_container %}` | `dismiss_event="dismiss_toast"` (:363) | bare name | unspecified | OK with caller-supplied handler |
| `{% notifications_dropdown %}` | `open_event="toggle_notifications"` (:2170) | bare name | unspecified | OK with caller-supplied handler |
| `{% empty_state %}` | `action_event=""` (:1442) | empty (caller chooses) | N/A | ✓ |
| `{% search_command %}` | `search_event=""` (:1777) | empty | N/A | ✓ |

The data_table tag is the one that auto-wires the entire DataTableMixin, and the entire wiring is broken.

---

## 4. Current weaknesses, ranked

🔴 = production-deploy-blocker class. 🟡 = should-fix.

**Review-when convention (#1309)**: Every 🟡 row must include a "review-when" trigger — a concrete condition under which the severity should be re-evaluated. This makes the deferral bet explicit: under what new evidence would this 🟡 become 🔴? Examples: "Re-rate if fuzz finds matching shape", "Re-rate if downstream consumer reports auth failure", "Re-rate if telemetry shows >0.1% error rate". When a follow-up PR ships warnings/observability instead of a fix, annotate with "warnings-shipped, real-fix-pending". 🔴 rows are not deferrable and get `— (🔴, not deferrable)`.

| # | Weakness | Cite | Impact | Effort | Review-when | Issue |
|---|---|---|---|---|---|
| 1 | `data_table` tag emits 23 event names that don't match any DataTableMixin handler. Every tablesort/filter/page/select/etc. interaction returns "no handler found" over WS. | `djust_components.py:509-595` (defaults), `data_table.py:476-804` (handlers), `websocket_utils.py:173` (exact-match dispatch), `table.html:31, 87` (template renders raw value) | 🔴 | M (rename one side) | — (🔴, not deferrable) | [#1275](https://github.com/djust-org/djust/issues/1275) |
| 2 | `prev_event="table_prev"` and `next_event="table_next"` have no matching handler at all (neither `table_prev` nor `on_table_prev` exists). Pagination is fully broken in `data_table` over WS. | `djust_components.py:512-513`, `table.html:94, 96` | 🔴 | S | — (🔴, not deferrable) | (file new — see §6) |
| 3 | `@action` re-raises exception after recording state; dispatcher sends error frame instead of triggering re-render. Template never sees `{{ name.error }}`. Promise/implementation drift. | `decorators.py:374-379` (re-raise), `websocket.py:2695-2708` (error-frame path), `decorators.py:262-268` (docstring promise) | 🔴 | M | — (🔴, not deferrable) | [#1276](https://github.com/djust-org/djust/issues/1276) |
| 4 | `DataTableMixin.on_table_sort/filter/page` mutate state but don't call `refresh_table()`. Even if #1275/#2 are fixed, the dispatcher would call them and the rows wouldn't change. | `data_table.py:476-529` (handlers), `data_table.py:1326-1345` (`refresh_table` exists but not auto-called) | 🔴 | S | — (🔴, not deferrable) | [#1279](https://github.com/djust-org/djust/issues/1279) |
| 5 | `@reactive` silent no-op on subclasses missing `update()`. `hasattr` guard hides the contract violation. | `decorators.py:520-521` | 🟡 | S | Re-rate if a downstream consumer reports a silent-no-op bug where `@reactive` on a non-LiveView class produced no error and no re-render | (file new) |
| 6 | `@background` discards return value silently. No clear contract for "what does the handler return?". User confused if return value not in `_action_state` (only `@action` populates it). | `decorators.py:954-979` | 🟡 | S | Re-rate if user reports or forum post surfaces confusion about `@background` return-value semantics; docstring clarified in (#1288) | (file new — doc-only) |
| 7 | `@computed` cache-dict mutation not thread-safe. Concurrent property access from `start_async` callback races against template-render access. | `decorators.py:656-664` (no lock) | 🟡 | M | Re-rate if production telemetry shows `KeyError` or stale-read bugs under concurrent `start_async` load | (file new) |
| 8 | No symbol cross-reference linter for tag-emit defaults vs handler names. The class of bug #1275 is impossible to catch without a static check; runtime "no handler found" comes too late. | (no current static check exists) | 🟡 | M | Re-rate if a second occurrence of the tag-emit/handler-name mismatch class surfaces in production; linter shipped in (#1290) | (file new — see linter sketch §7) |

---

## 5. Test gaps

| Area | Gap | Reproducer outline |
|---|---|---|
| Tag-emit symmetry | No test that `data_table` tag's default emit names match a DataTableMixin handler | Render `{% data_table %}`; click each interactive element; assert dispatcher resolves the handler |
| `@action` exception → template error visibility | No test that exception-raising `@action` re-renders with `{{ action.error }}` populated | Define `@action def fail(): raise ValueError("boom")`; trigger event; assert next render has `_action_state["fail"]["error"] = "boom"` AND no error frame sent |
| Decorator stackability | No test grid for valid/invalid stack orders | `@event_handler @background`, `@background @event_handler`, `@action @event_handler`, etc. |
| `@reactive` setter on non-LiveView | No test that `@reactive` raises a clear error on classes missing `update()` | Subclass without `update()`; mutate property; expect AttributeError or warning |
| `@computed` thread safety | No test for concurrent access | Two threads access memoized property; assert no `KeyError` or stale read |

---

## 6. Improvement roadmap

### Phase 1 — Quick wins (~3 PRs)

| # | Fix | Files | Effort | Closes |
|---|---|---|---|---|
| 1 | **Resolve the `on_` prefix divergence**. Pick one convention and apply it project-wide. Recommendation: keep DataTableMixin handlers as `on_table_*` (Phoenix-style, distinguishes "event handler" from "regular method"), and update `djust_components.py:509-595` defaults to `on_table_*` strings. Also update `table.html` in the same PR. | `djust_components.py`, `table.html`, `pagination.html`, `data_table.py` (no rename needed) | M | #1275 + weakness #2 |
| 2 | **Fix `@action` re-raise contract**. Either: (a) catch and **don't** re-raise; consumer re-renders with `_action_state[name]["error"]` populated; OR (b) update docstring to match reality (re-raise IS the contract; client receives error frame). Recommendation: (a) — matches the docstring's promise and most user-test expectations. | `decorators.py:374-379` | M | #1276 |
| 3 | **Add auto-refresh to DataTableMixin handlers**. Each `on_table_sort/filter/page/...` handler should call `self.refresh_table()` after mutating state. Add a `@_auto_refresh` decorator local to the mixin to avoid repetition. | `data_table.py:476-628` | S | #1279 |

### Phase 2 — Symbol cross-reference linter (split-foundation)

| Initiative | Effort | Closes |
|---|---|---|
| **Foundation**: build the linter — `scripts/check-handler-contracts.py`. Walk the AST of `templatetags/djust_components.py`; extract every `*_event=` default value. Walk every `mixins/*.py`; extract every `def on_*` (or whichever convention is chosen in Phase 1). Cross-reference; fail if any default isn't covered by a handler. Run as part of pre-push hook (Makefile target + pre-push wrapper). | M | Weakness #8 (foundation) |
| **Capability**: extend linter to catch cross-package mismatches (third-party packages registering djust components). Provide a programmatic API: `register_tag_emit_default(tag, param, handler)` that the linter can introspect. | M | Weakness #8 (capability) |

### Phase 3 — Decorator-contract spec tests

| Initiative | Effort | Impact |
|---|---|---|
| For every decorator, write a spec-test class with one test per docstring claim. E.g., `@action` docstring says "exception sets `_action_state[name]["error"]`" → test asserts that. Currently zero such tests; Audit B's findings hinge on the gap. | M | Locks the contract; future docstring-vs-implementation drift fails CI |
| Document decorator stackability as a matrix in `docs/STATE_MANAGEMENT_API.md`. Validate the matrix's invariants in tests. | S | User-visible contract for "can I stack X with Y?" |
| Add `@reactive` warn-on-missing-update assertion at `__set_name__` time, not at set-time. Catches the silent-no-op class. | S | Closes weakness #5 |

### Phase 4 — Documentation

- Audit table in §3 belongs in `docs/STATE_MANAGEMENT_API.md` as a canonical reference for component-tag authors.
- Decorator inventory in §2 belongs alongside or replaces the existing decorator docs (currently scattered across `decorators.py` docstrings + a few guides).

---

## 7. Linter sketch

`scripts/check-handler-contracts.py` (proposed):

```python
"""
Cross-reference tag-emit defaults against handler-name registry.
Catches bugs of class #1275 at pre-push (or pre-commit) time.

Exit 0 = all defaults match a handler. Exit 1 = mismatches found.
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAG_FILES = [ROOT / "python/djust/components/templatetags/djust_components.py"]
MIXIN_FILES = list((ROOT / "python/djust/components/mixins").glob("*.py"))

def extract_event_defaults(path: Path) -> dict[str, str]:
    """Return {param_name: default_value} for every kwarg ending in _event."""
    tree = ast.parse(path.read_text())
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for arg, default in zip(
            node.args.kwonlyargs + node.args.args[-len(node.args.defaults):],
            node.args.kw_defaults + list(node.args.defaults),
        ):
            if arg.arg.endswith("_event") and isinstance(default, ast.Constant):
                if isinstance(default.value, str) and default.value:
                    out.setdefault(default.value, []).append(f"{path.name}:{node.lineno}")
    return out

def extract_handler_names(path: Path) -> set[str]:
    """Return every method def name on classes in this file (one filter pass)."""
    tree = ast.parse(path.read_text())
    return {
        n.name for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
    }

emit_defaults = {}
for f in TAG_FILES:
    for name, sites in extract_event_defaults(f).items():
        emit_defaults.setdefault(name, []).extend(sites)

all_handlers = set()
for f in MIXIN_FILES:
    all_handlers |= extract_handler_names(f)

mismatches = [
    (name, sites) for name, sites in emit_defaults.items()
    if name not in all_handlers
]

if mismatches:
    print(f"Found {len(mismatches)} tag-emit defaults with no matching handler:")
    for name, sites in mismatches:
        print(f"  '{name}' — emitted at {', '.join(sites)}; no handler in mixins/")
    sys.exit(1)
else:
    print(f"OK — {len(emit_defaults)} tag-emit defaults all matched.")
```

This would have caught #1275 the day the prefix divergence first landed. ~50 LoC, runs in under 100ms.

---

## 8. Strategic observations

1. **The `data_table` integration is the load-bearing example of this whole class.** 23 broken emit names in one component. If the prefix divergence is fixed there (Phase 1 #1) and the linter is added (Phase 2), the pattern won't recur. If only the linter is added without fixing data_table, it will fail on every CI run from day 1 — also useful, but a worse user experience.

2. **The dispatcher's "no handler found" error in DEBUG mode already does typo suggestions** (`websocket_utils.py:36-88`). It computes `difflib.get_close_matches(event_name, public_methods)` and surfaces "Did you mean: on_table_sort?" to the user. This is a great runtime band-aid, but the linter is the right place to catch it — not at user-click-time.

3. **The `@action` re-raise is the most consequential decorator drift.** The docstring's "On exception (re-raised after recording)" sounds like it acknowledges re-raise, but the consumer behavior (error frame, no re-render) breaks the visible contract. Doc-claim-verbatim TDD (#1046) would have caught this; it's exactly the failure mode that canon was created for. Phase 1 #2 should land alongside a regression test.

4. **Decorator stackability is implicit.** Users compose decorators by trial and error. The audit's stackability matrix (§2) needs to be exposed as user-facing docs + locked in tests. The current contract is "guard at decoration time for `@event_handler`/`@server_function` mutual exclusion; everything else is silent." That's not enough.

5. **The decorator metadata system (`func._djust_decorators` dict) is the right design.** Per CLAUDE.md canon (#1198), it's already established as the single source for marker detection. The audit's findings don't require changing the metadata system — they require **using it more strictly** (linter; contract tests).

---

## 9. Sequencing

- **v0.9.2-5 drain bucket** (~3 days, 3 PRs): Phase 1 quick wins. **Blocks v0.9.2 stable** because all four cited bugs (#1275, #1276, #1279, #1291) are 🔴 production-deploy-blocker class. Fix the `on_` prefix in data_table integration (closes #1275). Add the missing pagination handlers (closes #1291). Fix `@action` re-raise contract (closes #1276). Add auto-refresh to DataTableMixin (closes #1279). Each is a focused PR with regression tests.
- **v0.9.3** (~2 days, 2 PRs): Phase 2 linter foundation + capability (closes #1290). Lock in the contract; pre-push hook fails on drift.
- **v0.9.3 late** (~3 days, ~6 PRs): Phase 3 decorator-contract spec tests (closes #1287, #1288, #1289). One test class per decorator; one test per docstring claim. This is mostly mechanical but high-leverage — catches future drift before it reaches users.
- **Continuous**: Phase 4 documentation alongside Phase 1/2/3 PRs.

---

## 10. Companion canon update

When this audit lands, add to `CLAUDE.md` under "Process canonicalizations" and `docs/PULL_REQUEST_CHECKLIST.md`:

> **Tag-emit / handler symbol cross-reference** (Audit B — 2026-05). Any
> change to a template-tag's `*_event=` default OR a corresponding mixin
> handler must run `scripts/check-handler-contracts.py` (added in
> Phase 2) before commit. The pre-push hook enforces this; bypassing it
> reproduces #1275's failure mode.
>
> **Decorator-contract spec tests** (Audit B — 2026-05). Adding any new
> decorator (or modifying an existing decorator's contract) requires a
> spec test in `tests/test_decorator_contracts.py` that asserts each
> docstring claim. The contract is the docstring; the test locks it in.
> Skipping reproduces #1276's failure mode.
