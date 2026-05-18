---
title: "Upgrading to djust 1.0"
slug: upgrade-to-1.0
section: guides
order: 18.2
level: reference
description: "What the djust 1.0 SemVer commitment means for a 0.9.x app, the deprecated symbols, and the new accessibility checks. 1.0 is a consolidation release with no breaking changes from 0.9.7."
---

# Upgrading to djust 1.0

**TL;DR:** djust 1.0 is a **consolidation release**. There are **no breaking
changes from 0.9.7** — if your app runs on 0.9.7, it runs on 1.0 unchanged.
The headline of 1.0 is a *promise*, not a migration: starting at 1.0.0, code
written against djust's public API keeps working across every 1.x release.

## 1.0 is a consolidation release

There is nothing to migrate. 1.0 caps the v0.9.x audit-driven bake — an
API-stability policy, a framework-wide accessibility pass, and a pre-1.0
security sweep — without changing any public behavior. Bump your dependency
and run your test suite; that is the whole upgrade.

## What 1.0 means: the SemVer commitment

The 0.x series carried **no** stability guarantee — per SemVer, pre-1.0
versions make no promise, and djust used that latitude. The commitment takes
effect at **1.0.0**:

- A symbol in djust's **public API surface** is never removed or changed
  incompatibly in a MINOR or PATCH release. Breaking a public symbol is a
  MAJOR-only change.
- New public symbols may be added in any MINOR release (additive changes are
  backward-compatible).
- Anything underscore-prefixed, or reachable only by reaching into a
  submodule, is **internal** and may change at any time.

For the full definition of the public API surface, see
[`docs/API_STABILITY.md`](https://github.com/johnrtipton/djust/blob/main/docs/API_STABILITY.md)
and the [API Stability & Deprecations guide](api-stability.md).

## Deprecated symbols

Three symbols are deprecated as of 0.9.7. **All three still work in the 1.x
line** — each carries the 0.x carve-out removal floor of **`>= 1.1.0`**, so
nothing is removed in 1.0 itself.

| Symbol | Use instead | Removed no earlier than |
| --- | --- | --- |
| `@event` decorator | `@event_handler` | 1.1.0 |
| `LiveViewForm` | `django.forms.Form` (form reactivity comes from `FormMixin` on the `LiveView`) | 1.1.0 |
| `_legacy` theming module (`THEMES`, `get_theme()`, `list_themes()`) | `DESIGN_SYSTEMS` / `get_design_system` / `get_all_design_systems` from `djust.theming.theme_packs` | 1.1.0 |

Each migration is mechanical — see
[`docs/API_STABILITY.md` § "Currently Deprecated Symbols"](https://github.com/johnrtipton/djust/blob/main/docs/API_STABILITY.md#currently-deprecated-symbols)
for before/after code for every symbol.

## New in the 1.0 line: accessibility system checks

djust 1.0 ships the `Y` (accessibility) category of Django system checks
(`Y001`, `Y002`). When you run `manage.py check`, they scan your templates for
common accessibility gaps.

- They emit **warnings only** — they never fail `manage.py check`.
- They are **suppressible** like any Django check (via `SILENCED_SYSTEM_CHECKS`).

See the [Accessibility guide](accessibility.md) for the full check list and
remediation guidance.

## Action items

1. Bump your `djust` dependency to `1.0.x` and run your test suite — nothing
   should change.
2. Surface any deprecated-symbol usage before the 1.1 line removes it:

   ```bash
   python -W error::DeprecationWarning manage.py check
   ```

   This turns djust's deprecation warnings into errors, making every `@event`
   / `LiveViewForm` / `_legacy` theming call site visible.
3. Migrate any flagged symbols at your own pace — they keep working through
   the entire 1.x line.
