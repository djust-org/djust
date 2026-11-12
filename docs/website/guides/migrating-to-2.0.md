---
title: "Migrating to djust 2.0"
slug: migrating-to-2.0
section: guides
order: 18.4
level: reference
description: "Move a 1.3.x app to djust 2.0: explicit state exposure, strict event parameters, and the names and settings 2.0 removes, one view at a time."
---

# Migrating to djust 2.0

djust 2.0 changes three things in an application:

1. **Explicit exposure.** An attribute on a view reaches the template, the
   session or the browser only when the view says so.
2. **Strict event parameters.** A handler's Python signature decides what an
   event may carry. Anything else is rejected before the handler runs.
3. **Removed names and settings.** Deprecated imports are gone, and a removed
   setting stops the server at startup with a message naming its replacement.

**Do the work on 1.3.x, before you install 2.0.** Both new contracts ship in
1.3 as opt-ins, and every replacement named in this guide exists there, unless
its row says otherwise. You can migrate one view and one handler at a time,
deploy between steps, and install 2.0 once nothing in the app still depends on
the old behaviour.

If you are already on a 2.0 pre-release with views you have not migrated,
state `exposure_policy = "legacy"` on them, or once on a base class they
inherit from, and then migrate them one at a time. Without it an unmigrated
view renders the names nothing supplies as empty, with no error. See
[Keeping the legacy policy](../state/explicit-exposure.md#keeping-the-legacy-policy).

Stating `"legacy"` raises a `DeprecationWarning` when the class is defined. If
your tests run with warnings as errors, that fails the import of every module
that states it, so allow this one warning until the views are migrated. With
pytest:

```toml
# pyproject.toml
[tool.pytest.ini_options]
filterwarnings = [
    "error",
    'ignore:exposure_policy = "legacy" \(stated on:DeprecationWarning',
]
```

With `python -W` or `PYTHONWARNINGS` the message part is matched literally
from its start, so the entry is
`-W 'ignore:exposure_policy = "legacy":DeprecationWarning'`, placed after
`-W error`.

Two things tell you which names a view still has to supply. With
`DEBUG = True` djust logs a warning on the `djust.exposure` logger the first
time a view renders a name that its template reads and the view sets but does
not supply. `python manage.py check` reports the same statically as
`djust.T027`.

> **Where 2.0 is today.** There is no 2.0 release yet. On the development line
> (`2.0.0.dev0`) the removals listed under [Removed names](#3-replace-removed-names-and-settings)
> have landed, and explicit exposure is the default: a view that states no
> policy is explicit, and a view that states `exposure_policy = "legacy"` still
> works and raises a `DeprecationWarning`. Strict event parameters are still an
> opt-in there. 2.0.0 final makes both the only behaviour. [What is still to be removed](#4-still-to-be-removed-before-the-final-release)
> lists the rest.

## Before you start

1. Upgrade to the newest 1.3.x and finish [Upgrading to djust 1.3](upgrade-to-1.3.md).
   An app that still imports the old standalone packages (`djust_theming`,
   `djust_components`, `djust_auth`, `djust_tenants`, `djust_admin`) has to
   move to the `djust.*` modules first:
   [From standalone djust packages](migration-from-standalone-packages.md).
2. Run your tests with deprecation warnings as errors, and fix what they name.
   The Python names 1.3 deprecates warn with their replacement. Settings,
   view attributes and template attributes do not warn, so you find those by
   searching for the names in sections 3 to 5.

   ```bash
   python -W error::DeprecationWarning manage.py test
   ```

3. Run `python manage.py check` and keep it free of errors while you work
   through the sections below.
4. Save the HTML of each page as it renders now, logged in as a test user. You
   will compare against it after each step: a page that renders the same bytes
   before and after has lost nothing.

   ```bash
   curl -s -b "sessionid=<your session cookie>" http://localhost:8000/cart/ > before-cart.html
   ```

## 1. Move each view to explicit exposure

Do this for one view at a time:

1. Run the inventory command on the view.
2. Put everything the template reads into `get_context_data()`.
3. Declare the state you want kept or sent to the browser with `state()`.
4. Set `exposure_policy = "explicit"` on the class. On 1.3 this is what turns
   the new behaviour on. On 2.0 it is the default, so the line changes nothing
   and you can remove it after you upgrade.
5. Load the page, use it, and check that nothing renders empty.

### What changes

Under the 1.3 default (`exposure_policy = "legacy"`), every public attribute
you set on `self` becomes template context, and is also copied into the
session and the client state. Under `"explicit"`:

| Destination | What goes there |
| --- | --- |
| The template | What `get_context_data()` returns, what Django context processors add (`user`, `perms`, `messages`, `request`), and what framework features provide (forms, uploads, streams, components declared on the class) |
| The browser, as raw data (`get_state()`, HTTP API `assigns`) | `state(..., client=True)` fields only |
| The session, restored on a reconnect | `state(..., persist="server")` fields only |
| The signed back-navigation snapshot | `state(..., persist="client", client=True)` fields only |
| The debug panel, time travel, bug capture | Field names and a redacted placeholder, not values. A `client=True` field shows its value. |

Everything else stays on the view object in server memory. Your handlers can
read it; nothing exports it. A value you render is in the HTML, as in any
Django template, and that is the only place it is.

### Step 1: run the inventory

```bash
python manage.py djust_exposure_inventory --view shop.views.CartView
```

Without `--view` it reports every LiveView in the project; `--app shop` limits
it to one app, and `--json` prints JSON. It reads your source and never
creates a view or prints a value. For this view:

```python
from djust import LiveView
from djust.decorators import event_handler


class CartView(LiveView):
    exposure_policy = "legacy"  # the 1.3 default; on 1.3 the line can be left out
    template = """<div dj-root>
        <p>{{ items|length }} items, coupon {{ coupon }}, page {{ page }}</p>
        <button dj-click="add" data-sku="A1">Add</button>
    </div>"""

    def mount(self, request, **kwargs):
        self.items = []
        self.coupon = "none"
        self.page = 1
        self._audit_note = "internal"

    @event_handler()
    def add(self, sku: str = "", **kwargs):
        self.items.append(sku)

    @event_handler()
    def apply_coupon(self, value: str = "", **kwargs):
        self.coupon = value
```

it prints:

```text
ADR-038 exposure inventory (legacy destinations; values redacted)

shop.views.CartView  [policy: legacy; reconnect: fresh mount]
  coupon  str  instance  -> template_context, render_cache, session_state, client_state, snapshot
      suggest: coupon: str = state(...)  # not restored on a reconnect today, so no persistence grant is needed; supply coupon=self.coupon from get_context_data() if the template uses it
  items  list  instance  -> template_context, render_cache, session_state, client_state, snapshot
      suggest: items: list = state(...)  # not restored on a reconnect today, so no persistence grant is needed; supply items=self.items from get_context_data() if the template uses it
  page  int  instance  -> template_context, render_cache, session_state, client_state, snapshot
      suggest: page: int = state(...)  # not restored on a reconnect today, so no persistence grant is needed; supply page=self.page from get_context_data() if the template uses it
  _audit_note  str  private  -> private_session
      suggest: explicit mode never persists underscore attributes, and this view does not restore them on a reconnect today; leave it instance-only
```

Read it this way:

- **The arrow list** is where the name goes today. Those are the places it
  stops going once the view is explicit.
- **`reconnect: fresh mount`** means the view runs `mount()` again when the
  socket reconnects, so nothing needs a persistence grant to behave as it does
  now. A view that sets `enable_state_snapshot = True`, or subclasses
  `PersistentLiveView`, reads `reconnect: restored (enable_state_snapshot)`
  and gets `state(persist="server")` suggestions.
- **The suggestion is a starting point, not a list to paste.** The command does
  not read your template, so it cannot tell which names the template uses, and
  it suggests `state(...)` for every instance attribute, including ones that
  are better left as plain attributes (see step 3).
- **Some names are not yours.** Class attributes inherited from a mixin are
  listed too, including Django's own (`raise_exception` and
  `permission_denied_message` from `LoginRequiredMixin`), and so are
  underscore attributes that belong to a mixin. Skip the ones your template
  does not use.

Releases before the fix for [#4023](https://github.com/djust-org/djust/issues/4023)
suggest `state(persist="server")` for every attribute and print no `reconnect:`
note. If you see that, do not copy the `persist="server"`: it grants session
persistence the view does not have today.

### Step 2: supply the template

List the names your template reads, and return each one from
`get_context_data()`. **A name the template reads that nothing supplies
renders as an empty string, with no error and no warning.** This is the
mistake to look for: after the switch, open every state of the page and look
for blanks. Remember names used only inside `{% if %}`, in `{% include %}`d
templates and in a base template.

To find the names, take each one the inventory lists and search the template
for it, along with every template it includes or extends:

```bash
grep -nE "(\{\{|\{%)[^}]*\bcoupon\b" shop/templates/shop/cart.html
```

Then check the result instead of trusting the list: after step 4, fetch the
page again and `diff` it against the copy you saved. Apart from the CSRF token
and the debug payload (and the handler metadata, once handlers are strict),
the two should be identical. A blank shows up as a changed line. Repeat for
each state of the page you can reach by URL.

A view that already builds its whole context in `get_context_data()` needs
nothing here. It is common: in the app this guide was tested on, five of six
views rendered identically with only the policy line added.

`user`, `perms`, `messages` and `request` still come from Django's context
processors; you do not add those.

### Step 3: declare state

`state()` declares a field with a default, on the class. Use it for the
values that are the view's state. It takes two independent grants:

- no grant (`state(0)`): the field lives for the life of the connection. It
  is not saved and not sent as data.
- `persist="server"`: saved in the session after each event and restored when
  the same user reconnects or the request lands on another worker.
- `client=True`: the raw value may reach the browser, through `get_state()`
  and the HTTP API's `assigns`.
- `persist="client", client=True`: restored from the signed snapshot on
  back-navigation. `persist="client"` without `client=True` is refused when
  the class is defined.

Anything you do not want saved or sent (a service object, a model instance, a
cache) stays a plain attribute set in `mount()`. Assigning a plain attribute
in a handler still re-renders the view. The checks `djust.V006` and
`djust.V008` still warn that such an attribute "cannot be serialized"; under
explicit exposure it never is, and the warning can be ignored
([#4032](https://github.com/djust-org/djust/issues/4032)).

### Step 4: switch the policy

The view from step 1, migrated to behave as it did:

```python
from djust import LiveView
from djust.decorators import event_handler, state


class CartView(LiveView):
    exposure_policy = "explicit"
    template = """<div dj-root>
        <p>{{ items|length }} items, coupon {{ coupon }}, page {{ page }}</p>
        <button dj-click="add" dj-value-sku="A1">Add</button>
    </div>"""

    items = state(default_factory=list)
    coupon = state("none")
    page = state(1)

    def mount(self, request, **kwargs):
        self._audit_note = "internal"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(items=self.items, coupon=self.coupon, page=self.page)
        return context

    @event_handler(parameter_policy="strict")
    def add(self, sku: str):
        self.items.append(sku)

    @event_handler(parameter_policy="strict")
    def apply_coupon(self, value: str):
        self.coupon = value
```

The handlers are also moved to strict parameters here; [section 2](#2-make-event-handlers-strict)
covers that change, and you can do it separately.

To move a whole app at once, set the policy on a base class of your own. The
policy is inherited:

```python
from djust import LiveView


class AppView(LiveView):
    exposure_policy = "explicit"
```

### Keeping state across a reconnect

A reconnect, a deploy or a second worker gives the user a new view object.
Under explicit exposure, `mount()` runs, then every `persist="server"` field
is put back from the session, and nothing else is. Decide per field whether
the user would notice it resetting:

```python
from djust import LiveView
from djust.decorators import event_handler, state


class ReportView(LiveView):
    exposure_policy = "explicit"
    template = """<div dj-root>
        <p>Report {{ report_id }}: {{ status }}, {{ rows|length }} rows</p>
        <button dj-click="load" dj-value-report-id="7">Load</button>
    </div>"""

    report_id = state(0, persist="server")
    status = state("idle", persist="server")

    def mount(self, request, **kwargs):
        self.rows = []

    def handle_restore(self, request):
        # report_id and status are back; rows were never saved.
        self.rows = self.fetch_rows(self.report_id)
        if self.status == "loading":
            self.status = "idle"

    def fetch_rows(self, report_id):
        return [{"n": n} for n in range(report_id)]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(report_id=self.report_id, status=self.status, rows=self.rows)
        return context

    @event_handler(parameter_policy="strict")
    def load(self, report_id: int):
        self.report_id = report_id
        self.rows = self.fetch_rows(report_id)
        self.status = "loaded"
```

- **Save ids, not objects.** A persisted or client field must be JSON: strings,
  numbers, booleans, `None`, and lists and dicts of those. A `date`, `Decimal`,
  `UUID` or model instance is refused, not converted to text. Keep the id in
  `state()` and load the object in `get_context_data()` or `handle_restore()`.
- **`handle_restore(request)` runs after the saved fields are put back.**
  `mount()` runs before, so it never sees restored values. Use
  `handle_restore()` to reload what you derive from them, and to reset a flag
  that describes work the old connection was doing, such as a loading
  indicator: the background job did not survive the reconnect. Releases
  without the fix for [#3989](https://github.com/djust-org/djust/issues/3989)
  call `handle_restore()` only on legacy views; on those, an explicit view
  gets its fields back and no hook.
- **Underscore names are never persisted.** `state()` cannot declare a name
  that starts with `_`. If `self._draft` has to survive a reconnect, rename it
  and declare it.
- **Grants are not inherited.** A `state(..., persist=...)` or `client=True`
  field on a base class has to be declared again on each concrete view. A base
  class may declare `state()` fields without grants.
- **Server persistence needs a server-side session.** Use the `db`,
  `cached_db`, `cache` or `file` session backend. Signed-cookie sessions are
  refused.
- **Changing the fields.** Set `exposure_schema_version = 2` (then 3, and so
  on) when you rename or retype a persisted field. Saved state from the older
  version is dropped and the view mounts fresh, unless you define
  `migrate_state(self, old_version, values)` to translate it.

A view that is ever served without a WebSocket (the HTTP fallback) gets a new
view object for every event. In 1.3 such a view had all its public attributes
restored from the session between events; under explicit exposure only
`persist="server"` fields are. The inventory command cannot see this. If the
view must work without a socket, give every field its handlers change
`persist="server"`.

### Other things explicit exposure refuses

- **Components assigned in `mount()`** (`self.nav = Tabs(...)`). Declare them
  in the class body: `nav = Tabs(active="overview")`. The exception is a plain
  component that a handler changes (`self.rating.value = value`): a class-body
  one is a single object shared by every session, so keep it in a private
  attribute (`self._rating = Rating(...)` in `mount()`) and hand it to the
  template in `get_context_data()`.
- **`{% live_render %}` with `lazy=`**, when the child view is explicit.
- **Actors** (`use_actors = True`). See [What does not survive](#5-what-does-not-survive).
- **A `state()` grant on a legacy view.** `state(0, persist="server")` on a
  view that is still `"legacy"` raises when the view is created, so a grant
  never looks like protection it does not give. Add the grant and the policy
  together.

The full reference is [Explicit exposure](../state/explicit-exposure.md).

## 2. Make event handlers strict

For each handler:

1. Annotate every parameter.
2. In the template, pass arguments with `dj-value-*`, not `data-*`. Do this
   before anything else: a strict handler ignores `data-*`.
3. Remove defaults that only existed to survive a missing argument.
4. Remove `**kwargs`, unless the handler really takes an open set of fields.
5. Add `parameter_policy="strict"` and run `python manage.py check`.

### What changes

Legacy dispatch already rejects an event that omits a required parameter or
carries a key the handler cannot take. What it also does is guess: it fills in
for values it cannot read, and it accepts whatever an unannotated parameter is
given. Most 1.3 handlers therefore carry `**kwargs` and defaults that exist
only to absorb what the browser sends. Under the strict policy the signature
is the contract:

| The event | Legacy | Strict |
| --- | --- | --- |
| has a parameter with no annotation | accepted, with any value | rejected: annotate it, or use `Any` to accept anything |
| carries blank text for `count: int` | the handler gets `0` | rejected |
| carries JSON `true` for `count: int` | the handler gets `True` | rejected |
| carries blank or unrecognized text for `on: bool` | the handler gets `False` | rejected |
| carries a key the handler does not declare | rejected, unless the handler has `**kwargs` | rejected, unless the handler has a `**` catch-all |
| is a `dj-input`, `dj-change` or `dj-submit` | the browser also sends `field` and `_target` | the browser sends only what the handler declares; `_target` is never sent |
| passes arguments as `data-*` attributes | read | ignored: use `dj-value-*` |
| is rejected | the error text can repeat the value that was sent | the error names the parameter and the expected type, never the value |

The browser knows each strict handler's signature, so a `dj-input` bound to
`search(self, value: str)` sends only `value`. Form fields, `value`, `field`,
`key` and `code` are sent only when the handler declares them or has a `**`
catch-all. That is why a strict handler no longer needs `**kwargs`. The server
checks every event again, including ones a script sends by hand.

### Before and after

```python
from djust import LiveView
from djust.decorators import event_handler


class SearchView(LiveView):
    template = """<div dj-root>
        <input name="q" dj-input="search">
        <button dj-click="open_item" data-item-id="42">Open</button>
        <p>{{ query }} / {{ selected }}</p>
    </div>"""

    def mount(self, request, **kwargs):
        self.query = "none"
        self.selected = 0

    def get_context_data(self, **kwargs):
        return {
            **super().get_context_data(**kwargs),
            "query": self.query,
            "selected": self.selected,
        }

    @event_handler()
    def search(self, value="", **kwargs):
        self.query = value

    @event_handler()
    def open_item(self, item_id=0, **kwargs):
        self.selected = int(item_id)
```

becomes:

```python
from djust import LiveView
from djust.decorators import event_handler


class SearchView(LiveView):
    template = """<div dj-root>
        <input name="q" dj-input="search">
        <button dj-click="open_item" dj-value-item-id="42">Open</button>
        <p>{{ query }} / {{ selected }}</p>
    </div>"""

    def mount(self, request, **kwargs):
        self.query = "none"
        self.selected = 0

    def get_context_data(self, **kwargs):
        return {
            **super().get_context_data(**kwargs),
            "query": self.query,
            "selected": self.selected,
        }

    @event_handler(parameter_policy="strict")
    def search(self, value: str):
        self.query = value

    @event_handler(parameter_policy="strict")
    def open_item(self, item_id: int):
        self.selected = item_id
```

`dj-value-item-id="42"` arrives as `item_id=42`: the annotation does the
conversion, and the handler no longer calls `int()`. A converted id is still
not an authorized one; look the record up and check permissions as before.

A form handler that takes whatever fields the form has keeps a typed
catch-all: `def save(self, title: str, **fields: str)`.

The accepted annotations are `str`, `int`, `float`, `bool`, `Decimal`, `UUID`,
`date`, `Optional[T]`, `list[T]` and `Any`. The exact rules for each are in
[Typed event parameters](../core-concepts/events.md#typed-event-parameters-strict-policy).

### Switching the whole project

When most handlers are converted, make strict the project default and mark
the stragglers:

<!-- doc-snippet-check: skip -->
```python
# settings.py
LIVEVIEW_CONFIG = {"event_parameter_policy": "strict"}
```

A handler that still has `**kwargs` keeps working under the project-wide
setting, because the catch-all takes whatever the browser sends, so the switch
itself is usually small. The catch-all also keeps the handler from being
checked; remove it handler by handler afterwards.

`@event_handler(parameter_policy="legacy")` keeps one handler on the old
behaviour until you get to it. `manage.py check` reports what strict dispatch
would reject: `djust.V016` for a parameter without a usable annotation or with
a reserved name, and `djust.T020` for a template binding that sends something
its handler does not take.

**`check` does not catch every dropped argument.** `djust.T020` reports a
`data-*` attribute only when the parameter it fed is required. When the
parameter has a default, the handler is called with the default, the click
does the wrong thing, and nothing is reported
([#4033](https://github.com/djust-org/djust/issues/4033)). Search the
templates yourself:

```bash
grep -rnE 'dj-(click|change|input|submit|blur|keydown)[^>]*data-[a-z]' --include='*.html' .
grep -rnE 'data-[a-z-]+=[^>]*dj-(click|change|input|submit|blur|keydown)' --include='*.html' .
```

A line search misses an element whose attributes are spread over several
lines, so also read the bindings of each strict handler you convert.
`data-component-id` and `data-value` on an element that calls a
[component's event](#python-names) are not affected.

Handlers named by a `dj-auto-recover` attribute always run under the legacy
policy for now; `djust.V019` warns if you mark one strict.

## 3. Replace removed names and settings

Each of these is gone on the 2.0 line. An import of a removed name raises
`ImportError` or `AttributeError`, except where a row says the failure is
silent. A removed setting raises
`ImproperlyConfigured` at startup, and the message names the replacement.
Every replacement works on 1.3.x, so make the change there.

### Python names

| Removed | Use |
| --- | --- |
| `@event` (`djust.event`, `djust.decorators.event`) | `@event_handler` |
| `LiveViewForm` (`djust.LiveViewForm`, `djust.forms.LiveViewForm`) | A plain `django.forms.Form`, with `FormMixin` on the view. `get_field_errors_json()` becomes `json.dumps(form.errors)`; `get_field_value(name)` becomes `form.cleaned_data` or `form.data`. |
| `@reactive` (`djust.reactive`, `djust.decorators.reactive`) | A plain attribute, or `@computed` from `djust.decorators` |
| `djust.theming.themes._legacy`, and `THEMES`, `get_theme`, `list_themes`, `Theme`, `Typography`, `Spacing`, `BorderRadius`, `Shadows`, `Animations`, `ComponentStyles` from `djust.theming.themes` | `get_design_system`, `get_all_design_systems` and `DESIGN_SYSTEMS` from `djust.theming.theme_packs` |
| `AccordionMixin`, `TabsMixin`, `ModalMixin`, `CollapsibleMixin`, `SheetMixin`, `DropdownMixin`, `TooltipMixin`, `CarouselMixin` from `djust.components.mixins` | The component of the same name from `djust.components.descriptors`, declared on the class (example below) |
| `djust.auth.social.social_auth_providers` (a context processor) | `{% load djust_auth %}` and `{% auth_providers auth %}` in the template. Remove the processor from `TEMPLATES`. |
| `djust.template_backend` | `djust.template`; in `TEMPLATES`, `"djust.template.backend.DjustTemplateBackend"` |
| `djust.state_backend` | `djust.state_backends` |
| `djust._view_resolution.is_view_import_allowed` | `djust.security.mount.is_view_path_allowed` |
| `djust.simple_live_view.LiveView` | `djust.simple_live_view.SimpleLiveView` |
| `djust.rust_components`, and `<RustButton/>`-style tags in templates | `djust.components.ui.Button` and the other `djust.components.ui` components |
| `StreamingMixin.stream_insert(self, ...)` and `StreamingMixin.stream_delete(self, ...)` (the async pair) | `await self.astream_insert(...)` and `await self.astream_delete(...)`. `self.stream_insert` and `self.stream_delete` are the synchronous collection streams. |
| `djust.sse.DjustSSEEventView` and the `sse/<session_id>/event/` route | `DjustSSEMessageView` at `sse/<session_id>/message/`, which the bundled client already uses |
| `self._async_pending = (fn, args, kwargs)` in a handler | `self.start_async(fn, *args, **kwargs)`, or `@background` on the handler. Assigning the tuple raises `ImproperlyConfigured` when the handler runs, on every transport ([#4031](https://github.com/djust-org/djust/issues/4031)); search your code for `_async_pending` to find them before then. |
| `use_actors = True` | Remove the attribute. See [What does not survive](#5-what-does-not-survive). |

A component mixin becomes a class attribute. This replaces
`class DocsView(TabsMixin, LiveView)` with `self.init_tabs("nav", active="overview")`
in `mount()`:

```python
from djust import LiveView
from djust.components.descriptors import Tabs


class DocsView(LiveView):
    template = """<div dj-root>
        <button dj-click="set_tab" data-component-id="nav" data-value="api">API</button>
        <p>Showing {{ nav.active }}</p>
    </div>"""

    nav = Tabs(active="overview")
```

The component registers its own event on the view, and `self.nav.active` is
the current tab in your handlers. Each component has one event, so a template
that called a mixin's separate open and close handlers needs its `dj-click`
values changed:

| Component | State on `self.<name>` | Event | Mixin handlers it replaces |
| --- | --- | --- | --- |
| `Accordion` | `active`, `multiple` | `accordion_toggle` | `accordion_toggle` |
| `Tabs` | `active` | `set_tab` | `set_tab` |
| `Modal` | `is_open` | `toggle_modal` | `open_modal`, `close_modal`, `toggle_modal` |
| `Collapsible` | `is_open` | `toggle_collapsible` | `toggle_collapsible` |
| `Sheet` | `is_open`, `side` | `toggle_sheet` | `open_sheet`, `close_sheet` |
| `Dropdown` | `is_open` | `toggle_dropdown` | `toggle_dropdown`, `close_dropdown` |
| `Tooltip` | `is_visible` | `toggle_tooltip` | `show_tooltip`, `hide_tooltip` |
| `Carousel` | `active`, `total` | `carousel_go` | `carousel_prev`, `carousel_next`, `carousel_go` |

To open or close from your own handler, set the state:
`self.confirm.is_open = True`.

### Settings

| Removed | Use |
| --- | --- |
| `DJUST_WS_COMPRESSION` | `LIVEVIEW_CONFIG['websocket_compression']` |
| `DJUST_VDOM_CACHE_ENABLED`, `DJUST_VDOM_CACHE_TTL_SECONDS`, `DJUST_VDOM_CACHE_MAX_ENTRIES` | `vdom_cache_enabled`, `vdom_cache_ttl_seconds`, `vdom_cache_max_entries` in `LIVEVIEW_CONFIG['service_worker']` |
| `DJUST_STATE_BACKEND` | `DJUST_CONFIG['STATE_BACKEND']`, set to `'memory'` or `'redis'`. A `redis://` URL goes in `DJUST_CONFIG['REDIS_URL']`, not in the backend setting. |
| `DJUST_REDIS_URL` | `DJUST_CONFIG['REDIS_URL']` |
| `TEMPLATES[...]['BACKEND'] = "djust.template_backend.DjustTemplateBackend"` | `"djust.template.backend.DjustTemplateBackend"` |
| `template_auto_call`, `virtual_keyed_ops`, `django_value_repr` in `LIVEVIEW_CONFIG` or `DJUST_CONFIG` | Delete the key. What each one could switch off is always on. |

### Templates and client code

| Removed | Use |
| --- | --- |
| The JSON `dj-params` attribute | One `dj-value-*` attribute per argument |
| `data-djust-root`, `data-djust-view` | `dj-root`, `dj-view` |
| `window.LiveViewWebSocket`, `window.setCacheConfig`, `window.globalLoadingManager` | The same names on `window.djust` |
| `block["super"]` and `dict(block)` inside a custom tag that receives `block` | `context["block"].super()` |
| System checks `djust.C018` and `djust.T015` | Remove them from `SILENCED_SYSTEM_CHECKS`; nothing reports them. |

### Behaviour that now raises

- **The compiled extension is required.** `import djust` fails when the Rust
  extension is missing; the slower pure-Python paths are gone. Install a wheel
  for your platform, or build from source with a Rust toolchain.
- **A template the Rust engine rejects is an error.** A component whose
  template failed in the Rust engine used to be rendered again by Django's
  engine, silently. The error now propagates.
- **A `{% extends %}` parent that cannot be found is an error.** The first
  page load used to serve the page without its base template.
- **A model or QuerySet that cannot be serialized for the template is an
  error.** It used to be serialized again a slower way.

### Defaults that changed

Nothing raises for these; the page looks or behaves differently until you
state the old value.

- **`{% theme_head %}` no longer links the component catalog's stylesheet.**
  With `djust.components` installed it used to link
  `djust_components/components.css` (about 330 KB) on every page. Pages that
  render catalog tags (`{% modal %}`, `{% data_table %}`, `{% app_shell %}`,
  ...) now render them unstyled unless the project sets
  `LIVEVIEW_CONFIG["theme"]["component_catalog_css"] = True`, or the page
  passes `{% theme_head component_catalog=True %}`. The theme pack's own
  component CSS (`.card`, `.btn`, `.input`, ...) is linked as before. System
  check `djust_theming.W004` reports a project that installs
  `djust.components` without stating the setting; set it to `False` once you
  know no page needs the file
  ([#4003](https://github.com/djust-org/djust/issues/4003)).
- **The Chart.js adapter redraws only when the canvas's attributes change.**
  `<canvas dj-hook="Chart">` used to call `chart.update()` after every server
  update; it now does so only when one of the canvas's attributes changed. A
  chart whose plugins read state from outside the canvas (the theme, a global)
  either changes `dj-chart-revision` when that state changes, or sets
  `dj-chart-update="always"` for the old behaviour
  ([#4004](https://github.com/djust-org/djust/issues/4004)).

## 4. Still to be removed before the final release

These still work on the 2.0 development line. Each is to be removed, or made
the only behaviour, before 2.0.0 is released, so finish with them on 1.3.x:

| Still present | Do this now |
| --- | --- |
| `exposure_policy = "legacy"`. It is no longer the default: a view has to state it, and stating it warns | Migrate every view that states it, or that you have not checked yet ([section 1](#1-move-each-view-to-explicit-exposure)) |
| `event_parameter_policy = "legacy"`, the default, and `parameter_policy="legacy"` | Make every handler strict ([section 2](#2-make-event-handlers-strict)) |
| `event_security` set to `"open"` or `"warn"` | Remove the setting (the default is `"strict"`) and put `@event_handler` on every method a template calls |
| `enable_state_snapshot = True` and `PersistentLiveView` | `state(..., persist="server")` fields, and `persist="client", client=True` for back-navigation |
| Time travel that restores a past state | Nothing to change in code; see below |

## 5. What does not survive

Three features have no equivalent in 2.0. Plan for their absence instead of
looking for a replacement.

- **Actors (`use_actors = True`).** The actor system was experimental, worked
  only over WebSocket and cannot run under explicit exposure. Remove the
  attribute; the view then runs like any other LiveView.
- **`enable_state_snapshot` and `PersistentLiveView`.** These saved every
  public attribute, and the browser held a signed, readable copy. 2.0 has no
  way to save a view's whole state. Declare the fields worth keeping with
  `state(..., persist=...)`, and rebuild the rest in `mount()` and
  `handle_restore()`.
- **Time travel that changes the view.** Jumping back to a past state and
  replaying events from it is removed. The debug panel keeps a read-only
  history. An explicit view already records field names and redacted
  placeholders, not values, and its history entries cannot be restored.

## 6. Finish

1. `python manage.py djust_exposure_inventory` shows `[policy: explicit]` for
   every view.
2. `python -W error::DeprecationWarning manage.py test` passes.
3. `python manage.py check` is clean with
   `LIVEVIEW_CONFIG = {"event_parameter_policy": "strict"}` set.
4. No view sets `enable_state_snapshot`, `use_actors` or subclasses
   `PersistentLiveView`.
5. Open each page, use it, and reconnect (stop and start the server with the
   page open). Look for values that render empty and for state that resets
   when it should not.

Then install 2.0, and start the server once: a removed setting you missed
stops it with the name of its replacement.
