# Secure Defaults — Pattern Catalog

This document names the four **proven secure-by-default patterns** the djust
framework uses so that a *new* feature inherits the same posture instead of
re-deriving (and re-getting-wrong) the controls. Each pattern below lists:

- **Threat** — what it defends against.
- **Canonical site** — the verified `file:symbol` to read and copy.
- **Shape to copy** — the minimal skeleton for a new feature.

> Companion docs: `docs/SECURITY_GUIDELINES.md` is the *how-to-use* guide for the
> `djust.security` utilities and banned patterns; `docs/PULL_REQUEST_CHECKLIST.md`
> has the per-PR "Secure defaults" review subsection that cross-references the four
> patterns here. This file is the *architectural* catalog: the shapes that make a
> feature secure-by-default.

All file:line citations below were grep-verified at write time (#1197 canon-doc
citation discipline). Line numbers drift — if a citation looks off, grep the
named symbol; the symbol name is the load-bearing reference.

---

## 1. Denylist serialization

**Threat.** A model instance reaches the wire (template context, JIT
serialization, state snapshot) and leaks a sensitive field — `password`, an auth
flag, a PII column — because serialization defaulted to *allow everything*.

> [!IMPORTANT]
> **The floor governs the template getattr sidecar too (#1986).** Besides the
> eager serialization dict, the Rust template engine has a *lazy* fallback: for
> a `{{ obj.attr }}` path not in the eager dict, it `getattr`-walks the live
> model instance (reverse relations, managers, methods — ADR-024). That walk
> would bypass this floor via **two mechanisms** — a floor field read off a
> raw model during the getattr walk, and a raw model `__dict__`-dumped during
> value conversion — reachable on SEVEN surface paths (direct field;
> manager/queryset traversal `{{ x.mgr.first.password }}`; `{% for %}`
> iteration; `_`-prefixed access `{{ obj._meta }}`, which also segfaulted the
> worker; `.values()`/`.values_list()` projections; a non-model intermediary
> object `{{ presenter.user.password }}` / a model **method** returning a
> model; and a raw `list`/`tuple` of models via an intermediary). The fix is
> **two structural chokepoints** (one per mechanism, #1646), plus the sidecar
> proxies:
>
> - **Proxies** — every model/manager/queryset entering the sidecar is wrapped
>   in `_SidecarModelProxy` / `_SidecarQuerySetProxy` (`serialization.py`),
>   which **transitively** protect everything they return
>   (`_protect_sidecar_value`), refuse the SAME floor fields (via
>   `_field_is_serializable`) and the SAME sensitive methods
>   (`_SENSITIVE_MODEL_METHODS`, shared with `_add_safe_model_methods` so the
>   two paths can't drift), refuse `_`-prefixed names (Django parity), and
>   **refuse `.values()`/`.values_list()` projections wholesale** (their rows
>   have no model identity to floor; precompute in `get_context_data()`).
> - **Chokepoint 1 (getattr walk)** — the Rust resolve walk
>   (`crates/djust_core/src/context.rs`) routes every just-materialized value —
>   after `getattr` AND the auto-call — through `_protect_sidecar_value`, so a
>   model is floor-wrapped **however it was reached** (a raw intermediary
>   object, a Rust-auto-called method result). Python proxies alone can't cover
>   those.
> - **Chokepoint 2 (value-conversion root)** — `FromPyObject for Value`
>   (`crates/djust_core/src/lib.rs`) routes **any** raw Django model through
>   `normalize_django_value` (denylist serializer) instead of the `__dict__`
>   bulk-dump, so a raw model reaching a `Value` via a list/tuple/dict
>   container is floor-filtered too. A djust proxy's `__djust_serialize__` hook
>   is preferred first.
>
> A refused name raises `AttributeError` → empty render. The floor is **not**
> gated on the `template_auto_call` kill-switch. Field-TYPE exclusion
> (always-drop `BinaryField`) is tracked as a follow-up
> ([#1987](https://github.com/djust-org/djust/issues/1987)).

**Canonical site.**
`python/djust/serialization.py`:

- `_ALWAYS_EXCLUDED_FIELDS = frozenset({"password", "is_superuser", "is_staff"})`
  (line 56) — the secure-by-default **floor**. Dropped on the auto-serialization
  paths regardless of `settings.DJUST_SENSITIVE_FIELDS` configuration **and
  regardless of a per-model `djust_serializable_fields` allowlist** (#1868 — the
  floor is now UNCONDITIONAL; an allowlist may only NARROW, never re-expose a
  floor field; module comment lines 30–40).
- `_resolve_sensitive_fields()` (line 63) — **unions** the floor with the
  optional project-wide `settings.DJUST_SENSITIVE_FIELDS`. Degrades gracefully
  (a missing setting, unconfigured Django, or non-iterable value falls back to
  the floor — serialization never raises because of this lookup).
- `_serialize_model_safely(self, obj)` (line 226) — the serializer entry point.
  A model-level `to_dict()` (read at line 242) is the full opt-out and overrides
  everything. Otherwise it resolves the effective denylist
  (`_get_denied_fields`, line 329 — floor ∪ `DJUST_SENSITIVE_FIELDS` ∪ per-model
  `djust_exclude_fields`, read at line 336), the per-model
  `djust_serializable_fields` **allowlist** (`_get_allowlist_fields`, line 348 —
  read at line 355), and the per-model `djust_serialize_sensitive_fields`
  **opt-out** (`_get_sensitive_optout_fields`, line 368 — read at line 378),
  then decides each field via `_field_is_serializable` (line 391).
- `_field_is_serializable(field_name, denied, allowed, optout=frozenset())`
  (line 391) — the actual per-field precedence, **floor-first (#1868)**
  (body lines 406–417):
  1. **Identity keys** (`_IDENTITY_KEYS = {"pk", "id", "__str__", "__model__"}`,
     line 60) always pass.
  2. **The floor wins first.** If `field_name` is in `denied` (floor ∪
     `DJUST_SENSITIVE_FIELDS` ∪ `djust_exclude_fields`) it is dropped
     **regardless of any allowlist** — UNLESS the developer deliberately
     re-includes it via the per-model `djust_serialize_sensitive_fields`
     opt-out (`optout`). A `djust_serializable_fields` allowlist **alone can NOT
     re-expose** a floor field (e.g. `djust_serializable_fields = ['is_staff',
     'password']` no longer ships those fields).
  3. **If a per-model allowlist is set, it NARROWS** the remaining (now
     floor-cleared) fields — a field passes iff it is in the allowlist (or was
     explicitly opted in via `optout`).
  4. Otherwise the field is serialized.

> [!IMPORTANT]
> **The floor is now an unconditional invariant (#1868).** A per-model
> `djust_serializable_fields` allowlist may only **narrow** the serialized set —
> it can never opt a hardcore-floor field (`password` / `is_superuser` /
> `is_staff`) back in. The **only** way to re-include a floor field is the
> deliberate, loudly-named per-model **`djust_serialize_sensitive_fields`**
> opt-out (default is always deny). This was the open question surfaced by the
> #1867 review and resolved in
> [#1868](https://github.com/johnrtipton/djust/issues/1868) by making the floor
> win — the prior allowlist-first behavior (where
> `djust_serializable_fields = ['is_staff', 'password']` re-exposed those fields)
> is gone. **Migration:** a model that previously relied on the allowlist to
> ship a floor field must now name it in `djust_serialize_sensitive_fields` as
> well — a second, explicit declaration that you accept the disclosure.

**Deliberate opt-out — re-include a floor field only when you mean it:**

```python
class AuditEntry(models.Model):
    # The allowlist narrows what ships; it can NOT re-expose the floor on its own.
    djust_serializable_fields = ["actor", "action", "is_staff"]
    # The floor still drops is_staff above — UNLESS you also opt it in explicitly:
    djust_serialize_sensitive_fields = ["is_staff"]   # I accept shipping this flag.
    # `password` / `is_superuser` are NOT opted in → they stay dropped forever.
```

**Recommended shape for your own code** that serializes user/model data — the
framework now does floor-first internally, but if you hand-roll a serializer,
mirror it: read the floor FIRST and let it win.

```python
# Read the global denylist FIRST — never start from "expose all fields".
from djust.serialization import (
    _resolve_sensitive_fields,   # floor ∪ DJUST_SENSITIVE_FIELDS
    _ALWAYS_EXCLUDED_FIELDS,     # the floor: password / is_superuser / is_staff
)

denied = _resolve_sensitive_fields()
denied |= frozenset(getattr(type(obj), "djust_exclude_fields", ()) or ())
allow = getattr(type(obj), "djust_serializable_fields", None)
# Mirror the framework's deliberate opt-out: only this set lifts the floor.
optout = frozenset(getattr(type(obj), "djust_serialize_sensitive_fields", ()) or ())

for name, value in fields(obj):
    # Floor wins first — unless deliberately opted in.
    if name in denied and name not in optout:
        continue
    # Allowlist only narrows the remaining set.
    if allow is not None and name not in allow and name not in optout:
        continue
    emit(name, value)
```

The principle: the floor must win **before** the allowlist is consulted, so a
developer's allowlist can never silently re-expose `password` / `is_superuser` /
`is_staff` — only the explicit `djust_serialize_sensitive_fields` opt-out can.

---

## 2. HMAC-signed snapshots

**Threat.** State that round-trips through the *client* (a back-navigation state
snapshot the browser echoes back, a resumable token) is attacker-controllable.
Trusting it verbatim is CWE-345 (insufficient authenticity) / CWE-915 (mass
assignment).

**Canonical site.**
`python/djust/security/state_snapshot.py`:

- `sign_snapshot(state_json, view_slug, session_key)` (line 101) — wraps the
  serialized public state in a `django.core.signing.TimestampSigner`
  (`SNAPSHOT_SALT = "djust.state_snapshot"`, line 61; keyed on `SECRET_KEY`) and
  **binds** the payload to the view slug + Django session key.
- `unsign_snapshot(...)` (line 127) — verifies signature **+ TTL**
  (`DEFAULT_MAX_AGE = 3600`, line 66; overridable via
  `DJUST_STATE_SNAPSHOT_MAX_AGE` — `get_max_age()`, line 69) **+ identity**
  (slug + session). Returns **`None`** (fail-closed) on any of: non-string
  input, tamper, expiry, cross-view replay (slug mismatch), or cross-session
  replay (sid mismatch). A `None` return means the caller MUST discard and fall
  through to a clean `mount()` — no legacy-plaintext bypass.

The wiring lives in `python/djust/websocket.py`: the server signs on emit
(`from .security import sign_snapshot`, line 2727) and verifies on receive
(`from .security import unsign_snapshot`, line 2467) before trusting any client
bytes.

**Shape to copy** for a new feature that accepts state back from the client:

```python
# EMIT — sign before sending to the client.
response["my_token_signed"] = sign_snapshot(state_json, view_slug, session_key)

# RECEIVE — verify before trusting. None ⇒ reject and fall through to a clean path.
raw = unsign_snapshot(signed_blob, view_slug, session_key)
if raw is None:
    raw = None  # rejected: tampered / expired / wrong view / wrong session
    # ... fall through to the unprivileged default (e.g. fresh mount()).
```

### Private-attr signing boundary — READ THIS

The HMAC signature covers **public state only**. `_capture_snapshot_state()`
(`python/djust/live_view.py`, line 757) **skips every `_`-prefixed attribute**
when building the snapshot, so private (`_*`) attrs are *never* in the signed
blob.

Private attrs are restored on a **separate, unsigned** path:
`_restore_private_state()` (`python/djust/live_view.py`, line 740) iterates the
saved private dict and assigns each `_*` key with a plain `setattr` — there is no
HMAC verification on this path. It is fed from the **server-side Django session**
(`request.session[...__private]`), restored in
`websocket.py` at the `_restore_private_state(...)` call (line 2390), which is
trusted *because it is server-side session storage* — not because it is signed.

**Implication:** do **not** store auth/ownership/PII state in `_*` attributes
expecting cryptographic integrity. The integrity guarantee is HMAC for public
snapshot state; for private state the guarantee is "lives in the server-side
session, never signed." If a private attr's value would be dangerous to trust
when the session backend is compromised, that is the wrong place for it —
re-derive ownership/auth from the request on each event instead of persisting it
in `_*`.

---

## 3. Fail-closed precedence gate

**Threat.** A diagnostic / introspection / schema endpoint exposes internal
surface (live cross-session state, tracebacks, the full API attack-surface map)
to anyone, because it defaulted to *serve unless explicitly locked down*.

**Canonical site.** Two sibling gates, both **fail-closed with a
non-disclosing 404** (not 403 — a 403 confirms the endpoint exists):

- **OpenAPI schema gate** — `python/djust/api/openapi.py`, `_openapi_gate(request)`
  (line 189). This is the canonical *DEBUG → opt-in-setting → authenticated →
  non-disclosing-404* ladder (first match wins):
  1. `settings.DEBUG` is True → serve (dev convenience).
  2. `settings.DJUST_API_OPENAPI_PUBLIC` is True → serve (operator opted in).
  3. `request.user.is_authenticated` → serve.
  4. Otherwise → `HttpResponse(status=404)`.
  The auth check is fail-closed: a missing `request.user` (no
  `AuthenticationMiddleware`) or an anonymous user falls through to the 404.

- **Observability gate** — `python/djust/observability/views.py`, `_gate(request)`
  (line 90). A sibling ladder: `settings.DEBUG` must be on **AND** the request
  must be loopback (`is_localhost`); otherwise 404. (Note the second rung here is
  *localhost*, not an opt-in setting — pick the rung that matches your endpoint's
  threat model; both return the same non-disclosing 404.) Every endpoint calls
  `_gate(request)` first and returns its 404 response if non-`None`.

A third instance of the *single-source-the-sequence, fail-closed* shape is the
**mount-auth** chokepoint: `python/djust/auth/core.py`,
`run_pre_mount_auth(view_instance, request)` (line 396, added in #1853). It
single-sources the canonical pre-mount order — view-level auth
(`check_view_auth`, line 38) → on success, tenant resolve (`_ensure_tenant`) →
tenant ContextVar bind — across the WebSocket, runtime, and SSE mount paths so a
future edit cannot reorder the steps or drop one on a single transport
(parallel-path drift, #1646). Auth denial **skips** tenant resolve/bind (returns
the redirect immediately); a non-`PermissionDenied` error aborts **fail-closed**.
The post-mount object-permission check is `enforce_object_permission(view, request)`
(`auth/core.py`, line 328).

**Shape to copy** for a new diagnostic / privileged endpoint:

```python
def _my_gate(request):
    # First match wins; default is DENY.
    if settings.DEBUG:
        return None                       # dev convenience
    if getattr(settings, "MY_FEATURE_PUBLIC", False):
        return None                       # explicit operator opt-in
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return None                       # authenticated
    return HttpResponse(status=404)       # non-disclosing — never 403

def my_view(request):
    gate = _my_gate(request)
    if gate is not None:
        return gate
    ...  # the privileged work
```

Rules: **deny is the default branch**, the gate is enforced **inside the view**
(not only in opt-in middleware — middleware can be omitted from a project's
setup), and the rejection is a **404, never 403**.

---

## 4. `safe_setattr` — guarded attribute writes

**Threat.** Setting an attribute whose *name* comes from client input (state
restoration, deserialized event params) enables prototype-pollution-style
attacks: `__class__`, `__init__`, `__dict__`, or a `_private` framework attr
overwritten via a crafted key.

**Canonical site.**
`python/djust/security/attribute_guard.py`:

- `safe_setattr(obj, name, value, allow_private=False, ...)` (line 191) — use
  this instead of raw `setattr()` whenever the name is untrusted. Returns
  `False` (or raises `AttributeSecurityError` with `raise_on_blocked=True`) for a
  blocked name; otherwise performs the `setattr`.
- `is_safe_attribute_name(name, allow_private=False, ...)` (line 134) — the
  underlying check, which rejects in three ways:
  1. **Dunder/dangerous names** — `DANGEROUS_ATTRIBUTES` (line 28: `__class__`,
     `__bases__`, `__mro__`, `__subclasses__`, `__init__`, `__dict__`, …) plus
     any `__…__` name.
  2. **Private names** — a leading-underscore name is rejected unless
     `allow_private=True`.
  3. **Format/injection guard** — the name must match
     `SAFE_ATTRIBUTE_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")` (line
     125), blocking dots, dashes, spaces, and other special chars.

This is the guard used by the public-state restore path in `websocket.py`
(`safe_setattr(self.view_instance, key, value, allow_private=False)`) and by
`LiveView`'s default restore loop (`live_view.py`, line 885,
`safe_setattr(self, key, value, allow_private=False)`).

**Shape to copy** whenever an attribute *name* originates from untrusted input:

```python
from djust.security import safe_setattr

# Client-controlled keys: keep allow_private=False (the secure default).
for key, value in client_supplied_dict.items():
    safe_setattr(view, key, value)               # silently skips blocked names
    # or, to reject loudly:
    # safe_setattr(view, key, value, raise_on_blocked=True)
```

Only pass `allow_private=True` when the keys come from a **trusted, server-side**
source (e.g. the framework's own session-backed private-state dict) — never for
client-supplied keys.

---

## 5. Template auto-call guards (`alters_data` / `do_not_call_in_templates`)

**The default**: template variable resolution auto-calls no-argument callables
(Django parity, ADR-024) — but a callable with `alters_data = True` is **never
invoked** (the expression renders empty), and one with
`do_not_call_in_templates = True` is used as-is. Every auto-call site shares
these guards: the Rust sidecar resolution walk
(`crates/djust_core/src/context.rs::maybe_call`), the JIT serializer's
generated code (`python/djust/optimization/codegen.py`), and eager model
serialization (`python/djust/serialization.py::_add_safe_model_methods`).

**Why it matters**: without the `alters_data` refusal, a template typo like
`{{ user.delete }}` or `{{ qs.update }}` would execute a destructive ORM
operation on first render. Django stamps `alters_data = True` on
`Model.save`/`Model.delete`, `QuerySet.delete`/`update`/etc.; djust honors the
same attribute.

**Your responsibility**: stamp `alters_data = True` on any custom model/helper
method that mutates state, exactly as you would in classic Django:

```python
class Invoice(models.Model):
    def cancel(self):
        ...
    cancel.alters_data = True   # {{ invoice.cancel }} renders empty, never runs
```

Regression tests: `python/djust/tests/test_template_auto_call_1985.py`
(side-effect sentinels prove the guarded callables are not executed).

---

## How to make a NEW feature secure-by-default

When you add a feature that emits data, accepts client-echoed state, exposes an
endpoint, or writes attributes from input, walk this checklist before writing the
happy path:

1. **Does it serialize models/user data?** Start from the denylist
   (`_resolve_sensitive_fields()`), not "expose all fields". The
   `_ALWAYS_EXCLUDED_FIELDS` floor is unconditional. → Pattern 1.

2. **Does it accept state back from the client?** Sign on emit
   (`sign_snapshot`), verify on receive (`unsign_snapshot`), treat a `None`
   verdict as "reject and fall through to the unprivileged default". Remember the
   signing boundary: HMAC covers *public* state only — don't put auth/ownership/
   PII in `_*` attrs expecting integrity. → Pattern 2.

3. **Does it expose an endpoint or diagnostic surface?** Gate it with a
   fail-closed precedence ladder whose **default branch denies**, enforced inside
   the view, returning a **non-disclosing 404**. → Pattern 3.

4. **Does it write attributes from input?** Route every untrusted-key write
   through `safe_setattr` with `allow_private=False`. → Pattern 4.

5. **Does it add a security control on a transport?** Add the control in
   `security/` or `runtime.py` so *every* transport inherits it (never hand-rolled
   per transport — parallel-path drift, #1646), and extend the anti-drift nets:
   a parity axis in `python/djust/tests/test_transport_parity_security.py` and a
   concern-4 pin in `python/djust/tests/test_mount_chokepoint_structural.py`
   (`TestMountOrchestrationChokepoint`). See the PR-checklist "Secure defaults"
   and "Transport chokepoint" subsections.

The throughline of all four patterns: **the default branch is the safe one.**
A new feature is secure-by-default when removing every project-specific override
still leaves it locked down.

---

## Audit cadence

The four patterns above only stay load-bearing if new transports and endpoints
keep routing through them. To catch drift, do a **quarterly** lightweight
re-review (this is a documented checklist, deliberately **not** a STRIDE/DFD
exercise):

1. **Re-inventory the transport chokepoints.** Confirm the mount / event /
   state-apply surface is still reached only through the shared
   `djust.security` / `runtime.py` chokepoints. Grep for new transport entry
   points (anything new that calls `mount()` / dispatches events / applies
   client state) and confirm each routes through `run_pre_mount_auth`
   (`auth/core.py`) and the `ViewRuntime` dispatch path.

2. **Re-run the anti-drift nets against any new transport.** Run
   `pytest python/djust/tests/test_transport_parity_security.py` and
   `pytest python/djust/tests/test_mount_chokepoint_structural.py`. If a new
   transport was added since the last review, confirm it appears in the parity
   axes (`TestViewAuthParity`, `TestObjectPermissionParity`,
   `TestRateLimitParity`, `TestOriginParity`) and in the concern-4 structural
   pin (`TestMountOrchestrationChokepoint`) — a transport not covered by these
   nets is a drift gap.

3. **Spot-check the four patterns for new sites.** For each pattern, grep for
   new call sites added since last review and confirm they copy the canonical
   shape (denylist floor honored, snapshot signed+verified, gate fail-closed,
   `safe_setattr` for untrusted keys).

This is intended to ride along with the existing release cadence — fold it into
the pre-release security audit rather than scheduling a separate event.
