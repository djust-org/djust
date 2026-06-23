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

### T3 — No auth re-check on the live event path — **MITIGATED, opt-in (#1777)**

Auth is **mount-time only**; `handle_event` (`websocket.py:2689`) never re-checks
`login_required`/`permission_required`. A user who logs out or loses a permission
**mid-session** keeps dispatching events on the still-open socket until they
reconnect. This is also the enabler that makes T1/T2 reachable. **Mitigation:** a
lightweight re-check in `handle_event` for views declaring auth
(`check_view_auth_lightweight`, `auth/core.py:241`) — defense-in-depth; the T1/T2
close is the primary fix. **Shipped opt-in in #1777** as
`LIVEVIEW_CONFIG['reauth_on_event']` (default OFF — one session read per event).

### T4 — Client-controlled `view` path → **unauthenticated arbitrary module import** — **CONFIRMED (High), FIXED (GHSA-7prp-2623-8g45)**

> **Severity upgrade (2026-06-19).** This entry originally read "MITIGATED, with
> a default-open caveat" and framed the impact as *"a client can probe which
> arbitrary LiveView classes exist."* That **understated** it. The real
> primitive is arbitrary-module **import + top-level code execution**, reachable
> **unauthenticated**, and independent of whether the target is a LiveView.

The client supplies the `view` dotted path to mount; the consumer resolves it
with `__import__(module_path, ...)` — executing that module's **top-level code**
— **before** the `LiveView`-subclass check and before per-view `check_view_auth`
(which needs the instance). The `LIVEVIEW_ALLOWED_MODULES` guard is **fail-open**
(`if allowed_modules:` — skipped when unset, the framework default) and uses
loose `startswith` matching. Three sinks share the flaw: `handle_mount`
(`websocket.py`), `ViewRuntime.dispatch_mount` / `_instantiate_view`
(`runtime.py`, the SSE + `url_change` path), and `sse.py`.

**Impact:** an unauthenticated WS/SSE client can cause the server to import (and
run the import-time side effects of) any importable module by name → RCE-by-proxy
on any host with a side-effectful importable module, DoS (import bombs), and a
module/class enumeration oracle. Reproduced end-to-end via an unauthenticated
`WebsocketCommunicator` `mount` frame with the allowlist unset (a sentinel
non-LiveView module was imported + executed before the rejection).

**Fix (GHSA-7prp-2623-8g45):** fail-**closed** resolution gate
`djust._view_resolution.is_view_import_allowed`, applied at all three sinks
(+ defense-in-depth inside `_instantiate_view`): a client view path resolves
only if its module is **already loaded** (`sys.modules` — resolving runs no new
code; URL-routed views loaded at startup keep working with zero config) OR it
matches `LIVEVIEW_ALLOWED_MODULES` on a **module-segment boundary**. The gate runs
*before* `__import__`. Regression:
`python/djust/tests/test_security_view_import_failclosed.py`.

**Stage-11 review hardening (PEP 562 `__getattr__` residual).** The adversarial
code review found that even with the gate, resolving an already-loaded module
could still trigger a *fixed* submodule import via a module-level `__getattr__`
(PEP 562) — both `__import__(module_path, fromlist=[class_name])` (its fromlist
`hasattr` check) and the explicit `getattr(module, class_name)` consult
`__getattr__` for a client-controlled `class_name` (e.g.
`view="concurrent.futures.ProcessPoolExecutor"` loaded `concurrent.futures.process`).
Narrowed (those `__getattr__` only import a hardcoded submodule set, not arbitrary
names) but it contradicted the "imports nothing new" invariant. Closed at all
three sinks: resolve via `importlib.import_module(module_path)` (no fromlist) +
`vars(module).get(class_name)` (`__dict__` lookup never consults `__getattr__`);
the DEBUG "available views" hint enumerates `vars(module)` too. Regression:
`test_ws_mount_does_not_trigger_module_getattr` (gate-off verified).

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
| **T3** no event-path re-check | Med | opt-in `reauth_on_event` (default OFF) | done (#1777) |
| **T4** client view path → arbitrary module import | **High** | fail-closed resolution gate (`is_view_import_allowed`) before `__import__`, all 3 sinks | **FIXED — GHSA-7prp-2623-8g45** |
| T8 replay XSS | Med | escape + iframe sandbox | with #1562 |
| T9 disappearing auth UI | Low | docs (outside `[dj-root]`) | BEST_PRACTICES |
| **T10** pickle serializer cache (CWE-502) | Med | don't unpickle from shared/writable store; sign or non-pickle format | GAP (see addendum) |
| **T11** serializer codegen `exec` (CWE-94) | Low | validate identifiers before interpolation / AST-build | GAP (see addendum) |
| T5 route-map | — | done (#1758) | ✅ |
| T6 CSWSH / T7 tenant | — | controls exist / accepted | ✅ |

**This session:** T1 + T2 (the bounded, confirmed transport fix). T3 and the
docs/XSS items are tracked follow-ups so the model isn't lost.

---

## Addendum (2026-06-19) — non-transport deserialization / codegen surface

Surfaced during the audit that confirmed T4. These are **not** WebSocket-transport
threats and are **not** client-reachable in the current data flow — but they are
RCE-class deserialization / code-generation sinks that the threat model should
track as defense-in-depth gaps (the project's de-facto security threat model
lives here, so they are recorded here rather than scattered).

### T10 — Pickle deserialization of the serializer-function cache — **GAP (CWE-502, defense-in-depth)**

`python/djust/optimization/cache.py` caches generated *serializer functions* by
`pickle.dump`-ing them to `cache_dir/<key>.pkl` and to Redis
(`djust:serializer:<key>`), and loads them back via `pickle.load` /
`pickle.loads`. The data is framework-written and the key is server-derived, so
this is **not client-reachable**. However, `pickle.load` of anything is RCE if
the store can be tampered with — a world-writable `cache_dir`, or a shared /
unauthenticated Redis instance, lets an attacker with that access plant a
malicious pickle → code execution on the next cache hit. **Mitigation:** treat
the serializer cache store as a trust boundary — document that `cache_dir` must
not be world-writable and the Redis instance must be authenticated and not shared
with untrusted tenants; better, sign cache entries (HMAC) or move to a non-pickle
representation (the serializer is regenerable from the view structure, so the
cache can store a declarative spec rather than a pickled callable).

### T11 — Serializer codegen `exec` of string-interpolated source — **GAP (CWE-94, defense-in-depth)**

`python/djust/optimization/codegen.py` builds serializer source by interpolating
attribute / field names into f-strings (e.g. ``dict_path['{attr_name}']``) and
`exec`s the `compile()`d result. The interpolated names come from the view's
context / field structure (developer-defined), so this is **not client-reachable
today** — but an `attr_name` containing a quote or newline would break out of the
generated string literal (code injection), so any future path that lets
client-influenced data become a serialized key/field name is RCE. **Mitigation:**
validate each interpolated name with `str.isidentifier()` (reject otherwise)
before it enters generated source, or construct the function via the `ast` module
instead of string concatenation. Add a regression asserting a non-identifier
field name is rejected, not interpolated.

### Cleared during this audit

- **Bug-capture `djbug1.` decode** — confirmed `base64 + compact-JSON` (no
  pickle / eval), so the decoder itself is not an RCE sink. The attacker-influenced
  data risk is the **T8** replay-viewer XSS (the not-yet-built viewer must escape
  `vdom_patches[].html` and sandbox rendering), already tracked.
