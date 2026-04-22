# ADR-008: Auto-Generated HTTP API from `@event_handler`

**Status**: Proposed
**Date**: 2026-04-20
**Deciders**: Project maintainers
**Target version**: v0.7.0 (candidate; could merge with ADR-N "Server Actions" in v0.8.0 if scope allows)
**Related**: [ADR-002](002-backend-driven-ui-automation.md), [ADR-003](003-llm-provider-abstraction.md), [ADR-007](007-package-taxonomy-and-consolidation.md)

---

## Summary

djust already marks every reactive server-side action with `@event_handler`, and the WebSocket dispatch path already does everything a REST framework would do for those methods: name-based routing, parameter coercion from strings, type validation, per-handler permission checks, rate limiting, and signature introspection. This ADR proposes auto-generating an HTTP+JSON API layer on top of that same infrastructure, gated by an opt-in flag per handler (`@event_handler(expose_api=True)`). The HTTP dispatch is a thin transport swap, not a reimplementation — the same handler runs, with the same validation, the same permission checks, and the same rate limiter bucket.

The feature unlocks four concrete caller classes that currently cannot talk to djust apps: mobile / native clients that don't hold WebSockets, server-to-server integrations, CLI scripts and cron jobs, and — most strategically — AI agents that speak OpenAPI. Because handlers stay the single source of truth, this directly supports manifesto principle #4 ("One Stack, One Truth"): no REST view layer duplicating business logic, no parallel serializer hierarchy, no "this validation runs in two places" drift.

The TL;DR: **if a handler has `expose_api=True`, it gets a `POST /djust/api/<view_slug>/<handler_name>/` endpoint and an OpenAPI schema entry, reusing every existing WS-path safety check**.

## Context

### What djust already provides

The WebSocket handler pipeline already contains every piece the HTTP transport would need:

- **Decorator metadata** — `@event_handler` stores a full parameter manifest on `func._djust_decorators["event_handler"]` (name, type, required/optional, default, `accepts_kwargs`, `coerce_types`, description). See `djust/python/djust/decorators.py:60-155`.
- **Parameter validation + coercion** — `validate_handler_params()` in `djust/python/djust/validation.py:170-340` maps named params, coerces strings to `int` / `float` / `bool` / `UUID` / list, and produces structured error reports. Used unchanged from HTTP.
- **Signature introspection** — `get_handler_signature_info()` in `djust/python/djust/validation.py:395-456` returns params + description in a JSON-ready shape. This is already close enough to an OpenAPI `parameters` / `requestBody` schema that the generator is a straightforward mapping.
- **View-level auth** — `check_view_auth()` in `djust/python/djust/auth/core.py` honors `login_required` and `permission_required` on the view class.
- **Handler-level auth** — `check_handler_permission()` in the same module honors `@permission_required(...)` on individual handlers.
- **Rate limiting** — per-handler token buckets already instrumented via `djust/python/djust/websocket_utils.py:155-216`; the same bucket can gate HTTP calls so the WS and HTTP transports share a budget.
- **Handler dispatch** — WS path uses `getattr(owner_instance, event_name, None)`; handlers are just methods. HTTP does the same lookup.
- **Assigns-diff snapshot** — the existing WS response path already captures which assigns changed during a handler run so the client can patch state incrementally. The HTTP response reuses that output (serialized to JSON).

The implication is that the feature is a transport adapter, not a new framework surface. Everything security-relevant lives in the handler decorator stack and runs identically regardless of transport.

### Why now

Three forces:

1. **AI-agent consumers.** ADRs 002–006 commit djust to a strategic AI direction (`AssistantMixin`, LLM provider abstraction, AI-generated UIs). AI agents consume tools via OpenAPI / tool schemas. Shipping djust without a machine-readable API surface leaves that integration story to every app developer to hand-roll.
2. **Non-browser clients.** Mobile apps, native desktop clients, CLI tooling, and cron / server-to-server jobs cannot reasonably hold a WS connection for a single action. Today they must either write a parallel REST layer or skip djust entirely.
3. **Incremental cost is low.** Because all building blocks already exist, the implementation is primarily a dispatch view, a URL wiring, a pluggable auth hook, and an OpenAPI generator. The opt-in default keeps blast radius minimal.

## Options considered

### (A) Opt-in per handler — `@event_handler(expose_api=True)` (chosen)

```python
@event_handler(expose_api=True)
def update_quantity(self, item_id: int, quantity: int, **kwargs):
    ...
```

- Security default: nothing is exposed unless the developer explicitly marks it.
- Surface area is visible at the call site — reviewing a view's API surface is `grep expose_api`.
- Fine-grained: a view can have five internal handlers and one public one.

### (B) Opt-in at the view level — `expose_handlers_as_api = True`

```python
class MyView(LiveView):
    expose_handlers_as_api = True
```

