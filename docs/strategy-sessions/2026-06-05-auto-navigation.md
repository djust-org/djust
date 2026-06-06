# Strategy Session — Automatic SPA Navigation

**Date**: 2026-06-05
**Mode**: deep
**Trigger**: on-demand (surfaced from a downstream consumer integration)
**Slug**: auto-navigation
**Outcome**: **Path 2 — Foundation + opt-in `auto_navigate`** (chosen by user, = recommendation)
**Artifacts**: ADR-021 (Proposed); issues #1733 (foundation, v1.0.2-1 → ships in 1.0.2), #1734 (`auto_navigate`, v1.1.0), #1735 (nav-story reconcile, v1.1.0)

## What triggered this

A downstream production app (rent tracker, djust 1.0.2rc1) added `dj-navigate`
per the docs and got **silent full-page reloads** instead of SPA navigation. The
investigation found three framework problems:

1. `dj-navigate` requires a client route map (`window.djust._routeMap`) that is
   only built by manually wiring `live_session()` + emitting
   `get_route_map_script()` — **undocumented**: `navigation.md` never mentions the
   prerequisite and even labels `dj-navigate` "full page navigation".
2. `get_route_map_script`'s docstring cites a `{% djust_route_map %}` template tag
   that **does not exist** (doc-vs-code drift; the tag was never implemented — it
   500s in *both* engines, not just Rust).
3. djust ships a **second, competing** navigation story
   (`turbonav-integration.md`: external turbo.js, AJAX + `<main>` swap, WS
   teardown/reconnect per nav) with no canonical recommendation.

## Survey (where we are)

Native `dj-navigate`/`live_redirect` does SPA nav over the *existing* WebSocket
(light, preserves the connection) but is under-wired/undocumented; external
TurboNav is documented but heavier (per-nav WS reconnect). ADR-013 (View
Transitions, Accepted) means SPA patches already support the VT wrap. Action
Tracker #225/#1361 (open) wants `routeMap[pathname]` prototype-pollution
tightening — the same code an auto-route-map would expand. Client budget healthy.
Highest existing ADR: 020.

## Paths considered

| Path | Scope | Milestones | Risk | Verdict |
|------|-------|-----------|------|---------|
| **1 — Foundation-only** | Auto route map only | 1.0.2-1 | low | Rejected as *primary* — but it is exactly Stage 1 of Path 2, so nothing lost |
| **2 — Foundation + opt-in `auto_navigate`** (chosen) | Auto route map → opt-in Turbo-Drive interception + ADR + reconcile | 1.0.2-1 → 1.1.0 | med | **Chosen** |
| **3 — Default-on Turbo-Drive** | Foundation + `auto_navigate` default-on, deprecate external TurboNav | 1.1.0 | high | Rejected (for now) — behavior flip for every app on a minor, no soak; the eventual destination, revisited as a future major |

## Why Path 2

The foundation (auto route map) is unambiguously "make documented behavior work"
— low-risk, immediate DX win, ships in 1.0.2 now (drain bucket 1.0.2-1). `auto_navigate` is a
*directional* opinion that touches every link in every app, so it earns an ADR
and an opt-in soak before any default-on — both the split-foundation canon
(#1122) and the rent app's own silent-full-reload failure argue against flipping
nav behavior without a soak. Path 3's default-on is the likely eventual
destination, but earning it via opt-in soak first avoids repeating the exact
silent-behavior-change class that triggered this session.

## Decision → execution

- **v1.0.2-1** (ships in **1.0.2**): #1733 (foundation) — `dj-navigate` works with zero wiring.
- **v1.1.0**: #1734 (`auto_navigate`, opt-in, default off) + #1735 (reconcile nav
  stories), governed by **ADR-021**.
- Default-on is **not** decided here; it is a future-major decision that will
  amend ADR-021 after opt-in soak.
