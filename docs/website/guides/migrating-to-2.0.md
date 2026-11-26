---
title: "Migrating to djust 2.0"
slug: migrating-to-2.0
section: guides
order: 18.4
level: reference
description: "Upgrade a 1.3.x app to djust 2.0, which removes the legacy modes: every 1.3 capability with its 2.0 spelling, the check that reports it and whether djust_exposure_inventory --fix rewrites it."
---

# Migrating to djust 2.0

djust 2.0 removes the legacy modes instead of carrying them
([#4171](https://github.com/djust-org/djust/issues/4171)). Explicit exposure
and strict event parameters are the only behaviour in 2.0.0, and a removed
name or setting stops the server with the name of its replacement. For each
1.3 capability this guide gives the 2.0 spelling, the system check that
reports it, and whether `python manage.py djust_exposure_inventory --fix`
rewrites it.

**Do the work on 1.3.x, before you install 2.0.** Both new contracts ship in
1.3 as opt-ins, and every replacement named in this guide exists there, unless
its row says "(2.0 only)". You can migrate one view and one handler at a time,
deploy between steps, and install 2.0 once nothing in the app still depends on
the old behaviour.

> **Where 2.0 is today.** 2.0.0rc5 is a release candidate. Install it with
> `pip install --pre djust` (a plain `pip install djust` still installs 1.3.x).
> In rc5 explicit exposure is the default (a view that states no policy is
> explicit) and strict event parameters are the default
> ([#4189](https://github.com/djust-org/djust/issues/4189)). The removals in
> [section 3](#3-replace-removed-names-and-settings) have landed.
> `exposure_policy = "legacy"` and `parameter_policy="legacy"` are refused
> (2.0.0rc6, [#4199](https://github.com/djust-org/djust/issues/4199)): each
> raises `ImproperlyConfigured` naming the replacement.

If you are already on a pre-release with views you have not migrated, the
class statement of each one that states `exposure_policy = "legacy"` now fails.
Run `djust_exposure_inventory --fix` (step 1 of
[section 1](#1-move-each-view-to-explicit-exposure)): the command reads and
rewrites such modules, and is the only code allowed to import them.

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
   through the sections below. On a 2.0 pre-release it also lists every change
   this guide describes ([which check reports what](#which-checks-report-what));
   the upgrade checks (`djust.T027`, `djust.V034`, `djust.T029` and the rest)
   and `djust_exposure_inventory --fix` exist only there, so run them in a scratch
   environment (`pip install --pre djust`) and make the changes on 1.3.x.
4. Save the HTML of each page as it renders now, logged in as a test user. You
   will compare against it after each step: a page that renders the same bytes
   before and after has lost nothing.

   ```bash
   curl -s -b "sessionid=<your session cookie>" http://localhost:8000/cart/ > before-cart.html
   ```

## The upgrade in five steps

1. Run `python manage.py check` and read the upgrade checks.
   [Which checks report what](#which-checks-report-what) lists them.
2. Run `python manage.py djust_exposure_inventory --fix --dry-run` and read
   the diff. Nothing is written.
3. Run `python manage.py djust_exposure_inventory --fix`, then read
   "Needs a person".
4. Work through "Needs a person" and the rows marked `No` or `Partly` in the
   tables below.
5. Run `python manage.py check` again, load every page, and compare it with
   the copy you saved in "Before you start".

For the `CartView` of [step 1 in section 1](#step-1-run-the-inventory), `--fix`
prints this:

```text
djust_exposure_inventory --fix: 1 file changed

Changed:
  shop/views.py
    line 6: CartView: deleted exposure_policy = "legacy"
    line 9: data-sku -> dj-value-sku (dj-click="add": add() declares sku)
    line 25: CartView: added get_context_data() supplying items, coupon, page

Needs a person:
  shop.views.CartView: items, coupon and page are supplied from get_context_data(). Declare a name with state() instead when it is the view's state; add persist="server" only when it must survive a reconnect.

Next: run python manage.py check, then load each page and compare it with how it rendered before.
```

This assumes the step 1 code is the whole of `shop/views.py` and the command
runs from the project root. The line numbers are those of the file before the
fix, and paths are relative to `--root`.

**What `--fix` does.**

- It deletes `exposure_policy = "legacy"`. On a base class it deletes the line
  only when every view that inherits it was fully supplied; otherwise it keeps
  the line and tells you which view needs a person. Run it again afterwards.
- It supplies the names each template reads, by adding them to
  `get_context_data()`.
- It annotates unannotated handler parameters as `Any`, and adds
  `from typing import Any` when the module needs it.
- It renames `data-*` event arguments to `dj-value-*` in a template when the
  bound handler declares that argument.

**What it never does.** It never writes `state()`, `persist=` or `client=`:
which names are state, and what must survive a reconnect, is your decision. It
never creates a view, edits a file outside `--root` (default: `BASE_DIR`, else
the current directory), or touches markup that Python code builds. It reports
`_target` reads, `data-*` built in Python and `{{ items_count }}` instead.
`--json` prints the report as JSON. It imports your views, so a module that
does not import cannot be fixed.

On 2.0 a class that states `exposure_policy = "legacy"`,
`parameter_policy="legacy"` or `cancel_async_on_disconnect = True` is refused
when it is created. The command imports your views under its own permit, so
it can still read and rewrite them; nothing else can, and constructing such a
view is refused inside the command too. A module that is imported while
Django starts (a URLconf, an `AppConfig.ready()`, a `models.py`, or an
`admin.py` that admin autodiscovery imports) is refused before the command
runs: delete its line by hand, then run the command. Do the steps in this
order: first delete the removed `LIVEVIEW_CONFIG` / `DJUST_CONFIG` keys
(`event_parameter_policy`, `reauth_on_event`, `reauth_server_turn_interval`),
which are refused at startup, then run `--fix`.

### Which checks report what

A row that names a check is reported by `python manage.py check`. A row that
says `ImproperlyConfigured` is not a check: the class, the decorator or the
server refuses to start, with the replacement in the message. Only
`djust_exposure_inventory` can still import such code, which is why `--fix`
runs before anything else.

| Check | It reports | `--fix` | Fixed in |
| --- | --- | --- | --- |
| `ImproperlyConfigured` when the class is created | a class states `exposure_policy = "legacy"` | Yes, once every view that inherits the line is supplied | [section 1](#step-4-delete-the-policy-line) |
| `djust.T027` | a name the template reads that nothing supplies, or an `X_count` 1.x generated | Partly: it supplies the name from `get_context_data()`; reports `X_count` | [section 1](#step-2-supply-the-template) |
| `djust.V016` | a handler declaration strict dispatch refuses: an unannotated parameter, a declared `_target` | Partly: it annotates an unannotated parameter `Any`; reports the rest | [section 2](#2-make-event-handlers-strict) |
| `djust.T020` | an event binding's arguments missing, unexpected or supplied twice, including a `data-*` argument | Partly: it renames `data-X` to `dj-value-X` in a template when the handler declares `X` | [section 2](#2-make-event-handlers-strict) |
| `djust.T029` | Python code that emits a `data-*` event argument | No: reported | [section 2](#strict-is-the-only-policy) |
| `djust.V034` | a handler that reads `_target` | No: reported | [section 2](#strict-is-the-only-policy) |
| `djust.V031` | a `dj-auto-recover` handler on the 1.3 names | No | [section 2](#recovery-handlers-dj-auto-recover) |
| `ImproperlyConfigured` when the decorator applies | `parameter_policy="legacy"` | No: reported | [section 2](#strict-is-the-only-policy) |
| `ImproperlyConfigured` when the class is created | `cancel_async_on_disconnect = True` | No | [section 4](#background-work-that-must-finish) |
| `djust.V035` | `_on_sticky_update` without `sticky_update_inputs` | No | [section 4](#sticky-children) |
| `djust.V037` | `sw_cacheable = True` on a page that differs per visitor | No | [section 4](#service-worker-caching) |
| `ImproperlyConfigured` at startup | `event_parameter_policy`, `reauth_on_event` or `reauth_server_turn_interval` is set | No: delete the keys before you run it | [section 2](#strict-is-the-only-policy) and [section 4](#4-capabilities-with-a-new-20-spelling) |

`djust.V033` and `djust.T028`, which reported what a legacy handler would
lose under strict, were retired with the legacy parameter policy (#4205):
`djust.V016` and `djust.T020` report the same code now. `djust.V028` and
`djust.V036` (the legacy policies themselves) were retired too, and so was
`djust.V026` (#4204). Remove retired IDs from `SILENCED_SYSTEM_CHECKS` and
`suppress_checks`.

## 1. Move each view to explicit exposure

Do this for one view at a time:

1. Run the inventory command on the view, with `--fix` to make the mechanical
   changes.
2. Declare the view's state with `state()`. A declared field renders; choose
   `persist` and `client` only for what must be kept or sent to the browser.
3. Return everything else the template reads from `get_context_data()`.
4. Delete `exposure_policy = "legacy"`. On 1.3 the line states the default, so
   deleting it changes nothing there; set `exposure_policy = "explicit"`
   instead to turn the new behaviour on while you are still on 1.3. On 2.0
   explicit is the only behaviour.
5. Load the page, use it, and check that nothing renders empty.

### At a glance

| 1.3 | 2.0 | Reported by | `--fix` |
| --- | --- | --- | --- |
| A public attribute on `self` renders | A `state()` field, or a name returned from `get_context_data()` | `djust.T027` | Partly: it supplies the name from `get_context_data()`; `state()` is your choice |
| `exposure_policy = "legacy"` | Delete the line | `ImproperlyConfigured` when the class is created | Yes. It keeps the line on a base class while a view that inherits it needs a person |
| A generated `{{ items_count }}` | `{{ items\|length }}` | `djust.T027` | No: reported |
| `_private` attributes kept between fallback requests | Rebuilt in `mount()`; `state(persist="server")` for what must survive | Nothing reports it | No |
| `enable_state_snapshot`, `PersistentLiveView` | `state(..., persist=...)` on each field | `ImproperlyConfigured` when the class is created; `ImportError` for `PersistentLiveView` | No |
| A plain component in the class body, or `self.x = Badge()` in `mount()` | A class-body `LiveComponent`, or `state(default_factory=...)` | `ImproperlyConfigured` when the class is created | No |
| A legacy widget on an admin page | Migrate the widget like any view | `ImproperlyConfigured` when the widget class is created | Partly: the widget view is fixed like any view |
| `{{ page_title }}` read from `self.page_title` | Supply it from `get_context_data()` | Nothing reports it (a framework name, which `djust.T027` skips) | No |

Interactive components, `{% live_render %}` children, `dj-lazy`, activity
events over the HTTP fallback and related-manager traversal of a supplied
QuerySet work on explicit views as they did on legacy ones
([#4046](https://github.com/djust-org/djust/issues/4046),
[#4173](https://github.com/djust-org/djust/issues/4173),
[#4174](https://github.com/djust-org/djust/issues/4174),
[#4179](https://github.com/djust-org/djust/issues/4179),
[#4069](https://github.com/djust-org/djust/issues/4069)). Nothing to change.

### What changes

Under the 1.3 default (`exposure_policy = "legacy"`), every public attribute
you set on `self` becomes template context, and is also copied into the
session and the client state. In 2.0 (and under `"explicit"` on 1.3):

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
it to one app, and `--json` prints JSON. It imports your views but never
creates one or prints a value. Add `--fix` to make the mechanical changes
instead of only listing them ([the five steps](#the-upgrade-in-five-steps)).
For this view:

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
Names a djust 1.3 view exposes implicitly (values redacted)

shop.views.CartView  [policy: legacy]
  coupon  str  instance  -> template_context, client_state
      suggest: coupon: str = state(...)  # not restored on a reconnect today, so no persistence grant is needed; a declared field renders in the template without get_context_data()
  items  list  instance  -> template_context, client_state
      suggest: items: list = state(...)  # not restored on a reconnect today, so no persistence grant is needed; a declared field renders in the template without get_context_data()
  page  int  instance  -> template_context, client_state
      suggest: page: int = state(...)  # not restored on a reconnect today, so no persistence grant is needed; a declared field renders in the template without get_context_data()
  _audit_note  str  private  -> -
      suggest: explicit mode never persists underscore attributes, and this view does not restore them on a reconnect today; leave it instance-only
```

Read it this way:

- **The arrow list** is where 1.3 sent the name. Those are the places it
  stops going once the view is explicit.
- **No persistence grant is suggested**, because on 2.0 a view runs
  `mount()` again when the socket reconnects and nothing restores undeclared
  state. The 1.3.x command reads
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

Two things tell you which names a view still has to supply. With
`DEBUG = True` djust logs a warning on the `djust.exposure` logger the first
time a view renders a name that its template reads and the view sets but does
not supply. `python manage.py check` reports the same statically as
`djust.T027`. A field declared with `state()` is supplied: it renders without
a `get_context_data()` ([#4050](https://github.com/djust-org/djust/issues/4050)).
`djust_exposure_inventory --fix` supplies what it can from `get_context_data()`
and leaves the choice of `state()` to you.

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
    state = state("idle")  # from here on, state is a StateProperty, not the function
    page = state(1)  # TypeError: 'StateProperty' object is not callable
```

Anything you do not want saved or sent (a service object, a model instance, a
cache) stays a plain attribute set in `mount()`. Assigning a plain attribute
in a handler still re-renders the view. On 1.3 the checks
`djust.V006` and `djust.V008` warn that such an attribute "cannot be
serialized"; in 2.0 it never is, and they no longer report it on a LiveView
([#4032](https://github.com/djust-org/djust/issues/4032),
[#4204](https://github.com/djust-org/djust/issues/4204)).

### Step 4: delete the policy line

On 2.0 there is nothing to switch to: explicit is the only behaviour, and
`--fix` has already deleted the line where it could. The view from step 1,
migrated to behave as it did, with strict handlers:

```python
from djust import LiveView
from djust.decorators import event_handler, state


class CartView(LiveView):
    template = """<div dj-root>
        <p>{{ items|length }} items, coupon {{ coupon }}, page {{ page }}</p>
        <button dj-click="add" dj-value-sku="A1">Add</button>
    </div>"""

    items = state(default_factory=list)
    coupon = state("none")
    page = state(1)

    def mount(self, request, **kwargs):
        self._audit_note = "internal"

    @event_handler()
    def add(self, sku: str):
        self.items.append(sku)

    @event_handler()
    def apply_coupon(self, value: str):
        self.coupon = value
```

`items`, `coupon` and `page` render because they are declared; the view needs
no `get_context_data()`. Releases without
[#4050](https://github.com/djust-org/djust/issues/4050), including 1.3.x, do
not render a declared field by itself: there, also return the three from
`get_context_data()`. That hand-over keeps working after the upgrade, where a
`get_context_data()` value of the same name wins. (`--fix` writes exactly that
hand-over, which is why it works on both.)

The handlers are also strict here; [section 2](#2-make-event-handlers-strict)
covers that change, and you can do it separately.

To move a whole app at once while you are still on 1.3, put the explicit
policy on a base class of your own; every view below it inherits it. On 2.0
the line does nothing and you can delete it. (A `"legacy"` line on a base
class is the one `--fix` deletes, once every view below it is supplied.)

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

    @event_handler()
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

- **`_on_sticky_update` on an explicit sticky child, and service-worker
  caching of page HTML.** Both need a new class attribute:
  [Capabilities with a new 2.0 spelling](#4-capabilities-with-a-new-20-spelling).
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
  reason above: it is refused when the class is created.
- **Actors** (`use_actors = True`). See [What does not survive](#5-what-does-not-survive).
- **A legacy widget on an admin page.** The admin change list and change
  form are explicit, and a widget class that states `"legacy"` is refused
  when it is created, like any view. Migrate the widget first.
- **Undeclared names in a `TtydTerminalView` subclass.** The terminal base
  class is explicit. Its own names (`ttyd_url`, `rows`, `cols`,
  `theme_json`) come from `super().get_context_data()`; a subclass whose
  template reads its own attributes (`session_log`, `terminal_connected`)
  lists them there, or they render empty (`djust.T027` names each one).
- **A `state()` grant on a 1.3 legacy view.** On 1.3,
  `state(0, persist="server")` on a view that is still `"legacy"` raises when
  the view is created, so a grant never looks like protection it does not
  give. Add the grant and the explicit policy together.

The full reference is [Explicit exposure](../state/explicit-exposure.md).

## 2. Make event handlers strict

For each handler:

1. Annotate every parameter.
2. In the template, pass arguments with `dj-value-*`, not `data-*`. Do this
   before anything else: a strict handler ignores `data-*`.
3. Remove defaults that only existed to survive a missing argument.
4. Remove `**kwargs`, unless the handler really takes an open set of fields.
5. Run `python manage.py check`. Strict is the only policy in 2.0 ([#4189](https://github.com/djust-org/djust/issues/4189), [#4199](https://github.com/djust-org/djust/issues/4199)): `djust.V016` reports a declaration dispatch refuses and `djust.T020` a binding. `@event_handler(parameter_policy="legacy")` is refused with `ImproperlyConfigured` when the decorator applies, so convert every handler before you install 2.0.

### At a glance

| 1.3 | 2.0 | Reported by | `--fix` |
| --- | --- | --- | --- |
| An unannotated parameter | An annotation (`Any` for unchecked input) | `djust.V016` | Yes: it writes `Any`; the real type is your choice |
| A `data-item-id="3"` argument | `dj-value-item-id="3"` | `djust.T020` (templates), `djust.T029` (Python) | Yes in a template when the handler declares it; No for markup that Python builds (reported) |
| A `_target` parameter, or `kwargs.get("_target")` | A declared `field: str = ""` | `djust.V034` (and `djust.V016` for a declared `_target`) | No: reported |
| Blank text becomes `0` for `count: int` | `count: Optional[int] = None`, and handle `None` | No check: dispatch rejects the event | No |
| `parameter_policy="legacy"` on a handler | Delete it | `ImproperlyConfigured` when the decorator applies | No: reported, delete it once the templates send `dj-value-*` |
| `LIVEVIEW_CONFIG["event_parameter_policy"]` | Delete the key | `ImproperlyConfigured` at startup | No |
| A `**kwargs` that absorbs what the browser sends | The declared parameters | No check: a catch-all is valid | No |
| A `dj-auto-recover` handler that reads `_form_values` / `_data_attrs` | `form_values: dict[str, Any]`, `data_attrs: dict[str, str]` (2.0 only) | `djust.V031` | No |

### What changes

1.3's legacy dispatch already rejected an event that omits a required
parameter or carries a key the handler cannot take. What it also did was guess: it filled in
for values it could not read, and it accepted whatever an unannotated parameter
was given. Most 1.3 handlers therefore carry `**kwargs` and defaults that exist
only to absorb what the browser sends. Under the strict policy, the only one
in 2.0, the signature is the contract:

| The event | 1.3 legacy | 2.0 strict |
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

    @event_handler()
    def search(self, value: str):
        self.query = value

    @event_handler()
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

### Strict is the only policy

There is no setting to choose the policy. Strict is the only one in 2.0
([#4189](https://github.com/djust-org/djust/issues/4189),
[#4199](https://github.com/djust-org/djust/issues/4199)), and
`LIVEVIEW_CONFIG["event_parameter_policy"]` is refused at startup: delete the
key.

A handler whose named parameters are all annotated keeps working under the
strict default when it also has `**kwargs`: the catch-all takes whatever else
the browser sends, so the switch is usually small. An unannotated named
parameter fails the page (`djust.V016` reports it), with or without
`**kwargs`. The catch-all also keeps the handler from being checked; remove it
handler by handler afterwards.

`@event_handler(parameter_policy="legacy")` and
`@server_function(parameter_policy="legacy")` raise `ImproperlyConfigured`
when the decorator applies; there is no per-handler way back.
`manage.py check` reports what strict dispatch refuses:

- `djust.V016`: a parameter without a usable annotation, or with a reserved
  name such as `_target`. `--fix` annotates an unannotated parameter `Any`.
- `djust.T020`: a template binding that sends something its handler does not
  take, or misses a required argument (a `data-*` argument, for one), in
  `{% extends %}` and `{% include %}` templates too. `--fix` renames `data-X`
  to `dj-value-X` when the handler declares `X`.
- `djust.V034`: a handler that reads `_target`, which a strict client never
  sends (`dj-input` and its siblings send `field` instead). `--fix` reports it.
- `djust.T029`: Python code that emits a `data-*` argument in the markup it
  builds with `format_html()` or an f-string; emit the `dj-value-*` attribute
  instead. `--fix` reports it.

`djust.V033` and `djust.T028`, which a 2.0 pre-release used to report against
legacy handlers, are retired (#4205); `djust.V016` and `djust.T020` cover the
same code now.

**`check` does not catch every dropped argument.** `djust.T020` reports a
`data-*` attribute that names a declared parameter, whether the parameter is
required or has a default
([#4033](https://github.com/djust-org/djust/issues/4033)). It does not see a
binding whose handler name the template computes, a `data-*` attribute that
names no declared parameter (a `**kwargs` handler that read it), or markup
that Python builds (`djust.T029` covers some of that). Search the templates
yourself:

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

### Recovery handlers (dj-auto-recover)

A handler named by a `dj-auto-recover` attribute declares the recovered
values instead of reading `_form_values` / `_data_attrs`, which a strict
signature cannot declare. It follows the same policy as any other handler:

<!-- doc-snippet-check: before -->
```python
from djust.decorators import event_handler


@event_handler()
def restore(self, _form_values=None, _data_attrs=None):
    title = (_form_values or {}).get("title", "")
```

becomes, on 2.0 only (the 1.3 client sends only `_form_values` and
`_data_attrs`, so make this change after the upgrade):

```python
from typing import Any

from djust import LiveView
from djust.decorators import event_handler, state


class DraftView(LiveView):
    template = """<form dj-root dj-auto-recover="restore">
        <input name="title" dj-input="edit">
        <p>{{ title }}</p>
    </form>"""

    title = state("")

    @event_handler()
    def edit(self, value: str = ""):
        self.title = value

    @event_handler()
    def restore(self, form_values: dict[str, Any], data_attrs: dict[str, str]):
        self.title = form_values.get("title", "")
```

The browser sends only the dictionaries the handler declares.
`djust.V031` reports, as an error, a recovery handler that still declares or
reads the 1.3 names, and one that declares the 2.0 names positional-only.
`--fix` does not rewrite it. The values and their
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
| `exposure_policy = "legacy"` | Supply the names the template reads from `get_context_data()`, declare the state a later event or a reconnect needs with `state()`, then delete the line; `python manage.py djust_exposure_inventory --fix` does the mechanical part. Refused with `ImproperlyConfigured` when the class is created and on construction. `exposure_policy = "explicit"` is accepted and does nothing; any other value is refused. System check `djust.V028` is retired. |
| `@event_handler(parameter_policy="legacy")` and `@server_function(parameter_policy="legacy")` | Annotate the handler's parameters (`Any` accepts a value unchecked), send `dj-value-*` from the template instead of `data-*`, and delete the argument. Refused with `ImproperlyConfigured` when the decorator applies. `parameter_policy="strict"` is accepted and does nothing. System check `djust.V036` is retired. |
| `cancel_async_on_disconnect = True` on a view | Delete the attribute: a view's background work is always cancelled when its connection closes. Work that must finish says so with `start_async(fn, survive_disconnect=True)` or `@background(survive_disconnect=True)`. Refused with `ImproperlyConfigured` when the class is created and on the mount path; `False` is accepted. |
| the `djust startproject` command | `djust new <name>` (or `uvx djust@latest init` beside an existing `manage.py`); `djust startapp` stays ([#4207](https://github.com/djust-org/djust/issues/4207)) |
| the back-compat re-exports in `djust.theming.manager` (`COOKIE_NAMESPACE_RE`, `DEFAULT_CONFIG`, `_validate_cookie_namespace`), `djust.theming.presets` (`ColorScale`, `SurfaceTreatment`, `ThemeTokens`, `BLUE_THEME`, `GREEN_THEME`, `ORANGE_THEME`, `PURPLE_THEME`, `ROSE_THEME`, `SHADCN_THEME`, `NATURAL20_THEME`, `CATPPUCCIN_THEME`, `ROSE_PINE_THEME`, `TOKYO_NIGHT_THEME`, `NORD_THEME`, `SYNTHWAVE_THEME`, `CYBERPUNK_THEME`, `OUTRUN_THEME`, `FOREST_THEME`, `AMBER_THEME`, `SLATE_THEME`, `NEBULA_THEME`, `DJUST_THEME`, `DRACULA_THEME`, `GRUVBOX_THEME`, `SOLARIZED_THEME`, `HIGH_CONTRAST_THEME`, `MONO_THEME`, `EMBER_THEME`, `AURORA_THEME`, `INK_THEME`, `SOLARPUNK_THEME`, `BAUHAUS_THEME`, `CYBERDECK_THEME`, `PAPER_THEME`, `NEON_NOIR_THEME`, `OCEAN_THEME`, `STRIPE_THEME`, `LINEAR_THEME`, `NOTION_THEME`, `VERCEL_THEME`, `GITHUB_THEME`, `ART_DECO_THEME`, `HANDCRAFT_THEME`, `TERMINAL_THEME`, `MAGAZINE_THEME`, `DOCS_THEME`, `SWISS_THEME`, `CANDY_THEME`, `RETRO_COMPUTING_THEME`, `MEDICAL_THEME`, `LEGAL_THEME`, `MIDNIGHT_THEME`, `SUNRISE_THEME`, `FOREST_FLOOR_THEME`, `DASHBOARD_THEME`, `ONE_DARK_THEME`, `MONOKAI_THEME`, `AYU_THEME`, `KANAGAWA_THEME`, `EVERFOREST_THEME`, `POIMANDRES_THEME`, `TAILWIND_THEME`, `SUPABASE_THEME`, `RAYCAST_THEME`, `ADAPTIVE_THEME`, `SAKURA_THEME`, `OBSIDIAN_THEME`, `DUNE_THEME`, `MISSION_CONTROL_THEME`, `ART_NOUVEAU_THEME`), `djust.theming.theme_packs` (`AnimationStyle`, `IconStyle`, `IllustrationStyle`, `InteractionStyle`, `PatternStyle`, `PATTERN_GLASS`) | import the types from `djust.theming` and the rest from the module that defines it (`djust.theming._config`, `_constants`, `_builtin_presets`) ([#4207](https://github.com/djust-org/djust/issues/4207)) |
| `LiveViewConsumer.handle_event`, `LiveViewConsumer.handle_url_change`, `LiveViewConsumer.handle_mount` (now the private `_mount_page`) and `PushEventMixin._flush_pending_push_events` | events and URL changes reach a view through the runtime (`_dispatch_runtime_owned`); call `flush_push_events` instead of `_flush_pending_push_events`. A `LiveViewConsumer` subclass overriding one of these is not called ([#4207](https://github.com/djust-org/djust/issues/4207)) |
| `djust._rust.RustAlert`, `RustAvatar`, `RustBadge`, `RustButton`, `RustCard`, `RustDivider`, `RustIcon`, `RustModal`, `RustProgress`, `RustRange`, `RustSpinner`, `RustSwitch`, `RustTextArea`, `RustToast`, `RustTooltip` (the `djust_components` crate) | `djust.components.ui.Alert` ... `Tooltip`, which draw the same markup through `render()` ([#4002](https://github.com/djust-org/djust/issues/4002)) |

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

A token a 1.x server signed is refused, and the session copy that
`enable_state_snapshot` also restored from is gone too (below): a view mounts
fresh on Back unless it declares the fields to keep.

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
| `event_parameter_policy` in `LIVEVIEW_CONFIG` or `DJUST_CONFIG` | Delete the key; djust refuses it with `ImproperlyConfigured` at startup. Strict event parameters are the only policy: `manage.py check` reports each handler parameter strict refuses as `djust.V016`. System check `djust.C045` is retired. |
| `reauth_on_event` and `reauth_server_turn_interval` in `LIVEVIEW_CONFIG` or `DJUST_CONFIG` | Delete the keys; djust refuses them with `ImproperlyConfigured` at startup. Every event and server turn is re-authorized against a freshly loaded session, with no interval, so there is nothing to switch on. |

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
| `window.djust.extractTypedParams` and `data-*` attributes as event arguments | `dj-value-*` attributes (`dj-value-item-id:int="4"`); the browser sends only what the handler's signature declares ([#4205](https://github.com/djust-org/djust/issues/4205)) |
| `{% static 'djust_theming/css/base.css' %}`, a one-line `@import` of `scaffold.css` | `{% static 'djust_theming/css/scaffold.css' %}` ([#4207](https://github.com/djust-org/djust/issues/4207)) |
| The `djust_components/badge.html`, `avatar.html`, `progress.html` and `pagination.html` override points | Style the `dj-badge`, `dj-avatar`, `dj-progress` and `dj-pagination` classes the tags draw. `{% badge %}`, `{% avatar %}`, `{% progress %}` and `{% pagination %}` are drawn by one native handler on both engines, so a project template of the same name no longer takes effect. |

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
- **Production logs and error trackers lose exception detail.** With
  `DEBUG = False` a 1.3 legacy view's failure was logged with its
  exception and traceback, and error trackers listening to
  `got_request_exception` (Sentry) received it. A 2.0 view logs a static
  line, sends the signal with a value-free `ExposureError` from the HTTP page
  view, and none from the WebSocket, SSE or HTTP fallback. To get the detail
  back in server sinks only, set
  `LIVEVIEW_CONFIG = {"log_exception_detail": True}`: client frames and HTTP
  responses stay generic
  ([#4180](https://github.com/djust-org/djust/issues/4180)).
- **Background work stops when the connection closes.** Under the 1.3
  default a view's `start_async()` / `@background` work ran to completion
  after its socket or SSE stream closed, and the result was discarded, unless
  the view set `cancel_async_on_disconnect = True`. In 2.0 a view's work,
  and the tasks it starts with `self.create_task()`, are cancelled when the
  view is disposed: when the connection closes and when the user navigates to
  another view. An embedded child's `_cleanup_on_unregister` runs at the same
  two points, as on 1.3. `cancel_async_on_disconnect = True` is refused when the class
  is created; delete it. Work that must finish (a payment, an import) states
  `survive_disconnect=True`: `self.start_async(self._import)` becomes
  `self.start_async(self._import, survive_disconnect=True)`, and `@background`
  becomes `@background(survive_disconnect=True)`. It then runs to completion
  and its result is discarded
  ([#4182](https://github.com/djust-org/djust/issues/4182)).
- **WebSocket and SSE turns save their state after the reply, within 2
  seconds.** In 1.3 every turn's state was in storage before its reply. In
  2.0 `DJUST_STATE_SAVE_INTERVAL` defaults to 2: a turn answers first and its
  state is saved no later than 2 seconds after the first unsaved change, when
  the connection closes, before a navigation, and at the ASGI `lifespan`
  shutdown. Turns that send the browser elsewhere, write `request.session`,
  or ask (`@event_handler(save=True)`, `self.save_state()`) are still saved
  before their reply, and views with `persist="server"` / `persist="client"`
  fields, `DJUST_STATE_STORE_STRICT`, and the HTTP fallback are unchanged.
  Measured, a turn with the save taken out of it costs 30 to 38 per cent less.
  A killed process (SIGKILL, out of memory) can lose up to 2 seconds of
  changes, and under daphne a graceful stop can too. For the 1.3 behaviour
  set `DJUST_STATE_SAVE_INTERVAL = 0`, or `state_save_interval = 0` on one
  view ([#4164](https://github.com/djust-org/djust/issues/4164); see
  [How often view state is saved](../state/save-frequency.md)).

### Markup that changed: the ui simple components

Eight of the stateless classes in `djust.components.ui` now render through their
ui `*Component` twin, so each widget has one renderer
([#4002](https://github.com/djust-org/djust/issues/4002)). Their arguments still
work; the class docstring gives each mapping. What a page sees:

| Class | Draws | What changed |
|---|---|---|
| all eight | the twin's markup | An `id` on the outer element: the class's `id` (`alert`, `badge`, ..., or the explicit `id=` of `Modal` / `Dropdown`). The markup follows `css_framework` (Tailwind and plain variants), and the line breaks and indentation inside it changed. Text is still escaped whatever it is marked as. |
| `Alert` | `AlertComponent` | The close button stays Bootstrap's `data-bs-dismiss="alert"` (the class renders the twin `standalone`); under Tailwind and plain, where nothing would receive its click, there is no close button. A variant other than info / success / warning / danger / error draws `alert-info` and warns. |
| `Badge` | `BadgeComponent` | No size class: the default `md` badge loses `fs-6`, `lg` loses `fs-5`, and `size` other than `md` warns. An unknown variant draws `bg-primary` and warns. |
| `Button` | `ButtonComponent` | An unknown variant (including `link`) draws `btn-primary` and warns. |
| `Card` | `CardComponent` | `header` draws as `<h5 class="card-title">` inside `.card-body`, not a `.card-header`; the body is a `<p class="card-text">`; the footer gains `text-muted`. An empty header or footer draws no section. `variant` (`border` / `shadow`) is not drawn and warns. |
| `Dropdown` | `DropdownComponent` | Items are `DropdownItem`s; `url` (checked and escaped, `javascript:` becomes `#`) and `disabled` (`disabled` class, `aria-disabled="true"`) carry over, and `action` / `icon` / `variant` / `data` now work too (a `data` value is escaped text; one with a `javascript:`, `vbscript:` or `data:` scheme becomes `#`). The markup is on one line. |
| `Modal` | `ModalComponent` | Under Bootstrap `show=False`, the default, still draws the hidden `modal fade` (no backdrop) for `data-bs-toggle` to open, and both close buttons are `data-bs-dismiss="modal"` (the class renders the twin `standalone`); under Tailwind and plain a hidden modal renders nothing and raises a `UserWarning`, and a shown one has no close controls (nothing would receive them). A shown Bootstrap modal is `modal fade show d-block` followed by a `.modal-backdrop`. It always has a header with a close button (`dismissable=False` warns) and, without a `footer`, a "Close" button. `centered` is not drawn and warns; `aria-labelledby` is gone. |
| `Progress` | `ProgressComponent` | The `.progress` element always carries a height (`1rem` by default), the inline width is a whole percent (`40%`, not `40.0%`) and the fill class truncates (`42.5` is `dj-progress-w-42`). An empty `label` counts as none. |
| `Spinner` | `SpinnerComponent` | Wrapped in `<div id="spinner">`; `lg` / `xl` draw a size; the classes are single-spaced; an unknown `animation` draws `spinner-grow`. The screen-reader text is always "Loading...": `sr_text` warns. |

`AlertComponent` and `ModalComponent` take `standalone=True` for this: their
Bootstrap close controls become `data-bs-dismiss`, a hidden `ModalComponent` is
drawn hidden instead of as nothing, and under Tailwind and plain they draw no close
controls. A component registered with a view keeps the
default, `dj-click="dismiss"` and an empty hidden modal. `DropdownItem` gained `url`
and `disabled`. A deprecated argument warns from the constructor and from `update()`.

The other simple classes (`Accordion`, `Avatar`, `Breadcrumb`, `ButtonGroup`,
`Checkbox`, `Divider`, `Icon`, `Input`, `ListGroup`, `NavBar`, `Offcanvas`,
`Pagination`, `Radio`, `Range`, `Select`, `Switch`, `Table`, `Tabs`, `TextArea`,
`Toast`, `Tooltip`) have no twin in `djust.components.ui` and draw what they drew.

### Markup that changed: one renderer per component tag

Every `djust_components` tag now has one renderer, so both template engines
draw the same bytes ([#4331](https://github.com/djust-org/djust/issues/4331)).
Where the two engines used to disagree, one side's markup is gone. A stylesheet or
test that targets the old markup needs the new selector:

- **Rust engine, `badge`, `avatar`, `progress`, `pagination`.** They draw the
  `dj-*` BEM markup the Django engine drew. The old Rust classes `badge-*`,
  `avatar-image`, `progress-wrapper` and `pagination-btn` are gone from these
  tags. The inclusion templates `djust_components/badge.html`, `avatar.html`,
  `progress.html` and `pagination.html` are removed, so a project template of
  the same name no longer overrides them.
- **Django engine, `sidebar`, `nav_menu`, `toolbar`, `filter_bar`, `app_shell`.**
  They draw the native markup. `toolbar` groups its children between separators
  in `dj-toolbar__group`; the default `filter_bar` id is `filter-bar`; the
  default `toolbar` id is `toolbar-<hash>`.
- **`accordion`.** The Rust engine renamed its classes to the `dj-accordion*`
  BEM classes the Django engine draws. The panel is always drawn and a closed one
  carries `hidden`; the header button has `aria-controls` and the panel
  `role="region"`.
- **Standalone `sidebar_item`, `nav_item`, `accordion_item`.** Outside their
  parent tag they carry an invisible marker comment, so the Rust engine can tell
  where the item sits.
- **Marked-safe operands.** A `mark_safe` value passed as a tag argument is
  escaped by both engines, as it already was in an attribute.
- **`form_array`.** Its body is compiled by Django's engine on both engines, so a
  Rust-only tag inside it (`{% call %}`, `{% component %}`) is no longer
  available there.

### State a legacy view no longer keeps

A 1.3 legacy view kept these between requests and reconnects. None of them
exists in 2.0, so check what your views relied on:

- **Its private attributes.** `_name` attributes were saved in the session
  (`liveview_<path>__private`) and put back on the next page-POST fallback
  request. In 2.0 automatic state saves the `_private` attributes `mount()`
  created ([Keeping state across a reconnect](#keeping-state-across-a-reconnect));
  a key 1.x wrote is never read.
- **Its public state and its components' state under 1.x keys.** The page GET
  and each page-POST fallback request saved the view's public context
  (`liveview_<path>`) and its components (`liveview_<path>_components`), and
  the next POST restored them instead of running `mount()`. 2.0 saves a view's
  state, and the state of the interactive components declared on its class,
  under its own keys: a component's id is derived from its declaration, so an
  event addressed to it over the fallback reaches it (#4046).
- **Its cached renderer.** The compiled template, render state and diff
  baseline were kept in the state backend (`DJUST_CONFIG['STATE_BACKEND']`)
  under the session and page and reused by the next request or reconnect. Each
  view instance builds its own in 2.0.

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
  apply to. A state over its limit raises `StateLimitExceeded`, as the save
  would. The renderer a connection holds in memory is not measured.

Keys a 1.x server left in a session are never read. Declare a field that must
survive a reconnect with `state(..., persist="server")`; a private attribute
stays private, never reaches the template, and is set again by `mount()` on
each page load and socket reconnect:

```python
from djust import LiveView
from djust.decorators import event_handler, state


class FeedView(LiveView):
    # 1.3 (legacy): self.offset = 0 and self._page_size = 20 in mount(); the
    # page-POST fallback saved both in the session between requests.
    template = """<div dj-root>
        <p>Showing from {{ offset }}</p>
        <button dj-click="more">More</button>
    </div>"""

    offset = state(0, persist="server")

    def mount(self, request, **kwargs):
        self._page_size = 20  # set again by mount() on each page load

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["offset"] = self.offset
        return context

    @event_handler()
    def more(self):
        self.offset += self._page_size
```

A test that called `_get_private_state()` to check what a view keeps fails: the method is gone.
Use `LiveViewTestClient.persisted_state()` (or `djust.testing.persisted_state(view)`)
instead: it returns what the view would save, split by destination, and
`"name" not in persisted_state(view).names` asserts that a value a page only reads is saved nowhere
([Asserting what is saved](../testing/index.md#asserting-what-is-saved)).

## 4. Capabilities with a new 2.0 spelling

These worked on a 1.3 legacy view without any declaration. In 2.0 each needs
one, or behaves differently. The sections below show the code for
the rows that need it.

| 1.3 | 2.0 | Reported by | `--fix` |
| --- | --- | --- | --- |
| A sticky child keeps state through `_on_sticky_update` | `sticky_update_inputs = ("section",)` (2.0 only) | `djust.V035` | No |
| Background work ran to completion after a disconnect | `start_async(fn, survive_disconnect=True)` or `@background(survive_disconnect=True)` (2.0 only) | Nothing reports it: the work is cancelled | No |
| `cancel_async_on_disconnect = True` | Delete it: a view's work is always cancelled | `ImproperlyConfigured` when the class is created | No |
| The service worker cached every page | `sw_cacheable = True` on public pages (2.0 only) | `djust.V037` | No |
| Exception detail in production logs and error trackers | `LIVEVIEW_CONFIG = {"log_exception_detail": True}` (2.0 only) | Nothing reports it | No |
| `reauth_on_event`, `reauth_server_turn_interval` | Delete them: every turn is re-authorized | `ImproperlyConfigured` at startup | No |
| A `dj-auto-recover` handler on `_form_values` | `form_values` and `data_attrs` parameters (2.0 only) | `djust.V031` | No ([section 2](#recovery-handlers-dj-auto-recover)) |

### Sticky children

As a sticky child, a 2.0 view is remounted whenever a
`{% live_render %}` input changes, so djust never calls `_on_sticky_update`
(it ran only on djust 1.3; `djust.V035` reports it). Name the inputs
the hook takes, or delete the hook and read the inputs in `mount()`.

<!-- doc-snippet-check: before -->
```python
from djust import LiveView


class Sidebar(LiveView):
    sticky = True
    sticky_id = "sidebar"

    def mount(self, request, section="home", **kwargs):
        self.section = section

    def _on_sticky_update(self, changed):
        if "section" in changed:
            self.section = changed["section"]
```

becomes:

```python
from djust import LiveView, state


class Sidebar(LiveView):
    sticky = True
    sticky_id = "sidebar"
    sticky_update_inputs = ("section",)
    template = """<nav dj-root>Section: {{ section }}</nav>"""

    section = state("home")

    def mount(self, request, section="home", **kwargs):
        self.section = section

    def _on_sticky_update(self, changed):
        if "section" in changed:
            self.section = changed["section"]
```

`section` is declared with `state()`, so it renders by itself. Every other
input is part of the child's identity: a change to one remounts the child. The
rules are in [Sticky LiveViews](sticky-liveviews.md#changed-kwargs-the-on_sticky_update-hook).

### Background work that must finish

A view's `start_async()` and `@background` work, and the tasks it
starts with `self.create_task()`, are cancelled when the view is disposed:
when the connection closes and when the user navigates to another view.
`cancel_async_on_disconnect = True` is refused when the class is created;
delete it. Work that must finish (a payment, an import) says so:

<!-- doc-snippet-check: before -->
```python
from djust import LiveView
from djust.decorators import event_handler


class ImportView(LiveView):
    template = """<div dj-root><button dj-click="start">Import</button></div>"""

    @event_handler()
    def start(self):
        self.start_async(self._import)

    def _import(self):
        pass
```

becomes:

```python
from djust import LiveView
from djust.decorators import event_handler


class ImportView(LiveView):
    template = """<div dj-root><button dj-click="start">Import</button></div>"""

    @event_handler()
    def start(self):
        self.start_async(self._import, survive_disconnect=True)

    def _import(self):
        pass
```

For a decorated handler write `@background(survive_disconnect=True)`. The
work then runs to completion and its result is discarded
([#4182](https://github.com/djust-org/djust/issues/4182)).

### Service-worker caching

1.3 let the worker cache every (legacy) page. An explicit page is cached only
when its view opts in, and only a page that is the same for every visitor
should. `djust.V037` reports an opted-in view that reads the user, the
session or persisted state, because the worker would serve that HTML to
whoever opens the URL in that browser.

```python
from djust import LiveView


class Docs(LiveView):
    sw_cacheable = True  # public page, same HTML for everyone
    template = """<main dj-root><h1>Docs</h1></main>"""
```

### Exception detail in production logs

With `DEBUG = False` a 1.3 legacy view's failure was logged with its exception
and traceback, and error trackers listening to `got_request_exception`
(Sentry) received it. A 2.0 view logs a static line, and sends the
signal with a value-free `ExposureError` from the HTTP page view only. To get
the detail back in server sinks only, set this in settings; client frames and
HTTP responses stay generic
([#4180](https://github.com/djust-org/djust/issues/4180)):

```python
LIVEVIEW_CONFIG = {"log_exception_detail": True}
```

### Nothing to change

These work on explicit views as they did before: a wizard's flat
`{{ borough_choices }}` ([#4177](https://github.com/djust-org/djust/issues/4177)),
`@live_view` function views, which are explicit and HTTP only
([#4183](https://github.com/djust-org/djust/issues/4183)), and upload resume
after a reconnect for `ResumableUploadWriter`
([#4175](https://github.com/djust-org/djust/issues/4175)).

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

## 6. Tests after the upgrade

The test helpers in `djust.testing` change with the contracts.

| 1.3 | 2.0 | Reported by | `--fix` |
| --- | --- | --- | --- |
| `scope["user"] = StubUser()` in a socket test | `with_live_session(WebsocketCommunicator(...), user)` (2.0 only) | Nothing reports it | No |
| Asserting `_get_private_state()` | `persisted_state(view)` or `LiveViewTestClient.persisted_state()` (2.0 only) | `AttributeError` when the test runs: the method is gone | No |
| A blank name found by eye | `DJUST_UNSUPPLIED_NAMES = "error"` in test settings (2.0 only) | `djust.C043` (an invalid value) | No |

A socket test gets a saved server session and a user from
`with_live_session`; a hand-built scope with a stub user has no session, so
saved state does not work in it:

<!-- doc-snippet-check: before -->
```python
async def test_cart(user):
    communicator = WebsocketCommunicator(app, "/ws/live/")
    communicator.scope["user"] = StubUser()
```

becomes:

```python
from channels.testing import WebsocketCommunicator

from djust.testing import with_live_session
from djust.websocket import LiveViewConsumer


async def test_cart(user):
    communicator = with_live_session(
        WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/live/"), user
    )
    connected, _ = await communicator.connect()
    assert connected
    await communicator.disconnect()
```

`DJUST_UNSUPPLIED_NAMES = "error"` makes every name a template reads and
nothing supplies fail the test that renders it. Put it in your test settings,
never in production settings. The helpers are described in the
[testing guide](testing.md).

## 7. Finish

1. `python manage.py djust_exposure_inventory` shows `[policy: explicit]` for
   every view.
2. `python -W error::DeprecationWarning manage.py test` passes.
3. `python manage.py check` reports none of `djust.T027`, `djust.V016`,
   `djust.T020`, `djust.V034`, `djust.T029`, `djust.V031`, `djust.V035` and
   `djust.V037`. Do not look for `djust.V033` or `djust.T028`: they are
   retired and never reported (`djust.V016` and `djust.T020` cover the same
   code).
4. No view sets `enable_state_snapshot`, `use_actors` or subclasses
   `PersistentLiveView`.
5. Open each page, use it, and reconnect (stop and start the server with the
   page open). Look for values that render empty and for state that resets
   when it should not.

Then install 2.0 and start the server once. What no check reports is refused
there, with the name of its replacement in an `ImproperlyConfigured`: a
removed setting at startup, and `exposure_policy = "legacy"`,
`parameter_policy="legacy"` or `cancel_async_on_disconnect = True` when the
module that states it is imported. `djust_exposure_inventory` lists any view
still on `[policy: legacy]`; run `--fix` on it.

<!-- generated-doc prose-sha256: 571fec9b15e4 -->
