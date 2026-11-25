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
view renders the names nothing supplies as empty, with no error. No setting
changes the default. The 2.0 pre-releases accept the policy and 2.0.0 does
not, so this is only a stopgap
([section 4](#4-still-to-be-removed-before-the-final-release)).

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
`djust.T027`. A field declared with `state()` is supplied: it renders without
a `get_context_data()` ([#4050](https://github.com/djust-org/djust/issues/4050)).

> **Where 2.0 is today.** 2.0.0rc5 is a release candidate. Install it with
> `pip install --pre djust` (a plain `pip install djust` still installs 1.3.x).
> In 2.0.0rc5 the removals listed under [Removed names](#3-replace-removed-names-and-settings)
> have landed, and explicit exposure is the default: a view that states no
> policy is explicit. The legacy modes are still present in rc5: a view that
> states `exposure_policy = "legacy"` still works and raises a
> `DeprecationWarning`, and strict event parameters are the default, with the
> legacy policy still available. The legacy modes are removed before 2.0.0 final, which makes explicit exposure and strict
> parameters the only behaviour ([#4171](https://github.com/djust-org/djust/issues/4171)).
> [What is still to be removed](#4-still-to-be-removed-before-the-final-release)
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
2. Declare the view's state with `state()`. A declared field renders; choose
   `persist` and `client` only for what must be kept or sent to the browser.
3. Return everything else the template reads from `get_context_data()`.
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
| The template | Fields declared with `state()` (unless `render=False`), what `get_context_data()` returns, what Django context processors add (`user`, `perms`, `messages`, `request`), and what framework features provide (forms, uploads, streams, components declared on the class) |
| The browser, as raw data (`get_state()`, HTTP API `assigns`) | `state(..., client=True)` fields only |
| The session, restored on a reconnect | `state(..., persist="server")` fields only |
| The signed back-navigation snapshot | `state(..., persist="client", client=True)` fields only |
| The debug panel | Field names and a redacted placeholder, not values. A `client=True` field shows its value. |

Everything else stays on the view object in server memory. Your handlers can
read it; nothing exports it. A value you render is in the HTML, as in any
Django template, and that is the only place it is.

**No generated `_count` names.** Under the 1.3 default a QuerySet or list in
the context as `items` also gave the template `items_count`. Explicit exposure
does not generate it, so `{{ items_count }}` renders empty. Write
`{{ items|length }}` instead (it works on a QuerySet and on a list), or, for a
QuerySet, `{{ items.count }}` (answered from the rows already fetched, no extra
query), or return
`items_count` from `get_context_data()`. With `DEBUG = True` djust logs a
warning on the `djust.exposure` logger that names the template's
`items_count` and points at `{{ items|length }}`, once per view and name, and
`manage.py check` reports it as `djust.T027` when `items` is supplied.

**Models and rows carry only what the template reads.** As under the 1.3
default, a Model, a model list or a QuerySet in the template context reaches
the renderer reduced to the fields the template names, not every column
(the identity keys `id`, `pk` and `__model__` only when the template prints
the value whole or reads nothing off it). `{{ person.username }}` hands
the renderer `username`; `email` stays out of its state. 2.0.0rc1 to rc3
copied every concrete field (less `djust_exclude_fields`) of a value whose
relations the template did not walk (#4290); if you worked around that with
`self.serialize_for_template(value, name)` in `get_context_data()`, the
workaround is no longer needed and does no harm. A single Model whose
relations or methods the template walks (`{{ person.groups.count }}`,
`{{ order.customer }}`) keeps its full normalized fields. A row list the template
reads no field path off (`{% regroup %}`, `{{ rows.0 }}`) keeps whole rows,
as on 1.3, and a `dictsort:<variable>` loop
whose variable names a property or method rather than a column keeps the
full rows it sorts by. `djust.testing.rendered_state(view)` shows what a
render handed the renderer.

### Step 1: run the inventory

```bash
python manage.py djust_exposure_inventory --view shop.views.CartView
```

Without `--view` it reports every LiveView in the project; `--app shop` limits
it to one app, and `--json` prints JSON. It reads your source and never
creates a view or prints a value. For this view:

<!-- doc-snippet-check: before -->
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
  coupon  str  instance  -> template_context, client_state, snapshot (conditional: session_state)
      suggest: coupon: str = state(...)  # not restored on a reconnect today, so no persistence grant is needed; a declared field renders in the template without get_context_data()
  items  list  instance  -> template_context, client_state, snapshot (conditional: session_state)
      suggest: items: list = state(...)  # not restored on a reconnect today, so no persistence grant is needed; a declared field renders in the template without get_context_data()
  page  int  instance  -> template_context, client_state, snapshot (conditional: session_state)
      suggest: page: int = state(...)  # not restored on a reconnect today, so no persistence grant is needed; a declared field renders in the template without get_context_data()
  _audit_note  str  private  -> - (conditional: private_session)
      suggest: explicit mode never persists underscore attributes, and this view does not restore them on a reconnect today; leave it instance-only
```

Read it this way:

- **The arrow list** is where the name goes today. Those are the places it
  stops going once the view is explicit. `session_state` and
  `private_session` are always conditional on 2.0: a legacy view's state
  reaches the session only when it renders as a `lazy=True` child and an event
  reaches it over the HTTP fallback.
- **`reconnect: fresh mount`** means the view runs `mount()` again when the
  socket reconnects, so nothing needs a persistence grant to behave as it does
  now. On 2.0 every view reads `fresh mount`, because nothing restores a
  legacy view's state on a reconnect any more. The 1.3.x command reads
  `reconnect: restored (enable_state_snapshot)` for a view that sets
  `enable_state_snapshot = True` or subclasses `PersistentLiveView`, and
  suggests `state(persist="server")` for its names: run it there first if you
  have such views (see [Python names](#python-names)).
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

List the names your template reads. Each one is either declared with `state()`
(step 3), which renders it, or returned from `get_context_data()`. **A name
the template reads that nothing supplies
renders as an empty string, with no error and no warning** (with `DEBUG`
on djust logs it once; set `DJUST_UNSUPPLIED_NAMES = "error"` in your test
settings to make each one fail the test that renders it; never in production settings). This is the
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
  renders in the template, and is not saved and not sent as data.
- `persist="server"`: saved in the session after each event and restored when
  the same user reconnects or the request lands on another worker.
- `client=True`: the raw value may reach the browser, through `get_state()`
  and the HTTP API's `assigns`.
- `persist="client", client=True`: restored from the signed snapshot on
  back-navigation. `persist="client"` without `client=True` is refused when
  the class is defined.
- `render=False`: kept out of the template. Rendering is the default for a
  declared field; it is not a grant, and it never puts the value in
  `get_state()` or the session.

**A field named `state`.** A class-body `state = state("ok")` rebinds the name
`state` to the field, so every later `state(...)` in the same class body fails
with `TypeError: 'StateProperty' object is not callable` (the traceback points
at the next field, not at the one that shadowed it). Rename the field (`load_state`),
declare it last in the class body, or write `djust.state(...)` for the others:

<!-- djust-example: skip -- the shadowing mistake this paragraph describes -->
<!-- doc-snippet-check: anti-pattern -->
```python
class SearchView(LiveView):
    exposure_policy = "explicit"
    state = state("idle")  # from here on, state is a StateProperty, not the function
    page = state(1)  # TypeError: 'StateProperty' object is not callable
```

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

    @event_handler(parameter_policy="strict")
    def add(self, sku: str):
        self.items.append(sku)

    @event_handler(parameter_policy="strict")
    def apply_coupon(self, value: str):
        self.coupon = value
```

`items`, `coupon` and `page` render because they are declared; the view needs
no `get_context_data()`. Releases without
[#4050](https://github.com/djust-org/djust/issues/4050), including 1.3.x, do
not render a declared field by itself: there, also return the three from
`get_context_data()`. That hand-over keeps working after the upgrade, where a
`get_context_data()` value of the same name wins.

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

A reconnect, a second worker and every event over the HTTP fallback give the
user a new view object, built from the view's saved state as in 1.3.

**What is saved.** After the page load and after each event that changed it,
the view's state is saved in the server-side session: its public attributes,
the `_private` attributes `mount()` created, and its `state()` fields. Every
plain Python value comes back with its type (numbers, strings, lists, dicts,
tuples, sets, `Decimal`, dates and times, `UUID`, bytes, enum members). A
model instance is saved as a dict of its fields, as in 1.3. Saving needs no
declaration and exposes nothing: a saved value reaches the template only
through a `state()` field or `get_context_data()`, and reaches the browser as
data only with `client=True`.

**How it comes back.** Over the HTTP fallback a later request restores the
saved state and does **not** run `mount()` again; then `handle_params()` and
`handle_restore()` run. While the query string is the one the state was saved
under, what a handler set goes back over what `handle_params()` assigned; when
the query changed (`?page=1` then `?page=3`), `handle_params()` has the last
word for what it assigns and the rest of the saved state is kept. `mount()` runs again only when nothing is saved, or
when something of the view could not be saved (a queryset, the request's user,
an arbitrary object, a callable such as a class or a `functools.partial`, work
`mount()` queued): such a value is skipped, never
turned into a string, a WARNING on the `djust.automatic_state` logger names it
once, and the next request runs `mount()` to re-create it before the saved
state is put back over it. Over a socket, a mount or reconnect runs `mount()`
and then puts back only what a handler changed since `mount()`, so everything
`mount()` computes is fresh there. A full page load runs `mount()` and starts a
new saved state.

**One state per tab.** Each page load carries a tab id; two tabs on one route
do not share state (1.3 shared it). A reload is a new tab. A session keeps at
most 8 tab states per view and route, together no larger than that view's size
limit, and at most 1 MB of saved view state in all; the oldest go first.

**What is not saved.** A field declared `state(..., persist=False)`; names on
the sensitive-field list (`password`, `is_staff`, `is_superuser`,
`DJUST_SENSITIVE_FIELDS`); and, in a `FormMixin` form, password, one-time-code
and payment-card inputs.

`persist="server"` is the strict form: a value that cannot be saved is an
error instead of being skipped.

**Switching it off.** `DJUST_AUTOMATIC_STATE = False` in settings saves nothing
of any view between requests, and `automatic_state = False` on a view does the
same for that view (`True` there overrides a project that switched it off).
This replaces 1.x's `view_state_in_session = False`. Every fallback request
then runs `mount()` again. A field declared `persist="server"` is still kept.
See [Switching saving off](state-survival.md#switching-saving-off).

**Check your session engine before upgrading.** Saved state, and
`persist="server"` fields, now work with an engine whose `SessionStore`
subclasses Django's `db`, `cached_db`, `cache` or `file` store. Earlier 2.0
pre-releases accepted only those exact classes: a project with its own subclass
had nothing saved (one log line) and its `persist="server"` fields refused. If
your project relied on that, it will start saving view state in its session on
upgrade. Set `DJUST_AUTOMATIC_STATE = False` to keep saving nothing.

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
        # report_id, status and rows are back; read the rows again, they
        # may have changed since they were saved.
        self.rows = self.fetch_rows(self.report_id)
        if self.status == "loading":
            self.status = "idle"

    def fetch_rows(self, report_id):
        return [{"n": n} for n in range(report_id)]

    def get_context_data(self, **kwargs):
        # report_id and status are declared, so they render already.
        return super().get_context_data(rows=self.rows, **kwargs)

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
- **`mount()` is not the place for what must be fresh.** A value `mount()`
  computes (a permission flag, a list of rows) is saved and comes back as it
  was until the page is loaded again, exactly as it lives on in a socket's
  view. Authorization belongs in the framework's gates (`login_required`,
  `permission_required`, `check_permissions`, object permissions), which run on
  every request; data that must be current belongs in `handle_restore()` or
  `get_context_data()`.
- **Size.** A WARNING names the largest attributes once a view's saved state
  passes 100 KB (`DJUST_AUTOMATIC_STATE_WARN_KB`). Past 512 KB
  (`DJUST_AUTOMATIC_STATE_MAX_KB`, or `automatic_state_max_kb` on the view)
  the event's change is refused whole, with an error naming the view, the
  attribute and the limit; the page stays on its last saved state. State is
  never saved in part.
- **Deploys.** As in 1.3, saved state is applied to the code that is running;
  a saved name that is now a method or a property is skipped.
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

A view that is served without a WebSocket (the HTTP fallback) gets a new view
object for every event, rebuilt from its saved state as in 1.3.

### Other things explicit exposure refuses

- **`_on_sticky_update` on an explicit sticky child.** As a sticky child it is
  remounted whenever a `{% live_render %}` input changes, so djust never calls
  the hook (it ran only for legacy children, `djust.V035`). Name the inputs the
  hook takes with `sticky_update_inputs = ("title",)`, or delete the hook and
  read the inputs in `mount()`.
- **Service-worker caching of page HTML.** 1.3 let the worker cache every
  (legacy) page; an explicit page is cached only when its view sets
  `sw_cacheable = True` (`class Docs(LiveView): sw_cacheable = True  # public
  page, same HTML for everyone`). Set it only on public pages: `manage.py check` (`djust.V037`) reports an
  opted-in view that reads the user, the session or persisted state.
- **`page_title` is a framework setting.** It cannot be a `state()` field (the
  class raises `ExposureConfigurationError`, see
  [Behaviour that now raises](#behaviour-that-now-raises)). Keep setting
  `self.page_title = "..."` in `mount()` or a handler: that still updates the
  tab over the live connection. But neither that nor a class attribute `page_title = "..."`
  supplies `{{ page_title }}` to the template, which renders it empty, with no
  error. T027 and `DJUST_UNSUPPLIED_NAMES` do not report it either, because
  it is a framework name. Supply it from `get_context_data()`
  (`ctx["page_title"] = self.page_title`), typically once in a shared base
  view's `get_context_data()` for a layout's `<title>`; that is the only place
  that reaches the template.
- **Components assigned in `mount()`** (`self.nav = Tabs(...)`). Declare a
  `LiveComponent` in the class body: `nav = Tabs(active="overview")`; each
  view gets its own copy. A plain component (`Rating`, `Badge` and the other
  `djust.components.components` classes) cannot go there: a class-body one is
  a single object shared by every session, so it is refused. Declare it as a
  field, `rating = state(default_factory=lambda: Rating(value=4))`, which
  renders it, or build it in `get_context_data()`.
  Do not give a component field `persist=`.
- **A plain component in the class body** (`rating = Rating(...)`), for the
  reason above. On a legacy view it is not refused, but `manage.py check`
  warns about it (`djust.V026`), because a legacy view renders it to every
  user.
- **Actors** (`use_actors = True`). See [What does not survive](#5-what-does-not-survive).
- **A legacy widget on an admin page.** The admin change list and change
  form are explicit, and an explicit page cannot embed a view that states
  `"legacy"`: a `change_list_widgets` / `change_form_widgets` view that
  still does makes the page fail (`djust.V032` names it). Migrate the
  widget first.
- **Undeclared names in a `TtydTerminalView` subclass.** The terminal base
  class is explicit. Its own names (`ttyd_url`, `rows`, `cols`,
  `theme_json`) come from `super().get_context_data()`; a subclass whose
  template reads its own attributes (`session_log`, `terminal_connected`)
  lists them there, or they render empty (`djust.T027` names each one).
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
5. Run `python manage.py check`. Strict is the default since 2.0 ([#4189](https://github.com/djust-org/djust/issues/4189)): `djust.V016` reports what dispatch refuses. A handler you have not converted yet can keep `@event_handler(parameter_policy="legacy")` until you get to it.

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
| is rejected | the error text can repeat the value that was sent | the error names the rule that failed and the declared parameter, never a value or an undeclared key; set the `djust.validation` logger to DEBUG to log undeclared key names on the server |

The browser knows each strict handler's signature, so a `dj-input` bound to
`search(self, value: str)` sends only `value`. Form fields, `value`, `field`,
`key` and `code` are sent only when the handler declares them or has a `**`
catch-all. A component's identity `name` (ADR-033 D5: `{% toggle_group name=... %}`,
or `Component(name=...)` through `event_attrs`) follows the same rule: the framework
marks it `data-djust-optional` (framework-internal; don't write it yourself), so a handler that does not declare `name` is not
sent it. A `dj-value-name` you write yourself is still checked, and still refused
when undeclared. That is why a strict handler no longer needs `**kwargs`. The server
checks every event again, including ones a script sends by hand.

### Before and after

<!-- doc-snippet-check: before -->
```python
from djust import LiveView, state
from djust.decorators import event_handler


class SearchView(LiveView):
    template = """<div dj-root>
        <input name="q" dj-input="search">
        <button dj-click="open_item" data-item-id="42">Open</button>
        <p>{{ query }} / {{ selected }}</p>
    </div>"""

    query = state("none")
    selected = state(0)

    @event_handler()
    def search(self, value="", **kwargs):
        self.query = value

    @event_handler()
    def open_item(self, item_id=0, **kwargs):
        self.selected = int(item_id)
```

becomes:

```python
from djust import LiveView, state
from djust.decorators import event_handler


class SearchView(LiveView):
    template = """<div dj-root>
        <input name="q" dj-input="search">
        <button dj-click="open_item" dj-value-item-id="42">Open</button>
        <p>{{ query }} / {{ selected }}</p>
    </div>"""

    query = state("none")
    selected = state(0)

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
`date`, `Optional[T]`, `list[T]`, `dict[str, T]` and `Any`. The exact rules for each are in
[Typed event parameters](../core-concepts/events.md#typed-event-parameters-strict-policy).

### The project default

Strict is the default since 2.0
([#4189](https://github.com/djust-org/djust/issues/4189)). A project that needs
time can set the old policy temporarily, which djust 2.0.0 refuses
(`djust.C045`):

<!-- doc-snippet-check: skip -->
```python
# settings.py
LIVEVIEW_CONFIG = {"event_parameter_policy": "legacy"}
```

A handler whose named parameters are all annotated keeps working under the
strict default when it also has `**kwargs`: the catch-all takes whatever else
the browser sends, so the switch is usually small. An unannotated named
parameter fails the page (`djust.V016` reports it), with or without
`**kwargs`. The catch-all also keeps the handler from being checked; remove it
handler by handler afterwards.

`@event_handler(parameter_policy="legacy")` keeps one handler on the old
behaviour until you get to it. `manage.py check` reports what strict dispatch
would reject: `djust.V016` for a parameter without a usable annotation or with
a reserved name, and `djust.T020` for a template binding that sends something
its handler does not take.

`manage.py check` also reports what strict would change while a handler is
still legacy:

- `djust.V033`: a legacy handler whose signature strict would refuse.
- `djust.T028`: a template binding a legacy handler accepts that strict would
  refuse (a `data-*` argument, for one), in `{% extends %}` and `{% include %}`
  templates too.
- `djust.V034`: a handler that declares or reads `_target`, which a strict
  client never sends (`dj-input` and its siblings send `field` instead).
- `djust.T029`: Python code that emits a `data-*` argument in the markup it
  builds with `format_html()` or an f-string; emit the `dj-value-*` attribute
  instead.

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
`data-component-id` on an element that calls a
[component's event](#python-names) is routing, not an argument, and stays;
pass the event's argument as `dj-value-value`.

djust's own component tags and templates already send their arguments as
`dj-value-*` (#4186): a `{% data_table %}` header fires
`dj-click="on_table_sort" dj-value-value="name"` where 1.3 wrote
`data-value="name"`. If your CSS, JavaScript or tests select a framework
element by `data-value`, `data-column`, `data-mode`, `data-tab` or another
argument attribute, select the `dj-value-*` attribute instead.

A handler named by a `dj-auto-recover` attribute declares the recovered
values instead of reading `_form_values` / `_data_attrs`, which a strict
signature cannot declare. It follows the same policy as any other handler:

```python
# 1.3
@event_handler()
def restore(self, _form_values=None, _data_attrs=None):
    title = (_form_values or {}).get("title", "")

# 2.0
@event_handler(parameter_policy="strict")
def restore(self, form_values: dict[str, Any], data_attrs: dict[str, str]):
    title = form_values.get("title", "")
```

The browser sends only the dictionaries the handler declares.
`djust.V031` reports a recovery handler that still reads the 1.3 names (a
warning under the legacy policy, an error under strict) and one that declares
the 2.0 names while it runs under the legacy policy. The values and their
limits are in [Reconnection](reconnection.md#custom-recovery-with-dj-auto-recover).

## 3. Replace removed names and settings

Each of these is gone on the 2.0 line. An import of a removed name raises
`ImportError` or `AttributeError`, except where a row says the failure is
silent. A removed setting raises
`ImproperlyConfigured` at startup, and the message names the replacement.
Every replacement works on 1.3.x, so make the change there.

### Python names

<!-- BEGIN GENERATED removed-table:python: do not edit by hand; edit docs/removed/2.0/migration/ and run make generated-docs -->

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
| `@optimistic` and `@client_state(keys=...)` (`djust.optimistic`, `djust.client_state`, `djust.decorators`) | Delete the decorator; the handler behaves as before, because nothing read either one. Importing either raises `ImportError` naming this advice ([#4006](https://github.com/djust-org/djust/issues/4006)). For feedback before the server answers, use `dj-loading.*` attributes or JS Commands. |
| `sign_snapshot`, `unsign_snapshot` and `SNAPSHOT_SALT` from `djust.security`, the `djust.security.state_snapshot` module, and the `LiveView._restore_snapshot()` override | `state(..., persist="client", client=True)` fields. djust signs and verifies their token itself (example below). Importing one of the three names raises `ImportError` with this advice, and so does reading it as an attribute: `hasattr(djust.security, "sign_snapshot")` raises instead of returning `False`, so a compatibility shim must catch `ImportError`. A class that defines `_restore_snapshot()` is refused with `ImproperlyConfigured`; move what it did after restoring into `handle_restore()`. |
| `enable_state_snapshot = True`, on a view or a mixin | `state(..., persist="server")` on each field that must survive a reconnect, and `persist="client", client=True` for one the browser may hold across Back (example below). A view that still sets it is refused with `ImproperlyConfigured` when the class is created, or when it mounts if the value is set on the instance or later. `False` is accepted. |
| `PersistentLiveView` (`djust.PersistentLiveView`, `djust.live_view.PersistentLiveView`) | `LiveView`, with the fields to keep declared as above. Importing it raises `ImportError` with this advice. |
| Time-travel debugging: `time_travel_enabled = True`, `time_travel_excluded_fields`, the `djust.time_travel` module (`TimeTravelBuffer`, `EventSnapshot`), `djust.bug_capture.encode_view_state()`, the debug panel's Time Travel tab, and the `time_travel_jump` / `time_travel_component_jump` / `forward_replay` / `bug_capture_share` frames | Delete the attributes; a view that sets either to a true value is refused with `ImproperlyConfigured` when the class is created, or when it mounts if the value is set on the instance or later. To follow state changes event by event, open the debug panel's **State** tab (with `DEBUG = True`); its **Share** button copies a bug capture with the event's real patches, leaving out `LIVEVIEW_CONFIG['bug_capture_default_scrub']`. From code: `BugCapture(state_before=..., state_after=..., vdom_patches=...).encode(scrub=scrub_fields(...))`. See [What does not survive](#5-what-does-not-survive). |
| The observability endpoints `reset_view_state/` and `eval_handler/`, `djust.observability.dry_run` (`DryRunContext`, `DryRunViolation`), and the MCP tools `eval_handler` and `reset_view_state` | Nothing replaces them. They worked only on views that state `exposure_policy = "legacy"` (every other view got `409`), so they leave with the legacy policy. Drive a handler through the live page, or through `LiveViewTestClient.send_event()` in a test. The read-only endpoints (`view_assigns/`, `last_traceback/`, `log/`, `handler_timings/`, `turn_timings/`, `sql_queries/`) and their MCP tools stay. |
| System check `djust.V011` (sticky child and parent snapshot opt-in) | Remove it from `SILENCED_SYSTEM_CHECKS`; nothing reports it. `djust.C304` now warns about a `client=True` field with a credential-like name. |
| `NonPersistableStateError`, and the `strict=` argument of `LiveView._capture_snapshot_state()` | Nothing. A client field has to be JSON, and a model or `Decimal` is refused when the token is built. |
| `djust.react` (`ReactMixin`, `react_components`, `register_react_component`) and capitalized `<Button />`-style component tags in templates | A JavaScript hook: `dj-hook="Name"` on an ordinary element, the hook in `window.djust.hooks` ([hooks guide](hooks.md)). Importing the names raises `ImportError`; a capitalized tag is a template error unless it is an HTML element, and HTML reads element names in any case: `<DIV>`, `<TextArea>` and `<tItLe>` are elements, while a self-closing non-void element (`<Button label="x" />`, `<Header />`) and a name HTML does not have (`<Widget>`) are component tags ([#4006](https://github.com/djust-org/djust/issues/4006)). |

<!-- END GENERATED removed-table:python -->

A component mixin becomes a class attribute. This replaces
`class DocsView(TabsMixin, LiveView)` with `self.init_tabs("nav", active="overview")`
in `mount()`:

```python
from djust import LiveView
from djust.components.descriptors import Tabs


class DocsView(LiveView):
    template = """<div dj-root>
        <button dj-click="set_tab" data-component-id="nav" dj-value-value="api">API</button>
        <p>Showing {{ nav.active }}</p>
    </div>"""

    nav = Tabs(active="overview")
```

The signed back-navigation snapshot is now built from declared fields only.
In 1.3 a view that set `enable_state_snapshot = True` had every public
attribute signed and sent to the browser, and restored through
`_restore_snapshot()` when the user pressed Back. In 2.0 the browser holds
only what the view declares:

```python
from djust import LiveView
from djust.decorators import event_handler, state


class WizardView(LiveView):
    template = """<div dj-root>
        <p>Step {{ step }}</p>
        <button dj-click="forward">Next</button>
    </div>"""

    # 1.3: enable_state_snapshot = True, and self.step = 1 in mount()
    step = state(1, persist="client", client=True)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["step"] = self.step
        return context

    @event_handler()
    def forward(self):
        self.step += 1
```

A legacy view no longer sends a token at all, and a token a 1.x server signed
is refused. The session copy that `enable_state_snapshot` also restored from
is gone too (below), so a legacy view mounts fresh on Back.

`enable_state_snapshot = True` (and `PersistentLiveView`, which set it) also
saved every public attribute in the session after each socket event and put
it back, instead of running `mount()`, when the socket reconnected. Sticky
children of such a page were saved the same way. In 2.0 a reconnect runs
`mount()` and then restores the declared `persist="server"` fields:

```python
from djust import LiveView
from djust.decorators import event_handler, state


class CartView(LiveView):
    # 1.3: class CartView(PersistentLiveView), with self.items = [] in mount()
    exposure_policy = "explicit"
    template = """<div dj-root>
        <p>{{ items|length }} items</p>
        <button dj-click="add" dj-value-sku="A1">Add</button>
    </div>"""

    items = state([], persist="server")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["items"] = self.items
        return context

    @event_handler()
    def add(self, sku: str = ""):
        self.items = [*self.items, sku]
```

Session state that 1.x saved for a page is not read on a reconnect, nor by the
HTTP page-POST fallback ([below](#state-a-legacy-view-no-longer-keeps)).

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

<!-- BEGIN GENERATED removed-table:settings: do not edit by hand; edit docs/removed/2.0/migration/ and run make generated-docs -->

| Removed | Use |
| --- | --- |
| `DJUST_WS_COMPRESSION` | `LIVEVIEW_CONFIG['websocket_compression']` |
| `DJUST_VDOM_CACHE_ENABLED`, `DJUST_VDOM_CACHE_TTL_SECONDS`, `DJUST_VDOM_CACHE_MAX_ENTRIES` | `vdom_cache_enabled`, `vdom_cache_ttl_seconds`, `vdom_cache_max_entries` in `LIVEVIEW_CONFIG['service_worker']` |
| `DJUST_STATE_BACKEND` | `DJUST_CONFIG['STATE_BACKEND']`, set to `'memory'` or `'redis'`. A `redis://` URL goes in `DJUST_CONFIG['REDIS_URL']`, not in the backend setting. |
| `DJUST_REDIS_URL` | `DJUST_CONFIG['REDIS_URL']` |
| `TEMPLATES[...]['BACKEND'] = "djust.template_backend.DjustTemplateBackend"` | `"djust.template.backend.DjustTemplateBackend"` |
| `template_auto_call`, `virtual_keyed_ops`, `django_value_repr` in `LIVEVIEW_CONFIG` or `DJUST_CONFIG` | Delete the key. What each one could switch off is always on. |
| `view_state_in_session` in `LIVEVIEW_CONFIG` or `DJUST_CONFIG` | Delete the key. A view's state is saved in the server-side session by default. To save nothing, as `view_state_in_session = False` did, set `DJUST_AUTOMATIC_STATE = False` in settings (the whole project) or `automatic_state = False` on a view ([Switching saving off](state-survival.md#switching-saving-off)). System check `djust.C027`, which validated the old key, is retired. |
| `event_security` set to `"open"` or `"warn"` in `LIVEVIEW_CONFIG` or `DJUST_CONFIG` | Put `@event_handler` on every method a template binds (`dj-click`, `dj-submit`, ...), then delete the key. Only decorated methods are client events, on every transport (WebSocket, SSE, the page-POST fallback, components and the test client); startup refuses the two modes (any case) with this hint and any other value except `"strict"` as unknown (#4101). `"strict"` is accepted and does nothing. |
| `time_travel_enabled` and `time_travel_max_events` in `LIVEVIEW_CONFIG` or `DJUST_CONFIG` | Delete the keys; djust refuses them with `ImproperlyConfigured` at startup. Time-travel debugging was removed (#4184). System checks `djust.C501`, `djust.C502` and `djust.V014` are retired. `bug_capture_default_scrub` stays: the State tab's Share button applies it. |

<!-- END GENERATED removed-table:settings -->

### Templates and client code

<!-- BEGIN GENERATED removed-table:templates: do not edit by hand; edit docs/removed/2.0/migration/ and run make generated-docs -->

| Removed | Use |
| --- | --- |
| The JSON `dj-params` attribute | One `dj-value-*` attribute per argument |
| `data-djust-root`, `data-djust-view` | `dj-root`, `dj-view` |
| `window.LiveViewWebSocket`, `window.setCacheConfig`, `window.globalLoadingManager` | The same names on `window.djust` |
| `block["super"]` and `dict(block)` inside a custom tag that receives `block` | `context["block"].super()` |
| System checks `djust.C018` and `djust.T015` | Remove them from `SILENCED_SYSTEM_CHECKS`; nothing reports them. |
| The `djust_components/table.html` override point and the filters `table_cell`, `table_href`, `table_has_href`, `table_width`, `table_width_attrs` | Read the row in your own template. `{% data_table %}` is drawn by the native handler on both engines; the row-click script is `/static/djust_components/data-table-row-click.js` (serve it from `/static/` or load it yourself). |

<!-- END GENERATED removed-table:templates -->

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
- **A `state()` field named after a framework name raises when the view class
  is created.** `template_name = state(...)`, `temporary_assigns = state(...)`,
  `request`, `kwargs`, `page_title`, `sticky` and any other name of a framework
  setting, a `LiveView` method or per-request identity
  (`djust._exposure.reserved_state_names()`) raises `ExposureConfigurationError`
  naming the field, whether it is declared on the view, on a mixin or on a base
  class. Such a field used to import and then crash a system check or a
  management command, or fail at render. Rename it (`template_name_value`).
  For `page_title`, keep setting `self.page_title` and supply it to the
  template from `get_context_data()`
  ([`page_title` is a framework setting](#other-things-explicit-exposure-refuses)).

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
- **Production logs and error trackers lose exception detail for explicit
  views.** With `DEBUG = False` a legacy view's failure was logged with its
  exception and traceback, and error trackers listening to
  `got_request_exception` (Sentry) received it. An explicit view logs a static
  line, sends the signal with a value-free `ExposureError` from the HTTP page
  view, and none from the WebSocket, SSE or HTTP fallback. To get the detail
  back in server sinks only, set
  `LIVEVIEW_CONFIG = {"log_exception_detail": True}`: client frames and HTTP
  responses stay generic
  ([#4180](https://github.com/djust-org/djust/issues/4180)).
- **Background work stops when the connection closes.** Under the 1.3
  default a view's `start_async()` / `@background` work ran to completion
  after its socket or SSE stream closed, and the result was discarded, unless
  the view set `cancel_async_on_disconnect = True`. An explicit view's work,
  and the tasks it starts with `self.create_task()`, are cancelled when the
  view is disposed: when the connection closes and when the user navigates to
  another view. `cancel_async_on_disconnect` changes nothing on an explicit
  view. Work that must finish (a payment, an import) states
  `survive_disconnect=True`: `self.start_async(self._import)` becomes
  `self.start_async(self._import, survive_disconnect=True)`, and `@background`
  becomes `@background(survive_disconnect=True)`. It then runs to completion
  and its result is discarded
  ([#4182](https://github.com/djust-org/djust/issues/4182)).

### State a legacy view no longer keeps

A view that still states `exposure_policy = "legacy"` mounts, renders its
public attributes and handles events within one connection as before. What it
no longer has between requests and reconnects:

- **Its private attributes.** `_name` attributes were saved in the session
  (`liveview_<path>__private`) and put back on the next page-POST fallback
  request. They are rebuilt by `mount()` now.
- **Its public state and its components' state in the session.** The page GET
  and each page-POST fallback request saved the view's public context
  (`liveview_<path>`) and its components (`liveview_<path>_components`), and
  the next POST restored them instead of running `mount()`. Each POST now
  mounts the view again, runs the work `mount()` queued and applies the event,
  so a "load more" offset, a form draft or an open dropdown set on `self` is
  back at its `mount()` value. An event addressed to an interactive component
  (`DropdownMenu`) the previous answer rendered is refused with 400
  `"Component not found"`, because the new mount gives it a new identity. (On
  an explicit view it is reached and its state carries: its id is derived
  from its declaration and its state is saved with the view's declared state,
  #4046.) For the same reason a client-owned
  component's visibility report (`observe_toggle`) sent over the fallback no
  longer reaches the view: each POST mounts the component with a new identity
  and a new observation lifetime, so the report is refused or dropped and an
  `on.toggled` observer does not run. `handle_restore()` is
  not called for a legacy view, since nothing is restored.
- **Its cached renderer.** The compiled template, render state and diff
  baseline were kept in the state backend (`DJUST_CONFIG['STATE_BACKEND']`)
  under the session and page and reused by the next request or reconnect. Each
  view instance builds its own now, as an explicit view always did.

  So a page GET makes no call to the state backend at all (no `set()`,
  `refresh()` or `get()`, with `DJUST_AUTOMATIC_STATE` on or off), and the
  `DjustPerformanceWarning: Large LiveView state` warning, which that `set()`
  measured, no longer fires for page loads (#4281). It fires only for a
  snapshot your own code stores in the backend. A test that budgeted a page's
  size by wrapping the backend's `set()` now records nothing. What 2.0 bounds
  instead is the state it persists for a view:

  - fields declared `state(..., persist="server")`: 64 KB encoded per view
    ([explicit exposure, Size](../state/explicit-exposure.md#supported-configuration));
  - automatic state: a WARNING names the largest attributes past 100 KB
    (`DJUST_AUTOMATIC_STATE_WARN_KB`), and a change past 512 KB
    (`DJUST_AUTOMATIC_STATE_MAX_KB`, or `automatic_state_max_kb` on the view)
    is refused ([Keeping state across a reconnect](#keeping-state-across-a-reconnect)).

  A change over either limit is refused with a `state_too_large` error and a
  WARNING. To keep a budget test, measure the state the view would persist:
  `djust.testing.persisted_state(view)` returns it, and its `automatic_bytes`
  is the encoded size of the automatic state, the number the limits above
  apply to. It describes an explicit view (the default): a view still on
  `exposure_policy = "legacy"` raises `TypeError`, and a state over its limit
  raises `StateLimitExceeded`, as the save would. The renderer a connection
  holds in memory is not measured.

Keys a 1.x server left in a session are never read. Declare what must survive
on an explicit view; private attributes stay private and are rebuilt by
`mount()`:

```python
from djust import LiveView
from djust.decorators import event_handler, state


class FeedView(LiveView):
    # 1.3 (legacy): self.offset = 0 and self._page_size = 20 in mount(); the
    # page-POST fallback saved both in the session between requests.
    exposure_policy = "explicit"
    template = """<div dj-root>
        <p>Showing from {{ offset }}</p>
        <button dj-click="more">More</button>
    </div>"""

    offset = state(0, persist="server")

    def mount(self, request, **kwargs):
        self._page_size = 20  # rebuilt by mount() on every request

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["offset"] = self.offset
        return context

    @event_handler()
    def more(self):
        self.offset += self._page_size
```

A test that called `_get_private_state()` to check what a view keeps now raises on an
explicit view. Use `LiveViewTestClient.persisted_state()` (or `djust.testing.persisted_state(view)`)
instead: it returns what the view would save, split by destination, and
`"name" not in persisted_state(view).names` asserts that a value a page only reads is saved nowhere
([Asserting what is saved](../testing/index.md#asserting-what-is-saved)).

## 4. Still to be removed before the final release

These still work on the 2.0 development line. Each is to be removed, or made
the only behaviour, before 2.0.0 is released, so finish with them on 1.3.x:

| Still present | Do this now |
| --- | --- |
| `exposure_policy = "legacy"`. It is no longer the default: a view has to state it, stating it warns, and system check `djust.V028` lists each class that does | Migrate every view that states it, or that you have not checked yet ([section 1](#1-move-each-view-to-explicit-exposure)) |
| `event_parameter_policy = "legacy"` (no longer the default) and `parameter_policy="legacy"` | Make every handler strict ([section 2](#2-make-event-handlers-strict)) |
| `reauth_on_event`, `reauth_server_turn_interval` | Delete: an explicit view re-authorizes every turn |
| `cancel_async_on_disconnect` | Delete: an explicit view's work is cancelled on disconnect |

`manage.py check` lists each of these still in use: `djust.C045` (settings),
`djust.V036` (`parameter_policy="legacy"`, `cancel_async_on_disconnect`) and
`djust.V028` (`exposure_policy`).

## 5. What does not survive

These features have no full equivalent in 2.0. Plan for their absence instead
of looking for a replacement.

- **Actors (`use_actors = True`).** The actor system was experimental, worked
  only over WebSocket and cannot run under explicit exposure. Remove the
  attribute; the view then runs like any other LiveView.
- **`enable_state_snapshot` and `PersistentLiveView`.** These saved every
  public attribute in the session, and in 1.x the browser held a signed,
  readable copy. Both are removed, and so is the page-POST fallback's session
  copy of a legacy view ([State a legacy view no longer keeps](#state-a-legacy-view-no-longer-keeps)). 2.0 has no way to save a view's whole
  state. Declare the fields worth keeping with
  `state(..., persist=...)`, and rebuild the rest in `mount()` and
  `handle_restore()`.
- **Time-travel debugging (`time_travel_enabled = True`).** Removed with the
  debug panel's Time Travel tab (#4184). Recording and Share worked on
  explicit views (as a redacted, read-only history); jumping back, restoring
  one component and forward replay worked only on legacy views. **Lost:**
  stepping the view back to an earlier state, restoring one component, and
  replaying an event forward with edited parameters. **Kept:** the per-event
  history in the debug panel's **State** tab (each event with the state after
  it and what changed), the Events tab's "Replay this event", and Share, now
  on the State tab, which copies a bug capture with the event's real patches.
  Delete `time_travel_enabled` and `time_travel_excluded_fields`; a view that
  still sets either is refused when the class is created.
- **The `reset_view_state` and `eval_handler` debug endpoints** and their MCP
  tools. They worked only on views that state `exposure_policy = "legacy"`.
- **React component tags (`djust.react`, `ReactMixin`).** The integration
  rendered a placeholder that only a demo Counter hydrated. A capitalized tag
  such as `<Button />` is now a template error (an uppercase spelling of an
  ordinary HTML element, `<DIV>`, is still HTML; write tags in lowercase
  anyway). `<Name` inside an HTML comment (`<!-- <Header /> -->`) or inside
  `<script>` / `<style>` text (`if (a<MAX && b)`) is not a tag and renders as
  before. Mount a widget with a [JavaScript hook](hooks.md) instead.

## 6. Finish

1. `python manage.py djust_exposure_inventory` shows `[policy: explicit]` for
   every view.
2. `python -W error::DeprecationWarning manage.py test` passes.
3. `python manage.py check` is clean with no `event_parameter_policy` key and
   no `parameter_policy="legacy"`.
4. No view sets `enable_state_snapshot`, `use_actors` or subclasses
   `PersistentLiveView`.
5. Open each page, use it, and reconnect (stop and start the server with the
   page open). Look for values that render empty and for state that resets
   when it should not.

Then install 2.0, and start the server once: a removed setting you missed
stops it with the name of its replacement.

<!-- generated-doc prose-sha256: c4e3bc452997 -->