- Less decorator noise.
- Coarser blast radius: a reviewer must audit every handler on the view to know what's exposed, and adding a handler later silently extends the API surface. This is the exact failure mode we want to avoid.

### (C) Exposed by default

- Minimum boilerplate.
- Rejected outright: makes every new `@event_handler` an API endpoint by side effect. Too easy to leak internal state-mutation handlers (e.g., admin-only reset routines) without the developer realizing.

## Decision

1. **Opt-in per handler** via `@event_handler(expose_api=True)`. Default remains WS-only.
2. **Stateless invocation.** Each HTTP request instantiates a fresh view, runs the handler, serializes the result, and discards the instance. No coupling to existing WS sessions. This matches REST semantics and avoids the complexity of routing an HTTP call into a specific WS session.
3. **Auth**: Django session + CSRF by default, with a pluggable class hook:
   ```python
   class MyView(LiveView):
       api_auth_classes = [SessionAuth, MyTokenAuth]  # tried in order
   ```
   Each auth class implements `.authenticate(request) -> user | None` and may set `csrf_exempt = True` on itself to opt out of CSRF (token/header auth doesn't need it). Default when unset: `[SessionAuth]`. djust ships `SessionAuth` in v0.7.0; a first-party token auth implementation is deferred to a follow-up ADR.
4. **OpenAPI 3.1 schema** served at `/djust/api/openapi.json`, generated from the existing `get_handler_signature_info()` output. One path per exposed handler, `POST` only, with a request-body schema derived from the handler's type hints and a generic response envelope.

## Design sketch

### URL shape

```
POST /djust/api/<view_slug>/<handler_name>/
Content-Type: application/json
```

- Request body: JSON object with the handler's named params. Positional arguments are not supported over HTTP (handlers that rely on positional binding from `@click="handler('v')"` must still accept a named keyword).
- Response (success): `{ "result": <handler return value or null>, "assigns": { ...changed public assigns... } }`.
- Response (failure): `{ "error": "<kind>", "details": { ... } }` with `400` for validation, `401` for unauthenticated, `403` for permission denied, `404` for unknown view/handler or handler not marked `expose_api=True`, `429` for rate-limit, `500` for handler exception.
- `<view_slug>` is derived from a view registry keyed on a slug (class-level `api_name` attribute, falling back to a stable derivation from the view's URL name). This is one of the open questions below.

### Dispatch pipeline

```
HTTP request
  → resolve view class by slug (404 if unknown or no API-exposed handlers)
  → run api_auth_classes in order (401 if none succeed)
  → CSRF check unless auth class opts out
  → enforce login_required + view-level permission_required (check_view_auth)
  → parse JSON body (400 if malformed)
  → instantiate view, set request/user, run mount() equivalent
  → look up handler by name, verify expose_api=True (404 if not)
  → enforce handler-level @permission_required (check_handler_permission)
  → apply rate-limit bucket (429 if exceeded)
  → validate_handler_params(handler, body)  (400 on error, with details)
  → invoke handler (sync or async)
  → snapshot changed assigns from the WS path's diff machinery
  → JsonResponse({ "result": ..., "assigns": ... })
```

Every arrow except JSON parse, instance setup, and `JsonResponse` is an existing function being reused.

### OpenAPI generator

`djust.api.openapi.build_schema()` walks the registry of API-exposed handlers. For each, it calls `get_handler_signature_info(handler)` and maps:

- Python `int` / `float` / `bool` / `str` → OpenAPI `integer` / `number` / `boolean` / `string`.
- `UUID` → `string` with `format: uuid`.
- `list[T]` → `array` with item schema.
- Required/optional → `required` array in the request body schema.
- Handler docstring / explicit `description=` → operation `summary`.

The result is a static-enough document that we can cache it at process start and invalidate only on code reload.

## Consequences

### Positive

- **One stack, one truth** — business logic lives in handlers, not duplicated in REST views.
- **AI-agent integration** — an OpenAPI schema is the lingua franca for agent tool use; djust apps become agent-addressable without any per-app glue.
- **Non-browser client support** — mobile, CLI, and S2S callers unlocked.
- **Free observability** — the existing handler timing / SQL-query instrumentation also applies to HTTP calls since the same handler runs.
- **Parity narrative** — fills a gap relative to Phoenix (`JSON.SON` channels) and React 19 Server Actions.

### Negative / risks

- **New attack surface per exposed handler.** The opt-in flag is load-bearing; accidentally-exposed handlers are the dominant risk. Mitigation: require `expose_api=True` to be explicit; produce a system check that lists every API-exposed handler on `manage.py djust_audit`; document review practice ("treat `expose_api=True` like `@csrf_exempt`").
- **`mount()` semantics.** Many LiveViews do non-trivial work in `mount()` (loading presence state, subscribing to pubsub, seeding derived state). Stateless HTTP invocation runs `mount()` on every call, which may be expensive or semantically wrong (e.g., registering presence for a fire-and-forget API call). Mitigation is part of the open questions below.
- **CSRF fork.** Session auth needs CSRF; token auth must not. Two paths means two bugs-waiting-to-happen. Mitigation: the auth-class contract is the only place that decides, and the dispatch view reads a single boolean.
- **Response serialization.** Handler return values and changed assigns can contain Python types that aren't JSON-safe (`Decimal`, `date`, `UUID`). The WS path already has a serializer for this (same one forms use); reuse it rather than inventing a second.
- **Documentation surface grows.** Every developer now has two ways to invoke a handler, two auth stories, two error shapes to reason about. Keep the docs unified: "handlers are actions; here are the two transports."

## Open questions (resolved in implementation PR)

- **`mount()` vs. `api_mount()`?** Should the dispatch path call the full `mount()` or a lightweight `api_mount()` hook that skips WS-specific setup? Leaning toward: call `mount()`; allow views to override `api_mount()` to differ. Document the cost.
- **`request.user` / `request` shape** on the instance — the fresh instance needs the same attribute surface the WS path provides. Audit what the WS consumer sets and mirror it in one helper.
- **Return value vs. assigns in the response** — always include both? Let the handler suppress one via `@event_handler(expose_api=True, api_response="result-only")`? Default: send both; the caller reads what it needs.
- **Transport-conditional return values** — Handlers designed for WebSocket only mutate state (return None). HTTP API consumers need actual data (e.g., search results, not just `{"assigns": {"search_query": "..."}}`). But serializing query results on every WebSocket keystroke is wasteful. **Resolved:** The dispatch view sets `self._api_request = True` on the view instance before invoking the handler. Handlers (or a decorator) can check this flag to conditionally return serialized data. A `@api_returns(serializer_fn)` decorator pattern is recommended:
  ```python
  @event_handler(expose_api=True)
  @api_returns(lambda self: serialize_claims_page(self._get_filtered_claims()))
  def search(self, value: str = "", **kwargs):
      self.search_query = value  # state mutation only — no serialization overhead on WS
  ```
  The decorator calls `serializer_fn(self)` only when `self._api_request` is True (HTTP path), returning the serialized data as the handler's return value. On WebSocket, the decorator is a no-op — zero extra DB queries, zero serialization. This preserves "one stack, one truth" while avoiding performance overhead on the WebSocket path.
  **Implementation:** `dispatch.py` sets `view._api_request = True` (one line, before handler invocation). Projects define `@api_returns` as a thin wrapper. A framework-level `@api_returns` decorator may ship in a future release.
- **URL namespace** — `/djust/api/...` vs. `/api/...`. The `djust/` prefix matches existing paths like `/djust/ws/` and keeps the feature out of the app's own URL namespace. Leaning `/djust/api/`.
- **Slug derivation** — how to produce `<view_slug>` when the view isn't registered via `live_session()`. Options: class-level `api_name`, fallback to `app_label.ViewClassName`, or require explicit opt-in via `api_name`.
- **Token auth scope** — a first-party token auth belongs in a separate ADR; this one ships only the pluggable interface + `SessionAuth`.
- **System check** — should `djust_audit` list every `expose_api=True` handler and fail loudly if any is also missing `@permission_required`? Candidate new rule: **every API-exposed handler must declare explicit permissions**.

## Out of scope

- **First-party token auth implementation.** Shipped as a separate ADR and a later release.
- **Swagger UI hosting.** The OpenAPI JSON is enough; users can point any UI at it. A built-in `/djust/api/docs/` is a nice-to-have for a later iteration.
- **Per-handler URL customization** (`@event_handler(api_path="/custom/url")`). Keep v1 conventional; introduce if a real use case emerges.
- **Batch multi-call requests.** One handler per request. Compound use cases can be expressed as a handler that calls others.
- **GraphQL or gRPC transports.** Not in this ADR; REST/JSON is the pragmatic default.

## Alternatives briefly considered

- **Lean on DRF.** Wrap handlers in DRF views. Rejected: DRF brings serializers, viewsets, permissions, renderers, negotiation — all of which duplicate machinery djust already has in its handler stack. The right move is to treat djust handlers as the contract and generate transport adapters from them, not adopt another framework's abstractions.
- **Auto-generate per-app `urls.py` entries.** Rejected: implicit URL generation from decorators is harder to audit than a single dispatch view + slug routing.
- **Merge with Server Actions (the `@action` decorator already on the roadmap).** Possible, and flagged in the roadmap entry: if Server Actions arrives first and defines a typed action signature, this ADR's handler-to-HTTP mapping can build on that same signature rather than `@event_handler`. Revisit when both are in flight.

## Changelog

- **2026-04-20** — Initial proposal.
