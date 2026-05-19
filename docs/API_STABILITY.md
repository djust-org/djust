# API Stability & Deprecation Policy

This document is the canonical, authoritative statement of djust's API-stability
commitment and its deprecation process. It defines exactly what the djust 1.0
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) promise covers, what
it explicitly does not cover, and how features are deprecated and removed.

Starting with djust **1.0.0**, djust adheres to Semantic Versioning. A 1.0
release is a deliberate stability commitment: application code written against
the public API of djust 1.0 will keep working across every 1.x release.

> For a lighter, user-facing walkthrough, see the
> [API Stability guide](website/guides/api-stability.md). This document is the
> full reference — the guide links back here for anything substantive.

## Table of Contents

1. [The SemVer Commitment](#the-semver-commitment)
2. [The Public API Surface](#the-public-api-surface)
3. [What Is NOT Covered](#what-is-not-covered)
4. [Deprecation Policy](#deprecation-policy)
5. [The Support Window](#the-support-window)
6. [Currently Deprecated Symbols](#currently-deprecated-symbols)
7. [Template-Syntax Deprecations](#template-syntax-deprecations)
8. [Known Documented Items](#known-documented-items)

---

## The SemVer Commitment

Under Semantic Versioning, given a version `MAJOR.MINOR.PATCH`:

- **MAJOR** is incremented for incompatible (breaking) API changes.
- **MINOR** is incremented for backward-compatible feature additions.
- **PATCH** is incremented for backward-compatible bug fixes.

djust applies this strictly to its **public API surface** (defined below):

- A symbol in the public API surface is **never removed or changed
  incompatibly in a MINOR or PATCH release**. Removing or breaking a public
  symbol is a breaking change and may only happen in a MAJOR release.
- New public symbols may be **added** in any MINOR release (additive changes
  are backward-compatible).
- Bug fixes that correct behavior to match documented intent may ship in a
  PATCH release even if some code depended on the buggy behavior.

The 0.x series did **not** carry this guarantee — per SemVer, pre-1.0 versions
make no stability promise, and djust used that latitude. The commitment in this
document takes effect at **1.0.0**.

---

## The Public API Surface

The **public API** — the surface SemVer covers — is exactly the following:

1. **Top-level package exports.** Every name in `djust.__init__.py`'s `__all__`
   (58 symbols as of 0.9.7). `from djust import X` for any such `X` is a
   supported, stable import.

2. **The documented decorators.** Every name in `djust.decorators.__all__`
   (17 names). These include the core decorators (`event_handler`,
   `server_function`, `permission_required`, `rate_limit`, `reactive`,
   `state`, `computed`, `debounce`, `throttle`, `optimistic`, `cache`,
   `client_state`, `background`, `on_mount`), the deprecated alias `event`
   (see [Currently Deprecated Symbols](#currently-deprecated-symbols)), and the
   introspection predicates `is_event_handler` and `is_server_function`.

3. **Public `LiveView` / `LiveComponent` / `Component` methods.** The
   documented public methods and lifecycle hooks of these classes —
   `mount`, `get_context_data`, `render`, `handle_params`, `handle_info`,
   `start_async`, `as_view`, navigation/flash helpers, `get_state`,
   `get_object`, `has_object_permission`, `handle_tick`, and the
   `LiveComponent` lifecycle (`mount`, `set_parent`, `trigger_update`,
   `send_parent`, `unmount`, etc.). Subclassing `LiveView` /
   `LiveComponent` and overriding these hooks is the supported app-authoring
   pattern.

4. **Public mixins.** A mixin is part of the public API **if and only if it is
   re-exported from `djust.__init__.py`'s `__all__`** — for example
   `FormMixin`, `WizardMixin`, `DraftModeMixin`, `PresenceMixin`,
   `LiveCursorMixin`, `StreamingMixin`, `UploadMixin`, `FlashMixin`,
   `PageMetadataMixin`, `NotificationMixin`, `ReactMixin`,
   `LoginRequiredMixin`, and `PermissionRequiredMixin`. These are the opt-in
   mixins applications add to their own `LiveView` subclasses.

5. **Registered template tags and filters.** The tags/filters registered by
   djust's template libraries — the Django `templatetags/` libraries
   (`djust_formsets`, `djust_flash`, `djust_pwa`, `djust_tutorials`,
   `live_tags`) and the Rust-bridge `template_tags/` (`url`, `static`,
   `live_render`, `dj_flash`, `djust_markdown`, `djust_client_config`,
   PWA-head tags, etc.).

6. **Documented configuration keys.** The documented `LIVEVIEW_CONFIG` /
   `DJUST_CONFIG` settings keys.

7. **The WebSocket wire protocol** — treated as a versioned contract. It is
   snapshot-pinned in `crates/djust_vdom/tests/wire_protocol_snapshot.rs` and
   `python/djust/tests/test_wire_protocol_snapshots.py`. Changes to the wire
   format are reviewed for client compatibility.

If a symbol is reachable through one of the routes above and carries no
`experimental` / `provisional` marker (see
[Known Documented Items](#known-documented-items)), it is stable under this
policy.

---

## What Is NOT Covered

The following are **internal** — they may change or be removed in any release,
including MINOR and PATCH, without notice:

- **Any underscore-prefixed name.** Modules (`djust._deprecation`, and any
  other `djust._*`), classes, functions, methods, and attributes whose name
  begins with `_` are framework-internal. They are not part of the public
  contract even when technically importable.

- **`djust.mixins.*` internal-composition mixins.** The mixins reachable only
  via `from djust.mixins import X` — for example `TemplateMixin`,
  `ContextMixin`, `RustBridgeMixin`, `HandlerMixin`, `RequestMixin`,
  `JITMixin`, `ComponentMixin`, `PostProcessingMixin`, `StreamsMixin`,
  `PushEventMixin`, `ModelBindingMixin`, `NavigationMixin`, and the
  `StickyChildRegistry` — are framework-internal building blocks composed
  **into** the `LiveView` base class. Application code subclasses `LiveView`,
  not these mixins directly. Only mixins re-exported from the top-level
  `djust` package (see point 4 above) are public. This is documented audit
  finding **F4** (see [Known Documented Items](#known-documented-items)).

- **Rust crate internals.** The internals of the `djust_core`,
  `djust_templates`, and `djust_vdom` crates and the `djust._rust` bindings
  beyond the snapshot-pinned wire protocol.

- **Anything reachable only by reaching *into* a submodule** that the
  top-level package does not re-export, and that this document does not list
  as public.

- **Debug-panel, dev-server, and hot-reload internals.** Developer tooling
  surfaces (debug panel, hot view replacement plumbing, error overlay
  internals) are not API-stable.

- **Anything documented as experimental or provisional.** As of 0.9.7 there
  are **zero** such symbols — the public surface is clean for the 1.0 freeze.

---

## Deprecation Policy

When a public symbol must be removed, it goes through a deprecation cycle
first. djust deprecations follow these hard rules:

### How a deprecation is announced

A feature deprecated in release *X.Y.0* is announced through **all** of:

1. **A runtime `DeprecationWarning`** emitted via the shared internal helper
   `djust._deprecation.warn_deprecated`. Routing every deprecation through one
   helper guarantees the message format, warning category, and `stacklevel`
   are consistent — the warning points at the *caller's* frame, not djust's
   internals.
2. **A `.. deprecated:: X.Y` docstring marker** on the deprecated symbol,
   naming the version it was deprecated in.
3. **A `### Deprecated` entry in `CHANGELOG.md`** for that release.
4. For **template-syntax** deprecations, a `djust_check` system-check warning
   (see [Template-Syntax Deprecations](#template-syntax-deprecations)).

djust uses plain **`DeprecationWarning`** — visible by default under `-W` and
surfaced by pytest — paired with a concrete removal version.
**`PendingDeprecationWarning` is not used by djust**: rather than emit a
"pending" warning, djust deprecates only once it can commit to a concrete
removal floor.

### The migration-path rule

**Every deprecation must ship with a concrete migration path.** The warning
message and the CHANGELOG `### Deprecated` entry must both name the
replacement ("use X instead"). There is no deprecation without a documented
way forward. The `warn_deprecated` helper takes an `instead=` argument
precisely to make this the easy default.

---

## The Support Window

This is the core stability promise:

- **A symbol deprecated during the `1.x` series is removed no earlier than the
  next MAJOR release.** Concretely: **deprecated-in-`1.Y` → removed no earlier
  than `2.0.0`.** A deprecated public symbol is never removed in a MINOR or
  PATCH release.

  This is the strict SemVer reading — removing a public symbol is a breaking
  change, and breaking changes are MAJOR-only. It is the strongest stability
  promise djust can make, and it is the appropriate one for a framework making
  a deliberate 1.0 commitment. A deprecated symbol may linger for the rest of a
  major series; that is the deliberate cost of the guarantee.

- **0.x carve-out.** Three symbols were deprecated during the `0.x` series
  (`@event`, `LiveViewForm`, and the `_legacy` theming module — see below).
  SemVer permitted removing them in any `0.x` release. Rather than yank them at
  `1.0.0`, the policy gives 1.0 adopters one full minor cycle of overlap:
  **these three symbols are scheduled for removal no earlier than `1.1.0`.**

---

## Currently Deprecated Symbols

As of 0.9.7, three symbols are deprecated. All three carry the 0.x carve-out
removal floor of **`>= 1.1.0`**.

| Symbol | Deprecated since | Removed no earlier than | Replacement |
| --- | --- | --- | --- |
| `@event` decorator | 0.3 | **1.1.0** | `@event_handler` |
| `LiveViewForm` | 0.3 | **1.1.0** | `django.forms.Form` |
| `_legacy` theming module (`THEMES`, `get_theme()`, `list_themes()`) | 0.5 | **1.1.0** | `DESIGN_SYSTEMS` / `get_design_system` / `get_all_design_systems` from `djust.theming.theme_packs` |

### `@event` → `@event_handler`

`@event` is a deprecated alias for `@event_handler`. It has identical behavior;
only the name differs. Migration is a mechanical rename:

```python
# Before
from djust.decorators import event

@event
def increment(self):
    self.count += 1

# After
from djust.decorators import event_handler

@event_handler
def increment(self):
    self.count += 1
```

### `LiveViewForm` → `django.forms.Form`

`LiveViewForm` adds no functionality over Django's own `forms.Form`. djust's
form reactivity comes from `FormMixin` on the `LiveView`, not from a special
form base class. Migrate by subclassing `django.forms.Form` directly:

```python
# Before
from djust.forms import LiveViewForm

class ContactForm(LiveViewForm):
    name = forms.CharField()

# After
from django import forms

class ContactForm(forms.Form):
    name = forms.CharField()
```

> **Note on the corrected warning text.** Before djust 1.0, the `LiveViewForm`
> deprecation warning said it would be "removed in djust 0.4" — a version
> already long past. That stale text has been corrected to the policy-compliant
> `>= 1.1.0` floor.

### The `_legacy` theming module

The `djust.theming.themes._legacy` module — its `THEMES` dict and the
`get_theme()` / `list_themes()` functions — is wholly deprecated. Every access
emits a `DeprecationWarning`. Migrate to the design-system API:

```python
# Before
from djust.theming.themes._legacy import get_theme

theme = get_theme("dark")

# After
from djust.theming.theme_packs import get_design_system

theme = get_design_system("dark")
```

---

## Template-Syntax Deprecations

djust has a **second** deprecation track, separate from Python
`DeprecationWarning`s: deprecations of **template syntax**. These cannot be
announced via a Python warning (templates are not Python), so they are surfaced
through djust's system-check framework and reported by
`manage.py djust_check`.

Two such template-syntax deprecations exist today:

- **T001** — the `@click` / `@input` event-attribute shorthand. Migrate to the
  `dj-click` / `dj-input` directive form.
- **T014** — the `data-dj-id` attribute form. Migrate to `dj-id`.

Both are reported as system-check warnings with a `fix_hint` naming the
replacement syntax, mirroring the migration-path rule above. This is documented
audit finding **F6**.

---

## Known Documented Items

Items surfaced by the 1.0 public-API audit are recorded here. F4 is a known,
deliberate fact about the API surface; F3 was a deferred additive change that
has since shipped (see below).

### F3 — four decorators re-exported from the top-level `djust` package ✅ resolved

The decorators `optimistic`, `cache`, `client_state`, and `background` are
**stable** and part of the public API. They were originally reachable only via
`from djust.decorators import ...`; as of
[#1489](https://github.com/djust-org/djust/issues/1489) they are also
re-exported from the top-level `djust` package's `__all__`, so the canonical,
supported import path is now simply:

```python
from djust import optimistic, cache, client_state, background
```

The `from djust.decorators import ...` path continues to work unchanged. The
1.0 public-API audit originally recorded F3 as a deliberate 1.0-freeze
deferral; the re-export is a purely additive, SemVer-safe change and was
implemented in the v1.0.0rc4 cycle.

### F4 — `djust.mixins.*` internal-composition mixins are not public

As stated in [What Is NOT Covered](#what-is-not-covered), the mixins reachable
only via `from djust.mixins import X` are framework-internal composition
building blocks. They are exported from `djust.mixins.__all__` for the
framework's own internal use, but they are **not** part of the stable public
API. Application code subclasses `LiveView` (which composes these mixins
internally) and uses only the public mixins re-exported from the top-level
`djust` package.

---

## See Also

- [API Stability guide](website/guides/api-stability.md) — the user-facing
  walkthrough of this policy.
- [`CHANGELOG.md`](../CHANGELOG.md) — `### Deprecated` entries record every
  deprecation.
- [Security Guidelines](SECURITY_GUIDELINES.md) — the sibling contributor
  policy document.
- [System Checks Reference](system-checks.md) — all `djust_check` check IDs,
  including the template-syntax deprecation checks T001 and T014.
