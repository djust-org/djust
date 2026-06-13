# Threat Model — djust LiveView WebSocket auth & event transport (2026-06)

**Scope.** The live transport: the WebSocket handshake, the WS *mount* auth
gate, the WS *event* dispatch path, and the client-supplied data those paths
trust. HTTP (SSR) mount is in scope only where it differs from the WS path.

**Why now.** A confirmed auth bypass on the WS event path (T1 below) surfaced
during the v1.1.0 nav-arc work. Before patching the one instance, this models
the whole surface so the fix covers every parallel path (#1646 parallel-path
discipline) and known gaps are tracked rather than rediscovered.

**Method.** Each threat cites the load-bearing `file:line` (grep-verified at
write time, #1197) and a status: **CONFIRMED** (exploitable today), **GAP**
(weakness, not yet a proven exploit), **MITIGATED** (a control exists),
**ACCEPTED** (understood, low risk).

---

## Assets

1. **Authenticated state mutations** — `@event_handler` methods that write to the
   DB / external systems. The crown jewel: an event must not run without the
   auth the view declares.
2. **Tenant isolation** — one tenant's session must never read/write another's data.
3. **Route topology & internal names** — which routes exist + their view classes.
4. **Captured debug data** (bug-capture / replay) — may contain PII; captured
   HTML is attacker-influenced.
5. **Auth/CSRF UI integrity** — logout/CSRF controls must not silently vanish.

## Trust boundaries

- **B1 WS handshake (origin)** — cross-origin connection attempts (CSWSH).
- **B2 WS mount auth** — `handle_mount` (`websocket.py:1673`) runs
  `check_view_auth` (`auth/core.py:38`) before `mount()`.
- **B3 WS event dispatch** — `handle_event` (`websocket.py:2689`) dispatches
  client `{type:event}` frames to handler methods.
- **B4 client-supplied identifiers** — `view` path (`live_redirect_mount`),
  `view_id` (sticky children), `component_id`, event `params`.
- **B5 captured-data replay** — decoding/rendering shared `djbug1.` blobs.

---

## Threats

### T1 — Unauthenticated event dispatch after a `login_required` mount redirect — **CONFIRMED (High / P0)**

`handle_mount` runs `check_view_auth` (`websocket.py:1970`). Two failure shapes,
enforced **asymmetrically**:

- `PermissionDenied` (authed but unauthorized): `websocket.py:1977` →
  `await self.close(code=4403)`. ✅ socket terminated.
- redirect required (anonymous → `LOGIN_URL`): `websocket.py:1979-1986` sends
  `{"type":"navigate","to":…}` and **`return`s — no `close()`**. ⚠️

After the early return, `self.view_instance` is still set
(`websocket.py:1963`) but `mount()` never ran. `handle_event`
(`websocket.py:2722`) gates only on `if not self.view_instance:` and **never
re-runs `check_view_auth`** (verified: no auth call in `handle_event`'s body).
A raw WS client that ignores the navigate frame can then send
`{"type":"event",…}` and reach `@event_handler` methods with an anonymous
session. Reached via the initial mount AND `handle_live_redirect_mount`
(`websocket.py:4290`), which delegates to `handle_mount`.

**Impact:** any state-mutating handler on a `login_required` view is reachable
unauthenticated by a non-browser client. A browser hides the hole (it obeys the
navigate). Severity High — auth bypass on the live mutation path.

**Mitigation (this session):** on the redirect branch, send the navigate frame
*then* `await self.close(code=4403)` and `self.view_instance = None` — mirror the
`PermissionDenied` branch. One fix point covers both mount entries (delegation).

### T2 — Same class on the `on_mount` hook redirect branch — **GAP**

`handle_mount` (`websocket.py:2005-2007`): when an `on_mount` hook returns a
redirect, it sends `{"type":"navigate"}` and `return`s without `close()`. Same
unmounted-view-stays-reachable shape as T1. **Mitigation:** same close+clear on
this branch (fix both in one pass — parallel-path-drift #1646).

### T3 — No auth re-check on the live event path — **GAP (defense-in-depth)**

Auth is **mount-time only**; `handle_event` (`websocket.py:2689`) never re-checks
`login_required`/`permission_required`. A user who logs out or loses a permission
**mid-session** keeps dispatching events on the still-open socket until they
reconnect. This is also the enabler that makes T1/T2 reachable. **Mitigation:** a
lightweight re-check in `handle_event` for views declaring auth
(`check_view_auth_lightweight`, `auth/core.py:241`) — defense-in-depth; the T1/T2
close is the primary fix.

### T4 — Client-controlled `view` path on `live_redirect_mount` — **MITIGATED, with a default-open caveat**

The client supplies the `view` dotted path to mount. Gated by
`LIVEVIEW_ALLOWED_MODULES` (`websocket.py:1726`) + a `LiveView`-subclass check —
**but only `if allowed_modules:`**, so when the setting is unset (the default)
the module allowlist is **skipped**. The mounted view's own `check_view_auth`
still runs (via `handle_mount`), so this is not an auth bypass by itself, but a
client can probe which arbitrary `LiveView` classes exist. **Mitigation:**
document `LIVEVIEW_ALLOWED_MODULES` as recommended hardening; per-view auth
remains the real gate. (The T1 close also applies here.)

### T5 — Route-map information disclosure — **MITIGATED (#1758)**

`window.djust._routeMap` previously enumerated all routes (incl. gated) + view
classes to anonymous clients. `get_route_map_script(request)` now auth-filters
(see ADR-021 Stage 2). ✅

### T6 — Cross-origin WS hijack (CSWSH) — **MITIGATED**

`DjustMiddlewareStack(validate_origin=True)` wraps
`AllowedHostsOriginValidator` (`routing.py:48-101`), default on. Apps must keep
`ALLOWED_HOSTS` correct and not bypass the stack.

### T7 — Cross-tenant data access via the shared event path — **ACCEPTED (low risk)**

djust ships *logical* multi-tenancy (`djust/tenants/`): the tenant id is resolved
from the request at mount (`websocket.py:1996`) and reused; querysets are scoped
by `TenantMixin`. There is **no** Postgres schema/`search_path` switching, so the
django-tenants pooled-connection leak class does **not** apply (see #1557). Risk
is an app-level queryset-scoping bug, not a framework transport flaw.

### T8 — XSS via captured HTML in the replay viewer — **GAP (not yet built)**

The planned replay viewer (#1562) renders `vdom_patches[].html`, which is
attacker-influenced. **Mitigation (when built):** `escape()` all captured strings;
render captured DOM only in an `<iframe srcdoc>` with a CSP `sandbox` that
disables scripts; never reconstruct/dispatch the captured view.

### T9 — Auth/CSRF UI deleted on WS patches — **GAP (correctness / security-UI)**

The Rust engine re-renders the `[dj-view]` subtree on every WS patch from
**view-instance attributes only** — context processors and request-bound tags do
not run, so `{{ user }}` / `{% csrf_token %}` render empty and the DOM diff
deletes anything built from them (a logout form, a CSRF'd inline form vanish on
the first update). **Mitigation:** place such UI **outside** `[dj-root]` (HTTP-
rendered once, never WS-patched). Documented in BEST_PRACTICES.

---

## Prioritized mitigation plan

| Threat | Severity | Action | When |
|---|---|---|---|
| **T1** auth-redirect no-close | **High** | close(4403)+clear on redirect branch + reproducer | **this session** |
| **T2** hook-redirect no-close | Med | same close+clear (same PR) | this session |
| **T3** no event-path re-check | Med | `handle_event` re-check for auth views (defense-in-depth) | follow-up PR |
| T4 allowlist default-open | Low | docs: recommend `LIVEVIEW_ALLOWED_MODULES` | follow-up |
| T8 replay XSS | Med | escape + iframe sandbox | with #1562 |
| T9 disappearing auth UI | Low | docs (outside `[dj-root]`) | BEST_PRACTICES |
| T5 route-map | — | done (#1758) | ✅ |
| T6 CSWSH / T7 tenant | — | controls exist / accepted | ✅ |

**This session:** T1 + T2 (the bounded, confirmed transport fix). T3 and the
docs/XSS items are tracked follow-ups so the model isn't lost.
