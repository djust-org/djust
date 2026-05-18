---
title: "API Stability & Deprecations"
slug: api-stability
section: guides
order: 18
level: reference
description: "What djust's 1.0 SemVer commitment covers, how features are deprecated, and the support window for deprecated APIs"
---

# API Stability & Deprecations

**TL;DR:** Starting with djust **1.0.0**, code written against djust's public
API keeps working across every 1.x release. Anything underscore-prefixed or
reachable only by reaching into a submodule is internal and may change at any
time. Deprecated APIs survive until at least the next MAJOR release.

This guide is the user-facing walkthrough. For the full, authoritative
reference — the exact public-surface definition and every rule — see
[`docs/API_STABILITY.md`](https://github.com/johnrtipton/djust/blob/main/docs/API_STABILITY.md).

## The 1.0 commitment

djust 1.0 follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Given `MAJOR.MINOR.PATCH`:

- **MAJOR** — breaking changes only.
- **MINOR** — new, backward-compatible features.
- **PATCH** — backward-compatible bug fixes.

A public symbol is **never removed or changed incompatibly in a MINOR or PATCH
release**. New public symbols may be added in any MINOR release.

> The 0.x series made no such promise — that is what SemVer's pre-1.0 latitude
> is for. The commitment starts at 1.0.0.

## What counts as "public"

The public API — the surface SemVer covers — is:

- Everything exported from the top-level `djust` package
  (`from djust import X`).
- The documented decorators in `djust.decorators` — `@event_handler`,
  `@server_function`, `@permission_required`, `@rate_limit`, `@reactive`,
  `@state`, `@computed`, `@debounce`, `@throttle`, `@optimistic`, `@cache`,
  `@client_state`, `@background`, `@on_mount`.
- The documented public methods and lifecycle hooks of `LiveView`,
  `LiveComponent`, and `Component`.
- The mixins re-exported from the top-level `djust` package (`FormMixin`,
  `WizardMixin`, `StreamingMixin`, `PresenceMixin`, and the rest).
- The registered template tags and filters.
- The documented `LIVEVIEW_CONFIG` / `DJUST_CONFIG` settings keys.
- The WebSocket wire protocol, as a versioned contract.

## What is internal (not covered)

These may change in **any** release without notice:

- **Anything underscore-prefixed** — modules (e.g. `djust._deprecation`),
  classes, functions, and attributes whose name starts with `_`.
- **`djust.mixins.*` internals** — the composition mixins reachable only via
  `from djust.mixins import X`. These are framework building blocks composed
  *into* `LiveView`. Your app subclasses `LiveView`; it does not use these
  directly.
- **Rust crate internals** beyond the snapshot-pinned wire protocol.
- **Debug-panel, dev-server, and hot-reload internals.**

If you only ever `from djust import ...` and `from djust.decorators import ...`,
you are on the stable surface.

## How deprecations work

When a public API has to go away, it is deprecated first — never removed
outright. A deprecation is announced through all of:

1. A runtime `DeprecationWarning` (visible under `python -W` and in pytest).
2. A `.. deprecated:: X.Y` marker in the symbol's docstring.
3. A `### Deprecated` entry in the [changelog](https://github.com/johnrtipton/djust/blob/main/CHANGELOG.md).

Every deprecation **names its replacement** — there is no deprecation without a
migration path. djust uses plain `DeprecationWarning` with a concrete removal
version; it does not use `PendingDeprecationWarning`.

### The support window

- **Deprecated in `1.Y` → removed no earlier than `2.0.0`.** A deprecated
  public symbol is never removed in a MINOR or PATCH release. It survives until
  at least the next MAJOR.
- **0.x carve-out.** Three symbols deprecated back in the 0.x series get one
  full minor cycle of overlap rather than being yanked at 1.0 — they are
  scheduled for removal **no earlier than `1.1.0`**.

## Currently deprecated

| API | Replacement | Removed no earlier than |
| --- | --- | --- |
| `@event` | `@event_handler` | 1.1.0 |
| `LiveViewForm` | `django.forms.Form` | 1.1.0 |
| `_legacy` theming module (`THEMES`, `get_theme()`, `list_themes()`) | `djust.theming.theme_packs` (`get_design_system`, etc.) | 1.1.0 |

Migrating is mechanical in every case — see the
[full reference](https://github.com/johnrtipton/djust/blob/main/docs/API_STABILITY.md#currently-deprecated-symbols)
for before/after snippets.

## Template-syntax deprecations

Template syntax can't emit a Python warning, so djust deprecates it through
system checks instead. Run `manage.py djust_check` to see them. Two exist
today: **T001** (`@click` / `@input` → `dj-click` / `dj-input`) and **T014**
(`data-dj-id` → `dj-id`).

## See also

- [`docs/API_STABILITY.md`](https://github.com/johnrtipton/djust/blob/main/docs/API_STABILITY.md) — the full policy reference.
- [System Checks Reference](https://github.com/johnrtipton/djust/blob/main/docs/system-checks.md) — all `djust_check` IDs.
