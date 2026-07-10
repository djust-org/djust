# ADR-021: Automatic SPA Navigation (native `dj-navigate` as canonical)

**Status**: Accepted — Stage 1 (route-map foundation, #1733/PR #1736) shipped v1.0.2; Stage 2 (`auto_navigate` opt-in #1734, nav-story reconciliation #1735, route-map auth-hardening #1758) shipped 2026-06-13 in v1.0.4 (PRs #1775/#1776); **Stage 3 (default ON) shipped v1.1.0rc1 (2026-06-24)** — `LIVEVIEW_CONFIG["auto_navigate"]` defaults to `True`, making native `dj-navigate` the zero-config canonical SPA model (opt out with `auto_navigate=False`).
**Shipped in**: v1.0.2 (Stage 1); v1.0.4 (Stage 2); v1.1.0rc1 (Stage 3 — default ON)
**Source**: `/pipeline-strategy` 2026-06-05 (auto-navigation), Path 2 (Foundation + opt-in)

## Context

djust currently has a **fragmented navigation story** and a **zero-config gap** in
its native SPA navigation:

1. **Native `dj-navigate` / `live_redirect`** does SPA navigation over the
   *existing* WebSocket — light, no socket teardown, preserves the connection —
   but it only works if the developer manually wires `live_session()` (to build
   the URL→view route map) **and** emits `get_route_map_script()` into the page.
   That wiring is undocumented (`navigation.md` omits it entirely and even
   mislabels `dj-navigate` as "full page navigation"), and
   `get_route_map_script`'s docstring cites a `{% djust_route_map %}` template
   tag that **was never implemented**. With no route map, `dj-navigate` silently
   falls back to a full page reload.

2. **External TurboNav** (`docs/guides/turbonav-integration.md`) is a separate,
   documented path: an external `turbo.js` intercepts links, fetches via AJAX,
   and swaps `<main>` innerHTML — which tears down and **reconnects the WebSocket
   on every navigation** (heavier; loses LiveView connection continuity).

A downstream production app (rent tracker, on v1.0.2rc1) hit failure mode #1: it
added `dj-navigate` per the docs, got silent full reloads, and had to
reverse-engineer the `live_session` + route-map requirement from djust's source.

This violates the manifesto: **Developer First** ("we handle WebSockets, VDOM,
reconnection") and **Complexity Is the Enemy** (manual route-map plumbing for a
documented directive). It is also a **doc-vs-code drift** instance (the phantom
tag) of the class canonicalized in CLAUDE.md (#1046/#1071).

## Decision

Adopt **native `dj-navigate` as djust's canonical SPA-navigation model**, made to
work with **zero wiring**, and introduce an opt-in **Turbo-Drive-style automatic
link interception** on top of it. Concretely, in two stages (split-foundation,
per Action #1122):

### Stage 1 — Foundation (v1.0.2-1 → ships in v1.0.2, #1733): `dj-navigate` works with zero wiring
- The client route map is **auto-derived from the Django URLconf** (every route
  whose callback resolves to a `LiveView` subclass), not from `live_session()`.
  `live_session()` remains supported for WS session grouping and existing wiring
  stays valid (merge/idempotent), but is **no longer required** for routing.
- The route map is **auto-emitted via `{% djust_client_config %}`** — the tag
  already present in every scaffolded base `<head>` — with CSP-nonce support.
  No template change, no context processor, no manual script.
- Docs corrected (phantom tag removed; route-map prerequisite documented; the
  "full page navigation" mislabel fixed) and a system check warns if
  `dj-navigate` is used with an empty route map.

### Stage 2 — Capability (v1.1.0, #1734, #1735): opt-in `auto_navigate`
- `LIVEVIEW_CONFIG['auto_navigate']` (**default OFF**) enables a single delegated
  click listener that SPA-navigates plain `<a href>` links **only when the path
  resolves in the route map**, with standard opt-outs (modifier/middle-click,
  `target`/`download`, external/non-http, hash-only, `data-no-navigate`).
  Same-view query-only diffs use `live_patch` (state-preserving); cross-view uses
  `live_redirect`. Non-LiveView links (admin, logout, external) fall through to
  normal browser navigation.
- Native `dj-navigate` is positioned as **canonical**; external TurboNav is
  repositioned as **interop** ("djust also works under external Turbo", with the
  per-nav-WS-reconnect tradeoff stated), not a parallel-recommended path (#1735).

### Stage 2 — Route-map exposure hardening (#1758, security)

The Stage-1 risk note below originally read "URLs are public, not sensitive."
Investigation (#1758) found that under-stated the disclosure on two axes, so the
default-emitted route map is hardened in Stage 2:

1. **It is not just URLs.** Each entry is `{ url_path: "module.QualName" }` — it
   ships the *dotted view-class path* of every route. That is internal code
   structure (`myapp.admin.SecretDashboardView`), beyond the URL itself.
2. **No auth filtering.** `_walk_liveview_routes` walks the whole URLconf and
   deliberately unwraps `login_required(as_view())`, so **login-gated and admin
   routes are included**, and the module-cached map is emitted to **every**
   client including anonymous visitors. An anonymous visitor to a public page
   therefore enumerates the app's entire route table — including routes they
   cannot access — plus each view's class name. This is recon-grade information
   disclosure (not an auth bypass: the mount path allowlists modules at
   `websocket.py:1726` and views still enforce auth at mount, so it leaks
   *existence + naming*, not access).

**Decision:** `get_route_map_script(request)` — the single funnel both template
engines use via `_client_config_html` — auth-filters the emitted map: a route
whose `LiveView` declares `login_required` / `permission_required` (or whose
callback is decorator-gated) is **omitted** unless `request.user` satisfies it.
Public routes are unchanged; the filter **fails closed** (omits gated routes)
when there is no request/user. This removes gated-route enumeration and the
view-class disclosure of routes the user cannot reach, while keeping the
zero-round-trip SPA-nav behavior for the routes the user *can* reach.

The deeper protocol change — client sends the *URL path* and the server resolves
path→view authoritatively, removing client-sent view paths and the residual
view-class disclosure for accessible routes entirely — is noted as a possible
future Stage 3, not required by Stage 2 (it is a wire-protocol change with
regression surface across existing `dj-navigate`).

### Default-on is deferred (not decided here)
`auto_navigate` ships **opt-in** and must soak at least one release before any
consideration of becoming default-on. Flipping link behavior for every app by
default is a directional change with broad blast radius — exactly the silent
behavior change that triggered this ADR — so it is earned via opt-in soak, and a
default-on flip (if ever) is a **future-major** decision that will amend this ADR.

## Consequences

**Positive**
- `dj-navigate` works as documented with zero wiring (v1.0.2); with
  `auto_navigate` on, plain `<a href>` gets SPA nav with no djust attributes at
  all — Turbo-Drive-level DX, server-rendered over the existing WebSocket.
- One clear canonical nav model; the two-stories confusion is resolved.
- Composes with ADR-013 (View Transitions): auto-nav patches flow through
  `applyPatches`, which already supports the VT wrap.

**Negative / risks**
- The route map is exposed to the client by default (it already was for
  `live_session` apps). ⚠️ Originally assessed as "URLs are public, not
  sensitive" — corrected by #1758: the map also ships view-class paths and, in
  Stage 1, included auth-gated routes for anonymous clients (recon disclosure).
  Stage 2 auth-filters the emitted map (see "Route-map exposure hardening"
  above). The map remains empty-safe (no script when an app has no LiveViews).
- Auto-interception is opt-in but, once enabled, changes the behavior of *every*
  link — the same-view→`live_patch` branch and the skip-rule matrix
  (modifier/middle-click new-tab, downloads, external) are correctness-critical
  and get adversarial review (#1734).
- Walking the URLconf to build the map adds a one-time (cached) cost at first
  render; negligible (URLconf is static at runtime).
- Repositioning external TurboNav as interop (not co-recommended) is a
  documentation/stance change some existing users may notice (#1735).

## Alternatives considered

- **Path 1 (Foundation-only):** ship the zero-wiring route map, stop, never add
  auto-interception. Rejected as the *primary* path because it leaves the
  plain-`<a>` DX win unrealized — but it is exactly Stage 1 of this decision, so
  nothing is lost by sequencing.
- **Path 3 (Default-on Turbo-Drive in v1.1.0):** ship foundation + auto_navigate
  default-on together, deprecate external TurboNav outright. Rejected for now:
  flipping nav behavior for every app on a *minor* with no soak repeats the
  silent-change class that caused this ADR. It remains the plausible eventual
  destination, to be revisited (as a future major) after opt-in soak.
