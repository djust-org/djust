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
7. [Removed in 2.0.0](#removed-in-200)
8. [Template-Syntax Deprecations](#template-syntax-deprecations)
9. [Known Documented Items](#known-documented-items)

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
   (59 symbols as of 2.0). `from djust import X` for any such `X` is a
   supported, stable import.

2. **The documented decorators.** Every name in `djust.decorators.__all__`
   (19 names as of 2.0). These include the core decorators (`event_handler`,
   `action`, `server_function`, `push_handler`, `permission_required`, `rate_limit`,
   `state`, `computed`, `debounce`, `throttle`, `cache`, `background`,
   `on_mount`) and the
   introspection predicates `is_event_handler`, `is_action`,
   `is_server_function` and `is_push_handler`. Two names are deliberately in
   `djust.decorators.__all__` only, not at the top level: `StateProperty` (the
   type `state()` returns) and `alters_data` (the Django-parity marker).

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
  **these three symbols were scheduled for removal no earlier than `1.1.0`**,
  and djust `2.0.0` removed them (see [Removed in 2.0.0](#removed-in-200)).

### The 1.4 bridge and 2.x (direction, 2026-10-19)

The project owner settled this direction for the 1.4 → 2.0 transition. The
SemVer rules above are unchanged by it.

> **Amended 2026-11-11.** There will be no 1.4 release: the bridge described
> below shipped in 1.3.x (every 2.0 removal warns "since 1.3"), `main` is the 2.0
> line, and the 1.3 line takes security and serious fixes only. The rules in this
> section still hold for 1.3.x: it keeps every import, default and `__all__`
> name. The migration guide and checker are 2.0 work (#4022, #4023).

- **1.4 keeps compatibility.** It keeps every existing import, every default,
  and the current `djust.__all__` and `djust.decorators.__all__`, so the
  star-import surface (`from djust import *`) is the same as 1.3's. 1.4 adds
  warnings that name each 2.0 removal and its concrete replacement, a
  migration guide and checker, and an opt-in for the strict contracts. Every
  new warning names the release it was deprecated in, its replacement and its
  removal floor, as [the migration-path rule](#the-migration-path-rule)
  requires.
- **Removing a name from `__all__` is a removal.** It changes what
  `from djust import *` binds, so it counts as removing a public symbol and
  may only happen in a MAJOR release. Moving a name out of `__all__` in 1.4
  is not allowed, even if the name stays importable.
- **2.x has no broad compatibility mode.** 2.0 removes the announced legacy
  surfaces. It does not ship a `djust.compat` module or setting that keeps
  the legacy runtime available through 2.x. A narrow, non-behavioural alias
  needs its own review and must not keep a legacy code path reachable.

---

## Currently Deprecated Symbols

The one access pattern deprecated during the `1.x` series (dict-style
`block.super` reads in a bridged tag) was removed in `2.0.0`; see below. One
value is deprecated on the 2.0 line:

### `exposure_policy = "legacy"` → the default, `"explicit"`

`LiveView.exposure_policy` defaults to `"explicit"` since djust 2.0 (it was
`"legacy"` in 1.x). This is a changed default, made in a MAJOR release: a view
that states no policy now gets a template context, session state and browser
data made only of what it declares. See
[explicit exposure](website/state/explicit-exposure.md).

`"legacy"` is still accepted when a class states it, so a 1.x view keeps its
behaviour by adding one line:

```python
class OldView(LiveView):
    exposure_policy = "legacy"
```

Stating it raises a `DeprecationWarning` through `warn_deprecated`, once for
each class that states it, when the class is defined (or, for a policy assigned
to the class afterwards, the first time a view of that class is constructed).
The message names the class, the replacement and the guide. A class shipped in
djust that states `"legacy"` does not warn. The value is accepted through the
2.0 pre-releases and removed in `2.0.0`: 2.x has no legacy runtime (see
[the 2.x direction](#the-14-bridge-and-2x-direction-2026-10-19)).

System check `djust.V028` (Warning) also lists each application class that
states `"legacy"`, on every `manage.py check` and server start, with the views
that inherit it and a pointer to `djust_exposure_inventory`; a warning raised
at import time is easy to miss. djust's own classes are not reported.

This warning and the check are first raised on the 2.0 line. The 1.3.x releases
raise neither: there `"legacy"` is the default and stating it is not deprecated.

---

## Removed in 2.0.0

The three symbols that carried the 0.x carve-out floor were removed in
djust `2.0.0`. Importing them now raises `ImportError` (or `AttributeError` for
`djust.event` / `djust.LiveViewForm`). The dict-style `block.super` reads,
deprecated in 1.3, were removed too, as were the eight component mixins and the
`social_auth_providers` context processor, which had also warned since 1.3.
The inert `@optimistic` and `@client_state` decorators were removed without a
1.3 warning; see their section.

### `@event` → `@event_handler`

`@event` was a deprecated alias for `@event_handler`, with identical behavior.
Migration is a mechanical rename:

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

djust's form reactivity comes from `FormMixin` on the `LiveView`, not from a
special form base class; `LiveViewForm` only added two helpers to Django's
`forms.Form`. Migrate by subclassing `django.forms.Form` directly. If you called
`form.get_field_errors_json()`, use `json.dumps(form.errors)`; for
`form.get_field_value(name)`, read `form.cleaned_data` (or `form.data` before
validation):

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

### The `_legacy` theming module

The `djust.theming.themes._legacy` module — its `THEMES` dict and the
`get_theme()` / `list_themes()` functions, and the `Theme`, `Typography`,
`Spacing`, `BorderRadius`, `Shadows`, `Animations` and `ComponentStyles` classes
that `djust.theming.themes` re-exported from it — is gone. Migrate to the
design-system API:

```python
# Before
from djust.theming.themes._legacy import get_theme

theme = get_theme("dark")

# After
from djust.theming.theme_packs import get_design_system

theme = get_design_system("dark")
```

### JSX-style `<Rust…/>` template tags → `djust.components.ui`

A template tag such as `<RustButton id="save" label="Save" />` renders through a
Rust component path that djust 2.0.0 removes. Each render of one emits a
`DeprecationWarning` (deprecated since 1.3, removed no earlier than 2.0.0). In
2.0 the tag is a template error. Render the matching `djust.components.ui`
component instead:

```python
# Before (template)
# <RustButton id="save" label="Save" variant="primary" />

# After (view)
from djust.components.ui import Button

save_button = Button("Save", variant="primary")  # template: {{ save_button }}
```

### Dict-style `block.super` reads in a bridged tag → `block.super()`

Before `{{ block.super }}` became lazy, a Python-bridged tag received `block` as a
`dict` (`{"super": "<rendered parent>"}`). It now receives a lazy object with Django's
own `BlockNode` shape, whose `super()` method renders the parent when it is read.
The dict-style reads were deprecated in 1.3 and removed in 2.0.0:

- `block["super"]` raises `TypeError` (not subscriptable), with a message naming
  `block.super()`, and `dict(block)` raises the same error;
- `block.get("super")`, `block.values()` and `block.items()` raise `AttributeError`.

None of them renders the parent. Django's own resolution (`Variable("block.super")`,
`{% blocktranslate with s=block.super %}`) is unaffected: it falls through the
`TypeError` to `super()` exactly as it does for a `BlockNode`.

```python
# Before
parent_html = context["block"]["super"]

# After
parent_html = context["block"].super()
```

### The eight component mixins → `djust.components.descriptors`

`AccordionMixin`, `TabsMixin`, `ModalMixin`, `CollapsibleMixin`, `SheetMixin`,
`DropdownMixin`, `TooltipMixin` and `CarouselMixin`, with their `*State`
classes, are gone from `djust.components` and `djust.components.mixins`
(deprecated since 1.3). Importing one raises `ImportError`. `ComponentMixin`,
`TypedState` and `DataTableMixin` stay.

Declare the matching descriptor on the view instead. It holds the state and
registers its event handler; the template reads the state off the attribute.

```python
# Before
from djust.components.mixins import TabsMixin

class SettingsView(TabsMixin, LiveView):
    def mount(self, request, **kwargs):
        self.init_tabs("nav", active="general")

# After
from djust.components.descriptors import Tabs

class SettingsView(LiveView):
    nav = Tabs(active="general")  # template: {{ nav.active }}
```

A descriptor handles one event, so it does not replace every handler a mixin
defined. The handlers with no descriptor equivalent are now the application's
to define, or to route to the descriptor's event through the tag's `*_event`
argument:

| Mixin handlers | Descriptor event | Not handled by the descriptor |
| --- | --- | --- |
| `accordion_toggle` | `accordion_toggle` | none |
| `set_tab` | `set_tab` | none |
| `toggle_collapsible` | `toggle_collapsible` | none |
| `open_modal`, `close_modal`, `toggle_modal` | `toggle_modal` | `open_modal`, `close_modal` (the `{% modal %}` default `close_event`) |
| `open_sheet`, `close_sheet` | `toggle_sheet` | `open_sheet`, `close_sheet` (the `{% sheet %}` default `close_event`) |
| `toggle_dropdown`, `close_dropdown` | `toggle_dropdown` | `close_dropdown` |
| `show_tooltip`, `hide_tooltip` | `toggle_tooltip` | `show_tooltip`, `hide_tooltip` |
| `carousel_prev`, `carousel_next`, `carousel_go` | `carousel_go` | `carousel_prev`, `carousel_next` (the `{% carousel %}` default `prev_event` / `next_event`) |

The `init_*()` and `get_*_ctx()` helpers have no equivalent: the descriptor's
constructor arguments and attribute replace them.

### `djust.auth.social.social_auth_providers` → `{% auth_providers auth %}`

The context processor and the `djust.auth.social` module are gone (deprecated
since 1.3). A `TEMPLATES` entry that still lists the processor fails startup
with `ImproperlyConfigured` naming the replacement. Delete the entry and render
the buttons with the account kit's tag:

```html
<!-- Before: settings listed "djust.auth.social.social_auth_providers" -->
{% for provider in oauth_providers %}
  <a href="{{ provider.login_url }}">{{ provider.icon }} {{ provider.label }}</a>
{% endfor %}

<!-- After -->
{% load djust_auth %}
{% auth_context as auth %}
{% auth_providers auth %}
```

`auth.providers` is the same list for a template that renders its own buttons.
Each item has `id` (was `name`), `label` and `login_url`. The built-in backends
leave its `icon` empty, so take the icon from the `auth_provider_icon` filter
(`{{ provider.id|auth_provider_icon }}`).
Python code that called the function can read
`get_account_backend().providers(request)` from `djust.auth.accounts.registry`.

### `_drain_announcements` / `_drain_focus` → a live region and `JS.focus()`

The WebSocket consumer and the SSE runtime drain two duck-typed methods on the
view behind `hasattr`: `_drain_announcements()` (sends an `accessibility` frame)
and `_drain_focus()` (sends a `focus` frame). No 1.x release has ever defined
either: `mixins/accessibility.py` (with `announce()` / `focus()`) shipped only in
the 0.6.0 and 0.7.0 tags, which are not ancestors of `main`, and no tag from 0.9.0
on has it. The shipped client assigns no `window.djust.accessibility`, so the
frames reach only an object the application's own script defines, and the HTTP
fallback never drained them.

The names are underscore-prefixed, which this policy treats as internal. They are
deprecated rather than deleted because the `accessibility` and `focus` frames are
part of the wire protocol (point 7 above) and `djust.d.ts` types
`window.djust.accessibility`, so an application can have supplied both halves.

Defining either method on a `LiveView` subclass (in its body or through a mixin)
emits one `DeprecationWarning` per hook at the `class` statement. A hook added
after the class exists (`setattr`, a decorator, an instance attribute) is not
detected and does not warn. Behaviour is unchanged until 2.0.0, where the flush is
removed. Migrate to a live region the view renders and `JS.focus()` through
`push_commands()` (see the [accessibility guide](website/guides/accessibility.md#announcing-a-change-and-moving-focus-from-the-server)).
The replacement is not identical: a live region announces only when its text
changes, so the same message sent in two turns in a row announces once, and
several messages in one turn must be rendered together. The guide shows the
workaround (alternate a no-break-space suffix).

```python
# Before
class OrderView(LiveView):
    def _drain_announcements(self):
        out, self._said = self._said, []
        return out

# After: render the message into <p role="status" aria-live="polite">{{ status }}</p>
class OrderView(LiveView):
    @event_handler()
    def save(self, **kwargs):
        self.status = "Order saved"
        self.push_commands(JS.focus("#order-summary"))
```

---

### `use_actors` and the actor system → the standard runtime

`use_actors = True` moved a view's state and rendering into a per-session Tokio
actor in the Rust extension. It was experimental, WebSocket-only, and could not
run under explicit exposure. djust `2.0.0` removed it, together with
`djust._rust.create_session_actor`, `get_actor_stats`, `SessionActorHandle` and
`SupervisorStatsPy`. The system checks `djust.V017` and `djust.V020`, which
only reported on actor views, are retired; their IDs are not reused.

A `LiveView` subclass that sets a truthy `use_actors` raises
`ImproperlyConfigured` when the class is created, naming the class. A value the
class-creation check cannot see (set on the instance, or assigned to the class
later) raises the same error right after `mount()`, on every transport. Delete
the attribute:

```python
# Before
class Dashboard(LiveView):
    use_actors = True

# After
class Dashboard(LiveView):
    ...
```

The view then runs on the standard runtime, which already runs one turn at a
time per connection. For work that should not block a turn, use
`start_async()` or `@background`. `use_actors = False` is accepted and has no
effect; delete it too. Importing one of the removed `djust._rust` names raises
`ImportError`.

### `@optimistic` and `@client_state` → nothing (they did nothing)

Both decorators only recorded metadata that nothing read: no client code
applied an optimistic update for `@optimistic`, and the client bus that was
to consume `@client_state` was deleted as dead code in #2680. A decorated
handler behaved exactly like an undecorated one. djust `2.0.0` removed both
(#4006). `from djust import optimistic`, `from djust.decorators import
client_state` and the attribute reads raise `ImportError` with a message that
names the replacement and this guide (so `hasattr()` raises rather than
returning `False`, as for the signed snapshot below). Delete the decorator; the
handler keeps working as before:

```python
# Before (removed in 2.0, #4006)
from djust.decorators import client_state, event_handler, optimistic

@optimistic
@event_handler
def increment(self, **kwargs):
    self.count += 1

@client_state(keys=["filter"])  # removed in 2.0, #4006
@event_handler
def update_filter(self, filter: str = "", **kwargs):
    self.filter = filter

# After
from djust.decorators import event_handler

@event_handler
def increment(self, **kwargs):
    self.count += 1

@event_handler
def update_filter(self, filter: str = "", **kwargs):
    self.filter = filter
```

For an update that shows in the browser before the server answers, use the
client-side `JS` commands (`dj-click` with a `JS` chain, or `optimistic_rules`
on a descriptor component). To keep two values in step, update both in one
handler; the one render sends both.

These were not deprecated in 1.3.x: no warning preceded the removal, because
the decorators had no behaviour a project could depend on.

### The legacy signed snapshot → `state(..., persist="client", client=True)`

A view that set `enable_state_snapshot = True` had every public attribute
signed (`djust.security.sign_snapshot`) and sent to the browser on the mount
frame and after each event. On back-navigation the browser echoed the token,
and `LiveView._restore_snapshot()` applied it in place of `mount()`. djust
`2.0.0` removed that snapshot: `djust.security.state_snapshot`
(`sign_snapshot`, `unsign_snapshot`, `SNAPSHOT_SALT`),
`LiveView._restore_snapshot()`, `NonPersistableStateError` and the `strict=`
argument of `LiveView._capture_snapshot_state()`.

Importing `sign_snapshot`, `unsign_snapshot` or `SNAPSHOT_SALT` from
`djust.security` raises `ImportError` naming the replacement; so does reading
one as an attribute, so `hasattr()` raises rather than returning `False` (an
`AttributeError` from a module `__getattr__` would lose the message on
`from ... import`). A view class that defines `_restore_snapshot()` is refused
with `ImproperlyConfigured`, when the class is created and on the mount path. A legacy view no
longer sends a token, and a token a 1.x server signed is refused.
Back and a reconnect still restore an `enable_state_snapshot` view from the
state saved in the session, which this release keeps. Where nothing is saved
there (`LIVEVIEW_CONFIG['view_state_in_session'] = False`, no or an expired
session, an unresolved tenant), the view now mounts fresh instead of restoring
from the token.
Declare what the browser may hold:

```python
# Before (1.3)
class WizardView(LiveView):
    enable_state_snapshot = True

    def mount(self, request, **kwargs):
        self.step = 1

# After
class WizardView(LiveView):
    step = state(1, persist="client", client=True)

    def get_context_data(self, **kwargs):
        return {"step": self.step}
```

The token is still signed, bound and aged by the server; only its contents
changed. `get_max_age` and `DEFAULT_MAX_AGE` stay importable from
`djust.security`. `DJUST_STATE_SNAPSHOT_MAX_AGE` is now read in one place: a
whole number of seconds from 1 to 86400 is used as given, a larger value is
capped at 86400, and anything else falls back to 3600 with a warning.
`LiveView._should_restore_snapshot()` remains the hook that can refuse a
token.

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

All are reported as system-check warnings with a `fix_hint` naming the
replacement syntax, mirroring the migration-path rule above. This is documented
audit finding **F6**.

---

## Known Documented Items

Items surfaced by the 1.0 public-API audit are recorded here. F4 is a known,
deliberate fact about the API surface; F3 was a deferred additive change that
has since shipped (see below).

### F3 — four decorators re-exported from the top-level `djust` package ✅ resolved

The decorators `cache` and `background` are **stable** and part of the public
API. (`optimistic` and `client_state` were in this group until djust 2.0
removed them; see [Removed in 2.0.0](#removed-in-200).) They were originally reachable only via
`from djust.decorators import ...`; as of
[#1489](https://github.com/djust-org/djust/issues/1489) they are also
re-exported from the top-level `djust` package's `__all__`, so the canonical,
supported import path is now simply:

```python
from djust import cache, background
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

## Experimental shared clocks (1.3.x)

`djust.clocks` (`RoomClock`, `ClockTick`, `Publish`, `Stop`, `SharedPoll`),
`PresenceMixin.room_clock`, the `clock_workers` pool setting, and the clock
helpers in `djust.testing` are opt-in and **experimental for at least one minor
release**. They are an explicit exception to the stable 1.x surface while the
API is evaluated. Existing per-session ticks and push/presence behavior retain
their stability commitments. Phases 1–3 are process-local; distributed ownership
is deferred.
